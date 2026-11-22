"""F12 §B: a printer profile can hold a second border, for landscape sheets.

``imageable_area_pt`` is one border for every sheet. Folio feeds landscape,
and a printer usually leaves a different strip on the edge that feeds
first, so the one number is wrong for exactly the scheme that most needs
it. Decided by agent 2026-11-21 (owner may revisit): an optional
``landscape_imageable_area_pt``, ``None`` by default, and while it is
``None`` nothing about a profile changes -- not its file, not its resume
digest, not what any consumer reads.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from deckle.core.models import (
    LayoutSettings, Project, SourcePage, SourceRef,
)
from deckle.core.planning import impose_project
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile

PRESET = BUILTIN_PRESETS["generic_face_down_reversed"]
MEASURED = dataclasses.replace(
    PRESET, calibration_version=1, calibrated_at="2026-11-21T00:00:00",
    imageable_area_pt=(10.0, 11.0, 12.0, 13.0),
)
LANDSCAPE_AREA = (20.0, 30.0, 40.0, 50.0)
WITH_LANDSCAPE = dataclasses.replace(MEASURED, landscape_imageable_area_pt=LANDSCAPE_AREA)

LETTER = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


# -- the selector --------------------------------------------------------


def test_a_portrait_sheet_uses_the_one_border():
    assert WITH_LANDSCAPE.imageable_area_for(LETTER) == MEASURED.imageable_area_pt


def test_a_landscape_sheet_uses_the_landscape_border_when_it_was_measured():
    assert WITH_LANDSCAPE.imageable_area_for(LETTER_LANDSCAPE) == LANDSCAPE_AREA


def test_a_landscape_sheet_falls_back_when_it_was_never_measured():
    """Every profile written before the field existed."""
    assert MEASURED.imageable_area_for(LETTER_LANDSCAPE) == MEASURED.imageable_area_pt


def test_a_square_sheet_counts_as_portrait():
    assert WITH_LANDSCAPE.imageable_area_for((600.0, 600.0)) == MEASURED.imageable_area_pt


# -- storage: nothing changes for a profile that does not use it ---------


def test_an_unmeasured_landscape_border_is_not_written(config_home):
    MEASURED.save("Office")
    stored = json.loads(next(config_home.rglob("*.json")).read_text())

    assert "landscape_imageable_area_pt" not in stored
    assert PrinterProfile.load("Office") == MEASURED


def test_a_measured_landscape_border_survives_a_round_trip(config_home):
    WITH_LANDSCAPE.save("Office")

    loaded = PrinterProfile.load("Office")

    assert loaded == WITH_LANDSCAPE
    assert loaded.landscape_imageable_area_pt == LANDSCAPE_AREA  # a tuple, not a list


def test_a_resume_digest_is_unchanged_by_the_new_field():
    """A field that changed every profile's digest would make every
    interrupted run refuse to resume after the upgrade."""
    import hashlib

    from deckle.core.print_session import _hash_profile

    before = dataclasses.asdict(MEASURED)
    before.pop("landscape_imageable_area_pt")
    expected = hashlib.sha256(
        json.dumps(before, sort_keys=True, default=list).encode("utf-8")
    ).hexdigest()[:16]

    assert _hash_profile(MEASURED) == expected
    assert _hash_profile(WITH_LANDSCAPE) != expected


# -- the preview ---------------------------------------------------------


def _plan(paper):
    pages = [
        SourcePage(SourceRef("/x.pdf", i, "a" * 64, 396.0, 612.0), 0, False)
        for i in range(8)
    ]
    layout = LayoutSettings(paper=paper, gutter_pt=0.0, binding_edge="left",
                            fold_scheme="folio", sheets_per_signature=2)
    return impose_project(Project(pages, layout, None))


def _kinds(plan, profile):
    from deckle.app.views.preview_view import warnings_for_plan

    return {
        index: [w.kind for w in warnings]
        for index, warnings in warnings_for_plan(plan, profile).items()
    }


def test_a_measured_printer_on_a_landscape_job_is_told_the_border_is_unverified():
    pytest.importorskip("PySide6")
    plan = _plan(LETTER_LANDSCAPE)

    kinds = _kinds(plan, MEASURED)

    assert all("landscape_imageable_unverified" in kinds[s.index] for s in plan.sheets)


@pytest.mark.parametrize("profile,paper", [
    pytest.param(WITH_LANDSCAPE, LETTER_LANDSCAPE, id="landscape-measured"),
    pytest.param(PRESET, LETTER_LANDSCAPE, id="uncalibrated-preset"),
    pytest.param(MEASURED, LETTER, id="portrait-job"),
])
def test_the_warning_is_silent_otherwise(profile, paper):
    pytest.importorskip("PySide6")
    plan = _plan(paper)

    kinds = _kinds(plan, profile)

    assert not any("landscape_imageable_unverified" in k for k in kinds.values())


def test_clipping_is_judged_against_the_landscape_border():
    """Content 25pt in from every edge: inside the one border (10-13pt),
    outside the landscape one (20-50pt), so only the landscape profile may
    call it clipped."""
    pytest.importorskip("PySide6")
    from deckle.app.views.preview_view import imageable_rect_pt

    assert imageable_rect_pt(LETTER_LANDSCAPE, WITH_LANDSCAPE.imageable_area_for(LETTER_LANDSCAPE)) \
        == (20.0, 50.0, 792.0 - 40.0, 612.0 - 30.0)


def test_use_printer_margins_takes_the_landscape_border_on_landscape_paper():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel

    QApplication.instance() or QApplication([])
    plan_project = Project(
        [SourcePage(SourceRef("/x.pdf", 0, "a" * 64, 396.0, 612.0), 0, False)],
        LayoutSettings(paper=LETTER_LANDSCAPE, gutter_pt=0.0, binding_edge="left"),
        None,
    )
    panel = LayoutPanel(AppState(plan_project))
    panel.profile = WITH_LANDSCAPE

    panel._on_use_printer_margins()

    assert panel.state.project.layout.margin_top_pt == max(LANDSCAPE_AREA)


# -- the profile editor ----------------------------------------------------


def _editor(start_from):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from deckle.app.views.profile_editor import ProfileEditorDialog

    QApplication.instance() or QApplication([])
    saved = []
    editor = ProfileEditorDialog(
        "Brother", start_from=start_from,
        saver=lambda profile, name: saved.append(profile), now=lambda: "NOW",
    )
    editor.saved = saved
    return editor


def test_the_editor_starts_with_the_landscape_border_off_and_its_boxes_dead():
    editor = _editor(MEASURED)

    assert not editor.landscape_check.isChecked()
    assert not any(spin.isEnabled() for spin in editor.landscape_spins.values())
    assert editor.save()
    assert editor.saved[0].landscape_imageable_area_pt is None


def test_the_editor_saves_a_landscape_border_and_keeps_the_calibration():
    editor = _editor(MEASURED)
    editor.landscape_check.setChecked(True)
    for edge, value in zip(("left", "top", "right", "bottom"), LANDSCAPE_AREA):
        editor.landscape_spins[edge].setValue(value)

    assert editor.save()

    profile = editor.saved[0]
    assert profile.landscape_imageable_area_pt == LANDSCAPE_AREA
    assert profile.calibration_version == 1  # a border is typed, not measured by the wizard


def test_the_editor_shows_a_stored_landscape_border():
    editor = _editor(WITH_LANDSCAPE)

    assert editor.landscape_check.isChecked()
    assert tuple(editor.landscape_spins[e].value() for e in ("left", "top", "right", "bottom")) \
        == LANDSCAPE_AREA


# -- the CLI -------------------------------------------------------------


def test_profile_set_takes_and_clears_a_landscape_border(config_home, capsys):
    from deckle.cli import main

    assert main(["profile", "set", "Office", "--from", "generic_face_down_reversed"]) == 0
    assert main(["profile", "set", "Office", "--landscape-imageable-area", "20,30,40,50",
                 "--force"]) == 0
    assert PrinterProfile.load("Office").landscape_imageable_area_pt == LANDSCAPE_AREA

    capsys.readouterr()
    assert main(["profile", "show", "Office"]) == 0
    assert "landscape imageable area (margins, pt): left 20, top 30, right 40, bottom 50" \
        in capsys.readouterr().out

    assert main(["profile", "set", "Office", "--landscape-imageable-area", "none",
                 "--force"]) == 0
    assert PrinterProfile.load("Office").landscape_imageable_area_pt is None


def test_the_json_report_carries_it():
    from deckle.core.report import profile_fields

    assert profile_fields(WITH_LANDSCAPE)["landscape_imageable_area_pt"] == list(LANDSCAPE_AREA)
    assert profile_fields(MEASURED)["landscape_imageable_area_pt"] is None
