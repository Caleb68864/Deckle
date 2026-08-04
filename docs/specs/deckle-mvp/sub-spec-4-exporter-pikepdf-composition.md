---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-04
sub_spec_number: 4
title: "Exporter — PDF composition via pikepdf Form XObjects"
depends_on: ['SS-01', 'SS-03']
date: 2026-08-04
---

# SS-04 — Exporter: PDF composition via pikepdf Form XObjects

## Context

Render a `SheetPlan` to a PDF. Whole document or an arbitrary sheet subset, through one
code path — the subset case is what makes "reprint sheet 7" work, and it must not be a
separate branch.

**Read this first: `Caleb's Vault/Software/pikepdf/pikepdf - Gutter Shift Recipe`, Method
B.** That recipe is this function, already written and verified working against pikepdf
10.11.0 / libqpdf 12.3.2. Also read `[[pikepdf - Form XObject Placement]]` for the API
semantics and `[[pikepdf - Imposition and Signature Recipe]]` for a full worked example.
**Do not compose this from the pikepdf API docs when verified executed code exists.**

**Why this API and not `add_overlay`:** `add_overlay` centers and best-fits. Correct for
watermarks, wrong for imposition. The `as_form_xobject` + `calc_form_xobject_placement`
path with `allow_shrink=False, allow_expand=False` gives exact 1:1 placement — verified to
emit pure translations like `1 0 0 1 396 0 cm` with no scale factor and no rounding drift.

**The invariant this sub-spec must not break:** `Imposer` computes each `Placement`; the
exporter consumes it verbatim. No modification, no recomputation, no clamping. That is what
guarantees preview, PDF, and paper agree.

## Provides

| Symbol | Consumed by |
|---|---|
| `export(plan, out_path, sheets=None)` | SS-07, SS-10, SS-14 |

## Requires

- `SheetPlan`, `OutputPage`, `Placement` from SS-01.
- A `SheetPlan` produced by SS-03.

## Implementation Steps

### Step 1. Write the failing pure-translation test

`tests/test_export.py`: build a `fixed_gutter` plan whose source already fits, export it,
and assert the generated content stream segment contains a pure translation of the form
`1 0 0 1 <x> <y> cm` — **no scale factor**. Parse the bytes to check.

This test is the reason pikepdf was chosen over pypdf. Write it first.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_export.py -q
```

### Step 3. Implement the Method B composition loop

Create `deckle/core/export.py`. Per sheet side:

1. `sheet = out.add_blank_page(page_size=settings.paper)`
2. `formx = out.copy_foreign(Page(srcpage).as_form_xobject())` — `copy_foreign` is required
   because the source lives in a different `Pdf`.
3. `name = sheet.add_resource(formx, Name.XObject, prefix="Fx")` — `Name.random(prefix="Fx")`
   semantics, collision-free.
4. `rect` from the `Placement`.
5. `sheet.contents_add(sheet.calc_form_xobject_placement(formx, name, rect, invert_transformations=True, allow_shrink=..., allow_expand=...))`

`calc_form_xobject_placement` returns **bytes**, not an operation — you append them yourself.

`allow_shrink` / `allow_expand` are **False** for `fixed_gutter` (exact 1:1) and **True**
for `fit_height` (rect sized to the scaled target).

### Step 4. Green on pure translation

### Step 5. Handle fillers and blank sides

A filler `OutputPage` exports as a genuinely blank page with no source content. A `None`
side is likewise blank. Test both.

### Step 6. Implement subset export

`sheets=None` means all; `sheets=[6]` produces a 1-sheet (2-page) PDF matching sheet 6 of
the full export. **Same code path** — filter the input, do not branch.

### Step 7. Fail fast on unwritable output

Check writability before composing. A partial file must never appear on disk.

### Step 8. Batch for large documents

Source PDFs stay open once content is copied out (`[[pikepdf - Performance and Memory]]`).
For large documents, save and reopen between chunks rather than holding one handle across
500 pages. Add a test asserting a 500-page export completes without unbounded memory growth.

### Step 9. Clean up and add the invariant test

Call `out.remove_unreferenced_resources()` before save. Add a test asserting the `Placement`
objects reaching `export` are equal by dataclass equality to those `Imposer` produced.

### Step 10. Commit

```bash
python -m pytest tests/test_export.py -q
git add -A && git commit -m "feat(SS-04): Exporter via pikepdf Form XObject placement"
```

## Interface Contracts

### export
- Direction: SS-04 → SS-07, SS-10, SS-14
- Owner: SS-04
- Shape: `export(plan: SheetPlan, out_path: str, sheets: Sequence[int] | None = None) -> None`.
  `sheets=None` exports everything. Consumes `Placement` verbatim.

## Verification Commands

```bash
python -m pytest tests/test_export.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| export exposed with subset support | [STRUCTURAL] | `grep -q "def export" deckle/core/export.py \|\| (echo "FAIL: export missing" && exit 1)` |
| Form XObject placement used | [MECHANICAL] | `grep -q "calc_form_xobject_placement" deckle/core/export.py \|\| (echo "FAIL: not using calc_form_xobject_placement" && exit 1)` |
| as_form_xobject used | [MECHANICAL] | `grep -q "as_form_xobject" deckle/core/export.py \|\| (echo "FAIL: as_form_xobject not used" && exit 1)` |
| add_overlay and pypdf absent | [MECHANICAL] | `! grep -n "add_overlay\|merge_transformed_page" deckle/core/export.py \|\| (echo "FAIL: forbidden composition API used" && exit 1)` |
| copy_foreign used for cross-Pdf copy | [MECHANICAL] | `grep -q "copy_foreign" deckle/core/export.py \|\| (echo "FAIL: copy_foreign missing" && exit 1)` |
| Orphan resources cleaned | [MECHANICAL] | `grep -q "remove_unreferenced_resources" deckle/core/export.py \|\| (echo "FAIL: remove_unreferenced_resources missing" && exit 1)` |
| Export tests pass | [MECHANICAL] | `python -m pytest tests/test_export.py -q \|\| (echo "FAIL: export tests failed" && exit 1)` |
