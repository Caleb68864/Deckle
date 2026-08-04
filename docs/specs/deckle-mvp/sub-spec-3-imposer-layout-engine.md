---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-03
sub_spec_number: 3
title: "Imposer and LayoutStrategy — the gutter-shift engine"
depends_on: ['SS-01']
date: 2026-08-04
---

# SS-03 — Imposer and LayoutStrategy: the gutter-shift engine

## Context

**This is the highest-value testable code in the project.** It is a pure function: source
pages plus settings in, `SheetPlan` out. No I/O, no rendering, no printing. That purity is
what makes the imposition math verifiable without a GUI or a printer.

Two defects in the predecessor script must not reappear:

1. **Per-page geometry.** The old script computed the scale factor per page but the content
   width once from page 0's aspect ratio — so any page with a different aspect ratio got
   the wrong gutter. Compute each page's transform from **that page's own media box**.
2. **Single padding pass.** The old script inserted a filler at index 1 *and* possibly
   another at the end, driven by a counter that kept incrementing after the loop.
   Double-padding some documents. Parity here is explicit and runs exactly once.

**No library does this for you.** `pdfimpose` is AGPL and its margins are printer/scissors
margins, not a binding gutter. `cpdf` is AGPL-or-paid. Bundsteg scales rather than shifts.
The imposition math is yours to write — pikepdf will faithfully place pages wherever you
tell it, including wrong.

**The `LayoutStrategy` interface is the v2 seam.** Signature and saddle-stitch imposition
implement this same Protocol later. Do not narrow the signature to gutter-specific
parameters — that decision is the difference between adding signatures and rewriting.

## Provides

| Symbol | Consumed by |
|---|---|
| `LayoutStrategy` Protocol | v2 signature strategies |
| `GutterShiftStrategy` | SS-07, SS-10, SS-14 |
| `impose(pages, settings) -> SheetPlan` | SS-04, SS-05, SS-06, SS-07, SS-10 |

## Requires

- `SourcePage`, `Placement`, `OutputPage`, `Sheet`, `SheetPlan`, `LayoutSettings`,
  `LayoutWarning` from SS-01.

## Implementation Steps

### Step 1. Write the two regression tests first

`tests/test_layout.py`, before any implementation:

- **Defect 1:** a document whose page 3 has a different aspect ratio than page 1 produces a
  `Placement` for page 3 derived from page 3's own box.
- **Defect 2:** a 7-page document with `start_on_recto=True` and pad-to-even yields exactly
  8 output pages with **exactly one** filler.

These encode the reason this sub-spec exists. Write them first so they cannot be
rationalized away later.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_layout.py -q
```

### Step 3. Define the Protocol

In `deckle/core/layout.py`:

```python
class LayoutStrategy(Protocol):
    def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan: ...
```

Stateless. All configuration arrives via `settings`.

### Step 4. Implement fixed_gutter mode

Reserve `settings.gutter_pt`, scale content to fit the remaining area, alternate the
horizontal offset by output-page parity.

**Parity — get this right before anything else.** For a **left** binding edge:

```
index 0, 2, 4 …  →  RECTO  →  spine on the LEFT   →  tx = gutter_pt   (push right)
index 1, 3, 5 …  →  VERSO  →  spine on the RIGHT  →  tx = 0           (flush left)
```

Straight from the verified recipe, `[[pikepdf - Gutter Shift Recipe]]` Method B:
`x = GUTTER if i % 2 == 0 else 0   # recto: push right off the spine`

`binding_edge="right"` is the exact mirror. **`"top"` is out of scope for the MVP** — the
enum is narrowed to `left | right`.

Test with `gutter_pt=54` (0.75") on letter paper.

> **This step carried an inverted parity until red-team P-1.** It would have survived every
> numeric test in this file, because the tests would have encoded the same wrong assumption.
> Step 4b exists because of that.

### Step 4b. Add the sign-inversion guard

A geometric assertion, not an arithmetic one: after imposition, a **recto's content bounding
box sits further from the binding edge than a verso's.** This asserts physical intent, so it
fails on a parity inversion even if every computed number is internally consistent.

Also set `SheetPlan.paper_pt` from `settings.paper` here — `Exporter` and `Rasterizer` both
depend on it and neither receives `LayoutSettings`.

### Step 5. Implement fit_height mode (the default)

Scale content to exactly the page height; the gutter is the remaining width,
`page_width - (page_height * source_aspect)`. This is the predecessor script's behavior,
preserved because it maximizes content size.

Assert the resulting gutter matches that formula exactly.

### Step 6. Implement parity and filler handling

`start_on_recto` and pad-to-even are **separate settings**. Fillers are `OutputPage`s with
`is_filler=True` and `source_ref=None`. Run the padding logic **exactly once** and assert
the resulting plan is internally consistent (even side count, no orphan sheet) before
returning.

### Step 7. Implement landscape policy

`rotate` sets `placement.rotate_deg = 90` and emits a `mixed_orientation` warning; `scale`
fits within the portrait box; `letterbox` centers without scaling. Global setting,
per-page override.

### Step 8. Assert purity

Add a test asserting `impose` performs no file, network, or print I/O — patch `open` and
assert zero calls.

### Step 9. Reach 12+ cases, then commit

```bash
python -m pytest tests/test_layout.py -q
git add -A && git commit -m "feat(SS-03): Imposer and GutterShiftStrategy"
```

## Interface Contracts

### LayoutStrategy
- Direction: SS-03 → v2 signature strategies
- Owner: SS-03
- Shape: `def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`.
  Stateless. **Do not add gutter-specific parameters** — this signature must accommodate
  signature/saddle-stitch strategies unchanged.

### GutterShiftStrategy
- Direction: SS-03 → SS-07, SS-10, SS-14
- Owner: SS-03
- Shape: implements `LayoutStrategy`. `scale_mode="fixed_gutter"` reserves `gutter_pt` and
  scales to the remainder; `scale_mode="fit_height"` scales to page height and derives the
  gutter. Consumed downstream via the `Placement` objects on the returned `SheetPlan`.

## Verification Commands

```bash
python -m pytest tests/test_layout.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| LayoutStrategy Protocol defined | [STRUCTURAL] | `grep -q "class LayoutStrategy" deckle/core/layout.py \|\| (echo "FAIL: LayoutStrategy Protocol missing" && exit 1)` |
| impose has the committed signature | [STRUCTURAL] | `grep -q "def impose" deckle/core/layout.py \|\| (echo "FAIL: impose method missing" && exit 1)` |
| GutterShiftStrategy defined | [STRUCTURAL] | `grep -q "class GutterShiftStrategy" deckle/core/layout.py \|\| (echo "FAIL: GutterShiftStrategy missing" && exit 1)` |
| Both scale modes implemented | [STRUCTURAL] | `grep -q "fixed_gutter" deckle/core/layout.py && grep -q "fit_height" deckle/core/layout.py \|\| (echo "FAIL: both scale modes required" && exit 1)` |
| No I/O in the layout module | [MECHANICAL] | `! grep -rn "open(\|Pdf.open\|requests\." deckle/core/layout.py \|\| (echo "FAIL: I/O found in pure layout module" && exit 1)` |
| At least 12 layout test cases | [MECHANICAL] | `[ $(grep -c "def test_" tests/test_layout.py) -ge 12 ] \|\| (echo "FAIL: fewer than 12 layout tests" && exit 1)` |
| Layout tests pass | [MECHANICAL] | `python -m pytest tests/test_layout.py -q \|\| (echo "FAIL: layout tests failed" && exit 1)` |
