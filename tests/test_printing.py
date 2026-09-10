"""Tests for the PassPlanner: SheetPlan + PrinterProfile -> PrintPass list."""

from __future__ import annotations

import pytest

import json


from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.printing import (
    PrintPass,
    PrintResult,
    duplex_flip_edge,
    pass_export,
    plan_passes,
)
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile


def _blank_output_page() -> OutputPage:
    return OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )


def _make_plan(n_sheets: int, paper_pt: tuple[float, float] = (612.0, 792.0)) -> SheetPlan:
    sheets = [
        Sheet(
            index=i,
            front=Side(pages=(_blank_output_page(),)),
            back=Side(pages=(_blank_output_page(),)),
        )
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=paper_pt, warnings=[])


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


# --- rotate_backs: the flip axis against the paper ---------------------
#
# `flip_axis` is a MEASURED fact about the printer -- which named edge the
# operator physically turns the stack about. `duplex_flip_edge(paper)` is a
# GEOMETRIC fact about the job -- which named edge is the sheet's vertical
# one, and so the one it must turn about for the backs to land upright
# (docs/decisions.md, 2026-08-06: "the sheet must turn about its VERTICAL
# edge ... so either constant is wrong for half of Deckle's own output").
#
# The backs need a half turn exactly when those two disagree. All four
# combinations are pinned below, and they must stay four: for months the
# rule was `flip_axis == "long"`, which is right for landscape and exactly
# inverted for portrait, and no test noticed because every test used one
# orientation. A rule derived from either quantity alone passes half of
# this table and ruins a whole run of paper on the other half, with
# nothing on screen to say so.

A4_PORTRAIT = (595.0, 842.0)
A4_LANDSCAPE = (842.0, 595.0)


@pytest.mark.parametrize(
    "paper_pt, flip_axis, expected, why",
    [
        (A4_PORTRAIT, "long", False, "portrait: long IS vertical, lands upright"),
        (A4_PORTRAIT, "short", True, "portrait: short is horizontal, lands inverted"),
        (A4_LANDSCAPE, "short", False, "landscape: short IS vertical, lands upright"),
        (A4_LANDSCAPE, "long", True, "landscape: long is horizontal, lands inverted"),
    ],
)
def test_rotate_backs_compares_flip_axis_against_the_paper(
    paper_pt, flip_axis, expected, why
):
    """The half turn is needed exactly when the axes disagree.

    Not ``flip_axis == "long"`` (right only for landscape) and not
    ``duplex_flip_edge(paper)`` alone (which knows nothing about the
    printer). Each is a special case of this comparison, correct for
    exactly one orientation.
    """
    profile = _profile(flip_axis=flip_axis)
    passes = plan_passes(_make_plan(3, paper_pt=paper_pt), profile)
    back = next(p for p in passes if p.side == "back")

    assert back.rotate_backs is expected, (
        f"{why}: flip_axis={flip_axis!r} on a "
        f"{'portrait' if paper_pt[1] >= paper_pt[0] else 'landscape'} sheet "
        f"whose vertical edge is {duplex_flip_edge(paper_pt)!r} -- expected "
        f"rotate_backs={expected}, got {back.rotate_backs}. Getting this "
        "backwards prints every back side upside down for the whole run."
    )


def test_rotate_backs_is_not_derived_from_the_flip_axis_alone():
    """The same printer, two papers, two different answers.

    This is the shape of the bug that stood for months: a rule reading
    only ``flip_axis`` cannot produce two answers here, so it must be
    wrong for one of them.
    """
    profile = _profile(flip_axis="long")

    portrait = next(
        p
        for p in plan_passes(_make_plan(2, paper_pt=A4_PORTRAIT), profile)
        if p.side == "back"
    )
    landscape = next(
        p
        for p in plan_passes(_make_plan(2, paper_pt=A4_LANDSCAPE), profile)
        if p.side == "back"
    )

    assert portrait.rotate_backs != landscape.rotate_backs


def test_rotate_backs_is_not_derived_from_the_paper_alone():
    """The same paper, two printers, two different answers."""
    plan = _make_plan(2, paper_pt=A4_PORTRAIT)

    long_edge = next(
        p for p in plan_passes(plan, _profile(flip_axis="long")) if p.side == "back"
    )
    short_edge = next(
        p for p in plan_passes(plan, _profile(flip_axis="short")) if p.side == "back"
    )

    assert long_edge.rotate_backs != short_edge.rotate_backs


def test_front_pass_never_rotates_whatever_the_paper():
    """Only the back pass is turned; the fronts print as imposed."""
    for paper_pt in (A4_PORTRAIT, A4_LANDSCAPE):
        for flip_axis in ("long", "short"):
            passes = plan_passes(
                _make_plan(2, paper_pt=paper_pt), _profile(flip_axis=flip_axis)
            )
            front = next(p for p in passes if p.side == "front")
            assert front.rotate_backs is False


def test_pass_export_carries_the_same_half_turn_as_plan_passes():
    """``pass_export`` recomputes nothing -- including on landscape paper."""
    for paper_pt in (A4_PORTRAIT, A4_LANDSCAPE):
        for flip_axis in ("long", "short"):
            plan = _make_plan(2, paper_pt=paper_pt)
            profile = _profile(flip_axis=flip_axis)
            back = next(p for p in plan_passes(plan, profile) if p.side == "back")

            assert pass_export(plan, profile, "back").rotate_180 is back.rotate_backs
            assert pass_export(plan, profile, "front").rotate_180 is False


def test_reverse_stack_true_reverses_back_pass_order():
    profile = _profile(reverse_stack=True)
    passes = plan_passes(_make_plan(4), profile)
    front = next(p for p in passes if p.side == "front")
    back = next(p for p in passes if p.side == "back")
    assert back.sheet_order == list(reversed(front.sheet_order))


def test_reverse_stack_false_keeps_back_pass_order():
    profile = _profile(reverse_stack=False)
    passes = plan_passes(_make_plan(4), profile)
    front = next(p for p in passes if p.side == "front")
    back = next(p for p in passes if p.side == "back")
    assert back.sheet_order == front.sheet_order


def test_plan_passes_with_explicit_sheets_covers_only_those_sheets():
    profile = _profile()
    passes = plan_passes(_make_plan(10), profile, sheets=[7])
    for p in passes:
        assert p.sheet_order == [7]


def test_every_pass_has_a_nonempty_reload_instruction_naming_axis_and_face():
    profile = _profile(flip_axis="short", output_face="up")
    passes = plan_passes(_make_plan(2), profile)
    for p in passes:
        assert p.reload_instruction
        assert isinstance(p.reload_instruction, str)
    back = next(p for p in passes if p.side == "back")
    assert "short" in back.reload_instruction
    assert "face up" in back.reload_instruction


def test_builtin_presets_cover_face_down_reversed_and_face_up_in_order():
    assert len(BUILTIN_PRESETS) >= 2
    face_down_reversed = [
        p
        for p in BUILTIN_PRESETS.values()
        if p.output_face == "down" and p.reverse_stack is True
    ]
    face_up_in_order = [
        p
        for p in BUILTIN_PRESETS.values()
        if p.output_face == "up" and p.reverse_stack is False
    ]
    assert face_down_reversed
    assert face_up_in_order


def test_print_pass_and_print_result_and_print_backend_shapes():
    pp = PrintPass(
        index=0,
        sheet_order=[0, 1],
        side="front",
        reload_instruction="load paper",
        rotate_backs=False,
    )
    assert pp.side == "front"

    pr = PrintResult(submitted=2, job_id="abc", error=None)
    assert pr.submitted == 2


def test_printer_profile_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")

    profile = _profile()
    profile.save("My Test Printer")

    loaded = PrinterProfile.load("My Test Printer")
    assert loaded == profile

    saved_path = tmp_path / "deckle" / "printer_profiles" / "My Test Printer.json"
    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data["flip_axis"] == "long"


# --- duplex flip edge --------------------------------------------------
#
# The spine is vertical on the sheet under every scheme Deckle imposes, so
# the back must be turned about the sheet's *vertical* edge. Which named
# edge that is depends entirely on the sheet's orientation.


def test_a_portrait_sheet_flips_on_its_long_edge():
    assert duplex_flip_edge((612.0, 792.0)) == "long"


def test_a_landscape_sheet_flips_on_its_short_edge():
    # Folio imposes onto a landscape sheet folded down the middle. Turning
    # it about the long (horizontal) edge would land every back upside down.
    assert duplex_flip_edge((792.0, 612.0)) == "short"


def test_a_square_sheet_flips_on_its_long_edge():
    # Neither edge is longer, so neither answer is wrong -- pin one so the
    # exported PDF does not depend on a float comparison going either way.
    assert duplex_flip_edge((612.0, 612.0)) == "long"


# -- a printer name is not a filename -------------------------------------


def test_a_unc_printer_name_stays_inside_the_config_directory(tmp_path, monkeypatch):
    """`config_dir / f"{name}.json"` with a Windows queue name.

    ``\\\\server\\queue`` is an *absolute* UNC path, so joining it discarded
    the config directory entirely -- pathlib treats an absolute right-hand
    side as the whole answer -- and the calibration was written onto the
    print server, or nowhere.

    **This test cannot reproduce that on Linux**, and does not pretend to:
    ``PurePosixPath`` does not read ``\\`` as a separator, so the unfixed
    code passes here too. It is kept because it pins the invariant the
    Windows case needs -- one file, in the config directory, loadable back
    under the same name -- and would catch a future "fix" that mangled the
    name into something ``load`` could not find. The traversal coverage
    that genuinely fails without the fix is the ``../escape`` and ``a/b``
    parametrization below.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    profiles_dir = tmp_path / "deckle" / "printer_profiles"

    profile = _profile()
    profile.save(r"\\server\queue")

    written = list(profiles_dir.glob("*.json"))
    assert len(written) == 1, "the profile did not land in the config directory"
    assert PrinterProfile.load(r"\\server\queue") == profile


@pytest.mark.parametrize("name", ["../escape", "..", ".", "a/b", "c:d"])
def test_no_printer_name_escapes_the_config_directory(name, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    profiles_dir = tmp_path / "deckle" / "printer_profiles"

    _profile().save(name)

    written = [p for p in tmp_path.rglob("*.json")]
    assert len(written) == 1
    assert written[0].parent == profiles_dir, f"{name!r} escaped to {written[0]}"


def test_an_ordinary_name_is_still_readable_on_disk(tmp_path, monkeypatch):
    """This directory is one the GUIDE sends people into by hand."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")

    _profile().save("My Test Printer")

    assert (tmp_path / "deckle" / "printer_profiles" / "My Test Printer.json").exists()


def test_a_profile_saved_under_the_old_scheme_is_still_found(tmp_path, monkeypatch):
    """Changing the naming scheme must not orphan a calibration.

    It was measured by hand and reprinted until the numbers were right;
    reporting the printer as uncalibrated would send the user to do all of
    that again.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    profiles_dir = tmp_path / "deckle" / "printer_profiles"
    profiles_dir.mkdir(parents=True)
    import json as _json
    from dataclasses import asdict

    legacy = profiles_dir / "Old:Printer.json"
    legacy.write_text(_json.dumps(asdict(_profile())), encoding="utf-8")

    assert PrinterProfile.load("Old:Printer") == _profile()


# -- one pass, as a file ----------------------------------------------
#
# Printing a pass here and exporting it to be printed somewhere else -- a
# copy shop, a second machine -- are the same physical pass, so they have
# to come from the same arithmetic. The CLI assembled its own `export`
# call for `--pass` and the desktop app could not export a pass at all.



def test_pass_export_carries_the_sheet_order_the_pass_would_feed():
    from deckle.core.printing import pass_export, plan_passes

    plan = _make_plan(4)
    profile = _profile(reverse_stack=True, flip_axis="long")

    back = pass_export(plan, profile, "back")
    expected = next(p for p in plan_passes(plan, profile) if p.side == "back")

    assert back.sheets == expected.sheet_order
    assert back.sheets == [3, 2, 1, 0]


def test_the_half_turn_belongs_to_the_back_pass_only():
    """`rotate_180` on a front pass would turn every front upside down.
    The flag is the answer about the *back*, and the front pass must not
    inherit it. Stated on portrait paper with a short-edge flip, which is
    the combination that needs the turn there."""
    from deckle.core.printing import pass_export

    plan = _make_plan(2, paper_pt=A4_PORTRAIT)
    turns_about_the_horizontal_edge = _profile(flip_axis="short")

    assert pass_export(plan, turns_about_the_horizontal_edge, "back").rotate_180 is True
    assert pass_export(plan, turns_about_the_horizontal_edge, "front").rotate_180 is False


def test_a_flip_about_the_sheets_vertical_edge_needs_no_turn_at_all():
    """On portrait paper that is the long edge; on landscape, the short."""
    from deckle.core.printing import pass_export

    portrait = _make_plan(2, paper_pt=A4_PORTRAIT)
    landscape = _make_plan(2, paper_pt=A4_LANDSCAPE)

    assert pass_export(portrait, _profile(flip_axis="long"), "back").rotate_180 is False
    assert pass_export(landscape, _profile(flip_axis="short"), "back").rotate_180 is False


def test_the_measured_back_offset_travels_with_the_pass():
    """The most expensive datum Deckle holds -- printed, measured by hand,
    reprinted when the numbers were wrong. Dropping it on the way to a
    file is how a registration correction silently stops applying."""
    from deckle.core.printing import pass_export

    profile = _profile(back_offset_x_pt=1.5, back_offset_y_pt=-2.0)
    assert pass_export(_make_plan(2), profile, "back").back_offset_pt == (1.5, -2.0)


def test_the_reload_instruction_travels_with_the_file():
    """Whoever prints the exported pass may never have seen Deckle, and
    this is the sentence that decides whether the backs land on the right
    fronts."""
    from deckle.core.printing import pass_export

    export = pass_export(_make_plan(2), _profile(reverse_stack=True), "back")
    assert "reverse the printed stack" in export.reload_instruction


def test_a_narrowed_selection_narrows_the_pass():
    """Reprinting one signature is the normal path with a smaller input."""
    from deckle.core.printing import pass_export

    export = pass_export(_make_plan(6), _profile(reverse_stack=False), "front", [2, 3])
    assert export.sheets == [2, 3]
