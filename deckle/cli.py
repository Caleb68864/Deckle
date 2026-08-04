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
import os
import re
import sys
from typing import Sequence

from deckle.core.export import export as export_plan
from deckle.core.layout import GutterShiftStrategy
from deckle.core.loader import EncryptedPdfError, load_image_dir, load_pdf
from deckle.core.models import LayoutSettings, Project, SourcePage
from deckle.core.project_io import SourceChangedWarning, save_project

LETTER_PT = (612.0, 792.0)
A4_PT = (595.28, 841.89)
LEGAL_PT = (612.0, 1008.0)

_PAPER_PRESETS = {
    "letter": LETTER_PT,
    "a4": A4_PT,
    "legal": LEGAL_PT,
}

_UNIT_TO_PT = {
    "in": 72.0,
    "pt": 1.0,
    "mm": 72.0 / 25.4,
}

_LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(in|pt|mm)\s*$", re.IGNORECASE)


def _parse_length_pt(value: str) -> float:
    """Parse a length with a unit suffix (``0.75in``, ``18pt``, ``5mm``) to points."""
    match = _LENGTH_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid length {value!r}: expected a number followed by in/pt/mm"
        )
    number, unit = match.groups()
    return float(number) * _UNIT_TO_PT[unit.lower()]


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
    if os.path.isdir(path):
        return list(load_image_dir(path))
    return load_pdf(path)


def _build_layout_settings(args: argparse.Namespace) -> LayoutSettings:
    return LayoutSettings(
        paper=args.paper,
        gutter_pt=args.gutter,
        binding_edge=args.binding_edge,
        scale_mode=args.scale_mode,
    )


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
        "--scale-mode", choices=["fit_height", "fixed_gutter"], default="fit_height",
        help="how source content is scaled onto the sheet (default: fit_height)",
    )
    parser.add_argument(
        "--binding-edge", choices=["left", "right"], default="left",
        help="which edge the gutter shifts toward (default: left)",
    )


def _cmd_info(args: argparse.Namespace) -> int:
    try:
        pages = _load_source(args.source)
    except EncryptedPdfError as exc:
        print(f"error: password-protected PDF: {exc.path}", file=sys.stderr)
        return 1

    print(f"page count: {len(pages)}")
    sizes = sorted({(p.ref.width_pt, p.ref.height_pt) for p in pages})
    print("detected page sizes (pt):")
    for w, h in sizes:
        print(f"  {w:.2f} x {h:.2f}")

    import_warnings = list(getattr(pages, "warnings", []))
    settings = _build_layout_settings(args)
    plan = GutterShiftStrategy().impose(pages, settings)

    all_warnings = import_warnings + list(plan.warnings)
    if all_warnings:
        print("layout warnings:")
        for w in all_warnings:
            print(f"  [{w.kind}] sheet {w.sheet_index}: {w.detail}")
    else:
        print("layout warnings: none")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        pages = _load_source(args.source)
    except EncryptedPdfError as exc:
        print(f"error: password-protected PDF: {exc.path}", file=sys.stderr)
        return 1

    settings = _build_layout_settings(args)
    plan = GutterShiftStrategy().impose(pages, settings)
    export_plan(plan, args.output)
    print(f"wrote {args.output}")
    return 0


def _cmd_impose(args: argparse.Namespace) -> int:
    try:
        pages = _load_source(args.source)
    except EncryptedPdfError as exc:
        print(f"error: password-protected PDF: {exc.path}", file=sys.stderr)
        return 1

    settings = _build_layout_settings(args)
    project = Project(pages=list(pages), layout=settings, printer=args.printer)
    try:
        save_project(project, args.output)
    except SourceChangedWarning as exc:
        print(f"error: source changed: {exc.path}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deckle", description="Impose and print booklets.")
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
