# N3 — Print a proof sheet with a ruler, from the print dialog

**Roadmap item:** `docs/ROADMAP.md` N3
**Depends on:** —. Edits `deckle/app/backend.py` and `deckle/app/views/print_dialog.py`; **N4** edits the same dialog and should land next to it (N3 first — it is smaller and adds the shared "one-off action that is not a session" seam N4 also wants).
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

The schedule tells the user, in `_printer_lines`
(`deckle/core/schedule.py:322-326`):

```
  Print sheet 0 on its own first -- deckle export --sheets 0.
    Check the back is upright and the spine margin falls on
    the bound edge before committing the rest of the stack.
    Add --rule to print a measurable ruler on it: if the
    ruler is short, the printer scaled the page.
```

That is the only actual-size check Deckle has, and the desktop app cannot
perform it. A GUI user is told to open a terminal, retype their whole layout as
flags (the GUI has five paper presets, the CLI has three — M5), export a PDF,
and print it from a viewer whose "fit to page" default is the exact thing being
tested for.

Meanwhile the print dialog's own "Test one sheet first" checkbox does something
different and less useful: it starts a real `PrintSession`, prints sheet 0
*without* a rule, writes session state to disk, and then asks "Did the test
sheet print correctly?" — a question the user has no instrument to answer.

Verified: `--rule` is reachable only from `deckle export`.

```bash
$ grep -rn "rule=" deckle/app deckle/core/render.py
```

returns nothing — no path from the app to `export(..., rule=True)`.

## 2. Current code

`deckle/core/export.py:528-536` — the parameter, already there:

```python
def export(
    plan: SheetPlan,
    out_path: str,
    sheets: Sequence[int] | None = None,
    rule: bool = False,
    side: str | None = None,
    rotate_180: bool = False,
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
) -> None:
```

`deckle/cli.py:1001-1010` — the only caller that passes it:

```python
        export_plan(
            plan,
            args.output,
            sheets=selection,
            rule=args.rule,
            side=side,
            rotate_180=rotate_180,
            back_offset_pt=back_offset,
        )
```

`deckle/cli.py:1264-1271` — the flag:

```python
    export_parser.add_argument(
        "--rule",
        action="store_true",
        help=(
            "draw a ruler of known length on every sheet, to check whether "
            "the printer scaled the page. Use on a proof, not on the job"
        ),
    )
```

`deckle/cli.py:1026-1027` and `1031-1052` — `_report_rule(plan.paper_pt[0])`,
which turns the rule into a check with a pass condition by naming the expected
length in whole inches.

`deckle/core/export.py:456-473` — `proof_rule_length_pt(paper_width_pt)`, pure,
returns a whole number of inches in points or `0.0` for a sheet too narrow.

`deckle/app/backend.py:169-231` — `_render_sheet_side(plan, sheet_index, side,
dpi, rotate_backs, ignore_rotate, back_offset_pt=(0.0, 0.0))`. The fast path
delegates to `render_sheet` (`render.py:187`, whose signature is
`(plan, sheet_index, side, dpi, cancel=None)` — **no `rule` parameter, and the
export cache behind it is keyed on the plan alone**). The slow path already
exports for itself:

```python
        export.export(
            plan, tmp_path, sheets=[sheet_index], back_offset_pt=back_offset_pt
        )
```

`deckle/app/backend.py:474-513` — `_submit_chunk`, which builds the
`QPrinter`, calls `setFullPage(True)`, begins a `QPainter`, and paints each
sheet through `_paint_rendered_page`.

`deckle/app/views/print_dialog.py:217` — the existing checkbox, which is a
*session* feature, not a proof:

```python
        self.test_first_checkbox = QCheckBox("Test one sheet first", self.widget)
```

`deckle/app/views/print_dialog.py:253-280` — `start_print`, the only thing the
dialog's Print button does; it always constructs a `PrintSession`, which writes
resumable state (`print_session.py:583, 586`).

**Call sites of `proof_rule_length_pt` (grep):** `deckle/cli.py:25` (import),
`cli.py:1040`; `deckle/core/export.py:456` (definition), `export.py:487` (inside
`_draw_proof_rule`); `tests/test_export.py`, `tests/test_cli_sheets.py`.

**Call sites of `_render_sheet_side` (grep):** `deckle/app/backend.py:169`
(definition), `backend.py:499` (`_submit_chunk`), `backend.py:638`
(`submit_duplex`); `tests/test_backend.py`, `tests/test_registration.py`.

**Existing tests:** `tests/test_print_dialog.py` (the dialog's injectable
seams), `tests/test_backend.py` (chunking, rotation, `_render_sheet_side`),
`tests/test_print_painting.py` (`_paint_rendered_page`),
`tests/test_cli_sheets.py` (`--sheets`/`--rule` end to end).

## 3. Change

A proof is **not a print job**. It prints one face of one sheet, once, and
leaves nothing behind: no `PrintSession`, no state file in
`tempfile.gettempdir()`, no cursor, no resume offer at the next launch. That is
the whole design decision. The rejected alternative — a `rule=True` flag on
`PrintSession` — would put a proof into the resume list, where accepting it
would reprint a book from sheet 1.

### Steps

1. **`deckle/core/export.py` — nothing.** `rule=True` already exists.

2. **`deckle/app/backend.py` — thread `rule` into the self-export path.**
   Change `_render_sheet_side`'s signature to add a keyword-only parameter:

   ```python
   def _render_sheet_side(
       plan: SheetPlan,
       sheet_index: int,
       side: Literal["front", "back"],
       dpi: int,
       rotate_backs: bool,
       ignore_rotate: bool,
       back_offset_pt: tuple[float, float] = (0.0, 0.0),
       *,
       rule: bool = False,
   ) -> RenderedPage:
   ```

   and change the fast-path guard at `backend.py:185-191` so a ruled render
   never takes it:

   ```python
       corrected = side == "back" and back_offset_pt != (0.0, 0.0)
       if not (side == "back" and rotate_backs) and not corrected and not rule:
   ```

   with the existing comment extended by one sentence: *"…and the rule is not
   part of the plan at all, so a cached unruled render would answer a request
   for a ruled one."* Then pass it to the export call at `backend.py:203`:

   ```python
           export.export(
               plan, tmp_path, sheets=[sheet_index], rule=rule,
               back_offset_pt=back_offset_pt,
           )
   ```

   Add `:param rule:` to the docstring: *"draw a measurable ruler on the face.
   Forces a private export -- the shared preview cache is keyed on the plan,
   which knows nothing about a rule."*

3. **`deckle/app/backend.py` — the one-off submit.** New method on
   `QtPrintBackend`, placed after `submit_pass`:

   ```python
       def submit_proof(
           self,
           plan: SheetPlan,
           sheet_index: int,
           printer_name: str,
           dpi: int = 300,
           side: Literal["front", "back"] = "front",
       ) -> PrintResult:
           """Print one face of one sheet, with a measurable ruler on it.

           Not a pass and not a session. A proof answers one question --
           "did the driver print this at actual size?" -- and answering it
           must not leave resumable state on disk, because a proof appearing
           in the resume list would offer to continue a book from sheet 1
           that was never started.

           :param plan: the imposed sheets.
           :param sheet_index: which sheet to proof. The dialog offers 0.
           :param printer_name: the target queue. Empty means the system
               default.
           :param dpi: rasterisation resolution.
           :param side: which face. Front, because the rule is a
               scaling check and the front is what comes out first.
           :returns: a :class:`~deckle.core.printing.PrintResult`. A print
               failure comes back through ``error``, never raised -- the
               same contract :meth:`submit` gives.
           """
   ```

   Body: the availability check from `submit` (`backend.py:333-343`, same
   `print_printer_unavailable` event with `side="proof"`), then a
   `try/except Exception` around a private `_submit_proof_chunk` that is
   `_submit_chunk`'s body for exactly one sheet with `rule=True`, logging
   `print_proof_failed` on failure. **It does not call `log_print_job`** — the
   session log records book pages committed to paper, and a proof is a scrap
   sheet; adding it would inflate every later count.

   Set `self.submitted_sheets = [sheet_index]` on success and
   `self.uncertain_sheets = [sheet_index]` on failure, so the three-way split
   that `submit_pass` maintains stays meaningful if someone reads it after a
   proof.

4. **`deckle/app/views/print_dialog.py` — the button.** After the signature row
   and before `self.status_label` (`print_dialog.py:234`):

   ```python
           self.proof_button = QPushButton("Print a proof sheet with ruler", self.widget)
           self.proof_button.setToolTip(
               "Print sheet 0 on its own, with a ruler of known length drawn "
               "on it, and nothing else.\n\n"
               "Measure the printed ruler. If it is short, your driver "
               "scaled the page -- turn off \"fit to page\" and every other "
               "scaling option and try again. It is the only direct evidence "
               "that what comes out is the size Deckle computed.\n\n"
               "This is a scrap sheet: it starts no print job and can never "
               "be resumed."
           )
           self.proof_button.clicked.connect(self.print_proof)
           layout.addWidget(self.proof_button)
   ```

   Exact label string: `"Print a proof sheet with ruler"`. A button, not a
   checkbox: a checkbox next to Print reads as "also do this to the job", and
   the whole point is that a proof is a separate, cheap, standalone act.

5. **`deckle/app/views/print_dialog.py` — the handler.**

   ```python
       def print_proof(self) -> None:
           """Print sheet 0 alone, with a ruler, and start no session.

           :returns: nothing. A printer that is offline is reported through
               ``show_offline_error`` exactly as a real run is; nothing is
               left resumable either way.
           """
           if not self.plan.sheets:
               self.status_label.setText(PROOF_NO_SHEETS_MESSAGE)
               return
           printer_name = self.printer_combo.currentText()
           profile = self._resolve_profile(printer_name)
           backend = self._backend_cls(profile)
           sheet_index = self.plan.sheets[0].index
           result = backend.submit_proof(self.plan, sheet_index, printer_name)
           if result.error is not None:
               self._show_offline_error(printer_name, result.error)
               return
           self.status_label.setText(proof_measurement_message(self.plan.paper_pt[0]))
   ```

   `self.plan.sheets[0].index` rather than the literal `0`, because a plan
   filtered by `export(sheets=)` upstream need not start at 0 — and because
   `_report_missing_sheets` (`cli.py:336`) exists precisely because a
   nonexistent index writes an empty artefact silently.

6. **`deckle/app/views/print_dialog.py` — the message, pure and shared.** At
   module level, above the Qt wiring:

   ```python
   PROOF_NO_SHEETS_MESSAGE = "Nothing to proof yet -- import a document first."

   def proof_measurement_message(paper_width_pt: float) -> str:
       """What the printed rule should measure, and what a short one means.

       The same sentence ``deckle export --rule`` prints (see
       ``deckle.cli._report_rule``): the rule is labelled on the sheet, but
       the number belongs where the person about to walk to the printer is
       looking. One wording, two front ends -- the reasoning
       :mod:`deckle.core.outputs` already applies to write failures.
       """
       length = proof_rule_length_pt(paper_width_pt)
       if length <= 0:
           return (
               "This sheet is too narrow for a rule, so none was drawn."
           )
       inches = int(round(length / 72.0))
       return (
           f"Proof sent. Measure the printed rule: it should be {inches} in "
           'exactly. If it is short, the printer scaled the page -- turn off '
           '"fit to page" and print again.'
       )
   ```

   with `from deckle.core.export import proof_rule_length_pt` added to the
   module's imports (`print_dialog.py:19-24`). `deckle.core.export` is Qt-free,
   so this does not break the module's "importable without a display" property.

   The wording is deliberately the CLI's, minus the leading `measure the` and
   plus a `Proof sent.` prefix, so a user who has read GUIDE §5 or the schedule
   sees the same instruction in the same words.

7. **`deckle/app/views/print_dialog.py` — the module docstring.** Its first
   paragraph says every sequencing decision lives in `PrintSession`. Add:

   > The one action that is deliberately not a session is the proof sheet:
   > it prints one face of one sheet with a ruler on it and writes no state,
   > because a proof in the resume list would offer to continue a book that
   > was never started.

## 4. Tests

`tests/test_print_dialog.py` already builds real `PrintDialog` widgets headless
and injects every dependency; add to it. `tests/test_backend.py` already
exercises `_render_sheet_side` and `_chunked` directly.

### `tests/test_backend.py` (extend)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_a_ruled_render_never_uses_the_shared_cache` | monkeypatch `deckle.app.backend.render_sheet` to a function that raises `AssertionError("cache path taken")`; call `_render_sheet_side(plan, 0, "front", 72, False, False, rule=True)` | it returns a non-degenerate `RenderedPage` without the patched function being called | `TypeError: _render_sheet_side() got an unexpected keyword argument 'rule'` |
| `test_an_unruled_render_still_uses_the_shared_cache` | same monkeypatch, `rule=False` | `AssertionError: cache path taken` is raised — i.e. the fast path is untouched | `TypeError` as above |
| `test_the_proof_export_asks_for_the_rule` | monkeypatch `deckle.app.backend.export.export` to record its kwargs; `_render_sheet_side(..., rule=True)` | the recorded call has `rule=True` and `sheets=[0]` | `TypeError` as above |
| `test_a_proof_records_no_print_job` | monkeypatch `deckle.app.backend.log_print_job` to raise `AssertionError`; a `QtPrintBackend` whose `_submit_proof_chunk` is patched to a no-op; call `submit_proof(plan, 0, "P")` | no raise, `result.error is None` | `AttributeError: 'QtPrintBackend' object has no attribute 'submit_proof'` |
| `test_a_proof_to_a_vanished_printer_reports_rather_than_raises` | monkeypatch `printer_is_available` to `False` | `result.error == "printer 'P' is no longer available"` and `result.submitted == 0` | `AttributeError` as above |
| `test_a_failing_proof_marks_the_sheet_uncertain` | `_submit_proof_chunk` patched to raise `RuntimeError("offline")` | `result.error == "offline"` and `backend.uncertain_sheets == [0]` | `AttributeError` as above |

### `tests/test_print_dialog.py` (extend)

Use the module's existing fake backend/session classes; add a
`_ProofRecordingBackend` exposing `submit_proof` and a `calls` list.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_proof_button_prints_sheet_zero_with_a_rule` | after `dialog.proof_button.click()`, the fake backend recorded one `submit_proof(plan, 0, "Printer A", ...)` call | `AttributeError: 'PrintDialog' object has no attribute 'proof_button'` |
| `test_the_proof_starts_no_session` | the injected `session_cls` is a class whose `__init__` raises `AssertionError("a proof must not start a session")`; clicking the proof button does not raise, and `dialog._session is None` | `AttributeError` as above |
| `test_the_proof_says_what_the_rule_should_measure` | Letter plan (612pt wide); `dialog.status_label.text()` contains `"it should be 8 in exactly"` | `AttributeError` as above |
| `test_a_narrow_sheet_says_no_rule_was_drawn` | `proof_measurement_message(60.0)` (under one inch of clearance) | equals `"This sheet is too narrow for a rule, so none was drawn."` | `ImportError: cannot import name 'proof_measurement_message'` |
| `test_a_proof_with_no_sheets_says_so` | a `SheetPlan` with `sheets=[]`; click | `status_label.text() == PROOF_NO_SHEETS_MESSAGE` and the backend recorded nothing | `AttributeError` as above |
| `test_an_offline_printer_during_a_proof_is_reported` | fake backend returning `PrintResult(0, None, "offline")`; injected `show_offline_error` records | recorded once with `("Printer A", "offline")` | `AttributeError` as above |

### Parity with the CLI

`tests/test_output_command_parity.py` already exists for "two front ends, one
explanation". Add:

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_gui_and_cli_agree_on_what_the_rule_should_measure` | for Letter and A4 widths, `proof_measurement_message(w)` and the stdout of `deckle export --rule` both contain the same `f"{inches} in exactly"` substring | `ImportError` for `proof_measurement_message` |

## 5. Acceptance

| Check | Command |
|---|---|
| The new dialog tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py` |
| The new backend tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_backend.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| A proof never constructs a session | `! grep -n "session_cls\|PrintSession" <(sed -n '/def print_proof/,/^    def /p' deckle/app/views/print_dialog.py)` |
| A proof is never written to the session log | `! grep -n "log_print_job" <(sed -n '/def submit_proof/,/def duplex_modes/p' deckle/app/backend.py)` |
| The rule reaches the app at all | `grep -c "rule=" deckle/app/backend.py` (today: 0, after: ≥2) |
| The button's label is exactly as specified | `grep -q 'QPushButton("Print a proof sheet with ruler"' deckle/app/views/print_dialog.py` |
| `[HUMAN]` The proof is actually actual size | With a real printer: import `tests/fixtures/sample.pdf` (Letter), open Print, click **Print a proof sheet with ruler**, and measure the printed rule with a tape. The status bar says 8 in; the paper must agree to within about 1 mm. A short rule means the driver scaled — that is the finding, not a bug. |

## 6. Out of scope

- **The "Test one sheet first" checkbox.** It is a different thing — a
  session-scoped first sheet with a confirm prompt — and it stays exactly as it
  is. Do not merge them; do not remove it.
- **B6** (printing scales the sheet into the imageable area with aspect
  ignored). A proof measured against a Deckle that does that will read short by
  ~6% *for that reason*, which is arguably the proof doing its job. It is still
  B6's open owner decision, and N3 must not pre-empt it.
- **B14** (cancelling the resume-count prompt returns 0). Same dialog, unrelated
  path.
- **N4** (Save front/back pass PDF). Same dialog, next spec, same "not a
  session" seam.
- **F3** (a registration target with numbers you read off). A ruler answers
  "was it scaled"; F3 answers "how far is the back off". Different targets.
- **F6** (sheet-subset reprint from the GUI). The proof always takes the plan's
  first sheet; choosing an arbitrary subset is F6.

## 7. decisions.md entry

```
## 2026-09-05 — The only actual-size check was CLI-only
- Symptom: The binding schedule instructs every user to run `deckle export --sheets 0 --rule`, measure the printed ruler, and turn off "fit to page" if it comes up short. From the desktop app that meant reopening the job as CLI flags -- across two paper-preset tables that do not match -- and printing from a viewer whose scaling default is the thing under test. `grep -rn "rule=" deckle/app` returned nothing.
- Fix: A "Print a proof sheet with ruler" button on the print dialog. It prints the plan's first sheet, front only, with `export(rule=True)`, through a new `QtPrintBackend.submit_proof` -- and starts no `PrintSession`, writes no state file, and does not touch the session log. `_render_sheet_side` gained a keyword-only `rule` that forces its private-export branch, because the shared sheet cache is keyed on the plan and a plan knows nothing about a rule. The status bar says what the rule should measure, in the CLI's own words.
- Surfaces: A proof is not a job. Putting `rule` on `PrintSession` would have been fewer lines and would have left a proof sitting in the resume list, where accepting it offers to continue a book from sheet 1 that was never started.
- Watch: `render_sheet`'s cache key is the plan hash. Any future render option that is not part of the plan -- a rule, a watermark, a registration target -- has the same problem and needs the same private-export branch, not a wider cache key.
- Commit: <fill in>
```

## 8. Traps

- **`render_sheet`'s cache is keyed on the plan hash** (`export._plan_hash`,
  `export.py:87`), which does not include `rule`. Passing `rule` down to
  `render_sheet` would be worse than useless: a ruled request could be answered
  from an unruled cache entry, or an unruled print could be answered from a
  ruled one and put a ruler on a real book. Step 2's guard is the whole point.
- **`export(sheets=[i])` silently skips an index the plan does not have**
  (`export.py:589-593`, B20) and writes a zero-page PDF that passes
  `_verify_output`. Take the index from `plan.sheets[0].index`, never a literal.
- **`_submit_chunk` calls `setFullPage(True)`** and the proof path must too, or
  the proof and the job will be at different scales — which would make the
  proof lie in exactly the direction it exists to detect.
- **`log_print_job` raises rather than swallowing** by design
  (`backend.py:369-399`). The proof path does not call it; do not "fix" that by
  adding it.
- **`deckle/app/views/print_dialog.py` must stay importable without a display.**
  Keep `proof_measurement_message` and `PROOF_NO_SHEETS_MESSAGE` above the
  `# -- Qt wiring --` divider at line 85, with `proof_rule_length_pt` imported
  at module top (`deckle.core.export` is Qt-free).
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`
  for the parity test.
- **Constructing a real `QMainWindow` under pytest exits 127 here.** A
  `PrintDialog` is a `QDialog` and constructs fine — `tests/test_print_dialog.py`
  and `tests/test_ui_surface.py` both do it — so no subprocess is needed.
