"""The control table, and the drift it exists to make impossible.

``LayoutPanel`` used to maintain the same ~25 controls by hand in four
places -- the constructor, ``refresh_from_project``, ``_on_unit_changed``,
and twenty near-identical ``_on_*_changed`` handlers. B11, B12 and B29
were each one list extended in one place and not the others, and each was
fixed by extending the list again.

There is one table now. **The test that matters in this file is
``test_every_control_on_the_panel_is_on_the_table``**: it compares the
widget tree against :data:`CONTROLS`, so a control added to the panel
without a row fails the suite the moment it is built. Every other
assertion here is worth less, because every other assertion checks
something the table has already made true -- this one checks that the
table is still the only place the answer lives.

The rest generalise the three bugs across the whole table rather than
across the fields somebody remembered: every length reconverts on a unit
change (B11), a refresh writes nothing back (B12), and a caliper survives
being displayed in points (B29).
"""

from __future__ import annotations

import os
from dataclasses import fields, replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from deckle.app.state import AppState  # noqa: E402
from deckle.app.views.layout_panel import (  # noqa: E402
    CONTROLS,
    CUSTOM_STOCK_LABEL,
    LayoutPanel,
    from_points,
)
from deckle.core.models import (  # noqa: E402
    LayoutSettings, Project, SourcePage, SourceRef,
)

LAYOUT_FIELDS = [f.name for f in fields(LayoutSettings)]


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pages(count: int = 16):
    return [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(count)
    ]


@pytest.fixture
def panel(qt_app):
    project = Project(
        pages=_pages(),
        layout=LayoutSettings(paper=(792.0, 612.0), gutter_pt=36.0,
                              binding_edge="left", fold_scheme="folio",
                              sheets_per_signature=1),
        printer=None,
    )
    return LayoutPanel(AppState(project))


def _structural_widget_types():
    """Widget kinds that are layout or output, never a value someone sets.

    **A denylist, deliberately, and this is the important line in the
    file.** It used to be an allowlist -- ``(QAbstractSpinBox, QCheckBox,
    QComboBox, QLineEdit, QRadioButton)`` -- which made the guard blind to
    every control *kind* nobody had thought of. Demonstrated, not
    inferred: a ``QSlider``, a ``QPlainTextEdit`` and a ``QListWidget``
    were added to the Paper tab's form by hand, in exactly the shape the
    table replaced, and the suite stayed byte-identical at 2217 passed.

    An allowlist fails open. This file exists because a list maintained in
    more than one place drifts -- B11, B12 and B29 were each that -- so a
    guard that fails open re-arms the bug it was built to prevent.

    Inverted, an unfamiliar kind is treated as a control and has to be
    declared, which is a failure somebody reads. The cost is that a new
    *structural* kind has to be named here; that is a deliberate, visible
    decision rather than a silent gap.
    """
    from PySide6.QtWidgets import QLabel, QPushButton, QTabWidget, QToolButton

    return (QLabel, QPushButton, QToolButton, QTabWidget)


def _is_structural(widget) -> bool:
    from PySide6.QtWidgets import QWidget

    # `type(...) is QWidget`, not `isinstance`: a bare QWidget is a
    # container, but a QWidget *subclass* is something somebody wrote, and
    # the safe reading of something somebody wrote is "a control until
    # declared otherwise".
    return type(widget) is QWidget or isinstance(widget, _structural_widget_types())


def _laid_out_widgets(panel):
    """Every widget actually placed into one of the panel's layouts.

    Position rather than type. A control somebody added is one they put in
    a form row; the parts a control builds for itself -- a spin box's line
    edit, a combo's popup view and its scroll bars -- are children of that
    control, and are filtered out by :func:`_value_widgets` below.
    """
    from PySide6.QtWidgets import QLayout

    layouts = list(panel.widget.findChildren(QLayout))
    own = panel.widget.layout()
    if own is not None:
        layouts.append(own)

    seen: set[int] = set()
    found = []
    for layout in layouts:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is None or id(widget) in seen:
                continue
            seen.add(id(widget))
            found.append(widget)
    return found


def _value_widgets(panel):
    """Every control on the panel that holds a value.

    Deliberately not a list of names -- a list is the thing this file
    exists to prevent -- and no longer a list of *types* either, for the
    reason :func:`_structural_widget_types` gives.
    """
    candidates = [w for w in _laid_out_widgets(panel) if not _is_structural(w)]
    candidate_ids = {id(w) for w in candidates}

    found = []
    for widget in candidates:
        parent = widget.parent()
        while parent is not None:
            if id(parent) in candidate_ids:
                break  # an internal part of another control
            parent = parent.parent()
        else:
            found.append(widget)
    return found


# -- the guard ------------------------------------------------------------


def test_every_control_on_the_panel_is_on_the_table(panel):
    """Add a control without a table row and this fails.

    The point of the refactor, asserted. Four places used to have to be
    kept in step by hand and were not, three times over. The table is now
    the only place a control is declared -- so the thing worth pinning is
    not that the table is read correctly, but that nothing exists outside
    it.

    If this fails after you added a widget: add a `Control` row to
    `CONTROLS` in `deckle/app/views/layout_panel.py`. The constructor, the
    refresh, the unit change and the handler all come from that row, and
    there is nothing else to write.
    """
    declared = {spec.name: panel.controls[spec.name] for spec in CONTROLS}
    on_table = {id(widget) for widget in declared.values()}
    in_tree = {id(widget) for widget in _value_widgets(panel)}

    missing = [
        widget for widget in _value_widgets(panel) if id(widget) not in on_table
    ]
    assert not missing, (
        "these controls are on the panel but not in CONTROLS: "
        + ", ".join(
            f"{type(w).__name__}({w.toolTip()[:40]!r})" for w in missing
        )
    )
    # And the other direction: a row whose widget never reached the panel
    # would be a control declared and then not built.
    unbuilt = [
        spec.name for spec in CONTROLS
        if spec.is_value and id(declared[spec.name]) not in in_tree
    ]
    assert not unbuilt, f"declared but not on the panel: {unbuilt}"


def test_the_guard_notices_a_control_that_skipped_the_table(panel):
    """The guard's own teeth. A control built the old way -- straight into
    a form, with no row -- must be caught, or the test above is decoration.
    """
    from PySide6.QtWidgets import QDoubleSpinBox

    smuggled = QDoubleSpinBox(panel.widget)
    smuggled.setToolTip("a control nobody declared")
    panel.setup_tabs.widget(0).layout().addRow("Smuggled:", smuggled)

    with pytest.raises(AssertionError, match="not in CONTROLS"):
        test_every_control_on_the_panel_is_on_the_table(panel)


@pytest.mark.parametrize("factory_name", ["QSlider", "QPlainTextEdit", "QListWidget"])
def test_the_guard_notices_a_control_kind_it_has_never_seen(panel, factory_name):
    """The teeth that were missing, one kind at a time.

    The previous version of this test smuggled a ``QDoubleSpinBox`` -- a
    kind that *was* on the old five-class allowlist -- so it passed while
    proving nothing about anything else. These three are not on it: a
    ``QAbstractSlider``, a plain-text editor and an item view. All three
    were added to the Paper tab by hand against the old guard and the
    suite stayed at 2217 passed, byte-identical to the baseline.

    A guard against a list maintained in two places must not itself be a
    list maintained in two places.
    """
    from PySide6 import QtWidgets

    smuggled = getattr(QtWidgets, factory_name)(panel.widget)
    smuggled.setToolTip(f"an undeclared {factory_name}")
    panel.setup_tabs.widget(0).layout().addRow("Smuggled:", smuggled)

    with pytest.raises(AssertionError, match="not in CONTROLS"):
        test_every_control_on_the_panel_is_on_the_table(panel)


def test_every_control_is_reachable_and_carries_its_own_help(panel):
    """A row that is never placed is a control that does not exist."""
    for spec in CONTROLS:
        widget = panel.controls[spec.name]
        assert widget.parent() is not None, spec.name
        assert getattr(panel, spec.name) is widget, spec.name


def test_each_row_declares_what_its_kind_needs(panel):
    """The table is data, so it can be wrong in ways code cannot.

    A length with no cap would accept any number; a keyed combo with no
    choices would have nothing to select; a control that writes the
    project through neither `apply` nor a named `handler` would be inert.
    """
    for spec in CONTROLS:
        if spec.kind == "length":
            assert spec.cap_pt > 0, spec.name
            assert spec.decimals >= 0, spec.name
            assert spec.step_pt > 0, spec.name
        if spec.kind == "count":
            assert spec.maximum > spec.minimum, spec.name
        if spec.kind == "choice":
            choices = spec.choices() if callable(spec.choices) else spec.choices
            assert choices, spec.name
        if spec.is_value:
            assert spec.apply is not None or spec.handler is not None, spec.name
        if spec.field is not None:
            assert spec.field in LAYOUT_FIELDS, (spec.name, spec.field)
        if spec.error_prefix:
            assert spec.handler is not None or spec.apply is not None, spec.name


# -- B11, generalised across the table ------------------------------------


@pytest.mark.parametrize("unit", ["pt", "in", "mm", "cm"])
def test_every_length_on_the_table_reconverts_on_a_unit_change(unit, panel):
    """B11 was a length that was not on the unit-change list.

    Rather than naming trim and the eight crop boxes -- the ones that were
    missing that time -- this walks the table, so a length added tomorrow
    is checked tomorrow.
    """
    loaded = replace(
        panel.state.project.layout,
        gutter_pt=17.77, margin_top_pt=9.13, margin_bottom_pt=3.3,
        margin_outer_pt=25.4, paper_thickness_pt=0.37, trim_pt=6.5,
        crop_odd_pt=(1.25, 2.5, 3.75, 5.0),
        crop_even_pt=(5.0, 3.75, 2.5, 1.25),
    )
    panel.state._project = replace(panel.state.project, layout=loaded)
    panel.refresh_from_project()

    panel.unit_combo.setCurrentText(unit)

    for spec in CONTROLS:
        if spec.kind != "length":
            continue
        box = panel.controls[spec.name]
        points = spec.displayed(panel.state.project.layout)
        assert box.value() == pytest.approx(
            round(from_points(points, unit), box.decimals())
        ), f"{spec.name} was not reconverted into {unit}"
        # And the model is untouched: a unit is notation, not a change.
        assert spec.displayed(panel.state.project.layout) == points


def test_a_unit_change_leaves_every_field_of_the_document_alone(panel):
    """The whole layout, not the lengths somebody remembered to check."""
    before = panel.state.project.layout

    for unit in ("mm", "pt", "cm", "in"):
        panel.unit_combo.setCurrentText(unit)

    for field in LAYOUT_FIELDS:
        assert getattr(panel.state.project.layout, field) == getattr(before, field), (
            f"switching units changed {field}"
        )


# -- B12, generalised across the table ------------------------------------


def test_a_refresh_writes_nothing_back_through_any_control(panel):
    """B12 was a control whose signals the refresh forgot to block.

    The undo stack is the assertion that matters: a control left unblocked
    writes back the value the refresh was about to set, so the document
    still looks right while the user's history quietly fills with edits
    they never made and their redo is thrown away.
    """
    panel.state.mutate(lambda project: replace(
        project, layout=replace(project.layout, gutter_pt=24.0)
    ))
    panel.state.undo()
    assert panel.state.can_redo, "precondition: there is an undone future"

    undo_before = len(panel.state._undo_stack)
    redo_before = len(panel.state._redo_stack)
    loaded = replace(
        panel.state.project.layout,
        trim_pt=9.0, paper_thickness_pt=0.42, sewing_stations=4,
        sewing_station_positions_pt=(36.0, 144.0),
        crop_odd_pt=(1.0, 2.0, 3.0, 4.0), crop_even_pt=(4.0, 3.0, 2.0, 1.0),
    )
    panel.state._project = replace(panel.state.project, layout=loaded)

    panel.refresh_from_project()

    for field in LAYOUT_FIELDS:
        assert getattr(panel.state.project.layout, field) == getattr(loaded, field), (
            f"the refresh overwrote {field}"
        )
    assert len(panel.state._undo_stack) == undo_before
    assert len(panel.state._redo_stack) == redo_before
    assert panel.state.can_redo


def test_every_control_the_table_can_read_follows_a_loaded_project(panel):
    """The other half of a refresh: the panel must state what is true."""
    loaded = replace(
        panel.state.project.layout,
        gutter_pt=36.0, binding_edge="right", start_on_recto=False,
        landscape_policy="scale", margin_top_pt=12.0, margin_bottom_pt=13.0,
        margin_outer_pt=14.0, slack_to="split", margins_linked=False,
        sheets_per_signature=4, grain="short", paper_thickness_pt=0.2,
        sewing_stations=3, blank_mode="balanced", trim_pt=9.0,
        crop_odd_pt=(1.0, 2.0, 3.0, 4.0),
    )
    panel.state._project = replace(panel.state.project, layout=loaded)

    panel.refresh_from_project()

    for spec in CONTROLS:
        if spec.refresh is not None or not spec.is_value:
            continue
        expected = spec.displayed(loaded)
        if expected is None:
            continue
        widget = panel.controls[spec.name]
        if spec.kind == "length":
            assert widget.points() == pytest.approx(expected), spec.name
        elif spec.kind == "count":
            assert widget.value() == expected, spec.name
        elif spec.kind == "flag":
            assert widget.isChecked() == expected, spec.name
        elif spec.kind == "choice":
            if spec.by_key:
                keys = panel._choice_keys[spec.name]
                assert keys[widget.currentIndex()] == expected, spec.name
            else:
                assert widget.currentText() == expected, spec.name
        elif spec.kind == "text":
            assert widget.text() == expected, spec.name


# -- B29, closed ----------------------------------------------------------


def test_a_caliper_survives_being_displayed_in_points(panel):
    """B29. A caliper is 0.2-0.5pt and the box showed 0 decimals in `pt`,
    so it read as `0` -- and the next nudge wrote `0` into the project,
    losing the measurement the binder took with a micrometer."""
    panel.state._project = replace(
        panel.state.project,
        layout=replace(panel.state.project.layout, paper_thickness_pt=0.3),
    )
    panel.refresh_from_project()

    panel.unit_combo.setCurrentText("pt")

    assert panel.paper_thickness_spinbox.value() == pytest.approx(0.3)
    # And committing what is on screen keeps it, which is the half that
    # actually destroyed data.
    panel.paper_thickness_spinbox.setValue(0.0)
    panel.paper_thickness_spinbox.setValue(0.3)
    assert panel.state.project.layout.paper_thickness_pt == pytest.approx(0.3)


def test_a_typed_thickness_refreshes_the_gathering_advice(panel):
    """B29's second clause. The advice is computed from the caliper, so
    advice that does not move when the caliper does is stale advice beside
    a button that applies it."""
    panel.paper_stock_combo.setCurrentIndex(1)
    before = panel.suggestion_label.text()
    assert before, "precondition: there is advice to go stale"

    panel.paper_thickness_spinbox.setValue(
        panel.paper_thickness_spinbox.value() * 8
    )

    assert panel.suggestion_label.text() != before


def test_a_typed_thickness_stops_claiming_a_named_stock(panel):
    """B29's third clause. Pick 80gsm copier, then type your own caliper:
    the dropdown went on naming a paper whose thickness is no longer in
    the box."""
    panel.paper_stock_combo.setCurrentIndex(1)
    assert panel.paper_stock_combo.currentText() != CUSTOM_STOCK_LABEL

    panel.paper_thickness_spinbox.setValue(
        panel.paper_thickness_spinbox.value() + 0.005
    )

    assert panel.paper_stock_combo.currentText() == CUSTOM_STOCK_LABEL


def test_choosing_a_stock_still_names_that_stock(panel):
    """The other side of it: adopting a preset's caliper must not read as
    typing one, or the dropdown would reset itself the instant it was
    used."""
    panel.paper_stock_combo.setCurrentIndex(1)

    assert panel.paper_stock_combo.currentText() != CUSTOM_STOCK_LABEL
    assert panel.state.project.layout.paper_thickness_pt > 0
