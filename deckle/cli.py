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
import os
import re
import sys
from typing import Sequence

from deckle import __version__ as _DECKLE_VERSION
from deckle.core.export import export as export_plan
from deckle.core.layout import GutterShiftStrategy, LayoutStrategy, SaddleStitchStrategy
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.loader import SourceLoadError, load_image_dir, load_pdf
from deckle.core.models import LayoutSettings, Project, SourcePage
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.schedule import build_schedule, format_schedule_text
from deckle.core.project_io import SourceChangedWarning, save_project

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
    preset = _PAPER_PRESETS.get(value.lower())
    if preset is not None:
        return preset
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)x([0-9]*\.?[0-9]+)(in|pt|mm)?\s*$", value, re.IGNORECASE)
    if match:
        w, h, unit = match.groups()
        factor = _UNIT_TO_PT[(unit or "pt").lower()]
        return (float(w) * factor, float(h) * factor)
    raise argparse.ArgumentTypeError(
        f"invalid paper {value!r}: expected a preset ({', '.join(_PAPER_PRESETS)}) "
        "or WxH[unit]"
    )


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
    return LayoutSettings(
        paper=args.paper,
        gutter_pt=args.gutter,
        binding_edge=args.binding_edge,
        fold_scheme=args.fold_scheme,
        sheets_per_signature=args.sheets_per_signature,
        blank_mode=args.blank_mode,
        sewing_stations=args.sewing_stations,
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
        "--sewing-stations", type=int, default=3,
        help="number of sewing station marks per signature, under --fold-scheme folio (default: 3)",
    )


def _cmd_info(args: argparse.Namespace) -> int:
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1

    print(f"page count: {len(pages)}")
    sizes = sorted({(p.ref.width_pt, p.ref.height_pt) for p in pages})
    print("detected page sizes (pt):")
    for w, h in sizes:
        print(f"  {w:.2f} x {h:.2f}")

    import_warnings = list(getattr(pages, "warnings", []))
    settings = _build_layout_settings(args)
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
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1

    settings = _build_layout_settings(args)
    plan = _strategy_for(settings).impose(pages, settings)
    _emit_warnings(pages, plan)
    try:
        export_plan(plan, args.output)
    except OSError as exc:
        _report_write_failure(args.output, exc)
        return 1
    print(f"wrote {args.output}")
    return 0


def _cmd_impose(args: argparse.Namespace) -> int:
    if _report_output_problem(args.output, args.source):
        return 1
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1

    settings = _build_layout_settings(args)
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
    pages = _load_source_or_report(args.source)
    if pages is None:
        return 1

    if args.output is not None and _report_output_problem(args.output, args.source):
        return 1

    settings = _build_layout_settings(args)
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
    impose_parser.set_defaults(func=_cmd_impose)

    export_parser = subparsers.add_parser("export", help="impose and export a source directly to PDF")
    export_parser.add_argument("source", help="a PDF file or a directory of images")
    export_parser.add_argument("-o", "--output", required=True, help="path to write the exported PDF")
    _add_layout_args(export_parser)
    export_parser.set_defaults(func=_cmd_export)

    info_parser = subparsers.add_parser("info", help="print page count, sizes, and layout warnings")
    info_parser.add_argument("source", help="a PDF file or a directory of images")
    _add_layout_args(info_parser)
    info_parser.set_defaults(func=_cmd_info)

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
    schedule_parser.set_defaults(func=_cmd_schedule)

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
