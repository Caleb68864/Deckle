"""``--dry-run``: say what would be written, write nothing.

N14. Deckle's own argument for existing is that the artefact is physical
and paper is expensive, so the program should not make you spend either to
find out what it was going to do. ``--dry-run`` is that argument applied
to the CLI: every check runs, nothing is written, and what it reports is
the real plan rather than a description of one.

The two halves are tested separately on purpose:

- **Nothing is written.** The easy half, and the one that would fail
  loudly if it broke.
- **Everything is still checked.** The half that makes the flag worth
  having. A dry run that skipped validation would report a plan that
  cannot happen, which is worse than no dry run at all -- it would be a
  green light for a job that fails after the paper is already committed.
"""

from __future__ import annotations

import os

import pytest

from deckle.cli import main

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


# -- nothing is written ---------------------------------------------------


def test_export_dry_run_writes_no_pdf(tmp_path, capsys):
    out = tmp_path / "out.pdf"

    assert main(["export", FIXTURE, "-o", str(out), "--dry-run"]) == 0

    assert not out.exists()
    assert list(tmp_path.iterdir()) == [], "not even a scratch file"
    printed = capsys.readouterr().out
    assert str(out) in printed
    assert "nothing was written." in printed


def test_impose_dry_run_writes_no_project(tmp_path, capsys):
    out = tmp_path / "job.deckle"

    assert main(["impose", FIXTURE, "-o", str(out), "--dry-run"]) == 0

    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
    assert "nothing was written." in capsys.readouterr().out


def test_a_dry_run_does_not_overwrite_an_existing_file(tmp_path, capsys):
    """The destination usually already exists -- that is why you dry-run it.

    Someone checking what a re-export would do is standing over the
    previous one, and a "dry" run that truncated it would destroy exactly
    the thing they were being careful about.
    """
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the previous export")

    assert main(["export", FIXTURE, "-o", str(out), "--dry-run"]) == 0

    assert out.read_bytes() == b"the previous export"


# -- everything is still checked -----------------------------------------


def test_a_dry_run_still_rejects_a_destination_that_cannot_be_written(tmp_path, capsys):
    """A plan whose destination is impossible is not a plan.

    Reported before any imposition work, the same as a real export, so the
    message names the path the user typed.
    """
    missing = tmp_path / "no-such-folder" / "out.pdf"

    assert main(["export", FIXTURE, "-o", str(missing), "--dry-run"]) == 1

    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert captured.out == ""


def test_a_dry_run_still_rejects_a_source_that_cannot_be_read(tmp_path, capsys):
    assert main(
        ["export", "no-such-file.pdf", "-o", str(tmp_path / "o.pdf"), "--dry-run"]
    ) == 1
    assert "error:" in capsys.readouterr().err


def test_a_dry_run_still_rejects_a_sheet_the_document_does_not_have(tmp_path, capsys):
    """The check that costs the most to discover late.

    ``--sheets 99`` on a one-sheet document is a typo, and the point of
    the flag is to be told so before the stack is in the tray rather than
    after.
    """
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--sheets", "99", "--dry-run",
        ]
    ) == 1

    captured = capsys.readouterr()
    assert "no sheet 99" in captured.err
    assert captured.out == ""


def test_a_dry_run_still_rejects_a_pass_without_a_profile(tmp_path, capsys):
    """``--pass`` needs ``--profile``, dry or not.

    Neither the sheet order nor the half turn has a safe default, and a
    dry run that answered anyway would be reporting a guess.
    """
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--pass", "front", "--dry-run",
        ]
    ) == 1
    assert "needs --profile" in capsys.readouterr().err


def test_a_dry_run_still_rejects_impossible_layout_settings(tmp_path, capsys):
    """Settings only judgeable once the pages are read are judged anyway."""
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--fold-scheme", "folio", "--signatures", "99", "--dry-run",
        ]
    ) == 1
    assert "error:" in capsys.readouterr().err


# -- what it says ---------------------------------------------------------


def test_export_dry_run_reports_the_sheets_it_would_write(tmp_path, capsys):
    """The selection is echoed in ``--sheets`` notation, so it round-trips.

    What a dry run prints should be pasteable back into the real command;
    a different notation would make the reader translate between two
    spellings of the same list.
    """
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--fold-scheme", "folio", "--landscape",
            "--sheets", "0", "--dry-run",
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "sheets: 1 of" in printed
    assert "(0)" in printed


def test_export_dry_run_reports_the_registration_it_would_apply(tmp_path, capsys):
    """The back offset is the most expensive number in the job.

    It usually comes from a saved profile rather than from this
    invocation, and it cannot be seen in the output -- a shifted back
    looks exactly like an unshifted one until it is printed and held up
    against its own front. So a dry run that did not name it would be
    silent about the thing hardest to verify afterwards.
    """
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--back-offset", "3,-2", "--dry-run",
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "registration: back faces moved +3, -2pt" in printed


def test_export_dry_run_names_the_single_pass_and_the_half_turn(tmp_path, capsys):
    """``generic_face_up_in_order`` is ``flip_axis="short"``, and the
    fixture is portrait, whose vertical edge is its long one -- so this
    printer turns the sheet about the horizontal edge and the backs need
    the half turn. The dry run has to say so before any paper is used."""
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--profile", "generic_face_up_in_order",
            "--pass", "back", "--dry-run",
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "backs only" in printed
    assert "turned 180 degrees" in printed
    assert "reload:" in printed


def test_export_dry_run_is_silent_about_a_turn_it_would_not_make(tmp_path, capsys):
    """The other half of the pair. A dry run that named the half turn
    unconditionally would be no evidence at all -- and the turn is the
    thing whose sign nobody can check until the paper is printed."""
    assert main(
        [
            "export", FIXTURE, "-o", str(tmp_path / "o.pdf"),
            "--profile", "generic_face_down_reversed",  # long, on portrait
            "--pass", "back", "--dry-run",
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "backs only" in printed
    assert "turned 180 degrees" not in printed


def test_impose_dry_run_reports_the_layout_it_would_record(tmp_path, capsys):
    assert main(
        [
            "impose", FIXTURE, "-o", str(tmp_path / "job.deckle"),
            "--fold-scheme", "folio", "--landscape",
            "--printer", "Office LaserJet", "--dry-run",
        ]
    ) == 0

    printed = capsys.readouterr().out
    assert "fold scheme: folio" in printed
    assert "printer recorded: Office LaserJet" in printed


@pytest.mark.parametrize("command", ["export", "impose"])
def test_a_dry_run_still_surfaces_layout_warnings(tmp_path, capsys, command):
    """A warning is the main thing a dry run is looking for.

    Suppressing them here would leave the flag reporting a job that looks
    fine and is not -- and stderr is where they belong, so stdout stays
    the plan.
    """
    suffix = ".pdf" if command == "export" else ".deckle"
    assert main(
        [
            command, FIXTURE, "-o", str(tmp_path / f"out{suffix}"),
            "--fold-scheme", "folio", "--dry-run",
        ]
    ) == 0

    captured = capsys.readouterr()
    assert "sheet_orientation" in captured.err
    assert "nothing was written." in captured.out
