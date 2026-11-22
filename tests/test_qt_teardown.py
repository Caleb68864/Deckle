"""No render thread outlives its test (the exit-134 teardown crash).

Three GUI test files run together aborted about one run in five with
``QThread: Destroyed while thread is still running``, after every test had
passed: an ``ArrangeView`` left its thumbnail render running, and PySide
deleted the thread with the application at interpreter exit. The autouse
fixture in ``conftest.py`` now waits for such threads after each test.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_settler_waits_for_a_thread_left_running_under_a_widget(qapp, qt_thread_settler):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QWidget

    release = threading.Event()

    class Slow(QThread):
        def run(self):
            release.wait(5)

    owner = QWidget()  # top-level, as a view's widget is in a test
    thread = Slow(owner)
    thread.start()
    threading.Timer(0.3, release.set).start()
    started = time.monotonic()

    assert qt_thread_settler() == 1
    assert not thread.isRunning()
    assert time.monotonic() - started < 5


def test_an_arrange_view_is_settled_after_its_test(qapp, qt_thread_settler):
    """The real case: a view with real pages starts a thumbnail render."""
    from deckle.app.state import AppState
    from deckle.app.views.arrange_view import ArrangeView
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    project = Project(load_pdf(fixture) * 20, LayoutSettings((612.0, 792.0), 0.0, "left"), None)
    view = ArrangeView(AppState(project))

    qt_thread_settler()

    from deckle.app.shutdown import live_threads

    assert not any(t.isRunning() for t in live_threads(view))
