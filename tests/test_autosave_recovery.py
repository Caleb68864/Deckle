"""Offering an autosave back after a crash.

Autosave has been written on every edit and flushed on close since the
beginning, and **nothing ever offered it back**. The file was a corpse.

Detection cannot be "does the file exist": `_on_close_event` flushes the
autosave, so it exists after every clean quit. It is **mtime** -- offer
when the autosave is newer than the project file.

That rule gets the crash case right, and by accident of being correct
there it also gets close-without-saving right: those edits are real, the
user declined to save them, and offering them back on the next open is
the right thing to do rather than a false positive.
"""

from __future__ import annotations

import os

import pytest

from deckle.app.state import autosave_recovery_offer


def _pair(tmp_path, project_age: float, autosave_age: float):
    """A project and its autosave, aged in seconds relative to now."""
    project = tmp_path / "job.deckle"
    autosave = tmp_path / "job.deckle.autosave"
    project.write_text("{}", encoding="utf-8")
    autosave.write_text("{}", encoding="utf-8")
    now = 1_700_000_000
    os.utime(project, (now - project_age, now - project_age))
    os.utime(autosave, (now - autosave_age, now - autosave_age))
    return str(project), str(autosave)


def test_a_crash_leaves_a_newer_autosave_and_is_offered(tmp_path):
    project, autosave = _pair(tmp_path, project_age=100, autosave_age=0)

    assert autosave_recovery_offer(project) == autosave


def test_closing_without_saving_is_also_offered(tmp_path):
    """`_on_close_event` flushes, so this is the common case rather than
    an edge one. Those edits are real and the user should get them back."""
    project, autosave = _pair(tmp_path, project_age=50, autosave_age=0)

    assert autosave_recovery_offer(project) == autosave


def test_a_saved_project_is_silent(tmp_path):
    """Saving makes the project newer, so the prompt does not appear --
    which is what stops it appearing on every single open."""
    project, _ = _pair(tmp_path, project_age=0, autosave_age=100)

    assert autosave_recovery_offer(project) is None


def test_no_autosave_is_silent(tmp_path):
    project = tmp_path / "job.deckle"
    project.write_text("{}", encoding="utf-8")

    assert autosave_recovery_offer(str(project)) is None


def test_a_never_saved_project_has_nothing_to_recover():
    """A project with no path has no autosave path either."""
    assert autosave_recovery_offer(None) is None


def test_an_autosave_beside_a_vanished_project_is_still_offered(tmp_path):
    """The project file being gone is not a reason to discard the only
    copy of the work -- that is the case where recovery matters most."""
    autosave = tmp_path / "job.deckle.autosave"
    autosave.write_text("{}", encoding="utf-8")

    assert autosave_recovery_offer(str(tmp_path / "job.deckle")) == str(autosave)


def test_an_identical_timestamp_is_silent(tmp_path):
    """Ties go to silence. A spurious prompt teaches the user to dismiss
    prompts, which costs more than the rare recovery it would offer."""
    project, _ = _pair(tmp_path, project_age=0, autosave_age=0)

    assert autosave_recovery_offer(project) is None


# -- the prompt ----------------------------------------------------------
#
# Driven through the injected `confirm_recovery` seam, the way
# `print_dialog` injects `_confirm_resume`, so the branches are testable
# without a display.


class _Window:
    """Just enough of MainWindow to exercise the recovery branch."""

    def __init__(self, answer):
        from deckle.app.main import MainWindow

        self.confirm_recovery = lambda name: answer
        self.asked = []
        self.messages = []
        self.status_bar = type("Bar", (), {
            "showMessage": lambda _self, text: self.messages.append(text)
        })()
        self._recover = MainWindow._recover_autosave_if_offered.__get__(self)


def _project_with_autosave(tmp_path, gutter_saved, gutter_autosaved):
    """A saved project and a newer autosave differing by one setting."""
    import json

    def write(path, gutter):
        path.write_text(json.dumps({
            "version": 1, "pages": [],
            "layout": {"paper": [792.0, 612.0], "gutter_pt": gutter,
                       "binding_edge": "left"},
        }), encoding="utf-8")

    project = tmp_path / "job.deckle"
    autosave = tmp_path / "job.deckle.autosave"
    write(project, gutter_saved)
    write(autosave, gutter_autosaved)
    now = 1_700_000_000
    os.utime(project, (now - 100, now - 100))
    os.utime(autosave, (now, now))
    return str(project), str(autosave)


def _loaded(path):
    import warnings

    from deckle.core.project_io import load_project

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_project(path, check_sources=False)


def test_accepting_returns_the_autosaved_project(tmp_path):
    project_path, _ = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=True)

    recovered = window._recover(project_path, _loaded(project_path))

    assert recovered.layout.gutter_pt == 72.0


def test_declining_keeps_the_saved_project(tmp_path):
    project_path, _ = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=False)

    kept = window._recover(project_path, _loaded(project_path))

    assert kept.layout.gutter_pt == 36.0


def test_declining_deletes_the_autosave(tmp_path):
    """Otherwise the prompt returns on every open, and the one that
    matters is the one the user then dismisses without reading."""
    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=False)

    window._recover(project_path, _loaded(project_path))

    assert not os.path.exists(autosave_path)
    assert autosave_recovery_offer(project_path) is None


def test_nothing_is_asked_when_there_is_nothing_to_recover(tmp_path):
    import json

    project = tmp_path / "job.deckle"
    project.write_text(json.dumps({
        "version": 1, "pages": [],
        "layout": {"paper": [792.0, 612.0], "gutter_pt": 36.0,
                   "binding_edge": "left"},
    }), encoding="utf-8")
    asked = []
    window = _Window(answer=True)
    window.confirm_recovery = lambda name: asked.append(name) or True

    window._recover(str(project), _loaded(str(project)))

    assert asked == []


def test_an_unreadable_autosave_falls_back_to_the_saved_project(tmp_path):
    """The recovery file is the damaged one by definition here, so
    falling back to what was saved is the safe direction."""
    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    with open(autosave_path, "w", encoding="utf-8") as handle:
        handle.write("{ truncated")
    now = 1_700_000_000
    os.utime(autosave_path, (now, now))
    window = _Window(answer=True)

    kept = window._recover(project_path, _loaded(project_path))

    assert kept.layout.gutter_pt == 36.0
    assert window.messages, "the user was not told the recovery failed"
