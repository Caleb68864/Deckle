"""End-to-end run of the desktop app, in a subprocess.

Every UI test before this one built a single view and asked whether that
view behaved. All of them passed while the thumbnail pipeline rendered
correctly and put nothing on screen: the worker worked, the cancellation
worked, the cache worked, and the one line that turns a render into an icon
did not exist. Testing parts in isolation cannot see a missing connection.

Runs out of process because constructing a real ``QMainWindow`` under pytest
exits 127 on this machine -- an interaction with the offscreen Qt platform,
not a defect in Deckle, and one the app does not exhibit when actually run.
The workflow itself lives in ``tests/gui_workflow.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")
SCRIPT = os.path.join(REPO_ROOT, "tests", "gui_workflow.py")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """Run the whole workflow once; every test reads the same report."""
    pytest.importorskip("PySide6")
    out_pdf = tmp_path_factory.mktemp("gui") / "out.pdf"

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"

    result = subprocess.run(
        [sys.executable, "-u", SCRIPT, FIXTURE, str(out_pdf)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )

    assert result.returncode == 0, (
        f"the workflow did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-2000:]}"
    )
    assert result.stdout.strip(), "the workflow produced no report"
    return json.loads(result.stdout.strip().splitlines()[-1])


# -- empty state ---------------------------------------------------------


def test_an_empty_deckle_names_the_first_step(report):
    assert report["empty_status"].startswith("Import a PDF")
    assert report["empty_save_enabled"] is False
    assert report["empty_print_enabled"] is False


def test_importing_enables_saving_and_clears_the_instruction(report):
    """The status bar told the user to import something and kept saying it
    after they had. An instruction the user has already carried out is
    worse than silence."""
    assert report["after_import_save_enabled"] is True
    assert report["after_import_status"] == ""


# -- the bug this file exists for ---------------------------------------


def test_thumbnails_actually_reach_the_screen(report):
    """Renders used to land in item data and stop there. Every part of the
    pipeline was tested and worked; nothing asked whether the result was
    ever drawn."""
    assert report["thumbnail_worker_failed"] is False
    assert report["thumbnails_rendered"] >= 3
    assert report["icons_present"] >= 3, (
        "thumbnails rendered but no icon reached the list"
    )
    assert report["icon_px"] >= 64, (
        "Qt's default icon size draws a page as a bullet"
    )


def test_a_blank_is_named_and_does_not_break_the_window(report):
    assert report["labels"][1] == "page 2 _blank"
    assert report["page_count"] == 2, "the import itself is unchanged"


# -- layout, modes, warnings --------------------------------------------


def test_the_mode_tabs_are_the_fold_scheme(report):
    assert report["tabs"] == ["Flat sheets", "Signatures"]
    assert report["fold_scheme_after_tab"] == "folio"
    assert report["fold_scheme_back"] == "none"


def test_orientation_turns_the_sheet(report):
    assert report["paper_after_landscape"] == [792.0, 612.0]


def test_long_grain_on_a_folded_sheet_warns(report):
    """Ordinary office paper, folded the usual way: the commonest
    home-binding mistake, and the one no other tool reports."""
    assert "grain_direction" in report["warning_kinds"]


def test_folio_produces_signatures(report):
    assert report["signatures"] >= 1
    assert report["sheets"] >= 1


# -- the artifact --------------------------------------------------------


def test_the_workflow_ends_in_a_real_pdf(report):
    assert report["exported"] is True
    assert report["export_bytes"] > 0
    assert report["preview_sheets"] >= 1
