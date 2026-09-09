"""Quitting while a render is in flight.

Found by a hardening pass that drove a live window through 300 random
edits: the run finished, printed its results, and then died with an
access violation inside pdfium's ``FPDF_LoadPage``.

Nothing cancelled or waited for the background threads. Both the preview
and the thumbnail grid render on a ``QThread``, both already support
cancellation, and neither was ever asked -- so quitting mid-render left
them running into interpreter teardown, calling pdfium after it had been
finalised. The user sees a crash on exit, on the one action that is
supposed to be safe.

Out of process, because the failure is a process-level crash: it cannot
be caught, only observed as an exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")

# Churns the plan so several render threads are in flight, then exits the
# way the argument says.
PROBE = '''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
import deckle.app.main as am
am.available_printer_names = lambda: []
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
)
from dataclasses import replace
from deckle.core.loader import load_pdf

window = am.MainWindow()
# Closing a window with unsaved work now asks about it. That prompt is a
# real modal and would block this probe forever; what is under test here
# is the thread shutdown behind it, so the answer is injected the same way
# the print dialog's confirmations are.
window.confirm_discard_changes = lambda name, action: "discard"
pages = list(load_pdf(sys.argv[1])) * 8
window.state.mutate(lambda p: replace(p, pages=list(pages)))
window._on_imported(list(pages), [])
for _ in range(12):
    window.arrange_view.move_pages([0], len(window.state.project.pages))

how = sys.argv[2]
if how == "close":
    window.close()
elif how == "close_event":
    from PySide6.QtGui import QCloseEvent
    window.window.closeEvent(QCloseEvent())
elif how == "stop_only":
    window.stop_background_work()
    # Every live thread, not just `view._thread`. Counting only the
    # current one is what let this probe report "nothing running" while
    # superseded renders were still in flight -- the exact condition
    # that then crashed the process during interpreter teardown.
    running = [
        name
        for name, view in (("preview", window.preview_view),
                           ("thumbnails", window.arrange_view))
        for t in am._live_threads(view)
        if t.isRunning()
    ]
    print("still running:", running)
    # Closing the window as well, so the probe exits the way a real
    # session does. This used to claim that destroying a live QMainWindow
    # at interpreter exit crashes PySide6 regardless of any thread, and
    # that was false: a window built, closed and dropped with no render in
    # flight exits cleanly every time, measured over 16 runs. The crash
    # always needed a surviving render thread, which is why chasing a
    # "separate teardown problem" found nothing for so long.
    window.close()
print("exited via", how)
'''


def _run(how: str) -> subprocess.CompletedProcess:
    pytest.importorskip("PySide6")
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-u", "-c", PROBE, FIXTURE, how],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        cwd=REPO_ROOT,
    )


def test_closing_mid_render_exits_cleanly():
    """The regression. Exit 127 before this was fixed."""
    result = _run("close")

    assert result.returncode == 0, (
        f"quitting mid-render crashed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-1500:]}"
    )
    assert "exited via close" in result.stdout


def test_closing_the_window_directly_also_exits_cleanly():
    """Qt calls closeEvent itself when the user clicks the X; it does not
    go through our `close()`, so the cleanup has to live on both."""
    result = _run("close_event")

    assert result.returncode == 0, (
        f"the window's own close crashed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-1500:]}"
    )


def test_stopping_background_work_is_enough_on_its_own():
    """Isolates the cancel-and-wait from the autosave flush beside it."""
    result = _run("stop_only")

    assert result.returncode == 0, (
        f"stop_background_work did not settle the threads "
        f"(exit {result.returncode}).\nstderr:\n{result.stderr[-1500:]}"
    )
    # The invariant, asserted rather than inferred from the exit code. A
    # surviving thread only SOMETIMES crashes the process, so exit 0 on
    # its own never proved the threads had settled -- which is why this
    # file went green for a fix that was incomplete.
    assert "still running: []" in result.stdout, (
        f"threads survived stop_background_work.\nstdout:\n{result.stdout}"
    )


def test_the_unguarded_exit_is_what_this_protects_against():
    """Documents the failure rather than asserting it stays broken.

    Left as a skip-with-reason if the platform no longer reproduces it:
    the point is the fix above, and a flaky assertion on a native crash
    would be worse than none.
    """
    result = _run("none")

    if result.returncode == 0:
        pytest.skip("this platform no longer crashes on an unguarded exit")
    assert result.returncode != 0


# -- which threads the shutdown actually waits for ------------------------
#
# The subprocess tests above were flaky at about one run in four, and the
# cause was not the test: `stop_background_work` waited only for each
# view's CURRENT `_thread`. Both views replace `_thread` every time a
# render is superseded, so rapid churn leaves earlier threads running with
# nobody waiting for them. Measured directly, every crashing run had
# threads still running after `close()` returned and every clean run had
# none -- exit 0xC0000409, no Python traceback, pdfium called during
# interpreter teardown.
#
# Superseded workers already get their cancel token set when they are
# replaced, so cancellation was never the gap. Waiting was.


class _FakeThread:
    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.waited_ms: int | None = None

    def isRunning(self) -> bool:
        return self._running

    def wait(self, timeout_ms: int) -> bool:
        self.waited_ms = timeout_ms
        self._running = False
        return True


class _FakeWidget:
    def __init__(self, children: list) -> None:
        self._children = children

    def findChildren(self, _type) -> list:
        return list(self._children)


class _FakeView:
    def __init__(self, current, children) -> None:
        self._thread = current
        self._worker = None
        self.widget = _FakeWidget(children)


def test_live_threads_includes_superseded_ones_not_just_the_current():
    from deckle.app.main import _live_threads

    current = _FakeThread()
    superseded_a = _FakeThread()
    superseded_b = _FakeThread()
    view = _FakeView(current, [superseded_a, current, superseded_b])

    found = _live_threads(view)

    assert current in found
    assert superseded_a in found
    assert superseded_b in found


def test_live_threads_reports_each_thread_once():
    """The current thread is also a child of the widget, so a naive union
    would wait on it twice -- harmless, but it would make the count a lie
    for anything that reads it."""
    from deckle.app.main import _live_threads

    current = _FakeThread()
    view = _FakeView(current, [current])

    assert len(_live_threads(view)) == 1


def test_live_threads_survives_a_view_with_no_widget():
    from deckle.app.main import _live_threads

    class _Bare:
        _thread = None

    assert _live_threads(_Bare()) == []
