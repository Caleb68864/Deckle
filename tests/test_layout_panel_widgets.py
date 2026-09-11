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


# -- the schedule under flat sheets ---------------------------------------


@pytest.fixture
def flat_panel(qt_app):
    """A panel over a flat-sheet document with a measured stock."""
    from deckle.app.views.layout_panel import LayoutPanel

    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(10)
    ]
    project = Project(
        pages=pages,
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=36.0,
                              binding_edge="left", fold_scheme="none",
                              paper_thickness_pt=0.3),
        printer=None,
    )
    panel = LayoutPanel(AppState(project))
    panel.set_document_loaded(True)
    return panel


def test_the_schedule_button_is_reachable_under_flat_sheets(flat_panel):
    assert flat_panel.save_schedule_button.isEnabled() is True


def test_the_schedule_button_lives_below_the_mode_tabs(flat_panel):
    """On the Signatures tab it was not merely disabled under flat sheets:
    selecting that tab IS selecting folio, so it was not on screen."""
    assert flat_panel.save_schedule_button.parentWidget() is flat_panel.widget


def test_the_schedule_button_still_needs_a_document(qt_app):
    from deckle.app.views.layout_panel import LayoutPanel

    panel = LayoutPanel(AppState(Project(
        pages=[],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    )))
    panel.set_document_loaded(False)

    assert panel.save_schedule_button.isEnabled() is False
    assert panel.save_schedule_button.toolTip() == (
        "Import a document to build a schedule."
    )


def test_saving_a_flat_sheet_schedule_writes_the_block_thickness(
    flat_panel, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QFileDialog

    out = tmp_path / "book-schedule.txt"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")),
    )
    messages = []
    flat_panel.schedule_saved.connect(messages.append)

    flat_panel.save_schedule_button.click()

    text = out.read_text(encoding="utf-8")
    assert "BINDING THE STACK" in text
    assert "Block thickness: about 0.02in (2pt)" in text
    assert "nothing to gather or sew" in text
    assert any("Saved binding schedule to" in m for m in messages), messages


# -- "save as my defaults" -------------------------------------------------


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_the_panel_offers_saving_and_forgetting_defaults(panel):
    assert panel.save_defaults_button.text() == "Save as my defaults"
    assert panel.forget_defaults_button.text() == "Forget my defaults"


def test_the_defaults_buttons_are_not_on_a_mode_tab(panel):
    assert panel.save_defaults_button.parentWidget() is panel.widget
    assert panel.forget_defaults_button.parentWidget() is panel.widget


def test_saving_defaults_writes_the_current_layout(panel, config_root):
    from deckle.core.defaults import load_defaults

    messages = []
    panel.schedule_saved.connect(messages.append)

    panel.save_defaults_button.click()

    saved = load_defaults()
    assert saved is not None
    assert saved.gutter_pt == 36.0
    assert saved.fold_scheme == "folio"
    assert any("Saved these settings as your defaults" in m for m in messages)


def test_forgetting_defaults_says_so(panel, config_root):
    from deckle.core.defaults import defaults_path

    messages = []
    panel.schedule_saved.connect(messages.append)
    panel.save_defaults_button.click()
    messages.clear()

    panel.forget_defaults_button.click()

    assert not defaults_path().exists()
    assert messages == [
        "Forgot your defaults. New projects start from Deckle's own."
    ]


def test_forgetting_nothing_says_so(panel, config_root):
    messages = []
    panel.schedule_saved.connect(messages.append)

    panel.forget_defaults_button.click()

    assert messages == ["You have no saved defaults."]


def test_a_failed_save_is_reported_not_raised(panel, config_root, monkeypatch):
    def boom(layout):
        raise OSError("disk full")

    monkeypatch.setattr("deckle.app.views.layout_panel.save_defaults", boom)
    messages = []
    panel.schedule_saved.connect(messages.append)

    panel.save_defaults_button.click()

    assert messages and "defaults.json" in messages[0]


def test_saving_defaults_does_not_touch_the_open_project(panel, config_root):
    before = panel.state.project

    panel.save_defaults_button.click()
    panel.forget_defaults_button.click()

    assert panel.state.project is before
    assert panel.state.can_undo is False


# -- paper weight, from the ream wrapper ---------------------------------
#
# `set_paper_from_weight` existed, was tested as a function, and had no
# caller: `deckle-cli --paper-weight 80gsm` derived a caliper and the app
# could not. Its own docstring called it "the escape hatch behind
# `Custom...`", and `_on_paper_stock_changed` early-returns on exactly
# that entry.
#
# These drive the widgets rather than the function, because the function
# was never the broken part. A control wired to nothing, or to the wrong
# setter, passes every test of `set_paper_from_weight` there is.


def _set_weight(panel, weight, *, unit=None, grade=None, stock_type=None):
    """Type a weight into the Paper tab the way a user would.

    Each combo is set by its key through the panel's own index map, so
    this cannot silently select the wrong row if the lists are reordered.
    """
    if unit is not None:
        keys = panel._choice_keys["paper_weight_unit_combo"]
        panel.paper_weight_unit_combo.setCurrentIndex(keys.index(unit))
    if grade is not None:
        keys = panel._choice_keys["paper_grade_combo"]
        panel.paper_grade_combo.setCurrentIndex(keys.index(grade))
    if stock_type is not None:
        keys = panel._choice_keys["paper_type_combo"]
        panel.paper_type_combo.setCurrentIndex(keys.index(stock_type))
    panel.paper_weight_spinbox.setValue(weight)


def test_the_paper_tab_starts_with_no_weight_typed(panel):
    """The premise. Every assertion below is about a change, so a panel
    that already carried the expected thickness would prove nothing."""
    from deckle.core.paper import caliper_pt_from_gsm

    assert panel.paper_weight_spinbox.value() == 0
    assert panel.state.project.layout.paper_thickness_pt != pytest.approx(
        caliper_pt_from_gsm(90, "offset")
    )


def test_a_grammage_typed_on_the_paper_tab_reaches_the_document(panel):
    """The wiring, end to end: widget -> handler -> project."""
    from deckle.core.paper import caliper_pt_from_gsm

    _set_weight(panel, 90, unit="gsm", stock_type="offset")

    assert panel.state.project.layout.paper_thickness_pt == pytest.approx(
        caliper_pt_from_gsm(90, "offset")
    )


def test_a_different_weight_gives_a_different_thickness(panel):
    """The control that must fail for a handler that writes a constant.

    A control wired to `set_paper_thickness_pt(project, 0.42)` would
    satisfy the test above for one value and this one for none.
    """
    _set_weight(panel, 80, unit="gsm", stock_type="offset")
    lighter = panel.state.project.layout.paper_thickness_pt

    _set_weight(panel, 160, unit="gsm", stock_type="offset")
    heavier = panel.state.project.layout.paper_thickness_pt

    assert heavier > lighter
    assert heavier == pytest.approx(2 * lighter)


def test_the_paper_type_re_derives_the_caliper(panel):
    """`--paper-type` is what separates two papers of the same weight, so
    the combo has to reach the arithmetic too -- not just the weight."""
    from deckle.core.paper import caliper_pt_from_gsm

    _set_weight(panel, 100, unit="gsm", stock_type="coated")
    coated = panel.state.project.layout.paper_thickness_pt

    _set_weight(panel, 100, unit="gsm", stock_type="bulky")
    bulky = panel.state.project.layout.paper_thickness_pt

    assert coated == pytest.approx(caliper_pt_from_gsm(100, "coated"))
    assert bulky == pytest.approx(caliper_pt_from_gsm(100, "bulky"))
    assert bulky > coated


def test_pounds_are_read_against_the_chosen_grade(panel):
    """20lb is 75gsm as bond and 54gsm as cover. A pound weight that
    ignored the grade would be wrong by half, silently."""
    from deckle.core.paper import caliper_pt_from_gsm, gsm_from_pounds

    _set_weight(panel, 20, unit="lb", grade="bond", stock_type="offset")
    bond = panel.state.project.layout.paper_thickness_pt

    _set_weight(panel, 20, unit="lb", grade="cover", stock_type="offset")
    cover = panel.state.project.layout.paper_thickness_pt

    assert bond == pytest.approx(
        caliper_pt_from_gsm(gsm_from_pounds(20, "bond"), "offset")
    )
    assert cover == pytest.approx(
        caliper_pt_from_gsm(gsm_from_pounds(20, "cover"), "offset")
    )
    assert bond != pytest.approx(cover)


def test_clearing_the_weight_leaves_the_thickness_alone(panel):
    """Zero is "I have not said", not "this paper is infinitely thin".

    Writing 0 back would erase a caliper the user had set another way --
    from a named stock, or by typing it -- the moment they glanced at
    this box.
    """
    _set_weight(panel, 120, unit="gsm", stock_type="offset")
    derived = panel.state.project.layout.paper_thickness_pt
    assert derived > 0

    panel.paper_weight_spinbox.setValue(0)

    assert panel.state.project.layout.paper_thickness_pt == pytest.approx(derived)


def test_the_thickness_box_follows_the_weight(panel):
    """B29's lesson: a greyed button beside a live menu item is the app
    disagreeing with itself, and so is a thickness box showing one number
    while the document holds another."""
    _set_weight(panel, 90, unit="gsm", stock_type="offset")

    assert panel.paper_thickness_spinbox.points() == pytest.approx(
        panel.state.project.layout.paper_thickness_pt
    )


def test_a_derived_thickness_says_the_stock_is_custom(panel):
    """The named-stock combo can no longer describe this paper, and
    saying otherwise is how B29 was found.

    A named stock is selected FIRST. Without that the assertion is
    vacuous -- a fresh panel already shows ``Custom``, so the first
    version of this test passed against code that left the combo reading
    "80gsm copier (0.104 mm)" over a document holding 0.434pt.
    """
    from deckle.app.views.layout_panel import CUSTOM_STOCK_LABEL
    from deckle.core.paper import caliper_pt_from_gsm

    keys = panel._choice_keys["paper_stock_combo"]
    panel.paper_stock_combo.setCurrentIndex(keys.index("80gsm copier"))
    assert panel.paper_stock_combo.currentText() != CUSTOM_STOCK_LABEL

    _set_weight(panel, 90, unit="gsm", stock_type="bulky")

    assert panel.paper_stock_combo.currentText() == CUSTOM_STOCK_LABEL
    assert panel.state.project.layout.paper_thickness_pt == pytest.approx(
        caliper_pt_from_gsm(90, "bulky")
    )


def test_the_weight_controls_are_declared_in_the_table(panel):
    """The four new widgets go through CONTROLS, not into the form by
    hand. That table exists because this list was once maintained in four
    places, then five."""
    from deckle.app.views.layout_panel import CONTROLS

    declared = {spec.name for spec in CONTROLS}
    for name in (
        "paper_weight_spinbox",
        "paper_weight_unit_combo",
        "paper_grade_combo",
        "paper_type_combo",
    ):
        assert name in declared, f"{name} was added outside the CONTROLS table"
