---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-12
sub_spec_number: 12
title: "PrintDialog — the manual-duplex print UI"
depends_on: ['SS-08', 'SS-10', 'SS-11']
date: 2026-08-04
---

# SS-12 — PrintDialog: the manual-duplex print UI

## Context

The Qt front end for a print run: choose printer and sheets, drive `PrintSession`, present
the reload instruction between passes, and offer resume on reopening an interrupted job.

**Presentation only.** All sequencing lives in SS-11. The dialog constructs a `PrintSession`
with a `QtPrintBackend` and calls its methods — it never computes pass order, sheet cursors,
or reload text. This boundary is enforced by a grep check below, because the temptation to
"just reverse the list here" is exactly how print ordering bugs become untestable.

**This is the screen where a mistake costs paper.** Two affordances exist specifically to
prevent that:

- **"Test one sheet first"** on every pass — print one, look at it, then commit the rest.
  The difference between losing one sheet and losing a ream.
- **The reload instruction is displayed verbatim** from `PrintPass.reload_instruction`, in
  plain language, and the dialog blocks until the user confirms. No inference, no
  paraphrasing.

## Provides

| Symbol | Consumed by |
|---|---|
| `PrintDialog` | SS-14 orphan check, end-to-end flow |

## Requires

- `PrintSession` from SS-11
- `QtPrintBackend` from SS-08
- `PrinterProfile` + presets from SS-06
- Sheet selection from SS-10's `PreviewView`
- `MainWindow` from SS-09

## Implementation Steps

### Step 1. Write the failing boundary test

`tests/test_print_dialog.py`: assert the dialog module contains **no** ordering arithmetic —
no `reverse`, no `sheet_order` manipulation, no `% 2`. This is a structural guard, and it
belongs first so it shapes the implementation rather than judging it.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_print_dialog.py -q
```

### Step 3. Build the dialog shell

Create `deckle/app/views/print_dialog.py`. List available printers via `QPrinterInfo` and
**preselect the one with a saved `PrinterProfile`**, if any. Where none exists, offer the
built-in presets from SS-06 with a note that calibration (SS-13) will improve accuracy.

### Step 4. Wire PrintSession

Construct `PrintSession(plan, profile, QtPrintBackend(), sheets=..., test_first=...)`. The
dialog holds the session and calls `start()` / `advance()`.

Sheet selection comes from `PreviewView` — pass it through unchanged.

### Step 5. Implement the between-pass step

Display `PrintPass.reload_instruction` **verbatim** and block until confirmed. Do not
reformat or summarize it; SS-06 wrote it to be read while standing at a printer.

### Step 6. Implement test-one-sheet

Offer it on every pass; map it to `test_first=True`. After the sheet prints, present a
confirm/abort choice that maps to `confirm_test_sheet()` or session teardown.

### Step 7. Implement resume-on-open

On application start, detect an interrupted session on disk. Offer to resume, prompt
**"How many sheets came out?"**, and pass the number straight to `PrintSession.resume()`.

### Step 8. Handle the offline printer

A printer offline at submission surfaces a blocking modal naming the printer. The session
must remain resumable afterward — test this, since the natural implementation discards it.

### Step 9. Commit

```bash
python -m pytest tests/test_print_dialog.py -q
git add -A && git commit -m "feat(SS-12): PrintDialog driving PrintSession"
```

## Interface Contracts

### PrintDialog → PrintSession
- Direction: SS-12 → SS-11
- Owner: SS-11 owns the session contract; SS-12 is purely a consumer.
- Shape: SS-12 constructs `PrintSession(..., backend=QtPrintBackend())` and calls its public
  methods only. **No print sequencing logic may live in SS-12.**

### PrintDialog → PreviewView selection
- Direction: SS-10 → SS-12
- Owner: SS-10
- Shape: `list[int]` of selected sheet indices, forwarded unchanged as `PrintSession(sheets=...)`.

## Verification Commands

```bash
python -m pytest tests/test_print_dialog.py -q
python -m pytest -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| PrintDialog exists | [STRUCTURAL] | `test -f deckle/app/views/print_dialog.py \|\| (echo "FAIL: print_dialog.py missing" && exit 1)` |
| Dialog constructs PrintSession | [MECHANICAL] | `grep -q "PrintSession" deckle/app/views/print_dialog.py \|\| (echo "FAIL: dialog does not use PrintSession" && exit 1)` |
| Dialog uses QtPrintBackend | [MECHANICAL] | `grep -q "QtPrintBackend" deckle/app/views/print_dialog.py \|\| (echo "FAIL: dialog does not wire the Qt backend" && exit 1)` |
| No ordering logic leaked into the UI | [MECHANICAL] | `! grep -n "reverse\|sheet_order\|% 2" deckle/app/views/print_dialog.py \|\| (echo "FAIL: print ordering logic leaked into the dialog" && exit 1)` |
| Reload instruction displayed | [MECHANICAL] | `grep -q "reload_instruction" deckle/app/views/print_dialog.py \|\| (echo "FAIL: reload instruction not shown" && exit 1)` |
| Test-one-sheet offered | [MECHANICAL] | `grep -q "test_first" deckle/app/views/print_dialog.py \|\| (echo "FAIL: test-one-sheet not wired" && exit 1)` |
| Resume prompt wired | [MECHANICAL] | `grep -q "resume" deckle/app/views/print_dialog.py \|\| (echo "FAIL: resume flow missing" && exit 1)` |
| Dialog tests pass | [MECHANICAL] | `python -m pytest tests/test_print_dialog.py -q \|\| (echo "FAIL: print dialog tests failed" && exit 1)` |
