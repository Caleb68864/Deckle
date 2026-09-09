"""Tests for deckle.core.marks -- bindery marks as pure geometry."""

from __future__ import annotations

import pytest

from deckle.core.marks import (
    SEWING_MARGIN_PT,
    fold_line,
    sewing_stations,
    signature_order_mark,
)
from deckle.core.models import Mark

LETTER_LANDSCAPE = (792.0, 612.0)  # width, height -- landscape letter
SHEET_H = LETTER_LANDSCAPE[1]
FOLD_X = LETTER_LANDSCAPE[0] / 2.0


def _midpoint_y(mark: Mark) -> float:
    return (mark.y0 + mark.y1) / 2.0


def test_sewing_stations_count_and_kind() -> None:
    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)
    assert len(stations) == 3
    assert all(m.kind == "sewing_station" for m in stations)


def test_sewing_stations_evenly_spaced() -> None:
    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)
    ys = [_midpoint_y(m) for m in stations]
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert gaps[0] == pytest.approx(gaps[1])


def test_sewing_stations_first_and_last_margins() -> None:
    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)
    ys = sorted(_midpoint_y(m) for m in stations)
    assert ys[0] == pytest.approx(SEWING_MARGIN_PT)
    assert ys[-1] == pytest.approx(612.0 - SEWING_MARGIN_PT)


def test_sewing_stations_straddle_fold() -> None:
    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)
    for m in stations:
        lo, hi = sorted((m.x0, m.x1))
        assert lo <= 396.0 <= hi


def test_sewing_stations_zero_count_returns_empty() -> None:
    assert sewing_stations(sheet_h=612.0, fold_x=396.0, count=0) == ()


def test_sewing_stations_negative_count_returns_empty() -> None:
    assert sewing_stations(sheet_h=612.0, fold_x=396.0, count=-2) == ()


def test_sewing_stations_count_one_is_centred() -> None:
    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=1)
    assert len(stations) == 1
    assert _midpoint_y(stations[0]) == pytest.approx(306.0)


def test_signature_order_mark_monotonically_increasing() -> None:
    sig_count = 5
    ys = [
        signature_order_mark(i, sig_count, sheet_h=612.0, fold_x=396.0).y0
        for i in range(sig_count)
    ]
    assert all(b > a for a, b in zip(ys, ys[1:]))


def test_signature_order_mark_endpoints_at_margins() -> None:
    sig_count = 4
    first = signature_order_mark(0, sig_count, sheet_h=612.0, fold_x=396.0)
    last = signature_order_mark(sig_count - 1, sig_count, sheet_h=612.0, fold_x=396.0)
    assert first.y0 == pytest.approx(SEWING_MARGIN_PT)
    assert last.y0 == pytest.approx(612.0 - SEWING_MARGIN_PT - (last.y1 - last.y0))


def test_signature_order_mark_single_signature_no_zero_division() -> None:
    mark = signature_order_mark(0, 1, sheet_h=612.0, fold_x=396.0)
    assert mark.kind == "signature_order"
    assert mark.y0 == pytest.approx(SEWING_MARGIN_PT)


def test_signature_order_mark_kind_and_x() -> None:
    mark = signature_order_mark(1, 3, sheet_h=612.0, fold_x=396.0)
    assert mark.kind == "signature_order"
    assert mark.x0 == mark.x1 == 396.0


def test_fold_line_spans_full_height() -> None:
    mark = fold_line(sheet_h=612.0, fold_x=396.0)
    assert mark.kind == "fold_line"
    assert mark.x0 == mark.x1 == 396.0
    assert sorted((mark.y0, mark.y1)) == [0.0, 612.0]


def test_no_mark_inside_content_box_for_letter_landscape() -> None:
    """Every mark lies on or across the fold -- never strictly inside a cell."""
    marks: list[Mark] = []
    marks.extend(sewing_stations(sheet_h=SHEET_H, fold_x=FOLD_X, count=3))
    marks.append(signature_order_mark(0, 3, sheet_h=SHEET_H, fold_x=FOLD_X))
    marks.append(fold_line(sheet_h=SHEET_H, fold_x=FOLD_X))

    for m in marks:
        lo, hi = sorted((m.x0, m.x1))
        # A mark strictly inside a cell would have both x-endpoints strictly
        # on one side of the fold; every mark here touches or straddles it.
        assert lo <= FOLD_X <= hi


# --- stated positions ----------------------------------------------------
#
# `sewing_stations` as a count describes an evenly spaced pamphlet stitch
# and nothing else. Sewing on tapes needs a PAIR of stations either side of
# each tape at the tape's width; kettle stitches sit at a fixed inset with
# the rest between them. No integer produces either.


def test_stated_positions_are_used_exactly() -> None:
    from deckle.core.marks import STATION_TICK_PT

    stations = sewing_stations(
        sheet_h=612.0, fold_x=396.0, count=3, positions=(36.0, 144.0, 162.0)
    )

    assert [m.y0 for m in stations] == [36.0, 144.0, 162.0]
    assert all(m.y0 == m.y1 for m in stations)
    assert all(m.x0 == 396.0 - STATION_TICK_PT for m in stations)
    assert all(m.x1 == 396.0 + STATION_TICK_PT for m in stations)
    assert all(m.kind == "sewing_station" for m in stations)


def test_a_tape_pair_straddles_the_tape() -> None:
    """The physical need the integer cannot express: two stations either
    side of a 1/2in tape whose centre is 2.25in up from the tail."""
    centre, tape = 2.25 * 72.0, 0.5 * 72.0
    positions = (centre - tape / 2.0, centre + tape / 2.0)

    stations = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3, positions=positions)

    assert [m.y0 for m in stations] == [144.0, 180.0]
    assert stations[1].y0 - stations[0].y0 == tape


def test_stated_positions_ignore_the_count() -> None:
    stations = sewing_stations(
        sheet_h=612.0, fold_x=396.0, count=99, positions=(100.0,)
    )

    assert len(stations) == 1


def test_an_empty_position_list_marks_nothing() -> None:
    assert sewing_stations(sheet_h=612.0, fold_x=396.0, count=3, positions=()) == ()


def test_positions_none_falls_back_to_even_spacing() -> None:
    assert sewing_stations(
        sheet_h=612.0, fold_x=396.0, count=4, positions=None
    ) == sewing_stations(sheet_h=612.0, fold_x=396.0, count=4)


def test_a_position_off_the_sheet_is_refused() -> None:
    """Refused rather than clamped: silently moving a stated position would
    print a mark somewhere the binder did not ask for."""
    with pytest.raises(ValueError) as excinfo:
        sewing_stations(sheet_h=612.0, fold_x=396.0, count=3, positions=(700.0,))

    assert "700" in str(excinfo.value)
    assert "612" in str(excinfo.value)


@pytest.mark.parametrize("y", [0.0, 612.0, -1.0])
def test_a_position_on_or_past_an_edge_is_refused(y: float) -> None:
    with pytest.raises(ValueError):
        sewing_stations(sheet_h=612.0, fold_x=396.0, count=3, positions=(y,))


def test_marks_does_not_normalise_what_it_is_given() -> None:
    """Pure geometry reproduces its input. Sorting or de-duplicating here
    as well as at the boundary is how the CLI and the GUI come to disagree
    about the same text."""
    stations = sewing_stations(
        sheet_h=612.0, fold_x=396.0, count=3, positions=(200.0, 100.0, 100.0)
    )

    assert [m.y0 for m in stations] == [200.0, 100.0, 100.0]
