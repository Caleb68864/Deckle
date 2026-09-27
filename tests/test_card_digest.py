from dataclasses import replace

from deckle.core.card_layout import impose_cards
from deckle.core.plan_digest import plan_digest
from deckle.core.models import CardAsset, CardDeck, CardEntry, CardFace, CardSettings, SourcePage, SourceRef


def asset(name):
    import hashlib
    from pathlib import Path
    path = str(Path(__file__).resolve())
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ref = SourceRef(path, 0, digest, 200, 300)
    return CardAsset(name, name, path, digest, SourcePage(ref, 0, False))


def test_digest_tracks_every_card_rectangle_and_identity():
    a, b = asset("a"), asset("b")
    deck = CardDeck((a,b),(CardEntry("c",CardFace("a"),CardFace("b")),),CardSettings())
    plan = impose_cards(deck,(612,792)); base=plan_digest(plan)
    page=plan.sheets[0].front.pages[0]
    variants = [
        replace(page, card=replace(page.card, black_border=False)),
        replace(page, card=replace(page.card, copy_index=1)),
        replace(page, card=replace(page.card, card_id="different")),
        replace(page, card=replace(page.card, bleed_rect_pt=(0,0,1,1))),
        replace(page, card=replace(page.card, art_rect_pt=(0,0,1,1))),
        replace(page, card=replace(page.card, trim_rect_pt=(0,0,1,1))),
    ]
    for candidate in variants:
        clone=replace(plan, sheets=[replace(plan.sheets[0],front=replace(plan.sheets[0].front,pages=(candidate,)+plan.sheets[0].front.pages[1:]))])
        assert plan_digest(clone)!=base
