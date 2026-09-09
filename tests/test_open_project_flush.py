"""Opening another project must not abandon the one being replaced.

``open_project`` builds a fresh ``AppState`` and assigns it over
``self.state``. The outgoing state was simply dropped, and an ``AppState``
that is dropped is not an ``AppState`` that is finished: it holds up to
``autosave_delay_s`` of edits behind a live ``threading.Timer``, and losing
the last reference to it cancels nothing.

So two things went wrong at once, and only the first is obvious.

1. **The edits are lost.** Half a second of work on the project the user is
   leaving -- exactly the interval autosave exists to cover.
2. **The timer is orphaned, not dead.** It is a daemon timer holding the
   *old* project. It fires after the swap and writes that old project to
   the old project's autosave, behind the back of a user who has moved on.
   Because the recovery offer is decided on mtime, the next open of that
   project then reports a crash that never happened and offers stale work
   back.

``close()`` already flushes for the first reason. Swapping the project out
is a close as far as the outgoing state is concerned.

Out of process, because constructing a real ``QMainWindow`` under pytest
exits non-zero on this machine -- the same reason
``tests/test_gui_workflow.py`` and ``tests/test_shutdown.py`` shell out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")

# Saves two projects, points the window at the first, edits it, then opens
# the second -- and reports what happened to the first one's autosave and
# to its debounce timer.
#
# The debounce is a fake that never fires, on purpose: a real 500ms timer
# makes "were the edits saved?" a race, and the answer this asserts is not
# "the timer got there first" but "the swap flushed". A fake that never
# fires means the autosave file exists if and only if something flushed it.
PROBE = '''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

import deckle.app.main as am
am.available_printer_names = lambda: []
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
)

from dataclasses import replace
from deckle.app.state import AppState
from deckle.core.loader import load_pdf
from deckle.core.project_io import save_project

fixture, workdir = sys.argv[1], sys.argv[2]
leaving = os.path.join(workdir, "leaving.deckle")
arriving = os.path.join(workdir, "arriving.deckle")


class FakeTimer:
    """A debounce that never fires, and remembers being cancelled."""

    last = None

    def __init__(self, delay, fn):
        self.fn = fn
        self.cancelled = False
        self.daemon = False
        FakeTimer.last = self

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


window = am.MainWindow()
pages = load_pdf(fixture)
project = replace(window.state.project, pages=list(pages))
save_project(project, leaving)
save_project(project, arriving)

# The state the window is about to abandon: a saved project, so it has an
# autosave path, with a debounce we control.
state = AppState(project, project_path=leaving, timer_factory=FakeTimer)
window.state = state
window.import_view.state = state
window.arrange_view.state = state
window.layout_panel.state = state

# An edit the user has made and has not explicitly saved.
state.mutate(lambda p: replace(p, pages=list(p.pages) + [p.pages[0]]))
edited_pages = len(state.project.pages)

report = {
    "edited_pages": edited_pages,
    "autosave_before_open": os.path.exists(leaving + ".autosave"),
    "opened": window.open_project(arriving),
    "state_replaced": window.state is not state,
    "timer_cancelled": FakeTimer.last.cancelled,
    "autosave_exists": os.path.exists(leaving + ".autosave"),
}

if report["autosave_exists"]:
    with open(leaving + ".autosave", "r", encoding="utf-8") as f:
        report["autosave_pages"] = len(json.load(f)["pages"])

print(json.dumps(report))
sys.stdout.flush()
# Leave without unwinding, like tests/gui_workflow.py: the offscreen
# platform dies during interpreter teardown here and would turn a
# completed run into a spurious failure.
os._exit(0)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    pytest.importorskip("PySide6")
    workdir = tmp_path_factory.mktemp("open_project_flush")

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    # A fresh `MainWindow` offers back any never-saved autosave it finds
    # under `data_dir("autosave")`, through a modal that a headless probe
    # cannot answer. Point the data root at a scratch directory so the
    # probe sees an empty store -- and so it cannot leave recovery offers
    # in the developer's real one either.
    env["XDG_DATA_HOME"] = str(workdir / "data")
    env["APPDATA"] = str(workdir / "data")

    result = subprocess.run(
        [sys.executable, "-u", "-c", PROBE, FIXTURE, str(workdir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"the probe did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-2000:]}"
    )
    assert result.stdout.strip(), "the probe produced no report"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_probe_set_up_the_case_it_claims_to(report):
    """Guards the two premises the real assertions rest on: there really
    were unsaved edits, and the project really was swapped."""
    assert report["edited_pages"] == 3
    assert report["autosave_before_open"] is False, (
        "the debounce fired after all -- the fake timer is not fake"
    )
    assert report["opened"] is True
    assert report["state_replaced"] is True


def test_the_edits_survive_the_swap(report):
    """The half-second of work the user did on the project they are
    leaving. Without the flush this file does not exist at all."""
    assert report["autosave_exists"] is True, (
        "opening another project discarded the outgoing project's "
        "pending autosave"
    )
    assert report["autosave_pages"] == report["edited_pages"], (
        "the autosave was written but does not contain the edit"
    )


def test_the_outgoing_debounce_timer_is_disarmed(report):
    """The half nobody sees. A live daemon timer holding the previous
    project writes it to disk minutes later, and the mtime that write
    leaves behind is read as a crash on the next open."""
    assert report["timer_cancelled"] is True, (
        "the replaced AppState's debounce timer is still armed and will "
        "write the old project after the user has moved on"
    )
