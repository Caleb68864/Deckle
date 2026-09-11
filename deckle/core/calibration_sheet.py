"""The printed half of the calibration wizard: a duplex test sheet.

Deckle prints two-sided books by printing every front, asking the operator
to turn the stack over, and printing every back. Four things decide
whether the backs land on the right fronts the right way up, and **no
driver reports any of them**: ``flip_axis``, ``reverse_stack``,
``output_face`` and ``feed_edge``. They are the fields of
:class:`~deckle.core.profiles.PrinterProfile`, and until somebody prints a
target and looks at it they are guesses inherited from a built-in preset.

The README records the calibration *wizard* as not built. This is its
printed half. It is usable on its own -- generate it, print it, read it,
and ``deckle-cli profile set`` the answers -- and it is what a wizard
would put on paper when there is one.

**Nothing here is imposed, and that is the whole point.**

``deckle.core.printing.plan_passes`` is the code under test. If this sheet
were produced by running pages through it, the operator's report would
say only that Deckle agrees with itself -- a probe that replicates its
own subject, and worth nothing. So the pages come out in plain sequential
order: page N says which sheet it is and which side of it, and no sheet
ordering, no reversal and no half turn is applied anywhere in this module.
The model is then checked *against* what the operator saw, never used to
produce it. ``tests/test_calibration_sheet.py`` holds a structural guard
that keeps it that way.

**Both orientations, always.** The flip rule inverts between them -- a
portrait sheet's vertical edge is its long one and a landscape sheet's is
its short one -- so one orientation establishes half the answer and
invites the operator to generalise the wrong way. Getting portrait
backwards is exactly the defect the documentation carried until
2026-09-11.

**The sheet never states what it expects.** Every question asks what the
operator *sees*. A sheet that said "the backs should be upright" would be
answered "yes" by a tired person at a printer under bad light, and the
one observation the whole exercise exists to collect would be lost.

Drawn in Helvetica and Helvetica-Bold, two of the PDF base-14 fonts, so
nothing is embedded and no font licence is involved -- the same choice
:mod:`deckle.core.dummy` makes and for the same reason.
"""

from __future__ import annotations

import pikepdf
from pikepdf import Name
from pikepdf.canvas import ContentStreamBuilder

from deckle.core.paths import atomic_output

LETTER_PT = (612.0, 792.0)

MM_PT = 72.0 / 25.4
"""One millimetre in PDF points. The registration ticks are in millimetres
because that is what the operator's ruler is in, and what
``profile set --back-offset-x`` converts from."""

SHEETS = 4
"""Four sheets, eight faces.

Two cannot tell a reversal from a coincidence: with sheets 1 and 2, "the
backs came out in the other order" and "the printer happened to hand them
back the way it received them" produce the same stack. Four makes the
order unambiguous, and the fourth sheet also gives the operator a spare
if one jams.
"""

_BAND_INSET_PT = 12.0
_BAND_HEIGHT_PT = 16.0
_REG_ARM_PT = 40.0
_REG_TICKS_MM = 10

_FACE_LABELS = ("FRONT", "BACK")


# -- low-level drawing -------------------------------------------------
#
# `pikepdf.canvas.ContentStreamBuilder` has no general path fill -- only
# `append_rectangle`/`fill` and `line`/`stroke_and_close` -- so the one
# filled triangle goes in as raw operators through `extend`. Everything
# else uses the builder's own methods.


def _fonts(page: pikepdf.Page) -> tuple[Name, Name]:
    """Add Helvetica and Helvetica-Bold to ``page`` and return their names."""
    regular = page.add_resource(
        pikepdf.Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica),
        Name.Font,
        prefix="Ft",
    )
    bold = page.add_resource(
        pikepdf.Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/Helvetica-Bold")
        ),
        Name.Font,
        prefix="Ft",
    )
    return regular, bold


def _text_width(text: str, size: float, bold: bool) -> float:
    """A good-enough width for centring, with no font metrics to load.

    Helvetica's digits are exactly 0.556em and its uppercase averages near
    0.66em (0.72 for the bold). Being a point or two off centre does not
    matter for something whose entire job is to be legible across a room;
    loading AFM metrics to do better would add a dependency for nothing.
    """
    per_em = 0.72 if bold else 0.66
    return size * per_em * len(text)


def _show(
    builder: ContentStreamBuilder,
    font: Name,
    size: float,
    x: float,
    y: float,
    text: str,
) -> None:
    builder.push()
    builder.begin_text()
    builder.set_text_font(font, size)
    builder.move_cursor(x, y)
    builder.show_text(text)
    builder.end_text()
    builder.pop()


def _show_centred(
    builder: ContentStreamBuilder,
    font: Name,
    size: float,
    centre_x: float,
    y: float,
    text: str,
    bold: bool,
) -> None:
    _show(builder, font, size, centre_x - _text_width(text, size, bold) / 2.0, y, text)


def _filled_rect(
    builder: ContentStreamBuilder, x: float, y: float, w: float, h: float
) -> None:
    builder.push()
    builder.set_fill_color(0.0, 0.0, 0.0)
    builder.append_rectangle(x, y, w, h)
    builder.fill()
    builder.pop()


def _filled_triangle(
    builder: ContentStreamBuilder,
    apex_x: float,
    apex_y: float,
    half_base: float,
    height: float,
) -> None:
    """A solid triangle pointing **up**, apex at ``(apex_x, apex_y)``.

    The up-marker. A sheet that came back rotated shows this pointing
    down, which is readable across a room -- which a line of small text
    is not, and the room in question is usually a badly lit one with a
    printer in it.
    """
    base_y = apex_y - height
    left_x = apex_x - half_base
    right_x = apex_x + half_base
    builder.push()
    builder.set_fill_color(0.0, 0.0, 0.0)
    builder.extend(
        f"{left_x:.2f} {base_y:.2f} m "
        f"{right_x:.2f} {base_y:.2f} l "
        f"{apex_x:.2f} {apex_y:.2f} l h f\n".encode("ascii")
    )
    builder.pop()


def _registration_cross(
    builder: ContentStreamBuilder, centre_x: float, centre_y: float
) -> None:
    """Crosshairs with millimetre ticks, at the sheet's exact centre.

    **The centre is not decoration.** A flip about any centre axis maps
    the centre to itself, so a cross there coincides with the back's cross
    whatever the operator did with the stack -- and any gap between the
    two, held up to a light, is the registration offset itself rather than
    a consequence of where the mark was put. An off-centre mark would
    measure the flip instead.
    """
    builder.push()
    builder.set_line_width(0.7)
    builder.line(centre_x - _REG_ARM_PT, centre_y, centre_x + _REG_ARM_PT, centre_y)
    builder.stroke_and_close()
    builder.line(centre_x, centre_y - _REG_ARM_PT, centre_x, centre_y + _REG_ARM_PT)
    builder.stroke_and_close()

    for step in range(1, _REG_TICKS_MM + 1):
        offset = step * MM_PT
        length = 7.0 if step % 5 == 0 else 3.0
        for sign in (-1.0, 1.0):
            builder.line(
                centre_x + sign * offset, centre_y - length,
                centre_x + sign * offset, centre_y + length,
            )
            builder.stroke_and_close()
            builder.line(
                centre_x - length, centre_y + sign * offset,
                centre_x + length, centre_y + sign * offset,
            )
            builder.stroke_and_close()
    builder.pop()


# -- one test face -----------------------------------------------------


def _draw_face(
    page: pikepdf.Page,
    width: float,
    height: float,
    sheet_number: int,
    side: str,
    strip: tuple[str, ...] = (),
) -> None:
    """One of the eight test faces.

    Everything on it is an *observation aid*: a marker whose state the
    operator reads off and reports. Nothing states what that state ought
    to be.

    :param page: the page to draw on.
    :param width: page width in points.
    :param height: page height in points.
    :param sheet_number: 1-based, as the operator counts sheets.
    :param side: ``"FRONT"`` or ``"BACK"``.
    :param strip: extra lines printed small below the centre. Sheet 1's
        front carries the essential instructions here, so that whoever
        picks the stack out of the tray has them in hand without going
        back to the file or the guide.
    """
    regular, bold = _fonts(page)
    builder = ContentStreamBuilder()
    centre_x = width / 2.0
    centre_y = height / 2.0

    # -- the edge marker, on one named edge ---------------------------
    # A solid band across the TOP edge only. Asymmetric on purpose: a
    # face that comes back turned shows this band along the bottom, and
    # that is visible without reading anything.
    _filled_rect(
        builder,
        _BAND_INSET_PT,
        height - _BAND_INSET_PT - _BAND_HEIGHT_PT,
        width - 2 * _BAND_INSET_PT,
        _BAND_HEIGHT_PT,
    )

    # -- all four edges named, so the operator can say what they saw ---
    # Naming the edges is a coordinate system, not an expectation. Without
    # it "the marked edge" has to be described in the operator's own words
    # and the report needs a conversation to interpret.
    label_size = 15.0
    _show_centred(
        builder, bold, label_size, centre_x,
        height - _BAND_INSET_PT - _BAND_HEIGHT_PT - 22.0, "TOP EDGE", True,
    )
    _show_centred(
        builder, bold, label_size, centre_x, _BAND_INSET_PT + 8.0, "BOTTOM EDGE", True
    )
    _show(builder, bold, label_size, _BAND_INSET_PT + 4.0, centre_y, "LEFT")
    _show(
        builder, bold, label_size,
        width - _BAND_INSET_PT - 4.0 - _text_width("RIGHT", label_size, True),
        centre_y, "RIGHT",
    )

    # -- the centre: registration -------------------------------------
    _registration_cross(builder, centre_x, centre_y)

    # -- above the cross: up-marker and sheet number -------------------
    # The markers are split either side of the centre rather than stacked
    # above it. The cross has to be at the exact middle (see
    # `_registration_cross`), which leaves two zones, and on a landscape
    # sheet the upper one is a third shorter -- everything stacked into it
    # is either cramped or collides with the crosshair.
    #
    # Sizes scale with the zone so the same code lays out both
    # orientations. `scale` is 1.0 on portrait letter.
    zone_top = height - _BAND_INSET_PT - _BAND_HEIGHT_PT - 44.0
    upper = max(zone_top - (centre_y + _REG_ARM_PT + 18.0), 60.0)
    scale = min(upper / 268.0, 1.0)

    triangle_h = 70.0 * scale
    # Not scaled: at landscape's 0.66 a scaled "SHEET" is 9pt, which is
    # the one thing on the face that stops being readable at arm's length.
    label_pt = 13.0
    numeral_size = 150.0 * scale

    cursor = zone_top
    _filled_triangle(
        builder, centre_x, cursor, half_base=triangle_h, height=triangle_h
    )
    cursor -= triangle_h + 14.0 + 4.0 * scale
    _show_centred(builder, regular, label_pt, centre_x, cursor, "SHEET", False)
    # Helvetica-Bold's digits are 0.717em tall, so this drops the baseline
    # far enough that the numeral's top sits just under the word above it
    # -- a fixed gap leaves a hand-sized hole at 150pt and none at 60pt.
    cursor -= 6.0 * scale + numeral_size * 0.717
    _show_centred(builder, bold, numeral_size, centre_x, cursor, str(sheet_number), True)

    # -- below the cross: which side, and sheet 1's strip --------------
    cursor = centre_y - _REG_ARM_PT - 20.0 - 56.0 * scale * 0.717
    _show_centred(builder, bold, 56.0 * scale, centre_x, cursor, side, True)

    cursor -= 34.0 * scale
    for line in strip:
        _show_centred(builder, regular, 11.0, centre_x, cursor, line, False)
        cursor -= 15.0

    # Repeated small, just above the bottom edge: in a fanned output tray
    # only a strip of each sheet is visible, and it is the wrong strip.
    _show_centred(
        builder, regular, 12.0, centre_x, _BAND_INSET_PT + 44.0,
        f"sheet {sheet_number} {side.lower()}", False,
    )

    page.contents_add(b"q\n" + builder.build() + b"Q\n")


# -- the cover ---------------------------------------------------------

_COVER_TITLE = "DECKLE DUPLEX CALIBRATION SHEET"

#: ``(indent, size, bold, text)``. Laid out by hand rather than wrapped,
#: because this is the one page whose line breaks matter: it is read at a
#: printer, standing up, once.
_COVER_BODY: tuple[tuple[float, float, bool, str], ...] = (
    (0, 8, False, ""),
    (0, 11, True, "WHAT THIS IS"),
    (0, 10, False, "Deckle prints two-sided books by printing every front, asking you to turn the"),
    (0, 10, False, "stack over, and printing every back. Four things decide whether the backs land"),
    (0, 10, False, "on the right fronts the right way up, and no printer driver reports any of them."),
    (0, 10, False, "This sheet finds them out by having you look at paper."),
    (0, 10, False, "Nothing in this file is imposed: the pages come out in plain order, so what you"),
    (0, 10, False, "report is evidence about your printer and not a check of Deckle against itself."),
    (0, 8, False, ""),
    (0, 11, True, "HOW TO PRINT IT"),
    (0, 10, False, "1.  Print pages 2-5 of this file at 100%. Turn OFF \"fit to page\" and any"),
    (14, 10, False, "\"scale to fit\" or \"shrink oversized pages\" option."),
    (0, 10, False, "2.  Take the stack out of the output tray. Do NOT reorder or rotate it."),
    (0, 10, False, "3.  Turn the stack over the way you would to print its other side, and put it"),
    (14, 10, False, "back in the paper tray."),
    (0, 10, False, "4.  Print pages 6-9 of this file, same settings."),
    (0, 10, False, "You now have four double-sided sheets."),
    (0, 8, False, ""),
    (0, 11, True, "WHAT TO WRITE DOWN"),
    (0, 9, False, "Answer what you SEE. Do not correct anything, and do not reprint to tidy it up --"),
    (0, 9, False, "a wrong-looking result is the result."),
    (0, 6, False, ""),
    (0, 10, False, "A.  After step 1, the printed side of the sheets in the output tray faced"),
    (14, 10, True, "UP  /  DOWN                                      -> output_face"),
    (0, 6, False, ""),
    (0, 10, False, "B.  The edge of the paper that went into the printer first was the sheet's"),
    (14, 10, True, "TOP  /  BOTTOM edge                              -> feed_edge"),
    (0, 6, False, ""),
    (0, 10, False, "C.  After step 1, the sheet lying on TOP of the output stack was number"),
    (14, 10, True, "1    2    3    4                                 -> reverse_stack"),
    (0, 6, False, ""),
    (0, 10, False, "D.  On each finished sheet, the BACK number that landed on it:"),
    (14, 10, True, "front 1 -> back ____      front 2 -> back ____"),
    (14, 10, True, "front 3 -> back ____      front 4 -> back ____    -> reverse_stack"),
    (0, 6, False, ""),
    (0, 10, False, "E.  Hold a finished sheet so its FRONT reads upright, with the solid black band"),
    (14, 10, False, "along the top. Now turn it over left-to-right, like a page in a book."),
    (14, 10, False, "On the BACK, the solid black band lies along the"),
    (28, 10, True, "TOP  /  BOTTOM edge"),
    (14, 10, False, "and the big triangle points"),
    (28, 10, True, "UP  /  DOWN                                      -> flip_axis"),
    (0, 6, False, ""),
    (0, 10, False, "F.  Hold sheet 1 up to a bright light so both crosses show through. The BACK"),
    (14, 10, False, "cross sits  ______ mm to the left/right  and  ______ mm up/down"),
    (14, 10, False, "of the FRONT cross. Ticks on the crosshair arms are 1 mm, long ones 5 mm."),
    (28, 10, True, "-> back_offset_x_pt / back_offset_y_pt"),
    (0, 6, False, ""),
    (0, 10, False, "G.  This file is the  PORTRAIT  /  LANDSCAPE  one (circle one)."),
    (0, 8, False, ""),
    (0, 9, True, "Do all of the above for the portrait file AND the landscape file."),
    (0, 9, False, "The rule that turns these answers into a profile inverts between the two, so one"),
    (0, 9, False, "orientation answers only half of it."),
)


def _draw_cover(
    page: pikepdf.Page, width: float, height: float, orientation: str
) -> None:
    """The instructions and the results grid.

    Page 1 rather than an appendix: the operator opens the file, reads
    what to do, and prints the ranges it names. Sheet 1's front repeats
    the essentials so the stack in their hand is self-describing too.
    """
    regular, bold = _fonts(page)
    builder = ContentStreamBuilder()
    left = 54.0
    y = height - 54.0

    _show(builder, bold, 17.0, left, y, _COVER_TITLE)
    y -= 16.0
    _show(
        builder, regular, 9.5, left, y,
        f"{orientation.upper()}  --  the printed half of the calibration wizard. "
        "Do not print this page in either pass.",
    )
    y -= 8.0
    builder.push()
    builder.set_line_width(1.2)
    builder.line(left, y, width - left, y)
    builder.stroke_and_close()
    builder.pop()
    y -= 16.0

    for indent, size, is_bold, text in _COVER_BODY:
        if text:
            _show(builder, bold if is_bold else regular, size, left + indent, y, text)
        y -= size + 3.0

    page.contents_add(b"q\n" + builder.build() + b"Q\n")


# -- the document ------------------------------------------------------

#: Printed small on sheet 1's front, below the registration cross.
_SHEET_ONE_STRIP = (
    "PASS 1 = pages 2-5 of this file.  Then turn the stack over and print pages 6-9.",
    "Full instructions and the grid to fill in are on page 1.",
    "Record what you see. Do not reprint to tidy up the result.",
)


def page_plan(sheets: int = SHEETS) -> tuple[tuple[str, int, str], ...]:
    """What lands on each page, in order, as ``(kind, sheet, side)``.

    Exposed so the tests can state the page order without restating the
    loop that builds it, and so that the *absence* of any reordering is
    legible: the fronts are ``1..n`` ascending and the backs are ``1..n``
    ascending, with no reversal and no rotation anywhere. That is what
    makes the operator's report independent of
    :func:`~deckle.core.printing.plan_passes`.

    :param sheets: how many sheets the document covers.
    :returns: one entry per page, page 1 first.
    """
    plan: list[tuple[str, int, str]] = [("cover", 0, "")]
    for side in _FACE_LABELS:
        for number in range(1, sheets + 1):
            plan.append(("face", number, side))
    return tuple(plan)


def make_calibration_pdf(
    out_path: str,
    page_size: tuple[float, float] = LETTER_PT,
    landscape: bool = False,
    sheets: int = SHEETS,
) -> None:
    """Write the duplex calibration document.

    :param out_path: the PDF to write.
    :param page_size: ``(width, height)`` of the paper, given **upright**.
    :param landscape: swap ``page_size`` so the sheet feeds the long way.
    :param sheets: how many sheets to test. Four by default -- see
        :data:`SHEETS` for why two is not enough.
    :returns: nothing.
    :raises ValueError: ``sheets`` is below two, which cannot distinguish
        a reversed stack from a preserved one and would produce a
        confident-looking sheet that answers nothing.
    """
    if sheets < 2:
        raise ValueError(
            f"sheets must be at least 2 to tell a reversed stack from a "
            f"preserved one, got {sheets}"
        )

    width, height = page_size
    if landscape:
        width, height = height, width
    orientation = "landscape" if landscape else "portrait"

    pdf = pikepdf.Pdf.new()
    try:
        for kind, number, side in page_plan(sheets):
            page = pdf.add_blank_page(page_size=(width, height))
            if kind == "cover":
                _draw_cover(page, width, height, orientation)
            else:
                _draw_face(
                    page, width, height, number, side,
                    strip=_SHEET_ONE_STRIP if (number == 1 and side == "FRONT") else (),
                )
        # Written beside the target and renamed, so a failure partway
        # leaves whatever was there -- the guarantee `export`, `schedule`,
        # `crop-preview` and `dummy` already give.
        with atomic_output(out_path) as scratch:
            pdf.save(scratch)
    finally:
        pdf.close()
