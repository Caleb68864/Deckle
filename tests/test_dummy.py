"""The numbered dummy: a document whose only content is its own page order.

This exists to answer a question about Deckle rather than about a user's
book. Folio ships experimental because its page ordering has never been
checked against a physically folded sheet, and quarto is blocked behind
the same question. Stamping sequence numbers on scrap and folding it is
how that check is actually done -- Bookbinder JS keeps a hand-made
`example_page_numbers.pdf` for exactly this.

So this is a generator, not a stamper. Numbers go on a synthetic source
that then runs through the ordinary pipeline, which means the thing being
verified is the real imposition path and not a special "test mode" that
could diverge from it.

Every page carries three things, and the second and third are the ones
that make folding conclusive:

- the page number, large enough to read across a bench;
- an underline, because a folded leaf can arrive upside down and 6, 9 and
  8 are all ambiguous without one;
- a HEAD label at the top edge, so a rotated leaf is obvious even when
  the numeral happens to be symmetrical.
"""

from __future__ import annotations

import os

import pypdfium2
import pytest

from deckle.core.dummy import make_numbered_pdf


def _page_text(path: str, index: int) -> str:
    doc = pypdfium2.PdfDocument(path)
    try:
        return doc[index].get_textpage().get_text_bounded()
    finally:
        doc.close()


def _page_count(path: str) -> int:
    doc = pypdfium2.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


def test_it_writes_the_requested_number_of_pages(tmp_path):
    out = os.path.join(str(tmp_path), "dummy.pdf")

    make_numbered_pdf(out, pages=16)

    assert _page_count(out) == 16


def test_pages_are_numbered_from_one_as_a_reader_counts(tmp_path):
    out = os.path.join(str(tmp_path), "dummy.pdf")

    make_numbered_pdf(out, pages=4)

    assert "1" in _page_text(out, 0)
    assert "4" in _page_text(out, 3)


def test_every_page_says_which_edge_is_the_head(tmp_path):
    """A folded leaf can arrive upside down, and an underlined numeral
    still leaves a symmetrical layout ambiguous at a glance."""
    out = os.path.join(str(tmp_path), "dummy.pdf")

    make_numbered_pdf(out, pages=2)

    for index in range(2):
        assert "HEAD" in _page_text(out, index)


def test_the_page_size_is_honoured(tmp_path):
    out = os.path.join(str(tmp_path), "dummy.pdf")

    make_numbered_pdf(out, pages=1, page_size=(300.0, 500.0))

    doc = pypdfium2.PdfDocument(out)
    try:
        assert doc[0].get_size() == (300.0, 500.0)
    finally:
        doc.close()


def test_asking_for_no_pages_is_refused(tmp_path):
    out = os.path.join(str(tmp_path), "dummy.pdf")

    with pytest.raises(ValueError):
        make_numbered_pdf(out, pages=0)


def test_the_dummy_survives_the_real_imposition_path(tmp_path):
    """The point of generating a source rather than stamping output: what
    gets verified is the ordinary pipeline, not a parallel one."""
    from deckle.cli import main

    src = os.path.join(str(tmp_path), "dummy.pdf")
    out = os.path.join(str(tmp_path), "folio.pdf")
    make_numbered_pdf(src, pages=8)

    rc = main([
        "export", src, "-o", out,
        "--fold-scheme", "folio", "--paper", "11x8.5in",
    ])

    assert rc == 0
    # Four leaves per folio sheet: 8 pages -> 2 sheets -> 4 faces.
    assert _page_count(out) == 4


def test_the_numbers_land_in_the_saddle_stitch_order(tmp_path):
    """The check this whole feature exists to make possible, run here on
    the text rather than on paper: an 8-page folio puts 8 and 1 on the
    outermost sheet's front, and 4 and 5 innermost."""
    from deckle.cli import main

    src = os.path.join(str(tmp_path), "dummy.pdf")
    out = os.path.join(str(tmp_path), "folio.pdf")
    make_numbered_pdf(src, pages=8)
    main([
        "export", src, "-o", out,
        "--fold-scheme", "folio", "--paper", "11x8.5in",
    ])

    faces = [_page_text(out, i) for i in range(4)]
    numbers = [
        [tok for tok in text.split() if tok.isdigit()] for text in faces
    ]
    assert numbers[0] == ["8", "1"], numbers
    assert numbers[-1] == ["4", "5"], numbers
