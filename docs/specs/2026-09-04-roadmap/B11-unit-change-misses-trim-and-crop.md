# B11 — Convert trim and the eight crop boxes when the unit changes

**Roadmap item:** `docs/ROADMAP.md` B11
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

> **Read this first.** B11 is satisfied *by* M1
> (`M1-layout-panel-binding-table.md`), which rewrites `_on_unit_changed`
> into a loop over a binding table that cannot omit a control. If M1 is
> scheduled, **do not do B11 separately** — it would be written and then
> immediately deleted. This file exists so B11 can ship on its own if M1
> slips, and so M1 has a written statement of the behaviour it must
> preserve. The tests in §4 are written to survive M1 unchanged.

---

## 1. Context

Every length on the layout panel is stored in PDF points and displayed in
whatever unit the *Units* dropdown is showing. Switching that dropdown is
supposed to be a pure re-display: the same physical measurement, a
different number. `LayoutPanel._on_unit_changed` says so in its own
docstring, and the panel's tooltip promises it to the user:

> "The unit every length on this tab is typed in. Values are stored in
> points regardless, so switching units re-displays the same measurement
> -- it never changes your layout." (`layout_panel.py:934-938`)

It re-displays five of the fourteen length controls. The trim depth and
the eight crop boxes are not in the list, so they keep the number they
were showing and the range they were given at construction. Switch from
inches to millimetres with a trim of 0.125 in and the box still reads
`0.125`, but `self._unit` is now `"mm"` — so the panel is stating a
0.125 mm trim over a document that holds 9 pt, and the *next* edit to any
crop box on that parity reads all four boxes back through
`to_points(value, "mm")` and writes the crop the panel is displaying
rather than the crop the document had.

That last part is the damaging half. `_crop_from_boxes` reads **all four
edges** of a parity every time **one** of them changes
(`layout_panel.py:1602-1607`), so nudging the top inset after a unit
change silently rewrites left, bottom and right too, each divided by
25.4/72 × 72 — a factor of 25.4 too small. A 0.5 in gutter crop measured
off a scan becomes 0.0197 in, the crop effectively disappears, and the
book prints with the scanner's white border back on every page.

The ranges are the second, quieter half. The trim box is constructed with
`setRange(0.0, from_points(144.0, "in"))` — a maximum of `2.0`. Switch to
millimetres and the maximum is still `2.0`, so the largest trim that can
be typed is 2 mm rather than 144 pt (50.8 mm). The crop boxes cap at
`10.0` for the same reason, which in millimetres is 10 mm instead of
720 pt (254 mm).

**Repro** (headless, from the repo root):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.app.state import AppState
from deckle.core.models import LayoutSettings, Project
from PySide6.QtWidgets import QApplication
app = QApplication([])
from deckle.app.views.layout_panel import LayoutPanel

state = AppState(Project(
    pages=[],
    layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=18.0,
                          binding_edge="left", trim_pt=9.0,
                          crop_odd_pt=(36.0, 0.0, 0.0, 0.0)),
    printer=None,
))
panel = LayoutPanel(state)
panel.refresh_from_project()
print("in  -> trim box", panel.trim_spinbox.value(),
      "crop left box", panel.crop_spinboxes[("odd", "left")].value())
panel.unit_combo.setCurrentText("mm")
print("mm  -> trim box", panel.trim_spinbox.value(),
      "crop left box", panel.crop_spinboxes[("odd", "left")].value())
print("mm  -> trim max", panel.trim_spinbox.maximum())
panel.crop_spinboxes[("odd", "top")].setValue(1.0)
print("crop after one edit:", state.project.layout.crop_odd_pt)
EOF
```

Current output:

```
in  -> trim box 0.125 crop left box 0.5
mm  -> trim box 0.125 crop left box 0.5
mm  -> trim max 2.0
crop after one edit: (1.4173228346456694, 0.0, 0.0, 2.834645669291339)
```

The left inset was 36 pt before the unit change and is 1.42 pt after it.
Nothing the user did was a crop edit to the *left* edge.

## 2. Current code

`deckle/app/views/layout_panel.py:1420-1442`, verbatim:

```python
    def _on_unit_changed(self, unit: str) -> None:
        """Re-display the same physical lengths in a new unit.

        The stored model is always points, so switching units must not
        change the layout -- only how it reads. Signals are blocked while
        the displayed numbers are rewritten, otherwise the spinboxes would
        emit and re-apply their pre-conversion values as if the user had
        typed them.
        """
        layout = self.state.project.layout
        boxes = [(self.gutter_spinbox, layout.gutter_pt, 288.0)]
        boxes += [
            (self.margin_spinboxes[f], getattr(layout, f), 216.0) for f in MARGIN_FIELDS
        ]
        boxes.append((self.paper_thickness_spinbox, layout.paper_thickness_pt, 10.0))
        self._unit = unit
        for box, points, cap_pt in boxes:
            box.blockSignals(True)
            box.setRange(0.0, from_points(cap_pt, unit))
            box.setDecimals(0 if unit == "pt" else 3)
            box.setSingleStep(1.0 if unit in ("pt", "mm") else 0.125)
            box.setValue(from_points(points, unit))
            box.blockSignals(False)
```

The method ends at line 1442.

Five boxes are covered: `gutter_spinbox`, three `margin_spinboxes`, and
`paper_thickness_spinbox`. Nine are not.

The nine that are missing are built here, each with a cap expressed in
points and converted **once**, at construction time, in the unit that
happened to be selected then (`"in"`, set at `layout_panel.py:797-798`):

`deckle/app/views/layout_panel.py:894-922`:

```python
        self.trim_spinbox = QDoubleSpinBox(self.widget)
        self.trim_spinbox.setDecimals(3)
        self.trim_spinbox.setRange(0.0, from_points(144.0, self._unit))
        self.trim_spinbox.setValue(
            from_points(state.project.layout.trim_pt, self._unit)
        )
        self.trim_spinbox.setToolTip(
            "How deep the fore-edge, head and tail will be ploughed after "
            "sewing. Draws the cut lines, and gives the gathering-size "
            "suggestion room to work with -- creep is absorbed by trimming. "
            "0 draws none."
        )
        self.trim_spinbox.valueChanged.connect(self._on_trim_changed)
        crop_form.addRow("Trim depth:", self.trim_spinbox)

        # Eight boxes rather than four: a scan's gutter swaps sides every
        # leaf, so one rectangle cannot fit both parities. Built in a loop
        # because they differ only by which field they write.
        self.crop_spinboxes: dict[tuple[str, str], object] = {}
        for parity in ("odd", "even"):
            for edge in ("left", "bottom", "right", "top"):
                box = QDoubleSpinBox(self.widget)
                box.setDecimals(3)
                box.setRange(0.0, from_points(720.0, self._unit))
                box.valueChanged.connect(
                    lambda _value, p=parity: self._on_crop_changed(p)
                )
                self.crop_spinboxes[(parity, edge)] = box
                crop_form.addRow(f"Crop {parity} {edge}:", box)
```

The reader that turns those eight boxes back into a stored crop, which is
what makes the omission destructive rather than merely cosmetic —
`deckle/app/views/layout_panel.py:1602-1622`:

```python
    def _crop_from_boxes(self, parity: str):
        values = tuple(
            to_points(self.crop_spinboxes[(parity, edge)].value(), self._unit)
            for edge in ("left", "bottom", "right", "top")
        )
        return None if not any(values) else values

    def _on_crop_changed(self, parity: str) -> None:
        insets = self._crop_from_boxes(parity)
        try:
            plan = apply_layout_change(
                self.state, lambda project: set_crop(project, parity, insets)
            )
        except ValueError as exc:
            # A crop that consumes the page is refused by the imposer with
            # a message naming both numbers. Reported rather than raised:
            # it is a number the user can correct, in the box they are
            # already looking at.
            self.schedule_saved.emit(f"Crop: {exc}")
            return
        self.layout_changed.emit(plan)
```

and the trim reader, `deckle/app/views/layout_panel.py:1647-1655`:

```python
    def _on_trim_changed(self, value: float) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_trim(project, to_points(value, self._unit))
        )
```

### Everything that reads or writes `self._unit`

Grepped (`grep -n "_unit\b" deckle/app/views/layout_panel.py`), all in
`deckle/app/views/layout_panel.py`:

| Line | Use |
|---|---|
| 798 | `self._unit = "in"` — the construction-time default |
| 864, 866 | thickness box range and value |
| 896, 898 | trim box range and value |
| 917 | crop box range |
| 943, 944 | gutter box range and value |
| 1020, 1021 | margin box range and value |
| 1245, 1247, 1252, 1259, 1264 | `refresh_from_project` re-display |
| 1373 | `_on_gutter_changed` |
| 1404 | `_on_margin_changed` |
| 1417 | `_refresh_margin_boxes` |
| 1435 | `_on_unit_changed` assignment |
| 1453, 1456 | `_on_use_printer_margins` |
| 1595 | `_on_paper_stock_changed` re-display of thickness |
| 1604 | `_crop_from_boxes` |
| 1640 | `_on_auto_crop` re-display |
| 1649 | `_on_trim_changed` |
| 1715 | `_on_paper_thickness_changed` |

Nothing outside this module reads `_unit`. `to_points` / `from_points`
are also imported by nothing else — `grep -rn "from_points\|to_points"
--include="*.py" deckle tests` returns hits only in
`deckle/app/views/layout_panel.py`.

### Existing tests that touch this code

- `tests/test_layout_panel_refresh.py::test_refreshing_never_changes_the_document_in_any_unit`
  — parametrised over `pt`/`in`/`mm`/`cm`; sets the unit combo, then loads
  a layout and refreshes. It sets **only** `gutter_pt`, three margins and
  `paper_thickness_pt`, so it passes today and does not cover this bug.
- `tests/test_layout_panel_widgets.py::test_a_crop_box_reaches_the_project`,
  `::test_zeroing_every_crop_box_removes_the_crop`,
  `::test_the_two_parities_crop_independently`,
  `::test_a_trim_changes_the_advice`,
  `::test_reopening_a_project_refreshes_every_new_control` — all run in the
  panel's default unit (`in`) and never change it.
- `tests/test_ui_surface.py::test_thickness_feeds_the_spine_estimate` —
  sets `paper_thickness_spinbox` to `0.004` "inches, the panel's unit".
- No test anywhere calls `panel.unit_combo.setCurrentText(...)` except the
  refresh test above. `grep -rn "unit_combo" tests/` confirms it: two hits,
  both in `tests/test_layout_panel_refresh.py:187`.

## 3. Change

Bring the nine missing boxes into `_on_unit_changed`'s `boxes` list, with
the caps they were constructed with. Nothing else moves; no signature
changes; no new module.

Chosen: extend the existing list in place. Rejected: teaching each
handler to remember the unit its value was typed in — that stores the same
measurement in two representations, which is the class of bug the
points-only model exists to prevent.

1. **`deckle/app/views/layout_panel.py`, `LayoutPanel._on_unit_changed`
   (currently lines 1420-1442).** Replace the three lines that build
   `boxes` (1430-1434) with a list that also carries the trim box and all
   eight crop boxes. The crop insets are stored as a 4-tuple or `None`;
   `None` displays as `0.0`. The edge order is
   `("left", "bottom", "right", "top")`, matching `set_crop`'s docstring
   and `_crop_from_boxes`, and **not** the `(left, top, right, bottom)`
   order `PrinterProfile.imageable_area_pt` uses — the two conventions
   already coexist in this codebase and the crop one is the tuple order.

   ```python
        layout = self.state.project.layout
        boxes = [(self.gutter_spinbox, layout.gutter_pt, 288.0)]
        boxes += [
            (self.margin_spinboxes[f], getattr(layout, f), 216.0) for f in MARGIN_FIELDS
        ]
        boxes.append((self.paper_thickness_spinbox, layout.paper_thickness_pt, 10.0))
        # Trim and the eight crop boxes convert too. Leaving them out did
        # not merely mis-label them: `_crop_from_boxes` reads all four
        # edges of a parity on every edit, so one nudge after a unit
        # change rewrote the other three at the wrong scale.
        boxes.append((self.trim_spinbox, layout.trim_pt, 144.0))
        for parity, insets in (("odd", layout.crop_odd_pt),
                               ("even", layout.crop_even_pt)):
            for index, edge in enumerate(("left", "bottom", "right", "top")):
                boxes.append(
                    (
                        self.crop_spinboxes[(parity, edge)],
                        insets[index] if insets else 0.0,
                        720.0,
                    )
                )
        self._unit = unit
   ```

   The `for box, points, cap_pt in boxes:` loop below is unchanged: it
   already blocks signals, sets the range from the cap, sets decimals and
   single step, sets the value, and unblocks.

2. **No change to the decimals rule in this spec.** The loop's
   `setDecimals(0 if unit == "pt" else 3)` stays exactly as it is. It is
   wrong for the thickness box, and fixing it is B29
   (`B29-thickness-decimals-and-suggestion.md`); a trim or crop at 3
   decimals is correct in every unit and at 0 decimals in points is the
   same coarseness the gutter and margins already have.

3. **No change to `refresh_from_project`.** It already re-displays trim
   and crop (`layout_panel.py:1259-1265`) using `self._unit`. What it does
   *not* do is block those boxes' signals first, which is B12 and a
   separate spec. Do not fix it here; the tests below are written so they
   do not depend on B12 being done.

## 4. Tests

New file `tests/test_layout_panel_units.py`. Header the module the way
`tests/test_layout_panel_widgets.py` does: `os.environ.setdefault(
"QT_QPA_PLATFORM", "offscreen")` before importing anything Qt, a
module-scoped `QApplication` fixture, and `pytest.importorskip("PySide6")`.

Fixture, mirroring `tests/test_layout_panel_widgets.py::panel`:

```python
@pytest.fixture
def panel(qt_app):
    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel
    from deckle.core.models import LayoutSettings, Project

    state = AppState(Project(
        pages=[],
        layout=LayoutSettings(
            paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left",
            trim_pt=9.0, crop_odd_pt=(36.0, 0.0, 0.0, 18.0),
        ),
        printer=None,
    ))
    p = LayoutPanel(state)
    p.refresh_from_project()
    return p
```

| Test | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_switching_units_reconverts_the_trim` | `panel.unit_combo.setCurrentText("mm")` | The trim box reads 3.175 (9 pt in mm), and `state.project.layout.trim_pt` is still 9.0. | `assert 0.125 == approx(3.175)` — the box was never converted. |
| `test_switching_units_reconverts_every_crop_box` | as above | `crop_spinboxes[("odd","left")].value()` is 12.7 (36 pt in mm) and `[("odd","top")]` is 6.35; every other crop box reads 0.0. | `assert 0.5 == approx(12.7)`. |
| `test_switching_units_widens_the_trim_range` | as above | `panel.trim_spinbox.maximum() == approx(from_points(144.0, "mm"))` (50.8). | `assert 2.0 == approx(50.8)` — the construction-time inch cap survived. |
| `test_switching_units_widens_the_crop_range` | as above | every crop box's `maximum()` is `approx(from_points(720.0, "mm"))` (254.0). | `assert 10.0 == approx(254.0)`. |
| `test_editing_one_crop_edge_after_a_unit_change_leaves_the_others_alone` | switch to `"mm"`, then `panel.crop_spinboxes[("odd","bottom")].setValue(5.0)` | `state.project.layout.crop_odd_pt[0]` is still `approx(36.0)` and `[3]` still `approx(18.0)`; only the bottom inset changed, to `approx(14.173)`. | Left comes back as `approx(1.417)` — the panel wrote its stale inch numbers as millimetres. This is the assertion that names the actual damage. |
| `test_a_unit_round_trip_leaves_the_document_untouched` | record `state.project.layout`, then set the unit to each of `mm`, `cm`, `pt`, `in` in turn | `state.project.layout == before` after the whole sequence. | Passes today for a trim/crop-free layout and fails once trim and crop are set, because `to_points` is applied against a `_unit` the boxes were never converted for. Run it with `trim_pt=9.0` set, as the fixture does. |
| `test_the_gutter_and_margins_still_convert` | switch to `"mm"` | gutter box reads `approx(6.35)` for an 18 pt gutter. | Passes before and after — it is the regression guard on the five boxes that already worked. |

Also add, to the existing
`tests/test_layout_panel_widgets.py`, one end-to-end assertion in the
panel's own words:

- `test_a_crop_measured_in_inches_survives_a_switch_to_millimetres`:
  set `crop_spinboxes[("odd","left")]` to `0.5` while in inches, switch to
  `mm`, assert `state.project.layout.crop_odd_pt[0] == approx(36.0)` and
  that the box now reads `approx(12.7)`. Fails on the unfixed tree at the
  box value.

**Do not** extend
`tests/test_layout_panel_refresh.py::test_refreshing_never_changes_the_document_in_any_unit`
with trim and crop in this spec. Roadmap §5 attributes that extension to
B11; it is actually a B12 test — the failure it produces is
`refresh_from_project` firing the trim and crop handlers because they are
absent from the signal-block list, and it fails identically before and
after this change. See §6.

## 5. Acceptance

Run from the repo root, with the environment from `00-environment.md`.

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_panel_units.py -q --no-header -p no:cacheprovider` |
| The trim box is registered in `_on_unit_changed` | `sed -n '/    def _on_unit_changed/,/    def _on_use_printer_margins/p' deckle/app/views/layout_panel.py \| grep -q trim_spinbox` |
| The crop boxes are registered in `_on_unit_changed` | `sed -n '/    def _on_unit_changed/,/    def _on_use_printer_margins/p' deckle/app/views/layout_panel.py \| grep -q crop_spinboxes` |
| No third unit table appeared | `test $(grep -c "72.0 / 25.4" deckle/app/views/layout_panel.py) -eq 1` |
| The whole panel suite still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_panel_refresh.py tests/test_layout_panel_widgets.py tests/test_layout_panel_paper.py tests/test_layout_panel_settings.py tests/test_ui_surface.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two known failures from `00-environment.md` and no others) |

Each of the two `sed | grep` rows currently exits 1, which is the
mechanical statement of the bug:

```
$ sed -n '/    def _on_unit_changed/,/    def _on_use_printer_margins/p' \
      deckle/app/views/layout_panel.py | grep -c trim_spinbox
0
```

## 6. Out of scope

- **B12** (`B12-suspend-handlers-during-refresh.md`) — `refresh_from_project`'s hand-maintained signal-block list
  omits the trim and crop boxes, so a refresh fires their handlers and
  pushes up to nine spurious undo entries while clearing the redo stack.
  That is a different list in a different method; fix it in its own spec.
  Roadmap §5 attributes the "include trim and crop in
  `test_refreshing_never_changes_the_document_in_any_unit`" test to B11;
  it belongs to B12 and should be written there.
- **B29** — the thickness box's decimals and the missing
  `_refresh_suggestion()` call. See `B29-thickness-decimals-and-suggestion.md`.
- **M1** — the structural fix that makes this class of omission
  impossible. If M1 is scheduled, do that instead of this.
- **M5** — consolidating `LENGTH_UNITS` with `cli._UNIT_TO_PT` into
  `core/paper.py`. Do not move `to_points`/`from_points` here.

## 7. decisions.md entry

```
## 2026-09-XX — The unit dropdown converted five lengths out of fourteen
- Symptom: Switching Units from inches to millimetres re-displayed the gutter, three margins and the paper thickness, and left the trim depth and all eight crop boxes showing their old number under a new unit. Worse than a mislabel: `_crop_from_boxes` reads all four edges of a parity on every single edit, so one nudge to the top inset after a unit change rewrote left, bottom and right at 1/25.4 of their measured value -- a 0.5in gutter crop became 0.02in and the scan's white border came back on every page. Both sets of boxes also kept the range they were given at construction, so the largest trim typeable in millimetres was 2mm rather than 144pt.
- Fix: The trim box and the eight crop boxes join `_on_unit_changed`'s `boxes` list, each with the cap in points it was constructed with -- 144.0 for the trim, 720.0 for a crop -- so the existing loop converts the range, the value and the step for all fourteen.
- Surfaces: The panel maintained the same set of controls in four separate hand-written lists -- the constructor, `refresh_from_project`'s block-list, `_on_unit_changed`'s box-list and twenty `_on_*_changed` handlers -- and this is the second of them to be found one entry short. M1 replaces all four with one binding table for exactly that reason; this fix is what that refactor has to keep true.
- Watch: The failure was silent in the direction that matters. Nothing raised, nothing warned, and the number in the box looked entirely reasonable -- a trim of 0.125 is plausible in every unit Deckle offers. A test that switches units and then EDITS something is what catches it; a test that only reads a widget back sees a number and is satisfied.
- Commit: <fill in>
```

## 8. Traps

- **`self._unit = unit` must stay above the loop.** The loop calls
  `from_points(points, unit)` with the local `unit`, but the boxes'
  `valueChanged` handlers read `self._unit`. The assignment is at line
  1435, after the `boxes` list is built (which reads points from the
  model, not from `self._unit`) and before the loop. Keep that order: move
  it above the list-building and nothing breaks; move it below the loop
  and every handler is one unit behind.
- **`layout.crop_odd_pt` / `crop_even_pt` may be `None`.** `set_crop`
  stores `None` for "no crop" and `_crop_from_boxes` returns `None` when
  every edge is zero — the imposer's cache key distinguishes `None` from
  `(0,0,0,0)`, which is what
  `tests/test_layout_panel_widgets.py::test_zeroing_every_crop_box_removes_the_crop`
  pins. Display `0.0` for `None`; never index a `None`.
- **Blocking signals is what stops the conversion writing itself back.**
  The loop's `blockSignals(True)`/`(False)` around each `setValue` is not
  optional: `_on_crop_changed` and `_on_trim_changed` both call
  `apply_layout_change`, which calls `AppState.mutate`, which pushes an
  undo snapshot and **clears the redo stack**. Adding these boxes to the
  loop without the block would turn a unit change into nine undo entries.
- **`QDoubleSpinBox.setRange` clamps the current value.** Setting the
  range before the value (which the existing loop already does) is
  required when narrowing — e.g. mm → in drops the crop cap from 254 to
  10, and a value of 200 would be clamped to 10 and then overwritten by
  the correct `from_points`. Do not reorder those two calls.
- **`setDecimals` truncates.** A stored 17.77 pt shown at 3 decimals in
  inches is `0.247`, which reads back as 17.784 pt. Unit switching itself
  never writes back (signals are blocked), so the document is safe — but
  any test that asserts an exact point value after a *user* edit must use
  `pytest.approx`.
- `python -m deckle` launches the GUI and blocks. Use
  `python -m deckle.cli` for anything headless, and the `QApplication([])`
  + `LayoutPanel(state)` form above for panel repros; a real
  `QMainWindow` under pytest kills the interpreter on this machine (see
  the comment at `tests/test_ui_surface.py:534-541`).
