"""Independent grid and duplex-center checks for the card planner."""
import math
import random

from deckle.core.card_layout import card_grid, card_grid_summary, impose_cards
from deckle.core.models import CardAsset, CardDeck, CardEntry, CardFace, CardSettings, SourcePage, SourceRef


def _asset(asset_id):
    ref = SourceRef(f"/tmp/{asset_id}.pdf", 0, "a" * 64, 83, 127)
    return CardAsset(asset_id, asset_id, f"/tmp/{asset_id}.png", "b" * 64,
                     SourcePage(ref, 0, False))


def test_generated_grids_match_independent_capacity_and_center_oracle():
    rng = random.Random(18431)
    for _ in range(100):
        width, height = rng.uniform(45, 180), rng.uniform(45, 180)
        bleed, mark_pad = rng.choice((0, 2, 5)), rng.choice((0, 3))
        settings = CardSettings(width, height, 7, 9, rng.uniform(0, 12),
                                rng.uniform(0, 12), True, 4, bleed,
                                "contain", bool(mark_pad))
        paper = (rng.uniform(260, 900), rng.uniform(260, 900))
        cell_w = width + 2 * (bleed + mark_pad)
        cell_h = height + 2 * (bleed + mark_pad)
        usable_w = paper[0] - 14
        usable_h = paper[1] - 18
        columns = math.floor((usable_w + settings.gap_x_pt + 1e-7) /
                             (cell_w + settings.gap_x_pt))
        rows = math.floor((usable_h + settings.gap_y_pt + 1e-7) /
                          (cell_h + settings.gap_y_pt))
        if columns < 1 or rows < 1:
            try:
                card_grid(settings, paper)
            except ValueError:
                continue
            raise AssertionError("planner accepted a grid with no fitting cell")
        grid = card_grid(settings, paper)
        grid_w = columns * cell_w + (columns - 1) * settings.gap_x_pt
        grid_h = rows * cell_h + (rows - 1) * settings.gap_y_pt
        assert (grid.columns, grid.rows, grid.capacity) == (columns, rows, columns * rows)
        assert grid.origin_pt == ((paper[0] - grid_w) / 2, (paper[1] - grid_h) / 2)
        assert grid.origin_pt[0] >= settings.margin_x_pt - 1e-7
        assert grid.origin_pt[1] >= settings.margin_y_pt - 1e-7
        assert grid.origin_pt[0] + grid_w <= paper[0] - settings.margin_x_pt + 1e-7
        assert grid.origin_pt[1] + grid_h <= paper[1] - settings.margin_y_pt + 1e-7


def test_independent_duplex_center_simulator_tracks_copies_and_partial_sheets():
    assets = tuple(_asset(f"face-{i}") for i in range(8))
    cards = tuple(CardEntry(f"entry-{i}", CardFace(f"face-{i}"),
                            CardFace(f"face-{i + 1}"), 2 if i == 3 else 1)
                  for i in range(7))
    deck = CardDeck(assets, cards, CardSettings(83, 127, 11, 13, 7, 9,
                                               False, 0, 0, "contain", False),
                    "individual")
    paper = (340, 340)
    plan = impose_cards(deck, paper)
    summary = card_grid_summary(deck, paper)
    assert summary.active_copies == 8
    assert summary.sheet_count == len(plan.sheets) > 1

    # Physical long-edge turn: a point on the viewed back corresponds to
    # (paper_width - x, y) on the front sheet. This simulator does not call
    # the production duplex or coordinate helpers.
    front_positions = {}
    back_positions = {}
    for sheet_index, sheet in enumerate(plan.sheets):
        for side_name, side, positions in (("front", sheet.front, front_positions),
                                           ("back", sheet.back, back_positions)):
            for page in side.pages:
                if page.card is None:
                    continue
                x, y, width, height = page.card.trim_rect_pt
                center = (x + width / 2, y + height / 2)
                key = (sheet_index, page.card.card_id, page.card.copy_index)
                if side_name == "back":
                    center = (paper[0] - center[0], center[1])
                assert key not in positions
                positions[key] = center
    assert front_positions.keys() == back_positions.keys()
    for key, front_center in front_positions.items():
        back_center = back_positions[key]
        assert abs(front_center[0] - back_center[0]) < 1e-7
        assert abs(front_center[1] - back_center[1]) < 1e-7
