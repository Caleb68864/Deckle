"""``--sheets`` and ``--rule``: printing one sheet, and measuring it.

A proof sheet is the answer to a question the export path cannot otherwise
settle. Deckle asks the viewer not to scale the page, but that is a hint a
driver can ignore -- so the only way to know whether it was honoured is to
print one sheet and measure something on it whose length is known.

Sheet numbers here are 0-based, matching every other number Deckle prints:
the layout warnings, the schedule's gathering list, ``Sheet.index``.
"""

from __future__ import annotations

import os

import pytest

from deckle.cli import _parse_sheet_selection, main

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


# --- parsing ------------------------------------------------------------


def test_a_single_sheet_number_selects_that_sheet():
    assert _parse_sheet_selection("0") == [0]


def test_a_comma_list_selects_each_sheet_in_the_order_given():
    assert _parse_sheet_selection("2,0") == [2, 0]


def test_a_range_is_inclusive_at_both_ends():
    assert _parse_sheet_selection("1-3") == [1, 2, 3]


def test_a_range_of_one_sheet_is_that_sheet():
    assert _parse_sheet_selection("2-2") == [2]


def test_ranges_and_singles_mix():
    assert _parse_sheet_selection("0,2-4") == [0, 2, 3, 4]


def test_whitespace_around_items_is_tolerated():
    assert _parse_sheet_selection(" 0 , 2 - 3 ") == [0, 2, 3]


@pytest.mark.parametrize(
    "value",
    ["", "a", "0,", "-1", "1-", "3-1", "0..2", "1,,2"],
)
def test_a_malformed_selection_is_rejected_while_the_user_is_still_looking(value):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_sheet_selection(value)


# --- the CLI ------------------------------------------------------------


def test_exporting_one_sheet_writes_only_that_sheet(tmp_path, capsys):
    import pikepdf

    out = os.path.join(str(tmp_path), "proof.pdf")

    rc = main(["export", FIXTURE, "-o", out, "--sheets", "0"])

    assert rc == 0
    with pikepdf.open(out) as pdf:
        # One sheet, so at most its two faces.
        assert len(pdf.pages) <= 2


def test_asking_for_a_sheet_the_document_does_not_have_is_an_error(tmp_path, capsys):
    """``export`` silently skips an index it cannot find, which would write
    an empty PDF and report success -- the worst pair. The CLI checks."""
    out = os.path.join(str(tmp_path), "proof.pdf")

    rc = main(["export", FIXTURE, "-o", out, "--sheets", "99"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "99" in err
    assert not os.path.exists(out), "an unwritable request still wrote a file"


def test_asking_for_a_rule_says_what_to_measure(tmp_path, capsys):
    """A ruler nobody knows the length of settles nothing."""
    out = os.path.join(str(tmp_path), "proof.pdf")

    rc = main(["export", FIXTURE, "-o", out, "--sheets", "0", "--rule"])

    assert rc == 0
    assert "7 in" in capsys.readouterr().out


def test_a_plain_export_says_nothing_about_measuring(tmp_path, capsys):
    out = os.path.join(str(tmp_path), "out.pdf")

    main(["export", FIXTURE, "-o", out])

    assert "measure" not in capsys.readouterr().out.lower()
