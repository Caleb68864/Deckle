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

from typing import Sequence

from deckle.app.state import AppState, insert_blank, reorder_pages, set_rotation, toggle_skip
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
    """
    start, count = visible_range(scroll_index, viewport_count, len(pages))
    return start, thumbnails(pages, start, count, dpi=dpi)


# -- mutation helpers, each routed through AppState.mutate -------------


def reorder(state: AppState, old_index: int, new_index: int) -> None:
    state.mutate(lambda project: reorder_pages(project, old_index, new_index))


def rotate(state: AppState, index: int, rotate_deg: int) -> None:
    state.mutate(lambda project: set_rotation(project, index, rotate_deg))


def skip(state: AppState, index: int) -> None:
    state.mutate(lambda project: toggle_skip(project, index))


def insert_blank_page(state: AppState, index: int) -> None:
    state.mutate(lambda project: insert_blank(project, index))


class ThumbnailWorker:
    """Runs ``request_visible_thumbnails`` on a background ``QThread``.

    Plain class, not a ``QObject`` -- Qt wiring lives entirely in
    ``ArrangeView``, mirroring ``ImportWorker`` in ``import_view.py``.
    """

    def __init__(self, pages: Sequence[SourcePage], scroll_index: int, viewport_count: int) -> None:
        self.pages = pages
        self.scroll_index = scroll_index
        self.viewport_count = viewport_count
        self.start = 0
        self.rendered: list[RenderedPage] = []

    def run(self) -> None:
        self.start, self.rendered = request_visible_thumbnails(
            self.pages, self.scroll_index, self.viewport_count
        )


# -- Qt wiring -----------------------------------------------------------


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
    """

    VIEWPORT_COUNT = 40

    def __init__(self, state: AppState, parent=None) -> None:
        QObject, QThread, Signal = _qt_core()
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
        self.widget = QWidget(parent)
        outer = QVBoxLayout(self.widget)

        self.list_widget = QListWidget(self.widget)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
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
        """Repopulate placeholder items for the current project's pages."""
        QListWidgetItem = _qt_widgets()[3]
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            for i, page in enumerate(self.state.project.pages):
                label = f"page {i + 1}" + (" (skipped)" if page.skipped else "")
                self.list_widget.addItem(QListWidgetItem(label))
        finally:
            self.list_widget.blockSignals(False)
        self.request_visible_thumbnails(0)

    def request_visible_thumbnails(self, scroll_index: int) -> None:
        """Kick off a background thumbnail fetch for the visible window."""
        pages = self.state.project.pages
        worker = ThumbnailWorker(pages, scroll_index, self.VIEWPORT_COUNT)
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_thumbnails_ready(worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thumbnails_ready(self, worker: ThumbnailWorker) -> None:
        for offset, rendered in enumerate(worker.rendered):
            index = worker.start + offset
            if index < self.list_widget.count() and rendered.width and rendered.height:
                item = self.list_widget.item(index)
                if item is not None:
                    item.setData(0x0100, rendered)  # Qt.ItemDataRole.UserRole

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

    def _on_skip_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        skip(self.state, index)
        self.refresh()

    def _on_insert_blank_clicked(self) -> None:
        index = self._selected_index()
        target = index if index is not None else len(self.state.project.pages)
        insert_blank_page(self.state, target)
        self.refresh()

    def _on_rows_moved(self, parent, start, end, destination, row) -> None:
        new_index = row if row < start else row - 1
        reorder(self.state, start, new_index)

    def _on_scrolled(self, value: int) -> None:
        self.request_visible_thumbnails(max(0, value))
