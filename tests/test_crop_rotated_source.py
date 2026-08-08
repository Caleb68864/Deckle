"""Cropping a source page that carries ``/Rotate``.

``/Rotate`` is how most scanners, and every "rotate and save" in every
viewer, record that a page is sideways: the content is left alone and the
page object says which way up to display it. So it is not an exotic input
-- it is what a scanned book looks like when someone straightened it.

Every other layer of Deckle already works in the *displayed* frame.
``loader`` measures with pdfium, which applies ``/Rotate``, so a 400x600
page marked ``/Rotate 90`` is recorded as 600x400. The preview rasterises
through pdfium too, and ``--auto-crop`` measures ink through the same
renderer. The user types ``--crop`` against what they saw.

``_cropped_source_box`` was the one place working in the *stored* frame.
It read ``page.cropbox`` -- 400x600, rotation not applied -- subtracted
the insets from those edges, and returned that size. Two things go wrong
at once, and neither announces itself:

- **The wrong edge is cut.** Under ``/Rotate 90`` the stored left edge is
  the displayed top, so ``--crop 100,0,0,0`` ("take 100pt off the left")
  takes it off the top instead.
- **The returned size is transposed.** ``as_form_xobject()`` emits a
  ``/Matrix`` for the rotation, so the form's effective extent is the
  rotated box while the returned size describes the stored one. The
  placement is then scaled from a width and height the wrong way round.

The invariant asserted here is the one the placement arithmetic actually
needs, and it is stated so that all four rotations answer it the same
way: cropping ``(left, bottom, right, top)`` off what the user can see
leaves a page ``(ref.width - left - right)`` by ``(ref.height - bottom -
top)``, measured in the same frame ``ref`` is. Parametrised over every
rotation rather than asserted for 90 alone, because 180 transposes
nothing and would have hidden half the defect.
"""

from __future__ import annotations

import os

import pikepdf
import pytest

from deckle.core.export import _cropped_source_box
from deckle.core.loader import load_pdf
from deckle.core.models import SourceRef

ROTATIONS = [0, 90, 180, 270]


@pytest.fixture
def source(tmp_path):
    """A 400x600 source, and a factory for the same file at any rotation."""

    def build(rotate: int) -> tuple[str, SourceRef]:
        path = os.path.join(str(tmp_path), f"src{rotate}.pdf")
        pdf = pikepdf.Pdf.new()
        for _ in range(2):
            pdf.add_blank_page(page_size=(400.0, 600.0))
        for page in pdf.pages:
            page.obj["/Rotate"] = rotate
        pdf.save(path)
        pdf.close()
        return path, load_pdf(path)[0].ref

    return build


@pytest.mark.parametrize("rotate", ROTATIONS)
def test_the_loader_records_the_displayed_size(source, rotate):
    """The premise the rest of this file rests on: every other layer is
    already in the displayed frame, so the exporter is the odd one out."""
    _, ref = source(rotate)

    expected = (600.0, 400.0) if rotate in (90, 270) else (400.0, 600.0)
    assert (ref.width_pt, ref.height_pt) == expected


@pytest.mark.parametrize("rotate", ROTATIONS)
def test_a_crop_leaves_the_size_the_user_asked_for(source, rotate):
    path, ref = source(rotate)
    left, bottom, right, top = 100.0, 10.0, 20.0, 30.0

    with pikepdf.open(path) as pdf:
        size = _cropped_source_box(pdf.pages[0], (left, bottom, right, top), ref)

    assert size == (
        ref.width_pt - left - right,
        ref.height_pt - bottom - top,
    )


@pytest.mark.parametrize("rotate", ROTATIONS)
def test_an_absent_crop_reports_the_page_unchanged(source, rotate):
    path, ref = source(rotate)

    with pikepdf.open(path) as pdf:
        size = _cropped_source_box(pdf.pages[0], None, ref)

    assert size == (ref.width_pt, ref.height_pt)


def test_the_cut_lands_on_the_edge_the_user_can_see(source):
    """The half of the defect that a size check alone cannot catch: a
    transposed *size* and a cut on the wrong *edge* are separate errors,
    and under ``/Rotate 180`` the size is right while the edge is not.

    Under ``/Rotate 180`` the displayed left edge is the stored right one,
    so taking 100pt off the left must shrink the stored box at its top-right
    corner, never at its origin.
    """
    path, ref = source(180)

    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        _cropped_source_box(page, (100.0, 0.0, 0.0, 0.0), ref)
        box = [float(v) for v in page.cropbox]

    assert box == [0.0, 0.0, 300.0, 600.0]


def test_a_rotated_page_is_cut_on_its_displayed_left_not_its_stored_left(source):
    """``/Rotate 90`` maps the stored left edge to the displayed top, so a
    ``--crop`` of the left must move the stored *bottom*."""
    path, ref = source(90)

    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        _cropped_source_box(page, (100.0, 0.0, 0.0, 0.0), ref)
        box = [float(v) for v in page.cropbox]

    assert box == [0.0, 100.0, 400.0, 600.0]


@pytest.mark.parametrize("rotate", ROTATIONS)
def test_the_form_the_exporter_draws_matches_the_size_it_reports(source, rotate):
    """The two must agree or the placement is scaled wrongly, and this is
    the disagreement the defect consisted of.

    ``as_form_xobject`` puts the rotation in a ``/Matrix`` rather than in
    the ``BBox``, so the form's effective extent is the BBox with the
    rotation applied -- which is what the reported size has to describe.
    """
    path, ref = source(rotate)

    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        reported = _cropped_source_box(page, (100.0, 10.0, 20.0, 30.0), ref)
        form = pikepdf.Page(page).as_form_xobject()
        bbox = [float(v) for v in form.BBox]

    stored = (abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1]))
    effective = (stored[1], stored[0]) if rotate in (90, 270) else stored
    assert reported == effective
