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
    scale_mode: Literal["fit_height", "fixed_gutter"] = "fit_height"
    start_on_recto: bool = True
    landscape_policy: Literal["rotate", "scale", "letterbox"] = "rotate"
    margin_pt: float = 0.0
    """Uniform margin on the three non-spine edges: head, tail, and fore-edge.

    The gutter handles the spine side; this handles the other three. Zero
    reproduces the original edge-to-edge behaviour, which is why it defaults
    to 0.0 -- but it is rarely what you want on a real printer. A source
    scaled to full page height has *no* head or tail margin, so its content
    necessarily falls inside the printer's non-printable border and triggers
    ``clipped_by_imageable_area``. Setting this to at least the printer's
    imageable inset is what makes a page physically printable.
    """


@dataclass(frozen=True)
class Project:
    """The document model: source pages plus layout and printer settings."""

    pages: list[SourcePage]
    layout: LayoutSettings
    printer: str | None
