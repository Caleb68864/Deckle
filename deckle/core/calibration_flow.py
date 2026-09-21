"""The calibration wizard's decisions, with no Qt in them.

:mod:`deckle.core.calibration_sheet` prints the target, this module reads what
the operator wrote on it, and :mod:`deckle.core.calibration` turns that into a
:class:`~deckle.core.profiles.PrinterProfile`. Splitting the wizard this way is
the same split :mod:`deckle.core.print_session` has against
``deckle/app/views/print_dialog.py``: the screens are Qt, the judgements are
not, and the judgements are where the bugs would be.

**Never imports Qt** -- ``tests/test_core_purity.py`` enforces it.

What the wizard has to decide, and why each is here
---------------------------------------------------

*Which reload happened.* The sheet's question D gives the back number that
landed on front 1. On a four-sheet run only two answers are explicable, and
:func:`stack_order_from_back_number` refuses the rest rather than rounding them
to the nearer one. That refusal is the whole reason this is a function: a
printer that reorders in some third way must not be silently recorded as one of
the two Deckle models.

*What the two orientations together mean.* The sheet is printed twice, and
Finding C in ``docs/spikes/calibration-state-space.md`` is why: the same
observation means opposite things on portrait and landscape paper. Two runs
therefore yield two independent readings of the same machine, and
:func:`reconcile` requires them to agree. A disagreement is not a tie to be
broken -- it means one of the two sheets was read wrong, and the operator is
the only one who can say which.
"""

from __future__ import annotations

from collections.abc import Mapping

from deckle.core.calibration import ANSWER_VALUES, derive_profile
from deckle.core.calibration_sheet import SHEETS
from deckle.core.profiles import PrinterProfile


class UnexplainableResult(Exception):
    """The sheets say something Deckle's two-state model cannot express.

    Raised rather than resolved. Deckle models a reload as either preserving
    sheet order or reversing it; a printer that does something else produces a
    reading that is neither, and the honest response is to save nothing and say
    so. Rounding it to the nearer of the two would write a profile that looks
    measured and is wrong, and `calibration_version=1` would vouch for it.
    """


def back_number_choices(sheets: int = SHEETS) -> tuple[str, ...]:
    """The answers question D can take, as strings, for a run of `sheets`.

    Offered in full rather than as just the two explicable ones: an operator
    whose printer did something else has to be able to say so, and a picker
    listing only "1" and "4" would force them to lie.
    """
    return tuple(str(n) for n in range(1, sheets + 1))


def stack_order_from_back_number(back_number: int, sheets: int = SHEETS) -> str:
    """What the back landing on front 1 says about the reload.

    :param back_number: the number printed on the back of the sheet whose
        front says 1 -- the sheet's question D.
    :param sheets: how many sheets the run covered.
    :returns: ``"same"`` or ``"reversed"``, a value of
        ``ANSWER_VALUES["stack_order"]``.
    :raises UnexplainableResult: for any other reading. See the class.
    """
    if back_number == 1:
        # Back 1 landed on front 1: the stack went back in the order it came
        # out, so the back pass is fed in the same order.
        return "same"
    if back_number == sheets:
        # Back 1 landed on the last sheet through, so the stack was inverted
        # on reload and the back pass must be fed in reverse to re-pair them.
        return "reversed"
    raise UnexplainableResult(
        f"front 1 came back with back {back_number} on a {sheets}-sheet run. "
        f"Deckle models a reload as preserving order (back 1) or reversing it "
        f"(back {sheets}), and this is neither, so nothing has been saved. "
        f"Please report it with the sheets in front of you."
    )


def answers_for(
    *,
    orientation: str,
    output_face: str,
    feed_edge: str,
    back_number: int,
    back_orientation: str,
    sheets: int = SHEETS,
) -> dict[str, str]:
    """One orientation's readings, as an answer set `derive_profile` accepts.

    Takes the operator's words and does the one conversion they should not have
    to do in their head -- the back number into a stack order. Everything else
    is carried straight through, because a question that needs interpreting at
    the keyboard is a question the sheet asked badly.

    :raises UnexplainableResult: the back number is not explicable.
    :raises ValueError: any other value is outside its domain, named by
        :func:`deckle.core.calibration.derive_profile`'s validator.
    """
    return {
        "orientation": orientation,
        "output_face": output_face,
        "feed_edge": feed_edge,
        "stack_order": stack_order_from_back_number(back_number, sheets),
        "back_orientation": back_orientation,
    }


def reconcile(
    portrait: Mapping[str, str],
    landscape: Mapping[str, str],
    *,
    imageable_area_pt: tuple[float, float, float, float] = (18.0, 18.0, 18.0, 18.0),
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
    calibrated_at: str = "",
) -> PrinterProfile:
    """One profile from both orientations, or a refusal to guess.

    The sheet is printed twice because the flip rule inverts between the two
    (Finding C). That makes the second run a genuine check on the first rather
    than a repetition: both describe the same machine, so both must derive the
    same profile. When they do, the result is a measurement taken twice; when
    they do not, one of the sheets was misread and this function will not
    choose which.

    Only `back_orientation` can disagree in a way that is *about* the flip
    rule; the other three are direct observations that should not vary with
    paper shape at all, and a difference in them means the two runs were done
    on different settings.

    :raises ValueError: the two orientations describe different printers, with
        the differing fields named.
    :raises UnexplainableResult: propagated from either answer set.
    """
    if portrait.get("orientation") != "portrait":
        raise ValueError("the first answer set is not the portrait one")
    if landscape.get("orientation") != "landscape":
        raise ValueError("the second answer set is not the landscape one")

    left = derive_profile(
        portrait,
        imageable_area_pt=imageable_area_pt,
        back_offset_pt=back_offset_pt,
        calibrated_at=calibrated_at,
    )
    right = derive_profile(
        landscape,
        imageable_area_pt=imageable_area_pt,
        back_offset_pt=back_offset_pt,
        calibrated_at=calibrated_at,
    )

    differing = [
        field
        for field in ("flip_axis", "output_face", "feed_edge", "reverse_stack")
        if getattr(left, field) != getattr(right, field)
    ]
    if differing:
        raise ValueError(
            "the portrait and landscape sheets describe different printers "
            f"({', '.join(differing)}). Both were printed on the same machine, "
            "so one of them was read wrong -- check the two sheets against the "
            "cover's questions before saving anything."
        )

    return left


#: The screens, in order, as `(key, heading, body)`.
#:
#: Declared here rather than in the Qt shell so the order and the wording are
#: testable, and so a screen cannot be added to the UI without appearing in a
#: test that walks this tuple. The shell renders them; it does not decide them.
SCREENS: tuple[tuple[str, str, str], ...] = (
    (
        "print_portrait",
        "Print the portrait sheet",
        "Deckle has written the calibration document. Open it and follow the "
        "instructions on its first page: print the front pass, turn the stack "
        "over the way you normally would, then print the back pass. Do not "
        "reorder or rotate the stack by hand.",
    ),
    (
        "read_portrait",
        "Read the portrait sheets",
        "Answer what you SEE. Do not correct anything and do not reprint to "
        "tidy up the result -- a wrong-looking result is the result.",
    ),
    (
        "print_landscape",
        "Print the landscape sheet",
        "Now the same again with the landscape document. The rule that turns "
        "these answers into a profile inverts between the two orientations, so "
        "one of them answers only half of it.",
    ),
    (
        "read_landscape",
        "Read the landscape sheets",
        "The same four questions. If your answers here disagree with the "
        "portrait ones, Deckle will say so rather than pick one.",
    ),
    (
        "measure",
        "Measure the printable border",
        "Measure the unprinted border on any of the four sheets, on each edge.",
    ),
    (
        "save",
        "Save",
        "Both orientations agreed. Saving records this as a measured profile, "
        "which is what tells it apart from one typed in by hand.",
    ),
)

#: The questions each reading screen asks, as `(answer_key, label, choices)`.
#:
#: The labels are the sheet's own words. An operator holding the paper should
#: not have to translate between what it asked and what the dialog asks.
READING_QUESTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "output_face",
        "After the front pass, the printed side of the sheets in the output tray faced:",
        ANSWER_VALUES["output_face"],
    ),
    (
        "feed_edge",
        "The edge of the paper that went into the printer first was the sheet's:",
        ANSWER_VALUES["feed_edge"],
    ),
    (
        "back_number",
        "On the sheet whose FRONT says 1, the BACK number that landed on it:",
        back_number_choices(),
    ),
    (
        "back_orientation",
        "Hold that sheet so its FRONT reads upright and turn it over "
        "left-to-right, like a page in a book. The BACK reads:",
        ANSWER_VALUES["back_orientation"],
    ),
)
