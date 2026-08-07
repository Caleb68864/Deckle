"""The recent-projects list.

Opening a project started from nowhere -- `QFileDialog.getOpenFileName`
was passed an empty start directory, so every open began at whatever the
OS considered default, not at the folder the last three projects came
from. This is small, and it is friction on a path taken several times a
session.

Two decisions are worth stating because the obvious implementation gets
them wrong:

**A missing file is hidden, not forgotten.** A path on a disconnected
network share or an unplugged drive is temporarily unreachable, not
retired. Pruning the store on read would quietly erase real history the
first time a drive was unmounted, so listing filters and only an explicit
removal deletes.

**Paths are absolute and normalised before comparison.** The same project
reached as `./job.deckle` and as an absolute path is one entry, not two.
"""

from __future__ import annotations

import json
import os

import pytest

from deckle.core import recent


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    yield


def _touch(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    return str(path)


def test_nothing_recorded_yet_is_an_empty_list():
    assert recent.load() == []


def test_a_recorded_project_comes_back(tmp_path):
    path = _touch(tmp_path, "a.deckle")

    recent.record(path)

    assert recent.load() == [path]


def test_the_most_recent_is_first(tmp_path):
    first = _touch(tmp_path, "a.deckle")
    second = _touch(tmp_path, "b.deckle")

    recent.record(first)
    recent.record(second)

    assert recent.load() == [second, first]


def test_reopening_a_project_moves_it_up_rather_than_duplicating(tmp_path):
    first = _touch(tmp_path, "a.deckle")
    second = _touch(tmp_path, "b.deckle")

    recent.record(first)
    recent.record(second)
    recent.record(first)

    assert recent.load() == [first, second]


def test_the_same_file_by_a_different_route_is_one_entry(tmp_path, monkeypatch):
    path = _touch(tmp_path, "a.deckle")
    monkeypatch.chdir(tmp_path)

    recent.record(path)
    recent.record("a.deckle")
    recent.record(os.path.join(".", "a.deckle"))

    assert recent.load() == [path]


def test_the_list_is_bounded(tmp_path):
    for i in range(recent.MAX_ENTRIES + 5):
        recent.record(_touch(tmp_path, f"p{i}.deckle"))

    assert len(recent.load()) == recent.MAX_ENTRIES


def test_a_file_that_has_gone_is_hidden_but_not_forgotten(tmp_path):
    """An unplugged drive is temporarily unreachable, not retired."""
    kept = _touch(tmp_path, "kept.deckle")
    vanished = _touch(tmp_path, "gone.deckle")
    recent.record(kept)
    recent.record(vanished)
    os.remove(vanished)

    assert recent.existing() == [kept]
    assert vanished in recent.load(), "the entry was erased, not just hidden"


def test_the_last_directory_is_where_the_dialog_should_open(tmp_path):
    path = _touch(tmp_path, "a.deckle")
    recent.record(path)

    assert recent.last_directory() == str(tmp_path)


def test_the_last_directory_is_empty_when_there_is_no_history():
    assert recent.last_directory() == ""


def test_the_last_directory_skips_a_folder_that_has_gone(tmp_path):
    reachable = _touch(tmp_path, "a.deckle")
    gone = tmp_path / "sub"
    gone.mkdir()
    gone_file = gone / "b.deckle"
    gone_file.write_text("{}", encoding="utf-8")
    recent.record(reachable)
    recent.record(str(gone_file))
    os.remove(gone_file)
    gone.rmdir()

    assert recent.last_directory() == str(tmp_path)


def test_a_corrupt_store_reads_as_empty_rather_than_raising(tmp_path):
    recent.record(_touch(tmp_path, "a.deckle"))
    recent._store_path().write_text("{ not json", encoding="utf-8")

    assert recent.load() == []


def test_removing_an_entry_is_explicit(tmp_path):
    path = _touch(tmp_path, "a.deckle")
    recent.record(path)

    recent.forget(path)

    assert recent.load() == []


def test_recording_never_raises_when_the_store_cannot_be_written(monkeypatch, tmp_path):
    """Bookkeeping must not be the thing that breaks opening a project."""
    path = _touch(tmp_path, "a.deckle")
    monkeypatch.setattr(
        "deckle.core.recent._write", lambda entries: (_ for _ in ()).throw(OSError("nope"))
    )

    recent.record(path)  # must not raise
