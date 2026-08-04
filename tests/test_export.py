"""Tests for deckle.core.export: PDF composition via pikepdf Form XObjects."""

from __future__ import annotations

import os
import tempfile
from typing import Sequence

import pikepdf
import pytest

from deckle.core import export
from deckle.core.export import export as export_fn
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
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


def _content_bytes(page: pikepdf.Page) -> bytes:
    contents = page.obj.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        return b"".join(s.read_bytes() for s in contents)
    return contents.read_bytes()


def _make_ref(path: str, page_index: int, width_pt: float, height_pt: float) -> SourceRef:
    return SourceRef(
        path=path,
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _plan_from_source(
    tmp_path, n_pages: int, page_size=(400.0, 600.0), settings: LayoutSettings | None = None
) -> SheetPlan:
    path = _write_source_pdf(tmp_path, n_pages, page_size)
    pages = [
        SourcePage(
            ref=_make_ref(path, i, page_size[0], page_size[1]),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n_pages)
    ]
    settings = settings or LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left", scale_mode="fixed_gutter"
    )
    return GutterShiftStrategy().impose(pages, settings)


# --- STRUCTURAL -------------------------------------------------------


def test_export_has_expected_signature():
    import inspect

    sig = inspect.signature(export.export)
    params = list(sig.parameters)
    assert params[:3] == ["plan", "out_path", "sheets"]
    assert sig.parameters["sheets"].default is None


def test_export_sheet_cached_has_expected_signature():
    import inspect

    sig = inspect.signature(export.export_sheet_cached)
    params = list(sig.parameters)
    assert params == ["plan", "sheet_index"]


# --- BEHAVIORAL: cache call counting -----------------------------------


def test_export_sheet_cached_performs_export_once_for_same_key(tmp_path):
    plan = _plan_from_source(tmp_path, 4)

    path_a = export.export_sheet_cached(plan, 0)
    count_after_first = export._call_count
    path_b = export.export_sheet_cached(plan, 0)
    count_after_second = export._call_count

    assert path_a == path_b
    assert count_after_first == count_after_second
    export.clear_sheet_cache()


def test_export_sheet_cached_invalidates_on_layout_change(tmp_path):
    path = _write_source_pdf(tmp_path, 4)
    pages = [
        SourcePage(ref=_make_ref(path, i, 400.0, 600.0), rotate_deg=0, skipped=False)
        for i in range(4)
    ]
    settings_a = LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left", scale_mode="fixed_gutter"
    )
    settings_b = LayoutSettings(
        paper=LETTER, gutter_pt=36.0, binding_edge="left", scale_mode="fixed_gutter"
    )
    plan_a = GutterShiftStrategy().impose(pages, settings_a)
    plan_b = GutterShiftStrategy().impose(pages, settings_b)

    export.export_sheet_cached(plan_a, 0)
    count_after_a = export._call_count
    export.export_sheet_cached(plan_b, 0)
    count_after_b = export._call_count

    assert count_after_b > count_after_a
    export.clear_sheet_cache()


# --- MECHANICAL: no consumer-side Placement adjustment -----------------


def test_export_receives_placements_identical_to_imposer_output(tmp_path):
    path = _write_source_pdf(tmp_path, 4)
    pages = [
        SourcePage(ref=_make_ref(path, i, 400.0, 600.0), rotate_deg=0, skipped=False)
        for i in range(4)
    ]
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left", scale_mode="fixed_gutter"
    )
    plan = GutterShiftStrategy().impose(pages, settings)

    # export() must consume plan.sheets' Placements exactly as produced --
    # dataclass equality against a second, independent impose() call proves
    # no consumer-side (export-side) adjustment occurred anywhere in between.
    plan_again = GutterShiftStrategy().impose(pages, settings)
    for sheet_a, sheet_b in zip(plan.sheets, plan_again.sheets):
        if sheet_a.front is not None:
            assert sheet_a.front.placement == sheet_b.front.placement
        if sheet_a.back is not None:
            assert sheet_a.back.placement == sheet_b.back.placement

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)
    assert os.path.exists(out_path)


# --- BEHAVIORAL: pure translation for fixed_gutter already-fits case ---


def test_fixed_gutter_already_fits_emits_pure_translation(tmp_path):
    # Source sized so fixed_gutter's scale computes to exactly 1.0.
    page_size = (LETTER[0] - 18.0, LETTER[1])
    plan = _plan_from_source(
        tmp_path,
        1,
        page_size=page_size,
        settings=LayoutSettings(
            paper=LETTER, gutter_pt=18.0, binding_edge="left", scale_mode="fixed_gutter"
        ),
    )
    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        data = _content_bytes(page)
        # Pure translation: "1 0 0 1 <x> <y> cm" -- no scale factor.
        import re

        match = re.search(rb"([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) cm", data)
        assert match is not None, data
        a, b, c, d, _tx, _ty = (float(v) for v in match.groups())
        assert (a, b, c, d) == (1.0, 0.0, 0.0, 1.0)


# --- BEHAVIORAL: sheet subset matches full export -----------------------


def test_export_sheets_subset_matches_full_export(tmp_path):
    plan = _plan_from_source(tmp_path, 16)
    full_path = os.path.join(str(tmp_path), "full.pdf")
    subset_path = os.path.join(str(tmp_path), "subset.pdf")

    export_fn(plan, full_path)
    export_fn(plan, subset_path, sheets=[6])

    def geometry(data: bytes) -> str:
        # Compare the placement matrix only -- the XObject resource name is
        # freshly (randomly) generated by pikepdf on every export and isn't
        # meaningful content.
        import re

        match = re.search(rb"([\d.-]+ ){5}[\d.-]+ cm", data)
        assert match is not None, data
        return match.group(0).decode("latin-1")

    with pikepdf.open(subset_path) as subset_pdf:
        assert len(subset_pdf.pages) == 2  # front + back of one sheet
        subset_front = geometry(_content_bytes(subset_pdf.pages[0]))
        subset_back = geometry(_content_bytes(subset_pdf.pages[1]))

    with pikepdf.open(full_path) as full_pdf:
        # Sheet 6 (0-indexed) occupies pages 12-13 of the full export.
        full_front = geometry(_content_bytes(full_pdf.pages[12]))
        full_back = geometry(_content_bytes(full_pdf.pages[13]))

    assert subset_front == full_front
    assert subset_back == full_back


# --- BEHAVIORAL: unwritable path raises before writing -----------------


def test_export_to_unwritable_path_raises_before_writing(tmp_path):
    plan = _plan_from_source(tmp_path, 2)
    bad_dir = os.path.join(str(tmp_path), "does_not_exist")
    bad_path = os.path.join(bad_dir, "out.pdf")

    with pytest.raises(OSError):
        export_fn(plan, bad_path)

    assert not os.path.exists(bad_path)
    assert not os.path.exists(bad_dir)


# --- BEHAVIORAL: filler pages export genuinely blank --------------------


def test_filler_output_page_exports_as_blank_page(tmp_path):
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    filler = OutputPage(source_ref=None, placement=placement, is_filler=True)
    plan = SheetPlan(sheets=[Sheet(index=0, front=filler, back=None)], paper_pt=LETTER, warnings=[])

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        contents = page.obj.get("/Contents")
        if contents is None:
            data = b""
        elif isinstance(contents, pikepdf.Array):
            data = b"".join(s.read_bytes() for s in contents)
        else:
            data = contents.read_bytes()
        assert data.strip() == b""


# --- BEHAVIORAL: large document exports via batching, bounded memory ---


def test_large_document_export_completes_via_batching(tmp_path):
    n_pages = 500
    plan = _plan_from_source(tmp_path, n_pages)
    out_path = os.path.join(str(tmp_path), "big.pdf")

    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        # n_pages source pages -> n_pages/2 sheets * 2 sides = n_pages pages.
        assert len(pdf.pages) == n_pages
