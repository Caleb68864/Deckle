"""Machine-readable reports: the ``--json`` contract the CLI prints.

The CLI's stated purpose is scripting, and until this module existed that
meant regex-scraping prose written for a person -- ``page count: 12``,
``  [sheet_orientation] sheet 0: ...``. Prose is allowed to be reworded;
a script that greps it breaks silently when it is, and the failure looks
like "the document has no warnings" rather than like a broken script.

So the JSON is a **contract, not a dump**. Three consequences, all of
which are the point rather than incidental:

1. **The key names are stable.** They are pinned by
   ``tests/test_cli_json.py``, which compares the exact key set at every
   level of every document. Renaming or dropping one fails the suite --
   which is the whole value of the flag, because a caller cannot see the
   rename until their pipeline quietly produces nothing.
2. **The shapes are built here, not in the CLI.** ``deckle/cli.py`` was
   already ~1400 lines and interleaved seven value parsers with six
   commands when this module was written (roadmap M4, since done -- it is
   ``deckle/cli/`` now, and ``deckle.cli.report`` only *prints* these
   documents); another 150 lines of dict-building would have made
   that worse, and the shapes are worth unit-testing without going
   through argparse.
3. **Every document is self-describing.** ``report`` names which shape it
   is and ``report_version`` says which revision of that shape, so a
   script can refuse a document it was not written for instead of reading
   a missing key as a missing value.

Nothing here does I/O, formats prose, or decides anything: every number
comes from a plan, a schedule or a profile that some other module already
computed. It is the same rule ``deckle.core.schedule`` states for itself
-- **describe, never re-derive** -- and for the same reason. A report that
recomputed a sheet order would be a second implementation of the
imposition, free to disagree with the PDF in the user's hand.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from deckle import __version__ as _DECKLE_VERSION
from deckle.core.models import LayoutSettings, SheetPlan, SourcePage
from deckle.core.printing import PrintPass
from deckle.core.profiles import PrinterProfile
from deckle.core.schedule import Schedule

# Bumped when a document's shape changes in a way a reader cannot absorb
# -- a key renamed or removed, or an existing key's meaning changed.
#
# Adding a key does **not** bump it: a reader that indexes the keys it
# knows is unaffected by a new sibling, and bumping for an addition would
# make every consumer opt in again to something that cannot break them.
# Renaming or removing one does, because that reader gets a ``KeyError``
# at best and a silently absent value at worst.
REPORT_VERSION = 1


def _document(report: str, **fields: Any) -> dict:
    """One report, wrapped in the envelope every report shares.

    :param report: which shape this is -- ``"info"``, ``"schedule"``,
        ``"profile"``, ``"profile_list"`` or ``"print_plan"``.
    :param fields: the shape's own keys.
    :returns: the complete document.

    The envelope goes first so a human reading a raw dump sees what they
    are looking at before the payload, and ``deckle_version`` is in it
    because the first thing asked of any machine-readable output in a bug
    report is which build wrote it.
    """
    return {
        "report": report,
        "report_version": REPORT_VERSION,
        "deckle_version": _DECKLE_VERSION,
        **fields,
    }


def _warnings(warnings: Iterable) -> list[dict]:
    """Layout warnings as objects, never as the prose the terminal prints.

    ``kind`` is the field a script branches on -- it is a
    ``Literal`` on :class:`~deckle.core.models.LayoutWarning` and so is
    part of the model's own contract -- while ``detail`` is written for a
    person and may be reworded. Both are carried, in that order, so the
    stable one is the obvious one to use.
    """
    return [
        {"kind": w.kind, "sheet_index": w.sheet_index, "detail": w.detail}
        for w in warnings
    ]


def _size(width_pt: float, height_pt: float) -> dict:
    """A ``(width, height)`` pair as a named object.

    A two-element array would be shorter and is exactly how the same pair
    gets swapped by accident: ``paper[0]`` and ``paper[1]`` read the same
    whichever way round they are, and a landscape sheet reported as
    portrait is not visible until the paper is wrong.
    """
    return {"width_pt": width_pt, "height_pt": height_pt}


def info_report(
    source: str,
    source_kind: str,
    pages: Sequence[SourcePage],
    plan: SheetPlan,
    warnings: Iterable,
) -> dict:
    """What ``deckle-cli info --json`` prints.

    :param source: the path the user named.
    :param source_kind: ``"pdf"``, ``"images"`` or ``"project"``.
    :param pages: the loaded source pages.
    :param plan: the imposed plan.
    :param warnings: import warnings followed by the plan's own, already
        concatenated by the caller -- the terminal output merges them too,
        and a script that had to merge two lists to get the same answer
        would be reading a different report from the one a person sees.
    :returns: the document.

    ``page_sizes_pt`` carries a ``count`` per distinct size, which the
    prose form does not. A script picking "the size this document mostly
    is" cannot do it from a bare set, and a document with 264 pages at one
    size and two covers at another is the ordinary case rather than an
    exotic one.
    """
    counts: dict[tuple[float, float], int] = {}
    for page in pages:
        key = (page.ref.width_pt, page.ref.height_pt)
        counts[key] = counts.get(key, 0) + 1

    return _document(
        "info",
        source=source,
        source_kind=source_kind,
        page_count=len(pages),
        skipped_page_count=sum(1 for page in pages if page.skipped),
        page_sizes_pt=[
            {"width_pt": width, "height_pt": height, "count": counts[(width, height)]}
            for width, height in sorted(counts)
        ],
        paper_pt=_size(*plan.paper_pt),
        sheet_count=len(plan.sheets),
        signature_count=len(plan.signatures),
        blank_count=sum(signature.blank_count for signature in plan.signatures),
        warnings=_warnings(warnings),
    )


def _sheet_instruction(sheet) -> dict:
    """One sheet's row of the bench schedule.

    ``front_pages``/``back_pages`` keep ``None`` for a blank rather than
    substituting ``0`` or ``""``. JSON has a null and a blank leaf is
    genuinely the absence of a page number, so any other encoding would
    need the reader to know which sentinel Deckle chose.
    """
    return {
        "sheet_index": sheet.sheet_index,
        "position": sheet.position,
        "is_outermost": sheet.is_outermost,
        "front_pages": list(sheet.front_pages),
        "back_pages": list(sheet.back_pages),
    }


def schedule_report(source: str, schedule: Schedule) -> dict:
    """What ``deckle-cli schedule --json`` prints.

    :param source: the path the user named.
    :param schedule: the schedule built from the plan.
    :returns: the document.

    A flat-sheet job has no signatures, and this reports the empty list
    rather than inventing one -- the same refusal
    :func:`deckle.core.schedule.build_schedule` already makes. One page
    per side is not a folded book.
    """
    return _document(
        "schedule",
        source=source,
        fold_scheme=schedule.fold_scheme,
        sheets_total=schedule.sheets_total,
        blank_total=schedule.blank_total,
        signature_count=schedule.signature_count,
        sewing_stations=schedule.sewing_stations,
        sewing_margin_pt=schedule.sewing_margin_pt,
        paper_thickness_pt=schedule.paper_thickness_pt,
        spine_width_pt=(
            None
            if schedule.spine_width_pt is None
            else {
                "low_pt": schedule.spine_width_pt[0],
                "high_pt": schedule.spine_width_pt[1],
            }
        ),
        duplex_flip_edge=schedule.duplex_flip_edge,
        notes=list(schedule.notes),
        signatures=[
            {
                "index": signature.index,
                "sheet_count": signature.sheet_count,
                "page_count": signature.page_count,
                "blank_count": signature.blank_count,
                "sheets": [
                    _sheet_instruction(sheet) for sheet in signature.sheets
                ],
            }
            for signature in schedule.signatures
        ],
    )


def profile_fields(profile: PrinterProfile) -> dict:
    """A profile's own stored fields, as JSON.

    Written out field by field rather than through ``dataclasses.asdict``,
    which would make the wire format follow the dataclass automatically --
    and that is precisely what a contract must not do. Renaming a field
    would then rename a documented key with nothing failing, which is the
    silent break this whole module exists to prevent.

    ``imageable_area_pt`` stays a four-element array in the stored order,
    ``(left, top, right, bottom)`` **margins** -- see
    ``deckle.app.views.preview_view.imageable_rect_pt``, which is what
    consumes it. It is deliberately not expanded into named keys here: the
    order is the file format's, and one place inventing names for it is
    how the app and the CLI would come to disagree about which number is
    the top margin.

    :param profile: the profile to describe.
    :returns: its fields.
    """
    return {
        "version": profile.version,
        "flip_axis": profile.flip_axis,
        "output_face": profile.output_face,
        "feed_edge": profile.feed_edge,
        "reverse_stack": profile.reverse_stack,
        "imageable_area_pt": list(profile.imageable_area_pt),
        "calibrated_at": profile.calibrated_at,
        "calibration_version": profile.calibration_version,
        "back_offset_x_pt": profile.back_offset_x_pt,
        "back_offset_y_pt": profile.back_offset_y_pt,
    }


def profile_entry(
    name: str,
    profile: PrinterProfile | None,
    origin: str,
    path: str | None = None,
    error: str | None = None,
) -> dict:
    """One profile as it appears in a listing or a ``show``.

    ``origin`` is the field that decides whether a number was *measured*.
    ``"saved"`` came from a calibration run against that physical printer;
    ``"builtin"`` is a generic stand-in. They are not interchangeable, and
    a listing that did not distinguish them would let a script pick the
    stand-in believing it had the measurement.

    ``profile`` is ``None`` exactly when ``error`` is set -- a stored file
    that cannot be read. Reported rather than omitted, because a
    calibration that has become unreadable is the one thing about that
    printer its owner most needs to be told.
    """
    return {
        "name": name,
        "origin": origin,
        "path": path,
        "error": error,
        "profile": None if profile is None else profile_fields(profile),
    }


def profile_report(
    name: str,
    profile: PrinterProfile,
    origin: str,
    path: str | None = None,
) -> dict:
    """What ``deckle-cli profile show --json`` prints."""
    return _document(
        "profile", **profile_entry(name, profile, origin, path=path)
    )


def profile_list_report(entries: Sequence[dict]) -> dict:
    """What ``deckle-cli profile list --json`` prints.

    :param entries: rows built by :func:`profile_entry` -- the CLI
        builds them, being the layer that knows which files exist and
        which of them failed to parse.
    :returns: the document.
    """
    return _document("profile_list", profiles=list(entries))


def print_plan_report(
    source: str,
    plan: SheetPlan,
    settings: LayoutSettings,
    passes: Sequence[PrintPass],
    profile_name: str,
    profile: PrinterProfile,
    profile_origin: str,
    outputs: Sequence[str | None] | None = None,
) -> dict:
    """What ``deckle-cli print --json`` prints: the manual-duplex run's plan.

    Every field comes from ``plan_passes`` -- the sheet order, the half
    turn, the reload wording. A second implementation of the ordering
    table would be free to disagree with the desktop app about the same
    printer, and the paper would be wrong while both halves looked right.

    :param source: the path the user named.
    :param plan: the imposed plan.
    :param settings: the layout it was imposed with, for the grain and
        thickness a bench operator sets before committing paper.
    :param passes: the planned passes, in the order they are run.
    :param profile_name: the name the user asked for.
    :param profile: the profile that name resolved to.
    :param profile_origin: ``"saved"`` or ``"builtin"``.
    :param outputs: the file written for each pass, positionally, or
        ``None`` throughout when nothing was written.
    :returns: the document.

    ``submitted`` is always ``false`` and says so in the document rather
    than only in the prose, because "did this reach a printer?" is exactly
    the question a script must not have to infer. See
    ``docs/decisions.md`` for why the CLI plans a run it does not submit.
    """
    written = list(outputs) if outputs is not None else [None] * len(passes)
    return _document(
        "print_plan",
        source=source,
        submitted=False,
        printer_profile=profile_entry(profile_name, profile, profile_origin),
        paper_pt=_size(*plan.paper_pt),
        sheet_count=len(plan.sheets),
        grain=settings.grain,
        passes=[
            {
                "index": print_pass.index,
                "side": print_pass.side,
                "sheet_count": len(print_pass.sheet_order),
                "sheet_order": list(print_pass.sheet_order),
                "rotate_backs": print_pass.rotate_backs,
                "reload_instruction": print_pass.reload_instruction,
                "output": written[print_pass.index]
                if print_pass.index < len(written)
                else None,
            }
            for print_pass in passes
        ],
        warnings=_warnings(plan.warnings),
    )
