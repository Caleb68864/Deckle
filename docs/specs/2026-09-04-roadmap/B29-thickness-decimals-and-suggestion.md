# B29 — A caliper needs more than whole points, and typing one must update what it decides

**Roadmap item:** `docs/ROADMAP.md` B29
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

> **Read this first.** B29 is satisfied *by* M1
> (`M1-layout-panel-binding-table.md`): the `LengthSpinBox` seam M1
> introduces owns `decimals_for(unit)` per field class, which is exactly
> the rule below. If M1 is scheduled, **do not do B29 separately.** This
> file exists so B29 can ship alone if M1 slips, and so M1 has the
> numbers written down. The pure `decimals_for` / `single_step_for`
> functions specified here are the ones `LengthSpinBox` should absorb
> verbatim, so doing B29 first costs M1 nothing.

---

## 1. Context

`paper_thickness_pt` is a single sheet's caliper. Ordinary 80 gsm copier
paper is 0.295 pt; 160 gsm card is 0.59 pt. Every caliper Deckle's own
preset list can produce lies between 0.2 and 0.6 pt
(`deckle/core/paper.py:129-135` with `caliper_pt_from_gsm`).

The panel shows that number in whatever the Units dropdown says, and
`_on_unit_changed` gives every length box the same display rule:
`setDecimals(0 if unit == "pt" else 3)` and
`setSingleStep(1.0 if unit in ("pt", "mm") else 0.125)`. Applied to a
caliper, that is three separate failures:

1. **In points the box shows `0`.** Zero decimals against a value of 0.28.
   The document still holds 0.2835 — the conversion blocks signals, so
   nothing is written — but the panel is now stating that this paper has
   no thickness, which is the value that disables the spine estimate and
   the creep advisory entirely.
2. **The next nudge writes the lie back.** One press of the up arrow
   steps by 1.0 pt, so the caliper becomes 1.0 — a sheet three times
   thicker than 160 gsm card. Two presses is board.
3. **The step is wrong in every unit, not just points.** The box is
   constructed with `setDecimals(4)` and `setSingleStep(0.001)`
   (`layout_panel.py:862-863`) — sensible for inches. The first unit
   change overwrites both with the generic rule, so afterwards a single
   arrow press in inches moves the caliper by **0.125 in**, which is 9 pt:
   thirty sheets of copier paper per keystroke.

Separately, and independently of units:

4. **Typing a thickness updates nothing that depends on it.**
   `_on_paper_thickness_changed` recomputes the plan and the binding
   readout, and stops. The gathering-size suggestion under *Sheets per
   signature* — the whole point of asking for a caliper — keeps showing
   the advice computed for the previous number. And the *Paper stock*
   dropdown keeps naming a preset the document no longer matches, so the
   panel says "160gsm card (0.208 mm)" over a stored 1.44 pt (0.51 mm).
   Choosing a stock refreshes both (`layout_panel.py:1598-1599`); typing
   the same information by hand refreshes neither.

**Repro** (headless, from the repo root):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.app.state import AppState
from deckle.core.models import LayoutSettings, Project
from PySide6.QtWidgets import QApplication
app = QApplication([])
from deckle.app.views.layout_panel import LayoutPanel

state = AppState(Project(pages=[], layout=LayoutSettings(
    paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left",
    paper_thickness_pt=0.2835, fold_scheme="folio",
    sheets_per_signature=4), printer=None))
p = LayoutPanel(state)
print("in : decimals", p.paper_thickness_spinbox.decimals(),
      "step", p.paper_thickness_spinbox.singleStep(),
      "value", p.paper_thickness_spinbox.value())
p.unit_combo.setCurrentText("pt")
print("pt : decimals", p.paper_thickness_spinbox.decimals(),
      "step", p.paper_thickness_spinbox.singleStep(),
      "shows", p.paper_thickness_spinbox.text())
p.paper_thickness_spinbox.stepBy(1)
print("after one step up -> stored", state.project.layout.paper_thickness_pt)
p.unit_combo.setCurrentText("in")
print("back to in: decimals", p.paper_thickness_spinbox.decimals(),
      "step", p.paper_thickness_spinbox.singleStep())

p2 = LayoutPanel(AppState(Project(pages=[], layout=LayoutSettings(
    paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left",
    fold_scheme="folio", sheets_per_signature=4), printer=None)))
p2.paper_stock_combo.setCurrentText(p2.paper_stock_combo.itemText(5))
print("stock chosen ->", repr(p2.paper_stock_combo.currentText()))
print("            suggestion:", repr(p2.suggestion_label.text()))
p2.paper_thickness_spinbox.setValue(0.02)
print("typed 0.02in ->", repr(p2.paper_stock_combo.currentText()))
print("            suggestion:", repr(p2.suggestion_label.text()))
print("            stored:", p2.state.project.layout.paper_thickness_pt)
EOF
```

Current output:

```
in : decimals 4 step 0.001 value 0.0039
pt : decimals 0 step 1.0 shows 0
after one step up -> stored 1.0
back to in: decimals 3 step 0.125
stock chosen -> '160gsm card  (0.208 mm)'
            suggestion: '2 sheets (8 pages) suits this paper -- beyond that the fore-edge creep starts to show; a trim would allow more.'
typed 0.02in -> '160gsm card  (0.208 mm)'
            suggestion: '2 sheets (8 pages) suits this paper -- beyond that the fore-edge creep starts to show; a trim would allow more.'
            stored: 1.44
```

The last three lines are the second half of the bug in one picture: the
stored caliper is now 1.44 pt, the dropdown still names a 0.208 mm paper,
and the advice on screen was computed for a caliper that is no longer
anywhere in the document.

## 2. Current code

The construction of the box, `deckle/app/views/layout_panel.py:861-876`:

```python
        self.paper_thickness_spinbox = QDoubleSpinBox(self.widget)
        self.paper_thickness_spinbox.setDecimals(4)
        self.paper_thickness_spinbox.setSingleStep(0.001)
        self.paper_thickness_spinbox.setRange(0.0, from_points(10.0, self._unit))
        self.paper_thickness_spinbox.setValue(
            from_points(state.project.layout.paper_thickness_pt, self._unit)
        )
        self.paper_thickness_spinbox.setToolTip(
            "The caliper of a single sheet. Ordinary 20lb office paper is "
            "about 0.004in; card is several times that.\n\n"
            "Deckle uses it for two things on the binding schedule: how far "
            "the innermost leaf of a signature protrudes at the fore-edge, "
            "and how thick the sewn block will be at the spine -- which is "
            "the number you cut boards against.\n\n"
            "Leave at 0 and neither is estimated."
        )
```

The rule that overwrites it, `deckle/app/views/layout_panel.py:1436-1442`:

```python
        for box, points, cap_pt in boxes:
            box.blockSignals(True)
            box.setRange(0.0, from_points(cap_pt, unit))
            box.setDecimals(0 if unit == "pt" else 3)
            box.setSingleStep(1.0 if unit in ("pt", "mm") else 0.125)
            box.setValue(from_points(points, unit))
            box.blockSignals(False)
```

The handler that does too little, `deckle/app/views/layout_panel.py:1714-1720`:

```python
    def _on_paper_thickness_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(
            self.state, lambda project: set_paper_thickness_pt(project, points)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
```

Compare the handler for the *other* way to set the same field,
`deckle/app/views/layout_panel.py:1586-1600` — which does all four things:

```python
    def _on_paper_stock_changed(self, label: str) -> None:
        if label == CUSTOM_STOCK_LABEL:
            return
        name = label.split("  (")[0]
        plan = apply_layout_change(
            self.state, lambda project: set_paper_stock(project, name)
        )
        self.paper_thickness_spinbox.blockSignals(True)
        self.paper_thickness_spinbox.setValue(
            from_points(self.state.project.layout.paper_thickness_pt, self._unit)
        )
        self.paper_thickness_spinbox.blockSignals(False)
        self._refresh_suggestion()
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
```

Two controls write one field and only one of them keeps the panel
consistent afterwards.

### Everything that reads `paper_thickness_pt` or the suggestion

`grep -rn "paper_thickness_pt\|_refresh_suggestion\|signature_suggestion\|CUSTOM_STOCK_LABEL" --include="*.py" deckle tests`:

| Site | What it does |
|---|---|
| `deckle/core/models.py:409` | the field |
| `deckle/core/paper.py:178` `suggest_sheets_per_signature(caliper_pt, trim_pt)` | the advice |
| `deckle/core/schedule.py` (`spine_width_pt`, `_creep_note`) | the schedule's spine and creep numbers |
| `deckle/core/layout.py:800` `_creep_advisory` | the imposer's creep warning |
| `layout_panel.py:347-358` `set_paper_thickness_pt` | the pure setter |
| `layout_panel.py:457-477` `set_paper_stock` | preset → setter |
| `layout_panel.py:480-508` `set_paper_from_weight` | weight → setter |
| `layout_panel.py:511-524` `signature_suggestion` | wraps `suggest_sheets_per_signature`, adds `trim_pt` |
| `layout_panel.py:527-555` `signature_suggestion_text` | the sentence |
| `layout_panel.py:1683-1693` `_refresh_suggestion` | shows/hides label and button |
| `layout_panel.py:1598` | `_on_paper_stock_changed` calls `_refresh_suggestion` |
| `layout_panel.py:1653` | `_on_trim_changed` calls `_refresh_suggestion` |
| `layout_panel.py:1679` | `_on_apply_suggestion` calls `_refresh_suggestion` |
| `layout_panel.py:1285` | `refresh_from_project` calls `_refresh_suggestion` |
| `layout_panel.py:361` | `CUSTOM_STOCK_LABEL`, and `:1273` where refresh resets the combo |
| `deckle/cli.py` `--paper-thickness` / `--paper-weight` | the CLI paths |
| `tests/test_layout_panel_paper.py` | the pure suggestion functions |
| `tests/test_layout_panel_widgets.py:76-112, 181-188` | the label appearing/vanishing and the Custom label |
| `tests/test_ui_surface.py:94, 952-968` | `set_paper_thickness_pt` round trip; `paper_thickness_spinbox.setValue(0.004)` "inches, the panel's unit" feeding the spine estimate |

Four handlers call `_refresh_suggestion`; the one that sets the number by
hand is the fifth and does not.

### Existing tests that touch this code

- `tests/test_layout_panel_widgets.py::test_advice_appears_once_a_paper_is_chosen`,
  `::test_taking_the_advice_changes_the_sheet_count_and_clears_it`,
  `::test_a_trim_changes_the_advice`,
  `::test_a_thickness_with_no_preset_behind_it_reads_as_custom`
- `tests/test_ui_surface.py::test_thickness_feeds_the_spine_estimate` —
  sets the spinbox to `0.004` in inches and asserts the schedule's spine
  estimate appears. Runs in the default unit, so it does not see this.
- Nothing asserts a decimals or step value anywhere:
  `grep -rn "decimals()\|singleStep()" tests/` returns nothing.

## 3. Change

Two independent pieces. Do both; either alone leaves the panel able to
state a caliper it does not hold.

Chosen for the decimals rule: **a field class**, so the caliper box and
the geometry boxes can have different precision without either being a
special case buried in a loop. Rejected: keying precision off the
magnitude of the current value — the box would change resolution while
being typed into, which is worse than a fixed wrong precision.

1. **`deckle/app/views/layout_panel.py`, new module-level functions**,
   placed immediately after `from_points`
   (i.e. after line 162, before `imageable_inset_pt`). Pure, Qt-free,
   directly testable:

   ```python
   #: How a length box's precision and step depend on what it measures.
   #: Two classes, because a caliper and a margin differ by two orders of
   #: magnitude: 0.28pt of paper against 18pt of margin. One rule for both
   #: displayed a caliper as "0" and stepped it by a whole point, which is
   #: three sheets of card per keystroke.
   LENGTH_KINDS: tuple[str, ...] = ("length", "thickness")


   def decimals_for(kind: str, unit: str) -> int:
       """How many decimal places a box of this kind shows in ``unit``.

       :param kind: ``"length"`` for geometry (gutter, margins, trim,
           crop) or ``"thickness"`` for a caliper.
       :param unit: a key of :data:`LENGTH_UNITS`.
       :returns: the decimal places. A caliper keeps two even in points,
           because every paper in :data:`deckle.core.paper.PAPER_PRESETS`
           lands between 0.2 and 0.6pt and zero places renders all of them
           as ``0``.
       """
       if kind == "thickness":
           return 2 if unit == "pt" else 4
       return 0 if unit == "pt" else 3


   def single_step_for(kind: str, unit: str) -> float:
       """How far one arrow press moves a box of this kind in ``unit``.

       :param kind: as :func:`decimals_for`.
       :param unit: a key of :data:`LENGTH_UNITS`.
       :returns: the step. Always the smallest increment the box can
           display, for a thickness -- a caliper is measured, not
           adjusted, so a step that overshoots every real paper is worse
           than one that is slow.
       """
       if kind == "thickness":
           return 0.05 if unit == "pt" else (0.01 if unit == "mm" else 0.001)
       return 1.0 if unit in ("pt", "mm") else 0.125
   ```

   Values, stated so they are not left to taste: thickness decimals
   `2` in pt and `4` otherwise; thickness step `0.05` pt, `0.01` mm,
   `0.001` in and cm. Length decimals and step are the current rule,
   unchanged.

2. **`LayoutPanel.__init__`, thickness box (lines 862-863).** Replace the
   two literals with the functions, so construction and unit change
   cannot disagree:

   ```python
        self.paper_thickness_spinbox.setDecimals(decimals_for("thickness", self._unit))
        self.paper_thickness_spinbox.setSingleStep(single_step_for("thickness", self._unit))
   ```

3. **`LayoutPanel.__init__`, the other length boxes.** Do the same for
   `trim_spinbox` (line 895), the crop box loop (line 916), `gutter_spinbox`
   (lines 941-942) and the margin box loop (lines 1018-1019), passing
   `"length"`. Their literals are already what `decimals_for("length", "in")`
   and `single_step_for("length", "in")` return, so this is a
   no-behaviour-change edit that removes the second copy of the rule.

4. **`LayoutPanel._on_unit_changed` (lines 1429-1442).** Carry a kind
   through the `boxes` list and use the functions:

   ```python
        layout = self.state.project.layout
        boxes = [(self.gutter_spinbox, layout.gutter_pt, 288.0, "length")]
        boxes += [
            (self.margin_spinboxes[f], getattr(layout, f), 216.0, "length")
            for f in MARGIN_FIELDS
        ]
        boxes.append(
            (self.paper_thickness_spinbox, layout.paper_thickness_pt, 10.0, "thickness")
        )
        self._unit = unit
        for box, points, cap_pt, kind in boxes:
            box.blockSignals(True)
            box.setRange(0.0, from_points(cap_pt, unit))
            box.setDecimals(decimals_for(kind, unit))
            box.setSingleStep(single_step_for(kind, unit))
            box.setValue(from_points(points, unit))
            box.blockSignals(False)
   ```

   If B11 has already landed, its trim and crop entries join this list
   with kind `"length"`; if it has not, leave the list as it is — B11 is
   a separate spec and this one must not silently do half of it.

5. **`LayoutPanel._on_paper_thickness_changed` (lines 1714-1720).** Make
   it keep the panel consistent, exactly as `_on_paper_stock_changed`
   does. Order matters: mutate first, then refresh the two things that
   read the new value, then emit.

   ```python
    def _on_paper_thickness_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(
            self.state, lambda project: set_paper_thickness_pt(project, points)
        )
        # A caliper typed by hand is no longer the preset that was
        # chosen, and the gathering advice was computed from the old
        # number. Choosing a stock already refreshed both; typing the
        # same information refreshed neither, so the dropdown named a
        # paper the document did not hold and the advice on screen was
        # about a caliper nothing stored any more.
        self.paper_stock_combo.blockSignals(True)
        self.paper_stock_combo.setCurrentText(CUSTOM_STOCK_LABEL)
        self.paper_stock_combo.blockSignals(False)
        self._refresh_suggestion()
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
   ```

   `blockSignals` around the combo is belt-and-braces —
   `_on_paper_stock_changed` returns immediately for
   `CUSTOM_STOCK_LABEL` — but it states the intent, and it stops a future
   change to that early return from turning this into a mutation loop.

6. **Do not change `_refresh_suggestion` itself.** It already hides the
   label and the button when there is nothing to say
   (`layout_panel.py:1683-1693`), which is what makes "typed a caliper the
   suggestion already matches" produce silence rather than a repeat.

## 4. Tests

New file `tests/test_layout_panel_thickness.py`, headed the way
`tests/test_layout_panel_widgets.py` is (offscreen platform set before
any Qt import, module-scoped `QApplication` fixture,
`pytest.importorskip("PySide6")`).

Pure-function tests need no Qt at all and go first in the file:

| Test | Assertion in words | Failure on the unfixed tree |
|---|---|---|
| `test_a_caliper_keeps_two_decimals_in_points` | `decimals_for("thickness", "pt") == 2` | `ImportError: cannot import name 'decimals_for' from 'deckle.app.views.layout_panel'` |
| `test_a_caliper_keeps_four_decimals_everywhere_else` | `decimals_for("thickness", u) == 4` for `in`, `mm`, `cm` | same import error |
| `test_a_geometry_length_keeps_the_rule_it_had` | `decimals_for("length", "pt") == 0` and `== 3` for the rest | same import error |
| `test_every_preset_caliper_is_visible_at_the_points_precision` | for each `stock` in `deckle.core.paper.PAPER_PRESETS`, `round(stock.caliper_pt, decimals_for("thickness", "pt")) > 0` | same import error. This is the test that justifies the constant: it fails if anyone puts the value back to 0 or 1. |
| `test_one_step_never_moves_a_caliper_past_the_thickest_preset` | `single_step_for("thickness", "pt") < min(s.caliper_pt for s in PAPER_PRESETS)` | same import error |

Widget tests:

| Test | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_a_caliper_is_still_readable_in_points` | panel with `paper_thickness_pt=0.2835`; `panel.unit_combo.setCurrentText("pt")` | `panel.paper_thickness_spinbox.value() == approx(0.28, abs=0.005)` | `assert 0.0 == approx(0.28 ± 0.005)` — the box rounded the caliper away. |
| `test_stepping_a_caliper_in_points_does_not_triple_it` | as above, then `panel.paper_thickness_spinbox.stepBy(1)` | `state.project.layout.paper_thickness_pt < 0.4` | `assert 1.0 < 0.4` — one arrow press stored 1.0 pt. |
| `test_the_step_survives_a_unit_round_trip` | `setCurrentText("pt")` then `setCurrentText("in")` | `panel.paper_thickness_spinbox.singleStep() == approx(0.001)` and `decimals() == 4` | `assert 0.125 == approx(0.001)` — the generic rule overwrote the construction-time values. |
| `test_typing_a_caliper_resets_the_stock_dropdown_to_custom` | choose `paper_stock_combo.itemText(5)` (160 gsm card), then `paper_thickness_spinbox.setValue(0.02)` | `panel.paper_stock_combo.currentText() == CUSTOM_STOCK_LABEL` | `assert '160gsm card  (0.208 mm)' == 'Custom (set thickness below)'`. |
| `test_typing_a_caliper_refreshes_the_gathering_advice` | as above; capture `suggestion_label.text()` before and after | the text differs, and mentions `"1 sheets"` for the thicker paper | the two strings are equal — the label still advises for 0.208 mm stock. |
| `test_typing_a_caliper_that_matches_the_advice_hides_it` | set `sheets_per_signature` to the suggestion, then re-type the same caliper | `suggestion_label.isVisible() is False` and `text() == ""` | fails only after the previous fix exists; include it so the "advice that repeats the current state back is noise" rule is pinned on this path too. |
| `test_choosing_a_stock_still_works` | `paper_stock_combo.setCurrentText(itemText(1))` | thickness stored `> 0`, suggestion label non-empty | passes before and after — the regression guard on the path that was already right. |

Extend one existing test rather than adding a near-duplicate:

- `tests/test_ui_surface.py::test_thickness_feeds_the_spine_estimate`
  already sets the spinbox to `0.004` inches. Add one line after it
  asserting `panel.paper_stock_combo.currentText() == CUSTOM_STOCK_LABEL`.
  Fails on the unfixed tree with the combo still on `CUSTOM_STOCK_LABEL`
  — it starts there, so this particular panel does **not** catch the bug.
  Change the test to choose a stock first if you want it to bite; if that
  makes the test about two things, leave it alone and rely on the new
  file. Stated here so the implementer does not spend time on it: the new
  file is where the coverage belongs.

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_panel_thickness.py -q --no-header -p no:cacheprovider` |
| The decimals rule is a named function, not a literal in a loop | `grep -qE "^def decimals_for\(" deckle/app/views/layout_panel.py` |
| The step rule likewise | `grep -qE "^def single_step_for\(" deckle/app/views/layout_panel.py` |
| The old inline rule is gone | `! grep -q 'setDecimals(0 if unit == "pt" else 3)' deckle/app/views/layout_panel.py` |
| The thickness handler refreshes the advice | `sed -n '/    def _on_paper_thickness_changed/,$p' deckle/app/views/layout_panel.py \| grep -q _refresh_suggestion` |
| The thickness handler resets the stock dropdown | `sed -n '/    def _on_paper_thickness_changed/,$p' deckle/app/views/layout_panel.py \| grep -q CUSTOM_STOCK_LABEL` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

The last three rows currently exit 1. Verified:

```
$ grep -c 'setDecimals(0 if unit == "pt" else 3)' deckle/app/views/layout_panel.py
1
$ sed -n '/    def _on_paper_thickness_changed/,$p' deckle/app/views/layout_panel.py \
      | grep -c _refresh_suggestion
0
```

## 6. Out of scope

- **B11** — the trim and the eight crop boxes are missing from
  `_on_unit_changed` entirely. This spec changes the *rule* the loop
  applies; B11 changes *what is in* the loop. See
  `B11-unit-change-misses-trim-and-crop.md`. If both are being done, land
  B11 first and this spec's step 4 then covers eleven boxes rather than
  five.
- **B12** (`B12-suspend-handlers-during-refresh.md`) —
  `refresh_from_project` does not block the trim, crop or stock widgets
  before writing to them.
- **B8** — the creep advisory in `core/layout.py` reads
  `sheets_per_signature` even when `signature_lengths` decided the real
  grouping, so the imposer's warning and this panel's advice can still
  disagree about one document. Different layer; do not touch
  `core/layout.py` here.
- Adding a *weight* entry field to the panel. `set_paper_from_weight`
  exists and is tested (`tests/test_layout_panel_paper.py:71-91`) and is
  reachable only from the CLI. Wiring it into the GUI is F11-adjacent
  work and is not this.

## 7. decisions.md entry

```
## 2026-09-XX — The caliper box could not display a caliper
- Symptom: `paper_thickness_pt` is 0.2-0.6pt for every paper Deckle's own preset list can produce, and the panel gave it the same display rule as a margin: zero decimals in points and a step of one whole point. In points the box showed "0" over a stored 0.2835, and one press of the up arrow wrote 1.0 -- a sheet three times thicker than 160gsm card. The first unit change also overwrote the construction-time step of 0.001, so afterwards a single arrow press in inches moved the caliper by 0.125in, which is thirty sheets of copier paper. And typing a caliper by hand left the Paper stock dropdown naming a preset the document no longer held, and the gathering-size advice showing a sentence computed for the previous number.
- Fix: Precision and step become a function of what the box measures, not just of the unit: `decimals_for(kind, unit)` and `single_step_for(kind, unit)`, with a caliper keeping two decimals even in points and stepping by 0.05pt. Both are called from the constructor as well as from `_on_unit_changed`, so the two places cannot disagree. And `_on_paper_thickness_changed` now does what `_on_paper_stock_changed` always did -- reset the dropdown to Custom and refresh the suggestion -- because two controls write one field and only one of them was keeping the panel honest.
- Surfaces: A test over `PAPER_PRESETS` asserts that every preset's caliper is still non-zero at the points precision, so the constant is enforced rather than remembered. That is the only validation available for a display rule: had two decimals rendered 80gsm copier as 0.00, it would be wrong.
- Watch: The panel maintains the same set of controls in four hand-written lists, and this is the third defect to come out of one of them. The generic rule was not wrong when it was written -- there were no sub-point lengths on the panel. It became wrong when a caliper was added to a list of margins, and nothing in the code said the list had a precondition.
- Commit: <fill in>
```

## 8. Traps

- **`setDecimals` truncates the displayed value immediately, and
  `setRange`/`setSingleStep` do not.** Order inside the loop is range,
  decimals, step, value — set the value last or it is rounded by the old
  precision and then re-rounded by the new one.
- **The unit-change loop blocks signals; the constructor does not need
  to** (nothing is connected yet at line 862 — the `valueChanged` wiring
  is at line 1205). Do not add `blockSignals` in the constructor; it would
  read as if a handler existed.
- **`_on_paper_stock_changed` returns early on `CUSTOM_STOCK_LABEL`**
  (`layout_panel.py:1587-1588`). That early return is what makes step 5's
  combo reset harmless. If anyone removes it, the reset becomes a mutation
  and the two handlers call each other.
- **`refresh_from_project` also sets the combo to `CUSTOM_STOCK_LABEL`**
  (line 1273), unblocked, deliberately: "A thickness that came from a
  saved project has no preset behind it". Leave that alone; it is the same
  decision, and this spec is only making the *typed* path agree with it.
- **`signature_suggestion` is annotated `layout: LayoutSettings` but
  `LayoutSettings` is never imported into this module**
  (`layout_panel.py:511`, against the imports at lines 21-39). It works
  only because `from __future__ import annotations` makes the annotation a
  string. Anything that calls `typing.get_type_hints` on this module
  raises `NameError`. Do not introduce such a call; if you move these
  functions (M1 does), import `LayoutSettings` with them.
- Constructing a real `QMainWindow` under pytest kills the interpreter on
  this machine (see `tests/test_ui_surface.py:534-541`); build the
  `LayoutPanel` directly, as every existing panel test does.
