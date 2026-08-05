"""QtPrintBackend: the only Qt code outside the UI.

Implements the ``PrintBackend`` Protocol (``deckle.core.printing``) by
rasterizing each output page at the printer's own device DPI via
``deckle.core.render.render_sheet`` and painting it into a ``QPainter`` at
an exact device-space rectangle. ``QPrinter.setFullPage(True)`` is always
set, so Qt applies no margin of its own -- margins come from the profile's
``imageable_area_pt`` instead. Transform control guarantees fidelity
*within* the imageable area; it cannot defeat the printer's physical
non-printable border (see ``docs/spikes/qprinter-capability-report.md``).

Tray selection is deliberately never touched -- it is effectively
Windows-only and manual duplex does not need it.

Jobs are submitted in bounded chunks (default 10 sheets) so a failure
during a long run loses at most one chunk, not the whole pass. A failed
chunk cancels the remaining chunks of that pass -- submission does not
continue into a jammed or offline printer.

``rotate_backs`` on a ``PrintPass`` is honored by rotating every back-side
page 180 degrees with pikepdf's ``page.rotate(180, relative=True)`` (never
by assigning ``page.Rotate`` directly) before rasterizing it, falling back
to ``page.flatten_rotation()`` in addition for drivers known to ignore the
PDF ``/Rotate`` key.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Literal, Sequence

import pikepdf
import pypdfium2 as pdfium

from deckle.core import export
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import SheetPlan
from deckle.core.printing import PrintPass, PrintResult
from deckle.core.profiles import PrinterProfile
from deckle.core.render import RenderedPage, render_sheet

try:
    from deckle.core.session_log import log_print_job
except ImportError:  # pragma: no cover - SS-07 (persistence/log) not yet landed
    def log_print_job(
        printer: str,
        profile: PrinterProfile,
        sheets: Sequence[int],
        dpi: int,
        pass_index: int,
    ) -> None:
        """Fallback no-op used only until ``deckle.core.session_log`` exists.

        Mirrors the ``log_print_job`` shape from SS-07 exactly so callers
        never have to change once the real module lands.
        """
        return None


# Default bound on sheets submitted as a single Qt print job. Chunking
# bounds the blast radius of a mid-job failure to one chunk, not a ream.
DEFAULT_CHUNK_SIZE = 10

# Print drivers known to silently ignore a PDF's /Rotate key. The spike
# report (docs/spikes/qprinter-capability-report.md) is the source of
# truth for entries here; start empty/conservative and only add a driver
# once divergence has actually been observed and reviewed.
DRIVERS_IGNORING_ROTATE: frozenset[str] = frozenset()


# PySide6 is imported lazily, only inside the functions below that
# actually touch Qt -- deckle.app is allowed to depend on Qt (unlike
# deckle.core, see tests/test_core_purity.py), but merely importing this
# module (or exercising the pure chunking/rotation helpers, which the
# test suite does directly) should not pull PySide6 into sys.modules.
def _new_qprinter():
    from PySide6.QtPrintSupport import QPrinter

    return QPrinter(QPrinter.PrinterMode.HighResolution)


def _qimage(rgba: bytes, width: int, height: int):
    from PySide6.QtGui import QImage

    return QImage(rgba, width, height, QImage.Format.Format_RGBA8888)


def _new_qpainter():
    from PySide6.QtGui import QPainter

    return QPainter()


def _printer_info(printer_name: str):
    from PySide6.QtPrintSupport import QPrinterInfo

    return QPrinterInfo.printerInfo(printer_name)


def printer_is_available(printer_name: str) -> bool:
    """Whether the OS still knows about ``printer_name``.

    A printer can vanish between the moment it was enumerated and the
    moment a job is submitted to it -- unplugged, removed from the system,
    or a network queue that went away while the user was busy reloading
    paper for the back pass. Deckle's manual-duplex flow makes that window
    unusually wide: minutes of physical work sit between pass 1 and pass 2.

    Qt surfaces the disappearance only as a failure inside
    ``QPainter.begin()``, by which point a chunk is already half set up and
    the error text says nothing about the printer being gone. Checking
    first turns that into a specific, actionable message.

    :param printer_name: the printer to check. An empty name means "the
        system default", which is not ours to second-guess.
    :returns: True if the printer exists, or if the check itself could not
        be made -- an unavailable *check* must never block a print the user
        asked for.
    """
    if not printer_name:
        return True
    try:
        info = _printer_info(printer_name)
        return not info.isNull()
    except Exception as exc:  # noqa: BLE001 -- inconclusive, not fatal
        log_exception("printer_availability_check_failed", exc, printer=printer_name)
        return True


def _duplex_none_mode():
    from PySide6.QtPrintSupport import QPrinter

    return QPrinter.DuplexMode.DuplexNone


def _duplex_auto_mode():
    from PySide6.QtPrintSupport import QPrinter

    return QPrinter.DuplexMode.DuplexAuto


def _chunked(items: Sequence[int], size: int) -> list[list[int]]:
    """Split ``items`` into consecutive chunks of at most ``size``."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _apply_rotate_backs(pdf_path: str, ignore_rotate: bool) -> None:
    """Rotate every page in ``pdf_path`` 180 degrees, in place.

    Uses ``page.rotate(180, relative=True)`` -- never a direct assignment
    to the page's rotation key, which older qpdf has mishandled. When
    ``ignore_rotate`` is True (the target driver is known to ignore
    ``/Rotate``), also bakes the rotation into content with
    ``page.flatten_rotation()`` so the driver cannot discard it.
    """
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            page.rotate(180, relative=True)
            if ignore_rotate:
                page.flatten_rotation()
        pdf.save(pdf_path)


def _render_sheet_side(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int,
    rotate_backs: bool,
    ignore_rotate: bool,
) -> RenderedPage:
    """Render one side of one sheet, applying back-side rotation if asked.

    Delegates to ``render_sheet`` (the shared export-then-rasterize path)
    for the common case. When rotation is required this exports the
    single-sheet PDF itself so pikepdf can rotate the page before pdfium
    rasterizes it -- ``render_sheet`` has no rotation hook.
    """
    if not (side == "back" and rotate_backs):
        return render_sheet(plan, sheet_index, side, dpi)

    by_index = {sheet.index: sheet for sheet in plan.sheets}
    sheet = by_index.get(sheet_index)
    has_front = sheet is not None and sheet.front is not None
    has_back = sheet is not None and sheet.back is not None
    if not has_back:
        return RenderedPage(width=0, height=0, rgba=b"")

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        export.export(plan, tmp_path, sheets=[sheet_index])
        _apply_rotate_backs(tmp_path, ignore_rotate)

        page_index = 1 if has_front else 0
        pdf = pdfium.PdfDocument(tmp_path)
        try:
            if page_index >= len(pdf):
                return RenderedPage(width=0, height=0, rgba=b"")
            page = pdf[page_index]
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil().convert("RGBA")
            width, height = pil_image.size
            return RenderedPage(width=width, height=height, rgba=pil_image.tobytes())
        finally:
            pdf.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@dataclass(frozen=True)
class DuplexModes:
    """Duplex options a given printer can offer for a pass.

    :ivar manual: always ``True``. Deckle's two-pass reload flow needs no
        hardware support -- it exists for printers that have none.
    :ivar single_pass_duplex: whether the printer reports a real duplexer.
        Offered alongside manual duplex because a user who *does* own a
        duplexer should not be worse off for using Deckle.
    """

    manual: bool
    single_pass_duplex: bool


class QtPrintBackend:
    """Implements ``PrintBackend`` by painting rasterized sheets via Qt.

    ``profile`` supplies the imageable area (never Qt's own default
    margins) and is what gets logged with every submitted chunk.

    :param profile: the calibrated printer profile.
    :param chunk_size: sheets per Qt print job. Chunking bounds the blast
        radius of a mid-job failure to one chunk, not a ream.
    :param drivers_ignoring_rotate: printer names known to discard a PDF's
        ``/Rotate`` key, for which rotation is baked into the content
        instead. Deliberately empty by default -- a driver is added only
        once divergence has actually been observed.
    :ivar submitted_sheets: sheet indices the last pass definitely got to
        the spooler.
    :ivar uncertain_sheets: the sheets in the chunk that failed. Genuinely
        unknown: a chunk can fail on its first sheet or its last, and
        telling a user they printed is how a reprint comes out with holes
        in it. ``PrintResult`` carries only a count, and
        ``deckle.core.printing`` is a frozen seam, so the indices live here.
    :ivar unsubmitted_sheets: sheets never sent, because the failure
        cancelled the rest of the pass.
    """

    def __init__(
        self,
        profile: PrinterProfile,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        drivers_ignoring_rotate: frozenset[str] = DRIVERS_IGNORING_ROTATE,
    ) -> None:
        self.profile = profile
        self.chunk_size = chunk_size
        self._drivers_ignoring_rotate = drivers_ignoring_rotate

        # What the last submit_pass()/submit_duplex() call actually got as
        # far as. Paper cannot be read backwards: when a run dies partway
        # the user is holding a stack and cannot tell which sheets made it,
        # and PrintResult carries only a count (deckle.core.printing is a
        # frozen seam). These carry the indices.
        self.submitted_sheets: list[int] = []
        self.uncertain_sheets: list[int] = []
        self.unsubmitted_sheets: list[int] = []

    # -- PrintBackend Protocol -------------------------------------------------

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        *,
        side: Literal["front", "back"] = "front",
        rotate_backs: bool = False,
        pass_index: int = 0,
    ) -> PrintResult:
        """Submit ``sheets`` as a single Qt print job (one chunk).

        ``side``/``rotate_backs``/``pass_index`` are additional keyword-only
        parameters beyond the ``PrintBackend`` Protocol's required
        ``(plan, sheets, printer_name, copies, dpi)`` shape -- callers that
        only know the Protocol (SS-11/SS-12 submitting a single front pass)
        can omit them and get front-side, unrotated behavior.

        A printer that has disappeared since it was chosen is reported as
        such before anything is painted, rather than as whatever Qt says
        when ``QPainter.begin()`` fails on a queue that no longer exists.

        :param plan: the imposed sheets.
        :param sheets: the sheet indices in this chunk, in submission order.
        :param printer_name: the target queue. Empty means the system
            default, which is not ours to second-guess.
        :param copies: copies of the chunk.
        :param dpi: rasterization resolution.
        :param side: which physical face to paint.
        :param rotate_backs: whether back sides need a 180-degree turn to
            land right side up, per the profile's flip axis.
        :param pass_index: recorded in the session log and diagnostics.
        :returns: a :class:`~deckle.core.printing.PrintResult`. **Never
            raises for a print failure** -- offline, out of paper, driver
            rejection and a printer removed mid-run all come back as
            ``PrintResult.error``, because the caller's job is to offer a
            resume rather than to unwind a stack.
        """
        if not printer_is_available(printer_name):
            error = f"printer {printer_name!r} is no longer available"
            log_event(
                "print_printer_unavailable",
                level=logging.WARNING,
                printer=printer_name,
                sheets=list(sheets),
                side=side,
                pass_index=pass_index,
            )
            return PrintResult(submitted=0, job_id=None, error=error)

        try:
            self._submit_chunk(plan, sheets, printer_name, copies, dpi, side, rotate_backs)
        except Exception as exc:  # noqa: BLE001 - reported back as PrintResult.error
            # Offline, out of paper, driver rejection, printer removed
            # mid-run: Qt reports all of them the same way, as an exception
            # from begin()/newPage(). The distinction lives in the message,
            # so record it -- along with which sheets were in flight, which
            # is the part the paper cannot tell you afterwards.
            log_exception(
                "print_chunk_failed",
                exc,
                printer=printer_name,
                sheets=list(sheets),
                side=side,
                pass_index=pass_index,
                copies=copies,
                dpi=dpi,
            )
            return PrintResult(submitted=0, job_id=None, error=str(exc))

        log_print_job(printer_name, self.profile, sheets, dpi, pass_index)
        return PrintResult(submitted=len(sheets), job_id=None, error=None)

    def submit_pass(
        self,
        plan: SheetPlan,
        print_pass: PrintPass,
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult:
        """Submit an entire ``PrintPass`` in bounded chunks.

        A failed chunk cancels the remaining chunks of the pass rather
        than continuing to submit into a jammed or offline printer. The
        returned ``PrintResult.submitted`` is the count actually
        submitted before the failure (or the full pass on success).

        A partial run also records **which** sheets got where, on
        :attr:`submitted_sheets`, :attr:`uncertain_sheets` and
        :attr:`unsubmitted_sheets`, and in the diagnostic log. The
        three-way split is not pedantry: a chunk that fails may have failed
        on its first sheet or its last, so its sheets are genuinely
        unknown, and telling a user they printed is how a reprint comes out
        with holes in it.

        :param plan: the imposed sheets.
        :param print_pass: the pass to submit, supplying sheet order, side
            and ``rotate_backs``. None of that is recomputed here.
        :param printer_name: the target queue.
        :param copies: copies per chunk.
        :param dpi: rasterization resolution.
        :returns: a ``PrintResult`` whose ``submitted`` is the count
            actually submitted before any failure. A failure is reported
            through ``error``, never raised.
        """
        chunks = _chunked(print_pass.sheet_order, self.chunk_size)
        self.submitted_sheets = []
        self.uncertain_sheets = []
        self.unsubmitted_sheets = list(print_pass.sheet_order)
        submitted_total = 0
        for position, chunk in enumerate(chunks):
            result = self.submit(
                plan,
                chunk,
                printer_name,
                copies,
                dpi,
                side=print_pass.side,
                rotate_backs=print_pass.rotate_backs,
                pass_index=print_pass.index,
            )
            if result.error is not None:
                self.uncertain_sheets = list(chunk)
                remaining = [s for c in chunks[position + 1 :] for s in c]
                self.unsubmitted_sheets = remaining
                log_event(
                    "print_pass_incomplete",
                    level=logging.WARNING,
                    printer=printer_name,
                    pass_index=print_pass.index,
                    side=print_pass.side,
                    error=result.error,
                    submitted_sheets=list(self.submitted_sheets),
                    uncertain_sheets=list(chunk),
                    unsubmitted_sheets=remaining,
                )
                return PrintResult(submitted=submitted_total, job_id=None, error=result.error)
            self.submitted_sheets.extend(chunk)
            submitted_total += result.submitted
        self.unsubmitted_sheets = []
        return PrintResult(submitted=submitted_total, job_id=None, error=None)

    # -- internals ---------------------------------------------------------

    def _submit_chunk(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        side: Literal["front", "back"],
        rotate_backs: bool,
    ) -> None:
        printer = _new_qprinter()
        if printer_name:
            printer.setPrinterName(printer_name)
        printer.setCopyCount(max(1, copies))
        # Margins come from the profile's imageable_area_pt, never Qt's
        # own defaults -- setFullPage(True) is what makes that true.
        printer.setFullPage(True)

        painter = _new_qpainter()
        if not painter.begin(printer):
            raise RuntimeError(f"could not begin painting on printer {printer_name!r}")
        try:
            for i, sheet_index in enumerate(sheets):
                if i > 0:
                    printer.newPage()
                rendered = _render_sheet_side(
                    plan,
                    sheet_index,
                    side,
                    dpi,
                    rotate_backs,
                    printer_name in self._drivers_ignoring_rotate,
                )
                self._paint_rendered_page(painter, printer, rendered)
        finally:
            painter.end()

    def _paint_rendered_page(self, painter, printer, rendered: RenderedPage) -> None:
        if rendered.width == 0 or rendered.height == 0:
            return
        image = _qimage(rendered.rgba, rendered.width, rendered.height)
        left_pt, top_pt, right_pt, bottom_pt = self.profile.imageable_area_pt
        dpi_scale = printer.resolution() / 72.0
        target_x = left_pt * dpi_scale
        target_y = top_pt * dpi_scale
        target_w = max(0.0, printer.width() - (left_pt + right_pt) * dpi_scale)
        target_h = max(0.0, printer.height() - (top_pt + bottom_pt) * dpi_scale)
        painter.drawImage(
            int(target_x),
            int(target_y),
            image.scaled(int(target_w) or 1, int(target_h) or 1),
        )

    # -- duplex -------------------------------------------------------------

    def duplex_modes(self, printer_name: str) -> DuplexModes:
        """The duplex options available for ``printer_name``.

        Manual duplex (Deckle's two-pass reload flow) is always offered --
        Deckle exists for duplexer-less printers. When
        ``QPrinterInfo.supportedDuplexModes()`` reports real hardware
        duplex capability, a single-pass duplex mode is offered alongside
        it: a printer that already has a duplexer should not be *worse*
        off using Deckle than using its own driver directly, and the user
        may not own this printer forever.

        :param printer_name: the printer to ask about.
        :returns: the available modes. A printer Qt does not recognise
            reports manual duplex only, which is the safe direction.
        """
        info = _printer_info(printer_name)
        supported = set(info.supportedDuplexModes()) if not info.isNull() else set()
        hardware_duplex = bool(supported - {_duplex_none_mode()})
        return DuplexModes(manual=True, single_pass_duplex=hardware_duplex)

    def submit_duplex(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult:
        """Submit fronts and backs as one hardware-duplex Qt job.

        Only used when ``duplex_modes(printer_name).single_pass_duplex``
        is True. Chunking still applies -- one job per chunk of sheets,
        each job interleaving front/back pages for QPrinter's own duplex
        unit to reassemble.

        Tracks the same submitted/uncertain/unsubmitted split as
        :meth:`submit_pass`.

        :param plan: the imposed sheets.
        :param sheets: the sheet indices to print.
        :param printer_name: the target queue, which must actually have a
            duplexer -- check :meth:`duplex_modes` first.
        :param copies: copies per chunk.
        :param dpi: rasterization resolution.
        :returns: a ``PrintResult``. As with :meth:`submit`, a print
            failure is reported through ``error`` rather than raised.
        """
        chunks_all = _chunked(list(sheets), self.chunk_size)
        self.submitted_sheets = []
        self.uncertain_sheets = []
        self.unsubmitted_sheets = list(sheets)

        if not printer_is_available(printer_name):
            error = f"printer {printer_name!r} is no longer available"
            log_event(
                "print_printer_unavailable",
                level=logging.WARNING,
                printer=printer_name,
                sheets=list(sheets),
                side="duplex",
                pass_index=0,
            )
            return PrintResult(submitted=0, job_id=None, error=error)

        printer = _new_qprinter()
        if printer_name:
            printer.setPrinterName(printer_name)
        printer.setCopyCount(max(1, copies))
        printer.setFullPage(True)
        printer.setDuplex(_duplex_auto_mode())

        chunks = chunks_all
        submitted_total = 0
        for position, chunk in enumerate(chunks):
            try:
                painter = _new_qpainter()
                if not painter.begin(printer):
                    raise RuntimeError(f"could not begin painting on printer {printer_name!r}")
                try:
                    first = True
                    for sheet_index in chunk:
                        for side in ("front", "back"):
                            if not first:
                                printer.newPage()
                            first = False
                            rendered = _render_sheet_side(plan, sheet_index, side, dpi, False, False)
                            self._paint_rendered_page(painter, printer, rendered)
                finally:
                    painter.end()
            except Exception as exc:  # noqa: BLE001
                self.uncertain_sheets = list(chunk)
                remaining = [s for c in chunks[position + 1 :] for s in c]
                self.unsubmitted_sheets = remaining
                log_exception(
                    "print_duplex_chunk_failed",
                    exc,
                    printer=printer_name,
                    submitted_sheets=list(self.submitted_sheets),
                    uncertain_sheets=list(chunk),
                    unsubmitted_sheets=remaining,
                )
                return PrintResult(submitted=submitted_total, job_id=None, error=str(exc))
            log_print_job(printer_name, self.profile, chunk, dpi, 0)
            self.submitted_sheets.extend(chunk)
            submitted_total += len(chunk)
        self.unsubmitted_sheets = []
        return PrintResult(submitted=submitted_total, job_id=None, error=None)
