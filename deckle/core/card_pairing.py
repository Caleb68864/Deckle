"""Explicit proposals for pairing fronts and backs by name or natural order."""
from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import natsort

from deckle.core.cards import validate_card_deck
from deckle.core.models import CardAsset, CardDeck, CardFace


@dataclass(frozen=True)
class PairingProposal:
    matches: tuple[tuple[str, str], ...]
    unmatched_front_ids: tuple[str, ...]
    unused_back_ids: tuple[str, ...]
    ambiguous_keys: tuple[str, ...]


def _key(label: str) -> str:
    stem = unicodedata.normalize("NFC", Path(label).stem).casefold()
    for suffix in ("_front", "-front", "_back", "-back"):
        if stem.endswith(suffix):
            return stem[:-len(suffix)]
    return stem


def _natural(values: Sequence[CardAsset]) -> list[CardAsset]:
    return sorted(values, key=lambda a: (natsort.natsort_key(a.label), a.label, a.id))


def suggest_card_pairs(fronts: Sequence[CardAsset], backs: Sequence[CardAsset], method: str) -> PairingProposal:
    fronts, backs = _natural(fronts), _natural(backs)
    if method == "order":
        count = min(len(fronts), len(backs))
        return PairingProposal(tuple((fronts[i].id, backs[i].id) for i in range(count)),
                               tuple(x.id for x in fronts[count:]), tuple(x.id for x in backs[count:]), ())
    if method != "filename":
        raise ValueError("pairing method must be filename or order")
    fgroups: dict[str, list[CardAsset]] = defaultdict(list)
    bgroups: dict[str, list[CardAsset]] = defaultdict(list)
    for item in fronts: fgroups[_key(item.label)].append(item)
    for item in backs: bgroups[_key(item.label)].append(item)
    matches = []; unmatched = []; ambiguous = []
    matched_backs = set()
    for key, items in fgroups.items():
        candidates = bgroups.get(key, [])
        if len(items) == 1 and len(candidates) == 1:
            matches.append((items[0].id, candidates[0].id)); matched_backs.add(candidates[0].id)
        else:
            unmatched.extend(item.id for item in items)
            if candidates and (len(items) > 1 or len(candidates) > 1): ambiguous.append(key)
    unused = [item.id for item in backs if item.id not in matched_backs]
    return PairingProposal(tuple(matches), tuple(unmatched), tuple(unused), tuple(sorted(ambiguous)))


def apply_pairing(deck: CardDeck, proposal: PairingProposal, *, replace_existing: bool = False) -> CardDeck:
    validate_card_deck(deck)
    if deck.back_mode != "individual":
        raise ValueError("switch to individual backs before applying individual pairings")
    match_map = dict(proposal.matches)
    if len(match_map) != len(proposal.matches): raise ValueError("pairing proposal repeats a front")
    asset_ids = {a.id for a in deck.assets}
    if any(f not in asset_ids or b not in asset_ids for f, b in proposal.matches):
        raise ValueError("pairing proposal references an unknown image")
    cards = []
    for card in deck.cards:
        back_id = match_map.get(card.front.asset_id)
        if back_id is None or (card.back is not None and not replace_existing):
            cards.append(card)
        else:
            cards.append(replace(card, back=CardFace(back_id)))
    updated = replace(deck, cards=tuple(cards))
    validate_card_deck(updated)
    return updated
