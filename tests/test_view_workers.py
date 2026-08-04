"""The background-worker contract shared by ArrangeView and PreviewView.

Both views spawn a ``QThread`` per user action -- scrolling the thumbnail
grid, changing sheet/side/layout. Three things must hold, and none of them
were covered before: a superseded job must stop doing work, must not
overwrite a newer result, and its thread must not accumulate.

These workers are plain classes, not ``QObject``s, so everything here runs
headlessly with no Qt event loop.
"""

from __future__ import annotations

import threading


from deckle.app.views.arrange_view import ThumbnailWorker
from deckle.app.views.preview_view import PreviewWorker
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core.profiles import BUILTIN_PRESETS

LETTER = (612.0, 792.0)


def make_pages(n=2, size=(432.0, 648.0)):
    w, h = size
    return [
        SourcePage(
            ref=SourceRef(path="p.pdf", page_index=i, sha256="a" * 64,
                          width_pt=w, height_pt=h),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def make_plan(pages=None):
    pages = pages or make_pages()
    return GutterShiftStrategy().impose(
        pages, LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    )


def profile():
    return list(BUILTIN_PRESETS.values())[0]


# ------------------------------------------------------------ construction


def test_thumbnail_worker_constructs():
    """Regression: ThumbnailWorker had NO test coverage, so a missing
    `import threading` in arrange_view survived a green suite entirely.
    Constructing it is the minimum that would have caught that."""
    worker = ThumbnailWorker(make_pages(), 0, 10)
    assert isinstance(worker.cancel, threading.Event)
    assert not worker.cancel.is_set()


def test_preview_worker_constructs_with_a_cancel_token():
    worker = PreviewWorker(make_plan(), profile(), 0, "front")
    assert isinstance(worker.cancel, threading.Event)
    assert not worker.cancel.is_set()


# ------------------------------------------------------------- cancellation


def test_cancelled_thumbnail_worker_does_no_work(monkeypatch):
    called = []
    monkeypatch.setattr(
        "deckle.app.views.arrange_view.request_visible_thumbnails",
        lambda *a, **k: (called.append(a), (0, []))[1],
    )
    worker = ThumbnailWorker(make_pages(), 0, 10)
    worker.cancel.set()
    worker.run()
    assert called == [], "a cancelled fetch must not rasterize anything"


def test_cancelled_preview_worker_produces_no_frames(monkeypatch):
    called = []
    monkeypatch.setattr(
        "deckle.app.views.preview_view.build_preview_frame",
        lambda *a, **k: called.append(a),
    )
    worker = PreviewWorker(make_plan(), profile(), 0, "front")
    worker.cancel.set()
    worker.run()
    assert called == []
    assert worker.frames == []
    assert worker.frame is None


def test_preview_worker_threads_the_cancel_token_into_the_renderer(monkeypatch):
    """The token must reach render_sheet, not just gate the loop -- a single
    150 DPI sheet of a large PDF is where the time actually goes."""
    seen = {}

    def fake(plan, prof, sheet_index, side, dpi=150, cancel=None):
        seen["cancel"] = cancel
        from deckle.app.views.preview_view import PreviewFrame
        from deckle.core.render import RenderedPage

        return PreviewFrame(
            sheet_index=sheet_index, side=side,
            rendered=RenderedPage(width=0, height=0, rgba=b""), warnings=[],
        )

    monkeypatch.setattr("deckle.app.views.preview_view.build_preview_frame", fake)
    worker = PreviewWorker(make_plan(), profile(), 0, "front")
    worker.run()
    assert seen["cancel"] is worker.cancel


def test_preview_worker_stops_between_sides_when_cancelled(monkeypatch):
    """Spread mode renders two sides; cancelling after the first must skip
    the second rather than finish the pair."""
    calls = []

    def fake(plan, prof, sheet_index, side, dpi=150, cancel=None):
        calls.append(side)
        cancel.set()  # superseded while the first side was rendering
        from deckle.app.views.preview_view import PreviewFrame
        from deckle.core.render import RenderedPage

        return PreviewFrame(
            sheet_index=sheet_index, side=side,
            rendered=RenderedPage(width=0, height=0, rgba=b""), warnings=[],
        )

    monkeypatch.setattr("deckle.app.views.preview_view.build_preview_frame", fake)
    worker = PreviewWorker(
        make_plan(), profile(), 0, "front", sides=("front", "back")
    )
    worker.run()
    assert calls == ["front"], "must not render the back after cancellation"
    assert worker.frames == []


def test_uncancelled_preview_worker_renders_every_requested_side(monkeypatch):
    def fake(plan, prof, sheet_index, side, dpi=150, cancel=None):
        from deckle.app.views.preview_view import PreviewFrame
        from deckle.core.render import RenderedPage

        return PreviewFrame(
            sheet_index=sheet_index, side=side,
            rendered=RenderedPage(width=0, height=0, rgba=b""), warnings=[],
        )

    monkeypatch.setattr("deckle.app.views.preview_view.build_preview_frame", fake)
    worker = PreviewWorker(
        make_plan(), profile(), 0, "front", sides=("front", "back")
    )
    worker.run()
    assert [f.side for f in worker.frames] == ["front", "back"]
    assert worker.frame is worker.frames[0]


def test_cancelled_worker_leaves_previous_results_untouched(monkeypatch):
    """A cancelled run must not half-write its output -- a caller checking
    `frames` should see nothing rather than a partial pair."""
    def fake(plan, prof, sheet_index, side, dpi=150, cancel=None):
        from deckle.app.views.preview_view import PreviewFrame
        from deckle.core.render import RenderedPage

        if side == "back":
            cancel.set()
        return PreviewFrame(
            sheet_index=sheet_index, side=side,
            rendered=RenderedPage(width=0, height=0, rgba=b""), warnings=[],
        )

    monkeypatch.setattr("deckle.app.views.preview_view.build_preview_frame", fake)
    worker = PreviewWorker(
        make_plan(), profile(), 0, "front", sides=("front", "back")
    )
    worker.run()
    assert worker.frames == []
    assert worker.frame is None


# ------------------------------------------------------- printer enumeration


def test_printer_query_worker_collects_names(monkeypatch):
    from deckle.app import main as app_main

    monkeypatch.setattr(app_main, "available_printer_names", lambda: ["A", "B"])
    worker = app_main._PrinterQueryWorker()
    worker.run()
    assert worker.names == ["A", "B"]


def test_printer_query_worker_survives_a_spooler_failure(monkeypatch):
    """A broken spooler must not take the window down.

    Deckle is fully usable for Save PDF with no printers at all, so an
    enumeration failure degrades to "no printers", never an exception on a
    background thread.
    """
    from deckle.app import main as app_main

    def boom():
        raise OSError("spooler unavailable")

    monkeypatch.setattr(app_main, "available_printer_names", boom)
    worker = app_main._PrinterQueryWorker()
    worker.run()
    assert worker.names == []


def test_printer_enumeration_is_not_called_during_import(monkeypatch):
    """Regression: printer enumeration used to run synchronously inside
    MainWindow.__init__. QPrinterInfo.availablePrinters() enumerates network
    printers, and the Windows spooler blocks per printer until it times out
    when one is unreachable -- so Deckle hung on launch whenever a networked
    printer was offline. Importing the module must not query anything.
    """
    calls = []
    from deckle.app import main as app_main

    monkeypatch.setattr(
        app_main, "available_printer_names", lambda: (calls.append(1), [])[1]
    )
    import importlib

    importlib.reload(app_main)
    assert calls == []
