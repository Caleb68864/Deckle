"""Two threads rasterizing at once must not take the process down.

pdfium's rendering is not thread-safe and it does not fail politely. Two
concurrent rasterizations produce either ``PdfiumError: Failed to load
document (Data format error)`` -- for work that has not started -- or
``OSError: exception: access violation reading 0x0``, which is a native
fault rather than a Python exception: no traceback, nothing in the log,
and the whole application gone. Measured here at roughly one concurrent
render in thirty, which is exactly the rate that reads to a user as
"Deckle randomly closes".

**Deckle reaches that through ordinary use.** Both views that render do
it on a background ``QThread``, and both supersede a running job the same
way -- set the outgoing worker's ``cancel`` flag, then start the next
thread *without waiting for the old one to stop*
(``preview_view._start_render``, ``arrange_view._request_thumbnails``).
The flag is cooperative and checked between steps, never inside a pdfium
call, so the outgoing render is still inside pdfium when the incoming one
begins. Scrubbing the preview does it. So does scrolling thumbnails while
a preview renders, because the two views hold independent threads.

The reproducer is ``ink_bbox`` rather than the preview, for two reasons:
it needs no Qt and no display, and it fails *catchably* -- 32 of 32
concurrent calls raised ``PdfiumError`` before the lock, against 0 after.
That matters for a test suite, because the access-violation form would
take pytest down with it rather than reporting a failure.

Guarded in ``deckle.core.render``, which is the module that owns pdfium,
so every caller is covered rather than each view having to remember.
"""

from __future__ import annotations

import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import pytest

from deckle.core.dummy import make_numbered_pdf
from deckle.core.loader import load_pdf
from deckle.core.render import (
    _PDFIUM_LOCK, clear_ink_bbox_cache, ink_bbox, rasterize_page, thumbnails,
)

WORKERS = 8
ROUNDS = 24


@pytest.fixture
def pages(tmp_path):
    path = os.path.join(str(tmp_path), "book.pdf")
    make_numbered_pdf(path, pages=8, page_size=(400.0, 600.0))
    loaded = load_pdf(path)
    clear_ink_bbox_cache()
    yield loaded
    clear_ink_bbox_cache()


def _run(work) -> list[str]:
    """Run ``work`` on many threads, returning one line per failure."""
    failures: list[str] = []

    def guarded(index):
        try:
            work(index)
        except BaseException:
            failures.append(traceback.format_exc().strip().splitlines()[-1])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(guarded, range(ROUNDS)))
    return failures


def test_concurrent_ink_measurement_does_not_fail(pages):
    """The reproducer: 32 of 32 raised before the lock, 0 after.

    The cache is cleared per test, so every thread does real pdfium work
    rather than being handed a memoised answer -- which is what makes this
    a concurrency test rather than a cache test.
    """
    refs = [page.ref for page in pages]

    failures = _run(lambda i: ink_bbox(refs[i % len(refs)]))

    assert failures == [], failures[:3]


def test_concurrent_thumbnail_windows_do_not_fail(pages):
    """The path ``arrange_view`` actually runs on its worker thread, and
    the one that overlaps a preview render when someone scrolls."""
    failures = _run(lambda i: thumbnails(pages, start=i % 4, count=3, dpi=24))

    assert failures == [], failures[:3]


def test_thumbnails_and_ink_measurement_together_do_not_fail(pages):
    """The realistic shape: two *different* render paths in flight, which
    is what two independent views produce. A per-path lock would pass the
    two tests above and fail this one."""
    refs = [page.ref for page in pages]

    def mixed(i):
        if i % 2:
            ink_bbox(refs[i % len(refs)])
        else:
            thumbnails(pages, start=i % 4, count=2, dpi=24)

    assert _run(mixed) == []


def test_every_thread_still_gets_a_real_measurement(pages):
    """Serialising must not have turned failures into empty answers -- a
    lock that made every call return a degenerate box would pass the tests
    above without rendering anything."""
    refs = [page.ref for page in pages]
    results: list[tuple] = []
    lock = threading.Lock()

    def measure(i):
        box = ink_bbox(refs[i % len(refs)])
        with lock:
            results.append(box)

    _run(measure)

    assert len(results) == ROUNDS
    assert all(x1 > x0 and y1 > y0 for x0, y0, x1, y1 in results)


def test_the_guard_is_reentrant(pages):
    """``render_sheet`` holds the lock across a document's lifetime and
    calls ``rasterize_page``, which takes it again on the same thread. A
    plain ``Lock`` deadlocks there, and a deadlock in a GUI worker is a
    hang rather than a crash -- quieter, and harder to diagnose."""
    assert _PDFIUM_LOCK.acquire(blocking=False)
    try:
        assert _PDFIUM_LOCK.acquire(blocking=False), "not reentrant"
        _PDFIUM_LOCK.release()
    finally:
        _PDFIUM_LOCK.release()


def test_rasterizing_is_serialised(pages, monkeypatch):
    """Asserted on observable overlap rather than on the lock object, so a
    different mechanism that also serialises would still pass."""
    import deckle.core.render as render_mod

    live = 0
    peak = 0
    counter_lock = threading.Lock()
    real = render_mod.rasterize_page

    def counting(doc, page_index, **kwargs):
        nonlocal live, peak
        with counter_lock:
            live += 1
            peak = max(peak, live)
        try:
            return real(doc, page_index, **kwargs)
        finally:
            with counter_lock:
                live -= 1

    monkeypatch.setattr(render_mod, "rasterize_page", counting)
    refs = [page.ref for page in pages]
    _run(lambda i: render_mod.ink_bbox(refs[i % len(refs)]))

    assert peak == 1, f"{peak} rasterizations overlapped"
