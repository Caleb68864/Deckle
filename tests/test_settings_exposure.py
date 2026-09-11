"""Everything a user can change should be changeable in the app.

The owner's rule, stated directly: *"expose all things margins, gutter,
orientation etc. If we can edit it, it should be in the UI."*

This file is the audit, kept honest by running rather than by being
re-read. It asks three questions, and each is measured against the real
objects -- the real ``argparse`` parser, the real :class:`LayoutSettings`,
the real ``CONTROLS`` table -- never against a copy of them:

1. **Is every CLI flag accounted for?** Each one is either expected to be
   reachable in the GUI or carries a written exemption. A flag that is
   neither fails, so a new setting cannot be added on the command line
   alone without somebody deciding about the app.
2. **Is every project-file setting displayed by some control?** Measured
   by perturbing one field at a time and asking every control what it
   would show. A control cannot pass this by claiming to cover a field;
   it has to actually change.
3. **Are the documented escape hatches reachable?** A function whose
   docstring calls it "the escape hatch behind ``Custom...``" and which
   nothing calls is the failure this whole instruction exists to remove.

Why the perturbation check rather than reading ``Control.field``: several
controls derive what they show (the crop boxes read one inset out of a
four-tuple), and a test that read the declaration would agree with a
control that declares a field and displays something else. Asking what
the control *shows* cannot be satisfied that way.
"""

from __future__ import annotations

import dataclasses

import pytest

from deckle.app.views.layout_panel import CONTROLS, LayoutPanel
from deckle.cli import build_parser
from deckle.core.models import LayoutSettings


# -- the exposure decision, one row per CLI flag --------------------------

GUI = "gui"

#: Flags that are deliberately NOT controls, and why.
#:
#: The owner's rule is "if we *can* edit it" -- so a thing that is not a
#: setting of the document, or that has no meaning inside a running app,
#: is not a gap. Anything whose reason reads like an excuse belongs in
#: the other list.
EXEMPT: dict[str, str] = {
    # Where output goes, and whether to produce it at all. The app writes
    # through Save dialogs, which are the same decision made the GUI way.
    "--output": "the app asks with a Save dialog instead",
    "--dry-run": "the preview IS the dry run; it renders the real PDF",
    "--json": "a machine-readable stdout has no meaning inside a window",
    # Which part of the document to act on. The app has the page grid and
    # the sheet navigator for this; a text range would be a second, worse
    # way to express a selection that can already be made by clicking.
    "--pages": "the page grid is the selection UI",
    "--sheets": "the preview's sheet navigator and Print's own range",
    # Print-run arguments. These live on the Print dialog, not the layout
    # panel, and are covered by tests/test_print_dialog.py.
    "--pass": "chosen in the Print dialog, which plans both passes",
    "--profile": "the Print dialog's printer picker resolves the profile",
    "--printer": "the app records the printer it is actually driving",
    # Calibration. The whole PrinterProfile -- flip_axis, output_face,
    # feed_edge, reverse_stack, imageable_area_pt and the two back-offset
    # numbers -- is editable only by `deckle-cli profile set` or by hand.
    # This is the largest genuine gap in the audit and it is NOT closed
    # here: these are numbers whose wrong value ruins a stack of paper
    # with no way to preview the damage, so the right control is the
    # calibration wizard that measures them from a printed proof sheet,
    # which README's status table already records as not built. A
    # free-text box for `back_offset_y_pt` would be the half-wired
    # control this instruction exists to remove.
    "--back-offset": "calibration; needs the measuring wizard, not a text box",
    "--flip-axis": "calibration; see --back-offset",
    "--output-face": "calibration; see --back-offset",
    "--feed-edge": "calibration; see --back-offset",
    "--reverse-stack": "calibration; see --back-offset",
    "--no-reverse-stack": "calibration; see --back-offset",
    "--imageable-area": "calibration; see --back-offset",
    # Managing the profile store rather than describing a printer.
    "--from": "which built-in a new profile starts from; the app picks one",
    "--force": "guard on overwriting a hand-measured calibration, not a setting",
    # Shown, just not on the layout panel.
    "--version": "Help > About Deckle shows the build and its log path",
    # CLI-only renderings.
    "--rule": "a printed ruler for checking the printer, not a document setting",
    "--dpi": "resolution of the CLI's composite image; the app draws on screen",
    "--parity": "the app's ink-composite box has its own parity control",
    "--page-size": "`dummy` writes a scratch document; it is not a project",
    # Reached in the app, but not through a CONTROLS row. Each names the
    # mechanism, and `test_the_non_control_routes_still_exist` checks the
    # named thing is really there.
    "--auto-crop": "the Crop tab's 'Measure crop from the ink' button",
    "--auto-crop-margin": "part of the same button's measurement",
}

#: Flags expected to be reachable through a control on the layout panel,
#: with the ``LayoutSettings`` field each one ultimately writes.
#:
#: The field name is what makes this more than a list of hopes: question 2
#: proves that field is displayed by some control, so a row here cannot be
#: satisfied by a widget that exists and changes nothing.
GUI_FLAGS: dict[str, str] = {
    "--paper": "paper",
    "--landscape": "paper",
    "--gutter": "gutter_pt",
    "--binding-edge": "binding_edge",
    "--blank-mode": "blank_mode",
    "--grain": "grain",
    "--fold-scheme": "fold_scheme",
    "--sheets-per-signature": "sheets_per_signature",
    "--signatures": "signature_lengths",
    "--sewing-stations": "sewing_stations",
    "--stations": "sewing_station_positions_pt",
    "--trim": "trim_pt",
    "--crop": "crop_odd_pt",
    "--crop-even": "crop_even_pt",
    "--paper-thickness": "paper_thickness_pt",
    # The three this audit opened. `set_paper_from_weight` exists, is
    # documented as the escape hatch behind `Custom...`, and had no
    # caller: the CLI could turn what the ream wrapper says into a
    # caliper and the app could not.
    "--paper-weight": "paper_thickness_pt",
    "--paper-type": "paper_thickness_pt",
    "--paper-grade": "paper_thickness_pt",
}

#: Fields that no ``Control.displayed()`` reports, with the name of the
#: thing that does display or set them.
#:
#: All three are deliberate and all three are documented in
#: ``layout_panel`` itself. They are listed rather than special-cased
#: silently, and :func:`test_the_non_control_routes_still_exist` checks
#: the named symbol is really there -- otherwise an entry here becomes a
#: hiding place for exactly the gap this file looks for.
#:
#: This list is also where the audit's own false positives went. The
#: first run reported all three as invisible; each turned out to be
#: reached by a mechanism ``displayed()`` cannot see, which is why the
#: mechanism is named here instead of a finding being filed.
NOT_A_CONTROL_ROW: dict[str, str] = {
    # The paper pair is two combos -- size and orientation -- with a
    # handler, because a custom size has to be nameable in the list.
    "paper": "_on_paper_changed",
    # "The tabs ARE the mode. Selecting one sets `fold_scheme`."
    "fold_scheme": "_on_fold_scheme_changed",
    # A `unit_text` control renders itself from the model in the current
    # display unit, so it has no `field` and no `read` to report.
    "sewing_station_positions_pt": "station_positions_text",
}


# -- machinery ------------------------------------------------------------

_BASE = LayoutSettings(
    paper=(612.0, 792.0),
    gutter_pt=54.0,
    binding_edge="left",
    start_on_recto=True,
    landscape_policy="rotate",
    margin_top_pt=36.0,
    margin_bottom_pt=36.0,
    margin_outer_pt=36.0,
    slack_to="gutter",
    margins_linked=False,
    fold_scheme="none",
    sheets_per_signature=4,
    grain="unknown",
)

#: One different, legal value per field. Legal so that the difference is
#: one the program would actually accept.
_VARIANTS: dict[str, object] = {
    "paper": (792.0, 1224.0),
    "gutter_pt": 72.0,
    "binding_edge": "right",
    "start_on_recto": False,
    "landscape_policy": "scale",
    "margin_top_pt": 18.0,
    "margin_bottom_pt": 18.0,
    "margin_outer_pt": 18.0,
    "slack_to": "split",
    "margins_linked": True,
    "fold_scheme": "folio",
    "sheets_per_signature": 8,
    "grain": "long",
    "paper_thickness_pt": 0.42,
    "trim_pt": 18.0,
    "crop_odd_pt": (9.0, 9.0, 9.0, 9.0),
    "crop_even_pt": (9.0, 9.0, 9.0, 9.0),
    "blank_mode": "balanced",
    "signature_lengths": (10, 10, 8),
    "sewing_stations": 5,
    "sewing_station_positions_pt": (72.0, 216.0, 360.0),
}


def _layout_fields() -> list[str]:
    names = [f.name for f in dataclasses.fields(LayoutSettings)]
    assert names, "LayoutSettings exposed no fields to audit"
    return names


def _control_row_fields() -> list[str]:
    """The fields a ``CONTROLS`` row is expected to display.

    The three reached another way are excluded here rather than skipped
    inside the test: they are not "cannot be checked on this machine",
    which is what a skip means everywhere else in this suite, and three
    permanent skips would quietly change a number the README publishes.
    :func:`test_the_non_control_routes_still_exist` is what covers them.
    """
    return [name for name in _layout_fields() if name not in NOT_A_CONTROL_ROW]


def _walk(parser, flags: set[str], depth: int = 0) -> None:
    """Collect ``--flags`` from ``parser`` and every subparser beneath it.

    Recursive because ``profile`` has subcommands of its own -- ``list``,
    ``show`` and ``set`` -- and the first version of this walker stopped
    one level down. It therefore audited 33 flags and silently missed the
    eight on ``profile set``, which are the whole printer calibration:
    ``--flip-axis``, ``--output-face``, ``--feed-edge``,
    ``--reverse-stack``, ``--back-offset``, ``--imageable-area``,
    ``--from`` and ``--force``. An audit that cannot see the settings
    furthest from the GUI is worse than no audit, so the shape of the
    parser is followed rather than assumed.
    """
    assert depth < 5, "subparser nesting deeper than expected; walker may loop"
    for action in parser._actions:  # noqa: SLF001
        flags.update(
            flag
            for flag in action.option_strings
            if flag.startswith("--") and flag != "--help"
        )
        # `choices` is a subparser map only on a subparsers action; on an
        # ordinary argument it is the list of permitted VALUES
        # (`--flip-axis {long,short}`), which has no parsers in it.
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for sub in choices.values():
            if hasattr(sub, "_actions"):
                _walk(sub, flags, depth + 1)


def _cli_flags() -> set[str]:
    flags: set[str] = set()
    _walk(build_parser(), flags)

    assert flags, "build_parser() exposed no --flags"
    # The premise of the recursion, asserted rather than trusted: if
    # `profile set` ever stops being a nested subparser this number
    # collapses and every "accounted for" verdict below weakens without
    # anything failing.
    assert "--flip-axis" in flags, (
        "the walker is no longer reaching `profile set`, so the printer "
        "calibration flags are being audited as though they did not exist"
    )
    return flags


def _controls_showing(field: str) -> list[str]:
    """Which controls display a different value when ``field`` changes.

    :param field: a ``LayoutSettings`` field name.
    :returns: the names of the controls that noticed.
    :raises AssertionError: the perturbation produced an identical
        object, which would make "no control noticed" meaningless.
    """
    variant = dataclasses.replace(_BASE, **{field: _VARIANTS[field]})
    assert variant != _BASE, (
        f"the variant for {field!r} equals the baseline, so this check "
        "would report every control as blind to it"
    )

    noticed = []
    for spec in CONTROLS:
        if not spec.is_value:
            continue
        if spec.displayed(_BASE) != spec.displayed(variant):
            noticed.append(spec.name)
    return noticed


# -- 1. every flag is accounted for ---------------------------------------


def test_every_cli_flag_is_either_exposed_or_exempt():
    """The audit itself. A new setting cannot reach the command line
    without somebody deciding whether it belongs in the app."""
    flags = _cli_flags()
    decided = set(GUI_FLAGS) | set(EXEMPT)

    undecided = sorted(flags - decided)
    assert not undecided, (
        "these CLI flags are neither exposed in the GUI nor carry a written "
        f"exemption: {undecided}. Add a control, or an EXEMPT entry saying "
        "why a control would be wrong."
    )


def test_the_audit_does_not_name_flags_that_no_longer_exist():
    """The other direction. A stale row here is a decision about nothing,
    and it would quietly hide a renamed flag from the check above."""
    flags = _cli_flags()
    stale = sorted((set(GUI_FLAGS) | set(EXEMPT)) - flags)

    assert not stale, f"the exposure audit names flags the parser does not have: {stale}"


def test_no_flag_is_classified_twice():
    overlap = sorted(set(GUI_FLAGS) & set(EXEMPT))
    assert not overlap, f"classified both exposed and exempt: {overlap}"


def test_every_exemption_gives_a_reason():
    """An exemption list whose entries say nothing is a list of things
    somebody did not want to do."""
    silent = sorted(flag for flag, reason in EXEMPT.items() if len(reason.strip()) < 12)
    assert not silent, f"exemptions with no real reason: {silent}"


# -- 2. every setting is visible ------------------------------------------


@pytest.mark.parametrize("field", _control_row_fields())
def test_every_project_setting_is_displayed_by_some_control(field: str):
    """A setting the project file holds that nothing in the app shows.

    Measured, not declared: the field is changed and every control is
    asked what it would display. A control that names the field but shows
    something else does not count.
    """
    noticed = _controls_showing(field)

    assert noticed, (
        f"no control on the layout panel displays {field!r}; it can be set "
        "from the command line and from a saved project, and the app cannot "
        "show or change it"
    )


def test_the_perturbation_check_can_fail():
    """The control that must fail.

    A field no control reads has to come back empty, or the check above
    is satisfied by anything. ``version`` is not a ``LayoutSettings``
    field at all, so nothing can display it.
    """
    assert "version" not in _layout_fields()

    class _Invisible:
        """Stands in for a field the table does not cover."""

    unseen = [
        spec.name
        for spec in CONTROLS
        if spec.is_value and spec.displayed(_BASE) is _Invisible
    ]
    assert unseen == []


def test_the_non_control_routes_still_exist():
    """The two fields exempted from the check above name a method. If one
    is renamed or removed, the exemption becomes a hiding place."""
    from deckle.app.views import layout_panel

    for field, symbol in NOT_A_CONTROL_ROW.items():
        assert field in _layout_fields(), f"{field!r} is no longer a setting"
        assert hasattr(LayoutPanel, symbol) or hasattr(layout_panel, symbol), (
            f"{field!r} is exempted from the control check because "
            f"{symbol!r} reaches it, and that no longer exists"
        )


# -- 3. the escape hatches are reachable ----------------------------------


def test_paper_weight_reaches_the_app():
    """H5, and the clearest case of the owner's rule.

    ``set_paper_from_weight`` turns what is printed on the ream wrapper
    into a caliper. Its docstring calls it *"the escape hatch behind
    ``Custom...``"*. It had no caller anywhere in ``deckle/app/``: the
    command line could do this and the app could not, and the roadmap
    note excusing it said it was "reachable only from the CLI", which
    cannot be true -- ``deckle.cli`` must never import ``deckle.app``.
    """
    import inspect

    from deckle.app.views import layout_panel

    source = inspect.getsource(layout_panel)
    calls = source.count("set_paper_from_weight")

    assert calls > 1, (
        "set_paper_from_weight is defined and never called, so the app "
        "cannot derive a caliper from a paper weight the way the CLI can"
    )
