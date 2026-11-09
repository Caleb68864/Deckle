"""F3: the registration target the operator reads numbers off.

The calibration sheet's back faces carry two numbered millimetre scales; the
front carries a plain heavy cross. Held to a light, the cross shows through and
the two numbers it crosses are the profile's registration correction, sign
included. These tests pin each link of that chain: the sheet prints the scales
(and only on the backs), the reading converts to points with no sign games,
an off-scale reading is refused, and the wizard's two boxes reach the saved
profile's ``back_offset_x_pt``/``back_offset_y_pt``.
"""

from __future__ import annotations

import math
import os

import pypdfium2 as pdfium
import pytest

from deckle.core.calibration_flow import registration_offset_pt
from deckle.core.calibration_sheet import (
    MM_PT,
    REGISTRATION_SCALE_MM,
    REGISTRATION_STEP_MM,
    SHEETS,
    make_calibration_pdf,
    page_plan,
    registration_scale_labels,
    registration_scale_ticks,
)


def _texts(path) -> list[str]:
    document = pdfium.PdfDocument(str(path))
    try:
        return [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    path = tmp_path_factory.mktemp("reg") / "portrait.pdf"
    make_calibration_pdf(str(path))
    return _texts(path)


# -- the printed target ------------------------------------------------


def test_the_scale_spans_its_range_at_its_step():
    ticks = registration_scale_ticks()
    assert ticks[0] == -REGISTRATION_SCALE_MM
    assert ticks[-1] == REGISTRATION_SCALE_MM
    assert 0.0 in ticks
    steps = {round(b - a, 9) for a, b in zip(ticks, ticks[1:])}
    assert steps == {REGISTRATION_STEP_MM}


def test_the_labels_carry_their_sign():
    """The sign is the sign of the correction; an unsigned "5" off the left
    arm would be copied down as a correction in the wrong direction."""
    labels = registration_scale_labels()
    assert labels[0] == f"-{REGISTRATION_SCALE_MM}"
    assert labels[-1] == f"+{REGISTRATION_SCALE_MM}"
    assert "0" in labels
    assert all(label == "0" or label[0] in "+-" for label in labels)


def test_every_back_carries_the_numbered_scales_and_no_front_does(pages):
    """The index is on the front and the scale on the back, never both: a
    scale on the front would show through mirrored and be read instead."""
    plan = page_plan(SHEETS)
    for (kind, number, side), text in zip(plan, pages):
        if kind != "face":
            continue
        has_scale = "X mm" in text and "Y mm" in text and f"+{REGISTRATION_SCALE_MM}" in text
        if side == "BACK":
            assert has_scale, f"back {number} has no registration scale"
        else:
            assert not has_scale, f"front {number} carries a scale"


def test_the_cover_asks_for_the_two_numbers(pages):
    cover = pages[0]
    assert "BACK towards you" in cover
    assert "X ______ mm Y ______ mm" in cover
    assert "back_offset_x_pt" in cover and "back_offset_y_pt" in cover


def test_the_command_says_where_the_two_readings_go(tmp_path, capsys):
    """The CLI path for F3: the readings are typed as they were read."""
    from deckle.cli.options import build_parser

    parser = build_parser()
    args = parser.parse_args(["calibration-sheet", "-o", str(tmp_path / "cal.pdf")])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "--back-offset Xmm,Ymm" in out

    # And that spelling is one `profile set` accepts, as the same points
    # `registration_offset_pt` would produce.
    parsed = parser.parse_args(["profile", "set", "Brother", "--back-offset", "2.5mm,-1mm"])
    assert parsed.back_offset == pytest.approx(registration_offset_pt(2.5, -1.0))


# -- the reading -> the profile ----------------------------------------


def test_a_reading_is_the_correction_in_points_with_its_sign():
    assert registration_offset_pt(3.0, -1.5) == pytest.approx((3.0 * MM_PT, -1.5 * MM_PT))
    assert registration_offset_pt(0.0, 0.0) == (0.0, 0.0)


@pytest.mark.parametrize("x, y", [(REGISTRATION_SCALE_MM + 0.5, 0.0), (0.0, -REGISTRATION_SCALE_MM - 1)])
def test_an_off_scale_reading_is_refused_not_clamped(x, y):
    with pytest.raises(ValueError, match="off the printed scale"):
        registration_offset_pt(x, y)


@pytest.mark.parametrize("bad", [math.nan, math.inf, "3", None, True])
def test_a_reading_that_is_not_a_number_is_refused(bad):
    with pytest.raises(ValueError, match="not a number"):
        registration_offset_pt(bad, 0.0)


# -- the wizard --------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def _qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _at_measure(_qapp, **kwargs):
    from tests.test_calibration_wizard import _to_measure, _wizard

    wizard = _wizard(**kwargs)
    _to_measure(wizard)
    assert wizard.current_key == "measure"
    return wizard


def test_the_readings_are_off_until_ticked(_qapp):
    wizard = _at_measure(_qapp)
    assert not wizard.registration_check.isChecked()
    assert not any(s.isEnabled() for s in wizard.registration_spins.values())
    wizard.registration_check.setChecked(True)
    assert all(s.isEnabled() for s in wizard.registration_spins.values())


def test_the_spins_offer_exactly_the_printed_scale(_qapp):
    wizard = _at_measure(_qapp)
    for spin in wizard.registration_spins.values():
        assert (spin.minimum(), spin.maximum()) == (-REGISTRATION_SCALE_MM, REGISTRATION_SCALE_MM)
        assert spin.singleStep() == REGISTRATION_STEP_MM


def test_ticked_readings_reach_the_saved_profile(_qapp):
    wizard = _at_measure(_qapp)
    wizard.registration_check.setChecked(True)
    wizard.registration_spins["X"].setValue(2.5)
    wizard.registration_spins["Y"].setValue(-1.0)
    wizard.go_next()
    wizard.go_next()

    profile = wizard.saved[0][0]
    assert profile.back_offset_x_pt == pytest.approx(2.5 * MM_PT)
    assert profile.back_offset_y_pt == pytest.approx(-1.0 * MM_PT)


def test_ticked_readings_replace_a_saved_registration(_qapp):
    from tests.test_calibration_wizard import _existing

    wizard = _at_measure(_qapp, profile_loader=lambda name: _existing(), ask_overwrite=lambda name: True)
    wizard.registration_check.setChecked(True)
    wizard.registration_spins["X"].setValue(-4.0)
    wizard.go_next()
    wizard.go_next()

    profile = wizard.saved[0][0]
    assert (profile.back_offset_x_pt, profile.back_offset_y_pt) == pytest.approx((-4.0 * MM_PT, 0.0))


def test_a_refused_reading_saves_nothing_and_says_why(_qapp, monkeypatch):
    """The spins cannot go off the scale, but the conversion is the
    authority; if it refuses, the wizard must not save a guess."""
    from deckle.app.views import calibration_wizard

    def refuse(x, y):
        raise ValueError("registration reading X=99 mm is off the printed scale")

    monkeypatch.setattr(calibration_wizard, "registration_offset_pt", refuse)
    wizard = _at_measure(_qapp)
    wizard.registration_check.setChecked(True)
    wizard.go_next()
    wizard.go_next()

    assert wizard.saved == []
    assert "off the printed scale" in wizard.status_label.text()
