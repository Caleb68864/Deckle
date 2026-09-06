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
from deckle.core.paths import evict_lru_files
from deckle.core.printing import duplex_flip_edge

_CACHE_DIR_NAME = "deckle_export_cache"

# Disk budget for the single-sheet cache directory, mirroring the import
# cache's 2 GB (loader._CACHE_MAX_BYTES). Smaller because these are single
# imposed sheets rather than normalised scans: 512 MB is thousands of them.
_CACHE_MAX_BYTES = 512 * 1024 ** 2

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
    """One page's contribution to the plan hash.

    The **path is length-prefixed** and everything else is joined plainly.
    That asymmetry is the point: `_update_delimited` explains why running
    variable-length keys together lets content forge a field boundary, and
    the path is the only free-text field here -- the rest are an integer, a
    64-character hex digest, two float reprs and a bool, none of which can
    contain the separator.

    So no collision was constructible before this, and that was **a
    property of the field types rather than of the framing**: a
    `SourceRef` gaining any second string field would have removed it
    silently. The framing now does not depend on what the other fields
    happen to be, which matters because `.deckle` files carry these paths
    and may be shared (red-team A-3).
    """
    ref = page.source_ref
    ref_key = (
        "none"
        if ref is None
        else (
            f"path[{len(ref.path.encode('utf-8'))}]:{ref.path}"
            f":{ref.page_index}:{ref.sha256}:{ref.width_pt}:{ref.height_pt}"
        )
    )
    placement = page.placement
    placement_key = (
        f"{placement.scale_x}:{placement.scale_y}:{placement.tx}:"
        f"{placement.ty}:{placement.rotate_deg}"
    )
    return f"{ref_key}|{placement_key}|{page.is_filler}|{page.crop_pt}"


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
    """A ``cm`` matrix rotating ``rotate_deg`` CLOCKWISE about ``(cx, cy)``.

    Clockwise because that is what ``Placement.rotate_deg`` means, which in
    turn is what PDF ``/Rotate`` and pdfium's ``rotation`` both mean. A
    positive angle in the PDF's own coordinate system turns
    counter-clockwise, so the angle is negated here -- once, in the one
    place that builds the matrix, rather than at each call site.

    This was counter-clockwise, silently, for as long as nothing composed
    it with a rotation the user could see: the only producer was the
    landscape policy, whose two directions are equally plausible on a page
    nobody asked to turn.
    """
    theta = math.radians(-rotate_deg)
    cos_t = round(math.cos(theta), 10)
    sin_t = round(math.sin(theta), 10)
    e = cx - cx * cos_t + cy * sin_t
    f = cy - cx * sin_t - cy * cos_t
    return f"{cos_t} {sin_t} {-sin_t} {cos_t} {e} {f} cm"


def _insets_in_stored_space(
    crop_pt: tuple[float, float, float, float], rotate_deg: int
) -> tuple[float, float, float, float]:
    """Re-label displayed-frame insets as stored-frame ones.

    ``/Rotate`` turns the page clockwise for display, so under 90 degrees
    the stored left edge is what the reader sees at the top. The insets
    arrive named for what the *user* can see -- that is the frame the
    loader measures in, the preview rasterises in, ``--auto-crop`` reports
    in, and therefore the only frame the number on the command line can
    mean -- so each one has to be re-labelled before it is subtracted from
    a stored coordinate.

    :param crop_pt: ``(left, bottom, right, top)`` as displayed.
    :param rotate_deg: the page's ``/Rotate``, normalised to 0/90/180/270.
    :returns: the same four insets, named for the stored edges.
    """
    left, bottom, right, top = crop_pt
    if rotate_deg == 90:
        return top, left, bottom, right
    if rotate_deg == 180:
        return right, top, left, bottom
    if rotate_deg == 270:
        return bottom, right, top, left
    return left, bottom, right, top


def _cropped_source_box(
    src_page: pikepdf.Page,
    crop_pt: tuple[float, float, float, float] | None,
    ref,
) -> tuple[float, float]:
    """Apply ``crop_pt`` to ``src_page`` and return the resulting size.

    The crop is enforced by setting the page's ``CropBox``, because
    ``as_form_xobject()`` takes the form's ``BBox`` from it -- so the
    cropped-away region is not merely covered up or placed off-sheet, it
    is outside the form's own bounding box and cannot be drawn at all.
    ``calc_form_xobject_placement`` then maps that BBox onto the
    destination rect, which is how the crop's offset is accounted for
    without any translation arithmetic here.

    **The two frames are the whole difficulty.** A page carrying
    ``/Rotate`` -- which is how every scanner and every "rotate and save"
    records a sideways page, so not an exotic input -- stores its box
    unrotated and is displayed turned. Every other layer of Deckle is
    already in the displayed frame: ``loader`` measures through pdfium,
    which applies ``/Rotate``; the preview rasterises through the same
    renderer; ``--auto-crop`` reports insets measured there; and the user
    types a number against what they saw. This function is the one place
    that touches the stored frame, so it is the one place that has to
    convert, and it converts in both directions: the insets are re-labelled
    on the way in, and the size is transposed on the way out.

    Getting only one of those right still fails. ``as_form_xobject`` puts
    the rotation in a ``/Matrix`` rather than in the ``BBox``, so the
    form's effective extent is the rotated box -- a size describing the
    stored box scales the placement by a width and height the wrong way
    round, while a cut on the stored edge removes the wrong margin. Both
    produce a confident, plausible sheet that is wrong by inches.

    Mutating the source page is safe and local: ``source_cache`` is
    created per export and closed with it, the source file is opened
    read-only and never saved, and each page's box is set immediately
    before its own form is built.

    :param src_page: the page to crop, modified in place.
    :param crop_pt: ``(left, bottom, right, top)`` insets as displayed, or
        ``None``.
    :param ref: the ``SourceRef``, for the page's measured size -- which is
        already the displayed size.
    :returns: the ``(width, height)`` the placement should be sized from,
        in the same frame as ``ref``.
    """
    if crop_pt is None:
        return ref.width_pt, ref.height_pt
    rotate_deg = int(src_page.rotation or 0) % 360
    left, bottom, right, top = _insets_in_stored_space(crop_pt, rotate_deg)
    box = [float(v) for v in src_page.cropbox]
    x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    src_page.cropbox = Rectangle(x0 + left, y0 + bottom, x1 - right, y1 - top)
    width = (x1 - x0) - left - right
    height = (y1 - y0) - bottom - top
    if rotate_deg in (90, 270):
        width, height = height, width
    return width, height


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

    # The other half of the page-index check. `project_io` refuses a
    # negative index, which it can judge from the entry alone; whether an
    # index is past the end depends on the source, which may not even have
    # been present at load time. Raised as a `ValueError` naming all three
    # numbers the remedy needs -- which file, which page was asked for, how
    # many it has -- rather than surfacing pikepdf's `IndexError:
    # Accessing nonexistent PDF page number` as a traceback out of
    # `deckle export`.
    page_count = len(src_pdf.pages)
    if not 0 <= ref.page_index < page_count:
        raise ValueError(
            f"{os.path.basename(ref.path)} has {page_count} page(s), but the "
            f"project asks for page {ref.page_index}. The source has probably "
            "been replaced with a shorter document since the project was saved."
        )
    src_page = src_pdf.pages[ref.page_index]
    # The crop is applied by mutating the page's CropBox, and `source_cache`
    # hands out the *same* page object every time a document reuses it --
    # which `deckle.core.locate` says outright is supported ("a document may
    # use the same source page twice"). Each insets were subtracted from the
    # box left behind by the previous one, so the second copy of a page was
    # cropped twice and the fourth four times: measured on a page repeated
    # four times with a 50pt left crop, the ink came out 71, 71, 61 and 35
    # pixels wide, the last one clipped through the middle of the numeral.
    #
    # Restoring the box makes the operation idempotent without keeping a
    # cache of original boxes: `copy_foreign` has already materialised the
    # form into `sheet_pdf` by then, so the source page is free to go back
    # to how it was found.
    original_box = None
    if output_page.crop_pt is not None:
        original_box = Rectangle(*(float(v) for v in src_page.cropbox))
    try:
        src_w, src_h = _cropped_source_box(src_page, output_page.crop_pt, ref)
        formx = sheet_pdf.copy_foreign(Page(src_page).as_form_xobject())
    finally:
        if original_box is not None:
            src_page.cropbox = original_box
    name = dest_page.add_resource(formx, Name.XObject, prefix="Fx")

    placement = output_page.placement
    rotate_deg = placement.rotate_deg % 360

    if rotate_deg in (90, 180, 270):
        # The final on-sheet footprint (tx/ty/width/height in Placement) is
        # already expressed post-rotation. Place the form at its natural
        # (pre-rotation) orientation centered on that same footprint, then
        # rotate the whole thing about the footprint's center -- this keeps
        # the placement rect's own scale exact while the wrapping transform
        # supplies the rotation Imposer decided on.
        #
        # A HALF TURN does not swap the footprint: a page turned 180 covers
        # the rectangle it covered upright. Only the quarter turns transpose
        # it. This branch used to test `in (90, 270)` and drop 180 into the
        # translation path below, where it was discarded in silence -- the
        # exported ink landed in exactly the pixels an unrotated placement
        # produced, which is what made it invisible for so long.
        scaled_w = src_w * placement.scale_x
        scaled_h = src_h * placement.scale_y
        if rotate_deg == 180:
            footprint_w, footprint_h = scaled_w, scaled_h
        else:
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
        rect = _rect_for_placement(placement, src_w, src_h)
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


def _sides(sheet: Sheet, side: str | None = None) -> list[Side | None]:
    """The faces of ``sheet`` to write, front then back.

    One physical PDF page is produced per entry, regardless of how many
    ``OutputPage``s (Form XObjects) it carries. ``None`` in the result
    means "write a blank page here".

    ``side`` narrows the result to one face -- one manual-duplex pass. The
    two modes differ in what they do about a face that does not exist, and
    the difference is load-bearing:

    - **Both faces** (``side is None``) omit it. This is the interleaved
      document a real duplexer consumes; there is nothing to print, so a
      page there is a wasted side of paper.

    - **A pass** pads it with a blank. A pass PDF's pages map one-to-one
      onto the sheets being fed, so dropping one shifts every later back
      onto the wrong front -- the entire stack ruined, and not discovered
      until the paper is spent. A blank sheet through the printer costs a
      pass; a mis-registered stack costs the job.

    No plan Deckle currently produces reaches the padding branch --
    ``_pad_to_even`` gives gutter shift an even slot count, so its
    ``back=None`` case is unreachable. It is written correctly anyway
    because the cost of the two branches is so lopsided.
    """
    if side is None:
        return [face for face in (sheet.front, sheet.back) if face is not None]
    return [sheet.front if side == "front" else sheet.back]


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
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
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
        sheet -- one manual-duplex pass, one page per sheet, padded with a
        blank where a sheet has no such face so the pass stays in register.
        ``None`` writes both faces interleaved, which is what a real
        duplexer wants, and there omits a face that does not exist.
    :param rotate_180: turn every written page a half turn. What a back
        pass needs when the operator flips the stack on its long edge.
        Applied to the scratch file before it is renamed into place, so the
        output is never briefly present in the wrong orientation.
    :param back_offset_pt: the front/back registration correction, applied
        to BACK faces only -- see
        :attr:`deckle.core.profiles.PrinterProfile.back_offset_x_pt`.
        Expressed on the paper, so it is negated when ``rotate_180`` turns
        the face. ``(0.0, 0.0)`` writes nothing at all.
    :returns: nothing.
    :raises OSError: the output directory does not exist, or the scratch
        file cannot be created.
    :raises PermissionError: the output directory or an existing
        ``out_path`` is not writable. Checked *before* any bytes are
        written, so a partial file never appears on disk.
    :raises pikepdf.PdfError: a source page cannot be read or copied.
    :raises ExportVerificationError: the assembled document does not match
        the plan -- a page count or a sheet size that disagrees. Checked
        before the scratch file is renamed, so the destination is never
        written and any previous export there survives. Not a user error:
        see the exception's own documentation.
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
        _export_batched(
            plan,
            selected,
            tmp_path,
            rule=rule,
            side=side,
            # By keyword: these two are both "extra behaviour flags" and
            # positionally interchangeable to the reader, so an argument
            # order that drifts would swap a tuple and a bool silently.
            back_offset_pt=back_offset_pt,
            rotate_180=rotate_180,
        )
        if rotate_180:
            rotate_pages_180(tmp_path)
        # Checked on the scratch file, before it is renamed into place: a
        # file that fails this must never reach the destination, and a
        # previous good export sitting at that path has to survive.
        _verify_output(
            tmp_path,
            expected_pages=sum(len(_sides(sheet, side)) for sheet in selected),
            paper_pt=plan.paper_pt,
        )
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            _safe_remove(tmp_path)


def _is_back_face(sheet: Sheet, side: str | None, position: int) -> bool:
    """Whether the face at ``position`` in ``_sides(sheet, side)`` is a back.

    Under ``side="back"`` every face is one. Under ``side="front"`` none
    is. In a both-faces export the faces are front then back, so the back
    is whichever entry is not the front -- which is position 1 normally,
    and position 0 on a sheet that has a back and no front.
    """
    if side == "back":
        return True
    if side == "front":
        return False
    return position == (1 if sheet.front is not None else 0)


def _apply_back_offset(
    dest_page: pikepdf.Page,
    offset_pt: tuple[float, float],
    rotate_180: bool,
) -> None:
    """Shift a whole back face by the registration correction.

    Applied as a page-level translation wrapping everything already
    composed -- placements, marks and rule alike. Two reasons it is not
    done by adjusting each ``Placement``: this module promises never to
    modify a ``Placement`` the imposer produced (see the module
    docstring), and the correction is not a layout decision at all. The
    layout is right; the printer is putting it in the wrong place, and the
    fold line and sewing stations have to move with the content or they
    would no longer mark where the content actually is.

    **The rotation interaction is the subtle part.** A long-edge back pass
    is turned a half turn, and a point reflection maps a translation to
    its negation -- so a correction measured on the paper has to be
    inverted in page space to survive the turn. Applied unchanged it would
    move the back exactly twice as far wrong as leaving it alone.

    :param dest_page: the composed back face.
    :param offset_pt: the correction on the paper, ``(dx, dy)``.
    :param rotate_180: whether this face will be turned a half turn.
    :returns: nothing. A zero offset writes nothing at all, so an
        uncalibrated printer produces byte-for-byte what it always did.
    """
    dx, dy = offset_pt
    if rotate_180:
        dx, dy = -dx, -dy
    if dx == 0.0 and dy == 0.0:
        return
    dest_page.contents_add(f"q\n1 0 0 1 {dx:g} {dy:g} cm\n".encode("latin-1"), prepend=True)
    dest_page.contents_add(b"Q\n")


class ExportVerificationError(Exception):
    """The written PDF does not match the plan it was composed from.

    Deliberately not caught by the CLI. Every input this module rejects
    for a user's reason -- an unwritable path, a missing directory, a
    corrupt source -- is refused before composition starts, so by the time
    a document has been assembled the only way it can fail this check is a
    defect in Deckle. A traceback is the correct outcome for that, and a
    message that made it look like the user's problem would send them
    looking for an error they did not make.
    """


# Page sizes round-trip through the PDF as decimal strings, so the value
# read back is close to, not identical with, the float laid out for.
_SIZE_TOLERANCE_PT = 1e-6


def _verify_output(path: str, expected_pages: int, paper_pt: tuple[float, float]) -> None:
    """Check the written file against what the plan said it would be.

    Every other check in this module runs on the ``SheetPlan``. Nothing had
    ever looked at the artifact, so a composition that dropped a page or
    sized one wrongly would be reported as a successful export and the
    first symptom would be paper coming out of a printer.

    Two properties, chosen because they are total and cheap -- neither
    rasterizes anything, so this costs one open and a metadata read even
    on a long document:

    - **One page per face.** Catches a face silently lost, and a pass that
      lost its one-to-one mapping onto the sheets being fed.
    - **Every page the size the plan was laid out for.** Catches a sheet
      composed against a different paper than the placements assumed,
      which is the failure that looks correct on screen and is wrong by a
      measurable margin on paper.

    :param path: the PDF to inspect.
    :param expected_pages: how many pages composition should have written.
    :param paper_pt: the plan's sheet size.
    :returns: nothing.
    :raises ExportVerificationError: the file disagrees with the plan.
    """
    width, height = paper_pt
    with pikepdf.open(path) as pdf:
        actual_pages = len(pdf.pages)
        if actual_pages != expected_pages:
            raise ExportVerificationError(
                f"composed {actual_pages} page(s) but the plan calls for "
                f"{expected_pages}"
            )
        for index, page in enumerate(pdf.pages):
            box = [float(value) for value in page.mediabox]
            page_w = abs(box[2] - box[0])
            page_h = abs(box[3] - box[1])
            if not (
                math.isclose(page_w, width, abs_tol=_SIZE_TOLERANCE_PT)
                and math.isclose(page_h, height, abs_tol=_SIZE_TOLERANCE_PT)
            ):
                raise ExportVerificationError(
                    f"page {index} is {page_w:g}x{page_h:g}pt but the plan "
                    f"is laid out for {width:g}x{height:g}pt"
                )


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
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
) -> None:
    """Assemble ``selected`` sheets into ``tmp_path``, saving/reopening in
    batches of ``_BATCH_SHEETS`` so source handles never accumulate across a
    very large document.
    """
    out = pikepdf.Pdf.new()
    source_cache: dict[str, pikepdf.Pdf] = {}
    try:
        for i, sheet in enumerate(selected, start=1):
            faces = _sides(sheet, side)
            for position, face in enumerate(faces):
                dest_page = out.add_blank_page(page_size=plan.paper_pt)
                # `None` is a face that does not exist on a sheet a pass
                # still has to feed -- the blank page above is the whole
                # of it, and it keeps the pass in register.
                if face is not None:
                    for output_page in face.pages:
                        _place_output_page(out, dest_page, output_page, source_cache)
                    _draw_marks(dest_page, face.marks)
                if rule:
                    _draw_proof_rule(dest_page, plan.paper_pt)
                if _is_back_face(sheet, side, position):
                    _apply_back_offset(dest_page, back_offset_pt, rotate_180)

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
    # The in-memory LRU bounds what this cache will hand back. It does
    # nothing about the *directory*, which keeps every file a killed
    # process left behind and every file whose deletion failed -- and
    # `clear_sheet_cache`, whose docstring says to call it on project
    # close, had no callers at all. Measured at 8,297 files and 50 MB in
    # one development machine's temp directory.
    #
    # Bounded here rather than at shutdown for the reason the import cache
    # already is (red-team A-7): a budget enforced by the next writer
    # survives a crash, and a cleanup call at exit is exactly what a crash
    # skips.
    evict_lru_files(
        directory, _CACHE_MAX_BYTES,
        on_error=lambda event, exc, path: log_exception(event, exc, path=path),
    )
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

    Called when a project is replaced, so scrubbing a large document does
    not leave a session's worth of sheets behind. It **said** that before
    anything called it, which is why the directory is also bounded by a
    disk budget in :func:`_cache_dir`: a cleanup hook only runs on the
    exits that reach it, and the leak this addresses is largest on the
    ones that do not.

    :returns: nothing. A temp file that cannot be deleted is logged and
        skipped -- cleanup failing must not fail the close.
    """
    _cache.clear()
