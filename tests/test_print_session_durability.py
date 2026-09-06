"""The print session's state file is the fourth store, and it was the
one written most nearly right.

``PrintSession._save`` already wrote a temp file and renamed it, which is
the shape the other three lacked entirely. What it did not do was
``fsync`` before the rename, and that is the half that matters *here*
more than anywhere else in Deckle. The other stores are written during
ordinary use and their worst realistic failure is a full disk. This one
exists specifically to survive "a crash or a printer disappearing" -- its
own module docstring says so -- and a crash is exactly the case where an
unsynced rename can reach the platter ahead of the content it renames,
leaving a state file of zeros where a resumable job used to be.

Losing it costs paper. Resume is sheet-granular precisely because
reprinting a long job from the start wastes a stack of sheets and a
manual-duplex reload; a state file that cannot be read is that reprint.

The rest of the shape was already right and is pinned here rather than
changed: because the write went to a temp file, a failed save left the
previous state parseable, which is the property the other three stores
lacked. What it did lack is cleanup -- nothing removed the temp file when
the write failed, so a stranded ``.json.tmp`` sat beside the real state
forever, in a directory ``list_resumable`` walks.

``paths.write_text_atomic`` had already solved all of this for the other
stores, so this is now the fourth *caller* rather than the fourth
implementation.
"""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import dataclass, field
from typing import Sequence

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import PrintSession
from deckle.core.printing import PrintResult
from deckle.core.profiles import PrinterProfile


def _make_plan(n_sheets: int) -> SheetPlan:
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    sheets = [
        Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=(612.0, 792.0), warnings=[])


def _profile() -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )


@dataclass
class StubBackend:
    calls: list[tuple[list[int], str, int, int]] = field(default_factory=list)

    def submit(self, plan, sheets: Sequence[int], printer_name: str,
               copies: int, dpi: int, **_kwargs) -> PrintResult:
        self.calls.append((list(sheets), printer_name, copies, dpi))
        return PrintResult(submitted=len(sheets), job_id="job", error=None)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def torn_write():
    """The next file written under this context dies halfway through.

    Armed around one call rather than aimed at one path, so it does not
    encode which file the implementation happens to write -- see the
    2026-08-07 entry on why naming the file makes it a test of the
    mechanism instead of the property.
    """
    real_open = io.open

    @contextlib.contextmanager
    def armed():
        def guarded(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if "w" in mode:
                real_write = handle.write

                def half_then_die(data):
                    real_write(data[: len(data) // 2])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = half_then_die
            return handle

        io.open = guarded
        try:
            yield
        finally:
            io.open = real_open

    return armed


def _session(chunk_size: int = 1) -> PrintSession:
    return PrintSession(
        _make_plan(6), _profile(), StubBackend(),
        printer_name="Test Printer", chunk_size=chunk_size,
    )


def test_a_failed_save_leaves_the_state_file_parseable(torn_write):
    """A state file that will not parse is worse than one that is stale:
    stale resumes from the wrong sheet, unparseable resumes from nothing.
    """
    session = _session()
    session.start()
    before = session.state_path.read_text(encoding="utf-8")

    with torn_write(), pytest.raises(OSError):
        session.advance()

    assert json.loads(session.state_path.read_text(encoding="utf-8"))
    assert session.state_path.read_text(encoding="utf-8") == before


def test_a_failed_save_leaves_no_scratch_file_beside_the_state(torn_write):
    """A stranded temp file sat beside the real state forever, one per
    failed save.

    It was never *listed* -- ``list_resumable`` globs ``*.json`` and
    ``<id>.json.tmp`` does not match, which also makes the ``.tmp`` skip
    that used to sit inside that loop unreachable. So this is litter
    rather than a wrong answer, but litter in a directory the user does
    not own and cannot be expected to sweep.
    """
    session = _session()
    session.start()

    with torn_write(), pytest.raises(OSError):
        session.advance()

    directory = session.state_path.parent
    assert [p.name for p in directory.iterdir()] == [session.state_path.name]


def test_every_session_gets_its_own_state_file():
    """This assertion was written backwards first, and finding out why is
    what turned up the defect.

    The guess was that two sessions of one plan are deliberately one
    resumable job. They are not: ``session_id`` hashed
    ``plan_hash:printer:time.time()``, and ``time.time()`` moves in ~15ms
    steps on Windows -- so the id was neither unique nor stable. Two
    hundred sessions of one plan to one printer produced **five** distinct
    ids, one group of them seventy deep. A shared id is a shared state
    file: one job overwrites another's resume point, and
    ``list_resumable`` reports one job where there are two.

    Two hundred rather than two, because two collide only if the clock
    happens not to tick between them -- which is exactly why the original
    version of this test passed alone and failed in a full run.
    """
    paths = {_session().state_path for _ in range(200)}

    assert len(paths) == 200


def test_a_completed_run_removes_its_state_file():
    """Unchanged behaviour, pinned here because the save path moved."""
    session = _session(chunk_size=10)
    session.start()
    session.advance()

    assert session.finished
    assert not session.state_path.exists()
