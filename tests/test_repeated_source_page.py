"""A document that uses the same source page more than once.

``deckle.core.locate`` states this is supported, in its own module
docstring: "A document may use the same source page twice, so the *n*-th
appearance in the document maps to the *n*-th appearance in the plan." It
is what a repeated plate, a section divider, or a deliberately duplicated
leaf produces.

The crop is applied by mutating the source page's ``CropBox``, and
``source_cache`` hands out the *same* page object every time a document
reuses it. So each application subtracted its insets from the box left
behind by the previous one: the second copy of a page was cropped twice,
and the fourth four times.

Measured end to end on a page repeated four times with a 50pt left crop,
the ink came out 71, 71, 61 and 35 pixels wide -- the last one clipped
through the middle of the numeral. Nothing warned, and nothing in the
output says the pages were meant to be identical.

``_cropped_source_box``'s docstring called the mutation "safe and local"
on the grounds that "each page's box is set immediately before its own
form is built". That was true and it was not enough: it accounts for two
*different* pages, and says nothing about one page used twice.

The fix restores the box after the form is built rather than remembering
original boxes in a second cache -- ``copy_foreign`` has already
materialised the form into the sheet by then, so the source page is free
to go back to how it was found.
"""

from __future__ import annotations

import os

import pypdfium2
import pytest

from deckle.cli import _strategy_for
from deckle.core.dummy import make_numbered_pdf
from deckle.core.export import export
from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings

CROP = (50.0, 0.0, 0.0, 0.0)


@pytest.fixture
def source(tmp_path):
    path = os.path.join(str(tmp_path), "book.pdf")
    make_numbered_pdf(path, pages=2, page_size=(400.0, 600.0))
    return load_pdf(path)


def _ink_widths(path: str) -> list[int]:
    """The ink width on each exported sheet, in pixels.

    Pillow rather than numpy, and not for taste: numpy was the only thing
    in the suite that a fresh ``.[dev]`` install did not have, and a
    module-level import of it turned every one of the 1531 tests into a
    single collection error. Pillow is already a runtime dependency and
    ``page.render(...).to_pil()`` already returns one of its images.

    ``getbbox`` on the thresholded mask gives the same integer the
    ``np.where`` version did: its ``right`` is one past the last dark
    column, so ``right - left - 1`` is ``columns.max() - columns.min()``.
    Verified equal on the four-sheet export this file measures.
    """
    widths: list[int] = []
    doc = pypdfium2.PdfDocument(path)
    try:
        for index in range(len(doc)):
            page = doc[index]
            try:
                ink = (
                    page.render(scale=0.5)
                    .to_pil()
                    .convert("L")
                    .point(lambda value: 255 if value < 200 else 0)
                )
            finally:
                page.close()
            box = ink.getbbox()
            widths.append(box[2] - box[0] - 1 if box else 0)
    finally:
        doc.close()
    return widths


def _export(tmp_path, pages, **layout) -> str:
    settings = LayoutSettings(
        paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left", **layout
    )
    plan = _strategy_for(settings).impose(pages, settings)
    out = os.path.join(str(tmp_path), "out.pdf")
    export(plan, out)
    return out


def test_a_repeated_page_is_cropped_the_same_every_time(tmp_path, source):
    """The property, stated without reference to how many copies: every
    appearance of one page must come out identical."""
    repeated = [source[0]] * 4

    widths = _ink_widths(
        _export(tmp_path, repeated, crop_odd_pt=CROP, crop_even_pt=CROP)
    )

    assert max(widths) - min(widths) <= 2, widths


def test_the_last_copy_is_not_narrower_than_the_first(tmp_path, source):
    """Stated as a direction as well as a spread, because compounding is
    monotonic -- a spread check alone could be satisfied by noise."""
    repeated = [source[0]] * 4

    widths = _ink_widths(
        _export(tmp_path, repeated, crop_odd_pt=CROP, crop_even_pt=CROP)
    )

    assert widths[-1] >= widths[0] - 2, widths


def test_a_repeated_page_matches_the_same_page_used_once(tmp_path, source):
    """The strongest form: repetition must not change the answer at all,
    so the crop of a page used four times equals the crop of that page
    used once."""
    once = _ink_widths(_export(tmp_path, [source[0]], crop_odd_pt=CROP))
    repeated = _ink_widths(
        _export(tmp_path, [source[0]] * 4, crop_odd_pt=CROP, crop_even_pt=CROP)
    )

    assert abs(repeated[0] - once[0]) <= 2, (once, repeated)


def test_the_source_file_is_left_as_it_was_found(tmp_path, source):
    """The export opens sources read-only and never saves them, so a
    mutated CropBox was only ever in memory -- but it outlived the page it
    was applied for, which is the same bug seen from the other side."""
    import pikepdf

    path = source[0].ref.path
    with pikepdf.open(path) as pdf:
        before = [float(v) for v in pdf.pages[0].cropbox]

    _export(tmp_path, [source[0]] * 4, crop_odd_pt=CROP, crop_even_pt=CROP)

    with pikepdf.open(path) as pdf:
        assert [float(v) for v in pdf.pages[0].cropbox] == before


def test_repetition_without_a_crop_was_never_affected(tmp_path, source):
    """The uncropped path never mutated anything, so this passed before
    the fix and is here to keep the guard honest about its scope."""
    widths = _ink_widths(_export(tmp_path, [source[0]] * 4))

    assert max(widths) - min(widths) <= 2, widths


def test_two_different_pages_still_crop_independently(tmp_path, source):
    """Restoring the box must not restore it *before* the form is built --
    that would drop the crop entirely, which a same-page test cannot
    detect because every copy would then be equally uncropped."""
    uncropped = _ink_widths(_export(tmp_path, [source[0], source[1]]))
    cropped = _ink_widths(
        _export(tmp_path, [source[0], source[1]],
                crop_odd_pt=CROP, crop_even_pt=CROP)
    )

    assert cropped != uncropped
