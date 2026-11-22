"""Save a folding dummy: this book's imposition, numbered, ready to fold.

F15 (output commands in scope, decided by agent 2026-11-21; owner may
revisit). ``deckle-cli dummy`` writes a numbered source to impose by hand;
the app knows the layout, so it imposes the stand-in itself -- through the
ordinary import-impose-export path, which is what makes it a check of the
real pipeline rather than of a second one.
"""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest

from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.planning import impose_project

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


def _book(n=8, fold_scheme="folio"):
    pages = load_pdf(FIXTURE)
    pages = [replace(pages[i % len(pages)]) for i in range(n)]
    layout = LayoutSettings(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                            fold_scheme=fold_scheme, sheets_per_signature=2,
                            crop_odd_pt=(10.0, 10.0, 10.0, 10.0))
    return Project(pages, layout, None)


def test_the_stand_in_keeps_blanks_and_skips_and_drops_rotation_and_crop(tmp_path):
    from deckle.core.dummy import make_numbered_pdf, stand_in_project
    from deckle.core.models import BLANK_SOURCE_PATH

    project = _book(4)
    blank = SourcePage(SourceRef(BLANK_SOURCE_PATH, 0, "", 612.0, 792.0), 0, False)
    pages = list(project.pages)
    pages[1] = blank
    pages[2] = replace(pages[2], skipped=True, rotate_deg=180)
    project = replace(project, pages=pages)
    numbered = tmp_path / "n.pdf"
    make_numbered_pdf(str(numbered), 4)

    stand_in = stand_in_project(project, load_pdf(str(numbered)))

    assert stand_in.pages[1] is blank
    assert stand_in.pages[2].skipped and stand_in.pages[2].rotate_deg == 0
    assert [p.ref.page_index for i, p in enumerate(stand_in.pages) if i != 1] == [0, 2, 3]
    assert stand_in.layout.crop_odd_pt is None
    assert stand_in.layout.fold_scheme == "folio"


def test_the_dummy_folds_as_the_book_does():
    """Same layout, same page count: the same sheets, signatures and order."""
    from deckle.core.dummy import make_numbered_pdf, stand_in_project
    import tempfile

    project = _book(8)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "n.pdf")
        make_numbered_pdf(path, 8, (612.0, 792.0))
        dummy = impose_project(stand_in_project(project, load_pdf(path)))
    real = impose_project(replace(project, layout=replace(project.layout, crop_odd_pt=None)))

    assert len(dummy.sheets) == len(real.sheets)
    assert [s.sheet_indices for s in dummy.signatures] == [s.sheet_indices for s in real.signatures]

    # The dummy's numbers are list positions: page i is numbered i + 1.
    first = dummy.sheets[0].front.pages
    assert sorted(p.source_ref.page_index for p in first if p.source_ref) == [0, 7]


@pytest.fixture
def _app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path_factory, monkeypatch):
    from deckle.core import diagnostics

    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


def _window(project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from deckle.app.workers import run_inline

    shown = []
    window = SimpleNamespace(
        state=SimpleNamespace(project=project),
        status_bar=SimpleNamespace(showMessage=shown.append),
        _jobs={}, _job_runner=run_inline, window=None,
        save_pdf_button=SimpleNamespace(setEnabled=lambda _v: None),
        _sync_document_actions=lambda: None,
        suggested_export_name=lambda: "book.pdf",
    )
    target = str(tmp_path / "book-dummy.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    return window, shown, target


def test_the_app_writes_the_imposed_dummy(_app, tmp_path, monkeypatch):
    from deckle.app.exporting import save_folding_dummy_as

    project = _book(8)
    window, shown, target = _window(project, tmp_path, monkeypatch)

    save_folding_dummy_as(window)

    plan = impose_project(project)
    pdf = pdfium.PdfDocument(target)
    try:
        assert len(pdf) == 2 * len(plan.sheets)
        # The folio's outer sheet front carries 8 and 1, as the dummy's own
        # docstring says to check.
        text = pdf[0].get_textpage().get_text_range()
        assert "8" in text and "1" in text
    finally:
        pdf.close()
    assert shown[-1].startswith("Saved a folding dummy")


def test_a_card_project_is_told_a_dummy_is_for_books(_app, tmp_path, monkeypatch):
    from deckle.app.exporting import DUMMY_CARDS_MESSAGE, save_folding_dummy_as
    from deckle.core.models import CardAsset, CardDeck, CardEntry, CardFace, CardSettings

    ref = SourceRef("/f.pdf", 0, "a" * 64, 100.0, 140.0)
    asset = CardAsset("f", "f.png", "/f.png", "c" * 64, SourcePage(ref, 0, False))
    deck = CardDeck((asset,), (CardEntry("one", CardFace("f"), CardFace("f")),), CardSettings())
    project = Project([], LayoutSettings((612.0, 792.0), 18.0, "left"), None, "cards", deck)
    window, shown, target = _window(project, tmp_path, monkeypatch)

    save_folding_dummy_as(window)

    assert shown == [DUMMY_CARDS_MESSAGE]
    assert not os.path.exists(target)


def test_the_menu_offers_it():
    from deckle.app.menus import MENUS

    entry = next(i for m in MENUS for i in m.items if i.action == "save_folding_dummy")
    assert entry.gate == "document"
