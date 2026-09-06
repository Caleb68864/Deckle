# B4 — Give `log_print_job` one owner: the backend

**Roadmap item:** `docs/ROADMAP.md` B4
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Every submitted print chunk is written to the session log twice, and the
second write is unguarded.

`QtPrintBackend.submit` logs the chunk, catching `OSError` and turning it
into a `PrintResult.error` so the run stops with a message instead of a
traceback. `PrintSession._submit_sheets` then logs the *same* chunk again,
with no guard at all — and it does so **after** the backend returned and
**before** `self._save()` writes the cursor.

So on a machine whose data directory is unwritable (a full disk, a roaming
profile that did not mount, a locked-down `%APPDATA%`), the sequence for a
60-sheet front pass is:

1. Ten sheets physically print.
2. The backend's `log_print_job` raises `OSError`; the backend catches it
   and returns `PrintResult(submitted=10, error="10 sheet(s) printed, but
   the print could not be recorded…")`.
3. `PrintSession._submit_sheets` calls `log_print_job` again on the same
   chunk. It raises. Nothing catches it.
4. The exception escapes `session.start()` into
   `PrintDialog.start_print`, which has no `try`. The user gets a
   traceback.
5. `self._state.sheet_cursor` was never incremented and `_save` was never
   reached, so the state file still reads `0`. Resuming reprints all ten.

That is the exact bug `deckle/app/backend.py:365-380` says was fixed:

> the raise used to escape `submit` entirely: past this method's promise
> that failures come back through `error`, out of `session.start()`, and
> into a print dialog that does not catch it. Three sheets would
> physically print, the user would get a traceback, and the session cursor
> would still read 0, so a resume reprinted every one of them.

It was fixed in one of the two callers.

On the happy path the cost is smaller but still wrong: **every chunk
appears twice in `session_log.jsonl`**, so the log Deckle's own GUIDE tells
users to attach to a bug report double-counts every sheet that was ever
printed from the desktop app.

## 2. Current code

`deckle/core/print_session.py:559-564`:

```python
    def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
        result = self.backend.submit(
            self.plan, sheets, self.printer_name, self.copies, self.dpi
        )
        log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
        return result
```

The import that feeds it, `deckle/core/print_session.py:45-60`:

```python
try:
    from deckle.core.session_log import log_print_job
except ImportError:  # pragma: no cover - SS-07 (persistence/log) not yet landed
    def log_print_job(
        printer: str,
        profile: PrinterProfile,
        sheets: Sequence[int],
        dpi: int,
        pass_index: int,
    ) -> None:
        """Fallback no-op used only until ``deckle.core.session_log`` exists.

        Mirrors the ``log_print_job`` shape from SS-07 exactly so callers
        never have to change once the real module lands.
        """
        return None
```

The surviving owner, `deckle/app/backend.py:365-398`:

```python
        # The paper is already out by this point, so a logging failure is a
        # different animal from a print failure and has to be reported as
        # one. `log_print_job` raises rather than swallowing, on purpose --
        # the session log is a hard constraint, not a best-effort trace --
        # but the raise used to escape `submit` entirely: past this method's
        # promise that failures come back through `error`, out of
        # `session.start()`, and into a print dialog that does not catch it.
        # ...
        try:
            log_print_job(printer_name, self.profile, sheets, dpi, pass_index)
        except OSError as exc:
            log_exception(
                "print_session_log_failed",
                exc,
                printer=printer_name,
                sheets=list(sheets),
                side=side,
                pass_index=pass_index,
            )
            return PrintResult(
                submitted=len(sheets),
                job_id=None,
                error=(
                    f"{len(sheets)} sheet(s) printed, but the print could "
                    f"not be recorded in the session log ({exc}). The paper "
                    "is correct; the record of it is missing."
                ),
            )
        return PrintResult(submitted=len(sheets), job_id=None, error=None)
```

The two callers of `_submit_sheets`, `deckle/core/print_session.py:580` and
`:615`:

```python
            result = self._submit_sheets(pass_, first)
```

```python
        result = self._submit_sheets(pass_, chunk)
```

### Every call site of `log_print_job`

Production:

- `deckle/core/session_log.py:70` — the definition
- `deckle/app/backend.py:48` — the real import
- `deckle/app/backend.py:50-62` — the `ImportError` fallback no-op
- `deckle/app/backend.py:381` — inside `QtPrintBackend.submit` (**the survivor**)
- `deckle/app/backend.py:661` — inside `QtPrintBackend.submit_duplex`
  (unwired; see F9)
- `deckle/core/print_session.py:46` — the real import
- `deckle/core/print_session.py:48-60` — the `ImportError` fallback no-op
- `deckle/core/print_session.py:563` — **the call this spec deletes**

Tests:

- `tests/test_print_log_failure.py:91` — `monkeypatch.setattr(backend_mod, "log_print_job", explode)`
- `tests/test_print_log_failure.py:144` — patches the same name with a recorder
- `tests/test_duplex_submission.py:86` — patches the same name
- `tests/test_backend.py:116` — patches the same name (`test_submit_logs_one_record_before_returning`)
- `tests/test_backend.py:136` — patches the same name (`test_submit_pass_calls_log_once_per_chunk`)
- `tests/test_project_io.py:219` — calls `session_log.log_print_job` **directly**,
  not through a backend or a session

Documentation that names the wrong owner, `deckle/core/session_log.py:79`:

```python
    Called once per submitted chunk (see ``PrintSession._submit_sheets``).
```

### What the fake backend in the tests does

Two shapes, and neither logs:

- `tests/test_print_session.py:66-85`, `tests/test_print_session_durability.py:72-79`
  and `tests/test_print_session_state_validation.py:40-47` each define a
  plain `StubBackend` dataclass with a `submit` method that appends to a
  list and returns a `PrintResult`. **It never calls `log_print_job`.**
  Today the session's own call is what writes a log record in those tests
  — to the *real* user data directory, since none of those files sets
  `DECKLE_SESSION_LOG_DIR`. After this change nothing writes a record
  there, which is correct: a stub backend that does not print should not
  produce a record saying it did.
- `tests/test_print_log_failure.py:71-83` subclasses the real
  `QtPrintBackend`, overriding `_submit_chunk` only. That backend *does*
  reach `deckle/app/backend.py:381`, so with `backend_mod.log_print_job`
  patched, the log call still happens and is still observed.

**No test asserts that a record reaches the session log by way of a
`PrintSession`.** `tests/test_project_io.py::test_session_log_rotates_and_retains_bounded_generations`
is the only test that reads a real log file, and it calls
`session_log.log_print_job` directly in a loop. So deleting the session's
call costs no coverage.

### Existing tests over this code

- `tests/test_print_log_failure.py` — the whole file (6 tests)
- `tests/test_backend.py::test_submit_logs_one_record_before_returning`
- `tests/test_backend.py::test_submit_pass_calls_log_once_per_chunk`
- `tests/test_duplex_submission.py` (patches the name so `submit_duplex`
  does not touch disk)
- `tests/test_print_session.py` — every test that calls `start()` or
  `advance()` exercises `_submit_sheets`

## 3. Change

**The backend is the owner.** It is the layer that actually put ink on
paper; it already wraps the failure into `PrintResult.error` with a message
written for someone standing at a printer; and it is the only one of the two
that knows `side` and `pass_index` well enough to record them. The rejected
alternative — make the *session* the owner and delete the backend's call —
was rejected because the session cannot turn a logging failure into a
`PrintResult` (it has already been handed one) and because
`QtPrintBackend.submit` is a public method that other callers may reach
without a session at all.

1. **`deckle/core/print_session.py`** — delete the `log_print_job` call at
   line 563 and simplify the method. `pass_` was only ever read for
   `pass_.index`, which was only ever passed to that call, so the parameter
   goes with it:

   ```python
   def _submit_sheets(self, sheets: list[int]) -> PrintResult:
       """Hand one chunk to the backend.

       The session log is written by the backend and only by the backend.
       Both used to write it, so every chunk was recorded twice -- and the
       copy here sat outside any guard, after the sheets had printed and
       before the cursor was saved, so an unwritable data directory
       produced a traceback out of ``start()`` and a resume that reprinted
       the whole chunk. That is precisely the failure
       ``QtPrintBackend.submit`` was changed to prevent; it was fixed in
       one of the two callers.
       """
       return self.backend.submit(
           self.plan, sheets, self.printer_name, self.copies, self.dpi
       )
   ```

2. **`deckle/core/print_session.py:580`** — `self._submit_sheets(pass_, first)`
   → `self._submit_sheets(first)`.

3. **`deckle/core/print_session.py:615`** — `self._submit_sheets(pass_, chunk)`
   → `self._submit_sheets(chunk)`.

4. **`deckle/core/print_session.py:45-60`** — delete the whole
   `try: from deckle.core.session_log import log_print_job / except
   ImportError:` block, including the fallback function. Nothing in this
   module calls `log_print_job` any more, and a dead import that reads as a
   live capability is the defect shape the decision log has caught before.
   Leave `deckle/app/backend.py:47-62`'s twin alone — removing it is M7's,
   and doing it here would put a second unrelated change in the diff.

5. **`deckle/core/print_session.py`** — after step 4, check whether
   `PrinterProfile` and `Sequence` are still used. `PrinterProfile` is
   still the annotation on `PrintSession.__init__`'s `profile` parameter
   and `Sequence` is still used for `sheets: Sequence[int] | None`, so both
   imports stay. Confirm with
   `grep -n "PrinterProfile\|Sequence" deckle/core/print_session.py`.

6. **`deckle/core/session_log.py:79`** — change

   ```python
       Called once per submitted chunk (see ``PrintSession._submit_sheets``).
   ```

   to

   ```python
       Called once per submitted chunk, by ``QtPrintBackend.submit`` and
       nowhere else. ``PrintSession`` used to call it too, so every chunk
       was recorded twice; the backend owns it because the backend is what
       put ink on paper and is the layer that can turn a failure here into
       a ``PrintResult`` rather than a traceback.
   ```

7. **`deckle/core/print_session.py`'s module docstring** — the paragraph at
   lines 8-12 describes state persistence and is unaffected. No change.

8. **Tests** — see §4.

### What is deliberately unchanged

A logging failure still stops the run, and still reports
`submitted=len(sheets)` because the paper came out. The policy question
`tests/test_print_log_failure.py`'s docstring flags ("whether it *should*
stop the run") stays open.

## 4. Tests

### `tests/test_print_log_failure.py`

The file already patches the surviving copy
(`monkeypatch.setattr(backend_mod, "log_print_job", …)` at `:91` and
`:144`), so its six tests keep passing unchanged. Two things must still be
done to it:

**a. Add `test_the_log_records_each_chunk_exactly_once`.**

- **File / function:** `tests/test_print_log_failure.py::test_the_log_records_each_chunk_exactly_once`
- **Setup:** the existing `backend` fixture (the `Recording` subclass of
  `QtPrintBackend`); a recorder patched onto `backend_mod.log_print_job`
  collecting `list(sheets)`; a `PrintSession(_plan(3), backend.profile,
  backend, printer_name="P", chunk_size=10)`; call `session.start()` and
  then `session.advance()` until `session.finished`.
- **Assertion in words:** the recorder holds exactly one entry per
  submitted chunk — two chunks for a three-sheet plan (one front pass, one
  back pass), so `len(recorded) == 2`, not 4.
- **Expected failure on the unfixed tree:** `assert 4 == 2`. The session's
  own call reaches the *real* `deckle.core.session_log.log_print_job`
  rather than the patched `backend_mod` name, so the recorder sees only the
  backend's two — which means the naive count assertion **passes** by
  accident. To make it fail honestly, patch **both** names in this test:
  `monkeypatch.setattr(backend_mod, "log_print_job", recorder)` **and**
  `monkeypatch.setattr(print_session_mod, "log_print_job", recorder)`,
  importing `from deckle.core import print_session as print_session_mod`.
  Then the unfixed tree records 4 and the assertion fails with
  `assert 4 == 2`; after the change, `print_session_mod` has no
  `log_print_job` attribute at all and `monkeypatch.setattr` raises
  `AttributeError`, which is the wrong failure. So: use
  `monkeypatch.setattr(print_session_mod, "log_print_job", recorder,
  raising=False)`, which is a no-op once the name is gone and is the
  form that reads as "there must be nothing here to patch".

**b. Add `test_the_session_does_not_write_the_log_itself`.**

- **File / function:** `tests/test_print_log_failure.py::test_the_session_does_not_write_the_log_itself`
- **Setup:** none beyond importing `deckle.core.print_session`.
- **Assertion in words:** the module has no `log_print_job` attribute —
  `assert not hasattr(print_session_mod, "log_print_job")` — and its source
  contains no call to it. This is the structural guard; without it the call
  can be reintroduced and every behavioural test still passes, because the
  duplicate record is invisible unless something counts.
- **Expected failure on the unfixed tree:**
  `AssertionError: assert not True`.

**c. Extend the module docstring** of `tests/test_print_log_failure.py`
with a paragraph recording that there were two writers and that the backend
is now the only one — in the file's existing voice, which explains *why*
each wrong thing was wrong.

### `tests/test_print_session.py`

Add `test_submitting_does_not_touch_the_session_log`:

- **Setup:** `monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path / "log"))`;
  a `StubBackend`; a two-sheet plan; `session.start()` then
  `session.advance()` to completion.
- **Assertion in words:** `tmp_path / "log" / "session_log.jsonl"` does not
  exist — a stub backend that never printed must not leave a record saying
  it did.
- **Expected failure on the unfixed tree:** the file exists (the session
  writes two records), so `assert not path.exists()` fails. This test also
  documents that the suite used to write into the real user data directory.

### Tests that must keep passing untouched

- `tests/test_backend.py::test_submit_logs_one_record_before_returning`
- `tests/test_backend.py::test_submit_pass_calls_log_once_per_chunk`
- `tests/test_project_io.py::test_session_log_rotates_and_retains_bounded_generations`
- `tests/test_print_session.py::test_print_session_public_surface_is_unchanged`
  — `_submit_sheets` is private and not in the pinned surface, so the
  signature change is allowed. Re-read `expected_methods` at
  `tests/test_print_session.py:389-396` to confirm before editing.

## 5. Acceptance

| Check | Command |
|---|---|
| `print_session` no longer names the logger | `! grep -n "log_print_job" deckle/core/print_session.py` |
| `print_session` no longer imports `session_log` | `! grep -n "session_log" deckle/core/print_session.py` |
| There is exactly one production call | `test "$(grep -rn 'log_print_job(' --include='*.py' deckle/ \| grep -v 'def log_print_job' \| wc -l)" -eq 2` |
| The session log module names the right owner | `grep -n "QtPrintBackend.submit" deckle/core/session_log.py` |
| The two new log-failure tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_log_failure.py` |
| The session no longer writes a log | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session.py -k "does_not_touch_the_session_log"` |
| Backend log tests unaffected | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_backend.py tests/test_duplex_submission.py tests/test_project_io.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Row 3's grep currently returns **3** production call lines
(`deckle/app/backend.py:381`, `deckle/app/backend.py:661`,
`deckle/core/print_session.py:563`); after the change it must return **2**.
Verified on the unfixed tree.

Row 4's grep currently returns nothing — `deckle/core/session_log.py:79`
says `PrintSession._submit_sheets`.

## 6. Out of scope

- **The backend's own `ImportError` fallback** at
  `deckle/app/backend.py:47-62`. That is **M7** ("try/except ImportError
  fallbacks for `session_log` in two modules"). This spec removes one of
  the two because its only consumer is going away; the other has a live
  consumer and is M7's to judge.
- **`submit_duplex`'s unguarded `log_print_job`** at
  `deckle/app/backend.py:661`. `submit_duplex` is unwired (**F9**, and
  `tests/test_duplex_submission.py` asserts nothing calls it), so its
  logging call is dark code. Do not change it here.
- **Whether a logging failure should stop the run.** Pinned as deliberate
  by `tests/test_print_log_failure.py`'s docstring.
- **`pass_index` in the log.** Deleting the session's call also deletes the
  only place a *correct* pass index reached the log from the GUI path:
  `PrintSession` submits through the five-argument `PrintBackend` Protocol
  (`deckle/core/printing.py:82-92`), which carries no `side`,
  `rotate_backs` or `pass_index`, so `QtPrintBackend.submit` uses its
  defaults (`side="front"`, `pass_index=0`) for **both** passes. That means
  the desktop app's back pass is already painted as fronts and unrotated —
  a HIGH bug that is not in the roadmap and is not this spec's. It is named
  here so the implementer does not "fix" the log by threading `pass_index`
  through and think the job is done.
- **B3, B27, B28** — same file. See §8.

## 7. decisions.md entry

```
## 2026-09-05 — Two writers for the session log, one of them unguarded
- Symptom: `QtPrintBackend.submit` and `PrintSession._submit_sheets` both called `log_print_job`, so every submitted chunk appeared twice in `session_log.jsonl`. Worse, the session's copy sat outside any guard, after the backend returned and before `_save` -- so on an unwritable data directory the sheets printed, the exception escaped `session.start()` into a print dialog with no `try`, and the cursor still read 0, so a resume reprinted the chunk. That is the exact failure the backend's own comment says was fixed; it was fixed in one of the two callers.
- Fix: The backend owns it. Deleted the session's call, its import and its `ImportError` fallback. `_submit_sheets` lost its now-unused `pass_` parameter. `session_log`'s docstring names `QtPrintBackend.submit` instead of `PrintSession._submit_sheets`.
- Surfaces: The regression test patched `deckle.app.backend.log_print_job` only, so the session's copy ran for real -- writing into the developer's actual `~/.local/share/deckle/session_log.jsonl` during the test run. A test that patches one of two call sites proves the patched one works and hides the other.
- Watch: A fix applied at one call site of a duplicated call is not a fix. Before closing a "this escaped and produced a traceback" bug, grep for the symbol and count the callers.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B3, B4, B27 and B28 all edit
  `deckle/core/print_session.py`. B4 touches only `_submit_sheets`, its two
  callers and the import block, so it is disjoint from all three.
  Recommended order for the cluster: **B4 → B3 → B28 → B27.**
- **`monkeypatch.setattr(module, name, value)` raises `AttributeError` when
  the name is absent.** Any new test that patches
  `deckle.core.print_session.log_print_job` must pass `raising=False`, or
  it fails *after* the fix instead of before it.
- **Three separate `StubBackend` classes** exist, in
  `tests/test_print_session.py`, `tests/test_print_session_durability.py`
  and `tests/test_print_session_state_validation.py`. None of them logs.
  Do not add logging to any of them to "keep the log written" — that would
  be a stub asserting a property of a real backend.
- **The suite writes to the real user data dir today.** Tests that drive a
  `PrintSession` set `DECKLE_SESSION_STATE_DIR` but not
  `DECKLE_SESSION_LOG_DIR`, so the duplicate log records land in
  `~/.local/share/deckle/session_log.jsonl` (or `%APPDATA%\Deckle\`). After
  this change nothing does. If you are checking the fix by hand, look at
  that file's line count before and after.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
