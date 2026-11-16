"""H8: the card-project output check logs a bug instead of hiding it.

``MainWindow._output_is_ready`` wrapped planning in ``except Exception:
return False``. ``_plan_for_project`` already turns the expected
``ValueError``/``TypeError`` into ``_output_problem``, so anything that got
that far was a bug -- and it greyed out Save PDF and Print with no message
and nothing in the diagnostics log. The check is now
``deckle.app.main.card_output_ready``, testable without a window.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from deckle.app import main as app_main
from .test_card_workspace import fixture_project


@pytest.fixture
def logged(monkeypatch):
    events = []
    monkeypatch.setattr(
        app_main,
        "log_exception",
        lambda event, exc, **fields: events.append((event, type(exc).__name__)),
    )
    return events


def test_an_unexpected_planning_failure_is_logged_and_blocks_output(logged):
    def broken(project):
        raise KeyError()

    assert app_main.card_output_ready(fixture_project(True), broken) is False
    assert logged == [("card_output_check_failed", "KeyError")]


def test_an_expected_planning_problem_blocks_output_without_a_log_line(logged):
    """The ``ValueError``/``TypeError`` path is unchanged: it arrives as
    text for the window to show, and is not a bug to log."""
    assert (
        app_main.card_output_ready(fixture_project(True), lambda p: "cards do not fit")
        is False
    )
    assert logged == []


def test_a_clean_plan_is_ready(logged):
    assert app_main.card_output_ready(fixture_project(True), lambda p: "") is True
    assert logged == []


def test_an_unpaired_deck_is_not_planned_at_all(logged):
    def must_not_plan(project):
        raise AssertionError("planned an incomplete deck")

    assert app_main.card_output_ready(fixture_project(False), must_not_plan) is False
    assert logged == []


def test_the_window_routes_its_card_check_through_the_seam(monkeypatch):
    """``_output_is_ready`` must use ``card_output_ready`` rather than keep a
    private copy with its own ``except``, or this test file proves nothing
    about the window."""
    import inspect

    source = inspect.getsource(app_main.MainWindow._output_is_ready)
    assert "card_output_ready(" in source
    assert "except" not in source
