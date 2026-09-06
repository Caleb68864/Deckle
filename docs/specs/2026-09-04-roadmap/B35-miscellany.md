# B35 — Seven small defects, each a complete fix

**Roadmap item:** `docs/ROADMAP.md` B35
**Depends on:** B9 (§4 uses the `project_path` property it introduces); B12 (§2 edits the same file)
**Blocks:** B10/N9 (§3 is in the code path B10's Recover prompt rewrites)
**Size:** S each, seven of them

---

## 1. Context

B35 is the roadmap's miscellany row: seven unrelated defects, each too small
for its own spec and each with a real cost. They are grouped here because they
are all "S", not because they interact. **Every sub-section below is a
complete mini-spec** — its own current code, its own edit, its own test — and
they can be landed in any order, in one commit or seven.

| § | Defect | File |
|---|---|---|
| 1 | The thumbnail grid keeps every raw RGBA buffer that nothing reads (~145 MB for 300 pages, measured) | `app/views/arrange_view.py` |
| 2 | Link margins pushes 2 undo entries per click; Use printer margins pushes 3 | `app/views/layout_panel.py` |
| 3 | Accepting autosave recovery re-prompts on every subsequent open | `app/main.py` |
| 4 | A replaced `AppState` leaves a live debounce timer racing the new one | `app/main.py`, `app/state.py` |
| 5 | `deckle schedule` hashes the whole document before checking the output path | `cli.py` |
| 6 | Four `assert`-based invariants in the imposer vanish under `python -O` | `core/layout.py` |
| 7 | `sewing_stations` on a sheet shorter than twice the margin marches backwards | `core/marks.py` |

---

## §1 — The thumbnail grid keeps every raw RGBA buffer

### 1.1 Context

`ArrangeView._on_thumbnails_ready` stores the whole `RenderedPage` — including
its tightly-packed RGBA bytes — in the list item's `UserRole`, *and* builds a
`QIcon` from it. The comment says the buffer is kept "for anything that wants
the pixels". **Nothing wants them:** there is no reader of
`item.data(0x0100)` anywhere in `deckle/` or `tests/`.

Items are rebuilt (and their data dropped) by `refresh()`, but **not** by
scrolling — `_on_scrolled` only requests a new window and stamps data onto the
items in it. So scrolling through a long document accumulates one buffer per
page visited, for the life of the current item list.

Measured on `tests/fixtures/sample.pdf` at the default 36 dpi:

```bash
.venv/bin/python - <<'EOF'
from deckle.core.render import thumbnails
from deckle.core.models import SourcePage, SourceRef
FIX = "tests/fixtures/sample.pdf"
pages = [SourcePage(ref=SourceRef(path=FIX, page_index=i, sha256="a"*64,
                                  width_pt=612.0, height_pt=792.0),
                    rotate_deg=0, skipped=False) for i in range(2)]
r = thumbnails(pages, 0, 2)
print(f"{r[0].width}x{r[0].height} rgba bytes = {len(r[0].rgba):,}")
print("300 pages ~", f"{len(r[0].rgba)*300/1e6:.0f} MB")
EOF
```

Output:

```
306x396 rgba bytes = 484,704
300 pages ~ 145 MB
```

145 MB of dead bytes for scrolling a 300-page scan, on top of the `QPixmap`
that is actually drawn.

### 1.2 Current code

`deckle/app/views/arrange_view.py:632-648`:

```python
    def _on_thumbnails_ready(self, worker: ThumbnailWorker) -> None:
        # Ignore a superseded fetch: stale thumbnails must not land on top
        # of the window the user actually scrolled to.
        if worker is not self._worker or worker.cancel.is_set():
            return
        for offset, rendered in enumerate(worker.rendered):
            index = worker.start + offset
            if index < self.list_widget.count() and rendered.width and rendered.height:
                item = self.list_widget.item(index)
                if item is not None:
                    # Keep the raw buffer for anything that wants the pixels,
                    # and ALSO put it on screen. Storing it in UserRole and
                    # stopping there is what the grid did for months: every
                    # thumbnail rendered correctly and went nowhere, so the
                    # page list showed nothing but text labels.
                    item.setData(0x0100, rendered)  # Qt.ItemDataRole.UserRole
                    item.setIcon(_icon_from_rendered(rendered))
```

`deckle/app/views/arrange_view.py:319-339` — the icon already owns its own
copy of the pixels, so nothing downstream depends on the stored buffer:

```python
def _icon_from_rendered(rendered: RenderedPage):
    """A ``QIcon`` from a rendered page's raw RGBA bytes.
    ...
    ``QImage`` does not copy the buffer it is handed, so the ``.copy()``
    matters: ``rendered.rgba`` is a Python ``bytes`` owned by a worker
    thread's result, and painting from freed memory is the kind of bug that
    shows as intermittent garbage rather than a crash.
    """
    from PySide6.QtGui import QIcon, QImage, QPixmap

    image = QImage(
        rendered.rgba,
        rendered.width,
        rendered.height,
        rendered.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    return QIcon(QPixmap.fromImage(image))
```

Call sites — `grep -rn "setData\|\.data(" --include="*.py" deckle/app/ tests/`:

| Site | Kind |
|---|---|
| `arrange_view.py:601` | `item.setData(_page_index_role(), i)` — the page index, **read back after a drop; keep it** |
| `arrange_view.py:647` | `item.setData(0x0100, rendered)` — **the defect** |

There is no `item.data(...)` call anywhere. Tests that touch this code:
`tests/gui_workflow.py:89-100` (drives `_on_thumbnails_ready` and counts
icons) and `tests/test_gui_workflow.py::test_thumbnails_actually_reach_the_screen`.

### 1.3 Change

1. **`arrange_view.py:640-648`** — drop the `setData` line and rewrite the
   comment so the next reader knows why it is gone:

   ```python
                item = self.list_widget.item(index)
                if item is not None:
                    # The icon owns its own copy of the pixels (`.copy()` in
                    # `_icon_from_rendered`), so the raw buffer has no
                    # second reader -- and there never was one. Keeping it
                    # in UserRole cost 485 KB per page, ~145 MB for a
                    # 300-page scan scrolled end to end, because scrolling
                    # stamps data onto items without rebuilding them.
                    item.setIcon(_icon_from_rendered(rendered))
   ```

   `item.setData(_page_index_role(), i)` at line 601 stays: it is the page's
   identity, read back after a drop.

### 1.4 Tests

`ArrangeView` constructs fine under pytest. Add to
`tests/test_arrange_reorder.py`, which already has a module-scoped `qapp`
fixture (line 42), sets `QT_QPA_PLATFORM=offscreen` (line 34), and provides
`_pages(n)` (line 50) and `_view(n) -> (state, ArrangeView)` (line 64). `_view`
builds its pages from `tests/fixtures/sample.pdf`, so `ThumbnailWorker.run()`
rasterises for real.

```python
def test_a_thumbnail_leaves_no_raw_buffer_on_the_item():
    """485 KB per page, ~145 MB for a 300-page scan scrolled end to end,
    for a buffer nothing ever read."""
    from deckle.app.views.arrange_view import ThumbnailWorker

    state, view = _view(2)
    worker = ThumbnailWorker(state.project.pages, 0, 2)
    worker.run()
    assert worker.failed is False and worker.rendered, "the fixture did not render"
    view._worker = worker

    view._on_thumbnails_ready(worker)

    item = view.list_widget.item(0)
    assert not item.icon().isNull(), "the icon must still be painted"
    assert item.data(0x0100) is None, "the raw RGBA buffer is still held"
```

Assertion in words: the icon reaches the screen and no `RenderedPage` is
retained on the item.
Expected failure on the unfixed tree: `AssertionError: the raw RGBA buffer is
still held`.

A structural guard, in the same file:

```python
def test_nothing_reads_the_thumbnail_user_role():
    """If a reader is ever added, this fails and the buffer can come back
    deliberately rather than by accident."""
    from pathlib import Path

    from deckle.app.views import arrange_view as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "item.setData(0x0100" not in source
```

Expected failure on the unfixed tree: `AssertionError`.

`tests/test_gui_workflow.py::test_thumbnails_actually_reach_the_screen` must
keep passing unchanged — it counts icons, not item data.

---

## §2 — Link margins and Use printer margins push several undo entries per click

### 2.1 Context

One click, several steps of undo. Pressing Ctrl+Z after ticking **Link all
three** appears to do nothing: it undoes the second of two mutations and
leaves the visible state where it was, so the user presses it again, and the
second press undoes something else entirely.

Verified — the exact counts:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication; app = QApplication([])
from deckle.app.state import AppState
from deckle.app.views.layout_panel import LayoutPanel
from deckle.core.models import LayoutSettings, Project
from deckle.core.profiles import BUILTIN_PRESETS

def fresh(**kw):
    base = dict(paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left"); base.update(kw)
    s = AppState(Project(pages=[], layout=LayoutSettings(**base), printer=None))
    return s, LayoutPanel(s, profile=next(iter(BUILTIN_PRESETS.values())))

s, p = fresh(margins_linked=False, margin_top_pt=10.0, margin_bottom_pt=20.0, margin_outer_pt=30.0)
n = len(s._undo_stack); p.link_margins_check.setChecked(True)
print("link margins ON  ->", len(s._undo_stack) - n, "undo entries")

s, p = fresh(margins_linked=True, margin_top_pt=10.0)
n = len(s._undo_stack); p.link_margins_check.setChecked(False)
print("link margins OFF ->", len(s._undo_stack) - n, "undo entries")

s, p = fresh(margins_linked=False, margin_top_pt=1.0, margin_bottom_pt=2.0, margin_outer_pt=3.0)
n = len(s._undo_stack); p._on_use_printer_margins()
print("use printer margins (unlinked) ->", len(s._undo_stack) - n, "undo entries")

s, p = fresh(margins_linked=True, margin_top_pt=1.0)
n = len(s._undo_stack); p._on_use_printer_margins()
print("use printer margins (linked)   ->", len(s._undo_stack) - n, "undo entries")
EOF
```

Current output:

```
link margins ON  -> 2 undo entries
link margins OFF -> 1 undo entries
use printer margins (unlinked) -> 3 undo entries
use printer margins (linked)   -> 1 undo entries
```

Expected after this section: `1 / 1 / 1 / 1`.

### 2.2 Current code

`deckle/app/views/layout_panel.py:1390-1411`:

```python
    def _on_link_margins_toggled(self, linked: bool) -> None:
        self._sync_margin_enabled()
        plan = apply_layout_change(
            self.state, lambda project: set_margins_linked(project, linked)
        )
        if linked:
            # Adopt the head margin for all three, so linking is a visible,
            # predictable action rather than a silent mode change.
            head = self.margin_spinboxes["margin_top_pt"].value()
            self._on_margin_changed("margin_top_pt", head)
            return
        self.layout_changed.emit(plan)

    def _on_margin_changed(self, field: str, value: float) -> None:
        points = to_points(value, self._unit)
        linked = self.link_margins_check.isChecked()
        plan = apply_layout_change(
            self.state, lambda project: set_margin(project, field, points, linked=linked)
        )
        if linked:
            self._refresh_margin_boxes()
        self.layout_changed.emit(plan)
```

`deckle/app/views/layout_panel.py:1444-1456`:

```python
    def _on_use_printer_margins(self) -> None:
        """Set the margin to the active printer's non-printable inset."""
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        # Set every margin, regardless of link state -- the printer's dead
        # border applies to all four edges, so a partial application would
        # leave some edge still unprintable.
        self.margin_spinboxes["margin_top_pt"].setValue(from_points(inset, self._unit))
        if not self.link_margins_check.isChecked():
            for field in ("margin_bottom_pt", "margin_outer_pt"):
                self.margin_spinboxes[field].setValue(from_points(inset, self._unit))
```

Each `setValue` fires `valueChanged` → `_on_margin_changed` →
`apply_layout_change` → `AppState.mutate`. Three boxes, three mutations.

The pure mutators, `layout_panel.py:85-125`:

```python
def set_margin(project: Project, field: str, points: float, *, linked: bool = False) -> Project:
```

```python
def set_margins_linked(project: Project, linked: bool) -> Project:
```

`AppState.mutate`, `deckle/app/state.py:249-256` — one snapshot per call:

```python
        with self._lock:
            previous = self._project
            new_project = fn(previous)
            self._undo_stack.append(previous)
            self._redo_stack.clear()
            self._project = new_project
```

Call sites — `grep -rn "set_margins_linked\|set_margin\b\|_on_use_printer_margins\|_on_link_margins_toggled" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `layout_panel.py:85` / `:100` | definitions of `set_margin` / `set_margins_linked` |
| `layout_panel.py:1393` | `set_margins_linked` in `_on_link_margins_toggled` |
| `layout_panel.py:1407` | `set_margin` in `_on_margin_changed` |
| `layout_panel.py:1189` | `link_margins_check.toggled` → `_on_link_margins_toggled` |
| `layout_panel.py:1192` | `use_printer_margins_button.clicked` → `_on_use_printer_margins` |
| `tests/test_layout_panel_settings.py` | exercises both mutators directly |
| `tests/test_ui_surface.py:86` | `test_binding_mutators_return_new_projects` |

Tests that touch it: `tests/test_layout_panel_settings.py`,
`tests/test_layout_panel_widgets.py`, `tests/test_ui_surface.py:86`.

### 2.3 Change

Compose the mutation, then apply it once. Two designs were possible.
**Chosen:** a composed mutator passed to a single `apply_layout_change`.
**Rejected:** an `AppState.mutate_many([...])` batching API — it would put
undo grouping in `AppState` for two callers, and every other handler in the
panel is already one click, one mutation.

1. **`layout_panel.py`, new pure mutator**, placed immediately after
   `set_margins_linked` (line 125):

   ```python
   def set_all_margins(project: Project, points: float) -> Project:
       """Set head, tail and fore-edge to the same value, in one step.

       Exists so a control that changes three fields is one step of undo.
       Ticking "Link all three" and pressing "Use printer margins" each
       wrote the margins one box at a time, so Ctrl+Z afterwards appeared
       to do nothing -- it undid the last of two or three mutations and
       left the visible state where it was.

       :param project: the project to derive a new one from.
       :param points: the margin, in points, for all three edges.
       :returns: a new project.
       """
       return replace(
           project,
           layout=replace(
               project.layout,
               margin_top_pt=points,
               margin_bottom_pt=points,
               margin_outer_pt=points,
           ),
       )
   ```

2. **`layout_panel.py:1390-1401`, `_on_link_margins_toggled`** — one mutation
   whether linking or unlinking:

   ```python
    def _on_link_margins_toggled(self, linked: bool) -> None:
        """Link or unlink the three margins, in one step of undo.

        Linking also ADOPTS the head margin for all three, so it is a
        visible, predictable action rather than a silent mode change. That
        used to be a second `mutate` on top of the flag's own, so one click
        was two steps of undo and Ctrl+Z appeared inert.

        :param linked: the checkbox's new state.
        :returns: nothing.
        """
        self._sync_margin_enabled()
        if linked:
            head = to_points(self.margin_spinboxes["margin_top_pt"].value(), self._unit)
            plan = apply_layout_change(
                self.state,
                lambda project: set_all_margins(
                    set_margins_linked(project, True), head
                ),
            )
            self._refresh_margin_boxes()
        else:
            plan = apply_layout_change(
                self.state, lambda project: set_margins_linked(project, False)
            )
        self.layout_changed.emit(plan)
   ```

   Note `to_points(...)`: the old path went through `_on_margin_changed`,
   which converted; the composed mutator takes points directly.

3. **`layout_panel.py:1444-1456`, `_on_use_printer_margins`** — one mutation,
   and refresh the boxes from the model rather than driving them:

   ```python
    def _on_use_printer_margins(self) -> None:
        """Set all three margins to the printer's non-printable inset.

        One step of undo. Setting the three spinboxes in turn fired
        `_on_margin_changed` three times, so a single click was three
        entries on the undo stack.

        :returns: nothing. A panel with no profile does nothing at all --
            the button is constructible without a printer.
        """
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        # The printer's dead border applies to all four edges, so this
        # ignores the link state: a partial application would leave some
        # edge still unprintable.
        inset = imageable_inset_pt(profile.imageable_area_pt)
        plan = apply_layout_change(
            self.state, lambda project: set_all_margins(project, inset)
        )
        self._refresh_margin_boxes()
        self.layout_changed.emit(plan)
   ```

   This also fixes a second, quieter defect: the old version emitted **no**
   `layout_changed` at all when linked (it relied on `_on_margin_changed` to
   do it), so the preview did not re-draw.

### 2.4 Tests

Add to **`tests/test_layout_panel_widgets.py`**, not
`tests/test_layout_panel_settings.py` — the latter is pure-setter tests with
no `QApplication` at all ("Pure setters only, as the rest of this module's
suite does", line 8). `test_layout_panel_widgets.py` sets
`QT_QPA_PLATFORM=offscreen` at line 22, has a module-scoped `qt_app` fixture
(line 31) and a `panel` fixture (line 37) — but that fixture constructs
`LayoutPanel(AppState(project))` with **no `profile=`**, so
`_on_use_printer_margins` returns early. Add a local builder beside it:

```python
def _panel(**layout):
    """A panel with a printer profile, so "Use printer margins" does
    something. The module's `panel` fixture has no profile, deliberately --
    the panel must stay constructible without a printer.
    """
    from deckle.app.views.layout_panel import LayoutPanel
    from deckle.core.profiles import BUILTIN_PRESETS

    base = dict(paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left")
    base.update(layout)
    state = AppState(
        Project(pages=[], layout=LayoutSettings(**base), printer=None)
    )
    return state, LayoutPanel(state, profile=next(iter(BUILTIN_PRESETS.values())))
```

`next(iter(BUILTIN_PRESETS.values()))` is the same profile
`tests/test_view_workers.py:47` uses; its `imageable_area_pt` is
`(18.0, 18.0, 18.0, 18.0)`, so `imageable_inset_pt` gives `18.0`.

```python
def test_linking_the_margins_is_one_step_of_undo():
    """One click, one Ctrl+Z. It was two, so the first press appeared to do
    nothing and the second undid something else."""
    state, panel = _panel(margins_linked=False, margin_top_pt=10.0,
                          margin_bottom_pt=20.0, margin_outer_pt=30.0)
    before = len(state._undo_stack)

    panel.link_margins_check.setChecked(True)

    assert len(state._undo_stack) - before == 1
    layout = state.project.layout
    assert layout.margins_linked is True
    assert layout.margin_bottom_pt == layout.margin_top_pt == layout.margin_outer_pt


def test_undoing_a_link_restores_all_three_margins():
    state, panel = _panel(margins_linked=False, margin_top_pt=10.0,
                          margin_bottom_pt=20.0, margin_outer_pt=30.0)
    panel.link_margins_check.setChecked(True)

    state.undo()

    layout = state.project.layout
    assert layout.margins_linked is False
    assert (layout.margin_top_pt, layout.margin_bottom_pt, layout.margin_outer_pt) \
        == (10.0, 20.0, 30.0)


def test_use_printer_margins_is_one_step_of_undo():
    state, panel = _panel(margins_linked=False, margin_top_pt=1.0,
                          margin_bottom_pt=2.0, margin_outer_pt=3.0)
    before = len(state._undo_stack)

    panel._on_use_printer_margins()

    assert len(state._undo_stack) - before == 1


def test_use_printer_margins_announces_the_change_even_when_linked():
    """It emitted layout_changed only on the unlinked path, so with the
    margins linked the preview did not redraw."""
    _state, panel = _panel(margins_linked=True, margin_top_pt=1.0)
    seen = []
    panel.layout_changed.connect(seen.append)

    panel._on_use_printer_margins()

    assert len(seen) == 1
```

Expected failures on the unfixed tree, in order:
`AssertionError: assert 2 == 1`;
`AssertionError: assert (10.0, 10.0, 10.0) == (10.0, 20.0, 30.0)` (undo
restores only the second mutation);
`AssertionError: assert 3 == 1`;
`AssertionError: assert 0 == 1`.

---

## §3 — Accepting autosave recovery re-prompts on every subsequent open

### 3.1 Context

Recover once, and Deckle asks again every time that project is opened, forever.

`autosave_recovery_offer` returns the autosave when it is newer than the
project. Declining **deletes** the autosave (deliberately — its docstring says
why). Accepting does not: it returns the recovered project and leaves both
files exactly as they were, so the autosave is still newer and the next open
prompts again. The user recovers, works, saves — that makes the project newer
and the prompt stops — but a user who recovers and then *closes without
saving* is prompted on every open until they do.

Worse than the nuisance: the second prompt offers an autosave that is now
**older than the work the user did after recovering**, and the flushed
autosave from the close overwrites it, so accepting a second time can hand
back a mixture the user never had.

Verified:

```bash
.venv/bin/python - <<'EOF'
import json, os, tempfile, warnings
from deckle.app.main import autosave_recovery_offer, MainWindow
from deckle.core.project_io import load_project

d = tempfile.mkdtemp(); proj = os.path.join(d, "job.deckle"); auto = proj + ".autosave"
def write(p, g):
    open(p, "w").write(json.dumps({"version": 1, "pages": [], "layout":
        {"paper": [792.0, 612.0], "gutter_pt": g, "binding_edge": "left"}}))
write(proj, 36.0); write(auto, 72.0)
now = 1_700_000_000
os.utime(proj, (now - 100, now - 100)); os.utime(auto, (now, now))

class W:
    def __init__(self):
        self.confirm_recovery = lambda name: True
        self.status_bar = type("B", (), {"showMessage": lambda s, t: None})()
        self._recover = MainWindow._recover_autosave_if_offered.__get__(self)

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    loaded = load_project(proj, check_sources=False)
rec = W()._recover(proj, loaded)
print("recovered gutter:", rec.layout.gutter_pt)
print("autosave still present:", os.path.exists(auto))
print("offered AGAIN on next open:", autosave_recovery_offer(proj))
EOF
```

Current output:

```
recovered gutter: 72.0
autosave still present: True
offered AGAIN on next open: /tmp/.../job.deckle.autosave
```

The last line must become `None`.

### 3.2 Current code

`deckle/app/main.py:842-882`:

```python
    def _recover_autosave_if_offered(self, path: str, project: Project) -> Project:
        """Offer a newer autosave in place of the project just loaded.
        ...
        Declining **deletes** the autosave. Leaving it would bring the
        prompt back on every subsequent open, which trains someone to
        dismiss it -- and the one that matters is the one they then
        dismiss without reading.
        ...
        """
        autosave_path = autosave_recovery_offer(path)
        if autosave_path is None:
            return project
        if not self.confirm_recovery(os.path.basename(path)):
            try:
                os.remove(autosave_path)
            except OSError as exc:  # noqa: BLE001 -- declined, never fatal
                log_exception("autosave_discard_failed", exc, path=autosave_path)
            return project
        try:
            recovered = load_project(
                autosave_path, allowed_roots=(os.path.dirname(path),)
            )
        except Exception as exc:  # noqa: BLE001 -- reported, never a crash
            self.status_bar.showMessage(
                f"Could not read the recovered changes for "
                f"{os.path.basename(path)}: {exc}"
            )
            log_exception("autosave_recovery_failed", exc, path=autosave_path)
            return project
        log_event("autosave_recovered", path=path, pages=len(recovered.pages))
        return recovered
```

The accept branch (last three lines) does nothing about the file. The
docstring's own reasoning — "Leaving it would bring the prompt back on every
subsequent open" — is applied to the decline path only.

`deckle/app/main.py:309-348`, `autosave_recovery_offer`, the pure rule:

```python
    autosave_path = autosave_path_for(project_path)
    if autosave_path is None or not os.path.isfile(autosave_path):
        return None
    try:
        autosave_time = os.path.getmtime(autosave_path)
    except OSError:
        return None
    try:
        project_time = os.path.getmtime(project_path)
    except OSError:
        # The project is gone and the autosave is not. That is the case
        # where recovery matters most, not a reason to discard the only
        # remaining copy of the work.
        return autosave_path
    return autosave_path if autosave_time > project_time else None
```

Call sites — `grep -rn "_recover_autosave_if_offered\|autosave_recovery_offer" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `main.py:309` | `autosave_recovery_offer` definition |
| `main.py:861` | its only production call |
| `main.py:842` | `_recover_autosave_if_offered` definition |
| `main.py:994` | its only call, in `open_project` |
| `tests/test_autosave_recovery.py:22, 40, 48, 56, 63, 68, 77, 85, 168` | the pure rule |
| `tests/test_autosave_recovery.py:107` | `MainWindow._recover_autosave_if_offered.__get__(self)` on the `_Window` double |

Tests: `tests/test_autosave_recovery.py`, all 14. In particular
`test_declining_deletes_the_autosave` (line 159) pins the decline path;
nothing pins the accept path's file handling.

### 3.3 Change

Accepting **renames** the autosave out of the way rather than deleting it. Two
designs were possible. **Chosen:** rename to `<project>.autosave.recovered`.
**Rejected:** deleting it, as the decline path does — the user has just told
Deckle these edits matter, and a recovery that turns out to be the wrong
generation would leave nothing to go back to. A rename silences the prompt
(`autosave_recovery_offer` looks for exactly `<project>.autosave`) and keeps
the bytes.

A previous `.recovered` file is replaced, via `os.replace`, which is atomic on
both POSIX and Windows.

1. **`main.py`, module constant** — beside `NO_PRINTERS_MESSAGE` and friends
   at line 39:

   ```python
   RECOVERED_AUTOSAVE_SUFFIX = ".recovered"
   """Appended to an autosave that has just been recovered from.

   Renaming rather than deleting: the prompt is driven by
   ``<project>.autosave`` existing and being newer, so moving the file aside
   is what stops it coming back on every open -- and unlike the DECLINE
   path, the user has just said these edits matter, so the bytes are kept
   in case the recovery was the wrong generation.
   """
   ```

2. **`main.py:881-882`**, the accept branch of
   `_recover_autosave_if_offered` — move the file aside before returning:

   ```python
        log_event("autosave_recovered", path=path, pages=len(recovered.pages))
        # Move it aside, or `autosave_recovery_offer` keeps returning it and
        # the prompt reappears on every subsequent open -- the same reason
        # the decline path deletes it. Kept rather than deleted: the user
        # has just said these edits matter.
        try:
            os.replace(autosave_path, autosave_path + RECOVERED_AUTOSAVE_SUFFIX)
        except OSError as exc:  # noqa: BLE001 -- recovered already, never fatal
            log_exception("autosave_archive_failed", exc, path=autosave_path)
        return recovered
   ```

3. **`main.py:842-860`, the docstring** — the "Declining **deletes**"
   paragraph becomes:

   ```
        Neither answer leaves the autosave where it was. Declining
        **deletes** it; accepting **renames** it to
        ``<project>.autosave.recovered``. Either way the prompt does not
        come back on the next open, which is what stops it training
        someone to dismiss prompts -- and the one that matters is the one
        they then dismiss without reading. The difference is that a user
        who accepted has said these edits matter, so the bytes are kept.
   ```

4. **The new state's autosave is unaffected.** `open_project` builds
   `AppState(project, project_path=path)` at `main.py:1004`, whose
   `autosave_path` is `<path>.autosave` — the name just vacated. The first
   edit after recovering writes it fresh. Nothing else to change.

### 3.4 Tests

Add to `tests/test_autosave_recovery.py`, reusing its `_Window` double
(line 95), `_project_with_autosave` (line 110) and `_loaded` (line 131). No
display needed — `_Window` never touches Qt.

```python
def test_accepting_moves_the_autosave_aside_so_it_is_not_offered_again():
    """Declining deleted it; accepting left it, so the prompt came back on
    every subsequent open -- and the second offer was of a generation older
    than the work done since recovering."""
    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=True)

    window._recover(project_path, _loaded(project_path))

    assert not os.path.exists(autosave_path)
    assert autosave_recovery_offer(project_path) is None


def test_accepting_keeps_the_recovered_bytes(tmp_path):
    """Renamed, not deleted: the user has just said these edits matter, and
    a recovery of the wrong generation must leave something to go back to."""
    from deckle.app.main import RECOVERED_AUTOSAVE_SUFFIX

    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=True)

    window._recover(project_path, _loaded(project_path))

    archived = autosave_path + RECOVERED_AUTOSAVE_SUFFIX
    assert os.path.exists(archived)
    assert _loaded(archived).layout.gutter_pt == 72.0


def test_recovering_twice_replaces_the_archive(tmp_path):
    """A second recovery must not fail on an existing archive."""
    from deckle.app.main import RECOVERED_AUTOSAVE_SUFFIX

    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    _Window(answer=True)._recover(project_path, _loaded(project_path))

    # A fresh crash: a new autosave appears, newer than the project again.
    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 99.0)
    _Window(answer=True)._recover(project_path, _loaded(project_path))

    assert _loaded(autosave_path + RECOVERED_AUTOSAVE_SUFFIX).layout.gutter_pt == 99.0


def test_a_failed_rename_still_returns_the_recovered_project(tmp_path, monkeypatch):
    """The work is already in hand by then. Losing it because a tidy-up
    failed would be the worst possible trade."""
    project_path, _ = _project_with_autosave(tmp_path, 36.0, 72.0)

    def _boom(src, dst):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "replace", _boom)
    window = _Window(answer=True)

    recovered = window._recover(project_path, _loaded(project_path))

    assert recovered.layout.gutter_pt == 72.0
```

(The first test needs `tmp_path` in its signature — add it; it is written
above matching the file's existing style, which takes `tmp_path` on every
such test.)

Expected failures on the unfixed tree, in order:
`AssertionError: assert not True` (the autosave is still there);
`AssertionError: assert False` (no archive exists);
`AssertionError` on the archive's gutter;
the fourth **passes** on the unfixed tree (there is no rename to fail) and is
the guard for the new `try/except`.

`test_declining_deletes_the_autosave` must keep passing unchanged.

---

## §4 — A replaced `AppState` leaves a live debounce timer

### 4.1 Context

`open_project` replaces `self.state` with a brand-new `AppState`. The outgoing
one is dropped, but its `threading.Timer` is still armed and still holds a
reference to the outgoing `AppState` through the bound `_do_autosave` method —
so it is not even garbage.

The concrete failure is reopening the project you are already in, which is an
ordinary thing to do after an accidental edit: edit, then Open project → the
same file. The outgoing timer fires up to 500 ms later and writes
`<path>.autosave` with the **pre-reopen** project, after the incoming
`AppState` has taken ownership of exactly that path. Two `AppState` instances
now write one file with no ordering between them — and `_save_lock` is a
per-instance lock (`state.py:213`), so it does not serialise them.

`tests/test_autosave_concurrency.py` exists precisely because the autosave
file is the one Deckle reads back after a crash and "neither of them" is not a
recoverable state. Its guarantees hold for two writers *within* one
`AppState`; this is two `AppState`s.

There is no way to cancel a pending autosave without also writing it:
`flush_autosave` (`state.py:323-337`) cancels **and** saves.

### 4.2 Current code

`deckle/app/state.py:289-298`:

```python
    def _schedule_autosave(self) -> None:
        if self.autosave_path is None:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = self._timer_factory(self._autosave_delay_s, self._do_autosave)
            timer.daemon = True
            self._timer = timer
            timer.start()
```

`deckle/app/state.py:323-337` — the only public way to stop it:

```python
    def flush_autosave(self) -> None:
        """Cancel any pending debounce timer and save immediately.
        ...
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._do_autosave()
```

`deckle/app/main.py:998-1010`, `open_project`:

```python
        # The outgoing project's cached sheet renders are unreachable the
        # moment the plan changes -- their key is the plan hash -- so they
        # are dead weight in temp until something removes them. Removed
        # here rather than only at exit, because scrubbing one long
        # document and then opening another is an ordinary session.
        clear_sheet_cache()
        self.state = AppState(project, project_path=path)
        self.import_view.state = self.state
        self.arrange_view.state = self.state
        self.layout_panel.state = self.state
```

Nothing touches the outgoing `self.state`.

Call sites — `grep -rn "flush_autosave\|_schedule_autosave" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `state.py:255, 270, 284` | `_schedule_autosave` from `mutate`/`undo`/`redo` |
| `state.py:289` | definition |
| `state.py:323` | `flush_autosave` definition |
| `main.py:1125` | `close()` |
| `main.py:1136` | `_on_close_event` |
| `tests/test_app_state.py:169, 181` | direct |
| `tests/test_autosave_concurrency.py:78, 95, 104, 121, 134, 147` | direct |

### 4.3 Change

Give `AppState` a way to stop autosaving without writing, and use it in
`open_project`. Two designs were possible. **Chosen:** an explicit
`cancel_autosave()`, called before the swap. **Rejected:** calling
`flush_autosave()` — it writes, and the point of opening a different project
is that the outgoing edits have already been offered back through the recovery
prompt (or deliberately abandoned); writing them again after the incoming
state owns the path is the race, not the fix.

1. **`state.py`, new method** on `AppState`, immediately after
   `flush_autosave`:

   ```python
    def cancel_autosave(self) -> None:
        """Stop the pending debounce without writing anything.

        The counterpart to :meth:`flush_autosave`, which cancels **and**
        saves. Needed when an ``AppState`` is being REPLACED rather than
        closed: the outgoing state's timer holds a bound reference to it,
        so it is still armed and not even collectable, and it would fire up
        to half a second later and write the autosave path the INCOMING
        state now owns. Two ``AppState`` instances writing one file with no
        ordering between them -- ``_save_lock`` is per-instance, so it does
        not serialise them, and that file is the one Deckle reads back
        after a crash.

        :returns: nothing. A no-op when nothing is scheduled.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
   ```

2. **`state.py:323-337`, `flush_autosave`** — express it in terms of the new
   method, so there is one cancel:

   ```python
        self.cancel_autosave()
        self._do_autosave()
   ```

3. **`main.py:1003-1004`, `open_project`** — cancel before the swap:

   ```python
        clear_sheet_cache()
        # The outgoing state's debounce timer holds a bound reference to it
        # and is still armed. Left alone it fires up to half a second later
        # and writes the autosave path the INCOMING state now owns -- two
        # AppStates racing for the file Deckle reads back after a crash.
        # Cancelled rather than flushed: the outgoing edits have already
        # been offered back through the recovery prompt.
        self.state.cancel_autosave()
        self.state = AppState(project, project_path=path)
   ```

4. **`state.py:174-193`, the class docstring** — add one line to the ivar/
   method summary noting that `cancel_autosave` exists for the replace case.

### 4.4 Tests

Headless — `deckle.app.state` imports no Qt.

Add to `tests/test_app_state.py`:

```python
def test_cancel_autosave_stops_the_timer_without_writing(tmp_path):
    """The counterpart to flush: a state being REPLACED must stop writing,
    not write once more into the path its successor now owns."""
    import os

    project_path = str(tmp_path / "proj.deckle")
    fired = []

    class _Recording(_ImmediateTimer):
        def start(self):
            fired.append(True)
            super().start()

    state = AppState(_make_project(2), project_path=project_path,
                     timer_factory=_Recording)
    state.mutate(lambda p: toggle_skip(p, 0))
    before = list(fired)

    state.cancel_autosave()

    assert fired == before, "cancel_autosave wrote"
    assert state._timer is None


def test_cancel_autosave_is_a_noop_with_nothing_scheduled():
    state = AppState(_make_project(1))
    state.cancel_autosave()          # must not raise
```

Expected failure on the unfixed tree: `AttributeError: 'AppState' object has
no attribute 'cancel_autosave'`.

Add to `tests/test_autosave_concurrency.py` — the file whose whole subject is
this file being torn:

```python
def test_a_replaced_state_stops_writing_the_autosave(tmp_path):
    """`open_project` swaps AppState. The outgoing one's armed timer would
    fire into the path the incoming one now owns -- two AppStates writing
    one file, with `_save_lock` per-instance and so no ordering between
    them."""
    path = os.path.join(str(tmp_path), "job.deckle")
    outgoing = AppState(_project(36.0), project_path=path)
    outgoing.mutate(lambda p: _project(11.0))

    outgoing.cancel_autosave()
    incoming = AppState(_project(99.0), project_path=path)
    incoming.mutate(lambda p: _project(99.0))
    incoming.flush_autosave()

    assert _load(incoming.autosave_path).layout.gutter_pt == 99.0
```

Expected failure on the unfixed tree: `AttributeError: 'AppState' object has
no attribute 'cancel_autosave'`.

And a structural guard in `tests/test_autosave_concurrency.py`:

```python
def test_open_project_cancels_the_outgoing_states_autosave():
    from pathlib import Path

    import deckle.app.main as app_main

    source = Path(app_main.__file__).read_text(encoding="utf-8")
    assert "self.state.cancel_autosave()" in source, (
        "open_project replaces AppState without stopping the outgoing timer"
    )
```

Expected failure on the unfixed tree:
`AssertionError: open_project replaces AppState without stopping the outgoing timer`.

---

## §5 — `deckle schedule` hashes the whole document before checking the output path

### 5.1 Context

`deckle schedule big-book.pdf -o /nonexistent/dir/sched.txt` loads and
SHA-256s every page of a 300-page PDF, then reports that the output directory
does not exist and exits 1. The user waits out a full load to be told
something that was knowable before it started.

Every other command in the CLI checks the output path first. `_cmd_schedule`
is the only one that does not.

Verified by reading the ordering — `_cmd_export` at `cli.py:953/955`,
`_cmd_impose` at `cli.py:1056/1058` and `_cmd_crop_preview` at
`cli.py:1131/1133` all check then load; `_cmd_schedule` at `cli.py:1090/1095`
loads then checks.

### 5.2 Current code

`deckle/cli.py:1080-1101`:

```python
def _cmd_schedule(args: argparse.Namespace) -> int:
    """Print the binding schedule for a source document.
    ...
    """
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    if args.output is not None and _report_output_problem(args.output, args.source):
        return 1

    plan = _impose_or_report(pages, settings)
```

`deckle/cli.py:1125-1133`, `_cmd_crop_preview` — the model to copy:

```python
def _cmd_crop_preview(args: argparse.Namespace) -> int:
    """Write a composite of every page, with the proposed crop drawn on it."""
    from PIL import Image

    from deckle.core.render import auto_crop_insets, composite_pages

    if _report_output_problem(args.output, args.source):
        return 1
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1
```

`deckle/cli.py:621-651`, `_resolve_input` — the expensive half:

```python
    pages = _load_source_or_report(args.source)
    if pages is None:
        return None
```

`_load_source_or_report` calls the loader, which SHA-256s the whole file
(`core/loader.py:335-378`).

Call sites — `grep -n "_report_output_problem\|_resolve_input\|_load_source_or_report" deckle/cli.py`:

| Line | Command | Order |
|---|---|---|
| `953` / `955` | `_cmd_export` | check, then resolve — correct |
| `1056` / `1058` | `_cmd_impose` | check, then resolve — correct |
| `1090` / `1095` | `_cmd_schedule` | **resolve, then check — the defect** |
| `1131` / `1133` | `_cmd_crop_preview` | check, then load — correct |
| `1196` | `_cmd_dummy` | check only |

Tests: `tests/test_cli_errors.py` and `tests/test_output_command_parity.py`
cover output-path refusal wording across commands.

### 5.3 Change

1. **`cli.py:1090-1096`, `_cmd_schedule`** — swap the two blocks, matching
   `_cmd_crop_preview` exactly:

   ```python
       # Checked BEFORE the source is loaded: `_resolve_input` SHA-256s
       # every page, and telling someone their output directory does not
       # exist after a 300-page hash is telling them something that was
       # knowable before it started. Every other command already does this.
       if args.output is not None and _report_output_problem(args.output, args.source):
           return 1

       resolved = _resolve_input(args)
       if resolved is None:
           return 1
       pages, settings = resolved

       plan = _impose_or_report(pages, settings)
   ```

   `_report_output_problem(args.output, args.source)` reads `args.source` as a
   *string* (to refuse writing over the input), not as a loaded document, so
   it is safe before the load.

### 5.4 Tests

Add to `tests/test_cli_errors.py` (headless, no Qt). Use `deckle dummy` to
make a source, per the environment note.

```python
def test_schedule_rejects_a_bad_output_path_before_reading_the_source(tmp_path, monkeypatch):
    """Every other command checks the destination first. Schedule hashed a
    300-page PDF and then said the directory did not exist."""
    from deckle import cli

    source = tmp_path / "book.pdf"
    assert cli.main(["dummy", "-o", str(source), "--pages", "4"]) == 0

    loaded = []
    real = cli._load_source_or_report
    monkeypatch.setattr(
        cli, "_load_source_or_report",
        lambda path: loaded.append(path) or real(path),
    )

    code = cli.main([
        "schedule", str(source), "-o", str(tmp_path / "no-such-dir" / "s.txt"),
    ])

    assert code == 1
    assert loaded == [], "the source was read before the output path was checked"
```

Assertion in words: a bad output path is refused without the source being
loaded at all.
Expected failure on the unfixed tree: `AssertionError: the source was read
before the output path was checked` — `loaded` holds the source path.

```python
def test_schedule_still_works_with_a_good_output_path(tmp_path):
    """The reorder must not break the ordinary path."""
    from deckle import cli

    source = tmp_path / "book.pdf"
    assert cli.main(["dummy", "-o", str(source), "--pages", "4"]) == 0
    out = tmp_path / "sched.txt"

    assert cli.main(["schedule", str(source), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").strip()
```

Passes before and after; it is the guard on the reorder.

---

## §6 — Four `assert`-based invariants vanish under `python -O`

### 6.1 Context

`SaddleStitchStrategy.impose` verifies four structural invariants with bare
`assert` statements. `python -O` strips every `assert`, so a packaged or
optimised build silently loses all four — and what they check is not a
programmer convenience but the thing that decides whether a folded gathering
reads correctly: that signature sheet indices are contiguous and gapless, that
every signature's slot count is a multiple of 4, that the counts sum to the
padded page count, and that concatenating the signatures reproduces the page
list in order.

A build with these stripped will impose a mis-collated book and say nothing.
The failure surfaces at the bench, after the paper is spent.

Verified: `grep -rn "^\s*assert " deckle/ | grep -v test` returns exactly four
lines, all in `core/layout.py`.

### 6.2 Current code

`deckle/core/layout.py:1039-1057`:

```python
        _creep_advisory(warnings, settings)

        # Invariants, verified rather than trusted -- the SS-03 precedent.
        all_sheet_indices = [i for sig in signatures for i in sig.sheet_indices]
        assert all_sheet_indices == list(range(len(sheets))), (
            "signature sheet_indices must be contiguous, gapless, and cover "
            "every sheet exactly once, in binding order"
        )
        assert all(len(sig_slice) % 4 == 0 for sig_slice in signature_slices), (
            "every signature's slot count must be a multiple of 4"
        )
        assert sum(len(sig_slice) for sig_slice in signature_slices) == len(slots), (
            "signature slot counts must sum to the padded page count"
        )
        concatenated = [slot for sig_slice in signature_slices for slot in sig_slice]
        assert concatenated == slots, (
            "concatenating the signatures' source slices must reproduce the "
            "padded page list in order"
        )

        return SheetPlan(
```

### 6.3 Change

Raise instead of asserting. Two designs were possible. **Chosen:** a private
`_require(condition, message)` helper raising `AssertionError` with the same
messages — the exception type is unchanged, so any test asserting on it keeps
working, and the messages stay verbatim. **Rejected:** a new exception class —
these are internal invariants, not a user-facing refusal, and inventing a
public error type for "this build has a bug" invites callers to catch it.

1. **`core/layout.py`, module-level private helper**, placed beside the other
   module-private helpers:

   ```python
   def _require(condition: bool, message: str) -> None:
       """Verify an internal invariant, in optimised builds too.

       ``assert`` is stripped by ``python -O``, and these four invariants
       are not a programmer convenience: they are what decides whether a
       folded gathering reads correctly. A build with them removed imposes
       a mis-collated book and says nothing, and the failure surfaces at
       the bench after the paper is spent.

       :param condition: the invariant.
       :param message: what it guarantees, stated positively.
       :returns: nothing.
       :raises AssertionError: the invariant does not hold. The same type
           `assert` raised, so nothing that catches it changes.
       """
       if not condition:
           raise AssertionError(message)
   ```

2. **`core/layout.py:1041-1057`** — the four asserts become four calls:

   ```python
        # Invariants, verified rather than trusted -- the SS-03 precedent.
        # `_require`, not `assert`: `python -O` strips asserts, and a
        # packaged build must not impose a mis-collated book in silence.
        all_sheet_indices = [i for sig in signatures for i in sig.sheet_indices]
        _require(
            all_sheet_indices == list(range(len(sheets))),
            "signature sheet_indices must be contiguous, gapless, and cover "
            "every sheet exactly once, in binding order",
        )
        _require(
            all(len(sig_slice) % 4 == 0 for sig_slice in signature_slices),
            "every signature's slot count must be a multiple of 4",
        )
        _require(
            sum(len(sig_slice) for sig_slice in signature_slices) == len(slots),
            "signature slot counts must sum to the padded page count",
        )
        concatenated = [slot for sig_slice in signature_slices for slot in sig_slice]
        _require(
            concatenated == slots,
            "concatenating the signatures' source slices must reproduce the "
            "padded page list in order",
        )
   ```

### 6.4 Tests

Headless, no Qt, no display. Add to `tests/test_layout_saddle.py`.

```python
def test_the_saddle_invariants_survive_python_dash_O():
    """`assert` is stripped by -O. These four decide whether a folded
    gathering reads correctly, so a packaged build losing them imposes a
    mis-collated book in silence."""
    import os
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = (
        "from deckle.core.layout import _require\n"
        "try:\n"
        "    _require(False, 'the invariant')\n"
        "except AssertionError as exc:\n"
        "    print('raised:', exc)\n"
        "else:\n"
        "    print('SILENT')\n"
    )
    env = dict(os.environ, PYTHONPATH=repo_root)
    result = subprocess.run(
        [sys.executable, "-O", "-c", probe],
        capture_output=True, text=True, env=env, cwd=repo_root, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "raised: the invariant" in result.stdout
    assert "SILENT" not in result.stdout


def test_no_bare_assert_remains_in_the_imposer():
    from pathlib import Path

    from deckle.core import layout as module

    lines = Path(module.__file__).read_text(encoding="utf-8").splitlines()
    offenders = [
        (i + 1, line.strip())
        for i, line in enumerate(lines)
        if line.strip().startswith("assert ")
    ]
    assert offenders == [], f"stripped under -O: {offenders}"
```

Expected failures on the unfixed tree: `ImportError: cannot import name
'_require'` for the first; for the second,
`AssertionError: stripped under -O: [(1043, 'assert all_sheet_indices == ...'), ...]`
naming all four.

A repo-wide version, if a broader guard is wanted, belongs with the other
mechanical checks and is **out of scope** here — this section fixes the four
that exist.

---

## §7 — `sewing_stations` marches backwards on a short sheet

### 7.1 Context

`sewing_stations(sheet_h, fold_x, count)` spaces `count` ticks between a
`SEWING_MARGIN_PT = 36.0` inset from the tail and the same inset from the
head. On a sheet shorter than `2 × 36 = 72pt`, `span = sheet_h - 72` is
**negative**, `step` is negative, and the stations walk *down* from 36 instead
of up — so the first tick is not at the tail inset, the last is not at the
head inset, and on a very short sheet they land at negative y, off the paper
entirely.

The v2 sub-spec states the required behaviour explicitly
(`docs/specs/deckle-signatures-v2/sub-spec-6-marks-geometry.md`, §3
Invariants):

> `sheet_h <= 2 * SEWING_MARGIN_PT` degenerates safely: clamp `span` to `0.0`
> so all stations coincide at `SEWING_MARGIN_PT` rather than producing a
> negative span. Never raise.

It was never implemented.

Verified:

```bash
.venv/bin/python -c "
from deckle.core.marks import sewing_stations
for m in sewing_stations(50.0, 100.0, 3): print(m.y0)
"
```

Current output:

```
36.0
25.0
14.0
```

Descending, with none of them at the tail inset. Expected after this section:
`36.0`, `36.0`, `36.0`.

### 7.2 Current code

`deckle/core/marks.py:32-63`:

```python
def sewing_stations(
    sheet_h: float, fold_x: float, count: int
) -> tuple[Mark, ...]:
    """``count`` short ticks crossing the fold line, evenly spaced.

    Spacing runs from ``SEWING_MARGIN_PT`` above the tail to the same distance
    below the head. ``count <= 0`` returns ``()`` -- how
    ``settings.sewing_stations = 0`` disables stations without a new boolean.
    A single station is centred on the sheet.

    :param sheet_h: the sheet height in points.
    :param fold_x: the x coordinate of the fold, which the ticks straddle.
    :param count: how many stations. ``<= 0`` returns no marks.
    :returns: the station ticks, tail to head.
    """
    if count <= 0:
        return ()

    x0 = fold_x - STATION_TICK_PT
    x1 = fold_x + STATION_TICK_PT

    if count == 1:
        y = sheet_h / 2.0
        return (Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y),)

    span = sheet_h - 2 * SEWING_MARGIN_PT
    step = span / (count - 1)
    marks = []
    for i in range(count):
        y = SEWING_MARGIN_PT + i * step
        marks.append(Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y))
    return tuple(marks)
```

Line 57 — `span = sheet_h - 2 * SEWING_MARGIN_PT` — is unclamped. The
docstring's own promise, "the station ticks, **tail to head**", is what fails.

`SEWING_MARGIN_PT = 36.0` and `STATION_TICK_PT = 18.0`, verified at runtime.

Call sites — `grep -rn "sewing_stations" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `core/marks.py:32` | definition |
| `core/layout.py:18` | imported |
| `core/layout.py:976` | `sewing_stations(paper_h, fold_x, settings.sewing_stations)`, inside `SaddleStitchStrategy.impose` — the only production caller |
| `core/models.py:410` | `LayoutSettings.sewing_stations: int = 3` |
| `core/schedule.py:270, 397-399` | carried onto the bench sheet as a count |
| `cli.py:725` | `--sewing-stations` |
| `app/views/layout_panel.py:336, 1707` | `set_sewing_stations`, `_on_sewing_stations_changed` |
| `tests/test_marks.py` | the module's own tests |
| `tests/test_export_marks.py` | drawing |

Tests: `tests/test_marks.py`. Nothing there uses a sheet shorter than 72pt.

### 7.3 Change

1. **`marks.py:57`** — clamp:

   ```python
       # Clamped, not allowed to go negative: below 72pt of sheet the inset
       # from the head and the inset from the tail overlap, and an
       # unclamped span made `step` negative so the stations walked DOWN
       # from the tail inset -- the first tick not at the tail, the last
       # not at the head, and on a very short sheet off the paper
       # altogether. Coinciding at the tail inset is the degenerate answer;
       # never raise. (sub-spec-6 §3, Invariants.)
       span = max(0.0, sheet_h - 2 * SEWING_MARGIN_PT)
       step = span / (count - 1)
   ```

2. **`marks.py:35-46`, the docstring** — add to the `:returns:` block:

   ```
       :returns: the station ticks, tail to head. A sheet shorter than
           ``2 * SEWING_MARGIN_PT`` degenerates safely: every station
           coincides at ``SEWING_MARGIN_PT``. Never raises.
   ```

   Note `count == 1` is already handled above and is unaffected — a single
   station is centred at `sheet_h / 2.0`, which is on the paper for any
   positive height.

### 7.4 Tests

Headless, pure arithmetic, no Qt. Add to `tests/test_marks.py`.

```python
def test_stations_on_a_short_sheet_coincide_rather_than_marching_backwards():
    """Below 72pt the head inset and the tail inset overlap. An unclamped
    span made `step` negative, so the ticks walked DOWN from the tail inset
    -- the docstring promises tail to head."""
    from deckle.core.marks import SEWING_MARGIN_PT, sewing_stations

    marks = sewing_stations(50.0, 100.0, 3)

    assert len(marks) == 3
    assert [m.y0 for m in marks] == [SEWING_MARGIN_PT] * 3


def test_stations_are_never_below_the_sheet():
    """The worst case: a very short sheet put ticks at negative y, off the
    paper entirely."""
    from deckle.core.marks import sewing_stations

    for sheet_h in (1.0, 10.0, 50.0, 71.9, 72.0):
        for count in (2, 3, 5, 20):
            marks = sewing_stations(sheet_h, 100.0, count)
            assert all(m.y0 >= 0.0 for m in marks), (sheet_h, count)


def test_a_normal_sheet_is_unchanged():
    """The regression guard: clamping must not move a station on any sheet
    tall enough for the insets not to overlap."""
    from deckle.core.marks import SEWING_MARGIN_PT, sewing_stations

    marks = sewing_stations(792.0, 396.0, 3)

    assert [m.y0 for m in marks] == [
        SEWING_MARGIN_PT, 792.0 / 2.0, 792.0 - SEWING_MARGIN_PT
    ]
```

Expected failures on the unfixed tree:
`AssertionError: assert [36.0, 25.0, 14.0] == [36.0, 36.0, 36.0]`;
`AssertionError: (1.0, 2)` — at `sheet_h=1.0, count=2` the last station is at
`-34.0`;
the third **passes** before and after, deliberately.

---

## 5. Acceptance

Run from the repository root. Rows are grouped by sub-section so partial
landings can be checked.

| Check | Command |
|---|---|
| §1 thumbnails | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_arrange_reorder.py -k "raw_buffer or user_role"` |
| §1 the buffer is not stored | `! grep -n "item.setData(0x0100" deckle/app/views/arrange_view.py` — currently hits line 647 |
| §1 the page index is still stored | `grep -q "item.setData(_page_index_role(), i)" deckle/app/views/arrange_view.py` |
| §2 undo grouping | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_panel_widgets.py -k "one_step_of_undo or restores_all_three or announces_the_change"` |
| §2 the repro reads 1/1/1/1 | paste §2.1's `python - <<'EOF'` block |
| §3 recovery | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_autosave_recovery.py` |
| §3 the repro says `None` | paste §3.1's `python - <<'EOF'` block; the last line must read `offered AGAIN on next open: None` |
| §4 cancel | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py tests/test_autosave_concurrency.py` |
| §4 `open_project` cancels | `grep -q "self.state.cancel_autosave()" deckle/app/main.py` |
| §5 schedule order | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_errors.py -k schedule` |
| §5 check precedes resolve | `test "$(awk '/^def _cmd_schedule/,/^def _cmd_crop_preview/' deckle/cli.py \| grep -n '_report_output_problem\|_resolve_input' \| head -1 \| grep -c _report_output_problem)" -eq 1` |
| §6 invariants survive `-O` | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout_saddle.py -k "dash_O or bare_assert"` |
| §6 no bare assert in `deckle/` | `! grep -rn "^\s*assert " deckle/` — currently hits `core/layout.py:1043, 1047, 1050, 1054` |
| §6 the imposer still passes `-O` | `QT_QPA_PLATFORM=offscreen .venv/bin/python -O -m pytest -q --no-header -p no:cacheprovider tests/test_layout_saddle.py tests/test_signatures.py` |
| §7 short sheets | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_marks.py` |
| §7 the repro reads 36/36/36 | `.venv/bin/python -c "from deckle.core.marks import sewing_stations; print([m.y0 for m in sewing_stations(50.0, 100.0, 3)])"` |
| Everything | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| `deckle.core` still imports no Qt | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| [HUMAN] §7 on paper | Impose a folio job on a sheet under 72pt tall (a miniature book), export, and look at the fold. Every sewing tick must be on the paper, and the marks must not read as an inverted ladder. |

## 6. Out of scope

- **B9** — `autosave_path` freezing at construction. §4 uses the
  `project_path` property B9 introduces but does not create it; land B9 first.
- **B10/N9 — the dirty flag and save prompts.** §3 edits
  `_recover_autosave_if_offered`, which B10 also touches (it adds a
  Save/Discard/Cancel prompt on Recover). Do §3 first and B10 builds on it.
- **B12 — the `refresh_from_project` block-list.** §2 edits the same file;
  they do not overlap (§2 touches two handlers, B12 touches the constructor's
  connections and the refresh). Land B12 first, since it changes how those
  handlers are connected.
- **B32** — `_blank_thumbnail` ignoring `rotate_deg`, `ink_bbox`'s cache key
  omitting dpi, `actual_margins_pt` ignoring `crop_pt`. Same neighbourhood as
  §1; different bugs.
- **B33** — SHA-256 under the pdfium lock, and every image's PDF bytes held in
  memory. §5 makes the CLI *skip* the load in one failure case; it does not
  make the load cheaper.
- **B18** — `signature_lengths` / `sheets_per_signature <= 0` raising
  `ZeroDivisionError` under `blank_mode="balanced"`. Adjacent to §6's
  invariants in `core/layout.py`; a separate defect.
- **M2** — the `_place_page` / `SaddleStitchStrategy.impose` refactor. §6
  changes four lines inside `impose`; do not restructure it.
- **N5 — sewing-station *positions*** (`--stations 0.5in,2in,...`) rather than
  a count. §7 fixes the arithmetic for the count that exists.
- **M6** — the two atomic-write implementations. §3 uses `os.replace`
  directly, which is the same primitive `write_text_atomic` uses; do not
  route it through `paths.atomic_output`, which is for creating a new file.

## 7. decisions.md entry

```
## 2026-09-05 — Seven small defects: memory, undo grouping, recovery, timers, ordering, -O, and a negative span
- Symptom: (1) The thumbnail grid stored every page's raw RGBA buffer in the item's UserRole for a reader that does not exist — 485 KB per page, 145 MB for a 300-page scan scrolled end to end, measured. (2) "Link all three" pushed 2 undo entries per click and "Use printer margins" pushed 3, so Ctrl+Z appeared inert and the second press undid something else; the linked path of Use printer margins also emitted no `layout_changed`, so the preview did not redraw. (3) Accepting autosave recovery left the autosave in place, so the prompt returned on every subsequent open and the second offer was of a generation older than the work done since. (4) `open_project` replaced `AppState` while the outgoing one's debounce timer was armed and holding a bound reference to it, so it fired into the autosave path the incoming state now owned — two AppStates racing for the file Deckle reads back after a crash, with `_save_lock` per-instance and so no ordering between them. (5) `deckle schedule` SHA-256'd a 300-page document before checking the output path; every other command checks first. (6) Four structural invariants in `SaddleStitchStrategy.impose` were bare `assert`s, stripped by `python -O`, so a packaged build would impose a mis-collated book in silence. (7) `sewing_stations` on a sheet under 72pt gave a negative span, so the ticks walked DOWN from the tail inset and on a very short sheet landed off the paper — contradicting both the docstring's "tail to head" and sub-spec-6's stated invariant.
- Fix: (1) drop the `setData`, keep the icon, which already owns its own copy of the pixels. (2) a `set_all_margins(project, points)` mutator so both controls are one `mutate`. (3) accepting renames the autosave to `<project>.autosave.recovered` — renamed, not deleted like the decline path, because the user has just said these edits matter. (4) `AppState.cancel_autosave()`, the counterpart to `flush_autosave`, called before the swap. (5) swap the two blocks in `_cmd_schedule` to match `_cmd_crop_preview`. (6) a `_require(condition, message)` helper raising `AssertionError` verbatim. (7) `span = max(0.0, sheet_h - 2 * SEWING_MARGIN_PT)`.
- Surfaces: Three of the seven are a comment or a docstring describing behaviour the code does not have — the "keep the raw buffer for anything that wants the pixels" that nothing wants, the recovery docstring reasoning about re-prompts on the decline path only, and sub-spec-6's clamp invariant never implemented. A promise in prose is not a test.
- Watch: `python -O` strips `assert`. Anything in `deckle/` that must hold in a packaged build has to raise. `grep -rn "^\s*assert " deckle/` is the check, and it found exactly four.
- Commit: <fill in>
```

## 8. Traps

- **§1: `item.setData(_page_index_role(), i)` at line 601 must stay.** It is
  the page's identity, stamped so the order after a drop can be read back
  (`arrange_view.py:382-392`). Deleting it silently breaks drag-to-reorder,
  and `tests/test_arrange_reorder.py` will say so.
- **§2: `_on_link_margins_toggled`'s old code returned early on the linked
  path** and let `_on_margin_changed` emit `layout_changed`. The rewrite must
  emit it on both paths, or linking stops redrawing the preview.
- **§2: the composed mutator takes points, not display units.** The old path
  went through `_on_margin_changed`, which called `to_points`. Forgetting the
  conversion sets a margin of "0.25 points" for a user working in inches.
- **§2 interacts with B12.** B12 rewrites how these handlers are *connected*
  (`_bind`); §2 rewrites what they *do*. Land B12 first and re-read the
  connection block before editing.
- **§3: `os.replace` is atomic on both POSIX and Windows** and overwrites an
  existing destination. `os.rename` is not — it raises `FileExistsError` on
  Windows when the target exists, which is exactly the second-recovery case.
  Use `os.replace`.
- **§3: the rename must not be able to lose the recovery.** It happens *after*
  `load_project` succeeded and the project is already in hand, and its
  `except OSError` returns the recovered project anyway.
- **§4: `flush_autosave` is called from `close()` and `_on_close_event`**
  (`main.py:1125, 1136`) and must keep writing. Only `open_project` cancels.
  Do not swap one for the other.
- **§4: `_lock` is an `RLock` and `_save_lock` is a plain `Lock`, per
  instance.** Two `AppState`s do not serialise against each other; that is the
  whole defect. Do not "fix" it with a module-level lock — the point is that
  the second writer should not exist.
- **§5: `_report_output_problem(args.output, args.source)` takes `args.source`
  as a path string**, to refuse writing over the input. It does not need the
  document loaded, which is what makes the reorder safe.
- **§6: keep `AssertionError`.** `_require` raising anything else would change
  what a caller catches, and these are internal invariants, not a user-facing
  refusal. Do not invent a public exception type.
- **§6: run the suite under `-O` at least once** (the acceptance table has the
  row). A test that only ever runs unoptimised cannot see this class of bug.
- **§7: `count == 1` returns early** and centres the station at
  `sheet_h / 2.0`; the clamp is only reachable for `count >= 2`. Do not clamp
  the single-station case to `SEWING_MARGIN_PT` — a centred station is correct
  on any sheet.
- **§7: `deckle/core` must not import Qt** (`tests/test_core_purity.py`).
  `marks.py` and `layout.py` are core; keep them pure.
- **A real `QMainWindow` cannot be constructed under pytest here** (exit 127
  with the offscreen platform). §3's tests use `tests/test_autosave_recovery.py`'s
  `_Window` double; §4's use structural source assertions.
- **`python -m deckle` launches the GUI and blocks.** Every repro here is a
  `python - <<'EOF'` script or `python -m deckle.cli`.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`.
</content>
