"""What the desktop export paths say, and what they leave in the log.

Found by the 2026-11-09 hardening pass:

- ``save_pdf_as`` logged its failures and never its success, while
  ``export_single_pass`` logged both. A diagnostics log that shows an
  ``export_failed`` and no ``pdf_exported`` on either side of it cannot answer
  "did the PDF they printed come from this run?".
- A non-``OSError`` failure was reported as ``f"Export failed: {exc}"``, which
  is ``"Export failed: "`` -- nothing after the colon -- for any exception
  raised without a message.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deckle.app.workers import run_inline


def _window(tmp_path):
    shown = []
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=shown.append),
        _jobs={},
        _job_runner=run_inline,
        window=None,
        save_pdf_button=SimpleNamespace(setEnabled=lambda value: None),
        _sync_document_actions=lambda: None,
        state=SimpleNamespace(project=SimpleNamespace(kind="book")),
        preview_view=SimpleNamespace(plan=SimpleNamespace(sheets=[object(), object()])),
        suggested_export_name=lambda: "out.pdf",
    )
    return window, shown


@pytest.fixture
def save_as(monkeypatch, tmp_path):
    """Drive the real ``save_pdf_as`` with the dialog and the writer stubbed."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QFileDialog

    from deckle.app import exporting

    target = str(tmp_path / "book.pdf")
    events = []
    monkeypatch.setattr(exporting, "_output_ready", lambda window: True)
    monkeypatch.setattr(exporting, "project_source_pages", lambda project: [])
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, ""))
    )
    monkeypatch.setattr(
        exporting, "log_event", lambda event, **f: events.append((event, f))
    )
    monkeypatch.setattr(
        exporting, "log_exception", lambda event, exc, **f: events.append((event, f))
    )

    def run(writer):
        monkeypatch.setattr(exporting, "export", writer)
        window, shown = _window(tmp_path)
        exporting.save_pdf_as(window)
        return shown, events

    return run, target


def test_a_successful_save_is_logged(save_as):
    run, target = save_as

    shown, events = run(lambda plan, path: None)

    assert shown[-1] == f"Saved 2 sheet(s) to {target}"
    assert events == [("pdf_exported", {"path": target, "sheets": 2})]


def test_a_failed_save_is_logged_and_not_also_logged_as_a_success(save_as):
    run, target = save_as

    def boom(plan, path):
        raise RuntimeError("pikepdf said no")

    shown, events = run(boom)

    assert [event for event, _ in events] == ["export_failed"]
    assert shown[-1] == "Export failed (RuntimeError): pikepdf said no"


def test_a_failure_with_no_message_still_says_what_it_was(save_as):
    run, _target = save_as

    def boom(plan, path):
        raise AssertionError()

    shown, _events = run(boom)

    assert shown[-1] == "Export failed (AssertionError)."


@pytest.mark.parametrize(
    "exc, expected",
    [
        (KeyError(), "Export failed (KeyError)."),
        (ValueError("   "), "Export failed (ValueError)."),
        (ValueError("bad plan"), "Export failed (ValueError): bad plan"),
    ],
)
def test_unexpected_failure_message_is_never_empty(exc, expected):
    from deckle.app.exporting import unexpected_failure_message

    assert unexpected_failure_message(exc) == expected
