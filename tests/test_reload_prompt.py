"""The operator has to be told to turn the paper over.

Manual duplex is the premise of this program: print the fronts, stop, tell
the person at the printer exactly how to reverse and reload the stack, then
print the backs. ``plan_passes`` computes that sentence and
``PrintSession.reload_instruction`` hands it over. ``PrintDialog._drive``
was the only thing in the GUI that showed it -- and it detected the moment
by watching ``pass_index`` change *inside its own loop*.

``PrintSession.start()`` submits chunks until the pass is exhausted and
then advances the pass itself. On any plan whose fronts fit in one chunk --
``DEFAULT_CHUNK_SIZE`` is 10 -- the boundary is already behind the session
when ``_drive`` takes its first reading, the comparison is ``1 != 1``, and
the backs go through the machine with nobody asked to do anything.

**A 40-page folio book is exactly ten sheets, and every "Signature N"
reprint is four to eight.** Those are not edge cases; they are the jobs
Deckle exists for, and the result is the failure the whole manual-duplex
design is built to prevent.

Why this file drives a **real** ``PrintSession``: every existing
``PrintDialog`` test injects a stub session whose ``start()`` leaves
``pass_index`` at ``0``, which the real one does not do. The dialog tests
were green, the session tests were green, and the paper would still have
been wrong -- the same shape as B36, where a Protocol narrower than its
implementation let every double reproduce the gap. So the session here is
the real class and only the *printer* is a stand-in.
"""

from __future__ import annotations

from typing import Literal, Sequence

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import DEFAULT_CHUNK_SIZE, PrintSession
from deckle.core.printing import PrintResult
from deckle.core.profiles import PrinterProfile


@pytest.fixture(scope="module")
def qapp():
    """Only the two tests that build a real ``PrintDialog`` need this."""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path / "logs"))


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


class RecordingBackend:
    """The printer, and only the printer."""

    def __init__(self, profile=None):
        self.profile = profile
        self.chunks: list[tuple[list[int], str, int]] = []

    def submit(self, plan, sheets: Sequence[int], printer_name: str, copies: int,
               dpi: int, *, side: Literal["front", "back"] = "front",
               rotate_backs: bool = False, pass_index: int = 0) -> PrintResult:
        self.chunks.append((list(sheets), side, pass_index))
        return PrintResult(submitted=len(sheets), job_id=None, error=None)


class _Drive:
    """Everything ``PrintDialog._drive`` reads off ``self``, and nothing else.

    ``_drive`` itself is the real method, taken off the class unbound. The
    injected seams are the same ones ``PrintDialog.__init__`` exposes for
    exactly this: a modal cannot be answered in a test process.
    """

    def __init__(self):
        self.reloads: list[str] = []
        self.statuses: list[str] = []
        self.offline: list[tuple[str, str]] = []
        self.test_sheet_questions = 0

    def drive(self, session):
        # Resolved here rather than at import time so this module stays
        # importable without PySide6, like `print_dialog` itself.
        from deckle.app.views.print_dialog import PrintDialog

        PrintDialog._drive(self, session)

    def _confirm_reload(self, instruction):
        self.reloads.append(instruction)

    def _confirm_test_sheet(self):
        self.test_sheet_questions += 1
        return True

    def _show_offline_error(self, printer_name, error):
        self.offline.append((printer_name, error))

    class _Label:
        def __init__(self, sink):
            self._sink = sink

        def setText(self, text):
            self._sink.append(text)

    @property
    def status_label(self):
        return _Drive._Label(self.statuses)


def _run(n_sheets: int, *, test_first: bool = False):
    plan = _make_plan(n_sheets)
    backend = RecordingBackend(_profile())
    session = PrintSession(
        plan, _profile(), backend, printer_name="Test Printer",
        test_first=test_first,
    )
    session.start()
    pass_index_after_start = session.state["pass_index"]
    driver = _Drive()
    driver.drive(session)
    return session, backend, driver, pass_index_after_start


# -- the premise, asserted before anything is concluded from it -----------


def test_a_small_plans_front_pass_is_already_over_when_the_dialog_starts_driving():
    """The mechanism, stated as a fact about the real session.

    Without this, a green result below could come from a plan whose fronts
    never finished rather than from the dialog doing its job.
    """
    _, _, _, pass_index = _run(DEFAULT_CHUNK_SIZE)
    assert pass_index == 1, (
        "PrintSession.start() no longer crosses the pass boundary on a "
        "single-chunk plan, so this file is measuring something else"
    )
    _, _, _, big = _run(DEFAULT_CHUNK_SIZE + 1)
    assert big == 0, "a plan that needs two chunks should still be on pass 0"


# -- the defect ------------------------------------------------------------


@pytest.mark.parametrize("n_sheets", [1, 2, 4, 8, DEFAULT_CHUNK_SIZE])
def test_a_job_that_fits_in_one_chunk_still_asks_the_operator_to_reload(n_sheets):
    """The jobs this program is for. A 40-page folio book is ten sheets."""
    session, backend, driver, _ = _run(n_sheets)

    backs = [chunk for chunk in backend.chunks if chunk[1] == "back"]
    assert backs, "the run never reached the back pass; nothing to conclude"
    assert driver.reloads, (
        f"{n_sheets} sheet(s): the backs were submitted with the operator "
        "never asked to reverse and reload the stack"
    )


@pytest.mark.parametrize("n_sheets", [1, 4, DEFAULT_CHUNK_SIZE,
                                      DEFAULT_CHUNK_SIZE + 1, 25])
def test_the_operator_is_asked_exactly_once_whatever_the_job_size(n_sheets):
    """One reload, because there is one reload. Asking twice for a job of
    twenty-five sheets would be the fix overshooting into a different lie."""
    _, backend, driver, _ = _run(n_sheets)

    assert len([c for c in backend.chunks if c[1] == "back"]) >= 1
    assert len(driver.reloads) == 1, driver.reloads


def test_the_instruction_shown_is_the_one_plan_passes_computed():
    """Shown verbatim; the dialog never composes its own. It is the single
    most consequential sentence Deckle prints."""
    from deckle.core.printing import plan_passes

    plan = _make_plan(4)
    profile = _profile()
    expected = plan_passes(plan, profile)[1].reload_instruction

    _, _, driver, _ = _run(4)

    assert driver.reloads == [expected]
    assert "reverse the printed stack" in expected


def test_the_reload_question_comes_before_the_backs_are_submitted():
    """Order is the whole point: asking afterwards is a description of a
    ruined stack rather than a chance to prevent one."""
    plan = _make_plan(4)
    backend = RecordingBackend(_profile())
    session = PrintSession(plan, _profile(), backend, printer_name="Test Printer")
    session.start()

    driver = _Drive()
    backs_when_asked = []

    def watch(instruction):
        backs_when_asked.append(
            len([c for c in backend.chunks if c[1] == "back"])
        )
        driver.reloads.append(instruction)

    driver._confirm_reload = watch
    driver.drive(session)

    assert backs_when_asked == [0], (
        "the operator was asked to reload after the back pass had already "
        "been submitted"
    )


def test_test_one_sheet_first_does_not_swallow_the_reload_question():
    """The third way in. With "Test one sheet first" ticked, `start()`
    submits one sheet and stops; confirming it submits the rest of the
    chunk, which on a small plan finishes the front pass and advances --
    again before `_drive` samples anything."""
    session, backend, driver, _ = _run(8, test_first=True)

    assert driver.test_sheet_questions == 1, "the test sheet was not offered"
    assert [c for c in backend.chunks if c[1] == "back"], "no back pass"
    assert driver.reloads, (
        "the test-sheet path lost the reload question as well"
    )


# -- and the cases that must NOT gain a prompt ----------------------------


def test_resuming_mid_back_pass_does_not_ask_again():
    """The stack is already in the machine. A second reload instruction
    there tells the operator to turn over paper that is half printed.

    The interruption is manufactured the way a real one happens: the
    printer refuses a chunk part-way through the back pass, which leaves
    the session on disk with a non-zero cursor on pass 1.
    """
    class FailsOnCall(RecordingBackend):
        def __init__(self, profile, fail_at):
            super().__init__(profile)
            self.fail_at = fail_at

        def submit(self, plan, sheets, printer_name, copies, dpi, **kwargs):
            index = len(self.chunks)
            result = super().submit(plan, sheets, printer_name, copies, dpi, **kwargs)
            if index == self.fail_at:
                self.chunks.pop()
                return PrintResult(submitted=0, job_id=None, error="printer offline")
            return result

    plan = _make_plan(25)
    # Front: chunks 0,1,2 (10/10/5). Back: chunk 3 goes, chunk 4 refuses.
    backend = FailsOnCall(_profile(), fail_at=4)
    session = PrintSession(plan, _profile(), backend, printer_name="Test Printer")
    session.start()
    driver = _Drive()
    driver.drive(session)

    assert len(driver.reloads) == 1, "the first run's own reload question"
    assert session.last_error is not None, "the run did not stop where intended"
    assert session.state["pass_index"] == 1
    assert session.state["sheet_cursor"] > 0, (
        "the interruption did not land mid-pass, so this test says nothing"
    )

    resumed = PrintSession.load(
        plan, _profile(), RecordingBackend(_profile()),
        session.state["session_id"],
    )
    resumed.resume(session.state["sheet_cursor"])
    second = _Drive()
    second.drive(resumed)

    assert second.reloads == [], (
        "a resume that lands mid-pass asked the operator to reload a stack "
        "that is already in the machine"
    )


class RefusesTheBacks(RecordingBackend):
    """A printer that takes the fronts and then goes offline."""

    def submit(self, plan, sheets, printer_name, copies, dpi, **kwargs):
        if kwargs.get("side") == "back":
            return PrintResult(submitted=0, job_id=None, error="printer offline")
        return super().submit(plan, sheets, printer_name, copies, dpi, **kwargs)


def _dialog_resuming(plan, reloads, count):
    """A real ``PrintDialog`` driving a real ``PrintSession`` through the
    real ``_offer_resume``. Only the modals and the printer are injected.

    Constructing it *is* the resume: ``__init__`` offers any interrupted
    run it finds (``print_dialog.py:552-555``), which is how the operator
    meets this path -- they reopen Print and are asked. Nothing here calls
    ``_offer_resume`` a second time.
    """
    from deckle.app.views.print_dialog import PrintDialog

    return PrintDialog(
        plan,
        printer_names=["Test Printer"],
        profile_loader=lambda name: _profile(),
        session_cls=PrintSession,
        backend_cls=RecordingBackend,
        resumable_lister=PrintSession.list_resumable,
        confirm_resume=lambda summaries: summaries[0],
        ask_resume_count=lambda summary: count,
        confirm_reload=reloads.append,
        confirm_test_sheet=lambda: True,
        show_offline_error=lambda printer, error: None,
    )


def test_resuming_exactly_at_the_start_of_the_back_pass_does_ask(qapp):
    """The other side of the same rule, and the path ``_drive`` cannot
    cover on its own.

    Fronts finished, nothing of the backs has gone, and the run stopped in
    between. ``PrintSession.resume`` submits the first chunk **itself**, so
    by the time ``_drive`` gets the session the paper is already moving --
    the question has to be asked before ``resume``, not after it.
    """
    plan = _make_plan(25)
    session = PrintSession(plan, _profile(), RefusesTheBacks(_profile()),
                           printer_name="Test Printer")
    session.start()
    _Drive().drive(session)

    assert session.state["pass_index"] == 1
    assert session.state["sheet_cursor"] == 0, (
        "the run did not stop at the pass boundary, so this test says nothing"
    )

    reloads: list[str] = []
    dialog = _dialog_resuming(plan, reloads, count=0)

    assert dialog._session is not None, "the dialog did not resume anything"
    assert reloads, (
        "a run resumed at the pass boundary printed its backs with the "
        "operator never told to reverse and reload the stack"
    )


def test_resuming_part_way_through_the_backs_is_not_asked_again(qapp):
    """Same path, the other answer. The operator says some of the backs
    came out, so the stack is in the machine and must not be turned."""
    plan = _make_plan(25)

    class FailsOnTheSecondBackChunk(RecordingBackend):
        def submit(self, plan, sheets, printer_name, copies, dpi, **kwargs):
            if kwargs.get("side") == "back" and any(
                c[1] == "back" for c in self.chunks
            ):
                return PrintResult(submitted=0, job_id=None, error="printer offline")
            return super().submit(plan, sheets, printer_name, copies, dpi, **kwargs)

    session = PrintSession(plan, _profile(), FailsOnTheSecondBackChunk(_profile()),
                           printer_name="Test Printer")
    session.start()
    _Drive().drive(session)

    assert session.state["pass_index"] == 1
    assert session.state["sheet_cursor"] > 0, (
        "the run did not stop mid-pass, so this test says nothing"
    )

    reloads: list[str] = []
    dialog = _dialog_resuming(plan, reloads, count=session.state["sheet_cursor"])

    assert dialog._session is not None, "the dialog did not resume anything"
    assert reloads == [], (
        "the operator was told to reload a stack already half printed"
    )


def test_the_front_pass_instruction_is_not_shown_after_the_fronts_have_gone():
    """`pass_index == 0`'s instruction is "load paper face down ... and
    print pass 1 (fronts)" -- advice about a run that has already started
    by the time `_drive` can say it. Showing it then would be worse than
    silence, so the rule is deliberately about later passes only."""
    from deckle.app.views.print_dialog import pass_needs_reloading_first

    assert pass_needs_reloading_first(
        {"pass_index": 0, "sheet_cursor": 0}
    ) is False
    assert pass_needs_reloading_first(
        {"pass_index": 1, "sheet_cursor": 0}
    ) is True
    assert pass_needs_reloading_first(
        {"pass_index": 1, "sheet_cursor": 3}
    ) is False


def test_the_rule_reads_the_cursor_rather_than_defaulting_it():
    """A state dict that has lost `sheet_cursor` must fail loudly here.
    Tolerating it with `.get(..., 0)` is how a double that omits a field
    silently decides the answer -- which is the B36 shape, and it is the
    shape this whole file is about."""
    from deckle.app.views.print_dialog import pass_needs_reloading_first

    with pytest.raises(KeyError):
        pass_needs_reloading_first({"pass_index": 1})
