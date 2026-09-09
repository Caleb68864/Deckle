"""SourceLoader: turns files on disk into ``SourcePage`` records.

Images are normalized to PDF pages here via img2pdf so nothing downstream
ever sees an image -- ``Imposer`` and ``Exporter`` only ever deal with PDF
pages. Import is metadata-only: no rasterization, no page copying. This
module intentionally reads page geometry via cheap metadata calls
(``PdfPage.get_size`` / PDF media boxes) and never renders a bitmap.

Every ``SourceRef`` this module builds carries an **absolute** path, and
for a folder of images that path names a file in :func:`import_store_dir`
that is kept for good. Both facts exist for the same reason: a ``.deckle``
records references rather than content, so the reference has to still mean
something when it is read from another directory, another day, or after a
reboot.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import img2pdf
import natsort
import pikepdf
import pypdfium2 as pdfium
from PIL import Image

from deckle.core.diagnostics import log_exception
from deckle.core.models import LayoutWarning, SourcePage, SourceRef
from deckle.core.paths import atomic_output, data_dir
from deckle.core.render import pdfium_guard

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Fallback page used when an image carries no DPI metadata at all. img2pdf's
# own fallback (96.0 dpi) is a print-size hazard -- silently guessing a
# resolution instead of a physical size. We fit the image into a standard
# page instead of trusting an invented DPI.
_FALLBACK_PAGE_PT = (612.0, 792.0)  # US Letter, matching Project defaults.

_IMPORT_STORE_DIR_NAME = "imported"


def import_store_dir() -> str:
    """Where an image folder's normalised PDF is kept, for good.

    A folder of scans has no PDF of its own, so ``load_image_dir`` makes
    one -- and every ``SourceRef`` for those pages names *that* file, not
    the images. A ``.deckle`` referencing it is therefore only as durable
    as the file is.

    It used to live in ``<tempdir>/deckle_import_cache/`` under a 2 GB
    least-recently-used budget (A-7), which made it exactly as durable as
    a cache: **Deckle deleted its own saved projects' sources.** Import a
    book of scans, save the project, import anything else months later,
    and the eviction pass took the least-recently-used entry -- which is
    the one belonging to the project you have not opened in months. The
    OS emptied the directory on the next reboot in any case. Either way
    the project opened to "a source file is missing", naming a path like
    ``/tmp/deckle_import_cache/tmpq4k1z0.pdf`` that the user never chose
    and cannot go and find.

    So this is not a cache and is not evicted. It lives under
    :func:`deckle.core.paths.data_dir` beside the session log, which is
    where things Deckle accumulates and must not lose already live -- the
    same move B27 makes for print-session state, for the same reason.

    :returns: the directory. Nothing is created; the writer makes it.
    """
    return str(data_dir(_IMPORT_STORE_DIR_NAME))


def _import_key(per_image_pdfs: list[bytes]) -> str:
    """A name for the normalised PDF a set of images will produce.

    Content-addressed, which is what keeps a store nobody prunes from
    growing without limit in the case that actually repeats: the same
    folder of scans imported again -- after a cancelled job, or to start a
    second project from the same book -- lands on the name already there
    and costs nothing.

    Derived from the per-image PDFs the caller is already holding rather
    than from the merged file, so the answer is known *before* anything is
    written and a repeat import can skip the merge entirely. Each page's
    digest is folded in rather than its bytes concatenated, so where one
    page ends and the next begins is part of the key.

    :param per_image_pdfs: one single-page PDF per image, in page order.
    :returns: a hex digest, used as the stored file's stem.
    """
    digest = hashlib.sha256()
    for pdf_bytes in per_image_pdfs:
        digest.update(hashlib.sha256(pdf_bytes).digest())
    return digest.hexdigest()


def _merge_image_pdfs(per_image_pdfs: list[bytes], out_path: str) -> None:
    """Concatenate single-page PDFs into ``out_path``, one page each.

    Written through :func:`deckle.core.paths.atomic_output`, like every
    other file Deckle names: an import killed partway must not leave a
    truncated PDF under a name a project will later trust, and this one is
    named by content, so a truncated file would sit there claiming to be
    the whole book.

    :param per_image_pdfs: one single-page PDF per image, in page order.
    :param out_path: the file to end up with.
    :raises OSError: the file cannot be written.
    """
    merged = pikepdf.Pdf.new()
    opened = []
    try:
        for pdf_bytes in per_image_pdfs:
            single = pikepdf.open(io.BytesIO(pdf_bytes))
            opened.append(single)
            merged.pages.extend(single.pages)
        with atomic_output(out_path) as scratch:
            merged.save(scratch)
    finally:
        for single in opened:
            single.close()
        merged.close()


class SourceLoadError(Exception):
    """Base for every refusal to turn a path into ``SourcePage``\\ s.

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

    :param path: the offending path.
    :param message: the user-facing explanation, ending in a remedy.
    :ivar path: the offending path, available on every subclass.
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
    """Raised by ``load_pdf`` when the PDF is password-protected.

    Deckle cannot supply a password, so the message tells the user to save
    an unprotected copy rather than offering an option that does not exist.

    :param path: the encrypted PDF. The message is composed here, so this
        is the only subclass whose constructor takes no ``message``.
    """

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

    :param pages: the loaded pages.
    :param warnings: non-fatal import advisories, or ``None`` for none.
    :ivar warnings: the advisories. Callers read them via
        ``getattr(pages, "warnings", [])`` so a plain list works too --
        which is exactly why wrapping the result in ``list()`` silently
        drops every one of them.
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
    :returns: one :class:`~deckle.core.models.SourcePage` per PDF page,
        each carrying the **absolute** path, so a project saved from one
        directory opens from another. Error messages still name the path
        as the caller typed it.
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
    return _read_pdf_pages(path)


def _read_pdf_pages(path: str) -> list[SourcePage]:
    """Open ``path`` and measure every page, holding the pdfium guard.

    pdfium's state is process-global, so an import racing a preview render
    faults natively rather than raising. The guard spans the document's
    whole life, not just the open, because a render on another thread is
    just as fatal while this one is measuring pages.
    """
    # The path a `SourceRef` records is written into `.deckle` files and
    # resolved by whoever opens one next, from whatever directory they
    # happen to be in. Stored as typed, `deckle impose ./book.pdf` recorded
    # `./book.pdf`, and the project then failed to open from anywhere but
    # the directory it was made in -- reported as "a source file is
    # missing", naming a file that had not moved.
    #
    # `abspath` rather than `realpath`: joining against the cwd is the fix,
    # and resolving symlinks as well would record a name the user did not
    # choose -- scans under a symlinked ~/Books would be filed as
    # /mnt/volume-3/..., which is the same file only until the mount point
    # changes. `project_io._path_within_roots` still uses `realpath` on
    # both sides, where "is this the same file" is the actual question.
    #
    # Absolutised here and nowhere else. `os.path.abspath("")` is the
    # current working directory, and `BLANK_SOURCE_PATH` is `""` -- so the
    # same call applied to an inserted blank would turn every blank into a
    # reference to whatever folder Deckle was launched from, and
    # `is_blank_page` tests for equality with `""`, so `load_project` would
    # start demanding those folders exist as files.
    stored_path = os.path.abspath(path)
    with pdfium_guard():
        try:
            doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            raise _classify_pdf_open_failure(path, exc) from exc

        # The document is closed before returning. Import is metadata-only --
        # nothing downstream holds a pdfium handle, and every later read reopens
        # the file. Leaving it open kept the source LOCKED on Windows for the
        # life of the app: import a PDF and you could not move, rename or delete
        # it until Deckle exited, with no indication of what held it.
        try:
            if len(doc) == 0:
                # Reachable independently of the open failure above: some
                # zero-page files open cleanly and only reveal themselves on
                # len().
                raise EmptyPdfError(
                    path,
                    f"cannot use {path}: the PDF has no pages, so there is "
                    "nothing to impose. Check that the export that produced it "
                    "actually wrote its pages.",
                )

            sha256 = _sha256_file(path)
            pages: list[SourcePage] = []
            for index in range(len(doc)):
                page = doc[index]
                try:
                    width_pt, height_pt = page.get_size()
                finally:
                    # Children before the parent, or their finalizers assert
                    # against a closed document -- see render.rasterize_page.
                    page.close()
                ref = SourceRef(
                    path=stored_path,
                    page_index=index,
                    sha256=sha256,
                    width_pt=float(width_pt),
                    height_pt=float(height_pt),
                )
                pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))
            return pages
        finally:
            doc.close()


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


def load_image_dir(path: str) -> list[SourcePage]:
    """Import a directory of images as one normalized PDF, metadata-only.

    Writes a single normalized PDF (one page per image, in natural sort
    order) into :func:`import_store_dir`, named by its own content hash,
    and returns ``SourcePage``\\ s whose ``SourceRef``\\ s point at it.

    That file is the only PDF those pages will ever have, so it is kept
    rather than cached -- see :func:`import_store_dir` for what went wrong
    while it was a cache, and :doc:`the decisions log </decisions>` for
    what that costs.

    :param path: the directory of images to import.
    :returns: an :class:`ImportedPages` -- a ``list[SourcePage]`` that also
        carries ``.warnings``. Do **not** wrap it in ``list()``; that
        discards the mixed-DPI and skipped-file advisories.
    :raises MissingSourceError: ``path`` does not exist.
    :raises UnreadableSourceError: ``path`` is a file, or cannot be listed.
    :raises NoImagesFoundError: the folder contains no importable images.
    :raises CorruptImageError: a file with an image extension cannot be
        decoded or converted. Fatal to the whole import by design -- a page
        quietly missing from a book is discovered after it is folded.
    """
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

    store_dir = import_store_dir()
    out_path = os.path.join(store_dir, f"{_import_key(per_image_pdfs)}.pdf")
    if not os.path.exists(out_path):
        os.makedirs(store_dir, exist_ok=True)
        _merge_image_pdfs(per_image_pdfs, out_path)
    # Otherwise this exact set of images has been imported before and the
    # merge is skipped entirely. The existing file is left alone rather
    # than rewritten with the same bytes -- another project is already
    # referencing it, and its content hash is what that project checks.

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
