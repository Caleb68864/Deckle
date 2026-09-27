"""Pure card-deck validation, pairing resolution, and immutable edit helpers."""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from deckle.core.models import (
    CardAsset, CardDeck, CardEntry, CardFace, CardSettings, Project, SourcePage, SourceRef,
)

MAX_ACTIVE_COPIES = 10_000
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _finite_number(name: str, value: object, *, minimum: float | None = None,
                   strictly_positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if strictly_positive and value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")


def _validate_face(face: CardFace, asset_ids: set[str], subject: str) -> None:
    if not isinstance(face, CardFace) or not isinstance(face.asset_id, str) or not face.asset_id:
        raise ValueError(f"{subject} must reference a card image")
    if face.asset_id not in asset_ids:
        raise ValueError(f"{subject} references missing image {face.asset_id!r}")
    if type(face.rotate_deg) is not int or face.rotate_deg not in (0, 90, 180, 270):
        raise ValueError(f"{subject} rotation must be 0, 90, 180, or 270 degrees")


def validate_card_deck(deck: CardDeck) -> None:
    if not isinstance(deck, CardDeck):
        raise ValueError("card project is missing its card deck")
    if not isinstance(deck.assets, tuple) or not isinstance(deck.cards, tuple):
        raise ValueError("card assets and entries must be tuples")
    if deck.back_mode not in ("shared", "individual"):
        raise ValueError("back mode must be shared or individual")
    settings = deck.settings
    if not isinstance(settings, CardSettings):
        raise ValueError("card settings are missing")
    _finite_number("card width", settings.width_pt, strictly_positive=True)
    _finite_number("card height", settings.height_pt, strictly_positive=True)
    for name in ("margin_x_pt", "margin_y_pt", "gap_x_pt", "gap_y_pt", "bleed_pt", "border_width_pt"):
        _finite_number(name.replace("_pt", " "), getattr(settings, name), minimum=0)
    if settings.border_width_pt >= min(settings.width_pt, settings.height_pt) / 2:
        raise ValueError("border width must be less than half the smaller card dimension")
    if type(settings.border_enabled) is not bool or type(settings.cut_marks) is not bool:
        raise ValueError("border and cut marks settings must be true or false")
    if settings.fit not in ("contain", "cover"):
        raise ValueError("image fit must be contain or cover")

    by_id: dict[str, CardAsset] = {}
    for asset in deck.assets:
        if not isinstance(asset, CardAsset) or not isinstance(asset.id, str) or not asset.id:
            raise ValueError("each card image must have a nonempty ID")
        if asset.id in by_id:
            raise ValueError(f"duplicate card image ID {asset.id!r}")
        if not isinstance(asset.page, SourcePage) or not isinstance(asset.page.ref, SourceRef):
            raise ValueError(f"card image {asset.id!r} needs one source page")
        if not all(isinstance(v, str) and v for v in (asset.label, asset.original_path, asset.page.ref.path)):
            raise ValueError(f"card image {asset.id!r} is missing its name or source path")
        if not Path(asset.original_path).is_absolute() or not Path(asset.page.ref.path).is_absolute():
            raise ValueError(f"card image {asset.label!r} paths must be absolute")
        if not isinstance(asset.original_sha256, str) or not isinstance(asset.page.ref.sha256, str) or not _HASH.fullmatch(asset.original_sha256) or not _HASH.fullmatch(asset.page.ref.sha256):
            raise ValueError(f"card image {asset.label!r} has an invalid SHA-256")
        if asset.page.ref.page_index != 0 or asset.page.rotate_deg != 0 or asset.page.skipped:
            raise ValueError(f"card image {asset.label!r} must reference its single upright page")
        try:
            _finite_number("source page width", asset.page.ref.width_pt, strictly_positive=True)
            _finite_number("source page height", asset.page.ref.height_pt, strictly_positive=True)
        except ValueError as exc:
            raise ValueError(f"card image {asset.label!r} has invalid page dimensions: {exc}")
        by_id[asset.id] = asset
    ids = set(by_id)
    if deck.shared_back is not None:
        _validate_face(deck.shared_back, ids, "shared back")

    card_ids: set[str] = set()
    for card in deck.cards:
        if not isinstance(card, CardEntry) or not isinstance(card.id, str) or not card.id:
            raise ValueError("each card entry must have a nonempty ID")
        if card.id in card_ids:
            raise ValueError(f"duplicate card entry ID {card.id!r}")
        card_ids.add(card.id)
        _validate_face(card.front, ids, f"front of card {card.id!r}")
        if card.back is not None:
            _validate_face(card.back, ids, f"back of card {card.id!r}")
        if type(card.copies) is not int or not 1 <= card.copies <= 999:
            raise ValueError(f"copies for card {card.id!r} must be an integer from 1 to 999")
        if type(card.skipped) is not bool:
            raise ValueError(f"skip state for card {card.id!r} must be true or false")
    if active_copy_count(deck) > MAX_ACTIVE_COPIES:
        raise ValueError(f"a deck may contain at most {MAX_ACTIVE_COPIES} active card copies")


def active_copy_count(deck: CardDeck) -> int:
    return sum(card.copies for card in deck.cards if not card.skipped)


def resolve_card_back(deck: CardDeck, entry: CardEntry) -> CardFace | None:
    return deck.shared_back if deck.back_mode == "shared" else entry.back


def card_output_problems(deck: CardDeck) -> tuple[str, ...]:
    validate_card_deck(deck)
    active = [card for card in deck.cards if not card.skipped]
    if not active:
        return ("Add or unskip at least one card before printing.",)
    labels = {asset.id: asset.label for asset in deck.assets}
    missing = []
    for card in active:
        if resolve_card_back(deck, card) is None:
            missing.append(labels[card.front.asset_id])
    if missing:
        return ("Assign backs for: " + ", ".join(missing) + ".",)
    return ()


def append_fronts(deck: CardDeck, asset_ids: Sequence[str]) -> CardDeck:
    ids = {asset.id for asset in deck.assets}
    entries = deck.cards + tuple(CardEntry(uuid.uuid4().hex, CardFace(asset_id)) for asset_id in asset_ids)
    updated = replace(deck, cards=entries)
    validate_card_deck(updated)
    return updated


def set_shared_back(deck: CardDeck, face: CardFace | None) -> CardDeck:
    updated = replace(deck, shared_back=face)
    validate_card_deck(updated)
    return updated


def set_back_mode(deck: CardDeck, mode: str) -> CardDeck:
    updated = replace(deck, back_mode=mode)
    validate_card_deck(updated)
    return updated


def assign_back(deck: CardDeck, card_id: str, face: CardFace | None) -> CardDeck:
    if deck.back_mode == "shared":
        raise ValueError("switch to individual backs before assigning a back to one card")
    found = False
    cards = []
    for card in deck.cards:
        if card.id == card_id:
            cards.append(replace(card, back=face)); found = True
        else:
            cards.append(card)
    if not found:
        raise ValueError(f"unknown card {card_id!r}")
    updated = replace(deck, cards=tuple(cards)); validate_card_deck(updated); return updated


def materialize_shared_back(deck: CardDeck) -> CardDeck:
    if deck.shared_back is None:
        raise ValueError("choose a shared back before copying it to every card")
    updated = replace(deck, cards=tuple(replace(c, back=deck.shared_back) for c in deck.cards), back_mode="individual")
    validate_card_deck(updated); return updated


def reorder_cards(deck: CardDeck, ordered_ids: Sequence[str]) -> CardDeck:
    validate_card_deck(deck)
    if len(ordered_ids) != len(deck.cards) or set(ordered_ids) != {c.id for c in deck.cards}:
        raise ValueError("card order must contain every card ID exactly once")
    by_id = {c.id: c for c in deck.cards}
    updated = replace(deck, cards=tuple(by_id[cid] for cid in ordered_ids))
    validate_card_deck(updated)
    return updated


def set_card_copies(deck: CardDeck, card_id: str, copies: int) -> CardDeck:
    if not any(c.id == card_id for c in deck.cards): raise ValueError(f"unknown card {card_id!r}")
    cards = tuple(replace(c, copies=copies) if c.id == card_id else c for c in deck.cards)
    updated = replace(deck, cards=cards); validate_card_deck(updated); return updated


def set_card_skipped(deck: CardDeck, card_id: str, skipped: bool) -> CardDeck:
    if not any(c.id == card_id for c in deck.cards): raise ValueError(f"unknown card {card_id!r}")
    cards = tuple(replace(c, skipped=skipped) if c.id == card_id else c for c in deck.cards)
    updated = replace(deck, cards=cards); validate_card_deck(updated); return updated


def set_face_rotation(deck: CardDeck, card_id: str, side: str, rotate_deg: int) -> CardDeck:
    if side not in ("front", "back"):
        raise ValueError("face side must be front or back")
    if side == "back" and deck.back_mode == "shared":
        if deck.shared_back is None:
            raise ValueError("choose a shared back before rotating it")
        updated = replace(deck, shared_back=replace(deck.shared_back, rotate_deg=rotate_deg))
    else:
        found = False; entries = []
        for card in deck.cards:
            if card.id == card_id:
                face = card.front if side == "front" else card.back
                if face is None: raise ValueError(f"card {card_id!r} has no back to rotate")
                entries.append(replace(card, **{side: replace(face, rotate_deg=rotate_deg)})); found = True
            else: entries.append(card)
        if not found: raise ValueError(f"unknown card {card_id!r}")
        updated = replace(deck, cards=tuple(entries))
    validate_card_deck(updated); return updated


def remove_cards(deck: CardDeck, card_ids: Sequence[str]) -> CardDeck:
    validate_card_deck(deck)
    wanted = set(card_ids)
    if not wanted <= {c.id for c in deck.cards}: raise ValueError("cannot remove an unknown card")
    updated = replace(deck, cards=tuple(c for c in deck.cards if c.id not in wanted))
    validate_card_deck(updated)
    return updated


def project_has_content(project: Project) -> bool:
    return bool(project.card_deck.cards) if getattr(project, "kind", "book") == "cards" and getattr(project, "card_deck", None) else bool(project.pages)


def project_source_pages(project: Project) -> tuple[SourcePage, ...]:
    if getattr(project, "kind", "book") == "book": return tuple(project.pages)
    if getattr(project, "card_deck", None) is None: return ()
    result = []; seen = set()
    for asset in project.card_deck.assets:
        key = (asset.page.ref.path, asset.page.ref.page_index)
        if key not in seen: seen.add(key); result.append(asset.page)
    return tuple(result)
