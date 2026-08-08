"""The four layout settings the desktop panel could not reach.

The panel exposed 16 of `LayoutSettings`' 20 fields. The four it did not
were `crop_odd_pt`, `crop_even_pt`, `trim_pt` and `signature_lengths` --
so cropping, the feature the README calls "what keeps type readable at a
small trim size", was unreachable without a terminal.

Pure setters only, as the rest of this module's suite does.
"""

from __future__ import annotations

import pytest

from deckle.app.views.layout_panel import (
    recompute_plan, set_crop, set_signature_lengths, set_trim,
)
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef

CROP = (50.0, 10.0, 20.0, 30.0)


def _project(pages=16, **layout) -> Project:
    base = dict(paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left")
    base.update(layout)
    return Project(
        pages=[
            SourcePage(
                ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                              width_pt=400.0, height_pt=600.0),
                rotate_deg=0, skipped=False,
            )
            for i in range(pages)
        ],
        layout=LayoutSettings(**base), printer=None,
    )


# -- crop ----------------------------------------------------------------


@pytest.mark.parametrize("parity,field", [
    ("odd", "crop_odd_pt"), ("even", "crop_even_pt"),
])
def test_each_parity_crops_independently(parity, field):
    """A scan's gutter swaps sides every leaf, and one rectangle cannot
    fit both -- which is the whole reason there are two."""
    project = set_crop(_project(), parity, CROP)

    assert getattr(project.layout, field) == CROP
    other = "crop_even_pt" if field == "crop_odd_pt" else "crop_odd_pt"
    assert getattr(project.layout, other) is None


def test_a_crop_can_be_removed_again():
    project = set_crop(_project(), "odd", CROP)

    assert set_crop(project, "odd", None).layout.crop_odd_pt is None


def test_an_unknown_parity_is_refused():
    with pytest.raises(ValueError) as caught:
        set_crop(_project(), "middle", CROP)

    assert "middle" in str(caught.value)


def test_a_crop_reaches_the_imposed_plan():
    """The end that matters: the setting has to change the book, not just
    the settings object."""
    plain = recompute_plan(_project())
    cropped = recompute_plan(set_crop(_project(), "odd", CROP))

    assert plain.sheets[0].front.pages[0].placement != (
        cropped.sheets[0].front.pages[0].placement
    )


# -- trim ----------------------------------------------------------------


def test_a_trim_draws_cut_lines():
    project = set_trim(_project(fold_scheme="folio"), 18.0)

    marks = [
        m for sheet in recompute_plan(project).sheets
        for side in (sheet.front, sheet.back) if side
        for m in side.marks
    ]
    assert any(m.kind == "cut_line" for m in marks)


def test_zero_trim_draws_none():
    """`0` disables the marks, exactly as `sewing_stations = 0` does,
    rather than adding a boolean a number could already express."""
    project = set_trim(_project(fold_scheme="folio"), 0.0)

    marks = [
        m for sheet in recompute_plan(project).sheets
        for side in (sheet.front, sheet.back) if side
        for m in side.marks
    ]
    assert not any(m.kind == "cut_line" for m in marks)


# -- explicit gatherings -------------------------------------------------


def test_stated_gatherings_are_honoured():
    project = set_signature_lengths(_project(pages=16, fold_scheme="folio"), "2,2")

    plan = recompute_plan(project)

    assert [len(s.sheet_indices) for s in plan.signatures] == [2, 2]


def test_whitespace_around_the_numbers_is_forgiven():
    project = set_signature_lengths(_project(fold_scheme="folio"), " 2 , 2 ")

    assert project.layout.signature_lengths == (2, 2)


def test_an_empty_field_returns_to_a_uniform_size():
    project = set_signature_lengths(_project(fold_scheme="folio"), "2,2")

    assert set_signature_lengths(project, "   ").layout.signature_lengths is None


@pytest.mark.parametrize("text", ["2,x", "two", "2,,2", "2.5,2"])
def test_a_malformed_list_is_refused_with_the_offending_part(text):
    with pytest.raises(ValueError):
        set_signature_lengths(_project(), text)


def test_a_zero_length_gathering_is_refused():
    with pytest.raises(ValueError) as caught:
        set_signature_lengths(_project(), "2,0,2")

    assert "at least one sheet" in str(caught.value)


def test_lengths_that_do_not_add_up_are_left_to_the_imposer():
    """Whether they sum to the sheet count is not knowable until the
    document is imposed, and `split_signatures_at` already refuses a
    mismatch naming both numbers. A second check here would be free to
    disagree with the first."""
    project = set_signature_lengths(_project(pages=16, fold_scheme="folio"), "99")

    assert project.layout.signature_lengths == (99,)
    with pytest.raises(ValueError) as caught:
        recompute_plan(project)
    assert "99" in str(caught.value)
