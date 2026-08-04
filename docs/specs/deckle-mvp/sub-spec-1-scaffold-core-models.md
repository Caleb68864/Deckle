---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-01
sub_spec_number: 1
title: "Project scaffold, repo files, and core data models"
depends_on: []
date: 2026-08-04
---

# SS-01 — Project scaffold, repo files, and core data models

## Context

Deckle is a greenfield offline desktop app (Windows + Linux) for PDF binding-margin prep
and manual-duplex printing. **This sub-spec establishes every convention the other 13
follow** — naming, typing, test layout, module boundaries. Get it right; everything
inherits from it.

The organizing idea is the **four-level page model**: source pages → output pages → sheets
→ print passes. Name things after it. This vocabulary appears in every later sub-spec.

**Hard architectural rule introduced here:** `deckle.core` must never import Qt. This
sub-spec ships the automated test that enforces it.

## Provides

| Symbol | Consumed by |
|---|---|
| `Placement` | SS-03, SS-04, SS-05 |
| `SourceRef`, `SourcePage` | SS-02, SS-03, SS-05, SS-07, SS-09 |
| `OutputPage`, `Sheet`, `SheetPlan` | SS-03, SS-04, SS-05, SS-06, SS-11 |
| `LayoutSettings` | SS-03, SS-10 |
| `LayoutWarning` | SS-02, SS-03, SS-10 |
| `tests/test_core_purity.py` | all core sub-specs (regression guard) |

## Requires

Nothing. This is the root of the dependency graph.

## Implementation Steps

### Step 1. Write the failing purity test

Create `tests/test_core_purity.py`. It walks every module under `deckle/core/`, imports each
in a subprocess, and asserts no `PySide6.*` or `PyQt*` module appears in `sys.modules`.

Use a subprocess per module so one import cannot mask another.

### Step 2. Run it and watch it fail

```bash
python -m pytest tests/test_core_purity.py -q
```

Expect collection failure — `deckle` does not exist yet.

### Step 3. Create the package skeleton

Create `deckle/__init__.py`, `deckle/core/__init__.py`, `tests/__init__.py`. Empty is fine.

### Step 4. Run the purity test again

```bash
python -m pytest tests/test_core_purity.py -q
```

Now passes trivially (zero modules to check). That is correct — it becomes meaningful as
core modules land.

### Step 5. Write failing model tests

Create `tests/test_models.py` asserting each dataclass exists, is frozen, and has the exact
field names and types from the master spec. Include one test asserting
`LayoutSettings().scale_mode == "fit_height"` — the default matters and is easy to flip by
accident.

### Step 6. Implement the models

**Post-red-team model changes — all three are load-bearing, not cosmetic:**

- **`Project(pages: list[SourcePage], layout: LayoutSettings, printer: str | None)`** — the
  document model. It was referenced by SS-07 and SS-09 but defined nowhere (C-1).
- **`SourceRef` gains `width_pt: float, height_pt: float`** — each page's own media-box
  geometry. Without it `Imposer` cannot compute a per-page transform without opening the
  PDF, which violates its purity requirement *and* trips its own no-I/O check (C-2).
- **`SheetPlan` gains `paper_pt: tuple[float, float]`** — the output sheet size. `Exporter`
  needs it for `add_blank_page(page_size=...)` and `Rasterizer` for its canvas; neither
  receives `LayoutSettings` (C-3). Paper size belongs to the plan, not to a consumer.

Create `deckle/core/models.py` with all eight types. Every one is a **frozen dataclass**
with full type annotations. Stdlib only — no third-party imports in this module.

`Placement` fields are in **PDF points, origin bottom-left**. Document that in the docstring;
it is the single most common source of coordinate bugs downstream.

### Step 7. Run model tests to green

```bash
python -m pytest tests/test_models.py -q
```

### Step 8. Write repo files

- `LICENSE` — **already exists** at the repo root (MIT, copyright "Caleb Bennett", from GitHub's initial commit). Verify only; do not create or overwrite.
- `README.md` — purpose, the four-level page model, MIT license, "not yet released" status.
- `CHANGELOG.md` — Keep a Changelog format with an `## [Unreleased]` section.
- `.gitignore` — Python (`__pycache__/`, `*.pyc`, `.venv/`), PyInstaller (`build/`, `dist/`,
  `*.spec`), Qt (`*.qmlc`), `tests/fixtures/*.pdf` (fixtures are large and external), and `*.stackdump` (Git Bash leaves these in the working tree).
- `pyproject.toml` — dependencies `pikepdf`, `pypdfium2`, `img2pdf`, `natsort`, `Pillow`,
  `PySide6`; `license = "MIT"`; pytest config. **Do not add `pypdf`.**

### Step 9. Full suite green, then commit

```bash
python -m pytest -q
git add -A && git commit -m "feat(SS-01): scaffold, repo files, and core data models"
```

## Interface Contracts

### Placement
- Direction: SS-01 → SS-03, SS-04, SS-05
- Owner: SS-01
- Shape: `Placement(scale_x: float, scale_y: float, tx: float, ty: float, rotate_deg: int)`,
  frozen, PDF points, origin bottom-left.

### SheetPlan
- Direction: SS-01 → SS-03, SS-04, SS-05, SS-06, SS-11
- Owner: SS-01
- Shape: `SheetPlan(sheets: list[Sheet], warnings: list[LayoutWarning])`;
  `Sheet(index: int, front: OutputPage | None, back: OutputPage | None)`;
  `OutputPage(source_ref: SourceRef | None, placement: Placement, is_filler: bool)`.
  A `None` side is an intentionally blank side; `is_filler=True` marks generated padding.

### LayoutSettings
- Direction: SS-01 → SS-03, SS-10
- Owner: SS-01
- Shape: `paper: tuple[float, float]`, `gutter_pt: float`,
  `binding_edge: Literal["left","right","top"]`,
  `scale_mode: Literal["fit_height","fixed_gutter"]` **defaulting to `"fit_height"`**,
  `start_on_recto: bool`, `landscape_policy: Literal["rotate","scale","letterbox"]`.

## Verification Commands

```bash
python -m pytest tests/test_models.py tests/test_core_purity.py -q
python -c "import deckle.core.models"
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| Placement dataclass defined | [STRUCTURAL] | `grep -q "class Placement" deckle/core/models.py \|\| (echo "FAIL: Placement not defined" && exit 1)` |
| SheetPlan / Sheet / OutputPage defined | [STRUCTURAL] | `for s in SheetPlan Sheet OutputPage SourceRef SourcePage; do grep -q "class $s" deckle/core/models.py \|\| (echo "FAIL: $s not defined" && exit 1); done` |
| LayoutSettings defaults to fit_height | [STRUCTURAL] | `grep -q 'fit_height' deckle/core/models.py \|\| (echo "FAIL: fit_height default missing" && exit 1)` |
| LayoutWarning defined | [STRUCTURAL] | `grep -q "class LayoutWarning" deckle/core/models.py \|\| (echo "FAIL: LayoutWarning not defined" && exit 1)` |
| Core purity test passes | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity violated" && exit 1)` |
| models imports with no third-party dep | [MECHANICAL] | `python -c "import deckle.core.models" \|\| (echo "FAIL: models.py import failed" && exit 1)` |
| pyproject declares pikepdf, not pypdf | [STRUCTURAL] | `grep -q "pikepdf" pyproject.toml && ! grep -q '"pypdf"' pyproject.toml \|\| (echo "FAIL: wrong PDF library declared" && exit 1)` |
| README documents the page model | [STRUCTURAL] | `grep -qi "four-level\|source.*output.*sheet.*pass" README.md \|\| (echo "FAIL: README missing page model" && exit 1)` |
