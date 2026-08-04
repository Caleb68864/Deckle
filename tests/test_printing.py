"""Tests for the PassPlanner: SheetPlan + PrinterProfile -> PrintPass list."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan
from deckle.core.printing import PrintPass, PrintResult, plan_passes
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile


def _blank_output_page() -> OutputPage:
    return OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )


def _make_plan(n_sheets: int) -> SheetPlan:
    sheets = [
        Sheet(index=i, front=_blank_output_page(), back=_blank_output_page())
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=(612.0, 792.0), warnings=[])


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


def test_flip_axis_long_yields_rotate_backs_true():
    profile = _profile(flip_axis="long")
    passes = plan_passes(_make_plan(3), profile)
    back = next(p for p in passes if p.side == "back")
    assert back.rotate_backs is True


def test_flip_axis_short_yields_rotate_backs_false():
    profile = _profile(flip_axis="short")
    passes = plan_passes(_make_plan(3), profile)
    back = next(p for p in passes if p.side == "back")
    assert back.rotate_backs is False


def test_reverse_stack_true_reverses_back_pass_order():
    profile = _profile(reverse_stack=True)
    passes = plan_passes(_make_plan(4), profile)
    front = next(p for p in passes if p.side == "front")
    back = next(p for p in passes if p.side == "back")
    assert back.sheet_order == list(reversed(front.sheet_order))


def test_reverse_stack_false_keeps_back_pass_order():
    profile = _profile(reverse_stack=False)
    passes = plan_passes(_make_plan(4), profile)
    front = next(p for p in passes if p.side == "front")
    back = next(p for p in passes if p.side == "back")
    assert back.sheet_order == front.sheet_order


def test_plan_passes_with_explicit_sheets_covers_only_those_sheets():
    profile = _profile()
    passes = plan_passes(_make_plan(10), profile, sheets=[7])
    for p in passes:
        assert p.sheet_order == [7]


def test_every_pass_has_a_nonempty_reload_instruction_naming_axis_and_face():
    profile = _profile(flip_axis="short", output_face="up")
    passes = plan_passes(_make_plan(2), profile)
    for p in passes:
        assert p.reload_instruction
        assert isinstance(p.reload_instruction, str)
    back = next(p for p in passes if p.side == "back")
    assert "short" in back.reload_instruction
    assert "face up" in back.reload_instruction


def test_builtin_presets_cover_face_down_reversed_and_face_up_in_order():
    assert len(BUILTIN_PRESETS) >= 2
    face_down_reversed = [
        p
        for p in BUILTIN_PRESETS.values()
        if p.output_face == "down" and p.reverse_stack is True
    ]
    face_up_in_order = [
        p
        for p in BUILTIN_PRESETS.values()
        if p.output_face == "up" and p.reverse_stack is False
    ]
    assert face_down_reversed
    assert face_up_in_order


def test_print_pass_and_print_result_and_print_backend_shapes():
    pp = PrintPass(
        index=0,
        sheet_order=[0, 1],
        side="front",
        reload_instruction="load paper",
        rotate_backs=False,
    )
    assert pp.side == "front"

    pr = PrintResult(submitted=2, job_id="abc", error=None)
    assert pr.submitted == 2


def test_printer_profile_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.profiles.sys.platform", "linux")

    profile = _profile()
    profile.save("My Test Printer")

    loaded = PrinterProfile.load("My Test Printer")
    assert loaded == profile

    saved_path = tmp_path / "deckle" / "printer_profiles" / "My Test Printer.json"
    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data["flip_axis"] == "long"
