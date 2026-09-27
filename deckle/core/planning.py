"""Project-kind dispatch for book and card imposition."""
from __future__ import annotations

from deckle.core.cards import project_has_content, project_source_pages
from deckle.core.models import Project, SheetPlan
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy


def impose_project(project: Project) -> SheetPlan:
    if project.kind == "cards":
        if project.pages or project.card_deck is None:
            raise ValueError("card projects must contain a card deck and no book pages")
        from deckle.core.card_layout import impose_cards
        return impose_cards(project.card_deck, project.layout.paper)
    if project.kind != "book" or project.card_deck is not None:
        raise ValueError("project kind and content do not match")
    strategy = SaddleStitchStrategy() if project.layout.fold_scheme == "folio" else GutterShiftStrategy()
    return strategy.impose(project.pages, project.layout)


__all__ = ["impose_project", "project_has_content", "project_source_pages"]
