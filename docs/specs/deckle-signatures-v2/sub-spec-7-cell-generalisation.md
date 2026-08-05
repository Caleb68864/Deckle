# SS-07 — Generalise the shared placement math to a `Cell`

**Parent spec:** `docs/specs/2026-08-04-deckle-signatures-v2.md` (section 7)
**Contracts:** `docs/specs/deckle-signatures-v2/contracts.yaml` — `Cell`
**Phase:** run
**Depends on:** SS-02
**Satisfies:** REQ-009 (preserved), REQ-020, REQ-023 (enabling half), REQ-025 (enabling half)

---

## 1. Context

**What this sub-spec does:**
Four functions in `deckle/core/layout.py` currently assume the content box is the whole sheet:

- `content_box_size(settings)` (`layout.py:81`)
- `document_scale(pages, settings)` (`layout.py:109`)
- `content_box_rect_pt(settings, *, is_recto)` (`layout.py:257`)
- `actual_margins_pt(output_page, paper, *, is_recto, binding_edge)` (`layout.py:283`)

plus the private `_place_page` (`layout.py:139`). Each gains a **keyword-only `cell` parameter
defaulting to `None`, meaning "the whole sheet"**. `GutterShiftStrategy` passes a full-sheet
cell (or nothing, which means the same). `_place_page` additionally gains an explicit
`spine_side: Literal["left", "right"] | None = None`.

**Why it matters.** Everything the MVP earned inside those four functions — the four-margin
model, `slack_to`, the one-rule-for-both-axes placement, the fore-edge/gutter distinction —
then applies **per leaf**, which is exactly where it belongs. SS-08 gets a working half-sheet
placement engine for free instead of writing a second one.

**Generalise, do not duplicate. This is the load-bearing instruction in this sub-spec.**
The spec's Intent hierarchy puts *one implementation of every geometry rule* above local
convenience, and `docs/decisions.md` records the reason in an entry titled
*fixed_gutter put the reserved gutter on the wrong side of the verso* — the recorded instance
of one geometry rule being quietly relied on by a second code path. Two strategies must never
contain two copies of the placement rule. If you find yourself writing
`_place_page_in_cell(...)` beside `_place_page(...)`, stop: that is the exact defect shape.

**Two decision-log entries live inside this code. Do not disturb them.**

| `docs/decisions.md` entry | What it pins in this file | How to avoid breaking it |
|---|---|---|
| *Uniform document-wide scale; per-page scaling resized the text* | `document_scale` computes **one** scale for the whole document — the largest that fits every page. Per-page scaling made the Traveller cover's text 2.5% larger than the body's. | The `cell` parameter changes *which box* the scale is fitted to. It must **not** turn the scale per-page or per-cell-instance. Under folio all cells are identical, so one call with one cell still yields one scale. Filler pages carry a neutral `scale_x = 1.0` and are excluded from any "one scale" assertion. |
| *Rebuilt the placement math on one rule for both axes* | Margins are minimums; spare space inside the box is shared equally between opposing margins so their difference is preserved; on overflow the specified margin is held and the overflow goes to the opposite edge. `actual_margins_pt` exists so tests measure margins rather than raw `tx`/`ty`. | Translate the box, do not re-derive it. Every new expression should be the old one with `paper_w` → `cell_w`, `0.0` → `cell_x0`. Assert **measured margins** via `actual_margins_pt`, never raw `tx`/`ty` — a coordinate assertion on one edge of one page is how a verso-only bug survived the original suite. |

**The hard constraint: `GutterShiftStrategy`'s output must not move.** Not "should not" —
must not. The Pinebox golden fixture and all 46 existing `tests/test_layout.py` test functions
(81 parametrised cases) must pass **untouched**, with no expected value edited. Per the spec's
*Constraints*: if Pinebox drifts, **stop**. This sub-spec adds a byte-level placement pin
(Step 1) whose expected value was computed from the current tree at authoring time, so drift is
caught by a hash rather than by reading a diff.

**Not in this sub-spec.** `SaddleStitchStrategy`, `fold_scheme`, `sheets_per_signature`,
`paper_thickness_pt`, `_creep_advisory`, marks attachment, cell *construction* from paper size,
and the folio spine rule's *use* are all SS-08. SS-07 only makes the math cell-aware and leaves
`_gutter_side_is_left` in place for `GutterShiftStrategy`.

---

## 2. Provides / Requires

**Provides:**

| Symbol | Kind | Module | Consumed by |
|---|---|---|---|
| `Cell = tuple[float, float, float, float]` | type alias (`x0, y0, x1, y1` in sheet points) | `deckle/core/layout.py` | SS-08, `tests/test_layout.py`, SS-08's preview work in SS-12 |
| `content_box_size(settings, *, cell: Cell \| None = None)` | function | `deckle/core/layout.py` | `document_scale`, SS-08 |
| `document_scale(pages, settings, *, cell: Cell \| None = None)` | function | `deckle/core/layout.py` | SS-08 (`scale = document_scale(active, settings, cell=cells[0])`) |
| `content_box_rect_pt(settings, *, is_recto, cell: Cell \| None = None)` | function | `deckle/core/layout.py` | SS-12 preview guides, SS-08 |
| `actual_margins_pt(output_page, paper, *, is_recto, binding_edge, cell: Cell \| None = None)` | function | `deckle/core/layout.py` | `tests/test_layout.py`, `tests/test_layout_saddle.py`, SS-08 |
| `_place_page(..., *, cell: Cell \| None = None, spine_side: Literal["left","right"] \| None = None)` | private function | `deckle/core/layout.py` | SS-08 (`place(slot, cells[i], spine_side=...)`) |
| `_full_sheet_cell(settings) -> Cell` | private helper | `deckle/core/layout.py` | the four public functions' `cell is None` path |

**Requires:**

| Symbol | Kind | Owner | Used for |
|---|---|---|---|
| `Side` (`pages: tuple[OutputPage, ...]`) | frozen dataclass | SS-01, wired in SS-02 | `GutterShiftStrategy` already wraps placed pages; unchanged here |
| `Sheet.front` / `.back` typed `Side \| None` | frozen dataclass | SS-02 | unchanged here — SS-07 must not touch the sheet-assembly loop |
| `LayoutSettings.paper`, `gutter_pt`, `margin_outer_pt`, `margin_top_pt`, `margin_bottom_pt`, `slack_to`, `binding_edge`, `landscape_policy` | fields | MVP `models.py` | the existing margin/slack model, now measured inside a cell |
| `Placement`, `OutputPage`, `SheetPlan`, `LayoutWarning` | frozen dataclasses | MVP `models.py` | unchanged |

**Explicitly NOT required, and a signal you have overreached if you reach for one:**
`deckle.core.signatures`, `deckle.core.marks`, `Signature`, `Mark`, `fold_scheme`,
`sheets_per_signature`, `paper_thickness_pt`.

---

## 3. Interface Contracts

### Cell

**Direction:** `layout.py` → SS-08, SS-12, `tests/test_layout.py`
**Owner:** SS-07
**Shape:**

```python
Cell = tuple[float, float, float, float]
"""A rectangle on the sheet that one leaf is placed into: (x0, y0, x1, y1).

Sheet points, PDF origin bottom-left, matching Placement. A cell of
(0, 0, paper_w, paper_h) IS the whole sheet, which is what GutterShiftStrategy
passes and what `cell=None` means.
"""
```

**Invariants:**

- `x1 > x0` and `y1 > y0`. A degenerate cell falls back to the full sheet rather than raising —
  same permissive posture as the existing margins-exceed-the-sheet branch.
- `GutterShiftStrategy` passes the full sheet, and **its output must be byte-identical** to the
  pre-SS-07 tree.
- Under folio on letter landscape (792 × 612) the two cells are `(0, 0, 396, 612)` and
  `(396, 0, 792, 612)` — matching the vault recipe's recorded `1 0 0 1 0 0 cm` /
  `1 0 0 1 396 0 cm`. SS-08 constructs them; SS-07 only consumes them.

### The cell parameter, on all four public functions

**Direction:** `layout.py` → every caller
**Owner:** SS-07
**Shape:** in every case, **keyword-only, defaulting to `None`**, appended after the existing
parameters:

```python
def content_box_size(settings: LayoutSettings, *, cell: Cell | None = None) -> tuple[float, float]: ...

def document_scale(pages: Sequence[SourcePage], settings: LayoutSettings,
                   *, cell: Cell | None = None) -> float: ...

def content_box_rect_pt(settings: LayoutSettings, *, is_recto: bool,
                        cell: Cell | None = None) -> tuple[float, float, float, float]: ...

def actual_margins_pt(output_page: OutputPage, paper: tuple[float, float], *,
                      is_recto: bool, binding_edge: str,
                      cell: Cell | None = None) -> tuple[float, float, float, float]: ...
```

**Why keyword-only with a default, per the spec's *Preferences*:** no existing call site and no
existing test changes shape, so the diff stays reviewable and REQ-009's byte-identity claim is
easy to believe. A positional cell parameter would touch every call site and every test, which
is exactly the noise that hides a drift.

**Semantics — one rule, stated once:**

```
cell is None  ->  cell = (0.0, 0.0, paper_w, paper_h)
cell_w = x1 - x0 ;  cell_h = y1 - y0
```

Then, everywhere the pre-SS-07 code read `paper_w` / `paper_h` as the box being divided up,
read `cell_w` / `cell_h`; and everywhere it produced an absolute sheet coordinate, add `x0` /
`y0`. Concretely:

- `content_box_size` returns `(cell_w - gutter - outer, cell_h - top - bottom)`, falling back
  to `(cell_w, cell_h)` when the margins consume the cell.
- `content_box_rect_pt` returns `(x0 + left, y0 + bottom, x1 - right, y1 - top)` where
  `(left, right)` is `(gutter, outer)` or `(outer, gutter)` exactly as today.
- `_place_page` computes `tx = x0 + inner_actual` when the spine is on the left, and
  `tx = x1 - inner_actual - scaled_w` when it is on the right; `ty = y0 + bottom_actual`.
- `actual_margins_pt` measures against the cell edges: `left = p.tx - x0`,
  `right = x1 - p.tx - scaled_w`, `bottom = p.ty - y0`, `top = y1 - p.ty - scaled_h`.

With `cell = (0, 0, paper_w, paper_h)` every one of these reduces algebraically to today's
expression. That reduction **is** REQ-009, and Step 1's pin is how it is proven.

**Invariants:**

- Each function called with a full-sheet cell (explicitly, or by omitting `cell`) returns
  exactly what it returned before this sub-spec.
- `content_box_rect_pt` with cell `(396, 0, 792, 612)` and a right-hand spine returns a rect
  whose `x0` is `396 + gutter_pt` — **margins are measured inside the cell, not from the sheet
  edge.**
- `actual_margins_pt` returns `(inner, outer, top, bottom)` relative to the supplied cell, so a
  leaf in the right-hand cell reports its inner margin against `x0 = 396`, not `0`.
- `document_scale` against a half-width cell is **smaller** than against the full sheet for the
  same pages, and remains a **single value** across the whole page sequence.

### `_place_page`'s explicit spine side

**Direction:** `layout.py` internal → SS-08
**Owner:** SS-07
**Shape:**

```python
def _place_page(
    slot: SourcePage | None,
    output_index: int,
    settings: LayoutSettings,
    sheet_index: int,
    warnings: list[LayoutWarning],
    scale: float,
    *,
    cell: Cell | None = None,
    spine_side: Literal["left", "right"] | None = None,
) -> OutputPage: ...
```

**Semantics:**

```
spine_side is None  ->  gutter_on_left = _gutter_side_is_left(_is_recto(output_index),
                                                              settings.binding_edge)
spine_side given    ->  gutter_on_left = (spine_side == "left")
                        and _gutter_side_is_left is NOT consulted at all
```

**Why this matters more than it looks.** In the MVP, `_gutter_side_is_left` derives the gutter
side from **output-page parity** — index 0 is a recto, spine left, so push right. Under folio
that rule is **wrong**: the spine of a leaf is determined by where its cell sits relative to the
fold —

```
left cell   ->  spine on its RIGHT edge   (the fold)
right cell  ->  spine on its LEFT  edge   (the fold)
```

— and this holds on both the front and the back of the sheet. The spec's *Must-Nots* say it
outright: MUST NOT reuse `_gutter_side_is_left` to decide the spine under folio. SS-07's job is
to make the non-parity path **exist and be reachable**; SS-08 is what uses it. Leave
`_gutter_side_is_left` in place, unchanged, for `GutterShiftStrategy`.

`content_box_rect_pt` and `actual_margins_pt` keep their existing `is_recto` parameter (their
callers are tests and the preview, which know the parity). SS-08 and SS-12 derive `is_recto`
from the cell's side of the fold rather than from parity; that derivation is theirs, not
SS-07's.

---

## 4. Implementation Steps

Each step is 2–10 minutes. Run commands from the repository root
(`C:\Users\CalebBennett\Documents\GitHub\BookBinder`). `ruff` is not on `PATH` in Git Bash on
this machine; invoke it as `python -m ruff`.

---

### Step 1 — Failing test FIRST: pin `GutterShiftStrategy`'s placements by hash

**This step comes before any production edit.** It is the regression net for the whole
sub-spec, and it is worthless if written after the change.

**Write:** `tests/test_layout.py` — append, do not modify anything above it.

```python
GUTTER_SHIFT_PLACEMENT_PIN = (
    "68c325043f7e0f3d5703a072f1bcb0fd0b0bf742dceb8a10f82958d3d2c507dc"
)
"""SHA-256 of every Placement GutterShiftStrategy emits over a 4-aspect-ratio x
2-binding-edge x 3-slack_to grid, computed from the tree at SS-07 authoring time
(2026-08-04, before any cell parameter existed).

REQ-009: GutterShiftStrategy's output must be byte-identical across the Side
refactor and the cell generalisation. If this pin drifts, the generalisation
changed MVP behaviour -- STOP and surface, per the spec's Constraints ("If
Pinebox drifts, stop"). Regenerating this value is an ESCALATION, not a
maintenance chore.
"""


def _placement_digest():
    import hashlib, json
    sizes = [LETTER, TRAVELLER, DIGEST, WIDE]
    rows = []
    for size in sizes:
        for edge in ("left", "right"):
            for slack in ("gutter", "outer", "split"):
                se = settings(binding_edge=edge, slack_to=slack, margin_outer_pt=18.0,
                              margin_top_pt=18.0, margin_bottom_pt=18.0)
                plan = GutterShiftStrategy().impose(make_pages(7, size=size), se)
                for sh in plan.sheets:
                    for name in ("front", "back"):
                        side = getattr(sh, name)
                        if side is None:
                            rows.append([sh.index, name, None])
                            continue
                        # Shape-agnostic: an OutputPage before SS-02, a Side after.
                        for op in getattr(side, "pages", (side,)):
                            p = op.placement
                            rows.append([sh.index, name,
                                         [round(p.scale_x, 9), round(p.scale_y, 9),
                                          round(p.tx, 9), round(p.ty, 9),
                                          p.rotate_deg, op.is_filler]])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def test_gutter_shift_placements_are_byte_identical_to_the_pin():
    assert _placement_digest() == GUTTER_SHIFT_PLACEMENT_PIN
```

**Assertion:** `_placement_digest() == "68c325043f7e0f3d5703a072f1bcb0fd0b0bf742dceb8a10f82958d3d2c507dc"`.

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k byte_identical_to_the_pin`
**Expect:** **green immediately.** This value was computed against the tree at authoring time
and re-verified twice. Green here is the point: it establishes the baseline. If it is red
before you have edited anything, the tree already drifted — stop and surface before proceeding.

Then prove it can fail: temporarily change `settings.gutter_pt`'s default in the local
`settings()` helper, re-run, watch it go red, revert. A pin never observed failing is not a pin.

**Commit:** `test(layout): pin GutterShiftStrategy's placements before the cell generalisation`

---

### Step 2 — Failing test: `Cell` exists and the four functions accept it

**Write:** `tests/test_layout.py`

```python
def test_the_four_placement_functions_accept_a_full_sheet_cell():
    from deckle.core.layout import Cell, content_box_size, content_box_rect_pt  # noqa: F401
    se = settings()
    full = (0.0, 0.0, *LETTER)
    assert content_box_size(se, cell=full) == content_box_size(se)
    assert content_box_rect_pt(se, is_recto=True, cell=full) == content_box_rect_pt(se, is_recto=True)
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k accept_a_full_sheet_cell`
**Expect:** red — `Cell` is undefined and `content_box_size` takes no `cell`.

---

### Step 3 — Implementation: `Cell`, `_full_sheet_cell`, and `content_box_size`

**Write:** `deckle/core/layout.py`

Define the `Cell` alias with the docstring from §3 and a private
`_full_sheet_cell(settings) -> Cell` returning `(0.0, 0.0, *settings.paper)`. Add the
keyword-only `cell` parameter to `content_box_size`, normalising `None` and a degenerate cell to
the full sheet, then substituting `cell_w` / `cell_h` for `paper_w` / `paper_h`.

**Run:**

```
python -m pytest tests/test_cell_geometry.py -q -k "accept_a_full_sheet_cell or byte_identical_to_the_pin"
python -m pytest tests/test_layout.py -q
```

**Expect:** green, 46+ test functions.
**Commit:** `refactor(layout): Cell alias and cell-aware content_box_size`

---

### Step 4 — Failing test: `document_scale` against a half-width cell

**Write:** `tests/test_layout.py`

```python
def test_document_scale_against_a_half_width_cell_is_smaller_and_still_single_valued():
    se = settings()
    pages = make_pages(9, size=TRAVELLER)
    full = document_scale(pages, se)
    half = document_scale(pages, se, cell=(0.0, 0.0, 306.0, 792.0))
    assert half < full
    # One scale for the WHOLE document, per docs/decisions.md "Uniform document-wide
    # scale" -- adding a cell must not make it per page.
    assert document_scale(pages[:3], se, cell=(0.0, 0.0, 306.0, 792.0)) == pytest.approx(half)
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k half_width_cell` → red.

> The second assertion holds because every page in the fixture is the same size, so any subset
> yields the same minimum. Use a uniform-size fixture deliberately; a mixed-size one would make
> this assertion about the fixture rather than about the rule.

---

### Step 5 — Implementation: cell-aware `document_scale`

**Write:** `deckle/core/layout.py` — thread `cell` through to the `content_box_size` call at
`layout.py:127`. Nothing else in the function changes; the `min` over every non-skipped page is
what makes the scale document-wide and it must stay exactly as it is.

**Run:** `python -m pytest tests/test_layout.py -q` → green, pin still green.
**Commit:** `refactor(layout): cell-aware document_scale, still one scale per document`

---

### Step 6 — Failing test: `content_box_rect_pt` measures margins inside the cell

**Write:** `tests/test_layout.py`

```python
def test_content_box_rect_is_measured_inside_the_cell_not_from_the_sheet_edge():
    """The right-hand folio cell of a letter-landscape sheet."""
    se = settings(paper=(792.0, 612.0), gutter_pt=54.0, margin_outer_pt=18.0,
                  margin_top_pt=18.0, margin_bottom_pt=18.0)
    right_cell = (396.0, 0.0, 792.0, 612.0)
    # Spine on the cell's LEFT edge -> gutter on the left -> x0 = 396 + gutter.
    x0, y0, x1, y1 = content_box_rect_pt(se, is_recto=True, cell=right_cell)
    assert x0 == pytest.approx(396.0 + 54.0)
    assert x1 == pytest.approx(792.0 - 18.0)
    assert (y0, y1) == pytest.approx((18.0, 612.0 - 18.0))
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k measured_inside_the_cell` → red.

---

### Step 7 — Implementation: cell-aware `content_box_rect_pt`

**Write:** `deckle/core/layout.py` — offset the returned rect by the cell origin and size the
box from `cell_w` / `cell_h`. Keep the existing `_gutter_side_is_left(is_recto, binding_edge)`
call and the existing margins-exceed-the-box fallback; only the coordinates move.

**Run:** `python -m pytest tests/test_layout.py -q` → green, pin green.
**Commit:** `refactor(layout): content_box_rect_pt measures margins inside its cell`

---

### Step 8 — Failing test: `actual_margins_pt` measures against the cell

**Write:** `tests/test_layout.py`

```python
def test_actual_margins_are_measured_against_the_cell():
    """A leaf in the right-hand cell reports its inner margin against x0 = 396, not 0."""
    se = settings(paper=(792.0, 612.0), gutter_pt=54.0, margin_outer_pt=18.0,
                  margin_top_pt=18.0, margin_bottom_pt=18.0)
    right_cell = (396.0, 0.0, 792.0, 612.0)
    page = _place_page(make_page(size=DIGEST), 0, se, 0, [], 1.0,
                       cell=right_cell, spine_side="left")
    inner, outer, top, bottom = actual_margins_pt(
        page, se.paper, is_recto=True, binding_edge="left", cell=right_cell)
    assert inner >= 54.0 - 1e-6      # the gutter is a MINIMUM, not an exact value
    assert outer >= 18.0 - 1e-6
    assert top == pytest.approx(bottom)   # vertical slack is always split
    # Measured against the SHEET instead, the inner margin would include the 396pt
    # offset -- this is the assertion that catches a cell-unaware measurement.
    assert inner < 396.0
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k measured_against_the_cell` → red.

> Assert **measured margins**, never raw `tx`/`ty`, per `docs/decisions.md`,
> *Rebuilt the placement math on one rule for both axes*: "The old suite asserted raw `tx`/`ty`
> on one edge of one page, which is how a verso-only bug survived it."

---

### Step 9 — Implementation: cell-aware `actual_margins_pt` and `_place_page`

**Write:** `deckle/core/layout.py`

- `actual_margins_pt`: subtract the cell origin and measure the far edges against `x1` / `y1`.
- `_place_page`: add the keyword-only `cell` and `spine_side`; compute the box from the cell;
  offset `tx` / `ty` by the cell origin; select `gutter_on_left` from `spine_side` when given,
  falling back to `_gutter_side_is_left` when it is `None`. The slack rules
  (`slack_to`, vertical split) are untouched — they already operate on box-relative slack.
- `GutterShiftStrategy.impose` is **not edited**: it omits `cell` and `spine_side`, which means
  the full sheet and parity-derived spine, exactly as before.

**Run:**

```
python -m pytest tests/test_layout.py -q
python -m pytest tests/test_cell_geometry.py -q -k byte_identical_to_the_pin
python -m pytest tests/test_golden_pinebox.py -q
```

**Expect:** all green. **If the pin or Pinebox drifts, stop and surface** — do not adjust the
pin. Per the spec's *Escalation Triggers* and *Constraints*, drift here means the
generalisation changed MVP behaviour, which is the one outcome this sub-spec exists to prevent.

**Commit:** `refactor(layout): cell-aware actual_margins_pt and _place_page`

---

### Step 10 — Failing test: an explicit spine side bypasses output-page parity

**Write:** `tests/test_layout.py`

```python
@pytest.mark.parametrize("output_index", [0, 1, 2, 3])
@pytest.mark.parametrize("binding_edge", ["left", "right"])
def test_explicit_spine_side_does_not_consult_output_page_parity(output_index, binding_edge):
    """Under folio the spine comes from the cell's side of the fold, never parity.

    docs/decisions.md, 'fixed_gutter put the reserved gutter on the wrong side of
    the verso', is what a parity-derived spine costs when it is wrong.
    """
    se = settings(paper=(792.0, 612.0), binding_edge=binding_edge)
    cell = (0.0, 0.0, 396.0, 612.0)
    placed = [_place_page(make_page(size=DIGEST), i, se, 0, [], 1.0,
                          cell=cell, spine_side="right").placement
              for i in (output_index,)]
    # Same spine_side -> same tx, whatever the parity or the binding edge.
    reference = _place_page(make_page(size=DIGEST), 0, se, 0, [], 1.0,
                            cell=cell, spine_side="right").placement
    assert placed[0].tx == pytest.approx(reference.tx)


def test_explicit_spine_side_left_and_right_are_mirror_images():
    se = settings(paper=(792.0, 612.0))
    cell = (0.0, 0.0, 396.0, 612.0)
    left = _place_page(make_page(size=DIGEST), 0, se, 0, [], 1.0,
                       cell=cell, spine_side="left")
    right = _place_page(make_page(size=DIGEST), 0, se, 0, [], 1.0,
                        cell=cell, spine_side="right")
    lm = actual_margins_pt(left, se.paper, is_recto=True, binding_edge="left", cell=cell)
    rm = actual_margins_pt(right, se.paper, is_recto=False, binding_edge="left", cell=cell)
    assert lm == pytest.approx(rm)   # measured (inner, outer, top, bottom) is identical
    assert left.placement.tx != pytest.approx(right.placement.tx)   # but the leaf moved
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k actual_margins_are_measured_against_the_cell` → green if Step 9 wired
`spine_side` correctly; red if parity still leaks in. The parametrisation over four output
indices and both binding edges is what proves the bypass, because parity and `binding_edge` are
the only two inputs `_gutter_side_is_left` consumes.

**Commit:** `test(layout): explicit spine side bypasses output-page parity`

---

### Step 11 — Failing test: the full-sheet-cell equivalence grid

**Write:** `tests/test_layout.py`

```python
@pytest.mark.parametrize("size", [LETTER, TRAVELLER, DIGEST, WIDE])
@pytest.mark.parametrize("binding_edge", ["left", "right"])
@pytest.mark.parametrize("slack", ["gutter", "outer", "split"])
def test_full_sheet_cell_is_identical_to_omitting_the_cell(size, binding_edge, slack):
    se = settings(binding_edge=binding_edge, slack_to=slack, margin_outer_pt=18.0,
                  margin_top_pt=18.0, margin_bottom_pt=18.0)
    pages = make_pages(5, size=size)
    full = (0.0, 0.0, *se.paper)
    assert content_box_size(se, cell=full) == content_box_size(se)
    assert document_scale(pages, se, cell=full) == document_scale(pages, se)
    for is_recto in (True, False):
        assert (content_box_rect_pt(se, is_recto=is_recto, cell=full)
                == content_box_rect_pt(se, is_recto=is_recto))
        op = _place_page(make_page(size=size), 0 if is_recto else 1, se, 0, [], 1.0, cell=full)
        assert (actual_margins_pt(op, se.paper, is_recto=is_recto,
                                  binding_edge=binding_edge, cell=full)
                == actual_margins_pt(op, se.paper, is_recto=is_recto,
                                     binding_edge=binding_edge))
```

**Run:** `python -m pytest tests/test_cell_geometry.py -q -k full_sheet_cell_is_identical` → green.
24 parametrised cases (4 × 2 × 3), each checking four functions on both parities. This is
REQ-020's "returns exactly what it returned before" criterion made executable.

**Commit:** `test(layout): full-sheet cell equals the pre-generalisation behaviour`

---

### Step 12 — Purity, lint, and the full suite

**Run, in order:**

```
python -m pytest tests/test_layout.py -q
python -m pytest tests/test_golden_pinebox.py -q
python -m pytest tests/test_export.py tests/test_render.py tests/test_preview_fidelity.py -q
! grep -nE "^import (os|io)$|open\(|requests|urllib|socket|PySide6" deckle/core/layout.py || (echo "FAIL: I/O or Qt in layout.py" && exit 1)
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests
python -m pytest -q
```

`tests/test_layout.py` must hold no fewer than 46 test functions and no fewer than the 81
parametrised cases it collected at authoring time; `python -m pytest -q` must collect no fewer
than 237 tests.

**Commit:** `chore(layout): cell generalisation clean under ruff, purity and the golden fixture`

---

## 5. Verification Commands

```bash
cd "C:/Users/CalebBennett/Documents/GitHub/BookBinder"

# 1. The MVP did not move. If either of these is red, STOP.
python -m pytest tests/test_cell_geometry.py -q -k byte_identical_to_the_pin
python -m pytest tests/test_golden_pinebox.py -q

# 2. The whole layout suite, no fewer than 46 test functions / 81 cases.
python -m pytest tests/test_layout.py -q

# 3. The new cell behaviour.
python -m pytest tests/test_cell_geometry.py -q -k "cell or spine_side"

# 4. Downstream consumers of layout.py.
python -m pytest tests/test_export.py tests/test_render.py tests/test_preview_fidelity.py -q

# 5. Purity and lint.
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests

# 6. Nothing else moved.
python -m pytest -q
```

---

## 6. Checks

Every command below exits 0 on the passing case. Negative assertions are written
`! grep … || (echo "FAIL: …" && exit 1)` per `docs/decisions.md`, *Negative-assertion
acceptance criteria must exit 0 when the pattern is absent*. Run from the repository root in
Git Bash. `ruff` is invoked as `python -m ruff`; the bare `ruff` binary is not on `PATH` in Git
Bash on this machine.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | `layout.py` defines `Cell` and all four functions accept a keyword-only `cell` defaulting to the full sheet (REQ-020) | `[STRUCTURAL]` | `python -c "import inspect,sys; from deckle.core import layout as L; sys.exit('FAIL: Cell alias missing') if not hasattr(L,'Cell') else None; bad=[n for n in ('document_scale','content_box_size','content_box_rect_pt','actual_margins_pt') if (lambda p: 'cell' not in p or p['cell'].kind is not inspect.Parameter.KEYWORD_ONLY or p['cell'].default is not None)(inspect.signature(getattr(L,n)).parameters)]; sys.exit('FAIL: no keyword-only cell=None on '+repr(bad)) if bad else print('OK')"` |
| 2 | `_place_page` accepts keyword-only `cell` and `spine_side`, both defaulting to `None` (REQ-025) | `[STRUCTURAL]` | `python -c "import inspect,sys; from deckle.core.layout import _place_page as f; p=inspect.signature(f).parameters; bad=[n for n in ('cell','spine_side') if n not in p or p[n].kind is not inspect.Parameter.KEYWORD_ONLY or p[n].default is not None]; sys.exit('FAIL: _place_page missing keyword-only '+repr(bad)) if bad else print('OK')"` |
| 3 | **`GutterShiftStrategy`'s placements are byte-identical to the pre-SS-07 tree** (REQ-009) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k byte_identical_to_the_pin` |
| 4 | A full-sheet cell returns exactly the pre-SS-07 value, over 4 aspect ratios × 2 binding edges × 3 `slack_to` (REQ-020) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k full_sheet_cell_is_identical_to_omitting_the_cell` |
| 5 | `content_box_rect_pt` with cell `(396, 0, 792, 612)` and a right-hand spine returns `x0 == 396 + gutter_pt` (REQ-020) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k content_box_rect_is_measured_inside_the_cell` |
| 6 | `document_scale` against a half-width cell is smaller and still single-valued (REQ-023) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k document_scale_against_a_half_width_cell` |
| 7 | `actual_margins_pt` measures `(inner, outer, top, bottom)` relative to the cell (REQ-020, REQ-025) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k actual_margins_are_measured_against_the_cell` |
| 8 | An explicit `spine_side` bypasses output-page parity (REQ-025) | `[BEHAVIORAL]` | `python -m pytest tests/test_cell_geometry.py -q -k actual_margins_are_measured_against_the_cell` |
| 9 | The existing layout suite passes with no fewer than 46 test functions / 81 collected cases (REQ-009) | `[MECHANICAL]` | `python -m pytest tests/test_layout.py -q && test "$(python -m pytest tests/test_layout.py --collect-only -q 2>/dev/null \| grep -c '::')" -ge 81` |
| 10 | The Pinebox golden fixture passes, skipping cleanly when the fixture is absent (REQ-009) | `[MECHANICAL]` | `python -m pytest tests/test_golden_pinebox.py -q` |
| 11 | Downstream consumers of `layout.py` still pass | `[MECHANICAL]` | `python -m pytest tests/test_export.py tests/test_render.py tests/test_preview_fidelity.py -q` |
| 12 | No I/O and no Qt in `layout.py` (REQ-040) | `[MECHANICAL]` | `! grep -nE "^import (os\|io)$\|open\(\|requests\|urllib\|socket\|PySide6" deckle/core/layout.py \|\| (echo "FAIL: I/O or Qt in layout.py" && exit 1)` |
| 13 | No Qt import anywhere in `deckle.core` — anchored to import statements, because the unanchored form matches the "must not import PySide6" docstrings at `models.py:5` and `__init__.py:3` and fails on a clean tree | `[MECHANICAL]` | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 14 | No pikepdf import in `layout.py` — it is pure math; drawing is `export.py` | `[MECHANICAL]` | `! grep -nE "^[[:space:]]*(import\|from)[[:space:]]+pikepdf" deckle/core/layout.py \|\| (echo "FAIL: pikepdf imported into layout math" && exit 1)` |
| 15 | SS-07 did not smuggle in an uncommitted `LayoutSettings` field | `[MECHANICAL]` | `! grep -n "sewing_marks\|sig_order_marks\|fold_lines\|signature_pattern\|sewing_tape_width_pt\|sewing_margin_pt" deckle/core/layout.py \|\| (echo "FAIL: uncommitted LayoutSettings field referenced — human-approval escalation" && exit 1)` |
| 16 | `deckle.core` stays pure on import | `[MECHANICAL]` | `python -m pytest tests/test_core_purity.py -q` |
| 17 | Lint is clean | `[MECHANICAL]` | `python -m ruff check deckle tests` |
| 18 | Nothing else moved; no fewer than 237 tests collected | `[MECHANICAL]` | `python -m pytest -q && test "$(python -m pytest --collect-only -q 2>/dev/null \| grep -c '::')" -ge 237` |

**Executed against the current tree at authoring time:** checks 9, 10, 11, 12, 13, 14, 15, 16,
17 and 18 were run and each exited 0 — including check 9's `-ge 81` collected-case count and
check 10's clean 3-skip on a machine without `DECKLE_PINEBOX_FIXTURE`. Check 3's expected value,
`68c325043f7e0f3d5703a072f1bcb0fd0b0bf742dceb8a10f82958d3d2c507dc`, was **computed twice from
the live tree** with the shape-agnostic digest script in Step 1 and is therefore already
correct; the test wrapping it cannot pass before Step 1 writes it. Checks 1, 2 and 4–8 exercise
code this sub-spec creates; their **command forms** were verified against existing symbols —
the `inspect.signature` parameter-kind probe of checks 1–2 against
`deckle.core.layout.content_box_rect_pt`, and the `--collect-only | grep -c '::'` counting form
of check 9 against `tests/test_layout.py` itself. `pytest -k <missing-name>` exits 5, so checks
3–8 fail loudly rather than passing vacuously if a test is renamed.

**Note on check 15:** it exits 0 today and must still exit 0 at SS-07 completion. SS-08
legitimately adds `fold_scheme`, `sheets_per_signature` and `_creep_advisory` to this file, but
**not** any of the six names above — those remain forbidden for the life of the project, so
this check stays valid downstream.
