---
dispatch: manual
---

# F4 — Fold the folio dummy on real paper, and record what it read

**Roadmap item:** `docs/ROADMAP.md` F4
**Depends on:** — for the preparation. **F1 or F2** for the strict form: v2
sub-spec 13 requires a *calibrated* `PrinterProfile`, not a builtin preset,
and until F1 there is no way to make one from inside the app. See §3, step 0.
**Blocks:** **F7 (quarto), F8 (French fold)**, and the removal of
"experimental" from folio in the README, GUIDE §5 and the layout panel's
Signatures hint.
**Size:** S of effort, gated on hardware and a person
**Decision needed first:** none. This *produces* the input to the hardening
plan's open decision 2, *"Folio release status: gated on the folded dummy, or
shipped experimental?"*

---

## NO AGENT CAN CLOSE THIS SPEC

Read this before anything else in this file.

**F4 is `dispatch: manual`. It is closed by a human holding folded paper, and
by nothing else.** There is no automatable substitute, none is to be
invented, and none is accepted.

**What an agent may do:** steps 1-5 below — generate the dummy, confirm the
plan arithmetic, produce the print-ready PDFs, create
`docs/verification/2026-MM-DD-folio-dummy.md` with every verdict reading
`untested`, and stop.

**What an agent must never do:** run the operator checklist, fill in any
verdict, change `untested` to `confirmed`, write the findings half of the
`docs/decisions.md` entry, or report F4 as satisfied. The `[STRUCTURAL]`
checks in §5 verify only that the record has the right **shape**. They cannot
verify that it is **true**. Filling the record in to turn them green is
fabricating a verification record for a physical test that never happened,
and it defeats the only real check on this feature's central arithmetic.

`docs/specs/deckle-signatures-v2/sub-spec-13-folded-dummy.md` is the parent
of this file and says the same thing at greater length. **This spec supersedes
its commands, not its rules.**

---

## 1. Context

`saddle_order` is hand-written. `deckle/core/signatures.py:120-151` says so:

> Executed, verified recipe from ``[[pikepdf - Imposition and Signature
> Recipe]]`` (lines 35-44).

and the vault note that arithmetic came from says, quoted in
sub-spec-13:

> `saddle_order` is my own function, not pikepdf's… Validate the fold order
> against a physical folded dummy before trusting it for a real print run.

Every test in the repo that touches fold order derives from one of two
things: `saddle_order` itself, or `fold_reading_order`, which
`deckle/core/signatures.py:154-203` writes from the physical fold description
*specifically so that it is not a second copy of `saddle_order`*:

> ``saddle_order`` and ``fold_reading_order`` must never share an
> implementation: if the closed form were subtly wrong in a way that
> ``fold_reading_order`` encoded identically, the round trip between them
> would falsely pass and the printed book would come out of order.

That independence is real and it is still two pieces of software written by
one process from one mental model. **Two independent derivations of a wrong
physical assumption agree with each other.** Paper does not.

`docs/decisions.md` records this project learning the same lesson twice —
*Added run.bat; verified the GUI genuinely launches*, and *Printer
enumeration blocked the UI thread on launch*, where a green suite was the
symptom's disguise: 237 tests passed while the app hung and the suite ran for
81 minutes. **Passing tests are not observation.**

Two further questions get answered by the same print run, which is why they
are bundled:

**Is the sewing-station default physically right?** `layout.py:974-977` puts
sewing stations on the innermost sheet's **back** face. That is a reasoned
default, not a measured one. If it is wrong the fix is bounded: one predicate
in `deckle/core/layout.py` and one default in `deckle/core/marks.py`.

**Does the landscape imageable area differ from the portrait-measured
profile?** Folio feeds landscape; most printers have a different unprintable
border per orientation. `LayoutWarning.kind` already declares
`landscape_imageable_unverified` and nothing emits it (B31). A
per-orientation `PrinterProfile` is out of scope here and is F12's; measuring
it costs one extra sheet while the printer is already out.

**Calibrate on a throwaway, never on the manuscript.** The dummy exists so no
real book is spent on this.

## 2. Current code and current facts

Everything below was run on the tree at `08e7f49`.

`deckle/core/dummy.py:44-56` — the generator, and why its asymmetry matters:

```python
def _draw_page(page: pikepdf.Page, number: int, width: float, height: float) -> None:
    """Draw one numbered page: numeral, underline, and a HEAD label.

    All three matter, and the last two are what make a fold conclusive:

    - The **numeral** says which leaf this is.
    - The **underline** disambiguates a leaf that arrives upside down.
      ``6`` and ``9`` are each other rotated, and ``8`` is its own
      rotation, so a bare numeral cannot always tell you.
    - The **HEAD label** names the top edge outright, because a
      symmetrical layout can still be read the wrong way up at a glance
      even with an underline.
    """
```

`deckle/core/layout.py:961-981` — the mark predicate under test:

```python
                is_outermost = local_idx == 0
                is_innermost = local_idx == len(group) - 1

                front_marks: list = [fold_line(paper_h, fold_x)]
                back_marks: list = [fold_line(paper_h, fold_x)]
                # Folio folds down the middle, so each face carries two
                # leaves and BOTH outer edges are fore-edges. The fold
                # itself is never cut -- that is where the book bends.
                folio_cuts = cut_lines(
                    paper_w, paper_h, settings.trim_pt, ("left", "right")
                )
                front_marks.extend(folio_cuts)
                back_marks.extend(folio_cuts)
                if is_innermost:
                    back_marks.extend(
                        sewing_stations(paper_h, fold_x, settings.sewing_stations)
                    )
                if is_outermost:
                    front_marks.append(
                        signature_order_mark(sig_index, sig_count, paper_h, fold_x)
                    )
```

`deckle/core/marks.py:19-29` — the defaults being verified:

```python
SEWING_MARGIN_PT = 36.0
"""Distance from head and tail to the first/last sewing station or order mark.

Matches Bookbinder JS's "(A) Margin" setting.
"""

STATION_TICK_PT = 18.0
"""Half-length of a sewing station tick, measured perpendicular to the fold."""

ORDER_BAR_PT = 24.0
"""Height of a signature order bar, measured along the spine (y-axis)."""
```

`deckle/core/models.py:245` — the warning kind that exists for this run's
second question and is never emitted:

```python
        "landscape_imageable_unverified",
```

### What the current CLI actually is

Sub-spec 13's commands are stale. `deckle dummy` did not exist when it was
written, and there is no `--sewing-stations 3` on a bare `export` without a
fold scheme. `tools/make_numbered_fixture.py` and
`tests/fixtures/numbered-16.pdf` from that sub-spec **do not exist and must
not be created** — `deckle dummy` replaced them and is the shipped path.

```
$ ls tools/ tests/fixtures/numbered-16.pdf
ls: cannot access 'tools/': No such file or directory
ls: cannot access 'tests/fixtures/numbered-16.pdf': No such file or directory
$ ls docs/verification
"docs/verification": No such file or directory
```

`deckle/cli.py:1360-1378` — the `dummy` subcommand as it is today:

```python
    dummy_parser = subparsers.add_parser(
        "dummy",
        help="write a numbered document for checking how an imposition folds",
    )
    dummy_parser.add_argument(
        "-o", "--output", required=True, help="path to write the PDF"
    )
    dummy_parser.add_argument(
        "--pages", type=int, default=16,
        help="how many numbered pages (default: 16)",
    )
    dummy_parser.add_argument(
        "--page-size", type=_parse_paper, default=LETTER_PT, metavar="WxH",
        help=(
            "page size of the numbered document, matching the source you "
            "are standing in for (default: letter)"
        ),
    )
```

### Existing tests

`tests/test_dummy.py` checks the eight-page folio face table
(`8 | 1`, `2 | 7`, `6 | 3`, `4 | 5`) against the manual's arithmetic — which
is the thing under test, not evidence for it.
`tests/test_layout_saddle.py:83` and `tests/test_integration_signatures.py:236`
run the `fold_reading_order` ↔ `saddle_order` round trip.
`tests/test_layout_saddle.py:470-501` pins the mark predicate (fold line
everywhere, stations on the innermost back only, order mark on the outermost
front only). **If the awl verdict comes back `diverged`, those are the tests
that change**, deliberately and in a follow-up, not here.

## 3. The procedure

### Step 0 — the profile (read this before printing anything)

Sub-spec 13's Operator Procedure step 1 requires a **calibrated**
`PrinterProfile`, not a builtin preset, on the grounds that *"a dummy printed
through a guessed flip convention tests the guess, not `saddle_order`"*.

That is right and it is also not a reason to leave F4 undone. Two acceptable
routes, and the record must say which was used:

- **Strict (preferred).** F1's editor or F2's wizard has written a profile
  under this printer's name. Set `Profile:` in the record to that printer
  name.
- **Cheap.** Use a builtin preset and print **sheet 0 alone first** (step 4a
  below) to confirm the back lands upright before committing the other three
  sheets. Set `Profile:` to `generic_face_down_reversed (builtin, uncalibrated)`
  or `generic_face_up_in_order (builtin, uncalibrated)`.

The cheap route still answers the reading-order question, because a wrong
flip convention shows up as *every back upside down* — which is visibly a
flip problem, not an ordering problem. It does **not** answer it if you
silently correct for it by hand during the reload; do not.

### Step 1 — generate the numbered dummy (agent may do this)

Half-letter pages, so two of them sit side by side on a landscape Letter
sheet with nothing to scale:

```bash
python -m deckle.cli dummy -o /tmp/dummy16.pdf --pages 16 --page-size 5.5x8.5in
```

Real output on the current tree:

```
wrote /tmp/dummy16.pdf -- 16 numbered page(s)
Impose it, print it on scrap, fold it, and read the numbers. An eight-page folio puts 8 and 1 on the outside of the sheet and 4 and 5 at the centre.
```

**Write it to a temp path, not into the repository.** Sub-spec 13 asked for
`tests/fixtures/numbered-16.pdf` to be committed; `deckle dummy` is
deterministic and shipped, so the command line *is* the reproducibility
record. `tests/fixtures/*.pdf` is gitignored anyway (R0.1).

### Step 2 — confirm the arithmetic before spending paper (agent may do this)

```bash
python -m deckle.cli info /tmp/dummy16.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape
```

Real output on the current tree — **this exact output is the pass condition**:

```
page count: 16
detected page sizes (pt):
  396.00 x 612.00
signature count: 1
sheet count: 4
blank count: 0
layout warnings: none
```

If it is not exactly 1 signature / 4 sheets / 0 blanks, **stop**: the fixture
or the settings are wrong and printing would prove nothing.

### Step 3 — save the schedule (agent may do this)

```bash
python -m deckle.cli schedule /tmp/dummy16.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \
    -o /tmp/dummy-schedule.txt
```

The gathering table it prints is the sheet-by-sheet expectation the operator
checks against. On the current tree it reads:

```
  Gather in this order -- first listed is the OUTSIDE of the fold:
    sheet 0  (outermost)
        front:  16  1
        back:   2  15
    sheet 1  (position 2)
        front:  14  3
        back:   4  13
    sheet 2  (position 3)
        front:  12  5
        back:   6  11
    sheet 3  (position 4)
        front:  10  7
        back:   8  9
```

and

```
  Duplex: turn the sheet about its SHORT edge.
    The sheet is landscape and the spine runs head to tail,
    so its short edge is the vertical one.
```

### Step 4 — produce the two pass PDFs (agent may do this)

Substitute your profile name for `PROFILE` in both lines. `--pass` requires
`--profile`; that is deliberate (`cli.py:976-986`).

```bash
python -m deckle.cli export /tmp/dummy16.pdf -o /tmp/dummy-fronts.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \
    --sewing-stations 3 --pass front --profile PROFILE

python -m deckle.cli export /tmp/dummy16.pdf -o /tmp/dummy-backs.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \
    --sewing-stations 3 --pass back --profile PROFILE
```

Real output of the first, with `--profile generic_face_down_reversed`:

```
wrote /tmp/dummy-fronts.pdf
Load paper face down, feed edge top, and print pass 1 (fronts).
```

Each file is **4 pages of 792 × 612 pt**. Confirm before printing:

```bash
python -c "import pikepdf,sys
p=pikepdf.open('/tmp/dummy-fronts.pdf')
print(len(p.pages), list(p.pages[0].mediabox))"
```

expects `4 [0, 0, 792, 612]` — verified on the current tree.

Then **open both and look at them on screen**, before printing:

- 4 pages each; two numbered leaves per page; a dashed fold line down the
  centre of every page.
- **Three sewing stations on exactly one page of `dummy-backs.pdf`** — the
  innermost sheet's back, which is page 4 of the back pass in front order
  (the back pass may be reversed; identify it by the numerals `8 | 9`).
- **One signature-order bar on exactly one page of `dummy-fronts.pdf`** — the
  outermost sheet's front, the one carrying `16 | 1`.

### Step 4a — the cheap-route proof sheet (operator, optional but advised)

If Step 0 took the cheap route, print **sheet 0 only**, both passes, first:

```bash
python -m deckle.cli export /tmp/dummy16.pdf -o /tmp/s0-front.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \
    --sheets 0 --pass front --profile PROFILE
python -m deckle.cli export /tmp/dummy16.pdf -o /tmp/s0-back.pdf \
    --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \
    --sheets 0 --pass back --profile PROFILE
```

One sheet of paper. If `2 | 15` comes out upside down relative to `16 | 1`,
the preset is the wrong one — swap to the other and reprint that one sheet
before committing the remaining three.

### Step 5 — create the record, all verdicts `untested` (agent may do this, then STOP)

Create `docs/verification/` (it does not exist) and inside it
`2026-MM-DD-folio-dummy.md`, dated the day the operator will print, with
**exactly** this content and no verdicts filled in:

```markdown
# Folded dummy — folio, 16 pages, 4 sheets — 2026-MM-DD

Fixture: /tmp/dummy16.pdf, from `deckle dummy -o /tmp/dummy16.pdf --pages 16 --page-size 5.5x8.5in`
Deckle commit:
Printer:
Profile:
Paper:
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

Every verdict line is exactly one of `confirmed`, `diverged` or `untested`.

**An agent's work ends here.** Report the prepared artefacts and that F4 is
**open, pending physical verification**.

### Step 6 onward — the operator's checklist

A human performs every step. Nothing here can be delegated.

1. **Load plain 20 lb / 75 gsm Letter paper, landscape-capable.** Not the
   manuscript stock. Record what you loaded under `Paper:`.
2. **Print `/tmp/dummy-fronts.pdf` at ACTUAL SIZE.** Turn off "fit to page"
   and every other scaling option. Four sheets.
3. **Reload exactly as the pass-1 line told you**, then print
   `/tmp/dummy-backs.pdf`. Do not improvise the flip; if the instruction is
   wrong for your printer, that is the finding, and it belongs in the record.
4. **Check the sheet count.** Four sheets, printed both sides. More or fewer
   is a subset-path defect — stop and record it.
5. **Fold each sheet in half, short edge to short edge, on the printed fold
   line.** Crease firmly with a bone folder or a thumbnail.
6. **Nest them.** Sheet 1 outermost, sheet 4 innermost. **The outermost sheet
   must carry 1 and 16.** If it does not, that alone is the failure — record
   it and go to step 11.
7. **Read the gathering front to back.** Turn one leaf at a time and write
   down the sequence you actually see, **in full, even if it is correct**.
   The observed sequence is the evidence; "it was right" is not.
   - Expected: `1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16`.
   - `Verdict: confirmed` only if it reads exactly that.
   - **Pass means:** every numeral in ascending order, every `HEAD` label at
     the top of its leaf, and every underline below its numeral. A leaf whose
     number is right but whose `HEAD` is at the bottom is `diverged`, not
     confirmed — that is a rotation bug, and it is why the dummy carries the
     label at all.
8. **Open the gathering flat at its centre and prick the sewing stations.**
   - **Pass means:** three ticks are printed on the surface now facing you —
     the innermost sheet's inner face — crossing the fold, and an awl pushed
     through the centre tick goes through all four folded sheets at the fold.
     The head and tail ticks sit 36 pt (0.5 in) from the head and tail edges,
     far enough in that the thread will not tear out.
   - `confirmed` or `diverged`. **If diverged, record where they should be**
     — which sheet, which side, what inset. Do not propose a code change; it
     is one predicate in `layout.py` and one default in `marks.py`, and the
     fix belongs in a follow-up.
9. **Check the staircase.** Fold a second gathering if you have paper, or
   compare the printed bar's position against
   `marks.signature_order_mark(sig_index, sig_count, 612.0, 396.0)` for
   another index. Stacked in order, the bars should form a clean diagonal
   down the spine; deliberately swap two and confirm the break is obvious at
   a glance.
   - **Pass means:** the diagonal reads as a diagonal from arm's length, and
     a single swapped gathering is visible without counting.
   - A one-signature dummy can only confirm that the bar is *present and on
     the outermost front*; record `diverged` only for that, and note that a
     multi-signature run is still `untested`.
10. **Measure the landscape imageable area.** Measure the unprinted border on
    each of the four edges of any printed sheet, with a ruler, in
    millimetres, and convert (1 mm = 2.835 pt). Record all four alongside the
    portrait values in `PrinterProfile.imageable_area_pt`.
    - **Pass means:** you wrote down four numbers. There is no "correct"
      answer here; the verdict is `confirmed` when the two orientations agree
      within about 3 pt on every edge and `diverged` when they do not.
    - **Do not implement a per-orientation `PrinterProfile`.** A material
      difference is an escalation to F12, not a change here.
11. **Fill in the record.** All six header fields and all four verdicts with
    their observations.
12. **Add the `docs/decisions.md` entry** under the header
    `## 2026-MM-DD — The physical folded dummy`, in the file's
    Symptom / Fix / Surfaces / Watch / Commit structure.
13. **If the dummy reads out of order: STOP and escalate.** The arithmetic is
    wrong and no amount of test coverage would have shown it. **Do not adjust
    `fold_reading_order` to agree with `saddle_order`** — that is the
    shared-wrong-assumption failure this whole design is structured to
    prevent, and the v2 master spec names it as an explicit escalation
    trigger. Record the observed sequence in full; the sequence itself is the
    diagnostic.

### Step 7 — after a `confirmed` reading order

Only then, and in a separate commit:

- Remove "experimental" from `README.md`'s folio row, GUIDE §5's *"Read this
  before committing paper"* block quote, and
  `layout_panel.py:1081-1083`'s hint text (`"Experimental: the page ordering
  is hand-written arithmetic..."`), replacing each with a pointer to the
  verification record.
- Unblock F7 and F8.

Editorial while you are in GUIDE §5: **"Check 0" and "Check 2" are the same
check** (D11). Collapse them.

## 4. Tests

F4 adds no runtime code and therefore no unit tests. What it adds is a
**structural** test that the record exists and has the right shape, so the
record cannot rot into prose.

New file `tests/test_verification_records.py`:

1. `test_the_folded_dummy_record_exists`
   Glob `docs/verification/*-folio-dummy.md`; assert exactly one match.
   Unfixed: `AssertionError: no folded-dummy record under docs/verification`
   — the directory does not exist at all.

2. `test_the_record_names_its_fixture_printer_profile_paper_and_operator`
   For each of `Fixture`, `Printer`, `Profile`, `Paper`, `Operator`,
   `Deckle commit`: the line exists and has **non-empty text after the
   colon**. A header field left blank is a record of nothing.

3. `test_the_record_carries_all_four_verdict_sections`
   The headings `Reading order`, `Awl verdict`, `Staircase verdict`,
   `Landscape imageable` are all present.

4. `test_every_verdict_uses_the_committed_vocabulary`
   Exactly four lines match `^Verdict: (confirmed|diverged|untested)$`.

5. `test_the_landscape_imageable_area_is_recorded_as_numbers`
   The `Measured (left, top, right, bottom, pt):` line contains at least four
   numbers, once the verdict on that section is not `untested`. Skip the
   assertion while it is `untested`, so the test is green during preparation
   and meaningful after.

6. `test_a_confirmed_reading_order_is_matched_by_the_docs`
   **The one that keeps the docs honest.** If the `Reading order` verdict
   reads `confirmed`, then `README.md`, `docs/GUIDE.md` and
   `deckle/app/views/layout_panel.py` must no longer contain the word
   `Experimental`/`experimental` in a folio context; if it reads `untested`
   or `diverged`, they must. Implemented as: `confirmed` ⇒
   `"Experimental: the page ordering is hand-written arithmetic"` is absent
   from `layout_panel.py`; otherwise ⇒ present.

None of these can verify the record is *true*. §5 says so again, because it
is the point of this file.

## 5. Acceptance

There is **no command that verifies F4.** The rows below verify the
*preparation* and the *shape of the record*. The verification itself is a
person reading paper.

| Check | Command |
|---|---|
| [MECHANICAL] The dummy generates | `python -m deckle.cli dummy -o /tmp/dummy16.pdf --pages 16 --page-size 5.5x8.5in` |
| [MECHANICAL] It imposes to 1 signature / 4 sheets / 0 blanks | `python -m deckle.cli info /tmp/dummy16.pdf --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \| grep -q "^sheet count: 4"` |
| [MECHANICAL] Both pass PDFs are 4 landscape-Letter pages | `python -c "import pikepdf;p=pikepdf.open('/tmp/dummy-fronts.pdf');assert len(p.pages)==4;assert list(p.pages[0].mediabox)==[0,0,792,612]"` |
| [STRUCTURAL] The record exists | `ls docs/verification/*-folio-dummy.md >/dev/null` |
| [STRUCTURAL] It names fixture, printer, profile, paper, operator | `for t in Fixture Printer Profile Paper Operator; do grep -q "^$t:" docs/verification/*-folio-dummy.md \|\| { echo "FAIL: the record does not name the $t"; exit 1; }; done` |
| [STRUCTURAL] All four verdict sections present | `for t in "Reading order" "Awl verdict" "Staircase verdict" "Landscape imageable"; do grep -q -- "$t" docs/verification/*-folio-dummy.md \|\| { echo "FAIL: missing the '$t' section"; exit 1; }; done` |
| [STRUCTURAL] Exactly four verdicts, in the committed vocabulary | `test "$(grep -cE "^Verdict: (confirmed\|diverged\|untested)$" docs/verification/*-folio-dummy.md)" = "4"` |
| [MECHANICAL] Record tests pass | `.venv/bin/python -m pytest -q tests/test_verification_records.py` |
| [MECHANICAL] The suite is still green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| [STRUCTURAL] `docs/decisions.md` gains a folded-dummy entry with the house fields | `SEC=$(awk '/^## /{f=0} /^## .*[Ff]olded [Dd]ummy/{f=1} f' docs/decisions.md); test -n "$SEC" && for h in Symptom Fix Surfaces Watch Commit; do printf '%s\n' "$SEC" \| grep -q "^- $h:" \|\| { echo "FAIL: entry missing $h:"; exit 1; }; done` |
| **[HUMAN REVIEW]** A 16-page numbered dummy printed through the real printer folds, nests and reads 1 → 16 | **None.** No executable check exists. Operator steps 1-7. |
| **[HUMAN REVIEW]** The sewing stations land where an awl actually enters an opened gathering | **None.** Operator step 8. |
| **[HUMAN REVIEW]** The signature-order marks form a clean staircase and a misordered stack is visibly wrong | **None.** Operator step 9. |
| **[HUMAN REVIEW]** The measured landscape imageable area is compared against the portrait profile | **None.** Operator step 10; a material difference is an escalation to F12, not an implementation. |
| **[HUMAN REVIEW]** If the dummy reads out of order, STOP and escalate | **None.** Operator step 13. Do not adjust the fold simulator to agree with `saddle_order`. |

Every `[MECHANICAL]` and `[STRUCTURAL]` row above can be green while F4 is
entirely unsatisfied. The five `[HUMAN REVIEW]` rows are the spec.

Commands verified against the current tree while writing this file: the
`dummy`, `info`, `schedule` and `export --pass front` lines all ran and
produced the output quoted in §3. `ls docs/verification` exits non-zero.

## 6. Out of scope

- **Any code change.** F4 observes. If the awl verdict is `diverged`, the
  predicate change is a follow-up spec; if the reading order is `diverged`,
  it is an escalation, not an edit.
- **F7 (quarto) and F8 (French fold).** Both are gated on this; neither is
  started here.
- **F12** — a per-orientation `PrinterProfile`. Step 10 measures; F12 would
  implement. Explicitly forbidden here, per v2 open question 3.
- **B31** — emitting `landscape_imageable_unverified`. This run produces the
  data that would justify it; it does not add the emission.
- **Committing `tools/make_numbered_fixture.py` or
  `tests/fixtures/numbered-16.pdf`** from v2 sub-spec 13. `deckle dummy`
  replaced both.
- **Multi-signature staircase verification** beyond what one gathering can
  show. Record it `untested` and say so.

## 7. decisions.md entry

The **operator** writes the findings. An agent preparing steps 1-5 writes
only the preparation entry:

```
## 2026-09-05 — Prepared the folio folded dummy, and stopped
- Symptom: `saddle_order` is hand-written arithmetic; `fold_reading_order` is an independent derivation of the same physical assumption by the same process. Two derivations of a wrong assumption agree with each other. Nothing in the repo has ever been checked against folded paper, and folio has shipped marked experimental for that reason since 2026-08-04.
- Fix: none yet. `docs/specs/2026-09-04-roadmap/F4-folded-dummy.md` supersedes v2 sub-spec 13's stale command set with the shipped CLI (`deckle dummy`, `--fold-scheme folio --paper letter --landscape`), and `docs/verification/2026-MM-DD-folio-dummy.md` exists with all four verdicts reading `untested`. F4 is open, pending physical verification.
- Surfaces: v2 sub-spec 13 asked for `tools/make_numbered_fixture.py` and a committed `tests/fixtures/numbered-16.pdf`; `deckle dummy` shipped in the meantime and does the same job through the real pipeline, so the spec's own artefacts were obsolete before anyone ran it. A dispatch:manual spec rots at exactly the rate the CLI moves.
- Watch: every [MECHANICAL] and [STRUCTURAL] check on F4 can be green while F4 is entirely unsatisfied. An agent that fills in a verdict to turn one green has fabricated a verification record.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Every command in this
  file is `python -m deckle.cli`.
- **`--pass` requires `--profile`** (`cli.py:976-986`) and refuses with an
  explanatory message otherwise. `--profile` *without* `--pass` is silently
  ignored (B22) — so the two-line form above is the only one that works.
- **Folio wants landscape paper.** `--paper letter --landscape` gives
  792 × 612. Without `--landscape` you get a `sheet_orientation` warning and
  two pages squeezed onto a portrait sheet — and under `deckle export` that
  warning is printed to stderr, which is easy to miss in a scripted run
  (hardening plan H-2 covers the wider case).
- **The dummy's page size must be half the sheet**: `5.5x8.5in` for Letter
  landscape. A Letter-sized dummy on a Letter landscape sheet scales to fit
  and the numerals come out small, which does not invalidate the fold test
  but does make it harder to read.
- **`tests/fixtures/*.pdf` is gitignored** (R0.1). Do not try to commit the
  dummy.
- **The back pass may be reversed.** With `generic_face_down_reversed`,
  `/tmp/dummy-backs.pdf`'s page order is sheets 3, 2, 1, 0. Identify pages by
  their numerals, not by page position, when checking for the sewing
  stations.
- **A green suite is the symptom's disguise.** `docs/decisions.md` records
  237 tests passing while the app hung for 81 minutes. Do not treat
  `pytest -q` as evidence about paper.
- The pre-commit hook refuses a code commit that does not also touch
  `docs/decisions.md`, and one whose added lines contain `<FILL-IN>`. The
  record template's blank fields are `Field:` with nothing after them, which
  the hook allows; do not write `<FILL-IN>` in it.
