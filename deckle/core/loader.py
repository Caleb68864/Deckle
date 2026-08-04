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

from deckle.core.models import LayoutWarning, SourcePage, SourceRef

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Fallback page used when an image carries no DPI metadata at all. img2pdf's
# own fallback (96.0 dpi) is a print-size hazard -- silently guessing a
# resolution instead of a physical size. We fit the image into a standard
# page instead of trusting an invented DPI.
_FALLBACK_PAGE_PT = (612.0, 792.0)  # US Letter, matching Project defaults.

_CACHE_DIR_NAME = "deckle_import_cache"


class EncryptedPdfError(Exception):
    """Raised by ``load_pdf`` when the PDF is password-protected."""

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"PDF is password-protected: {path}")


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
    except Exception:
        # Not a password issue -- some other corruption; let the original
        # pypdfium2 error surface instead of misreporting it as encrypted.
        return False


def load_pdf(path: str) -> list[SourcePage]:
    """Load an existing PDF's pages, metadata-only (no rasterization).

    Raises ``EncryptedPdfError`` (carrying ``path``) if the PDF requires a
    password, instead of letting a bare pypdfium2/pikepdf exception escape.
    """
    try:
        doc = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if _is_encrypted(path):
            raise EncryptedPdfError(path) from exc
        raise

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


def _list_images(dir_path: str) -> list[str]:
    entries = [
        entry.path
        for entry in os.scandir(dir_path)
        if entry.is_file() and Path(entry.path).suffix.lower() in IMAGE_EXTENSIONS
    ]
    # Natural sort so img1, img2, img10 stay in numeric order rather than
    # the lexicographic img1, img10, img2.
    return natsort.natsorted(entries)


def _image_dpi(image_path: str) -> tuple[float, float] | None:
    with Image.open(image_path) as img:
        dpi = img.info.get("dpi")
    if not dpi:
        return None
    x, y = dpi
    if not x or not y:
        return None
    return (float(x), float(y))


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
    order) into a cache directory under the OS temp dir, and returns
    ``SourcePage``s whose ``SourceRef``s point at that cached PDF.
    """
    image_paths = _list_images(path)

    dpis = [_image_dpi(p) for p in image_paths]
    warnings: list[LayoutWarning] = []
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
        _single_image_pdf_bytes(img_path, dpi)
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
