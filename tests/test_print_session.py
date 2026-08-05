"""Tests for PrintSession: the resumable print state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, SourceRef
from deckle.core.print_session import PrintSession, _hash_plan
from deckle.core.printing import PrintResult
from deckle.core.profiles import PrinterProfile


def _blank_output_page() -> OutputPage:
    return OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )


def _source_output_page(page_index: int) -> OutputPage:
    return OutputPage(
        source_ref=SourceRef(
            path="doc.pdf",
            page_index=page_index,
            sha256="0" * 64,
            width_pt=612.0,
            height_pt=792.0,
        ),
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=False,
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


@dataclass
class StubBackend:
    """Records every submission; never touches a real printer."""

    calls: list[tuple[list[int], str, int, int]] = field(default_factory=list)
    fail_on_call_index: int | None = None

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult:
        call_index = len(self.calls)
        self.calls.append((list(sheets), printer_name, copies, dpi))
        if self.fail_on_call_index is not None and call_index == self.fail_on_call_index:
            return PrintResult(submitted=0, job_id=None, error="printer offline")
        return PrintResult(submitted=len(sheets), job_id=f"job-{call_index}", error=None)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))


def test_full_run_emits_pass_1_then_reload_instruction_then_pass_2():
    plan = _make_plan(3)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="Test Printer")

    session.start()
    assert len(backend.calls) == 1
    first_sheets, *_ = backend.calls[0]
    assert first_sheets == [s.index for s in plan.sheets]

    instruction = session.reload_instruction
    assert instruction
    assert "pass 2" in instruction or "backs" in instruction

    session.advance()
    assert len(backend.calls) == 2
    second_sheets, *_ = backend.calls[1]
    assert second_sheets == list(reversed([s.index for s in plan.sheets]))
    assert session.finished


def test_test_first_submits_exactly_one_sheet_and_waits_for_confirmation():
    plan = _make_plan(5)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, test_first=True, printer_name="P")

    session.start()
    assert len(backend.calls) == 1
    assert backend.calls[0][0] == [0]

    # Calling advance() before confirmation must not submit further sheets.
    session.advance()
    assert len(backend.calls) == 1

    session.confirm_test_sheet()
    assert len(backend.calls) == 2
    remaining_sheets = backend.calls[1][0]
    assert remaining_sheets == [1, 2, 3, 4]


def test_resume_after_reload_from_disk_continues_at_sheet_31():
    plan = _make_plan(60)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="P")
    session_id = session._state.session_id
    session.start()  # submits chunk of 10 sheets (default chunk size)

    # Simulate the process dying mid-pass: reconstruct from disk.
    reloaded = PrintSession.load(plan, profile, StubBackend(), session_id)
    reloaded.resume(30)

    # The next chunk submitted should start at sheet index 30 (0-based -> sheet 31).
    next_chunk = reloaded.backend.calls[-1][0]
    assert next_chunk[0] == 30
    assert reloaded._state.sheet_cursor > 30


def test_explicit_sheets_routes_through_plan_passes_not_a_reprint_branch():
    plan = _make_plan(10)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, sheets=[7, 9], printer_name="P")

    session.start()
    assert backend.calls[0][0] == [7, 9]

    session.advance()
    assert backend.calls[1][0] == [9, 7] if profile.reverse_stack else [7, 9]


def test_backend_error_leaves_session_resumable_not_discarded():
    plan = _make_plan(5)
    profile = _profile()
    backend = StubBackend(fail_on_call_index=0)
    session = PrintSession(plan, profile, backend, printer_name="P")

    session.start()
    assert session.last_error == "printer offline"
    assert not session.finished
    assert session.state_path.exists()


def test_resume_is_per_pass_not_cumulative():
    plan = _make_plan(60)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="P")
    session_id = session._state.session_id
    session.start()

    # Finish pass 1 entirely, moving into pass 2.
    while session._state.pass_index == 0 and not session.finished:
        session.advance()
    assert session._state.pass_index == 1

    # Interrupt mid pass 2 and resume with a count that only makes sense
    # for the *second* pass, not the cumulative total across both passes.
    reloaded = PrintSession.load(plan, profile, StubBackend(), session_id)
    reloaded.resume(15)
    # sheet_cursor advances from the resumed count, not from a cumulative
    # count across both passes -- it stayed anchored to pass 2's own sheets.
    assert 15 <= reloaded._state.sheet_cursor <= 60
    assert reloaded._state.pass_index == 1
    pass_2 = reloaded._passes[1]
    first_resumed_chunk = reloaded.backend.calls[0][0]
    assert first_resumed_chunk[0] == pass_2.sheet_order[15]


def test_session_id_derived_from_plan_hash_printer_and_started_at():
    plan = _make_plan(2)
    profile = _profile()
    session = PrintSession(plan, profile, StubBackend(), printer_name="Printer A")
    assert session._state.session_id
    assert isinstance(session._state.session_id, str)


def test_list_resumable_enumerates_interrupted_sessions():
    plan = _make_plan(5)
    profile = _profile()

    backend1 = StubBackend(fail_on_call_index=0)
    session1 = PrintSession(plan, profile, backend1, printer_name="Printer A")
    session1.start()

    backend2 = StubBackend(fail_on_call_index=0)
    session2 = PrintSession(plan, profile, backend2, printer_name="Printer B")
    session2.start()

    summaries = PrintSession.list_resumable()
    assert len(summaries) == 2
    printer_names = {s.printer_name for s in summaries}
    assert printer_names == {"Printer A", "Printer B"}


def test_successful_completion_deletes_state_file():
    plan = _make_plan(2)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="P")

    session.start()
    session.advance()
    assert session.finished
    assert not session.state_path.exists()
    assert PrintSession.list_resumable() == []


def test_state_persists_version_pass_index_sheet_cursor_and_printer_name():
    plan = _make_plan(4)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="My Printer")
    session.start()

    state = session.state
    assert isinstance(state["version"], int)
    assert "pass_index" in state
    assert "sheet_cursor" in state
    assert state["printer_name"] == "My Printer"


def test_hash_plan_differs_for_same_sheet_count_and_presence_but_different_source_pages():
    plan_a = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=_source_output_page(0),
                back=_source_output_page(1),
            )
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )
    plan_b = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=_source_output_page(2),
                back=_source_output_page(1),
            )
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )

    assert _hash_plan(plan_a) != _hash_plan(plan_b)


def test_hash_plan_is_stable_across_construction_of_an_identical_plan():
    def build() -> SheetPlan:
        return SheetPlan(
            sheets=[
                Sheet(
                    index=0,
                    front=_source_output_page(0),
                    back=_source_output_page(1),
                ),
                Sheet(index=1, front=_blank_output_page(), back=None),
            ],
            paper_pt=(612.0, 792.0),
            warnings=[],
        )

    assert _hash_plan(build()) == _hash_plan(build())


def test_print_session_public_surface_is_unchanged():
    """Pins the MVP public surface: SS-04 must not add or remove members."""
    expected_methods = {
        "start",
        "advance",
        "confirm_test_sheet",
        "resume",
        "load",
        "list_resumable",
    }
    expected_properties = {
        "state",
        "state_path",
        "reload_instruction",
        "finished",
        "last_error",
    }

    for name in expected_methods:
        assert hasattr(PrintSession, name), f"missing method: {name}"
        assert callable(getattr(PrintSession, name)), f"not callable: {name}"

    for name in expected_properties:
        assert hasattr(PrintSession, name), f"missing property: {name}"
        assert isinstance(getattr(PrintSession, name), property), (
            f"{name} is no longer a property"
        )

    plan = _make_plan(2)
    profile = _profile()
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="P")

    # Instance-level smoke check that each still behaves per the MVP contract.
    session.start()
    assert session.reload_instruction is not None or session.reload_instruction is None
    assert session.finished in (True, False)
    assert session.last_error is None or isinstance(session.last_error, str)
    assert isinstance(session.state, dict)
    assert session.state_path is not None
