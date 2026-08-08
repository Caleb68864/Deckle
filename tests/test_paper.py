"""Stating paper by what is printed on the ream wrapper.

Deckle asked for paper thickness as a caliper in a four-decimal spin box.
Nobody knows their paper's caliper; everybody has its weight printed on
the wrapper. These cover the conversion, and then the question that
follows from it -- how many sheets should a gathering hold?

The US basis-weight factors are *derived* from each grade's basis size
rather than tabulated, so they are checked here against published pairs.
A transcription error in one factor would otherwise shift every caliper
that grade produces, quietly and by a plausible-looking amount.
"""

from __future__ import annotations

import pytest

from deckle.core.paper import gsm_from_pounds


# Published pairs, one per grade Deckle knows. "20 lb" means nothing
# without a grade -- bond, text and cover give 75, 30 and 54 gsm for the
# same number -- which is why the grade is required rather than guessed.
@pytest.mark.parametrize("pounds,grade,expected_gsm", [
    (20, "bond", 75.2),
    (24, "bond", 90.2),
    (28, "bond", 105.3),
    (70, "text", 103.6),
    (80, "text", 118.4),
    (65, "cover", 175.8),
    (80, "cover", 216.3),
    (90, "index", 162.7),
    (100, "tag", 162.7),
])
def test_us_basis_weights_convert_to_grammage(pounds, grade, expected_gsm):
    assert gsm_from_pounds(pounds, grade) == pytest.approx(expected_gsm, abs=0.5)


def test_one_number_means_different_paper_in_different_grades():
    """The reason the grade is a required argument rather than a default.

    A silent default would be wrong by up to 2.5x for anyone whose paper
    is quoted against a different basis size, and the result would look
    entirely reasonable.
    """
    assert gsm_from_pounds(20, "bond") != pytest.approx(
        gsm_from_pounds(20, "cover"), rel=0.1
    )


def test_an_unknown_grade_is_refused_by_name():
    """The remedy is the list of accepted grades, so the message carries
    it -- withholding it turns a typo into a search through the source."""
    with pytest.raises(ValueError) as caught:
        gsm_from_pounds(20, "newsprint")

    assert "newsprint" in str(caught.value)
    assert "bond" in str(caught.value)


def test_grammage_scales_with_the_weight():
    assert gsm_from_pounds(40, "bond") == pytest.approx(
        2 * gsm_from_pounds(20, "bond")
    )


# -- weight to caliper ---------------------------------------------------


def test_office_paper_lands_where_a_caliper_would_read_it():
    """80gsm copier measures about 0.104mm in the hand. If this drifts,
    every number the feature reports drifts with it."""
    from deckle.core.paper import PT_PER_MM, caliper_pt_from_gsm

    assert caliper_pt_from_gsm(80, "copier") / PT_PER_MM == pytest.approx(
        0.104, abs=0.002
    )


def test_bulk_is_what_separates_two_papers_of_one_weight():
    """The whole reason weight alone is not enough: the same grammage is
    thicker as a bulky book paper than as a coated one."""
    from deckle.core.paper import caliper_pt_from_gsm

    assert caliper_pt_from_gsm(100, "bulky") > caliper_pt_from_gsm(100, "coated")


def test_an_unknown_paper_type_is_refused_by_name():
    from deckle.core.paper import caliper_pt_from_gsm

    with pytest.raises(ValueError) as caught:
        caliper_pt_from_gsm(80, "papyrus")

    assert "papyrus" in str(caught.value)
    assert "copier" in str(caught.value)


def test_every_preset_carries_a_plausible_caliper():
    """Guards a typo in the table: no paper in this range is thinner than
    0.03mm or thicker than 0.5mm, so a misplaced decimal fails here."""
    from deckle.core.paper import PAPER_PRESETS, PT_PER_MM

    assert PAPER_PRESETS
    for preset in PAPER_PRESETS:
        millimetres = preset.caliper_pt / PT_PER_MM
        assert 0.03 < millimetres < 0.5, preset


def test_presets_are_ordered_thinnest_first():
    """A dropdown that jumps around is harder to scan than one that does
    not, and the order is the only thing making it scannable."""
    from deckle.core.paper import PAPER_PRESETS

    calipers = [preset.caliper_pt for preset in PAPER_PRESETS]
    assert calipers == sorted(calipers)


def test_preset_names_are_distinct():
    """They are the identity a stored project would round-trip through."""
    from deckle.core.paper import PAPER_PRESETS

    names = [preset.name for preset in PAPER_PRESETS]
    assert len(set(names)) == len(names)


def test_a_preset_agrees_with_its_own_weight_and_type():
    """The preset table must not carry a hand-written caliper that has
    drifted from the formula everything else uses."""
    from deckle.core.paper import PAPER_PRESETS, caliper_pt_from_gsm

    for preset in PAPER_PRESETS:
        assert preset.caliper_pt == caliper_pt_from_gsm(
            preset.gsm, preset.paper_type
        )


# -- sizing a gathering --------------------------------------------------
#
# The calibration table from `docs/plans/2026-08-08-paper-and-recovery-design.md`,
# written as a test rather than as prose so it is enforced rather than
# remembered. 80gsm reaching 8 sheets -- a 32-page gathering, the standard
# trade signature -- is the evidence that the 1.8mm fold cap is honest
# rather than tuned. Had it produced 7 or 11, the constant would be wrong.


@pytest.mark.parametrize("gsm,paper_type,no_trim,with_trim", [
    (80, "copier", 4, 8),
    (100, "offset", 3, 6),
    (120, "offset", 3, 5),
    (160, "offset", 2, 4),
])
def test_the_suggestion_matches_the_calibration_table(
    gsm, paper_type, no_trim, with_trim
):
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    caliper = caliper_pt_from_gsm(gsm, paper_type)
    assert suggest_sheets_per_signature(caliper).sheets == no_trim
    assert suggest_sheets_per_signature(caliper, trim_pt=18.0).sheets == with_trim


def test_thin_paper_with_no_trim_is_limited_by_creep():
    """Which constraint bound the answer decides the remedy, so it is
    reported rather than merely used: creep-bound means "plan a trim"."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    caliper = caliper_pt_from_gsm(80, "copier")
    assert suggest_sheets_per_signature(caliper).limited_by == "creep"


def test_the_same_paper_with_a_trim_is_limited_by_the_fold():
    """Creep is absorbed by trimming, so planning one moves the binding
    constraint to how thick a gathering will still fold."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    caliper = caliper_pt_from_gsm(80, "copier")
    assert suggest_sheets_per_signature(caliper, trim_pt=18.0).limited_by == "fold"


def test_creep_alone_would_have_given_an_absurd_answer():
    """The reason the fold cap exists at all. Without it, 80gsm with a
    quarter-inch trim tolerates dozens of sheets before creep shows -- and
    nobody hand-sews a 62-sheet gathering."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    caliper = caliper_pt_from_gsm(80, "copier")
    creep_only = int(18.0 // caliper) + 1
    assert creep_only > 50
    assert suggest_sheets_per_signature(caliper, trim_pt=18.0).sheets < 10


def test_a_suggestion_never_drops_below_one_sheet():
    """Board thick enough to bind both constraints to zero still has to
    produce a gathering someone can fold."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    assert suggest_sheets_per_signature(caliper_pt_from_gsm(400, "offset")).sheets >= 1


def test_no_thickness_means_no_suggestion():
    """Thickness is optional, and silence is the honest answer without it
    -- the same contract `spine_width_pt` already keeps."""
    from deckle.core.paper import suggest_sheets_per_signature

    assert suggest_sheets_per_signature(0.0) is None
    assert suggest_sheets_per_signature(-1.0) is None


def test_the_reported_creep_uses_the_schedules_own_formula():
    """Two formulas for one physical quantity would drift apart. This is
    the same ``(sheets - 1) * caliper`` the schedule already prints."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    caliper = caliper_pt_from_gsm(100, "offset")
    suggestion = suggest_sheets_per_signature(caliper, trim_pt=18.0)
    assert suggestion.creep_pt == pytest.approx(
        (suggestion.sheets - 1) * caliper
    )


def test_the_suggested_creep_stays_inside_the_planned_trim():
    """The promise the suggestion makes. If this fails the advice is
    actively harmful: it would tell a binder a gathering is safe to trim
    when it is not."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    for gsm in (80, 100, 120, 160, 200):
        caliper = caliper_pt_from_gsm(gsm, "offset")
        suggestion = suggest_sheets_per_signature(caliper, trim_pt=18.0)
        assert suggestion.creep_pt <= 18.0 + 1e-9 or suggestion.sheets == 1


def test_pages_follow_from_sheets_under_folio():
    """One sheet is four pages folded, which is what a binder counts in."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    suggestion = suggest_sheets_per_signature(caliper_pt_from_gsm(80, "copier"))
    assert suggestion.pages == suggestion.sheets * 4


def test_thicker_paper_never_suggests_more_sheets():
    """Monotonic, which a two-constraint minimum could easily not be."""
    from deckle.core.paper import caliper_pt_from_gsm, suggest_sheets_per_signature

    counts = [
        suggest_sheets_per_signature(caliper_pt_from_gsm(gsm, "offset"), trim_pt=18.0).sheets
        for gsm in range(60, 300, 10)
    ]
    assert counts == sorted(counts, reverse=True)


def test_the_schedule_shares_this_creep_threshold():
    """One physical threshold expressed twice is the duplication this
    codebase has already been bitten by four times over one platform
    ladder."""
    from deckle.core import schedule
    from deckle.core.paper import CREEP_INVISIBLE_PT

    assert schedule.CREEP_INVISIBLE_PT is CREEP_INVISIBLE_PT
