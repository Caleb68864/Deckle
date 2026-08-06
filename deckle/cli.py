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
import importlib.metadata
import json
import os
import re
import sys
import warnings
from typing import Sequence

from deckle import __version__ as _DECKLE_VERSION
from deckle.core.export import export as export_plan, proof_rule_length_pt
from deckle.core.layout import GutterShiftStrategy, LayoutStrategy, SaddleStitchStrategy
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.loader import SourceLoadError, load_image_dir, load_pdf
from deckle.core.models import LayoutSettings, Project, SourcePage
from deckle.core.outputs import describe_write_failure, output_path_problem
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
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)x([0-9]*\.?[0-9]+)(in|pt|mm)?\s*$", value, re.IGNORECASE)
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


def _parse_sheet_selection(value: str) -> list[int]:
    """A ``--sheets`` value as the sheet indices it names, in order.

    Accepts single numbers and inclusive ranges, comma-separated:
    ``0``, ``2,0``, ``1-3``, ``0,2-4``. Indices are **0-based**, matching
    every other sheet number Deckle prints -- the layout warnings, the
    schedule's gathering list, ``Sheet.index``. A 1-based flag would
    disagree with all three.

    Order is preserved rather than sorted, and repeats are kept: both are
    what :func:`deckle.core.export.export` documents for its ``sheets``
    argument, and neither is worth silently correcting -- ``2,0`` is a
    reasonable thing to ask for.

    Open-ended ranges (``2-``) are deliberately not accepted: the total
    sheet count is not known until the document is imposed, which is after
    argparse has run, so the flag cannot honour one at the point it is read.

    :param value: the raw flag text.
    :returns: the indices, in the order named.
    :raises argparse.ArgumentTypeError: empty, malformed, negative, or a
        range that runs backwards.
    """
    selection: list[int] = []
    items = [item.strip() for item in value.split(",")]
    if not value.strip() or any(not item for item in items):
        raise argparse.ArgumentTypeError(
            f"invalid sheets {value!r}: expected sheet numbers like 0, 2,0 "
            "or 0,2-4 -- counting from 0, as the schedule and the warnings do"
        )
    for item in items:
        bounds = [part.strip() for part in item.split("-")]
        if len(bounds) > 2 or any(not part.isdigit() for part in bounds):
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: {item!r} is not a sheet number "
                "or an inclusive range like 2-4"
            )
        if len(bounds) == 1:
            selection.append(int(bounds[0]))
            continue
        start, end = int(bounds[0]), int(bounds[1])
        if end < start:
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: the range {item!r} runs backwards"
            )
        selection.extend(range(start, end + 1))
    return selection


def _report_missing_sheets(plan, selection: list[int], total: int) -> bool:
    """Refuse a selection naming a sheet the document does not have.

    :func:`deckle.core.export.export` skips an unknown index rather than
    raising, which is right for a library and wrong for a command: asking
    for sheet 99 of a four-sheet book would write a PDF with nothing in it
    and print ``wrote proof.pdf``. A file that exists and is empty, from a
    command that reported success, is the worst available outcome.

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
    return pages, _build_layout_settings(args)


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
    return LayoutSettings(
        paper=paper,
        gutter_pt=args.gutter,
        binding_edge=args.binding_edge,
        fold_scheme=args.fold_scheme,
        sheets_per_signature=args.sheets_per_signature,
        blank_mode=args.blank_mode,
        sewing_stations=args.sewing_stations,
        paper_thickness_pt=args.paper_thickness,
        grain=args.grain,
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
        "--sewing-stations", type=int, default=3,
        help="number of sewing station marks per signature, under --fold-scheme folio (default: 3)",
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
    plan = _strategy_for(settings).impose(pages, settings)

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


def _cmd_export(args: argparse.Namespace) -> int:
    if _report_output_problem(args.output, args.source):
        return 1
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    plan = _strategy_for(settings).impose(pages, settings)
    _emit_warnings(pages, plan)

    selection = args.sheets
    if selection is not None and _report_missing_sheets(
        plan, selection, len(plan.sheets)
    ):
        return 1

    try:
        export_plan(plan, args.output, sheets=selection, rule=args.rule)
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

    _emit_warnings(pages, _strategy_for(settings).impose(pages, settings))
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

    plan = _strategy_for(settings).impose(pages, settings)
    _emit_warnings(pages, plan)

    text = format_schedule_text(
        build_schedule(plan, settings), os.path.basename(args.source)
    )

    if args.output is None:
        print(text, end="")
        return 0

    try:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        print(f"error: {describe_write_failure(args.output, exc)}", file=sys.stderr)
        log_exception("output_write_failed", exc, path=args.output)
        return 1
    print(f"wrote {args.output}")
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
