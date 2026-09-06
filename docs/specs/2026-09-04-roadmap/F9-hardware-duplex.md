# F9 — Wire the hardware-duplex path that has never been called

**Roadmap item:** `docs/ROADMAP.md` F9
**Depends on:** **F1.** The first assumption below is that a hardware
duplexer does **not** get the hand-measured back offset. That is only
defensible once the user can see and edit that number inside the app;
without F1 the offset lives in a JSON file the program never mentions, and
"Deckle silently ignores a number you cannot find" is not an assumption, it
is a trap. F1 also gives the eventual per-printer home for the checkbox's
remembered state (§6).
**Blocks:** —
**Size:** S of code, plus two decisions
**Decision needed first:** ROADMAP §6 — *"F9: wire hardware duplex now (with
the two recorded questions answered by assumption) or leave it dark?"* This
spec **is** the "wire it now" answer, with both questions written down as
assumptions and both made visible rather than silent. If the owner prefers
"leave it dark", do not implement; delete nothing.

---

## 1. Context

`QtPrintBackend.submit_duplex` is ~70 lines of complete, tested Qt printing
code that no path in Deckle reaches. `duplex_modes(printer_name)` reports
whether it would apply, and is read by nothing but its own tests. A user with
a duplexer gets Deckle's two-pass manual flow — walk to the printer, reverse
the stack, flip every sheet, walk back — on a machine that would have done it
in one pass unattended.

The roadmap lists it under *"complete, unwired"*, and M7 lists it under
*"Dead / scaffold code"* beside the empty `DRIVERS_IGNORING_ROTATE` and a
`try: ... finally: pass`.

Two questions were recorded and never answered, both in the code itself:

**(1) Does a duplexer deserve the offset measured for a hand reload?**
`submit_duplex`'s own docstring:

> Whether a *hardware* duplexer deserves the offset measured for a hand
> reload is a real question and not settled here — but discarding a measured
> number without saying so is not the answer to it, and if the answer turns
> out to be "a duplexer needs no correction" then the profile should record
> that rather than this method deciding it in silence.

**(2) Can `supportedDuplexModes()` be trusted on Linux?**
`docs/spikes/qprinter-capability-report.md` records this as the one
`diverged` row across both platforms:

> For a printer whose PPD advertises `Duplex` as a supported option,
> `supportedDuplexModes()` sometimes returned only `DuplexNone` until at
> least one job had been submitted to that queue in the current session …
> Once a job had gone through, capability reporting matched the PPD.

with an owner decision still unticked and a recommendation of *"option 1 for
MVP, revisit before adding a 'you have a duplex printer' prompt to the UI."*
**F9 is that prompt**, so the revisit is now.

## 2. Current code

`deckle/app/backend.py:553-599` — the docstring, which states the two open
questions and the fact that nothing calls it:

```python
    def submit_duplex(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult:
        """Submit fronts and backs as one hardware-duplex Qt job.

        **Nothing calls this yet.** It is written and complete, and
        ``duplex_modes(printer_name).single_pass_duplex`` reports whether
        it would apply, but no path in Deckle reaches it: the print dialog
        and :class:`~deckle.core.print_session.PrintSession` both drive
        manual duplex unconditionally. Said outright because the previous
        wording -- "only used when ``single_pass_duplex`` is True" --
        described a condition on something that never happens, which is
        the same shape as ``Mark.cut_line``'s declared-but-unproduced kind
        and ``Sheet.back``'s old docstring: a sentence asserting a
        capability the program does not have.
        ...
        **The registration correction is applied here too**, as it is on
        the manual path. It was omitted, which cost nothing while nothing
        called this and would have cost a calibration the moment something
        did ...
        """
```

`deckle/app/backend.py:633-645` — where the offset is applied today:

```python
                    for sheet_index in chunk:
                        for side in ("front", "back"):
                            if not first:
                                printer.newPage()
                            first = False
                            rendered = _render_sheet_side(
                                plan, sheet_index, side, dpi, False, False,
                                back_offset_pt=(
                                    self.profile.back_offset_x_pt,
                                    self.profile.back_offset_y_pt,
                                ),
                            )
                            self._paint_rendered_page(painter, printer, rendered)
```

Note it applies the offset to **both** faces — the `back_offset_pt` argument
goes to `_render_sheet_side` for `side="front"` too. Read `_render_sheet_side`
(line 169) before assuming that is a bug; the manual path's `submit` may do
the same.

`deckle/app/backend.py:533-551` — the capability query:

```python
    def duplex_modes(self, printer_name: str) -> DuplexModes:
        """The duplex options available for ``printer_name``.

        Manual duplex (Deckle's two-pass reload flow) is always offered --
        Deckle exists for duplexer-less printers. ...

        :returns: the available modes. A printer Qt does not recognise
            reports manual duplex only, which is the safe direction.
        """
        info = _printer_info(printer_name)
        supported = set(info.supportedDuplexModes()) if not info.isNull() else set()
        hardware_duplex = bool(supported - {_duplex_none_mode()})
        return DuplexModes(manual=True, single_pass_duplex=hardware_duplex)
```

`deckle/core/printing.py:95-104` and `134-165` — `PrintPass` and
`plan_passes`, which always plan two passes:

```python
@dataclass(frozen=True)
class PrintPass:
    """One physical pass through the printer: an ordered set of sheets."""

    index: int
    sheet_order: list[int]
    side: Literal["front", "back"]
    reload_instruction: str
    rotate_backs: bool
```

`deckle/core/print_session.py:559-564` — the only submission site, hard-wired
to `backend.submit`:

```python
    def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
        result = self.backend.submit(
            self.plan, sheets, self.printer_name, self.copies, self.dpi
        )
        log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
        return result
```

`deckle/core/print_session.py:174-208` — `_SessionState`, which F9 extends by
one field. Note `from_json` reads every key with `data["..."]`:

```python
@dataclass
class _SessionState:
    """The full on-disk representation of a session's progress."""

    version: int
    session_id: str
    printer_name: str
    plan_hash: str
    started_at: float
    pass_index: int
    sheet_cursor: int
    sheets: list[int]
    test_first: bool
    test_sheet_pending: bool
    dpi: int
    copies: int
```

`deckle/app/views/print_dialog.py:217-218` — the checkbox's neighbour:

```python
        self.test_first_checkbox = QCheckBox("Test one sheet first", self.widget)
        layout.addWidget(self.test_first_checkbox)
```

### The test that must be deleted

`tests/test_duplex_submission.py:145-175`:

```python
def test_nothing_in_deckle_calls_this_yet():
    """The claim the docstring now makes, kept honest.

    If this ever fails it is good news -- someone wired the path up -- but
    the docstring and the open question about hardware duplexers versus a
    hand-measured correction both need revisiting at that moment, and this
    is what will say so.
    """
    ...
    assert callers == [], (
        "submit_duplex now has callers -- revisit its docstring and whether "
        f"a hardware duplexer should use a hand-measured offset: {callers}"
    )
```

**This test is doing its job.** Deleting it is the correct action and it must
be deliberate: it is a tripwire that has now fired, and its two instructions
— revisit the docstring, and answer the offset question — are §3's first two
steps. Delete it in the same commit that answers both, never before.

### Call sites

```
$ grep -rn "submit_duplex\|duplex_modes\|DuplexModes\|single_pass_duplex" deckle/ tests/
deckle/app/backend.py:233:@dataclass(frozen=True)  class DuplexModes
deckle/app/backend.py:282:  (comment)
deckle/app/backend.py:533:    def duplex_modes(...)
deckle/app/backend.py:553:    def submit_duplex(...)
deckle/app/backend.py:564,594  (docstring references)
tests/test_backend.py:239,250,255,266  (duplex_modes)
tests/test_duplex_submission.py:3,6,94,145,169,173
```

Nothing in `deckle/` outside `backend.py` mentions either symbol.

### Existing tests

`tests/test_duplex_submission.py` (five tests; four survive, one is deleted),
`tests/test_backend.py:239-270` (`duplex_modes` on a stubbed
`QPrinterInfo`), `tests/test_print_session.py`,
`tests/test_print_session_state_validation.py`,
`tests/test_print_dialog.py`, `tests/test_registration.py`.

## 3. Change

### 3.0 The two assumptions, written down first

Both go in `docs/decisions.md` (§7) and both are surfaced to the user rather
than taken in silence. Neither is a finding; each is a stated assumption with
the evidence that would overturn it.

**Assumption A — a hardware duplexer does not get the hand-measured back
offset.** The stored `back_offset_x_pt`/`back_offset_y_pt` describe a *manual
reload*: a stack re-registered by hand against the paper guides. A duplexer
turns the sheet inside its own paper path with the same mechanical
registration it used for the front, so the error it makes is a different
error and applying a hand-measured one is as likely to double it as to cancel
it. **Overturned by:** printing F3's registration target through hardware
duplex and reading a non-zero offset. If that happens the right fix is a
second pair of profile fields, not this switch — because the two numbers
would then be two different measurements, exactly as the current docstring
says.

**Assumption B — trust `supportedDuplexModes()`, and let the user
override.** It is used to *default* the checkbox, never to disable it. On
Linux it under-reports until a job has gone through the queue
(qprinter spike, `diverged`), so a user with a duplexer would otherwise be
told they do not have one and have no way to say otherwise. A wrongly-ticked
box costs one misprinted job; a wrongly-disabled control costs the feature.
**Overturned by:** a report of a driver that accepts `DuplexAuto` and
silently prints single-sided, which would make an un-defaulted, un-overridable
control the safer shape.

Both assumptions are the reason the checkbox is **unticked by default when
the query says no, ticked when it says yes, and always editable.**

### 3.1 `deckle/app/backend.py`

1. **`submit_duplex` gains a keyword and stops applying the offset by
   default:**

```python
    def submit_duplex(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        *,
        apply_back_offset: bool = False,
    ) -> PrintResult:
```

   The render call becomes:

```python
                            offset = (
                                (self.profile.back_offset_x_pt, self.profile.back_offset_y_pt)
                                if apply_back_offset
                                else (0.0, 0.0)
                            )
                            rendered = _render_sheet_side(
                                plan, sheet_index, side, dpi, False, False,
                                back_offset_pt=offset,
                            )
```

2. **Rewrite the docstring.** Delete *"**Nothing calls this yet.**"* and the
   paragraph explaining why it was said. Replace with what is now true:

```
        Reached when the user ticks "Printer has a duplexer" in the print
        dialog. ``duplex_modes(printer_name).single_pass_duplex`` supplies
        that checkbox's default; it does not gate it, because
        ``supportedDuplexModes()`` under-reports on Linux until a job has
        been through the queue (``docs/spikes/qprinter-capability-report.md``)
        and a wrongly-disabled control costs more than a wrongly-ticked box.

        **The calibrated back offset is NOT applied by default.** It was
        measured for a manual reload -- a stack re-registered by hand
        against the paper guides -- and a duplexer turns the sheet in its
        own paper path, so the error it makes is a different error and the
        hand-measured correction is as likely to double it as to cancel it.
        ``apply_back_offset=True`` restores the old behaviour for a caller
        that has measured otherwise. The dialog says which choice it made,
        because a measured number must not be discarded in silence.
```

   Keep the licence-free `log_print_job` call and the chunking prose.

3. **`log_event`** on entry, once per call:
   `log_event("duplex_submit", printer=printer_name, sheets=len(list(sheets)), apply_back_offset=apply_back_offset)`.
   The offset decision has to be visible in the JSON Lines log, because the
   user will not see it on the paper.

### 3.2 `deckle/core/printing.py`

`PrintPass.side` widens:

```python
    side: Literal["front", "back", "duplex"]
```

`:ivar side:` gains: *"``duplex`` is a single pass through a printer that
turns the sheet itself; there is no reload between faces and no second
pass."*

`plan_passes` gains a keyword-only parameter:

```python
def plan_passes(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheets: Sequence[int] | None = None,
    *,
    duplex: bool = False,
) -> list[PrintPass]:
```

with, before the existing return:

```python
    if duplex:
        # One pass, in plan order: the printer turns the sheet, so neither
        # the stack reversal nor the half turn applies -- both describe what
        # a person does between two passes, and there is only one.
        return [
            PrintPass(
                index=0,
                sheet_order=front_order,
                side="duplex",
                reload_instruction=_duplex_instruction(profile),
                rotate_backs=False,
            )
        ]
```

and

```python
def _duplex_instruction(profile: PrinterProfile) -> str:
    return (
        f"Load paper {_face_word(profile.output_face)}, feed edge "
        f"{profile.feed_edge}, and print. The printer turns each sheet "
        "itself -- there is no reload."
    )
```

**`reverse_stack` and `flip_axis` are deliberately not consulted.** Both
describe what a human does between two passes. Reading them here would be a
second opinion on a physical act that is not happening.

### 3.3 `deckle/core/print_session.py`

1. `__init__` gains `duplex: bool = False`, passed straight through:
   `plan_passes(plan, profile, sheets=sheets, duplex=duplex)`. Document it as
   *"submit both faces as one hardware-duplex job instead of two manual
   passes. The caller is responsible for knowing the printer has a duplexer;
   see ``QtPrintBackend.duplex_modes``."*

2. `_SessionState` gains `duplex: bool` and `from_json` reads it as
   `data.get("duplex", False)` — tolerant on read, because a state file
   written before this field exists is a manual job and `False` is exactly
   right for it. Everything else in `from_json` uses `data["..."]`; this one
   deviates and the reason is that its absence has a correct meaning, which
   is not true of `sheet_cursor`.

3. **Bump `STATE_VERSION` from 2 to 3** (`print_session.py:70`). A file
   *written* by this build and read
   by an older one would drive a manual reload for a job that has already
   printed both sides. `_check_state` compares versions
   (`print_session.py`'s `_check_state`, and B28 notes the ordering is wrong
   — do not fix that here, just bump).

4. `_submit_sheets` dispatches:

```python
    def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
        if pass_.side == "duplex":
            # The hand-measured back offset is a manual-reload measurement;
            # see QtPrintBackend.submit_duplex. Not passed, not silently
            # dropped -- the dialog says so.
            result = self.backend.submit_duplex(
                self.plan, sheets, self.printer_name, self.copies, self.dpi
            )
        else:
            result = self.backend.submit(
                self.plan, sheets, self.printer_name, self.copies, self.dpi
            )
        log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
        return result
```

   **B4 note:** `submit_duplex` already calls `log_print_job` itself (line
   661), so this logs the chunk twice — the exact double-logging B4 records
   for the manual path. Do not fix B4 here, but do not make it worse: add a
   comment naming B4 at both sites so the eventual fix finds both.

5. `PrintBackend` Protocol (`printing.py:82-92`) is **not** widened.
   `submit_duplex` stays a `QtPrintBackend` method reached only when the
   caller asked for duplex, and the Protocol keeps describing the minimum a
   backend must offer. Say so in a comment; a Protocol that lists everything
   any implementation happens to have is not a seam.

### 3.4 `deckle/app/views/print_dialog.py`

1. New checkbox, immediately after `test_first_checkbox`:

```python
        self.duplex_checkbox = QCheckBox(
            "Printer has a duplexer (print both sides in one pass)", self.widget
        )
        self.duplex_checkbox.setToolTip(
            "Let the printer turn each sheet itself: one pass, no reload, "
            "no flip.\n\n"
            "Ticked automatically when your printer reports a duplexer. "
            "On Linux that report can be wrong until the printer has had a "
            "job this session, so you can tick it yourself.\n\n"
            "The printer registers both sides itself, so the profile's "
            "front/back offset is not applied."
        )
        layout.addWidget(self.duplex_checkbox)
```

2. Default it from the backend, guarded, at the end of `__init__` and again
   whenever the printer changes:

```python
    def _sync_duplex_default(self) -> None:
        """Tick the duplex box when the selected printer reports a duplexer.

        Defaults only -- never disables. ``supportedDuplexModes()``
        under-reports on Linux until a job has been through the queue, so a
        user with a duplexer must be able to say so.
        """
        probe = self._duplex_probe
        if probe is None:
            return
        try:
            self.duplex_checkbox.setChecked(
                bool(probe(self.printer_combo.currentText()))
            )
        except Exception as exc:  # noqa: BLE001
            # A capability query is never worth failing a print dialog over.
            log_exception("duplex_query_failed", exc,
                          printer=self.printer_combo.currentText())
```

   with a new keyword-only constructor parameter
   `duplex_probe: Callable[[str], bool] | None = None`, defaulting to
   `lambda name: self._backend_cls(resolve_profile(name, self._profile_loader, self._builtin_presets)).duplex_modes(name).single_pass_duplex`.
   Injected so the headless tests never touch `QPrinterInfo`, exactly as
   `profile_loader` and `backend_cls` already are.

   Connect `self.printer_combo.currentTextChanged.connect(lambda _t: self._sync_duplex_default())`.

   **Do not call it during `__init__` before the widgets exist**, and do not
   call it from a paint or resize path: `docs/decisions.md` records printer
   enumeration on the UI thread hanging the app for 81 minutes.
   `duplex_modes` calls `QPrinterInfo.printerInfo(name)`, which is a single
   named lookup rather than an enumeration, but the same rule applies —
   `tests/test_ui_surface.py:357-365` is the guard.

3. `start_print` passes it and says what it decided:

```python
        duplex = self.duplex_checkbox.isChecked()
        ...
        session = self._session_cls(
            self.plan, profile, backend,
            test_first=self.test_first_checkbox.isChecked(),
            printer_name=printer_name,
            duplex=duplex,
            **kwargs,
        )
        if duplex and (profile.back_offset_x_pt or profile.back_offset_y_pt):
            self.status_label.setText(
                "Single-pass duplex: the printer registers both sides "
                "itself, so this profile's front/back offset "
                f"({profile.back_offset_x_pt:+g}, {profile.back_offset_y_pt:+g}pt) "
                "is not applied."
            )
```

   **That message is the whole of assumption A's honesty requirement.** A
   measured number is not discarded without saying so, and the person who
   measured it is the one reading this line.

4. Class docstring: add `:param duplex_probe:` beside the other injectables.

### 3.5 Delete the tripwire

Delete `tests/test_duplex_submission.py::test_nothing_in_deckle_calls_this_yet`
and the two paragraphs of the module docstring that describe the path as
dead (lines 1-14 and 16-21). Replace the module docstring's opening with a
statement of what the file now pins: the interleaving, the unavailable-printer
path, and **assumption A** — that the hand-measured offset is deliberately not
applied, and what would overturn it.

Do not delete `test_a_calibrated_offset_reaches_the_back_side`; **invert** it
(test 3 below), keeping its docstring's reasoning and reversing its
conclusion, so the decision is legible in the diff.

Remove `submit_duplex` from M7's dead-code list when that spec is written.

### 3.6 Docs

- GUIDE §6, after "The printer profile": a short subsection **"If your
  printer has a duplexer"** — tick the box, one pass, no reload; it is ticked
  for you when the printer reports one; on Linux the report can be wrong
  until the printer has had a job; and the profile's front/back offset is not
  applied because the printer registers both sides itself.
- `docs/spikes/qprinter-capability-report.md`: tick **Accepted — option 1
  (ship as-is)** in the owner-decision block and add one line under it
  recording that F9 defaults from the query and lets the user override, which
  is what the recommendation's *"revisit before adding a 'you have a duplex
  printer' prompt to the UI"* asked for. Fill in the reviewer and date.
- README feature table.

## 4. Tests

Changes to `tests/test_duplex_submission.py`:

1. **Delete** `test_nothing_in_deckle_calls_this_yet`.

2. `test_the_print_dialog_reaches_submit_duplex` — the replacement tripwire,
   pointing the other way. In `tests/test_print_dialog.py`: with
   `duplex_probe=lambda name: True` and a stub backend recording calls,
   `start_print()` results in a `submit_duplex` call and **no** `submit`
   call. Unfixed: `TypeError: __init__() got an unexpected keyword argument
   'duplex_probe'`.

3. `test_a_hardware_duplexer_does_not_get_the_hand_measured_offset`
   — the inversion of the old
   `test_a_calibrated_offset_reaches_the_back_side`. Profile with
   `back_offset_x_pt=3.0, back_offset_y_pt=-1.5`; `submit_duplex` with no
   `apply_back_offset`; every recorded render got `(0.0, 0.0)`. Docstring
   states assumption A and names F3's target as what would overturn it.
   Unfixed: fails — the current code passes `(3.0, -1.5)`.

4. `test_apply_back_offset_restores_the_correction`
   `apply_back_offset=True` → `(3.0, -1.5)` reaches every render. The escape
   hatch exists and is tested, so overturning assumption A is a one-line
   caller change and not a rewrite.

5. `test_both_sides_of_every_sheet_are_rendered` and
   `test_an_unavailable_printer_is_reported_rather_than_painted` — unchanged.

New tests in `tests/test_printing.py`:

6. `test_duplex_plans_a_single_pass`
   `len(plan_passes(plan, profile, duplex=True)) == 1`, `side == "duplex"`,
   `rotate_backs is False`, `sheet_order` in plan order. Unfixed:
   `TypeError: plan_passes() got an unexpected keyword argument 'duplex'`.

7. `test_duplex_ignores_reverse_stack_and_flip_axis`
   With `reverse_stack=True, flip_axis="long"`, the single pass is still in
   plan order and still `rotate_backs=False`. Both fields describe what a
   person does between two passes.

8. `test_the_duplex_instruction_says_there_is_no_reload`
   The instruction contains `"there is no reload"` and does **not** contain
   `"pass 2"` or `"reverse"`.

9. `test_manual_planning_is_unchanged`
   `plan_passes(plan, profile)` still returns two passes with the existing
   orders and instructions. The regression guard for every existing user.

New tests in `tests/test_print_session.py`:

10. `test_a_duplex_session_submits_through_submit_duplex_and_finishes_in_one_pass`
    Stub backend with both methods; drive to `finished`; assert only
    `submit_duplex` was called, once per chunk, and `state["pass_index"]`
    never exceeded 0.

11. `test_a_duplex_session_resumes_as_a_duplex_session`
    Save, `load`, and assert the reloaded session still plans one pass. Fails
    on an implementation that forgets to persist `duplex`, in the most
    expensive possible way: a resumed job that reprints every front.

12. `test_a_state_file_without_the_duplex_key_loads_as_manual`
    Hand-write a state dict omitting `duplex`; `from_json` gives `False`.
    Tolerant on read, because its absence has a correct meaning.

13. `test_the_state_version_was_bumped`
    `STATE_VERSION == 3` (it is `2` on the current tree,
    `print_session.py:70`). Cheap, and it is what stops a silent format
    change.

New tests in `tests/test_print_dialog.py`:

14. `test_the_duplex_box_defaults_from_the_probe`
    `duplex_probe=lambda name: True` → ticked; `lambda name: False` →
    unticked.

15. `test_the_duplex_box_is_never_disabled`
    Even with `duplex_probe=lambda name: False`,
    `duplex_checkbox.isEnabled() is True`. **Assumption B as an assertion.**

16. `test_a_failing_probe_leaves_the_dialog_usable`
    `duplex_probe` raises; the dialog constructs, the box is unticked and
    enabled, and `start_print()` still works.

17. `test_changing_printer_re_defaults_the_box`
    A probe that answers per name; selecting the other printer re-ticks.

18. `test_the_dialog_says_the_offset_was_not_applied`
    Duplex ticked, profile with a non-zero offset →
    `status_label.text()` contains `"is not applied"` and both numbers.
    **Assumption A's honesty requirement as an assertion.**

19. `test_a_zero_offset_produces_no_such_message`
    Nothing to say, so nothing said. Advice that repeats a no-op back at the
    user is what teaches people to stop reading it.

20. In `tests/test_ui_surface.py`:
    `test_the_duplex_probe_does_not_enumerate_printers` — monkeypatch
    `_available_printer_names` to a spy, construct the dialog with the real
    default probe against a stub backend class, assert the spy was not
    called.

## 5. Acceptance

| Check | Command |
|---|---|
| The tripwire is gone | `! grep -rn "test_nothing_in_deckle_calls_this_yet" tests/` |
| Something calls it now | `grep -rn "submit_duplex" deckle/core/print_session.py` |
| The docstring no longer claims it is dead | `! grep -q "Nothing calls this yet" deckle/app/backend.py` |
| Duplex submission tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_duplex_submission.py` |
| Assumption A is asserted | `.venv/bin/python -m pytest -q -k hardware_duplexer_does_not_get_the_hand_measured_offset` |
| Assumption B is asserted | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -k duplex_box_is_never_disabled` |
| Manual planning unchanged | `.venv/bin/python -m pytest -q tests/test_printing.py tests/test_print_session.py` |
| The state version moved | `grep -q "^STATE_VERSION = 3" deckle/core/print_session.py` |
| The `PrintBackend` Protocol was not widened | `! grep -q "submit_duplex" deckle/core/printing.py` |
| The spike's owner decision is filled in | `grep -q "\[x\] Accepted" docs/spikes/qprinter-capability-report.md` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| **[HUMAN] one pass on real hardware** | On a duplexer, print a 4-sheet job with the box ticked. Four sheets, both sides, no reload prompt. Check the backs are upright and in register. |
| **[HUMAN] assumption A, measured** | With F3 landed: print its registration target through hardware duplex on a printer whose profile carries a non-zero offset. The target should read near zero. **If it reads the profile's own numbers back, assumption A is wrong** and the answer is a second pair of profile fields, not `apply_back_offset=True` on this call site. |

Greps run against the current tree:

```
$ grep -rn "submit_duplex" deckle/ | grep -v "app/backend.py"
(no output — exit 1)
$ grep -c "Nothing calls this yet" deckle/app/backend.py
1
$ grep -rn "test_nothing_in_deckle_calls_this_yet" tests/
tests/test_duplex_submission.py:145:def test_nothing_in_deckle_calls_this_yet():
```

## 6. Out of scope

- **B4** — `log_print_job` being called by both the backend and the session,
  so every chunk is logged twice, and the session's unguarded call raising
  after the sheets printed. F9 adds a second site with the same shape;
  comment it, do not fix it, and make sure B4's spec knows about it.
- **B17** — submission running synchronously on the GUI thread. Hardware
  duplex halves the number of jobs, not their blocking.
- **B28** — `print_session` checking `version` *after* the state check, so a
  newer file is misreported. F9 bumps the version and inherits the bug.
- **Remembering the checkbox per printer.** It is session-only. The right
  home is a `PrinterProfile` field, which is a profile-shape change and
  therefore F12's, and F1's editor is where it would be edited.
- **`DuplexLongSide` versus `DuplexShortSide`.** `submit_duplex` sets
  `DuplexAuto` and lets the driver choose; `duplex_flip_edge` already
  computes the right named edge for the exported PDF's `/Duplex` entry, and
  two opinions about one physical turn is how a stack comes out upside down.
- **F8 (French fold).** A single-sided plan never wants a duplexer;
  `plan_passes` returning one pass covers it there for a different reason.
- **M7's other dead code** — `DRIVERS_IGNORING_ROTATE`, the `session_log`
  import fallbacks, `try: ... finally: pass`.

## 7. decisions.md entry

```
## 2026-09-05 — Wired hardware duplex, and wrote down the two assumptions it rests on
- Symptom: `QtPrintBackend.submit_duplex` was seventy lines of complete, tested Qt printing code that nothing called, guarded by a test asserting nothing called it. A user with a duplexer walked to the printer, reversed a stack and flipped every sheet on a machine that would have done it in one pass unattended.
- Fix: a "Printer has a duplexer (print both sides in one pass)" checkbox on the print dialog; `plan_passes(..., duplex=True)` returns a single `side="duplex"` pass; `PrintSession` dispatches to `submit_duplex` and persists the flag so a resume stays duplex; `STATE_VERSION` bumped. `test_nothing_in_deckle_calls_this_yet` deleted deliberately -- it was a tripwire that fired, and its two instructions were the first two steps of this change.
- Surfaces: two open questions were answered by assumption, both stated rather than taken in silence. (A) A hardware duplexer does NOT get the hand-measured back offset: that number describes a stack re-registered by hand against the paper guides, and a duplexer's error is a different error, so applying it is as likely to double it as to cancel it. The dialog says so, with the numbers, whenever a non-zero offset is skipped. (B) `supportedDuplexModes()` defaults the checkbox and never disables it, because it under-reports on Linux until a job has gone through the queue -- a wrongly-ticked box costs one job, a wrongly-disabled control costs the feature.
- Watch: `submit_duplex` calls `log_print_job` and so does `PrintSession._submit_sheets`, so a duplex chunk is logged twice -- the same double-logging B4 records for the manual path, now at a second site, commented at both. And `plan_passes` deliberately ignores `reverse_stack` and `flip_axis` under duplex: both describe what a person does between two passes, and there is only one.
- Commit: <fill in>
```

## 8. Traps

- **Delete the tripwire last, not first.** It exists to make someone read the
  docstring and answer the offset question. Answer both, then delete it in
  the same commit.
- **Persist `duplex` in the session state.** A resumed duplex job that
  reverts to manual reprints every front and then asks the user to flip a
  stack that is already printed on both sides. Test 11 is the guard.
- **Bump `STATE_VERSION`.** An older build reading a new state file would
  drive a manual reload for a job already printed both sides.
- **`from_json` reads every other key with `data["..."]`.** `duplex` uses
  `.get(..., False)` because its absence has a correct meaning. Do not
  convert the rest.
- **Do not widen the `PrintBackend` Protocol.** It describes the minimum a
  backend must offer; `submit_duplex` is reached only when the caller asked
  for duplex. A Protocol listing everything one implementation happens to
  have is not a seam.
- **Do not query the printer on a paint, resize or construction-time hot
  path.** `duplex_modes` calls `QPrinterInfo.printerInfo(name)` — a named
  lookup, not an enumeration — but `docs/decisions.md` records an
  enumeration on the UI thread hanging the app for 81 minutes, and
  `tests/test_ui_surface.py` guards it.
- **`submit_duplex` currently passes `back_offset_pt` for the FRONT face
  too.** Read `_render_sheet_side` (`backend.py:169`) before changing the
  argument; whatever the manual path does, the two should agree or the
  difference should be explained.
- **`QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit
  127).** Construct and drive.
- **`python -m deckle` launches the GUI and blocks.**
