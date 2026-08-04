"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
"""

from __future__ import annotations

from deckle.app.state import AppState
from deckle.app.views.arrange_view import ArrangeView
from deckle.app.views.import_view import ImportView
from deckle.app.views.layout_panel import LayoutPanel, recompute_plan
from deckle.app.views.preview_view import PreviewView
from deckle.app.views.print_dialog import PrintDialog
from deckle.core.models import LayoutSettings, Project
from deckle.core.profiles import BUILTIN_PRESETS

LETTER_PT = (612.0, 792.0)

NO_PRINTERS_MESSAGE = "No printers installed -- connect a printer to enable printing."

# Used to seed PreviewView before any printer/profile has been chosen -- the
# same fallback resolve_profile() reaches for when a printer has no saved
# PrinterProfile yet (see deckle/app/views/print_dialog.py).
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))


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

        self.layout_panel = LayoutPanel(self.state, central)
        layout.addWidget(self.layout_panel.widget)

        self.preview_view = PreviewView(
            recompute_plan(self.state.project), DEFAULT_PROFILE, central
        )
        layout.addWidget(self.preview_view.widget)

        self.print_button = QPushButton("Print...", central)
        layout.addWidget(self.print_button)

        self.window.setCentralWidget(central)
        self.status_bar = QStatusBar(self.window)
        self.window.setStatusBar(self.status_bar)

        self.import_view.imported.connect(self._on_imported)
        self.layout_panel.layout_changed.connect(self.preview_view.on_layout_changed)
        self.print_button.clicked.connect(self._on_print_clicked)

        self.refresh_printers()

    def _on_imported(self, pages, warnings) -> None:
        self.arrange_view.refresh()
        self.preview_view.on_layout_changed(recompute_plan(self.state.project))

    def refresh_printers(self) -> None:
        """Re-check available printers and disable Print when there are none."""
        printers = available_printer_names()
        has_printers = bool(printers)
        self.print_button.setEnabled(has_printers)
        if has_printers:
            self.print_button.setToolTip("")
            self.status_bar.clearMessage()
        else:
            self.print_button.setToolTip(NO_PRINTERS_MESSAGE)
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)

    def _on_print_clicked(self) -> None:
        printers = available_printer_names()
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
