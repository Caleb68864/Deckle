---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-06
sub_spec_number: 6
title: "PrinterProfile and PassPlanner — the manual-duplex brain"
depends_on: ['SS-01', 'SS-03']
date: 2026-08-04
---

# SS-06 — PrinterProfile and PassPlanner: the manual-duplex brain

## Context

This is the module that makes Deckle worth building. Every imposition tool surveyed hands
you a PDF and abandons you at the print dialog. This one converts a `SheetPlan` plus a
printer's *calibrated physical behavior* into ordered passes with human reload instructions.

Pure logic. No Qt, no submission, no I/O beyond profile persistence. `PrintBackend` is a
Protocol precisely so this module never imports Qt.

**Read `Caleb's Vault/Software/pikepdf/pikepdf - Manual Duplex Reordering` before starting.**
It contains the verified back-pass ordering table this module implements, and it reaches the
same conclusion Deckle's design did from the other direction: *"Make the flip convention a
user setting with a test-page wizard, not a guess. Every printer model behaves differently
and no amount of PDF work fixes a wrong assumption about the paper path."*

### The verified ordering table — this is the specification

| Reload behavior | Back-pass order |
|---|---|
| Outputs face-down, stack reversed on reload | backs **as-is** |
| Outputs face-up, stack retains order | backs **reversed** |
| User flips on the long edge | back sides may also need 180° rotation |
| User flips on the short edge | usually no rotation |

**Rotation rule:** use `page.rotate(180, relative=True)` or assign `page.rotation`.
**Never set `page.Rotate` directly** — non-normalized values are accepted but have been
mishandled by older qpdf when transforming pages. If a print driver ignores `/Rotate`, bake
it in with `page.flatten_rotation()`.

**Deckle must be usable before the calibration wizard (SS-13) exists.** Ship built-in
presets covering at minimum the two common reload behaviors above.

## Provides

| Symbol | Consumed by |
|---|---|
| `PrintBackend` Protocol, `PrintResult` | SS-08, SS-11 |
| `plan_passes(...) -> list[PrintPass]` | SS-11, SS-14 |
| `PrintPass` | SS-11, SS-12 |
| `PrinterProfile` (+ save/load, presets) | SS-08, SS-10, SS-11, SS-12, SS-13 |

## Requires

- `SheetPlan`, `Sheet` from SS-01.

## Implementation Steps

### Step 1. Write the failing ordering tests

`tests/test_printing.py`, one test per row of the table above. Assert that a profile with
`reverse_stack=True` yields a pass-2 `sheet_order` that is the reverse of pass 1's, and
`reverse_stack=False` yields the same order.

These four tests *are* the specification. Write them before any implementation.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_printing.py -q
```

### Step 3. Define PrinterProfile

Create `deckle/core/profiles.py`. Dataclass with `version: int`,
`flip_axis: Literal["long","short"]`, `output_face: Literal["up","down"]`,
`feed_edge: Literal["top","bottom"]`, `reverse_stack: bool`,
`imageable_area_pt: tuple[float,float,float,float]`, `calibrated_at: str`,
`calibration_version: int`.

`save(name)` / `load(name)` persist JSON keyed by printer name under the OS config dir. The
`version` integer is mandatory from the first commit so migration stays possible.

### Step 4. Ship built-in presets

At minimum: face-down/reversed and face-up/in-order. Deckle must print before SS-13 lands.

### Step 5. Define the PrintBackend Protocol

In `deckle/core/printing.py`:

```python
class PrintBackend(Protocol):
    def submit(self, plan: SheetPlan, sheets: Sequence[int],
               printer_name: str, copies: int, dpi: int) -> PrintResult: ...
```

`PrintResult(submitted: int, job_id: str | None, error: str | None)`.

This Protocol is the reason the core stays Qt-free. SS-08 implements it.

### Step 6. Implement plan_passes

`plan_passes(plan, profile, sheets=None) -> list[PrintPass]` where
`PrintPass(index: int, sheet_order: list[int], side: Literal["front","back"], reload_instruction: str)`.

Apply the ordering table. Every pass carries a **non-empty** `reload_instruction` naming
the flip axis and face direction in plain language — "remove the stack, flip along the short
edge, reinsert printed-side-down", not a code.

### Step 7. Make reprint the normal path

`sheets=[7]` produces passes covering only sheet 7. Filter the input; **do not branch**.
Test this explicitly — it is the payoff for making `Sheet` a first-class object.

### Step 8. Assert Qt-free

```bash
grep -rn "PySide6" deckle/core/printing.py deckle/core/profiles.py
```

Must return nothing.

### Step 9. Commit

```bash
python -m pytest tests/test_printing.py -q
git add -A && git commit -m "feat(SS-06): PrinterProfile and PassPlanner"
```

## Interface Contracts

### PrintBackend
- Direction: SS-06 → SS-08 (implementer), SS-11 (consumer)
- Owner: SS-06
- Shape: `submit(plan: SheetPlan, sheets: Sequence[int], printer_name: str, copies: int, dpi: int) -> PrintResult`

### PrintPass
- Direction: SS-06 → SS-11, SS-12
- Owner: SS-06
- Shape: `PrintPass(index: int, sheet_order: list[int], side: Literal["front","back"], reload_instruction: str)`.
  `reload_instruction` is human-readable prose, displayed verbatim by SS-12.

### PrinterProfile
- Direction: SS-06 → SS-08, SS-10, SS-11, SS-12, SS-13
- Owner: SS-06
- Shape: see Step 3. **SS-13 populates it; everyone else only reads it.**
  `imageable_area_pt` is consumed by SS-10's clipping guide and SS-08's page setup.

## Verification Commands

```bash
python -m pytest tests/test_printing.py -q
python -m pytest tests/test_core_purity.py -q
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| PrintBackend Protocol defined | [STRUCTURAL] | `grep -q "class PrintBackend" deckle/core/printing.py \|\| (echo "FAIL: PrintBackend Protocol missing" && exit 1)` |
| PrintResult defined | [STRUCTURAL] | `grep -q "class PrintResult\|PrintResult = " deckle/core/printing.py \|\| (echo "FAIL: PrintResult missing" && exit 1)` |
| plan_passes exposed | [STRUCTURAL] | `grep -q "def plan_passes" deckle/core/printing.py \|\| (echo "FAIL: plan_passes missing" && exit 1)` |
| PrinterProfile carries imageable area | [STRUCTURAL] | `grep -q "imageable_area_pt" deckle/core/profiles.py \|\| (echo "FAIL: imageable_area_pt missing from profile" && exit 1)` |
| Profile is versioned | [STRUCTURAL] | `grep -q "version" deckle/core/profiles.py \|\| (echo "FAIL: profile not versioned" && exit 1)` |
| Built-in presets exist | [STRUCTURAL] | `grep -qi "preset" deckle/core/profiles.py \|\| (echo "FAIL: no built-in presets" && exit 1)` |
| page.Rotate never set directly | [MECHANICAL] | `! grep -n "\.Rotate *=" deckle/core/printing.py \|\| (echo "FAIL: page.Rotate assigned directly" && exit 1)` |
| Printing modules are Qt-free | [MECHANICAL] | `! grep -n "PySide6" deckle/core/printing.py deckle/core/profiles.py \|\| (echo "FAIL: Qt imported in core printing" && exit 1)` |
| Printing tests pass | [MECHANICAL] | `python -m pytest tests/test_printing.py -q \|\| (echo "FAIL: printing tests failed" && exit 1)` |
