"""Set a printer's profile by hand, from the print dialog.

A profile is the most expensive data Deckle holds. This edits stored numbers
and never measures anything: the calibration wizard reads the machine, and this
is where a registration offset measured with a ruler gets typed in.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Callable

from deckle.core.diagnostics import log_exception
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile

#: The widest back offset the form takes, each way, in points: one inch.
MAX_BACK_OFFSET_PT = 72.0

#: The widest printable border the form takes, in points: two inches.
MAX_BORDER_PT = 144.0

#: The smallest sheet the border check assumes: US Letter.
SMALLEST_SHEET_PT = (612.0, 792.0)

#: In the order of ``imageable_area_pt``, which is four **margins** from the
#: paper edges and not an ``(x0, y0, x1, y1)`` rect.
BORDER_EDGES = ("left", "top", "right", "bottom")

_BEHAVIOUR_FIELDS = ("flip_axis", "output_face", "feed_edge", "reverse_stack")


def profile_from_fields(
    *,
    base: PrinterProfile,
    flip_axis: str,
    output_face: str,
    feed_edge: str,
    reverse_stack: bool,
    back_offset_pt: tuple[float, float],
    imageable_area_pt: tuple[float, float, float, float],
    calibrated_at: str,
) -> PrinterProfile:
    """A profile from edited values.

    ``calibration_version`` is the only thing distinguishing a measured profile
    from a typed one, so typing over any of the four behaviours measured by the
    wizard drops it to ``0``, and ``calibrated_at`` becomes the time of the
    edit. Typing only a border or an offset keeps both: the wizard measures
    neither of them, and the offset is expected to be typed in afterwards. A
    profile that was never measured stays at ``0``.

    :param base: the profile the form was loaded from.
    :param flip_axis: which edge the sheet turns about.
    :param output_face: how sheets land in the tray.
    :param feed_edge: which edge enters first.
    :param reverse_stack: whether the back pass runs in reverse.
    :param back_offset_pt: ``(x, y)``, signed.
    :param imageable_area_pt: ``(left, top, right, bottom)`` margins.
    :param calibrated_at: the timestamp to record if this counts as a new edit.
    :returns: the new profile.
    """
    behaviour = {
        "flip_axis": flip_axis,
        "output_face": output_face,
        "feed_edge": feed_edge,
        "reverse_stack": reverse_stack,
    }
    measured = base.calibration_version >= 1 and all(
        getattr(base, field) == behaviour[field] for field in _BEHAVIOUR_FIELDS
    )
    return dataclasses.replace(
        base,
        **behaviour,
        imageable_area_pt=imageable_area_pt,
        back_offset_x_pt=back_offset_pt[0],
        back_offset_y_pt=back_offset_pt[1],
        calibrated_at=base.calibrated_at if measured else calibrated_at,
        calibration_version=base.calibration_version if measured else 0,
    )


def _qt_widgets():
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
    )

    return QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton


class ProfileEditorDialog:
    """One form over a printer's profile.

    :param printer_name: the printer whose profile this is.
    :param parent: the parent ``QWidget``, or ``None``.
    :param start_from: the profile to load, normally what the print dialog has
        selected for this printer.
    :param builtin_presets: what "Start from" offers, or ``None`` for
        ``BUILTIN_PRESETS``.
    :param saver: ``(profile, name) -> None``.
    :param now: ``() -> str``, the timestamp to record.
    :ivar widget: the ``QDialog`` to show. This class is not itself a widget.
    :ivar saved_profile: what was saved, or ``None`` if cancelled.
    """

    def __init__(
        self,
        printer_name: str,
        parent=None,
        *,
        start_from: PrinterProfile | None = None,
        builtin_presets: dict[str, PrinterProfile] | None = None,
        saver: Callable[[PrinterProfile, str], None] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        (
            QCheckBox,
            QComboBox,
            QDialog,
            QDoubleSpinBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
        ) = _qt_widgets()

        self._printer_name = printer_name
        self._presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
        self._save = saver or (lambda profile, name: profile.save(name))
        self._now = now or (
            lambda: datetime.datetime.now().isoformat(timespec="seconds")
        )
        self.saved_profile: PrinterProfile | None = None
        self._base = start_from if start_from is not None else next(iter(self._presets.values()))

        self.widget = QDialog(parent)
        self.widget.setWindowTitle("Printer profile")
        form = QFormLayout(self.widget)
        form.addRow("Printer:", QLabel(printer_name or "(no printer selected)", self.widget))

        self.preset_combo = QComboBox(self.widget)
        self.preset_combo.addItem("(keep these values)", None)
        for name in self._presets:
            self.preset_combo.addItem(name, name)
        form.addRow("Start from:", self.preset_combo)

        self.flip_axis_combo = QComboBox(self.widget)
        self.flip_axis_combo.addItems(["long", "short"])
        self.flip_axis_combo.setToolTip("Which edge you turn each sheet about when you reload it.")
        form.addRow("Flip edge:", self.flip_axis_combo)

        self.output_face_combo = QComboBox(self.widget)
        self.output_face_combo.addItems(["up", "down"])
        self.output_face_combo.setToolTip(
            "Whether printed sheets land face up or face down in the output tray."
        )
        form.addRow("Sheets land:", self.output_face_combo)

        self.feed_edge_combo = QComboBox(self.widget)
        self.feed_edge_combo.addItems(["top", "bottom"])
        self.feed_edge_combo.setToolTip("Which edge of the sheet enters the printer first.")
        form.addRow("Feed edge:", self.feed_edge_combo)

        self.reverse_stack_check = QCheckBox("Reverse the stack before reloading", self.widget)
        self.reverse_stack_check.setToolTip(
            "Tick this if the printed stack comes out in reverse order, so the "
            "back pass must be fed in reverse to match."
        )
        form.addRow(self.reverse_stack_check)

        def length(low: float, high: float, tooltip: str):
            spin = QDoubleSpinBox(self.widget)
            spin.setDecimals(3)
            spin.setRange(low, high)
            spin.setSuffix(" pt")
            spin.setToolTip(tooltip)
            return spin

        offset_tip = (
            "How far to move back-side content so it lands behind the front. "
            "This is the correction, not the error, and it is signed."
        )
        self.back_offset_x_spin = length(-MAX_BACK_OFFSET_PT, MAX_BACK_OFFSET_PT, offset_tip)
        self.back_offset_y_spin = length(-MAX_BACK_OFFSET_PT, MAX_BACK_OFFSET_PT, offset_tip)
        form.addRow("Back offset X:", self.back_offset_x_spin)
        form.addRow("Back offset Y:", self.back_offset_y_spin)

        self.imageable_spins = {}
        for edge in BORDER_EDGES:
            spin = length(
                0.0,
                MAX_BORDER_PT,
                "How far in from that paper edge your printer can actually print. "
                "Content outside it is clipped by the printer.",
            )
            self.imageable_spins[edge] = spin
            form.addRow(f"Imageable {edge}:", spin)

        self.status_label = QLabel("", self.widget)
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save", self.widget)
        self.save_button.setDefault(True)
        self.save_button.setEnabled(bool(printer_name))
        self.cancel_button = QPushButton("Cancel", self.widget)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        form.addRow(buttons)

        self._load_into_form(self._base)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.widget.reject)

    def _load_into_form(self, profile: PrinterProfile) -> None:
        """Set every control from ``profile``.

        :param profile: the values to show.
        :returns: nothing.
        """
        self.flip_axis_combo.setCurrentText(profile.flip_axis)
        self.output_face_combo.setCurrentText(profile.output_face)
        self.feed_edge_combo.setCurrentText(profile.feed_edge)
        self.reverse_stack_check.setChecked(profile.reverse_stack)
        self.back_offset_x_spin.setValue(profile.back_offset_x_pt)
        self.back_offset_y_spin.setValue(profile.back_offset_y_pt)
        for edge, value in zip(BORDER_EDGES, profile.imageable_area_pt):
            self.imageable_spins[edge].setValue(value)

    def _on_preset_changed(self, _index: int) -> None:
        """Load a preset's values, or put the original ones back.

        :param _index: the combo's new index, read back as its data.
        :returns: nothing. Nothing is written until Save.
        """
        name = self.preset_combo.currentData()
        self._load_into_form(self._base if name is None else self._presets[name])
        self.status_label.setText(
            "" if name is None else f"Loaded {name} -- nothing is saved until you press Save."
        )

    def save(self) -> bool:
        """Write the edited profile.

        :returns: whether it was written. A refusal or a failed write leaves
            the dialog open and whatever was stored untouched.
        """
        if not self._printer_name:
            self.status_label.setText("Choose a printer before editing its profile.")
            return False
        spins = self.imageable_spins
        if (
            spins["left"].value() + spins["right"].value() >= SMALLEST_SHEET_PT[0]
            or spins["top"].value() + spins["bottom"].value() >= SMALLEST_SHEET_PT[1]
        ):
            self.status_label.setText(
                "Those imageable margins leave no printable area on a Letter sheet."
            )
            return False
        profile = profile_from_fields(
            base=self._base,
            flip_axis=self.flip_axis_combo.currentText(),
            output_face=self.output_face_combo.currentText(),
            feed_edge=self.feed_edge_combo.currentText(),
            reverse_stack=self.reverse_stack_check.isChecked(),
            back_offset_pt=(self.back_offset_x_spin.value(), self.back_offset_y_spin.value()),
            imageable_area_pt=tuple(spins[edge].value() for edge in BORDER_EDGES),
            calibrated_at=self._now(),
        )
        try:
            self._save(profile, self._printer_name)
        except OSError as exc:
            log_exception("profile_save_failed", exc, printer=self._printer_name)
            self.status_label.setText(
                f"Could not save the profile for {self._printer_name!r}: {exc}"
            )
            return False
        self.saved_profile = profile
        self.widget.accept()
        return True
