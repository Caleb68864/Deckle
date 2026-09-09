"""``deckle profile list/show/set``, and the guard on the ``set``.

N14. ``PrinterProfile.save`` gained its first caller this week (B16's
printer picker); until now the CLI could neither see a profile nor make
one, so the only way to get a calibration onto a headless machine was to
copy a JSON file into a directory the user has never opened.

Most of this file is about **not destroying one**. ``save``'s own
docstring calls a calibration the most expensive data Deckle holds, and it
is right: it is not derived from anything. It comes from printing a
target, measuring it with a ruler, and reprinting when the numbers were
wrong. Everything else in the program can be recomputed from the source
document; this cannot be recomputed at all.

So ``set`` refuses to overwrite one and prints the exact before-and-after
instead. The tests below pin both halves of that -- the refusal, and that
the refusal *shows the diff*, because a guard that only says no is an
obstruction while one that says what would change is a review.

Every test isolates ``XDG_CONFIG_HOME``, so none of them can see or touch
the profiles on the machine running the suite.
"""

from __future__ import annotations

import json

import pytest

from deckle.cli import main
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile, saved_profiles


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    """An empty profile directory, isolated from the real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _create(name="Office LaserJet", *extra):
    return main(
        ["profile", "set", name, "--from", "generic_face_down_reversed", *extra]
    )


# -- list -----------------------------------------------------------------


def test_list_shows_the_builtins_when_nothing_is_saved(config_home, capsys):
    assert main(["profile", "list"]) == 0

    printed = capsys.readouterr().out
    assert "saved profiles: none" in printed
    for name in BUILTIN_PRESETS:
        assert name in printed


def test_list_labels_a_measured_profile_apart_from_a_generic_one(config_home, capsys):
    """The two are not interchangeable and the listing must not imply they are.

    A saved profile was measured against that physical printer; a built-in
    is a stand-in for a measurement nobody has made. Presenting them as one
    undifferentiated list is how somebody picks the stand-in believing they
    have the calibration -- and the difference is invisible until a stack
    of backs comes out upside down.
    """
    assert _create() == 0
    capsys.readouterr()

    assert main(["profile", "list"]) == 0
    printed = capsys.readouterr().out

    assert "saved profiles -- calibrated on this machine:" in printed
    assert "built-in profiles -- generic stand-ins, not measured:" in printed
    assert "Office LaserJet" in printed


def test_list_names_an_unreadable_profile_instead_of_hiding_it(config_home, capsys):
    """A calibration that has stopped opening is the thing to say loudest.

    Skipping it would render as "you never calibrated this printer",
    sending its owner to re-measure something they had already measured --
    and the file may well be recoverable by hand.
    """
    assert _create("Broken") == 0
    capsys.readouterr()
    (config_home / "deckle" / "printer_profiles" / "Broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    assert main(["profile", "list"]) == 0
    printed = capsys.readouterr().out

    assert "Broken" in printed
    assert "unreadable" in printed


def test_list_json_reports_an_unreadable_profile_as_an_error_field(config_home, capsys):
    assert _create("Broken") == 0
    capsys.readouterr()
    (config_home / "deckle" / "printer_profiles" / "Broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    assert main(["profile", "list", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)

    broken = next(e for e in document["profiles"] if e["name"] == "Broken")
    assert broken["profile"] is None
    assert broken["error"] is not None


def test_list_finds_a_profile_whose_name_needed_percent_encoding(config_home, capsys):
    r"""A printer name is not a filename, and the listing has to undo that.

    ``\\server\queue`` is an ordinary Windows queue name and an absolute
    UNC path, so ``_safe_profile_stem`` percent-encodes it. A listing that
    printed the filename would show the user something they cannot pass
    back to ``--profile``.
    """
    name = r"\\server\Copy Room"
    assert _create(name) == 0
    capsys.readouterr()

    assert name in saved_profiles()
    assert main(["profile", "list"]) == 0
    assert name in capsys.readouterr().out


# -- show -----------------------------------------------------------------


def test_show_prints_a_builtin_in_full(config_home, capsys):
    assert main(["profile", "show", "generic_face_up_in_order"]) == 0

    printed = capsys.readouterr().out
    assert "flip axis: short" in printed
    assert "reverse stack: no" in printed
    assert "built-in preset" in printed


def test_show_names_the_file_a_saved_profile_lives_in(config_home, capsys):
    assert _create() == 0
    capsys.readouterr()

    assert main(["profile", "show", "Office LaserJet"]) == 0
    printed = capsys.readouterr().out

    assert "saved calibration" in printed
    assert str(config_home) in printed


def test_show_reports_why_a_saved_profile_will_not_open(config_home, capsys):
    """``--profile`` falls through to a built-in here; ``show`` must not.

    The two want opposite things. An export should keep working from a
    generic preset when a calibration cannot be read; someone who typed
    ``profile show`` is asking about that file specifically and is owed
    the reason, not a different profile with the same name.
    """
    assert _create("Broken") == 0
    capsys.readouterr()
    (config_home / "deckle" / "printer_profiles" / "Broken.json").write_text(
        "{not json", encoding="utf-8"
    )

    assert main(["profile", "show", "Broken"]) == 1
    captured = capsys.readouterr()

    assert "cannot be read" in captured.err
    assert captured.out == ""


def test_show_refuses_an_unknown_name_and_lists_the_alternatives(config_home, capsys):
    assert main(["profile", "show", "nope"]) == 1

    err = capsys.readouterr().err
    assert "no printer profile 'nope'" in err
    for name in BUILTIN_PRESETS:
        assert name in err


# -- set: creating --------------------------------------------------------

def test_set_creates_a_profile_from_a_builtin(config_home, capsys):
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    profile = PrinterProfile.load("Office LaserJet")
    assert profile.back_offset_x_pt == 3.0
    assert profile.back_offset_y_pt == -2.0
    assert profile.flip_axis == "long"  # inherited from the preset


def test_set_refuses_to_invent_a_printer_it_was_not_told_about(config_home, capsys):
    """Creating requires ``--from``, because a profile has no partial form.

    ``flip_axis``, ``output_face``, ``feed_edge`` and ``reverse_stack`` all
    have to say something, and defaulting them would be Deckle guessing a
    printer's reload behaviour -- the same guess ``--pass`` refuses to make
    without a profile, and for the same reason.
    """
    assert main(["profile", "set", "Mystery", "--back-offset", "1,1"]) == 1

    err = capsys.readouterr().err
    assert "--from" in err
    for name in BUILTIN_PRESETS:
        assert name in err
    assert saved_profiles() == {}


def test_set_refuses_to_save_over_a_builtin_name(config_home, capsys):
    """Saving under a built-in's name hides it everywhere without removing it.

    ``PrinterProfile.load`` prefers a saved file, so from then on the
    built-in that ``--profile generic_face_down_reversed`` resolves to is
    not the built-in, with nothing anywhere saying so.
    """
    assert main(
        [
            "profile", "set", "generic_face_down_reversed",
            "--from", "generic_face_up_in_order",
        ]
    ) == 1

    assert "built-in profile name" in capsys.readouterr().err
    assert saved_profiles() == {}


def test_a_created_profile_is_what_export_then_resolves(config_home, tmp_path, capsys):
    """The whole point: a profile set here is one ``--profile`` can use.

    A ``set`` that wrote somewhere ``load`` does not look would pass every
    test about its own output and be useless.
    """
    import os

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    out = tmp_path / "o.pdf"
    assert main(
        ["export", fixture, "-o", str(out), "--profile", "Office LaserJet"]
    ) == 0

    assert "back faces moved +3, -2pt (profile 'Office LaserJet')" in (
        capsys.readouterr().out
    )


# -- set: the guard -------------------------------------------------------


def test_set_will_not_overwrite_a_saved_calibration(config_home, capsys):
    """The test this whole command exists to pass.

    A calibration cannot be recomputed -- it is measured with a ruler --
    so an accidental overwrite costs an afternoon and a stack of paper,
    and a silent one costs it without anyone noticing until the backs
    print wrong.
    """
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    assert main(
        ["profile", "set", "Office LaserJet", "--back-offset", "0,0"]
    ) == 1

    assert PrinterProfile.load("Office LaserJet").back_offset_x_pt == 3.0


def test_the_refusal_shows_exactly_what_would_change(config_home, capsys):
    """A guard that only says no is an obstruction; one that shows the diff
    is a review. Someone who reads it and still wants the change is one
    flag away, and someone who typed the wrong printer name has just been
    shown their mistake.
    """
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    assert main(
        [
            "profile", "set", "Office LaserJet",
            "--back-offset", "1.5,0", "--flip-axis", "short",
        ]
    ) == 1
    err = capsys.readouterr().err

    assert "flip_axis: long -> short" in err
    assert "back_offset_x_pt: 3.0 -> 1.5" in err
    assert "back_offset_y_pt: -2.0 -> 0.0" in err
    assert "--force" in err
    # And where to find the old numbers before replacing them.
    assert str(config_home) in err


def test_force_applies_the_change_and_says_what_it_did(config_home, capsys):
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    assert main(
        [
            "profile", "set", "Office LaserJet",
            "--back-offset", "1.5,0", "--force",
        ]
    ) == 0
    printed = capsys.readouterr().out

    assert "back_offset_x_pt: 3.0 -> 1.5" in printed
    profile = PrinterProfile.load("Office LaserJet")
    assert (profile.back_offset_x_pt, profile.back_offset_y_pt) == (1.5, 0.0)


def test_setting_the_values_a_profile_already_has_is_not_an_overwrite(
    config_home, capsys
):
    """A no-op must not demand ``--force``.

    A script that re-asserts the intended values every run is a reasonable
    thing to write, and making it fail -- or making it pass ``--force``
    routinely, which trains the reflex the flag exists to prevent -- would
    be worse than either.
    """
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    assert main(
        ["profile", "set", "Office LaserJet", "--back-offset", "3,-2"]
    ) == 0
    assert "already has those values" in capsys.readouterr().out


def test_set_refuses_a_from_preset_alongside_a_saved_profile(config_home, capsys):
    """Two possible bases and no rule for which wins, so neither is chosen.

    The same refusal ``--paper-weight`` with ``--paper-thickness`` gets:
    either the preset discards the saved values or it is ignored, and
    silently picking one is how someone ends up with a book bound to a
    number they did not give.
    """
    assert _create("Office LaserJet", "--back-offset", "3,-2") == 0
    capsys.readouterr()

    assert main(
        [
            "profile", "set", "Office LaserJet",
            "--from", "generic_face_up_in_order",
        ]
    ) == 1
    assert "--from" in capsys.readouterr().err
    assert PrinterProfile.load("Office LaserJet").back_offset_x_pt == 3.0


def test_set_will_not_overwrite_a_calibration_it_cannot_read(config_home, capsys):
    """Unreadable is not the same as absent, and must not be treated as it.

    A corrupt file may be a truncated write or a hand edit with a missing
    brace, and either is recoverable by someone who opens it. Replacing it
    because it did not parse would destroy the recoverable case, and there
    would be no diff to show first.
    """
    assert _create("Broken", "--back-offset", "3,-2") == 0
    capsys.readouterr()
    path = config_home / "deckle" / "printer_profiles" / "Broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert main(
        ["profile", "set", "Broken", "--back-offset", "0,0", "--force"]
    ) == 1

    assert path.read_text(encoding="utf-8") == "{not json"
    assert "cannot be read" in capsys.readouterr().err


def test_set_keeps_the_calibration_date_of_the_profile_it_edits(config_home, capsys):
    """Typing a number in is not a calibration run.

    Clearing the date would throw away when a measurement that is mostly
    still standing was made; setting it to today would claim one that
    never happened. Neither is true, so it is left alone.
    """
    PrinterProfile(
        version=1, flip_axis="long", output_face="down", feed_edge="top",
        reverse_stack=True, imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-02T03:04:05", calibration_version=2,
    ).save("Measured")

    assert main(
        ["profile", "set", "Measured", "--back-offset", "1,1", "--force"]
    ) == 0
    capsys.readouterr()

    profile = PrinterProfile.load("Measured")
    assert profile.calibrated_at == "2026-01-02T03:04:05"
    assert profile.calibration_version == 2
    assert profile.back_offset_x_pt == 1.0


def test_set_needs_something_to_change(config_home, capsys):
    assert _create("Office LaserJet") == 0
    capsys.readouterr()

    assert main(["profile", "set", "Office LaserJet"]) == 1
    assert "nothing to change" in capsys.readouterr().err


def test_no_reverse_stack_is_sayable_and_distinct_from_silence(config_home, capsys):
    """``--no-reverse-stack`` must mean false, not "unspecified".

    A ``store_true`` cannot tell "set this to false" from "do not touch
    it", and on a calibration those are very different requests -- one of
    them reverses the back pass of every job on that printer.
    """
    assert _create("Office LaserJet") == 0  # the preset reverses the stack
    capsys.readouterr()
    assert PrinterProfile.load("Office LaserJet").reverse_stack is True

    assert main(
        ["profile", "set", "Office LaserJet", "--no-reverse-stack", "--force"]
    ) == 0
    capsys.readouterr()

    assert PrinterProfile.load("Office LaserJet").reverse_stack is False


def test_imageable_area_is_stored_in_the_order_the_profile_uses(config_home, capsys):
    """left, top, right, bottom -- not ``--crop``'s left, bottom, right, top.

    They are different quantities and the file format's order is the one
    that matters here; re-ordering to match ``--crop`` would apply a head
    margin to the tail.
    """
    assert _create(
        "Office LaserJet", "--imageable-area", "1,2,3,4"
    ) == 0
    capsys.readouterr()

    assert PrinterProfile.load("Office LaserJet").imageable_area_pt == (
        1.0, 2.0, 3.0, 4.0
    )
