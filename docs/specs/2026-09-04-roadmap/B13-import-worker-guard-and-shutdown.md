# B13 — Guard the import worker, free its thread, and settle it on quit

**Roadmap item:** `docs/ROADMAP.md` B13
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`ImportView` is the only one of the three background-worker views in
`deckle/app` that has none of the protections the other two have. Three
consequences, in the order they hurt:

**Two quick imports apply in completion order.** `import_path` overwrites
`self._thread` / `self._worker` and starts a second `QThread` without
cancelling or superseding the first. `_on_finished` has no
"is this still current" check, so whichever `load_pdf` finishes last wins —
which for a 300-page scan started first and a 4-page cover started second is
the 300-page scan, landing *after* the user has already seen the cover
imported. Both mutations go through `AppState.mutate`, so the undo stack
records both.

**`open_project` swaps `AppState` mid-import, and the import lands in an
orphaned state.** `ImportWorker.__init__` captures `state` at construction
(`import_view.py:93`). `MainWindow.open_project` assigns a brand-new
`AppState` at `main.py:1004` and re-points `self.import_view.state` at
`main.py:1005` — but the *running worker* still holds the old one. The
import completes, mutates a state nothing reads, and `_on_finished` emits
`imported`, so `MainWindow._on_imported` refreshes the arrange view over the
newly opened project and reports "Imported 300 page(s)." for pages that are
nowhere.

**Quitting mid-import runs pdfium into interpreter teardown.**
`stop_background_work` (`main.py:1160, 1168`) iterates
`(self.preview_view, self.arrange_view)` and nothing else. `ImportWorker` has
no `cancel` event to set, and `_live_threads` is never asked about
`import_view`, so an import thread inside `load_pdf` survives `close()`.
That is precisely the crash class `_live_threads` and `stop_background_work`
were written for: exit `0xC0000409`, no Python traceback, pdfium called after
finalisation. See `tests/test_shutdown.py` and the 2026-08-04 decision-log
entry *"Superseded background renders raced and leaked"*.

**And the thread is never freed.** `import_path` parents the `QThread` to
`self.widget` and never connects `deleteLater`, so every import leaks a
`QThread` for the life of the window. `PreviewView.refresh` and
`ArrangeView.request_visible_thumbnails` both connect it; the same decision
entry records why.

## 2. Current code

### The pattern to copy, named

`ArrangeView.request_visible_thumbnails` / `_on_thumbnails_ready`,
`deckle/app/views/arrange_view.py:607-636`. Copy this exactly — the same four
moves, in the same order:

```python
    def request_visible_thumbnails(self, scroll_index: int) -> None:
        """Kick off a background thumbnail fetch for the visible window.

        Supersedes any fetch still in flight -- scrolling a 266-page grid
        otherwise queues a thread per scroll step, each parented to the
        widget and so never freed, with the slowest painting last.
        ...
        """
        if self._worker is not None:
            self._worker.cancel.set()

        pages = self.state.project.pages
        worker = ThumbnailWorker(pages, scroll_index, self.VIEWPORT_COUNT)
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_thumbnails_ready(worker))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thumbnails_ready(self, worker: ThumbnailWorker) -> None:
        # Ignore a superseded fetch: stale thumbnails must not land on top
        # of the window the user actually scrolled to.
        if worker is not self._worker or worker.cancel.is_set():
            return
```

`PreviewView.refresh` / `_on_frame_ready`, `deckle/app/views/preview_view.py:741-772`,
is the identical shape:

```python
        if self._worker is not None:
            self._worker.cancel.set()
        ...
        thread.finished.connect(lambda: self._on_frame_ready(worker))
        # Threads are parented to the widget, so without this they pile up
        # for the life of the view -- one per sheet scrubbed past.
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_frame_ready(self, worker: PreviewWorker) -> None:
        # Ignore anything a superseded render produces: a slow earlier job
        # must never repaint over a newer one.
        if worker is not self._worker or worker.cancel.is_set():
            return
```

And the worker side, `ThumbnailWorker`, `arrange_view.py:256-301`:

```python
        #: Set when the fetch raised. Distinct from an empty ``rendered``,
        #: which is also what a cancelled or genuinely blank window leaves.
        self.failed = False
        # Set when a newer scroll position supersedes this fetch.
        self.cancel = threading.Event()

    def run(self) -> None:
        if self.cancel.is_set():
            return
        try:
            ...
        if self.cancel.is_set():
            return
        self.start, self.rendered = start, rendered
```

### What `ImportView` has instead

`deckle/app/views/import_view.py:185-208` — the whole of it:

```python
    def import_path(self, source_path: str) -> None:
        """Kick off a background import of ``source_path``.
        ...
        """
        self.status_label.setText(f"Importing {source_path}...")
        worker = ImportWorker(self.state, source_path)
        thread = self._QThread(self.widget)
        thread.run = worker.run  # simplest correct QThread.run override
        thread.finished.connect(lambda: self._on_finished(worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_finished(self, worker: ImportWorker) -> None:
        if worker.error is not None:
            self.status_label.setText(worker.error)
            self.failed.emit(worker.error)
        else:
            self.status_label.setText(f"Imported {len(worker.pages)} page(s).")
            self.imported.emit(worker.pages, worker.warnings)
```

No `worker.cancel`, no supersede, no `deleteLater`, no current-worker guard.

`ImportWorker.__init__`, `import_view.py:92-97` — no cancel token:

```python
    def __init__(self, state: AppState, source_path: str) -> None:
        self.state = state
        self.source_path = source_path
        self.pages: list[SourcePage] = []
        self.warnings: list = []
        self.error: str | None = None
```

`ImportWorker.run`, `import_view.py:99-125` — the mutation happens inside
`load_and_apply_import`:

```python
        try:
            self.pages, self.warnings = load_and_apply_import(self.state, self.source_path)
        except EncryptedPdfError as exc:
            self.error = f"password-protected PDF: {exc.path}"
        except SourceLoadError as exc:
            ...
            self.error = str(exc)
            log_exception("import_failed", exc, path=self.source_path)
```

and `load_and_apply_import`, `import_view.py:29-49`:

```python
    pages = _load_source(source_path)
    warnings = list(getattr(pages, "warnings", []))
    page_list = list(pages)
    state.mutate(lambda project: replace(project, pages=page_list))
    return page_list, warnings
```

### The shutdown path

`deckle/app/main.py:1140-1182`, `stop_background_work` — note both loops:

```python
        for view in (self.preview_view, self.arrange_view):
            try:
                worker = getattr(view, "_worker", None)
                if worker is not None:
                    worker.cancel.set()
            except Exception as exc:  # pragma: no cover - defensive
                log_exception("shutdown_cancel_failed", exc)

        for view in (self.preview_view, self.arrange_view):
            try:
                # Every live thread, not just `view._thread` -- see
                # `_live_threads`. Waiting only for the current one left
                # superseded renders running into interpreter teardown,
                # which is the crash this method exists to prevent.
                for thread in _live_threads(view):
                    if thread.isRunning():
                        if not thread.wait(timeout_ms):
```

### The `AppState` swap

`deckle/app/main.py:1003-1010`, in `open_project`:

```python
        clear_sheet_cache()
        self.state = AppState(project, project_path=path)
        self.import_view.state = self.state
        self.arrange_view.state = self.state
        self.layout_panel.state = self.state
        self.arrange_view.refresh()
        self.layout_panel.refresh_from_project()
        self._on_pages_changed()
```

`self.import_view.state = self.state` re-points the *view*; the running
worker's captured `state` is untouched.

### Call sites

`grep -rn "import_path\|ImportWorker\|_on_finished" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `deckle/app/views/import_view.py:177, 183` | `_pick_pdf` / `_pick_images` call `import_path` |
| `deckle/app/views/import_view.py:185` | definition |
| `deckle/app/views/import_view.py:194` | constructs `ImportWorker` |
| `deckle/app/views/import_view.py:197` | connects `_on_finished` |
| `deckle/app/views/import_view.py:202` | definition of `_on_finished` |
| `tests/test_import_view.py:51-56` | `_worker()` helper constructs `ImportWorker` and calls `run()` directly |
| `tests/test_import_view.py:75, 88, 149, 165` | calls `view._on_finished(worker)` directly |

Nothing outside `import_view.py` calls `import_path`; nothing anywhere sets
`view._worker` before calling `_on_finished`, which is why adding the guard
requires updating those tests (see §4).

`grep -rn "stop_background_work\|_live_threads" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `deckle/app/main.py:223` | `_live_threads` definition |
| `deckle/app/main.py:1126, 1137` | called from `close` and `_on_close_event` |
| `deckle/app/main.py:1140` | `stop_background_work` definition |
| `deckle/app/main.py:1174` | `_live_threads(view)` inside it |
| `tests/test_shutdown.py:58, 67` | the `stop_only` probe |
| `tests/test_shutdown.py:196, 211, 227` | `_live_threads` unit tests with `_FakeView` |

### Existing tests that touch this code

- `tests/test_import_view.py` — the whole file (17 tests).
- `tests/test_shutdown.py` — all seven.
- `tests/test_view_workers.py::test_printer_enumeration_is_not_called_during_import`.
- `tests/gui_workflow.py` does **not** exercise `ImportView`; it mutates the
  state directly at line 67 and calls `window._on_imported`.

## 3. Change

1. **`import_view.py:11-21`, module imports** — add `threading` beside the
   existing `os`:

   ```python
   import os
   import threading
   from dataclasses import replace
   from typing import Sequence
   ```

2. **`import_view.py`, `ImportWorker.__init__`** — add the cancel token,
   worded like `ThumbnailWorker`'s:

   ```python
        self.error: str | None = None
        # Set when a newer import, or a project being opened, supersedes
        # this one. Checked before the load and again before the mutation,
        # because the load is the long part and the mutation is the part
        # that would land in an orphaned AppState.
        self.cancel = threading.Event()
   ```

   Update the class docstring's `:ivar:` block (`import_view.py:87-89`) with:

   ```
       :ivar cancel: set when a newer import supersedes this one, or when
           the window is shutting down.
   ```

3. **`import_view.py`, `ImportWorker.run`** — check the token at both ends,
   the way `ThumbnailWorker.run` does:

   ```python
        if self.cancel.is_set():
            return
        try:
            pages, warnings = load_and_apply_import(
                self.state, self.source_path, cancel=self.cancel
            )
        except EncryptedPdfError as exc:
            self.error = f"password-protected PDF: {exc.path}"
        except SourceLoadError as exc:
            ...
            self.error = str(exc)
            log_exception("import_failed", exc, path=self.source_path)
        else:
            self.pages, self.warnings = pages, warnings
   ```

   Note the restructure: results are assigned in an `else:` so a cancelled
   load leaves `pages`/`warnings` at their empty defaults, matching
   `ThumbnailWorker.run`'s "a cancelled fetch leaves both untouched".

4. **`import_view.py`, `load_and_apply_import`** — add the optional token and
   the mutation guard. This is the one that stops an orphaned mutation:

   ```python
   def load_and_apply_import(
       state: AppState,
       source_path: str,
       cancel: threading.Event | None = None,
   ) -> tuple[list[SourcePage], list]:
       """Load ``source_path`` and replace ``state.project.pages`` with it.

       Returns ``(pages, warnings)``. Routed through ``AppState.mutate`` like
       every other project change, so importing participates in undo and
       triggers the same debounced autosave.

       :param state: the app state whose project is replaced.
       :param source_path: a PDF file, or a directory of images.
       :param cancel: optional event. When it is set by the time the load
           finishes, the pages are returned but **not** applied -- the
           window has moved on, either to a newer import or to a different
           project entirely, and `ImportWorker` captured this ``state`` at
           construction. Mutating it then writes into an ``AppState``
           nothing reads while the status bar announces a successful import
           of pages that are nowhere.
       :returns: ``(pages, warnings)``.
       :raises deckle.core.loader.SourceLoadError: any refusal from the
           loader, unchanged.
       """
       pages = _load_source(source_path)
       warnings = list(getattr(pages, "warnings", []))
       page_list = list(pages)
       if cancel is not None and cancel.is_set():
           return page_list, warnings
       state.mutate(lambda project: replace(project, pages=page_list))
       return page_list, warnings
   ```

   The default `cancel=None` keeps `tests/test_app_state.py:192, 240, 252`
   working unchanged.

5. **`import_view.py`, `ImportView.import_path`** — supersede, and free the
   thread. Copy `ArrangeView.request_visible_thumbnails` line for line:

   ```python
    def import_path(self, source_path: str) -> None:
        """Kick off a background import of ``source_path``.

        Supersedes any import still in flight, the same way
        ``ArrangeView.request_visible_thumbnails`` and
        ``PreviewView.refresh`` do: two quick imports otherwise applied in
        completion order, so a 300-page scan started first landed on top of
        the 4-page cover the user imported second.

        :param source_path: a PDF file, or a directory of images.
        :returns: nothing, immediately. The import runs on a ``QThread``
            so a 300-page source never blocks the UI; completion arrives as
            ``imported`` or ``failed``.
        """
        if self._worker is not None:
            self._worker.cancel.set()

        self.status_label.setText(f"Importing {source_path}...")
        worker = ImportWorker(self.state, source_path)
        thread = self._QThread(self.widget)
        thread.run = worker.run  # simplest correct QThread.run override
        thread.finished.connect(lambda: self._on_finished(worker))
        # Threads are parented to the widget, so without this they pile up
        # for the life of the window -- one per import.
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()
   ```

6. **`import_view.py`, `ImportView._on_finished`** — the current-worker
   guard, worded like `_on_thumbnails_ready`'s:

   ```python
    def _on_finished(self, worker: ImportWorker) -> None:
        # Ignore a superseded import: a slow earlier load must not report
        # over the one the user actually started, and must not announce a
        # page count for a mutation that was skipped.
        if worker is not self._worker or worker.cancel.is_set():
            return
        if worker.error is not None:
            self.status_label.setText(worker.error)
            self.failed.emit(worker.error)
        else:
            self.status_label.setText(f"Imported {len(worker.pages)} page(s).")
            self.imported.emit(worker.pages, worker.warnings)
   ```

7. **`main.py:1160` and `main.py:1168`, `stop_background_work`** — add
   `self.import_view` to both tuples:

   ```python
        for view in (self.preview_view, self.arrange_view, self.import_view):
   ```

   in both loops. `_live_threads` already works on `ImportView`: it reads
   `view._thread` (set at `import_view.py:198`) and
   `view.widget.findChildren(QThread)` (the thread is parented at
   `import_view.py:195`). No change to `_live_threads` is needed.

8. **`main.py:1140-1159`, `stop_background_work` docstring** — extend the
   first paragraph:

   ```
        Nothing did this, so quitting mid-render left preview, thumbnail and
        import threads running into interpreter teardown -- where they
        called pdfium after it had been finalised and took the process down
        with an access violation.
   ```

   and the "Both workers" sentence becomes "All three workers".

9. **`main.py:1003-1007`, `open_project`** — cancel the in-flight import
   before replacing the state, immediately after `clear_sheet_cache()`:

   ```python
        clear_sheet_cache()
        # An import still running holds the OUTGOING AppState -- it captured
        # it at construction -- so without this it completes, mutates a
        # state nothing reads, and reports a page count for pages that are
        # nowhere. Cancelling is enough; the load is not waited on, because
        # a project should open at once.
        worker = getattr(self.import_view, "_worker", None)
        if worker is not None:
            worker.cancel.set()
        self.state = AppState(project, project_path=path)
        self.import_view.state = self.state
```

10. **`docs/api/`** — no new module. No change.

### Signature changes

```python
def load_and_apply_import(
    state: AppState,
    source_path: str,
    cancel: threading.Event | None = None,
) -> tuple[list[SourcePage], list]
```

`ImportWorker` gains `cancel: threading.Event`. Nothing else changes shape.

### Design choices

- **Chosen:** cancel-only on the `open_project` path, no `wait()`. **Rejected:**
  waiting for the import thread before swapping state — opening a project
  would then block the UI for the length of a 300-page load, which is the
  freeze the thread exists to avoid.
- **Chosen:** guard the mutation inside `load_and_apply_import`, not only in
  `_on_finished`. **Rejected:** guarding only at the Qt layer — the mutation
  happens on the worker thread before `finished` ever fires, so a Qt-side
  guard suppresses the *message* while the orphaned write has already
  happened.

## 4. Tests

`ImportView` constructs fine under pytest (a `QWidget`, not a `QMainWindow`);
`MainWindow` does not (see `tests/test_ui_surface.py:537-541`). So the view
tests go in `tests/test_import_view.py` with its existing module-scoped
`qapp` fixture and `_state()` / `_worker()` helpers, and the shutdown test
goes in `tests/test_shutdown.py`'s subprocess probe.

All GUI tests run with `QT_QPA_PLATFORM=offscreen`, which
`tests/test_import_view.py:22` and `tests/test_shutdown.py:87` already set.

### `tests/test_import_view.py::test_a_superseded_import_does_not_report_over_the_current_one`

```python
def test_a_superseded_import_does_not_report_over_the_current_one():
    """Two quick imports applied in completion order, so a 300-page scan
    started first landed on top of the cover imported second."""
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    _, first = _worker(FIXTURE)
    _, second = _worker(FIXTURE)
    view._worker = second           # `second` is the current one
    first.cancel.set()              # as `import_path` would on supersede

    seen = []
    view.imported.connect(lambda pages, warnings: seen.append(pages))
    view._on_finished(first)

    assert seen == [], "a superseded import reported its result"
    assert "Importing" not in view.status_label.text()
```

Assertion in words: a worker that is not `view._worker` emits nothing.
Expected failure on the unfixed tree: `AttributeError: 'ImportWorker' object
has no attribute 'cancel'` at `first.cancel.set()`.

### `tests/test_import_view.py::test_a_cancelled_import_never_touches_the_project`

```python
def test_a_cancelled_import_never_touches_the_project():
    """`ImportWorker` captures its AppState at construction. Opening a
    project mid-import replaces that state, so the mutation would land in
    an AppState nothing reads."""
    from deckle.app.views.import_view import ImportWorker

    state = _state()
    worker = ImportWorker(state, FIXTURE)
    worker.cancel.set()

    worker.run()

    assert state.project.pages == []
    assert state.can_undo is False, "a cancelled import pushed an undo entry"
```

Expected failure on the unfixed tree: `AttributeError: 'ImportWorker' object
has no attribute 'cancel'`.

### `tests/test_import_view.py::test_cancelling_after_the_load_still_skips_the_mutation`

```python
def test_cancelling_after_the_load_still_skips_the_mutation(monkeypatch):
    """The cancel usually arrives DURING the load -- that is the whole
    window in which `open_project` swaps the state. So the check that
    matters is the one immediately before `mutate`, not the one before the
    load."""
    import threading

    from deckle.app.views import import_view as module

    state = _state()
    cancel = threading.Event()

    def _load_then_cancel(path):
        cancel.set()
        return module._load_source(FIXTURE)

    monkeypatch.setattr(module, "_load_source", _load_then_cancel)

    pages, warnings = module.load_and_apply_import(state, FIXTURE, cancel=cancel)

    assert len(pages) == 2, "the pages are still returned"
    assert state.project.pages == [], "but they were not applied"
```

Expected failure on the unfixed tree: `TypeError: load_and_apply_import()
takes 2 positional arguments but 3 were given` (`cancel` does not exist).

### `tests/test_import_view.py::test_a_second_import_supersedes_the_first`

```python
def test_a_second_import_supersedes_the_first(monkeypatch):
    """The wiring: `import_path` must set the outgoing worker's cancel,
    the way ArrangeView.request_visible_thumbnails does."""
    from deckle.app.views.import_view import ImportView

    class _FakeThread:
        def __init__(self, parent=None):
            self.finished = _FakeSignal()
        def start(self):
            pass
        def deleteLater(self):
            pass

    class _FakeSignal:
        def __init__(self):
            self.slots = []
        def connect(self, slot):
            self.slots.append(slot)

    view = ImportView(_state())
    view._QThread = _FakeThread

    view.import_path(FIXTURE)
    first = view._worker
    view.import_path(FIXTURE)

    assert first.cancel.is_set()
    assert view._worker is not first
```

Expected failure on the unfixed tree: `AttributeError: 'ImportWorker' object
has no attribute 'cancel'`.

(`_FakeSignal` must be defined before `_FakeThread` uses it — hoist both to
module level in the test file, matching `tests/test_hardening_printing.py:136-156`.)

### `tests/test_import_view.py::test_the_import_thread_is_freed_when_it_finishes`

```python
def test_the_import_thread_is_freed_when_it_finishes():
    """Threads are parented to the widget, so without deleteLater they pile
    up for the life of the window -- the same leak the 2026-08-04 entry
    records for the preview and thumbnail views."""
    import ast
    from pathlib import Path

    from deckle.app.views import import_view as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "thread.finished.connect(thread.deleteLater)" in source
```

Expected failure on the unfixed tree: `AssertionError`.

### `tests/test_shutdown.py::test_an_import_in_flight_is_settled_by_stop_background_work`

Extend the existing `PROBE` string. Add a new `how` branch and a new test.
The probe already fakes out printers and builds a real `MainWindow` in a
subprocess, which is the only place a `MainWindow` can exist here.

Add to `PROBE`, after the `for _ in range(12):` churn loop:

```python
elif how == "import_running":
    window.import_view.import_path(sys.argv[1])
    window.stop_background_work()
    running = [t for t in am._live_threads(window.import_view) if t.isRunning()]
    print("still running:", running)
    print("import cancelled:", window.import_view._worker.cancel.is_set())
    window.close()
```

and the test:

```python
def test_an_import_in_flight_is_settled_by_stop_background_work():
    """Import threads were skipped by `stop_background_work` entirely, so
    quitting mid-import ran pdfium into interpreter teardown -- the crash
    class `_live_threads` exists to prevent."""
    result = _run("import_running")

    assert result.returncode == 0, (
        f"quitting mid-import crashed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-1500:]}"
    )
    assert "still running: []" in result.stdout, (
        f"an import thread survived stop_background_work.\nstdout:\n{result.stdout}"
    )
    assert "import cancelled: True" in result.stdout
```

Expected failure on the unfixed tree: the probe raises
`AttributeError: 'ImportWorker' object has no attribute 'cancel'` inside
`stop_background_work`'s `getattr(view, "_worker", None)` branch — no, more
precisely, `stop_background_work` never reaches `import_view` at all, so the
probe's own `window.import_view._worker.cancel` line raises and the
subprocess exits non-zero, failing the first assert with the traceback in
stderr.

### `tests/test_shutdown.py::test_stop_background_work_covers_every_worker_view`

A cheap structural guard, in the same file, using its existing
`_FakeView`/`_FakeThread` doubles:

```python
def test_stop_background_work_covers_every_worker_view():
    """Three views spawn QThreads; the shutdown iterated two of them."""
    from pathlib import Path

    import deckle.app.main as app_main

    source = Path(app_main.__file__).read_text(encoding="utf-8")
    assert source.count(
        "for view in (self.preview_view, self.arrange_view, self.import_view):"
    ) == 2, "stop_background_work does not cover all three worker views"
```

Expected failure on the unfixed tree: `AssertionError: stop_background_work
does not cover all three worker views` (the count is 0).

### Existing tests that must be updated deliberately

`tests/test_import_view.py::test_a_successful_import_reports_the_page_count`
(line 70), `::test_a_successful_import_emits_pages_and_warnings` (line 80),
`::test_a_failed_import_says_so_rather_than_claiming_zero_pages` (line 142)
and `::test_a_failed_import_emits_failed_and_not_imported` (line 156) all
build a fresh `ImportView` and call `view._on_finished(worker)` on a worker
the view has never seen — so the new guard makes every one of them return
early and go silent.

Fix each by adopting the worker first, one line before the `_on_finished`
call:

```python
    view._worker = worker
    view._on_finished(worker)
```

This is the correct update, not a weakening: `_on_finished` is only ever
reached in production via `thread.finished` for a worker the view *is*
holding. Note `_worker(...)` (the module helper at line 50) returns
`(state, worker)`, so the tests already have the worker in hand.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new import-view tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_import_view.py -k "superseded or cancelled or supersedes or freed"` |
| The whole import-view file passes, updated tests included | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_import_view.py` |
| Shutdown, including the new probe branch | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_shutdown.py` |
| State and worker suites | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py tests/test_view_workers.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| The import thread is freed | `grep -c "thread.finished.connect(thread.deleteLater)" deckle/app/views/import_view.py` returns 1 — currently 0 |
| All three views are cancelled and waited on | `test "$(grep -c 'for view in (self.preview_view, self.arrange_view, self.import_view):' deckle/app/main.py)" -eq 2` — currently 0 |
| The two-view spelling is gone | `! grep -n "for view in (self.preview_view, self.arrange_view):" deckle/app/main.py` — currently hits 1160 and 1168 |
| `open_project` cancels the import | `grep -n "worker.cancel.set()" deckle/app/main.py` returns a line inside `open_project` |
| The guard matches the house pattern verbatim | `grep -c "if worker is not self._worker or worker.cancel.is_set():" deckle/app/views/import_view.py deckle/app/views/arrange_view.py deckle/app/views/preview_view.py` returns 1 for each |

## 6. Out of scope

- **B33** — the loader holding the pdfium lock while it SHA-256s a whole
  file, and every image's PDF bytes in memory before merging. This spec adds
  a cancel token; it does not make the load itself interruptible partway
  through, so a cancelled 300-page import still finishes reading before it
  returns. Making `load_pdf` itself check a token is B33's territory.
- **B26** — image-folder imports storing the temp cache file as the source.
- **N6** — page-range selection at import.
- **N8** — drag-and-drop import.
- **B35 §4** — the replaced `AppState`'s live debounce timer. `open_project`
  gains one `worker.cancel.set()` here; the autosave timer is B35's.
- **M3** — moving `_live_threads`/`stop_background_work` into
  `app/shutdown.py`. Leave them in `main.py`.
- Do not add a progress bar or a Cancel button to `ImportView`.

## 7. decisions.md entry

```
## 2026-09-05 — The import worker had none of the guards the other two workers have
- Symptom: `ImportView` spawned a QThread per import with no supersede, no current-worker check on completion, no `deleteLater`, and no cancel token. Three failures: two quick imports applied in COMPLETION order, so a 300-page scan started first landed on top of the cover imported second; `open_project` swapped `AppState` while a worker held the outgoing one, so the import mutated a state nothing reads and the status bar announced pages that were nowhere; and `stop_background_work` iterated only the preview and arrange views, so quitting mid-import ran pdfium into interpreter teardown — the exact crash class `_live_threads` was written to prevent.
- Fix: `ImportWorker` gains a `threading.Event` cancel token checked before the load and again immediately before the `mutate`; `import_path` supersedes the previous worker and connects `deleteLater`; `_on_finished` gains the `worker is not self._worker or worker.cancel.is_set()` guard verbatim from `ArrangeView._on_thumbnails_ready`; `open_project` cancels the in-flight import before replacing the state; `stop_background_work` now iterates all three views.
- Surfaces: The mutation guard has to be inside `load_and_apply_import`, not in `_on_finished` — the write happens on the worker thread before `finished` ever fires, so a Qt-side guard suppresses the message while the orphaned write has already landed.
- Watch: The 2026-08-04 entry fixed exactly this for the preview and thumbnail workers and did not ask which other view spawned threads. When a pattern is established as a fix, grep for every site that should have it rather than fixing the ones that crashed.
- Commit: <fill in>
```

## 8. Traps

- **`ImportWorker` captures `state` at construction.** Re-pointing
  `self.import_view.state` does nothing for a worker already running. That
  is why step 9 cancels rather than reassigns.
- **`tests/test_import_view.py` calls `_on_finished` on an unadopted
  worker in four places.** The new guard silences them; they must be updated
  as §4 says. Skipping this turns four passing tests into four *silently*
  passing tests that assert on empty lists — check they still fail if you
  revert the fix.
- **A real `QMainWindow` cannot be constructed under pytest here** (exit 127
  with the offscreen platform). The `stop_background_work` behaviour test
  must go in `tests/test_shutdown.py`'s subprocess `PROBE`, not in
  `tests/test_import_view.py`.
- **`_live_threads` already handles `ImportView`** — it reads `_thread` and
  `widget.findChildren(QThread)`, both of which `ImportView` has. Do not
  add a special case.
- **The `stop_background_work` loops are separate on purpose**: cancel every
  view first, then wait for every view. Do not merge them, or the third
  view's worker only learns it is cancelled after the first two have been
  waited out.
- **`load_and_apply_import` is called directly by three tests in
  `tests/test_app_state.py`** (lines 192, 240, 252) with two arguments. The
  `cancel=None` default keeps them working; do not make the parameter
  required.
- **`python -m deckle` launches the GUI and blocks.** The shutdown probe
  uses `python -c` with an explicit `os._exit`-free script and a `close()`;
  follow `tests/test_shutdown.py`'s existing shape.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
</content>
