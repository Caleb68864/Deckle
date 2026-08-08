"""``spine_side`` wins over parity, in all three places that decide it.

Three places in ``layout`` resolve a spine from two competing signals --
``_place_page`` (from ``output_index``), ``content_box_rect_pt`` and
``actual_margins_pt`` (from ``is_recto``) -- and all three document the
parity signal as ignored once ``spine_side`` is given. That precedence is
what makes folio work at all: under a fold the spine is a function of
*which cell a leaf sits in*, not of page parity, so
``SaddleStitchStrategy`` passes ``spine_side`` and the parity answer
would be wrong for half the leaves.

`docs/converge/2026-08-04-deckle-signatures-v2-design/criteria-drift-classification.md`
classified this criterion **VACUOUS** on 2026-08-04, reasoning that every
existing test passes `spine_side` *or* the parity signal and never both,
so reversing the precedence would leave the suite green. Each test here
therefore supplies **both, disagreeing** -- parity naming one edge and
``spine_side`` the other -- so the assertion cannot pass under either
reading by accident.

**Mutation says the 2026-08-04 classification was right about one of the
three, not all of them.** Disabling the ``spine_side`` branch at each site
in turn, and running the three suites that existed before this file:

- ``content_box_rect_pt`` -- 157 passed. Genuinely unguarded, exactly as
  classified, and still unguarded five days later.
- ``_place_page`` -- 7 failed. Already covered.
- ``actual_margins_pt`` -- 3 failed. Already covered.

Recorded rather than smoothed over, because the difference matters to
anyone reading that classification: it was a reasonable inference from
"no test passes both signals", and it was over-general. Two of the three
were protected by tests that never pass both, because a *plan-level* test
exercises the folio path end to end and a wrong spine moves real content.
The one that escaped is the function nothing calls through a plan.
"""

from __future__ import annotations

import pytest

from deckle.core.layout import (
    _place_page, actual_margins_pt, content_box_rect_pt,
)
from deckle.core.models import (
    LayoutSettings, OutputPage, Placement, SourcePage, SourceRef,
)

PAPER = (792.0, 612.0)
# The right-hand cell of a folio sheet: the fold is its LEFT edge.
RIGHT_CELL = (396.0, 0.0, 792.0, 612.0)


def _settings(**over) -> LayoutSettings:
    base = dict(
        paper=PAPER, gutter_pt=72.0, binding_edge="left",
        margin_outer_pt=18.0, margin_top_pt=18.0, margin_bottom_pt=18.0,
        margins_linked=False,
    )
    base.update(over)
    return LayoutSettings(**base)


def _placed_page() -> OutputPage:
    """A page sitting hard against the left edge of ``RIGHT_CELL``."""
    return OutputPage(
        source_ref=SourceRef(path="s.pdf", page_index=0, sha256="a" * 64,
                             width_pt=100.0, height_pt=100.0),
        placement=Placement(scale_x=1.0, scale_y=1.0,
                            tx=396.0, ty=0.0, rotate_deg=0),
        is_filler=False,
    )


def test_measured_margins_follow_spine_side_not_parity():
    """``is_recto=True`` under a left binding says "spine on the left";
    ``spine_side="right"`` says the opposite. The page sits against the
    cell's left edge, so the two readings give different answers: inner
    is 0 if parity wins, 296 if ``spine_side`` does."""
    inner, outer, _, _ = actual_margins_pt(
        _placed_page(), PAPER,
        is_recto=True, binding_edge="left",
        spine_side="right", cell=RIGHT_CELL,
    )

    assert (inner, outer) == (296.0, 0.0)


def test_measured_margins_follow_spine_side_in_the_other_direction():
    """The mirror, so a fix that simply hard-codes one side fails too."""
    inner, outer, _, _ = actual_margins_pt(
        _placed_page(), PAPER,
        is_recto=False, binding_edge="left",
        spine_side="left", cell=RIGHT_CELL,
    )

    assert (inner, outer) == (0.0, 296.0)


@pytest.mark.parametrize("is_recto", [True, False])
def test_parity_cannot_change_a_measurement_that_named_its_spine(is_recto):
    """The precedence stated as an invariant: once ``spine_side`` is
    given, ``is_recto`` is inert. Both values must produce one answer."""
    margins = actual_margins_pt(
        _placed_page(), PAPER,
        is_recto=is_recto, binding_edge="left",
        spine_side="right", cell=RIGHT_CELL,
    )

    assert margins == actual_margins_pt(
        _placed_page(), PAPER,
        is_recto=not is_recto, binding_edge="left",
        spine_side="right", cell=RIGHT_CELL,
    )


def test_the_content_box_follows_spine_side_not_parity():
    """The same precedence in the function that *decides* where content
    goes, rather than the one that measures where it went. A disagreement
    between these two is a preview that reports margins the exporter did
    not produce."""
    settings = _settings()

    by_spine = content_box_rect_pt(
        settings, is_recto=True, spine_side="right", cell=RIGHT_CELL
    )
    by_parity = content_box_rect_pt(
        settings, is_recto=True, cell=RIGHT_CELL
    )

    assert by_spine != by_parity
    assert by_spine == content_box_rect_pt(
        settings, is_recto=False, spine_side="right", cell=RIGHT_CELL
    )


@pytest.mark.parametrize("output_index", [0, 1])
def test_the_placement_itself_ignores_parity_once_the_spine_is_named(output_index):
    """Third of the three, and the one that matters most: the other two
    describe where content *is*, this one decides where it *goes*.

    ``_place_page`` takes no ``is_recto`` -- it derives parity from
    ``output_index``, which is the same signal by another name -- so the
    precedence is asserted by holding ``spine_side`` fixed and flipping
    the index under it.
    """
    settings = _settings()
    source = SourcePage(
        ref=SourceRef(path="s.pdf", page_index=0, sha256="a" * 64,
                      width_pt=100.0, height_pt=100.0),
        rotate_deg=0, skipped=False,
    )

    placed = _place_page(
        source, output_index, settings, 0, [], 1.0,
        cell=RIGHT_CELL, spine_side="right",
    )
    flipped = _place_page(
        source, 1 - output_index, settings, 0, [], 1.0,
        cell=RIGHT_CELL, spine_side="right",
    )

    assert placed.placement == flipped.placement


def test_the_placement_still_follows_parity_with_no_spine_side():
    """The other half, so a fix cannot simply ignore the index."""
    settings = _settings()
    source = SourcePage(
        ref=SourceRef(path="s.pdf", page_index=0, sha256="a" * 64,
                      width_pt=100.0, height_pt=100.0),
        rotate_deg=0, skipped=False,
    )

    recto = _place_page(source, 0, settings, 0, [], 1.0, cell=RIGHT_CELL)
    verso = _place_page(source, 1, settings, 0, [], 1.0, cell=RIGHT_CELL)

    assert recto.placement.tx != verso.placement.tx


def test_parity_is_still_used_when_no_spine_side_is_given():
    """The other half of the precedence, and the case every existing test
    covers -- pinned here so a fix that makes ``spine_side`` mandatory
    would fail rather than silently breaking gutter-shift."""
    left = actual_margins_pt(
        _placed_page(), PAPER, is_recto=True, binding_edge="left",
        cell=RIGHT_CELL,
    )
    right = actual_margins_pt(
        _placed_page(), PAPER, is_recto=False, binding_edge="left",
        cell=RIGHT_CELL,
    )

    assert left != right
