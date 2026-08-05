"""Finding a document page in an imposed plan.

Imposition scatters a document across sheets: page 7 of a folio might be
the back of sheet 2, and nothing in the plan records where it came from.
``SheetPlan`` is deliberately a description of paper, not a map back to
the manuscript -- the exporter has no reason to care which document page
a slot came from.

Answering "which sheet is this page on?" means matching on what a slot
*does* carry: its ``source_ref``. Counting slots in plan order would work
for the flat gutter-shift layout, where the plan runs in document order,
and would be wrong for every signature -- folio puts the first and last
pages on the same outer sheet, so the second slot on the paper is not the
second page of the book. Matching by reference does not care how the
imposer shuffled anything.

A document may use the same source page twice, so the *n*-th appearance
in the document maps to the *n*-th appearance in the plan.

This module reads a plan and never recomputes one. If it ever disagrees
with the imposer, the imposer is right.
"""

from __future__ import annotations

from deckle.core.models import SheetPlan, SourcePage, is_blank_page

__all__ = ["content_positions", "sheet_index_for_page"]


def content_positions(pages: list[SourcePage]) -> list[int]:
    """Document indices of the pages that reach the paper as content.

    Skipped pages are dropped by the imposer and inserted blanks become
    fillers, so neither occupies a non-filler slot.

    :param pages: the project's pages, in document order.
    :returns: their indices, in order, filtered to those carrying content.
    """
    return [
        i for i, page in enumerate(pages)
        if not page.skipped and not is_blank_page(page)
    ]


def sheet_index_for_page(
    plan: SheetPlan, pages: list[SourcePage], page_index: int
) -> int | None:
    """Which sheet carries the document page at ``page_index``.

    A page with no content of its own -- skipped, or an inserted blank --
    has no slot to point at, so this falls back to the nearest preceding
    page that does. Jumping to roughly the right place beats refusing to
    move: the blank the user clicked is next to the sheet they want.

    :param plan: the imposed plan to search.
    :param pages: the project's pages, in document order. Needed because
        the plan does not record which document page a slot came from.
    :param page_index: the document page, 0-based.
    :returns: the ``Sheet.index`` carrying it, or ``None`` when the plan
        is empty or the document has no content at all.
    """
    if not plan.sheets or not pages or not 0 <= page_index < len(pages):
        return None

    positions = content_positions(pages)
    if not positions:
        return None

    # The clicked page may itself carry no content -- a blank or a skipped
    # page holds no slot. Fall back to the nearest preceding page that
    # does; `positions` is ascending, so that is the last entry at or
    # before the click. A leading blank has nothing before it, so it falls
    # forward instead.
    target_position = positions[0]
    for position in positions:
        if position > page_index:
            break
        target_position = position

    # How many earlier pages carry the same content, so a source page used
    # twice resolves to the right one of its appearances.
    target_ref = pages[target_position].ref
    occurrence = sum(
        1 for position in positions
        if position < target_position and pages[position].ref == target_ref
    )

    seen = 0
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            for output_page in side.pages:
                if output_page.is_filler or output_page.source_ref != target_ref:
                    continue
                if seen == occurrence:
                    return sheet.index
                seen += 1
    return None
