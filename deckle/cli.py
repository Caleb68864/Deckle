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


def _output_path_problem(out_path: str) -> str | None:
    """Why ``out_path`` cannot be written to, or ``None`` if it looks fine.

    Checked in the CLI *before* any imposition work, so a mistyped
    destination costs no time, and so the message names the path the user
    typed rather than the temp file the exporter was about to rename.

    This cannot be exhaustive -- a file locked by another process passes
    every check here and still fails at the final rename -- so the callers
    also handle ``OSError`` from the write itself.
    """
    if os.path.isdir(out_path):
        return (
            f"cannot write to {out_path}: that is an existing folder, not a "
            "file. Give a file name instead, e.g. "
            f"{os.path.join(out_path, 'booklet.pdf')}."
        )

    directory = os.path.dirname(os.path.abspath(out_path)) or "."
    if not os.path.isdir(directory):
        anchor = os.path.splitdrive(os.path.abspath(out_path))[0]
        if anchor and not os.path.exists(anchor + os.sep):
            return (
                f"cannot write to {out_path}: the drive {anchor} does not "
                "exist or is not connected. Check the drive letter, or "
                "choose a folder on a drive that is available."
            )
        return (
            f"cannot write to {out_path}: the folder {directory} does not "
            "exist. Create it first, or choose a folder that does."
        )
    if not os.access(directory, os.W_OK):
        return (
            f"cannot write to {out_path}: the folder {directory} is not "
            "writable. Choose another location, or grant yourself write "
            "permission on that folder."
        )
    if os.path.exists(out_path) and not os.access(out_path, os.W_OK):
        return (
            f"cannot write to {out_path}: the file is read-only. Clear its "
            "read-only flag, or choose a different output file."
        )
    return None


def _report_output_problem(out_path: str) -> bool:
    """Print the problem with ``out_path``, if any. ``True`` means stop."""
    problem = _output_path_problem(out_path)
    if problem is None:
        return False
    print(f"error: {problem}", file=sys.stderr)
    log_event("output_path_rejected", path=out_path, detail=problem)
    return True


def _report_write_failure(out_path: str, exc: OSError) -> None:
    """Explain an ``OSError`` raised while writing ``out_path``.

    The overwhelmingly common case on Windows is that the previous export
    is still open in a PDF viewer, which holds the file and makes the
    exporter's final rename fail with a bare ``[WinError 5] Access is
    denied`` naming a temp file the user has never heard of.
    """
    if isinstance(exc, PermissionError):
        detail = (
            "permission denied -- is the file already open in a PDF viewer? "
            "Close it and try again, or export to a different name."
        )
    else:
        detail = f"{exc.strerror or exc}. Check the path, the drive, and free disk space."
    print(f"error: cannot write to {out_path}: {detail}", file=sys.stderr)
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
    if _report_output_problem(args.output):
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
    if _report_output_problem(args.output):
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


def build_parser() -> argparse.ArgumentParser:
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
