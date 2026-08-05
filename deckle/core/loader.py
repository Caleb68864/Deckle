"""SourceLoader: turns files on disk into ``SourcePage`` records.

Images are normalized to PDF pages here via img2pdf so nothing downstream
ever sees an image -- ``Imposer`` and ``Exporter`` only ever deal with PDF
pages. Import is metadata-only: no rasterization, no page copying. This
module intentionally reads page geometry via cheap metadata calls
(``PdfPage.get_size`` / PDF media boxes) and never renders a bitmap.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path

import img2pdf
import natsort
import pikepdf
import pypdfium2 as pdfium
from PIL import Image

from deckle.core.diagnostics import log_exception
from deckle.core.models import LayoutWarning, SourcePage, SourceRef

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Fallback page used when an image carries no DPI metadata at all. img2pdf's
# own fallback (96.0 dpi) is a print-size hazard -- silently guessing a
# resolution instead of a physical size. We fit the image into a standard
# page instead of trusting an invented DPI.
_FALLBACK_PAGE_PT = (612.0, 792.0)  # US Letter, matching Project defaults.

_CACHE_DIR_NAME = "deckle_import_cache"

# A-7 (docs/specs/2026-08-04-deckle-mvp.md, Edge Cases "Red-team advisories,
# resolved"): the img2pdf normalization cache is bounded and cleaned. Cap at
# 2 GB, evict least-recently-used on startup -- heavy image import otherwise
# accumulates silently in temp.
_CACHE_MAX_BYTES = 2 * 1024 ** 3


def _evict_lru_cache_entries(cache_dir: str, max_bytes: int) -> None:
    """Evict least-recently-used files from ``cache_dir`` until its total
    size is at or under ``max_bytes``.

    Runs at the start of every ``load_image_dir`` call -- the only place
    that touches this cache -- so the bound is enforced "on startup" of the
    next import rather than requiring a separate app-lifecycle hook.
    Recency is each file's last-access time (falling back to modification
    time on filesystems that don't track atime), so a file that was merely
    read still counts as recently used.
    """
    if not os.path.isdir(cache_dir):
        return

    entries: list[tuple[float, int, str]] = []
    total = 0
    for entry in os.scandir(cache_dir):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError as exc:
            # A file that vanished or is unreadable simply does not count
            # toward the cache budget. Recorded because a cache that will
            # not shrink is otherwise a mystery.
            log_exception("cache_entry_stat_failed", exc, path=entry.path)
            continue
        total += stat.st_size
        recency = getattr(stat, "st_atime", None) or stat.st_mtime
        entries.append((recency, stat.st_size, entry.path))

    if total <= max_bytes:
        return

    entries.sort(key=lambda item: item[0])  # oldest-accessed first
    for _recency, size, file_path in entries:
        if total <= max_bytes:
            break
        try:
            os.remove(file_path)
        except OSError as exc:
            # Undeletable entry -- skip it and keep evicting others. Note
            # `total` is deliberately NOT decremented here: the bytes are
            # still on disk, so pretending otherwise would end eviction
            # early and leave the cache over budget.
            log_exception("cache_eviction_failed", exc, path=file_path)
            continue
        total -= size


class SourceLoadError(Exception):
    """Base for every refusal to turn a path into ``SourcePage``s.

    Loading is the first thing Deckle does and the first thing that goes
    wrong: a path typed with a typo, a PDF that finished downloading
    halfway, a folder of scans that turned out to hold only a README. Every
    one of those used to surface as somebody else's exception --
    ``FileNotFoundError``, or ``PdfiumError: Failed to load document
    (PDFium: Success)``, which is a genuinely unreadable thing to hand a
    bookbinder.

    So every subclass carries the offending ``path`` as an attribute and a
    message that names three things: what failed, which file, and what the
    user can do next. Callers that only need "did the import fail, and what
    do I tell the user" can catch this base class and print ``str(exc)``.
    """

    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(message)


class MissingSourceError(SourceLoadError):
    """Raised when the source path does not exist at all."""


class UnreadableSourceError(SourceLoadError):
    """Raised when the source exists but cannot be opened for reading."""


class CorruptPdfError(SourceLoadError):
    """Raised when a file offered as a PDF cannot be parsed as one.

    Covers both "this is not a PDF at all" (no ``%PDF`` header, e.g. an
    HTML error page saved under a ``.pdf`` name) and "this was a PDF and is
    now damaged" (truncated by an interrupted copy). The two get different
    advice, so they are distinguished in the message rather than by type.
    """


class EmptyPdfError(SourceLoadError):
    """Raised when a PDF parses but contains no pages.

    Worth its own type because PDFium reports a zero-page document as
    ``Failed to load document (PDFium: Success)`` -- an error string that
    tells the user nothing and sends the reader looking for corruption that
    is not there.
    """


class NoImagesFoundError(SourceLoadError):
    """Raised when a directory import finds no files Deckle can import.

    Previously this returned zero pages and reported success, so pointing
    Deckle at the wrong folder produced an empty booklet with no complaint.
    """


class CorruptImageError(SourceLoadError):
    """Raised when a file with an image extension cannot be decoded.

    Deliberately fatal to the whole import rather than skipped. A page
    quietly missing from a book is discovered after it is folded and sewn;
    a refused import is discovered immediately.
    """


class EncryptedPdfError(SourceLoadError):
    """Raised by ``load_pdf`` when the PDF is password-protected."""

    def __init__(self, path: str):
        super().__init__(
            path,
            f"cannot open {path}: the PDF is password-protected. Deckle "
            "cannot supply a password -- open it in a PDF viewer, save an "
            "unprotected copy, and import that instead.",
        )


class ImportedPages(list):
    """A ``list[SourcePage]`` that also carries non-fatal import warnings.

    ``load_image_dir`` behaves as a plain ``list[SourcePage]`` for every
    caller that only cares about pages, while still surfacing warnings
    (e.g. mixed DPI across an import) via the ``.warnings`` attribute.
    """

    def __init__(self, pages, warnings: list[LayoutWarning] | None = None):
        super().__init__(pages)
        self.warnings: list[LayoutWarning] = warnings or []


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_encrypted(path: str) -> bool:
    try:
        with pikepdf.open(path):
            return False
    except pikepdf.PasswordError:
        return True
    except Exception as exc:  # noqa: BLE001 -- classification probe, not the open
        # Not a password issue -- some other corruption; let the original
        # pypdfium2 error surface instead of misreporting it as encrypted.
        # The probe's own failure is still worth recording: the error the
        # user eventually sees comes from a different library, so without
        # this the pikepdf-side detail is lost entirely.
        log_exception("encryption_probe_failed", exc, path=path)
        return False


def _drive_hint(path: str) -> str:
    """A clause naming the drive when the path's drive is what is missing.

    ``Q:\\book.pdf`` on a machine with no ``Q:`` is a real and common
    Windows case -- a mapped network drive that is not connected, or a USB
    stick that was pulled. "no such file" is technically true and
    practically useless there; naming the drive is the whole diagnosis.
    """
    anchor = Path(path).anchor
    if anchor and not os.path.exists(anchor):
        return f" The drive {anchor} is not available -- is it disconnected or unmapped?"
    return ""


def _require_readable_file(path: str) -> None:
    """Reject a source path before any library gets a chance to mangle it.

    pypdfium2 raises a bare ``FileNotFoundError`` carrying only the path,
    and reports an unreadable file as a parse failure. Checking here means
    the caller gets one exception type with one actionable message for all
    three of "not there", "is a folder", and "cannot read it".
    """
    if os.path.isdir(path):
        raise UnreadableSourceError(
            path,
            f"cannot read {path}: that is a folder, not a PDF file. To "
            "import a folder of scans, point Deckle at the folder itself "
            "(it will be imported as images); to import a PDF, name the "
            "PDF file.",
        )
    if not os.path.exists(path):
        raise MissingSourceError(
            path,
            f"cannot read {path}: no such file.{_drive_hint(path)} Check "
            "the path for typos and try again.",
        )
    if not os.access(path, os.R_OK):
        raise UnreadableSourceError(
            path,
            f"cannot read {path}: permission denied. Check the file's "
            "permissions, or copy it somewhere you can read.",
        )


def _has_pdf_header(path: str) -> bool:
    """Whether the file starts with ``%PDF``, the PDF magic number.

    A file named ``.pdf`` that is really an HTML error page or a partial
    download is common enough that "this is not a PDF at all" deserves
    different advice from "this PDF is damaged".
    """
    try:
        with open(path, "rb") as f:
            return f.read(5).startswith(b"%PDF")
    except OSError as exc:
        log_exception("pdf_header_probe_failed", exc, path=path)
        return False


def _pikepdf_page_count(path: str) -> int | None:
    """The page count according to pikepdf, or ``None`` if it cannot open it.

    Used only to classify a PDFium open failure. pikepdf is stricter about
    some things and more forgiving about others; where it *can* open a file
    PDFium rejected, its page count tells us whether the real problem is
    simply that there are no pages.
    """
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception as exc:  # noqa: BLE001 -- classification probe, not the open
        log_exception("page_count_probe_failed", exc, path=path)
        return None


def _classify_pdf_open_failure(path: str, exc: Exception) -> SourceLoadError:
    """Turn a PDFium open failure into an error a human can act on.

    PDFium's own strings are the problem this exists to solve: a zero-page
    document reports ``Failed to load document (PDFium: Success)``, and an
    encrypted one reports a format error indistinguishable from corruption.
    """
    if _is_encrypted(path):
        return EncryptedPdfError(path)
    if _pikepdf_page_count(path) == 0:
        return EmptyPdfError(
            path,
            f"cannot use {path}: the PDF has no pages, so there is nothing "
            "to impose. Check that the export that produced it actually "
            "wrote its pages.",
        )
    if not _has_pdf_header(path):
        return CorruptPdfError(
            path,
            f"cannot read {path}: this file is not a PDF -- it does not "
            "begin with a %PDF header, despite its name. It may be an "
            "error page or a partial download saved under a .pdf name. "
            "Open it in a text editor to see what it really is.",
        )
    return CorruptPdfError(
        path,
        f"cannot read {path}: the PDF is damaged or truncated ({exc}). "
        "Try opening it in a PDF viewer; if that fails too, re-download or "
        "re-export the original.",
    )


def load_pdf(path: str) -> list[SourcePage]:
    """Load an existing PDF's pages, metadata-only (no rasterization).

    :param path: the PDF file to read.
    :returns: one :class:`~deckle.core.models.SourcePage` per PDF page.
    :raises MissingSourceError: the path does not exist.
    :raises UnreadableSourceError: the path is a directory, or cannot be
        opened for reading.
    :raises EncryptedPdfError: the PDF is password-protected.
    :raises EmptyPdfError: the PDF parses but has no pages.
    :raises CorruptPdfError: the file is damaged, truncated, or not a PDF.

    Every one of those is a :class:`SourceLoadError` carrying ``path``, so
    a caller that just needs a message can catch the base class. Nothing
    from pypdfium2 or pikepdf escapes this function.
    """
    _require_readable_file(path)
    try:
        doc = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise _classify_pdf_open_failure(path, exc) from exc

    if len(doc) == 0:
        # Reachable independently of the open failure above: some zero-page
        # files open cleanly and only reveal themselves on len().
        raise EmptyPdfError(
            path,
            f"cannot use {path}: the PDF has no pages, so there is nothing "
            "to impose. Check that the export that produced it actually "
            "wrote its pages.",
        )

    sha256 = _sha256_file(path)
    pages: list[SourcePage] = []
    for index in range(len(doc)):
        page = doc[index]
        width_pt, height_pt = page.get_size()
        ref = SourceRef(
            path=path,
            page_index=index,
            sha256=sha256,
            width_pt=float(width_pt),
            height_pt=float(height_pt),
        )
        pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))
    return pages


def _scan_image_dir(dir_path: str) -> tuple[list[str], list[str]]:
    """Split a directory's files into importable images and everything else.

    The "everything else" half is not waste: a folder of scans that also
    contains ``Thumbs.db`` is normal and silent, whereas a folder where
    *every* file was skipped means the user picked the wrong folder. Both
    answers need the same scan, so it is done once here.

    :returns: ``(image_paths, skipped_names)`` -- images in natural sort
        order, skipped entries by bare filename.
    :raises MissingSourceError: ``dir_path`` does not exist.
    :raises UnreadableSourceError: ``dir_path`` cannot be listed.
    """
    if not os.path.exists(dir_path):
        raise MissingSourceError(
            dir_path,
            f"cannot read {dir_path}: no such folder.{_drive_hint(dir_path)} "
            "Check the path for typos and try again.",
        )
    if not os.path.isdir(dir_path):
        raise UnreadableSourceError(
            dir_path,
            f"cannot read {dir_path} as a folder of images: it is a file. "
            "Import it directly if it is a PDF, or point Deckle at the "
            "folder that contains your images.",
        )

    images: list[str] = []
    skipped: list[str] = []
    try:
        entries = list(os.scandir(dir_path))
    except OSError as exc:
        raise UnreadableSourceError(
            dir_path,
            f"cannot list {dir_path}: {exc.strerror or exc}. Check the "
            "folder's permissions, or copy the images somewhere you can "
            "read.",
        ) from exc

    for entry in entries:
        if not entry.is_file():
            continue
        if Path(entry.path).suffix.lower() in IMAGE_EXTENSIONS:
            images.append(entry.path)
        else:
            skipped.append(entry.name)

    # Natural sort so img1, img2, img10 stay in numeric order rather than
    # the lexicographic img1, img10, img2.
    return natsort.natsorted(images), sorted(skipped)


def _image_dpi(image_path: str) -> tuple[float, float] | None:
    with Image.open(image_path) as img:
        dpi = img.info.get("dpi")
    if not dpi:
        return None
    x, y = dpi
    if not x or not y:
        return None
    return (float(x), float(y))


def _image_dpi_or_raise(image_path: str) -> tuple[float, float] | None:
    """``_image_dpi``, but reporting an undecodable file as Deckle's own error.

    Pillow's ``UnidentifiedImageError`` names the file but not the remedy,
    and a ``.png`` that is really a truncated download is exactly the input
    a user cannot diagnose from the library's wording.
    """
    try:
        return _image_dpi(image_path)
    except OSError as exc:
        # Covers Pillow's UnidentifiedImageError (an OSError subclass) as
        # well as genuine read failures.
        raise CorruptImageError(
            image_path,
            f"cannot read the image {image_path}: {exc}. It may be damaged "
            "or only partly copied. Remove or replace it, then import the "
            "folder again.",
        ) from exc


def _single_image_pdf_or_raise(
    image_path: str, dpi: tuple[float, float] | None
) -> bytes:
    """``_single_image_pdf_bytes``, with img2pdf's refusals named for the user."""
    try:
        return _single_image_pdf_bytes(image_path, dpi)
    except Exception as exc:  # noqa: BLE001 -- img2pdf raises bare ValueError
        raise CorruptImageError(
            image_path,
            f"cannot convert the image {image_path} to a PDF page: {exc}. "
            "Deckle could not use this file even though its extension says "
            "it is an image -- re-save it as a normal JPEG or PNG, or "
            "remove it from the folder.",
        ) from exc


def _single_image_pdf_bytes(image_path: str, dpi: tuple[float, float] | None) -> bytes:
    kwargs = {"rotation": img2pdf.Rotation.ifvalid}
    if dpi is None:
        # No usable DPI metadata: fit to a standard page instead of
        # inheriting img2pdf's silent 96.0 dpi default.
        kwargs["layout_fun"] = img2pdf.get_layout_fun(
            pagesize=_FALLBACK_PAGE_PT, fit=img2pdf.FitMode.into
        )
    # Pass the original file bytes straight to img2pdf -- never round-trip
    # the pixel data through Pillow's own PDF writer, which measurably
    # mangles JPEG content even at quality='keep'.
    with open(image_path, "rb") as f:
        raw = f.read()
    return img2pdf.convert(raw, **kwargs)


def load_image_dir(
    path: str, cache_max_bytes: int | None = None
) -> list[SourcePage]:
    """Import a directory of images as one normalized PDF, metadata-only.

    Writes a single normalized PDF (one page per image, in natural sort
    order) into a cache directory under the OS temp dir, and returns
    ``SourcePage``s whose ``SourceRef``s point at that cached PDF.

    The cache directory is bounded (A-7): before writing, least-recently-used
    entries are evicted until the directory's total size is at or under
    ``cache_max_bytes`` (default 2 GB, ``_CACHE_MAX_BYTES``). The parameter
    exists mainly so tests can exercise eviction without writing 2 GB of
    fixtures; production callers should leave it at the default.

    :raises MissingSourceError: ``path`` does not exist.
    :raises UnreadableSourceError: ``path`` is a file, or cannot be listed.
    :raises NoImagesFoundError: the folder contains no importable images.
    """
    max_bytes = _CACHE_MAX_BYTES if cache_max_bytes is None else cache_max_bytes
    image_paths, skipped_names = _scan_image_dir(path)

    if not image_paths:
        extensions = ", ".join(sorted(IMAGE_EXTENSIONS))
        if skipped_names:
            detail = (
                f"none of its {len(skipped_names)} file(s) are images "
                f"(e.g. {', '.join(skipped_names[:3])})"
            )
        else:
            detail = "the folder is empty"
        raise NoImagesFoundError(
            path,
            f"no images to import from {path}: {detail}. Deckle imports "
            f"{extensions}; point it at the folder that actually holds the "
            "scans, or import a PDF instead.",
        )

    warnings: list[LayoutWarning] = []
    if skipped_names:
        # Advisory, not fatal: a folder of scans routinely also holds a
        # Thumbs.db or a README. Said out loud anyway, because the other
        # reason for a skipped file is a scan in a format Deckle does not
        # read -- and that silently drops a page out of the book.
        warnings.append(
            LayoutWarning(
                sheet_index=-1,  # not yet applicable at import time
                kind="skipped_non_image_files",
                detail=(
                    f"{len(skipped_names)} file(s) in {path!r} were not "
                    f"imported because they are not images: "
                    f"{', '.join(skipped_names[:5])}"
                    + ("..." if len(skipped_names) > 5 else "")
                ),
            )
        )

    dpis = [_image_dpi_or_raise(p) for p in image_paths]
    distinct = {tuple(round(v, 3) for v in d) if d else None for d in dpis}
    if len(distinct) > 1:
        warnings.append(
            LayoutWarning(
                sheet_index=-1,  # not yet applicable at import time
                kind="mixed_dpi",
                detail=(
                    f"images in {path!r} have differing or missing DPI: "
                    f"{sorted((d for d in distinct), key=lambda x: (x is None, x))}"
                ),
            )
        )

    per_image_pdfs = [
        _single_image_pdf_or_raise(img_path, dpi)
        for img_path, dpi in zip(image_paths, dpis)
    ]

    merged = pikepdf.Pdf.new()
    opened = []
    try:
        for pdf_bytes in per_image_pdfs:
            single = pikepdf.open(io.BytesIO(pdf_bytes))
            opened.append(single)
            merged.pages.extend(single.pages)

        cache_dir = os.path.join(tempfile.gettempdir(), _CACHE_DIR_NAME)
        os.makedirs(cache_dir, exist_ok=True)
        _evict_lru_cache_entries(cache_dir, max_bytes)
        fd, out_path = tempfile.mkstemp(suffix=".pdf", dir=cache_dir)
        os.close(fd)
        merged.save(out_path)
    finally:
        for single in opened:
            single.close()
        merged.close()

    sha256 = _sha256_file(out_path)
    pages: list[SourcePage] = []
    with pikepdf.open(out_path) as result:
        for index, page in enumerate(result.pages):
            box = page.mediabox
            width_pt = float(box[2]) - float(box[0])
            height_pt = float(box[3]) - float(box[1])
            # img2pdf encodes EXIF rotation as a page /Rotate flag rather
            # than baking it into the media box -- swap dimensions here so
            # SourceRef reports the actual upright (displayed) size, which
            # is what Imposer needs.
            rotate = int(page.get("/Rotate", 0)) % 360
            if rotate in (90, 270):
                width_pt, height_pt = height_pt, width_pt
            ref = SourceRef(
                path=out_path,
                page_index=index,
                sha256=sha256,
                width_pt=width_pt,
                height_pt=height_pt,
            )
            pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))

    return ImportedPages(pages, warnings)
