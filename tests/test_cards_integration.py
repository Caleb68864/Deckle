"""One saved card project through pairing, planning, persistence, and PDF export."""
import hashlib

import pikepdf

from deckle.core.card_pairing import apply_pairing, suggest_card_pairs
from deckle.core.card_layout import card_grid, impose_cards
from deckle.core.export import export
from deckle.core.models import (
    CardAsset, CardDeck, CardEntry, CardFace, CardSettings, LayoutSettings,
    Project, SourcePage, SourceRef,
)
from deckle.core.plan_digest import plan_digest
from deckle.core.project_io import load_project, save_project


def _asset(tmp_path, asset_id, label):
    source = tmp_path / f"{asset_id}.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 140))
    pdf.save(source)
    pdf.close()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    page = SourcePage(SourceRef(str(source), 0, digest, 100, 140), 0, False)
    return CardAsset(asset_id, label, str(tmp_path / label), "b" * 64, page)


def test_pair_persist_and_export_the_same_card_plan(tmp_path):
    goblin = _asset(tmp_path, "g", "Goblin-front.png")
    dragon = _asset(tmp_path, "d", "Dragon-front.png")
    goblin_back = _asset(tmp_path, "gb", "Goblin-back.png")
    dragon_back = _asset(tmp_path, "db", "Dragon-back.png")
    assets = (goblin, dragon, goblin_back, dragon_back)
    cards = (CardEntry("goblin", CardFace("g"), copies=2), CardEntry("dragon", CardFace("d")))
    deck = CardDeck(assets, cards, CardSettings(), back_mode="individual")
    backs = (goblin_back, dragon_back)
    proposal = suggest_card_pairs((goblin, dragon), backs, "filename")
    deck = apply_pairing(deck, proposal)
    project = Project([], LayoutSettings((612, 792), 18, "left"), None, "cards", deck)

    original_plan = impose_cards(deck, project.layout.paper)
    destination = tmp_path / "cards.deckle"
    save_project(project, str(destination))
    reopened = load_project(str(destination), allowed_roots=(str(tmp_path),))
    reopened_plan = impose_cards(reopened.card_deck, reopened.layout.paper)
    assert reopened_plan == original_plan
    assert plan_digest(reopened_plan) == plan_digest(original_plan)
    assert reopened_plan.sheets[0].front.pages[0].card.card_id == "goblin"
    columns = card_grid(deck.settings, project.layout.paper).columns
    assert reopened_plan.sheets[0].back.pages[columns - 1].card.card_id == "goblin"

    output = tmp_path / "proof.pdf"
    export(reopened_plan, str(output))
    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) == 2
        assert tuple(float(v) for v in pdf.pages[0].MediaBox[2:]) == (612.0, 792.0)
