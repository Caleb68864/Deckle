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
import dataclasses
import importlib.metadata
import json
import os
import re
import sys
import warnings
from typing import Sequence

from deckle import __version__ as _DECKLE_VERSION
from deckle.core.export import export as export_plan, proof_rule_length_pt
from deckle.core.printing import plan_passes
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
# a bug report. Resolved via importlib.metadata (installed-distribution
# metadata) rather than importing the packages themselves, so this never
# imports PySide6 -- and so deckle.cli never has to import deckle.app.
_VERSIONED_DISTRIBUTIONS = ("pikepdf", "pypdfium2", "img2pdf", "PySide6")


def _distribution_version(dist_name: str) -> str:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _version_string() -> str:
    parts = [f"deckle {_DECKLE_VERSION}"]
    parts.extend(
        f"{dist} {_distribution_version(dist)}" for dist in _VERSIONED_DISTRIBUTIONS
    )
    return "\n".join(parts)

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
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"invalid crop {value!r}: expected four insets -- left, bottom, "
            "right, top -- e.g. 0.5in,0.25in,0.5in,0.25in"
        )
    return tuple(_parse_length_pt(part) for part in parts)


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
    try:
        return PrinterProfile.load(name)
    except (OSError, ValueError, KeyError, TypeError):
        # No saved profile, or one that cannot be read. Either way the
        # built-ins are the next place to look, and a corrupt saved file
        # should not be more fatal than a missing one.
        pass
    preset = BUILTIN_PRESETS.get(name)
    if preset is not None:
        return preset
    print(
        f"error: no printer profile {name!r}. Built-in profiles: "
        f"{', '.join(sorted(BUILTIN_PRESETS))}. Calibrate a printer in the "
        "desktop app to save one under its own name.",
        file=sys.stderr,
    )
    return None


def _pass_for(plan, side: str, profile, sheets: list[int] | None):
    """The :class:`~deckle.core.printing.PrintPass` for one side.

    Everything here comes from ``plan_passes`` -- the sheet order, the
    half turn, the reload wording. The CLI decides none of it: a second
    implementation of the ordering table would be free to disagree with
    the desktop app about the same printer, and the paper would be wrong
    while both halves looked right.
    """
    passes = plan_passes(plan, profile, sheets=sheets)
    return next(print_pass for print_pass in passes if print_pass.side == side)


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


def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Which layout options the user actually typed, as flag names.

    :param args: the parsed arguments.
    :param parser: the parser they came from, for its defaults.
    :returns: the flags whose value differs from the default.

    Used to warn rather than silently ignore. Comparing against defaults is
    approximate -- typing the default value looks like not typing it -- but
    it errs toward silence, which is the right direction for a warning.
    """
    sub = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(getattr(args, "_command", ""))
            break
    if sub is None:
        return []
    given = []
    for action in sub._actions:
        if not action.option_strings or action.dest in ("output", "help", "source"):
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
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

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

    # Signature breakdown -- always printed, even under the MVP
    # (fold_scheme="none") path, where there are simply zero signatures.
    blank_total = sum(sig.blank_count for sig in plan.signatures)
    print(f"signature count: {len(plan.signatures)}")
    print(f"sheet count: {len(plan.sheets)}")
    print(f"blank count: {blank_total}")

    all_warnings = import_warnings + list(plan.warnings)
    if all_warnings:
        print("layout warnings:")
        for w in all_warnings:
            print(f"  [{w.kind}] sheet {w.sheet_index}: {w.detail}")
    else:
        print("layout warnings: none")
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
        print_pass = _pass_for(plan, args.pass_side, profile, selection)
        side = args.pass_side
        selection = print_pass.sheet_order
        rotate_180 = print_pass.side == "back" and print_pass.rotate_backs

    if args.back_offset is not None:
        back_offset = args.back_offset
        offset_source = "--back-offset"

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


def _report_rule(paper_width_pt: float) -> None:
    """Say what the printed rule should measure, and what it means if it
    does not.

    The rule is labelled on the sheet, but the number belongs here too: it
    is what turns "print this and look at it" into a check with a pass
    condition, and the person reading this line is the one about to walk to
    the printer.
    """
    length = proof_rule_length_pt(paper_width_pt)
    if length <= 0:
        print(
            "note: this sheet is too narrow for a rule, so none was drawn",
            file=sys.stderr,
        )
        return
    inches = int(round(length / 72.0))
    print(
        f"measure the printed rule: it should be {inches} in exactly. "
        "If it is short, the printer scaled the page -- turn off "
        '"fit to page" and print again.'
    )


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
    """
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    if args.output is not None and _report_output_problem(args.output, args.source):
        return 1

    plan = _impose_or_report(pages, settings)
    if plan is None:
        return 1
    _emit_warnings(pages, plan)

    text = format_schedule_text(
        build_schedule(plan, settings), os.path.basename(args.source)
    )

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
    _add_layout_args(export_parser)
    export_parser.set_defaults(func=_cmd_export, _command="export")

    info_parser = subparsers.add_parser("info", help="print page count, sizes, and layout warnings")
    info_parser.add_argument("source", help="a PDF file or a directory of images")
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
