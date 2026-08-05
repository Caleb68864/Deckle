"""Tests for deckle.core.project_io -- ``.deckle`` project persistence."""

from __future__ import annotations

import json
import os

import pytest

from deckle.core import session_log
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.profiles import PrinterProfile
from deckle.core.project_io import (
    SourceChangedWarning,
    SourceMissingError,
    load_project,
    save_project,
)


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


def test_load_raises_source_missing_error_for_deleted_file(tmp_path):
    """A-4: a moved/deleted source is a different failure from a changed
    one -- it must raise SourceMissingError, not SourceChangedWarning."""
    content = b"original content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    os.remove(source_path)

    with pytest.raises(SourceMissingError) as exc_info:
        load_project(out_path)
    assert exc_info.value.expected_path == source_path
    assert not isinstance(exc_info.value, SourceChangedWarning)


def test_source_missing_error_relocate_records_new_path(tmp_path):
    """The relocate affordance lets a caller (CLI/UI) record where the
    missing source was actually found, for a subsequent load/save cycle."""
    content = b"original content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)
    os.remove(source_path)

    with pytest.raises(SourceMissingError) as exc_info:
        load_project(out_path)

    relocated = os.path.join(str(tmp_path), "relocated.pdf")
    result = exc_info.value.relocate(relocated)
    assert result == relocated
    assert exc_info.value.relocated_path == relocated


def test_load_still_raises_source_changed_warning_when_file_exists(tmp_path):
    """Regression guard: a file that exists but hashes differently must
    still raise SourceChangedWarning, never SourceMissingError, now that
    a missing-file branch exists alongside it."""
    content = b"original content"
    source_path = _make_source_file(tmp_path, content=content)
    project = _make_project(source_path, content)

    out_path = os.path.join(str(tmp_path), "proj.deckle")
    save_project(project, out_path)

    with open(source_path, "wb") as f:
        f.write(b"different content now")

    with pytest.raises(SourceChangedWarning):
        load_project(out_path)


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


def _make_profile() -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-08-04T00:00:00",
        calibration_version=1,
    )


def test_session_log_rotates_and_retains_bounded_generations(tmp_path, monkeypatch):
    """A-5: the session log is capped and rotated rather than growing
    forever. Shrink the rotation threshold so the test doesn't have to
    write 5 MB of records to exercise it."""
    monkeypatch.setenv("DECKLE_SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(session_log, "_MAX_BYTES", 500)
    monkeypatch.setattr(session_log, "_BACKUP_COUNT", 3)

    profile = _make_profile()
    for i in range(200):
        session_log.log_print_job(
            printer="my_printer",
            profile=profile,
            sheets=[i, i + 1, i + 2],
            dpi=300,
            pass_index=i % 2,
        )

    base_path = session_log.session_log_path()
    assert base_path.exists()
    assert base_path.stat().st_size <= 500

    # At most _BACKUP_COUNT rolled-over generations are retained --
    # session_log.jsonl.1 .. .3 -- never an unbounded number of files.
    rotated = sorted(tmp_path.glob("session_log.jsonl.*"))
    assert 1 <= len(rotated) <= 3
    for rotated_file in rotated:
        assert rotated_file.stat().st_size <= 500 + 512  # allow one record's slack

    no_generation_4 = tmp_path / "session_log.jsonl.4"
    assert not no_generation_4.exists()


# --- Red-team A-3: .deckle files carry filesystem paths and may be shared ---


def test_load_project_warns_when_source_path_is_outside_allowed_roots(tmp_path):
    """A source outside the project dir is surfaced, never opened silently.

    Non-fatal by design: Deckle's normal case is sources living in Downloads
    or a sync folder, so refusing to load them would break the primary
    workflow rather than protect it.
    """
    from deckle.core.project_io import PathOutsideRootsAdvisory

    outside = tmp_path / "elsewhere" / "book.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF-1.4\n")

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj_path = proj_dir / "p.deckle"

    project = _make_project(str(outside), b"%PDF-1.4")
    save_project(project, str(proj_path))

    with pytest.warns(PathOutsideRootsAdvisory):
        load_project(str(proj_path), check_sources=False)


def test_load_project_callback_can_veto_an_outside_root_path(tmp_path):
    """A UI that wants a real confirmation prompt gets a veto."""
    from deckle.core.project_io import PathOutsideRootsWarning

    outside = tmp_path / "elsewhere" / "book.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF-1.4\n")

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj_path = proj_dir / "p.deckle"

    project = _make_project(str(outside), b"%PDF-1.4")
    save_project(project, str(proj_path))

    with pytest.raises(PathOutsideRootsWarning):
        load_project(
            str(proj_path),
            check_sources=False,
            on_outside_roots=lambda path, roots: False,
        )

    # Approving proceeds without raising.
    load_project(
        str(proj_path),
        check_sources=False,
        on_outside_roots=lambda path, roots: True,
    )


def test_traversal_cannot_escape_via_dotdot(tmp_path):
    """`..` segments are resolved before the containment check."""
    from deckle.core.project_io import PathOutsideRootsAdvisory

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4\n")

    sneaky = str(proj_dir / ".." / "secret.pdf")
    project = _make_project(sneaky, b"%PDF-1.4")
    proj_path = proj_dir / "p.deckle"
    save_project(project, str(proj_path))

    with pytest.warns(PathOutsideRootsAdvisory):
        load_project(str(proj_path), check_sources=False)


# --- Red-team (signatures v2): .deckle must survive LayoutSettings drift ---


def test_legacy_deckle_with_removed_field_still_loads(tmp_path):
    """Regression: deleting `scale_mode` made every project file saved before
    the removal unopenable.

    `_layout_from_dict` passed every stored key straight into
    `LayoutSettings(**kwargs)`, so a retired field raised
    `TypeError: unexpected keyword argument 'scale_mode'`. Any field ever
    added or removed permanently broke files written on the other side of
    that change -- and signatures v2 adds five more.
    """
    from deckle.core.project_io import UnknownLayoutFieldsWarning, _layout_from_dict

    legacy = {
        "paper": [612.0, 792.0],
        "gutter_pt": 18.0,
        "binding_edge": "left",
        "scale_mode": "fit",  # retired
    }
    with pytest.warns(UnknownLayoutFieldsWarning):
        settings = _layout_from_dict(legacy)
    assert settings.gutter_pt == 18.0
    assert settings.paper == (612.0, 792.0)


def test_future_deckle_with_unknown_field_still_loads():
    """Forward direction: a file written by a build that knows some field
    this build has never heard of must open here, dropping what this build
    cannot use.

    Uses a placeholder field name rather than a real one: ``fold_scheme``
    and ``sheets_per_signature`` are now known ``LayoutSettings`` fields
    (signatures v2, SS-09/SS-10), so they no longer exercise this path --
    the genuinely-unknown-field case still needs a name this build's
    ``LayoutSettings`` has never defined.
    """
    from deckle.core.project_io import UnknownLayoutFieldsWarning, _layout_from_dict

    future = {
        "paper": [612.0, 792.0],
        "gutter_pt": 18.0,
        "binding_edge": "left",
        "some_field_a_future_build_added": "placeholder",
    }
    with pytest.warns(UnknownLayoutFieldsWarning):
        settings = _layout_from_dict(future)
    assert settings.binding_edge == "left"


def test_minimal_layout_dict_warns_about_nothing():
    """Only genuinely unknown keys warn -- missing ones just take defaults."""
    import warnings as _w

    from deckle.core.project_io import UnknownLayoutFieldsWarning, _layout_from_dict

    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        _layout_from_dict({"paper": [612.0, 792.0], "gutter_pt": 18.0, "binding_edge": "left"})
    assert not [c for c in caught if issubclass(c.category, UnknownLayoutFieldsWarning)]


def test_round_trip_still_preserves_every_known_field(tmp_path):
    from deckle.core.project_io import _layout_from_dict, _layout_to_dict

    original = LayoutSettings(
        paper=(612.0, 792.0), gutter_pt=54.0, binding_edge="right",
        margin_top_pt=18.0, margin_bottom_pt=9.0, margin_outer_pt=27.0,
        slack_to="outer", margins_linked=False, start_on_recto=False,
    )
    assert _layout_from_dict(_layout_to_dict(original)) == original
