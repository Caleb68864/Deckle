"""B13: an import knows whether it is still the one anybody wants.

Before this, two quick imports applied in completion order, an import running
while a project was opened landed in a state nothing reads and announced its
pages anyway, and nothing waited for an import thread on the way out.
"""

from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import LayoutSettings, Project  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _state():
    from deckle.app.state import AppState

    return AppState(
        Project(
            pages=[],
            layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
            printer=None,
        )
    )


class _Thread:
    """Starts nothing, so a test decides when each import finishes."""

    def __init__(self, parent=None):
        self.finished = self
        self.running = False
        self.deleted_later = 0

    def connect(self, slot):
        if getattr(slot, "__name__", "") != "deleteLater":
            self.slot = slot

    def deleteLater(self):
        self.deleted_later += 1

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def finish(self):
        """What Qt does when ``run`` returns."""
        self.run()
        self.running = False
        self.slot()


def _view(state=None):
    from deckle.app.views.import_view import ImportView

    view = ImportView(state or _state())
    view._QThread = _Thread
    view.seen = []
    view.imported.connect(lambda pages, warnings: view.seen.append(len(pages)))
    return view


def test_a_cancelled_load_is_returned_but_not_applied():
    from deckle.app.views.import_view import load_and_apply_import

    state = _state()
    cancel = threading.Event()
    cancel.set()

    pages, _warnings = load_and_apply_import(state, FIXTURE, cancel=cancel)

    assert len(pages) == 2
    assert state.project.pages == ()


def test_cancelling_during_the_load_still_skips_the_mutation(monkeypatch):
    """The load is the long part, so this is the case that actually happens."""
    from deckle.app.views import import_view

    state = _state()
    cancel = threading.Event()
    real = import_view._load_source

    def _slow(path):
        pages = real(path)
        cancel.set()
        return pages

    monkeypatch.setattr(import_view, "_load_source", _slow)
    import_view.load_and_apply_import(state, FIXTURE, cancel=cancel)
    assert state.project.pages == ()


def test_a_replacing_import_supersedes_the_one_running():
    view = _view()
    view.import_path(FIXTURE, append=False)
    first_thread = view._thread
    view.import_path(FIXTURE, append=False)
    second_thread = view._thread

    second_thread.finish()
    assert view.seen == [2]
    first_thread.finish()

    assert view.seen == [2], "the superseded import reported over the current one"
    assert len(view.state.project.pages) == 2


def test_adding_while_an_import_runs_keeps_both_in_the_order_asked():
    """Cancelling the first would silently lose pages the user asked to keep."""
    view = _view()
    view.import_path(FIXTURE, append=True)
    first_thread, first_worker = view._thread, view._worker

    view.import_path(FIXTURE, append=True)

    assert view._worker is first_worker
    assert not first_worker.cancel.is_set()
    first_thread.finish()
    assert view._thread is not first_thread
    view._thread.finish()

    assert view.seen == [2, 2]
    assert len(view.state.project.pages) == 4


def test_a_replacing_import_drops_additions_still_waiting():
    view = _view()
    view.import_path(FIXTURE, append=True)
    first_thread = view._thread
    view.import_path(FIXTURE, append=True)
    view.import_path(FIXTURE, append=False)
    replacing_thread = view._thread

    first_thread.finish()
    replacing_thread.finish()

    assert view.seen == [2]
    assert view._thread is None
    assert len(view.state.project.pages) == 2


def test_replacing_the_state_abandons_the_import_running_against_the_old_one():
    outgoing = _state()
    view = _view(outgoing)
    view.import_path(FIXTURE, append=False)
    thread = view._thread
    view.import_path(FIXTURE, append=True)

    incoming = _state()
    view.state = incoming
    thread.finish()

    assert view.seen == [], "announced pages that are in no project"
    assert outgoing.project.pages == ()
    assert incoming.project.pages == ()
    assert view._thread is None, "the waiting addition ran against the new project"


def test_assigning_the_same_state_cancels_nothing():
    state = _state()
    view = _view(state)
    view.import_path(FIXTURE, append=False)
    view.state = state
    assert not view._worker.cancel.is_set()


def test_a_real_import_thread_is_freed_and_settled_by_shutdown():
    """Against real ``QThread``s: the thread is freed, and the shutdown wait
    does not trip over the deleted wrapper."""
    from PySide6.QtCore import QCoreApplication, QEvent, QThread

    from deckle.app import shutdown
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    view.import_path(FIXTURE, append=False)
    thread = view._thread
    thread.wait(30000)
    QCoreApplication.processEvents()
    # This thread's only: a global flush frees objects other tests still hold.
    QCoreApplication.sendPostedEvents(thread, QEvent.Type.DeferredDelete)

    assert view._thread is None
    assert view.widget.findChildren(QThread) == []

    view.import_path(FIXTURE, append=False)
    worker = view._worker
    shutdown.stop_background_work((view,), 30000)
    assert worker.cancel.is_set()
    assert not view._thread.isRunning()
