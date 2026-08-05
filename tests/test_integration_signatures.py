"""End-to-end integration for the v2 signature/saddle-stitch feature.

The MVP's ``tests/test_integration.py`` already proves the gutter-shift
path end to end; this file proves the same thing for
``fold_scheme="folio"``: a numbered 32-page source, imposed as
saddle-stitch signatures, exported to a real PDF, round-tripped through
the independently-derived fold simulator, and submitted through a
stubbed ``PrintBackend`` -- plus a wiring check that ``signatures.py`` and
``marks.py`` are actually invoked along that path, not merely importable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence
from unittest import mock

import pikepdf

import deckle.core.export as export_module
import deckle.core.layout as layout_module
import deckle.core.marks as marks_module
import deckle.core.signatures as signatures_module
from deckle.core.export import export
from deckle.core.layout import SaddleStitchStrategy
from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings
from deckle.core.printing import PrintResult, plan_passes
from deckle.core.profiles import PrinterProfile
from deckle.core.signatures import fold_reading_order

LANDSCAPE_LETTER = (792.0, 612.0)


def _make_numbered_pdf(tmp_path, n_pages: int) -> str:
    """A real ``n_pages``-page PDF, pages ordered ``0..n_pages-1``.

    Each page is a distinct blank page (portrait, half the folio sheet
    width) -- distinct ``pikepdf.Pdf.new()`` blank pages so
    ``load_pdf``'s page order matches the file's own page order exactly,
    which is what the reading-order assertions below depend on.
    """
    path = os.path.join(str(tmp_path), "numbered.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(396.0, 612.0))
    pdf.save(path)
    pdf.close()
    return path


def _settings(**overrides) -> LayoutSettings:
    kwargs = dict(
        paper=LANDSCAPE_LETTER,
        gutter_pt=0.0,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=4,
    )
    kwargs.update(overrides)
    return LayoutSettings(**kwargs)


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


@dataclass
class _StubPrintBackend:
    """Records every submission it receives; never touches a real printer."""

    calls: list[list[int]] = field(default_factory=list)

    def submit(
        self,
        plan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
    ) -> PrintResult:
        self.calls.append(list(sheets))
        return PrintResult(submitted=len(sheets), job_id=f"job-{len(self.calls)}", error=None)


def _resource_xobject_count(page: pikepdf.Page) -> int:
    resources = page.obj.get("/Resources")
    if resources is None:
        return 0
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return 0
    return len(xobjects.keys())


def _flat_output_pages(plan):
    """Every ``OutputPage`` in the plan, in physical print-slot order."""
    flat = []
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            flat.extend(side.pages)
    return flat


def _reconstruct_reading_order(plan):
    """Scatter print-slot ``OutputPage``s into reading-order positions via
    ``fold_reading_order`` -- the same technique
    ``tests/test_layout_saddle.py`` uses, applied here to a *real*, exported
    plan rather than a synthetic one."""
    order = fold_reading_order(plan)
    flat = _flat_output_pages(plan)
    assert len(order) == len(flat)
    result = [None] * len(order)
    for reading_pos, output_page in zip(order, flat):
        result[reading_pos] = output_page
    return result


# -- the end-to-end fold round trip -----------------------------------------


def test_end_to_end_folio_signature_impose_export_fold_and_print(tmp_path):
    source_path = _make_numbered_pdf(tmp_path, 32)
    pages = load_pdf(source_path)
    assert len(pages) == 32

    settings = _settings(sheets_per_signature=4)
    plan = SaddleStitchStrategy().impose(pages, settings)

    # 32 pages -> 8 sheets (4 pages/sheet), grouped 4 sheets/signature -> 2
    # signatures, no padding blanks needed (32 is already a multiple of 16).
    assert len(plan.sheets) == 8
    assert len(plan.signatures) == 2
    assert sum(sig.blank_count for sig in plan.signatures) == 0

    out_path = str(tmp_path / "signatures.pdf")
    export(plan, out_path)
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 0

    with pikepdf.open(out_path) as exported:
        assert len(exported.pages) == 16  # 8 sheets x 2 sides
        for page in exported.pages:
            assert _resource_xobject_count(page) == 2  # two folio cells per side

    # The fold simulator, closing over the *real* exported plan (not a
    # synthetic one), must reconstruct the exact reading-order page
    # sequence -- REQ-019.
    reconstructed = _reconstruct_reading_order(plan)
    reconstructed_page_indices = [op.source_ref.page_index for op in reconstructed]
    assert reconstructed_page_indices == list(range(32))

    # Print only the second signature's sheets -- the existing
    # ``plan_passes(..., sheets=...)`` subset path, unmodified.
    second_sig_sheets = plan.signatures[1].sheet_indices
    profile = _profile()
    passes = plan_passes(plan, profile, sheets=second_sig_sheets)
    assert len(passes) == 2
    assert passes[0].sheet_order == list(second_sig_sheets)

    backend = _StubPrintBackend()
    for pass_ in passes:
        result = backend.submit(plan, pass_.sheet_order, "Test Printer", 1, 300)
        assert result.error is None

    assert backend.calls[0] == list(second_sig_sheets)
    assert backend.calls[1] == list(reversed(second_sig_sheets))


# -- no orphaned modules: signatures.py and marks.py are actually invoked ---


def test_signatures_and_marks_modules_are_invoked_and_marks_consumed_by_export(tmp_path):
    source_path = _make_numbered_pdf(tmp_path, 16)
    pages = load_pdf(source_path)
    settings = _settings(sheets_per_signature=4)

    with (
        mock.patch.object(
            layout_module, "split_signatures",
            wraps=signatures_module.split_signatures,
        ) as mock_split_signatures,
        mock.patch.object(
            layout_module, "saddle_order", wraps=signatures_module.saddle_order,
        ) as mock_saddle_order,
        mock.patch.object(
            layout_module, "fold_line", wraps=marks_module.fold_line,
        ) as mock_fold_line,
        mock.patch.object(
            layout_module, "sewing_stations", wraps=marks_module.sewing_stations,
        ) as mock_sewing_stations,
        mock.patch.object(
            layout_module, "signature_order_mark",
            wraps=marks_module.signature_order_mark,
        ) as mock_signature_order_mark,
        mock.patch.object(
            export_module, "_draw_marks", wraps=export_module._draw_marks,
        ) as mock_draw_marks,
    ):
        plan = SaddleStitchStrategy().impose(pages, settings)
        out_path = str(tmp_path / "wiring.pdf")
        export(plan, out_path)

    # deckle/core/signatures.py -- invoked from SaddleStitchStrategy.impose.
    assert mock_split_signatures.called, "signatures.split_signatures was never invoked"
    assert mock_saddle_order.called, "signatures.saddle_order was never invoked"

    # deckle/core/marks.py -- invoked from SaddleStitchStrategy.impose.
    assert mock_fold_line.called, "marks.fold_line was never invoked"
    assert mock_sewing_stations.called, "marks.sewing_stations was never invoked"
    assert mock_signature_order_mark.called, "marks.signature_order_mark was never invoked"

    # marks.py's output (non-empty Mark tuples on at least one Side) is
    # consumed by export.py's _draw_marks during composition.
    assert mock_draw_marks.called, "export._draw_marks was never invoked"
    marks_seen = [
        call.args[1] for call in mock_draw_marks.call_args_list if len(call.args) > 1
    ]
    assert any(marks_seen), "export._draw_marks was never called with non-empty marks"


def test_fold_reading_order_matches_saddle_order_independently_for_the_real_plan(tmp_path):
    """A second, narrower proof that the round trip holds for a smaller,
    non-power-of-4 page count needing padding blanks."""
    source_path = _make_numbered_pdf(tmp_path, 30)  # pads to 32 (2 blanks)
    pages = load_pdf(source_path)
    settings = _settings(sheets_per_signature=2)
    plan = SaddleStitchStrategy().impose(pages, settings)

    reconstructed = _reconstruct_reading_order(plan)
    real_pages = [op for op in reconstructed if op is not None and not op.is_filler]
    real_page_indices = [op.source_ref.page_index for op in real_pages]
    assert real_page_indices == list(range(30))
    blank_pages = [op for op in reconstructed if op is not None and op.is_filler]
    assert len(blank_pages) == 2
