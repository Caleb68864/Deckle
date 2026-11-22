"""Save signatures as separate PDFs: the app's half of ``export --split``.

The output-commands question -- are commands that write a file, rather
than settings, inside the owner's rule that the app reach what the CLI
reaches? -- was decided by agent 2026-11-21 (owner may revisit): yes. This
is the first of them.
"""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pikepdf
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.workers import run_inline  # noqa: E402
from deckle.core.loader import load_pdf  # noqa: E402
from deckle.core.models import LayoutSettings, Project  # noqa: E402
from deckle.core.planning import impose_project  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path_factory, monkeypatch):
    from deckle.core import diagnostics

    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


def _window(tmp_path, monkeypatch, fold_scheme="folio", target="book.pdf"):
    from PySide6.QtWidgets import QFileDialog

    pages = load_pdf(FIXTURE)
    # Sixteen pages from the fixture's two, so there are several gatherings.
    pages = [replace(pages[i % len(pages)]) for i in range(16)]
    layout = LayoutSettings(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                            fold_scheme=fold_scheme, sheets_per_signature=1)
    project = Project(pages, layout, None)
    shown = []
    window = SimpleNamespace(
        state=SimpleNamespace(project=project),
        preview_view=SimpleNamespace(plan=impose_project(project)),
        status_bar=SimpleNamespace(showMessage=shown.append),
        _jobs={}, _job_runner=run_inline, window=None,
        save_pdf_button=SimpleNamespace(setEnabled=lambda _v: None),
        _sync_document_actions=lambda: None,
        suggested_export_name=lambda: "book.pdf",
    )
    chosen = str(tmp_path / target)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (chosen, "")))
    return window, shown


def test_each_signature_lands_in_its_own_file_named_as_the_cli_names_it(tmp_path, monkeypatch):
    from deckle.app.exporting import save_signatures_as
    from deckle.core.outputs import signature_split_paths

    window, shown = _window(tmp_path, monkeypatch)
    plan = window.preview_view.plan
    assert len(plan.signatures) == 4

    save_signatures_as(window)

    expected = signature_split_paths(str(tmp_path / "book.pdf"), 4)
    assert [os.path.basename(p) for p in expected] == [
        "book-sig-01.pdf", "book-sig-02.pdf", "book-sig-03.pdf", "book-sig-04.pdf"]
    for path, signature in zip(expected, plan.signatures):
        with pikepdf.open(path) as pdf:
            # front and back of each sheet in the gathering
            assert len(pdf.pages) == 2 * len(signature.sheet_indices)
    assert not (tmp_path / "book.pdf").exists()
    assert shown[-1].startswith("Saved 4 signature(s)")


def test_a_document_without_signatures_says_how_to_get_them(tmp_path, monkeypatch):
    from deckle.app.exporting import NO_SIGNATURES_MESSAGE, save_signatures_as

    window, shown = _window(tmp_path, monkeypatch, fold_scheme="none")

    save_signatures_as(window)

    assert shown == [NO_SIGNATURES_MESSAGE]
    assert list(tmp_path.iterdir()) == []


def test_every_file_is_checked_before_the_first_is_written(tmp_path, monkeypatch):
    """A folder in the way of file 3 stops the run before file 1."""
    from deckle.app.exporting import save_signatures_as

    window, shown = _window(tmp_path, monkeypatch)
    (tmp_path / "book-sig-03.pdf").mkdir()

    save_signatures_as(window)

    assert not (tmp_path / "book-sig-01.pdf").exists()
    assert "book-sig-03.pdf" in shown[-1]


def test_the_cli_and_the_app_share_one_naming_rule():
    from deckle.cli.commands import _split_paths
    from deckle.core.outputs import signature_split_paths

    assert _split_paths("/a/b.pdf", 3) == signature_split_paths("/a/b.pdf", 3)


def test_the_menu_offers_it_and_greys_it_with_save_pdf():
    from deckle.app.menus import MENUS

    items = [item for menu in MENUS for item in menu.items]
    entry = next(i for i in items if i.action == "save_signatures")
    assert entry.gate == "document"
