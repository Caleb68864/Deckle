"""Structural tests for deckle.core.models."""

import dataclasses

import pytest

from deckle.core.models import (
    LayoutSettings,
    LayoutWarning,
    Mark,
    OutputPage,
    Placement,
    Project,
    Sheet,
    SheetPlan,
    Side,
    Signature,
    SourcePage,
    SourceRef,
)


def make_source_ref(page_index: int = 0) -> SourceRef:
    return SourceRef(
        path="input.pdf",
        page_index=page_index,
        sha256="a" * 64,
        width_pt=612.0,
        height_pt=792.0,
    )


def test_placement_is_frozen_with_expected_fields():
    p = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    assert dataclasses.is_dataclass(p)
    field_names = {f.name for f in dataclasses.fields(Placement)}
    assert field_names == {"scale_x", "scale_y", "tx", "ty", "rotate_deg"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.tx = 1.0  # type: ignore[misc]


def test_source_ref_fields():
    ref = make_source_ref()
    assert ref.path == "input.pdf"
    assert ref.page_index == 0
    assert ref.sha256 == "a" * 64
    assert ref.width_pt == 612.0
    assert ref.height_pt == 792.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.page_index = 1  # type: ignore[misc]


def test_source_page_fields():
    ref = make_source_ref()
    page = SourcePage(ref=ref, rotate_deg=90, skipped=False)
    assert page.ref is ref
    assert page.rotate_deg == 90
    assert page.skipped is False


def test_output_page_fields_allow_none_source_ref():
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    filler = OutputPage(source_ref=None, placement=placement, is_filler=True)
    assert filler.source_ref is None
    assert filler.is_filler is True

    ref = make_source_ref()
    real = OutputPage(source_ref=ref, placement=placement, is_filler=False)
    assert real.source_ref is ref
    assert real.is_filler is False


def test_sheet_fields_allow_none_front_and_back():
    sheet = Sheet(index=0, front=None, back=None)
    assert sheet.index == 0
    assert sheet.front is None
    assert sheet.back is None


def test_side_fields_and_defaults():
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    page = OutputPage(source_ref=None, placement=placement, is_filler=True)
    side = Side(pages=(page,))
    assert side.pages == (page,)
    assert side.marks == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        side.pages = ()  # type: ignore[misc]


def test_side_rejects_empty_pages():
    with pytest.raises(ValueError):
        Side(pages=())


def test_sheet_accepts_side_for_front_and_back():
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    page = OutputPage(source_ref=None, placement=placement, is_filler=True)
    side = Side(pages=(page,))
    sheet = Sheet(index=0, front=side, back=None)
    assert sheet.front is side
    assert sheet.back is None


def test_mark_fields():
    mark = Mark(kind="fold_line", x0=0.0, y0=1.0, x1=2.0, y1=3.0)
    assert mark.kind == "fold_line"
    assert (mark.x0, mark.y0, mark.x1, mark.y1) == (0.0, 1.0, 2.0, 3.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mark.x0 = 5.0  # type: ignore[misc]


def test_signature_fields():
    signature = Signature(index=0, sheet_indices=(0, 1, 2), blank_count=1)
    assert signature.index == 0
    assert signature.sheet_indices == (0, 1, 2)
    assert signature.blank_count == 1


def test_layout_warning_fields():
    warning = LayoutWarning(
        sheet_index=2,
        kind="clipped_by_page",
        detail="content exceeds page bounds",
    )
    assert warning.sheet_index == 2
    assert warning.kind == "clipped_by_page"
    assert warning.detail == "content exceeds page bounds"


def test_sheet_plan_fields():
    plan = SheetPlan(sheets=[], paper_pt=(612.0, 792.0), warnings=[])
    assert plan.sheets == []
    assert plan.paper_pt == (612.0, 792.0)
    assert plan.warnings == []
    assert plan.signatures == ()


def test_sheet_plan_signatures_field():
    signature = Signature(index=0, sheet_indices=(0,), blank_count=0)
    plan = SheetPlan(
        sheets=[],
        paper_pt=(612.0, 792.0),
        warnings=[],
        signatures=(signature,),
    )
    assert plan.signatures == (signature,)


def test_layout_settings_defaults():
    settings = LayoutSettings(
        paper=(612.0, 792.0),
        gutter_pt=18.0,
        binding_edge="left",
    )
    assert not hasattr(settings, "scale_mode"), "there is one scale rule, not a mode"
    assert settings.start_on_recto is True
    assert settings.margin_top_pt == 0.0
    assert settings.margin_bottom_pt == 0.0
    assert settings.margin_outer_pt == 0.0
    assert settings.margins_linked is True
    assert settings.landscape_policy == "rotate"
    assert settings.fold_scheme == "none"
    assert settings.sheets_per_signature == 4
    assert settings.paper_thickness_pt == 0.0
    assert settings.sewing_stations == 3
    assert settings.blank_mode == "end"


def test_layout_warning_new_kinds():
    for kind in (
        "sheet_orientation",
        "signature_padding",
        "creep_advisory",
        "landscape_imageable_unverified",
    ):
        warning = LayoutWarning(sheet_index=0, kind=kind, detail="x")
        assert warning.kind == kind


def test_layout_settings_top_binding_edge_not_supported():
    field_types = {f.name: f.type for f in dataclasses.fields(LayoutSettings)}
    # "top" was explicitly removed after red-team review (P-5); only
    # left/right binding edges are supported in this version.
    assert "top" not in str(field_types["binding_edge"])


def test_project_fields():
    ref = make_source_ref()
    page = SourcePage(ref=ref, rotate_deg=0, skipped=False)
    settings = LayoutSettings(
        paper=(612.0, 792.0),
        gutter_pt=18.0,
        binding_edge="left",
    )
    project = Project(pages=[page], layout=settings, printer=None)
    assert project.pages == [page]
    assert project.layout is settings
    assert project.printer is None
