"""Deckle's headless CLI.

A shipped MVP feature, not a test harness -- it is also what lets the
golden-fixture regression run headlessly in CI, since ``deckle.core`` is
Qt-free. Subcommands: ``impose``, ``export``, ``info``, ``schedule``,
``crop-preview``, ``dummy``, ``print`` and ``profile``.

Imports only the pure core package -- never the Qt-based desktop app layer
or any Qt binding -- so it stays runnable in a headless container with no
display server present. ``tests/test_cli.py`` asserts that statically over
every file in this package, and ``tests/test_cli_print.py`` over every
import node in it including nested ones -- so a lazy import cannot keep
the letter of the promise while breaking it.

Four modules, in the order one invocation travels through them:
:mod:`~deckle.cli.options` declares the flags,
:mod:`~deckle.cli.values` turns each flag's text into a value,
:mod:`~deckle.cli.commands` does the work, and
:mod:`~deckle.cli.report` says what happened. They import in that one
direction and no other. This module holds ``main`` and re-exports the
names that were importable from ``deckle.cli`` when it was a single file,
so ``from deckle.cli import main`` still means what it always did.

A re-export is a **different binding**, though, so a test that needs to
*patch* one of these names must patch it on the module that defines it.
``python -m deckle.cli`` runs the CLI; ``python -m deckle`` launches the
GUI and blocks. See :mod:`deckle.cli.__main__`.
"""

from __future__ import annotations

import sys
from typing import Sequence

from deckle.cli import commands, options, report, values
from deckle.cli.options import _version_string, build_parser
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
    parser = options.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
