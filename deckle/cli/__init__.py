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
import sys
from typing import Sequence

from deckle.core import about
from deckle.core.paper import GRADE_BASIS_SIZES_IN, PAPER_BULK
from deckle.core.profiles import BUILTIN_PRESETS
from deckle.cli import commands, report, values
from deckle.cli.commands import (
    PROJECT_SUFFIX,
    _build_layout_settings,
    _cmd_crop_preview,
    _cmd_dummy,
    _cmd_export,
    _cmd_impose,
    _cmd_info,
    _cmd_print,
    _cmd_profile_list,
    _cmd_profile_set,
    _cmd_profile_show,
    _cmd_schedule,
    _impose_or_report,
    _is_project_file,
    _load_project_or_report,
    _load_source,
    _load_source_or_report,
    _resolve_input,
    _strategy_for,
)
from deckle.cli.report import (
    _emit_json,
    _emit_warnings,
    _format_insets,
    _format_sheet_list,
    _json_stdout,
    _profile_summary,
    _report_export_dry_run,
    _report_missing_sheets,
    _report_output_problem,
    _report_registration,
    _report_rule,
    _report_write_failure,
    _resolve_profile,
    _resolve_profile_origin,
)
from deckle.cli.values import (
    A4_PT,
    LEGAL_PT,
    LETTER_PT,
    MAX_PAPER_PT,
    MIN_PAPER_PT,
    _parse_crop,
    _parse_imageable_area,
    _parse_length_pt,
    _parse_offset_pair,
    _parse_page_selection,
    _parse_paper,
    _parse_paper_weight,
    _parse_sheet_selection,
    _parse_signature_lengths,
    _parse_station_positions,
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


def build_parser() -> argparse.ArgumentParser:
    """The full argument parser for ``deckle``.

    Three subcommands -- ``impose``, ``export`` and ``info`` -- sharing one
    set of layout options, so a plan previewed with ``info`` is the plan
    ``export`` writes.

    :returns: the parser. Each subparser sets a ``func`` default, which is
        what :func:`main` dispatches on.
    """
    parser = argparse.ArgumentParser(prog="deckle", description="Impose and print booklets.")
    # Derived once, here, rather than by each `.deckle` load building a
    # throwaway parser to read it back off. `_layout_flags_given` needs it
    # and has no other way to tell a layout flag from `--sheets`.
    layout_dests = _layout_dests()
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
    impose_parser.set_defaults(
        func=commands._cmd_impose, _command="impose",
        _subparser=impose_parser, _layout_dests=layout_dests,
    )

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
    export_parser.set_defaults(
        func=commands._cmd_export, _command="export",
        _subparser=export_parser, _layout_dests=layout_dests,
    )

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
    info_parser.set_defaults(
        func=commands._cmd_info, _command="info",
        _subparser=info_parser, _layout_dests=layout_dests,
    )

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
    schedule_parser.set_defaults(
        func=commands._cmd_schedule, _command="schedule",
        _subparser=schedule_parser, _layout_dests=layout_dests,
    )

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
    preview_parser.set_defaults(
        func=commands._cmd_crop_preview, _command="crop-preview", _subparser=preview_parser,
    )

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
    dummy_parser.set_defaults(
        func=commands._cmd_dummy, _command="dummy", _subparser=dummy_parser,
    )

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
    print_parser.set_defaults(
        func=commands._cmd_print, _command="print",
        _subparser=print_parser, _layout_dests=layout_dests,
    )

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
    profile_list_parser.set_defaults(
        func=commands._cmd_profile_list, _command="profile", _subparser=profile_list_parser,
    )

    profile_show_parser = profile_subparsers.add_parser(
        "show", help="print one profile's stored values in full"
    )
    profile_show_parser.add_argument("name", help="the profile or printer name")
    profile_show_parser.add_argument(
        "--json", action="store_true",
        help="emit the profile as one JSON document with stable key names",
    )
    profile_show_parser.set_defaults(
        func=commands._cmd_profile_show, _command="profile", _subparser=profile_show_parser,
    )

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
    profile_set_parser.set_defaults(
        func=commands._cmd_profile_set, _command="profile", _subparser=profile_set_parser,
    )

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
