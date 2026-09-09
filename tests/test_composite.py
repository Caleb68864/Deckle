"""The composite overlay: every page's content in one picture.

briss's distinguishing feature is superimposing all pages so you can see
the real content extent before committing to a crop. `--auto-crop` now
measures that extent, so what is left for a picture is *verification*:
does the crop I am about to apply cut anything off, on any page?

Darkest pixel wins. A page contributes its ink and nothing else, so the
composite is the union of every page's content -- which is exactly the
question being asked. Averaging would fade a mark that appears on one page
out of two hundred into invisibility, and that mark is precisely the one
that would be clipped without anyone noticing.
"""

from __future__ import annotations

import os

import pikepdf
import pytest
from pikepdf.canvas import ContentStreamBuilder

from deckle.core.models import SourcePage, SourceRef
from deckle.core.render import composite_pages

PAGE_W, PAGE_H = 400.0, 600.0
DPI = 36


def _pdf(tmp_path, rects, name="src.pdf") -> str:
    path = os.path.join(str(tmp_path), name)
    pdf = pikepdf.Pdf.new()
    for rect in rects:
        page = pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
        if rect is not None:
            x0, y0, x1, y1 = rect
            b = ContentStreamBuilder()
            b.push()
            b.append_rectangle(x0, y0, x1 - x0, y1 - y0)
            b.fill()
            b.pop()
            page.contents_add(b"q\n" + b.build() + b"Q\n")
    pdf.save(path)
    pdf.close()
    return path


def _pages(path, count, skipped=()):
    return [
        SourcePage(
            ref=SourceRef(path=path, page_index=i, sha256="a" * 64,
                          width_pt=PAGE_W, height_pt=PAGE_H),
            rotate_deg=0,
            skipped=i in skipped,
        )
        for i in range(count)
    ]


def _pixel(rendered, x_pt: float, y_pt: float):
    """The RGBA tuple at a point on the page, bottom-left origin."""
    scale = rendered.width / PAGE_W
    col = int(x_pt * scale)
    row = int((PAGE_H - y_pt) * scale)  # image origin is top-left
    index = (row * rendered.width + col) * 4
    return tuple(rendered.rgba[index : index + 4])


def _is_dark(pixel) -> bool:
    return pixel[0] < 128 and pixel[1] < 128 and pixel[2] < 128


def test_the_composite_carries_ink_from_every_page(tmp_path):
    """The whole point: a mark present on only one page must survive."""
    path = _pdf(tmp_path, [
        (20.0, 500.0, 80.0, 560.0),    # page 1 only, top-left
        (320.0, 40.0, 380.0, 100.0),   # page 2 only, bottom-right
    ])

    composite = composite_pages(_pages(path, 2), dpi=DPI)

    assert _is_dark(_pixel(composite, 50.0, 530.0)), "page 1's mark is missing"
    assert _is_dark(_pixel(composite, 350.0, 70.0)), "page 2's mark is missing"


def test_blank_areas_stay_blank(tmp_path):
    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0)])

    composite = composite_pages(_pages(path, 1), dpi=DPI)

    assert not _is_dark(_pixel(composite, 200.0, 300.0))


def test_a_blank_page_does_not_erase_the_others(tmp_path):
    """Darkest wins, so an all-white page contributes nothing. Averaging
    would fade a mark that appears on one page in two hundred into
    invisibility -- and that is the mark most likely to be clipped."""
    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0), None])

    composite = composite_pages(_pages(path, 2), dpi=DPI)

    assert _is_dark(_pixel(composite, 50.0, 530.0))


def test_a_skipped_page_is_not_composited(tmp_path):
    """A page excluded from the imposition must not widen the extent the
    crop is judged against."""
    path = _pdf(tmp_path, [
        (20.0, 500.0, 80.0, 560.0),
        (320.0, 40.0, 380.0, 100.0),
    ])

    composite = composite_pages(_pages(path, 2, skipped={1}), dpi=DPI)

    assert _is_dark(_pixel(composite, 50.0, 530.0))
    assert not _is_dark(_pixel(composite, 350.0, 70.0)), "a skipped page was drawn"


def test_odd_and_even_can_be_composited_separately(tmp_path):
    """A scan's margins alternate, so the two parities are different
    pictures and averaging them together would answer neither."""
    path = _pdf(tmp_path, [
        (20.0, 500.0, 80.0, 560.0),    # page 1, odd
        (320.0, 40.0, 380.0, 100.0),   # page 2, even
    ])
    pages = _pages(path, 2)

    odd = composite_pages(pages, dpi=DPI, parity="odd")
    even = composite_pages(pages, dpi=DPI, parity="even")

    assert _is_dark(_pixel(odd, 50.0, 530.0))
    assert not _is_dark(_pixel(odd, 350.0, 70.0))
    assert _is_dark(_pixel(even, 350.0, 70.0))
    assert not _is_dark(_pixel(even, 50.0, 530.0))


def test_the_crop_is_drawn_where_it_would_fall(tmp_path):
    """The picture answers "would this crop cut anything", which it can
    only do if the crop is visible on it."""
    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0)])

    composite = composite_pages(
        _pages(path, 1), dpi=DPI, crop_pt=(100.0, 100.0, 100.0, 100.0)
    )

    # The rectangle sits at x=100 and x=300, y=100 and y=500.
    edge = _pixel(composite, 100.0, 300.0)
    assert edge[0] > edge[1] and edge[0] > edge[2], f"no red crop edge, got {edge}"


def test_no_crop_draws_no_rectangle(tmp_path):
    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0)])

    composite = composite_pages(_pages(path, 1), dpi=DPI)

    edge = _pixel(composite, 100.0, 300.0)
    assert not (edge[0] > edge[1] and edge[0] > edge[2])


def test_nothing_to_composite_is_refused(tmp_path):
    path = _pdf(tmp_path, [None])

    with pytest.raises(ValueError):
        composite_pages(_pages(path, 1, skipped={0}), dpi=DPI)


# --- the picture and the rectangle must answer the same question --------


def test_an_all_pages_composite_gets_an_all_pages_crop(tmp_path):
    """`auto_crop_insets` splits odd from even by default, because a scan's
    margins alternate. A composite of EVERY page drawn against an odd-only
    measurement is a confidently wrong picture: it shows the even pages'
    ink and a rectangle that was never measured against it, so a crop that
    clips them looks safe.
    """
    import contextlib
    import io as _io

    from deckle.cli import main

    captured = _io.StringIO()

    def capsys_stdout():
        return captured.getvalue()

    # Page 2 (even) alone carries a mark far to the left.
    path = _pdf(tmp_path, [
        (200.0, 300.0, 380.0, 400.0),
        (20.0, 300.0, 380.0, 400.0),
    ])
    src_pages = _pages(path, 2)
    out = os.path.join(str(tmp_path), "overlay.png")

    with contextlib.redirect_stdout(captured):
        rc = main(["crop-preview", path, "-o", out, "--auto-crop"])

    assert rc == 0
    # Assert on the crop the COMMAND reported, which is the one it drew.
    # Checking `auto_crop_insets` directly would pass without the fix and
    # prove nothing about the picture.
    reported = capsys_stdout()
    left = float(reported.split("--crop ")[1].split(",")[0].rstrip("pt"))
    assert left < 60.0, f"drew an odd-only crop over an all-pages composite: {reported}"


# -- cancellation --------------------------------------------------------
#
# The composite rasterises EVERY unskipped page, and the panel restarts it
# on every crop edit (N11). A superseded 300-page run is 300 rasterisations
# nobody will look at, so it has to be interruptible -- the same contract
# `render_sheet` already gives.


def test_a_cancelled_composite_returns_a_degenerate_page(tmp_path):
    """Already-set cancel: back out before rasterising anything."""
    import threading

    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0), None])
    cancel = threading.Event()
    cancel.set()

    rendered = composite_pages(_pages(path, 2), dpi=DPI, cancel=cancel)

    assert (rendered.width, rendered.height, rendered.rgba) == (0, 0, b"")


def test_a_cancelled_composite_does_not_raise_on_an_empty_selection(tmp_path):
    """Cancellation outranks the "nothing to composite" ValueError.

    A caller that has already moved on must not have to catch an error
    about the state it abandoned.
    """
    import threading

    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0)])
    cancel = threading.Event()
    cancel.set()

    rendered = composite_pages(
        _pages(path, 1, skipped=(0,)), dpi=DPI, cancel=cancel
    )

    assert rendered.rgba == b""


def test_cancelling_partway_stops_rasterising(tmp_path, monkeypatch):
    """The point of the parameter: work stops, it does not merely go unused."""
    import threading

    from deckle.core import render as render_mod

    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0)] * 6)
    cancel = threading.Event()
    calls = []
    real = render_mod._rasterize_for_bbox

    def counting(ref, dpi):
        calls.append(ref)
        if len(calls) == 2:
            cancel.set()
        return real(ref, dpi)

    monkeypatch.setattr(render_mod, "_rasterize_for_bbox", counting)

    rendered = composite_pages(_pages(path, 6), dpi=DPI, cancel=cancel)

    assert rendered.rgba == b""
    assert len(calls) == 2, f"kept rasterising after cancellation: {len(calls)}"


def test_an_unset_cancel_composites_normally(tmp_path):
    """A cancel that never fires must change nothing at all."""
    import threading

    path = _pdf(tmp_path, [(20.0, 500.0, 80.0, 560.0), (320.0, 40.0, 380.0, 100.0)])
    pages = _pages(path, 2)

    with_event = composite_pages(pages, dpi=DPI, cancel=threading.Event())
    without = composite_pages(pages, dpi=DPI)

    assert with_event.rgba == without.rgba
    assert (with_event.width, with_event.height) == (without.width, without.height)
