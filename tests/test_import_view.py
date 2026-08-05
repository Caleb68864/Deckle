"""Importing, where every workflow starts.

``import_view.py`` was the least-covered module in the app at 33%. It is
also the only path by which a document enters Deckle at all, and it runs on
a background thread — which is where this session has repeatedly found the
same defect: work that fails in a QThread has nowhere to report to, so Qt
prints a traceback nobody can act on and the UI shows something untrue.

That is exactly what these found. ``ImportWorker.run`` caught only
``EncryptedPdfError``; every other loader refusal propagated out of the
thread, leaving ``error`` unset, so the view took its *success* branch and
announced "Imported 0 page(s)." for a file it had failed to read.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import LayoutSettings, Project  # noqa: E402

LETTER = (612.0, 792.0)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _state():
    from deckle.app.state import AppState

    return AppState(
        Project(
            pages=[],
            layout=LayoutSettings(paper=LETTER, gutter_pt=0.0, binding_edge="left"),
            printer=None,
        )
    )


def _worker(path: str):
    from deckle.app.views.import_view import ImportWorker

    state = _state()
    worker = ImportWorker(state, path)
    worker.run()
    return state, worker


# -- the happy path -------------------------------------------------------


def test_importing_a_pdf_loads_its_pages():
    state, worker = _worker(FIXTURE)

    assert worker.error is None
    assert len(worker.pages) == 2
    assert len(state.project.pages) == 2, "the import must reach the project"


def test_a_successful_import_reports_the_page_count():
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    _, worker = _worker(FIXTURE)
    view._on_finished(worker)

    assert "2 page(s)" in view.status_label.text()


def test_a_successful_import_emits_pages_and_warnings():
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    seen = []
    view.imported.connect(lambda pages, warnings: seen.append((len(pages), warnings)))

    _, worker = _worker(FIXTURE)
    view._on_finished(worker)

    assert seen and seen[0][0] == 2


# -- failure must reach the user -----------------------------------------


def test_a_file_that_is_not_a_pdf_reports_instead_of_raising(tmp_path):
    """The bug this file found. It raised inside a QThread, which has
    nowhere to deliver an exception, so `error` stayed None and the view
    announced a successful import of zero pages."""
    bad = tmp_path / "notreally.pdf"
    bad.write_bytes(b"this is not a pdf")

    _, worker = _worker(str(bad))  # must not raise

    assert worker.error is not None
    assert "not a PDF" in worker.error


def test_a_truncated_pdf_reports_instead_of_raising(tmp_path):
    import pikepdf

    good = tmp_path / "whole.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=LETTER)
    pdf.save(str(good))
    pdf.close()

    truncated = tmp_path / "half.pdf"
    truncated.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])

    _, worker = _worker(str(truncated))

    assert worker.error is not None


def test_a_missing_file_reports_instead_of_raising(tmp_path):
    _, worker = _worker(str(tmp_path / "nothing-here.pdf"))

    assert worker.error is not None
    assert "no such file" in worker.error.lower()


def test_an_empty_image_directory_reports_instead_of_raising(tmp_path):
    empty = tmp_path / "no-images"
    empty.mkdir()

    _, worker = _worker(str(empty))

    assert worker.error is not None


def test_a_failed_import_says_so_rather_than_claiming_zero_pages():
    """"Imported 0 page(s)." for a file that could not be read is a lie
    that reads like a shrug."""
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    _, worker = _worker(os.path.join(os.path.dirname(__file__), "does-not-exist.pdf"))
    view._on_finished(worker)

    text = view.status_label.text()
    assert "Imported" not in text, f"a failure was reported as a success: {text!r}"
    assert text == worker.error


def test_a_failed_import_emits_failed_and_not_imported():
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    failures, successes = [], []
    view.failed.connect(failures.append)
    view.imported.connect(lambda pages, warnings: successes.append(pages))

    _, worker = _worker(os.path.join(os.path.dirname(__file__), "missing.pdf"))
    view._on_finished(worker)

    assert failures and not successes


def test_a_failed_import_leaves_the_project_untouched(tmp_path):
    """A half-applied import is worse than none: the arrange grid would
    show pages the user cannot account for."""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")

    state, worker = _worker(str(bad))

    assert worker.error is not None
    assert state.project.pages == []


def test_import_failures_are_recorded_in_the_diagnostic_log(tmp_path, monkeypatch):
    from deckle.core import diagnostics

    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path))
    diagnostics.reset_for_tests()

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    _worker(str(bad))

    log = tmp_path / "diagnostics.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text().splitlines() if line]
    assert "import_failed" in events

    diagnostics.reset_for_tests()


# -- encrypted PDFs keep their own wording -------------------------------


def test_a_password_protected_pdf_keeps_its_specific_message(tmp_path):
    """It was already handled, and its message names the cause precisely --
    the broader catch must not swallow it into something vaguer."""
    import pikepdf

    protected = tmp_path / "locked.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=LETTER)
    pdf.save(str(protected), encryption=pikepdf.Encryption(owner="o", user="u"))
    pdf.close()

    _, worker = _worker(str(protected))

    assert worker.error is not None
    assert "password-protected" in worker.error
