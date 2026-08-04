"""PreviewView: the sheet-accurate preview, with the imageable-area guide.

This is where Deckle's central honesty requirement lives: **the preview is
the exported PDF rasterized, never an independent redraw of the layout
math.** ``render_visible_sheet`` below is a thin, directly-testable wrapper
around ``deckle.core.render.render_sheet``, which itself always routes
through ``deckle.core.export.export`` -- see the module docstring in
``deckle/core/render.py``. There is nothing to diff a rendered sheet
against; the only thing worth asserting is that the *path* taken to produce
it is the export path, for the exact sheet being viewed.

The other half of the honesty requirement is distinguishing *why* content
is clipped. ``clipping_warnings_for_sheet`` compares each placed output
page's on-sheet bounding box against two rectangles: the physical page
(``paper_pt``) and the printer's calibrated imageable area
(``PrinterProfile.imageable_area_pt``). Content that escapes the page
itself is a ``clipped_by_page`` warning; content that stays on the page but
falls outside the imageable area is a distinct ``clipped_by_imageable_area``
warning -- the two causes are never conflated, and their ``detail`` text
always differs (see ``clipping_warnings_for_sheet``).

Every warning below is attached to the ``sheet_index`` it affects and is
surfaced by ``PreviewView`` as a per-sheet badge -- **never a global modal
dialog**. This module deliberately imports no dialog widget for that
reason; ``tests/test_preview_fidelity.py`` asserts that directly by
inspecting this module's own source.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal, Sequence

from deckle.core.models import LayoutWarning, OutputPage, Sheet, SheetPlan
from deckle.core.profiles import PrinterProfile
from deckle.core.render import RenderedPage, render_sheet


# -- pure geometry / warning computation ----------------------------------


def imageable_rect_pt(
    paper_pt: tuple[float, float],
    imageable_area_pt: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """The imageable area as an ``(x0, y0, x1, y1)`` rect in PDF (bottom-left
    origin) points, matching ``Placement``'s own coordinate convention.

    ``imageable_area_pt`` is stored as ``(left, top, right, bottom)`` margins
    from the paper edges (see ``PrinterProfile`` and
    ``QtPrintBackend._paint_rendered_page``, the other consumer of this same
    field) -- the top/bottom pair is flipped here to translate from that
    top-left-origin convention into PDF's bottom-left one.
    """
    paper_w, paper_h = paper_pt
    left, top, right, bottom = imageable_area_pt
    return (left, bottom, paper_w - right, paper_h - top)


def _output_page_bbox(output_page: OutputPage) -> tuple[float, float, float, float] | None:
    """An output page's on-sheet footprint, in PDF points.

    ``None`` for filler pages -- there is no content to have escaped
    anything. Mirrors the footprint math in ``deckle/core/export.py``'s
    ``_place_output_page``: for a 90/270-degree rotation the placement rect
    already describes the *post-rotation* footprint, so width/height swap
    there and nowhere else.
    """
    if output_page.is_filler or output_page.source_ref is None:
        return None
    ref = output_page.source_ref
    placement = output_page.placement
    scaled_w = ref.width_pt * placement.scale_x
    scaled_h = ref.height_pt * placement.scale_y
    if placement.rotate_deg % 360 in (90, 270):
        footprint_w, footprint_h = scaled_h, scaled_w
    else:
        footprint_w, footprint_h = scaled_w, scaled_h
    return (placement.tx, placement.ty, placement.tx + footprint_w, placement.ty + footprint_h)


def _escapes(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = inner
    ox0, oy0, ox1, oy1 = outer
    return x0 < ox0 or y0 < oy0 or x1 > ox1 or y1 > oy1


def clipping_warnings_for_sheet(
    sheet: Sheet,
    paper_pt: tuple[float, float],
    imageable_area_pt: tuple[float, float, float, float],
) -> list[LayoutWarning]:
    """Front/back clipping warnings for one sheet, page-box vs imageable-area
    kept strictly separate.

    A side that escapes the physical page gets ``clipped_by_page`` only --
    if content is already off the page, reporting it as *also* outside the
    (page-relative) imageable area would be redundant and would blur the
    two distinct causes this function exists to keep apart.
    """
    paper_rect = (0.0, 0.0, paper_pt[0], paper_pt[1])
    imageable_rect = imageable_rect_pt(paper_pt, imageable_area_pt)

    warnings: list[LayoutWarning] = []
    for side_name, output_page in (("front", sheet.front), ("back", sheet.back)):
        if output_page is None:
            continue
        bbox = _output_page_bbox(output_page)
        if bbox is None:
            continue

        if _escapes(bbox, paper_rect):
            warnings.append(
                LayoutWarning(
                    sheet_index=sheet.index,
                    kind="clipped_by_page",
                    detail=(
                        f"sheet {sheet.index} {side_name}: content extends past the "
                        "physical page edge"
                    ),
                )
            )
            continue

        if _escapes(bbox, imageable_rect):
            warnings.append(
                LayoutWarning(
                    sheet_index=sheet.index,
                    kind="clipped_by_imageable_area",
                    detail=(
                        f"sheet {sheet.index} {side_name}: content is on the page but "
                        "falls outside the printer's imageable area"
                    ),
                )
            )
    return warnings


def warnings_for_plan(plan: SheetPlan, profile: PrinterProfile) -> dict[int, list[LayoutWarning]]:
    """Every warning (Imposer-produced and clipping) grouped by sheet index.

    Grouping by ``sheet_index`` -- rather than a flat list -- is what lets a
    viewer show only the warnings for the sheet currently on screen, per
    sheet, never a global modal.
    """
    by_sheet: dict[int, list[LayoutWarning]] = {}
    for warning in plan.warnings:
        by_sheet.setdefault(warning.sheet_index, []).append(warning)
    for sheet in plan.sheets:
        for warning in clipping_warnings_for_sheet(sheet, plan.paper_pt, profile.imageable_area_pt):
            by_sheet.setdefault(warning.sheet_index, []).append(warning)
    return by_sheet


def badge_text(warnings: Sequence[LayoutWarning]) -> str:
    """Plain-text badge content for a sheet's warnings, empty if none.

    Deliberately not a dialog -- just text a caller sticks on a label next
    to the sheet it describes.
    """
    return "\n".join(w.detail for w in warnings)


# -- the preview render path (routes through export, always) --------------


def render_visible_sheet(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int = 150,
    cancel: threading.Event | None = None,
) -> RenderedPage:
    """Render exactly the sheet/side on screen -- and only that one.

    A thin pass-through to ``deckle.core.render.render_sheet``, which
    exports ``sheets=[sheet_index]`` and rasterizes that artifact. Kept as
    its own function (rather than calling ``render_sheet`` directly from
    the Qt class below) so the preview's own render path is the thing
    ``tests/test_preview_fidelity.py`` patches and asserts against -- not
    an implementation detail one layer down.
    """
    return render_sheet(plan, sheet_index, side, dpi, cancel=cancel)


@dataclass(frozen=True)
class PreviewFrame:
    """One rendered preview frame plus the warnings for that sheet.

    Plain data -- no Qt/PIL types -- so the composition of "which sheet,
    which side, what warnings apply" can be exercised headlessly.
    """

    sheet_index: int
    side: Literal["front", "back"]
    rendered: RenderedPage
    warnings: list[LayoutWarning]


def build_preview_frame(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int = 150,
    cancel: threading.Event | None = None,
) -> PreviewFrame:
    """Render sheet ``sheet_index``/``side`` and attach only its own warnings."""
    rendered = render_visible_sheet(plan, sheet_index, side, dpi=dpi, cancel=cancel)
    warnings = warnings_for_plan(plan, profile).get(sheet_index, [])
    return PreviewFrame(sheet_index=sheet_index, side=side, rendered=rendered, warnings=warnings)


# -- Qt wiring -----------------------------------------------------------
# Imported lazily so this module -- and every pure function above -- stays
# importable without PySide6/a display, matching arrange_view.py/import_view.py.


def _qt_core():
    from PySide6.QtCore import QObject, QRectF, QThread, Signal

    return QObject, QRectF, QThread, Signal


def _qt_gui():
    from PySide6.QtGui import QImage, QPainter, QPen, QPixmap

    return QImage, QPainter, QPen, QPixmap


def _qt_widgets():
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

    return QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget


class PreviewWorker:
    """Runs ``build_preview_frame`` on a background ``QThread``.

    Plain class, not a ``QObject`` -- mirrors ``ThumbnailWorker`` in
    ``arrange_view.py``.
    """

    def __init__(
        self,
        plan: SheetPlan,
        profile: PrinterProfile,
        sheet_index: int,
        side: Literal["front", "back"],
    ) -> None:
        self.plan = plan
        self.profile = profile
        self.sheet_index = sheet_index
        self.side = side
        self.frame: PreviewFrame | None = None

    def run(self) -> None:
        self.frame = build_preview_frame(self.plan, self.profile, self.sheet_index, self.side)


class PreviewView:
    """Sheet-by-sheet preview with a front/back toggle and imageable-area guide.

    Changing sheet, side, or layout settings (via a connected
    ``LayoutPanel.layout_changed``) re-renders **only** the sheet currently
    on screen -- never the whole document -- matching the "arithmetic
    everywhere, rasterize only what's visible" split documented on
    ``LayoutPanel``. Warnings for the current sheet are shown as a
    non-modal label directly on the view, never a dialog.
    """

    def __init__(self, plan: SheetPlan, profile: PrinterProfile, parent=None) -> None:
        QObject, QRectF, QThread, Signal = _qt_core()
        QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget = _qt_widgets()

        self.plan = plan
        self.profile = profile
        self.sheet_index = plan.sheets[0].index if plan.sheets else 0
        self.side: Literal["front", "back"] = "front"

        self.widget = QWidget(parent)
        outer = QVBoxLayout(self.widget)

        toolbar = QHBoxLayout()
        self.front_button = QPushButton("Front", self.widget)
        self.back_button = QPushButton("Back", self.widget)
        self.sheet_spinbox = QSpinBox(self.widget)
        self.sheet_spinbox.setMinimum(0)
        self.sheet_spinbox.setMaximum(max(0, len(plan.sheets) - 1))
        self.sheet_spinbox.setValue(self.sheet_index)
        toolbar.addWidget(self.front_button)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.sheet_spinbox)
        outer.addLayout(toolbar)

        self.image_label = QLabel(self.widget)
        outer.addWidget(self.image_label)

        # A per-sheet warning badge -- a plain, non-modal label.
        self.warning_label = QLabel("", self.widget)
        self.warning_label.setWordWrap(True)
        outer.addWidget(self.warning_label)

        self.front_button.clicked.connect(lambda: self._set_side("front"))
        self.back_button.clicked.connect(lambda: self._set_side("back"))
        self.sheet_spinbox.valueChanged.connect(self._set_sheet_index)

        self._QThread = QThread
        self._thread = None
        self._worker: PreviewWorker | None = None

        self.refresh()

    # -- navigation --------------------------------------------------------

    def _set_side(self, side: Literal["front", "back"]) -> None:
        self.side = side
        self.refresh()

    def _set_sheet_index(self, sheet_index: int) -> None:
        self.sheet_index = sheet_index
        self.refresh()

    def on_layout_changed(self, plan: SheetPlan) -> None:
        """Connected to ``LayoutPanel.layout_changed``: swap in the fresh
        whole-document plan but re-render only the sheet on screen.
        """
        self.plan = plan
        self.sheet_spinbox.setMaximum(max(0, len(plan.sheets) - 1))
        self.refresh()

    # -- rendering -----------------------------------------------------------

    def refresh(self) -> None:
        """Kick off a background render of exactly the visible sheet/side."""
        worker = PreviewWorker(self.plan, self.profile, self.sheet_index, self.side)
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_frame_ready(worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_frame_ready(self, worker: PreviewWorker) -> None:
        frame = worker.frame
        if frame is None:
            return
        self.warning_label.setText(badge_text(frame.warnings))
        self._paint_frame(frame)

    def _paint_frame(self, frame: PreviewFrame) -> None:
        QImage, QPainter, QPen, QPixmap = _qt_gui()
        QRectF = _qt_core()[1]
        rendered = frame.rendered
        if rendered.width == 0 or rendered.height == 0:
            self.image_label.clear()
            return

        image = QImage(rendered.rgba, rendered.width, rendered.height, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(image)

        # Draw the imageable-area guide directly on top of the rasterized
        # sheet -- a visible rectangle, not a separately-computed layout.
        dpi_scale = rendered.height / self.plan.paper_pt[1] if self.plan.paper_pt[1] else 0.0
        x0, y0, x1, y1 = imageable_rect_pt(self.plan.paper_pt, self.profile.imageable_area_pt)
        paper_h = self.plan.paper_pt[1]
        painter = QPainter(pixmap)
        try:
            pen = QPen()
            pen.setWidth(2)
            painter.setPen(pen)
            # Flip y: PDF origin bottom-left, image origin top-left.
            guide = QRectF(
                x0 * dpi_scale,
                (paper_h - y1) * dpi_scale,
                (x1 - x0) * dpi_scale,
                (y1 - y0) * dpi_scale,
            )
            painter.drawRect(guide)
        finally:
            painter.end()

        self.image_label.setPixmap(pixmap)
