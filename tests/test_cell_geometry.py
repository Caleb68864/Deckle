"""SS-07 acceptance tests: the placement math generalised to a ``Cell``.

Every one of the four public placement functions -- ``content_box_size``,
``document_scale``, ``content_box_rect_pt``, ``actual_margins_pt`` -- plus
the private ``_place_page`` gained a keyword ``cell`` meaning "the box this
leaf is placed into", defaulting to the whole sheet. Two properties have to
hold simultaneously, and this file asserts both:

1. **A full-sheet cell changes nothing.** Passing ``(0, 0, paper_w,
   paper_h)`` explicitly, or omitting ``cell`` entirely, must produce
   identical results -- and identical to the pre-generalisation tree.
   ``test_gutter_shift_placements_are_byte_identical_to_the_pin`` holds the
   literal numbers ``GutterShiftStrategy`` emitted, so drift is caught by
   a value rather than by reading a diff.

2. **A non-full-sheet cell measures margins inside the cell.** The gutter of
   a leaf in the right-hand folio cell is measured against ``x0 = 396``, not
   against the sheet edge at ``0``.

Organised around **measured margins** via ``actual_margins_pt``, never raw
``tx``/``ty``, per ``docs/decisions.md`` *Rebuilt the placement math on one
rule for both axes*: a coordinate assertion on one edge of one page is how a
verso-only gutter bug survived the original suite. The one deliberate
exception is the placement pin, where the literal coordinates ARE the
subject of the test.
"""

from __future__ import annotations

import pytest

from deckle.core.layout import (
    GutterShiftStrategy,
    _place_page,
    actual_margins_pt,
    content_box_rect_pt,
    content_box_size,
    document_scale,
)
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

LETTER = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)

TRAVELLER = (506.88, 672.0)   # Traveller Core Rulebook -- narrower than letter
DIGEST = (432.0, 648.0)       # 6x9in trade paperback
WIDE = (900.0, 600.0)         # landscape


def make_page(page_index=0, size=LETTER, path="input.pdf", rotate_deg=0, skipped=False):
    w, h = size
    return SourcePage(
        ref=SourceRef(path=path, page_index=page_index, sha256="a" * 64, width_pt=w, height_pt=h),
        rotate_deg=rotate_deg,
        skipped=skipped,
    )


def make_pages(n, size=LETTER, **kw):
    return [make_page(page_index=i, size=size, **kw) for i in range(n)]


def settings(**kw):
    base = dict(paper=LETTER, gutter_pt=54.0, binding_edge="left")
    base.update(kw)
    return LayoutSettings(**base)


def flat_output_pages(plan):
    """Every output page in the plan, front-then-back, sheet by sheet."""
    flat = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is not None:
                flat.extend(side.pages)
    return flat


def margins(page, paper, *, is_recto=None, binding_edge="left", spine_side=None, cell=None):
    """Measured ``(inner, outer, top, bottom)`` -- the only way this file
    inspects a placement outside the pin test."""
    return actual_margins_pt(
        page,
        paper,
        is_recto=is_recto,
        binding_edge=binding_edge,
        spine_side=spine_side,
        cell=cell,
    )


# ------------------------------------------------------- the placement pin


GUTTER_SHIFT_PLACEMENT_PIN = {
    # (page size, binding_edge, slack_to):
    #     [(scale_x, scale_y, tx, ty, rotate_deg, is_filler), ...]  in
    #     front-then-back sheet order, for make_pages(3, size=...).
    #
    # DIGEST (432 x 648) on portrait letter is HEIGHT-bound: scaled width is
    # 504pt inside a 540pt box, so there are 36pt of horizontal slack for
    # slack_to to distribute. That is what makes the three slack_to rows
    # differ from each other rather than collapsing to one value.
    ("DIGEST", "left", "gutter"): [
        (1.166667, 1.166667, 90.0, 18.0, 0, False),
        (1.166667, 1.166667, 18.0, 18.0, 0, False),
        (1.166667, 1.166667, 90.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("DIGEST", "left", "outer"): [
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("DIGEST", "left", "split"): [
        (1.166667, 1.166667, 72.0, 18.0, 0, False),
        (1.166667, 1.166667, 36.0, 18.0, 0, False),
        (1.166667, 1.166667, 72.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("DIGEST", "right", "gutter"): [
        (1.166667, 1.166667, 18.0, 18.0, 0, False),
        (1.166667, 1.166667, 90.0, 18.0, 0, False),
        (1.166667, 1.166667, 18.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("DIGEST", "right", "outer"): [
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.166667, 1.166667, 54.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("DIGEST", "right", "split"): [
        (1.166667, 1.166667, 36.0, 18.0, 0, False),
        (1.166667, 1.166667, 72.0, 18.0, 0, False),
        (1.166667, 1.166667, 36.0, 18.0, 0, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    # WIDE (900 x 600) is landscape content on portrait paper, so it also
    # pins the rotate_deg = 90 branch and the post-rotation scale.
    ("WIDE", "left", "gutter"): [
        (0.84, 0.84, 90.0, 18.0, 90, False),
        (0.84, 0.84, 18.0, 18.0, 90, False),
        (0.84, 0.84, 90.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("WIDE", "left", "outer"): [
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("WIDE", "left", "split"): [
        (0.84, 0.84, 72.0, 18.0, 90, False),
        (0.84, 0.84, 36.0, 18.0, 90, False),
        (0.84, 0.84, 72.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("WIDE", "right", "gutter"): [
        (0.84, 0.84, 18.0, 18.0, 90, False),
        (0.84, 0.84, 90.0, 18.0, 90, False),
        (0.84, 0.84, 18.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("WIDE", "right", "outer"): [
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (0.84, 0.84, 54.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
    ("WIDE", "right", "split"): [
        (0.84, 0.84, 36.0, 18.0, 90, False),
        (0.84, 0.84, 72.0, 18.0, 90, False),
        (0.84, 0.84, 36.0, 18.0, 90, False),
        (1.0, 1.0, 0.0, 0.0, 0, True),
    ],
}
"""Every ``Placement`` ``GutterShiftStrategy`` emits over a fully specified
2-page-size x 2-binding-edge x 3-``slack_to`` grid, as literal values.

REQ-009: ``GutterShiftStrategy``'s output must not move across the ``Side``
refactor and the cell generalisation. The numbers live in this file rather
than in a fixture on disk so the pin has no external dependency and fails
loudly the moment any placement number changes.

Regenerating these values is an ESCALATION, not a maintenance chore -- per
the spec's Constraints, drift here means the generalisation changed MVP
behaviour, which is the one outcome SS-07 exists to prevent.
"""

PIN_SIZES = {"DIGEST": DIGEST, "WIDE": WIDE}


def _pin_placements(size, binding_edge, slack_to):
    """``GutterShiftStrategy``'s placements for one fully specified input."""
    se = settings(
        binding_edge=binding_edge,
        slack_to=slack_to,
        margin_outer_pt=18.0,
        margin_top_pt=18.0,
        margin_bottom_pt=18.0,
    )
    plan = GutterShiftStrategy().impose(make_pages(3, size=size), se)
    rows = []
    for op in flat_output_pages(plan):
        p = op.placement
        rows.append(
            (
                round(p.scale_x, 6),
                round(p.scale_y, 6),
                round(p.tx, 6),
                round(p.ty, 6),
                p.rotate_deg,
                op.is_filler,
            )
        )
    return rows


@pytest.mark.parametrize("key", sorted(GUTTER_SHIFT_PLACEMENT_PIN))
def test_gutter_shift_placements_are_byte_identical_to_the_pin(key):
    """Literal values, deliberately: this is the regression net for REQ-009.

    Unlike every other test in this file, this one asserts raw ``tx``/``ty``
    -- because pinning the exact emitted coordinates IS the requirement. If
    it goes red, STOP and surface; do not update the numbers.
    """
    size_name, binding_edge, slack_to = key
    assert _pin_placements(PIN_SIZES[size_name], binding_edge, slack_to) == (
        GUTTER_SHIFT_PLACEMENT_PIN[key]
    )


def test_the_pin_covers_every_placement_the_strategy_emits():
    """A pin that silently stopped covering rows would pass while blind."""
    for key, rows in GUTTER_SHIFT_PLACEMENT_PIN.items():
        size_name, binding_edge, slack_to = key
        se = settings(
            binding_edge=binding_edge,
            slack_to=slack_to,
            margin_outer_pt=18.0,
            margin_top_pt=18.0,
            margin_bottom_pt=18.0,
        )
        plan = GutterShiftStrategy().impose(make_pages(3, size=PIN_SIZES[size_name]), se)
        assert len(rows) == len(flat_output_pages(plan)) == 4


# ------------------------------------------- a full-sheet cell changes nothing


def test_the_four_placement_functions_accept_a_full_sheet_cell():
    """``Cell`` exists and all four functions take ``cell`` as a keyword."""
    from deckle.core.layout import Cell  # noqa: F401  -- the alias must exist

    se = settings(margin_outer_pt=18.0, margin_top_pt=18.0, margin_bottom_pt=18.0)
    pages = make_pages(5, size=TRAVELLER)
    full = (0.0, 0.0, *LETTER)

    assert content_box_size(se, cell=full) == content_box_size(se)
    assert document_scale(pages, se, cell=full) == document_scale(pages, se)
    assert content_box_rect_pt(se, is_recto=True, cell=full) == content_box_rect_pt(
        se, is_recto=True
    )
    op = _place_page(make_page(size=TRAVELLER), 0, se, 0, [], 1.0, cell=full)
    assert margins(op, se.paper, is_recto=True, binding_edge="left", cell=full) == margins(
        op, se.paper, is_recto=True, binding_edge="left"
    )


@pytest.mark.parametrize("size", [LETTER, TRAVELLER, DIGEST, WIDE])
@pytest.mark.parametrize("binding_edge", ["left", "right"])
@pytest.mark.parametrize("slack", ["gutter", "outer", "split"])
def test_full_sheet_cell_is_identical_to_omitting_the_cell(size, binding_edge, slack):
    """REQ-020's "returns exactly what it returned before", made executable.

    An explicit ``(0, 0, paper_w, paper_h)`` and an omitted ``cell`` are the
    same box, so every one of the four functions must agree on both parities
    across the whole aspect-ratio / binding-edge / ``slack_to`` grid.
    """
    se = settings(
        binding_edge=binding_edge,
        slack_to=slack,
        margin_outer_pt=18.0,
        margin_top_pt=18.0,
        margin_bottom_pt=18.0,
    )
    pages = make_pages(5, size=size)
    full = (0.0, 0.0, *se.paper)

    assert content_box_size(se, cell=full) == content_box_size(se)
    assert document_scale(pages, se, cell=full) == document_scale(pages, se)
    for is_recto in (True, False):
        assert content_box_rect_pt(se, is_recto=is_recto, cell=full) == content_box_rect_pt(
            se, is_recto=is_recto
        )
        output_index = 0 if is_recto else 1
        with_cell = _place_page(
            make_page(size=size), output_index, se, 0, [], 1.0, cell=full
        )
        without_cell = _place_page(make_page(size=size), output_index, se, 0, [], 1.0)
        assert with_cell.placement == without_cell.placement
        assert margins(
            with_cell, se.paper, is_recto=is_recto, binding_edge=binding_edge, cell=full
        ) == margins(with_cell, se.paper, is_recto=is_recto, binding_edge=binding_edge)


# ------------------------------------------ a real cell is a smaller box


def test_document_scale_against_a_half_width_cell_is_smaller_and_still_single_valued():
    """REQ-023: the cell narrows the box the one document-wide scale fits.

    Per ``docs/decisions.md`` *Uniform document-wide scale; per-page scaling
    resized the text*, adding a cell must change WHICH box the scale is
    fitted to without making the scale per page.
    """
    se = settings()
    pages = make_pages(9, size=TRAVELLER)
    half_cell = (0.0, 0.0, 306.0, 792.0)

    full = document_scale(pages, se)
    half = document_scale(pages, se, cell=half_cell)

    assert half < full
    # The half-width cell's content box is 306 - gutter wide, not 612 -
    # gutter. Deriving the expected value from the CELL width is what makes
    # this fail if the cell were ignored, rather than merely "some smaller
    # number".
    box_w, box_h = content_box_size(se, cell=half_cell)
    assert box_w == pytest.approx(306.0 - 54.0)
    assert half == pytest.approx(min(box_w / 506.88, box_h / 672.0))

    # One scale for the WHOLE document: every page in the fixture is the
    # same size, so any subset must yield the same minimum. A per-page or
    # per-call scale would make these diverge.
    assert document_scale(pages[:3], se, cell=half_cell) == pytest.approx(half)
    assert document_scale(pages[4:], se, cell=half_cell) == pytest.approx(half)


def test_content_box_rect_is_measured_inside_the_cell_not_from_the_sheet_edge():
    """REQ-020: the right-hand folio cell of a letter-landscape sheet.

    Its spine is the fold on its LEFT edge, so the gutter is measured from
    x = 396 inward -- not from the sheet's left edge at x = 0.
    """
    se = settings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=54.0,
        margin_outer_pt=18.0,
        margin_top_pt=18.0,
        margin_bottom_pt=18.0,
    )
    right_cell = (396.0, 0.0, 792.0, 612.0)
    left_cell = (0.0, 0.0, 396.0, 612.0)

    x0, y0, x1, y1 = content_box_rect_pt(se, is_recto=True, cell=right_cell)
    assert x0 == pytest.approx(396.0 + 54.0)
    assert x1 == pytest.approx(792.0 - 18.0)
    assert (y0, y1) == pytest.approx((18.0, 612.0 - 18.0))

    # The rect is the CELL's box, not the sheet's: it is half a sheet wide.
    assert (x1 - x0) == pytest.approx(396.0 - 54.0 - 18.0)

    # The left cell mirrors it -- its spine is the fold on its RIGHT edge,
    # so the gutter lands at the fold and the fore-edge at the sheet edge.
    lx0, _, lx1, _ = content_box_rect_pt(se, spine_side="right", cell=left_cell)
    assert lx0 == pytest.approx(18.0)
    assert lx1 == pytest.approx(396.0 - 54.0)


def test_actual_margins_are_measured_against_the_cell():
    """REQ-020/REQ-025: a leaf in the right-hand cell reports its inner
    margin against ``x0 = 396``, not against ``0``.

    Measured margins only -- ``docs/decisions.md``, *Rebuilt the placement
    math on one rule for both axes*: "the old suite asserted raw ``tx``/
    ``ty`` on one edge of one page, which is how a verso-only bug survived
    it."
    """
    se = settings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=54.0,
        margin_outer_pt=18.0,
        margin_top_pt=18.0,
        margin_bottom_pt=18.0,
        slack_to="split",
    )
    right_cell = (396.0, 0.0, 792.0, 612.0)
    # output_index 1 is a VERSO, whose parity-derived spine under a left
    # binding is the opposite edge. Passing spine_side="left" anyway means
    # a placement that fell back to parity would be measurable here.
    page = _place_page(
        make_page(size=DIGEST), 1, se, 0, [], 0.7, cell=right_cell, spine_side="left"
    )

    inner, outer, top, bottom = margins(
        page, se.paper, spine_side="left", binding_edge="left", cell=right_cell
    )
    assert inner >= 54.0 - 1e-6       # the gutter is a MINIMUM, not an exact value
    assert outer >= 18.0 - 1e-6
    assert top == pytest.approx(bottom)   # vertical slack is always split

    # The content is 302.4 x 453.6pt inside the cell's 324 x 576pt box, and
    # slack_to="split" shares the 21.6pt of horizontal slack evenly, so the
    # margins are the cell's box exactly. Sized against the SHEET's 720pt
    # box instead, the slack -- and so both horizontal margins -- would be
    # an order of magnitude larger.
    assert inner == pytest.approx(54.0 + 10.8)
    assert outer == pytest.approx(18.0 + 10.8)
    assert inner + outer == pytest.approx(396.0 - 302.4)
    assert top + bottom == pytest.approx(612.0 - 453.6)

    # Measured against the SHEET instead, the inner margin would carry the
    # 396pt cell offset. This is the assertion that catches a cell-unaware
    # measurement.
    assert inner < 396.0
    sheet_inner, _, _, _ = margins(
        page, se.paper, spine_side="left", binding_edge="left"
    )
    assert sheet_inner == pytest.approx(inner + 396.0)
