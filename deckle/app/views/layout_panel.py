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

from dataclasses import replace
from typing import Literal

from deckle.app.state import AppState
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
import os

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import Project, SheetPlan
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.schedule import build_schedule, format_schedule_text

BINDING_EDGES: tuple[str, ...] = ("left", "right")

LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale", "letterbox")

FOLD_SCHEMES: tuple[str, ...] = ("none", "folio")

BLANK_MODES: tuple[str, ...] = ("end", "balanced")

#: Paper grain, in the order the combo offers it. "Unknown" leads because it
#: is the honest default -- most people have not checked their stock.
GRAINS: tuple[tuple[str, str], ...] = (
    ("unknown", "Unknown"),
    ("long", "Long grain"),
    ("short", "Short grain"),
)

SCHEDULE_TOOLTIP = (
    "Write the binding schedule to a text file: which sheets gather into "
    "each signature, which way round they nest, where the blanks fall, and "
    "where to pierce for sewing.\n\n"
    "Print it and keep it at the bench -- the imposed PDF says nothing "
    "about what to do with the paper."
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
#: ``deckle/cli.py``'s ``--paper`` presets so both front ends offer the
#: same stock.
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
    project: Project, landscape_policy: Literal["rotate", "scale", "letterbox"]
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
    from PySide6.QtCore import QObject, Signal

    return QObject, Signal


def _qt_widgets():
    from PySide6.QtWidgets import (
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QLabel,
        QPushButton,
        QRadioButton,
        QSpinBox,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    return (
        QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
        QLabel, QPushButton, QRadioButton, QSpinBox, QTabWidget,
        QVBoxLayout, QWidget,
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
        QObject, Signal = _qt_core()
        (
            QButtonGroup,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFormLayout,
            QLabel,
            QPushButton,
            QRadioButton,
            QSpinBox,
            QTabWidget,
            QVBoxLayout,
            QWidget,
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
        page_setup = QWidget(self.widget)
        form = QFormLayout(page_setup)
        # Numeric fields hold values like "0.750". Letting them stretch to
        # the pane width pushes the form wider than the column and grows a
        # horizontal scrollbar across the whole settings panel -- the one
        # kind of scrolling a settings form should never need.
        form.setFieldGrowthPolicy(_qt_fields_at_size_hint())
        outer.addWidget(page_setup)

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

        self.single_hint_label = QLabel(
            "Sheets are printed but never folded. Each one carries two pages, "
            "one per side, and the gutter alternates so it always falls on "
            "the bound edge.\n\n"
            "Print all fronts, reload the stack, print all backs, then bind "
            "the stack however you like -- glued, punched, or side-sewn.\n\n"
            "This is the proven path, and it needs no settings beyond Page "
            "setup above.",
            single_tab,
        )
        self.single_hint_label.setWordWrap(True)
        single_form.addRow(self.single_hint_label)

        signature_tab = QWidget(self.widget)
        signature_form = QFormLayout(signature_tab)
        signature_form.setFieldGrowthPolicy(_qt_fields_at_size_hint())
        self.tabs.addTab(signature_tab, "Signatures")
        self._signature_tab_index = self.tabs.indexOf(signature_tab)

        # Lengths are stored in points but entered in whatever unit suits the
        # job -- inches for a US letter binder, cm for metric stock.
        self.unit_combo = QComboBox(self.widget)
        self.unit_combo.addItems(["pt", "in", "cm", "mm"])
        self.unit_combo.setCurrentText("in")
        self._unit = "in"
        form.addRow("Units:", self.unit_combo)

        # Paper size and orientation. Folio needs landscape stock -- two
        # portrait pages side by side do not fit on a portrait sheet -- and
        # until now the GUI offered no way to say so at all, only the CLI's
        # --paper. The imposer could warn about it and nothing else.
        self.paper_combo = QComboBox(self.widget)
        for name, _dimensions in PAPER_PRESETS:
            self.paper_combo.addItem(name)
        self._paper_names = [name for name, _ in PAPER_PRESETS]
        current_preset = preset_name_for(state.project.layout.paper)
        if current_preset is None:
            # A custom size from the CLI or an older project. Offer it as an
            # option rather than snapping it to the nearest preset.
            width, height = sorted(state.project.layout.paper)
            self._custom_paper_label = f"Custom ({width:.0f} x {height:.0f}pt)"
            self.paper_combo.addItem(self._custom_paper_label)
            self._paper_names.append(self._custom_paper_label)
            current_preset = self._custom_paper_label
        else:
            self._custom_paper_label = None
        self.paper_combo.setCurrentIndex(self._paper_names.index(current_preset))
        self.paper_combo.setToolTip(
            "The size of the paper you are printing on -- not the size of a "
            "page in the book.\n\n"
            "Under Signatures a sheet is folded in half, so each book page "
            "ends up half the sheet."
        )
        form.addRow("Paper:", self.paper_combo)

        self.orientation_combo = QComboBox(self.widget)
        self.orientation_combo.addItems(list(ORIENTATIONS))
        self.orientation_combo.setCurrentText(
            "Landscape" if paper_is_landscape(state.project.layout.paper) else "Portrait"
        )
        self.orientation_combo.setToolTip(
            "Which way round the sheet goes through the printer.\n\n"
            "Signatures want LANDSCAPE: two portrait book pages sit side by "
            "side on one sheet, and the fold runs down the middle. On "
            "portrait stock they get squeezed, and Deckle warns rather than "
            "silently rotating your paper for you."
        )
        form.addRow("Orientation:", self.orientation_combo)

        # Grain and thickness are properties of the STOCK, so they sit with
        # the paper rather than in a mode tab. Neither changes any geometry:
        # grain drives a warning, thickness drives the creep and spine
        # estimates on the schedule.
        self.grain_combo = QComboBox(self.widget)
        for _key, label in GRAINS:
            self.grain_combo.addItem(label)
        self._grain_keys = [key for key, _ in GRAINS]
        current_grain = getattr(state.project.layout, "grain", "unknown")
        if current_grain in self._grain_keys:
            self.grain_combo.setCurrentIndex(self._grain_keys.index(current_grain))
        self.grain_combo.setToolTip(
            "Which way the paper's fibres run. Paper creases cleanly ALONG "
            "the grain and cracks ACROSS it, so the grain should run "
            "parallel to the spine.\n\n"
            "Ordinary office letter and A4 are LONG grain -- fibres along "
            "the longer edge. Turn a letter sheet landscape to fold a "
            "booklet and the fold now runs across the grain, which is why "
            "binders buy short-grain stock.\n\n"
            "Leave Unknown and Deckle stays quiet. Set it and you get a "
            "warning when a fold is going to fight the paper."
        )
        form.addRow("Paper grain:", self.grain_combo)

        self.paper_thickness_spinbox = QDoubleSpinBox(self.widget)
        self.paper_thickness_spinbox.setDecimals(4)
        self.paper_thickness_spinbox.setSingleStep(0.001)
        self.paper_thickness_spinbox.setRange(0.0, from_points(10.0, self._unit))
        self.paper_thickness_spinbox.setValue(
            from_points(state.project.layout.paper_thickness_pt, self._unit)
        )
        self.paper_thickness_spinbox.setToolTip(
            "The caliper of a single sheet. Ordinary 20lb office paper is "
            "about 0.004in; card is several times that.\n\n"
            "Deckle uses it for two things on the binding schedule: how far "
            "the innermost leaf of a signature protrudes at the fore-edge, "
            "and how thick the sewn block will be at the spine -- which is "
            "the number you cut boards against.\n\n"
            "Leave at 0 and neither is estimated."
        )
        form.addRow("Paper thickness:", self.paper_thickness_spinbox)
        self.unit_combo.setToolTip(
            "The unit every length on this tab is typed in. Values are "
            "stored in points regardless, so switching units re-displays "
            "the same measurement -- it never changes your layout."
        )

        self.gutter_spinbox = QDoubleSpinBox(self.widget)
        self.gutter_spinbox.setDecimals(3)
        self.gutter_spinbox.setSingleStep(0.125)
        self.gutter_spinbox.setRange(0.0, from_points(288.0, self._unit))
        self.gutter_spinbox.setValue(from_points(state.project.layout.gutter_pt, self._unit))
        form.addRow("Gutter:", self.gutter_spinbox)
        self.gutter_spinbox.setToolTip(
            "The margin on the spine edge -- the strip swallowed by the "
            "binding. It alternates side by side so it always falls on "
            "the bound edge. This is a MINIMUM: if a page is narrower "
            "than the others, the spare width lands here by default, so "
            "the gutter you get can exceed the gutter you asked for. "
            "Change that with 'Spare width to'."
        )

        # Pages of differing widths produce differing slack; this picks
        # which margin absorbs it -- i.e. which stays constant.
        self.slack_combo = QComboBox(self.widget)
        for _key, label in SLACK_TARGETS:
            self.slack_combo.addItem(label)
        self._slack_keys = [k for k, _ in SLACK_TARGETS]
        current_slack = state.project.layout.slack_to
        if current_slack in self._slack_keys:
            self.slack_combo.setCurrentIndex(self._slack_keys.index(current_slack))
        self.slack_combo.setToolTip(
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
        )
        form.addRow("Spare width to:", self.slack_combo)

        self.link_margins_check = QCheckBox("Link all three", self.widget)
        self.link_margins_check.setChecked(state.project.layout.margins_linked)
        form.addRow("", self.link_margins_check)
        self.link_margins_check.setToolTip(
            "Edit head, tail and fore-edge as a single value. Untick to "
            "set them independently -- useful when the fore-edge needs "
            "room for a thumb but the head does not."
        )

        # Three editable margins; the fourth edge is the spine, whose margin
        # is the gutter above.
        self.margin_spinboxes: dict[str, object] = {}
        for field, label, tip in (
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
        ):
            box = QDoubleSpinBox(self.widget)
            box.setDecimals(3)
            box.setSingleStep(0.125)
            box.setRange(0.0, from_points(216.0, self._unit))
            box.setValue(from_points(getattr(state.project.layout, field), self._unit))
            box.setToolTip(tip)
            form.addRow(label, box)
            self.margin_spinboxes[field] = box
        self._sync_margin_enabled()

        self.use_printer_margins_button = QPushButton("Use printer margins", self.widget)
        self.use_printer_margins_button.setToolTip(
            "Set the margin to the printer's non-printable inset, so content "
            "clears the dead border on every edge."
        )
        form.addRow("", self.use_printer_margins_button)

        self.binding_edge_combo = QComboBox(self.widget)
        self.binding_edge_combo.addItems(list(BINDING_EDGES))
        self.binding_edge_combo.setCurrentText(state.project.layout.binding_edge)
        self.binding_edge_combo.setToolTip(
            "Which edge the book is bound on. Left is conventional for "
            "left-to-right languages; right suits Arabic, Hebrew, or "
            "Japanese tate-gaki. This mirrors which side the gutter "
            "falls on for every page."
        )
        form.addRow("Binding edge:", self.binding_edge_combo)

        self.landscape_policy_combo = QComboBox(self.widget)
        self.landscape_policy_combo.addItems(list(LANDSCAPE_POLICIES))
        self.landscape_policy_combo.setCurrentText(state.project.layout.landscape_policy)
        self.landscape_policy_combo.setToolTip(
            "What to do with a landscape page in a portrait book.\n\n"
            "rotate: turn it 90 degrees so it fills the page (the default "
            "-- the reader turns the book).\n"
            "scale: shrink it to fit upright.\n"
            "letterbox: leave it upright with bands above and below."
        )
        form.addRow("Landscape policy:", self.landscape_policy_combo)

        # -- signature/binding controls ---------------------------------
        # Everything below lands on the Signatures tab, and is reachable
        # only while that tab is selected -- which is also what sets
        # fold_scheme="folio". The description mirrors the Flat sheets one:
        # a mode should say what it does before it asks you to configure it.
        self.signature_hint_label = QLabel(
            "Sheets are imposed two-up, folded in half, and nested inside "
            "one another to make gatherings that get sewn through the "
            "fold.\n\n"
            "Page setup above still applies -- a folded signature has a "
            "gutter and margins exactly as a flat sheet does. The settings "
            "here control only how the sheets are grouped and folded.\n\n"
            "Experimental: the page ordering is hand-written arithmetic. "
            "Save the schedule, print onto scrap, fold it, and check it "
            "reads correctly before committing a real book.",
            signature_tab,
        )
        self.signature_hint_label.setWordWrap(True)
        signature_form.addRow(self.signature_hint_label)

        self.sheets_per_signature_spinbox = QSpinBox(self.widget)
        self.sheets_per_signature_spinbox.setRange(1, 100)
        self.sheets_per_signature_spinbox.setValue(state.project.layout.sheets_per_signature)
        self.sheets_per_signature_spinbox.setToolTip(
            "How many sheets are nested inside one another to make a single "
            "folded gathering.\n\n"
            "Each sheet becomes 4 pages once folded, so 4 sheets is a "
            "16-page signature -- a common choice. More sheets means fewer "
            "gatherings to sew, but a thicker fold that bulges at the "
            "fore-edge and needs trimming."
        )
        signature_form.addRow("Sheets per signature:", self.sheets_per_signature_spinbox)

        self.blank_mode_combo = QComboBox(self.widget)
        self.blank_mode_combo.addItems(list(BLANK_MODES))
        self.blank_mode_combo.setCurrentText(state.project.layout.blank_mode)
        self.blank_mode_combo.setToolTip(
            "A folded book needs a page count that is a multiple of 4, so "
            "blanks get added. This chooses where.\n\n"
            "end: all blanks at the back of the book.\n"
            "balanced: spread across signatures, so no single gathering is "
            "noticeably emptier than its neighbours."
        )
        signature_form.addRow("Blank mode:", self.blank_mode_combo)

        self.sewing_stations_spinbox = QSpinBox(self.widget)
        self.sewing_stations_spinbox.setRange(0, 20)
        self.sewing_stations_spinbox.setValue(state.project.layout.sewing_stations)
        self.sewing_stations_spinbox.setToolTip(
            "Marks printed on the fold line showing where to pierce for "
            "sewing. Three is the traditional pamphlet stitch; five suits a "
            "taller book.\n\nSet to 0 to print no sewing marks -- useful if "
            "you are stapling rather than sewing."
        )
        signature_form.addRow("Sewing stations:", self.sewing_stations_spinbox)


        # Live readout -- "17 signatures · 67 sheets · 2 blanks" -- derived
        # from the recomputed SheetPlan, since that arithmetic is the thing
        # a binder actually decides on.
        self.binding_readout_label = QLabel("", self.widget)
        self.binding_readout_label.setToolTip(
            "What your current settings actually produce, recomputed live. "
            "This is the arithmetic a binder decides on -- how many "
            "gatherings to sew, how much paper to cut, and how many blank "
            "pages the fold count forced."
        )
        signature_form.addRow("Binding:", self.binding_readout_label)
        self.binding_readout_label.setText(binding_readout_str(recompute_plan(state.project)))

        # The schedule lives here rather than beside Save PDF because it is
        # a signature artifact: under gutter shift there is nothing to
        # gather, so the button would be permanently inert next to the
        # export actions.
        self.save_schedule_button = QPushButton("Save schedule...", signature_tab)
        self.save_schedule_button.setToolTip(SCHEDULE_TOOLTIP)
        signature_form.addRow("", self.save_schedule_button)
        self.save_schedule_button.clicked.connect(self._on_save_schedule_clicked)

        self.tabs.setCurrentIndex(
            self._signature_tab_index
            if state.project.layout.fold_scheme == "folio"
            else self._single_tab_index
        )
        self._sync_signature_tab()

        self.gutter_spinbox.valueChanged.connect(self._on_gutter_changed)
        for field, box in self.margin_spinboxes.items():
            box.valueChanged.connect(
                lambda value, f=field: self._on_margin_changed(f, value)
            )
        self.link_margins_check.toggled.connect(self._on_link_margins_toggled)
        self.slack_combo.currentIndexChanged.connect(self._on_slack_to_changed)
        self.unit_combo.currentTextChanged.connect(self._on_unit_changed)
        self.use_printer_margins_button.clicked.connect(self._on_use_printer_margins)
        self.binding_edge_combo.currentTextChanged.connect(self._on_binding_edge_changed)
        self.landscape_policy_combo.currentTextChanged.connect(self._on_landscape_policy_changed)
        self.grain_combo.currentIndexChanged.connect(self._on_grain_changed)
        self.paper_combo.currentTextChanged.connect(self._on_paper_changed)
        self.orientation_combo.currentTextChanged.connect(self._on_orientation_changed)
        self.tabs.currentChanged.connect(self._on_mode_tab_changed)
        self.sheets_per_signature_spinbox.valueChanged.connect(
            self._on_sheets_per_signature_changed
        )
        self.blank_mode_combo.currentTextChanged.connect(self._on_blank_mode_changed)
        self.sewing_stations_spinbox.valueChanged.connect(self._on_sewing_stations_changed)
        self.paper_thickness_spinbox.valueChanged.connect(self._on_paper_thickness_changed)

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
        """
        layout = self.state.project.layout
        widgets = [
            self.unit_combo, self.paper_combo, self.orientation_combo,
            self.grain_combo, self.paper_thickness_spinbox, self.gutter_spinbox,
            self.slack_combo, self.link_margins_check, self.binding_edge_combo,
            self.landscape_policy_combo, self.sheets_per_signature_spinbox,
            self.blank_mode_combo, self.sewing_stations_spinbox, self.tabs,
            *self.margin_spinboxes.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            preset = preset_name_for(layout.paper)
            if preset is not None and preset in self._paper_names:
                self.paper_combo.setCurrentIndex(self._paper_names.index(preset))
            self.orientation_combo.setCurrentText(
                "Landscape" if paper_is_landscape(layout.paper) else "Portrait"
            )
            grain = getattr(layout, "grain", "unknown")
            if grain in self._grain_keys:
                self.grain_combo.setCurrentIndex(self._grain_keys.index(grain))
            self.paper_thickness_spinbox.setValue(
                from_points(layout.paper_thickness_pt, self._unit)
            )
            self.gutter_spinbox.setValue(from_points(layout.gutter_pt, self._unit))
            if layout.slack_to in self._slack_keys:
                self.slack_combo.setCurrentIndex(self._slack_keys.index(layout.slack_to))
            self.link_margins_check.setChecked(layout.margins_linked)
            for field, box in self.margin_spinboxes.items():
                box.setValue(from_points(getattr(layout, field), self._unit))
            self.binding_edge_combo.setCurrentText(layout.binding_edge)
            self.landscape_policy_combo.setCurrentText(layout.landscape_policy)
            self.sheets_per_signature_spinbox.setValue(layout.sheets_per_signature)
            self.blank_mode_combo.setCurrentText(layout.blank_mode)
            self.sewing_stations_spinbox.setValue(layout.sewing_stations)
            self.tabs.setCurrentIndex(
                self._signature_tab_index
                if layout.fold_scheme == "folio"
                else self._single_tab_index
            )
        finally:
            for widget in widgets:
                widget.blockSignals(False)

        self._sync_margin_enabled()
        self._sync_signature_tab()
        self._refresh_binding_readout(recompute_plan(self.state.project))

    def set_document_loaded(self, loaded: bool) -> None:
        """Enable the document-dependent actions on this panel.

        :param loaded: whether a document is open.
        :returns: nothing.

        Called by the window rather than watched from here, so there is one
        place that decides what "a document exists" enables.
        """
        self._document_loaded = loaded
        self._sync_signature_tab()

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

        # A schedule for nothing is an empty schedule, so the button needs
        # both a fold scheme that gathers AND something to gather.
        self.save_schedule_button.setEnabled(folio and loaded)
        self.save_schedule_button.setToolTip(
            SCHEDULE_TOOLTIP if loaded else "Import a document to build a schedule."
        )

    def _on_gutter_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(self.state, lambda project: set_gutter_pt(project, points))
        self.layout_changed.emit(plan)

    def _on_slack_to_changed(self, index: int) -> None:
        key = self._slack_keys[index]
        plan = apply_layout_change(self.state, lambda project: set_slack_to(project, key))
        self.layout_changed.emit(plan)

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
            head = self.margin_spinboxes["margin_top_pt"].value()
            self._on_margin_changed("margin_top_pt", head)
            return
        self.layout_changed.emit(plan)

    def _on_margin_changed(self, field: str, value: float) -> None:
        points = to_points(value, self._unit)
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
            box.setValue(from_points(getattr(self.state.project.layout, field), self._unit))
            box.blockSignals(False)

    def _on_unit_changed(self, unit: str) -> None:
        """Re-display the same physical lengths in a new unit.

        The stored model is always points, so switching units must not
        change the layout -- only how it reads. Signals are blocked while
        the displayed numbers are rewritten, otherwise the spinboxes would
        emit and re-apply their pre-conversion values as if the user had
        typed them.
        """
        layout = self.state.project.layout
        boxes = [(self.gutter_spinbox, layout.gutter_pt, 288.0)]
        boxes += [
            (self.margin_spinboxes[f], getattr(layout, f), 216.0) for f in MARGIN_FIELDS
        ]
        boxes.append((self.paper_thickness_spinbox, layout.paper_thickness_pt, 10.0))
        self._unit = unit
        for box, points, cap_pt in boxes:
            box.blockSignals(True)
            box.setRange(0.0, from_points(cap_pt, unit))
            box.setDecimals(0 if unit == "pt" else 3)
            box.setSingleStep(1.0 if unit in ("pt", "mm") else 0.125)
            box.setValue(from_points(points, unit))
            box.blockSignals(False)

    def _on_use_printer_margins(self) -> None:
        """Set the margin to the active printer's non-printable inset."""
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        # Set every margin, regardless of link state -- the printer's dead
        # border applies to all four edges, so a partial application would
        # leave some edge still unprintable.
        self.margin_spinboxes["margin_top_pt"].setValue(from_points(inset, self._unit))
        if not self.link_margins_check.isChecked():
            for field in ("margin_bottom_pt", "margin_outer_pt"):
                self.margin_spinboxes[field].setValue(from_points(inset, self._unit))

    def _on_binding_edge_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_binding_edge(project, value))
        self.layout_changed.emit(plan)

    def _on_landscape_policy_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_landscape_policy(project, value))
        self.layout_changed.emit(plan)

    def _on_grain_changed(self, index: int) -> None:
        """Record the stock's grain. Changes no geometry, only the warning.

        :returns: nothing.
        """
        if not 0 <= index < len(self._grain_keys):
            return
        plan = apply_layout_change(
            self.state, lambda project: set_grain(project, self._grain_keys[index])
        )
        self.layout_changed.emit(plan)

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

    def _on_sheets_per_signature_changed(self, value: int) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_sheets_per_signature(project, value)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_blank_mode_changed(self, value: str) -> None:
        plan = apply_layout_change(self.state, lambda project: set_blank_mode(project, value))
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_sewing_stations_changed(self, value: int) -> None:
        plan = apply_layout_change(
            self.state, lambda project: set_sewing_stations(project, value)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _on_paper_thickness_changed(self, value: float) -> None:
        points = to_points(value, self._unit)
        plan = apply_layout_change(
            self.state, lambda project: set_paper_thickness_pt(project, points)
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _refresh_binding_readout(self, plan: SheetPlan) -> None:
        self.binding_readout_label.setText(binding_readout_str(plan))
