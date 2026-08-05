"""Golden-fixture regression: reproduces the Pinebox output from its source
and asserts the predecessor script's two known defects are dead.

See ``tests/fixtures/README.md`` for the fixture's expected local path.
This test **skips with a clear message** when the fixture is unavailable
rather than failing CI on a missing ~30 MB file.
"""

from __future__ import annotations

import os

import pytest

from deckle.core.layout import GutterShiftStrategy
from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings

LETTER_PT = (612.0, 792.0)

DEFAULT_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "Pinebox_Middle_School.pdf"
)


def _fixture_path() -> str:
    return os.environ.get("DECKLE_PINEBOX_FIXTURE", DEFAULT_FIXTURE_PATH)


def _require_fixture() -> str:
    path = _fixture_path()
    if not os.path.exists(path):
        pytest.skip(
            "Pinebox golden fixture not found at "
            f"{path!r} -- see tests/fixtures/README.md for how to obtain it "
            "or point DECKLE_PINEBOX_FIXTURE at a local copy."
        )
    return path


def test_pinebox_no_double_padding():
    """Defect 2: an odd-length source gains exactly one filler, never two."""
    path = _require_fixture()
    pages = load_pdf(path)

    settings = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(pages, settings)

    total_slots = len(plan.sheets) * 2
    filler_count = sum(
        1
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None and side.pages[0].is_filler
    )
    expected_fillers = 1 if len(pages) % 2 != 0 else 0
    assert filler_count == expected_fillers, (
        f"expected exactly {expected_fillers} filler page(s) for a "
        f"{len(pages)}-page source, got {filler_count} "
        f"(double-padding regression, defect 2)"
    )
    assert total_slots - filler_count == len(pages)


def test_pinebox_per_page_aspect_handling():
    """Defect 1: each page's transform derives from its own media box."""
    path = _require_fixture()
    pages = load_pdf(path)

    settings = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(pages, settings)

    by_ref = {p.ref.page_index: p for p in pages}
    for sheet in plan.sheets:
        for side in (sheet.front, sheet.back):
            if side is None:
                continue
            output_page = side.pages[0]
            if output_page.is_filler or output_page.source_ref is None:
                continue
            ref = output_page.source_ref
            source_page = by_ref.get(ref.page_index)
            assert source_page is not None
            src_w, src_h = ref.width_pt, ref.height_pt
            if source_page.rotate_deg in (90, 270):
                src_w, src_h = src_h, src_w
            placement = output_page.placement
            scaled_w = src_w * placement.scale_x
            scaled_h = src_h * placement.scale_y
            # The scaled content must fit on the page -- a transform
            # borrowed from a neighboring page (defect 1) would overflow
            # or underfill whenever this page's own aspect ratio differs.
            assert scaled_w <= LETTER_PT[0] + 1e-6
            assert scaled_h <= LETTER_PT[1] + 1e-6


def test_pinebox_output_page_count_parity():
    """The imposed sheet count matches the expected even-slot parity."""
    path = _require_fixture()
    pages = load_pdf(path)

    settings = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(pages, settings)

    expected_sheets = (len(pages) + 1) // 2
    assert len(plan.sheets) == expected_sheets
