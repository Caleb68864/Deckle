"""What the CLI says when things go wrong.

The uncovered third of ``cli.py`` was almost entirely error reporting,
which is exactly the property the hardening work was meant to establish:
everything either handles itself or fails with something a person can act
on. Untested error paths are the ones that rot, because nothing exercises
them until a user hits one.

Two things were wrong. ``--paper 0x0`` was accepted, imposed, and then
failed inside pikepdf with "Page size must be between 3 and 14400 PDF
units" -- a library message naming neither the flag nor the value, after
doing all the work. And a malformed project file reported
``error: cannot open job.deckle: 'pages'``, which is ``str(KeyError)``:
the problem named only to someone who already knows the file format.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


def _cli(*args: str) -> subprocess.CompletedProcess:
    """Run the CLI the way a user does, out of process.

    ``python -m deckle`` is the GUI entry point; ``deckle.cli`` is the
    headless one. Getting that wrong hangs, which is worth encoding here.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    return subprocess.run(
        [sys.executable, "-m", "deckle.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        cwd=REPO_ROOT,
    )


# -- paper sizes ----------------------------------------------------------


@pytest.mark.parametrize("paper", ["0x0", "1x1pt", "2.9x11pt", "300x300in", "1x20000pt"])
def test_a_paper_size_no_pdf_can_hold_is_refused_before_any_work(paper, tmp_path):
    """Refused at the flag, not four seconds later inside pikepdf."""
    out = tmp_path / "out.pdf"

    result = _cli("export", FIXTURE, "-o", str(out), "--paper", paper)

    assert result.returncode != 0
    assert "--paper" in result.stderr
    assert paper in result.stderr, "the message does not repeat what was typed"
    assert not out.exists(), "a rejected size still produced a file"


def test_the_rejection_says_what_the_limits_are():
    """An error that says only "invalid" leaves the user guessing."""
    result = _cli("export", FIXTURE, "-o", "x.pdf", "--paper", "0x0")

    assert "3" in result.stderr and "14400" in result.stderr


@pytest.mark.parametrize(
    "paper", ["letter", "a4", "legal", "8.5x11in", "612x792", "216x279mm"]
)
def test_usable_paper_sizes_are_still_accepted(paper, tmp_path):
    """The guard must not have narrowed what works."""
    out = tmp_path / f"{paper.replace('.', '_')}.pdf"

    result = _cli("export", FIXTURE, "-o", str(out), "--paper", paper)

    assert result.returncode == 0, result.stderr[-400:]
    assert out.exists() and out.stat().st_size > 0


def test_an_unparseable_paper_names_the_units_it_accepts():
    result = _cli("export", FIXTURE, "-o", "x.pdf", "--paper", "8.5*11")

    assert result.returncode != 0
    for unit in ("in", "pt", "mm"):
        assert unit in result.stderr


# -- a project file carries its own paper and skips the flag parser -------


def test_a_project_holding_an_impossible_paper_reports_rather_than_traces(tmp_path):
    """The other way in. A `.deckle` never passes through the flag parser,
    so the guard above cannot see it -- and without the export catching it,
    the user gets a bare pikepdf traceback."""
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project
    from deckle.core.project_io import save_project

    project = Project(
        pages=list(load_pdf(FIXTURE)),
        layout=LayoutSettings(paper=(1.0, 1.0), gutter_pt=18.0, binding_edge="left"),
        printer=None,
    )
    path = tmp_path / "impossible.deckle"
    save_project(project, str(path))

    result = _cli("export", str(path), "-o", str(tmp_path / "out.pdf"))

    assert result.returncode == 1
    assert result.stderr.startswith("layout warnings:") or "error:" in result.stderr
    assert "Traceback" not in result.stderr, "the failure surfaced as a crash"
    assert "error: cannot write" in result.stderr


# -- malformed project files ----------------------------------------------


def test_a_project_missing_a_field_says_so_in_words(tmp_path):
    """`str(KeyError)` is the bare key, so this used to read
    `error: cannot open job.deckle: 'pages'`."""
    path = tmp_path / "empty.deckle"
    path.write_text("{}", encoding="utf-8")

    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "not a complete Deckle project" in result.stderr
    assert "pages" in result.stderr, "the missing field is not named"
    assert "Traceback" not in result.stderr


def test_a_project_that_is_not_json_says_that(tmp_path):
    path = tmp_path / "corrupt.deckle"
    path.write_text("this is not json", encoding="utf-8")

    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "not valid JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_project_whose_source_has_vanished_names_the_source(tmp_path):
    """A project stores references, not content, so it outlives its
    sources -- and the message has to say which one went."""
    from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
    from deckle.core.project_io import save_project

    missing = tmp_path / "gone.pdf"
    missing.write_bytes(b"%PDF-1.4\n")
    project = Project(
        pages=[
            SourcePage(
                ref=SourceRef(
                    path=str(missing), page_index=0, sha256="a" * 64,
                    width_pt=612.0, height_pt=792.0,
                ),
                rotate_deg=0,
                skipped=False,
            )
        ],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left"),
        printer=None,
    )
    path = tmp_path / "orphan.deckle"
    save_project(project, str(path))
    missing.unlink()

    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "gone.pdf" in result.stderr
    assert "Traceback" not in result.stderr


# -- sources --------------------------------------------------------------


def test_a_missing_source_is_reported_not_traced(tmp_path):
    result = _cli("info", str(tmp_path / "nope.pdf"))

    assert result.returncode == 1
    assert "no such file" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_a_file_that_is_not_a_pdf_is_reported_not_traced(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"nope")

    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "not a PDF" in result.stderr
    assert "Traceback" not in result.stderr


# -- every failure leaves a trail -----------------------------------------


def test_failures_are_written_to_the_diagnostic_log(tmp_path):
    """The whole point of the log: a user reports "it did not work" and
    there is somewhere to look."""
    log_dir = tmp_path / "logs"
    path = tmp_path / "corrupt.deckle"
    path.write_text("{}", encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["DECKLE_LOG_DIR"] = str(log_dir)
    subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", str(path)],
        capture_output=True, text=True, env=env, timeout=120, cwd=REPO_ROOT,
    )

    log = log_dir / "diagnostics.jsonl"
    assert log.exists(), "nothing was logged for a failed open"
    events = [json.loads(line)["event"] for line in log.read_text().splitlines() if line]
    assert "project_open_failed" in events


# -- the parsing itself, in process ---------------------------------------
#
# The tests above run the CLI as a subprocess, which is the only way to
# assert on exit codes and stderr as a user sees them -- but it means
# coverage cannot see the lines they exercise. These call the same
# functions directly, so the guard is measured as well as demonstrated.


@pytest.mark.parametrize(
    "value,expected",
    [
        ("letter", (612.0, 792.0)),
        ("LETTER", (612.0, 792.0)),
        ("a4", (595.28, 841.89)),
        ("legal", (612.0, 1008.0)),
        ("8.5x11in", (612.0, 792.0)),
        ("612x792", (612.0, 792.0)),
        ("612x792pt", (612.0, 792.0)),
        ("3x3pt", (3.0, 3.0)),
    ],
)
def test_parse_paper_accepts(value, expected):
    from deckle.cli import _parse_paper

    got = _parse_paper(value)
    assert got == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    ["", "x", "8.5x", "x11", "8.5*11", "letterx", "A5", "8.5 x 11in", "-1x5in"],
)
def test_parse_paper_rejects_nonsense(value):
    import argparse

    from deckle.cli import _parse_paper

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_paper(value)


@pytest.mark.parametrize("value", ["0x0", "1x1pt", "2.9x11pt", "300x300in", "1x20000pt"])
def test_parse_paper_rejects_sizes_no_pdf_can_hold(value):
    """The boundary is pikepdf's, and it is applied here so the failure
    arrives before any imposition rather than after all of it."""
    import argparse

    from deckle.cli import _parse_paper

    with pytest.raises(argparse.ArgumentTypeError) as caught:
        _parse_paper(value)
    assert "3" in str(caught.value) and "14400" in str(caught.value)


def test_the_limits_are_exactly_pikepdfs():
    """If pikepdf ever widens or narrows its range, this guard becomes a
    lie in one direction or the other."""
    import pikepdf

    from deckle.cli import MAX_PAPER_PT, MIN_PAPER_PT

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(MIN_PAPER_PT, MIN_PAPER_PT))
    pdf.add_blank_page(page_size=(MAX_PAPER_PT, MAX_PAPER_PT))

    for bad in ((MIN_PAPER_PT - 0.5, 100.0), (MAX_PAPER_PT + 1, 100.0)):
        with pytest.raises(ValueError):
            pdf.add_blank_page(page_size=bad)
    pdf.close()
