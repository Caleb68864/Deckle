# M1 — One binding table instead of four hand-maintained lists

**Roadmap item:** `docs/ROADMAP.md` M1
**Depends on:** B12 (or do B12 inside this — see §3 step 0)
**Blocks:** —
**Size:** M
**Decision needed first:** none

> **B11 and B29 are satisfied by this spec and must not be done
> separately if M1 is scheduled.** Both are "a list was updated in one
> place and not the others", and both disappear when the list stops
> existing. `B11-unit-change-misses-trim-and-crop.md` and
> `B29-thickness-decimals-and-suggestion.md` are written as standalone
> narrow fixes in case M1 slips; if M1 is going ahead, read them for
> their §4 tests (which are written to survive this refactor unchanged)
> and for B29's decimals constants, and skip their §3.
>
> **B12 must land first.** `B12-suspend-handlers-during-refresh.md`
> introduces exactly the seam this spec extends: a `_Control(widget,
> signal)` record, a `LayoutPanel._bind(widget, signal, handler)` that
> connects and registers in one call, and a `_suspend_handlers()` context
> manager that `refresh_from_project` wraps its body in. That spec says
> so in its own words — "The registry is the seam M1's binding table
> extends" — and commits to the `widget`/`signal` members while leaving
> `field`/`to_model`/`from_model` to this one. M1 **grows `_Control` into
> `_Binding`**; it does not replace the mechanism. Doing B12 after M1
> means fixing a method that no longer exists in that shape.

---

## 1. Context

`LayoutPanel` is 1,723 lines and maintains the same set of controls in
**four separate hand-written places**:

1. the constructor, where each widget is built, given a range and a
   precision, and given an initial value from the project
   (`layout_panel.py:795-1205`);
2. `refresh_from_project`'s `widgets` list, which decides whose signals
   are blocked while a freshly opened project is written into the panel
   (`layout_panel.py:1223-1231`);
3. `_on_unit_changed`'s `boxes` list, which decides whose range, decimals,
   step and displayed value are reconverted when the unit changes
   (`layout_panel.py:1430-1434`);
4. roughly twenty near-identical `_on_*_changed` handlers, each of which
   reads a widget, calls `apply_layout_change` with one `set_*` mutator,
   refreshes some subset of `{binding readout, suggestion, margin boxes}`
   and emits `layout_changed` (`layout_panel.py:1372-1723`).

Three shipped bugs are the *same* bug in three of those four lists:

- **B11** — the trim box and the eight crop boxes are absent from list 3,
  so switching units leaves them showing an inch number under a
  millimetre label, and the next crop edit rewrites all four edges of that
  parity at 1/25.4 of their measured value.
- **B12** — the trim box, the eight crop boxes and the paper-stock combo
  are absent from list 2, so opening a project fires their handlers, which
  `mutate` the state: up to nine spurious undo entries per open, and a
  cleared redo stack, so undoing a trim change leaves Ctrl+Y inert.
- **B29** — the caliper box is in list 3 but shares the geometry boxes'
  precision rule, so in points it displays a 0.28 pt sheet as `0` and
  steps by a whole point; and its handler in list 4 is one of the ones
  that forgets to refresh the suggestion, so typing a caliper leaves the
  gathering advice describing the previous paper.

None of these is a mistake in the code that was written. Each is a
control that was added correctly to two or three lists out of four. The
lists have no relationship to each other that a compiler, a linter or a
reader can check, and there are now 28 value-bearing widgets across them.

What a person printing a book sees: a crop measured off a scan silently
disappears after a unit change and the scanner's white border comes back
on all 300 pages; opening a saved project quietly destroys the redo
stack; a caliper typed by hand produces gathering advice for a paper they
are not using.

## 2. Current code

### List 2 — `refresh_from_project`, `deckle/app/views/layout_panel.py:1207-1286`

*(Quoted as it stands at `08e7f49`. B12 replaces the `widgets` list and
the two block loops with `with self._suspend_handlers():`, leaving the
twenty-six write statements inside untouched — those are what step 3b
below replaces.)*

```python
    def refresh_from_project(self) -> None:
        """Re-read every control from the current project.

        ...

        Signals are blocked throughout. Setting a widget's value fires its
        handler, and those handlers write back to the project -- so an
        unguarded refresh would overwrite the freshly loaded layout with
        whatever the widgets happened to hold, one control at a time.
        """
        layout = self.state.project.layout
        widgets = [
            self.unit_combo, self.paper_combo, self.orientation_combo,
            self.grain_combo, self.paper_thickness_spinbox, self.gutter_spinbox,
            self.slack_combo, self.link_margins_check, self.binding_edge_combo,
            self.start_on_recto_check,
            self.landscape_policy_combo, self.sheets_per_signature_spinbox,
            self.blank_mode_combo, self.sewing_stations_spinbox, self.tabs,
            *self.margin_spinboxes.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
```

Fifteen entries plus the three margin boxes. The docstring states the
invariant the list is supposed to enforce; the list itself omits
`self.trim_spinbox`, all eight `self.crop_spinboxes`, and
`self.paper_stock_combo` — every one of which is written to inside the
`try` block, at lines 1259-1273:

```python
            self.trim_spinbox.setValue(from_points(layout.trim_pt, self._unit))
            for parity, insets in (("odd", layout.crop_odd_pt),
                                   ("even", layout.crop_even_pt)):
                for index, edge in enumerate(("left", "bottom", "right", "top")):
                    self.crop_spinboxes[(parity, edge)].setValue(
                        from_points(insets[index], self._unit) if insets else 0.0
                    )
            self.signature_lengths_edit.setText(
                ",".join(str(n) for n in layout.signature_lengths)
                if layout.signature_lengths else ""
            )
            # A thickness that came from a saved project has no preset
            # behind it, so the dropdown says Custom rather than naming a
            # paper the binder may not be using.
            self.paper_stock_combo.setCurrentText(CUSTOM_STOCK_LABEL)
```

### List 3 — `_on_unit_changed`, `deckle/app/views/layout_panel.py:1420-1442`

```python
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

Five boxes out of fourteen; the caps are repeated here and in the
constructor, in points in one place and converted in the other.

### List 4 — the handlers

Twenty-five methods match `^    def _on_` (`grep -cE "^    def _on_"
deckle/app/views/layout_panel.py` → `25`). Eight of them are
indistinguishable except for the mutator. Verbatim,
`deckle/app/views/layout_panel.py:1458-1470` and `1695-1720`:

```python
    def _on_binding_edge_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_binding_edge(project, value))
        self.layout_changed.emit(plan)

    def _on_start_on_recto_toggled(self, checked: bool) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_start_on_recto(project, checked)
        )
        self.layout_changed.emit(plan)

    def _on_landscape_policy_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_landscape_policy(project, value))
        self.layout_changed.emit(plan)
```

```python
    def _on_sheets_per_signature_changed(self, value: int) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_sheets_per_signature(project, value)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_blank_mode_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_blank_mode(project, value))
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_sewing_stations_changed(self, value: int) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_sewing_stations(project, value)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_paper_thickness_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(
            self.state, lambda project: set_paper_thickness_pt(project, points)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
```

Note which of those call `_refresh_binding_readout` and which do not —
that is a fifth informal list, and it is also inconsistent:
`_on_binding_edge_changed` changes which side the gutter falls on, which
changes nothing in the readout, but neither does `_on_blank_mode_changed`
under `fold_scheme="none"`, and that one refreshes. `_refresh_suggestion`
is called by four handlers out of twenty
(`layout_panel.py:1285, 1598, 1653, 1679`).

### The connect block, `deckle/app/views/layout_panel.py:1184-1205`

```python
        self.gutter_spinbox.valueChanged.connect(self._on_gutter_changed)
        for field, box in self.margin_spinboxes.items():
            box.valueChanged.connect(
                lambda value, f=field: self._on_margin_changed(f, value)
            )
        self.link_margins_check.toggled.connect(self._on_link_margins_toggled)
        self.slack_combo.currentIndexChanged.connect(self._on_slack_to_changed)
        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)
        self.use_printer_margins_button.clicked.connect(self._on_use_printer_margins)
        self.binding_edge_combo.currentTextChanged.connect(self._on_binding_edge_changed)
        self.start_on_recto_check.toggled.connect(self._on_start_on_recto_toggled)
        self.landscape_policy_combo.currentTextChanged.connect(self._on_landscape_policy_changed)
        self.grain_combo.currentIndexChanged.connect(self._on_grain_changed)
        self.paper_combo.currentTextChanged.connect(self._on_paper_changed)
        self.orientation_combo.currentTextChanged.connect(self._on_orientation_changed)
        self.tabs.currentChanged.connect(self._on_mode_tab_changed)
        self.sheets_per_signature_spinbox.valueChanged.connect(
            self._on_sheets_per_signature_changed
        )
        self.blank_mode_combo.currentTextChanged.connect(self._on_blank_mode_changed)
        self.sewing_stations_spinbox.valueChanged.connect(self._on_sewing_stations_changed)
        self.paper_thickness_spinbox.valueChanged.connect(self._on_paper_thickness_changed)
```

Three more connections are made inline at construction and are therefore
easy to miss when reading this block: `paper_stock_combo` at line 889,
`trim_spinbox` at line 906, the crop boxes at line 918, `auto_crop_button`
at 932, `suggestion_button` at 1109, `signature_lengths_edit` at 1126,
`save_schedule_button` at 1175.

### The pure half that moves

`grep -nE "^def " deckle/app/views/layout_panel.py` gives the whole
module-level surface. Everything between line 41 and line 623 is Qt-free
(`_qt_fields_at_size_hint` at line 630 is the first Qt reference in the
file). Confirmed by reading: no function in that span imports PySide6,
constructs a widget or reads `self`.

### Call sites of the symbols this spec moves

`grep -rn "from deckle.app.views.layout_panel import\|layout_panel\."
--include="*.py" deckle tests`:

| Site | Imports |
|---|---|
| `deckle/app/main.py:20` | `LayoutPanel, recompute_plan` |
| `tests/gui_workflow.py:44` | `recompute_plan` |
| `tests/test_integration.py:33` | `apply_layout_change, set_gutter_pt` |
| `tests/test_layout_panel_paper.py:17-25` | `apply_suggested_sheets, set_paper_from_weight, set_paper_stock, set_sheets_per_signature, set_trim, signature_suggestion, signature_suggestion_text`; `recompute_plan` at `:192` |
| `tests/test_layout_panel_settings.py:15-17` | `recompute_plan, set_crop, set_signature_lengths, set_trim` |
| `tests/test_layout_panel_refresh.py:45` | `LayoutPanel` |
| `tests/test_layout_panel_widgets.py:40, 184` | `LayoutPanel`, `CUSTOM_STOCK_LABEL` |
| `tests/test_preview_fidelity.py:16, 219, 228, 236` | module import; `recompute_plan`, `set_gutter_pt` |
| `tests/test_ui_surface.py:22, 90-94, 132-175, 235-248, 333, 440-446, 514-968` | module import; `set_fold_scheme`, `set_sheets_per_signature`, `set_blank_mode`, `set_sewing_stations`, `set_paper_thickness_pt`, `recompute_plan`, `binding_readout_str`, `paper_is_landscape`, `preset_name_for`, `LayoutPanel` |
| `docs/api/app.views.layout_panel.rst` | `automodule` |

**Every one of those imports must keep working.** They are the reason
§3 requires an explicit re-export list rather than moving the names and
updating the callers: the pure functions are the panel's tested public
surface, ten test files reach for them by that path, and a rename would
turn a mechanical refactor into a diff across the suite.

### Existing tests that pin behaviour here

Run these before touching anything; they are the contract.

| Test | What it pins |
|---|---|
| `tests/test_layout_panel_refresh.py::test_refreshing_never_changes_the_document` | a refresh writes to every control and changes no field |
| `tests/test_layout_panel_refresh.py::test_refreshing_never_changes_the_document_in_any_unit[pt\|in\|mm\|cm]` | the same, across the display units — the rounding trip through the spinboxes is the likeliest way a write-back sneaks in |
| `tests/test_layout_panel_refresh.py::test_every_control_follows_the_loaded_layout` | eleven controls read back what was loaded |
| `tests/test_layout_panel_refresh.py::test_a_custom_paper_is_named_after_a_refresh` and the five around it | `_sync_paper_choices` keeps `paper_combo` and `_paper_names` in step |
| `tests/test_layout_panel_refresh.py::test_the_mode_tab_follows_the_fold_scheme_both_ways` | the tab follows `fold_scheme` in both directions |
| `tests/test_layout_panel_widgets.py::test_reopening_a_project_refreshes_every_new_control` | trim, crop and gatherings are re-displayed |
| `tests/test_layout_panel_widgets.py::test_choosing_a_settings_tab_never_changes_how_the_book_folds` and `::test_the_mode_tabs_still_set_the_fold_scheme` | the two tab widgets have different jobs |
| `tests/test_layout_panel_widgets.py::test_every_control_survives_the_split` | every named widget has a parent |
| `tests/test_layout_panel_widgets.py::test_no_settings_tab_is_taller_than_a_dozen_rows` | the row ceiling |
| `tests/test_ui_surface.py::test_selecting_the_current_mode_again_changes_nothing` | `state.project is before` — the re-entrancy guard |
| `tests/test_ui_surface.py::test_changing_paper_size_keeps_the_chosen_orientation` | paper and orientation are two widgets over one field |
| `tests/test_ui_surface.py::test_thickness_feeds_the_spine_estimate` | the caliper box is in inches by default |
| `tests/test_ui_surface.py::test_the_schedule_button_needs_both_folio_and_a_document` | `_sync_signature_tab` gating |
| `tests/test_view_workers.py`, `tests/test_preview_fidelity.py` | `recompute_plan` and `set_gutter_pt` reachable from `deckle.app.views.layout_panel` |

## 3. Change

Three seams, in this order. Each step leaves the suite green; do not
batch them.

### Step 0 — land B12 first

Follow `B12-suspend-handlers-during-refresh.md` to the letter and commit
it. When it is done the panel has:

- `_Control(widget: object, signal: str)`, a frozen dataclass above the
  `# -- Qt wiring` divider;
- `self._controls: list[_Control]`, created in `__init__` before any
  control exists;
- `LayoutPanel._bind(widget, signal, handler)`, which connects and
  registers in one call, and through which **every** value-bearing
  control is connected (buttons are not: `clicked` never fires from a
  programmatic set);
- `_suspend_handlers()`, a `@contextmanager` that blocks every registered
  control and restores each one's *prior* blocked state;
- `refresh_from_project` as `with self._suspend_handlers():` over its
  existing body;
- `test_every_connected_control_is_registered_for_suspension`, the
  structural test that a control connected outside `_bind` fails the
  suite.

Everything below builds on that. B12's tests
(`test_refreshing_leaves_the_undo_stack_untouched`,
`test_refreshing_leaves_redo_available`,
`test_undo_then_redo_works_for_a_trim_change`,
`test_undo_then_redo_works_for_a_crop_change`,
`test_refreshing_does_not_round_trip_the_crop_through_the_spinboxes`)
must stay green through every step of this spec — they are the statement
of the invariant M1 is restructuring around.

### Step 1 — `deckle/app/layout_mutators.py`

Create the module and move the following, **unchanged**, in the order
they appear today. Every one is Qt-free; verified by reading each and by
the fact that the first Qt reference in the file is at line 630.

| Symbol | Current lines | Kind |
|---|---|---|
| `set_gutter_pt` | 68-76 | function |
| `MARGIN_FIELDS` | 79-82 | constant (with its comment) |
| `set_margin` | 85-97 | function |
| `set_margins_linked` | 100-111 | function |
| `set_slack_to` | 127-135 | function |
| `LENGTH_UNITS` | 138-140 | constant (with its comment) |
| `to_points` | 143-151 | function |
| `from_points` | 154-162 | function |
| `imageable_inset_pt` | 165-183 | function |
| `set_binding_edge` | 186-196 | function |
| `PAPER_PRESETS` | 199-209 | constant (with its comment) |
| `paper_is_landscape` | 214-222 | function |
| `preset_name_for` | 225-237 | function |
| `set_paper` | 240-262 | function |
| `set_grain` | 265-275 | function |
| `set_landscape_policy` | 278-287 | function |
| `set_start_on_recto` | 290-297 | function |
| `set_fold_scheme` | 300-308 | function |
| `set_sheets_per_signature` | 311-321 | function |
| `set_blank_mode` | 324-333 | function |
| `set_sewing_stations` | 336-344 | function |
| `set_paper_thickness_pt` | 347-358 | function |
| `set_trim` | 370-383 | function |
| `set_crop` | 386-411 | function |
| `set_signature_lengths` | 414-454 | function |
| `set_paper_stock` | 457-477 | function |
| `set_paper_from_weight` | 480-508 | function |
| `signature_suggestion` | 511-524 | function |
| `signature_suggestion_text` | 527-555 | function |
| `apply_suggested_sheets` | 558-569 | function |
| `recompute_plan` | 572-585 | function |
| `binding_readout_str` | 588-604 | function |
| `apply_layout_change` | 607-622 | function |

Thirty-three symbols. **The span is not contiguous** — three UI tables
are interleaved and stay in `layout_panel.py`: `SLACK_TARGETS`
(114-125), `ORIENTATIONS` (211) and `CUSTOM_STOCK_LABEL` (361-367). So do
these stay: `BINDING_EDGES` (41), `LANDSCAPE_POLICIES` (43),
`FOLD_SCHEMES` (45), `BLANK_MODES` (47), `GRAINS` (49-55),
`SCHEDULE_TOOLTIP` (57-63). Those are combo contents and label text — the
panel's business, not the model's.

`deckle/app/layout_mutators.py` imports:

```python
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from deckle.app.state import AppState
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
from deckle.core.models import LayoutSettings, Project, SheetPlan
# Aliased on import. `PAPER_PRESETS` in this module has meant sheet
# *sizes* (A4, Letter) since it was written, and the new list is paper
# *stock* (80gsm copier). Two meanings of "paper" in one file is how a
# reader ends up wiring a dropdown to the wrong one.
from deckle.core.paper import PAPER_PRESETS as PAPER_STOCKS
from deckle.core.paper import (
    caliper_pt_from_gsm,
    gsm_from_pounds,
    suggest_sheets_per_signature,
)
```

Note `LayoutSettings` in that list: `signature_suggestion` and
`signature_suggestion_text` are annotated `layout: LayoutSettings` at
`layout_panel.py:511` and `:527` and the name has **never been imported**
into that module. It works only because `from __future__ import
annotations` makes annotations strings. Import it properly here; do not
carry the latent `NameError` across.

Module docstring — write it in the house voice; the substance it must
carry:

> The layout settings as arithmetic: one function per setting, each
> taking a `Project` and returning a new one. Qt-free on purpose, and not
> merely as a happy accident of where they were written — these are what
> `tests/test_layout_panel_paper.py` and
> `tests/test_layout_panel_settings.py` exercise directly, without a
> display, and what `LayoutPanel`'s binding table names as the effect of
> each control. Nothing here touches a widget, and nothing here decides
> what a control looks like; `deckle/app/views/layout_panel.py` owns
> that.

Then in `deckle/app/views/layout_panel.py`, replace the moved bodies with
one explicit re-export block at the top of the module, immediately after
the existing imports:

```python
# Re-exported so `deckle.app.views.layout_panel` stays the address these
# functions have had since SS-11: main.py, tests/gui_workflow.py and six
# test modules import them from here, and the panel's own handlers name
# them. The definitions live in `deckle.app.layout_mutators` because they
# are arithmetic over a Project and this module is a Qt widget.
from deckle.app.layout_mutators import (  # noqa: F401
    LENGTH_UNITS,
    MARGIN_FIELDS,
    PAPER_PRESETS,
    PAPER_STOCKS,
    apply_layout_change,
    apply_suggested_sheets,
    binding_readout_str,
    from_points,
    imageable_inset_pt,
    paper_is_landscape,
    preset_name_for,
    recompute_plan,
    set_binding_edge,
    set_blank_mode,
    set_crop,
    set_fold_scheme,
    set_grain,
    set_gutter_pt,
    set_landscape_policy,
    set_margin,
    set_margins_linked,
    set_paper,
    set_paper_from_weight,
    set_paper_stock,
    set_paper_thickness_pt,
    set_sewing_stations,
    set_sheets_per_signature,
    set_signature_lengths,
    set_slack_to,
    set_start_on_recto,
    set_trim,
    signature_suggestion,
    signature_suggestion_text,
    to_points,
)
```

`layout_panel.py` keeps `from deckle.core.paper import PT_PER_MM` (used
at line 881 for the stock combo's label) and drops its own
`PAPER_PRESETS as PAPER_STOCKS` alias and the three
`caliper_pt_from_gsm`/`gsm_from_pounds`/`suggest_sheets_per_signature`
imports, which now have no user in this file.

Add `docs/api/app.layout_mutators.rst` (copy the two-line shape of
`docs/api/app.state.rst`) and list `app.layout_mutators` in
`docs/api/app.rst`'s toctree, immediately after `app.state`.
`tests/test_docs_coverage.py` fails without both.

Commit here. The suite must be green with zero test edits.

### Step 2 — `deckle/app/views/length_spinbox.py`

One widget class that owns everything a length box needs to know about
itself, so no caller has to remember a cap, a precision or a step.

**Qt must stay lazily imported.** `layout_panel.py`, `preview_view.py`
and `print_dialog.py` all import PySide6 inside functions so the modules
stay importable without a display — the Sphinx build depends on it and
`00-environment.md` states it as a repository rule. A module-scope
`class LengthSpinBox(QDoubleSpinBox)` would break that. Chosen: an
accessor that builds the class on first call and caches it, matching
`preview_view._make_scroll_area` (`preview_view.py:420-445`), which
already builds a `QScrollArea` subclass lazily. Rejected: a module-level
`__getattr__` (PEP 562) exposing `LengthSpinBox` as a name — a symbol
that exists only sometimes is harder to grep than a function call.

```python
"""LengthSpinBox: a spin box that knows what it is measuring.

<house-voice docstring: every length on the layout panel is stored in
points and typed in one of four units, and each box needs a range, a
precision and a step that all depend on both the unit and on WHAT the box
measures. Keeping those three facts beside the widget rather than in a
list somewhere else is the whole point: the list was updated in one place
and not the others three separate times -- see docs/decisions.md.>
"""

from __future__ import annotations

#: How a length box's precision and step depend on what it measures.
#: Two classes, because a caliper and a margin differ by two orders of
#: magnitude: 0.28pt of paper against 18pt of margin.
LENGTH_KINDS: tuple[str, ...] = ("length", "thickness")


def decimals_for(kind: str, unit: str) -> int:
    """How many decimal places a box of this kind shows in ``unit``.

    :param kind: ``"length"`` for geometry (gutter, margins, trim, crop)
        or ``"thickness"`` for a caliper.
    :param unit: a key of :data:`deckle.app.layout_mutators.LENGTH_UNITS`.
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

    :returns: the step. A caliper is measured, not adjusted, so its step
        is the smallest increment the box can display -- a step that
        overshoots every real paper is worse than one that is slow.
    """
    if kind == "thickness":
        return 0.05 if unit == "pt" else (0.01 if unit == "mm" else 0.001)
    return 1.0 if unit in ("pt", "mm") else 0.125


_CLASS = None


def length_spin_box_class():
    """The ``LengthSpinBox`` class, built on first use.

    Qt is imported here rather than at module scope for the reason every
    view in this package does it: these modules must import without
    PySide6 or a display, and the documentation build does exactly that.

    :returns: the class. Cached, so every box is an instance of one type
        and ``isinstance`` means something.
    """
    global _CLASS
    if _CLASS is not None:
        return _CLASS

    from PySide6.QtWidgets import QDoubleSpinBox

    from deckle.app.layout_mutators import from_points, to_points

    class LengthSpinBox(QDoubleSpinBox):
        """A spin box holding a length in points, displayed in ``unit``.

        :param parent: the parent widget.
        :param cap_pt: the largest value the box accepts, **in points**.
            Held in points and reconverted on every unit change, which is
            what the old code could not do: each box's range was computed
            once at construction, in whatever unit happened to be selected
            then, so a trim capped at 144pt could only be typed up to 2mm.
        :param kind: ``"length"`` or ``"thickness"``; see
            :func:`decimals_for`.
        :param unit: the display unit to start in.
        """

        def __init__(self, parent=None, *, cap_pt, kind="length", unit="in"):
            super().__init__(parent)
            self._cap_pt = float(cap_pt)
            self._kind = kind
            self._unit = unit
            self._apply_unit()

        @property
        def unit(self) -> str:
            """The unit currently displayed."""
            return self._unit

        @property
        def cap_pt(self) -> float:
            """The maximum, in points, independent of the display unit."""
            return self._cap_pt

        @property
        def kind(self) -> str:
            """``"length"`` or ``"thickness"``."""
            return self._kind

        def decimals_for(self, unit: str) -> int:
            """This box's precision in ``unit``."""
            return decimals_for(self._kind, unit)

        def single_step_for(self, unit: str) -> float:
            """This box's arrow step in ``unit``."""
            return single_step_for(self._kind, unit)

        def points(self) -> float:
            """The displayed value, in points."""
            return to_points(self.value(), self._unit)

        def set_points(self, points: float) -> None:
            """Display ``points`` in the current unit."""
            self.setValue(from_points(points, self._unit))

        def set_unit(self, unit: str, *, points: float | None = None) -> None:
            """Re-display in a new unit, keeping the measurement.

            :param unit: the new display unit.
            :param points: the value to show, in points. Pass the model's
                number rather than letting the box re-derive it from its
                own display: the display is rounded to
                :func:`decimals_for` places, so re-deriving compounds that
                rounding on every switch.
            :returns: nothing. Does **not** block its own signals -- the
                caller does, because the caller knows whether this is a
                user edit or a re-display.
            """
            if points is None:
                points = self.points()
            self._unit = unit
            self._apply_unit()
            self.set_points(points)

        def _apply_unit(self) -> None:
            # Range before decimals before value: setRange clamps the
            # current value, and setDecimals rounds it, so setting the
            # value last is the only order that survives a narrowing
            # switch (mm -> in drops a crop cap from 254 to 10).
            self.setRange(0.0, from_points(self._cap_pt, self._unit))
            self.setDecimals(self.decimals_for(self._unit))
            self.setSingleStep(self.single_step_for(self._unit))

    _CLASS = LengthSpinBox
    return _CLASS


def make_length_spin_box(parent=None, *, cap_pt, kind="length", unit="in"):
    """A :class:`LengthSpinBox`. See :func:`length_spin_box_class`."""
    return length_spin_box_class()(parent, cap_pt=cap_pt, kind=kind, unit=unit)
```

Two housekeeping edits this module forces:

- Add `docs/api/app.views.length_spinbox.rst` and list it in
  `docs/api/app.rst`'s toctree, or `tests/test_docs_coverage.py` fails.
- `tests/test_integration.py::test_orphaned_views_every_view_module_is_imported_by_main`
  asserts that the basename of **every** module under `deckle/app/views/`
  appears in `deckle/app/main.py`'s source. `length_spinbox` is a widget
  the panel builds, not a view the window mounts, so it will fail. Add an
  explicit exemption to `_view_module_names` (`tests/test_integration.py:46-52`):

  ```python
  #: Modules under views/ that are not themselves views -- a reusable
  #: widget the panel builds, which MainWindow has no reason to name.
  #: Named individually rather than pattern-matched, so adding one is a
  #: deliberate act and an actual orphaned view still fails.
  _NOT_A_VIEW = frozenset({"length_spinbox"})
  ```

  Rejected alternative: naming the file `_length_spinbox.py`, which the
  existing `startswith("_")` skip already covers and would need no test
  edit — rejected because the class is public API of this package and a
  leading underscore would say otherwise.

Commit here, with the class built but not yet used. The suite must be
green.

### Step 3 — grow `_Control` into the `_Binding` table

B12 left a `_Control(widget, signal)` record above the `# -- Qt wiring`
divider and a list of them on `self._controls`. Rename it to `_Binding`
and add the fields B12 reserved for this spec. Keep `signal` a **string**
attribute name, as B12 chose, "so the record stays printable and
comparable in a test" — `_KIND_SIGNAL` below returns that name rather
than a bound signal.

Qt-free (it holds widgets but constructs none):

```python
@dataclass(frozen=True)
class _Binding:
    """One control, and everything the panel needs to know about it.

    The panel used to maintain the same set of controls in four separate
    hand-written lists -- the constructor, `refresh_from_project`'s
    block-list, `_on_unit_changed`'s box-list, and twenty near-identical
    handlers -- with no relationship between them that anything could
    check. Three shipped bugs were one control missing from one list; see
    B11, B12 and B29 in docs/ROADMAP.md. This is that set, once.

    :ivar field: the ``LayoutSettings`` field this control edits, or
        ``""`` for one that edits no single field (the paper-stock
        dropdown derives a thickness; the mode tabs set the fold scheme
        through their own index).
    :ivar widget: the control.
    :ivar kind: how to read, write and connect it -- a key of
        :data:`_KIND_READ`.
    :ivar apply: ``(project, value) -> project``; one of the
        ``layout_mutators`` setters, bound to this control's field.
    :ivar to_model: ``(widget) -> value`` in MODEL units -- points for a
        length, the stored literal for a choice. Unit conversion belongs
        to :class:`LengthSpinBox` and happens nowhere else.
    :ivar from_model: ``(layout) -> value``, in the same units.
    :ivar write: ``(widget, value) -> None``, the inverse of ``to_model``.
    :ivar signal: the signal's attribute name on the widget, e.g.
        ``"valueChanged"`` -- B12's choice, kept because a record holding
        a bound signal is neither printable nor comparable in a test.
    :ivar label: how this control names itself in an error message shown
        to the user, e.g. ``"Crop"``.
    :ivar cap_pt: the maximum in points, for length kinds only.
    :ivar after: ``(panel, plan) -> None`` run after a successful change,
        for the four controls that also have to re-display something else.
    :ivar on_error: ``(panel, exc) -> None`` for a value the user can
        correct. Default reports ``f"{label}: {exc}"`` through
        ``schedule_saved``.
    """

    field: str
    widget: object
    kind: str
    apply: Callable[[Project, object], Project]
    to_model: Callable[[object], object]
    from_model: Callable[[LayoutSettings], object]
    write: Callable[[object, object], None]
    signal: str
    label: str = ""
    cap_pt: float | None = None
    after: Callable[[object, SheetPlan], None] | None = None
    on_error: Callable[[object, Exception], None] | None = None
```

with kind tables so an ordinary row is one line:

```python
#: How each kind of control is read, written and connected. `keyed` is a
#: combo whose display labels differ from the stored values (grain, slack
#: target); `text` is one whose labels ARE the values (binding edge,
#: blank mode, landscape policy).
_KIND_READ = {
    "length":    lambda w: w.points(),
    "thickness": lambda w: w.points(),
    "int":       lambda w: w.value(),
    "bool":      lambda w: w.isChecked(),
    "text":      lambda w: w.currentText(),
    "line":      lambda w: w.text(),
}
_KIND_WRITE = {
    "length":    lambda w, v: w.set_points(v),
    "thickness": lambda w, v: w.set_points(v),
    "int":       lambda w, v: w.setValue(v),
    "bool":      lambda w, v: w.setChecked(v),
    "text":      lambda w, v: w.setCurrentText(v),
    "line":      lambda w, v: w.setText(v),
}
#: Signal attribute NAMES, matching `_Control.signal` from B12.
_KIND_SIGNAL = {
    "length":    "valueChanged",
    "thickness": "valueChanged",
    "int":       "valueChanged",
    "bool":      "toggled",
    "text":      "currentTextChanged",
    "line":      "editingFinished",
}
```

and a factory that fills the defaults:

```python
def _bind(field, widget, kind, apply, *, to_model=None, from_model=None,
          write=None, signal=None, label="", cap_pt=None, after=None,
          on_error=None) -> _Binding:
    return _Binding(
        field=field, widget=widget, kind=kind, apply=apply,
        to_model=to_model or _KIND_READ[kind],
        from_model=from_model or (lambda layout, f=field: getattr(layout, f)),
        write=write or _KIND_WRITE[kind],
        signal=signal or _KIND_SIGNAL[kind],
        label=label, cap_pt=cap_pt, after=after, on_error=on_error,
    )
```

**The table.** Built at the end of `LayoutPanel.__init__`, after every
widget exists and before anything is connected. Twenty-eight rows,
transcribed — every value-bearing control on the panel is here, and that
is the property the tests in §4 assert.

| # | Widget attribute | `field` | `kind` | `cap_pt` | `apply` | Row notes |
|---|---|---|---|---|---|---|
| 1 | `paper_combo` | `paper` | `text` | — | `set_paper(p, v, landscape=self.orientation_combo.currentText() == "Landscape")` | `to_model=lambda w: self._current_paper_pt()`; `from_model=lambda l: self._sync_paper_choices(l.paper)`; `after` = refresh nothing extra (the shared post-change block covers the readout) |
| 2 | `orientation_combo` | `paper` | `text` | — | `set_paper(p, p.layout.paper, landscape=v == "Landscape")` | `to_model=lambda w: w.currentText()`; `from_model=lambda l: "Landscape" if paper_is_landscape(l.paper) else "Portrait"` |
| 3 | `grain_combo` | `grain` | `keyed` | — | `set_grain` | keys `self._grain_keys`; see `_keyed` helper below |
| 4 | `paper_stock_combo` | `""` | `text` | — | `set_paper_stock(p, v.split("  (")[0])` | `to_model` returns `w.currentText()`; a `CUSTOM_STOCK_LABEL` value is a no-op (see step 3f); `from_model=lambda l: CUSTOM_STOCK_LABEL`; `after` re-displays `paper_thickness_spinbox` from the model |
| 5 | `paper_thickness_spinbox` | `paper_thickness_pt` | `thickness` | `10.0` | `set_paper_thickness_pt` | `after` resets `paper_stock_combo` to `CUSTOM_STOCK_LABEL` (B29) |
| 6 | `trim_spinbox` | `trim_pt` | `length` | `144.0` | `set_trim` | |
| 7 | `crop_spinboxes[("odd", "left")]` | `crop_odd_pt` | `length` | `720.0` | `set_crop(p, "odd", v)` | `to_model=lambda w: self._crop_from_boxes("odd")`; `from_model` = index 0 of `l.crop_odd_pt` or `0.0`; `label="Crop"` |
| 8 | `crop_spinboxes[("odd", "bottom")]` | `crop_odd_pt` | `length` | `720.0` | as row 7 | index 1 |
| 9 | `crop_spinboxes[("odd", "right")]` | `crop_odd_pt` | `length` | `720.0` | as row 7 | index 2 |
| 10 | `crop_spinboxes[("odd", "top")]` | `crop_odd_pt` | `length` | `720.0` | as row 7 | index 3 |
| 11 | `crop_spinboxes[("even", "left")]` | `crop_even_pt` | `length` | `720.0` | `set_crop(p, "even", v)` | index 0, `label="Crop"` |
| 12 | `crop_spinboxes[("even", "bottom")]` | `crop_even_pt` | `length` | `720.0` | as row 11 | index 1 |
| 13 | `crop_spinboxes[("even", "right")]` | `crop_even_pt` | `length` | `720.0` | as row 11 | index 2 |
| 14 | `crop_spinboxes[("even", "top")]` | `crop_even_pt` | `length` | `720.0` | as row 11 | index 3 |
| 15 | `gutter_spinbox` | `gutter_pt` | `length` | `288.0` | `set_gutter_pt` | |
| 16 | `slack_combo` | `slack_to` | `keyed` | — | `set_slack_to` | keys `self._slack_keys` |
| 17 | `link_margins_check` | `margins_linked` | `bool` | — | `set_margins_linked` | `after` = `_sync_margin_enabled()`, and when newly linked, re-apply the head margin to all three (see step 3e) |
| 18 | `margin_spinboxes["margin_top_pt"]` | `margin_top_pt` | `length` | `216.0` | `set_margin(p, "margin_top_pt", v, linked=self.link_margins_check.isChecked())` | `after` = `_refresh_margin_boxes()` when linked |
| 19 | `margin_spinboxes["margin_bottom_pt"]` | `margin_bottom_pt` | `length` | `216.0` | as row 18, own field | as row 18 |
| 20 | `margin_spinboxes["margin_outer_pt"]` | `margin_outer_pt` | `length` | `216.0` | as row 18, own field | as row 18 |
| 21 | `binding_edge_combo` | `binding_edge` | `text` | — | `set_binding_edge` | |
| 22 | `start_on_recto_check` | `start_on_recto` | `bool` | — | `set_start_on_recto` | |
| 23 | `landscape_policy_combo` | `landscape_policy` | `text` | — | `set_landscape_policy` | |
| 24 | `tabs` | `fold_scheme` | `custom` | — | `set_fold_scheme` | `to_model=lambda w: "folio" if w.currentIndex() == self._signature_tab_index else "none"`; `from_model=lambda l: self._signature_tab_index if l.fold_scheme == "folio" else self._single_tab_index`; `write=lambda w, i: w.setCurrentIndex(i)`; `signal=lambda w: w.currentChanged`; `after` = `_sync_signature_tab()`; guarded by `_syncing_mode` and a no-change early return (see step 3e) |
| 25 | `sheets_per_signature_spinbox` | `sheets_per_signature` | `int` | — | `set_sheets_per_signature` | |
| 26 | `signature_lengths_edit` | `signature_lengths` | `line` | — | `set_signature_lengths` | `from_model=lambda l: ",".join(str(n) for n in l.signature_lengths) if l.signature_lengths else ""`; `label="Gatherings"`; `on_error` also sets the edit's tooltip to `str(exc)` |
| 27 | `blank_mode_combo` | `blank_mode` | `text` | — | `set_blank_mode` | |
| 28 | `sewing_stations_spinbox` | `sewing_stations` | `int` | — | `set_sewing_stations` | |

Two kinds beyond the six in `_KIND_READ` are needed: `keyed` (rows 3 and
16) and `custom` (row 24). Add them:

```python
_KIND_READ["keyed"] = None      # supplied per row by _keyed()
_KIND_READ["custom"] = None     # supplied per row


def _keyed(widget, keys, field, apply, **kw) -> _Binding:
    """A combo whose display labels differ from the values stored.

    ``grain_combo`` shows "Long grain" and stores ``"long"``;
    ``slack_combo`` shows "Fore-edge" and stores ``"outer"``. Indexing a
    parallel key list is how both already work; this is that, once.
    """
    return _Binding(
        field=field, widget=widget, kind="keyed", apply=apply,
        to_model=lambda w: keys[w.currentIndex()],
        from_model=lambda layout, f=field: getattr(layout, f),
        write=lambda w, v: w.setCurrentIndex(keys.index(v)) if v in keys else None,
        signal="currentIndexChanged",
        **kw,
    )
```

Controls that are **deliberately not rows**, because they carry no
document value: `unit_combo` (a display setting), `auto_crop_button`,
`use_printer_margins_button`, `suggestion_button`, `save_schedule_button`
(actions), and every label. `unit_combo` is still blocked during a
refresh, as it is today, so keep it in the block list explicitly.

### Step 3, the three loops

**a. Connect** — replaces the twenty-odd `self._bind(...)` calls B12 left
in the constructor. `_bind` keeps its job (connect *and* register) and
gains an overload that takes a whole binding:

```python
    def _bind_binding(self, binding: _Binding) -> None:
        """Connect one binding's control and register it for suspension.

        :param binding: the row to wire.
        :returns: nothing.
        """
        self._bindings.append(binding)
        self._bind(
            binding.widget,
            binding.signal,
            lambda *_ignored, b=binding: self._on_binding_changed(b),
        )
```

so `self._controls` (B12's suspension registry) and `self._bindings` stay
in step by construction rather than by discipline. The constructor's
connection block becomes:

```python
        for binding in self._build_bindings():
            self._bind_binding(binding)
        self._bind(self.unit_combo, "currentTextChanged", self._on_unit_changed)
        self.use_printer_margins_button.clicked.connect(self._on_use_printer_margins)
        self.auto_crop_button.clicked.connect(self._on_auto_crop)
        self.suggestion_button.clicked.connect(self._on_apply_suggestion)
        self.save_schedule_button.clicked.connect(self._on_save_schedule_clicked)
```

The four buttons are connected directly and deliberately not registered,
per `_bind`'s docstring: `clicked` never fires from a programmatic set,
so blocking them would be inert and they have no field for the table.
`unit_combo` *is* registered (it emits on `setCurrentText`, and B12
registers it today) but is not a `_Binding` — it edits no
`LayoutSettings` field.

`*_ignored` because the kinds' signals carry different argument shapes
(`valueChanged(float)`, `toggled(bool)`, `currentTextChanged(str)`,
`currentIndexChanged(int)`, `currentChanged(int)`, `editingFinished()`).
The value is always read back from the widget through `to_model`, never
taken from the signal — which is also what makes rows 7-14 correct: a
crop edit must read all four boxes of its parity, not just the one that
fired.

**b. `refresh_from_project`** — replaces the body B12 left inside
`with self._suspend_handlers():` (twenty-six hand-written lines) with one
loop. The suspension mechanism is B12's and does not change:

```python
    def refresh_from_project(self) -> None:
        """Re-read every control from the current project.

        <keep the existing docstring's substance verbatim: needed when the
        project is REPLACED rather than edited; handlers suspended because
        setting a widget's value fires its handler and those handlers
        write back.>

        What is written is now the binding table too, not just what is
        suspended -- so a control cannot be re-displayed in one unit and
        forgotten in another. B12 made the suspension complete; this makes
        the writing complete from the same list.
        """
        layout = self.state.project.layout
        with self._suspend_handlers():
            for binding in self._bindings:
                binding.write(binding.widget, binding.from_model(layout))

        self._sync_margin_enabled()
        self._sync_signature_tab()
        self._refresh_suggestion()
        self._refresh_binding_readout(recompute_plan(self.state.project))
```

Row order matters in one place: row 1 (`paper_combo`) calls
`_sync_paper_choices`, which adds or removes the custom entry and mutates
`self._paper_names`. Keep it first, as it is today
(`layout_panel.py:1235-1237`), so row 2 (`orientation_combo`) reads a
combo that already names the right sheet.

**c. `_on_unit_changed`** — replaces `layout_panel.py:1420-1442`:

```python
    def _on_unit_changed(self, unit: str) -> None:
        """Re-display the same physical lengths in a new unit.

        <keep the existing docstring's substance.>
        """
        self._unit = unit
        layout = self.state.project.layout
        # B12's suspension, reused: it blocks every registered control and
        # restores each one's prior state, which is exactly what a
        # re-display needs. Blocking only the boxes being written would be
        # a fourth list.
        with self._suspend_handlers():
            for binding in self._bindings:
                if binding.kind not in ("length", "thickness"):
                    continue
                binding.widget.set_unit(unit, points=binding.from_model(layout))
```

Fourteen boxes, by construction. The caps and the precision rule are gone
from this method entirely: `LengthSpinBox` holds `cap_pt` in points and
derives decimals and step from its own `kind`.

`self._unit` survives as the panel's current unit, still read by
`_on_use_printer_margins` (line 1453), `_on_auto_crop` (1640) and
`_on_paper_stock_changed`'s replacement (row 4's `after`). Everything
else now goes through `LengthSpinBox.points()`/`set_points()`.

### Step 3, the shared handler

```python
    def _on_binding_changed(self, binding: _Binding) -> None:
        """Apply one control's new value and re-show everything it affects.

        The single handler that replaces twenty. The three refreshes at
        the end are unconditional on purpose: they were previously called
        by an informal subset of the handlers -- the binding readout by
        eight of twenty, the gathering suggestion by four -- and the
        subsets were wrong in both directions. Both are arithmetic over a
        plan that has just been computed anyway, so running them always
        costs nothing and removes a fifth list to keep in step.
        """
        try:
            value = binding.to_model(binding.widget)
            plan = apply_layout_change(
                self.state, lambda project: binding.apply(project, value)
            )
        except ValueError as exc:
            # A value the user can correct, in the box they are already
            # looking at: a crop that consumes the page, a mistyped
            # gathering list. Reported rather than raised -- an exception
            # out of a Qt slot is a crash with no traceback.
            if binding.on_error is not None:
                binding.on_error(self, exc)
            else:
                self.schedule_saved.emit(f"{binding.label or binding.field}: {exc}")
            return
        if binding.after is not None:
            binding.after(self, plan)
        self._refresh_suggestion()
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
```

### Step 3, the rows that keep behaviour of their own

**e. Four `after` hooks and two guards, transcribed from today's code:**

- Row 17 (`link_margins_check`): `_sync_margin_enabled()`, then — when
  the new value is `True` — adopt the head margin for all three by
  writing `margin_spinboxes["margin_top_pt"]`'s points through
  `set_margin(..., linked=True)` and calling `_refresh_margin_boxes()`.
  This is `_on_link_margins_toggled` (`layout_panel.py:1390-1401`)
  unchanged in behaviour: "linking is a visible, predictable action
  rather than a silent mode change".
- Rows 18-20 (margins): `_refresh_margin_boxes()` when
  `link_margins_check.isChecked()` — `layout_panel.py:1409-1410`.
- Row 24 (`tabs`): the `_syncing_mode` re-entrancy guard
  (`layout_panel.py:1573-1578`) moves into `to_model`'s caller. Keep the
  early return: if `_syncing_mode` is set, or the computed scheme already
  equals `self.state.project.layout.fold_scheme`, return **before**
  calling `apply_layout_change`. `tests/test_ui_surface.py::test_selecting_the_current_mode_again_changes_nothing`
  asserts `state.project is before`, i.e. object identity, so a no-op
  must not go through `mutate` at all. Implement it as a generic
  `_Binding.skip_if_unchanged: bool = False` flag set on row 24 only, or
  as an `if` inside a custom `apply` — either is fine; state which in the
  commit.
- Row 4 (`paper_stock_combo`): return without mutating when the value is
  `CUSTOM_STOCK_LABEL` (`layout_panel.py:1587-1588`), then in `after`
  re-display `paper_thickness_spinbox` from the model with signals
  blocked (`layout_panel.py:1593-1597`).
- Row 5 (`paper_thickness_spinbox`): in `after`, set
  `paper_stock_combo` to `CUSTOM_STOCK_LABEL` with signals blocked. This
  is the B29 half that has no equivalent today.

**f. Handlers that are deleted**, because the table now does their work:
`_on_gutter_changed`, `_on_slack_to_changed`, `_on_link_margins_toggled`,
`_on_margin_changed`, `_on_binding_edge_changed`,
`_on_start_on_recto_toggled`, `_on_landscape_policy_changed`,
`_on_grain_changed`, `_on_paper_changed`, `_on_orientation_changed`,
`_on_mode_tab_changed`, `_on_fold_scheme_changed`,
`_on_paper_stock_changed`, `_on_crop_changed`, `_on_trim_changed`,
`_on_signature_lengths_changed`, `_on_sheets_per_signature_changed`,
`_on_blank_mode_changed`, `_on_sewing_stations_changed`,
`_on_paper_thickness_changed`. Twenty.

**Handlers that survive**, because they are not "a control writes a
field": `_on_unit_changed`, `_on_use_printer_margins`, `_on_auto_crop`,
`_on_apply_suggestion`, `_on_save_schedule_clicked`, plus the private
helpers `_sync_margin_enabled`, `_sync_signature_tab`,
`_sync_paper_choices`, `_current_paper_pt`, `_crop_from_boxes`,
`_refresh_margin_boxes`, `_refresh_suggestion`, `_refresh_binding_readout`,
`set_document_loaded`.

`_on_auto_crop` (`layout_panel.py:1624-1645`) currently ends by calling
`self._on_crop_changed(parity)`. Point it at the corresponding binding
instead: `self._on_binding_changed(self._binding_for(("odd", "left")))`,
or keep a small `self._crop_binding = {"odd": ..., "even": ...}` map. The
same applies to `_on_apply_suggestion`, which re-displays
`sheets_per_signature_spinbox` (`layout_panel.py:1674-1678`) — write it
through row 25's `write` instead of `setValue`.

### Expected size

`deckle/app/views/layout_panel.py` is 1,723 lines today. After steps 1-3
expect roughly: `layout_mutators.py` ~600, `length_spinbox.py` ~150,
`layout_panel.py` ~800 (of which the `LayoutPanel` class is ~600, mostly
widget construction and tooltip prose, which is where it should be). The
roadmap's "~1050 → ~400 lines of Qt class" is optimistic: it does not
count the twenty-eight-row table or the tooltips, which are the panel's
actual documentation and must not be trimmed to hit a number.

## 4. Tests

### Invariants to run before touching anything

These already exist and are the contract this refactor must not break.
Record that they pass, then keep re-running them after each step:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_layout_panel_refresh.py \
  tests/test_layout_panel_widgets.py \
  tests/test_layout_panel_paper.py \
  tests/test_layout_panel_settings.py \
  tests/test_ui_surface.py \
  tests/test_preview_fidelity.py \
  tests/test_integration.py \
  -q --no-header -p no:cacheprovider
```

The load-bearing ones are named in §2's table. Two deserve calling out:

- `test_refreshing_never_changes_the_document_in_any_unit[pt|in|mm|cm]`
  is the strongest existing statement of the refresh contract, and it is
  **incomplete**: it sets only `gutter_pt`, three margins and
  `paper_thickness_pt`. Extend it with `trim_pt=13.5` and
  `crop_odd_pt=(36.0, 4.5, 0.0, 18.0)`. On the unfixed tree it then fails
  in every unit with `assert 13.5 == 13.512` (or similar) — the refresh
  writes the box, the unblocked handler reads it back through a 3-decimal
  display, and the document changes. **That failure is B12, not B11**;
  roadmap §5 attributes it to B11 and is wrong about which list is short.
  `B12-suspend-handlers-during-refresh.md` covers the same ground with
  `test_refreshing_does_not_round_trip_the_crop_through_the_spinboxes`;
  extend the parametrised test as well, in step 0, and keep both green
  thereafter.
- `test_selecting_the_current_mode_again_changes_nothing` asserts
  `state.project is before` — identity, not equality. Row 24's no-op
  early return must be before `apply_layout_change`, not inside a mutator
  that happens to return the same values.

### New tests: `tests/test_layout_panel_bindings.py`

Headed like `tests/test_layout_panel_widgets.py` (offscreen platform,
module-scoped `QApplication`, `importorskip`).

| Test | Assertion in words | Failure on the unfixed tree |
|---|---|---|
| `test_every_value_bearing_control_is_in_the_binding_table` | Walk `vars(panel)`; for every attribute that is a `QDoubleSpinBox`, `QSpinBox`, `QComboBox`, `QCheckBox` or `QLineEdit`, and for every value of `panel.crop_spinboxes` and `panel.margin_spinboxes`, assert it is in `{b.widget for b in panel._bindings}` — except `panel.unit_combo`, named in an explicit allowlist in the test with a comment saying it is a display setting. **This is the test that makes B11/B12/B29 unrepeatable.** | `AttributeError: 'LayoutPanel' object has no attribute '_bindings'` |
| `test_every_binding_names_a_real_layout_field` | For every binding with a non-empty `field`, that name is in `{f.name for f in dataclasses.fields(LayoutSettings)}`. | same |
| `test_every_layout_field_a_control_can_reach_has_a_binding` | The set of `binding.field` values covers all 20 `LayoutSettings` fields. (All twenty are reachable today: 16 before `fa7d888`, plus crop×2, trim and gatherings.) | same |
| `test_every_length_box_carries_its_cap_in_points` | For every binding of kind `length`/`thickness`, `binding.widget.cap_pt` equals the cap in the table (288/216/144/720/10), and `binding.widget.maximum()` equals `from_points(cap_pt, panel._unit)`. | same |
| `test_a_unit_change_reconverts_every_length_box` | Record each length widget's `points()`; `panel.unit_combo.setCurrentText("mm")`; assert every one is unchanged to `approx`, and that each `maximum()` is now `from_points(cap_pt, "mm")`. | same (and, with `_bindings` stubbed out, it is B11) |
| `test_the_table_and_the_suspension_registry_hold_the_same_widgets` | `{b.widget for b in panel._bindings} \| {panel.unit_combo}` equals `{c.widget for c in panel._controls}` — the two lists B12 and M1 own cannot drift, which is the only way this refactor could reintroduce B12 | `AttributeError: '_bindings'` |
| `test_the_table_and_the_refresh_agree_on_every_field` | For each binding with a `field`, set that field to a distinctive value in the model, refresh, then assert `binding.to_model(binding.widget) == binding.from_model(layout)`. Round-trips all twenty-eight controls in one loop. | `_bindings` missing |
| `test_changing_any_control_emits_exactly_one_plan` | Connect a counter to `layout_changed`; drive each binding by writing a changed value through `binding.write` **after** re-enabling signals, and assert exactly one emission per control. | `_bindings` missing |
| `test_no_handler_named_on_gutter_changed_survives` | `not hasattr(panel, "_on_gutter_changed")` — the structural statement that the twenty handlers are gone rather than merely unused. | passes trivially today (the attribute exists), so write it as the post-condition and expect it red until step 3 |

### New tests: `tests/test_length_spinbox.py`

Pure-function tests need no Qt; put them first.

| Test | Assertion |
|---|---|
| `test_a_caliper_keeps_two_decimals_in_points` | `decimals_for("thickness", "pt") == 2` |
| `test_a_caliper_keeps_four_decimals_everywhere_else` | `4` for `in`, `mm`, `cm` |
| `test_a_geometry_length_keeps_the_rule_it_had` | `0` in pt, `3` otherwise |
| `test_every_preset_caliper_is_visible_at_the_points_precision` | for every `PaperPreset`, `round(s.caliper_pt, decimals_for("thickness", "pt")) > 0` |
| `test_one_step_never_moves_a_caliper_past_the_thinnest_preset` | `single_step_for("thickness", "pt") < min(s.caliper_pt for s in PAPER_PRESETS)` |
| `test_the_module_imports_without_qt` | subprocess: `import deckle.app.views.length_spinbox` then assert no `PySide6*` in `sys.modules`, copying `tests/test_backend.py::test_backend_module_import_does_not_load_qt` |
| `test_a_box_holds_its_cap_in_points_across_a_unit_change` | build with `cap_pt=144.0, unit="in"`, `maximum() == approx(2.0)`; `set_unit("mm")`; `maximum() == approx(50.8)` |
| `test_a_box_keeps_its_measurement_across_a_unit_round_trip` | `set_points(9.0)` in inches; `set_unit("mm", points=9.0)`; `set_unit("in", points=9.0)`; `points() == approx(9.0)` |
| `test_a_thickness_box_survives_points` | `kind="thickness"`, `set_points(0.2835)`, `set_unit("pt", points=0.2835)`, `points() == approx(0.28, abs=0.005)` |

Both new modules also need a `tests/test_docs_coverage.py` pass, which is
covered by the two new `.rst` files in step 1 and step 2.

## 5. Acceptance

| Check | Command |
|---|---|
| The mutators module exists and is Qt-free | `.venv/bin/python -c "import sys, deckle.app.layout_mutators as m; assert not [n for n in sys.modules if n.startswith('PySide6')], sorted(n for n in sys.modules if n.startswith('PySide6'))"` |
| The spinbox module is Qt-free on import | `.venv/bin/python -c "import sys, deckle.app.views.length_spinbox; assert not [n for n in sys.modules if n.startswith('PySide6')]"` |
| The panel still exports every moved name | `.venv/bin/python -c "import deckle.app.views.layout_panel as p; [getattr(p, n) for n in ('set_gutter_pt','set_margin','set_margins_linked','set_slack_to','to_points','from_points','imageable_inset_pt','set_binding_edge','paper_is_landscape','preset_name_for','set_paper','set_grain','set_landscape_policy','set_start_on_recto','set_fold_scheme','set_sheets_per_signature','set_blank_mode','set_sewing_stations','set_paper_thickness_pt','set_trim','set_crop','set_signature_lengths','set_paper_stock','set_paper_from_weight','signature_suggestion','signature_suggestion_text','apply_suggested_sheets','recompute_plan','binding_readout_str','apply_layout_change','MARGIN_FIELDS','LENGTH_UNITS','PAPER_PRESETS','CUSTOM_STOCK_LABEL')]"` |
| B12's suspension survives (no hand-written block list) | `! grep -q "self.unit_combo, self.paper_combo, self.orientation_combo" deckle/app/views/layout_panel.py && grep -q "_suspend_handlers()" deckle/app/views/layout_panel.py` |
| No hand-written unit box list | `! grep -q "boxes.append((self.paper_thickness_spinbox" deckle/app/views/layout_panel.py` |
| No hand-written refresh writes | `! grep -q "self.blank_mode_combo.setCurrentText(layout.blank_mode)" deckle/app/views/layout_panel.py` |
| The twenty handlers are gone | `test $(grep -cE "^    def _on_" deckle/app/views/layout_panel.py) -le 6` |
| B12's structural test still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_panel_refresh.py -k every_connected_control -q --no-header -p no:cacheprovider` |
| The binding table exists and has 28 rows | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_panel_bindings.py -q --no-header -p no:cacheprovider` |
| The spinbox tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_length_spinbox.py -q --no-header -p no:cacheprovider` |
| Docs pages exist for both new modules | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_docs_coverage.py -q --no-header -p no:cacheprovider` |
| No view is orphaned | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_integration.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Baseline for the handler-count row, on the unfixed tree:

```
$ grep -cE "^    def _on_" deckle/app/views/layout_panel.py
25
```

## 6. Out of scope

- **B13** — the import worker has no "is this still the current worker"
  guard, and import threads are skipped by `stop_background_work`. Same
  shape of bug, different file (`views/import_view.py`).
- **B17** — moving auto-crop off the GUI thread. `_on_auto_crop` survives
  this refactor unchanged, and B17 rewrites its body. See
  `B17-off-thread-long-operations.md`; if both are scheduled, land M1
  first — B17 touches one method, M1 touches the file.
- **B15** — pushing the resolved `PrinterProfile` onto
  `LayoutPanel.profile`. It adds a `set_profile` method and touches
  nothing this spec restructures. See
  `B15-push-resolved-printer-profile.md`.
- **M5** — consolidating `LENGTH_UNITS` with `cli._UNIT_TO_PT` into
  `core/paper.py`. `LENGTH_UNITS` moves to `deckle/app/layout_mutators.py`
  here and moves again there; do not try to do both at once, and do not
  add a third copy.
- **F11** (custom paper typed in the GUI) and **N11** (the crop
  composite in-app). Both add controls; adding them *after* this is one
  table row each, which is the argument for doing this first.
### Collisions with the other specs in this directory

| Spec | What it touches in `layout_panel.py` | Order |
|---|---|---|
| `B12-suspend-handlers-during-refresh.md` | the constructor's connection block, `refresh_from_project` | **before** M1 — it is the seam M1 extends (§3 step 0) |
| `B35-miscellany.md` §2 | `_on_link_margins_toggled` and `_on_use_printer_margins` — one becomes row 17's `after` hook, the other survives as a method | B12 → B35 §2 → M1, or B12 → M1 → B35 §2 re-read against the table. B35 says the same in its own §6. |
| `B11`, `B29` | subsumed; do not do them separately if M1 is scheduled |
| `B15-push-resolved-printer-profile.md` | adds `LayoutPanel.set_profile`, a new method beside `set_document_loaded` | either order; disjoint from the table |
| `B17-off-thread-long-operations.md` | rewrites `_on_auto_crop`'s body and adds `self._worker`/`self._thread` | M1 first — `_on_auto_crop` survives M1 unchanged, and B17 then edits one method instead of racing a file rewrite |

- `docs/specs/deckle-signatures-v2/sub-spec-11-ui-surface.md` carries
  `[STRUCTURAL]` acceptance rows that grep `deckle/app/views/layout_panel.py`
  for `^def set_fold_scheme(` and friends (lines 378, 383, 384). No test
  runs them — `tests/test_spec_residue.py` covers other criteria, not
  these — so they will simply become historically inaccurate. Leave them;
  that sub-spec is a record of a completed piece of work, not a live
  contract. Do not "fix" it by keeping the definitions in the panel.

## 7. decisions.md entry

```
## 2026-09-XX — The panel kept one set of controls in four lists, and three bugs came out of it
- Symptom: `LayoutPanel` maintained the same twenty-eight controls in four hand-written places -- the constructor, `refresh_from_project`'s signal-block list, `_on_unit_changed`'s box list, and twenty near-identical `_on_*_changed` handlers -- with nothing relating them that a reader, a linter or a test could check. Three shipped bugs were the same bug in three of those lists. B11: the trim box and eight crop boxes were missing from the unit list, so switching in->mm left them showing an inch number under a millimetre label and the next crop edit rewrote all four edges at 1/25.4 of their measured value. B12: the same nine were missing from the block list, so opening a project fired their handlers, pushed nine spurious undo entries and cleared the redo stack. B29: the caliper box shared the margins' precision rule, showed a 0.28pt sheet as "0" in points and stepped by a whole point.
- Fix: One `_Binding` table -- field, widget, kind, cap, `to_model`, `from_model` -- and three loops over it, for connect, refresh and unit change. A `LengthSpinBox` that holds its cap in POINTS and derives decimals and step from what it measures, so a box's range survives a unit change instead of being computed once at construction in whatever unit happened to be selected. And the pure `set_*` mutators, `recompute_plan` and the unit conversion move to `deckle/app/layout_mutators.py`, re-exported from the panel because ten test modules and `main.py` import them from there.
- Surfaces: The shared handler refreshes the binding readout and the gathering suggestion unconditionally. Those were previously called by an informal fifth list -- the readout by eight handlers of twenty, the suggestion by four -- and both subsets were wrong in both directions. Both are arithmetic over a plan that has just been computed, so doing them always costs nothing and removes the last list. The refactor's own guard is a test that walks `vars(panel)` and asserts every spin box, combo, check box and line edit is in the table: adding a control without binding it now fails the suite, which is the only thing that would have caught any of the three bugs.
- Watch: None of the three was a mistake in the code that was written. Each control was added correctly to two or three lists out of four, by someone who had read the file. A structure that needs the same fact stated in four places will be wrong in one of them, and the number of places is the defect -- not the diligence of whoever last touched it.
- Commit: <fill in>
```

## 8. Traps

- **`refresh_from_project` must keep `paper_combo` first.**
  `_sync_paper_choices` adds or removes the "Custom (500 x 700pt)" entry
  and edits `self._paper_names` in place; `_paper_names.index(...)` is
  what selects the entry. `tests/test_layout_panel_refresh.py` has six
  tests on exactly this
  (`test_switching_between_custom_sizes_does_not_accumulate_entries`,
  `test_the_name_list_stays_in_step_with_the_combo`, …). Iterating the
  table in a different order, or in a `dict` that reorders, breaks them.
- **`blockSignals` is per widget, not per connection**, and
  `QComboBox.setCurrentText` with the value already selected emits
  nothing. That is why B12's damage is exactly nine entries and not
  twelve — do not "verify" the block list by counting emissions on a
  panel whose widgets already hold the loaded values.
- **`QDoubleSpinBox.setRange` clamps and `setDecimals` rounds.**
  `LengthSpinBox._apply_unit` sets range, then decimals, then step, and
  the value is set afterwards by `set_points`. Reordering silently
  destroys values on a narrowing switch (mm → in drops the crop cap from
  254 to 10).
- **Capture the binding by default argument.**
  `lambda *_ignored, b=binding: self._on_binding_changed(b)` — a bare
  closure over the loop variable connects every control to the *last*
  binding, which is the classic version of this refactor's own failure
  mode, and it fails silently: every control still works, and every one
  edits the same field.
- **`signal` is a string, not a bound signal** (B12's choice, so the
  record stays printable). `_bind` resolves it with
  `getattr(widget, signal)`, so a typo is an `AttributeError` at
  construction rather than a control that silently never fires — which is
  the right direction, but only if the panel is actually constructed by a
  test. `test_every_value_bearing_control_is_in_the_binding_table`
  constructs it.
- **`_syncing_mode` and `_document_loaded` are read via `getattr` with
  defaults** (`layout_panel.py:1353`, `1573`) and never initialised in
  `__init__` — M7 in the roadmap. Do not "clean that up" here; it is a
  separate change and `set_document_loaded` is called from `main.py:602`.
- **`_crop_from_boxes` returns `None` when every edge is zero**, and the
  imposer's cache key distinguishes `None` from `(0,0,0,0)`
  (`tests/test_layout_panel_widgets.py::test_zeroing_every_crop_box_removes_the_crop`).
  Rows 7-14's `to_model` must keep calling it rather than reading one box.
- **`set_crop` raises `ValueError` for an unknown parity, and
  `recompute_plan` raises for a crop that consumes the page.** The current
  handler catches `ValueError` around `apply_layout_change` and reports it
  through `schedule_saved`; `apply_layout_change` calls `recompute_plan`
  after `mutate`, so a bad crop leaves the mutation applied and the
  message shown. Preserve that: it is what
  `tests/test_layout_panel_settings.py::test_lengths_that_do_not_add_up_are_left_to_the_imposer`
  and the crop error path depend on.
- **`test_no_settings_tab_is_taller_than_a_dozen_rows`** counts
  `QFormLayout.rowCount()` per settings tab. The table changes no row
  count, but if you take the opportunity to move a control between tabs,
  that test is the ceiling.
- **A real `QMainWindow` under pytest kills the interpreter here** (exit
  127; see `tests/test_ui_surface.py:534-541` and
  `tests/gui_workflow.py:6-16`). Every panel test builds `LayoutPanel`
  directly; the whole-window path is `tests/gui_workflow.py`, run in a
  subprocess by `tests/test_gui_workflow.py`. If you break the panel's
  constructor signature, that subprocess is where it shows up as a JSON
  key that is missing rather than as an assertion.
- `python -m deckle` launches the GUI and blocks. Use
  `python -m deckle.cli` for anything headless.
