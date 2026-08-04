"""Tests for QtPrintBackend: chunking, rotation, logging, cancellation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from deckle.app import backend as backend_mod
from deckle.app.backend import DEFAULT_CHUNK_SIZE, QtPrintBackend, _chunked
from deckle.core.printing import PrintPass, PrintResult
from deckle.core.profiles import PrinterProfile


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


def _pass(sheet_order, side="front", rotate_backs=False, index=0) -> PrintPass:
    return PrintPass(
        index=index,
        sheet_order=list(sheet_order),
        side=side,
        reload_instruction="do the thing",
        rotate_backs=rotate_backs,
    )


class _RecordingBackend(QtPrintBackend):
    """A QtPrintBackend whose actual Qt submission is stubbed out.

    Records each chunk it was asked to submit and, optionally, fails on a
    chosen chunk -- so chunking/cancellation behavior can be tested without
    a real printer.
    """

    def __init__(self, *args, fail_on_chunk: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunks_attempted: list[list[int]] = []
        self._fail_on_chunk = fail_on_chunk

    def _submit_chunk(self, plan, sheets, printer_name, copies, dpi, side, rotate_backs):
        self.chunks_attempted.append(list(sheets))
        if self._fail_on_chunk is not None and len(self.chunks_attempted) == self._fail_on_chunk:
            raise RuntimeError("printer jammed")


def test_chunked_splits_into_bounded_groups():
    assert _chunked(list(range(25)), 10) == [
        list(range(0, 10)),
        list(range(10, 20)),
        list(range(20, 25)),
    ]


def test_submit_pass_25_sheets_chunk_10_produces_3_submissions():
    backend = _RecordingBackend(_profile(), chunk_size=10)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="Test Printer", copies=1, dpi=300
    )
    assert len(backend.chunks_attempted) == 3
    assert result.submitted == 25
    assert result.error is None


def test_submit_pass_failure_on_second_chunk_reports_submitted_10():
    backend = _RecordingBackend(_profile(), chunk_size=10, fail_on_chunk=2)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="Test Printer", copies=1, dpi=300
    )
    assert result.submitted == 10
    assert result.error is not None


def test_failed_chunk_cancels_remaining_chunks_of_the_pass():
    backend = _RecordingBackend(_profile(), chunk_size=10, fail_on_chunk=1)
    result = backend.submit_pass(
        plan=None, print_pass=_pass(range(25)), printer_name="Test Printer", copies=1, dpi=300
    )
    # Only the failing first chunk was attempted -- the rest never ran.
    assert len(backend.chunks_attempted) == 1
    assert result.submitted == 0
    assert result.error is not None


def test_submit_logs_one_record_before_returning(monkeypatch):
    logged = []

    def _fake_log(printer, profile, sheets, dpi, pass_index):
        logged.append((printer, profile, list(sheets), dpi, pass_index))

    monkeypatch.setattr(backend_mod, "log_print_job", _fake_log)

    class _NoopSubmit(QtPrintBackend):
        def _submit_chunk(self, plan, sheets, printer_name, copies, dpi, side, rotate_backs):
            return None

    backend = _NoopSubmit(_profile())
    result = backend.submit(plan=None, sheets=[0, 1, 2], printer_name="Test Printer", copies=1, dpi=300)

    assert result.error is None
    assert result.submitted == 3
    assert len(logged) == 1
    assert logged[0][0] == "Test Printer"
    assert logged[0][2] == [0, 1, 2]


def test_submit_pass_calls_log_once_per_chunk(monkeypatch):
    logged = []
    monkeypatch.setattr(
        backend_mod,
        "log_print_job",
        lambda printer, profile, sheets, dpi, pass_index: logged.append(list(sheets)),
    )

    backend = _RecordingBackend(_profile(), chunk_size=10)
    backend.submit_pass(plan=None, print_pass=_pass(range(25)), printer_name="P", copies=1, dpi=300)

    assert len(logged) == 3


def test_default_chunk_size_is_10():
    assert DEFAULT_CHUNK_SIZE == 10


def test_apply_rotate_backs_rotates_every_page(tmp_path):
    import pikepdf

    src = tmp_path / "sheet.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 300))
        pdf.save(str(src))

    backend_mod._apply_rotate_backs(str(src), ignore_rotate=False)

    with pikepdf.open(str(src)) as pdf:
        assert int(pdf.pages[0].Rotate) == 180


def test_apply_rotate_backs_flattens_for_drivers_that_ignore_rotate(tmp_path):
    import pikepdf

    src = tmp_path / "sheet.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 300))
        pdf.save(str(src))

    backend_mod._apply_rotate_backs(str(src), ignore_rotate=True)

    with pikepdf.open(str(src)) as pdf:
        # flatten_rotation() bakes the rotation into content and removes
        # (or zeroes) the /Rotate key so an ignoring driver can't drop it.
        rotate = pdf.pages[0].get("/Rotate")
        assert rotate is None or int(rotate) % 360 == 0


def test_rotate_backs_never_assigns_page_rotate_directly():
    import inspect

    source = inspect.getsource(backend_mod._apply_rotate_backs)
    assert "page.Rotate =" not in source
    assert "page.rotate(180, relative=True)" in source


def test_duplex_modes_offers_manual_always(monkeypatch):
    class _FakeInfo:
        def isNull(self):
            return True

        def supportedDuplexModes(self):
            return []

    monkeypatch.setattr(backend_mod, "_printer_info", lambda name: _FakeInfo())
    monkeypatch.setattr(backend_mod, "_duplex_none_mode", lambda: "duplex-none")
    backend = QtPrintBackend(_profile())
    modes = backend.duplex_modes("Unknown Printer")
    assert modes.manual is True
    assert modes.single_pass_duplex is False


def test_duplex_modes_offers_single_pass_when_hardware_supports_it(monkeypatch):
    class _FakeInfo:
        def isNull(self):
            return False

        def supportedDuplexModes(self):
            return ["duplex-long-side"]

    monkeypatch.setattr(backend_mod, "_printer_info", lambda name: _FakeInfo())
    monkeypatch.setattr(backend_mod, "_duplex_none_mode", lambda: "duplex-none")
    backend = QtPrintBackend(_profile())
    modes = backend.duplex_modes("Real Duplex Printer")
    assert modes.manual is True
    assert modes.single_pass_duplex is True


def test_backend_module_import_does_not_load_qt():
    import sys

    qt_prefixes = ("PySide6", "PyQt5", "PyQt6")
    loaded = {name for name in sys.modules if name.startswith(qt_prefixes)}
    assert not loaded, f"importing deckle.app.backend pulled in Qt eagerly: {sorted(loaded)}"
