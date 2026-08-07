"""Auto-crop: deriving the crop from where the ink actually is.

Cropping by hand means measuring a scan in a viewer and typing four
numbers, then reprinting when they were wrong. Deckle already rasterises
pages and already caches a per-page ink bounding box, so it can measure
them itself.

Two decisions shape the whole thing:

**Document-wide, not per page.** Cropping each page to its own ink would
let the text block jump around from page to page -- a page whose last line
is short would be cropped tighter than its neighbour, and the body text
would sit at a different height on every leaf. That is worse than not
cropping. The crop is the least aggressive inset any page needs, so no
page loses content and every page keeps the same frame.

**Odd and even measured separately.** This is the case `crop_even_pt`
exists for: a scanned book's gutter alternates sides, so odd and even
pages genuinely have different margins and one answer cannot fit both.
"""

from __future__ import annotations

import os

import pikepdf
import pytest
from pikepdf.canvas import ContentStreamBuilder

from deckle.core.render import auto_crop_insets, clear_ink_bbox_cache
from deckle.core.models import SourcePage, SourceRef

PAGE_W, PAGE_H = 400.0, 600.0


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_ink_bbox_cache()
    yield
    clear_ink_bbox_cache()


def _pdf_with_ink(tmp_path, rects, name="src.pdf") -> str:
    """One page per rect; ``None`` means a blank page."""
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


def _pages(path, count):
    return [
        SourcePage(
            ref=SourceRef(path=path, page_index=i, sha256="a" * 64,
                          width_pt=PAGE_W, height_pt=PAGE_H),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(count)
    ]


def _about(actual, expected, tol=6.0):
    """Ink bounds are measured off a low-dpi raster, so a few points of
    slack is the honest comparison -- an exact assertion would be testing
    the rasteriser's rounding, not the crop."""
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def test_a_centred_block_gives_the_surrounding_whitespace(tmp_path):
    path = _pdf_with_ink(tmp_path, [(100.0, 150.0, 300.0, 450.0)])

    odd, even = auto_crop_insets(_pages(path, 1))

    # left, bottom, right, top
    assert _about(odd, (100.0, 150.0, 100.0, 150.0)), odd


def test_the_crop_is_the_least_aggressive_any_page_needs(tmp_path):
    """One page's wider content must not be cut off to suit another's."""
    path = _pdf_with_ink(
        tmp_path,
        [(100.0, 150.0, 300.0, 450.0), (40.0, 150.0, 300.0, 450.0)],
    )

    odd, even = auto_crop_insets(_pages(path, 2), split_parity=False)

    assert _about(odd, (40.0, 150.0, 100.0, 150.0)), odd


def test_a_blank_page_does_not_defeat_the_crop(tmp_path):
    """`ink_bbox` returns a degenerate (0,0,0,0) for a blank page. Folded
    into the union unexamined, that reads as "content touches every edge"
    and silently disables cropping for the whole document."""
    path = _pdf_with_ink(tmp_path, [(100.0, 150.0, 300.0, 450.0), None])

    odd, even = auto_crop_insets(_pages(path, 2), split_parity=False)

    assert _about(odd, (100.0, 150.0, 100.0, 150.0)), odd


def test_a_document_with_no_ink_at_all_is_not_cropped(tmp_path):
    path = _pdf_with_ink(tmp_path, [None, None])

    odd, even = auto_crop_insets(_pages(path, 2))

    assert odd is None and even is None


def test_odd_and_even_are_measured_separately(tmp_path):
    """A scan's gutter alternates sides. Page 1 is odd, page 2 is even."""
    path = _pdf_with_ink(
        tmp_path,
        [(120.0, 150.0, 380.0, 450.0), (20.0, 150.0, 280.0, 450.0)],
    )

    odd, even = auto_crop_insets(_pages(path, 2))

    assert _about(odd, (120.0, 150.0, 20.0, 150.0)), odd
    assert _about(even, (20.0, 150.0, 120.0, 150.0)), even


def test_a_safety_margin_makes_the_crop_less_aggressive(tmp_path):
    """Ink bounds come off a low-dpi scan, and a descender or a hairline
    rule can fall just outside. The margin is how you buy that back."""
    path = _pdf_with_ink(tmp_path, [(100.0, 150.0, 300.0, 450.0)])

    tight, _ = auto_crop_insets(_pages(path, 1), margin_pt=0.0)
    loose, _ = auto_crop_insets(_pages(path, 1), margin_pt=20.0)

    assert all(l < t for l, t in zip(loose, tight))


def test_an_inset_never_goes_negative(tmp_path):
    """Content touching an edge, plus a margin, must clamp at zero rather
    than asking to add space -- which the crop path refuses outright."""
    path = _pdf_with_ink(tmp_path, [(0.0, 0.0, PAGE_W, PAGE_H)])

    odd, _ = auto_crop_insets(_pages(path, 1), margin_pt=20.0)

    assert odd is None or all(v >= 0.0 for v in odd)


def test_skipped_pages_are_not_measured(tmp_path):
    """A page excluded from the imposition must not constrain the crop of
    the pages that are in it."""
    path = _pdf_with_ink(
        tmp_path,
        [(100.0, 150.0, 300.0, 450.0), (5.0, 5.0, 395.0, 595.0)],
    )
    pages = _pages(path, 2)
    pages[1] = SourcePage(ref=pages[1].ref, rotate_deg=0, skipped=True)

    odd, even = auto_crop_insets(pages, split_parity=False)

    assert _about(odd, (100.0, 150.0, 100.0, 150.0)), odd


# --- from the command line ----------------------------------------------

from deckle.cli import main  # noqa: E402


def test_auto_crop_reports_values_that_can_be_pinned(tmp_path, capsys):
    """The numbers are the useful artifact: measure once, then pin them
    and stop rasterising the whole document on every run."""
    src = _pdf_with_ink(tmp_path, [(100.0, 150.0, 300.0, 450.0)] * 2)
    out = os.path.join(str(tmp_path), "out.pdf")

    rc = main(["export", src, "-o", out, "--auto-crop"])

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "--crop " in stdout, stdout


def test_auto_crop_actually_enlarges_the_content(tmp_path):
    """The point of the feature, asserted on the placement rather than on
    the message that claims it happened."""
    src = _pdf_with_ink(tmp_path, [(150.0, 200.0, 250.0, 400.0)] * 2)
    plain = os.path.join(str(tmp_path), "plain.pdf")
    cropped = os.path.join(str(tmp_path), "cropped.pdf")

    main(["export", src, "-o", plain])
    main(["export", src, "-o", cropped, "--auto-crop"])

    def _scale(path):
        import re
        with pikepdf.open(path) as pdf:
            contents = pdf.pages[0].obj.get("/Contents")
            raw = (
                b"".join(s.read_bytes() for s in contents)
                if isinstance(contents, pikepdf.Array)
                else contents.read_bytes()
            )
        m = re.search(rb"([\d.]+) 0 0 ([\d.]+) ", raw)
        return float(m.group(1)) if m else 1.0

    assert _scale(cropped) > _scale(plain)


def test_a_document_with_nothing_to_measure_says_so(tmp_path, capsys):
    src = _pdf_with_ink(tmp_path, [None, None])
    out = os.path.join(str(tmp_path), "out.pdf")

    rc = main(["export", src, "-o", out, "--auto-crop"])

    assert rc == 0
    assert "no content to measure" in capsys.readouterr().err
