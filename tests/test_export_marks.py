"""Tests for mark rendering in deckle.core.export via pikepdf's
ContentStreamBuilder, and for multi-page (``Side``) folio placement.
"""

from __future__ import annotations

import os
import re

import pikepdf
import pytest

from deckle.core.export import export as export_fn
from deckle.core.models import (
    Mark,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourceRef,
)

LETTER = (612.0, 792.0)
LANDSCAPE_LETTER = (792.0, 612.0)


def _write_source_pdf(tmp_path, n_pages: int, page_size=(396.0, 612.0)) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    pdf.close()
    return path


def _make_ref(path: str, page_index: int, width_pt: float, height_pt: float) -> SourceRef:
    return SourceRef(
        path=path,
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _content_bytes(page: pikepdf.Page) -> bytes:
    contents = page.obj.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        return b"".join(s.read_bytes() for s in contents)
    return contents.read_bytes()


def _output_page(ref: SourceRef, tx: float, ty: float, scale: float = 1.0) -> OutputPage:
    return OutputPage(
        source_ref=ref,
        placement=Placement(scale_x=scale, scale_y=scale, tx=tx, ty=ty, rotate_deg=0),
        is_filler=False,
    )


def _stroke_op_count(data: bytes) -> int:
    # ``stroke_and_close`` emits the ``s`` operator (close + stroke).
    return len(re.findall(rb"(?:^|\s)s(?:\s|$)", data))


def _q_do_blocks(data: bytes) -> list[bytes]:
    """Every balanced ``q ... Do ... Q`` block in ``data``."""
    blocks = []
    for match in re.finditer(rb"q\n(.*?)\nQ\n", data, re.DOTALL):
        body = match.group(1)
        if b" Do" in body or body.rstrip().endswith(b"Do"):
            blocks.append(body)
    return blocks


# --- MECHANICAL: marks additive, none when absent -----------------------


def test_side_with_no_marks_exports_no_stroke_ops(tmp_path):
    path = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 396.0, 612.0)
    side = Side(pages=(_output_page(ref, 0.0, 0.0),), marks=())
    plan = SheetPlan(sheets=[Sheet(index=0, front=side, back=None)], paper_pt=LETTER, warnings=[])

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        data = _content_bytes(pdf.pages[0])
        assert _stroke_op_count(data) == 0


# --- BEHAVIORAL: 3 sewing stations + 1 signature order + 1 fold line = 5 strokes


def test_five_marks_produce_five_stroke_operations(tmp_path):
    path = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 396.0, 612.0)
    marks = (
        Mark(kind="sewing_station", x0=10.0, y0=0.0, x1=10.0, y1=18.0),
        Mark(kind="sewing_station", x0=200.0, y0=0.0, x1=200.0, y1=18.0),
        Mark(kind="sewing_station", x0=380.0, y0=0.0, x1=380.0, y1=18.0),
        Mark(kind="signature_order", x0=396.0, y0=300.0, x1=396.0, y1=324.0),
        Mark(kind="fold_line", x0=0.0, y0=306.0, x1=792.0, y1=306.0),
    )
    side = Side(pages=(_output_page(ref, 0.0, 0.0),), marks=marks)
    plan = SheetPlan(sheets=[Sheet(index=0, front=side, back=None)], paper_pt=LETTER, warnings=[])

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        page.contents_coalesce()
        data = page.obj["/Contents"].read_bytes()
        assert _stroke_op_count(data) == 5


# --- BEHAVIORAL: two Form XObjects per folio PDF page --------------------


def test_two_page_side_places_two_form_xobjects_pure_translation(tmp_path):
    path = _write_source_pdf(tmp_path, 2)
    ref_a = _make_ref(path, 0, 396.0, 612.0)
    ref_b = _make_ref(path, 1, 396.0, 612.0)
    side = Side(
        pages=(
            _output_page(ref_a, 0.0, 0.0),
            _output_page(ref_b, 396.0, 0.0),
        ),
        marks=(),
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=side, back=None)],
        paper_pt=LANDSCAPE_LETTER,
        warnings=[],
    )

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        assert len(pdf.pages) == 1
        page = pdf.pages[0]
        page.contents_coalesce()
        data = page.obj["/Contents"].read_bytes()

        blocks = _q_do_blocks(data)
        assert len(blocks) == 2

        matrices = []
        for block in blocks:
            match = re.search(
                rb"([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) cm", block
            )
            assert match is not None, block
            matrices.append(tuple(float(v) for v in match.groups()))

        matrices.sort(key=lambda m: m[4])  # sort by tx
        (a0, b0, c0, d0, tx0, ty0), (a1, b1, c1, d1, tx1, ty1) = matrices

        assert (a0, b0, c0, d0) == (1.0, 0.0, 0.0, 1.0)
        assert (a1, b1, c1, d1) == (1.0, 0.0, 0.0, 1.0)
        assert (tx0, ty0) == (0.0, 0.0)
        assert (tx1, ty1) == (396.0, 0.0)


def test_two_page_side_shares_identical_scale_no_drift(tmp_path):
    path = _write_source_pdf(tmp_path, 2, page_size=(198.0, 306.0))
    ref_a = _make_ref(path, 0, 198.0, 306.0)
    ref_b = _make_ref(path, 1, 198.0, 306.0)
    scale = 2.0
    side = Side(
        pages=(
            _output_page(ref_a, 0.0, 0.0, scale=scale),
            _output_page(ref_b, 396.0, 0.0, scale=scale),
        ),
        marks=(),
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=side, back=None)],
        paper_pt=LANDSCAPE_LETTER,
        warnings=[],
    )

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        page.contents_coalesce()
        data = page.obj["/Contents"].read_bytes()
        blocks = _q_do_blocks(data)
        assert len(blocks) == 2

        for block in blocks:
            match = re.search(
                rb"([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) cm", block
            )
            assert match is not None, block
            a, _b, _c, d, _tx, _ty = (float(v) for v in match.groups())
            assert a == pytest.approx(scale)
            assert d == pytest.approx(scale)


# --- BEHAVIORAL: filler page inside a two-page Side stays blank ----------


def test_filler_page_in_two_page_side_stays_blank_sibling_places(tmp_path):
    path = _write_source_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 396.0, 612.0)
    filler_placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    filler = OutputPage(source_ref=None, placement=filler_placement, is_filler=True)
    side = Side(
        pages=(
            filler,
            _output_page(ref, 396.0, 0.0),
        ),
        marks=(),
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=side, back=None)],
        paper_pt=LANDSCAPE_LETTER,
        warnings=[],
    )

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        page.contents_coalesce()
        data = page.obj["/Contents"].read_bytes()

        blocks = _q_do_blocks(data)
        assert len(blocks) == 1
        match = re.search(
            rb"([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) cm", blocks[0]
        )
        assert match is not None, blocks[0]
        _a, _b, _c, _d, tx, ty = (float(v) for v in match.groups())
        assert (tx, ty) == (396.0, 0.0)


# --- BEHAVIORAL: exported PDF reopens cleanly, expected page count -------


def test_export_with_marks_reopens_cleanly_with_expected_page_count(tmp_path):
    path = _write_source_pdf(tmp_path, 2)
    ref_a = _make_ref(path, 0, 396.0, 612.0)
    ref_b = _make_ref(path, 1, 396.0, 612.0)
    marks = (Mark(kind="fold_line", x0=0.0, y0=306.0, x1=792.0, y1=306.0),)
    front = Side(
        pages=(_output_page(ref_a, 0.0, 0.0), _output_page(ref_b, 396.0, 0.0)),
        marks=marks,
    )
    back = Side(pages=(_output_page(ref_a, 0.0, 0.0),), marks=())
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=front, back=back)],
        paper_pt=LANDSCAPE_LETTER,
        warnings=[],
    )

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        assert len(pdf.pages) == 2
