---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-11
sub_spec_number: 11
title: "PrintSession — the resumable print state machine"
depends_on: ['SS-06']
date: 2026-08-04
---

# SS-11 — PrintSession: the resumable print state machine

## Context

The state machine behind a print run: pass sequencing, chunked submission, disk-backed
state, and sheet-granular resume. **No Qt.**

This was split out of the print dialog during prep because it touched four cross-cutting
concerns at once — persistence, crash recovery, state management, and logging. Separating
the logic that must be *correct* from the UI that must be *usable* means this module is
unit-testable headlessly against a stub backend, and never needs a printer to verify.

**The honest-resume principle:** software cannot know how many sheets physically emerged
before a jam. The spooler reports what it sent, not what the rollers produced. So
`PrintSession` **takes the completed count as input** rather than inferring it. SS-12 asks
the user; this module just accepts the answer.

**Why chunking matters:** submitting 200 sheets as one spool job means a failure at sheet 30
loses everything. Bounded chunks (default 10) bound the blast radius.

**"Test one sheet" is a session mode, not a UI behavior.** Putting it here keeps the
guarantee testable — `start(test_first=True)` submits exactly one sheet and parks.

## Provides

| Symbol | Consumed by |
|---|---|
| `PrintSession` | SS-12, SS-14 |
| on-disk session state | SS-12 resume flow |

## Requires

- `plan_passes`, `PrintPass`, `PrintBackend`, `PrintResult`, `PrinterProfile` from SS-06
- `SheetPlan` from SS-01
- `log_print_job` from SS-07

## Implementation Steps

### Step 1. Write the failing test-first-sheet test

`tests/test_print_session.py` with a stub `PrintBackend` that records calls:

Assert `PrintSession(..., test_first=True).start()` submits exactly 1 sheet and that no
further submission occurs until `confirm_test_sheet()` is called.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_print_session.py -q
```

### Step 3. Implement the session skeleton

Create `deckle/core/print_session.py`:

```python
PrintSession(plan, profile, backend, sheets=None, test_first=False)
```

with `start()`, `advance()`, `confirm_test_sheet()`, `resume(sheets_completed: int)`, and a
`state` property.

The `backend` is **injected** — that is what lets every test here run without a printer.

### Step 4. Implement pass sequencing

Call `plan_passes(plan, profile, sheets)` and walk the resulting passes. Between passes,
expose the `reload_instruction` from `PrintPass` verbatim. **Do not compose reload text
here** — SS-06 owns it.

### Step 5. Implement chunked submission

Default chunk size 10. On a `PrintResult` carrying an error, stop and leave the session
resumable rather than discarding it. Test the 25-sheets-at-chunk-10 case: 3 submissions, and
a failure on the second reports `submitted == 10`.

### Step 6. Implement disk-backed state

Persist after **every chunk**: `version` integer, pass index, sheet cursor, printer name.
Under the OS data dir, keyed by session id.

### Step 7. Implement resume

Test the real scenario: discard the session object mid-pass, reconstruct from disk, call
`resume(30)` on a 60-sheet pass, assert it continues at sheet 31.

### Step 8. Make reprint the normal path

`sheets=[7, 9]` submits only those sheets, routed through `plan_passes` — not a separate
branch. Test it.

### Step 9. Wire logging and assert Qt-free

Each submitted chunk writes one `log_print_job` record with printer, profile, sheet range,
DPI, and pass index populated.

```bash
grep -rn "PySide6\|QtWidgets" deckle/core/print_session.py
```

Must return nothing.

### Step 10. Commit

```bash
python -m pytest tests/test_print_session.py -q
git add -A && git commit -m "feat(SS-11): resumable PrintSession state machine"
```

## Interface Contracts

### PrintSession
- Direction: SS-11 → SS-12, SS-14
- Owner: SS-11
- Shape: `PrintSession(plan: SheetPlan, profile: PrinterProfile, backend: PrintBackend, sheets: Sequence[int] | None = None, test_first: bool = False)`
  with `start()`, `advance()`, `confirm_test_sheet()`, `resume(sheets_completed: int)`,
  `state`.
  **The backend is injected, not constructed** — SS-12 supplies `QtPrintBackend`; tests
  supply a stub.

### session state file
- Direction: SS-11 → SS-12
- Owner: SS-11
- Shape: JSON carrying `version: int`, pass index, sheet cursor, printer name. SS-12 reads
  it only to detect that a resumable session exists.

## Verification Commands

```bash
python -m pytest tests/test_print_session.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| PrintSession defined | [STRUCTURAL] | `grep -q "class PrintSession" deckle/core/print_session.py \|\| (echo "FAIL: PrintSession missing" && exit 1)` |
| Full session API present | [STRUCTURAL] | `for m in start advance confirm_test_sheet resume; do grep -q "def $m" deckle/core/print_session.py \|\| (echo "FAIL: PrintSession.$m missing" && exit 1); done` |
| Session state is versioned | [STRUCTURAL] | `grep -q "version" deckle/core/print_session.py \|\| (echo "FAIL: session state not versioned" && exit 1)` |
| Session module is Qt-free | [MECHANICAL] | `! grep -n "PySide6\|QtWidgets" deckle/core/print_session.py \|\| (echo "FAIL: Qt imported in core print session" && exit 1)` |
| Backend is injected | [MECHANICAL] | `grep -q "backend" deckle/core/print_session.py \|\| (echo "FAIL: backend not injected" && exit 1)` |
| Job logging wired | [MECHANICAL] | `grep -q "log_print_job" deckle/core/print_session.py \|\| (echo "FAIL: print job logging missing" && exit 1)` |
| Session tests pass | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q \|\| (echo "FAIL: print session tests failed" && exit 1)` |
