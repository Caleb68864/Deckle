---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-09
sub_spec_number: 9
title: "Application shell, ImportView, and ArrangeView"
depends_on: ['SS-01', 'SS-02', 'SS-05']
date: 2026-08-04
---

# SS-09 — Application shell, ImportView, and ArrangeView

## Context

The main window, import flows, and the thumbnail grid with drag reorder, rotate, skip,
insert-blank, and undo. This is the view you touch most, so responsiveness matters more here
than anywhere else.

**Single source of truth:** `deckle/app/state.py` owns the `Project` instance and the undo
stack. Views mutate through `AppState.mutate(fn)` and re-read derived state. **No view holds
its own copy of the project** — that is how undo silently stops working.

**Undo is snapshot-based**, decided deliberately: `Project` is small and JSON-serializable,
so a bounded deque of serialized snapshots is simpler and more obviously correct than a
command pattern. Page reordering is exactly the case where command-pattern inverse
operations get fiddly.

**Reordering operates on the in-memory `list[SourcePage]`** — plain Python value objects,
never a `pikepdf.Pdf.pages` list. This matters: pikepdf cannot distinguish reorder from copy
in tuple-swap or slice-assignment form, so those idioms duplicate pages, assign fresh
`objgen`s, and silently break bookmarks and links. Deckle avoids the trap by construction.
Keep it that way. See `[[pikepdf - Reordering Pages Without Breaking Links]]`.

**Thumbnails are requested for the visible range only.** A 300-page import must not generate
300 thumbnails, and the UI thread must stay responsive throughout.

## Provides

| Symbol | Consumed by |
|---|---|
| `AppState` (project + undo) | SS-10, SS-12, SS-14 |
| `MainWindow` | SS-10, SS-12, SS-13, SS-14 |
| `ImportView`, `ArrangeView` | SS-14 orphan check |

## Requires

- `SourcePage`, `Project` from SS-01
- `load_pdf`, `load_image_dir` from SS-02
- `thumbnails(pages, start, count, dpi)` from SS-05

## Implementation Steps

### Step 1. Write failing undo tests

`tests/test_app_state.py`, headless (no widgets):

- Reorder pages, `undo()`, assert the previous order is restored exactly.
- Perform 60 mutations, assert the stack holds at its bounded depth of 50 and the oldest
  snapshots drop without error.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_app_state.py -q
```

### Step 3. Implement AppState

Create `deckle/app/state.py` exposing `AppState` with `project`, `undo()`, `redo()`, and
`mutate(fn)` — where `mutate` snapshots **before** applying. Bounded deque, default depth 50.

Keep this module free of widget imports so it stays unit-testable headlessly.

### Step 4. Green on undo

### Step 5. Build the main window

Create `deckle/app/main.py` and `deckle/app/views/__init__.py`. `MainWindow` hosts the views
and owns the single `AppState`.

### Step 6. Implement ImportView

File and directory pickers routed to `load_pdf` / `load_image_dir`. Import runs **off the UI
thread** with a progress indication. Surface `EncryptedPdfError` as a password prompt and
mixed-DPI warnings as info.

### Step 7. Implement ArrangeView

Thumbnail grid supporting drag reorder, per-page rotate, per-page skip, and insert-blank —
each routed through `AppState.mutate`.

Request thumbnails for the **visible range plus a small lookahead**, via
`render.thumbnails(pages, start, count)`. Never call it for the whole document.

### Step 7b. Wire autosave into mutate

Every `AppState.mutate` schedules a **debounced (500 ms)** `save_project` to the project's
autosave path. Test it: mutate, kill the process, reopen, assert the last mutated state is
restored.

`mutate` is the only correct hook — it is the single choke point for project change. Autosave
was stated in the master spec's Edge Cases but owned by no sub-spec (red-team C-5), which is
how a stated crash-recovery behavior silently doesn't happen.

### Step 7c. Define the zero-printer state

With no printers installed, the print action is **disabled with an explanatory message** —
not an empty dialog, not an exception. This is a plausible first run on a fresh Linux box
(red-team A-1).

### Step 8. Prove the performance properties

Two tests: importing a 300-page PDF calls `thumbnails` with a `count` well under 300; and no
synchronous render call occurs on the main thread during import.

### Step 9. Commit

```bash
python -m pytest tests/test_app_state.py -q
git add -A && git commit -m "feat(SS-09): app shell, ImportView, ArrangeView with undo"
```

## Interface Contracts

### AppState
- Direction: SS-09 → SS-10, SS-12, SS-14
- Owner: SS-09
- Shape: `AppState.project`, `mutate(fn) -> None` (snapshots first), `undo()`, `redo()`.
  **All project mutation in the entire app goes through `mutate`.** Bounded snapshot depth,
  default 50.

### MainWindow
- Direction: SS-09 → SS-10, SS-12, SS-13, SS-14
- Owner: SS-09
- Shape: hosts every view and owns the single `AppState` instance. SS-14 asserts every view
  module is imported here — that is the orphan check.

## Verification Commands

```bash
python -m pytest tests/test_app_state.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| AppState with mutate/undo/redo | [STRUCTURAL] | `grep -q "class AppState" deckle/app/state.py && grep -q "def mutate" deckle/app/state.py && grep -q "def undo" deckle/app/state.py \|\| (echo "FAIL: AppState API incomplete" && exit 1)` |
| Undo depth is bounded | [STRUCTURAL] | `grep -q "deque\|maxlen\|50" deckle/app/state.py \|\| (echo "FAIL: undo stack not bounded" && exit 1)` |
| ArrangeView exists | [STRUCTURAL] | `test -f deckle/app/views/arrange_view.py \|\| (echo "FAIL: arrange_view.py missing" && exit 1)` |
| ImportView exists | [STRUCTURAL] | `test -f deckle/app/views/import_view.py \|\| (echo "FAIL: import_view.py missing" && exit 1)` |
| Thumbnails requested by range | [MECHANICAL] | `grep -q "thumbnails(" deckle/app/views/arrange_view.py \|\| (echo "FAIL: arrange view does not request thumbnails" && exit 1)` |
| No pikepdf page-list manipulation in app | [MECHANICAL] | `! grep -rn "pdf.pages\[.*\] *=\|pages\[.*:.*\] *=" deckle/app/ \|\| (echo "FAIL: unsafe pikepdf page reordering idiom found" && exit 1)` |
| App state tests pass | [MECHANICAL] | `python -m pytest tests/test_app_state.py -q \|\| (echo "FAIL: app state tests failed" && exit 1)` |
