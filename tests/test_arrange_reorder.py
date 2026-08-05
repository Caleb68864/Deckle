"""Drag-to-reorder, and following the selection into the preview.

Reordering by dragging was reported broken twice. The first fix added the
``pages_changed`` signal, which was a real defect -- edits reached the
project and nothing announced them -- but it was downstream of the actual
problem: the drop never reached the project at all, because nothing Qt
emitted ever ran the handler.

Three separate faults, all in how the view was configured:

1. ``rowsMoved`` is never emitted for a ``QListWidget`` internal move. The
   drop inserts through ``dropMimeData`` and the view removes the original,
   so a handler connected to it cannot run.
2. ``Movement.Snap`` in icon mode makes ``QListView::dropEvent`` reposition
   the icon in the viewport and return without touching the model.
3. ``setMovement()`` calls ``setDragEnabled(movement != Static)``, so
   setting movement after the drag-drop mode downgrades ``InternalMove``
   to ``DropOnly`` and disables dragging outright.

A genuine drag cannot be forged headlessly -- ``QDropEvent::source()`` is
not virtual, so Qt's C++ side never sees a Python override and the
internal-move branch is unreachable from a synthetic event. These tests
therefore pin the two things that *are* reachable: the configuration,
which is where all three faults lived, and the reconcile step that turns
whatever order the drop leaves behind into the document order.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef  # noqa: E402

LETTER = (612.0, 792.0)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pages(n: int) -> list[SourcePage]:
    return [
        SourcePage(
            ref=SourceRef(
                path=FIXTURE, page_index=i, sha256="a" * 64,
                width_pt=612.0, height_pt=792.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _view(n: int = 4):
    from deckle.app.state import AppState
    from deckle.app.views.arrange_view import ArrangeView

    state = AppState(
        Project(
            pages=_pages(n),
            layout=LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left"),
            printer=None,
        )
    )
    return state, ArrangeView(state)


def _order(state) -> list[int]:
    """The document as page-index identities, so a move is visible."""
    return [page.ref.page_index for page in state.project.pages]


# -- the configuration, where all three faults lived ----------------------


def test_the_grid_is_configured_to_reorder_by_dragging():
    """Every one of these was wrong, and each alone breaks the drag."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QListWidget

    _, view = _view()
    lw = view.list_widget

    assert lw.movement() == QListWidget.Movement.Static, (
        "non-Static movement makes QListView reposition the icon and leave "
        "the model alone"
    )
    assert lw.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert lw.dragEnabled(), "the grid cannot start a drag"
    assert lw.viewport().acceptDrops(), "the grid cannot receive a drop"
    assert lw.defaultDropAction() == Qt.DropAction.MoveAction, (
        "a copy action duplicates the page instead of moving it"
    )


def test_setting_movement_after_drop_mode_would_disable_dragging():
    """The trap that made the second attempt worse than the first.

    Pinned as a property of Qt rather than of our code: if a later edit
    reorders these two calls, the grid silently stops accepting drags, and
    the symptom is identical to the original bug.
    """
    from PySide6.QtWidgets import QAbstractItemView, QListWidget

    lw = QListWidget()
    lw.setViewMode(QListWidget.ViewMode.IconMode)
    lw.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    lw.setMovement(QListWidget.Movement.Static)  # the wrong order

    assert not lw.dragEnabled(), (
        "Qt no longer disables dragging here -- if so, the ordering comment "
        "in arrange_view is stale and this test should be removed"
    )


def test_a_drop_is_reported_even_though_rows_moved_is_not():
    """The signal the old handler waited on never arrives."""
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QDropEvent

    _, view = _view()
    lw = view.list_widget
    fired = []
    lw.dropped.connect(lambda: fired.append(1))

    # QDropEvent does NOT take ownership of the mime data, and Qt keeps
    # using the pointer after this call. Letting Python collect either
    # object leaves Qt reading freed memory -- which crashed the suite with
    # an access violation 48 tests later, nowhere near here. Both are held
    # for the life of the test.
    mime = lw.model().mimeData([lw.model().index(0, 0)])
    event = QDropEvent(
        QPointF(1, 1), Qt.DropAction.MoveAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    lw.dropEvent(event)

    assert fired, "dropEvent did not report the drop"
    assert mime is not None and event is not None  # keep both alive


# -- reconciling the document with what the drop left behind --------------


def _simulate_drop(view, from_row: int, to_row: int) -> None:
    """Rearrange the widget as a completed drop leaves it, then reconcile.

    This is what Qt does to the items; ``_on_dropped`` is what we do about
    it. It is deliberately not a call to ``reorder`` -- the point is that
    the handler reads the view's own state.
    """
    item = view.list_widget.takeItem(from_row)
    view.list_widget.insertItem(to_row, item)
    view._on_dropped()


def test_dragging_a_page_moves_it_in_the_document():
    state, view = _view()

    _simulate_drop(view, 0, 3)

    assert _order(state) == [1, 2, 3, 0]


def test_dragging_a_page_backwards_moves_it_in_the_document():
    state, view = _view()

    _simulate_drop(view, 3, 0)

    assert _order(state) == [3, 0, 1, 2]


def test_a_drop_announces_that_the_pages_changed():
    """Without this the preview -- and therefore the exported PDF, which is
    the preview's plan -- keeps showing the order from before the drag."""
    state, view = _view()
    seen = []
    view.pages_changed.connect(lambda: seen.append(1))

    _simulate_drop(view, 0, 2)

    assert seen, "the reorder reached the project and nothing announced it"


def test_the_labels_are_renumbered_after_a_drop():
    """The label says "page 3"; after a drag it must mean the third page."""
    state, view = _view()

    _simulate_drop(view, 0, 3)

    labels = [view.list_widget.item(i).text() for i in range(4)]
    assert labels == ["page 1", "page 2", "page 3", "page 4"]


def test_a_drop_is_one_step_of_undo():
    state, view = _view()

    _simulate_drop(view, 0, 3)
    state.undo()

    assert _order(state) == [0, 1, 2, 3], "one drag took more than one undo"


def test_a_drop_that_is_not_a_permutation_is_refused(monkeypatch):
    """A copy rather than a move, or an item dragged in from elsewhere,
    would otherwise duplicate or drop pages silently."""
    state, view = _view()
    lw = view.list_widget
    before = _order(state)

    # A copy: the item is duplicated rather than moved.
    lw.insertItem(1, lw.item(0).clone())
    view._on_dropped()

    assert _order(state) == before, "a malformed drop was applied"
    assert lw.count() == len(before), "the view was not rebuilt from the project"


# -- clicking a page follows it into the preview --------------------------


def test_selecting_a_page_announces_it():
    state, view = _view()
    seen = []
    view.page_selected.connect(seen.append)

    view.list_widget.setCurrentRow(2)

    assert seen == [2]


def test_clearing_the_selection_announces_nothing():
    """-1 is not a page, and a spinbox clamped to 0 would jump the preview
    to the front of the book for no reason the user can see."""
    state, view = _view()
    view.list_widget.setCurrentRow(2)
    seen = []
    view.page_selected.connect(seen.append)

    view.list_widget.setCurrentRow(-1)

    assert seen == []
