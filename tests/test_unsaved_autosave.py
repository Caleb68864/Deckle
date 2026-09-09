"""Autosave for a project that has never been saved.

Import 200 scanned pages, spend an hour in Arrange, lose power. Nothing was
written: ``AppState.autosave_path`` was ``None`` for the whole session, so
every ``_schedule_autosave`` returned immediately and ``flush_autosave``
wrote nothing at shutdown either. The recovery design called the unsaved
case "silent", and it was.

A never-saved project is keyed on **what it was made from**, so the same
import lands in the same file across crashes rather than accumulating one
file per launch -- and so reordering pages, which is exactly the work being
protected, keeps writing to the same place.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from deckle.app.state import (
    UNSAVED_AUTOSAVE_KEEP,
    UNSAVED_AUTOSAVE_MAX_AGE_S,
    AppState,
    UnsavedAutosave,
    insert_blank,
    reorder_pages,
    toggle_skip,
    unsaved_autosave_key,
    unsaved_autosave_label,
    unsaved_autosave_offers,
    unsaved_autosave_path,
)
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import load_project


class _ImmediateTimer:
    """A ``threading.Timer`` stand-in that runs synchronously.

    The real debounce is a daemon thread; a test that writes files and then
    asserts on the directory has to drive it synchronously or sleep past
    500ms, which makes the suite slow and flaky.
    """

    def __init__(self, interval, function, args=None, kwargs=None):
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


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    """Both variables, every test.

    ``paths._root`` reads the environment at call time, so an unguarded
    test writes into the developer's real ``~/.local/share/deckle`` and
    leaves recovery offers behind that their next launch will show them.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _page(index: int, path: str = "/scan/book.pdf", sha: str = "a" * 64):
    return SourcePage(
        ref=SourceRef(path=path, page_index=index, sha256=sha,
                      width_pt=612.0, height_pt=792.0),
        rotate_deg=0,
        skipped=False,
    )


def _project(pages) -> Project:
    return Project(
        pages=list(pages),
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    )


def _store(data_root):
    return data_root / "deckle" / "autosave"


# -- the key -------------------------------------------------------------


def test_an_unsaved_project_still_gets_an_autosave(data_root):
    state = AppState(_project([_page(0), _page(1)]),
                     timer_factory=_ImmediateTimer)

    state.mutate(lambda p: toggle_skip(p, 0))

    assert state.autosave_path.startswith(str(_store(data_root)))
    assert os.path.exists(state.autosave_path)
    loaded = load_project(state.autosave_path, check_sources=False)
    assert len(loaded.pages) == 2
    assert loaded.pages[0].skipped is True


def test_the_key_is_the_same_for_the_same_sources():
    """Page order is deliberately not in the key: reordering is the work
    being protected and must land in the same file. Nor is a page imported
    twice, nor an inserted blank, which references no file."""
    one = _project([_page(0), _page(1)])
    other = _project([_page(1), _page(0), _page(0)])
    other = insert_blank(other, 0)

    assert unsaved_autosave_key(one.pages) == unsaved_autosave_key(other.pages)
    assert unsaved_autosave_path(one.pages) == unsaved_autosave_path(other.pages)


def test_reordering_keeps_writing_to_the_same_file(data_root):
    state = AppState(_project([_page(0), _page(1), _page(2)]),
                     timer_factory=_ImmediateTimer)
    state.mutate(lambda p: toggle_skip(p, 0))
    first = state.autosave_path

    before = {p.name for p in _store(data_root).glob("*.deckle.autosave")}

    state.mutate(lambda p: reorder_pages(p, 0, 2))

    assert state.autosave_path == first
    after = {p.name for p in _store(data_root).glob("*.deckle.autosave")}
    assert after == before, "the reorder started a second recovery file"


def test_the_key_changes_when_a_source_changes():
    """A different document is a different recovery, not an overwrite."""
    one = _project([_page(0, sha="a" * 64)])
    other = _project([_page(0, sha="b" * 64)])

    assert unsaved_autosave_path(one.pages) != unsaved_autosave_path(other.pages)


def test_the_key_ignores_how_the_path_was_spelled():
    plain = _project([_page(0, path="/scan/book.pdf")])
    noisy = _project([_page(0, path="/scan/./sub/../book.pdf")])

    assert unsaved_autosave_key(plain.pages) == unsaved_autosave_key(noisy.pages)


def test_an_empty_project_has_no_unsaved_autosave(data_root):
    """A window with nothing imported has nothing worth recovering."""
    state = AppState(_project([]), timer_factory=_ImmediateTimer)

    state.mutate(lambda p: p)
    state.flush_autosave()

    assert state.autosave_path is None
    assert not _store(data_root).exists()


def test_a_project_of_only_blanks_has_no_unsaved_autosave():
    project = insert_blank(_project([]), 0)

    assert unsaved_autosave_path(project.pages) is None


# -- what happens once it really is saved --------------------------------


def test_saving_the_project_moves_the_autosave_and_deletes_the_unsaved_one(
    tmp_path, data_root
):
    state = AppState(_project([_page(0), _page(1)]),
                     timer_factory=_ImmediateTimer)
    state.mutate(lambda p: toggle_skip(p, 0))
    unsaved = state.autosave_path
    assert os.path.exists(unsaved)

    # What Save does, and all it does.
    state.project_path = str(tmp_path / "job.deckle")
    state.discard_unsaved_autosave()
    state.flush_autosave()

    assert os.path.exists(str(tmp_path / "job.deckle.autosave"))
    assert not os.path.exists(unsaved)


def test_discarding_what_was_never_written_is_not_an_error(data_root):
    state = AppState(_project([_page(0)]))

    state.discard_unsaved_autosave()  # nothing has been written yet

    assert not _store(data_root).exists()


# -- keeping the store readable ------------------------------------------


def test_only_the_newest_ten_unsaved_autosaves_survive(data_root):
    store = _store(data_root)
    store.mkdir(parents=True)
    stale = []
    for index in range(13):
        path = store / f"{index:016x}.deckle.autosave"
        path.write_text('{"version": 1, "pages": [], "layout": {}}', encoding="utf-8")
        os.utime(path, (1_000_000 + index, 1_000_000 + index))
        stale.append(path)

    state = AppState(_project([_page(0)]), timer_factory=_ImmediateTimer)
    state.mutate(lambda p: toggle_skip(p, 0))

    # The real autosave is the newest of all, so the four oldest planted
    # files are the ones that go: 13 planted + 1 written, keeping 10.
    for path in stale[:4]:
        assert not path.exists()
    for path in stale[4:]:
        assert path.exists()
    assert len(list(store.glob("*.deckle.autosave"))) == UNSAVED_AUTOSAVE_KEEP


def test_eviction_leaves_a_saved_projects_autosave_alone(tmp_path, data_root):
    """Only the never-saved store is pruned, and only by its own name."""
    store = _store(data_root)
    store.mkdir(parents=True)
    bystander = store / "notes.txt"
    bystander.write_text("not an autosave", encoding="utf-8")
    for index in range(12):
        path = store / f"{index:016x}.deckle.autosave"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_000_000 + index, 1_000_000 + index))

    state = AppState(_project([_page(0)]), timer_factory=_ImmediateTimer)
    state.mutate(lambda p: toggle_skip(p, 0))

    assert bystander.exists()


def test_a_saved_project_does_not_prune_the_unsaved_store(tmp_path, data_root):
    store = _store(data_root)
    store.mkdir(parents=True)
    for index in range(12):
        (store / f"{index:016x}.deckle.autosave").write_text("{}", encoding="utf-8")

    state = AppState(_project([_page(0)]),
                     project_path=str(tmp_path / "job.deckle"),
                     timer_factory=_ImmediateTimer)
    state.mutate(lambda p: toggle_skip(p, 0))

    assert len(list(store.glob("*.deckle.autosave"))) == 12


# -- offering them back --------------------------------------------------


def _write_offer(store, name: str, pages, mtime: float | None = None):
    store.mkdir(parents=True, exist_ok=True)
    path = store / name
    path.write_text(json.dumps({
        "version": 1,
        "pages": pages,
        "layout": {"paper": [612, 792], "gutter_pt": 0, "binding_edge": "left"},
        "printer": None,
    }), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _stored_page(path: str):
    return {
        "path": path, "page_index": 0, "sha256": "a" * 64,
        "width_pt": 612.0, "height_pt": 792.0,
        "rotate_deg": 0, "skipped": False,
    }


def test_offers_are_newest_first_and_skip_stale_ones(data_root):
    store = _store(data_root)
    now = time.time()
    _write_offer(store, "aaa.deckle.autosave", [_stored_page("/a.pdf")], now - 10)
    _write_offer(store, "bbb.deckle.autosave", [_stored_page("/b.pdf")], now - 100)
    _write_offer(
        store, "ccc.deckle.autosave", [_stored_page("/c.pdf")],
        now - UNSAVED_AUTOSAVE_MAX_AGE_S - 60,
    )

    offers = unsaved_autosave_offers(now=now)

    assert [os.path.basename(o.path) for o in offers] == [
        "aaa.deckle.autosave", "bbb.deckle.autosave"
    ]


def test_an_offer_survives_a_source_that_has_moved(data_root):
    """`load_project` verifies every source hash and refuses a project
    whose source has moved -- which is exactly when the recovery matters
    most, so the offer list reads the JSON directly."""
    store = _store(data_root)
    _write_offer(store, "aaa.deckle.autosave", [
        _stored_page("/gone/book.pdf"), _stored_page("/gone/book.pdf"),
    ])

    offers = unsaved_autosave_offers()

    assert len(offers) == 1
    assert offers[0].page_count == 2
    assert offers[0].first_source == "/gone/book.pdf"


def test_a_corrupt_autosave_is_skipped_not_raised(data_root):
    store = _store(data_root)
    store.mkdir(parents=True)
    (store / "bad.deckle.autosave").write_text("not json", encoding="utf-8")
    (store / "worse.deckle.autosave").write_text('{"pages": 7}', encoding="utf-8")
    _write_offer(store, "good.deckle.autosave", [_stored_page("/a.pdf")])

    offers = unsaved_autosave_offers()

    assert [os.path.basename(o.path) for o in offers] == ["good.deckle.autosave"]


def test_a_missing_store_offers_nothing(data_root):
    assert unsaved_autosave_offers() == []


def test_the_offer_label_names_the_source_and_the_page_count():
    offer = UnsavedAutosave(
        path="x", modified_at=time.mktime((2026, 9, 8, 14, 30, 0, 0, 0, -1)),
        page_count=7, first_source="/tmp/scan/book.pdf",
    )

    assert unsaved_autosave_label(offer).startswith("book.pdf -- 7 page(s), ")
    assert "2026-09-08 14:30" in unsaved_autosave_label(offer)


def test_an_offer_with_no_source_is_still_named():
    offer = UnsavedAutosave(path="x", modified_at=0.0, page_count=0,
                            first_source="")

    assert unsaved_autosave_label(offer).startswith("Untitled -- ")


# -- offering it back at startup ------------------------------------------
#
# Constructing a real `QMainWindow` under pytest kills the process on this
# machine, so `_offer_unsaved_recovery` is driven unbound against a stub,
# the way the printer paths are.


class _StatusBar:
    def __init__(self):
        self.message = None

    def showMessage(self, message):
        self.message = message


class _StateHolder:
    def __init__(self):
        self.state = None


class _Arrange(_StateHolder):
    def __init__(self):
        super().__init__()
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1


class _Panel(_StateHolder):
    def __init__(self):
        super().__init__()
        self.refreshed = 0

    def refresh_from_project(self):
        self.refreshed += 1


class _FakeWindow:
    """Enough of MainWindow to drive `_offer_unsaved_recovery`."""

    def __init__(self, state, chooser):
        self.state = state
        self.status_bar = _StatusBar()
        self.import_view = _StateHolder()
        self.arrange_view = _Arrange()
        self.layout_panel = _Panel()
        self.confirm_unsaved_recovery = chooser
        self.pages_changed_calls = 0

    def _on_pages_changed(self):
        self.pages_changed_calls += 1


def _offer(window):
    import deckle.app.main as app_main

    app_main.MainWindow._offer_unsaved_recovery(window)


def test_startup_offers_the_newest_unsaved_session(data_root):
    store = _store(data_root)
    now = time.time()
    _write_offer(store, "old.deckle.autosave", [_stored_page("/a.pdf")], now - 500)
    _write_offer(
        store, "new.deckle.autosave",
        [_stored_page("/b.pdf"), _stored_page("/b.pdf")], now - 5,
    )
    original = AppState(_project([]))
    window = _FakeWindow(original, lambda offers: offers[0])

    _offer(window)

    assert len(window.state.project.pages) == 2
    assert window.state.project_path is None, "recovered work is still unsaved"
    assert window.status_bar.message == "Recovered 2 unsaved page(s)."
    assert window.arrange_view.state is window.state
    assert window.layout_panel.state is window.state
    assert window.import_view.state is window.state
    assert window.arrange_view.refreshed == 1
    assert window.layout_panel.refreshed == 1
    assert window.pages_changed_calls == 1


def test_the_recovered_state_keeps_writing_to_the_same_file(data_root):
    """The whole round trip: a crashed session's file is picked back up,
    and the new `AppState` re-derives the same key from the same sources,
    so continuing to edit does not start a second recovery file."""
    crashed = AppState(_project([_page(0), _page(1)]),
                       timer_factory=_ImmediateTimer)
    crashed.mutate(lambda p: toggle_skip(p, 0))
    written = crashed.autosave_path

    window = _FakeWindow(AppState(_project([])), lambda offers: offers[0])
    _offer(window)

    assert window.state.autosave_path == written
    assert window.state.project.pages[0].skipped is True

    before = {p.name for p in _store(data_root).glob("*.deckle.autosave")}

    window.state.mutate(lambda p: toggle_skip(p, 1))

    after = {p.name for p in _store(data_root).glob("*.deckle.autosave")}
    assert after == before, "editing recovered work started a second file"


def test_declining_leaves_the_file_on_disk(data_root):
    """`_recover_autosave_if_offered` deletes on decline because the work
    also exists in the user's own `.deckle`. Here it does not -- this file
    is the only copy -- so a mis-click must not be destructive."""
    store = _store(data_root)
    path = _write_offer(store, "one.deckle.autosave", [_stored_page("/b.pdf")])
    original = AppState(_project([]))
    window = _FakeWindow(original, lambda offers: None)

    _offer(window)

    assert path.exists()
    assert window.state is original
    assert window.status_bar.message is None


def test_nothing_to_offer_asks_nothing(data_root):
    def refuse(offers):  # pragma: no cover -- must not be reached
        raise AssertionError("the user was prompted with an empty list")

    original = AppState(_project([]))
    window = _FakeWindow(original, refuse)

    _offer(window)

    assert window.state is original


def test_an_unreadable_recovery_is_reported_not_raised(data_root):
    """The offer list reads JSON directly and tolerates a moved source, so
    a file can look offerable and still fail to load as a project."""
    store = _store(data_root)
    store.mkdir(parents=True, exist_ok=True)
    path = store / "one.deckle.autosave"
    path.write_text(json.dumps({
        "version": 1,
        "pages": [_stored_page("/b.pdf")],
        "layout": {"binding_edge": "middle"},
        "printer": None,
    }), encoding="utf-8")
    original = AppState(_project([]))
    window = _FakeWindow(original, lambda offers: offers[0])

    _offer(window)

    assert window.state is original
    assert "Could not read the recovered work" in window.status_bar.message


def test_recovery_is_offered_only_for_a_fresh_window():
    """A window opened ON a project already has its own recovery prompt;
    two for one launch is one too many."""
    import ast
    import importlib

    path = importlib.import_module("deckle.app.main").__file__
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    window = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    init = next(
        node for node in ast.walk(window)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )

    guarded = [
        branch for branch in ast.walk(init)
        if isinstance(branch, ast.If)
        and "_offer_unsaved_recovery" in ast.dump(branch)
        and "project_path" in ast.dump(branch.test)
    ]

    assert guarded, (
        "the startup recovery offer is not inside `if project_path is None:`"
    )
