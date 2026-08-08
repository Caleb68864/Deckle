"""The new panel controls, driven as widgets rather than as functions.

Every other test of this module exercises the pure ``set_*`` functions,
which is right for arithmetic and blind to wiring: a control connected to
nothing, or to the wrong setter, passes all of them. These construct the
panel offscreen and click it.

That matters most for the two controls that are not simple setters. The
suggestion label has to *appear* when advice exists and *vanish* once it
is taken -- a stale sentence beside a button that applies something else
is worse than no advice. And ``refresh_from_project`` writes to every
control when a project is opened, so a new widget it does not know about
either shows the previous job's value or raises.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from deckle.app.state import AppState  # noqa: E402
from deckle.core.models import (  # noqa: E402
    LayoutSettings, Project, SourcePage, SourceRef,
)


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qt_app):
    from deckle.app.views.layout_panel import LayoutPanel

    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(16)
    ]
    project = Project(
        pages=pages,
        layout=LayoutSettings(paper=(792.0, 612.0), gutter_pt=36.0,
                              binding_edge="left", fold_scheme="folio",
                              sheets_per_signature=1),
        printer=None,
    )
    return LayoutPanel(AppState(project))


def _stock_option(panel, index):
    return panel.paper_stock_combo.itemText(index)


def test_choosing_a_paper_sets_a_thickness(panel):
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))

    assert panel.state.project.layout.paper_thickness_pt > 0


def test_the_dropdown_shows_each_paper_with_its_caliper(panel):
    """The number is what lets someone with calipers see whether the
    estimate matches their stock."""
    assert "mm" in _stock_option(panel, 1)


def test_advice_appears_once_a_paper_is_chosen(panel):
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))

    assert panel.suggestion_label.text()


def test_taking_the_advice_changes_the_sheet_count_and_clears_it(panel):
    """The label must go when the advice is taken. A stale sentence beside
    a live button is how someone applies something they cannot see."""
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))
    before = panel.state.project.layout.sheets_per_signature

    panel.suggestion_button.click()

    assert panel.state.project.layout.sheets_per_signature != before
    assert panel.suggestion_label.text() == ""


def test_the_spin_box_follows_the_applied_advice(panel):
    """The control has to show what was applied, or the panel disagrees
    with the project underneath it."""
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))
    panel.suggestion_button.click()

    assert (panel.sheets_per_signature_spinbox.value()
            == panel.state.project.layout.sheets_per_signature)


def test_a_trim_changes_the_advice(panel):
    """Creep is absorbed by trimming, so the trim is part of the answer."""
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))
    without = panel.suggestion_label.text()

    panel.trim_spinbox.setValue(0.25)

    assert panel.suggestion_label.text() != without
    assert panel.state.project.layout.trim_pt > 0


def test_gatherings_reach_the_project(panel):
    panel.signature_lengths_edit.setText("2,2")
    panel.signature_lengths_edit.editingFinished.emit()

    assert panel.state.project.layout.signature_lengths == (2, 2)


def test_a_mistyped_gathering_list_does_not_raise(panel):
    """A typo in a text field is a typo, not a bug report -- and an
    exception out of a Qt slot is a crash with no traceback."""
    panel.signature_lengths_edit.setText("2,2")
    panel.signature_lengths_edit.editingFinished.emit()

    panel.signature_lengths_edit.setText("2,x")
    panel.signature_lengths_edit.editingFinished.emit()

    assert panel.state.project.layout.signature_lengths == (2, 2)


def test_a_crop_box_reaches_the_project(panel):
    panel.crop_spinboxes[("odd", "left")].setValue(0.5)

    assert panel.state.project.layout.crop_odd_pt is not None


def test_zeroing_every_crop_box_removes_the_crop(panel):
    """All zeros means no crop, not a crop of nothing -- the imposer
    treats ``None`` and ``(0,0,0,0)`` differently in the cache key."""
    panel.crop_spinboxes[("odd", "left")].setValue(0.5)

    panel.crop_spinboxes[("odd", "left")].setValue(0.0)

    assert panel.state.project.layout.crop_odd_pt is None


def test_the_two_parities_crop_independently(panel):
    panel.crop_spinboxes[("odd", "left")].setValue(0.5)

    assert panel.state.project.layout.crop_odd_pt is not None
    assert panel.state.project.layout.crop_even_pt is None


def test_reopening_a_project_refreshes_every_new_control(panel):
    """``refresh_from_project`` writes to every control when a project is
    opened. A widget it does not know about keeps the previous job's
    value, which is worse than showing nothing."""
    panel.paper_stock_combo.setCurrentText(_stock_option(panel, 1))
    panel.trim_spinbox.setValue(0.25)
    panel.crop_spinboxes[("odd", "left")].setValue(0.5)
    panel.signature_lengths_edit.setText("2,2")
    panel.signature_lengths_edit.editingFinished.emit()

    fresh = Project(
        pages=panel.state.project.pages,
        layout=LayoutSettings(paper=(792.0, 612.0), gutter_pt=36.0,
                              binding_edge="left"),
        printer=None,
    )
    panel.state = AppState(fresh)
    panel.refresh_from_project()

    assert panel.trim_spinbox.value() == 0.0
    assert panel.crop_spinboxes[("odd", "left")].value() == 0.0
    assert panel.signature_lengths_edit.text() == ""


def test_a_thickness_with_no_preset_behind_it_reads_as_custom(panel):
    """A saved project carries a caliper, not a paper name. Naming a
    preset the binder may not be using would be a confident guess."""
    from deckle.app.views.layout_panel import CUSTOM_STOCK_LABEL

    panel.refresh_from_project()

    assert panel.paper_stock_combo.currentText() == CUSTOM_STOCK_LABEL


# -- the panel's own shape -----------------------------------------------


def test_page_setup_is_split_across_tabs(panel):
    """It had grown to twenty-six rows -- eight of them crop boxes -- and
    a settings panel you have to scroll is one where the control you want
    is never on screen."""
    titles = [panel.setup_tabs.tabText(i) for i in range(panel.setup_tabs.count())]

    assert len(titles) >= 3
    assert any("Crop" in title for title in titles)


def test_no_settings_tab_is_taller_than_a_dozen_rows(panel):
    """The number that made this necessary. A ceiling rather than an exact
    count, so adding one control does not fail the suite -- but adding
    eight does, which is what happened."""
    for index in range(panel.setup_tabs.count()):
        rows = panel.setup_tabs.widget(index).layout().rowCount()
        assert rows <= 12, (panel.setup_tabs.tabText(index), rows)


def test_the_settings_tabs_are_not_the_mode_tabs(panel):
    """Two separate tab widgets, deliberately."""
    assert panel.setup_tabs is not panel.tabs


def test_choosing_a_settings_tab_never_changes_how_the_book_folds(panel):
    """The invariant that decided the design. The mode tabs *are* the
    mode -- selecting one sets ``fold_scheme`` -- so putting Crop beside
    Signatures would mean clicking it changed how the book folds. If these
    two tab widgets are ever merged, this is what fails.
    """
    before = panel.state.project.layout.fold_scheme

    for index in range(panel.setup_tabs.count()):
        panel.setup_tabs.setCurrentIndex(index)
        assert panel.state.project.layout.fold_scheme == before


def test_the_mode_tabs_still_set_the_fold_scheme(panel):
    """The other half: the behaviour the settings tabs must not acquire is
    one the mode tabs must keep."""
    panel.tabs.setCurrentIndex(panel._signature_tab_index)
    assert panel.state.project.layout.fold_scheme == "folio"

    panel.tabs.setCurrentIndex(panel._single_tab_index)
    assert panel.state.project.layout.fold_scheme == "none"


def test_every_control_survives_the_split(panel):
    """Seventeen rows moved between layouts. A control left behind would
    still exist as an attribute and simply never appear."""
    for widget in (
        panel.unit_combo, panel.paper_combo, panel.grain_combo,
        panel.paper_stock_combo, panel.paper_thickness_spinbox,
        panel.trim_spinbox, panel.auto_crop_button, panel.gutter_spinbox,
        panel.binding_edge_combo,
    ):
        assert widget.parent() is not None, widget
    for key, box in panel.crop_spinboxes.items():
        assert box.parent() is not None, key
