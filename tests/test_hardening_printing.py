"""Hardening pass 6: printer and spooler faults.

Two real incidents on the development machine are the reason this file
exists. Printer enumeration ran on the UI thread and froze Deckle for
**81 minutes** during a network outage, and Deckle hung on launch with an
unreachable network printer configured. Enumeration has since moved to a
background thread -- but a background thread parked forever inside the OS
spooler is still a hang, it has only moved where the freeze shows up. So
the enumeration tests here are as much about the *deadline* as the thread.

The submission tests cover the other half: a printer that disappears
between being chosen and being printed to (Deckle's manual-duplex flow
puts minutes of paper handling in that window), and a run that dies
partway. The second matters because paper cannot be read backwards -- the
user is left holding a stack with no way to tell which sheets made it.

Nothing here may touch a real printer, and nothing here may construct a
live Qt window: ``QT_QPA_PLATFORM=offscreen`` plus ``show()`` hard-kills
the process on this machine. Both modules were built with injectable
seams for exactly this reason.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import ast
import json
import logging
from pathlib import Path

import pytest

from deckle.app import backend as backend_mod
from deckle.app import main as app_main
from deckle.core import diagnostics
from deckle.core.printing import PrintPass
from deckle.core.profiles import PrinterProfile


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    """Point the diagnostic log at this test's own directory."""
    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("DECKLE_LOG_LEVEL", raising=False)
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


def _records(tmp_path: Path) -> list[dict]:
    path = tmp_path / "diagnostics.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _events(tmp_path: Path) -> list[str]:
    return [r["event"] for r in _records(tmp_path)]


def _record(tmp_path: Path, event: str) -> dict:
    matches = [r for r in _records(tmp_path) if r["event"] == event]
    assert matches, f"no {event!r} record in {_events(tmp_path)}"
    return matches[-1]


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


def _pass(sheet_order, side="front", rotate_backs=False, index=0) -> PrintPass:
    return PrintPass(
        index=index,
        sheet_order=list(sheet_order),
        side=side,
        reload_instruction="do the thing",
        rotate_backs=rotate_backs,
    )


# ------------------------------------------------- enumeration: off-thread


def test_enumeration_is_wired_to_a_thread_and_never_called_inline():
    """The 81-minute freeze, as a structural assertion.

    ``refresh_printers`` must hand ``available_printer_names`` to a worker
    that a QThread runs, and must not call it on the UI thread. The only
    inline call permitted is under ``blocking=True``, which is the
    no-event-loop path for tests and the CLI.
    """
    source = Path(app_main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "refresh_printers"
    )

    # The non-blocking branch is everything after the `if blocking:` guard.
    blocking_guard = next(
        node
        for node in func.body
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "blocking"
    )
    tail = [n for n in func.body if n is not blocking_guard]
    tail_src = "\n".join(ast.dump(n) for n in tail)

    assert "available_printer_names" not in tail_src, (
        "refresh_printers enumerates printers on the UI thread -- this is the "
        "81-minute launch hang"
    )
    assert "_PrinterQueryWorker" in tail_src
    assert "_new_thread" in tail_src
    assert "start" in tail_src
    # A thread that never runs the worker would look identical from the UI
    # right up until the moment it mattered.
    assert "thread.run = worker.run" in source


# --------------------------------------------------- enumeration: deadline


class _FakeThread:
    """Records that it was started; never actually runs anything."""

    def __init__(self):
        self.started = False
        self.finished = _FakeSignal()

    def start(self):
        self.started = True

    def deleteLater(self):
        pass


class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self):
        for slot in list(self.slots):
            slot()


class _Applied:
    """Captures what ``_apply_printers`` was handed."""

    def __init__(self):
        self.calls = []
        self.imageable_areas = None

    def __call__(self, printers, message=None, imageable_areas=None):
        self.calls.append((list(printers), message))
        # N2: a timed-out query must apply {}, not a half-filled dict.
        self.imageable_areas = imageable_areas


def test_query_settles_as_no_printers_when_the_deadline_passes(tmp_path):
    """A spooler that never answers must degrade, not hang forever."""
    worker = app_main._PrinterQueryWorker()  # .names stays [] -- still blocked
    applied = _Applied()
    query = app_main._PrinterQuery(worker, applied, timeout_ms=5000)

    query.time_out()

    assert query.timed_out is True
    assert applied.calls == [([], app_main.PRINTER_TIMEOUT_MESSAGE)]
    record = _record(tmp_path, "printer_enumeration_timeout")
    assert record["timeout_ms"] == 5000


def test_a_late_answer_after_the_deadline_is_discarded(tmp_path):
    """The abandoned thread may unblock hours later. It must not re-enable
    Print underneath a user who has long since moved on."""
    worker = app_main._PrinterQueryWorker()
    applied = _Applied()
    query = app_main._PrinterQuery(worker, applied, timeout_ms=50)

    query.time_out()
    worker.names = ["Brother HL-2270DW"]
    query.complete()

    assert len(applied.calls) == 1
    assert applied.calls[0][0] == []
    assert "printer_enumeration_late_result" in _events(tmp_path)


def test_a_timeout_after_a_successful_answer_is_ignored(tmp_path):
    """The ordinary case: the worker wins, the timer fires anyway."""
    worker = app_main._PrinterQueryWorker()
    worker.names = ["Brother HL-2270DW"]
    applied = _Applied()
    query = app_main._PrinterQuery(worker, applied, timeout_ms=5000)

    query.complete()
    query.time_out()

    assert applied.calls == [(["Brother HL-2270DW"], None)]
    assert query.timed_out is False
    assert "printer_enumeration_timeout" not in _events(tmp_path)


def test_refresh_printers_arms_a_deadline_against_the_worker(monkeypatch, tmp_path):
    """The wiring, not just the arbitration: starting a thread without also
    arming a timer reproduces the original unbounded hang exactly."""
    scheduled = []
    monkeypatch.setattr(
        app_main, "_single_shot", lambda ms, cb: scheduled.append((ms, cb))
    )
    monkeypatch.setattr(app_main, "_new_thread", lambda parent: _FakeThread())

    window = _FakeWindow()
    app_main.MainWindow.refresh_printers(window, timeout_ms=1234)

    assert window._printer_thread.started is True
    assert len(scheduled) == 1, "no deadline was armed against the enumeration thread"
    interval, callback = scheduled[0]
    assert interval == 1234

    # And firing it degrades the UI rather than leaving it mid-check.
    callback()
    assert window.print_button.enabled is False
    assert window.status_bar.message == app_main.PRINTER_TIMEOUT_MESSAGE
    assert "printer_enumeration_timeout" in _events(tmp_path)


def test_default_timeout_is_bounded_and_sane():
    assert 0 < app_main.PRINTER_QUERY_TIMEOUT_MS <= 30_000


# ----------------------------------------------------- zero / broken spooler


class _FakeButton:
    def __init__(self):
        self.enabled = True
        self.tooltip = ""

    def setEnabled(self, value):
        self.enabled = value

    def setToolTip(self, value):
        self.tooltip = value


class _FakeStatusBar:
    def __init__(self):
        self.message = None

    def showMessage(self, message):
        self.message = message

    def clearMessage(self):
        self.message = None


def _no_saved_profile(name):
    """A printer nobody has calibrated -- the ordinary case."""
    raise FileNotFoundError(name)


class _FakeProfileConsumer:
    """A stand-in for the preview or the layout panel.

    Both hold a ``PrinterProfile`` and nothing else about them matters to
    ``_apply_printers``; the preview additionally has to be told to draw
    again, because the red guide is painted from the profile and the
    clipping warnings are computed from it in the render worker.
    """

    def __init__(self):
        self.profile = None
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1


class _FakeWindow:
    """Enough of MainWindow to drive refresh_printers/_apply_printers.

    Unbound-method calls against this stand-in are how the printer paths get
    tested at all: constructing a real QMainWindow under this environment's
    offscreen platform kills the process outright.
    """

    def __init__(self, pages=()):
        self.print_button = _FakeButton()
        self.save_pdf_button = _FakeButton()
        self.status_bar = _FakeStatusBar()
        self.window = None
        self._printers = []
        self._printer_thread = None
        self._printer_query = None
        # _apply_printers consults the document to decide what the status
        # bar should say: with nothing loaded, "no printers" is not the
        # user's next step, importing is. The double has to model that.
        self.state = SimpleNamespace(project=SimpleNamespace(pages=list(pages)))
        #: Set by _apply_printers, read by _refresh_status_message. The
        #: status bar has one writer so that a later import cannot leave a
        #: stale "import something" instruction on screen.
        self._printer_message = ""
        # Enumeration is also the moment the window learns which printer
        # it is drawing for (B15): `_apply_printers` resolves a profile
        # and pushes it into the preview and the layout panel, which for
        # the whole life of the window before that were pinned to the
        # first built-in preset's 18pt border.
        self.profile = app_main.DEFAULT_PROFILE
        self.profile_loader = _no_saved_profile
        # The Print menu entry is disabled alongside the Print button, so
        # the double has to own the map the real window keeps them in.
        self.menu_actions = {}
        self.layout_panel = _FakeProfileConsumer()
        self.preview_view = _FakeProfileConsumer()

    _apply_printers = app_main.MainWindow._apply_printers
    _refresh_status_message = app_main.MainWindow._refresh_status_message
    set_printer_profile = app_main.MainWindow.set_printer_profile
    _profile_with_driver_answer = app_main.MainWindow._profile_with_driver_answer


def test_zero_printers_disables_print_but_leaves_save_pdf_alone():
    """Deckle is an imposition tool that happens to print. With no printer
    at all it must stay fully usable for Save PDF."""
    window = _FakeWindow(pages=["one page"])
    app_main.MainWindow._apply_printers(window, [])

    assert window.print_button.enabled is False
    assert window.save_pdf_button.enabled is True
    assert window.status_bar.message == app_main.NO_PRINTERS_MESSAGE
    # The tooltip is what explains a disabled control to someone who
    # hovers it wondering why they cannot click.
    assert window.print_button.tooltip == app_main.NO_PRINTERS_MESSAGE


def test_with_no_document_the_status_bar_names_the_first_step_not_the_printer():
    """Opening Deckle should not lead with a complaint about hardware.

    Printing is the last step of the workflow and needs no printer until
    then; importing is the first. With nothing loaded, say that instead.
    """
    window = _FakeWindow(pages=())
    app_main.MainWindow._apply_printers(window, [])

    assert window.status_bar.message == app_main.NO_DOCUMENT_MESSAGE
    # Print is still correctly disabled -- only the wording changes.
    assert window.print_button.enabled is False


def test_a_document_with_printers_present_clears_the_status_bar():
    window = _FakeWindow(pages=["one page"])
    app_main.MainWindow._apply_printers(window, ["Brother HL-2270DW"])

    assert window.print_button.enabled is True
    assert window.status_bar.message in ("", None)
    # With printers available there is nothing to explain, so the tooltip
    # is cleared rather than left saying the opposite of what is true.
    assert window.print_button.tooltip == ""


def test_apply_printers_never_touches_the_save_pdf_button():
    """Structural guard: a future edit that gates Save PDF on printers
    would break the one workflow that survives a dead spooler."""
    source = Path(app_main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_printers"
    )
    assert "save_pdf_button" not in ast.dump(func)


def test_an_empty_printer_list_is_logged_with_a_reason(tmp_path):
    """"The printer list was empty" was a support report with nothing
    behind it. Now it says which way it got there."""
    worker = app_main._PrinterQueryWorker()
    query = app_main._PrinterQuery(worker, _Applied(), timeout_ms=5000)
    query.complete()

    record = _record(tmp_path, "printer_enumeration_empty")
    assert record["reason"]


def test_a_spooler_failure_is_logged_and_degrades_to_no_printers(tmp_path):
    """A broken spooler is not a crash; it is zero printers plus a log line."""

    def boom():
        raise OSError("RPC server is unavailable")

    original = app_main.available_printer_names
    app_main.available_printer_names = boom
    try:
        worker = app_main._PrinterQueryWorker()
        worker.run()
    finally:
        app_main.available_printer_names = original

    assert worker.names == []
    record = _record(tmp_path, "printer_enumeration_failed")
    assert record["error_type"] == "OSError"
    assert "RPC server is unavailable" in record["error"]


def test_blocking_refresh_degrades_instead_of_raising(monkeypatch):
    """The CLI/test path must not turn a spooler fault into a traceback
    either -- it is the same fault, on a path with no event loop."""

    def boom():
        raise OSError("spooler unavailable")

    monkeypatch.setattr(app_main, "available_printer_names", boom)

    window = _FakeWindow()
    app_main.MainWindow.refresh_printers(window, blocking=True)

    assert window._printers == []
    assert window.print_button.enabled is False


# ----------------------------------- the preview draws the printer's border


def test_enumeration_hands_the_preview_the_printers_own_border(monkeypatch):
    """B15. The red "printer imageable area" guide, the
    ``clipped_by_imageable_area`` warnings beside it and "Use printer
    margins" all read one profile, and it was the first built-in preset
    for the life of the window -- so a calibrated printer's measured
    border was read from disk by the print dialog and ignored by every
    part of the app the user looks at first."""
    from deckle.core.profiles import PrinterProfile

    measured = PrinterProfile(
        version=1, flip_axis="long", output_face="down", feed_edge="top",
        reverse_stack=True, imageable_area_pt=(36.0, 12.0, 36.0, 12.0),
        calibrated_at="2026-01-01T00:00:00", calibration_version=1,
    )
    monkeypatch.setattr(app_main, "available_printer_names", lambda: ["Measured"])

    window = _FakeWindow(pages=["one page"])
    window.profile_loader = lambda name: measured
    app_main.MainWindow.refresh_printers(window, blocking=True)

    assert window.preview_view.profile is measured
    assert window.layout_panel.profile is measured
    assert window.preview_view.refreshed == 1, (
        "the profile changed and the sheet was not drawn again, so the old "
        "border stays on screen"
    )


def test_an_uncalibrated_printer_leaves_the_preview_alone(monkeypatch):
    """The no-op half. This runs on every printer refresh, and a redraw
    costs a rasterisation of the visible sheet."""
    monkeypatch.setattr(app_main, "available_printer_names", lambda: ["Plain"])

    window = _FakeWindow(pages=["one page"])
    app_main.MainWindow.refresh_printers(window, blocking=True)

    assert window.profile == app_main.DEFAULT_PROFILE
    assert window.preview_view.refreshed == 0


# ------------------------------------------- printer vanishes before submit


class _NullInfo:
    """What QPrinterInfo returns for a name the system no longer knows."""

    def isNull(self):
        return True


class _LiveInfo:
    def isNull(self):
        return False


class _NoopSubmitBackend(backend_mod.QtPrintBackend):
    """QtPrintBackend with the Qt painting stubbed out."""

    def __init__(self, *args, fail_on_chunk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunks_attempted: list[list[int]] = []
        self._fail_on_chunk = fail_on_chunk

    def _submit_chunk(self, plan, sheets, printer_name, copies, dpi, side, rotate_backs):
        self.chunks_attempted.append(list(sheets))
        if self._fail_on_chunk is not None and len(self.chunks_attempted) == self._fail_on_chunk:
            raise RuntimeError("The printer is offline or out of paper")


def test_printer_is_available_reports_a_vanished_printer(monkeypatch):
    monkeypatch.setattr(backend_mod, "_printer_info", lambda name: _NullInfo())
    assert backend_mod.printer_is_available("Ghost Printer") is False


def test_printer_is_available_reports_a_live_printer(monkeypatch):
    monkeypatch.setattr(backend_mod, "_printer_info", lambda name: _LiveInfo())
    assert backend_mod.printer_is_available("Brother HL-2270DW") is True


def test_an_empty_printer_name_means_the_default_and_is_not_second_guessed(monkeypatch):
    def fail(name):
        raise AssertionError("the default printer must not be looked up by name")

    monkeypatch.setattr(backend_mod, "_printer_info", fail)
    assert backend_mod.printer_is_available("") is True


def test_a_failing_availability_check_never_blocks_the_print(monkeypatch, tmp_path):
    """An inconclusive check is not a negative one. Refusing to print
    because the *check* broke would be a worse failure than the one it is
    guarding against."""

    def boom(name):
        raise RuntimeError("QPrinterInfo exploded")

    monkeypatch.setattr(backend_mod, "_printer_info", boom)

    assert backend_mod.printer_is_available("Brother HL-2270DW") is True
    assert "printer_availability_check_failed" in _events(tmp_path)


def test_submit_refuses_a_printer_that_disappeared_after_enumeration(monkeypatch, tmp_path):
    """The manual-duplex window: minutes of paper handling sit between the
    front pass and the back pass, and the printer can go away in them."""
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: False)

    backend = _NoopSubmitBackend(_profile())
    result = backend.submit(
        plan=None, sheets=[0, 1, 2], printer_name="Ghost Printer", copies=1, dpi=300
    )

    assert result.submitted == 0
    assert result.error is not None
    assert "Ghost Printer" in result.error
    # Nothing was painted -- the failure came before any Qt work.
    assert backend.chunks_attempted == []
    record = _record(tmp_path, "print_printer_unavailable")
    assert record["printer"] == "Ghost Printer"
    assert record["sheets"] == [0, 1, 2]


def test_a_vanished_printer_stops_the_pass_at_the_first_chunk(monkeypatch):
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: False)

    backend = _NoopSubmitBackend(_profile(), chunk_size=10)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="Ghost", copies=1, dpi=300
    )

    assert backend.chunks_attempted == []
    assert result.submitted == 0
    assert result.error is not None


# ------------------------------------ mid-run failure: what actually printed


def test_a_mid_run_failure_records_which_sheets_made_it(monkeypatch, tmp_path):
    """Paper cannot be read backwards.

    The user is holding a stack and cannot tell which sheets printed --
    that is the whole problem. ``PrintResult`` carries only a count and
    ``deckle.core.printing`` is a frozen seam, so the indices land on the
    backend and in the diagnostic log.
    """
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=10, fail_on_chunk=2)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="P", copies=1, dpi=300
    )

    assert result.submitted == 10
    assert backend.submitted_sheets == list(range(10))
    # The failing chunk is genuinely unknown: it may have died on its first
    # sheet or its last. Calling these printed is how a reprint comes out
    # with holes in it.
    assert backend.uncertain_sheets == list(range(10, 20))
    assert backend.unsubmitted_sheets == list(range(20, 25))

    record = _record(tmp_path, "print_pass_incomplete")
    assert record["submitted_sheets"] == list(range(10))
    assert record["uncertain_sheets"] == list(range(10, 20))
    assert record["unsubmitted_sheets"] == list(range(20, 25))
    assert record["pass_index"] == 0
    assert record["side"] == "front"


def test_the_back_pass_records_its_own_reversed_order(monkeypatch):
    """The back pass runs in reversed sheet order, so "sheets 0-9 printed"
    would name the wrong physical sheets entirely."""
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=4, fail_on_chunk=2)
    back = _pass([9, 8, 7, 6, 5, 4, 3, 2, 1, 0], side="back", rotate_backs=True, index=1)
    backend.submit_pass(plan=None, print_pass=back, printer_name="P", copies=1, dpi=300)

    assert backend.submitted_sheets == [9, 8, 7, 6]
    assert backend.uncertain_sheets == [5, 4, 3, 2]
    assert backend.unsubmitted_sheets == [1, 0]


def test_a_fully_successful_pass_leaves_nothing_uncertain(monkeypatch):
    """The correct path is unchanged and says so."""
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=10)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="P", copies=1, dpi=300
    )

    assert result.error is None
    assert result.submitted == 25
    assert backend.submitted_sheets == list(range(25))
    assert backend.uncertain_sheets == []
    assert backend.unsubmitted_sheets == []


def test_a_second_pass_does_not_inherit_the_first_passs_bookkeeping(monkeypatch):
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=10, fail_on_chunk=1)
    backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="P", copies=1, dpi=300
    )
    assert backend.uncertain_sheets

    backend._fail_on_chunk = None
    backend.chunks_attempted = []
    backend.submit_pass(
        plan=None, print_pass=_pass(range(5)), printer_name="P", copies=1, dpi=300
    )

    assert backend.submitted_sheets == list(range(5))
    assert backend.uncertain_sheets == []
    assert backend.unsubmitted_sheets == []


def test_an_offline_printer_error_text_reaches_the_diagnostic_log(monkeypatch, tmp_path):
    """Offline, out of paper and driver rejection all arrive as the same
    Qt exception; the distinction lives entirely in the message."""
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=10, fail_on_chunk=1)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(5)), printer_name="Brother", copies=1, dpi=300
    )

    assert "offline or out of paper" in result.error
    record = _record(tmp_path, "print_chunk_failed")
    assert record["error_type"] == "RuntimeError"
    assert "offline or out of paper" in record["error"]
    assert record["printer"] == "Brother"
    assert record["sheets"] == [0, 1, 2, 3, 4]


def test_the_incomplete_pass_record_is_a_warning(monkeypatch, tmp_path):
    """It must survive DECKLE_LOG_LEVEL=WARNING -- a partial print run is
    exactly what someone raises the bar to look for."""
    monkeypatch.setenv("DECKLE_LOG_LEVEL", "WARNING")
    diagnostics.reset_for_tests()
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    backend = _NoopSubmitBackend(_profile(), chunk_size=2, fail_on_chunk=2)
    backend.submit_pass(
        plan=None, print_pass=_pass(range(6)), printer_name="P", copies=1, dpi=300
    )

    assert "print_pass_incomplete" in _events(tmp_path)


def test_logging_a_failure_never_becomes_the_failure(monkeypatch):
    """diagnostics is total by design; assert the submission path relies on
    that rather than guarding every call itself."""
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)

    def boom(*args, **kwargs):
        raise RuntimeError("log handler exploded")

    monkeypatch.setattr(diagnostics.logging.getLogger(diagnostics._LOGGER_NAME), "log", boom)
    diagnostics._configure()

    backend = _NoopSubmitBackend(_profile(), chunk_size=10, fail_on_chunk=1)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(5)), printer_name="P", copies=1, dpi=300
    )
    assert result.error is not None


def test_the_timeout_record_is_a_warning(tmp_path, monkeypatch):
    """It must survive DECKLE_LOG_LEVEL=WARNING too: an enumeration timeout
    is the single most valuable line in the log for the 81-minute hang."""
    monkeypatch.setenv("DECKLE_LOG_LEVEL", "WARNING")
    diagnostics.reset_for_tests()

    query = app_main._PrinterQuery(app_main._PrinterQueryWorker(), _Applied(), timeout_ms=10)
    query.time_out()

    assert "printer_enumeration_timeout" in _events(tmp_path)
    assert logging.WARNING == 30


# -- the driver's imageable area (N2) ------------------------------------
#
# The preview's red guide is documented as the printer's hardware limit.
# It was a flat 0.25in from the first built-in preset, drawn identically
# on every machine. B15 made it follow the *selected* profile; this is the
# other half -- asking the driver what the border actually is.
#
# Sheets print at actual size (B6), so content inside that border is lost
# rather than shrunk to fit. A wrong number here is a ruined stack of
# paper, which is why every path below falls back to the preset rather
# than guessing.


def test_the_worker_collects_an_imageable_area_per_printer(monkeypatch):
    monkeypatch.setattr(app_main, "available_printer_names", lambda: ["A", "B"])
    monkeypatch.setattr(
        app_main, "query_imageable_area_pt", {"A": (1.0, 1.0, 1.0, 1.0)}.get
    )
    worker = app_main._PrinterQueryWorker()

    worker.run()

    assert worker.names == ["A", "B"]
    # B declined, so B is absent -- not present with a fabricated zero.
    assert worker.imageable_areas == {"A": (1.0, 1.0, 1.0, 1.0)}


def test_one_unreachable_printer_does_not_cost_the_others(monkeypatch):
    """A `try` per printer, not one around the loop."""
    monkeypatch.setattr(app_main, "available_printer_names", lambda: ["A", "B"])

    def query(name):
        if name == "A":
            raise OSError("network queue is not answering")
        return (2.0, 2.0, 2.0, 2.0)

    monkeypatch.setattr(app_main, "query_imageable_area_pt", query)
    worker = app_main._PrinterQueryWorker()

    worker.run()

    assert worker.names == ["A", "B"]
    assert worker.imageable_areas == {"B": (2.0, 2.0, 2.0, 2.0)}


def test_a_failed_enumeration_asks_no_driver_anything(monkeypatch):
    def explode():
        raise OSError("spooler is down")

    monkeypatch.setattr(app_main, "available_printer_names", explode)
    asked = []
    monkeypatch.setattr(
        app_main, "query_imageable_area_pt", lambda name: asked.append(name)
    )
    worker = app_main._PrinterQueryWorker()

    worker.run()

    assert worker.names == [] and worker.imageable_areas == {}
    assert asked == []


def test_applying_printers_pushes_the_driver_border_to_the_views():
    window = _FakeWindow(pages=["one page"])

    app_main.MainWindow._apply_printers(
        window, ["A"], None, {"A": (1.0, 2.0, 3.0, 4.0)}
    )

    assert window.preview_view.profile.imageable_area_pt == (1.0, 2.0, 3.0, 4.0)
    assert window.layout_panel.profile.imageable_area_pt == (1.0, 2.0, 3.0, 4.0)


def test_a_printer_with_no_driver_answer_keeps_the_preset():
    """`set_printer_profile` is a no-op when nothing changed, so the views
    are deliberately left untouched here -- the window's own profile is
    what says which border is in force."""
    window = _FakeWindow(pages=["one page"])

    app_main.MainWindow._apply_printers(window, ["A"], None, {})

    assert window.profile.imageable_area_pt == (18.0, 18.0, 18.0, 18.0)
    assert window.preview_view.profile is None, "a needless re-render"


def test_the_driver_never_overrules_a_measured_calibration():
    """A calibration was printed and measured with a ruler. The driver's
    number has been checked against nothing. Where they disagree the ruler
    wins, or a calibration pass was wasted."""
    calibrated = replace(
        app_main.DEFAULT_PROFILE, imageable_area_pt=(30.0, 30.0, 30.0, 30.0)
    )
    window = _FakeWindow(pages=["one page"])
    window.profile_loader = lambda name: calibrated

    app_main.MainWindow._apply_printers(
        window, ["A"], None, {"A": (1.0, 2.0, 3.0, 4.0)}
    )

    assert window.preview_view.profile.imageable_area_pt == (30.0, 30.0, 30.0, 30.0)


def test_applying_printers_still_takes_two_positional_arguments():
    """Four existing tests call this unbound with two arguments and none of
    them are about margins."""
    window = _FakeWindow(pages=["one page"])

    app_main.MainWindow._apply_printers(window, ["A"])

    assert window.profile.imageable_area_pt == (18.0, 18.0, 18.0, 18.0)


def test_a_timed_out_query_applies_no_margins_at_all():
    """Not a half-filled dict: the worker may be mid-loop, and margins for
    some printers and not others is a state nothing downstream expects."""
    applied = _Applied()
    worker = app_main._PrinterQueryWorker()
    worker.names = ["A"]
    worker.imageable_areas = {"A": (1.0, 2.0, 3.0, 4.0)}
    query = app_main._PrinterQuery(worker, applied, timeout_ms=1)

    query.time_out()

    assert applied.calls == [([], app_main.PRINTER_TIMEOUT_MESSAGE)]
    assert applied.imageable_areas == {}


def test_a_settled_query_hands_on_what_the_worker_collected():
    applied = _Applied()
    worker = app_main._PrinterQueryWorker()
    worker.names = ["A"]
    worker.imageable_areas = {"A": (1.0, 2.0, 3.0, 4.0)}
    query = app_main._PrinterQuery(worker, applied, timeout_ms=1)

    query.complete()

    assert applied.calls == [(["A"], None)]
    assert applied.imageable_areas == {"A": (1.0, 2.0, 3.0, 4.0)}
