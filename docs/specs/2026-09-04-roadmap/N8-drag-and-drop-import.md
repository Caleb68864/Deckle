# N8 — Drop a PDF or a folder on the window to import it

**Roadmap item:** `docs/ROADMAP.md` N8
**Depends on:** —. Edits `deckle/app/main.py` (collides with **M3**, **N4**, **N7**, **N13**, **B9/B10/N9**) and `deckle/app/views/import_view.py` (collides with **B13**, which adds a "is this still the current worker" guard to the same class — land B13 first if it is scheduled, because N8's multi-item import is a *second* reason that guard has to exist). Recommended: **B13 → N7 → N8**.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Dragging a file onto a window is how most people put a document into a desktop
application, and it is the first thing anyone tries with an imposition tool.
Deckle ignores it entirely: nothing in the tree calls `setAcceptDrops`, so the
window shows the "no" cursor and the drop does nothing.

```bash
$ grep -rn "acceptDrops\|dragEnterEvent\|dropEvent" deckle/app
deckle/app/views/arrange_view.py:214:    dragged row in ``startDrag``, AFTER ``dropEvent`` returns, so anything
deckle/app/views/arrange_view.py:404:    is removed in ``startDrag``, AFTER ``dropEvent`` returns, so anything
deckle/app/views/arrange_view.py:423:        def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
deckle/app/views/arrange_view.py:528:        # In icon mode with any non-Static movement, QListView::dropEvent
```

Four hits, all in the arrange grid: one real override, three comments about
it — and `setAcceptDrops` appears exactly once in the tree, on the grid's own
viewport (`arrange_view.py:546`), for internal page reordering. There is no
`dragEnterEvent` anywhere. The window itself accepts nothing.

The cost is not just a missing convenience. The import path today is
"Import PDF..." → a `QFileDialog` that opens with an empty start directory
(`import_view.py:175` passes `""`), so a user whose scan is in `~/Downloads`
navigates there every single time — while the file is already visible in the
file manager they dragged it from.

## 2. Current code

`deckle/app/views/import_view.py:29-49` — the one place a source becomes a
document:

```python
def load_and_apply_import(state: AppState, source_path: str) -> tuple[list[SourcePage], list]:
    """Load ``source_path`` and replace ``state.project.pages`` with it.

    Returns ``(pages, warnings)``. Routed through ``AppState.mutate`` like
    every other project change, so importing participates in undo and
    triggers the same debounced autosave.
    ...
    """
    pages = _load_source(source_path)
    warnings = list(getattr(pages, "warnings", []))
    page_list = list(pages)
    state.mutate(lambda project: replace(project, pages=page_list))
    return page_list, warnings
```

Note **replace**, not append. Two calls in a row leave only the second import's
pages.

`deckle/app/views/import_view.py:23-26`:

```python
def _load_source(path: str) -> Sequence[SourcePage]:
    if os.path.isdir(path):
        return load_image_dir(path)
    return load_pdf(path)
```

`deckle/app/views/import_view.py:76-125` — `ImportWorker`, a plain class whose
`run` records `pages` / `warnings` / `error` and turns `EncryptedPdfError` and
every other `SourceLoadError` into a message.

`deckle/app/views/import_view.py:185-208` — `import_path`, and the completion
handler:

```python
    def import_path(self, source_path: str) -> None:
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

`self._thread` and `self._worker` are overwritten without checking whether a
previous import is still running — **that is B13**, and N8 makes it easier to
hit (a drop of three files, or an impatient second drop).

`deckle/app/main.py:414-419` — where the window is built and where its one
existing event override goes:

```python
        self.window = QMainWindow()
        self.window.setWindowTitle("Deckle")
        # Closing the window is the usual way out, and it does not go
        # through `close()` below -- Qt calls closeEvent directly. Without
        # this, quitting mid-render crashed on exit.
        self.window.closeEvent = self._on_close_event
```

Assigning a bound method onto the instance attribute is the established pattern
here; N8 follows it rather than subclassing `QMainWindow`.

`deckle/app/main.py:554` — `self.import_view.imported.connect(self._on_imported)`;
`main.py:609-620` — `_on_imported(pages, warnings)`, which refreshes the grid,
syncs the actions and re-imposes.

`deckle/core/loader.py:381-430` — `_scan_image_dir`, which returns images in
**natural sort order** via `natsort.natsorted`, with the comment:

```python
    # Natural sort so img1, img2, img10 stay in numeric order rather than
    # the lexicographic img1, img10, img2.
    return natsort.natsorted(images), sorted(skipped)
```

`deckle/core/loader.py:156-174` — `ImportedPages(list)`, carrying `.warnings`.

**Call sites of `load_and_apply_import` (grep):** `import_view.py:29` (definition),
`import_view.py:112` (inside `ImportWorker.run`);
`tests/test_app_state.py:24` (import), and its import tests around line 187.

**Call sites of `import_path` (grep):** `import_view.py:177,183,185`;
`tests/test_import_view.py`, `tests/gui_workflow.py`.

**Existing tests:** `tests/test_import_view.py` (the worker and the view),
`tests/test_app_state.py` (`load_and_apply_import` directly, headless),
`tests/test_loader.py` (`_make_pdf` helper), `tests/test_gui_workflow.py`.

## 3. Change

### What a drop means

**A drop replaces the document with the concatenation of every dropped item, in
one mutation.** Not "import the first and ignore the rest", not "queue N
imports".

- **One PDF** → its pages, exactly as the Import PDF button gives them.
- **One folder** → its images, exactly as Import Images gives them.
- **Several items, mixed** → every item loaded in order and concatenated: a PDF
  contributes its pages, a folder contributes its images in natural sort order.
- **Order** is the dropped paths sorted with `natsort.natsorted` on the full
  path. Qt hands over `QMimeData.urls()` in whatever order the source
  application produced — on most file managers that is *selection* order, which
  is not reproducible and is almost never what someone dragging `ch01.pdf`,
  `ch02.pdf`, `ch10.pdf` means. `natsorted` is already how the image loader
  orders a folder (`loader.py:428-430`), so a folder of `img1, img2, img10` and
  a drop of `img1.pdf, img2.pdf, img10.pdf` behave the same way.

One mutation, so a drop is one step of undo — the same reason `reorder_to`
exists beside `reorder` (`arrange_view.py:112-123`). The rejected alternative,
one `import_path` call per dropped item, is worse three ways: each call
*replaces* the pages so only the last survives, each starts a racing thread
(B13), and the result is N undo entries for one gesture.

### What is accepted

`dragEnterEvent` accepts the drop when **every** URL is a local path that is
either a directory or a file whose suffix is `.pdf` (case-insensitively), and
there is at least one. Not "any": a drag containing one PDF and one `.docx`
that accepts and then silently drops the `.docx` is worse than refusing the
whole gesture, because the user is left believing both arrived.

Images dropped individually are **not** accepted. `load_image_dir` takes a
directory, computes DPI advisories across the set, and spools a normalised PDF
(`loader.py:496`); accepting loose image files would mean a second, parallel
image path. Drop the folder. `dragEnterEvent` refusing is how the user finds
that out, immediately, instead of after a failed import.

### Steps

1. **`deckle/app/views/import_view.py` — a pure, multi-source loader.** Replace
   the body of `load_and_apply_import` with a generalisation and keep the
   single-path name as a wrapper, so nothing else has to change:

   ```python
   def load_and_apply_imports(
       state: AppState, source_paths: Sequence[str]
   ) -> tuple[list[SourcePage], list]:
       """Load every path in order and replace ``state.project.pages`` with
       the concatenation.

       One ``AppState.mutate`` call for the whole set, so a drop of six
       chapters is one step of undo rather than six -- the same reasoning
       ``arrange_view.reorder_to`` records for a drag.

       Loaded in the order given; the caller decides that order. A PDF
       contributes its pages, a directory its images in natural sort order.

       :param state: the app state whose project is replaced.
       :param source_paths: PDF files and/or directories of images.
       :returns: ``(pages, warnings)`` -- warnings from every source,
           concatenated, so an advisory from the third folder is not lost
           behind the first.
       :raises deckle.core.loader.SourceLoadError: from the first source
           that refuses, unchanged and before anything is mutated. A drop
           of six folders where the fourth is empty must not leave the
           document half-replaced.
       """
       page_list: list[SourcePage] = []
       warnings: list = []
       for path in source_paths:
           pages = _load_source(path)
           warnings.extend(getattr(pages, "warnings", []))
           page_list.extend(pages)
       state.mutate(lambda project: replace(project, pages=page_list))
       return page_list, warnings


   def load_and_apply_import(state: AppState, source_path: str) -> tuple[list[SourcePage], list]:
       """Load one source and replace ``state.project.pages`` with it.

       The single-source spelling of :func:`load_and_apply_imports`, kept
       because it is what the two Import buttons mean and what
       ``tests/test_app_state.py`` exercises directly.
       ...unchanged param/return docs...
       """
       return load_and_apply_imports(state, [source_path])
   ```

   Everything is loaded before the single `mutate`, which is what makes the
   "raises before anything is mutated" promise true.

2. **`deckle/app/views/import_view.py` — the worker takes a list.**
   `ImportWorker.__init__(self, state, source_path)` becomes
   `(self, state, source_paths)`, stored as `self.source_paths = list(source_paths)`,
   with `run` calling `load_and_apply_imports(self.state, self.source_paths)`.
   Keep a read-only convenience for the message:

   ```python
       @property
       def source_label(self) -> str:
           """What to call this import in a status message."""
           if len(self.source_paths) == 1:
               return os.path.basename(self.source_paths[0]) or self.source_paths[0]
           return f"{len(self.source_paths)} items"
   ```

   Update the class docstring's `:param:`/`:ivar:` block.

3. **`deckle/app/views/import_view.py` — `import_paths`.** Rename
   `import_path(source_path)` to `import_paths(source_paths)` and keep the
   singular as a wrapper (it is called from `pick_pdf`/`pick_images` and from
   `tests/gui_workflow.py`):

   ```python
       def import_paths(self, source_paths: Sequence[str]) -> None:
           """Kick off one background import covering every path.

           :param source_paths: PDF files and/or directories of images, in
               the order their pages should appear.
           :returns: nothing, immediately. Completion arrives as
               ``imported`` or ``failed``.
           """
           paths = list(source_paths)
           if not paths:
               return
           worker = ImportWorker(self.state, paths)
           self.status_label.setText(f"Importing {worker.source_label}...")
           thread = self._QThread(self.widget)
           thread.run = worker.run
           thread.finished.connect(lambda: self._on_finished(worker))
           self._thread = thread
           self._worker = worker
           thread.start()

       def import_path(self, source_path: str) -> None:
           """Import one source. The singular spelling of
           :meth:`import_paths`.
           ...
           """
           self.import_paths([source_path])
   ```

   If **B13** has landed, `import_paths` carries whatever "is this still the
   current worker" guard B13 added; do not add a second one.

4. **`deckle/app/main.py` — the pure predicate.** Beside the other module-level
   helpers (near `autosave_recovery_offer`, `main.py:309`), so it is testable
   without a drag event:

   ```python
   IMPORTABLE_SUFFIXES: frozenset[str] = frozenset({".pdf"})

   def droppable_paths(paths: Sequence[str]) -> list[str]:
       """The paths a drop should import, or ``[]`` to refuse the drop.

       Accepts a set where **every** entry is a directory of images or a
       ``.pdf``. All-or-nothing on purpose: a drag holding one PDF and one
       ``.docx`` that accepted and then silently ignored the second would
       leave the user believing both arrived, and the missing pages would
       not be noticed until the book was folded.

       Loose image files are refused. ``load_image_dir`` takes a folder --
       it measures DPI across the set and spools one normalised PDF -- so
       accepting individual images would mean a second image path. Drop the
       folder; refusing here is how someone finds that out before the
       import rather than after it.

       Ordered with ``natsort``, not as the file manager handed them over.
       A drag's URL order is usually *selection* order, which is not
       reproducible; ``ch1, ch2, ch10`` is what the person meant, and it is
       the ordering :func:`deckle.core.loader._scan_image_dir` already uses
       inside a folder.

       :param paths: local filesystem paths from the drop.
       :returns: the paths to import, in order, or ``[]``.
       """
       import natsort

       if not paths:
           return []
       for path in paths:
           if os.path.isdir(path):
               continue
           if os.path.splitext(path)[1].lower() in IMPORTABLE_SUFFIXES:
               continue
           return []
       return list(natsort.natsorted(paths))
   ```

   `natsort` is already a runtime dependency (`deckle/core/loader.py:19`), so
   this adds nothing to the dependency closure that
   `tests/test_license_audit.py` and `test_packaging_audit.py` walk.

5. **`deckle/app/main.py` — accept drops.** After
   `self.window.closeEvent = self._on_close_event` (`main.py:419`):

   ```python
           # Dropping a file on a window is how most people open a document,
           # and it is the first thing anyone tries with an imposition tool.
           # Bound onto the instance rather than subclassing QMainWindow,
           # matching how closeEvent is handled directly above.
           self.window.setAcceptDrops(True)
           self.window.dragEnterEvent = self._on_drag_enter
           self.window.dragMoveEvent = self._on_drag_enter
           self.window.dropEvent = self._on_drop
   ```

   `dragMoveEvent` as well as `dragEnterEvent`: Qt asks again on every move
   inside the widget, and a window that accepts the enter and then ignores the
   moves shows the "no" cursor for the whole drag and refuses the drop on some
   platforms.

6. **`deckle/app/main.py` — the two handlers.**

   ```python
       @staticmethod
       def _dropped_paths(event) -> list[str]:
           """Local filesystem paths carried by a drag event.

           Non-local URLs (``http:``, ``ftp:``) yield an empty
           ``toLocalFile()`` and are dropped here, which makes
           :func:`droppable_paths` refuse the whole drag -- correct: Deckle
           opens files, and half-importing a drag from a browser would be
           worse than refusing it.
           """
           mime = event.mimeData()
           if not mime.hasUrls():
               return []
           return [
               url.toLocalFile() for url in mime.urls() if url.toLocalFile()
           ]

       def _on_drag_enter(self, event) -> None:
           """Accept a drag of PDFs and/or image folders, refuse anything else.

           :param event: the ``QDragEnterEvent``/``QDragMoveEvent``.
           :returns: nothing.
           """
           if droppable_paths(self._dropped_paths(event)):
               event.acceptProposedAction()
           else:
               event.ignore()

       def _on_drop(self, event) -> None:
           """Import everything dropped, in one go.

           :param event: the ``QDropEvent``.
           :returns: nothing. The import runs on the existing background
               worker, so a 300-page drop does not block the window, and it
               is a single ``AppState.mutate`` so the whole drop is one step
               of undo.
           """
           paths = droppable_paths(self._dropped_paths(event))
           if not paths:
               event.ignore()
               return
           event.acceptProposedAction()
           log_event("import_dropped", count=len(paths))
           self.import_view.import_paths(paths)
   ```

   Routed through `self.import_view.import_paths` and nothing else — the same
   `ImportWorker`, the same `imported`/`failed` signals, the same
   `_on_imported` handler at `main.py:609`. A drop is not a second import path.

7. **`deckle/app/main.py` — the module docstring.** Add one line to the class
   docstring of `MainWindow`: *"The window accepts drops of PDFs and folders of
   images; see :func:`droppable_paths` for what it will and will not take."*

## 4. Tests

A `QDropEvent` and a `QDragEnterEvent` can both be constructed and inspected
headless — verified on this tree:

```python
mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile("/tmp/a.pdf")])
ev = QDropEvent(QPointF(1, 1), Qt.DropAction.CopyAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
[u.toLocalFile() for u in ev.mimeData().urls()]   # ['/tmp/a.pdf']
```

So the handlers can be driven unbound against a stub, the way
`tests/test_hardening_printing.py:271-297` drives `_apply_printers` — no
`QMainWindow` (which exits 127 under pytest here) and no subprocess.

### `tests/test_drag_and_drop.py` (new)

Fixtures: `tmp_path` with a real `.pdf` written by `tests/test_loader.py::_make_pdf`
(or copied from `tests/fixtures/sample.pdf`), a real directory, and a `.docx`.
`droppable_paths` calls `os.path.isdir`, so the paths must actually exist.

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_a_single_pdf_is_accepted` | one `.pdf` | `droppable_paths([p]) == [p]` | `ImportError: cannot import name 'droppable_paths' from 'deckle.app.main'` |
| `test_an_uppercase_suffix_is_accepted` | `BOOK.PDF` | accepted | as above |
| `test_a_directory_is_accepted` | one directory | accepted | as above |
| `test_a_mixed_set_is_accepted_and_naturally_ordered` | `ch10.pdf`, `ch2.pdf`, `scans/` | result is `["ch2.pdf", "ch10.pdf", "scans/"]`-ordered by `natsort`, **not** the input order | as above |
| `test_one_unsupported_file_refuses_the_whole_drop` | `.pdf` + `.docx` | `== []` | as above |
| `test_loose_images_are_refused` | `a.png` | `== []` | as above |
| `test_an_empty_drop_is_refused` | `[]` | `== []` | as above |
| `test_a_non_local_url_refuses_the_drop` | build a `QMimeData` with `QUrl("https://example.com/a.pdf")` and run `MainWindow._dropped_paths` unbound | `[]`, and `droppable_paths([]) == []` | `AttributeError: type object 'MainWindow' has no attribute '_dropped_paths'` |

### `tests/test_drag_and_drop.py`, the event handlers

A `_FakeWindow` stub with `import_view = SimpleNamespace(import_paths=_Recorder())`
and the three handlers bound from `MainWindow`.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_dragging_a_pdf_accepts_the_action` | build a `QDragEnterEvent` with one `.pdf` URL; call `MainWindow._on_drag_enter(window, event)`; `event.isAccepted()` is `True` | `AttributeError: ... '_on_drag_enter'` |
| `test_dragging_a_word_document_is_ignored` | one `.docx` | `event.isAccepted()` is `False` | as above |
| `test_dropping_imports_every_path_in_one_call` | `QDropEvent` with three URLs | the recorder fired **once**, with a list of three paths in natural order | as above |
| `test_dropping_something_unsupported_imports_nothing` | `.docx` | the recorder did not fire and the event is not accepted | as above |

### `tests/test_import_view.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_importing_several_sources_concatenates_them` | monkeypatch `deckle.app.views.import_view.load_pdf` to return 2 pages for `a.pdf` and 3 for `b.pdf`; `load_and_apply_imports(state, ["a.pdf", "b.pdf"])` | `len(state.project.pages) == 5`, and the first two came from `a.pdf` | `ImportError: cannot import name 'load_and_apply_imports'` |
| `test_a_multi_source_import_is_one_undo_step` | same, on a state with an existing document | `state.can_undo` is `True`, and one `state.undo()` restores the original pages exactly | as above |
| `test_a_failing_source_leaves_the_document_untouched` | second source raises `CorruptPdfError` | the exception propagates and `state.project.pages` is unchanged — nothing was mutated | as above |
| `test_warnings_from_every_source_are_kept` | two image directories, each returning an `ImportedPages` with one warning | both warnings come back | as above |
| `test_the_single_source_spelling_still_works` | `load_and_apply_import(state, "a.pdf")` | 2 pages, and `tests/test_app_state.py`'s existing import tests still pass | passes today; it pins the wrapper |
| `test_import_paths_starts_one_worker_for_the_whole_set` | patch `view._QThread` with a fake recording `start()` calls; `view.import_paths(["a.pdf", "b.pdf"])` | exactly one thread started, and `view._worker.source_paths == ["a.pdf", "b.pdf"]` | `AttributeError: 'ImportView' object has no attribute 'import_paths'` |
| `test_the_status_label_names_the_count_for_a_multi_drop` | as above | `view.status_label.text() == "Importing 2 items..."` | as above |

### `tests/test_integration.py` (extend — its AST pattern)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_window_accepts_drops` | `MainWindow.__init__`'s AST contains a `setAcceptDrops` call | `AssertionError` |

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_drag_and_drop.py` |
| The import tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_import_view.py tests/test_app_state.py tests/test_integration.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The window accepts drops | `grep -q "setAcceptDrops" deckle/app/main.py` |
| A drop is one mutation | `grep -c "state.mutate" deckle/app/views/import_view.py` (expect exactly 1) |
| No second import path | `sed -n '/def _on_drop/,/^    def /p' deckle/app/main.py \| grep -q "self.import_view.import_paths"` and `! sed -n '/def _on_drop/,/^    def /p' deckle/app/main.py \| grep -q "load_pdf\|load_image_dir"` |
| No new runtime dependency | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_license_audit.py` |
| `[HUMAN]` It actually works | Launch Deckle. Drag `tests/fixtures/sample.pdf` from a file manager onto the window: the cursor shows a copy affordance and the pages appear. Drag a `.txt`: the cursor shows "no" and nothing happens. Drag three chapter PDFs at once: they arrive in `ch1, ch2, ch10` order, and one Ctrl+Z removes all of them. |

## 6. Out of scope

- **B13** — no "is this still the current worker" guard on import results, and
  import threads skipped by `stop_background_work`. N8 makes a second overlapping
  import *easier* to trigger (drop, then drop again) but does not introduce the
  race, and fixing it properly means touching `open_project` and
  `_live_threads` too. Land B13 first if it is scheduled.
- **B26** (image-folder imports record the temp cache file as the source).
  Unchanged: a dropped folder goes through the same `load_image_dir`.
- **B33** (whole-file SHA-256 computed under the pdfium lock; every image's PDF
  bytes held in memory). A six-folder drop makes both worse in proportion. Same
  code, same fix, not here.
- **Dropping a `.deckle`.** Tempting and genuinely different: it *replaces* the
  project rather than importing into it, so it needs B10/N9's "save first?"
  prompt. `droppable_paths` refuses it today, which is the safe direction.
- **Loose image files.** Refused, with the reason recorded in
  `droppable_paths`'s docstring. Accepting them means a second image path
  parallel to `load_image_dir`'s DPI advisories and spooling.
- **A drop target on the arrange grid** that inserts at the drop position.
  The grid's `dropEvent` is already load-bearing for internal reordering
  (`arrange_view.py:395-442` explains at length why), and adding an external
  branch to it is its own spec.
- **N7's menu bar.** Independent, but both edit `MainWindow.__init__`.

## 7. decisions.md entry

```
## 2026-09-05 — The window ignored every dropped file
- Symptom: `grep -rn "acceptDrops" deckle/app` found one hit, in the arrange grid's internal page reordering. Dragging a PDF onto Deckle showed the "no" cursor and did nothing, so the only way in was a file dialog that opens with an empty start directory -- meaning a user navigated to ~/Downloads on every single import, for a file already visible in the file manager they were dragging from.
- Fix: `setAcceptDrops(True)` on the window with `dragEnterEvent`/`dragMoveEvent`/`dropEvent` bound onto the instance, matching how `closeEvent` is already handled. `droppable_paths` decides, purely: every entry must be a directory or a `.pdf`, or the whole drag is refused. A drop routes to `ImportView.import_paths`, a new plural spelling that loads every source and calls `AppState.mutate` ONCE, so a six-chapter drop is one step of undo.
- Surfaces: All-or-nothing acceptance. A drag holding one PDF and one .docx that accepted and silently ignored the second would leave the user believing both arrived, and the missing pages would not be noticed until the book was folded. Loose image files are refused for a different reason: `load_image_dir` takes a folder because it measures DPI across the set and spools one normalised PDF, so accepting individual images would mean a second image path.
- Surfaces: Dropped URLs arrive in the file manager's *selection* order, which is not reproducible. `natsort.natsorted` -- already how `_scan_image_dir` orders a folder -- makes `ch1, ch2, ch10` mean what it looks like.
- Watch: `dragMoveEvent` has to be accepted too. Qt re-asks on every mouse move inside the widget, and a window that accepts only the enter shows the "no" cursor for the whole drag and refuses the drop on some platforms.
- Watch: This makes B13 easier to hit -- two quick drops start two workers that apply in completion order.
- Commit: <fill in>
```

## 8. Traps

- **`dragMoveEvent` must be accepted as well as `dragEnterEvent`.** Step 5
  binds the same handler to both; dropping one of them produces a drag that
  looks refused throughout.
- **`QUrl.toLocalFile()` returns `""` for a non-local URL**, not `None` and not
  an exception. Filtering on truthiness is what makes a browser drag refuse
  cleanly instead of importing a file named `""`.
- **`droppable_paths` calls `os.path.isdir`**, so tests must use paths that
  really exist under `tmp_path`. A test with `"/tmp/a.pdf"` that was never
  created still passes the suffix branch — fine for the suffix tests, wrong for
  the directory ones.
- **`load_and_apply_imports` must load everything before it mutates.** A
  `SourceLoadError` from the fourth of six folders has to leave the document
  exactly as it was; mutating per source would half-replace it and put a
  half-import on the undo stack.
- **`ImportWorker.run` catches `SourceLoadError` and records `error`**
  (`import_view.py:111-125`) precisely because a `QThread` has nowhere to
  deliver an exception — `import_view.py:117-125` records that letting them
  propagate made `_on_finished` report a failure as "Imported 0 page(s)". The
  multi-source version must keep that catch.
- **Constructing a real `QMainWindow` under pytest exits 127 here.** Bind the
  handlers unbound against a stub; `QDropEvent`/`QDragEnterEvent`/`QMimeData`
  all construct fine offscreen.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
- **`natsort` is imported inside `droppable_paths`**, not at module top:
  `deckle/app/main.py` is imported by `tests/test_integration.py`'s AST walk and
  by `tests/test_hardening_printing.py`, and keeping module-level imports to
  what is already there avoids widening what those pay for. It is a real runtime
  dependency either way (`loader.py:19`).
