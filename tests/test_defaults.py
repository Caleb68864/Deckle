"""``deckle.core.defaults`` -- the layout a new project starts from.

Every new window opened on US Letter, portrait, zero gutter, zero margins,
flat sheets. For the one person Deckle is built for -- at home, binding on
the paper they always buy -- that is about a dozen controls to reset before
the first useful preview, every single time.

Two asymmetries are deliberate and both are tested here: ``load_defaults``
never raises (it runs while a window is being built, and a preference must
not be what stops Deckle opening), while ``save_defaults`` does (it is an
explicit action, and swallowing its failure would leave someone believing
their settings were remembered).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from deckle.core.defaults import (
    DEFAULTS_VERSION,
    EXCLUDED_FIELDS,
    defaults_path,
    forget_defaults,
    load_defaults,
    save_defaults,
)
from deckle.core.models import LayoutSettings


@pytest.fixture(autouse=True)
def config_root(tmp_path, monkeypatch):
    """Point every config lookup at ``tmp_path``.

    Both variables, because ``paths._root`` reads the environment at call
    time: an unguarded test writes into the developer's real config
    directory and silently changes what their next launch does.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _layout(**overrides) -> LayoutSettings:
    base = dict(paper=(841.89, 595.28), gutter_pt=54.0, binding_edge="right")
    base.update(overrides)
    return LayoutSettings(**base)


def _write_raw(text: str) -> None:
    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_nothing_saved_means_no_defaults():
    assert load_defaults() is None


def test_a_saved_layout_comes_back_equal():
    layout = _layout(
        fold_scheme="folio", sheets_per_signature=5, blank_mode="balanced",
        grain="short", paper_thickness_pt=0.288, trim_pt=18.0,
        margin_top_pt=36.0, margin_bottom_pt=36.0, margin_outer_pt=36.0,
        slack_to="outer", margins_linked=False, start_on_recto=False,
        landscape_policy="scale", sewing_stations=5,
        sewing_station_positions_pt=(36.0, 144.0),
    )

    save_defaults(layout)

    assert load_defaults() == layout


def test_tuples_come_back_as_tuples():
    """JSON has one sequence type and Python has two. A layout holding a
    list is not equal to the one saved and is not hashable."""
    save_defaults(_layout(paper=(841.89, 595.28)))

    loaded = load_defaults()

    assert isinstance(loaded.paper, tuple)
    hash(loaded)


def test_the_crops_are_not_saved():
    """Measured off one scan's margins. Carried into the next project they
    would silently crop a document they were never measured against."""
    save_defaults(_layout(
        crop_odd_pt=(1.0, 2.0, 3.0, 4.0), crop_even_pt=(5.0, 6.0, 7.0, 8.0),
    ))

    loaded = load_defaults()

    assert loaded.crop_odd_pt is None
    assert loaded.crop_even_pt is None
    stored = json.loads(defaults_path().read_text(encoding="utf-8"))["layout"]
    assert "crop_odd_pt" not in stored
    assert "crop_even_pt" not in stored


def test_the_gathering_list_is_not_saved():
    """`split_signatures_at` refuses lengths that do not sum to the sheet
    count, so a carried-over value would make the NEXT import fail to
    impose with a message about numbers the user never typed."""
    save_defaults(_layout(fold_scheme="folio", signature_lengths=(7, 7, 6)))

    assert load_defaults().signature_lengths is None


def test_the_excluded_fields_are_named_in_one_place():
    assert EXCLUDED_FIELDS == frozenset(
        {"crop_odd_pt", "crop_even_pt", "signature_lengths"}
    )


def test_the_paper_stock_is_saved():
    """Properties of the paper someone buys and the way they sew, which is
    precisely what a default is for."""
    save_defaults(_layout(
        paper_thickness_pt=0.288, grain="short", sewing_stations=5,
        trim_pt=18.0,
    ))

    loaded = load_defaults()

    assert loaded.paper_thickness_pt == 0.288
    assert loaded.grain == "short"
    assert loaded.sewing_stations == 5
    assert loaded.trim_pt == 18.0


def test_the_file_lives_in_the_config_directory(config_root):
    """`config_dir`, not `data_dir`: this is a setting the user chose,
    exactly like a printer profile."""
    assert defaults_path() == config_root / "deckle" / "defaults.json"


def test_the_file_is_versioned():
    save_defaults(_layout())

    payload = json.loads(defaults_path().read_text(encoding="utf-8"))

    assert payload["version"] == DEFAULTS_VERSION
    assert isinstance(payload["layout"], dict)


@pytest.mark.parametrize("text", [
    "not json",
    "[1, 2, 3]",
    '"a string"',
    '{"version": 1}',
    '{"version": 1, "layout": []}',
    '{"version": 1, "layout": {"binding_edge": "middle"}}',
    '{"version": 1, "layout": {"paper": [0, 0]}}',
    '{"version": 99, "layout": {"paper": [612, 792]}}',
])
def test_an_unusable_file_reads_as_none_rather_than_raising(text):
    _write_raw(text)

    assert load_defaults() is None


def test_a_file_from_an_older_build_still_opens():
    _write_raw(json.dumps({
        "version": 1,
        "layout": {"paper": [612, 792], "gutter_pt": 18, "binding_edge": "left"},
    }))

    loaded = load_defaults()

    assert loaded.paper == (612.0, 792.0)
    assert loaded.gutter_pt == 18
    assert loaded.fold_scheme == "none"


def test_a_file_from_a_newer_build_still_opens_without_warning(recwarn):
    """An older build opening a newer defaults file is the normal case, not
    a data-loss event; a bare warning naming a line inside Deckle is not
    something the user can act on."""
    _write_raw(json.dumps({
        "version": 1,
        "layout": {
            "paper": [612, 792], "gutter_pt": 18, "binding_edge": "left",
            "future_field": 1,
        },
    }))

    loaded = load_defaults()

    assert loaded.paper == (612.0, 792.0)
    assert [w for w in recwarn.list] == []


def test_a_failed_save_leaves_the_previous_defaults_intact(monkeypatch):
    save_defaults(_layout(gutter_pt=36.0))

    def boom(path, text, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("deckle.core.defaults.write_text_atomic", boom)
    with pytest.raises(OSError):
        save_defaults(_layout(gutter_pt=99.0))

    assert load_defaults().gutter_pt == 36.0


def test_forget_removes_the_file():
    save_defaults(_layout())

    assert forget_defaults() is True
    assert not defaults_path().exists()
    assert load_defaults() is None


def test_forgetting_nothing_is_not_an_error():
    assert forget_defaults() is False


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is advisory on Windows")
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_an_unwritable_config_directory_raises():
    """Deliberately unlike `load_defaults`: the user asked for this, and a
    silent failure would have them believe it worked."""
    root = defaults_path().parent
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o500)
    try:
        with pytest.raises(OSError):
            save_defaults(_layout())
    finally:
        root.chmod(0o700)
