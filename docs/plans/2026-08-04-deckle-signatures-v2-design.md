---
date: 2026-08-04
topic: "Deckle v2 — signature imposition: splitting, folio saddle-stitch fold order, 2-up placement, and bindery marks"
author: Caleb Bennett
status: evaluated
evaluated_date: 2026-08-04
supersedes: null
builds_on: "docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md"
repo: https://github.com/Caleb68864/Deckle
license: MIT
tags:
  - design
  - deckle
  - v2
  - imposition
  - signatures
  - saddle-stitch
  - bookbinding
---

# Deckle v2 — Signature Imposition

## Summary

The MVP ships one source page per physical side of paper with an alternating binding
gutter. That is the right layout for a 3-hole punch and the wrong layout for a sewn book.
This design adds **signature imposition**: split a 266-page book into a sequence of
short, foldable gatherings; place two pages side by side on each side of a landscape
sheet; order them so that folding and nesting the stack reproduces the reading order;
and print bindery marks — sewing stations and signature order marks — directly onto the
fold.

The whole feature enters through the existing `LayoutStrategy` seam. `impose(pages,
settings) -> SheetPlan` does not change. A new `SaddleStitchStrategy` sits beside
`GutterShiftStrategy` in `deckle/core/layout.py`, and the manual-duplex machinery
(`plan_passes`, `PrintSession`, `PrinterProfile`, the calibration wizard) is **not
touched at all** — signature imposition decides *what lands on a side*, and the
`PassPlanner` already owns *the order sides are fed*. Those two concerns were correctly
separated in the MVP and this design's central claim is that the separation holds.

One model change is required and it is a real one: a sheet side now carries **two** pages,
not one. `Sheet.front` / `Sheet.back` become a `Side` object holding a tuple of
`OutputPage` plus its bindery marks.

Concretely, for the Traveller Core Rulebook (266 pages, the fixture already used
throughout `docs/decisions.md`): folio at 4 sheets per signature yields **16 signatures of
4 sheets plus one of 3 — 67 sheets, 2 blanks** — against the MVP's 133 sheets. Half the
paper, and a book you can actually sew.

## Approach

**Selected: a second `LayoutStrategy` implementation, plus a `Side` type in the model,
plus a pure `signatures` module for the fold/split arithmetic.**

Four decisions define the shape:

**1. Folio only. Quarto and deeper are deferred, explicitly.**
A folio fold — one fold, two pages per sheet side, four pages per sheet — needs no page
rotation, no 2D cell grid, and no general fold-sequence machinery. Quarto (2×2), octavo
(4×2) and sextodecimo (4×4) need all three. This is not timidity: HornPenguin's *general*
imposition algorithm is [open issue #1 since September 2022](https://github.com/HornPenguin/Booklet/issues/1),
and its own `TODO` calls arbitrary fold sequences "difficult to complete on schedule" and
cites map-folding papers. The one project that tried to generalise this never finished it.
Folio is the fold the user's Singer 111w101 and hand-sewing workflow actually use, and it
is the fold where `Placement.rotate_deg` stays `0` — so the export path's `_rotation_matrix`
branch stays out of the signature code path entirely.

**2. One `SheetPlan` for the whole book, with a `signatures` index — not one plan per
signature.**
Bookbinder JS emits `_side1.pdf` / `_side2.pdf` files *per signature*
(`[[Bookbinder JS - Manual Duplex Workflow]]`). Deckle gets the same capability for free
without splitting the plan, because `export(plan, path, sheets=[...])`,
`plan_passes(plan, profile, sheets=[...])` and `PrintSession(..., sheets=[...])` all
already accept a sheet subset — and `docs/specs/2026-08-04-deckle-mvp.md` SS-11 makes the
subset path *the normal path with a smaller input, not a separate branch*. Printing one
signature at a time is therefore a UI affordance over machinery that already exists and is
already tested. Splitting the plan would fork the preview, the export cache
(`export_sheet_cached` keys on `_plan_hash`), and the resumable session identity.

**3. Bindery marks are computed as pure geometry in `deckle.core`, drawn in
`deckle/core/export.py`.**
`deckle/core/layout.py` is I/O-free and a `[MECHANICAL]` check enforces it. A new
`deckle/core/marks.py` returns `Mark` value objects in sheet points; `export.py` renders
them with `pikepdf.canvas`'s `ContentStreamBuilder`. Marks are then testable as numbers
and, because the preview rasterizes the exported artifact (SS-05), they are *visible in
preview by construction*. Bookbinder JS carries an open bug — [#135, sig order marks wrong
under page rotation](https://github.com/momijizukamori/bookbinder-js/issues/135) — that this
architecture makes very hard to have.

**4. Write the imposition math. Do not vendor HornPenguin.** Reasoning in *Approaches
Considered*; this reverses the "Approved reuse" row in the MVP design and Open Question 6,
so it is an escalation, not a free choice.

## Architecture

The four-level page model is unchanged. Signatures are a **grouping over sheets**, a fifth
noun that sits beside the existing four rather than displacing any of them:

```
source pages ──▶ output pages ──▶ SIDES ──▶ sheets ──▶ signatures ──▶ print passes
                                  (new)                 (new, a view over sheets)
```

```
┌──────────────────────────────────────────────────────────────────────┐
│  deckle.core.layout                                                  │
│                                                                       │
│    LayoutStrategy (Protocol)  ── UNCHANGED SIGNATURE ──               │
│      ├── GutterShiftStrategy      (MVP, 1-up)                         │
│      └── SaddleStitchStrategy     (v2, folio 2-up)  ◀── new           │
│                                                                       │
│    shared per-leaf placement math, generalised to a CELL:             │
│      document_scale(pages, settings, cell)                            │
│      content_box_rect_pt(settings, cell)                              │
│      actual_margins_pt(output_page, cell, spine)                      │
└───────────┬──────────────────────────────┬───────────────────────────┘
            │                              │
┌───────────▼──────────┐      ┌────────────▼─────────────┐
│ deckle.core.signatures│      │ deckle.core.marks        │  ◀── new
│  split_signatures()   │      │  sewing_stations()       │
│  saddle_order()       │      │  signature_order_mark()  │
│  fold_reading_order() │      │  fold_line()             │
│  (pure arithmetic)    │      │  (pure geometry -> Mark) │
└───────────┬──────────┘      └────────────┬─────────────┘
            │                              │
            └──────────────┬───────────────┘
                           ▼
                    ┌─────────────┐
                    │  SheetPlan  │  sheets[] + signatures[] + paper_pt + warnings
                    └──────┬──────┘
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
   export.export()   render_sheet()     plan_passes()   ◀── UNCHANGED
   (iterates Side)   (via export)       (reads .index only)
```

### The seam holds — here is the proof

`deckle/core/printing.py::plan_passes` reads exactly one thing off a `Sheet`:
`s.index`. It never inspects `front` or `back`. Therefore adding pages to a side, adding
marks to a side, and grouping sheets into signatures are all invisible to the manual-duplex
brain. `PrintPass`, `rotate_backs`, `reverse_stack`, `PrinterProfile` and the SS-13
calibration state space are untouched by this design. That is the single most valuable
property of the MVP's decomposition and this design spends none of it.

The one composition rule worth stating explicitly: **signatures must occupy contiguous
runs of sheet indices**, in binding order. `plan_passes` reverses the whole sheet list when
`profile.reverse_stack` is true; contiguity is what makes a per-signature subset
(`sheets=signature.sheet_indices`) reverse correctly in isolation as well as in aggregate.

## Components

### `deckle/core/models.py` — the model changes

**`Side`** — new. The thing a sheet has two of.

```python
@dataclass(frozen=True)
class Side:
    pages: tuple[OutputPage, ...]
    marks: tuple[Mark, ...] = ()
```

**`Sheet`** — changed. `front: Side | None`, `back: Side | None`.

> **This is a deliberate breaking change, and it is cheap for one specific reason:
> `SheetPlan` is never persisted.** `deckle/core/project_io.py` writes `pages`, `layout`
> and `printer` — the plan is recomputed on every load. So there is no file-format
> migration, no `version` bump, no compatibility window: only a mechanical refactor of
> `export._sides`, `render`, `preview_view`, and the layout tests.
>
> The rejected alternative was keeping `front: OutputPage | None` and adding a parallel
> `front_extra: tuple[OutputPage, ...]`. `docs/decisions.md` (2026-08-04, *slack_to
> replaces the maximize_gutter boolean*) is the argument against it: a shape that cannot
> express the real question makes one third of the answer space unreachable, and the
> workaround hides the gap. A side holds N pages. Say N.

**`Mark`** — new, pure geometry, no drawing.

```python
@dataclass(frozen=True)
class Mark:
    kind: Literal["sewing_station", "sig_order", "fold_line", "trim"]
    x0: float; y0: float; x1: float; y1: float      # sheet points, bottom-left origin
    filled: bool = False
    line_width_pt: float = 0.5
```

**`Signature`** — new. A view over sheets, not a container of them.

```python
@dataclass(frozen=True)
class Signature:
    index: int
    sheet_indices: tuple[int, ...]          # contiguous, in binding order
    source_page_count: int                  # real content pages
    blank_count: int
```

**`SheetPlan`** — gains `signatures: list[Signature]`. `GutterShiftStrategy` returns a
single `Signature` spanning every sheet, so the field is never empty and consumers never
branch on `if signatures`.

**`LayoutWarning.kind`** — gains `"sheet_orientation"`, `"signature_padding"`, and
`"creep_advisory"`.

**`LayoutSettings`** — new fields, all defaulted so the MVP's behaviour and its 223 tests
are unchanged when they are left alone:

| Field | Default | Meaning |
|---|---|---|
| `fold_scheme: Literal["none","folio_saddle"]` | `"none"` | `"none"` selects `GutterShiftStrategy` |
| `sheets_per_signature: int` | `4` | The bookbinder's number. 4–6 is the sweet spot; see *creep* below |
| `signature_pattern: tuple[int, ...] \| None` | `None` | Explicit per-signature sheet counts, e.g. `(4,4,3,4)`. Overrides the above |
| `blank_mode: Literal["end","balanced"]` | `"end"` | Where padding blanks land |
| `sewing_marks: bool` | `False` | |
| `sewing_stations: int` | `3` | Pamphlet stitch. 5 for a taller book |
| `sewing_margin_pt: float` | `36.0` | Head/tail inset to the first station (Bookbinder JS's "(A) Margin") |
| `sewing_tape_width_pt: float` | `0.0` | `>0` splits each station into a pair straddling a tape |
| `sig_order_marks: bool` | `False` | The spine staircase |
| `fold_lines: bool` | `False` | Dashed centreline |
| `paper_thickness_pt: float` | `0.27` | ~20 lb bond. **Advisory only — never geometry.** See *creep* |

The field surface deliberately mirrors HornPenguin's `--sig-composition [sheets] [inserts]`
and `--blank-mode` and Bookbinder JS's sewing-mark trio. Copying an option vocabulary that
was designed by people who bind books is not copying code, and it is the part of
HornPenguin genuinely worth having.

### `deckle/core/signatures.py` — the arithmetic, pure and I/O-free

**`split_signatures(page_count, settings) -> list[SignatureSpec]`**
The capability that only Bookbinder JS and pdfimpose have. Stirling PDF and PDF Arranger
impose the whole document as one signature — verified by reading
`BookletImpositionController.java` and `generate_booklet` respectively
(`[[Bookbinding Toolchain Comparison]]`) — which makes a 300-page book one 75-sheet fold.
That is not a limitation, it is a physically impossible output, and it is the reason this
function exists.

Algorithm, `blank_mode="end"`:

```
pages_per_sig = 4 * sheets_per_signature
full, remainder = divmod(page_count, pages_per_sig)
if remainder: last = ceil4(remainder)          # round up to a multiple of 4
sizes = [sheets_per_signature] * full + ([last // 4] if remainder else [])
```

Traveller, 266 pages, 4 sheets per signature (16 pages): `divmod(266, 16) = (16, 10)`,
`ceil4(10) = 12`, so **16 signatures of 4 sheets plus one of 3 — 67 sheets, 268 slots,
2 blanks.** `blank_mode="balanced"` instead shaves the remainder off the tail signatures so
no gathering is more than one sheet thinner than its neighbours, which matters when the
spine has to look even.

Invariants asserted in `impose` before returning, following the SS-03 precedent of
verifying internal consistency rather than trusting the loop:
- every signature's slot count is a multiple of 4;
- signature slot counts sum to the padded page count;
- concatenating the signatures' source slices reproduces the padded page list *in order*;
- `sheet_indices` are contiguous across the whole plan with no gaps and no overlaps.

Padding emits one `signature_padding` warning naming the count and location — the MVP's
predecessor-script defect 2 was silent double-padding, and the lesson recorded there is
that parity must be a *stated* property of the plan.

**`saddle_order(n) -> list[int]`**
The fold order for one nested gathering of `n` slots (`n % 4 == 0`). This is the executed,
verified function from `[[pikepdf - Imposition and Signature Recipe]]`, which ran end to end
against pikepdf 10.11.0 / libqpdf 12.3.2 on this machine:

```python
def saddle_order(n):
    seq, lo, hi = [], 0, n - 1
    while lo < hi:
        seq += [hi, lo, lo + 1, hi - 1]
        lo += 2
        hi -= 2
    return seq
```

For `n = 8`: `[7, 0, 1, 6, 5, 2, 3, 4]`. Read two at a time — sheet 1 front is
`(p8, p1)`, sheet 1 back is `(p2, p7)`, sheet 2 front `(p6, p3)`, sheet 2 back `(p4, p5)`.
The outermost sheet carries the first and last pages, which is the defining property of a
**nested** (saddle) gathering as opposed to a **stacked** one
(`[[pdfimpose - The Seven Schemas]]`: *"If you nest sheets that were imposed for stacking,
the page order comes out scrambled."*).

The vault note is explicit that this half is **not** verified: *"`saddle_order` is my own
function, not pikepdf's... Validate the fold order against a physical folded dummy before
trusting it for a real print run."* The pikepdf mechanics are confirmed; the bindery
arithmetic is not. That is why the physical dummy in *Testing* is a gate, not a nicety.

**`fold_reading_order(plan) -> list[int | None]`** — a **fold simulator**. Pure function:
walk each signature's sheets outermost-to-innermost, unfold each into its four leaves in
the order a reader would encounter them, concatenate across signatures, and return the
source page indices. This is the test oracle we own, and it is the single highest-value
test in the feature — see *Testing*.

### `deckle/core/layout.py` — `SaddleStitchStrategy`

Implements `LayoutStrategy` unchanged: `impose(self, pages, settings) -> SheetPlan`. All
configuration arrives via `settings`, per the committed contract in
`docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md`.

```
impose(pages, settings):
    active   = [p for p in pages if not p.skipped]
    specs    = split_signatures(len(active), settings)
    slots    = pad(active, total_slots(specs))        # exactly one padding pass
    cells    = cell_geometry(settings.paper)          # two cells, split at the fold
    scale    = document_scale(active, settings, cells[0])   # ONE scale, whole document
    for sig in specs:
        order = saddle_order(sig.slot_count)
        for each consecutive pair in order:
            build Side(pages=(place(left_slot, cells[0]),
                              place(right_slot, cells[1])),
                       marks=marks_for(sheet, sig, settings))
```

**Cell geometry.** The paper is the *sheet*; the fold is its vertical centreline. For
letter landscape (792 × 612) the cells are `(0,0,396,612)` and `(396,0,792,612)`.

**The spine side inverts, and this is the trap.** In the MVP, `_gutter_side_is_left`
derives the gutter side from output-page parity — index 0 is a recto, spine left, so push
right. Under folio that rule is **wrong and must not be reused**: the spine of a leaf is
determined by where its cell sits relative to the fold, not by its parity.

```
left cell   → spine on its RIGHT edge   (the fold)
right cell  → spine on its LEFT edge    (the fold)
```

This holds on both the front and the back of the sheet. Consequently `settings.binding_edge`
**changes meaning** under `fold_scheme="folio_saddle"`: it no longer selects which side of a
page gets the gutter, it selects reading direction — `"left"` = left-bound / LTR, `"right"`
= right-bound / RTL, which mirrors which source slot goes in which cell. Naming a field
whose meaning is strategy-dependent is a wart; the alternative (a second field meaning
almost the same thing) is the `maximize_gutter` mistake again. Document it at the field and
assert it in tests.

**Generalise, do not duplicate.** `content_box_size`, `content_box_rect_pt`,
`actual_margins_pt` and `document_scale` all currently assume the content box is the whole
sheet. Each gains a cell parameter; `GutterShiftStrategy` passes a single full-sheet cell
and its behaviour is byte-identical. Everything the MVP earned inside those functions —
the four-margin model, `slack_to`, the one-rule-for-both-axes placement from the
2026-08-04 *Rebuilt the placement math* entry, the fore-edge/gutter distinction — then
applies **per leaf**, which is exactly where it belongs. Two strategies must never contain
two copies of the placement rule; that is how the verso-only bug in the decision log
survived its tests.

**One document-wide scale, still.** `document_scale` is computed once against the *cell*
box and applied to every leaf on every sheet. All cells are identical under folio, so this
is a strictly smaller change than it looks. Non-negotiable per `docs/decisions.md`
(*Uniform document-wide scale; per-page scaling resized the text*): per-page scaling made
the Traveller cover's text 2.5% larger than the body's.

**Orientation.** A folio fold on a *portrait* sheet produces two tall, narrow cells. That
is legal and almost never intended. Emit a `sheet_orientation` warning; do **not** rotate
the paper automatically and do **not** block. The MVP's disambiguation rule is *permissive
— never reject a document*, and `docs/decisions.md` (*Path-traversal validation must
advise, not refuse*) records what happens when a warning is implemented as a refusal.

### `deckle/core/marks.py` — the bindery payoff

Bookbinder JS's genuinely valuable idea, and the cheapest large win in this design
(`[[Bookbinder JS - PDF Markup and Sewing Marks]]`).

**`sewing_stations(sheet_h, settings) -> tuple[Mark, ...]`**
`n` short ticks crossing the fold line, evenly spaced between `sewing_margin_pt` from the
head and the same from the tail. With `sewing_tape_width_pt > 0`, each station becomes a
*pair* straddling the tape, for sewing over tapes or cords. This replaces measuring and
pricking each gathering against a jig, which is the most tedious and error-prone step in
hand binding.

*Placement recommendation, flagged for physical verification:* print the stations on the
**innermost sheet of each signature, on its inner side** — the surface that faces you when
the folded gathering lies open, which is where the awl actually goes in. An option to print
them on every folio exists in Bookbinder JS and should exist here, but the default should
be the one that matches the motion.

**`signature_order_mark(sig_index, sig_count, sheet_h, settings) -> Mark`**
A short filled bar on the spine fold, stepped down by signature index, so that a correctly
collated stack of folded gatherings shows a clean diagonal staircase down the spine and a
misordered one is instantly visible before a single stitch. Traditional bindery technique,
reproduced faithfully.

```
step = (sheet_h - 2*margin - bar_h) / max(1, sig_count - 1)
y    = margin + sig_index * step
```

Printed on the **outermost** sheet of each signature — that is the surface that becomes the
visible spine of the folded gathering.

**`fold_line(...)`** — a light dashed line down the cell boundary. Trivial, and it makes
the fold reproducible without a bone folder against a ruler.

**Rendering.** `export.py` draws marks with `pikepdf.canvas`'s `ContentStreamBuilder`
(`append_rectangle`, `stroke_and_close`, `line_width`, `dashes`). Per
`[[pikepdf - Canvas API]]`, `Canvas` is *"the right tool for crop marks, fold lines,
registration marks, signature numbers and page folios"* — vector content, no file-size
penalty. Two traps from that note apply: colours are `pikepdf.canvas.Color`, never tuples,
and `Canvas.draw_image` is never used here (12.6× size blowup; irrelevant for vectors but
the temptation exists for a logo). Text is avoided entirely — every mark is a line or a
rectangle, so pikepdf's *"rudimentary interface. You've been warned"* text engine is never
in the path, and `reportlab` stays out of the dependency set.

### `deckle/core/export.py` — a smaller change than expected

`_place_output_page` already takes an arbitrary `Placement` in sheet coordinates and emits
`calc_form_xobject_placement` with `invert_transformations=True` and minimal
shrink/expand flags. **It needs no change for N-up.** The only edits:

1. `_sides(sheet) -> list[Side]` instead of `list[OutputPage]`.
2. The assembly loop creates **one** blank page per `Side` and iterates `side.pages`,
   placing each into its own cell rect — the vault recipe's exact structure, where two
   `place_exact` calls land on one `add_blank_page`.
3. After the pages, draw `side.marks`.

The verified output to expect per sheet side is two balanced `q ... Q` blocks with pure
translations — `[[pikepdf - Form XObject Placement]]` records
`1 0 0 1 0 0 cm` and `1 0 0 1 396 0 cm` for exactly this 2-up-on-landscape-letter case,
with zero scale drift. When the document scale is not 1.0, `_scale_flags_for` grants
shrink or expand in the single needed direction and the matrix carries that one scale — the
same behaviour the MVP already ships and the same one `docs/decisions.md` verified on the
real book (`1.06534 0 0 1.06534 54 38.045 cm`).

Constraints carried forward unchanged: never `add_overlay` (it centres and best-fits),
never reorder a `pikepdf.Pdf.pages` list by tuple-swap or slice assignment,
`remove_unreferenced_resources()` before save, batched save-and-reopen at
`_BATCH_SHEETS`.

### `deckle/app` — UI surface

- **`layout_panel.py`** gains a *Binding* group: fold scheme, sheets per signature (with a
  custom-pattern text field, Bookbinder JS's escape hatch for page counts that are never a
  tidy multiple of 32), blank mode, and a marks sub-panel. It gains a live readout —
  *"17 signatures · 67 sheets · 2 blanks"* — because that arithmetic is the thing a binder
  actually decides on.
- **`preview_view.py`** already toggles Front / Back / Both. Under folio it additionally
  labels each cell with its source page number and shows the signature index, so a
  mis-ordered gathering is visible on screen and not only on paper. The content-box guide
  (`content_box_rect_pt`) is drawn **per cell**.
- **`print_dialog.py`** gains a signature selector that populates the existing
  `sheets=` argument. No new print logic — the dialog's `[MECHANICAL]` check forbidding
  `reverse`, `sheet_order` and `% 2` still holds.

## Data Flow

```
 pages (list[SourcePage])          LayoutSettings{fold_scheme, sheets_per_signature, marks…}
        │                                       │
        └───────────────┬───────────────────────┘
                        ▼
              SaddleStitchStrategy.impose
                        │
        ┌───────────────┼────────────────┬──────────────────┐
        ▼               ▼                ▼                  ▼
  split_signatures  pad-to-slots   cell_geometry     document_scale
   (17 specs)       (268 slots)    (2 cells)         (ONE factor)
        │                                                   │
        └──────────────┬────────────────────────────────────┘
                       ▼
              per signature: saddle_order(n)
                       │
                       ▼          per leaf: existing per-cell placement math
              Side(pages=(L,R), marks=…)          (slack_to, 4 margins, one rule/axis)
                       │
                       ▼
      SheetPlan{ sheets[67], signatures[17], paper_pt=(792,612), warnings }
                       │
   ┌───────────────────┼────────────────────────┬────────────────────┐
   ▼                   ▼                        ▼                    ▼
export()          export_sheet_cached()    plan_passes()      fold_reading_order()
 (2 XObjects       → render_sheet()        UNCHANGED —         (test oracle,
  + marks per       → preview               reads .index        never shipped
  PDF page)                                 only               in a UI path)
                                                │
                                                ▼
                                        PrintSession → QtPrintBackend
```

### The invariant that has to hold

`fold_reading_order(impose(pages, settings)) == list(range(len(pages))) + [None] * blanks`

Fold the plan back up and you get the book. Everything else in this design is in service of
that one line.

## Error Handling

New failure modes, and what Deckle does about each.

| Condition | Tier | Response |
|---|---|---|
| Page count not a multiple of `4 × sheets_per_signature` | **Info** | Pad to the next multiple of 4 in the final signature. One `signature_padding` warning naming the count. Never two padding passes — the predecessor script's defect 2 |
| `sheets_per_signature` so large the gathering cannot be folded (`> 8` at 20 lb) | **Warning** | Advise, attached to the first sheet of each affected signature. Do not block; heavy paper and thin paper differ |
| Portrait paper under folio | **Warning** | `sheet_orientation`. The output is legal, just probably not wanted |
| Custom pattern's sheet counts do not sum to cover the document | **Blocking** | Named, with the shortfall. This is a typed-in value that cannot be guessed at |
| A signature would contain zero sheets | **Blocking** | Reject the pattern; it is a typo, not a layout |
| Content clipped inside its cell | **Warning** | Existing `clipped_by_page`, now measured against the **cell**, not the sheet. Halving the cell width doubles how often this fires — expected, and the honest report |
| Cell falls outside the printer's imageable area | **Warning** | Existing `clipped_by_imageable_area`. Landscape feed has a different imageable area than portrait on most printers; the profile must be consulted with the *actual* orientation |
| Predicted creep exceeds ~0.5 mm | **Info** | `creep_advisory`: *"4 sheets × 0.27 pt ≈ 0.5 mm of creep at the fore-edge. Reduce to 3 sheets per signature to remove it."* — see below |

Every warning carries a `sheet_index` and, in the UI, the signature label. The MVP's
governing rule stands: warnings attach to the sheet they affect, never a global modal.

### Creep: measured and reported, never compensated

**Deckle v2 ships no creep compensation, and this is a decision, not an omission.** Creep
is absent from every tool surveyed and *broken where claimed* — pdfimpose's own help text
reads *"⚠ Warning ⚠ This option is broken"*
([issue #36](https://framagit.org/spalax/pdfimpose/-/issues/36)); Stirling's
[#705](https://github.com/Stirling-Tools/Stirling-PDF/issues/705) has been open since
January 2024. At 4–6 sheets per signature the effect is sub-millimetre.

What Deckle does instead is **report it**: `paper_thickness_pt × sheets_per_signature`
gives the predicted fore-edge trim, surfaced as an info-tier advisory with the actionable
remedy (thin the signature). This is the MVP's stated trade-off hierarchy — *honest
reporting over reassuring UI* — applied to a feature that four other projects shipped as a
knob that silently did nothing. `paper_thickness_pt` **must never enter placement
geometry** in v2; a `[MECHANICAL]` grep can enforce that it appears only in the warning
path.

## Evaluation Findings (2026-08-04)

Three load-bearing claims were verified against the code rather than accepted:

- **`plan_passes` reads only `sheet.index`. CONFIRMED.** Its body is
  `indices = [s.index for s in plan.sheets]` and nothing else touches a sheet. The design's
  central claim — that signature imposition is invisible to the manual-duplex brain — holds.
- **`saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`. CONFIRMED** against
  `[[pikepdf - Imposition and Signature Recipe]]` line 63.
- **`pikepdf.canvas.ContentStreamBuilder` exists** in the installed pikepdf 10.11.0.

Two Critical gaps were found and are resolved below.

### C-1 — Success Criterion 2's zero-diff claim has an unstated precondition

`deckle/core/print_session.py::_hash_plan` **does** read sheet sides:

```python
{"index": s.index, "front": s.front is not None, "back": s.back is not None}
```

Zero diff therefore holds **only if** `Sheet.front`/`Sheet.back` remain `Side | None` and an
absent side stays `None`. Representing an absent side as `Side(pages=())` would leave the
code compiling, the test passing, and the hash silently wrong.

**Committed contract:** `Sheet.front: Side | None`, `Sheet.back: Side | None`. An absent
side is `None`. `Side(pages=())` is invalid and a `[MECHANICAL]` check must reject it.

### C-2 — The `Side` change touches five modules, not one

The design names `export.py`. The actual blast radius, from the current tree:

| Module | Reads | What the change requires |
|---|---|---|
| `deckle/core/export.py` | 5 sites | Iterate `side.pages` instead of one `OutputPage`; draw `side.marks` |
| `deckle/core/render.py` | 2 sites | Pass through to `export`; no logic change, but the call shape moves |
| `deckle/core/print_session.py` | 2 sites | **None**, given C-1's contract. Asserted by test |
| `deckle/app/backend.py` | 2 sites | Paint every page on a side, not one |
| `deckle/app/views/preview_view.py` | 1 site | `_output_page_bbox` becomes per-page-in-side for clipping warnings |

A spec derived from the unamended design would have missed `render.py`, `backend.py` and
`preview_view.py` — and the preview one is a correctness gap, not a mechanical one: clipping
warnings computed per *side* instead of per *page* would under-report on a 2-up sheet.

### I-1 — `_hash_plan` is content-blind, and folio amplifies it

The hash covers sheet index and side *presence* only — not placements, not which source
pages landed where. Two different page orderings over the same sheet count therefore hash
identically. A resumed `PrintSession` could bind to a document whose content changed. This
is latent in the MVP; folio doubles the content behind each hash.

**Resolution for v2:** extend `_hash_plan` to include each side's `source_ref.page_index`
tuple. This is a deliberate, scoped exception to the zero-diff criterion for
`print_session.py` — Success Criterion 2 is amended accordingly below.

### Escalations resolved (operator AFK; recorded for review)

| Escalation | Decision | Reasoning |
|---|---|---|
| Vendor HornPenguin? | **No — write the math** | Accepts the design's recommendation. `pdf2image` needs a Poppler binary, which violates Deckle's no-external-runtime-binary constraint outright — this is not a preference but a hard-constraint failure. Reverses the MVP's "Approved reuse" row and closes MVP Open Question 6 |
| `Sheet` contract change | **Approved** | `SheetPlan` is never persisted (`project_io` writes pages/layout/printer only), so there is no format migration. Bounded by the C-1 contract above |
| `PrinterProfile` per-orientation imageable area | **Deferred, not blocking** | Only hardware answers it. v2 emits a `landscape_imageable_unverified` info warning when a portrait-measured profile is used with landscape paper. Measure during the folded-dummy run |
| Folded-dummy verification | **`dispatch: manual`** | Cannot be delegated; it is the only check on `saddle_order`, and the vault note states plainly that the function is hand-written and unverified against paper |

## Success Criteria

1. `SaddleStitchStrategy` implements `LayoutStrategy` with the **unmodified** signature
   `impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`, and
   `deckle/core/layout.py` still passes the no-I/O `[MECHANICAL]` check.
2. `deckle/core/printing.py` and `deckle/core/profiles.py` have **zero diff**, asserted by
   a test rather than inspection. `deckle/core/print_session.py` changes in exactly one
   place — `_hash_plan` gains each side's `source_ref.page_index` tuple (evaluation finding
   I-1); `PrintSession`'s public surface and behaviour are unchanged, also asserted.
3. A 266-page document at `sheets_per_signature=4` produces 17 signatures, 67 sheets, and
   exactly 2 blanks, all in the final signature.
4. `fold_reading_order(plan)` returns the source pages in reading order for every
   combination of {page count 1…40, 100, 266} × {sheets per signature 1…8} × {left, right
   binding} — the fold-simulator round trip.
5. `saddle_order(n)` is a permutation of `range(n)` for every `n` that is a multiple of 4
   up to 128, and `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]` — pinned against
   `[[pikepdf - Imposition and Signature Recipe]]`'s executed output.
6. An exported folio sheet's coalesced content stream contains **two** balanced `q…Q`
   blocks per PDF page, each a pure `1 0 0 1 tx ty cm` translation when the document scale
   is 1.0 — asserted by parsing bytes, after `contents_coalesce()`.
7. `GutterShiftStrategy` output is unchanged after the `Side` refactor: the Pinebox golden
   fixture and all existing layout tests pass untouched except for the mechanical
   `Side` wrapper.
8. One document-wide scale: across all 67 sheets of the Traveller fixture, exactly one
   distinct non-filler `scale_x` value.
9. Per-signature printing works through the existing subset path —
   `plan_passes(plan, profile, sheets=sig.sheet_indices)` returns passes covering only that
   signature, with no new branch in `printing.py`.
10. Sewing station marks appear at the specified count, spacing and margin, on the fold
    line, on the innermost sheet of each signature — asserted as `Mark` coordinates in
    core, and visible in the rasterized preview because the preview *is* the export.
11. Signature order marks step monotonically down the spine across signatures, with the
    first and last at the stated margins.
12. The license audit test additionally fails if `pdfimpose`, `PyMuPDF`, `fitz`, `cpdf` or
    any AGPL distribution is present in the environment — the oracle can never leak into a
    shipped or CI dependency.
13. **`[HUMAN REVIEW]`** A physical folded dummy: one 4-sheet signature printed on the real
    printer through the existing calibrated `PrinterProfile`, folded, nested, and read
    front to back in correct page order. **This gates the feature.**

## Exclusions

Not in v2, with the reason each is out:

- **Quarto, octavo, sextodecimo** (2, 3, 4 folds). Need a 2D cell grid, per-cell 180°
  rotation, and general fold-sequence machinery — the exact thing HornPenguin left
  unfinished for four years. Folio first; the `fold_scheme` enum is the seam.
- **`hardcover` / stack-don't-nest** as a separate scheme. A folio gathering of **one**
  sheet, repeated, *is* fold-individually-and-stack. `sheets_per_signature=1` already
  expresses it; a second enum value for the same output would be a control that changes
  nothing (`docs/decisions.md`, *Deleted the scale mode*).
- **`cutstackfold`, `copycutfold`, `wire`, `cards`, `onepagezine`** — pdfimpose's other
  schemas. Different physical operations, none of which the user performs.
- **Perfect binding.** Bookbinder JS has a real `PERFECTBOUND_LAYOUTS` table; there is no
  glue-up in this workflow yet.
- **Creep compensation.** Reported, never applied. See above.
- **Trim/bleed/CMYK/registration marks.** HornPenguin has them; they are for a commercial
  press, not a home laser printer.
- **Top-edge binding.** Still deferred from the MVP (red-team P-5); a folio fold does not
  change that analysis.
- **Automatic paper rotation** for folio. Warn, do not rotate.
- **Vendoring any third-party source.** See below.

## Open Questions

1. **Which sheet and side carries the sewing stations?** Recommendation is the innermost
   sheet's inner side, because that is where the awl enters an opened gathering.
   **Changes:** one predicate in `marks.py` and one default in `LayoutSettings`. Resolved
   only by pricking a real dummy — this is the kind of thing no documentation states because
   everyone who binds already knows it.

2. **Does `binding_edge` overloading survive contact with use?** Under folio it means
   reading direction, not gutter side. **Changes:** if it confuses in practice, split into
   `reading_direction` and deprecate `binding_edge` for folio — a `LayoutSettings` field
   change and a `.deckle` migration (the first one this project would have, so the `version`
   integer finally earns its keep).

3. **Is the imageable area orientation-dependent on the target printer?** Most printers
   have a different unprintable border on the leading edge, so landscape feed may not simply
   transpose the portrait numbers. **Changes:** `PrinterProfile` would need
   `imageable_area_pt` **per orientation**, which is a profile-shape change and therefore a
   human-approval item. Measure during the folded-dummy run; it costs one extra test sheet.

4. **`blank_mode="balanced"` — where exactly?** Spread the shortfall across trailing
   signatures, or place blanks at the *front* of the first signature so the book starts on a
   recto? **Changes:** one function; strongly affects whether page 1 lands where a reader
   expects. Note `start_on_recto` is currently a documented no-op in
   `GutterShiftStrategy` and becomes genuinely load-bearing under folio.

5. **Should the fold simulator ship, or stay a test fixture?** As a UI feature it is a
   "read the book back" preview — arguably the most reassuring thing the app could show
   before committing 67 sheets. **Changes:** a preview mode; no core change, since the
   function is pure either way.

6. **Does per-signature export want its own files** (`sig-01_side1.pdf`), matching
   Bookbinder JS's desk procedure, or is in-app per-signature printing enough?
   **Changes:** a CLI/UI affordance over `export(..., sheets=...)`; no core change. Relevant
   only if the user ever wants to print from something other than Deckle.

## Approaches Considered

**Approach A — new `LayoutStrategy` implementation + `Side` in the model. SELECTED.**
The seam was designed for exactly this and the signature does not move. Placement math is
generalised to a cell and shared between strategies rather than duplicated. The manual-duplex
half of the application is untouched. Cost: one breaking change to `Sheet`, which is cheap
because `SheetPlan` is never persisted.

**Approach B — post-process a `GutterShiftStrategy` plan: reorder and pair up its sheets.
Rejected.** The 1-up plan's `Placement` values are computed against a full-sheet content
box, so every one of them is wrong as an input to a half-width cell; you would recompute all
of them, which means two places in the codebase computing placement. That directly violates
the MVP's hard constraint that no consumer recomputes or adjusts a `Placement`, and
`docs/decisions.md` (*fixed_gutter put the reserved gutter on the wrong side of the verso*)
is a recorded instance of what happens when one geometry rule is quietly relied on by a
second code path.

**Approach C — a `PageOrderer` stage producing a reordered `list[SourcePage]`, then run the
existing 1-up strategy. Rejected, and instructively.** This cannot express two pages on one
side at all — the output is one page per side in fold order. That is precisely what Stirling
PDF and PDF Arranger do, and it is why both produce physically impossible single-signature
output. Reordering is not imposition.

**Approach D — vendor HornPenguin's BSD-3 imposition math. Rejected. RECOMMENDATION, and an
escalation.**
This reverses the "Approved reuse" row in
`docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md` and closes Open Question 6.
Per the MVP's Decision Authority, vendoring is a human decision — so this is a
recommendation with reasons, not a done deal.

- **The math we need is ten lines, and it is already executed on this machine.**
  `saddle_order` in `[[pikepdf - Imposition and Signature Recipe]]` is verified working
  output, not composed API. Vendoring a file to save ten lines is negative value.
- **What we would actually be vendoring is the unfinished part.** HornPenguin's general
  imposition algorithm is [open issue #1 since Sept 2022](https://github.com/HornPenguin/Booklet/issues/1);
  its `TODO` calls general fold sequences "difficult to complete on schedule." Folio and
  saddle order — the parts we need — are the easy parts. The hard parts are the parts it
  does not have.
- **ASM-6 is confirmed, not hypothetical.** The MVP's war-game flagged "HornPenguin's
  imposition math may be coupled to PyPDF2 objects" as a risk to a claimed time saving.
  `[[HornPenguin Booklet - Dependencies]]` confirms the coupling: pinned `pypdf==3.16.2`,
  plus `pdf2image` (needs an external Poppler binary — an outright violation of Deckle's
  no-runtime-binary constraint) and `simpleaudio` (a C extension with no recent wheels,
  present only to play a completion chime). Taking "the math, not the I/O" means untangling
  the math from pypdf 3 object types first, which is more work than writing it.
- **There is no PyPI package and no `pyproject.toml`** — no upstream to track, no version to
  pin, and the last release was 2022.
- **BSD-3 is compatible but not free.** It requires preserving the copyright and
  no-endorsement clauses, and it puts third-party-provenance files inside a repository whose
  license audit is a hard automated gate. Non-zero permanent cost for zero saved work.
- **Take the interface, not the code.** `--sig-composition [sheets] [inserts]`,
  `--blank-mode`, riffle direction, the `4f/8f/16f/32f` fold-capable enumeration — that
  vocabulary was designed by someone who binds books and it is reflected directly in this
  design's `LayoutSettings` fields. Interface design is not copyrightable expression, and it
  is the genuinely valuable part.

**Approach E — shell out to, or import, pdfimpose. Rejected, hard gate.** AGPL-3.0, and it
pulls AGPL PyMuPDF. `docs/specs/2026-08-04-deckle-mvp.md` names this as a *live temptation,
not a hypothetical*. It remains excellent to run personally and is used below as a
development-time oracle — never as a dependency of anything shipped or tested.

## Testing Strategy

Layered, cheapest first, with a physical gate at the end.

**1. Arithmetic (pytest, fast).** `split_signatures` over a table: page counts
{1,2,3,4,5,7,8,15,16,17,32,100,266} × sheets-per-signature {1,2,3,4,6,8} × blank modes ×
custom patterns. Property assertions, not golden values: every signature is a multiple of 4
slots; the sum covers the padded count; slices concatenate to the padded list in order;
`sheet_indices` are contiguous and gapless; blank count equals padded minus original.

**2. Fold-order properties.** `saddle_order(n)` is a permutation of `range(n)`; the first
pair is `(n-1, 0)` — the outermost sheet carries the last and first pages, the defining
property of a nested gathering; symmetric under the left/right binding mirror.

**3. The fold simulator — the highest-value test in the feature.**
`fold_reading_order(impose(pages, settings)) == range(len(pages))`, parametrised over the
full grid in Success Criterion 4. This is the direct analogue of SS-03's **sign-inversion
guard**: it asserts *physical intent* rather than a computed value, so it survives a bug
that the imposition code and its arithmetic tests would encode identically. The MVP's
recorded lesson is that a gutter-parity inversion *"would have passed every numeric test in
this sub-spec, because the tests encode the same parity assumption as the criterion."* Fold
order is the same class of bug with a larger blast radius, and the simulator is written
against the *physical* description of folding and nesting, deliberately not by reusing
`saddle_order`.

**4. Placement and export.** Two Form XObjects per output PDF page; balanced `q…Q`; pure
translations at scale 1.0; one distinct non-filler scale across the document; per-cell
`actual_margins_pt` matching the requested gutter/fore-edge under each `slack_to` mode. Note
the trap already recorded in `docs/decisions.md`: `page.Contents` is an `Array` of streams,
so `contents_coalesce()` must run before `read_bytes()` — it bites tooling, not just
application code.

**5. Marks.** Station count, spacing, margins and tape pairing as coordinates; signature
order marks stepping monotonically; marks land on the fold line and not inside a cell's
content box.

**6. Regression.** `GutterShiftStrategy` must be unchanged. Pinebox golden fixture plus the
existing layout suite, with only the mechanical `Side(...)` wrapper edited.

**7. pdfimpose as a development-time oracle — never a dependency.**
`tools/oracle_diff.py`, outside `tests/`, absent from `pyproject.toml`, run manually in a
throwaway virtualenv. It drives
`pdfimpose.schema.saddle.impose(files, output, signature=(2,1), group=N, bind="left")` on a
numbered fixture and diffs pdfimpose's resulting source-page→(sheet, side, cell) matrix
against Deckle's. pdfimpose is one of only two surveyed tools that split signatures
correctly, and `[[pdfimpose - Content Is Shifted Not Scaled]]` verifies it never rescales —
so it is a genuinely good reference for the one thing being checked. Its `impose()` accepts
`io.BytesIO` at both ends (`[[pdfimpose - Library API]]`), so the diff needs no temp files.

**License hygiene is enforced, not trusted:** `tests/test_license_audit.py` gains explicit
failures for `pdfimpose`, `pymupdf`/`fitz`, and `cpdf` in the installed distribution set, so
a developer who runs the oracle in the project venv gets a red test rather than a shipped
AGPL dependency. The oracle is a tool that must be *unable* to become a dependency.

**8. The physical folded dummy — `[HUMAN REVIEW]`, and a gate.**
Print one 4-sheet signature of a numbered fixture on the real printer, through the existing
`PrinterProfile` and `PassPlanner`. Fold. Nest. Read 1→16. Then prick the sewing stations
and confirm they land where an awl wants them. `[[pikepdf - Imposition and Signature
Recipe]]` says this in as many words: *"the pikepdf half is confirmed, the bindery half is
yours."* No amount of green pytest substitutes for folding paper, and
`docs/decisions.md` (*Added run.bat; verified the GUI genuinely launches*) records this
project already learning once that passing tests are not observation.

Use the numbered-pages approach Bookbinder JS ships for exactly this
(`/docs/example_page_numbers.pdf`) — calibrate on a throwaway, never on the manuscript.

## Risks

| Risk | Likelihood | What it costs | What the answer changes |
|---|---|---|---|
| **`saddle_order` is subtly wrong** (off-by-one at a signature boundary, or correct for nesting but applied to a stack) | Medium | An entire printed book, discovered at the fold | The fold simulator plus the physical dummy are both aimed at this. If the dummy fails, the arithmetic is wrong and no amount of test coverage would have shown it |
| **`Side` refactor breaks the MVP** | Low, high blast radius | The 223-test suite and a shipped, working app | Do the refactor as a standalone commit with **zero behaviour change**, verified by the Pinebox golden, *before* any signature code exists. If Pinebox drifts, stop |
| **Imageable area differs in landscape** | Medium | Fore-edge content lost near the fold on every sheet | `PrinterProfile` gains per-orientation imageable area — a profile-shape change requiring human approval and a re-calibration |
| **Halved cell width makes clipping the normal case** | High | User-visible warning fatigue that trains people to ignore warnings | Content sized for a full letter page will not fit a half-letter cell without shrinking. The scale rule handles it correctly; the *warnings* may need a "this is expected under folio" framing rather than the 1-up phrasing |
| **`binding_edge` means two different things** | Medium | Right-bound books imposed mirror-reversed | Split the field, and take the `.deckle` migration |
| **Marks land in the wrong place physically** | Medium | Wasted paper, not a wasted book — recoverable | Pricking a dummy answers it in ten minutes. This is why the mark defaults are called out as an open question rather than asserted |
| **Scope creep into quarto** | Medium | The feature never ships | The exclusion is explicit and the enum is the seam. HornPenguin spent four years on exactly this and did not finish |

### Escalate now

- **Do not vendor HornPenguin.** Reverses an approved-reuse decision in the MVP design and
  closes Open Question 6. Human decision per the Decision Authority.
- **`Sheet` is a breaking model change.** It touches no persisted format, but it is a
  deviation from the SS-01 committed contract and therefore an "agent recommends, human
  approves" item.
- **`PrinterProfile` may need per-orientation imageable area.** Profile field set is
  explicitly a human-approval item, and this can only be answered against hardware.
- **The physical folded dummy cannot be delegated to an agent.** Mark the corresponding
  sub-spec `dispatch: manual`, as SS-13 already is.

## Commander's Intent

**Desired End State**

`SaddleStitchStrategy` sits beside `GutterShiftStrategy` behind the unchanged
`LayoutStrategy` Protocol. A 266-page book at 4 sheets per signature imposes to 17
signatures / 67 sheets / 2 blanks, exports with two pure-translation placements per PDF
page, carries sewing-station and signature-order marks on the fold, and prints per-signature
through the existing `sheets=` subset path with **no new branch in `printing.py`**. All 234
existing tests still pass. The gate is a physical folded dummy that reads front to back in
correct order.

**Purpose**

The MVP produces a 3-hole-punch layout. The user hand-sews on a Singer 111w101 and wants a
real book. Folio at 4 sheets per signature also halves the paper — 67 sheets against 133.
**When a judgment call is not covered here, favour whatever makes the folded, sewn result
correct**; physical correctness outranks code elegance, feature breadth and UI polish, in
that order.

**Constraints**

- **MUST NOT** change `LayoutStrategy.impose`'s signature.
- **MUST NOT** introduce AGPL. `pdfimpose` is a dev-time oracle in a throwaway venv and
  must be denylisted in the license audit so it can never become a dependency.
- **MUST NOT** add an external runtime binary. This is what disqualifies vendoring
  HornPenguin (`pdf2image` → Poppler).
- **MUST NOT** import Qt into `deckle.core`, or perform I/O in `layout.py`.
- **MUST NOT** let `paper_thickness_pt` reach placement geometry — warning path only,
  enforced by grep.
- **MUST NOT** recompute or adjust a `Placement` outside `Imposer`.
- **MUST** keep signatures on contiguous runs of sheet indices, in binding order.
- **MUST** keep one document-wide scale (`document_scale`), generalised to a cell.
- **MUST** represent an absent side as `None`, never `Side(pages=())`.
- **MUST** run exactly one padding pass — the predecessor script's defect 2.

**Freedoms** — the implementing agent MAY choose module layout, naming, test organisation,
the internal representation of `Mark`, the sewing-station spacing algorithm's internals, and
whether `signatures` is a tuple or list on `SheetPlan`.

### Committed interface/contract defaults

- **`Side`** → **Default:** `@dataclass(frozen=True) class Side: pages: tuple[OutputPage, ...]; marks: tuple[Mark, ...] = ()`.
- **`Sheet`** → **Default:** `front: Side | None`, `back: Side | None`. Absent side is
  `None`. _(C-1: `Side(pages=())` is invalid and must be rejected.)_
- **`Signature`** → **Default:**
  `@dataclass(frozen=True) class Signature: index: int; sheet_indices: tuple[int, ...]; blank_count: int`.
- **`SheetPlan`** → **Default:** gains `signatures: tuple[Signature, ...] = ()`. Empty for
  `GutterShiftStrategy`, so the MVP strategy needs no change beyond the `Side` wrapper.
- **`Mark`** → **Default:**
  `@dataclass(frozen=True) class Mark: kind: Literal["sewing_station","signature_order","fold_line"]; x0: float; y0: float; x1: float; y1: float` — sheet points, PDF origin bottom-left, a line segment in every case.
- **`split_signatures`** → **Default:**
  `split_signatures(sheet_count: int, sheets_per_signature: int) -> list[tuple[int, ...]]`, contiguous, remainder in the final signature.
- **`saddle_order`** → **Default:** `saddle_order(n: int) -> list[int]` where `n` is a
  multiple of 4. Pinned: `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`.
- **`fold_reading_order`** → **Default:**
  `fold_reading_order(plan: SheetPlan) -> list[int]` returning source page indices in the
  order a folded, nested, gathered stack reads. **Agent-free** on internals, but it MUST be
  derived from the physical fold description, never by reusing `saddle_order` — a shared
  implementation would let both encode the same error and agree.
- **Cell** → **Default:** `Cell = tuple[float, float, float, float]` (`x0, y0, x1, y1` in
  sheet points). `GutterShiftStrategy` passes the full sheet.
- **New `LayoutSettings` fields** → **Default:** `fold_scheme: Literal["none","folio"] = "none"`,
  `sheets_per_signature: int = 4`, `paper_thickness_pt: float = 0.0`,
  `sewing_stations: int = 3`, `blank_mode: Literal["end","balanced"] = "end"`.

## Execution Guidance

**Observe**
- `python -m pytest -q` — 234 passing before any change; that number must not fall.
- The core-purity test (no Qt in `deckle.core`) and the no-I/O check on `layout.py`.
- The license audit, extended with `pdfimpose`/`cpdf` denylist entries.
- The Pinebox golden fixture and every existing `test_layout.py` test — the `Side` refactor
  must not move MVP output.
- `ruff check deckle tests` — currently clean.

**Orient**
- `docs/decisions.md` is the highest-value context in the repo. Read it. Several entries are
  directly about this code path.
- Assert **measured margins** via `actual_margins_pt`, never raw `tx`/`ty`. A coordinate
  assertion on one edge of one page is how a verso-only bug survived the original suite.
- The preview rasterises the exported PDF; marks are visible in preview by construction, so
  there is no separate preview drawing path to keep in sync.
- Filler pages carry a neutral `scale_x = 1.0` and must be excluded from any "one document
  scale" assertion.

**Escalate When**
- Any change is needed to `printing.py` or `profiles.py` — the zero-diff criterion is the
  design's central claim and a diff there means the seam did not hold.
- `fold_reading_order` and the imposition disagree and the discrepancy is not obviously a
  bug in one of them. That is the signature of a shared wrong assumption.
- The `.deckle` format would need to change (it should not; `SheetPlan` is not persisted).
- Any dependency addition.

**Shortcuts (apply without deliberation)**
- Composition: `as_form_xobject` + `calc_form_xobject_placement`. Never `add_overlay`.
- Never reorder a `pikepdf.Pdf.pages` list by tuple-swap or slice assignment.
- `page.Contents` may be an Array — `contents_coalesce()` before reading bytes.
- Wrap `page.mediabox` in `Rectangle` before using `.width`.
- Marks drawing: `pikepdf.canvas.ContentStreamBuilder` (confirmed present in 10.11.0).
- Tests parametrise over aspect ratios and both binding edges; follow `test_layout.py`.

## Decision Authority

**Agent decides autonomously** — module and file layout, naming, test organisation, `Mark`
internals, sewing-station spacing arithmetic, warning message wording, whether `signatures`
is a tuple or list.

**Agent recommends, human approves** — any new `LayoutSettings` field beyond those committed
above; any change to `PrintSession`'s public surface; any dependency; changing the `.deckle`
format.

**Human decides** — vendoring third-party source (resolved: no); `PrinterProfile` shape
changes (deferred); scope changes to the Exclusions list; the folded-dummy verdict.

## War-Game Results

**Most likely failure:** `saddle_order` is subtly wrong in a way `fold_reading_order`
encodes identically, so the round-trip test passes and the printed book is out of order.
*Mitigation:* `fold_reading_order` must be written from the physical fold description
without reference to `saddle_order`, plus the pinned `saddle_order(8)` value, plus the
mandatory physical dummy. The vault note says this outright: *"saddle_order is my own
function, not pikepdf's… validate the fold order against a physical folded dummy."*

**Scale stress:** 266 pages → 67 sheets → 134 sides → 268 placements, versus the MVP's 266.
Export is already batched (`_BATCH_SHEETS`), and the preview renders one sheet. Marks add
~10 line segments per sheet. No new scaling concern.

**Dependency risk:** none added. The only new API surface is `pikepdf.canvas`, confirmed
present. `pdfimpose` as an oracle is the live risk — hence the denylist in the audit.

**Maintenance (6 months):** strong. The `Side`/`Signature` nouns extend the existing
four-level vocabulary rather than competing with it, and every rejected approach is recorded
with its reason.

## Evaluation Metadata

- Evaluated: 2026-08-04
- Cynefin: **Complicated**, with `saddle_order`'s physical correctness as a **Complex**
  pocket that only paper resolves
- Critical gaps: 2 (2 resolved) · Important: 1 (1 resolved) · Escalations: 4 (4 decided)
- Verified claims: 3 of 3 confirmed against the code

## Next Steps

- [ ] Commit the `Side` / `Sheet` refactor alone, with the Pinebox golden green and zero
      behaviour change. Nothing else lands until this is clean.
- [ ] Generalise `content_box_size`, `content_box_rect_pt`, `actual_margins_pt` and
      `document_scale` to take a cell; confirm `GutterShiftStrategy` output is identical.
- [ ] Write `deckle/core/signatures.py` — `split_signatures`, `saddle_order`,
      `fold_reading_order` — **fold simulator first**, per the SS-03 write-the-regression-
      test-first precedent.
- [ ] Implement `SaddleStitchStrategy`; assert `printing.py` / `profiles.py` /
      `print_session.py` diff is empty.
- [ ] Extend `export._sides` to iterate a `Side`; verify two pure-translation XObjects per
      PDF page against the vault's recorded `1 0 0 1 396 0 cm`.
- [ ] Write `deckle/core/marks.py` and the `ContentStreamBuilder` drawing path.
- [ ] Add `pdfimpose`, `pymupdf`, `fitz`, `cpdf` to the license-audit denylist **before**
      writing `tools/oracle_diff.py`.
- [ ] Build the oracle in a throwaway venv; diff the imposition matrix on a numbered
      32-page fixture.
- [ ] Wire the *Binding* group into `layout_panel.py` and per-cell labels into
      `preview_view.py`.
- [ ] **Print, fold, nest, read, prick.** Record the result in `docs/decisions.md`.
- [ ] Turn this design into a Forge spec (`/forge <this file>`).
