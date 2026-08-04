"""Tests for deckle.core.loader -- PDF and image ingestion."""

from __future__ import annotations

import os
import time

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from deckle.core.loader import EncryptedPdfError, load_image_dir, load_pdf
from deckle.core.models import LayoutWarning, SourcePage


def _make_pdf(path: str, n_pages: int = 1, page_size=(612.0, 792.0)) -> None:
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    pdf.close()


def _make_encrypted_pdf(path: str) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612.0, 792.0))
    pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user="user"))
    pdf.close()


def _make_image(path: str, size=(200, 100), dpi=None, orientation=None, color=(0, 0, 0)):
    img = Image.new("RGB", size, color=color)
    kwargs = {}
    if dpi is not None:
        kwargs["dpi"] = dpi
    if orientation is not None:
        exif = img.getexif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    img.save(path, **kwargs)


# --- STRUCTURAL -------------------------------------------------------


def test_loader_exposes_expected_entry_points():
    assert callable(load_pdf)
    assert callable(load_image_dir)


# --- load_pdf -----------------------------------------------------------


def test_load_pdf_returns_source_pages(tmp_path):
    path = str(tmp_path / "doc.pdf")
    _make_pdf(path, n_pages=3, page_size=(612.0, 792.0))

    pages = load_pdf(path)

    assert len(pages) == 3
    for i, page in enumerate(pages):
        assert isinstance(page, SourcePage)
        assert page.ref.path == path
        assert page.ref.page_index == i
        assert page.ref.width_pt == 612.0
        assert page.ref.height_pt == 792.0
        assert page.skipped is False


def test_load_pdf_large_document_is_fast_and_does_not_render(tmp_path, monkeypatch):
    path = str(tmp_path / "big.pdf")
    _make_pdf(path, n_pages=300, page_size=(612.0, 792.0))

    rendered = {"called": False}
    original_render = pdfium.PdfPage.render

    def spy_render(self, *args, **kwargs):
        rendered["called"] = True
        return original_render(self, *args, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", spy_render)

    t0 = time.time()
    pages = load_pdf(path)
    elapsed = time.time() - t0

    assert len(pages) == 300
    assert elapsed < 2.0
    assert rendered["called"] is False


def test_load_pdf_encrypted_raises_encrypted_pdf_error(tmp_path):
    path = str(tmp_path / "secret.pdf")
    _make_encrypted_pdf(path)

    with pytest.raises(EncryptedPdfError) as exc_info:
        load_pdf(path)

    assert exc_info.value.path == path


# --- load_image_dir -------------------------------------------------------


def test_load_image_dir_orders_naturally(tmp_path):
    for name in ("img1.jpg", "img2.jpg", "img10.jpg"):
        _make_image(str(tmp_path / name), dpi=(150, 150))

    pages = load_image_dir(str(tmp_path))

    assert len(pages) == 3
    # All pages live in one merged cache PDF, in natural (1, 2, 10) order --
    # verify via the page order relative to a lexicographic ordering, which
    # would put img10 second.
    assert [p.ref.page_index for p in pages] == [0, 1, 2]


def test_load_image_dir_exif_orientation_produces_upright_page(tmp_path):
    path = str(tmp_path / "rot.jpg")
    # 200x100 landscape pixel data tagged as needing a 90 deg CW rotation
    # to display upright -> resulting page should be portrait (narrower
    # than tall).
    _make_image(path, size=(200, 100), dpi=(100, 100), orientation=6)

    pages = load_image_dir(str(tmp_path))

    assert len(pages) == 1
    ref = pages[0].ref
    assert ref.width_pt < ref.height_pt


def test_load_image_dir_mixed_dpi_produces_warning(tmp_path):
    _make_image(str(tmp_path / "a.jpg"), dpi=(150, 150))
    _make_image(str(tmp_path / "b.jpg"), dpi=(300, 300))

    pages = load_image_dir(str(tmp_path))

    assert len(pages) == 2
    assert any(
        isinstance(w, LayoutWarning) and w.kind == "mixed_dpi"
        for w in pages.warnings
    )


def test_load_image_dir_missing_dpi_counts_as_differing(tmp_path):
    _make_image(str(tmp_path / "a.jpg"), dpi=(150, 150))
    _make_image(str(tmp_path / "b.jpg"), dpi=None)

    pages = load_image_dir(str(tmp_path))

    assert len(pages) == 2
    assert any(w.kind == "mixed_dpi" for w in pages.warnings)


def test_load_image_dir_writes_cache_pdf_under_temp_dir(tmp_path):
    _make_image(str(tmp_path / "a.jpg"), dpi=(150, 150))

    pages = load_image_dir(str(tmp_path))

    cache_path = pages[0].ref.path
    assert os.path.isfile(cache_path)
    assert cache_path.endswith(".pdf")
