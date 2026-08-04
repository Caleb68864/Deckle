"""Integration wiring tests: no orphaned views, and a full end-to-end flow.

The orphan-view test enumerates every module under ``deckle/app/views/`` and
asserts each is imported by ``deckle/app/main.py`` -- the classic failure
where a view is built and tested in isolation but never reachable from the
running app.

The end-to-end test drives the full pipeline (import PDF -> import image
directory -> reorder -> set gutter -> impose -> preview -> export -> plan
passes -> submit) against a stubbed ``PrintBackend``, crossing every
sub-spec boundary on purpose.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from dataclasses import dataclass, field
from typing import Sequence

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import deckle.app.views
from deckle.app.main import default_project
from deckle.app.state import AppState
from deckle.app.views.arrange_view import reorder
from deckle.app.views.import_view import load_and_apply_import
from deckle.app.views.layout_panel import apply_layout_change, set_gutter_pt
from deckle.app.views.preview_view import build_preview_frame
from deckle.core.export import export
from deckle.core.print_session import PrintSession
from deckle.core.printing import PrintResult, plan_passes

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_PDF = os.path.join(FIXTURES_DIR, "sample.pdf")


# -- orphan-view test ------------------------------------------------------


def _view_module_names() -> list[str]:
    names = []
    for module_info in pkgutil.iter_modules(deckle.app.views.__path__):
        if module_info.name.startswith("_"):
            continue
        names.append(f"deckle.app.views.{module_info.name}")
    return names


def test_orphaned_views_every_view_module_is_imported_by_main():
    main_source_path = importlib.import_module("deckle.app.main").__file__
    with open(main_source_path, encoding="utf-8") as f:
        main_source = f.read()

    view_modules = _view_module_names()
    assert view_modules, "expected at least one view module under deckle/app/views/"

    orphaned = [
        name for name in view_modules if name.rsplit(".", 1)[-1] not in main_source
    ]
    assert not orphaned, f"deckle/app/main.py never imports: {orphaned}"


# -- end-to-end flow ---------------------------------------------------------


def _profile(**overrides):
    from deckle.core.profiles import PrinterProfile

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


@pytest.fixture(autouse=True)
def _isolated_session_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "sessions"))


def _make_image_dir(tmp_path) -> str:
    from PIL import Image

    dir_path = tmp_path / "images"
    dir_path.mkdir()
    for i in range(3):
        image = Image.new("RGB", (200, 300), color=(10 * i, 20, 30))
        image.save(dir_path / f"page{i + 1}.png", dpi=(150, 150))
    return str(dir_path)


def test_end_to_end_import_arrange_layout_preview_export_print(tmp_path):
    state = AppState(default_project())

    # 1. import a PDF
    pdf_pages, _pdf_warnings = load_and_apply_import(state, SAMPLE_PDF)
    assert len(state.project.pages) == len(pdf_pages) > 0

    # 2. import an image directory (replaces the source, matching
    # load_and_apply_import's documented behavior)
    image_dir = _make_image_dir(tmp_path)
    image_pages, _image_warnings = load_and_apply_import(state, image_dir)
    assert len(state.project.pages) == len(image_pages) == 3

    # 3. reorder: move the last imported page to the front
    reorder(state, 2, 0)
    assert state.project.pages[0] == image_pages[2]

    # 4. set gutter, 5. impose -- apply_layout_change mutates and
    # immediately recomputes the whole-document SheetPlan
    plan = apply_layout_change(state, lambda project: set_gutter_pt(project, 24.0))
    assert state.project.layout.gutter_pt == 24.0
    assert plan.sheets

    # 6. preview -- render exactly the sheet on screen
    profile = _profile()
    frame = build_preview_frame(plan, profile, plan.sheets[0].index, "front")
    assert frame.rendered.width > 0
    assert frame.rendered.height > 0

    # 7. export
    out_path = str(tmp_path / "out.pdf")
    export(plan, out_path)
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 0

    # 8. plan passes
    passes = plan_passes(plan, profile)
    assert len(passes) == 2
    assert passes[0].side == "front"
    assert passes[1].side == "back"

    # 9. submit through a stubbed PrintBackend
    backend = _StubPrintBackend()
    session = PrintSession(plan, profile, backend, printer_name="Test Printer")
    session.start()
    while not session.finished:
        assert session.last_error is None
        session.advance()

    expected_front_order = [s.index for s in plan.sheets]
    expected_back_order = list(reversed(expected_front_order)) if profile.reverse_stack else expected_front_order
    assert backend.calls[0] == expected_front_order
    assert backend.calls[-1] == expected_back_order

    # Final SheetPlan assertions -- one sheet per two pages, no double
    # padding of an already-even three-page-plus-reorder document.
    assert plan.paper_pt == state.project.layout.paper
    assert len(plan.sheets) == (len(state.project.pages) + 1) // 2
