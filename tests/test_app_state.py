"""Tests for deckle.app.state -- AppState, undo/redo, autosave.

These import ``deckle.app.state`` and the plain (Qt-free) helper
functions in ``deckle.app.views.import_view`` / ``arrange_view`` directly,
never instantiating a ``QWidget`` -- so they run headless with no display
server needed.
"""

from __future__ import annotations

import os
import threading
import time

import pytest


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


@pytest.fixture(autouse=True)
def data_root(tmp_path_factory, monkeypatch):
    """Point the never-saved autosave store at a temporary directory.

    Autouse and unconditional: `paths._root` reads the environment at call
    time, so a test that does not set both variables writes into the
    developer's real `~/.local/share/deckle` and leaves recovery offers
    behind that their next launch will show them.
    """
    root = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("XDG_DATA_HOME", str(root))
    monkeypatch.setenv("APPDATA", str(root))
    return root


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


def test_autosave_follows_the_path_a_first_save_gives_the_project(
    tmp_path, data_root
):
    """A project that starts unnamed autosaves once Save names it.

    This is the ordinary session, not an edge case: ``main()`` opens a
    blank project with no path, and Save is what supplies one. When the
    path was computed once in ``__init__`` it stayed ``None`` for the life
    of the window, so from the first save onward the project autosaved
    nowhere -- the whole session was unprotected by the feature meant to
    protect it.

    Before Save the autosave now goes to the never-saved store rather than
    nowhere; what matters here is that it MOVES to the project's own file.
    """
    state = AppState(_make_project(2), timer_factory=_ImmediateTimer)
    assert state.autosave_path.startswith(str(data_root))

    project_path = str(tmp_path / "proj.deckle")
    # What Save does, and all it does -- see `MainWindow._save_project_to`.
    state.project_path = project_path
    assert state.autosave_path == f"{project_path}.autosave"

    state.mutate(lambda p: set_rotation(p, 0, 180))

    loaded = load_project(state.autosave_path, check_sources=False)
    assert loaded.pages[0].rotate_deg == 180


def test_autosave_repoints_when_the_project_is_saved_somewhere_else(tmp_path):
    """Save As moves the autosave with the project.

    The stronger half of the same rule: a cached path would keep writing the
    *new* project's contents into the *old* project's autosave, so opening
    the old file afterwards would be offered someone else's work as its
    recovery.
    """
    first = str(tmp_path / "first.deckle")
    second = str(tmp_path / "second.deckle")
    state = AppState(_make_project(2), project_path=first, timer_factory=_ImmediateTimer)

    state.project_path = second
    state.mutate(lambda p: toggle_skip(p, 0))

    assert state.autosave_path == f"{second}.autosave"
    assert not os.path.exists(f"{first}.autosave")
    loaded = load_project(state.autosave_path, check_sources=False)
    assert loaded.pages[0].skipped is True


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


def test_a_project_with_no_sources_has_no_autosave(data_root):
    """The one case that still writes nothing.

    This test used to assert the same of a project with three real pages,
    which pinned the hole rather than the rule: a never-saved project is
    now keyed on what it was imported from. A window with nothing imported
    has nothing worth recovering, so it still has nowhere to write -- and
    flushing must still not be an error.
    """
    state = AppState(Project(
        pages=[],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    ))

    assert state.autosave_path is None

    state.flush_autosave()

    assert not (data_root / "deckle" / "autosave").exists()


# -- import: populates state, virtualized thumbnails, off-thread ---------


def _named_page(source: str, index: int) -> SourcePage:
    """A page that remembers which source it came from."""
    ref = SourceRef(
        path=source, page_index=index, sha256="abc", width_pt=612.0, height_pt=792.0
    )
    return SourcePage(ref=ref, rotate_deg=0, skipped=False)


def test_a_second_import_can_add_pages_instead_of_replacing_them(tmp_path, monkeypatch):
    """A book made of more than one source is the normal case here.

    The README promises "PDFs and image folders, interleaved", and every
    layer below this already delivered it: `Project.pages` is a flat list
    whose entries each name their own source, the imposer never asks where
    a page came from, and `.deckle` round-trips a mixed list. Only the
    import verb was missing -- it always wrote `pages=page_list`, so the
    second source silently discarded the first.
    """
    scan = [_named_page("scan.pdf", i) for i in range(3)]
    plates = [_named_page("plates.pdf", i) for i in range(2)]
    sources = {"scan.pdf": scan, "plates.pdf": plates}
    monkeypatch.setattr(
        "deckle.app.views.import_view.load_pdf",
        lambda path: sources[os.path.basename(path)],
    )
    state = AppState(_make_project(0))

    load_and_apply_import(state, str(tmp_path / "scan.pdf"))
    added, _warnings = load_and_apply_import(
        state, str(tmp_path / "plates.pdf"), append=True
    )

    assert [page.ref.path for page in state.project.pages] == (
        ["scan.pdf"] * 3 + ["plates.pdf"] * 2
    )
    # The return value describes what was just added, not the whole
    # document -- the status line says "Added 2 page(s)", not "Added 5".
    assert len(added) == 2


def test_appending_keeps_replacing_as_the_default(tmp_path, monkeypatch):
    """An import that quietly appended to a document the user meant to
    replace would be its own surprise, so the old behaviour is the default
    and the checkbox is what opts into the new one."""
    scan = [_named_page("scan.pdf", i) for i in range(3)]
    plates = [_named_page("plates.pdf", i) for i in range(2)]
    sources = {"scan.pdf": scan, "plates.pdf": plates}
    monkeypatch.setattr(
        "deckle.app.views.import_view.load_pdf",
        lambda path: sources[os.path.basename(path)],
    )
    state = AppState(_make_project(0))

    load_and_apply_import(state, str(tmp_path / "scan.pdf"))
    load_and_apply_import(state, str(tmp_path / "plates.pdf"))

    assert [page.ref.path for page in state.project.pages] == ["plates.pdf"] * 2


def test_adding_pages_is_undoable_like_any_other_change(tmp_path, monkeypatch):
    """It goes through `mutate`, so Ctrl+Z takes the added pages back off
    rather than leaving the user to delete them by hand."""
    plates = [_named_page("plates.pdf", i) for i in range(2)]
    monkeypatch.setattr(
        "deckle.app.views.import_view.load_pdf", lambda path: list(plates)
    )
    state = AppState(_make_project(3))

    load_and_apply_import(state, str(tmp_path / "plates.pdf"), append=True)
    assert len(state.project.pages) == 5

    state.undo()

    assert len(state.project.pages) == 3


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


# --- removing and skipping whole ranges ----------------------------------


def test_remove_pages_deletes_them():
    from deckle.app.state import remove_pages

    result = remove_pages(_make_project(5), [1, 3])

    assert [p.ref.page_index for p in result.pages] == [0, 2, 4]


def test_remove_pages_ignores_order_and_repeats():
    from deckle.app.state import remove_pages

    project = _make_project(5)

    assert remove_pages(project, [3, 1, 3]) == remove_pages(project, [1, 3])


def test_remove_pages_does_not_mutate_the_original():
    from deckle.app.state import remove_pages

    project = _make_project(5)

    remove_pages(project, [0])

    assert len(project.pages) == 5


def test_remove_pages_rejects_a_bad_index():
    """Raises rather than skipping it, matching `set_rotation` and
    `toggle_skip` -- a silently ignored index removes the wrong page."""
    import pytest

    from deckle.app.state import remove_pages

    with pytest.raises(IndexError):
        remove_pages(_make_project(5), [9])


def test_removing_every_page_leaves_an_empty_document():
    from deckle.app.state import remove_pages

    assert remove_pages(_make_project(5), list(range(5))).pages == []


def test_remove_is_undoable():
    from deckle.app.state import remove_pages

    state = AppState(_make_project(5))

    state.mutate(lambda p: remove_pages(p, [0, 1]))
    state.undo()

    assert [p.ref.page_index for p in state.project.pages] == [0, 1, 2, 3, 4]


def test_skip_pages_states_rather_than_toggles():
    """A range is stated, so naming a page that is already skipped is not
    a request to bring it back."""
    from deckle.app.state import skip_pages

    project = skip_pages(_make_project(4), [0])

    result = skip_pages(project, [0, 1])

    assert [p.skipped for p in result.pages] == [True, True, False, False]


def test_skip_pages_never_removes_anything():
    from deckle.app.state import skip_pages

    result = skip_pages(_make_project(4), [1, 2])

    assert [p.ref.page_index for p in result.pages] == [0, 1, 2, 3]


def test_skip_pages_rejects_a_page_the_document_does_not_have():
    import pytest

    from deckle.app.state import skip_pages

    with pytest.raises(ValueError, match="no page 400"):
        skip_pages(_make_project(4), [399])


# -- unsaved work ------------------------------------------------------
#
# Autosave has run on every edit since the MVP and is not a save: it writes
# `<project>.autosave` precisely so it can never clobber the file the user
# named, and it is only ever offered back as crash recovery. So an
# afternoon's reordering has always been lost from the user's own file on
# close, silently. `dirty` is what lets the window say so and stop to ask.


def test_a_freshly_loaded_project_is_not_modified():
    state = AppState(_make_project(3))
    assert state.dirty is False


def test_any_edit_marks_the_project_modified():
    state = AppState(_make_project(3))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    assert state.dirty is True


def test_saving_clears_the_marker():
    state = AppState(_make_project(3))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.mark_saved()
    assert state.dirty is False


def test_editing_after_a_save_marks_it_again():
    state = AppState(_make_project(3))
    state.mark_saved()
    state.mutate(lambda p: toggle_skip(p, 0))
    assert state.dirty is True


def test_undoing_back_to_the_saved_state_clears_the_marker():
    """`Project` is frozen, so undo restores the very object that was
    saved. A window still titled "modified" over a document identical to
    the one on disk teaches its user to ignore the marker."""
    state = AppState(_make_project(3))
    state.mark_saved()
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.undo()
    assert state.dirty is False


def test_redoing_marks_it_again():
    state = AppState(_make_project(3))
    state.mark_saved()
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.undo()
    state.redo()
    assert state.dirty is True


def test_an_autosave_is_not_a_save(tmp_path):
    """The distinction the whole feature rests on. An autosave that cleared
    the marker would stop the window saying "unsaved" while the file the
    user named was still stale, and the close prompt -- the thing standing
    between a session's work and the bin -- would never appear."""
    state = AppState(_make_project(3), project_path=str(tmp_path / "book.deckle"))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.flush_autosave()

    assert os.path.exists(str(tmp_path / "book.deckle.autosave"))
    assert state.dirty is True


def test_recovered_work_can_be_declared_unsaved():
    """Content loaded from an autosave exists nowhere but in memory until
    the user saves it, so the window that accepted the recovery must not
    open clean and let them close it again."""
    state = AppState(_make_project(3))
    assert state.dirty is False
    state.mark_unsaved()
    assert state.dirty is True
