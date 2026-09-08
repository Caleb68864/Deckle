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
    reorder_pages_to,
    set_rotation,
    toggle_skip,
)
from deckle.core.models import Project, SourcePage
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
    :returns: ``(start, count)``, widened by :data:`THUMBNAIL_PREFETCH` on
        each side so a small scroll does not immediately trigger another
        fetch. ``(0, 0)`` for an empty document or a zero-height viewport.

        ``count`` is clamped to the document; ``start`` is not, so a
        ``scroll_index`` past the end returns a start beyond the last page
        with a count of ``0``. That is inert -- the caller places an empty
        result at an offset nothing reads -- but the previous wording said
        both were clamped, and only one is. Reachable transiently when
        pages are deleted while the view is scrolled to the end.
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


def reorder_to(state: AppState, order: list[int]) -> None:
    """Rearrange the pages into ``order``, through ``AppState.mutate``.

    One mutation, so a drag is one step of undo rather than none or many.

    :param state: the app state to mutate.
    :param order: every existing page index, exactly once, in their new
        order.
    :returns: nothing; read the result back from ``state.project``.
    :raises ValueError: ``order`` is not a permutation of the page indices.
    """
    state.mutate(lambda project: reorder_pages_to(project, order))


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


def rotate_many(state: AppState, indices: Sequence[int], delta_deg: int = 90) -> None:
    """Turn every page in ``indices`` by ``delta_deg``, as one change.

    One ``mutate`` for the whole gesture, not one per page. Calling
    :func:`rotate` in a loop would be simpler to write and wrong to use:
    selecting forty scanned pages and turning them would bury forty
    entries in a stack that only holds :data:`DEFAULT_UNDO_DEPTH`, so the
    single Ctrl+Z the user expects would undo one page and the rest of
    their history would be gone.

    Each page turns from its own current angle, so a selection that is not
    all facing the same way stays that way, only rotated.

    :param state: the app state to mutate.
    :param indices: which pages, in any order.
    :param delta_deg: how far to turn each; normalised downstream.
    :returns: nothing.
    :raises IndexError: any index is out of range.
    """
    def apply(project: Project) -> Project:
        for index in indices:
            project = set_rotation(
                project, index, project.pages[index].rotate_deg + delta_deg
            )
        return project

    state.mutate(apply)


def skip_many(state: AppState, indices: Sequence[int]) -> None:
    """Skip every page in ``indices``, or unskip them if all are skipped.

    Toggling each page independently is the obvious reading and the wrong
    one: on a mixed selection it inverts the mixture rather than resolving
    it, so the user asks "skip these" and gets back the same number of
    skipped pages in different places. Deciding once for the whole
    selection -- skip unless everything is already skipped -- is what makes
    the gesture mean what it looks like. For a single page it is still an
    ordinary toggle.

    :param state: the app state to mutate.
    :param indices: which pages, in any order.
    :returns: nothing. An empty selection is a no-op.
    :raises IndexError: any index is out of range.
    """
    rows = list(indices)
    if not rows:
        return

    def apply(project: Project) -> Project:
        skipping = not all(project.pages[index].skipped for index in rows)
        for index in rows:
            if project.pages[index].skipped != skipping:
                project = toggle_skip(project, index)
        return project

    state.mutate(apply)


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


def move_choices(page_count: int, moving: Sequence[int]) -> list[tuple[str, int]]:
    """Every place the selected pages can go, as ``(label, target)``.

    Labels number the pages that are *staying*, because that is what the
    user will still see around the moved page once it lands. Positions
    already occupied by the selection are left out: offering "between 2
    and 3" to a page that is already there is offering to do nothing.

    :param page_count: how many pages the document has.
    :param moving: indices being moved.
    :returns: labels paired with a target index in the ORIGINAL list, in
        reading order. Empty when there is nowhere to go.
    """
    moving_set = set(moving)
    staying = [i for i in range(page_count) if i not in moving_set]
    if not staying:
        return []

    unmoved = list(range(page_count))
    choices: list[tuple[str, int]] = []
    for slot in range(len(staying) + 1):
        target = staying[slot] if slot < len(staying) else page_count
        # A target that would leave the pages exactly where they are is not
        # a move; offering it is offering to do nothing.
        if move_rows_to(page_count, moving, target) == unmoved:
            continue
        if slot == 0:
            label = f"Before page {staying[0] + 1}"
        elif slot == len(staying):
            label = f"After page {staying[-1] + 1} (at the end)"
        else:
            label = f"Between pages {staying[slot - 1] + 1} and {staying[slot] + 1}"
        choices.append((label, target))
    return choices


def move_rows_to(count: int, rows: Sequence[int], target: int) -> list[int]:
    """The page order after moving ``rows`` to sit before index ``target``.

    Pure, because the alternative is asking Qt what it did to its own
    model and Qt's answer arrives too late: a ``QListWidget`` removes the
    dragged row in ``startDrag``, AFTER ``dropEvent`` returns, so anything
    that reads the view during the drop sees a half-applied move.

    :param count: how many pages there are.
    :param rows: the indices being moved. Their relative order is kept.
    :param target: the index, in the original list, that the selection
        should come to sit before. ``count`` means the end.
    :returns: the new order as original indices.
    """
    moving = sorted(set(rows))
    staying = [i for i in range(count) if i not in set(moving)]
    # `target` counts positions in the original list, so the pages being
    # lifted out from before it shift the insertion point back.
    insert_at = target - sum(1 for row in moving if row < target)
    insert_at = max(0, min(insert_at, len(staying)))
    return staying[:insert_at] + moving + staying[insert_at:]


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


def _qt_move_action():
    from PySide6.QtCore import Qt

    return Qt.DropAction.MoveAction


def _qt_custom_context_menu():
    from PySide6.QtCore import Qt

    return Qt.ContextMenuPolicy.CustomContextMenu


def _page_index_role():
    """The item role carrying a page's index in the document.

    Items are rebuilt on every refresh and Qt renumbers rows during a
    drop, so the row an item sits on is not a stable identity. Stamping
    the document index onto the item means the order left behind by a drop
    can be read straight back.
    """
    from PySide6.QtCore import Qt

    return Qt.ItemDataRole.UserRole + 1


def _reorderable_list_widget_class():
    """A ``QListWidget`` that reports a completed internal-move drop.

    Built lazily, like every other Qt type in this module, so importing it
    does not require Qt.

    ``QListWidget`` does not emit ``rowsMoved`` for an internal move, so
    nothing reports the drop by signal. Worse, letting Qt perform the move
    and then reading the result back does not work either: the source row
    is removed in ``startDrag``, AFTER ``dropEvent`` returns, so anything
    reading the view during the drop sees the inserted copy and the
    original both present. That half-applied state is not a permutation, so
    it was rejected and the view rebuilt -- and Qt then removed a row from
    the rebuilt list, deleting whichever page happened to sit at the source
    index. That is the page that "disappeared" (the project was never
    wrong, which is why undo put it back).

    So this does not let Qt move anything. It works out the target row,
    refuses the drop so ``startDrag`` has nothing to clean up, and reports
    the move for the view to apply in one piece.
    """
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import QAbstractItemView, QListWidget

    class ReorderableListWidget(QListWidget):
        # rows being moved, and the row they should come to sit before.
        reorder_requested = Signal(list, int)

        def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
            rows = sorted({index.row() for index in self.selectedIndexes()})
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                target = index.row()
                below = QAbstractItemView.DropIndicatorPosition.BelowItem
                if self.dropIndicatorPosition() == below:
                    target += 1
            else:
                # Dropped past the last item, on empty space.
                target = self.count()

            # Refuse the move so Qt neither inserts a copy nor removes the
            # source; the view is repopulated from the project instead.
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.accept()
            if rows:
                self.reorder_requested.emit(rows, target)

    return ReorderableListWidget


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

    def __init__(
        self,
        state: AppState,
        parent=None,
        *,
        choose_blank_position=None,
        choose_move_target=None,
    ) -> None:
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

            # Which page the user is looking at. The preview follows it, so
            # clicking a page in the grid shows the sheet it lands on --
            # which is the question the grid exists to answer and could not
            # answer before: a 60-page book is 30 sheets, and finding the
            # one carrying page 41 meant stepping a spinbox.
            page_selected = Signal(int)

        self._signals = _Signals()
        self.pages_changed = self._signals.pages_changed
        self.page_selected = self._signals.page_selected
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
        self._choose_move_target = (
            choose_move_target or self._default_choose_move_target
        )
        self.widget = QWidget(parent)
        outer = QVBoxLayout(self.widget)

        self.list_widget = _reorderable_list_widget_class()(self.widget)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        # Without an explicit icon size Qt paints thumbnails at a default
        # ~16px, which reads as a decorative bullet rather than a page.
        self.list_widget.setIconSize(_qt_size(THUMBNAIL_ICON_PX, THUMBNAIL_ICON_PX))
        self.list_widget.setGridSize(
            _qt_size(THUMBNAIL_ICON_PX + 24, THUMBNAIL_ICON_PX + 40)
        )
        # Static, not Snap, and set BEFORE the drag-drop mode -- both halves
        # matter, and each was wrong.
        #
        # In icon mode with any non-Static movement, QListView::dropEvent
        # takes its own branch: it repositions the icon in the viewport and
        # returns without touching the model, so the page order never
        # changes and the drag looks like it did nothing.
        #
        # And setMovement() calls setDragEnabled(movement != Static), so
        # setting it after setDragDropMode() turns dragging back off --
        # downgrading InternalMove to DropOnly, which is worse than the bug
        # it was meant to fix.
        # Several pages at once: a chapter dragged to the front, or moved
        # together through "Move to...". Qt's default is SingleSelection,
        # which made every multi-page path below unreachable.
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDragEnabled(True)
        self.list_widget.viewport().setAcceptDrops(True)
        # InternalMove does not imply a move action: the default stays
        # CopyAction, and a copy-dropped page duplicates rather than moves.
        self.list_widget.setDefaultDropAction(_qt_move_action())
        self.list_widget.setDropIndicatorShown(True)
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
        self.list_widget.reorder_requested.connect(self.move_pages)
        self.list_widget.setContextMenuPolicy(_qt_custom_context_menu())
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
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
                item = QListWidgetItem(label)
                # Its identity, so the order after a drop can be read back.
                item.setData(_page_index_role(), i)
                self.list_widget.addItem(item)
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
        # The whole selection. The list is `ExtendedSelection` and the
        # context menu already says "Move 12 pages to..." -- but Rotate and
        # Skip both went through `currentRow()`, so selecting twelve pages
        # and rotating turned exactly one of them, from either the button
        # or the menu, with no indication that the other eleven were
        # ignored.
        rows = self._selected_indices()
        if not rows:
            return
        rotate_many(self.state, rows)
        self.refresh()
        self._reselect(rows)
        self.pages_changed.emit()

    def _on_skip_clicked(self) -> None:
        rows = self._selected_indices()
        if not rows:
            return
        skip_many(self.state, rows)
        self.refresh()
        self._reselect(rows)
        self.pages_changed.emit()

    def _reselect(self, rows: Sequence[int]) -> None:
        """Put the selection back after a refresh that did not move anything.

        ``refresh`` rebuilds the list with ``clear()``, which drops the
        selection. That is barely noticeable on one page and makes a
        multi-page gesture unusable: rotating twelve pages 180 degrees means
        clicking Rotate twice, and after the first click there is nothing
        selected to rotate again.

        Called only from the handlers that leave the page *order* alone.
        Restoring by row index is correct for those and wrong after a move
        or an insert, where the same index names a different page -- which
        is why this is not inside ``refresh`` itself.

        :param rows: the rows to select again.
        :returns: nothing.
        """
        from PySide6.QtCore import QItemSelectionModel

        model = self.list_widget.selectionModel()
        if model is None:
            return
        model.clearSelection()
        page_count = self.list_widget.count()
        for row in rows:
            if 0 <= row < page_count:
                model.select(
                    self.list_widget.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select,
                )
        if rows:
            self.list_widget.setCurrentRow(rows[0], QItemSelectionModel.SelectionFlag.NoUpdate)

    def _default_choose_move_target(self, choices):
        """Ask where the selected pages should go.

        :param choices: ``(label, target)`` pairs from
            :func:`move_choices`.
        :returns: the chosen target index, or ``None`` if cancelled.
        """
        from PySide6.QtWidgets import QInputDialog

        label, ok = QInputDialog.getItem(
            self.widget,
            "Move page",
            "Where should it go?",
            [text for text, _target in choices],
            0,
            False,
        )
        if not ok:
            return None
        return next(target for text, target in choices if text == label)

    def _selected_indices(self) -> list[int]:
        """Every selected row, in document order."""
        return sorted(index.row() for index in self.list_widget.selectedIndexes())

    def _on_context_menu(self, point) -> None:
        """Offer the actions for the page under the cursor.

        Dragging is fine for nudging a page a few places and hopeless
        across a long document -- reaching page 200 from page 3 means
        dragging against an auto-scroll. "Move to..." names the destination
        instead.

        :param point: the click position, in viewport coordinates.
        :returns: nothing.
        """
        from PySide6.QtWidgets import QMenu

        row = self.list_widget.indexAt(point).row()
        if row < 0:
            return
        # A right-click outside the selection acts on what was clicked,
        # which is what every file manager does.
        if row not in self._selected_indices():
            self.list_widget.setCurrentRow(row)
        rows = self._selected_indices() or [row]

        menu = QMenu(self.widget)
        move_action = menu.addAction(
            "Move to..." if len(rows) == 1 else f"Move {len(rows)} pages to..."
        )
        menu.addSeparator()
        rotate_action = menu.addAction(
            "Rotate 90°" if len(rows) == 1 else f"Rotate {len(rows)} pages 90°"
        )
        skip_action = menu.addAction(
            "Skip / unskip" if len(rows) == 1 else f"Skip / unskip {len(rows)} pages"
        )
        blank_action = menu.addAction("Insert blank...")

        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(point))
        if chosen is None:
            return
        if chosen is move_action:
            self._move_selection_via_dialog(rows)
        elif chosen is rotate_action:
            self._on_rotate_clicked()
        elif chosen is skip_action:
            self._on_skip_clicked()
        elif chosen is blank_action:
            self._on_insert_blank_clicked()

    def _move_selection_via_dialog(self, rows: Sequence[int]) -> None:
        """Ask where ``rows`` should go, then move them there.

        :param rows: the pages to move.
        :returns: nothing. A document with nowhere to move to -- every page
            selected -- offers no choices and does nothing.
        """
        choices = move_choices(len(self.state.project.pages), rows)
        if not choices:
            return
        target = self._choose_move_target(choices)
        if target is None:
            return
        self.move_pages(rows, target)

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

    def move_pages(self, rows: Sequence[int], target: int) -> None:
        """Move ``rows`` so they sit before ``target``, then re-impose.

        The one path every reorder goes through -- a drag, or "Move to..."
        from the context menu -- so both are a single step of undo and both
        announce themselves.

        :param rows: indices to move, in the current order.
        :param target: the index they should come to sit before;
            ``len(pages)`` means the end.
        :returns: nothing. A move that would change nothing is dropped
            rather than pushed onto the undo stack.
        """
        count = len(self.state.project.pages)
        rows = [row for row in rows if 0 <= row < count]
        if not rows:
            return
        order = move_rows_to(count, rows, target)
        if order == list(range(count)):
            self.refresh()
            return
        reorder_to(self.state, order)
        self.refresh()
        # Keep hold of the pages that moved. `refresh` rebuilds every item,
        # which drops the selection -- so without this the moved page is
        # deselected the instant it lands, the preview stops following it,
        # and dragging it again means finding and re-clicking it first.
        self._select_rows([order.index(row) for row in rows])
        self.pages_changed.emit()

    def _select_rows(self, rows: Sequence[int]) -> None:
        """Select exactly ``rows``, and show the first of them.

        :param rows: rows to select, in the rebuilt list.
        :returns: nothing.
        """
        if not rows:
            return
        self.list_widget.clearSelection()
        # setCurrentRow FIRST: it selects that row alone, discarding
        # anything selected before it, so calling it after the loop would
        # throw away every row but this one.
        self.list_widget.setCurrentRow(rows[0])
        for row in rows[1:]:
            item = self.list_widget.item(row)
            if item is not None:
                item.setSelected(True)
        self.list_widget.scrollToItem(self.list_widget.item(rows[0]))

    def _on_current_row_changed(self, row: int) -> None:
        """Announce which page the user is looking at.

        :param row: the newly current row, or -1 when the selection was
            cleared.
        :returns: nothing; ``page_selected`` carries the index.
        """
        if 0 <= row < len(self.state.project.pages):
            self.page_selected.emit(row)

    def _on_scrolled(self, value: int) -> None:
        self.request_visible_thumbnails(max(0, value))
