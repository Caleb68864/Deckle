"""B30: a printer turned on after launch is noticed without a relaunch.

The Print button is disabled while there are no printers, so a re-check on
click alone could never have helped the case that matters. The subprocess
probe drives the real window through the two routes that remain reachable
with Print greyed out: activating the window and opening a menu.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import deckle.app.main as app_main
from .test_hardening_printing import _FakeThread, _FakeWindow

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


class _Window(_FakeWindow):
    _apply_requeried_printers = getattr(app_main.MainWindow, "_apply_requeried_printers", None)
    _imageable_areas = {}


def _quiet_refresh(monkeypatch, window):
    """Start one quiet refresh; returns the query it created."""
    monkeypatch.setattr(app_main, "_single_shot", lambda ms, cb: None)
    monkeypatch.setattr(app_main, "_new_thread", lambda parent: _FakeThread())
    app_main.MainWindow.refresh_printers(window, quiet=True)
    return window._printer_query


def test_a_quiet_refresh_leaves_the_button_and_the_status_bar_alone(monkeypatch):
    window = _Window(pages=["one page"])
    window.print_button.enabled = True
    window.status_bar.message = "Saved 12 sheet(s)"

    _quiet_refresh(monkeypatch, window)

    assert window._printer_thread.started is True
    assert window.print_button.enabled is True
    assert window.status_bar.message == "Saved 12 sheet(s)"


def test_a_loud_refresh_still_says_it_is_checking(monkeypatch):
    monkeypatch.setattr(app_main, "_single_shot", lambda ms, cb: None)
    monkeypatch.setattr(app_main, "_new_thread", lambda parent: _FakeThread())
    window = _Window(pages=["one page"])
    app_main.MainWindow.refresh_printers(window)
    assert window.print_button.enabled is False
    assert window.status_bar.message == "Checking for printers..."


def test_a_second_quiet_refresh_waits_for_the_running_thread(monkeypatch):
    """Even past the deadline: a thread parked in a dead spooler must not
    gain a sibling every time the window gets focus."""
    window = _Window()
    first = _quiet_refresh(monkeypatch, window)
    first_thread = window._printer_thread
    first.time_out()

    assert _quiet_refresh(monkeypatch, window) is first
    assert window._printer_thread is first_thread

    first.complete()
    assert _quiet_refresh(monkeypatch, window) is not first


def test_a_new_printer_enables_print(monkeypatch):
    window = _Window(pages=["one page"])
    window._apply_requeried_printers(["Brother"], None, {})
    assert window._printers == ["Brother"]
    assert window.print_button.enabled is True


def test_a_printer_that_went_away_stops_being_offered():
    window = _Window(pages=["one page"])
    window._apply_printers(["Brother"], None, {})
    window._apply_requeried_printers([], None, {})
    assert window._printers == []
    assert window.print_button.enabled is False


def test_an_unchanged_answer_keeps_the_printer_chosen_in_the_dialog():
    """`_apply_printers` re-resolves the profile for the preselected printer,
    which would quietly undo the choice made in the Print dialog."""
    window = _Window(pages=["one page"])
    window._apply_printers(["Brother"], None, {})
    chosen = object()
    window.profile = chosen
    window.status_bar.message = "Saved 12 sheet(s)"

    window._apply_requeried_printers(["Brother"], None, {})

    assert window.profile is chosen
    assert window.status_bar.message == "Saved 12 sheet(s)"


def test_a_spooler_that_misses_the_deadline_does_not_take_print_away():
    window = _Window(pages=["one page"])
    window._apply_printers(["Brother"], None, {})

    window._apply_requeried_printers([], app_main.PRINTER_TIMEOUT_MESSAGE, {})

    assert window._printers == ["Brother"]
    assert window.print_button.enabled is True


def test_clicking_print_rechecks_and_opens_against_the_cached_list(monkeypatch):
    opened = []

    class _Dialog:
        def __init__(self, plan, parent, printer_names=None, **_kwargs):
            opened.append(list(printer_names))
            self.widget = SimpleNamespace(exec=lambda: 0)

        def selected_profile(self):
            return app_main.DEFAULT_PROFILE

    monkeypatch.setattr(app_main, "PrintDialog", _Dialog)
    refreshed = []
    window = _Window(pages=["one page"])
    window.refresh_printers = lambda **kwargs: refreshed.append(kwargs)
    window._printers = ["Brother"]
    window._imageable_areas = {}
    window.preview_view.plan = object()
    window.state.project.printer = None
    window._output_is_ready = lambda: True

    app_main.MainWindow.print_document(window)

    assert refreshed == [{"quiet": True}]
    assert opened == [["Brother"]]


class _Stoppable:
    """Enough of the window for `stop_background_work`."""

    def __init__(self, query, thread):
        empty = SimpleNamespace(_worker=None, _thread=None)
        self.preview_view = self.arrange_view = self.layout_panel = self.import_view = empty
        self._jobs = {}
        self._stopping = False
        self._printer_query = query
        self._printer_thread = thread


def test_closing_waits_for_a_recheck_still_running():
    """Qt aborts the process if a running thread's parent is destroyed, and
    dismissing the save prompt re-activates the window just before that."""
    waited = []
    thread = SimpleNamespace(wait=waited.append)
    window = _Stoppable(SimpleNamespace(worker_finished=False), thread)

    app_main.MainWindow.stop_background_work(window, timeout_ms=7)

    assert waited == [7]
    assert window._stopping is True


def test_closing_does_not_touch_a_recheck_that_has_finished():
    """Qt has freed that thread, and asking it would raise."""
    thread = SimpleNamespace(wait=lambda ms: pytest.fail("touched a freed thread"))
    window = _Stoppable(SimpleNamespace(worker_finished=True), thread)
    app_main.MainWindow.stop_background_work(window, timeout_ms=7)


def test_nothing_new_is_started_once_the_window_is_closing():
    started = []
    window = SimpleNamespace(
        _stopping=True, refresh_printers=lambda **kwargs: started.append(kwargs)
    )
    app_main.MainWindow._recheck_printers(window)
    assert started == []
    window._stopping = False
    app_main.MainWindow._recheck_printers(window)
    assert started == [{"quiet": True}]


PROBE = '''
import json, os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
app = QApplication([])

import deckle.app.main as am
import deckle.app.printer_query as pq

names = []
pq.available_printer_names = lambda: list(names)
pq.query_imageable_area_pt = lambda name, **_: None

real = am.MainWindow.refresh_printers
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None, quiet=False: self._apply_printers([])
)
window = am.MainWindow()
am.MainWindow.refresh_printers = real

from dataclasses import replace
from deckle.core.loader import load_pdf
pages = load_pdf(sys.argv[1])
window.state.mutate(lambda p: replace(p, pages=list(pages)))
window._on_imported(pages, [])

def settle():
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        query = window._printer_query
        if query is not None and query.worker_finished:
            return
        time.sleep(0.01)
    raise SystemExit("the re-check never finished")

report = {"before": window.print_button.isEnabled()}

names.append("Turned on after launch")
window.window.isActiveWindow = lambda: True
app.sendEvent(window.window, QEvent(QEvent.Type.ActivationChange))
settle()
report["after_activation"] = window.print_button.isEnabled()

names.clear()
window._printer_query = None
window.window.menuBar().actions()[0].menu().aboutToShow.emit()
settle()
report["after_menu"] = window.print_button.isEnabled()

print("REPORT " + json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


def test_the_real_window_notices_a_printer_with_print_greyed_out():
    """Launch, turn the printer on, come back: no click was possible, because
    the only control that used to re-check is the one that is disabled."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, FIXTURE],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": REPO_ROOT},
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("REPORT ")]
    assert lines, result.stdout + result.stderr
    assert json.loads(lines[0][len("REPORT "):]) == {
        "before": False,
        "after_activation": True,
        "after_menu": False,
    }
