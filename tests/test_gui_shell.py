"""The desktop shell: menus, drops, the modified marker, and one pass out.

Four roadmap items meet in the main window, and none of them can be seen
from a unit test of a pure helper:

* **N7** -- a menu bar and keyboard shortcuts. Only undo and redo were
  bound; opening, saving and printing each meant finding a button.
* **N8** -- drag and drop. Nothing in Deckle set ``acceptDrops`` at all.
* **N9** -- the modified marker and the prompt before work is thrown away.
  Autosave has run on every edit since the MVP and is not a save.
* **N4** -- writing one pass as its own PDF, for printing at a shop or on a
  second machine.

Out of process, because constructing a real ``QMainWindow`` under pytest
exits non-zero on this machine -- the same reason
``tests/test_gui_workflow.py``, ``tests/test_shutdown.py`` and
``tests/test_open_project_flush.py`` shell out. One probe answers every
question so the suite pays for one window, not four.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")

PROBE = '''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog
app = QApplication([])

import deckle.app.main as am
import deckle.app.printer_query as pq
pq.available_printer_names = lambda: []
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
)

from dataclasses import replace
from deckle.core.loader import load_pdf

fixture, workdir = sys.argv[1], sys.argv[2]
report = {}

window = am.MainWindow()

# -- the menu bar exists at all ---------------------------------------
bar = window.window.menuBar()
report["menu_titles"] = [
    a.text().replace("&", "") for a in bar.actions() if a.text()
]
report["shortcuts"] = {
    name: [s.toString() for s in action.shortcuts()]
    for name, action in window.menu_actions.items()
}
report["grid_action_labels"] = [
    a.text().replace("&", "") for a in window.arrange_view.list_widget.actions()
]
report["grid_action_contexts"] = [
    a.shortcutContext().name for a in window.arrange_view.list_widget.actions()
]
report["widget_with_children"] = Qt.ShortcutContext.WidgetWithChildrenShortcut.name
file_menu = next(
    a.menu() for a in bar.actions() if a.text().replace("&", "") == "File"
)
report["file_has_recent_submenu"] = any(
    a.menu() is window._recent_menu for a in file_menu.actions()
)
report["recent_is_no_longer_a_button"] = not hasattr(window, "recent_button")

# -- drops -------------------------------------------------------------
report["accepts_drops"] = window.window.acceptDrops()


class _Mime:
    def __init__(self, paths):
        self._urls = [QUrl.fromLocalFile(p) for p in paths]

    def hasUrls(self):
        return bool(self._urls)

    def urls(self):
        return self._urls


class _DropEvent:
    def __init__(self, paths):
        self._mime = _Mime(paths)
        self.accepted = None

    def mimeData(self):
        return self._mime

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


enter = _DropEvent([fixture])
window._on_drag_enter(enter)
report["drag_enter_pdf_accepted"] = enter.accepted

refused = _DropEvent([os.path.join(workdir, "notes.docx")])
window._on_drag_enter(refused)
report["drag_enter_docx_accepted"] = refused.accepted

# Onto an empty document, a drop can only mean one thing, so it must not
# stop to ask. The import runs on a QThread and this probe has no event
# loop, so what is asserted is the request, not its completion.
asked = []
window.confirm_drop_append = lambda name: asked.append(name) or True
drop = _DropEvent([fixture])
window._on_drop(drop)
report["drop_on_empty_accepted"] = drop.accepted
report["drop_on_empty_asked"] = list(asked)
report["drop_on_empty_import"] = [
    window.import_view._worker.source_path,
    window.import_view._worker.append,
]
window.import_view._thread.wait(30000)

# Now load the document for real, without going through the thread.
pages = list(load_pdf(fixture))
window.state.mutate(lambda p: replace(p, pages=list(pages)))
window._on_imported(pages, [])
report["page_count"] = len(window.state.project.pages)

# Onto a document that has pages, the same drop is genuinely ambiguous.
asked.clear()
drop = _DropEvent([fixture])
window._on_drop(drop)
report["drop_on_document_asked"] = list(asked)
report["drop_on_document_import"] = [
    window.import_view._worker.source_path,
    window.import_view._worker.append,
]
window.import_view._thread.wait(30000)

# Declining leaves the document alone.
window.confirm_drop_append = lambda name: None
before = window.import_view._worker
window._on_drop(_DropEvent([fixture]))
report["cancelled_drop_started_nothing"] = window.import_view._worker is before

# A dropped project opens rather than importing.
from deckle.core.project_io import save_project
project_path = os.path.join(workdir, "dropped.deckle")
save_project(window.state.project, project_path)
window.confirm_discard_changes = lambda name, action: "discard"
window._on_drop(_DropEvent([project_path]))
report["dropped_project_opened"] = window.state.project_path == project_path

# -- the modified marker ----------------------------------------------
report["clean_after_open"] = window.window.isWindowModified()
report["title_after_open"] = window.window.windowTitle()
window.state.mutate(lambda p: replace(p, pages=list(p.pages) + [p.pages[0]]))
window._on_pages_changed()
report["modified_after_edit"] = window.window.isWindowModified()
report["title_marker"] = "[*]" in window.window.windowTitle()

report["saved"] = window.save_project()
report["modified_after_save"] = window.window.isWindowModified()
report["save_was_silent"] = window.state.project_path == project_path

# An autosave is not a save.
window.state.mutate(lambda p: replace(p, pages=list(p.pages)[:-1]))
window._on_pages_changed()
window.state.flush_autosave()
report["modified_after_autosave"] = window.window.isWindowModified()

# -- closing asks ------------------------------------------------------
answers = []
window.confirm_discard_changes = lambda name, action: (
    answers.append((name, action)) or "cancel"
)
event = QCloseEvent()
window.window.closeEvent(event)
report["close_asked"] = list(answers)
report["cancel_refused_the_close"] = not event.isAccepted()

window.confirm_discard_changes = lambda name, action: "discard"
event = QCloseEvent()
window.window.closeEvent(event)
report["discard_allowed_the_close"] = event.isAccepted()

# A clean document is never worth a prompt.
window.state.mark_saved()
answers.clear()
event = QCloseEvent()
window.window.closeEvent(event)
report["clean_close_asked"] = list(answers)
report["clean_close_accepted"] = event.isAccepted()

# -- one pass as its own PDF ------------------------------------------
front_path = os.path.join(workdir, "job-front.pdf")
suggested = {}


def _fake_save(parent, caption, start, filt):
    suggested["caption"] = caption
    suggested["name"] = os.path.basename(start)
    return front_path, filt


QFileDialog.getSaveFileName = staticmethod(_fake_save)
window.choose_export_pass = lambda: "front"
window.export_single_pass()
report["pass_caption"] = suggested.get("caption")
report["pass_suggested_name"] = suggested.get("name")
report["pass_written"] = os.path.exists(front_path)
report["pass_status"] = window.status_bar.currentMessage()

import pikepdf
with pikepdf.open(front_path) as pdf:
    report["pass_pages"] = len(pdf.pages)
report["plan_sheets"] = len(window.preview_view.plan.sheets)

# Cancelling the pass picker writes nothing.
window.choose_export_pass = lambda: None
missing = os.path.join(workdir, "never.pdf")
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (missing, "PDF files (*.pdf)")
)
window.export_single_pass()
report["cancelled_pass_wrote_nothing"] = not os.path.exists(missing)

print(json.dumps(report))
sys.stdout.flush()
# Leave without unwinding, like tests/gui_workflow.py: the offscreen
# platform dies during interpreter teardown and would turn a completed run
# into a spurious failure.
os._exit(0)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    pytest.importorskip("PySide6")
    if not os.path.exists(FIXTURE):
        pytest.skip("tests/fixtures/sample.pdf is missing")
    workdir = tmp_path_factory.mktemp("gui_shell")

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["DECKLE_LOG_DIR"] = str(workdir / "logs")
    # A fresh `MainWindow` offers back any never-saved autosave it finds
    # under `data_dir("autosave")`, through a modal this probe has no event
    # loop to answer -- so it blocks until the 300s timeout and reports as
    # an error with no message. `tests/conftest.py` points the suite's data
    # root at a scratch directory, and a subprocess inherits it, but that
    # root is shared with every other test in the run: anything that leaves
    # a recovery offer there hangs this probe. Its own root, so its store
    # is empty whatever else ran first.
    data_root = str(workdir / "data")
    env["XDG_DATA_HOME"] = data_root
    env["APPDATA"] = data_root

    result = subprocess.run(
        [sys.executable, "-u", "-c", PROBE, FIXTURE, str(workdir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"the probe did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
    assert result.stdout.strip(), f"the probe produced no report\n{result.stderr[-2000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


# -- N7: the menu bar ---------------------------------------------------


def test_the_window_has_a_menu_bar(report):
    """There was none at all."""
    assert report["menu_titles"] == ["File", "Edit", "View", "Help"]


def test_open_save_and_print_have_the_shortcuts_everyone_expects(report):
    shortcuts = report["shortcuts"]
    assert "Ctrl+O" in shortcuts["open_project_dialog"]
    assert "Ctrl+S" in shortcuts["save_project"]
    assert "Ctrl+P" in shortcuts["print_document"]


def test_undo_and_redo_are_still_bound_both_ways(report):
    """Ctrl+Y and Ctrl+Shift+Z are both muscle memory, depending on which
    applications someone lives in."""
    redo = report["shortcuts"]["redo"]
    assert "Ctrl+Y" in redo and "Ctrl+Shift+Z" in redo


def test_zoom_and_sheet_navigation_reached_the_real_menu(report):
    shortcuts = report["shortcuts"]
    for name in ("zoom_in", "zoom_out", "zoom_fit", "zoom_actual"):
        assert shortcuts[name], f"{name} has no key"
    for name in ("next_sheet", "previous_sheet", "first_sheet", "last_sheet"):
        assert shortcuts[name], f"{name} has no key"


def test_the_grid_keys_belong_to_the_grid(report):
    """A bare R or S bound window-wide competes with every text field in
    the settings column."""
    labels = report["grid_action_labels"]
    assert any("Rotate" in label for label in labels)
    assert any("Skip" in label for label in labels)
    assert set(report["grid_action_contexts"]) == {report["widget_with_children"]}


def test_recent_projects_is_a_normal_submenu_now(report):
    assert report["file_has_recent_submenu"] is True
    assert report["recent_is_no_longer_a_button"] is True


# -- N8: drag and drop --------------------------------------------------


def test_the_window_accepts_drops(report):
    """Nothing set ``acceptDrops``, so dragging a PDF onto Deckle did
    nothing at all."""
    assert report["accepts_drops"] is True


def test_the_cursor_says_no_over_a_file_deckle_cannot_take(report):
    assert report["drag_enter_pdf_accepted"] is True
    assert report["drag_enter_docx_accepted"] is False


def test_a_drop_onto_an_empty_deckle_does_not_stop_to_ask(report):
    """There is nothing to add to, so there is no question to put."""
    assert report["drop_on_empty_accepted"] is True
    assert report["drop_on_empty_asked"] == []
    path, append = report["drop_on_empty_import"]
    assert path.endswith("sample.pdf")
    assert append is False


def test_a_drop_onto_a_document_offers_the_same_choice_the_import_bar_does(report):
    """Add or replace is genuinely ambiguous once there are pages -- the
    import bar has a checkbox for it precisely because both answers are
    normal for a book made of several sources. Picking one silently either
    throws the document away or buries a replacement at the end of it."""
    assert len(report["drop_on_document_asked"]) == 1
    path, append = report["drop_on_document_import"]
    assert path.endswith("sample.pdf")
    assert append is True


def test_cancelling_the_choice_imports_nothing(report):
    assert report["cancelled_drop_started_nothing"] is True


def test_a_dropped_project_is_opened_not_imported(report):
    assert report["dropped_project_opened"] is True


# -- N9: the modified marker and the prompt -----------------------------


def test_a_project_just_opened_is_not_modified(report):
    assert report["clean_after_open"] is False


def test_the_title_names_the_project_and_carries_the_marker(report):
    assert "dropped.deckle" in report["title_after_open"]
    assert "Deckle" in report["title_after_open"]


def test_an_edit_marks_the_window_modified(report):
    assert report["modified_after_edit"] is True


def test_saving_clears_the_marker_and_does_not_ask_where(report):
    assert report["saved"] is True
    assert report["modified_after_save"] is False
    assert report["save_was_silent"] is True


def test_an_autosave_does_not_clear_the_marker(report):
    """Autosave writes ``<project>.autosave`` precisely so it never touches
    the file the user named. If it cleared the marker, the window would
    stop saying "unsaved" while the user's own file was still stale."""
    assert report["modified_after_autosave"] is True


def test_closing_with_unsaved_work_asks_first(report):
    """The whole of B10 seen as a feature: an afternoon's reordering used
    to disappear on close with nothing said."""
    assert len(report["close_asked"]) == 1
    name, action = report["close_asked"][0]
    assert name == "dropped.deckle"
    assert action == "closing"


def test_cancelling_that_prompt_keeps_the_window_open(report):
    """The one reason worth refusing a close. A prompt that could not stop
    the close would be a notification, not a question."""
    assert report["cancel_refused_the_close"] is True
    assert report["discard_allowed_the_close"] is True


def test_a_saved_document_closes_without_a_word(report):
    """A prompt on the way out of a window with nothing to lose is how
    people learn to dismiss prompts without reading them."""
    assert report["clean_close_asked"] == []
    assert report["clean_close_accepted"] is True


# -- N4: one pass as its own PDF ----------------------------------------


def test_the_app_can_write_a_single_pass(report):
    """``--pass front`` has existed in the CLI since the beginning and the
    app could only ever write the whole document, so anyone taking a job
    to a copy shop had no way to produce the two files they needed."""
    assert report["pass_written"] is True


def test_the_pass_holds_one_face_per_sheet(report):
    """A pass is one side of every sheet. A file with both faces in it is
    the whole document under another name."""
    assert report["pass_pages"] == report["plan_sheets"]


def test_the_suggested_name_says_which_pass_it_is(report):
    """The two files are indistinguishable once they leave this machine,
    and printing the back pass first ruins the stack."""
    assert report["pass_suggested_name"].endswith("-front.pdf")
    assert "front" in report["pass_caption"]


def test_the_reload_instruction_is_reported_with_the_file(report):
    """Whoever prints it may never have seen Deckle."""
    assert "Load paper" in report["pass_status"]


def test_cancelling_the_pass_picker_writes_nothing(report):
    assert report["cancelled_pass_wrote_nothing"] is True
