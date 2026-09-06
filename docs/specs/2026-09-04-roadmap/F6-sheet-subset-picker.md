# F6 — Let the print dialog reprint an arbitrary run of sheets

**Roadmap item:** `docs/ROADMAP.md` F6
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none.

---

## 1. Context

Sheet 41 jams, comes out creased, or gets a coffee ring on it. The CLI can
reprint exactly that sheet:

```bash
deckle export book.pdf -o one.pdf --sheets 41 --pass back --profile mine
```

The desktop app cannot. Its print dialog offers **All** or **one whole
signature**, so reprinting one ruined sheet out of a four-sheet gathering
means reprinting four — eight faces, two passes, and three good sheets
thrown away because there is no way to say "41".

The print-prep design document lists this as SC-10 (*select sheets in the
preview → filtered plan*), and everything under the UI already supports it:
`PrintSession` takes `sheets`, `plan_passes` takes `sheets`, and
`export(sheets=...)` takes it too. The roadmap records the state as
*"Dialog exposes only a signature selector; CLI has `--sheets`."*

Repro, on the current tree:

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from tests.test_print_dialog import _make_plan
# construct the dialog as tests/test_print_dialog.py:160 does and list its
# widgets: printer_combo, test_first_checkbox, signature_combo,
# status_label, print_button. There is no sheet entry of any kind.
EOF
```

```
$ grep -n "sheets" deckle/app/views/print_dialog.py
128:    :param plan: the imposed sheets to print.
142:    :param ask_resume_count: asked how many sheets physically emerged
224:        # combo only ever supplies a `sheets=` subset, never computes one.
256:        The signature combo supplies a ``sheets=`` subset and nothing more
268:        sheets = self.signature_combo.currentData()
269:        kwargs = {} if sheets is None else {"sheets": sheets}
372:            "How many sheets came out?",
```

Only lines 268-269 are executable, and their sole input is
`signature_combo.currentData()` — a whole signature or `None`.

## 2. Current code

`deckle/app/views/print_dialog.py:220-232` — the only subset control there
is, and the comment that already frames the right design:

```python
        # Signature selector: "All" (the default -- prints the whole plan)
        # or one signature by index, so a binder can reprint a single
        # gathering without touching pass/sheet-order arithmetic. That
        # arithmetic already lives in `plan_passes`/`PrintSession` -- this
        # combo only ever supplies a `sheets=` subset, never computes one.
        signature_row = QHBoxLayout()
        signature_row.addWidget(QLabel("Signature:", self.widget))
        self.signature_combo = QComboBox(self.widget)
        self.signature_combo.addItem("All", None)
        for signature in self.plan.signatures:
            self.signature_combo.addItem(f"Signature {signature.index}", signature.sheet_indices)
        signature_row.addWidget(self.signature_combo)
        layout.addLayout(signature_row)
```

`deckle/app/views/print_dialog.py:253-280` — where the subset is consumed:

```python
    def start_print(self) -> None:
        """Construct a fresh ``PrintSession`` for the selected printer and run it.

        The signature combo supplies a ``sheets=`` subset and nothing more
        -- reprinting one gathering is the normal path with a smaller
        input, and the pass/sheet-order arithmetic stays in
        ``plan_passes``/``PrintSession``.
        ...
        """
        printer_name = self.printer_combo.currentText()
        profile = self._resolve_profile(printer_name)
        backend = self._backend_cls(profile)
        sheets = self.signature_combo.currentData()
        kwargs = {} if sheets is None else {"sheets": sheets}
        session = self._session_cls(
            self.plan,
            profile,
            backend,
            test_first=self.test_first_checkbox.isChecked(),
            printer_name=printer_name,
            **kwargs,
        )
```

`deckle/cli.py:287-333` — the grammar to reuse, verbatim:

```python
def _parse_sheet_selection(value: str) -> list[int]:
    """A ``--sheets`` value as the sheet indices it names, in order.

    Accepts single numbers and inclusive ranges, comma-separated:
    ``0``, ``2,0``, ``1-3``, ``0,2-4``. Indices are **0-based**, matching
    every other sheet number Deckle prints -- the layout warnings, the
    schedule's gathering list, ``Sheet.index``. A 1-based flag would
    disagree with all three.

    Order is preserved rather than sorted, and repeats are kept: both are
    what :func:`deckle.core.export.export` documents for its ``sheets``
    argument, and neither is worth silently correcting -- ``2,0`` is a
    reasonable thing to ask for.

    Open-ended ranges (``2-``) are deliberately not accepted: the total
    sheet count is not known until the document is imposed, which is after
    argparse has run, so the flag cannot honour one at the point it is read.
    ...
    """
    selection: list[int] = []
    items = [item.strip() for item in value.split(",")]
    if not value.strip() or any(not item for item in items):
        raise argparse.ArgumentTypeError(
            f"invalid sheets {value!r}: expected sheet numbers like 0, 2,0 "
            "or 0,2-4 -- counting from 0, as the schedule and the warnings do"
        )
    for item in items:
        bounds = [part.strip() for part in item.split("-")]
        if len(bounds) > 2 or any(not part.isdigit() for part in bounds):
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: {item!r} is not a sheet number "
                "or an inclusive range like 2-4"
            )
        if len(bounds) == 1:
            selection.append(int(bounds[0]))
            continue
        start, end = int(bounds[0]), int(bounds[1])
        if end < start:
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: the range {item!r} runs backwards"
            )
        selection.extend(range(start, end + 1))
    return selection
```

`deckle/cli.py:336-361` — the out-of-range check, whose message the dialog
should echo:

```python
def _report_missing_sheets(plan, selection: list[int], total: int) -> bool:
    """Refuse a selection naming a sheet the document does not have.

    :func:`deckle.core.export.export` skips an unknown index rather than
    raising, which is right for a library and wrong for a command: asking
    for sheet 99 of a four-sheet book would write a PDF with nothing in it
    and print ``wrote proof.pdf``. A file that exists and is empty, from a
    command that reported success, is the worst available outcome.
    """
    have = {sheet.index for sheet in plan.sheets}
    missing = sorted({index for index in selection if index not in have})
    if not missing:
        return False
    named = ", ".join(str(index) for index in missing)
    print(
        f"error: no sheet {named} in this document -- it has {total} "
        f"sheet(s), numbered 0 to {total - 1}",
        file=sys.stderr,
    )
    return True
```

`deckle/core/print_session.py:306-329` — the constructor. Note it does
**not** validate `sheets` against the plan; unknown indices go straight to
`plan_passes` and then to the backend:

```python
    def __init__(
        self,
        plan: SheetPlan,
        profile: PrinterProfile,
        backend: PrintBackend,
        sheets: Sequence[int] | None = None,
        ...
    ) -> None:
        ...
        self._passes: list[PrintPass] = plan_passes(plan, profile, sheets=sheets)
        indices = (
            [s.index for s in plan.sheets] if sheets is None else list(sheets)
        )
```

`deckle/core/printing.py:134-148` — where a subset lands:

```python
def plan_passes(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheets: Sequence[int] | None = None,
) -> list[PrintPass]:
    """Compute the front and back passes for ``sheets`` (default: all).

    Passing a narrower ``sheets`` sequence (e.g. a single reprinted sheet)
    is the normal path with a smaller input -- there is no separate
    reprint branch.
    """
    indices = [s.index for s in plan.sheets] if sheets is None else list(sheets)
```

### Call sites

```
$ grep -rn "_parse_sheet_selection" deckle/ tests/
deckle/cli.py:287:def _parse_sheet_selection(value: str) -> list[int]:
deckle/cli.py:1255:        type=_parse_sheet_selection,
tests/test_cli_sheets.py:18:from deckle.cli import _parse_sheet_selection, main
tests/test_cli_sheets.py:27,31,35,39,43,47,58  (seven direct calls)

$ grep -rn "^ *from deckle\.cli\|^ *import deckle\.cli" deckle/app/
(no output — exit 1)
$ grep -rn "deckle.cli" deckle/app/
deckle/app/views/layout_panel.py:201:#: ``deckle/cli.py``'s ``--paper`` presets so both front ends offer the
```

`deckle.app` has never *imported* `deckle.cli` — the one textual hit is a
comment about the paper presets (M5's duplication) — and must not start: the
grammar has to move down into `deckle.core`, not sideways.

### Existing tests

- `tests/test_cli_sheets.py:26-58` — seven parser tests, one of which asserts
  `argparse.ArgumentTypeError` specifically for eight malformed inputs
  (`"", "a", "0,", "-1", "1-", "3-1", "0..2", "1,,2"`). **The exception type
  is pinned**, so the CLI wrapper must keep raising it.
- `tests/test_print_dialog.py` — `_make_dialog` at line 160 is the harness.
- `tests/test_ui_surface.py:341-365` — builds a real `PrintDialog` and
  asserts that selecting a signature enumerates no printers.
- `tests/test_printing.py` — `plan_passes(sheets=...)`.

## 3. Change

A `Sheets:` text entry on the print dialog using the CLI's grammar, with the
existing signature combo demoted to a **shortcut that fills it in**.

**Chosen: a range entry.** **Rejected: a multi-select list.** Four reasons,
in the order they matter:

1. **The grammar already exists, is documented, and is 0-based to match
   `Sheet.index`, the layout warnings and the schedule.** A user who types
   `41` in the app and `--sheets 41` in a script gets the same sheet. A
   second selection model would be a second thing to keep 0-based.
2. **Order and repeats are expressible.** `2,0` is a real request — print
   sheet 2 first — and `export`'s own docstring commits to preserving it. A
   list widget cannot say it.
3. **Scale.** The working case in this project's own docs is a 266-page book
   at 67 sheets. "Reprint 41-46" is seven keystrokes; in a list it is a
   scroll, six ctrl-clicks, and one slip away from printing the wrong six.
4. **It composes with what is there.** The combo can write into the entry;
   a list widget and a combo would be two selection models with a
   precedence rule between them — the two-controls-for-one-decision shape
   `layout_panel.py:754-759` records as the reason the mode tabs replaced a
   dropdown.

### 1. `deckle/core/printing.py` — the grammar moves down

Add two pure functions. `printing.py` is the right home: it is the module
that already answers "which sheets, in what order", it imports nothing but
`models` and `profiles`, and it is Qt-free by test.

```python
def parse_sheet_selection(value: str) -> list[int]:
    """The sheet indices a selection string names, in the order named.

    Accepts single numbers and inclusive ranges, comma-separated: ``0``,
    ``2,0``, ``1-3``, ``0,2-4``. Indices are **0-based**, matching every
    other sheet number Deckle prints -- the layout warnings, the schedule's
    gathering list, ``Sheet.index``.

    Order is preserved rather than sorted, and repeats are kept: both are
    what :func:`deckle.core.export.export` documents for its ``sheets``
    argument, and ``2,0`` is a reasonable thing to ask for.

    Open-ended ranges (``2-``) are not accepted: the total sheet count is
    not known where this is read.

    :raises ValueError: empty, malformed, negative, or a range that runs
        backwards. The message names the value and the accepted forms.
    """
```

Body: `deckle/cli.py:310-333` moved verbatim, with the three
`argparse.ArgumentTypeError(...)` raises changed to `ValueError(...)` and the
**message strings kept character-identical**.

```python
def sheet_selection_text(indices: Sequence[int]) -> str:
    """The shortest selection string naming exactly ``indices``, in order.

    The inverse of :func:`parse_sheet_selection` for any ascending,
    gapless-in-runs input: ``(4, 5, 6, 7)`` becomes ``"4-7"`` and
    ``(0, 3, 4)`` becomes ``"0,3-4"``. An empty sequence gives ``""``,
    which the print dialog reads as "all sheets".

    Written so the signature shortcut and the typed entry are one value and
    not two: the combo writes text the user could have typed, and there is
    no second place a subset can live.
    """
```

Body: walk `indices`, collect maximal ascending runs of consecutive values,
emit `f"{a}"` for a run of one, `f"{a}-{b}"` otherwise, join with `","`. Do
not sort and do not deduplicate — the round-trip property is what the test
asserts.

### 2. `deckle/cli.py` — a thin wrapper

`_parse_sheet_selection` keeps its name, its position and its docstring
(shortened to a pointer), and becomes:

```python
def _parse_sheet_selection(value: str) -> list[int]:
    """A ``--sheets`` value as the sheet indices it names, in order.

    The grammar lives in :func:`deckle.core.printing.parse_sheet_selection`
    so the desktop app's sheet entry accepts exactly what this flag does --
    a user who types ``41`` in the dialog and ``--sheets 41`` in a script
    must get the same sheet. This wrapper only restates the refusal as the
    exception argparse renders as a usage error.
    """
    try:
        return parse_sheet_selection(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
```

Add `parse_sheet_selection` to the `from deckle.core.printing import ...`
line at the top of `cli.py`.

### 3. `deckle/app/views/print_dialog.py` — the entry

`_qt_widgets()` gains `QLineEdit` (add to both the import list and the
returned tuple, and to the unpacking at lines 189-198).

After the signature row, before `status_label`:

```python
        # A typed range rather than a list: the grammar is the CLI's, so a
        # sheet number typed here and one passed to `--sheets` mean the same
        # thing, and `2,0` -- print sheet 2 first -- is sayable at all. The
        # signature combo above writes into this box rather than competing
        # with it, so a subset lives in exactly one place.
        sheets_row = QHBoxLayout()
        sheets_row.addWidget(QLabel("Sheets:", self.widget))
        self.sheets_edit = QLineEdit(self.widget)
        self.sheets_edit.setPlaceholderText("all")
        self.sheets_edit.setToolTip(
            "Which sheets to print, counting from 0 -- 41, or 4-7, or "
            "0,2-4. Leave empty for all of them.\n\n"
            "Sheet numbers are the ones the binding schedule and the layout "
            "warnings use."
        )
        self.sheets_edit.textChanged.connect(self._on_sheets_changed)
        sheets_row.addWidget(self.sheets_edit)
        layout.addLayout(sheets_row)
```

### 4. The signature combo becomes a shortcut

Change the combo's items to carry the **text**, not the tuple:

```python
        self.signature_combo.addItem("All", "")
        for signature in self.plan.signatures:
            self.signature_combo.addItem(
                f"Signature {signature.index}",
                sheet_selection_text(signature.sheet_indices),
            )
```

and connect:

```python
        self.signature_combo.currentIndexChanged.connect(
            lambda _index: self.sheets_edit.setText(self.signature_combo.currentData())
        )
```

Set the combo's tooltip to `"A shortcut that fills in the Sheets box below. Edit that box to narrow it further."`

### 5. Validation

```python
    def _on_sheets_changed(self, text: str) -> None:
        """Validate the typed selection and gate the Print button on it.

        Refusing before the run rather than after is the same reason
        ``deckle.cli._report_missing_sheets`` exists: a selection naming a
        sheet the document does not have would otherwise submit an empty
        pass and report success.
        """
```

Body:

1. `if not text.strip():` → `self._sheets = None`, `status_label.setText("")`,
   `print_button.setEnabled(True)`, return. Empty means all.
2. `try: selection = parse_sheet_selection(text)` /
   `except ValueError as exc:` → `self._sheets = None`,
   `status_label.setText(str(exc))`, `print_button.setEnabled(False)`,
   return.
3. `have = {sheet.index for sheet in self.plan.sheets}`;
   `missing = sorted({i for i in selection if i not in have})`. If any:
   `total = len(self.plan.sheets)`;
   `status_label.setText(f"No sheet {', '.join(str(i) for i in missing)} in this document -- it has {total} sheet(s), numbered 0 to {total - 1}.")`,
   `print_button.setEnabled(False)`, return.
   The wording deliberately mirrors `_report_missing_sheets`'s message, minus
   the `error: ` prefix a GUI does not need.
4. Otherwise `self._sheets = selection`,
   `status_label.setText(f"{len(selection)} of {len(self.plan.sheets)} sheet(s) selected.")`,
   `print_button.setEnabled(True)`.

Initialise `self._sheets: list[int] | None = None` in `__init__` **before**
the widgets are built, so a `textChanged` fired during construction finds it.

### 6. `start_print` reads the entry, not the combo

```python
        sheets = self._sheets
        kwargs = {} if sheets is None else {"sheets": sheets}
```

Nothing else changes. `PrintSession` and `plan_passes` already do the rest,
and this dialog still computes no ordering — the comment at lines 220-224
stays true and should be updated only to name the entry instead of the combo.

### 7. Resume

`_offer_resume` must **not** pass `self._sheets`: a resumed session restores
its own sheet list from the state file (`print_session.py:203`,
`sheets=list(data["sheets"])`). Leave `_offer_resume` alone, and add a
one-line comment saying why, because "the dialog has a sheets box now" is
exactly the reasoning that would put it there.

### 8. Docs

- GUIDE §6, after "Test one sheet first", a new short subsection
  **"Printing part of a job"**: the box takes the same numbers `--sheets`
  does, counting from 0; the signature dropdown fills it in; empty means all.
- `deckle/app/views/print_dialog.py`'s module docstring lists the
  `PrintSession` API the dialog drives — no change needed, but check it still
  reads true.

## 4. Tests

Additions to `tests/test_printing.py` (the pure half):

1. `test_the_selection_grammar_moved_without_changing_a_message`
   For each of `"", "a", "0,", "-1", "1-", "3-1", "0..2", "1,,2"`, both
   `deckle.core.printing.parse_sheet_selection` (raising `ValueError`) and
   `deckle.cli._parse_sheet_selection` (raising
   `argparse.ArgumentTypeError`) refuse, **with the same message text**.
   Unfixed: `ImportError: cannot import name 'parse_sheet_selection' from
   'deckle.core.printing'`.

2. `test_sheet_selection_text_round_trips`
   Parametrised: `(0,)→"0"`, `(4,5,6,7)→"4-7"`, `(0,3,4)→"0,3-4"`,
   `(2,0)→"2,0"`, `()→""`. And for each non-empty case,
   `parse_sheet_selection(sheet_selection_text(t)) == list(t)`.

3. `test_sheet_selection_text_keeps_an_unsorted_order`
   `sheet_selection_text((5, 1, 2))` parses back to `[5, 1, 2]`. Guards
   against an implementation that sorts to make the runs prettier and
   silently reorders a print run.

`tests/test_cli_sheets.py` — unchanged, and that is the point: it is the
regression test for the move. Add one line to its module docstring noting the
grammar now lives in `core.printing`.

New tests in `tests/test_print_dialog.py`:

4. `test_an_empty_sheets_box_prints_the_whole_plan`
   `dialog.start_print()` with the box empty; the stub session was
   constructed with **no** `sheets` kwarg. Unfixed:
   `AttributeError: 'PrintDialog' object has no attribute 'sheets_edit'`.

5. `test_a_typed_range_reaches_the_session_as_sheets`
   `dialog.sheets_edit.setText("0-1")`, `start_print()`; the stub session got
   `sheets=[0, 1]`.

6. `test_a_typed_order_is_preserved`
   `"1,0"` → `sheets=[1, 0]`. The property a list widget cannot express, and
   the reason for the design choice.

7. `test_a_malformed_selection_disables_print_and_says_why`
   `"3-1"`; `print_button.isEnabled() is False` and `status_label.text()`
   contains `"runs backwards"`.

8. `test_a_sheet_the_document_does_not_have_disables_print`
   On a 2-sheet plan, `"9"`; Print disabled and the status text contains
   `"it has 2 sheet(s), numbered 0 to 1"`. Mirrors
   `_report_missing_sheets`'s guarantee: **B20** is the bug where an unknown
   index writes an empty PDF and reports success; the dialog must not repeat
   it.

9. `test_clearing_the_box_re_enables_print`
   Set `"9"`, then `""`; Print is enabled again and the status is empty.

10. `test_choosing_a_signature_fills_in_the_sheets_box`
    A plan with signatures; select `"Signature 0"`; `sheets_edit.text()`
    equals `sheet_selection_text(signature.sheet_indices)`, and
    `start_print()` passes exactly those indices. Then select `"All"`; the
    box is empty and no `sheets` kwarg is passed.

11. `test_narrowing_a_signature_by_hand_wins`
    Select a signature, then type `"5"` into the box; `start_print()` passes
    `[5]`. There is one subset, and it is whatever the box says.

12. `test_resume_does_not_take_the_sheets_box`
    Drive `_offer_resume` with a typed selection in the box; the stub's
    `load` classmethod was called and `resume(count)` ran, and no `sheets`
    argument was involved. A resumed session's sheet list comes from its
    state file.

Addition to `tests/test_ui_surface.py`:

13. `test_the_sheets_box_does_not_enumerate_printers`
    Same shape as the existing signature test at line 341: setting
    `sheets_edit` text must not call `_available_printer_names`. Printer
    enumeration on the UI thread is the 81-minute hang in
    `docs/decisions.md`; every new control gets this guard.

## 5. Acceptance

| Check | Command |
|---|---|
| Grammar move is message-identical | `.venv/bin/python -m pytest -q -k selection_grammar_moved_without_changing_a_message` |
| CLI parser tests untouched and green | `.venv/bin/python -m pytest -q tests/test_cli_sheets.py` |
| Round trip holds | `.venv/bin/python -m pytest -q -k sheet_selection_text_round_trips` |
| Dialog tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_print_dialog.py` |
| The app still never imports the CLI | `! grep -rnE "^ *(from deckle\.cli\|import deckle\.cli)" deckle/app/` |
| The grammar is not duplicated | `test "$(grep -rl 'is not a sheet number' deckle/ \| wc -l)" = "1"` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| [HUMAN] The box reads as optional | Launch the app, import a document, press Print. The Sheets box shows the placeholder `all` and the Print button is enabled with it empty. |

Greps run against the current tree:

```
$ grep -rnE "^ *(from deckle\.cli|import deckle\.cli)" deckle/app/
(no output — exit 1)
$ grep -rl "is not a sheet number" deckle/
deckle/cli.py
$ grep -c "sheets" deckle/app/views/print_dialog.py
7
```

After F6 the last count is higher, and the second must still name exactly one
file — `deckle/core/printing.py` instead of `deckle/cli.py`.

## 6. Out of scope

- **B20** — `export(sheets=...)` silently skipping unknown indices, and
  `render_sheet` caching an empty PDF for a stale index. F6 validates at the
  dialog so the GUI cannot reach it; the library bug stays B20's.
- **B14** — the cancelled resume-count prompt returning 0 and reprinting the
  whole pass. Adjacent in the same dialog, separate bug.
- **B17** — print submission running synchronously on the GUI thread. A
  narrower selection makes the freeze shorter, not absent.
- **Selecting sheets by clicking them in the preview** (design doc SC-10's
  literal wording). That needs a selection model in `PreviewView` and a
  signal into the dialog; the typed range delivers the capability now, and
  the preview affordance can write into the same box later.
- **N4** — exporting a single pass PDF from the GUI. Different button.
- **M5/M4** — the wider CLI/app duplication. F6 moves one parser because it
  has to; it does not start the split.

## 7. decisions.md entry

```
## 2026-09-05 — The app could reprint a whole signature or nothing
- Symptom: one creased sheet out of a four-sheet gathering meant reprinting all four, because the print dialog offered "All" or one signature and nothing narrower. `PrintSession`, `plan_passes` and `export` have all taken a `sheets=` subset since SS-11; only the UI could not say one.
- Fix: a "Sheets:" entry on the print dialog taking the CLI's own grammar -- 0-based, inclusive ranges, order preserved -- with the signature dropdown demoted to a shortcut that writes into it. `_parse_sheet_selection`'s body moved to `deckle.core.printing.parse_sheet_selection` raising `ValueError`, with the CLI keeping a wrapper that restates it as `argparse.ArgumentTypeError`; every message string is character-identical, and `tests/test_cli_sheets.py` is the regression test for the move.
- Surfaces: a range entry over a multi-select list, because `2,0` -- print sheet 2 first -- is expressible in one and not the other, and because a number typed in the dialog and a number passed to `--sheets` must mean the same sheet. `sheet_selection_text` is the inverse, so the shortcut writes text the user could have typed and a subset lives in exactly one place instead of two competing controls.
- Watch: the dialog validates against the plan's own indices and disables Print, because `export` skips an unknown index rather than raising (B20) -- which would have submitted an empty pass and reported success. Resume deliberately ignores the box: a resumed session's sheet list comes from its state file.
- Commit: <fill in>
```

## 8. Traps

- **`deckle.app` must never import `deckle.cli`.** It does not today. Move
  the grammar down into `deckle.core.printing`; do not import the private
  `_parse_sheet_selection`.
- **`argparse.ArgumentTypeError` is not a `ValueError`.** It derives from
  `Exception`. `tests/test_cli_sheets.py:56-58` catches it by type, so the
  wrapper must re-raise as that exact class or eight tests fail.
- **Keep the refusal messages byte-identical.** They are user-facing on the
  CLI and now on the GUI too; two wordings for one refusal is how the
  schedule and the layout ended up with three creep formulas (B8).
- **`textChanged` fires during construction** if you set text before the
  handler's state exists. Initialise `self._sheets` first.
- **Do not sort or deduplicate in `sheet_selection_text`.** `export`'s
  docstring commits to preserving order and repeats; a prettier string that
  reorders a print run is a silent correctness change.
- **`PrintSession` does not validate `sheets`.** Unknown indices reach
  `plan_passes` and then the backend. The dialog is the only guard on this
  path.
- **`_offer_resume` must not pass the box.** The state file carries the
  session's own sheet list.
- **`QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit
  127).** Construct and drive; never show.
- **Printer enumeration must not be triggered by a new control.**
  `tests/test_ui_surface.py:357-365` exists because a UI-thread enumeration
  once hung the app for 81 minutes.
