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
class Sheet:
    """One physical piece of paper with an optional front and back."""

    index: int
    front: OutputPage | None
    back: OutputPage | None


@dataclass(frozen=True)
class LayoutWarning:
    """A non-fatal issue surfaced while planning a sheet's layout."""

    sheet_index: int
    kind: Literal[
        "clipped_by_page",
        "clipped_by_imageable_area",
        "mixed_orientation",
        "mixed_dpi",
    ]
    detail: str


@dataclass(frozen=True)
class SheetPlan:
    """The full set of sheets produced by imposing a project's pages."""

    sheets: list[Sheet]
    paper_pt: tuple[float, float]
    warnings: list[LayoutWarning]


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

    maximize_gutter: bool = True
    """Push spare horizontal space into the gutter instead of splitting it.

    Defaults on, because it is what binding wants: content sits as far from
    the spine as it can, the fore-edge margin lands on exactly its requested
    value, and ``gutter_pt`` becomes a **minimum** rather than an exact
    figure. A generous spine margin is nearly always preferable -- it is the
    one that disappears into the binding.

    Turn it off to share the slack equally between inner and outer, which
    preserves their requested difference and visually centres the content
    between them.

    Vertical slack is always shared, since no edge there has a binding to
    accommodate.
    """

    margins_linked: bool = True
    """Whether the UI edits the three margins as one value or individually.

    Persisted with the project so reopening restores how you were working,
    not just the numbers. Purely presentational -- the imposer always reads
    the three fields independently.
    """


@dataclass(frozen=True)
class Project:
    """The document model: source pages plus layout and printer settings."""

    pages: list[SourcePage]
    layout: LayoutSettings
    printer: str | None
