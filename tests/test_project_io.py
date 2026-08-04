"""Tests for deckle.core.project_io -- ``.deckle`` project persistence."""

from __future__ import annotations

import json
import os

import pytest

from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import SourceChangedWarning, load_project, save_project


def _make_source_file(tmp_path, name: str = "src.pdf", content: bytes = b"hello world") -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _make_project(source_path: str, content: bytes) -> Project:
    sha = _sha256(content)
    pages = [
        SourcePage(
            ref=SourceRef(path=source_path, page_index=2, sha256=sha, width_pt=612.0, height_pt=792.0),
            rotate_deg=90,
            skipped=False,
        ),
        SourcePage(
            ref=SourceRef(path=source_path, page_index=0, sha256=sha, width_pt=612.0, height_pt=792.0),
            rotate_deg=0,
            skipped=True,
        ),
        SourcePage(
            ref=SourceRef(path=source_path, page_index=1, sha256=sha, width_pt=612.0, height_pt=792.0),
            rotate_deg=180,
            skipped=False,
        ),
    ]
    layout = LayoutSettings(
        paper=(612.0, 792.0),
        gutter_pt=36.0,
        binding_edge="left",
        scale_mode="fixed_gutter",
        start_on_recto=True,
        landscape_policy="rotate",
    )
    return Project(pages=pages, layout=layout, printer="my_printer")


def test_round_trip_preserves_reorder_rotate_skip(tmp_path):
    content = b"hello world"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)
    loaded = load_project(out_path)

    assert [p.ref.page_index for p in loaded.pages] == [p.ref.page_index for p in project.pages]
    assert [p.rotate_deg for p in loaded.pages] == [p.rotate_deg for p in project.pages]
    assert [p.skipped for p in loaded.pages] == [p.skipped for p in project.pages]
    assert loaded.layout == project.layout
    assert loaded.printer == project.printer


def test_saved_file_has_top_level_version(tmp_path):
    content = b"hello world"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    with open(out_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1


def test_saved_file_never_embeds_page_content(tmp_path):
    content = b"hello world, this is definitely page content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    with open(out_path, "r", encoding="utf-8") as f:
        raw = f.read()
    assert "this is definitely page content" not in raw


def test_load_raises_source_changed_warning_on_hash_mismatch(tmp_path):
    content = b"original content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    # Mutate the source file after saving.
    with open(source_path, "wb") as f:
        f.write(b"different content now")

    with pytest.raises(SourceChangedWarning) as exc_info:
        load_project(out_path)
    assert exc_info.value.path == source_path


def test_load_does_not_silently_substitute_changed_content(tmp_path):
    content = b"original content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    with open(source_path, "wb") as f:
        f.write(b"different content now")

    with pytest.raises(SourceChangedWarning):
        loaded = load_project(out_path)
        # If no exception were raised, this would be the silent-substitution
        # failure mode this test guards against.
        assert loaded is None  # pragma: no cover - unreachable if raised correctly
