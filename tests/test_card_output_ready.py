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
    """``_output_is_ready`` and the button sync must use the seam rather than
    keep private copies with their own ``except``, or this test file proves
    nothing about the window. ``card_output_blocker`` is the seam
    ``card_output_ready`` is a yes/no view of; the window takes the text."""
    import inspect

    for function in (app_main.MainWindow._output_is_ready, app_main._project_output_ready):
        source = inspect.getsource(function)
        assert "card_output_blocker(" in source
        assert "except" not in source


# -- follow-ups recorded under H8 ---------------------------------------------


def _raise(exc):
    def _fn(*args, **kwargs):
        raise exc

    return _fn


def test_the_button_sync_check_logs_an_unexpected_failure_too(logged, monkeypatch):
    """``_project_output_ready`` is what greys the buttons on every sync. It
    caught only ``ValueError``/``TypeError``, so any other planning bug
    escaped into whichever Qt slot happened to be syncing the buttons."""
    monkeypatch.setattr(app_main, "impose_project", _raise(KeyError()))

    assert app_main._project_output_ready(fixture_project(True)) is False
    assert logged == [("card_output_check_failed", "KeyError")]


def test_the_button_sync_check_still_answers_quietly_for_a_deck_that_does_not_fit(
    logged, monkeypatch
):
    monkeypatch.setattr(app_main, "impose_project", _raise(ValueError("too big")))

    assert app_main._project_output_ready(fixture_project(True)) is False
    assert logged == []


@pytest.mark.parametrize("exc", [ValueError(), TypeError("  ")])
def test_an_expected_failure_with_no_message_still_blocks_output(logged, monkeypatch, exc):
    """``str(ValueError())`` is ``""``, and ``""`` is how a plan says it is
    fine -- so an imposing failure with no message used to *enable* output
    on a plan that could not be made."""
    from types import SimpleNamespace

    monkeypatch.setattr(app_main, "impose_project", _raise(exc))
    window = SimpleNamespace(
        state=SimpleNamespace(project=fixture_project(True)), _output_problem=""
    )
    window._plan_for_project = lambda project: app_main.MainWindow._plan_for_project(
        window, project
    )
    window._card_plan_problem = lambda project: app_main.MainWindow._card_plan_problem(
        window, project
    )

    assert app_main.MainWindow._output_is_ready(window) is False
    assert window._output_problem.strip()
    assert app_main._project_output_ready(fixture_project(True)) is False


def _print_window(plan_problem):
    from types import SimpleNamespace

    shown = []
    window = SimpleNamespace(
        state=SimpleNamespace(project=fixture_project(True)),
        _output_problem="",
        _printers=["Laser"],
        refresh_printers=lambda **kwargs: None,
        status_bar=SimpleNamespace(showMessage=shown.append),
        _card_plan_problem=plan_problem,
    )
    window._output_is_ready = lambda: app_main.MainWindow._output_is_ready(window)
    return window, shown


def test_print_blocked_by_a_bug_does_not_say_there_is_nothing_imported(logged):
    """The deck is imported and paired; what stopped Print was a planning
    bug. Telling the operator to import a document sends them looking for
    a problem they do not have."""
    window, shown = _print_window(_raise(KeyError()))

    app_main.MainWindow.print_document(window)

    assert shown and shown[-1] != app_main.NOTHING_TO_PRINT_MESSAGE
    assert shown[-1] == app_main.OUTPUT_CHECK_FAILED_MESSAGE
    assert "diagnostics" in shown[-1].lower()


def test_print_blocked_by_an_unpaired_deck_says_what_is_missing(logged):
    window, shown = _print_window(_raise(AssertionError("must not plan")))
    window.state.project = fixture_project(False)

    app_main.MainWindow.print_document(window)

    assert shown and shown[-1] != app_main.NOTHING_TO_PRINT_MESSAGE
    assert shown[-1] == " ".join(
        app_main.card_output_problems(window.state.project.card_deck)
    )
