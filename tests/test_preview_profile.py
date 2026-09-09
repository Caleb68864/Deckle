"""The red guide is the selected printer's border, not a constant (B15).

Three things outside the print path read a ``PrinterProfile``: the
preview's solid red imageable-area guide, the
``clipped_by_imageable_area`` warnings computed beside it, and the layout
panel's "Use printer margins" button. All three were wired to
``main.DEFAULT_PROFILE`` at construction and never touched again -- a
fixed 18pt border on every edge, for the life of the window, whatever
printer was selected and whatever had been measured for it.

So the app drew a border, called it the printer's, and offered to set the
margins to it, on a machine whose printer had been calibrated and whose
calibration was sitting in a file Deckle had already learned to read. The
GUIDE described that line as "your printer's hardware limit"; README §4
said the same. This is what makes those sentences true, as far as they
can be -- the driver's own printable rectangle is still not consulted
(N2), so an *uncalibrated* printer still gets a generic preset's stand-in,
and the docs now say which of the two you are looking at.

The window-level wiring runs out of process, because constructing a real
``QMainWindow`` under pytest exits non-zero here -- the same reason
``tests/test_gui_workflow.py`` and ``tests/test_shutdown.py`` shell out.

**Which of these fail against the old code for the real reason.** The
three probe-driven ones at the bottom: the preview and the panel keep the
18pt constant, and "Use printer margins" writes 18pt into a document whose
printer has a 36pt border. The tests above them fail against the old code
only because ``profile_for_printers`` and ``selected_profile`` did not
exist to call -- they pin the choosing, which had no home at all before.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


def _profile(**overrides):
    from deckle.core.profiles import PrinterProfile

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


def _missing(name):
    raise FileNotFoundError(name)


# -- choosing, without a display -----------------------------------------


def test_no_printers_leaves_the_generic_preset():
    """Nothing has been chosen, so there is nothing better to draw -- and
    Print is disabled in this state anyway."""
    from deckle.app.main import DEFAULT_PROFILE, profile_for_printers

    assert profile_for_printers([], _missing) is DEFAULT_PROFILE


def test_a_calibrated_printer_supplies_the_border():
    """The whole point. A measured imageable area reaches the preview."""
    from deckle.app.main import profile_for_printers

    measured = _profile(imageable_area_pt=(36.0, 12.0, 36.0, 12.0))

    resolved = profile_for_printers(
        ["Uncalibrated", "Measured"],
        lambda name: measured if name == "Measured" else _missing(name),
    )

    assert resolved.imageable_area_pt == (36.0, 12.0, 36.0, 12.0)


def test_an_uncalibrated_printer_falls_back_to_a_preset():
    from deckle.core.profiles import BUILTIN_PRESETS
    from deckle.app.main import profile_for_printers

    resolved = profile_for_printers(["Plain"], _missing)

    assert resolved == next(iter(BUILTIN_PRESETS.values()))


def test_the_preview_and_the_print_dialog_choose_the_same_printer():
    """Not an implementation detail. A preview drawing one printer's
    border while the dialog is about to preselect another's would be a
    worse lie than the constant it replaces, so both go through
    ``select_preselected_printer`` and neither has its own opinion."""
    from deckle.app.main import profile_for_printers
    from deckle.app.views.print_dialog import (
        resolve_profile,
        select_preselected_printer,
    )

    measured = _profile(imageable_area_pt=(30.0, 30.0, 9.0, 9.0))

    def loader(name):
        if name == "Second":
            return measured
        raise FileNotFoundError(name)

    names = ["First", "Second", "Third"]
    dialog_choice = select_preselected_printer(names, loader)

    assert profile_for_printers(names, loader) == resolve_profile(
        dialog_choice, loader
    )


# -- the dialog hands its choice back ------------------------------------


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_plan():
    from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side

    page = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    return SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,)), back=Side(pages=(page,)))],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )


def test_the_dialog_reports_the_profile_it_is_set_to_print_with():
    """B16 added the picker; without a way to read the choice back it
    ended when the dialog closed."""
    from deckle.app.views.print_dialog import PrintDialog

    measured = _profile(imageable_area_pt=(27.0, 27.0, 27.0, 27.0))
    dialog = PrintDialog(
        _make_plan(),
        printer_names=["Measured"],
        profile_loader=lambda name: measured,
        resumable_lister=lambda: [],
    )

    assert dialog.selected_profile() == measured


def test_picking_a_different_paper_behaviour_changes_what_is_reported():
    from deckle.app.views.print_dialog import PrintDialog

    dialog = PrintDialog(
        _make_plan(),
        printer_names=["Plain"],
        profile_loader=_missing,
        resumable_lister=lambda: [],
    )
    assert dialog.profile_combo.count() >= 2

    first = dialog.selected_profile()
    dialog.profile_combo.setCurrentIndex(1)

    assert dialog.selected_profile() != first


# -- the window actually pushes it, out of process -----------------------

PROBE = '''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

import deckle.app.main as am
from deckle.core.profiles import PrinterProfile

MEASURED = PrinterProfile(
    version=1, flip_axis="long", output_face="down", feed_edge="top",
    reverse_stack=True, imageable_area_pt=(36.0, 36.0, 36.0, 36.0),
    calibrated_at="2026-01-01T00:00:00", calibration_version=1,
)

# No real spooler is touched: enumeration is answered by this stub, and no
# profile is read from or written to the user's real config directory.
import deckle.app.printer_query as pq
pq.available_printer_names = lambda: ["Measured"]

# Built with no printers so construction does not start a background query
# with no event loop to settle it; the real `refresh_printers` is then
# driven synchronously below, which is the path under test.
real_refresh = am.MainWindow.refresh_printers
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
)
window = am.MainWindow()
am.MainWindow.refresh_printers = real_refresh

from dataclasses import replace
from deckle.core.loader import load_pdf

pages = load_pdf(sys.argv[1])
window.state.mutate(lambda p: replace(p, pages=list(pages)))
window._on_imported(list(pages), [])

report = {"seeded": list(window.preview_view.profile.imageable_area_pt)}

window.profile_loader = lambda name: MEASURED
window.refresh_printers(blocking=True)

report["preview"] = list(window.preview_view.profile.imageable_area_pt)
report["panel"] = list(window.layout_panel.profile.imageable_area_pt)

# The button a user actually presses, and the number it writes into the
# document. This is the whole visible consequence of the wiring.
window.layout_panel._on_use_printer_margins()
report["margin_top_pt"] = window.state.project.layout.margin_top_pt

print(json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    pytest.importorskip("PySide6")
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    # A fresh `MainWindow` offers back any never-saved autosave it finds
    # under `data_dir("autosave")`, through a modal that a headless probe
    # cannot answer. Point the data root at a scratch directory so the
    # probe sees an empty store -- and so it cannot leave recovery offers
    # in the developer's real one either.
    data_root = tmp_path_factory.mktemp("data")
    env["XDG_DATA_HOME"] = str(data_root)
    env["APPDATA"] = str(data_root)
    result = subprocess.run(
        [sys.executable, "-u", "-c", PROBE, FIXTURE],
        capture_output=True, text=True, env=env, timeout=180, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"the probe did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-2000:]}"
    )
    assert result.stdout.strip(), "the probe produced no report"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_window_starts_on_the_generic_preset(report):
    """The premise. Enumeration is asynchronous, so the window is built
    before it knows anything about the printers, and 18pt is what it draws
    for that fraction of a second. The defect was that it never stopped."""
    assert report["seeded"] == [18.0, 18.0, 18.0, 18.0]


def test_enumeration_pushes_the_profile_into_the_preview(report):
    assert report["preview"] == [36.0, 36.0, 36.0, 36.0], (
        "the preview is still drawing the built-in constant after the "
        "printer it belongs to was resolved"
    )


def test_enumeration_pushes_the_profile_into_the_layout_panel(report):
    assert report["panel"] == [36.0, 36.0, 36.0, 36.0]


def test_use_printer_margins_adopts_the_printers_actual_inset(report):
    """The button said "Use printer margins" and used 18pt regardless --
    so on a printer with a 0.5in border it set margins that still print
    into the dead zone, and the clipping warning that would have caught it
    was measured against the same wrong number."""
    assert report["margin_top_pt"] == 36.0
