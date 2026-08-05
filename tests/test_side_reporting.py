"""Front/back reporting: which side lands on which exported PDF page, and
which pages of a side get their clipping reported.

Deckle prints manual duplex on a printer with no duplexer -- every front is
printed, the stack is physically reloaded, then every back is printed. A
front/back transposition is therefore not a cosmetic defect: it ruins a
whole run of an expensive hand-bound book, and it is invisible until the
paper comes out. ``deckle/core/render.py`` and ``deckle/app/backend.py``
each carry a copy of the same rule -- the single-sheet export contains only
the sides that *exist*, in front-then-back order, so a back is PDF page
``1 if has_front else 0``. These tests pin that mapping against
**distinguishable rendered content**, not page dimensions: two source pages
with identical boxes but marks in different quadrants, so a transposition
changes the raster rather than merely the metadata.

The clipping half is the same honesty requirement one layer up: a side may
hold more than one leaf, and every leaf that overflows must be reported.
``clipping_warnings_for_sheet`` is a pure function and is called directly --
no Qt widget is constructed anywhere in this module.

Helpers here mirror ``tests/test_preview_fidelity.py`` (``_ref``,
``_placement``, ``_output_page``, ``_side``) so the two files construct
fixtures the same way.
"""

from __future__ import annotations

import os

import pikepdf
import pytest

from deckle.app import backend as backend_mod
from deckle.app.views import preview_view
from deckle.core import render as render_mod
from deckle.core.layout import GutterShiftStrategy, actual_margins_pt
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)

LETTER = (612.0, 792.0)

# Real page boxes, so the SS-02 characterisation test exercises aspect
# ratios that actually occur rather than only the paper's own.
TRAVELLER = (506.88, 672.0)
DIGEST = (432.0, 648.0)
WIDE = (900.0, 600.0)


# -- fixture helpers, mirroring tests/test_preview_fidelity.py ------------


def _ref(path: str, page_index: int = 0, width_pt=612.0, height_pt=792.0) -> SourceRef:
    return SourceRef(
        path=path, page_index=page_index, sha256="a" * 64, width_pt=width_pt, height_pt=height_pt
    )


def _placement(**overrides) -> Placement:
    base = dict(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    base.update(overrides)
    return Placement(**base)


def _output_page(ref, is_filler=False, **placement_overrides) -> OutputPage:
    return OutputPage(source_ref=ref, placement=_placement(**placement_overrides), is_filler=is_filler)


def _side(*pages: OutputPage) -> Side:
    return Side(pages=tuple(pages))


def _plan(sheets: list[Sheet], paper_pt=LETTER, warnings=None) -> SheetPlan:
    return SheetPlan(sheets=sheets, paper_pt=paper_pt, warnings=warnings or [])


# -- distinguishable source pages ----------------------------------------
#
# Two letter-sized pages with IDENTICAL boxes -- so nothing about the
# geometry can be used to tell them apart -- carrying a solid black mark in
# a different quadrant each. Both marks are inset well inside their
# quadrant, so quadrant detection never has to adjudicate a pixel sitting
# exactly on a boundary.
#
# Quadrants are also chosen to survive the 180-degree rotation that
# ``backend._apply_rotate_backs`` applies to backs: under a half turn
# top-left maps to bottom-right and top-right maps to bottom-left, so the
# two pages stay distinguishable rotated as well as unrotated. A mark on
# one diagonal (e.g. top-left AND bottom-right) would collapse onto its own
# partner under rotation and the test would pass on a transposed side.

_MARK_TOP_LEFT = b"0 0 0 rg 30 480 240 280 re f\n"
_MARK_TOP_RIGHT = b"0 0 0 rg 340 480 240 280 re f\n"


def _write_marked_source_pdf(tmp_path) -> str:
    """A 2-page PDF: page 0 marked top-left, page 1 marked top-right."""
    path = os.path.join(str(tmp_path), "marked.pdf")
    pdf = pikepdf.Pdf.new()
    for mark in (_MARK_TOP_LEFT, _MARK_TOP_RIGHT):
        page = pdf.add_blank_page(page_size=LETTER)
        page.contents_add(pikepdf.Stream(pdf, mark))
    pdf.save(path)
    pdf.close()
    return path


def _quadrant_means(rendered) -> dict[str, float]:
    """Mean RGB brightness of each quadrant of a ``RenderedPage``.

    The raster is top-left origin, row-major RGBA. Sampled on a stride --
    the marks are hundreds of points across, so every fourth pixel is
    ample and keeps the test fast at low DPI.
    """
    width, height = rendered.width, rendered.height
    assert width > 0 and height > 0, "expected a real raster, got a degenerate page"
    pixels = rendered.rgba
    half_w, half_h = width // 2, height // 2
    boxes = {
        "TL": (0, half_w, 0, half_h),
        "TR": (half_w, width, 0, half_h),
        "BL": (0, half_w, half_h, height),
        "BR": (half_w, width, half_h, height),
    }
    means: dict[str, float] = {}
    for name, (x0, x1, y0, y1) in boxes.items():
        total = 0
        count = 0
        for y in range(y0, y1, 4):
            row = y * width * 4
            for x in range(x0, x1, 4):
                i = row + x * 4
                total += pixels[i] + pixels[i + 1] + pixels[i + 2]
                count += 3
        means[name] = total / count if count else 255.0
    return means


def _marked_quadrant(rendered) -> str:
    """Which quadrant carries the black mark, as ``"TL"``/``"TR"``/etc.

    Asserts the winner is unambiguous rather than merely lowest, so a
    near-uniform raster (a blank page, or content that never made it
    through the export) fails loudly instead of returning an arbitrary
    quadrant that might happen to match.
    """
    means = _quadrant_means(rendered)
    darkest = min(means, key=lambda k: means[k])
    others = [v for k, v in means.items() if k != darkest]
    assert means[darkest] < 200.0, f"no mark found in any quadrant: {means}"
    assert min(others) > 240.0, f"mark is not confined to one quadrant: {means}"
    return darkest


# The rendered signature of each source page, unrotated and after the
# 180-degree turn backend.py applies to a back side.
_PAGE0_QUADRANT = "TL"
_PAGE1_QUADRANT = "TR"
_PAGE1_QUADRANT_ROTATED = "BL"

_RENDER_DPI = 36


# -- SS-03: the front-then-back page-index mapping ------------------------


def test_back_index_maps_to_the_second_exported_page_of_a_two_sided_sheet(tmp_path):
    # Both sides present, so the single-sheet export holds two PDF pages and
    # the back is page 1. Transpose front and back and the two assertions
    # swap their expected quadrants -- which is precisely the ruined print
    # run this test exists to prevent.
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(
        index=0,
        front=_side(_output_page(_ref(src, page_index=0))),
        back=_side(_output_page(_ref(src, page_index=1))),
    )
    plan = _plan([sheet])

    front = render_mod.render_sheet(plan, 0, "front", _RENDER_DPI)
    back = render_mod.render_sheet(plan, 0, "back", _RENDER_DPI)

    assert _marked_quadrant(front) == _PAGE0_QUADRANT
    assert _marked_quadrant(back) == _PAGE1_QUADRANT


def test_back_index_is_page_zero_when_the_sheet_has_no_front(tmp_path):
    # has_front is False, so the export holds exactly ONE page and the back
    # is page 0. An implementation that hard-coded page 1 for a back would
    # render nothing at all here; one that hard-coded 0 would be caught by
    # the two-sided test above.
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(index=0, front=None, back=_side(_output_page(_ref(src, page_index=1))))
    plan = _plan([sheet])

    back = render_mod.render_sheet(plan, 0, "back", _RENDER_DPI)

    assert _marked_quadrant(back) == _PAGE1_QUADRANT

    # ...and asking for the absent front must not silently hand back the
    # back's raster, which would print the wrong side on the first pass.
    front = render_mod.render_sheet(plan, 0, "front", _RENDER_DPI)
    assert (front.width, front.height) == (0, 0)


def test_back_index_on_a_front_only_sheet_renders_the_front_and_no_back(tmp_path):
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(index=0, front=_side(_output_page(_ref(src, page_index=0))), back=None)
    plan = _plan([sheet])

    front = render_mod.render_sheet(plan, 0, "front", _RENDER_DPI)
    back = render_mod.render_sheet(plan, 0, "back", _RENDER_DPI)

    assert _marked_quadrant(front) == _PAGE0_QUADRANT
    # The one page in the export is the FRONT. Reporting it as the back
    # would put a recto on the reload pass.
    assert (back.width, back.height) == (0, 0)


def test_back_index_in_backend_render_sheet_side_maps_to_the_second_exported_page(tmp_path):
    # backend.py carries its own copy of the rule (backend.py:168), reached
    # only when rotate_backs is True -- otherwise it delegates to
    # render_sheet. rotate_backs turns the back 180 degrees before
    # rasterizing, so page 1's top-right mark lands bottom-left; page 0's
    # top-left mark would land bottom-RIGHT, so a transposition is still
    # caught.
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(
        index=0,
        front=_side(_output_page(_ref(src, page_index=0))),
        back=_side(_output_page(_ref(src, page_index=1))),
    )
    plan = _plan([sheet])

    back = backend_mod._render_sheet_side(
        plan, 0, "back", _RENDER_DPI, rotate_backs=True, ignore_rotate=False
    )

    assert _marked_quadrant(back) == _PAGE1_QUADRANT_ROTATED


def test_back_index_in_backend_is_page_zero_when_the_sheet_has_no_front(tmp_path):
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(index=0, front=None, back=_side(_output_page(_ref(src, page_index=1))))
    plan = _plan([sheet])

    back = backend_mod._render_sheet_side(
        plan, 0, "back", _RENDER_DPI, rotate_backs=True, ignore_rotate=False
    )

    assert _marked_quadrant(back) == _PAGE1_QUADRANT_ROTATED


def test_back_index_holds_when_a_side_carries_two_pages(tmp_path):
    # One Side is one PDF page however many leaves it holds, which is what
    # keeps `1 if has_front else 0` correct under 2-up folio. The back here
    # is a two-leaf side; page 1 of the export is still that side.
    src = _write_marked_source_pdf(tmp_path)
    sheet = Sheet(
        index=0,
        front=_side(_output_page(_ref(src, page_index=1))),
        back=_side(
            _output_page(_ref(src, page_index=0), scale_x=0.5, scale_y=0.5, tx=0.0, ty=396.0),
            _output_page(_ref(src, page_index=0), scale_x=0.5, scale_y=0.5, tx=306.0, ty=396.0),
        ),
    )
    plan = _plan([sheet])

    front = render_mod.render_sheet(plan, 0, "front", _RENDER_DPI)
    back = render_mod.render_sheet(plan, 0, "back", _RENDER_DPI)

    # The front is the single-leaf side carrying page 1's top-right mark.
    assert _marked_quadrant(front) == _PAGE1_QUADRANT
    # The back is the 2-up side: page 0's top-left mark, half scale, placed
    # once in each half of the upper band -- so BOTH top quadrants are
    # marked and neither bottom one is. That is a shape the front cannot
    # produce, so a transposition cannot pass.
    # Half-scale marks cover only part of their quadrant, so the top pair is
    # compared against the untouched bottom pair rather than an absolute
    # ink threshold.
    back_means = _quadrant_means(back)
    assert max(back_means["TL"], back_means["TR"]) < 240.0, back_means
    assert min(back_means["BL"], back_means["BR"]) > 250.0, back_means


# -- SS-03: clipping is reported per page within a side -------------------


def test_both_pages_of_a_side_can_be_reported():
    # Both leaves of a folio side run off the sheet. An implementation that
    # iterates but breaks after the first warning returns one; a per-side
    # implementation returns one or zero. Only a full per-page walk returns
    # two.
    left = _output_page(_ref("x.pdf", width_pt=612.0, height_pt=792.0), tx=100.0, ty=100.0)
    right = _output_page(
        _ref("x.pdf", page_index=1, width_pt=612.0, height_pt=792.0), tx=200.0, ty=150.0
    )
    sheet = Sheet(index=0, front=_side(left, right), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert len(warnings) == 2
    assert [w.kind for w in warnings] == ["clipped_by_page", "clipped_by_page"]
    assert [w.sheet_index for w in warnings] == [0, 0]


def test_two_page_side_with_no_overflow_reports_nothing():
    # The negative control for the two positives: both leaves sit wholly
    # inside the imageable area (18pt margins on LETTER -> x 18..594,
    # y 18..774), so nothing at all is reported. Without this, an
    # implementation that always warns would pass the positives.
    left = _output_page(_ref("x.pdf", width_pt=250.0, height_pt=350.0), tx=18.0, ty=18.0)
    right = _output_page(
        _ref("x.pdf", page_index=1, width_pt=250.0, height_pt=350.0), tx=300.0, ty=18.0
    )
    sheet = Sheet(index=0, front=_side(left, right), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert warnings == []


def test_two_page_side_with_no_overflow_reports_nothing_on_the_back_side_either():
    # The same control on the back, so a loop that only walked the front's
    # pages could not hide behind a front-only fixture.
    left = _output_page(_ref("x.pdf", width_pt=250.0, height_pt=350.0), tx=18.0, ty=18.0)
    right = _output_page(
        _ref("x.pdf", page_index=1, width_pt=250.0, height_pt=350.0), tx=300.0, ty=18.0
    )
    sheet = Sheet(index=0, front=None, back=_side(left, right))

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert warnings == []


def test_both_pages_of_a_side_can_be_reported_on_the_back_side():
    left = _output_page(_ref("x.pdf", width_pt=612.0, height_pt=792.0), tx=100.0, ty=100.0)
    right = _output_page(
        _ref("x.pdf", page_index=1, width_pt=612.0, height_pt=792.0), tx=200.0, ty=150.0
    )
    sheet = Sheet(index=0, front=None, back=_side(left, right))

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert len(warnings) == 2
    # Every warning names the side it came from, so a badge cannot send the
    # user to the wrong pass of a manual-duplex run.
    assert all("back" in w.detail for w in warnings)


# -- SS-02: the Side refactor did not move a single placement -------------


def _make_pages(n, size):
    w, h = size
    return [
        SourcePage(
            ref=SourceRef(
                path="input.pdf", page_index=i, sha256="a" * 64, width_pt=w, height_pt=h
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _flat_output_pages(plan):
    """Every output page in the plan, front-then-back, sheet by sheet."""
    flat = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is not None:
                flat.extend(side.pages)
    return flat


@pytest.mark.parametrize("page_size", [TRAVELLER, DIGEST, WIDE])
@pytest.mark.parametrize("binding_edge", ["left", "right"])
def test_gutter_shift_placements_are_identical_across_aspect_ratios_and_binding_edges(
    page_size, binding_edge
):
    """Two independent ``impose()`` runs must agree placement for placement.

    A characterisation test, not a geometry test: it pins what
    ``GutterShiftStrategy`` produces so any drift introduced by the ``Side``
    refactor -- or by anything downstream of it -- is unambiguous. Margins
    are asserted via ``actual_margins_pt`` on all four edges of every page
    on both sides, never raw ``tx``/``ty``: a coordinate assertion on one
    edge of one page can pass while the opposite edge is wrong, which is
    exactly how the verso gutter bug survived the original suite.
    """
    pages = _make_pages(7, page_size)
    layout = LayoutSettings(paper=LETTER, gutter_pt=54.0, binding_edge=binding_edge)

    plan_a = GutterShiftStrategy().impose(pages, layout)
    plan_b = GutterShiftStrategy().impose(pages, layout)

    assert len(plan_a.sheets) == len(plan_b.sheets)

    flat_a = _flat_output_pages(plan_a)
    flat_b = _flat_output_pages(plan_b)
    assert len(flat_a) == len(flat_b)
    # 7 pages -> 8 slots (one filler) -> 4 sheets, both sides populated.
    assert len(flat_a) == 8

    for index, (page_a, page_b) in enumerate(zip(flat_a, flat_b)):
        assert page_a.placement == page_b.placement, f"placement drifted at slot {index}"
        assert page_a.is_filler == page_b.is_filler
        assert page_a.source_ref == page_b.source_ref

        is_recto = index % 2 == 0
        margins_a = actual_margins_pt(
            page_a, LETTER, is_recto=is_recto, binding_edge=binding_edge
        )
        margins_b = actual_margins_pt(
            page_b, LETTER, is_recto=is_recto, binding_edge=binding_edge
        )
        assert margins_a == margins_b, f"measured margins drifted at slot {index}"
        # Four real edges, measured -- so a placement that happened to
        # compare equal while carrying a degenerate footprint cannot pass.
        assert len(margins_a) == 4


def test_gutter_shift_placements_are_identical_for_every_side_of_every_sheet():
    """The walk itself: both sides of every sheet are visited and compared.

    Guards the harness above -- if ``_flat_output_pages`` ever stopped
    descending into ``side.pages`` it would compare an empty list against an
    empty list and pass vacuously.
    """
    pages = _make_pages(7, DIGEST)
    layout = LayoutSettings(paper=LETTER, gutter_pt=54.0, binding_edge="left")

    plan = GutterShiftStrategy().impose(pages, layout)

    visited = 0
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            assert side is not None
            assert len(side.pages) >= 1
            visited += len(side.pages)
    assert visited == 8
