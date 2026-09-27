"""Printable card grid geometry and SheetPlan construction."""
from __future__ import annotations

import math
from dataclasses import dataclass

from deckle.core.cards import MAX_ACTIVE_COPIES, active_copy_count, card_output_problems, resolve_card_back, validate_card_deck
from deckle.core.models import (
    CardDeck, CardDecoration, CardFace, Mark, OutputPage, Placement, Sheet, SheetPlan, Side,
)

_TOL = 1e-7


@dataclass(frozen=True)
class CardGrid:
    columns: int
    rows: int
    capacity: int
    origin_pt: tuple[float, float]
    cell_pt: tuple[float, float]


@dataclass(frozen=True)
class CardGridSummary:
    columns: int
    rows: int
    capacity: int
    active_copies: int
    sheet_count: int


def card_grid_summary(deck: CardDeck, paper_pt: tuple[float, float]) -> CardGridSummary:
    """Return the planner's grid dimensions and active sheet count."""
    validate_card_deck(deck)
    grid = card_grid(deck.settings, paper_pt)
    copies = active_copy_count(deck)
    return CardGridSummary(grid.columns, grid.rows, grid.capacity, copies,
                           math.ceil(copies / grid.capacity))


def card_grid(settings, paper_pt: tuple[float, float]) -> CardGrid:
    validate_card_deck(CardDeck((), (), settings))
    if len(paper_pt) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in paper_pt):
        raise ValueError("paper width and height must be finite values greater than zero")
    W, H = paper_pt
    w, h = settings.width_pt, settings.height_pt
    enabled = settings.border_enabled
    bleed = settings.bleed_pt if enabled else 0.0
    marks = 3.0 if settings.cut_marks else 0.0
    pad = bleed + marks
    cw, ch = w + 2 * pad, h + 2 * pad
    usable_w, usable_h = W - 2 * settings.margin_x_pt, H - 2 * settings.margin_y_pt
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("card margins leave no printable sheet area")
    columns = math.floor((usable_w + settings.gap_x_pt + _TOL) / (cw + settings.gap_x_pt))
    rows = math.floor((usable_h + settings.gap_y_pt + _TOL) / (ch + settings.gap_y_pt))
    if columns < 1 or rows < 1:
        raise ValueError("no card fits on this sheet with the current size, margins, border, bleed, and cut marks")
    grid_w = columns * cw + (columns - 1) * settings.gap_x_pt
    grid_h = rows * ch + (rows - 1) * settings.gap_y_pt
    return CardGrid(columns, rows, columns * rows, ((W - grid_w) / 2, (H - grid_h) / 2), (cw, ch))


def _face_page(deck: CardDeck, face: CardFace, card_id: str, copy_index: int,
               trim: tuple[float, float, float, float], settings) -> OutputPage:
    asset = next(a for a in deck.assets if a.id == face.asset_id)
    x, y, w, h = trim
    border = settings.border_width_pt if settings.border_enabled else 0.0
    bleed = settings.bleed_pt if settings.border_enabled else 0.0
    art = (x + border, y + border, w - 2 * border, h - 2 * border)
    bleed_rect = (x - bleed, y - bleed, w + 2 * bleed, h + 2 * bleed)
    sw, sh = asset.page.ref.width_pt, asset.page.ref.height_pt
    if face.rotate_deg in (90, 270): sw, sh = sh, sw
    fit_w, fit_h = art[2], art[3]
    scale = min(fit_w / sw, fit_h / sh) if settings.fit == "contain" else max(fit_w / sw, fit_h / sh)
    footprint_w, footprint_h = sw * scale, sh * scale
    # Placement tx/ty identify the post-rotation bounding box, matching export._place_output_page.
    tx = art[0] + (fit_w - footprint_w) / 2
    ty = art[1] + (fit_h - footprint_h) / 2
    decoration = CardDecoration(card_id, copy_index, trim, art, bleed_rect, settings.border_enabled)
    return OutputPage(asset.page.ref, Placement(scale, scale, tx, ty, face.rotate_deg), False, None, decoration)


def _cut_marks(trim, bleed_rect):
    x, y, w, h = trim
    bx, by, bw, bh = bleed_rect
    sep, length = 1.0, 2.0
    result = []
    for edge_x in (x, x + w):
        sign = -1 if edge_x == x else 1
        bleed_edge = bx if sign < 0 else bx + bw
        start = bleed_edge + sign * (sep + length)
        end = bleed_edge + sign * sep
        for edge_y in (y, y + h):
            result.append(Mark("cut_line", start, edge_y, end, edge_y))
    for edge_y in (y, y + h):
        sign = -1 if edge_y == y else 1
        bleed_edge = by if sign < 0 else by + bh
        start = bleed_edge + sign * (sep + length)
        end = bleed_edge + sign * sep
        for edge_x in (x, x + w):
            result.append(Mark("cut_line", edge_x, start, edge_x, end))
    return tuple(result)


def impose_cards(deck: CardDeck, paper_pt: tuple[float, float]) -> SheetPlan:
    validate_card_deck(deck)
    problems = card_output_problems(deck)
    if problems: raise ValueError(" ".join(problems))
    copies = active_copy_count(deck)
    if copies > MAX_ACTIVE_COPIES: raise ValueError(f"at most {MAX_ACTIVE_COPIES} active copies are supported")
    grid = card_grid(deck.settings, paper_pt)
    entries = [(card, copy_index) for card in deck.cards if not card.skipped for copy_index in range(card.copies)]
    W, H = paper_pt
    settings = deck.settings
    enabled_bleed = settings.bleed_pt if settings.border_enabled else 0.0
    mark_pad = 3.0 if settings.cut_marks else 0.0
    pad = enabled_bleed + mark_pad
    cw, ch = grid.cell_pt
    sheet_count = math.ceil(len(entries) / grid.capacity)
    sheets = []
    for sheet_index in range(sheet_count):
        active = entries[sheet_index * grid.capacity:(sheet_index + 1) * grid.capacity]
        fronts = [None] * grid.capacity
        backs = [None] * grid.capacity
        front_marks = []
        for local_index, (entry, copy_index) in enumerate(active):
            row, col = divmod(local_index, grid.columns)
            x = grid.origin_pt[0] + col * (cw + settings.gap_x_pt) + pad
            y = H - grid.origin_pt[1] - row * (ch + settings.gap_y_pt) - pad - settings.height_pt
            trim = (x, y, settings.width_pt, settings.height_pt)
            fronts[local_index] = _face_page(deck, entry.front, entry.id, copy_index, trim, settings)
            back_col = grid.columns - 1 - col
            back_index = row * grid.columns + back_col
            back_trim = (W - x - settings.width_pt, y, settings.width_pt, settings.height_pt)
            back_face = resolve_card_back(deck, entry)
            backs[back_index] = _face_page(deck, back_face, entry.id, copy_index, back_trim, settings)
            if settings.cut_marks:
                front_marks.extend(_cut_marks(trim, fronts[local_index].card.bleed_rect_pt))
        for i in range(grid.capacity):
            if fronts[i] is None:
                filler = OutputPage(None, Placement(1, 1, 0, 0, 0), True)
                fronts[i] = filler
            if backs[i] is None:
                backs[i] = OutputPage(None, Placement(1, 1, 0, 0, 0), True)
        sheets.append(Sheet(sheet_index,
                            Side(tuple(fronts), tuple(front_marks)),
                            Side(tuple(backs), ())))
    return SheetPlan(sheets, tuple(paper_pt), [])
