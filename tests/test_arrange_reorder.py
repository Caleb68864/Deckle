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


def test_a_drop_reports_the_move_and_refuses_to_let_qt_apply_it():
    """The heart of the second bug.

    Qt removes the dragged row in ``startDrag``, after ``dropEvent``
    returns. Anything that lets Qt do the move and then reads the result
    sees the inserted copy and the original both present -- a half-applied
    state that is not a permutation. So the drop is refused outright and
    reported instead, leaving Qt with nothing to clean up.
    """
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QDropEvent

    _, view = _view()
    lw = view.list_widget
    seen = []
    lw.reorder_requested.connect(lambda rows, target: seen.append((rows, target)))
    lw.setCurrentRow(0)

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

    assert seen, "the drop was not reported"
    assert seen[0][0] == [0], "the dragged row was misidentified"
    assert event.dropAction() == Qt.DropAction.IgnoreAction, (
        "Qt was left to move the row itself, which it finishes after this "
        "returns -- the state read here would be half-applied"
    )
    assert lw.count() == 4, "the drop inserted a copy"
    assert mime is not None  # keep alive


# -- reconciling the document with what the drop left behind --------------


def _simulate_drop(view, from_row: int, to_row: int) -> None:
    """The move a drop reports, applied the way the view applies it.

    ``to_row`` is a destination among the pages as they are now, so
    dropping page 0 "at row 3" means it comes to sit before the page
    currently at 3 -- hence the +1 for a forward move, which is what the
    drop indicator shows on screen.
    """
    view.move_pages([from_row], to_row + 1 if to_row > from_row else to_row)


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


def test_a_move_that_changes_nothing_is_not_an_undo_step():
    """Dropping a page back where it started must not leave the user
    pressing Ctrl+Z for a move that never happened."""
    state, view = _view()

    view.move_pages([1], 1)

    assert _order(state) == [0, 1, 2, 3]
    assert not state.can_undo, "a no-op move was pushed onto the history"


def test_a_row_outside_the_document_is_ignored():
    """A stale selection, or a drop reported against a document that has
    since shrunk. Indexing straight into the page list would raise inside
    a Qt event handler, where the traceback goes to the console and the
    user sees nothing."""
    state, view = _view()

    view.move_pages([99], 0)

    assert _order(state) == [0, 1, 2, 3]


# -- the move arithmetic --------------------------------------------------


def test_move_rows_to_is_always_a_permutation():
    """Exhaustive over every small document, every selection, and targets
    past both ends. A move that loses or clones a page is the one failure
    mode that cannot be undone by dragging it back."""
    import itertools

    from deckle.app.views.arrange_view import move_rows_to

    for n in range(7):
        for size in range(1, n + 1):
            for rows in itertools.combinations(range(n), size):
                for target in range(-2, n + 3):
                    order = move_rows_to(n, list(rows), target)
                    assert sorted(order) == list(range(n)), (rows, target, order)
                    moved = [i for i in order if i in set(rows)]
                    assert moved == sorted(rows), "the selection lost its order"
                    stayed = [i for i in order if i not in set(rows)]
                    assert stayed == sorted(stayed), "the other pages were shuffled"


def test_move_rows_to_tolerates_duplicate_and_unsorted_rows():
    """Qt reports selections in click order, not document order."""
    from deckle.app.views.arrange_view import move_rows_to

    assert move_rows_to(4, [3, 0], 2) == move_rows_to(4, [0, 3], 2)
    assert sorted(move_rows_to(4, [1, 1, 1], 3)) == [0, 1, 2, 3]


def test_every_offered_destination_actually_moves_something():
    """A menu entry that does nothing when picked is worse than no entry."""
    import itertools

    from deckle.app.views.arrange_view import move_choices, move_rows_to

    for n in range(7):
        for size in range(1, n + 1):
            for rows in itertools.combinations(range(n), size):
                choices = move_choices(n, list(rows))
                labels = [label for label, _ in choices]
                assert len(set(labels)) == len(labels), f"duplicate label: {labels}"
                for label, target in choices:
                    assert move_rows_to(n, list(rows), target) != list(range(n)), (
                        f"{label!r} is a no-op"
                    )


def test_every_reachable_arrangement_is_offered():
    """The converse: a destination that cannot be picked cannot be reached
    without dragging, which is the thing this menu exists to avoid."""
    import itertools

    from deckle.app.views.arrange_view import move_choices, move_rows_to

    for n in range(1, 7):
        for size in range(1, n + 1):
            for rows in itertools.combinations(range(n), size):
                reachable = {
                    tuple(move_rows_to(n, list(rows), t)) for t in range(n + 1)
                }
                reachable.discard(tuple(range(n)))
                offered = {
                    tuple(move_rows_to(n, list(rows), t))
                    for _, t in move_choices(n, list(rows))
                }
                assert offered == reachable, (n, rows)


def test_moving_every_page_offers_nowhere_to_go():
    from deckle.app.views.arrange_view import move_choices

    assert move_choices(3, [0, 1, 2]) == []
    assert move_choices(0, []) == []


def test_the_labels_name_the_pages_that_stay():
    """They are the numbers still on screen around the gap."""
    from deckle.app.views.arrange_view import move_choices

    labels = [label for label, _ in move_choices(5, [2])]

    assert labels[0] == "Before page 1"
    assert labels[-1] == "After page 5 (at the end)"
    assert "Between pages 2 and 4" not in labels, "that is where it already is"


# -- several pages at once ------------------------------------------------


def test_the_grid_allows_selecting_more_than_one_page():
    """Qt's default is SingleSelection, which made every multi-page path
    unreachable."""
    from PySide6.QtWidgets import QAbstractItemView

    _, view = _view()

    assert (
        view.list_widget.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )


def test_moving_several_pages_keeps_them_together_and_in_order():
    state, view = _view(5)

    view.move_pages([0, 1], 5)

    assert _order(state) == [2, 3, 4, 0, 1]


def test_moving_several_pages_is_one_step_of_undo():
    state, view = _view(5)

    view.move_pages([0, 1], 5)
    state.undo()

    assert _order(state) == [0, 1, 2, 3, 4]


# -- the moved pages stay selected ----------------------------------------


def test_the_moved_page_is_still_selected_where_it_landed():
    """`refresh` rebuilds every item, which drops the selection. Without
    restoring it the page is deselected the moment it lands, the preview
    stops following it, and dragging it again means finding it first."""
    state, view = _view(5)

    view.move_pages([0], 5)

    assert view.list_widget.currentRow() == 4


def test_a_multi_page_move_keeps_the_whole_selection():
    """setCurrentRow selects one row and discards the rest, so it has to
    come before the others are added, not after."""
    state, view = _view(5)

    view.move_pages([0, 1], 5)

    selected = sorted(index.row() for index in view.list_widget.selectedIndexes())
    assert selected == [3, 4]


def test_the_landing_page_is_announced_so_the_preview_follows_it():
    state, view = _view(5)
    seen = []
    view.page_selected.connect(seen.append)

    view.move_pages([0], 5)

    assert seen and seen[-1] == 4


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


# -- the context menu -----------------------------------------------------


def test_move_to_moves_the_page_the_user_picked():
    """Dragging is fine for nudging a page a few places and hopeless
    across a long document: reaching page 200 from page 3 means dragging
    against an auto-scroll."""
    from deckle.app.views.arrange_view import ArrangeView
    from deckle.app.state import AppState

    state = AppState(
        Project(
            pages=_pages(5),
            layout=LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left"),
            printer=None,
        )
    )
    # Always pick the last offered destination: the end of the document.
    view = ArrangeView(state, choose_move_target=lambda choices: choices[-1][1])

    view._move_selection_via_dialog([0])

    assert _order(state) == [1, 2, 3, 4, 0]


def test_cancelling_the_move_dialog_changes_nothing():
    from deckle.app.views.arrange_view import ArrangeView
    from deckle.app.state import AppState

    state = AppState(
        Project(
            pages=_pages(4),
            layout=LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left"),
            printer=None,
        )
    )
    view = ArrangeView(state, choose_move_target=lambda choices: None)

    view._move_selection_via_dialog([0])

    assert _order(state) == [0, 1, 2, 3]
    assert not state.can_undo


def test_a_right_click_on_empty_space_opens_nothing():
    """indexAt returns -1 there, and indexing the page list with it would
    silently act on the last page."""
    from PySide6.QtCore import QPoint

    state, view = _view()

    view._on_context_menu(QPoint(9999, 9999))  # must not raise or act

    assert _order(state) == [0, 1, 2, 3]


def test_the_grid_offers_a_context_menu_at_all():
    from PySide6.QtCore import Qt

    _, view = _view()

    assert (
        view.list_widget.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    )


# -- Rotate and Skip act on the selection, not on one row -----------------
#
# The grid is `ExtendedSelection` and the context menu already offered
# "Move 12 pages to...", but Rotate and Skip both went through
# `currentRow()`. Selecting twelve scanned pages and rotating turned
# exactly one of them, from either the button or the menu, with nothing on
# screen to say the other eleven had been ignored.


def _select(view, rows):
    from PySide6.QtCore import QItemSelectionModel

    model = view.list_widget.selectionModel()
    model.clearSelection()
    for row in rows:
        model.select(
            view.list_widget.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select,
        )


def test_rotate_turns_every_selected_page():
    state, view = _view(4)
    _select(view, [0, 2, 3])

    view.rotate_selection()

    assert [page.rotate_deg for page in state.project.pages] == [90, 0, 90, 90]


def test_rotate_turns_each_page_from_its_own_angle():
    """A selection that is not all facing the same way stays that way."""
    state, view = _view(3)
    from deckle.app.views.arrange_view import rotate_many

    rotate_many(state, [1], 180)
    _select(view, [0, 1, 2])

    view.rotate_selection()

    assert [page.rotate_deg for page in state.project.pages] == [90, 270, 90]


def test_rotating_a_selection_is_one_undo_step():
    """Calling `rotate` in a loop would be simpler and wrong: forty pages
    would bury forty entries in a bounded stack, so the single Ctrl+Z the
    user expects would undo one page and lose the rest of their history."""
    state, view = _view(4)
    _select(view, [0, 1, 2, 3])
    before = len(state._undo_stack)

    view.rotate_selection()

    assert len(state._undo_stack) == before + 1
    state.undo()
    assert [page.rotate_deg for page in state.project.pages] == [0, 0, 0, 0]


def test_skip_marks_the_whole_selection():
    state, view = _view(4)
    _select(view, [1, 2])

    view.skip_selection()

    assert [page.skipped for page in state.project.pages] == [False, True, True, False]


def test_skip_on_a_mixed_selection_skips_rather_than_inverting_it():
    """Toggling each page independently is the obvious reading and the
    wrong one: on a mixed selection it inverts the mixture instead of
    resolving it, so "skip these" returns the same number of skipped pages
    in different places."""
    state, view = _view(4)
    from deckle.app.views.arrange_view import skip_many

    skip_many(state, [1])
    _select(view, [0, 1, 2])

    view.skip_selection()

    assert [page.skipped for page in state.project.pages] == [True, True, True, False]


def test_skip_unskips_only_when_everything_selected_is_already_skipped():
    state, view = _view(3)
    from deckle.app.views.arrange_view import skip_many

    skip_many(state, [0, 1])
    _select(view, [0, 1])

    view.skip_selection()

    assert [page.skipped for page in state.project.pages] == [False, False, False]


def test_the_selection_survives_the_action_so_it_can_be_repeated():
    """`refresh` rebuilds the list with `clear()`, which drops the
    selection -- barely noticeable on one page, and fatal to a multi-page
    gesture: rotating twelve pages 180 degrees means clicking Rotate
    twice, and after the first click there was nothing left selected."""
    state, view = _view(4)
    _select(view, [0, 2])

    view.rotate_selection()

    assert view._selected_indices() == [0, 2]

    view.rotate_selection()

    assert [page.rotate_deg for page in state.project.pages] == [180, 0, 180, 0]


def test_a_single_page_still_toggles():
    """The one-page case is what it always was."""
    state, view = _view(3)
    _select(view, [1])

    view.skip_selection()
    assert state.project.pages[1].skipped is True

    view.skip_selection()
    assert state.project.pages[1].skipped is False


# --- Skip range..., and Remove -------------------------------------------
#
# Two gestures that look adjacent and are not. "Skip range..." STATES a
# skip and never deletes; Remove deletes and is the only thing in the grid
# that does. Both are a single `mutate`, so each is one Ctrl+Z.


def _view_with(n, **kwargs):
    from deckle.app.state import AppState
    from deckle.app.views.arrange_view import ArrangeView

    state = AppState(
        Project(
            pages=_pages(n),
            layout=LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left"),
            printer=None,
        )
    )
    return state, ArrangeView(state, **kwargs)


def test_parse_page_range_matches_the_cli_grammar():
    from deckle.app.views.arrange_view import parse_page_range

    assert parse_page_range("1-6, 309-312", 312) == (
        list(range(0, 6)) + list(range(308, 312))
    )


def test_parse_page_range_agrees_with_the_cli_parser():
    """The grammar exists twice, because `deckle.app` must not import
    `deckle.cli`. This is what keeps them from drifting."""
    from deckle.cli import _parse_page_selection
    from deckle.app.views.arrange_view import parse_page_range

    for text in ("7", "1,3", "7-12,20", " 2 , 4 - 6 "):
        assert parse_page_range(text, 400) == sorted(
            set(_parse_page_selection(text))
        )


@pytest.mark.parametrize("text", ["0", "313", "one to six", "", "6-1", "1,,2"])
def test_parse_page_range_refuses_what_it_cannot_honour(text):
    from deckle.app.views.arrange_view import parse_page_range

    with pytest.raises(ValueError) as excinfo:
        parse_page_range(text, 312)

    assert str(excinfo.value), "the message is shown to the user"


def test_skip_range_marks_the_named_pages():
    state, view = _view_with(6, ask_skip_range=lambda n: "1-2")

    view.skip_range_button.click()

    assert [p.skipped for p in state.project.pages] == [
        True, True, False, False, False, False
    ]


def test_skip_range_is_one_undo_step():
    """Sixteen pages of front matter skipped one call at a time would bury
    sixteen entries in a bounded undo stack."""
    state, view = _view_with(10, ask_skip_range=lambda n: "1-6")

    view.skip_range_selection()
    state.undo()

    assert all(page.skipped is False for page in state.project.pages)
    assert state.can_undo is False


def test_skip_range_states_rather_than_toggles():
    """Naming a page that is already skipped is not a request to bring it
    back -- that is what the Skip button is for."""
    from deckle.app.views.arrange_view import skip_many

    state, view = _view_with(4, ask_skip_range=lambda n: "1-2")
    skip_many(state, [0])

    view.skip_range_selection()

    assert [p.skipped for p in state.project.pages] == [True, True, False, False]


def test_a_cancelled_skip_range_changes_nothing():
    state, view = _view_with(4, ask_skip_range=lambda n: None)

    view.skip_range_selection()

    assert state.can_undo is False


def test_a_malformed_skip_range_reports_on_the_button():
    state, view = _view_with(4, ask_skip_range=lambda n: "one to six")

    view.skip_range_selection()

    assert state.can_undo is False
    assert "page number" in view.skip_range_button.toolTip()


def test_remove_deletes_the_selection():
    state, view = _view_with(5, confirm_remove=lambda n: True)
    _select(view, [0, 1])

    view.remove_selection()

    assert _order(state) == [2, 3, 4]


def test_remove_asks_first():
    state, view = _view_with(5, confirm_remove=lambda n: False)
    _select(view, [0, 1])

    view.remove_selection()

    assert len(state.project.pages) == 5
    assert state.can_undo is False


def test_remove_is_told_how_many():
    asked: list[int] = []
    state, view = _view_with(5, confirm_remove=lambda n: asked.append(n) or True)
    _select(view, [0, 2, 4])

    view.remove_selection()

    assert asked == [3]


def test_remove_is_one_undo_step():
    """A loop over a one-page helper would push three entries, so the
    single Ctrl+Z the user expects would bring back one page and cost the
    rest of their history."""
    state, view = _view_with(5, confirm_remove=lambda n: True)
    _select(view, [0, 1, 2])

    view.remove_selection()
    state.undo()

    assert _order(state) == [0, 1, 2, 3, 4]
    assert state.can_undo is False


def test_remove_announces_the_change():
    state, view = _view_with(4, confirm_remove=lambda n: True)
    seen: list[int] = []
    view.pages_changed.connect(lambda: seen.append(1))
    _select(view, [0])

    view.remove_selection()

    assert seen == [1], "the preview would keep showing the pre-removal plan"


def test_remove_leaves_the_cursor_where_the_pages_were():
    state, view = _view_with(5, confirm_remove=lambda n: True)
    _select(view, [1, 2])

    view.remove_selection()

    assert view.list_widget.currentRow() == 1


def test_removing_everything_leaves_no_current_row():
    state, view = _view_with(3, confirm_remove=lambda n: True)
    _select(view, [0, 1, 2])

    view.remove_selection()

    assert state.project.pages == []


def test_remove_with_nothing_selected_does_nothing():
    state, view = _view_with(3, confirm_remove=lambda n: True)
    view.list_widget.clearSelection()

    view.remove_selection()

    assert len(state.project.pages) == 3
    assert state.can_undo is False
