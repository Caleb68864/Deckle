"""Choosing paper, and taking the gathering size it suggests.

The panel asked for a caliper in a four-decimal spin box. These cover the
two ways to avoid that -- a named paper, or a weight off the ream wrapper
-- and the advice that follows from either.

Only the pure module-level functions are exercised, as the rest of this
module's suite does: the Qt widgets are wired but never instantiated in a
headless run.
"""

from __future__ import annotations

import pytest

from deckle.app.state import AppState
from deckle.app.views.layout_panel import (
    apply_suggested_sheets,
    set_paper_from_weight,
    set_paper_stock,
    set_sheets_per_signature,
    set_trim,
    signature_suggestion,
    signature_suggestion_text,
)
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.paper import PAPER_PRESETS as PAPER_STOCKS
from deckle.core.paper import caliper_pt_from_gsm


def _project(**layout) -> Project:
    base = dict(paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left",
                fold_scheme="folio")
    base.update(layout)
    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(16)
    ]
    return Project(pages=pages, layout=LayoutSettings(**base), printer=None)


# -- choosing the paper --------------------------------------------------


def test_a_named_paper_sets_the_thickness():
    project = set_paper_stock(_project(), "80gsm copier")

    assert project.layout.paper_thickness_pt == pytest.approx(
        caliper_pt_from_gsm(80, "copier")
    )


def test_every_preset_in_the_list_can_be_chosen():
    """The dropdown is built from this list, so a name it cannot resolve
    would be an option that does nothing when clicked."""
    for stock in PAPER_STOCKS:
        assert set_paper_stock(_project(), stock.name).layout.paper_thickness_pt > 0


def test_an_unknown_paper_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        set_paper_stock(_project(), "vellum")

    assert "vellum" in str(caught.value)


def test_a_custom_grammage_sets_the_thickness():
    project = set_paper_from_weight(_project(), 90, "gsm", "offset")

    assert project.layout.paper_thickness_pt == pytest.approx(
        caliper_pt_from_gsm(90, "offset")
    )


def test_a_custom_pound_weight_needs_a_grade():
    """20lb is 75gsm as bond and 54gsm as cover -- guessing would be wrong
    by half and the result would look entirely reasonable."""
    with pytest.raises(ValueError) as caught:
        set_paper_from_weight(_project(), 20, "lb", "offset")

    assert "grade" in str(caught.value).lower()


def test_a_pound_weight_with_a_grade_is_accepted():
    project = set_paper_from_weight(_project(), 20, "lb", "offset", grade="bond")

    assert project.layout.paper_thickness_pt > 0


def test_the_panel_and_the_command_line_agree_on_one_paper():
    """Two entry points to the same setting must not describe different
    books."""
    from deckle.core.paper import caliper_pt_from_gsm as cli_path

    panel = set_paper_from_weight(_project(), 80, "gsm", "copier")
    assert panel.layout.paper_thickness_pt == cli_path(80, "copier")


# -- the suggestion ------------------------------------------------------


def test_no_thickness_means_no_advice():
    """Most projects never set a thickness, and silence is the honest
    answer without it."""
    assert signature_suggestion_text(_project().layout) is None


def test_advice_appears_once_a_paper_is_chosen():
    project = set_paper_stock(_project(sheets_per_signature=4), "160gsm card")

    text = signature_suggestion_text(project.layout)

    assert text and "sheets" in text


def test_advice_says_nothing_when_the_setting_already_matches():
    """Advice that repeats the current state back is noise, and noise is
    what teaches people to stop reading advisories."""
    project = set_paper_stock(_project(), "80gsm copier")
    suggestion = signature_suggestion(project.layout)
    project = set_sheets_per_signature(project, suggestion.sheets)

    assert signature_suggestion_text(project.layout) is None


def test_advice_names_the_trim_when_creep_is_what_binds():
    """The remedy differs by constraint, so the sentence has to say which
    one: creep means plan a trim, fold means this paper is thick."""
    project = set_paper_stock(_project(sheets_per_signature=1), "80gsm copier")

    text = signature_suggestion_text(project.layout)

    assert "trim" in text.lower()


def test_advice_names_the_fold_when_bulk_is_what_binds():
    project = set_paper_stock(_project(sheets_per_signature=1), "80gsm copier")
    project = set_trim(project, 18.0)

    text = signature_suggestion_text(project.layout)

    assert "fold" in text.lower()


def test_a_planned_trim_allows_a_bigger_gathering():
    """The whole reason the tolerance keys off `trim_pt`: creep is
    absorbed by trimming."""
    plain = set_paper_stock(_project(), "80gsm copier")
    trimmed = set_trim(plain, 18.0)

    assert (signature_suggestion(trimmed.layout).sheets
            > signature_suggestion(plain.layout).sheets)


# -- taking it -----------------------------------------------------------


def test_applying_the_suggestion_sets_the_sheet_count():
    project = set_paper_stock(_project(sheets_per_signature=1), "80gsm copier")
    expected = signature_suggestion(project.layout).sheets

    applied = apply_suggested_sheets(project)

    assert applied.layout.sheets_per_signature == expected


def test_applying_twice_changes_nothing_the_second_time():
    """Once taken, the advice disappears -- so the button cannot drift the
    setting further on a second press."""
    project = set_paper_stock(_project(sheets_per_signature=1), "80gsm copier")

    once = apply_suggested_sheets(project)
    twice = apply_suggested_sheets(once)

    assert twice.layout.sheets_per_signature == once.layout.sheets_per_signature
    assert signature_suggestion_text(once.layout) is None


def test_applying_with_no_thickness_is_a_no_op():
    project = _project(sheets_per_signature=4)

    assert apply_suggested_sheets(project).layout.sheets_per_signature == 4


def test_the_applied_size_survives_imposition():
    """The end that matters: the suggested number has to produce the
    gatherings it promised, not merely sit in the settings."""
    from deckle.app.views.layout_panel import recompute_plan

    project = apply_suggested_sheets(
        set_paper_stock(_project(sheets_per_signature=1), "160gsm card")
    )
    plan = recompute_plan(project)

    assert plan.signatures
    assert max(len(s.sheet_indices) for s in plan.signatures) <= (
        project.layout.sheets_per_signature
    )
