---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-14
sub_spec_number: 14
title: "Integration wiring, golden-fixture regression, and dependency audit"
depends_on: ['SS-07', 'SS-09', 'SS-10', 'SS-12']
date: 2026-08-04
---

# SS-14 — Integration wiring, golden-fixture regression, and dependency audit

## Context

The sub-spec that turns thirteen parts into an application. Three jobs:

1. **Wire everything into entry points** and prove no view is orphaned.
2. **Prove the predecessor's defects are dead** via the Pinebox golden fixture.
3. **Gate the licensing constraint automatically**, so an AGPL dependency can never sneak
   in later.

**Packaging is explicitly out of scope.** One-file vs one-folder is a deferred decision.
Do not add PyInstaller specs, Inno Setup scripts, or AppImage recipes. This sub-spec must
not depend on packaging in any form.

**The golden fixture is external and large.** `Pinebox_Middle_School.pdf` is ~30 MB and
lives outside the repo — currently under a Resilio Sync temp directory, which is not a
durable location. `tests/fixtures/README.md` documents the expected local path, and the test
**skips with a clear message** when the fixture is absent rather than failing CI. A skip that
explains itself is useful; a red build on a missing 30 MB file is not.

**The license audit is not ceremony.** The predecessor script depended on AGPL PyMuPDF, and
two of the most attractive adjacent tools (`pdfimpose`, `cpdf`) are AGPL. A future
contributor reaching for "the obvious imposition library" would silently make Deckle
undistributable. The test catches it.

## Provides

| Symbol | Consumed by |
|---|---|
| `deckle/__main__.py` GUI entry point | end users |
| golden-fixture regression | correctness guard |
| license audit | licensing guard |

## Requires

Everything. This sub-spec depends on SS-07 (CLI, persistence), SS-09 (shell, views),
SS-10 (layout, preview), and SS-12 (print dialog) — and transitively on all the rest.

## Implementation Steps

### Step 1. Write the failing orphan-view test

`tests/test_integration.py`: enumerate every module under `deckle/app/views/` and assert each
is imported by `deckle/app/main.py`.

This catches the classic failure where a view is built, tested in isolation, and never
reachable from the running app.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_integration.py -q
```

### Step 3. Create the GUI entry point

Create `deckle/__main__.py` so `python -m deckle` launches the app. `deckle/cli.py` remains
the headless entry point — two entry points, one core.

### Step 4. Wire every view into MainWindow

Import and mount `ImportView`, `ArrangeView`, `LayoutPanel`, `PreviewView`, `PrintDialog`,
`CalibrationWizard`. Green on the orphan test.

### Step 5. Write the end-to-end integration test

Drive the full flow against a stub `PrintBackend`: import a PDF → import an image directory →
reorder → set gutter → impose → preview → export → plan passes → submit. Assert the final
`SheetPlan` and the stub's received sheet order.

This is the `[INTEGRATION]` criterion. It crosses every sub-spec boundary, which is the point.

### Step 6. Write the license audit

`tests/test_license_audit.py`: enumerate installed distribution licenses via
`importlib.metadata` and fail on any AGPL. Also fail if `PyMuPDF` or `fitz` is importable.

Add a grep guard for `pdfimpose` and `cpdf` — both AGPL, both tempting.

### Step 7. Set up the golden fixture

Write `tests/fixtures/README.md` documenting the expected path for
`Pinebox_Middle_School.pdf` and how to obtain it.

Write `tests/test_golden_pinebox.py`. It must:

- **Skip with an explanatory message** when the fixture is absent.
- Assert **no double-padding** — a 7-page source yields exactly one filler (defect 2).
- Assert **per-page aspect handling** — a page with a differing aspect ratio gets a transform
  derived from its own box (defect 1).
- Assert the output page count matches the expected parity.

### Step 8. Write CONTRIBUTING

`docs/CONTRIBUTING.md` documenting the three rules a newcomer will otherwise break: the
core-purity rule, the four-level page model vocabulary, and the no-external-runtime-binaries
constraint.

### Step 9. Full suite green

```bash
python -m pytest -q
```

Zero failures across everything.

### Step 10. Commit

```bash
git add -A && git commit -m "feat(SS-14): integration wiring, golden fixture, license audit"
```

## Interface Contracts

### deckle/__main__.py
- Direction: SS-14 → end users
- Owner: SS-14
- Shape: `python -m deckle` launches `MainWindow`. Every view module under
  `deckle/app/views/` must be imported by `deckle/app/main.py` — asserted, not assumed.

### license audit
- Direction: SS-14 → the whole project
- Owner: SS-14
- Shape: `tests/test_license_audit.py` fails on any AGPL distribution, on `PyMuPDF`/`fitz`,
  and on `pdfimpose`/`cpdf`. **This is the enforcement mechanism for the project's hardest
  constraint.**

## Verification Commands

```bash
python -m pytest -q
python -m pytest tests/test_integration.py tests/test_license_audit.py -q
python -m pytest tests/test_golden_pinebox.py -q
python -m deckle.cli info tests/fixtures/sample.pdf
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| GUI entry point exists | [STRUCTURAL] | `test -f deckle/__main__.py \|\| (echo "FAIL: deckle/__main__.py missing" && exit 1)` |
| Integration test exists | [STRUCTURAL] | `test -f tests/test_integration.py \|\| (echo "FAIL: integration test missing" && exit 1)` |
| Golden fixture test exists | [STRUCTURAL] | `test -f tests/test_golden_pinebox.py \|\| (echo "FAIL: golden fixture test missing" && exit 1)` |
| Fixture README documents the path | [STRUCTURAL] | `test -f tests/fixtures/README.md \|\| (echo "FAIL: fixture README missing" && exit 1)` |
| CONTRIBUTING documents core purity | [STRUCTURAL] | `grep -qi "core" docs/CONTRIBUTING.md && grep -qi "qt" docs/CONTRIBUTING.md \|\| (echo "FAIL: CONTRIBUTING missing the core purity rule" && exit 1)` |
| No AGPL PDF library anywhere | [MECHANICAL] | `! grep -rn "PyMuPDF\|import fitz\|pdfimpose\|cpdf" deckle/ \|\| (echo "FAIL: AGPL-licensed PDF tooling referenced" && exit 1)` |
| License audit passes | [MECHANICAL] | `python -m pytest tests/test_license_audit.py -q \|\| (echo "FAIL: license audit failed" && exit 1)` |
| Full suite passes | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: full test suite failed" && exit 1)` |
| No packaging artifacts added | [MECHANICAL] | `! ls *.spec 2>/dev/null \|\| (echo "FAIL: packaging is out of scope for the MVP" && exit 1)` |
