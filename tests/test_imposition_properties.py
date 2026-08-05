"""Property-based tests for imposition.

The existing suite checks imposition at a handful of hand-picked sizes: 8
pages, 16, 64, a few signature counts. Those catch the cases someone thought
of. Imposition is arithmetic over an unbounded space -- any page count, any
signature size, any paper -- and the defects that reach paper live in the
corners nobody picked: 1 page, 5 pages at 4 sheets per signature, a document
that is exactly one blank short of dividing evenly.

So these assert *properties* that must hold for every input, and let
Hypothesis go looking for the input that breaks them.

The load-bearing one is that imposition is a **permutation**: every page the
user kept appears exactly once, somewhere. A page duplicated or dropped is a
misbound book, and it is the failure mode least likely to be noticed on
screen -- the preview shows one sheet at a time, and sheet 34 looking
plausible says nothing about page 71 having vanished.

Hypothesis is MPL-2.0 (verified from its own LICENSE.txt) and test-only, so
it never enters the shipped closure that ``tests/test_license_audit.py`` and
``tests/test_packaging_audit.py`` guard.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from deckle.core.layout import (  # noqa: E402
    GutterShiftStrategy,
    SaddleStitchStrategy,
    cell_geometry,
)
from deckle.core.models import LayoutSettings, SourcePage, SourceRef  # noqa: E402
from deckle.core.signatures import fold_reading_order, saddle_order, split_signatures  # noqa: E402

# Imposition never opens a file -- it works on dimensions alone -- so a
# synthetic ref is honest here. Anything that DOES open the file (export,
# thumbnails) is tested against real PDFs elsewhere.
SOURCE = "book.pdf"

LETTER_PORTRAIT = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)


def _pages(count: int, width: float = 400.0, height: float = 600.0) -> list[SourcePage]:
    return [
        SourcePage(
            ref=SourceRef(
                path=SOURCE, page_index=i, sha256="a" * 64,
                width_pt=width, height_pt=height,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(count)
    ]


def _placed_page_indices(plan) -> list[int]:
    """Every real source page in the plan, in sheet order.

    Fillers carry ``source_ref=None`` and are excluded: they are the
    imposer's own padding, not the user's pages.
    """
    found = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            for output_page in side.pages:
                if output_page.source_ref is not None:
                    found.append(output_page.source_ref.page_index)
    return found


# Bounded so the suite stays fast; the interesting corners are all small.
page_counts = st.integers(min_value=1, max_value=60)
sheets_per_signature = st.integers(min_value=1, max_value=8)
gutters = st.floats(min_value=0.0, max_value=72.0, allow_nan=False, allow_infinity=False)

SLOW = settings(
    max_examples=60,
    deadline=None,  # imposition of 60 pages is not instant on a cold cache
    suppress_health_check=[HealthCheck.too_slow],
)


# -- the permutation property -------------------------------------------


@SLOW
@given(count=page_counts, gutter=gutters)
def test_gutter_shift_places_every_page_exactly_once(count, gutter):
    settings_ = LayoutSettings(
        paper=LETTER_PORTRAIT, gutter_pt=gutter, binding_edge="left"
    )
    plan = GutterShiftStrategy().impose(_pages(count), settings_)

    assert sorted(_placed_page_indices(plan)) == list(range(count))


@SLOW
@given(count=page_counts, per_sig=sheets_per_signature, gutter=gutters)
def test_folio_places_every_page_exactly_once(count, per_sig, gutter):
    """The one that matters. A page dropped or duplicated is a misbound
    book, and the preview cannot show it -- one sheet at a time says
    nothing about whether page 71 still exists."""
    settings_ = LayoutSettings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=gutter,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=per_sig,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)

    assert sorted(_placed_page_indices(plan)) == list(range(count))


@SLOW
@given(count=page_counts, skip_every=st.integers(min_value=2, max_value=5))
def test_skipped_pages_never_reach_the_paper(count, skip_every):
    pages = _pages(count)
    kept = []
    marked = []
    for i, page in enumerate(pages):
        if i % skip_every == 0:
            marked.append(
                SourcePage(ref=page.ref, rotate_deg=page.rotate_deg, skipped=True)
            )
        else:
            marked.append(page)
            kept.append(i)

    settings_ = LayoutSettings(
        paper=LETTER_PORTRAIT, gutter_pt=18.0, binding_edge="left"
    )
    plan = GutterShiftStrategy().impose(marked, settings_)

    assert sorted(_placed_page_indices(plan)) == kept


# -- structural properties ----------------------------------------------


@SLOW
@given(count=page_counts, per_sig=sheets_per_signature)
def test_signatures_partition_the_sheets(count, per_sig):
    """Every sheet belongs to exactly one signature, and none is invented.

    A sheet in two signatures gets gathered twice; a sheet in none never
    gets sewn in at all.
    """
    settings_ = LayoutSettings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=0.0,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=per_sig,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)

    claimed = [i for signature in plan.signatures for i in signature.sheet_indices]
    assert sorted(claimed) == sorted(s.index for s in plan.sheets)
    assert len(claimed) == len(set(claimed)), "a sheet is in two signatures"


@SLOW
@given(count=page_counts)
def test_folio_pads_to_a_multiple_of_four_and_no_further(count):
    """A folded sheet yields four pages, so the total must be a multiple of
    four -- but padding past the next multiple wastes a whole sheet."""
    settings_ = LayoutSettings(
        paper=LETTER_LANDSCAPE, gutter_pt=0.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=4,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)

    slots = sum(
        len(side.pages)
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
    )
    assert slots % 4 == 0
    assert slots - count < 4, f"padded {slots - count} pages past what was needed"


@SLOW
@given(count=page_counts, per_sig=sheets_per_signature)
def test_every_folio_side_carries_exactly_two_leaves(count, per_sig):
    settings_ = LayoutSettings(
        paper=LETTER_LANDSCAPE, gutter_pt=0.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=per_sig,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)

    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is not None:
                assert len(side.pages) == 2


@SLOW
@given(count=page_counts, gutter=gutters)
def test_one_document_scale_for_every_page(count, gutter):
    """A book is not a pile of independent pages. Two pages at different
    scales means body text that changes size mid-chapter."""
    settings_ = LayoutSettings(
        paper=LETTER_PORTRAIT, gutter_pt=gutter, binding_edge="left"
    )
    plan = GutterShiftStrategy().impose(_pages(count), settings_)

    scales = {
        (op.placement.scale_x, op.placement.scale_y)
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
        for op in side.pages
        if op.source_ref is not None
    }
    assert len(scales) <= 1, f"pages imposed at differing scales: {scales}"


@SLOW
@given(count=page_counts, gutter=gutters)
def test_no_content_is_placed_off_the_sheet(count, gutter):
    """Content that starts outside the page is not a clipping warning, it
    is arithmetic that went wrong."""
    paper = LETTER_PORTRAIT
    settings_ = LayoutSettings(paper=paper, gutter_pt=gutter, binding_edge="left")
    plan = GutterShiftStrategy().impose(_pages(count), settings_)

    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            for op in side.pages:
                assert op.placement.tx >= -1e-6
                assert op.placement.ty >= -1e-6
                assert op.placement.tx <= paper[0] + 1e-6
                assert op.placement.ty <= paper[1] + 1e-6


@SLOW
@given(count=page_counts, per_sig=sheets_per_signature)
def test_folio_leaves_stay_inside_their_own_cell(count, per_sig):
    """A leaf that crosses the fold prints across the spine."""
    paper = LETTER_LANDSCAPE
    settings_ = LayoutSettings(
        paper=paper, gutter_pt=0.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=per_sig,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)
    left_cell, right_cell = cell_geometry(paper)
    fold_x = paper[0] / 2.0

    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            left_op, right_op = side.pages
            if left_op.source_ref is not None:
                right_edge = (
                    left_op.placement.tx
                    + left_op.source_ref.width_pt * left_op.placement.scale_x
                )
                assert right_edge <= fold_x + 1e-6, "left leaf crosses the fold"
            if right_op.source_ref is not None:
                assert right_op.placement.tx >= fold_x - 1e-6, (
                    "right leaf crosses the fold"
                )


# -- the signature arithmetic itself ------------------------------------


@SLOW
@given(sheet_count=st.integers(min_value=0, max_value=200), per_sig=sheets_per_signature)
def test_split_signatures_partitions_without_gaps_or_overlap(sheet_count, per_sig):
    groups = split_signatures(sheet_count, per_sig)

    flat = [i for group in groups for i in group]
    assert flat == list(range(sheet_count)), "groups must tile 0..n-1 in order"
    assert all(len(g) <= per_sig for g in groups)
    if len(groups) > 1:
        assert all(len(g) == per_sig for g in groups[:-1]), (
            "only the last group may be short"
        )


@SLOW
@given(n=st.integers(min_value=1, max_value=40).map(lambda k: k * 4))
def test_saddle_order_is_always_a_permutation(n):
    order = saddle_order(n)

    assert sorted(order) == list(range(n))


@SLOW
@given(n=st.integers(min_value=1, max_value=60))
def test_saddle_order_rejects_anything_not_a_multiple_of_four(n):
    assume(n % 4 != 0)

    with pytest.raises(ValueError):
        saddle_order(n)


@SLOW
@given(count=page_counts, per_sig=sheets_per_signature)
def test_fold_reading_order_is_a_permutation_of_the_print_slots(count, per_sig):
    """``fold_reading_order`` is derived independently of ``saddle_order``
    -- by simulating the physical fold rather than re-evaluating the same
    formula. That independence is only worth having if it holds for every
    shape of document, not the two that were hand-checked."""
    settings_ = LayoutSettings(
        paper=LETTER_LANDSCAPE, gutter_pt=0.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=per_sig,
    )
    plan = SaddleStitchStrategy().impose(_pages(count), settings_)

    order = fold_reading_order(plan)

    assert sorted(order) == list(range(len(order)))


# -- binding edge --------------------------------------------------------


@SLOW
@given(count=page_counts, edge=st.sampled_from(["left", "right"]))
def test_the_binding_edge_mirrors_rather_than_moving_content(count, edge):
    """Flipping the binding edge must mirror every placement about the
    sheet's centre, not shift or rescale anything."""
    paper = LETTER_PORTRAIT
    gutter = 36.0

    def placements(binding_edge):
        settings_ = LayoutSettings(
            paper=paper, gutter_pt=gutter, binding_edge=binding_edge,
            margin_outer_pt=12.0,
        )
        plan = GutterShiftStrategy().impose(_pages(count), settings_)
        return [
            (op.placement.tx, op.placement.scale_x)
            for sheet in plan.sheets
            for side in (sheet.front, sheet.back)
            if side is not None
            for op in side.pages
            if op.source_ref is not None
        ]

    left = placements("left")
    right = placements("right")
    assert len(left) == len(right)

    for (lx, lscale), (rx, rscale) in zip(left, right):
        assert lscale == pytest.approx(rscale), "the binding edge changed the scale"
        width = 400.0 * lscale
        assert lx + width + rx == pytest.approx(paper[0], abs=1e-6), (
            "placements are not mirror images about the sheet centre"
        )
