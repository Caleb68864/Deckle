"""Finding a document page in an imposed plan.

The grid shows the document; the preview shows the paper. Mapping one to
the other is the whole point of imposition, and nothing recorded it: a
``SheetPlan`` describes paper and keeps no reference back to the page a
slot came from. This walks it back by replaying the correspondence both
strategies build.
"""

from __future__ import annotations

import os

from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
from deckle.core.locate import content_positions, sheet_index_for_page
from deckle.core.models import (
    BLANK_SOURCE_PATH,
    LayoutSettings,
    SourcePage,
    SourceRef,
)

LETTER = (612.0, 792.0)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


def _settings(**overrides) -> LayoutSettings:
    base = dict(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    base.update(overrides)
    return LayoutSettings(**base)


def _page(i: int, *, skipped: bool = False, blank: bool = False) -> SourcePage:
    return SourcePage(
        ref=SourceRef(
            path=BLANK_SOURCE_PATH if blank else FIXTURE,
            page_index=0 if blank else i,
            sha256="a" * 64,
            width_pt=612.0,
            height_pt=792.0,
        ),
        rotate_deg=0,
        skipped=skipped,
    )


def _pages(n: int) -> list[SourcePage]:
    return [_page(i) for i in range(n)]


def _plan(pages, **overrides):
    return GutterShiftStrategy().impose(pages, _settings(**overrides))


# -- which pages reach the paper as content -------------------------------


def test_content_positions_are_every_page_by_default():
    assert content_positions(_pages(4)) == [0, 1, 2, 3]


def test_a_skipped_page_holds_no_slot():
    pages = [_page(0), _page(1, skipped=True), _page(2)]

    assert content_positions(pages) == [0, 2]


def test_an_inserted_blank_holds_no_slot():
    """A blank is imposed as a filler, and fillers are exactly what the
    walk-back skips."""
    pages = [_page(0), _page(1, blank=True), _page(2)]

    assert content_positions(pages) == [0, 2]


# -- walking a page back to its sheet -------------------------------------


def test_the_first_page_is_on_the_first_sheet():
    pages = _pages(8)

    assert sheet_index_for_page(_plan(pages), pages, 0) == 0


def test_each_page_lands_on_the_sheet_that_carries_it():
    """One page per side, so pages 0 and 1 share sheet 0, 2 and 3 share
    sheet 1, and so on. Getting this wrong by one is the difference between
    the front and the back of a leaf."""
    pages = _pages(8)
    plan = _plan(pages)

    found = [sheet_index_for_page(plan, pages, i) for i in range(8)]

    assert found == [0, 0, 1, 1, 2, 2, 3, 3]


def test_a_skipped_page_does_not_shift_the_pages_after_it():
    """The imposer drops it, so page 2 takes the slot page 1 would have
    had. A mapping that counted document positions instead of content
    would point at the wrong leaf for the whole rest of the book."""
    pages = [_page(0), _page(1, skipped=True), _page(2), _page(3), _page(4)]
    plan = _plan(pages)

    assert sheet_index_for_page(plan, pages, 2) == 0, "page 2 shares sheet 0"
    assert sheet_index_for_page(plan, pages, 3) == 1


def test_a_leading_blank_pushes_the_first_content_page_onto_the_back():
    pages = [_page(0, blank=True), _page(1), _page(2)]
    plan = _plan(pages)

    assert sheet_index_for_page(plan, pages, 1) == 0
    assert sheet_index_for_page(plan, pages, 2) == 1


# -- pages with no slot of their own --------------------------------------


def test_a_blank_falls_back_to_the_nearest_preceding_page():
    """It has no content slot to point at. Landing near it beats refusing
    to move -- the blank is next to the sheet the user wants."""
    pages = [_page(0), _page(1), _page(2, blank=True), _page(3)]
    plan = _plan(pages)

    assert sheet_index_for_page(plan, pages, 2) == sheet_index_for_page(plan, pages, 1)


def test_a_leading_blank_falls_forward_rather_than_off_the_front():
    """There is no preceding page to fall back to."""
    pages = [_page(0, blank=True), _page(1)]
    plan = _plan(pages)

    assert sheet_index_for_page(plan, pages, 0) == 0


def test_the_same_source_page_used_twice_resolves_to_each_appearance():
    """Matching on the reference alone would send both copies to whichever
    sheet carried the first one."""
    pages = [_page(0), _page(1), _page(1), _page(2)]
    plan = _plan(pages)

    assert sheet_index_for_page(plan, pages, 1) == 0, "the first copy is on sheet 0"
    assert sheet_index_for_page(plan, pages, 2) == 1, "the second copy is on sheet 1"


# -- degenerate inputs ----------------------------------------------------


def test_an_empty_document_locates_nothing():
    assert sheet_index_for_page(_plan([]), [], 0) is None


def test_a_document_of_nothing_but_blanks_locates_nothing():
    """No content slot exists anywhere, so there is no honest answer."""
    pages = [_page(0, blank=True), _page(1, blank=True)]

    assert sheet_index_for_page(_plan(pages), pages, 0) is None


def test_an_out_of_range_page_locates_nothing():
    pages = _pages(4)

    assert sheet_index_for_page(_plan(pages), pages, 99) is None
    assert sheet_index_for_page(_plan(pages), pages, -1) is None


# -- signatures -----------------------------------------------------------


def test_folio_pages_are_located_on_their_folded_sheet():
    """Under folio the mapping stops being page // 2 -- page 1 and the last
    page share the outermost sheet. This is where a naive index calculation
    would be wrong and this walk is not."""
    pages = _pages(8)
    plan = SaddleStitchStrategy().impose(
        pages, _settings(paper=(792.0, 612.0), fold_scheme="folio")
    )

    first = sheet_index_for_page(plan, pages, 0)
    last = sheet_index_for_page(plan, pages, 7)

    assert first is not None and last is not None
    assert first == last, "the first and last pages share the outer sheet"


def test_every_folio_page_lands_somewhere_real():
    pages = _pages(8)
    plan = SaddleStitchStrategy().impose(
        pages, _settings(paper=(792.0, 612.0), fold_scheme="folio")
    )
    valid = {sheet.index for sheet in plan.sheets}

    for i in range(8):
        assert sheet_index_for_page(plan, pages, i) in valid
