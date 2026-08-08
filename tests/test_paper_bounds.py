"""A stored paper size that is not a paper size.

The type check added for stored layout values asks whether ``paper`` is
two numbers. It is not asking whether those numbers describe a sheet, and
``[-792.0, -612.0]`` is two perfectly good numbers.

What that produces is a placement with a **negative scale**: -1.98 on an
ordinary 400x600 source. A negative scale is a mirror, so the export
comes out reversed -- and ``[0.0, 0.0]`` gives a scale of exactly zero,
so the content vanishes and the sheets come out blank.

Positive scale is already an invariant this project asserts elsewhere.
``test_margins_larger_than_the_sheet_warn_and_fall_back`` exists for the
neighbouring case -- margins bigger than the sheet -- and its whole point
is that the imposer warns and falls back "rather than a negative-size box
and a nonsense scale", asserting ``scale_x > 0.0``. Non-positive paper
walked straight past that.

The rule is not new either. ``--paper 0x0`` has always been refused by
the command line with a message about a PDF page needing positive
dimensions. The project file was the path with no bounds, which is the
same asymmetry that let ``binding_edge: "middle"`` through: argparse
guards what a user types and nothing guarded what a file carries.

**Only ``paper`` is bounded here, deliberately.** The other numeric
settings look like candidates and are not:

- Negative margins and gutters are *clamped to zero by the imposer*, on
  purpose, and ``test_negative_margins_are_clamped_to_zero`` pins that.
  Rejecting them at load would contradict a tested decision.
- ``sheets_per_signature`` and ``signature_lengths`` are already
  validated where they are used, with messages naming the numbers.

So this adds the one bound that is missing rather than a table of bounds
that would have to argue with existing behaviour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings

import pytest

from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core.project_io import load_project

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(tmp_path, paper) -> str:
    path = os.path.join(str(tmp_path), "job.deckle")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "version": 1, "pages": [],
            "layout": {"paper": paper, "gutter_pt": 36.0,
                       "binding_edge": "left"},
        }, f)
    return path


def _load(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_project(path, check_sources=False)


NOT_A_SHEET = [
    pytest.param([-792.0, -612.0], id="both-negative"),
    pytest.param([-792.0, 612.0], id="width-negative"),
    pytest.param([792.0, -612.0], id="height-negative"),
    pytest.param([0.0, 0.0], id="both-zero"),
    pytest.param([792.0, 0.0], id="height-zero"),
    pytest.param([0.0, 612.0], id="width-zero"),
]


@pytest.mark.parametrize("paper", NOT_A_SHEET)
def test_a_paper_size_that_is_not_a_sheet_is_refused(tmp_path, paper):
    path = _write(tmp_path, paper)

    with pytest.raises(ValueError) as caught:
        _load(path)

    assert "paper" in str(caught.value)


@pytest.mark.parametrize("paper", NOT_A_SHEET)
def test_the_cli_reports_it_without_a_traceback(tmp_path, paper):
    path = _write(tmp_path, paper)

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")


def test_the_message_gives_the_measurement_that_is_wrong(tmp_path):
    """Both numbers, so someone editing the file can see which one they
    mistyped rather than being told "paper is invalid"."""
    path = _write(tmp_path, [792.0, -612.0])

    with pytest.raises(ValueError) as caught:
        _load(path)

    assert "-612" in str(caught.value)


def test_an_ordinary_sheet_still_loads(tmp_path):
    path = _write(tmp_path, [792.0, 612.0])

    assert _load(path).layout.paper == (792.0, 612.0)


def test_a_very_small_but_real_sheet_still_loads(tmp_path):
    """The bound is "positive", not "sensible". A postage-stamp book is a
    real thing to want, and this check is not the place to have an opinion
    about it."""
    path = _write(tmp_path, [72.0, 72.0])

    assert _load(path).layout.paper == (72.0, 72.0)


# -- the invariant this protects ----------------------------------------


def _pages(n=2):
    return [
        SourcePage(
            ref=SourceRef(path="s.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("paper", [(-792.0, -612.0), (0.0, 0.0)])
def test_the_imposer_would_have_produced_a_useless_scale(paper):
    """Not a guard -- a record of *why* the guard is at load time.

    The imposer does not defend itself here, and this is what it emits if
    something gets past the reader: a negative scale (mirrored output) or
    a zero one (blank sheets). Kept as a test so that if the imposer is
    ever taught to clamp this the way it clamps negative margins, the
    contradiction shows up here rather than in an argument about which
    layer should own the rule.
    """
    from deckle.cli import _strategy_for

    settings = LayoutSettings(paper=paper, gutter_pt=36.0, binding_edge="left")
    plan = _strategy_for(settings).impose(_pages(), settings)

    scales = [
        page.placement.scale_x
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back) if side
        for page in side.pages if not page.is_filler
    ]
    assert scales and all(s <= 0.0 for s in scales)
