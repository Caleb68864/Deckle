"""Durable per-image imports for trading card fronts and backs."""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import natsort
import img2pdf
from PIL import Image

from deckle.core.loader import (
    CorruptImageError, IMAGE_EXTENSIONS, _image_dpi_or_raise,
    _scan_image_dir, _single_image_pdf_or_raise, import_store_dir, load_pdf,
)
from deckle.core.models import CardAsset, LayoutWarning
from deckle.core.paths import atomic_output


@dataclass(frozen=True)
class CardImportResult:
    assets: tuple[CardAsset, ...]
    warnings: tuple[LayoutWarning, ...] = ()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _natural_paths(paths: Sequence[str]) -> list[str]:
    unique = {}
    for path in paths:
        absolute = os.path.realpath(os.path.abspath(os.fspath(path)))
        unique.setdefault(os.path.normcase(absolute), absolute)
    return sorted(unique.values(), key=lambda p: (natsort.natsort_key(Path(p).name), Path(p).name, p))


def import_card_images(paths: Sequence[str]) -> CardImportResult:
    if not paths:
        raise ValueError("Select at least one image for the card deck.")
    resolved = _natural_paths(paths)
    prepared = []
    for path in resolved:
        if not os.path.isfile(path):
            raise ValueError(f"Cannot import image {path}: file does not exist or is not readable.")
        if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Cannot import {path}: this file type is not a supported image.")
        before = _sha256(path)
        try:
            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) > 1:
                    raise ValueError(f"Cannot import {path}: animated or multi-frame images are not supported.")
                image.verify()
            _image_dpi_or_raise(path)  # malformed resolution metadata uses the loader's established error
            pdf_bytes = _card_pdf_bytes(path)
        except ValueError:
            raise
        except Exception as exc:
            raise CorruptImageError(path, f"cannot import image {path}: {exc}") from exc
        after = _sha256(path)
        if before != after:
            raise ValueError(f"Image changed while it was being imported: {path}")
        prepared.append((path, before, pdf_bytes))

    store = import_store_dir()
    os.makedirs(store, exist_ok=True)
    assets = []
    for path, original_hash, pdf_bytes in prepared:
        key = hashlib.sha256((original_hash + ":card-import-v1").encode("ascii")).hexdigest()
        target = os.path.join(store, f"card-{key}.pdf")
        if not os.path.exists(target) or not _is_valid_one_page_pdf(target):
            with atomic_output(target) as temporary:
                with open(temporary, "wb") as stream:
                    stream.write(pdf_bytes)
            if not _is_valid_one_page_pdf(target):
                try: os.remove(target)
                except OSError: pass
                raise ValueError(f"Converted image PDF failed validation: {path}")
        pages = load_pdf(target)
        if len(pages) != 1:
            raise ValueError(f"Converted image did not produce exactly one page: {path}")
        assets.append(CardAsset(
            id=uuid.uuid4().hex,
            label=Path(path).name,
            original_path=path,
            original_sha256=original_hash,
            page=pages[0],
        ))
    return CardImportResult(tuple(assets))


def _card_pdf_bytes(path: str) -> bytes:
    """Convert at 72 pixels per inch so image DPI never changes artwork aspect/size."""
    with Image.open(path) as image:
        width, height = image.size
        orientation = image.getexif().get(274, 1)
    if orientation in (5, 6, 7, 8): width, height = height, width
    with open(path, "rb") as stream: raw = stream.read()
    layout = img2pdf.get_layout_fun(pagesize=(float(width), float(height)), fit=img2pdf.FitMode.into)
    try:
        return img2pdf.convert(raw, rotation=img2pdf.Rotation.ifvalid, layout_fun=layout)
    except Exception as exc:
        raise CorruptImageError(path, f"cannot convert the image {path} to a PDF page: {exc}") from exc


def _is_valid_one_page_pdf(path: str) -> bool:
    try:
        return len(load_pdf(path)) == 1
    except Exception:
        return False


def import_card_folder(path: str) -> CardImportResult:
    image_paths, skipped = _scan_image_dir(path)
    if not image_paths:
        if skipped:
            detail = f"the folder contains no supported images; found {', '.join(skipped[:5])}"
        else:
            detail = "the folder is empty"
        raise ValueError(f"No card images found in {path}: {detail}.")
    result = import_card_images(image_paths)
    warnings = ()
    if skipped:
        warnings = (LayoutWarning(
            sheet_index=-1,
            kind="skipped_non_image_files",
            detail=f"Skipped {len(skipped)} non-image file(s): {', '.join(skipped[:5])}",
        ),)
    return CardImportResult(result.assets, warnings)
