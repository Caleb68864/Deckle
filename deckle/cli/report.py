"""Saying what went wrong, or what was measured, and recording it.

Every function here writes to stdout or stderr and, where the event is
worth a bug report, calls into ``deckle.core.diagnostics``. None of them
loads, imposes or exports anything -- the ``*_or_report`` wrappers that
do both live in :mod:`deckle.cli.commands`, beside the operation they
wrap.

The split that matters to a user is which stream a line goes to.
Warnings, notes and errors go to **stderr** so ``deckle export`` keeps a
clean stdout for scripting; the one thing a command has to say about what
it did -- ``wrote out.pdf`` -- goes to stdout. Keeping both in one module
is what makes that rule checkable by reading it.

``--json`` is the same rule stated harder: :func:`_json_stdout` moves the
prose aside for the duration of a command so stdout holds exactly one
document. Not to be confused with :mod:`deckle.core.report`, which builds
those documents; this module only prints them.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Sequence

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.export import RULE_TOO_NARROW_NOTE, proof_rule_advice
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile



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



def _format_insets(insets) -> str:
    """Insets as a `--crop` value, so the output can be pasted back in."""
    return ",".join(f"{v:.1f}pt" for v in insets)



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
