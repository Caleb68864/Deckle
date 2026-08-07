"""Generate a document whose only content is its own page order.

This exists to answer a question about Deckle, not about a user's book.
``fold_scheme="folio"`` ships marked experimental because its page
ordering has never been checked against a physically folded sheet, and
quarto is blocked behind that same question. The way that check is
actually made is to print sequence numbers on scrap, fold it, and read
it -- Bookbinder JS keeps a hand-made ``example_page_numbers.pdf`` for
precisely this.

**A generator, not a stamper.** The numbers go onto a synthetic *source*
which then runs through the ordinary import-impose-export path, so what
gets verified is the real pipeline. Stamping numbers onto imposed output
would instead verify a second code path that could disagree with the
first, which is the failure this is meant to detect rather than commit.

Numerals are drawn in Helvetica, one of the PDF base-14 fonts, so nothing
is embedded and no font licence is involved.
"""

from __future__ import annotations

import pikepdf
from pikepdf import Name
from pikepdf.canvas import ContentStreamBuilder

LETTER_PT = (612.0, 792.0)

_NUMERAL_SIZE_PT = 144.0
"""Big enough to read at arm's length on a bench, folded."""

_LABEL_SIZE_PT = 14.0

_HEAD_INSET_PT = 36.0
"""How far the HEAD label sits below the top edge -- inside the printable
area of any ordinary printer, which a label at the very edge would not be."""

_UNDERLINE_DROP_PT = 18.0
_UNDERLINE_HALF_WIDTH_PT = 70.0


def _draw_page(page: pikepdf.Page, number: int, width: float, height: float) -> None:
    """Draw one numbered page: numeral, underline, and a HEAD label.

    All three matter, and the last two are what make a fold conclusive:

    - The **numeral** says which leaf this is.
    - The **underline** disambiguates a leaf that arrives upside down.
      ``6`` and ``9`` are each other rotated, and ``8`` is its own
      rotation, so a bare numeral cannot always tell you.
    - The **HEAD label** names the top edge outright, because a
      symmetrical layout can still be read the wrong way up at a glance
      even with an underline.
    """
    font = pikepdf.Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
    )
    font_name = page.add_resource(font, Name.Font, prefix="Ft")

    text = str(number)
    # Helvetica digits are 0.556em wide, which is enough to centre by --
    # no font metrics to load, and being a point or two off centre does
    # not matter for something whose whole job is to be legible.
    text_width = _NUMERAL_SIZE_PT * 0.556 * len(text)
    numeral_x = (width - text_width) / 2.0
    numeral_y = (height - _NUMERAL_SIZE_PT) / 2.0

    builder = ContentStreamBuilder()

    builder.push()
    builder.begin_text()
    builder.set_text_font(font_name, _NUMERAL_SIZE_PT)
    builder.move_cursor(numeral_x, numeral_y)
    builder.show_text(text)
    builder.end_text()
    builder.pop()

    builder.push()
    builder.set_line_width(3.0)
    underline_y = numeral_y - _UNDERLINE_DROP_PT
    builder.line(
        width / 2.0 - _UNDERLINE_HALF_WIDTH_PT,
        underline_y,
        width / 2.0 + _UNDERLINE_HALF_WIDTH_PT,
        underline_y,
    )
    builder.stroke_and_close()
    builder.pop()

    builder.push()
    builder.begin_text()
    builder.set_text_font(font_name, _LABEL_SIZE_PT)
    builder.move_cursor(width / 2.0 - 20.0, height - _HEAD_INSET_PT)
    builder.show_text("HEAD")
    builder.end_text()
    builder.pop()

    page.contents_add(b"q\n" + builder.build() + b"Q\n")


def make_numbered_pdf(
    out_path: str,
    pages: int,
    page_size: tuple[float, float] = LETTER_PT,
) -> None:
    """Write a PDF of ``pages`` pages, each carrying its own number.

    Run the result through ``deckle export`` with the scheme you want to
    check, print it on scrap, fold it, and read the numbers. An eight-page
    folio should put 8 and 1 on the outside of the sheet and 4 and 5 at
    the centre; anything else is an ordering defect, and it takes about
    five minutes to find out.

    :param out_path: the PDF to write.
    :param pages: how many pages, numbered 1 to ``pages`` as a reader
        counts them.
    :param page_size: ``(width, height)`` in points. Match it to the
        source you are standing in for, since page size feeds the scale
        the imposer picks.
    :returns: nothing.
    :raises ValueError: ``pages`` is less than one -- there is no document
        to make, and an empty PDF would verify nothing while looking like
        it had worked.
    """
    if pages < 1:
        raise ValueError(f"pages must be at least 1, got {pages}")

    width, height = page_size
    pdf = pikepdf.Pdf.new()
    try:
        for number in range(1, pages + 1):
            page = pdf.add_blank_page(page_size=(width, height))
            _draw_page(page, number, width, height)
        pdf.save(out_path)
    finally:
        pdf.close()
