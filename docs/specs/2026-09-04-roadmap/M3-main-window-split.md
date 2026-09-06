# M3 — Split MainWindow into the five things it is

**Roadmap item:** `docs/ROADMAP.md` M3
**Depends on:** —
**Blocks:** —
**Size:** M
**Decision needed first:** none

---

## 1. Context

`deckle/app/main.py` is 1,205 lines and `MainWindow` is 793 of them
(lines 393-1182, twenty-seven methods). It is five separable jobs sharing
one namespace:

1. **The window** — splitters, buttons, tooltips, status bar, undo/redo
   wiring, shortcuts. `__init__` (400-583) plus
   `_sync_document_actions`, `_on_layout_changed`, `_on_imported`,
   `_on_pages_changed`, `_on_page_selected`, `_refresh_status_message`,
   `_install_shortcuts`, `undo`, `redo`, `_after_history_change`,
   `_sync_history_actions`, `show`, `close`, `_on_close_event`.
2. **Printer enumeration** — a worker, a deadline arbiter, three
   patchable Qt seams and two constants (lines 65-88, 91-220, 272-287,
   674-753). This is the code that once hung the app for 81 minutes on
   launch and it is the most carefully reasoned code in the file; it has
   nothing to do with the window except that the window owns the button
   it enables.
3. **Project I/O** — open, save, Save PDF, recent-files menu, and the
   three-way failure reporting for a project whose sources went missing or
   changed (lines 351-365, 770-783, 897-1110). 260 lines.
4. **Autosave recovery** — `autosave_recovery_offer` (309-348) is pure,
   Qt-free, and belongs beside `autosave_path_for`, which it calls and
   which lives in `deckle/app/state.py:46-60`. It is in `main.py` only
   because that is where the prompt was written.
5. **Shutdown** — `_live_threads` (223-269) and `stop_background_work`
   (1140-1182), the fix for the crash-on-exit that took roughly one run in
   four. `_live_threads` is a pure function of a duck-typed view and is
   already tested as one (`tests/test_shutdown.py:195-228` builds
   `_FakeView`/`_FakeThread` objects).

The cost is not aesthetic. Three of the five have tests that reach into
`deckle.app.main` and monkey-patch module attributes to get at them
(`tests/test_hardening_printing.py:369-393`, `tests/gui_workflow.py:50`,
`tests/test_shutdown.py:36-40`), and two more call unbound methods against
hand-built fake windows (`tests/test_autosave_recovery.py:107`,
`tests/test_hardening_printing.py:296-330`). The tests have already
worked out that these are functions over a window-shaped object; the
module has not.

For a person printing a book this changes nothing directly. What it
changes is that B9 (autosave path never re-derived after Save), B10
(closing discards a never-saved project), B13 (import races,
`stop_background_work` skips import threads), B15 and B17 all land in
this file, and four of them land in *different* parts of it.

## 2. Current code

### The module surface, with line ranges

| Symbol | Lines | Destination |
|---|---|---|
| `LETTER_PT` | 37 | stays |
| `NO_PRINTERS_MESSAGE` | 39 | stays |
| `NO_DOCUMENT_MESSAGE` | 41-47 | stays |
| `NOTHING_TO_SAVE_MESSAGE` | 49 | `project_actions.py` |
| `SAVE_PROJECT_TOOLTIP` | 51-55 | stays (a button tooltip set in `__init__`) |
| `NOTHING_TO_EXPORT_MESSAGE` | 57-63 | `project_actions.py` |
| `PRINTER_TIMEOUT_MESSAGE` | 65-73 | `printer_query.py` |
| `DEFAULT_PROFILE` | 75-78 | stays (B15 changes its use, not its home) |
| `PRINTER_QUERY_TIMEOUT_MS` | 80-88 | `printer_query.py` |
| `_PrinterQueryWorker` | 91-121 | `printer_query.py` |
| `_PrinterQuery` | 124-201 | `printer_query.py` |
| `_single_shot` | 204-213 | `printer_query.py` |
| `_new_thread` | 216-220 | `printer_query.py` |
| `_live_threads` | 223-269 | `shutdown.py`, renamed `live_threads` |
| `available_printer_names` | 272-287 | `printer_query.py` |
| `default_project` | 290-296 | stays |
| `_qt_vertical` | 299-306 | stays |
| `autosave_recovery_offer` | 309-348 | `deckle/app/state.py` |
| `_recent_label` | 351-358 | `project_actions.py`, renamed `recent_label` |
| `_new_menu` | 361-365 | `project_actions.py`, renamed `new_menu` |
| `_qt_message_box` | 368-372 | `project_actions.py`, renamed `qt_message_box` |
| `_qt_widgets` | 375-390 | stays |
| `MainWindow` | 393-1182 | stays, shrunk |
| `main` | 1185-1199 | stays |

### `MainWindow`'s methods, with line ranges and callers

| Method | Lines | Called from | Destination |
|---|---|---|---|
| `__init__` | 400-583 | `main()`, `tests/gui_workflow.py:56, 222`, `tests/test_shutdown.py` PROBE | stays |
| `_sync_document_actions` | 585-602 | `__init__:580`, `_on_imported:611`, `_on_pages_changed:636`, `open_project` (via `_on_pages_changed`) | stays |
| `_on_layout_changed` | 604-607 | connected at `:557` | stays |
| `_on_imported` | 609-620 | connected at `:554`; `tests/gui_workflow.py:68`, `tests/test_shutdown.py` PROBE | stays |
| `_on_pages_changed` | 622-638 | connected at `:555`; `_after_history_change:834`; `open_project:1010` | stays |
| `_on_page_selected` | 640-656 | connected at `:556` | stays |
| `_refresh_status_message` | 658-672 | `_on_imported:619`, `_on_pages_changed:637`, `_apply_printers:753`; `tests/test_hardening_printing.py:297` | stays |
| `refresh_printers` | 674-725 | `__init__:583`; patched wholesale by `tests/gui_workflow.py:51` and `tests/test_shutdown.py` PROBE; AST-parsed by `tests/test_hardening_printing.py:96-130`; called directly at `:227, :393` | **stays** — see §3 |
| `_apply_printers` | 727-753 | `refresh_printers:710, 717`; `tests/test_hardening_printing.py:296-330`; `tests/gui_workflow.py:52` | stays |
| `_on_print_clicked` | 755-768 | connected at `:559` | stays |
| `suggested_export_name` | 770-783 | `_on_save_pdf_clicked:1075` only | `project_actions.py` |
| `_install_shortcuts` | 785-800 | `__init__:582` | stays |
| `undo` / `redo` | 802-820 | connected at `:561-562`; `_install_shortcuts` | stays |
| `_after_history_change` | 822-835 | `undo:810`, `redo:820` | stays |
| `_sync_history_actions` | 837-840 | `__init__:581`, `_on_imported:614`, `_on_pages_changed:638`, `_after_history_change:835` | stays |
| `_recover_autosave_if_offered` | 842-882 | `open_project:994`; `tests/test_autosave_recovery.py:107` | `project_actions.py` |
| `_default_confirm_recovery` | 884-895 | `__init__:572` | `project_actions.py` |
| `_refresh_recent_menu` | 897-921 | `__init__:579`, `open_project:997`, `_on_save_project_clicked:1058` | `project_actions.py` |
| `_on_open_project_clicked` | 923-943 | connected at `:563` | `project_actions.py` |
| `open_project` | 945-1015 | `_on_open_project_clicked:943`, the recent-menu lambda `:917`; `tests/gui_workflow.py:223` | `project_actions.py`, thin method kept |
| `_on_save_project_clicked` | 1017-1060 | connected at `:564` | `project_actions.py` |
| `_on_save_pdf_clicked` | 1062-1110 | connected at `:560` | `project_actions.py` |
| `show` | 1112-1117 | `main():1198` | stays |
| `close` | 1119-1127 | `tests/test_shutdown.py` PROBE | stays |
| `_on_close_event` | 1129-1138 | assigned at `:419` | stays |
| `stop_background_work` | 1140-1182 | `close:1126`, `_on_close_event:1137`; `tests/test_shutdown.py:58` | thin method kept, body to `shutdown.py` |

### The parts worth quoting

`deckle/app/main.py:223-269` — pure, no `self`, already tested against a
fake view:

```python
def _live_threads(view) -> list:
    """Every render thread ``view`` still has alive, current or superseded.
    ...
    :param view: a view exposing ``_thread`` and/or a ``widget``.
    :returns: the threads, current first, each appearing once. ...
    """
    from PySide6.QtCore import QThread

    threads: list = []
    seen: set[int] = set()

    current = getattr(view, "_thread", None)
    if current is not None:
        threads.append(current)
        seen.add(id(current))

    widget = getattr(view, "widget", None)
    finder = getattr(widget, "findChildren", None)
    if finder is not None:
        for child in finder(QThread):
            if id(child) not in seen:
                threads.append(child)
                seen.add(id(child))
    return threads
```

`deckle/app/main.py:309-348` — pure, no Qt, calls only
`deckle.app.state.autosave_path_for` and `os`:

```python
def autosave_recovery_offer(project_path: str | None) -> str | None:
    """The autosave worth offering back, or ``None`` to stay silent.
    ...
    """
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

`deckle/app/main.py:1160-1182` — the body that becomes a free function
over a sequence of views:

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
                            log_event(
                                "shutdown_thread_timeout",
                                view=type(view).__name__,
                            )
            except Exception as exc:  # pragma: no cover - defensive
                log_exception("shutdown_wait_failed", exc)
```

### Tests that import a moved symbol by its current path

Grepped (`grep -rn "app.main\|app_main" tests/`):

| Test file:line | What it reaches for | Action |
|---|---|---|
| `tests/test_autosave_recovery.py:22` | `from deckle.app.main import autosave_recovery_offer` | **update** to `deckle.app.state` |
| `tests/test_autosave_recovery.py:99, 107` | `MainWindow._recover_autosave_if_offered.__get__(self)` on a fake window | **update** to `project_actions.recover_autosave_if_offered(window, path, project)` — which is what the test is already doing by hand |
| `tests/test_shutdown.py:196, 214, 223` | `from deckle.app.main import _live_threads` | **update** to `from deckle.app.shutdown import live_threads` |
| `tests/test_shutdown.py:36-40, 59, 67` (PROBE source string) | `am._live_threads(view)`, `am.available_printer_names`, `am.MainWindow.refresh_printers`, `window.stop_background_work()`, `window.close()` | **update** the `_live_threads` and `available_printer_names` lines; the two method calls keep working |
| `tests/gui_workflow.py:50` | `app_main.available_printer_names = lambda: []` | **update** to `printer_query.available_printer_names` — see the trap in §8 |
| `tests/gui_workflow.py:51` | patches `MainWindow.refresh_printers` wholesale | keeps working |
| `tests/test_hardening_printing.py:174-214, 355-375, 634` | `app_main._PrinterQueryWorker`, `app_main._PrinterQuery`, `app_main.PRINTER_TIMEOUT_MESSAGE` | keep working via re-export; the constructor calls do not care where the class was defined |
| `tests/test_hardening_printing.py:369-375, 390` | sets `app_main.available_printer_names = boom` | **update** to `printer_query` — see §8 |
| `tests/test_view_workers.py:191, 209` | `monkeypatch.setattr(app_main, "available_printer_names", ...)` then `app_main._PrinterQueryWorker().run()` | **update** to `printer_query` — otherwise the worker reaches the real spooler |
| `tests/test_view_workers.py:224-230` | patches `app_main.available_printer_names` with a call recorder, then `importlib.reload(app_main)` and asserts nothing was called | **update** the patch target to `printer_query` and reload `deckle.app.printer_query`, or the test asserts nothing |
| `tests/test_ui_surface.py:365` | `monkeypatch.setattr(print_dialog_module, "_available_printer_names", _spy)` | unrelated — that is `print_dialog`'s own copy of the seam (`print_dialog.py:114-117`) and does not move |
| `tests/test_hardening_printing.py:222-227` | `monkeypatch.setattr(app_main, "_single_shot"/"_new_thread", ...)` | keeps working via re-export, because `refresh_printers` stays in `main.py` and resolves those names in `main`'s globals |
| `tests/test_hardening_printing.py:104-130` | AST-parses `main.py` for a `FunctionDef` named `refresh_printers` containing `if blocking:`, `_PrinterQueryWorker`, `_new_thread`, `thread.run = worker.run` | **this is why `refresh_printers` does not move** |
| `tests/test_hardening_printing.py:296-330` | `app_main.MainWindow._apply_printers(window, [])` on a fake window | keeps working |
| `tests/test_ui_surface.py:544-618` | reads `main.py`'s **source text** and asserts on `__init__` (`QSplitter`, `output.setStretchFactor(0, 3)`, `self.splitter.setSizes([440, 840])`, `output.addWidget(self.preview_view.widget)`, and that neither view is added to `central_layout`/`controls_layout`) | `__init__` stays in `main.py`; keeps working |
| `tests/test_integration.py:55-66` | every module basename under `deckle/app/views/` appears in `main.py`'s source | the new modules are under `deckle/app/`, not `deckle/app/views/`; unaffected |
| `tests/test_integration.py:98-179` | AST of `MainWindow.__init__`: instantiates and mounts `ImportView`, `ArrangeView`, `LayoutPanel`, `PreviewView` | `__init__` stays; keeps working |
| `tests/test_integration.py:182-215` | AST of `MainWindow`: constructs `PrintDialog` and has a `.clicked.connect(...)` | `_on_print_clicked` stays; keeps working |
| `tests/test_integration.py:29, 273` | `from deckle.app.main import default_project` | `default_project` stays |

**Choice, stated once:** every symbol a *test* or a *Qt signal* reaches
for by name keeps a home in `main.py` — either the real definition
(`refresh_printers`, `_apply_printers`, `default_project`,
`available_printer_names` as a re-export) or a thin delegating method
(`open_project`, `stop_background_work`, the four `_on_*_clicked`
slots). Three tests change, and only because their target genuinely
moved to a better address: `autosave_recovery_offer` → `state`,
`live_threads` → `shutdown`, and the one attribute that cannot be
re-exported and stay patchable, `available_printer_names`.

## 3. Change

Four commits, in this order. Each is green on its own.

### Commit 1 — `deckle/app/shutdown.py`

New module. Docstring in the house voice; its substance:

> Cancelling and waiting for background renders on the way out. Quitting
> mid-render used to crash about one run in four with `0xC0000409` and no
> Python traceback: a render still inside pdfium when the interpreter
> finalises takes the process down with it. Both views already supported
> cancellation and nobody had ever asked, and nobody waited. Kept out of
> the window because `live_threads` is a function of a duck-typed view
> and is tested as one.

```python
def live_threads(view) -> list:
    """<moved verbatim from main.py:223-269, docstring included>"""


def stop_background_work(views, timeout_ms: int = 5000) -> None:
    """Cancel in-flight renders on ``views`` and wait for their threads.

    <keep main.py:1141-1159's docstring substance: both workers already
    support cancellation; a stuck thread is a worse outcome than an
    abandoned one, so the wait is bounded; never raises, because this runs
    while the app is closing and an exception here would replace a clean
    exit with the crash it exists to prevent.>

    :param views: the views to settle, each exposing ``_worker`` and/or
        ``widget``. Passed in rather than reached for, so a caller that
        grows a third background job -- the layout panel's auto-crop, the
        window's own export -- adds it here and not in a tuple buried in
        a method.
    :param timeout_ms: how long to wait per thread.
    :returns: nothing. Never raises.
    """
```

Body: `main.py:1160-1182` verbatim with `(self.preview_view,
self.arrange_view)` replaced by `views` and `_live_threads` by
`live_threads`.

In `main.py`, `MainWindow.stop_background_work` becomes:

```python
    def stop_background_work(self, timeout_ms: int = 5000) -> None:
        """Cancel in-flight renders and wait for their threads to finish.

        :param timeout_ms: how long to wait per thread.
        :returns: nothing. See :func:`deckle.app.shutdown.stop_background_work`.
        """
        shutdown.stop_background_work(
            (self.preview_view, self.arrange_view), timeout_ms
        )
```

with `from deckle.app import shutdown` at the top of `main.py`. Delete
`_live_threads` from `main.py`; do **not** re-export it — the two places
that use it are the shutdown module and a test, and a stale alias in
`main` is exactly the kind of second address this split exists to remove.

Update `tests/test_shutdown.py`: the three
`from deckle.app.main import _live_threads` imports become
`from deckle.app.shutdown import live_threads`, and the PROBE string's
`am._live_threads(view)` becomes
`__import__("deckle.app.shutdown", fromlist=["live_threads"]).live_threads(view)`
or, more readably, add `import deckle.app.shutdown as sd` near the
PROBE's other imports and call `sd.live_threads(view)`.

Add `docs/api/app.shutdown.rst` and list it in `docs/api/app.rst`.

### Commit 2 — `autosave_recovery_offer` into `deckle/app/state.py`

Move `main.py:309-348` verbatim to `deckle/app/state.py`, immediately
after `autosave_path_for` (which ends at line 60). It needs `import os`
added to `state.py`'s imports (currently `threading`, `collections`,
`dataclasses`, `typing` — see `state.py:27-35`).

`state.py`'s module docstring says "This module must not import PySide6".
`autosave_recovery_offer` does not, so the constraint holds; add a
sentence to the moved function's docstring noting it lives here because
`autosave_path_for` does and two spellings of `<project>.autosave` would
be a rule expressed twice — which is what `autosave_path_for`'s own
docstring already says.

In `main.py`, `_recover_autosave_if_offered` imports it from the new
home. Update `tests/test_autosave_recovery.py:22`.

Do **not** re-export from `main.py`.

### Commit 3 — `deckle/app/printer_query.py`

Move, verbatim: `PRINTER_TIMEOUT_MESSAGE` (65-73),
`PRINTER_QUERY_TIMEOUT_MS` (80-88), `_PrinterQueryWorker` (91-121),
`_PrinterQuery` (124-201), `_single_shot` (204-213), `_new_thread`
(216-220), `available_printer_names` (272-287).

Module docstring substance:

> Asking the OS what printers exist, without letting it hang the app.
> `QPrinterInfo.availablePrinters()` enumerates network printers and the
> spooler blocks per printer until it times out when one is unreachable —
> 21ms with the network up, 81 minutes without it, measured. So the query
> runs on a thread *and* under a deadline, and neither half is enough
> alone: a background thread parked in a driver call is still a hang, it
> has only moved where. The window owns the button this enables; this
> module owns the question.

`main.py` then carries:

```python
from deckle.app.printer_query import (  # noqa: F401
    PRINTER_QUERY_TIMEOUT_MS,
    PRINTER_TIMEOUT_MESSAGE,
    _PrinterQuery,
    _PrinterQueryWorker,
    _new_thread,
    _single_shot,
    available_printer_names,
)
```

The `noqa: F401` is load-bearing and needs the comment above it: these
names are re-exported because `MainWindow.refresh_printers` resolves
`_single_shot`, `_new_thread` and `_PrinterQueryWorker` in **this
module's** globals, which is what makes
`monkeypatch.setattr(app_main, "_single_shot", ...)`
(`tests/test_hardening_printing.py:222-224`) work.

**`refresh_printers` and `_apply_printers` stay on `MainWindow`**, at
lines 674-753, unchanged. `refresh_printers` disables the print button and
writes the status bar; `_apply_printers` is the "enable **only** Print"
decision. Neither is a question about printers. Also,
`tests/test_hardening_printing.py:104-130` parses `main.py`'s AST for a
`FunctionDef` named `refresh_printers` and asserts that
`available_printer_names` appears only inside the `if blocking:` branch —
the structural statement of the 81-minute hang — so moving it would
delete that guard.

Rejected: moving `refresh_printers` too and rewriting that AST test to
parse `printer_query.py`. The test asserts a property of *the window's*
launch path; pointing it somewhere else weakens it.

Add `docs/api/app.printer_query.rst` and list it.

Update every site that **assigns** to `available_printer_names` to patch
`deckle.app.printer_query.available_printer_names`. There are six, in
four files:

```
$ grep -rn "available_printer_names" tests/
tests/test_shutdown.py:37            am.available_printer_names = lambda: []      # inside PROBE
tests/test_view_workers.py:191       monkeypatch.setattr(app_main, ...)
tests/test_view_workers.py:209       monkeypatch.setattr(app_main, ...)
tests/test_view_workers.py:226       monkeypatch.setattr(app_main, ...)
tests/gui_workflow.py:50             app_main.available_printer_names = lambda: []
tests/test_hardening_printing.py:370 app_main.available_printer_names = boom
tests/test_hardening_printing.py:390 monkeypatch.setattr(app_main, ...)
```

(plus three read-only or textual references at
`tests/test_hardening_printing.py:99, 121, 375` and one unrelated hit at
`tests/test_ui_surface.py:365`, which is `print_dialog`'s own
`_available_printer_names` seam and does not move). See §8 for why a
re-export does not cover assignment.

### Commit 4 — `deckle/app/project_actions.py`

Free functions over a window-shaped object, matching what the tests
already do by hand (`tests/test_autosave_recovery.py:95-107` builds a
`_Window` with `confirm_recovery`, `status_bar` and nothing else, then
binds an unbound `MainWindow` method to it). Chosen over a
`ProjectActions` class holding a back-reference: the functions already
take exactly one implicit argument, and a class would add a lifetime and
a construction order to something that has neither. Rejected: leaving
them as methods and merely reordering `main.py` — that is not a seam.

Constants that move: `NOTHING_TO_SAVE_MESSAGE` (49),
`NOTHING_TO_EXPORT_MESSAGE` (57-63). Both are consumed by these
functions; `main.py` re-exports them because `_sync_document_actions`
(585-602) uses them for tooltips.

| New function | From | Signature |
|---|---|---|
| `recent_label(path)` | `_recent_label`, 351-358 | `(str) -> str`, pure |
| `new_menu(parent)` | `_new_menu`, 361-365 | patchable `QMenu` seam |
| `qt_message_box()` | `_qt_message_box`, 368-372 | patchable `QMessageBox` seam |
| `suggested_export_name(pages)` | `suggested_export_name`, 770-783 | **takes the page list, not the window** — it reads nothing else (`self.state.project.pages` at 779); this makes it pure and directly testable |
| `refresh_recent_menu(window)` | `_refresh_recent_menu`, 897-921 | reads `window._recent_menu`, `window.recent_button`, calls `window.open_project` |
| `recover_autosave_if_offered(window, path, project)` | `_recover_autosave_if_offered`, 842-882 | reads `window.confirm_recovery`, `window.status_bar` |
| `default_confirm_recovery(window, project_name)` | `_default_confirm_recovery`, 884-895 | reads `window.window` |
| `choose_and_open_project(window)` | `_on_open_project_clicked`, 923-943 | the file dialog |
| `open_project(window, path)` | `open_project`, 945-1015 | the whole load-and-swap |
| `save_project_as(window)` | `_on_save_project_clicked`, 1017-1060 | |
| `save_pdf_as(window)` | `_on_save_pdf_clicked`, 1062-1110 | |

`main.py` keeps these thin methods, because a signal or a test connects
to each by name:

```python
    def suggested_export_name(self) -> str:
        """A default filename derived from the first imported source."""
        return project_actions.suggested_export_name(self.state.project.pages)

    def open_project(self, path: str) -> bool:
        """Load ``path`` into the window.

        :param path: the ``.deckle`` to open.
        :returns: whether it opened. See
            :func:`deckle.app.project_actions.open_project`.
        """
        return project_actions.open_project(self, path)

    def _refresh_recent_menu(self) -> None:
        project_actions.refresh_recent_menu(self)

    def _recover_autosave_if_offered(self, path, project):
        return project_actions.recover_autosave_if_offered(self, path, project)

    def _default_confirm_recovery(self, project_name: str) -> bool:
        return project_actions.default_confirm_recovery(self, project_name)

    def _on_open_project_clicked(self) -> None:
        project_actions.choose_and_open_project(self)

    def _on_save_project_clicked(self) -> None:
        project_actions.save_project_as(self)

    def _on_save_pdf_clicked(self) -> None:
        project_actions.save_pdf_as(self)
```

`main.py` loses these imports, which move to `project_actions.py`:
`warnings`, `from deckle.core.export import clear_sheet_cache, export`,
`from deckle.core.outputs import describe_write_failure,
output_path_problem`, the five names from `deckle.core.project_io`, and
`from deckle.core import recent`. It keeps `os`, `logging`, `AppState`,
`log_event`/`log_exception`, the five view imports, `BUILTIN_PRESETS`,
`LayoutSettings`, `Project`, `locate_page`.

**`open_project` mutates the window in six ways** (`main.py:1003-1014`) —
`clear_sheet_cache()`, a new `AppState`, then `import_view.state`,
`arrange_view.state`, `layout_panel.state`, `arrange_view.refresh()`,
`layout_panel.refresh_from_project()`, `_on_pages_changed()`. Move that
block verbatim; do not "improve" it here. B13 (a project swap landing
mid-import) is a separate spec and this is where it will be fixed.

Module docstring substance:

> Opening, saving and exporting the job. Free functions over the window
> rather than methods on it, because that is what they already are: each
> reads a handful of the window's attributes and writes the status bar,
> and the tests that exercise them build a five-line fake window and bind
> the unbound method to it. The two ways a project outlives its sources
> are reported differently on purpose — a missing file is obvious once
> named; a source that still exists but has *changed* is the dangerous
> one, because nothing looks wrong and imposing it would use content the
> user has never reviewed.

Add `docs/api/app.project_actions.rst` and list it.

Update `tests/test_autosave_recovery.py`'s `_Window` class (lines 95-107)
to call `project_actions.recover_autosave_if_offered(self, path,
project)` instead of binding the unbound method. Nothing else in that
file changes.

### Expected shape afterwards

`main.py` ~640 lines, `MainWindow` ~430 (the window and nothing else);
`project_actions.py` ~290; `printer_query.py` ~180; `shutdown.py` ~90;
`state.py` +45.

## 4. Tests

This is a refactor: the existing suite is the specification, and the new
tests exist to make the seams *addressable* rather than to describe new
behaviour.

### Before touching anything

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_shutdown.py tests/test_autosave_recovery.py \
  tests/test_hardening_printing.py tests/test_integration.py \
  tests/test_ui_surface.py tests/test_gui_workflow.py \
  tests/test_recent.py tests/test_app_state.py \
  -q --no-header -p no:cacheprovider
```

Record the count. Every one of these must still pass at every commit,
with only the four test edits named in §2 and §3.

### New: `tests/test_project_actions.py`

Now possible without a `QMainWindow`, which is why the split is worth
testing at all: `tests/test_ui_surface.py:534-541` records that a real
`QMainWindow` under pytest kills the interpreter here, so none of this
code has ever had a direct test.

Build a fake window the way `tests/test_autosave_recovery.py:95-107` and
`tests/test_hardening_printing.py:246-296` already do — an object with
`state`, `status_bar.showMessage`, `import_view`/`arrange_view`/
`layout_panel` stubs exposing `state`, `refresh()` and
`refresh_from_project()`, a `_on_pages_changed()` recorder, and
`confirm_recovery`.

| Test | Assertion in words | Failure before the change |
|---|---|---|
| `test_the_suggested_name_never_matches_the_source` | `suggested_export_name(pages)` for `book.pdf` is `book-deckle.pdf` | `ImportError` — it is a method on a class that cannot be constructed under pytest |
| `test_the_suggested_name_with_nothing_imported` | `suggested_export_name([]) == "deckle-output.pdf"` | same |
| `test_a_recent_label_names_the_file_then_its_folder` | `recent_label("/a/b/book.deckle")` contains both `book.deckle` and `/a/b` | same |
| `test_opening_a_project_with_a_missing_source_reports_and_keeps_the_current_one` | write a `.deckle` naming a non-existent PDF; `open_project(window, path)` returns `False`, the message names the missing path, and `window.state` is the object it was | same; today this path is only reachable through a real window |
| `test_opening_a_project_whose_source_changed_says_re_import_it` | same shape with a changed sha256; message contains "has changed since the project was saved" | same |
| `test_opening_a_good_project_swaps_the_state_into_every_view` | `window.import_view.state`, `arrange_view.state` and `layout_panel.state` are all the *new* `AppState`, and `arrange_view.refresh` / `layout_panel.refresh_from_project` / `_on_pages_changed` were each called once | same — and this is the assertion B13 will later need |
| `test_opening_records_the_project_in_the_recent_list` | `recent.record` was called with the path (monkeypatch `deckle.core.recent`) | same |
| `test_declining_recovery_deletes_the_autosave` | (moved from `tests/test_autosave_recovery.py::test_declining_deletes_the_autosave`, calling the free function) | keeps its current meaning |

### New: `tests/test_shutdown.py` additions

The three existing `live_threads` tests move to the new import path.
Add one:

| Test | Assertion | Failure before |
|---|---|---|
| `test_stop_background_work_takes_the_views_it_is_given` | `shutdown.stop_background_work((fake_a, fake_b))` cancels both workers and waits on every thread `live_threads` reports, and `stop_background_work(())` is a no-op | `ImportError: cannot import name 'stop_background_work' from 'deckle.app.shutdown'` |

### New: `tests/test_printer_query.py`

The five `_PrinterQuery` tests currently in
`tests/test_hardening_printing.py:172-214` are the arbiter's tests and
belong beside the arbiter. **Do not move them in this commit** — they
pass unchanged through the re-export and moving them is churn that hides
the refactor. Add only:

| Test | Assertion | Failure before |
|---|---|---|
| `test_the_module_imports_without_qt` | subprocess importing `deckle.app.printer_query` loads no `PySide6*`, copying `tests/test_backend.py::test_backend_module_import_does_not_load_qt` | `ModuleNotFoundError` |
| `test_a_spooler_failure_degrades_to_no_printers` | patch `printer_query.available_printer_names` to raise; `_PrinterQueryWorker().run()` leaves `names == []` and logs `printer_enumeration_failed` | `ModuleNotFoundError`. This is the duplicate of `tests/test_hardening_printing.py:365-375` at the new patch address, and it is what proves the patch site actually works. |

## 5. Acceptance

| Check | Command |
|---|---|
| The four new/updated modules exist | `test -f deckle/app/shutdown.py -a -f deckle/app/printer_query.py -a -f deckle/app/project_actions.py` |
| `autosave_recovery_offer` is in `state.py` and gone from `main.py` | `grep -qE "^def autosave_recovery_offer\(" deckle/app/state.py && ! grep -qE "^def autosave_recovery_offer\(" deckle/app/main.py` |
| `live_threads` is in `shutdown.py` and gone from `main.py` | `grep -qE "^def live_threads\(" deckle/app/shutdown.py && ! grep -q "^def _live_threads(" deckle/app/main.py` |
| `refresh_printers` is still in `main.py` (the AST test's target) | `grep -q "    def refresh_printers(" deckle/app/main.py` |
| `state.py` still imports no Qt | `.venv/bin/python -c "import sys, deckle.app.state; assert not [n for n in sys.modules if n.startswith('PySide6')]"` |
| `printer_query.py` imports no Qt | `.venv/bin/python -c "import sys, deckle.app.printer_query; assert not [n for n in sys.modules if n.startswith('PySide6')]"` |
| `MainWindow` is under 500 lines | `.venv/bin/python -c "import ast,inspect,deckle.app.main as m; src=open(m.__file__).read(); t=ast.parse(src); c=next(n for n in ast.walk(t) if isinstance(n,ast.ClassDef) and n.name=='MainWindow'); n=c.end_lineno-c.lineno; assert n<500, n"` |
| Every module has a docs page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_docs_coverage.py -q --no-header -p no:cacheprovider` |
| The structural window tests still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_surface.py tests/test_integration.py -q --no-header -p no:cacheprovider` |
| The printer-hang guards still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_hardening_printing.py -q --no-header -p no:cacheprovider` |
| Shutdown still exits cleanly | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_shutdown.py -q --no-header -p no:cacheprovider` |
| The whole-window subprocess walk still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_gui_workflow.py -q --no-header -p no:cacheprovider` |
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_project_actions.py tests/test_printer_query.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Baseline for the `MainWindow` size row, on the unfixed tree: the class
spans lines 393-1182, i.e. 789 lines, so the check exits 1 today.

## 6. Out of scope

- **B9** (`autosave_path` computed once in `AppState.__init__` and never
  re-derived after Save), **B10** (no dirty flag, no save-before-close),
  **B13** (import races and `stop_background_work` skipping import
  threads), **B30** (printers enumerated once), **N1** (autosave for
  never-saved projects). All five live in the code this spec moves.
  Move it first, fix it after: a bug fix inside a 790-line class and a
  790-line-class split in one commit is a diff nobody can review.
- **B15** and **B17** both edit `main.py`. See §8 for the ordering.
- **D10** — `main.py`'s module docstring claims printers refresh
  "whenever the printer menu is opened" and there is no such menu. The
  docstring moves nowhere; leave the sentence alone here so the
  documentation pass (D1-D11) can fix it in one place with the rest.
- Making `ImportView` a shutdown participant. `stop_background_work` takes
  its views as an argument after this, which makes that a one-word change
  — but making it is B13.

### Collisions with the other specs in this directory

Seven other specs edit `main.py`. `00-order-and-collisions.md` maps the
B9/B10/B12/B13/B14/B16/B30/B35 slice; this row is what it does not
cover.

| Spec | Methods it touches in `main.py` | Order |
|---|---|---|
| `B9-autosave-path-follows-project-path.md` | a comment in `_on_save_project_clicked` (which M3 moves to `project_actions.py`) | B9 first — it is one line, and M3 then carries it along |
| `B10-N9-dirty-and-save-prompts.md` | `__init__`, `_sync_document_actions`, `_on_layout_changed`, `_after_history_change`, `_recover_autosave_if_offered`, `_default_confirm_recovery`, `_refresh_recent_menu`, `_on_open_project_clicked`, `open_project`, `_on_save_project_clicked`, `close`, `_on_close_event` | **the big one.** Eight of those twelve are methods M3 moves to `project_actions.py`. Land B10 first and let M3 move the finished code, or land M3 first and have B10 edit `project_actions.py` instead — decide once, up front, and say which in both commits. Doing them concurrently is a merge nobody can review. |
| `B13-import-worker-guard-and-shutdown.md` | 3 lines in `open_project`, 2 in `stop_background_work` | either order; both land in code M3 relocates rather than rewrites |
| `B30-requery-printers.md` | module docstring, `refresh_printers`, `show`, `_on_print_clicked` | either order — M3 deliberately leaves all four in `main.py` |
| `B35-miscellany.md` §3 §4 | `_recover_autosave_if_offered`, `open_project` | as B13 |
| `B15-push-resolved-printer-profile.md` | `_apply_printers`, `_on_print_clicked`, `__init__` | M3 first; all three stay in `main.py`, so B15's edits land in the same place either way |
| `B17-off-thread-long-operations.md` | `_on_save_pdf_clicked`, `open_project`, `stop_background_work` | M3 first — `stop_background_work` gaining a `jobs=` argument is a one-line change afterwards and a method rewrite before |

## 7. decisions.md entry

```
## 2026-09-XX — MainWindow was five things in one namespace
- Symptom: `deckle/app/main.py` was 1,205 lines and `MainWindow` 790 of them, holding the window layout, printer enumeration under a deadline, project open/save/export, autosave recovery, and the shutdown cancel-and-wait. Five of the next bugs to fix live in that file and four of them live in different parts of it. The tests had already worked out what the module had not: three of the five were reached by monkey-patching module attributes, and two more by binding an unbound method to a five-line fake window.
- Fix: `deckle/app/printer_query.py` (the worker, the deadline arbiter and the three patchable Qt seams), `deckle/app/project_actions.py` (open, save, Save PDF, recent files, recovery -- free functions over a window-shaped object, which is what they already were), `deckle/app/shutdown.py` (`live_threads` and `stop_background_work`, which now takes its views as an argument), and `autosave_recovery_offer` into `state.py` beside `autosave_path_for`, whose answer it needs.
- Surfaces: `refresh_printers` deliberately stayed on the window. It disables the Print button and writes the status bar, and `tests/test_hardening_printing.py` parses `main.py`'s AST to assert that `available_printer_names` appears only inside its `blocking=True` branch -- the structural statement of the 81-minute launch hang. Moving it would have relocated that guard to a module where it asserts something weaker. The split is by what a function ANSWERS, not by how many lines it has.
- Watch: One test patch site had to change rather than being covered by a re-export. `tests/gui_workflow.py` set `app_main.available_printer_names = lambda: []`, and `_PrinterQueryWorker.run` now resolves that name in `printer_query`'s globals, so patching the old module would have silently stopped working -- the enumeration would have gone to the real spooler and the test would have hung rather than failed. Re-exporting a name keeps `getattr` working and does NOT keep `setattr` working, and the difference is invisible until a slow network makes it a 20-minute test run.
- Commit: <fill in>
```

## 8. Traps

- **A re-export makes a name readable, not patchable.**
  `from deckle.app.printer_query import available_printer_names` in
  `main.py` means `app_main.available_printer_names` still *resolves* —
  so `tests/test_hardening_printing.py:355-360`, which only reads it,
  keeps passing. But `_PrinterQueryWorker.run` calls
  `available_printer_names()` and resolves it in **`printer_query`'s**
  globals, so `app_main.available_printer_names = boom` (line 370) and
  `= lambda: []` (`tests/gui_workflow.py:50`) stop having any effect. The
  failure mode is not a red test: `gui_workflow.py` would reach the real
  spooler and hang, and `tests/test_view_workers.py:187-193` would fail
  with whatever printers the machine happens to have. Patch
  `deckle.app.printer_query.available_printer_names` at all six
  assignment sites, and grep before declaring the commit done —
  `grep -rn "available_printer_names" tests/` returns eleven hits across
  five files today, six of which are assignments (listed in §3, commit 3).
  `_single_shot`, `_new_thread` and `_PrinterQueryWorker` are the
  *opposite* case — `refresh_printers` stays in `main.py` and resolves
  them in `main`'s globals, so the existing `monkeypatch.setattr(app_main,
  ...)` calls keep working precisely because of the re-export.
- **`tests/test_ui_surface.py:544-618` reads `main.py` as text** and
  asserts exact strings from `__init__`, including
  `output.setStretchFactor(0, 3)`, `self.splitter.setSizes([440, 840])`
  and `output.addWidget(self.preview_view.widget)`, plus the *negative*
  `f"{column}.addWidget(self.{panel}" not in source`. Reformatting
  `__init__` breaks them. Do not touch it.
- **`tests/test_integration.py:55-66` requires every module basename
  under `deckle/app/views/` to appear in `main.py`'s source.** The four
  new modules are under `deckle/app/`, not `deckle/app/views/`, so this
  spec is safe — but M1 adds `deckle/app/views/length_spinbox.py`, which
  is not, and needs the exemption M1 §3 step 2 specifies.
- **`window.closeEvent = self._on_close_event` at `main.py:419`** is a
  bound-method assignment onto a `QMainWindow` instance, not an override.
  It must stay in `__init__`; there is no `closeEvent` on `MainWindow`
  itself to move.
- **`stop_background_work` must never raise.** Both loops are wrapped in
  bare `except Exception` with `# pragma: no cover - defensive`. Keep
  both wrappers when the body moves: this runs while the app is closing,
  and an exception here replaces a clean exit with the crash the function
  exists to prevent.
- **`tests/test_shutdown.py` runs its probes in a subprocess** with a
  `PROBE` source string embedded in the test file. Editing the module
  layout means editing that string; a `NameError` inside the probe shows
  up as a non-zero exit code with the traceback in `stderr`, which the
  assertions do print.
- **`_recover_autosave_if_offered` returns the *saved* project when the
  autosave will not load** (`main.py:874-880`), and **declining deletes
  the autosave** (`:864-869`). Both are load-bearing and both have tests
  (`tests/test_autosave_recovery.py:150-202`). Move the body verbatim.
- **Ordering against B15 and B17.** All three edit `main.py`. Recommended:
  **M1 → M3 → B15 → B17**. M3 first because B15 adds a signal connection
  in `__init__` and a `_on_profile_resolved` slot (both in the part that
  stays), and B17 adds jobs to `stop_background_work`'s argument — which
  is a one-line change *after* this spec and a method rewrite before it.
  If B17 goes first, its `deckle/app/workers.py` should own `_new_thread`
  and `printer_query.py` should import it from there; if M3 goes first,
  `_new_thread` lands in `printer_query.py` and B17 moves it on. Say
  which happened in the commit message either way, so the next reader is
  not looking for it in the wrong module.
- `python -m deckle` launches the GUI and blocks. Use
  `python -m deckle.cli` for headless work, and remember that a real
  `QMainWindow` under pytest exits 127 on this machine — the only
  whole-window coverage is `tests/gui_workflow.py`, run as a subprocess.
