---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
contracts: "./contracts.yaml"
sub_spec_id: SS-02
sub_spec_number: 2
title: "The Side refactor across deckle.core — zero behaviour change"
depends_on: ["SS-01"]
date: 2026-08-04
---

# SS-02 — The `Side` refactor across `deckle.core`: zero behaviour change

## Context

**What this does:** makes `deckle.core` compile and behave **identically** against SS-01's new
`Sheet` shape. `GutterShiftStrategy` wraps each placed page in `Side(pages=(page,))`;
`export._sides` returns `list[Side]` and `_export_batched` iterates `side.pages`; `render.py`'s
front-then-back page-index mapping is preserved unchanged. **No signature logic, no marks, no
cell geometry.** Those are SS-05 through SS-09.

**Why it is a separate sub-spec.** The design's Risks table is explicit: *"Do the refactor as a
standalone commit with zero behaviour change, verified by the Pinebox golden, before any
signature code exists. If Pinebox drifts, stop."* The refactor and the feature must never be
entangled in one diff, because the only way to know the refactor was neutral is to have
nothing else in the change.

**Why this sub-spec deliberately exceeds the usual 1–3 file budget (seven files).**
`Sheet.front`'s type change is **atomic** across `layout.py`, `export.py` and `render.py`.
`layout.py` produces `Sheet`s, `export.py` consumes them, and `render.py` reasons about how
many PDF pages a `Sheet` yields. Splitting the change across waves would leave the tree in a
state where the imposer emits `Side` objects and the exporter still expects `OutputPage` — the
tree would import fine (annotations are lazy) and then fail at runtime on every export, with
`tests/test_export.py`, `tests/test_render.py`, `tests/test_integration.py` and the whole
preview path red for the duration. There is no smaller unit that leaves the tree working. The
budget is exceeded on purpose and the justification travels with the sub-spec so a reviewer
does not have to reconstruct it.

**Contracts this sub-spec consumes.** `Side` and `Sheet` (contracts 1 and 2, owner SS-01) and
`export_content_stream` (`contracts.yaml`, owner SS-09) — SS-02 lands only the `_sides(sheet)
-> list[Side]` half of that last contract; the marks-drawing half is SS-09's.

**Contracts this sub-spec must not touch.** `LayoutStrategy.impose` is marked `frozen: true`
in `contracts.yaml`. Its signature does not move here or anywhere.
`SheetPlan.signatures` stays at its default `()` for `GutterShiftStrategy` — this is a
committed conflict resolution (the design's prose said "a single `Signature` spanning every
sheet"; the committed contract says empty). Do not populate it.

**Two decision-log entries bind this sub-spec directly.** *Rebuilt the placement math on one
rule for both axes* — assert **measured margins** via `actual_margins_pt`, never raw `tx`/`ty`;
a coordinate assertion on one edge of one page is precisely how a verso-only bug survived the
original suite. *`fixed_gutter` put the reserved gutter on the wrong side of the verso* — the
recorded instance of a shared geometry rule being quietly relied on by a second code path;
generalise, never duplicate. Both mean the same thing here: the test edits in this sub-spec are
**wrapper edits only**. `sheet.front` becomes `sheet.front.pages[0]`. **Do not change any
assertion's expected value.** If an expected value appears to need changing, the refactor was
not neutral — stop and escalate rather than adjust the test.

**Verified facts to build on, read from the tree rather than inferred:**

- `deckle/core/export.py:198-204` — `_sides(sheet)` returns `list[OutputPage]` by appending
  `sheet.front` and `sheet.back` when non-`None`. Only the annotation and the caller change;
  the body is already correct for `Side`.
- `deckle/core/export.py:257-260` — `_export_batched` does
  `for output_page in _sides(sheet): dest_page = out.add_blank_page(...)`, i.e. one
  `add_blank_page` per side already. The change is to place *N* pages onto that one blank page
  instead of one. This is exactly the vault recipe's structure, where two `place_exact` calls
  land on one `add_blank_page`.
- `deckle/core/export.py:134-195` — `_place_output_page` **needs no change**. It already takes
  an arbitrary `Placement` in sheet coordinates and adds its own balanced `q…Q` block via
  `dest_page.contents_add`. Calling it twice against one `dest_page` produces two balanced
  blocks, which is the property SS-09 asserts.
- `deckle/core/export.py:50-67` — `_plan_hash` calls `_output_page_key(side)` per side. It must
  iterate `side.pages` so the export cache stays content-aware; a 2-up sheet whose two pages
  swap must invalidate the cache.
- `deckle/core/render.py:82-83` — the only two reads of a side, and both are **presence**
  checks (`sheet.front is not None`). They stay correct verbatim.
- `deckle/core/render.py:93-102` — the front-then-back page-index mapping. It stays correct
  because one `Side` still yields exactly **one** PDF page. `render.py` therefore needs no
  logic change at all; its edit is the comment at lines 93-94, updated to say "one `Side`
  yields one PDF page" so a later reader does not have to re-derive why 2-up did not break it.
- `deckle/core/layout.py:339-353` — the sheet-construction loop, the only place
  `GutterShiftStrategy` builds a `Sheet`.
- `deckle/core/printing.py:121` — `indices = [s.index for s in plan.sheets] if sheets is None
  else list(sheets)`, and nothing else in `plan_passes` touches a `Sheet`. This is why
  `printing.py` needs no edit here and stays zero-diff (SS-04 proves it).

**Two extra test files, justified.** The master spec lists four test files. Two more construct
`Sheet(index=…, front=_blank_output_page(), back=_blank_output_page())` and must be wrapped in
the same mechanical pass:

- `tests/test_printing.py:14-23` — fixture only; `plan_passes` reads `s.index`, so this is a
  type-consistency edit, not a behaviour one.
- `tests/test_print_dialog.py:41-50` — **not optional**. It builds a plan and hands it to
  `PrintSession`, whose `__init__` calls `_hash_plan`. SS-04 changes `_hash_plan` to read
  `side.pages`, and `tests/test_print_dialog.py` appears in **no** sub-spec's Files list. Left
  unwrapped it goes red in SS-04 with a failure that looks like an SS-04 defect and is not one.
  Wrapping it here, in the mechanical pass that owns exactly this kind of edit, is the correct
  place.

`tests/test_preview_fidelity.py` also constructs `Sheet`s (lines 80, 81, 107, 120, 131, 132,
145, 153, 165, 166) but belongs to **SS-03**; leave it alone here. `tests/test_print_session.py`
belongs to **SS-04**; leave it alone here. Both sub-specs depend on SS-02 and run after it.

**Files (modify):**
- `deckle/core/layout.py`
- `deckle/core/export.py`
- `deckle/core/render.py`
- `tests/test_layout.py`
- `tests/test_export.py`
- `tests/test_render.py`
- `tests/test_golden_pinebox.py`
- `tests/test_printing.py` — fixture wrapper only, justified above
- `tests/test_print_dialog.py` — fixture wrapper only, justified above

## Provides / Requires

**Provides:**

| Symbol | Module | Consumed by |
|---|---|---|
| `export._sides(sheet: Sheet) -> list[Side]` | `deckle/core/export.py` | SS-09 (adds mark drawing after the placements) |
| `export._export_batched` (one `add_blank_page` per `Side`, one `_place_output_page` per page) | `deckle/core/export.py` | SS-09 |
| `export._plan_hash` (iterates `side.pages`) | `deckle/core/export.py` | SS-09 (extends with `side.marks`) |
| `GutterShiftStrategy.impose` emitting `Side`-shaped sheets | `deckle/core/layout.py` | SS-07, SS-08 |
| `render.render_sheet` front-then-back mapping, unchanged | `deckle/core/render.py` | SS-03 (`backend._render_sheet_side` carries a second copy) |
| Test-suite fixture convention `Side(pages=(output_page,))` | `tests/` | SS-03, SS-04, SS-08 |

**Requires:**

| Symbol | Owner | Why |
|---|---|---|
| `Side` | SS-01 | `GutterShiftStrategy` constructs it; `export`/`render` consume it |
| `Sheet` with `front`/`back: Side \| None` | SS-01 | the type this refactor conforms to |
| `SheetPlan.signatures` default `()` | SS-01 | `GutterShiftStrategy` leaves it untouched |
| `LayoutStrategy` Protocol | MVP SS-03 (`deckle/core/layout.py:26-38`) | unchanged; must not move |
| `_place_output_page` | MVP SS-04 (`deckle/core/export.py:134-195`) | reused verbatim, once per page |

## Implementation Steps

### Step 1. Write the placement-identity regression test first

This is the test that proves the refactor was neutral, so it is written **before** any
production edit. Add to `tests/test_layout.py`, reusing the module's existing helpers —
`make_pages` (line 50), `settings` (line 54), `impose` (line 76), `actual_margins_pt` (imported
at line 28), and the real page boxes `TRAVELLER`/`DIGEST`/`SQUARE`/`WIDE`/`TALL` (lines 34-38):

`test_gutter_shift_placements_are_identical_across_aspect_ratios_and_binding_edges` —
parametrised over 3 aspect ratios × 2 binding edges (following the file's existing
parametrisation style), it imposes a 7-page document, walks **every sheet and both sides**,
and asserts each `OutputPage`'s `Placement` compares equal by dataclass equality to the
placement produced by a second, independent `impose()` call. Then, for every page, it asserts
the four **measured** margins from `actual_margins_pt` — not `tx`/`ty` — round-trip to the same
values on both sides. This is the *Rebuilt the placement math on one rule for both axes*
lesson applied: measure all four edges of both sides, for both binding edges.

Write it against the **current** (`OutputPage`-valued) `sheet.front`, so it is green now.

### Step 2. Run it and confirm it is green on the pre-refactor tree

```bash
python -m pytest tests/test_side_reporting.py -q -k placements_are_identical
```

Green here is the point: this is a *characterisation* test. It captures behaviour before the
refactor so any drift after it is unambiguous.

### Step 3. Wrap the imposer's sheet construction

`deckle/core/layout.py`:

- Add `Side` to the `deckle.core.models` import block (lines 15-23).
- At lines 343-353, wrap both sides:

```python
front_page = _place_page(front_slot, sheet_index, settings, sheet_index // 2, warnings, scale)
back_page = (
    _place_page(back_slot, sheet_index + 1, settings, sheet_index // 2, warnings, scale)
    if sheet_index + 1 < len(slots)
    else None
)
sheets.append(
    Sheet(
        index=sheet_index // 2,
        front=Side(pages=(front_page,)),
        back=Side(pages=(back_page,)) if back_page is not None else None,
    )
)
```

  `_place_page` always returns an `OutputPage` (a filler when `slot is None`), so `front` is
  never `None` and `Side(pages=())` can never arise. An absent back stays `None` — never an
  empty `Side`. Leave the `return SheetPlan(...)` at line 355 exactly as it is:
  `signatures` defaults to `()`, which is the committed behaviour for `GutterShiftStrategy`.

### Step 4. Run the layout suite and watch it fail in the tests, not the code

```bash
python -m pytest tests/test_layout.py -q
```

Expect `AttributeError: 'Side' object has no attribute 'placement'` from
`flat_output_pages`. That is the correct red: the production change landed, the test helpers
have not caught up.

### Step 5. Wrap the layout test reads

`tests/test_layout.py`:

- `flat_output_pages` (lines 60-67) — replace the two `flat.append(sheet.front)` /
  `flat.append(sheet.back)` calls with `flat.extend(sheet.front.pages)` /
  `flat.extend(sheet.back.pages)`. Everything downstream (`margins`, line 70-73) is unchanged,
  because `flat_output_pages` still yields `OutputPage`s in the same order.
- Line 464 — `p = plan.sheets[0].front.placement` becomes
  `p = plan.sheets[0].front.pages[0].placement`.
- Update the Step 1 characterisation test's side reads the same way.

Change nothing else in this file. In particular, do not touch any expected value.

### Step 6. Run to green

```bash
python -m pytest tests/test_layout.py -q
```

Must report **no fewer than 46 tests** (the tree currently collects 81) and zero failures. If
any assertion's expected value would have to change to reach green, **stop and escalate** —
that is the "Pinebox drifted" condition arriving early.

### Step 7. Wrap the exporter

`deckle/core/export.py`:

- Import `Side` from `deckle.core.models` (line 37).
- `_sides` (lines 198-204) — annotate `-> list[Side]`; the body is already correct. Rename the
  local `sides` list's element sense in the docstring, not the code.
- `_export_batched` (lines 257-260) — one `add_blank_page` per `Side`, then one
  `_place_output_page` per page in that side:

```python
for side in _sides(sheet):
    dest_page = out.add_blank_page(page_size=plan.paper_pt)
    for output_page in side.pages:
        _place_output_page(out, dest_page, output_page, source_cache)
```

- `_plan_hash` (lines 59-67) — the per-side branch currently ends
  `digest.update(_output_page_key(side).encode("utf-8"))`. Replace with a loop over
  `side.pages`, so a 2-up sheet whose two pages swap produces a different cache key:

```python
for output_page in side.pages:
    digest.update(_output_page_key(output_page).encode("utf-8"))
```

  Leave `_output_page_key` (lines 70-82) untouched — it is already per-page and correct.
  SS-09 extends `_plan_hash` again with `side.marks`; do not anticipate it here.

Nothing else in `export.py` changes. `_place_output_page`, `_rect_for_placement`,
`_scale_flags_for` and `_rotation_matrix` are all per-`Placement` and already correct.

### Step 8. Wrap the export tests, run to green

`tests/test_export.py`:

- Line 251 — `Sheet(index=0, front=filler, back=None)` becomes
  `Sheet(index=0, front=Side(pages=(filler,)), back=None)`; add `Side` to the import block at
  lines 13-21.
- Lines 156-159 — `sheet_a.front.placement` becomes `sheet_a.front.pages[0].placement`, and
  the same for `.back`.

```bash
python -m pytest tests/test_export.py -q
```

### Step 9. Update `render.py`'s comment and run its tests

`deckle/core/render.py` needs **no logic change**: lines 82-83 are presence checks and lines
95-102 map front-then-back onto PDF page indices, both still correct because one `Side` still
yields exactly one PDF page. Update the comment at lines 93-94 to say so explicitly:

```python
# The single-sheet export contains one PDF page per Side that exists, in
# front-then-back order -- see export._export_batched/_sides. A Side may
# hold several pages (2-up under folio) but is still ONE PDF page, which
# is why this index mapping is unchanged from the 1-up MVP.
```

`tests/test_render.py` — wrap `_one_sheet_plan` (lines 53-59):
`front=Side(pages=(_output_page(front_ref),)) if front_ref is not None else None`, and the same
for `back`; add `Side` to the import block.

```bash
python -m pytest tests/test_render.py -q
```

### Step 10. Wrap the golden fixture's reads

`tests/test_golden_pinebox.py`:

- Line 53 — `for side in (sheet.front, sheet.back)` with the filler test at line 54
  (`side is not None and side.is_filler`) becomes an iteration over each side's pages:

```python
filler_count = sum(
    1
    for sheet in plan.sheets
    for side in (sheet.front, sheet.back)
    if side is not None
    for output_page in side.pages
    if output_page.is_filler
)
```

- Line 75 — `for output_page in (sheet.front, sheet.back)` becomes a nested walk over
  `side.pages`, keeping the `output_page is None or output_page.is_filler or
  output_page.source_ref is None` guard body verbatim.

The two assertions' expected values (`expected_fillers`, `total_slots - filler_count ==
len(pages)`, the `scaled_w <= LETTER_PT[0] + 1e-6` bounds, `expected_sheets`) do **not**
change. `total_slots = len(plan.sheets) * 2` at line 49 stays correct because
`GutterShiftStrategy` still puts exactly one page on each side.

```bash
python -m pytest tests/test_golden_pinebox.py -q
```

This skips cleanly when the fixture is absent (`tests/test_golden_pinebox.py:30-38`) and exits
0 either way. If the fixture **is** present and this drifts, **stop** — that is the design's
named halt condition.

### Step 11. Wrap the two remaining fixture files

- `tests/test_printing.py:23` — `Sheet(index=i, front=_blank_output_page(), back=_blank_output_page())`
  becomes `Sheet(index=i, front=Side(pages=(_blank_output_page(),)), back=Side(pages=(_blank_output_page(),)))`;
  add `Side` to the import block.
- `tests/test_print_dialog.py:50` — the identical edit.

```bash
python -m pytest tests/test_printing.py tests/test_print_dialog.py -q
```

### Step 12. Full suite, lint, commit

```bash
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "refactor(SS-02): Sheet sides become Side, zero behaviour change"
```

`python -m pytest -q` must report zero failures with no fewer than 237 collected. Any change
in the *number* of passing assertions, or any expected value that had to move, means this was
not a neutral refactor — surface it.

## Interface Contracts

### export._sides
- Direction: SS-02 → SS-09
- Owner: SS-02
- Shape: `def _sides(sheet: Sheet) -> list[Side]` — returns `[sheet.front]`, `[sheet.back]` or
  both, in front-then-back order, omitting `None`s. Never returns an empty `Side`.

### export._export_batched composition rule
- Direction: SS-02 → SS-09
- Owner: SS-02
- Shape: exactly one `out.add_blank_page(page_size=plan.paper_pt)` per `Side`, then one
  `_place_output_page(out, dest_page, output_page, source_cache)` per page in `side.pages`.
- Invariant: each `_place_output_page` call adds its own balanced `q…Q` block via
  `dest_page.contents_add`, so an N-page side yields N balanced blocks on one PDF page. SS-09
  asserts exactly two per folio PDF page and draws `side.marks` in a further, separate block
  **after** the placements, leaving the two-XObject assertion unaffected.

### export._plan_hash
- Direction: SS-02 → SS-09
- Owner: SS-02
- Shape: iterates `side.pages` and folds `_output_page_key(output_page)` into the digest, so
  two orderings of the same pages over the same sheet count produce different cache keys.
- Note: this mirrors, but is separate from, `print_session._hash_plan` (SS-04). They serve
  different caches and must not be merged.

### render.render_sheet page-index mapping
- Direction: SS-02 → SS-03
- Owner: MVP SS-05, unchanged by SS-02
- Shape: `page_index = 0` for a present front; for a back, `1 if has_front else 0`.
- Invariant: one `Side` yields exactly one PDF page, at every page count per side.
  `deckle/app/backend.py:168` carries a second copy of this rule; SS-03 preserves it.

### GutterShiftStrategy side construction
- Direction: SS-02 → SS-07, SS-08
- Owner: SS-02
- Shape: `front=Side(pages=(front_page,))`, `back=Side(pages=(back_page,))` or `None`.
  `SheetPlan.signatures` is left at its default `()`.

## Verification Commands

Build check:

```bash
python -c "import deckle.core.layout, deckle.core.export, deckle.core.render" || (echo "FAIL: core tree not importable" && exit 1)
python -m ruff check deckle tests
```

Test check:

```bash
python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q
python -m pytest tests/test_export.py tests/test_render.py -q
python -m pytest tests/test_printing.py tests/test_print_dialog.py -q
python -m pytest tests/test_core_purity.py -q
python -m pytest -q
```

Per-criterion acceptance check:

```bash
# REQ-010 -- _sides returns Sides; one add_blank_page per Side
grep -q "def _sides(sheet: Sheet) -> list\[Side\]" deckle/core/export.py || (echo "FAIL: _sides not retyped" && exit 1)
test $(grep -c "out.add_blank_page(" deckle/core/export.py) -eq 1 || (echo "FAIL: add_blank_page is no longer a single call site" && exit 1)

# REQ-009 -- GutterShiftStrategy still produces identical placements
python -m pytest tests/test_layout.py -q
python -m pytest tests/test_golden_pinebox.py -q

# REQ-011 -- the renderer reads the exported artifact, never Placement
! grep -n "Placement" deckle/core/render.py || (echo "FAIL: renderer reads Placement instead of the exported artifact" && exit 1)

# REQ-040 -- no I/O in layout.py, no Qt in core
! grep -nE "^import (os|io)$|open\(|requests|urllib|socket" deckle/core/layout.py || (echo "FAIL: I/O in layout.py" && exit 1)
python -m pytest tests/test_core_purity.py -q

# The seam did not move
git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py || (echo "FAIL: manual-duplex seam changed in SS-02" && exit 1)
```

## Checks

Every negative check below was executed against the working tree at commit `b54194c` and
confirmed to exit 0.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | `_sides` annotated `-> list[Side]` (REQ-010) | [STRUCTURAL] | `grep -q "def _sides(sheet: Sheet) -> list\[Side\]" deckle/core/export.py \|\| (echo "FAIL: _sides not annotated -> list[Side]" && exit 1)` |
| 2 | Exactly one `out.add_blank_page(` call site, i.e. one per `Side` (REQ-010) | [STRUCTURAL] | `test $(grep -c "out.add_blank_page(" deckle/core/export.py) -eq 1 \|\| (echo "FAIL: add_blank_page is not a single per-Side call site" && exit 1)` |
| 3 | `_export_batched` iterates a side's pages (REQ-010) | [STRUCTURAL] | `grep -q "for output_page in side.pages" deckle/core/export.py \|\| (echo "FAIL: exporter does not place every page of a side" && exit 1)` |
| 4 | `_plan_hash` is content-aware over a side's pages | [STRUCTURAL] | `grep -q "side.pages" deckle/core/export.py \|\| (echo "FAIL: export cache key ignores side content" && exit 1)` |
| 5 | `GutterShiftStrategy` wraps each placed page in a `Side` | [STRUCTURAL] | `grep -q "Side(pages=(" deckle/core/layout.py \|\| (echo "FAIL: imposer does not construct Side" && exit 1)` |
| 6 | No empty `Side` is ever constructed (SS-01 invariant) | [MECHANICAL] | `! grep -rnE "Side\(\s*(pages\s*=\s*)?\(\s*\)\s*\)" deckle/ tests/ \|\| (echo "FAIL: Side(pages=()) constructed -- an absent side is None" && exit 1)` |
| 7 | `GutterShiftStrategy` leaves `signatures` at its empty default | [MECHANICAL] | `python -c "import ast,pathlib,sys; t=ast.parse(pathlib.Path('deckle/core/layout.py').read_text(encoding='utf-8')); c=[n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='GutterShiftStrategy']; sys.exit(1 if not c or any(isinstance(n,ast.keyword) and n.arg=='signatures' for n in ast.walk(c[0])) else 0)" \|\| (echo "FAIL: GutterShiftStrategy populated SheetPlan.signatures" && exit 1)` |
| 8 | `tests/test_layout.py` green, no coverage lost (REQ-009) | [MECHANICAL] | `python -m pytest tests/test_layout.py -q \|\| (echo "FAIL: layout suite red after refactor" && exit 1)` |
| 9 | `tests/test_layout.py` still collects at least 46 tests (REQ-009) | [MECHANICAL] | `test $(python -m pytest tests/test_layout.py -q --collect-only 2>/dev/null \| grep -c "::") -ge 46 \|\| (echo "FAIL: layout coverage shrank below 46" && exit 1)` |
| 10 | Pinebox golden green or cleanly skipped (REQ-009) | [MECHANICAL] | `python -m pytest tests/test_golden_pinebox.py -q \|\| (echo "FAIL: Pinebox golden drifted -- STOP, do not adjust the test" && exit 1)` |
| 11 | Export and render suites green | [MECHANICAL] | `python -m pytest tests/test_export.py tests/test_render.py -q \|\| (echo "FAIL: export/render red after refactor" && exit 1)` |
| 12 | Wrapped fixture files green | [MECHANICAL] | `python -m pytest tests/test_printing.py tests/test_print_dialog.py -q \|\| (echo "FAIL: fixture wrapper edits red" && exit 1)` |
| 13 | Renderer never reads `Placement` (REQ-011) | [MECHANICAL] | `! grep -n "Placement" deckle/core/render.py \|\| (echo "FAIL: renderer reads Placement instead of the exported artifact" && exit 1)` |
| 14 | No forbidden composition API in the exporter | [MECHANICAL] | `! grep -n "add_overlay\|merge_transformed_page\|draw_image" deckle/core/export.py \|\| (echo "FAIL: forbidden composition API present" && exit 1)` |
| 15 | No I/O in `layout.py` (REQ-040) | [MECHANICAL] | `! grep -nE "^import (os\|io)$\|open\(\|requests\|urllib\|socket" deckle/core/layout.py \|\| (echo "FAIL: I/O in layout.py" && exit 1)` |
| 16 | No Qt import in `deckle.core` (REQ-040) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 17 | Core purity test green (REQ-040) | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity violated" && exit 1)` |
| 18 | `LayoutStrategy.impose` signature unmoved (frozen contract) | [MECHANICAL] | `grep -q "def impose(" deckle/core/layout.py && grep -q "self, pages: Sequence\[SourcePage\], settings: LayoutSettings" deckle/core/layout.py \|\| (echo "FAIL: impose signature moved" && exit 1)` |
| 19 | The manual-duplex seam is untouched (REQ-015) | [MECHANICAL] | `git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py \|\| (echo "FAIL: printing.py or profiles.py changed in SS-02" && exit 1)` |
| 20 | Core tree imports cleanly | [MECHANICAL] | `python -c "import deckle.core.layout, deckle.core.export, deckle.core.render" \|\| (echo "FAIL: core tree not importable" && exit 1)` |
| 21 | Suite has not shrunk (REQ-039) | [MECHANICAL] | `test $(python -m pytest -q --collect-only 2>/dev/null \| grep -c "::") -ge 237 \|\| (echo "FAIL: suite collected fewer than 237 tests" && exit 1)` |
| 22 | Full suite green (REQ-039) | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: suite red after the Side refactor" && exit 1)` |
| 23 | Lint clean (REQ-039) | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff violations" && exit 1)` |

**Note on check 2:** the token `add_blank_page` appears three times in
`deckle/core/export.py` — twice in prose (lines 4 and 142) and once as a call (line 259).
The check therefore counts `out.add_blank_page(`, which is exactly one today and must stay
exactly one, since that single call site is what makes "one PDF page per `Side`" true. If
SS-09 ever needs a second call site, revisit this criterion deliberately rather than relaxing
it silently.

**Note on check 16 and the anchored grep:** the unanchored `! grep -rn "PySide6" deckle/core/`
fails on a clean tree — `deckle/core/models.py:5` and `deckle/core/__init__.py:3` state the
rule in prose. The anchored form matches import statements only and was verified to exit 0.

**Note on `ruff`:** `ruff` 0.15.13 is installed but is not on `PATH` under Git Bash here;
`python -m ruff check deckle tests` is the verified invocation.
