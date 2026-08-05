"""Pure data models for Deckle's four-level page vocabulary.

Source page -> output page -> sheet -> pass. All types here are frozen
dataclasses with full type annotations and no behavior beyond simple
derived properties. This module must not import PySide6, PyQt, or any
PDF/image I/O library -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Placement:
    """An affine placement of a page's content on a sheet, in PDF points.

    Origin is bottom-left, matching PDF page-coordinate conventions.

    :ivar scale_x: horizontal scale factor applied to the source content.
    :ivar scale_y: vertical scale factor. Equal to ``scale_x`` for every
        placement Deckle emits -- aspect ratio is never distorted.
    :ivar tx: translation of the content's lower-left corner, in sheet
        points.
    :ivar ty: translation of the content's lower-left corner, in sheet
        points.
    :ivar rotate_deg: rotation applied about the placement's own footprint
        centre. ``0``, ``90``, ``180`` or ``270``; ``tx``/``ty`` already
        describe the *post*-rotation footprint.
    """

    scale_x: float
    scale_y: float
    tx: float
    ty: float
    rotate_deg: int


@dataclass(frozen=True)
class SourceRef:
    """A reference to a single page within a source file on disk.

    :ivar path: the file the page lives in. Empty for a blank inserted in
        the arrange view -- see ``deckle.app.state.BLANK_SOURCE_PATH``.
    :ivar page_index: zero-based index within that file. ``-1`` for an
        inserted blank.
    :ivar sha256: the content hash of the whole file at import time. This
        is what lets ``deckle.core.project_io.load_project`` tell "the
        source moved" apart from "the source changed underneath us".
    :ivar width_pt: the page's upright width in PDF points.
    :ivar height_pt: the page's upright height in PDF points.
    """

    path: str
    page_index: int
    sha256: str
    width_pt: float
    height_pt: float


@dataclass(frozen=True)
class SourcePage:
    """A page as it exists in the input, before placement is decided.

    :ivar ref: where the page's content comes from.
    :ivar rotate_deg: the user's own rotation for this page, applied on top
        of whatever the source file says. Distinct from
        ``Placement.rotate_deg``, which is the imposer's decision.
    :ivar skipped: whether the page is excluded from imposition entirely.
        A skipped page keeps its position in ``Project.pages`` so
        un-skipping restores it where it was.
    """

    ref: SourceRef
    rotate_deg: int
    skipped: bool


@dataclass(frozen=True)
class OutputPage:
    """A source page (or blank filler) assigned a placement on a sheet.

    :ivar source_ref: the content placed here, or ``None`` for a filler.
    :ivar placement: the exact geometry the exporter will reproduce.
    :ivar is_filler: whether this slot is padding rather than content.
        Filler pages exist so signature arithmetic works out; the exporter
        leaves their page blank.
    """

    source_ref: SourceRef | None
    placement: Placement
    is_filler: bool


@dataclass(frozen=True)
class Mark:
    """A single line-segment mark drawn on a sheet, in sheet points.

    Origin is bottom-left, matching PDF page-coordinate conventions. Every
    mark is a line segment regardless of ``kind`` -- no colour, no width, no
    fill. Stroke styling is entirely the renderer's concern.

    :ivar kind: what the segment means to a binder. ``fold_line`` is where
        the sheet folds, ``sewing_station`` where a needle goes through,
        ``signature_order`` the staircase bar that makes a miscollated
        stack visible before a single stitch.
    :ivar x0: start of the segment, in sheet points.
    :ivar y0: start of the segment, in sheet points.
    :ivar x1: end of the segment, in sheet points.
    :ivar y1: end of the segment, in sheet points.
    """

    kind: Literal["sewing_station","signature_order","fold_line","cut_line"]
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Side:
    """One printable face of a sheet: its output pages plus any marks.

    :ivar pages: the leaves imposed onto this face -- one under gutter
        shift, two under folio. Never empty: an absent side is ``None``,
        never ``Side(pages=())``, which is what keeps "this face does not
        exist" distinct from "this face carries only filler". See
        ``deckle.core.print_session`` for why that distinction is
        load-bearing.
    :ivar marks: bindery marks drawn on this face.
    :raises ValueError: if ``pages`` is empty.
    """

    pages: tuple[OutputPage, ...]
    marks: tuple[Mark, ...] = ()

    def __post_init__(self) -> None:
        if not self.pages:
            raise ValueError(
                "Side.pages must not be empty -- an absent side is None, "
                "never Side(pages=())"
            )


@dataclass(frozen=True)
class Sheet:
    """One physical piece of paper with an optional front and back.

    :ivar index: the sheet's position in the plan, and the identifier every
        pass, export subset and cache key refers to it by.
    :ivar front: the first face through the printer, or ``None``.
    :ivar back: the second face, or ``None`` -- an odd final sheet under
        gutter shift has no back at all.
    """

    index: int
    front: Side | None
    back: Side | None


@dataclass(frozen=True)
class LayoutWarning:
    """A non-fatal issue surfaced while planning a sheet's layout.

    Advisory by construction: a warning never stops an imposition and never
    changes an exit code. It is said out loud so the user can decide, which
    is why every consumer -- the CLI, the preview badge -- surfaces them
    rather than filtering them.

    :ivar sheet_index: the sheet the warning is about, so a viewer can show
        it beside that sheet instead of as a global modal. ``-1`` for a
        warning raised at import time, before sheets exist.
    :ivar kind: a stable identifier for the class of problem.
    :ivar detail: the user-facing explanation.
    """

    sheet_index: int
    kind: Literal[
        "clipped_by_page",
        "clipped_by_imageable_area",
        "mixed_orientation",
        "mixed_dpi",
        "sheet_orientation",
        "signature_padding",
        "creep_advisory",
        "landscape_imageable_unverified",
        "grain_direction",
    ]
    detail: str


@dataclass(frozen=True)
class Signature:
    """A group of sheets folded and nested together as one signature.

    :ivar index: the signature's position in binding order, and what the
        ``signature_order`` mark encodes as its staircase step.
    :ivar sheet_indices: the ``Sheet.index`` values in this signature,
        contiguous and in binding order. Concatenating every signature's
        indices reproduces the plan's sheets exactly once each -- an
        invariant ``SaddleStitchStrategy`` asserts rather than assumes.
    :ivar blank_count: how many of this signature's slots are padding.
    """

    index: int
    sheet_indices: tuple[int, ...]
    blank_count: int


@dataclass(frozen=True)
class SheetPlan:
    """The full set of sheets produced by imposing a project's pages.

    :ivar sheets: every sheet, in print order.
    :ivar paper_pt: the paper size the sheets were laid out for, as
        ``(width, height)`` in points.
    :ivar warnings: everything non-fatal noticed while planning. Advisory,
        never a failure -- but callers are expected to surface them.
    :ivar signatures: the signature grouping, empty under
        ``fold_scheme="none"`` where there is nothing to gather.
    """

    sheets: list[Sheet]
    paper_pt: tuple[float, float]
    warnings: list[LayoutWarning]
    signatures: tuple[Signature, ...] = ()


@dataclass(frozen=True)
class LayoutSettings:
    """User-configurable layout and binding options for imposition.

    The three margin fields, ``gutter_pt`` and ``slack_to`` carry their own
    extended documentation below -- they are the settings whose behaviour
    is not recoverable from their names.

    :ivar paper: sheet size as ``(width, height)`` in points.
    :ivar gutter_pt: the spine margin. See ``margin_outer_pt`` for why this
        is the fourth margin and not a separate concept.
    :ivar binding_edge: which edge the book is bound on. Under
        ``fold_scheme="folio"`` this changes meaning to *reading
        direction* -- see ``deckle.core.layout.SaddleStitchStrategy``.
    :ivar start_on_recto: whether the first content page is a right-hand
        page. Honoured for free in the one-page-per-side layout, where
        output index 0 is a recto by definition.
    :ivar landscape_policy: what to do with a landscape page in a portrait
        cell. ``rotate`` turns it and warns.
    :ivar margin_top_pt: head margin, in points.
    :ivar margin_bottom_pt: tail margin, in points.
    :ivar fold_scheme: ``none`` for one page per side, ``folio`` for
        saddle-stitch signatures.
    :ivar sheets_per_signature: how many sheets are nested into one
        gathering under ``folio``.
    :ivar paper_thickness_pt: stock thickness, used **only** to predict
        fore-edge creep as an advisory. No placement Deckle emits ever
        differs because of this value.
    :ivar sewing_stations: how many sewing-station marks per signature.
        ``0`` disables them.
    :ivar blank_mode: where padding blanks land -- all in the final
        signature (``end``), or spread so no gathering is more than one
        sheet thinner than its neighbours (``balanced``).
    """

    paper: tuple[float, float]
    gutter_pt: float
    binding_edge: Literal["left", "right"]
    # There is deliberately no scale mode. Content is always scaled to the
    # largest size fitting the content box in BOTH dimensions -- which fills
    # the page height whenever height is the binding constraint, and scales
    # down when width is. The earlier "fill height" mode differed from this
    # only by overflowing the page, and once margins existed its sole
    # distinct behaviour was producing output that could not be printed.
    start_on_recto: bool = True
    landscape_policy: Literal["rotate", "scale", "letterbox"] = "rotate"
    margin_top_pt: float = 0.0
    margin_bottom_pt: float = 0.0
    margin_outer_pt: float = 0.0
    """Margins on the three non-spine edges: head, tail, and fore-edge.

    The **fourth** edge is the spine, and its margin is ``gutter_pt`` -- the
    gutter *is* the inner margin, so these four fields describe all four
    sides of the page. Each is honoured exactly: any slack left over after
    scaling lands on the fore-edge, never on the spine, so the gutter you
    ask for is the gutter you get.

    All default to 0.0, which reproduces edge-to-edge behaviour -- rarely
    what you want on a real printer. Content scaled to full page height has
    no head or tail margin at all, so it necessarily falls inside the
    printer's non-printable border and triggers
    ``clipped_by_imageable_area``.
    """

    slack_to: Literal["gutter", "outer", "split"] = "gutter"
    """Where spare horizontal width goes when content is narrower than its box.

    Source pages of differing widths produce differing slack, and this
    decides which margin absorbs the difference -- i.e. which one stays
    constant through the book and which one varies.

    ``gutter`` (default) -- all slack to the spine. The fore-edge is exact
    and identical on every page; the gutter varies and is a **minimum**.
    Good default because the fore-edge is the edge you see when the book is
    closed, and gutter variation disappears into the binding.

    ``outer`` -- all slack to the fore-edge. The gutter is exact and
    identical on every page; the fore-edge varies. Prefer this when a
    consistent spine margin matters more, e.g. a fixed punch or sewing
    template.

    ``split`` -- half each. Both vary, but their requested difference is
    preserved and the content sits visually centred between them.

    Vertical slack is always split: neither head nor tail has a binding to
    accommodate, so there is nothing to bias toward.
    """

    margins_linked: bool = True
    """Whether the UI edits the three margins as one value or individually.

    Persisted with the project so reopening restores how you were working,
    not just the numbers. Purely presentational -- the imposer always reads
    the three fields independently.
    """

    fold_scheme: Literal["none","folio"] = "none"
    sheets_per_signature: int = 4
    grain: Literal["long", "short", "unknown"] = "unknown"
    """Which way the paper's fibres run. Defaults to ``"unknown"``, silent.

    Grain is the material property bookbinding literature treats as most
    consequential, and no imposition tool models it. Fibres align during
    manufacture, and paper folds cleanly *along* them and cracks *across*
    them. A book folded against the grain will not open flat, cockles when
    glue introduces moisture, and warps as humidity changes.

    ``"long"`` means the fibres run along the sheet's longer edge, which is
    what ordinary office letter and A4 stock is. ``"short"`` means the
    shorter edge -- binders buy short-grain stock specifically so a
    half-folded sheet folds with the grain rather than across it.

    The rule this exists to check is simply: **grain should run parallel to
    the spine.** Deckle knows the sheet size, its orientation and where the
    spine falls, which is everything the check needs.

    Default ``"unknown"`` because most people do not know their paper's
    grain and a warning they cannot act on is noise. Set it and Deckle will
    tell you when a fold is going to fight the paper.
    """

    paper_thickness_pt: float = 0.0
    sewing_stations: int = 3
    blank_mode: Literal["end","balanced"] = "end"


@dataclass(frozen=True)
class Project:
    """The document model: source pages plus layout and printer settings.

    :ivar pages: the ordered page list, including skipped pages and
        inserted blanks. Plain Python value objects -- never a
        ``pikepdf.Pdf.pages`` proxy; see
        ``deckle.app.views.arrange_view`` for why that boundary matters.
    :ivar layout: the imposition settings.
    :ivar printer: the printer name recorded with the project, or ``None``.
    """

    pages: list[SourcePage]
    layout: LayoutSettings
    printer: str | None
