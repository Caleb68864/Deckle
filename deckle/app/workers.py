"""Running one slow thing on a thread, the way this app already does it.

Save PDF, Auto-crop and the source check behind Open project ran on the GUI
thread, so the window froze for the length of the job and the "Exporting..."
status never painted: ``showMessage`` queues a repaint that cannot happen until
control returns to the event loop, which is after the work (B17). Qt is imported
only when a thread is made.

Print submission still runs on the GUI thread. See B17 in ``docs/ROADMAP.md``.
"""

from __future__ import annotations

import threading
from typing import Callable

from deckle.core.diagnostics import log_exception


class Job:
    """One unit of background work, and what came back from it.

    :param fn: called on the background thread with the cancel event as its
        only argument. Whatever it returns lands on :attr:`result`.
    :ivar result: what ``fn`` returned, or ``None``.
    :ivar error: what ``fn`` raised, or ``None``. Held rather than propagated,
        because a ``QThread`` has nowhere to deliver an exception and the
        operation would simply appear not to have happened.
    :ivar cancel: set when the outcome is no longer wanted. ``fn`` may ignore
        it, and then it means "drop the result" rather than "stop working".
    :ivar thread: the running ``QThread``, or ``None`` once it has finished,
        because Qt frees it then.
    """

    def __init__(self, fn: Callable[[threading.Event], object]) -> None:
        self._fn = fn
        self.result: object = None
        self.error: Exception | None = None
        self.cancel = threading.Event()
        self.thread = None

    def run(self) -> None:
        """Run ``fn``. Never raises.

        :returns: nothing. Results land on :attr:`result` and :attr:`error`.
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

    :param parent: the owning ``QObject``, so the thread cannot leak.
    :returns: the thread, not started.
    """
    from PySide6.QtCore import QThread

    return QThread(parent)


def _deliver(job: Job, on_done, on_error) -> None:
    """Hand a finished job's outcome to the GUI, unless nobody wants it.

    :param job: the finished job.
    :param on_done: called with the result.
    :param on_error: called with the exception.
    :returns: nothing.
    """
    job.thread = None
    if job.cancel.is_set():
        return
    if job.error is not None:
        on_error(job.error)
        return
    on_done(job.result)


def run_in_thread(fn, on_done, on_error, parent) -> Job:
    """Run ``fn`` on a background thread and deliver the result on the GUI one.

    :param fn: ``(cancel) -> result``, run off the GUI thread. Must touch no
        widget.
    :param on_done: ``(result) -> None``, run on the GUI thread.
    :param on_error: ``(exc) -> None``, run on the GUI thread.
    :param parent: the ``QObject`` to parent the thread to.
    :returns: the :class:`Job`. **Store it**: it is what shutdown waits on.
    """
    job = Job(fn)
    thread = _new_thread(parent)
    thread.run = job.run
    thread.finished.connect(lambda: _deliver(job, on_done, on_error))
    # Without this they pile up for the life of the parent.
    thread.finished.connect(thread.deleteLater)
    job.thread = thread
    thread.start()
    return job


def run_inline(fn, on_done, on_error, parent=None) -> Job:
    """Run ``fn`` synchronously, then deliver. Same shape as :func:`run_in_thread`.

    For tests and any caller with no event loop to return to.

    :param fn: ``(cancel) -> result``.
    :param on_done: ``(result) -> None``.
    :param on_error: ``(exc) -> None``.
    :param parent: ignored.
    :returns: the finished :class:`Job`.
    """
    job = Job(fn)
    job.run()
    _deliver(job, on_done, on_error)
    return job
