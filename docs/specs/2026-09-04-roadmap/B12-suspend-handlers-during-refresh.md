# B12 — Block every control's signals during `refresh_from_project`, from a registered list

**Roadmap item:** `docs/ROADMAP.md` B12
**Depends on:** —
**Blocks:** M1 (the binding table consumes this spec's registry)
**Size:** S
**Decision needed first:** none

---

## 1. Context

`LayoutPanel.refresh_from_project` writes a value into ~29 controls. Setting a
control's value fires its `valueChanged` / `currentTextChanged` signal, and
every one of those handlers calls `apply_layout_change`, which calls
`AppState.mutate` — which pushes an undo snapshot **and clears the redo
stack**.

The method knows this and blocks signals for it. But it blocks a *hand-written
list*, and that list omits `trim_spinbox`, all eight `crop_spinboxes`,
`paper_stock_combo` and `signature_lengths_edit`. So the guard is 60%
complete, and the 40% that leaks does two visible things:

**Undo a trim or crop change and Ctrl+Y is inert.** `MainWindow.undo` calls
`_after_history_change`, which calls `layout_panel.refresh_from_project()`.
The refresh pushes the trim value back into the spinbox, the spinbox emits,
the handler mutates, and `mutate` clears the redo stack that the undo had just
filled. The user pressed Undo once and lost Redo entirely.

**Opening a project pushes up to nine spurious undo entries** — one for trim
and one per crop box — so the first Ctrl+Z after opening a saved project
undoes nothing the user did.

And a third, not in the roadmap: **the crop values are corrupted by the
round-trip.** The crop boxes display 3 decimals in inches, so a crop of 1.0pt
is shown as 0.014in and written back as 1.008pt. Opening a project mutates its
own crop rectangle.

Verified, both halves:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication; app = QApplication([])
from dataclasses import replace
from deckle.app.state import AppState
from deckle.app.views.layout_panel import LayoutPanel
from deckle.core.models import LayoutSettings, Project

def fresh():
    s = AppState(Project(pages=[], layout=LayoutSettings(
        paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left"), printer=None))
    return s, LayoutPanel(s)

# (a) undo-then-redo
s, p = fresh()
p.trim_spinbox.setValue(0.25)
s.undo()
print("can_redo before refresh:", s.can_redo)
p.refresh_from_project()
print("can_redo after  refresh:", s.can_redo)

# (b) opening a project with a trim and both crops
s, p = fresh()
s._project = replace(s.project, layout=replace(s.project.layout,
    trim_pt=9.0, crop_odd_pt=(1.0, 2.0, 3.0, 4.0), crop_even_pt=(5.0, 6.0, 7.0, 8.0)))
p.refresh_from_project()
print("undo entries pushed by one refresh:", len(s._undo_stack))
print("crop_odd  after refresh:", s.project.layout.crop_odd_pt)
print("crop_even after refresh:", s.project.layout.crop_even_pt)
EOF
```

Current output:

```
can_redo before refresh: True
can_redo after  refresh: False
undo entries pushed by one refresh: 9
crop_odd  after refresh: (1.008, 2.016, 3.024, 4.032)
crop_even after refresh: (4.968, 5.976, 6.984, 7.992)
```

Every one of those five lines is wrong. After this spec they read `True`,
`True`, `0`, `(1.0, 2.0, 3.0, 4.0)`, `(5.0, 6.0, 7.0, 8.0)`.

**This is the narrow fix.** M1 replaces the registry with a full
`_Binding(field, widget, to_model, from_model)` table that the panel iterates
for connect, refresh *and* unit change. This spec builds the registry M1 will
extend, and nothing more.

Read `M1-layout-panel-binding-table.md` §3 step 0 before starting: it names
B12 as its dependency and offers to absorb this spec rather than land it
separately. If M1 is being done in the same pass, do this spec's edits first
and keep its tests — M1's step 3 deletes the registry, and a green B12 test is
how the deletion is proved safe. If M1 is not scheduled, this spec stands
alone and `_Control` is the record M1 later extends.

## 2. Current code

`deckle/app/views/layout_panel.py:1207-1286` — the whole method. The list at
1223-1231 is the defect:

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

and the four controls it writes to that are **not** in that list:

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

— `layout_panel.py:1259`, `1260-1265`, `1266-1269`, `1273`.

The connections those four have, made in the constructor:

```python
        self.paper_stock_combo.currentTextChanged.connect(self._on_paper_stock_changed)   # :889
        self.trim_spinbox.valueChanged.connect(self._on_trim_changed)                     # :906
                box.valueChanged.connect(                                                 # :918
                    lambda _value, p=parity: self._on_crop_changed(p)
                )
        self.signature_lengths_edit.editingFinished.connect(                              # :1126
            self._on_signature_lengths_changed
        )
```

The handlers that then run — each ending in an `AppState.mutate`:

```python
    def _on_trim_changed(self, value: float) -> None:                     # :1647
        plan = apply_layout_change(
            self.state, lambda project: set_trim(project, to_points(value, self._unit))
        )
```

```python
    def _on_crop_changed(self, parity: str) -> None:                      # :1609
        insets = self._crop_from_boxes(parity)
        try:
            plan = apply_layout_change(
                self.state, lambda project: set_crop(project, parity, insets)
            )
```

and `apply_layout_change` at `layout_panel.py:607-622`:

```python
def apply_layout_change(state: AppState, mutator) -> SheetPlan:
    ...
    state.mutate(mutator)
    return recompute_plan(state.project)
```

with `AppState.mutate` at `deckle/app/state.py:249-256`:

```python
        with self._lock:
            previous = self._project
            new_project = fn(previous)
            self._undo_stack.append(previous)
            self._redo_stack.clear()
            self._project = new_project
```

`self._redo_stack.clear()` is the line that kills Redo.

### Every caller of `refresh_from_project`

`grep -rn "refresh_from_project" --include="*.py" deckle/ tests/`:

| Site | Context |
|---|---|
| `deckle/app/views/layout_panel.py:1207` | the definition |
| `deckle/app/main.py:833` | `_after_history_change` — **the undo/redo path** |
| `deckle/app/main.py:1009` | `open_project` |
| `tests/test_layout_panel_refresh.py` | 12 call sites across the file |
| `tests/test_ui_surface.py:714` | `test_reopening_a_folio_project_lands_on_the_signatures_tab` |

### Every other place that blocks signals by hand

These stay as they are — B12 does not touch them, because each is a
*handler* re-displaying a value it just wrote, not a whole-panel refresh:

| Site | Method |
|---|---|
| `layout_panel.py:1416-1418` | `_refresh_margin_boxes` |
| `layout_panel.py:1437-1442` | `_on_unit_changed` |
| `layout_panel.py:1593-1597` | `_on_paper_stock_changed` |
| `layout_panel.py:1639-1641` | `_on_auto_crop` — and note it deliberately calls `_on_crop_changed(parity)` afterwards |
| `layout_panel.py:1674-1678` | `_on_apply_suggestion` |
| `arrange_view.py:587, 604` | `ArrangeView.refresh` (a different widget) |

### Existing tests that touch this code

- `tests/test_layout_panel_refresh.py` — the whole file. In particular
  `test_refreshing_never_changes_the_document` and
  `test_refreshing_never_changes_the_document_in_any_unit` pass **today**,
  because the `LayoutSettings` they build leave `trim_pt` at `0.0` and both
  crop tuples at `None`: the setValue writes the value the widget already
  holds, Qt suppresses the signal, and no handler runs. They are green over
  the bug. Roadmap §5 says so: *"`test_refreshing_never_changes_the_document_in_any_unit`
  should include trim and crop"*.
- `tests/test_ui_surface.py:714` `test_reopening_a_folio_project_lands_on_the_signatures_tab`
- `tests/test_gui_workflow.py::test_reopening_a_project_restores_the_layout` and
  `::test_reopening_a_project_restores_the_pages_and_the_view`, via
  `tests/gui_workflow.py:223`.

## 3. Change

Two designs were possible. **Chosen:** a registry populated at connect time,
plus a `_suspend_handlers()` context manager that iterates it. **Rejected:**
`self.widget.findChildren(QWidget)` and block everything — it would also block
`self.tabs`' children and every label, it cannot be extended with the
per-field metadata M1 needs, and "every widget under this panel" is not the
same set as "every control whose signal writes to the project".

### The registry's shape

A frozen dataclass, deliberately carrying more than the widget so M1 can add
`field` / `to_model` / `from_model` fields to the *same* record type and
iterate the *same* list for refresh and unit-change:

```python
@dataclass(frozen=True)
class _Control:
    """One panel control whose signal writes back to the project.

    Registered at connect time by :meth:`LayoutPanel._bind` so that
    ``refresh_from_project`` can suspend all of them without a second,
    hand-maintained list to fall out of step -- which is exactly how trim,
    crop and paper-stock came to be omitted (B12).

    M1 extends this record with ``field``/``to_model``/``from_model`` and
    drives refresh and unit conversion from the same list; the ``widget``
    and ``signal`` members are what this spec commits to.

    :ivar widget: the Qt control.
    :ivar signal: the signal's attribute name on the widget, e.g.
        ``"valueChanged"``. Held by name rather than as the bound signal so
        the record stays printable and comparable in a test.
    """

    widget: object
    signal: str
```

Placed in `deckle/app/views/layout_panel.py`, immediately above the
`# -- Qt wiring` divider at line 625, next to the other module-level types.
`dataclass` is already imported? No — the module imports `replace` from
`dataclasses` at line 18. Extend that import to
`from dataclasses import dataclass, replace`.

### Numbered edits

1. **`layout_panel.py:18`** — change
   `from dataclasses import replace` to
   `from dataclasses import dataclass, replace`.

2. **`layout_panel.py`, just before line 625's `# -- Qt wiring` divider** —
   add the `_Control` dataclass exactly as quoted above.

3. **`layout_panel.py`, `LayoutPanel.__init__`, immediately after
   `self.state = state` (line 707)** — create the registry before any control
   exists:

   ```python
        #: Every control whose signal writes to the project, registered by
        #: `_bind`. `refresh_from_project` suspends all of them; M1 will
        #: iterate the same list for refresh and unit conversion.
        self._controls: list[_Control] = []
   ```

4. **`layout_panel.py`, `LayoutPanel`, new method** placed immediately after
   `__init__` and before `refresh_from_project`:

   ```python
    def _bind(self, widget, signal: str, handler) -> None:
        """Connect ``widget.<signal>`` to ``handler`` and register it.

        Connecting and registering in one call is the whole point: the two
        lists in this class -- what is connected, and what
        ``refresh_from_project`` blocks -- were maintained separately, and
        the blocking one silently omitted trim, the eight crop boxes,
        paper stock and the gathering list. A refresh then fired their
        handlers, which mutate, which clears the redo stack. Undo a trim
        change and Ctrl+Y did nothing.

        Only controls that emit when their value is set programmatically go
        through here. Buttons (`clicked`) never do, so they are connected
        directly and are not registered -- blocking them would be inert,
        and M1's binding table has no field for them.

        :param widget: the Qt control.
        :param signal: the signal's attribute name on ``widget``.
        :param handler: the slot to connect.
        :returns: nothing.
        """
        getattr(widget, signal).connect(handler)
        self._controls.append(_Control(widget=widget, signal=signal))
   ```

5. **`layout_panel.py:1184-1205`** — rewrite the constructor's connection
   block to go through `_bind`, keeping the order and the handlers exactly as
   they are:

   ```python
        self._bind(self.gutter_spinbox, "valueChanged", self._on_gutter_changed)
        for field, box in self.margin_spinboxes.items():
            self._bind(
                box, "valueChanged",
                lambda value, f=field: self._on_margin_changed(f, value),
            )
        self._bind(self.link_margins_check, "toggled", self._on_link_margins_toggled)
        self._bind(self.slack_combo, "currentIndexChanged", self._on_slack_to_changed)
        self._bind(self.unit_combo, "currentTextChanged", self._on_unit_changed)
        self.use_printer_margins_button.clicked.connect(self._on_use_printer_margins)
        self._bind(self.binding_edge_combo, "currentTextChanged", self._on_binding_edge_changed)
        self._bind(self.start_on_recto_check, "toggled", self._on_start_on_recto_toggled)
        self._bind(
            self.landscape_policy_combo, "currentTextChanged",
            self._on_landscape_policy_changed,
        )
        self._bind(self.grain_combo, "currentIndexChanged", self._on_grain_changed)
        self._bind(self.paper_combo, "currentTextChanged", self._on_paper_changed)
        self._bind(self.orientation_combo, "currentTextChanged", self._on_orientation_changed)
        self._bind(self.tabs, "currentChanged", self._on_mode_tab_changed)
        self._bind(
            self.sheets_per_signature_spinbox, "valueChanged",
            self._on_sheets_per_signature_changed,
        )
        self._bind(self.blank_mode_combo, "currentTextChanged", self._on_blank_mode_changed)
        self._bind(
            self.sewing_stations_spinbox, "valueChanged", self._on_sewing_stations_changed
        )
        self._bind(
            self.paper_thickness_spinbox, "valueChanged", self._on_paper_thickness_changed
        )
   ```

   `use_printer_margins_button` stays a direct `.clicked.connect` — it is a
   button, per `_bind`'s docstring.

6. **`layout_panel.py:889`** — route paper stock through `_bind`:

   ```python
        self._bind(self.paper_stock_combo, "currentTextChanged", self._on_paper_stock_changed)
   ```

   (Harmless today: `_on_paper_stock_changed` returns early on
   `CUSTOM_STOCK_LABEL`, which is what the refresh sets. That early return is
   one edit away from being deleted, and the roadmap names paper-stock as one
   of the three omissions, so it is registered.)

7. **`layout_panel.py:906`** — route trim through `_bind`:

   ```python
        self._bind(self.trim_spinbox, "valueChanged", self._on_trim_changed)
   ```

8. **`layout_panel.py:918-920`** — route each crop box through `_bind`:

   ```python
                self._bind(
                    box, "valueChanged", lambda _value, p=parity: self._on_crop_changed(p)
                )
   ```

9. **`layout_panel.py:1126-1128`** — route the gathering list through `_bind`:

   ```python
        self._bind(
            self.signature_lengths_edit, "editingFinished",
            self._on_signature_lengths_changed,
        )
   ```

   (`setText` does not emit `editingFinished`, so this one is registered for
   uniformity, not because it leaks today. Uniformity is the fix: a list with
   a judgement call in it is a list that drifts.)

10. **`layout_panel.py`, `LayoutPanel`, new method** placed immediately after
    `_bind`:

    ```python
    @contextmanager
    def _suspend_handlers(self):
        """Block every registered control's signals for the duration.

        Restores each control's PRIOR blocked state rather than unblocking
        unconditionally, so a nested suspension -- or a handler that is
        already blocking a box of its own -- is not silently un-guarded on
        the way out.

        :returns: a context manager. The controls are unblocked even if the
            body raises: `set_crop` refuses a crop that consumes the page,
            and a refresh that left every control deaf would be worse than
            the crop error.
        """
        previous = [(control.widget, control.widget.blockSignals(True))
                    for control in self._controls]
        try:
            yield
        finally:
            for widget, was_blocked in previous:
                widget.blockSignals(was_blocked)
    ```

    Add `from contextlib import contextmanager` to the module imports,
    alongside `from dataclasses import dataclass, replace` at line 18.

11. **`layout_panel.py:1222-1281`, `refresh_from_project`** — delete the
    `widgets = [...]` list, the `for widget in widgets: widget.blockSignals(True)`
    loop and the `finally:` unblocking loop, and wrap the body:

    ```python
        layout = self.state.project.layout
        with self._suspend_handlers():
            self.paper_combo.setCurrentIndex(
                self._paper_names.index(self._sync_paper_choices(layout.paper))
            )
            ...                       # every existing line, unchanged
            self.tabs.setCurrentIndex(
                self._signature_tab_index
                if layout.fold_scheme == "folio"
                else self._single_tab_index
            )

        self._sync_margin_enabled()
        self._sync_signature_tab()
        self._refresh_suggestion()
        self._refresh_binding_readout(recompute_plan(self.state.project))
    ```

    The four trailing calls stay **outside** the `with`, exactly as they are
    outside the current `try/finally`.

12. **`layout_panel.py:1216-1221`, the `refresh_from_project` docstring** —
    replace the last paragraph with:

    ```
        Signals are suspended throughout, for every control registered by
        `_bind`. Setting a widget's value fires its handler, and those
        handlers write back to the project through `AppState.mutate` --
        which pushes an undo snapshot and CLEARS THE REDO STACK. The guard
        used to be a hand-written list that omitted trim, the eight crop
        boxes, paper stock and the gathering list, so undoing a trim change
        and pressing Ctrl+Y did nothing, and opening a project pushed up to
        nine undo entries the user never made.
    ```

13. **`docs/api/`** — no new module; `_Control` lives in `layout_panel.py`,
    which already has a page. `tests/test_docs_coverage.py` stays green.

### The registered set, for review

29 controls: `unit_combo`, `paper_combo`, `orientation_combo`, `grain_combo`,
`paper_thickness_spinbox`, `paper_stock_combo`, `trim_spinbox`, the eight
`crop_spinboxes`, `gutter_spinbox`, `slack_combo`, `link_margins_check`, the
three `margin_spinboxes`, `binding_edge_combo`, `start_on_recto_check`,
`landscape_policy_combo`, `sheets_per_signature_spinbox`, `blank_mode_combo`,
`sewing_stations_spinbox`, `signature_lengths_edit`, `tabs`.

Not registered, and why: `use_printer_margins_button`, `auto_crop_button`,
`suggestion_button`, `save_schedule_button` — all `clicked`, which nothing
emits programmatically.

## 4. Tests

GUI tests, so `QT_QPA_PLATFORM=offscreen`. `LayoutPanel` constructs fine
under pytest (unlike `QMainWindow`); reuse the module-scoped `qapp` fixture
and the `_panel` / `_load` helpers already at
`tests/test_layout_panel_refresh.py:35-58`.

Add a new section to `tests/test_layout_panel_refresh.py` headed
`# -- the refresh must not touch the history ---`.

### `test_refreshing_leaves_the_undo_stack_untouched`

```python
def test_refreshing_leaves_the_undo_stack_untouched():
    """Opening a saved project pushed up to nine undo entries -- one for
    trim and one per crop box -- so the first Ctrl+Z after opening undid
    something the user never did."""
    state, panel = _panel()
    _load(state, trim_pt=9.0,
          crop_odd_pt=(1.0, 2.0, 3.0, 4.0), crop_even_pt=(5.0, 6.0, 7.0, 8.0))
    before = len(state._undo_stack)

    panel.refresh_from_project()

    assert len(state._undo_stack) == before
```

Expected failure on the unfixed tree: `AssertionError: assert 9 == 0`.

### `test_refreshing_leaves_redo_available`

```python
def test_refreshing_leaves_redo_available():
    """`AppState.mutate` clears the redo stack. `MainWindow.undo` calls
    `refresh_from_project`, so a refresh that fires one handler makes Redo
    inert the instant Undo is pressed."""
    state, panel = _panel()
    panel.trim_spinbox.setValue(0.25)
    state.undo()
    assert state.can_redo

    panel.refresh_from_project()

    assert state.can_redo, "the refresh cleared the redo stack"
```

Expected failure on the unfixed tree:
`AssertionError: the refresh cleared the redo stack`.

### `test_undo_then_redo_works_for_a_trim_change`

```python
def test_undo_then_redo_works_for_a_trim_change():
    """The user-visible symptom, driven the way MainWindow drives it:
    undo, refresh the panel, redo."""
    state, panel = _panel(trim_pt=0.0)
    panel.trim_spinbox.setValue(0.25)          # 0.25in = 18pt
    changed = state.project.layout.trim_pt
    assert changed == pytest.approx(18.0)

    state.undo()
    panel.refresh_from_project()               # what _after_history_change does
    assert state.project.layout.trim_pt == 0.0

    state.redo()
    panel.refresh_from_project()

    assert state.project.layout.trim_pt == pytest.approx(changed)
```

Expected failure on the unfixed tree: the first `refresh_from_project` mutates
and clears the redo stack, so `state.redo()` is a no-op and the last assert
fails with `AssertionError: assert 0.0 == 18.0 ± 1.8e-05`.

### `test_undo_then_redo_works_for_a_crop_change`

```python
def test_undo_then_redo_works_for_a_crop_change():
    state, panel = _panel()
    panel.crop_spinboxes[("odd", "left")].setValue(0.5)   # 36pt
    changed = state.project.layout.crop_odd_pt
    assert changed is not None

    state.undo()
    panel.refresh_from_project()
    assert state.project.layout.crop_odd_pt is None

    state.redo()
    panel.refresh_from_project()

    assert state.project.layout.crop_odd_pt == changed
```

Expected failure on the unfixed tree: same shape —
`AssertionError: assert None == (36.0, 0.0, 0.0, 0.0)`.

### `test_refreshing_does_not_round_trip_the_crop_through_the_spinboxes`

```python
def test_refreshing_does_not_round_trip_the_crop_through_the_spinboxes():
    """Not in the roadmap, found while reproducing B12: the crop boxes show
    three decimals of an inch, so 1.0pt displays as 0.014in and is written
    back as 1.008pt. Opening a project rewrote its own crop rectangle."""
    state, panel = _panel()
    _load(state, crop_odd_pt=(1.0, 2.0, 3.0, 4.0), crop_even_pt=(5.0, 6.0, 7.0, 8.0))

    panel.refresh_from_project()

    assert state.project.layout.crop_odd_pt == (1.0, 2.0, 3.0, 4.0)
    assert state.project.layout.crop_even_pt == (5.0, 6.0, 7.0, 8.0)
```

Expected failure on the unfixed tree:
`AssertionError: assert (1.008, 2.016, 3.024, 4.032) == (1.0, 2.0, 3.0, 4.0)`.

### `test_every_connected_control_is_registered_for_suspension`

```python
def test_every_connected_control_is_registered_for_suspension():
    """The structural assertion. A control connected without `_bind` is
    exactly the defect: the panel maintained two lists and one of them was
    incomplete."""
    import ast
    from pathlib import Path

    from deckle.app.views import layout_panel as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "connect"):
            continue
        signal = func.value
        if not isinstance(signal, ast.Attribute):
            continue
        # `clicked` is a button; nothing emits it by setting a value.
        if signal.attr in ("clicked", "customContextMenuRequested"):
            continue
        offenders.append((signal.attr, node.lineno))

    assert offenders == [], (
        f"controls connected without _bind, so refresh cannot suspend them: "
        f"{offenders}"
    )
```

Expected failure on the unfixed tree: a non-empty list naming
`('currentTextChanged', 889)`, `('valueChanged', 906)`, `('valueChanged', 918)`,
`('editingFinished', 1126)` and every line in the 1184-1205 block.

### `test_suspend_handlers_restores_a_prior_block`

```python
def test_suspend_handlers_restores_a_prior_block():
    """Restoring the PRIOR state, not unconditionally unblocking -- a
    handler that is mid-blockSignals must not come out of a nested refresh
    with its guard silently removed."""
    _state, panel = _panel()
    panel.gutter_spinbox.blockSignals(True)

    with panel._suspend_handlers():
        pass

    assert panel.gutter_spinbox.signalsBlocked() is True
```

Expected failure on the unfixed tree: `AttributeError: 'LayoutPanel' object
has no attribute '_suspend_handlers'`.

### Existing tests to strengthen deliberately

`tests/test_layout_panel_refresh.py::test_refreshing_never_changes_the_document`
and `::test_refreshing_never_changes_the_document_in_any_unit` currently build
a `LayoutSettings` with `trim_pt` and both crops at their defaults, which is
why they pass over the bug. Per roadmap §5, extend both `loaded = ...`
constructions with:

```python
        trim_pt=13.5, crop_odd_pt=(1.0, 2.0, 3.0, 4.0),
        crop_even_pt=(5.0, 6.0, 7.0, 8.0), signature_lengths=(7, 7, 6),
```

Both then fail on the unfixed tree with
`AssertionError: refreshing the panel overwrote crop_odd_pt`, which is the
right reason. Verified: the strengthened `test_refreshing_never_changes_the_document`
reports exactly three overwritten fields —
`crop_odd_pt (1.0, 2.0, 3.0, 4.0) -> (1.008, 2.016, 3.024, 4.032)`,
`crop_even_pt (5.0, 6.0, 7.0, 8.0) -> (4.968, 5.976, 6.984, 7.992)`,
`trim_pt 13.5 -> 13.536` — and 9 undo entries. `signature_lengths` is
unchanged, as expected: `setText` does not emit `editingFinished`.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new history tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_panel_refresh.py -k "undo or redo or registered or suspend or round_trip"` |
| The whole refresh file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_panel_refresh.py` |
| Panel and UI-surface suites pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_panel_settings.py tests/test_layout_panel_paper.py tests/test_layout_panel_widgets.py tests/test_ui_surface.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| The hand-written block list is gone | `! grep -n "widget.blockSignals(True)" deckle/app/views/layout_panel.py` — currently hits line 1233; must find nothing |
| `refresh_from_project` uses the registry | `grep -q "_suspend_handlers()" deckle/app/views/layout_panel.py` |
| No control is connected outside `_bind` | covered by `test_every_connected_control_is_registered_for_suspension` above |
| Repro is clean | paste the `python - <<'EOF'` block from §1; expect `True / True / 0 / (1.0, 2.0, 3.0, 4.0) / (5.0, 6.0, 7.0, 8.0)` |

## 6. Out of scope

- **M1 — the binding table.** Do not add `field` / `to_model` / `from_model`
  to `_Control`, do not move `refresh_from_project`'s body into a loop over
  the registry, do not extract the pure mutators to
  `app/layout_mutators.py`, and do not build `LengthSpinBox`. This spec's
  contract to M1 is: the list exists, it is complete, and every entry knows
  its widget and its signal name.
- **B11 — unit change misses trim and crop.** `_on_unit_changed` (line 1420)
  still converts only gutter, the three margins and thickness. Leave it.
  B12's `test_refreshing_never_changes_the_document_in_any_unit` extension
  will pass regardless, because the refresh reads the model, not the box.
- **B29 — thickness decimals in `pt`, and the stale stock combo.** Leave
  `_on_paper_thickness_changed` and `_on_paper_stock_changed` alone.
- **B35 §2 — Link margins / Use printer margins pushing 2-3 undo entries.**
  Same file, different defect. See `B35-miscellany.md`.
- Do not change any handler's behaviour. Every `_on_*_changed` body is
  untouched by this spec; only how they are connected changes.

## 7. decisions.md entry

```
## 2026-09-05 — refresh_from_project blocked a hand-written list, so undo lost its redo
- Symptom: `LayoutPanel.refresh_from_project` writes ~29 controls and blocked signals on a list of 18, omitting trim, the eight crop boxes, paper stock and the gathering list. Setting those fires their handlers, which `mutate`, which clears the redo stack. Undo a trim change and Ctrl+Y did nothing; opening a saved project pushed nine undo entries the user never made; and the crop rectangle was corrupted on the way through, 1.0pt coming back as 1.008pt because the boxes show three decimals of an inch.
- Fix: A `_bind(widget, signal, handler)` helper that connects and registers in one call, a `_Control(widget, signal)` record, and a `_suspend_handlers()` context manager that blocks every registered control and restores each one's PRIOR blocked state. `refresh_from_project` is now `with self._suspend_handlers():` over its existing body. The registry is the seam M1's binding table extends.
- Surfaces: Any class that maintains "what is connected" and "what to block" as two lists. This one had four such lists -- constructor, refresh block-list, `_on_unit_changed` box-list, and twenty near-identical handlers -- which is M1, and B11/B12/B29 are all the same shape.
- Watch: `test_refreshing_never_changes_the_document` was green over this the whole time, because the `LayoutSettings` it builds leaves trim at 0.0 and both crops at None -- Qt suppresses `setValue` to the value already held, so no handler ran. A "never changes anything" test proves nothing unless every field it covers is non-default.
- Commit: <fill in>
```

## 8. Traps

- **`blockSignals` returns the previous state.** `_suspend_handlers` must
  capture and restore it, not call `blockSignals(False)`. `_on_auto_crop`
  blocks a crop box, sets it, unblocks it, and then calls
  `_on_crop_changed(parity)` on purpose — a nested unconditional unblock
  would break that deliberate sequence.
- **`_on_crop_changed` can raise `ValueError`** (via `set_crop`, for a crop
  that consumes the page) and catches it itself; but `refresh_from_project`
  also calls `recompute_plan`, which can raise. The `finally:` in
  `_suspend_handlers` is what keeps the panel from being left permanently
  deaf. Do not replace it with a plain `yield`.
- **`self.tabs` is registered, and `_on_mode_tab_changed` is already guarded**
  by `_syncing_mode` (line 1573). Both guards stay; they cover different
  entry points (`_sync_signature_tab` sets the tab outside the refresh).
- **`_bind` is called from the constructor at four points before line 1184**
  (paper stock 889, trim 906, crop 918, gathering 1126). `self._controls`
  must therefore be created at line 707, before any of them — not down at
  1184.
- **`tests/test_ui_surface.py` reads `main.py` as source text** and
  `test_gui_workflow.py` runs the real window in a subprocess. Neither reads
  `layout_panel.py` structurally today, but the new
  `test_every_connected_control_is_registered_for_suspension` does — an AST
  test breaks on a formatting change that a behavioural one would not. Keep
  the `connect` calls as attribute calls, not aliased locals.
- **A real `QMainWindow` cannot be constructed under pytest here** (exit 127
  with the offscreen platform; see `tests/test_ui_surface.py:537-541`). All
  tests in this spec build a bare `LayoutPanel`, which is fine. The
  end-to-end path through `MainWindow.undo` is covered by
  `tests/gui_workflow.py` in a subprocess — do not add a `MainWindow()` to
  `tests/test_layout_panel_refresh.py`.
- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` or a `python - <<'EOF'` script.
</content>
