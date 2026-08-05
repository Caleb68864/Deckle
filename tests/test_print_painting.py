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


def _paint(profile, printer, rendered):
    from deckle.app.backend import QtPrintBackend

    backend = QtPrintBackend(profile)
    painter = _RecordingPainter()
    backend._paint_rendered_page(painter, printer, rendered)
    return painter.calls


# -- placement -----------------------------------------------------------


def test_the_sheet_is_inset_by_the_printers_imageable_margins():
    """``setFullPage(True)`` means the painter's origin is the paper corner,
    including the border no printer can reach. The inset is what keeps
    content out of it."""
    profile = _profile(imageable_area_pt=(18.0, 18.0, 18.0, 18.0))
    printer = _FakePrinter(dpi=600)

    (call,) = _paint(profile, printer, _sheet())

    # 18pt at 600dpi is 150 device pixels.
    assert call["x"] == 150
    assert call["y"] == 150


def test_an_asymmetric_imageable_area_is_honoured_per_edge():
    """Real printers do not have equal borders -- the edge that feeds
    usually has the largest."""
    profile = _profile(imageable_area_pt=(12.0, 36.0, 24.0, 48.0))
    printer = _FakePrinter(dpi=300)

    (call,) = _paint(profile, printer, _sheet())

    assert call["x"] == int(12.0 * 300 / 72)  # left
    assert call["y"] == int(36.0 * 300 / 72)  # top
    # Width loses left+right, height loses top+bottom.
    assert call["w"] == int(printer.width() - (12.0 + 24.0) * 300 / 72)
    assert call["h"] == int(printer.height() - (36.0 + 48.0) * 300 / 72)


def test_the_placement_scales_with_the_printers_resolution():
    """The same paper geometry at a different DPI must land in the same
    physical place."""
    profile = _profile(imageable_area_pt=(18.0,) * 4)

    (low,) = _paint(profile, _FakePrinter(dpi=300), _sheet())
    (high,) = _paint(profile, _FakePrinter(dpi=600), _sheet())

    # Twice the resolution, twice the pixels, same position on paper.
    assert high["x"] == pytest.approx(low["x"] * 2, abs=1)
    assert high["w"] == pytest.approx(low["w"] * 2, abs=2)


def test_a_zero_size_page_paints_nothing():
    """An absent side is a degenerate RenderedPage, not an error."""
    calls = _paint(_profile(), _FakePrinter(), RenderedPage(width=0, height=0, rgba=b""))

    assert calls == []


def test_an_imageable_area_larger_than_the_paper_does_not_paint_backwards():
    """A profile with nonsense margins must not produce a negative target.

    Calibration is user-supplied and the wizard does not exist yet, so a
    bad profile is reachable today by hand-editing one.
    """
    profile = _profile(imageable_area_pt=(500.0, 500.0, 500.0, 500.0))

    (call,) = _paint(profile, _FakePrinter(dpi=300), _sheet())

    assert call["w"] >= 1
    assert call["h"] >= 1


# -- the question this file exists to make visible -----------------------


def test_the_sheet_is_scaled_to_the_imageable_area_not_printed_true_size():
    """Pins a real design decision so it is a choice rather than an accident.

    The rasterised sheet is the WHOLE sheet -- 612x792pt for letter, with
    the imposer's margins already inside it. Painting it into the imageable
    area scales it down by the border: on a printer with 18pt margins, a
    letter sheet prints at about 94% of its designed size, and every
    measurement in the finished book is 6% short.

    The alternative is drawing it 1:1 at the paper origin and letting the
    printer's border clip whatever falls in it -- true size, with a warning
    when content is inside the border, which Deckle already computes
    (``clipped_by_imageable_area``).

    Which is right is a product decision, not a bug to fix quietly. For
    bookbinding it matters: boards are cut to measured dimensions, so a
    text block 6% smaller than designed is a text block that does not fit
    the case made for it. Recorded here with numbers so the decision can be
    made deliberately.
    """
    profile = _profile(imageable_area_pt=(18.0,) * 4)
    printer = _FakePrinter(dpi=600, paper_pt=LETTER_PT)

    (call,) = _paint(profile, printer, _sheet())

    printed_scale = call["w"] / printer.width()
    assert printed_scale == pytest.approx(1 - 36.0 / 612.0, abs=1e-3), (
        "the full sheet is scaled into the printable area"
    )
    # Stated plainly: about 94%, i.e. ~6% smaller than designed.
    assert 0.93 < printed_scale < 0.95


def test_the_aspect_ratio_changes_when_the_borders_are_asymmetric():
    """A consequence of the same decision, and the more visible one.

    ``QImage.scaled(w, h)`` defaults to ignoring aspect ratio. With equal
    margins the distortion is nil, but real printers have a larger border
    on the feed edge, and then the printed sheet is stretched relative to
    the design.
    """
    profile = _profile(imageable_area_pt=(12.0, 48.0, 12.0, 12.0))
    printer = _FakePrinter(dpi=600, paper_pt=LETTER_PT)

    (call,) = _paint(profile, printer, _sheet(612, 792))

    sheet_aspect = 612 / 792
    printed_aspect = call["w"] / call["h"]
    assert printed_aspect != pytest.approx(sheet_aspect, abs=1e-3), (
        "with asymmetric margins the printed sheet is not the design's shape"
    )
