"""Cut lines reaching the imposed sheets, and the exported PDF."""

from __future__ import annotations

import os

import pikepdf
import pytest

from deckle.core.export import export
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

LETTER = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)


def _pages(n, w=400.0, h=600.0):
    return [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=w, height_pt=h),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _cuts(side):
    return [m for m in side.marks if m.kind == "cut_line"]


def _vertical_xs(side):
    return sorted(m.x0 for m in _cuts(side) if m.x0 == m.x1)


def test_no_trim_means_no_cut_lines_anywhere():
    settings = LayoutSettings(paper=LETTER, gutter_pt=36.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(_pages(4), settings)

    for sheet in plan.sheets:
        assert _cuts(sheet.front) == []
        assert _cuts(sheet.back) == []


def test_gutter_shift_trims_the_edge_opposite_the_spine_on_each_face():
    """The gutter alternates between recto and verso, so the fore-edge
    alternates with it. A cut line fixed to one side of the sheet would be
    on the spine for every other leaf."""
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="left", trim_pt=18.0
    )
    plan = GutterShiftStrategy().impose(_pages(4), settings)
    sheet = plan.sheets[0]

    # Front binds left, so its fore-edge -- and its cut -- is on the right.
    assert _vertical_xs(sheet.front) == [612.0 - 18.0]
    # The back mirrors it.
    assert _vertical_xs(sheet.back) == [18.0]


def test_gutter_shift_honours_a_right_hand_binding():
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="right", trim_pt=18.0
    )
    plan = GutterShiftStrategy().impose(_pages(4), settings)
    sheet = plan.sheets[0]

    assert _vertical_xs(sheet.front) == [18.0]
    assert _vertical_xs(sheet.back) == [612.0 - 18.0]


def test_folio_trims_both_outer_edges():
    """Two leaves side by side with the fold between them: both outer
    edges are fore-edges."""
    settings = LayoutSettings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=36.0,
        binding_edge="left",
        fold_scheme="folio",
        trim_pt=18.0,
    )
    plan = SaddleStitchStrategy().impose(_pages(8), settings)

    assert _vertical_xs(plan.sheets[0].front) == [18.0, 792.0 - 18.0]


def test_folio_never_cuts_along_its_own_fold():
    settings = LayoutSettings(
        paper=LETTER_LANDSCAPE,
        gutter_pt=36.0,
        binding_edge="left",
        fold_scheme="folio",
        trim_pt=18.0,
    )
    plan = SaddleStitchStrategy().impose(_pages(8), settings)

    assert 792.0 / 2 not in _vertical_xs(plan.sheets[0].front)


def test_head_and_tail_are_trimmed_on_every_face():
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="left", trim_pt=18.0
    )
    plan = GutterShiftStrategy().impose(_pages(4), settings)

    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            ys = sorted(m.y0 for m in _cuts(side) if m.y0 == m.y1)
            assert ys == [18.0, 792.0 - 18.0]


def test_cut_lines_reach_the_exported_pdf(tmp_path):
    src = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(4):
        pdf.add_blank_page(page_size=(400.0, 600.0))
    pdf.save(src)
    pdf.close()
    pages = [
        SourcePage(
            ref=SourceRef(path=src, page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(4)
    ]
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="left", trim_pt=18.0
    )
    plan = GutterShiftStrategy().impose(pages, settings)
    out = os.path.join(str(tmp_path), "out.pdf")

    export(plan, out)

    with pikepdf.open(out) as doc:
        contents = doc.pages[0].obj.get("/Contents")
        raw = (
            b"".join(s.read_bytes() for s in contents)
            if isinstance(contents, pikepdf.Array)
            else contents.read_bytes()
        )
    # The right-hand cut, drawn full height.
    assert b"594" in raw, "no cut line at the fore-edge in the exported page"


def test_a_trim_that_would_consume_the_sheet_is_refused():
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="left", trim_pt=400.0
    )

    with pytest.raises(ValueError):
        GutterShiftStrategy().impose(_pages(4), settings)
