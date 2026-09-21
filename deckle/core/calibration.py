"""The pure half of the calibration wizard: answers in, a profile out.

The printed half is :mod:`deckle.core.calibration_sheet` -- the duplex target
an operator prints and reads. This module is the other end: it turns what they
wrote down into a :class:`~deckle.core.profiles.PrinterProfile`.

**Never imports Qt**, and ``tests/test_core_purity.py`` enforces that in a
child process. Nothing here reads a clock or touches the filesystem either:
``calibrated_at`` is passed in rather than taken from ``datetime.now`` so the
exhaustive mapping test can compare whole profiles, and so two runs of the same
answers cannot disagree.

:func:`derive_profile` is **total over the enumerated answer space**
(:data:`ANSWER_KEYS` x :data:`ANSWER_VALUES`, written out in
``docs/spikes/calibration-state-space.md``). Total means the exhaustive test
can iterate that space rather than sample it, and assert that every row of the
spike's 16-state table is producible.

Why this is a function and not a dict literal
---------------------------------------------

A report is an observation; a profile field is a correction. The operator says
"the backs came out upside down"; the profile says "turn every back 180 degrees
before printing it". Those are different statements, and the mapping between
them is the thing the spike exists to pin down. ``stack_order`` and
``back_orientation`` are therefore named for **what the user reports**, not for
the fields they end up setting.

The orientation field, and why the F2 spec is short one answer
--------------------------------------------------------------

``docs/specs/2026-09-04-roadmap/F2-calibration-spike.md`` gives a four-key
vocabulary and derives ``flip_axis = "long" if back_orientation == "inverted"``,
on the stated grounds that ``plan_passes`` reads
``rotate_backs = profile.flip_axis == "long"``.

**It has not read that since 2026-08-06.** The rule is now::

    rotate_backs = profile.flip_axis != duplex_flip_edge(plan.paper_pt)

``flip_axis`` is a measured fact about the *machine* -- which named edge the
operator turns the stack about. ``duplex_flip_edge`` is a geometric fact about
the *job* -- which named edge is that sheet's vertical one. The backs need a
half turn exactly when the two disagree, and which named edge is vertical
inverts between portrait and landscape.

So "the backs came out upside down" means **opposite things on the two
orientations**, and cannot be converted into ``flip_axis`` without knowing
which paper it was observed on. The spec's formula is right for landscape and
exactly backwards for portrait -- the failure mode its own source module
describes as "a whole run of ruined paper that nothing on screen reports".

``orientation`` is therefore a fifth answer key. It widens the *record*, not
the question count: the operator judges nothing extra, because
``calibration_sheet`` already prints both orientations and asks which one is in
their hand (its question G). See ``docs/spikes/calibration-state-space.md``,
Finding C.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from deckle.core.profiles import PrinterProfile

#: The answers a completed calibration sheet yields, in the order the sheet
#: asks for them. Five, not the spec's four -- see the module docstring.
ANSWER_KEYS: tuple[str, ...] = (
    "output_face",
    "feed_edge",
    "stack_order",
    "back_orientation",
    "orientation",
)

#: What each answer may be. The exhaustive test iterates the cross product of
#: these, so adding a value here widens the proven space rather than escaping
#: it.
ANSWER_VALUES: dict[str, tuple[str, ...]] = {
    "output_face": ("up", "down"),
    "feed_edge": ("top", "bottom"),
    "stack_order": ("same", "reversed"),
    "back_orientation": ("upright", "inverted"),
    "orientation": ("portrait", "landscape"),
}

#: Which named edge is the sheet's vertical one, by orientation.
#:
#: The same fact :func:`deckle.core.printing.duplex_flip_edge` computes from a
#: paper size, stated here against the operator's word for the sheet in their
#: hand. Not imported from there because this module is handed an orientation,
#: not a paper size: the sheet says "PORTRAIT / LANDSCAPE" on its cover and
#: never quotes millimetres, so converting through a paper size would invent a
#: precision the answer does not have. The round-trip test in
#: ``tests/test_calibration.py`` pins the two against each other.
_VERTICAL_EDGE: dict[str, Literal["long", "short"]] = {
    "portrait": "long",
    "landscape": "short",
}

_OTHER_EDGE: dict[str, Literal["long", "short"]] = {"long": "short", "short": "long"}


def _validate(answers: Mapping[str, str]) -> None:
    """Refuse an answer set that is incomplete, padded, or out of domain.

    Every gap is named in one message rather than one per call: the person
    holding the sheet is at a printer, and sending them back four times for
    four fields is the kind of thing that ends with a guess written in.

    :raises ValueError: with the offending keys or values named.
    """
    missing = [key for key in ANSWER_KEYS if key not in answers]
    if missing:
        raise ValueError(f"calibration answers are missing {', '.join(missing)}")

    unknown = sorted(set(answers) - set(ANSWER_KEYS))
    if unknown:
        raise ValueError(f"unknown calibration answer(s): {', '.join(unknown)}")

    for key in ANSWER_KEYS:
        if answers[key] not in ANSWER_VALUES[key]:
            raise ValueError(
                f"calibration answer {key}={answers[key]!r} is not one of "
                f"{', '.join(ANSWER_VALUES[key])}"
            )


def derive_profile(
    answers: Mapping[str, str],
    *,
    imageable_area_pt: tuple[float, float, float, float] = (18.0, 18.0, 18.0, 18.0),
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
    calibrated_at: str = "",
) -> PrinterProfile:
    """The profile a set of calibration answers describes.

    Pure: no Qt, no I/O, no clock.

    ``calibration_version=1`` is the committed value, and it is the **only**
    thing distinguishing a profile measured against a printed target from one
    typed into F1's editor, which writes ``0``. Nothing else in the record
    differs, so a consumer that wants to know whether a ruler was involved has
    this field and nothing else.

    :param answers: one value per :data:`ANSWER_KEYS`.
    :param imageable_area_pt: the margins to record, unmeasured by this
        function -- it derives duplex behaviour, not page geometry.
    :param back_offset_pt: ``(x, y)`` from the sheet's registration-cross
        question, in points.
    :param calibrated_at: an ISO timestamp, or ``""``. Passed in rather than
        read so this function stays comparable across calls.
    :returns: the profile those answers describe.
    :raises ValueError: a key is missing, unknown, or carries a value outside
        :data:`ANSWER_VALUES`. Guessing a missing answer is how a calibration
        silently becomes a preset -- and one wearing
        ``calibration_version=1``, which says a human measured it.
    """
    _validate(answers)

    # The operator turned the stack about *some* edge and reported what landed.
    # `upright` means the edge they used was already the sheet's vertical one,
    # so the machine agrees with the geometry and no correction is wanted.
    # `inverted` means it was the other edge, and `plan_passes` will ask for a
    # half turn because `flip_axis` then disagrees with `duplex_flip_edge`.
    vertical = _VERTICAL_EDGE[answers["orientation"]]
    flip_axis = vertical if answers["back_orientation"] == "upright" else _OTHER_EDGE[vertical]

    # Sheet 1's back landed on the last sheet through the printer, so the back
    # pass has to be fed in the opposite order to restore the pairing.
    reverse_stack = answers["stack_order"] == "reversed"

    return PrinterProfile(
        version=1,
        flip_axis=flip_axis,
        output_face=cast(Literal["up", "down"], answers["output_face"]),
        feed_edge=cast(Literal["top", "bottom"], answers["feed_edge"]),
        reverse_stack=reverse_stack,
        imageable_area_pt=imageable_area_pt,
        calibrated_at=calibrated_at,
        calibration_version=1,
        back_offset_x_pt=back_offset_pt[0],
        back_offset_y_pt=back_offset_pt[1],
    )
