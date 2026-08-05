"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
"""

from __future__ import annotations

import logging
import os

from deckle.app.state import AppState
from deckle.core.diagnostics import log_event, log_exception
from deckle.app.views.arrange_view import ArrangeView
from deckle.app.views.import_view import ImportView
from deckle.app.views.layout_panel import LayoutPanel, recompute_plan
from deckle.app.views.preview_view import PreviewView
from deckle.app.views.print_dialog import PrintDialog
from deckle.core.export import export
from deckle.core.models import LayoutSettings, Project
from deckle.core.profiles import BUILTIN_PRESETS

LETTER_PT = (612.0, 792.0)

NO_PRINTERS_MESSAGE = "No printers installed -- connect a printer to enable printing."

PRINTER_TIMEOUT_MESSAGE = (
    "Could not reach the print spooler -- printing is unavailable. "
    "Saving a PDF still works."
)
"""Shown instead of :data:`NO_PRINTERS_MESSAGE` when enumeration timed out.

The UI *state* is identical (Print disabled, Save PDF untouched), but the
cause is not, and "no printers installed" is actively misleading to someone
who is looking straight at their printer."""

# Used to seed PreviewView before any printer/profile has been chosen -- the
# same fallback resolve_profile() reaches for when a printer has no saved
# PrinterProfile yet (see deckle/app/views/print_dialog.py).
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))

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
    """

    def __init__(self) -> None:
        self.names: list[str] = []

    def run(self) -> None:
        """Enumerate printers into :attr:`names`.

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
        self._apply(names)

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
        self._apply([], PRINTER_TIMEOUT_MESSAGE)


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


def default_project() -> Project:
    """An empty project on US Letter, for a freshly launched window.

    :returns: a project with no pages, no gutter and no printer.
    """
    layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)


def _qt_widgets():
    from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget

    return QHBoxLayout, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget


class MainWindow:
    """The application shell: import controls, arrange grid, print action.

    :param project_path: the project to open, or ``None`` for a new one.
        Also decides where autosave writes.
    """

    def __init__(self, project_path: str | None = None) -> None:
        QHBoxLayout, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget = _qt_widgets()

        self.state = AppState(default_project(), project_path=project_path)

        self.window = QMainWindow()
        self.window.setWindowTitle("Deckle")
        central = QWidget(self.window)
        layout = QVBoxLayout(central)

        self.import_view = ImportView(self.state, central)
        layout.addWidget(self.import_view.widget)

        self.arrange_view = ArrangeView(self.state, central)
        layout.addWidget(self.arrange_view.widget)

        self.layout_panel = LayoutPanel(self.state, central, profile=DEFAULT_PROFILE)
        layout.addWidget(self.layout_panel.widget)

        self.preview_view = PreviewView(
            recompute_plan(self.state.project),
            DEFAULT_PROFILE,
            central,
            layout_settings=self.state.project.layout,
        )
        layout.addWidget(self.preview_view.widget)

        # Two ways out of the app: a file, or paper. Export shares the exact
        # same SheetPlan the preview is showing, so what you save is what you
        # previewed -- and it needs no printer, so it stays enabled when
        # Print is disabled.
        self.save_pdf_button = QPushButton("Save PDF...", central)
        layout.addWidget(self.save_pdf_button)

        self.print_button = QPushButton("Print...", central)
        layout.addWidget(self.print_button)

        self.window.setCentralWidget(central)
        self.status_bar = QStatusBar(self.window)
        self.window.setStatusBar(self.status_bar)

        self.import_view.imported.connect(self._on_imported)
        self.layout_panel.layout_changed.connect(self._on_layout_changed)
        self.print_button.clicked.connect(self._on_print_clicked)
        self.save_pdf_button.clicked.connect(self._on_save_pdf_clicked)

        # Known-empty until the background query returns, so nothing reads
        # an undefined attribute if the user clicks Print immediately.
        self._printers: list[str] = []
        self._printer_thread = None
        self._printer_query: _PrinterQuery | None = None
        self.refresh_printers()

    def _on_layout_changed(self, plan) -> None:
        # Hand the preview the settings too, so its content-box guide
        # tracks the gutter/margins rather than going stale.
        self.preview_view.on_layout_changed(plan, self.state.project.layout)

    def _on_imported(self, pages, warnings) -> None:
        self.arrange_view.refresh()
        self.preview_view.on_layout_changed(recompute_plan(self.state.project))

    def refresh_printers(self, *, blocking: bool = False, timeout_ms: int | None = None) -> None:
        """Re-check available printers, off the UI thread and under a deadline.

        ``QPrinterInfo.availablePrinters()`` enumerates **network** printers
        too, and the Windows spooler blocks per printer until it times out
        when one is unreachable. Run synchronously from ``__init__`` -- as
        this was -- that means Deckle hangs on launch whenever a networked
        printer is offline. Enumeration measures ~21ms with the network up
        and unbounded without it, so it does not belong on the UI thread.

        The thread alone is not enough: the spooler's own timeout is what
        was unbounded, and a background thread parked in it forever still
        leaves the user watching "Checking for printers..." with no end.
        :class:`_PrinterQuery` puts a deadline on it, after which the app
        behaves as it does with no printers at all.

        ``blocking=True`` keeps a synchronous path for tests and the CLI,
        where there is no event loop to return to. It cannot be bounded --
        there is nothing to time out *against* -- but it goes through
        ``_PrinterQueryWorker`` so a spooler failure degrades to "no
        printers" there too rather than propagating out of a refresh.

        :param blocking: run synchronously, for tests and the CLI where
            there is no event loop to return to. Cannot be bounded -- there
            is nothing to time out against.
        :param timeout_ms: the deadline for the asynchronous path, or
            ``None`` for :data:`PRINTER_QUERY_TIMEOUT_MS`.
        :returns: nothing. The result arrives via ``_apply_printers``,
            which enables or disables **only** the Print action.
        """
        if timeout_ms is None:
            timeout_ms = PRINTER_QUERY_TIMEOUT_MS

        if blocking:
            worker = _PrinterQueryWorker()
            worker.run()
            _PrinterQuery(worker, self._apply_printers, timeout_ms).complete()
            return

        self.print_button.setEnabled(False)
        self.status_bar.showMessage("Checking for printers...")

        worker = _PrinterQueryWorker()
        query = _PrinterQuery(worker, self._apply_printers, timeout_ms)
        thread = _new_thread(self.window)
        thread.run = worker.run
        thread.finished.connect(query.complete)
        thread.finished.connect(thread.deleteLater)
        self._printer_query = query
        self._printer_thread = thread
        thread.start()
        _single_shot(timeout_ms, query.time_out)

    def _apply_printers(self, printers: list[str], no_printers_message: str | None = None) -> None:
        """Enable or disable **only** the Print action.

        Save PDF is deliberately never touched here: zero printers, a dead
        spooler and an enumeration timeout all leave Deckle fully usable as
        an imposition tool that writes a file.
        """
        self._printers = list(printers)
        has_printers = bool(printers)
        self.print_button.setEnabled(has_printers)
        if has_printers:
            self.print_button.setToolTip("")
            self.status_bar.clearMessage()
        else:
            message = no_printers_message or NO_PRINTERS_MESSAGE
            self.print_button.setToolTip(message)
            self.status_bar.showMessage(message)

    def _on_print_clicked(self) -> None:
        # Use the cached list rather than re-enumerating: a second query
        # would re-introduce exactly the block this moved off the UI thread.
        printers = self._printers
        if not printers:
            # Defensive: the button should already be disabled, but never
            # open a print dialog against zero printers even if this
            # slot is reached some other way.
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)
            return
        plan = self.preview_view.plan
        self.print_dialog = PrintDialog(plan, self.window, printer_names=printers)
        self.print_dialog.widget.exec()
        self.status_bar.showMessage(f"{len(printers)} printer(s) available.")

    def suggested_export_name(self) -> str:
        """A default filename derived from the first imported source.

        ``book.pdf`` imposed becomes ``book-deckle.pdf`` -- never the source
        name itself, so a careless Save can't overwrite the input.

        :returns: the suggested filename, or ``"deckle-output.pdf"`` when
            nothing has been imported yet.
        """
        pages = self.state.project.pages
        if not pages:
            return "deckle-output.pdf"
        stem = os.path.splitext(os.path.basename(pages[0].ref.path))[0]
        return f"{stem}-deckle.pdf"

    def _on_save_pdf_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if not self.state.project.pages:
            self.status_bar.showMessage("Nothing to export -- import a PDF or images first.")
            return

        start_dir = os.path.dirname(self.state.project.pages[0].ref.path) or os.getcwd()
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save imposed PDF",
            os.path.join(start_dir, self.suggested_export_name()),
            "PDF files (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        plan = self.preview_view.plan
        sheets = len(plan.sheets)
        self.status_bar.showMessage(f"Exporting {sheets} sheet(s) to {os.path.basename(path)}...")
        try:
            export(plan, path)
        except Exception as exc:  # surfaced, never swallowed
            self.status_bar.showMessage(f"Export failed: {exc}")
            return
        self.status_bar.showMessage(f"Saved {sheets} sheet(s) to {path}")

    def show(self) -> None:
        """Show the window.

        :returns: nothing.
        """
        self.window.show()

    def close(self) -> None:
        """Flush the pending autosave and close the window.

        :returns: nothing. The flush is not optional: the debounce window
            is exactly the interval a closing app would otherwise lose.
        """
        self.state.flush_autosave()
        self.window.close()


def main(argv: list[str] | None = None) -> int:
    """Launch the desktop app and run the Qt event loop.

    :param argv: arguments for ``QApplication``, or ``None`` to use
        ``sys.argv``.
    :returns: the event loop's exit code, for ``sys.exit``.
    """
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(argv if argv is not None else sys.argv)
    main_window = MainWindow()
    main_window.show()
    return app.exec()


if __name__ == "__main__":
    import sys

    sys.exit(main())
