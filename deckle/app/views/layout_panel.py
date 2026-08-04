"""LayoutPanel: live controls for gutter, binding edge, scale mode, etc.

Changing any layout setting on this panel does two things, immediately and
synchronously: it replaces ``AppState.project.layout`` via ``AppState.mutate``
(so it participates in undo/autosave like every other project change), and
it re-runs ``GutterShiftStrategy.impose`` over the *whole* document to
produce a fresh ``SheetPlan`` -- arithmetic only, no rasterization. The
panel then emits that plan via ``layout_changed`` so a listening
``PreviewView`` can re-render just the sheet currently on screen; see
``deckle/app/views/preview_view.py``.

There is deliberately no scale-mode control: content is always fitted to
the content box, which fills the page height whenever geometry allows.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from deckle.app.state import AppState
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, Project, SheetPlan

BINDING_EDGES: tuple[str, ...] = ("left", "right")

LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale", "letterbox")

# -- pure layout-settings mutators, each routed through AppState.mutate ----


def set_gutter_pt(project: Project, gutter_pt: float) -> Project:
    return replace(project, layout=replace(project.layout, gutter_pt=gutter_pt))


#: The three editable margin fields. The fourth page edge is the spine,
#: whose margin is ``gutter_pt`` -- edited separately since it behaves
#: differently (it mirrors between recto and verso).
MARGIN_FIELDS: tuple[str, ...] = ("margin_top_pt", "margin_bottom_pt", "margin_outer_pt")


def set_margin(project: Project, field: str, points: float, *, linked: bool = False) -> Project:
    """Set one margin, or all three when ``linked``."""
    updates = {f: points for f in MARGIN_FIELDS} if linked else {field: points}
    return replace(project, layout=replace(project.layout, **updates))


def set_margins_linked(project: Project, linked: bool) -> Project:
    return replace(project, layout=replace(project.layout, margins_linked=linked))


#: Display units for lengths. Values are points-per-unit, so the stored
#: model stays in PDF points and only the UI converts.
LENGTH_UNITS: dict[str, float] = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}


def to_points(value: float, unit: str) -> float:
    """Convert a displayed value in ``unit`` to PDF points."""
    return value * LENGTH_UNITS[unit]


def from_points(points: float, unit: str) -> float:
    """Convert PDF points to a displayed value in ``unit``."""
    return points / LENGTH_UNITS[unit]


def imageable_inset_pt(imageable_area_pt: tuple[float, float, float, float]) -> float:
    """The largest edge inset of a printer's imageable area, in points.

    ``imageable_area_pt`` is ``(left, top, right, bottom)`` **margins** from
    the paper edges -- the same convention ``PrinterProfile``,
    ``QtPrintBackend._paint_rendered_page`` and
    ``preview_view.imageable_rect_pt`` all use. It is *not* an
    ``(x0, y0, x1, y1)`` rect; reading it as one yields a ~600pt "inset" and
    a nonsense margin.

    Used by "Use printer margins": a margin at least this large clears the
    non-printable border on every edge, which is the condition that stops
    ``clipped_by_imageable_area`` firing.
    """
    return max(*imageable_area_pt, 0.0)


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
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QPushButton,
        QRadioButton,
        QWidget,
    )

    return (
        QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
        QPushButton, QRadioButton, QWidget,
    )


class LayoutPanel:
    """Form of layout controls, wired straight to ``AppState``.

    Every control change routes through ``apply_layout_change`` above and
    emits ``layout_changed`` with the freshly recomputed ``SheetPlan`` --
    it never rasterizes anything itself.
    """

    def __init__(self, state: AppState, parent=None, profile=None) -> None:
        # `profile` supplies the printer's imageable inset for the
        # "Use printer margins" button. Optional so the panel stays
        # constructible without a printer.
        self.profile = profile
        QObject, Signal = _qt_core()
        (
            QButtonGroup,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFormLayout,
            QPushButton,
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

        # Lengths are stored in points but entered in whatever unit suits the
        # job -- inches for a US letter binder, cm for metric stock.
        self.unit_combo = QComboBox(self.widget)
        self.unit_combo.addItems(["pt", "in", "cm", "mm"])
        self.unit_combo.setCurrentText("in")
        self._unit = "in"
        form.addRow("Units:", self.unit_combo)

        self.gutter_spinbox = QDoubleSpinBox(self.widget)
        self.gutter_spinbox.setDecimals(3)
        self.gutter_spinbox.setSingleStep(0.125)
        self.gutter_spinbox.setRange(0.0, from_points(288.0, self._unit))
        self.gutter_spinbox.setValue(from_points(state.project.layout.gutter_pt, self._unit))
        form.addRow("Gutter:", self.gutter_spinbox)

        self.link_margins_check = QCheckBox("Link margins (one value for all)", self.widget)
        self.link_margins_check.setChecked(state.project.layout.margins_linked)
        form.addRow("", self.link_margins_check)

        # Three editable margins; the fourth edge is the spine, whose margin
        # is the gutter above.
        self.margin_spinboxes: dict[str, object] = {}
        for field, label in (
            ("margin_top_pt", "Margin top (head):"),
            ("margin_bottom_pt", "Margin bottom (tail):"),
            ("margin_outer_pt", "Margin outer (fore-edge):"),
        ):
            box = QDoubleSpinBox(self.widget)
            box.setDecimals(3)
            box.setSingleStep(0.125)
            box.setRange(0.0, from_points(216.0, self._unit))
            box.setValue(from_points(getattr(state.project.layout, field), self._unit))
            form.addRow(label, box)
            self.margin_spinboxes[field] = box
        self._sync_margin_enabled()

        self.use_printer_margins_button = QPushButton("Use printer margins", self.widget)
        self.use_printer_margins_button.setToolTip(
            "Set the margin to the printer's non-printable inset, so content "
            "clears the dead border on every edge."
        )
        form.addRow("", self.use_printer_margins_button)

        self.binding_edge_combo = QComboBox(self.widget)
        self.binding_edge_combo.addItems(list(BINDING_EDGES))
        self.binding_edge_combo.setCurrentText(state.project.layout.binding_edge)
        form.addRow("Binding edge:", self.binding_edge_combo)

        self.landscape_policy_combo = QComboBox(self.widget)
        self.landscape_policy_combo.addItems(list(LANDSCAPE_POLICIES))
        self.landscape_policy_combo.setCurrentText(state.project.layout.landscape_policy)
        form.addRow("Landscape policy:", self.landscape_policy_combo)

        self.gutter_spinbox.valueChanged.connect(self._on_gutter_changed)
        for field, box in self.margin_spinboxes.items():
            box.valueChanged.connect(
                lambda value, f=field: self._on_margin_changed(f, value)
            )
        self.link_margins_check.toggled.connect(self._on_link_margins_toggled)
        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)
        self.use_printer_margins_button.clicked.connect(self._on_use_printer_margins)
        self.binding_edge_combo.currentTextChanged.connect(self._on_binding_edge_changed)
        self.landscape_policy_combo.currentTextChanged.connect(self._on_landscape_policy_changed)

    def _on_gutter_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(self.state, lambda project: set_gutter_pt(project, points))
        self.layout_changed.emit(plan)

    def _sync_margin_enabled(self) -> None:
        """When linked, only the head box is editable -- the other two mirror
        it. Disabling rather than hiding keeps the values visible, so you can
        see what linking did before unlinking again."""
        linked = self.link_margins_check.isChecked()
        for field, box in self.margin_spinboxes.items():
            box.setEnabled(not linked or field == "margin_top_pt")

    def _on_link_margins_toggled(self, linked: bool) -> None:
        self._sync_margin_enabled()
        plan = apply_layout_change(
            self.state, lambda project: set_margins_linked(project, linked)
        )
        if linked:
            # Adopt the head margin for all three, so linking is a visible,
            # predictable action rather than a silent mode change.
            head = self.margin_spinboxes["margin_top_pt"].value()
            self._on_margin_changed("margin_top_pt", head)
            return
        self.layout_changed.emit(plan)

    def _on_margin_changed(self, field: str, value: float) -> None:
        points = to_points(value, self._unit)
        linked = self.link_margins_check.isChecked()
        plan = apply_layout_change(
            self.state, lambda project: set_margin(project, field, points, linked=linked)
        )
        if linked:
            self._refresh_margin_boxes()
        self.layout_changed.emit(plan)

    def _refresh_margin_boxes(self) -> None:
        """Re-display all three margins from the model without re-emitting."""
        for field, box in self.margin_spinboxes.items():
            box.blockSignals(True)
            box.setValue(from_points(getattr(self.state.project.layout, field), self._unit))
            box.blockSignals(False)

    def _on_unit_changed(self, unit: str) -> None:
        """Re-display the same physical lengths in a new unit.

        The stored model is always points, so switching units must not
        change the layout -- only how it reads. Signals are blocked while
        the displayed numbers are rewritten, otherwise the spinboxes would
        emit and re-apply their pre-conversion values as if the user had
        typed them.
        """
        layout = self.state.project.layout
        boxes = [(self.gutter_spinbox, layout.gutter_pt, 288.0)]
        boxes += [
            (self.margin_spinboxes[f], getattr(layout, f), 216.0) for f in MARGIN_FIELDS
        ]
        self._unit = unit
        for box, points, cap_pt in boxes:
            box.blockSignals(True)
            box.setRange(0.0, from_points(cap_pt, unit))
            box.setDecimals(0 if unit == "pt" else 3)
            box.setSingleStep(1.0 if unit in ("pt", "mm") else 0.125)
            box.setValue(from_points(points, unit))
            box.blockSignals(False)

    def _on_use_printer_margins(self) -> None:
        """Set the margin to the active printer's non-printable inset."""
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        # Set every margin, regardless of link state -- the printer's dead
        # border applies to all four edges, so a partial application would
        # leave some edge still unprintable.
        self.margin_spinboxes["margin_top_pt"].setValue(from_points(inset, self._unit))
        if not self.link_margins_check.isChecked():
            for field in ("margin_bottom_pt", "margin_outer_pt"):
                self.margin_spinboxes[field].setValue(from_points(inset, self._unit))

    def _on_binding_edge_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_binding_edge(project, value))
        self.layout_changed.emit(plan)

    def _on_landscape_policy_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_landscape_policy(project, value))
        self.layout_changed.emit(plan)
