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
        # Trim and crop belong here for the same reason as the rest: they
        # are lengths stored in points and re-displayed in the current
        # unit. They were absent, so the fields whose blocking was actually
        # missing were the ones this test never exercised.
        trim_pt=6.5,
        crop_odd_pt=(1.25, 2.5, 3.75, 5.0),
        crop_even_pt=(5.0, 3.75, 2.5, 1.25),
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


# -- the refresh must not cost the user their history ---------------------


def test_refreshing_adds_no_undo_entries_and_keeps_the_redo_stack():
    """A refresh is not an edit, and must not read as one.

    This is the assertion the value checks above cannot make. When a widget
    is set without its signals blocked, its handler writes back the very
    value the refresh was about to set -- so every "did the document
    change?" test still passes while the user quietly loses their history:
    one undo entry per unblocked control, and a cleared redo stack, because
    `mutate` treats a fresh change as invalidating any undone future.
    """
    state, panel = _panel()
    state.mutate(lambda project: replace(
        project, layout=replace(project.layout, gutter_pt=24.0)
    ))
    state.undo()
    assert state.can_redo, "precondition: there is an undone future to lose"

    undo_before = len(state._undo_stack)
    redo_before = len(state._redo_stack)

    _load(
        state,
        trim_pt=9.0,
        crop_odd_pt=(1.0, 2.0, 3.0, 4.0),
        crop_even_pt=(4.0, 3.0, 2.0, 1.0),
        paper_thickness_pt=0.42,
    )
    panel.refresh_from_project()

    assert len(state._undo_stack) == undo_before
    assert len(state._redo_stack) == redo_before
    assert state.can_redo, "the refresh cleared a redo the user had not spent"


# -- a unit change is a change of notation, not of the book ---------------


@pytest.mark.parametrize("unit", ["in", "mm", "cm", "pt"])
def test_changing_unit_reconverts_trim_and_every_crop_box(unit):
    """Switching units must re-display every stored length, not most of them.

    A box left out keeps the number it showed in the old unit. Nothing looks
    wrong -- and the model is still right until the box is touched -- but the
    next nudge writes that stale number back through `to_points` under the
    new unit, so a 0.25in trim becomes a 0.25mm one.
    """
    from deckle.app.views.layout_panel import from_points

    state, panel = _panel()
    panel.unit_combo.setCurrentText("in")
    _load(
        state,
        trim_pt=18.0,
        crop_odd_pt=(9.0, 18.0, 27.0, 36.0),
        crop_even_pt=(36.0, 27.0, 18.0, 9.0),
    )
    panel.refresh_from_project()

    panel.unit_combo.setCurrentText(unit)

    def shown(box, points):
        """What the box can display: the converted length, at its own
        precision. `0.3175cm` in a three-decimal box is `0.318`, and that
        rounding is the widget's, not the conversion's."""
        return round(from_points(points, unit), box.decimals())

    assert panel.trim_spinbox.value() == pytest.approx(
        shown(panel.trim_spinbox, 18.0)
    )
    for parity, insets in (("odd", (9.0, 18.0, 27.0, 36.0)),
                           ("even", (36.0, 27.0, 18.0, 9.0))):
        for index, edge in enumerate(("left", "bottom", "right", "top")):
            box = panel.crop_spinboxes[(parity, edge)]
            assert box.value() == pytest.approx(shown(box, insets[index])), (
                f"crop {parity} {edge} was not reconverted into {unit}"
            )

    # The unit is notation. The book is unchanged.
    assert state.project.layout.trim_pt == 18.0
    assert state.project.layout.crop_odd_pt == (9.0, 18.0, 27.0, 36.0)
    assert state.project.layout.crop_even_pt == (36.0, 27.0, 18.0, 9.0)


def test_a_unit_change_then_a_nudge_does_not_rewrite_trim_in_the_new_unit():
    """The failure the conversion prevents, driven end to end."""
    state, panel = _panel()
    panel.unit_combo.setCurrentText("in")
    _load(state, trim_pt=18.0)  # 0.25in
    panel.refresh_from_project()

    panel.unit_combo.setCurrentText("mm")
    # Whatever the box now shows, committing it must mean the same physical
    # depth it meant a moment ago -- not 0.25mm.
    panel._on_trim_changed(panel.trim_spinbox.value())

    assert state.project.layout.trim_pt == pytest.approx(18.0)


# -- station positions, and the unit they are displayed in ----------------


def test_refreshing_shows_the_projects_station_positions():
    state, panel = _panel()
    _load(state, sewing_station_positions_pt=(36.0, 144.0))

    panel.refresh_from_project()

    assert panel.station_positions_edit.text() == "0.5, 2"
    assert state.can_undo is False, "the refresh wrote back through a handler"


def test_switching_units_redisplays_the_same_positions():
    """B11's exact shape. A length the model keeps in points that is not
    re-rendered on a unit change keeps its old number under the new unit,
    and the next edit writes that number back as though it were typed --
    `0.5, 2` inches silently becoming 0.5mm and 2mm."""
    state, panel = _panel(sewing_station_positions_pt=(36.0, 144.0))

    panel._on_unit_changed("mm")

    assert panel.station_positions_edit.text() == "12.7, 50.8"

    panel._on_station_positions_changed()

    positions = state.project.layout.sewing_station_positions_pt
    assert [round(p, 3) for p in positions] == [36.0, 144.0]


def test_a_bad_position_is_reported_not_raised():
    state, panel = _panel()
    messages = []
    panel.schedule_saved.connect(messages.append)
    panel.station_positions_edit.setText("two")

    panel._on_station_positions_changed()

    assert messages and messages[0].startswith("Station positions:")
