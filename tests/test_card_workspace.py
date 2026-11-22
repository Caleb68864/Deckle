import hashlib

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from deckle.app.state import AppState
from deckle.app.views.cards_view import CardsView
from deckle.app.views.card_layout_panel import CardLayoutPanel
from deckle.app.main import _project_output_ready
from deckle.core.models import *


class Timer:
    def __init__(self,*args): pass
    def start(self): pass
    def cancel(self): pass


def fixture_project(paired=True):
    assets=[]
    for name in ("front","back"):
        ref=SourceRef(f"/{name}.pdf",0,("a" if name=="front" else "b")*64,100,140)
        assets.append(CardAsset(name,name+".png",f"/{name}.png","c"*64,SourcePage(ref,0,False)))
    card=CardEntry("one",CardFace("front"),CardFace("back") if paired else None)
    return Project([],LayoutSettings((612,792),18,"left"),None,"cards",CardDeck(tuple(assets),(card,),CardSettings()))


@pytest.fixture(scope="module")
def app(): return QApplication.instance() or QApplication([])


def test_table_mode_and_edits_keep_card_pair_together(app):
    state=AppState(fixture_project(),project_path="/tmp/card-ui.deckle",timer_factory=Timer)
    changes=[]; view=CardsView(state,on_change=lambda:changes.append(1))
    assert view.table.rowCount()==1
    assert view.table.item(0,1).text()=="front.png"
    assert view.table.item(0,2).text()=="back.png"
    view.mode.setCurrentIndex(1)
    assert state.project.card_deck.back_mode=="shared"
    assert state.project.card_deck.cards[0].back.asset_id=="back"
    view.move(1)
    assert len(changes)==1


def test_layout_panel_units_border_toggle_and_capacity(app):
    state=AppState(fixture_project(),project_path="/tmp/card-ui.deckle",timer_factory=Timer)
    panel=CardLayoutPanel(state,on_change=lambda:None)
    before=state.project.card_deck.settings
    panel.unit.setCurrentText("mm")
    assert abs(panel.width_pt.value()-(180*25.4/72))<0.001
    assert panel.border_width_pt.isEnabled()
    panel.border.setChecked(False)
    after=state.project.card_deck.settings
    assert not after.border_enabled and after.border_width_pt==before.border_width_pt and after.bleed_pt==before.bleed_pt
    assert "9 cards per sheet" in panel.capacity.text()


def test_incomplete_card_project_is_saveable_but_not_output_ready():
    assert not _project_output_ready(fixture_project(False))
    assert _project_output_ready(fixture_project(True))


# -- BH5: leaving a box is not an edit ------------------------------------


def _number_boxes(panel):
    return list(panel._boxes.items())


@pytest.mark.parametrize("unit", ["pt", "mm", "in"])
def test_tabbing_through_the_card_boxes_changes_nothing(app, unit):
    """Qt fires editingFinished on focus-out. It used to re-apply every box:
    an undo step each, the redo stack wiped, and an untouched project marked
    unsaved, so closing the window asked to save it."""
    state = AppState(fixture_project(), project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    panel = CardLayoutPanel(state, on_change=lambda: None)
    panel.unit.setCurrentText(unit)
    before = state.project

    for _key, box in _number_boxes(panel):
        box.editingFinished.emit()

    assert state.project is before
    assert not state.can_undo
    assert not state.dirty


def test_leaving_a_box_in_mm_does_not_round_the_stored_length(app):
    """100pt shows as 35.278mm; writing that back was 100.0006pt."""
    from dataclasses import replace

    project = fixture_project()
    deck = project.card_deck
    project = replace(project, card_deck=replace(deck, settings=replace(deck.settings, width_pt=100.0)))
    state = AppState(project, project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    panel = CardLayoutPanel(state, on_change=lambda: None)
    panel.unit.setCurrentText("mm")

    panel.width_pt.editingFinished.emit()

    assert state.project.card_deck.settings.width_pt == 100.0


def test_a_real_edit_still_lands_and_is_one_undo_step(app):
    state = AppState(fixture_project(), project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    panel = CardLayoutPanel(state, on_change=lambda: None)

    panel.width_pt.setValue(144.0)
    panel.width_pt.editingFinished.emit()

    assert state.project.card_deck.settings.width_pt == 144.0
    assert state.can_undo
    state.undo()
    assert state.project.card_deck.settings.width_pt == 180.0


# -- BH6: the row buttons say when they would do nothing -----------------


def _three_card_view():
    from dataclasses import replace

    project = fixture_project()
    deck = project.card_deck
    one = deck.cards[0]
    cards = (one, replace(one, id="two"), replace(one, id="three"))
    state = AppState(replace(project, card_deck=replace(deck, cards=cards)),
                     project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    return CardsView(state, on_change=lambda: None)


def _enabled(view):
    return {name: getattr(view, name).isEnabled() for name in (
        "remove_button", "rotate_front_button", "rotate_back_button", "up_button", "down_button")}


def test_with_nothing_selected_the_row_buttons_are_off(app):
    view = _three_card_view()
    view.table.clearSelection()

    assert not any(_enabled(view).values())


@pytest.mark.parametrize("row,up,down", [(0, False, True), (1, True, True), (2, True, False)])
def test_move_is_offered_only_where_the_card_can_go(app, row, up, down):
    view = _three_card_view()
    view.table.selectRow(row)

    state = _enabled(view)
    assert state["remove_button"] and state["rotate_front_button"] and state["rotate_back_button"]
    assert (state["up_button"], state["down_button"]) == (up, down)


def test_move_is_off_with_two_cards_selected_because_it_moves_one(app):
    from PySide6.QtCore import QItemSelectionModel

    view = _three_card_view()
    view.table.selectRow(0)
    view.table.selectionModel().select(
        view.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )

    state = _enabled(view)
    assert state["remove_button"]
    assert not state["up_button"] and not state["down_button"]


def test_a_move_keeps_the_buttons_true_to_the_new_row(app):
    view = _three_card_view()
    view.table.selectRow(1)
    view.move(1)

    assert view.table.selectionModel().selectedRows()[0].row() == 2
    assert not view.down_button.isEnabled() and view.up_button.isEnabled()
