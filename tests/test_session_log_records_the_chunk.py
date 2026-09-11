"""The session log keeps the promise its own module docstring makes.

``deckle/core/session_log.py`` opens with a hard constraint, not a
nicety:

    Print failures are the hardest class of bug in this app and are not
    reproducible after the fact, so **every submitted chunk's full
    parameters** get written down.

It recorded five of the eight parameters ``PrintBackend.submit`` is
given. ``copies``, ``side`` and ``rotate_backs`` went missing, and the
last two are the ones that hurt: ``rotate_backs`` is the half turn that
``plan_passes`` derives from the flip axis *and* the paper, and it is the
first thing anyone would want to know about a report that opens "the
backs came out upside down". Nothing in the record could recover it
afterwards without re-running the planner against a profile that may
since have been recalibrated.

Written against the record on disk rather than against a double, because
a double is what let this survive: every stub for ``log_print_job`` in
the suite named exactly the five parameters it had, so the three that
were missing were missing from the tests in the same shape as from the
code.
"""

from __future__ import annotations

import json

import pytest

from deckle.app import backend as backend_mod
from deckle.app.backend import QtPrintBackend
from deckle.core import session_log
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.profiles import BUILTIN_PRESETS

#: Everything ``PrintBackend.submit`` is handed that describes the chunk.
#: ``plan`` is not a parameter of the record -- the sheet indices are what
#: identify it, and the plan itself is reconstructible from the project.
SUBMIT_PARAMETERS = ("printer", "sheets", "copies", "dpi", "side", "rotate_backs")


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)


def _plan(n=3) -> SheetPlan:
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


@pytest.fixture
def backend():
    """A real ``QtPrintBackend`` with only the painting stubbed out.

    The real ``submit`` -- so the real call to ``log_print_job``, with
    whatever it really passes.
    """

    class Recording(QtPrintBackend):
        def _submit_chunk(self, plan, sheets, printer_name, copies, dpi,
                          side, rotate_backs):
            return None

    return Recording(BUILTIN_PRESETS["generic_face_down_reversed"])


def _records() -> list[dict]:
    path = session_log.session_log_path()
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_a_back_pass_chunk_records_every_parameter_it_was_given(backend):
    """The back pass, because that is the one with something to get wrong."""
    backend.submit(
        _plan(), [2, 1, 0], "Brother HL-L2350DW", copies=3, dpi=600,
        side="back", rotate_backs=True, pass_index=1,
    )

    record = _records()[-1]

    assert record["printer"] == "Brother HL-L2350DW"
    assert record["sheets"] == [2, 1, 0]
    assert record["copies"] == 3
    assert record["dpi"] == 600
    assert record["side"] == "back"
    assert record["rotate_backs"] is True
    assert record["pass_index"] == 1


def test_the_half_turn_is_recorded_and_not_merely_implied(backend):
    """``rotate_backs`` is not a function of ``side`` -- ``plan_passes``
    decides it from the flip axis *and* the paper, and it comes out
    ``False`` for a back pass on portrait paper with a long-edge flip.
    A reader inferring it from ``side`` would get it exactly backwards
    for the default preset on the default paper."""
    backend.submit(
        _plan(), [0], "P", copies=1, dpi=300,
        side="back", rotate_backs=False, pass_index=1,
    )

    record = _records()[-1]

    assert record["side"] == "back"
    assert record["rotate_backs"] is False


def test_no_parameter_of_submit_is_missing_from_the_record(backend):
    """The promise, checked as a promise rather than field by field.

    Every value ``submit`` is given about the chunk must appear in the
    record. Stated this way so that a parameter added to ``submit``
    tomorrow and not added here fails *this* test, which is the one whose
    docstring explains why it matters.
    """
    backend.submit(
        _plan(), [0, 1], "P", copies=2, dpi=150,
        side="front", rotate_backs=False, pass_index=0,
    )

    record = _records()[-1]
    missing = [name for name in SUBMIT_PARAMETERS if name not in record]

    assert not missing, (
        f"the session log omits {missing} -- its module docstring promises "
        "every submitted chunk's full parameters, and print failures are "
        "not reproducible after the fact"
    )


def test_the_default_call_still_works_for_the_older_callers(tmp_path):
    """Three call sites pass five positional arguments and none of them
    are about copies or sides. Defaulted rather than required so they
    keep working, and pinned so the defaults are not quietly dropped."""
    session_log.log_print_job(
        "P", BUILTIN_PRESETS["generic_face_down_reversed"], [0], 300, 0
    )

    record = _records()[-1]

    assert record["copies"] == 1
    assert record["side"] == "front"
    assert record["rotate_backs"] is False
