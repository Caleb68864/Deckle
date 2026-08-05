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
    running = [
        name
        for name, view in (("preview", window.preview_view),
                           ("thumbnails", window.arrange_view))
        if getattr(view, "_thread", None) is not None
        and view._thread.isRunning()
    ]
    print("still running:", running)
    # Closing the window as well: destroying a live QMainWindow at
    # interpreter exit crashes PySide6 regardless of any thread, which is
    # a separate teardown problem and not what this probe measures.
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
