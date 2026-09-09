"""Turning one flag's text into a value.

Everything here is an ``argparse`` ``type=`` callable or a helper of one:
given the string a user typed, either return the value or raise
``argparse.ArgumentTypeError`` with a message naming what was expected.
No file is opened and nothing is printed -- a parse failure is argparse's
to report, on the flag it happened to, while the user is still looking at
what they typed.

This module imports nothing from ``deckle``. That is deliberate and worth
keeping: the unit table and the paper presets here are duplicated in
``deckle/app/views/layout_panel.py`` and are what spec M5 consolidates
into ``deckle.core.paper``, and a leaf module is the easiest thing to
lift out from under.
"""

from __future__ import annotations

import argparse
import re


LETTER_PT = (612.0, 792.0)
A4_PT = (595.28, 841.89)
LEGAL_PT = (612.0, 1008.0)

_PAPER_PRESETS = {
    "letter": LETTER_PT,
    "a4": A4_PT,
    "legal": LEGAL_PT,
}

_ACCEPTED_LENGTH_UNITS = ("in", "pt", "mm", "cm")

_UNIT_TO_PT = {
    "in": 72.0,
    "pt": 1.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
}

# A-9: unit is optional (a bare number means points), and an optional space
# is allowed between the number and the unit -- e.g. "18", "5cm", "3 mm".
_LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$", re.IGNORECASE)


def _parse_length_pt(value: str) -> float:
    """Parse a length to points.

    Accepts ``in``, ``pt``, ``mm``, or ``cm`` as the unit, with an optional
    space before it (``0.75in``, ``18pt``, ``5mm``, ``5 cm``). A bare
    number with no unit (``"18"``) is interpreted as points.
    """
    match = _LENGTH_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid length {value!r}: expected a number optionally followed "
            f"by a unit ({', '.join(_ACCEPTED_LENGTH_UNITS)}); a bare number "
            "is interpreted as points"
        )
    number, unit = match.groups()
    factor = _UNIT_TO_PT[unit.lower()] if unit else 1.0
    return float(number) * factor


def _parse_paper(value: str) -> tuple[float, float]:
    """A ``--paper`` value as ``(width_pt, height_pt)``.

    :param value: a preset name, or ``WxH`` with an optional unit.
    :returns: the dimensions in points.
    :raises argparse.ArgumentTypeError: unparseable, or a size no PDF can
        represent.
    """
    preset = _PAPER_PRESETS.get(value.lower())
    if preset is not None:
        return preset
    # `cm` belongs here for the same reason it is in `_ACCEPTED_LENGTH_UNITS`
    # and `_LENGTH_RE`: this parser's own error message lists it. A unit the
    # program advertises and then rejects reads as a typo in the user's
    # input rather than a gap in ours. The optional space matches
    # `_parse_length_pt` too -- "20 x 28 cm" is how a paper size is written
    # down. B25.
    match = re.match(
        r"^\s*([0-9]*\.?[0-9]+)\s*x\s*([0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$",
        value,
        re.IGNORECASE,
    )
    if match:
        w, h, unit = match.groups()
        factor = _UNIT_TO_PT[(unit or "pt").lower()]
        paper = (float(w) * factor, float(h) * factor)
        _reject_unprintable_paper(paper, value)
        return paper
    raise argparse.ArgumentTypeError(
        f"invalid paper {value!r}: expected a preset ({', '.join(_PAPER_PRESETS)}) "
        f"or WxH with an optional unit ({', '.join(_UNIT_TO_PT)}) -- e.g. 8.5x11in"
    )


# The PDF page-size limits pikepdf enforces, in points. Outside them there
# is no page to make, so there is no point imposing one.
MIN_PAPER_PT = 3.0
MAX_PAPER_PT = 14400.0


def _reject_unprintable_paper(paper: tuple[float, float], typed: str) -> None:
    """Refuse a sheet size no PDF can represent, while the user is still
    looking at what they typed.

    ``--paper 0x0`` was accepted here, imposed, and then failed inside
    pikepdf with "Page size must be between 3 and 14400 PDF units" -- a
    library traceback that names neither ``--paper`` nor the value the
    user gave, after doing all the work. The limit is real; the place to
    apply it is the boundary the value came in through.

    :param paper: the parsed dimensions, in points.
    :param typed: what the user actually wrote, for the message.
    :raises argparse.ArgumentTypeError: the size is outside PDF's range.
    """
    for length in paper:
        if not MIN_PAPER_PT <= length <= MAX_PAPER_PT:
            raise argparse.ArgumentTypeError(
                f"invalid paper {typed!r}: a PDF page must be between "
                f"{MIN_PAPER_PT:g} and {MAX_PAPER_PT:g}pt "
                f"({MIN_PAPER_PT / 72:.2g}in to {MAX_PAPER_PT / 72:g}in) on "
                f"each side; that is {paper[0]:g}x{paper[1]:g}pt"
            )


def _parse_paper_weight(text: str) -> tuple[float, str]:
    """A ream-wrapper weight, as ``(number, "gsm"|"lb")``.

    Deliberately does not convert here. Pounds need a grade to mean
    anything, and the grade is a separate argument, so this parses the
    shape and leaves the arithmetic to `_settings_from_args` where both
    are in hand.

    :param text: e.g. ``80gsm``, ``100 gsm``, ``24lb``.
    :returns: the number and its unit.
    :raises argparse.ArgumentTypeError: anything else, naming the forms
        that are accepted.
    """
    cleaned = text.strip().lower().replace(" ", "")
    for suffix in ("gsm", "lb", "lbs", "#"):
        if cleaned.endswith(suffix):
            number = cleaned[: -len(suffix)]
            try:
                value = float(number)
            except ValueError:
                break
            if value <= 0:
                break
            return (value, "gsm" if suffix == "gsm" else "lb")
    raise argparse.ArgumentTypeError(
        f"invalid paper weight {text!r}: expected a positive number with a "
        "unit, such as 80gsm or 24lb"
    )


def _parse_signature_lengths(value: str) -> tuple[int, ...]:
    """A ``--signatures`` value as the sheet count of each gathering.

    ``10,10,8`` -- the same shape Bookbinder JS asks for, because a binder
    who has used one should not have to learn a second notation for the
    same idea.

    :param value: comma-separated positive integers.
    :returns: the lengths, in binding order.
    :raises argparse.ArgumentTypeError: empty, non-numeric, or any value
        below one. Whether the lengths ADD UP to the document is checked
        later, by the imposer, because the sheet count is not known until
        the pages have been read.
    """
    items = [item.strip() for item in value.split(",")]
    if not value.strip() or any(not item.isdigit() for item in items):
        raise argparse.ArgumentTypeError(
            f"invalid signatures {value!r}: expected sheet counts separated "
            "by commas, such as 10,10,8"
        )
    lengths = tuple(int(item) for item in items)
    if any(length < 1 for length in lengths):
        raise argparse.ArgumentTypeError(
            f"invalid signatures {value!r}: every signature must hold at "
            "least one sheet"
        )
    return lengths


def _parse_four_insets(
    value: str, flag: str, order: str
) -> tuple[float, float, float, float]:
    """Four comma-separated lengths, for a flag that takes a set of insets.

    Shared by ``--crop``, ``--crop-even`` and ``--imageable-area``, which
    ask for the same four numbers in two different orders. The ``flag``
    and ``order`` are parameters rather than baked in because a message
    saying "invalid crop" under ``--imageable-area`` sends the reader to
    the wrong flag, and because the two orders genuinely differ -- a crop
    is ``left, bottom, right, top`` and a printer's imageable area is
    stored ``left, top, right, bottom``. Naming one order for both is how
    a head margin ends up applied to the tail.

    :param value: the raw flag text.
    :param flag: what to call it in the error, e.g. ``"crop"``.
    :param order: the four names, in order, for the error.
    :returns: the four lengths in points.
    :raises argparse.ArgumentTypeError: not four values, or any of them
        unparseable.
    """
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"invalid {flag} {value!r}: expected four insets -- {order} -- "
            "e.g. 0.5in,0.25in,0.5in,0.25in"
        )
    return tuple(_parse_length_pt(part) for part in parts)


def _parse_station_positions(value: str) -> tuple[float, ...]:
    """A ``--stations`` value as exact positions in points, tail upward.

    Comma-separated lengths, each with an optional unit --
    ``0.5in,2in,2.25in,9.5in`` for a two-tape sewing. Sorted and
    de-duplicated here, once, so nothing downstream has to decide what two
    identical stations mean.

    Whether the positions FIT the sheet is checked later, by
    :func:`deckle.core.marks.sewing_stations`, because the sheet height is
    not known until the paper is -- the same split ``--signatures`` makes
    for its sum.

    :param value: the raw flag text.
    :returns: the positions in points, ascending, without repeats.
    :raises argparse.ArgumentTypeError: empty, unparseable, or any value at
        or below zero.
    """
    parts = value.split(",")
    if not value.strip() or any(not part.strip() for part in parts):
        raise argparse.ArgumentTypeError(
            f"invalid stations {value!r}: expected positions separated by "
            "commas, such as 0.5in,2in,2.25in -- measured up from the tail"
        )
    positions = [_parse_length_pt(part) for part in parts]
    if any(position <= 0 for position in positions):
        raise argparse.ArgumentTypeError(
            f"invalid stations {value!r}: a station must sit above the tail "
            "edge, so every position must be greater than zero"
        )
    return tuple(sorted(set(positions)))


def _parse_crop(value: str) -> tuple[float, float, float, float]:
    """A ``--crop`` value as ``(left, bottom, right, top)`` insets in points.

    Four lengths, comma-separated, each with an optional unit --
    ``0.5in,0.25in,0.5in,0.25in`` or ``36,18,36,18``. Insets rather than a
    rectangle because one document can hold pages of different sizes, and
    a fixed rectangle would mean something different on each of them.

    Unsigned on purpose: a crop removes space, and the margin settings are
    what add it. A negative inset would place content outside its own page
    box, so ``_parse_length_pt``'s refusal of a minus sign is correct here
    rather than something to work around.

    :param value: the raw flag text.
    :returns: the four insets in points.
    :raises argparse.ArgumentTypeError: not four values, or any of them
        unparseable.
    """
    return _parse_four_insets(value, "crop", "left, bottom, right, top")


def _parse_imageable_area(value: str) -> tuple[float, float, float, float]:
    """An ``--imageable-area`` value as ``(left, top, right, bottom)`` margins.

    A different order from ``--crop`` because it is a different quantity:
    this is the non-printable border the printer imposes, and
    ``PrinterProfile.imageable_area_pt`` stores it ``left, top, right,
    bottom`` (see ``deckle.app.views.preview_view.imageable_rect_pt``,
    which draws it). Re-ordering it here to match ``--crop`` would make
    this flag disagree with the file it writes.
    """
    return _parse_four_insets(value, "imageable-area", "left, top, right, bottom")


def _parse_index_selection(
    value: str, *, noun: str, example: str, offset: int = 0
) -> list[int]:
    """Comma-separated numbers and inclusive ranges, as a list of indices.

    One grammar, two flags. ``--sheets`` counts from 0 because it names
    Deckle's own artefact (``Sheet.index``, the warnings, the schedule);
    ``--pages`` counts from 1 because it names the user's document and a
    person types what their PDF viewer shows. ``offset`` is what reconciles
    them: it is subtracted from every number, so the caller states the base
    once instead of every consumer remembering it.

    Order is preserved and repeats are kept, because
    :func:`deckle.core.export.export` documents both for its ``sheets``
    argument. A caller that does not care (``--pages`` sets flags, so it
    does not) may ignore that.

    Open-ended ranges (``2-``) are deliberately not accepted: neither the
    sheet count nor the page count is known when argparse runs.

    :param value: the raw flag text.
    :param noun: what the numbers name, for the messages.
    :param example: the forms that are accepted, for the messages.
    :param offset: the base the user counts from.
    :returns: the indices, in the order named.
    :raises argparse.ArgumentTypeError: empty, malformed, a range that runs
        backwards, or a number below ``offset``.
    """
    selection: list[int] = []
    items = [item.strip() for item in value.split(",")]
    if not value.strip() or any(not item for item in items):
        raise argparse.ArgumentTypeError(
            f"invalid {noun} {value!r}: expected {example}"
        )
    for item in items:
        bounds = [part.strip() for part in item.split("-")]
        if len(bounds) > 2 or any(not part.isdigit() for part in bounds):
            raise argparse.ArgumentTypeError(
                f"invalid {noun} {value!r}: {item!r} is not a {noun[:-1]} "
                "number or an inclusive range like 2-4"
            )
        start = int(bounds[0])
        end = int(bounds[-1])
        if start < offset or end < offset:
            raise argparse.ArgumentTypeError(
                f"invalid {noun} {value!r}: {noun} are numbered from {offset}"
            )
        if len(bounds) == 1:
            selection.append(start - offset)
            continue
        if end < start:
            raise argparse.ArgumentTypeError(
                f"invalid {noun} {value!r}: the range {item!r} runs backwards"
            )
        selection.extend(range(start - offset, end - offset + 1))
    return selection


def _parse_sheet_selection(value: str) -> list[int]:
    """A ``--sheets`` value as the sheet indices it names, in order.

    Accepts single numbers and inclusive ranges, comma-separated:
    ``0``, ``2,0``, ``1-3``, ``0,2-4``. Indices are **0-based**, matching
    every other sheet number Deckle prints -- the layout warnings, the
    schedule's gathering list, ``Sheet.index``. A 1-based flag would
    disagree with all three. See :func:`_parse_index_selection`.

    :param value: the raw flag text.
    :returns: the indices, in the order named.
    :raises argparse.ArgumentTypeError: empty, malformed, negative, or a
        range that runs backwards.
    """
    return _parse_index_selection(
        value,
        noun="sheets",
        example=(
            "sheet numbers like 0, 2,0 or 0,2-4 -- counting from 0, as the "
            "schedule and the warnings do"
        ),
    )


def _parse_page_selection(value: str) -> list[int]:
    """A ``--pages`` value as 0-based page indices, in order.

    1-based on the way in, because a person types what their PDF viewer's
    page counter shows -- the same reason the binding schedule prints
    1-based page numbers over 0-based ``page_index`` values.

    :param value: the raw flag text.
    :returns: 0-based page indices, in the order named.
    :raises argparse.ArgumentTypeError: empty, malformed, a range that runs
        backwards, or a page number below 1.
    """
    return _parse_index_selection(
        value,
        noun="pages",
        example=(
            "page numbers like 7, 1,3 or 7-312,400 -- counting from 1, as "
            "your PDF viewer does"
        ),
        offset=1,
    )


_SIGNED_LENGTH_RE = re.compile(
    r"^\s*([+-]?[0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$", re.IGNORECASE
)


def _parse_offset_pair(value: str) -> tuple[float, float]:
    """A ``--back-offset`` value as ``(dx, dy)`` in points.

    Signed, which is why this does not reuse :func:`_parse_length_pt`:
    that one's pattern has no sign, because every length it was written
    for -- a margin, a gutter, a paper edge -- is a magnitude. A
    registration correction goes both ways by nature, and half of the
    possible answers would be unsayable without a minus.

    :param value: ``"dx,dy"``, each with an optional unit -- ``3,-2``,
        ``0.5mm,-1mm``, ``-0.25in,0``.
    :returns: the pair in points.
    :raises argparse.ArgumentTypeError: not two values, or either
        unparseable.
    """
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"invalid back-offset {value!r}: expected two values, x and y, "
            "separated by a comma -- e.g. 3,-2 or 0.5mm,-1mm"
        )
    pair = []
    for part in parts:
        match = _SIGNED_LENGTH_RE.match(part)
        if not match:
            raise argparse.ArgumentTypeError(
                f"invalid back-offset {value!r}: {part.strip()!r} is not a "
                f"signed number with an optional unit "
                f"({', '.join(_ACCEPTED_LENGTH_UNITS)})"
            )
        number, unit = match.groups()
        pair.append(float(number) * (_UNIT_TO_PT[unit.lower()] if unit else 1.0))
    return (pair[0], pair[1])
