"""Rasterizer: preview bitmaps, virtualized thumbnails, and ink bounds.

**The preview renders the actual exported PDF. It never re-draws the layout
independently.** ``render_sheet`` exports the requested sheet via
``deckle.core.export.export`` and rasterizes *that artifact* with
``pypdfium2`` -- it does not composite the sheet's page transforms in
parallel with the exporter. A rasterized artifact cannot lie, because it
*is* the thing that goes to the printer; a preview that re-draws the
layout independently could be wrong in exactly the way the exporter is
wrong and agree with it.

This module returns raw buffers only -- no GUI-toolkit image types cross
this boundary. Callers convert ``RenderedPage.rgba`` into whatever the UI
toolkit wants.

Thumbnails are scroll-driven and virtualized: ``thumbnails`` takes an
explicit page range, never "all pages", so scrubbing a thousand-page
document doesn't rasterize the whole thing up front.

Ink bounding boxes are cached per ``SourceRef`` (frozen dataclasses hash
by value, so two ``SourceRef``\\ s describing the same page hit the same
cache entry) and computed on demand -- never eagerly across a document.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, Sequence

import pypdfium2 as pdfium

from deckle.core import export
from deckle.core.models import SheetPlan, SourcePage, SourceRef

# Background pixels at or above this value (0-255 per channel) are treated
# as "paper", not ink, when scanning for a page's content bounds.
_BACKGROUND_THRESHOLD = 250

# Upper bound on remembered ink boxes. Generous -- an entry is five floats
# and a SourceRef -- but finite: the cache is module-level and survives
# closing a project, so a session that opens several long documents would
# otherwise keep one entry per page of every document it has ever seen.
_INK_BBOX_CACHE_MAX = 4096


@dataclass(frozen=True)
class RenderedPage:
    """A rasterized page as a raw RGBA buffer -- no Qt/PIL types.

    :ivar width: pixel width.
    :ivar height: pixel height.
    :ivar rgba: tightly packed RGBA8888 bytes, ``width * height * 4`` long.
        Handed straight to ``QImage`` by the callers that need a Qt type;
        this module never constructs one.

    A degenerate page -- ``0`` by ``0`` with empty bytes -- is how "there
    is nothing to show" is expressed: an absent side, or a render that was
    cancelled. Callers check ``width``/``height`` rather than catching.
    """

    width: int
    height: int
    rgba: bytes


def rasterize_page(doc, page_index: int, *, scale: float, rotation: int = 0):
    """Render one page to a PIL image, closing pdfium's children eagerly.

    :param doc: an open ``pdfium.PdfDocument``.
    :param page_index: which page.
    :param scale: render scale, i.e. ``dpi / 72``.
    :param rotation: quarter turns clockwise.
    :returns: a PIL image, owned by the caller and outliving the page.

    pdfium's Python bindings attach a finalizer to every child object that
    asserts its parent is still open. Rendering with the obvious shape --

        page = doc[i]
        bitmap = page.render(...)
        ...
        doc.close()

    -- leaves ``page`` and ``bitmap`` referenced by locals when the document
    closes. Whenever the garbage collector gets to them afterwards, their
    finalizers fire against a closed parent and pdfium raises
    ``AssertionError`` from ``_close_template``. Python swallows it
    ("Exception ignored in: <finalize object...>"), so nothing crashes and
    the console fills with tracebacks that point at a weakref rather than at
    us.

    It is timing-dependent, which is worse than deterministic: a short
    script drops its references in a friendly order and looks fine, while a
    GUI holding objects across a worker thread reproduces it readily. This
    closes the children in the order pdfium expects, so the caller is free
    to close the document whenever it likes.
    """
    page = doc[page_index]
    try:
        bitmap = page.render(scale=scale, rotation=rotation)
        try:
            # to_pil() copies the pixels out, so the result does not alias
            # the bitmap's buffer and stays valid after it closes.
            return bitmap.to_pil()
        finally:
            bitmap.close()
    finally:
        page.close()


def _pil_to_rendered_page(pil_image) -> RenderedPage:
    rgba_image = pil_image.convert("RGBA")
    width, height = rgba_image.size
    return RenderedPage(width=width, height=height, rgba=rgba_image.tobytes())


def _empty_rendered_page() -> RenderedPage:
    return RenderedPage(width=0, height=0, rgba=b"")


def render_sheet(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int,
    cancel: threading.Event | None = None,
) -> RenderedPage:
    """Render one side of one sheet by exporting it and rasterizing the PDF.

    Always routes through ``deckle.core.export.export`` for the requested
    ``sheet_index`` -- the exported artifact is the source of truth, never
    the ``Sheet``/output-page transforms on ``plan``. If ``cancel`` is
    already set, returns a degenerate page promptly without exporting or
    rasterizing.

    ``cancel`` is honoured at every point where the next step is expensive:
    before exporting, after exporting, and immediately before rasterizing
    -- rasterization is the single most expensive step here and scales with
    ``dpi`` squared, so a cancel that arrives while the export is running
    must not still pay for it. Whichever checkpoint fires, the scratch PDF
    is removed before returning; cancellation never leaks a temp file.

    :param plan: the plan to render from. Only ``sheet_index`` is
        exported, but the whole plan is passed because that is what
        ``export`` takes.
    :param sheet_index: which sheet, by ``Sheet.index``.
    :param side: which physical face.
    :param dpi: rasterization resolution. Cost scales with its square.
    :param cancel: optional event; when set, the call returns a degenerate
        page at the next checkpoint.
    :returns: the rasterized side, or a degenerate ``0x0`` page when the
        sheet or side does not exist, or the render was cancelled.
    :raises OSError: the scratch PDF cannot be written.
    :raises pypdfium2.PdfiumError: the exported PDF cannot be rasterized.
    """
    if cancel is not None and cancel.is_set():
        return _empty_rendered_page()

    by_index = {sheet.index: sheet for sheet in plan.sheets}
    sheet = by_index.get(sheet_index)
    has_front = sheet is not None and sheet.front is not None
    has_back = sheet is not None and sheet.back is not None

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        export.export(plan, tmp_path, sheets=[sheet_index])

        if cancel is not None and cancel.is_set():
            return _empty_rendered_page()

        # The single-sheet export contains only the sides that exist, in
        # front-then-back order -- see export._export_batched/_sides.
        if side == "front":
            if not has_front:
                return _empty_rendered_page()
            page_index = 0
        else:
            if not has_back:
                return _empty_rendered_page()
            page_index = 1 if has_front else 0

        pdf = pdfium.PdfDocument(tmp_path)
        try:
            if page_index >= len(pdf):
                return _empty_rendered_page()
            if cancel is not None and cancel.is_set():
                return _empty_rendered_page()
            return _pil_to_rendered_page(
                rasterize_page(pdf, page_index, scale=dpi / 72)
            )
        finally:
            pdf.close()
    finally:
        # Never let cleanup failure mask the render's own outcome -- on
        # Windows the scratch file can still be held for a moment after
        # pdfium closes it. export._safe_remove logs and moves on.
        if os.path.exists(tmp_path):
            export._safe_remove(tmp_path)


def thumbnails(
    pages: Sequence[SourcePage],
    start: int,
    count: int,
    dpi: int = 36,
    cancel: threading.Event | None = None,
) -> list[RenderedPage]:
    """Rasterize an explicit range ``pages[start:start + count]``.

    Deliberately not "render every page" -- callers virtualize a scroll
    view and only ask for the pages currently visible.

    :param pages: the full page list. Only the requested window is opened.
    :param start: index of the first page in the window.
    :param count: how many pages to rasterize. A window that falls entirely
        past the end returns an empty list.
    :param dpi: rasterization resolution, low by default -- these are
        thumbnails.
    :param cancel: optional event checked before each page. A fast scrub
        through a long document queues window after window, and without a
        way to abandon one the rasterizer keeps working on windows the user
        has already scrolled past. Returns the pages completed so far
        rather than raising -- a superseded window's partial result is
        discarded by the caller anyway. Omitting ``cancel`` renders the
        whole window exactly as before.
    :returns: one rendered page per page in the window, in order, or the
        pages completed before cancellation.
    :raises pypdfium2.PdfiumError: a referenced source cannot be opened or
        rasterized.
    """
    window = list(pages)[start : start + count]
    if not window:
        return []

    result: list[RenderedPage] = []
    open_docs: dict[str, pdfium.PdfDocument] = {}
    try:
        for source_page in window:
            if cancel is not None and cancel.is_set():
                break
            ref = source_page.ref
            doc = open_docs.get(ref.path)
            if doc is None:
                doc = pdfium.PdfDocument(ref.path)
                open_docs[ref.path] = doc
            result.append(
                _pil_to_rendered_page(
                    rasterize_page(
                        doc,
                        ref.page_index,
                        scale=dpi / 72,
                        rotation=_rotation_quarter_turns(source_page.rotate_deg),
                    )
                )
            )
    finally:
        for doc in open_docs.values():
            doc.close()
    return result


def _rotation_quarter_turns(rotate_deg: int) -> int:
    """pdfium's ``rotation`` is quarter turns clockwise, not degrees."""
    return (rotate_deg // 90) % 4


_ink_bbox_cache: OrderedDict[SourceRef, tuple[float, float, float, float]] = OrderedDict()
_ink_bbox_cache_lock = threading.Lock()


def _rasterize_for_bbox(ref: SourceRef, dpi: int):
    """Rasterize ``ref`` at low ``dpi`` for ink-bounds scanning.

    Split out from ``ink_bbox`` so tests can patch this single choke point
    and count how many times an actual rasterization happens.
    """
    doc = pdfium.PdfDocument(ref.path)
    try:
        return rasterize_page(doc, ref.page_index, scale=dpi / 72).convert("RGB")
    finally:
        doc.close()


def ink_bbox(ref: SourceRef, dpi: int = 36) -> tuple[float, float, float, float]:
    """The bounding box of non-background content on ``ref``, in PDF points.

    Cached per ``SourceRef`` -- calling this twice for the same ref
    rasterizes only once. A page with no non-background pixels (blank)
    returns a degenerate ``(0.0, 0.0, 0.0, 0.0)`` box rather than raising.

    The cache is LRU-bounded at :data:`_INK_BBOX_CACHE_MAX` entries; past
    that, the least recently used ref is dropped and would be recomputed on
    demand. Eviction costs one rasterization, never a wrong answer.

    :param ref: the page to scan. ``SourceRef`` is a frozen dataclass and
        hashes by value, so two refs describing the same page share one
        cache entry.
    :param dpi: scan resolution. Low on purpose -- ink bounds do not need
        detail, and this runs on demand while the user waits.
    :returns: ``(x0, y0, x1, y1)`` in PDF points, bottom-left origin.
    :raises pypdfium2.PdfiumError: the source cannot be opened or
        rasterized.
    """
    with _ink_bbox_cache_lock:
        cached = _ink_bbox_cache.get(ref)
        if cached is not None:
            _ink_bbox_cache.move_to_end(ref)
            return cached

    pil_image = _rasterize_for_bbox(ref, dpi)
    # Threshold to a content mask first: PIL's getbbox() treats any
    # non-zero pixel as content, which would make a white background
    # (255, 255, 255) itself "content". Map near-white -> 0 (background)
    # and everything darker -> 255 (ink) before asking for the bbox.
    grayscale = pil_image.convert("L")
    mask = grayscale.point(lambda p: 0 if p >= _BACKGROUND_THRESHOLD else 255)
    bbox_px = mask.getbbox()

    if bbox_px is None:
        result = (0.0, 0.0, 0.0, 0.0)
    else:
        result = _scale_bbox_to_points(bbox_px, pil_image.size, ref, dpi)

    with _ink_bbox_cache_lock:
        _ink_bbox_cache[ref] = result
        _ink_bbox_cache.move_to_end(ref)
        while len(_ink_bbox_cache) > _INK_BBOX_CACHE_MAX:
            _ink_bbox_cache.popitem(last=False)
    return result


def _scale_bbox_to_points(
    bbox_px: tuple[int, int, int, int],
    image_size: tuple[int, int],
    ref: SourceRef,
    dpi: int,
) -> tuple[float, float, float, float]:
    left_px, top_px, right_px, bottom_px = bbox_px
    img_w, img_h = image_size
    scale_x = ref.width_pt / img_w if img_w else 0.0
    scale_y = ref.height_pt / img_h if img_h else 0.0

    # Image origin is top-left; PDF origin is bottom-left.
    x0 = left_px * scale_x
    x1 = right_px * scale_x
    y0 = (img_h - bottom_px) * scale_y
    y1 = (img_h - top_px) * scale_y
    return (x0, y0, x1, y1)


def clear_ink_bbox_cache() -> None:
    """Drop every cached ink bbox. Mainly useful for test isolation.

    :returns: nothing.
    """
    with _ink_bbox_cache_lock:
        _ink_bbox_cache.clear()
