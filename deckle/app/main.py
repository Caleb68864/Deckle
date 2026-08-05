"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
"""

from __future__ import annotations

import os

from deckle.app.state import AppState
from deckle.core.diagnostics import log_exception
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

# Used to seed PreviewView before any printer/profile has been chosen -- the
# same fallback resolve_profile() reaches for when a printer has no saved
# PrinterProfile yet (see deckle/app/views/print_dialog.py).
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))


class _PrinterQueryWorker:
    """Enumerates printers on a background thread.

    Plain class, not a ``QObject`` -- same shape as ``ThumbnailWorker`` and
    ``PreviewWorker``. Exists because printer enumeration can block for the
    OS spooler's timeout when a network printer is unreachable.
    """

    def __init__(self) -> None:
        self.names: list[str] = []

    def run(self) -> None:
        try:
            self.names = available_printer_names()
        except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
            # A spooler failure must not take the window down; the app is
            # fully usable for Save PDF with no printers at all. Record why,
            # though: "the printer list was empty" is otherwise a support
            # report with nothing behind it.
            log_exception("printer_enumeration_failed", exc)
            self.names = []


def available_printer_names() -> list[str]:
    """The names of printers Qt currently knows about.

    A thin, patchable seam over ``QPrinterInfo`` so callers (and tests)
    don't need a real printer attached -- an empty list is a normal,
    expected result, not an error.
    """
    from PySide6.QtPrintSupport import QPrinterInfo

    return [info.printerName() for info in QPrinterInfo.availablePrinters()]


def default_project() -> Project:
    layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)


def _qt_widgets():
    from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget

    return QHBoxLayout, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget


class MainWindow:
    """The application shell: import controls, arrange grid, print action."""

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
        self.refresh_printers()

    def _on_layout_changed(self, plan) -> None:
        # Hand the preview the settings too, so its content-box guide
        # tracks the gutter/margins rather than going stale.
        self.preview_view.on_layout_changed(plan, self.state.project.layout)

    def _on_imported(self, pages, warnings) -> None:
        self.arrange_view.refresh()
        self.preview_view.on_layout_changed(recompute_plan(self.state.project))

    def refresh_printers(self, *, blocking: bool = False) -> None:
        """Re-check available printers, off the UI thread.

        ``QPrinterInfo.availablePrinters()`` enumerates **network** printers
        too, and the Windows spooler blocks per printer until it times out
        when one is unreachable. Run synchronously from ``__init__`` -- as
        this was -- that means Deckle hangs on launch whenever a networked
        printer is offline. Enumeration measures ~21ms with the network up
        and unbounded without it, so it does not belong on the UI thread.

        ``blocking=True`` keeps a synchronous path for tests and the CLI,
        where there is no event loop to return to.
        """
        if blocking:
            self._apply_printers(available_printer_names())
            return

        from PySide6.QtCore import QThread

        self.print_button.setEnabled(False)
        self.status_bar.showMessage("Checking for printers...")

        worker = _PrinterQueryWorker()
        thread = QThread(self.window)
        thread.run = worker.run
        thread.finished.connect(lambda: self._apply_printers(worker.names))
        thread.finished.connect(thread.deleteLater)
        self._printer_thread = thread
        thread.start()

    def _apply_printers(self, printers: list[str]) -> None:
        self._printers = list(printers)
        has_printers = bool(printers)
        self.print_button.setEnabled(has_printers)
        if has_printers:
            self.print_button.setToolTip("")
            self.status_bar.clearMessage()
        else:
            self.print_button.setToolTip(NO_PRINTERS_MESSAGE)
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)

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
        self.window.show()

    def close(self) -> None:
        self.state.flush_autosave()
        self.window.close()


def main(argv: list[str] | None = None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(argv if argv is not None else sys.argv)
    main_window = MainWindow()
    main_window.show()
    return app.exec()


if __name__ == "__main__":
    import sys

    sys.exit(main())
