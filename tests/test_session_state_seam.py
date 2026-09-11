"""The state file as a seam: what a session persists, and what it forgives.

Two changes on 2026-09-11, both about the same boundary.

**``SessionSummary.state_path`` is gone.** It was written by
``list_resumable`` from the path it had just read, carried through the
resume picker, and read by nothing. ``load`` takes a ``session_id`` and
re-derives the path from it, which is the right source: it is the same
derivation that *wrote* the file, and two spellings of where a session
lives is one more than can be kept in agreement.

**``chunk_size`` is persisted.** It was a constructor bound that ``load``
reset to the default, so a non-default value silently did not survive a
resume -- a bound you could set and not get back. No production caller
sets it; this suite does, extensively, and that is a legitimate use. A
bound that accepts a value and quietly discards it is worse than no
bound.

The second change puts a new key in a persisted file, which is the part
worth being careful about. A state file written before today does not
have it, and **must load anyway**: it is missing the key because Deckle
changed, not because anything is wrong with the file. An operator with a
half-finished print job and a stopped printer must not be told their
session is invalid over a field they never knew existed.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import (
    DEFAULT_CHUNK_SIZE,
    PrintSession,
    SessionSummary,
    StaleSessionError,
)
from deckle.core.printing import PrintResult
from deckle.core.profiles import BUILTIN_PRESETS


class StubBackend:
    def __init__(self):
        self.chunks: list[list[int]] = []

    def submit(self, plan, sheets, printer_name, copies, dpi, **kwargs):
        self.chunks.append(list(sheets))
        return PrintResult(submitted=len(sheets), job_id="job", error=None)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path / "logs"))


def _plan(n=9) -> SheetPlan:
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    return SheetPlan(
        sheets=[
            Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
            for i in range(n)
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )


PROFILE = BUILTIN_PRESETS["generic_face_down_reversed"]


# -- SessionSummary.state_path is gone -----------------------------------


def test_a_summary_carries_no_path():
    """A summary carrying a path nobody resumes from is an invitation to
    resume from it."""
    names = {f.name for f in dataclasses.fields(SessionSummary)}

    assert "state_path" not in names
    assert names == {
        "session_id", "printer_name", "started_at", "pass_index", "sheet_cursor",
    }


def test_a_session_is_still_resumable_by_its_id_alone():
    """Which is the whole reason the path was removable: the id is
    sufficient, and it is what ``load`` already took."""
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()

    summaries = PrintSession.list_resumable()

    assert [s.session_id for s in summaries] == [session.state["session_id"]]
    resumed = PrintSession.load(
        _plan(3), PROFILE, StubBackend(), summaries[0].session_id
    )
    assert resumed.state["session_id"] == summaries[0].session_id


def test_the_session_itself_still_knows_its_path():
    """``PrintSession.state_path`` is a different thing and stays -- it is
    the live session's own file, and six durability tests read it."""
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()

    assert session.state_path is not None


# -- chunk_size survives a resume ----------------------------------------


def test_a_non_default_chunk_size_is_written_down():
    session = PrintSession(
        _plan(9), PROFILE, StubBackend(), printer_name="P", chunk_size=3
    )
    session.start()

    stored = json.loads(session.state_path.read_text(encoding="utf-8"))

    assert stored["chunk_size"] == 3


def test_a_non_default_chunk_size_survives_a_resume():
    """The defect, stated as the run it spoils: a session started at 3 came
    back at 10 and re-chunked the remainder of a run whose earlier chunks
    were sized differently."""
    backend = StubBackend()
    session = PrintSession(
        _plan(9), PROFILE, backend, printer_name="P", chunk_size=3
    )
    session.start()
    assert backend.chunks[0] == [0, 1, 2], "the first chunk was not sized 3"

    resumed_backend = StubBackend()
    resumed = PrintSession.load(
        _plan(9), PROFILE, resumed_backend, session.state["session_id"]
    )

    assert resumed.chunk_size == 3
    resumed.resume(0)
    assert len(resumed_backend.chunks[0]) == 3, (
        f"the resumed run re-chunked at {len(resumed_backend.chunks[0])}"
    )


def test_the_default_is_still_the_default():
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()

    assert session.chunk_size == DEFAULT_CHUNK_SIZE
    stored = json.loads(session.state_path.read_text(encoding="utf-8"))
    assert stored["chunk_size"] == DEFAULT_CHUNK_SIZE


def test_a_chunk_size_of_zero_is_refused():
    """``remaining[:0]`` is empty on a non-empty pass, so ``_submit_chunk``
    advances the pass without submitting anything and the job "completes"
    having printed nothing."""
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()
    path = session.state_path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["chunk_size"] = 0
    data["pass_index"] = 0
    data["sheet_cursor"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StaleSessionError) as caught:
        PrintSession.load(_plan(3), PROFILE, StubBackend(), path.stem)

    assert caught.value.reason == "state"


# -- the persisted seam: an older file still loads ------------------------


def test_a_state_file_written_before_chunk_size_existed_still_loads():
    """**The one that matters for anybody mid-job when they update.**

    The file is missing the key because Deckle changed, not because
    anything is wrong with it. Refusing would tell an operator standing
    at a stopped printer that their session is invalid over a field they
    never knew existed -- and the remedy offered would be "start a new
    print run", meaning reprint the stack.
    """
    session = PrintSession(
        _plan(9), PROFILE, StubBackend(), printer_name="P", chunk_size=3
    )
    session.start()
    path = session.state_path
    data = json.loads(path.read_text(encoding="utf-8"))
    # Exactly what an older build wrote: no `chunk_size` at all.
    del data["chunk_size"]
    data["pass_index"] = 0
    data["sheet_cursor"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")

    resumed = PrintSession.load(_plan(9), PROFILE, StubBackend(), path.stem)

    assert resumed.chunk_size == DEFAULT_CHUNK_SIZE, (
        "an older file should resume at the default, which is what it "
        "was actually running at"
    )


def test_an_unknown_extra_key_is_ignored_rather_than_refused():
    """The same tolerance in the other direction: a file written by a
    *newer* build, or one carrying a field since removed, must not be
    refused for carrying something this build has no use for."""
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()
    path = session.state_path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state_path"] = "/somewhere/it/used/to/say"
    data["a_field_from_the_future"] = 42
    data["pass_index"] = 0
    data["sheet_cursor"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")

    resumed = PrintSession.load(_plan(3), PROFILE, StubBackend(), path.stem)

    assert resumed.state["session_id"] == path.stem


def test_a_genuinely_missing_field_is_still_refused():
    """The tolerance above must not become "anything goes". Only the
    fields on the additive list are optional; a file missing
    ``sheet_cursor`` is broken, and saying so is the whole reason that
    guard exists."""
    session = PrintSession(_plan(3), PROFILE, StubBackend(), printer_name="P")
    session.start()
    path = session.state_path
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["sheet_cursor"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StaleSessionError) as caught:
        PrintSession.load(_plan(3), PROFILE, StubBackend(), path.stem)

    assert "sheet_cursor" in caught.value.detail
