---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-08
sub_spec_number: 8
title: "SaddleStitchStrategy — folio 2-up imposition"
depends_on: ['SS-05', 'SS-06', 'SS-07']
date: 2026-08-04
---

# SS-08 — `SaddleStitchStrategy`: folio 2-up imposition

## Context

This is the sub-spec the whole feature exists for. Everything before it built vocabulary
(`Side`, `Mark`, `Signature`), arithmetic (`split_signatures`, `saddle_order`,
`fold_reading_order`), geometry (`marks.py`) and a cell-aware placement rule (SS-07). This
sub-spec assembles them into the second `LayoutStrategy` implementation.

**Read before writing code:**

1. `docs/specs/2026-08-04-deckle-signatures-v2.md` — Meta, Context, the *Contracts* table
   (all ten), Requirements REQ-016 … REQ-030 and REQ-042, and *Edge Cases*.
2. `docs/specs/deckle-signatures-v2/contracts.yaml` — the same ten contracts in machine form.
   Where the design's narrative prose disagrees with the table, **the table wins**; the seven
   known conflicts are already enumerated, do not re-derive them.
3. `docs/decisions.md`, entries *Uniform document-wide scale*, *Rebuilt the placement math on
   one rule for both axes*, *`fixed_gutter` put the reserved gutter on the wrong side of the
   verso*, *`slack_to` replaces the `maximize_gutter` boolean*, and *Path-traversal validation
   must advise, not refuse*.
4. **`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition
   and Signature Recipe.md`** — a *working, executed* saddle-stitch imposition, run end to end
   against pikepdf 10.11.0 / libqpdf 12.3.2 on this machine on 2026-08-04. It produces
   precisely this sub-spec's target: 8 half-letter pages onto 4 landscape letter sheets, 2-up,
   in fold order. Two things in it are load-bearing here:
   - Its `saddle_order` (note lines 35–44) is the function SS-05 already shipped, and its
     recorded executed output at line 63 — `order: [7, 0, 1, 6, 5, 2, 3, 4]` — is what pins
     the pairing this strategy consumes: sheet 1 front `(p8, p1)`, sheet 1 back `(p2, p7)`,
     sheet 2 front `(p6, p3)`, sheet 2 back `(p4, p5)`. **The outermost sheet carries the
     first and last pages** — the defining property of a *nested* gathering.
   - Its placement loop (note lines 51–54) places the two leaves at `x = 0` and
     `x = PAGE_W`, on a sheet of `(2 * PAGE_W, PAGE_H)`, **with no rotation of either leaf**
     and with the identical rect for both. Its recorded coalesced content stream,
     `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm`, is the proof that folio 2-up on landscape letter
     is pure translation. That is *why* folio was chosen over quarto: **`rotate_deg` stays
     `0` everywhere in this code path**, and `export._rotation_matrix` never enters it.
   - That note also states, in as many words: *"`saddle_order` is my own function, not
     pikepdf's… Validate the fold order against a physical folded dummy before trusting it
     for a real print run."* The pikepdf half is confirmed; the bindery half is not. That is
     why SS-13 gates and why `fold_reading_order` must never reuse `saddle_order`.

**Six things this sub-spec must get right, each with its own failure mode:**

| # | Rule | The failure it prevents |
|---|---|---|
| 1 | `impose`'s signature is **unmodified** — character-identical to `GutterShiftStrategy.impose` | The `LayoutStrategy` seam is the feature's entry point (contracts.yaml, `frozen: true`). Widening it to take signature options breaks every caller and the design's central claim. |
| 2 | `Placement.rotate_deg == 0` for every placed leaf under folio | Folio was chosen *because* it needs no rotation. A non-zero `rotate_deg` means the quarto machinery leaked in and `export._rotation_matrix` is now in the signature path. |
| 3 | **One** document-wide scale, computed once against a **cell** | `docs/decisions.md`, *Uniform document-wide scale* — per-page scaling made the Traveller cover's text 2.5% larger than the body's. |
| 4 | Padding runs **exactly one pass** | The predecessor script's defect 2 was silent double-padding. |
| 5 | Signatures occupy **contiguous** runs of sheet indices, in binding order | `plan_passes` reverses the whole sheet list when `profile.reverse_stack` is true. Contiguity is what lets `plan_passes(plan, profile, sheets=sig.sheet_indices)` reverse correctly *in isolation* as well as in aggregate — this is what makes per-signature printing work with **no new branch in `printing.py`** (REQ-035). |
| 6 | The spine side comes from **cell position**, never from output-page parity | `_gutter_side_is_left` is the MVP rule and it is **wrong under folio**. `docs/decisions.md` records the verso-only bug that survived its own tests because one geometry rule was quietly relied on by a second code path. |

**The spine side inverts, and this is the trap.**

```
left cell   -> spine on its RIGHT edge   (the fold)
right cell  -> spine on its LEFT edge    (the fold)
```

This holds on **both** the front and the back of the sheet. Consequently
`settings.binding_edge` **changes meaning** under `fold_scheme="folio"`: it no longer selects
which side of a page carries the gutter, it selects **reading direction** — `"left"` =
left-bound / LTR, `"right"` = right-bound / RTL — which mirrors which source slot goes into
which cell. Document this at the field (SS-01 already reserves the docstring slot) and assert
it in tests. Splitting it into a separate `reading_direction` field is explicitly *Out of
Scope*: it would need an uncommitted `LayoutSettings` field and the project's first `.deckle`
migration.

**Cell geometry.** The paper is the *sheet*; the fold is its vertical centreline. For letter
landscape (792 × 612) the cells are `(0, 0, 396, 612)` and `(396, 0, 792, 612)` — matching
the vault recipe's recorded `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm` exactly.

**Two constraints on this file that are grep-enforced and must survive every edit:**

- `deckle/core/layout.py` stays **I/O-free** — no file, network or print I/O, no Qt, no
  pikepdf import. `tests/test_core_purity.py` and a `[MECHANICAL]` check both hold the line.
- **`paper_thickness_pt` must never reach placement geometry.** Every reference to it in
  `layout.py` lives inside one function named `_creep_advisory`, enforced by an AST test.
  Creep is *measured and reported, never compensated* (Intent 2). pdfimpose's own help text
  for its creep option reads "⚠ Warning ⚠ This option is broken".

**When writing code, do not name a forbidden token in a comment or docstring** in a file a
`[MECHANICAL]` criterion greps. Say "the whole-page overlay/watermark helper" rather than the
API name, exactly as `deckle/core/export.py`'s module docstring already does. A criterion that
fails on correct code is indistinguishable from one that caught a real defect — factory run
`c46e15e3` shows what that costs.

## Provides

| Symbol | Shape | Consumed by |
|---|---|---|
| `SaddleStitchStrategy` | `class` implementing `LayoutStrategy` | SS-11 (`recompute_plan` dispatch), SS-12 (integration, CLI) |
| `SaddleStitchStrategy.impose` | `impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan` | SS-09 (consumes `Side.marks`), SS-11, SS-12 |
| `SheetPlan.signatures` populated | `tuple[Signature, ...]`, non-empty under folio | SS-11 (readout, print selector), SS-12 (`plan_passes(sheets=...)`) |
| `Side.marks` populated | `tuple[Mark, ...]` per side | SS-09 (mark rendering in `export.py`) |
| `_creep_advisory` | private; the sole `paper_thickness_pt` call site | nothing — enforced to stay private |

## Requires

| From | Symbol | Why |
|---|---|---|
| SS-01 | `Side`, `Mark`, `Signature`, `SheetPlan.signatures`, the five `LayoutSettings` fields | The vocabulary this strategy emits. |
| SS-05 | `split_signatures(sheet_count, sheets_per_signature)` | Contiguous sheet-index groups, remainder in the final group. |
| SS-05 | `saddle_order(n)` | Fold order within one signature; `n` a positive multiple of 4. |
| SS-05 | `fold_reading_order(plan)` | The round-trip oracle. **Used by tests only** — `impose` must never call it. |
| SS-06 | `sewing_stations`, `signature_order_mark`, `fold_line` | Pure `Mark` geometry in sheet points. |
| SS-07 | `Cell`, cell-aware `document_scale`, `content_box_size`, `content_box_rect_pt`, `actual_margins_pt`, and `_place_page`'s explicit `spine_side` parameter | The one placement rule, applied per leaf. |
| MVP | `GutterShiftStrategy`, `LayoutStrategy` | Unmoved; the Pinebox golden proves it. |

## Implementation Steps

Each step is 2–10 minutes. Run the stated command, see it fail, implement the minimum, run it
green, commit. Do not skip the red run — an assertion that has never failed has never been
tested.

### Step 1. Failing test: the protocol signature did not move

Create `tests/test_layout_saddle.py`. First test:

```python
def test_protocol_signature_unchanged():
    import inspect
    from deckle.core.layout import GutterShiftStrategy, LayoutStrategy, SaddleStitchStrategy
    assert isinstance(SaddleStitchStrategy(), LayoutStrategy)
    assert inspect.signature(SaddleStitchStrategy.impose) == inspect.signature(
        GutterShiftStrategy.impose
    )
```

Run `python -m pytest tests/test_layout_saddle.py -q -k protocol_signature_unchanged`.
Expect `ImportError: cannot import name 'SaddleStitchStrategy'`.

### Step 2. Minimal `SaddleStitchStrategy` skeleton, green on step 1

In `deckle/core/layout.py`, add `class SaddleStitchStrategy` with `impose` copied
**character-for-character** from `LayoutStrategy.impose`'s declaration, returning
`SheetPlan(sheets=[], paper_pt=settings.paper, warnings=[])`. Re-run step 1's command; green.
Commit: `feat(SS-08): SaddleStitchStrategy skeleton behind the unmodified LayoutStrategy seam`.

### Step 3. Failing test: the 266-page arithmetic

```python
def test_266_pages_at_4_sheets_per_signature():
    plan = SaddleStitchStrategy().impose(_numbered_pages(266), _folio_settings(sheets_per_signature=4))
    assert len(plan.signatures) == 17
    assert len(plan.sheets) == 67
    fillers = [p for s in plan.sheets for side in _sides(s) for p in side.pages if p.is_filler]
    assert len(fillers) == 2
    assert all(i in plan.signatures[-1].sheet_indices for i in _sheet_indices_of(fillers, plan))
```

Run `python -m pytest tests/test_layout_saddle.py -q -k 266`. Expect failure (0 sheets).

### Step 4. The single padding pass and the slot count

Implement, in `impose`:

```python
active = [p for p in pages if not p.skipped]
per_sig = max(1, settings.sheets_per_signature)      # clamped, see step 12
slots = _pad_to_folio_slots(active, per_sig, settings.blank_mode)   # EXACTLY ONE PASS
sheets_n = len(slots) // 4
```

`_pad_to_folio_slots` appends `None` fillers **once**, to the total slot count, and returns
`(slots, blank_count)`. It never loops and never re-enters. Green on step 3's counts.
Commit: `feat(SS-08): folio slot padding in exactly one pass`.

### Step 5. Failing test: exactly one padding warning naming the blank count

```python
@pytest.mark.parametrize("n", [7, 17])
def test_one_signature_padding_warning(n):
    plan = SaddleStitchStrategy().impose(_numbered_pages(n), _folio_settings())
    pads = [w for w in plan.warnings if w.kind == "signature_padding"]
    assert len(pads) == 1
    fillers = _filler_count(plan)
    assert str(fillers) in pads[0].detail
    assert fillers == _slot_count(plan) - n          # never double-padded
```

Implement the single warning. Green. Commit.

### Step 6. Failing test: cells, and one document-wide scale

```python
def test_cells_split_the_sheet_at_the_fold():
    assert _cells((792.0, 612.0)) == ((0.0, 0.0, 396.0, 612.0), (396.0, 0.0, 792.0, 612.0))

def test_exactly_one_scale_across_the_document():
    plan = SaddleStitchStrategy().impose(_numbered_pages(266), _folio_settings())
    scales = {p.placement.scale_x for s in plan.sheets for side in _sides(s)
              for p in side.pages if not p.is_filler}
    assert len(scales) == 1
```

Implement `_cells(paper)` splitting at the vertical centreline, and compute
`scale = document_scale(active, settings, cell=cells[0])` **once**, before the sheet loop.
**Fillers carry a neutral `scale_x = 1.0` and are excluded from this assertion** — a test
asserting one distinct scale already failed on exactly this once (`docs/decisions.md`,
*Uniform document-wide scale*). Green. Commit.

### Step 7. Failing test: the fold order and the two-leaf side

```python
def test_every_folio_side_holds_exactly_two_pages():
    plan = SaddleStitchStrategy().impose(_numbered_pages(32), _folio_settings())
    for sheet in plan.sheets:
        for side in _sides(sheet):
            assert len(side.pages) == 2
        assert sheet.front is not None            # a folio sheet always has a front
def test_outermost_sheet_carries_first_and_last_page():
    plan = SaddleStitchStrategy().impose(_numbered_pages(8), _folio_settings(sheets_per_signature=2))
    front = plan.sheets[0].front
    assert _page_indices(front) == (7, 0)          # vault note line 63: saddle_order(8)[:2]
```

Implement the sheet loop: for each signature group, `order = saddle_order(len(group) * 4)`,
then consume `order` two at a time — first pair is the sheet's front, second pair its back.
`Side(pages=())` is never constructed; an absent side is `None`. Green. Commit:
`feat(SS-08): folio fold-order sheet loop, two leaves per side`.

### Step 8. Failing test: the spine inverts by cell, on both sides

Assert **measured margins**, never raw `tx` — `docs/decisions.md`, *Rebuilt the placement math
on one rule for both axes*: a coordinate assertion on one edge of one page is how a verso-only
bug survived the original suite.

```python
@pytest.mark.parametrize("side_name", ["front", "back"])
def test_spine_is_the_fold_on_both_sides(side_name):
    plan = SaddleStitchStrategy().impose(_numbered_pages(8), _folio_settings())
    side = getattr(plan.sheets[0], side_name)
    left_leaf, right_leaf = side.pages
    li, lo, _, _ = actual_margins_pt(left_leaf, plan.paper_pt, cell=CELL_L, spine_side="right")
    ri, ro, _, _ = actual_margins_pt(right_leaf, plan.paper_pt, cell=CELL_R, spine_side="left")
    assert li == pytest.approx(_expected_gutter) and ri == pytest.approx(_expected_gutter)
```

Implement placement: `_place_page(slot, ..., cell=cells[0], spine_side="right")` for the left
leaf and `cell=cells[1], spine_side="left"` for the right leaf. **`_gutter_side_is_left` must
not be reachable from this path.** Green. Commit.

### Step 9. Failing test: `rotate_deg` is zero everywhere under folio

```python
def test_folio_never_rotates_a_leaf():
    """Folio was chosen because it needs no rotation -- see the vault recipe's
    `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm`, both pure translations."""
    plan = SaddleStitchStrategy().impose(_numbered_pages(40), _folio_settings())
    for sheet in plan.sheets:
        for side in _sides(sheet):
            for page in side.pages:
                assert page.placement.rotate_deg == 0
```

This must pass without special-casing: nothing in the folio path sets a rotation. If it fails,
the MVP's `landscape_policy == "rotate"` branch is being reached — confine that branch to the
gutter-shift path rather than suppressing the assertion. Green. Commit.

### Step 10. Failing test: `binding_edge` mirrors the cells

```python
def test_binding_edge_right_mirrors_the_cells():
    left = SaddleStitchStrategy().impose(_numbered_pages(8), _folio_settings(binding_edge="left"))
    right = SaddleStitchStrategy().impose(_numbered_pages(8), _folio_settings(binding_edge="right"))
    assert _page_indices(left.sheets[0].front) == tuple(reversed(_page_indices(right.sheets[0].front)))
```

Implement: under folio, `binding_edge` selects reading direction — it swaps which source slot
lands in which cell, and nothing else. Green. Commit.

### Step 11. Failing test: signature contiguity and the plan-level invariants

```python
def test_signature_sheet_indices_are_contiguous_and_cover_the_plan():
    plan = SaddleStitchStrategy().impose(_numbered_pages(266), _folio_settings())
    seen = []
    for sig in plan.signatures:
        idx = list(sig.sheet_indices)
        assert idx == list(range(idx[0], idx[0] + len(idx)))     # contiguous
        seen += idx
    assert seen == list(range(len(plan.sheets)))                 # gapless, no overlap, in order
```

Then add the **internal** invariant assertions inside `impose`, before returning — the SS-03
precedent of verifying internal consistency rather than trusting the loop:

- every signature's slot count is a multiple of 4;
- slot counts sum to the padded slot count;
- concatenating the signatures' source slices reproduces the padded slot list *in order*;
- `sheet_indices` are contiguous across the whole plan, with no gaps and no overlaps.

Green. Commit: `feat(SS-08): plan-level signature invariants asserted inside impose`.

### Step 12. Failing tests: `blank_mode`, portrait paper, and the `sheets_per_signature` clamp

Three permissive edge cases, all *warn, never block* (`docs/decisions.md`, *Path-traversal
validation must advise, not refuse*):

- `blank_mode="balanced"` on 250 pages: no signature is more than one sheet thinner than its
  neighbours. `blank_mode="end"` on the same input puts the whole shortfall in the final
  signature.
- Portrait letter under folio: exactly one `sheet_orientation` warning, a **full** plan still
  returned, and `plan.paper_pt` **unchanged** — do not swap it.
- `sheets_per_signature <= 0` from settings is **clamped to 1** with a warning, not raised —
  it arrives from a UI spinbox. (`saddle_order` still raises `ValueError` for a bad `n`; that
  is a programming error, not user input.)

Green. Commit.

### Step 13. Failing test: creep is advisory and cannot touch geometry

```python
def test_creep_advisory_fires_and_names_the_remedy():
    plan = SaddleStitchStrategy().impose(
        _numbered_pages(32), _folio_settings(paper_thickness_pt=0.27, sheets_per_signature=8))
    adv = [w for w in plan.warnings if w.kind == "creep_advisory"]
    assert len(adv) >= 1 and "sheets per signature" in adv[0].detail

def test_thickness_never_moves_a_placement():
    a = SaddleStitchStrategy().impose(_numbered_pages(32), _folio_settings(paper_thickness_pt=0.0))
    b = SaddleStitchStrategy().impose(_numbered_pages(32), _folio_settings(paper_thickness_pt=2.0))
    assert _all_placements(a) == _all_placements(b)     # dataclass equality

def test_creep_references_are_isolated():
    """Every paper_thickness_pt reference in layout.py lies inside _creep_advisory."""
    import ast, pathlib
    tree = ast.parse(pathlib.Path("deckle/core/layout.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_creep_advisory")
    inside = {id(n) for n in ast.walk(fn)}
    stray = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "paper_thickness_pt"
             and id(n) not in inside]
    assert not stray, "paper_thickness_pt referenced outside _creep_advisory"
```

Implement `_creep_advisory(settings, sheet_index) -> LayoutWarning | None`, reporting
`paper_thickness_pt × sheets_per_signature` as the predicted fore-edge trim and naming the
remedy ("reduce to N sheets per signature"). **Never compensate.** `paper_thickness_pt=0.0`
emits none. Green. Commit.

### Step 14. Failing test: marks are attached to the right sheets

```python
def test_marks_land_on_the_right_sheets():
    plan = SaddleStitchStrategy().impose(_numbered_pages(32), _folio_settings(sewing_stations=3))
    sig = plan.signatures[0]
    for i in sig.sheet_indices:                     # fold line on every side
        for side in _sides(plan.sheets[i]):
            assert any(m.kind == "fold_line" for m in side.marks)
    innermost = plan.sheets[sig.sheet_indices[-1]]
    assert sum(m.kind == "sewing_station" for m in innermost.back.marks) == 3
    outermost = plan.sheets[sig.sheet_indices[0]]
    assert sum(m.kind == "signature_order"
               for side in _sides(outermost) for m in side.marks) == 1
```

Implement `_marks_for(...)` calling SS-06's three functions. **Sewing stations on the
innermost sheet of each signature, on its inner side** — the surface facing you when the
folded gathering lies open, which is where the awl goes in. **Signature order marks on the
outermost sheet** — the surface that becomes the visible spine. Fold lines on every sheet
side. `settings.sewing_stations <= 0` yields no station marks and **still** yields fold lines.
Both placements are flagged for SS-13 physical verification; if the dummy disagrees, this is
one predicate here and one default in `marks.py`. Green. Commit.

### Step 15. Failing test: clipping is measured against the cell

Under folio a leaf whose scaled content exceeds its **cell** emits `clipped_by_page`; the same
content measured against the full sheet does not (REQ-042). Frame the warning text as
*expected under folio* rather than reusing the 1-up phrasing — halved cell width makes
clipping the normal case, and warning fatigue trains a user to ignore warnings. Green. Commit.

### Step 16. The fold-simulator round trip — the highest-value test in the feature

```python
@pytest.mark.parametrize("n", list(range(1, 41)) + [100, 266])
@pytest.mark.parametrize("per_sig", [1, 2, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_fold_reading_order_round_trip(n, per_sig, edge):
    settings = _folio_settings(sheets_per_signature=per_sig, binding_edge=edge)
    plan = SaddleStitchStrategy().impose(_numbered_pages(n), settings)
    order = fold_reading_order(plan)
    assert [i for i in order if i is not None] == list(range(n))
```

`fold_reading_order` was written in SS-05 **from the physical fold description**, with no
reference to `saddle_order`, so it survives a bug the imposition code and its own arithmetic
tests would encode identically.

**If this fails, do NOT adjust `fold_reading_order` to agree with the imposition.** That is
the shared-wrong-assumption failure this whole design is structured to prevent, and it is an
explicit escalation trigger. Establish which one is wrong from the physical description first;
if it is not obviously a bug in exactly one of them, **stop and surface**.

Green. Commit: `feat(SS-08): folio round trip closes over the fold simulator`.

### Step 17. Full gate, then commit

```bash
python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q
python -m pytest tests/test_layout_saddle.py -q
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "feat(SS-08): SaddleStitchStrategy folio 2-up imposition"
```

If `tests/test_layout.py` or the Pinebox golden is red, **stop** — `GutterShiftStrategy` moved
and the refactor broke the MVP.

## Interface Contracts

### SaddleStitchStrategy.impose
- Direction: SS-08 → SS-09, SS-11, SS-12
- Owner: SS-08 (implements the seam owned by `contracts.yaml: LayoutStrategy.impose`,
  `frozen: true`)
- Shape: `def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`
  — character-identical to `LayoutStrategy.impose` and `GutterShiftStrategy.impose`. All
  configuration arrives via `settings`; the class is stateless and takes no constructor
  arguments.

### SheetPlan.signatures (populated)
- Direction: SS-08 → SS-11 (readout, print selector), SS-12 (`plan_passes(sheets=...)`)
- Owner: SS-01 declares the field; SS-08 owns its folio values
- Shape: `tuple[Signature, ...]`, non-empty under `fold_scheme="folio"`, **empty** under
  `"none"`. `sheet_indices` are contiguous, gapless, non-overlapping, in binding order, and
  together cover `range(len(plan.sheets))` exactly. Contiguity is the property that makes
  `plan_passes(plan, profile, sheets=sig.sheet_indices)` reverse correctly in isolation.

### Side.marks (populated)
- Direction: SS-08 → SS-09
- Owner: SS-01 declares the field; SS-06 owns the geometry; SS-08 owns the *predicate* for
  which sheet gets which mark
- Shape: `tuple[Mark, ...]` in sheet points, PDF origin bottom-left. Fold line on every side;
  `settings.sewing_stations` stations on the innermost sheet's inner side; exactly one
  `signature_order` mark on the outermost sheet.

### Cell geometry (internal)
- Direction: internal to `layout.py`
- Owner: SS-08
- Shape: `_cells(paper: tuple[float, float]) -> tuple[Cell, Cell]`, splitting at the vertical
  centreline. Letter landscape → `((0, 0, 396, 612), (396, 0, 792, 612))`.

### _creep_advisory (private, quarantined)
- Direction: internal to `layout.py`
- Owner: SS-08
- Shape: `_creep_advisory(settings: LayoutSettings, sheet_index: int) -> LayoutWarning | None`.
  **The only function in `layout.py` permitted to reference `paper_thickness_pt`**, enforced by
  an AST test. Returns `None` when `paper_thickness_pt == 0.0`. Never returns geometry.

## Verification Commands

```bash
python -m pytest tests/test_layout_saddle.py -q
python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q
python -m pytest tests/test_signatures.py tests/test_marks.py -q
python -m pytest tests/test_core_purity.py -q
python -m pytest -q
python -m ruff check deckle tests
```

## Checks

Every command below exits **0** when the criterion passes. The negative-grep rows were each
executed against the working tree at authoring time and confirmed to exit 0 — see
`docs/decisions.md`, *Negative-assertion acceptance criteria must exit 0 when the pattern is
absent*, which records the cascade that a bare `grep` caused across seven sub-specs.

| Criterion | Type | Command |
|---|---|---|
| `SaddleStitchStrategy` defined in the imposer | [STRUCTURAL] | `grep -q "class SaddleStitchStrategy" deckle/core/layout.py \|\| (echo "FAIL: SaddleStitchStrategy missing" && exit 1)` |
| `impose` signature is character-identical to the seam | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k protocol_signature_unchanged \|\| (echo "FAIL: LayoutStrategy.impose signature moved" && exit 1)` |
| 266 pages / 4 per signature → 17 sig, 67 sheets, 2 blanks | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k 266 \|\| (echo "FAIL: 266-page arithmetic wrong" && exit 1)` |
| Exactly one document-wide scale | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k one_scale \|\| (echo "FAIL: more than one document scale" && exit 1)` |
| Padding runs exactly one pass, one warning | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k padding \|\| (echo "FAIL: padding pass or warning wrong" && exit 1)` |
| `rotate_deg == 0` for every folio leaf | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k never_rotates \|\| (echo "FAIL: folio path rotated a leaf" && exit 1)` |
| Rotation machinery never entered the imposer | [MECHANICAL] | `! grep -n "_rotation_matrix" deckle/core/layout.py \|\| (echo "FAIL: rotation machinery reached the imposer" && exit 1)` |
| Signature sheet indices contiguous and covering | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k contiguous \|\| (echo "FAIL: signatures not contiguous over the plan" && exit 1)` |
| Spine derived from cell position on both sides | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k spine \|\| (echo "FAIL: spine side wrong under folio" && exit 1)` |
| `binding_edge` mirrors the cells | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k binding_edge \|\| (echo "FAIL: binding_edge does not mirror under folio" && exit 1)` |
| `paper_thickness_pt` confined to `_creep_advisory` | [MECHANICAL] | `python -m pytest tests/test_layout_saddle.py -q -k creep_references_are_isolated \|\| (echo "FAIL: paper_thickness_pt escaped _creep_advisory" && exit 1)` |
| Thickness never moves a placement | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k creep_never_affects_placement_geometry \|\| (echo "FAIL: creep reached placement geometry" && exit 1)` |
| `paper_thickness_pt` never reaches the exporter | [MECHANICAL] | `! grep -n "paper_thickness_pt" deckle/core/export.py \|\| (echo "FAIL: paper_thickness_pt reached the exporter" && exit 1)` |
| Portrait paper warns, does not block or rotate | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k portrait \|\| (echo "FAIL: portrait folio handling wrong" && exit 1)` |
| Marks on the right sheets and sides | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k marks \|\| (echo "FAIL: mark predicate wrong" && exit 1)` |
| Fold-simulator round trip over the full grid | [STRUCTURAL] | `python -m pytest tests/test_layout_saddle.py -q -k round_trip \|\| (echo "FAIL: fold round trip broken -- ESCALATE, do not adjust the simulator" && exit 1)` |
| Saddle suite has at least 25 tests | [MECHANICAL] | `python -m pytest tests/test_layout_saddle.py -q --collect-only 2>/dev/null \| grep -qE "^([2-9][5-9]\|[3-9][0-9]\|[0-9]{3,}) tests? collected" \|\| (echo "FAIL: fewer than 25 saddle tests" && exit 1)` |
| `GutterShiftStrategy` unmoved | [MECHANICAL] | `python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q \|\| (echo "FAIL: MVP layout drifted" && exit 1)` |
| No I/O or Qt in `layout.py` | [MECHANICAL] | `! grep -nE "^import (os\|io)$\|open\(\|requests\|urllib\|socket\|PySide6" deckle/core/layout.py \|\| (echo "FAIL: I/O or Qt in layout.py" && exit 1)` |
| No rendering/Qt import in `layout.py` (anchored) | [MECHANICAL] | `! grep -nE "^[[:space:]]*(import\|from)[[:space:]]+(pikepdf\|PySide6\|PyQt)" deckle/core/layout.py \|\| (echo "FAIL: rendering or Qt imported into layout.py" && exit 1)` |
| No Qt anywhere in `deckle.core` (anchored) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| Core purity gate | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity gate red" && exit 1)` |
| Full suite still green | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: full suite red" && exit 1)` |
| Lint clean | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff findings" && exit 1)` |

**Note on the anchored negative greps.** The unanchored form `! grep -rn "PySide6" deckle/core/`
matches the *"must not import PySide6"* docstrings at `deckle/core/models.py:5` and
`deckle/core/__init__.py:3` and therefore **fails on a clean tree**. Keep the
`^\s*(import|from)\s+` anchor. Do not copy the unanchored form forward from the MVP spec.

**Note on `ruff`.** Invoked as `python -m ruff check` rather than bare `ruff`, which is not on
the Git Bash `PATH` in this environment (verified: exit 127 bare, exit 0 via `python -m`).
