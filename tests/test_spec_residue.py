"""Acceptance criteria from the deckle-signatures-v2 sub-specs that no other
test file covered.

Each test here is named so that the sub-spec's own ``pytest -k <selector>``
command matches it. The selector is quoted in the docstring alongside the
sub-spec that owns it, so a reader can get from a red test back to the
criterion text without grepping the spec tree.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from pathlib import Path

import pikepdf
import pytest

from deckle.cli import build_parser
from deckle.core import marks as marks_module
from deckle.core.export import export as export_fn
from deckle.core.layout import SaddleStitchStrategy
from deckle.core.models import (
    LayoutSettings,
    Mark,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)
from deckle.core.printing import plan_passes
from deckle.core.profiles import PrinterProfile
from deckle.core.signatures import fold_reading_order

REPO_ROOT = Path(__file__).resolve().parent.parent
LETTER_LANDSCAPE = (792.0, 612.0)
LETTER_PORTRAIT = (612.0, 792.0)


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


# --- SS-04: `reads_only_sheet_index` -----------------------------------
#
# REQ-035. The zero-diff claim over ``deckle/core/printing.py`` rests on a
# single verified fact: ``plan_passes`` reads ``Sheet.index`` and nothing
# else, so growing a ``Sheet`` into two-sided, multi-page, mark-carrying
# folio sides cannot reach it. Both halves below assert that fact -- once
# at runtime, once statically -- because either alone can be defeated.


class _TripwireSheet:
    """A ``Sheet`` stand-in that explodes on any attribute but ``index``.

    ``front``/``back``/``marks`` are precisely the attributes the folio work
    added content behind; reading any of them from ``plan_passes`` is the
    zero-diff violation this catches.
    """

    def __init__(self, index: int) -> None:
        object.__setattr__(self, "index", index)

    def __getattribute__(self, name: str):
        if name != "index" and not name.startswith("__"):
            raise AssertionError(
                f"plan_passes read Sheet.{name}; it must read only Sheet.index "
                "-- the zero-diff seam over printing.py is broken. ESCALATE."
            )
        return object.__getattribute__(self, name)


def test_plan_passes_reads_only_sheet_index_at_runtime():
    plan = SheetPlan(
        sheets=[_TripwireSheet(i) for i in range(4)],
        paper_pt=LETTER_PORTRAIT,
        warnings=[],
    )

    passes = plan_passes(plan, _profile(reverse_stack=True))

    assert [p.sheet_order for p in passes] == [[0, 1, 2, 3], [3, 2, 1, 0]]


def test_plan_passes_reads_only_sheet_index_by_ast():
    source = inspect.getsource(plan_passes)
    func_def = ast.parse(source).body[0]
    assert isinstance(func_def, ast.FunctionDef)

    # Names bound to an element of ``plan.sheets``, via either a for loop
    # or a comprehension over it.
    sheet_element_names: set[str] = set()

    def _iterates_sheets(node: ast.expr) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "sheets"

    def _bind(target: ast.expr) -> None:
        for name_node in ast.walk(target):
            if isinstance(name_node, ast.Name):
                sheet_element_names.add(name_node.id)

    for node in ast.walk(func_def):
        if isinstance(node, ast.For) and _iterates_sheets(node.iter):
            _bind(node.target)
        for generator in getattr(node, "generators", []):
            if _iterates_sheets(generator.iter):
                _bind(generator.target)

    assert sheet_element_names, (
        "no iteration over plan.sheets found in plan_passes -- the AST guard "
        "has stopped guarding anything; fix the walk, do not delete the test"
    )

    read_attributes = {
        node.attr
        for node in ast.walk(func_def)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in sheet_element_names
    }
    assert read_attributes == {"index"}, (
        f"plan_passes reads {sorted(read_attributes)} off a Sheet; only "
        "'index' is permitted. ESCALATE -- do not relax this test."
    )


# --- SS-05: `fold_reading_order_works_with_saddle_order_disabled` -------
#
# REQ-018. ``tests/test_signatures.py`` already proves ``fold_reading_order``
# carries no *static* reference to ``saddle_order``. This is the runtime
# complement: with ``saddle_order`` replaced by a landmine, the fold
# simulator must still return the correct order -- catching indirect reuse
# (a lookup through the module object, an alias, a helper) that the AST walk
# cannot see.


def _plan_with_signatures(sheet_counts_per_signature) -> SheetPlan:
    from deckle.core.models import Signature

    signatures = []
    start = 0
    for index, sheet_count in enumerate(sheet_counts_per_signature):
        signatures.append(
            Signature(
                index=index,
                sheet_indices=tuple(range(start, start + sheet_count)),
                blank_count=0,
            )
        )
        start += sheet_count
    return SheetPlan(
        sheets=[],
        paper_pt=LETTER_PORTRAIT,
        warnings=[],
        signatures=tuple(signatures),
    )


def test_fold_reading_order_works_with_saddle_order_disabled(monkeypatch):
    from deckle.core import signatures as signatures_module

    def _boom(*args, **kwargs):
        raise AssertionError("fold_reading_order must not use saddle_order")

    monkeypatch.setattr(signatures_module, "saddle_order", _boom)

    # Two sheets in one signature -> 8 pages. The expected order is the
    # vault-pinned n=8 answer, spelled out literally rather than by calling
    # saddle_order, which is disabled for the duration of this test.
    assert fold_reading_order(_plan_with_signatures([2])) == [7, 0, 1, 6, 5, 2, 3, 4]

    # And across signatures, with the second signature's slots offset.
    assert fold_reading_order(_plan_with_signatures([2, 1])) == [
        7, 0, 1, 6, 5, 2, 3, 4,
        11, 8, 9, 10,
    ]


# --- SS-06: `every_public_function_returns_only_Mark_values` ------------
#
# Walks the module's public callables rather than naming three functions, so
# a fourth mark constructor that returned, say, a raw tuple of floats would
# fail this the day it was added.

_MARKS_CALL_ARGS = {
    "sheet_h": 612.0,
    "fold_x": 396.0,
    "count": 3,
    "sig_index": 1,
    "sig_count": 3,
}


def _public_marks_functions() -> list:
    return [
        obj
        for name, obj in vars(marks_module).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == marks_module.__name__
    ]


def test_every_public_function_returns_only_Mark_values():
    functions = _public_marks_functions()
    assert {f.__name__ for f in functions} >= {
        "sewing_stations",
        "signature_order_mark",
        "fold_line",
    }, "marks.py lost a public geometry function -- this test would go vacuous"

    for func in functions:
        parameters = inspect.signature(func).parameters
        unknown = [p for p in parameters if p not in _MARKS_CALL_ARGS]
        assert not unknown, (
            f"{func.__name__} takes unknown parameter(s) {unknown}; add them to "
            "_MARKS_CALL_ARGS so this test keeps covering the whole module"
        )
        result = func(**{p: _MARKS_CALL_ARGS[p] for p in parameters})

        values = result if isinstance(result, tuple) else (result,)
        assert values or isinstance(result, tuple), (
            f"{func.__name__} returned {result!r}, neither a Mark nor a tuple"
        )
        for value in values:
            assert isinstance(value, Mark), (
                f"{func.__name__} returned {value!r} ({type(value).__name__}); "
                "every public function in marks.py returns Mark values only"
            )


# --- SS-08: `never_rotates` --------------------------------------------
#
# Folio was chosen precisely because it needs no rotation: the vault recipe's
# two placement matrices are `1 0 0 1 0 0 cm` and `1 0 0 1 396 0 cm`, both
# pure translations. Every leaf the saddle imposer places must therefore
# carry rotate_deg == 0 -- including under portrait paper, which warns rather
# than rotating.


def _source_pages(n: int, w: float = 396.0, h: float = 612.0) -> list[SourcePage]:
    return [
        SourcePage(
            ref=SourceRef(
                path="book.pdf", page_index=i, sha256="a" * 64, width_pt=w, height_pt=h
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _folio_settings(**overrides) -> LayoutSettings:
    kwargs = dict(
        paper=LETTER_LANDSCAPE,
        gutter_pt=18.0,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=4,
    )
    kwargs.update(overrides)
    return LayoutSettings(**kwargs)


@pytest.mark.parametrize(
    "settings_overrides",
    [
        {},
        {"binding_edge": "right"},
        {"blank_mode": "balanced"},
        {"paper": LETTER_PORTRAIT},
    ],
)
def test_folio_never_rotates_a_leaf(settings_overrides):
    plan = SaddleStitchStrategy().impose(
        _source_pages(40), _folio_settings(**settings_overrides)
    )

    placed = 0
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            for page in side.pages:
                assert page.placement.rotate_deg == 0, (
                    f"sheet {sheet.index} carries a rotated leaf "
                    f"({page.placement.rotate_deg} deg); the folio path must "
                    "place by pure translation only"
                )
                placed += 1

    assert placed == 40, f"expected 40 placed leaves, counted {placed}"


# --- SS-09: `dashed` ---------------------------------------------------
#
# Fold lines dash, sewing stations and signature order bars do not. Counting
# stroke operators cannot tell those apart -- this reads the `d` (set-dash)
# operator out of the exported content stream and ties each dash state to the
# coordinates of the mark it belongs to, so making both kinds solid, both
# dashed, or swapping them all fail.

_MARK_BLOCK_RE = re.compile(
    rb"q\n0\.5 w\n(?P<dash>\[[^\]]*\] \d+ d)\n"
    rb"(?P<x0>[\d.]+) (?P<y0>[\d.]+) m\n(?P<x1>[\d.]+) (?P<y1>[\d.]+) l\ns\nQ"
)

_DASHED = b"[ 3 3 ] 0 d"
_SOLID = b"[ ] 0 d"


def _write_source_pdf(tmp_path, n_pages: int) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(396.0, 612.0))
    pdf.save(path)
    pdf.close()
    return path


def _coalesced_content(pdf_path: str, page_index: int = 0) -> bytes:
    with pikepdf.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        page.contents_coalesce()
        return page.obj["/Contents"].read_bytes()


def test_fold_lines_are_dashed_and_other_marks_are_not(tmp_path):
    source = _write_source_pdf(tmp_path, 1)
    ref = SourceRef(
        path=source, page_index=0, sha256="a" * 64, width_pt=396.0, height_pt=612.0
    )
    page = OutputPage(
        source_ref=ref,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=False,
    )
    # Distinct y0 per mark so each stroked segment is attributable to the
    # mark that produced it.
    fold = Mark(kind="fold_line", x0=396.0, y0=0.0, x1=396.0, y1=612.0)
    station = Mark(kind="sewing_station", x0=378.0, y0=36.0, x1=414.0, y1=36.0)
    order_bar = Mark(kind="signature_order", x0=396.0, y0=100.0, x1=396.0, y1=124.0)

    plan = SheetPlan(
        sheets=[
            Sheet(
                index=0,
                front=Side(pages=(page,), marks=(fold, station, order_bar)),
                back=None,
            )
        ],
        paper_pt=LETTER_LANDSCAPE,
        warnings=[],
    )
    out_path = os.path.join(str(tmp_path), "dash.pdf")
    export_fn(plan, out_path)

    data = _coalesced_content(out_path)
    blocks = {
        (float(m.group("y0")), float(m.group("y1"))): m.group("dash")
        for m in _MARK_BLOCK_RE.finditer(data)
    }
    assert len(blocks) == 3, (
        f"expected 3 stroked mark blocks, parsed {len(blocks)} from {data!r}"
    )

    assert blocks[(0.0, 612.0)] == _DASHED, "the fold line must be dashed"
    assert blocks[(36.0, 36.0)] == _SOLID, "a sewing station must be solid"
    assert blocks[(100.0, 124.0)] == _SOLID, "a signature order bar must be solid"


def test_a_markless_side_emits_no_dashed_or_solid_dash_operator(tmp_path):
    """Guards the test above against a stream that always carries both
    patterns regardless of the marks present.
    """
    source = _write_source_pdf(tmp_path, 1)
    ref = SourceRef(
        path=source, page_index=0, sha256="a" * 64, width_pt=396.0, height_pt=612.0
    )
    page = OutputPage(
        source_ref=ref,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=False,
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,), marks=()), back=None)],
        paper_pt=LETTER_LANDSCAPE,
        warnings=[],
    )
    out_path = os.path.join(str(tmp_path), "nodash.pdf")
    export_fn(plan, out_path)

    data = _coalesced_content(out_path)
    assert _DASHED not in data
    assert _SOLID not in data


# --- SS-10: `denylist_names` -------------------------------------------
#
# Deckle is MIT. ``pdfimpose`` and its ``cpdf`` backend are AGPL-3.0/
# commercial and pull AGPL PyMuPDF (import name ``fitz``); each is a live
# temptation during imposition work and none may ever become a dependency.


def test_denylist_names_every_undistributable_tool():
    from tests.test_license_audit import FORBIDDEN_DISTRIBUTIONS

    assert {"pymupdf", "fitz", "pdfimpose", "cpdf"} <= FORBIDDEN_DISTRIBUTIONS


def test_denylist_names_are_not_listed_as_project_dependencies():
    """The denylist and ``pyproject.toml`` must never contradict each other."""
    from tests.test_license_audit import FORBIDDEN_DISTRIBUTIONS

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    for name in sorted(FORBIDDEN_DISTRIBUTIONS):
        assert name not in pyproject, f"{name} named in pyproject.toml"


# --- SS-12: `cli_accepts_folio_flags` ----------------------------------


FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


def test_cli_accepts_folio_flags():
    args = build_parser().parse_args(
        [
            "info",
            FIXTURE,
            "--fold-scheme", "folio",
            "--sheets-per-signature", "8",
            "--blank-mode", "balanced",
            "--sewing-stations", "0",
        ]
    )

    assert args.fold_scheme == "folio"
    assert args.sheets_per_signature == 8
    assert args.blank_mode == "balanced"
    assert args.sewing_stations == 0


@pytest.mark.parametrize("subcommand", ["info", "impose", "export"])
def test_cli_accepts_folio_flags_on_every_layout_subcommand(subcommand, tmp_path):
    argv = [subcommand, FIXTURE]
    if subcommand != "info":
        argv += ["-o", os.path.join(str(tmp_path), "out.bin")]
    argv += [
        "--fold-scheme", "folio",
        "--sheets-per-signature", "2",
        "--blank-mode", "end",
        "--sewing-stations", "3",
    ]

    args = build_parser().parse_args(argv)

    assert args.fold_scheme == "folio"
    assert args.sheets_per_signature == 2
    assert args.blank_mode == "end"
    assert args.sewing_stations == 3
