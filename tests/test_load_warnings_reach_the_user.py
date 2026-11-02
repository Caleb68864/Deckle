"""B23: every warning a ``.deckle`` load raises reaches somebody.

``catch_warnings(record=True)`` suppresses what it records. The CLI then
re-emitted two chosen classes, so a project carrying settings this build does
not know opened on a layout that was not the one in the file, with nothing
said. The desktop app logged nothing and showed nothing for the same warning.
"""

from __future__ import annotations

import json
import warnings
from types import SimpleNamespace

from deckle.core.models import LayoutSettings, Project
from deckle.core.project_io import save_project


def _project_with_a_future_field(path):
    save_project(
        Project(
            pages=[],
            layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
            printer=None,
        ),
        path,
    )
    data = json.loads(path.read_text())
    data["layout"]["future_field"] = 1
    path.write_text(json.dumps(data))


def test_the_cli_says_which_fields_it_dropped_on_stderr(tmp_path, capsys, monkeypatch):
    from deckle.cli import commands

    path = tmp_path / "book.deckle"
    _project_with_a_future_field(path)
    monkeypatch.chdir(tmp_path)

    assert commands._load_project_or_report(str(path)) is not None

    captured = capsys.readouterr()
    assert "note: " in captured.err
    assert "future_field" in captured.err
    assert "future_field" not in captured.out


def test_the_cli_has_no_allow_list_to_fall_behind(tmp_path, capsys, monkeypatch):
    """A warning class nobody has written yet must arrive too."""
    from deckle.cli import commands

    def _load(*_args, **_kwargs):
        warnings.warn("something this build has not met", DeprecationWarning)
        return "project"

    monkeypatch.setattr(commands, "load_project", _load)

    assert commands._load_project_or_report(str(tmp_path / "x.deckle")) == "project"
    assert "something this build has not met" in capsys.readouterr().err


def test_the_desktop_app_shows_which_fields_it_dropped(tmp_path, monkeypatch):
    from deckle.app import project_actions

    path = tmp_path / "book.deckle"
    _project_with_a_future_field(path)

    shown: list[str] = []
    view = SimpleNamespace(state=None, refresh=lambda: None,
                           refresh_from_project=lambda: None)
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=shown.append),
        state=SimpleNamespace(mark_unsaved=lambda: None),
        _recover_autosave_if_offered=lambda p, project: project,
        _refresh_recent_menu=lambda: None,
        _sync_title=lambda: None,
        _flush_outgoing_state=lambda: None,
        _on_pages_changed=lambda: None,
        import_view=view,
        arrange_view=view,
        layout_panel=view,
    )
    monkeypatch.setattr(project_actions, "recent", SimpleNamespace(record=lambda p: None))
    monkeypatch.setattr(project_actions, "clear_sheet_cache", lambda: None)
    monkeypatch.setattr(project_actions, "AppState", lambda project, project_path=None: (
        SimpleNamespace(mark_unsaved=lambda: None)
    ))

    assert project_actions.open_project(window, str(path)) is True

    assert "future_field" in shown[-1]
