"""Grain direction and spine thickness.

Both come from the competitive gap analysis in
``docs/research/2026-08-05-competitive-gaps.md``: no imposition tool surveyed
models paper grain, and spine width lives in standalone calculators rather
than in the tool that already knows the sheet count.

Grain is the one bookbinding literature treats as most consequential. Fibres
align during manufacture; paper creases cleanly along them and cracks across
them. The rule is simply that grain runs parallel to the spine.
"""

from __future__ import annotations

import pytest

from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy, grain_warning
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core.schedule import (
    SWELL_FRACTION_HIGH,
    SWELL_FRACTION_LOW,
    build_schedule,
    format_schedule_text,
    spine_width_pt,
)

LETTER_PORTRAIT = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)


def _pages(n: int) -> list[SourcePage]:
    return [
        SourcePage(
            ref=SourceRef(
                path="book.pdf", page_index=i, sha256="a" * 64,
                width_pt=400.0, height_pt=600.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _settings(**overrides) -> LayoutSettings:
    base = dict(paper=LETTER_LANDSCAPE, gutter_pt=18.0, binding_edge="left")
    base.update(overrides)
    return LayoutSettings(**base)


# -- grain -------------------------------------------------------------


def test_unknown_grain_is_silent():
    """The default. Most people do not know their paper's grain, and a
    warning nobody can act on is noise."""
    assert grain_warning(_settings(fold_scheme="folio")) is None


def test_ordinary_office_paper_folded_into_a_booklet_warns():
    """The mistake almost everyone makes, on the paper almost everyone has.

    Letter stock is long grain -- fibres along the 11in edge. Turn it
    landscape to fold a 5.5x8.5 book and the fold runs vertically while the
    grain runs horizontally: across the grain, the wrong way.
    """
    warning = grain_warning(_settings(fold_scheme="folio", grain="long"))

    assert warning is not None
    assert warning.kind == "grain_direction"
    assert "across the paper grain" in warning.detail
    # It must say what to do, not merely that something is wrong.
    assert "short-grain" in warning.detail


def test_short_grain_stock_folds_correctly_and_stays_silent():
    """What binders buy the special paper for."""
    assert grain_warning(_settings(fold_scheme="folio", grain="short")) is None


def test_flat_sheets_on_portrait_letter_are_correct_with_long_grain():
    """No fold, but the binding edge is still a spine -- and on portrait
    stock the grain already runs vertically, along it."""
    settings = _settings(paper=LETTER_PORTRAIT, fold_scheme="none", grain="long")

    assert grain_warning(settings) is None


def test_flat_sheets_on_portrait_letter_warn_with_short_grain():
    settings = _settings(paper=LETTER_PORTRAIT, fold_scheme="none", grain="short")
    warning = grain_warning(settings)

    assert warning is not None
    assert "long-grain" in warning.detail, "should suggest the other stock"


def test_the_wording_distinguishes_a_fold_from_a_plain_spine():
    """Under folio the offending crease is a fold; under gutter shift there
    is no fold at all and calling it one would confuse."""
    folded = grain_warning(_settings(fold_scheme="folio", grain="long"))
    flat = grain_warning(_settings(paper=LETTER_PORTRAIT, fold_scheme="none", grain="short"))

    assert "fold runs across" in folded.detail
    assert "spine runs across" in flat.detail


def test_square_stock_cannot_be_wrong_either_way():
    assert grain_warning(_settings(paper=(600.0, 600.0), grain="long")) is None


@pytest.mark.parametrize(
    "strategy,scheme",
    [(SaddleStitchStrategy(), "folio"), (GutterShiftStrategy(), "none")],
)
def test_both_strategies_surface_the_warning_on_the_plan(strategy, scheme):
    """A warning the imposer computes but never attaches is invisible."""
    settings = _settings(fold_scheme=scheme, grain="long", paper=LETTER_LANDSCAPE)
    plan = strategy.impose(_pages(8), settings)

    assert "grain_direction" in {w.kind for w in plan.warnings}


# -- spine width -------------------------------------------------------


def test_spine_width_is_unset_without_a_paper_thickness():
    assert spine_width_pt(67, 0.0) is None


def test_spine_width_is_the_block_plus_sewing_swell():
    """67 sheets of 0.004in stock: the block is 0.268in, and thread in every
    fold adds swell at the spine that the fore-edge does not have."""
    caliper = 0.004 * 72  # 0.288pt
    low, high = spine_width_pt(67, caliper)

    block = 67 * caliper
    assert low == pytest.approx(block * (1 + SWELL_FRACTION_LOW))
    assert high == pytest.approx(block * (1 + SWELL_FRACTION_HIGH))
    assert low < high, "a single number here would be false precision"
    # Sanity in the units a binder actually cuts boards in.
    assert 0.28 < low / 72 < 0.32


def test_spine_width_scales_with_the_sheet_count():
    caliper = 0.3
    thin = spine_width_pt(20, caliper)
    thick = spine_width_pt(80, caliper)

    assert thick[0] > thin[0]
    assert thick[1] / thin[1] == pytest.approx(4.0)


def test_the_schedule_prints_the_spine_range_and_names_its_assumption():
    settings = _settings(fold_scheme="folio", paper_thickness_pt=0.288)
    plan = SaddleStitchStrategy().impose(_pages(64), settings)

    text = format_schedule_text(build_schedule(plan, settings))

    assert "Spine thickness" in text
    assert "swell" in text
    # A range, not a point estimate.
    assert "-" in text.split("Spine thickness")[1].split("\n")[0]
    # And it says to measure again, because caliper moves with humidity.
    assert "measure the real block" in text


def test_the_schedule_says_so_when_thickness_is_unset():
    """Silence would read as 'no spine', which is a different claim."""
    settings = _settings(fold_scheme="folio", paper_thickness_pt=0.0)
    plan = SaddleStitchStrategy().impose(_pages(64), settings)

    text = format_schedule_text(build_schedule(plan, settings))

    assert "not estimated" in text
    assert "set paper thickness" in text


# -- the two thicknesses describe two different physical objects ---------


def test_a_flat_sheet_plan_still_computes_a_spine_range():
    """Pins the surprising fact this feature was built on: the number was
    always computed for every plan, and the flat-sheet branch of the
    formatter returned before printing it."""
    settings = _settings(fold_scheme="none", paper_thickness_pt=0.3)
    plan = GutterShiftStrategy().impose(_pages(10), settings)

    assert build_schedule(plan, settings).spine_width_pt is not None


def test_the_glued_block_is_thinner_than_the_sewn_range_for_the_same_stack():
    """The arithmetic that makes this worth a separate function: the sewn
    range starts 10% above the block and ends 25% above it, so reusing it
    for a glued spine overstates by up to a quarter."""
    from deckle.core.schedule import block_width_pt

    block = block_width_pt(64, 0.288)
    low, high = spine_width_pt(64, 0.288)

    assert block == pytest.approx(18.432)
    assert low == pytest.approx(block * (1 + SWELL_FRACTION_LOW))
    assert high == pytest.approx(block * (1 + SWELL_FRACTION_HIGH))
    assert high - block == pytest.approx(4.608)
