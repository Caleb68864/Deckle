"""F1's other half: typing a profile in, from the print dialog."""

from __future__ import annotations

import dataclasses
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


PRESET = next(iter(BUILTIN_PRESETS.values()))
MEASURED = dataclasses.replace(
    PRESET, calibration_version=1, calibrated_at="2026-01-01T00:00:00",
    imageable_area_pt=(10.0, 11.0, 12.0, 13.0), back_offset_x_pt=1.5, back_offset_y_pt=-2.5,
)


def _editor(printer="Brother", **kwargs):
    from deckle.app.views.profile_editor import ProfileEditorDialog

    saved = []
    defaults = dict(saver=lambda profile, name: saved.append((profile, name)), now=lambda: "NOW")
    defaults.update(kwargs)
    editor = ProfileEditorDialog(printer, **defaults)
    editor.saved = saved
    return editor


def test_the_form_shows_the_profile_it_was_given_in_margin_order():
    editor = _editor(start_from=MEASURED)
    spins = editor.imageable_spins
    assert [spins[e].value() for e in ("left", "top", "right", "bottom")] == [10.0, 11.0, 12.0, 13.0]
    assert editor.back_offset_x_spin.value() == 1.5
    assert editor.back_offset_y_spin.value() == -2.5
    assert editor.flip_axis_combo.currentText() == MEASURED.flip_axis
    assert editor.reverse_stack_check.isChecked() is MEASURED.reverse_stack


def test_saving_writes_what_the_form_says_under_the_printers_name():
    editor = _editor(start_from=PRESET)
    editor.back_offset_x_spin.setValue(-3.0)
    editor.imageable_spins["top"].setValue(20.0)

    assert editor.save() is True

    (profile, name), = editor.saved
    assert name == "Brother"
    assert editor.saved_profile is profile
    assert profile.back_offset_x_pt == -3.0
    assert profile.imageable_area_pt[1] == 20.0
    assert profile.calibration_version == 0
    assert profile.calibrated_at == "NOW"


def test_the_offset_can_go_either_way():
    """A zero minimum would make half the answer space unreachable."""
    editor = _editor()
    assert editor.back_offset_y_spin.minimum() < 0 < editor.back_offset_y_spin.maximum()


def test_typing_an_offset_into_a_measured_profile_leaves_it_measured():
    """The wizard does not measure offsets, so typing one is the expected
    next step and must not un-measure everything else."""
    editor = _editor(start_from=MEASURED)
    editor.back_offset_x_spin.setValue(4.0)
    editor.save()

    profile = editor.saved[0][0]
    assert profile.calibration_version == 1
    assert profile.calibrated_at == MEASURED.calibrated_at
    assert profile.back_offset_x_pt == 4.0


def test_overruling_a_measured_behaviour_marks_it_typed():
    editor = _editor(start_from=MEASURED)
    other = "short" if MEASURED.flip_axis == "long" else "long"
    editor.flip_axis_combo.setCurrentText(other)
    editor.save()

    profile = editor.saved[0][0]
    assert profile.flip_axis == other
    assert profile.calibration_version == 0
    assert profile.calibrated_at == "NOW"


def test_choosing_a_preset_loads_it_and_writes_nothing():
    other_name, other = list(BUILTIN_PRESETS.items())[1]
    editor = _editor(start_from=MEASURED)

    editor.preset_combo.setCurrentIndex(editor.preset_combo.findData(other_name))

    assert editor.output_face_combo.currentText() == other.output_face
    assert editor.reverse_stack_check.isChecked() is other.reverse_stack
    assert "nothing is saved" in editor.status_label.text()
    assert editor.saved == []

    editor.preset_combo.setCurrentIndex(0)
    assert editor.back_offset_x_spin.value() == 1.5


def test_margins_that_leave_no_sheet_are_refused():
    editor = _editor(builtin_presets=BUILTIN_PRESETS)
    editor.imageable_spins["left"].setRange(0.0, 1000.0)
    editor.imageable_spins["left"].setValue(600.0)
    editor.imageable_spins["right"].setValue(100.0)

    assert editor.save() is False
    assert editor.saved == []
    assert "no printable area" in editor.status_label.text()


def test_no_printer_means_no_save():
    editor = _editor(printer="")
    assert not editor.save_button.isEnabled()
    assert editor.save() is False
    assert editor.saved == []


def test_a_failed_write_says_so_and_stays_open():
    def _refuse(profile, name):
        raise OSError("disk full")

    editor = _editor(saver=_refuse)
    assert editor.save() is False
    assert editor.saved_profile is None
    assert "disk full" in editor.status_label.text()


def test_it_round_trips_through_the_real_store():
    from deckle.app.views.profile_editor import ProfileEditorDialog

    editor = ProfileEditorDialog("Editor Test Printer", start_from=PRESET)
    editor.back_offset_y_spin.setValue(2.25)
    assert editor.save() is True
    assert PrinterProfile.load("Editor Test Printer") == editor.saved_profile


# -- from the print dialog --------------------------------------------------------


def _print_dialog(saved_profile, seen, **kwargs):
    from types import SimpleNamespace

    from deckle.app.views.print_dialog import PrintDialog
    from deckle.core.models import SheetPlan

    class _Editor:
        def __init__(self, name, parent, *, start_from):
            seen.append((name, start_from))
            self.widget = SimpleNamespace(exec=lambda: 0)
            self.saved_profile = saved_profile

    class _Session:
        list_resumable = staticmethod(lambda: [])

    return PrintDialog(
        SheetPlan(sheets=[], paper_pt=(612.0, 792.0), warnings=[]),
        printer_names=["Brother"],
        profile_loader=kwargs.pop("profile_loader", lambda name: (_ for _ in ()).throw(FileNotFoundError(name))),
        session_cls=_Session,
        backend_cls=lambda profile: None,
        resumable_lister=lambda: [],
        profile_editor_cls=_Editor,
        **kwargs,
    )


def test_the_print_dialog_opens_the_editor_on_what_it_has_selected():
    seen = []
    dialog = _print_dialog(None, seen)
    dialog.edit_profile_button.click()
    assert seen == [("Brother", dialog.selected_profile())]
    assert dialog.status_label.text() == ""


def test_a_saved_edit_is_what_the_dialog_prints_with_next():
    """Nothing cached: the picker re-reads, and the saved one is preselected."""
    store = {}

    def _load(name):
        if name in store:
            return store[name]
        raise FileNotFoundError(name)

    typed = dataclasses.replace(PRESET, back_offset_x_pt=7.0)
    seen = []
    dialog = _print_dialog(typed, seen, profile_loader=_load)
    store["Brother"] = typed

    dialog.edit_profile()

    assert dialog.selected_profile().back_offset_x_pt == 7.0
    assert "Brother" in dialog.status_label.text()
