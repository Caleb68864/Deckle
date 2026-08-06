"""Exporter: render a ``SheetPlan`` to a PDF on disk via pikepdf Form XObjects.

Composition follows the pikepdf "Gutter Shift Recipe" Method B: build the
output sheet with ``add_blank_page``, obtain the source content as a Form
XObject via ``copy_foreign(Page(src).as_form_xobject())``, register it as a
resource, and place it with ``calc_form_xobject_placement`` using
``invert_transformations=True``. The destination rect is always sized to
exactly reproduce a placement's own ``scale_x``/``scale_y`` -- ``shrink``/
``expand`` permission is granted only in the single direction (if any)
actually needed to hit that exact scale, so pikepdf reproduces
``Placement`` verbatim rather than substituting its own best-fit: when a
placement's scale is already 1.0 (the common ``fit`` case), that
means neither is granted and the result is a pure translation with no
scale factor at all.

The whole-page overlay/watermark helper on ``pikepdf.Page`` is never used
here -- it centers and best-fits content, which is wrong for imposition
where ``Placement`` dictates exact geometry.

This module does not modify, recompute, or clamp any ``Placement`` produced
by ``Imposer``: it only translates ``Placement`` into PDF geometry.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import threading
from collections import OrderedDict, deque
from typing import Sequence

import pikepdf
from pikepdf import Name, Page, Rectangle
from pikepdf.canvas import ContentStreamBuilder

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import Mark, OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.printing import duplex_flip_edge

_CACHE_DIR_NAME = "deckle_export_cache"

# Default bound on the number of cached single-sheet exports kept on disk.
_DEFAULT_CACHE_SIZE = 200

# How many race-displaced exports to keep before reclaiming the oldest.
# Only a concurrent render of the same sheet produces one, so this is
# generously above anything a real session reaches.
_MAX_RETIRED = 32

# How many sheets to assemble before saving and reopening the output PDF,
# so source handles opened during composition don't accumulate across a
# very large document (see "pikepdf - Performance and Memory").
_BATCH_SHEETS = 50


def _mark_key(mark: Mark) -> str:
    return f"{mark.kind}:{mark.x0}:{mark.y0}:{mark.x1}:{mark.y1}"


def _update_delimited(digest: "hashlib._Hash", tag: str, value: str) -> None:
    """Feed ``value`` into ``digest`` length-prefixed, not just concatenated.

    Running variable-length keys together makes the boundary between them
    recoverable from their content rather than fixed by the framing: a
    ``SourceRef.path`` that happens to contain the field separators can
    reproduce, on its own, the exact bytes that two *different* pages would
    contribute, so two genuinely different plans hash identically and the
    second one is handed the first one's cached PDF.

    A cache that returns the wrong page is worse than no cache at all, so
    every variable-length component announces its own byte length and no
    content can straddle a boundary.
    """
    encoded = value.encode("utf-8")
    digest.update(f"|{tag}[{len(encoded)}]:".encode("utf-8"))
    digest.update(encoded)


def _plan_hash(plan: SheetPlan) -> str:
    """A stable hash of everything that affects rendered output.

    Deliberately built from the plan's own field values (not Python's
    ``id()`` or ``hash()``, which are unstable across processes) so the
    same layout settings always produce the same cache key.

    Every variable-length component is fed in length-prefixed -- see
    :func:`_update_delimited` for why plain concatenation is not safe here.
    """
    digest = hashlib.sha256()
    digest.update(repr(plan.paper_pt).encode("utf-8"))
    for sheet in plan.sheets:
        digest.update(f"|sheet:{sheet.index}".encode("utf-8"))
        for side_name, side in (("front", sheet.front), ("back", sheet.back)):
            digest.update(f"|{side_name}:".encode("utf-8"))
            if side is None:
                digest.update(b"none")
                continue
            for page in side.pages:
                _update_delimited(digest, "page", _output_page_key(page))
            for mark in side.marks:
                _update_delimited(digest, "mark", _mark_key(mark))
    return digest.hexdigest()


def _output_page_key(page: OutputPage) -> str:
    ref = page.source_ref
    ref_key = (
        "none"
        if ref is None
        else f"{ref.path}:{ref.page_index}:{ref.sha256}:{ref.width_pt}:{ref.height_pt}"
    )
    placement = page.placement
    placement_key = (
        f"{placement.scale_x}:{placement.scale_y}:{placement.tx}:"
        f"{placement.ty}:{placement.rotate_deg}"
    )
    return f"{ref_key}|{placement_key}|{page.is_filler}"


def _scale_flags_for(scale: float) -> tuple[bool, bool]:
    """The minimal ``(allow_shrink, allow_expand)`` that reproduces ``scale``.

    ``Placement`` already carries the exact target scale computed by
    ``Imposer`` -- this module must never let pikepdf recompute or drift
    from it. Passing a rect that already matches the intended scale and
    granting *only* the direction actually needed (shrink for scale < 1,
    expand for scale > 1, neither for scale == 1) means pikepdf reproduces
    that exact scale rather than substituting its own best-fit: when
    neither direction is permitted and none is needed, the result is a
    pure translation with no scale factor at all.
    """
    if math.isclose(scale, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        return False, False
    if scale < 1.0:
        return True, False
    return False, True


def _rect_for_placement(placement: Placement, src_w: float, src_h: float) -> Rectangle:
    """The destination rectangle a source page's content is placed into.

    ``invert_transformations=True`` means pikepdf's own placement math
    expects the *destination* rectangle in final (post-scale) sheet
    coordinates, derived here directly from ``Placement`` without any
    reinterpretation of scale or translation. Sized to the page's own
    (unrotated) natural dimensions -- rotation, if any, is applied as a
    separate wrapping transform around a pivot, see ``_place_output_page``.
    """
    scaled_w = src_w * placement.scale_x
    scaled_h = src_h * placement.scale_y
    return Rectangle(
        placement.tx,
        placement.ty,
        placement.tx + scaled_w,
        placement.ty + scaled_h,
    )


def _rotation_matrix(rotate_deg: int, cx: float, cy: float) -> str:
    """A ``cm`` matrix string rotating ``rotate_deg`` about ``(cx, cy)``."""
    theta = math.radians(rotate_deg)
    cos_t = round(math.cos(theta), 10)
    sin_t = round(math.sin(theta), 10)
    e = cx - cx * cos_t + cy * sin_t
    f = cy - cx * sin_t - cy * cos_t
    return f"{cos_t} {sin_t} {-sin_t} {cos_t} {e} {f} cm"


def _place_output_page(
    sheet_pdf: pikepdf.Pdf,
    dest_page: pikepdf.Page,
    output_page: OutputPage,
    source_cache: dict[str, pikepdf.Pdf],
) -> None:
    if output_page.is_filler or output_page.source_ref is None:
        # A filler page carries no source content -- leave the blank page
        # exactly as ``add_blank_page`` created it.
        return

    ref = output_page.source_ref
    src_pdf = source_cache.get(ref.path)
    if src_pdf is None:
        src_pdf = pikepdf.open(ref.path)
        source_cache[ref.path] = src_pdf

    src_page = src_pdf.pages[ref.page_index]
    formx = sheet_pdf.copy_foreign(Page(src_page).as_form_xobject())
    name = dest_page.add_resource(formx, Name.XObject, prefix="Fx")

    placement = output_page.placement
    rotate_deg = placement.rotate_deg % 360

    if rotate_deg in (90, 270):
        # The final on-sheet footprint (tx/ty/width/height in Placement) is
        # already expressed post-rotation. Place the form at its natural
        # (pre-rotation) orientation centered on that same footprint, then
        # rotate the whole thing about the footprint's center -- this keeps
        # the placement rect's own scale exact while the wrapping transform
        # supplies the rotation Imposer decided on.
        scaled_w = ref.width_pt * placement.scale_x
        scaled_h = ref.height_pt * placement.scale_y
        footprint_w, footprint_h = scaled_h, scaled_w
        cx = placement.tx + footprint_w / 2.0
        cy = placement.ty + footprint_h / 2.0
        prerotate_rect = Rectangle(
            cx - scaled_w / 2.0, cy - scaled_h / 2.0, cx + scaled_w / 2.0, cy + scaled_h / 2.0
        )
        allow_shrink, allow_expand = _scale_flags_for(placement.scale_x)
        inner = dest_page.calc_form_xobject_placement(
            formx,
            name,
            prerotate_rect,
            invert_transformations=True,
            allow_shrink=allow_shrink,
            allow_expand=allow_expand,
        )
        rotation = _rotation_matrix(rotate_deg, cx, cy)
        content_stream = f"q\n{rotation}\n{inner.decode('latin-1')}\nQ\n".encode("latin-1")
    else:
        rect = _rect_for_placement(placement, ref.width_pt, ref.height_pt)
        allow_shrink, allow_expand = _scale_flags_for(placement.scale_x)
        content_stream = dest_page.calc_form_xobject_placement(
            formx,
            name,
            rect,
            invert_transformations=True,
            allow_shrink=allow_shrink,
            allow_expand=allow_expand,
        )
    dest_page.contents_add(content_stream)


def _sides(sheet: Sheet, side: str | None = None) -> list[Side]:
    """The physical faces of ``sheet`` -- front then back. One physical PDF
    page is produced per ``Side``, regardless of how many ``OutputPage``s
    (Form XObjects) it carries.

    ``side`` narrows the result to one face, which is what a manual-duplex
    pass needs: every front, or every back, one page per sheet. A sheet
    that has no such face contributes nothing rather than a blank page --
    an odd final sheet under gutter shift genuinely has no back, and
    inventing one is a sheet of paper the binder does not need.
    """
    sides = []
    if sheet.front is not None and side in (None, "front"):
        sides.append(sheet.front)
    if sheet.back is not None and side in (None, "back"):
        sides.append(sheet.back)
    return sides


_DASHED_MARK_KINDS = frozenset({"fold_line"})


def _draw_marks(dest_page: pikepdf.Page, marks: Sequence[Mark]) -> None:
    """Draw ``marks`` as vector line segments via ``ContentStreamBuilder``.

    Appended in its own balanced ``q...Q`` block after every page placement
    so it never interferes with the Form XObject placements already added.
    Every mark is a plain line segment stroked in the PDF default (black)
    colour -- ``fold_line`` marks are dashed, ``sewing_station`` and
    ``signature_order`` marks are solid.
    """
    if not marks:
        return
    builder = ContentStreamBuilder()
    for mark in marks:
        builder.push()
        builder.set_line_width(0.5)
        if mark.kind in _DASHED_MARK_KINDS:
            builder.set_dashes([3, 3])
        else:
            builder.set_dashes(None)
        builder.line(mark.x0, mark.y0, mark.x1, mark.y1)
        builder.stroke_and_close()
        builder.pop()
    content_stream = b"q\n" + builder.build() + b"Q\n"
    dest_page.contents_add(content_stream)


PT_PER_INCH = 72.0

# Clear space kept at each end of the rule, so it does not run into the
# sheet edge or the printer's non-printable border.
_RULE_END_CLEARANCE_PT = 0.5 * PT_PER_INCH

_RULE_BASELINE_PT = 0.5 * PT_PER_INCH
_RULE_TICK_PT = 6.0
_RULE_END_TICK_PT = 10.0
_RULE_LABEL_SIZE_PT = 8.0


def proof_rule_length_pt(paper_width_pt: float) -> float:
    """The length of the printed rule for a sheet this wide, in points.

    A whole number of inches, because the entire point is that a person
    reads it against a tape measure: "is this line 7 inches?" is a question
    anyone can answer, and "is this line 7.43 inches?" is not.

    :param paper_width_pt: the sheet width in points.
    :returns: the rule length in points, or ``0.0`` when the sheet is too
        narrow for even one inch with clearance -- there is no useful rule
        to draw, and half of one would be worse than none.
    """
    usable = paper_width_pt - 2 * _RULE_END_CLEARANCE_PT
    whole_inches = math.floor(usable / PT_PER_INCH)
    if whole_inches < 1:
        return 0.0
    return whole_inches * PT_PER_INCH


def _draw_proof_rule(dest_page: pikepdf.Page, paper_pt: tuple[float, float]) -> None:
    """Draw a ruler of known length, labelled with that length.

    This is the only direct evidence available that the printer honoured
    the export's "actual size" request. ``/PrintScaling /None`` is a hint;
    a driver preset can override it, and a sheet scaled by three percent
    looks exactly like one that was not. A line that should measure seven
    inches and measures six and three quarters settles it in one reading.

    Drawn over the page content rather than around it: a proof is a proof,
    and a rule tucked somewhere guaranteed to be empty would have to be
    short enough to be useless. Print it on sheet 0, measure, discard.
    """
    width, _height = paper_pt
    length = proof_rule_length_pt(width)
    if length <= 0.0:
        return

    inches = int(round(length / PT_PER_INCH))
    x0 = (width - length) / 2.0
    y = _RULE_BASELINE_PT

    builder = ContentStreamBuilder()
    builder.push()
    builder.set_line_width(0.5)
    builder.set_dashes(None)
    builder.line(x0, y, x0 + length, y)
    builder.stroke_and_close()
    for step in range(inches + 1):
        tick = _RULE_END_TICK_PT if step in (0, inches) else _RULE_TICK_PT
        tick_x = x0 + step * PT_PER_INCH
        builder.line(tick_x, y, tick_x, y + tick)
        builder.stroke_and_close()
    builder.pop()

    font = pikepdf.Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
    )
    font_name = dest_page.add_resource(font, Name.Font, prefix="Ft")
    builder.push()
    builder.begin_text()
    builder.set_text_font(font_name, _RULE_LABEL_SIZE_PT)
    builder.move_cursor(x0, y - _RULE_LABEL_SIZE_PT - 3.0)
    builder.show_text(
        f"{inches} in exactly -- if this measures short, the printer scaled "
        "the page"
    )
    builder.end_text()
    builder.pop()

    dest_page.contents_add(b"q\n" + builder.build() + b"Q\n")


def export(
    plan: SheetPlan,
    out_path: str,
    sheets: Sequence[int] | None = None,
    rule: bool = False,
    side: str | None = None,
    rotate_180: bool = False,
) -> None:
    """Render ``plan`` (or the sheets in ``sheets``) to a PDF at ``out_path``.

    ``sheets=None`` exports every sheet in the plan, in order. A subset
    selection uses the exact same composition code path -- there is no
    separate "single sheet" implementation.

    Raises before any bytes are written if ``out_path`` isn't writable, so a
    partial file never appears on disk.

    The scratch file is removed on every exit path -- success, exception, or
    cancellation upstream. Cleanup itself never raises: on Windows the
    scratch file can still be held briefly by a handle the failing export
    was using, and letting that ``PermissionError`` escape the ``finally``
    would replace the real cause of the failure with a misleading one.

    :param plan: the imposed sheets to render.
    :param out_path: the PDF to write. Written atomically -- composition
        goes to a scratch file beside it, which is renamed into place only
        once the whole document is assembled.
    :param sheets: the sheet indices to export, in the order given, or
        ``None`` for the whole plan. An index not present in the plan is
        skipped rather than raising.
    :param rule: draw a labelled ruler of known length on every face, so a
        printed sheet can be measured against it. For proofs -- it is drawn
        over the content, not around it.
    :param side: ``"front"`` or ``"back"`` to write only that face of each
        sheet -- one manual-duplex pass. ``None`` writes both, interleaved,
        which is what a real duplexer wants. A sheet lacking the requested
        face is skipped, not padded with a blank.
    :param rotate_180: turn every written page a half turn. What a back
        pass needs when the operator flips the stack on its long edge.
        Applied to the scratch file before it is renamed into place, so the
        output is never briefly present in the wrong orientation.
    :returns: nothing.
    :raises OSError: the output directory does not exist, or the scratch
        file cannot be created.
    :raises PermissionError: the output directory or an existing
        ``out_path`` is not writable. Checked *before* any bytes are
        written, so a partial file never appears on disk.
    :raises pikepdf.PdfError: a source page cannot be read or copied.
    """
    target_indices = (
        [s.index for s in plan.sheets] if sheets is None else list(sheets)
    )
    by_index = {s.index: s for s in plan.sheets}
    selected = [by_index[i] for i in target_indices if i in by_index]

    _check_writable(out_path)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(out_path)) or None)
    os.close(tmp_fd)
    try:
        _export_batched(plan, selected, tmp_path, rule, side)
        if rotate_180:
            rotate_pages_180(tmp_path)
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            _safe_remove(tmp_path)


def rotate_pages_180(pdf_path: str, flatten: bool = False) -> None:
    """Turn every page in ``pdf_path`` a half turn, in place.

    What a manual-duplex back pass needs when the operator flips the stack
    on its long edge: the sheet comes back through the printer upside down
    relative to its front, so the back sides have to be turned to match.
    :func:`deckle.core.printing.plan_passes` decides *whether*; this does it.

    Uses ``page.rotate(180, relative=True)`` rather than assigning the
    page's rotation key directly -- older qpdf has mishandled that, and a
    relative turn is also the only correct one for a page that already
    carries a rotation.

    :param pdf_path: the PDF to rotate, modified in place.
    :param flatten: also bake the rotation into the page content, for a
        driver known to ignore ``/Rotate``. Off by default: flattening is
        lossier, and most drivers honour the key.
    :returns: nothing.
    """
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            page.rotate(180, relative=True)
            if flatten:
                page.flatten_rotation()
        pdf.save(pdf_path)


def _check_writable(out_path: str) -> None:
    directory = os.path.dirname(os.path.abspath(out_path)) or "."
    if not os.path.isdir(directory):
        raise OSError(f"Directory does not exist: {directory}")
    if not os.access(directory, os.W_OK):
        raise PermissionError(f"Directory is not writable: {directory}")
    if os.path.exists(out_path) and not os.access(out_path, os.W_OK):
        raise PermissionError(f"Path is not writable: {out_path}")


def _export_batched(
    plan: SheetPlan,
    selected: list[Sheet],
    tmp_path: str,
    rule: bool = False,
    side: str | None = None,
    rotate_180: bool = False,
) -> None:
    """Assemble ``selected`` sheets into ``tmp_path``, saving/reopening in
    batches of ``_BATCH_SHEETS`` so source handles never accumulate across a
    very large document.
    """
    out = pikepdf.Pdf.new()
    source_cache: dict[str, pikepdf.Pdf] = {}
    try:
        for i, sheet in enumerate(selected, start=1):
            for face in _sides(sheet, side):
                dest_page = out.add_blank_page(page_size=plan.paper_pt)
                for output_page in face.pages:
                    _place_output_page(out, dest_page, output_page, source_cache)
                _draw_marks(dest_page, face.marks)
                if rule:
                    _draw_proof_rule(dest_page, plan.paper_pt)

            if i % _BATCH_SHEETS == 0 and i != len(selected):
                _flush_batch(out, source_cache, tmp_path)
                out = pikepdf.open(tmp_path, allow_overwriting_input=True)

        # Written to the document that is actually saved, not the first one
        # assembled: a batched export replaces `out` on every flush.
        _set_print_intent(out, plan.paper_pt, side)
        out.remove_unreferenced_resources()
        out.save(tmp_path)
    finally:
        for src in source_cache.values():
            src.close()
        out.close()


def _set_print_intent(
    out: pikepdf.Pdf, paper_pt: tuple[float, float], side: str | None = None
) -> None:
    """State in the catalog how this document is meant to reach paper.

    An exported PDF is printed by whatever viewer the user opens it in, and
    every one of them defaults to *fit to page*. That rescales the sheet to
    the printer's imageable area by a few percent, which moves the gutter,
    the margins and the sewing stations off the numbers the imposer
    computed -- and the output still looks entirely plausible. It is the
    only failure mode in the export path that produces a wrong result
    nobody can see.

    ``/ViewerPreferences`` is where a PDF says otherwise, and the values
    here are the print dialog Deckle would set if it were driving:

    - ``/PrintScaling /None`` -- print at actual size. The load-bearing one.
    - ``/Duplex`` -- which edge to turn the sheet about, from
      :func:`deckle.core.printing.duplex_flip_edge`. The same rule
      ``plan_passes`` applies to a manual reload, told to the duplexer
      instead of to the user. **A single pass says ``/Simplex`` instead**:
      it carries one face per page, so a driver that honoured a flip-edge
      hint would print two consecutive fronts onto two sides of one sheet.
      The hint that helps a duplexer is the one that ruins a manual-duplex
      job, and the difference is exactly whether the document interleaves
      faces.
    - ``/PickTrayByPDFSize`` -- choose the tray that fits the sheet rather
      than scaling the sheet to fit a tray.

    Every one is a *hint*: a viewer may ignore it, and a driver's own saved
    preset can still override it. That is worth doing anyway -- it makes
    the correct setting the default the user has to override, instead of a
    step they have to know about.

    :param out: the document about to be saved.
    :param paper_pt: the plan's sheet size, which decides the flip edge.
    :returns: nothing.
    """
    if side is None:
        flip = "Long" if duplex_flip_edge(paper_pt) == "long" else "Short"
        duplex = Name(f"/DuplexFlip{flip}Edge")
    else:
        duplex = Name("/Simplex")
    out.Root.ViewerPreferences = out.make_indirect(
        pikepdf.Dictionary(
            PrintScaling=Name("/None"),
            Duplex=duplex,
            PickTrayByPDFSize=True,
        )
    )


def _flush_batch(
    out: pikepdf.Pdf, source_cache: dict[str, pikepdf.Pdf], tmp_path: str
) -> None:
    """Save the in-progress output and close every open source handle.

    Called mid-assembly on very large documents so source PDFs stay open
    only for the batch that references them, not for the whole export.

    Clearing ``source_cache`` is the load-bearing half: without it the
    handles are closed but the dict keeps growing, and a project assembled
    from one PDF per page would hold an open handle for every page in the
    document by the end. The bound this establishes is on *concurrently
    open source handles*, not on total output size -- the assembled output
    document is necessarily in memory until it is saved.
    """
    out.remove_unreferenced_resources()
    out.save(tmp_path)
    out.close()
    log_event("export_batch_flushed", open_sources=len(source_cache))
    for src in source_cache.values():
        src.close()
    source_cache.clear()


class _LRUCache:
    """A bounded, LRU-evicting mapping of cache key -> exported file path.

    Evicted entries have their backing temp file removed. Not otherwise
    persisted -- cleared entirely on project close via ``clear()``.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[tuple[int, str], str] = OrderedDict()
        # Paths displaced by a racing put. They cannot be deleted on the
        # spot -- see put() -- but they must stay reachable so clear()
        # can still reclaim them.
        self._retired: deque[str] = deque()
        self._lock = threading.Lock()

    def get(self, key: tuple[int, str]) -> str | None:
        with self._lock:
            path = self._data.get(key)
            if path is None:
                return None
            if not os.path.exists(path):
                # Backing file vanished out from under us (e.g. temp
                # cleanup) -- treat as a miss rather than returning a
                # dangling path.
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return path

    def put(self, key: tuple[int, str], path: str) -> None:
        with self._lock:
            # Two threads can race to export the same key: each makes its
            # own temp file and both call put(). The loser's file must not
            # be deleted here -- the thread that exported it has already
            # been handed the path and is about to open it, so removing it
            # is a use-after-free that surfaces as a FileNotFoundError from
            # deep inside pdfium. Retire it instead: not reachable as a
            # cache hit, still reachable by clear(), so it is neither live
            # nor stranded.
            displaced = self._data.get(key)
            self._data[key] = path
            self._data.move_to_end(key)
            if displaced is not None and displaced != path:
                self._retired.append(displaced)
                # Bounded, so a long session of racing renders cannot fill
                # the disk. Anything this far back is long since closed.
                while len(self._retired) > _MAX_RETIRED:
                    _safe_remove(self._retired.popleft())
            while len(self._data) > self._maxsize:
                _, evicted_path = self._data.popitem(last=False)
                _safe_remove(evicted_path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            for path in self._data.values():
                _safe_remove(path)
            self._data.clear()
            while self._retired:
                _safe_remove(self._retired.popleft())


def _safe_remove(path: str) -> None:
    """Delete a temp file, tolerating failure.

    Failing to clean up must never fail the export that succeeded -- but
    silent failures accumulate, and a user who runs out of disk after a
    long session deserves a trail explaining where the space went.
    """
    try:
        os.remove(path)
    except OSError as exc:
        log_exception("temp_file_cleanup_failed", exc, path=path)


_cache = _LRUCache(_DEFAULT_CACHE_SIZE)
_call_count = 0
_call_count_lock = threading.Lock()


def _cache_dir() -> str:
    directory = os.path.join(tempfile.gettempdir(), _CACHE_DIR_NAME)
    os.makedirs(directory, exist_ok=True)
    return directory


def export_sheet_cached(plan: SheetPlan, sheet_index: int) -> str:
    """Export a single sheet to a temp PDF, cached by ``(sheet_index, plan_hash)``.

    Bounded LRU cache (default 200 entries): requesting the same
    ``(sheet_index, plan_hash)`` twice performs the export only once.
    Changing any layout setting changes ``plan_hash`` and so invalidates
    the cache for that sheet.

    A failed export leaves nothing behind: the placeholder temp file is
    removed before the exception propagates. Otherwise every failure --
    a corrupt source page, a full disk, a cancelled preview -- would strand
    a file in the cache directory that no ``clear_sheet_cache()`` can ever
    find, because it never reached the cache.

    :param plan: the plan the sheet belongs to. Its content hash is half
        the cache key, so changing any layout setting invalidates the
        entry rather than returning a stale render.
    :param sheet_index: which sheet, by ``Sheet.index``.
    :returns: the path to the cached single-sheet PDF. Owned by the cache
        -- callers must not delete it; use :func:`clear_sheet_cache`.
    :raises Exception: whatever :func:`export` raises, unchanged.
    """
    global _call_count

    key = (sheet_index, _plan_hash(plan))
    # A hit is already checked for existence by the cache itself: the OS
    # cleans the temp directory on a schedule Deckle does not control, and
    # an entry whose file has gone is reported as a miss and re-exported
    # rather than handing back a path that pdfium will reject.
    cached = _cache.get(key)
    if cached is not None:
        return cached

    fd, out_path = tempfile.mkstemp(suffix=".pdf", dir=_cache_dir())
    os.close(fd)
    with _call_count_lock:
        _call_count += 1
    try:
        export(plan, out_path, sheets=[sheet_index])
    except BaseException as exc:
        log_exception(
            "cached_sheet_export_failed", exc, sheet_index=sheet_index, path=out_path
        )
        _safe_remove(out_path)
        raise
    _cache.put(key, out_path)
    return out_path


def clear_sheet_cache() -> None:
    """Evict every cached single-sheet export and remove its temp file.

    Call on project close so scrubbing a large document doesn't leak temp
    files across sessions.

    :returns: nothing. A temp file that cannot be deleted is logged and
        skipped -- cleanup failing must not fail the close.
    """
    _cache.clear()
