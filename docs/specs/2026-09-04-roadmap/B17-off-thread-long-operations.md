# B17 — Take the four long operations off the GUI thread

**Roadmap item:** `docs/ROADMAP.md` B17
**Depends on:** —
**Blocks:** —
**Size:** M
**Decision needed first:** none

---

## 1. Context

Four operations in Deckle run synchronously on the GUI thread, with no
cancel and no repaint:

| Operation | Where | What it does | How long |
|---|---|---|---|
| Print submission | `print_dialog.py:279-280, 307-308, 312-351` | rasterises every sheet at printer DPI and hands each chunk to the spooler | minutes for a 60-sheet pass |
| Save PDF | `main.py:1097` | composes and writes the whole imposed document | seconds to a minute |
| Open project | `main.py:969` | SHA-256s every source file | seconds on a 300MB scan set |
| Auto-crop | `layout_panel.py:1629` | rasterises **every page** and finds the ink | tens of seconds on a 300-page book |

Each of them freezes the window. Not "is slow" — freezes: the event loop
is inside the call, so nothing repaints, the title bar greys out, and the
OS offers to kill the application. Save PDF is the sharpest example
because the code *tries* to say what it is doing and cannot:

`deckle/app/main.py:1093-1097`:

```python
        plan = self.preview_view.plan
        sheets = len(plan.sheets)
        self.status_bar.showMessage(f"Exporting {sheets} sheet(s) to {os.path.basename(path)}...")
        try:
            export(plan, path)
```

`showMessage` queues a repaint. The repaint happens when control returns
to the event loop, which is after `export` finishes — so the "Exporting…"
message is never on screen for a single frame. The user clicks Save PDF
and the window dies until the file appears.

The print case is the one that costs paper. A 60-sheet front pass is six
chunks of ten sheets; between chunks the dialog wants to show a reload
instruction, and at the end of the pass it must. With the loop on the GUI
thread there is no way to stop a run that is going wrong — the printer is
jamming, the wrong tray is loaded, the user notices the first sheet is
90° out — short of killing the process, which loses the session cursor
and makes the resume prompt guess.

The whole app is already built for this. `ImportView`, `ArrangeView`,
`PreviewView` and the printer query each spawn a `QThread` with a plain
worker object, a `threading.Event` cancel token, and a
"is this still the current worker" guard. The four operations above are
the ones that never got it, and each would be a fourth copy of the same
twenty lines.

## 2. Current code

### The existing pattern, four times over

`deckle/app/views/preview_view.py:741-781` — the most developed copy,
and the one this spec generalises:

```python
    def refresh(self) -> None:
        """Kick off a background render of exactly the visible sheet/side.

        Supersedes any render still in flight. Without that, scrubbing
        sheets quickly left several threads racing and the *last to finish*
        won -- which is not necessarily the one the user is looking at.

        :returns: nothing, immediately; the frame is painted when the
            background thread finishes, and only if it is still current.
        """
        if self._worker is not None:
            self._worker.cancel.set()

        sides = ("front", "back") if self._spread else (self.side,)
        worker = PreviewWorker(
            self.plan, self.profile, self.sheet_index, self.side, sides=sides
        )
        thread = self._QThread(self.widget)
        thread.run = worker.run
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

`deckle/app/views/arrange_view.py:619-636` is the same fifteen lines with
`ThumbnailWorker`; `deckle/app/views/import_view.py:193-200` is the same
without `deleteLater` and without a supersede guard (that omission is
B13); `deckle/app/main.py:713-725` is the same again with a deadline
bolted on.

`ImportWorker.run` also records why the exception handling belongs in the
helper (`import_view.py:99-125`, abridged):

> An unreadable source must not raise inside a `QThread`. Qt has nowhere
> to deliver the exception, so it prints a traceback the user cannot act
> on…

### Save PDF — `deckle/app/main.py:1062-1110`

Dialog, path validation, then the blocking call at `:1097` and three
`except` branches that write the status bar. The validation
(`output_path_problem` at `:1087`) is instant and must stay on the GUI
thread; the `export` call is the only long part.

### Open project — `deckle/app/main.py:945-1015`

```python
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                # Sources normally live somewhere other than the project --
                # Downloads, a scanner folder. Without swallowing the
                # advisory, Python prints a bare warning naming a line
                # inside Deckle, which tells the user nothing they can act
                # on. The containment check still runs; only its rendering
                # changes.
                project = load_project(path, allowed_roots=(os.path.dirname(path),))
            for warning in caught:
                if issubclass(warning.category, PathOutsideRootsAdvisory):
                    log_event("project_source_outside_roots", path=path,
                              detail=str(warning.message))
        except SourceMissingError as exc:
            ...
```

then, at `:994-1015`, the part that must run on the GUI thread: the
recovery prompt, `recent.record`, `clear_sheet_cache()`, a new `AppState`
pushed into three views, `arrange_view.refresh()`,
`layout_panel.refresh_from_project()` and `_on_pages_changed()`.

The hashing is inside `load_project` — `deckle/core/loader.py` computes a
whole-file SHA-256 per source, and B33 records that it does so **while
holding the pdfium lock**, which is a separate defect but explains why an
open blocks the preview as well as the window.

### Auto-crop — `deckle/app/views/layout_panel.py:1624-1645`

```python
    def _on_auto_crop(self) -> None:
        """Fill the crop boxes from where the ink actually is."""
        from deckle.core.render import auto_crop_insets

        try:
            odd, even = auto_crop_insets(self.state.project.pages)
        except Exception as exc:  # noqa: BLE001 -- reported, never a crash
            log_exception("auto_crop_failed", exc)
            self.schedule_saved.emit(f"Could not measure the ink: {exc}")
            return
        for parity, insets in (("odd", odd), ("even", even)):
            if insets is None:
                continue
            for edge, value in zip(("left", "bottom", "right", "top"), insets):
                box = self.crop_spinboxes[(parity, edge)]
                box.blockSignals(True)
                box.setValue(from_points(value, self._unit))
                box.blockSignals(False)
            self._on_crop_changed(parity)
        self.schedule_saved.emit(
            "Crop measured from the ink -- check it before printing."
        )
```

`auto_crop_insets` (`core/render.py:563`) rasterises every non-skipped
page at 36 DPI through `ink_bbox`, which takes `_PDFIUM_LOCK`
(`core/render.py:392`).

### Print submission — `deckle/app/views/print_dialog.py:253-351`

`start_print` builds the session and calls `session.start()` then
`self._drive(session)`; `_offer_resume` does the same via
`session.resume(count)`. `_drive` is the loop:

```python
    def _drive(self, session: PrintSession) -> None:
        """Advance ``session`` through chunks and pass boundaries.
        ...
        """
        if session.last_error is not None:
            self._show_offline_error(session.printer_name, session.last_error)
            return

        if session.state["test_sheet_pending"]:
            if self._confirm_test_sheet():
                session.confirm_test_sheet()
            else:
                return

        prior_pass_index = session.state["pass_index"]
        while not session.finished and session.last_error is None:
            session.advance()
            if session.last_error is not None:
                self._show_offline_error(session.printer_name, session.last_error)
                return
            if session.finished:
                self.status_label.setText("Print job complete.")
                return
            if session.state["pass_index"] != prior_pass_index:
                prior_pass_index = session.state["pass_index"]
                instruction = session.reload_instruction
                if instruction:
                    self._confirm_reload(instruction)
```

`session.advance()` submits **one chunk** (`core/print_session.py:637-648`
→ `_submit_chunk`), default ten sheets (`backend.py:67`). That is the
granularity cancellation can have.

Three of the loop's four interactions are modal dialogs that must run on
the GUI thread: `_confirm_test_sheet`, `_confirm_reload`,
`_show_offline_error` (`print_dialog.py:378-399`). All are injectable
callables so headless tests can drive the whole flow — that injection is
what makes this movable at all.

### The pdfium lock

`deckle/core/render.py:68-98` and `:101-129`. Two sentences in
`pdfium_guard`'s docstring become false when print submission moves:

```
    Three callers outside this module need it, and the *printing* one is
    the reason it is public rather than private. The print dialog runs no
    thread of its own, so a print rasterizes **on the GUI thread** -- and
    printing while the preview is still drawing is an entirely ordinary
    thing to do, with a background render in flight the whole time.
```

and `deckle/app/backend.py:210-214`:

```python
        # Held across the document's whole life, not just the render.
        # pdfium is process-global and this path rasterizes on the GUI
        # thread -- the print dialog runs no thread of its own -- so a
        # print issued while the preview is still drawing puts two threads
        # in pdfium at once, which faults natively rather than raising.
```

Both must be rewritten. The lock itself is right and unchanged: it is an
`RLock`, so a print thread and a preview thread now serialise against
each other rather than faulting. Lock ordering is safe —
`render_sheet` calls `export.export_sheet_cached` (which takes the
export cache's own `threading.Lock`, `export.py:919`) **before** entering
`with _PDFIUM_LOCK` (`render.py:246, 262`), so the two locks are never
nested.

### Existing tests

| Test | What it pins |
|---|---|
| `tests/test_view_workers.py` | the worker contract: cancel stops work, a superseded result is dropped, threads do not accumulate |
| `tests/test_shutdown.py` | `stop_background_work` cancels and waits for **every** live thread, not just the current one; three subprocess probes assert a clean exit |
| `tests/test_print_dialog.py:215-330` and `tests/test_ui_surface.py:361-415` | `dialog.start_print()` followed **immediately** by assertions on the stub session — six tests across two files, each with its own `_dialog(...)` factory, depend on submission being synchronous from the caller's point of view |
| `tests/test_auto_crop.py` | `auto_crop_insets` itself, called directly |
| `tests/test_hardening_io.py`, `tests/test_cli_output_durability.py` | `export`'s failure modes |
| `tests/gui_workflow.py:137-154` | exports the preview's plan directly, not through the button |

```
$ grep -rn "start_print" tests/
tests/test_print_dialog.py:222    tests/test_ui_surface.py:387
tests/test_print_dialog.py:231    tests/test_ui_surface.py:411
tests/test_print_dialog.py:241    tests/test_ui_surface.py:415
tests/test_print_dialog.py:298
```

Every one asserts on state immediately after the call — the
`test_ui_surface.py` three on `dialog._session.sheets`, which is set
inside `start_print` itself.

## 3. Change

### Step 1 — `deckle/app/workers.py`

One helper with the pattern the four views already share. Qt imported
lazily, like every other module in `deckle/app`.

```python
"""Running one slow thing on a thread, the way this app already does it.

Four places spawn a ``QThread`` with a plain worker object, a
``threading.Event`` cancel token and an "is this still the current job"
guard -- ``PreviewView.refresh``, ``ArrangeView``'s thumbnail fetch,
``ImportView.import_path`` and the printer query. Four more needed it and
never got it: print submission, Save PDF, Open project and Auto-crop all
ran on the GUI thread, so a sixty-sheet pass froze the window for
minutes and the "Exporting..." status never painted -- ``showMessage``
queues a repaint that cannot happen until control returns to the event
loop, which is after the work.

The guard matters as much as the thread. Two quick Save PDFs, or a
project opened while another is still loading, apply in completion order
without it, and the slower one wins.
"""

from __future__ import annotations

import threading
from typing import Callable

from deckle.core.diagnostics import log_exception


class Job:
    """One unit of background work, and what came back from it.

    Plain class, not a ``QObject`` -- same shape as ``PreviewWorker`` and
    ``ThumbnailWorker``, so it can be exercised headlessly.

    :param fn: called on the background thread with the cancel event as
        its only argument. Whatever it returns lands on :attr:`result`.
    :ivar result: what ``fn`` returned, or ``None``.
    :ivar error: what ``fn`` raised, or ``None``. Held rather than
        propagated: a ``QThread`` has nowhere to deliver an exception, so
        it prints a traceback the user cannot act on and the operation
        simply appears not to have happened.
    :ivar cancel: set when a newer job supersedes this one, or when the
        user cancels. Passed to ``fn``, which is free to ignore it --
        several of the core operations have no cancellation point yet, and
        for those this means "drop the result" rather than "stop working".
    :ivar thread: the ``QThread`` running it, once started.
    """

    def __init__(self, fn: Callable[[threading.Event], object]) -> None:
        self._fn = fn
        self.result: object = None
        self.error: Exception | None = None
        self.cancel = threading.Event()
        self.thread = None

    def run(self) -> None:
        """Run ``fn``. Never raises.

        :returns: nothing -- results land on :attr:`result` and
            :attr:`error`.
        """
        if self.cancel.is_set():
            return
        try:
            self.result = self._fn(self.cancel)
        except Exception as exc:  # noqa: BLE001 -- a thread, not the UI
            log_exception("background_job_failed", exc)
            self.error = exc


def _new_thread(parent):
    """A ``QThread`` parented to ``parent``. Patchable seam.

    Parented so it cannot leak, and so
    :func:`deckle.app.shutdown.live_threads` finds it through the
    widget's own child list -- there is no second register to drift out
    of sync.
    """
    from PySide6.QtCore import QThread

    return QThread(parent)


def run_in_thread(fn, on_done, on_error, parent, *, previous=None,
                  thread_factory=None) -> Job:
    """Run ``fn`` on a background thread and deliver the result on the GUI one.

    :param fn: ``(cancel: threading.Event) -> result``, run off the GUI
        thread. Must touch no widget.
    :param on_done: ``(result) -> None``, run on the GUI thread when
        ``fn`` returns and the job is still current.
    :param on_error: ``(exc) -> None``, run on the GUI thread when ``fn``
        raised and the job is still current.
    :param parent: the ``QWidget`` to parent the thread to.
    :param previous: the job this one supersedes, cancelled before the
        new one starts. Pass the attribute you are about to overwrite.
    :param thread_factory: injected for tests.
    :returns: the :class:`Job`. **Store it** -- it is what a later call
        passes as ``previous``, what a Cancel button sets, and what
        shutdown waits on.
    """
    job = Job(fn)
    if previous is not None:
        previous.cancel.set()
    thread = (thread_factory or _new_thread)(parent)
    thread.run = job.run
    thread.finished.connect(lambda: _deliver(job, on_done, on_error))
    # Without this they pile up for the life of the parent widget.
    thread.finished.connect(thread.deleteLater)
    job.thread = thread
    thread.start()
    return job


def run_inline(fn, on_done, on_error, parent=None, *, previous=None,
               thread_factory=None) -> Job:
    """Run ``fn`` synchronously, then deliver. Same signature as
    :func:`run_in_thread`.

    For tests and any caller with no event loop to return to -- the same
    seam ``MainWindow.refresh_printers(blocking=True)`` keeps, and for the
    same reason. Behaviourally identical, including the supersede guard,
    so a test that uses it exercises the real delivery path.
    """
    job = Job(fn)
    if previous is not None:
        previous.cancel.set()
    job.run()
    _deliver(job, on_done, on_error)
    return job


def _deliver(job: Job, on_done, on_error) -> None:
    """Hand a finished job's outcome to the GUI, unless it was superseded."""
    if job.cancel.is_set():
        return
    if job.error is not None:
        on_error(job.error)
        return
    on_done(job.result)
```

Chosen for the supersede guard: **cancel the previous job and drop any
result whose `cancel` is set.** Rejected: `PreviewView`'s identity check
(`worker is not self._worker`), which needs the helper to know where the
caller stores the job. Setting `cancel` on supersede is what the identity
check is *for*, and it is one fact instead of two. The existing views
keep their own guard; they are not rewritten here.

Add `docs/api/app.workers.rst` and list it in `docs/api/app.rst`.

**Collision with M3.** M3 moves the current `main._new_thread` into
`deckle/app/printer_query.py`. If M3 lands first, delete
`printer_query._new_thread` and have `printer_query` import it from
`deckle.app.workers`; if B17 lands first, M3 leaves `_new_thread` where
this spec puts it. Either way there is exactly one `_new_thread` in
`deckle/app/` when both are done — say which order happened in the
commit message. `main.py` must keep re-exporting the name it re-exports,
because `tests/test_hardening_printing.py:224` does
`monkeypatch.setattr(app_main, "_new_thread", ...)`.

### Step 2 — shutdown participation

Extend `stop_background_work` so a job is a first-class participant:

```python
def stop_background_work(views, jobs=(), timeout_ms: int = 5000) -> None:
```

Cancel every `view._worker` and every job, then wait on every thread
`live_threads(view)` reports plus every `job.thread`. Never raises. (If
M3 has not landed, make the same change to
`MainWindow.stop_background_work` in `main.py:1140-1182`.)

`MainWindow` gains:

```python
        #: Background jobs this window owns, so shutdown can settle them.
        #: A thread still inside pdfium when the interpreter finalises
        #: takes the process down -- see `deckle/app/shutdown.py`.
        self._jobs: dict[str, object] = {}
```

and its `stop_background_work` passes
`(self.preview_view, self.arrange_view, self.layout_panel)` and
`[j for j in self._jobs.values() if j is not None]`. The layout panel
joins the views tuple because its auto-crop job is parented to
`panel.widget` and sets `panel._worker`, which is exactly what
`live_threads` and the cancel loop already read.

### Step 3 — Save PDF

`main.py:_on_save_pdf_clicked`: keep everything up to and including
`output_path_problem` on the GUI thread. Replace lines 1093-1110 with:

```python
        plan = self.preview_view.plan
        sheets = len(plan.sheets)
        self.save_pdf_button.setEnabled(False)
        self.status_bar.showMessage(
            f"Exporting {sheets} sheet(s) to {os.path.basename(path)}..."
        )
        self._jobs["export"] = run_in_thread(
            lambda _cancel: export(plan, path),
            lambda _result: self._on_export_done(path, sheets),
            lambda exc: self._on_export_failed(path, exc),
            self.window,
            previous=self._jobs.get("export"),
        )
```

with the two completions carrying the current message text verbatim,
and both re-enabling the button through `_sync_document_actions()`:

```python
    def _on_export_done(self, path: str, sheets: int) -> None:
        self._sync_document_actions()
        self.status_bar.showMessage(f"Saved {sheets} sheet(s) to {path}")

    def _on_export_failed(self, exc: Exception, path: str) -> None:
        self._sync_document_actions()
        if isinstance(exc, OSError):
            # The common failures are all OSError and all explainable: the
            # file is open in a viewer, the drive went away, the disk is
            # full. Anything else is a bug and should still surface as one.
            self.status_bar.showMessage(describe_write_failure(path, exc))
            log_exception("output_write_failed", exc, path=path)
            return
        self.status_bar.showMessage(f"Export failed: {exc}")
        log_exception("export_failed", exc, path=path)
```

(Bind `path` with a default-argument lambda or `functools.partial`; the
argument order above is `(exc, path)` because `on_error` is called with
the exception.)

**`export` has no cancellation point**, so cancelling here means "ignore
the result", not "stop writing". `export` writes to a temp file and
`os.replace`s it (`export.py:617-625`), so a superseded export still
lands. Giving `export` a `cancel` parameter, mirroring
`render_sheet(..., cancel=)`, is the honest follow-up and is **not** this
spec — it is a core change with its own partial-file question. Say so in
the commit rather than implying a cancel that does not exist.

### Step 4 — Open project

Split `open_project` (`main.py:945-1015`, or
`project_actions.open_project` after M3) into a loader and an applier:

```python
def load_project_for_window(path: str):
    """Load ``path``, collecting its advisories. Runs off the GUI thread.

    :param path: the ``.deckle`` to read.
    :returns: the loaded ``Project``.
    :raises SourceMissingError, SourceChangedWarning, Exception: unchanged
        from ``load_project``; the caller turns each into its own message.
    """
```

— body is `main.py:960-973` verbatim (the `catch_warnings` block and the
`PathOutsideRootsAdvisory` logging) — and

```python
def apply_loaded_project(window, path: str, project) -> bool:
    """Swap a freshly loaded project into the window. GUI thread only."""
```

— body is `main.py:994-1015` verbatim, including
`_recover_autosave_if_offered` (which shows a modal), `recent.record`,
`clear_sheet_cache()` and the three view state swaps.

`open_project(window, path)` becomes: disable `open_project_button` and
`recent_button`, show `f"Opening {os.path.basename(path)}..."`, then
`run_in_thread(lambda _c: load_project_for_window(path), on_done,
on_error, window.window, previous=window._jobs.get("open"))`. The three
`except` branches move into `on_error`, keyed on the exception type, with
their message strings unchanged.

**The return value changes.** `open_project` currently returns `bool`
and `tests/gui_workflow.py:223` asserts on it
(`report["reopened"] = reopened.open_project(project_path)`). Keep a
synchronous entry point for that: `open_project(window, path, *,
runner=run_in_thread)`, and have `gui_workflow.py` pass
`runner=run_inline`. With `run_inline` the whole flow, including
`on_done`, completes before the call returns, so the boolean is truthful.
Chosen over changing `gui_workflow.py` to poll: the injected-runner seam
is the same one `refresh_printers(blocking=True)` already uses, and it
keeps the subprocess walk deterministic.

### Step 5 — Auto-crop

`layout_panel._on_auto_crop`: everything after `auto_crop_insets` writes
widgets, so only that one call moves.

```python
    def _on_auto_crop(self) -> None:
        """Fill the crop boxes from where the ink actually is."""
        from deckle.app.workers import run_in_thread
        from deckle.core.render import auto_crop_insets

        pages = list(self.state.project.pages)
        if not pages:
            return
        self.auto_crop_button.setEnabled(False)
        self.schedule_saved.emit(
            f"Measuring the ink on {len(pages)} page(s)..."
        )
        job = run_in_thread(
            lambda _cancel: auto_crop_insets(pages),
            self._on_auto_crop_done,
            self._on_auto_crop_failed,
            self.widget,
            previous=self._worker,
        )
        # `_worker` and `_thread` are the names `deckle.app.shutdown`
        # reads, so naming them here is what puts this job under the same
        # cancel-and-wait as a preview render -- a rasterisation still
        # inside pdfium at interpreter teardown takes the process down.
        self._worker = job
        self._thread = job.thread
```

`_on_auto_crop_done(insets)` is the existing body from `layout_panel.py:1634-1645`
(the box writes and the two `_on_crop_changed` calls);
`_on_auto_crop_failed(exc)` is `:1630-1632`.

`LayoutPanel.__init__` must initialise `self._worker = None` and
`self._thread = None` — the panel has neither today.

**`auto_crop_insets` has no cancellation point either**; the page list is
copied so the worker never reads `self.state` while the GUI thread edits
it.

### Step 6 — Print submission

The long one. Three pieces: a worker loop, prompts marshalled back, and a
cancel.

**6a. Signals.** `PrintDialog` gains an inner `_Signals(QObject)` (the
`LayoutPanel` pattern at `layout_panel.py:699-705`; if B15 has landed it
already exists — add to it):

```python
        class _Signals(QObject):
            # `object` is a `_Prompt`: its text, and the Event the worker
            # is blocked on. A Signal cannot carry a return value, so the
            # answer comes back on the object.
            prompt_requested = Signal(object)
            status = Signal(str)
```

connected in `__init__`:

```python
        self._signals.prompt_requested.connect(self._on_prompt)
        self._signals.status.connect(self.status_label.setText)
```

**Connect with the default `AutoConnection`.** That resolves to a direct
call when emitter and receiver are on the same thread — which is what
makes `run_inline` work with no special case — and to a queued call from
the worker thread, which is what puts the modal on the GUI thread. An
explicit `QueuedConnection` would deadlock the inline path; an explicit
`DirectConnection` would open a modal on the worker thread. Neither is a
theoretical hazard: `tests/test_print_dialog.py` drives the whole flow
inline.

**6b. The prompt object.**

```python
@dataclass
class _Prompt:
    """One question the print loop has to ask the GUI thread.

    The loop runs on a background thread and every confirmation it needs
    is a modal, which Qt will only show on the GUI thread. So the worker
    emits one of these and blocks on ``event``; the GUI slot shows the
    dialog, writes ``answer``, and sets the event.

    :ivar kind: ``"reload"``, ``"test_sheet"`` or ``"offline"``.
    :ivar args: the arguments for that prompt's injected callable --
        ``(instruction,)``, ``()``, or ``(printer_name, error)``.
    :ivar event: set by the GUI thread when the prompt is answered, in a
        ``finally`` -- a prompt that raises must still release the worker,
        or the thread waits forever and shutdown blocks for its whole
        timeout.
    :ivar answer: what the prompt returned, for the one that has a return
        value.
    """

    kind: str
    args: tuple = ()
    event: threading.Event = field(default_factory=threading.Event)
    answer: object = None
```

**6c. The worker side.**

```python
    def _ask(self, kind: str, *args):
        """Put a prompt to the GUI thread and wait for its answer.

        :returns: the prompt's answer, or ``None`` if the run was
            cancelled while waiting.
        """
        prompt = _Prompt(kind=kind, args=args)
        self._signals.prompt_requested.emit(prompt)
        while not prompt.event.wait(0.1):
            if self._cancelled():
                return None
        return prompt.answer
```

The 100 ms poll rather than a bare `wait()` is what lets Cancel (or the
dialog closing) unblock a worker whose prompt will never be answered.

`_drive` keeps its shape and its comments; the three call sites change:

- `self._show_offline_error(session.printer_name, session.last_error)`
  → `self._ask("offline", session.printer_name, session.last_error)`
- `if self._confirm_test_sheet():` → `if self._ask("test_sheet"):`
- `self._confirm_reload(instruction)` → `self._ask("reload", instruction)`
- `self.status_label.setText("Print job complete.")` →
  `self._signals.status.emit("Print job complete.")`

and the loop head gains a cancel check:

```python
        while not session.finished and session.last_error is None:
            if self._cancelled():
                self._signals.status.emit(
                    "Cancelled -- the sheets already printed are recorded, "
                    "and the job can be resumed."
                )
                return
            session.advance()
```

**6d. The GUI side.**

```python
    def _on_prompt(self, prompt: _Prompt) -> None:
        """Answer one prompt from the print loop. GUI thread only."""
        try:
            if prompt.kind == "reload":
                self._confirm_reload(*prompt.args)
            elif prompt.kind == "test_sheet":
                prompt.answer = self._confirm_test_sheet()
            elif prompt.kind == "offline":
                self._show_offline_error(*prompt.args)
        finally:
            # Unconditional: a prompt that raises must still release the
            # worker, or the thread blocks forever and shutdown waits out
            # its whole timeout on the way to a crash-free exit.
            prompt.event.set()
```

**6e. Starting and cancelling.** `start_print` and `_offer_resume` stop
calling `_drive` directly:

```python
        self._session = session
        self._job = self._runner(
            lambda cancel: self._run_session(session, cancel),
            lambda _result: self._on_run_finished(),
            self._on_run_failed,
            self.widget,
            previous=self._job,
        )
```

where `self._runner` is a new constructor parameter,
`runner: Callable = run_in_thread`, injected exactly like every other
dependency this class takes (`print_dialog.py:154-170`), and
`_run_session(session, cancel)` does `session.start()` (or
`session.resume(count)`) followed by `self._drive(session)`.

Cancel:

```python
        self.cancel_button = QPushButton("Cancel", self.widget)
        self.cancel_button.setToolTip(
            "Stop after the chunk now being submitted. Sheets already "
            "printed stay recorded, so the job can be resumed from where "
            "it stopped -- nothing is reprinted."
        )
        self.cancel_button.setEnabled(False)
```

`cancel_print()` sets `self._job.cancel`, releases any outstanding
prompt's event, and disables itself. `self.widget.finished.connect(...)`
wires the dialog closing to the same thing, and
`MainWindow._on_print_clicked` calls `self.print_dialog.finish()` after
`exec()` returns — cancel, then wait up to 5 s on the job's thread — so a
run cannot outlive the dialog that owns it. That wait is on the GUI
thread and is bounded by one chunk; the alternative is a spooler
submission with no dialog behind it.

**What cancel means, precisely:** the loop stops before the *next*
`session.advance()`. A chunk already handed to `QPrinter` is out of
Deckle's hands — up to ten sheets, `backend.DEFAULT_CHUNK_SIZE` — and the
session cursor on disk already records them, so a resume starts after
them and reprints nothing. The session state is **not** deleted on
cancel; that is the whole point of it existing.

**6f. The existing tests keep working** by adding `runner=run_inline` to
**both** `_dialog(...)` factories — `tests/test_print_dialog.py` and
`tests/test_ui_surface.py:340-358`. Seven call sites assert immediately
after `start_print()` (four in the first file, three in the second, the
latter reading `dialog._session.sheets`); with the inline runner the job
runs, the prompts fire as direct connections, and everything completes
before the call returns.

### Step 7 — the two docstrings that become false

- `deckle/core/render.py:101-129`, `pdfium_guard`: rewrite the paragraph
  beginning "Three callers outside this module need it". The print path
  now runs on its own thread, so the reason the lock is public is that
  **printing and previewing are two background threads in the same
  process-global library**, which is a stronger reason, not a weaker one.
  Keep the "hold it across the document's whole life" instruction
  verbatim.
- `deckle/app/backend.py:210-214`: the comment inside `_render_sheet_side`
  says "this path rasterizes on the GUI thread -- the print dialog runs
  no thread of its own". Rewrite to say the print dialog now runs its own
  thread and this is one of two threads that reach pdfium.

Both are comment-only edits in files this spec otherwise does not touch.
Leaving them is worse than not writing them: a comment asserting a
property the program no longer has is the shape `docs/decisions.md` has
caught five times.

## 4. Tests

### New: `tests/test_workers.py`

Pure; no Qt needed for most of it (`run_inline` and `Job`).

| Test | Assertion in words | Failure on the unfixed tree |
|---|---|---|
| `test_a_job_carries_back_what_it_returned` | `run_inline(lambda c: 7, done, err)` calls `done(7)` and not `err` | `ModuleNotFoundError: deckle.app.workers` |
| `test_a_job_carries_back_what_it_raised` | `fn` raising `OSError("x")` calls `on_error` with that exception, and `Job.run` itself does not raise | same |
| `test_a_raising_job_is_recorded` | `background_job_failed` appears in the diagnostics log (use `tests/test_hardening_printing.py`'s `_events` helper or `tests/test_diagnostics.py`'s) | same |
| `test_a_superseded_job_delivers_nothing` | run job A with a `fn` that blocks on an event; start job B with `previous=A`; release A | A's `on_done` was never called; B's was | same |
| `test_a_cancelled_job_never_starts_its_work` | set `job.cancel` before `run()` | `fn` was not called | same |
| `test_the_runner_parents_its_thread` | `run_in_thread` with a fake `thread_factory` recording its `parent` argument | the parent is the widget passed in | same |
| `test_run_inline_and_run_in_thread_deliver_identically` | the same `fn` through both, with a real `QApplication` and `thread.wait()` for the threaded one | same `on_done` argument | same |

### New: `tests/test_print_dialog_threading.py`

| Test | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_the_reload_prompt_is_answered_on_the_calling_thread` | stub session that reports a pass boundary; `runner=run_inline`; record `threading.get_ident()` inside the injected `confirm_reload` | it equals the main thread's ident | `TypeError: __init__() got an unexpected keyword argument 'runner'` |
| `test_the_worker_waits_for_the_reload_answer` | a `confirm_reload` that records the session's `pass_index` when it is called | the loop had not advanced past the boundary at that moment | same |
| `test_a_prompt_that_raises_still_releases_the_worker` | `confirm_reload` raising `RuntimeError` | the call returns rather than hanging, and the error is reported once | same |
| `test_cancelling_stops_before_the_next_chunk` | stub session counting `advance()` calls, with a `confirm_reload` that calls `dialog.cancel_print()` | `advance` was called no more times after the cancel | same |
| `test_cancelling_leaves_the_session_resumable` | as above | the stub session's `delete`/`_delete_state` was never called, and `status_label` says the job can be resumed | same |
| `test_submitting_runs_off_the_gui_thread` | `runner=run_in_thread` with a real `QApplication`; the stub session records `threading.get_ident()` inside `advance` | it differs from the main thread's ident | same |

Every existing test in `tests/test_print_dialog.py` must still pass, with
one edit: the `_dialog(...)` helper gains `runner=run_inline`.

### New: `tests/test_layout_panel_auto_crop.py`

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_auto_crop_reports_before_it_measures` | patch `deckle.core.render.auto_crop_insets` with a recorder; a `schedule_saved` message containing "Measuring" arrives **before** the recorder is called | no such message today — the first message the user sees is the result |
| `test_auto_crop_fills_the_boxes_when_it_finishes` | with `run_inline` injected, the eight boxes carry the measured insets and `crop_odd_pt` reaches the project | passes today; the regression guard |
| `test_auto_crop_failure_is_reported_and_not_raised` | patch to raise; the message contains "Could not measure the ink" | passes today; guard |
| `test_the_panel_registers_its_job_for_shutdown` | after `_on_auto_crop`, `panel._worker` is a `Job` with a `cancel` event | `AttributeError: 'LayoutPanel' object has no attribute '_worker'` |

### Extend: `tests/test_shutdown.py`

| Test | Assertion | Failure before |
|---|---|---|
| `test_stop_background_work_settles_jobs_too` | `shutdown.stop_background_work((), jobs=[fake_job])` sets its cancel and waits on its thread | `TypeError: stop_background_work() got an unexpected keyword argument 'jobs'` |

Also add a fourth subprocess probe to the PROBE script: start a Save PDF
job against a large plan, then `window.close()` immediately, and assert
exit 0 and `still running: []`. That is the property the whole spec risks
breaking — a background job outliving the process.

### Extend: `tests/gui_workflow.py`

Pass `runner=run_inline` where the workflow calls `open_project`, so
`report["reopened"]` stays a truthful boolean
(`tests/gui_workflow.py:223`), and add
`report["export_via_button"]` driving `_on_save_pdf_clicked` with the
file dialog stubbed — the current file exports the plan directly
(`:137-140`) and so has never touched the button's code path.

## 5. Acceptance

| Check | Command |
|---|---|
| The helper exists | `test -f deckle/app/workers.py && grep -qE "^def run_in_thread\(" deckle/app/workers.py` |
| It imports no Qt | `.venv/bin/python -c "import sys, deckle.app.workers; assert not [n for n in sys.modules if n.startswith('PySide6')]"` |
| Save PDF no longer exports inline | `! sed -n '/    def _on_save_pdf_clicked/,/^    def /p' deckle/app/main.py \| grep -qE "^ *export\(plan, path\)$"` |
| Auto-crop no longer measures inline | `! sed -n '/    def _on_auto_crop/,/^    def /p' deckle/app/views/layout_panel.py \| grep -q "odd, even = auto_crop_insets"` |
| The print loop is driven through a runner | `grep -q "self._runner(" deckle/app/views/print_dialog.py` |
| Prompts are marshalled, not called from the loop | `! sed -n '/    def _drive/,/^    def /p' deckle/app/views/print_dialog.py \| grep -qE "self\._confirm_reload\(\|self\._confirm_test_sheet\(\|self\._show_offline_error\("` |
| Exactly one `_new_thread` in the app layer | `test $(grep -rlE "^def _new_thread\(" deckle/app \| wc -l) -eq 1` |
| The pdfium docstring no longer claims the GUI thread | `! grep -qi "thread of its own" deckle/core/render.py deckle/app/backend.py` |
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_workers.py tests/test_print_dialog_threading.py tests/test_layout_panel_auto_crop.py -q --no-header -p no:cacheprovider` |
| The print dialog suite still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_print_dialog.py tests/test_print_session.py tests/test_print_log_failure.py -q --no-header -p no:cacheprovider` |
| Shutdown still exits cleanly, four probes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_shutdown.py -q --no-header -p no:cacheprovider` |
| The whole-window walk still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_gui_workflow.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree — the four "no longer" rows exit 1, and the
`_new_thread` row currently reports 1 file (`deckle/app/main.py`), so it
passes now and must still pass after:

```
$ grep -rlE "^def _new_thread\(" deckle/app | wc -l
1
$ grep -ci "thread of its own" deckle/core/render.py deckle/app/backend.py
deckle/core/render.py:1
deckle/app/backend.py:1
```

(The two sentences differ in case and in line-wrapping —
`render.py:110-111` has "The print dialog runs no / thread of its own",
`backend.py:211-212` has it mid-sentence and lower-case — which is why
the check is case-insensitive on the fragment they share.)

## 6. Out of scope

- **Giving `export`, `load_project` and `auto_crop_insets` a `cancel`
  parameter.** All three are core operations with no cancellation point,
  so for those three "cancel" here means "drop the result", and the work
  runs to completion. `render_sheet(..., cancel=)` is the model to copy
  when someone does it; the interesting question is what a half-written
  export leaves behind, and `export` already writes to a temp and
  `os.replace`s, so the answer is probably "nothing" — but that is a core
  spec, not this one.
- **B13** — `ImportView` has no supersede guard and import threads are
  invisible to `stop_background_work`. This spec adds the helper that
  fixes it and the `jobs=` argument that lets shutdown see it, and does
  not rewrite `import_view.py`. Doing B13 after this is a rewrite of
  fifteen lines into three.
- **Rewriting `PreviewView.refresh` / `ArrangeView` / the printer query
  to use `run_in_thread`.** They work, they are tested, and three of them
  carry behaviour the helper does not model (spread rendering, the
  visible-range fetch, the enumeration deadline). Converting them is a
  follow-up worth doing once the helper has earned its keep.
- **B33** — `loader` computes a whole-file SHA-256 while holding the
  pdfium lock, so an Open blocks every preview render regardless of which
  thread it runs on. Moving Open off the GUI thread makes the window
  responsive; it does not make the preview responsive. Different fix,
  different layer.
- **B14** — cancelling the "How many sheets came out?" prompt returns 0
  and resumes from sheet 0. `_default_ask_resume_count`
  (`print_dialog.py:366-376`) is untouched here; a cancelled *prompt* and
  a cancelled *run* are different things and this spec only adds the
  second.
- **B4** — `log_print_job` called by both the backend and the session, so
  every chunk is logged twice.
- **B6** — the backend's aspect-ignoring scale into the imageable area.

### Collisions with the other specs in this directory

| Spec | Overlap | Order |
|---|---|---|
| `B13-import-worker-guard-and-shutdown.md` | adds an import-thread branch to `stop_background_work` and a supersede guard to `ImportView` | **B13 first**, then this spec's `jobs=` argument is additive. If B17 goes first, B13's supersede guard should just be `run_in_thread(..., previous=self._job)`. |
| `B10-N9-dirty-and-save-prompts.md` | rewrites `open_project`, `close`, `_on_close_event`, and adds a `confirm_discard` prompt to `tests/test_shutdown.py`'s PROBE | **B10 first.** It changes `open_project`'s control flow and return type; splitting it into loader + applier afterwards is mechanical, the other way round is not. |
| `B14-cancelled-resume-count-aborts.md` | `_default_ask_resume_count` and `_offer_resume` | either order — B14 is about a cancelled *prompt*, this is about a cancelled *run*, and they meet only in `_offer_resume`'s first three lines |
| `B30-requery-printers.md` | `_on_print_clicked` | either order; this spec adds a `finish()` call after `exec()` returns |
| `M1-layout-panel-binding-table.md` | `_on_auto_crop` | **M1 first** — it survives M1 unchanged, so B17 then edits one method rather than racing a file rewrite |
| `M3-main-window-split.md` | `stop_background_work`, `open_project`, `_on_save_pdf_clicked`, and `_new_thread`'s home | **M3 first**, per §8's ordering trap |
| `B33-loader-memory-and-lock.md` | the sha256-under-the-pdfium-lock that makes Open block previews | either order; different layer |

## 7. decisions.md entry

```
## 2026-09-XX — Four long operations ran on the GUI thread, and one of them held paper
- Symptom: Print submission, Save PDF, Open project and Auto-crop all ran synchronously on the GUI thread with no cancel. A sixty-sheet pass froze the window for minutes; a project open SHA-256'd every source; Auto-crop rasterised every page. Save PDF's own "Exporting N sheet(s)..." message was never on screen for a single frame -- `showMessage` queues a repaint that cannot happen until control returns to the event loop, which is after `export` returns. And a print run that was visibly going wrong could only be stopped by killing the process, which loses the session cursor the resume prompt depends on.
- Fix: `deckle/app/workers.py` -- `run_in_thread(fn, on_done, on_error, parent, previous=)` with the cancel token and supersede guard the four existing background paths already share, plus a `run_inline` twin for tests and any caller with no event loop, the same seam `refresh_printers(blocking=True)` keeps. Applied to all four. The print loop's three modal prompts are marshalled back to the GUI thread as a `_Prompt` object carrying a `threading.Event` the worker blocks on, connected with Qt's default AutoConnection so the inline path stays a direct call and the threaded path becomes a queued one.
- Surfaces: Cancel means two different things and the commit says which. For print submission it is real: the loop stops before the next `session.advance()`, up to ten sheets are already with the spooler, the cursor on disk records them and a resume reprints nothing -- so the session state is deliberately NOT deleted on cancel. For the other three it means "drop the result": `export`, `load_project` and `auto_crop_insets` have no cancellation point, and giving them one is a core change with its own partial-file question. Printing now also holds the process-global pdfium lock from a background thread, which serialises it against preview renders rather than faulting -- the lock was always right, and two comments claiming the print path runs on the GUI thread had to be rewritten rather than left asserting a property the program no longer has.
- Watch: The prompt slot sets its event in a `finally`. A prompt that raises would otherwise leave the worker blocked forever, and the first symptom would be a five-second pause on quit followed by whatever a thread parked in a driver call does at interpreter teardown -- which is the crash `stop_background_work` exists to prevent. The 100ms poll in the worker's wait is there for the same reason from the other side: a dialog closed while a prompt is outstanding has to be able to release it.
- Commit: <fill in>
```

## 8. Traps

- **`warnings.catch_warnings` is not thread-safe.** It mutates the
  process-global `warnings.filters`. `open_project` uses it
  (`main.py:961-962`) and step 4 moves it onto a worker thread — the
  first threaded use in the app. It is tolerable here because the only
  other threads running during a project load are renders, which emit no
  warnings, but it is a real hazard and the proper fix is a
  `load_project(..., on_advisory=...)` callback in core. Record it in the
  commit; do not silently rely on it.
- **A prompt's `event.set()` must be in a `finally`.** See §7's Watch.
- **Connect the prompt signal with the default `AutoConnection`.** An
  explicit `QueuedConnection` deadlocks `run_inline` (the emit returns
  immediately, the worker waits for an event nothing will set until the
  event loop runs, and there is no event loop); an explicit
  `DirectConnection` opens a modal on the worker thread, which Qt
  refuses.
- **Do not touch a widget from `fn`.** `run_in_thread`'s `fn` runs off
  the GUI thread. Step 5 copies the page list before starting for exactly
  this reason (`pages = list(self.state.project.pages)`); reading
  `self.state.project` from the worker would race the GUI thread's edits.
- **Store the `Job`.** It is the supersede token, the cancel handle and
  the shutdown handle. A job that is started and dropped cannot be
  cancelled, cannot be superseded, and will not be waited for — which
  reintroduces the crash class `tests/test_shutdown.py` exists to prevent.
- **`live_threads` reads `view._thread` first and then
  `view.widget.findChildren(QThread)`** (`main.py:255-268`, or
  `shutdown.py` after M3). Parenting a job's thread to a widget is what
  makes it visible; a thread parented to `None` is invisible to shutdown
  and will outlive the process's Python state.
- **`MainWindow` has `.window`, not `.widget`**, so `live_threads` cannot
  find its jobs through a widget. That is why `stop_background_work` gains
  an explicit `jobs=` argument rather than a fifth view.
- **`tests/test_print_dialog.py` asserts immediately after
  `start_print()`** in six tests (`:222, 231, 241, 298`). They pass only
  with `runner=run_inline`; adding the parameter and forgetting the test
  edit turns six tests red in a way that looks like a logic bug.
- **`PrintDialog` is a plain class, not a `QObject`** — signals need the
  inner-`QObject` pattern and the instance must be kept alive on
  `self._signals`, exactly as in `layout_panel.py:699-705`.
- **The pdfium lock is an `RLock`, reentrant per thread, not across
  threads.** A print thread and a preview thread now serialise: a print
  chunk waits for the visible sheet's render and vice versa, bounded by
  one sheet in each direction. Lock ordering is safe because
  `render_sheet` calls `export.export_sheet_cached` — which takes the
  export cache's own lock (`export.py:919`) — **before** entering
  `with _PDFIUM_LOCK` (`render.py:246, 262`), so the two are never
  nested. Do not introduce a path that takes them in the other order.
- **Ordering.** Recommended overall: **M1 → M3 → B15 → B17**. B17 last
  because it touches `main.py`, `print_dialog.py` and `layout_panel.py`
  — one file from each of the other three specs — and because
  `stop_background_work` gaining a `jobs=` argument is a one-line change
  after M3 and a method rewrite before it. If B17 must go first, own
  `_new_thread` in `workers.py` and have M3's `printer_query.py` import
  it from there.
- `python -m deckle` launches the GUI and blocks; a real `QMainWindow`
  under pytest exits 127 here (`tests/test_ui_surface.py:534-541`). The
  only whole-window coverage is `tests/gui_workflow.py`, run as a
  subprocess by `tests/test_gui_workflow.py`, and `tests/test_shutdown.py`'s
  four probes.
