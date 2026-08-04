---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-13
sub_spec_number: 13
title: "Calibration wizard — state-space enumeration then implementation"
depends_on: ['SS-06', 'SS-08']
dispatch: manual
date: 2026-08-04
---

# SS-13 — Calibration wizard: state-space enumeration then implementation

## Context

**This is a spike before it is a feature, and it is marked `dispatch: manual` because it
cannot be completed unattended.** It requires a physical printer and a human looking at
paper.

This is the one **Complex-domain** component in an otherwise Complicated project. No prior
art attempts it — every surveyed tool either ignores manual duplex or hardcodes one flip
convention. There is no correct answer to analyze toward; it settles by experiment.

**The unsupported claim to kill first.** The original design asserted "2–3 questions" would
derive the printer's behavior. That is not supported. The state space is
*flip axis × output face × feed edge × stack order* — up to 16 combinations. Two or three
binary questions resolve at most 4–8. **Enumerate first, then derive the question count.**
Expect four to five questions, **or** a smarter test sheet whose asymmetric corner glyphs
disambiguate several axes in a single look — which is the more elegant answer if it holds.

**The enumeration document must live in the repo.** The derived rules are the kind of thing
that becomes undocumented folklore, and then nobody can safely change the wizard.

Independent corroboration: `[[pikepdf - Manual Duplex Reordering]]` reaches the same
conclusion from the library side — *"Make the flip convention a user setting with a test-page
wizard, not a guess. Every printer model behaves differently and no amount of PDF work fixes
a wrong assumption about the paper path."*

## Provides

| Symbol | Consumed by |
|---|---|
| `build_test_plan() -> SheetPlan` | the wizard itself |
| `derive_profile(answers) -> PrinterProfile` | SS-06 profile store |
| `docs/spikes/calibration-state-space.md` | future maintainers, SS-06 field set |
| `CalibrationWizard` | SS-14 orphan check |

## Requires

- `PrinterProfile`, `PrintPass` from SS-06
- `QtPrintBackend` from SS-08
- `SheetPlan`, `Sheet`, `OutputPage` from SS-01

## Implementation Steps

### Step 1. Enumerate the state space — before any code

Write `docs/spikes/calibration-state-space.md`. Enumerate every reachable combination of
`flip_axis`, `output_face`, `feed_edge`, `reverse_stack`. For each, mark whether it is
**physically distinguishable from a printed test sheet**. Several combinations are likely
observationally identical — those collapse, and that is what shrinks the question count.

State the derived minimum question count and the reasoning.

### Step 2. Commit the enumeration before implementing

```bash
git add docs/spikes/calibration-state-space.md
git commit -m "docs(SS-13): calibration state-space enumeration"
```

**If the enumeration shows more than five questions are needed, or that the axes are not
independently determinable — stop and escalate.** That changes the UX and is a human
decision.

### Step 3. Write the failing exhaustive-mapping test

`tests/test_calibration.py`: iterate the **full enumeration** from Step 1 and assert
`derive_profile` maps each answer combination to exactly one profile, with no combination
producing an ambiguous or unreachable result.

This test is why the enumeration comes first — it has nothing to iterate otherwise.

### Step 4. Implement build_test_plan

Create `deckle/core/calibration.py`. `build_test_plan() -> SheetPlan` produces a 4-sheet plan
with **asymmetric per-corner glyphs** and visible sheet numbers on **both sides**.

Asymmetry is the whole point: a symmetric mark cannot distinguish a 180° rotation from no
rotation, which is precisely the ambiguity being resolved.

### Step 5. Implement derive_profile

`derive_profile(answers: Mapping[str, str]) -> PrinterProfile` — **pure, no Qt, no I/O.**
That purity is what makes Step 3's exhaustive test possible.

### Step 6. Green on the exhaustive mapping

### Step 7. Build the wizard UI

Create `deckle/app/views/calibration_wizard.py`. Print pass 1, let the user reload however
they normally would, print pass 2, then ask the illustrated questions.

Illustrations matter more than wording here — a picture of the expected mark position is
unambiguous where prose is not.

### Step 8. Capture the imageable area

Read the observed printable bounds from the test sheet and write them into
`PrinterProfile.imageable_area_pt`. This is the calibration run's second deliverable and
SS-10's preview guide depends on it.

### Step 9. Persist and verify round-trip

Completing the wizard writes a profile that survives an application restart.

### Step 10. Run it against real hardware

Calibrate the actual printer, print a multi-sheet document double-sided, and confirm the
output collates correctly. **This is the `[HUMAN REVIEW]` criterion and the reason this
sub-spec is `dispatch: manual`.**

### Step 11. Commit

```bash
python -m pytest tests/test_calibration.py -q
git add -A && git commit -m "feat(SS-13): calibration wizard and profile derivation"
```

## Interface Contracts

### derive_profile
- Direction: SS-13 → SS-06 profile store
- Owner: SS-13
- Shape: `derive_profile(answers: Mapping[str, str]) -> PrinterProfile`. **Pure** — no Qt,
  no I/O. Total over the enumerated answer space.

### build_test_plan
- Direction: SS-13 → SS-08 (submitted like any other plan)
- Owner: SS-13
- Shape: `build_test_plan() -> SheetPlan`. A synthetic 4-sheet plan; it flows through the
  ordinary print path, which also exercises that path end to end.

## Verification Commands

```bash
python -m pytest tests/test_calibration.py -q
test -f docs/spikes/calibration-state-space.md
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| State-space enumeration exists | [STRUCTURAL] | `test -f docs/spikes/calibration-state-space.md \|\| (echo "FAIL: calibration state space not enumerated" && exit 1)` |
| Enumeration covers all four axes | [STRUCTURAL] | `for a in flip_axis output_face feed_edge reverse_stack; do grep -q "$a" docs/spikes/calibration-state-space.md \|\| (echo "FAIL: axis $a not enumerated" && exit 1); done` |
| Derived question count stated | [STRUCTURAL] | `grep -qi "question" docs/spikes/calibration-state-space.md \|\| (echo "FAIL: derived question count not stated" && exit 1)` |
| build_test_plan exposed | [STRUCTURAL] | `grep -q "def build_test_plan" deckle/core/calibration.py \|\| (echo "FAIL: build_test_plan missing" && exit 1)` |
| derive_profile exposed and pure | [STRUCTURAL] | `grep -q "def derive_profile" deckle/core/calibration.py && ! grep -n "PySide6" deckle/core/calibration.py \|\| (echo "FAIL: derive_profile missing or not pure" && exit 1)` |
| Wizard view exists | [STRUCTURAL] | `test -f deckle/app/views/calibration_wizard.py \|\| (echo "FAIL: calibration_wizard.py missing" && exit 1)` |
| Imageable area captured | [MECHANICAL] | `grep -q "imageable_area" deckle/app/views/calibration_wizard.py \|\| (echo "FAIL: imageable area not captured during calibration" && exit 1)` |
| Calibration tests pass | [MECHANICAL] | `python -m pytest tests/test_calibration.py -q \|\| (echo "FAIL: calibration tests failed" && exit 1)` |
