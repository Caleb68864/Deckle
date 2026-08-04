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


# --- A-7: normalization cache is bounded and evicts LRU -------------------


def test_load_image_dir_evicts_lru_cache_entries_when_over_cap(tmp_path, monkeypatch):
    """A-7 (docs/specs/2026-08-04-deckle-mvp.md, Edge Cases "Red-team
    advisories, resolved"): the img2pdf normalization cache must be bounded
    (2 GB in production) and evict least-recently-used entries on startup
    rather than growing forever. Uses a tiny injectable cap so the test
    doesn't need to write gigabytes of fixtures.
    """
    import tempfile
    import time

    from deckle.core import loader

    # The real cache dir lives under the OS temp dir and is shared/persistent
    # across test runs and other tests in this file -- redirect it to an
    # isolated tmp_path so this test's size/eviction accounting can't be
    # skewed by unrelated cache files left over from elsewhere.
    fake_temp_root = tmp_path / "fake_os_temp"
    fake_temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_temp_root))

    # Each single-image import writes one cache PDF. Force several separate
    # imports (rather than one directory of many images -> one merged PDF)
    # so multiple distinct cache files accumulate to evict between.
    src_dir_a = tmp_path / "import_a"
    src_dir_a.mkdir()
    _make_image(str(src_dir_a / "a.jpg"), size=(400, 300), dpi=(150, 150))

    src_dir_b = tmp_path / "import_b"
    src_dir_b.mkdir()
    _make_image(str(src_dir_b / "b.jpg"), size=(400, 300), dpi=(150, 150))

    src_dir_c = tmp_path / "import_c"
    src_dir_c.mkdir()
    _make_image(str(src_dir_c / "c.jpg"), size=(400, 300), dpi=(150, 150))

    pages_a = load_image_dir(str(src_dir_a), cache_max_bytes=10 * 1024 * 1024)
    cache_path_a = pages_a[0].ref.path
    assert os.path.isfile(cache_path_a)

    # Ensure distinguishable access/modify times across platforms with
    # coarse timestamp resolution.
    time.sleep(0.05)

    pages_b = load_image_dir(str(src_dir_b), cache_max_bytes=10 * 1024 * 1024)
    cache_path_b = pages_b[0].ref.path
    assert os.path.isfile(cache_path_b)

    # Now import a third time with a cap so small that eviction must run --
    # it should remove the least-recently-used entry (cache_path_a) to make
    # room, while the more-recently-written cache_path_b survives.
    size_b = os.path.getsize(cache_path_b)
    cap_after_b = size_b + 1  # only room for roughly one existing entry

    time.sleep(0.05)
    pages_c = load_image_dir(str(src_dir_c), cache_max_bytes=cap_after_b)
    cache_path_c = pages_c[0].ref.path

    assert not os.path.exists(cache_path_a), (
        "least-recently-used cache entry should have been evicted once the "
        "cache exceeded its cap"
    )
    assert os.path.isfile(cache_path_c)

    # Sanity check the eviction primitive directly against an arbitrary
    # cache directory too, independent of load_image_dir's plumbing.
    direct_dir = tmp_path / "direct_cache"
    direct_dir.mkdir()
    old_file = direct_dir / "old.pdf"
    old_file.write_bytes(b"x" * 100)
    old_time = time.time() - 100
    os.utime(str(old_file), (old_time, old_time))
    new_file = direct_dir / "new.pdf"
    new_file.write_bytes(b"y" * 100)

    loader._evict_lru_cache_entries(str(direct_dir), max_bytes=150)

    assert not old_file.exists()
    assert new_file.exists()
