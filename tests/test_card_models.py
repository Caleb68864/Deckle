from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from deckle.core.cards import card_output_problems, project_has_content, project_source_pages, validate_card_deck
from deckle.core.models import *


def asset(i, path=None):
    path = path or f"/tmp/{i}.pdf"
    ref = SourceRef(path, 0, "a" * 64, 100.0, 100.0)
    return CardAsset(i, f"{i}.png", f"/tmp/{i}.png", "b" * 64, SourcePage(ref, 0, False))


def deck():
    a, b = asset("a"), asset("b")
    return CardDeck((a, b), (CardEntry("c", CardFace("a"), CardFace("b")),), CardSettings())


def test_card_defaults_and_frozen():
    d = deck()
    assert d.settings == CardSettings(180, 252, 18, 18, 0, 0, True, 9, 3, "contain", False)
    with pytest.raises(FrozenInstanceError): d.cards[0].copies = 2
    assert Project([], LayoutSettings((612, 792), 18, "left"), None).kind == "book"


@pytest.mark.parametrize("kwargs", [
    {"width_pt": 0}, {"gap_x_pt": -1}, {"border_width_pt": 90},
    {"height_pt": float("nan")}, {"bleed_pt": float("inf")},
    {"margin_x_pt": True},
])
def test_invalid_settings(kwargs):
    d = deck()
    with pytest.raises(ValueError): validate_card_deck(__import__('dataclasses').replace(d, settings=CardSettings(**(d.settings.__dict__ | kwargs))))


def test_unmatched_incomplete_is_editable_but_not_printable():
    d = deck(); d = __import__('dataclasses').replace(d, cards=(CardEntry("c", CardFace("a")),))
    validate_card_deck(d)
    assert card_output_problems(d) == ("Assign backs for: a.png.",)
    p = Project([], LayoutSettings((612, 792), 18, "left"), None, "cards", d)
    assert project_has_content(p)
    assert project_source_pages(p) == tuple(a.page for a in d.assets)


def test_reused_sources_are_deduped_for_source_checks():
    a = asset("a"); d = CardDeck((a,), (CardEntry("1", CardFace("a")),), CardSettings())
    p = Project([], LayoutSettings((612, 792), 18, "left"), None, "cards", d)
    assert project_source_pages(p) == (a.page,)
