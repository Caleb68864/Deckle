---
type: phase-spec-index
master_spec: "../2026-08-04-deckle-signatures-v2.md"
date: 2026-08-04
sub_specs: 13
---

# Deckle v2 — Signature Imposition — Phase Specs

Refined from [2026-08-04-deckle-signatures-v2.md](../2026-08-04-deckle-signatures-v2.md).

| Sub-Spec | Title | Depends On | Wave | Phase Spec |
|---|---|---|---|---|
| SS-01 | Model changes — `Side`, `Mark`, `Signature`, `LayoutSettings` | — | 1 | [sub-spec-1-model-changes.md](sub-spec-1-model-changes.md) |
| SS-10 | License-audit denylist, and the pdfimpose oracle | — | 1 | [sub-spec-10-license-denylist.md](sub-spec-10-license-denylist.md) |
| SS-02 | The `Side` refactor across `deckle.core` | SS-01 | 2 | [sub-spec-2-core-side-refactor.md](sub-spec-2-core-side-refactor.md) |
| SS-05 | `signatures.py` — arithmetic and the fold simulator | SS-01 | 2 | [sub-spec-5-signatures-arithmetic.md](sub-spec-5-signatures-arithmetic.md) |
| SS-06 | `marks.py` — bindery marks as pure geometry | SS-01 | 2 | [sub-spec-6-marks-geometry.md](sub-spec-6-marks-geometry.md) |
| SS-03 | The `Side` refactor in `deckle.app` | SS-01, SS-02 | 3 | [sub-spec-3-app-side-refactor.md](sub-spec-3-app-side-refactor.md) |
| SS-04 | `_hash_plan` content-awareness, zero-diff seam | SS-01, SS-02 | 3 | [sub-spec-4-hash-plan-zero-diff.md](sub-spec-4-hash-plan-zero-diff.md) |
| SS-07 | Generalise placement math to a `Cell` | SS-02 | 3 | [sub-spec-7-cell-generalisation.md](sub-spec-7-cell-generalisation.md) |
| SS-09 | Mark rendering via `ContentStreamBuilder` | SS-02, SS-06 | 3 | [sub-spec-9-mark-rendering.md](sub-spec-9-mark-rendering.md) |
| SS-08 | `SaddleStitchStrategy` — folio 2-up imposition | SS-05, SS-06, SS-07 | 4 | [sub-spec-8-saddlestitch-strategy.md](sub-spec-8-saddlestitch-strategy.md) |
| SS-11 | UI surface — binding group, cell preview, signature selector | SS-03, SS-08 | 5 | [sub-spec-11-ui-surface.md](sub-spec-11-ui-surface.md) |
| SS-12 | Integration, fold round trip, full-suite gate | SS-04, SS-08, SS-09, SS-10, SS-11 | 6 | [sub-spec-12-integration.md](sub-spec-12-integration.md) |
| SS-13 | **The physical folded dummy — `dispatch: manual`** | SS-12 | 7 | [sub-spec-13-folded-dummy.md](sub-spec-13-folded-dummy.md) |

## Wave Ordering

```
Wave 1   SS-01  SS-10
Wave 2   SS-02  SS-05  SS-06
Wave 3   SS-03  SS-04  SS-07  SS-09
Wave 4   SS-08
Wave 5   SS-11
Wave 6   SS-12
Wave 7   SS-13   (manual)
```

No cycles, no backward phase references, no intra-wave file collisions.

## The one sub-spec no agent can close

**SS-13 is `dispatch: manual`.** It is a physical folded dummy and the **only real check on
`saddle_order`**, which is hand-written. Its own phase spec opens with a section titled *"NO
AGENT CAN CLOSE THIS SUB-SPEC"* and states the reason plainly: two independent derivations
of a wrong physical assumption will agree with each other, but paper will not. Its five
`[HUMAN REVIEW]` rows carry *"None. No executable check exists."* in the command column
rather than a substitute, and the spec notes that **every mechanical row can be green while
SS-13 is entirely unsatisfied**.

## How `fold_reading_order`'s independence is enforced

The project's largest risk is that `saddle_order` and `fold_reading_order` encode the same
wrong assumption and therefore agree — a green round-trip on a wrong book. SS-05 makes the
independence checkable three ways:

1. an **AST test** walking `fold_reading_order`'s `FunctionDef` for any `Name`/`Attribute`
   named `saddle_order`;
2. the same check as a standalone one-liner;
3. a **monkeypatch runtime test** replacing `saddle_order` with a raising stub and asserting
   the simulator still round-trips — this catches indirect reuse the AST check cannot see.

Step ordering enforces it structurally: `fold_reading_order` is written from a written-out
physical nesting table **before `saddle_order` exists in the file**.

## Shell traps these specs were written around

Each was found by executing check commands against the live tree, not by reading:

| Trap | Consequence | Form used instead |
|---|---|---|
| `cmd \|\| (echo …; exit 1)` **inside a `for` loop** | exits only the subshell; the loop continues and returns the last iteration's status, so a missing middle token prints FAIL and **passes** | brace form `\|\| { echo …; exit 1; }` |
| `^\s*(import\|from)\s+PySide6` in the app layer | matches the legitimate **lazily-imported** Qt inside functions, failing on correct code | `^(import\|from)` for module-scope checks; `^\s*` only where Qt is banned outright (`deckle.core`) |
| bare `ruff check` | exits **127** — not on PATH in this Git Bash | `python -m ruff check` |
| a negative grep naming a symbol broadly | matches the **docstring stating the rule** | anchor to the construct being forbidden |
| `pytest -k <missing>` | exits **5**, not 0 — verified | relied upon, so pre-implementation checks fail loudly rather than vacuously |

## Execution

```
/forge-run ../2026-08-04-deckle-signatures-v2.md
```

Point at the master spec — phase specs are auto-detected.
