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


def test_the_stored_pdf_is_not_somewhere_the_os_empties(tmp_path, monkeypatch):
    """The reboot half. A source under the system temp directory is gone
    after the crash the project was supposed to survive, and on Linux tmpfs
    it does not even need the reboot to be tmpfs-sized.

    Asserted in two steps, because the suite now runs with its data root
    redirected into a scratch directory (``tests/conftest.py``), which is
    itself under the system temp directory: an import lands in whatever
    store is configured, and the store an *unconfigured* environment
    resolves is the one that must be durable.
    """
    import tempfile

    from deckle.core.loader import import_store_dir

    source = tmp_path / "scans"
    source.mkdir()
    _make_image(str(source / "a.jpg"), dpi=(150, 150))

    stored = load_image_dir(str(source))[0].ref.path

    assert os.path.realpath(stored).startswith(
        os.path.realpath(import_store_dir())
    ), stored

    for key in ("XDG_DATA_HOME", "APPDATA"):
        monkeypatch.delenv(key, raising=False)
    temp_root = os.path.realpath(tempfile.gettempdir())
    real_store = os.path.realpath(import_store_dir())
    assert os.path.commonpath([real_store, temp_root]) != temp_root, (
        "a saved project's only source would sit in the system temp "
        f"directory: {real_store}"
    )


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


# -- B33 -----------------------------------------------------------------------


def test_the_pdfium_lock_is_free_while_the_file_is_hashed(monkeypatch):
    """Every preview waits on that lock, and a hash is seconds on a big scan."""
    import threading

    from deckle.core import loader
    from deckle.core.pdfium_lock import pdfium_guard

    free = []
    real = loader._sha256_file

    def _probe():
        # An RLock, so only another thread's answer means anything.
        with pdfium_guard():
            free.append(True)

    def _hash(path):
        probe = threading.Thread(target=_probe, daemon=True)
        probe.start()
        probe.join(2.0)
        free.append(bool(free))
        return real(path)

    monkeypatch.setattr(loader, "_sha256_file", _hash)
    pages = loader.load_pdf(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"))
    assert len(pages) == 2
    assert free[-1] is True, "the hash ran with the pdfium lock held"


def _image_folder(tmp_path, count):
    from PIL import Image

    folder = tmp_path / "scans"
    folder.mkdir()
    for i in range(count):
        # Each a different size, so a page out of order or lost shows.
        Image.new("RGB", (100 + i, 200), (i * 20, 0, 0)).save(
            folder / f"{i:03}.png", dpi=(72, 72)
        )
    return folder


def test_a_merge_holds_a_bounded_number_of_images(tmp_path, monkeypatch):
    from deckle.core import loader

    monkeypatch.setattr(loader, "_MERGE_BATCH_IMAGES", 3)
    held = []
    real = loader._flush_merge_batch

    def _spy(merged, opened, directory):
        held.append(len(opened))
        return real(merged, opened, directory)

    monkeypatch.setattr(loader, "_flush_merge_batch", _spy)

    pages = loader.load_image_dir(str(_image_folder(tmp_path, 8)))

    assert held == [3, 3]
    assert [round(page.ref.width_pt) for page in pages] == [100 + i for i in range(8)]


def test_a_batched_merge_keeps_every_pages_content(tmp_path, monkeypatch):
    """Closing a source before saving loses its content and raises nothing."""
    import pypdfium2 as pdfium

    from deckle.core import loader

    monkeypatch.setattr(loader, "_MERGE_BATCH_IMAGES", 2)
    pages = loader.load_image_dir(str(_image_folder(tmp_path, 5)))

    doc = pdfium.PdfDocument(pages[0].ref.path)
    try:
        reds = []
        for i in range(5):
            bitmap = doc[i].render(scale=1).to_pil().convert("RGB")
            reds.append(bitmap.getpixel((10, 10))[0])
    finally:
        doc.close()
    assert reds == [i * 20 for i in range(5)]


def test_no_part_files_survive_a_merge(tmp_path, monkeypatch):
    import os

    from deckle.core import loader

    monkeypatch.setattr(loader, "_MERGE_BATCH_IMAGES", 2)
    pages = loader.load_image_dir(str(_image_folder(tmp_path, 5)))
    store = os.path.dirname(pages[0].ref.path)
    assert [n for n in os.listdir(store) if n.endswith(".part.pdf") or n.startswith("tmp")] == []


def test_the_image_bytes_are_never_all_held_in_a_list(tmp_path, monkeypatch):
    """The key pass and the merge each see a generator, not the whole folder."""
    from deckle.core import loader

    seen = []
    real_key, real_merge = loader._import_key, loader._merge_image_pdfs
    monkeypatch.setattr(
        loader, "_import_key", lambda pdfs: (seen.append(type(pdfs).__name__), real_key(pdfs))[1]
    )
    monkeypatch.setattr(
        loader,
        "_merge_image_pdfs",
        lambda pdfs, out: (seen.append(type(pdfs).__name__), real_merge(pdfs, out))[1],
    )
    loader.load_image_dir(str(_image_folder(tmp_path, 3)))
    assert seen == ["generator", "generator"]


def test_a_repeat_import_lands_on_the_same_file(tmp_path, monkeypatch):
    """Batching must not change the content-addressed name."""
    from deckle.core import loader

    folder = _image_folder(tmp_path, 4)
    first = loader.load_image_dir(str(folder))
    monkeypatch.setattr(
        loader,
        "_merge_image_pdfs",
        lambda *_a: (_ for _ in ()).throw(AssertionError("merged again")),
    )
    second = loader.load_image_dir(str(folder))
    assert first[0].ref.path == second[0].ref.path
