# Converge Pass 1 — standard

**Base:** `main` (9744069) · **Branch:** `2026/08/04-2345-caleb-logic-feat-deckle-signatures-v2`
**Baseline commit:** `69c7d08` — SS-12 stranded output committed; `bash.exe.stackdump` untracked

## Entry state

`python -m pytest -q` → **14 failed, 302 passed, 3 skipped**. ruff clean.

## Orchestrator triage (recorded before scanner return)

The 14 failures share one root cause with two distinct defects behind it. The
`Side` refactor — a sheet face became `Side(pages: tuple[OutputPage, ...], marks)`
instead of a bare `OutputPage` — did not fully propagate.

### Defect A — stale test constructors (test-side)

Production `deckle/core/export.py` already iterates `side.pages` correctly
(`export.py:66`, `export.py:212-215`). Eight test files still build
`Sheet(front=<OutputPage>)`, so `export.py:301` raises
`AttributeError: 'OutputPage' object has no attribute 'pages'`.

Files: `test_render`, `test_export`, `test_cli`, `test_integration`,
`test_preview_fidelity`, `test_printing`, `test_print_dialog`, `test_print_session`.

### Defect B — `deckle/core/print_session.py` (production, real)

`_side_page_index()` reads `side.source_ref`, an attribute `Side` does not have.
Two consequences:

1. `AttributeError` at runtime on any real print session under the new model.
2. `_hash_plan` records **one** page index per side (`front_page`/`back_page`).
   Under `fold_scheme="folio"` a side carries **two** output pages, so two plans
   with different page *orderings* hash identically — the exact inverse of
   REQ-014, which that function's own docstring cites.

**Defect A was masking Defect B.** Because the print-session tests never construct
a `Side`, the production path was never exercised against the new model. This is
the pattern worth remembering: a stale test fixture does not merely fail loudly,
it also silences the assertion it was supposed to make.

### Confirmed *not* affected

- `deckle/core/render.py`, `deckle/app/backend.py` — test only side *presence*
  (`is not None`); Side-safe.
- `deckle/app/views/preview_view.py` — already Side-aware;
  `content_box_guides_for_side` returns per-cell `(rect, output_page)` pairs, so
  the singular-`OutputPage` helpers (`_output_page_bbox`, `cell_label`) receive
  one cell at a time by design.

## Fixes dispatched

| Agent | Pathspec | Gap |
|---|---|---|
| `fixA-tests` | 7 test files (excludes `test_print_session.py`) | Defect A — mechanical `Side` wrap |
| `fixB-session` | `deckle/core/print_session.py`, `tests/test_print_session.py` | Defect B + REQ-014 regression test |

Scanners `scan1-core` (SS-01..07) and `scan2-app` (SS-08..12) executed
`[MECHANICAL]`/`[STRUCTURAL]` criteria concurrently. SS-13 excluded as
`manual_pending` by design (physical folded dummy, `dispatch: manual`).

## Outcome

See `pass-1-results.md` — appended once scanners and fixers returned.
