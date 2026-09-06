# F2 — Enumerate the calibration state space, then build the pure half

**Roadmap item:** `docs/ROADMAP.md` F2
**Depends on:** — (no code dependency). The roadmap's suggested order puts
this at step 9, *"only if F1 turns out not to be enough"*: F1 gives the model
a writer, and this gives it a measurer. Phase 5 needs the hardware F4 also
needs; do them in one session at the printer if you can.
**Blocks:** — (F3 is usable without it; the GUIDE's iterative procedure is
the fallback)
**Size:** L
**Decision needed first:** none to start. Phase 1 ends in a **stop-and-escalate
gate** that is an owner decision (see §3, step 1.4).

---

## 1. Context

`docs/specs/deckle-mvp/sub-spec-13-calibration-wizard.md` has never been
implemented, and it is explicit that it must not be implemented as written:

> **This is a spike before it is a feature, and it is marked `dispatch:
> manual` because it cannot be completed unattended.**
>
> **The unsupported claim to kill first.** The original design asserted "2–3
> questions" would derive the printer's behavior. That is not supported. The
> state space is *flip axis × output face × feed edge × stack order* — up to
> 16 combinations. Two or three binary questions resolve at most 4–8.
> **Enumerate first, then derive the question count.**
>
> **If the enumeration shows more than five questions are needed, or that the
> axes are not independently determinable — stop and escalate.**

Neither `docs/spikes/calibration-state-space.md` nor
`deckle/core/calibration.py` exists:

```
$ ls docs/spikes/
qprinter-capability-report.md
$ ls deckle/core/calibration.py
ls: cannot access 'deckle/core/calibration.py': No such file or directory
```

What a user pays for this today, from GUIDE §6:

> **Finding your two numbers.** The calibration wizard is not built, so this
> is currently iterative:
> 1. Print one sheet, both sides, with a ruler on each face …
> 3. Try that as `--back-offset`, reprint, and look again. If it got worse,
>    flip the sign …
> 4. Two or three rounds is normal.

and for the reload behaviour, nothing at all: they get whichever of the two
generic presets the app reaches (one, before F1) and find out it was the
wrong one after printing a stack.

**This spec is the spike, not the wizard.** Its deliverable order is: an
enumeration document, then two pure functions with an exhaustive table test,
then a PDF generator, then — last, and only once the first three are green —
a Qt wizard. The wizard is Phase 4 for a specific reason: the exhaustive
mapping test is only possible because `derive_profile` is pure, and a wizard
built first will pull the mapping into a widget where it cannot be tested
over its whole domain.

## 2. Current code

`deckle/core/profiles.py:30-52` — the four axes, as stored:

```python
@dataclass(frozen=True)
class PrinterProfile:
    version: int
    flip_axis: Literal["long", "short"]
    output_face: Literal["up", "down"]
    feed_edge: Literal["top", "bottom"]
    reverse_stack: bool
    imageable_area_pt: tuple[float, float, float, float]
    calibrated_at: str
    calibration_version: int

    back_offset_x_pt: float = 0.0
    back_offset_y_pt: float = 0.0
```

`deckle/core/profiles.py:33-42` makes a claim the enumeration must test:

```python
    """A printer's calibrated manual-duplex behavior, persisted as JSON.

    ``reverse_stack`` and ``flip_axis`` are the two behavioral axes that
    ``plan_passes`` consumes directly: ``reverse_stack`` (derived, at
    calibration time, from the combination of ``output_face`` and
    ``feed_edge`` -- a face-down output on a printer that does not
    re-invert the stack needs its back pass reversed to restore sheet
    order; a face-up output that preserves order does not) drives sheet
    order on the back pass, while ``flip_axis`` drives whether back sides
    need a 180-degree rotation.
    """
```

**"Derived from the combination of `output_face` and `feed_edge`" is the
16→8 collapse, asserted and never measured.** If it holds, `reverse_stack`
is not a question at all. If it does not, it is a question and the docstring
is wrong. Settling that is half of Phase 1's value.

`deckle/core/printing.py:134-165` — everything the profile actually drives:

```python
def plan_passes(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheets: Sequence[int] | None = None,
) -> list[PrintPass]:
    indices = [s.index for s in plan.sheets] if sheets is None else list(sheets)

    front_order = list(indices)
    back_order = list(reversed(indices)) if profile.reverse_stack else list(indices)

    return [
        PrintPass(
            index=0,
            sheet_order=front_order,
            side="front",
            reload_instruction=_front_instruction(profile),
            rotate_backs=False,
        ),
        PrintPass(
            index=1,
            sheet_order=back_order,
            side="back",
            reload_instruction=_back_instruction(profile),
            rotate_backs=profile.flip_axis == "long",
        ),
    ]
```

`deckle/core/printing.py:106-131` — `output_face` and `feed_edge` reach
**only** the instruction prose:

```python
def _axis_word(flip_axis: str) -> str:
    return "long" if flip_axis == "long" else "short"


def _face_word(output_face: str) -> str:
    return "face down" if output_face == "down" else "face up"


def _front_instruction(profile: PrinterProfile) -> str:
    return (
        f"Load paper {_face_word(profile.output_face)}, feed edge "
        f"{profile.feed_edge}, and print pass 1 (fronts)."
    )
```

That asymmetry is the single most useful fact in this spec: **two of the four
axes cost only wording when wrong; two cost paper.** Any question-count
budget should be spent on `reverse_stack` and `flip_axis`.

`deckle/core/dummy.py:44-56` — the drawing technique the calibration source
reuses, and why asymmetry is required:

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

`docs/spikes/qprinter-capability-report.md` records the one platform
divergence the wizard interacts with (Linux `supportedDuplexModes()`
under-reports until a job has been submitted) and its option 2:

> **Add a query-time warm-up** … or have the calibration wizard (SS-13)
> refresh `duplex_modes()` after a calibration print, so first-run detection
> is more reliable.

### Call sites and existing tests

```
$ grep -rn "calibration" deckle/ | grep -v "calibration_version\|calibrated_at"
deckle/core/profiles.py:9:Until calibration (SS-13) exists, ``BUILTIN_PRESETS`` supplies profiles
deckle/core/profiles.py:88:        holds: a calibration is not derived from anything, it comes from
deckle/core/profiles.py:129:        set is a profile asking for behaviour this build cannot produce,
deckle/app/views/print_dialog.py:68:    Until calibration (SS-13) exists, this is the only way a print run
deckle/cli.py:444:        f"{', '.join(sorted(BUILTIN_PRESETS))}. Calibrate a printer in the "
```

`deckle/cli.py:444` tells users to "Calibrate a printer in the desktop app to
save one under its own name" — a feature that does not exist. That is B21's
half of the sentence; F2 is what makes it true.

Tests that pin the fields F2 writes: `tests/test_printing.py` (profile
round trip, `plan_passes` ordering), `tests/test_schema.py`
(`check_values` rejecting an out-of-`Literal` value),
`tests/test_registration.py` (back offsets through the export path).

## 3. Change

Five phases. **Phases 2 and 3 must be green before Phase 4 starts**, and
Phase 4 must be complete before Phase 5. Phase 1 ends in a human gate.

### Phase 1 — the enumeration document (no code)

#### 1.1 Create `docs/spikes/calibration-state-space.md`

Front matter matching `docs/spikes/qprinter-capability-report.md`'s tone: a
title, what the spike is for, and a verdict vocabulary of
**confirmed / diverged / untested**.

#### 1.2 The full 16-state table

Write it out. Axis order `flip_axis × output_face × feed_edge ×
reverse_stack`, all 16 rows, no abbreviation — the point of the document is
that a future maintainer can see the whole domain:

| # | flip_axis | output_face | feed_edge | reverse_stack | Physically reachable? | Distinguishable from a printed sheet? |
|---|---|---|---|---|---|---|
| 1 | long | down | top | False | untested | |
| 2 | long | down | top | True | untested | ← `generic_face_down_reversed` |
| 3 | long | down | bottom | False | untested | |
| 4 | long | down | bottom | True | untested | |
| 5 | long | up | top | False | untested | |
| 6 | long | up | top | True | untested | |
| 7 | long | up | bottom | False | untested | |
| 8 | long | up | bottom | True | untested | |
| 9 | short | down | top | False | untested | |
| 10 | short | down | top | True | untested | |
| 11 | short | down | bottom | False | untested | |
| 12 | short | down | bottom | True | untested | |
| 13 | short | up | top | False | untested | |
| 14 | short | up | top | True | untested | |
| 15 | short | up | bottom | False | untested | ← `generic_face_up_in_order` |
| 16 | short | up | bottom | True | untested | |

#### 1.3 The collapse argument, stated as three findings

Each finding gets a heading, the evidence, and a verdict.

**Finding A — only two axes change what lands on paper.**
`plan_passes` reads `reverse_stack` and `flip_axis`. `output_face` and
`feed_edge` reach only `_front_instruction`/`_back_instruction`. Verdict:
**confirmed by reading `deckle/core/printing.py:106-165`** — this one needs
no hardware, and the acceptance table below greps for it. Consequence: 16
states collapse to **4 behaviourally distinct** ones, and the other two axes
are *reported observations*, not derivations. A wrong answer on them
mis-words an instruction; a wrong answer on the other two ruins a stack.

**Finding B — is `reverse_stack` a function of `(output_face, feed_edge)`?**
`profiles.py:33-42` asserts it is. Both shipped presets are consistent with
`down+top → True`, `up+bottom → False`, which is two data points fitting four
possible combinations. Verdict: **untested**. Phase 5 fills it in. If it
holds, `reverse_stack` is not a question and the count drops by one; if it
does not, the docstring at `profiles.py:36-40` is wrong and must be
corrected in the same commit.

**Finding C — `flip_axis` names an operator action but encodes a
correction.** `plan_passes` uses it for exactly one thing:
`rotate_backs=profile.flip_axis == "long"`. What the test sheet can observe
is "does the back need a half turn", not "which named edge did the operator
turn about". Verdict: **untested**; record whether the two coincide on the
test hardware. If they do not, that is an escalation about the field's
*name*, not its value — do not rename it here.

#### 1.4 State the derived question count, and the gate

The document must contain a heading `## Derived question count` and a number.
The design in Phase 3 below is **four questions**. Write the reasoning:
two directly-observed facts (`output_face`, `feed_edge`) plus two test-sheet
readings (`stack_order`, `back_orientation`), with `reverse_stack` kept as a
question rather than derived from Finding B until Finding B is confirmed.

**The gate:** if working through 1.2 and 1.3 lands on **more than five**
questions, or on any axis that cannot be determined independently of another,
**stop**. Do not implement Phases 2-5. Record the count, the reason, and the
combinations that are indistinguishable, and escalate to the owner — that
changes the UX and is a human decision, per MVP SS-13 Step 2.

#### 1.5 Commit the enumeration on its own

```bash
git add docs/spikes/calibration-state-space.md docs/decisions.md
git commit -m "docs(F2): calibration state-space enumeration"
```

One commit, before any code, so the table cannot be quietly reshaped to fit
whatever the implementation turned out to do.

### Phase 2 — `derive_profile`, pure, with an exhaustive test

#### 2.1 Create `deckle/core/calibration.py`

Module docstring: what the module is (the pure half of the calibration
wizard), what it must never import (Qt — `tests/test_core_purity.py`
enforces it), and the sentence that `derive_profile` is total over the
enumerated answer space so the exhaustive test in
`tests/test_calibration.py` can iterate it.

#### 2.2 The answer vocabulary — committed values

```python
ANSWER_KEYS: tuple[str, ...] = (
    "output_face",
    "feed_edge",
    "stack_order",
    "back_orientation",
)

ANSWER_VALUES: dict[str, tuple[str, ...]] = {
    "output_face": ("up", "down"),
    "feed_edge": ("top", "bottom"),
    "stack_order": ("same", "reversed"),
    "back_orientation": ("upright", "inverted"),
}
```

`stack_order` and `back_orientation` are named for **what the user reports**,
not for the profile fields they set. That separation is the whole reason
`derive_profile` is a function and not a dict literal: a report is an
observation, a profile field is a correction, and the mapping between them is
the thing being spiked.

#### 2.3 `derive_profile`

```python
def derive_profile(
    answers: Mapping[str, str],
    *,
    imageable_area_pt: tuple[float, float, float, float] = (18.0, 18.0, 18.0, 18.0),
    back_offset_pt: tuple[float, float] = (0.0, 0.0),
    calibrated_at: str = "",
) -> PrinterProfile:
    """The profile a set of calibration answers describes.

    Pure: no Qt, no I/O, no clock. ``calibrated_at`` is passed in rather
    than read from ``datetime.now`` so the exhaustive mapping test can
    compare whole profiles.

    :raises ValueError: a key is missing, unknown, or carries a value
        outside :data:`ANSWER_VALUES`. Guessing a missing answer is how a
        calibration silently becomes a preset.
    """
```

Body, in this order:

1. `missing = [k for k in ANSWER_KEYS if k not in answers]` → raise
   `ValueError(f"calibration answers are missing {', '.join(missing)}")`.
2. `unknown = sorted(set(answers) - set(ANSWER_KEYS))` → raise
   `ValueError(f"unknown calibration answer(s): {', '.join(unknown)}")`.
3. For each key, value not in `ANSWER_VALUES[key]` → raise
   `ValueError(f"calibration answer {key}={answers[key]!r} is not one of {', '.join(ANSWER_VALUES[key])}")`.
4. Return:

```python
    return PrinterProfile(
        version=1,
        flip_axis="long" if answers["back_orientation"] == "inverted" else "short",
        output_face=answers["output_face"],
        feed_edge=answers["feed_edge"],
        reverse_stack=answers["stack_order"] == "reversed",
        imageable_area_pt=imageable_area_pt,
        calibrated_at=calibrated_at,
        calibration_version=1,
    ).__class__(...)  # see note
```

Written plainly rather than with `dataclasses.replace`, and with
`back_offset_x_pt=back_offset_pt[0]`, `back_offset_y_pt=back_offset_pt[1]`.

`calibration_version=1` is the committed value and is what distinguishes a
**measured** profile from one typed into F1's editor, which writes `0`. Say
so in the docstring; it is the only thing that can tell them apart.

The two derivations, each with a one-line comment stating the physical
reason:

- `back_orientation == "inverted"` → the printer put the back upside down
  relative to the front, so Deckle must pre-rotate every back face →
  `flip_axis = "long"`, because `plan_passes` reads
  `rotate_backs = flip_axis == "long"`.
- `stack_order == "reversed"` → sheet 1's back landed on the last sheet
  through the printer, so the back pass must be fed in reverse →
  `reverse_stack = True`.

#### 2.4 The exhaustive test — write it before 2.3

`tests/test_calibration.py`, `test_every_answer_combination_maps_to_exactly_one_profile`:
iterate `itertools.product(*(ANSWER_VALUES[k] for k in ANSWER_KEYS))` — 16
combinations — call `derive_profile`, collect the results into a dict keyed
by the answer tuple, and assert:

- all 16 calls returned a `PrinterProfile` (none raised),
- the 16 resulting `(flip_axis, output_face, feed_edge, reverse_stack)`
  tuples are **all distinct** — 16 answers, 16 profiles, no collisions and no
  unreachable state,
- the set of those tuples equals the full cross product of the four
  `Literal` domains, i.e. every row of the Phase 1 table is producible.

That last assertion is what makes the table and the code one artefact instead
of two.

### Phase 3 — the test sheet

#### 3.1 `calibration_source_pdf(out_path: str, paper_pt=LETTER_PT) -> None`

In `deckle/core/calibration.py`. Writes an **8-page** PDF using the same
`pikepdf.canvas.ContentStreamBuilder` + base-14 Helvetica approach as
`deckle/core/dummy.py` (no embedded font, no licence surface), through
`deckle.core.paths.atomic_output` as `dummy.py` does.

Page content, exactly:

| PDF page | Sheet | Face | Big glyph, 144pt, centred | Word, 24pt, above the glyph | Corner glyph |
|---|---|---|---|---|---|
| 1 | 1 | front | `1` | `FRONT` | filled 24pt square, top-**left**, 36pt in |
| 2 | 1 | back | `A` | `BACK` | filled 24pt square, top-**left**, 36pt in |
| 3 | 2 | front | `2` | `FRONT` | same |
| 4 | 2 | back | `B` | `BACK` | same |
| 5 | 3 | front | `3` | `FRONT` | same |
| 6 | 3 | back | `C` | `BACK` | same |
| 7 | 4 | front | `4` | `FRONT` | same |
| 8 | 4 | back | `D` | `BACK` | same |

Plus, on every page, an **arrow to the head**: a vertical line from the glyph
up to 72pt below the top edge, with a chevron at its top. Three redundant
asymmetries — the letter/numeral, the word, and the corner square — because
MVP SS-13 Step 4 is explicit that *"a symmetric mark cannot distinguish a
180° rotation from no rotation, which is precisely the ambiguity being
resolved"*, and `dummy.py:44-56` records the same lesson learned twice.

**Numerals for fronts and letters for backs** so a user reading a finished
sheet never has to remember which side they are looking at. `4` and `A` are
not confusable; `4` and `4` would be.

#### 3.2 `build_test_plan(source_path, *, sha256="", paper_pt=LETTER_PT) -> SheetPlan`

Pure — no I/O — building four `Sheet`s from `SourceRef`s into
`source_path`, page indices 0..7, front face = even index, back face = odd:

```python
def build_test_plan(
    source_path: str,
    *,
    sha256: str = "",
    paper_pt: tuple[float, float] = LETTER_PT,
) -> SheetPlan:
    """A four-sheet plan over the calibration source, one page per face."""
```

Each face is `Side(pages=(OutputPage(source_ref=ref, placement=Placement(
scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0), is_filler=False),))`,
i.e. the source page at 1:1 at the paper origin. `SheetPlan(sheets=...,
paper_pt=paper_pt, warnings=[], signatures=())`.

**Chosen:** a real PDF source plus a plan over it, so the calibration print
travels the ordinary `export`/`QtPrintBackend` path and exercises it end to
end — which MVP SS-13's contract explicitly wants. **Rejected:** a
source-less plan drawing the pattern from `Mark` segments, which would avoid
the file but needs a new `Mark.kind` and produces tally marks where a
bench-readable numeral belongs.

**Deviation from MVP SS-13's frozen contract**, which names
`build_test_plan() -> SheetPlan` with no parameters: a plan with no
`source_ref` renders blank pages, so the zero-argument form cannot print
anything to read. Record the deviation in the decisions entry and add a line
to `docs/specs/deckle-mvp/sub-spec-13-calibration-wizard.md`'s *Interface
Contracts* section noting that F2 supersedes that shape.

#### 3.3 Docs

New `docs/api/core.calibration.rst` (copy `docs/api/core.dummy.rst`'s shape)
and an entry in `docs/api/core.rst`'s toctree, after `core.dummy`.
`tests/test_docs_coverage.py` fails without both.

### Phase 4 — the Qt wizard (only after 1-3 are green)

Create `deckle/app/views/calibration_wizard.py`. Qt imported lazily inside
functions, as every other module in `deckle/app/views` does.

`CalibrationWizard(printer_name, parent=None, *, backend_cls=None,
source_writer=calibration_source_pdf, plan_builder=build_test_plan,
deriver=derive_profile, saver=None, ask=None)` — every collaborator
injectable, so the flow is testable headlessly the way `PrintDialog` is.

Screens, in order, with these exact headings and question texts:

1. **"Print the front pass"** — writes the source to a temp file, builds the
   plan, submits `side="front"`, `rotate_backs=False`, `sheets=[0,1,2,3]`
   through `QtPrintBackend.submit`. Body text: *"Four numbered sheets will
   print. Take the stack out of the tray without reordering it."*
2. **"How did they come out?"** — two combos:
   - `"Printed side facing:"` → `Up` / `Down` → `output_face`
   - `"Edge that went in first:"` → `Top` / `Bottom` → `feed_edge`
   Body text must say: *"These two only change the wording of the reload
   instruction. If you are not sure, pick the one that matches how you
   normally load paper."*
3. **"Reload and print the back pass"** — body text: *"Put the stack back in
   the way you normally would for a two-sided job. Deckle is recording what
   your printer does with **your** reload habit, so do it exactly as you will
   do it for a real book."* Then submits `side="back"`, `rotate_backs=False`,
   `sheets=[0,1,2,3]` — **uncorrected on purpose**, so the questions observe
   the printer rather than Deckle's guess.
4. **"Read the finished sheets"** — two questions:
   - `"Find the sheet whose front says 1. What letter is on its back?"` →
     `A` / `B` / `C` / `D`. `A` → `stack_order="same"`; `D` →
     `stack_order="reversed"`; **`B` or `C` → do not derive a profile.** Show
     `"That is not a result Deckle's two-state model can explain. Nothing has been saved. Please report it with the four sheets in front of you."`
     and offer only Cancel. This is the escalation path, in the UI, and it
     must exist: a printer that reorders in some third way must not be
     silently rounded to one of two.
   - `"Hold that sheet with the front's arrow pointing up, and turn it over left-to-right like a page. Which way does the back's arrow point?"`
     → `Up` / `Down`. `Up` → `back_orientation="upright"`; `Down` →
     `back_orientation="inverted"`.
5. **"Measure the printable border"** — four spinboxes, `Left/Top/Right/Bottom`,
   for `imageable_area_pt`, seeded from the current profile via
   `resolve_profile`. Body text: *"Measure the unprinted border on any of the
   four sheets, on each edge."* This is MVP SS-13 Step 8 and B15's number.
6. **"Save"** — calls `derive_profile(answers,
   imageable_area_pt=..., calibrated_at=datetime.now().isoformat(timespec="seconds"))`
   and saves under `printer_name`. Then calls
   `backend.duplex_modes(printer_name)` once and logs the result via
   `log_event("duplex_modes_after_calibration", ...)` — this is
   `docs/spikes/qprinter-capability-report.md`'s escalation option 2, taken
   at the one moment a job has definitely been submitted to the queue.

Reachable from **F1's profile editor**: a `"Calibrate..."` button beside
Save, disabled when `printer_name` is empty. Not from the print dialog
directly — the profile editor is where profiles are made, and two entry
points to one wizard is the control duplication `layout_panel.py:754-759`
records.

`docs/api/app.views.calibration_wizard.rst` plus the `docs/api/app.rst`
toctree entry.

### Phase 5 — `[HUMAN]` run it on hardware

Not automatable. Run the wizard against the real printer, print a
multi-sheet document double-sided through the resulting profile, and confirm
it collates. Then fill in `docs/spikes/calibration-state-space.md`'s Finding
B and Finding C verdicts with what the hardware did, and record the run in
`docs/verification/2026-MM-DD-calibration.md` with the same
`confirmed`/`diverged`/`untested` vocabulary F4 uses. An agent must not fill
in a verdict.

## 4. Tests

New file `tests/test_calibration.py`. Pure — no Qt import, no display, no
temp printer.

1. `test_every_answer_combination_maps_to_exactly_one_profile`
   As specified in 2.4. Unfixed:
   `ModuleNotFoundError: No module named 'deckle.core.calibration'`.

2. `test_an_inverted_back_asks_for_the_rotation_plan_passes_applies`
   `derive_profile({... "back_orientation": "inverted" ...})`, then
   `plan_passes(plan, profile)[1].rotate_backs is True`. The assertion is in
   terms of the *behaviour*, not the field, so a future rename of `flip_axis`
   cannot make this test pass while the paper comes out wrong.

3. `test_an_upright_back_asks_for_no_rotation`
   Mirror of 2, `rotate_backs is False`.

4. `test_a_reversed_stack_reverses_the_back_pass_order`
   `stack_order="reversed"` →
   `plan_passes(plan, profile)[1].sheet_order == [3, 2, 1, 0]` on a 4-sheet
   plan; `"same"` → `[0, 1, 2, 3]`.

5. `test_a_missing_answer_is_refused_by_name`
   Drop `"feed_edge"`; `pytest.raises(ValueError, match="feed_edge")`.

6. `test_an_answer_outside_its_vocabulary_is_refused_and_names_the_options`
   `back_orientation="sideways"`; the message contains `upright` and
   `inverted`.

7. `test_an_unknown_answer_key_is_refused_rather_than_ignored`
   An extra `"paper": "letter"` raises. Tolerant-on-read is right for a
   *stored* file (`PrinterProfile.load`) and wrong for a *live* answer set: a
   key nobody consumes means a question was asked and dropped.

8. `test_a_derived_profile_is_marked_as_measured`
   `derive_profile(...).calibration_version == 1`, and a profile from F1's
   editor has `0`. Names the one distinction between typed and measured.

9. `test_a_derived_profile_round_trips_through_save_and_load`
   With the `XDG_CONFIG_HOME` + `deckle.core.paths.sys.platform`
   monkeypatch pair from `tests/test_printing.py:132-133`.

10. `test_the_calibration_source_has_eight_pages_and_no_embedded_font`
    `calibration_source_pdf(tmp_path/"cal.pdf")`; open with `pikepdf`,
    assert 8 pages, and that no page's `/Resources//Font` dictionary
    contains a `/FontFile`, `/FontFile2` or `/FontFile3` key. The base-14
    guarantee is a licence property, not a cosmetic one.

11. `test_the_test_plan_has_four_sheets_with_both_faces`
    `build_test_plan("cal.pdf")` → 4 sheets, every `front` and `back` a
    `Side` with exactly one page, page indices `0..7` in front/back order.

12. `test_the_test_plan_is_pure` — call it with a path that does not exist
    and assert it still returns a plan. Pins that `build_test_plan` does no
    I/O, which is what lets the wizard build a plan before writing the file.

13. `tests/test_core_purity.py` covers the "no Qt" requirement for the new
    core module automatically — it walks the package. No new test needed;
    confirm it picks the module up.

Phase 4 only, in `tests/test_calibration_wizard.py` (headless, the
`QT_QPA_PLATFORM=offscreen` + module-scoped `qapp` pattern from
`tests/test_print_dialog.py:19-37`):

14. `test_the_wizard_saves_the_profile_the_answers_describe`
    Drive with injected `ask` returning a scripted answer dict; assert the
    injected `saver` received `derive_profile(answers)`'s output.
15. `test_an_unexplainable_stack_result_saves_nothing`
    Answer `B` to the letter question; assert `saver` was never called and
    the shown message is the exact string in Phase 4 screen 4.
16. `test_the_back_pass_is_printed_uncorrected`
    Assert the recorded `submit` call for the back pass had
    `rotate_backs=False` and `sheets=[0, 1, 2, 3]`. If the wizard applies its
    own guess, the questions measure Deckle, not the printer.

## 5. Acceptance

| Check | Command |
|---|---|
| The enumeration exists | `test -f docs/spikes/calibration-state-space.md` |
| It covers all four axes | `for a in flip_axis output_face feed_edge reverse_stack; do grep -q "$a" docs/spikes/calibration-state-space.md \|\| { echo "FAIL: axis $a not enumerated"; exit 1; }; done` |
| It contains all 16 rows | `test "$(grep -cE '^\| *[0-9]+ *\| *(long\|short) *\|' docs/spikes/calibration-state-space.md)" = "16"` |
| It states a question count | `grep -qi "Derived question count" docs/spikes/calibration-state-space.md` |
| The count is at or below five | `[HUMAN] read the "Derived question count" section; more than five is a stop-and-escalate, not a bigger wizard` |
| Finding A is checkable without hardware | `grep -c "output_face\|feed_edge" deckle/core/printing.py` — every hit must be inside `_front_instruction`/`_back_instruction`/`_face_word` |
| `build_test_plan` exposed | `grep -q "def build_test_plan" deckle/core/calibration.py` |
| `derive_profile` exposed and pure | `grep -q "def derive_profile" deckle/core/calibration.py && ! grep -q "PySide6" deckle/core/calibration.py` |
| Calibration tests pass | `.venv/bin/python -m pytest -q tests/test_calibration.py` |
| The exhaustive mapping specifically | `.venv/bin/python -m pytest -q -k every_answer_combination_maps` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| New modules documented | `test -f docs/api/core.calibration.rst && grep -q core.calibration docs/api/core.rst` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| Phase 4 only: wizard exists | `test -f deckle/app/views/calibration_wizard.py && grep -q "imageable_area" deckle/app/views/calibration_wizard.py` |
| Phase 4 only: wizard tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_calibration_wizard.py` |
| [HUMAN] Phase 5 | Calibrate the real printer, print a 16-page document double-sided through the saved profile, confirm it collates and no back is upside down. Fill in Findings B and C. |

Greps run against the current tree:

```
$ ls docs/spikes/
qprinter-capability-report.md
$ grep -rn "def derive_profile\|def build_test_plan" deckle/
(no output — exit 1)
```

## 6. Out of scope

- **F3** — the graduated registration target. F2's wizard screen 5 captures
  the *imageable area*; the *back offset* is still typed. F3 is what makes
  the offset readable; the two meet in F1's editor.
- **B15 / N2** — pushing the resolved profile to the preview, and reading the
  driver's printable rect. The wizard writes `imageable_area_pt` from a
  ruler; nothing else changes about who reads it.
- **F9** — wiring hardware duplex. Phase 4 screen 6 *logs* `duplex_modes` after
  a calibration print (the QPrinter spike's option 2); it does not act on it.
- **B21** — `cli.py:444`'s "Calibrate a printer in the desktop app" message
  becomes true here, but fixing the CLI's `_resolve_profile` error handling is
  B21's.
- **Renaming `flip_axis`** even if Finding C shows the name over-claims. Record
  it; a field rename is a `.deckle`-adjacent migration (see F12).
- **Per-orientation imageable area** — v2 open question 3, roadmap F12.

## 7. decisions.md entry

```
## 2026-09-05 — Enumerated the calibration state space before building the wizard
- Symptom: MVP SS-13 has sat unimplemented since 2026-08-04 with an explicit instruction not to implement it as written: the original "2-3 questions" claim was unsupported against a 16-state space, and the spec demands an enumeration first with a stop-and-escalate at more than five questions.
- Fix: `docs/spikes/calibration-state-space.md` enumerates all 16 combinations of flip_axis x output_face x feed_edge x reverse_stack. The collapse is that only two of the four axes reach paper -- `plan_passes` reads `reverse_stack` and `flip_axis`; `output_face` and `feed_edge` reach only the reload instruction's wording. That makes four questions: two observed at the tray, two read off a four-sheet test print. `deckle/core/calibration.py` carries `derive_profile` (pure, total over the answer space, exhaustively tested over all 16) and `build_test_plan`; the Qt wizard came last, after the pure half was green.
- Surfaces: `profiles.py` asserts `reverse_stack` is "derived, at calibration time, from the combination of `output_face` and `feed_edge`". Two shipped presets fit that claim; two data points do not confirm a four-way mapping. It is recorded as Finding B, untested, and the hardware run settles it.
- Watch: `flip_axis` names an operator action and encodes a correction -- `plan_passes` uses it only for `rotate_backs`. The test sheet can observe the correction, not the action. A field whose name describes the mechanism rather than the decision is the shape the `maximize_gutter` entry already caught.
- Commit: <fill in>
```

## 8. Traps

- **Do not build the wizard first.** The exhaustive test exists only because
  `derive_profile` is pure; a mapping that starts life inside a `QWizardPage`
  cannot be iterated over its domain and will never be exhaustively tested.
- **The back pass in screen 3 must be printed uncorrected.** Submitting with
  `rotate_backs` from a guessed profile means the questions measure Deckle's
  guess. Test 16 pins this.
- **`derive_profile` must not read the clock.** `calibrated_at` is a
  parameter. A `datetime.now()` inside it makes whole-profile equality
  assertions impossible and drags a non-determinism into a pure module.
- **`PrinterProfile` values are validated on load** by
  `deckle.core.schema.check_values` against the `Literal` annotations. A
  derivation that emits `"Up"` instead of `"up"` saves fine and then fails to
  load — write the lowercase values, and let the combo carry the capitalised
  label with `addItem(label, value)`.
- **`tests/test_core_purity.py` walks every module under `deckle.core`.** A
  stray `from PySide6...` at module scope in `calibration.py` fails it.
- **`tests/test_docs_coverage.py`** fails on a new module with no `.rst`.
- **Base-14 fonts only** in the test sheet. `tests/test_license_audit.py` and
  `test_packaging_audit.py` walk the dependency closure; an embedded font is
  a licence surface `dummy.py` deliberately avoided.
- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for scripted work.
- **`QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit
  127)** — hardening plan H-3. Construct wizard pages and drive them; never
  show them in a test.
- Linux `supportedDuplexModes()` under-reports until a job has been submitted
  (`docs/spikes/qprinter-capability-report.md`). Screen 6 queries it *after*
  two passes have gone through the queue, which is the one moment it is
  reliable. Do not move that query earlier.
