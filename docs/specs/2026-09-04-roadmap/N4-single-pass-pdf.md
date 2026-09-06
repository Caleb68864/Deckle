# N4 — Save one manual-duplex pass as a PDF, from the app

**Roadmap item:** `docs/ROADMAP.md` N4
**Depends on:** —, but reads `MainWindow.profile` if **N2** has landed (step 3 states the fallback if it has not). Edits `deckle/app/main.py`, so it collides with **M3** (which splits `MainWindow`), **N7**, **N8**, **N13**, **B9/B10/N9**. Recommended order within that group: B9/B10 → N7 → N8 → N4 → N13 → M3 last, so M3 moves finished code rather than being re-merged against each.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Deckle drives the printer, and that is its advantage. It is also, for one real
user, the problem: someone whose own printer cannot handle 200 sheets of 120gsm
takes the job to a copy shop, or to the machine in the other room. Today the
app's only file output is `Save PDF...`, which writes **both faces interleaved**
— front, back, front, back. Handed to a shop, that is a duplex job the shop
cannot do manually in the right order, and handed to a second machine it means
the user has to split it themselves, in the right sheet order, with the right
half-turn on every back. Getting the order wrong is invisible until the whole
stack is printed.

The CLI can do it. `deckle export --pass front --profile generic_face_down_reversed`
writes exactly the front pass, in the order the reload behaviour demands, and
prints the reload instruction. Nothing in `deckle/app` reaches it.

Verified — no path from the app to `plan_passes`:

```bash
$ grep -rn "plan_passes" deckle/app
deckle/app/views/print_dialog.py:224:    # arithmetic already lives in `plan_passes`/`PrintSession` -- this
deckle/app/views/print_dialog.py:259:        -- reprinting one gathering is the normal path with a smaller
```

Both hits are comments.

## 2. Current code

`deckle/cli.py:971-1028` — the composition N4 must reproduce exactly:

```python
    side = None
    rotate_180 = False
    print_pass = None
    back_offset = (0.0, 0.0)
    offset_source = ""
    if args.pass_side is not None:
        if args.profile is None:
            print(
                f"error: --pass {args.pass_side} needs --profile, because "
                "neither the sheet order nor the half turn has a safe "
                "default -- guessing wrong prints every back onto the wrong "
                "front. Built-in profiles: "
                f"{', '.join(sorted(BUILTIN_PRESETS))}",
                file=sys.stderr,
            )
            return 1
        profile = _resolve_profile(args.profile)
        if profile is None:
            return 1
        back_offset = (profile.back_offset_x_pt, profile.back_offset_y_pt)
        offset_source = f"profile {args.profile!r}"
        print_pass = _pass_for(plan, args.pass_side, profile, selection)
        side = args.pass_side
        selection = print_pass.sheet_order
        rotate_180 = print_pass.side == "back" and print_pass.rotate_backs
```

then

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

and afterwards, at `cli.py:1021-1025`:

```python
    print(f"wrote {args.output}")
    if back_offset != (0.0, 0.0):
        _report_registration(back_offset, offset_source)
    if print_pass is not None:
        print(print_pass.reload_instruction)
```

Four things compose, and all four come from the profile:

1. `print_pass.sheet_order` — reversed for the back pass when
   `profile.reverse_stack` (`printing.py:148`).
2. `side` — `"front"` or `"back"`, which makes `export` write one page per
   sheet and pad an absent face (`export.py:565-570`).
3. `rotate_180` — `profile.flip_axis == "long"` (`printing.py:164`), and only
   on the back.
4. `back_offset_pt` — `(profile.back_offset_x_pt, profile.back_offset_y_pt)`,
   applied by `export` to **back faces only**, and negated when `rotate_180`
   turns the face (`export.py:573-575`).

`deckle/cli.py:451-461` — `_pass_for(plan, side, profile, sheets)`:

```python
def _pass_for(plan, side: str, profile, sheets: list[int] | None):
    """The :class:`~deckle.core.printing.PrintPass` for one side.

    Everything here comes from ``plan_passes`` -- the sheet order, the
    half turn, the reload wording. The CLI decides none of it: a second
    implementation of the ordering table would be free to disagree with
    the desktop app about the same printer, and the paper would be wrong
    while both halves looked right.
    """
    passes = plan_passes(plan, profile, sheets=sheets)
    return next(print_pass for print_pass in passes if print_pass.side == side)
```

`deckle/cli.py:404-418` — `_report_registration(offset_pt, source)`, the
"back faces were moved and by how much" line.

`deckle/app/main.py:497-501` — where the two file/paper exits live today:

```python
        self.save_pdf_button = QPushButton("Save PDF...", controls)
        controls_layout.addWidget(self.save_pdf_button)

        self.print_button = QPushButton("Print...", controls)
        controls_layout.addWidget(self.print_button)
```

`deckle/app/main.py:1062-1110` — `_on_save_pdf_clicked`: the whole
dialog → suffix → `output_path_problem` → `export` → `describe_write_failure`
shape N4 reuses.

`deckle/app/main.py:770-783` — `suggested_export_name()`, `book.pdf` →
`book-deckle.pdf`.

`deckle/app/main.py:585-602` — `_sync_document_actions`, which gates
`save_pdf_button` and `save_project_button` on `bool(self.state.project.pages)`.

`deckle/app/main.py:78` — `DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))`,
i.e. `generic_face_down_reversed`.

**Call sites of `plan_passes` (grep):** `deckle/core/printing.py:134`
(definition), `deckle/cli.py:26,460`, `deckle/core/print_session.py`,
`tests/test_printing.py`, `tests/test_cli_passes.py`,
`tests/test_print_session.py`, `tests/test_duplex_submission.py`.

**Existing tests:** `tests/test_cli_passes.py` (the CLI composition, end to
end), `tests/test_printing.py` (`plan_passes` itself),
`tests/test_registration.py` (`back_offset_pt` through `export`),
`tests/test_gui_workflow.py` (the app's export path, in a subprocess).

## 3. Change

**Where:** `MainWindow`, directly under `Save PDF...`. Chosen over the print
dialog because the whole point of this feature is a user who is *not* printing
here — a shop, a second machine — and `_on_print_clicked` (`main.py:755-764`)
refuses to open the dialog at all when `self._printers` is empty. An action for
"I have no printer" cannot live behind "you have a printer".

**Profile:** `getattr(self, "profile", DEFAULT_PROFILE)`. After N2, `self.profile`
is the resolved profile for the preselected printer; before N2 it does not
exist and the generic preset is used — the same answer `resolve_profile` gives
today, and the same one B16 is about. The status message always names which,
so the user is never guessing which reload behaviour the file assumes.

### Steps

1. **`deckle/app/main.py` — one pure composer, above `MainWindow`.** Placed
   beside the other module-level helpers so it is testable without a widget,
   the way `autosave_recovery_offer` and `_recent_label` are:

   ```python
   def pass_export_arguments(plan, profile, side: str) -> dict:
       """The ``export`` keyword arguments for one manual-duplex pass.

       Every value comes from :func:`deckle.core.printing.plan_passes` --
       the sheet order, the half turn, the reload wording -- for the same
       reason ``deckle.cli._pass_for`` does: a second implementation of the
       back-pass ordering table would be free to disagree with the CLI
       about the same printer, and the paper would be wrong while both
       halves looked right.

       :param plan: the imposed sheets.
       :param profile: the printer profile whose reload behaviour decides
           the order and the half turn.
       :param side: ``"front"`` or ``"back"``.
       :returns: ``{"sheets": ..., "side": ..., "rotate_180": ...,
           "back_offset_pt": ...}``, ready to splat into
           :func:`deckle.core.export.export`.
       :raises StopIteration: never in practice -- ``plan_passes`` always
           returns both sides.
       """
       print_pass = next(
           p for p in plan_passes(plan, profile) if p.side == side
       )
       return {
           "sheets": print_pass.sheet_order,
           "side": side,
           "rotate_180": print_pass.side == "back" and print_pass.rotate_backs,
           "back_offset_pt": (profile.back_offset_x_pt, profile.back_offset_y_pt),
       }
   ```

   Add `from deckle.core.printing import plan_passes` to `main.py`'s imports.

   The four keys are exactly the four values `cli._cmd_export` composes, in the
   same order, with `rotate_180` guarded by `side == "back"` for the same
   reason: `rotate_backs` is a property of the *profile*, and applying it to a
   front pass would turn every front upside down.

2. **`deckle/app/main.py` — the message, also pure.**

   ```python
   def pass_saved_message(path: str, sheets: int, side: str, profile_name: str,
                          reload_instruction: str,
                          back_offset_pt: tuple[float, float]) -> str:
       """What was written, for which reload behaviour, and what to do next."""
   ```

   Returns, joined with `"  "` (two spaces, so it fits one status-bar line):

   - `f"Saved the {side} pass -- {sheets} sheet(s) -- to {path}"`
   - `f"Assumes {profile_name}."`
   - the pass's `reload_instruction` verbatim (it is
     `deckle.core.printing`'s wording; do not paraphrase it)
   - when `back_offset_pt != (0.0, 0.0)`:
     `f"Back faces moved {dx:+g}, {dy:+g}pt. Corrects a constant offset only -- not skew or scale."`
     — the same sentence `cli._report_registration` prints, minus its
     `registration:` prefix.

3. **`deckle/app/main.py` — the two buttons.** Immediately after
   `self.save_pdf_button` is added (`main.py:498`):

   ```python
           self.save_front_pass_button = QPushButton("Save front pass PDF...", controls)
           self.save_front_pass_button.setToolTip(PASS_PDF_TOOLTIP)
           controls_layout.addWidget(self.save_front_pass_button)

           self.save_back_pass_button = QPushButton("Save back pass PDF...", controls)
           self.save_back_pass_button.setToolTip(PASS_PDF_TOOLTIP)
           controls_layout.addWidget(self.save_back_pass_button)
   ```

   with, beside `SAVE_PROJECT_TOOLTIP` at `main.py:51`:

   ```python
   PASS_PDF_TOOLTIP = (
       "Write ONE manual-duplex pass to its own PDF, for printing somewhere "
       "else -- a copy shop, or a second machine.\n\n"
       "Save PDF writes both faces interleaved, which is what a duplexer "
       "wants. These two write every front, then every back, each in the "
       "order your printer's reload behaviour demands and with the half "
       "turn it needs. Print the front file, reload exactly as Deckle "
       "tells you, then print the back file.\n\n"
       "The order and the turn come from the printer profile, so a file "
       "saved for one reload behaviour must not be printed under another."
   )
   ```

   Exact labels: `"Save front pass PDF..."` and `"Save back pass PDF..."`.

4. **`deckle/app/main.py` — wire them.** Beside the other `clicked.connect`
   calls (`main.py:559-564`):

   ```python
           self.save_front_pass_button.clicked.connect(
               lambda: self._on_save_pass_clicked("front")
           )
           self.save_back_pass_button.clicked.connect(
               lambda: self._on_save_pass_clicked("back")
           )
   ```

5. **`deckle/app/main.py` — the handler.**

   ```python
       def _on_save_pass_clicked(self, side: str) -> None:
           """Write one manual-duplex pass to a PDF the user names.

           :param side: ``"front"`` or ``"back"``.
           :returns: nothing. Every failure is reported in the status bar,
               in the wording :mod:`deckle.core.outputs` owns -- the same
               words the CLI uses for the same failure.
           """
   ```

   Body, in order:

   - Defensive empty-document guard, mirroring `_on_save_pdf_clicked:1065-1069`:
     `if not self.state.project.pages: self.status_bar.showMessage(NOTHING_TO_EXPORT_MESSAGE); return`.
   - `profile = getattr(self, "profile", DEFAULT_PROFILE)` and
     `profile_name = getattr(self, "profile_name", DEFAULT_PROFILE_NAME)`, with
     `DEFAULT_PROFILE_NAME = next(iter(BUILTIN_PRESETS))` beside `DEFAULT_PROFILE`
     at `main.py:78`.
   - `QFileDialog.getSaveFileName(self.window, f"Save {side} pass PDF",
     os.path.join(start_dir, self.suggested_pass_name(side)), "PDF files (*.pdf)")`
     with `start_dir` computed as in `_on_save_pdf_clicked:1071`.
   - Empty path returns; append `".pdf"` if absent, exactly as
     `_on_save_pdf_clicked:1080-1081`.
   - `output_path_problem(path, source)` → status bar +
     `log_event("output_path_rejected", path=path, detail=problem)` → return.
   - `plan = self.preview_view.plan` — **the preview's plan, not a fresh
     recompute**, so what you save is what you previewed, the rule
     `_on_save_pdf_clicked:1093` already follows.
   - `kwargs = pass_export_arguments(plan, profile, side)`.
   - `self.status_bar.showMessage(f"Exporting the {side} pass to {os.path.basename(path)}...")`.
   - `export(plan, path, **kwargs)` inside the same two-branch `except` as
     `_on_save_pdf_clicked:1098-1109` (`OSError` →
     `describe_write_failure` + `log_exception("output_write_failed", ...)`;
     `Exception` → `f"Export failed: {exc}"` +
     `log_exception("pass_export_failed", ...)`).
   - On success: `print_pass = next(p for p in plan_passes(plan, profile) if p.side == side)`,
     then `self.status_bar.showMessage(pass_saved_message(path, len(kwargs["sheets"]), side, profile_name, print_pass.reload_instruction, kwargs["back_offset_pt"]))`
     and `log_event("pass_exported", path=path, side=side, sheets=len(kwargs["sheets"]), profile=profile_name)`.

6. **`deckle/app/main.py` — the suggested name.**

   ```python
       def suggested_pass_name(self, side: str) -> str:
           """A default filename for one pass, derived from the source.

           ``book.pdf`` becomes ``book-deckle-front.pdf`` -- never the
           source name, and never the same name as Save PDF's own output,
           so the three files a job can produce are distinguishable in a
           folder a month later.
           """
           stem = os.path.splitext(self.suggested_export_name())[0]
           return f"{stem}-{side}.pdf"
   ```

7. **`deckle/app/main.py` — gate them with the other document actions.** In
   `_sync_document_actions` (`main.py:594-602`), after
   `self.save_pdf_button.setEnabled(has_pages)`:

   ```python
           for button in (self.save_front_pass_button, self.save_back_pass_button):
               button.setEnabled(has_pages)
               button.setToolTip(PASS_PDF_TOOLTIP if has_pages else NOTHING_TO_EXPORT_MESSAGE)
   ```

   Same reasoning as the comment already on that method: an unavailable action
   should look unavailable rather than accept the click and then explain itself.

## 4. Tests

`MainWindow` cannot be constructed under pytest here (exit 127 —
`tests/test_gui_workflow.py:9`), so the pure composers get direct tests and the
wiring gets the stub / subprocess treatment the repo already uses.

### `tests/test_pass_export.py` (new)

Build plans with the helpers in `tests/test_ui_surface.py` (`_ref`, `_page`) or
`tests/test_printing.py`, and profiles from
`deckle.core.profiles.BUILTIN_PRESETS`.

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_the_front_pass_keeps_sheet_order_and_never_turns` | 4-sheet plan, `generic_face_down_reversed` (`reverse_stack=True`, `flip_axis="long"`) | `pass_export_arguments(plan, profile, "front") == {"sheets": [0,1,2,3], "side": "front", "rotate_180": False, "back_offset_pt": (0.0, 0.0)}` | `ImportError: cannot import name 'pass_export_arguments' from 'deckle.app.main'` |
| `test_a_reversing_printer_reverses_the_back_pass` | same profile | `...("back")["sheets"] == [3,2,1,0]` and `rotate_180 is True` | as above |
| `test_an_in_order_printer_keeps_the_back_pass_in_order` | `generic_face_up_in_order` (`reverse_stack=False`, `flip_axis="short"`) | `...("back")["sheets"] == [0,1,2,3]` and `rotate_180 is False` | as above |
| `test_the_back_offset_comes_from_the_profile` | `dataclasses.replace(preset, back_offset_x_pt=3.0, back_offset_y_pt=-2.0)` | both sides report `back_offset_pt == (3.0, -2.0)` — `export` is what restricts it to back faces, not this | as above |
| `test_the_gui_composes_a_pass_exactly_as_the_cli_does` | for both presets and both sides, compare `pass_export_arguments(...)` against the four values `deckle.cli._pass_for` + the surrounding block produce for the same plan and profile | the dicts agree on all four keys | as above |
| `test_the_saved_message_names_the_profile_and_the_reload` | `pass_saved_message("/x/b-front.pdf", 4, "front", "generic_face_down_reversed", "Load paper face down, ...", (0.0, 0.0))` | contains `"Saved the front pass -- 4 sheet(s)"`, `"generic_face_down_reversed"`, and the reload sentence verbatim; contains no `"Back faces moved"` | as above |
| `test_a_registration_correction_is_said_out_loud` | same with `(3.0, -2.0)` | contains `"Back faces moved +3, -2pt"` | as above |
| `test_the_pass_name_is_distinct_from_save_pdfs` | a `SimpleNamespace` stub whose `suggested_export_name` returns `"book-deckle.pdf"`, with `MainWindow.suggested_pass_name` bound to it | `"book-deckle-front.pdf"` and `"book-deckle-back.pdf"` | `AttributeError: type object 'MainWindow' has no attribute 'suggested_pass_name'` |

### `tests/test_pass_export.py`, the round trip that matters

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_two_pass_files_together_hold_every_face` | impose `tests/fixtures/sample.pdf` (2 pages, Letter) into a plan; `export(plan, front_pdf, **pass_export_arguments(plan, preset, "front"))` and the same for back; open both with `pikepdf` | each has exactly `len(plan.sheets)` pages, and their page counts sum to the both-faces export's | `ImportError` as above |
| `test_the_back_file_is_written_in_the_reload_order` | with `generic_face_down_reversed`, patch `deckle.core.export.export` to record `sheets=` | the recorded list is `list(reversed(range(len(plan.sheets))))` | `ImportError` as above |

### `tests/gui_workflow.py` + `tests/test_gui_workflow.py` (extend)

The subprocess workflow is the only place a real `MainWindow` runs. After the
existing export block (`gui_workflow.py:~200`), add:

```python
    front_pdf = out_pdf + ".front.pdf"
    window._on_save_pass_clicked  # bound, but the dialog would block
```

so instead call the composer and export directly, and record the button state:

- `report["front_pass_button_enabled"]` — `window.save_front_pass_button.isEnabled()`
- `report["back_pass_button_enabled"]`
- `report["pass_buttons_before_import"]` — captured in the empty-state block at
  `gui_workflow.py:58`, expected `False`

and in `tests/test_gui_workflow.py`:

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_pass_buttons_follow_the_document` | `report["pass_buttons_before_import"] is False` and `report["front_pass_button_enabled"] is True` | the workflow script raises `AttributeError` and the module fixture fails with a non-zero exit |

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_pass_export.py` |
| The workflow test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_gui_workflow.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The app reaches the ordering table at all | `grep -c "plan_passes" deckle/app/main.py` (today `deckle/app` has 0 non-comment hits; after: ≥2) |
| The app does not reimplement the ordering table | `! grep -n "reverse_stack\|flip_axis" deckle/app/main.py` |
| The labels are exactly as specified | `grep -q '"Save front pass PDF\.\.\."' deckle/app/main.py && grep -q '"Save back pass PDF\.\.\."' deckle/app/main.py` |
| The saved plan is the previewed plan | `sed -n '/_on_save_pass_clicked/,/^    def /p' deckle/app/main.py \| grep -q "self.preview_view.plan"` |
| `[HUMAN]` The two files really register | Save both passes for a 4-sheet folio job. Print the front file, reload exactly as the status bar said, print the back file onto the same sheets. Fold one: the backs must be upright and behind their own fronts. This is the check `deckle export --pass` has always needed and never had a GUI for. |

## 6. Out of scope

- **B16** — the app only ever resolves the *first* built-in preset, so a
  face-up printer gets the wrong order and the wrong turn. N4 makes that
  visible (the status message names the profile) and does not fix it. **F1**
  (a profile picker) is the fix, and once it lands the two buttons read the
  chosen profile with no further change.
- **B22** (`--profile` without `--pass` is silently ignored) — CLI only.
- **N3** (proof sheet with ruler). Adjacent, and both are "produce something
  that is not the whole job", but N3 goes to paper and N4 goes to a file.
- **F12's per-signature export to separate files.** These two write the whole
  document's fronts and the whole document's backs.
- **A `--rule` option on the pass export.** A ruler belongs on a proof, not on
  a pass the user is about to commit paper to; that is what N3 is for, and
  `export`'s own `--rule` help says "use on a proof, not on the job".
- **M3** (splitting `MainWindow`). These handlers are exactly the kind of thing
  M3 wants to move into `app/project_actions.py`; write them so they move
  cleanly (two module-level pure functions, one thin method) and let M3 move
  them.

## 7. decisions.md entry

```
## 2026-09-05 — The app could not write one duplex pass, only both faces interleaved
- Symptom: `Save PDF...` writes front, back, front, back -- what a real duplexer wants and what a copy shop cannot use. Someone printing a 200-sheet job somewhere other than their own desk had to split it themselves, in the reload order their printer demands, with the right half turn on every back, and would not find out they got it wrong until the whole stack was printed. `deckle export --pass front --profile ...` does exactly this and nothing in `deckle/app` reached it: `grep -rn "plan_passes" deckle/app` found two comments.
- Fix: "Save front pass PDF..." / "Save back pass PDF..." beside Save PDF, composing `export`'s four pass arguments in one pure `pass_export_arguments(plan, profile, side)` that reads them straight off `plan_passes` -- sheet order, side, half turn, back offset -- for the same reason `cli._pass_for` does. The status bar then repeats the profile's own reload instruction verbatim, and says the registration correction out loud when there is one, because a shifted back looks exactly like an unshifted one until it is printed.
- Surfaces: On the main window rather than the print dialog. The user this exists for has no printer here, and `_on_print_clicked` refuses to open the dialog when the printer list is empty -- an action for "I print elsewhere" cannot live behind "you have a printer".
- Watch: The profile is still whatever `DEFAULT_PROFILE`/`resolve_profile` returns, which is always the first built-in preset (B16). The file is only correct for a printer that behaves like it, so the message names which profile the file assumes rather than letting the user guess.
- Commit: <fill in>
```

## 8. Traps

- **`rotate_180` must be gated on `side == "back"`.** `PrintPass.rotate_backs`
  is a property of the profile; `plan_passes` already returns `False` on the
  front pass, but the CLI guards it anyway (`cli.py:995`) and so must this —
  a front pass turned 180° prints every front upside down.
- **`back_offset_pt` is passed for both sides and applied by `export` to back
  faces only** (`export._is_back_face`, `export.py:628`). Do not "optimise" it
  to zero on the front pass: that would work today and break the moment
  `_is_back_face` changes, and it would diverge from the CLI.
- **`export(sheets=...)` silently skips an unknown index and can write a
  0-page PDF that passes `_verify_output`** (B20, `export.py:589-593`). The
  indices here come from `plan_passes` over the same plan, so they cannot be
  unknown — but do not let a future "reprint a subset" option reach this path
  without the `_report_missing_sheets` check the CLI has.
- **Export runs synchronously on the GUI thread** (B17). A 60-sheet pass will
  freeze the window, and the "Exporting..." message will not paint. That is
  pre-existing and B17's to fix; do not add a thread here, or B17 will have two
  patterns to unify.
- **`self.preview_view.plan`, not `recompute_plan(...)`.** What you save must
  be what you previewed; `_on_save_pdf_clicked` already reads it that way and a
  second rule would be a second answer.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
- **Constructing a real `QMainWindow` under pytest exits 127 here.** The pure
  composers are why the interesting assertions do not need one.
