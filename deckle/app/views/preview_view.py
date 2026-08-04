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

from deckle.core.layout import content_box_rect_pt
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


#: Discrete zoom stops, so "+"/"-" step predictably instead of drifting.
ZOOM_STOPS: tuple[float, ...] = (
    0.10, 0.15, 0.25, 0.33, 0.50, 0.67, 0.75, 1.00, 1.25, 1.50, 2.00, 3.00, 4.00
)


def fit_scale(
    pixmap_size: tuple[int, int],
    viewport_size: tuple[int, int],
    *,
    max_scale: float = 1.0,
) -> float:
    """Scale that fits ``pixmap_size`` inside ``viewport_size``.

    Capped at ``max_scale`` (default 1.0) so a large window shows the sheet at
    100% rather than blurrily upscaling a raster. Pure arithmetic, no Qt, so
    the fit rule is unit-testable without a display.
    """
    pw, ph = pixmap_size
    vw, vh = viewport_size
    if pw <= 0 or ph <= 0 or vw <= 0 or vh <= 0:
        return max_scale
    return min(vw / pw, vh / ph, max_scale)


def next_zoom_stop(current: float, direction: int) -> float:
    """The next discrete stop above (+1) or below (-1) ``current``."""
    if direction > 0:
        for stop in ZOOM_STOPS:
            if stop > current + 1e-9:
                return stop
        return ZOOM_STOPS[-1]
    for stop in reversed(ZOOM_STOPS):
        if stop < current - 1e-9:
            return stop
    return ZOOM_STOPS[0]


def _make_scroll_area(parent, on_resize, on_ctrl_wheel):
    """A QScrollArea that reports resizes and Ctrl+wheel.

    Built lazily as a subclass so this module keeps its "import Qt only when
    a widget is actually constructed" property -- the core stays importable
    headlessly.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea

    class _PreviewScrollArea(QScrollArea):
        def resizeEvent(self, event):  # noqa: N802 - Qt naming
            super().resizeEvent(event)
            on_resize()

        def wheelEvent(self, event):  # noqa: N802 - Qt naming
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                on_ctrl_wheel(1 if event.angleDelta().y() > 0 else -1)
                event.accept()
                return
            super().wheelEvent(event)

    area = _PreviewScrollArea(parent)
    area.setWidgetResizable(False)
    area.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return area


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
        sides: tuple[Literal["front", "back"], ...] | None = None,
    ) -> None:
        self.plan = plan
        self.profile = profile
        self.sheet_index = sheet_index
        self.side = side
        # Spread mode renders both sides in one background pass, so the two
        # halves always come from the same plan revision -- rendering them
        # as two independent jobs could show a stale front beside a fresh
        # back if settings changed mid-flight.
        self.sides = sides or (side,)
        self.frame: PreviewFrame | None = None
        self.frames: list[PreviewFrame] = []

    def run(self) -> None:
        self.frames = [
            build_preview_frame(self.plan, self.profile, self.sheet_index, s)
            for s in self.sides
        ]
        self.frame = self.frames[0] if self.frames else None


class PreviewView:
    """Sheet-by-sheet preview with a front/back toggle and imageable-area guide.

    Changing sheet, side, or layout settings (via a connected
    ``LayoutPanel.layout_changed``) re-renders **only** the sheet currently
    on screen -- never the whole document -- matching the "arithmetic
    everywhere, rasterize only what's visible" split documented on
    ``LayoutPanel``. Warnings for the current sheet are shown as a
    non-modal label directly on the view, never a dialog.
    """

    def __init__(
        self,
        plan: SheetPlan,
        profile: PrinterProfile,
        parent=None,
        layout_settings=None,
    ) -> None:
        # Needed to draw the content-box guide alongside the imageable
        # area. Optional so the view stays constructible from a bare plan.
        self.layout_settings = layout_settings
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

        # Zoom controls. `self._zoom is None` means fit-to-window, which is
        # the default -- a letter sheet at 150 DPI is ~1275x1650px and would
        # otherwise overflow the pane at native size.
        self._zoom: float | None = None
        self._source_pixmap = None

        self.fit_button = QPushButton("Fit", self.widget)
        self.actual_button = QPushButton("100%", self.widget)
        self.zoom_out_button = QPushButton("-", self.widget)
        self.zoom_in_button = QPushButton("+", self.widget)
        self.zoom_label = QLabel("Fit", self.widget)
        for w in (
            self.zoom_out_button, self.zoom_in_button,
            self.fit_button, self.actual_button,
        ):
            w.setMaximumWidth(56)
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_in_button)
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.actual_button)
        toolbar.addWidget(self.zoom_label)

        self.image_label = QLabel()
        self.scroll_area = _make_scroll_area(
            self.widget, self._apply_zoom, self._zoom_step
        )
        self.scroll_area.setWidget(self.image_label)
        outer.addWidget(self.scroll_area, stretch=1)

        self.fit_button.clicked.connect(lambda: self._set_zoom(None))
        self.actual_button.clicked.connect(lambda: self._set_zoom(1.0))
        self.zoom_in_button.clicked.connect(lambda: self._zoom_step(1))
        self.zoom_out_button.clicked.connect(lambda: self._zoom_step(-1))

        # A per-sheet warning badge -- a plain, non-modal label.
        self.warning_label = QLabel("", self.widget)
        self.warning_label.setWordWrap(True)
        outer.addWidget(self.warning_label)

        self.both_button = QPushButton("Both", self.widget)
        self.both_button.setCheckable(True)
        self.both_button.setToolTip(
            "Show front and back of the same sheet side by side, so you can "
            "check the gutter mirrors without toggling."
        )
        toolbar.insertWidget(2, self.both_button)
        self._spread = False

        self.front_button.clicked.connect(lambda: self._set_side("front"))
        self.back_button.clicked.connect(lambda: self._set_side("back"))
        self.both_button.toggled.connect(self._set_spread)
        self.sheet_spinbox.valueChanged.connect(self._set_sheet_index)

        self._QThread = QThread
        self._thread = None
        self._worker: PreviewWorker | None = None

        self.refresh()

    # -- navigation --------------------------------------------------------

    def _set_side(self, side: Literal["front", "back"]) -> None:
        self.side = side
        if self._spread:
            # Picking a specific side is an explicit exit from spread mode.
            self.both_button.setChecked(False)
            return
        self.refresh()

    def _set_spread(self, on: bool) -> None:
        self._spread = bool(on)
        self.front_button.setEnabled(not self._spread)
        self.back_button.setEnabled(not self._spread)
        self.refresh()

    def _set_sheet_index(self, sheet_index: int) -> None:
        self.sheet_index = sheet_index
        self.refresh()

    def set_layout_settings(self, settings) -> None:
        """Update the settings behind the content-box guide."""
        self.layout_settings = settings
        self.refresh()

    def on_layout_changed(self, plan: SheetPlan, settings=None) -> None:
        """Connected to ``LayoutPanel.layout_changed``: swap in the fresh
        whole-document plan but re-render only the sheet on screen.
        """
        self.plan = plan
        if settings is not None:
            self.layout_settings = settings
        self.sheet_spinbox.setMaximum(max(0, len(plan.sheets) - 1))
        self.refresh()

    # -- rendering -----------------------------------------------------------

    def refresh(self) -> None:
        """Kick off a background render of exactly the visible sheet/side."""
        sides = ("front", "back") if self._spread else (self.side,)
        worker = PreviewWorker(
            self.plan, self.profile, self.sheet_index, self.side, sides=sides
        )
        thread = self._QThread(self.widget)
        thread.run = worker.run
        thread.finished.connect(lambda: self._on_frame_ready(worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_frame_ready(self, worker: PreviewWorker) -> None:
        frames = worker.frames or ([worker.frame] if worker.frame else [])
        if not frames:
            return
        # Warnings are per-sheet, not per-side, so show them once.
        self.warning_label.setText(badge_text(frames[0].warnings))
        if len(frames) == 1:
            self._paint_frame(frames[0])
        else:
            self._paint_spread(frames)

    def _paint_spread(self, frames: list[PreviewFrame]) -> None:
        """Compose front and back onto one pixmap, side by side.

        Each half is painted through ``_frame_pixmap`` so the imageable-area
        guide is drawn identically to single-side mode -- the spread is a
        layout of the same rendering, not a second rendering path.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter, QPixmap

        halves = [self._frame_pixmap(f) for f in frames]
        halves = [h for h in halves if h is not None]
        if not halves:
            return
        gap = 24
        width = sum(h.width() for h in halves) + gap * (len(halves) - 1)
        height = max(h.height() for h in halves)

        canvas = QPixmap(width, height)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        try:
            x = 0
            for h in halves:
                painter.drawPixmap(x, 0, h)
                x += h.width() + gap
        finally:
            painter.end()

        self._source_pixmap = canvas
        self._apply_zoom()

    def _paint_frame(self, frame: PreviewFrame) -> None:
        pixmap = self._frame_pixmap(frame)
        if pixmap is None:
            self.image_label.clear()
            return
        # Keep the full-resolution render as the source of truth; zoom only
        # ever scales a copy, so zooming in never re-rasterizes and never
        # loses detail captured at 150 DPI.
        self._source_pixmap = pixmap
        self._apply_zoom()

    def _frame_pixmap(self, frame: PreviewFrame):
        """Rasterized sheet with the imageable-area guide drawn on it.

        Shared by single-side and spread painting so the guide can't drift
        between the two views.
        """
        QImage, QPainter, QPen, QPixmap = _qt_gui()
        QRectF = _qt_core()[1]
        rendered = frame.rendered
        if rendered.width == 0 or rendered.height == 0:
            return None

        image = QImage(rendered.rgba, rendered.width, rendered.height, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(image)

        # Two guides, drawn distinctly, because they answer different
        # questions and are routinely confused for one another:
        #
        #   solid red   -- the printer's IMAGEABLE AREA. A hardware limit;
        #                  nothing outside it can be marked at all.
        #   dashed blue -- the CONTENT BOX defined by your gutter and
        #                  margins. Content fills this on whichever axis
        #                  binds and sits inset on the other by the
        #                  aspect-ratio slack, so it will NOT touch all four
        #                  edges unless the source aspect happens to match.
        #
        # Showing only the imageable area invites the reasonable-but-wrong
        # conclusion that content should line up with it.
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor

        dpi_scale = rendered.height / self.plan.paper_pt[1] if self.plan.paper_pt[1] else 0.0
        paper_h = self.plan.paper_pt[1]

        def to_image_rect(rect_pt):
            x0, y0, x1, y1 = rect_pt
            # Flip y: PDF origin bottom-left, image origin top-left.
            return QRectF(
                x0 * dpi_scale,
                (paper_h - y1) * dpi_scale,
                (x1 - x0) * dpi_scale,
                (y1 - y0) * dpi_scale,
            )

        painter = QPainter(pixmap)
        try:
            imageable = QPen(QColor(220, 40, 40))
            imageable.setWidth(2)
            painter.setPen(imageable)
            painter.drawRect(
                to_image_rect(
                    imageable_rect_pt(self.plan.paper_pt, self.profile.imageable_area_pt)
                )
            )

            if self.layout_settings is not None:
                box = QPen(QColor(40, 110, 230))
                box.setWidth(2)
                box.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(box)
                is_recto = frame.side == "front"
                painter.drawRect(
                    to_image_rect(
                        content_box_rect_pt(self.layout_settings, is_recto=is_recto)
                    )
                )
        finally:
            painter.end()

        return pixmap

    # -- zoom ----------------------------------------------------------------

    def current_scale(self) -> float:
        """The scale actually in use, resolving fit-to-window to a number."""
        if self._source_pixmap is None:
            return self._zoom or 1.0
        if self._zoom is not None:
            return self._zoom
        viewport = self.scroll_area.viewport().size()
        return fit_scale(
            (self._source_pixmap.width(), self._source_pixmap.height()),
            (viewport.width() - 2, viewport.height() - 2),
        )

    def _set_zoom(self, zoom: float | None) -> None:
        self._zoom = zoom
        self._apply_zoom()

    def _zoom_step(self, direction: int) -> None:
        self._set_zoom(next_zoom_stop(self.current_scale(), direction))

    def _apply_zoom(self) -> None:
        from PySide6.QtCore import Qt

        if self._source_pixmap is None:
            return
        scale = self.current_scale()
        w = max(1, int(self._source_pixmap.width() * scale))
        h = max(1, int(self._source_pixmap.height() * scale))
        scaled = self._source_pixmap.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())
        self.zoom_label.setText(
            "Fit" if self._zoom is None else f"{round(scale * 100)}%"
        )
