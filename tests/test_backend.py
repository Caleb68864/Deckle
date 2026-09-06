"""Tests for QtPrintBackend: chunking, rotation, logging, cancellation."""

from __future__ import annotations

import pytest

from deckle.app import backend as backend_mod
from deckle.app.backend import DEFAULT_CHUNK_SIZE, QtPrintBackend, _chunked, _render_sheet_side
from deckle.core import export as export_module
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side, SourceRef
from deckle.core.printing import PrintPass
from deckle.core.profiles import PrinterProfile


@pytest.fixture(autouse=True)
def _printer_always_present(monkeypatch):
    """Stub the pre-submission printer-existence check.

    ``submit()`` verifies the printer still exists before painting (a
    printer can vanish between enumeration and submission). That check
    reaches QPrinterInfo, which these tests deliberately never touch --
    they are about chunking and rotation, and none of them may pull Qt in.
    ``tests/test_hardening_printing.py`` exercises the check itself.
    """
    monkeypatch.setattr(backend_mod, "printer_is_available", lambda name: True)


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


def test_render_sheet_side_paints_every_page_in_a_two_page_side(tmp_path, monkeypatch):
    import pikepdf

    src = tmp_path / "src.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(300.0, 400.0))
        pdf.add_blank_page(page_size=(300.0, 400.0))
        pdf.save(str(src))

    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    ref0 = SourceRef(path=str(src), page_index=0, sha256="a" * 64, width_pt=150.0, height_pt=200.0)
    ref1 = SourceRef(path=str(src), page_index=1, sha256="b" * 64, width_pt=150.0, height_pt=200.0)
    side = Side(
        pages=(
            OutputPage(source_ref=ref0, placement=placement, is_filler=False),
            OutputPage(source_ref=ref1, placement=placement, is_filler=False),
        )
    )
    sheet = Sheet(index=0, front=side, back=None)
    plan = SheetPlan(sheets=[sheet], paper_pt=(300.0, 400.0), warnings=[])

    calls = []
    real_place = export_module._place_output_page

    def spy_place(sheet_pdf, dest_page, output_page, source_cache):
        calls.append(output_page)
        return real_place(sheet_pdf, dest_page, output_page, source_cache)

    monkeypatch.setattr(export_module, "_place_output_page", spy_place)

    rendered = _render_sheet_side(plan, 0, "front", 72, False, False)

    # A two-page side must produce a raster carrying content from both
    # source pages -- asserted by placement count, not a pixel diff.
    assert len(calls) == 2
    assert rendered.width > 0
    assert rendered.height > 0


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
    """Older qpdf has mishandled a direct assignment to the rotation key,
    and a relative turn is the only correct one for a page that already
    carries a rotation.

    Inspects ``export.rotate_pages_180``, which is where the operation now
    lives: the CLI needs it for ``--pass back`` and cannot import the Qt
    layer. ``backend._apply_rotate_backs`` delegates to it, so this guards
    the code that actually runs in both callers.
    """
    import inspect

    from deckle.core import export as export_mod

    source = inspect.getsource(export_mod.rotate_pages_180)
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
    """Importing the backend must not drag Qt in with it.

    In a subprocess that imports nothing else. Read from the live
    ``sys.modules`` this asserted that *no test in the whole session* had
    imported Qt yet -- so it passed on alphabetical luck and broke the
    moment a test file sorting before this one used a widget, which says
    nothing at all about the backend. Same fix, and same reason, as
    ``test_core_purity``.
    """
    import subprocess
    import sys

    source = (
        "import sys; import deckle.app.backend; "
        "print(sorted(n for n in sys.modules "
        "if n.startswith(('PySide6', 'PyQt5', 'PyQt6'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing deckle.app.backend pulled in Qt eagerly: {result.stdout.strip()}"
    )


def test_the_protocol_carries_the_side():
    """The Protocol must describe the arguments a pass actually needs.

    ``PrintBackend`` declared only ``(plan, sheets, printer_name, copies,
    dpi)`` while ``QtPrintBackend.submit`` had three keyword-only extras
    with front-side defaults. ``PrintSession`` was written against the
    Protocol, so it submitted five positional arguments and every back pass
    silently painted fronts, unturned, with no registration offset.

    Every stub backend in the suite mirrored the narrow signature, so
    nothing could observe the omission -- which is why this asserts on the
    declaration rather than on behaviour. A Protocol narrower than its only
    implementation hides the seam between them, and this is what keeps the
    two in step.
    """
    import inspect

    from deckle.core.printing import PrintBackend

    protocol = inspect.signature(PrintBackend.submit).parameters
    concrete = inspect.signature(QtPrintBackend.submit).parameters

    for name, default in (("side", "front"), ("rotate_backs", False), ("pass_index", 0)):
        assert name in protocol, (
            f"PrintBackend.submit does not declare {name!r}, so a caller "
            "written against the Protocol cannot pass it"
        )
        assert protocol[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert protocol[name].default == default
        assert name in concrete
        assert concrete[name].default == default, (
            f"{name!r} defaults to {concrete[name].default!r} on the backend "
            f"and {protocol[name].default!r} on the Protocol"
        )


def test_the_session_tells_the_backend_which_side_to_paint():
    """The caller that broke, pinned at its call site.

    ``PrintSession._submit_sheets`` holds the ``PrintPass`` and must pass
    its ``side``, ``rotate_backs`` and ``index`` on. It deliberately does
    *not* delegate to ``submit_pass``: that submits a whole pass at once,
    and the cursor has to advance per chunk for resume to land on a sheet.
    So the keywords are threaded by hand here, and that is worth a guard.
    """
    import inspect

    from deckle.core.print_session import PrintSession

    source = inspect.getsource(PrintSession._submit_sheets)

    for fragment in ("side=pass_.side", "rotate_backs=pass_.rotate_backs",
                     "pass_index=pass_.index"):
        assert fragment in source, (
            f"PrintSession._submit_sheets does not pass {fragment!r}; a back "
            "pass submitted without it paints fronts"
        )
