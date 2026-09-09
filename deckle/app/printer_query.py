"""Asking the OS what printers exist, without letting it hang the app.

``QPrinterInfo.availablePrinters()`` enumerates **network** printers too,
and the spooler blocks per printer until it times out when one is
unreachable -- 21ms with the network up, 81 minutes without it, both
measured on the machine this was found on. So the query runs on a thread
*and* under a deadline, and neither half is enough alone: a background
thread parked in a driver call is still a hang, it has only moved where.

The window owns the button this enables and the status bar that explains
it; this module owns the question and the two ways it can fail to be
answered. Kept out of ``main.py`` for the ordinary reason -- it is the
most carefully reasoned code in the app and it has nothing to do with
splitters and tooltips.

PySide6 is imported inside the functions that need it, so importing this
module needs neither Qt nor a display.
"""

from __future__ import annotations

import logging

from deckle.app.printer_capabilities import query_imageable_area_pt
from deckle.core.diagnostics import log_event, log_exception


PRINTER_TIMEOUT_MESSAGE = (
    "Could not reach the print spooler -- printing is unavailable. "
    "Saving a PDF still works."
)
"""Shown instead of :data:`NO_PRINTERS_MESSAGE` when enumeration timed out.

The UI *state* is identical (Print disabled, Save PDF untouched), but the
cause is not, and "no printers installed" is actively misleading to someone
who is looking straight at their printer."""

PRINTER_QUERY_TIMEOUT_MS = 5000
"""How long to wait for printer enumeration before giving up on it.

Enumeration measures ~21ms with the network up. Five seconds is two orders
of magnitude of headroom for a slow-but-working spooler, and still short
enough that a user staring at "Checking for printers..." does not conclude
the app has hung -- which, for 81 minutes on this machine during a network
outage, it had.
"""


class _PrinterQueryWorker:
    """Enumerates printers on a background thread.

    Plain class, not a ``QObject`` -- same shape as ``ThumbnailWorker`` and
    ``PreviewWorker``. Exists because printer enumeration can block for the
    OS spooler's timeout when a network printer is unreachable.

    :ivar names: the enumerated printer names, or an empty list if
        enumeration failed. Read only after the thread finishes.
    :ivar imageable_areas: what each driver said its non-printable border
        is, keyed by printer name. A printer that declined to answer is
        absent rather than present with a zero -- see
        :mod:`deckle.app.printer_capabilities`. Read only after the thread
        finishes.
    """

    def __init__(self) -> None:
        self.names: list[str] = []
        self.imageable_areas: dict[str, tuple[float, float, float, float]] = {}

    def run(self) -> None:
        """Enumerate printers into :attr:`names`, then ask each its border.

        :returns: nothing, and never raises. A spooler failure degrades to
            an empty list -- the app is fully usable for Save PDF with no
            printers at all -- and is recorded, because "the printer list
            was empty" is otherwise a support report with nothing behind it.
        """
        try:
            self.names = available_printer_names()
        except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
            # A spooler failure must not take the window down; the app is
            # fully usable for Save PDF with no printers at all. Record why,
            # though: "the printer list was empty" is otherwise a support
            # report with nothing behind it.
            log_exception("printer_enumeration_failed", exc)
            self.names = []
            return

        # On this thread and inside the same deadline, because it is
        # another call into the same spooler. A `try` per printer rather
        # than one around the loop, so a single unreachable network queue
        # does not cost the margins of every other printer.
        for name in self.names:
            try:
                area = query_imageable_area_pt(name)
            except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
                log_exception("printer_imageable_query_failed", exc, printer=name)
                continue
            if area is not None:
                self.imageable_areas[name] = area


class _PrinterQuery:
    """Arbitrates one enumeration between its worker and a deadline.

    Moving enumeration onto a background thread stopped it freezing the UI,
    but it did not bound it: ``QPrinterInfo.availablePrinters()`` blocks
    inside the OS spooler and there is no way to interrupt it. A worker
    thread that never returns is still a hang -- it has only moved where.
    So the query is settled by whichever comes first, the worker's answer
    or the deadline, and only once.

    On timeout the app proceeds exactly as it does with no printers
    installed: Print disabled, Save PDF untouched, and a log line saying
    which way it went. A late answer from a thread that eventually
    unblocks is discarded rather than re-enabling Print underneath a user
    who has since moved on -- and the abandoned thread is left to finish
    on its own, because killing a thread parked in a driver call is worse
    than leaking one.

    :param worker: the enumeration worker to read an answer from.
    :param apply_result: called with ``(names)`` or
        ``(names, message)`` once the query settles, on the UI thread.
    :param timeout_ms: the deadline.
    :ivar settled: whether either outcome has already been applied.
    :ivar timed_out: whether the deadline, rather than the worker, settled
        it.
    """

    def __init__(self, worker: _PrinterQueryWorker, apply_result, timeout_ms: int) -> None:
        self._worker = worker
        self._apply = apply_result
        self.timeout_ms = timeout_ms
        self.settled = False
        self.timed_out = False

    def complete(self) -> None:
        """Settle with the worker's names. Ignored if already settled.

        :returns: nothing. A late answer from a thread that eventually
            unblocks is logged and discarded rather than re-enabling Print
            underneath a user who has since moved on.
        """
        if self.settled:
            log_event(
                "printer_enumeration_late_result",
                count=len(self._worker.names),
                timeout_ms=self.timeout_ms,
            )
            return
        self.settled = True
        names = list(self._worker.names)
        if not names:
            # Zero printers is a legitimate, fully supported state -- but it
            # is indistinguishable at the UI from a spooler that failed, so
            # say which happened.
            log_event("printer_enumeration_empty", reason="spooler returned no printers")
        else:
            log_event("printer_enumeration_completed", count=len(names))
        self._apply(names, None, dict(self._worker.imageable_areas))

    def time_out(self) -> None:
        """Settle as "no printers found". Ignored if already settled.

        :returns: nothing. The app then behaves exactly as it does with no
            printers installed, but says :data:`PRINTER_TIMEOUT_MESSAGE`
            instead -- "no printers installed" is actively misleading to
            someone looking straight at their printer.
        """
        if self.settled:
            return
        self.settled = True
        self.timed_out = True
        log_event(
            "printer_enumeration_timeout",
            level=logging.WARNING,
            timeout_ms=self.timeout_ms,
            reason="spooler did not answer before the deadline",
        )
        # An empty dict, never a half-filled one: the worker is still
        # running and may be mid-loop, and margins for some printers and
        # not others is a state nothing downstream is written to expect.
        self._apply([], PRINTER_TIMEOUT_MESSAGE, {})


def _single_shot(interval_ms: int, callback) -> None:
    """Fire ``callback`` on the UI thread after ``interval_ms``.

    A thin, patchable seam over ``QTimer`` for the same reason
    :func:`available_printer_names` is one: the timeout logic has to be
    testable without an event loop.
    """
    from PySide6.QtCore import QTimer

    QTimer.singleShot(interval_ms, callback)


def _new_thread(parent):
    """A ``QThread`` parented to ``parent``. Patchable seam, as above."""
    from PySide6.QtCore import QThread

    return QThread(parent)


def available_printer_names() -> list[str]:
    """The names of printers Qt currently knows about.

    A thin, patchable seam over ``QPrinterInfo`` so callers (and tests)
    don't need a real printer attached -- an empty list is a normal,
    expected result, not an error.

    :returns: the printer names Qt reports.
    :raises Exception: whatever the Qt/spooler call raises. Callers wrap
        this in :class:`_PrinterQueryWorker`, which is where the
        degrade-to-empty decision lives -- it is deliberately not made
        here, so a caller that wants the real failure can have it.
    """
    from PySide6.QtPrintSupport import QPrinterInfo

    return [info.printerName() for info in QPrinterInfo.availablePrinters()]
