"""B17, the part that needs no printer: Save PDF, Auto-crop and Open project.

Print submission is still synchronous, and its row in the roadmap says so.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from deckle.app.workers import Job, run_inline

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def test_a_job_holds_its_outcome_rather_than_raising():
    def _boom(_cancel):
        raise ValueError("no")

    job = Job(_boom)
    job.run()
    assert isinstance(job.error, ValueError)
    assert job.result is None


def test_a_cancelled_job_delivers_nothing(qapp):
    from deckle.app.workers import _deliver

    seen = []
    job = Job(lambda _cancel: 1)
    job.run()
    job.cancel.set()
    _deliver(job, seen.append, seen.append)
    assert seen == []


def test_run_in_thread_works_off_the_gui_thread_and_delivers_on_it(qapp):
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread

    from deckle.app.workers import run_in_thread

    parent = QObject()
    delivered = []
    job = run_in_thread(
        lambda _cancel: threading.current_thread(),
        lambda result: delivered.append((result, threading.current_thread())),
        delivered.append,
        parent,
    )
    thread = job.thread
    assert thread.wait(10000)
    QCoreApplication.processEvents()
    # This thread's only: flushing every deferred delete in the process frees
    # objects other tests' Python wrappers still point at, and segfaults.
    QCoreApplication.sendPostedEvents(thread, QEvent.Type.DeferredDelete)

    assert delivered[0][1] is threading.main_thread()
    assert delivered[0][0] is not threading.main_thread()
    assert job.thread is None
    assert parent.findChildren(QThread) == []


# -- Save PDF ------------------------------------------------------------------


def _window(path_problem=False):
    shown = []
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=shown.append),
        _jobs={},
        _job_runner=run_inline,
        window=None,
        save_pdf_button=SimpleNamespace(setEnabled=lambda value: shown.append(("enabled", value))),
        _sync_document_actions=lambda: shown.append("synced"),
    )
    return window, shown


def test_an_export_says_so_first_and_finishes_on_the_gui_thread():
    from deckle.app import exporting

    window, shown = _window()
    calls = []
    exporting._start_export(
        window, lambda: calls.append("wrote"), lambda: calls.append("done"), calls.append
    )
    assert calls == ["wrote", "done"]
    assert shown == [("enabled", False), "synced"]


def test_an_export_failure_reaches_on_error_and_re_syncs_the_button():
    from deckle.app import exporting

    window, shown = _window()
    calls = []

    def _fail():
        raise OSError("disk full")

    exporting._start_export(window, _fail, lambda: calls.append("done"), calls.append)

    assert isinstance(calls[0], OSError)
    assert "synced" in shown


def test_a_second_export_is_refused_while_one_is_writing():
    from deckle.app import exporting

    window, shown = _window()
    window._jobs["export"] = SimpleNamespace(thread=object())

    assert exporting._export_running(window) is True
    assert "Still writing" in shown[-1]

    window._jobs["export"] = SimpleNamespace(thread=None)
    assert exporting._export_running(window) is False


@pytest.mark.parametrize("function", ["save_pdf_as", "export_single_pass"])
def test_neither_export_path_calls_export_directly(function):
    """The structural half: a direct call is the frozen window."""
    from deckle.app import exporting

    tree = ast.parse(Path(exporting.__file__).read_text(encoding="utf-8"))
    body = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    # `export(...)` may appear only inside the lambda handed to `_start_export`.
    lambdas = [n for n in ast.walk(body) if isinstance(n, ast.Lambda)]
    inside = {id(c) for lam in lambdas for c in ast.walk(lam)}
    direct = [
        n for n in ast.walk(body)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "export"
        and id(n) not in inside
    ]
    assert direct == []


# -- the whole window -----------------------------------------------------------

PROBE = '''
import json, os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QFileDialog
app = QApplication([])

import deckle.app.main as am
import deckle.app.exporting as ex
import threading

am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None, quiet=False: self._apply_printers([])
)
window = am.MainWindow()

from dataclasses import replace
from deckle.core.loader import load_pdf
pages = load_pdf(sys.argv[1])
window.state.mutate(lambda p: replace(p, pages=list(pages)))
window._on_imported(pages, [])
window.preview_view.plan = am.recompute_plan(window.state.project)

target = sys.argv[2]
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (target, ""))

release = threading.Event()
threads = []
real_export = ex.export
def slow_export(*args, **kwargs):
    threads.append(threading.current_thread() is threading.main_thread())
    release.wait(30)
    return real_export(*args, **kwargs)
ex.export = slow_export

report = {}
window.save_pdf()
app.processEvents()
report["painted_while_running"] = window.status_bar.currentMessage().startswith("Exporting")
report["button_disabled"] = not window.save_pdf_button.isEnabled()
report["file_yet"] = os.path.exists(target)

window.save_pdf()
report["second_refused"] = "Still writing" in window.status_bar.currentMessage()

release.set()
deadline = time.monotonic() + 30
while window._jobs["export"].thread is not None and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
app.processEvents()

report["on_main_thread"] = threads[0]
report["file_after"] = os.path.exists(target)
report["saved"] = window.status_bar.currentMessage().startswith("Saved")
report["button_back"] = window.save_pdf_button.isEnabled()
print("REPORT " + json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


def test_the_real_window_stays_alive_through_a_save(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, FIXTURE, str(tmp_path / "out.pdf")],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": REPO_ROOT},
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("REPORT ")]
    assert lines, result.stdout + result.stderr
    assert json.loads(lines[0][len("REPORT "):]) == {
        "painted_while_running": True,
        "button_disabled": True,
        "file_yet": False,
        "second_refused": True,
        "on_main_thread": False,
        "file_after": True,
        "saved": True,
        "button_back": True,
    }


# -- Auto-crop -------------------------------------------------------------------


def _panel(qapp, monkeypatch, insets=((1.0, 2.0, 3.0, 4.0), None), runner=run_inline):
    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel
    from deckle.core import render
    from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef

    def _measure(pages):
        return insets

    monkeypatch.setattr(render, "auto_crop_insets", _measure)
    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(2)
    ]
    panel = LayoutPanel(AppState(Project(
        pages=pages,
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
        printer=None,
    )))
    panel._job_runner = runner
    panel._schedule_composite = lambda: None
    return panel


def test_auto_crop_fills_the_boxes_through_the_job(qapp, monkeypatch):
    panel = _panel(qapp, monkeypatch)
    said = []
    panel.schedule_saved.connect(said.append)

    panel._on_auto_crop()

    assert said[0] == "Measuring the ink on 2 page(s)..."
    assert panel.state.project.layout.crop_odd_pt == (1.0, 2.0, 3.0, 4.0)
    assert panel.auto_crop_button.isEnabled()


def test_auto_crop_failure_is_said_and_gives_the_button_back(qapp, monkeypatch):
    panel = _panel(qapp, monkeypatch)
    from deckle.core import render

    def _boom(pages):
        raise OSError("source moved")

    monkeypatch.setattr(render, "auto_crop_insets", _boom)
    said = []
    panel.schedule_saved.connect(said.append)

    panel._on_auto_crop()

    assert said[-1] == "Could not measure the ink: source moved"
    assert panel.auto_crop_button.isEnabled()


def test_a_measurement_of_a_document_that_moved_on_is_dropped(qapp, monkeypatch):
    """The pages were copied before the job started, so an edit made while it
    ran would otherwise be cropped by measurements of something else."""
    from dataclasses import replace

    held = []

    def _hold(fn, on_done, on_error, parent):
        held.append((fn, on_done))
        return SimpleNamespace(thread=None)

    panel = _panel(qapp, monkeypatch, runner=_hold)
    panel._on_auto_crop()
    assert not panel.auto_crop_button.isEnabled()

    panel.state.mutate(lambda p: replace(p, pages=p.pages[:1]))
    said = []
    panel.schedule_saved.connect(said.append)
    fn, on_done = held[0]
    on_done(fn(threading.Event()))

    assert panel.state.project.layout.crop_odd_pt is None
    assert "changed while" in said[-1]
    assert panel.auto_crop_button.isEnabled()


def test_an_empty_document_starts_no_job(qapp, monkeypatch):
    from dataclasses import replace

    panel = _panel(qapp, monkeypatch, runner=lambda *a: pytest.fail("started a job"))
    panel.state.mutate(lambda p: replace(p, pages=[]))
    panel._on_auto_crop()


# -- shutdown --------------------------------------------------------------------


def test_shutdown_waits_for_a_job_without_cancelling_it():
    from deckle.app.shutdown import stop_background_work

    waited = []
    thread = SimpleNamespace(isRunning=lambda: True, wait=lambda ms: waited.append(ms) or True)
    job = SimpleNamespace(thread=thread, cancel=threading.Event())

    stop_background_work((), 10, jobs=[job], job_timeout_ms=999)

    assert waited == [999]
    assert not job.cancel.is_set(), "an export the user asked for was abandoned"


def test_shutdown_tolerates_a_finished_job():
    from deckle.app.shutdown import stop_background_work

    stop_background_work((), 10, jobs=[SimpleNamespace(thread=None)])


# -- Open project ----------------------------------------------------------------


def _saved_project(tmp_path, source=None):
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project
    from deckle.core.project_io import save_project

    path = tmp_path / "book.deckle"
    save_project(
        Project(
            pages=load_pdf(source or FIXTURE),
            layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left"),
            printer=None,
        ),
        str(path),
    )
    return str(path)


def _opening_window(monkeypatch, runner, *, confirm=True):
    from deckle.app import project_actions

    shown = []
    swapped = []
    view = SimpleNamespace(state=None, refresh=lambda: None, refresh_from_project=lambda: None)
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=shown.append),
        state=SimpleNamespace(project="before", mark_unsaved=lambda: None),
        window=None,
        _jobs={},
        _job_runner=runner,
        _confirm_discard=lambda action: confirm,
        _recover_autosave_if_offered=lambda p, project: swapped.append(project) or project,
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
        SimpleNamespace(project=project, mark_unsaved=lambda: None)
    ))
    return window, shown, swapped


def _holding_runner(held):
    def _run(fn, on_done, on_error, parent):
        job = SimpleNamespace(cancel=threading.Event(), thread=object())
        held.append((job, fn, on_done, on_error))
        return job

    return _run


def test_opening_hashes_the_sources_off_the_gui_thread(tmp_path, monkeypatch):
    """Nothing has been swapped, and nothing hashed, when `open_project` returns."""
    from deckle.app import project_actions
    from deckle.core import project_io

    path = _saved_project(tmp_path)
    hashed = []
    real = project_io._sha256_file
    monkeypatch.setattr(project_io, "_sha256_file", lambda p: hashed.append(p) or real(p))
    held = []
    window, shown, swapped = _opening_window(monkeypatch, _holding_runner(held))

    assert project_actions.open_project(window, path) is True

    assert hashed == [] and swapped == []
    assert shown[-1] == "Opening book.deckle..."

    _job, fn, on_done, _on_error = held[0]
    on_done(fn(threading.Event()))

    assert len(hashed) == 1
    assert len(swapped) == 1
    assert shown[-1].startswith("Opened book.deckle")


@pytest.mark.parametrize("damage, says", [("remove", "is missing"), ("rewrite", "has changed")])
def test_a_source_that_went_wrong_is_reported_from_the_job(tmp_path, monkeypatch, damage, says):
    import shutil

    from deckle.app import project_actions

    source = tmp_path / "src.pdf"
    shutil.copy(FIXTURE, source)
    path = _saved_project(tmp_path, str(source))
    if damage == "remove":
        source.unlink()
    else:
        source.write_bytes(source.read_bytes() + b"%changed")
    window, shown, swapped = _opening_window(monkeypatch, run_inline)

    assert project_actions.open_project(window, path) is False

    assert says in shown[-1]
    assert swapped == []


def test_edits_made_during_the_hash_are_offered_a_save(tmp_path, monkeypatch):
    """The prompt came before the job, so it never saw them."""
    from deckle.app import project_actions

    path = _saved_project(tmp_path)
    held = []
    window, shown, swapped = _opening_window(monkeypatch, _holding_runner(held), confirm=False)
    project_actions.open_project(window, path)

    window.state.project = "edited meanwhile"
    held[0][2](None)

    assert swapped == [], "edits were replaced without asking"
    assert window.state.project == "edited meanwhile"
    assert shown[-1] == "Did not open book.deckle."


def test_a_second_open_supersedes_the_first(tmp_path, monkeypatch):
    from deckle.app import project_actions

    path = _saved_project(tmp_path)
    held = []
    window, _shown, _swapped = _opening_window(monkeypatch, _holding_runner(held))

    project_actions.open_project(window, path)
    project_actions.open_project(window, path)

    assert held[0][0].cancel.is_set()
    assert not held[1][0].cancel.is_set()


def test_a_bad_project_file_fails_before_any_job(tmp_path, monkeypatch):
    from deckle.app import project_actions

    bad = tmp_path / "bad.deckle"
    bad.write_text("not json")
    window, shown, _swapped = _opening_window(
        monkeypatch, lambda *a: pytest.fail("started a job")
    )
    assert project_actions.open_project(window, str(bad)) is False
    assert "Cannot open bad.deckle" in shown[-1]


def test_load_project_still_checks_by_default(tmp_path):
    """The CLI's path, which must not have lost the check in the split."""
    import shutil

    from deckle.core.project_io import SourceChangedWarning, load_project

    source = tmp_path / "src.pdf"
    shutil.copy(FIXTURE, source)
    path = _saved_project(tmp_path, str(source))
    source.write_bytes(source.read_bytes() + b"%changed")

    with pytest.raises(SourceChangedWarning):
        load_project(path, allowed_roots=(str(tmp_path),))


OPEN_PROBE = '''
import json, os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

import deckle.app.main as am
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None, quiet=False: self._apply_printers([])
)
window = am.MainWindow()
before = window.state

report = {"started": window.open_project(sys.argv[1])}
report["says_opening"] = window.status_bar.currentMessage().startswith("Opening")
report["not_swapped_yet"] = window.state is before

deadline = time.monotonic() + 30
while window.state is before and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)

report["swapped"] = window.state is not before
report["pages"] = len(window.state.project.pages)
report["says_opened"] = window.status_bar.currentMessage().startswith("Opened")
print("REPORT " + json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


def test_the_real_window_opens_a_project_through_a_real_thread(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", OPEN_PROBE, _saved_project(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": REPO_ROOT},
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("REPORT ")]
    assert lines, result.stdout + result.stderr
    assert json.loads(lines[0][len("REPORT "):]) == {
        "started": True,
        "says_opening": True,
        "not_swapped_yet": True,
        "swapped": True,
        "pages": 2,
        "says_opened": True,
    }
