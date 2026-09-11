"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once, on a background worker, at
window construction; when it comes back empty the print action is disabled
with an explanatory status message instead of opening an empty/broken print
dialog or raising. There is no printer menu and no re-query -- a printer
plugged in after launch is not seen until Deckle restarts (B30).

What this module is, and is not (M3). It is the window: splitters,
buttons, tooltips, the status bar, the menu bar's target methods, the
drop handlers, the modal prompts, and the enable/disable rules that keep
a greyed button and a live menu item from disagreeing. What each command
*does* lives next door -- :mod:`deckle.app.printer_query` for
enumeration under a deadline, :mod:`deckle.app.project_actions` for the
``.deckle`` file and its recovery, :mod:`deckle.app.exporting` for the
PDF, :mod:`deckle.app.shutdown` for the cancel-and-wait on the way out --
and the window keeps a thin method for each, because that is the name a
menu entry, a ``clicked`` signal or a test reaches for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Sequence

from deckle.app.state import AppState, unsaved_autosave_label
from deckle.app.printer_capabilities import profile_with_driver_margins

# Re-exported, and the ``noqa`` is load-bearing. ``MainWindow.refresh_printers``
# stays in this module and resolves ``_PrinterQueryWorker``, ``_new_thread``
# and ``_single_shot`` in *these* globals, which is what makes
# ``monkeypatch.setattr(app_main, "_single_shot", ...)`` work in
# ``tests/test_hardening_printing.py``. Note the asymmetry: a re-export makes
# a name readable, not assignable -- ``available_printer_names`` is called
# inside ``printer_query``'s own globals, so it must be patched there.
from deckle.app.printer_query import (  # noqa: F401
    PRINTER_QUERY_TIMEOUT_MS,
    PRINTER_TIMEOUT_MESSAGE,
    _new_thread,
    _PrinterQuery,
    _PrinterQueryWorker,
    _single_shot,
    available_printer_names,
)
from deckle.core.defaults import load_defaults
from deckle.app import menus
from deckle.app.menus import MENUS, build_menu_bar
from deckle.app import exporting, project_actions, shutdown

#: Re-exported for the two button tooltips ``_sync_document_actions`` sets;
#: the functions that say them live in the modules that own the action.
from deckle.app.exporting import NOTHING_TO_EXPORT_MESSAGE  # noqa: F401
from deckle.app.project_actions import NOTHING_TO_SAVE_MESSAGE  # noqa: F401
from deckle.core import about
from deckle.core.diagnostics import (
    diagnostics_log_path,
    data_dir as diagnostics_dir,
    log_event,
    log_exception,
)
from deckle.app.views.arrange_view import ArrangeView
from deckle.app.views.import_view import ImportView, import_advisory_message
from deckle.app.views.layout_panel import LayoutPanel, recompute_plan
from deckle.app.views.preview_view import PreviewView
from deckle.app.views.print_dialog import (
    PrintDialog,
    resolve_profile,
    select_preselected_printer,
)
from deckle.core.locate import locate_page
from deckle.core.models import LayoutSettings, Project
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile

LETTER_PT = (612.0, 792.0)

NO_PRINTERS_MESSAGE = "No printers installed -- connect a printer to enable printing."

NOTHING_TO_PRINT_MESSAGE = "Import a document before printing -- there are no pages."
"""Why Print is unavailable when the printers are fine and the document is
empty.

Print was gated on printers alone. With nothing imported it stayed live,
opened the dialog against a plan of zero sheets, walked both empty passes
and reported *"Print job complete."* for a job that submitted nothing --
which is the app telling the operator that paper came out. Save PDF and
Save project were gated on pages from the start; Print needs both, and
this is the half that was missing.
"""

NO_DOCUMENT_MESSAGE = "Import a PDF or a folder of images to begin."
"""The first thing Deckle says when it opens with nothing loaded.

Previously the only first-run message was :data:`NO_PRINTERS_MESSAGE`, so an
empty Deckle led with a complaint about hardware the user does not need yet.
The first message should name the first step.
"""

UNSAVED_CHANGES_QUESTION = (
    "{name} has changes you have not saved.\n\n"
    "Save them before {action}?"
)
"""Asked before a document is thrown away.

Deckle has autosaved on every edit since the MVP, and autosave is not a
save: it writes `<project>.autosave` so it can never clobber the file the
user named, and it is offered back only as crash recovery. So closing a
window full of an afternoon's reordering has always lost that work from
the user's own file, silently, with the recovery prompt as the only way
back -- and the recovery prompt appears on the *next* open, which is not
where anybody looks for it.
"""

UNTITLED_PROJECT_NAME = "This document"
"""What the prompts call a project that has never been saved."""


SAVE_PROJECT_TOOLTIP = (
    "Save this job as a .deckle project so you can come back to it.\n\n"
    "Saves straight back to the project's own file; a job that has never "
    "been saved asks where to put it.\n\n"
    "Saves the page order, rotations, skips, blanks and every layout "
    "setting -- not the PDF. Use Save PDF for the imposed document."
)

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
    recorded_printer: str | None = None,
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
    through the same two functions and given the same three inputs. A
    preview drawing one printer's border while the print dialog is about to
    preselect another's would be a worse lie than the constant it replaces
    -- which is why ``recorded_printer`` is threaded here as well as into
    the dialog, rather than into the dialog alone.

    Pure and Qt-free, so the choosing is testable without a display.

    :param printer_names: the printers Qt reported, in its order.
    :param profile_loader: how to load a saved profile; raising means the
        printer has none.
    :param fallback: what to answer when no printer is installed. Nothing
        has been chosen, so nothing better than the generic preset is
        available -- and Print is disabled in that state anyway.
    :param recorded_printer: the printer the open project names
        (``Project.printer``), or ``None``.
    :returns: the profile to draw against.
    """
    chosen = select_preselected_printer(
        list(printer_names), profile_loader, recorded_printer
    )
    if chosen is None:
        return fallback
    return resolve_profile(chosen, profile_loader)

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


PROJECT_SUFFIX = ".deckle"

#: What a dropped *source* may be. A folder is accepted whatever it is
#: called; `load_image_dir` is the one that decides whether it holds images,
#: and it already says so in words a person can act on.
DROPPABLE_SOURCE_SUFFIXES = (".pdf",)

DROP_REJECTED_MESSAGE = (
    "Deckle takes one PDF, one folder of images, or one .deckle project at "
    "a time -- drop one of those."
)


@dataclass(frozen=True)
class Drop:
    """What a dropped path turned out to be.

    :ivar kind: ``"project"`` for a ``.deckle`` to open, ``"source"`` for
        something to import.
    :ivar path: the local path.
    """

    kind: Literal["project", "source"]
    path: str


def classify_drop(paths: Sequence[str], is_dir=os.path.isdir) -> Drop | None:
    """What, if anything, a drop of ``paths`` should do.

    Dragging a PDF onto the window is how most people expect to get into an
    imposition tool, and nothing in Deckle set ``acceptDrops`` at all. This
    is the whole of the decision, kept out of the event handler so it can be
    tested without a display or a drag.

    One path, deliberately. Two PDFs dropped together look like one gesture
    and are two imports, which the import view runs on one background thread
    at a time; sequencing them is a queue, and a queue that silently
    reorders someone's book is worse than a refusal that names the rule.

    :param paths: the local paths carried by the drop, in the order Qt
        reported them.
    :param is_dir: how to ask whether a path is a directory. Injected so a
        test can classify paths that do not exist.
    :returns: the drop, or ``None`` when it is not something Deckle takes.
    """
    if len(paths) != 1:
        return None
    path = paths[0]
    if path.lower().endswith(PROJECT_SUFFIX):
        return Drop(kind="project", path=path)
    if is_dir(path):
        return Drop(kind="source", path=path)
    if path.lower().endswith(DROPPABLE_SOURCE_SUFFIXES):
        return Drop(kind="source", path=path)
    return None


def _open_folder(path: str) -> bool:
    """Show ``path`` in the desktop's file manager.

    A patchable seam, like :func:`_new_thread`: a test must be able to
    assert that Help > Open diagnostics folder asked for the right
    directory without a file manager opening on the developer's screen.

    Strictly a ``file://`` URL. Deckle does not contact the network for
    anything, and the one function in the program that could is this one --
    ``QDesktopServices.openUrl`` will happily open ``https://`` too, so the
    URL is built from a local path and never from a string a caller supplied.

    :param path: the directory to show.
    :returns: whether the desktop accepted it.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))


def _new_menu(parent):
    """A ``QMenu``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMenu

    return QMenu(parent)


def _qt_selectable_text():
    """``Qt.TextInteractionFlag.TextSelectableByMouse``, imported lazily."""
    from PySide6.QtCore import Qt

    return Qt.TextInteractionFlag.TextSelectableByMouse


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

        #: ``{action name: QAction}`` for the menu bar, filled in by
        #: :func:`deckle.app.menus.build_menu_bar`. Empty until then, so
        #: the enable/disable helpers below can run at any point in
        #: construction without asking whether the menus exist yet.
        self.menu_actions: dict = {}

        #: The menu description this window was built from. Held as an
        #: attribute rather than reached for as a module global so that
        #: the enable rules below read the *same* description the menu
        #: bar was built from -- they used to name their commands in
        #: hand-written tuples, which is how a rename could disable
        #: nothing and redden nothing.
        self._menus = MENUS

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
        # `[*]` is Qt's placeholder for the modified marker; it is replaced
        # by an asterisk (or the platform's own convention) when
        # `setWindowModified(True)` and by nothing otherwise. The title is
        # rewritten whenever the project changes -- see `_sync_title`.
        self.window.setWindowTitle("Deckle[*]")
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

        # Recent projects lives in the File menu now that there is one.
        # It used to be a button in this column, on the argument that
        # burying the list a click deeper than the dialog it exists to
        # replace would defeat it -- true when the alternative was a
        # submenu of a submenu, and no longer true when File is one
        # keystroke away and holds every other way into a document.
        self._recent_menu = _new_menu(self.window)
        self._recent_menu.setToolTipsVisible(True)

        self.save_project_button = QPushButton("Save project", controls)
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
        # `failed` was emitted and connected to nothing, so the one thing a
        # refused import updated was the import row's own small label --
        # while the status bar went on telling the user to import
        # something. Every other failure in the app reaches the bar.
        self.import_view.failed.connect(self._on_import_failed)
        self.arrange_view.pages_changed.connect(self._on_pages_changed)
        self.arrange_view.page_selected.connect(self._on_page_selected)
        self.layout_panel.layout_changed.connect(self._on_layout_changed)
        self.layout_panel.schedule_saved.connect(self.status_bar.showMessage)
        self.print_button.clicked.connect(self.print_document)
        self.save_pdf_button.clicked.connect(self.save_pdf)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.open_project_button.clicked.connect(self.open_project_dialog)
        self.save_project_button.clicked.connect(self.save_project)

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
        #: What the last import had to say -- why it was refused, or what
        #: it left behind -- or "" when it had nothing to report. Held for
        #: the same reason `_printer_message` is: printer enumeration
        #: finishes on a background thread and ends in a
        #: `_refresh_status_message()`, so a message written straight to
        #: the bar could be wiped a moment later by an unrelated one, with
        #: nothing left to say it ever appeared.
        self._import_message = ""
        #: Asked, before a document is thrown away, what to do with the
        #: unsaved work. Injected the way `confirm_recovery` is, so the
        #: whole close/open flow is drivable without a modal.
        self.confirm_discard_changes = self._default_confirm_discard_changes
        #: Asked whether a dropped source is added to the document or
        #: replaces it. Injected for the same reason.
        self.confirm_drop_append = self._default_confirm_drop_append
        #: Asked which pass a single-pass export should write.
        self.choose_export_pass = self._default_choose_export_pass

        # Dropping a PDF on the window is how most people expect to open
        # one, and nothing in Deckle accepted a drop at all.
        self.window.setAcceptDrops(True)
        self.window.dragEnterEvent = self._on_drag_enter
        self.window.dropEvent = self._on_drop

        self.menu_actions = build_menu_bar(
            self.window,
            self,
            self._menus,
            scope_widgets={"grid": self.arrange_view.list_widget},
        )

        self._refresh_recent_menu()
        self._sync_document_actions()
        self._sync_history_actions()
        self._sync_title()
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
        accepting the click and then explaining itself. Print needs *both*
        a document and a printer, so it is decided by
        :meth:`_sync_print_action`, which this method and
        :meth:`_apply_printers` both call.

        Which entries those are is read off :data:`deckle.app.menus.MENUS`
        -- ``gate="document"`` -- rather than listed here. It was listed
        here, as four string literals, and the list had to agree with the
        menu description by hand while the lookup below quietly tolerated
        a name it could not find. A rename in either direction turned the
        gating into a no-op that nothing detected.
        """
        has_pages = bool(self.state.project.pages)
        self.save_pdf_button.setEnabled(has_pages)
        # The menu entries are the same commands as the buttons, so they
        # have to be unavailable at the same moments -- a greyed button
        # beside a live menu item is the app disagreeing with itself.
        self._set_gated_actions("document", has_pages)
        # Opening is always available; saving needs something to save.
        self.save_project_button.setEnabled(has_pages)
        self.save_project_button.setToolTip(
            SAVE_PROJECT_TOOLTIP if has_pages else NOTHING_TO_SAVE_MESSAGE
        )
        self.save_pdf_button.setToolTip("" if has_pages else NOTHING_TO_EXPORT_MESSAGE)
        self.layout_panel.set_document_loaded(has_pages)
        self._sync_print_action()

    def _set_gated_actions(self, gate: str, enabled: bool) -> None:
        """Enable or disable every menu entry carrying ``gate``.

        :param gate: one of :data:`deckle.app.menus.GATES`. A gate the
            menus do not define raises rather than matching nothing --
            see :func:`deckle.app.menus.actions_gated_by`.
        :param enabled: what to set them to.
        :returns: nothing.

        The ``is not None`` below is for a window whose menu bar has not
        been built: :attr:`menu_actions` is empty for the whole first half
        of ``__init__`` and stays empty on the test doubles that drive
        these methods directly. It is no longer load-bearing for
        *correctness* -- the names come from the same description the menu
        bar was built from, so one cannot be absent because it was
        misspelled.
        """
        for name in menus.actions_gated_by(gate, self._menus):
            action = self.menu_actions.get(name)
            if action is not None:
                action.setEnabled(enabled)

    def _sync_print_action(self) -> None:
        """Print needs a printer **and** a document.

        It was gated on printers alone, by :meth:`_apply_printers`, whose
        docstring is explicit that zero printers deliberately leaves Save
        PDF alone -- *"leave Deckle fully usable as an imposition tool that
        writes a file"*. The converse was never considered: with zero
        pages Print stayed live, opened the dialog against a plan of no
        sheets, and reported a completed job that submitted nothing.

        One method rather than a condition in each caller, because the two
        halves of the answer arrive at different moments -- a document
        after an import, a printer after enumeration -- and either of them
        writing the button on its own is how they came to disagree.

        :returns: nothing. The tooltip names whichever half is missing,
            printers first: a machine with no printer cannot print
            whatever is imported.
        """
        has_printers = bool(self._printers)
        has_pages = bool(self.state.project.pages)
        enabled = has_printers and has_pages
        self.print_button.setEnabled(enabled)
        self._set_gated_actions("document+printer", enabled)
        if enabled:
            self.print_button.setToolTip("")
        elif not has_printers:
            # Whatever `_apply_printers` last explained -- it may be a
            # timeout rather than "none installed", and that distinction
            # is worth more than a generic message.
            self.print_button.setToolTip(
                self._printer_message or NO_PRINTERS_MESSAGE
            )
        else:
            self.print_button.setToolTip(NOTHING_TO_PRINT_MESSAGE)

    def _on_layout_changed(self, plan) -> None:
        # Hand the preview the settings too, so its content-box guide
        # tracks the gutter/margins rather than going stale.
        self.preview_view.on_layout_changed(plan, self.state.project.layout)
        # A margin is as much a change to the job as a reordered page, and
        # closing without saving loses it just as completely.
        self._sync_title()

    def _on_import_failed(self, message: str) -> None:
        """Put a refused import where the user is already looking.

        ``ImportView`` sets its own row label too, but that label is one
        line inside a toolbar and the status bar is where every other
        failure in this app reports: a schedule that could not be saved, a
        project whose source is missing or changed, a drop of something
        Deckle does not take. Import was the exception, and it is the
        first thing anyone does.

        :param message: ``ImportView.failed``'s text, which already names
            the file and what was wrong with it.
        :returns: nothing.
        """
        self._import_message = message
        log_event("import_failed", detail=message)
        self._refresh_status_message()

    def _on_imported(self, pages, warnings) -> None:
        """Take an import's pages, and say what it left behind.

        :param pages: the newly imported pages. Not read here: they are
            already in ``state.project`` by the time this runs, and the
            views below read them from there.
        :param warnings: the loader's advisories. **This is the half that
            was missing.** ``ImportView`` carried them across the signal
            and this slot ignored both arguments, so a folder of scans
            containing four files Deckle will not take imported silently
            and the book came out four pages short. The CLI has always
            printed them.
        :returns: nothing.
        """
        # The failure this replaces is no longer the newest true thing,
        # and an error about a file the user has since replaced is worse
        # than silence. An advisory about the import that just happened
        # takes its place in the same slot, for the same reason
        # `_refresh_status_message` gives: it is the answer to something
        # the user did a second ago.
        self._import_message = import_advisory_message(warnings)
        if self._import_message:
            log_event("import_advisory", detail=self._import_message)
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
        self._sync_title()
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
        self._sync_title()

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

        There are four, in order of precedence: the last thing an import
        said, then a printer fault the user cannot otherwise see, then the
        next step when nothing is loaded, then nothing at all. Silence is
        the right answer for a document that is ready to print -- the
        status bar is not a place to announce that everything is fine.

        "The last thing an import said" is a refusal *or* an advisory --
        an import that succeeded while leaving files behind. Both go in
        one slot because both are answers to something the user did a
        second ago, and a second successful import replaces either.

        The import outranks the printer fault while it lasts. A printer
        fault is a standing condition; what an import just said is the
        only one of the two the user can act on with no document loaded.
        It is cleared by the next import that has nothing to report, at
        which point the printer fault comes back.
        """
        if self._import_message:
            self.status_bar.showMessage(self._import_message)
        elif self._printer_message:
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
        if has_printers:
            self._printer_message = ""
        else:
            message = no_printers_message or NO_PRINTERS_MESSAGE
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
        # After `_printer_message`, which the tooltip reads.
        self._sync_print_action()
        self._refresh_status_message()
        # Enumeration is the first moment the window knows which printer
        # it is drawing for. Until this call existed, it never found out.
        self.set_printer_profile(
            self._profile_with_driver_answer(
                profile_for_printers(
                    self._printers,
                    self.profile_loader,
                    recorded_printer=self.state.project.printer,
                )
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
        name = select_preselected_printer(
            self._printers, self.profile_loader, self.state.project.printer
        )
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

    def print_document(self) -> None:
        # Use the cached list rather than re-enumerating: a second query
        # would re-introduce exactly the block this moved off the UI thread.
        printers = self._printers
        if not printers:
            # Defensive: the button should already be disabled, but never
            # open a print dialog against zero printers even if this
            # slot is reached some other way.
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)
            return
        if not self.state.project.pages:
            # Defensive in the same way, and for a defect that was real
            # rather than hypothetical: the button used not to be disabled
            # here at all, and a plan of zero sheets reached a dialog that
            # ran both empty passes and said "Print job complete."
            self.status_bar.showMessage(NOTHING_TO_PRINT_MESSAGE)
            return
        plan = self.preview_view.plan
        self.print_dialog = PrintDialog(
            plan, self.window, printer_names=printers,
            # `deckle-cli impose --printer` has written this into the
            # `.deckle` since the beginning -- "printer name to record in
            # the project" -- and nothing read it. Opening such a project
            # and pressing Print preselected a different machine, and with
            # it a different calibration.
            recorded_printer=self.state.project.printer,
            profile_loader=self.profile_loader,
        )
        self.print_dialog.widget.exec()
        # The dialog is where the printer and its paper behaviour are
        # actually chosen (B16). Picking a face-up printer there and coming
        # back to a preview still drawing the first preset's border would
        # put the two halves of the same answer on screen at once.
        self.set_printer_profile(self.print_dialog.selected_profile())
        self.status_bar.showMessage(f"{len(printers)} printer(s) available.")

    # -- menu commands -------------------------------------------------
    # Every one of these is named by `deckle.app.menus.MENUS`. They are
    # thin on purpose: the menu is a second way to reach the commands the
    # buttons already reach, never a second implementation of them.

    def import_pdf(self) -> None:
        """Ask for a PDF and import it.

        :returns: nothing.
        """
        self.import_view.pick_pdf()

    def import_images(self) -> None:
        """Ask for a folder of images and import it.

        :returns: nothing.
        """
        self.import_view.pick_images()

    def zoom_in(self) -> None:
        """Step the preview one zoom stop closer."""
        self.preview_view.zoom_in()

    def zoom_out(self) -> None:
        """Step the preview one zoom stop further away."""
        self.preview_view.zoom_out()

    def zoom_actual(self) -> None:
        """Show the sheet at the size it will print."""
        self.preview_view.zoom_actual()

    def zoom_fit(self) -> None:
        """Fit the whole sheet in the window."""
        self.preview_view.zoom_fit()

    def next_sheet(self) -> None:
        """Show the next sheet."""
        self.preview_view.next_sheet()

    def previous_sheet(self) -> None:
        """Show the previous sheet."""
        self.preview_view.previous_sheet()

    def first_sheet(self) -> None:
        """Show the first sheet."""
        self.preview_view.go_to_first_sheet()

    def last_sheet(self) -> None:
        """Show the last sheet."""
        self.preview_view.go_to_last_sheet()

    def rotate_selection(self) -> None:
        """Turn the pages selected in the grid by 90 degrees."""
        self.arrange_view.rotate_selection()

    def skip_selection(self) -> None:
        """Skip, or unskip, the pages selected in the grid.

        :returns: nothing.

        Bound to Delete as well as S. Deckle has no destructive page
        removal -- a skipped page keeps its slot in the document, which is
        what lets it come back -- so skipping is what "leave this one out"
        means here, and it is what the Delete key should reach.
        """
        self.arrange_view.skip_selection()

    def insert_blank(self) -> None:
        """Ask where a blank page goes, and put one there."""
        self.arrange_view.insert_blank_at_choice()

    def quit(self) -> None:
        """Close the window, prompting for unsaved work on the way out."""
        self.window.close()

    def show_about(self) -> None:
        """Say what this build is, and where its diagnostic log lives.

        The GUIDE tells anyone reporting a problem to send the output of
        ``deckle --version`` and the JSON Lines log. Until now the app
        offered neither, so a desktop user following those instructions
        had to be talked through their operating system's data directory
        by hand.

        No network is touched, and the box says so: there is no update
        check to make and no version to compare against.

        :returns: nothing.
        """
        QMessageBox = _qt_message_box()
        box = QMessageBox(self.window)
        box.setWindowTitle("About Deckle")
        box.setText(about.about_text(str(diagnostics_log_path())))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        # The log path is the one thing in here somebody has to act on,
        # and reading a path off a screen and retyping it is how support
        # requests end up naming a file that does not exist.
        box.setTextInteractionFlags(_qt_selectable_text())
        box.exec()

    def open_diagnostics_folder(self) -> bool:
        """Show the folder holding the diagnostic log.

        Created if it is not there yet: a user asking for the folder
        before anything has been logged should be shown an empty folder,
        not told there isn't one.

        :returns: whether the desktop opened it. A refusal is reported in
            the status bar along with the path, which is the answer the
            user actually needed.
        """
        folder = diagnostics_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log_exception("diagnostics_folder_create_failed", exc, path=str(folder))
        opened = _open_folder(str(folder))
        if opened:
            self.status_bar.showMessage(f"Diagnostics folder: {folder}")
        else:
            self.status_bar.showMessage(
                f"Could not open the diagnostics folder -- it is at {folder}"
            )
            log_event("diagnostics_folder_open_failed", path=str(folder))
        return opened

    # -- unsaved work ----------------------------------------------------

    def _sync_title(self) -> None:
        """Put the project's name, and whether it is modified, in the title.

        Qt's own mechanism: the title carries a ``[*]`` placeholder and
        ``setWindowModified`` decides whether it renders as an asterisk.
        That is what makes the marker look native -- on macOS it is a dot
        in the close button, not an asterisk at all -- and it is why this
        is not a string Deckle assembles itself.

        :returns: nothing.
        """
        path = self.state.project_path
        name = os.path.basename(path) if path else "Untitled"
        self.window.setWindowTitle(f"{name}[*] -- Deckle")
        self.window.setWindowModified(self.state.dirty)

    def has_unsaved_changes(self) -> bool:
        """Whether there is work worth stopping to ask about.

        An empty document is never worth a prompt: there is nothing in it
        to lose, and a prompt on the way out of a window the user never
        put anything into is how people learn to dismiss prompts without
        reading them.

        :returns: whether to ask before discarding.
        """
        return self.state.dirty and bool(self.state.project.pages)

    def _confirm_discard(self, action: str) -> bool:
        """Ask about unsaved work, and act on the answer.

        :param action: what is about to happen, as a phrase that completes
            "Save them before ...?".
        :returns: whether to go ahead. See
            :func:`deckle.app.project_actions.confirm_discard`.
        """
        return project_actions.confirm_discard(self, action)

    def _default_confirm_discard_changes(self, name: str, action: str) -> str:
        """Ask whether to save, discard or stay. Replaceable for tests.

        :param name: the project's file name, or
            :data:`UNTITLED_PROJECT_NAME`.
        :param action: the phrase completing the question.
        :returns: ``"save"``, ``"discard"`` or ``"cancel"``.
        """
        QMessageBox = _qt_message_box()
        box = QMessageBox(self.window)
        box.setWindowTitle("Unsaved changes")
        box.setText(UNSAVED_CHANGES_QUESTION.format(name=name, action=action))
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        # Cancel is the default so that a reflexive Return keeps the work.
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return "save"
        if answer == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    # -- drag and drop ---------------------------------------------------

    def _dropped_paths(self, event) -> list[str]:
        """The local paths a drag event carries.

        :param event: the ``QDragEnterEvent`` or ``QDropEvent``.
        :returns: the local file paths, ignoring any URL that does not name
            one -- a link dragged out of a browser is not a document.
        """
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]

    def _on_drag_enter(self, event) -> None:
        """Accept the drag only if the drop would do something.

        :param event: the ``QDragEnterEvent``.
        :returns: nothing. Refusing here is what makes the cursor say no
            over a file Deckle cannot take, instead of accepting the drop
            and then explaining.
        """
        if classify_drop(self._dropped_paths(event)) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _on_drop(self, event) -> None:
        """Open or import what was dropped on the window.

        :param event: the ``QDropEvent``.
        :returns: nothing. Anything Deckle does not take is refused with a
            message naming what it does take.
        """
        drop = classify_drop(self._dropped_paths(event))
        if drop is None:
            self.status_bar.showMessage(DROP_REJECTED_MESSAGE)
            event.ignore()
            return
        event.acceptProposedAction()
        log_event("dropped", kind=drop.kind, path=drop.path)
        if drop.kind == "project":
            self.open_project(drop.path)
            return
        self.import_dropped_source(drop.path)

    def import_dropped_source(self, path: str) -> None:
        """Import a dropped PDF or image folder, asking what to do with it.

        A drop onto an empty Deckle can only mean one thing, so it does
        not ask. A drop onto a document that already has pages is genuinely
        ambiguous -- the import bar offers "Add to the current document"
        precisely because both answers are normal for the books this
        program is for -- and picking one silently would either throw away
        the user's document or bury a replacement at the end of it.

        :param path: the dropped source.
        :returns: nothing. Cancelling imports nothing at all.
        """
        if not self.state.project.pages:
            self.import_view.import_path(path, append=False)
            return
        append = self.confirm_drop_append(os.path.basename(path))
        if append is None:
            return
        self.import_view.import_path(path, append=append)

    def _default_confirm_drop_append(self, name: str) -> bool | None:
        """Ask whether a dropped source adds to the document or replaces it.

        :param name: the dropped file's name.
        :returns: ``True`` to add, ``False`` to replace, ``None`` to
            cancel.
        """
        QMessageBox = _qt_message_box()
        box = QMessageBox(self.window)
        box.setWindowTitle("Add or replace?")
        box.setText(
            f"{name}\n\nAdd these pages to the document you already have, "
            "or replace it?"
        )
        add = box.addButton("Add", QMessageBox.ButtonRole.AcceptRole)
        replace_button = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        # Adding is the safe answer, so it is the one a reflexive Return
        # takes: replacing throws a document away.
        box.setDefaultButton(add)
        box.exec()
        clicked = box.clickedButton()
        if clicked is add:
            return True
        if clicked is replace_button:
            return False
        return None

    def suggested_export_name(self) -> str:
        """A default filename derived from the first imported source.

        :returns: the suggested filename. See
            :func:`deckle.app.exporting.suggested_export_name`.
        """
        return exporting.suggested_export_name(self.state.project.pages)

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
        # Same two names, same reason: read off the menu description
        # rather than repeated here. See :meth:`_set_gated_actions`.
        self._set_gated_actions("undo", self.state.can_undo)
        self._set_gated_actions("redo", self.state.can_redo)

    def _recover_autosave_if_offered(self, path: str, project: Project) -> Project:
        """Offer a newer autosave in place of the project just loaded.

        :param path: the project file just opened.
        :param project: what was loaded from it.
        :returns: the recovered project, or ``project`` unchanged. See
            :func:`deckle.app.project_actions.recover_autosave_if_offered`.
        """
        return project_actions.recover_autosave_if_offered(self, path, project)

    def _offer_unsaved_recovery(self) -> None:
        """Offer back work from a session that never got as far as Save.

        :returns: nothing, and never raises. See
            :func:`deckle.app.project_actions.offer_unsaved_recovery`.
        """
        project_actions.offer_unsaved_recovery(self)

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

        :returns: nothing, and never raises. See
            :func:`deckle.app.project_actions.refresh_recent_menu`.
        """
        project_actions.refresh_recent_menu(self)

    def open_project_dialog(self) -> None:
        """Open a saved project, replacing whatever is loaded.

        :returns: nothing. See
            :func:`deckle.app.project_actions.choose_and_open_project`.
        """
        project_actions.choose_and_open_project(self)

    def open_project_with_prompt(self, path: str) -> bool:
        """Open ``path``, offering to save the document it replaces.

        :param path: the ``.deckle`` to open.
        :returns: whether it opened.
        """
        return project_actions.open_project_with_prompt(self, path)

    def open_project(self, path: str) -> bool:
        """Load ``path`` into the window.

        :param path: the ``.deckle`` to open.
        :returns: whether it opened. See
            :func:`deckle.app.project_actions.open_project`.
        """
        return project_actions.open_project(self, path)

    def _flush_outgoing_state(self) -> None:
        """Write and disarm the ``AppState`` that is about to be replaced.

        :returns: nothing, and never raises. See
            :func:`deckle.app.project_actions.flush_outgoing_state`.
        """
        project_actions.flush_outgoing_state(self)

    def save_project(self) -> bool:
        """Save the job back to the file it came from.

        :returns: whether the project was written. See
            :func:`deckle.app.project_actions.save_project`.
        """
        return project_actions.save_project(self)

    def save_project_as(self) -> bool:
        """Ask where to save the job, and save it there.

        :returns: whether the project was written; ``False`` for a
            cancelled dialog.
        """
        return project_actions.save_project_as(self)

    def save_pdf(self) -> None:
        """Write the imposed document the preview is showing.

        :returns: nothing. See :func:`deckle.app.exporting.save_pdf_as`.
        """
        exporting.save_pdf_as(self)

    def export_single_pass(self) -> None:
        """Write one pass -- fronts or backs -- as its own PDF.

        :returns: nothing. See
            :func:`deckle.app.exporting.export_single_pass`.
        """
        exporting.export_single_pass(self)

    def _default_choose_export_pass(self) -> str | None:
        """Ask which pass to write. Replaceable for tests.

        :returns: ``"front"``, ``"back"``, or ``None`` if cancelled.
        """
        from PySide6.QtWidgets import QInputDialog

        choices = [
            "Fronts (pass 1 -- print this first)",
            "Backs (pass 2 -- print after reloading the paper)",
        ]
        label, ok = QInputDialog.getItem(
            self.window,
            "Save one pass",
            "Which pass?",
            choices,
            0,
            False,
        )
        if not ok:
            return None
        return "front" if label == choices[0] else "back"

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

        :param event: the ``QCloseEvent``. Accepted unless the user
            cancels out of the unsaved-changes prompt -- the one reason
            worth refusing a close, since the alternative is losing the
            work the prompt exists to protect. A running render is never
            a reason: refusing for that would trap the user.
        :returns: nothing.
        """
        if not self._confirm_discard("closing"):
            event.ignore()
            return
        self.state.flush_autosave()
        self.stop_background_work()
        event.accept()

    def stop_background_work(self, timeout_ms: int = 5000) -> None:
        """Cancel in-flight renders and wait for their threads to finish.

        The three views that rasterise off-thread, named here rather than
        inside :func:`deckle.app.shutdown.stop_background_work`, because
        which views a window has is the window's own business.
        ``layout_panel`` joined the list when the Crop & trim tab started
        compositing the document off-thread (N11).

        :param timeout_ms: how long to wait per thread.
        :returns: nothing. Never raises -- see
            :func:`deckle.app.shutdown.stop_background_work`.
        """
        shutdown.stop_background_work(
            (self.preview_view, self.arrange_view, self.layout_panel),
            timeout_ms,
        )


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
