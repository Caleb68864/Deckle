"""Tests for deckle.app.views.preview_view / layout_panel.

Only the plain (Qt-free) functions are exercised here -- matching
tests/test_app_state.py and friends, these modules are importable and
their pure logic testable without PySide6/a display; the Qt widget classes
themselves are wired but not instantiated in headless tests.
"""

from __future__ import annotations

import inspect
import os

import pikepdf
import pytest

from deckle.app.views import layout_panel, preview_view
from deckle.core import export as export_module
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Project,
    Sheet,
    SheetPlan,
    SourcePage,
    SourceRef,
)
from deckle.core.profiles import PrinterProfile

LETTER = (612.0, 792.0)


def _profile(imageable_area_pt=(18.0, 18.0, 18.0, 18.0)) -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=imageable_area_pt,
        calibrated_at="",
        calibration_version=0,
    )


def _ref(path: str, page_index: int = 0, width_pt=612.0, height_pt=792.0) -> SourceRef:
    return SourceRef(path=path, page_index=page_index, sha256="a" * 64, width_pt=width_pt, height_pt=height_pt)


def _placement(**overrides) -> Placement:
    base = dict(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    base.update(overrides)
    return Placement(**base)


def _output_page(ref, is_filler=False, **placement_overrides) -> OutputPage:
    return OutputPage(source_ref=ref, placement=_placement(**placement_overrides), is_filler=is_filler)


def _write_source_pdf(tmp_path, n_pages: int = 1, page_size=(612.0, 792.0)) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    pdf.close()
    return path


def _plan(sheets: list[Sheet], paper_pt=LETTER, warnings=None) -> SheetPlan:
    return SheetPlan(sheets=sheets, paper_pt=paper_pt, warnings=warnings or [])


# -- [MECHANICAL] the preview render path routes through export -----------


def test_render_visible_sheet_routes_through_export_with_sheet_index(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path)
    ref = _ref(src)
    sheet = Sheet(index=3, front=_output_page(ref), back=None)
    plan = _plan([Sheet(index=i, front=None, back=None) for i in range(3)] + [sheet])

    calls = []
    real_export = export_module.export

    def spy_export(plan_arg, out_path, sheets=None):
        calls.append((plan_arg, out_path, sheets))
        return real_export(plan_arg, out_path, sheets=sheets)

    monkeypatch.setattr(export_module, "export", spy_export)

    result = preview_view.render_visible_sheet(plan, 3, "front", dpi=72)

    assert len(calls) == 1
    assert calls[0][2] == [3]
    assert result.width > 0
    assert result.height > 0


# -- [BEHAVIORAL] two distinct clipping causes, distinct text -------------


def test_content_past_page_edge_is_clipped_by_page():
    # tx/ty/scale chosen so the placed content's footprint runs off the
    # right/top edge of the LETTER page entirely.
    ref = _ref("x.pdf", width_pt=612.0, height_pt=792.0)
    sheet = Sheet(index=0, front=_output_page(ref, tx=100.0, ty=100.0), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert len(warnings) == 1
    assert warnings[0].kind == "clipped_by_page"
    assert warnings[0].sheet_index == 0


def test_content_inside_page_but_outside_imageable_area_is_flagged_separately():
    ref = _ref("x.pdf", width_pt=612.0, height_pt=792.0)
    # Full-bleed placement: exactly fills the page, so it necessarily spills
    # into the printer's non-zero margins without ever leaving the page box.
    sheet = Sheet(index=0, front=_output_page(ref, tx=0.0, ty=0.0), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert len(warnings) == 1
    assert warnings[0].kind == "clipped_by_imageable_area"
    assert warnings[0].sheet_index == 0


def test_clipped_by_page_and_clipped_by_imageable_area_have_different_text():
    ref = _ref("x.pdf", width_pt=612.0, height_pt=792.0)
    page_clip_sheet = Sheet(index=0, front=_output_page(ref, tx=100.0, ty=100.0), back=None)
    imageable_clip_sheet = Sheet(index=1, front=_output_page(ref, tx=0.0, ty=0.0), back=None)

    page_warning = preview_view.clipping_warnings_for_sheet(page_clip_sheet, LETTER, (18.0, 18.0, 18.0, 18.0))[0]
    imageable_warning = preview_view.clipping_warnings_for_sheet(
        imageable_clip_sheet, LETTER, (18.0, 18.0, 18.0, 18.0)
    )[0]

    assert page_warning.detail != imageable_warning.detail
    assert page_warning.kind != imageable_warning.kind


def test_content_fully_within_imageable_area_has_no_warning():
    ref = _ref("x.pdf", width_pt=612.0 - 36.0, height_pt=792.0 - 36.0)
    sheet = Sheet(index=0, front=_output_page(ref, tx=18.0, ty=18.0), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert warnings == []


def test_filler_page_produces_no_clipping_warning():
    sheet = Sheet(index=0, front=_output_page(None, is_filler=True), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert warnings == []


# -- [BEHAVIORAL] warning on sheet 12 is visible there, never modal -------


def test_warning_on_sheet_12_is_attached_to_sheet_12_only():
    ref = _ref("x.pdf", width_pt=612.0, height_pt=792.0)
    sheets = [Sheet(index=i, front=None, back=None) for i in range(12)]
    sheets.append(Sheet(index=12, front=_output_page(ref, tx=0.0, ty=0.0), back=None))
    plan = _plan(sheets)

    by_sheet = preview_view.warnings_for_plan(plan, _profile())

    assert 12 in by_sheet
    assert by_sheet[12][0].sheet_index == 12
    assert all(sheet_index != 12 for sheet_index in by_sheet if sheet_index != 12 and by_sheet[sheet_index])
    text = preview_view.badge_text(by_sheet[12])
    assert text  # non-empty text for a plain label, not a dialog


def test_preview_view_module_never_uses_a_modal_dialog():
    source = inspect.getsource(preview_view)
    assert "QMessageBox" not in source
    assert ".exec(" not in source and ".exec_(" not in source


# -- [BEHAVIORAL] gutter change re-renders only the visible sheet ---------


def test_gutter_change_recomputes_plan_arithmetically_without_rendering(monkeypatch, tmp_path):
    src = _write_source_pdf(tmp_path, n_pages=4)
    pages = [SourcePage(ref=_ref(src, i), rotate_deg=0, skipped=False) for i in range(4)]
    settings = LayoutSettings(paper=LETTER, gutter_pt=36.0, binding_edge="left", scale_mode="fixed_gutter")
    project = Project(pages=pages, layout=settings, printer=None)

    render_calls = []
    monkeypatch.setattr(
        preview_view, "render_sheet", lambda *a, **k: render_calls.append(a) or None
    )

    plan = layout_panel.recompute_plan(replace_gutter(project, 54.0))

    # recompute_plan is arithmetic-only: it must never touch the render path.
    assert render_calls == []
    assert isinstance(plan, SheetPlan)
    assert len(plan.sheets) == 2


def replace_gutter(project: Project, gutter_pt: float) -> Project:
    return layout_panel.set_gutter_pt(project, gutter_pt)


def test_visible_sheet_render_after_gutter_change_only_rasterizes_that_sheet(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path, n_pages=4)
    pages = [SourcePage(ref=_ref(src, i), rotate_deg=0, skipped=False) for i in range(4)]
    settings = LayoutSettings(paper=LETTER, gutter_pt=36.0, binding_edge="left", scale_mode="fixed_gutter")
    project = replace_gutter(Project(pages=pages, layout=settings, printer=None), 54.0)
    plan = layout_panel.recompute_plan(project)

    calls = []
    real_export = export_module.export

    def spy_export(plan_arg, out_path, sheets=None):
        calls.append(sheets)
        return real_export(plan_arg, out_path, sheets=sheets)

    monkeypatch.setattr(export_module, "export", spy_export)

    preview_view.render_visible_sheet(plan, 1, "front", dpi=72)

    assert calls == [[1]]


# -- [STRUCTURAL] LayoutPanel offers both scale modes, fit_height default -


def test_scale_modes_include_fit_height_and_fixed_gutter():
    assert set(layout_panel.SCALE_MODES) == {"fit_height", "fixed_gutter"}


def test_fit_height_is_the_preselected_default():
    assert layout_panel.SCALE_MODES[0] == "fit_height"
    assert LayoutSettings.__dataclass_fields__["scale_mode"].default == "fit_height"


def test_set_scale_mode_updates_project_layout():
    settings = LayoutSettings(paper=LETTER, gutter_pt=36.0, binding_edge="left")
    project = Project(pages=[], layout=settings, printer=None)
    assert project.layout.scale_mode == "fit_height"

    updated = layout_panel.set_scale_mode(project, "fixed_gutter")

    assert updated.layout.scale_mode == "fixed_gutter"
    # original untouched -- frozen dataclasses, no in-place mutation.
    assert project.layout.scale_mode == "fit_height"


# -- imageable_rect_pt geometry --------------------------------------------


def test_imageable_rect_pt_converts_top_left_margins_to_bottom_left_rect():
    rect = preview_view.imageable_rect_pt(LETTER, (18.0, 20.0, 18.0, 22.0))
    x0, y0, x1, y1 = rect
    assert x0 == 18.0
    assert y0 == 22.0
    assert x1 == LETTER[0] - 18.0
    assert y1 == LETTER[1] - 20.0
