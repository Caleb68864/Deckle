"""LayoutPanel: live controls for gutter, binding edge, scale mode, etc.

Changing any layout setting on this panel does two things, immediately and
synchronously: it replaces ``AppState.project.layout`` via ``AppState.mutate``
(so it participates in undo/autosave like every other project change), and
it re-runs ``GutterShiftStrategy.impose`` over the *whole* document to
produce a fresh ``SheetPlan`` -- arithmetic only, no rasterization. The
panel then emits that plan via ``layout_changed`` so a listening
``PreviewView`` can re-render just the sheet currently on screen; see
``deckle/app/views/preview_view.py``.

``LayoutPanel`` exposes both entries in ``SCALE_MODES`` with ``fit_height``
preselected, matching ``LayoutSettings.scale_mode``'s own default.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from deckle.app.state import AppState
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, Project, SheetPlan

# The only two scale modes GutterShiftStrategy understands in the MVP.
# Order matters: index 0 is what the panel preselects.
SCALE_MODES: tuple[str, ...] = ("fit_height", "fixed_gutter")

BINDING_EDGES: tuple[str, ...] = ("left", "right")

LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale", "letterbox")

assert SCALE_MODES[0] == LayoutSettings.__dataclass_fields__["scale_mode"].default


# -- pure layout-settings mutators, each routed through AppState.mutate ----


def set_scale_mode(project: Project, scale_mode: Literal["fit_height", "fixed_gutter"]) -> Project:
    return replace(project, layout=replace(project.layout, scale_mode=scale_mode))


def set_gutter_pt(project: Project, gutter_pt: float) -> Project:
    return replace(project, layout=replace(project.layout, gutter_pt=gutter_pt))


def set_binding_edge(project: Project, binding_edge: Literal["left", "right"]) -> Project:
    return replace(project, layout=replace(project.layout, binding_edge=binding_edge))


def set_landscape_policy(
    project: Project, landscape_policy: Literal["rotate", "scale", "letterbox"]
) -> Project:
    return replace(project, layout=replace(project.layout, landscape_policy=landscape_policy))


def set_start_on_recto(project: Project, start_on_recto: bool) -> Project:
    return replace(project, layout=replace(project.layout, start_on_recto=start_on_recto))


def recompute_plan(project: Project) -> SheetPlan:
    """Re-run the imposer over every (non-skipped) page. Arithmetic only."""
    return GutterShiftStrategy().impose(project.pages, project.layout)


def apply_layout_change(state: AppState, mutator) -> SheetPlan:
    """Apply ``mutator`` (one of the ``set_*`` functions above, partially
    applied to a new value) through ``AppState.mutate`` and immediately
    recompute the whole-document ``SheetPlan``.

    Returns the fresh plan so a caller (the Qt panel below, or a test) can
    hand it to a preview without any rendering happening here.
    """
    state.mutate(mutator)
    return recompute_plan(state.project)


# -- Qt wiring -----------------------------------------------------------
# Imported lazily so this module -- and every pure function above -- stays
# importable without PySide6/a display, matching arrange_view.py/import_view.py.


def _qt_core():
    from PySide6.QtCore import QObject, Signal

    return QObject, Signal


def _qt_widgets():
    from PySide6.QtWidgets import (
        QButtonGroup,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QRadioButton,
        QWidget,
    )

    return QButtonGroup, QComboBox, QDoubleSpinBox, QFormLayout, QRadioButton, QWidget


class LayoutPanel:
    """Form of layout controls, wired straight to ``AppState``.

    Every control change routes through ``apply_layout_change`` above and
    emits ``layout_changed`` with the freshly recomputed ``SheetPlan`` --
    it never rasterizes anything itself.
    """

    def __init__(self, state: AppState, parent=None) -> None:
        QObject, Signal = _qt_core()
        (
            QButtonGroup,
            QComboBox,
            QDoubleSpinBox,
            QFormLayout,
            QRadioButton,
            QWidget,
        ) = _qt_widgets()

        class _Signals(QObject):
            layout_changed = Signal(object)  # SheetPlan

        self._signals = _Signals()
        self.layout_changed = self._signals.layout_changed

        self.state = state
        self.widget = QWidget(parent)
        form = QFormLayout(self.widget)

        # -- scale mode: both options offered, fit_height preselected -----
        self.fit_height_radio = QRadioButton("Fit height", self.widget)
        self.fixed_gutter_radio = QRadioButton("Fixed gutter", self.widget)
        self.scale_mode_group = QButtonGroup(self.widget)
        self.scale_mode_group.addButton(self.fit_height_radio)
        self.scale_mode_group.addButton(self.fixed_gutter_radio)
        current_mode = state.project.layout.scale_mode
        self.fit_height_radio.setChecked(current_mode == "fit_height")
        self.fixed_gutter_radio.setChecked(current_mode == "fixed_gutter")
        form.addRow("Scale mode:", self.fit_height_radio)
        form.addRow("", self.fixed_gutter_radio)

        self.gutter_spinbox = QDoubleSpinBox(self.widget)
        self.gutter_spinbox.setRange(0.0, 288.0)
        self.gutter_spinbox.setValue(state.project.layout.gutter_pt)
        form.addRow("Gutter (pt):", self.gutter_spinbox)

        self.binding_edge_combo = QComboBox(self.widget)
        self.binding_edge_combo.addItems(list(BINDING_EDGES))
        self.binding_edge_combo.setCurrentText(state.project.layout.binding_edge)
        form.addRow("Binding edge:", self.binding_edge_combo)

        self.landscape_policy_combo = QComboBox(self.widget)
        self.landscape_policy_combo.addItems(list(LANDSCAPE_POLICIES))
        self.landscape_policy_combo.setCurrentText(state.project.layout.landscape_policy)
        form.addRow("Landscape policy:", self.landscape_policy_combo)

        self.fit_height_radio.toggled.connect(self._on_scale_mode_toggled)
        self.gutter_spinbox.valueChanged.connect(self._on_gutter_changed)
        self.binding_edge_combo.currentTextChanged.connect(self._on_binding_edge_changed)
        self.landscape_policy_combo.currentTextChanged.connect(self._on_landscape_policy_changed)

    def _on_scale_mode_toggled(self, checked: bool) -> None:
        mode = "fit_height" if checked else "fixed_gutter"
        plan = apply_layout_change(self.state, lambda project: set_scale_mode(project, mode))
        self.layout_changed.emit(plan)

    def _on_gutter_changed(self, value: float) -> None:
        plan = apply_layout_change(self.state, lambda project: set_gutter_pt(project, value))
        self.layout_changed.emit(plan)

    def _on_binding_edge_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_binding_edge(project, value))
        self.layout_changed.emit(plan)

    def _on_landscape_policy_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_landscape_policy(project, value))
        self.layout_changed.emit(plan)
