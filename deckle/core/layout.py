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

    source_aspect = src_w / src_h

    # The content box: the gutter eats the spine edge, `margin_pt` eats the
    # other three. "fit height" means fit the *content box* height, not the
    # paper height -- otherwise a margin would be silently ignored.
    margin = settings.margin_pt
    avail_h = max(1e-6, paper_h - 2.0 * margin)

    if settings.scale_mode == "fixed_gutter":
        gutter = settings.gutter_pt
        avail_w = max(1e-6, paper_w - gutter - margin)
        scale = min(avail_w / src_w, avail_h / src_h)
    else:  # fit_height (default)
        scale = avail_h / src_h
        scaled_w = src_w * scale
        # Whatever is left after the fore-edge margin becomes the gutter.
        gutter = max(0.0, paper_w - margin - scaled_w)

    scaled_w = src_w * scale
    scaled_h = src_h * scale

    gutter_on_left = _gutter_side_is_left(is_recto, settings.binding_edge)
    # The reserved gutter always sits adjacent to the binding edge, and any
    # slack left over lands on the fore-edge. Writing the verso as a bare
    # tx=0.0 silently assumes the scaled content exactly fills
    # ``paper_w - gutter`` -- true in fit_height (where the gutter IS the
    # leftover) but false in fixed_gutter whenever height is the binding
    # constraint. That asymmetry put the spine gutter on the wrong side of
    # the verso and read as the binding edge flipping between modes.
    # This form reduces to 0.0 in fit_height, so both modes share one rule.
    tx = gutter if gutter_on_left else paper_w - gutter - scaled_w
    # Centre within the margin box, not the sheet, so head and tail margins
    # are actually honoured rather than averaged away.
    ty = margin + (avail_h - scaled_h) / 2.0

    placement = Placement(
        scale_x=scale,
        scale_y=scale,
        tx=tx,
        ty=ty,
        rotate_deg=rotate_deg,
    )
    return OutputPage(source_ref=slot.ref, placement=placement, is_filler=False)


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
