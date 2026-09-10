"""QtPrintBackend: the only Qt code outside the UI.

Implements the ``PrintBackend`` Protocol (``deckle.core.printing``) by
rasterizing each output page at the printer's own device DPI via
``deckle.core.render.render_sheet`` and painting it into a ``QPainter`` at
an exact device-space rectangle. ``QPrinter.setFullPage(True)`` is always
set, so Qt applies no margin of its own and the painter's origin is the
physical corner of the paper.

**Sheets are painted at actual size** -- one inch of the design is one inch
of paper (roadmap B6, settled 2026-09-08). The profile's
``imageable_area_pt`` does not enter the paint transform; it describes the
printer's non-printable border, which at actual size is something to warn
about (``clipped_by_imageable_area``) rather than to shrink into. Nothing
here can defeat that physical border (see
``docs/spikes/qprinter-capability-report.md``) -- it can only decline to
disguise it as a smaller book.

The device page is set from ``plan.paper_pt`` on every job
(``_apply_paper``). Deckle draws its own print dialog, so nothing else
would ever tell the driver what paper this is for, and at actual size a
mismatch is clipped rather than scaled.

Tray selection is deliberately never touched -- it is effectively
Windows-only and manual duplex does not need it.

Jobs are submitted in bounded chunks (default 10 sheets) so a failure
during a long run loses at most one chunk, not the whole pass. A failed
chunk cancels the remaining chunks of that pass -- submission does not
continue into a jammed or offline printer.

``rotate_backs`` on a ``PrintPass`` is honored by asking
``deckle.core.export.export`` for a turned page -- never by turning it
here afterwards. The turn and the registration correction interact (a
point reflection negates a translation), and ``export`` is the one place
that knows about both; a caller that applies the correction through
``export`` and then turns the page behind its back gets the correction at
twice its size in the wrong direction. For drivers known to ignore the PDF
``/Rotate`` key the turn is additionally baked into the page content by
``page.flatten_rotation()``.
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
from deckle.core.render import (
    pdfium_guard, rasterize_page, RenderedPage, render_sheet,
)
from deckle.core.session_log import log_print_job

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


def _page_layout(paper_pt: tuple[float, float]):
    """A ``QPageLayout`` describing ``paper_pt``, full-bleed.

    Qt stores standard page sizes portrait-side-up and carries the turn in
    the layout's orientation, so a landscape plan is described as its own
    portrait size *plus* ``Landscape`` rather than as a custom size with
    the numbers swapped. Doing it the other way works but throws away the
    match: ``QPageSize(QSizeF(792, 612))`` is "Letter", and a driver that
    is handed a page named Letter and told to turn it does something much
    more predictable than one handed 792x612 points of nothing in
    particular.

    ``FuzzyOrientationMatch`` is what performs that naming, within Qt's
    3-point tolerance -- Deckle's A4 is 595.276x841.89pt and Qt's is
    595x842, and the two must not become a "custom" size over 0.28pt.

    Margins are zero because ``setFullPage(True)`` is always set (see the
    module docstring): the imposer's own margins are inside the rasterised
    sheet already, and Qt adding more would move the paper corner away
    from ``(0, 0)``.

    :param paper_pt: the plan's paper as ``(width, height)`` in points.
    :returns: the layout to hand to ``QPrinter.setPageLayout``.
    """
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize

    width_pt, height_pt = float(paper_pt[0]), float(paper_pt[1])
    landscape = width_pt > height_pt
    upright = QSizeF(height_pt, width_pt) if landscape else QSizeF(width_pt, height_pt)
    size = QPageSize(
        upright,
        QPageSize.Unit.Point,
        "",
        QPageSize.SizeMatchPolicy.FuzzyOrientationMatch,
    )
    orientation = (
        QPageLayout.Orientation.Landscape
        if landscape
        else QPageLayout.Orientation.Portrait
    )
    return QPageLayout(
        size, orientation, QMarginsF(0.0, 0.0, 0.0, 0.0), QPageLayout.Unit.Point
    )


def _apply_paper(printer, paper_pt: tuple[float, float], printer_name: str) -> None:
    """Tell ``printer`` what paper the plan is for.

    Deckle draws its own print dialog, so no ``QPrintDialog`` ever asks the
    user for a page size and nothing else in the program would set one.
    Without this the job goes to whatever page the driver defaults to --
    portrait A4 or portrait Letter -- and since B6 that is not a cosmetic
    difference. The painter draws the sheet at actual size from ``(0, 0)``
    and never reads ``printer.width()``, so paper the device page is too
    small for is **clipped**, not scaled: a folio (letter-landscape) plan
    on a default portrait page loses 180pt, two and a half inches, off its
    width. Deckle's own clip warnings cannot see it either, because
    ``clipped_by_imageable_area`` and ``clipped_by_page`` are both computed
    in ``plan.paper_pt`` and assume the device page *is* the plan's paper.
    This is the call that makes that assumption true.

    A printer with a fixed paper list can refuse, and Qt reports the
    refusal only through a return value. The job still goes -- declining to
    print is worse than printing on the paper the operator loaded -- but
    the refusal is logged rather than dropped, because it is the one
    warning that the sheet about to come out will not measure what the
    schedule says it does.

    :param printer: the ``QPrinter`` about to be painted on.
    :param paper_pt: the plan's paper as ``(width, height)`` in points.
    :param printer_name: for the log line only.
    :returns: nothing.
    """
    layout = _page_layout(paper_pt)
    if not printer.setPageLayout(layout):
        log_event(
            "print_paper_size_refused",
            level=logging.WARNING,
            printer=printer_name,
            paper_pt=[float(paper_pt[0]), float(paper_pt[1])],
        )


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


def _bake_rotation(pdf_path: str) -> None:
    """Fold each page's ``/Rotate`` into its content stream, in place.

    For a driver known to discard ``/Rotate`` (``DRIVERS_IGNORING_ROTATE``).
    The half turn itself is *not* applied here: it is asked of
    :func:`deckle.core.export.export`, which is the only code that knows
    the turn also negates the registration correction. This bakes an
    already-decided turn into the ink so a driver that ignores the key
    still prints it.

    Flattening is lossier than the key, which is why it is not the
    default; most drivers honour ``/Rotate``.
    """
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            page.flatten_rotation()
        pdf.save(pdf_path)


def _render_sheet_side(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int,
    rotate_backs: bool,
    ignore_rotate: bool,
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
) -> RenderedPage:
    """Render one side of one sheet, applying back-side rotation if asked.

    Delegates to ``render_sheet`` (the shared export-then-rasterize path)
    for the common case. When rotation is required this exports the
    single-sheet PDF itself so pikepdf can rotate the page before pdfium
    rasterizes it -- ``render_sheet`` has no rotation hook.
    """
    turn = side == "back" and rotate_backs
    corrected = side == "back" and back_offset_pt != (0.0, 0.0)
    if not turn and not corrected:
        # The shared preview cache is keyed on the plan alone, which
        # knows nothing about a printer correction -- so anything
        # carrying one exports for itself rather than risking a
        # cached uncorrected render being sent to paper.
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
        # `rotate_180` goes in, rather than the page being turned here
        # afterwards, because the turn and the correction are not
        # independent: a point reflection maps a translation to its
        # negation, so `export` negates `back_offset_pt` for a face it
        # knows will be turned. Turning it behind `export`'s back leaves
        # the correction un-negated and lands the ink at twice the
        # measured error, in the wrong direction.
        export.export(
            plan,
            tmp_path,
            sheets=[sheet_index],
            back_offset_pt=back_offset_pt,
            rotate_180=turn,
        )
        if turn and ignore_rotate:
            _bake_rotation(tmp_path)

        page_index = 1 if has_front else 0
        # Held across the document's whole life, not just the render.
        # pdfium is process-global and this path rasterizes on the GUI
        # thread -- the print dialog runs no thread of its own -- so a
        # print issued while the preview is still drawing puts two threads
        # in pdfium at once, which faults natively rather than raising.
        with pdfium_guard():
            pdf = pdfium.PdfDocument(tmp_path)
            try:
                if page_index >= len(pdf):
                    return RenderedPage(width=0, height=0, rgba=b"")
                # rasterize_page closes pdfium's page and bitmap eagerly;
                # see its docstring for why leaving them to the GC produces
                # "Exception ignored in: <finalize object...>" on the console.
                pil_image = rasterize_page(pdf, page_index, scale=dpi / 72).convert("RGBA")
                width, height = pil_image.size
                return RenderedPage(width=width, height=height, rgba=pil_image.tobytes())
            finally:
                pdf.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _render_ruled_sheet_side(
    plan: SheetPlan,
    sheet_index: int,
    side: Literal["front", "back"],
    dpi: int,
) -> RenderedPage:
    """Render one face with a labelled ruler drawn across it.

    The proof sheet. ``render_sheet``'s cache is keyed on the plan alone,
    which knows nothing about a rule, so this exports for itself rather
    than risking a ruled render being served back to the preview -- the
    same reasoning that makes a back-offset correction export for itself
    in :func:`_render_sheet_side`.

    Exported with ``side=`` so the temporary PDF holds exactly the one face
    asked for, which is what makes the page index unambiguously zero.

    :param plan: the imposed sheets.
    :param sheet_index: which sheet to proof.
    :param side: which face of it.
    :param dpi: rasterization resolution.
    :returns: the rasterised face, or an empty page when the sheet has no
        such face.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        export.export(plan, tmp_path, sheets=[sheet_index], side=side, rule=True)
        # pdfium is process-global and this rasterizes on the GUI thread;
        # see `_render_sheet_side` for why the guard is held this wide.
        with pdfium_guard():
            pdf = pdfium.PdfDocument(tmp_path)
            try:
                if len(pdf) == 0:
                    return RenderedPage(width=0, height=0, rgba=b"")
                pil_image = rasterize_page(pdf, 0, scale=dpi / 72).convert("RGBA")
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

        ``side``/``rotate_backs``/``pass_index`` are keyword-only and are
        declared on the ``PrintBackend`` Protocol too. Their defaults exist
        for a one-face job; **any caller submitting a pass must pass all
        three, or a back pass paints fronts.** They were once extras beyond
        a narrower Protocol, described here as safe to omit, and
        ``PrintSession`` -- written against that Protocol -- omitted them on
        both passes for the life of the manual-duplex path.

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
            land right side up. Decided by ``plan_passes`` from the flip
            axis *and* the paper -- never re-derived here from either one
            alone.
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

        # The paper is already out by this point, so a logging failure is a
        # different animal from a print failure and has to be reported as
        # one. `log_print_job` raises rather than swallowing, on purpose --
        # the session log is a hard constraint, not a best-effort trace --
        # but the raise used to escape `submit` entirely: past this method's
        # promise that failures come back through `error`, out of
        # `session.start()`, and into a print dialog that does not catch it.
        # Three sheets would physically print, the user would get a
        # traceback, and the session cursor would still read 0, so a resume
        # reprinted every one of them.
        #
        # Reported rather than swallowed, so the constraint still holds: the
        # user is told, `log_exception` records it, and the run stops exactly
        # as it did before. `submitted` counts the sheets because they
        # printed -- that number describes paper, not bookkeeping.
        try:
            log_print_job(printer_name, self.profile, sheets, dpi, pass_index)
        except OSError as exc:
            log_exception(
                "print_session_log_failed",
                exc,
                printer=printer_name,
                sheets=list(sheets),
                side=side,
                pass_index=pass_index,
            )
            return PrintResult(
                submitted=len(sheets),
                job_id=None,
                error=(
                    f"{len(sheets)} sheet(s) printed, but the print could "
                    f"not be recorded in the session log ({exc}). The paper "
                    "is correct; the record of it is missing."
                ),
            )
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

    def submit_proof(
        self,
        plan: SheetPlan,
        sheet_index: int,
        printer_name: str,
        dpi: int,
    ) -> PrintResult:
        """Print one sheet's front with a ruler on it, and nothing else.

        Sheets print at actual size (roadmap B6) -- an inch of the design
        is an inch of paper. That is a claim about someone else's printer,
        made by a program that cannot see it, and a driver preset saying
        "fit to page" quietly falsifies it. The rule is the only evidence
        available: print it, measure it against a tape, and if it is short
        the claim is not true on this machine.

        Deliberately **not** a :class:`~deckle.core.print_session.PrintSession`.
        A proof is not a job: it has one face, no reload, no back pass, and
        nothing worth resuming. Routing it through a session would leave a
        resumable run on disk that the next print dialog would offer to
        finish, which is how a proof turns into a ruined stack of paper.

        The front, because that is the face a proof needs -- the check is
        the geometry of the sheet, and reloading paper to measure the same
        rule on the other side proves nothing new.

        :param plan: the imposed sheets.
        :param sheet_index: the sheet to proof.
        :param printer_name: the target queue.
        :param dpi: rasterization resolution.
        :returns: a ``PrintResult`` counting the one sheet, or carrying the
            failure. Never raises, for the same reason :meth:`submit` does
            not: the caller's job is to say what happened, not to unwind.
        """
        if not printer_is_available(printer_name):
            error = f"printer {printer_name!r} is no longer available"
            log_event(
                "proof_printer_unavailable",
                level=logging.WARNING,
                printer=printer_name,
                sheet=sheet_index,
            )
            return PrintResult(submitted=0, job_id=None, error=error)
        try:
            self._submit_chunk(
                plan,
                [sheet_index],
                printer_name,
                1,
                dpi,
                "front",
                False,
                rule=True,
            )
        except Exception as exc:  # noqa: BLE001 - reported as PrintResult.error
            log_exception(
                "proof_failed", exc, printer=printer_name, sheet=sheet_index, dpi=dpi
            )
            return PrintResult(submitted=0, job_id=None, error=str(exc))
        # No `log_print_job`: the session log records the paper a *job*
        # consumed, and a proof is not part of one. Counting it there would
        # put a sheet in the record that no pass ever fed.
        log_event("proof_printed", printer=printer_name, sheet=sheet_index, dpi=dpi)
        return PrintResult(submitted=1, job_id=None, error=None)

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
        rule: bool = False,
    ) -> None:
        printer = _new_qprinter()
        if printer_name:
            printer.setPrinterName(printer_name)
        printer.setCopyCount(max(1, copies))
        # Margins come from the profile's imageable_area_pt, never Qt's
        # own defaults -- setFullPage(True) is what makes that true.
        printer.setFullPage(True)
        # ...and this is what makes the page the plan's paper rather than
        # the driver's default. Since B6 a mismatch clips instead of
        # scaling; see _apply_paper.
        _apply_paper(printer, plan.paper_pt, printer_name)

        painter = _new_qpainter()
        if not painter.begin(printer):
            raise RuntimeError(f"could not begin painting on printer {printer_name!r}")
        try:
            for i, sheet_index in enumerate(sheets):
                if i > 0:
                    printer.newPage()
                if rule:
                    # A proof measures the sheet, so it carries no back
                    # correction: the offset moves the image and would be
                    # read as the printer having scaled it.
                    rendered = _render_ruled_sheet_side(plan, sheet_index, side, dpi)
                else:
                    rendered = _render_sheet_side(
                        plan,
                        sheet_index,
                        side,
                        dpi,
                        rotate_backs,
                        printer_name in self._drivers_ignoring_rotate,
                        back_offset_pt=(
                            self.profile.back_offset_x_pt,
                            self.profile.back_offset_y_pt,
                        ),
                    )
                self._paint_rendered_page(painter, printer, rendered, dpi)
        finally:
            painter.end()

    def _paint_rendered_page(
        self, painter, printer, rendered: RenderedPage, dpi: int
    ) -> None:
        """Paint one rasterised sheet at its true physical size.

        **An inch of the design is an inch of paper.** That is the decision
        this method used to leave open (roadmap B6), settled 2026-09-08.

        It previously scaled the whole sheet into the profile's imageable
        area, which shrank it by the border: on a printer with 18pt margins
        a letter sheet printed at about 94% of its designed size, with the
        aspect ratio skewed whenever the borders were asymmetric. Every
        measurement in the finished book came out ~6% short. For a program
        whose output is bound by hand that is not a rounding error -- boards
        are cut to measured dimensions, the spine width in the schedule is
        computed from paper thickness, and a text block 6% smaller than
        designed does not fit the case made for it. Everything else here
        already assumed actual size: the exported PDF carries
        ``/PrintScaling /None``, the schedule prints "Print at ACTUAL SIZE",
        and the proof rule exists to be measured with a ruler.

        The transform is therefore only a change of units. The sheet was
        rasterised at ``dpi`` dots to the inch and the printer lays down
        ``printer.resolution()`` of them, so that ratio is the whole of it.
        ``setFullPage(True)`` -- always set, see the module docstring --
        puts the painter's origin on the physical paper corner rather than
        inside Qt's own margin, so ``(0, 0)`` really is the corner of the
        sheet.

        The profile's ``imageable_area_pt`` is deliberately no longer read
        here. At actual size the printer's non-printable border is a fact
        to be *warned* about, not a box to shrink into: content that falls
        inside it is clipped, and Deckle already computes that warning as
        ``clipped_by_imageable_area`` and draws the border in the preview.
        Telling the truth and losing a millimetre at the edge is better
        than silently resizing the book, because the first is visible
        before the paper is spent and the second is not visible until the
        case will not close.

        :param painter: the ``QPainter`` begun on ``printer``.
        :param printer: the ``QPrinter`` being painted onto.
        :param rendered: the rasterised sheet.
        :param dpi: the resolution ``rendered`` was rasterised at.
        :returns: nothing. A degenerate page paints nothing.
        """
        if rendered.width == 0 or rendered.height == 0:
            return
        image = _qimage(rendered.rgba, rendered.width, rendered.height)
        device_per_pixel = printer.resolution() / float(dpi)
        target_w = max(1, int(round(rendered.width * device_per_pixel)))
        target_h = max(1, int(round(rendered.height * device_per_pixel)))
        painter.drawImage(0, 0, image.scaled(target_w, target_h))

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

        **Nothing calls this yet.** It is written and complete, and
        ``duplex_modes(printer_name).single_pass_duplex`` reports whether
        it would apply, but no path in Deckle reaches it: the print dialog
        and :class:`~deckle.core.print_session.PrintSession` both drive
        manual duplex unconditionally. Said outright because the previous
        wording -- "only used when ``single_pass_duplex`` is True" --
        described a condition on something that never happens, which is
        the same shape as ``Mark.cut_line``'s declared-but-unproduced kind
        and ``Sheet.back``'s old docstring: a sentence asserting a
        capability the program does not have.

        Chunking still applies -- one job per chunk of sheets, each job
        interleaving front/back pages for QPrinter's own duplex unit to
        reassemble. Tracks the same submitted/uncertain/unsubmitted split
        as :meth:`submit_pass`.

        **The registration correction is applied here too**, as it is on
        the manual path. It was omitted, which cost nothing while nothing
        called this and would have cost a calibration the moment something
        did: a user who measured their printer's back-side offset would
        have had it silently ignored on exactly the printers good enough
        to have a duplexer. Whether a *hardware* duplexer deserves the
        offset measured for a hand reload is a real question and not
        settled here -- but discarding a measured number without saying so
        is not the answer to it, and if the answer turns out to be "a
        duplexer needs no correction" then the profile should record that
        rather than this method deciding it in silence.

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
        _apply_paper(printer, plan.paper_pt, printer_name)
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
                            rendered = _render_sheet_side(
                                plan, sheet_index, side, dpi, False, False,
                                back_offset_pt=(
                                    self.profile.back_offset_x_pt,
                                    self.profile.back_offset_y_pt,
                                ),
                            )
                            self._paint_rendered_page(painter, printer, rendered, dpi)
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
