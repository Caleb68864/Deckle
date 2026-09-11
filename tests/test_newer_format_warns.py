"""A file from a newer Deckle is warned about, never refused.

``.deckle``'s ``FORMAT_VERSION`` and ``PrinterProfile.version`` were both
written from the beginning and acted on by nothing, so a file from a newer
build was parsed hopefully and silently. The standing Watch in
``docs/decisions.md`` recorded the honest wiring as *a warning, not a
refusal*, and left it undecided. Decided 2026-09-11.

**Why a warning and not a refusal**, since the refusal is the reflex:

- Refusing strands somebody who opened a project on a newer build and
  came back to an older one. This program's whole posture is that a
  person standing at a printer always has a way forward.
- Both readers are *already* tolerant in both directions. ``.deckle``
  drops layout keys it does not know and defaults the ones it lacks;
  ``PrinterProfile.load`` does the same. So the file genuinely does open,
  correctly, in every case anyone has produced. What was missing was
  saying so.
- A profile is the most expensive data Deckle holds -- somebody printed a
  target and measured it with a ruler. Refusing to read one over a version
  integer sends them back to the printer to recover a measurement that is
  sitting in a file this build can nearly read in full.

**And the warning has to reach a person.** This project has just been
through a sweep whose worst finding after the reload prompt was the
interface silently dropping every import advisory: computed, carried
across a signal, and shown nowhere. A warning that reaches only a log is
that defect again, so these tests check the surfaces, not just the
``warnings`` call.
"""

from __future__ import annotations

import json
import warnings
from types import SimpleNamespace

import pytest

from deckle.core.models import LayoutSettings, Project
from deckle.core.profiles import (
    BUILTIN_PRESETS,
    NewerProfileAdvisory,
    PROFILE_VERSION,
    PrinterProfile,
)
from deckle.core.project_io import (
    FORMAT_VERSION,
    NewerFormatAdvisory,
    load_project,
    save_project,
)


def _layout() -> LayoutSettings:
    return LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge="left")


def _write_project(path, version) -> None:
    save_project(Project(pages=[], layout=_layout(), printer=None), str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if version is None:
        data.pop("version", None)
    else:
        data["version"] = version
    path.write_text(json.dumps(data), encoding="utf-8")


# -- .deckle -----------------------------------------------------------


def test_a_newer_project_opens_and_says_so(tmp_path):
    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION + 1)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        project = load_project(str(path), check_sources=False)

    assert project is not None, "it must open"
    advisories = [w for w in caught if issubclass(w.category, NewerFormatAdvisory)]
    assert len(advisories) == 1
    message = str(advisories[0].message)
    assert "newer version of Deckle" in message
    assert "book.deckle" in message


def test_the_message_does_not_say_it_failed(tmp_path):
    """It is read *after* the project is on screen. "Cannot open" would be
    a sentence contradicted by the window the user is looking at."""
    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION + 5)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_project(str(path), check_sources=False)

    message = str(caught[0].message).lower()
    assert "cannot open" not in message
    assert "has been opened" in message
    # And it names what to do, like every other message in this program.
    assert "check the layout" in message


def test_this_builds_own_file_says_nothing(tmp_path):
    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_project(str(path), check_sources=False)

    assert not [w for w in caught if issubclass(w.category, NewerFormatAdvisory)]


def test_an_older_file_says_nothing(tmp_path):
    """This format has defaulted missing fields from the start, so an older
    file is not a surprise and not worth a sentence."""
    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION - 1)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_project(str(path), check_sources=False)

    assert not [w for w in caught if issubclass(w.category, NewerFormatAdvisory)]


@pytest.mark.parametrize("version", [None, "2", 1.5, True, [], {}])
def test_a_version_that_is_not_an_integer_is_left_alone(tmp_path, version):
    """Every other shape check in that module answers "is this a Deckle
    project", and a missing or odd version has never stopped it being one.
    Warning here would fire on files this build itself once wrote."""
    path = tmp_path / "book.deckle"
    _write_project(path, version)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        project = load_project(str(path), check_sources=False)

    assert project is not None
    assert not [w for w in caught if issubclass(w.category, NewerFormatAdvisory)]


# -- PrinterProfile ----------------------------------------------------


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _save_profile(name, version):
    profile = BUILTIN_PRESETS["generic_face_down_reversed"]
    profile.save(name)
    from deckle.core.profiles import _profile_path

    path = _profile_path(name)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_newer_calibration_loads_and_says_so(profile_dir):
    _save_profile("Brother", PROFILE_VERSION + 1)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = PrinterProfile.load("Brother")

    assert loaded is not None, "a measured calibration must not be refused"
    advisories = [w for w in caught if issubclass(w.category, NewerProfileAdvisory)]
    assert len(advisories) == 1
    assert "Brother" in str(advisories[0].message)


def test_the_calibration_message_says_it_loaded(profile_dir):
    _save_profile("Brother", PROFILE_VERSION + 2)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        PrinterProfile.load("Brother")

    message = str(caught[0].message).lower()
    assert "has been loaded" in message
    assert "check the reload instruction" in message


@pytest.mark.parametrize("version", [None, "2", 1.5, True])
def test_a_version_that_is_not_an_integer_raises_no_advisory(version):
    """The guard itself, so the rule is pinned without entangling it with
    what ``load`` does afterwards.

    A missing or odd ``version`` must not warn: every other shape check in
    that module answers "is this a profile", and a version this build
    cannot compare has never been what makes one.

    Checked on ``_warn_if_newer_profile`` directly, deliberately: it pins
    the rule without tying it to what ``load`` does afterwards. The
    through-the-load half is
    ``test_a_calibration_with_no_version_loads_and_says_nothing`` below.
    """
    from deckle.core.profiles import _warn_if_newer_profile

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_if_newer_profile("Brother", version)

    assert not [w for w in caught if issubclass(w.category, NewerProfileAdvisory)]


# -- the other half: a calibration with no version at all --------------
#
# Decided 2026-09-11, a day after the newer-file rule above and by the same
# reasoning carried one step further. `version` was a required dataclass
# field with no default, so `cls(**kwargs)` raised `TypeError` and a
# profile that omitted it did not open at all -- the exact failure `load`'s
# own docstring says it exists to prevent, and worse than the newer-file
# case it sits beside, because there is no way forward from it at all.
#
# The GUIDE tells people a hand-edited profile survives, and
# `calibration-sheet` now exists, so hand-written profiles are about to be
# the ordinary case rather than a hypothetical one.


def _write_profile_without(name, *fields, extra=None):
    """A saved profile with ``fields`` removed from the JSON.

    Written through ``save`` first so the payload is exactly what Deckle
    writes, then edited -- a hand-built dict would let this test decide the
    shape it is checking.
    """
    BUILTIN_PRESETS["generic_face_down_reversed"].save(name)
    from deckle.core.profiles import _profile_path

    path = _profile_path(name)
    data = json.loads(path.read_text(encoding="utf-8"))
    for field in fields:
        data.pop(field, None)
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_calibration_with_no_version_loads_and_says_nothing(profile_dir):
    """The measurement survives a missing bookkeeping integer.

    Before 2026-09-11 this raised
    ``TypeError: PrinterProfile.__init__() missing 1 required positional
    argument: 'version'``.
    """
    _write_profile_without(
        "Brother", "version", extra={"flip_axis": "short", "back_offset_x_pt": 3.5}
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = PrinterProfile.load("Brother")

    assert loaded.version == PROFILE_VERSION, "read as this build's shape"
    # The measured values are the point. A load that returned a preset
    # would satisfy the line above and lose the calibration.
    assert loaded.flip_axis == "short"
    assert loaded.back_offset_x_pt == 3.5
    assert not [w for w in caught if issubclass(w.category, NewerProfileAdvisory)], (
        "a versionless file is not a file from the future"
    )


def test_the_print_dialog_can_be_opened_with_a_versionless_calibration(profile_dir):
    """The surface that made this worse than the CLI.

    ``resolve_profile`` catches ``(FileNotFoundError, OSError, ValueError)``
    and runs inside ``PrintDialog.__init__``. ``TypeError`` is not in that
    tuple, so one hand-edited file did not degrade a printer to
    uncalibrated -- it made the Print dialog impossible to open. The CLI's
    four ``except`` clauses all list ``TypeError`` and reported it.
    """
    from deckle.app.views.print_dialog import resolve_profile

    _write_profile_without("Brother", "version", extra={"flip_axis": "short"})

    resolved = resolve_profile("Brother")

    assert resolved.flip_axis == "short", "the saved calibration, not a preset"


@pytest.mark.parametrize(
    "field",
    ["flip_axis", "output_face", "feed_edge", "reverse_stack", "imageable_area_pt"],
)
def test_a_calibration_missing_a_measurement_is_still_refused(profile_dir, field):
    """The control on the rule above: bookkeeping is supplied, a
    measurement never is.

    ``deckle-cli profile set`` already refuses to create a profile without
    ``--from`` -- *"a printer profile has no partial form"* -- and this is
    the same refusal on the reading side. A value this reader invented
    would plan a back pass against a printer nobody measured.
    """
    path = _write_profile_without("Brother", field)

    with pytest.raises(ValueError) as raised:
        PrinterProfile.load("Brother")

    message = str(raised.value)
    assert repr(field) in message, "it must name the field"
    assert str(path) in message, "and the file, so it can be opened"


def test_a_missing_measurement_reaches_the_print_dialog_as_a_fallback(profile_dir):
    """Refused, but not as a ``TypeError`` out of ``PrintDialog.__init__``.

    This is the half the fix to ``version`` alone would not have covered:
    the refusal has to be a ``ValueError`` for the dialog's existing
    ``except`` to see it.
    """
    from deckle.app.views.print_dialog import resolve_profile

    _write_profile_without("Brother", "flip_axis")

    resolved = resolve_profile("Brother")

    assert resolved is not None, "the dialog still opens"


def test_calibrated_at_is_not_supplied_either(profile_dir):
    """Absent, there is no calibration run to claim.

    ``calibrated_at`` and ``calibration_version`` describe a run that
    measured this printer. Defaulting them to the built-ins' ``""``/``0``
    would be a reader asserting a run did not happen, which is a different
    claim from the file not saying. They stay required, and the refusal
    names them.
    """
    _write_profile_without("Brother", "calibrated_at", "calibration_version")

    with pytest.raises(ValueError) as raised:
        PrinterProfile.load("Brother")

    message = str(raised.value)
    assert "'calibrated_at'" in message
    assert "'calibration_version'" in message


def test_constructing_a_profile_in_code_still_names_its_version():
    """The default lives in the reader, not on the dataclass.

    A caller building a profile in code knows which shape it is building;
    only a caller reading one does not. Putting a default on the field
    would have let a future constructor call silently record the wrong
    version -- and would have forced a default onto every field after it.
    """
    with pytest.raises(TypeError):
        PrinterProfile(
            flip_axis="long",
            output_face="down",
            feed_edge="top",
            reverse_stack=True,
            imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
            calibrated_at="",
            calibration_version=0,
        )


def test_this_builds_own_calibration_says_nothing(profile_dir):
    _save_profile("Brother", PROFILE_VERSION)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = PrinterProfile.load("Brother")

    assert loaded.version == PROFILE_VERSION
    assert not [w for w in caught if issubclass(w.category, NewerProfileAdvisory)]


# -- the surfaces ------------------------------------------------------


def test_the_desktop_app_shows_the_project_advisory_rather_than_the_page_count(
    tmp_path, monkeypatch
):
    """The status bar, which is where a refused import and an import
    advisory already land. A warning that reaches only a log is the defect
    the W1 sweep found in the import path."""
    from deckle.app import project_actions

    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION + 1)

    shown: list[str] = []
    view = SimpleNamespace(state=None, refresh=lambda: None,
                           refresh_from_project=lambda: None)
    window = SimpleNamespace(
        status_bar=SimpleNamespace(showMessage=shown.append),
        state=SimpleNamespace(mark_unsaved=lambda: None),
        _recover_autosave_if_offered=lambda p, project: project,
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
        SimpleNamespace(mark_unsaved=lambda: None)
    ))

    assert project_actions.open_project(window, str(path)) is True

    assert shown, "nothing reached the status bar at all"
    assert "newer version of Deckle" in shown[-1], (
        f"the advisory did not reach the user: {shown[-1]!r}"
    )


def test_the_cli_prints_the_project_advisory_on_stderr(tmp_path, capsys, monkeypatch):
    """stderr, not stdout: ``--json`` promises stdout carries the JSON
    document and nothing else, and a note that broke that would break
    every script parsing it."""
    from deckle.cli import commands

    path = tmp_path / "book.deckle"
    _write_project(path, FORMAT_VERSION + 1)
    monkeypatch.chdir(tmp_path)

    project = commands._load_project_or_report(str(path))

    assert project is not None
    captured = capsys.readouterr()
    assert "newer version of Deckle" in captured.err
    assert "newer version of Deckle" not in captured.out


def test_the_cli_prints_the_calibration_advisory_on_stderr(profile_dir, capsys):
    from deckle.cli import report

    _save_profile("Brother", PROFILE_VERSION + 1)

    resolved = report._resolve_profile_origin("Brother")

    assert resolved is not None and resolved[1] == "saved"
    assert "newer version of Deckle" in capsys.readouterr().err
