---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-11
sub_spec_number: 11
title: "UI surface — Binding group, per-cell preview, signature print selector"
depends_on: ['SS-03', 'SS-08']
dispatch: auto
date: 2026-08-04
---

# SS-11 — UI surface: Binding group, per-cell preview, signature print selector

## Context

Three app-layer affordances make signatures usable: a **Binding group** on `LayoutPanel`
with a live *"17 signatures · 67 sheets · 2 blanks"* readout, a **per-cell preview** that
labels each leaf with its source page number and signature index, and a **signature
selector** on `PrintDialog`.

**There is no new print logic here.** `export(plan, path, sheets=[...])`,
`plan_passes(plan, profile, sheets=[...])` and `PrintSession(..., sheets=[...])` all already
accept a sheet subset — confirmed at `deckle/core/print_session.py:156` (`sheets:
Sequence[int] | None = None`) and `deckle/core/printing.py:121`. Per-signature printing is
the existing subset path with a smaller input, not a branch.

**Follow `layout_panel.py`'s established construction pattern exactly.** There are no
`QGroupBox`es today; everything is a row on the single `QFormLayout` created at
`layout_panel.py:189`. Pure `set_*` mutators live in the Qt-free half
(`layout_panel.py:32-107`), each returning `replace(project, layout=replace(project.layout,
<field>=<value>))`. Widgets are wired in the single connect block at
`layout_panel.py:260-270`, and every handler routes through
`apply_layout_change(self.state, mutator)` then `self.layout_changed.emit(plan)`. Any new
unit-aware spinbox must also be registered in `_on_unit_changed`'s `boxes` list
(`layout_panel.py:329-341`), or switching units will silently rescale the stored value.

**Qt stays lazily imported.** Every module touched here keeps its property that importing it
loads no Qt: `layout_panel.py`, `preview_view.py` and `print_dialog.py` all import PySide6
only inside function bodies (`_qt_core`, `_qt_widgets`, `_qt_gui`). All new logic in this
sub-spec goes in the Qt-free half so it is unit-testable headlessly, and a `[MECHANICAL]`
check pins that no module-scope Qt import appears.

### Two hard-won constraints from `docs/decisions.md`

**1. Background work must be cancellable and must never repaint over a newer result.**
`docs/decisions.md` — *Superseded background renders raced and leaked*: `PreviewView.refresh()`
spawned a `QThread` per user action and overwrote `self._thread` without stopping the previous
one, so **the last thread to finish won** rather than the sheet being viewed, threads
accumulated one per sheet scrubbed past, and the `cancel` token `render_sheet` already accepted
was never created. The fix is live at `preview_view.py:499-500` (`self._worker.cancel.set()`),
`preview_view.py:516-520` (`if worker is not self._worker or worker.cancel.is_set(): return`)
and `preview_view.py:511` (`thread.finished.connect(thread.deleteLater)`), and its contract is
pinned by `tests/test_view_workers.py`. **Under folio the preview does strictly more work per
sheet** — two placements and marks instead of one placement — so a regression here bites
harder than it did in the MVP. Adding per-cell guides must not move guide computation onto the
worker thread's output path in a way that bypasses the supersede check, and must not add a
second render path.

That decision log also records the *reason* the bug survived: **`ThumbnailWorker` had zero
test coverage**, so a missing `import threading` passed a fully green suite. Any new helper
added here gets a direct test.

**2. Nothing may enumerate printers on the UI thread.** `docs/decisions.md` — *Printer
enumeration blocked the UI thread on launch*: `QPrinterInfo.availablePrinters()` enumerates
**network** printers and the Windows spooler blocks per printer until it times out when one is
unreachable, so Deckle hung on launch whenever a networked printer was offline (measured 21ms
healthy, effectively unbounded otherwise; the suite took **81 minutes** instead of 7 seconds).
Enumeration now lives in `main.py`'s background `_PrinterQueryWorker`, `MainWindow` passes the
cached list into `PrintDialog(plan, ..., printer_names=printers)` (`main.py:193`), and
`tests/test_view_workers.py:215` asserts importing the module queries nothing.

**The signature selector must be built from `plan.signatures` alone.** It is plan arithmetic,
not hardware. It must not call `QPrinterInfo`, must not re-enumerate on selection change, and
must not add a second `availablePrinters()` call site. The one existing call site inside
`print_dialog._available_printer_names` (`print_dialog.py:93-96`, reached only when the caller
passes no `printer_names`) stays exactly one.

### Consumers must tolerate `signatures == ()`

`GutterShiftStrategy` plans carry an **empty** `signatures` tuple (contract 4). Every helper
here must treat that as the normal `fold_scheme="none"` case, never as an error and never as a
reason to branch on truthiness in a way that raises.

## Files

**Files (modify):**
- `deckle/app/views/layout_panel.py`
- `deckle/app/views/preview_view.py`
- `deckle/app/views/print_dialog.py`
- `tests/test_print_dialog.py`
- `tests/test_preview_fidelity.py`

**Files (new):**
- `tests/test_layout_panel_binding.py`

Every `(modify)` path was confirmed present on the current tree.

## Provides

| Symbol | Module | Consumed by |
|---|---|---|
| `FOLD_SCHEMES`, `BLANK_MODES` | `deckle/app/views/layout_panel.py` | the panel's combo boxes |
| `set_fold_scheme(project, fold_scheme) -> Project` | `deckle/app/views/layout_panel.py` | `apply_layout_change`, SS-12 |
| `set_sheets_per_signature(project, count) -> Project` | `deckle/app/views/layout_panel.py` | `apply_layout_change` |
| `set_blank_mode(project, blank_mode) -> Project` | `deckle/app/views/layout_panel.py` | `apply_layout_change` |
| `set_sewing_stations(project, count) -> Project` | `deckle/app/views/layout_panel.py` | `apply_layout_change` |
| `set_paper_thickness_pt(project, points) -> Project` | `deckle/app/views/layout_panel.py` | `apply_layout_change` |
| `strategy_for(layout) -> LayoutStrategy` | `deckle/app/views/layout_panel.py` | `recompute_plan`, SS-12 |
| `binding_readout(plan) -> str` | `deckle/app/views/layout_panel.py` | the panel's readout label, tests |
| `cell_guides_for_side(sheet, side, settings, paper_pt) -> list[Cell]` | `deckle/app/views/preview_view.py` | `PreviewView._frame_pixmap` |
| `cell_labels_for_side(plan, sheet, side) -> list[str]` | `deckle/app/views/preview_view.py` | `PreviewView._frame_pixmap` |
| `signature_choices(plan) -> list[tuple[str, tuple[int, ...] | None]]` | `deckle/app/views/print_dialog.py` | the dialog's combo, tests |
| `sheets_for_choice(plan, index) -> tuple[int, ...] | None` | `deckle/app/views/print_dialog.py` | `PrintDialog.start_print` |

## Requires

| Symbol | From | Note |
|---|---|---|
| `LayoutSettings.fold_scheme / sheets_per_signature / blank_mode / sewing_stations / paper_thickness_pt` | SS-01 | exactly the five committed fields |
| `Side`, `Signature`, `SheetPlan.signatures` | SS-01 | `signatures` is `()` under `GutterShiftStrategy` |
| `SaddleStitchStrategy` | SS-08 | the folio branch of `strategy_for` |
| `Cell`, `content_box_rect_pt(settings, *, cell=None, is_recto=...)` | SS-07 | per-cell guide geometry |
| per-page `clipping_warnings_for_sheet` | SS-03 | already iterates `side.pages` |
| `plan_passes(plan, profile, sheets=...)`, `PrintSession(..., sheets=...)` | MVP, unchanged | the subset path |

## Implementation Steps

Each step is 2–10 minutes: write a failing test, run it and see it fail for the stated reason,
write the minimal implementation, run it green, commit.

### Step 1. Failing test — the five Qt-free mutators exist and are pure

Create `tests/test_layout_panel_binding.py`. Add
`test_binding_mutators_return_new_projects`: build a `Project` with a default
`LayoutSettings`, then for each of `set_fold_scheme("folio")`,
`set_sheets_per_signature(8)`, `set_blank_mode("balanced")`, `set_sewing_stations(5)`,
`set_paper_thickness_pt(0.27)` assert the returned `Project` carries the new value **and**
the original `Project` is unchanged (purity), matching the `replace`-based style of
`set_gutter_pt`.

```bash
python -m pytest tests/test_layout_panel_binding.py -q -k binding_mutators_return_new_projects
```

Expect `ImportError`/`AttributeError` — the mutators do not exist.

### Step 2. Implement the five mutators

Add them in the Qt-free half of `layout_panel.py`, alongside `set_binding_edge`
(`layout_panel.py:96`), each a one-line `replace(project, layout=replace(project.layout,
<field>=<value>))`. Add `FOLD_SCHEMES: tuple[str, ...] = ("none", "folio")` and
`BLANK_MODES: tuple[str, ...] = ("end", "balanced")` beside `BINDING_EDGES`
(`layout_panel.py:25`).

Document at `set_fold_scheme` that **`binding_edge` changes meaning under
`fold_scheme="folio"`**: it selects reading direction (LTR/RTL), not gutter side.

Run green, then commit.

### Step 3. Failing test — `strategy_for` dispatches on `fold_scheme`

Add `test_strategy_for_dispatches_on_fold_scheme`: assert
`isinstance(strategy_for(LayoutSettings(..., fold_scheme="folio")), SaddleStitchStrategy)`
and `isinstance(strategy_for(LayoutSettings(...)), GutterShiftStrategy)` for the `"none"`
default.

Add `test_recompute_plan_under_folio_yields_signatures`: `recompute_plan` on a project whose
layout is folio returns a plan with non-empty `signatures`; under `"none"` it returns a plan
whose `signatures == ()`.

### Step 4. Implement `strategy_for` and route `recompute_plan` through it

`recompute_plan` (`layout_panel.py:110-112`) currently hardcodes `GutterShiftStrategy()`.
Replace its body with `return strategy_for(project.layout).impose(project.pages,
project.layout)`. `strategy_for` is a two-line dispatch — no other caller changes, because
`apply_layout_change` already funnels through `recompute_plan`.

Run green, commit.

### Step 5. Failing test — the readout string, headless

Add `test_binding_readout_names_signatures_sheets_and_blanks`: build (or synthesise) a
266-page folio plan and assert `binding_readout(plan)` contains `"17"`, `"67"` and `"2"`.
Add `test_binding_readout_on_a_plan_with_no_signatures`: a `GutterShiftStrategy` plan
(`signatures == ()`) returns a non-empty string and **does not raise** — the empty-tuple
tolerance rule.

**No Qt widget is constructed in either test.** Assert that by checking
`"PySide6" not in sys.modules` is *not* required here (other tests in the session may have
loaded it); instead the test simply imports the pure function and calls it.

### Step 6. Implement `binding_readout`

```
binding_readout(plan) -> str
```
Derived from the recomputed plan, never from settings: `len(plan.signatures)` signatures,
`len(plan.sheets)` sheets, and the blank count as the number of filler `OutputPage`s across
every side. Format `"17 signatures · 67 sheets · 2 blanks"`, with correct singulars. Return
a plain sheet-count string when `plan.signatures` is empty.

Run green, commit.

### Step 7. Failing test — per-cell preview guides and labels

In `tests/test_preview_fidelity.py` add:

- `test_folio_side_produces_one_content_box_guide_per_cell` — `cell_guides_for_side` on a
  folio `Side` holding two pages returns **two** rects, whose x-ranges are disjoint and lie
  inside `(0, 396)` and `(396, 792)` respectively for letter landscape.
- `test_non_folio_side_produces_exactly_one_guide` — under `fold_scheme="none"` it returns
  exactly one rect, byte-identical to `content_box_rect_pt(settings, is_recto=...)`. MVP
  behaviour unchanged.
- `test_cell_labels_carry_source_page_number_and_signature_index` — `cell_labels_for_side`
  returns one label per page in the side, each containing that page's source page number and
  the sheet's signature index.

### Step 8. Implement `cell_guides_for_side` and `cell_labels_for_side`

Both pure, both in `preview_view.py`'s Qt-free half beside `imageable_rect_pt`. Guides come
from SS-07's `content_box_rect_pt` called **once per cell** — do not re-derive margin math
here; that is the "one implementation of every geometry rule" constraint. Labels look up the
sheet's signature by scanning `plan.signatures` for the group containing `sheet.index`,
tolerating an empty tuple by omitting the signature part.

### Step 9. Wire the guides into `_frame_pixmap` without touching the worker contract

`preview_view.py:629-639` currently draws one content-box rect per side. Replace the single
`painter.drawRect(...)` with a loop over `cell_guides_for_side(...)`, and draw each label
next to its cell. **Do not touch** `refresh()` (`preview_view.py:492-514`) or
`_on_frame_ready` (`preview_view.py:516-529`): the cancel/supersede guard, the
`deleteLater` wiring and the `cancel` token threaded into `build_preview_frame` stay exactly
as they are.

```bash
python -m pytest tests/test_view_workers.py tests/test_preview_fidelity.py -q
```

Both must stay green. Commit.

### Step 10. Failing test — signature choices are pure plan arithmetic

In `tests/test_print_dialog.py` add:

- `test_signature_choices_lists_all_then_each_signature` — `signature_choices(plan)` on a
  17-signature plan returns 18 entries, the first being an "All" entry whose sheet tuple is
  `None`, and entry *k+1* carrying `plan.signatures[k].sheet_indices`.
- `test_signature_choices_on_a_plan_with_no_signatures` — a `GutterShiftStrategy` plan
  yields exactly the single "All" entry and does not raise.
- `test_sheets_for_choice_returns_none_for_all` — index 0 gives `None`.

### Step 11. Implement `signature_choices` / `sheets_for_choice`

Qt-free, in `print_dialog.py` beside `select_preselected_printer`. Labels are presentational
strings only. **No arithmetic beyond reading `sheet_indices`** — no `reverse`, no
`sheet_order`, no `% 2`. The existing negative `[MECHANICAL]` check on that file still holds
and this step is the one most likely to break it.

### Step 12. Failing test — the dialog constructs `PrintSession` with the selected subset

Add `test_selecting_a_signature_passes_its_sheet_indices_to_the_session` to
`tests/test_print_dialog.py`, driving `PrintDialog` with the existing stub-session pattern:
select signature *k*, call `start_print()`, assert the stub received
`sheets == plan.signatures[k].sheet_indices`. Add
`test_selecting_all_passes_sheets_none`.

Add `test_signature_selection_enumerates_no_printers`: patch
`print_dialog._available_printer_names` to a function that records calls, construct the
dialog with an explicit `printer_names=[...]`, change the signature selection, and assert
the recorder was never called. **This is the UI-thread-enumeration guard from
`docs/decisions.md` applied to the new control.**

### Step 13. Implement the signature combo

Add `self.signature_combo` to the dialog's layout, populated from `signature_choices(self.plan)`
at construction. `start_print` (`print_dialog.py:193-207`) passes
`sheets=sheets_for_choice(self.plan, self.signature_combo.currentIndex())` into the
`self._session_cls(...)` call at `print_dialog.py:198-204`. Nothing else in `start_print`
or `_drive` changes.

Run green, commit.

### Step 14. Failing test — `plan_passes` covers exactly one signature

Add `test_plan_passes_over_one_signature_covers_only_that_signature` (in
`tests/test_print_dialog.py` or `tests/test_printing.py`): `plan_passes(plan, profile,
sheets=plan.signatures[3].sheet_indices)` returns two passes whose `sheet_order`s together
cover exactly that signature's sheets and no others. This exercises the **existing** subset
path and is the proof that no new branch was needed.

### Step 15. Add the Binding group widgets

On the existing `QFormLayout` (`layout_panel.py:189`), after the landscape-policy row:

- `fold_scheme_combo` (`FOLD_SCHEMES`)
- `sheets_per_signature_spinbox` (`QSpinBox`, range 1–32)
- `blank_mode_combo` (`BLANK_MODES`)
- `sewing_stations_spinbox` (`QSpinBox`, range 0–9; 0 disables stations)
- `paper_thickness_spinbox` (`QDoubleSpinBox`, **unit-aware**)
- `binding_readout_label` (`QLabel`)

Connect all five in the block at `layout_panel.py:260-270`; each handler calls
`apply_layout_change(self.state, ...)`, sets `self.binding_readout_label.setText(binding_readout(plan))`,
then emits `layout_changed`.

**Register `paper_thickness_spinbox` in `_on_unit_changed`'s `boxes` list**
(`layout_panel.py:329-341`) with its own cap. Omitting it is the specific failure this step
exists to prevent: the stored point value would be reinterpreted on every unit switch.

### Step 16. Full-suite and lint gate, then commit

```bash
python -m pytest -q
python -m ruff check deckle tests
```

## Interface Contracts

### `strategy_for`
- Direction: SS-11 → SS-08
- Owner: SS-11
- Shape: `strategy_for(layout: LayoutSettings) -> LayoutStrategy`. Returns
  `SaddleStitchStrategy()` when `layout.fold_scheme == "folio"`, else `GutterShiftStrategy()`.
  Pure, Qt-free, total over the committed `Literal["none","folio"]`.

### `binding_readout`
- Direction: SS-11 → the panel label and SS-12's CLI `info` wording
- Owner: SS-11
- Shape: `binding_readout(plan: SheetPlan) -> str`. Derived **from the plan**, never from
  settings. Must not raise when `plan.signatures == ()`.

### `cell_guides_for_side` / `cell_labels_for_side`
- Direction: SS-11 → `PreviewView._frame_pixmap`
- Owner: SS-11
- Shape: `cell_guides_for_side(sheet, side, settings, paper_pt) -> list[Cell]`,
  `cell_labels_for_side(plan, sheet, side) -> list[str]`. One entry per page in the side.
  Guide rects come from SS-07's `content_box_rect_pt` per cell — no second copy of the margin
  rule.

### `signature_choices` / `sheets_for_choice`
- Direction: SS-11 → `PrintSession(..., sheets=...)`
- Owner: SS-11
- Shape: `signature_choices(plan) -> list[tuple[str, tuple[int, ...] | None]]`;
  `sheets_for_choice(plan, index) -> tuple[int, ...] | None`. Index 0 is always "All" →
  `None`. **No ordering arithmetic** — the values are `Signature.sheet_indices` verbatim.

### Unchanged and frozen
- `PrintSession`'s public surface. `sheets=` is an existing parameter
  (`print_session.py:156`), not a new one.
- `PreviewView.refresh` / `_on_frame_ready`'s supersede-and-cancel contract, pinned by
  `tests/test_view_workers.py`.
- The single printer-enumeration call site in `print_dialog.py`.

## Verification Commands

```bash
python -m pytest tests/test_layout_panel_binding.py -q
python -m pytest tests/test_print_dialog.py tests/test_preview_fidelity.py tests/test_view_workers.py -q
python -m pytest -q
python -m ruff check deckle tests
```

## Checks

Every command below exits **0** on the passing case. Negative assertions use
`! grep … || (echo "FAIL: …" && exit 1)`, and each was executed against the tree before being
written here. Loops use `|| { echo …; exit 1; }` with **braces, not a subshell** — a
`|| ( … exit 1 )` inside a `for` loop exits only the subshell, so the loop continues and the
loop's status is that of its *last* iteration, silently passing when a middle token is
missing. This was verified empirically; do not convert these to the parenthesised form.

**Markdown escaping:** `|` inside a table cell is written `\|`. Unescape before running:
`\|\|` is the shell's `||`, and `\|` inside a `grep -E` pattern is `|` (alternation). Every
command below was executed in its **unescaped** form against the current tree.

| Criterion | Type | Command |
|---|---|---|
| Five Qt-free binding mutators exist (REQ-036) | [STRUCTURAL] | `for s in set_fold_scheme set_sheets_per_signature set_blank_mode set_sewing_stations set_paper_thickness_pt; do grep -qE "^def $s\(" deckle/app/views/layout_panel.py \|\| { echo "FAIL: layout_panel.py is missing the Qt-free mutator $s"; exit 1; }; done` |
| Each mutator returns a new `Project` via `replace` | [STRUCTURAL] | `python -m pytest tests/test_layout_panel_binding.py -q -k binding_mutators_return_new_projects` |
| `strategy_for` dispatches on `fold_scheme` (REQ-021, REQ-036) | [MECHANICAL] | `python -m pytest tests/test_layout_panel_binding.py -q -k strategy_for_dispatches_on_fold_scheme` |
| Folio through the panel emits a plan with signatures (REQ-036) | [MECHANICAL] | `python -m pytest tests/test_layout_panel_binding.py -q -k recompute_plan_under_folio_yields_signatures` |
| Readout names signatures/sheets/blanks, headlessly (REQ-036) | [MECHANICAL] | `python -m pytest tests/test_layout_panel_binding.py -q -k binding_readout` |
| `binding_readout` exposed as a pure module-level function | [STRUCTURAL] | `grep -qE "^def binding_readout\(" deckle/app/views/layout_panel.py \|\| { echo "FAIL: binding_readout is not a module-level pure helper"; exit 1; }` |
| New unit-aware spinbox registered in `_on_unit_changed` | [STRUCTURAL] | `grep -q "paper_thickness_spinbox" deckle/app/views/layout_panel.py && sed -n '/_on_unit_changed/,/blockSignals(False)/p' deckle/app/views/layout_panel.py \| grep -q "paper_thickness" \|\| { echo "FAIL: paper_thickness_spinbox is not registered in _on_unit_changed's boxes list"; exit 1; }` |
| Per-cell guides and labels under folio (REQ-037) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k "folio_side_produces_one_content_box_guide_per_cell or cell_labels_carry_source_page_number_and_signature_index"` |
| One guide per side under `fold_scheme="none"` (REQ-037) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k non_folio_side_produces_exactly_one_guide` |
| Signature selector populates `sheets=` (REQ-038) | [MECHANICAL] | `python -m pytest tests/test_ui_surface.py -q -k signature_selection_carries_the_signature_sheet_indices` |
| Subset path covers exactly one signature (REQ-035) | [MECHANICAL] | `python -m pytest -q -k "plan_passes_with_explicit_sheets_covers_only_those_sheets or end_to_end_folio_signature_impose_export_fold_and_print"` |
| No ordering arithmetic in the print UI (REQ-038) | [MECHANICAL] | `! grep -nE "reverse\|sheet_order\|% 2" deckle/app/views/print_dialog.py \|\| (echo "FAIL: ordering logic leaked into the print UI" && exit 1)` |
| Printer enumeration stays at its one existing call site | [MECHANICAL] | `test "$(grep -c 'QPrinterInfo.availablePrinters()' deckle/app/views/print_dialog.py)" = "1" \|\| (echo "FAIL: printer enumeration call sites changed in print_dialog.py" && exit 1)` |
| No printer enumeration in the panel or the preview | [MECHANICAL] | `! grep -rnE "QPrinterInfo\|availablePrinters" deckle/app/views/layout_panel.py deckle/app/views/preview_view.py \|\| (echo "FAIL: printer enumeration reached a non-print view -- this hung the app on launch once" && exit 1)` |
| Selecting a signature enumerates nothing | [MECHANICAL] | `python -m pytest tests/test_print_dialog.py -q -k signature_selection_enumerates_no_printers` |
| Qt stays lazily imported; modules import headlessly | [MECHANICAL] | `! grep -nE "^(import\|from)[[:space:]]+PySide6" deckle/app/views/layout_panel.py deckle/app/views/preview_view.py deckle/app/views/print_dialog.py \|\| (echo "FAIL: module-scope Qt import breaks headless importability" && exit 1)` |
| Importing all three views loads no Qt | [MECHANICAL] | `python -c "import sys, deckle.app.views.layout_panel, deckle.app.views.preview_view, deckle.app.views.print_dialog; sys.exit(0 if 'PySide6' not in sys.modules else 1)" \|\| (echo "FAIL: importing a view pulled in PySide6" && exit 1)` |
| Preview supersede/cancel guard intact | [MECHANICAL] | `grep -q "self._worker.cancel.set()" deckle/app/views/preview_view.py && grep -q "worker is not self._worker" deckle/app/views/preview_view.py \|\| { echo "FAIL: PreviewView lost its supersede/cancel guard -- a slow earlier render can repaint over a newer one"; exit 1; }` |
| Worker contract suite green | [MECHANICAL] | `python -m pytest tests/test_view_workers.py -q` |
| UI suites green | [MECHANICAL] | `python -m pytest tests/test_print_dialog.py tests/test_preview_fidelity.py -q` |
| Full suite green | [MECHANICAL] | `python -m pytest -q` |
| Lint clean | [MECHANICAL] | `python -m ruff check deckle tests` |

**Note on `ruff`:** the executable is not on `PATH` in this environment's Git Bash;
`python -m ruff check …` is the invocation that works and was verified to exit 0 on the
current tree.
