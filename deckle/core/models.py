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
    scale_mode: Literal["fit", "fill_height"] = "fit"
    """How source content is scaled into the content box.

    ``fit`` -- largest scale fitting BOTH box dimensions. Never overflows,
    so it is the default.
    ``fill_height`` -- fill the box height exactly; the width falls where it
    falls and may overflow, which the clipping detector reports.

    Renamed from ``fit_height``/``fixed_gutter``, which described how the
    code worked rather than what you get, and whose default could silently
    push content off the page.
    """
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
