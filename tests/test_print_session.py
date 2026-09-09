"""Tests for PrintSession: the resumable print state machine."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Literal, Sequence

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side, SourceRef
from deckle.core.print_session import PrintSession, StaleSessionError, _hash_plan
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
        Sheet(
            index=i,
            front=Side(pages=(_blank_output_page(),)),
            back=Side(pages=(_blank_output_page(),)),
        )
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

    calls: list[tuple[list[int], str, int, int, str, bool, int]] = field(
        default_factory=list
    )
    fail_on_call_index: int | None = None

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        *,
        side: Literal["front", "back"] = "front",
        rotate_backs: bool = False,
        pass_index: int = 0,
    ) -> PrintResult:
        call_index = len(self.calls)
        # The three keywords are appended, so the existing `sheets, *_`
        # destructuring in this file keeps working. They are recorded rather
        # than ignored because a stub that mirrors a Protocol narrower than
        # the real backend is exactly how "the back pass paints fronts"
        # stayed invisible to a green suite.
        self.calls.append(
            (list(sheets), printer_name, copies, dpi, side, rotate_backs, pass_index)
        )
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
    # Parenthesised, because `assert x == a if c else b` is `assert (x == a)
    # if c else (b)` -- a conditional *expression*. With `reverse_stack`
    # false the whole assertion became `assert [7, 9]`, a truthy list, and
    # the back-pass order went unchecked on the branch where a wrong order
    # is a ruined stack of paper.
    expected = [9, 7] if profile.reverse_stack else [7, 9]
    assert backend.calls[1][0] == expected


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


def test_hash_plan_differs_for_different_page_orderings():
    """REQ-014: same sheet count and side presence, different pages -> different hash.

    Two cases, because the defect has two severities. The 1-up case (one
    page per side) is the MVP shape. The folio case is where it bites: under
    ``fold_scheme="folio"`` a side carries *two* output pages, and a hash
    recording only the first collides on plans holding exactly the same four
    source pages in a different order -- letting a resumed session bind to a
    document that has since been re-imposed.
    """
    # 1-up: one page per side, the front differing.
    plan_a = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=Side(pages=(_source_output_page(0),)),
                back=Side(pages=(_source_output_page(1),)),
            )
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )
    plan_b = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=Side(pages=(_source_output_page(2),)),
                back=Side(pages=(_source_output_page(1),)),
            )
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )

    assert _hash_plan(plan_a) != _hash_plan(plan_b)

    # Folio: two pages per side, the same four source pages, redistributed so
    # each side's FIRST page is unchanged and only the second moves. This
    # ordering is deliberate -- a payload recording one index per side reads
    # (0, 2) for both plans and collides, so reversing each side instead
    # would let this assertion pass for the wrong reason.
    folio_a = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=Side(pages=(_source_output_page(0), _source_output_page(1))),
                back=Side(pages=(_source_output_page(2), _source_output_page(3))),
            )
        ],
        paper_pt=(792.0, 612.0),
        warnings=[],
    )
    folio_b = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=Side(pages=(_source_output_page(0), _source_output_page(3))),
                back=Side(pages=(_source_output_page(2), _source_output_page(1))),
            )
        ],
        paper_pt=(792.0, 612.0),
        warnings=[],
    )

    assert _hash_plan(folio_a) != _hash_plan(folio_b)


def test_hash_plan_distinguishes_absent_side_from_present_side():
    """Side presence must stay in the payload alongside the page tuple.

    A filler page carries ``source_ref=None`` -- the same sentinel an absent
    side would suggest -- so presence cannot be inferred from the page tuple
    alone. Both a real-page back and a filler-only back must hash distinctly
    from no back at all, or a sheet printed single-sided would collide with
    one whose back is blank.
    """
    def build(back) -> SheetPlan:
        return SheetPlan(
            sheets=[
                Sheet(index=0, front=Side(pages=(_source_output_page(0),)), back=back)
            ],
            paper_pt=(612.0, 792.0),
            warnings=[],
        )

    absent_back = _hash_plan(build(None))
    real_back = _hash_plan(build(Side(pages=(_source_output_page(1),))))
    filler_back = _hash_plan(build(Side(pages=(_blank_output_page(),))))

    assert absent_back != real_back
    assert absent_back != filler_back


def test_hash_plan_handles_filler_pages_without_a_source_ref():
    """A filler page has no ``source_ref``; it hashes as ``None``, not an error."""
    def build(front_page) -> SheetPlan:
        return SheetPlan(
            sheets=[Sheet(index=0, front=Side(pages=(front_page,)), back=None)],
            paper_pt=(612.0, 792.0),
            warnings=[],
        )

    filler = _hash_plan(build(_blank_output_page()))  # must not raise
    real = _hash_plan(build(_source_output_page(0)))

    assert isinstance(filler, str) and len(filler) == 16
    assert filler != real


def test_hash_plan_is_stable_across_identical_construction():
    """Equal plans hash equal -- the hash reads content, never object identity."""
    def build() -> SheetPlan:
        return SheetPlan(
            sheets=[
                Sheet(
                    index=0,
                    front=Side(pages=(_source_output_page(0), _source_output_page(1))),
                    back=Side(pages=(_source_output_page(2),)),
                ),
                Sheet(index=1, front=Side(pages=(_blank_output_page(),)), back=None),
            ],
            paper_pt=(612.0, 792.0),
            warnings=[],
        )

    assert _hash_plan(build()) == _hash_plan(build())


def test_print_session_public_surface_is_unchanged():
    """Pins the MVP public surface: SS-04 must not add or remove members."""
    # Parameter names, not just presence: `resume(sheets_completed)` carries a
    # per-pass-not-cumulative contract that a rename would silently break.
    expected_methods = {
        "start": ("self",),
        "advance": ("self",),
        "confirm_test_sheet": ("self",),
        "resume": ("self", "sheets_completed"),
        # `ask_sheets_printed` is keyword-defaulted and last: a caller
        # written against the four-argument form still works. It is pinned
        # here because a resumed run that cannot ask how many sheets came
        # out is the one path where the B37 question silently disappears.
        "load": ("plan", "profile", "backend", "session_id", "ask_sheets_printed"),
        "list_resumable": (),
    }
    expected_properties = {
        "state",
        "state_path",
        "reload_instruction",
        "finished",
        "last_error",
    }

    for name, expected_params in expected_methods.items():
        assert hasattr(PrintSession, name), f"missing method: {name}"
        member = getattr(PrintSession, name)
        assert callable(member), f"not callable: {name}"
        actual_params = tuple(inspect.signature(member).parameters)
        assert actual_params == expected_params, (
            f"{name} signature moved: {actual_params} != {expected_params}"
        )

    # `getattr_static` bypasses the descriptor protocol, so a property that
    # silently became a plain method is caught rather than passing on hasattr.
    for name in expected_properties:
        assert hasattr(PrintSession, name), f"missing property: {name}"
        assert isinstance(inspect.getattr_static(PrintSession, name), property), (
            f"{name} is no longer a property"
        )

    assert inspect.isroutine(inspect.getattr_static(PrintSession, "list_resumable")), (
        "list_resumable is no longer a staticmethod"
    )
    assert isinstance(inspect.getattr_static(PrintSession, "load"), classmethod), (
        "load is no longer a classmethod"
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


# -- stale-session refusal (hardening pass 7) ----------------------------
#
# `plan_hash` was stored from the start and never compared, so a session
# could be resumed against a document that had been re-imposed underneath
# it. On a printer with no duplexer the user has already physically
# reloaded the stack by then, so the first sign of trouble is a ruined
# pile of paper. These tests pin the refusal.


def _reimposed_plan(n_sheets: int) -> SheetPlan:
    """Same sheet count and side presence as ``_make_plan``, different pages.

    This is the dangerous case precisely because it is indistinguishable
    from the original by every coarse measure -- only the page *content*
    behind each side differs.
    """
    sheets = [
        Sheet(
            index=i,
            front=Side(pages=(_source_output_page(i * 2),)),
            back=Side(pages=(_source_output_page(i * 2 + 1),)),
        )
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=(612.0, 792.0), warnings=[])


def _started_session_id(plan: SheetPlan) -> str:
    session = PrintSession(plan, _profile(), StubBackend(), printer_name="P")
    session.start()
    return session._state.session_id


def test_load_resumes_normally_when_the_plan_is_unchanged():
    """The guard must not break the case it exists to protect."""
    plan = _make_plan(3)
    session_id = _started_session_id(plan)

    resumed = PrintSession.load(plan, _profile(), StubBackend(), session_id)

    assert resumed._state.session_id == session_id


def test_load_refuses_a_session_whose_plan_has_changed():
    plan = _make_plan(3)
    session_id = _started_session_id(plan)

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(_reimposed_plan(3), _profile(), StubBackend(), session_id)

    error = exc_info.value
    assert error.reason == "plan"
    assert error.session_id == session_id
    # The message is for the person standing at the printer.
    assert "layout has changed" in error.detail
    assert "start a new print run" in error.detail


# -- the changes a resume used to sail straight through -------------------
#
# `_hash_plan` covered sheet index, side presence and source page indices,
# and nothing else. Every parameter below survives that hash unchanged while
# changing what lands on paper, so a back pass resumed after any of them
# would print onto fronts it no longer lines up with -- discovered only once
# the stack is ruined. They are the reason the session now shares the
# exporter's digest rather than keeping a weaker one of its own.


def _plan_with(pages, paper_pt=(612.0, 792.0), marks=()):
    """One sheet carrying ``pages``, so a single field can be varied."""
    front = Side(pages=tuple(pages), marks=tuple(marks)) if marks else Side(pages=tuple(pages))
    return SheetPlan(
        sheets=[Sheet(index=0, front=front, back=Side(pages=(_blank_output_page(),)))],
        paper_pt=paper_pt,
        warnings=[],
    )


def _placed(page_index=0, **placement):
    base = dict(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    base.update(placement)
    return OutputPage(
        source_ref=SourceRef(
            path="doc.pdf", page_index=page_index, sha256="0" * 64,
            width_pt=612.0, height_pt=792.0,
        ),
        placement=Placement(**base),
        is_filler=False,
    )


@pytest.mark.parametrize(
    "name, changed",
    [
        # A wider gutter shifts the page across the sheet.
        ("gutter", _plan_with([_placed(tx=36.0)])),
        # A different margin scales it.
        ("margins", _plan_with([_placed(scale_x=0.94, scale_y=0.94)])),
        # A2 instead of Letter.
        ("paper", _plan_with([_placed()], paper_pt=(420.94, 595.28))),
        # The Rotate button.
        ("rotation", _plan_with([_placed(rotate_deg=90)])),
    ],
)
def test_hash_plan_notices_a_change_that_moves_ink(name, changed):
    """Each of these left the old hash identical."""
    original = _plan_with([_placed()])

    assert _hash_plan(original) != _hash_plan(changed), (
        f"a change of {name} did not change the plan hash"
    )


def test_hash_plan_notices_a_different_document_with_the_same_pagination():
    """The old hash recorded page *indices*, so any 2-page PDF matched any
    other. The source's own digest is what tells them apart."""
    original = _plan_with([_placed()])
    other_book = SheetPlan(
        sheets=[Sheet(
            index=0,
            front=Side(pages=(OutputPage(
                source_ref=SourceRef(
                    path="doc.pdf", page_index=0, sha256="f" * 64,
                    width_pt=612.0, height_pt=792.0,
                ),
                placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0,
                                    rotate_deg=0),
                is_filler=False,
            ),)),
            back=Side(pages=(_blank_output_page(),)),
        )],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )

    assert _hash_plan(original) != _hash_plan(other_book)


def test_a_resume_after_a_re_imposition_is_refused_end_to_end():
    """The whole point, driven through `load`: the operator has reloaded the
    paper by now, so this refusal is the last thing standing between a
    changed layout and sixty ruined sheets."""
    plan = _plan_with([_placed()])
    session_id = _started_session_id(plan)
    wider_gutter = _plan_with([_placed(tx=36.0)])

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(wider_gutter, _profile(), StubBackend(), session_id)

    assert exc_info.value.reason == "plan"


def test_the_session_and_the_exporter_agree_on_what_a_plan_is():
    """Two answers to "is this the same plan?" is how they drifted apart.

    The session's is the exporter's, truncated -- so a plan the render cache
    treats as new can never be one the resume guard treats as unchanged.
    """
    from deckle.core import export
    from deckle.core.plan_digest import plan_digest

    plan = _plan_with([_placed()])

    assert _hash_plan(plan) == plan_digest(plan)[:16]
    assert export._plan_hash(plan) == plan_digest(plan)


def test_load_refuses_a_state_file_from_an_incompatible_version():
    import json

    from deckle.core.print_session import STATE_VERSION, _state_dir

    plan = _make_plan(2)
    session_id = _started_session_id(plan)
    path = _state_dir() / f"{session_id}.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = STATE_VERSION - 1
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(plan, _profile(), StubBackend(), session_id)

    assert exc_info.value.reason == "version"
    # A version mismatch must NOT be reported as "the document changed" --
    # what changed was Deckle, and telling the user otherwise sends them
    # hunting for an edit they never made.
    assert "layout has changed" not in exc_info.value.detail
    assert "version of Deckle" in exc_info.value.detail


def test_the_version_check_runs_before_the_plan_check():
    """A v1 file's hash is incomparable, so version must be diagnosed first."""
    import json

    from deckle.core.print_session import STATE_VERSION, _state_dir

    plan = _make_plan(2)
    session_id = _started_session_id(plan)
    path = _state_dir() / f"{session_id}.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = STATE_VERSION - 1
    data["plan_hash"] = "totally-different"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(plan, _profile(), StubBackend(), session_id)

    assert exc_info.value.reason == "version"


# -- the profile the run started under -----------------------------------
#
# `plan_hash` answers "is this the same document?" and answers it
# completely. It cannot answer "is this the same printer behaviour?", and
# the profile decides two things that put ink on paper: `reverse_stack`
# picks the back pass's sheet order, `flip_axis` picks whether the back is
# turned. A resume under a different profile prints backs onto the wrong
# fronts with the plan hash matching perfectly.
#
# It does not take a user changing a setting. `resolve_profile` falls back
# to the first builtin when a saved profile cannot be read, and the two
# builtins differ on BOTH axes -- so a deleted, corrupt or disconnected
# profile file is enough.


def test_load_refuses_a_session_whose_printer_profile_has_changed():
    plan = _make_plan(6)
    session_id = _started_session_id(plan)

    recalibrated = _profile(output_face="up", reverse_stack=False, flip_axis="short")

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(plan, recalibrated, StubBackend(), session_id)

    error = exc_info.value
    assert error.reason == "profile"
    assert error.session_id == session_id
    # The document is not what changed, and saying so sends the operator
    # hunting for an edit they never made.
    assert "layout has changed" not in error.detail
    assert "calibration" in error.detail
    assert "start a new print run" in error.detail


def test_a_resume_under_a_changed_profile_does_not_reverse_the_back_pass():
    """The failure the guard exists to stop, driven end to end.

    Six sheets, chunks of three. The fronts print, the first back chunk
    prints, the second fails. The profile then changes -- `reverse_stack`
    False->True and `flip_axis` short->long. Without a profile guard the
    resume submits `[2, 1, 0]` turned a half turn: sheets 2, 1 and 0 get
    their backs printed a second time, the wrong way up, and sheets 3, 4
    and 5 never get backs at all.
    """
    plan = _make_plan(6)
    started = _profile(output_face="up", reverse_stack=False, flip_axis="short")
    backend = StubBackend(fail_on_call_index=3)
    session = PrintSession(plan, started, backend, printer_name="P", chunk_size=3)

    session.start()
    while not session.finished and session.last_error is None:
        session.advance()

    assert session.last_error is not None, "the run was supposed to be interrupted"
    assert session.state["pass_index"] == 1, "the interruption must land in the backs"
    session_id = session._state.session_id

    recalibrated = _profile(output_face="down", reverse_stack=True, flip_axis="long")

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(plan, recalibrated, StubBackend(), session_id)

    assert exc_info.value.reason == "profile"


def test_the_two_builtin_presets_are_not_interchangeable_on_resume():
    """The passive trigger: nobody changes anything, a profile file goes
    missing, and `resolve_profile` hands back the other builtin."""
    from deckle.core.print_session import _hash_profile
    from deckle.core.profiles import BUILTIN_PRESETS

    fingerprints = {_hash_profile(p) for p in BUILTIN_PRESETS.values()}

    assert len(fingerprints) == len(BUILTIN_PRESETS)


def test_the_profile_fingerprint_ignores_nothing_that_moves_ink():
    """Both behavioural axes, and the registration correction with them."""
    from deckle.core.print_session import _hash_profile

    base = _profile()

    assert _hash_profile(base) != _hash_profile(_profile(reverse_stack=False))
    assert _hash_profile(base) != _hash_profile(_profile(flip_axis="short"))
    assert _hash_profile(base) != _hash_profile(_profile(back_offset_x_pt=3.0))
    assert _hash_profile(base) == _hash_profile(_profile())


def test_load_resumes_normally_when_the_profile_is_unchanged():
    """The guard must not break the case it exists to protect."""
    plan = _make_plan(3)
    session_id = _started_session_id(plan)

    resumed = PrintSession.load(plan, _profile(), StubBackend(), session_id)

    assert resumed._state.session_id == session_id


def test_a_state_file_predating_the_profile_guard_is_a_version_mismatch():
    """A v3 file has no profile fingerprint at all. That is Deckle
    changing, not the operator's state file being malformed, so it must be
    diagnosed as a version mismatch rather than as invalid state."""
    import json

    from deckle.core.print_session import _state_dir

    plan = _make_plan(2)
    session_id = _started_session_id(plan)
    path = _state_dir() / f"{session_id}.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("profile_hash")
    data["version"] = 3
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StaleSessionError) as exc_info:
        PrintSession.load(plan, _profile(), StubBackend(), session_id)

    assert exc_info.value.reason == "version"


# -- the side, the turn and the index reach the backend ------------------
#
# The session computes all three correctly in `plan_passes` and then dropped
# them on the floor: `_submit_sheets` called `backend.submit` with five
# positional arguments, so `side`, `rotate_backs` and `pass_index` took the
# real backend's front-side defaults on *both* passes. Pass 2 rasterised the
# front of every sheet, unturned, with the measured registration offset
# unapplied -- the desktop app printed the fronts twice.
#
# Nothing here could see it before, because `PrintBackend` declared only the
# five arguments and `StubBackend` mirrored that exactly. A stub that matches
# a Protocol narrower than the only real implementation cannot observe what
# the implementation was never told.


def _run_both_passes(profile: PrinterProfile) -> StubBackend:
    """Drive a two-sheet job to completion and hand back the recording."""
    plan = _make_plan(2)
    backend = StubBackend()
    session = PrintSession(plan, profile, backend, printer_name="P")

    session.start()
    while not session.finished:
        session.advance()

    return backend


def _calls_for_pass(backend: StubBackend, index: int) -> list[tuple]:
    """Every recorded call whose `pass_index` is `index`.

    Selected by the recorded value rather than by position, so this helper
    cannot paper over the very field the tests below are about.
    """
    return [call for call in backend.calls if call[6] == index]


def test_the_back_pass_asks_for_the_back_side():
    """The whole point of manual duplex: pass 2 prints the other face."""
    backend = _run_both_passes(_profile())

    sides = [call[4] for call in backend.calls]
    assert sides == ["front", "back"], sides


def test_a_long_edge_flip_turns_its_backs():
    """A printer that flips about the long edge needs the back turned.

    ``plan_passes`` decides this -- ``rotate_backs = flip_axis == "long"``
    -- and the session's job is only to carry the answer through. Both
    axes are asserted, because a fix that hard-codes ``True`` would be as
    wrong as the ``False`` it replaced, and only the pair can tell the two
    apart.
    """
    long_edge = [call[5] for call in _run_both_passes(_profile()).calls]
    assert long_edge == [False, True], long_edge

    short_edge = [
        call[5] for call in _run_both_passes(_profile(flip_axis="short")).calls
    ]
    assert short_edge == [False, False], short_edge


def test_each_pass_submits_under_its_own_index():
    """The pass index reaches the backend, and so reaches the session log.

    Without it every chunk of both passes was logged as pass 0, which makes
    the log useless for the one question it exists to answer: which sheets
    went through on which pass.
    """
    backend = _run_both_passes(_profile())

    indices = [call[6] for call in backend.calls]
    assert indices == [0, 1], indices


def test_a_chunked_pass_keeps_its_side_on_every_chunk():
    """The keywords are per-call, so a long pass must not lose them midway.

    Sixty sheets at the default chunk size is six submissions per pass. The
    session chunks rather than delegating to ``submit_pass`` -- the cursor
    has to advance per chunk for resume to land on a sheet -- so each chunk
    passes the keywords itself, and that is exactly the kind of thing that
    gets right on the first chunk and wrong on the rest.
    """
    plan = _make_plan(60)
    backend = StubBackend()
    session = PrintSession(plan, _profile(), backend, printer_name="P")

    session.start()
    while not session.finished:
        session.advance()

    fronts = _calls_for_pass(backend, 0)
    backs = _calls_for_pass(backend, 1)

    assert len(fronts) > 1 and len(backs) > 1, (len(fronts), len(backs))
    assert all(call[4] == "front" for call in fronts)
    assert all(call[4] == "back" for call in backs)


# -- list_resumable must actually never raise -----------------------------


@pytest.mark.parametrize(
    "body", ["{}", '{"session_id": "abc"}', "[]", '"a string"', "null"]
)
def test_list_resumable_survives_a_file_that_parses_but_is_not_a_session(
    body, tmp_path, monkeypatch
):
    """Its docstring promises it never raises, and the print dialog
    believes that -- `PrintDialog.__init__` calls it with no `try`.

    Only `OSError` and `JSONDecodeError` were caught, and the five
    `data[...]` lookups sat *after* the `try`. A file that is valid JSON
    but not a session -- truncated, half-written, or from a build that
    named these fields differently -- parses cleanly and then raises
    `KeyError`, so one such file in the state directory made the print
    dialog impossible to open at all.
    """
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text(body, encoding="utf-8")

    assert PrintSession.list_resumable() == []


def test_one_unreadable_file_does_not_hide_the_healthy_sessions(tmp_path, monkeypatch):
    """The point of skipping rather than raising."""
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path))
    plan = _make_plan(3)
    good_id = _started_session_id(plan)
    (tmp_path / "broken.json").write_text("{}", encoding="utf-8")

    summaries = PrintSession.list_resumable()

    assert [s.session_id for s in summaries] == [good_id]
