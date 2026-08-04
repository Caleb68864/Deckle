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
    flat = []
    for sheet in plan.sheets:
        if sheet.front is not None:
            flat.append(sheet.front)
        if sheet.back is not None:
            flat.append(sheet.back)
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


def test_inner_outer_difference_is_preserved_when_not_maximising():
    """With maximize_gutter off the horizontal slack is split, so the
    requested inner/outer difference survives."""
    s = settings(gutter_pt=72.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 maximize_gutter=False)
    plan = impose(make_pages(2, size=TALL), s)
    inner, outer, _, _ = margins(plan, 0)
    assert round(inner - outer, 6) == 54.0


def test_equal_margins_centre_the_content_when_not_maximising():
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 maximize_gutter=False)
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
    p = plan.sheets[0].front.placement
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


def test_maximize_gutter_is_on_by_default():
    assert LayoutSettings(paper=LETTER, gutter_pt=0.0, binding_edge="left").maximize_gutter


def test_maximize_gutter_pushes_all_slack_to_the_spine():
    """Content sits as far from the binding as it can: the fore-edge margin
    lands on exactly its requested value and the gutter absorbs the rest."""
    s = settings(gutter_pt=18.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0,
                 maximize_gutter=True)
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
                    margin_bottom_pt=18.0, maximize_gutter=True)
    s_off = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                     margin_bottom_pt=18.0, maximize_gutter=False)
    on = margins(impose(make_pages(2, size=DIGEST), s_on), 0)
    off = margins(impose(make_pages(2, size=DIGEST), s_off), 0)
    assert on[0] > off[0]                        # inner larger when maximising
    assert on[1] < off[1]                        # outer smaller
    assert round(on[0] + on[1], 6) == round(off[0] + off[1], 6)  # total unchanged


def test_maximize_gutter_does_not_affect_the_vertical_axis():
    """Head and tail always share their slack -- neither has a binding."""
    for maximize in (True, False):
        s = settings(gutter_pt=18.0, margin_outer_pt=18.0, margin_top_pt=18.0,
                     margin_bottom_pt=18.0, maximize_gutter=maximize)
        _, _, top, bottom = margins(impose(make_pages(2, size=DIGEST), s), 0)
        assert round(top, 6) == round(bottom, 6)


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_maximize_gutter_still_mirrors_recto_and_verso(size, edge):
    s = settings(binding_edge=edge, gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=18.0, margin_bottom_pt=18.0, maximize_gutter=True)
    plan = impose(make_pages(4, size=size), s)
    for pair in (0, 2):
        assert margins(plan, pair, binding_edge=edge) == pytest.approx(
            margins(plan, pair + 1, binding_edge=edge)
        )


@pytest.mark.parametrize("size", [TRAVELLER, DIGEST, SQUARE, TALL])
def test_maximize_gutter_never_violates_the_requested_minimums(size):
    s = settings(gutter_pt=54.0, margin_outer_pt=18.0,
                 margin_top_pt=36.0, margin_bottom_pt=9.0, maximize_gutter=True)
    plan = impose(make_pages(2, size=size), s)
    for i in range(2):
        inner, outer, top, bottom = margins(plan, i)
        assert inner >= 54.0 - 1e-6
        assert outer >= 18.0 - 1e-6
        assert top >= 36.0 - 1e-6
        assert bottom >= 9.0 - 1e-6
