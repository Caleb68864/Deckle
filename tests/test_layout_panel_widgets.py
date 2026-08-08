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
