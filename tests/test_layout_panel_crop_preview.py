"""The ink composite under the crop spinboxes.

The Crop & trim tab asked for eight numbers and showed nothing. The
question a cropper actually has is not "what does page 1 look like" but
"does this rectangle clip anything, on *any* page" -- and the Measure crop
from the ink tooltip says exactly that, then offered no way to look.

Two halves are tested separately, because they fail differently. The
parity rule and the caption are pure functions: which rectangle belongs
over which composite is a judgement, and getting it wrong draws a
confidently safe-looking picture. The wiring is driven through the panel
with a synchronous fake thread -- a control connected to nothing passes
every test of the pure half.
"""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from deckle.app.state import AppState  # noqa: E402
from deckle.app.views.layout_panel import (  # noqa: E402
    COMPOSITE_MIXED_MESSAGE,
    COMPOSITE_PREVIEW_DPI,
    COMPOSITE_RECTANGLE_SENTENCE,
    CompositeWorker,
    LayoutPanel,
    composite_caption,
    composite_crop_for,
)
from deckle.app.views.layout_panel import set_crop  # noqa: E402
from deckle.core.models import (  # noqa: E402
    LayoutSettings, Project, SourcePage, SourceRef,
)

ODD = (10.0, 11.0, 12.0, 13.0)
EVEN = (20.0, 21.0, 22.0, 23.0)


def _set_crops(panel, odd=None, even=None):
    """``LayoutSettings`` is frozen, so go through the mutator the panel does."""
    for parity, insets in (("odd", odd), ("even", even)):
        if insets is not None:
            panel.state.mutate(
                lambda project, p=parity, i=insets: set_crop(project, p, i)
            )


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _layout(**kwargs):
    return LayoutSettings(
        paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=1, **kwargs,
    )


def _project(pages=8, **layout_kwargs):
    src = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(pages)
    ]
    return Project(pages=src, layout=_layout(**layout_kwargs), printer=None)


@pytest.fixture
def panel(qt_app):
    return LayoutPanel(AppState(_project()))


# -- which rectangle goes over which picture -----------------------------
#
# Drawing the odd crop over a composite of every page shows the even
# pages' ink beside a rectangle never measured against it, so a crop that
# clips them looks safe. `cli._cmd_crop_preview` made the same call.


def test_odd_shows_the_odd_crop():
    assert composite_crop_for(_layout(crop_odd_pt=ODD, crop_even_pt=EVEN), "odd") == ODD


def test_even_shows_the_even_crop():
    assert composite_crop_for(_layout(crop_odd_pt=ODD, crop_even_pt=EVEN), "even") == EVEN


def test_even_falls_back_to_the_odd_crop():
    """Because that is what the imposer applies when only one is set."""
    assert composite_crop_for(_layout(crop_odd_pt=ODD), "even") == ODD


def test_all_pages_shows_nothing_when_the_two_differ():
    """The confidently-wrong case. No rectangle beats the wrong rectangle."""
    assert composite_crop_for(_layout(crop_odd_pt=ODD, crop_even_pt=EVEN), "all") is None


def test_all_pages_shows_the_one_crop_when_there_is_one():
    assert composite_crop_for(_layout(crop_odd_pt=ODD), "all") == ODD


def test_no_crop_at_all_draws_no_rectangle():
    assert composite_crop_for(_layout(), "all") is None
    assert composite_crop_for(_layout(), "odd") is None


# -- the caption ---------------------------------------------------------


class _FinishedWorker:
    def __init__(self, message="", failed=False, page_count=0):
        self.message = message
        self.failed = failed
        self.page_count = page_count


def test_the_caption_says_what_the_rectangle_means():
    caption = composite_caption(_FinishedWorker(page_count=12))

    assert "12 page(s) superimposed" in caption
    assert COMPOSITE_RECTANGLE_SENTENCE in caption


def test_a_mixed_crop_under_all_pages_explains_itself():
    caption = composite_caption(_FinishedWorker(page_count=8), COMPOSITE_MIXED_MESSAGE)

    assert caption == COMPOSITE_MIXED_MESSAGE
    # Not the sentence about a rectangle that was not drawn.
    assert COMPOSITE_RECTANGLE_SENTENCE not in caption


def test_a_failure_outranks_the_page_count():
    assert composite_caption(_FinishedWorker(failed=True, page_count=8)) == (
        "Could not draw the composite."
    )


def test_why_there_is_no_picture_outranks_everything():
    """The "you filtered everything out" case is a sentence, not a fault."""
    caption = composite_caption(
        _FinishedWorker(message="nothing to composite: every page was skipped",
                        failed=True, page_count=0),
        COMPOSITE_MIXED_MESSAGE,
    )

    assert caption.startswith("nothing to composite")


# -- the worker ----------------------------------------------------------


def _fixture_pages(tmp_path, count, skipped=()):
    import pikepdf
    from pikepdf.canvas import ContentStreamBuilder

    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(count):
        page = pdf.add_blank_page(page_size=(400.0, 600.0))
        b = ContentStreamBuilder()
        b.push()
        b.append_rectangle(20.0, 500.0, 60.0, 60.0)
        b.fill()
        b.pop()
        page.contents_add(b"q\n" + b.build() + b"Q\n")
    pdf.save(path)
    pdf.close()
    return [
        SourcePage(
            ref=SourceRef(path=path, page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=i in skipped,
        )
        for i in range(count)
    ]


def test_the_worker_renders_and_counts_the_pages(tmp_path):
    worker = CompositeWorker(_fixture_pages(tmp_path, 4), dpi=COMPOSITE_PREVIEW_DPI)

    worker.run()

    assert worker.rendered is not None and worker.rendered.rgba
    assert worker.page_count == 4
    assert worker.failed is False and worker.message == ""


def test_the_worker_counts_only_the_parity_it_composited(tmp_path):
    worker = CompositeWorker(
        _fixture_pages(tmp_path, 4), parity="odd", dpi=COMPOSITE_PREVIEW_DPI
    )

    worker.run()

    assert worker.page_count == 2, "the caption would name pages not in the picture"


def test_a_cancelled_worker_stores_nothing(tmp_path):
    worker = CompositeWorker(_fixture_pages(tmp_path, 4), dpi=COMPOSITE_PREVIEW_DPI)
    worker.cancel.set()

    worker.run()

    assert worker.rendered is None and worker.failed is False


def test_a_document_of_only_skipped_pages_says_so(tmp_path):
    """A reachable state (N6's Skip range), reported rather than logged."""
    worker = CompositeWorker(
        _fixture_pages(tmp_path, 3, skipped=(0, 1, 2)), dpi=COMPOSITE_PREVIEW_DPI
    )

    worker.run()

    assert worker.rendered is None
    assert worker.failed is False
    assert "nothing to composite" in worker.message


def test_the_worker_records_a_failure_rather_than_raising(tmp_path, monkeypatch):
    """A QThread has nowhere to deliver an exception."""
    from deckle.core import render as render_mod

    def boom(*args, **kwargs):
        raise RuntimeError("pdfium fell over")

    monkeypatch.setattr(render_mod, "composite_pages", boom)
    worker = CompositeWorker(_fixture_pages(tmp_path, 2), dpi=COMPOSITE_PREVIEW_DPI)

    worker.run()  # must not raise

    assert worker.failed is True and worker.rendered is None


# -- the wiring ----------------------------------------------------------


class _SyncThread:
    """A ``QThread`` stand-in that runs on the calling thread.

    The panel's Qt half is what a pure-function test cannot reach: a
    combo connected to nothing, or a worker never superseded, passes
    everything above.
    """

    def __init__(self, parent=None):
        self.run = lambda: None
        self._finished = []
        self.started = False

    @property
    def finished(self):
        outer = self

        class _Signal:
            def connect(self, slot):
                outer._finished.append(slot)

        return _Signal()

    def start(self):
        self.started = True
        self.run()
        for slot in list(self._finished):
            slot()

    def deleteLater(self):
        pass

    def isRunning(self):
        return False


def test_the_panel_has_a_composite_picture(panel):
    from PySide6.QtWidgets import QLabel

    assert isinstance(panel.composite_label, QLabel)
    assert isinstance(panel.composite_caption, QLabel)
    assert panel.composite_check.isChecked()


def test_editing_a_crop_schedules_a_redraw(panel):
    fired = []
    panel._start_composite = lambda: fired.append(1)

    panel.crop_spinboxes[("odd", "left")].setValue(0.5)
    panel._composite_timer.timeout.emit()

    assert fired == [1]


def test_eight_quick_edits_produce_one_redraw(panel):
    """Debounced. Otherwise holding an arrow key rasterises the document
    once per step, each run parented to the widget and never freed."""
    fired = []
    panel._start_composite = lambda: fired.append(1)

    for parity in ("odd", "even"):
        for edge in ("left", "bottom", "right", "top"):
            panel.crop_spinboxes[(parity, edge)].setValue(0.25)
    panel._composite_timer.timeout.emit()

    assert fired == [1], f"one rasterisation per edit: {len(fired)}"


def test_the_composite_never_runs_on_the_gui_thread(panel, monkeypatch):
    """It is started on a thread, not called inline."""
    panel._QThread = _SyncThread
    ran_on = []
    monkeypatch.setattr(
        "deckle.app.views.layout_panel.CompositeWorker.run",
        lambda self: ran_on.append(threading.current_thread()),
    )

    panel._start_composite()

    assert panel._composite_thread.started is True
    assert ran_on, "the worker never ran"


def test_a_finished_composite_paints_and_captions(panel, tmp_path):
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)

    panel._start_composite()

    assert not panel.composite_label.pixmap().isNull()
    assert COMPOSITE_RECTANGLE_SENTENCE in panel.composite_caption.text()


def test_a_superseded_composite_never_paints(panel, tmp_path):
    """Nudging a spinbox eight times must not paint the slowest run last."""
    first = CompositeWorker(_fixture_pages(tmp_path, 2), dpi=COMPOSITE_PREVIEW_DPI)
    first.run()
    second = CompositeWorker(_fixture_pages(tmp_path, 2), dpi=COMPOSITE_PREVIEW_DPI)
    panel._composite_worker = second

    panel._on_composite_ready(first)

    assert panel.composite_label.pixmap().isNull()


def test_starting_a_composite_cancels_the_one_in_flight(panel, tmp_path):
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)
    panel._start_composite()
    superseded = panel._composite_worker

    panel._start_composite()

    assert superseded.cancel.is_set()


def test_unticking_the_checkbox_clears_the_picture(panel, tmp_path):
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)
    panel._start_composite()
    assert not panel.composite_label.pixmap().isNull()

    panel.composite_check.setChecked(False)

    assert panel.composite_label.pixmap().isNull()
    assert panel.composite_caption.text() == ""


def test_unticking_cancels_a_run_in_flight(panel, tmp_path):
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)
    panel._start_composite()
    worker = panel._composite_worker

    panel.composite_check.setChecked(False)

    assert worker.cancel.is_set(), "kept rasterising a picture nobody asked for"


def test_an_empty_document_draws_nothing(qt_app):
    panel = LayoutPanel(AppState(_project(pages=0)))
    panel._QThread = _SyncThread

    panel._start_composite()

    assert panel._composite_thread is None
    assert panel.composite_caption.text() == ""


def test_the_parity_combo_picks_the_rectangle(panel, tmp_path):
    """The combo is connected, and it selects both the pages and the crop."""
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 4)
    _set_crops(panel, odd=ODD, even=EVEN)

    panel.composite_parity_combo.setCurrentIndex(
        panel._composite_parity_keys.index("even")
    )
    panel._composite_timer.timeout.emit()  # the combo debounces like the boxes

    assert panel._composite_worker is not None
    assert panel._composite_worker.parity == "even"
    assert panel._composite_worker.crop_pt == EVEN


def test_all_pages_with_two_crops_says_why_there_is_no_rectangle(panel, tmp_path):
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)
    _set_crops(panel, odd=ODD, even=EVEN)

    panel._start_composite()

    assert panel._composite_worker.crop_pt is None
    assert panel.composite_caption.text() == COMPOSITE_MIXED_MESSAGE


def test_the_panel_exposes_its_thread_to_shutdown(panel, tmp_path):
    """``_live_threads``/``stop_background_work`` read these exact names.

    A render still inside pdfium when the interpreter finalises takes the
    process down with it.
    """
    panel._QThread = _SyncThread
    panel.state.project.pages[:] = _fixture_pages(tmp_path, 2)
    panel._start_composite()

    assert panel._thread is panel._composite_thread
    assert panel._worker is panel._composite_worker
    assert hasattr(panel._worker, "cancel")
