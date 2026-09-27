from deckle.core.card_layout import _cut_marks, card_grid, impose_cards
from deckle.core.models import *


def asset(name, w=100, h=120):
    ref = SourceRef(f"/tmp/{name}.pdf", 0, "a"*64, w, h)
    return CardAsset(name, name + ".png", f"/tmp/{name}.png", "b"*64, SourcePage(ref, 0, False))


def test_fixed_geometry_oracle_and_mirrored_partial_sheet():
    assets = tuple(asset(x) for x in "ABCD")
    cards = tuple(CardEntry(x, CardFace(x), CardFace(x)) for x in "ABC")
    settings = CardSettings(100,120,20,20,10,10,False,9,3)
    deck = CardDeck(assets,cards,settings)
    plan = impose_cards(deck,(500,400))
    assert card_grid(settings,(500,400)) == __import__('deckle.core.card_layout',fromlist=['CardGrid']).CardGrid(4,2,8,(35,75),(100,120))
    front=plan.sheets[0].front.pages; back=plan.sheets[0].back.pages
    assert [p.card.card_id if p.card else None for p in front[:4]] == ["A","B","C",None]
    assert [p.card.card_id if p.card else None for p in back[:4]] == [None,"C","B","A"]
    assert front[0].card.trim_rect_pt == (35,205,100,120)
    assert back[3].card.trim_rect_pt == (365,205,100,120)

    small = CardDeck(assets[:3], cards, CardSettings(100,120,20,20,10,10,False,9,3))
    half = impose_cards(small, (260, 300))
    assert [p.card.card_id if p.card else None for p in half.sheets[0].front.pages[:4]] == ["A","B","C",None]
    assert [p.card.card_id if p.card else None for p in half.sheets[0].back.pages[:4]] == ["B","A",None,"C"]


def test_default_letter_and_a4_capacities_and_border_toggle():
    settings=CardSettings()
    assert card_grid(settings,(612,792)).capacity==6
    assert card_grid(settings,(595.275590551,841.889763780)).capacity==9
    assert card_grid(__import__('dataclasses').replace(settings,border_enabled=False),(612,792)).capacity==9


def test_copies_get_stable_indices_and_cut_marks_are_front_only():
    a,b=asset("a"),asset("b")
    deck=CardDeck((a,b),(CardEntry("c",CardFace("a"),CardFace("b"),2),),CardSettings(100,120,20,20,0,0,False,9,3,"contain",True))
    plan=impose_cards(deck,(500,400))
    cards=[p.card for p in plan.sheets[0].front.pages if p.card]
    assert [c.copy_index for c in cards]==[0,1]
    assert len(plan.sheets[0].front.marks)==16
    assert plan.sheets[0].back.marks==()
    # With a 3 pt bleed, a 2 pt tick ends 1 pt outside the bleed edge.
    marks=_cut_marks((10,20,100,120),(7,17,106,126))
    assert (marks[0].x0,marks[0].y0,marks[0].x1,marks[0].y1)==(4,20,6,20)
    assert (marks[4].x0,marks[4].y0,marks[4].x1,marks[4].y1)==(10,14,10,16)


def test_500_card_shared_back_smoke_has_no_missing_slots():
    common = asset("common")
    entries = tuple(CardEntry(f"card-{i}", CardFace("common")) for i in range(500))
    deck = CardDeck((common,), entries, CardSettings(), "shared", CardFace("common"))
    plan = impose_cards(deck, (612, 792))
    assert len(plan.sheets) == 84
    assert all(len(sheet.front.pages) == len(sheet.back.pages) == 6 for sheet in plan.sheets)
    assert sum(page.card is not None for sheet in plan.sheets for page in sheet.front.pages) == 500
    assert sum(page.card is not None for sheet in plan.sheets for page in sheet.back.pages) == 500
