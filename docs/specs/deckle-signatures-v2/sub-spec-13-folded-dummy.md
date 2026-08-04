---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-13
sub_spec_number: 13
title: "The physical folded dummy — [HUMAN REVIEW], and the gate"
depends_on: ['SS-12']
dispatch: manual
date: 2026-08-04
---

# SS-13 — The physical folded dummy: `[HUMAN REVIEW]`, and the gate

## NO AGENT CAN CLOSE THIS SUB-SPEC

Read this section before anything else in this file.

**SS-13 is `dispatch: manual`. It is closed by a human holding folded paper, and by nothing
else.** There is no automatable substitute, none is to be invented, and none is accepted.

What it requires: a real printer, real paper, a calibrated `PrinterProfile`, a bone folder or
a thumbnail, an awl, and a person who folds four sheets, nests them, and reads the page
numbers front to back.

**Why no test can stand in for it.** `saddle_order` is hand-written. It is not pikepdf's, not
pdfimpose's, and not derived from any published imposition table. The vault note
`[[pikepdf - Imposition and Signature Recipe]]`
(`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition and Signature Recipe.md`)
that this feature's arithmetic came from says so in as many words:

> `saddle_order` is my own function, not pikepdf's… Validate the fold order against a physical
> folded dummy before trusting it for a real print run.

and summarises the split as: **the pikepdf half is confirmed, the bindery half is yours.**

Every test in the repo that touches fold order derives from one of two things: `saddle_order`
itself, or `fold_reading_order`, which SS-05 writes from the physical fold description
specifically *so that it is not a second copy of `saddle_order`*. That independence is
valuable, and it is still two pieces of software written by the same process from the same
mental model. **Two independent derivations of a wrong physical assumption agree with each
other.** Paper does not.

`docs/decisions.md` records this project learning the same lesson once already — *Added
run.bat; verified the GUI genuinely launches* — and again in *Printer enumeration blocked the
UI thread on launch*, where **a green suite was the symptom's disguise**: 237 tests passed
while the app hung on launch and the suite ran for 81 minutes. Passing tests are not
observation.

**What an agent may do here:** the preparation in Steps 1–4 — generate the numbered fixture,
confirm the plan arithmetic, produce the print-ready PDF, and create the record file with
every field marked `untested`. Then it **stops** and hands off.

**What an agent must never do:** run the operator checklist, fill in any verdict, change
`untested` to `confirmed`, write the `docs/decisions.md` entry's findings, or report SS-13 as
satisfied. The `[STRUCTURAL]` checks in the *Checks* table below verify only that the record
has the right **shape**. They cannot verify that it is **true**. An agent that fills the
record in to make those checks green has not verified anything — it has fabricated a
verification record for a physical test that never happened, and it has defeated the only
real check on the feature's central arithmetic.

**This sub-spec gates the feature. Nothing ships without it.**

## Context

Print one 4-sheet signature of a numbered fixture on the real printer, through the existing
calibrated `PrinterProfile` and `PassPlanner`. Fold. Nest. Read 1 → 16. Then prick the sewing
stations and confirm they land where an awl wants them.

Two live questions get answered by the same print run, which is why they are bundled:

**1. Is the sewing-station default physically right?** SS-06 and SS-08 place sewing stations
on the **innermost sheet of each signature, on its inner side** — the surface facing you when
the folded gathering lies open, which is where the awl goes in. That is a reasoned default,
not a measured one. The dummy is what measures it. If it is wrong, the fix is small and
bounded: **one predicate in `deckle/core/layout.py` (which sheet/side gets the marks) and one
default in `deckle/core/marks.py`.** Record where they *should* be; do not guess a
replacement.

**2. Does the landscape imageable area differ from the portrait-measured profile?** The
calibration wizard (MVP SS-13) measured `PrinterProfile.imageable_area_pt` with the paper fed
in **portrait**. Folio feeds **landscape**, and most printers have a different imageable area
per orientation — hence v2's `landscape_imageable_unverified` info warning. A
`PrinterProfile` shape change (per-orientation imageable area) is explicitly **out of scope**
and is a human-approval item that *only hardware can answer*. **It costs one extra test sheet
while the printer is already out**, so measure it now. If the two differ materially, that is
an **escalation**, not an implementation — do not add the field here.

**Calibrate on a throwaway, never on the manuscript.** Use the numbered fixture, not the
266-page Traveller book.

**Numbered pages are the whole technique.** Bookbinder JS ships `/docs/example_page_numbers.pdf`
for exactly this. A dummy printed from unnumbered content cannot be read out of order.

## Files

**Files (modify):**
- `docs/decisions.md`

**Files (new):**
- `docs/verification/2026-08-04-folded-dummy.md`
- `tools/make_numbered_fixture.py`
- `tests/fixtures/numbered-16.pdf`

`docs/decisions.md` was confirmed present on the current tree; `docs/verification/` does not
exist yet and must be created. `tools/` is created by SS-10.

## Provides

| Artifact | Consumed by |
|---|---|
| `docs/verification/2026-08-04-folded-dummy.md` | the feature gate; future maintainers; any future `fold_scheme` |
| one entry in `docs/decisions.md` | the decision log |
| the awl verdict | `deckle/core/marks.py`'s station default, `layout.py`'s mark predicate |
| the measured landscape imageable area | a `PrinterProfile` shape-change escalation (deferred) |
| `tests/fixtures/numbered-16.pdf` | reproducibility of this run |

## Requires

| Symbol / artifact | From | Note |
|---|---|---|
| a working folio impose-and-export through the CLI | SS-12 | `--fold-scheme folio --sheets-per-signature 4` |
| `SaddleStitchStrategy`, mark placement predicate | SS-08 | innermost sheet / inner side |
| `sewing_stations`, `signature_order_mark`, `fold_line` | SS-06 | the geometry being verified |
| mark drawing | SS-09 | the marks must actually be on the paper |
| a **calibrated** `PrinterProfile` for the real printer | MVP SS-13 | not a builtin preset |
| the signature selector, or the CLI export | SS-11 / SS-12 | either route to one signature |
| a real printer, paper, bone folder, awl, cutting mat | the operator | — |

## Implementation Steps

Steps 1–4 are agent-preparable. **Step 5 is a hard stop.** Steps 6 onward are the operator's,
and are reproduced as the numbered checklist in *Operator Procedure* below.

### Step 1. Generate the numbered fixture

Add `tools/make_numbered_fixture.py` — development-only, alongside `tools/oracle_diff.py`
from SS-10, absent from `pyproject.toml`, imported by no shipped or tested module. It writes
a half-letter (396 × 612 pt) PDF whose every page carries its **1-based page number** large
and centred, plus a small corner glyph so a 180° rotation is distinguishable from none (the
asymmetry lesson from MVP SS-13's calibration test sheet — a symmetric mark cannot tell those
two apart, which is precisely the ambiguity being resolved).

Commit the generated `tests/fixtures/numbered-16.pdf` so the record can name the exact file
that was printed. It adds no tests; the 237-test collection floor is unaffected.

```bash
python tools/make_numbered_fixture.py 16 -o tests/fixtures/numbered-16.pdf
python -m ruff check deckle tests tools
```

### Step 2. Confirm the plan arithmetic before spending paper

```bash
python -m deckle.cli info tests/fixtures/numbered-16.pdf --fold-scheme folio --sheets-per-signature 4
```

Expect **1 signature, 4 sheets, 0 blanks**. If it is not exactly that, stop — the fixture or
the settings are wrong and printing would prove nothing.

### Step 3. Produce the print-ready PDF

```bash
python -m deckle.cli export tests/fixtures/numbered-16.pdf -o dummy.pdf \
    --fold-scheme folio --sheets-per-signature 4 --sewing-stations 3
```

Open it and confirm on screen, before printing: 8 PDF pages; two numbered leaves per page; a
fold line down the centre of every side; **three sewing stations on exactly one side of
exactly one sheet** (the innermost sheet's inner side); one signature-order mark on the
outermost sheet.

### Step 4. Create the record with every field `untested`

Create `docs/verification/2026-08-04-folded-dummy.md` (the `docs/verification/` directory
does not exist yet). Structure it with these headings and **no verdicts filled in** — every
verdict line reads `untested`:

```
# Folded dummy — 2026-08-04

Fixture:
Printer:
Profile:
Operator:
Date printed:

## Reading order (1 -> 16)
Verdict: untested
Observed:

## Awl verdict — sewing stations, innermost sheet, inner side
Verdict: untested
Observed:

## Staircase verdict — signature order marks down the spine
Verdict: untested
Observed:

## Landscape imageable area
Verdict: untested
Measured (left, top, right, bottom, pt):
Portrait-measured PrinterProfile.imageable_area_pt:
Material difference?:
```

Each verdict is one of **`confirmed`**, **`diverged`** or **`untested`**.

### Step 5. STOP — hand off to the operator

An agent's work on SS-13 ends here. Report the prepared artifacts and the fact that SS-13 is
**open, pending physical verification**. Do not run the checklist. Do not fill in a verdict.
Do not mark the sub-spec complete.

### Step 6 onward. The operator runs the checklist below

The human follows *Operator Procedure*, fills in the record, and adds the `docs/decisions.md`
entry.

## Operator Procedure

A human performs every step. Nothing here can be delegated.

1. **Confirm the printer is the calibrated one.** The `PrinterProfile` in use must be the one
   the MVP calibration wizard wrote for this physical printer — not a builtin preset. If the
   app falls back to a preset, calibrate first; a dummy printed through a guessed flip
   convention tests the guess, not `saddle_order`.
2. **Load plain 20 lb letter paper, landscape-feed capable.** Do not use the manuscript stock.
3. **Print signature 1 only.** In the app: select signature 1 in the print dialog and print.
   Or print `dummy.pdf` from Step 3. Follow the manual-duplex reload prompt exactly as the
   dialog gives it — do not improvise the flip.
4. **Check the sheet count.** Four sheets, printed both sides. If more or fewer came out, stop
   and record it; the subset path is wrong and that is an SS-11/SS-12 defect.
5. **Fold each sheet in half, short edge to short edge, on the printed fold line.** Crease
   firmly with a bone folder or a thumbnail.
6. **Nest them.** Sheet 1 outermost, sheet 4 innermost — the outermost sheet must carry pages
   1 and 16. If it does not, that alone is the failure; record it and go to step 12.
7. **Read the gathering front to back.** Turn one leaf at a time and read the printed numbers.
   Write down the sequence you actually see, in full, even if it is correct — the observed
   sequence is the evidence, "it was right" is not.
   - Expected: **1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16.**
   - Record `Verdict: confirmed` only if it reads exactly that.
8. **Open the gathering flat at its centre.** The three sewing stations should be printed on
   the surface now facing you, on the fold, at the head, centre and tail. Prick each one with
   the awl through all four folded sheets.
   - Are they on the surface an awl actually enters? Are the head and tail stations far enough
     in from the edges (the default inset is 36 pt / 0.5 in) that the thread will not tear
     out? Is the spacing one you would actually sew?
   - Record `confirmed` or `diverged`. **If diverged, record where they should be** — which
     sheet, which side, what inset. Do not propose a code change here; it is one predicate and
     one default, and the fix belongs in a follow-up.
9. **Check the staircase.** Fold a second and third gathering if you have paper to spare (or
   reuse the same one and compare against the printed marks' positions across signature
   indices). Stacked in correct order, the signature-order marks should form a clean diagonal
   down the spine. Deliberately swap two gatherings and confirm the break is obvious at a
   glance.
10. **Measure the landscape imageable area.** Print one extra sheet — a landscape page with a
    full-bleed rectangle at the paper edge, or simply measure the unprinted border on the
    dummy's sheets with a ruler. Record all four insets in points, alongside the portrait
    values already stored in `PrinterProfile.imageable_area_pt`. State whether the difference
    is material (roughly: more than ~3 pt on any edge).
    - **Do not implement a per-orientation `PrinterProfile`.** If they differ materially, that
      is an escalation. Record the numbers and raise it.
11. **Fill in `docs/verification/2026-08-04-folded-dummy.md`.** Fixture, printer, profile,
    operator, date, and each of the four verdicts with its observation.
12. **Add the `docs/decisions.md` entry**, following the file's existing
    Symptom / Fix / Surfaces / Watch / Commit structure, with the header
    `## 2026-08-04 — The physical folded dummy`.
13. **If the dummy reads out of order: STOP and escalate.** The arithmetic is wrong and no
    amount of test coverage would have shown it. **Do not adjust `fold_reading_order` to agree
    with `saddle_order`** — that is the shared-wrong-assumption failure this whole design is
    structured to prevent, and the master spec names it as an explicit escalation trigger.
    Record the observed sequence in full; the sequence itself is the diagnostic.

## Interface Contracts

### `docs/verification/2026-08-04-folded-dummy.md`
- Direction: SS-13 → the feature gate, and to any future `fold_scheme` work
- Owner: SS-13's **operator**, not an agent
- Shape: records the fixture, the printer, the profile, the operator, the observed reading
  order, the awl verdict, the staircase verdict, and the measured landscape imageable area.
  Each of the four verdicts is `confirmed`, `diverged` or `untested`.

### The awl verdict → `marks.py`
- Direction: SS-13 → SS-06 / SS-08
- Owner: SS-13 reports; a follow-up implements
- Shape: if `diverged`, names which sheet and side should carry the stations and at what
  inset. Bounded to **one predicate in `layout.py` and one default in `marks.py`.**

### The landscape imageable area → escalation only
- Direction: SS-13 → human approval
- Owner: SS-13 measures; **nobody implements in v2**
- Shape: four measured insets in points plus a material-difference verdict. A per-orientation
  `PrinterProfile` is out of scope; a material difference is raised, not coded.

### Frozen
- `saddle_order`'s pinned value `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`. If the dummy
  contradicts it, that is an escalation to a human, not an edit by whoever noticed.

## Verification Commands

**There is no command that verifies SS-13.** The commands below verify the *preparation* and
the *shape of the record*. The verification itself is a person reading paper.

```bash
python -m deckle.cli info tests/fixtures/numbered-16.pdf --fold-scheme folio --sheets-per-signature 4
python -m ruff check deckle tests tools
test -f docs/verification/2026-08-04-folded-dummy.md
```

## Checks

The `[STRUCTURAL]` rows below are executable and each was run in its unescaped form against
the current tree (all correctly report the artifacts as absent, exit 1, pre-preparation).
**They check that the record exists and has the right shape. They cannot check that it is
true.** Filling the record in to turn them green, without having folded paper, is fabrication.

The `[HUMAN REVIEW]` rows have **no command**, deliberately. That is not an oversight to be
patched by a clever agent; it is the accurate statement that no executable check exists.

**Markdown escaping:** `|` inside a table cell is written `\|`; `\|\|` is the shell's `||`.
**Loop form:** loops use `|| { echo …; exit 1; }` with braces, not a subshell — a
`|| ( … exit 1 )` inside a `for` loop exits only the subshell, so a missing middle token
passes silently. Verified empirically.

| Criterion | Type | Command |
|---|---|---|
| Numbered fixture generator exists and is dev-only | [STRUCTURAL] | `test -f tools/make_numbered_fixture.py \|\| (echo "FAIL: numbered-fixture generator missing" && exit 1)` |
| Generator is not a dependency and is imported by nothing shipped or tested | [STRUCTURAL] | `! grep -rn "make_numbered_fixture" deckle/ tests/ pyproject.toml \|\| (echo "FAIL: the dev-time fixture generator is referenced by shipped, tested or packaged code" && exit 1)` |
| Numbered 16-page fixture committed | [STRUCTURAL] | `python -c "import pikepdf,sys; sys.exit(0 if len(pikepdf.open('tests/fixtures/numbered-16.pdf').pages)==16 else 1)" \|\| (echo "FAIL: tests/fixtures/numbered-16.pdf is missing or not 16 pages" && exit 1)` |
| The fixture imposes to exactly 1 signature / 4 sheets / 0 blanks | [MECHANICAL] | `python -m deckle.cli info tests/fixtures/numbered-16.pdf --fold-scheme folio --sheets-per-signature 4 \| grep -qiE "signature" \|\| (echo "FAIL: info reports no signature breakdown for the dummy fixture" && exit 1)` |
| Verification record exists (REQ-041) | [STRUCTURAL] | `test -f docs/verification/2026-08-04-folded-dummy.md \|\| (echo "FAIL: the folded-dummy record does not exist" && exit 1)` |
| Record names fixture, printer, profile and operator | [STRUCTURAL] | `for t in Fixture Printer Profile Operator; do grep -qi "$t" docs/verification/2026-08-04-folded-dummy.md \|\| { echo "FAIL: the folded-dummy record does not name the $t"; exit 1; }; done` |
| Record carries all four verdict sections | [STRUCTURAL] | `for t in "Reading order" "Awl verdict" "Staircase verdict" "Landscape imageable"; do grep -qi -- "$t" docs/verification/2026-08-04-folded-dummy.md \|\| { echo "FAIL: the folded-dummy record is missing the '$t' section"; exit 1; }; done` |
| Every verdict uses the confirmed/diverged/untested vocabulary | [STRUCTURAL] | `test "$(grep -ciE "^Verdict: (confirmed\|diverged\|untested)" docs/verification/2026-08-04-folded-dummy.md)" = "4" \|\| (echo "FAIL: expected exactly 4 verdict lines reading confirmed, diverged or untested" && exit 1)` |
| Landscape imageable area recorded as numbers, not prose | [STRUCTURAL] | `grep -qiE "measured.*pt" docs/verification/2026-08-04-folded-dummy.md \|\| (echo "FAIL: the landscape imageable area was not recorded as a measurement" && exit 1)` |
| `docs/decisions.md` gains a folded-dummy entry in the file's structure | [STRUCTURAL] | `SEC=$(awk "/^## /{f=0} /^## .*[Ff]olded [Dd]ummy/{f=1} f" docs/decisions.md); test -n "$SEC" \|\| { echo "FAIL: docs/decisions.md has no folded-dummy entry"; exit 1; }; for h in Symptom Fix Surfaces Watch Commit; do printf "%s\n" "$SEC" \| grep -q "^- $h:" \|\| { echo "FAIL: the folded-dummy entry is missing $h:"; exit 1; }; done` |
| Lint clean including `tools/` | [MECHANICAL] | `python -m ruff check deckle tests tools` |
| Suite still green and no smaller after the fixture lands (REQ-039) | [MECHANICAL] | `python -m pytest -q` |
| **A 16-page numbered fixture printed through the real `PrinterProfile` folds, nests and reads 1 → 16** (REQ-041) | [HUMAN REVIEW] | **None.** No executable check exists. Operator Procedure steps 1–7. |
| **The sewing stations land where an awl actually enters an opened gathering** (REQ-041) | [HUMAN REVIEW] | **None.** No executable check exists. Operator Procedure step 8. |
| **The signature-order marks form a clean staircase, and a misordered stack is visibly wrong** (REQ-029, REQ-041) | [HUMAN REVIEW] | **None.** No executable check exists. Operator Procedure step 9. |
| **The measured landscape imageable area is compared against the portrait-measured profile** | [HUMAN REVIEW] | **None.** No executable check exists. Operator Procedure step 10; a material difference is an escalation, not an implementation. |
| **If the dummy reads out of order, STOP and escalate** | [HUMAN REVIEW] | **None.** No executable check exists. Operator Procedure step 13. Do not adjust the fold simulator to agree with `saddle_order`. |

**Note on `ruff`:** the executable is not on `PATH` in this environment's Git Bash;
`python -m ruff check …` is the invocation that works.

**Closing statement, restated because it is the point of this file:** every `[MECHANICAL]`
and `[STRUCTURAL]` row above can be green while SS-13 is entirely unsatisfied. The five
`[HUMAN REVIEW]` rows are the sub-spec. They are closed by a person, or they are not closed.
