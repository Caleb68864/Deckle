"""The app can keep a margin back from the ink, as ``--auto-crop-margin`` can.

The owner's rule of 2026-09-11 (``../../ROADMAP.md``, decision 7): anything
a user can change by any route and cannot change in the app is in scope.
``--auto-crop-margin`` was one: **Measure crop from the ink** always
measured with a margin of zero, so the descender or hairline rule the
GUIDE tells you to buy back with the flag could only be bought back from
a terminal.

The same rule says a control that changes nothing is the defect, not a
smaller version of the fix. So the test that matters here measures a real
PDF through the panel and checks the crop boxes moved by the margin --
not that a spinbox exists.
"""

from __future__ import annotations

import os

import pikepdf
import pytest
from pikepdf.canvas import ContentStreamBuilder

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from deckle.app.workers import run_inline  # noqa: E402
from deckle.core.models import (  # noqa: E402
    LayoutSettings, Project, SourcePage, SourceRef,
)
from deckle.core.render import clear_ink_bbox_cache  # noqa: E402

PAGE_W, PAGE_H = 400.0, 600.0
#: Ink well inside every edge, so a margin of several points does not
#: clamp at zero anywhere.
INK = (60.0, 80.0, 340.0, 520.0)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_ink_bbox_cache()
    yield
    clear_ink_bbox_cache()


def _inked_pdf(tmp_path) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(2):
        page = pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
        x0, y0, x1, y1 = INK
        b = ContentStreamBuilder()
        b.push()
        b.append_rectangle(x0, y0, x1 - x0, y1 - y0)
        b.fill()
        b.pop()
        page.contents_add(b"q\n" + b.build() + b"Q\n")
    pdf.save(path)
    pdf.close()
    return path


def _panel(path):
    from deckle.app.state import AppState
    from deckle.app.views.layout_panel import LayoutPanel

    pages = [
        SourcePage(
            ref=SourceRef(path=path, page_index=i, sha256="a" * 64,
                          width_pt=PAGE_W, height_pt=PAGE_H),
            rotate_deg=0, skipped=False,
        )
        for i in range(2)
    ]
    panel = LayoutPanel(AppState(Project(
        pages=pages,
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    )))
    panel._job_runner = run_inline
    panel._schedule_composite = lambda: None
    return panel


def _measured(panel, margin_pt: float):
    panel.auto_crop_margin_spinbox.set_points(margin_pt)
    panel._on_auto_crop()
    return panel.state.project.layout.crop_odd_pt, panel.state.project.layout.crop_even_pt


def test_the_margin_box_moves_the_measured_crop_back_from_the_ink(qapp, tmp_path):
    """End to end: a real raster, through the real job, into the project."""
    path = _inked_pdf(tmp_path)

    tight_odd, tight_even = _measured(_panel(path), 0.0)
    clear_ink_bbox_cache()
    loose_odd, loose_even = _measured(_panel(path), 9.0)

    assert tight_odd is not None and tight_even is not None
    for tight, loose in ((tight_odd, loose_odd), (tight_even, loose_even)):
        assert loose == pytest.approx(tuple(t - 9.0 for t in tight))


def test_the_margin_is_read_in_the_unit_the_panel_shows(qapp, monkeypatch, tmp_path):
    """Typed in mm, measured in points -- the box is a length like the rest."""
    from deckle.core import render

    seen = []

    def _record(pages, **kwargs):
        seen.append(kwargs.get("margin_pt"))
        return (1.0, 2.0, 3.0, 4.0), None

    monkeypatch.setattr(render, "auto_crop_insets", _record)
    panel = _panel(_inked_pdf(tmp_path))
    panel._on_unit_changed("mm")
    panel.auto_crop_margin_spinbox.setValue(2.0)

    panel._on_auto_crop()

    assert seen == [pytest.approx(2.0 * 72.0 / 25.4)]


def test_changing_the_margin_is_not_a_change_to_the_project(qapp, tmp_path):
    """It says how to measure. Only the insets it leads to are stored, as
    the CLI stores only the insets ``--auto-crop`` found."""
    panel = _panel(_inked_pdf(tmp_path))
    before = panel.state.project

    panel.auto_crop_margin_spinbox.set_points(12.0)

    assert panel.state.project is before
    assert not panel.state.can_undo
