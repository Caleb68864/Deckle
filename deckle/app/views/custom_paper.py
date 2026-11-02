"""The app's half of ``--paper WxH[unit]``: type a sheet size.

Stores points like everything else in the core, and takes its bounds from
:mod:`deckle.core.paper`, so the dialog and the flag refuse the same sizes for
the same stated reason. There is no orientation control: the panel's own
Orientation combo owns that, and a second one would be two controls for one
decision.
"""

from __future__ import annotations

from deckle.core.paper import MAX_PAPER_PT, MIN_PAPER_PT, paper_size_problem


class CustomPaperDialog:
    """Type a sheet size in whatever unit suits the stock.

    :param paper: the size to start from, in points.
    :param unit: the unit to start in, so the numbers on screen match every
        other length in the panel.
    :param parent: the parent ``QWidget``, or ``None``.
    :ivar widget: the ``QDialog`` to show. This class is not itself a widget.
    :ivar paper_pt: the accepted size in points, or ``None`` if cancelled.
    """

    def __init__(self, paper: tuple[float, float], *, unit: str = "in", parent=None) -> None:
        from PySide6.QtWidgets import (
            QComboBox,
            QDialog,
            QDoubleSpinBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
        )

        from deckle.app.views.layout_panel import LENGTH_UNITS, from_points

        self.paper_pt: tuple[float, float] | None = None
        self.widget = QDialog(parent)
        self.widget.setWindowTitle("Custom paper size")
        form = QFormLayout(self.widget)

        self.unit_combo = QComboBox(self.widget)
        self.unit_combo.addItems(list(LENGTH_UNITS))
        self.unit_combo.setCurrentText(unit)
        form.addRow("Units:", self.unit_combo)

        self.width_spin = QDoubleSpinBox(self.widget)
        self.height_spin = QDoubleSpinBox(self.widget)
        self._unit = unit
        for spin, points, label in (
            (self.width_spin, paper[0], "Width:"),
            (self.height_spin, paper[1], "Height:"),
        ):
            spin.setDecimals(3)
            spin.setRange(from_points(MIN_PAPER_PT, unit), from_points(MAX_PAPER_PT, unit))
            spin.setValue(from_points(points, unit))
            form.addRow(label, spin)

        self.status_label = QLabel("", self.widget)
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)

        buttons = QHBoxLayout()
        self.ok_button = QPushButton("OK", self.widget)
        self.ok_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel", self.widget)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)
        form.addRow(buttons)

        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.widget.reject)

    def _on_unit_changed(self, unit: str) -> None:
        """Show the same physical size in ``unit``: values and ranges both.

        :param unit: the unit just chosen.
        :returns: nothing.
        """
        from deckle.app.views.layout_panel import from_points, to_points

        for spin in (self.width_spin, self.height_spin):
            points = to_points(spin.value(), self._unit)
            blocked = spin.blockSignals(True)
            try:
                spin.setRange(from_points(MIN_PAPER_PT, unit), from_points(MAX_PAPER_PT, unit))
                spin.setValue(from_points(points, unit))
            finally:
                spin.blockSignals(blocked)
        self._unit = unit

    def accept(self) -> None:
        """Take the size, unless no PDF page could have it.

        The spin-box ranges should make that unreachable, but rounding to
        three decimals can land just outside, and the ranges are a second
        expression of a rule that must not be able to disagree with itself.

        :returns: nothing. The dialog stays open on a refusal.
        """
        from deckle.app.views.layout_panel import to_points

        paper = (
            to_points(self.width_spin.value(), self._unit),
            to_points(self.height_spin.value(), self._unit),
        )
        problem = paper_size_problem(paper)
        if problem is not None:
            self.status_label.setText(problem)
            return
        self.paper_pt = paper
        self.widget.accept()
