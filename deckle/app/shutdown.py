"""Cancelling and waiting for background renders on the way out.

Quitting mid-render used to crash about one run in four with
``0xC0000409`` and no Python traceback: a render still inside pdfium when
the interpreter finalises takes the process down with it. Both render
workers already supported cancellation -- nobody had ever asked, and
nobody waited.

Kept out of the window because neither function is a question about the
window. :func:`live_threads` is a function of a duck-typed view and is
already tested as one, and :func:`stop_background_work` takes the views it
settles as an argument, so a fourth background render source is added to a
caller's tuple rather than buried inside a method.

Qt is imported inside :func:`live_threads`, like everywhere else in
``deckle.app``, so importing this module needs neither PySide6 nor a
display.
"""

from __future__ import annotations

from deckle.core.diagnostics import log_event, log_exception


def live_threads(view) -> list:
    """Every render thread ``view`` still has alive, current or superseded.

    ``view._thread`` is only the LATEST one. Both views replace it every
    time a render is superseded -- scrubbing sheets, churning the arrange
    grid -- so reading that attribute alone reports one thread while
    several are running.

    That was the whole of the shutdown flakiness. Quitting mid-render
    crashed about one run in four with ``0xC0000409`` and no Python
    traceback, and every crashing run was measured to have threads still
    running after ``close()`` returned, while every clean run had none.
    A superseded worker is cancelled the moment it is replaced, so
    cancellation was never the gap; nobody waited for it to notice, and a
    render still inside pdfium when the interpreter finalises takes the
    process down with it.

    Threads are parented to the view's widget precisely so they cannot
    leak, which makes the widget's own child list the authoritative
    register -- no second bookkeeping to drift out of sync with it.
    ``_thread`` is still consulted first: tests inject a fake thread that
    is not a real ``QObject`` and so is not a child of anything.

    :param view: a view exposing ``_thread`` and/or a ``widget``.
    :returns: the threads, current first, each appearing once. Includes
        threads that have already finished -- waiting on one of those
        returns immediately, and filtering them here would race with them
        finishing between the check and the wait.
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


def stop_background_work(views, timeout_ms: int = 5000) -> None:
    """Cancel in-flight renders on ``views`` and wait for their threads.

    Nothing did this, so quitting mid-render left preview and thumbnail
    threads running into interpreter teardown -- where they called pdfium
    after it had been finalised and took the process down with an access
    violation. The user sees a crash on exit, on the one action that is
    supposed to be safe.

    Both workers already support cancellation; they simply were never
    asked, and nobody waited.

    :param views: the views to settle, each exposing ``_worker`` and/or a
        ``widget``. Passed in rather than reached for, so a caller that
        grows a third background render source -- the layout panel's crop
        composite did, in N11 -- adds it at the call site instead of
        inside a tuple buried in a method. A view that is not on this list
        is the exact shutdown crash :func:`live_threads` exists to prevent.
    :param timeout_ms: how long to wait per thread. A render that ignores
        cancellation must not hang the quit -- a stuck thread is a worse
        outcome than an abandoned one, and the wait is bounded for that
        reason.
    :returns: nothing. Never raises: this runs while the app is closing,
        and an exception here would replace a clean exit with the crash it
        exists to prevent.
    """
    for view in views:
        try:
            worker = getattr(view, "_worker", None)
            if worker is not None:
                worker.cancel.set()
        except Exception as exc:  # pragma: no cover - defensive
            log_exception("shutdown_cancel_failed", exc)

    for view in views:
        try:
            # Every live thread, not just `view._thread` -- see
            # `live_threads`. Waiting only for the current one left
            # superseded renders running into interpreter teardown,
            # which is the crash this function exists to prevent.
            for thread in live_threads(view):
                if thread.isRunning():
                    if not thread.wait(timeout_ms):
                        log_event(
                            "shutdown_thread_timeout",
                            view=type(view).__name__,
                        )
        except Exception as exc:  # pragma: no cover - defensive
            log_exception("shutdown_wait_failed", exc)
