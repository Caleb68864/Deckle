"""Telling the printer what paper the plan is for.

Deckle draws its own print dialog, so no ``QPrintDialog`` ever asks the
user for a page size, and until this file existed nothing in the print
path called ``setPageSize``, ``setPageLayout`` or built a ``QPageSize``
at all. Every job went to whatever page the driver happened to default
to -- usually A4 or Letter, always portrait.

Before B6 (settled 2026-09-08) that was survivable: the painter fitted
the rasterised sheet into ``printer.width()``, so a mismatch came out
silently *scaled*. B6 made the painter draw one inch of design as one
inch of paper, which is right, and in exchange a mismatch now comes out
silently *clipped*. Folio is the case that bites: ``SaddleStitchStrategy``
warns when the paper is portrait because folio is meant to run on
landscape stock, and a default ``QPrinter`` is portrait -- so a
letter-landscape plan on a default printer loses 180pt, two and a half
inches, off its width.

Neither clip warning can see it. ``clipped_by_imageable_area`` and
``clipped_by_page`` both compute in ``plan.paper_pt`` coordinates and
assume the device page *is* the plan's paper. Making that assumption true
is what these tests pin.

The assertions are about paper, in points, read back off the
``QPageLayout`` the backend handed the printer.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app import backend as backend_mod  # noqa: E402
from deckle.app.backend import QtPrintBackend  # noqa: E402
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side  # noqa: E402
from deckle.core.profiles import PrinterProfile  # noqa: E402

LETTER_PORTRAIT = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)
A4_PORTRAIT = (595.0, 842.0)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _profile(**over) -> PrinterProfile:
    base = dict(
        version=1, flip_axis="long", output_face="down", feed_edge="top",
        reverse_stack=True, imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00", calibration_version=1,
    )
    base.update(over)
    return PrinterProfile(**base)


def _plan(paper, n_sheets: int = 1) -> SheetPlan:
    page = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    sheets = [
        Sheet(index=i, front=Side(pages=(page,)), back=Side(pages=(page,)))
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=paper, warnings=[])


class _RecordingPrinter:
    """A ``QPrinter`` stand-in that records the page layout it was given.

    Deliberately *not* a Mock: an attribute this printer does not have
    should be an ``AttributeError`` naming it, not a silently-recorded
    call to something ``QPrinter`` cannot do.
    """

    def __init__(self):
        self.layouts = []
        self.page_sizes = []
        self.full_page = None
        self.pages_painted = 0

    def setPrinterName(self, name):  # noqa: N802 - Qt naming
        self.name = name

    def setCopyCount(self, count):  # noqa: N802 - Qt naming
        self.copies = count

    def setFullPage(self, on):  # noqa: N802 - Qt naming
        self.full_page = on

    def setPageLayout(self, layout):  # noqa: N802 - Qt naming
        self.layouts.append(layout)
        return True

    def setPageSize(self, size):  # noqa: N802 - Qt naming
        self.page_sizes.append(size)
        return True

    def setDuplex(self, mode):  # noqa: N802 - Qt naming
        self.duplex = mode

    def newPage(self):  # noqa: N802 - Qt naming
        self.pages_painted += 1
        return True

    def resolution(self):
        return 300


class _RecordingPainter:
    def __init__(self):
        self.drawn = 0

    def begin(self, printer):
        self.begun = printer
        return True

    def end(self):
        return None

    def drawImage(self, *args):  # noqa: N802 - Qt naming
        self.drawn += 1


@pytest.fixture
def rig(monkeypatch):
    """Stub Qt around the *real* ``_submit_chunk`` / ``submit_duplex``.

    The printer, the painter and the rasteriser are stubs; the code that
    decides what paper to ask for is the production code. A rig that
    re-implemented that decision would agree with itself forever.
    """
    printers = []
    rendered = []

    def new_printer():
        printers.append(_RecordingPrinter())
        return printers[-1]

    painter = _RecordingPainter()

    def fake_render(plan, sheet_index, side, dpi, rotate_backs, ignore_rotate,
                    back_offset_pt=(0.0, 0.0)):
        rendered.append((sheet_index, side))
        return backend_mod.RenderedPage(width=1, height=1, rgba=b"\x00\x00\x00\x00")

    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)
    monkeypatch.setattr(backend_mod, "_new_qprinter", new_printer)
    monkeypatch.setattr(backend_mod, "_new_qpainter", lambda: painter)
    monkeypatch.setattr(backend_mod, "_duplex_auto_mode", lambda: "auto")
    monkeypatch.setattr(backend_mod, "_render_sheet_side", fake_render)
    monkeypatch.setattr(backend_mod, "_render_ruled_sheet_side",
                        lambda plan, sheet_index, side, dpi: fake_render(
                            plan, sheet_index, side, dpi, False, False))
    monkeypatch.setattr(backend_mod, "log_print_job", lambda *a, **k: None)
    monkeypatch.setattr(QtPrintBackend, "_paint_rendered_page",
                        lambda self, p, pr, r, dpi: None)
    return {"printers": printers, "painter": painter, "rendered": rendered}


def _paper_asked_for(printer) -> tuple[int, int]:
    """The oriented page, in points, that ``printer`` was told to use."""
    assert printer.layouts, (
        "the backend never called setPageLayout -- nothing told this "
        "QPrinter what paper the plan is for"
    )
    rect = printer.layouts[-1].fullRectPoints()
    return (rect.width(), rect.height())


# -- the manual (two-pass) path ------------------------------------------


def test_the_printer_is_told_the_plans_paper(rig):
    backend = QtPrintBackend(_profile())

    backend._submit_chunk(
        _plan(LETTER_PORTRAIT), [0], "Laser", 1, 72, "front", False,
    )

    # The rig actually reached the subject, so a later assertion about the
    # page size is about this call and not about an empty list.
    assert rig["rendered"] == [(0, "front")]
    (printer,) = rig["printers"]
    assert _paper_asked_for(printer) == (612, 792)


def test_a_landscape_plan_asks_for_landscape_paper(rig):
    """The folio case, and the whole reason this matters.

    A letter-landscape plan on a portrait device page loses 180pt --
    2.5in -- off its width, and B6's 1:1 painter clips it rather than
    shrinking it. The page must turn with the plan.
    """
    backend = QtPrintBackend(_profile())

    backend._submit_chunk(
        _plan(LETTER_LANDSCAPE), [0], "Laser", 1, 72, "front", False,
    )

    assert rig["rendered"] == [(0, "front")]
    (printer,) = rig["printers"]
    assert _paper_asked_for(printer) == (792, 612), (
        "a folio plan was sent to a portrait page; 2.5in of the sheet "
        "would be clipped off the right-hand edge"
    )


def test_a4_and_letter_are_not_interchangeable(rig):
    """17pt is only a quarter of an inch, and it is still ink on the floor."""
    backend = QtPrintBackend(_profile())

    backend._submit_chunk(_plan(A4_PORTRAIT), [0], "Laser", 1, 72, "front", False)

    (printer,) = rig["printers"]
    assert _paper_asked_for(printer) == (595, 842)


def test_the_page_is_still_full_page(rig):
    """Setting a layout must not undo ``setFullPage(True)``: the painter's
    origin is the physical corner of the paper, which is what makes
    ``(0, 0)`` mean the corner in ``_paint_rendered_page``."""
    backend = QtPrintBackend(_profile())

    backend._submit_chunk(_plan(LETTER_PORTRAIT), [0], "Laser", 1, 72, "front", False)

    (printer,) = rig["printers"]
    assert printer.full_page is True


def test_a_proof_measures_the_plans_paper_too(rig):
    """The proof exists to be measured with a ruler. Printing it on a
    different page than the job would measure the wrong thing."""
    backend = QtPrintBackend(_profile())

    result = backend.submit_proof(_plan(LETTER_LANDSCAPE, 3), 1, "Laser", 300)

    assert result.error is None
    (printer,) = rig["printers"]
    assert _paper_asked_for(printer) == (792, 612)


# -- the hardware-duplex path --------------------------------------------


def test_the_duplex_path_asks_for_the_same_paper(rig):
    """``submit_duplex`` has no production caller (see
    ``test_duplex_submission.py``), and the reason to keep it correct is
    the same reason the registration offset was added to it: a defect
    parked in unreached code is a defect the day something reaches it."""
    backend = QtPrintBackend(_profile())

    backend.submit_duplex(
        plan=_plan(LETTER_LANDSCAPE, 2), sheets=[0, 1],
        printer_name="Laser", copies=1, dpi=72,
    )

    assert len(rig["rendered"]) == 4
    (printer,) = rig["printers"]
    assert _paper_asked_for(printer) == (792, 612)


# -- when the printer cannot do it ---------------------------------------


def test_a_refused_page_size_is_logged_rather_than_swallowed(rig, monkeypatch):
    """A printer with a fixed paper list can refuse. Qt says so only
    through ``setPageLayout``'s return value, which is the kind of answer
    that gets dropped. The job still goes -- refusing to print is worse
    than printing on the wrong paper -- but it is recorded."""
    events = []
    monkeypatch.setattr(backend_mod, "log_event",
                        lambda name, **kw: events.append((name, kw)))

    backend = QtPrintBackend(_profile())

    def refuse(layout):
        return False

    def new_printer():
        printer = _RecordingPrinter()
        printer.setPageLayout = refuse
        rig["printers"].append(printer)
        return printer

    monkeypatch.setattr(backend_mod, "_new_qprinter", new_printer)

    backend._submit_chunk(_plan(LETTER_LANDSCAPE), [0], "Laser", 1, 72, "front", False)

    assert rig["rendered"] == [(0, "front")], "the rig never reached the subject"
    names = [name for name, _ in events]
    assert "print_paper_size_refused" in names, names
