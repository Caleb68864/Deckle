"""Two autosaves racing for the same file.

``AppState`` schedules a debounced autosave on a ``threading.Timer``, and
``flush_autosave`` cancels the pending timer and saves immediately. The
cancel is not a join: a timer that has *already fired* is inside
``save_project`` when ``flush_autosave`` starts, so both write the same
path at once. Shutdown is exactly when that happens -- the window calls
flush while the last debounce is in flight.

Neither writer is wrong; the question is what the file looks like
afterwards. Before autosave was made atomic, two writers truncating the
same path could interleave into a document that was neither of them. Now
each writes its own scratch file and renames, so the loser is discarded
whole and the winner is complete.

That is worth pinning rather than assuming, because it is a property
autosave *inherited* rather than one anybody designed for it: the atomic
write was added for the single-writer crash case, and this is a second
guarantee that came with it. A future change that reverted to writing in
place would still pass every single-threaded autosave test in
``tests/test_app_state.py``.

The autosave file is also the one Deckle reads back after a crash, so
"neither of them" is not a recoverable state -- it is the recovery file
being the thing that got corrupted.
"""

from __future__ import annotations

import json
import os
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor

import pytest

from deckle.app.state import AppState
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import load_project


def _project(gutter: float) -> Project:
    pages = [
        SourcePage(
            ref=SourceRef(path="s.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(6)
    ]
    return Project(
        pages=pages,
        layout=LayoutSettings(paper=(792.0, 612.0), gutter_pt=gutter,
                              binding_edge="left"),
        printer=None,
    )


def _load(path: str) -> Project:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_project(path, check_sources=False)


@pytest.fixture
def state(tmp_path):
    path = os.path.join(str(tmp_path), "job.deckle")
    return AppState(_project(36.0), project_path=path)


def test_many_concurrent_flushes_leave_a_readable_project(state):
    """The property that matters after a crash: the file parses, and it is
    one of the generations that was written -- never a blend."""
    def flush(i):
        state.mutate(lambda p, i=i: _project(float(i)))
        state.flush_autosave()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(flush, range(40)))

    loaded = _load(state.autosave_path)
    assert loaded.layout.gutter_pt in {float(i) for i in range(40)}
    assert len(loaded.pages) == 6


def test_a_flush_racing_a_fired_timer_does_not_tear_the_file(state, tmp_path):
    """``cancel`` is not a join. A timer that has already fired is inside
    ``save_project`` when the flush begins, so both write the same path."""
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def racer(_):
        try:
            barrier.wait(timeout=5)
            state.flush_autosave()
        except BaseException as exc:  # noqa: BLE001 -- reported, not raised
            errors.append(exc)

    for _ in range(20):
        state.mutate(lambda p: _project(72.0))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(racer, range(2)))
            barrier.reset()

    assert errors == []
    with open(state.autosave_path, encoding="utf-8") as handle:
        assert json.load(handle)["layout"]["gutter_pt"] == 72.0


def test_no_scratch_file_survives_the_race(state, tmp_path):
    """Each writer uses its own scratch name, so a loser is discarded
    rather than left behind -- otherwise a burst of edits would litter the
    user's project folder."""
    def flush(i):
        state.mutate(lambda p, i=i: _project(float(i)))
        state.flush_autosave()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(flush, range(40)))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["job.deckle.autosave"]


def test_the_autosave_never_clobbers_the_saved_project(state, tmp_path):
    """Autosave writes ``<project>.autosave``, deliberately, so a crash
    recovery file can never be mistaken for -- or overwrite -- the user's
    last explicit save."""
    saved = os.path.join(str(tmp_path), "job.deckle")
    with open(saved, "w", encoding="utf-8") as handle:
        handle.write("explicitly saved, not JSON")

    state.mutate(lambda p: _project(18.0))
    state.flush_autosave()

    with open(saved, encoding="utf-8") as handle:
        assert handle.read() == "explicitly saved, not JSON"


def test_an_unsaved_project_writes_to_the_recovery_store(tmp_path, monkeypatch):
    """Flushing a never-saved project must not be an error -- shutdown
    calls flush unconditionally -- and it must now actually write.

    This test used to assert `autosave_path is None` over six real-path
    pages and an empty directory afterwards, which pinned the hole rather
    than the property its docstring wanted. A never-saved project is keyed
    on its sources and lands under `data_dir("autosave")`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fresh = AppState(_project(36.0), project_path=None)

    fresh.mutate(lambda p: _project(1.0))
    fresh.flush_autosave()

    store = tmp_path / "deckle" / "autosave"
    assert fresh.autosave_path.startswith(str(store))
    assert os.path.exists(fresh.autosave_path)


# -- a timer must not outlive the test that scheduled it -----------------
#
# The debounce is a *daemon* timer, so nothing stops one running past the
# end of its test. It then fires inside whatever test is running half a
# second later and writes into whatever data root that test has in force.
# That is a real flake, not a theoretical one: it put a fourth
# `.deckle.autosave` into a `tests/test_unsaved_autosave.py` test that
# planted exactly three and asserted on all of them, once in nine
# full-suite runs. `tests/conftest.py` cancels them; these two pin that it
# does, from both ends.


def _live_timers():
    return [
        thread for thread in threading.enumerate()
        if isinstance(thread, threading.Timer) and thread.is_alive()
    ]


def test_the_guard_cancels_a_debounce_a_test_left_running(
    tmp_path, monkeypatch, cancel_autosave_timers
):
    """The helper itself: it reports what it cancelled, and nothing lands."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    leaky = AppState(_project(36.0), project_path=None, autosave_delay_s=30.0)

    leaky.mutate(lambda p: _project(2.0))

    assert _live_timers(), "the premise failed: no real debounce was scheduled"

    cancelled = cancel_autosave_timers()

    assert cancelled >= 1
    assert _live_timers() == []
    assert not (tmp_path / "deckle" / "autosave").exists(), (
        "the cancelled debounce still wrote a recovery file"
    )


def test_a_debounce_left_running_by_one_test_is_gone_by_the_next(tmp_path,
                                                                monkeypatch):
    """Half of a pair. This one deliberately leaks; the next asserts the
    leak was cleaned up, which is what the autouse fixture is *for*.

    A 30-second debounce, so the timer cannot have fired on its own
    between the two tests -- the only thing that can end it is the guard.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    leaky = AppState(_project(36.0), project_path=None, autosave_delay_s=30.0)

    leaky.mutate(lambda p: _project(3.0))

    assert _live_timers(), "the premise failed: no real debounce was scheduled"


def test_and_so_this_test_starts_with_no_debounce_in_flight():
    """The other half. Reddens if the autouse guard stops being autouse."""
    assert _live_timers() == [], (
        "an autosave debounce from an earlier test is still in flight; it "
        "will fire inside this one and write into this test's data root"
    )
