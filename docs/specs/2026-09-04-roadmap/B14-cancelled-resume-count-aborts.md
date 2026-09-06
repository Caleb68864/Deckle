# B14 — Cancelling "How many sheets came out?" must abort the resume, not resume from zero

**Roadmap item:** `docs/ROADMAP.md` B14
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Deckle finds an interrupted print run on startup of the print dialog and
offers to resume it. Saying yes opens a spinbox asking **"How many sheets came
out?"** — the one fact software cannot observe, taken as ground truth from the
person holding the stack.

`QInputDialog.getInt` returns `(value, ok)`. `_default_ask_resume_count`
discards `ok`, so pressing **Cancel** returns `0` — indistinguishable from
someone who genuinely answered zero. `_offer_resume` then calls
`session.resume(0)`, and the interrupted pass restarts from sheet 0.

The exact user action and the exact wrong result: a 60-sheet front pass dies
at sheet 47. Reopen the print dialog, accept the resume offer, then think
better of it and hit Cancel on the count prompt. Deckle reprints all 60 sheets
— 47 of them onto paper that already has ink on it, in a manual-duplex job
where the stack is already loaded. Cancel is the button a user presses to make
nothing happen; here it is the most expensive button in the dialog.

Zero is also a legitimate answer ("nothing came out, start the pass over"), so
the fix cannot be "treat 0 as cancel". The two have to be different values.

## 2. Current code

`deckle/app/views/print_dialog.py:366-376` — the prompt:

```python
    def _default_ask_resume_count(self, summary: SessionSummary) -> int:
        from PySide6.QtWidgets import QInputDialog

        count, _ok = QInputDialog.getInt(
            self.widget,
            "Resume print job",
            "How many sheets came out?",
            0,
            0,
        )
        return count
```

`_ok` is named with a leading underscore and dropped. Cancel returns
`(0, False)`; the caller sees `0`.

`deckle/app/views/print_dialog.py:284-308` — `_offer_resume`, the only caller:

```python
    def _offer_resume(self) -> None:
        chosen = self._confirm_resume(self._resumable)
        if chosen is None:
            return
        count = self._ask_resume_count(chosen)
        profile = self._resolve_profile(chosen.printer_name)
        backend = self._backend_cls(profile)
        try:
            session = self._session_cls.load(
                self.plan, profile, backend, chosen.session_id
            )
        except StaleSessionError as exc:
            ...
            self._show_offline_error("Cannot resume this print run", exc.detail)
            return
        self._session = session
        session.resume(count)
        self._drive(session)
```

The `chosen is None` line directly above is the pattern to copy: the resume
*offer* already has a decline path, and it returns. The count prompt has none.

`deckle/app/views/print_dialog.py:164-169` — the injection seam:

```python
        resumable_lister: Callable[[], list[SessionSummary]] | None = None,
        confirm_resume: Callable[[list[SessionSummary]], SessionSummary | None] | None = None,
        ask_resume_count: Callable[[SessionSummary], int] | None = None,
```

Note `confirm_resume` is already typed `-> SessionSummary | None`;
`ask_resume_count` is typed `-> int`. That type is the bug written down.

`deckle/app/views/print_dialog.py:184` — the default is bound in `__init__`:

```python
        self._ask_resume_count = ask_resume_count or self._default_ask_resume_count
```

`deckle/app/views/print_dialog.py:143-146` — the docstring for the parameter:

```python
    :param ask_resume_count: asked how many sheets physically emerged
        during the interrupted pass. Software cannot observe that, so it
        is taken as ground truth from the person holding the stack.
```

### Every call site

`grep -rn "ask_resume_count\|_ask_sheets_completed" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `deckle/app/views/print_dialog.py:166` | constructor parameter |
| `deckle/app/views/print_dialog.py:184` | bound to `self._ask_resume_count` |
| `deckle/app/views/print_dialog.py:288` | the only call, in `_offer_resume` |
| `deckle/app/views/print_dialog.py:366` | `_default_ask_resume_count` definition |
| `tests/test_print_dialog.py:266, 271` | `ask_resume_count=ask_resume_count` returning `7` |
| `tests/test_print_dialog.py:350` | `ask_resume_count=lambda chosen: 7` |

There is **no** symbol named `_ask_sheets_completed` anywhere in the tree
(the roadmap text refers to the prompt by its user-facing wording; the method
is `_default_ask_resume_count` and the seam is `ask_resume_count`).

`grep -rn "\.resume(" --include="*.py" deckle/ tests/` — `PrintSession.resume`
is called from `print_dialog.py:307` and from tests only.

### `PrintSession.resume`'s contract

`deckle/core/print_session.py` — `resume(sheets_completed: int)`. It accepts
`0` and means it: the pass restarts. Nothing there needs changing; the bug is
entirely in the dialog turning "cancelled" into `0`.

### Existing tests that touch this code

- `tests/test_print_dialog.py::test_reopening_with_interrupted_session_offers_resume_and_prompts_sheet_count`
  — asserts `dialog._session.resumed_with == 7`.
- `tests/test_print_dialog.py::test_resume_is_refused_and_explained_when_the_plan_has_changed`
  — passes `ask_resume_count=lambda chosen: 7`.
- `tests/test_print_dialog.py::test_no_resumable_sessions_means_no_resume_prompt`
  — asserts `dialog._session is None` for the no-sessions case, which is the
  same end state a cancelled count must produce.

Nothing covers the cancel path.

## 3. Change

`ask_resume_count` returns `int | None`; `None` means "the user cancelled".
`_offer_resume` aborts on `None` exactly as it already aborts on
`confirm_resume` returning `None`.

Two designs were possible. **Chosen:** `None` for cancel, keeping `0` as a
real answer. **Rejected:** a sentinel `-1` — it would still be an `int`, so
every caller that forgot to check it would silently pass a negative sheet
count into `PrintSession.resume`, which is worse than a `TypeError`.

1. **`print_dialog.py:366-376`, `_default_ask_resume_count`** — honour `ok`:

   ```python
    def _default_ask_resume_count(self, summary: SessionSummary) -> int | None:
        """Ask how many sheets physically came out of the printer.

        :param summary: the interrupted run being resumed.
        :returns: the count, or ``None`` if the user cancelled.

        ``None``, not ``0``. ``QInputDialog.getInt`` returns ``(0, False)``
        on Cancel, and dropping the flag made Cancel indistinguishable from
        an honest answer of zero -- so cancelling out of the prompt resumed
        from sheet 0 and reprinted the whole interrupted pass onto paper
        that already had ink on it. Zero is a legitimate answer ("nothing
        came out"), so the two cannot share a value.
        """
        from PySide6.QtWidgets import QInputDialog

        count, ok = QInputDialog.getInt(
            self.widget,
            "Resume print job",
            "How many sheets came out?",
            0,
            0,
        )
        return count if ok else None
   ```

2. **`print_dialog.py:284-290`, `_offer_resume`** — abort on `None`, and say
   why:

   ```python
    def _offer_resume(self) -> None:
        chosen = self._confirm_resume(self._resumable)
        if chosen is None:
            return
        count = self._ask_resume_count(chosen)
        if count is None:
            # Cancelling the count prompt is a second chance to decline the
            # resume, and it must behave like the first: do nothing. The
            # session stays on disk, so the offer comes back next time.
            return
        profile = self._resolve_profile(chosen.printer_name)
   ```

   The rest of the method is unchanged.

3. **`print_dialog.py:166`, the constructor annotation**:

   ```python
        ask_resume_count: Callable[[SessionSummary], int | None] | None = None,
   ```

4. **`print_dialog.py:143-146`, the `:param ask_resume_count:` docstring**:

   ```python
    :param ask_resume_count: asked how many sheets physically emerged
        during the interrupted pass. Software cannot observe that, so it
        is taken as ground truth from the person holding the stack.
        Returning ``None`` declines the resume, the same way
        ``confirm_resume`` does.
   ```

5. **`print_dialog.py:15`, module docstring** — the last paragraph currently
   reads:

   ```
   The confirmation prompts (reload-between-passes, resume count, offline
   error, resume offer) are all injectable callables so headless tests can
   drive the full flow without blocking on a real modal event loop.
   ```

   Append one sentence:

   ```
   Two of them can decline: ``confirm_resume`` and ``ask_resume_count``
   both return ``None`` to mean "not this run", and ``_offer_resume``
   returns on either.
   ```

6. **`docs/api/`** — no new module. No change.

### Behaviour after the change

| User action | Result |
|---|---|
| Decline the resume offer | nothing happens; session stays on disk (unchanged) |
| Accept, then answer `0` | `session.resume(0)` — pass restarts from sheet 0 (unchanged) |
| Accept, then answer `47` | `session.resume(47)` (unchanged) |
| Accept, then **Cancel** the count | nothing happens; session stays on disk; `self._session` stays `None` (**new**) |

No error message, no status text. Cancel means nothing happened, and saying so
in the status bar would be noise about a non-event.

## 4. Tests

`PrintDialog` constructs fine under pytest — it is a `QDialog`, not a
`QMainWindow`. Reuse `tests/test_print_dialog.py`'s module-scoped `qapp`
fixture and the `_make_dialog(**kwargs)` helper at line 160, which already
injects `_StubSession`, `_StubBackend` and every prompt. The file sets
`QT_QPA_PLATFORM=offscreen` at line 19.

Add to `tests/test_print_dialog.py`, in the `# -- resume on reopen ---`
section after `test_no_resumable_sessions_means_no_resume_prompt`.

### `test_cancelling_the_sheet_count_abandons_the_resume`

```python
def test_cancelling_the_sheet_count_abandons_the_resume():
    """Cancel returned 0 from QInputDialog.getInt, which is also a valid
    answer, so cancelling resumed from sheet 0 and reprinted the whole
    interrupted pass onto paper that already had ink on it."""
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=47,
        state_path="/tmp/abc123.json",
    )

    dialog = _make_dialog(
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: None,       # the user pressed Cancel
    )

    assert dialog._session is None, "a cancelled count still started a session"
```

Setup: one resumable session at sheet 47, the resume offer accepted, the count
prompt cancelled.
Assertion in words: no session is adopted, so nothing is submitted to the
printer.
Expected failure on the unfixed tree:
`AssertionError: a cancelled count still started a session` — today
`_offer_resume` calls `_StubSession.load(...)`, assigns `self._session`, and
calls `session.resume(None)`.

### `test_answering_zero_still_resumes_from_the_start`

```python
def test_answering_zero_still_resumes_from_the_start():
    """Zero is a real answer -- nothing came out, run the pass again -- so
    it must not be conflated with Cancel."""
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=47,
        state_path="/tmp/abc123.json",
    )

    dialog = _make_dialog(
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: 0,
    )

    assert dialog._session is not None
    assert dialog._session.resumed_with == 0
```

Assertion in words: an explicit zero resumes the session from sheet 0.
On the unfixed tree this **passes** — it is the regression guard that stops
the fix from being "treat 0 as cancel", which would be a different, quieter
bug. Write it anyway and note in the commit that it is green before and after.

### `test_the_real_prompt_reports_cancel_as_none`

```python
def test_the_real_prompt_reports_cancel_as_none(monkeypatch):
    """The default implementation, not just the injected seam.
    QInputDialog.getInt returns (0, False) on Cancel and the flag was
    dropped."""
    from PySide6.QtWidgets import QInputDialog

    dialog = _make_dialog()
    summary = SessionSummary(
        session_id="abc", printer_name="P", started_at=0.0,
        pass_index=0, sheet_cursor=3, state_path="/tmp/abc.json",
    )

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False)))
    assert dialog._default_ask_resume_count(summary) is None

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, True)))
    assert dialog._default_ask_resume_count(summary) == 0

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (47, True)))
    assert dialog._default_ask_resume_count(summary) == 47
```

Assertion in words: the real Qt prompt distinguishes Cancel from an answer of
zero.
Expected failure on the unfixed tree: `AssertionError: assert 0 is None` on
the first assert.

### Existing tests to leave alone

`test_reopening_with_interrupted_session_offers_resume_and_prompts_sheet_count`
returns `7` and `test_resume_is_refused_and_explained_when_the_plan_has_changed`
returns `7`; both stay valid and unchanged.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py -k "cancelling_the_sheet_count or answering_zero or reports_cancel_as_none"` |
| The whole print-dialog file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py` |
| Print session and printing suites unaffected | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session.py tests/test_print_session_durability.py tests/test_printing.py tests/test_hardening_printing.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| The `ok` flag is no longer discarded | `! grep -n "count, _ok = QInputDialog.getInt" deckle/app/views/print_dialog.py` — currently hits line 369 |
| `_offer_resume` has the abort | `grep -n "if count is None:" deckle/app/views/print_dialog.py` returns a line between 284 and 310 |
| The annotation records the new shape | `grep -q "ask_resume_count: Callable\[\[SessionSummary\], int | None\]" deckle/app/views/print_dialog.py` |
| [HUMAN] A real interrupted run, resumed and cancelled | Interrupt a 4-sheet front pass, reopen the print dialog, accept the resume offer, press Cancel on "How many sheets came out?". The printer must stay silent and the dialog must show no status change. |

## 6. Out of scope

- **B3** — `_hash_plan` covering only sheet index, side presence and page
  indices, so a re-imposed document resumes without a `StaleSessionError`.
  Different bug, same dialog. Do not touch `deckle/core/print_session.py`.
- **B21** — the dialog catching only `OSError` from a corrupt saved profile
  where the CLI also catches `ValueError`.
- **B27** — print-session state living in `tempfile.gettempdir()`.
- **B16** — the profile combo. See `B16-print-dialog-profile-combo.md`.
- **F6** — sheet-subset reprint from the GUI.
- Do not change `PrintSession.resume`'s signature or its handling of `0`.
- Do not add a status-bar message for the cancel; a cancel is a non-event.

## 7. decisions.md entry

```
## 2026-09-05 — Cancelling the resume-count prompt reprinted the whole pass
- Symptom: `_default_ask_resume_count` called `QInputDialog.getInt`, which returns `(0, False)` on Cancel, and dropped the flag. Cancel was therefore indistinguishable from an honest answer of zero, so `_offer_resume` called `session.resume(0)` and the interrupted pass restarted from sheet 0 — reprinting up to 60 sheets onto paper that already had ink on it, in a manual-duplex job where the stack is already loaded. Cancel is the button a user presses to make nothing happen.
- Fix: `ask_resume_count` now returns `int | None`; `None` means cancelled and `_offer_resume` returns on it, exactly as it already returns when `confirm_resume` returns `None`. Zero stays a legitimate answer ("nothing came out, run the pass again"), so the two are different values rather than 0 being reinterpreted.
- Surfaces: Every Qt prompt that returns `(value, ok)`. `QInputDialog.getItem` is used twice in `arrange_view.py` and both call sites do check `ok`; this was the only one that did not.
- Watch: The type annotation was the bug written down. `confirm_resume` was declared `-> SessionSummary | None` and `ask_resume_count` `-> int`, next to each other in the same parameter list, and only one of them could decline.
- Commit: <fill in>
```

## 8. Traps

- **`0` is a real answer.** Do not "fix" this by treating a zero count as a
  cancel; `test_answering_zero_still_resumes_from_the_start` exists to catch
  that. It passes before and after the change, deliberately.
- **`_offer_resume` runs from `__init__`** (`print_dialog.py:241-244`), before
  `PrintDialog.__init__` returns. An exception there takes the dialog's
  construction down, which is why the return is a plain `return` and not a
  raise.
- **`session.resume(None)` today does not raise** with the real
  `PrintSession` — check before assuming the current bug is loud. It is not:
  the wrong behaviour is a silent reprint.
- **`tests/test_print_dialog.py` imports PySide6 only inside the `qapp`
  fixture body**, deliberately, so `tests/test_core_purity.py` and
  `tests/test_backend.py` can assert Qt is not loaded in the same session.
  Any new import in that file must stay inside a function.
- **`monkeypatch.setattr(QInputDialog, "getInt", ...)` needs
  `staticmethod(...)`** — `getInt` is a static method on the Qt class, and
  patching it with a bare lambda makes Qt pass the class as the first
  argument on some PySide6 builds. The `*a, **k` signature absorbs it either
  way, but the `staticmethod` wrapper is what keeps the patch faithful.
- **`python -m deckle` launches the GUI and blocks.** The `[HUMAN]` row is
  the only place a real window appears; run it by hand, not from a script.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
</content>
