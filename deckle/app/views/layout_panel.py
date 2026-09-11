"""LayoutPanel: live controls for gutter, binding edge, scale mode, etc.

Changing any layout setting on this panel does two things, immediately and
synchronously: it replaces ``AppState.project.layout`` via ``AppState.mutate``
(so it participates in undo/autosave like every other project change), and
it re-runs ``GutterShiftStrategy.impose`` over the *whole* document to
produce a fresh ``SheetPlan`` -- arithmetic only, no rasterization. The
panel then emits that plan via ``layout_changed`` so a listening
``PreviewView`` can re-render just the sheet currently on screen; see
``deckle/app/views/preview_view.py``.

There is deliberately no scale-mode control: content is always fitted to
the content box, which fills the page height whenever geometry allows.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Literal

from deckle.app.state import AppState
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
import os

from deckle.core.defaults import defaults_path, forget_defaults, save_defaults
from deckle.core.diagnostics import log_event, log_exception
# Aliased on import. `PAPER_PRESETS` in this module has meant sheet
# *sizes* (A4, Letter) since it was written, and the new list is paper
# *stock* (80gsm copier). Two meanings of "paper" in one file is how a
# reader ends up wiring a dropdown to the wrong one.
from deckle.core.paper import PT_PER_MM
from deckle.core.paper import PAPER_PRESETS as PAPER_STOCKS
from deckle.core.paper import (
    GRADE_BASIS_SIZES_IN,
    PAPER_BULK,
    caliper_pt_from_gsm,
    gsm_from_pounds,
    suggest_sheets_per_signature,
)
from deckle.core.models import Project, SheetPlan
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.schedule import build_schedule, format_schedule_text

BINDING_EDGES: tuple[str, ...] = ("left", "right")

LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale")

FOLD_SCHEMES: tuple[str, ...] = ("none", "folio")

BLANK_MODES: tuple[str, ...] = ("end", "balanced")

#: Paper grain, in the order the combo offers it. "Unknown" leads because it
#: is the honest default -- most people have not checked their stock.
GRAINS: tuple[tuple[str, str], ...] = (
    ("unknown", "Unknown"),
    ("long", "Long grain"),
    ("short", "Short grain"),
)

SAVE_DEFAULTS_TOOLTIP = (
    "Remember these settings and start every new project from them.\n\n"
    "Paper, orientation, grain, thickness, margins, gutter, how it folds, "
    "trim and sewing -- everything on this panel except the crop boxes and "
    "the gathering list, which are measured from one document and mean "
    "nothing on the next.\n\n"
    "Affects new projects only. Opening a saved .deckle always uses that "
    "project's own settings."
)

FORGET_DEFAULTS_TOOLTIP = (
    "Delete your saved defaults, so new projects go back to Deckle's own: "
    "US Letter, portrait, no gutter, no margins, flat sheets.\n\n"
    "Does not change the project you have open."
)

SCHEDULE_TOOLTIP = (
    "Write the binding schedule to a text file, and keep it at the bench "
    "-- the imposed PDF says nothing about what to do with the paper.\n\n"
    "Under Signatures: which sheets gather into each signature, which way "
    "round they nest, where the blanks fall, and where to pierce for "
    "sewing.\n\n"
    "Under Flat sheets: how the stack collates, how thick the block will "
    "be so you can cut boards against it, and what to set at the printer "
    "-- actual size, and which edge to turn the sheet about.\n\n"
    "Both carry the print-sheet-0-first check."
)

# -- pure layout-settings mutators, each routed through AppState.mutate ----


def set_gutter_pt(project: Project, gutter_pt: float) -> Project:
    """Set the gutter -- which is also the spine margin.

    :param project: the project to derive a new one from.
    :param gutter_pt: the new gutter, in points.
    :returns: a new project. Nothing is rasterized; the caller recomputes
        the plan.
    """
    return replace(project, layout=replace(project.layout, gutter_pt=gutter_pt))


#: The three editable margin fields. The fourth page edge is the spine,
#: whose margin is ``gutter_pt`` -- edited separately since it behaves
#: differently (it mirrors between recto and verso).
MARGIN_FIELDS: tuple[str, ...] = ("margin_top_pt", "margin_bottom_pt", "margin_outer_pt")


def set_margin(project: Project, field: str, points: float, *, linked: bool = False) -> Project:
    """Set one margin, or all three when ``linked``.

    :param project: the project to derive a new one from.
    :param field: which margin, one of :data:`MARGIN_FIELDS`. Ignored when
        ``linked``.
    :param points: the new value, in points.
    :param linked: apply to all three margins at once.
    :returns: a new project.
    :raises TypeError: ``field`` is not a ``LayoutSettings`` field.
    """
    updates = {f: points for f in MARGIN_FIELDS} if linked else {field: points}
    return replace(project, layout=replace(project.layout, **updates))


def set_margins_linked(project: Project, linked: bool) -> Project:
    """Record whether the UI edits the three margins as one value.

    Purely presentational -- the imposer always reads the three fields
    independently. Persisted so reopening restores how you were working,
    not just the numbers.

    :param project: the project to derive a new one from.
    :param linked: the new setting.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, margins_linked=linked))


#: Where spare horizontal width goes. Index 0 is the panel default.
SLACK_TARGETS: tuple[tuple[str, str], ...] = (
    ("gutter", "Gutter"),
    ("outer", "Fore-edge"),
    ("split", "Split evenly"),
)
#: Kept short deliberately: the full sentence for each option lives in the
#: control's tooltip. A dropdown wide enough to spell out
#: "Gutter (fore-edge exact, gutter varies)" forces a horizontal scrollbar
#: onto the whole settings column, which is the one kind of scrolling a
#: form should never need.


def set_slack_to(project: Project, slack_to: str) -> Project:
    """Choose which margin absorbs spare horizontal width.

    :param project: the project to derive a new one from.
    :param slack_to: ``"gutter"``, ``"outer"`` or ``"split"``. See
        ``LayoutSettings.slack_to`` for what each one keeps constant.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, slack_to=slack_to))


#: Display units for lengths. Values are points-per-unit, so the stored
#: model stays in PDF points and only the UI converts.
LENGTH_UNITS: dict[str, float] = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}


def to_points(value: float, unit: str) -> float:
    """Convert a displayed value in ``unit`` to PDF points.

    :param value: the displayed number.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: the value in points, which is what the model stores.
    :raises KeyError: ``unit`` is not a known unit.
    """
    return value * LENGTH_UNITS[unit]


def from_points(points: float, unit: str) -> float:
    """Convert PDF points to a displayed value in ``unit``.

    :param points: the stored value.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: the number to display.
    :raises KeyError: ``unit`` is not a known unit.
    """
    return points / LENGTH_UNITS[unit]


def imageable_inset_pt(imageable_area_pt: tuple[float, float, float, float]) -> float:
    """The largest edge inset of a printer's imageable area, in points.

    ``imageable_area_pt`` is ``(left, top, right, bottom)`` **margins** from
    the paper edges -- the same convention ``PrinterProfile``,
    ``QtPrintBackend._paint_rendered_page`` and
    ``preview_view.imageable_rect_pt`` all use. It is *not* an
    ``(x0, y0, x1, y1)`` rect; reading it as one yields a ~600pt "inset" and
    a nonsense margin.

    Used by "Use printer margins": a margin at least this large clears the
    non-printable border on every edge, which is the condition that stops
    ``clipped_by_imageable_area`` firing.

    :param imageable_area_pt: ``(left, top, right, bottom)`` margins from
        the paper edges -- **not** an ``(x0, y0, x1, y1)`` rect.
    :returns: the largest of the four insets, in points, floored at ``0``.
    """
    return max(*imageable_area_pt, 0.0)


def set_binding_edge(project: Project, binding_edge: Literal["left", "right"]) -> Project:
    """Set which edge the book is bound on.

    :param project: the project to derive a new one from.
    :param binding_edge: ``"left"`` or ``"right"``. Under
        ``fold_scheme="folio"`` this means reading direction rather than
        which side of a page gets the gutter -- see
        ``deckle.core.layout.SaddleStitchStrategy``.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, binding_edge=binding_edge))


#: Paper sizes offered in the UI, in points, portrait. Landscape is the
#: same tuple swapped -- see :func:`set_paper`. These mirror
#: ``deckle/cli/values.py``'s ``--paper`` presets so both front ends offer
#: the same stock.
PAPER_PRESETS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("Letter", (612.0, 792.0)),
    ("A4", (595.28, 841.89)),
    ("Legal", (612.0, 1008.0)),
    ("A3", (841.89, 1190.55)),
    ("Tabloid", (792.0, 1224.0)),
)

ORIENTATIONS: tuple[str, ...] = ("Portrait", "Landscape")


def paper_is_landscape(paper: tuple[float, float]) -> bool:
    """Whether ``paper`` is wider than it is tall.

    :param paper: ``(width, height)`` in points.
    :returns: ``True`` for landscape. A perfect square counts as portrait,
        arbitrarily but consistently -- it has to go somewhere and nothing
        downstream distinguishes them.
    """
    return paper[0] > paper[1]


def preset_name_for(paper: tuple[float, float]) -> str | None:
    """The preset whose dimensions match ``paper`` in either orientation.

    :param paper: ``(width, height)`` in points.
    :returns: the preset's name, or ``None`` for a custom size -- a project
        imposed from the CLI with ``--paper 500x700pt`` is legitimate and
        must not be silently snapped to the nearest preset.
    """
    upright = tuple(sorted(paper))
    for name, dimensions in PAPER_PRESETS:
        if tuple(sorted(dimensions)) == upright:
            return name
    return None


def set_paper(
    project: Project,
    paper: tuple[float, float],
    *,
    landscape: bool | None = None,
) -> Project:
    """Set the sheet size, optionally forcing an orientation.

    :param project: the project to derive a new one from.
    :param paper: ``(width, height)`` in points, in any orientation.
    :param landscape: force landscape (``True``) or portrait (``False``).
        ``None`` keeps ``paper`` exactly as given.
    :returns: a new project.

    This is the setting folio most needs and the UI longest lacked: two
    portrait pages side by side want a landscape sheet, and without a way
    to ask for one the imposer could only warn and carry on squeezing them
    onto portrait stock.
    """
    if landscape is not None:
        short, long = sorted(paper)
        paper = (long, short) if landscape else (short, long)
    return replace(project, layout=replace(project.layout, paper=paper))


def set_grain(project: Project, grain: str) -> Project:
    """Record which way the paper's fibres run.

    :param project: the project to derive a new one from.
    :param grain: ``"long"``, ``"short"`` or ``"unknown"``.
    :returns: a new project.

    Purely an input to the grain warning -- it changes no geometry. See
    ``LayoutSettings.grain`` for why the rule matters.
    """
    return replace(project, layout=replace(project.layout, grain=grain))


def set_landscape_policy(
    project: Project, landscape_policy: Literal["rotate", "scale"]
) -> Project:
    """Set what happens to a landscape page in a portrait cell.

    :param project: the project to derive a new one from.
    :param landscape_policy: ``"rotate"`` turns the content and warns.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, landscape_policy=landscape_policy))


def set_start_on_recto(project: Project, start_on_recto: bool) -> Project:
    """Set whether the first content page is a right-hand page.

    :param project: the project to derive a new one from.
    :param start_on_recto: the new setting.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, start_on_recto=start_on_recto))


def set_fold_scheme(project: Project, fold_scheme: Literal["none", "folio"]) -> Project:
    """Switch between one-page-per-side and saddle-stitch imposition.

    :param project: the project to derive a new one from.
    :param fold_scheme: ``"none"`` or ``"folio"``. This is what
        :func:`recompute_plan` dispatches on.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, fold_scheme=fold_scheme))


def set_sheets_per_signature(project: Project, sheets_per_signature: int) -> Project:
    """Set how many sheets nest into one gathering.

    :param project: the project to derive a new one from.
    :param sheets_per_signature: the new count. Larger signatures mean more
        fore-edge creep, which the imposer reports as an advisory.
    :returns: a new project.
    """
    return replace(
        project, layout=replace(project.layout, sheets_per_signature=sheets_per_signature)
    )


def set_blank_mode(project: Project, blank_mode: Literal["end", "balanced"]) -> Project:
    """Set where padding blanks land across signatures.

    :param project: the project to derive a new one from.
    :param blank_mode: ``"end"`` puts the whole remainder in the final
        gathering; ``"balanced"`` spreads it so no gathering is more than
        one sheet thinner than its neighbours.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, blank_mode=blank_mode))


def set_sewing_stations(project: Project, sewing_stations: int) -> Project:
    """Set how many sewing-station marks each signature gets.

    :param project: the project to derive a new one from.
    :param sewing_stations: the count. ``0`` disables the marks -- which is
        why there is no separate boolean.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, sewing_stations=sewing_stations))


def set_paper_thickness_pt(project: Project, paper_thickness_pt: float) -> Project:
    """Set the stock thickness used to predict fore-edge creep.

    :param project: the project to derive a new one from.
    :param paper_thickness_pt: thickness per sheet, in points. Used
        **only** for the creep advisory -- no placement the imposer emits
        ever differs because of this value.
    :returns: a new project.
    """
    return replace(
        project, layout=replace(project.layout, paper_thickness_pt=paper_thickness_pt)
    )


CUSTOM_STOCK_LABEL = "Custom (set thickness below)"
"""The stock dropdown's escape hatch.

Anyone whose paper is not on the list, or who owns calipers, sets the
thickness directly and the dropdown says so rather than showing a
preset that is not what they have.
"""


def set_trim(project: Project, trim_pt: float) -> Project:
    """Set how deep the fore-edge, head and tail will be ploughed.

    Two effects, and the second is easy to miss: it draws the cut lines,
    and it is the tolerance the gathering-size suggestion works to,
    because creep is absorbed by trimming.

    :param project: the project to derive a new one from.
    :param trim_pt: depth in points. ``0`` disables the marks, exactly as
        ``sewing_stations = 0`` does, rather than adding a boolean a
        number could already express.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, trim_pt=trim_pt))


def set_crop(
    project: Project, parity: str, insets_pt: tuple[float, float, float, float] | None
) -> Project:
    """Set the crop applied to odd or even source pages.

    Odd and even are separate because a scan's gutter swaps sides every
    leaf, and one rectangle cannot fit both.

    The insets are measured against the page **as displayed** -- a scan a
    viewer has straightened carries a ``/Rotate`` flag, and the exporter
    converts. See ``export._cropped_source_box``.

    :param project: the project to derive a new one from.
    :param parity: ``"odd"`` or ``"even"``.
    :param insets_pt: ``(left, bottom, right, top)``, or ``None`` to
        remove the crop.
    :returns: a new project.
    :raises ValueError: an unknown parity.
    """
    if parity == "odd":
        field = "crop_odd_pt"
    elif parity == "even":
        field = "crop_even_pt"
    else:
        raise ValueError(f"unknown parity {parity!r}: expected odd or even")
    return replace(project, layout=replace(project.layout, **{field: insets_pt}))


def set_signature_lengths(project: Project, text: str) -> Project:
    """State each gathering's sheet count outright, as ``10,10,8``.

    The setting a binder reaches for when a page count divides badly --
    ``7,7,6,6`` beats ``4,4,4,4,4,4,2`` and six blank leaves -- or to land
    a chapter break on a signature boundary.

    Only the *shape* is checked here. Whether the lengths add up to the
    sheet count cannot be known until the document is imposed, and
    ``signatures.split_signatures_at`` already refuses a mismatch with a
    message naming both numbers. Repeating that check here would be a
    second implementation free to disagree with the first.

    :param project: the project to derive a new one from.
    :param text: the field's contents. Empty clears the setting and
        returns to a uniform ``sheets_per_signature``.
    :returns: a new project.
    :raises ValueError: the text is not a comma-separated list of
        positive whole numbers.
    """
    stripped = text.strip()
    if not stripped:
        return replace(project, layout=replace(project.layout, signature_lengths=None))
    lengths = []
    for part in stripped.split(","):
        part = part.strip()
        try:
            value = int(part)
        except ValueError:
            raise ValueError(
                f"{part!r} is not a whole number: give each gathering's "
                "sheet count, such as 10,10,8"
            ) from None
        if value < 1:
            raise ValueError(
                f"every gathering must hold at least one sheet, got {value}"
            )
        lengths.append(value)
    return replace(
        project, layout=replace(project.layout, signature_lengths=tuple(lengths))
    )


def set_sewing_station_positions(project: Project, text: str, unit: str) -> Project:
    """State exactly where the sewing stations go, as ``0.5, 2, 2.25``.

    Values are in ``unit`` -- the panel's display unit, like every other
    length on it -- and stored in points. Sorted and de-duplicated here,
    once, so the imposer never has to decide what two identical stations
    mean.

    Only the *shape* is checked. Whether the positions fit the sheet cannot
    be known until the paper is, and
    :func:`deckle.core.marks.sewing_stations` already refuses one that does
    not with a message naming both numbers -- the same split
    :func:`set_signature_lengths` makes.

    :param project: the project to derive a new one from.
    :param text: the field's contents. Empty clears the setting and returns
        to evenly spaced ``sewing_stations``.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: a new project.
    :raises ValueError: the text is not a comma-separated list of positive
        numbers.
    """
    stripped = text.strip()
    if not stripped:
        return replace(
            project,
            layout=replace(project.layout, sewing_station_positions_pt=None),
        )
    positions = []
    for part in stripped.split(","):
        part = part.strip()
        try:
            value = float(part)
        except ValueError:
            raise ValueError(
                f"{part!r} is not a number: give each station's distance "
                "from the tail, such as 0.5, 2, 2.25"
            ) from None
        if value <= 0:
            raise ValueError(
                f"every station must sit above the tail, got {value:g}"
            )
        positions.append(to_points(value, unit))
    return replace(
        project,
        layout=replace(
            project.layout,
            sewing_station_positions_pt=tuple(sorted(set(positions))),
        ),
    )


def station_positions_text(layout, unit: str) -> str:
    """The Station positions field's contents for ``layout``.

    Shared by the constructor, ``refresh_from_project`` and the unit
    change, so all three cannot disagree about how the same tuple reads.

    :param layout: the ``LayoutSettings`` to render.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: the field text, empty for evenly spaced stations.
    """
    positions = layout.sewing_station_positions_pt
    if not positions:
        return ""
    return ", ".join(f"{from_points(p, unit):g}" for p in positions)


def set_paper_stock(project: Project, stock_name: str) -> Project:
    """Set the stock thickness from a named paper.

    The dropdown's whole purpose: nobody knows their paper's caliper and
    everybody has its weight on the ream wrapper. The caliper is derived
    from the preset's weight and type rather than stored beside it, so
    this cannot drift from what the CLI computes for the same paper.

    :param project: the project to derive a new one from.
    :param stock_name: a name from :data:`deckle.core.paper.PAPER_PRESETS`,
        which this module imports as ``PAPER_STOCKS``.
    :returns: a new project.
    :raises ValueError: no preset by that name, naming the known ones.
    """
    for stock in PAPER_STOCKS:
        if stock.name == stock_name:
            return set_paper_thickness_pt(project, stock.caliper_pt)
    raise ValueError(
        f"unknown paper stock {stock_name!r}: expected one of "
        + ", ".join(stock.name for stock in PAPER_STOCKS)
    )


def set_paper_from_weight(
    project: Project, weight: float, unit: str, paper_type: str,
    grade: str | None = None,
) -> Project:
    """Set the stock thickness from a weight off the ream wrapper.

    The escape hatch behind ``Custom...``, for a paper the preset list
    does not carry.

    :param project: the project to derive a new one from.
    :param weight: the number on the wrapper.
    :param unit: ``"gsm"`` or ``"lb"``.
    :param paper_type: which bulk to apply.
    :param grade: the basis size, required for ``"lb"`` -- 20lb is 75gsm
        as bond and 54gsm as cover, so guessing would be wrong by half.
    :returns: a new project.
    :raises ValueError: an unknown unit, type or grade, or pounds without
        a grade.
    """
    if unit == "lb":
        if grade is None:
            raise ValueError(
                "a weight in pounds also needs a paper grade: 20lb is "
                "75gsm as bond but 54gsm as cover"
            )
        weight = gsm_from_pounds(weight, grade)
    elif unit != "gsm":
        raise ValueError(f"unknown weight unit {unit!r}: expected gsm or lb")
    return set_paper_thickness_pt(project, caliper_pt_from_gsm(weight, paper_type))


def signature_suggestion(layout: LayoutSettings):
    """The gathering size this paper suggests, or ``None``.

    A thin wrapper that decides *what to ask*: the planned trim is part of
    the answer, because creep is absorbed by trimming, and the panel holds
    both numbers while :mod:`deckle.core.paper` holds neither.

    :param layout: the current settings.
    :returns: a :class:`~deckle.core.paper.SignatureSuggestion`, or
        ``None`` when no thickness is set.
    """
    return suggest_sheets_per_signature(
        layout.paper_thickness_pt, trim_pt=layout.trim_pt
    )


def signature_suggestion_text(layout: LayoutSettings) -> str | None:
    """The sentence shown under *Sheets per signature*, or ``None``.

    Names **which constraint bound the answer**, because the remedy
    differs and a number the binder cannot act on is not advice: creep
    means plan a trim, fold means this paper is thick. Says nothing at all
    when the suggestion matches what is already set -- advice that repeats
    the current state back is noise.

    :param layout: the current settings.
    :returns: the sentence, or ``None`` to show nothing.
    """
    suggestion = signature_suggestion(layout)
    if suggestion is None or suggestion.sheets == layout.sheets_per_signature:
        return None
    if suggestion.limited_by == "creep":
        because = (
            f"beyond that the fore-edge creeps more than the "
            f"{layout.trim_pt:g}pt trim will remove"
            if layout.trim_pt > 0
            else "beyond that the fore-edge creep starts to show; a trim "
                 "would allow more"
        )
    else:
        because = "beyond that the gathering is too thick to fold cleanly"
    return (
        f"{suggestion.sheets} sheets ({suggestion.pages} pages) suits this "
        f"paper -- {because}."
    )


def apply_suggested_sheets(project: Project) -> Project:
    """Take the suggested gathering size.

    :param project: the project to derive a new one from.
    :returns: a new project, or the same one when there is nothing to
        suggest -- the button is only offered alongside a suggestion, and
        a no-op is safer than a guess if that ever stops being true.
    """
    suggestion = signature_suggestion(project.layout)
    if suggestion is None:
        return project
    return set_sheets_per_signature(project, suggestion.sheets)


def recompute_plan(project: Project) -> SheetPlan:
    """Re-run the imposer over every (non-skipped) page. Arithmetic only.

    Dispatches on ``fold_scheme``: ``"folio"`` groups sheets into saddle-
    stitched signatures via ``SaddleStitchStrategy``; ``"none"`` (the MVP
    default) stays on ``GutterShiftStrategy``, one source page per side.

    :param project: the project to impose.
    :returns: the fresh whole-document plan. Cheap enough to run on every
        control change because it rasterizes nothing.
    """
    if project.layout.fold_scheme == "folio":
        return SaddleStitchStrategy().impose(project.pages, project.layout)
    return GutterShiftStrategy().impose(project.pages, project.layout)


def binding_readout_str(plan: SheetPlan) -> str:
    """Render the binding readout, e.g. ``"17 signatures · 67 sheets · 2 blanks"``.

    Pure text over the recomputed ``SheetPlan`` -- the signature count,
    sheet count and total filler/blank count are the arithmetic a binder
    actually decides on, so this reads straight off ``plan`` rather than
    re-deriving anything from ``LayoutSettings``.

    :param plan: the recomputed plan.
    :returns: the readout text. Under ``fold_scheme="none"`` the signature
        and blank counts are legitimately zero.
    """
    blank_count = sum(sig.blank_count for sig in plan.signatures)
    return (
        f"{len(plan.signatures)} signatures · {len(plan.sheets)} sheets · "
        f"{blank_count} blanks"
    )


def apply_layout_change(state: AppState, mutator) -> SheetPlan:
    """Apply ``mutator`` (one of the ``set_*`` functions above, partially
    applied to a new value) through ``AppState.mutate`` and immediately
    recompute the whole-document ``SheetPlan``.

    Returns the fresh plan so a caller (the Qt panel below, or a test) can
    hand it to a preview without any rendering happening here.

    :param state: the app state to mutate.
    :param mutator: one of the ``set_*`` functions above, already bound to
        its new value.
    :returns: the freshly recomputed whole-document plan.
    :raises Exception: whatever ``mutator`` raises, unchanged.
    """
    state.mutate(mutator)
    return recompute_plan(state.project)


# -- the ink composite ---------------------------------------------------

COMPOSITE_PREVIEW_DPI = 36
"""Resolution for the in-panel ink composite.

Half ``composite_pages``'s own default. That default is 72 because
``crop-preview`` writes a PNG someone opens and zooms; this one is shown in
a settings column a few hundred pixels wide, where 72 dpi doubles the
rasterisation cost of every page in the document for detail the column
cannot display.
"""

COMPOSITE_DEBOUNCE_MS = 300
"""How long to wait after the last crop edit before redrawing.

Long enough that holding a spinbox's arrow key does not queue a
whole-document rasterisation per step, short enough that it still feels
like a response to what you typed. The same cancel-and-reschedule shape
``AppState`` uses for autosave, for the same reason.
"""

COMPOSITE_RECTANGLE_SENTENCE = (
    "Anything outside the red rectangle is what the crop would remove."
)
"""What the red rectangle means, in the CLI's own words.

Lifted verbatim from ``deckle.cli._cmd_crop_preview`` so both front ends
say the same thing about the same picture; ``tests/test_output_command_parity``
fails if they drift apart.
"""

COMPOSITE_MIXED_MESSAGE = (
    "Odd and even pages are cropped differently, so no single rectangle "
    "describes this picture. Pick a parity above."
)
"""Why "All pages" sometimes draws no rectangle.

Saying nothing would read as "this crop removes nothing", which is the
one answer that is certainly wrong when two different crops are set.
"""

COMPOSITE_PARITIES: tuple[tuple[str, str], ...] = (
    ("all", "All pages"),
    ("odd", "Odd pages"),
    ("even", "Even pages"),
)
"""The composite's parity choices, as ``(key, label)``."""


def composite_crop_for(layout, parity: str):
    """The rectangle to draw over a composite of ``parity``, or ``None``.

    Drawing the odd crop over a picture of every page is a confidently
    wrong answer: it shows the even pages' ink beside a rectangle never
    measured against it, so a crop that clips them looks safe. The CLI
    records the same reasoning at ``deckle.cli._cmd_crop_preview``.

    So: odd shows ``crop_odd_pt``; even shows ``crop_even_pt``, falling
    back to ``crop_odd_pt`` because that is what the imposer applies when
    only one is set; and "all pages" shows a rectangle only when there IS
    one rectangle -- when ``crop_even_pt`` is unset and ``crop_odd_pt``
    therefore applies to the whole document.

    :param layout: the current ``LayoutSettings``.
    :param parity: ``"all"``, ``"odd"`` or ``"even"``.
    :returns: the insets, or ``None`` to draw nothing.
    """
    if parity == "odd":
        return layout.crop_odd_pt
    if parity == "even":
        return layout.crop_even_pt or layout.crop_odd_pt
    return None if layout.crop_even_pt else layout.crop_odd_pt


def composite_caption(worker, no_rectangle_reason: str = "") -> str:
    """What to say under the picture, given a finished worker.

    Pure, and separate from the widget, because the precedence is a
    judgement rather than a layout detail: a message about *why there is no
    picture* outranks anything said about a picture that is not there.

    :param worker: a finished :class:`CompositeWorker`.
    :param no_rectangle_reason: why no rectangle was drawn, or ``""``.
    :returns: the caption text.
    """
    if worker.message:
        return worker.message
    if worker.failed:
        return "Could not draw the composite."
    if no_rectangle_reason:
        return no_rectangle_reason
    return f"{worker.page_count} page(s) superimposed. {COMPOSITE_RECTANGLE_SENTENCE}"


class CompositeWorker:
    """Runs ``composite_pages`` on a background ``QThread``.

    Plain class, not a ``QObject`` -- the same shape as ``ThumbnailWorker``
    in ``arrange_view.py`` and ``PreviewWorker`` in ``preview_view.py``,
    with the Qt wiring left to the panel.

    :param pages: the document's pages.
    :param parity: ``"odd"``, ``"even"`` or ``None`` for all.
    :param crop_pt: the rectangle to draw, or ``None`` for none.
    :param dpi: rasterisation resolution.
    :ivar rendered: the composite, or ``None``.
    :ivar failed: set when the composite raised. Distinct from a ``None``
        :attr:`rendered`, which is also what a cancelled run leaves.
    :ivar message: why there is no picture, or ``""``. The documented
        "you filtered everything out" case is a state a user can reach
        with Skip, not a fault, so it is reported rather than logged.
    :ivar page_count: how many pages went into the picture.
    :ivar cancel: set when a newer edit supersedes this run.
    """

    def __init__(self, pages, *, parity=None, crop_pt=None, dpi=COMPOSITE_PREVIEW_DPI):
        self.pages = list(pages)
        self.parity = parity
        self.crop_pt = crop_pt
        self.dpi = dpi
        self.rendered = None
        self.failed = False
        self.message = ""
        self.page_count = 0
        self.cancel = threading.Event()

    def run(self) -> None:
        """Composite the pages, unless already superseded.

        :returns: nothing -- the result lands on :attr:`rendered`. A
            cancelled run leaves it ``None``, so a superseded picture never
            reaches the label.
        """
        if self.cancel.is_set():
            return
        from deckle.core.render import composite_pages

        try:
            rendered = composite_pages(
                self.pages,
                dpi=self.dpi,
                parity=self.parity,
                crop_pt=self.crop_pt,
                cancel=self.cancel,
            )
        except ValueError as exc:
            # Documented and reachable: every page skipped, blank, or
            # filtered out by parity. A sentence, not a logged fault.
            self.message = str(exc)
            return
        except Exception as exc:  # noqa: BLE001 -- a thread, not the UI
            # A QThread has nowhere to deliver an exception: Qt prints a
            # traceback the user cannot act on and the panel silently
            # stays blank, which looks identical to a slow render.
            log_exception("crop_composite_failed", exc)
            self.failed = True
            return
        if self.cancel.is_set():
            return
        self.rendered = rendered
        self.page_count = _composited_page_count(self.pages, self.parity)


def _composited_page_count(pages, parity) -> int:
    """How many pages :func:`composite_pages` would include.

    The same filter, stated once more here rather than returned from the
    renderer, so the caption can name a number without ``composite_pages``
    growing a second return value for one caller's benefit.
    """
    from deckle.core.models import is_blank_page

    count = 0
    for page in pages:
        if page.skipped or is_blank_page(page):
            continue
        if parity is not None:
            is_odd = (page.ref.page_index + 1) % 2 == 1
            if (parity == "odd") != is_odd:
                continue
        count += 1
    return count


# -- the control table ----------------------------------------------------
#
# The panel used to maintain the same controls by hand in four places: the
# constructor, `refresh_from_project`, `_on_unit_changed`, and twenty-odd
# near-identical `_on_*_changed` handlers. Three shipped bugs came from a
# list that was extended in one place and not the others -- B11 (the unit
# change skipped trim and the eight crop boxes), B12 (the refresh blocked
# signals on a list that omitted trim, crop and paper stock, so opening a
# project filled the undo stack), and B29 (thickness declared four decimals
# at construction and zero-in-`pt` on every unit change afterwards).
#
# So there is one table. It says, per control, what the widget is, which
# field it shows, whether it is a length, its cap, its precision, and what
# happens when it changes. The constructor builds from it, the refresh
# reads from it, the unit change is driven by the widget types it produces,
# and the handlers are generated from it. Adding a control means adding a
# row -- there is nowhere else to forget.


@dataclass(frozen=True)
class Control:
    """One control on the layout panel.

    :param name: the attribute the panel files the widget under.
    :param kind: what to build. ``"length"`` is a
        :func:`length_spin_box_class` box over a value the model keeps in
        points; ``"count"`` a ``QSpinBox``; ``"flag"`` a ``QCheckBox``;
        ``"choice"`` a ``QComboBox``; ``"text"`` a ``QLineEdit`` committed
        on ``editingFinished``; ``"unit_text"`` the same but holding
        lengths, so a unit change re-renders it; ``"action"`` a
        ``QPushButton``; ``"label"`` a read-only ``QLabel``.
    :param tab: which form it goes on -- ``"paper"``, ``"crop"``,
        ``"margins"``, ``"single"``, ``"signature"``, or ``"composite"``
        for the two controls inside the ink-composite box.
    :param label: the form label to its left. Empty spans the row, which
        is what a checkbox, a button and a hint paragraph all want.
    :param tooltip: the control's tooltip.
    :param field: the ``LayoutSettings`` field it displays, when reading
        one attribute is enough.
    :param read: displays something more than one field -- a crop inset,
        say. Takes the layout, returns the value. Wins over ``field``.
    :param apply: ``(project, value) -> project``, the mutator a change
        routes through. ``None`` means the control is handled elsewhere
        (``handler``) or shows something that is not a project field.
    :param needs_unit: ``apply`` also takes the display unit.
    :param handler: a panel method that owns this control instead of the
        generic path, for the half-dozen that genuinely differ -- the
        paper pair, the linked margins, the crop parities.
    :param refresh: a panel method that re-displays this control, for the
        same few.
    :param after: what to redo once the change lands, in order:
        ``"readout"``, ``"suggestion"``, ``"composite"``, ``"stock_custom"``.
    :param error_prefix: how a refused value is announced. A refusal is a
        number the user can correct, not a traceback.
    :param group: ``(dict_attr, key)`` to also file the widget in a dict,
        for the margin and crop families.
    :param cap_pt: ``"length"`` only -- the largest value, in points.
    :param decimals: ``"length"`` only -- decimals shown, in every unit.
    :param step_pt: ``"length"`` only -- one nudge, in points.
    :param minimum: ``"count"`` only.
    :param maximum: ``"count"`` only.
    :param choices: ``"choice"`` only -- ``(key, label)`` pairs, or a
        callable returning them for a list built at runtime.
    :param by_key: ``"choice"`` only -- the combo carries labels and the
        model carries keys, so it is read by index rather than by text.
    :param placeholder: ``"text"`` only.
    :param text: ``"label"`` only -- its fixed text.
    :param word_wrap: ``"label"`` only.
    :param default: what to show when the control has no ``field`` and no
        ``read`` -- the display unit, whether the composite is on. A
        refresh leaves these alone, because the project does not hold them.
    """

    name: str
    kind: str
    tab: str
    label: str = ""
    tooltip: str = ""
    field: str | None = None
    read: object = None
    apply: object = None
    needs_unit: bool = False
    handler: str | None = None
    refresh: str | None = None
    after: tuple[str, ...] = ()
    error_prefix: str = ""
    group: tuple[str, object] | None = None
    cap_pt: float = 0.0
    decimals: int = 3
    step_pt: float = 9.0
    minimum: int = 0
    maximum: int = 0
    choices: object = ()
    by_key: bool = False
    placeholder: str = ""
    text: str = ""
    word_wrap: bool = False
    default: object = None

    @property
    def is_value(self) -> bool:
        """Whether this row is a control the user sets, not a caption.

        The guard test in ``tests/test_layout_panel_table.py`` compares
        exactly these against the widget tree.
        """
        return self.kind not in ("action", "label")

    def displayed(self, layout):
        """What this control should show for ``layout``.

        :param layout: the current ``LayoutSettings``.
        :returns: the value, or ``None`` when the control shows nothing
            the project holds -- the display unit, the ink composite.
        """
        if self.read is not None:
            return self.read(layout)
        if self.field is not None:
            return getattr(layout, self.field)
        return None


def _crop_control(parity: str, edge: str, index: int) -> Control:
    """One of the eight crop boxes.

    Eight rather than four because a scan's gutter swaps sides every leaf,
    so one rectangle cannot fit both parities. They differ only by which
    inset they read and which parity they write, which is why they are
    generated rather than written out.
    """
    field = f"crop_{parity}_pt"
    return Control(
        name=f"crop_{parity}_{edge}",
        kind="length",
        tab="crop",
        label=f"Crop {parity} {edge}:",
        group=("crop_spinboxes", (parity, edge)),
        read=lambda layout, f=field, i=index: (
            getattr(layout, f)[i] if getattr(layout, f) else 0.0
        ),
        cap_pt=720.0,
        decimals=3,
        handler="_on_crop_changed",
        error_prefix="Crop",
    )


#: The three editable margins, with the label and tooltip each carries.
#: The fourth page edge is the spine, whose margin is the gutter.
_MARGIN_TOOLTIPS: tuple[tuple[str, str, str], ...] = (
    (
        "margin_top_pt",
        "Head (top):",
        "The blank strip along the top edge, called the head. Most "
        "printers cannot print to the very edge of the paper, so a "
        "head margin of zero usually means clipped content -- see "
        "'Use printer margins'.",
    ),
    (
        "margin_bottom_pt",
        "Tail (bottom):",
        "The blank strip along the bottom edge, called the tail. "
        "Traditionally set larger than the head: it looks balanced "
        "to the eye, and it is where a thumb rests when the book is "
        "held open.",
    ),
    (
        "margin_outer_pt",
        "Fore-edge:",
        "The blank strip on the edge opposite the spine -- the edge "
        "you see when the book is closed and the one you turn pages "
        "by.\n\nThis is the margin most worth keeping consistent, "
        "which is why 'Spare width to' sends leftover space to the "
        "gutter by default.",
    ),
)


CONTROLS: tuple[Control, ...] = (
    # -- Page setup > Paper -----------------------------------------------
    Control(
        name="unit_combo",
        kind="choice",
        tab="paper",
        label="Units:",
        # No `field`: the display unit is how the panel reads, not
        # something the project holds, so a refresh must leave it alone.
        choices=tuple((u, u) for u in ("pt", "in", "cm", "mm")),
        default="in",
        handler="_on_unit_changed",
        tooltip=(
            "The unit every length on this tab is typed in. Values are "
            "stored in points regardless, so switching units re-displays "
            "the same measurement -- it never changes your layout."
        ),
    ),
    Control(
        name="paper_combo",
        kind="choice",
        tab="paper",
        label="Paper:",
        choices=lambda: tuple((n, n) for n, _ in PAPER_PRESETS),
        handler="_on_paper_changed",
        refresh="_refresh_paper_combo",
        tooltip=(
            "The size of the paper you are printing on -- not the size of a "
            "page in the book.\n\n"
            "Under Signatures a sheet is folded in half, so each book page "
            "ends up half the sheet."
        ),
    ),
    Control(
        name="orientation_combo",
        kind="choice",
        tab="paper",
        label="Orientation:",
        choices=tuple((o, o) for o in ORIENTATIONS),
        handler="_on_orientation_changed",
        refresh="_refresh_orientation_combo",
        tooltip=(
            "Which way round the sheet goes through the printer.\n\n"
            "Signatures want LANDSCAPE: two portrait book pages sit side by "
            "side on one sheet, and the fold runs down the middle. On "
            "portrait stock they get squeezed, and Deckle warns rather than "
            "silently rotating your paper for you."
        ),
    ),
    Control(
        name="grain_combo",
        kind="choice",
        tab="paper",
        label="Paper grain:",
        field="grain",
        choices=GRAINS,
        by_key=True,
        apply=lambda project, key: set_grain(project, key),
        tooltip=(
            "Which way the paper's fibres run. Paper creases cleanly ALONG "
            "the grain and cracks ACROSS it, so the grain should run "
            "parallel to the spine.\n\n"
            "Ordinary office letter and A4 are LONG grain -- fibres along "
            "the longer edge. Turn a letter sheet landscape to fold a "
            "booklet and the fold now runs across the grain, which is why "
            "binders buy short-grain stock.\n\n"
            "Leave Unknown and Deckle stays quiet. Set it and you get a "
            "warning when a fold is going to fight the paper."
        ),
    ),
    Control(
        name="paper_stock_combo",
        kind="choice",
        tab="paper",
        label="Paper stock:",
        choices=lambda: ((CUSTOM_STOCK_LABEL, CUSTOM_STOCK_LABEL),) + tuple(
            (stock.name, f"{stock.name}  ({stock.caliper_pt / PT_PER_MM:.3f} mm)")
            for stock in PAPER_STOCKS
        ),
        by_key=True,
        handler="_on_paper_stock_changed",
        refresh="_refresh_stock_combo",
        tooltip=(
            "Pick the paper off the ream wrapper and Deckle works out the "
            "caliper. The thickness below is an estimate -- bulk varies "
            "about 10% between manufacturers -- and is used only to predict "
            "fore-edge creep and spine width, never to place a page."
        ),
    ),
    # -- the weight off the ream wrapper --------------------------------
    #
    # `set_paper_from_weight` was written for this, documented as "the
    # escape hatch behind `Custom...`", and had no caller: the CLI could
    # turn 80gsm into a caliper and the app could not. Four rows rather
    # than one because a weight on its own does not determine a caliper
    # -- the bulk does the other half, and a pound weight means nothing
    # without the basis size it is quoted against.
    #
    # None of the four is stored in the project. Only the caliper they
    # derive is, exactly as the CLI stores only the caliper: keeping the
    # weight as well would be two descriptions of one sheet with no rule
    # for which wins, which is the error `_caliper_pt` already refuses.
    Control(
        name="paper_weight_spinbox",
        kind="count",
        tab="paper",
        label="Paper weight:",
        minimum=0,
        maximum=1000,
        handler="_on_paper_weight_changed",
        tooltip=(
            "The number on the ream wrapper -- 80 for 80gsm, 24 for 24lb "
            "-- for a paper the stock list above does not carry.\n\n"
            "Deckle derives the caliper from this and the two boxes "
            "beside it, and writes it into Paper thickness below. Bulk "
            "varies about 10% between manufacturers, so it is an "
            "estimate, used only to predict fore-edge creep and spine "
            "width, never to place a page.\n\n"
            "Leave at 0 if you would rather type the thickness itself."
        ),
    ),
    Control(
        name="paper_weight_unit_combo",
        kind="choice",
        tab="paper",
        label="Weight unit:",
        choices=(("gsm", "gsm (grams per m2)"), ("lb", "lb (US basis weight)")),
        by_key=True,
        default="gsm",
        handler="_on_paper_weight_changed",
        tooltip=(
            "Which number is on the wrapper. Metric reams say gsm; US "
            "reams say a basis weight in pounds, which also needs the "
            "grade beside it."
        ),
    ),
    Control(
        name="paper_grade_combo",
        kind="choice",
        tab="paper",
        label="Weight grade:",
        choices=tuple((name, name) for name in sorted(GRADE_BASIS_SIZES_IN)),
        by_key=True,
        default="bond",
        handler="_on_paper_weight_changed",
        tooltip=(
            "Which basis size a POUND weight is quoted against. Ignored "
            "for gsm.\n\n"
            "This is not a detail: 20lb is 75gsm as bond and 54gsm as "
            "cover, so guessing would be wrong by half."
        ),
    ),
    Control(
        name="paper_type_combo",
        kind="choice",
        tab="paper",
        label="Paper bulk:",
        choices=tuple((name, name) for name in sorted(PAPER_BULK)),
        by_key=True,
        default="offset",
        handler="_on_paper_weight_changed",
        tooltip=(
            "How bulky the stock is, which is what separates two papers "
            "of the same weight -- a coated sheet is thinner than an "
            "offset sheet of identical grammage, and a bulky one is "
            "thicker by a third again."
        ),
    ),
    Control(
        name="paper_thickness_spinbox",
        kind="length",
        tab="paper",
        label="Paper thickness:",
        field="paper_thickness_pt",
        apply=lambda project, points: set_paper_thickness_pt(project, points),
        # Four decimals in EVERY unit, `pt` included. A caliper is
        # 0.2-0.5pt, and the blanket zero-decimals-in-`pt` rule this
        # replaces showed that as `0` and wrote `0` back -- B29.
        cap_pt=10.0,
        decimals=4,
        step_pt=0.072,  # a thousandth of an inch
        # The other two halves of B29: a typed thickness changes what
        # gathering size fits the trim, and it no longer describes the
        # named stock it was picked from.
        after=("suggestion", "readout", "stock_custom"),
        tooltip=(
            "The caliper of a single sheet. Ordinary 20lb office paper is "
            "about 0.004in; card is several times that.\n\n"
            "Deckle uses it for two things on the binding schedule: how far "
            "the innermost leaf of a signature protrudes at the fore-edge, "
            "and how thick the sewn block will be at the spine -- which is "
            "the number you cut boards against.\n\n"
            "Leave at 0 and neither is estimated."
        ),
    ),
    # -- Page setup > Crop && trim ----------------------------------------
    Control(
        name="trim_spinbox",
        kind="length",
        tab="crop",
        label="Trim depth:",
        field="trim_pt",
        apply=lambda project, points: set_trim(project, points),
        cap_pt=144.0,
        decimals=3,
        # The trim is the tolerance the suggestion works to, so changing
        # it can change the advice without the paper changing at all.
        after=("suggestion", "readout"),
        tooltip=(
            "How deep the fore-edge, head and tail will be ploughed after "
            "sewing. Draws the cut lines, and gives the gathering-size "
            "suggestion room to work with -- creep is absorbed by trimming. "
            "0 draws none."
        ),
    ),
    *(
        _crop_control(parity, edge, index)
        for parity in ("odd", "even")
        for index, edge in enumerate(("left", "bottom", "right", "top"))
    ),
    Control(
        name="auto_crop_button",
        kind="action",
        tab="crop",
        text="Measure crop from the ink",
        handler="_on_auto_crop",
        tooltip=(
            "Rasterise every page, find where the ink actually is, and fill "
            "the boxes above. Measures odd and even separately, which is "
            "what a scan whose gutter alternates needs. Check the result "
            "before printing -- a marginal note on one page in two hundred "
            "is what a number cannot show you."
        ),
    ),
    Control(
        name="composite_check",
        kind="flag",
        tab="composite",
        text="Show the ink composite",
        default=True,
        handler="_on_composite_toggled",
        tooltip=(
            "Superimpose every page's ink in one picture and draw the crop "
            "on it. " + COMPOSITE_RECTANGLE_SENTENCE + "\n\n"
            "This is the question a number cannot answer: not \"what does "
            "page 1 look like\" but \"does this rectangle clip anything, on "
            "any page\". A marginal note on one page in two hundred is "
            "exactly what it catches.\n\n"
            "Untick it on a very large scan -- it rasterises every page."
        ),
    ),
    Control(
        name="composite_parity_combo",
        kind="choice",
        tab="composite",
        label="Composite:",
        choices=COMPOSITE_PARITIES,
        by_key=True,
        handler="_on_composite_parity_changed",
        tooltip=(
            "A scan's margins alternate leaf by leaf, so odd and even pages "
            "are different pictures and one rectangle rarely fits both. "
            "Composite them separately to see it."
        ),
    ),
    # -- Page setup > Margins ---------------------------------------------
    Control(
        name="gutter_spinbox",
        kind="length",
        tab="margins",
        label="Gutter:",
        field="gutter_pt",
        apply=lambda project, points: set_gutter_pt(project, points),
        cap_pt=288.0,
        decimals=3,
        tooltip=(
            "The margin on the spine edge -- the strip swallowed by the "
            "binding. It alternates side by side so it always falls on "
            "the bound edge. This is a MINIMUM: if a page is narrower "
            "than the others, the spare width lands here by default, so "
            "the gutter you get can exceed the gutter you asked for. "
            "Change that with 'Spare width to'."
        ),
    ),
    Control(
        name="slack_combo",
        kind="choice",
        tab="margins",
        label="Spare width to:",
        field="slack_to",
        choices=SLACK_TARGETS,
        by_key=True,
        apply=lambda project, key: set_slack_to(project, key),
        tooltip=(
            "When source pages differ in width, the spare space has to go "
            "somewhere. This chooses which margin absorbs it -- and therefore "
            "which one stays identical on every page.\n\n"
            "Gutter: the fore-edge is exact on every page, the gutter varies. "
            "The default, because the fore-edge is the edge you see when the "
            "book is closed.\n"
            "Fore-edge: the gutter is exact on every page, the fore-edge "
            "varies. Prefer this for a fixed punch or sewing template.\n"
            "Split evenly: both vary by half, so the content sits centred "
            "between them."
        ),
    ),
    Control(
        name="link_margins_check",
        kind="flag",
        tab="margins",
        text="Link all three",
        field="margins_linked",
        handler="_on_link_margins_toggled",
        tooltip=(
            "Edit head, tail and fore-edge as a single value. Untick to "
            "set them independently -- useful when the fore-edge needs "
            "room for a thumb but the head does not."
        ),
    ),
    *(
        Control(
            name=f"{field}_spinbox",
            kind="length",
            tab="margins",
            label=label,
            tooltip=tooltip,
            field=field,
            group=("margin_spinboxes", field),
            cap_pt=216.0,
            decimals=3,
            handler="_on_margin_changed",
        )
        for field, label, tooltip in _MARGIN_TOOLTIPS
    ),
    Control(
        name="use_printer_margins_button",
        kind="action",
        tab="margins",
        text="Use printer margins",
        handler="_on_use_printer_margins",
        tooltip=(
            "Set the margin to the printer's non-printable inset, so content "
            "clears the dead border on every edge."
        ),
    ),
    Control(
        name="binding_edge_combo",
        kind="choice",
        tab="margins",
        label="Binding edge:",
        field="binding_edge",
        choices=tuple((e, e) for e in BINDING_EDGES),
        apply=lambda project, value: set_binding_edge(project, value),
        tooltip=(
            "Which edge the book is bound on. Left is conventional for "
            "left-to-right languages; right suits Arabic, Hebrew, or "
            "Japanese tate-gaki. This mirrors which side the gutter "
            "falls on for every page."
        ),
    ),
    Control(
        name="start_on_recto_check",
        kind="flag",
        tab="margins",
        text="Start on a right-hand page",
        field="start_on_recto",
        apply=lambda project, checked: set_start_on_recto(project, checked),
        tooltip=(
            "Where page 1 lands once the book is bound. Ticked, it falls on "
            "a recto -- the right-hand page, which is where a title page "
            "belongs.\n\n"
            "Untick when the first page should face left, as it does when "
            "your document already begins with its own title leaf. Deckle "
            "adds one blank in front to shift everything over."
        ),
    ),
    Control(
        name="landscape_policy_combo",
        kind="choice",
        tab="margins",
        label="Landscape policy:",
        field="landscape_policy",
        choices=tuple((p, p) for p in LANDSCAPE_POLICIES),
        apply=lambda project, value: set_landscape_policy(project, value),
        tooltip=(
            "What to do with a landscape page in a portrait book.\n\n"
            "rotate: turn it 90 degrees so it fills the page (the default "
            "-- the reader turns the book).\n"
            "scale: leave it upright, filling the width, with bands above "
            "and below."
        ),
    ),
    # -- How it folds > Flat sheets ---------------------------------------
    Control(
        name="single_hint_label",
        kind="label",
        tab="single",
        word_wrap=True,
        text=(
            "Sheets are printed but never folded. Each one carries two pages, "
            "one per side, and the gutter alternates so it always falls on "
            "the bound edge.\n\n"
            "Print all fronts, reload the stack, print all backs, then bind "
            "the stack however you like -- glued, punched, or side-sewn.\n\n"
            "This is the proven path, and it needs no settings beyond Page "
            "setup above."
        ),
    ),
    # -- How it folds > Signatures ----------------------------------------
    Control(
        name="signature_hint_label",
        kind="label",
        tab="signature",
        word_wrap=True,
        text=(
            "Sheets are imposed two-up, folded in half, and nested inside "
            "one another to make gatherings that get sewn through the "
            "fold.\n\n"
            "Page setup above still applies -- a folded signature has a "
            "gutter and margins exactly as a flat sheet does. The settings "
            "here control only how the sheets are grouped and folded.\n\n"
            "Experimental: the page ordering is hand-written arithmetic. "
            "Save the schedule, print onto scrap, fold it, and check it "
            "reads correctly before committing a real book."
        ),
    ),
    Control(
        name="sheets_per_signature_spinbox",
        kind="count",
        tab="signature",
        label="Sheets per signature:",
        field="sheets_per_signature",
        minimum=1,
        maximum=100,
        apply=lambda project, value: set_sheets_per_signature(project, value),
        after=("readout",),
        tooltip=(
            "How many sheets are nested inside one another to make a single "
            "folded gathering.\n\n"
            "Each sheet becomes 4 pages once folded, so 4 sheets is a "
            "16-page signature -- a common choice. More sheets means fewer "
            "gatherings to sew, but a thicker fold that bulges at the "
            "fore-edge and needs trimming."
        ),
    ),
    # The suggestion sits directly under the control it is about, and says
    # nothing at all when the setting already matches -- advice that
    # repeats the current state back is what teaches people to stop
    # reading advisories.
    Control(name="suggestion_label", kind="label", tab="signature", word_wrap=True),
    Control(
        name="suggestion_button",
        kind="action",
        tab="signature",
        text="Use it",
        handler="_on_apply_suggestion",
    ),
    Control(
        name="signature_lengths_edit",
        kind="text",
        tab="signature",
        label="Gatherings:",
        placeholder="e.g. 10,10,8",
        read=lambda layout: (
            ",".join(str(n) for n in layout.signature_lengths)
            if layout.signature_lengths else ""
        ),
        apply=lambda project, text: set_signature_lengths(project, text),
        after=("readout",),
        error_prefix="Gatherings",
        tooltip=(
            "State each gathering's sheet count outright, instead of one "
            "uniform size. For a page count that divides badly -- 7,7,6,6 "
            "beats 4,4,4,4,4,4,2 and six blank leaves -- or to land a "
            "chapter break on a signature boundary. Leave empty to use "
            "Sheets per signature."
        ),
    ),
    Control(
        name="blank_mode_combo",
        kind="choice",
        tab="signature",
        label="Blank mode:",
        field="blank_mode",
        choices=tuple((m, m) for m in BLANK_MODES),
        apply=lambda project, value: set_blank_mode(project, value),
        after=("readout",),
        tooltip=(
            "A folded book needs a page count that is a multiple of 4, so "
            "blanks get added. This chooses where.\n\n"
            "end: all blanks at the back of the book.\n"
            "balanced: spread across signatures, so no single gathering is "
            "noticeably emptier than its neighbours."
        ),
    ),
    Control(
        name="sewing_stations_spinbox",
        kind="count",
        tab="signature",
        label="Sewing stations:",
        field="sewing_stations",
        minimum=0,
        maximum=20,
        apply=lambda project, value: set_sewing_stations(project, value),
        after=("readout",),
        tooltip=(
            "Marks printed on the fold line showing where to pierce for "
            "sewing. Three is the traditional pamphlet stitch; five suits a "
            "taller book.\n\nSet to 0 to print no sewing marks -- useful if "
            "you are stapling rather than sewing."
        ),
    ),
    Control(
        name="station_positions_edit",
        kind="unit_text",
        tab="signature",
        label="Station positions:",
        placeholder="evenly spaced",
        apply=lambda project, text, unit: set_sewing_station_positions(
            project, text, unit
        ),
        needs_unit=True,
        after=("readout",),
        error_prefix="Station positions",
        tooltip=(
            "Where the holes actually go, measured up from the TAIL, in "
            "the unit above -- for example 0.5, 2, 2.25, 9.5.\n\n"
            "Leave empty and Deckle spaces 'Sewing stations' evenly, which "
            "is a pamphlet stitch. Fill it in when even spacing will not "
            "do: sewing on tapes needs a pair either side of each tape, and "
            "kettle stitches sit at a fixed inset from head and tail.\n\n"
            "This wins over the count above."
        ),
    ),
    # Live readout -- "17 signatures - 67 sheets - 2 blanks" -- derived
    # from the recomputed SheetPlan, since that arithmetic is the thing a
    # binder actually decides on.
    Control(
        name="binding_readout_label",
        kind="label",
        tab="signature",
        label="Binding:",
        tooltip=(
            "What your current settings actually produce, recomputed live. "
            "This is the arithmetic a binder decides on -- how many "
            "gatherings to sew, how much paper to cut, and how many blank "
            "pages the fold count forced."
        ),
    ),
)


#: Every control, by name. Built once, so a lookup is not a scan.
CONTROLS_BY_NAME: dict[str, Control] = {spec.name: spec for spec in CONTROLS}


# -- Qt wiring -----------------------------------------------------------
# Imported lazily so this module -- and every pure function above -- stays
# importable without PySide6/a display, matching arrange_view.py/import_view.py.


def _qt_fields_at_size_hint():
    """``QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint``.

    :returns: the enum member, imported lazily like every other Qt name.
    """
    from PySide6.QtWidgets import QFormLayout

    return QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint


def _qt_core():
    from PySide6.QtCore import QObject, QThread, Signal

    return QObject, QThread, Signal


def _qt_align_center():
    """``Qt.AlignmentFlag.AlignCenter``, imported lazily like the rest."""
    from PySide6.QtCore import Qt

    return Qt.AlignmentFlag.AlignCenter


#: Built on first use and cached, so this module stays importable without
#: PySide6 -- the same reason every other Qt name here is fetched lazily.
_LENGTH_SPIN_BOX = None
_UNIT_LINE_EDIT = None


def length_spin_box_class():
    """The ``QDoubleSpinBox`` subclass every length control on this panel uses.

    :returns: the class. Built on first call, cached thereafter.

    A length control is one whose value the model stores in points and the
    panel displays in the user's chosen unit. Three numbers describe it --
    the range cap, how many decimals it shows, and how far one nudge moves
    it -- and before this class they were written once in the constructor
    and again in ``_on_unit_changed``, from a hand-maintained list of
    boxes. The two copies disagreed: paper thickness opened with four
    decimals and a 0.001 step, and after any unit change had three (zero,
    in ``pt``) and a step of one whole unit. That is B29.

    Here the three numbers are declared once, at the box, and the panel
    finds its length boxes **by type** rather than from a list -- so a
    control added tomorrow is reconverted on a unit change because it is a
    length, not because somebody remembered to write it down a second
    time.

    :attr:`points` is the value the *model* holds, not the rounded number
    on screen. Converting a display value would compound its rounding at
    every unit change; a 17.77pt margin shown as ``0.247in`` would come
    back as 17.784pt.
    """
    global _LENGTH_SPIN_BOX
    if _LENGTH_SPIN_BOX is not None:
        return _LENGTH_SPIN_BOX
    from PySide6.QtWidgets import QDoubleSpinBox

    class LengthSpinBox(QDoubleSpinBox):
        """A spinbox over a length the model keeps in points.

        :param parent: the parent widget.
        :param cap_pt: the largest value it will accept, in points.
        :param decimals: how many decimals it shows, in **every** unit.
            One number rather than one per unit: the old blanket rule
            ("zero in ``pt``, three otherwise") is what displayed a
            0.3pt caliper as ``0`` and wrote ``0`` back on the next nudge.
        :param step_pt: how far one nudge moves it, in points, converted
            into whatever unit is on screen.
        :param unit: the unit it opens in.
        """

        def __init__(self, parent, *, cap_pt, decimals=3, step_pt=9.0, unit="in"):
            super().__init__(parent)
            self.cap_pt = cap_pt
            self.step_pt = step_pt
            self._unit = unit
            self._points = 0.0
            self.setDecimals(decimals)
            self._apply_unit(unit)
            self.valueChanged.connect(self._note_typed_value)

        def _apply_unit(self, unit: str) -> None:
            self.setRange(0.0, from_points(self.cap_pt, unit))
            self.setSingleStep(from_points(self.step_pt, unit))

        def _note_typed_value(self, value: float) -> None:
            # What the user typed IS the exact value, rounding included.
            self._points = to_points(value, self._unit)

        @property
        def unit(self) -> str:
            """The unit currently on screen."""
            return self._unit

        def points(self) -> float:
            """The length in points -- the model's value, not the display's."""
            return self._points

        def set_points(self, points: float) -> None:
            """Display ``points``, remembering the exact value behind it."""
            self.setValue(from_points(points, self._unit))
            self._points = points

        def set_display_unit(self, unit: str) -> None:
            """Re-display the same physical length in ``unit``.

            Signals are blocked: the spinbox would otherwise emit its
            pre-conversion number as though the user had typed it, which
            is how a 0.25in trim became a 0.25mm one.
            """
            points = self._points
            self._unit = unit
            blocked = self.blockSignals(True)
            try:
                self._apply_unit(unit)
                self.setValue(from_points(points, unit))
            finally:
                self.blockSignals(blocked)
            self._points = points

    _LENGTH_SPIN_BOX = LengthSpinBox
    return _LENGTH_SPIN_BOX


def unit_line_edit_class():
    """A ``QLineEdit`` holding lengths, which a unit change must re-render.

    :returns: the class. Built on first call, cached thereafter.

    Station positions are lengths typed as text -- ``0.5, 2, 9.5`` -- so
    they are exactly as wrong under a stale unit as a spinbox would be,
    and exactly as easy to leave off a list. Giving them the same
    ``set_display_unit`` method the length boxes have means the panel's
    unit change asks the widget tree a question instead of consulting a
    list of names.
    """
    global _UNIT_LINE_EDIT
    if _UNIT_LINE_EDIT is not None:
        return _UNIT_LINE_EDIT
    from PySide6.QtWidgets import QLineEdit

    class UnitLineEdit(QLineEdit):
        """A line edit whose text is re-rendered when the unit changes.

        :param parent: the parent widget.
        :param text_for_unit: called with the new unit; returns the text
            to show. It reads the *model*, so the displayed text never
            drifts from what the project holds.
        """

        def __init__(self, parent, *, text_for_unit):
            super().__init__(parent)
            self._text_for_unit = text_for_unit

        def set_display_unit(self, unit: str) -> None:
            blocked = self.blockSignals(True)
            try:
                self.setText(self._text_for_unit(unit))
            finally:
                self.blockSignals(blocked)

    _UNIT_LINE_EDIT = UnitLineEdit
    return _UNIT_LINE_EDIT


def _qt_scaled_pixmap(rendered, width: int):
    """A ``QPixmap`` of ``rendered``, scaled to ``width``.

    The ``.copy()`` is not optional. ``QImage`` does not copy the buffer it
    is handed, and ``rendered.rgba`` is bytes owned by a worker thread's
    result -- painting from it after the worker is collected shows as
    intermittent garbage rather than a clean crash. ``arrange_view``'s
    ``_icon_from_rendered`` records the same thing.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap

    image = QImage(
        rendered.rgba,
        rendered.width,
        rendered.height,
        rendered.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    pixmap = QPixmap.fromImage(image)
    if width > 0 and pixmap.width() > width:
        pixmap = pixmap.scaledToWidth(
            width, Qt.TransformationMode.SmoothTransformation
        )
    return pixmap


def _qt_widgets():
    from PySide6.QtWidgets import (
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QRadioButton,
        QSpinBox,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    return (
        QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
        QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox,
        QTabWidget, QVBoxLayout, QWidget,
    )


class LayoutPanel:
    """Form of layout controls, wired straight to ``AppState``.

    Every control change routes through ``apply_layout_change`` above and
    emits ``layout_changed`` with the freshly recomputed ``SheetPlan`` --
    it never rasterizes anything itself.

    :param state: the app state whose layout the controls edit.
    :param parent: the parent ``QWidget``, or ``None``.
    :param profile: the printer profile supplying the imageable inset for
        "Use printer margins". Optional, so the panel stays constructible
        without a printer.
    :ivar layout_changed: ``Signal(object)`` carrying the recomputed
        ``SheetPlan``.
    :ivar widget: the ``QWidget`` to place in a layout.
    """

    def __init__(self, state: AppState, parent=None, profile=None) -> None:
        # `profile` supplies the printer's imageable inset for the
        # "Use printer margins" button. Optional so the panel stays
        # constructible without a printer.
        self.profile = profile
        QObject, QThread, Signal = _qt_core()
        (
            QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
            QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox,
            QTabWidget, QVBoxLayout, QWidget,
        ) = _qt_widgets()

        class _Signals(QObject):
            layout_changed = Signal(object)  # SheetPlan
            schedule_saved = Signal(str)  # a user-facing outcome message

        self._signals = _Signals()
        self.layout_changed = self._signals.layout_changed
        self.schedule_saved = self._signals.schedule_saved

        self.state = state
        self.widget = QWidget(parent)
        outer = QVBoxLayout(self.widget)

        # Page setup sits ABOVE the tabs because BOTH ways of making a book
        # need it: a folded signature has a gutter and margins exactly as a
        # single page does. Only what differs between the two goes in a tab.
        # Page setup is itself tabbed, because it had grown to twenty-six
        # rows -- eight of them crop boxes -- and a settings panel you have
        # to scroll is one where the control you want is never on screen.
        #
        # A SEPARATE tab widget from the one below, deliberately. These
        # tabs group settings; those tabs ARE the mode, and selecting one
        # sets `fold_scheme`. Putting Crop beside Signatures would mean
        # clicking it changed how the book folds, which is exactly the
        # confusion the mode tabs were built to remove. The label above
        # each strip is what keeps the two readable as different things.
        setup_label = QLabel("Page setup", self.widget)
        outer.addWidget(setup_label)

        self.setup_tabs = QTabWidget(self.widget)
        outer.addWidget(self.setup_tabs)

        def _sub_tab(title: str) -> QFormLayout:
            page = QWidget(self.widget)
            layout = QFormLayout(page)
            # Numeric fields hold values like "0.750". Letting them stretch
            # to the pane width pushes the form wider than the column and
            # grows a horizontal scrollbar across the whole settings panel
            # -- the one kind of scrolling a settings form should never
            # need.
            layout.setFieldGrowthPolicy(_qt_fields_at_size_hint())
            self.setup_tabs.addTab(page, title)
            return layout

        # Ordered by how often they are touched: the paper is chosen once
        # per job, the margins are adjusted while looking at the preview,
        # and the crop is set once from a scan and then left alone.
        paper_form = _sub_tab("Paper")
        margins_form = _sub_tab("Margins")
        crop_form = _sub_tab("Crop && trim")

        # The tabs ARE the mode. Selecting one sets ``fold_scheme``, so the
        # two ways of making a book are mutually exclusive by construction:
        # there is no state in which you are imposing single pages while a
        # signature control is reachable, and no separate dropdown that can
        # disagree with the tab you are looking at.
        #
        # The earlier design had a fold-scheme dropdown AND tabs, with the
        # unusable tab disabled. That was worse twice over: a disabled tab
        # silently swallows the click, and having two controls for one
        # decision meant the tab could look active while the dropdown said
        # otherwise.
        outer.addWidget(QLabel("How it folds", self.widget))
        self.tabs = QTabWidget(self.widget)
        outer.addWidget(self.tabs)

        single_tab = QWidget(self.widget)
        single_form = QFormLayout(single_tab)
        single_form.setFieldGrowthPolicy(_qt_fields_at_size_hint())
        # "Flat sheets", not "Single pages": each sheet still carries two
        # pages, one per side. What this mode lacks is the FOLD, and naming
        # it for a page count that is wrong invites exactly the confusion
        # the tabs exist to remove.
        self.tabs.addTab(single_tab, "Flat sheets")
        self._single_tab_index = self.tabs.indexOf(single_tab)

        signature_tab = QWidget(self.widget)
        signature_form = QFormLayout(signature_tab)
        signature_form.setFieldGrowthPolicy(_qt_fields_at_size_hint())
        self.tabs.addTab(signature_tab, "Signatures")
        self._signature_tab_index = self.tabs.indexOf(signature_tab)

        # The ink composite's own box. It is filled by the two `composite`
        # rows of the table and added to the Crop tab after them, so the
        # picture lands under the boxes it is a picture of. One spanning
        # row, not four labelled ones: the tab is already at the dozen-row
        # ceiling `test_no_settings_tab_is_taller_than_a_dozen_rows`
        # enforces, and a picture wants the full width anyway.
        composite_box = QWidget(self.widget)
        composite_layout = QVBoxLayout(composite_box)
        composite_layout.setContentsMargins(0, 0, 0, 0)

        def _add_to_composite(*row) -> None:
            if len(row) == 1:
                composite_layout.addWidget(row[0])
                return
            label, widget = row
            parity_row = QHBoxLayout()
            parity_row.addWidget(QLabel(label, composite_box))
            parity_row.addWidget(widget)
            parity_row.addStretch(1)
            composite_layout.addLayout(parity_row)

        # -- every control, built from the one table ------------------------
        #
        # The constructor does not know what controls exist. It knows how to
        # build each KIND, and reads the rest -- which form, which field,
        # the cap, the precision, the handler, what to redo afterwards --
        # off the row. That is the whole point of the table: this loop, the
        # refresh, the unit change and the handlers are four readers of one
        # declaration rather than four copies of one list.
        self._unit = "in"
        self.controls: dict[str, object] = {}
        self._choice_keys: dict[str, list] = {}
        self.margin_spinboxes: dict[str, object] = {}
        self.crop_spinboxes: dict[tuple[str, str], object] = {}
        forms = {
            "paper": paper_form.addRow,
            "margins": margins_form.addRow,
            "crop": crop_form.addRow,
            "single": single_form.addRow,
            "signature": signature_form.addRow,
            "composite": _add_to_composite,
        }
        for spec in CONTROLS:
            self._build_control(spec, forms[spec.tab])

        self.composite_label = QLabel("", self.widget)
        self.composite_label.setAlignment(_qt_align_center())
        self.composite_label.setMinimumHeight(160)
        self.composite_caption = QLabel("", self.widget)
        self.composite_caption.setWordWrap(True)
        composite_layout.addWidget(self.composite_label)
        composite_layout.addWidget(self.composite_caption)
        crop_form.addRow(composite_box)

        # Names a few callers reach for by hand. Derived from the table's
        # choices rather than written out beside them, so a reordered or
        # extended list cannot leave an index lookup pointing at the wrong
        # entry.
        self._paper_names = self._choice_keys["paper_combo"]
        self._grain_keys = self._choice_keys["grain_combo"]
        self._slack_keys = self._choice_keys["slack_combo"]
        self._composite_parity_keys = self._choice_keys["composite_parity_combo"]
        self._custom_paper_label = None

        # Below the mode tabs, not inside one. A schedule is not a
        # signature artefact: the flat-sheet schedule carries how the stack
        # collates, the block thickness a perfect binder cuts boards
        # against, and the whole AT THE PRINTER block -- actual size and
        # which edge to flip about -- which ruins a job either way when it
        # is got wrong. On the Signatures tab it was not merely disabled
        # under flat sheets, it was on a tab that is not on screen:
        # selecting that tab IS selecting folio.
        self.save_schedule_button = QPushButton("Save schedule...", self.widget)
        self.save_schedule_button.setToolTip(SCHEDULE_TOOLTIP)
        outer.addWidget(self.save_schedule_button)
        self.save_schedule_button.clicked.connect(self._on_save_schedule_clicked)

        # Also mode-independent: a default is about how this person makes
        # books, not about which fold scheme is selected right now.
        defaults_row = QHBoxLayout()
        self.save_defaults_button = QPushButton("Save as my defaults", self.widget)
        self.save_defaults_button.setToolTip(SAVE_DEFAULTS_TOOLTIP)
        # "Forget", not "Reset": the second is ambiguous between forgetting
        # the saved file and resetting THIS project's settings, and only
        # one of those is non-destructive. The destructive reading is what
        # undo is for, so it is not built and the button is named for the
        # one it does.
        self.forget_defaults_button = QPushButton("Forget my defaults", self.widget)
        self.forget_defaults_button.setToolTip(FORGET_DEFAULTS_TOOLTIP)
        defaults_row.addWidget(self.save_defaults_button)
        defaults_row.addWidget(self.forget_defaults_button)
        outer.addLayout(defaults_row)
        self.save_defaults_button.clicked.connect(self._on_save_defaults_clicked)
        self.forget_defaults_button.clicked.connect(self._on_forget_defaults_clicked)

        # Every control now shows what the project holds. Done before a
        # single signal is connected, so nothing written here can be read
        # back as an edit the user made -- the same guarantee
        # `refresh_from_project` gets from blocking the widget tree.
        self._display_controls()
        self._sync_margin_enabled()
        self.tabs.setCurrentIndex(
            self._signature_tab_index
            if state.project.layout.fold_scheme == "folio"
            else self._single_tab_index
        )
        self._sync_signature_tab()
        self.binding_readout_label.setText(
            binding_readout_str(recompute_plan(state.project))
        )

        for spec in CONTROLS:
            self._connect_control(spec)
        self.tabs.currentChanged.connect(self._on_mode_tab_changed)

        # Cancel-and-reschedule, so holding a spinbox's arrow key produces
        # one rasterisation of the document rather than one per step.
        from PySide6.QtCore import QTimer

        self._QThread = QThread
        self._composite_timer = QTimer(self.widget)
        self._composite_timer.setSingleShot(True)
        self._composite_timer.setInterval(COMPOSITE_DEBOUNCE_MS)
        # Late-bound on purpose: connecting the bound method would freeze
        # today's implementation into the signal, which is exactly what a
        # test that replaces `_start_composite` needs not to happen.
        self._composite_timer.timeout.connect(lambda: self._start_composite())
        self._composite_thread = None
        self._composite_worker: CompositeWorker | None = None
        self._composite_reason = ""
        self._schedule_composite()

    # -- the table, read four ways --------------------------------------
    #
    # `_build_control` builds it, `_display_controls` shows the project in
    # it, `_on_unit_changed` reconverts it (by asking the widget tree, not
    # by name), and `_on_control_changed` applies an edit from it. Adding a
    # control means adding a row to :data:`CONTROLS`; there is no second
    # place that has to be remembered.

    def _build_control(self, spec: Control, add) -> None:
        """Build one control and put it on its form.

        :param spec: the row describing it.
        :param add: the form's ``addRow`` -- called with ``(label, widget)``
            when the row has a label and ``(widget,)`` when it spans.
        :returns: nothing. The widget lands on ``self`` under ``spec.name``,
            in ``self.controls``, and in ``spec.group``'s dict if it has one.
        """
        from PySide6.QtWidgets import (
            QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton, QSpinBox,
        )

        if spec.kind == "length":
            widget = length_spin_box_class()(
                self.widget,
                cap_pt=spec.cap_pt,
                decimals=spec.decimals,
                step_pt=spec.step_pt,
                unit=self._unit,
            )
        elif spec.kind == "count":
            widget = QSpinBox(self.widget)
            widget.setRange(spec.minimum, spec.maximum)
        elif spec.kind == "flag":
            widget = QCheckBox(spec.text, self.widget)
            if spec.default is not None:
                widget.setChecked(bool(spec.default))
        elif spec.kind == "choice":
            widget = QComboBox(self.widget)
            choices = spec.choices() if callable(spec.choices) else spec.choices
            keys = []
            for key, label in choices:
                widget.addItem(label)
                keys.append(key)
            self._choice_keys[spec.name] = keys
            if spec.default is not None and spec.default in keys:
                widget.setCurrentIndex(keys.index(spec.default))
        elif spec.kind == "unit_text":
            widget = unit_line_edit_class()(
                self.widget,
                text_for_unit=lambda unit: station_positions_text(
                    self.state.project.layout, unit
                ),
            )
            widget.setPlaceholderText(spec.placeholder)
        elif spec.kind == "text":
            widget = QLineEdit(self.widget)
            widget.setPlaceholderText(spec.placeholder)
        elif spec.kind == "action":
            widget = QPushButton(spec.text, self.widget)
        elif spec.kind == "label":
            widget = QLabel(spec.text, self.widget)
            widget.setWordWrap(spec.word_wrap)
        else:  # pragma: no cover -- a typo in the table, not a user path
            raise ValueError(f"unknown control kind {spec.kind!r}")

        if spec.tooltip:
            widget.setToolTip(spec.tooltip)
        setattr(self, spec.name, widget)
        self.controls[spec.name] = widget
        if spec.group is not None:
            getattr(self, spec.group[0])[spec.group[1]] = widget
        if spec.label:
            add(spec.label, widget)
        else:
            add(widget)

    def _connect_control(self, spec: Control) -> None:
        """Wire one control's signal to whatever owns it.

        :param spec: the row describing it.
        :returns: nothing.

        A row with no ``handler`` goes through :meth:`_on_control_changed`,
        which is every regular control: twenty near-identical
        ``_on_*_changed`` methods collapsed into one that reads ``apply``
        and ``after`` off the row. A row that names a handler gets it,
        called with the control's current value -- and with its group key
        first, for the margin and crop families, since those are one
        handler over several boxes.
        """
        widget = self.controls[spec.name]
        if spec.kind == "label":
            return
        if spec.handler is not None:
            method = getattr(self, spec.handler)
            if spec.kind == "action":
                widget.clicked.connect(method)
                return
            if spec.group is not None:
                def slot(*_args, s=spec, m=method):
                    m(s.group[1], self._control_value(s))
            else:
                def slot(*_args, s=spec, m=method):
                    m(self._control_value(s))
        elif spec.kind == "action":  # pragma: no cover -- table typo
            return
        else:
            def slot(*_args, s=spec):
                self._on_control_changed(s)

        if spec.kind in ("length", "count"):
            widget.valueChanged.connect(slot)
        elif spec.kind == "flag":
            widget.toggled.connect(slot)
        elif spec.kind == "choice":
            # By index when the combo shows labels and the model stores
            # keys; by text when the two are the same string.
            if spec.by_key:
                widget.currentIndexChanged.connect(slot)
            else:
                widget.currentTextChanged.connect(slot)
        else:  # text, unit_text
            widget.editingFinished.connect(slot)

    def _control_value(self, spec: Control):
        """What ``spec``'s widget currently holds, in the model's terms.

        :param spec: the row describing it.
        :returns: the value to hand ``apply`` -- points for a length, the
            key for a keyed combo -- or ``None`` when a keyed combo has no
            valid selection, which is a combo mid-rebuild rather than an
            edit.
        """
        widget = self.controls[spec.name]
        if spec.kind == "length":
            return widget.points()
        if spec.kind == "count":
            return widget.value()
        if spec.kind == "flag":
            return widget.isChecked()
        if spec.kind == "choice":
            if not spec.by_key:
                return widget.currentText()
            keys = self._choice_keys[spec.name]
            index = widget.currentIndex()
            return keys[index] if 0 <= index < len(keys) else None
        return widget.text()

    def _show_control_value(self, spec: Control, value) -> None:
        """Display ``value`` in ``spec``'s widget.

        :param spec: the row describing it.
        :param value: the model's value.
        :returns: nothing. Signals are the caller's problem -- the
            constructor has not connected any yet, and
            :meth:`refresh_from_project` has blocked them all.
        """
        widget = self.controls[spec.name]
        if spec.kind == "length":
            widget.set_points(value)
        elif spec.kind == "count":
            widget.setValue(value)
        elif spec.kind == "flag":
            widget.setChecked(bool(value))
        elif spec.kind == "choice":
            if spec.by_key:
                keys = self._choice_keys[spec.name]
                if value in keys:
                    widget.setCurrentIndex(keys.index(value))
            else:
                widget.setCurrentText(value)
        elif spec.kind == "text":
            widget.setText(value)

    def _display_controls(self) -> None:
        """Show the current project in every control on the table.

        :returns: nothing.

        The list of what to set used to be written out here by hand, and
        it had drifted from the one in the constructor and the one in the
        unit change. It is now the table, so it cannot.
        """
        layout = self.state.project.layout
        for spec in CONTROLS:
            if spec.refresh is not None:
                # The handful that need more than a field read: the paper
                # combo has to be able to NAME a size the presets do not
                # cover, and the orientation is derived from the paper.
                getattr(self, spec.refresh)()
                continue
            if spec.kind == "unit_text":
                # It renders itself from the model, in the current unit.
                self.controls[spec.name].set_display_unit(self._unit)
                continue
            value = spec.displayed(layout)
            if value is None:
                continue
            self._show_control_value(spec, value)

    def _after_change(self, spec: Control, plan: SheetPlan) -> None:
        """Redo whatever ``spec`` says a change to it invalidates.

        :param spec: the row describing the control.
        :param plan: the freshly recomputed plan.
        :returns: nothing.
        """
        for effect in spec.after:
            if effect == "readout":
                self._refresh_binding_readout(plan)
            elif effect == "suggestion":
                self._refresh_suggestion()
            elif effect == "composite":
                self._schedule_composite()
            elif effect == "stock_custom":
                self._set_stock_combo_custom()

    def _report_refusal(self, spec: Control, exc: Exception) -> None:
        """Say why a value was refused, where the user is looking.

        :param spec: the row describing the control.
        :param exc: what the mutator or the imposer raised.
        :returns: nothing. A mistyped gathering list is a typo, not a bug
            report, and a crop that consumes the page is a number the user
            can correct in the box they are already looking at.
        """
        if spec.kind in ("text", "unit_text"):
            self.controls[spec.name].setToolTip(str(exc))
        self.schedule_saved.emit(f"{spec.error_prefix}: {exc}")

    def _on_control_changed(self, spec: Control) -> None:
        """Apply an edit to a control the table describes completely.

        :param spec: the row describing it.
        :returns: nothing.

        This is the twenty near-identical ``_on_*_changed`` handlers,
        written once. What differed between them -- the mutator, whether
        the binding readout or the gathering advice has to be recomputed,
        how a refusal is announced -- is data on the row.
        """
        value = self._control_value(spec)
        if value is None:
            return
        if spec.needs_unit:
            def mutate(project, s=spec, v=value):
                return s.apply(project, v, self._unit)
        else:
            def mutate(project, s=spec, v=value):
                return s.apply(project, v)
        try:
            plan = apply_layout_change(self.state, mutate)
        except ValueError as exc:
            self._report_refusal(spec, exc)
            return
        self._after_change(spec, plan)
        self.layout_changed.emit(plan)

    # -- the ink composite ----------------------------------------------

    @property
    def _thread(self):
        """The current composite thread, for ``main._live_threads``.

        Named to match ``ArrangeView``/``PreviewView`` so shutdown can treat
        all three the same. A render still inside pdfium when the
        interpreter finalises takes the process down with it, which is what
        ``stop_background_work`` exists to prevent -- and this panel now
        starts renders too.
        """
        return self._composite_thread

    @property
    def _worker(self):
        """The current composite worker, for ``stop_background_work``."""
        return self._composite_worker

    def _schedule_composite(self) -> None:
        """Redraw the composite shortly, cancelling any pending redraw.

        :returns: nothing. The redraw happens :data:`COMPOSITE_DEBOUNCE_MS`
            after the last edit, on a background thread.
        """
        if not self.composite_check.isChecked():
            self._clear_composite()
            return
        self._composite_timer.start()

    def _on_composite_toggled(self, checked: bool) -> None:
        if not checked and self._composite_worker is not None:
            # Untick means "stop rasterising", not "stop when you finish".
            self._composite_worker.cancel.set()
        self._schedule_composite()

    def _start_composite(self) -> None:
        """Kick off a background composite of the current crop.

        Supersedes any run still in flight, exactly as
        ``ArrangeView.request_visible_thumbnails`` does: without it, nudging
        a spinbox eight times queues eight whole-document rasterisations,
        each parented to the widget and so never freed, with the slowest
        painting last.

        :returns: nothing. The picture arrives via
            :meth:`_on_composite_ready`.
        """
        if self._composite_worker is not None:
            self._composite_worker.cancel.set()
            self._composite_worker = None
        pages = list(self.state.project.pages)
        if not pages:
            self._clear_composite()
            return
        parity = self._composite_parity_keys[
            self.composite_parity_combo.currentIndex()
        ]
        layout = self.state.project.layout
        self._composite_reason = (
            COMPOSITE_MIXED_MESSAGE
            if parity == "all" and layout.crop_even_pt
            else ""
        )
        worker = CompositeWorker(
            pages,
            parity=None if parity == "all" else parity,
            crop_pt=composite_crop_for(layout, parity),
            dpi=COMPOSITE_PREVIEW_DPI,
        )
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_composite_ready(worker))
        # Parented to the widget, so without this every superseded run
        # leaks a thread for the life of the window.
        thread.finished.connect(thread.deleteLater)
        self._composite_thread = thread
        self._composite_worker = worker
        self.composite_caption.setText("Compositing...")
        thread.start()

    def _on_composite_ready(self, worker) -> None:
        """Paint a finished composite, unless it has been superseded.

        :param worker: the worker whose thread just finished.
        :returns: nothing.
        """
        if worker is not self._composite_worker or worker.cancel.is_set():
            return
        if worker.rendered is None or not worker.rendered.rgba:
            self.composite_label.clear()
        else:
            self.composite_label.setPixmap(
                _qt_scaled_pixmap(worker.rendered, self.composite_label.width())
            )
        self.composite_caption.setText(
            composite_caption(worker, self._composite_reason)
        )

    def _clear_composite(self) -> None:
        """Take the picture down and say nothing."""
        self.composite_label.clear()
        self.composite_caption.setText("")

    def refresh_from_project(self) -> None:
        """Re-read every control from the current project.

        :returns: nothing.

        Needed when the project is REPLACED rather than edited -- opening a
        saved one. Without it the controls keep showing the previous job's
        settings while the document underneath is a different book, which
        is worse than showing nothing: the panel would be confidently wrong.

        Signals are blocked throughout. Setting a widget's value fires its
        handler, and those handlers write back to the project -- so an
        unguarded refresh would overwrite the freshly loaded layout with
        whatever the widgets happened to hold, one control at a time.

        Every child widget is blocked rather than a list of them named
        here. That list existed and had drifted: trim, the eight crop
        boxes and the paper-stock combo were all set below while none of
        them were blocked. The drift is silent, which is what makes the
        hand-maintained version the wrong shape -- an omitted widget's
        handler writes back the very value the refresh was about to set,
        so the document still looks correct, while the undo stack fills
        with up to nine entries the user never made and each one clears
        the redo stack out from under them (B12). A list that has to be
        extended every time a control is added will be forgotten again;
        asking the widget tree cannot be.

        What to *set* was a second such list, and it is now
        :meth:`_display_controls` walking :data:`CONTROLS`.
        """
        from PySide6.QtWidgets import QWidget

        widgets = self.widget.findChildren(QWidget)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._display_controls()
            self.tabs.setCurrentIndex(
                self._signature_tab_index
                if self.state.project.layout.fold_scheme == "folio"
                else self._single_tab_index
            )
        finally:
            for widget in widgets:
                widget.blockSignals(False)

        self._sync_margin_enabled()
        self._sync_signature_tab()
        self._refresh_suggestion()
        self._refresh_binding_readout(recompute_plan(self.state.project))
        # The document underneath is a different book, so the picture of
        # the old one is as stale as the numbers were.
        self._schedule_composite()

    def set_document_loaded(self, loaded: bool) -> None:
        """Enable the document-dependent actions on this panel.

        :param loaded: whether a document is open.
        :returns: nothing.

        Called by the window rather than watched from here, so there is one
        place that decides what "a document exists" enables.
        """
        self._document_loaded = loaded
        self._sync_signature_tab()
        self._schedule_composite()

    def _on_save_defaults_clicked(self) -> None:
        """Remember the current settings for new projects.

        :returns: nothing. A config directory that cannot be written is
            reported in the wording :mod:`deckle.core.outputs` owns, so it
            reads the same as any other failed write -- and it IS reported,
            because ``save_defaults`` raises rather than swallowing, which
            is the whole difference between an explicit action and a
            preference read at startup.
        """
        try:
            save_defaults(self.state.project.layout)
        except OSError as exc:
            log_exception("defaults_write_failed", exc, path=str(defaults_path()))
            self.schedule_saved.emit(describe_write_failure(str(defaults_path()), exc))
            return
        log_event("defaults_saved", path=str(defaults_path()))
        self.schedule_saved.emit(
            "Saved these settings as your defaults for new projects."
        )

    def _on_forget_defaults_clicked(self) -> None:
        """Delete the saved defaults.

        :returns: nothing. Not confirmed: it discards a preference, not
            work, and saving them again is one click.
        """
        if forget_defaults():
            log_event("defaults_forgotten", path=str(defaults_path()))
            self.schedule_saved.emit(
                "Forgot your defaults. New projects start from Deckle's own."
            )
        else:
            self.schedule_saved.emit("You have no saved defaults.")

    def _on_save_schedule_clicked(self) -> None:
        """Write the binding schedule beside wherever the source came from.

        :returns: nothing. Failures are reported through the same wording
            the CLI uses -- see :mod:`deckle.core.outputs`.
        """
        from PySide6.QtWidgets import QFileDialog

        pages = self.state.project.pages
        if not pages:
            return

        source = pages[0].ref.path
        suggested = os.path.join(
            os.path.dirname(source) or os.getcwd(),
            os.path.splitext(os.path.basename(source))[0] + "-schedule.txt",
        )
        path, _ = QFileDialog.getSaveFileName(
            self.widget, "Save binding schedule", suggested, "Text files (*.txt)"
        )
        if not path:
            return

        problem = output_path_problem(path, source)
        if problem is not None:
            log_event("schedule_path_rejected", path=path, detail=problem)
            self.schedule_saved.emit(problem)
            return

        settings = self.state.project.layout
        text = format_schedule_text(
            build_schedule(recompute_plan(self.state.project), settings),
            os.path.basename(source),
        )
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            log_exception("schedule_write_failed", exc, path=path)
            self.schedule_saved.emit(describe_write_failure(path, exc))
            return
        self.schedule_saved.emit(f"Saved binding schedule to {path}")

    def _sync_signature_tab(self) -> None:
        """Keep the Signatures tab consistent with the current mode.

        The tab IS the mode now, so there is nothing to grey out: a control
        on the Signatures tab is only reachable when signatures are what you
        are making. All this does is keep the selected tab honest if the
        scheme changed from elsewhere (reopening a project, undo), and gate
        the schedule button on having a document to schedule.
        """
        folio = self.state.project.layout.fold_scheme == "folio"
        loaded = getattr(self, "_document_loaded", bool(self.state.project.pages))

        wanted = self._signature_tab_index if folio else self._single_tab_index
        if self.tabs.currentIndex() != wanted:
            # Guarded: setCurrentIndex fires currentChanged, which would
            # write the scheme straight back and push a redundant undo entry.
            self._syncing_mode = True
            try:
                self.tabs.setCurrentIndex(wanted)
            finally:
                self._syncing_mode = False

        # A schedule needs a document; it does not need a fold scheme.
        # Under flat sheets it says how to collate the stack, how thick the
        # block will be, and what to set at the printer -- none of which is
        # signature-specific, and all of which ruins a job when got wrong.
        self.save_schedule_button.setEnabled(loaded)
        self.save_schedule_button.setToolTip(
            SCHEDULE_TOOLTIP if loaded else "Import a document to build a schedule."
        )

    def _sync_margin_enabled(self) -> None:
        """When linked, only the head box is editable -- the other two mirror
        it. Disabling rather than hiding keeps the values visible, so you can
        see what linking did before unlinking again."""
        linked = self.link_margins_check.isChecked()
        for field, box in self.margin_spinboxes.items():
            box.setEnabled(not linked or field == "margin_top_pt")

    def _on_link_margins_toggled(self, linked: bool) -> None:
        self._sync_margin_enabled()
        plan = apply_layout_change(
            self.state, lambda project: set_margins_linked(project, linked)
        )
        if linked:
            # Adopt the head margin for all three, so linking is a visible,
            # predictable action rather than a silent mode change.
            head = self.margin_spinboxes["margin_top_pt"].points()
            self._on_margin_changed("margin_top_pt", head)
            return
        self.layout_changed.emit(plan)

    def _on_margin_changed(self, field: str, points: float) -> None:
        """Apply one margin, or all three when they are linked.

        :param field: which margin, the control's group key.
        :param points: the new value, in points.
        :returns: nothing.
        """
        linked = self.link_margins_check.isChecked()
        plan = apply_layout_change(
            self.state, lambda project: set_margin(project, field, points, linked=linked)
        )
        if linked:
            self._refresh_margin_boxes()
        self.layout_changed.emit(plan)

    def _refresh_margin_boxes(self) -> None:
        """Re-display all three margins from the model without re-emitting."""
        for field, box in self.margin_spinboxes.items():
            box.blockSignals(True)
            box.set_points(getattr(self.state.project.layout, field))
            box.blockSignals(False)

    def _on_unit_changed(self, unit: str) -> None:
        """Re-display the same physical lengths in a new unit.

        The stored model is always points, so switching units must not
        change the layout -- only how it reads. Each widget blocks its own
        signals while its number is rewritten, otherwise it would emit and
        re-apply the pre-conversion value as if the user had typed it.

        **This asks the widget tree, it does not consult a list.** The list
        was the bug: it carried the gutter, the margins and the thickness
        but not the trim or the eight crop boxes, so switching in->mm left
        a 0.25in trim reading ``0.25`` and the next nudge wrote it back as
        0.25mm (B11). Every widget that holds a length declares itself by
        having ``set_display_unit`` -- see :func:`length_spin_box_class`
        and :func:`unit_line_edit_class` -- so a length control added
        tomorrow is converted because of what it *is*, and cannot be
        forgotten.
        """
        from PySide6.QtWidgets import QWidget

        self._unit = unit
        for widget in self.widget.findChildren(QWidget):
            convert = getattr(widget, "set_display_unit", None)
            if convert is not None:
                convert(unit)

    def _on_use_printer_margins(self) -> None:
        """Set the margin to the active printer's non-printable inset."""
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        # Set every margin, regardless of link state -- the printer's dead
        # border applies to all four edges, so a partial application would
        # leave some edge still unprintable.
        self.margin_spinboxes["margin_top_pt"].set_points(inset)
        if not self.link_margins_check.isChecked():
            for field in ("margin_bottom_pt", "margin_outer_pt"):
                self.margin_spinboxes[field].set_points(inset)

    def _refresh_paper_combo(self) -> None:
        """Select the entry that names the project's paper.

        :returns: nothing. Worked out through :meth:`_sync_paper_choices`,
            which adds an entry for a size the presets do not cover --
            doing that only in the constructor is what once left the combo
            saying "A4" over a 500x700 sheet.
        """
        self.paper_combo.setCurrentIndex(
            self._paper_names.index(
                self._sync_paper_choices(self.state.project.layout.paper)
            )
        )

    def _refresh_orientation_combo(self) -> None:
        """Say which way round the sheet is.

        :returns: nothing. Derived from the paper rather than stored, so
            it needs its own read rather than a field on the table.
        """
        self.orientation_combo.setCurrentText(
            "Landscape"
            if paper_is_landscape(self.state.project.layout.paper)
            else "Portrait"
        )

    def _refresh_stock_combo(self) -> None:
        """Say Custom, because a saved project carries a caliper.

        :returns: nothing. A thickness that came from a file has no preset
            behind it, so naming a paper the binder may not be using would
            be a confident guess.
        """
        self._set_stock_combo_custom()

    def _set_stock_combo_custom(self) -> None:
        """Point the stock combo at Custom without firing its handler.

        :returns: nothing. Called on a refresh, and after a thickness is
            typed by hand -- at which point the named stock no longer
            describes the number in the box, which is the second half of
            B29.
        """
        blocked = self.paper_stock_combo.blockSignals(True)
        try:
            self.paper_stock_combo.setCurrentText(CUSTOM_STOCK_LABEL)
        finally:
            self.paper_stock_combo.blockSignals(blocked)

    def _on_composite_parity_changed(self, _parity: str) -> None:
        """Redraw the composite for the newly chosen parity.

        :param _parity: the parity key, read again by
            :meth:`_start_composite` when the redraw actually runs.
        :returns: nothing.
        """
        self._schedule_composite()

    def _sync_paper_choices(self, paper: tuple[float, float]) -> str:
        """Make sure the combo can name ``paper``, and say what to select.

        A custom size -- from the CLI's ``--paper``, or an older project --
        matches no preset, so it gets an entry of its own naming its
        dimensions. Offering it beats snapping it to the nearest preset,
        which would silently resize the user's book.

        This was worked out once, in the constructor, and never again.
        ``refresh_from_project`` therefore left the combo showing the
        PREVIOUS job's paper when the newly opened one was custom: the
        panel said "A4" over a 500x700 sheet. The document itself stayed
        correct -- changing orientation still swapped the real dimensions
        -- so nothing broke, it just stated something untrue, which is the
        exact failure that method exists to prevent.

        :param paper: the dimensions to name.
        :returns: the combo entry that describes ``paper``.
        """
        preset = preset_name_for(paper)
        wanted = None
        if preset is None:
            width, height = sorted(paper)
            wanted = f"Custom ({width:.0f} x {height:.0f}pt)"

        # Drop a custom entry that no longer describes anything, so a
        # project opened after it does not inherit a stale size.
        if self._custom_paper_label is not None and self._custom_paper_label != wanted:
            index = self._paper_names.index(self._custom_paper_label)
            self.paper_combo.removeItem(index)
            self._paper_names.pop(index)
            self._custom_paper_label = None

        if wanted is not None and self._custom_paper_label is None:
            self.paper_combo.addItem(wanted)
            self._paper_names.append(wanted)
            self._custom_paper_label = wanted

        return wanted if wanted is not None else preset

    def _current_paper_pt(self) -> tuple[float, float]:
        """The paper dimensions the combos currently describe."""
        name = self.paper_combo.currentText()
        for preset_name, dimensions in PAPER_PRESETS:
            if preset_name == name:
                return dimensions
        # The custom entry: keep whatever the project already has.
        return self.state.project.layout.paper

    def _on_paper_changed(self, _name: str) -> None:
        """Apply a new sheet size, preserving the chosen orientation.

        :returns: nothing.
        """
        landscape = self.orientation_combo.currentText() == "Landscape"
        plan = apply_layout_change(
            self.state,
            lambda project: set_paper(
                project, self._current_paper_pt(), landscape=landscape
            ),
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_orientation_changed(self, value: str) -> None:
        """Turn the sheet, keeping its size.

        :returns: nothing.
        """
        plan = apply_layout_change(
            self.state,
            lambda project: set_paper(
                project, project.layout.paper, landscape=value == "Landscape"
            ),
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_mode_tab_changed(self, index: int) -> None:
        """Switching tab switches how the book is made.

        :param index: the newly selected tab.
        :returns: nothing.

        The tab IS the mode, so this is the only place ``fold_scheme`` is
        set from the UI. Guarded against re-entry: selecting the tab during
        construction, or from :meth:`refresh`, must not write the value back
        and push a redundant undo entry.
        """
        if getattr(self, "_syncing_mode", False):
            return
        scheme = "folio" if index == self._signature_tab_index else "none"
        if scheme == self.state.project.layout.fold_scheme:
            return
        self._on_fold_scheme_changed(scheme)

    def _on_fold_scheme_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_fold_scheme(project, value))
        self._refresh_binding_readout(plan)
        self._sync_signature_tab()
        self.layout_changed.emit(plan)

    def _on_paper_stock_changed(self, name: str) -> None:
        """Adopt a named stock's caliper.

        :param name: the stock's key -- its name, not the combo's label,
            which also carries the caliper in millimetres.
        :returns: nothing.
        """
        if name == CUSTOM_STOCK_LABEL:
            return
        plan = apply_layout_change(
            self.state, lambda project: set_paper_stock(project, name)
        )
        self._show_derived_thickness(plan)

    def _on_paper_weight_changed(self, _value=None) -> None:
        """Re-derive the caliper from the weight boxes.

        Four controls share this handler and it reads all four, the way
        :meth:`_on_crop_changed` re-reads a whole rectangle when one box
        moves: a weight, a unit, a grade and a bulk are one description
        of one sheet, and applying them one at a time would write three
        wrong calipers on the way to the right one.

        :param _value: the changed control's value, ignored. Which of the
            four moved does not matter; the answer is a function of all
            of them.
        :returns: nothing.

        Zero means *not said* rather than *infinitely thin*, so it leaves
        the thickness alone. Writing 0 back would erase a caliper set
        from a named stock, or typed into the box below, the moment
        somebody glanced at this one.
        """
        weight = self.paper_weight_spinbox.value()
        if not weight:
            return
        unit = self._choice_key("paper_weight_unit_combo")
        grade = self._choice_key("paper_grade_combo")
        stock_type = self._choice_key("paper_type_combo")
        if unit is None or stock_type is None:
            # A combo mid-rebuild, not an edit. Same guard as
            # `_control_value` applies to every keyed combo.
            return
        try:
            plan = apply_layout_change(
                self.state,
                lambda project: set_paper_from_weight(
                    project, weight, unit, stock_type, grade
                ),
            )
        except ValueError as exc:
            # A refused weight is a number the user can correct, not a
            # traceback -- the same contract every other control here has.
            self.schedule_saved.emit(f"Paper weight: {exc}")
            return
        # The named stock can no longer describe this paper. Leaving it
        # selected is B29 exactly: the panel said "80gsm copier (0.104
        # mm)" over a document holding 0.434pt. The document stayed
        # correct; the panel stated something untrue, which is the
        # failure the stock combo exists to prevent.
        self._set_stock_combo_custom()
        self._show_derived_thickness(plan)

    def _choice_key(self, name: str) -> str | None:
        """The model-side key a keyed combo is showing.

        :param name: the control's name.
        :returns: the key, or ``None`` when the combo has no valid
            selection -- a rebuild in progress rather than a choice.
        """
        keys = self._choice_keys.get(name, [])
        index = self.controls[name].currentIndex()
        return keys[index] if 0 <= index < len(keys) else None

    def _show_derived_thickness(self, plan) -> None:
        """Display a caliper the user did not type, and say where it came from.

        Shared by the named-stock combo and the weight boxes, so the two
        routes to a derived thickness cannot disagree about what they
        update afterwards -- which is how B29 was found: a thickness box
        showing one number while the document held another.

        :param plan: the re-imposed plan to announce.
        :returns: nothing.
        """
        self.paper_thickness_spinbox.blockSignals(True)
        self.paper_thickness_spinbox.set_points(
            self.state.project.layout.paper_thickness_pt
        )
        self.paper_thickness_spinbox.blockSignals(False)
        self._refresh_suggestion()
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _crop_from_boxes(self, parity: str):
        values = tuple(
            self.crop_spinboxes[(parity, edge)].points()
            for edge in ("left", "bottom", "right", "top")
        )
        return None if not any(values) else values

    def _on_crop_changed(self, key: tuple[str, str], _points: float) -> None:
        """One crop box moved, so re-read that parity's whole rectangle.

        :param key: the control's group key, ``(parity, edge)``.
        :param _points: the box's new value, unused -- all four edges are
            read together, since the model stores one rectangle.
        :returns: nothing.
        """
        self._commit_crop(key[0])

    def _commit_crop(self, parity: str) -> None:
        insets = self._crop_from_boxes(parity)
        try:
            plan = apply_layout_change(
                self.state, lambda project: set_crop(project, parity, insets)
            )
        except ValueError as exc:
            # A crop that consumes the page is refused by the imposer with
            # a message naming both numbers. Reported rather than raised:
            # it is a number the user can correct, in the box they are
            # already looking at.
            self.schedule_saved.emit(f"Crop: {exc}")
            return
        self._schedule_composite()
        self.layout_changed.emit(plan)

    def _on_auto_crop(self) -> None:
        """Fill the crop boxes from where the ink actually is."""
        from deckle.core.render import auto_crop_insets

        try:
            odd, even = auto_crop_insets(self.state.project.pages)
        except Exception as exc:  # noqa: BLE001 -- reported, never a crash
            log_exception("auto_crop_failed", exc)
            self.schedule_saved.emit(f"Could not measure the ink: {exc}")
            return
        for parity, insets in (("odd", odd), ("even", even)):
            if insets is None:
                continue
            for edge, value in zip(("left", "bottom", "right", "top"), insets):
                box = self.crop_spinboxes[(parity, edge)]
                box.blockSignals(True)
                box.set_points(value)
                box.blockSignals(False)
            self._commit_crop(parity)
        self._schedule_composite()
        self.schedule_saved.emit(
            "Crop measured from the ink -- check it before printing."
        )

    def _on_apply_suggestion(self) -> None:
        plan = apply_layout_change(self.state, apply_suggested_sheets)
        self.sheets_per_signature_spinbox.blockSignals(True)
        self.sheets_per_signature_spinbox.setValue(
            self.state.project.layout.sheets_per_signature
        )
        self.sheets_per_signature_spinbox.blockSignals(False)
        self._refresh_suggestion()
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _refresh_suggestion(self) -> None:
        """Show or hide the gathering-size advice.

        Hidden entirely when there is nothing to say, rather than left
        showing a stale sentence -- the button beside it would otherwise
        apply advice that is no longer on screen.
        """
        text = signature_suggestion_text(self.state.project.layout)
        self.suggestion_label.setText(text or "")
        self.suggestion_label.setVisible(bool(text))
        self.suggestion_button.setVisible(bool(text))

    def _refresh_binding_readout(self, plan: SheetPlan) -> None:
        self.binding_readout_label.setText(binding_readout_str(plan))
