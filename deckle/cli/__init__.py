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
import json
import os
import sys
import warnings
from typing import Sequence

from deckle import __version__ as _DECKLE_VERSION
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
from deckle.core.export import export as export_plan
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
from deckle.core.outputs import describe_write_failure
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
from deckle.cli import report, values
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


def _source_kind(path: str) -> str:
    """What ``source`` is, in the vocabulary the reports use.

    :param path: the path the user named.
    :returns: ``"project"``, ``"images"`` or ``"pdf"``.
    """
    if _is_project_file(path):
        return "project"
    return "images" if os.path.isdir(path) else "pdf"


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


def _layout_flags_given(args: argparse.Namespace) -> list[str]:
    """Which layout options the user actually typed, as flag names.

    :param args: the parsed arguments. Both things this needs are on the
        namespace, put there by :func:`build_parser`: ``_subparser`` is the
        subparser they came from, for its defaults, and ``_layout_dests``
        is the set :func:`_layout_dests` derived once.
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

    This used to take a parser and be handed a **freshly built one**: six
    subparsers and every flag and help string in the program, constructed
    on every ``.deckle`` load, then walked for a private
    ``argparse._SubParsersAction`` and indexed by ``args._command`` to
    rediscover the subparser the arguments had just come out of. It then
    built a *second* throwaway parser, in :func:`_layout_dests`, to read
    back the dests of the flags that subparser already carried.
    ``set_defaults`` carries both instead -- which is how ``_command``
    already reached here -- so ``build_parser`` runs exactly once per
    invocation and this function constructs nothing at all.
    """
    sub = getattr(args, "_subparser", None)
    if sub is None:
        return []
    layout = getattr(args, "_layout_dests", frozenset())
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
        ignored = _layout_flags_given(args)
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
        func=_cmd_impose, _command="impose",
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
        func=_cmd_export, _command="export",
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
        func=_cmd_info, _command="info",
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
        func=_cmd_schedule, _command="schedule",
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
        func=_cmd_crop_preview, _command="crop-preview", _subparser=preview_parser,
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
        func=_cmd_dummy, _command="dummy", _subparser=dummy_parser,
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
        func=_cmd_print, _command="print",
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
        func=_cmd_profile_list, _command="profile", _subparser=profile_list_parser,
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
        func=_cmd_profile_show, _command="profile", _subparser=profile_show_parser,
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
        func=_cmd_profile_set, _command="profile", _subparser=profile_set_parser,
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
