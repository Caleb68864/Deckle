---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-10
sub_spec_number: 10
title: "LayoutPanel and PreviewView with imageable-area guide"
depends_on: ['SS-03', 'SS-05', 'SS-09']
date: 2026-08-04
---

# SS-10 — LayoutPanel and PreviewView with imageable-area guide

## Context

Live layout controls and the sheet-accurate preview. **This is where the app's central
honesty requirement is enforced.**

Every other imposition tool shows you a preview that implies the output will look exactly
like this, then lets the printer's physical non-printable border silently eat your fore-edge
content. Deckle draws that boundary and distinguishes the two clipping causes, because the
remedies differ and one of them is not a bug:

- **`clipped_by_page`** — Deckle's own transform pushed content off the sheet. Fixable by
  reducing the gutter or changing scale mode. This is Deckle's doing.
- **`clipped_by_imageable_area`** — content sits on the sheet but inside the printer's dead
  border. The printer physically cannot mark there. Fixable only by reducing content size or
  accepting the loss.

Reporting these identically would be the single most misleading thing the app could do.

**Performance shape:** changing any layout setting re-runs `Imposer` over the whole document
immediately — that is arithmetic on a few hundred numbers, effectively instant — and
re-renders **only the visible sheet**. This split is what makes the layout panel feel live
on a 300-page document.

**Warnings attach to the sheet they affect.** A badge on sheet 12, visible when viewing sheet
12. Never a global modal.

## Provides

| Symbol | Consumed by |
|---|---|
| `LayoutPanel` | SS-14 orphan check |
| `PreviewView` (sheet selection for reprint) | SS-12, SS-14 |
| `tests/test_preview_fidelity.py` | the single-transform invariant guard |

## Requires

- `GutterShiftStrategy.impose`, `LayoutSettings`, `LayoutWarning` from SS-03
- `render_sheet`, `ink_bbox` from SS-05
- `AppState`, `MainWindow` from SS-09
- `PrinterProfile.imageable_area_pt` from SS-06

## Implementation Steps

### Step 1. Write the failing routing test

`tests/test_preview_fidelity.py`: patch `deckle.core.export.export`, render a sheet through
the preview path, and assert `export` was called with `sheets=[sheet_index]`.

**The preview *is* the exported PDF rasterized (see SS-05), so there is nothing to diff.**
What must be guarded is that the preview never grows a second, independent compositing path
— because two implementations of imposition can share a bug and agree with each other, which
is worse than having no preview at all. Write this first; it shapes the implementation.

*(Red-team C-4: the earlier pixel-tolerance formulation never specified a tolerance and was
untestable as written.)*

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_preview_fidelity.py -q
```

### Step 3. Implement LayoutPanel

Create `deckle/app/views/layout_panel.py`. Controls for paper size, gutter width (inches or
mm), binding edge, scale mode, landscape policy, and parity settings.

**Scale mode offers both options with `fit_height` preselected.** Getting the default wrong
changes every output.

Every control routes through `AppState.mutate`.

### Step 4. Implement PreviewView

Create `deckle/app/views/preview_view.py`. Sheet-by-sheet navigation with a front/back
toggle. Render the visible sheet only, off the UI thread, cancelling in-flight renders when
the user scrubs.

### Step 5. Draw the imageable-area guide

Overlay the active `PrinterProfile.imageable_area_pt` boundary as a visible guide. When no
profile is selected, use the built-in preset's value and label it as an estimate.

### Step 6. Implement distinct clipping warnings

Compare each page's `ink_bbox` against both the page box and the imageable area. Emit
`clipped_by_page` or `clipped_by_imageable_area` accordingly, with **different user-facing
text**. Test that the two produce different messages.

### Step 7. Attach warnings to sheets

Badge on the affected sheet in the preview, plus a pre-print aggregate summary. Add a test
asserting a warning on sheet 12 raises no modal dialog.

### Step 8. Prove the render scope

Test that changing the gutter width rasterizes only the visible sheet — assert `render_sheet`
call count is 1, not N.

### Step 9. Enable sheet selection for reprint

Multi-select in the preview, exposed for SS-12 to consume as a `sheets` list.

### Step 10. Commit

```bash
python -m pytest tests/test_preview_fidelity.py -q
git add -A && git commit -m "feat(SS-10): LayoutPanel and PreviewView with imageable-area guide"
```

## Interface Contracts

### PreviewView sheet selection
- Direction: SS-10 → SS-12
- Owner: SS-10
- Shape: exposes the user's selected sheet indices as `list[int]`, passed straight to
  `PrintSession(sheets=...)`. Reprint is the normal path with a smaller input.

### Clipping warning kinds
- Direction: SS-03/SS-05 → SS-10
- Owner: SS-01 (`LayoutWarning.kind`)
- Shape: `clipped_by_page` and `clipped_by_imageable_area` **must render as distinct
  messages.** Collapsing them is a correctness bug, not a copy choice.

## Verification Commands

```bash
python -m pytest tests/test_preview_fidelity.py -q
python -m pytest -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| LayoutPanel exists | [STRUCTURAL] | `test -f deckle/app/views/layout_panel.py \|\| (echo "FAIL: layout_panel.py missing" && exit 1)` |
| PreviewView exists | [STRUCTURAL] | `test -f deckle/app/views/preview_view.py \|\| (echo "FAIL: preview_view.py missing" && exit 1)` |
| fit_height is the preselected mode | [MECHANICAL] | `grep -q "fit_height" deckle/app/views/layout_panel.py \|\| (echo "FAIL: scale mode default not wired" && exit 1)` |
| Imageable-area guide drawn | [MECHANICAL] | `grep -q "imageable_area" deckle/app/views/preview_view.py \|\| (echo "FAIL: imageable area guide missing" && exit 1)` |
| Both clipping kinds handled | [MECHANICAL] | `grep -q "clipped_by_page" deckle/app/views/preview_view.py && grep -q "clipped_by_imageable_area" deckle/app/views/preview_view.py \|\| (echo "FAIL: clipping causes not distinguished" && exit 1)` |
| Preview/export fidelity test passes | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q \|\| (echo "FAIL: preview and export diverged" && exit 1)` |
