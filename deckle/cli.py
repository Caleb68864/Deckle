"""Deckle's headless CLI.

A shipped MVP feature, not a test harness -- it is also what lets the
golden-fixture regression run headlessly in CI, since ``deckle.core`` is
Qt-free. Subcommands: ``impose``, ``export``, ``info``.

Imports only the pure core package -- never the Qt-based desktop app layer
or any Qt binding -- so it stays runnable in a headless container with no
display server present.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import re
import sys
import warnings
from typing import Sequence

from deckle import __version__ as _DECKLE_VERSION
from deckle.core.export import export as export_plan, proof_rule_length_pt
from deckle.core.printing import plan_passes
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile, saved_profiles
from deckle.core.report import (
    info_report,
    print_plan_report,
    profile_entry,
    profile_list_report,
    profile_report,
    schedule_report,
)
from deckle.core import about
from deckle.core.export import (
    RULE_TOO_NARROW_NOTE,
    export as export_plan,
    proof_rule_advice,
)
from deckle.core.printing import pass_export
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile
from deckle.core.layout import GutterShiftStrategy, LayoutStrategy, SaddleStitchStrategy
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.loader import (
    ImportedPages,
    SourceLoadError,
    apply_page_selection,
    load_image_dir,
    load_pdf,
)
from deckle.core.models import LayoutSettings, Project, SourcePage
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.paper import (
    GRADE_BASIS_SIZES_IN, PAPER_BULK, caliper_pt_from_gsm, gsm_from_pounds,
)
from deckle.core.paths import atomic_output, write_text_atomic
from deckle.core.schedule import build_schedule, format_schedule_text
from deckle.core.project_io import (
    PathOutsideRootsAdvisory,
    SourceChangedWarning,
    SourceMissingError,
    load_project,
    save_project,
)

# A-6: `deckle --version` prints the app version plus the resolved versions
# of its key third-party dependencies -- the first thing anyone asks for in
# a bug report. Resolved via installed-distribution metadata rather than by
# importing the packages themselves, so this never imports PySide6 -- and so
# deckle.cli never has to import deckle.app.
#
# The list and the formatting live in `deckle.core.about`, because the
# desktop app's About box has to say the same thing: a report built from
# one has to be comparable with a report built from the other.
_version_string = about.version_string

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


def _paper_thickness_from_args(args) -> float:
    """One sheet's caliper, from whichever way the user stated the paper.

    Two ways to say one thing is fine; a silent precedence between them is
    not, so giving both is refused rather than resolved. Someone who sets
    a weight and forgets an old `--paper-thickness` in a script would
    otherwise get a book bound to a number they did not give.

    :param args: the parsed arguments.
    :returns: the caliper in points, or ``0.0`` when unset.
    :raises ValueError: both forms were given, or pounds without a grade.
    """
    weight = getattr(args, "paper_weight", None)
    thickness = getattr(args, "paper_thickness", 0.0)
    if weight is None:
        return thickness
    if thickness:
        raise ValueError(
            "give either --paper-weight or --paper-thickness, not both: "
            "they are two ways to describe the same sheet, and there is no "
            "sensible rule for which one wins"
        )
    value, unit = weight
    if unit == "lb":
        grade = getattr(args, "paper_grade", None)
        if grade is None:
            raise ValueError(
                "--paper-weight in pounds also needs --paper-grade: a US "
                "basis weight means nothing without one, and 20lb is 75gsm "
                "as bond but 54gsm as cover. Expected one of "
                + ", ".join(sorted(GRADE_BASIS_SIZES_IN))
            )
        value = gsm_from_pounds(value, grade)
    return caliper_pt_from_gsm(value, args.paper_type)


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


def _report_missing_sheets(plan, selection: list[int], total: int) -> bool:
    """Refuse a selection naming a sheet the document does not have.

    :func:`deckle.core.export.export` now refuses an unknown index too, so
    this is no longer the only thing standing between a user and a PDF
    with nothing in it. It stays because the two failures read very
    differently: this one names the document's own sheet count in the
    vocabulary of the command line and exits 1, where the library raises a
    ``ValueError`` that would reach the user as a traceback. Checked here
    first, so the friendly message is the one that fires.

    :param plan: the imposed plan, for the indices it really has.
    :param selection: what the user asked for.
    :param total: how many sheets the document has, for the message.
    :returns: ``True`` if the selection is impossible and a message has
        been printed.
    """
    have = {sheet.index for sheet in plan.sheets}
    missing = sorted({index for index in selection if index not in have})
    if not missing:
        return False
    named = ", ".join(str(index) for index in missing)
    print(
        f"error: no sheet {named} in this document -- it has {total} "
        f"sheet(s), numbered 0 to {total - 1}",
        file=sys.stderr,
    )
    return True


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


def _report_registration(offset_pt: tuple[float, float], source: str) -> None:
    """Say that back faces were moved, and by how much.

    The correction usually comes from a saved profile rather than from
    this invocation, so without this the geometry would change for
    reasons nothing on screen mentions. It also cannot be seen in the
    output: a shifted back looks exactly like an unshifted one until it
    is printed and held up against its own front.
    """
    dx, dy = offset_pt
    print(
        f"registration: back faces moved {dx:+g}, {dy:+g}pt ({source}). "
        "Corrects a constant offset only -- not skew or scale."
    )


@contextlib.contextmanager
def _json_stdout(active: bool):
    """Keep stdout empty of prose while a ``--json`` command does its work.

    Under ``--json`` the promise is that stdout holds **exactly one JSON
    document** -- that is the whole difference between output a script can
    pipe into ``jq`` and output it has to clean up first. But the work a
    report describes prints as it goes: ``--auto-crop`` reports the insets
    it measured, ``_resolve_input`` reports the flags a project made
    irrelevant. Every one of those lines is worth keeping, and none of
    them belongs in the middle of a JSON object.

    So they are moved rather than suppressed. Redirecting the whole block
    also means a ``print`` added later cannot silently corrupt the
    contract -- the alternative, threading a stream through every helper,
    is a change each of those helpers has to remember to make.

    :param active: whether ``--json`` was given. When ``False`` this does
        nothing at all, so the human path is untouched.
    """
    if not active:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _emit_json(document: dict) -> None:
    """Write one report to stdout, indented, with a trailing newline.

    Indented rather than compact because these are read by people at least
    as often as by scripts -- ``jq`` does not care either way, and a
    terminal full of one long line is unreadable.
    """
    print(json.dumps(document, indent=2))


def _source_kind(path: str) -> str:
    """What ``source`` is, in the vocabulary the reports use.

    :param path: the path the user named.
    :returns: ``"project"``, ``"images"`` or ``"pdf"``.
    """
    if _is_project_file(path):
        return "project"
    return "images" if os.path.isdir(path) else "pdf"


def _resolve_profile_origin(name: str):
    """The profile called ``name``, and whether it was measured or generic.

    :param name: the profile or printer name asked for.
    :returns: ``(profile, origin)`` where ``origin`` is ``"saved"`` or
        ``"builtin"``, or ``None`` if nothing matched -- in which case a
        message naming the alternatives has already been printed.

    The origin is carried because the two are not interchangeable and a
    caller reporting the profile must be able to say which it got: a saved
    one was measured against that physical printer, a built-in is a
    stand-in for a measurement nobody has made yet.
    """
    try:
        return (PrinterProfile.load(name), "saved")
    except (OSError, ValueError, KeyError, TypeError):
        # No saved profile, or one that cannot be read. Either way the
        # built-ins are the next place to look, and a corrupt saved file
        # should not be more fatal than a missing one.
        pass
    preset = BUILTIN_PRESETS.get(name)
    if preset is not None:
        return (preset, "builtin")
    print(
        f"error: no printer profile {name!r}. Built-in profiles: "
        f"{', '.join(sorted(BUILTIN_PRESETS))}. Calibrate a printer in the "
        "desktop app to save one under its own name.",
        file=sys.stderr,
    )
    return None


def _resolve_profile(name: str):
    """The printer profile called ``name``: saved first, then built-in.

    A saved profile wins because it came from a calibration run against
    that actual printer, and a built-in preset is a generic stand-in.
    Names are printer names, which is how the desktop app stores them.

    :param name: the profile or printer name asked for.
    :returns: the :class:`~deckle.core.profiles.PrinterProfile`, or
        ``None`` if nothing matched -- in which case a message naming the
        alternatives has already been printed.
    """
    resolved = _resolve_profile_origin(name)
    return None if resolved is None else resolved[0]


def _load_source(path: str) -> list[SourcePage]:
    """Load a PDF file or a directory of images.

    The image-directory result is returned as-is rather than copied into a
    plain ``list``: ``load_image_dir`` returns an ``ImportedPages``, whose
    ``.warnings`` are what tell the user about mixed DPI or files that were
    skipped. Wrapping it in ``list()`` dropped every one of them on the
    floor before they reached ``_emit_warnings``.
    """
    if os.path.isdir(path):
        return load_image_dir(path)
    return load_pdf(path)


PROJECT_SUFFIX = ".deckle"


def _is_project_file(path: str) -> bool:
    """Whether ``path`` names a saved project rather than a source document.

    :param path: the path the user gave.
    :returns: ``True`` for a ``.deckle`` file.

    Decided by suffix, not by sniffing content: a project file is something
    Deckle wrote and the user named, and guessing would make
    ``book.pdf.deckle`` ambiguous for no benefit.
    """
    return path.lower().endswith(PROJECT_SUFFIX)


def _load_project_or_report(path: str) -> Project | None:
    """Open a ``.deckle``, or print an actionable error and return ``None``.

    :param path: the project file.
    :returns: the project, or ``None`` when it could not be opened.

    A project stores *references* to its sources, not their content, so it
    can outlive them. Both ways that goes wrong are reported plainly rather
    than as a traceback: the source has moved, or the source is still there
    but has been edited since the project was saved. The second is the
    dangerous one -- the imposition would be computed against content the
    user has not seen.
    """
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # The directory the user is working in counts as chosen. Without
            # it, the ordinary case -- a project saved beside its output,
            # sources in Downloads -- advises on every single load, and Python
            # renders a bare warning with a file and line number that points
            # at Deckle rather than at anything the user did.
            project = load_project(path, allowed_roots=(os.getcwd(),))
        for warning in caught:
            if issubclass(warning.category, PathOutsideRootsAdvisory):
                print(f"note: {warning.message}", file=sys.stderr)
        return project
    except SourceMissingError as exc:
        print(
            f"error: cannot open {path}: a source file is missing -- "
            f"{exc.expected_path}. Restore it, or re-import from its new "
            "location.",
            file=sys.stderr,
        )
        log_exception("project_source_missing", exc, path=path)
        return None
    except SourceChangedWarning as exc:
        print(
            f"error: cannot open {path}: {exc.path} has changed since the "
            "project was saved. Imposing it would use content you have not "
            "reviewed. Re-import the file to accept the new version.",
            file=sys.stderr,
        )
        log_exception("project_source_changed", exc, path=path)
        return None
    except KeyError as exc:
        # `str(KeyError)` is the bare key, so the default branch below
        # produced "error: cannot open job.deckle: 'pages'" -- which names
        # the problem only to someone who already knows the file format.
        print(
            f"error: cannot open {path}: it is missing {exc} and so is not a "
            "complete Deckle project. If you edited it by hand, compare it "
            "against one Deckle wrote.",
            file=sys.stderr,
        )
        log_exception("project_open_failed", exc, path=path)
        return None
    except json.JSONDecodeError as exc:
        print(
            f"error: cannot open {path}: it is not valid JSON ({exc.msg} at "
            f"line {exc.lineno}), so it is not a Deckle project file.",
            file=sys.stderr,
        )
        log_exception("project_open_failed", exc, path=path)
        return None
    except (OSError, ValueError) as exc:
        print(f"error: cannot open {path}: {exc}", file=sys.stderr)
        log_exception("project_open_failed", exc, path=path)
        return None


def _layout_dests() -> set[str]:
    """The ``dest`` of every flag :func:`_add_layout_args` contributes.

    Derived from the function itself rather than written out, so it cannot
    drift: a layout flag added there is in this set the moment it exists,
    and one removed leaves it.

    :returns: the destination names.
    """
    probe = argparse.ArgumentParser(add_help=False)
    _add_layout_args(probe)
    return {action.dest for action in probe._actions if action.option_strings}


def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Which layout options the user actually typed, as flag names.

    :param args: the parsed arguments.
    :param parser: the parser they came from, for its defaults.
    :returns: the flags whose value differs from the default.

    Used to warn rather than silently ignore. Comparing against defaults is
    approximate -- typing the default value looks like not typing it -- but
    it errs toward silence, which is the right direction for a warning.

    Only genuine layout flags are considered. This used to exclude three
    ``dest`` names by hand and treat everything else on the subparser as
    layout, which named the wrong flags: ``--sheets``, ``--pass``,
    ``--profile``, ``--back-offset``, ``--rule`` and ``--printer`` are not
    layout and *are* honoured beside a project -- the GUIDE says so in as
    many words -- yet each was reported as "ignored", which is a message
    telling the user the opposite of what happened. The list could only
    grow: every non-layout flag added to any of these commands, including
    ``--json`` and ``--dry-run``, would have joined it.
    """
    sub = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(getattr(args, "_command", ""))
            break
    if sub is None:
        return []
    layout = _layout_dests()
    given = []
    for action in sub._actions:
        if not action.option_strings or action.dest not in layout:
            continue
        if getattr(args, action.dest, action.default) != action.default:
            given.append(action.option_strings[0])
    return sorted(given)


def _apply_auto_crop(pages, settings, args):
    """Measure the crop from the pages themselves and report what it found.

    Reported rather than applied silently, and reported as the exact
    ``--crop`` values that reproduce it. A geometry change nobody typed is
    worth saying out loud, and the numbers are the useful artifact: measure
    once, then pin them and stop rasterising the document on every run.
    """
    from deckle.core.render import auto_crop_insets

    odd, even = auto_crop_insets(pages, margin_pt=args.auto_crop_margin)
    if odd is None and even is None:
        print(
            "note: --auto-crop found no content to measure, so nothing was "
            "cropped. A scan of blank pages, or a threshold that read the "
            "whole page as background.",
            file=sys.stderr,
        )
        return settings
    print(f"auto-crop: --crop {_format_insets(odd)}" if odd else "auto-crop: odd pages not measured")
    if even is not None:
        print(f"auto-crop: --crop-even {_format_insets(even)}")
    return dataclasses.replace(settings, crop_odd_pt=odd, crop_even_pt=even)


def _format_insets(insets) -> str:
    """Insets as a `--crop` value, so the output can be pasted back in."""
    return ",".join(f"{v:.1f}pt" for v in insets)


def _resolve_input(args: argparse.Namespace) -> tuple[list, LayoutSettings] | None:
    """Turn ``args.source`` into pages plus the layout to impose them with.

    :param args: parsed arguments carrying ``source`` and the layout flags.
    :returns: ``(pages, settings)``, or ``None`` when the input could not
        be read -- the error has already been reported.

    A ``.deckle`` carries its own layout, and that layout wins. It is the
    one the user set up, previewed and saved; silently overriding it from
    flag defaults would mean ``deckle export project.deckle`` produced a
    different book from the one the project describes. Flags typed
    alongside a project are reported as ignored rather than quietly
    dropped -- and rather than applied, which would be worse.
    """
    if _is_project_file(args.source):
        project = _load_project_or_report(args.source)
        if project is None:
            return None
        ignored = _layout_flags_given(args, build_parser())
        if ignored:
            print(
                "note: "
                + ", ".join(ignored)
                + f" ignored -- {args.source} carries its own layout.",
                file=sys.stderr,
            )
        return list(project.pages), project.layout

    pages = _load_source_or_report(args.source)
    if pages is None:
        return None
    # Before the layout, and so before `--auto-crop`, deliberately: a
    # scanner target's black calibration bar must not widen the measured
    # ink extent of a book it is not part of. `auto_crop_insets` already
    # excludes skipped pages.
    selection = getattr(args, "page_selection", None)
    if selection is not None:
        try:
            selected = apply_page_selection(pages, keep=selection)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            log_exception("page_selection_rejected", exc)
            return None
        # `apply_page_selection` returns a plain list, which drops
        # `ImportedPages.warnings` -- and with them every mixed-DPI
        # advisory an image-directory import raised.
        pages = ImportedPages(selected, list(getattr(pages, "warnings", [])))
        if all(page.skipped for page in pages):
            print(
                "error: --pages kept no pages, so there would be nothing "
                "to impose",
                file=sys.stderr,
            )
            return None
    try:
        settings = _build_layout_settings(args)
    except ValueError as exc:
        # How the paper was stated is the user's problem to fix, not a bug
        # report -- two conflicting ways to give a thickness, or pounds
        # without the grade that makes them mean anything. The messages
        # already name the remedy; only their presentation was missing.
        print(f"error: {exc}", file=sys.stderr)
        log_exception("layout_settings_rejected", exc)
        return None
    if getattr(args, "auto_crop", False):
        settings = _apply_auto_crop(pages, settings, args)
    return pages, settings


def _load_source_or_report(path: str) -> list[SourcePage] | None:
    """Load the source, or print an actionable error and return ``None``.

    Every refusal from the loader is a :class:`SourceLoadError` whose
    message already names the file and the remedy, so the CLI's job is only
    to route it to stderr and record it. Nothing else is caught here: an
    unexpected exception should still produce a traceback, because a
    traceback is a bug report and a swallowed one is not.
    """
    try:
        return _load_source(path)
    except SourceLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        log_exception("source_load_failed", exc, path=path)
        return None


def _report_output_problem(out_path: str, source: str | None = None) -> bool:
    """Print the problem with ``out_path``, if any. ``True`` means stop.

    :param out_path: the destination the user asked for.
    :param source: the document being imposed, when known.
    :returns: ``True`` if the caller should stop.
    """
    problem = output_path_problem(out_path, source)
    if problem is None:
        return False
    print(f"error: {problem}", file=sys.stderr)
    log_event("output_path_rejected", path=out_path, detail=problem)
    return True


def _report_write_failure(out_path: str, exc: OSError) -> None:
    """Print why the write failed, and record it.

    The wording lives in :mod:`deckle.core.outputs` so the desktop app says
    the same thing; this only routes it to stderr.
    """
    print(f"error: {describe_write_failure(out_path, exc)}", file=sys.stderr)
    log_exception("output_write_failed", exc, path=out_path)


def _build_layout_settings(args: argparse.Namespace) -> LayoutSettings:
    paper = args.paper
    if getattr(args, "landscape", False):
        # Turn whatever was asked for, rather than assuming the preset came
        # out portrait: `--paper 792x612pt --landscape` must stay landscape
        # instead of being flipped back.
        short, long = sorted(paper)
        paper = (long, short)
    thickness_pt = _paper_thickness_from_args(args)
    return LayoutSettings(
        paper=paper,
        gutter_pt=args.gutter,
        binding_edge=args.binding_edge,
        fold_scheme=args.fold_scheme,
        sheets_per_signature=args.sheets_per_signature,
        blank_mode=args.blank_mode,
        sewing_stations=args.sewing_stations,
        sewing_station_positions_pt=args.sewing_station_positions_pt,
        paper_thickness_pt=thickness_pt,
        grain=args.grain,
        trim_pt=args.trim_pt,
        crop_odd_pt=args.crop,
        crop_even_pt=args.crop_even,
        signature_lengths=args.signature_lengths,
    )


def _strategy_for(settings: LayoutSettings) -> LayoutStrategy:
    """Pick the imposition strategy named by ``settings.fold_scheme``.

    ``"folio"`` is the v2 saddle-stitch path; anything else (``"none"``,
    the default) keeps the MVP one-page-per-side behavior.
    """
    if settings.fold_scheme == "folio":
        return SaddleStitchStrategy()
    return GutterShiftStrategy()


def _add_layout_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gutter", type=_parse_length_pt, default=0.0,
        help="gutter width, e.g. 0.75in, 18pt, 5mm (default: 0)",
    )
    parser.add_argument(
        "--paper", type=_parse_paper, default=LETTER_PT,
        help="paper size: a preset (letter, a4, legal) or WxH[unit] (default: letter)",
    )
    parser.add_argument(
        "--landscape", action="store_true",
        help="turn the sheet on its side. Signatures want this: two portrait "
        "book pages sit side by side on one landscape sheet",
    )
    parser.add_argument(
        "--binding-edge", choices=["left", "right"], default="left",
        help="which edge the gutter shifts toward (default: left)",
    )
    parser.add_argument(
        "--fold-scheme", choices=["none", "folio"], default="none",
        help="imposition scheme: 'none' (one page per side) or 'folio' "
        "(saddle-stitch signatures) (default: none)",
    )
    parser.add_argument(
        "--sheets-per-signature", type=int, default=4,
        help="sheets per saddle-stitch signature, under --fold-scheme folio (default: 4)",
    )
    parser.add_argument(
        "--blank-mode", choices=["end", "balanced"], default="end",
        help="how padding blanks are distributed across signatures, under "
        "--fold-scheme folio (default: end)",
    )
    parser.add_argument(
        "--grain", choices=["long", "short", "unknown"], default="unknown",
        help="which way the paper's fibres run. Ordinary office letter and "
        "A4 are long grain. Deckle warns when the spine would run across "
        "the grain, which cracks the fold (default: unknown, silent)",
    )
    parser.add_argument(
        "--paper-thickness", type=_parse_length_pt, default=0.0,
        help="caliper of one sheet, e.g. 0.004in or 0.1mm. Used to estimate "
        "fore-edge creep and spine thickness (default: 0, unset)",
    )
    parser.add_argument(
        "--paper-weight", type=_parse_paper_weight, default=None,
        metavar="WEIGHT",
        help="what the ream wrapper says, e.g. 80gsm or 24lb -- an "
        "alternative to --paper-thickness for anyone without calipers. "
        "Pounds also need --paper-grade, because a US basis weight means "
        "nothing without one",
    )
    parser.add_argument(
        "--paper-type", choices=sorted(PAPER_BULK), default="offset",
        help="how bulky the stock is, which is what separates two papers "
        "of the same weight (default: offset)",
    )
    parser.add_argument(
        "--paper-grade", choices=sorted(GRADE_BASIS_SIZES_IN), default=None,
        help="which basis size a pound weight is quoted against. Required "
        "with a lb --paper-weight: 20lb is 75gsm as bond and 54gsm as cover",
    )
    parser.add_argument(
        "--signatures", dest="signature_lengths",
        type=_parse_signature_lengths, default=None, metavar="N,N,N",
        help=(
            "sheet count of each signature, such as 10,10,8 -- instead of "
            "one uniform --sheets-per-signature. For a page count that "
            "divides badly, or to land a chapter break on a signature "
            "boundary. Must add up to the document's sheet count"
        ),
    )
    parser.add_argument(
        "--sewing-stations", type=int, default=3,
        help="number of sewing station marks per signature, under --fold-scheme folio (default: 3)",
    )
    parser.add_argument(
        "--stations", dest="sewing_station_positions_pt",
        type=_parse_station_positions, default=None, metavar="Y,Y,Y",
        help=(
            "exactly where the sewing stations go, measured up from the "
            "tail -- e.g. 0.5in,2in,2.25in,9.5in. Use this instead of "
            "--sewing-stations when even spacing will not do: tapes need a "
            "pair either side of each tape, and kettle stitches sit at a "
            "fixed inset from head and tail. Wins over --sewing-stations "
            "when both are given"
        ),
    )
    parser.add_argument(
        "--crop", type=_parse_crop, default=None, metavar="L,B,R,T",
        help=(
            "remove space from every source page before imposing -- insets "
            "from the left, bottom, right and top, e.g. 0.5in,0.25in,"
            "0.5in,0.25in. Cropping a scan's wide margins is what lets the "
            "type stay readable at a small trim size"
        ),
    )
    parser.add_argument(
        "--auto-crop", action="store_true",
        help=(
            "measure the crop from where the ink actually is, instead of "
            "typing it. Odd and even pages are measured separately. Prints "
            "the values it found so you can pin them with --crop"
        ),
    )
    parser.add_argument(
        "--auto-crop-margin", type=_parse_length_pt, default=0.0,
        metavar="LENGTH",
        help=(
            "keep this much back from every edge found by --auto-crop, "
            "for descenders and hairline rules a low-dpi scan can miss"
        ),
    )
    parser.add_argument(
        "--crop-even", type=_parse_crop, default=None, metavar="L,B,R,T",
        help=(
            "a different crop for even-numbered pages, for a scan whose "
            "gutter swaps sides every leaf. Without this, --crop applies "
            "to the whole document"
        ),
    )
    parser.add_argument(
        "--trim", dest="trim_pt", type=_parse_length_pt, default=0.0,
        metavar="LENGTH",
        help=(
            "draw cut lines this far in from head, tail and fore-edge -- "
            "where the block is trimmed square after sewing, e.g. 0.25in. "
            "The spine is never cut. Default 0, meaning no cut lines"
        ),
    )
    parser.add_argument(
        "--pages", dest="page_selection",
        type=_parse_page_selection, default=None, metavar="SPEC",
        help=(
            "use only these pages of the source, counting from 1 as your "
            "PDF viewer does -- e.g. 7-312,400. A public-domain scan "
            "carries a scanner target, a bookplate and a colophon, and "
            "none of them belong in the book. The rest are marked skipped "
            "rather than deleted, so they are still there if you open the "
            "project. Ignored for a .deckle source, which carries its own"
        ),
    )


def _cmd_info(args: argparse.Namespace) -> int:
    as_json = getattr(args, "json", False)
    with _json_stdout(as_json):
        resolved = _resolve_input(args)
        if resolved is None:
            return 1
        pages, settings = resolved

        if not as_json:
            if _is_project_file(args.source):
                print(f"project: {args.source}")
            print(f"page count: {len(pages)}")
            sizes = sorted({(p.ref.width_pt, p.ref.height_pt) for p in pages})
            print("detected page sizes (pt):")
            for w, h in sizes:
                print(f"  {w:.2f} x {h:.2f}")

        import_warnings = list(getattr(pages, "warnings", []))
        plan = _impose_or_report(pages, settings)
        if plan is None:
            return 1

        all_warnings = import_warnings + list(plan.warnings)
        if not as_json:
            # Signature breakdown -- always printed, even under the MVP
            # (fold_scheme="none") path, where there are simply zero
            # signatures.
            blank_total = sum(sig.blank_count for sig in plan.signatures)
            print(f"signature count: {len(plan.signatures)}")
            print(f"sheet count: {len(plan.sheets)}")
            print(f"blank count: {blank_total}")

            if all_warnings:
                print("layout warnings:")
                for w in all_warnings:
                    print(f"  [{w.kind}] sheet {w.sheet_index}: {w.detail}")
            else:
                print("layout warnings: none")

    if as_json:
        _emit_json(
            info_report(
                args.source, _source_kind(args.source), pages, plan, all_warnings
            )
        )
    return 0


def _emit_warnings(pages, plan) -> None:
    """Print every layout warning to stderr, and record them.

    Warnings go to **stderr** so ``deckle export`` keeps a clean stdout for
    scripting, and they never change the exit code -- a warning is advice,
    not a failure. But they must be said out loud somewhere: until this
    existed only ``deckle info`` printed them, so
    ``export --fold-scheme folio`` onto portrait paper emitted the
    ``sheet_orientation`` warning, squeezed two pages onto every portrait
    sheet, and told the user nothing at all.
    """
    warnings = list(getattr(pages, "warnings", [])) + list(plan.warnings)
    if not warnings:
        return
    print("layout warnings:", file=sys.stderr)
    for w in warnings:
        print(f"  [{w.kind}] sheet {w.sheet_index}: {w.detail}", file=sys.stderr)
        log_event(
            "layout_warning", kind=w.kind, sheet_index=w.sheet_index, detail=w.detail
        )


def _impose_or_report(pages, settings):
    """Impose, or print why the settings cannot produce a book.

    A handful of settings can only be judged once the pages have been
    read -- ``--signatures`` that do not add up to the sheet count the
    document actually makes, a ``--trim`` deep enough that opposing cuts
    cross, a ``--crop`` that consumes the page. Each already raises a
    ``ValueError`` naming the numbers involved, but the raise happens
    inside ``impose()``, which sits outside every try block the commands
    had -- so a plain typo produced a traceback.

    That is the wrong side of the line this CLI draws: a traceback is a
    bug report, and these are the user telling Deckle to do something
    arithmetically impossible. The message was always right; only its
    presentation was wrong.

    :param pages: the source pages.
    :param settings: the layout to impose them with.
    :returns: the plan, or ``None`` when the settings cannot work -- the
        error is already on stderr.
    """
    try:
        return _strategy_for(settings).impose(pages, settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        log_exception("impose_failed", exc)
        return None


def _cmd_export(args: argparse.Namespace) -> int:
    if _report_output_problem(args.output, args.source):
        return 1
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    plan = _impose_or_report(pages, settings)
    if plan is None:
        return 1
    _emit_warnings(pages, plan)

    selection = args.sheets
    if selection is not None and _report_missing_sheets(
        plan, selection, len(plan.sheets)
    ):
        return 1

    side = None
    rotate_180 = False
    print_pass = None
    back_offset = (0.0, 0.0)
    offset_source = ""
    profile = None
    # Resolved whenever it is given, not only alongside `--pass`. The back
    # offset is the most expensive datum in the project -- it comes from
    # printing a target, measuring it by hand and reprinting when the
    # numbers are wrong -- and it was being parsed and then dropped on the
    # floor for anyone who asked for a profile without also asking for a
    # single pass. B22.
    if args.profile is not None:
        profile = _resolve_profile(args.profile)
        if profile is None:
            return 1
        back_offset = (profile.back_offset_x_pt, profile.back_offset_y_pt)
        offset_source = f"profile {args.profile!r}"
    if args.pass_side is not None:
        if profile is None:
            print(
                f"error: --pass {args.pass_side} needs --profile, because "
                "neither the sheet order nor the half turn has a safe "
                "default -- guessing wrong prints every back onto the wrong "
                "front. Built-in profiles: "
                f"{', '.join(sorted(BUILTIN_PRESETS))}",
                file=sys.stderr,
            )
            return 1
        print_pass = pass_export(plan, profile, args.pass_side, selection)
        side = args.pass_side
        selection = print_pass.sheets
        rotate_180 = print_pass.rotate_180

    if args.back_offset is not None:
        back_offset = args.back_offset
        offset_source = "--back-offset"

    if getattr(args, "dry_run", False):
        _report_export_dry_run(
            args, plan, selection, side, rotate_180, back_offset,
            offset_source, print_pass,
        )
        log_event("export_dry_run", path=args.output, sheets=len(plan.sheets))
        return 0

    try:
        export_plan(
            plan,
            args.output,
            sheets=selection,
            rule=args.rule,
            side=side,
            rotate_180=rotate_180,
            back_offset_pt=back_offset,
        )
    except OSError as exc:
        _report_write_failure(args.output, exc)
        return 1
    except ValueError as exc:
        # A `.deckle` carries its own paper and never passes through the
        # flag parser, so an out-of-range sheet can still arrive here.
        # Without this it surfaces as a bare pikepdf traceback.
        print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
        log_exception("export_failed", exc, path=args.output)
        return 1
    print(f"wrote {args.output}")
    if back_offset != (0.0, 0.0):
        _report_registration(back_offset, offset_source)
    if print_pass is not None:
        print(print_pass.reload_instruction)
    if args.rule:
        _report_rule(plan.paper_pt[0])
    return 0


def _format_sheet_list(indices: Sequence[int]) -> str:
    """Sheet indices as a ``--sheets`` value, with runs collapsed.

    ``[0, 1, 2, 4]`` becomes ``0-2,4`` -- the same notation
    :func:`_parse_sheet_selection` accepts, so what a dry run prints can be
    pasted straight back into the real command. Order is preserved and
    repeats are kept, because both are meaningful to ``--sheets`` and
    tidying them here would describe a job other than the one planned.

    :param indices: the sheets, in the order they will be written.
    :returns: the collapsed list, or ``"none"`` when empty.
    """
    if not indices:
        return "none"
    runs: list[list[int]] = [[indices[0], indices[0]]]
    for index in indices[1:]:
        if index == runs[-1][1] + 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    return ",".join(
        str(start) if start == end else f"{start}-{end}" for start, end in runs
    )


def _report_export_dry_run(args, plan, selection, side, rotate_180,
                           back_offset, offset_source, print_pass) -> None:
    """Say exactly what ``export`` would write, having written nothing.

    Everything above this point has already run -- the source was loaded,
    the settings were imposed, the destination was checked, the sheet
    selection was validated against the document and the profile was
    resolved -- so a dry run that reports success is a real statement about
    a job that would succeed, and one that exits 1 has found a genuine
    problem before any paper or disk was spent.

    That ordering is the whole feature. Deckle's own argument is that the
    artefact is physical and paper is expensive; a plan you can read
    without producing either is the cheapest possible place to notice that
    ``--sheets 0,2-4`` names a sheet the document does not have, or that
    the back offset in the profile is not the one you calibrated.

    :param args: the parsed arguments, for the destination and ``--rule``.
    :param plan: the imposed plan.
    :param selection: the sheets that would be written, or ``None`` for all.
    :param side: ``"front"``, ``"back"`` or ``None`` for both faces.
    :param rotate_180: whether backs would be turned.
    :param back_offset: the registration correction that would be applied.
    :param offset_source: where that correction came from.
    :param print_pass: the planned pass, when ``--pass`` was given.
    :returns: nothing. Prints to stdout, like the ``wrote ...`` line it
        stands in for.
    """
    indices = [sheet.index for sheet in plan.sheets] if selection is None else selection
    width, height = plan.paper_pt
    print(f"dry run: would write {args.output}")
    print(f"  paper: {width:g} x {height:g}pt")
    print(
        f"  sheets: {len(indices)} of {len(plan.sheets)} "
        f"({_format_sheet_list(indices)})"
    )
    if side is None:
        print("  faces: both sides of every sheet")
    else:
        turned = ", each turned 180 degrees" if rotate_180 else ""
        print(f"  faces: {side}s only (one manual-duplex pass){turned}")
    if back_offset != (0.0, 0.0):
        dx, dy = back_offset
        print(f"  registration: back faces moved {dx:+g}, {dy:+g}pt ({offset_source})")
    if args.rule:
        print("  proof rule: drawn on every sheet")
    if print_pass is not None:
        print(f"  reload: {print_pass.reload_instruction}")
    print("nothing was written.")


def _report_rule(paper_width_pt: float) -> None:
    """Say what the printed rule should measure, and what a short answer
    means.

    The wording lives in :func:`deckle.core.export.proof_rule_advice` --
    the print dialog's proof checkbox says the same thing, and a user who
    has done the check once should recognise it the next time.
    """
    advice = proof_rule_advice(paper_width_pt)
    if advice == RULE_TOO_NARROW_NOTE:
        print(f"note: {advice}", file=sys.stderr)
        return
    print(advice)


def _cmd_impose(args: argparse.Namespace) -> int:
    if _report_output_problem(args.output, args.source):
        return 1
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    plan = _impose_or_report(pages, settings)
    if plan is None:
        return 1
    _emit_warnings(pages, plan)

    if getattr(args, "dry_run", False):
        width, height = plan.paper_pt
        skipped = sum(1 for page in pages if page.skipped)
        print(f"dry run: would write {args.output}")
        print(
            f"  pages: {len(pages)}"
            + (f" ({skipped} skipped)" if skipped else "")
        )
        print(f"  paper: {width:g} x {height:g}pt")
        print(f"  sheets: {len(plan.sheets)}")
        print(
            f"  fold scheme: {settings.fold_scheme}"
            + (
                f", {settings.sheets_per_signature} sheet(s) per signature"
                if settings.fold_scheme != "none"
                else ""
            )
        )
        if args.printer:
            print(f"  printer recorded: {args.printer}")
        print("nothing was written.")
        log_event("impose_dry_run", path=args.output, sheets=len(plan.sheets))
        return 0

    project = Project(pages=list(pages), layout=settings, printer=args.printer)
    try:
        save_project(project, args.output)
    except SourceChangedWarning as exc:
        print(f"error: source changed: {exc.path}", file=sys.stderr)
        return 1
    except OSError as exc:
        _report_write_failure(args.output, exc)
        return 1
    print(f"wrote {args.output}")
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    """Print the binding schedule for a source document.

    :param args: parsed arguments.
    :returns: a process exit code.

    Goes to stdout by default so it can be piped or redirected; ``-o``
    writes a file. Layout warnings still go to stderr, so a redirected
    schedule stays clean while the warnings remain visible in the terminal.

    ``--json`` swaps the bench sheet for the machine-readable report, in
    whichever destination was chosen. It applies to ``-o`` too rather than
    only to stdout: the flag says what the schedule *is*, not where it
    goes, and a ``--json -o`` that quietly wrote prose would be the worse
    of the two possible surprises.
    """
    as_json = getattr(args, "json", False)
    with _json_stdout(as_json):
        resolved = _resolve_input(args)
        if resolved is None:
            return 1
        pages, settings = resolved

        if args.output is not None and _report_output_problem(
            args.output, args.source
        ):
            return 1

        plan = _impose_or_report(pages, settings)
        if plan is None:
            return 1
        _emit_warnings(pages, plan)

        schedule = build_schedule(plan, settings)

    if as_json:
        document = schedule_report(args.source, schedule)
        if args.output is None:
            _emit_json(document)
            return 0
        text = json.dumps(document, indent=2) + "\n"
    else:
        text = format_schedule_text(schedule, os.path.basename(args.source))

    if args.output is None:
        print(text, end="")
        return 0

    try:
        # Atomic, so a failed write leaves the previous schedule intact --
        # the same guarantee `export` already gives for a PDF. Two commands
        # of one program writing a file the user named should not differ on
        # whether a full disk destroys what was there.
        write_text_atomic(args.output, text)
    except OSError as exc:
        print(f"error: {describe_write_failure(args.output, exc)}", file=sys.stderr)
        log_exception("output_write_failed", exc, path=args.output)
        return 1
    print(f"wrote {args.output}")
    return 0


def _cmd_crop_preview(args: argparse.Namespace) -> int:
    """Write a composite of every page, with the proposed crop drawn on it."""
    from PIL import Image

    from deckle.core.render import auto_crop_insets, composite_pages

    if _report_output_problem(args.output, args.source):
        return 1
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1

    crop = args.crop
    if args.auto_crop:
        # The rectangle has to be measured over exactly the pages the
        # picture shows. Measuring odd and even separately -- the
        # default, and right when cropping -- and then drawing the odd
        # answer over a composite of every page produces a confidently
        # wrong picture: it shows the even pages' ink beside a
        # rectangle never measured against it, so a crop that clips
        # them looks safe.
        odd, even = auto_crop_insets(
            pages,
            margin_pt=args.auto_crop_margin,
            split_parity=args.parity is not None,
        )
        crop = even if args.parity == "even" else odd
        if crop is None:
            print(
                "note: --auto-crop found no content to measure, so no crop "
                "is drawn.",
                file=sys.stderr,
            )
        else:
            print(f"auto-crop: --crop {_format_insets(crop)}")

    try:
        composite = composite_pages(
            pages, dpi=args.dpi, parity=args.parity, crop_pt=crop
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        log_exception("composite_failed", exc)
        return 1

    image = Image.frombytes(
        "RGBA", (composite.width, composite.height), composite.rgba
    )
    try:
        # Written beside the target and renamed, so an encoder that fails
        # partway leaves the previous overlay rather than a truncated PNG.
        with atomic_output(args.output) as scratch:
            image.save(scratch)
    except (OSError, ValueError) as exc:
        _report_write_failure(args.output, exc)
        return 1

    counted = sum(1 for page in pages if not page.skipped)
    which = f" ({args.parity})" if args.parity else ""
    print(f"wrote {args.output} -- {counted} page(s) superimposed{which}")
    print(
        "Every page's ink in one picture. Anything outside the red "
        "rectangle is what the crop would remove."
    )
    return 0


def _cmd_dummy(args: argparse.Namespace) -> int:
    """Write a numbered document for checking how an imposition folds."""
    from deckle.core.dummy import make_numbered_pdf

    if _report_output_problem(args.output):
        return 1
    try:
        make_numbered_pdf(args.output, args.pages, args.page_size)
    except OSError as exc:
        _report_write_failure(args.output, exc)
        return 1
    except ValueError as exc:
        # `make_numbered_pdf` refuses a page count below 1 with a message
        # naming the number it got -- which was reaching the user as a
        # traceback, because nothing here caught it. Same shape as the
        # three settings `_impose_or_report` was written for: the message
        # was always right, only its presentation was wrong, and asking
        # for zero pages is a user's typo rather than a bug report.
        print(f"error: {exc}", file=sys.stderr)
        log_exception("dummy_failed", exc, pages=args.pages)
        return 1
    print(f"wrote {args.output} -- {args.pages} numbered page(s)")
    print(
        "Impose it, print it on scrap, fold it, and read the numbers. An "
        "eight-page folio puts 8 and 1 on the outside of the sheet and 4 "
        "and 5 at the centre."
    )
    return 0


def _profile_summary(profile: PrinterProfile) -> str:
    """A profile's behaviour in one line, in reload vocabulary.

    Named after what the operator does rather than after the field names:
    ``reverse_stack`` is true or false in the file, but at the printer it
    is "flip the whole stack over" or "reload it in the same order", and
    that is the difference that ruins a job.
    """
    stack = "stack reversed" if profile.reverse_stack else "stack in order"
    offset = ""
    if (profile.back_offset_x_pt, profile.back_offset_y_pt) != (0.0, 0.0):
        offset = (
            f", back offset {profile.back_offset_x_pt:+g}, "
            f"{profile.back_offset_y_pt:+g}pt"
        )
    return (
        f"flip on the {profile.flip_axis} edge, {stack}, outputs "
        f"{'face down' if profile.output_face == 'down' else 'face up'}{offset}"
    )


def _cmd_profile_list(args: argparse.Namespace) -> int:
    """List every printer profile this machine can resolve.

    Saved and built-in are listed apart, and labelled, because they are
    not the same kind of thing. A saved profile came from printing a
    target and measuring it; a built-in is a generic stand-in for a
    measurement nobody has made. Presenting them as one list would let
    someone pick the stand-in believing they had the measurement -- and
    the difference is not visible until a stack of backs comes out
    upside down.
    """
    saved = saved_profiles()
    # (name, profile-or-None, path-or-None, error-or-None), saved first.
    rows: list[tuple[str, PrinterProfile | None, str | None, str | None]] = []
    for name in sorted(saved):
        try:
            rows.append((name, PrinterProfile.load(name), str(saved[name]), None))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Listed with its failure rather than skipped. A calibration
            # that has become unreadable is the single thing about that
            # printer its owner most needs to be told, and a listing that
            # silently omitted it would read as "you never calibrated
            # this" -- sending them to re-measure something they had
            # already measured.
            rows.append(
                (name, None, str(saved[name]), f"{type(exc).__name__}: {exc}")
            )
    builtin = [
        (name, BUILTIN_PRESETS[name], None, None)
        for name in sorted(BUILTIN_PRESETS)
        if name not in saved  # a saved profile of the same name shadows it
    ]

    if getattr(args, "json", False):
        _emit_json(
            profile_list_report(
                [
                    profile_entry(name, profile, origin, path=path, error=error)
                    for origin, group in (("saved", rows), ("builtin", builtin))
                    for name, profile, path, error in group
                ]
            )
        )
        return 0

    if rows:
        print("saved profiles -- calibrated on this machine:")
        for name, profile, path, error in rows:
            if profile is None:
                print(f"  {name}: unreadable -- {error}")
                print(f"    {path}")
                continue
            print(f"  {name}: {_profile_summary(profile)}")
            print(
                f"    calibrated {profile.calibrated_at or 'date not recorded'}"
                f" -- {path}"
            )
    else:
        print(
            "saved profiles: none. Calibrate a printer in the desktop app, or "
            "start from a built-in with `deckle profile set NAME --from PRESET`."
        )
    print("built-in profiles -- generic stand-ins, not measured:")
    for name, profile, _path, _error in builtin:
        print(f"  {name}: {_profile_summary(profile)}")
    return 0


def _cmd_profile_show(args: argparse.Namespace) -> int:
    """Print one profile's stored values in full.

    A corrupt saved file is reported here rather than silently falling
    through to a built-in of the same name, which is what ``--profile``
    does on the export path. The two want opposite things: an export
    should keep working from a generic preset when a calibration cannot
    be read, while someone who typed ``profile show`` is asking about
    that file specifically and is owed the reason it did not open.
    """
    name = args.name
    saved = saved_profiles()
    profile = None
    origin = "builtin"
    path = None
    if name in saved:
        path = str(saved[name])
        origin = "saved"
        try:
            profile = PrinterProfile.load(name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(
                f"error: {name}'s saved calibration cannot be read: {exc}. "
                f"The file is {path}. Compare it against one Deckle wrote, or "
                "delete it and calibrate again.",
                file=sys.stderr,
            )
            log_exception("profile_unreadable", exc, name=name)
            return 1
    else:
        profile = BUILTIN_PRESETS.get(name)
        if profile is None:
            print(
                f"error: no printer profile {name!r}. "
                + (
                    f"Saved: {', '.join(sorted(saved))}. "
                    if saved
                    else "Nothing is saved on this machine. "
                )
                + f"Built-in: {', '.join(sorted(BUILTIN_PRESETS))}.",
                file=sys.stderr,
            )
            return 1

    if getattr(args, "json", False):
        _emit_json(profile_report(name, profile, origin, path=path))
        return 0

    print(f"name: {name}")
    print(
        "origin: "
        + (
            f"saved calibration ({path})"
            if origin == "saved"
            else "built-in preset -- a generic stand-in, not measured"
        )
    )
    print(f"behaviour: {_profile_summary(profile)}")
    print(f"flip axis: {profile.flip_axis}")
    print(f"output face: {profile.output_face}")
    print(f"feed edge: {profile.feed_edge}")
    print(f"reverse stack: {'yes' if profile.reverse_stack else 'no'}")
    left, top, right, bottom = profile.imageable_area_pt
    print(
        f"imageable area (margins, pt): left {left:g}, top {top:g}, "
        f"right {right:g}, bottom {bottom:g}"
    )
    print(
        f"back offset: {profile.back_offset_x_pt:+g}, "
        f"{profile.back_offset_y_pt:+g}pt -- the correction applied, not the "
        "error measured. Constant offset only, not skew or scale."
    )
    print(f"calibrated at: {profile.calibrated_at or '(never)'}")
    print(f"calibration version: {profile.calibration_version}")
    print(f"file format version: {profile.version}")
    return 0


def _profile_changes(args: argparse.Namespace) -> dict:
    """The fields ``profile set`` was actually asked to change.

    Every flag defaults to ``None`` -- including the boolean, which is why
    it is a ``BooleanOptionalAction`` rather than a ``store_true``. A
    ``store_true`` cannot distinguish "set this to false" from "do not
    touch it", and on a calibration those are very different requests.
    """
    changes: dict = {}
    for flag, field in (
        ("flip_axis", "flip_axis"),
        ("output_face", "output_face"),
        ("feed_edge", "feed_edge"),
        ("reverse_stack", "reverse_stack"),
        ("imageable_area", "imageable_area_pt"),
    ):
        value = getattr(args, flag, None)
        if value is not None:
            changes[field] = value
    if args.back_offset is not None:
        changes["back_offset_x_pt"], changes["back_offset_y_pt"] = args.back_offset
    return changes


def _cmd_profile_set(args: argparse.Namespace) -> int:
    """Create or edit a saved printer profile, never silently.

    A saved profile is the most expensive data Deckle holds -- so says
    :meth:`PrinterProfile.save`'s own docstring, and it is right: a
    calibration is not derived from anything. It comes from printing a
    target, measuring it with a ruler, and reprinting when the numbers
    were wrong. Nothing else in the program costs paper to reproduce.

    So this command will not overwrite one on its own. Editing an existing
    profile prints the exact before-and-after of every field that would
    change and exits 1; ``--force`` is what applies it. That is
    deliberately a two-step: the diff turns the guard into something
    useful rather than merely obstructive, and someone who reads it and
    still wants the change is one flag away, while someone who typed the
    wrong printer name has been shown their mistake instead of losing an
    afternoon's measuring to it.

    Creating a profile requires ``--from PRESET`` because a
    :class:`PrinterProfile` has no partial form: ``flip_axis``,
    ``output_face``, ``feed_edge`` and ``reverse_stack`` all have to say
    something, and defaulting them would be Deckle guessing a printer's
    reload behaviour. That is the same guess ``--pass`` refuses to make
    without a profile, for the same reason -- getting it wrong prints
    every back onto the wrong front, and it is not visible until the
    paper is already used.
    """
    name = args.name
    changes = _profile_changes(args)

    if name in BUILTIN_PRESETS:
        print(
            f"error: {name!r} is a built-in profile name, and saving over it "
            "would hide the built-in everywhere without removing it. Give the "
            "profile your printer's own name -- that is the name --profile and "
            f"the desktop app look it up by -- and use --from {name} to start "
            "from this one.",
            file=sys.stderr,
        )
        return 1

    saved = saved_profiles()
    if name in saved:
        path = saved[name]
        if args.from_preset is not None:
            print(
                f"error: {name} already has a saved profile, so --from "
                f"{args.from_preset} has nothing to say: either it starts from "
                "the preset and discards the saved values, or it starts from "
                "the saved profile and is ignored, and there is no sensible "
                "rule for which. Drop --from to edit what is saved, or delete "
                f"{path} first to start over.",
                file=sys.stderr,
            )
            return 1
        try:
            current = PrinterProfile.load(name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(
                f"error: {name} has a saved profile at {path} that cannot be "
                f"read ({exc}), so there is nothing to edit and no way to show "
                "you what would change. Deckle will not overwrite a "
                "calibration it cannot read -- inspect the file, and delete it "
                "if it is beyond saving.",
                file=sys.stderr,
            )
            log_exception("profile_unreadable", exc, name=name)
            return 1
        if not changes:
            print(
                f"error: nothing to change. Give at least one of --flip-axis, "
                "--output-face, --feed-edge, --reverse-stack/--no-reverse-stack, "
                "--back-offset or --imageable-area.",
                file=sys.stderr,
            )
            return 1
        updated = dataclasses.replace(current, **changes)
        if updated == current:
            print(f"{name} already has those values; nothing was written.")
            return 0
        differences = [
            (field, getattr(current, field), getattr(updated, field))
            for field in changes
            if getattr(current, field) != getattr(updated, field)
        ]
        if not args.force:
            print(
                f"error: {name} has a saved calibration and these values "
                "would change:",
                file=sys.stderr,
            )
            for field, was, now in differences:
                print(f"  {field}: {was} -> {now}", file=sys.stderr)
            print(
                f"A calibration is measured by hand, not derived, so Deckle "
                f"will not overwrite one without being told to. The file is "
                f"{path} -- copy it if you want the old numbers back -- then "
                "repeat this command with --force.",
                file=sys.stderr,
            )
            return 1
        try:
            updated.save(name)
        except OSError as exc:
            _report_write_failure(str(path), exc)
            return 1
        print(f"updated {name} -- overwrote a saved calibration.")
        for field, was, now in differences:
            print(f"  {field}: {was} -> {now}")
        # `calibrated_at` is left exactly as it was, on purpose. It records
        # when a calibration *run* measured this printer, and typing a
        # number in is not that run. Clearing it would throw away the date
        # of a measurement most of which is still standing; setting it to
        # today would claim a measurement that never happened.
        log_event("profile_set", name=name, fields=sorted(changes))
        return 0

    if args.from_preset is None:
        print(
            f"error: no saved profile called {name}, so this would create one "
            "-- and a printer profile has no partial form. Say which behaviour "
            "to start from with --from: "
            f"{', '.join(sorted(BUILTIN_PRESETS))}. Deckle will not guess a "
            "printer's reload behaviour; guessing wrong prints every back onto "
            "the wrong front.",
            file=sys.stderr,
        )
        return 1
    base = BUILTIN_PRESETS.get(args.from_preset)
    if base is None:
        print(
            f"error: no built-in profile {args.from_preset!r}. Built-in "
            f"profiles: {', '.join(sorted(BUILTIN_PRESETS))}.",
            file=sys.stderr,
        )
        return 1
    created = dataclasses.replace(base, **changes)
    try:
        created.save(name)
    except OSError as exc:
        print(f"error: cannot save profile {name}: {exc}", file=sys.stderr)
        log_exception("profile_save_failed", exc, name=name)
        return 1
    print(f"wrote profile {name}, from the built-in {args.from_preset}.")
    print(f"  {_profile_summary(created)}")
    print(
        "note: this is a hand-set profile, not a measurement -- its "
        "calibration date is still empty. --profile now prefers it over the "
        "built-in of the same behaviour."
    )
    log_event("profile_created", name=name, base=args.from_preset)
    return 0


def _pass_output_path(output: str, side: str) -> str:
    """Where one manual-duplex pass is written, given the base name.

    ``job.pdf`` becomes ``job.front.pdf`` and ``job.back.pdf``. The side
    goes in the name rather than in a folder or a suffix number because
    the two files are handled minutes apart by a person standing at a
    printer, and "which of these is the backs" must be answerable from the
    filename alone.

    :param output: the base path the user gave.
    :param side: ``"front"`` or ``"back"``.
    :returns: the path for that pass.
    """
    root, extension = os.path.splitext(output)
    return f"{root}.{side}{extension or '.pdf'}"


def _cmd_print(args: argparse.Namespace) -> int:
    """Plan a manual-duplex print run, and optionally write its passes.

    **This does not send anything to a printer, and says so.** Submission
    is the one part of the print path that is not Qt-free: the pass
    planner (``deckle.core.printing``) and the resumable session
    (``deckle.core.print_session``) are pure, but ``PrintBackend`` is a
    ``Protocol`` whose only implementation is ``QtPrintBackend`` in
    ``deckle.app.backend``. Reaching it from here would make
    ``deckle.cli`` import ``deckle.app`` -- inverting the dependency the
    CLI exists to keep, and breaking the promise in this module's own
    docstring that it runs on a machine with no display libraries at all.
    See ``docs/decisions.md``.

    What is genuinely headless is everything up to the spooler, and that
    is what this does: the pass order, the half turn, the reload
    instruction, and -- with ``-o`` -- one PDF per pass with the profile's
    registration correction already applied. That is the whole manual
    duplex workflow in one command, ending at two files to send to the
    printer instead of two invocations of ``export --pass``.

    Every number comes from ``plan_passes``, never from a second ordering
    table here: one free to disagree with the desktop app about the same
    printer would make the paper wrong while both halves looked right.
    """
    as_json = getattr(args, "json", False)
    with _json_stdout(as_json):
        resolved_profile = _resolve_profile_origin(args.profile)
        if resolved_profile is None:
            return 1
        profile, origin = resolved_profile

        resolved = _resolve_input(args)
        if resolved is None:
            return 1
        pages, settings = resolved

        plan = _impose_or_report(pages, settings)
        if plan is None:
            return 1
        _emit_warnings(pages, plan)

        selection = args.sheets
        if selection is not None and _report_missing_sheets(
            plan, selection, len(plan.sheets)
        ):
            return 1

        passes = plan_passes(plan, profile, sheets=selection)
        back_offset = (profile.back_offset_x_pt, profile.back_offset_y_pt)
        outputs: list[str | None] = [None] * len(passes)

        if args.output is not None:
            planned = [
                _pass_output_path(args.output, print_pass.side)
                for print_pass in passes
            ]
            # Both destinations are checked before either is written. A
            # run that produces the fronts and then fails on the backs
            # leaves the operator holding half a job with no way to tell
            # from the directory which half.
            for path in planned:
                if _report_output_problem(path, args.source):
                    return 1
            for print_pass, path in zip(passes, planned):
                try:
                    export_plan(
                        plan,
                        path,
                        sheets=print_pass.sheet_order,
                        side=print_pass.side,
                        rotate_180=(
                            print_pass.side == "back" and print_pass.rotate_backs
                        ),
                        back_offset_pt=back_offset,
                    )
                except OSError as exc:
                    _report_write_failure(path, exc)
                    return 1
                except ValueError as exc:
                    print(f"error: cannot write {path}: {exc}", file=sys.stderr)
                    log_exception("export_failed", exc, path=path)
                    return 1
                outputs[print_pass.index] = path

        if not as_json:
            width, height = plan.paper_pt
            print(
                f"printer profile: {args.profile} "
                + (
                    "(saved calibration)"
                    if origin == "saved"
                    else "(built-in preset -- generic, not measured)"
                )
            )
            print(f"paper: {width:g} x {height:g}pt, {len(plan.sheets)} sheet(s)")
            if back_offset != (0.0, 0.0):
                _report_registration(back_offset, f"profile {args.profile!r}")
            for print_pass in passes:
                turned = (
                    ", each turned 180 degrees"
                    if print_pass.side == "back" and print_pass.rotate_backs
                    else ""
                )
                print()
                print(
                    f"pass {print_pass.index + 1} of {len(passes)} -- "
                    f"{print_pass.side}s, {len(print_pass.sheet_order)} sheet(s) "
                    f"in order {_format_sheet_list(print_pass.sheet_order)}{turned}"
                )
                print(f"  {print_pass.reload_instruction}")
                if outputs[print_pass.index] is not None:
                    print(f"  wrote {outputs[print_pass.index]}")
            print()
            print(
                "Deckle sent nothing to a printer: submission needs the Qt "
                "backend, which lives in the desktop app, and this CLI is the "
                "half that runs without a display. Send each pass above to "
                "your printer in order, doing what its reload line says in "
                "between."
            )

    if as_json:
        _emit_json(
            print_plan_report(
                args.source, plan, settings, passes, args.profile, profile,
                origin, outputs=outputs,
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The full argument parser for ``deckle``.

    Three subcommands -- ``impose``, ``export`` and ``info`` -- sharing one
    set of layout options, so a plan previewed with ``info`` is the plan
    ``export`` writes.

    :returns: the parser. Each subparser sets a ``func`` default, which is
        what :func:`main` dispatches on.
    """
    parser = argparse.ArgumentParser(prog="deckle", description="Impose and print booklets.")
    # A-6: --version must work without a subcommand. argparse's "version"
    # action exits immediately when encountered, before the subparsers'
    # required-argument check runs, so this is reachable even though
    # add_subparsers(required=True) below would otherwise demand one.
    parser.add_argument(
        "--version", action="version", version=_version_string(),
        help="print the Deckle app version and key dependency versions",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    impose_parser = subparsers.add_parser("impose", help="impose a source into a .deckle project")
    impose_parser.add_argument("source", help="a PDF file or a directory of images")
    impose_parser.add_argument("-o", "--output", required=True, help="path to write the .deckle project")
    impose_parser.add_argument("--printer", default=None, help="printer name to record in the project")
    impose_parser.add_argument(
        "--dry-run", action="store_true",
        help="say what would be written and write nothing",
    )
    _add_layout_args(impose_parser)
    impose_parser.set_defaults(func=_cmd_impose, _command="impose")

    export_parser = subparsers.add_parser("export", help="impose and export a source directly to PDF")
    export_parser.add_argument("source", help="a PDF file or a directory of images")
    export_parser.add_argument("-o", "--output", required=True, help="path to write the exported PDF")
    export_parser.add_argument(
        "--sheets",
        type=_parse_sheet_selection,
        default=None,
        metavar="SPEC",
        help=(
            "export only these sheets, counting from 0 -- e.g. 0, 2,0 or "
            "0,2-4. Print sheet 0 on its own to proof a job before "
            "committing the stack"
        ),
    )
    export_parser.add_argument(
        "--rule",
        action="store_true",
        help=(
            "draw a ruler of known length on every sheet, to check whether "
            "the printer scaled the page. Use on a proof, not on the job"
        ),
    )
    export_parser.add_argument(
        "--pass",
        dest="pass_side",
        choices=("front", "back"),
        default=None,
        help=(
            "write one manual-duplex pass instead of both faces: every "
            "front, or every back in the order your printer's reload "
            "behaviour demands. Requires --profile"
        ),
    )
    export_parser.add_argument(
        "--back-offset",
        type=_parse_offset_pair,
        default=None,
        metavar="X,Y",
        help=(
            "move back faces by X,Y so they land behind their fronts -- a "
            "front/back registration correction, e.g. 3,-2 or 0.5mm,-1mm. "
            "Overrides the value stored in --profile. Corrects a constant "
            "offset only, not skew or scale"
        ),
    )
    export_parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help=(
            "the printer profile describing your reload behaviour -- a "
            "calibrated one saved under the printer's name, or a built-in: "
            + ", ".join(sorted(BUILTIN_PRESETS))
        ),
    )
    export_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "say what would be written -- destination, paper, sheets, faces "
            "and registration -- and write nothing. The checks all still run, "
            "so a plan that would fail fails here, before any paper"
        ),
    )
    _add_layout_args(export_parser)
    export_parser.set_defaults(func=_cmd_export, _command="export")

    info_parser = subparsers.add_parser("info", help="print page count, sizes, and layout warnings")
    info_parser.add_argument("source", help="a PDF file or a directory of images")
    info_parser.add_argument(
        "--json", action="store_true",
        help=(
            "print one JSON document instead of prose, with stable key names "
            "a script can rely on. Everything else moves to stderr"
        ),
    )
    _add_layout_args(info_parser)
    info_parser.set_defaults(func=_cmd_info, _command="info")

    schedule_parser = subparsers.add_parser(
        "schedule",
        help="print the binding schedule: what to gather, fold and sew",
    )
    schedule_parser.add_argument("source", help="a PDF file or a directory of images")
    schedule_parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="write the schedule to a file instead of stdout",
    )
    schedule_parser.add_argument(
        "--json", action="store_true",
        help=(
            "emit the schedule as one JSON document with stable key names, "
            "instead of the bench sheet. Applies to -o as well as to stdout"
        ),
    )
    _add_layout_args(schedule_parser)
    schedule_parser.set_defaults(func=_cmd_schedule, _command="schedule")

    preview_parser = subparsers.add_parser(
        "crop-preview",
        help="write a composite of every page, to check a crop before using it",
    )
    preview_parser.add_argument("source", help="a PDF file or a directory of images")
    preview_parser.add_argument(
        "-o", "--output", required=True, help="image to write (e.g. overlay.png)"
    )
    preview_parser.add_argument(
        "--crop", type=_parse_crop, default=None, metavar="L,B,R,T",
        help="draw this crop on the composite",
    )
    preview_parser.add_argument(
        "--auto-crop", action="store_true",
        help="measure the crop and draw what it found",
    )
    preview_parser.add_argument(
        "--auto-crop-margin", type=_parse_length_pt, default=0.0,
        metavar="LENGTH", help="keep this much back from every measured edge",
    )
    preview_parser.add_argument(
        "--parity", choices=("odd", "even"), default=None,
        help=(
            "composite only odd- or only even-numbered pages. A scan's "
            "margins alternate, so the two are different pictures"
        ),
    )
    preview_parser.add_argument(
        "--dpi", type=int, default=72,
        help="rasterisation resolution (default: 72)",
    )
    preview_parser.set_defaults(func=_cmd_crop_preview, _command="crop-preview")

    dummy_parser = subparsers.add_parser(
        "dummy",
        help="write a numbered document for checking how an imposition folds",
    )
    dummy_parser.add_argument(
        "-o", "--output", required=True, help="path to write the PDF"
    )
    dummy_parser.add_argument(
        "--pages", type=int, default=16,
        help="how many numbered pages (default: 16)",
    )
    dummy_parser.add_argument(
        "--page-size", type=_parse_paper, default=LETTER_PT, metavar="WxH",
        help=(
            "page size of the numbered document, matching the source you "
            "are standing in for (default: letter)"
        ),
    )
    dummy_parser.set_defaults(func=_cmd_dummy, _command="dummy")

    print_parser = subparsers.add_parser(
        "print",
        help=(
            "plan a manual-duplex run: pass order, reload instructions, and "
            "optionally one PDF per pass. Submits nothing"
        ),
    )
    print_parser.add_argument("source", help="a PDF file or a directory of images")
    print_parser.add_argument(
        "--profile",
        required=True,
        metavar="NAME",
        help=(
            "the printer profile describing your reload behaviour. Required, "
            "because neither the sheet order nor the half turn has a safe "
            "default: a calibrated one saved under the printer's name, or a "
            "built-in -- " + ", ".join(sorted(BUILTIN_PRESETS))
        ),
    )
    print_parser.add_argument(
        "-o", "--output", default=None,
        help=(
            "write one PDF per pass, named from this path -- job.pdf becomes "
            "job.front.pdf and job.back.pdf. Without it nothing is written "
            "and only the plan is printed"
        ),
    )
    print_parser.add_argument(
        "--sheets", type=_parse_sheet_selection, default=None, metavar="SPEC",
        help="plan only these sheets, counting from 0 -- e.g. 0, 2,0 or 0,2-4",
    )
    print_parser.add_argument(
        "--json", action="store_true",
        help="emit the pass plan as one JSON document with stable key names",
    )
    _add_layout_args(print_parser)
    print_parser.set_defaults(func=_cmd_print, _command="print")

    profile_parser = subparsers.add_parser(
        "profile",
        help="list, show and set the calibrated printer profiles on this machine",
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_command", required=True
    )

    profile_list_parser = profile_subparsers.add_parser(
        "list", help="list every profile, saved and built-in"
    )
    profile_list_parser.add_argument(
        "--json", action="store_true",
        help="emit the listing as one JSON document with stable key names",
    )
    profile_list_parser.set_defaults(func=_cmd_profile_list, _command="profile")

    profile_show_parser = profile_subparsers.add_parser(
        "show", help="print one profile's stored values in full"
    )
    profile_show_parser.add_argument("name", help="the profile or printer name")
    profile_show_parser.add_argument(
        "--json", action="store_true",
        help="emit the profile as one JSON document with stable key names",
    )
    profile_show_parser.set_defaults(func=_cmd_profile_show, _command="profile")

    profile_set_parser = profile_subparsers.add_parser(
        "set",
        help=(
            "create a profile from a built-in, or edit a saved one. Will not "
            "overwrite a calibration without --force"
        ),
    )
    profile_set_parser.add_argument("name", help="the printer name to save under")
    profile_set_parser.add_argument(
        "--from",
        dest="from_preset",
        default=None,
        metavar="PRESET",
        help=(
            "the built-in to start from when creating a profile -- "
            + ", ".join(sorted(BUILTIN_PRESETS))
            + ". Required to create one, and refused when editing a saved one"
        ),
    )
    profile_set_parser.add_argument(
        "--flip-axis", choices=("long", "short"), default=None,
        help="which edge the operator turns each sheet on between passes",
    )
    profile_set_parser.add_argument(
        "--output-face", choices=("up", "down"), default=None,
        help="which way up sheets land in the output tray",
    )
    profile_set_parser.add_argument(
        "--feed-edge", choices=("top", "bottom"), default=None,
        help="which edge of the sheet feeds first",
    )
    profile_set_parser.add_argument(
        "--reverse-stack", action=argparse.BooleanOptionalAction, default=None,
        help=(
            "whether the printed stack must be turned over before the back "
            "pass. Use --no-reverse-stack for a printer that reloads in order"
        ),
    )
    profile_set_parser.add_argument(
        "--back-offset", type=_parse_offset_pair, default=None, metavar="X,Y",
        help=(
            "the registration correction: how far back-side content moves so "
            "it lands behind its front, e.g. 3,-2 or 0.5mm,-1mm. This is the "
            "correction applied, not the error measured"
        ),
    )
    profile_set_parser.add_argument(
        "--imageable-area", type=_parse_imageable_area, default=None,
        metavar="L,T,R,B",
        help=(
            "the printer's non-printable border as four margins -- left, top, "
            "right, bottom. Note the order differs from --crop's, matching how "
            "the profile stores it"
        ),
    )
    profile_set_parser.add_argument(
        "--force", action="store_true",
        help=(
            "apply a change to a saved calibration. Without it the change is "
            "shown field by field and refused"
        ),
    )
    profile_set_parser.set_defaults(func=_cmd_profile_set, _command="profile")

    return parser


def _make_output_encoding_safe() -> None:
    """Stop a non-ASCII path from crashing Deckle while reporting success.

    The Windows console defaults to a legacy code page (cp1252 here), which
    cannot encode most non-ASCII characters. Printing a path containing any
    of them raises ``UnicodeEncodeError`` from deep inside ``print`` --
    *after* the export has already succeeded. The user gets a traceback and
    a non-zero exit for a PDF that was written correctly, which is the worst
    possible combination: it looks like a failure and is not one.

    Accented characters in a person's name are enough to trigger it, so this
    is an ordinary case, not an exotic one.

    ``errors="replace"`` rather than forcing UTF-8: the console's encoding is
    the user's business, and overriding it could mangle output being piped
    somewhere that expects the code page. Replacing the unencodable
    characters degrades the *display* of a path while keeping the program
    alive and the exit code honest.

    :returns: nothing, and never raises -- hardening must not be the thing
        that breaks startup.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # a redirected stream that is not a TextIOWrapper
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - defensive
            # Never let hardening be the thing that breaks startup.
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI invocation.

    :param argv: the arguments, or ``None`` to read ``sys.argv``.
    :returns: the process exit code -- ``0`` on success, ``1`` for a source
        that could not be loaded or a destination that could not be
        written. Layout warnings go to stderr and never change this: a
        warning is advice, not a failure.
    :raises SystemExit: from argparse, for ``--help``, ``--version`` and
        malformed arguments.

    Loader and write failures are caught and reported as messages, because
    they are the user's problem and already name the remedy. Nothing else
    is: an unexpected exception should still produce a traceback, because a
    traceback is a bug report and a swallowed one is not.
    """
    _make_output_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
