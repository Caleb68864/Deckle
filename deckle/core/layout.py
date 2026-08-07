"""The Imposer: pure gutter-shift layout math.

Given source pages and ``LayoutSettings``, produce a ``SheetPlan``. This
module must stay free of file, network, and print I/O -- see
``tests/test_core_purity.py``. ``LayoutStrategy`` is the v2 seam for
signature/saddle-stitch imposition; ``GutterShiftStrategy`` is the only
implementation shipped in the MVP, and every configuration knob arrives via
``settings`` rather than a narrowed, gutter-specific signature.
"""

from __future__ import annotations

from typing import Literal, Protocol, Sequence, runtime_checkable

from deckle.core.marks import (
    cut_lines,
    fold_line,
    sewing_stations,
    signature_order_mark,
)
from deckle.core.models import (
    LayoutSettings,
    LayoutWarning,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    Signature,
    SourcePage,
    is_blank_page,
)
from deckle.core.signatures import (
    saddle_order,
    split_signatures,
    split_signatures_at,
)

Cell = tuple[float, float, float, float]
"""A cell within a sheet: ``(x0, y0, x1, y1)`` in sheet points.

``GutterShiftStrategy`` passes the full sheet as its cell (the default,
``None``, means exactly that). ``SaddleStitchStrategy`` splits the sheet
into two cells at the fold -- see ``cell_geometry``.
"""


def _full_sheet_cell(paper: tuple[float, float]) -> Cell:
    return (0.0, 0.0, paper[0], paper[1])


@runtime_checkable
class LayoutStrategy(Protocol):
    """A pluggable page-imposition algorithm.

    Strategies are stateless: all configuration arrives via ``settings``,
    never via constructor arguments or narrowed method parameters, so v2
    strategies (signatures, saddle stitch) can implement this same
    interface without breaking callers.
    """

    def impose(
        self, pages: Sequence[SourcePage], settings: LayoutSettings
    ) -> SheetPlan:
        """Lay ``pages`` out onto sheets under ``settings``.

        :param pages: the project's pages in reading order. Implementations
            drop ``skipped`` pages themselves rather than expecting a
            pre-filtered list.
        :param settings: every configuration knob. Nothing else is passed;
            that is what keeps this signature stable across strategies.
        :returns: the imposed sheets, plus any advisory warnings.
        """
        ...


def _is_recto(output_index: int) -> bool:
    """Index 0 is the first page -- a recto, spine on the left."""
    return output_index % 2 == 0


def _gutter_side_is_left(is_recto: bool, binding_edge: str) -> bool:
    """Whether this output page's reserved gutter sits on the page's left.

    Left binding edge: rectos are pushed right off the spine (gutter on the
    left); versos are flush left (gutter falls on the right). Right binding
    edge is the exact mirror: rectos flush left, versos pushed right.
    """
    if binding_edge == "left":
        return is_recto
    return not is_recto


def _lead_for_recto(
    active: list[SourcePage | None], settings: LayoutSettings
) -> list[SourcePage | None]:
    """Prepend a filler when the first content page must fall on a verso.

    Output index 0 is a recto by definition, so ``start_on_recto=True``
    costs nothing. ``False`` is the case that needs work: the first content
    page has to be pushed onto a left-hand page, which takes exactly one
    leading filler. Without this the setting was accepted, persisted into
    the ``.deckle`` file, and then ignored -- a control that silently does
    nothing is worse than one that is missing.

    An empty document gets no filler: a leading blank is a position for
    content, and there is no content to position.

    :param active: slots so far, ``None`` for a blank.
    :param settings: read for ``start_on_recto``.
    :returns: ``active``, with one leading ``None`` when one is needed.
    """
    if settings.start_on_recto or not active:
        return active
    return [None, *active]


def _pad_to_even(pages: list[SourcePage]) -> tuple[list[SourcePage | None], bool]:
    """Pad an odd-length page list with exactly one trailing ``None`` filler.

    Runs exactly once -- never loops -- so an already-even list is returned
    untouched and an odd list always gains exactly one filler slot.
    """
    slots: list[SourcePage | None] = list(pages)
    padded = False
    if len(slots) % 2 != 0:
        slots.append(None)
        padded = True
    return slots, padded


def crop_for(
    page: SourcePage, settings: LayoutSettings
) -> tuple[float, float, float, float] | None:
    """The crop insets that apply to ``page``, or ``None``.

    Odd and even are chosen by the page number a reader would say, so
    ``page_index`` 0 is page 1 and odd. A scanned book alternates its
    margins -- the gutter swaps sides on every leaf -- so a single
    rectangle cannot fit both and the two are configured separately.

    Setting only ``crop_odd_pt`` crops the whole document, which is the
    common case and needs no second value.

    :param page: the source page.
    :param settings: supplies the two crop rectangles.
    :returns: ``(left, bottom, right, top)`` insets, or ``None``.
    """
    is_even_page = (page.ref.page_index + 1) % 2 == 0
    if is_even_page and settings.crop_even_pt is not None:
        return settings.crop_even_pt
    return settings.crop_odd_pt


def _cropped_dims(
    width: float, height: float, crop: tuple[float, float, float, float] | None
) -> tuple[float, float]:
    """``(width, height)`` after ``crop``, validating that anything remains.

    :raises ValueError: an inset is negative -- which would *add* space and
        place content outside its own page box -- or the opposing insets
        meet, leaving no content to impose.
    """
    if crop is None:
        return width, height
    left, bottom, right, top = crop
    if min(crop) < 0:
        raise ValueError(
            f"crop {crop} has a negative inset: a crop removes space, and "
            "the margin settings are what add it"
        )
    cropped_w = width - left - right
    cropped_h = height - bottom - top
    if cropped_w <= 0 or cropped_h <= 0:
        raise ValueError(
            f"crop {crop} leaves nothing of a {width:g}x{height:g}pt page"
        )
    return cropped_w, cropped_h


def _source_dims(
    page: SourcePage, settings: LayoutSettings | None = None
) -> tuple[float, float]:
    """The upright (unrotated-by-us) width/height of a source page's content.

    With ``settings``, this is the size *after* cropping -- the single
    place the cropped size is derived, so ``document_scale`` and
    ``_place_page`` cannot disagree about how big a page is.

    The crop is applied before the rotation swap, because the insets are
    named in the source page's own orientation: "left" is the left edge of
    the page as it exists in the file, not of the cell Deckle puts it in.
    """
    width = page.ref.width_pt
    height = page.ref.height_pt
    if settings is not None:
        width, height = _cropped_dims(width, height, crop_for(page, settings))
    if page.rotate_deg in (90, 270):
        width, height = height, width
    return width, height


def content_box_size(
    settings: LayoutSettings, cell: Cell | None = None
) -> tuple[float, float]:
    """The content box's ``(width, height)`` in points, after all four margins.

    Falls back to the bare cell when the margins would consume it entirely;
    ``_place_page`` warns about that case, this function stays silent so it
    can be called from the scale pass without duplicating warnings.

    ``cell`` defaults to the whole sheet -- ``GutterShiftStrategy``'s only
    cell. ``SaddleStitchStrategy`` passes one of the two folio cells.

    :param settings: supplies the paper size and all four margins.
    :param cell: the region to measure inside, or ``None`` for the whole
        sheet.
    :returns: ``(width, height)`` in points.
    """
    if cell is None:
        cell = _full_sheet_cell(settings.paper)
    cx0, cy0, cx1, cy1 = cell
    cell_w = cx1 - cx0
    cell_h = cy1 - cy0
    box_w = cell_w - max(0.0, settings.gutter_pt) - max(0.0, settings.margin_outer_pt)
    box_h = cell_h - max(0.0, settings.margin_top_pt) - max(0.0, settings.margin_bottom_pt)
    if box_w <= 0.0 or box_h <= 0.0:
        return (cell_w, cell_h)
    return (box_w, box_h)


def grain_warning(settings: LayoutSettings) -> LayoutWarning | None:
    """Warn when the spine will run across the paper's grain, not along it.

    :param settings: the layout to check.
    :returns: a :class:`LayoutWarning`, or ``None`` when the grain is
        unknown or already correct.

    The rule the whole of bookbinding agrees on is that grain runs parallel
    to the spine. Fold across the grain and the crease cracks and feathers
    instead of creasing; the finished book refuses to open flat, cockles
    when glue wets it, and warps with humidity.

    The spine is vertical in both of Deckle's schemes -- it is the left or
    right binding edge under gutter shift, and the vertical centre fold
    under folio -- so the test is whether the grain also runs vertically on
    the sheet as loaded.

    This catches the most common home-binding mistake there is. Ordinary
    letter and A4 stock is **long grain**: the fibres run along the longer
    edge. Turn a letter sheet landscape to fold it into a 5.5x8.5 book and
    the grain now runs horizontally while the fold runs vertically -- across
    the grain, the wrong way, on the paper almost everyone has. Binders buy
    short-grain stock specifically to fix this.

    Silent when ``grain`` is ``"unknown"``, which is the default: most
    people do not know their paper's grain, and a warning nobody can act on
    is noise.
    """
    if settings.grain not in ("long", "short"):
        return None

    width, height = settings.paper
    if width == height:
        return None  # square stock: the fold direction cannot be wrong

    long_edge_is_horizontal = width > height
    grain_is_horizontal = (
        long_edge_is_horizontal if settings.grain == "long" else not long_edge_is_horizontal
    )
    if not grain_is_horizontal:
        return None  # grain already runs with the spine

    folded = settings.fold_scheme == "folio"
    action = "fold" if folded else "spine"
    return LayoutWarning(
        sheet_index=0,
        kind="grain_direction",
        detail=(
            f"the {action} runs across the paper grain, not along it. "
            f"{settings.grain}-grain stock at "
            f"{width:.0f}x{height:.0f}pt has its fibres running horizontally, "
            "while the spine runs vertically. Expect the fold to crack rather "
            "than crease, and the finished book to resist opening flat. Turn "
            "the sheet, or use "
            f"{'short' if settings.grain == 'long' else 'long'}-grain stock."
        ),
    )


def _rotates_to_portrait(src_w: float, src_h: float, settings: LayoutSettings) -> bool:
    paper_w, paper_h = settings.paper
    return settings.landscape_policy == "rotate" and paper_h >= paper_w and src_w > src_h


def _fitted_dims(slot: SourcePage, settings: LayoutSettings) -> tuple[float, float]:
    """A page's upright dimensions after any landscape rotation."""
    src_w, src_h = _source_dims(slot, settings)
    if _rotates_to_portrait(src_w, src_h, settings):
        src_w, src_h = src_h, src_w
    return src_w, src_h


def document_scale(
    pages: Sequence[SourcePage], settings: LayoutSettings, cell: Cell | None = None
) -> float:
    """One scale for the WHOLE document -- the largest that fits every page.

    Scaling each page independently would let every page fill its own box,
    but a book is not a pile of independent pages: differing source widths
    would then be reproduced at differing scales, so body text would change
    size from page to page. On the Traveller Core Rulebook (264 pages at
    519.36pt, a 506.88pt cover, one 527.28pt page) per-page scaling made the
    cover's text 2.5% larger than the body's.

    Taking the minimum means no page overflows and every page is reproduced
    at identical scale; narrower pages simply carry more slack, which the
    margin rules then distribute.

    Note this is distinct from the predecessor script's defect, which applied
    page 0's *aspect ratio* to every page's geometry. Per-page geometry is
    correct; per-page scale is not.

    :param pages: the pages to fit. Skipped pages and ``None`` slots are
        ignored, as are pages with a non-positive dimension.
    :param settings: supplies the margins that define the content box.
    :param cell: the region pages are fitted into, or ``None`` for the
        whole sheet. Under folio every cell is identical, so any one of
        them gives the document-wide answer.
    :returns: the scale factor. ``1.0`` when there is nothing to fit --
        an empty document has no constraint to satisfy.
    """
    box_w, box_h = content_box_size(settings, cell)
    scales = []
    for slot in pages:
        if slot is None or slot.skipped:
            continue
        src_w, src_h = _fitted_dims(slot, settings)
        if src_w <= 0 or src_h <= 0:
            continue
        scales.append(min(box_w / src_w, box_h / src_h))
    return min(scales) if scales else 1.0


def _place_page(
    slot: SourcePage | None,
    output_index: int,
    settings: LayoutSettings,
    sheet_index: int,
    warnings: list[LayoutWarning],
    scale: float,
    *,
    cell: Cell | None = None,
    spine_side: Literal["left", "right"] | None = None,
) -> OutputPage:
    """Place one leaf's content within ``cell`` (default: the whole sheet).

    ``spine_side`` -- when given -- names which edge of ``cell`` carries the
    spine directly, bypassing output-page parity entirely. This is the
    folio path: under ``fold_scheme="folio"`` the spine is a function of
    which cell a leaf sits in, never of recto/verso parity, and
    ``_gutter_side_is_left`` must not be reachable from that path.
    """
    if cell is None:
        cell = _full_sheet_cell(settings.paper)
    cx0, cy0, cx1, cy1 = cell
    cell_w = cx1 - cx0
    cell_h = cy1 - cy0

    if slot is None:
        placement = Placement(scale_x=1.0, scale_y=1.0, tx=cx0, ty=cy0, rotate_deg=0)
        return OutputPage(source_ref=None, placement=placement, is_filler=True)

    src_w, src_h = _source_dims(slot, settings)
    rotate_deg = 0

    # Landscape content inside a portrait cell (or vice versa) under the
    # "rotate" policy: rotate the content to match the cell's orientation
    # and warn, rather than silently clipping or shrinking it. Under folio
    # each cell is portrait-shaped even though the sheet itself is
    # landscape, so this must be judged against the cell, not the sheet.
    cell_is_portrait = cell_h >= cell_w
    page_is_landscape = src_w > src_h
    if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
        rotate_deg = 90
        src_w, src_h = src_h, src_w
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="mixed_orientation",
                detail=(
                    f"page {slot.ref.page_index} of {slot.ref.path!r} is "
                    "landscape inside a portrait document; rotated 90deg"
                ),
            )
        )

    # ------------------------------------------------------------------
    # The content box. All four page edges have a margin: the gutter IS the
    # inner (spine) margin, and outer/top/bottom cover the rest.
    # ------------------------------------------------------------------
    gutter = max(0.0, settings.gutter_pt)
    outer = max(0.0, settings.margin_outer_pt)
    top = max(0.0, settings.margin_top_pt)
    bottom = max(0.0, settings.margin_bottom_pt)

    box_w = cell_w - gutter - outer
    box_h = cell_h - top - bottom

    if box_w <= 0.0 or box_h <= 0.0:
        # Margins consume the whole cell. Warn and fall back to the bare
        # cell rather than producing a negative-size box and nonsense scale.
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="clipped_by_page",
                detail=(
                    "margins and gutter exceed the paper size; "
                    "ignoring margins for this sheet"
                ),
            )
        )
        gutter = outer = top = bottom = 0.0
        box_w, box_h = cell_w, cell_h

    # `scale` is computed ONCE for the whole document by `document_scale` --
    # the largest that fits every page -- so body text is reproduced at
    # identical size throughout. Narrower pages carry more slack, which the
    # margin rules below distribute.
    scaled_w = src_w * scale
    scaled_h = src_h * scale

    # ------------------------------------------------------------------
    # Position. ONE rule, applied identically to both axes:
    #
    #   Margins are MINIMUMS. Spare space inside the box is shared equally
    #   between the two opposing margins, so their difference is preserved
    #   exactly. When content OVERFLOWS the box, there is no spare space to
    #   share, so the specified margin is held and the overflow goes to the
    #   opposite edge -- the gutter is never eaten by content that does not
    #   fit, because the gutter is the one margin the binding physically
    #   needs.
    #
    # Previously the horizontal axis anchored to the gutter while the
    # vertical axis centred, so the two axes behaved differently for the
    # same inputs.
    # ------------------------------------------------------------------
    slack_w = max(0.0, box_w - scaled_w)
    slack_h = max(0.0, box_h - scaled_h)

    # Content that overflows the box after scaling is clipped by whichever
    # edge it overflows -- worth a warning measured against the cell (the
    # box the content was actually fitted into), not the sheet. Under
    # folio the cell is half the sheet, so this can fire even when the
    # same content would fit comfortably on a full, unsplit sheet.
    if scaled_w > box_w + 1e-9 or scaled_h > box_h + 1e-9:
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="clipped_by_page",
                detail=(
                    f"page {slot.ref.page_index} of {slot.ref.path!r} "
                    "content exceeds its cell after scaling"
                ),
            )
        )

    # `slack_to` decides which horizontal margin absorbs the difference when
    # source pages vary in width -- i.e. which stays constant through the
    # book and which varies. See LayoutSettings.slack_to.
    #
    # Vertical slack is always split: neither head nor tail has a binding to
    # accommodate, so there is nothing to bias toward.
    if settings.slack_to == "outer":
        inner_actual = gutter                    # spine exact; fore-edge varies
    elif settings.slack_to == "split":
        inner_actual = gutter + slack_w / 2.0    # both vary, difference kept
    else:  # "gutter" (default)
        inner_actual = gutter + slack_w          # fore-edge exact; spine varies
    bottom_actual = bottom + slack_h / 2.0

    if spine_side is not None:
        gutter_on_left = spine_side == "left"
    else:
        gutter_on_left = _gutter_side_is_left(_is_recto(output_index), settings.binding_edge)
    tx = cx0 + (inner_actual if gutter_on_left else cell_w - inner_actual - scaled_w)
    ty = cy0 + bottom_actual

    placement = Placement(
        scale_x=scale,
        scale_y=scale,
        tx=tx,
        ty=ty,
        rotate_deg=rotate_deg,
    )
    return OutputPage(
        source_ref=slot.ref,
        placement=placement,
        is_filler=False,
        crop_pt=crop_for(slot, settings),
    )


def content_box_rect_pt(
    settings: LayoutSettings,
    *,
    is_recto: bool | None = None,
    spine_side: Literal["left", "right"] | None = None,
    cell: Cell | None = None,
) -> tuple[float, float, float, float]:
    """The content box as ``(x0, y0, x1, y1)`` in PDF (bottom-left origin) points.

    This is the rectangle the gutter and margins define -- the space content
    is fitted into. It is **not** the printer's imageable area, which is a
    property of the hardware; the two coincide only when every margin
    happens to equal the printer's inset. Drawing both is what makes the
    relationship legible: content aligns with this box on whichever axis
    binds, and sits inset from it on the other by the aspect-ratio slack.

    ``cell`` defaults to the whole sheet, matching every existing call site.
    Under folio, margins are measured **inside the cell**, not from the
    sheet edge -- pass the cell explicitly and give ``spine_side`` rather
    than ``is_recto``, since the folio spine is a function of cell position,
    not output-page parity.

    :param settings: supplies the paper size, gutter and margins.
    :param is_recto: whether this is a right-hand page, which together with
        ``settings.binding_edge`` decides which side the gutter falls on.
        Ignored when ``spine_side`` is given.
    :param spine_side: which edge of ``cell`` carries the spine directly,
        bypassing page parity. The folio path.
    :param cell: the region to measure inside, or ``None`` for the whole
        sheet.
    :returns: ``(x0, y0, x1, y1)`` in PDF points. Falls back to the bare
        cell when the margins would consume it entirely.
    """
    if cell is None:
        cell = _full_sheet_cell(settings.paper)
    cx0, cy0, cx1, cy1 = cell
    cell_w = cx1 - cx0
    cell_h = cy1 - cy0
    gutter = max(0.0, settings.gutter_pt)
    outer = max(0.0, settings.margin_outer_pt)
    top = max(0.0, settings.margin_top_pt)
    bottom = max(0.0, settings.margin_bottom_pt)

    if cell_w - gutter - outer <= 0.0 or cell_h - top - bottom <= 0.0:
        return (cx0, cy0, cx1, cy1)

    if spine_side is not None:
        gutter_on_left = spine_side == "left"
    else:
        gutter_on_left = _gutter_side_is_left(is_recto, settings.binding_edge)
    left, right = (gutter, outer) if gutter_on_left else (outer, gutter)
    return (cx0 + left, cy0 + bottom, cx1 - right, cy1 - top)


def actual_margins_pt(
    output_page: OutputPage,
    paper: tuple[float, float],
    *,
    is_recto: bool | None = None,
    binding_edge: str,
    spine_side: Literal["left", "right"] | None = None,
    cell: Cell | None = None,
) -> tuple[float, float, float, float]:
    """The margins a placed page actually ends up with: ``(inner, outer, top, bottom)``.

    "Inner" is the spine side, which is the left edge on a recto under a left
    binding and the right edge on a verso. Negative values mean the content
    overflows that edge. Pure geometry over the emitted ``Placement``, so
    tests and the UI measure the same numbers the exporter will use rather
    than re-deriving them from settings.

    ``cell`` defaults to the whole sheet, so margins are measured relative
    to sheet edges as before. Under folio, pass the leaf's cell so a
    right-hand-cell leaf's margins are measured against ``x0 = 396``, not
    ``0`` -- and pass ``spine_side`` instead of ``is_recto``, since the
    folio spine is a function of cell position, not page parity.

    :param output_page: the placed page to measure. A filler page has no
        content, so it measures as all zeros rather than raising.
    :param paper: the sheet size, used only to derive the default cell.
    :param is_recto: whether this is a right-hand page. Ignored when
        ``spine_side`` is given.
    :param binding_edge: ``"left"`` or ``"right"``, paired with
        ``is_recto`` to decide which measured edge is the spine.
    :param spine_side: which edge of ``cell`` is the spine, bypassing page
        parity.
    :param cell: the region to measure against, or ``None`` for the whole
        sheet.
    :returns: ``(inner, outer, top, bottom)`` in points. Negative means
        the content overflows that edge.
    """
    if cell is None:
        cell = _full_sheet_cell(paper)
    cx0, cy0, cx1, cy1 = cell
    p = output_page.placement
    ref = output_page.source_ref
    if ref is None:
        return (0.0, 0.0, 0.0, 0.0)

    src_w, src_h = ref.width_pt, ref.height_pt
    if p.rotate_deg in (90, 270):
        src_w, src_h = src_h, src_w
    scaled_w = src_w * p.scale_x
    scaled_h = src_h * p.scale_y

    left = p.tx - cx0
    right = cx1 - p.tx - scaled_w
    bottom = p.ty - cy0
    top = cy1 - p.ty - scaled_h

    if spine_side is not None:
        gutter_on_left = spine_side == "left"
    else:
        gutter_on_left = _gutter_side_is_left(is_recto, binding_edge)
    inner, outer = (left, right) if gutter_on_left else (right, left)
    return (inner, outer, top, bottom)


class GutterShiftStrategy:
    """MVP imposition: one source page per physical side, gutter-shifted."""

    def impose(
        self, pages: Sequence[SourcePage], settings: LayoutSettings
    ) -> SheetPlan:
        """Impose ``pages`` one per physical side.

        :param pages: the project's pages; ``skipped`` ones are dropped
            here rather than by the caller.
        :param settings: paper, gutter, margins and binding edge.
        :returns: a plan whose sheets carry a single-page ``Side`` each,
            and no signatures -- there is nothing gathered to group.
        """
        # An inserted blank becomes the same ``None`` slot the padding
        # filler uses. It carries no file to open -- export raised
        # FileNotFoundError on its empty path -- and it is sized to the
        # PAPER, so measuring it as an ordinary page made it the widest
        # thing in the document and shrank every real page to fit it.
        active = [None if is_blank_page(p) else p for p in pages if not p.skipped]

        active = _lead_for_recto(active, settings)

        slots, _padded = _pad_to_even(active)

        warnings: list[LayoutWarning] = []
        grain = grain_warning(settings)
        if grain is not None:
            warnings.append(grain)
        sheets: list[Sheet] = []
        # One scale for every page, so text does not change size mid-book.
        scale = document_scale(active, settings)
        for sheet_index in range(0, len(slots), 2):
            front_slot = slots[sheet_index]
            back_slot = slots[sheet_index + 1] if sheet_index + 1 < len(slots) else None

            front = _place_page(
                front_slot, sheet_index, settings, sheet_index // 2, warnings, scale
            )
            back = (
                _place_page(
                    back_slot, sheet_index + 1, settings, sheet_index // 2, warnings, scale
                )
                if sheet_index + 1 < len(slots)
                else None
            )
            # One leaf per physical side under gutter shift -- but still a
            # ``Side``, not a bare ``OutputPage``. Every consumer downstream
            # of the imposer iterates ``side.pages``, and a 1-up sheet is
            # just the degenerate case of that, not a separate shape.
            # The gutter alternates between recto and verso, so the
            # fore-edge -- the only vertical edge that is ever cut --
            # alternates with it. A cut fixed to one side of the sheet
            # would fall on the SPINE for every other leaf.
            paper_w, paper_h = settings.paper
            front_fore = "right" if settings.binding_edge == "left" else "left"
            back_fore = "left" if front_fore == "right" else "right"
            front_cuts = cut_lines(paper_w, paper_h, settings.trim_pt, (front_fore,))
            back_cuts = cut_lines(paper_w, paper_h, settings.trim_pt, (back_fore,))
            sheets.append(
                Sheet(
                    index=sheet_index // 2,
                    front=Side(pages=(front,), marks=front_cuts),
                    back=(
                        None
                        if back is None
                        else Side(pages=(back,), marks=back_cuts)
                    ),
                )
            )

        return SheetPlan(sheets=sheets, paper_pt=settings.paper, warnings=warnings)


# ======================================================================
# Folio saddle-stitch imposition
# ======================================================================


def cell_geometry(paper: tuple[float, float]) -> tuple[Cell, Cell]:
    """Split a sheet at its vertical centreline into the two folio cells.

    For letter landscape (792 x 612) this returns ``(0, 0, 396, 612)`` and
    ``(396, 0, 792, 612)`` -- matching the vault recipe's recorded
    ``1 0 0 1 0 0 cm`` / ``1 0 0 1 396 0 cm``.

    :param paper: the sheet size as ``(width, height)`` in points.
    :returns: ``(left_cell, right_cell)``, each an
        :data:`~deckle.core.layout.Cell`.
    """
    paper_w, paper_h = paper
    fold_x = paper_w / 2.0
    return (0.0, 0.0, fold_x, paper_h), (fold_x, 0.0, paper_w, paper_h)


def _ceil4_total(n: int) -> int:
    """The smallest multiple of 4 that is ``>= n`` (``0`` when ``n <= 0``)."""
    if n <= 0:
        return 0
    remainder = n % 4
    return n if remainder == 0 else n + (4 - remainder)


def _pad_to_slots(active: list[SourcePage]) -> tuple[list[SourcePage | None], int]:
    """Pad ``active`` to a multiple of 4 in exactly one pass.

    Returns the padded slot list and the number of blank slots appended --
    never a second padding pass, per the predecessor script's defect 2.
    """
    slots: list[SourcePage | None] = list(active)
    target = _ceil4_total(len(slots))
    blank_count = target - len(slots)
    slots.extend([None] * blank_count)
    return slots, blank_count


def _signature_sheet_groups(
    sheet_count: int,
    sheets_per_signature: int,
    blank_mode: str,
    lengths: Sequence[int] | None = None,
) -> list[tuple[int, ...]]:
    """Group sheet indices ``0..sheet_count-1`` into signatures.

    ``"end"`` reuses ``split_signatures`` directly -- contiguous groups with
    the whole remainder in the final group. ``"balanced"`` keeps the same
    number of groups but distributes the remainder across the *front*
    groups, so the tail groups -- which is where the padding blanks land,
    since sheets are always assigned to groups in ascending order -- are
    never more than one sheet thinner than their neighbours.
    """
    if sheet_count <= 0:
        return []
    if lengths:
        # An explicit list wins over both of the other two: they
        # describe how to DERIVE a grouping, and the user has
        # stated one instead.
        return split_signatures_at(sheet_count, lengths)
    if blank_mode != "balanced":
        return split_signatures(sheet_count, sheets_per_signature)

    n_groups = -(-sheet_count // sheets_per_signature)  # ceil division
    base = sheet_count // n_groups
    extra = sheet_count % n_groups
    sizes = [base + 1] * extra + [base] * (n_groups - extra)

    groups: list[tuple[int, ...]] = []
    start = 0
    for size in sizes:
        groups.append(tuple(range(start, start + size)))
        start += size
    return groups


def _creep_advisory(warnings: list[LayoutWarning], settings: LayoutSettings) -> None:
    """A never-applied advisory: predicted fore-edge creep, and the remedy.

    The **only** function in this module permitted to reference
    ``paper_thickness_pt`` -- enforced by an AST test in
    ``tests/test_layout_saddle.py``. Creep is measured and reported, never
    compensated in placement geometry: no ``Placement`` this module emits
    may differ because of this value.
    """
    if settings.paper_thickness_pt <= 0.0:
        return
    predicted_trim = settings.paper_thickness_pt * settings.sheets_per_signature
    remedy_sheets = max(1, settings.sheets_per_signature // 2)
    warnings.append(
        LayoutWarning(
            sheet_index=0,
            kind="creep_advisory",
            detail=(
                f"predicted fore-edge creep of {predicted_trim:.2f}pt over "
                f"{settings.sheets_per_signature} sheets per signature; "
                f"reduce to {remedy_sheets} sheets per signature to shrink it"
            ),
        )
    )


class SaddleStitchStrategy:
    """Folio 2-up imposition: two source pages per physical sheet side.

    All configuration arrives via ``settings`` -- this class takes no
    constructor arguments and its ``impose`` signature is character-
    identical to ``GutterShiftStrategy.impose``, per the ``LayoutStrategy``
    seam.

    The spine of a leaf is a function of **which cell it sits in**, not of
    output-page parity: the left cell's spine is on its right edge (the
    fold), the right cell's spine is on its left edge -- on both the front
    and the back of the sheet. Consequently ``settings.binding_edge``
    changes meaning under folio: it no longer selects which side of a page
    gets the gutter, it selects **reading direction** -- ``"left"`` is
    left-bound / LTR, ``"right"`` is right-bound / RTL -- which mirrors
    which folio cell (not which position in the print-order pair) each
    source slot lands in.
    """

    def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan:
        """Impose ``pages`` as folio signatures, two leaves per sheet side.

        :param pages: the project's pages; ``skipped`` ones are dropped
            here. The list is padded to a multiple of four in a single
            pass, and the padding is reported as a ``signature_padding``
            warning rather than done silently.
        :param settings: paper, margins, ``sheets_per_signature``,
            ``blank_mode``, ``sewing_stations`` and ``binding_edge`` (which
            here means reading direction).
        :returns: a plan whose sheets carry two-page ``Side``\\ s, plus fold
            lines, sewing stations and signature-order marks, and a
            populated ``signatures`` tuple.
        :raises AssertionError: if the signature grouping does not cover
            every sheet exactly once in binding order, or the per-signature
            slot counts do not reconstruct the padded page list. These are
            verified rather than trusted: a grouping bug is invisible until
            a book is folded, sewn and out of order.

        Portrait paper is a warning, never a refusal -- Deckle advises and
        proceeds rather than overriding the caller's paper choice.
        """
        # An inserted blank becomes the same ``None`` slot the padding
        # filler uses. It carries no file to open -- export raised
        # FileNotFoundError on its empty path -- and it is sized to the
        # PAPER, so measuring it as an ordinary page made it the widest
        # thing in the document and shrank every real page to fit it.
        active = [None if is_blank_page(p) else p for p in pages if not p.skipped]
        active = _lead_for_recto(active, settings)
        warnings: list[LayoutWarning] = []
        grain = grain_warning(settings)
        if grain is not None:
            warnings.append(grain)

        paper_w, paper_h = settings.paper
        if paper_h >= paper_w:
            # Portrait paper under folio: warn, never block or silently
            # rotate the paper out from under the caller. See the
            # path-traversal-validation decision-log precedent -- advise,
            # don't refuse.
            warnings.append(
                LayoutWarning(
                    sheet_index=0,
                    kind="sheet_orientation",
                    detail=(
                        'fold_scheme="folio" expects landscape paper wider '
                        "than it is tall; proceeding with the paper as given"
                    ),
                )
            )

        slots, blank_count = _pad_to_slots(active)
        if blank_count > 0:
            warnings.append(
                LayoutWarning(
                    sheet_index=0,
                    kind="signature_padding",
                    detail=f"padded with {blank_count} blank page(s) to complete the final signature",
                )
            )

        sheets_n = len(slots) // 4
        groups = _signature_sheet_groups(
            sheets_n,
            settings.sheets_per_signature,
            settings.blank_mode,
            settings.signature_lengths,
        )

        cells = cell_geometry(settings.paper)
        fold_x = cells[0][2]
        # ONE scale for the whole document, computed against a single cell
        # -- every cell is identical under folio, so any one will do. Never
        # recomputed per leaf: differing scales are exactly the bug the
        # Traveller cover reproduced.
        scale = document_scale(active, settings, cell=cells[0])

        sheets: list[Sheet] = []
        signatures: list[Signature] = []
        # The source slice each signature actually imposed, recorded as it is
        # taken so the concatenation invariant below checks what was used,
        # not a re-derivation of it.
        signature_slices: list[list[SourcePage | None]] = []
        sig_count = len(groups)

        for sig_index, group in enumerate(groups):
            page_offset = group[0] * 4
            page_count = 4 * len(group)
            sig_slots = slots[page_offset : page_offset + page_count]
            signature_slices.append(sig_slots)
            sig_blank_count = sum(1 for s in sig_slots if s is None)

            order = saddle_order(page_count)

            for local_idx, sheet_index in enumerate(group):
                pos = local_idx * 4
                front_a, front_b = order[pos], order[pos + 1]
                back_a, back_b = order[pos + 2], order[pos + 3]

                # `a` is always the print-order-first leaf of its pair and
                # `b` the second, regardless of binding edge -- so the
                # fold-simulator round trip (which knows nothing of
                # binding_edge) still lines up. `binding_edge` instead
                # mirrors which *cell* -- and so which physical side of the
                # sheet -- `a` and `b` land in.
                if settings.binding_edge == "right":
                    cell_a, spine_a = cells[1], "left"
                    cell_b, spine_b = cells[0], "right"
                else:
                    cell_a, spine_a = cells[0], "right"
                    cell_b, spine_b = cells[1], "left"

                is_outermost = local_idx == 0
                is_innermost = local_idx == len(group) - 1

                front_marks: list = [fold_line(paper_h, fold_x)]
                back_marks: list = [fold_line(paper_h, fold_x)]
                # Folio folds down the middle, so each face carries two
                # leaves and BOTH outer edges are fore-edges. The fold
                # itself is never cut -- that is where the book bends.
                folio_cuts = cut_lines(
                    paper_w, paper_h, settings.trim_pt, ("left", "right")
                )
                front_marks.extend(folio_cuts)
                back_marks.extend(folio_cuts)
                if is_innermost:
                    back_marks.extend(
                        sewing_stations(paper_h, fold_x, settings.sewing_stations)
                    )
                if is_outermost:
                    front_marks.append(
                        signature_order_mark(sig_index, sig_count, paper_h, fold_x)
                    )

                front = Side(
                    pages=(
                        _place_page(
                            slots[page_offset + front_a],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_a,
                            spine_side=spine_a,
                        ),
                        _place_page(
                            slots[page_offset + front_b],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_b,
                            spine_side=spine_b,
                        ),
                    ),
                    marks=tuple(front_marks),
                )
                back = Side(
                    pages=(
                        _place_page(
                            slots[page_offset + back_a],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_a,
                            spine_side=spine_a,
                        ),
                        _place_page(
                            slots[page_offset + back_b],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_b,
                            spine_side=spine_b,
                        ),
                    ),
                    marks=tuple(back_marks),
                )
                sheets.append(Sheet(index=sheet_index, front=front, back=back))

            signatures.append(
                Signature(index=sig_index, sheet_indices=group, blank_count=sig_blank_count)
            )

        _creep_advisory(warnings, settings)

        # Invariants, verified rather than trusted -- the SS-03 precedent.
        all_sheet_indices = [i for sig in signatures for i in sig.sheet_indices]
        assert all_sheet_indices == list(range(len(sheets))), (
            "signature sheet_indices must be contiguous, gapless, and cover "
            "every sheet exactly once, in binding order"
        )
        assert all(len(sig_slice) % 4 == 0 for sig_slice in signature_slices), (
            "every signature's slot count must be a multiple of 4"
        )
        assert sum(len(sig_slice) for sig_slice in signature_slices) == len(slots), (
            "signature slot counts must sum to the padded page count"
        )
        concatenated = [slot for sig_slice in signature_slices for slot in sig_slice]
        assert concatenated == slots, (
            "concatenating the signatures' source slices must reproduce the "
            "padded page list in order"
        )

        return SheetPlan(
            sheets=sheets,
            paper_pt=settings.paper,
            warnings=warnings,
            signatures=tuple(signatures),
        )
