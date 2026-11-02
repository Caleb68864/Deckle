"""The calibration wizard's screens (F2, Phase 4).

:mod:`deckle.core.calibration_flow` decides what the screens say and what the
answers mean; this module only shows them and collects the answers, the same
split :mod:`deckle.app.views.print_dialog` has against
:mod:`deckle.core.print_session`. A plain ``QDialog`` over a stack of pages
rather than a ``QWizard``, so the whole flow can be driven headlessly.

Deckle does not submit the sheets itself. They are handed over as a document to
print from whatever the operator normally uses, for the reason
:mod:`deckle.core.calibration_sheet` gives: a target run through Deckle's own
print path would only report that Deckle agrees with itself.

The registration offsets are not asked for here. A saved calibration's offsets
are carried over unchanged rather than reset to zero, because they are measured
by hand and F3 is where measuring them belongs.
"""

from __future__ import annotations

import datetime
import os

from deckle.core.calibration_flow import (
    READING_QUESTIONS,
    SCREENS,
    UnexplainableResult,
    answers_for,
    reconcile,
)
from deckle.core.calibration_sheet import make_calibration_pdf
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.outputs import describe_write_failure
from deckle.core.paper import LETTER_PT
from deckle.core.paths import data_dir
from deckle.core.profiles import PrinterProfile

#: Where the sheets are written. Kept, so they can be reprinted later.
CALIBRATION_DIR = "calibration"

#: The largest printable border the wizard accepts, in points: two inches.
MAX_BORDER_PT = 144.0

#: The printer field's order in ``imageable_area_pt``.
BORDER_EDGES = ("Left", "Top", "Right", "Bottom")

_ORIENTATION_OF = {"read_portrait": "portrait", "read_landscape": "landscape"}
_PRINT_ORIENTATION_OF = {"print_portrait": "portrait", "print_landscape": "landscape"}


def _qt_widgets():
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )

    return (
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )


def _open_document(path: str) -> bool:
    """Open a written sheet in the desktop's PDF viewer.

    :param path: the PDF.
    :returns: whether the desktop opened it.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))


def _load_saved_profile(name: str) -> PrinterProfile | None:
    """The calibration already saved under ``name``, if a readable one exists.

    :param name: the printer.
    :returns: the profile, or ``None``.
    """
    try:
        return PrinterProfile.load(name)
    except (OSError, ValueError, KeyError, TypeError):
        return None


class CalibrationWizard:
    """Walk an operator through the sheets and save what they report.

    :param printer_names: the printers to offer. May be empty: a name can be
        typed, for a printer this machine cannot currently see.
    :param parent: the parent ``QWidget``, or ``None``.
    :param page_size: the paper to print the sheets for, upright.
    :param printer_name: the printer to preselect, or ``None``.
    :param sheet_writer: ``(path, page_size, landscape) -> None``.
    :param opener: ``(path) -> bool``, shows a written document.
    :param profile_loader: ``(name) -> PrinterProfile | None``.
    :param saver: ``(profile, name) -> None``.
    :param ask_overwrite: ``(name) -> bool``, whether to replace an existing
        calibration. Declining saves nothing.
    :param now: ``() -> str``, the timestamp to record.
    :param sheet_dir: where to write the sheets, or ``None`` for the default.
    :ivar widget: the ``QDialog`` to show. This class is not itself a widget.
    :ivar saved_profile: what was saved, or ``None``.
    :ivar saved_name: the printer it was saved under, or ``None``.
    """

    def __init__(
        self,
        printer_names=(),
        parent=None,
        *,
        page_size: tuple[float, float] = LETTER_PT,
        printer_name: str | None = None,
        sheet_writer=None,
        opener=_open_document,
        profile_loader=_load_saved_profile,
        saver=None,
        ask_overwrite=None,
        now=None,
        sheet_dir: str | None = None,
    ) -> None:
        (
            QComboBox,
            QDialog,
            QDoubleSpinBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            self._QMessageBox,
            QPushButton,
            QStackedWidget,
            QVBoxLayout,
            QWidget,
        ) = _qt_widgets()

        self._page_size = tuple(sorted(page_size))
        self._write_sheet = sheet_writer or self._default_write_sheet
        self._open = opener
        self._load_profile = profile_loader
        self._save = saver or (lambda profile, name: profile.save(name))
        self._ask_overwrite = ask_overwrite or self._default_ask_overwrite
        self._now = now or (
            lambda: datetime.datetime.now().isoformat(timespec="seconds")
        )
        self._sheet_dir = sheet_dir
        self.saved_profile: PrinterProfile | None = None
        self.saved_name: str | None = None
        #: ``{orientation: path}`` for the sheets written so far.
        self.sheet_paths: dict[str, str] = {}
        #: ``{orientation: answers}`` once a reading screen has been accepted.
        self.answers: dict[str, dict[str, str]] = {}
        #: Set when a reading is one Deckle's model cannot express.
        self.unexplainable = False

        self.widget = QDialog(parent)
        self.widget.setWindowTitle("Calibrate a printer")
        outer = QVBoxLayout(self.widget)

        printer_row = QHBoxLayout()
        printer_row.addWidget(QLabel("Printer:", self.widget))
        self.printer_combo = QComboBox(self.widget)
        self.printer_combo.setEditable(True)
        self.printer_combo.addItems(list(printer_names))
        if printer_name:
            self.printer_combo.setCurrentText(printer_name)
        printer_row.addWidget(self.printer_combo)
        outer.addLayout(printer_row)

        self.heading_label = QLabel("", self.widget)
        outer.addWidget(self.heading_label)
        self.stack = QStackedWidget(self.widget)
        outer.addWidget(self.stack)
        self.status_label = QLabel("", self.widget)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        #: The screen keys in order, from :data:`SCREENS`.
        self.screen_keys = tuple(key for key, _heading, _body in SCREENS)
        self._headings = {key: heading for key, heading, _body in SCREENS}
        #: ``{reading screen: {question key: combo}}``.
        self.reading_combos: dict[str, dict] = {}
        #: ``{orientation: "open this document" button}``.
        self.open_buttons: dict = {}
        self.border_spins: dict = {}

        for key, _heading, body in SCREENS:
            page = QWidget(self.stack)
            layout = QVBoxLayout(page)
            text = QLabel(body, page)
            text.setWordWrap(True)
            layout.addWidget(text)
            if key in _PRINT_ORIENTATION_OF:
                orientation = _PRINT_ORIENTATION_OF[key]
                button = QPushButton(f"Open the {orientation} document", page)
                button.clicked.connect(lambda _=False, o=orientation: self._open_sheet(o))
                self.open_buttons[orientation] = button
                layout.addWidget(button)
            elif key in _ORIENTATION_OF:
                form = QFormLayout()
                combos = {}
                for answer_key, label, choices in READING_QUESTIONS:
                    combo = QComboBox(page)
                    combo.addItems(list(choices))
                    # Nothing preselected: a default is an answer the operator
                    # did not give, and this is the record of what they saw.
                    combo.setCurrentIndex(-1)
                    combo.currentIndexChanged.connect(lambda _=0: self._sync_buttons())
                    question = QLabel(label, page)
                    question.setWordWrap(True)
                    form.addRow(question, combo)
                    combos[answer_key] = combo
                self.reading_combos[key] = combos
                layout.addLayout(form)
            elif key == "measure":
                form = QFormLayout()
                for edge in BORDER_EDGES:
                    spin = QDoubleSpinBox(page)
                    spin.setDecimals(2)
                    spin.setRange(0.0, MAX_BORDER_PT)
                    spin.setSuffix(" pt")
                    form.addRow(f"{edge}:", spin)
                    self.border_spins[edge] = spin
                layout.addLayout(form)
            self.stack.addWidget(page)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel", self.widget)
        self.back_button = QPushButton("Back", self.widget)
        self.next_button = QPushButton("Next", self.widget)
        self.next_button.setDefault(True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        outer.addLayout(buttons)

        self.cancel_button.clicked.connect(self.widget.reject)
        self.back_button.clicked.connect(self.go_back)
        self.next_button.clicked.connect(self.go_next)
        self.printer_combo.currentTextChanged.connect(lambda _text: self._sync_buttons())

        self._show_screen(0)

    # -- where we are ----------------------------------------------------

    @property
    def current_key(self) -> str:
        """The key of the screen on show.

        :returns: an entry of :attr:`screen_keys`.
        """
        return self.screen_keys[self.stack.currentIndex()]

    @property
    def printer_name(self) -> str:
        """The printer this calibration is for.

        :returns: the combo's text, stripped.
        """
        return self.printer_combo.currentText().strip()

    def _show_screen(self, index: int) -> None:
        """Show one screen and do what arriving at it requires.

        :param index: into :attr:`screen_keys`.
        :returns: nothing.
        """
        self.stack.setCurrentIndex(index)
        key = self.current_key
        self.heading_label.setText(self._headings[key])
        self.status_label.setText("")
        if key in _PRINT_ORIENTATION_OF:
            self._write_sheet_for(_PRINT_ORIENTATION_OF[key])
        elif key == "measure":
            self._seed_borders()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Say what can be done from here.

        :returns: nothing. An unexplainable reading leaves only Cancel,
            because there is nothing honest to carry on towards.
        """
        index = self.stack.currentIndex()
        key = self.current_key
        self.back_button.setEnabled(index > 0 and not self.unexplainable)
        self.next_button.setText("Save" if key == "save" else "Next")
        self.next_button.setEnabled(not self.unexplainable and self._screen_is_complete(key))

    def _screen_is_complete(self, key: str) -> bool:
        """Whether the screen has everything it needs.

        :param key: the screen.
        :returns: ``True`` when Next may be pressed.
        """
        if not self.printer_name:
            return False
        if key in _PRINT_ORIENTATION_OF:
            return _PRINT_ORIENTATION_OF[key] in self.sheet_paths
        if key in _ORIENTATION_OF:
            return all(c.currentIndex() >= 0 for c in self.reading_combos[key].values())
        return True

    # -- the print screens -------------------------------------------------

    def _default_write_sheet(self, path: str, page_size, landscape: bool) -> None:
        """Write one orientation's sheet.

        :param path: the PDF to write.
        :param page_size: the paper, upright.
        :param landscape: which orientation.
        :returns: nothing.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_calibration_pdf(path, page_size, landscape=landscape)

    def _write_sheet_for(self, orientation: str) -> None:
        """Write the document this screen hands over.

        :param orientation: ``"portrait"`` or ``"landscape"``.
        :returns: nothing. A failure is said on the screen, and Next stays
            greyed out: there is nothing to print.
        """
        directory = self._sheet_dir or str(data_dir(CALIBRATION_DIR))
        path = os.path.join(directory, f"deckle-calibration.{orientation}.pdf")
        try:
            self._write_sheet(path, self._page_size, orientation == "landscape")
        except OSError as exc:
            self.sheet_paths.pop(orientation, None)
            self.status_label.setText(describe_write_failure(path, exc))
            log_exception("calibration_sheet_failed", exc, path=path)
            return
        self.sheet_paths[orientation] = path
        self.status_label.setText(f"Written to {path}")

    def _open_sheet(self, orientation: str) -> None:
        """Open a written document, saying where it is if that fails.

        :param orientation: ``"portrait"`` or ``"landscape"``.
        :returns: nothing.
        """
        path = self.sheet_paths.get(orientation)
        if path is not None and not self._open(path):
            self.status_label.setText(f"Could not open it. It is at {path}")

    # -- moving --------------------------------------------------------------

    def go_back(self) -> None:
        """Return to the previous screen.

        :returns: nothing.
        """
        index = self.stack.currentIndex()
        if index > 0:
            self._show_screen(index - 1)

    def go_next(self) -> None:
        """Accept this screen and move on, or stay and say why not.

        :returns: nothing.
        """
        key = self.current_key
        if key in _ORIENTATION_OF and not self._accept_reading(key):
            return
        if key == "read_landscape" and self._reconcile() is None:
            return
        if key == "save":
            self._finish()
            return
        self._show_screen(self.stack.currentIndex() + 1)

    def _accept_reading(self, key: str) -> bool:
        """Turn a reading screen's answers into an answer set.

        :param key: the reading screen.
        :returns: whether they make sense. A back number that does not is
            final: see :class:`~deckle.core.calibration_flow.UnexplainableResult`.
        """
        combos = self.reading_combos[key]
        orientation = _ORIENTATION_OF[key]
        try:
            self.answers[orientation] = answers_for(
                orientation=orientation,
                output_face=combos["output_face"].currentText(),
                feed_edge=combos["feed_edge"].currentText(),
                back_number=int(combos["back_number"].currentText()),
                back_orientation=combos["back_orientation"].currentText(),
            )
        except UnexplainableResult as exc:
            self.unexplainable = True
            self.status_label.setText(str(exc))
            log_event("calibration_unexplainable", detail=str(exc))
            self._sync_buttons()
            return False
        return True

    def _reconcile(self, **kwargs) -> PrinterProfile | None:
        """Ask whether the two orientations describe one printer.

        :param kwargs: passed to
            :func:`~deckle.core.calibration_flow.reconcile`.
        :returns: the profile, or ``None`` with the reason on the screen. Back
            stays available: one of the sheets was read wrong, and only the
            operator can say which.
        """
        try:
            return reconcile(self.answers["portrait"], self.answers["landscape"], **kwargs)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return None

    def _seed_borders(self) -> None:
        """Start the border boxes from what is known about this printer.

        :returns: nothing.
        """
        known = self._load_profile(self.printer_name)
        area = known.imageable_area_pt if known is not None else (18.0, 18.0, 18.0, 18.0)
        for edge, value in zip(BORDER_EDGES, area):
            self.border_spins[edge].setValue(value)

    def _finish(self) -> None:
        """Save the calibration and close.

        :returns: nothing. A declined overwrite or a failed write leaves the
            dialog open and whatever was stored untouched.
        """
        name = self.printer_name
        known = self._load_profile(name)
        if known is not None and not self._ask_overwrite(name):
            self.status_label.setText(f"Kept the calibration already saved for {name}.")
            return
        profile = self._reconcile(
            imageable_area_pt=tuple(self.border_spins[e].value() for e in BORDER_EDGES),
            back_offset_pt=(
                (known.back_offset_x_pt, known.back_offset_y_pt)
                if known is not None
                else (0.0, 0.0)
            ),
            calibrated_at=self._now(),
        )
        if profile is None:
            return
        try:
            self._save(profile, name)
        except OSError as exc:
            self.status_label.setText(f"Could not save the calibration: {exc}")
            log_exception("calibration_save_failed", exc, printer=name)
            return
        self.saved_profile = profile
        self.saved_name = name
        log_event("calibration_saved", printer=name)
        self.widget.accept()

    def _default_ask_overwrite(self, name: str) -> bool:
        """Ask before replacing a calibration. Replaceable for tests.

        :param name: the printer.
        :returns: whether to replace it. Declining is the default answer.
        """
        box = self._QMessageBox
        answer = box.question(
            self.widget,
            "Replace this calibration?",
            f"A calibration is already saved for {name}. Replace it with this one?",
            box.StandardButton.Yes | box.StandardButton.No,
            box.StandardButton.No,
        )
        return answer == box.StandardButton.Yes
