"""H9: the card view's background jobs keep the exception.

``CardsView``'s import and thumbnail ``QThread``\\ s emitted only
``str(exc)``: nothing reached the diagnostics log, and an exception raised
without a message showed an empty error box or *"Could not render card
thumbnails: "*. Both now run through ``deckle.app.workers``, which logs
``background_job_failed`` with the type, and the UI names the type when
there is no message.

These drive the view's real threads (no injected runner), so they say the
same thing about the old code and the new.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from deckle.app import workers
from deckle.app.state import AppState
from deckle.app.views import cards_view as cards_view_module
from deckle.app.views.cards_view import CardsView
from .test_card_workspace import Timer, fixture_project


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def logged(monkeypatch):
    events = []
    monkeypatch.setattr(
        workers,
        "log_exception",
        lambda event, exc, **fields: events.append((event, type(exc).__name__)),
    )
    return events


def _wait_for(app, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    # One more pass so a queued delivery right at the end still lands.
    app.processEvents()
    return condition()


def _view(state):
    return CardsView(state, on_change=lambda: None)


def test_a_message_less_import_failure_names_its_type_and_is_logged(app, logged):
    state = AppState(fixture_project(), project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    view = _view(state)
    shown = []
    view._show_error = shown.append

    def broken(*_args):
        raise KeyError()

    view._start_import(broken, (), True)

    assert _wait_for(app, lambda: shown), "the import failure never reached the UI"
    assert shown == ["KeyError (no message)"]
    assert ("background_job_failed", "KeyError") in logged
    assert view.import_fronts_button.isEnabled(), "a failed import must release the buttons"


def test_a_message_less_thumbnail_failure_names_its_type_and_is_logged(
    app, logged, monkeypatch
):
    import deckle.core.render as render

    def broken(*_args, **_kwargs):
        raise RuntimeError()

    monkeypatch.setattr(render, "thumbnails", broken)
    state = AppState(fixture_project(), project_path="/tmp/card-ui.deckle", timer_factory=Timer)
    advisories = []
    # The constructor already asked for thumbnails before `on_advisory` could
    # be set; wait that request out, then ask again with someone listening.
    view = _view(state)
    app.processEvents()
    _wait_for(app, lambda: view._thumbnail_signature is None, timeout=2.0)
    view.on_advisory = advisories.append
    view._thumbnail_signature = None
    view._request_thumbnails()

    assert _wait_for(app, lambda: advisories), "the thumbnail failure never reached the UI"
    assert advisories == ["Could not render card thumbnails: RuntimeError (no message)"]
    assert ("background_job_failed", "RuntimeError") in logged


def test_a_real_message_passes_through_unchanged():
    from deckle.app.views.cards_view import describe_failure

    assert describe_failure(OSError("disk full")) == "disk full"
    assert describe_failure(ValueError("  ")) == "ValueError (no message)"


def test_the_card_jobs_run_on_the_shared_worker_module():
    """No private ``QThread`` subclass left to lose the exception again."""
    import inspect

    source = inspect.getsource(cards_view_module)
    assert "QThread" not in source
    assert "run_in_thread" in source
