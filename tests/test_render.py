"""Tests for deckle.core.render: preview rasterization, thumbnails, ink bounds."""

from __future__ import annotations

import os
import threading

import pikepdf
import pytest

from deckle.core import export as export_module
from deckle.core import render
from deckle.core.models import (
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)

LETTER = (612.0, 792.0)


def _write_source_pdf(tmp_path, n_pages: int, page_size=(400.0, 600.0)) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    pdf.close()
    return path


def _make_ref(path: str, page_index: int, width_pt=400.0, height_pt=600.0) -> SourceRef:
    return SourceRef(
        path=path,
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _identity_placement() -> Placement:
    return Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)


def _output_page(ref: SourceRef | None, is_filler: bool = False) -> OutputPage:
    return OutputPage(source_ref=ref, placement=_identity_placement(), is_filler=is_filler)


def _one_sheet_plan(front_ref: SourceRef | None, back_ref: SourceRef | None) -> SheetPlan:
    sheet = Sheet(
        index=0,
        front=Side(pages=(_output_page(front_ref),)) if front_ref is not None else None,
        back=Side(pages=(_output_page(back_ref),)) if back_ref is not None else None,
    )
    return SheetPlan(sheets=[sheet], paper_pt=LETTER, warnings=[])


@pytest.fixture(autouse=True)
def _clear_ink_cache():
    render.clear_ink_bbox_cache()
    yield
    render.clear_ink_bbox_cache()


def test_rendered_page_is_a_plain_dataclass():
    page = render.RenderedPage(width=10, height=20, rgba=b"\x00" * (10 * 20 * 4))
    assert page.width == 10
    assert page.height == 20
    assert isinstance(page.rgba, bytes)


def test_render_sheet_routes_through_export(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(src, 0)
    plan = _one_sheet_plan(ref, None)

    calls = []
    real_export = export_module.export

    def spy_export(plan_arg, out_path, sheets=None):
        calls.append((plan_arg, out_path, sheets))
        return real_export(plan_arg, out_path, sheets=sheets)

    monkeypatch.setattr(export_module, "export", spy_export)

    result = render.render_sheet(plan, 0, "front", dpi=72)

    assert len(calls) == 1
    assert calls[0][2] == [0]
    assert result.width > 0
    assert result.height > 0
    assert len(result.rgba) == result.width * result.height * 4


def test_render_sheet_back_side_missing_returns_empty(tmp_path):
    src = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(src, 0)
    plan = _one_sheet_plan(ref, None)

    result = render.render_sheet(plan, 0, "back", dpi=72)

    assert result.width == 0
    assert result.height == 0
    assert result.rgba == b""


def test_render_sheet_front_and_back(tmp_path):
    src = _write_source_pdf(tmp_path, 2)
    front_ref = _make_ref(src, 0)
    back_ref = _make_ref(src, 1)
    plan = _one_sheet_plan(front_ref, back_ref)

    front = render.render_sheet(plan, 0, "front", dpi=72)
    back = render.render_sheet(plan, 0, "back", dpi=72)

    assert front.width > 0 and back.width > 0


# --- Which page of the exported sheet is this face ---------------------
#
# `export` writes one page per face that EXISTS, front first, so a sheet
# with a back and no front puts that back at page 0. `render_sheet` used
# to re-derive that from its own `has_front`/`has_back` pair; it now asks
# `export.face_page_index`. The two tests below are what would have caught
# the two copies drifting: each renders a face that is identifiable by its
# ink and compares it against the same content rendered where its page
# index is not in doubt.


def _write_marked_pdf(tmp_path, n_pages: int, page_size=(400.0, 600.0)) -> str:
    """A source whose pages are told apart by where their ink sits.

    ``_write_source_pdf`` makes blank pages, which rasterise identically,
    so a test built on one cannot tell which page it got back.
    """
    path = os.path.join(str(tmp_path), "marked.pdf")
    pdf = pikepdf.Pdf.new()
    for i in range(n_pages):
        page = pdf.add_blank_page(page_size=page_size)
        page.contents_add(f"q\n0 0 0 rg\n0 {i * 120} 100 50 re\nf\nQ\n".encode())
    pdf.save(path)
    pdf.close()
    return path


def test_a_sheet_with_no_front_renders_its_back_from_page_zero(tmp_path):
    """The one sheet shape where the two implementations could disagree.

    No plan Deckle currently produces reaches it -- ``_pad_to_even`` gives
    every gutter-shift sheet both faces -- so a preview showing the wrong
    face here would go unnoticed. The back's ink must be the ink that
    comes back, which pins the page index to 0 and not 1.
    """
    src = _write_marked_pdf(tmp_path, 2)
    ink = _make_ref(src, 1)

    try:
        back_only = render.render_sheet(_one_sheet_plan(None, ink), 0, "back", dpi=36)
        as_a_front = render.render_sheet(_one_sheet_plan(ink, None), 0, "front", dpi=36)
    finally:
        export_module.clear_sheet_cache()

    assert back_only.rgba != b""
    assert back_only.rgba == as_a_front.rgba


def test_a_back_beside_a_front_renders_from_page_one(tmp_path):
    """The other half of the rule, so the test above cannot be satisfied
    by a ``render_sheet`` that simply always reads page 0."""
    src = _write_marked_pdf(tmp_path, 2)
    front_ref = _make_ref(src, 0)
    back_ref = _make_ref(src, 1)

    try:
        beside_a_front = render.render_sheet(
            _one_sheet_plan(front_ref, back_ref), 0, "back", dpi=36
        )
        alone = render.render_sheet(
            _one_sheet_plan(back_ref, None), 0, "front", dpi=36
        )
    finally:
        export_module.clear_sheet_cache()

    assert beside_a_front.rgba != b""
    assert beside_a_front.rgba == alone.rgba


def test_render_sheet_already_cancelled_returns_promptly(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(src, 0)
    plan = _one_sheet_plan(ref, None)

    called = []
    monkeypatch.setattr(
        export_module,
        "export",
        lambda *a, **k: called.append(True),
    )

    cancel = threading.Event()
    cancel.set()

    result = render.render_sheet(plan, 0, "front", dpi=72, cancel=cancel)

    assert result.width == 0
    assert result.height == 0
    assert not called


def test_thumbnails_returns_explicit_range_only(tmp_path):
    src = _write_source_pdf(tmp_path, 5)
    pages = [
        SourcePage(ref=_make_ref(src, i), rotate_deg=0, skipped=False) for i in range(5)
    ]

    result = render.thumbnails(pages, start=1, count=2, dpi=36)

    assert len(result) == 2
    for page in result:
        assert page.width > 0
        assert page.height > 0


def test_thumbnails_empty_window():
    assert render.thumbnails([], start=0, count=5) == []


def test_ink_bbox_caches_per_source_ref(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(src, 0)

    call_count = {"n": 0}
    real_rasterize = render._rasterize_for_bbox

    def counting_rasterize(r, dpi):
        call_count["n"] += 1
        return real_rasterize(r, dpi)

    monkeypatch.setattr(render, "_rasterize_for_bbox", counting_rasterize)

    render.ink_bbox(ref)
    render.ink_bbox(ref)

    assert call_count["n"] == 1


def test_ink_bbox_blank_page_returns_degenerate_box(tmp_path):
    src = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(src, 0)

    bbox = render.ink_bbox(ref)

    assert bbox == (0.0, 0.0, 0.0, 0.0)


def test_ink_bbox_with_content_is_non_degenerate(tmp_path):
    path = os.path.join(str(tmp_path), "ink.pdf")
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200.0, 200.0))
    # Draw a small black filled rectangle so the page isn't blank.
    content = b"0 0 0 rg 50 50 30 30 re f\n"
    page.Contents = pdf.make_stream(content)
    pdf.save(path)
    pdf.close()

    ref = _make_ref(path, 0, width_pt=200.0, height_pt=200.0)
    bbox = render.ink_bbox(ref, dpi=72)

    x0, y0, x1, y1 = bbox
    assert x1 > x0
    assert y1 > y0
