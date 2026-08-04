"""Tests for deckle.core.layout: the gutter-shift imposition engine."""

from __future__ import annotations

from typing import Protocol

import pytest

from deckle.core.layout import GutterShiftStrategy, LayoutStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

LETTER = (612.0, 792.0)


def make_page(
    page_index: int = 0,
    width_pt: float = 612.0,
    height_pt: float = 792.0,
    path: str = "input.pdf",
    rotate_deg: int = 0,
    skipped: bool = False,
) -> SourcePage:
    ref = SourceRef(
        path=path,
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )
    return SourcePage(ref=ref, rotate_deg=rotate_deg, skipped=skipped)


def make_pages(n: int, **kwargs) -> list[SourcePage]:
    return [make_page(page_index=i, **kwargs) for i in range(n)]


def flat_output_pages(plan):
    """Flatten a SheetPlan's sheets back into reading-order output pages."""
    flat = []
    for sheet in plan.sheets:
        if sheet.front is not None:
            flat.append(sheet.front)
        if sheet.back is not None:
            flat.append(sheet.back)
    return flat


def test_layout_strategy_is_a_protocol():
    assert issubclass(LayoutStrategy, Protocol)
    assert hasattr(LayoutStrategy, "impose")


def test_gutter_shift_strategy_implements_protocol():
    strategy = GutterShiftStrategy()
    assert isinstance(strategy, LayoutStrategy)


def test_impose_is_free_of_io_side_effects():
    # Purity smoke check: calling impose twice with equal (but distinct)
    # inputs must be deterministic and touch nothing beyond its arguments.
    settings = LayoutSettings(paper=LETTER, gutter_pt=54.0, binding_edge="left")
    pages = make_pages(4)
    strategy = GutterShiftStrategy()
    plan_a = strategy.impose(pages, settings)
    plan_b = strategy.impose(pages, settings)
    assert plan_a.paper_pt == plan_b.paper_pt
    assert len(plan_a.sheets) == len(plan_b.sheets)


def test_fixed_gutter_left_binding_recto_and_verso_tx():
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=54.0,
        binding_edge="left",
        scale_mode="fixed_gutter",
    )
    pages = make_pages(4)
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)

    for i, out_page in enumerate(flat):
        if i % 2 == 0:  # recto
            assert out_page.placement.tx == 54.0
        else:  # verso
            assert out_page.placement.tx == 0.0


def test_sign_inversion_guard_recto_further_from_binding_edge_left():
    """A recto's content sits further from the (left) binding edge than a verso's."""
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=54.0,
        binding_edge="left",
        scale_mode="fixed_gutter",
    )
    pages = make_pages(2)
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)
    recto, verso = flat[0], flat[1]

    # Binding edge is the page's left edge (x=0): distance is simply tx.
    recto_distance = recto.placement.tx
    verso_distance = verso.placement.tx
    assert recto_distance > verso_distance


def test_binding_edge_right_is_mirror_of_left():
    settings_left = LayoutSettings(
        paper=LETTER,
        gutter_pt=54.0,
        binding_edge="left",
        scale_mode="fixed_gutter",
    )
    settings_right = LayoutSettings(
        paper=LETTER,
        gutter_pt=54.0,
        binding_edge="right",
        scale_mode="fixed_gutter",
    )
    pages = make_pages(2)
    plan_left = GutterShiftStrategy().impose(pages, settings_left)
    plan_right = GutterShiftStrategy().impose(pages, settings_right)

    flat_left = flat_output_pages(plan_left)
    flat_right = flat_output_pages(plan_right)

    # Rectos shifted left (tx=0), versos flush right (tx=gutter).
    assert flat_right[0].placement.tx == 0.0
    assert flat_right[1].placement.tx == 54.0

    # Exact mirror of left binding's assignment.
    assert flat_left[0].placement.tx == 54.0
    assert flat_left[1].placement.tx == 0.0


def test_sign_inversion_guard_recto_further_from_binding_edge_right():
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=54.0,
        binding_edge="right",
        scale_mode="fixed_gutter",
    )
    pages = make_pages(2)
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)
    recto, verso = flat[0], flat[1]

    paper_w = settings.paper[0]
    recto_right_edge = recto.placement.tx + recto.placement.scale_x * paper_w
    verso_right_edge = verso.placement.tx + verso.placement.scale_x * paper_w
    recto_distance = paper_w - recto_right_edge
    verso_distance = paper_w - verso_right_edge
    assert recto_distance > verso_distance


def test_impose_populates_paper_pt_from_settings():
    settings = LayoutSettings(paper=(500.0, 700.0), gutter_pt=18.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(make_pages(2), settings)
    assert plan.paper_pt == (500.0, 700.0)


def test_fit_height_scales_to_page_height_and_derives_gutter():
    src_w, src_h = 400.0, 600.0
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=18.0,
        binding_edge="left",
        scale_mode="fit_height",
    )
    pages = [make_page(width_pt=src_w, height_pt=src_h)]
    plan = GutterShiftStrategy().impose(pages, settings)
    out_page = plan.sheets[0].front

    expected_scale = LETTER[1] / src_h
    source_aspect = src_w / src_h
    expected_gutter = LETTER[0] - (LETTER[1] * source_aspect)

    assert out_page.placement.scale_y == pytest.approx(expected_scale)
    scaled_w = src_w * out_page.placement.scale_x
    actual_gutter = LETTER[0] - scaled_w
    assert actual_gutter == pytest.approx(expected_gutter)


def test_defect_1_regression_each_page_uses_its_own_media_box():
    pages = [
        make_page(page_index=0, width_pt=400.0, height_pt=600.0),
        make_page(page_index=1, width_pt=400.0, height_pt=600.0),
        make_page(page_index=2, width_pt=300.0, height_pt=900.0),
    ]
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=18.0,
        binding_edge="left",
        scale_mode="fit_height",
    )
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)

    page1_scale = flat[0].placement.scale_x
    page3_scale = flat[2].placement.scale_x

    expected_page3_scale = LETTER[1] / 900.0
    assert page3_scale == pytest.approx(expected_page3_scale)
    assert page3_scale != pytest.approx(page1_scale)


def test_defect_2_regression_seven_pages_pads_to_exactly_eight_with_one_filler():
    pages = make_pages(7)
    settings = LayoutSettings(
        paper=LETTER,
        gutter_pt=18.0,
        binding_edge="left",
        start_on_recto=True,
    )
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)

    assert len(flat) == 8
    fillers = [p for p in flat if p.is_filler]
    assert len(fillers) == 1


def test_filler_pages_have_is_filler_true_and_no_source_ref():
    pages = make_pages(7)
    settings = LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(pages, settings)
    flat = flat_output_pages(plan)

    fillers = [p for p in flat if p.is_filler]
    assert len(fillers) == 1
    for filler in fillers:
        assert filler.is_filler is True
        assert filler.source_ref is None


def test_landscape_page_in_portrait_document_rotates_and_warns():
    pages = [make_page(width_pt=792.0, height_pt=612.0)]  # landscape source
    settings = LayoutSettings(
        paper=LETTER,  # portrait paper
        gutter_pt=18.0,
        binding_edge="left",
        landscape_policy="rotate",
    )
    plan = GutterShiftStrategy().impose(pages, settings)
    out_page = plan.sheets[0].front

    assert out_page.placement.rotate_deg == 90
    assert any(w.kind == "mixed_orientation" for w in plan.warnings)


def _tall_source(n: int = 2):
    """A 6x9in source -- taller than letter's aspect, so fixed_gutter is
    height-constrained and the scaled width is narrower than paper - gutter."""
    return [
        SourcePage(
            ref=SourceRef(
                path="tall.pdf", page_index=i, sha256="x" * 64,
                width_pt=432.0, height_pt=648.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _margins(plan, paper_w: float):
    """(left, right) margin of each output page, in order."""
    out = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None or side.is_filler:
                continue
            p = side.placement
            scaled_w = 432.0 * p.scale_x
            out.append((round(p.tx, 1), round(paper_w - p.tx - scaled_w, 1)))
    return out


def test_fixed_gutter_verso_mirrors_recto_when_height_constrained():
    """Regression: the reserved gutter must stay on the binding edge.

    With a 6x9in source on letter and a 0.75in gutter, fixed_gutter is
    height-constrained, so the scaled content is narrower than
    paper - gutter. The verso previously got tx=0.0, which put the slack on
    the spine side and the content flush against the fore-edge -- the
    binding edge appeared to flip when switching modes.
    """
    settings = LayoutSettings(
        paper=(612.0, 792.0),
        gutter_pt=54.0,
        binding_edge="left",
        scale_mode="fixed_gutter",
        start_on_recto=True,
        landscape_policy="rotate",
    )
    plan = GutterShiftStrategy().impose(_tall_source(2), settings)
    recto, verso = _margins(plan, 612.0)

    # Recto: gutter on the left, slack on the fore-edge.
    assert recto == (54.0, 30.0)
    # Verso: exact mirror -- gutter on the RIGHT (the spine), slack on the left.
    assert verso == (30.0, 54.0)
    assert recto == verso[::-1]


def test_fixed_gutter_right_binding_is_mirror_of_left_when_height_constrained():
    base = dict(
        paper=(612.0, 792.0),
        gutter_pt=54.0,
        scale_mode="fixed_gutter",
        start_on_recto=True,
        landscape_policy="rotate",
    )
    left = GutterShiftStrategy().impose(
        _tall_source(2), LayoutSettings(binding_edge="left", **base)
    )
    right = GutterShiftStrategy().impose(
        _tall_source(2), LayoutSettings(binding_edge="right", **base)
    )
    lm, rm = _margins(left, 612.0), _margins(right, 612.0)
    assert lm == [m[::-1] for m in rm]


def test_fit_height_honours_the_gutter_instead_of_absorbing_the_slack():
    """Regression: the gutter is what you set, not whatever is left over.

    fit_height previously derived ``gutter = paper_w - scaled_w``, so the
    gutter silently swallowed every bit of spare width and ignored
    ``gutter_pt`` entirely -- asking for 0.75in on this source produced
    1.17in. Now the gutter is exact and the slack lands on the fore-edge.
    """
    settings = LayoutSettings(
        paper=(612.0, 792.0),
        gutter_pt=54.0,
        binding_edge="left",
        scale_mode="fit_height",
        start_on_recto=True,
        landscape_policy="rotate",
    )
    plan = GutterShiftStrategy().impose(_tall_source(2), settings)
    recto, verso = _margins(plan, 612.0)
    assert recto == (54.0, 30.0)   # gutter exact; slack to the fore-edge
    assert verso == (30.0, 54.0)   # mirrored


def test_fit_height_overflow_is_reported_not_silently_absorbed():
    """Content too wide for the gutter overflows and is flagged.

    The Traveller page box scaled to full letter height is ~597pt wide; with
    a 54pt gutter it needs 651pt on a 612pt sheet. The honest outcome is a
    negative fore-edge plus a clipping warning -- not a quietly shrunken
    gutter that hides the conflict.
    """
    plan = GutterShiftStrategy().impose(
        _traveller_like(),
        _settings(margin_top_pt=18.0, margin_bottom_pt=18.0, margin_outer_pt=18.0),
    )
    p = plan.sheets[0].front.placement
    scaled_w = 506.88 * p.scale_x
    assert round(p.tx, 1) == 54.0                    # gutter still exact
    assert 612.0 - p.tx - scaled_w < 0               # overflows the fore-edge


def _traveller_like(n: int = 2):
    """506.88 x 672pt -- the real Traveller Core Rulebook page box, which
    scaled to letter height leaves ~0pt head/tail margin."""
    return [
        SourcePage(
            ref=SourceRef(
                path="tr.pdf", page_index=i, sha256="x" * 64,
                width_pt=506.88, height_pt=672.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _settings(**kw):
    base = dict(
        paper=(612.0, 792.0), gutter_pt=54.0, binding_edge="left",
        scale_mode="fit_height", start_on_recto=True, landscape_policy="rotate",
    )
    base.update(kw)
    return LayoutSettings(**base)


def test_zero_margin_leaves_content_flush_to_the_page_edge():
    """The original behaviour, preserved: margin defaults to 0."""
    plan = GutterShiftStrategy().impose(_traveller_like(), _settings(margin_top_pt=0.0, margin_bottom_pt=0.0, margin_outer_pt=0.0))
    p = plan.sheets[0].front.placement
    assert round(p.ty, 3) == 0.0
    assert round(672.0 * p.scale_y, 1) == 792.0  # full page height


def test_all_four_margins_are_honoured_exactly_in_fixed_gutter():
    """Gutter (spine), fore-edge, head and tail are all respected.

    fixed_gutter fits the whole content box, so nothing overflows and every
    edge lands on exactly the value set -- except head/tail, which share the
    vertical slack evenly because the content is narrower-limited here.
    """
    plan = GutterShiftStrategy().impose(
        _traveller_like(),
        _settings(
            scale_mode="fixed_gutter",
            margin_top_pt=18.0, margin_bottom_pt=18.0, margin_outer_pt=18.0,
        ),
    )
    p = plan.sheets[0].front.placement
    scaled_w, scaled_h = 506.88 * p.scale_x, 672.0 * p.scale_y
    assert round(p.tx, 1) == 54.0                          # gutter, exact
    assert round(612.0 - p.tx - scaled_w, 1) == 18.0       # fore-edge, exact
    assert round(p.ty, 1) >= 18.0                          # tail, at least
    assert round(792.0 - p.ty - scaled_h, 1) >= 18.0       # head, at least


def test_margins_can_differ_per_side():
    plan = GutterShiftStrategy().impose(
        _traveller_like(),
        _settings(
            scale_mode="fixed_gutter",
            margin_top_pt=36.0, margin_bottom_pt=9.0, margin_outer_pt=18.0,
        ),
    )
    p = plan.sheets[0].front.placement
    scaled_h = 672.0 * p.scale_y
    tail = p.ty
    head = 792.0 - p.ty - scaled_h
    # Both are honoured as minimums; when width limits the scale the leftover
    # height is shared evenly, so the *difference* is the invariant.
    assert tail >= 9.0
    assert head >= 36.0
    assert round(head - tail, 1) == 27.0                   # 36 - 9


def test_equal_top_and_bottom_centre_the_content():
    plan = GutterShiftStrategy().impose(
        _traveller_like(),
        _settings(
            scale_mode="fixed_gutter",
            margin_top_pt=18.0, margin_bottom_pt=18.0, margin_outer_pt=18.0,
        ),
    )
    p = plan.sheets[0].front.placement
    scaled_h = 672.0 * p.scale_y
    assert round(p.ty, 1) == round(792.0 - p.ty - scaled_h, 1)


def test_fit_height_fits_the_margin_box_not_the_paper():
    """A margin must actually shrink the content, not be averaged away."""
    no_margin = GutterShiftStrategy().impose(_traveller_like(), _settings(margin_top_pt=0.0, margin_bottom_pt=0.0, margin_outer_pt=0.0))
    margined = GutterShiftStrategy().impose(_traveller_like(), _settings(margin_top_pt=18.0, margin_bottom_pt=18.0, margin_outer_pt=18.0))
    assert margined.sheets[0].front.placement.scale_y < no_margin.sheets[0].front.placement.scale_y


def test_margin_preserves_the_recto_verso_mirror():
    plan = GutterShiftStrategy().impose(
        _traveller_like(2), _settings(scale_mode="fixed_gutter", margin_top_pt=18.0, margin_bottom_pt=18.0, margin_outer_pt=18.0)
    )
    sheet = plan.sheets[0]
    recto, verso = sheet.front.placement, sheet.back.placement
    rw = 506.88 * recto.scale_x
    assert round(recto.tx, 1) == 54.0
    assert round(612.0 - verso.tx - rw, 1) == 54.0     # gutter on the far side
    assert round(recto.ty, 1) == round(verso.ty, 1)


# --- Unit conversion and printer-margin helpers (LayoutPanel, Qt-free part) ---


def test_length_unit_round_trip_is_lossless():
    from deckle.app.views.layout_panel import from_points, to_points

    for unit, pts in (("pt", 54.0), ("in", 54.0), ("cm", 54.0), ("mm", 54.0)):
        assert round(to_points(from_points(pts, unit), unit), 9) == pts


def test_known_unit_conversions():
    from deckle.app.views.layout_panel import from_points, to_points

    assert round(to_points(0.75, "in"), 4) == 54.0
    assert round(to_points(1.0, "cm"), 4) == 28.3465
    assert round(from_points(54.0, "cm"), 3) == 1.905
    assert to_points(18.0, "pt") == 18.0


def test_imageable_inset_reads_margins_not_a_rect():
    """Regression: imageable_area_pt is (left, top, right, bottom) MARGINS.

    Reading it as an (x0, y0, x1, y1) rect produced a ~600pt inset, which
    the margin spinbox then clamped to its 216pt cap -- a 3in margin from a
    0.25in printer border.
    """
    from deckle.app.views.layout_panel import imageable_inset_pt

    assert imageable_inset_pt((18.0, 18.0, 18.0, 18.0)) == 18.0
    assert imageable_inset_pt((12.2, 12.2, 12.2, 12.2)) == 12.2
    assert imageable_inset_pt((10.0, 20.0, 15.0, 12.0)) == 20.0
