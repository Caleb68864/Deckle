# Deckle v2 — Signature Imposition (folio saddle-stitch, bindery marks)

## Meta
- Client: Personal
- Project: Deckle
- Repo: `C:\Users\CalebBennett\Documents\GitHub\BookBinder` (https://github.com/Caleb68864/Deckle)
- Branch at authoring: `main`, clean, 237 tests collected (234 pass, 3 skip on a machine without the Pinebox fixture)
- Date: 2026-08-04
- Author: Forge Dark Factory (Stage 2)
- Design source: `docs/plans/2026-08-04-deckle-signatures-v2-design.md` (status: evaluated)
- Builds on: `docs/specs/2026-08-04-deckle-mvp.md`
- License: MIT
- Quality Score: **28/30**
  - Outcome: 5/5
  - Scope: 5/5
  - Decision guidance: 5/5
  - Edges: 5/5
  - Criteria: 4/5
  - Decomposition: 4/5
- Status: draft

## Outcome

`SaddleStitchStrategy` sits beside `GutterShiftStrategy` behind the **unchanged**
`LayoutStrategy` Protocol. A 266-page document at `sheets_per_signature=4` imposes to
**17 signatures / 67 sheets / 2 blanks**, exports as landscape sheets carrying **two**
pure-translation Form XObject placements per PDF page plus sewing-station, signature-order
and fold-line marks drawn on the fold, and prints one signature at a time through the
**existing** `sheets=` subset path with **no new branch in `deckle/core/printing.py`**.

Done means: every acceptance criterion in every sub-spec below passes; `python -m pytest -q`
reports zero failures with no fewer tests than the 237 collected at authoring time;
`ruff check deckle tests` is clean; the Pinebox golden fixture and all 46 existing
`tests/test_layout.py` tests still pass with only the mechanical `Side(...)` wrapper edited;
and **SS-13's physical folded dummy has been printed, folded, nested and read front to back
in correct order by a human.** SS-13 gates the feature — nothing ships without it.

## Intent

**Trade-off hierarchy** — when valid approaches conflict, prefer in this order:

1. **Printed physical correctness** over everything. When a judgment call is not covered
   here, favour whatever makes the folded, sewn result correct. Physical correctness
   outranks code elegance, feature breadth and UI polish, in that order.
2. **Honest reporting** over reassuring UI. Creep is *measured and reported, never
   compensated*. Four other projects shipped creep compensation as a knob that silently did
   nothing; Deckle ships an advisory that names the remedy.
3. **The seam holds** over local convenience. `printing.py` and `profiles.py` are
   zero-diff. A diff there means the design's central claim failed and is an escalation, not
   a fix.
4. **One implementation of every geometry rule** over two agreeing copies. Two strategies
   must never contain two copies of the placement rule — that is how the verso-only bug in
   `docs/decisions.md` survived its tests.
5. **Core purity and testability** over convenience. No Qt in `deckle.core`; no I/O in
   `layout.py`.
6. **Simple and obviously correct** over clever or general. Folio only. HornPenguin spent
   four years on general fold sequences and did not finish.

**Decide autonomously:** module and file layout, naming, test organisation, `Mark`
internals, the sewing-station spacing algorithm's internals, warning message wording,
whether `signatures` is materialised as a tuple or a list internally, cache sizes, helper
function names, test parametrisation style.

**Decision Boundaries — stop and escalate if:**

- Any change is needed to `deckle/core/printing.py` or `deckle/core/profiles.py`. The
  zero-diff criterion is the design's central claim; a diff there means the seam did not
  hold.
- `fold_reading_order` and the imposition disagree and the discrepancy is not obviously a
  bug in exactly one of them. That is the signature of a shared wrong assumption and is the
  single most expensive failure mode in this feature.
- The `.deckle` project format would need to change. It should not — `SheetPlan` is never
  persisted; `deckle/core/project_io.py` writes `pages`, `layout` and `printer` only.
- Any dependency addition, or any change to `PrintSession`'s **public** surface.
- Any new `LayoutSettings` field beyond the five committed in *Contracts* below.
- `PrinterProfile` would need a shape change (e.g. per-orientation imageable area). Deferred;
  only hardware answers it.
- Vendoring third-party source. **Already decided: no.** Re-opening it is an escalation.
- Any acceptance criterion appears unachievable as written.

**Decided — no further escalation, do not re-open:**

- **Do not vendor HornPenguin.** `pdf2image` requires an external Poppler binary, which
  violates Deckle's no-external-runtime-binary constraint outright — a hard-constraint
  failure, not a preference. This reverses the MVP design's "Approved reuse" row and closes
  MVP Open Question 6.
- **The `Sheet` model change is approved.** `SheetPlan` is never persisted, so there is no
  file-format migration and no `version` bump.
- **`pdfimpose` is a development-time oracle only**, in a throwaway virtualenv, denylisted
  in the license audit so it can never become a dependency.
- **Folio only.** Quarto, octavo and sextodecimo are out of scope; `fold_scheme` is the seam.
- **No creep compensation.** Reported as an advisory, never applied to geometry.
- **The folded dummy is `dispatch: manual`.** It cannot be delegated.

## Context

The MVP (`docs/specs/2026-08-04-deckle-mvp.md`, 14 sub-specs, shipped) produces a
3-hole-punch layout: one source page per physical side, with an alternating binding gutter.
The user hand-sews on a Singer 111w101 and wants a real book. Folio at 4 sheets per
signature also halves the paper — 67 sheets against the MVP's 133 for the 266-page Traveller
Core Rulebook fixture used throughout `docs/decisions.md`.

**The whole feature enters through the existing `LayoutStrategy` seam.** `impose(pages,
settings) -> SheetPlan` does not change. The manual-duplex machinery (`plan_passes`,
`PrintSession`, `PrinterProfile`, the SS-13 calibration wizard) is not touched: signature
imposition decides *what lands on a side*; the `PassPlanner` already owns *the order sides
are fed*. This was verified against the code, not assumed — `deckle/core/printing.py:121`
is `indices = [s.index for s in plan.sheets] if sheets is None else list(sheets)`, and
nothing else in `plan_passes` touches a `Sheet`.

**One model change is required and it is a real one:** a sheet side now carries **two**
pages, not one. `Sheet.front` / `Sheet.back` become `Side | None`.

**`docs/decisions.md` is the highest-value context in the repo. Read it before writing
code.** Six of its entries bear directly on this work:

| Decision-log entry | Why it binds here |
|---|---|
| *Uniform document-wide scale; per-page scaling resized the text* | One scale, computed against the **cell**, applied to every leaf. Per-page scaling made the Traveller cover's text 2.5% larger than the body's. Filler pages carry a neutral `scale_x = 1.0` and must be excluded from any "one scale" assertion. |
| *Rebuilt the placement math on one rule for both axes* | Assert **measured margins** via `actual_margins_pt`, never raw `tx`/`ty`. A coordinate assertion on one edge of one page is how a verso-only bug survived the original suite. |
| *`fixed_gutter` put the reserved gutter on the wrong side of the verso* | The recorded instance of one geometry rule being quietly relied on by a second code path. Generalise the shared math; never duplicate it. |
| *`slack_to` replaces the `maximize_gutter` boolean* | A shape that cannot express the real question makes part of the answer space unreachable. `Side.pages` is a tuple of N, not a page plus an "extra". |
| *Path-traversal validation must advise, not refuse* | Portrait paper under folio **warns**, never blocks. Check the criterion's verb before choosing the mechanism. |
| *Negative-assertion acceptance criteria must exit 0 when the pattern is absent* | Every negative `[MECHANICAL]` criterion below is written `! grep … \|\| (echo "FAIL: …" && exit 1)`. A bare `grep` with prose saying "returns nothing" deferred two sub-specs in factory run `c46e15e3` and cascaded into seven more. |

**The pikepdf half is confirmed; the bindery half is not.**
`[[pikepdf - Imposition and Signature Recipe]]`
(`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition and Signature Recipe.md`)
is executed working code against pikepdf 10.11.0 / libqpdf 12.3.2 on this machine. It
produces exactly this feature's target output — 8 half-letter pages onto 4 landscape letter
sheets, 2-up, in fold order — and its recorded coalesced content stream for sheet 1 is
`q 1 0 0 1 0 0 cm /Fx… Do Q  q 1 0 0 1 396 0 cm /Fx… Do Q`: two balanced blocks, pure
translation, zero scale drift. That note also says in as many words: *"`saddle_order` is my
own function, not pikepdf's… Validate the fold order against a physical folded dummy before
trusting it for a real print run."* That sentence is why SS-13 exists and why it gates.

Verified at authoring time in this environment: `pikepdf.canvas.ContentStreamBuilder`
exists; `ruff` 0.15.13 is installed; 237 tests collect, 234 pass and 3 skip.

## Contracts

These ten defaults are **committed** by the evaluated design. They are inputs, not
questions. Implement them verbatim; deviating from any of them is an escalation.

| # | Contract | Committed default |
|---|---|---|
| 1 | `Side` | `@dataclass(frozen=True) class Side: pages: tuple[OutputPage, ...]; marks: tuple[Mark, ...] = ()` |
| 2 | `Sheet` | `front: Side \| None`, `back: Side \| None`. An absent side is `None`. `Side(pages=())` is **invalid and must be rejected**. |
| 3 | `Signature` | `@dataclass(frozen=True) class Signature: index: int; sheet_indices: tuple[int, ...]; blank_count: int` |
| 4 | `SheetPlan` | gains `signatures: tuple[Signature, ...] = ()`. **Empty** for `GutterShiftStrategy`. |
| 5 | `Mark` | `@dataclass(frozen=True) class Mark: kind: Literal["sewing_station","signature_order","fold_line"]; x0: float; y0: float; x1: float; y1: float` — sheet points, PDF origin bottom-left, **a line segment in every case**. |
| 6 | `split_signatures` | `split_signatures(sheet_count: int, sheets_per_signature: int) -> list[tuple[int, ...]]`, contiguous sheet indices, remainder in the **final** signature. |
| 7 | `saddle_order` | `saddle_order(n: int) -> list[int]`, `n` a multiple of 4. Pinned: `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`. |
| 8 | `fold_reading_order` | `fold_reading_order(plan: SheetPlan) -> list[int]`. Internals are agent-free **except**: it MUST be derived from the physical fold description and MUST NOT reuse `saddle_order`. |
| 9 | `Cell` | `Cell = tuple[float, float, float, float]` (`x0, y0, x1, y1` in sheet points). `GutterShiftStrategy` passes the full sheet. |
| 10 | New `LayoutSettings` fields | `fold_scheme: Literal["none","folio"] = "none"`, `sheets_per_signature: int = 4`, `paper_thickness_pt: float = 0.0`, `sewing_stations: int = 3`, `blank_mode: Literal["end","balanced"] = "end"` |

**Contract-vs-prose conflicts, resolved in favour of the committed defaults** (the design's
narrative sections predate its Evaluation; where they disagree, the committed table wins):

- `fold_scheme` value is **`"folio"`**, not `"folio_saddle"`.
- `Mark.kind` value is **`"signature_order"`**, not `"sig_order"`. There is no `"trim"`
  kind, and `Mark` has no `filled` or `line_width_pt` field — every mark is a line segment.
- `Signature` has **no** `source_page_count` field.
- `SheetPlan.signatures` is **empty** for `GutterShiftStrategy` (the prose said "a single
  Signature spanning every sheet"). Consumers must therefore tolerate an empty tuple.
- `paper_thickness_pt` defaults to **`0.0`**, not `0.27`. The creep advisory therefore does
  not fire until the user supplies a thickness — which is correct: Deckle cannot know the
  paper.
- `split_signatures` takes a **sheet** count and returns **sheet-index groups**, not a page
  count returning `SignatureSpec` objects.
- `sewing_marks`, `sig_order_marks`, `fold_lines`, `sewing_margin_pt`,
  `sewing_tape_width_pt` and `signature_pattern` are **not** committed `LayoutSettings`
  fields and MUST NOT be added — new settings fields are an "agent recommends, human
  approves" item. Their behaviour is derived instead (see *Edge Cases*).

## Requirements

1. REQ-001: `deckle/core/models.py` defines `Side` as a frozen dataclass matching contract 1.
2. REQ-002: `Sheet.front` and `Sheet.back` are typed `Side | None`, and an absent side is represented as `None`.
3. REQ-003: Constructing `Side(pages=())` raises `ValueError` rather than producing an empty side.
4. REQ-004: `deckle/core/models.py` defines `Mark` as a frozen dataclass matching contract 5.
5. REQ-005: `deckle/core/models.py` defines `Signature` as a frozen dataclass matching contract 3.
6. REQ-006: `SheetPlan` gains `signatures: tuple[Signature, ...] = ()`.
7. REQ-007: `LayoutWarning.kind` accepts `"sheet_orientation"`, `"signature_padding"`, `"creep_advisory"` and `"landscape_imageable_unverified"` in addition to its four existing kinds.
8. REQ-008: `LayoutSettings` gains exactly the five committed fields with exactly the committed defaults, and no others.
9. REQ-009: `GutterShiftStrategy`'s emitted `Placement` values are byte-identical before and after the `Side` refactor, proven by the Pinebox golden fixture and the existing `tests/test_layout.py` suite.
10. REQ-010: `deckle/core/export.py` creates exactly one PDF page per `Side` and places every page in `side.pages` into that PDF page.
11. REQ-011: `deckle/core/render.py` continues to rasterise the exported artifact and never re-draws from `Placement`, with the front-then-back page-index mapping preserved.
12. REQ-012: `deckle/app/backend.py` paints every page on a side, not only the first.
13. REQ-013: `deckle/app/views/preview_view.py` computes clipping warnings **per page within a side**, not per side.
14. REQ-014: `deckle/core/print_session.py::_hash_plan` includes each side's `source_ref.page_index` tuple, so two different page orderings over the same sheet count hash differently.
15. REQ-015: `deckle/core/printing.py` and `deckle/core/profiles.py` are unchanged, asserted by a test rather than by inspection.
16. REQ-016: `split_signatures` returns contiguous, gapless, non-overlapping sheet-index groups covering every sheet exactly once, with the remainder in the final group.
17. REQ-017: `saddle_order(n)` is a permutation of `range(n)` for every multiple of 4 up to 128, and `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`.
18. REQ-018: `fold_reading_order` is implemented from the physical fold description and contains no call to `saddle_order`.
19. REQ-019: `fold_reading_order(impose(pages, settings))` reproduces the source page order for every combination of page count {1…40, 100, 266} × sheets-per-signature {1…8} × binding edge {left, right}.
20. REQ-020: `document_scale`, `content_box_size`, `content_box_rect_pt` and `actual_margins_pt` each accept a `Cell`, and `GutterShiftStrategy` passes the full sheet as its cell.
21. REQ-021: `SaddleStitchStrategy` implements `LayoutStrategy` with the unmodified signature `impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`.
22. REQ-022: A 266-page document at `sheets_per_signature=4` produces 17 signatures, 67 sheets, and exactly 2 blanks, all in the final signature.
23. REQ-023: Across all 67 sheets of the 266-page fixture there is exactly one distinct non-filler `scale_x` value.
24. REQ-024: Padding runs exactly one pass and emits exactly one `signature_padding` warning naming the blank count.
25. REQ-025: Under `fold_scheme="folio"` a leaf's spine edge is derived from its cell's position relative to the fold — left cell's spine on its right edge, right cell's spine on its left — never from output-page parity.
26. REQ-026: Portrait paper under `fold_scheme="folio"` emits a `sheet_orientation` warning, does not rotate the paper, and does not block.
27. REQ-027: `paper_thickness_pt` never reaches placement geometry; it appears only in the creep-advisory path, enforced mechanically.
28. REQ-028: `sewing_stations(...)` returns `settings.sewing_stations` `Mark`s of kind `sewing_station`, evenly spaced between a head and tail inset, centred on the fold line.
29. REQ-029: `signature_order_mark(...)` steps monotonically down the spine across signature indices, with the first and last at the stated insets.
30. REQ-030: `fold_line(...)` returns one `Mark` of kind `fold_line` lying on the cell boundary.
31. REQ-031: Marks are drawn in `deckle/core/export.py` with `pikepdf.canvas.ContentStreamBuilder`; no text is drawn, `add_overlay` is not used, and `Canvas.draw_image` is not used.
32. REQ-032: An exported folio sheet's coalesced content stream contains exactly two balanced `q…Q` blocks per PDF page, each a pure `1 0 0 1 tx ty cm` translation when the document scale is 1.0.
33. REQ-033: `tests/test_license_audit.py` fails if `pdfimpose`, `cpdf`, `pymupdf` or `fitz` is present in the dependency closure.
34. REQ-034: `tools/oracle_diff.py` exists outside `tests/`, is absent from `pyproject.toml`, and is imported by no shipped or tested module.
35. REQ-035: `plan_passes(plan, profile, sheets=sig.sheet_indices)` returns passes covering only that signature, with no new branch in `printing.py`.
36. REQ-036: `deckle/app/views/layout_panel.py` exposes a Binding group (fold scheme, sheets per signature, blank mode, sewing stations, paper thickness) with a live "N signatures · M sheets · K blanks" readout.
37. REQ-037: Under folio, `preview_view` labels each cell with its source page number and the sheet's signature index, and draws the content-box guide **per cell**.
38. REQ-038: `deckle/app/views/print_dialog.py` gains a signature selector that populates the existing `sheets=` argument, adding no pass-ordering or sheet-cursor arithmetic.
39. REQ-039: `python -m pytest -q` reports zero failures with no fewer than 237 tests collected, and `ruff check deckle tests` exits 0.
40. REQ-040: No AGPL dependency, no external runtime binary, no Qt import in `deckle.core`, and no file/network/print I/O in `deckle/core/layout.py`.
41. REQ-041: A physical folded dummy — one 4-sheet signature printed through the real calibrated `PrinterProfile`, folded, nested — reads front to back in correct page order, and the sewing stations land where an awl wants them.
42. REQ-042: Under folio, `clipped_by_page` is measured against the **cell**, not the sheet.

## Sub-Specs

---
sub_spec_id: SS-01
phase: run
depends_on: []
---

### 1. Model changes — `Side`, `Mark`, `Signature`, `SheetPlan.signatures`, `LayoutSettings`

**Scope:** The pure-data vocabulary v2 needs, and nothing else. No behaviour, only shapes.
Every subsequent sub-spec depends on this one. This sub-spec deliberately *breaks* the tree
— `Sheet.front` changes type — so it lands together with SS-02 in the same wave order and
nothing is expected to run green between them except `tests/test_models.py`.

**Files (modify):**
- `deckle/core/models.py`
- `tests/test_models.py`

**Decisions:** Implement contracts 1–5 and 10 from the *Contracts* table **verbatim**. `Side`
rejects an empty `pages` tuple in `__post_init__` with a `ValueError` naming the invariant
("an absent side is None, never Side(pages=())") — this is C-1 from the evaluation and it is
load-bearing: representing an absent side as `Side(pages=())` would leave the code compiling,
the tests passing, and `print_session._hash_plan` silently wrong. `Mark` carries no colour,
no width and no fill: every mark is a line segment, and stroke width is the renderer's
concern. Do **not** add any `LayoutSettings` field beyond the five committed.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/models.py` defines `@dataclass(frozen=True) class Side` with `pages: tuple[OutputPage, ...]` and `marks: tuple[Mark, ...] = ()`. Satisfies REQ-001.
- `[STRUCTURAL]` `deckle/core/models.py` defines `Sheet` with `front: Side | None` and `back: Side | None`. Satisfies REQ-002.
- `[STRUCTURAL]` `deckle/core/models.py` defines `@dataclass(frozen=True) class Mark` with `kind: Literal["sewing_station","signature_order","fold_line"]` and `x0: float, y0: float, x1: float, y1: float`. Satisfies REQ-004.
- `[STRUCTURAL]` `deckle/core/models.py` defines `@dataclass(frozen=True) class Signature` with `index: int`, `sheet_indices: tuple[int, ...]`, `blank_count: int`. Satisfies REQ-005.
- `[STRUCTURAL]` `SheetPlan` declares `signatures: tuple[Signature, ...] = ()`. Satisfies REQ-006.
- `[STRUCTURAL]` `LayoutWarning.kind`'s `Literal` includes `"sheet_orientation"`, `"signature_padding"`, `"creep_advisory"` and `"landscape_imageable_unverified"` alongside the existing four. Satisfies REQ-007.
- `[STRUCTURAL]` `LayoutSettings` declares exactly `fold_scheme: Literal["none","folio"] = "none"`, `sheets_per_signature: int = 4`, `paper_thickness_pt: float = 0.0`, `sewing_stations: int = 3`, `blank_mode: Literal["end","balanced"] = "end"` as its new fields. Satisfies REQ-008.
- `[MECHANICAL]` `python -m pytest tests/test_models.py -q -k side_rejects_empty_pages` exits 0 — a test asserting `Side(pages=())` raises `ValueError`. Satisfies REQ-003.
- `[MECHANICAL]` `python -m pytest tests/test_models.py -q` exits 0.
- `[MECHANICAL]` `! grep -n "sewing_marks\|sig_order_marks\|fold_lines\|signature_pattern\|sewing_tape_width_pt\|sewing_margin_pt" deckle/core/models.py || (echo "FAIL: uncommitted LayoutSettings field added — this is a human-approval escalation" && exit 1)`
- `[MECHANICAL]` `python -m pytest tests/test_core_purity.py -q` exits 0.

---
sub_spec_id: SS-02
phase: run
depends_on: ['SS-01']
---

### 2. The `Side` refactor across `deckle.core` — zero behaviour change

**Scope:** Make the core tree compile and behave **identically** against the new `Sheet`
shape. `GutterShiftStrategy` wraps each placed page in `Side(pages=(page,))`; `export._sides`
returns `list[Side]` and iterates `side.pages`; `render.py`'s front-then-back page-index
mapping is preserved. **No signature logic, no marks, no cell geometry.** Per the design's
Risks table: *"Do the refactor as a standalone commit with zero behaviour change, verified by
the Pinebox golden, before any signature code exists. If Pinebox drifts, stop."*

This sub-spec exceeds the usual 1–3 file budget deliberately: `Sheet.front`'s type change is
atomic across these three modules and splitting it would leave the tree unimportable
mid-wave.

**Files (modify):**
- `deckle/core/layout.py`
- `deckle/core/export.py`
- `deckle/core/render.py`
- `tests/test_layout.py`
- `tests/test_export.py`
- `tests/test_render.py`
- `tests/test_golden_pinebox.py`

**Decisions:** In `export.py`, `_sides(sheet) -> list[Side]` (currently
`deckle/core/export.py:198`, returning `list[OutputPage]`). `_export_batched` creates **one**
`add_blank_page` per `Side` and calls `_place_output_page` once per page in `side.pages` —
this is the vault recipe's exact structure, where two `place_exact` calls land on one
`add_blank_page`. `_place_output_page` itself **needs no change**: it already takes an
arbitrary `Placement` in sheet coordinates. `_plan_hash` (`export.py:50`) iterates
`side.pages` so the export cache stays content-aware. In `render.py`, the only two reads are
`render.py:82-83`; the front-then-back mapping at `render.py:95-102` is unchanged, because
one `Side` still yields exactly one PDF page. Test edits are **mechanical wrapper edits
only** — `sheet.front.pages[0]` where the test previously read `sheet.front`. Do not change
any assertion's expected value; if an expected value needs changing, stop and escalate.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/export.py`'s `_sides` is annotated `-> list[Side]` and `_export_batched` calls `add_blank_page` exactly once per `Side`. Satisfies REQ-010.
- `[STRUCTURAL]` `GutterShiftStrategy.impose` constructs each side as `Side(pages=(placed,))` and leaves `SheetPlan.signatures` at its default empty tuple.
- `[BEHAVIORAL]` A `GutterShiftStrategy` plan's `Placement` objects — compared by dataclass equality across every sheet, both sides, for a 3-aspect-ratio × 2-binding-edge fixture — are identical to those produced before the refactor. Satisfies REQ-009.
- `[MECHANICAL]` `python -m pytest tests/test_layout.py -q` exits 0 with no fewer than 46 tests. Satisfies REQ-009.
- `[MECHANICAL]` `python -m pytest tests/test_golden_pinebox.py -q` exits 0 (skipping cleanly when `DECKLE_PINEBOX_FIXTURE` is unset and the fixture is absent, per `tests/test_golden_pinebox.py:30-38`). Satisfies REQ-009.
- `[MECHANICAL]` `python -m pytest tests/test_export.py tests/test_render.py -q` exits 0.
- `[MECHANICAL]` `! grep -n "Placement" deckle/core/render.py || (echo "FAIL: renderer reads Placement instead of the exported artifact" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-011.
- `[MECHANICAL]` `! grep -n "add_overlay\|merge_transformed_page\|draw_image" deckle/core/export.py || (echo "FAIL: forbidden composition API present" && exit 1)` — negative assertion: exits 0 when absent.
- `[MECHANICAL]` `! grep -nE "^import (os|io)$|open\(|requests|urllib|socket" deckle/core/layout.py || (echo "FAIL: I/O in layout.py" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-040.
- `[MECHANICAL]` `python -m pytest tests/test_core_purity.py -q` exits 0. Satisfies REQ-040.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-03
phase: run
depends_on: ['SS-01', 'SS-02']
---

### 3. The `Side` refactor in `deckle.app` — per-page clipping is a correctness fix

**Scope:** The two app-layer consumers the unamended design missed (evaluation finding C-2).
`backend.py` must paint **every** page on a side, not one. `preview_view.py`'s clipping
warnings must be computed **per page within a side** — computing them per side would
under-report on a 2-up sheet, which is a correctness gap, not a mechanical one.

**Files (modify):**
- `deckle/app/backend.py`
- `deckle/app/views/preview_view.py`
- `tests/test_backend.py`
- `tests/test_preview_fidelity.py`

**Decisions:** `backend.py:157-158` and `backend.py:168` carry a second copy of `render.py`'s
front-then-back page-index rule; preserve it as-is (one `Side` still yields one PDF page) and
change only what reads a side's content. In `preview_view.py`, `_output_page_bbox`
(`preview_view.py:62-81`) keeps its `OutputPage` signature — it is already per-page and
correct. The change is at `preview_view.py:107`: the loop
`for side_name, output_page in (("front", sheet.front), ("back", sheet.back))` becomes a
nested iteration over `side.pages`, so each page in a side gets its own `clipped_by_page` /
`clipped_by_imageable_area` evaluation. Both warning kinds keep their existing distinct text.
`imageable_rect_pt` (`preview_view.py:44-59`) is unchanged — `imageable_area_pt` is
`(left, top, right, bottom)` **margins**, not a rect, and that convention is pinned in three
places (see the *imageable_area_pt is margins, not a rect* decision-log entry).

**Acceptance criteria:**
- `[BEHAVIORAL]` `_render_sheet_side` in `deckle/app/backend.py` paints every page in `side.pages`; a two-page side produces a raster containing content from both source pages, verified by asserting the placement count rather than by pixel diff. Satisfies REQ-012.
- `[BEHAVIORAL]` `clipping_warnings_for_sheet` on a `Side` holding two pages, where only the **second** page overflows the sheet, returns a `clipped_by_page` warning. (This is the exact under-report a per-side implementation produces and this criterion exists to catch it.) Satisfies REQ-013.
- `[BEHAVIORAL]` `clipping_warnings_for_sheet` reports `clipped_by_page` and `clipped_by_imageable_area` with different text, unchanged from the MVP.
- `[MECHANICAL]` `python -m pytest tests/test_backend.py tests/test_preview_fidelity.py -q` exits 0.
- `[MECHANICAL]` `python -m pytest tests/test_preview_fidelity.py -q` exits 0 — it asserts the preview path routes through `deckle.core.export.export` with `sheets=[sheet_index]`. Satisfies REQ-011.
- `[MECHANICAL]` `! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(PySide6|PyQt)" deckle/core/ || (echo "FAIL: Qt imported in deckle.core" && exit 1)` — negative assertion: exits 0 when absent. **Anchored to import statements deliberately:** the unanchored form matches the "must not import PySide6" prose in `deckle/core/models.py:5` and `deckle/core/__init__.py:3` and fails on a clean tree. Satisfies REQ-040.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-04
phase: run
depends_on: ['SS-01', 'SS-02']
---

### 4. `_hash_plan` content-awareness, and the zero-diff seam assertion

**Scope:** Evaluation finding I-1 plus Success Criterion 2. `print_session._hash_plan`
currently covers sheet index and side *presence* only (`print_session.py:73-86`), so two
different page orderings over the same sheet count hash identically and a resumed
`PrintSession` could bind to a document whose content changed. This is latent in the MVP;
folio doubles the content behind each hash. This is a **deliberate, scoped exception** to the
zero-diff criterion — `print_session.py` changes in exactly one place and `PrintSession`'s
public surface and behaviour are unchanged.

The same sub-spec installs the automated proof that `printing.py` and `profiles.py` did
*not* change. That proof is the design's central claim made falsifiable.

**Files (modify):**
- `deckle/core/print_session.py`
- `tests/test_print_session.py`

**Files (new):**
- `tests/test_seam_zero_diff.py`

**Decisions:** `_hash_plan`'s payload gains, per side, the tuple of
`page.source_ref.page_index` for each page in `side.pages` (using a sentinel such as `null`
for a filler whose `source_ref` is `None`), keeping `s.front is not None` presence as-is so
an absent side still hashes distinctly. Touch nothing else in the module — not `__init__`,
not `start`, not `resume`, not `list_resumable`. `tests/test_seam_zero_diff.py` pins the
SHA-256 of `deckle/core/printing.py` and `deckle/core/profiles.py`, computed from disk at
implementation time, with a module docstring stating that **regenerating a pin is an
escalation, not a maintenance chore** (see *Decision Boundaries*).

**Acceptance criteria:**
- `[BEHAVIORAL]` Two `SheetPlan`s with identical sheet counts and identical side presence, but different source pages on the sides, produce **different** `_hash_plan` values. Satisfies REQ-014.
- `[BEHAVIORAL]` A plan and an exact copy of it produce the **same** `_hash_plan` value (stability across construction).
- `[BEHAVIORAL]` `PrintSession`'s public surface is unchanged: a test asserts `start`, `advance`, `confirm_test_sheet`, `resume`, `load`, `list_resumable`, `state`, `state_path`, `reload_instruction`, `finished` and `last_error` all exist with their MVP signatures. Satisfies REQ-015.
- `[MECHANICAL]` `python -m pytest tests/test_print_session.py -q` exits 0 with no fewer than 10 tests.
- `[MECHANICAL]` `python -m pytest tests/test_seam_zero_diff.py -q` exits 0 — the pinned SHA-256 of `deckle/core/printing.py` and `deckle/core/profiles.py` match the files on disk. Satisfies REQ-015.
- `[MECHANICAL]` `! grep -n "\.front\|\.back\|Side\|Signature\|Mark\|marks" deckle/core/printing.py deckle/core/profiles.py || (echo "FAIL: signature/side concepts leaked into the manual-duplex seam" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-015.
- `[MECHANICAL]` `! grep -rn "PySide6\|QtWidgets" deckle/core/print_session.py || (echo "FAIL: Qt in print_session" && exit 1)` — negative assertion: exits 0 when absent.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-05
phase: run
depends_on: ['SS-01']
---

### 5. `deckle/core/signatures.py` — the arithmetic, and the fold simulator

**Scope:** Pure, I/O-free arithmetic: `split_signatures`, `saddle_order`,
`fold_reading_order`. **Write `fold_reading_order` FIRST**, from the physical description of
folding and nesting, before `saddle_order` exists — the SS-03 write-the-regression-test-first
precedent from the MVP. This is the highest-value code in the feature and the place where the
most expensive possible bug lives.

**Files (new):**
- `deckle/core/signatures.py`
- `tests/test_signatures.py`

**Decisions:** `saddle_order` is the executed, verified function from
`[[pikepdf - Imposition and Signature Recipe]]` (vault path
`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition and Signature Recipe.md`,
lines 35–44), which ran end to end against pikepdf 10.11.0 / libqpdf 12.3.2 on this machine:

```python
def saddle_order(n):
    seq, lo, hi = [], 0, n - 1
    while lo < hi:
        seq += [hi, lo, lo + 1, hi - 1]
        lo += 2
        hi -= 2
    return seq
```

For `n = 8` this is `[7, 0, 1, 6, 5, 2, 3, 4]`, recorded as executed output at line 63 of
that note. Read two at a time: sheet 1 front is `(p8, p1)`, sheet 1 back `(p2, p7)`, sheet 2
front `(p6, p3)`, sheet 2 back `(p4, p5)`. **The outermost sheet carries the first and last
pages** — the defining property of a *nested* (saddle) gathering as opposed to a *stacked*
one. Cite that note in the module docstring, including its warning that the bindery half is
unverified.

**`fold_reading_order` must not reuse `saddle_order`.** The design's war-game names this as
the most likely failure: `saddle_order` subtly wrong in a way `fold_reading_order` encodes
identically, so the round trip passes and the printed book is out of order. Write it by
walking each signature's sheets outermost-to-innermost, unfolding each into its four leaves
in the order a reader encounters them, and concatenating across signatures. A shared
implementation would let both encode the same error and agree.

`split_signatures(sheet_count, sheets_per_signature)` returns contiguous tuples of sheet
indices with the remainder in the final group; `blank_mode="balanced"` is applied by the
caller at the page-padding level (SS-08), not here.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/signatures.py` exposes `split_signatures(sheet_count: int, sheets_per_signature: int) -> list[tuple[int, ...]]`, `saddle_order(n: int) -> list[int]`, and `fold_reading_order(plan: SheetPlan) -> list[int]`.
- `[BEHAVIORAL]` `split_signatures` over sheet counts {1,2,3,4,5,7,8,15,16,17,25,67,100} × sheets-per-signature {1,2,3,4,6,8} asserts, as properties rather than golden values: groups are contiguous; concatenated they equal `range(sheet_count)` exactly; no gaps, no overlaps; only the final group may be short. Satisfies REQ-016.
- `[BEHAVIORAL]` `saddle_order(n)` is a permutation of `range(n)` for every multiple of 4 from 4 to 128. Satisfies REQ-017.
- `[BEHAVIORAL]` `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]` — pinned against the vault note's executed output. Satisfies REQ-017.
- `[BEHAVIORAL]` `saddle_order(n)[:2] == [n - 1, 0]` for every valid `n` — the outermost sheet carries the last and first pages, the defining property of a **nested** gathering. A stacked ordering fails this. Satisfies REQ-017.
- `[BEHAVIORAL]` `saddle_order(n)` raises `ValueError` when `n` is not a positive multiple of 4.
- `[MECHANICAL]` `python -m pytest tests/test_signatures.py -q -k fold_reading_order_does_not_call_saddle_order` exits 0 — an AST test walking `fold_reading_order`'s `FunctionDef` and asserting no `Name` or `Call` node references `saddle_order`. Satisfies REQ-018.
- `[MECHANICAL]` `! grep -nE "^import (os|io)$|open\(|requests|urllib|socket|PySide6" deckle/core/signatures.py || (echo "FAIL: I/O or Qt in signatures.py" && exit 1)` — negative assertion: exits 0 when absent.
- `[MECHANICAL]` `python -m pytest tests/test_signatures.py -q` exits 0 with no fewer than 12 tests.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-06
phase: run
depends_on: ['SS-01']
---

### 6. `deckle/core/marks.py` — bindery marks as pure geometry

**Scope:** Sewing stations, signature order marks and fold lines, computed as `Mark` value
objects in sheet points. **Pure geometry, no drawing** — `layout.py` is I/O-free and a
`[MECHANICAL]` check enforces it, and marks are testable as numbers. Bookbinder JS carries an
open bug ([#135](https://github.com/momijizukamori/bookbinder-js/issues/135), signature order
marks wrong under page rotation) that this architecture makes very hard to have.

**Files (new):**
- `deckle/core/marks.py`
- `tests/test_marks.py`

**Decisions:** Module constants, not settings fields (adding settings fields is a
human-approval escalation): `SEWING_MARGIN_PT = 36.0` (Bookbinder JS's "(A) Margin"),
`STATION_TICK_PT` and `ORDER_BAR_PT` chosen by the implementer. `sewing_tape_width_pt` and
tape-straddling station pairs are **out of scope** — they would need an uncommitted settings
field.

- `sewing_stations(sheet_h: float, fold_x: float, count: int) -> tuple[Mark, ...]` — `count`
  short ticks crossing the fold line, evenly spaced between `SEWING_MARGIN_PT` from the head
  and the same from the tail. `count <= 0` returns `()`, which is how `settings.sewing_stations
  = 0` disables them without a new boolean.
- `signature_order_mark(sig_index: int, sig_count: int, sheet_h: float, fold_x: float) -> Mark`
  — a short bar on the spine fold stepped down by signature index, so a correctly collated
  stack of folded gatherings shows a clean diagonal staircase down the spine and a misordered
  one is instantly visible before a single stitch:
  `step = (sheet_h - 2*margin - bar_h) / max(1, sig_count - 1)`, `y = margin + sig_index * step`.
- `fold_line(sheet_h: float, fold_x: float) -> Mark` — the cell boundary, head to tail. Dashing
  is the renderer's concern (SS-09), not the geometry's.

*Placement, per the design and flagged for SS-13 physical verification:* sewing stations on
the **innermost** sheet of each signature, on its **inner** side — the surface facing you
when the folded gathering lies open, which is where the awl goes in. Signature order marks on
the **outermost** sheet — the surface that becomes the visible spine. `marks.py` computes
geometry only; *which sheet gets which mark* is SS-08's predicate.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/marks.py` exposes `sewing_stations`, `signature_order_mark` and `fold_line`, each returning `Mark` values only.
- `[BEHAVIORAL]` `sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)` returns 3 `Mark`s of kind `"sewing_station"`; their midpoint y-values are evenly spaced; the first is at `SEWING_MARGIN_PT` from the tail and the last at `SEWING_MARGIN_PT` from the head. Satisfies REQ-028.
- `[BEHAVIORAL]` Every returned `sewing_station` mark straddles `fold_x` — its x-interval contains `fold_x`. Satisfies REQ-028.
- `[BEHAVIORAL]` `sewing_stations(..., count=0)` returns `()`, and `count=1` returns a single centred station. Satisfies REQ-028.
- `[BEHAVIORAL]` `signature_order_mark` over `sig_index` 0…`sig_count-1` yields strictly monotonically increasing `y0`, with index 0 at the tail margin and index `sig_count-1` at the head margin. `sig_count == 1` does not divide by zero. Satisfies REQ-029.
- `[BEHAVIORAL]` `fold_line(sheet_h, fold_x)` returns one `Mark` of kind `"fold_line"` with `x0 == x1 == fold_x`, spanning the full sheet height. Satisfies REQ-030.
- `[BEHAVIORAL]` No mark returned by any function lies strictly inside a cell's content box for a letter-landscape folio with default margins — every mark is on or across the fold. Satisfies REQ-028, REQ-030.
- `[MECHANICAL]` `! grep -n "pikepdf\|ContentStreamBuilder\|Canvas\|PySide6\|open(" deckle/core/marks.py || (echo "FAIL: drawing or I/O leaked into marks geometry" && exit 1)` — negative assertion: exits 0 when absent.
- `[MECHANICAL]` `! grep -n "paper_thickness_pt" deckle/core/marks.py || (echo "FAIL: paper_thickness_pt reached mark geometry" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-027.
- `[MECHANICAL]` `python -m pytest tests/test_marks.py -q` exits 0 with no fewer than 10 tests.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-07
phase: run
depends_on: ['SS-02']
---

### 7. Generalise the shared placement math to a `Cell`

**Scope:** `content_box_size`, `content_box_rect_pt`, `actual_margins_pt` and
`document_scale` currently assume the content box is the whole sheet. Each gains a cell
parameter. `GutterShiftStrategy` passes a single full-sheet cell and its behaviour must be
**byte-identical**. Everything the MVP earned inside those functions — the four-margin model,
`slack_to`, the one-rule-for-both-axes placement, the fore-edge/gutter distinction — then
applies **per leaf**, which is exactly where it belongs.

**Generalise, do not duplicate.** Two strategies must never contain two copies of the
placement rule; that is how the verso-only bug in `docs/decisions.md` survived its tests.

**Files (modify):**
- `deckle/core/layout.py`
- `tests/test_layout.py`

**Decisions:** Define `Cell = tuple[float, float, float, float]` (`x0, y0, x1, y1` in sheet
points) in `layout.py` per contract 9. Add the cell parameter **keyword-only with a default of
`None` meaning "the whole sheet"** so no existing call site or test changes shape and the
diff stays reviewable. `_place_page` gains the same cell parameter and an explicit
`spine_side: Literal["left","right"]` argument — under folio the spine is determined by cell
position, not by page parity, so `_gutter_side_is_left` must **not** be reachable from the
folio path. Leave `_gutter_side_is_left` in place for `GutterShiftStrategy`; SS-08 passes
`spine_side` directly.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/layout.py` defines `Cell = tuple[float, float, float, float]` and `document_scale`, `content_box_size`, `content_box_rect_pt` and `actual_margins_pt` each accept a cell parameter defaulting to the full sheet. Satisfies REQ-020.
- `[BEHAVIORAL]` Called with a full-sheet cell, each of the four functions returns exactly what it returned before this sub-spec, for a 4-aspect-ratio × 2-binding-edge × 3-`slack_to` grid. Satisfies REQ-020.
- `[BEHAVIORAL]` `content_box_rect_pt` with cell `(396, 0, 792, 612)` and a right-hand spine returns a rect whose `x0` is `396 + gutter_pt` — margins are measured **inside the cell**, not from the sheet edge. Satisfies REQ-020.
- `[BEHAVIORAL]` `document_scale` computed against a half-width cell is smaller than against the full sheet for the same pages, and remains a single value across the whole page sequence. Satisfies REQ-023.
- `[BEHAVIORAL]` `actual_margins_pt` measures `(inner, outer, top, bottom)` relative to the supplied cell, so a leaf in the right-hand cell reports its inner margin against `x0 = 396`, not `0`. Satisfies REQ-020, REQ-025.
- `[BEHAVIORAL]` `_place_page` accepts an explicit spine side and, when given one, does not consult output-page parity. Satisfies REQ-025.
- `[MECHANICAL]` `python -m pytest tests/test_layout.py -q` exits 0 with no fewer than 46 tests. Satisfies REQ-009.
- `[MECHANICAL]` `python -m pytest tests/test_golden_pinebox.py -q` exits 0. Satisfies REQ-009.
- `[MECHANICAL]` `! grep -nE "^import (os|io)$|open\(|requests|urllib|socket|PySide6" deckle/core/layout.py || (echo "FAIL: I/O or Qt in layout.py" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-040.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-08
phase: run
depends_on: ['SS-05', 'SS-06', 'SS-07']
---

### 8. `SaddleStitchStrategy` — folio 2-up imposition

**Scope:** The strategy itself. Implements `LayoutStrategy` with the **unmodified** signature.
All configuration arrives via `settings`, per the committed contract in
`docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md`.

**Files (modify):**
- `deckle/core/layout.py`

**Files (new):**
- `tests/test_layout_saddle.py`

**Decisions:** The algorithm:

```
impose(pages, settings):
    active   = [p for p in pages if not p.skipped]
    slots    = pad(active, ceil4_total(...))           # EXACTLY ONE padding pass
    sheets_n = len(slots) // 4
    groups   = split_signatures(sheets_n, settings.sheets_per_signature)
    cells    = cell_geometry(settings.paper)           # two cells, split at the fold
    scale    = document_scale(active, settings, cell=cells[0])   # ONE scale, whole document
    for sig_index, group in enumerate(groups):
        order = saddle_order(len(group) * 4)
        for each consecutive pair in order:
            build Side(pages=(place(left_slot,  cells[0], spine_side="right"),
                              place(right_slot, cells[1], spine_side="left")),
                       marks=marks_for(sheet, sig_index, group, settings))
```

**Cell geometry.** The paper is the *sheet*; the fold is its vertical centreline. For letter
landscape (792 × 612) the cells are `(0, 0, 396, 612)` and `(396, 0, 792, 612)`. This matches
the vault recipe's recorded `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm`.

**The spine side inverts, and this is the trap.** In the MVP, `_gutter_side_is_left` derives
the gutter side from output-page parity — index 0 is a recto, spine left, so push right.
Under folio that rule is **wrong and must not be reused**: the spine of a leaf is determined
by where its cell sits relative to the fold.

```
left cell   → spine on its RIGHT edge   (the fold)
right cell  → spine on its LEFT edge    (the fold)
```

This holds on both the front and the back of the sheet. Consequently `settings.binding_edge`
**changes meaning** under `fold_scheme="folio"`: it no longer selects which side of a page
gets the gutter, it selects **reading direction** — `"left"` = left-bound / LTR, `"right"` =
right-bound / RTL, which mirrors which source slot goes in which cell. Document this at the
field and assert it in tests.

**One document-wide scale, still.** Computed once against the *cell* box and applied to every
leaf on every sheet. All cells are identical under folio. Filler pages carry a neutral
`scale_x = 1.0` and are excluded from any "one scale" assertion.

**`blank_mode`.** `"end"` puts the whole shortfall in the final signature. `"balanced"`
shaves the remainder off the tail signatures so no gathering is more than one sheet thinner
than its neighbours. Padding is a **single pass** to the total slot count — never two — per
the predecessor script's defect 2.

**Invariants asserted in `impose` before returning** (the SS-03 precedent of verifying
internal consistency rather than trusting the loop): every signature's slot count is a
multiple of 4; slot counts sum to the padded page count; concatenating the signatures' source
slices reproduces the padded page list *in order*; `sheet_indices` are contiguous across the
whole plan with no gaps and no overlaps.

**Marks.** Sewing stations on the **innermost** sheet of each signature, on its **inner**
side. Signature order marks on the **outermost** sheet. Fold lines on every sheet side.
`settings.sewing_stations <= 0` yields no station marks.

**Creep.** `paper_thickness_pt × sheets_per_signature` gives the predicted fore-edge trim,
surfaced as a `creep_advisory` info warning naming the actionable remedy ("reduce to N sheets
per signature"). **It must never enter placement geometry.** Confine every reference to it in
`layout.py` to a single function named `_creep_advisory`.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/core/layout.py` defines `class SaddleStitchStrategy` with `def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan` — signature character-identical to `LayoutStrategy.impose`. Satisfies REQ-021.
- `[MECHANICAL]` `python -m pytest tests/test_layout_saddle.py -q -k protocol_signature_unchanged` exits 0 — a test asserting `isinstance(SaddleStitchStrategy(), LayoutStrategy)` and that `inspect.signature(SaddleStitchStrategy.impose) == inspect.signature(GutterShiftStrategy.impose)`. Satisfies REQ-021.
- `[BEHAVIORAL]` A 266-page fixture at `sheets_per_signature=4`, `fold_scheme="folio"`, letter landscape produces `len(plan.signatures) == 17`, `len(plan.sheets) == 67`, and exactly 2 filler `OutputPage`s — both in the final signature. Satisfies REQ-022.
- `[BEHAVIORAL]` Every `Side` under folio has `len(side.pages) == 2`, and no side is `Side(pages=())`; a sheet with no back is `back=None`. Satisfies REQ-002.
- `[BEHAVIORAL]` `plan.signatures` sheet indices are contiguous, gapless, non-overlapping, in binding order, and cover `range(len(plan.sheets))` exactly. Satisfies REQ-016, REQ-035.
- `[BEHAVIORAL]` Across the 266-page fixture there is exactly one distinct `scale_x` among non-filler output pages. Satisfies REQ-023.
- `[BEHAVIORAL]` A 7-page and a 17-page document each produce exactly one `signature_padding` warning naming the blank count, and the filler count equals padded-minus-original — never double-padded. Satisfies REQ-024.
- `[BEHAVIORAL]` For a sheet's front, `actual_margins_pt` on the left-cell leaf reports its **inner** margin at the leaf's **right** edge and the right-cell leaf reports **inner** at its **left** edge — and the same holds on the back. Asserted as measured margins, never raw `tx`. Satisfies REQ-025.
- `[BEHAVIORAL]` `binding_edge="right"` produces the exact mirror of `"left"` — the source slot that landed in the left cell now lands in the right. Satisfies REQ-025.
- `[BEHAVIORAL]` `blank_mode="balanced"` on a 250-page document yields no signature more than one sheet thinner than its neighbours; `blank_mode="end"` on the same input puts the whole shortfall in the final signature. Satisfies REQ-024.
- `[BEHAVIORAL]` Portrait letter paper with `fold_scheme="folio"` emits exactly one `sheet_orientation` warning, still returns a full plan, and does not swap `paper_pt`. Satisfies REQ-026.
- `[BEHAVIORAL]` `paper_thickness_pt=0.27`, `sheets_per_signature=8` emits a `creep_advisory` warning whose detail names both the predicted trim and the remedy; `paper_thickness_pt=0.0` emits none. Satisfies REQ-027.
- `[BEHAVIORAL]` No `Placement` produced under folio differs between `paper_thickness_pt=0.0` and `paper_thickness_pt=2.0` — compared by dataclass equality across every page of a 32-page fixture. Satisfies REQ-027.
- `[MECHANICAL]` `python -m pytest tests/test_layout_saddle.py -q -k creep_references_are_isolated` exits 0 — an AST test asserting every `paper_thickness_pt` reference in `deckle/core/layout.py` lies inside the function `_creep_advisory`. Satisfies REQ-027.
- `[BEHAVIORAL]` Under folio, a leaf whose scaled content exceeds its cell emits `clipped_by_page` measured against the **cell**; the same content on the full sheet does not. Satisfies REQ-042.
- `[BEHAVIORAL]` Every sheet side under folio carries a `fold_line` mark; the innermost sheet's inner side carries `settings.sewing_stations` `sewing_station` marks; the outermost sheet carries exactly one `signature_order` mark. Satisfies REQ-028, REQ-029, REQ-030.
- `[BEHAVIORAL]` `sewing_stations=0` produces zero marks of kind `sewing_station` and still produces fold lines.
- `[BEHAVIORAL]` **The fold-simulator round trip.** `fold_reading_order(SaddleStitchStrategy().impose(pages, settings))` equals `list(range(len(pages)))` padded with the plan's blanks, for every combination of page count {1…40, 100, 266} × `sheets_per_signature` {1…8} × `binding_edge` {left, right}. Satisfies REQ-019.
- `[MECHANICAL]` `python -m pytest tests/test_layout_saddle.py -q` exits 0 with no fewer than 25 tests.
- `[MECHANICAL]` `python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q` exits 0 — `GutterShiftStrategy` unmoved. Satisfies REQ-009.
- `[MECHANICAL]` `! grep -nE "^import (os|io)$|open\(|requests|urllib|socket|PySide6" deckle/core/layout.py || (echo "FAIL: I/O or Qt in layout.py" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-040.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-09
phase: run
depends_on: ['SS-02', 'SS-06']
---

### 9. Mark rendering in `export.py` via `pikepdf.canvas.ContentStreamBuilder`

**Scope:** Draw `side.marks` onto each exported PDF page as vector content, after the pages.
Because the preview rasterizes the exported artifact (MVP SS-05), marks are **visible in
preview by construction** — there is no second drawing path to keep in sync.

**Files (modify):**
- `deckle/core/export.py`

**Files (new):**
- `tests/test_export_marks.py`

**Decisions:** Use `pikepdf.canvas.ContentStreamBuilder` (`append_rectangle`,
`stroke_and_close`, `line_width`, `dashes`), confirmed present in the installed pikepdf
10.11.0. Two traps from `[[pikepdf - Canvas API]]` apply: colours are
`pikepdf.canvas.Color`, **never tuples**; and `Canvas.draw_image` is never used here (a
measured 12.6× size blowup — irrelevant for vectors, but the temptation exists for a logo).
**Text is avoided entirely** — every mark is a line segment, so pikepdf's *"rudimentary
interface. You've been warned"* text engine is never in the path and `reportlab` stays out of
the dependency set. `fold_line` marks are dashed; `sewing_station` and `signature_order` are
solid. Marks are appended via `dest_page.contents_add(...)` in their own balanced `q…Q` block
**after** every page placement, so the two-XObject assertion below is unaffected by them.

Constraints carried forward unchanged: never `add_overlay`; never reorder a
`pikepdf.Pdf.pages` list by tuple-swap or slice assignment; `remove_unreferenced_resources()`
before save; batched save-and-reopen at `_BATCH_SHEETS`. `page.Contents` may be an `Array` —
call `contents_coalesce()` before `read_bytes()`. That trap bites tooling, not just
application code (see the *Save PDF, spread preview* decision-log entry).

**Acceptance criteria:**
- `[MECHANICAL]` `grep -n "ContentStreamBuilder" deckle/core/export.py` returns at least one match. Satisfies REQ-031.
- `[MECHANICAL]` `! grep -n "add_overlay\|draw_image\|show_text\|set_font\|reportlab" deckle/core/export.py || (echo "FAIL: forbidden drawing API or text engine present" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-031.
- `[MECHANICAL]` `grep -n "remove_unreferenced_resources" deckle/core/export.py` returns at least one match.
- `[BEHAVIORAL]` A folio sheet whose `Side` carries 3 sewing stations, 1 signature-order mark and 1 fold line exports to a PDF page whose coalesced content stream contains 5 stroke operations beyond the page placements, verified by parsing bytes after `contents_coalesce()`. Satisfies REQ-031.
- `[BEHAVIORAL]` **Two Form XObjects per folio PDF page.** For a two-page `Side` at document scale 1.0, the coalesced content stream contains exactly two balanced `q…Q` blocks carrying a `Do` operator, and each is a pure `1 0 0 1 <tx> <ty> cm` translation with no scale factor — matching `[[pikepdf - Form XObject Placement]]`'s recorded `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm` for exactly this 2-up-on-landscape-letter case. Satisfies REQ-032.
- `[BEHAVIORAL]` At a document scale other than 1.0, the two placement matrices carry that one identical scale in both `a` and `d` positions — no scale drift between the two cells.
- `[BEHAVIORAL]` A `Side` with `marks=()` exports a page with no stroke operations at all — marks are additive, never mandatory.
- `[BEHAVIORAL]` The exported PDF reopens cleanly under `pikepdf.open` and reports the expected page count.
- `[BEHAVIORAL]` A filler `OutputPage` inside a two-page `Side` exports as genuinely blank content while its sibling page still places, so a padded final signature is not corrupted.
- `[MECHANICAL]` `python -m pytest tests/test_export.py tests/test_export_marks.py -q` exits 0.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-10
phase: run
depends_on: []
---

### 10. License-audit denylist, and the pdfimpose oracle that cannot become a dependency

**Scope:** Extend `tests/test_license_audit.py` so a developer who installs the oracle in the
project venv gets a **red test** rather than a shipped AGPL dependency, and add the
development-time oracle itself. **The denylist lands before the oracle exists** — that
ordering is the point.

`pdfimpose` is AGPL-3.0 and pulls AGPL PyMuPDF. The MVP spec names this as *a live temptation,
not a hypothetical*. It is excellent to run personally and is used here as a development-time
reference — never as a dependency of anything shipped or tested.

**Files (modify):**
- `tests/test_license_audit.py`

**Files (new):**
- `tools/oracle_diff.py`
- `tools/README.md`

**Decisions:** `tests/test_license_audit.py:33` currently reads
`FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz"}`. Extend it to
`{"pymupdf", "fitz", "pdfimpose", "cpdf"}`, keeping the existing dual check — the
distribution name **or** an intersection with the distribution's `top_level.txt` module
names — and keeping the separate AGPL substring test over license text and classifiers
(`test_no_agpl_dependency`, currently at line 105).

`tools/oracle_diff.py` lives **outside `tests/`**, is **absent from `pyproject.toml`**, and is
run manually in a throwaway virtualenv. It drives
`pdfimpose.schema.saddle.impose(files, output, signature=(2,1), group=N, bind="left")` on a
numbered fixture and diffs pdfimpose's resulting source-page → (sheet, side, cell) matrix
against Deckle's. Its `impose()` accepts `io.BytesIO` at both ends, so the diff needs no temp
files. pdfimpose is one of only two surveyed tools that split signatures correctly and it
never rescales content, so it is a genuinely good reference for the one thing being checked.
`tools/README.md` states in its first paragraph that installing `pdfimpose` into the project
venv will fail the license audit **by design**.

**Acceptance criteria:**
- `[STRUCTURAL]` `tests/test_license_audit.py`'s `FORBIDDEN_DISTRIBUTIONS` contains `"pymupdf"`, `"fitz"`, `"pdfimpose"` and `"cpdf"`. Satisfies REQ-033.
- `[MECHANICAL]` `python -m pytest tests/test_license_audit.py -q` exits 0 with no fewer than 3 tests. Satisfies REQ-033.
- `[BEHAVIORAL]` A test injects a fake distribution named `pdfimpose` into the closure and asserts the denylist test **fails** — proving the denylist is wired, not merely declared. Satisfies REQ-033.
- `[MECHANICAL]` `! grep -n "pdfimpose\|cpdf\|pymupdf\|fitz" pyproject.toml || (echo "FAIL: AGPL/oracle distribution declared as a dependency" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-034.
- `[MECHANICAL]` `! grep -rn "oracle_diff" deckle/ tests/ || (echo "FAIL: the dev-time oracle is imported by shipped or tested code" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-034.
- `[MECHANICAL]` `! grep -rn "PyMuPDF\|import fitz\|import pdfimpose" deckle/ tests/ || (echo "FAIL: AGPL import present" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-040.
- `[STRUCTURAL]` `tools/README.md` states that `tools/` is development-only, that `pdfimpose` is AGPL-3.0, that it must be installed only in a throwaway virtualenv, and that installing it in the project venv fails `tests/test_license_audit.py` by design. Satisfies REQ-034.
- `[STRUCTURAL]` `tools/oracle_diff.py` imports `pdfimpose` **inside** a function body, never at module scope, and prints a clear "install pdfimpose in a throwaway venv" message on `ImportError`.
- `[MECHANICAL]` `ruff check deckle tests tools` exits 0.

---
sub_spec_id: SS-11
phase: run
depends_on: ['SS-03', 'SS-08']
---

### 11. UI surface — Binding group, per-cell preview, signature print selector

**Scope:** The three app-layer affordances that make signatures usable. No new print logic:
per-signature printing is a UI affordance over machinery that already exists and is already
tested (`export(plan, path, sheets=[...])`, `plan_passes(plan, profile, sheets=[...])` and
`PrintSession(..., sheets=[...])` all already accept a sheet subset, and MVP SS-11 makes the
subset path *the normal path with a smaller input, not a separate branch*).

**Files (modify):**
- `deckle/app/views/layout_panel.py`
- `deckle/app/views/preview_view.py`
- `deckle/app/views/print_dialog.py`

**Decisions:** Follow `layout_panel.py`'s existing construction pattern exactly — there are no
`QGroupBox`es today; everything is rows on the single `QFormLayout` created at
`layout_panel.py:189`. Add pure `set_*` mutators in the Qt-free half of the module (the
pattern at `layout_panel.py:32-107`, each `replace(project, layout=replace(project.layout,
<field>=<value>))`), wire widgets in the single connect block at `layout_panel.py:260-270`,
and route every handler through `apply_layout_change(self.state, mutator)` followed by
`self.layout_changed.emit(plan)`. Any new unit-aware spinbox must also be registered in
`_on_unit_changed`'s `boxes` list (`layout_panel.py:329-341`).

The live readout — *"17 signatures · 67 sheets · 2 blanks"* — is derived from the recomputed
`SheetPlan` (`plan.signatures`, `plan.sheets`, and the filler count), because that arithmetic
is the thing a binder actually decides on.

In `preview_view.py`, the content-box guide currently drawn once per side at
`preview_view.py:634-637` is drawn **per cell** under folio, and each cell is labelled with
its source page number plus the sheet's signature index — so a mis-ordered gathering is
visible on screen, not only on paper.

In `print_dialog.py`, `PrintSession` is currently constructed at `print_dialog.py:198-204`
with `sheets` falling through to its `None` default. Add a signature selector that populates
`sheets=` with `signature.sheet_indices`. **The dialog gains no arithmetic** — the existing
`[MECHANICAL]` check forbidding `reverse`, `sheet_order` and `% 2` in that file still holds.

**Acceptance criteria:**
- `[STRUCTURAL]` `deckle/app/views/layout_panel.py` exposes Qt-free mutators `set_fold_scheme`, `set_sheets_per_signature`, `set_blank_mode`, `set_sewing_stations` and `set_paper_thickness_pt`, each returning a new `Project` via `replace`. Satisfies REQ-036.
- `[BEHAVIORAL]` Setting `fold_scheme="folio"` through the panel recomputes the plan via `apply_layout_change` and emits `layout_changed` with a `SheetPlan` whose `signatures` is non-empty. Satisfies REQ-036.
- `[BEHAVIORAL]` `recompute_plan` dispatches to `SaddleStitchStrategy` when `layout.fold_scheme == "folio"` and to `GutterShiftStrategy` when it is `"none"`. Satisfies REQ-021, REQ-036.
- `[BEHAVIORAL]` A pure helper renders the binding readout string for a 266-page folio plan as text containing `17`, `67` and `2` — tested headlessly, with no Qt widget constructed. Satisfies REQ-036.
- `[BEHAVIORAL]` Under folio, `preview_view` produces one content-box guide rect **per cell** (two per side) and one label per cell carrying its source page number and the sheet's signature index. Satisfies REQ-037.
- `[BEHAVIORAL]` Under `fold_scheme="none"`, `preview_view` produces exactly one content-box guide per side — MVP behaviour unchanged. Satisfies REQ-037.
- `[BEHAVIORAL]` Selecting signature *k* in the print dialog constructs `PrintSession` with `sheets=` equal to that signature's `sheet_indices`; selecting "All" passes `sheets=None`. Satisfies REQ-038.
- `[BEHAVIORAL]` `plan_passes(plan, profile, sheets=plan.signatures[3].sheet_indices)` returns two passes whose `sheet_order`s together cover exactly that signature's sheets and no others. Satisfies REQ-035.
- `[MECHANICAL]` `! grep -n "reverse\|sheet_order\|% 2" deckle/app/views/print_dialog.py || (echo "FAIL: ordering logic leaked into the print UI" && exit 1)` — negative assertion: exits 0 when absent. Satisfies REQ-038.
- `[MECHANICAL]` `python -m pytest tests/test_print_dialog.py tests/test_preview_fidelity.py -q` exits 0.
- `[MECHANICAL]` `ruff check deckle tests` exits 0.

---
sub_spec_id: SS-12
phase: run
depends_on: ['SS-04', 'SS-08', 'SS-09', 'SS-10', 'SS-11']
---

### 12. Integration, the end-to-end fold round trip, and the full-suite gate

**Scope:** Wire the feature into its entry points, prove the whole path end to end, and hold
the line on every constraint. This is the sub-spec that turns parts into a feature.

**Files (new):**
- `tests/test_integration_signatures.py`

**Files (modify):**
- `deckle/cli.py`
- `docs/CONTRIBUTING.md`

**Decisions:** `deckle/cli.py` gains `--fold-scheme`, `--sheets-per-signature`, `--blank-mode`
and `--sewing-stations` on the `impose` and `export` subcommands, and `info` reports the
signature breakdown. The CLI imports only `deckle.core` — never `deckle.app`; the existing
negative grep still holds. `docs/CONTRIBUTING.md` gains a short section naming the five-noun
page model (source → output → **side** → sheet → **signature** → pass), the zero-diff seam
rule for `printing.py`/`profiles.py`, and the "`paper_thickness_pt` is advisory only" rule.

**Acceptance criteria:**
- `[INTEGRATION]` An end-to-end test drives: load a numbered 32-page fixture → set `fold_scheme="folio"`, `sheets_per_signature=4` → `SaddleStitchStrategy().impose` → `export()` → reopen the exported PDF → assert 8 sheets, 16 PDF pages, two placements per page → `plan_passes(plan, profile, sheets=plan.signatures[1].sheet_indices)` → submit through a stubbed `PrintBackend` and assert the received sheet order. Satisfies REQ-010, REQ-032, REQ-035.
- `[INTEGRATION]` `fold_reading_order(plan) == list(range(32))` for that same exported plan — the fold simulator closes over the *real* imposed plan, not a synthetic one. Satisfies REQ-019.
- `[INTEGRATION]` `deckle/core/signatures.py` and `deckle/core/marks.py` are both imported and invoked from `deckle/core/layout.py`'s `SaddleStitchStrategy`, and `deckle/core/marks.py`'s output is consumed by `deckle/core/export.py` — asserted by a test that patches each module's public functions and confirms they are called during a full folio impose-and-export. **No orphaned modules.** Satisfies REQ-021, REQ-031.
- `[MECHANICAL]` `python -m deckle.cli info tests/fixtures/sample.pdf --fold-scheme folio --sheets-per-signature 4` exits 0 and prints a signature count, sheet count and blank count.
- `[MECHANICAL]` `python -m deckle.cli export tests/fixtures/sample.pdf -o "$TMPDIR/sig.pdf" --fold-scheme folio --sheets-per-signature 4` exits 0 and produces a readable PDF.
- `[MECHANICAL]` `! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(deckle\.app|PySide6|PyQt)" deckle/cli.py || (echo "FAIL: app or Qt import in the CLI" && exit 1)` — negative assertion: exits 0 when absent. **Anchored to import statements deliberately:** the MVP spec's unanchored version of this criterion (`docs/specs/2026-08-04-deckle-mvp.md`, SS-07) matches an explanatory comment at `deckle/cli.py:32` and the `_VERSIONED_DISTRIBUTIONS` tuple at `deckle/cli.py:33`, and therefore **fails on the current clean tree**. Do not copy the unanchored form forward. Satisfies REQ-040.
- `[MECHANICAL]` `python -m pytest -q` exits 0 with no fewer than 237 tests collected. Satisfies REQ-039.
- `[MECHANICAL]` `ruff check deckle tests tools` exits 0. Satisfies REQ-039.
- `[MECHANICAL]` `python -m pytest tests/test_license_audit.py tests/test_core_purity.py tests/test_seam_zero_diff.py tests/test_golden_pinebox.py -q` exits 0 — the four constraint gates in one command. Satisfies REQ-009, REQ-015, REQ-033, REQ-040.
- `[STRUCTURAL]` `docs/CONTRIBUTING.md` documents the extended page model, the `printing.py`/`profiles.py` zero-diff rule, and that `paper_thickness_pt` is advisory-only.

---
sub_spec_id: SS-13
phase: run
depends_on: ['SS-12']
dispatch: manual
---

### 13. The physical folded dummy — `[HUMAN REVIEW]`, and the gate

**Scope:** Print one 4-sheet signature of a numbered fixture on the real printer, through the
existing calibrated `PrinterProfile` and `PassPlanner`. Fold. Nest. Read 1→16. Then prick the
sewing stations and confirm they land where an awl wants them. **This gates the feature.**

**`dispatch: manual`.** It needs a printer and a human reading paper, and it is the **only
real check on `saddle_order`**. An agent cannot close it. `[[pikepdf - Imposition and
Signature Recipe]]` states this in as many words: *"the pikepdf half is confirmed, the bindery
half is yours."* No amount of green pytest substitutes for folding paper, and
`docs/decisions.md` (*Added run.bat; verified the GUI genuinely launches*) records this
project already learning once that passing tests are not observation.

**Files (new):**
- `docs/verification/2026-08-04-folded-dummy.md`

**Files (modify):**
- `docs/decisions.md`

**Decisions:** Use a **numbered-pages** fixture — the approach Bookbinder JS ships for exactly
this (`/docs/example_page_numbers.pdf`). Calibrate on a throwaway, never on the manuscript.
While the printer is out, answer the deferred question from the evaluation's escalation table:
measure the **landscape** imageable area against the portrait-measured profile. It costs one
extra test sheet, and a `PrinterProfile` shape change is a human-approval item that only
hardware can answer.

**Acceptance criteria:**
- `[HUMAN REVIEW]` A 16-page numbered fixture imposed at `sheets_per_signature=4` and printed through the real `PrinterProfile` folds, nests, and reads **1 → 16** in correct order. Satisfies REQ-041.
- `[HUMAN REVIEW]` The sewing stations printed on the innermost sheet's inner side land where an awl actually enters an opened gathering — confirmed by pricking the dummy. If they do not, record where they should be; this is one predicate in `marks.py` and one default. Satisfies REQ-041.
- `[HUMAN REVIEW]` The signature order marks on a correctly collated stack of folded gatherings form a clean diagonal staircase down the spine, and a deliberately misordered stack is visibly wrong. Satisfies REQ-029, REQ-041.
- `[HUMAN REVIEW]` The measured **landscape** imageable area is recorded and compared against the portrait-measured `PrinterProfile.imageable_area_pt`. If they differ materially, that is an escalation for a `PrinterProfile` shape change — do not implement it here.
- `[STRUCTURAL]` `docs/verification/2026-08-04-folded-dummy.md` records: the fixture used, the printer, the profile, the observed reading order, the awl verdict, the staircase verdict, and the measured landscape imageable area — each marked confirmed / diverged / untested.
- `[STRUCTURAL]` `docs/decisions.md` gains one entry for this run following the file's existing Symptom / Fix / Surfaces / Watch / Commit structure.
- `[HUMAN REVIEW]` **If the dummy reads out of order, STOP.** The arithmetic is wrong and no amount of test coverage would have shown it. Do not adjust the fold simulator to agree with `saddle_order` — that is the shared-wrong-assumption failure this whole design is structured to prevent. Escalate.

## Edge Cases

**Disambiguations** — abstract language resolved so agents do not guess. Each cites the
Intent trade-off it derives from.

- **"handles a page count that is not a multiple of `4 × sheets_per_signature`"** →
  **Permissive.** Pad to the next multiple of 4 in the affected signature, in **exactly one
  pass**, and emit one `signature_padding` warning naming the count and location. Never
  reject. *(Per Intent 2, honest reporting; and the predecessor script's defect 2, where
  silent double-padding was the bug.)*
- **"handles portrait paper under folio"** → **Permissive, warn only.** The output is legal
  and almost never intended. Emit `sheet_orientation`; do **not** rotate the paper
  automatically and do **not** block. *(Per Intent 2, and `docs/decisions.md` —
  *Path-traversal validation must advise, not refuse* — which records what happens when a
  warning is implemented as a refusal: every real project hard-failed on reopen.)*
- **"validates `sheets_per_signature`"** → **Strict at the type boundary, permissive above
  it.** `saddle_order(n)` raises `ValueError` for `n` not a positive multiple of 4 — it is a
  programming error, not user input. `sheets_per_signature <= 0` from settings is **clamped
  to 1** with a warning rather than raised, because it arrives from a UI spinbox.
  *(Per Intent 6 and the MVP's permissive-at-import rule.)*
- **"supports bindery marks"** → **Derived, not configured.** With no committed
  `sewing_marks` / `sig_order_marks` / `fold_lines` settings fields (adding them is a
  human-approval escalation), marks are on under `fold_scheme="folio"` and absent under
  `"none"`. `settings.sewing_stations = 0` is the disable switch for stations specifically.
  *(Per the Decision Authority: no uncommitted `LayoutSettings` fields.)*
- **"handles creep"** → **Configurable-by-data, defaulting to silent.** `paper_thickness_pt`
  defaults to `0.0`, so no advisory fires until the user supplies a real thickness. When it is
  non-zero, report `paper_thickness_pt × sheets_per_signature` as a `creep_advisory` naming
  the remedy. **Never compensate.** *(Per Intent 2. pdfimpose's own help text for its creep
  option reads "⚠ Warning ⚠ This option is broken"; Stirling's issue #705 has been open since
  January 2024.)*
- **"integrates with the print path"** → **No new branch.** Per-signature printing is
  `sheets=signature.sheet_indices` through the existing subset path. *(Per Intent 3.)*
- **"error handling"** → Unchanged from the MVP's three tiers: **blocking** (modal, names the
  file), **warning** (badge on the affected sheet), **info** (session log). Warnings are never
  modal, and every warning carries a `sheet_index`.

**Specific scenarios:**

- **A side would hold zero pages** → impossible by construction; `Side.__post_init__` raises
  `ValueError`. An absent side is `None`. *(C-1: this is what keeps `_hash_plan` honest.)*
- **A signature would contain zero sheets** → `split_signatures` never emits an empty group;
  a `sheets_per_signature` larger than the sheet count yields exactly one group covering
  every sheet.
- **One-page document under folio** → pads to 4 slots, 1 sheet, 1 signature, 3 blanks. Legal
  and covered by the round-trip grid.
- **`sheets_per_signature=1`** → a gathering of one sheet, repeated. This **is**
  fold-individually-and-stack; no second enum value is needed for it. *(Per
  `docs/decisions.md`, *Deleted the scale mode* — a control that changes nothing.)*
- **`sheets_per_signature > 8` at 20 lb** → the gathering cannot be cleanly folded. Advise via
  `creep_advisory` attached to the first sheet of each affected signature. Do not block:
  heavy paper and thin paper differ.
- **Halved cell width makes clipping the normal case** → expected and honest. Content sized
  for a full letter page will not fit a half-letter cell without shrinking; the one-scale rule
  handles it correctly. Frame the warning text as expected-under-folio rather than reusing the
  1-up phrasing, so warning fatigue does not train the user to ignore warnings.
- **Cell falls outside the printer's imageable area** → existing
  `clipped_by_imageable_area`. Landscape feed has a different imageable area than portrait on
  most printers. v2 emits a `landscape_imageable_unverified` info warning when a
  portrait-measured profile is used with landscape paper; the real measurement happens in
  SS-13.
- **Filler pages and the "one scale" assertion** → fillers carry a neutral `scale_x = 1.0` and
  **must be excluded** from any one-document-scale assertion. A test asserting one distinct
  scale already failed on exactly this once (`docs/decisions.md`, *Uniform document-wide
  scale*).
- **`page.Contents` is an `Array`** → call `contents_coalesce()` before `read_bytes()`. It
  bites tooling, not just application code.
- **`page.mediabox` returns an `Array`, not a `Rectangle`** → `.width` raises. Always wrap:
  `Rectangle(page.mediabox)`. Use `Rectangle(page.trimbox)` as the *source* box — the trim box
  is the finished page, and pikepdf synthesizes one equal to the mediabox when absent.
- **Reordering `pdf.pages`** → never by tuple-swap or slice assignment; pikepdf treats both as
  copies and breaks bookmarks and links.
- **`GutterShiftStrategy` plans have `signatures == ()`** → every consumer must tolerate an
  empty tuple. Do not branch on truthiness in a way that treats "no signatures" as an error.
- **Design-doc prose contradicts the committed contracts** → the committed contracts win; the
  seven known conflicts are enumerated in *Contracts* above. Do not re-derive them.
- **A forbidden-token grep matches your own comment** → a real, already-realised hazard in
  this repo: the MVP's `! grep -rn "deckle.app\|PySide6" deckle/cli.py` criterion fails on the
  current clean tree because `deckle/cli.py:32-33` *mentions* `PySide6` in a comment and in
  the `_VERSIONED_DISTRIBUTIONS` tuple, and the same unanchored pattern over `deckle/core/`
  matches the "must not import PySide6" docstrings at `models.py:5` and `__init__.py:3`. Two
  rules follow. **First**, the criteria in this spec that forbid *imports* are anchored to
  `^\s*(import|from)\s+…`; keep them that way. **Second**, when writing new code, do **not**
  name a forbidden token in a comment or docstring in a file that a `[MECHANICAL]` criterion
  greps — say "the whole-page overlay/watermark helper" rather than `add_overlay`, exactly as
  `deckle/core/export.py`'s existing module docstring already does. A criterion that fails on
  correct code is indistinguishable from one that caught a real defect, and factory run
  `c46e15e3` shows what that costs.

## Out of Scope

Not in v2, each with the reason it is out:

- **Quarto, octavo, sextodecimo** (2, 3, 4 folds). Need a 2D cell grid, per-cell 180°
  rotation, and general fold-sequence machinery — the exact thing HornPenguin left unfinished
  since [issue #1, September 2022](https://github.com/HornPenguin/Booklet/issues/1). Folio
  first; the `fold_scheme` enum is the seam. Under folio, `Placement.rotate_deg` stays `0`, so
  `export._rotation_matrix` stays out of the signature code path entirely.
- **A separate `hardcover` / stack-don't-nest scheme.** `sheets_per_signature=1` already
  expresses it.
- **`cutstackfold`, `copycutfold`, `wire`, `cards`, `onepagezine`** — pdfimpose's other
  schemas. Different physical operations, none of which the user performs.
- **Perfect binding.** No glue-up in this workflow yet.
- **Creep compensation.** Reported, never applied.
- **Trim, bleed, CMYK and registration marks.** For a commercial press, not a home laser
  printer.
- **Top-edge binding.** Deferred from the MVP (red-team P-5); a folio fold does not change
  that analysis.
- **Automatic paper rotation for folio.** Warn, do not rotate.
- **Vendoring any third-party source.** Decided: no.
- **`signature_pattern` (explicit per-signature sheet counts) and its two blocking
  validations.** Would require an uncommitted `LayoutSettings` field. The blocking error
  paths for "pattern does not sum to cover the document" and "a signature would contain zero
  sheets" therefore have no trigger in v2 and are not implemented.
- **`sewing_tape_width_pt` and tape-straddling station pairs.** Same reason.
- **A "print sewing stations on every folio" option.** Same reason. Default only: innermost
  sheet, inner side.
- **Per-signature PDF *files* (`sig-01_side1.pdf`).** In-app per-signature printing is enough;
  this is a CLI/UI affordance over `export(..., sheets=...)` with no core change, and is only
  relevant if the user ever prints from something other than Deckle.
- **Shipping the fold simulator as a UI feature.** It stays a pure function used by tests. It
  would be a reassuring "read the book back" preview, but it is not needed to make paper come
  out right.
- **Splitting `binding_edge` into a separate `reading_direction` field.** It would need a
  `LayoutSettings` change and the project's first `.deckle` migration. Document the
  strategy-dependent meaning at the field and assert it in tests instead; revisit only if it
  confuses in practice.
- **`PrinterProfile` per-orientation imageable area.** Deferred; only hardware answers it.
  SS-13 measures it and the result is an escalation, not an implementation.
- **Packaging, installers and PyInstaller specs.** Unchanged from the MVP: out.

## Constraints

**Musts:**
- `SaddleStitchStrategy` MUST implement `LayoutStrategy` with the **unmodified** signature
  `impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`.
- Signatures MUST occupy **contiguous runs of sheet indices, in binding order**. `plan_passes`
  reverses the whole sheet list when `profile.reverse_stack` is true; contiguity is what makes
  a per-signature subset reverse correctly in isolation as well as in aggregate.
- There MUST be **one document-wide scale** (`document_scale`), generalised to a cell and
  applied to every leaf.
- An absent side MUST be `None`. `Side(pages=())` MUST be rejected at construction.
- Padding MUST run **exactly one pass**.
- The `Side` refactor (SS-02, SS-03) MUST land with **zero behaviour change**, verified by the
  Pinebox golden fixture, **before** any signature code exists. If Pinebox drifts, stop.
- Every negative `[MECHANICAL]` criterion MUST be written
  `! grep … || (echo "FAIL: …" && exit 1)` so it exits 0 on the passing case.
- `python -m pytest -q` MUST pass with no fewer than 237 tests collected, and
  `ruff check deckle tests` MUST exit 0, at the end of every sub-spec that touches code.

**Must-Nots:**
- MUST NOT change `LayoutStrategy.impose`'s signature.
- MUST NOT introduce AGPL. `pdfimpose` is a dev-time oracle in a throwaway venv and is
  denylisted so it can never become a dependency.
- MUST NOT add an external runtime binary. This is what disqualifies vendoring HornPenguin
  (`pdf2image` → Poppler).
- MUST NOT import Qt into `deckle.core`, or perform file/network/print I/O in
  `deckle/core/layout.py`.
- MUST NOT let `paper_thickness_pt` reach placement geometry — warning path only, confined to
  `_creep_advisory` and grep-/AST-enforced.
- MUST NOT recompute, adjust or clamp a `Placement` outside the imposer.
- MUST NOT diff `deckle/core/printing.py` or `deckle/core/profiles.py`.
- MUST NOT reuse `saddle_order` inside `fold_reading_order`.
- MUST NOT reuse `_gutter_side_is_left` (output-page parity) to decide the spine under folio.
- MUST NOT add a `LayoutSettings` field beyond the five committed.
- MUST NOT use `add_overlay`, `Canvas.draw_image`, pikepdf text drawing, or `reportlab`.
- MUST NOT reorder a `pikepdf.Pdf.pages` list by tuple-swap or slice assignment.

**Preferences:**
- Prefer **generalising** a shared function over adding a second copy of the placement rule.
- Prefer **measured margins** (`actual_margins_pt`) over raw `tx`/`ty` in every assertion.
- Prefer **property assertions** (permutation, contiguity, monotonicity) over golden values in
  the arithmetic tests.
- Prefer a **keyword-only cell parameter with a full-sheet default** over changing existing
  call sites, so the SS-07 diff stays reviewable.
- Prefer **warning** over refusing whenever the output is legal but probably unwanted.
- Prefer **parametrising over aspect ratios and both binding edges**, following the existing
  `tests/test_layout.py` style.

**Escalation Triggers:**
- A diff appears in `printing.py` or `profiles.py`: stop and surface.
- `fold_reading_order` and the imposition disagree without an obvious single-sided bug: stop
  and surface. **Do not "fix" the simulator to agree.**
- The physical dummy (SS-13) reads out of order: stop and surface.
- The `.deckle` format would need to change: stop and surface.
- A dependency addition, a `PrintSession` public-surface change, a `PrinterProfile` shape
  change, or a new `LayoutSettings` field is needed: stop and surface.
- The test count would fall below 237, or `ruff` would need a suppression: stop and surface.

## Verification

End to end, cheapest first, with a physical gate at the end:

1. `python -m pytest tests/test_models.py tests/test_signatures.py tests/test_marks.py -q` —
   the pure arithmetic and geometry, milliseconds.
2. `python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q` — proof that
   `GutterShiftStrategy` did not move. If this is red, **stop**; the refactor broke the MVP.
3. `python -m pytest tests/test_layout_saddle.py -q` — the strategy, including the
   **fold-simulator round trip** over page count {1…40, 100, 266} × sheets-per-signature
   {1…8} × binding edge {left, right}. This is the highest-value test in the feature: it
   asserts *physical intent* rather than a computed value, and it is written from the physical
   fold description without reference to `saddle_order`, so it survives a bug that the
   imposition code and its arithmetic tests would encode identically.
4. `python -m pytest tests/test_export_marks.py tests/test_export.py -q` — two balanced `q…Q`
   pure-translation blocks per folio PDF page, and marks as stroke operations, asserted by
   parsing bytes after `contents_coalesce()`.
5. `python -m pytest tests/test_seam_zero_diff.py tests/test_core_purity.py tests/test_license_audit.py -q`
   — the four hard constraints: zero-diff seam, no Qt in core, no AGPL, no oracle leak.
6. `python -m pytest -q` — zero failures, no fewer than 237 tests collected.
7. `ruff check deckle tests tools` — exits 0.
8. `python -m deckle` — launch the GUI, set fold scheme to folio, confirm the readout says
   *"17 signatures · 67 sheets · 2 blanks"* on the 266-page fixture, scrub the preview and see
   two labelled cells with a fold line and sewing stations, print one signature.
9. **Optional, manual, throwaway venv:** `python tools/oracle_diff.py` against a numbered
   32-page fixture — diff pdfimpose's source-page → (sheet, side, cell) matrix against
   Deckle's. Never run in the project venv; the license audit will fail by design.
10. **SS-13, and the only check that counts.** Print one 4-sheet signature of a numbered
    fixture on the real printer through the calibrated `PrinterProfile`. **Fold. Nest. Read
    1→16. Prick.** Record the result in `docs/decisions.md`.

`fold_reading_order(impose(pages, settings)) == list(range(len(pages))) + [None] * blanks`
— fold the plan back up and you get the book. Everything else in this spec is in service of
that one line, and step 10 is the only thing that proves it.
