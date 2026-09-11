"""Which imageable area is actually in force, and for how long.

The driver-reported non-printable border is the number **both** clip
warnings are computed against (``preview_view.clipping_warnings_for_sheet``
takes it as its third argument), the number the preview's solid red guide
is drawn from, and the number "Use printer margins" adopts. Since B6 a
sheet prints at actual size and content outside that border is *lost*
rather than shrunk, so a wrong border is not cosmetic -- it is the same
class of defect as the page-size mismatch: it does not warn, and the loss
shows up on paper.

Before these tests, the border had the lifetime of a single enumeration
for a single printer:

* ``MainWindow._apply_printers`` resolved it once, for whichever printer
  was preselected, and pushed it to the views;
* ``PrintDialog`` knew nothing about it, so ``selected_profile()``
  returned the generic preset's flat 18pt, and ``print_document`` pushes
  that straight back into the window the moment the dialog closes;
* picking a *different* printer in the dialog never brought that
  printer's border with it.

A driver's answer is a fact about a **queue**. It is keyed by printer
name, looked up on every profile resolution, and never written to disk --
see ``printer_capabilities.query_imageable_area_pt``, which says why
saving one would make an uncalibrated printer indistinguishable from a
calibrated one.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.views.preview_view import clipping_warnings_for_sheet
from deckle.app.views.print_dialog import (
    driver_border,
    profile_choices,
    resolve_profile,
)
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side, SourceRef
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile

LETTER = (612.0, 792.0)
PRESET = next(iter(BUILTIN_PRESETS.values()))

#: A border a driver might report that is *narrower* than the preset's
#: 18pt, and one that is *wider*. The wide one is the dangerous case: with
#: the preset in force nothing warns and the content is cut off anyway.
NARROW = (4.0, 6.0, 4.0, 6.0)
WIDE = (36.0, 40.0, 36.0, 40.0)


def _no_saved_profile(name):
    raise FileNotFoundError(name)


def _calibrated(**overrides) -> PrinterProfile:
    return replace(PRESET, **overrides)


def _sheet_inset(inset_pt: float) -> Sheet:
    """One sheet whose only content sits ``inset_pt`` in from every edge."""
    ref = SourceRef(
        path="/nonexistent/edge.pdf",
        page_index=0,
        sha256="0" * 64,
        width_pt=LETTER[0] - 2 * inset_pt,
        height_pt=LETTER[1] - 2 * inset_pt,
    )
    return Sheet(
        index=0,
        front=Side(
            pages=(
                OutputPage(
                    source_ref=ref,
                    placement=Placement(
                        scale_x=1.0,
                        scale_y=1.0,
                        tx=inset_pt,
                        ty=inset_pt,
                        rotate_deg=0,
                    ),
                    is_filler=False,
                ),
            )
        ),
        back=None,
    )


def _blank_page() -> OutputPage:
    return OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )


def _plan(n_sheets: int = 2) -> SheetPlan:
    sheets = [
        Sheet(
            index=i,
            front=Side(pages=(_blank_page(),)),
            back=Side(pages=(_blank_page(),)),
        )
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=LETTER, warnings=[])


# -- the rule itself ---------------------------------------------------


def test_an_uncalibrated_printer_takes_the_drivers_border():
    filled = driver_border(PRESET, "A", False, {"A": NARROW})

    assert filled.imageable_area_pt == NARROW


def test_a_calibration_is_never_overruled_by_a_driver():
    """It exists because somebody printed a target and measured it with a
    ruler. The driver's number has been checked against nothing."""
    measured = _calibrated(imageable_area_pt=(30.0, 30.0, 30.0, 30.0))

    filled = driver_border(measured, "A", True, {"A": NARROW})

    assert filled is measured


def test_a_printer_nobody_asked_about_keeps_the_preset():
    """The map is consulted by name. A border resolved for one printer must
    not become every printer's border -- that is the defect this whole
    file is about, one hop further along."""
    filled = driver_border(PRESET, "Never Enumerated", False, {"A": NARROW})

    assert filled.imageable_area_pt == PRESET.imageable_area_pt


def test_no_answers_at_all_keeps_the_preset():
    assert driver_border(PRESET, "A", False, None) is PRESET
    assert driver_border(PRESET, "A", False, {}) is PRESET


# -- the rule applied where profiles are resolved ----------------------


def test_resolve_profile_fills_in_the_driver_border():
    profile = resolve_profile("A", _no_saved_profile, imageable_areas={"A": WIDE})

    assert profile.imageable_area_pt == WIDE


def test_resolve_profile_without_answers_behaves_as_it_always_did():
    """Three call sites and the whole CLI path pass no areas at all."""
    profile = resolve_profile("A", _no_saved_profile)

    assert profile.imageable_area_pt == PRESET.imageable_area_pt


def test_a_saved_calibration_still_wins_through_resolve_profile():
    measured = _calibrated(imageable_area_pt=(30.0, 30.0, 30.0, 30.0))

    profile = resolve_profile("A", lambda name: measured, imageable_areas={"A": WIDE})

    assert profile.imageable_area_pt == (30.0, 30.0, 30.0, 30.0)


def test_the_combos_own_entries_stay_unsubstituted():
    """``_remember_profile_choice`` writes ``profile_combo.currentData()``
    to disk. A driver's number is not a calibration and must never be
    saved as one -- ``query_imageable_area_pt``'s docstring is explicit
    that nothing it returns is ever written to ``config_dir``."""
    choices, _preselect = profile_choices("A", _no_saved_profile)

    for _label, profile, is_saved in choices:
        assert is_saved is False
        assert profile.imageable_area_pt == PRESET.imageable_area_pt


# -- the lifetime, through the real dialog -----------------------------


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _dialog(printer_names, imageable_areas, profile_loader=_no_saved_profile):
    from deckle.app.views.print_dialog import PrintDialog

    return PrintDialog(
        _plan(),
        None,
        printer_names=list(printer_names),
        profile_loader=profile_loader,
        imageable_areas=imageable_areas,
        resumable_lister=lambda: [],
    )


def test_closing_the_print_dialog_does_not_replace_the_drivers_border(qapp):
    """``print_document`` pushes ``selected_profile()`` into the window on
    every close. It used to push the preset's 18pt over a border the
    driver had already reported."""
    dialog = _dialog(["A"], {"A": WIDE})

    assert dialog.selected_profile().imageable_area_pt == WIDE


def test_the_border_follows_the_printer_picked_in_the_dialog(qapp):
    """The dialog is where the printer is actually chosen. Resolving the
    border once, for whichever printer happened to be preselected, drew
    one machine's limit while printing to another."""
    dialog = _dialog(["A", "B"], {"A": NARROW, "B": WIDE})
    assert dialog.selected_profile().imageable_area_pt == NARROW

    dialog.printer_combo.setCurrentIndex(1)

    assert dialog.printer_combo.currentText() == "B"
    assert dialog.selected_profile().imageable_area_pt == WIDE


def test_the_dialog_never_overrules_a_calibration_either(qapp):
    measured = _calibrated(imageable_area_pt=(30.0, 30.0, 30.0, 30.0))
    dialog = _dialog(["A"], {"A": WIDE}, profile_loader=lambda name: measured)

    assert dialog.selected_profile().imageable_area_pt == (30.0, 30.0, 30.0, 30.0)


def test_a_dialog_told_nothing_still_opens_on_the_preset(qapp):
    """Every existing caller that constructs a dialog without margins."""
    dialog = _dialog(["A"], None)

    assert dialog.selected_profile().imageable_area_pt == PRESET.imageable_area_pt


def test_the_window_passes_its_driver_answers_to_the_dialog():
    """Structural, because the alternative is an end-to-end test that must
    ``exec()`` a modal. ``print_document`` is the single seam between the
    window's enumeration and the dialog's resolution; if it stops handing
    the map over, every test above still passes and the border is wrong
    again the moment a dialog opens."""
    import ast
    import inspect
    import textwrap

    from deckle.app import main as app_main

    source = textwrap.dedent(inspect.getsource(app_main.MainWindow.print_document))
    tree = ast.parse(source)
    construction = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PrintDialog"
    ]
    assert len(construction) == 1, "print_document no longer builds exactly one dialog"
    passed = {kw.arg for kw in construction[0].keywords}
    assert "imageable_areas" in passed, (
        "print_document must hand the window's driver answers to the dialog, "
        "or closing it puts the generic 18pt preset back"
    )


# -- and why any of it matters -----------------------------------------


def test_a_border_wider_than_the_preset_is_the_warning_that_goes_missing():
    """The paper-losing direction, stated as paper.

    Content 25pt in from every edge clears the preset's 18pt border and is
    cut off by a driver reporting 36/40. With the preset in force nothing
    warns -- the same shape as the page-size mismatch: no warning, and the
    loss appears on the sheet.
    """
    sheet = _sheet_inset(25.0)

    against_preset = clipping_warnings_for_sheet(sheet, LETTER, PRESET.imageable_area_pt)
    against_driver = clipping_warnings_for_sheet(sheet, LETTER, WIDE)

    assert against_preset == []
    assert [w.kind for w in against_driver] == ["clipped_by_imageable_area"]


def test_a_border_narrower_than_the_preset_is_the_warning_that_cries_wolf():
    """The other direction costs trust rather than paper, and trust in this
    warning is what stops the next one being ignored."""
    sheet = _sheet_inset(10.0)

    against_preset = clipping_warnings_for_sheet(sheet, LETTER, PRESET.imageable_area_pt)
    against_driver = clipping_warnings_for_sheet(sheet, LETTER, NARROW)

    assert [w.kind for w in against_preset] == ["clipped_by_imageable_area"]
    assert against_driver == []
