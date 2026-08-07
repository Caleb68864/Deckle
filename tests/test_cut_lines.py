"""Cut lines: where the knife goes, as distinct from where the paper folds.

Two different physical operations, and drawing one as the other is
actively wrong. After sewing, the block is trimmed square on its three
non-spine edges -- head, tail and fore-edge -- and a line printed at the
trim depth tells you where the plough goes and warns you if text is
inside it. The schedule already estimates fore-edge creep; a cut line is
that estimate made physical.

The spine is never trimmed: it is the bound edge, and a cut there would
take the book apart.
"""

from __future__ import annotations

import pytest

from deckle.core.marks import cut_lines

LETTER_W, LETTER_H = 612.0, 792.0


def _kinds(marks):
    return {m.kind for m in marks}


def _verticals(marks):
    return sorted(m.x0 for m in marks if m.x0 == m.x1)


def _horizontals(marks):
    return sorted(m.y0 for m in marks if m.y0 == m.y1)


def test_no_trim_depth_means_no_cut_lines():
    """Zero disables, the way `sewing_stations = 0` does -- no separate
    boolean for a setting whose off-state is already expressible."""
    assert cut_lines(LETTER_W, LETTER_H, 0.0, ("right",)) == ()


def test_a_negative_trim_depth_is_also_no_cut_lines():
    assert cut_lines(LETTER_W, LETTER_H, -5.0, ("right",)) == ()


def test_every_mark_is_a_cut_line():
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("right",))

    assert _kinds(marks) == {"cut_line"}


def test_the_head_and_tail_are_always_trimmed():
    """Both are trimmed on any bound book, whichever edge the spine is on."""
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("right",))

    assert _horizontals(marks) == [18.0, LETTER_H - 18.0]


def test_a_right_hand_fore_edge_is_trimmed_on_the_right():
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("right",))

    assert _verticals(marks) == [LETTER_W - 18.0]


def test_a_left_hand_fore_edge_is_trimmed_on_the_left():
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("left",))

    assert _verticals(marks) == [18.0]


def test_a_folded_sheet_is_trimmed_on_both_outer_edges():
    """Folio puts two leaves side by side with the fold between them, so
    both outer edges are fore-edges and both get cut."""
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("left", "right"))

    assert _verticals(marks) == [18.0, LETTER_W - 18.0]


def test_the_spine_edge_is_never_cut():
    """A cut on the bound edge takes the book apart."""
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("right",))

    assert 18.0 not in _verticals(marks)


def test_cut_lines_span_the_whole_sheet():
    """Full-length rules, not corner ticks: you lay a straightedge along
    one and cut, and a mark that stops short is a mark you have to
    extrapolate by eye."""
    marks = cut_lines(LETTER_W, LETTER_H, 18.0, ("right",))

    for mark in marks:
        if mark.x0 == mark.x1:
            assert (mark.y0, mark.y1) == (0.0, LETTER_H)
        else:
            assert (mark.x0, mark.x1) == (0.0, LETTER_W)


def test_a_trim_deeper_than_the_sheet_is_refused():
    """Two lines that have crossed each other describe no region to keep,
    and drawing them would silently instruct a cut that discards the book."""
    with pytest.raises(ValueError):
        cut_lines(LETTER_W, LETTER_H, 500.0, ("right",))
