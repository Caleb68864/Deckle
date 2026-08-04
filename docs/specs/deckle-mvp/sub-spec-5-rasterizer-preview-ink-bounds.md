---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-05
sub_spec_number: 5
title: "Rasterizer — preview rendering, thumbnails, and ink bounds"
depends_on: ['SS-01', 'SS-03', 'SS-04']
date: 2026-08-04
---

# SS-05 — Rasterizer: preview rendering, thumbnails, and ink bounds

## Architecture change — read this first

**The preview renders the actual exported PDF. It never re-draws the layout independently.**

`render_sheet` calls `export(plan, <temp>, sheets=[sheet_index])` and rasterizes *that
artifact*. It does not composite from `Placement` objects in parallel with the exporter.

Why this is stronger than compositing independently: the failure mode of an imposition tool
is discovering after 200 printed pages that the gutter went the wrong way on versos. A
preview that re-draws the layout can be wrong in exactly the same way the exporter is wrong,
and agree with it — two implementations of the same bug reassure you. **A rasterized
artifact cannot lie**, because it *is* the thing that goes to the printer.

This supersedes the earlier "one transform, three consumers plus a fidelity diff" approach:
identity beats agreement-within-tolerance, and it deletes a whole class of drift. It is why
SS-05 now depends on SS-04.

## Context

Pixels for the preview, thumbnails for the arrange grid, and ink bounding boxes for
clipping detection. **This module is the Qt boundary** — it returns raw RGBA buffers, and
`deckle.app` wraps them in `QImage`. No Qt type may cross into the core.

**Read before writing render code:** `Caleb's Vault/Software/pypdfium2/` — particularly
`pypdfium2 - Memory and Object Lifetime`, `pypdfium2 - Bitmap Formats Stride and Buffers`,
and `pypdfium2 - Gotchas and Limitations`. Buffer stride handling is easy to get subtly
wrong and produces skewed images.

**Why pypdfium2 and not the obvious alternative:** pikepdf cannot render, and its own
documentation suggests PyMuPDF or Ghostscript — **both AGPL-or-commercial**, which would
make Deckle undistributable as MIT. pypdfium2 is BSD-3/Apache-2.0 and self-contained (no
external binary). Three independent research threads landed here, one by overruling
pikepdf's official docs.

**Two performance constraints that are architectural, not optimizations:**

1. **Thumbnails are virtualized.** The API takes an explicit page range, never "all pages".
   A 300-page document must not generate 300 thumbnails.
2. **Ink bbox is computed on demand and cached.** Neither pypdfium2 nor pikepdf exposes an
   ink bounding box — it requires rasterizing at low DPI and scanning for non-background
   pixels. Doing that eagerly across 300 pages stalls the UI.

## Provides

| Symbol | Consumed by |
|---|---|
| `RenderedPage` | SS-09, SS-10 |
| `render_sheet(...)` | SS-10, SS-14 |
| `thumbnails(pages, start, count, dpi)` | SS-09 |
| `ink_bbox(ref, dpi)` | SS-03 warnings, SS-10 |

## Requires

- `SheetPlan`, `SourcePage`, `SourceRef`, `Placement` from SS-01.

## Implementation Steps

### Step 1. Write the failing no-Qt test

Assert `deckle/core/render.py` imports no Qt symbol. Then assert `RenderedPage` exists and
carries `width`, `height`, `rgba: bytes` — a raw buffer, not a PIL or Qt image.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_render.py -q
```

### Step 3. Implement render_sheet

Create `deckle/core/render.py`. Render one sheet side at a requested DPI by rasterizing each
`OutputPage`'s source through pypdfium2 and compositing at its `Placement`.

Mind buffer **stride** — pypdfium2 bitmaps are not always tightly packed. Read the vault
note before assuming `width * 4`.

Respect `cancel: threading.Event` cooperatively: check it between pages and return promptly
when set.

### Step 4. Test cancellation

Assert a pre-set cancel event returns without completing a full render.

### Step 5. Implement virtualized thumbnails

`thumbnails(pages, start, count, dpi=36)` — an explicit range. Bounded LRU cache keyed by
`(SourceRef, dpi)`.

Add a test asserting there is **no** whole-document thumbnail entry point. This is a design
constraint, not a preference.

### Step 6. Implement ink_bbox with caching

Rasterize at low DPI, scan for non-background pixels, return
`(llx, lly, urx, ury)` in PDF points. Cache per `SourceRef`.

Add a call-counter test: calling `ink_bbox` twice for the same ref rasterizes **once**.

### Step 7. Handle the blank-page edge

An entirely blank page returns an empty or degenerate bbox — it must not raise. Test it.

### Step 8. Manage pypdfium2 object lifetime

Follow `[[pypdfium2 - Memory and Object Lifetime]]`. Close documents and bitmaps
deterministically; do not rely on GC. Leaked handles surface as file locks on Windows, which
is a confusing failure to debug later.

### Step 9. Commit

```bash
python -m pytest tests/test_render.py tests/test_core_purity.py -q
git add -A && git commit -m "feat(SS-05): Rasterizer with virtualized thumbnails and ink bounds"
```

## Interface Contracts

### RenderedPage
- Direction: SS-05 → SS-09, SS-10
- Owner: SS-05
- Shape: `RenderedPage(width: int, height: int, rgba: bytes)`. Raw buffer only — **no Qt or
  PIL type crosses the core boundary.**

### render_sheet
- Direction: SS-05 → SS-10, SS-14
- Owner: SS-05
- Shape: `render_sheet(plan: SheetPlan, sheet_index: int, side: Literal["front","back"], dpi: int, cancel: threading.Event | None = None) -> RenderedPage`

### thumbnails
- Direction: SS-05 → SS-09
- Owner: SS-05
- Shape: `thumbnails(pages: Sequence[SourcePage], start: int, count: int, dpi: int = 36) -> list[RenderedPage]`.
  **Range-based by contract** — there is deliberately no whole-document variant.

### ink_bbox
- Direction: SS-05 → SS-03, SS-10
- Owner: SS-05
- Shape: `ink_bbox(ref: SourceRef, dpi: int = 36) -> tuple[float, float, float, float]`,
  PDF points, cached per ref.

## Verification Commands

```bash
python -m pytest tests/test_render.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| RenderedPage defined | [STRUCTURAL] | `grep -q "class RenderedPage" deckle/core/render.py \|\| (echo "FAIL: RenderedPage missing" && exit 1)` |
| render_sheet exposed | [STRUCTURAL] | `grep -q "def render_sheet" deckle/core/render.py \|\| (echo "FAIL: render_sheet missing" && exit 1)` |
| thumbnails is range-based | [STRUCTURAL] | `grep -q "def thumbnails" deckle/core/render.py && grep -q "count" deckle/core/render.py \|\| (echo "FAIL: thumbnails must take an explicit range" && exit 1)` |
| ink_bbox exposed | [STRUCTURAL] | `grep -q "def ink_bbox" deckle/core/render.py \|\| (echo "FAIL: ink_bbox missing" && exit 1)` |
| No Qt types in render | [MECHANICAL] | `! grep -n "PySide6\|QImage\|QPixmap" deckle/core/render.py \|\| (echo "FAIL: Qt type in core renderer" && exit 1)` |
| pypdfium2 used, not PyMuPDF | [MECHANICAL] | `grep -q "pypdfium2" deckle/core/render.py && ! grep -q "fitz\|PyMuPDF" deckle/core/render.py \|\| (echo "FAIL: wrong render engine" && exit 1)` |
| Render tests pass | [MECHANICAL] | `python -m pytest tests/test_render.py -q \|\| (echo "FAIL: render tests failed" && exit 1)` |
