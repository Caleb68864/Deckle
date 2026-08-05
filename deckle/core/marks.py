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
    sheet_h: float, fold_x: float, count: int
) -> tuple[Mark, ...]:
    """``count`` short ticks crossing the fold line, evenly spaced.

    Spacing runs from ``SEWING_MARGIN_PT`` above the tail to the same distance
    below the head. ``count <= 0`` returns ``()`` -- how
    ``settings.sewing_stations = 0`` disables stations without a new boolean.
    A single station is centred on the sheet.
    """
    if count <= 0:
        return ()

    x0 = fold_x - STATION_TICK_PT
    x1 = fold_x + STATION_TICK_PT

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
    """
    step = (sheet_h - 2 * SEWING_MARGIN_PT - ORDER_BAR_PT) / max(1, sig_count - 1)
    y0 = SEWING_MARGIN_PT + sig_index * step
    y1 = y0 + ORDER_BAR_PT
    return Mark(kind="signature_order", x0=fold_x, y0=y0, x1=fold_x, y1=y1)


def fold_line(sheet_h: float, fold_x: float) -> Mark:
    """The cell boundary, head to tail. Dashing is the renderer's concern."""
    return Mark(kind="fold_line", x0=fold_x, y0=0.0, x1=fold_x, y1=sheet_h)
