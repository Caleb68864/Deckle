"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once, on a background worker, at
window construction; when it comes back empty the print action is disabled
with an explanatory status message instead of opening an empty/broken print
dialog or raising. There is no printer menu and no re-query -- a printer
plugged in after launch is not seen until Deckle restarts (B30).
"""

from __future__ import annotations

import logging
import os
import warnings

from deckle.app.state import (
    AppState,
    autosave_path_for,
    unsaved_autosave_label,
    unsaved_autosave_offers,
)
from deckle.app.printer_capabilities import (
    profile_with_driver_margins,
    query_imageable_area_pt,
)
from deckle.core.defaults import load_defaults
from deckle.core.diagnostics import log_event, log_exception
from deckle.app.views.arrange_view import ArrangeView
from deckle.app.views.import_view import ImportView
from deckle.app.views.layout_panel import LayoutPanel, recompute_plan
from deckle.app.views.preview_view import PreviewView
from deckle.app.views.print_dialog import (
    PrintDialog,
    resolve_profile,
    select_preselected_printer,
)
from deckle.core.export import clear_sheet_cache, export
from deckle.core.locate import locate_page
from deckle.core.models import LayoutSettings, Project
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.project_io import (
    PathOutsideRootsAdvisory,
    SourceChangedWarning,
    SourceMissingError,
    load_project,
    save_project,
)
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile
from deckle.core import recent

LETTER_PT = (612.0, 792.0)

NO_PRINTERS_MESSAGE = "No printers installed -- connect a printer to enable printing."

NO_DOCUMENT_MESSAGE = "Import a PDF or a folder of images to begin."
"""The first thing Deckle says when it opens with nothing loaded.

Previously the only first-run message was :data:`NO_PRINTERS_MESSAGE`, so an
empty Deckle led with a complaint about hardware the user does not need yet.
The first message should name the first step.
"""

NOTHING_TO_SAVE_MESSAGE = "Nothing to save yet -- import a PDF or images first."

SAVE_PROJECT_TOOLTIP = (
    "Save this job as a .deckle project so you can come back to it.\n\n"
    "Saves the page order, rotations, skips, blanks and every layout "
    "setting -- not the PDF. Use Save PDF for the imposed document."
)

NOTHING_TO_EXPORT_MESSAGE = "Nothing to export yet -- import a PDF or images first."
"""Why Save PDF is unavailable. Shown as the button's tooltip.

Save PDF used to stay enabled with no document and scold the user *after*
they clicked it, while Print in the identical situation was disabled with an
explanation. Same class of problem deserves the same affordance.
"""

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
#
# It is a *seed*, not the answer. Enumeration is asynchronous, so the window
# is built before anything is known about the printers, and this is what the
# preview draws for the fraction of a second before `_apply_printers` lands.
# It stays the answer only when there are no printers at all.
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))


def profile_for_printers(
    printer_names,
    profile_loader=PrinterProfile.load,
    fallback: PrinterProfile = DEFAULT_PROFILE,
) -> PrinterProfile:
    """The profile the preview should be drawing, given what is installed.

    The preview's red guide, its ``clipped_by_imageable_area`` warnings and
    the layout panel's "Use printer margins" all come from one
    ``PrinterProfile``, and until now that was :data:`DEFAULT_PROFILE` for
    the life of the window -- a fixed 18pt border on every edge, no matter
    which printer was selected and no matter what had been measured for it.
    The GUIDE called that red line "your printer's hardware limit"; it was
    the first builtin preset's stand-in for one.

    Deliberately the *same* answer :class:`PrintDialog` opens with, reached
    through the same two functions: the first printer with a saved
    calibration, then that calibration. A preview drawing one printer's
    border while the print dialog is about to preselect another's would be
    a worse lie than the constant it replaces.

    Pure and Qt-free, so the choosing is testable without a display.

    :param printer_names: the printers Qt reported, in its order.
    :param profile_loader: how to load a saved profile; raising means the
        printer has none.
    :param fallback: what to answer when no printer is installed. Nothing
        has been chosen, so nothing better than the generic preset is
        available -- and Print is disabled in that state anyway.
    :returns: the profile to draw against.
    """
    chosen = select_preselected_printer(list(printer_names), profile_loader)
    if chosen is None:
        return fallback
    return resolve_profile(chosen, profile_loader)

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


def _live_threads(view) -> list:
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
    """An empty project for a freshly launched window.

    Uses the user's saved defaults when there are any, and US Letter with
    no gutter and no margins when there are not. Someone who buys the same
    paper every time should not reset a dozen controls before the first
    useful preview.

    :returns: a project with no pages and no printer.
    """
    layout = load_defaults()
    if layout is None:
        layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)


def _qt_vertical():
    """``Qt.Orientation.Vertical``, imported lazily like every other Qt name.

    :returns: the enum member.
    """
    from PySide6.QtCore import Qt

    return Qt.Orientation.Vertical


def autosave_recovery_offer(project_path: str | None) -> str | None:
    """The autosave worth offering back, or ``None`` to stay silent.

    Autosave has been written on every edit and flushed on close since the
    beginning, and nothing ever offered it back -- the file was a corpse.
    This is the decision that changes that, kept pure and out of the
    dialog so it can be tested without a display.

    **Detection cannot be "does the file exist".** ``_on_close_event``
    flushes the autosave, so it exists after every clean quit. It is
    *mtime*: offer when the autosave is newer than the project.

    That rule is chosen for the crash case and gets close-without-saving
    right as a consequence -- those edits are real, the user declined to
    save them, and offering them back is correct rather than a false
    positive. Saving makes the project newer, which is what stops the
    prompt appearing on every open.

    Ties go to silence. A spurious prompt teaches someone to dismiss
    prompts, which costs more than the rare recovery it would offer.

    :param project_path: where the project lives, or ``None`` for one
        that has never been saved and so has no autosave.
    :returns: the autosave path, or ``None``.
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


def _recent_label(path: str) -> str:
    """A menu label for a recent project: its name, then its folder.

    Two projects called ``book.deckle`` in different folders are a normal
    thing to have, and a list showing the same word twice would be worse
    than no list.
    """
    return f"{os.path.basename(path)}  --  {os.path.dirname(path)}"


def _new_menu(parent):
    """A ``QMenu``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMenu

    return QMenu(parent)


def _qt_message_box():
    """``QMessageBox``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMessageBox

    return QMessageBox


def _qt_widgets():
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QSplitter,
        QStatusBar,
        QVBoxLayout,
        QWidget,
    )

    return (
        QHBoxLayout, QMainWindow, QPushButton, QScrollArea, QSplitter,
        QStatusBar, QVBoxLayout, QWidget,
    )


class MainWindow:
    """The application shell: import controls, arrange grid, print action.

    :param project_path: the project to open, or ``None`` for a new one.
        Also decides where autosave writes.
    """

    def __init__(self, project_path: str | None = None) -> None:
        (
            QHBoxLayout,
            QMainWindow,
            QPushButton,
            QScrollArea,
            QSplitter,
            QStatusBar,
            QVBoxLayout,
            QWidget,
        ) = _qt_widgets()

        self.state = AppState(default_project(), project_path=project_path)

        #: How a saved calibration is loaded. Injected rather than reached
        #: for, so a test can drive a calibrated printer without writing
        #: one into the user's real config directory -- and so the preview
        #: and the print dialog provably read the same source.
        self.profile_loader = PrinterProfile.load
        #: The profile the preview and the layout panel are drawing
        #: against. Replaced by :meth:`set_printer_profile` once
        #: enumeration answers; see :func:`profile_for_printers`.
        self.profile = DEFAULT_PROFILE

        self.window = QMainWindow()
        self.window.setWindowTitle("Deckle")
        # Closing the window is the usual way out, and it does not go
        # through `close()` below -- Qt calls closeEvent directly. Without
        # this, quitting mid-render crashed on exit.
        self.window.closeEvent = self._on_close_event
        # Controls on the left, the sheets on the right.
        #
        # Everything used to sit in one vertical column, which meant the
        # preview -- the whole reason this app exists, "see the physical
        # sheets before you commit paper" -- got whatever height was left
        # after four control panels. A splitter puts the settings where you
        # set them once and the paper where you look constantly, and lets
        # the user rebalance if their screen disagrees with the default.
        central = QWidget(self.window)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(central)
        central_layout.addWidget(self.splitter)

        # -- left: what you set ------------------------------------------
        controls = QWidget(self.splitter)
        controls_layout = QVBoxLayout(controls)

        self.import_view = ImportView(self.state, controls)
        controls_layout.addWidget(self.import_view.widget)

        self.layout_panel = LayoutPanel(self.state, controls, profile=self.profile)
        controls_layout.addWidget(self.layout_panel.widget)

        controls_layout.addStretch(1)

        # Two ways out of the app: a file, or paper. Export shares the exact
        # same SheetPlan the preview is showing, so what you save is what you
        # previewed -- and it needs no printer, so it stays enabled when
        # Print is disabled. They sit at the bottom of the controls column
        # because they are the end of the workflow, not part of it.
        # Undo and redo. AppState has carried a bounded 50-step history
        # since the MVP -- every view routes its mutations through
        # `mutate` precisely so undo is uniform rather than per-view -- and
        # until now nothing could invoke it. Reordering 266 pages without a
        # way back is the kind of risk the machinery was built to remove.
        history_row = QHBoxLayout()
        self.undo_button = QPushButton("Undo", controls)
        self.undo_button.setToolTip("Undo the last change (Ctrl+Z)")
        self.redo_button = QPushButton("Redo", controls)
        self.redo_button.setToolTip("Redo the change you just undid (Ctrl+Y)")
        history_row.addWidget(self.undo_button)
        history_row.addWidget(self.redo_button)
        controls_layout.addLayout(history_row)

        # Opening and saving the project itself, above the two ways OUT of
        # the app. A project is the job you are working on; a PDF and a
        # print run are what you produce from it.
        self.open_project_button = QPushButton("Open project...", controls)
        self.open_project_button.setToolTip(
            "Open a .deckle project: the pages you imported, the order you "
            "put them in, and every layout setting.\n\n"
            "A project records where its sources live rather than copying "
            "them, so it stays small -- and Deckle checks they have not "
            "changed since you saved."
        )
        controls_layout.addWidget(self.open_project_button)

        # A menu rather than a submenu of Open: the list is the whole
        # point, and burying it one click deeper than the dialog it
        # exists to save you from would defeat it.
        self.recent_button = QPushButton("Recent projects", controls)
        self.recent_button.setToolTip(
            "Projects you have opened or saved, most recent first.\n\n"
            "A project on a drive that is not currently connected is "
            "hidden rather than forgotten, and comes back when the "
            "drive does."
        )
        self._recent_menu = _new_menu(self.recent_button)
        self.recent_button.setMenu(self._recent_menu)
        controls_layout.addWidget(self.recent_button)

        self.save_project_button = QPushButton("Save project...", controls)
        self.save_project_button.setToolTip(SAVE_PROJECT_TOOLTIP)
        controls_layout.addWidget(self.save_project_button)

        self.save_pdf_button = QPushButton("Save PDF...", controls)
        controls_layout.addWidget(self.save_pdf_button)

        self.print_button = QPushButton("Print...", controls)
        controls_layout.addWidget(self.print_button)

        # The controls column has a natural width; below it the spinboxes
        # start truncating their own labels, which is worse than scrolling.
        controls_scroll = QScrollArea(self.splitter)
        controls_scroll.setWidget(controls)
        controls_scroll.setWidgetResizable(True)
        # 420, not 320: at 320 the margin spinboxes are pushed out of the
        # pane and the column grows a horizontal scrollbar, which is the
        # one kind of scrolling a settings form should never need.
        controls_scroll.setMinimumWidth(420)
        self.splitter.addWidget(controls_scroll)

        # -- right: what you get -----------------------------------------
        # Preview above, page grid below. Both want width, and stacking
        # them means you can drag a page into a new position and watch the
        # sheet it lands on redraw.
        output = QSplitter(self.splitter)
        output.setOrientation(_qt_vertical())

        self.preview_view = PreviewView(
            recompute_plan(self.state.project),
            self.profile,
            output,
            layout_settings=self.state.project.layout,
        )
        output.addWidget(self.preview_view.widget)

        self.arrange_view = ArrangeView(self.state, output)
        output.addWidget(self.arrange_view.widget)

        # The preview is the point; the page grid is a means of reordering.
        output.setStretchFactor(0, 3)
        output.setStretchFactor(1, 1)
        # A pane dragged shut looks like a bug, not a choice.
        output.setChildrenCollapsible(False)
        self.output_splitter = output

        self.splitter.addWidget(output)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)

        self.window.setCentralWidget(central)
        self.window.resize(1280, 860)
        # Explicit opening proportions rather than whatever Qt negotiates
        # from size hints: the controls want a fixed, readable column and
        # everything else belongs to the paper.
        self.splitter.setSizes([440, 840])
        output.setSizes([620, 240])
        self.status_bar = QStatusBar(self.window)
        self.window.setStatusBar(self.status_bar)

        self.import_view.imported.connect(self._on_imported)
        self.arrange_view.pages_changed.connect(self._on_pages_changed)
        self.arrange_view.page_selected.connect(self._on_page_selected)
        self.layout_panel.layout_changed.connect(self._on_layout_changed)
        self.layout_panel.schedule_saved.connect(self.status_bar.showMessage)
        self.print_button.clicked.connect(self._on_print_clicked)
        self.save_pdf_button.clicked.connect(self._on_save_pdf_clicked)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.open_project_button.clicked.connect(self._on_open_project_clicked)
        self.save_project_button.clicked.connect(self._on_save_project_clicked)

        # Known-empty until the background query returns, so nothing reads
        # an undefined attribute if the user clicks Print immediately.
        self._printers: list[str] = []
        #: What each driver said its non-printable border is, once
        #: enumeration has answered. Empty until then, and empty for every
        #: printer that declined -- see `deckle.app.printer_capabilities`.
        self._imageable_areas: dict[str, tuple[float, float, float, float]] = {}
        self._printer_thread = None
        # Injected so the recovery prompt can be driven headlessly, the
        # way `print_dialog` injects `_confirm_resume`.
        self.confirm_recovery = self._default_confirm_recovery
        self.confirm_unsaved_recovery = self._default_confirm_unsaved_recovery
        self._printer_query: _PrinterQuery | None = None
        #: A printer fault worth showing, or "" when there is none.
        #: Held rather than written straight to the status bar so the
        #: bar has ONE writer and a later import cannot leave a stale
        #: instruction up.
        self._printer_message = ""
        self._refresh_recent_menu()
        self._sync_document_actions()
        self._sync_history_actions()
        self._install_shortcuts()
        if project_path is None:
            # Only for a fresh window. A window opened ON a project already
            # has `_recover_autosave_if_offered` for its own autosave, and
            # offering someone else's unsaved session on top of it would be
            # two recovery prompts for one launch.
            self._offer_unsaved_recovery()
        self.refresh_printers()

    def _sync_document_actions(self) -> None:
        """Enable the document actions only when there is a document.

        Save PDF is gated here rather than checking inside its own click
        handler, so an unavailable action looks unavailable instead of
        accepting the click and then explaining itself. Print is gated
        separately by :meth:`_apply_printers`, since it additionally needs
        a printer.
        """
        has_pages = bool(self.state.project.pages)
        self.save_pdf_button.setEnabled(has_pages)
        # Opening is always available; saving needs something to save.
        self.save_project_button.setEnabled(has_pages)
        self.save_project_button.setToolTip(
            SAVE_PROJECT_TOOLTIP if has_pages else NOTHING_TO_SAVE_MESSAGE
        )
        self.save_pdf_button.setToolTip("" if has_pages else NOTHING_TO_EXPORT_MESSAGE)
        self.layout_panel.set_document_loaded(has_pages)

    def _on_layout_changed(self, plan) -> None:
        # Hand the preview the settings too, so its content-box guide
        # tracks the gutter/margins rather than going stale.
        self.preview_view.on_layout_changed(plan, self.state.project.layout)

    def _on_imported(self, pages, warnings) -> None:
        self.arrange_view.refresh()
        self._sync_document_actions()
        # An import is a project mutation like any other, so it lands on
        # the undo stack -- the buttons have to notice.
        self._sync_history_actions()
        # The status bar may still be telling the user to import something.
        # That message is correct only while nothing is loaded; leaving it up
        # after an import means the app is giving an instruction the user has
        # already carried out.
        self._refresh_status_message()
        self.preview_view.on_layout_changed(recompute_plan(self.state.project))

    def _on_pages_changed(self) -> None:
        """Re-impose after the document itself changed.

        :returns: nothing.

        Reordering, rotating, skipping or inserting a blank all change
        which pages land on which sheets. Without this the preview kept
        showing the plan from before the edit, and because Save PDF exports
        *the preview's* plan -- deliberately, so that what you save is what
        you saw -- the edit reached neither the screen nor the paper.
        """
        self.preview_view.on_layout_changed(
            recompute_plan(self.state.project), self.state.project.layout
        )
        self._sync_document_actions()
        self._refresh_status_message()
        self._sync_history_actions()

    def _on_page_selected(self, page_index: int) -> None:
        """Follow the arrange grid's selection in the preview.

        The grid shows the document and the preview shows the paper, and
        the map between them is the whole point of imposition -- so
        clicking page 41 should show the sheet carrying it rather than
        leaving the user to find it with a spinbox.

        :param page_index: the document page, 0-based.
        :returns: nothing. A page with no sheet of its own leaves the
            preview where it is.
        """
        location = locate_page(
            self.preview_view.plan, list(self.state.project.pages), page_index
        )
        if location is not None:
            self.preview_view.go_to_sheet(location.sheet_index, location.side)

    def _refresh_status_message(self) -> None:
        """Say the most useful true thing about the current state.

        There are three, in order of precedence: a printer fault the user
        cannot otherwise see, then the next step when nothing is loaded,
        then nothing at all. Silence is the right answer for a document
        that is ready to print -- the status bar is not a place to announce
        that everything is fine.
        """
        if self._printer_message:
            self.status_bar.showMessage(self._printer_message)
        elif not self.state.project.pages:
            self.status_bar.showMessage(NO_DOCUMENT_MESSAGE)
        else:
            self.status_bar.clearMessage()

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

    def _apply_printers(
        self,
        printers: list[str],
        no_printers_message: str | None = None,
        imageable_areas: dict[str, tuple[float, float, float, float]] | None = None,
    ) -> None:
        """Enable or disable **only** the Print action.

        Save PDF is deliberately never touched here: zero printers, a dead
        spooler and an enumeration timeout all leave Deckle fully usable as
        an imposition tool that writes a file.

        :param printers: the enumerated printer names.
        :param no_printers_message: why there are none, when something
            went wrong rather than none being installed.
        :param imageable_areas: what each driver said its non-printable
            border is. Defaulted, because four tests call this unbound
            with two positional arguments and none of them are about
            margins.
        """
        self._printers = list(printers)
        self._imageable_areas = dict(imageable_areas or {})
        has_printers = bool(printers)
        self.print_button.setEnabled(has_printers)
        if has_printers:
            self.print_button.setToolTip("")
            self._printer_message = ""
        else:
            message = no_printers_message or NO_PRINTERS_MESSAGE
            self.print_button.setToolTip(message)
            # An explanation of something that WENT WRONG outranks a
            # next-step hint. `no_printers_message` is only ever passed when
            # enumeration actually failed (it carries
            # PRINTER_TIMEOUT_MESSAGE), and a user who cannot print needs to
            # know the spooler was unreachable even if they have not
            # imported anything yet. Routine "no printers installed",
            # though, is not news on an empty Deckle.
            self._printer_message = (
                message if (no_printers_message or self.state.project.pages) else ""
            )
        self._refresh_status_message()
        # Enumeration is the first moment the window knows which printer
        # it is drawing for. Until this call existed, it never found out.
        self.set_printer_profile(
            self._profile_with_driver_answer(
                profile_for_printers(self._printers, self.profile_loader)
            )
        )

    def _profile_with_driver_answer(self, profile: PrinterProfile) -> PrinterProfile:
        """``profile``, with the driver's border in place of the preset's.

        **A saved calibration is never overwritten.** It exists because
        somebody printed a target and measured it with a ruler; the
        driver's number has been checked against nothing. Where the two
        disagree the ruler is right, so the substitution happens only when
        the profile is the generic preset standing in for a calibration
        nobody has done -- which is exactly the case the red guide was
        lying about.

        :param profile: the resolved profile.
        :returns: it, or a copy carrying the driver's border.
        """
        name = select_preselected_printer(self._printers, self.profile_loader)
        if name is None:
            return profile
        try:
            self.profile_loader(name)
        except (FileNotFoundError, OSError, ValueError):
            pass  # uncalibrated: the preset is a stand-in, so fill it in
        else:
            return profile
        return profile_with_driver_margins(profile, self._imageable_areas.get(name))

    def set_printer_profile(self, profile: PrinterProfile) -> None:
        """Draw the preview and the margins against ``profile``.

        Three things read a ``PrinterProfile`` outside the print path and
        all three were pinned to :data:`DEFAULT_PROFILE` for the life of
        the window: the preview's red imageable-area guide, the
        ``clipped_by_imageable_area`` warnings computed beside it, and the
        layout panel's "Use printer margins" button. So the app showed a
        fixed 18pt border and offered to adopt it, on a machine whose
        printer had been measured and whose measurement was sitting in a
        file Deckle had already read.

        :param profile: the profile to draw against.
        :returns: nothing. A no-op when the profile has not actually
            changed -- this is reached on every printer refresh, and a
            re-render costs a rasterisation of the visible sheet.
        """
        if profile == self.profile:
            return
        self.profile = profile
        self.layout_panel.profile = profile
        self.preview_view.profile = profile
        # The guide is painted from the profile and the warnings are
        # computed in the render worker from it, so both need the sheet
        # drawn again -- setting the attribute alone would leave the old
        # border on screen until something else happened to refresh.
        self.preview_view.refresh()

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
        self.print_dialog = PrintDialog(
            plan, self.window, printer_names=printers,
            profile_loader=self.profile_loader,
        )
        self.print_dialog.widget.exec()
        # The dialog is where the printer and its paper behaviour are
        # actually chosen (B16). Picking a face-up printer there and coming
        # back to a preview still drawing the first preset's border would
        # put the two halves of the same answer on screen at once.
        self.set_printer_profile(self.print_dialog.selected_profile())
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

    def _install_shortcuts(self) -> None:
        """Bind Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) to the history.

        :returns: nothing.

        Ctrl+Shift+Z as well as Ctrl+Y because both are muscle memory
        depending on which applications someone lives in, and a shortcut
        that silently does nothing is worse than one that does not exist.
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        self._shortcuts = [
            QShortcut(QKeySequence.StandardKey.Undo, self.window, self.undo),
            QShortcut(QKeySequence.StandardKey.Redo, self.window, self.redo),
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self.window, self.redo),
        ]

    def undo(self) -> None:
        """Step the project back one change and re-show it.

        :returns: nothing. A no-op when there is nothing to undo.
        """
        if not self.state.can_undo:
            return
        self.state.undo()
        self._after_history_change()

    def redo(self) -> None:
        """Reapply the change just undone.

        :returns: nothing. A no-op when there is nothing to redo.
        """
        if not self.state.can_redo:
            return
        self.state.redo()
        self._after_history_change()

    def _after_history_change(self) -> None:
        """Re-show everything, because undo can change anything.

        :returns: nothing.

        A single undo may restore a page order, a rotation, a margin or the
        fold scheme -- ``mutate`` is uniform, so history is too. Refreshing
        only the view that happened to make the change would leave the
        others describing a document that no longer exists.
        """
        self.arrange_view.refresh()
        self.layout_panel.refresh_from_project()
        self._on_pages_changed()
        self._sync_history_actions()

    def _sync_history_actions(self) -> None:
        """Enable each button only when it would do something."""
        self.undo_button.setEnabled(self.state.can_undo)
        self.redo_button.setEnabled(self.state.can_redo)

    def _recover_autosave_if_offered(self, path: str, project: Project) -> Project:
        """Offer a newer autosave in place of the project just loaded.

        Autosave was written on every edit and flushed on close and never
        offered back. This is where it stops being a corpse.

        Declining **deletes** the autosave. Leaving it would bring the
        prompt back on every subsequent open, which trains someone to
        dismiss it -- and the one that matters is the one they then
        dismiss without reading.

        A failure to load the autosave leaves the project as it was: the
        recovery file is the damaged one by definition here, so falling
        back to the saved project is the safe direction.

        :param path: the project file just opened.
        :param project: what was loaded from it.
        :returns: the recovered project, or ``project`` unchanged.
        """
        autosave_path = autosave_recovery_offer(path)
        if autosave_path is None:
            return project
        if not self.confirm_recovery(os.path.basename(path)):
            try:
                os.remove(autosave_path)
            except OSError as exc:  # noqa: BLE001 -- declined, never fatal
                log_exception("autosave_discard_failed", exc, path=autosave_path)
            return project
        try:
            recovered = load_project(
                autosave_path, allowed_roots=(os.path.dirname(path),)
            )
        except Exception as exc:  # noqa: BLE001 -- reported, never a crash
            self.status_bar.showMessage(
                f"Could not read the recovered changes for "
                f"{os.path.basename(path)}: {exc}"
            )
            log_exception("autosave_recovery_failed", exc, path=autosave_path)
            return project
        log_event("autosave_recovered", path=path, pages=len(recovered.pages))
        return recovered

    def _offer_unsaved_recovery(self) -> None:
        """Offer back work from a session that never got as far as Save.

        :returns: nothing, and never raises. A recovery store that cannot
            be read must not be what stops a window opening.

        **Declining deletes nothing.** ``_recover_autosave_if_offered``
        deletes on decline because the work also exists in the user's own
        ``.deckle``; here it does not -- this file is the only copy -- so a
        mis-click must not be destructive. ``UNSAVED_AUTOSAVE_MAX_AGE_S``
        and ``UNSAVED_AUTOSAVE_KEEP`` are what stop the list growing
        instead.
        """
        offers = unsaved_autosave_offers()
        if not offers:
            return
        chosen = self.confirm_unsaved_recovery(offers)
        if chosen is None:
            return
        try:
            # `check_sources=False` for the same reason
            # `unsaved_autosave_offers` reads the JSON directly: a source
            # that has moved raises `SourceMissingError`, and a moved
            # source is exactly when this recovery matters most. There is
            # no other copy of the arrangement to fall back on, so
            # refusing to load it would be refusing the whole feature at
            # the moment it is needed. Thumbnails degrade to placeholders
            # and the import view can point at the file again.
            #
            # `on_outside_roots` returns True rather than passing
            # `allowed_roots`: the sources of an unsaved project are
            # wherever the user imported from, and there is no project
            # directory to reason from.
            project = load_project(
                chosen.path,
                check_sources=False,
                on_outside_roots=lambda _path, _roots: True,
            )
        except Exception as exc:  # noqa: BLE001 -- a window is opening
            self.status_bar.showMessage(f"Could not read the recovered work: {exc}")
            log_exception(
                "unsaved_autosave_recovery_failed", exc, path=chosen.path
            )
            return
        clear_sheet_cache()
        # `project_path=None` deliberately: the work is still unsaved, and
        # the new `AppState` re-derives the same key from the same sources,
        # so continuing to edit keeps writing to the same file.
        self.state = AppState(project, project_path=None)
        self.import_view.state = self.state
        self.arrange_view.state = self.state
        self.layout_panel.state = self.state
        self.arrange_view.refresh()
        self.layout_panel.refresh_from_project()
        self._on_pages_changed()
        self.status_bar.showMessage(
            f"Recovered {len(project.pages)} unsaved page(s)."
        )
        log_event(
            "unsaved_autosave_recovered",
            path=chosen.path,
            pages=len(project.pages),
        )

    def _default_confirm_unsaved_recovery(self, offers):
        """Ask which unsaved session to pick up, if any.

        :param offers: the :class:`~deckle.app.state.UnsavedAutosave`
            entries, newest first.
        :returns: the chosen offer, or ``None`` to start fresh.
        """
        from PySide6.QtWidgets import QInputDialog

        labels = [unsaved_autosave_label(offer) for offer in offers]
        start_fresh = "Start a new project"
        label, ok = QInputDialog.getItem(
            self.window,
            "Recover unsaved work?",
            "Deckle closed with work that was never saved. Pick it up?",
            labels + [start_fresh],
            0,
            False,
        )
        if not ok or label == start_fresh:
            return None
        return offers[labels.index(label)]

    def _default_confirm_recovery(self, project_name: str) -> bool:
        """Ask whether to take the autosave. Replaceable for tests."""
        QMessageBox = _qt_message_box()
        answer = QMessageBox.question(
            self.window,
            "Recover unsaved changes?",
            f"{project_name} has changes that were never saved -- Deckle "
            "either closed unexpectedly or was closed without saving.\n\n"
            "Recover them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _refresh_recent_menu(self) -> None:
        """Rebuild the Recent projects menu from the store.

        Rebuilt rather than appended to, so an entry cannot appear twice
        after a project is reopened and so a file that has since gone
        drops out without any bookkeeping to keep in step.

        :returns: nothing, and never raises. A convenience menu that
            could not be built must not be what stops the window opening.
        """
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        try:
            menu.clear()
            paths = recent.existing()
            for path in paths:
                action = menu.addAction(_recent_label(path))
                action.setToolTip(path)
                action.triggered.connect(
                    lambda _checked=False, target=path: self.open_project(target)
                )
            self.recent_button.setEnabled(bool(paths))
        except Exception as exc:  # noqa: BLE001 -- convenience, never fatal
            log_exception("recent_menu_refresh_failed", exc)

    def _on_open_project_clicked(self) -> None:
        """Open a saved project, replacing whatever is loaded.

        :returns: nothing. Every failure is reported in the status bar; a
            project that cannot be opened leaves the current one alone
            rather than half-replacing it.
        """
        from PySide6.QtWidgets import QFileDialog

        # Start where the last project came from. An empty string here
        # meant every open began wherever the OS thought best, which is
        # rarely the folder holding the job you are working on.
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Open project",
            recent.last_directory(),
            "Deckle projects (*.deckle)",
        )
        if not path:
            return
        self.open_project(path)

    def open_project(self, path: str) -> bool:
        """Load ``path`` into the window.

        :param path: the ``.deckle`` to open.
        :returns: whether it opened.

        Separated from the dialog so the whole flow is drivable without a
        modal -- the same seam ``PrintDialog`` uses.

        The two ways a project outlives its sources are reported
        differently on purpose. A missing file is obvious once named. A
        source that still exists but has CHANGED is the dangerous one:
        nothing looks wrong, and imposing it would use content the user has
        never reviewed.
        """
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
            self.status_bar.showMessage(
                f"Cannot open {os.path.basename(path)}: a source file is "
                f"missing -- {exc.expected_path}."
            )
            log_exception("project_source_missing", exc, path=path)
            return False
        except SourceChangedWarning as exc:
            self.status_bar.showMessage(
                f"Cannot open {os.path.basename(path)}: {os.path.basename(exc.path)} "
                "has changed since the project was saved. Re-import it to "
                "accept the new version."
            )
            log_exception("project_source_changed", exc, path=path)
            return False
        except Exception as exc:  # noqa: BLE001 -- reported, never a crash
            self.status_bar.showMessage(f"Cannot open {os.path.basename(path)}: {exc}")
            log_exception("project_open_failed", exc, path=path)
            return False

        project = self._recover_autosave_if_offered(path, project)

        recent.record(path)
        self._refresh_recent_menu()
        # The outgoing project's cached sheet renders are unreachable the
        # moment the plan changes -- their key is the plan hash -- so they
        # are dead weight in temp until something removes them. Removed
        # here rather than only at exit, because scrubbing one long
        # document and then opening another is an ordinary session.
        clear_sheet_cache()
        # The outgoing AppState is about to become unreachable while it is
        # still holding up to `autosave_delay_s` of edits behind a debounce
        # timer, and dropping the reference does not cancel that timer.
        # Both halves of that are bugs. The user loses the last half-second
        # of work on the project they are leaving -- the interval autosave
        # exists to protect -- and the orphaned daemon timer then fires
        # against the *old* project and writes it to the old project's
        # autosave, minutes after the user moved on, so the next open of
        # that project offers back a file whose mtime says "you crashed".
        #
        # Flushing does both jobs at once: it cancels the timer and writes
        # what the timer was holding. Same call `close()` makes, for the
        # same reason -- swapping the project out is a close as far as the
        # outgoing state is concerned.
        #
        # After `_recover_autosave_if_offered`, not before: the offer is
        # decided on the autosave's mtime, and flushing first would make
        # reopening the currently-loaded project always look like a crash.
        self._flush_outgoing_state()
        self.state = AppState(project, project_path=path)
        self.import_view.state = self.state
        self.arrange_view.state = self.state
        self.layout_panel.state = self.state
        self.arrange_view.refresh()
        self.layout_panel.refresh_from_project()
        self._on_pages_changed()
        self.status_bar.showMessage(
            f"Opened {os.path.basename(path)} -- {len(project.pages)} page(s)."
        )
        log_event("project_opened", path=path, pages=len(project.pages))
        return True

    def _flush_outgoing_state(self) -> None:
        """Write and disarm the ``AppState`` that is about to be replaced.

        :returns: nothing, and never raises. An autosave that cannot be
            written must not be what stops the user opening another
            project -- they asked for the new project, and refusing it
            would lose the new work as well as the old.
        """
        try:
            self.state.flush_autosave()
        except OSError as exc:
            log_exception(
                "autosave_flush_failed", exc, path=self.state.autosave_path
            )

    def _on_save_project_clicked(self) -> None:
        """Save the current job as a ``.deckle``.

        :returns: nothing. Uses the same wording as the CLI for a
            destination it cannot write -- see :mod:`deckle.core.outputs`.
        """
        from PySide6.QtWidgets import QFileDialog

        if not self.state.project.pages:
            self.status_bar.showMessage(NOTHING_TO_SAVE_MESSAGE)
            return

        source = self.state.project.pages[0].ref.path
        suggested = os.path.join(
            os.path.dirname(source) or os.getcwd(),
            os.path.splitext(os.path.basename(source))[0] + ".deckle",
        )
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Save project", suggested, "Deckle projects (*.deckle)"
        )
        if not path:
            return
        if not path.lower().endswith(".deckle"):
            path += ".deckle"

        problem = output_path_problem(path)
        if problem is not None:
            self.status_bar.showMessage(problem)
            log_event("project_path_rejected", path=path, detail=problem)
            return

        try:
            save_project(self.state.project, path)
        except OSError as exc:
            self.status_bar.showMessage(describe_write_failure(path, exc))
            log_exception("project_write_failed", exc, path=path)
            return
        self.state.project_path = path
        # The work now exists in a file the user named, so the never-saved
        # copy under `data_dir("autosave")` would only be an offer to
        # recover something they already have. Re-derives the key from the
        # current pages, which the save did not change.
        self.state.discard_unsaved_autosave()
        # Saving is how a project first comes into existence, so it
        # belongs in the list as much as opening one does.
        recent.record(path)
        self._refresh_recent_menu()
        self.status_bar.showMessage(f"Saved project to {path}")
        log_event("project_saved", path=path, pages=len(self.state.project.pages))

    def _on_save_pdf_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if not self.state.project.pages:
            # Defensive: the button is disabled in this state. Never open a
            # save dialog for a document that does not exist.
            self.status_bar.showMessage(NOTHING_TO_EXPORT_MESSAGE)
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

        # Check the destination before doing any work, and say the same
        # thing the CLI says -- one document, two front ends, one
        # explanation. `deckle.core.outputs` owns the wording.
        source = self.state.project.pages[0].ref.path
        problem = output_path_problem(path, source)
        if problem is not None:
            self.status_bar.showMessage(problem)
            log_event("output_path_rejected", path=path, detail=problem)
            return

        plan = self.preview_view.plan
        sheets = len(plan.sheets)
        self.status_bar.showMessage(f"Exporting {sheets} sheet(s) to {os.path.basename(path)}...")
        try:
            export(plan, path)
        except OSError as exc:
            # The common failures are all OSError and all explainable: the
            # file is open in a viewer, the drive went away, the disk is
            # full. Anything else is a bug and should still surface as one.
            message = describe_write_failure(path, exc)
            self.status_bar.showMessage(message)
            log_exception("output_write_failed", exc, path=path)
            return
        except Exception as exc:  # noqa: BLE001 -- surfaced, never swallowed
            self.status_bar.showMessage(f"Export failed: {exc}")
            log_exception("export_failed", exc, path=path)
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
        self.stop_background_work()
        self.window.close()

    def _on_close_event(self, event) -> None:
        """Shut down cleanly however the window was closed.

        :param event: the ``QCloseEvent``; always accepted. Refusing to
            close because a render is running would trap the user.
        :returns: nothing.
        """
        self.state.flush_autosave()
        self.stop_background_work()
        event.accept()

    def stop_background_work(self, timeout_ms: int = 5000) -> None:
        """Cancel in-flight renders and wait for their threads to finish.

        Nothing did this, so quitting mid-render left preview and thumbnail
        threads running into interpreter teardown -- where they called
        pdfium after it had been finalised and took the process down with
        an access violation. The user sees a crash on exit, on the one
        action that is supposed to be safe.

        Both workers already support cancellation; they simply were never
        asked, and nobody waited.

        ``layout_panel`` joined the list when the Crop & trim tab started
        compositing the document off-thread (N11). A third render source
        that is not on this list is the exact shutdown crash
        :func:`_live_threads` was written for.

        :param timeout_ms: how long to wait per thread. A render that
            ignores cancellation must not hang the quit -- a stuck thread
            is a worse outcome than an abandoned one, and the wait is
            bounded for that reason.
        :returns: nothing. Never raises: this runs while the app is
            closing, and an exception here would replace a clean exit with
            the crash it exists to prevent.
        """
        for view in (self.preview_view, self.arrange_view, self.layout_panel):
            try:
                worker = getattr(view, "_worker", None)
                if worker is not None:
                    worker.cancel.set()
            except Exception as exc:  # pragma: no cover - defensive
                log_exception("shutdown_cancel_failed", exc)

        for view in (self.preview_view, self.arrange_view, self.layout_panel):
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
