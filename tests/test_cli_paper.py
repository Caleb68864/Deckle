"""Stating paper by weight on the command line.

A binder scripting a job should not have to compute a caliper by hand
either, so `deckle.core.paper` is reachable from here as well as from the
desktop panel.

The interesting case is not the conversion -- that is tested in
`tests/test_paper.py` -- but the two places the *interface* can mislead:
"20 lb" is meaningless without a grade, and giving both a weight and a
caliper is two ways to say one thing.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "sample.pdf")


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "deckle.cli", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )


def test_grammage_reaches_the_schedule():
    """The end a binder cares about: a weight in, a spine estimate out."""
    result = _cli("schedule", FIXTURE, "--fold-scheme", "folio",
                  "--paper-weight", "80gsm")

    assert result.returncode == 0, result.stderr
    assert "spine" in result.stdout.lower()


def test_a_weight_and_a_caliper_agree_on_the_same_paper():
    """`--paper-weight 80gsm` must produce what `--paper-thickness` would
    for the same sheet, or the two entry points describe different books."""
    from deckle.core.paper import caliper_pt_from_gsm

    caliper = caliper_pt_from_gsm(80, "offset")
    by_weight = _cli("schedule", FIXTURE, "--fold-scheme", "folio",
                     "--paper-weight", "80gsm")
    by_caliper = _cli("schedule", FIXTURE, "--fold-scheme", "folio",
                      "--paper-thickness", f"{caliper}pt")

    assert by_weight.returncode == 0, by_weight.stderr
    assert by_weight.stdout == by_caliper.stdout


def test_pounds_without_a_grade_are_refused():
    """"20 lb" is 75gsm as bond and 54gsm as cover. Guessing would be
    wrong by half and the answer would look entirely reasonable."""
    result = _cli("info", FIXTURE, "--paper-weight", "20lb")

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "grade" in result.stderr.lower()


def test_pounds_with_a_grade_are_accepted():
    result = _cli("info", FIXTURE, "--paper-weight", "20lb",
                  "--paper-grade", "bond")

    assert result.returncode == 0, result.stderr


def test_a_weight_and_a_thickness_together_are_refused():
    """Two ways to say one thing, with a silent precedence, is how someone
    ends up with a book bound to a number they did not give."""
    result = _cli("info", FIXTURE, "--paper-weight", "80gsm",
                  "--paper-thickness", "0.3pt")

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "--paper-thickness" in result.stderr
    assert "--paper-weight" in result.stderr


@pytest.mark.parametrize("bad", ["80", "gsm", "80kg", "eightygsm", "-80gsm"])
def test_a_malformed_weight_names_what_was_expected(bad):
    result = _cli("info", FIXTURE, "--paper-weight", bad)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_an_unknown_paper_type_is_refused_by_argparse():
    result = _cli("info", FIXTURE, "--paper-weight", "80gsm",
                  "--paper-type", "papyrus")

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_no_paper_arguments_still_works():
    """Thickness stays optional -- most jobs never set it."""
    assert _cli("info", FIXTURE).returncode == 0


# --- --stations: exact sewing positions ----------------------------------
#
# `--sewing-stations 4` is an evenly spaced pamphlet stitch. A tape pair at
# 2in and 2.25in is not reachable from any integer, which is why the flag
# takes positions instead of a count.


def test_stations_parses_units_and_sorts():
    from deckle.cli import _parse_station_positions

    assert _parse_station_positions("2in,0.5in,2.25in") == (36.0, 144.0, 162.0)


def test_stations_dedupes():
    from deckle.cli import _parse_station_positions

    assert _parse_station_positions("36,36pt,0.5in") == (36.0,)


@pytest.mark.parametrize("value", ["0", "0pt", "0in"])
def test_stations_rejects_a_position_on_the_tail_edge(value):
    """A station at y=0 is a hole in the edge of the paper."""
    import argparse

    from deckle.cli import _parse_station_positions

    with pytest.raises(argparse.ArgumentTypeError) as excinfo:
        _parse_station_positions(value)

    assert "above the tail" in str(excinfo.value)


def test_stations_rejects_a_negative_position():
    """Refused one layer down, by `_parse_length_pt`, which is unsigned for
    the same reason a crop inset is: a length below zero describes nothing
    on the sheet."""
    import argparse

    from deckle.cli import _parse_station_positions

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_station_positions("-1")


@pytest.mark.parametrize("value", ["", "36,,72", "36,two"])
def test_stations_rejects_a_malformed_list(value):
    import argparse

    from deckle.cli import _parse_station_positions

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_station_positions(value)


def test_stations_reaches_the_project(tmp_path):
    import json

    from deckle.cli import main

    out = tmp_path / "job.deckle"

    rc = main([
        "impose", FIXTURE, "-o", str(out),
        "--fold-scheme", "folio", "--landscape",
        "--stations", "0.5in,2in",
    ])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["layout"]["sewing_station_positions_pt"] == [36.0, 144.0]


def test_stations_shows_up_in_the_schedule(capsys):
    from deckle.cli import main

    rc = main([
        "schedule", FIXTURE,
        "--fold-scheme", "folio", "--landscape",
        "--stations", "0.5in,2in",
    ])

    assert rc == 0
    assert "Measured up from the TAIL:" in capsys.readouterr().out
