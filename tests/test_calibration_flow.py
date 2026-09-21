"""The wizard's judgements, tested without Qt.

The two that carry weight are the escalation (a reading Deckle's model cannot
express must not be rounded to the nearer of two) and the reconciliation (two
orientations are a check on each other, not a repetition).
"""

from __future__ import annotations

import pytest

from deckle.core.calibration import ANSWER_KEYS, ANSWER_VALUES
from deckle.core.calibration_flow import (
    READING_QUESTIONS,
    SCREENS,
    UnexplainableResult,
    answers_for,
    back_number_choices,
    reconcile,
    stack_order_from_back_number,
)


def _reading(orientation: str, **overrides):
    base = dict(
        orientation=orientation,
        output_face="down",
        feed_edge="top",
        back_number=4,
        back_orientation="upright",
    )
    base.update(overrides)
    return answers_for(**base)


# -- the escalation -----------------------------------------------------


def test_back_one_on_front_one_is_a_preserved_order():
    assert stack_order_from_back_number(1) == "same"


def test_the_last_back_on_front_one_is_a_reversed_order():
    assert stack_order_from_back_number(4) == "reversed"


@pytest.mark.parametrize("back_number", [2, 3])
def test_a_middle_reading_is_refused_rather_than_rounded(back_number):
    """The load-bearing refusal.

    A printer that reorders in some third way must not be recorded as one of
    the two Deckle models. Rounding would write a profile that looks measured
    and is wrong, with `calibration_version=1` vouching for it.
    """
    with pytest.raises(UnexplainableResult) as excinfo:
        stack_order_from_back_number(back_number)

    message = str(excinfo.value)
    assert "nothing has been saved" in message
    assert "report it" in message
    assert str(back_number) in message


def test_the_refusal_names_both_readings_it_can_explain():
    """So the operator can check whether they misread, before reporting."""
    with pytest.raises(UnexplainableResult, match=r"back 1.*back 4|back 4.*back 1"):
        stack_order_from_back_number(2)


def test_a_reading_outside_the_run_is_refused_too():
    with pytest.raises(UnexplainableResult):
        stack_order_from_back_number(9)


def test_the_choices_offered_include_the_inexplicable_ones():
    """An operator whose printer did something else must be able to say so.

    A picker listing only 1 and 4 would force them to pick a lie, and the
    escalation above would then never fire.
    """
    assert back_number_choices() == ("1", "2", "3", "4")


def test_answers_for_propagates_the_refusal():
    with pytest.raises(UnexplainableResult):
        _reading("portrait", back_number=2)


# -- assembling one orientation's answers -------------------------------


def test_answers_for_produces_a_set_derive_profile_accepts():
    answers = _reading("portrait")
    assert set(answers) == set(ANSWER_KEYS)
    for key, value in answers.items():
        assert value in ANSWER_VALUES[key], key


def test_only_the_back_number_is_converted():
    """Everything else is the operator's word, carried through unchanged."""
    answers = _reading("landscape", output_face="up", feed_edge="bottom")
    assert answers["output_face"] == "up"
    assert answers["feed_edge"] == "bottom"
    assert answers["orientation"] == "landscape"
    assert answers["stack_order"] == "reversed"  # from back_number=4


# -- reconciling the two orientations -----------------------------------


def test_two_agreeing_orientations_give_one_profile():
    """The same machine, read twice -- and the readings are *opposite*.

    This is the counter-intuitive heart of it. A printer whose `flip_axis` is
    "long" lands its backs upright on portrait paper (whose vertical edge is
    the long one) and upside down on landscape paper (whose vertical edge is
    the short one). So one machine produces two different reports, and
    "agreeing" means the derived profiles match -- never that the answers do.

    Writing the same answer twice here, which reads as the obvious thing to
    write, describes two *different* printers and is correctly refused below.
    """
    portrait = _reading("portrait", back_orientation="upright")
    landscape = _reading("landscape", back_orientation="inverted")

    profile = reconcile(portrait, landscape)
    assert profile.flip_axis in ("long", "short")
    assert profile.calibration_version == 1


def test_disagreeing_orientations_are_refused_with_the_field_named():
    """A disagreement is a misread sheet, and this will not choose which.

    Both sheets reporting `upright` is the natural mistake -- it looks like
    consistency and is not. See the test above.
    """
    portrait = _reading("portrait", back_orientation="upright")
    landscape = _reading("landscape", back_orientation="upright")

    with pytest.raises(ValueError) as excinfo:
        reconcile(portrait, landscape)

    message = str(excinfo.value)
    assert "flip_axis" in message
    assert "read wrong" in message


def test_a_difference_in_a_direct_observation_is_caught_too():
    """`output_face` cannot vary with paper shape; a difference means the two
    runs were done on different settings."""
    portrait = _reading("portrait", back_orientation="upright", output_face="down")
    landscape = _reading("landscape", back_orientation="inverted", output_face="up")

    with pytest.raises(ValueError, match="output_face"):
        reconcile(portrait, landscape)


def test_the_orientations_must_be_given_in_order():
    portrait = _reading("portrait")
    landscape = _reading("landscape")

    with pytest.raises(ValueError, match="not the portrait one"):
        reconcile(landscape, portrait)


def test_reconcile_passes_the_measurements_through():
    profile = reconcile(
        _reading("portrait", back_orientation="upright"),
        _reading("landscape", back_orientation="inverted"),
        imageable_area_pt=(9.0, 9.0, 9.0, 9.0),
        back_offset_pt=(1.5, -0.5),
        calibrated_at="2026-09-21T12:00:00",
    )
    assert profile.imageable_area_pt == (9.0, 9.0, 9.0, 9.0)
    assert (profile.back_offset_x_pt, profile.back_offset_y_pt) == (1.5, -0.5)
    assert profile.calibrated_at == "2026-09-21T12:00:00"


def test_reconcile_agrees_with_deriving_either_one_alone():
    """The check adds confidence, not a different answer."""
    from deckle.core.calibration import derive_profile

    portrait = _reading("portrait", back_orientation="upright")
    landscape = _reading("landscape", back_orientation="inverted")

    assert reconcile(portrait, landscape) == derive_profile(portrait)


# -- the screens --------------------------------------------------------


def test_the_screens_cover_both_orientations_and_end_in_a_save():
    keys = [key for key, _, _ in SCREENS]
    assert keys == [
        "print_portrait",
        "read_portrait",
        "print_landscape",
        "read_landscape",
        "measure",
        "save",
    ]


def test_every_screen_has_a_heading_and_a_body():
    for key, heading, body in SCREENS:
        assert heading.strip(), key
        assert len(body.strip()) > 40, key


def test_the_reading_screen_asks_exactly_what_answers_for_needs():
    """The questions and the function that consumes them, pinned together."""
    asked = {key for key, _, _ in READING_QUESTIONS}
    assert asked == {"output_face", "feed_edge", "back_number", "back_orientation"}


def test_every_question_offers_only_values_its_answer_accepts():
    for key, label, choices in READING_QUESTIONS:
        assert label.strip().endswith(":"), key
        if key == "back_number":
            assert choices == back_number_choices()
        else:
            assert choices == ANSWER_VALUES[key]


def test_no_screen_promises_a_result_before_it_is_known():
    """The sheet's own rule, applied to the dialog.

    `calibration_sheet` refuses to say what the operator should see, because a
    tired person at a printer will agree with whatever they are shown. The
    reading screens must not either.
    """
    for key, heading, body in SCREENS:
        if not key.startswith("read"):
            continue
        lowered = body.lower()
        for leading in ("should be", "normally", "usually", "expect"):
            assert leading not in lowered, f"{key} leads the witness: {leading!r}"
