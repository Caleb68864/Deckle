"""Opening a saved project from the command line.

``.deckle`` files could be written but never read: ``impose`` saved one and
no command would open it again, so a saved project was a dead end. The
format itself was already good -- versioned, with a per-page content hash so
a source edited behind Deckle's back is caught rather than silently imposed.

A project carries its own layout, and these pin that it wins. It is the
layout the user set up, previewed and saved; recomputing from flag defaults
would mean ``deckle export project.deckle`` producing a different book from
the one the project describes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "sample.pdf")


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "deckle.cli", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or REPO,
    )


@pytest.fixture
def project(tmp_path):
    """A project saved with a layout nothing would arrive at by default."""
    path = tmp_path / "book.deckle"
    result = _cli(
        "impose", FIXTURE, "-o", str(path),
        "--gutter", "0.75in", "--fold-scheme", "folio",
        "--landscape", "--sewing-stations", "5",
    )
    assert result.returncode == 0, result.stderr
    return path


# -- the format ----------------------------------------------------------


def test_a_saved_project_records_pages_layout_and_a_version(project):
    data = json.loads(project.read_text(encoding="utf-8"))

    assert data["version"] >= 1, "an unversioned format cannot be migrated"
    assert data["layout"]["fold_scheme"] == "folio"
    assert data["layout"]["sewing_stations"] == 5
    assert len(data["pages"]) == 2


def test_each_page_records_a_content_hash(project):
    """What lets Deckle notice a source edited behind its back, rather than
    imposing content the user has not seen."""
    data = json.loads(project.read_text(encoding="utf-8"))

    for page in data["pages"]:
        assert len(page["sha256"]) == 64


def test_a_project_stores_references_not_content(project):
    """It is a description of a job, not an archive of it. Worth pinning:
    the alternative would silently double every book's disk footprint."""
    assert project.stat().st_size < 4096


# -- opening it ----------------------------------------------------------


def test_info_opens_a_project(project):
    result = _cli("info", str(project))

    assert result.returncode == 0, result.stderr
    assert "project:" in result.stdout
    assert "page count: 2" in result.stdout


def test_the_saved_layout_is_what_gets_imposed(project, tmp_path):
    """The whole point. Exporting a project must reproduce the book it
    describes, not one recomputed from defaults."""
    import pikepdf

    out = tmp_path / "from-project.pdf"
    result = _cli("export", str(project), "-o", str(out))
    assert result.returncode == 0, result.stderr

    with pikepdf.open(str(out)) as pdf:
        box = [float(v) for v in pdf.pages[0].MediaBox]
    width, height = box[2] - box[0], box[3] - box[1]

    assert width > height, (
        "the saved landscape/folio layout was lost -- the project was "
        "re-imposed from flag defaults"
    )


def test_schedule_opens_a_project(project):
    result = _cli("schedule", str(project))

    assert result.returncode == 0, result.stderr
    assert "BINDING SCHEDULE" in result.stdout
    # Folio was saved, so there is something to gather.
    assert "SIGNATURE 1" in result.stdout


def test_layout_flags_are_reported_as_ignored_not_silently_applied(project):
    """Silently ignoring them is confusing; silently applying them would
    produce a different book than the project describes."""
    result = _cli("info", str(project), "--gutter", "2in")

    assert result.returncode == 0
    assert "--gutter" in result.stderr
    assert "ignored" in result.stderr


# -- when the sources have moved ----------------------------------------


def test_a_project_whose_source_moved_says_which_file(tmp_path):
    import shutil

    source = tmp_path / "movable.pdf"
    shutil.copy(FIXTURE, source)
    path = tmp_path / "p.deckle"
    assert _cli("impose", str(source), "-o", str(path)).returncode == 0

    source.unlink()
    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "missing" in result.stderr
    assert "movable.pdf" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_project_whose_source_changed_refuses_rather_than_imposing_it(tmp_path):
    """The dangerous case. The file is still there, so nothing looks wrong
    -- but imposing it would use content the user has never reviewed."""
    import shutil

    import pikepdf

    source = tmp_path / "edited.pdf"
    shutil.copy(FIXTURE, source)
    path = tmp_path / "p.deckle"
    assert _cli("impose", str(source), "-o", str(path)).returncode == 0

    pdf = pikepdf.Pdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(str(source))
    pdf.close()

    result = _cli("info", str(path))

    assert result.returncode == 1
    assert "changed" in result.stderr
    assert "Traceback" not in result.stderr


def test_an_unreadable_project_file_reports_rather_than_raising(tmp_path):
    broken = tmp_path / "broken.deckle"
    broken.write_text("{ not json at all", encoding="utf-8")

    result = _cli("info", str(broken))

    assert result.returncode == 1
    assert result.stderr.startswith("error:")
    assert "Traceback" not in result.stderr


def test_a_pdf_is_still_treated_as_a_source_not_a_project():
    """The suffix decides. A regression here would try to JSON-parse a PDF."""
    result = _cli("info", FIXTURE)

    assert result.returncode == 0
    assert "project:" not in result.stdout


# -- blanks are not sources ----------------------------------------------


def test_a_project_containing_a_blank_can_be_reopened(tmp_path):
    """A blank the user inserted references no file.

    Source validation treated its empty path as a missing source, so a
    project with a single blank in it could be saved and never opened
    again -- and the error named no file, because there was none.
    """
    from deckle.app.state import insert_blank
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project, is_blank_page
    from deckle.core.project_io import load_project, save_project

    pages = load_pdf(FIXTURE)
    project = Project(
        pages=list(pages),
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left"),
        printer=None,
    )
    project = insert_blank(project, 1)

    path = tmp_path / "with-blank.deckle"
    save_project(project, str(path))
    reopened = load_project(str(path), allowed_roots=(os.path.dirname(FIXTURE),))

    assert len(reopened.pages) == 3
    assert is_blank_page(reopened.pages[1])
    assert not is_blank_page(reopened.pages[0])


def test_a_real_missing_source_is_still_caught_with_a_blank_present(tmp_path):
    """Skipping blanks must not skip the check that matters."""
    import shutil

    from deckle.app.state import insert_blank
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project
    from deckle.core.project_io import SourceMissingError, load_project, save_project

    source = tmp_path / "src.pdf"
    shutil.copy(FIXTURE, source)
    project = Project(
        pages=list(load_pdf(str(source))),
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
        printer=None,
    )
    path = tmp_path / "p.deckle"
    save_project(insert_blank(project, 0), str(path))

    source.unlink()

    with pytest.raises(SourceMissingError):
        load_project(str(path))


def test_importing_a_pdf_does_not_lock_the_file(tmp_path):
    """Import is metadata-only, and the handle must not outlive it.

    ``load_pdf`` opened a pdfium document and never closed it, so on
    Windows every imported source stayed locked for the life of the app:
    move, rename or delete it and the OS refused, naming no reason a user
    could act on.
    """
    import shutil

    from deckle.core.loader import load_pdf

    source = tmp_path / "movable.pdf"
    shutil.copy(FIXTURE, source)

    pages = load_pdf(str(source))
    assert len(pages) == 2

    source.unlink()  # must not raise PermissionError
    assert not source.exists()
