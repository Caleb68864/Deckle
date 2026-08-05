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
    """

    scale_x: float
    scale_y: float
    tx: float
    ty: float
    rotate_deg: int


@dataclass(frozen=True)
class SourceRef:
    """A reference to a single page within a source file on disk."""

    path: str
    page_index: int
    sha256: str
    width_pt: float
    height_pt: float


@dataclass(frozen=True)
class SourcePage:
    """A page as it exists in the input, before placement is decided."""

    ref: SourceRef
    rotate_deg: int
    skipped: bool


@dataclass(frozen=True)
class OutputPage:
    """A source page (or blank filler) assigned a placement on a sheet."""

    source_ref: SourceRef | None
    placement: Placement
    is_filler: bool


@dataclass(frozen=True)
class Mark:
    """A single line-segment mark drawn on a sheet, in sheet points.

    Origin is bottom-left, matching PDF page-coordinate conventions. Every
    mark is a line segment regardless of ``kind`` -- no colour, no width, no
    fill. Stroke styling is entirely the renderer's concern.
    """

    kind: Literal["sewing_station","signature_order","fold_line"]
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Side:
    """One printable face of a sheet: its output pages plus any marks."""

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
    """One physical piece of paper with an optional front and back."""

    index: int
    front: Side | None
    back: Side | None


@dataclass(frozen=True)
class LayoutWarning:
    """A non-fatal issue surfaced while planning a sheet's layout."""

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
    ]
    detail: str


@dataclass(frozen=True)
class Signature:
    """A group of sheets folded and nested together as one signature."""

    index: int
    sheet_indices: tuple[int, ...]
    blank_count: int


@dataclass(frozen=True)
class SheetPlan:
    """The full set of sheets produced by imposing a project's pages."""

    sheets: list[Sheet]
    paper_pt: tuple[float, float]
    warnings: list[LayoutWarning]
    signatures: tuple[Signature, ...] = ()


@dataclass(frozen=True)
class LayoutSettings:
    """User-configurable layout and binding options for imposition."""

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
    paper_thickness_pt: float = 0.0
    sewing_stations: int = 3
    blank_mode: Literal["end","balanced"] = "end"


@dataclass(frozen=True)
class Project:
    """The document model: source pages plus layout and printer settings."""

    pages: list[SourcePage]
    layout: LayoutSettings
    printer: str | None
