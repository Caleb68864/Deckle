"""B35's remaining clauses, each small and each confirmed failing before."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from deckle.core.marks import SEWING_MARGIN_PT, sewing_stations
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import save_project

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module")
def _qapp():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _project(**layout):
    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(4)
    ]
    return Project(
        pages=pages,
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=36.0,
                              binding_edge="left", **layout),
        printer=None,
    )


# -- thumbnails ---------------------------------------------------------------


def test_the_grid_does_not_keep_the_raw_buffer(_qapp):
    """~150 MB for 300 pages, for a role nothing reads."""
    from deckle.app.state import AppState
    from deckle.app.views.arrange_view import ArrangeView
    from deckle.core.render import RenderedPage

    view = ArrangeView(AppState(_project()))
    rendered = RenderedPage(width=2, height=2, rgba=bytes([255]) * 16)
    worker = SimpleNamespace(start=0, rendered=[rendered], cancel=SimpleNamespace(is_set=lambda: False))
    view._worker = worker
    view._on_thumbnails_ready(worker)

    item = view.list_widget.item(0)
    assert not item.icon().isNull()
    assert item.data(0x0100) is None


# -- one click, one step of undo ----------------------------------------------


def _panel(_qapp, **layout):
    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel

    return LayoutPanel(AppState(_project(**layout)))


def test_linking_is_one_step_of_undo_and_redraws(_qapp):
    panel = _panel(
        _qapp, margins_linked=False, margin_top_pt=36.0, margin_bottom_pt=18.0, margin_outer_pt=9.0
    )
    before = panel.state.project
    plans = []
    panel.layout_changed.connect(plans.append)

    panel.link_margins_check.click()

    layout = panel.state.project.layout
    assert (layout.margin_top_pt, layout.margin_bottom_pt, layout.margin_outer_pt) == (36.0,) * 3
    assert layout.margins_linked is True
    assert plans, "linking did not redraw the preview"
    assert panel.margin_spinboxes["margin_outer_pt"].points() == pytest.approx(36.0)

    panel.state.undo()
    assert panel.state.project == before


def test_use_printer_margins_is_one_step_of_undo_and_redraws(_qapp):
    from deckle.core.profiles import BUILTIN_PRESETS

    for linked in (False, True):
        panel = _panel(_qapp, margins_linked=linked)
        panel.profile = next(iter(BUILTIN_PRESETS.values()))
        before = panel.state.project
        plans = []
        panel.layout_changed.connect(plans.append)

        panel._on_use_printer_margins()

        layout = panel.state.project.layout
        assert layout.margin_top_pt == layout.margin_bottom_pt == layout.margin_outer_pt > 0
        assert len(plans) == 1
        assert panel.margin_spinboxes["margin_outer_pt"].points() == pytest.approx(
            layout.margin_outer_pt
        )

        panel.state.undo()
        assert panel.state.project == before


# -- autosave recovery ---------------------------------------------------------


def _saved_with_a_newer_autosave(tmp_path):
    path = tmp_path / "book.deckle"
    project = Project(
        pages=[],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
        printer=None,
    )
    save_project(project, str(path))
    autosave = str(path) + ".autosave"
    save_project(project, autosave)
    future = time.time() + 60
    os.utime(autosave, (future, future))
    return str(path), autosave


def test_accepting_recovery_does_not_ask_again(tmp_path):
    from deckle.app import project_actions
    from deckle.app.state import autosave_recovery_offer

    path, autosave = _saved_with_a_newer_autosave(tmp_path)
    window = SimpleNamespace(confirm_recovery=lambda name: True)

    assert project_actions.recover_autosave_if_offered(window, path, "saved") != "saved"

    assert autosave_recovery_offer(path) is None, "the prompt comes back on the next open"
    assert not os.path.exists(autosave)
    assert os.path.exists(autosave + ".recovered"), "the bytes were not kept"


def test_a_second_recovery_replaces_the_first_copy(tmp_path):
    from deckle.app import project_actions

    path, autosave = _saved_with_a_newer_autosave(tmp_path)
    with open(autosave + ".recovered", "w", encoding="utf-8") as f:
        f.write("an older generation")
    window = SimpleNamespace(confirm_recovery=lambda name: True)

    project_actions.recover_autosave_if_offered(window, path, "saved")

    with open(autosave + ".recovered", encoding="utf-8") as f:
        assert f.read() != "an older generation"


def test_failing_to_move_it_aside_still_recovers(tmp_path, monkeypatch):
    from deckle.app import project_actions

    path, _autosave = _saved_with_a_newer_autosave(tmp_path)

    def _refuse(*_args):
        raise OSError("read-only")

    monkeypatch.setattr(project_actions, "move_over", _refuse)
    window = SimpleNamespace(confirm_recovery=lambda name: True)

    assert project_actions.recover_autosave_if_offered(window, path, "saved") != "saved"


# -- schedule's order of work --------------------------------------------------


def test_schedule_refuses_a_bad_output_before_touching_the_document(
    tmp_path, monkeypatch, capsys
):
    from deckle.cli import commands

    def _no(*_args, **_kwargs):
        raise AssertionError("the document was loaded before the output was checked")

    monkeypatch.setattr(commands, "_resolve_input", _no)
    args = SimpleNamespace(
        source=FIXTURE, output=str(tmp_path / "missing" / "s.txt"), json=False
    )

    assert commands._cmd_schedule(args) == 1
    assert "error" in capsys.readouterr().err


# -- invariants under -O ----------------------------------------------------------


def test_the_layout_invariants_survive_python_dash_o():
    probe = (
        "from deckle.core import layout\n"
        "assert False, 'not running under -O'\n"
        "try:\n"
        "    layout._require(False, 'held')\n"
        "except AssertionError as exc:\n"
        "    print(exc)\n"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert result.stdout.strip() == "held", result.stderr


def test_the_invariants_are_not_plain_asserts():
    import ast

    import deckle.core.layout as layout

    with open(layout.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    asserts = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert asserts == []


# -- sewing stations on a short sheet -------------------------------------------


@pytest.mark.parametrize("sheet_h", [60.0, 10.0])
def test_stations_on_a_short_sheet_do_not_march_backwards(sheet_h):
    marks = sewing_stations(sheet_h, 100.0, 3)
    assert [m.y0 for m in marks] == [SEWING_MARGIN_PT] * 3


def test_stations_on_an_ordinary_sheet_are_unchanged():
    marks = sewing_stations(100.0 + 2 * SEWING_MARGIN_PT, 100.0, 3)
    assert [m.y0 for m in marks] == [
        SEWING_MARGIN_PT, SEWING_MARGIN_PT + 50.0, SEWING_MARGIN_PT + 100.0
    ]
