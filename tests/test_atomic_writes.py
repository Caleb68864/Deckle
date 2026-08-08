"""A save that fails partway leaves the previous file intact.

``save_project`` opened the target with ``"w"``, which truncates before a
single byte is written. Everything after that point -- a full disk, a
disconnected share, a killed process -- leaves the file destroyed and
replaced with a prefix of the new document, which is not valid JSON and
will not open.

That is bad for an explicit save and worse for autosave, because autosave
runs unattended on a daemon timer thread and its whole stated purpose is
that "a killed process loses at most the last half-second of edits". A
process killed *during* the write lost the entire project instead: the
recovery file was the thing that got truncated. The failure mode the
feature exists to survive was the one that destroyed it.

Asserted on ``save_project`` rather than on the helper underneath it,
because the defect was in which write call the saver used, and a test of
the helper would have passed against the broken saver.
"""

from __future__ import annotations

import json
import os

import pytest

from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.paths import write_text_atomic
from deckle.core.project_io import load_project, save_project


def _project(marker: float) -> Project:
    """A project whose gutter carries ``marker``, so which of two saved
    generations is on disk can be read back off it."""
    pages = [
        SourcePage(
            ref=SourceRef(path="s.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(4)
    ]
    layout = LayoutSettings(paper=(792.0, 612.0), gutter_pt=marker,
                            binding_edge="left")
    return Project(pages=pages, layout=layout, printer=None)


def _load(path: str) -> Project:
    with pytest.warns(Warning):
        return load_project(path, check_sources=False)


def test_a_failed_save_leaves_the_previous_project_readable(tmp_path, monkeypatch):
    path = os.path.join(str(tmp_path), "job.deckle")
    save_project(_project(36.0), path)

    def explode(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(json, "dump", explode)
    with pytest.raises(OSError):
        save_project(_project(72.0), path)

    assert _load(path).layout.gutter_pt == 36.0


def test_a_failed_save_leaves_no_debris_beside_the_project(tmp_path, monkeypatch):
    """A temp file abandoned next to the project would accumulate one per
    failure, in the user's own folder, named nothing they recognise."""
    path = os.path.join(str(tmp_path), "job.deckle")
    save_project(_project(36.0), path)

    def explode(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(json, "dump", explode)
    with pytest.raises(OSError):
        save_project(_project(72.0), path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["job.deckle"]


def test_the_project_file_is_never_observed_half_written(tmp_path):
    """The property that matters to a reader racing the writer: the target
    path holds the old document or the new one, never a prefix of either.

    Checked from inside the serialiser, at the moment the new bytes exist
    but the save has not returned -- exactly when a truncating writer has
    an invalid file on disk.
    """
    path = os.path.join(str(tmp_path), "job.deckle")
    save_project(_project(36.0), path)

    seen: list[float] = []
    real_dump = json.dump

    def watching_dump(payload, fp, **kwargs):
        real_dump(payload, fp, **kwargs)
        with open(path, "r", encoding="utf-8") as f:
            seen.append(json.load(f)["layout"]["gutter_pt"])

    original = json.dump
    json.dump = watching_dump
    try:
        save_project(_project(72.0), path)
    finally:
        json.dump = original

    assert seen == [36.0]
    assert _load(path).layout.gutter_pt == 72.0


def test_a_save_over_a_missing_file_still_creates_it(tmp_path):
    """The ordinary first save: there is no previous generation to keep."""
    path = os.path.join(str(tmp_path), "new.deckle")

    save_project(_project(18.0), path)

    assert _load(path).layout.gutter_pt == 18.0


def test_the_helper_replaces_content_rather_than_appending(tmp_path):
    path = tmp_path / "f.json"
    write_text_atomic(path, "first")

    write_text_atomic(path, "second")

    assert path.read_text(encoding="utf-8") == "second"


def test_the_helper_reports_an_unwritable_directory_rather_than_passing(tmp_path):
    """A missing parent directory is the caller's problem to name, and it
    has to surface as ``OSError`` -- ``save_project`` documents that."""
    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "nope" / "f.json", "x")


def test_the_helper_leaves_no_debris_when_the_rename_fails(tmp_path, monkeypatch):
    """The last step is the one with nothing after it to clean up, so it is
    the step most likely to strand a temp file."""
    path = tmp_path / "f.json"
    write_text_atomic(path, "first")

    def explode(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        write_text_atomic(path, "second")

    assert path.read_text(encoding="utf-8") == "first"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.json"]
