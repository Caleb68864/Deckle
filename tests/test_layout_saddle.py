"""Tests for ``deckle.core.layout.SaddleStitchStrategy`` -- folio 2-up imposition.

Organised around the same measured-margin discipline as ``test_layout.py``
(``actual_margins_pt``, never raw ``tx``), plus one property no other
strategy needs: **the fold-simulator round trip**. ``fold_reading_order`` is
derived independently of ``saddle_order`` (see ``deckle/core/signatures.py``)
by simulating the physical fold. Scattering ``impose``'s physical print-slot
output through ``fold_reading_order``'s reading positions must reconstruct
the exact source page sequence -- for every page count, sheets-per-signature
and binding edge this suite exercises. That is the single highest-value
check in this file: two independently-derived permutations that disagree
would mean a printed, sewn book comes out of order.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from deckle.core.layout import (
    GutterShiftStrategy,
    LayoutStrategy,
    SaddleStitchStrategy,
    _signature_sheet_groups,
    actual_margins_pt,
    cell_geometry,
    content_box_size,
)
from deckle.core.models import LayoutSettings, OutputPage, SourcePage, SourceRef
from deckle.core.signatures import fold_reading_order

LETTER_LANDSCAPE = (792.0, 612.0)
LETTER_PORTRAIT = (612.0, 792.0)


def make_pages(n: int, w: float = 396.0, h: float = 612.0) -> list[SourcePage]:
    pages = []
    for i in range(n):
        ref = SourceRef(
            path="book.pdf", page_index=i, sha256="a" * 64, width_pt=w, height_pt=h
        )
        pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))
    return pages


def settings(**overrides) -> LayoutSettings:
    kwargs = dict(
        paper=LETTER_LANDSCAPE,
        gutter_pt=18.0,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=4,
    )
    kwargs.update(overrides)
    return LayoutSettings(**kwargs)


def impose(pages, s):
    return SaddleStitchStrategy().impose(pages, s)


def flat_sides(plan):
    """Every ``Side`` in the plan, in physical sheet order (front, back)."""
    sides = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is not None:
                sides.append(side)
    return sides


def flat_pages(plan) -> list[OutputPage]:
    """Every ``OutputPage`` in the plan, in physical print-slot order."""
    pages = []
    for side in flat_sides(plan):
        pages.extend(side.pages)
    return pages


def reconstruct_reading_order(plan) -> list[OutputPage | None]:
    """The fold-simulator round trip: scatter print-slot pages by reading position."""
    order = fold_reading_order(plan)
    flat = flat_pages(plan)
    assert len(order) == len(flat)
    result: list[OutputPage | None] = [None] * len(order)
    for reading_pos, output_page in zip(order, flat):
        result[reading_pos] = output_page
    return result


def assert_round_trip(pages, s):
    plan = impose(pages, s)
    active = [p for p in pages if not p.skipped]
    reconstructed = reconstruct_reading_order(plan)
    for i, p in enumerate(active):
        op = reconstructed[i]
        assert op is not None and not op.is_filler, f"page {i} missing or filler"
        assert op.source_ref == p.ref, f"page {i} out of order"
    for i in range(len(active), len(reconstructed)):
        op = reconstructed[i]
        assert op is not None and op.is_filler, f"slot {i} should be a blank filler"


# ------------------------------------------------------------- protocol


def test_protocol_signature_unchanged():
    assert isinstance(SaddleStitchStrategy(), LayoutStrategy)
    assert inspect.signature(SaddleStitchStrategy.impose) == inspect.signature(
        GutterShiftStrategy.impose
    )


# ------------------------------------------------------------- the fixture


def test_266_page_fixture_signature_counts():
    plan = impose(make_pages(266), settings(sheets_per_signature=4))
    assert len(plan.signatures) == 17
    assert len(plan.sheets) == 67
    fillers = [op for op in flat_pages(plan) if op.is_filler]
    assert len(fillers) == 2
    # both fillers land in the final signature
    final_sheet_indices = set(plan.signatures[-1].sheet_indices)
    filler_sheet_indices = set()
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side and any(op.is_filler for op in side.pages):
                filler_sheet_indices.add(sheet.index)
    assert filler_sheet_indices <= final_sheet_indices


def test_every_side_has_two_pages_and_absent_side_is_none():
    plan = impose(make_pages(266), settings())
    for sheet in plan.sheets:
        assert sheet.front is not None
        assert len(sheet.front.pages) == 2
        # folio always fills 4 pages per sheet, so back is never None here
        assert sheet.back is not None
        assert len(sheet.back.pages) == 2


def test_signature_sheet_indices_contiguous_gapless_covering():
    plan = impose(make_pages(266), settings())
    covered = [i for sig in plan.signatures for i in sig.sheet_indices]
    assert covered == list(range(len(plan.sheets)))


def test_one_distinct_scale_across_the_fixture():
    plan = impose(make_pages(266), settings())
    scales = {op.placement.scale_x for op in flat_pages(plan) if not op.is_filler}
    assert len(scales) == 1


def test_filler_pages_carry_neutral_scale():
    plan = impose(make_pages(7), settings())
    fillers = [op for op in flat_pages(plan) if op.is_filler]
    assert fillers
    assert all(op.placement.scale_x == 1.0 for op in fillers)


# ------------------------------------------------------------- padding


@pytest.mark.parametrize("n", [7, 17])
def test_padding_runs_once_with_exactly_one_warning(n):
    plan = impose(make_pages(n), settings())
    padding_warnings = [w for w in plan.warnings if w.kind == "signature_padding"]
    assert len(padding_warnings) == 1
    expected_blanks = -(-n // 4) * 4 - n
    assert str(expected_blanks) in padding_warnings[0].detail
    fillers = [op for op in flat_pages(plan) if op.is_filler]
    assert len(fillers) == expected_blanks


def test_no_padding_warning_when_already_a_multiple_of_four():
    plan = impose(make_pages(8), settings())
    assert not any(w.kind == "signature_padding" for w in plan.warnings)


# ------------------------------------------------------------- spine / margins


def test_front_left_cell_inner_margin_is_on_its_right_edge():
    plan = impose(make_pages(8), settings(margin_outer_pt=10.0, margin_top_pt=5.0, margin_bottom_pt=5.0))
    sheet0 = plan.sheets[0]
    left_op, right_op = sheet0.front.pages
    left_cell, right_cell = cell_geometry(LETTER_LANDSCAPE)

    left_margins = actual_margins_pt(
        left_op, LETTER_LANDSCAPE, spine_side="right", cell=left_cell, binding_edge="left"
    )
    right_margins = actual_margins_pt(
        right_op, LETTER_LANDSCAPE, spine_side="left", cell=right_cell, binding_edge="left"
    )
    # inner margin (index 0) measured against the fold on both leaves
    assert left_margins[0] == pytest.approx(18.0, abs=1e-6)
    assert right_margins[0] == pytest.approx(18.0, abs=1e-6)


def test_back_side_spine_also_faces_the_fold():
    """The back's gutters must land at the fold too, and match the front's.

    This previously asserted only ``>= 0.0``, which every possible placement
    satisfies -- including one that put the back's gutters on the outer
    edges, which is precisely the defect the test is named for. A sheet
    whose front hinges at the fold and whose back hinges at the trimmed
    edges produces a book that will not open.
    """
    plan = impose(make_pages(8), settings(margin_outer_pt=10.0))
    sheet0 = plan.sheets[0]
    left_cell, right_cell = cell_geometry(LETTER_LANDSCAPE)
    left_op, right_op = sheet0.back.pages

    left_margins = actual_margins_pt(
        left_op, LETTER_LANDSCAPE, spine_side="right", cell=left_cell, binding_edge="left"
    )
    right_margins = actual_margins_pt(
        right_op, LETTER_LANDSCAPE, spine_side="left", cell=right_cell, binding_edge="left"
    )

    assert left_margins[0] == pytest.approx(18.0, abs=1e-6)
    assert right_margins[0] == pytest.approx(18.0, abs=1e-6)


@pytest.mark.parametrize("gutter_pt", [0.0, 18.0, 54.0])
def test_both_leaves_sit_the_same_distance_from_the_fold(gutter_pt):
    """The gutter of a folded signature is in the MIDDLE of the sheet.

    The spine is the fold, so both leaves' inner margins are measured
    outward from the centre line -- not from the sheet's outer edges, which
    is where a flat-sheet gutter lives. Asserted here in absolute sheet
    coordinates rather than through ``actual_margins_pt``, so it would catch
    a cell-geometry error that the per-cell helper and the placement code
    happened to agree on.

    Asymmetry here means one leaf's text creeps toward the fold while the
    other drifts away, and it shows up as visibly uneven inner margins the
    moment the signature is opened flat.
    """
    plan = impose(
        make_pages(8),
        settings(gutter_pt=gutter_pt, margin_outer_pt=10.0),
    )
    fold_x = LETTER_LANDSCAPE[0] / 2.0

    for side in (plan.sheets[0].front, plan.sheets[0].back):
        left_op, right_op = side.pages

        # The left leaf's RIGHT edge and the right leaf's LEFT edge are the
        # two that face the fold; those are the ones that must match.
        left_edge = (
            left_op.placement.tx
            + left_op.source_ref.width_pt * left_op.placement.scale_x
        )
        right_edge = right_op.placement.tx

        gap_left = fold_x - left_edge
        gap_right = right_edge - fold_x

        assert gap_left == pytest.approx(gap_right, abs=1e-6), (
            f"leaves are not symmetric about the fold: {gap_left:.2f} vs "
            f"{gap_right:.2f} at gutter={gutter_pt}"
        )
        # And the gap is a real one on the fold side, never negative --
        # content crossing the fold would be printed across the spine.
        assert gap_left >= gutter_pt - 1e-6


def test_a_larger_gutter_widens_the_blank_band_at_the_fold():
    """Increasing the gutter must move both leaves AWAY from the centre.

    If a sign were flipped, a bigger gutter would push the leaves together
    and eventually overlap the fold -- which still produces a valid PDF, so
    nothing else would complain.
    """
    fold_x = LETTER_LANDSCAPE[0] / 2.0

    def gap_at(gutter_pt: float) -> float:
        plan = impose(make_pages(8), settings(gutter_pt=gutter_pt, margin_outer_pt=10.0))
        left_op = plan.sheets[0].front.pages[0]
        edge = left_op.placement.tx + left_op.source_ref.width_pt * left_op.placement.scale_x
        return fold_x - edge

    assert gap_at(54.0) > gap_at(18.0) > gap_at(0.0)


def test_binding_edge_right_mirrors_which_cell_a_slot_lands_in():
    left_plan = impose(make_pages(8), settings(binding_edge="left"))
    right_plan = impose(make_pages(8), settings(binding_edge="right"))

    left_cell, right_cell = cell_geometry(LETTER_LANDSCAPE)

    def cell_of(op):
        return "left" if op.placement.tx < left_cell[2] else "right"

    left_front = left_plan.sheets[0].front.pages
    right_front = right_plan.sheets[0].front.pages

    # the slot that landed in the left cell under "left" now lands in the
    # right cell under "right", and vice versa.
    left_slot_ref = left_front[0].source_ref if cell_of(left_front[0]) == "left" else left_front[1].source_ref
    assert left_slot_ref is not None
    matching = [op for op in right_front if op.source_ref == left_slot_ref]
    assert matching
    assert cell_of(matching[0]) == "right"


# ------------------------------------------------------------- blank_mode


def test_balanced_blank_mode_keeps_signatures_within_one_sheet():
    plan = impose(make_pages(250), settings(blank_mode="balanced"))
    sizes = [len(sig.sheet_indices) for sig in plan.signatures]
    assert max(sizes) - min(sizes) <= 1


def test_end_blank_mode_puts_shortfall_in_final_signature():
    plan_end = impose(make_pages(250), settings(blank_mode="end"))
    sizes = [len(sig.sheet_indices) for sig in plan_end.signatures]
    assert sizes[-1] <= sizes[0]
    assert all(s == sizes[0] for s in sizes[:-1])


def test_signature_sheet_groups_balanced_shaves_the_tail():
    end_sizes = [len(g) for g in _signature_sheet_groups(70, 8, "end")]
    balanced_sizes = [len(g) for g in _signature_sheet_groups(70, 8, "balanced")]
    assert sum(end_sizes) == sum(balanced_sizes) == 70
    assert max(balanced_sizes) - min(balanced_sizes) <= 1
    assert max(end_sizes) - min(end_sizes) > max(balanced_sizes) - min(balanced_sizes)


# ------------------------------------------------------------- sheet orientation


def test_portrait_paper_warns_once_and_does_not_swap_paper_pt():
    plan = impose(make_pages(8), settings(paper=LETTER_PORTRAIT))
    orientation_warnings = [w for w in plan.warnings if w.kind == "sheet_orientation"]
    assert len(orientation_warnings) == 1
    assert plan.paper_pt == LETTER_PORTRAIT
    assert len(plan.sheets) > 0


# ------------------------------------------------------------- creep


def test_creep_advisory_names_trim_and_remedy():
    """`(sheets - 1) * caliper`, not `sheets * caliper`.

    The outermost leaf is pushed out by nothing, so a gathering of one
    sheet creeps by nothing. This test used to assert 2.16 -- 0.27 x 8 --
    which made this advisory a third opinion on one physical quantity:
    `schedule._creep_note` and `paper.suggest_sheets_per_signature` both
    use the formula below, and three numbers for one measurement is worse
    than none.
    """
    plan = impose(make_pages(32), settings(paper_thickness_pt=0.27, sheets_per_signature=8))
    creep = [w for w in plan.warnings if w.kind == "creep_advisory"]
    assert len(creep) == 1
    assert "1.89" in creep[0].detail
    assert "sheets per signature" in creep[0].detail


def test_the_creep_remedy_is_the_size_the_panel_would_suggest():
    """The remedy used to be "halve it", which is derived from nothing and
    says the same thing however many times it is taken. Now it is the
    real answer, so the advisory and the panel cannot disagree about one
    document."""
    from deckle.core.paper import suggest_sheets_per_signature

    layout = settings(paper_thickness_pt=0.27, sheets_per_signature=8)
    plan = impose(make_pages(32), layout)
    detail = [w for w in plan.warnings if w.kind == "creep_advisory"][0].detail

    expected = suggest_sheets_per_signature(0.27, trim_pt=layout.trim_pt).sheets
    assert f"{expected} sheets per signature" in detail


def test_negligible_creep_says_nothing_at_all():
    """It used to fire whenever a thickness was set, however small the
    creep -- so a binder who filled the field in got a warning about
    1.18pt, which is invisible. An advisory that always fires is one
    people learn to skip."""
    plan = impose(make_pages(32), settings(paper_thickness_pt=0.01, sheets_per_signature=4))

    assert not any(w.kind == "creep_advisory" for w in plan.warnings)


def test_a_planned_trim_absorbs_the_creep_and_silences_the_advisory():
    """Creep inside the trim is not a problem to report."""
    plan = impose(
        make_pages(32),
        settings(paper_thickness_pt=0.27, sheets_per_signature=8, trim_pt=36.0),
    )

    assert not any(w.kind == "creep_advisory" for w in plan.warnings)


def test_zero_paper_thickness_emits_no_creep_advisory():
    plan = impose(make_pages(32), settings(paper_thickness_pt=0.0))
    assert not any(w.kind == "creep_advisory" for w in plan.warnings)


def test_creep_never_affects_placement_geometry():
    s0 = settings(paper_thickness_pt=0.0)
    s2 = settings(paper_thickness_pt=2.0)
    plan0 = impose(make_pages(32), s0)
    plan2 = impose(make_pages(32), s2)
    placements0 = [op.placement for op in flat_pages(plan0)]
    placements2 = [op.placement for op in flat_pages(plan2)]
    assert placements0 == placements2


def test_creep_references_are_isolated_to_creep_advisory():
    """AST test: every ``paper_thickness_pt`` reference lives inside ``_creep_advisory``."""
    import deckle.core.layout as layout_module

    source = inspect.getsource(layout_module)
    tree = ast.parse(source)

    creep_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_creep_advisory":
            creep_fn = node
            break
    assert creep_fn is not None

    creep_fn_lines = set(range(creep_fn.lineno, creep_fn.end_lineno + 1))

    bad_lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "paper_thickness_pt":
            if node.lineno not in creep_fn_lines:
                bad_lines.append(node.lineno)
        if isinstance(node, ast.Name) and node.id == "paper_thickness_pt":
            if node.lineno not in creep_fn_lines:
                bad_lines.append(node.lineno)
    assert not bad_lines, f"paper_thickness_pt referenced outside _creep_advisory at lines {bad_lines}"


# ------------------------------------------------------------- clipping


def test_clipped_by_page_measured_against_the_cell():
    from deckle.core.layout import _place_page, document_scale

    s = settings(gutter_pt=0.0, margin_outer_pt=0.0, margin_top_pt=0.0, margin_bottom_pt=0.0)
    ref = SourceRef(path="wide.pdf", page_index=0, sha256="b" * 64, width_pt=600.0, height_pt=612.0)
    page = SourcePage(ref=ref, rotate_deg=0, skipped=False)

    scale = document_scale([page], s)  # fits the FULL sheet, not the half-cell
    cell = cell_geometry(s.paper)[0]

    cell_warnings: list = []
    _place_page(page, 0, s, 0, cell_warnings, scale, cell=cell, spine_side="right")
    assert any(w.kind == "clipped_by_page" for w in cell_warnings)

    sheet_warnings: list = []
    _place_page(page, 0, s, 0, sheet_warnings, scale)
    assert not any(w.kind == "clipped_by_page" for w in sheet_warnings)


# ------------------------------------------------------------- marks


def test_every_side_carries_a_fold_line():
    plan = impose(make_pages(16), settings(sheets_per_signature=2))
    for side in flat_sides(plan):
        assert any(m.kind == "fold_line" for m in side.marks)


def test_innermost_sheet_inner_side_carries_sewing_stations():
    plan = impose(make_pages(16), settings(sheets_per_signature=2, sewing_stations=3))
    for sig in plan.signatures:
        innermost_sheet_index = sig.sheet_indices[-1]
        sheet = plan.sheets[innermost_sheet_index]
        station_marks = [m for m in sheet.back.marks if m.kind == "sewing_station"]
        assert len(station_marks) == 3
        # no other sheet in the signature carries station marks
        for idx in sig.sheet_indices[:-1]:
            other = plan.sheets[idx]
            assert not any(m.kind == "sewing_station" for m in other.front.marks)
            assert not any(m.kind == "sewing_station" for m in other.back.marks)


def test_outermost_sheet_carries_exactly_one_signature_order_mark():
    plan = impose(make_pages(16), settings(sheets_per_signature=2))
    for sig in plan.signatures:
        outermost_sheet_index = sig.sheet_indices[0]
        sheet = plan.sheets[outermost_sheet_index]
        order_marks = [m for m in sheet.front.marks if m.kind == "signature_order"]
        assert len(order_marks) == 1
        assert not any(m.kind == "signature_order" for m in sheet.back.marks)


def test_sewing_stations_zero_yields_no_station_marks_but_keeps_fold_lines():
    plan = impose(make_pages(16), settings(sheets_per_signature=2, sewing_stations=0))
    all_marks = [m for side in flat_sides(plan) for m in side.marks]
    assert not any(m.kind == "sewing_station" for m in all_marks)
    assert any(m.kind == "fold_line" for m in all_marks)


# ------------------------------------------------------------- the round trip


def test_fold_simulator_round_trip_small_matrix():
    for n in list(range(1, 41)):
        for sps in range(1, 9):
            for binding_edge in ("left", "right"):
                assert_round_trip(make_pages(n), settings(sheets_per_signature=sps, binding_edge=binding_edge))


def test_fold_simulator_round_trip_larger_documents():
    for n in (100, 266):
        for sps in range(1, 9):
            for binding_edge in ("left", "right"):
                assert_round_trip(make_pages(n), settings(sheets_per_signature=sps, binding_edge=binding_edge))


# ------------------------------------------------------------- invariants


def test_invariants_hold_across_many_shapes():
    for n in (1, 4, 5, 8, 17, 33, 100):
        for sps in (1, 3, 5):
            plan = impose(make_pages(n), settings(sheets_per_signature=sps))
            covered = [i for sig in plan.signatures for i in sig.sheet_indices]
            assert covered == list(range(len(plan.sheets)))
            total_padded_pages = sum(len(sig.sheet_indices) for sig in plan.signatures) * 4
            assert total_padded_pages == len(flat_pages(plan))


# ----- landscape sources under folio ------------------------------------
#
# The scale pass asked whether a page rotates by looking at the PAPER; the
# placement pass asked by looking at the CELL. Under folio the sheet is
# landscape and each of its two cells is portrait, so the two questions
# have opposite answers. A landscape source was then placed rotated at a
# scale computed for an unrotated page: 0.50 where 0.6471 fitted, on every
# leaf of the book, with nothing warning that it had happened.


def _cell_box(s: LayoutSettings) -> tuple[float, float]:
    """The content box of one folio cell, after margins."""
    cell = cell_geometry(s.paper)[0]
    return content_box_size(s, cell)


def test_a_landscape_source_is_scaled_for_the_cell_it_is_rotated_into():
    """The scale must be the one that fits the page as it is actually placed.

    A 792x612 source turned a quarter is 612x792, and a zero-margin folio
    cell on letter-landscape paper is 396x612. The number is derived here
    from those dimensions rather than written as 0.6471, so the test says
    why it is that and not something else.
    """
    s = settings(gutter_pt=0.0, margin_outer_pt=0.0,
                 margin_top_pt=0.0, margin_bottom_pt=0.0)
    plan = impose(make_pages(4, w=792.0, h=612.0), s)

    box_w, box_h = _cell_box(s)
    expected = min(box_w / 612.0, box_h / 792.0)

    placement = plan.sheets[0].front.pages[0].placement
    assert placement.rotate_deg == 90
    assert placement.scale_x == pytest.approx(expected)


def test_the_scale_pass_and_the_placement_pass_agree_about_rotation():
    """The invariant behind the bug, stated directly.

    A rotated leaf's scaled footprint must fit its cell's content box, and
    fit it snugly -- one axis has to bind. A scale computed for the other
    orientation also fits, which is why the bug was invisible; what it
    never does is bind.
    """
    s = settings()
    plan = impose(make_pages(4, w=792.0, h=612.0), s)
    box_w, box_h = _cell_box(s)

    rotated = [
        page.placement
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
        for page in side.pages
        if page.placement.rotate_deg % 180 == 90
    ]
    assert rotated, "no leaf rotated; the fixture is meant to be landscape"

    for placement in rotated:
        width = 612.0 * placement.scale_x
        height = 792.0 * placement.scale_y
        assert width <= box_w + 1e-6, (width, box_w)
        assert height <= box_h + 1e-6, (height, box_h)
        binds = (
            abs(width - box_w) < 1e-6 or abs(height - box_h) < 1e-6
        )
        assert binds, (
            f"scaled footprint {width:.4f}x{height:.4f} floats inside a "
            f"{box_w:.4f}x{box_h:.4f} box -- the scale was computed for the "
            "other orientation"
        )


def test_a_portrait_source_under_folio_is_unaffected():
    """The guard that the fix does not start rotating things.

    The module's default page is 396x612, already portrait, in a portrait
    cell. Nothing should turn, nothing should warn, and one scale should
    serve the whole document.
    """
    plan = impose(make_pages(8), settings())

    assert not [w for w in plan.warnings if w.kind == "mixed_orientation"]

    scales = set()
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            for page in side.pages:
                assert page.placement.rotate_deg == 0
                if not page.is_filler:
                    scales.add(round(page.placement.scale_x, 9))
    assert len(scales) == 1, scales


def test_the_landscape_warning_still_fires_once_per_leaf():
    """The fix changes the scale, not whether the rotation is reported.

    Four landscape pages produce four placed leaves and four warnings. A
    fix that silenced a true warning while correcting the arithmetic would
    be a worse trade than the bug.
    """
    plan = impose(make_pages(4, w=792.0, h=612.0), settings())

    mixed = [w for w in plan.warnings if w.kind == "mixed_orientation"]
    assert len(mixed) == 4, [w.detail for w in mixed]
