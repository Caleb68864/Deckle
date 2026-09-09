"""A print that reached paper but could not be written down.

``log_print_job`` is deliberately not best-effort. Its docstring says so:
unlike :mod:`deckle.core.diagnostics`, the session log is a hard
constraint -- print failures are the least reproducible class of bug in
this program, so a submitted chunk that goes unrecorded is not something
to shrug at. Raising rather than swallowing is the right instinct.

The call sat **outside** the ``try`` that turns print failures into a
``PrintResult``, and that produced three separate wrongs from one
unguarded line. Measured on a three-sheet plan with a log that raises
``OSError(28)``:

- **The paper had already come out.** All three sheets were painted
  before the log was touched.
- **The exception escaped ``session.start()``** and reached the print
  dialog, which does not catch it -- so a traceback, from a program whose
  CLI docstring draws exactly this line. ``submit``'s own docstring
  promises "a print failure is reported through ``error`` rather than
  raised", and this walked past that promise.
- **The session cursor stayed at 0**, because nothing extended
  ``submitted_sheets``. Resume would reprint all three: a second stack of
  paper, and a mis-collated one if the operator had already reloaded.

The fix keeps the constraint and drops the traceback. A logging failure
is reported through ``PrintResult.error`` -- visible, logged, and it
still stops the run -- with ``submitted`` counting the sheets that
physically printed, because they did.

**What is deliberately unchanged: a logging failure still stops the
run.** That was the previous behaviour (by exception) and it stays the
behaviour (by error). Whether it *should* is a policy question about the
Intent doc's constraint, not something to settle while fixing a
traceback.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import pytest

from deckle.app import backend as backend_mod
from deckle.app.backend import QtPrintBackend
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core import print_session as session_mod
from deckle.core.print_session import PrintSession
from deckle.core.profiles import BUILTIN_PRESETS


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)


def _plan(n=3) -> SheetPlan:
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    return SheetPlan(
        sheets=[Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
                for i in range(n)],
        paper_pt=(612.0, 792.0), warnings=[],
    )


@pytest.fixture
def backend(monkeypatch):
    """A backend that records what reached paper and never touches Qt."""
    printed: list[int] = []

    class Recording(QtPrintBackend):
        def _submit_chunk(self, plan, sheets, printer_name, copies, dpi,
                          side, rotate_backs):
            printed.extend(sheets)

    instance = Recording(BUILTIN_PRESETS["generic_face_down_reversed"])
    instance.printed = printed
    return instance


@pytest.fixture
def failing_log(monkeypatch):
    def explode(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backend_mod, "log_print_job", explode)


def test_a_logging_failure_is_reported_not_raised(backend, failing_log):
    """``submit`` promises errors come back through ``error``."""
    result = backend.submit(
        _plan(), [0, 1, 2], "P", copies=1, dpi=300,
        side="front", rotate_backs=False, pass_index=0,
    )

    assert result.error is not None


def test_the_message_says_the_sheets_did_print(backend, failing_log):
    """The single most important thing to tell someone standing at a
    printer: this is not "the print failed", it is "the print worked and
    the record did not"."""
    result = backend.submit(
        _plan(), [0, 1, 2], "P", copies=1, dpi=300,
        side="front", rotate_backs=False, pass_index=0,
    )

    assert "printed" in result.error.lower()
    assert "record" in result.error.lower() or "log" in result.error.lower()


def test_the_sheets_that_printed_are_counted_as_submitted(backend, failing_log):
    """``submitted`` describes paper, and the paper came out."""
    result = backend.submit(
        _plan(), [0, 1, 2], "P", copies=1, dpi=300,
        side="front", rotate_backs=False, pass_index=0,
    )

    assert backend.printed == [0, 1, 2]
    assert result.submitted == 3


def test_nothing_escapes_the_print_session(backend, failing_log):
    """The end-to-end shape: the dialog calls ``session.start()`` with no
    ``try`` around it, so anything escaping here is a traceback in the
    user's face."""
    session = PrintSession(_plan(), backend.profile, backend,
                           printer_name="P", chunk_size=10)

    session.start()

    assert session.last_error is not None


def test_a_working_log_still_records_and_reports_success(backend, monkeypatch):
    """The guard must not turn every print into a failure."""
    recorded: list[tuple] = []
    monkeypatch.setattr(
        backend_mod, "log_print_job",
        lambda printer, profile, sheets, dpi, pass_index:
            recorded.append((printer, list(sheets), dpi, pass_index)),
    )

    result = backend.submit(
        _plan(), [0, 1, 2], "P", copies=1, dpi=300,
        side="front", rotate_backs=False, pass_index=0,
    )

    assert result.error is None
    assert result.submitted == 3
    assert recorded == [("P", [0, 1, 2], 300, 0)]


def test_a_real_print_failure_still_reports_nothing_submitted(backend, monkeypatch):
    """The other branch must keep its own meaning: when painting fails,
    no paper came out and ``submitted`` is 0. A fix that reported the
    sheet count unconditionally would break this."""
    def explode(*args, **kwargs):
        raise RuntimeError("printer offline")

    # Patched on the fixture's own class, not on ``QtPrintBackend``: the
    # fixture overrides ``_submit_chunk`` to record what reached paper, so
    # patching the base class would be shadowed and the test would assert
    # against an ordinary successful print.
    monkeypatch.setattr(type(backend), "_submit_chunk", explode)

    result = backend.submit(
        _plan(), [0, 1, 2], "P", copies=1, dpi=300,
        side="front", rotate_backs=False, pass_index=0,
    )

    assert result.submitted == 0
    assert "offline" in result.error


# -- exactly one writer ----------------------------------------------------


def test_a_chunk_reaches_the_session_log_once(backend, monkeypatch):
    """The record is per chunk of paper, so it is written once per chunk.

    ``PrintSession`` used to log every chunk a second time itself, straight
    after handing it to the backend. Two entries per chunk is wrong on its
    own -- the log is what a reprint decision gets made from -- but the
    duplicate was also the unguarded one, which is the rest of this file's
    subject.
    """
    recorded: list[tuple] = []

    def record(printer, profile, sheets, dpi, pass_index):
        recorded.append((printer, tuple(sheets), pass_index))

    # Both namespaces, deliberately. `log_print_job` is imported *into* each
    # module that calls it, so patching one rebinds one name and leaves any
    # other caller running the real logger -- silently, into the session
    # state dir. Patching only `backend_mod` is why the duplicate survived
    # having a test file this thorough written about it. `raising=False`
    # because the session is not supposed to have the symbol at all any
    # more: if it reappears, this patch catches its calls rather than
    # erroring, and the count below is what fails.
    monkeypatch.setattr(backend_mod, "log_print_job", record)
    monkeypatch.setattr(session_mod, "log_print_job", record, raising=False)

    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10)

    session.start()

    assert len(recorded) == 1, f"one chunk, one record -- got {recorded}"
    assert recorded[0] == ("P", (0, 1, 2), 0)


def test_an_unwritable_log_reports_through_the_session_without_raising(backend, failing_log):
    """Driven through the session, which is where the duplicate call lived.

    Deleting the session's own unguarded ``log_print_job`` leaves exactly
    one writer, inside the backend's ``try``. The failure now arrives as
    ``PrintResult.error`` and is reported on ``last_error``; nothing
    escapes ``start()``.
    """
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10)

    session.start()

    assert session.last_error is not None, "the failure is still reported"
    assert backend.printed == [0, 1, 2], "and the paper still came out"


# -- the cursor follows the paper (B37) -----------------------------------
#
# This section was a strict `xfail` until 2026-09-09. `PrintResult.submitted`
# was set carefully on both branches of `submit` to say how many sheets
# physically printed, and no caller read it: `_submit_chunk` returned on
# `result.error` before touching `sheet_cursor`, so a chunk that printed and
# then failed to log left the cursor behind it and a resume fed that paper
# through a second time.
#
# It stayed open because the obvious edit -- advance by `result.submitted` --
# is a guess about the output tray, and advancing past a chunk that only
# partly printed is worse than reprinting it. The owner's answer removed the
# guess: ask the operator, who can see the tray. `ask_sheets_printed` is the
# seam, the app's dialog is the default implementation, and the answer lands
# on `sheet_cursor`.


def _answered(count):
    """An operator who says ``count``, recording what they were asked."""
    asked: list = []

    def ask(question):
        asked.append(question)
        return count

    ask.asked = asked
    return ask


def test_the_cursor_follows_the_paper_not_the_bookkeeping(backend, failing_log):
    """The sheets came out, the operator says so, and the cursor moves."""
    operator = _answered(3)
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=operator)

    session.start()

    assert session._state.sheet_cursor == 3
    assert session.last_error is not None, "the run still stops"


def test_the_operator_is_told_what_happened_before_being_asked(backend, failing_log):
    """The question carries the situation, not just a blank number field."""
    operator = _answered(3)
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=operator)

    session.start()

    assert len(operator.asked) == 1
    question = operator.asked[0]
    assert question.submitted == 3, "how many were sent"
    assert question.sheets == (0, 1, 2)
    assert question.side == "front" and question.pass_index == 0
    assert question.printer_name == "P"
    assert "record" in question.error.lower(), "why it stopped"


def test_the_operator_can_say_fewer_came_out_than_were_sent(backend, failing_log):
    """The whole reason for asking. Two of three in the tray means two
    behind the cursor -- the third gets printed again, which is right."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=_answered(2))

    session.start()

    assert session._state.sheet_cursor == 2


def test_declining_to_answer_leaves_the_cursor_where_it_was(backend, failing_log):
    """``None`` is "I would rather not say", which is not "none came out"
    but is treated as the same *cursor*: reprinting costs paper and
    skipping costs the book. The offer comes back on resume."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=lambda question: None)

    session.start()

    assert session._state.sheet_cursor == 0


def test_with_nobody_to_ask_the_cursor_does_not_move(backend, failing_log):
    """No seam injected means no operator. The session does not fill the
    silence with `result.submitted` -- that is the guess this was opened
    rather than made."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10)

    session.start()

    assert session._state.sheet_cursor == 0


def test_an_answer_larger_than_the_chunk_is_clamped_not_raised(backend, failing_log):
    """More sheets cannot come out than went in. `start()` promises not to
    raise, so a nonsense count is clamped rather than refused."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=_answered(99))

    session.start()

    assert session._state.sheet_cursor == 3


def test_a_negative_answer_is_clamped_to_nothing(backend, failing_log):
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=_answered(-4))

    session.start()

    assert session._state.sheet_cursor == 0


def test_an_offline_printer_is_not_a_question_about_paper(backend, monkeypatch):
    """The other branch. Painting failed, so `submitted` is 0 and nothing
    is known to have come out -- there is nothing to count and nobody is
    interrupted to count it. Asking here would train the operator to
    answer a question the program cannot use."""
    def explode(*args, **kwargs):
        raise RuntimeError("printer offline")

    monkeypatch.setattr(type(backend), "_submit_chunk", explode)
    operator = _answered(3)
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=operator)

    session.start()

    assert operator.asked == [], "no paper is known to have come out"
    assert session._state.sheet_cursor == 0


def test_a_stalled_chunk_never_walks_on_into_the_back_pass(backend, failing_log):
    """Counting the whole chunk fills the front pass, and the pass index
    must still not move. The run is stopped; printing backs against fronts
    that are still an open question is the ruined-stack failure."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=_answered(3))

    session.start()

    assert session._state.sheet_cursor == 3
    assert session._state.pass_index == 0, "still on the fronts"
    assert session.finished is False


def test_the_counted_cursor_survives_to_disk(backend, failing_log):
    """The answer has to outlive the process, because the point of it is
    the resume -- which may be after a crash, or tomorrow."""
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           ask_sheets_printed=_answered(2))

    session.start()

    import json

    stored = json.loads(session.state_path.read_text(encoding="utf-8"))
    assert stored["sheet_cursor"] == 2


def test_a_test_sheet_that_printed_is_counted_too(backend, failing_log):
    """One sheet in the tray is still a sheet in the tray."""
    operator = _answered(1)
    session = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10,
                           test_first=True, ask_sheets_printed=operator)

    session.start()

    assert operator.asked[0].submitted == 1
    assert session._state.sheet_cursor == 1


def test_a_resumed_session_can_still_ask(backend, failing_log):
    """`load` rebuilds the session by hand, field by field, so the seam is
    exactly the kind of thing that gets left off there and is then missing
    only on the path a resume takes."""
    working = PrintSession(_plan(3), backend.profile, backend,
                           printer_name="P", chunk_size=10)
    working._save()
    session_id = working.state["session_id"]

    operator = _answered(3)
    resumed = PrintSession.load(
        _plan(3), backend.profile, backend, session_id,
        ask_sheets_printed=operator,
    )
    resumed.resume(0)

    assert operator.asked, "the resumed session asked"
    assert resumed._state.sheet_cursor == 3
