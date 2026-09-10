"""What the driver will say about a printer's non-printable border.

The preview's solid red rectangle is the imageable area: the part of the
sheet the printer can actually put ink on. Everything outside it is dead
paper, and since sheets print at **actual size** (roadmap B6, settled
2026-09-08) content that falls outside it is *lost*, not shrunk to fit.
The rectangle is therefore a claim about hardware, and a wrong one costs
a stack of paper.

Until now that claim was ``(18, 18, 18, 18)`` points -- a flat quarter
inch, hard-coded in the first built-in preset, drawn identically on every
machine whether or not a printer was attached. This module is the start of
asking instead.

**Read this before changing the query.** There are two different numbers
inside a ``QPageLayout`` and only one of them is a hardware fact:

``minimumMargins()``
    The driver's non-printable border, as the print device reports it.
    This is the number this module wants. With no printer attached it is
    ``(0, 0, 0, 0)``.

``margins()`` -- equivalently, the gap between ``fullRectPixels()`` and ``paintRectPixels()``
    The layout's *current* margins -- Qt's own default, clamped up to the
    minimum. Measured on this tree with no printer installed it is a flat
    ``10pt`` on every edge, for A4, Letter and a custom 200x300pt page
    alike, while ``minimumMargins()`` is zero. It is freely settable back
    down to zero, which is the proof that it is a preference and not a
    limit.

The SS-08 spike and the N2 roadmap row both proposed the *second* number
(``paintRect`` minus ``fullRect``). That would have replaced a generic
0.25in with a fabricated 0.139in -- a number no driver was ever asked for,
indistinguishable at every call site from a measured one, and larger than
the truth on the edges where a real printer's border is smaller. Deckle
would have drawn a confident red line around a printer it had not
consulted. See ``docs/decisions.md``.

Everything here except :func:`query_imageable_area_pt` is pure, and that
one takes an injectable factory, so the whole path is exercised headlessly.
Qt is imported lazily inside the query for the same reason every other
module under ``deckle.app`` does it.
"""

from __future__ import annotations

import dataclasses

from deckle.core.profiles import PrinterProfile

Margins = tuple[float, float, float, float]


def imageable_area_from_margins(margins_pt) -> Margins | None:
    """A driver's reported minimum margins, as an imageable area.

    :param margins_pt: ``(left, top, right, bottom)`` in points, as the
        driver reports them.
    :returns: the same four numbers as a tuple of floats, or ``None`` when
        the answer is unusable.

    ``None`` is returned for an all-zero answer, and that is the
    load-bearing decision in this module. Zero is exactly what a driverless
    ``QPrinter`` reports, what a virtual printer reports, and what a queue
    Qt does not recognise reports. Taking it at face value would mean
    drawing the red guide on the paper's edge and telling a binder "this
    printer prints to the very edge of the sheet" -- a claim no consumer
    printer can honour, and a far more confident lie than the generic
    preset it would replace. ``None`` means "unknown", and every caller
    falls back to the preset.

    A negative margin is likewise refused rather than clamped: it means
    the reported geometry is not what this function assumes, and guessing
    which edge is wrong is worse than declining to answer.
    """
    try:
        left, top, right, bottom = (float(m) for m in margins_pt)
    except (TypeError, ValueError):
        return None
    margins = (left, top, right, bottom)
    if any(m < 0.0 for m in margins):
        return None
    if not any(margins):
        return None
    return margins


def _margins_from_page_layout(layout) -> Margins | None:
    """``(left, top, right, bottom)`` in points from a ``QPageLayout``.

    Split out from :func:`query_imageable_area_pt` so the reading of the
    Qt object -- the part with a real chance of being wrong -- can be
    driven by a stand-in in the tests.

    ``QPageLayout.minimumMargins()`` returns a ``QMarginsF`` in the
    layout's own units, which for a ``QPrinter`` is points. Converted
    explicitly anyway rather than assumed, because a unit change here
    would be silent and would land straight on paper.
    """
    from PySide6.QtGui import QPageLayout

    minimum = layout.minimumMargins()
    if layout.units() != QPageLayout.Unit.Point:
        # Re-express through a layout whose units we have set ourselves.
        layout = QPageLayout(layout)
        layout.setUnits(QPageLayout.Unit.Point)
        minimum = layout.minimumMargins()
    return imageable_area_from_margins(
        (minimum.left(), minimum.top(), minimum.right(), minimum.bottom())
    )


def _default_printer_layout(printer_name: str):
    """The ``QPageLayout`` for ``printer_name``, or ``None``.

    Built on a ``QPrinter`` of this function's own, in ``StandardMode``,
    which is never given to anything else. ``QtPrintBackend`` calls
    ``setFullPage(True)`` on the printer it paints with, deliberately (see
    ``backend.py``'s module docstring and roadmap B6) -- and this must
    neither disturb that object nor imitate it.
    """
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

    info = QPrinterInfo.printerInfo(printer_name)
    if info.isNull():
        # A queue that has gone away returns a null info rather than
        # raising -- the same check `backend.printer_is_available` makes.
        return None
    return QPrinter(info, QPrinter.PrinterMode.HighResolution).pageLayout()


def query_imageable_area_pt(
    printer_name: str, *, layout_factory=_default_printer_layout
) -> Margins | None:
    """Ask the driver for ``printer_name``'s non-printable border.

    :param printer_name: the queue to ask about.
    :param layout_factory: how to obtain a ``QPageLayout`` for a printer
        name. Injectable so the reading and the guards can be tested
        without a printer attached; the default builds a real ``QPrinter``.
    :returns: ``(left, top, right, bottom)`` in points, or ``None`` when
        the driver reports nothing usable -- the normal answer for a
        virtual printer, for a queue Qt does not recognise, and on any
        machine with no printer installed.

    Never raises for an unreachable printer: an unusable answer and a
    failed question are the same thing to every caller, and both mean
    "keep the preset".

    **Nothing here is ever saved.** A ``PrinterProfile`` on disk is a
    *calibration* -- it exists because somebody printed a target and
    measured it with a ruler. A number read off a driver has not been
    checked against paper, and writing it into ``config_dir`` would make
    an uncalibrated printer indistinguishable from a calibrated one for
    every later reader.
    """
    try:
        layout = layout_factory(printer_name)
    except Exception:  # noqa: BLE001 -- degraded, not fatal
        return None
    if layout is None:
        return None
    try:
        return _margins_from_page_layout(layout)
    except Exception:  # noqa: BLE001 -- degraded, not fatal
        return None


def profile_with_driver_margins(
    profile: PrinterProfile, imageable_area_pt: Margins | None
) -> PrinterProfile:
    """``profile`` with the driver's border substituted in, if there is one.

    Only ``imageable_area_pt`` is replaced. Every other field -- the flip
    axis, the reload behaviour, the measured back offset -- describes what
    the *operator* does with the paper between passes, which is not
    something a driver knows.

    :param profile: the resolved profile, saved calibration or preset.
    :param imageable_area_pt: the driver's answer, or ``None``.
    :returns: ``profile`` unchanged when there is no answer, else a copy.
    """
    if imageable_area_pt is None:
        return profile
    return dataclasses.replace(profile, imageable_area_pt=imageable_area_pt)
