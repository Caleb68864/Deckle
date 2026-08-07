"""Stating the gathering sizes outright, instead of one uniform number.

``--sheets-per-signature`` says "make them all this big and let the last
one be whatever is left". Two real jobs cannot be said that way:

- A page count that divides badly. Rather than accept six blank leaves at
  the end, make the last signature shorter -- ``7,7,6,6`` instead of
  ``4,4,4,4,4,4,2``.
- Chapter-aligned gatherings, so a chapter break lands on a signature
  boundary and the book opens flat there.

``blank_mode="balanced"`` gets partway to the first case by spreading the
padding, but the binder still cannot state the answer directly.

The lengths must sum to the sheet count exactly. Anything else is either
sheets nobody said where to put or signatures made of sheets that do not
exist, and guessing which the user meant would be worse than saying so.
"""

from __future__ import annotations

import pytest

from deckle.core.layout import SaddleStitchStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core.signatures import split_signatures_at

LETTER_LANDSCAPE = (792.0, 612.0)


def _pages(n):
    return [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


# --- the partition ------------------------------------------------------


def test_the_lengths_become_the_groups():
    assert split_signatures_at(6, (3, 2, 1)) == [(0, 1, 2), (3, 4), (5,)]


def test_the_groups_cover_every_sheet_once_in_order():
    groups = split_signatures_at(10, (4, 3, 3))

    assert [i for group in groups for i in group] == list(range(10))


def test_lengths_that_do_not_add_up_are_refused():
    with pytest.raises(ValueError) as excinfo:
        split_signatures_at(10, (4, 4))

    message = str(excinfo.value)
    assert "8" in message and "10" in message, "the message names neither number"


def test_a_zero_length_signature_is_refused():
    with pytest.raises(ValueError):
        split_signatures_at(4, (2, 0, 2))


def test_a_negative_length_is_refused():
    with pytest.raises(ValueError):
        split_signatures_at(4, (5, -1))


def test_no_lengths_at_all_is_refused():
    with pytest.raises(ValueError):
        split_signatures_at(4, ())


def test_a_single_signature_holding_everything_is_fine():
    assert split_signatures_at(3, (3,)) == [(0, 1, 2)]


# --- through the imposer ------------------------------------------------


def _settings(**kw):
    base = dict(
        paper=LETTER_LANDSCAPE,
        gutter_pt=36.0,
        binding_edge="left",
        fold_scheme="folio",
    )
    base.update(kw)
    return LayoutSettings(**base)


def test_custom_lengths_shape_the_signatures():
    # 24 pages -> 6 sheets. Ask for an uneven gathering.
    plan = SaddleStitchStrategy().impose(
        _pages(24), _settings(signature_lengths=(4, 2))
    )

    assert [len(s.sheet_indices) for s in plan.signatures] == [4, 2]


def test_custom_lengths_override_the_uniform_setting():
    plan = SaddleStitchStrategy().impose(
        _pages(24), _settings(sheets_per_signature=3, signature_lengths=(4, 2))
    )

    assert [len(s.sheet_indices) for s in plan.signatures] == [4, 2]


def test_the_uniform_setting_still_applies_when_no_lengths_are_given():
    plan = SaddleStitchStrategy().impose(_pages(24), _settings(sheets_per_signature=3))

    assert [len(s.sheet_indices) for s in plan.signatures] == [3, 3]


def test_lengths_that_do_not_match_the_document_are_refused():
    """The message has to name the sheet count the document actually makes,
    because that number is the one the user needs in order to fix it and
    it is not knowable without imposing."""
    with pytest.raises(ValueError) as excinfo:
        SaddleStitchStrategy().impose(_pages(24), _settings(signature_lengths=(4, 4)))

    assert "6" in str(excinfo.value)


def test_every_sheet_still_belongs_to_exactly_one_signature():
    plan = SaddleStitchStrategy().impose(
        _pages(24), _settings(signature_lengths=(1, 2, 3))
    )

    seen = [i for s in plan.signatures for i in s.sheet_indices]
    assert sorted(seen) == [sheet.index for sheet in plan.sheets]
    assert len(seen) == len(set(seen)), "a sheet is in two signatures"


# --- how a bad setting reaches the user ---------------------------------
#
# `cli.main`'s docstring draws the line: a user's problem gets a message
# that names the remedy, and only an unexpected exception gets a
# traceback, "because a traceback is a bug report and a swallowed one is
# not". Three settings added the same day -- --signatures, --trim and
# --crop -- all raise from inside `impose()`, which happens before any of
# the CLI's existing try blocks. All three landed on the wrong side of
# that line.

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    return subprocess.run(
        [sys.executable, "-m", "deckle.cli", *args],
        capture_output=True, text=True, env=env, timeout=180, cwd=REPO_ROOT,
    )


def test_signatures_that_do_not_add_up_are_reported_not_raised(tmp_path):
    out = str(tmp_path / "out.pdf")

    result = _cli(
        "export", FIXTURE, "-o", out,
        "--fold-scheme", "folio", "--paper", "11x8.5in", "--signatures", "5,5",
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr, result.stderr[-400:]
    assert "5, 5" in result.stderr or "(5, 5)" in result.stderr
    assert not os.path.exists(out)


def test_a_trim_that_consumes_the_sheet_is_reported_not_raised(tmp_path):
    out = str(tmp_path / "out.pdf")

    result = _cli("export", FIXTURE, "-o", out, "--trim", "400pt")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr, result.stderr[-400:]
    assert "trim" in result.stderr.lower()


def test_a_crop_that_consumes_the_page_is_reported_not_raised(tmp_path):
    out = str(tmp_path / "out.pdf")

    result = _cli("export", FIXTURE, "-o", out, "--crop", "400pt,0,400pt,0")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr, result.stderr[-400:]
    assert "crop" in result.stderr.lower()


def test_the_same_report_reaches_info_and_schedule():
    """Every subcommand imposes, so every one can hit this."""
    for command in ("info", "schedule"):
        result = _cli(command, FIXTURE, "--trim", "400pt")

        assert result.returncode == 1, command
        assert "Traceback" not in result.stderr, f"{command}: {result.stderr[-300:]}"


def test_the_schedule_does_not_blame_arithmetic_for_a_chosen_shape():
    """The uneven-signature note explains the shape as "the page count did
    not divide evenly". With explicit lengths that is false -- the binder
    asked for it -- and a confident wrong explanation is worse than none."""
    from deckle.core.schedule import build_schedule

    plan = SaddleStitchStrategy().impose(
        _pages(24), _settings(signature_lengths=(4, 2))
    )
    schedule = build_schedule(plan, _settings(signature_lengths=(4, 2)))

    assert not any("did not divide evenly" in note for note in schedule.notes), (
        schedule.notes
    )


def test_the_note_still_appears_when_the_arithmetic_really_did_it():
    from deckle.core.schedule import build_schedule

    settings = _settings(sheets_per_signature=4)
    plan = SaddleStitchStrategy().impose(_pages(24), settings)
    schedule = build_schedule(plan, settings)

    assert any("did not divide evenly" in note for note in schedule.notes), (
        schedule.notes
    )
