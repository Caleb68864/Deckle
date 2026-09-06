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

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, Sequence

import pypdfium2 as pdfium

from deckle.core import export
from deckle.core.models import is_blank_page, SheetPlan, SourcePage, SourceRef

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


_PDFIUM_LOCK = threading.RLock()
"""Serialises every pdfium call in the process.

**pdfium rendering is not thread-safe, and it does not fail politely.**
Two threads rasterizing at once produce ``OSError: exception: access
violation reading 0x0`` -- a native fault, not a Python exception -- which
takes the whole application down with no traceback and nothing in the log.
Measured at roughly one in thirty concurrent renders here, which is
exactly the frequency that reads to a user as "Deckle randomly closes".

Deckle reaches that state through ordinary use, not through an unusual
one. Both views that render do it on a background ``QThread``, and both
supersede a running job by setting its ``cancel`` flag and starting the
next thread **without waiting for the old one to stop**. The flag is
cooperative and is checked between steps, never inside a pdfium call, so
the outgoing render is still inside pdfium when the incoming one begins.
Scrubbing the preview does it; so does scrolling thumbnails while a
preview renders, since the two views hold independent threads.

An ``RLock`` rather than a ``Lock`` because these regions nest:
``render_sheet`` holds it across a document's lifetime and calls
``rasterize_page``, which takes it again on the same thread.

The cost is that renders no longer overlap. They contended for the same
cores anyway, and a superseded render drops out at its next checkpoint --
against a fault that ends the process, this is not a close trade.

**Every module that touches pdfium must hold this**, not only this one --
see :func:`pdfium_guard`. Guarding rasterization alone is not enough: an
*open* racing another thread's render faults just as readily.
"""


def pdfium_guard():
    """Hold while touching pdfium from anywhere in Deckle.

    pdfium is a single global library and its state is process-wide, so
    the rule cannot be per-module: every document open, page render and
    close has to be inside this, or the ones that are gain nothing from
    the ones that are not.

    Three callers outside this module need it, and the *printing* one is
    the reason it is public rather than private. The print dialog runs no
    thread of its own, so a print rasterizes **on the GUI thread** -- and
    printing while the preview is still drawing is an entirely ordinary
    thing to do, with a background render in flight the whole time.

    Usable as a context manager::

        with pdfium_guard():
            doc = pdfium.PdfDocument(path)
            ...
            doc.close()

    Reentrant, so a guarded region may call another one on the same
    thread. Hold it across the document's whole life rather than around
    the render alone: opening while another thread renders faults too --
    measured, not assumed.

    :returns: the process-wide pdfium lock.
    """
    return _PDFIUM_LOCK


def rasterize_page(doc, page_index: int, *, scale: float, rotation: int = 0):
    """Render one page to a PIL image, closing pdfium's children eagerly.

    :param doc: an open ``pdfium.PdfDocument``.
    :param page_index: which page.
    :param scale: render scale, i.e. ``dpi / 72``.
    :param rotation: degrees clockwise -- 0, 90, 180 or 270, as pypdfium2 wants them.
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
    with _PDFIUM_LOCK:
        page = doc[page_index]
        try:
            bitmap = page.render(scale=scale, rotation=rotation)
            try:
                # to_pil() copies the pixels out, so the result does not
                # alias the bitmap's buffer and stays valid after it closes.
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
    must not still pay for it.

    The exported sheet comes from :func:`deckle.core.export.export_sheet_cached`
    and belongs to that cache, so it is deliberately not deleted here --
    including on cancellation. It is not stranded: the cache is a bounded
    LRU that evicts and deletes, and :func:`~deckle.core.export.clear_sheet_cache`
    empties it. A cancelled render therefore leaves the sheet ready for the
    next request rather than throwing the work away.

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

    # Route through the cache rather than exporting to a fresh temp file
    # every time. The cache was built, bounded, tested -- and never called,
    # so scrubbing back and forth across a book re-exported every sheet on
    # every visit, and returning to a sheet cost exactly as much as seeing
    # it the first time.
    #
    # The cache owns the file it hands back, so nothing here deletes it;
    # `clear_sheet_cache` and the LRU eviction are what remove entries.
    # Its key includes the plan hash, so any layout change invalidates
    # rather than returning a stale sheet.
    tmp_path = export.export_sheet_cached(plan, sheet_index)
    try:
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

        with _PDFIUM_LOCK:
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
        # Deliberately no cleanup: the path belongs to the sheet cache, and
        # deleting it here would evict an entry the cache still believes it
        # holds -- the next hit would hand back a path that no longer
        # exists.
        pass


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
    # Held across the whole window rather than per page: `open_docs` keeps
    # documents alive between iterations, so the guard has to span every
    # open, every render and every close -- otherwise a document opened
    # under it is rendered outside it on the next page.
    with _PDFIUM_LOCK:
        try:
            for source_page in window:
                if cancel is not None and cancel.is_set():
                    break
                ref = source_page.ref
                if is_blank_page(source_page):
                    # A blank references no file. Opening its empty path
                    # raised FileNotFoundError, which failed the WHOLE
                    # window -- one inserted blank left every thumbnail
                    # beside it missing too.
                    result.append(_blank_thumbnail(ref, dpi))
                    continue
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
                            rotation=_pdfium_rotation(source_page.rotate_deg),
                        )
                    )
                )
        finally:
            for doc in open_docs.values():
                doc.close()
    return result


def _blank_thumbnail(ref: SourceRef, dpi: int) -> RenderedPage:
    """A white thumbnail matching a blank page's shape.

    :param ref: the blank's reference, for its dimensions.
    :param dpi: the render resolution the rest of the window used.
    :returns: an opaque white page.

    White rather than a zero-size page: a degenerate ``RenderedPage`` means
    "nothing to show", which the grid draws as no thumbnail at all -- the
    same picture as a render that has not finished. A blank the user
    deliberately inserted should look like a blank sheet.
    """
    scale = dpi / 72.0
    width = max(1, int(round(ref.width_pt * scale)))
    height = max(1, int(round(ref.height_pt * scale)))
    return RenderedPage(
        width=width,
        height=height,
        rgba=bytes([255, 255, 255, 255]) * (width * height),
    )


def _pdfium_rotation(rotate_deg: int) -> int:
    """``rotate_deg`` as pypdfium2's ``rotation`` argument wants it.

    **Degrees, not quarter turns.** ``pypdfium2.internal.RotationToConst``
    is ``{0: 0, 90: 1, 180: 2, 270: 3}`` and the helper it feeds does the
    division itself, so pre-dividing here handed it ``1`` and every rotated
    page raised ``KeyError: 1`` out of the thumbnail worker -- i.e. the one
    control Arrange offers for a sideways scan broke the grid rather than
    turning the page.

    Snapped to the nearest quarter turn for the same reason
    ``layout._page_rotation`` is: a stored ``45`` is not a rotation pdfium
    can perform, and a KeyError is not the way to say so.
    """
    return (round(rotate_deg / 90.0) * 90) % 360


_ink_bbox_cache: OrderedDict[SourceRef, tuple[float, float, float, float]] = OrderedDict()
_ink_bbox_cache_lock = threading.Lock()


def _rasterize_for_bbox(ref: SourceRef, dpi: int):
    """Rasterize ``ref`` at low ``dpi`` for ink-bounds scanning.

    Split out from ``ink_bbox`` so tests can patch this single choke point
    and count how many times an actual rasterization happens.
    """
    with _PDFIUM_LOCK:
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


Insets = tuple[float, float, float, float]

_CROP_RULE_RGB = (220, 40, 40)
"""Red, and the only colour in an otherwise greyscale picture -- so the
proposed crop cannot be mistaken for something the pages contain."""


def composite_pages(
    pages: Sequence[SourcePage],
    *,
    dpi: int = 72,
    parity: str | None = None,
    crop_pt: Insets | None = None,
) -> RenderedPage:
    """Every page's ink in one picture, with the proposed crop drawn on it.

    briss's distinguishing feature is superimposing all pages so the real
    content extent is visible before a crop is committed.
    :func:`auto_crop_insets` now measures that extent, so what a picture is
    still for is **verification**: would this crop cut anything off, on any
    page?

    **Darkest pixel wins.** A page contributes its ink and nothing else, so
    the composite is the union of every page's content -- which is the
    question being asked. Averaging would fade a mark appearing on one page
    in two hundred into invisibility, and that mark is precisely the one
    that gets clipped without anyone noticing.

    :param pages: the document's pages. Skipped pages are excluded, since
        a page not being imposed must not widen the extent the crop is
        judged against.
    :param dpi: rasterisation resolution. Higher than the ink-bbox default
        because a person looks at this one.
    :param parity: ``"odd"``, ``"even"``, or ``None`` for all. A scan's
        margins alternate, so the two parities are different pictures and
        compositing them together would answer neither.
    :param crop_pt: insets to draw as a rectangle, or ``None`` to draw
        none.
    :returns: the composite as a :class:`RenderedPage`.
    :raises ValueError: no page was left to composite -- an empty picture
        would look like a document with no content, which is a different
        and much more alarming answer than "you filtered everything out".
    """
    from PIL import Image, ImageChops, ImageDraw

    selected = []
    for page in pages:
        if page.skipped or is_blank_page(page):
            continue
        if parity is not None:
            is_odd = (page.ref.page_index + 1) % 2 == 1
            if (parity == "odd") != is_odd:
                continue
        selected.append(page)
    if not selected:
        raise ValueError(
            "nothing to composite: every page was skipped, blank, or "
            "filtered out by parity"
        )

    scale = dpi / 72.0
    width = max(int(round(page.ref.width_pt * scale)) for page in selected)
    height = max(int(round(page.ref.height_pt * scale)) for page in selected)

    canvas = Image.new("L", (width, height), 255)
    for page in selected:
        rendered = _rasterize_for_bbox(page.ref, dpi).convert("L")
        if rendered.size != (width, height):
            # Pages of differing sizes are aligned at the top-left, which
            # is the PDF's own origin corner once the image is flipped.
            # Arbitrary for mixed sizes, and correct for the scanned book
            # this exists to serve, where every page is the same size.
            sized = Image.new("L", (width, height), 255)
            sized.paste(rendered, (0, 0))
            rendered = sized
        canvas = ImageChops.darker(canvas, rendered)

    picture = canvas.convert("RGB")
    if crop_pt is not None:
        left, bottom, right, top = crop_pt
        draw = ImageDraw.Draw(picture)
        draw.rectangle(
            [
                left * scale,
                top * scale,
                width - right * scale - 1,
                height - bottom * scale - 1,
            ],
            outline=_CROP_RULE_RGB,
            width=max(1, int(round(scale))),
        )

    rgba = picture.convert("RGBA")
    return RenderedPage(width=rgba.width, height=rgba.height, rgba=rgba.tobytes())


def auto_crop_insets(
    pages: Sequence[SourcePage],
    *,
    margin_pt: float = 0.0,
    dpi: int = 36,
    split_parity: bool = True,
) -> tuple[Insets | None, Insets | None]:
    """Derive crop insets from where the ink actually is.

    Cropping by hand means measuring a scan in a viewer, typing four
    numbers and reprinting when they were wrong. Everything needed to
    measure it is already here: :func:`ink_bbox` rasterises a page and
    returns its content bounds, cached per ``SourceRef``.

    **Document-wide, not per page.** The result is the *least aggressive*
    inset any measured page needs, so no page loses content and every page
    keeps the same frame. Cropping each page to its own ink would let the
    text block move from leaf to leaf -- a page whose last line is short
    would crop tighter than its neighbour -- which is worse than not
    cropping at all.

    Computed as a per-page inset from each edge and then minimised, rather
    than as a union of boxes, so a document whose pages differ in size
    still gets an answer that is correct for all of them. A union of
    absolute rectangles is meaningless across mixed page sizes.

    :param pages: the document's pages. Skipped pages are ignored -- a
        page excluded from the imposition must not constrain the crop of
        the pages that are in it -- as are inserted blanks, which have no
        file to measure.
    :param margin_pt: kept back from every edge. Ink bounds come off a
        low-dpi raster and a descender or hairline rule can fall just
        outside the box, so this is how that is bought back. Insets clamp
        at zero rather than going negative.
    :param dpi: scan resolution, passed to :func:`ink_bbox`.
    :param split_parity: measure odd and even pages separately, which is
        the case ``crop_even_pt`` exists for -- a scanned book's gutter
        alternates sides, so one answer cannot fit both. ``False``
        measures the document as a whole and returns it as the odd value
        with ``None`` for even.
    :returns: ``(odd, even)`` insets, either of which is ``None`` when
        nothing was measurable -- a document with no ink is not cropped,
        because "content touches every edge" and "there is no content" are
        different answers and only one of them means do nothing.
    """
    groups: dict[bool, list[Insets]] = {True: [], False: []}
    for page in pages:
        if page.skipped or is_blank_page(page):
            continue
        ref = page.ref
        x0, y0, x1, y1 = ink_bbox(ref, dpi)
        if x1 <= x0 or y1 <= y0:
            # A blank page's degenerate box. Folded in unexamined it reads
            # as "content touches every edge" and silently disables the
            # crop for the whole document.
            continue
        insets = (x0, y0, ref.width_pt - x1, ref.height_pt - y1)
        is_odd = (ref.page_index + 1) % 2 == 1
        groups[is_odd if split_parity else True].append(insets)

    def _least(measured: list[Insets]) -> Insets | None:
        if not measured:
            return None
        return tuple(
            max(0.0, min(page_insets[edge] for page_insets in measured) - margin_pt)
            for edge in range(4)
        )

    return _least(groups[True]), _least(groups[False])


def clear_ink_bbox_cache() -> None:
    """Drop every cached ink bbox. Mainly useful for test isolation.

    :returns: nothing.
    """
    with _ink_bbox_cache_lock:
        _ink_bbox_cache.clear()
