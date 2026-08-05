"""ArrangeView: the thumbnail grid -- drag reorder, rotate, skip, insert-blank.

Reordering, rotating, skipping, and inserting a blank all mutate the
in-memory ``list[SourcePage]`` on ``AppState.project`` -- **never** a
``pikepdf.Pdf.pages`` list. Slice-assigning a ``pikepdf`` page list
corrupts the underlying document (pikepdf's pages proxy is stateful and a
naive Python-list-style reorder desyncs it from the PDF object graph).
Deckle sidesteps that trap entirely: ``SourcePage`` is a plain frozen
dataclass, so every operation below is ordinary list surgery on plain
Python values, then routed through ``AppState.mutate`` so it snapshots
for undo and triggers the debounced autosave. The corresponding
``pikepdf.Pdf`` page tree is only ever touched later, at export time
(``deckle/core/export.py``), by walking this same page list.

Thumbnails are requested for the visible range only, via
``deckle.core.render.thumbnails(pages, start, count)`` -- see the module
docstring in ``deckle/core/render.py``. That call (and any
``render_sheet`` preview) always runs on a background ``QThread``,
matching ``ImportWorker`` in ``import_view.py``: the pure range-math and
worker classes below are Qt-free so headless tests can exercise them
without a display.
"""

from __future__ import annotations

import threading
from typing import Sequence

from deckle.core.diagnostics import log_exception
from deckle.core.models import is_blank_page
from deckle.app.state import (
    AppState,
    insert_blank,
    reorder_pages,
    set_rotation,
    toggle_skip,
)
from deckle.core.models import SourcePage
from deckle.core.render import RenderedPage, thumbnails

# How many thumbnails to request around the visible window on either
# side, so a small scroll doesn't immediately trigger another fetch.
THUMBNAIL_PREFETCH = 10


def visible_range(scroll_index: int, viewport_count: int, total: int) -> tuple[int, int]:
    """The ``(start, count)`` window of pages to fetch thumbnails for.

    Deliberately bounded to the visible viewport (plus a small prefetch
    margin) rather than "all pages" -- scrubbing a thousand-page document
    must not rasterize the whole thing up front.

    :param scroll_index: the first page currently in view.
    :param viewport_count: how many pages the viewport shows.
    :param total: the document's page count.
    :returns: ``(start, count)``, clamped to the document and widened by
        :data:`THUMBNAIL_PREFETCH` on each side so a small scroll does not
        immediately trigger another fetch. ``(0, 0)`` for an empty
        document or a zero-height viewport.
    """
    if total <= 0 or viewport_count <= 0:
        return (0, 0)
    start = max(0, scroll_index - THUMBNAIL_PREFETCH)
    end = min(total, scroll_index + viewport_count + THUMBNAIL_PREFETCH)
    return (start, max(0, end - start))


def request_visible_thumbnails(
    pages: Sequence[SourcePage], scroll_index: int, viewport_count: int, dpi: int = 36
) -> tuple[int, list[RenderedPage]]:
    """Rasterize only the pages currently (or about to be) visible.

    Returns ``(start, rendered_pages)`` so a caller can place results at
    the right offset. Never called with the full page list -- see
    ``visible_range``.

    :param pages: the full page list. Only the visible window is
        rasterized.
    :param scroll_index: the first page currently in view.
    :param viewport_count: how many pages the viewport shows.
    :param dpi: thumbnail resolution.
    :returns: ``(start, rendered_pages)`` -- the index the first result
        belongs at, and the results.
    :raises pypdfium2.PdfiumError: a source page cannot be rasterized.
    """
    start, count = visible_range(scroll_index, viewport_count, len(pages))
    return start, thumbnails(pages, start, count, dpi=dpi)


# -- mutation helpers, each routed through AppState.mutate -------------


def reorder(state: AppState, old_index: int, new_index: int) -> None:
    """Move a page, through ``AppState.mutate``.

    :param state: the app state to mutate.
    :param old_index: where the page is now.
    :param new_index: where it should end up.
    :returns: nothing; read the result back from ``state.project``.
    :raises IndexError: ``old_index`` is out of range.
    """
    state.mutate(lambda project: reorder_pages(project, old_index, new_index))


def rotate(state: AppState, index: int, rotate_deg: int) -> None:
    """Set a page's rotation, through ``AppState.mutate``.

    :param state: the app state to mutate.
    :param index: which page.
    :param rotate_deg: the new rotation; normalised downstream, so a caller
        can keep adding 90 without wrapping it.
    :returns: nothing.
    :raises IndexError: ``index`` is out of range.
    """
    state.mutate(lambda project: set_rotation(project, index, rotate_deg))


def skip(state: AppState, index: int) -> None:
    """Toggle a page's skipped flag, through ``AppState.mutate``.

    :param state: the app state to mutate.
    :param index: which page.
    :returns: nothing.
    :raises IndexError: ``index`` is out of range.
    """
    state.mutate(lambda project: toggle_skip(project, index))


def blank_insert_choices(page_count: int) -> list[tuple[str, int]]:
    """Every place a blank can go, as ``(label, index)`` in reading order.

    :param page_count: how many pages the document currently has.
    :returns: labels a person can pick from, paired with the insert index.

    Positions are described by the pages they fall *between*, because that
    is how someone looking at a stack thinks about it -- "after the title
    page", not "at index 1". The ends are named rather than numbered for
    the same reason.

    An empty document still offers one position, so the control never
    presents an empty list.
    """
    if page_count <= 0:
        return [("As the only page", 0)]
    choices = [("Before page 1", 0)]
    for index in range(1, page_count):
        choices.append((f"Between pages {index} and {index + 1}", index))
    choices.append((f"After page {page_count} (at the end)", page_count))
    return choices


def insert_blank_page(state: AppState, index: int) -> None:
    """Insert a blank page, through ``AppState.mutate``.

    :param state: the app state to mutate.
    :param index: where the blank goes.
    :returns: nothing.
    """
    state.mutate(lambda project: insert_blank(project, index))


class ThumbnailWorker:
    """Runs ``request_visible_thumbnails`` on a background ``QThread``.

    Plain class, not a ``QObject`` -- Qt wiring lives entirely in
    ``ArrangeView``, mirroring ``ImportWorker`` in ``import_view.py``.

    :param pages: the full page list.
    :param scroll_index: the first page currently in view.
    :param viewport_count: how many pages the viewport shows.
    :ivar start: the index :attr:`rendered` begins at.
    :ivar rendered: the thumbnails, once :meth:`run` has finished.
    :ivar cancel: set when a newer scroll position supersedes this fetch.
    """

    def __init__(self, pages: Sequence[SourcePage], scroll_index: int, viewport_count: int) -> None:
        self.pages = pages
        self.scroll_index = scroll_index
        self.viewport_count = viewport_count
        self.start = 0
        self.rendered: list[RenderedPage] = []
        #: Set when the fetch raised. Distinct from an empty ``rendered``,
        #: which is also what a cancelled or genuinely blank window leaves.
        self.failed = False
        # Set when a newer scroll position supersedes this fetch.
        self.cancel = threading.Event()

    def run(self) -> None:
        """Fetch the thumbnails, unless already superseded.

        :returns: nothing -- results land on :attr:`start` and
            :attr:`rendered`. A cancelled fetch leaves both untouched, so a
            superseded window's partial work never reaches the grid.
        """
        if self.cancel.is_set():
            return
        try:
            start, rendered = request_visible_thumbnails(
                self.pages, self.scroll_index, self.viewport_count
            )
        except Exception as exc:  # noqa: BLE001 -- a thread, not the UI
            # An unreadable source must not raise inside a QThread. Qt has
            # nowhere to deliver the exception, so it prints a traceback the
            # user cannot act on and the grid simply stays empty -- the same
            # picture as thumbnails that are merely slow.
            #
            # The commonest cause is mundane: a project reopened after its
            # source PDF was moved or renamed. Degrade to placeholders,
            # which is what the grid already shows before a fetch returns,
            # and record why.
            log_exception(
                "thumbnail_render_failed",
                exc,
                start=self.scroll_index,
                count=self.viewport_count,
            )
            self.failed = True
            return
        if self.cancel.is_set():
            return
        self.start, self.rendered = start, rendered


# -- Qt wiring -----------------------------------------------------------


#: Rendered thumbnails are painted at this size, in pixels. Qt's default
#: icon size is around 16px, which reads as a bullet rather than a page.
THUMBNAIL_ICON_PX = 96


def _qt_size(width: int, height: int):
    """A ``QSize``, imported lazily like every other Qt name here."""
    from PySide6.QtCore import QSize

    return QSize(width, height)


def _icon_from_rendered(rendered: RenderedPage):
    """A ``QIcon`` from a rendered page's raw RGBA bytes.

    :param rendered: the rasterized page.
    :returns: an icon owning its own copy of the pixels.

    ``QImage`` does not copy the buffer it is handed, so the ``.copy()``
    matters: ``rendered.rgba`` is a Python ``bytes`` owned by a worker
    thread's result, and painting from freed memory is the kind of bug that
    shows as intermittent garbage rather than a crash.
    """
    from PySide6.QtGui import QIcon, QImage, QPixmap

    image = QImage(
        rendered.rgba,
        rendered.width,
        rendered.height,
        rendered.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    return QIcon(QPixmap.fromImage(image))


def _qt_core():
    from PySide6.QtCore import QObject, QThread, Signal

    return QObject, QThread, Signal


def _qt_widgets():
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QHBoxLayout,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    return (
        QAbstractItemView,
        QHBoxLayout,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )


class ArrangeView:
    """The thumbnail grid widget.

    A ``QListWidget`` in icon-grid mode with internal drag/drop for
    reorder, plus toolbar buttons for rotate / skip / insert-blank on the
    current selection -- every one of those actions is routed through
    ``AppState.mutate`` via the plain functions above.

    :param state: the app state whose pages are shown and mutated.
    :param parent: the parent ``QWidget``, or ``None``.
    :ivar widget: the ``QWidget`` to place in a layout. This class is not
        itself a widget.
    """

    VIEWPORT_COUNT = 40

    def __init__(self, state: AppState, parent=None, *, choose_blank_position=None) -> None:
        """
        :param state: the app state to arrange.
        :param parent: Qt parent widget.
        :param choose_blank_position: called with the list from
            :func:`blank_insert_choices` and returning the chosen index, or
            ``None`` to cancel. Injectable so the flow can be driven
            headlessly -- the default opens a modal, which a test cannot.
        """
        QObject, QThread, Signal = _qt_core()

        class _Signals(QObject):
            # Reorder, rotate, skip and insert-blank all change the
            # DOCUMENT, which changes the sheets. Nothing announced that, so
            # the preview kept showing the plan from before the edit -- and
            # Save PDF exports the preview's plan, so an inserted blank
            # reached neither the screen nor the paper.
            pages_changed = Signal()

        self._signals = _Signals()
        self.pages_changed = self._signals.pages_changed
        (
            QAbstractItemView,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QVBoxLayout,
            QWidget,
        ) = _qt_widgets()

        self.state = state
        self._choose_blank_position = (
            choose_blank_position or self._default_choose_blank_position
        )
        self.widget = QWidget(parent)
        outer = QVBoxLayout(self.widget)

        self.list_widget = QListWidget(self.widget)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        # Without an explicit icon size Qt paints thumbnails at a default
        # ~16px, which reads as a decorative bullet rather than a page.
        self.list_widget.setIconSize(_qt_size(THUMBNAIL_ICON_PX, THUMBNAIL_ICON_PX))
        self.list_widget.setGridSize(
            _qt_size(THUMBNAIL_ICON_PX + 24, THUMBNAIL_ICON_PX + 40)
        )
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setMovement(QListWidget.Movement.Snap)
        outer.addWidget(self.list_widget)

        toolbar = QHBoxLayout()
        self.rotate_button = QPushButton("Rotate", self.widget)
        self.skip_button = QPushButton("Skip", self.widget)
        self.insert_blank_button = QPushButton("Insert Blank", self.widget)
        toolbar.addWidget(self.rotate_button)
        toolbar.addWidget(self.skip_button)
        toolbar.addWidget(self.insert_blank_button)
        outer.addLayout(toolbar)

        self.rotate_button.clicked.connect(self._on_rotate_clicked)
        self.skip_button.clicked.connect(self._on_skip_clicked)
        self.insert_blank_button.clicked.connect(self._on_insert_blank_clicked)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.list_widget.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        self._QThread = QThread
        self._thread = None
        self._worker: ThumbnailWorker | None = None

        self.refresh()

    # -- population ------------------------------------------------------

    def refresh(self) -> None:
        """Repopulate placeholder items for the current project's pages.

        :returns: nothing. Signals are blocked while items are rebuilt,
            otherwise clearing the list would fire ``rowsMoved`` and
            reorder the project underneath itself.
        """
        QListWidgetItem = _qt_widgets()[3]
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            for i, page in enumerate(self.state.project.pages):
                label = f"page {i + 1}"
                if is_blank_page(page):
                    # Named, not just empty-looking: a blank thumbnail and
                    # a missing thumbnail look identical while one is still
                    # rendering, and only one of them is deliberate.
                    label += " _blank"
                if page.skipped:
                    label += " (skipped)"
                self.list_widget.addItem(QListWidgetItem(label))
        finally:
            self.list_widget.blockSignals(False)
        self.request_visible_thumbnails(0)

    def request_visible_thumbnails(self, scroll_index: int) -> None:
        """Kick off a background thumbnail fetch for the visible window.

        Supersedes any fetch still in flight -- scrolling a 266-page grid
        otherwise queues a thread per scroll step, each parented to the
        widget and so never freed, with the slowest painting last.

        :param scroll_index: the first page now in view.
        :returns: nothing, immediately; results are applied when the
            background thread finishes, and only if it is still the
            current one.
        """
        if self._worker is not None:
            self._worker.cancel.set()

        pages = self.state.project.pages
        worker = ThumbnailWorker(pages, scroll_index, self.VIEWPORT_COUNT)
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_thumbnails_ready(worker))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thumbnails_ready(self, worker: ThumbnailWorker) -> None:
        # Ignore a superseded fetch: stale thumbnails must not land on top
        # of the window the user actually scrolled to.
        if worker is not self._worker or worker.cancel.is_set():
            return
        for offset, rendered in enumerate(worker.rendered):
            index = worker.start + offset
            if index < self.list_widget.count() and rendered.width and rendered.height:
                item = self.list_widget.item(index)
                if item is not None:
                    # Keep the raw buffer for anything that wants the pixels,
                    # and ALSO put it on screen. Storing it in UserRole and
                    # stopping there is what the grid did for months: every
                    # thumbnail rendered correctly and went nowhere, so the
                    # page list showed nothing but text labels.
                    item.setData(0x0100, rendered)  # Qt.ItemDataRole.UserRole
                    item.setIcon(_icon_from_rendered(rendered))

    # -- selection / actions ---------------------------------------------

    def _selected_index(self) -> int | None:
        row = self.list_widget.currentRow()
        return row if row >= 0 else None

    def _on_rotate_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        current = self.state.project.pages[index].rotate_deg
        rotate(self.state, index, current + 90)
        self.refresh()
        self.pages_changed.emit()

    def _on_skip_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        skip(self.state, index)
        self.refresh()
        self.pages_changed.emit()

    def _default_choose_blank_position(self, choices):
        """Ask where the blank goes, defaulting to the current selection.

        :param choices: ``(label, index)`` pairs from
            :func:`blank_insert_choices`.
        :returns: the chosen insert index, or ``None`` if cancelled.
        """
        from PySide6.QtWidgets import QInputDialog

        selected = self._selected_index()
        default_index = selected if selected is not None else len(choices) - 1
        default_row = next(
            (row for row, (_label, index) in enumerate(choices) if index == default_index),
            len(choices) - 1,
        )
        label, ok = QInputDialog.getItem(
            self.widget,
            "Insert blank page",
            "Where should the blank go?",
            [text for text, _index in choices],
            default_row,
            False,
        )
        if not ok:
            return None
        return next(index for text, index in choices if text == label)

    def _on_insert_blank_clicked(self) -> None:
        """Ask where the blank belongs, then insert it there.

        :returns: nothing. Cancelling inserts nothing at all -- an
            accidental blank in a 266-page document is tedious to find.
        """
        choices = blank_insert_choices(len(self.state.project.pages))
        target = self._choose_blank_position(choices)
        if target is None:
            return
        insert_blank_page(self.state, target)
        self.refresh()
        self.pages_changed.emit()

    def _on_rows_moved(self, parent, start, end, destination, row) -> None:
        """Apply a drag-reorder to the project, then say the pages changed.

        :returns: nothing.

        Qt has already moved the row in its own model by the time this
        runs, so the list does not need rebuilding -- but the labels carry
        page numbers that are now wrong, and the sheets have changed
        underneath the preview.
        """
        new_index = row if row < start else row - 1
        reorder(self.state, start, new_index)
        self.refresh()
        self.pages_changed.emit()

    def _on_scrolled(self, value: int) -> None:
        self.request_visible_thumbnails(max(0, value))
