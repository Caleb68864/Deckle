"""The hardware-duplex submission path, which nothing calls.

``QtPrintBackend.submit_duplex`` is seventy lines of complete, working
Qt printing code, and no path in Deckle reaches it: the print dialog and
``PrintSession`` both drive manual duplex unconditionally, and
``duplex_modes(...).single_pass_duplex`` -- the flag that would select it
-- is read by nothing but its own tests.

Its docstring used to say "only used when ``single_pass_duplex`` is
True", which states a condition on something that never happens. That is
the shape this project has now named four times: ``Mark.cut_line``'s
declared-but-unproduced kind, ``Sheet.back``'s old docstring, the
unreachable ``.tmp`` skip in ``list_resumable``, and this. The docstring
now says outright that nothing calls it.

The reason to test dead code rather than delete it is the trap it was
carrying. The manual path passes the calibrated back-side offset into
every render; this path passed nothing, so a user who had measured their
printer's registration would have had that measurement silently dropped
-- on precisely the printers good enough to have a duplexer. Costless
while nothing calls it, and a lost calibration the moment something does.

Whether a *hardware* duplexer should get an offset measured for a hand
reload is a genuine open question, and this file does not settle it. It
pins the weaker thing that is true either way: **a measured number is not
discarded without saying so.** If the answer turns out to be that a
duplexer needs no correction, the profile should record that, rather than
one submission method deciding it in silence.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

import pytest

from deckle.app import backend as backend_mod
from deckle.app.backend import QtPrintBackend
from deckle.core.profiles import PrinterProfile


def _profile(**over) -> PrinterProfile:
    base = dict(
        version=1, flip_axis="long", output_face="down", feed_edge="top",
        reverse_stack=True, imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00", calibration_version=1,
    )
    base.update(over)
    return PrinterProfile(**base)


@pytest.fixture
def recorded(monkeypatch):
    """Record what every render of a sheet side was asked for.

    Stubs the Qt printer and painter rather than the render, so the
    arguments recorded are the ones ``submit_duplex`` actually composed.
    """
    calls: list[dict] = []

    class _Painter:
        def begin(self, printer): return True
        def end(self): return None

    class _Printer:
        def setPrinterName(self, name): self.name = name
        def setCopyCount(self, n): self.copies = n
        def setFullPage(self, on): pass
        def setDuplex(self, mode): pass
        def newPage(self): pass

    def fake_render(plan, sheet_index, side, dpi, rotate_backs, ignore_rotate,
                    back_offset_pt=(0.0, 0.0)):
        calls.append({
            "sheet": sheet_index, "side": side,
            "back_offset_pt": back_offset_pt,
        })
        return backend_mod.RenderedPage(width=1, height=1, rgba=b"\x00\x00\x00\x00")

    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)
    monkeypatch.setattr(backend_mod, "_new_qprinter", _Printer)
    monkeypatch.setattr(backend_mod, "_new_qpainter", _Painter)
    monkeypatch.setattr(backend_mod, "_duplex_auto_mode", lambda: "auto")
    monkeypatch.setattr(backend_mod, "_render_sheet_side", fake_render)
    monkeypatch.setattr(backend_mod, "log_print_job",
                        lambda *a, **k: None)
    monkeypatch.setattr(QtPrintBackend, "_paint_rendered_page",
                        lambda self, painter, printer, rendered, dpi: None)
    return calls


def _submit(backend, sheets=(0, 1)):
    return backend.submit_duplex(
        plan=None, sheets=list(sheets), printer_name="Test Printer",
        copies=1, dpi=300,
    )


def test_a_calibrated_offset_reaches_the_back_side(recorded):
    backend = QtPrintBackend(_profile(back_offset_x_pt=3.0, back_offset_y_pt=-1.5))

    _submit(backend)

    backs = [c for c in recorded if c["side"] == "back"]
    assert backs
    assert all(c["back_offset_pt"] == (3.0, -1.5) for c in backs)


def test_an_uncalibrated_profile_still_asks_for_no_correction(recorded):
    """Zero is the identity here, never a guess -- an uncalibrated printer
    must behave exactly as it did before the correction existed."""
    backend = QtPrintBackend(_profile())

    _submit(backend)

    assert all(c["back_offset_pt"] == (0.0, 0.0) for c in recorded)


def test_both_sides_of_every_sheet_are_rendered(recorded):
    """The duplexer reassembles interleaved pages, so a lost side is a
    book printed on one side of the paper."""
    backend = QtPrintBackend(_profile())

    _submit(backend, sheets=(0, 1, 2))

    assert [(c["sheet"], c["side"]) for c in recorded] == [
        (0, "front"), (0, "back"),
        (1, "front"), (1, "back"),
        (2, "front"), (2, "back"),
    ]


def test_an_unavailable_printer_is_reported_rather_than_painted(monkeypatch, recorded):
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: False)
    backend = QtPrintBackend(_profile())

    result = _submit(backend)

    assert result.submitted == 0
    assert "no longer available" in result.error
    assert recorded == []


def test_nothing_in_deckle_calls_this_yet():
    """The claim the docstring now makes, kept honest.

    If this ever fails it is good news -- someone wired the path up -- but
    the docstring and the open question about hardware duplexers versus a
    hand-measured correction both need revisiting at that moment, and this
    is what will say so.
    """
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    callers = []
    for folder, _, names in os.walk(os.path.join(root, "deckle")):
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, start=1):
                    # Comments and the definition itself are not callers.
                    # A prose mention is exactly what this file is about,
                    # so it must not be mistaken for a call.
                    code = line.split("#", 1)[0]
                    if re.search(r"\bsubmit_duplex\s*\(", code) and "def " not in code:
                        callers.append(f"{path}:{number}")

    assert callers == [], (
        "submit_duplex now has callers -- revisit its docstring and whether "
        f"a hardware duplexer should use a hand-measured offset: {callers}"
    )
