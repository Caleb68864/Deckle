"""Bindery marks as pure geometry -- sewing stations, signature order, fold lines.

Every function here returns ``Mark`` value objects in sheet points. This module
must stay free of drawing and I/O -- see ``tests/test_core_purity.py`` and the
negative-assertion checks in this sub-spec's acceptance criteria. Stroke width,
dashing, and colour are the renderer's concern, not this module's.

Bookbinder JS carries an open bug (#135, signature order marks wrong under page
rotation) that keeping this geometry pure and rotation-unaware at this layer is
meant to make very hard to reproduce here.
"""

from __future__ import annotations

from typing import Sequence

from deckle.core.models import Mark

SEWING_MARGIN_PT = 36.0
"""Distance from head and tail to the first/last sewing station or order mark.

Matches Bookbinder JS's "(A) Margin" setting.
"""

STATION_TICK_PT = 18.0
"""Half-length of a sewing station tick, measured perpendicular to the fold."""

ORDER_BAR_PT = 24.0
"""Height of a signature order bar, measured along the spine (y-axis)."""


def sewing_stations(
    sheet_h: float,
    fold_x: float,
    count: int,
    *,
    positions: Sequence[float] | None = None,
) -> tuple[Mark, ...]:
    """Short ticks crossing the fold: ``count`` evenly spaced, or exactly
    ``positions``.

    Even spacing runs from ``SEWING_MARGIN_PT`` above the tail to the same
    distance below the head. ``count <= 0`` returns ``()`` -- how
    ``settings.sewing_stations = 0`` disables stations without a new
    boolean. A single station is centred on the sheet.

    ``positions`` wins when given, and ``count`` is then ignored -- a binder
    who has stated where the holes go has not also asked how many there
    should be.

    Nothing here sorts or de-duplicates. This is pure geometry that
    reproduces what it is given; normalising in two places is how the CLI
    and the GUI come to disagree about the same input.

    :param sheet_h: the sheet height in points.
    :param fold_x: the x coordinate of the fold, which the ticks straddle.
    :param count: how many stations. ``<= 0`` returns no marks.
    :param positions: exact y coordinates in points, measured up from the
        tail, already sorted and de-duplicated by whoever accepted them.
        ``None`` falls back to even spacing; ``()`` returns no marks, the
        same as ``count <= 0``.
    :returns: the station ticks, tail to head.
    :raises ValueError: a position falls on or outside a sheet edge.
        Refused rather than clamped: a station at the very edge of the fold
        is a hole in nothing, and silently moving a stated position would
        print a mark somewhere the binder did not ask for.
    """
    x0 = fold_x - STATION_TICK_PT
    x1 = fold_x + STATION_TICK_PT

    if positions is not None:
        for y in positions:
            if not 0.0 < y < sheet_h:
                raise ValueError(
                    f"sewing station at {y:g}pt is not on a {sheet_h:g}pt "
                    "sheet: positions are measured up from the tail, and "
                    "must fall between the two edges"
                )
        return tuple(
            Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y)
            for y in positions
        )

    if count <= 0:
        return ()

    if count == 1:
        y = sheet_h / 2.0
        return (Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y),)

    span = sheet_h - 2 * SEWING_MARGIN_PT
    step = span / (count - 1)
    marks = []
    for i in range(count):
        y = SEWING_MARGIN_PT + i * step
        marks.append(Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y))
    return tuple(marks)


def signature_order_mark(
    sig_index: int, sig_count: int, sheet_h: float, fold_x: float
) -> Mark:
    """A short bar on the spine fold, stepped down by signature index.

    A correctly collated stack of folded gatherings shows a clean diagonal
    staircase down the spine; a misordered one is instantly visible before a
    single stitch. Index 0 sits at the tail margin, ``sig_count - 1`` at the
    head margin.

    :param sig_index: which signature this is, zero-based -- the step's
        position on the staircase.
    :param sig_count: how many signatures the book has, which sets the step
        height. A single-signature book puts its one bar at the tail.
    :param sheet_h: the sheet height in points.
    :param fold_x: the x coordinate of the spine fold.
    :returns: the order bar, lying along the fold.
    """
    step = (sheet_h - 2 * SEWING_MARGIN_PT - ORDER_BAR_PT) / max(1, sig_count - 1)
    y0 = SEWING_MARGIN_PT + sig_index * step
    y1 = y0 + ORDER_BAR_PT
    return Mark(kind="signature_order", x0=fold_x, y0=y0, x1=fold_x, y1=y1)


def cut_lines(
    sheet_w: float,
    sheet_h: float,
    trim_pt: float,
    fore_edges: Sequence[str],
) -> tuple[Mark, ...]:
    """Where the knife goes: the trim depth on every edge that is not the spine.

    Cutting and folding are two different physical operations, and drawing
    one as the other is actively wrong -- which is why this is a distinct
    ``Mark`` kind and not a fold line at a different place. After sewing,
    the block is trimmed square on head, tail and fore-edge; a line at the
    trim depth tells you where the plough or knife goes, and shows you
    before you cut whether any text is inside it. ``schedule`` already
    estimates fore-edge creep, and this is that estimate made physical.

    The spine is never included. It is the bound edge, and a cut there
    takes the book apart.

    :param sheet_w: the sheet width in points.
    :param sheet_h: the sheet height in points.
    :param trim_pt: how far in from each edge the cut falls. ``<= 0``
        returns ``()``, which is how ``trim_pt = 0`` disables cut lines
        without a second boolean -- the same shape as
        ``settings.sewing_stations = 0``.
    :param fore_edges: which vertical edges are fore-edges: ``("right",)``
        or ``("left",)`` under gutter shift, and **both** under folio,
        where the fold is in the middle and each leaf has its own.
    :returns: the cut lines -- head and tail always, plus one per fore
        edge. Each spans the whole sheet, so a straightedge can be laid
        along it; a mark that stopped short would have to be extrapolated
        by eye at the exact moment that is hardest to do accurately.
    :raises ValueError: ``trim_pt`` is deep enough that opposing cuts meet
        or cross. Two crossed lines describe no region to keep, and
        drawing them would confidently instruct a cut that discards the
        book.
    """
    if trim_pt <= 0:
        return ()
    if 2 * trim_pt >= min(sheet_w, sheet_h):
        raise ValueError(
            f"trim of {trim_pt}pt leaves nothing of a "
            f"{sheet_w:g}x{sheet_h:g}pt sheet: opposing cuts meet or cross"
        )

    marks = [
        Mark(kind="cut_line", x0=0.0, y0=trim_pt, x1=sheet_w, y1=trim_pt),
        Mark(
            kind="cut_line",
            x0=0.0,
            y0=sheet_h - trim_pt,
            x1=sheet_w,
            y1=sheet_h - trim_pt,
        ),
    ]
    for edge in fore_edges:
        x = trim_pt if edge == "left" else sheet_w - trim_pt
        marks.append(Mark(kind="cut_line", x0=x, y0=0.0, x1=x, y1=sheet_h))
    return tuple(marks)


def fold_line(sheet_h: float, fold_x: float) -> Mark:
    """The cell boundary, head to tail. Dashing is the renderer's concern.

    :param sheet_h: the sheet height in points; the line spans all of it.
    :param fold_x: the x coordinate of the fold.
    :returns: the fold line.
    """
    return Mark(kind="fold_line", x0=fold_x, y0=0.0, x1=fold_x, y1=sheet_h)
