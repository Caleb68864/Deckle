"""Paper: stating it by weight, and sizing a gathering from it.

Deckle asked for paper thickness as a caliper. Nobody knows their paper's
caliper; everybody has its weight printed on the ream wrapper. This
module turns the number a binder has into the number the imposer needs,
and then answers the question that follows from it -- how many sheets
should a gathering hold?

Qt-free, so the CLI reaches it too: a binder scripting a job should not
have to compute a caliper by hand either.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PT_PER_MM = 72.0 / 25.4


# US basis weights are pounds per ream of a grade's *own* basis size, so
# "20 lb" means nothing without knowing the grade -- it is 75gsm as bond,
# 30gsm as text and 54gsm as cover. Derived from the basis sizes rather
# than tabulated, so there is one place to check rather than five numbers
# to have transcribed correctly:
#
#     gsm = lb * 453.592 / (500 * width_in * height_in * 0.00064516)
GRADE_BASIS_SIZES_IN = {
    "bond": (17.0, 22.0),
    "text": (25.0, 38.0),
    "cover": (20.0, 26.0),
    "index": (25.5, 30.5),
    "tag": (24.0, 36.0),
}

_SQ_M_PER_SQ_IN = 0.00064516
_G_PER_LB = 453.592
_SHEETS_PER_REAM = 500


def gsm_from_pounds(pounds: float, grade: str) -> float:
    """Grammage from a US basis weight.

    :param pounds: the number printed on the wrapper.
    :param grade: which basis size it is quoted against.
    :returns: grams per square metre.
    :raises ValueError: an unknown grade, naming the accepted ones. The
        grade is required rather than defaulted because a silent default
        would be wrong by up to 2.5x for anyone whose paper is quoted
        against a different basis size, and the answer would look
        entirely reasonable.
    """
    try:
        width_in, height_in = GRADE_BASIS_SIZES_IN[grade]
    except KeyError:
        raise ValueError(
            f"unknown paper grade {grade!r}: expected one of "
            + ", ".join(sorted(GRADE_BASIS_SIZES_IN))
        ) from None
    ream_area_m2 = _SHEETS_PER_REAM * width_in * height_in * _SQ_M_PER_SQ_IN
    return pounds * _G_PER_LB / ream_area_m2


# Bulk -- specific volume in cm3/g -- is what separates two papers of the
# same grammage, and the reason weight alone cannot give a caliper. A
# bulky book paper and a coated one at 100gsm differ by nearly a factor
# of two in thickness.
#
# It varies about +/-10% between manufacturers, so a derived caliper is an
# *estimate*. `schedule.spine_width_pt` already reports a range rather
# than false precision for the same reason, and anything surfacing these
# numbers should say so.
PAPER_BULK = {
    "coated": 0.90,
    "laser": 1.25,
    "copier": 1.30,
    "offset": 1.30,
    "bulky": 1.70,
}


def caliper_pt_from_gsm(gsm: float, paper_type: str) -> float:
    """One sheet's thickness in points, from grammage and paper type.

    :param gsm: grams per square metre.
    :param paper_type: which bulk to apply -- see :data:`PAPER_BULK`.
    :returns: the caliper in PDF points.
    :raises ValueError: an unknown paper type, naming the accepted ones.
    """
    try:
        bulk = PAPER_BULK[paper_type]
    except KeyError:
        raise ValueError(
            f"unknown paper type {paper_type!r}: expected one of "
            + ", ".join(sorted(PAPER_BULK))
        ) from None
    return (gsm * bulk / 1000.0) * PT_PER_MM


@dataclass(frozen=True)
class PaperPreset:
    """A named paper a binder can pick without measuring anything.

    Carries the weight and type rather than a caliper, so the number it
    reports comes from the same formula as a custom entry -- a
    hand-written caliper in this table could drift from
    :func:`caliper_pt_from_gsm` and nobody would notice.

    :ivar name: what the dropdown shows.
    :ivar gsm: grammage.
    :ivar paper_type: the bulk to apply.
    """

    name: str
    gsm: float
    paper_type: str

    @property
    def caliper_pt(self) -> float:
        """This paper's estimated thickness, in points."""
        return caliper_pt_from_gsm(self.gsm, self.paper_type)


# Ordered thinnest first: the order is the only thing that makes the
# dropdown scannable.
PAPER_PRESETS = (
    PaperPreset("80gsm copier", 80, "copier"),
    PaperPreset("90gsm laser", 90, "laser"),
    PaperPreset("100gsm offset", 100, "offset"),
    PaperPreset("120gsm cartridge", 120, "offset"),
    PaperPreset("160gsm card", 160, "offset"),
)


# Below this, fore-edge creep is invisible and needs no trimming. Shared
# with `schedule._creep_note`, which imports it rather than repeating the
# literal -- one physical threshold expressed twice is the duplication
# this codebase has already been bitten by four times over one platform
# ladder.
CREEP_INVISIBLE_PT = 1.0


def creep_pt(sheets_per_signature: int, caliper_pt: float) -> float:
    """How far the innermost leaf of a nested gathering protrudes, in points.

    ``(sheets - 1) * caliper``: the outermost leaf is pushed out by
    nothing, so a gathering of one sheet creeps by nothing.

    :param sheets_per_signature: sheets nested into one gathering.
    :param caliper_pt: one sheet's thickness.
    :returns: the protrusion in points, ``0.0`` when either input is
        non-positive -- there is nothing to estimate, and a negative answer
        would read as "the fore-edge is inset".
    """
    if sheets_per_signature <= 1 or caliper_pt <= 0.0:
        return 0.0
    return (sheets_per_signature - 1) * caliper_pt


def creep_tolerance_pt(trim_pt: float = 0.0) -> float:
    """How much creep this job can absorb before it is worth mentioning.

    A planned fore-edge trim absorbs creep, so someone who is going to
    plough the block can carry far more of it than someone who is not;
    without a trim the threshold is the point below which it is simply
    invisible.

    :param trim_pt: the planned fore-edge trim, or ``0.0`` for none.
    :returns: the tolerance in points.
    """
    return trim_pt if trim_pt > 0 else CREEP_INVISIBLE_PT


def creep_is_worth_reporting(
    sheets_per_signature: int, caliper_pt: float, trim_pt: float = 0.0
) -> bool:
    """Whether this gathering's creep should be said out loud.

    **The single predicate.** Three places used to answer this and no two
    agreed: ``layout._creep_advisory`` judged the *requested*
    ``sheets_per_signature`` even when ``signature_lengths`` or
    ``blank_mode="balanced"`` had overridden it, ``schedule._creep_note``
    ignored ``trim_pt`` entirely, and the two used different operators at
    the boundary -- so a 0.25pt stock in 5-sheet gatherings, creeping
    exactly ``CREEP_INVISIBLE_PT``, produced a warning in the schedule and
    silence in the layout.

    **Strictly greater than.** Creep exactly equal to the tolerance is
    absorbed, not reported -- which is the reading
    :func:`suggest_sheets_per_signature` already had built into
    ``int(tolerance // caliper_pt) + 1``, and a suggestion that recommended
    a gathering its own advisory then warned about would be the fourth
    opinion this consolidation exists to remove.

    :param sheets_per_signature: sheets in the gathering **as built**, not
        as requested.
    :param caliper_pt: one sheet's thickness.
    :param trim_pt: the planned fore-edge trim, or ``0.0``.
    :returns: whether to report.
    """
    return creep_pt(sheets_per_signature, caliper_pt) > creep_tolerance_pt(trim_pt)

# A folded gathering of n nested sheets has 2n layers of paper at the
# fold. Past about 1.8mm it stops folding cleanly and is awkward to sew.
#
# Calibrated against traditional signature sizes rather than chosen to
# produce them: 80gsm office paper falls out at 8 sheets, a 32-page
# gathering, which is the standard trade signature. Had it produced 7 or
# 11 the constant would be wrong. No test can settle this number -- it is
# a judgement, recorded in
# `docs/plans/2026-08-08-paper-and-recovery-design.md` so it can be argued
# with rather than discovered here.
FOLD_BULK_LIMIT_MM = 1.8


@dataclass(frozen=True)
class SignatureSuggestion:
    """How big a gathering of a given paper should be.

    :ivar sheets: pieces of paper per gathering.
    :ivar pages: the same in pages, which is what a binder counts in --
        one folded sheet is four.
    :ivar creep_pt: how far the innermost leaf will protrude at the
        fore-edge, by the same formula the schedule prints. **No caller
        in Deckle reads it** -- both consumers of a suggestion take
        ``sheets``, ``pages`` and ``limited_by``. Kept, and deliberately
        not shown: at the *suggested* number of sheets the creep is
        within tolerance by construction, so putting it in the panel's
        sentence would be reporting a number the binder has nothing to do
        about. The creep worth showing is the one at the size actually
        set, and ``core.layout`` already warns with that.
    :ivar limited_by: which constraint bound the answer. Reported rather
        than merely used, because the remedy differs: ``"creep"`` means
        plan a trim, ``"fold"`` means this paper is thick.
    """

    sheets: int
    pages: int
    creep_pt: float
    limited_by: Literal["creep", "fold"]


def suggest_sheets_per_signature(
    caliper_pt: float, trim_pt: float = 0.0
) -> SignatureSuggestion | None:
    """How many sheets a gathering of this paper should hold.

    Two constraints, and the answer is the smaller of them:

    - **Creep.** Nested sheets push each other outward at the fore-edge by
      ``(sheets - 1) * caliper``. The tolerance is ``trim_pt`` when the
      binder plans to plough the fore-edge, else the point below which it
      is invisible -- creep is *absorbed by trimming*, so someone who
      plans one can carry far more of it.
    - **Fold bulk.** ``2 * sheets * caliper`` at the spine, capped at
      :data:`FOLD_BULK_LIMIT_MM`.

    Creep alone is not enough and gives absurd answers: 80gsm paper with a
    quarter-inch trim tolerates 62 sheets before creep shows, and nobody
    hand-sews a 62-sheet gathering. The fold cap is what makes the answer
    physical.

    :param caliper_pt: one sheet's thickness, measured or estimated.
    :param trim_pt: the planned fore-edge trim, or ``0.0`` for none.
    :returns: the suggestion, or ``None`` when thickness is unset --
        silence is the honest answer without it, which is the contract
        :func:`deckle.core.schedule.spine_width_pt` already keeps.
    """
    if caliper_pt <= 0:
        return None
    tolerance = creep_tolerance_pt(trim_pt)
    by_creep = int(tolerance // caliper_pt) + 1
    by_fold = int((FOLD_BULK_LIMIT_MM * PT_PER_MM) // (2 * caliper_pt))
    sheets = max(1, min(by_creep, by_fold))
    return SignatureSuggestion(
        sheets=sheets,
        pages=sheets * 4,
        creep_pt=creep_pt(sheets, caliper_pt),
        limited_by="creep" if by_creep <= by_fold else "fold",
    )
