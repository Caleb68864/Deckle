# N12 — Reach the schedule under flat sheets, and print the spine width there

**Roadmap item:** `docs/ROADMAP.md` N12
**Depends on:** —. Edits `deckle/app/views/layout_panel.py` (collides with **M1**, and with **N5**/**N11**, which add controls to the same panel) and `deckle/core/schedule.py` (collides with **N5**, which adds a field to `Schedule`, and with **B7**/**B8**, which change what the schedule says). Recommended: **B7/B8 → N5 → N12 → M1**.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

**Save schedule...** lives on the Signatures tab and is disabled unless
`fold_scheme == "folio"`. Under Flat sheets — the default, and the mode the
Flat sheets tab itself calls "the proven path" (`layout_panel.py:780-781`) —
the button is not merely greyed: it is on a tab that is not on screen, because
selecting the Signatures tab *is* selecting folio mode
(`layout_panel.py:749-759`). So there is no way to reach it at all.

The reasoning recorded on the button is that a flat-sheet schedule would be
empty:

```python
        # The schedule lives here rather than beside Save PDF because it is
        # a signature artifact: under gutter shift there is nothing to
        # gather, so the button would be permanently inert next to the
        # export actions.
```

That is half true and the half that is false is the useful half. `build_schedule`
handles a plan with no signatures perfectly well, and `format_schedule_text`
prints a real flat-sheet document: what the binding *is*, how many sheets to
print, and the whole `AT THE PRINTER` block — actual size, which edge to flip
about, print sheet 0 first. None of that is signature-specific and all of it
ruins a job when got wrong.

But the roadmap's specific claim — that "the flat-sheet schedule carries the
spine width a perfect binder cuts boards against" — is **not currently true**.
The number is computed and then not printed. Verified:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.schedule import build_schedule, format_schedule_text
pages=[SourcePage(ref=SourceRef(path="s.pdf",page_index=i,sha256="a"*64,
      width_pt=400.0,height_pt=600.0),rotate_deg=0,skipped=False) for i in range(10)]
s=LayoutSettings(paper=(612.0,792.0),gutter_pt=36.0,binding_edge="left",
                 paper_thickness_pt=0.3)
plan=GutterShiftStrategy().impose(pages,s)
sch=build_schedule(plan,s)
print("signatures:",sch.signatures,"sheets_total:",sch.sheets_total,
      "spine:",sch.spine_width_pt)
print("--- 'Spine' appears in the text?",
      "Spine" in format_schedule_text(sch,"book.pdf"))
EOF
```

```
signatures: () sheets_total: 5 spine: (1.6500000000000001, 1.875)
--- 'Spine' appears in the text? False
```

`Schedule.spine_width_pt` is `(1.65, 1.875)` and the rendered text never
mentions it. For a perfect-bound or side-sewn stack — which is exactly what
flat sheets are for — that is the one number you need before cutting boards and
cannot measure yet.

## 2. Current code

`deckle/app/views/layout_panel.py:1168-1175` — where the button is built, and
onto which tab:

```python
        # The schedule lives here rather than beside Save PDF because it is
        # a signature artifact: under gutter shift there is nothing to
        # gather, so the button would be permanently inert next to the
        # export actions.
        self.save_schedule_button = QPushButton("Save schedule...", signature_tab)
        self.save_schedule_button.setToolTip(SCHEDULE_TOOLTIP)
        signature_form.addRow("", self.save_schedule_button)
        self.save_schedule_button.clicked.connect(self._on_save_schedule_clicked)
```

`deckle/app/views/layout_panel.py:1343-1370` — the gate:

```python
    def _sync_signature_tab(self) -> None:
        """Keep the Signatures tab consistent with the current mode.
        ...
        """
        folio = self.state.project.layout.fold_scheme == "folio"
        loaded = getattr(self, "_document_loaded", bool(self.state.project.pages))
        ...
        # A schedule for nothing is an empty schedule, so the button needs
        # both a fold scheme that gathers AND something to gather.
        self.save_schedule_button.setEnabled(folio and loaded)
        self.save_schedule_button.setToolTip(
            SCHEDULE_TOOLTIP if loaded else "Import a document to build a schedule."
        )
```

`deckle/app/views/layout_panel.py:57-63` — the tooltip, which is written
entirely about gathering:

```python
SCHEDULE_TOOLTIP = (
    "Write the binding schedule to a text file: which sheets gather into "
    "each signature, which way round they nest, where the blanks fall, and "
    "where to pierce for sewing.\n\n"
    "Print it and keep it at the bench -- the imposed PDF says nothing "
    "about what to do with the paper."
)
```

`deckle/app/views/layout_panel.py:1300-1341` — `_on_save_schedule_clicked`,
which is entirely mode-agnostic already: it takes `pages[0].ref.path`, suggests
`<stem>-schedule.txt`, checks `output_path_problem`, and writes
`format_schedule_text(build_schedule(recompute_plan(...), settings), ...)`.
**No change needed to it.**

`deckle/app/views/layout_panel.py:724-747` — the Page setup tab strip, which is
above the mode tabs and applies to both modes:

```python
        setup_label = QLabel("Page setup", self.widget)
        outer.addWidget(setup_label)

        self.setup_tabs = QTabWidget(self.widget)
        outer.addWidget(self.setup_tabs)
        ...
        paper_form = _sub_tab("Paper")
        margins_form = _sub_tab("Margins")
        crop_form = _sub_tab("Crop && trim")
```

`deckle/app/views/layout_panel.py:760-763` — the mode tabs go in below it, and
`outer` continues after them, which is where a mode-independent action belongs.

`deckle/core/schedule.py:350-364` — the flat-sheet branch of the formatter, in
full:

```python
    if schedule.fold_scheme != "folio" or not schedule.signatures:
        lines.append(
            "This document is imposed one page per side, not folded into "
            "signatures, so there is nothing to gather or sew. The binding "
            "is the gutter: every sheet carries its spine margin on the "
            "edge that will be bound, alternating side so the margins line "
            "up once the stack is collated in order."
        )
        lines.append("")
        lines.append(f"Sheets to print: {schedule.sheets_total}")
        lines.append("")
        # A job with no folding is still a job, and the two settings that
        # ruin it are the same ones folio has to get right.
        lines.extend(_printer_lines(schedule))
        return "\n".join(lines) + "\n"
```

`deckle/core/schedule.py:410-435` — the spine block, reachable only from the
folio path:

```python
    lines.append("AFTER SEWING")
    lines.append("-" * len("AFTER SEWING"))
    lines.append("  Stack the signatures in order, 1 first.")
    if schedule.spine_width_pt is not None:
        low, high = schedule.spine_width_pt
        lines.append("")
        lines.append(
            f"  Spine thickness: about {low / 72:.2f}-{high / 72:.2f}in "
            f"({low:.0f}-{high:.0f}pt)."
        )
        lines.append(
            f"    {schedule.sheets_total} sheets at "
            f"{schedule.paper_thickness_pt:.3f}pt, plus "
            f"{int(SWELL_FRACTION_LOW * 100)}-{int(SWELL_FRACTION_HIGH * 100)}% "
            "swell from the sewing thread."
        )
        ...
```

`deckle/core/schedule.py:158-178` — `spine_width_pt(sheet_count, thickness_pt)`,
which is already computed for every plan in `build_schedule`
(`schedule.py:274`) regardless of fold scheme, and returns `None` only when
thickness or sheet count is zero.

`deckle/core/schedule.py:154-156` — the swell constants, whose docstring says
the range comes from **sewing thread**:

```python
SWELL_FRACTION_LOW = 0.10
SWELL_FRACTION_HIGH = 0.25
```

`deckle/core/schedule.py:240-264` — `build_schedule`'s notes block, guarded by
`if signatures:` so a flat-sheet schedule carries no notes at all — including
the "paper thickness is not set" one.

**Call sites of `build_schedule` (grep):** `deckle/core/schedule.py:201`
(definition), `deckle/cli.py:37,1104`,
`deckle/app/views/layout_panel.py:39,1331`; `tests/test_schedule.py:26,66,200,336`,
`tests/test_grain_and_spine.py:22,157,172`, `tests/test_ui_surface.py:954,960,965`,
`tests/test_custom_signatures.py:214,219,227,231`.

**Call sites of `save_schedule_button` (grep):**
`layout_panel.py:1172,1173,1174,1175,1367,1368`. No test references it by name;
`tests/test_ui_surface.py:954-965` tests `build_schedule` directly.

**Existing tests:** `tests/test_schedule.py` (the formatter, including
`test_sewing_stations_are_described_with_their_inset` and the flat-sheet
branch), `tests/test_grain_and_spine.py` (spine width),
`tests/test_ui_surface.py:950-970` (schedule from the panel's own
`recompute_plan`), `tests/test_layout_panel_widgets.py`.

## 3. Change

Two halves, and the second is what makes the first worth doing.

### Half one: the button is mode-independent

Move it out of the Signatures tab and below the mode tabs, where it applies to
whichever mode is selected. Gate it on **having a document**, not on the fold
scheme.

### Half two: the flat-sheet schedule carries the spine width

A flat-sheet stack is bound by gluing, punching or side-sewing, and the
thickness of the block is what boards and a spine piece are cut against. The
number is already on the `Schedule`. The **swell** wording is not transferable:
`SWELL_FRACTION_LOW/HIGH` are explicitly "swell from the sewing thread"
(`schedule.py:149-156`), and a perfect-bound block has no thread in its folds.
So the flat-sheet block reports the **block thickness with no swell** and says
why, rather than reusing a range that would overstate a glued spine by up to a
quarter.

That means one new derived value, not a new field:

```python
def block_width_pt(sheet_count: int, thickness_pt: float) -> float | None:
    """The thickness of an unsewn stack of ``sheet_count`` sheets.

    :param sheet_count: pieces of paper in the whole job.
    :param thickness_pt: caliper of one sheet, in points.
    :returns: the thickness in points, or ``None`` if thickness is unset.

    A single number rather than the range :func:`spine_width_pt` gives,
    because the range is swell -- thread accumulating in a fold -- and
    there are no folds and no thread here. Reporting a sewn range over a
    glued block would overstate it by up to a quarter, which is a recut
    set of boards.

    Still an estimate: paper caliper varies a few percent with humidity,
    and a perfect binder's glue adds a little. Measure the real block
    before covering, which is what the schedule says.
    """
    if thickness_pt <= 0 or sheet_count <= 0:
        return None
    return sheet_count * thickness_pt
```

### Steps

1. **`deckle/core/schedule.py` — add `block_width_pt`**, directly above
   `spine_width_pt` (`schedule.py:158`), with the docstring above.

2. **`deckle/core/schedule.py` — print it in the flat-sheet branch.** Replace
   the `return` at `schedule.py:364` so the branch continues:

   ```python
           lines.extend(_printer_lines(schedule))
           lines.extend(_binding_the_stack_lines(schedule))
           return "\n".join(lines) + "\n"
   ```

   with a new module-level formatter beside `_printer_lines`:

   ```python
   def _binding_the_stack_lines(schedule: Schedule) -> list[str]:
       """What to do with a flat stack once it is printed.

       The counterpart to the folio path's AFTER SEWING block. A flat-sheet
       job is bound by gluing, punching or side-sewing, and the one number
       needed before any of those -- and not measurable until it is too
       late to matter -- is how thick the block will be. It is what boards
       and a spine piece are cut against.
       """
       block = block_width_pt(schedule.sheets_total, schedule.paper_thickness_pt)
       lines = ["BINDING THE STACK", "-" * len("BINDING THE STACK")]
       lines.append("  Collate the sheets in the order they came off the")
       lines.append("  printer. The gutter alternates side by side, so the")
       lines.append("  spine margins line up once the stack is in order.")
       lines.append("")
       if block is None:
           lines.append(
               "  Block thickness is not estimated -- set paper thickness to"
           )
           lines.append("  get a figure to cut boards against.")
       else:
           lines.append(
               f"  Block thickness: about {block / 72:.2f}in ({block:.0f}pt)."
           )
           lines.append(
               f"    {schedule.sheets_total} sheets at "
               f"{schedule.paper_thickness_pt:.3f}pt. No swell is added:"
           )
           lines.append(
               "    swell is thread accumulating in a fold, and there are"
           )
           lines.append("    no folds here.")
           lines.append(
               "    Cut boards and spine against this, then measure the real"
           )
           lines.append("    block before covering.")
       lines.append("")
       return lines
   ```

   The wording deliberately parallels the folio block (`schedule.py:413-429`) —
   same heading shape, same "cut boards against this, then measure the real
   block" closing — so a binder who has read one recognises the other.

3. **`deckle/core/schedule.py` — carry the thickness note into flat sheets.**
   The notes block is guarded by `if signatures:` (`schedule.py:241`), so a
   flat-sheet schedule says nothing when thickness is unset. Move the
   thickness-specific note out of that guard:

   ```python
       notes: list[str] = []
       if settings.paper_thickness_pt <= 0:
           notes.append(
               "Paper thickness is not set, so the block thickness is not "
               "estimated. Measure your stock and set it if you are cutting "
               "boards."
           )
       if signatures:
           widest = max(sig.sheet_count for sig in signatures)
           creep = _creep_note(widest, settings.paper_thickness_pt)
           if creep is not None:
               notes.append(creep)
           uneven = ...unchanged...
   ```

   Note the folio path's `elif settings.paper_thickness_pt <= 0:` branch
   collapses into the new unconditional one, and its wording changes from
   "creep is not estimated" to cover both. Keep the creep note exactly as it
   is — creep is a folding phenomenon and belongs inside `if signatures:`.

   `format_schedule_text`'s flat-sheet branch must then also render
   `schedule.notes`, which today it does not. Add before the return:

   ```python
           if schedule.notes:
               for note in schedule.notes:
                   lines.append(f"  Note: {note}")
               lines.append("")
   ```

4. **`deckle/app/views/layout_panel.py` — move the button.** Delete the block
   at `layout_panel.py:1168-1175` and add, in `__init__` **after**
   `outer.addWidget(self.tabs)` (`layout_panel.py:762`) so it sits below both
   mode tabs:

   ```python
           # Below the mode tabs, not inside one. A schedule is not a
           # signature artefact: the flat-sheet schedule carries how the
           # stack collates, the block thickness a perfect binder cuts
           # boards against, and the whole AT THE PRINTER block -- actual
           # size and which edge to flip about -- which ruins a job either
           # way when it is got wrong. On the Signatures tab it was not
           # merely disabled under flat sheets, it was on a tab that is not
           # on screen: selecting that tab IS selecting folio.
           self.save_schedule_button = QPushButton("Save schedule...", self.widget)
           self.save_schedule_button.setToolTip(SCHEDULE_TOOLTIP)
           outer.addWidget(self.save_schedule_button)
           self.save_schedule_button.clicked.connect(self._on_save_schedule_clicked)
   ```

   It must be constructed before `self._sync_signature_tab()` runs
   (`layout_panel.py:1182`), which it now is.

5. **`deckle/app/views/layout_panel.py` — regate it.** In `_sync_signature_tab`
   (`layout_panel.py:1367-1370`):

   ```python
           # A schedule needs a document; it does not need a fold scheme.
           # Under flat sheets it says how to collate the stack, how thick
           # the block will be, and what to set at the printer.
           self.save_schedule_button.setEnabled(loaded)
           self.save_schedule_button.setToolTip(
               SCHEDULE_TOOLTIP if loaded else "Import a document to build a schedule."
           )
   ```

   The `folio` local is still used by the tab-selection half of the method
   above; leave that alone.

6. **`deckle/app/views/layout_panel.py` — the tooltip covers both modes.**

   ```python
   SCHEDULE_TOOLTIP = (
       "Write the binding schedule to a text file, and keep it at the bench "
       "-- the imposed PDF says nothing about what to do with the paper.\n\n"
       "Under Signatures: which sheets gather into each signature, which way "
       "round they nest, where the blanks fall, and where to pierce for "
       "sewing.\n\n"
       "Under Flat sheets: how the stack collates, how thick the block will "
       "be so you can cut boards against it, and what to set at the printer "
       "-- actual size, and which edge to turn the sheet about.\n\n"
       "Both carry the print-sheet-0-first check."
   )
   ```

7. **`deckle/app/views/layout_panel.py` — the module docstring and the
   `_sync_signature_tab` docstring** both describe the button as gated on the
   fold scheme. Update the sentence in `_sync_signature_tab`'s docstring from
   "and gate the schedule button on having a document to schedule" — it is
   already correct as written; only the code disagreed with it. Leave it.

## 4. Tests

### `tests/test_schedule.py` (extend)

The module already builds flat-sheet plans; reuse its helpers.

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_a_flat_sheet_schedule_gives_a_block_thickness` | 5 sheets, `paper_thickness_pt=0.3`, `fold_scheme="none"` | the text contains `"BINDING THE STACK"` and `"Block thickness: about 0.02in (2pt)"` | `AssertionError`: "Spine"/"Block" appears nowhere — verified above |
| `test_the_flat_sheet_block_carries_no_swell` | same | the text contains `"No swell is added"` and does **not** contain `"10-25%"` | as above |
| `test_a_flat_sheet_schedule_without_thickness_says_so` | `paper_thickness_pt=0.0` | the text contains `"Block thickness is not estimated"` and `"Note: Paper thickness is not set"` | as above; today the flat branch prints no notes at all |
| `test_block_width_is_sheets_times_caliper` | `block_width_pt(5, 0.3)` | `== 1.5` (exactly, to 9dp) | `ImportError: cannot import name 'block_width_pt'` |
| `test_block_width_is_none_without_a_thickness` | `block_width_pt(5, 0.0)` and `block_width_pt(0, 0.3)` | both `None` | as above |
| `test_the_folio_schedule_still_reports_a_sewn_range` | a folio plan with thickness | the text still contains `"Spine thickness: about"` and `"swell from the sewing thread"` | passes today; it pins that N12 did not change the folio path |
| `test_the_flat_sheet_schedule_still_names_the_flip_edge` | flat plan | `"Duplex: turn the sheet about its"` still present | passes today; pins the `AT THE PRINTER` block |
| `test_the_flat_sheet_schedule_still_counts_its_sheets` | flat plan of 5 | `"Sheets to print: 5"` | passes today |

### `tests/test_grain_and_spine.py` (extend — it owns spine width)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_a_flat_sheet_plan_still_computes_a_spine_range` | `build_schedule(flat_plan, settings).spine_width_pt` is not `None` | passes today — it pins the surprising fact that the number was always computed and never printed |

### `tests/test_layout_panel_widgets.py` (extend)

`LayoutPanel` constructs headless.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_schedule_button_is_reachable_under_flat_sheets` | a panel over a document with `fold_scheme="none"`; `panel.save_schedule_button.isEnabled()` | `AssertionError: assert False` |
| `test_the_schedule_button_is_not_on_a_mode_tab` | `panel.save_schedule_button.parentWidget() is panel.widget` — i.e. not the signature tab | `AssertionError` |
| `test_the_schedule_button_still_needs_a_document` | a panel over an empty project | `isEnabled()` is `False` and the tooltip is `"Import a document to build a schedule."` | passes today |
| `test_switching_to_signatures_leaves_the_button_enabled` | toggle `panel.tabs` to the signature tab and back | enabled throughout | `AssertionError` on the way back |
| `test_saving_a_flat_sheet_schedule_writes_the_block_thickness` | patch `QFileDialog.getSaveFileName` to return a `tmp_path` file; `fold_scheme="none"`, thickness set; click | the written file contains `"BINDING THE STACK"`, and a `schedule_saved` signal carrying `"Saved binding schedule to"` | the button is disabled, so the click does nothing and no file appears |

### `tests/test_ui_surface.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_panels_flat_sheet_schedule_is_not_empty` | `format_schedule_text(build_schedule(recompute_plan(state.project), state.project.layout))` for a flat-sheet project | longer than 500 characters and contains both `"AT THE PRINTER"` and `"BINDING THE STACK"` | `AssertionError` on the second substring |

### `tests/test_cli.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_schedule_under_flat_sheets_reports_the_block` | `deckle schedule tests/fixtures/sample.pdf --paper-thickness 0.004in` stdout | contains `"BINDING THE STACK"` and `"Block thickness"` | `AssertionError` |

## 5. Acceptance

| Check | Command |
|---|---|
| The schedule tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_schedule.py tests/test_grain_and_spine.py` |
| The panel tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider -k "layout_panel or ui_surface"` |
| The CLI test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The button no longer depends on the fold scheme | `! sed -n '/save_schedule_button.setEnabled/p' deckle/app/views/layout_panel.py \| grep -q folio` |
| The flat-sheet schedule reports a thickness | `.venv/bin/python -m deckle.cli schedule tests/fixtures/sample.pdf --paper-thickness 0.004in \| grep -q "Block thickness"` |
| A glued block is not given sewing swell | `! sed -n '/def _binding_the_stack_lines/,/^def /p' deckle/core/schedule.py \| grep -q "SWELL_FRACTION"` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| `[HUMAN]` It reads at a bench | Print the flat-sheet schedule for a 60-sheet job with a thickness set. It must tell you, in order: what the binding is, how many sheets, what to set at the printer, which edge to flip about, to proof sheet 0 first, and how thick the block will be. Nothing on it should mention gathering, nesting or sewing. |

## 6. Out of scope

- **B7** — the schedule's "reader-facing page numbers" are `source page_index + 1`,
  wrong whenever pages are skipped, blanks are inserted, or more than one
  source is imported. That is the folio path's per-sheet listing and N12 does
  not touch it. Land B7 first if both are scheduled; the flat-sheet branch
  prints no page numbers at all, so N12 neither helps nor hurts it.
- **B8** (creep judged against `sheets_per_signature` when the grouping
  overrode it). Folio only.
- **N5** (`sewing_station_positions_pt` on the `Schedule`). Folio only; the two
  edits to `schedule.py` do not overlap.
- **F10** ("read the book back", a fold simulator line in the schedule).
  Signature-specific.
- **M1.** Moving one button out of a form is not the control-table problem.
- **Making the schedule an action in N7's File menu.** Reasonable, and N7's
  `build_menu_bar` omits an action whose handler does not resolve — so it can
  be added there later without touching this.

## 7. decisions.md entry

```
## 2026-09-05 — The flat-sheet schedule was unreachable, and had no thickness in it
- Symptom: "Save schedule..." lived on the Signatures tab and was disabled unless `fold_scheme == "folio"`. Since selecting that tab IS selecting folio, the button was not merely greyed under Flat sheets -- it was on a tab that is not on screen. The comment on it said a flat-sheet schedule would be inert. It is not: it carries how the stack collates and the whole AT THE PRINTER block, which ruins a job either way.
- Symptom: And the roadmap's claim that the flat-sheet schedule "carries the spine width a perfect binder cuts boards against" was false. `Schedule.spine_width_pt` is computed for every plan -- measured at (1.65, 1.875)pt for a 5-sheet job -- and `format_schedule_text`'s flat-sheet branch returned before ever printing it.
- Fix: The button moved below the mode tabs and is gated on having a document, not on a fold scheme. A new BINDING THE STACK block reports `block_width_pt` -- sheets x caliper, a single number with NO swell, because swell is thread accumulating in a fold and a glued block has neither. The flat branch now renders `Schedule.notes` too, and the "paper thickness is not set" note moved out of the `if signatures:` guard that hid it.
- Surfaces: Reusing `spine_width_pt`'s 10-25% range for a perfect-bound block would have overstated it by up to a quarter, which is a recut set of boards. The two numbers describe different physical objects and now say so.
- Watch: A value computed on a dataclass and never rendered looks exactly like a value that does not exist. `spine_width_pt` was right for a year and invisible for half of them.
- Commit: <fill in>
```

## 8. Traps

- **Selecting the Signatures tab sets `fold_scheme = "folio"`**
  (`layout_panel.py:749-759`, `_on_mode_tab_changed` at 1562). Any control
  placed on that tab is unreachable in the other mode, not merely disabled.
  Below `outer.addWidget(self.tabs)` is the mode-independent region.
- **`_sync_signature_tab` runs during `__init__`** (`layout_panel.py:1182`),
  so the button must exist before it. Moving the construction *after* that call
  produces an `AttributeError` at window build time.
- **`format_schedule_text`'s flat branch returns early** (`schedule.py:364`).
  Anything appended after that line in the function body is folio-only; the new
  block must go before the `return`.
- **The notes list is built inside `if signatures:`** (`schedule.py:241`), and
  the folio path's thickness note is an `elif` on the creep note
  (`schedule.py:246`). Pulling it out has to keep creep inside the guard —
  creep is a folding phenomenon and a flat sheet has none.
- **`build_schedule` already handles a plan with no signatures** and its
  docstring says so (`schedule.py:208-212`). Nothing about that needs guarding;
  the defect was entirely in the formatter and the button.
- **`_on_save_schedule_clicked` needs `pages[0].ref.path`** for the suggested
  filename (`layout_panel.py:1312`) and returns early on an empty document. The
  enabled-state gate on `loaded` is what keeps those consistent.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli
  schedule` for the text checks.
