"""The two config stores get the same treatment the project file got.

``save_project`` was made atomic because a truncating write killed partway
destroys the previous generation. The printer profiles and the
recent-projects list are written the same way and had the same exposure --
and for profiles it is worse in two respects.

A calibration is the most expensive data Deckle holds. It is not derived
from anything; it comes from printing a target, measuring it by hand, and
reprinting when the numbers are wrong. Losing it means doing that again.

And ``PrinterProfile.load`` has no tolerance for a corrupt file the way
``recent.load`` does -- it lets ``JSONDecodeError`` out -- so a half-written
profile does not degrade to "uncalibrated", it makes that printer
unusable until the user finds and deletes a file in a directory they have
never opened.

This module also pins the *other* half of the lesson recorded for
``PrinterProfile.load`` on 2026-08-06. That entry cites the
``LayoutSettings`` field-drift bug and applies its unknown-key half, but
the tuple half was fixed only in the layout loader: ``load`` still named
``imageable_area_pt`` specifically, so the next tuple field added to a
profile would come back as a list, exactly as ``crop_odd_pt`` did.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from deckle.core import recent
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")


def _calibrated() -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-08-07T10:00:00",
        calibration_version=1,
        back_offset_x_pt=3.0,
        back_offset_y_pt=-1.5,
    )


# -- durability -------------------------------------------------------
#
# The fault injected below is a *torn write*: the file is opened for
# writing (which truncates it), some bytes land, and then the write dies.
# That is what a full disk, a dropped network share, or a killed process
# actually does, and it is the only fault model that tells a truncating
# writer apart from an atomic one. Injecting at the serialiser instead --
# the first thing tried here -- proved nothing: `json.dumps` runs before
# the file is opened, so every store survived and all seven tests passed
# against the unfixed code.
#
# Scoped to the one target path, so pytest's own capture and every other
# open in the process are untouched.


@pytest.fixture
def torn_write():
    """A context manager under which the next file written dies halfway.

    Armed around a single save call, so "the next file written" is
    unambiguously the one that save is writing -- which is what makes this
    independent of *which* file that is. A truncating writer is killed
    holding the store open; an atomic one is killed holding a temp file
    open, with the store untouched. Both raise; only one loses data.
    """
    import contextlib
    import io

    real_open = io.open

    @contextlib.contextmanager
    def armed():
        def guarded(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if "w" in mode:
                real_write = handle.write

                def half_then_die(data):
                    real_write(data[: len(data) // 2])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = half_then_die
            return handle

        io.open = guarded
        try:
            yield
        finally:
            io.open = real_open

    return armed


def test_a_failed_profile_write_keeps_the_previous_calibration(torn_write):
    _calibrated().save("ink")

    with torn_write(), pytest.raises(OSError):
        BUILTIN_PRESETS["generic_face_up_in_order"].save("ink")

    assert PrinterProfile.load("ink").back_offset_x_pt == 3.0


def test_a_failed_profile_write_leaves_the_profile_loadable(torn_write):
    """The distinction that matters: not merely "some file is there", but
    that the file still parses. ``PrinterProfile.load`` has no tolerance
    for a corrupt file the way ``recent.load`` does, so a truncated profile
    does not degrade to "uncalibrated" -- it raises, and that printer is
    unusable until the user finds and deletes a file in a directory they
    have never opened.
    """
    _calibrated().save("ink")

    with torn_write(), pytest.raises(OSError):
        _calibrated().save("ink")

    PrinterProfile.load("ink")


def test_a_failed_recent_write_keeps_the_previous_list(tmp_path, torn_write):
    kept = tmp_path / "a.deckle"
    kept.write_text("{}", encoding="utf-8")
    recent.record(str(kept))

    # `record` swallows OSError by contract -- it must never be what stops
    # a project opening -- so the assertion is on what survived, not on a
    # raise.
    with torn_write():
        recent.record(str(tmp_path / "b.deckle"))

    assert recent.load() == [os.path.normpath(str(kept))]


def test_neither_store_leaves_debris_in_the_config_directory(tmp_path, torn_write):
    """Temp files accumulating one per failure, in a directory the user
    never opens, is the classic cost of doing this by hand."""
    _calibrated().save("ink")
    project = tmp_path / "a.deckle"
    project.write_text("{}", encoding="utf-8")
    recent.record(str(project))

    with torn_write(), pytest.raises(OSError):
        _calibrated().save("ink")
    with torn_write():
        recent.record(str(tmp_path / "b.deckle"))

    config = tmp_path / "config" / "deckle"
    leftovers = sorted(p.name for p in config.rglob("*") if p.is_file())
    assert leftovers == ["ink.json", "recent_projects.json"]


# -- field drift, the half not yet applied to profiles -----------------


def test_a_profile_round_trips_equal_to_the_one_that_was_saved():
    profile = _calibrated()

    profile.save("ink")

    assert PrinterProfile.load("ink") == profile


def test_a_reloaded_profile_is_still_hashable():
    """A frozen dataclass holding a list is not hashable, and the failure
    surfaces far from the loader -- in a set, a dict key, or a cache."""
    _calibrated().save("ink")

    hash(PrinterProfile.load("ink"))


# -- values a profile cannot honour -------------------------------------
#
# The third loader with this defect, after `LayoutSettings` and a
# project's page entries. Here it wastes paper rather than merely
# misreporting: the profile is what `plan_passes` reads to decide whether
# the back sides need turning.

BAD_VALUES = [
    pytest.param("flip_axis", "diagonal", id="flip-axis"),
    pytest.param("output_face", "sideways", id="output-face"),
    pytest.param("feed_edge", "middle", id="feed-edge"),
    pytest.param("reverse_stack", "yes", id="reverse-stack"),
    pytest.param("imageable_area_pt", [18.0, 18.0], id="area-has-two-numbers"),
    pytest.param("imageable_area_pt", "wide", id="area-is-text"),
    pytest.param("back_offset_x_pt", "left a bit", id="offset-is-text"),
    pytest.param("version", "one", id="version-is-text"),
]


def _store(tmp_path, **over) -> None:
    data = dataclasses.asdict(_calibrated())
    data["imageable_area_pt"] = list(data["imageable_area_pt"])
    data.update(over)
    path = tmp_path / "config" / "deckle" / "printer_profiles" / "ink.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("field,value", BAD_VALUES)
def test_a_profile_value_this_build_cannot_honour_is_refused(tmp_path, field, value):
    _store(tmp_path, **{field: value})

    with pytest.raises(ValueError) as caught:
        PrinterProfile.load("ink")

    assert field in str(caught.value)


def test_a_bad_flip_axis_no_longer_plans_the_back_pass_unturned(tmp_path):
    """The one that costs a stack of paper.

    ``flip_axis`` is ``Literal["long", "short"]`` and nothing enforced it,
    so ``"diagonal"`` loaded happily and planned the back pass as though
    the operator flips about the sheet's horizontal edge. On any printer
    that actually flips about the vertical one, that turns every back side
    upside down, for the whole run, with nothing on screen to suggest it.
    """
    _store(tmp_path, flip_axis="diagonal")

    with pytest.raises(ValueError) as caught:
        PrinterProfile.load("ink")

    assert "'long'" in str(caught.value) and "'short'" in str(caught.value)


def test_an_unknown_profile_key_is_still_tolerated(tmp_path):
    """Unchanged and deliberately opposite: a key this build does not know
    is a setting it can ignore, so a calibration written by a newer build
    still opens. A *value* it does not know is not the same thing."""
    _store(tmp_path, some_future_axis="skew")

    assert PrinterProfile.load("ink").flip_axis == "long"


def test_no_profile_field_round_trips_as_a_list():
    """Exhaustive over the dataclass, so a tuple field added later is
    covered without anyone remembering to add a case here -- the same
    guard ``tests/test_settings_roundtrip.py`` puts on ``LayoutSettings``.
    """
    import dataclasses

    _calibrated().save("ink")

    loaded = PrinterProfile.load("ink")
    for field in dataclasses.fields(PrinterProfile):
        value = getattr(loaded, field.name)
        assert not isinstance(value, list), (
            f"{field.name} came back as a list: {value!r}"
        )


# -- the cross-process case, which no lock can cover --------------------


def test_recording_a_project_survives_a_rename_that_loses_a_race(monkeypatch, tmp_path):
    """Two Deckle windows share one config directory, and a lock inside
    one process cannot serialise them.

    On Windows a losing rename does not lose quietly -- ``os.replace`` is
    ``MoveFileEx``, which raises ``PermissionError: [WinError 5]`` when
    another handle holds the target. ``record`` already promises never to
    raise, on the grounds that a config directory that cannot be written
    must not be what stops a project opening; this pins that the promise
    covers a *contended* write and not only an unwritable one.
    """
    project = tmp_path / "a.deckle"
    project.write_text("{}", encoding="utf-8")

    def contended(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", contended)

    recent.record(str(project))  # must not raise

    assert recent.load() == []


def test_a_contended_profile_write_reports_rather_than_corrupting(monkeypatch):
    """Profiles take the opposite contract to ``recent`` -- ``save``
    raises, because losing a hand-measured calibration silently would be
    worse than being told. What must not happen either way is a damaged
    file, and the previous calibration is still there to re-read.
    """
    _calibrated().save("ink")

    def contended(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", contended)
    with pytest.raises(OSError):
        BUILTIN_PRESETS["generic_face_up_in_order"].save("ink")

    assert PrinterProfile.load("ink").back_offset_x_pt == 3.0
