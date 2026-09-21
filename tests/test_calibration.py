"""The pure half of the calibration wizard: answers in, profile out.

The exhaustive mapping test is the point of this file. ``derive_profile`` is
total over the enumerated answer space (``docs/spikes/calibration-state-space.md``),
so the test can iterate that space rather than sample it, and assert that every
row of the spike's 16-state table is producible and that nothing collides.

**Orientation is part of an answer, and that is the deviation from the F2
spec.** The spec's answer vocabulary has four keys and derives
``flip_axis = "long" if back_orientation == "inverted"``, justified by a rule
``plan_passes`` has not used since 2026-08-06. The real rule compares
``flip_axis`` against the paper's own vertical edge, so the same observation --
"the backs came out upside down" -- means opposite things on portrait and
landscape paper. Phase 1 found this; these tests pin it.
"""

from __future__ import annotations

import itertools

import pytest

from deckle.core.calibration import (
    ANSWER_KEYS,
    ANSWER_VALUES,
    derive_profile,
)
from deckle.core.printing import duplex_flip_edge, plan_passes
from deckle.core.profiles import PrinterProfile


PORTRAIT_PT = (612.0, 792.0)
LANDSCAPE_PT = (792.0, 612.0)


def _answers(**overrides: str) -> dict[str, str]:
    """A complete, valid answer set, overridable one key at a time."""
    base = {
        "output_face": "down",
        "feed_edge": "top",
        "stack_order": "reversed",
        "back_orientation": "upright",
        "orientation": "portrait",
    }
    base.update(overrides)
    return base


# -- the answer space ---------------------------------------------------


def test_answer_keys_and_values_agree():
    """Every key has a value domain and vice versa -- no orphans either way."""
    assert set(ANSWER_KEYS) == set(ANSWER_VALUES)
    for key in ANSWER_KEYS:
        assert len(ANSWER_VALUES[key]) >= 2, key


def test_orientation_is_part_of_the_answer_space():
    """The fifth field Phase 1 added, asserted rather than assumed.

    Without it `back_orientation` cannot be converted into `flip_axis` at all,
    so its absence would not be a smaller vocabulary -- it would be a wrong one.
    """
    assert "orientation" in ANSWER_KEYS
    assert set(ANSWER_VALUES["orientation"]) == {"portrait", "landscape"}


def test_every_answer_combination_maps_to_exactly_one_profile():
    """The whole enumerated space, iterated rather than sampled.

    32 answer combinations (the spike's 16 states x 2 orientations) produce
    profiles covering exactly the 16-row cross product of the four `Literal`
    domains. That equality is what makes the spike's table and this code one
    artefact instead of two.
    """
    combos = list(itertools.product(*(ANSWER_VALUES[k] for k in ANSWER_KEYS)))
    assert len(combos) == 32

    produced: dict[tuple[str, ...], tuple[object, ...]] = {}
    for combo in combos:
        answers = dict(zip(ANSWER_KEYS, combo))
        profile = derive_profile(answers)
        assert isinstance(profile, PrinterProfile)
        produced[combo] = (
            profile.flip_axis,
            profile.output_face,
            profile.feed_edge,
            profile.reverse_stack,
        )

    expected = set(
        itertools.product(
            ("long", "short"), ("up", "down"), ("top", "bottom"), (True, False)
        )
    )
    assert set(produced.values()) == expected, "every row of the spike table is producible"

    # 32 answers -> 16 profiles, so each profile is reached exactly twice: once
    # per orientation, from opposite `back_orientation` readings. Not a
    # collision -- it is the flip rule, and asserting the count pins it.
    counts: dict[tuple[object, ...], int] = {}
    for value in produced.values():
        counts[value] = counts.get(value, 0) + 1
    assert set(counts.values()) == {2}


# -- the flip rule, which the spec got backwards ------------------------


@pytest.mark.parametrize(
    "orientation,paper_pt",
    [("portrait", PORTRAIT_PT), ("landscape", LANDSCAPE_PT)],
)
@pytest.mark.parametrize("observed", ["upright", "inverted"])
def test_derived_profile_reproduces_what_the_operator_saw(
    orientation, paper_pt, observed
):
    """The round trip that makes the derivation meaningful.

    The operator reports what the backs did on paper of a known orientation.
    Feed that report to `derive_profile`, hand the profile to `plan_passes`
    for the *same* paper, and the plan must ask for a half turn exactly when
    the operator saw one needed.

    This is the assertion the F2 spec's formula fails: it derives
    `flip_axis = "long"` from `inverted` regardless of orientation, which is
    correct for landscape and exactly backwards for portrait.
    """
    profile = derive_profile(_answers(orientation=orientation, back_orientation=observed))
    plan = _sheet_plan(paper_pt)
    passes = plan_passes(plan, profile)
    back = next(p for p in passes if p.side == "back")

    assert back.rotate_backs is (observed == "inverted")


def test_the_same_observation_gives_opposite_flip_axis_per_orientation():
    """Stated directly, because it is the finding and not a side effect."""
    portrait = derive_profile(_answers(orientation="portrait", back_orientation="inverted"))
    landscape = derive_profile(_answers(orientation="landscape", back_orientation="inverted"))

    assert portrait.flip_axis != landscape.flip_axis
    assert portrait.flip_axis == "short"
    assert landscape.flip_axis == "long"


def test_flip_axis_is_the_paper_s_vertical_edge_when_backs_came_out_upright():
    """`upright` means the machine already agrees with the geometry."""
    for orientation, paper_pt in (("portrait", PORTRAIT_PT), ("landscape", LANDSCAPE_PT)):
        profile = derive_profile(_answers(orientation=orientation, back_orientation="upright"))
        assert profile.flip_axis == duplex_flip_edge(paper_pt)


# -- the other three derivations ---------------------------------------


def test_stack_order_reversed_sets_reverse_stack():
    assert derive_profile(_answers(stack_order="reversed")).reverse_stack is True
    assert derive_profile(_answers(stack_order="same")).reverse_stack is False


def test_output_face_and_feed_edge_are_carried_through_unchanged():
    """Reported observations, not derivations -- they must survive verbatim."""
    for face in ("up", "down"):
        for edge in ("top", "bottom"):
            profile = derive_profile(_answers(output_face=face, feed_edge=edge))
            assert (profile.output_face, profile.feed_edge) == (face, edge)


def test_a_measured_profile_is_distinguishable_from_a_typed_one():
    """`calibration_version=1` is the only thing telling them apart.

    F1's editor writes 0. If this ever matched, a profile somebody typed from
    memory would be indistinguishable from one measured with a ruler.
    """
    assert derive_profile(_answers()).calibration_version == 1
    assert derive_profile(_answers()).version == 1


def test_offsets_and_imageable_area_are_passed_through():
    profile = derive_profile(
        _answers(),
        imageable_area_pt=(1.0, 2.0, 3.0, 4.0),
        back_offset_pt=(-2.5, 0.75),
        calibrated_at="2026-09-21T00:00:00Z",
    )
    assert profile.imageable_area_pt == (1.0, 2.0, 3.0, 4.0)
    assert (profile.back_offset_x_pt, profile.back_offset_y_pt) == (-2.5, 0.75)
    assert profile.calibrated_at == "2026-09-21T00:00:00Z"


def test_it_is_pure_no_clock_is_read():
    """`calibrated_at` defaults to empty rather than to now().

    Passed in rather than read so the exhaustive test above can compare whole
    profiles; a clock inside would make two identical answer sets disagree.
    """
    assert derive_profile(_answers()).calibrated_at == ""


# -- refusals -----------------------------------------------------------


def test_a_missing_answer_is_refused_by_name():
    answers = _answers()
    del answers["feed_edge"]
    with pytest.raises(ValueError, match="missing"):
        derive_profile(answers)


def test_every_missing_answer_is_named_at_once():
    """One trip back to the operator, not one per gap."""
    answers = {"output_face": "up"}
    with pytest.raises(ValueError) as excinfo:
        derive_profile(answers)
    message = str(excinfo.value)
    for key in ("feed_edge", "stack_order", "back_orientation", "orientation"):
        assert key in message


def test_an_unknown_answer_is_refused_rather_than_ignored():
    with pytest.raises(ValueError, match="unknown"):
        derive_profile(_answers(paper_weight="heavy"))


def test_a_value_outside_the_domain_is_refused_with_the_domain_named():
    with pytest.raises(ValueError) as excinfo:
        derive_profile(_answers(feed_edge="sideways"))
    message = str(excinfo.value)
    assert "feed_edge" in message
    assert "top" in message and "bottom" in message


def test_guessing_is_never_the_fallback():
    """The reason the refusals above exist, stated once.

    A calibration that quietly fills in a missing answer is a preset wearing a
    measurement's `calibration_version`, and nothing downstream can tell.
    """
    for key in ANSWER_KEYS:
        answers = _answers()
        del answers[key]
        with pytest.raises(ValueError):
            derive_profile(answers)


# -- helpers ------------------------------------------------------------


def _sheet_plan(paper_pt: tuple[float, float]):
    """The smallest plan `plan_passes` will accept, for the round trip above.

    Built the way `tests/test_printing.py` builds one, so the round trip is
    exercised against the real plan shape rather than a stand-in.
    """
    from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side

    def side() -> Side:
        page = OutputPage(
            source_ref=None,
            placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
            is_filler=True,
        )
        return Side(pages=(page,))

    return SheetPlan(
        sheets=[Sheet(index=i, front=side(), back=side()) for i in range(2)],
        paper_pt=paper_pt,
        warnings=[],
    )


# -- the bridge to the printed half -------------------------------------
#
# `calibration_sheet` and `calibration` are deliberately kept apart: the sheet
# may not import `profiles`, and a guard in `tests/test_calibration_sheet.py`
# enforces it, because a target produced by the code it checks is evidence of
# nothing. The cost of that independence is silent drift -- the sheet could
# stop asking a question and nothing above would fail. These tests are the
# bridge, walked in both directions against the real rendered PDF.


def _cover_text(path) -> str:
    """The cover page's text, with runs of whitespace collapsed.

    The sheet aligns its columns with runs of spaces and the extractor decides
    its own spacing, so comparing raw text would assert a fact about pdfium
    rather than about the questions.
    """
    import re as _re

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        raw = document[0].get_textpage().get_text_range()
    finally:
        document.close()
    return _re.sub(r"[ \t]+", " ", raw)


@pytest.fixture(scope="module")
def covers(tmp_path_factory):
    """The cover page of both real orientations, rendered once.

    Rendered here rather than read from `docs/calibration/`: the committed
    artifacts have their own currency guard, and a bridge test should track the
    generator rather than a file that might be stale.
    """
    from deckle.core.calibration_sheet import make_calibration_pdf

    out = tmp_path_factory.mktemp("calibration")
    covers = {}
    for orientation, landscape in (("portrait", False), ("landscape", True)):
        path = out / f"{orientation}.pdf"
        make_calibration_pdf(str(path), landscape=landscape)
        covers[orientation] = _cover_text(path)
    return covers


def test_every_answer_this_module_needs_is_asked_for_on_paper(covers):
    """Forward direction: no answer key is uncollectable.

    A key here with no question on the sheet would be one the wizard must
    invent, and inventing one is how a calibration becomes a preset wearing
    `calibration_version=1`.
    """
    from deckle.core.calibration import SHEET_MARKERS

    for orientation, cover in covers.items():
        for key in ANSWER_KEYS:
            assert key in SHEET_MARKERS, f"{key} has no declared sheet marker"
            assert SHEET_MARKERS[key] in cover, (
                f"the {orientation} sheet does not ask for {key} "
                f"(looked for {SHEET_MARKERS[key]!r})"
            )


def test_the_paper_asks_for_nothing_this_module_cannot_consume(covers):
    """Reverse direction, and the one that catches a sixth question.

    Every `-> field` marker the sheet prints must be one some answer key
    resolves, or be a measurement this module takes as a parameter rather than
    an answer. A new marker appearing on the cover fails here rather than
    being quietly collected and dropped.
    """
    import re

    from deckle.core.calibration import SHEET_MARKERS

    # The two the sheet resolves that are not answers: they are passed to
    # `derive_profile` as `back_offset_pt`, measured with a ruler rather than
    # chosen from a domain.
    measured = {"back_offset_x_pt", "back_offset_y_pt"}
    answered = {marker.removeprefix("-> ") for marker in SHEET_MARKERS.values()}

    for orientation, cover in covers.items():
        # Only `-> field` markers, not question D's `front 1 -> back ____`
        # blanks: every profile field this sheet resolves is snake_case, and
        # the grid's blanks are single words. Keying on the underscore is what
        # separates a marker from a fill-in.
        printed = {
            token for token in re.findall(r"->\s*([a-z][a-z0-9_]*)", cover)
            if "_" in token
        }
        unexpected = printed - answered - measured
        assert not unexpected, (
            f"the {orientation} sheet asks for {sorted(unexpected)}, which "
            "nothing in this module consumes"
        )


def test_the_sheet_and_the_derivation_agree_on_the_orientation_question(covers):
    """The fifth key exists because of Finding C; the sheet must carry it.

    If the sheet ever stopped naming its own orientation, `back_orientation`
    would become unreadable -- not merely unvalidated -- because the same
    observation means opposite things on the two papers.
    """
    for cover in covers.values():
        assert "PORTRAIT" in cover and "LANDSCAPE" in cover
        assert "portrait file AND the landscape file" in cover
