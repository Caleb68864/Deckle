"""Tests for deckle.app.state -- AppState, undo/redo, autosave.

These import ``deckle.app.state`` and the plain (Qt-free) helper
functions in ``deckle.app.views.import_view`` / ``arrange_view`` directly,
never instantiating a ``QWidget`` -- so they run headless with no display
server needed.
"""

from __future__ import annotations

import threading
import time


from deckle.app.state import (
    AppState,
    insert_blank,
    make_blank_page,
    reorder_pages,
    set_rotation,
    toggle_skip,
)
from deckle.app.views.arrange_view import request_visible_thumbnails, visible_range
from deckle.app.views.import_view import load_and_apply_import
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import load_project


def _make_page(index: int) -> SourcePage:
    ref = SourceRef(
        path="src.pdf", page_index=index, sha256="abc", width_pt=612.0, height_pt=792.0
    )
    return SourcePage(ref=ref, rotate_deg=0, skipped=False)


def _make_project(n_pages: int = 3) -> Project:
    layout = LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left")
    return Project(pages=[_make_page(i) for i in range(n_pages)], layout=layout, printer=None)


class _ImmediateTimer:
    """A ``threading.Timer`` stand-in that runs synchronously.

    Lets autosave-related tests assert on side effects without sleeping
    past the real 500ms debounce window, while still exercising the
    "cancel the previous scheduled save" code path faithfully.
    """

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args or []
        self.kwargs = kwargs or {}
        self.cancelled = False
        self.daemon = False

    def start(self):
        if not self.cancelled:
            self.function(*self.args, **self.kwargs)

    def cancel(self):
        self.cancelled = True


# -- AppState structural / undo / redo ----------------------------------


def test_app_state_exposes_project_undo_redo_mutate():
    state = AppState(_make_project())
    assert state.project is not None
    assert hasattr(state, "undo")
    assert hasattr(state, "redo")
    assert hasattr(state, "mutate")


def test_mutate_snapshots_before_applying():
    project = _make_project()
    state = AppState(project)
    state.mutate(lambda p: reorder_pages(p, 0, 2))
    assert state.can_undo
    restored = state.undo()
    assert [pg.ref.page_index for pg in restored.pages] == [0, 1, 2]


def test_reorder_then_undo_restores_previous_order_exactly():
    state = AppState(_make_project(5))
    original_order = [pg.ref.page_index for pg in state.project.pages]

    state.mutate(lambda p: reorder_pages(p, 0, 3))
    reordered_order = [pg.ref.page_index for pg in state.project.pages]
    assert reordered_order != original_order

    restored = state.undo()
    assert [pg.ref.page_index for pg in restored.pages] == original_order


def test_redo_reapplies_an_undone_mutation():
    state = AppState(_make_project(5))
    state.mutate(lambda p: reorder_pages(p, 0, 3))
    after_mutate = [pg.ref.page_index for pg in state.project.pages]
    state.undo()
    redone = state.redo()
    assert [pg.ref.page_index for pg in redone.pages] == after_mutate


def test_60_mutations_bounds_undo_stack_at_depth_50_without_error():
    state = AppState(_make_project(1))
    for i in range(60):
        state.mutate(lambda p: set_rotation(p, 0, (p.pages[0].rotate_deg + 90) % 360))
    # 60 mutations pushed 60 snapshots; bounded deque(maxlen=50) silently
    # discarded the oldest 10 rather than growing or raising.
    assert len(state._undo_stack) == 50


def test_rotate_skip_insert_blank_helpers_route_through_project_value_types():
    project = _make_project(2)
    rotated = set_rotation(project, 0, 90)
    assert rotated.pages[0].rotate_deg == 90
    assert project.pages[0].rotate_deg == 0  # original untouched (frozen)

    skipped = toggle_skip(project, 1)
    assert skipped.pages[1].skipped is True

    blanked = insert_blank(project, 1)
    assert len(blanked.pages) == 3
    assert blanked.pages[1].ref.path == make_blank_page(project.layout.paper).ref.path
    assert blanked.pages[1].ref.page_index == -1


# -- autosave -------------------------------------------------------------


def test_mutate_schedules_debounced_autosave(tmp_path):
    project_path = str(tmp_path / "proj.deckle")
    state = AppState(_make_project(2), project_path=project_path, timer_factory=_ImmediateTimer)

    state.mutate(lambda p: reorder_pages(p, 0, 1))

    loaded = load_project(state.autosave_path, check_sources=False)
    assert [pg.ref.page_index for pg in loaded.pages] == [pg.ref.page_index for pg in state.project.pages]


def test_autosave_survives_kill_and_reopen(tmp_path):
    project_path = str(tmp_path / "proj.deckle")
    state = AppState(_make_project(3), project_path=project_path, timer_factory=_ImmediateTimer)

    state.mutate(lambda p: set_rotation(p, 0, 180))
    # Simulate the process dying right after the mutation: nothing further
    # is called on `state`. Reopening from the autosave path should
    # restore the last mutated state.
    reopened = load_project(state.autosave_path, check_sources=False)
    assert reopened.pages[0].rotate_deg == 180


def test_flush_autosave_cancels_pending_timer_and_saves_immediately(tmp_path):
    project_path = str(tmp_path / "proj.deckle")
    saved = threading.Event()
    real_timer = threading.Timer

    def _tracking_timer(interval, function, *a, **kw):
        def _wrapped():
            function(*a, **kw)
            saved.set()

        return real_timer(interval, _wrapped)

    state = AppState(_make_project(2), project_path=project_path, timer_factory=_tracking_timer)
    state.mutate(lambda p: toggle_skip(p, 0))
    state.flush_autosave()

    assert not saved.is_set()  # the real debounce timer was cancelled, never fired
    loaded = load_project(state.autosave_path, check_sources=False)
    assert loaded.pages[0].skipped is True


def test_no_project_path_means_autosave_is_a_noop():
    state = AppState(_make_project(1))
    assert state.autosave_path is None
    # Should not raise even though there is nowhere to write.
    state.mutate(lambda p: toggle_skip(p, 0))
    state.flush_autosave()


# -- import: populates state, virtualized thumbnails, off-thread ---------


def test_import_populates_project_pages(tmp_path, monkeypatch):
    fake_pages = [_make_page(i) for i in range(300)]
    monkeypatch.setattr("deckle.app.views.import_view.load_pdf", lambda path: fake_pages)

    state = AppState(_make_project(0))
    pages, warnings = load_and_apply_import(state, str(tmp_path / "big.pdf"))

    assert len(pages) == 300
    assert len(state.project.pages) == 300
    assert warnings == []


def test_visible_range_stays_well_under_full_document_size():
    start, count = visible_range(scroll_index=100, viewport_count=20, total=300)
    assert count < 100  # far under the 300-page document
    assert start >= 0
    assert start + count <= 300


def test_thumbnails_requested_only_for_visible_range_not_all_300(monkeypatch):
    calls = []

    def _fake_thumbnails(pages, start, count, dpi=36):
        calls.append(count)
        return []

    monkeypatch.setattr("deckle.app.views.arrange_view.thumbnails", _fake_thumbnails)

    pages = [_make_page(i) for i in range(300)]
    request_visible_thumbnails(pages, scroll_index=0, viewport_count=40)

    assert len(calls) == 1
    assert calls[0] < 100  # nowhere near all 300 pages


def test_import_and_mutate_never_call_render_synchronously(tmp_path, monkeypatch):
    """No 'thumbnails'/'render_sheet' call happens as a side effect of
    load_and_apply_import or AppState.mutate -- those are the two
    operations invoked directly on the main thread; the real UI wraps
    thumbnail fetches in a QThread (ArrangeView.request_visible_thumbnails)
    which this test deliberately does not call.
    """
    render_called = []
    monkeypatch.setattr(
        "deckle.core.render.thumbnails", lambda *a, **kw: render_called.append("thumbnails")
    )
    monkeypatch.setattr(
        "deckle.core.render.render_sheet", lambda *a, **kw: render_called.append("render_sheet")
    )
    fake_pages = [_make_page(i) for i in range(300)]
    monkeypatch.setattr("deckle.app.views.import_view.load_pdf", lambda path: fake_pages)

    state = AppState(_make_project(0))
    load_and_apply_import(state, str(tmp_path / "big.pdf"))
    state.mutate(lambda p: set_rotation(p, 0, 90))

    assert render_called == []


def test_300_page_import_completes_well_under_100ms_on_calling_thread(tmp_path, monkeypatch):
    fake_pages = [_make_page(i) for i in range(300)]
    monkeypatch.setattr("deckle.app.views.import_view.load_pdf", lambda path: fake_pages)

    state = AppState(_make_project(0))
    started = time.monotonic()
    load_and_apply_import(state, str(tmp_path / "big.pdf"))
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 100
