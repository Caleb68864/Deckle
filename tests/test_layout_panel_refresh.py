"""``refresh_from_project`` -- the controls following a loaded document.

The least-covered delicate code in the app, and the reason it is delicate
is that it writes to widgets whose handlers write back. Signals are
blocked throughout for exactly that reason; an unguarded refresh would
overwrite the freshly opened layout with whatever the widgets happened to
hold, one control at a time.

The blocking works. What did not was naming a paper size the presets do
not cover: that was worked out once in the constructor and never again, so
opening a project with a custom sheet into a window that already had one
left the combo showing the PREVIOUS job's paper. The panel said "A4" over
a 500x700 sheet. Nothing broke -- changing orientation still swapped the
real dimensions -- it just stated something untrue, which is precisely the
failure this method exists to prevent.
"""

from __future__ import annotations

import os
from dataclasses import fields, replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import LayoutSettings, Project  # noqa: E402

LETTER = (612.0, 792.0)
A4 = (595.28, 841.89)
CUSTOM = (500.0, 700.0)
FIELDS = [f.name for f in fields(LayoutSettings)]


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _panel(**overrides):
    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel

    base = dict(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    base.update(overrides)
    state = AppState(Project(pages=[], layout=LayoutSettings(**base), printer=None))
    return state, LayoutPanel(state)


def _load(state, **overrides):
    """Put a different layout under the panel, as opening a project does."""
    state._project = replace(
        state.project, layout=replace(state.project.layout, **overrides)
    )


def _items(panel) -> list[str]:
    return [panel.paper_combo.itemText(i) for i in range(panel.paper_combo.count())]


# -- naming a paper the presets do not cover ------------------------------


def test_a_custom_paper_is_named_after_a_refresh():
    """The bug. The combo went on showing the previous job's paper."""
    state, panel = _panel()

    _load(state, paper=CUSTOM)
    panel.refresh_from_project()

    assert "Custom" in panel.paper_combo.currentText()
    assert "500" in panel.paper_combo.currentText()
    assert "700" in panel.paper_combo.currentText()


def test_a_custom_paper_is_named_at_construction_too():
    """It always worked here; the constructor was the only place it did."""
    _state, panel = _panel(paper=CUSTOM)

    assert "Custom" in panel.paper_combo.currentText()


def test_switching_between_custom_sizes_does_not_accumulate_entries():
    """Each custom size needs its own label, and the one it replaces has to
    go -- otherwise the list grows for the life of the session and every
    stale entry names a sheet nothing is using."""
    state, panel = _panel()
    before = len(_items(panel))

    for paper in ((500.0, 700.0), (400.0, 900.0), (300.0, 400.0)):
        _load(state, paper=paper)
        panel.refresh_from_project()

    assert len(_items(panel)) == before + 1, f"entries piled up: {_items(panel)}"
    assert "300" in panel.paper_combo.currentText()


def test_returning_to_a_preset_removes_the_custom_entry():
    state, panel = _panel()
    _load(state, paper=CUSTOM)
    panel.refresh_from_project()

    _load(state, paper=LETTER)
    panel.refresh_from_project()

    assert panel.paper_combo.currentText() == "Letter"
    assert not any("Custom" in name for name in _items(panel))


def test_the_name_list_stays_in_step_with_the_combo():
    """`_paper_names` is indexed into to select an entry, so the moment the
    two disagree the panel selects the wrong paper."""
    state, panel = _panel()

    for paper in (CUSTOM, LETTER, A4, (400.0, 900.0), LETTER):
        _load(state, paper=paper)
        panel.refresh_from_project()
        assert _items(panel) == panel._paper_names


def test_an_exact_preset_is_recognised_rather_than_called_custom():
    state, panel = _panel()

    _load(state, paper=A4)
    panel.refresh_from_project()

    assert panel.paper_combo.currentText() == "A4"


def test_a_landscape_preset_is_still_that_preset():
    """Orientation is a separate control; rotating Letter does not make it
    a custom size."""
    state, panel = _panel()

    _load(state, paper=(792.0, 612.0))
    panel.refresh_from_project()

    assert panel.paper_combo.currentText() == "Letter"
    assert panel.orientation_combo.currentText() == "Landscape"


def test_a_custom_paper_survives_editing_something_else():
    """The panel showing the wrong name would be survivable. The panel
    writing that wrong name into the document would not."""
    state, panel = _panel()
    _load(state, paper=CUSTOM)
    panel.refresh_from_project()

    panel._on_gutter_changed(0.5)

    assert state.project.layout.paper == CUSTOM


# -- the refresh must not write back --------------------------------------


def test_refreshing_never_changes_the_document():
    """Every widget set here fires a handler that writes to the project.
    Signals are blocked for that reason; this is the assertion that the
    blocking is complete, across every field at once."""
    state, panel = _panel()
    loaded = LayoutSettings(
        paper=CUSTOM, gutter_pt=42.5, binding_edge="right", start_on_recto=False,
        landscape_policy="scale", margin_top_pt=11.0, margin_bottom_pt=22.0,
        margin_outer_pt=33.0, slack_to="outer", margins_linked=False,
        fold_scheme="folio", sheets_per_signature=6, grain="long",
        paper_thickness_pt=0.42, sewing_stations=5, blank_mode="balanced",
    )
    state._project = replace(state.project, layout=loaded)

    panel.refresh_from_project()

    for field in FIELDS:
        assert getattr(state.project.layout, field) == getattr(loaded, field), (
            f"refreshing the panel overwrote {field}"
        )


@pytest.mark.parametrize("unit", ["pt", "in", "mm", "cm"])
def test_refreshing_never_changes_the_document_in_any_unit(unit):
    """The lengths are converted for display, so a rounding trip through
    the spinboxes is the likeliest way a write-back would sneak in."""
    state, panel = _panel()
    panel.unit_combo.setCurrentText(unit)
    loaded = replace(
        state.project.layout,
        gutter_pt=17.77, margin_top_pt=9.13, margin_bottom_pt=3.3,
        margin_outer_pt=25.4, paper_thickness_pt=0.37,
    )
    state._project = replace(state.project, layout=loaded)

    panel.refresh_from_project()

    for field in FIELDS:
        assert getattr(state.project.layout, field) == getattr(loaded, field)


# -- the controls describe what was loaded --------------------------------


def test_every_control_follows_the_loaded_layout():
    state, panel = _panel()
    loaded = LayoutSettings(
        paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="right",
        start_on_recto=False, landscape_policy="scale", margin_top_pt=12.0,
        margin_bottom_pt=13.0, margin_outer_pt=14.0, slack_to="split",
        margins_linked=False, fold_scheme="folio", sheets_per_signature=4,
        grain="short", paper_thickness_pt=0.2, sewing_stations=3,
        blank_mode="balanced",
    )
    state._project = replace(state.project, layout=loaded)

    panel.refresh_from_project()

    assert panel.binding_edge_combo.currentText() == "right"
    assert panel.start_on_recto_check.isChecked() is False
    assert panel.landscape_policy_combo.currentText() == "scale"
    assert panel.blank_mode_combo.currentText() == "balanced"
    assert panel.sewing_stations_spinbox.value() == 3
    assert panel.sheets_per_signature_spinbox.value() == 4
    assert panel.link_margins_check.isChecked() is False
    assert panel._grain_keys[panel.grain_combo.currentIndex()] == "short"
    assert panel._slack_keys[panel.slack_combo.currentIndex()] == "split"
    assert panel.orientation_combo.currentText() == "Landscape"
    assert panel.tabs.currentIndex() == panel._signature_tab_index


def test_the_mode_tab_follows_the_fold_scheme_both_ways():
    """The tabs ARE the fold scheme, so a panel left on the wrong one
    offers the controls for a job the user is not doing."""
    state, panel = _panel()

    _load(state, fold_scheme="folio")
    panel.refresh_from_project()
    assert panel.tabs.currentIndex() == panel._signature_tab_index

    _load(state, fold_scheme="none")
    panel.refresh_from_project()
    assert panel.tabs.currentIndex() == panel._single_tab_index
