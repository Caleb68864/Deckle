"""F2 Phase 4: the screens, driven end to end with no printer and no modal.

The judgements are tested in ``test_calibration_flow.py``; what is tested here
is that the screens reach them, and that nothing can be saved that they refuse.
Physical confirmation is F2 Phase 5 and needs a real printer.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.calibration_flow import READING_QUESTIONS, SCREENS, reconcile  # noqa: E402
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


PORTRAIT = {"output_face": "down", "feed_edge": "top", "back_number": "4", "back_orientation": "inverted"}
#: The flip rule inverts between the orientations, so one machine reports
#: opposite orientations on the two sheets. See `test_calibration_flow.py`.
LANDSCAPE = {**PORTRAIT, "back_orientation": "upright"}


def _wizard(**kwargs):
    from deckle.app.views.calibration_wizard import CalibrationWizard

    saved = []
    defaults = dict(
        printer_names=["Brother"],
        sheet_writer=lambda path, page_size, landscape: None,
        opener=lambda path: True,
        profile_loader=lambda name: None,
        saver=lambda profile, name: saved.append((profile, name)),
        ask_overwrite=lambda name: pytest.fail("nothing to overwrite"),
        now=lambda: "2026-11-02T12:00:00",
        sheet_dir="/nowhere",
    )
    defaults.update(kwargs)
    wizard = CalibrationWizard(**defaults)
    wizard.saved = saved
    return wizard


def _answer(wizard, answers):
    combos = wizard.reading_combos[wizard.current_key]
    for key, value in answers.items():
        combos[key].setCurrentText(value)


def _to_measure(wizard, portrait=PORTRAIT, landscape=LANDSCAPE):
    wizard.go_next()
    _answer(wizard, portrait)
    wizard.go_next()
    wizard.go_next()
    _answer(wizard, landscape)
    wizard.go_next()


def test_the_screens_come_from_the_flow_in_its_order():
    wizard = _wizard()
    assert wizard.screen_keys == tuple(key for key, _h, _b in SCREENS)
    assert wizard.current_key == "print_portrait"
    assert wizard.heading_label.text() == "Print the portrait sheet"


def test_a_clean_run_saves_what_reconcile_says():
    wizard = _wizard()
    _to_measure(wizard)
    assert wizard.current_key == "measure"
    wizard.go_next()
    assert wizard.current_key == "save"
    assert wizard.next_button.text() == "Save"

    wizard.go_next()

    (profile, name), = wizard.saved
    assert name == "Brother"
    assert wizard.saved_profile is profile and wizard.saved_name == "Brother"
    assert profile.calibration_version == 1
    assert profile.calibrated_at == "2026-11-02T12:00:00"
    expected = reconcile(
        {"orientation": "portrait", "output_face": "down", "feed_edge": "top",
         "stack_order": "reversed", "back_orientation": "inverted"},
        {"orientation": "landscape", "output_face": "down", "feed_edge": "top",
         "stack_order": "reversed", "back_orientation": "upright"},
    )
    assert (profile.flip_axis, profile.reverse_stack) == (expected.flip_axis, expected.reverse_stack)


def test_each_print_screen_writes_its_own_orientation():
    written = []
    wizard = _wizard(sheet_writer=lambda path, page_size, landscape: written.append(
        (os.path.basename(path), page_size, landscape)
    ), page_size=(792.0, 612.0))

    wizard.go_next()
    _answer(wizard, PORTRAIT)
    wizard.go_next()

    assert written == [
        ("deckle-calibration.portrait.pdf", (612.0, 792.0), False),
        ("deckle-calibration.landscape.pdf", (612.0, 792.0), True),
    ]


def test_the_real_sheet_gets_written_and_can_be_opened(tmp_path):
    from deckle.app.views.calibration_wizard import CalibrationWizard

    opened = []
    wizard = CalibrationWizard(
        ["Brother"], sheet_dir=str(tmp_path / "new"), opener=lambda path: opened.append(path) or True
    )
    wizard.open_buttons["portrait"].click()

    assert opened == [str(tmp_path / "new" / "deckle-calibration.portrait.pdf")]
    assert os.path.getsize(opened[0]) > 0


def test_a_sheet_that_cannot_be_written_stops_the_screen():
    def _refuse(path, page_size, landscape):
        raise PermissionError(13, "denied", path)

    wizard = _wizard(sheet_writer=_refuse)
    assert not wizard.next_button.isEnabled()
    assert wizard.status_label.text()


def test_an_opener_that_fails_says_where_the_file_is():
    wizard = _wizard(opener=lambda path: False)
    wizard.open_buttons["portrait"].click()
    assert "deckle-calibration.portrait.pdf" in wizard.status_label.text()


def test_no_reading_is_preselected():
    """A default is an answer the operator did not give."""
    wizard = _wizard()
    wizard.go_next()
    assert not wizard.next_button.isEnabled()
    keys = [key for key, _l, _c in READING_QUESTIONS]
    combos = wizard.reading_combos["read_portrait"]
    for key in keys:
        assert combos[key].currentIndex() == -1
    for key in keys[:-1]:
        combos[key].setCurrentIndex(0)
    assert not wizard.next_button.isEnabled()
    combos[keys[-1]].setCurrentIndex(0)
    assert wizard.next_button.isEnabled()


@pytest.mark.parametrize("back_number", ["2", "3"])
def test_an_unexplainable_reading_leaves_only_cancel(back_number):
    """The escalation path, and the reason the picker offers all four."""
    wizard = _wizard()
    wizard.go_next()
    _answer(wizard, {**PORTRAIT, "back_number": back_number})

    wizard.go_next()

    assert wizard.current_key == "read_portrait"
    assert wizard.unexplainable
    assert "nothing has been saved" in wizard.status_label.text()
    assert not wizard.next_button.isEnabled()
    assert not wizard.back_button.isEnabled()
    assert wizard.cancel_button.isEnabled()
    assert wizard.saved == []


def test_two_orientations_that_disagree_stay_on_the_screen_and_can_go_back():
    wizard = _wizard()
    _to_measure(wizard, landscape=PORTRAIT)  # the "obvious" wrong answer

    assert wizard.current_key == "read_landscape"
    assert "different printers" in wizard.status_label.text()
    assert wizard.back_button.isEnabled()
    assert wizard.saved == []


def test_the_printer_must_be_named():
    wizard = _wizard(printer_names=[])
    assert not wizard.next_button.isEnabled()
    wizard.printer_combo.setCurrentText("Typed by hand")
    assert wizard.next_button.isEnabled()


def _existing(**overrides):
    base = next(iter(BUILTIN_PRESETS.values()))
    from dataclasses import replace

    return replace(
        base, imageable_area_pt=(10.0, 11.0, 12.0, 13.0),
        back_offset_x_pt=1.5, back_offset_y_pt=-2.5, **overrides
    )


def test_borders_are_seeded_and_recorded_in_the_profiles_order():
    wizard = _wizard(profile_loader=lambda name: _existing(), ask_overwrite=lambda name: True)
    _to_measure(wizard)
    spins = wizard.border_spins
    assert [spins[e].value() for e in ("Left", "Top", "Right", "Bottom")] == [10.0, 11.0, 12.0, 13.0]

    spins["Right"].setValue(30.0)
    wizard.go_next()
    wizard.go_next()

    assert wizard.saved[0][0].imageable_area_pt == (10.0, 11.0, 30.0, 13.0)


def test_recalibrating_keeps_the_hand_measured_registration():
    """The wizard does not ask for it, so saving must not zero it."""
    wizard = _wizard(profile_loader=lambda name: _existing(), ask_overwrite=lambda name: True)
    _to_measure(wizard)
    wizard.go_next()
    wizard.go_next()

    profile = wizard.saved[0][0]
    assert (profile.back_offset_x_pt, profile.back_offset_y_pt) == (1.5, -2.5)


def test_declining_to_overwrite_saves_nothing():
    wizard = _wizard(profile_loader=lambda name: _existing(), ask_overwrite=lambda name: False)
    _to_measure(wizard)
    wizard.go_next()
    wizard.go_next()

    assert wizard.saved == []
    assert wizard.saved_profile is None
    assert "Kept" in wizard.status_label.text()


def test_a_failed_save_says_so_and_stays_open():
    def _refuse(profile, name):
        raise OSError("disk full")

    wizard = _wizard(saver=_refuse)
    _to_measure(wizard)
    wizard.go_next()
    wizard.go_next()

    assert wizard.saved_profile is None
    assert "disk full" in wizard.status_label.text()


def test_it_round_trips_through_the_real_store():
    """Against `PrinterProfile.save` and `load`: conftest points them at scratch."""
    from deckle.app.views.calibration_wizard import CalibrationWizard

    wizard = CalibrationWizard(
        ["Wizard Test Printer"], sheet_writer=lambda *a: None, sheet_dir="/nowhere",
        now=lambda: "t",
    )
    _to_measure(wizard)
    wizard.go_next()
    wizard.go_next()

    assert PrinterProfile.load("Wizard Test Printer") == wizard.saved_profile


def test_the_menu_reaches_it_and_the_window_adopts_the_result(monkeypatch):
    from types import SimpleNamespace

    import deckle.app.main as app_main

    class _Wizard:
        def __init__(self, names, parent, page_size, printer_name):
            self.args = (names, page_size, printer_name)
            self.widget = SimpleNamespace(exec=lambda: 0)
            self.saved_profile = "measured"
            self.saved_name = "Brother"

    monkeypatch.setattr(app_main, "CalibrationWizard", _Wizard)
    applied = []
    shown = []
    window = SimpleNamespace(
        window=None,
        _printers=["Brother"],
        _imageable_areas={},
        state=SimpleNamespace(project=SimpleNamespace(
            layout=SimpleNamespace(paper=(612.0, 792.0)), printer="Brother"
        )),
        status_bar=SimpleNamespace(showMessage=shown.append),
        _apply_printers=lambda *args: applied.append(args),
    )

    app_main.MainWindow.calibrate_printer(window)

    assert applied == [(["Brother"], None, {})]
    assert "Brother" in shown[-1]
    assert any(
        item.action == "calibrate_printer"
        for menu in app_main.MENUS for item in menu.items
    )
