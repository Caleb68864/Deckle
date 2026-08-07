"""Source cropping: removing space the source already has.

Deckle's margins and scale only ever ADD space. The most common source for
a hand-bound book is a scan or a public-domain PDF typeset for a different
trim size, carrying an inch and a half of white on every side. Scaling that
to fit a small cell scales the MARGINS too, so the type ends up tiny and
the page mostly empty. Cropping first is the only way to get readable type
at a small trim size.

Stored as INSETS from each edge, not as an absolute rectangle. One document
can contain pages of different sizes -- the Traveller book that motivated
`document_scale` has 264 pages at one width and two outliers -- and an
absolute rect means something different on each of them, while "half an
inch off the left" means the same thing on all of them.

Odd and even are separately croppable because a scanned book alternates
margins: the gutter side swaps every leaf, so one rectangle cannot fit
both.
"""

from __future__ import annotations

import pytest

from deckle.core.layout import GutterShiftStrategy, crop_for, document_scale
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

LETTER = (612.0, 792.0)


def _page(index: int, w=400.0, h=600.0, rotate=0) -> SourcePage:
    return SourcePage(
        ref=SourceRef(path="b.pdf", page_index=index, sha256="a" * 64,
                      width_pt=w, height_pt=h),
        rotate_deg=rotate,
        skipped=False,
    )


def _settings(**kw) -> LayoutSettings:
    base = dict(paper=LETTER, gutter_pt=0.0, binding_edge="left")
    base.update(kw)
    return LayoutSettings(**base)


# --- which crop applies to which page -----------------------------------


def test_no_crop_configured_means_no_crop():
    assert crop_for(_page(0), _settings()) is None


def test_one_crop_applies_to_every_page():
    settings = _settings(crop_odd_pt=(10.0, 20.0, 30.0, 40.0))

    assert crop_for(_page(0), settings) == (10.0, 20.0, 30.0, 40.0)
    assert crop_for(_page(1), settings) == (10.0, 20.0, 30.0, 40.0)


def test_an_even_crop_applies_only_to_even_numbered_pages():
    """Page numbers as a reader counts them: page 1 is odd and is
    `page_index` 0. A scan's gutter swaps sides every leaf."""
    settings = _settings(
        crop_odd_pt=(10.0, 0.0, 30.0, 0.0),
        crop_even_pt=(30.0, 0.0, 10.0, 0.0),
    )

    assert crop_for(_page(0), settings) == (10.0, 0.0, 30.0, 0.0)  # page 1
    assert crop_for(_page(1), settings) == (30.0, 0.0, 10.0, 0.0)  # page 2
    assert crop_for(_page(2), settings) == (10.0, 0.0, 30.0, 0.0)  # page 3


def test_an_even_crop_alone_leaves_odd_pages_uncropped():
    settings = _settings(crop_even_pt=(30.0, 0.0, 10.0, 0.0))

    assert crop_for(_page(0), settings) is None
    assert crop_for(_page(1), settings) == (30.0, 0.0, 10.0, 0.0)


# --- what the crop does to the layout -----------------------------------


def test_cropping_makes_the_content_bigger_on_the_page():
    """The entire point. A page that is mostly margin scales down to fit;
    remove the margin and the type gets bigger at the same cell size."""
    pages = [_page(i) for i in range(2)]
    plain = document_scale(pages, _settings())
    cropped = document_scale(
        pages, _settings(crop_odd_pt=(100.0, 150.0, 100.0, 150.0))
    )

    assert cropped > plain


def test_the_scale_is_still_one_number_for_the_whole_document():
    """Cropping must not reintroduce per-page scale: differing crops would
    otherwise reproduce body text at differing sizes, which is the exact
    defect `document_scale` exists to prevent."""
    pages = [_page(i) for i in range(4)]
    settings = _settings(
        crop_odd_pt=(0.0, 0.0, 0.0, 0.0), crop_even_pt=(60.0, 0.0, 60.0, 0.0)
    )
    plan = GutterShiftStrategy().impose(pages, settings)

    scales = {
        p.placement.scale_x
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
        for p in side.pages
        if not p.is_filler
    }
    assert len(scales) == 1


def test_a_crop_that_consumes_the_page_is_refused():
    settings = _settings(crop_odd_pt=(200.0, 0.0, 200.0, 0.0))  # 400pt wide

    with pytest.raises(ValueError):
        GutterShiftStrategy().impose([_page(0)], settings)


def test_a_negative_inset_is_refused():
    """Negative would ADD space, which is what the margins already do --
    and it would silently place content outside its own page box."""
    settings = _settings(crop_odd_pt=(-5.0, 0.0, 0.0, 0.0))

    with pytest.raises(ValueError):
        GutterShiftStrategy().impose([_page(0)], settings)


# --- the crop reaching the exported PDF ---------------------------------

import os  # noqa: E402
import re  # noqa: E402

import pikepdf  # noqa: E402
from pikepdf.canvas import ContentStreamBuilder  # noqa: E402

from deckle.core import export as export_mod  # noqa: E402
from deckle.core.export import export  # noqa: E402


def _inked_source(tmp_path, n=2, size=(400.0, 600.0)) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n):
        page = pdf.add_blank_page(page_size=size)
        b = ContentStreamBuilder()
        b.push()
        b.append_rectangle(0.0, 0.0, size[0], size[1])
        b.fill()
        b.pop()
        page.contents_add(b"q\n" + b.build() + b"Q\n")
    pdf.save(path)
    pdf.close()
    return path


def _real_pages(src, n=2, w=400.0, h=600.0):
    return [
        SourcePage(
            ref=SourceRef(path=src, page_index=i, sha256="a" * 64,
                          width_pt=w, height_pt=h),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def test_the_crop_is_carried_on_the_output_page_not_re_derived():
    """The imposer decides; the exporter reproduces. An exporter that
    worked the crop out for itself would need the settings, which it is
    deliberately never given."""
    settings = _settings(crop_odd_pt=(10.0, 20.0, 30.0, 40.0))
    plan = GutterShiftStrategy().impose([_page(0)], settings)

    placed = [p for s in plan.sheets for side in (s.front,) for p in side.pages]
    assert placed[0].crop_pt == (10.0, 20.0, 30.0, 40.0)


def test_the_cache_key_changes_when_the_crop_changes(tmp_path):
    """Two plans differing only by crop must not share a cached render --
    a preview showing the uncropped page would be confidently wrong."""
    src = _inked_source(tmp_path)
    pages = _real_pages(src)
    plain = GutterShiftStrategy().impose(pages, _settings())
    cropped = GutterShiftStrategy().impose(
        pages, _settings(crop_odd_pt=(50.0, 50.0, 50.0, 50.0))
    )

    assert export_mod._plan_hash(plain) != export_mod._plan_hash(cropped)


def test_a_cropped_export_shows_only_the_cropped_region(tmp_path):
    src = _inked_source(tmp_path)
    pages = _real_pages(src)
    out = os.path.join(str(tmp_path), "out.pdf")

    export(
        GutterShiftStrategy().impose(
            pages, _settings(crop_odd_pt=(50.0, 80.0, 50.0, 80.0))
        ),
        out,
    )

    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        xobjects = page.obj["/Resources"]["/XObject"]
        bboxes = [list(map(float, xo["/BBox"])) for xo in xobjects.values()]

    # The form's own BBox is the crop rectangle, so nothing outside it can
    # be drawn -- the crop is enforced by the PDF, not by hoping the
    # placement happens to land somewhere that hides it.
    assert bboxes == [[50.0, 80.0, 350.0, 520.0]]


def test_an_uncropped_export_keeps_the_whole_page(tmp_path):
    src = _inked_source(tmp_path)
    out = os.path.join(str(tmp_path), "out.pdf")

    export(GutterShiftStrategy().impose(_real_pages(src), _settings()), out)

    with pikepdf.open(out) as pdf:
        xobjects = pdf.pages[0].obj["/Resources"]["/XObject"]
        bboxes = [list(map(float, xo["/BBox"])) for xo in xobjects.values()]

    assert bboxes == [[0.0, 0.0, 400.0, 600.0]]
