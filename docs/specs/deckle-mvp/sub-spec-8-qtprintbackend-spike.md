---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-08
sub_spec_number: 8
title: "QtPrintBackend and the cross-platform print spike"
depends_on: ['SS-06']
date: 2026-08-04
---

# SS-08 — QtPrintBackend and the cross-platform print spike

## Context

**Start with the spike. Do not implement first.**

The entire rationale for choosing a pure-Python Qt architecture (over Tauri or a local web
UI) rests on one assumption: that Qt's `QPrinter`/`QPrinterInfo` abstracts the Windows print
spooler *and* CUPS well enough that Deckle does not have to write two print backends. That
assumption is **unvalidated**. If it is wrong, it is a human decision, not something to work
around quietly — see the escalation triggers.

This sub-spec therefore produces a capability report **first**, then the implementation.

**Known limitation, already accounted for:** `QPrinter.PaperSource` (tray selection) is
effectively Windows-only. Manual duplex does not need it. Do not build on it.

**The fidelity mechanism:** rasterize each output page at the printer's own DPI via
pypdfium2 and paint it to a `QPainter` at an exact device-space rectangle, with
`setFullPage(True)` so Qt applies no margin of its own. Margins come from the profile's
`imageable_area_pt`. No PDF is ever handed to a viewer or driver that could apply
"fit to page."

**Honest scope of that promise:** transform control guarantees fidelity *within the
imageable area*. It cannot defeat the hardware's physical non-printable border. SS-10 draws
that boundary; this sub-spec supplies it.

## Provides

| Symbol | Consumed by |
|---|---|
| `QtPrintBackend` (implements `PrintBackend`) | SS-11, SS-12 |
| `docs/spikes/qprinter-capability-report.md` | human review, SS-13 |

## Requires

- `PrintBackend` Protocol, `PrintResult`, `PrinterProfile` from SS-06.
- `render_sheet` from SS-05.
- `log_print_job` from SS-07.

## Implementation Steps

### Step 1. Run the spike — Windows

Write a throwaway script exercising, on Windows:

- `QPrinterInfo.availablePrinters()` — enumeration
- `QPrinterInfo.supportedDuplexModes()` — capability reporting
- `QPrinter.pageLayout().paintRectPixels(dpi)` — imageable area
- Painting a known-size rect at device DPI and measuring the result on paper
- Page-range submission

Record each as **confirmed / diverged / untested**.

### Step 2. Run the same spike — Linux

Identical script under CUPS. Record the same five entries.

### Step 3. Write the capability report

Create `docs/spikes/qprinter-capability-report.md` with a row per capability per platform.
**Diverged entries are escalation material, not TODOs** — surface them rather than
patching around them.

### Step 4. Commit the report before implementing

```bash
git add docs/spikes/qprinter-capability-report.md
git commit -m "docs(SS-08): QPrinter cross-platform capability spike"
```

### Step 5. Write the failing chunking test

`tests/test_backend.py`: with a stubbed submission layer, a 25-sheet pass at chunk size 10
produces 3 submissions; a simulated failure on the second reports `submitted == 10`.

Chunking bounds the blast radius of a mid-job failure — losing one chunk, not a ream.

### Step 6. Implement QtPrintBackend

Create `deckle/app/__init__.py` and `deckle/app/backend.py`. Implement the `PrintBackend`
Protocol with the exact `submit` signature from SS-06.

Always `QPrinter.setFullPage(True)`. Take margins from `profile.imageable_area_pt`, never
from Qt's defaults. Do not touch `PaperSource`.

### Step 7. Wire the session log

Every `submit` call writes one `log_print_job` record **before returning**. Test it.

### Step 8. Green, then commit

```bash
python -m pytest tests/test_backend.py -q
git add -A && git commit -m "feat(SS-08): QtPrintBackend with chunked submission"
```

## Interface Contracts

### QtPrintBackend
- Direction: SS-08 → SS-11, SS-12
- Owner: SS-06 owns the Protocol; SS-08 owns this implementation.
- Shape: implements `PrintBackend.submit(plan, sheets, printer_name, copies, dpi) -> PrintResult`.
  Chunk size default 10, configurable.

### imageable area
- Direction: SS-08 → SS-10 (preview guide), SS-13 (calibration capture)
- Owner: SS-06 (`PrinterProfile.imageable_area_pt`); SS-08 populates it from
  `QPrinter.pageLayout().paintRectPixels()`.

## Verification Commands

```bash
python -m pytest tests/test_backend.py -q
test -f docs/spikes/qprinter-capability-report.md
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| Capability report exists | [STRUCTURAL] | `test -f docs/spikes/qprinter-capability-report.md \|\| (echo "FAIL: spike report not written" && exit 1)` |
| Report covers both platforms | [STRUCTURAL] | `grep -qi "windows" docs/spikes/qprinter-capability-report.md && grep -qi "linux" docs/spikes/qprinter-capability-report.md \|\| (echo "FAIL: report missing a platform" && exit 1)` |
| Report marks each capability | [STRUCTURAL] | `grep -qi "confirmed\|diverged\|untested" docs/spikes/qprinter-capability-report.md \|\| (echo "FAIL: report has no capability verdicts" && exit 1)` |
| QtPrintBackend defined | [STRUCTURAL] | `grep -q "class QtPrintBackend" deckle/app/backend.py \|\| (echo "FAIL: QtPrintBackend missing" && exit 1)` |
| setFullPage(True) used | [MECHANICAL] | `grep -q "setFullPage(True)" deckle/app/backend.py \|\| (echo "FAIL: setFullPage(True) not set" && exit 1)` |
| PaperSource not relied upon | [MECHANICAL] | `! grep -n "PaperSource" deckle/app/backend.py \|\| (echo "FAIL: PaperSource used" && exit 1)` |
| Session log wired | [MECHANICAL] | `grep -q "log_print_job" deckle/app/backend.py \|\| (echo "FAIL: print job logging missing" && exit 1)` |
| Backend tests pass | [MECHANICAL] | `python -m pytest tests/test_backend.py -q \|\| (echo "FAIL: backend tests failed" && exit 1)` |
