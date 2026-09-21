# Calibration state-space enumeration

`docs/specs/deckle-mvp/sub-spec-13-calibration-wizard.md` refuses to be
implemented as written. Its own words:

> The original design asserted "2–3 questions" would derive the printer's
> behavior. That is not supported. The state space is *flip axis × output face
> × feed edge × stack order* — up to 16 combinations. Two or three binary
> questions resolve at most 4–8. **Enumerate first, then derive the question
> count.**
>
> **If the enumeration shows more than five questions are needed, or that the
> axes are not independently determinable — stop and escalate.**

This document is that enumeration. It is committed before any wizard code so
the table cannot be quietly reshaped to fit whatever the implementation turned
out to do.

Verdicts are one of **confirmed** / **diverged** / **untested**, as in
`qprinter-capability-report.md`. "Confirmed" here means *confirmed by reading
the code named in the row*, and needs no hardware; anything needing a printer
is **untested** and says so.

## The 16 states

Axis order `flip_axis × output_face × feed_edge × reverse_stack`. Written out
in full rather than abbreviated: the point of the table is that a maintainer
can see the whole domain at once.

| # | flip_axis | output_face | feed_edge | reverse_stack | Physically reachable? | Distinguishable from a printed sheet? |
|---|---|---|---|---|---|---|
| 1 | long | down | top | False | untested | yes — E answers flip_axis, C/D answer reverse_stack |
| 2 | long | down | top | True | untested | yes ← `generic_face_down_reversed` |
| 3 | long | down | bottom | False | untested | yes |
| 4 | long | down | bottom | True | untested | yes |
| 5 | long | up | top | False | untested | yes |
| 6 | long | up | top | True | untested | yes |
| 7 | long | up | bottom | False | untested | yes |
| 8 | long | up | bottom | True | untested | yes |
| 9 | short | down | top | False | untested | yes |
| 10 | short | down | top | True | untested | yes |
| 11 | short | down | bottom | False | untested | yes |
| 12 | short | down | bottom | True | untested | yes |
| 13 | short | up | top | False | untested | yes |
| 14 | short | up | top | True | untested | yes |
| 15 | short | up | bottom | False | untested | yes ← `generic_face_up_in_order` |
| 16 | short | up | bottom | True | untested | yes |

Every row is marked distinguishable, and that claim is cheap rather than
impressive: it follows from Finding A. Each axis has its own question on the
printed sheet, so no two rows collapse into one set of answers. What is
**untested** is whether every row is physically *reachable* — whether a real
printer exists for each combination. That does not affect the question count,
because the wizard must handle any answer set it is given regardless of whether
some are rare.

## Finding A — only two axes change what lands on paper

`plan_passes` consumes exactly two of the four:

- `reverse_stack` → sheet order on the back pass
  (`deckle/core/printing.py:228`, `back_order = list(reversed(indices)) if
  profile.reverse_stack else list(indices)`).
- `flip_axis` → whether backs need a half turn
  (`deckle/core/printing.py:243`).

`output_face` and `feed_edge` reach only `_front_instruction` /
`_back_instruction` (`deckle/core/printing.py:190`, `:203`) — the prose telling
the operator how to reload. They are *reported observations*, not derivations.

**Verdict: confirmed** by reading `deckle/core/printing.py`. No hardware
needed.

Consequence, and it is the one that matters for risk: the 16 states collapse to
**4 behaviourally distinct** ones. A wrong answer on `output_face` or
`feed_edge` mis-words an instruction, which a human reading it will notice. A
wrong answer on `flip_axis` or `reverse_stack` ruins a stack of paper silently.
The wizard should weight its confirmation step accordingly.

## Finding B — is `reverse_stack` a function of `(output_face, feed_edge)`?

`profiles.py:61-74` says it is derived "at calibration time, from the
combination of `output_face` and `feed_edge`". The two shipped presets
(`profiles.py:452-469`) are the only evidence:

| preset | output_face | feed_edge | reverse_stack |
|---|---|---|---|
| `generic_face_down_reversed` | down | top | True |
| `generic_face_up_in_order` | up | bottom | False |

Two data points fitting four possible `(output_face, feed_edge)` combinations.
`down+bottom` and `up+top` have never been observed.

**Verdict: untested.** Phase 5 fills it in at a printer.

`docs/decisions.md` reached the same conclusion from the other direction on
2026-09-10: both axes "are claims about how a *physical* printer hands paper
back", re-derived independently and in agreement — and then the line worth
keeping, *"Two agreeing derivations from an unverified premise are still one
unverified premise."* That is this finding stated as a principle, and it is the
reason the wizard asks rather than computes.

This is why `reverse_stack` **stays a question** rather than being derived.
Deriving it from two data points would be the same class of error the spec
opens by rejecting — an unsupported claim about how few questions are needed.
If Phase 5 confirms the function, the count drops to three and `profiles.py`
keeps its docstring; if it does not, that docstring is wrong and must be
corrected in the same commit.

The printed sheet already hedges this correctly: question **C** (which sheet
was on top) and question **D** (which back landed on which front) are two
independent readings of the same axis, so a self-inconsistent printer shows up
as a contradiction rather than as a quiet wrong answer.

## Finding C — the spec's premise here is stale, and the correction widens the space

**The sub-spec and the F2 implementation spec both state that `plan_passes`
uses `flip_axis` as `rotate_backs = profile.flip_axis == "long"`. It does
not, and has not since 2026-08-06.** The current rule
(`deckle/core/printing.py:243`) is:

```python
rotate_backs=profile.flip_axis != duplex_flip_edge(plan.paper_pt)
```

The module docstring (`printing.py:43-64`) is explicit that this was a
correction to a rule "that stood here for months as `flip_axis == 'long'`",
and that the old rule "is right for exactly one orientation and silently upside
down for the other, which is a whole run of ruined paper that nothing on screen
reports."

**Verdict: confirmed** — the stale premise, by reading the code and the
docstring. Whether the operator's named flip edge coincides with the geometric
one on real hardware remains **untested**.

This matters to the wizard's design and not only to its paperwork:

- `flip_axis` is a *measured fact about the machine* — which named edge the
  operator turns the stack about.
- `duplex_flip_edge(paper_pt)` is a *geometric fact about the job* — which edge
  is the sheet's vertical one.
- The backs need a half turn exactly when the two disagree.

So **"were the backs upright?" cannot be converted into `flip_axis` without
knowing which orientation the sheet was printed in.** An observation taken on
portrait alone yields the opposite `flip_axis` from the same observation on
landscape.

The printed half already handles this: `calibration_sheet.py` emits both a
portrait and a landscape file, question **G** asks which one is in the
operator's hand, and the cover closes with "The rule that turns these answers
into a profile inverts between the two, so one orientation answers only half of
it." The wizard must carry the orientation alongside the reading, and should
collect both orientations so the pair cross-checks.

If the operator's named edge and the geometric edge turn out not to coincide on
real hardware, that is an escalation about the field's *name*, not its value —
do not rename it here.

## What the printed sheet actually asks

Taken from `calibration_sheet.py:_COVER_BODY`, since the wizard's vocabulary
has to match the paper the operator is holding:

| | Question | Feeds |
|---|---|---|
| A | printed side in the output tray faced UP / DOWN | `output_face` |
| B | edge that went in first was the TOP / BOTTOM edge | `feed_edge` |
| C | sheet on top of the output stack was number 1/2/3/4 | `reverse_stack` |
| D | which BACK number landed on each FRONT | `reverse_stack` (cross-check of C) |
| E | band along TOP/BOTTOM edge, triangle points UP/DOWN | `flip_axis` |
| F | back cross sits _ mm left/right, _ mm up/down of the front cross | `back_offset_x_pt`, `back_offset_y_pt` |
| G | this file is the PORTRAIT / LANDSCAPE one | interprets E |

## Derived question count

**Four**, plus one orientation tag and one measurement.

- `output_face` — A, directly observed.
- `feed_edge` — B, directly observed.
- `stack_order` — C, with D as its cross-check. Kept as a question rather than
  derived from `(output_face, feed_edge)` until Finding B is confirmed.
- `back_orientation` — E, interpreted through G.

`G` is not a fifth question about the printer; it is the frame that makes E
readable, and the operator answers it once per file rather than deciding
anything. `F` is a ruler measurement feeding the back offsets, not a state-space
axis — it does not multiply the combinations and cannot be got wrong in a way
that selects the wrong state.

The four are **independently determinable**: A and B are read off the machine's
behaviour before any back pass exists, C/D off the stack order, and E off a
single finished sheet. No answer is needed to ask another, and no two answers
are read from the same feature of the sheet.

### The gate

The spec's gate is *stop and escalate at more than five questions, or if any
axis cannot be determined independently of another.*

**Four questions, all independently determinable. The gate passes; Phases 2–5
may proceed.**

One deviation from the F2 spec's own design is recorded rather than silently
adopted: it lists an answer vocabulary of four keys and no orientation. Because
of Finding C, `back_orientation` is meaningless without the orientation it was
observed in, so the answer record must carry `orientation` as a fifth field.
That is a widening of the *record*, not of the question count — the operator is
not being asked to judge anything extra, and the sheet already prints the
answer on its own cover.

## Escalation: none

Phase 1 completes without escalation. Recorded explicitly because the gate is a
decision point and "nothing happened" is a result the next reader needs stated,
not inferred from the absence of a section.

The two things that would change this are both **untested** and both land in
Phase 5, at a printer:

1. Finding B, if `reverse_stack` turns out not to be a function of
   `(output_face, feed_edge)` — then `profiles.py:61-74` is wrong.
2. Finding C's second half, if the operator's named flip edge does not coincide
   with the geometric one — then `flip_axis` is misnamed.

Neither blocks Phases 2–4, which build the pure half against the enumerated
answer space above.
