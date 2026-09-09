"""Choosing a page range at import: ``apply_page_selection``.

A public-domain scan is not a book. A 296-page book arrives as a 312-page
PDF: scanner target, bookplate, two blank leaves, colophon, advertisement,
second target. The whole point of this function is that it *narrows* a
document without deleting anything -- a skipped page keeps its slot, shows
as ``(skipped)`` in the grid, and comes back with one click, none of which
is true of a page that was removed.
"""

from __future__ import annotations

import pytest

from deckle.core.loader import apply_page_selection
from deckle.core.models import SourcePage, SourceRef


def _pages(n: int, skipped: set[int] | None = None) -> list[SourcePage]:
    skipped = skipped or set()
    return [
        SourcePage(
            ref=SourceRef(
                path="/scan/book.pdf", page_index=i, sha256="a" * 64,
                width_pt=612.0, height_pt=792.0,
            ),
            rotate_deg=0,
            skipped=i in skipped,
        )
        for i in range(n)
    ]


def test_keeping_a_range_skips_everything_else():
    result = apply_page_selection(_pages(10), keep=[6, 7, 8])

    assert [p.skipped for p in result] == [
        True, True, True, True, True, True, False, False, False, True
    ]
    assert len(result) == 10


def test_skipping_a_range_leaves_the_rest_alone():
    result = apply_page_selection(_pages(10), skip=[0, 1])

    assert [p.skipped for p in result] == [True, True] + [False] * 8


def test_nothing_is_ever_removed():
    """The reversibility the whole design rests on."""
    pages = _pages(10)

    result = apply_page_selection(pages, keep=[3])

    assert len(result) == len(pages)
    assert [p.ref.page_index for p in result] == list(range(10))


def test_the_input_is_not_mutated():
    pages = _pages(4)

    apply_page_selection(pages, keep=[0])

    assert all(p.skipped is False for p in pages)


def test_keeping_never_unskips():
    """This narrows a document; it does not restore one. A page the user
    already skipped in Arrange and then names in a keep range stays
    skipped."""
    pages = _pages(4, skipped={1})

    result = apply_page_selection(pages, keep=[0, 1])

    assert result[1].skipped is True


def test_neither_direction_leaves_the_pages_alone():
    result = apply_page_selection(_pages(3))

    assert [p.skipped for p in result] == [False, False, False]


def test_both_directions_at_once_is_refused():
    with pytest.raises(ValueError, match="not both"):
        apply_page_selection(_pages(4), keep=[0], skip=[1])


def test_a_page_the_document_does_not_have_is_refused():
    """Refused rather than ignored: a range that names nothing is a typo,
    and silently producing a different book is the failure the feature
    exists to prevent. The message is 1-based, because that is what the
    user typed."""
    with pytest.raises(ValueError) as excinfo:
        apply_page_selection(_pages(10), keep=[400])

    assert "no page 401" in str(excinfo.value)
    assert "10 page(s)" in str(excinfo.value)


def test_a_negative_index_is_refused_rather_than_wrapping():
    with pytest.raises(ValueError):
        apply_page_selection(_pages(10), skip=[-1])
