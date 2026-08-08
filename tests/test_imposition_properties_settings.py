"""Properties over the settings added most recently.

``tests/test_imposition_properties.py`` covers the shape of imposition --
that it is a permutation, that content stays on the sheet, that one scale
serves the whole document -- across page counts, signature sizes and
gutters. It predates ``--crop``, ``--trim`` and ``--signatures``, and
none of those appear in it.

Those three are worth property coverage rather than examples for the same
reason the originals were: each interacts with the geometry the others
already established, and the interactions are where the arithmetic goes
wrong. A crop changes the size the placement is derived from, so it can
push content off a sheet that fits uncropped. A trim draws marks whose
depth is a free parameter. Explicit signature lengths replace a
*derivation* with a *statement*, so the partition property has to hold
for a partition nobody computed.

The properties asserted here are the ones that must survive all three at
once, which is the combination no example test covers.
"""

from __future__ import annotations

import math

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from deckle.core.layout import (  # noqa: E402
    GutterShiftStrategy,
    SaddleStitchStrategy,
)
from deckle.core.models import LayoutSettings, SourcePage, SourceRef  # noqa: E402

PAPER = (792.0, 612.0)
PAGE_W, PAGE_H = 400.0, 600.0

SLOW = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _pages(count: int, width: float = PAGE_W, height: float = PAGE_H):
    return [
        SourcePage(
            ref=SourceRef(path="book.pdf", page_index=i, sha256="a" * 64,
                          width_pt=width, height_pt=height),
            rotate_deg=0, skipped=False,
        )
        for i in range(count)
    ]


def _impose(pages, **over):
    base = dict(paper=PAPER, gutter_pt=36.0, binding_edge="left")
    base.update(over)
    settings_obj = LayoutSettings(**base)
    strategy = (
        SaddleStitchStrategy() if settings_obj.fold_scheme == "folio"
        else GutterShiftStrategy()
    )
    return strategy.impose(pages, settings_obj), settings_obj


def _real_pages(plan):
    return [
        output
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back) if side is not None
        for output in side.pages
        if output.source_ref is not None
    ]


def _marks(plan):
    return [
        mark
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back) if side is not None
        for mark in side.marks
    ]


# Insets bounded well inside the page, so the crop is always legal -- an
# illegal crop is refused by `impose` and tested by example elsewhere.
insets = st.floats(min_value=0.0, max_value=120.0,
                   allow_nan=False, allow_infinity=False)
crops = st.tuples(insets, insets, insets, insets)
counts = st.integers(min_value=1, max_value=32)
trims = st.floats(min_value=0.0, max_value=60.0,
                  allow_nan=False, allow_infinity=False)


# -- crop ----------------------------------------------------------------


@SLOW
@given(count=counts, crop=crops)
def test_a_crop_never_pushes_content_off_the_sheet(count, crop):
    """The property a crop is most likely to break: it changes the size
    the placement is derived from, so a page that fitted uncropped could
    be scaled or offset past an edge."""
    plan, _ = _impose(_pages(count), crop_odd_pt=crop, crop_even_pt=crop)

    width, height = PAPER
    for output in _real_pages(plan):
        placement = output.placement
        source_w, source_h = PAGE_W - crop[0] - crop[2], PAGE_H - crop[1] - crop[3]
        if placement.rotate_deg in (90, 270):
            source_w, source_h = source_h, source_w
        assert placement.tx >= -0.01
        assert placement.ty >= -0.01
        assert placement.tx + source_w * placement.scale_x <= width + 0.01
        assert placement.ty + source_h * placement.scale_y <= height + 0.01


@SLOW
@given(count=counts, crop=crops)
def test_a_crop_keeps_one_scale_for_the_whole_document(count, crop):
    """Uniform scale is the property that keeps body text the same size
    from leaf to leaf. A crop applied per parity could break it."""
    plan, _ = _impose(_pages(count), crop_odd_pt=crop, crop_even_pt=crop)

    scales = {round(o.placement.scale_x, 9) for o in _real_pages(plan)}
    assert len(scales) <= 1


@SLOW
@given(count=counts, crop=crops)
def test_a_crop_never_distorts_the_aspect_ratio(count, crop):
    """``scale_y`` equals ``scale_x`` for every placement Deckle emits --
    the model says so, and a crop is exactly the kind of change that
    would make an axis-specific scale look reasonable."""
    plan, _ = _impose(_pages(count), crop_odd_pt=crop, crop_even_pt=crop)

    for output in _real_pages(plan):
        assert math.isclose(
            output.placement.scale_x, output.placement.scale_y, rel_tol=1e-9
        )


@SLOW
@given(count=counts, odd=crops, even=crops)
def test_differing_odd_and_even_crops_still_place_every_page(count, odd, even):
    """The case ``crop_even_pt`` exists for -- a scan whose gutter swaps
    sides -- where the two parities genuinely differ in size."""
    plan, _ = _impose(_pages(count), crop_odd_pt=odd, crop_even_pt=even)

    placed = sorted(o.source_ref.page_index for o in _real_pages(plan))
    assert placed == list(range(count))


# -- trim ----------------------------------------------------------------


@SLOW
@given(count=st.integers(min_value=4, max_value=32), trim=trims)
def test_every_trim_mark_stays_on_the_sheet(count, trim):
    """A cut line off the sheet is an instruction that cannot be followed,
    and the depth is a free parameter the user supplies."""
    plan, _ = _impose(_pages(count), fold_scheme="folio", trim_pt=trim)

    width, height = PAPER
    for mark in _marks(plan):
        assert -0.01 <= min(mark.x0, mark.x1)
        assert max(mark.x0, mark.x1) <= width + 0.01
        assert -0.01 <= min(mark.y0, mark.y1)
        assert max(mark.y0, mark.y1) <= height + 0.01


@SLOW
@given(count=st.integers(min_value=4, max_value=32), trim=trims)
def test_a_trim_never_moves_a_page(count, trim):
    """Trim marks describe where to cut; they are not a layout decision
    and must not shift content. A trim that repositioned pages would put
    the fold line somewhere the fold is not."""
    without, _ = _impose(_pages(count), fold_scheme="folio")
    with_trim, _ = _impose(_pages(count), fold_scheme="folio", trim_pt=trim)

    assert [o.placement for o in _real_pages(without)] == [
        o.placement for o in _real_pages(with_trim)
    ]


# -- explicit signature lengths -----------------------------------------


@SLOW
@given(lengths=st.lists(st.integers(min_value=1, max_value=6),
                        min_size=1, max_size=6))
def test_stated_signature_lengths_are_honoured_exactly(lengths):
    """``--signatures 10,10,8`` replaces a derivation with a statement, so
    the only correct answer is the one the binder asked for."""
    sheets = sum(lengths)
    plan, _ = _impose(
        _pages(sheets * 4), fold_scheme="folio",
        signature_lengths=tuple(lengths),
    )

    assert [len(s.sheet_indices) for s in plan.signatures] == list(lengths)


@SLOW
@given(lengths=st.lists(st.integers(min_value=1, max_value=6),
                        min_size=1, max_size=6))
def test_stated_signatures_still_partition_every_sheet(lengths):
    """Each sheet in exactly one signature, with none invented or lost --
    the same property the derived grouping has, for a grouping nobody
    computed."""
    sheets = sum(lengths)
    plan, _ = _impose(
        _pages(sheets * 4), fold_scheme="folio",
        signature_lengths=tuple(lengths),
    )

    grouped = [i for s in plan.signatures for i in s.sheet_indices]
    assert sorted(grouped) == sorted(s.index for s in plan.sheets)


@SLOW
@given(lengths=st.lists(st.integers(min_value=1, max_value=6),
                        min_size=1, max_size=6),
       delta=st.integers(min_value=1, max_value=8))
def test_lengths_that_do_not_add_up_are_refused(lengths, delta):
    """The sum is checkable and the message names both numbers, because
    the sheet count is not knowable without imposing."""
    sheets = sum(lengths)
    with pytest.raises(ValueError):
        _impose(
            _pages((sheets + delta) * 4), fold_scheme="folio",
            signature_lengths=tuple(lengths),
        )


# -- the three together --------------------------------------------------


@SLOW
@given(count=st.integers(min_value=1, max_value=6).map(lambda n: n * 4),
       crop=crops, trim=trims)
def test_crop_trim_and_folio_together_keep_content_on_the_sheet(count, crop, trim):
    """The combination no example test covers, and the reason these are
    properties: each of the three was verified alone.

    The page count is *generated* as a multiple of four rather than
    filtered with ``assume``. Filtering discarded three examples in four,
    which trips Hypothesis's ``filter_too_much`` health check -- and it
    trips it *sometimes*, depending on how the draws happen to fall, so
    the test failed once in a full-suite run and passed on every rerun.
    A flaky property test is worse than no property test: it teaches
    people to rerun rather than to look.
    """
    plan, _ = _impose(
        _pages(count), fold_scheme="folio", trim_pt=trim,
        crop_odd_pt=crop, crop_even_pt=crop,
    )

    width, height = PAPER
    for output in _real_pages(plan):
        placement = output.placement
        source_w = PAGE_W - crop[0] - crop[2]
        source_h = PAGE_H - crop[1] - crop[3]
        if placement.rotate_deg in (90, 270):
            source_w, source_h = source_h, source_w
        assert placement.tx >= -0.01
        assert placement.ty >= -0.01
        assert placement.tx + source_w * placement.scale_x <= width + 0.01
        assert placement.ty + source_h * placement.scale_y <= height + 0.01
