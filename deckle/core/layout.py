"""The Imposer: pure gutter-shift layout math.

Given source pages and ``LayoutSettings``, produce a ``SheetPlan``. This
module must stay free of file, network, and print I/O -- see
``tests/test_core_purity.py``. ``LayoutStrategy`` is the v2 seam for
signature/saddle-stitch imposition; ``GutterShiftStrategy`` is the only
implementation shipped in the MVP, and every configuration knob arrives via
``settings`` rather than a narrowed, gutter-specific signature.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from deckle.core.models import (
    LayoutSettings,
    LayoutWarning,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    SourcePage,
)


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
    ) -> SheetPlan: ...


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


def _source_dims(page: SourcePage) -> tuple[float, float]:
    """The upright (unrotated-by-us) width/height of a source page's content."""
    width = page.ref.width_pt
    height = page.ref.height_pt
    if page.rotate_deg in (90, 270):
        width, height = height, width
    return width, height


def _place_page(
    slot: SourcePage | None,
    output_index: int,
    settings: LayoutSettings,
    sheet_index: int,
    warnings: list[LayoutWarning],
) -> OutputPage:
    paper_w, paper_h = settings.paper
    is_recto = _is_recto(output_index)

    if slot is None:
        placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
        return OutputPage(source_ref=None, placement=placement, is_filler=True)

    src_w, src_h = _source_dims(slot)
    rotate_deg = 0

    # Landscape content inside a portrait document (or vice versa) under
    # the "rotate" policy: rotate the content to match document orientation
    # and warn, rather than silently clipping or shrinking it.
    paper_is_portrait = paper_h >= paper_w
    page_is_landscape = src_w > src_h
    if settings.landscape_policy == "rotate" and paper_is_portrait and page_is_landscape:
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

    box_w = paper_w - gutter - outer
    box_h = paper_h - top - bottom

    if box_w <= 0.0 or box_h <= 0.0:
        # Margins consume the whole sheet. Warn and fall back to the bare
        # paper rather than producing a negative-size box and nonsense scale.
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
        box_w, box_h = paper_w, paper_h

    # ------------------------------------------------------------------
    # Scale. Exactly two modes:
    #   fit         -- largest scale fitting BOTH box dimensions; never
    #                  overflows, so it is the safe default.
    #   fill_height -- fill the box height exactly; width falls where it
    #                  falls and may overflow, which the clipping detector
    #                  then reports rather than the layout hiding it.
    # ------------------------------------------------------------------
    if settings.scale_mode == "fill_height":
        scale = box_h / src_h
    else:  # "fit"
        scale = min(box_w / src_w, box_h / src_h)

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

    inner_actual = gutter + slack_w / 2.0
    bottom_actual = bottom + slack_h / 2.0

    gutter_on_left = _gutter_side_is_left(is_recto, settings.binding_edge)
    tx = inner_actual if gutter_on_left else paper_w - inner_actual - scaled_w
    ty = bottom_actual

    placement = Placement(
        scale_x=scale,
        scale_y=scale,
        tx=tx,
        ty=ty,
        rotate_deg=rotate_deg,
    )
    return OutputPage(source_ref=slot.ref, placement=placement, is_filler=False)


def actual_margins_pt(
    output_page: OutputPage,
    paper: tuple[float, float],
    *,
    is_recto: bool,
    binding_edge: str,
) -> tuple[float, float, float, float]:
    """The margins a placed page actually ends up with: ``(inner, outer, top, bottom)``.

    "Inner" is the spine side, which is the left edge on a recto under a left
    binding and the right edge on a verso. Negative values mean the content
    overflows that edge. Pure geometry over the emitted ``Placement``, so
    tests and the UI measure the same numbers the exporter will use rather
    than re-deriving them from settings.
    """
    paper_w, paper_h = paper
    p = output_page.placement
    ref = output_page.source_ref
    if ref is None:
        return (0.0, 0.0, 0.0, 0.0)

    src_w, src_h = ref.width_pt, ref.height_pt
    if p.rotate_deg in (90, 270):
        src_w, src_h = src_h, src_w
    scaled_w = src_w * p.scale_x
    scaled_h = src_h * p.scale_y

    left = p.tx
    right = paper_w - p.tx - scaled_w
    bottom = p.ty
    top = paper_h - p.ty - scaled_h

    gutter_on_left = _gutter_side_is_left(is_recto, binding_edge)
    inner, outer = (left, right) if gutter_on_left else (right, left)
    return (inner, outer, top, bottom)


class GutterShiftStrategy:
    """MVP imposition: one source page per physical side, gutter-shifted."""

    def impose(
        self, pages: Sequence[SourcePage], settings: LayoutSettings
    ) -> SheetPlan:
        active = [p for p in pages if not p.skipped]

        # start_on_recto: the first content page always lands at output
        # index 0, which is a recto by definition, so no leading filler is
        # needed to honor it in this single-page-per-side MVP layout.
        _ = settings.start_on_recto  # documented no-op branch, see above

        slots, _padded = _pad_to_even(active)

        warnings: list[LayoutWarning] = []
        sheets: list[Sheet] = []
        for sheet_index in range(0, len(slots), 2):
            front_slot = slots[sheet_index]
            back_slot = slots[sheet_index + 1] if sheet_index + 1 < len(slots) else None

            front = _place_page(front_slot, sheet_index, settings, sheet_index // 2, warnings)
            back = (
                _place_page(back_slot, sheet_index + 1, settings, sheet_index // 2, warnings)
                if sheet_index + 1 < len(slots)
                else None
            )
            sheets.append(Sheet(index=sheet_index // 2, front=front, back=back))

        return SheetPlan(sheets=sheets, paper_pt=settings.paper, warnings=warnings)
