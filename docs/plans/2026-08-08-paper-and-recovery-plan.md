# Paper Specification, Signature Sizing and Autosave Recovery — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let a binder state their paper by weight, get a signature size suggested from it, recover an autosave after a crash, and reach the four layout settings the desktop app cannot currently touch.

**Architecture:** One new Qt-free core module (`deckle/core/paper.py`) holds every number and rule, so the CLI and the GUI both reach it and the suite tests it headlessly. The desktop work is wiring: a pure decision function plus a dialog for recovery, and four controls following the layout panel's existing `set_*` pure-function pattern.

**Tech Stack:** Python 3.12, PySide6 (GUI only), pytest. No new dependencies.

**Design doc:** `docs/plans/2026-08-08-paper-and-recovery-design.md` — read it first; it records why the fold-bulk cap is 1.8mm and why creep tolerance keys off `trim_pt`.

---

## Conventions for every task

- **TDD**: write the test, run it red, implement, run it green, commit.
- **Commit hook**: this repo requires a `docs/decisions.md` entry per commit. Append one before `git add`. Symptom / Fix / Surfaces / Watch / Commit.
- **Run the full suite** (`python -m pytest tests -q`) before each commit, not just the new file.
- **Docstrings**: this codebase writes *why*, not *what*. Match the surrounding density.
- **New module needs docs**: `tests/test_docs_coverage.py` fails if a module has no `docs/api/*.rst` **and** no toctree entry in `docs/api/core.rst`.

---

## Task 1: Weight conversions

**Files:**
- Create: `deckle/core/paper.py`
- Test: `tests/test_paper.py`

**Step 1: Write the failing test**

```python
"""Stating paper by what is printed on the ream wrapper."""
import pytest
from deckle.core.paper import gsm_from_pounds

# Published pairs. A transcription error in a factor fails here rather
# than silently shifting every caliper the program derives.
@pytest.mark.parametrize("pounds,grade,expected_gsm", [
    (20, "bond", 75.2), (24, "bond", 90.2), (28, "bond", 105.3),
    (70, "text", 103.6), (80, "text", 118.4),
    (65, "cover", 175.8), (80, "cover", 216.3),
    (90, "index", 162.7),
    (100, "tag", 162.7),
])
def test_us_basis_weights_convert_to_grammage(pounds, grade, expected_gsm):
    assert gsm_from_pounds(pounds, grade) == pytest.approx(expected_gsm, abs=0.5)


def test_an_unknown_grade_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        gsm_from_pounds(20, "newsprint")
    assert "newsprint" in str(caught.value)
    assert "bond" in str(caught.value)   # the message lists what is accepted
```

**Step 2: Run it red**

`python -m pytest tests/test_paper.py -q` → ModuleNotFoundError.

**Step 3: Implement**

```python
"""Paper: stating it by weight, and sizing a gathering from it.

Deckle asked for paper thickness as a caliper. Nobody knows their
paper's caliper; everybody has its weight printed on the ream wrapper.
This module turns the number a binder has into the number the imposer
needs, and then answers the question that follows from it -- how many
sheets should a gathering hold?

Qt-free, so the CLI reaches it too: a binder scripting a job should not
have to compute a caliper by hand either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PT_PER_MM = 72.0 / 25.4

# US basis weights are pounds per ream of a grade's own basis size, so
# "20 lb" means nothing without knowing which grade. Derived from the
# basis sizes rather than tabulated:
#   gsm = lb * 453.592 / (500 * w * h * 0.00064516)
GRADE_BASIS_SIZES_IN = {
    "bond": (17.0, 22.0),
    "text": (25.0, 38.0),
    "cover": (20.0, 26.0),
    "index": (25.5, 30.5),
    "tag": (24.0, 36.0),
}


def gsm_from_pounds(pounds: float, grade: str) -> float:
    """Grammage from a US basis weight.

    :param pounds: the number on the wrapper.
    :param grade: which basis size it is quoted against.
    :returns: grams per square metre.
    :raises ValueError: an unknown grade, naming the accepted ones --
        "20 lb" is ambiguous without it, and guessing would put every
        derived caliper out by up to 2.5x.
    """
    try:
        width_in, height_in = GRADE_BASIS_SIZES_IN[grade]
    except KeyError:
        raise ValueError(
            f"unknown paper grade {grade!r}: expected one of "
            + ", ".join(sorted(GRADE_BASIS_SIZES_IN))
        ) from None
    ream_m2 = 500 * width_in * height_in * 0.00064516
    return pounds * 453.592 / ream_m2
```

**Step 4: Run green.** **Step 5: Commit** `feat(paper): convert US basis weights to grammage`.

---

## Task 2: Weight to caliper, and the presets

**Files:** Modify `deckle/core/paper.py`, `tests/test_paper.py`

**Step 1: Failing test**

```python
from deckle.core.paper import PAPER_PRESETS, caliper_pt_from_gsm, PT_PER_MM

def test_office_paper_lands_where_a_caliper_would_read_it():
    """80gsm copier measures about 0.104mm in the hand."""
    mm = caliper_pt_from_gsm(80, "copier") / PT_PER_MM
    assert mm == pytest.approx(0.104, abs=0.002)

def test_bulk_is_what_separates_two_papers_of_one_weight():
    """The same grammage is thicker as a bulky book paper than as a
    coated one -- which is the whole reason weight alone is not enough."""
    assert caliper_pt_from_gsm(100, "bulky") > caliper_pt_from_gsm(100, "coated")

def test_an_unknown_type_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        caliper_pt_from_gsm(80, "papyrus")
    assert "papyrus" in str(caught.value)

def test_every_preset_carries_a_plausible_caliper():
    """Guards a typo in the table: no real paper is thinner than 0.03mm
    or thicker than 0.5mm in this range."""
    assert PAPER_PRESETS
    for preset in PAPER_PRESETS:
        mm = preset.caliper_pt / PT_PER_MM
        assert 0.03 < mm < 0.5, preset

def test_presets_are_ordered_thinnest_first():
    """A dropdown that jumps around is harder to scan than one that does
    not."""
    calipers = [p.caliper_pt for p in PAPER_PRESETS]
    assert calipers == sorted(calipers)
```

**Step 3: Implement**

```python
# Bulk (specific volume, cm3/g) is what separates two papers of the same
# grammage. It varies about +/-10% between manufacturers, so a derived
# caliper is an estimate -- see `spine_width_pt`, which already reports a
# range rather than false precision for the same reason.
PAPER_BULK = {
    "copier": 1.30,
    "laser": 1.25,
    "offset": 1.30,
    "bulky": 1.70,
    "coated": 0.90,
}


def caliper_pt_from_gsm(gsm: float, paper_type: str) -> float:
    """One sheet's thickness in points, from grammage and paper type."""
    try:
        bulk = PAPER_BULK[paper_type]
    except KeyError:
        raise ValueError(
            f"unknown paper type {paper_type!r}: expected one of "
            + ", ".join(sorted(PAPER_BULK))
        ) from None
    return (gsm * bulk / 1000.0) * PT_PER_MM


@dataclass(frozen=True)
class PaperPreset:
    """A named paper a binder can pick without measuring anything."""
    name: str
    gsm: float
    paper_type: str

    @property
    def caliper_pt(self) -> float:
        return caliper_pt_from_gsm(self.gsm, self.paper_type)


PAPER_PRESETS = (
    PaperPreset("80gsm copier", 80, "copier"),
    PaperPreset("90gsm laser", 90, "laser"),
    PaperPreset("100gsm offset", 100, "offset"),
    PaperPreset("120gsm cartridge", 120, "offset"),
    PaperPreset("160gsm card", 160, "offset"),
)
```

**Step 5: Commit** `feat(paper): derive a caliper from weight and paper type`.

---

## Task 3: Suggest a signature size

**Files:** Modify `deckle/core/paper.py`, `tests/test_paper.py`

**Step 1: Failing test — the calibration table from the design doc**

```python
from deckle.core.paper import suggest_sheets_per_signature

@pytest.mark.parametrize("gsm,paper_type,no_trim,with_trim", [
    (80,  "copier", 4, 8),
    (100, "offset", 3, 6),
    (120, "offset", 3, 5),
    (160, "offset", 2, 4),
])
def test_the_suggestion_matches_the_calibration_table(gsm, paper_type, no_trim, with_trim):
    """The table in the design doc, which is the only validation available
    for constants no test can settle. 80gsm reaching 8 sheets -- a 32-page
    gathering, the standard trade signature -- is the evidence that 1.8mm
    is honest rather than tuned."""
    caliper = caliper_pt_from_gsm(gsm, paper_type)
    assert suggest_sheets_per_signature(caliper).sheets == no_trim
    assert suggest_sheets_per_signature(caliper, trim_pt=18.0).sheets == with_trim


def test_thin_paper_with_no_trim_is_limited_by_creep():
    """Which constraint bound the answer decides the remedy, so it has to
    be reported, not just used."""
    caliper = caliper_pt_from_gsm(80, "copier")
    assert suggest_sheets_per_signature(caliper).limited_by == "creep"


def test_thin_paper_with_a_trim_is_limited_by_the_fold():
    caliper = caliper_pt_from_gsm(80, "copier")
    assert suggest_sheets_per_signature(caliper, trim_pt=18.0).limited_by == "fold"


def test_a_suggestion_never_drops_below_one_sheet():
    """Card thick enough to bind the constraints to zero still has to
    produce a gathering someone can fold."""
    assert suggest_sheets_per_signature(caliper_pt_from_gsm(400, "offset")).sheets >= 1


def test_no_thickness_means_no_suggestion():
    """Thickness is optional, and silence is the honest answer without it
    -- the same contract `spine_width_pt` keeps."""
    assert suggest_sheets_per_signature(0.0) is None


def test_the_reported_creep_matches_the_schedule_module():
    """Two formulas for one physical quantity would drift. This is the
    same `(sheets - 1) * caliper` the schedule already prints."""
    caliper = caliper_pt_from_gsm(100, "offset")
    s = suggest_sheets_per_signature(caliper, trim_pt=18.0)
    assert s.creep_pt == pytest.approx((s.sheets - 1) * caliper)
```

**Step 3: Implement**

```python
# Under this, fore-edge creep is invisible and needs no trimming. Shared
# with `schedule._creep_note`, which must import it rather than repeat it.
CREEP_INVISIBLE_PT = 1.0

# A folded gathering of n nested sheets has 2n layers of paper at the
# fold. Past about 1.8mm it stops folding cleanly and is awkward to sew.
# Calibrated against traditional signature sizes rather than chosen to
# produce them: 80gsm office paper falls out at 8 sheets, a 32-page
# gathering, which is the standard trade signature.
FOLD_BULK_LIMIT_MM = 1.8


@dataclass(frozen=True)
class SignatureSuggestion:
    sheets: int
    pages: int
    creep_pt: float
    limited_by: Literal["creep", "fold"]


def suggest_sheets_per_signature(
    caliper_pt: float, trim_pt: float = 0.0
) -> SignatureSuggestion | None:
    """How many sheets a gathering of this paper should hold.

    Two constraints, and the answer is the smaller:

    - **Creep.** Nested sheets push each other out at the fore-edge by
      ``(sheets - 1) * caliper``. Tolerance is ``trim_pt`` when the binder
      plans to plough the fore-edge, else the point below which it is
      invisible -- creep is *absorbed by trimming*, so someone who plans
      one can carry far more of it.
    - **Fold bulk.** ``2 * sheets * caliper`` at the spine, capped at
      1.8mm.

    Creep alone is not enough and gives absurd answers: 80gsm with a
    quarter-inch trim tolerates 62 sheets before creep shows, and nobody
    hand-sews a 62-sheet gathering. The fold cap is what makes it
    physical.

    :param caliper_pt: one sheet's thickness.
    :param trim_pt: planned fore-edge trim, or 0 for none.
    :returns: the suggestion, or ``None`` when thickness is unset --
        silence is the honest answer without it.
    """
    if caliper_pt <= 0:
        return None
    tolerance = trim_pt if trim_pt > 0 else CREEP_INVISIBLE_PT
    by_creep = int(tolerance // caliper_pt) + 1
    by_fold = int((FOLD_BULK_LIMIT_MM * PT_PER_MM) // (2 * caliper_pt))
    sheets = max(1, min(by_creep, by_fold))
    return SignatureSuggestion(
        sheets=sheets,
        pages=sheets * 4,          # folio: one sheet is four pages
        creep_pt=(sheets - 1) * caliper_pt,
        limited_by="creep" if by_creep <= by_fold else "fold",
    )
```

**Step 4:** Also change `schedule.py` to `from deckle.core.paper import CREEP_INVISIBLE_PT` and use it in `_creep_note` instead of the literal `1.0`. Run the full suite.

**Step 5: Commit** `feat(paper): suggest a gathering size from caliper and trim`.

---

## Task 4: Docs page for the new module

**Files:** Create `docs/api/core.paper.rst`; modify `docs/api/core.rst`

```rst
deckle.core.paper
=================

.. automodule:: deckle.core.paper
   :members:
   :show-inheritance:
```

Add `   core.paper` to the toctree in `docs/api/core.rst`, after `core.schema`.

Verify: `python -m pytest tests/test_docs_coverage.py -q` and
`python -m sphinx -b html -W --keep-going docs/api docs/api/_build/html`.

**Commit** `docs: add core.paper to the API reference`.

---

## Task 5: CLI — state paper by weight

**Files:** Modify `deckle/cli.py`; test `tests/test_cli_paper.py`

**Step 1: Failing tests**

```python
def test_weight_and_type_set_the_thickness(tmp_path):
    """`--paper-weight 80gsm --paper-type copier` must reach the schedule
    the same as `--paper-thickness 0.295pt` would."""
    result = _cli("schedule", FIXTURE, "--fold-scheme", "folio",
                  "--paper-weight", "80gsm", "--paper-type", "copier")
    assert result.returncode == 0
    assert "spine" in result.stdout.lower()

def test_pounds_need_a_grade(tmp_path):
    """`20lb` is ambiguous; the error must say so and list the grades."""
    result = _cli("info", FIXTURE, "--paper-weight", "20lb")
    assert result.returncode != 0
    assert "grade" in result.stderr.lower()

def test_weight_and_explicit_thickness_conflict(tmp_path):
    """Two ways to say one thing, silently picking one, is how a user
    ends up with a book bound to a number they did not give."""
    result = _cli("info", FIXTURE, "--paper-weight", "80gsm",
                  "--paper-thickness", "0.3pt")
    assert result.returncode != 0
    assert "--paper-thickness" in result.stderr
```

**Step 3: Implement** — add a `--paper-weight` (accepting `80gsm`, `20lb`), `--paper-type` (default `offset`), `--paper-grade` (for `lb`). Refuse when both `--paper-weight` and `--paper-thickness` are given. Reuse the existing `_parse_*` error style so messages match.

**Commit** `feat(cli): accept paper weight instead of a caliper`.

---

## Task 6: Autosave recovery — the decision

**Files:** Modify `deckle/app/main.py`; test `tests/test_autosave_recovery.py`

**Step 1: Failing tests — all four situations from the design doc**

```python
from deckle.app.main import autosave_recovery_offer

def test_a_newer_autosave_is_offered(tmp_path):
    project, auto = _pair(tmp_path, project_age=100, autosave_age=0)
    assert autosave_recovery_offer(project) == auto

def test_a_saved_project_is_silent(tmp_path):
    project, auto = _pair(tmp_path, project_age=0, autosave_age=100)
    assert autosave_recovery_offer(project) is None

def test_no_autosave_is_silent(tmp_path):
    project = tmp_path / "job.deckle"; project.write_text("{}")
    assert autosave_recovery_offer(str(project)) is None

def test_an_unsaved_project_has_nothing_to_recover():
    assert autosave_recovery_offer(None) is None

def test_a_closed_without_saving_session_is_offered(tmp_path):
    """`_on_close_event` flushes the autosave, so it exists after every
    clean quit. Detection is mtime, not existence -- and getting the crash
    case right gets this one right too: those edits are real."""
    project, auto = _pair(tmp_path, project_age=50, autosave_age=0)
    assert autosave_recovery_offer(project) == auto
```

**Step 3: Implement** a module-level pure function returning the autosave path or `None`, comparing `os.path.getmtime`. Missing project file → offer if the autosave exists.

**Commit** `feat(app): decide when an autosave is worth offering back`.

---

## Task 7: Autosave recovery — the prompt

**Files:** Modify `deckle/app/main.py` (`_open_project` path, near line 889)

Wire an injectable `confirm_recovery` seam (following `_confirm_resume` in `print_dialog.py`), defaulting to a `QMessageBox` with **Recover** / **Discard**. Recover loads the autosave and keeps `project_path`; **Discard deletes the autosave** — otherwise the prompt returns on every open and trains the user to dismiss it. Test both branches through the seam, headlessly.

**Commit** `feat(app): offer an autosave back after a crash`.

---

## Task 8: Layout panel — paper picker and the suggestion

**Files:** Modify `deckle/app/views/layout_panel.py`; test `tests/test_layout_panel_paper.py`

Add pure module-level functions first (the panel's established pattern), then the widgets:

- `set_paper_preset(state, preset_name)` — sets `paper_thickness_pt`.
- `signature_suggestion_text(layout) -> str | None` — the sentence shown, naming the bound constraint and the resulting creep.
- `apply_suggested_sheets(state)` — the Apply button's action.

Widgets: a `QComboBox` of `PAPER_PRESETS` plus `Custom…` revealing weight + type; a label and an **Apply** button under **Sheets per signature**.

Test the pure functions only, as the suite already does for this module.

**Commit** `feat(app): pick paper by name and suggest a gathering size`.

---

## Task 9: Layout panel — crop, trim, gatherings

**Files:** Modify `deckle/app/views/layout_panel.py`; test `tests/test_layout_panel_settings.py`

Three groups, each with a pure setter tested headlessly:

- `set_crop(state, parity, edge, value_pt)` and an **Auto-crop** button calling `render.auto_crop_insets`.
- `set_trim(state, value_pt)`.
- `set_signature_lengths(state, text)` — parses `10,10,8`, and on a sum mismatch surfaces the message `split_signatures_at` already produces rather than inventing a second one.

**Commit** `feat(app): reach crop, trim and explicit gatherings from the panel`.

---

## Task 10: Documentation and changelog

**Files:** `README.md`, `docs/GUIDE.md`, `CHANGELOG.md`

- README capability table: crop/trim/gatherings are no longer CLI-only; add a paper-weight row.
- GUIDE: a short section on stating paper by weight and reading the suggestion, including that the caliper is an **estimate** (bulk varies ±10%).
- CHANGELOG under **Added**.

**Commit** `docs: describe paper weight, signature sizing and recovery`.

---

## Verification before finishing

```bash
python -m pytest tests -q                       # expect all green
python -m sphinx -b html -W --keep-going docs/api docs/api/_build/html
./.buildenv/Scripts/python.exe -m PyInstaller --noconfirm \
    --distpath dist --workpath build packaging/deckle.spec
./dist/deckle/deckle-cli.exe --version
```

The GUI work cannot be verified headlessly beyond its pure functions. Launch it once by hand (`run.bat`) and check: the paper dropdown sets a thickness, the suggestion appears and Apply changes the spin box, crop/trim/gatherings round-trip through save and reopen, and killing the app mid-edit produces a recovery prompt on next open.
