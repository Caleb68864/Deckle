"""Tests for deckle.core.layout -- the gutter-shift imposition engine.

Organised around **measured margins** rather than raw ``tx``/``ty``, via
``actual_margins_pt``. A coordinate assertion only checks one edge of one
page and can pass while the opposite edge is wrong -- which is exactly how
the verso gutter bug survived the original suite. Measuring all four edges
of both sides, for both binding edges, is what catches mirror defects.

The rules under test, stated once:

1. All four page edges have a margin. The gutter IS the inner (spine)
   margin; outer/top/bottom cover the rest.
2. Margins are MINIMUMS. Spare space inside the content box is shared
   equally between opposing margins, so their difference is preserved.
3. When content OVERFLOWS, there is no spare space to share: the specified
   margin is held and the overflow lands on the opposite edge. The gutter is
   never eaten by content that does not fit.
4. There is one scale rule: fit the content box in both dimensions. It
   fills the page height whenever height is the binding constraint.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from deckle.core import layout
from deckle.core.layout import GutterShiftStrategy, LayoutStrategy, actual_margins_pt
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

LETTER = (612.0, 792.0)

# Real-world page boxes, so the suite exercises aspect ratios that actually occur.
TRAVELLER = (506.88, 672.0)   # Traveller Core Rulebook -- narrower than letter
DIGEST = (432.0, 648.0)       # 6x9in trade paperback
SQUARE = (500.0, 500.0)
WIDE = (900.0, 600.0)         # landscape
TALL = (300.0, 900.0)         # very narrow


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
    """Every output page in the plan, front-then-back, sheet by sheet.

    A side holds a *tuple* of output pages -- one under gutter shift, two
    under folio -- so this flattens through ``side.pages`` rather than
    treating a side as a page itself.
    """
    flat = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is not None:
                flat.extend(side.pages)
    return flat


def margins(plan, index, paper=LETTER, binding_edge="left"):
    """Measured (inner, outer, top, bottom) of the index-th output page."""
    page = flat_output_pages(plan)[index]
    return actual_margins_pt(page, paper, is_recto=(index % 2 == 0), binding_edge=binding_edge)


def impose(pages, s):
    return GutterShiftStrategy().impose(pages, s)


# ---------------------------------------------------------------- protocol


def test_layout_strategy_is_a_protocol():
    assert issubclass(LayoutStrategy, Protocol)
    assert hasattr(LayoutStrategy, "impose")


def test_gutter_shift_strategy_implements_protocol():
    assert isinstance(GutterShiftStrategy(), LayoutStrategy)


def test_impose_is_deterministic():
    s = settings()
    pages = make_pages(4)
    a, b = impose(pages, s), impose(pages, s)
    assert a.paper_pt == b.paper_pt
    assert len(a.sheets) == len(b.sheets)


def test_impose_populates_paper_pt():
    plan = impose(make_pages(2), settings(paper=(500.0, 700.0)))
    assert plan.paper_pt == (500.0, 700.0)


# ------------------------------------------------------- rule 1: four edges


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_fit_never_violates_any_requested_margin(size, edge):
    """`fit` must never produce a margin smaller than requested, on any edge,
    for any source aspect ratio, on either binding edge."""
    s = settings(
        binding_edge=edge, gutter_pt=54.0,
        margin_outer_pt=18.0, margin_top_pt=36.0, margin_bottom_pt=9.0,
    )
    plan = impose(make_pages(4, size=size), s)
    for i in range(4):
        inner, outer, top, bottom = margins(plan, i, binding_edge=edge)
        assert inner >= 54.0 - 1e-6, f"page {i} inner {inner}"
        assert outer >= 18.0 - 1e-6, f"page {i} outer {outer}"
        assert top >= 36.0 - 1e-6, f"page {i} top {top}"
        assert bottom >= 9.0 - 1e-6, f"page {i} bottom {bottom}"


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
def test_fit_content_always_lands_on_the_page(size):
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose(make_pages(2, size=size), s)
    for i in range(2):
        for m in margins(plan, i):
            assert m >= 0.0


# ----------------------------------------------- rule 2: minimums, shared slack


def test_opposing_margin_difference_is_preserved():
    """Slack is shared equally, so top-bottom keeps its requested delta."""
    s = settings(margin_top_pt=36.0, margin_bottom_pt=9.0,
                 margin_outer_pt=18.0)
    plan = impose(make_pages(2, size=TRAVELLER), s)
    _, _, top, bottom = margins(plan, 0)
    assert round(top - bottom, 6) == 27.0


def test_split_preserves_the_inner_outer_difference():
    """slack_to="split" shares the width evenly, so the requested
    inner/outer difference survives."""
    s = settings(gutter_pt=72.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 slack_to="split")
    plan = impose(make_pages(2, size=TALL), s)
    inner, outer, _, _ = margins(plan, 0)
    assert round(inner - outer, 6) == 54.0


def test_split_with_equal_margins_centres_the_content():
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 slack_to="split")
    plan = impose(make_pages(2, size=DIGEST), s)
    inner, outer, top, bottom = margins(plan, 0)
    assert round(inner, 6) == round(outer, 6)
    assert round(top, 6) == round(bottom, 6)


# ------------------------------- rule 3: overflow only via degenerate settings


# --------------------------------------------------- rule 4: one scale rule


def test_fit_is_limited_by_whichever_dimension_binds():
    """A very tall source is height-limited; a square one is width-limited
    once the gutter eats into the width."""
    s = settings(gutter_pt=108.0, margin_outer_pt=0.0,
                 margin_top_pt=0.0, margin_bottom_pt=0.0)
    tall = impose(make_pages(2, size=TALL), s)
    _, _, top, bottom = margins(tall, 0)
    assert round(top + bottom, 4) == 0.0            # height binds: no vertical slack

    square = impose(make_pages(2, size=SQUARE), s)
    inner, outer, _, _ = margins(square, 0)
    assert round(inner, 4) == 108.0                 # width binds: no horizontal slack
    assert round(outer, 4) == 0.0


# ------------------------------------------------------------- mirror symmetry


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
def test_recto_and_verso_are_exact_mirrors(size):
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=27.0, margin_bottom_pt=9.0)
    plan = impose(make_pages(4, size=size), s)
    for pair in (0, 2):
        recto = margins(plan, pair)
        verso = margins(plan, pair + 1)
        assert recto == pytest.approx(verso), f"{size} pages {pair}/{pair+1}"


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, TALL])
def test_left_and_right_binding_are_exact_mirrors(size):
    """Measured in (inner, outer, ...) terms, both binding edges agree --
    the whole difference is which physical side 'inner' names."""
    left = impose(make_pages(2, size=size), settings(binding_edge="left"))
    right = impose(make_pages(2, size=size), settings(binding_edge="right"))
    assert margins(left, 0, binding_edge="left") == pytest.approx(
        margins(right, 0, binding_edge="right")
    )


def test_binding_edge_selects_the_physical_side():
    """Left binding puts the recto's gutter on its left; right binding on its right."""
    left = impose(make_pages(2), settings(binding_edge="left", gutter_pt=54.0,
                                          margin_outer_pt=0.0))
    right = impose(make_pages(2), settings(binding_edge="right", gutter_pt=54.0,
                                           margin_outer_pt=0.0))
    assert flat_output_pages(left)[0].placement.tx > flat_output_pages(right)[0].placement.tx


# ------------------------------------------------------------- zero-value cases


def test_zero_gutter_and_zero_margins_fill_the_sheet():
    s = settings(gutter_pt=0.0, margin_outer_pt=0.0,
                 margin_top_pt=0.0, margin_bottom_pt=0.0)
    plan = impose(make_pages(2, size=LETTER), s)
    inner, outer, top, bottom = margins(plan, 0)
    for m in (inner, outer, top, bottom):
        assert round(m, 6) == 0.0


def test_zero_gutter_still_honours_the_other_margins():
    s = settings(gutter_pt=0.0, margin_outer_pt=36.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose(make_pages(2, size=DIGEST), s)
    inner, outer, top, bottom = margins(plan, 0)
    assert inner >= 0.0
    assert outer >= 36.0 - 1e-6
    assert top >= 18.0 - 1e-6
    assert bottom >= 18.0 - 1e-6


# ------------------------------------------------------------- degenerate input


def test_margins_larger_than_the_sheet_warn_and_fall_back():
    """Rather than a negative-size box and a nonsense scale."""
    s = settings(gutter_pt=400.0, margin_outer_pt=400.0,
                 margin_top_pt=500.0, margin_bottom_pt=500.0)
    plan = impose(make_pages(2), s)
    assert any(w.kind == "clipped_by_page" for w in plan.warnings)
    p = flat_output_pages(plan)[0].placement
    assert p.scale_x > 0.0 and p.scale_y > 0.0


def test_negative_margins_are_clamped_to_zero():
    s = settings(gutter_pt=-50.0, margin_outer_pt=-10.0,
                 margin_top_pt=-10.0, margin_bottom_pt=-10.0)
    plan = impose(make_pages(2), s)
    for m in margins(plan, 0):
        assert m >= 0.0


# ------------------------------------------------------ per-page geometry (defect 1)


def test_scale_is_uniform_across_the_document():
    """One scale for every page, so body text never changes size mid-book.

    Per-page scaling would let each page fill its own box, but differing
    source widths would then be reproduced at differing scales. On the real
    Traveller book that made the cover's text 2.5% larger than the body's.
    """
    pages = [
        make_page(0, size=(400.0, 600.0)),
        make_page(1, size=(400.0, 600.0)),
        make_page(2, size=(300.0, 900.0)),
    ]
    plan = impose(pages, settings(gutter_pt=18.0))
    # Fillers are blank, so their scale is meaningless -- exclude them.
    scales = {
        round(p.placement.scale_x, 9)
        for p in flat_output_pages(plan)
        if not p.is_filler
    }
    assert len(scales) == 1, f"expected one document scale, got {scales}"


def test_filler_scale_is_not_mistaken_for_a_content_scale():
    """A filler carries a neutral 1.0 scale; it has no content to size."""
    plan = impose(make_pages(3, size=DIGEST), settings())
    fillers = [p for p in flat_output_pages(plan) if p.is_filler]
    assert len(fillers) == 1
    assert fillers[0].placement.scale_x == 1.0
    assert fillers[0].source_ref is None


def test_uniform_scale_is_the_largest_that_fits_every_page():
    from deckle.core.layout import content_box_size, document_scale

    pages = [make_page(0, size=(400.0, 600.0)), make_page(1, size=(300.0, 900.0))]
    s = settings(gutter_pt=18.0)
    box_w, box_h = content_box_size(s)
    expected = min(min(box_w / 400.0, box_h / 600.0), min(box_w / 300.0, box_h / 900.0))
    assert document_scale(pages, s) == pytest.approx(expected)


def test_each_page_still_uses_its_own_geometry():
    """Uniform *scale*, per-page *geometry*.

    The predecessor script's defect was applying page 0's aspect ratio to
    every page's geometry. That stays fixed: each page's own media box
    determines its scaled size and therefore its slack and placement -- only
    the scale factor is shared.
    """
    pages = [make_page(0, size=(400.0, 600.0)), make_page(1, size=(300.0, 600.0))]
    plan = impose(pages, settings(gutter_pt=18.0, margin_outer_pt=18.0))
    flat = flat_output_pages(plan)
    w0 = 400.0 * flat[0].placement.scale_x
    w1 = 300.0 * flat[1].placement.scale_x
    assert w0 != pytest.approx(w1), "each page keeps its own width"
    # The narrower page carries more slack, so its placement differs.
    assert margins(plan, 0)[0] != pytest.approx(margins(plan, 1)[0])


def test_mixed_widths_share_one_scale_and_keep_their_minimums():
    """The real Traveller distribution: 264 pages at 519.36pt, a 506.88pt
    cover, one 527.28pt page. All three render at one scale, and every page
    still honours the requested minimums."""
    pages = [
        make_page(0, size=(506.88, 672.0)),
        make_page(1, size=(519.36, 672.0)),
        make_page(2, size=(527.28, 672.0)),
        make_page(3, size=(519.36, 672.0)),
    ]
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose(pages, s)
    scales = {round(p.placement.scale_x, 9) for p in flat_output_pages(plan)}
    assert len(scales) == 1, "text must be the same size on every page"
    for i in range(4):
        inner, outer, top, bottom = margins(plan, i)
        assert inner >= 54.0 - 1e-6
        assert outer >= 18.0 - 1e-6
        assert top >= 18.0 - 1e-6
        assert bottom >= 18.0 - 1e-6


def test_widest_page_binds_the_document_scale():
    """The most constraining page sets the scale, so nothing overflows."""
    narrow = make_page(0, size=(400.0, 672.0))
    wide = make_page(1, size=(560.0, 672.0))
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0)
    plan = impose([narrow, wide], s)
    for i in range(2):
        for m in margins(plan, i):
            assert m >= 0.0


# --------------------------------------------------------------- parity (defect 2)


def test_seven_pages_pad_to_eight_with_exactly_one_filler():
    plan = impose(make_pages(7), settings(start_on_recto=True))
    flat = flat_output_pages(plan)
    assert len(flat) == 8
    assert len([p for p in flat if p.is_filler]) == 1


def test_starting_on_a_verso_pushes_the_first_page_off_index_zero():
    """``start_on_recto=False`` was accepted, written into the ``.deckle``
    file, and then ignored -- the imposer read the field into ``_`` and
    moved on. Output index 0 is a recto, so landing content on a verso
    takes one leading filler."""
    plan = impose(make_pages(4), settings(start_on_recto=False))
    flat = flat_output_pages(plan)

    assert flat[0].is_filler, "the first content page is still on a recto"
    assert not flat[1].is_filler
    assert len([p for p in flat if p.is_filler]) == 2, (
        "4 content pages behind one filler is 5 slots, padded to 6"
    )


def test_starting_on_a_recto_costs_nothing():
    """The default must not gain a filler it never needed."""
    plan = impose(make_pages(4), settings(start_on_recto=True))

    assert len([p for p in flat_output_pages(plan) if p.is_filler]) == 0


def test_an_empty_document_gains_no_leading_filler():
    """A leading blank is a position for content. With no content there is
    nothing to position, and a one-sheet 'document' of pure filler is not
    an empty document."""
    assert impose([], settings(start_on_recto=False)).sheets == []


def test_even_page_counts_gain_no_filler():
    for n in (2, 4, 6, 8):
        plan = impose(make_pages(n), settings())
        assert len([p for p in flat_output_pages(plan) if p.is_filler]) == 0


def test_filler_pages_carry_no_source_ref():
    plan = impose(make_pages(7), settings())
    for filler in (p for p in flat_output_pages(plan) if p.is_filler):
        assert filler.is_filler is True
        assert filler.source_ref is None


# ------------------------------------------------------------------- landscape


def test_landscape_page_is_rotated_and_warned():
    plan = impose([make_page(0, size=WIDE), make_page(1, size=DIGEST)],
                  settings(landscape_policy="rotate"))
    assert flat_output_pages(plan)[0].placement.rotate_deg == 90
    assert any(w.kind == "mixed_orientation" for w in plan.warnings)


def test_rotated_landscape_still_honours_margins():
    s = settings(landscape_policy="rotate", gutter_pt=54.0,
                 margin_outer_pt=18.0, margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose([make_page(0, size=WIDE), make_page(1, size=WIDE)], s)
    inner, outer, top, bottom = margins(plan, 0)
    assert inner >= 54.0 - 1e-6
    assert outer >= 18.0 - 1e-6
    assert top >= 18.0 - 1e-6
    assert bottom >= 18.0 - 1e-6


# ------------------------------------------------------------------- purity


def test_impose_performs_no_file_io(monkeypatch):
    import builtins

    calls = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (calls.append(a), real_open(*a, **k))[1])
    impose(make_pages(4, size=TRAVELLER), settings())
    assert calls == []


# ------------------------------------------------------- content box geometry


def test_content_box_mirrors_between_recto_and_verso():
    from deckle.core.layout import content_box_rect_pt

    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    recto = content_box_rect_pt(s, is_recto=True)
    verso = content_box_rect_pt(s, is_recto=False)
    assert recto == (54.0, 18.0, 594.0, 774.0)   # gutter on the left
    assert verso == (18.0, 18.0, 558.0, 774.0)   # gutter on the right
    # Same width and height, mirrored horizontally.
    assert recto[2] - recto[0] == verso[2] - verso[0]
    assert 612.0 - recto[2] == verso[0]


def test_content_box_is_not_the_imageable_area():
    """The two guides answer different questions and generally differ.

    Conflating them is what makes 'why won't my content align with the black
    lines?' a reasonable question with a geometric answer.
    """
    from deckle.app.views.preview_view import imageable_rect_pt
    from deckle.core.layout import content_box_rect_pt

    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    assert content_box_rect_pt(s, is_recto=True) != imageable_rect_pt(
        LETTER, (18.0, 18.0, 18.0, 18.0)
    )


def test_content_touches_the_content_box_on_the_binding_axis_only():
    """Content fills the box on whichever axis binds and is inset on the
    other by the aspect-ratio slack -- so it cannot touch all four edges
    unless the source aspect matches the box aspect."""
    from deckle.core.layout import content_box_rect_pt

    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose(make_pages(2, size=TRAVELLER), s)
    x0, y0, x1, y1 = content_box_rect_pt(s, is_recto=True)
    (p,) = (page.placement for page in plan.sheets[0].front.pages)
    scaled_w = TRAVELLER[0] * p.scale_x
    scaled_h = TRAVELLER[1] * p.scale_y

    assert round(scaled_w, 4) == round(x1 - x0, 4)     # fills width
    assert scaled_h < (y1 - y0) - 1e-6                 # inset vertically


def test_degenerate_margins_collapse_the_content_box_to_the_sheet():
    from deckle.core.layout import content_box_rect_pt

    s = settings(gutter_pt=400.0, margin_outer_pt=400.0,
                 margin_top_pt=500.0, margin_bottom_pt=500.0)
    assert content_box_rect_pt(s, is_recto=True) == (0.0, 0.0, 612.0, 792.0)


# ------------------------------------------------------------ maximize_gutter


def test_slack_goes_to_the_gutter_by_default():
    assert LayoutSettings(paper=LETTER, gutter_pt=0.0, binding_edge="left").slack_to == "gutter"


def test_maximize_gutter_pushes_all_slack_to_the_spine():
    """Content sits as far from the binding as it can: the fore-edge margin
    lands on exactly its requested value and the gutter absorbs the rest."""
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 slack_to="gutter")
    plan = impose(make_pages(2, size=DIGEST), s)
    inner, outer, _, _ = margins(plan, 0)
    assert round(outer, 6) == 18.0        # fore-edge exact
    assert inner > 18.0                   # gutter got everything else


def test_maximize_gutter_makes_the_gutter_a_minimum_not_an_exact_value():
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0)
    plan = impose(make_pages(2, size=DIGEST), s)
    inner, _, _, _ = margins(plan, 0)
    assert inner >= 18.0


def test_maximize_gutter_off_centres_the_content_horizontally():
    s_on = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                    margin_bottom_pt=18.0, slack_to="gutter")
    s_off = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                     margin_bottom_pt=18.0, slack_to="split")
    on = margins(impose(make_pages(2, size=DIGEST), s_on), 0)
    off = margins(impose(make_pages(2, size=DIGEST), s_off), 0)
    assert on[0] > off[0]                        # inner larger when maximising
    assert on[1] < off[1]                        # outer smaller
    assert round(on[0] + on[1], 6) == round(off[0] + off[1], 6)  # total unchanged


def test_maximize_gutter_does_not_affect_the_vertical_axis():
    """Head and tail always share their slack -- neither has a binding."""
    for slack in ("gutter", "outer", "split"):
        s = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                     margin_bottom_pt=18.0, slack_to=slack)
        _, _, top, bottom = margins(impose(make_pages(2, size=DIGEST), s), 0)
        assert round(top, 6) == round(bottom, 6)


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_maximize_gutter_still_mirrors_recto_and_verso(size, edge):
    s = settings(binding_edge=edge, gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0, slack_to="gutter")
    plan = impose(make_pages(4, size=size), s)
    for pair in (0, 2):
        assert margins(plan, pair, binding_edge=edge) == pytest.approx(
            margins(plan, pair + 1, binding_edge=edge)
        )


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
def test_maximize_gutter_never_violates_the_requested_minimums(size):
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=36.0, margin_bottom_pt=9.0, slack_to="gutter")
    plan = impose(make_pages(2, size=size), s)
    for i in range(2):
        inner, outer, top, bottom = margins(plan, i)
        assert inner >= 54.0 - 1e-6
        assert outer >= 18.0 - 1e-6
        assert top >= 36.0 - 1e-6
        assert bottom >= 9.0 - 1e-6


# ------------------------------------------------- slack_to: which margin varies


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
def test_slack_to_outer_keeps_the_gutter_exact(size):
    """The point of the `outer` option: an identical spine margin on every
    page, so a fixed punch or sewing template lines up throughout."""
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                 margin_bottom_pt=18.0, slack_to="outer")
    plan = impose(make_pages(4, size=size), s)
    for i in range(4):
        inner, outer, _, _ = margins(plan, i)
        assert round(inner, 6) == 54.0     # spine exact, every page
        assert outer >= 18.0 - 1e-6        # fore-edge absorbs the difference


def test_slack_to_outer_holds_the_gutter_across_mixed_page_widths():
    """The real Traveller distribution, where the cover is 12.5pt narrower."""
    pages = [
        make_page(0, size=(506.88, 672.0)),
        make_page(1, size=(519.36, 672.0)),
        make_page(2, size=(527.28, 672.0)),
        make_page(3, size=(519.36, 672.0)),
    ]
    s = settings(gutter_pt=54.0, margin_outer_pt=0.0, margin_top_pt=18.0,
                 margin_bottom_pt=18.0, slack_to="outer")
    plan = impose(pages, s)
    inners = {round(margins(plan, i)[0], 6) for i in range(4)}
    assert inners == {54.0}, f"gutter must be identical everywhere, got {inners}"
    outers = {round(margins(plan, i)[1], 3) for i in range(4)}
    assert len(outers) > 1, "the fore-edge should absorb the width variation"


def test_slack_to_gutter_holds_the_fore_edge_across_mixed_page_widths():
    """The default: the fore-edge is what stays constant instead."""
    pages = [
        make_page(0, size=(506.88, 672.0)),
        make_page(1, size=(519.36, 672.0)),
        make_page(2, size=(527.28, 672.0)),
        make_page(3, size=(519.36, 672.0)),
    ]
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                 margin_bottom_pt=18.0, slack_to="gutter")
    plan = impose(pages, s)
    outers = {round(margins(plan, i)[1], 6) for i in range(4)}
    assert outers == {18.0}, f"fore-edge must be identical everywhere, got {outers}"
    inners = {round(margins(plan, i)[0], 3) for i in range(4)}
    assert len(inners) > 1, "the gutter should absorb the width variation"


@pytest.mark.parametrize("target", ["gutter", "outer", "split"])
def test_every_slack_target_respects_the_requested_minimums(target):
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0, margin_top_pt=36.0,
                 margin_bottom_pt=9.0, slack_to=target)
    plan = impose(make_pages(4, size=TRAVELLER), s)
    for i in range(4):
        inner, outer, top, bottom = margins(plan, i)
        assert inner >= 54.0 - 1e-6
        assert outer >= 18.0 - 1e-6
        assert top >= 36.0 - 1e-6
        assert bottom >= 9.0 - 1e-6


@pytest.mark.parametrize("target", ["gutter", "outer", "split"])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_every_slack_target_mirrors_recto_and_verso(target, edge):
    s = settings(binding_edge=edge, gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0, slack_to=target)
    plan = impose(make_pages(4, size=TRAVELLER), s)
    for pair in (0, 2):
        assert margins(plan, pair, binding_edge=edge) == pytest.approx(
            margins(plan, pair + 1, binding_edge=edge)
        )


def test_slack_targets_conserve_total_horizontal_margin():
    """All three distribute the same total width -- they only differ in where."""
    totals = set()
    for target in ("gutter", "outer", "split"):
        s = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                     margin_bottom_pt=18.0, slack_to=target)
        inner, outer, _, _ = margins(impose(make_pages(2, size=DIGEST), s), 0)
        totals.add(round(inner + outer, 6))
    assert len(totals) == 1, f"total horizontal margin must not vary: {totals}"


# -- the extracted placement seams ---------------------------------------
#
# `_margins` and `_box_within` replaced three hand-copied versions of the
# same arithmetic in `content_box_size`, `content_box_rect_pt` and
# `_place_page`. These tests pin the two properties that made the copies
# dangerous rather than merely repetitive: the tuple ORDER, which three
# call sites unpack positionally, and the exact boundary at which the box
# collapses, which all three have to agree on or the scale pass and the
# placement pass describe different paper.


def _margin_settings(**overrides) -> LayoutSettings:
    base = dict(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left")
    base.update(overrides)
    return LayoutSettings(**base)


def test_the_margin_helper_clamps_every_negative():
    """Negative margins clamp rather than raise, and the order is fixed.

    Clamping is deliberate and `project_io._check_layout_values` says so,
    which is why nothing validates these at load time. The order --
    spine, outer, top, bottom -- is what three call sites unpack
    positionally, so it is pinned here rather than left to a docstring.
    """
    clamped = layout._margins(
        _margin_settings(gutter_pt=-50.0, margin_outer_pt=-10.0,
                         margin_top_pt=-10.0, margin_bottom_pt=-10.0)
    )
    assert clamped == (0.0, 0.0, 0.0, 0.0)

    ordered = layout._margins(
        _margin_settings(gutter_pt=54.0, margin_outer_pt=18.0,
                         margin_top_pt=36.0, margin_bottom_pt=9.0)
    )
    assert ordered == (54.0, 18.0, 36.0, 9.0)


def test_the_content_box_helper_reports_a_collapse_without_warning():
    """A collapsed box comes back usable, flagged, and silent.

    The helper returns the bare cell with zeroed margins so a caller that
    ignores the flag still gets a positive box rather than a negative one
    and a nonsense scale. It emits no warning itself: `_place_page` wants
    one, and the scale pass must be able to ask the same question without
    duplicating it.
    """
    collapsed = layout._box_within(
        _margin_settings(gutter_pt=400.0, margin_outer_pt=400.0,
                         margin_top_pt=500.0, margin_bottom_pt=500.0),
        None,
    )
    assert collapsed == ((0.0, 0.0, 0.0, 0.0), 612.0, 792.0, True)

    ordinary = layout._box_within(
        _margin_settings(gutter_pt=54.0, margin_outer_pt=18.0,
                         margin_top_pt=36.0, margin_bottom_pt=9.0),
        None,
    )
    assert ordinary[3] is False
    assert ordinary[1:3] == (612.0 - 54.0 - 18.0, 792.0 - 36.0 - 9.0)


@pytest.mark.parametrize("gutter_pt", [0.0, 593.0, 594.0, 595.0])
@pytest.mark.parametrize("margin_top_pt", [0.0, 773.0, 774.0, 775.0])
def test_the_three_box_users_agree_about_when_it_collapses(gutter_pt, margin_top_pt):
    """The property the extraction exists to guarantee.

    On portrait letter with an 18pt outer and an 18pt bottom margin, the
    box collapses at gutter >= 594 (612 - 594 - 18 == 0) and at top >= 774
    (792 - 774 - 18 == 0). The values straddle both boundaries, because
    the test is `<= 0.0` and an off-by-one there is the difference between
    a book and a stack of blank paper.

    Before the extraction each of the three had its own copy of this
    condition. They agreed, and nothing said they had to.
    """
    settings = _margin_settings(
        gutter_pt=gutter_pt, margin_outer_pt=18.0,
        margin_top_pt=margin_top_pt, margin_bottom_pt=18.0,
    )
    expected_collapse = gutter_pt >= 594.0 or margin_top_pt >= 774.0

    size_says = layout.content_box_size(settings) == settings.paper
    rect_says = layout.content_box_rect_pt(settings, is_recto=True) == (
        0.0, 0.0, 612.0, 792.0,
    )

    warnings: list = []
    layout._place_page(
        make_page(size=(400.0, 600.0)), 0, settings, 0, warnings, 1.0,
        cell=(0.0, 0.0, 612.0, 792.0),
    )
    # Filter on the detail, not the kind: `clipped_by_page` is emitted for
    # two different reasons and only one of them is this one.
    place_says = any(
        "exceed the paper size" in w.detail for w in warnings
    )

    assert size_says is expected_collapse, ("content_box_size", size_says)
    assert rect_says is expected_collapse, ("content_box_rect_pt", rect_says)
    assert place_says is expected_collapse, ("_place_page", place_says)


# -- the user's own rotation ---------------------------------------------
#
# `_place_page` read `slot.rotate_deg` through `_source_dims`, to swap the
# page's dimensions for sizing, and then emitted `rotate_deg = 0`. A page
# rotated in Arrange was therefore MEASURED as turned and DRAWN as upright:
# under the default policies it exported unrotated and shrunk to 0.77, and
# a half turn was a complete no-op. The composition rule is
# `(user + policy) % 360`, both clockwise.


def _placement(page, **overrides):
    """The single placement for `page`, imposed alone on portrait letter.

    `landscape_policy` defaults to "scale" here, NOT to the model's own
    default of "rotate": these tests isolate the user's own turn, and
    under "rotate" a page the user has turned into landscape gets a second
    90 from the policy. The two tests that want that composition ask for
    it explicitly.
    """
    overrides.setdefault("landscape_policy", "scale")
    s = _margin_settings(**overrides)
    plan = GutterShiftStrategy().impose([page], s)
    return plan.sheets[0].front.pages[0].placement


def test_a_users_rotation_reaches_the_placement():
    """The defect itself: a quarter turn asked for is a quarter turn placed."""
    placement = _placement(make_page(size=(400.0, 600.0), rotate_deg=90))
    assert placement.rotate_deg == 90


def test_a_half_turn_is_not_silently_dropped():
    """A page imported upside down is the commonest scanner mistake there is.

    It was also the case that produced no visible difference at all: 180
    was neither swapped by `_source_dims` nor emitted by `_place_page`.
    """
    placement = _placement(make_page(size=(400.0, 600.0), rotate_deg=180))
    assert placement.rotate_deg == 180


def test_the_users_rotation_composes_with_the_landscape_policy():
    """Both turns apply, and they add.

    A portrait page turned 90 by hand IS landscape, so under the rotate
    policy the imposer turns it again to fit an upright cell -- net a half
    turn. Composition, not replacement: the policy used to overwrite the
    user's answer with its own.
    """
    placement = _placement(
        make_page(size=(400.0, 600.0), rotate_deg=90), landscape_policy="rotate"
    )
    assert placement.rotate_deg == 180


def test_a_three_quarter_turn_under_the_rotate_policy_lands_upright():
    """The composition that comes back round to zero.

    A portrait page turned 270 by hand is landscape; the policy's further
    90 clockwise brings it upright again, which is the right answer and
    the one a `rotate_deg = 90` assignment could never produce.
    """
    placement = _placement(
        make_page(size=(400.0, 600.0), rotate_deg=270), landscape_policy="rotate"
    )
    assert placement.rotate_deg == 0


@pytest.mark.parametrize(
    "stored,expected",
    [(-90, 270), (450, 90), (360, 0), (720, 0), (-180, 180), (45, 0), (135, 180)],
)
def test_a_stored_rotation_outside_zero_to_360_is_normalised(stored, expected):
    """A `.deckle` is checked for *int*, not for quarter turns.

    `app.state.set_rotation` takes % 360, but a hand-edited or
    older-format project can carry -90 or 450, and the old membership test
    `in (90, 270)` treated both as upright. A 45 is not a quarter turn and
    has no correct answer, so it snaps to the nearest rather than reaching
    pikepdf's matrix or pdfium's lookup table.
    """
    placement = _placement(make_page(size=(400.0, 600.0), rotate_deg=stored))
    assert placement.rotate_deg == expected


def test_a_rotated_page_is_measured_by_its_turned_footprint():
    """Sizing and placement must use the same footprint.

    A 400x600 page turned a quarter is 600x400, which is wider than tall,
    so on portrait letter it is the WIDTH that binds the scale. This is
    what `_source_dims` was already right about, and it is why the bug
    was a mismatch rather than a total absence of rotation.
    """
    upright = _placement(make_page(size=(400.0, 600.0), rotate_deg=0))
    turned = _placement(make_page(size=(400.0, 600.0), rotate_deg=90))

    assert upright.scale_x == pytest.approx(min(612.0 / 400.0, 792.0 / 600.0))
    assert turned.scale_x == pytest.approx(min(612.0 / 600.0, 792.0 / 400.0))
