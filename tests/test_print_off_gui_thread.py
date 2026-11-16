"""B17's last clause: a print run is painted and spooled off the GUI thread.

No printer here, so ``QtPrintBackend`` is replaced by fake backends and the
threads are real: the dialog is built with its production ``job_runner``
(:func:`deckle.app.workers.run_in_thread`), a real ``PrintSession`` and
small chunks, so a run crosses chunk and pass boundaries.

What these pin:

* the GUI thread keeps running while a chunk is in the printer;
* every question the run asks is asked on the GUI thread, in the order it
  was asked when the whole run was on the GUI thread;
* a failure reads exactly as it did (H5/H7/H14's text rules), and an
  exception still reaches the GUI thread;
* closing the dialog, or the window, stops the run after the chunk in the
  printer, waits for it, and leaves the run resumable -- including while the
  run is waiting on a question nobody will now answer.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import PrintSession, result_failure_text
from deckle.core.printing import PrintResult
from deckle.core.profiles import PrinterProfile


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path / "logs"))


def _plan(n_sheets: int) -> SheetPlan:
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    return SheetPlan(
        sheets=[
            Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
            for i in range(n_sheets)
        ],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )


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


class _TwoSheetChunks(PrintSession):
    """The real session, with chunks small enough to have several."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("chunk_size", 2)
        super().__init__(*args, **kwargs)


def _on_gui() -> bool:
    return threading.current_thread() is threading.main_thread()


class _Printer:
    """A fake ``QtPrintBackend``: records each chunk, the thread it came on,
    and can be told to hold a chunk, fail one, or raise."""

    #: Set by a test before the dialog constructs its backend.
    script: dict = {}
    events: list = []
    entered = None
    release = None

    def __init__(self, profile):
        self.profile = profile

    def submit(self, plan, sheets, printer_name, copies, dpi, *, side="front",
               rotate_backs=False, pass_index=0):
        cls = type(self)
        n = sum(1 for event in cls.events if event[0] == "submit")
        cls.events.append(("submit", side, list(sheets), _on_gui()))
        step = cls.script.get(n)
        if step == "hold":
            cls.entered.set()
            # Bounded, so the old, synchronous dialog fails the test rather
            # than hanging it.
            cls.release.wait(3.0)
        elif isinstance(step, PrintResult):
            return step
        elif isinstance(step, BaseException):
            raise step
        return PrintResult(submitted=len(sheets), job_id=None, error=None)

    def submit_proof(self, plan, sheet_index, printer_name, dpi):
        cls = type(self)
        cls.events.append(("proof", sheet_index, _on_gui()))
        cls.entered.set()
        cls.release.wait(3.0)
        step = cls.script.get("proof")
        return step or PrintResult(submitted=1, job_id=None, error=None)


@pytest.fixture
def printer():
    _Printer.script = {}
    _Printer.events = []
    _Printer.entered = threading.Event()
    _Printer.release = threading.Event()
    yield _Printer
    _Printer.release.set()


def _dialog(printer, *, test_first=False, n_sheets=5, **kwargs):
    from deckle.app.views.print_dialog import PrintDialog

    def record(name):
        def prompt(*args):
            printer.events.append((name, *args, _on_gui()))
            return True if name == "test_sheet" else None

        return prompt

    defaults = dict(
        printer_names=["Fake"],
        profile_loader=lambda name: _profile(),
        session_cls=_TwoSheetChunks,
        backend_cls=printer,
        resumable_lister=lambda: [],
        confirm_reload=record("reload"),
        confirm_test_sheet=record("test_sheet"),
        show_offline_error=record("offline"),
        ask_sheets_printed=lambda question: printer.events.append(
            ("sheets_printed", question.submitted, question.error, _on_gui())
        ) or question.submitted,
    )
    defaults.update(kwargs)
    # No `job_runner`: the production default is what is under test.
    dialog = PrintDialog(_plan(n_sheets), **defaults)
    dialog.test_first_checkbox.setChecked(test_first)
    original = dialog.status_label.setText

    def status(text):
        printer.events.append(("status", text, _on_gui()))
        original(text)

    dialog.status_label.setText = status
    return _watch(dialog)


#: The objects whose posted events these tests deliver: each dialog's
#: ``GuiCaller`` bridge and each job's ``QThread``.
_RECEIVERS: list = []


@pytest.fixture(autouse=True)
def _fresh_receivers():
    # A previous test's dialog, and its threads, may be gone.
    _RECEIVERS.clear()
    yield
    _RECEIVERS.clear()


def _watch(dialog_or_caller):
    gui = getattr(dialog_or_caller, "_gui", dialog_or_caller)
    if hasattr(gui, "_bridge"):
        _RECEIVERS.append(gui._bridge)
    return dialog_or_caller


def _pump_until(condition, timeout_s=10.0, dialogs=()):
    """Deliver this file's own queued calls until ``condition()`` holds.

    Never ``processEvents()``: in the full suite that also delivers events
    other tests left posted to objects whose wrappers have gone, and
    segfaults (as ``test_off_thread_jobs`` notes for deferred deletes).
    """
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for dialog in dialogs:
            job = getattr(dialog, "_print_job", None)
            thread = getattr(job, "thread", None)
            if thread is not None and thread not in _RECEIVERS:
                _RECEIVERS.append(thread)
        for receiver in list(_RECEIVERS):
            try:
                QCoreApplication.sendPostedEvents(receiver, 0)
            except RuntimeError:  # a finished job's thread, deleted by now
                _RECEIVERS.remove(receiver)
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def _settled(dialog):
    # `getattr`: the synchronous dialog had no job, and is always settled.
    return getattr(dialog, "_print_job", None) is None


# -- responsiveness -------------------------------------------------------


def test_the_gui_thread_runs_while_a_chunk_is_in_the_printer(printer):
    printer.script = {0: "hold"}
    dialog = _dialog(printer)

    began = time.monotonic()
    dialog.start_print()
    returned_after = time.monotonic() - began

    assert printer.entered.wait(5.0)
    # The chunk is still being "spooled" and the GUI thread is free.
    assert returned_after < 1.0
    assert not dialog.print_button.isEnabled()
    # Editing the Sheets box mid-run does not hand a second run the button.
    dialog.sheets_edit.setText("0-1")
    assert not dialog.print_button.isEnabled()
    dialog.sheets_edit.setText("")
    # Still held, on another thread, with the GUI thread back here.
    assert not printer.release.is_set()
    assert printer.events[0][-1] is False

    printer.release.set()
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])
    submits = [event for event in printer.events if event[0] == "submit"]
    assert submits and not any(on_gui for *_, on_gui in submits)
    assert printer.events[-1] == ("status", "Print job complete.", True)
    assert dialog.print_button.isEnabled()


@pytest.mark.parametrize("error", [None, ""], ids=["sent", "failed-without-a-message"])
def test_a_proof_sheet_is_spooled_off_the_gui_thread_too(printer, error):
    if error is not None:
        printer.script = {"proof": PrintResult(submitted=0, job_id=None, error=error)}
    dialog = _dialog(printer)
    dialog.proof_checkbox.setChecked(True)

    began = time.monotonic()
    dialog.start_print()
    assert printer.entered.wait(5.0)
    assert time.monotonic() - began < 1.0
    printer.release.set()
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])

    assert printer.events[0] == ("proof", 0, False)
    said = printer.events[-1]
    assert said[-1] is True  # on the GUI thread
    if error is None:
        assert said[0] == "status" and said[1].startswith("Proof of sheet 0 sent")
    else:
        assert said == (
            "offline", "Fake",
            result_failure_text(PrintResult(submitted=0, job_id=None, error="")), True,
        )


# -- order ----------------------------------------------------------------

# What the GUI-thread dialog asked, in order, for five sheets in chunks of
# two with "Test one sheet first": recorded from the dialog before this
# change, where the run was synchronous. Fronts go out in reverse for this
# face-down, reversed-stack profile.
EXPECTED_ORDER = [
    ("submit", "front"),
    ("test_sheet",),
    ("submit", "front"),
    ("submit", "front"),
    ("reload", "<instruction>"),
    ("submit", "back"),
    ("submit", "back"),
    ("submit", "back"),
    ("status", "Print job complete."),
]


def _shape(event):
    if event[0] == "submit":
        return ("submit", event[1])
    if event[0] == "reload":
        return ("reload", "<instruction>")
    return event[:-1]


def test_every_question_comes_to_the_gui_thread_in_the_old_order(printer):
    dialog = _dialog(printer, test_first=True)
    dialog.start_print()
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])

    assert [_shape(event) for event in printer.events] == EXPECTED_ORDER
    for event in printer.events:
        on_gui = event[-1]
        assert on_gui is (event[0] != "submit"), event
    # The run covered every sheet of both faces, once.
    fronts = [s for e in printer.events if e[:2] == ("submit", "front") for s in e[2]]
    backs = [s for e in printer.events if e[:2] == ("submit", "back") for s in e[2]]
    assert sorted(fronts) == sorted(backs) == [0, 1, 2, 3, 4]


def test_the_unrecorded_sheets_question_is_asked_on_the_gui_thread(printer):
    printer.script = {
        1: PrintResult(submitted=2, job_id=None, error="printed, but not recorded")
    }
    dialog = _dialog(printer)
    dialog.start_print()
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])

    asked = [e for e in printer.events if e[0] == "sheets_printed"]
    assert asked == [("sheets_printed", 2, "printed, but not recorded", True)]
    assert [e[0] for e in printer.events].index("sheets_printed") < [
        e[0] for e in printer.events
    ].index("offline")


# -- failures -------------------------------------------------------------


@pytest.mark.parametrize("error", ["printer offline", ""], ids=["message", "empty"])
def test_a_failed_chunk_reads_as_it_did(printer, error):
    failed = PrintResult(submitted=0, job_id=None, error=error)
    printer.script = {1: failed}
    dialog = _dialog(printer)
    dialog.start_print()
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])

    offline = [e for e in printer.events if e[0] == "offline"]
    assert offline == [("offline", "Fake", result_failure_text(failed), True)]
    assert offline[0][2]  # never "" (H7)
    # It stopped at the failure.
    assert [e[0] for e in printer.events][-1] == "offline"


def test_an_exception_in_the_printer_still_reaches_the_gui_thread(printer, monkeypatch):
    printer.script = {1: RuntimeError()}
    raised = []
    monkeypatch.setattr(
        sys, "excepthook", lambda kind, exc, tb: raised.append((exc, _on_gui()))
    )
    dialog = _dialog(printer)
    try:
        dialog.start_print()
    except RuntimeError as exc:  # where a synchronous run put it
        raised.append((exc, _on_gui()))
    assert _pump_until(lambda: raised, dialogs=[dialog])
    assert len(raised) == 1
    exc, on_gui = raised[0]
    assert type(exc) is RuntimeError and on_gui
    assert _pump_until(lambda: _settled(dialog), dialogs=[dialog])
    assert dialog.print_button.isEnabled()


# -- cancel and close -----------------------------------------------------


def test_closing_mid_chunk_waits_for_the_chunk_and_stops_there(printer):
    printer.script = {0: "hold"}
    dialog = _dialog(printer)
    dialog.start_print()
    assert printer.entered.wait(5.0)
    thread = dialog._print_job.thread if dialog._print_job else None

    threading.Timer(0.3, printer.release.set).start()
    dialog.widget.reject()

    # Closing returned only after the chunk did, and nothing followed it.
    assert thread is not None and thread.isFinished()
    assert [e[0] for e in printer.events] == ["submit"]
    _pump_until(lambda: False, 0.2, dialogs=[dialog])
    assert [e[0] for e in printer.events] == ["submit"]
    # Resumable from the next sheet.
    (summary,) = PrintSession.list_resumable()
    assert (summary.pass_index, summary.sheet_cursor) == (0, 2)


def test_closing_while_a_question_waits_does_not_hang_or_answer_it(printer):
    # Two sheets: the fronts fit one chunk, so the next thing the run does is
    # ask for the reload -- which this test never lets the GUI thread answer.
    dialog = _dialog(printer, n_sheets=2)
    dialog.start_print()
    deadline = time.monotonic() + 5.0
    while not printer.events and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # the job is now parked on the reload question

    began = time.monotonic()
    dialog.stop_background_work(timeout_ms=5000)
    assert time.monotonic() - began < 2.0
    _pump_until(lambda: False, 0.2, dialogs=[dialog])

    assert [e[0] for e in printer.events] == ["submit"]  # no reload, no backs
    (summary,) = PrintSession.list_resumable()
    assert summary.pass_index == 1


def test_closing_the_window_stops_the_print_run_first():
    from deckle.app.main import EXPORT_SHUTDOWN_WAIT_MS, MainWindow

    stopped = []
    window = SimpleNamespace(
        print_dialog=SimpleNamespace(stop_background_work=stopped.append),
        preview_view=SimpleNamespace(), arrange_view=SimpleNamespace(),
        layout_panel=SimpleNamespace(), import_view=SimpleNamespace(),
        cards_view=SimpleNamespace(), _jobs={}, _printer_query=None,
    )
    MainWindow.stop_background_work(window)
    assert stopped == [EXPORT_SHUTDOWN_WAIT_MS]


# -- the bridge itself ------------------------------------------------------


def test_a_gui_call_from_a_job_runs_on_the_gui_thread_and_returns(qapp):
    from PySide6.QtCore import QObject

    from deckle.app.workers import GuiCaller

    parent = QObject()
    caller = _watch(GuiCaller(parent))
    out = []

    def job():
        out.append(caller.call(lambda a, b: (a + b, _on_gui()), 1, 2))
        try:
            caller.call(lambda: 1 / 0)
        except ZeroDivisionError as exc:
            out.append(exc)

    worker = threading.Thread(target=job)
    worker.start()
    assert _pump_until(lambda: not worker.is_alive())
    assert out[0] == (3, True)
    assert isinstance(out[1], ZeroDivisionError)


def test_a_cancelled_gui_call_gives_up_and_is_never_run(qapp):
    from PySide6.QtCore import QObject

    from deckle.app.workers import Cancelled, GuiCaller

    parent = QObject()
    caller = _watch(GuiCaller(parent))
    cancel = threading.Event()
    ran, out = [], []

    def job():
        try:
            caller.call(ran.append, 1, cancel=cancel)
        except Cancelled:
            out.append("cancelled")

    worker = threading.Thread(target=job)
    worker.start()
    time.sleep(0.1)
    cancel.set()
    worker.join(2.0)
    assert out == ["cancelled"]
    _pump_until(lambda: False, 0.1)
    assert ran == []


def test_a_resumed_run_asks_first_then_spools_off_the_gui_thread(printer):
    # A run whose first back chunk failed, left on disk...
    printer.script = {3: PrintResult(submitted=0, job_id=None, error="jammed")}
    first = _dialog(printer)
    first.start_print()
    assert _pump_until(lambda: _settled(first), dialogs=[first])
    assert len(PrintSession.list_resumable()) == 1

    # ...is reopened, and the operator says no backs came out.
    printer.script = {}
    printer.events = []
    resumed = _dialog(
        printer,
        resumable_lister=PrintSession.list_resumable,
        confirm_resume=lambda summaries: summaries[0],
        ask_resume_count=lambda summary: 0,
    )
    assert _pump_until(lambda: _settled(resumed), dialogs=[resumed])

    for event in printer.events:
        assert event[-1] is (event[0] != "submit"), event
    # The status line is recorded only once the dialog exists, which is after
    # a synchronous resume had already finished.
    assert [_shape(e) for e in printer.events] == [
        ("reload", "<instruction>"),
        ("submit", "back"), ("submit", "back"), ("submit", "back"),
        ("status", "Print job complete."),
    ]
    assert PrintSession.list_resumable() == []
