"""Asking the driver where it can actually put ink.

**No printer is attached to the machine this suite runs on**, and Qt runs
offscreen. ``QPrinterInfo.printerInfo(name)`` is null for every name here,
so the real driver reading cannot be exercised and nothing below claims to
have exercised it -- see the "what this cannot prove" block at the bottom,
which asserts the emptiness rather than describing it.

What *is* tested is everything around that one call: the arithmetic, the
guards that decide an answer is unusable, and the whole query path driven
through an injected stand-in layout. That is the part where a wrong number
would come from, and a wrong number here is a ruined stack of paper --
sheets print at actual size (B6), so content inside the border is lost
rather than shrunk to fit.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.printer_capabilities import (  # noqa: E402
    imageable_area_from_margins,
    profile_with_driver_margins,
    query_imageable_area_pt,
)
from deckle.core.profiles import BUILTIN_PRESETS  # noqa: E402

PRESET = BUILTIN_PRESETS["generic_face_down_reversed"]


# -- reading an answer ---------------------------------------------------


def test_a_reported_border_is_carried_through_per_edge():
    """Four independent numbers, in (left, top, right, bottom) order.

    They are *margins*, not an (x0, y0, x1, y1) rect -- reading them as a
    rect yields a ~600pt "inset", which is what `imageable_inset_pt`'s
    docstring records having happened.
    """
    assert imageable_area_from_margins((12.0, 18.0, 12.0, 39.6)) == (
        12.0, 18.0, 12.0, 39.6,
    )


def test_integers_come_back_as_floats():
    assert imageable_area_from_margins((12, 18, 12, 40)) == (12.0, 18.0, 12.0, 40.0)


def test_an_all_zero_answer_means_unknown_not_borderless():
    """The load-bearing guard.

    Zero on every edge is what a driverless QPrinter reports, what a
    virtual printer reports, and what a queue Qt does not recognise
    reports. Believing it would draw the red guide on the paper's edge and
    promise a binder that this printer prints to the very edge of the
    sheet -- a claim no consumer printer can honour, and a more confident
    lie than the generic preset it would replace.
    """
    assert imageable_area_from_margins((0.0, 0.0, 0.0, 0.0)) is None


def test_one_real_edge_is_still_an_answer():
    """Only ALL-zero is refused. A printer with a border on one edge only
    is unusual but not unreadable, and blanking it would discard a true
    measurement."""
    assert imageable_area_from_margins((0.0, 0.0, 0.0, 39.6)) == (0.0, 0.0, 0.0, 39.6)


def test_a_negative_margin_reports_nothing():
    """Not clamped. A negative means the geometry is not what this assumes,
    and guessing which edge is wrong is worse than declining."""
    assert imageable_area_from_margins((-1.0, 18.0, 12.0, 12.0)) is None


@pytest.mark.parametrize("bad", [None, (1.0, 2.0), (1.0, 2.0, 3.0, 4.0, 5.0), "12"])
def test_an_unreadable_answer_reports_nothing(bad):
    assert imageable_area_from_margins(bad) is None


# -- the query path, driven by a stand-in driver -------------------------


class _FakeMargins:
    def __init__(self, left, top, right, bottom):
        self._v = (left, top, right, bottom)

    def left(self):
        return self._v[0]

    def top(self):
        return self._v[1]

    def right(self):
        return self._v[2]

    def bottom(self):
        return self._v[3]


class _FakeLayout:
    """A ``QPageLayout`` stand-in reporting a driver's minimum margins.

    ``minimumMargins()`` is deliberately the only source of numbers here.
    The gap between ``fullRect`` and ``paintRect`` -- what the N2 spec and
    the SS-08 spike proposed reading -- is Qt's own settable default page
    margin, not a hardware limit, so a fake offering it would be modelling
    the bug rather than the fix.
    """

    def __init__(self, margins, units=None):
        self._margins = margins
        self._units = units

    def units(self):
        from PySide6.QtGui import QPageLayout

        return self._units if self._units is not None else QPageLayout.Unit.Point

    def minimumMargins(self):
        return _FakeMargins(*self._margins)


def test_the_query_returns_what_the_driver_reported():
    pytest.importorskip("PySide6")

    area = query_imageable_area_pt(
        "Any", layout_factory=lambda _name: _FakeLayout((12.0, 18.0, 12.0, 39.6))
    )

    assert area == (12.0, 18.0, 12.0, 39.6)


def test_a_printer_qt_does_not_know_reports_nothing():
    """``QPrinterInfo.printerInfo`` returns a null info rather than raising
    for a queue that has gone away; the factory expresses that as None."""
    assert query_imageable_area_pt("Gone", layout_factory=lambda _name: None) is None


def test_a_driver_reporting_no_border_reports_nothing():
    pytest.importorskip("PySide6")

    assert query_imageable_area_pt(
        "Virtual", layout_factory=lambda _name: _FakeLayout((0.0, 0.0, 0.0, 0.0))
    ) is None


def test_an_unreachable_printer_never_raises():
    """An unusable answer and a failed question are the same thing to
    every caller, and both mean "keep the preset"."""

    def explode(_name):
        raise OSError("spooler is not answering")

    assert query_imageable_area_pt("Broken", layout_factory=explode) is None


def test_a_layout_that_misbehaves_never_raises():
    class _Broken:
        def units(self):
            raise RuntimeError("no")

        def minimumMargins(self):
            raise RuntimeError("no")

    assert query_imageable_area_pt("Odd", layout_factory=lambda _n: _Broken()) is None


def test_the_query_is_never_made_in_full_page_mode():
    """``setFullPage(True)`` is what `QtPrintBackend` sets on the printer it
    paints with, deliberately (B6). Under full page there is no inset to
    read at all, so this module must never touch it.

    Checked against the parsed syntax tree, not the text: the module
    docstring names ``setFullPage`` precisely to explain why it is absent,
    and a grep would fail on the explanation.
    """
    import ast
    import inspect

    from deckle.app import printer_capabilities

    tree = ast.parse(inspect.getsource(printer_capabilities))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "setFullPage" not in called


def test_a_driver_reading_is_never_persisted(tmp_path, monkeypatch):
    """A PrinterProfile on disk is a calibration -- somebody printed a
    target and measured it. Writing a driver reading there would make an
    uncalibrated printer indistinguishable from a calibrated one."""
    import ast
    import inspect

    from deckle.app import printer_capabilities

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    query_imageable_area_pt(
        "Any", layout_factory=lambda _n: _FakeLayout((12.0, 12.0, 12.0, 12.0))
    )
    profile_with_driver_margins(PRESET, (12.0, 12.0, 12.0, 12.0))

    assert list(tmp_path.rglob("*.json")) == []
    tree = ast.parse(inspect.getsource(printer_capabilities))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "save" not in called


# -- substituting it into a profile --------------------------------------


def test_the_driver_border_replaces_only_the_imageable_area():
    """The flip axis, the reload behaviour and the measured back offset
    describe what the OPERATOR does with the paper between passes. A
    driver knows none of it."""
    filled = profile_with_driver_margins(PRESET, (1.0, 2.0, 3.0, 4.0))

    assert filled.imageable_area_pt == (1.0, 2.0, 3.0, 4.0)
    assert filled.flip_axis == PRESET.flip_axis
    assert filled.output_face == PRESET.output_face
    assert filled.reverse_stack == PRESET.reverse_stack
    assert filled.back_offset_x_pt == PRESET.back_offset_x_pt
    assert filled.back_offset_y_pt == PRESET.back_offset_y_pt


def test_no_driver_answer_leaves_the_profile_exactly_alone():
    assert profile_with_driver_margins(PRESET, None) is PRESET


def test_the_original_profile_is_not_mutated():
    profile_with_driver_margins(PRESET, (1.0, 2.0, 3.0, 4.0))

    assert PRESET.imageable_area_pt == (18.0, 18.0, 18.0, 18.0)


# -- what this cannot prove ----------------------------------------------


def test_this_machine_has_no_printer_to_ask():
    """Asserted, not described.

    The one link in this chain that no test here covers is whether
    ``QPageLayout.minimumMargins()`` reports a real printer's true
    non-printable border. It cannot be covered from this machine, and this
    test exists so that stops being an invisible gap: if a printer is ever
    attached to a machine running this suite, it fails and says so, and
    the [HUMAN] check in the N2 spec becomes runnable.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from PySide6.QtPrintSupport import QPrinterInfo

    names = [i.printerName() for i in QPrinterInfo.availablePrinters()]

    if names:
        pytest.fail(
            "A printer is attached: "
            f"{names}. The driver reading in printer_capabilities has never "
            "been checked against hardware. Run the N2 [HUMAN] acceptance "
            "check (print a full-bleed target, measure the border with a "
            "ruler, compare against 'Use printer margins') and record the "
            "result before trusting the red guide on this machine."
        )


def test_the_gap_qt_reports_without_a_driver_is_not_a_hardware_limit():
    """The trap the N2 spec and the SS-08 spike both fell into.

    With no printer attached, the gap between ``fullRectPixels`` and
    ``paintRectPixels`` in StandardMode is a flat ~10pt on every edge, for
    every paper size -- while ``minimumMargins()``, the driver's actual
    channel, is zero. That 10pt is Qt's own default page margin: settable,
    and settable back down to zero, which is what proves it is a
    preference rather than a limit.

    Had N2 shipped the spec's arithmetic, every uncalibrated Deckle would
    have drawn a confident red line 0.139in inside the paper on behalf of
    a driver it never consulted.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout
    from PySide6.QtPrintSupport import QPrinter

    layout = QPrinter(QPrinter.PrinterMode.HighResolution).pageLayout()
    assert layout.mode() == QPageLayout.Mode.StandardMode

    minimum = layout.minimumMargins()
    reported = (minimum.left(), minimum.top(), minimum.right(), minimum.bottom())
    assert reported == (0.0, 0.0, 0.0, 0.0), (
        "a driver appeared where there is none -- re-read this module"
    )

    current = layout.margins()
    assert current.left() > 0.0, "Qt's default page margin is no longer non-zero"

    # The proof it is not a limit: it goes to zero when asked.
    layout.setMargins(QMarginsF(0, 0, 0, 0))
    assert layout.margins().left() == 0.0

    # And so the honest answer for this machine is "unknown".
    assert imageable_area_from_margins(reported) is None
