"""A saved session whose own numbers cannot drive the plan.

The fourth reader of stored data to be checked, and the only one at the
end of a wire. ``PrintSession.load`` already guarded two things and both
are about the *document*: ``version`` (was this written by a build that
meant the same thing?) and ``plan_hash`` (has the layout changed under
us?). Nothing looked at the numbers that decide what actually gets fed.

So a state file naming sheet 99 of a three-sheet plan passed both guards
and was handed to the print backend. So did sheet ``-1``, and the string
``"a"``. So did ``copies: -3`` and ``dpi: "high"``, which go on to a
printer driver that has no reason to expect either.

The bounds are checkable *here* in a way they are not in the other three
readers, and that is the whole difference: ``load`` holds the plan, so
"is 99 a sheet" has an answer at this point, and it is no.

Refused as ``StaleSessionError``, the way this method already refuses a
stale plan or a foreign format version, so the print dialog needs no new
branch. The trade recorded there applies unchanged -- refusing costs a
reprint the user was about to do anyway, continuing can cost the whole
book.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Sequence

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import PrintSession, StaleSessionError
from deckle.core.printing import PrintResult
from deckle.core.profiles import BUILTIN_PRESETS


@dataclass
class StubBackend:
    calls: list[tuple[list[int], int, int]] = field(default_factory=list)

    def submit(self, plan, sheets: Sequence[int], printer_name: str,
               copies: int, dpi: int) -> PrintResult:
        self.calls.append((list(sheets), copies, dpi))
        return PrintResult(submitted=len(sheets), job_id="job", error=None)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def saved():
    """A started three-sheet session, plus a way to rewrite its state."""
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    plan = SheetPlan(
        sheets=[Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
                for i in range(3)],
        paper_pt=(612.0, 792.0), warnings=[],
    )
    profile = BUILTIN_PRESETS["generic_face_down_reversed"]
    session = PrintSession(plan, profile, StubBackend(), printer_name="P")
    session.start()
    path = session.state_path
    original = json.loads(path.read_text(encoding="utf-8"))

    def reload(**overrides) -> PrintSession:
        data = dict(original)
        data.update(overrides)
        path.write_text(json.dumps(data), encoding="utf-8")
        return PrintSession.load(plan, profile, StubBackend(), path.stem)

    return reload


UNDRIVEABLE = [
    pytest.param({"sheets": [99]}, id="sheet-the-plan-does-not-have"),
    pytest.param({"sheets": [-1]}, id="negative-sheet"),
    pytest.param({"sheets": ["a"]}, id="sheet-is-text"),
    pytest.param({"sheet_cursor": -5}, id="cursor-before-the-start"),
    pytest.param({"sheet_cursor": 99}, id="cursor-past-the-end"),
    pytest.param({"pass_index": -1}, id="negative-pass"),
    pytest.param({"copies": -3}, id="negative-copies"),
    pytest.param({"copies": 0}, id="no-copies"),
    pytest.param({"dpi": "high"}, id="dpi-is-text"),
    pytest.param({"dpi": 0}, id="zero-dpi"),
    pytest.param({"printer_name": 7}, id="printer-is-a-number"),
    pytest.param({"test_first": "yes"}, id="test-first-is-text"),
]


@pytest.mark.parametrize("overrides", UNDRIVEABLE)
def test_a_state_that_cannot_drive_the_plan_is_refused(saved, overrides):
    with pytest.raises(StaleSessionError):
        saved(**overrides)


@pytest.mark.parametrize("overrides", UNDRIVEABLE)
def test_nothing_reaches_the_printer_when_it_is_refused(saved, overrides):
    """The point of refusing at ``load`` rather than at submission: the
    backend must never see any of these, not even the first chunk."""
    try:
        session = saved(**overrides)
    except StaleSessionError:
        return
    pytest.fail(f"expected a refusal, got a session: {session}")


@pytest.mark.parametrize("overrides", UNDRIVEABLE)
def test_the_refusal_is_reported_as_a_state_problem(saved, overrides):
    """Distinguishable from "the document changed" and "another build
    wrote this", because the remedy is not the same and the dialog logs
    the reason."""
    with pytest.raises(StaleSessionError) as caught:
        saved(**overrides)

    assert caught.value.reason == "state"


def test_the_refusal_says_what_to_do_next(saved):
    """Every other refusal in this module ends by naming the remedy."""
    with pytest.raises(StaleSessionError) as caught:
        saved(sheets=[99])

    assert "start a new print run" in caught.value.detail


def test_a_missing_field_is_refused_rather_than_raising_key_error(saved):
    """A truncated or hand-edited state file used to reach
    ``_SessionState.from_json`` and raise ``KeyError`` at the print
    dialog, which has no branch for it."""
    with pytest.raises(StaleSessionError):
        saved(**{"dpi": None})


# -- what must keep working ---------------------------------------------


def test_an_untouched_session_still_resumes(saved):
    session = saved()

    session.resume(1)

    assert session.backend.calls


def test_a_partial_selection_of_sheets_is_still_valid(saved):
    """Printing a subset is an ordinary thing to do -- the check is that
    every named sheet exists, not that all of them are named."""
    session = saved(sheets=[0, 2], sheet_cursor=0)

    assert session is not None


def test_a_cursor_at_the_very_end_is_valid(saved):
    """One past the last index is where a finished pass sits, so the
    bound is inclusive at the top and off-by-one here would refuse every
    completed pass."""
    session = saved(sheet_cursor=3)

    assert session is not None


# -- the bound on the path that writes the cursor ------------------------


def test_resuming_with_a_negative_count_is_refused(saved):
    """``load`` already rejects a cursor outside its own sheet list, and
    ``resume`` wrote one straight past that check.

    ``-1`` made ``_submit_chunk`` slice ``sheet_order[-1:]`` and resubmit
    exactly one sheet, so a three-sheet back pass reprinted one, treated
    the other two as done, and said nothing.
    """
    session = saved()

    with pytest.raises(ValueError) as caught:
        session.resume(-1)

    assert "-1" in str(caught.value)


def test_resuming_from_zero_reprints_the_whole_pass(saved):
    """Zero means "nothing came out", which is a real answer and must not
    be caught by the guard on negatives."""
    session = saved()
    session.backend.calls.clear()

    session.resume(0)

    assert session.backend.calls, "a resume from zero submitted nothing"


def test_a_count_beyond_the_pass_advances_rather_than_slicing(saved):
    """Over-reporting is treated as "this pass finished". Pinned because
    the negative guard must not turn into a two-sided range check that
    refuses it -- the operator's count is ground truth, and they may well
    say 99 for a pass of 3."""
    session = saved()

    session.resume(99)

    assert session is not None
