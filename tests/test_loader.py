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


def test_load_image_dir_writes_its_pdf_into_the_import_store(tmp_path):
    _make_image(str(tmp_path / "a.jpg"), dpi=(150, 150))

    pages = load_image_dir(str(tmp_path))

    stored = pages[0].ref.path
    assert os.path.isfile(stored)
    assert stored.endswith(".pdf")
    assert os.path.isabs(stored)


# --- B26: the normalised PDF is storage, not a cache ---------------------
#
# It used to be written into `<tempdir>/deckle_import_cache/` under a 2 GB
# least-recently-used budget, and every `SourceRef` for a folder of scans
# points at it -- so a saved project's only source was a file Deckle's own
# eviction pass deleted, by preferring the least-recently-used entry, which
# is precisely the project you have not opened in months. The OS emptied
# the directory on the next reboot regardless.
#
# The bound it replaced was A-7 (docs/specs/2026-08-04-deckle-mvp.md, Edge
# Cases, "Red-team advisories, resolved"), whose concern was heavy image
# import accumulating silently in temp. That concern is answered by
# content-addressing rather than by deletion -- see
# `test_reimporting_the_same_folder_adds_nothing_to_the_store` below and
# the entry in docs/decisions.md. `evict_lru_files` itself is unchanged and
# still bounds the *export* cache, which references nothing.
#
# **Which of these actually fail against the old code, and why.** Only
# `test_the_stored_pdf_is_not_somewhere_the_os_empties` does so for the
# real reason: the referenced PDF was under `tempfile.gettempdir()`. The
# three below it fail against the old code merely because
# `import_store_dir` did not exist to patch -- reproducing the eviction
# itself needed either the real 2 GB budget or the `cache_max_bytes`
# parameter the fix removes, so there is no honest way to drive it here.
# They pin the invariant going forward rather than the bug going back, and
# say so rather than implying coverage they do not have.


def _isolated_store(monkeypatch, tmp_path):
    """Point the import store at a directory of this test's own.

    Patched on the module rather than via ``XDG_DATA_HOME`` because the
    environment variable only steers ``data_dir`` on Linux -- Windows and
    macOS read the home directory, so an env-based test would silently
    write into the developer's real store there.
    """
    from deckle.core import loader

    store = tmp_path / "store"
    monkeypatch.setattr(loader, "import_store_dir", lambda: str(store))
    return store


def test_the_stored_pdf_is_not_somewhere_the_os_empties(tmp_path):
    """The reboot half. A source under the system temp directory is gone
    after the crash the project was supposed to survive, and on Linux tmpfs
    it does not even need the reboot to be tmpfs-sized."""
    import tempfile

    from deckle.core.loader import import_store_dir

    source = tmp_path / "scans"
    source.mkdir()
    _make_image(str(source / "a.jpg"), dpi=(150, 150))

    stored = load_image_dir(str(source))[0].ref.path

    temp_root = os.path.realpath(tempfile.gettempdir())
    assert os.path.commonpath([os.path.realpath(stored), temp_root]) != temp_root, (
        "a saved project's only source is sitting in the system temp "
        f"directory: {stored}"
    )
    assert os.path.realpath(stored).startswith(
        os.path.realpath(import_store_dir())
    ), stored


def test_a_later_import_does_not_delete_an_earlier_one(tmp_path, monkeypatch):
    """The eviction half, driven the way it actually happened: import a
    book of scans, then import something else. The first import's PDF is
    the least-recently-used file in the directory, so it was the first to
    go -- taking the saved project that referenced it with it."""
    store = _isolated_store(monkeypatch, tmp_path)

    first = tmp_path / "book"
    first.mkdir()
    _make_image(str(first / "a.jpg"), size=(400, 300), dpi=(150, 150))
    kept = load_image_dir(str(first))[0].ref.path
    assert os.path.isfile(kept)

    # Distinguishable access times on filesystems with coarse timestamps,
    # so "least recently used" would have had an unambiguous answer.
    time.sleep(0.05)

    second = tmp_path / "leaflet"
    second.mkdir()
    # Different content, so this genuinely is a second file in the store
    # rather than the same one found again by its hash.
    _make_image(str(second / "b.jpg"), size=(320, 240), dpi=(150, 150),
                color=(255, 0, 0))
    load_image_dir(str(second))

    assert os.path.isfile(kept), (
        "importing a second folder deleted the first folder's normalised "
        "PDF -- which is the only source a project made from those scans has"
    )
    assert len(list(store.glob("*.pdf"))) == 2


def test_reimporting_the_same_folder_adds_nothing_to_the_store(tmp_path, monkeypatch):
    """What replaces the 2 GB budget. The store is never pruned, so the
    repeat that would otherwise pile up -- the same book imported again
    after a cancelled job, or to start a second project from it -- has to
    cost nothing. Identical images normalise to identical bytes, and the
    file is named by its own hash."""
    store = _isolated_store(monkeypatch, tmp_path)

    source = tmp_path / "scans"
    source.mkdir()
    _make_image(str(source / "a.jpg"), size=(400, 300), dpi=(150, 150))

    first = load_image_dir(str(source))[0].ref.path
    second = load_image_dir(str(source))[0].ref.path

    assert first == second
    assert len(list(store.glob("*.pdf"))) == 1, (
        f"re-importing the same folder left {sorted(p.name for p in store.iterdir())}"
    )


def test_a_repeat_import_leaves_the_stored_file_alone(tmp_path, monkeypatch):
    """Dedupe must not be "overwrite with the same thing". Another project
    is already referencing that file, and replacing it would put a window
    -- however short -- where the reference points at nothing."""
    _isolated_store(monkeypatch, tmp_path)

    source = tmp_path / "scans"
    source.mkdir()
    _make_image(str(source / "a.jpg"), size=(400, 300), dpi=(150, 150))

    stored = load_image_dir(str(source))[0].ref.path
    before = os.stat(stored)
    time.sleep(0.05)

    load_image_dir(str(source))

    after = os.stat(stored)
    assert (after.st_ino, after.st_mtime) == (before.st_ino, before.st_mtime)
