"""What actually lands on the paper.

``QtPrintBackend._paint_rendered_page`` is the last thing that happens
before ink, and it was the least covered function in the app. It takes a
rasterised sheet and decides where on the physical page it goes. A mistake
here is invisible everywhere else: the preview is correct, the exported PDF
is correct, and the printed sheet is wrong.

It is also easy to test despite being Qt code, because it touches its
collaborators through four calls only -- ``printer.resolution()``,
``printer.width()``, ``printer.height()`` and ``painter.drawImage()``. Fakes
record what it asked for, in device pixels, and the assertions are about
millimetres of paper rather than about Qt.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.printing import PrinterProfile  # noqa: E402
from deckle.core.render import RenderedPage  # noqa: E402

LETTER_PT = (612.0, 792.0)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _FakePrinter:
    """Only the three things the painter asks a printer."""

    def __init__(self, dpi: int = 600, paper_pt=LETTER_PT):
        self._dpi = dpi
        self._w = int(paper_pt[0] * dpi / 72.0)
        self._h = int(paper_pt[1] * dpi / 72.0)

    def resolution(self) -> int:
        return self._dpi

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h


class _RecordingPainter:
    """Records the one drawImage call, in device pixels."""

    def __init__(self):
        self.calls = []

    def drawImage(self, x, y, image):
        self.calls.append({"x": x, "y": y, "w": image.width(), "h": image.height()})


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


def _sheet(width_px: int = 612, height_px: int = 792) -> RenderedPage:
    return RenderedPage(
        width=width_px,
        height=height_px,
        rgba=bytes([255, 255, 255, 255]) * (width_px * height_px),
    )


def _paint(profile, printer, rendered, dpi: int = 72):
    """Paint one sheet and report the draw calls.

    ``dpi`` is the resolution ``rendered`` was rasterised at; the default
    matches ``_sheet()``, whose 612x792 pixels are a letter sheet at 72.
    """
    from deckle.app.backend import QtPrintBackend

    backend = QtPrintBackend(profile)
    painter = _RecordingPainter()
    backend._paint_rendered_page(painter, printer, rendered, dpi)
    return painter.calls


# -- placement -----------------------------------------------------------


def test_the_sheet_is_printed_at_actual_size():
    """B6, settled 2026-09-08: an inch of the design is an inch of paper.

    This file previously pinned the opposite as an open question. The whole
    sheet was scaled into the profile's imageable area, so on a printer
    with 18pt margins a letter sheet printed at about 94% of its designed
    size and every measurement in the finished book came out ~6% short.
    Boards are cut to measured dimensions and the spine width in the
    schedule is computed from paper thickness, so a text block 6% smaller
    than designed is one the case will not close on.
    """
    profile = _profile(imageable_area_pt=(18.0,) * 4)
    printer = _FakePrinter(dpi=600, paper_pt=LETTER_PT)

    (call,) = _paint(profile, printer, _sheet(612, 792), dpi=72)

    # 612pt wide rasterised at 72dpi, laid down at 600: 8.5 inches either way.
    assert call["w"] == 612 * 600 // 72
    assert call["h"] == 792 * 600 // 72
    assert call["w"] / printer.width() == pytest.approx(1.0, abs=1e-3)


def test_the_sheet_starts_at_the_corner_of_the_paper():
    """`setFullPage(True)` puts the origin on the physical corner, so the
    design's own margins are the only margins. Insetting by the printer's
    border on top of them is what made the sheet small."""
    printer = _FakePrinter(dpi=600)

    (call,) = _paint(_profile(imageable_area_pt=(18.0,) * 4), printer, _sheet())

    assert (call["x"], call["y"]) == (0, 0)


def test_the_imageable_area_no_longer_moves_or_resizes_anything():
    """At actual size the printer's border is a fact to warn about, not a
    box to shrink into -- Deckle raises `clipped_by_imageable_area` and
    draws the border in the preview instead. Any profile must paint the
    same sheet in the same place."""
    printer = _FakePrinter(dpi=600, paper_pt=LETTER_PT)
    generous = _profile(imageable_area_pt=(0.0, 0.0, 0.0, 0.0))
    mean = _profile(imageable_area_pt=(12.0, 48.0, 24.0, 36.0))

    (a,) = _paint(generous, printer, _sheet(), dpi=72)
    (b,) = _paint(mean, printer, _sheet(), dpi=72)

    assert a == b


def test_the_aspect_ratio_is_the_designs_whatever_the_borders_are():
    """The more visible half of the old behaviour, now gone.

    `QImage.scaled(w, h)` ignores aspect ratio by default, and real
    printers have a larger border on the feed edge -- so an asymmetric
    profile used to stretch the printed sheet out of the design's shape.
    Scaling by a single ratio cannot do that.
    """
    profile = _profile(imageable_area_pt=(12.0, 48.0, 12.0, 12.0))
    printer = _FakePrinter(dpi=600, paper_pt=LETTER_PT)

    (call,) = _paint(profile, printer, _sheet(612, 792), dpi=72)

    assert call["w"] / call["h"] == pytest.approx(612 / 792, abs=1e-3)


def test_the_placement_scales_with_the_printers_resolution():
    """The same sheet at a different device DPI is the same physical size."""
    profile = _profile(imageable_area_pt=(18.0,) * 4)

    (low,) = _paint(profile, _FakePrinter(dpi=300), _sheet(), dpi=72)
    (high,) = _paint(profile, _FakePrinter(dpi=600), _sheet(), dpi=72)

    assert high["w"] == pytest.approx(low["w"] * 2, abs=2)
    assert high["h"] == pytest.approx(low["h"] * 2, abs=2)


def test_a_sheet_rasterised_at_the_device_dpi_is_painted_one_to_one():
    """The common path: render at the printer's own resolution and the
    transform is the identity, so no resampling touches the ink."""
    printer = _FakePrinter(dpi=300, paper_pt=LETTER_PT)
    px_w, px_h = int(612 * 300 / 72), int(792 * 300 / 72)

    (call,) = _paint(_profile(), printer, _sheet(px_w, px_h), dpi=300)

    assert (call["w"], call["h"]) == (px_w, px_h)


def test_a_zero_size_page_paints_nothing():
    """An absent side is a degenerate RenderedPage, not an error."""
    calls = _paint(_profile(), _FakePrinter(), RenderedPage(width=0, height=0, rgba=b""))

    assert calls == []


def test_a_nonsense_imageable_area_cannot_produce_a_bad_target():
    """Calibration is user-supplied and the wizard does not exist yet, so a
    profile with margins larger than the paper is reachable by hand. It no
    longer reaches the paint transform at all, but the guarantee is worth
    keeping stated."""
    profile = _profile(imageable_area_pt=(500.0, 500.0, 500.0, 500.0))

    (call,) = _paint(profile, _FakePrinter(dpi=300), _sheet(), dpi=72)

    assert call["w"] >= 1
    assert call["h"] >= 1
