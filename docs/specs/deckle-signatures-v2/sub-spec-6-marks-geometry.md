# SS-06 — `deckle/core/marks.py`: bindery marks as pure geometry

**Parent spec:** `docs/specs/2026-08-04-deckle-signatures-v2.md` (section 6)
**Contracts:** `docs/specs/deckle-signatures-v2/contracts.yaml` — `Mark`
**Phase:** run
**Depends on:** SS-01
**Satisfies:** REQ-027 (partially — the negative half), REQ-028, REQ-029, REQ-030

---

## 1. Context

**What this sub-spec does:**
Adds one new pure module, `deckle/core/marks.py`, that computes the three bindery marks a
folded signature needs, and returns them as `Mark` value objects in **sheet points, PDF origin
bottom-left**:

- `sewing_stations(...)` — the awl points. Short ticks crossing the fold, evenly spaced between
  a head inset and an equal tail inset.
- `signature_order_mark(...)` — a short bar on the spine, stepped down by signature index, so a
  correctly collated stack of folded gatherings shows a clean diagonal staircase down the spine
  and a misordered one is visible at a glance, before a single stitch.
- `fold_line(...)` — the cell boundary, head to tail.

**Why it is geometry and not drawing.** Three reasons, in the order they matter:

1. **`layout.py` is I/O-free and a `[MECHANICAL]` check enforces it.** Marks are computed
   during imposition and attached to `Side.marks`; if computing them required a drawing
   context, imposition would need one too.
2. **Numbers are testable; ink is not.** Every criterion below is an arithmetic assertion.
   Bookbinder JS carries an open bug —
   [#135](https://github.com/momijizukamori/bookbinder-js/issues/135), signature order marks
   wrong under page rotation — that this architecture makes very hard to have, because the
   marks are asserted as coordinates rather than eyeballed in a viewer.
3. **Stroke style belongs to the renderer.** Per contract 5, `Mark` carries no colour, no
   width, no fill and no dash: **every mark is a line segment**. Dashing the fold line is
   SS-09's business.

**Scope boundary — drawing is SS-09.** This module must not import `pikepdf`, must not
reference `ContentStreamBuilder` or `Canvas`, and must not open a file. A `[MECHANICAL]` check
greps for exactly that.

**Scope boundary — `marks.py` computes geometry only.** *Which* sheet gets which mark —
sewing stations on the innermost sheet's inner side, signature order marks on the outermost
sheet, fold lines everywhere — is SS-08's predicate, not this module's. These functions answer
"where would this mark go on a sheet of this size with the fold here", nothing more.

**Constants, not settings fields.** Adding a `LayoutSettings` field is an "agent recommends,
human approves" escalation, and the spec's *Contracts* section names
`sewing_margin_pt` / `sewing_tape_width_pt` explicitly as fields that MUST NOT be added.
So these live as module constants in `marks.py`:

- `SEWING_MARGIN_PT = 36.0` — Bookbinder JS's "(A) Margin", the inset from head and tail.
  **Committed value; do not change it.**
- `STATION_TICK_PT` — the tick's total length across the fold. Implementer's choice; `12.0` is
  recommended and the criteria below assume the tick half-length is smaller than the test
  fixture's 54pt gutter.
- `ORDER_BAR_PT` — the order bar's height along the spine. Implementer's choice; `18.0` is
  recommended.

`sewing_tape_width_pt` and tape-straddling station pairs are **out of scope** — they would
require an uncommitted settings field.

**Placement, recorded here and flagged for SS-13's physical verification:** sewing stations go
on the **innermost** sheet of each signature, on its **inner** side — the surface facing you
when the folded gathering lies open, which is where the awl goes in. Signature order marks go
on the **outermost** sheet — the surface that becomes the visible spine. SS-08 implements that
predicate; SS-13 is what proves it.

---

## 2. Provides / Requires

**Provides:**

| Symbol | Kind | Module | Consumed by |
|---|---|---|---|
| `SEWING_MARGIN_PT: float` | module constant (`36.0`) | `deckle/core/marks.py` | `tests/test_marks.py`, SS-08 |
| `STATION_TICK_PT: float` | module constant | `deckle/core/marks.py` | `tests/test_marks.py`, SS-09 |
| `ORDER_BAR_PT: float` | module constant | `deckle/core/marks.py` | `tests/test_marks.py`, SS-09 |
| `sewing_stations(sheet_h: float, fold_x: float, count: int) -> tuple[Mark, ...]` | function | `deckle/core/marks.py` | SS-08 (`marks_for`) |
| `signature_order_mark(sig_index: int, sig_count: int, sheet_h: float, fold_x: float) -> Mark` | function | `deckle/core/marks.py` | SS-08 (`marks_for`) |
| `fold_line(sheet_h: float, fold_x: float) -> Mark` | function | `deckle/core/marks.py` | SS-08 (`marks_for`) |

**Requires:**

| Symbol | Kind | Owner | Used for |
|---|---|---|---|
| `Mark` (`kind`, `x0`, `y0`, `x1`, `y1`) | frozen dataclass | SS-01 (`deckle/core/models.py`) | the only return type of this module |
| `Mark.kind` literals `"sewing_station"`, `"signature_order"`, `"fold_line"` | `Literal` members | SS-01 | tagging each mark |

**Explicitly NOT required:** `LayoutSettings`, `deckle.core.layout`, `deckle.core.export`,
`pikepdf`, `PySide6`. In particular this module never sees `paper_thickness_pt` — enforced by
a `[MECHANICAL]` check, because REQ-027 requires creep to stay out of every geometry path.

---

## 3. Interface Contracts

Coordinate convention throughout: **sheet points, PDF origin bottom-left**, matching
`Placement` and contract 5. `y = 0` is the **tail** (bottom edge of the sheet); `y = sheet_h`
is the **head** (top edge). `fold_x` is the cell boundary — for letter landscape (792 × 612)
that is `396.0`, matching the vault recipe's recorded `1 0 0 1 396 0 cm`.

### sewing_stations

**Direction:** `marks.py` → SS-08 `SaddleStitchStrategy` → `Side.marks` → SS-09 renderer
**Owner:** SS-06
**Shape:**

```python
def sewing_stations(sheet_h: float, fold_x: float, count: int) -> tuple[Mark, ...]:
    """`count` short ticks crossing the fold, evenly spaced head inset to tail inset."""
```

**Geometry:**

```
half = STATION_TICK_PT / 2.0
span = sheet_h - 2 * SEWING_MARGIN_PT
count == 0 (or < 0)  ->  ()
count == 1           ->  one station at y = sheet_h / 2.0
count >= 2           ->  y_i = SEWING_MARGIN_PT + i * span / (count - 1), i in 0..count-1

Mark(kind="sewing_station", x0=fold_x - half, y0=y_i, x1=fold_x + half, y1=y_i)
```

**Invariants:**

- Returns exactly `count` marks for `count >= 1`, all of kind `"sewing_station"`.
- Midpoint y-values are evenly spaced (constant successive difference).
- The first is `SEWING_MARGIN_PT` from the **tail**; the last is `SEWING_MARGIN_PT` from the
  **head**.
- Every mark's x-interval **contains** `fold_x` — it straddles the fold, so the tick is visible
  on both leaves and the awl has a target when the sheet is open flat.
- `count <= 0` returns `()`. **This is how `settings.sewing_stations = 0` disables stations
  without a new boolean** — a control that is already expressible must not gain a second
  control expressing it, per `docs/decisions.md`, *Deleted the scale mode*.
- `sheet_h <= 2 * SEWING_MARGIN_PT` degenerates safely: clamp `span` to `0.0` so all stations
  coincide at `SEWING_MARGIN_PT` rather than producing a negative span. Never raise.

### signature_order_mark

**Direction:** `marks.py` → SS-08 → `Side.marks` → SS-09
**Owner:** SS-06
**Shape:**

```python
def signature_order_mark(
    sig_index: int, sig_count: int, sheet_h: float, fold_x: float
) -> Mark:
    """A short bar on the spine, stepped down the sheet by signature index."""
```

**Geometry — verbatim from the master spec:**

```
step = (sheet_h - 2 * SEWING_MARGIN_PT - ORDER_BAR_PT) / max(1, sig_count - 1)
y    = SEWING_MARGIN_PT + sig_index * step

Mark(kind="signature_order", x0=fold_x, y0=y, x1=fold_x, y1=y + ORDER_BAR_PT)
```

**Invariants:**

- `y0` is strictly monotonically increasing across `sig_index` `0 … sig_count - 1` (for
  `sig_count >= 2` and a sheet tall enough to have positive `step`).
- `sig_index == 0` sits at the tail margin: `y0 == SEWING_MARGIN_PT`.
- `sig_index == sig_count - 1` reaches the head margin: `y1 == sheet_h - SEWING_MARGIN_PT`.
  (Substitute: `y0 = SEWING_MARGIN_PT + (sheet_h - 2·M - bar) = sheet_h - M - bar`, so
  `y1 = sheet_h - M`.)
- `sig_count == 1` does **not** divide by zero — `max(1, sig_count - 1)` is why — and returns
  the bar at the tail margin.
- The bar lies **on** the fold: `x0 == x1 == fold_x`.

**Why the staircase:** a stack of folded gatherings, spines out, shows one bar per gathering at
a different height. Correct collation reads as a clean diagonal; a swapped or inverted
gathering breaks the diagonal and is visible across the room. This is a physical
error-detection device, which is why it is worth the geometry.

### fold_line

**Direction:** `marks.py` → SS-08 → `Side.marks` → SS-09
**Owner:** SS-06
**Shape:**

```python
def fold_line(sheet_h: float, fold_x: float) -> Mark:
    """The cell boundary, head to tail."""
```

**Geometry:** `Mark(kind="fold_line", x0=fold_x, y0=0.0, x1=fold_x, y1=sheet_h)`

**Invariants:**

- Exactly one `Mark`, kind `"fold_line"`.
- `x0 == x1 == fold_x`; spans the full sheet height, `0.0` to `sheet_h`.
- **Dashing is the renderer's concern (SS-09), not the geometry's.** Do not add a `dashed`
  field, a `style` field, or a second "dash segment" mark per dash — contract 5 says every mark
  is a line segment and `Mark` has no style fields.

---

## 4. Implementation Steps

Each step is 2–10 minutes. Run commands from the repository root
(`C:\Users\CalebBennett\Documents\GitHub\BookBinder`). `ruff` is not on `PATH` in Git Bash on
this machine; invoke it as `python -m ruff`.

Test fixture used throughout, matching the feature's target output — **letter landscape**,
`sheet_h = 612.0`, `fold_x = 396.0`, which is the vault recipe's recorded sheet
(`mediabox: pikepdf.Rectangle(0.0, 0.0, 792.0, 612.0)`, placements at `x = 0` and `x = 396`).

---

### Step 1 — Failing test: `fold_line` is one segment on the cell boundary

**Write:** `tests/test_marks.py`

Module docstring: pure geometry in sheet points, PDF origin bottom-left; `y = 0` is the tail,
`y = sheet_h` the head; no drawing happens here.

```python
def test_fold_line_is_a_single_segment_on_the_cell_boundary():
    m = fold_line(sheet_h=612.0, fold_x=396.0)
    assert m.kind == "fold_line"
    assert m.x0 == m.x1 == 396.0
    assert (m.y0, m.y1) == (0.0, 612.0)
```

**Run:** `python -m pytest tests/test_marks.py -q` → collection error, `deckle.core.marks` does
not exist. Red.

---

### Step 2 — Implementation: the module, its constants, and `fold_line`

**Write:** `deckle/core/marks.py`

Module docstring stating: pure geometry, sheet points, PDF origin bottom-left, no drawing and
no I/O, and that stroke style belongs to the renderer. **Do not name a forbidden token in a
docstring or comment.** Per the spec's *Edge Cases*, a `[MECHANICAL]` grep over this file
matches `pikepdf`, `ContentStreamBuilder`, `Canvas`, `PySide6` and `open(` — so write "the
exporter draws these as vectors" rather than naming the drawing API. A criterion that fails on
correct code is indistinguishable from one that caught a real defect, and factory run
`c46e15e3` shows what that costs.

Define `SEWING_MARGIN_PT = 36.0`, `STATION_TICK_PT`, `ORDER_BAR_PT`, and `fold_line`.

**Run:** `python -m pytest tests/test_marks.py -q` → green, 1 test.
**Commit:** `feat(core): marks module with fold-line geometry`

---

### Step 3 — Failing test: three evenly spaced sewing stations at the stated insets

**Write:** `tests/test_marks.py`

```python
def test_three_sewing_stations_are_evenly_spaced_between_the_insets():
    marks = sewing_stations(sheet_h=612.0, fold_x=396.0, count=3)
    assert len(marks) == 3
    assert all(m.kind == "sewing_station" for m in marks)
    ys = [(m.y0 + m.y1) / 2.0 for m in marks]
    assert ys[0] == pytest.approx(SEWING_MARGIN_PT)                  # from the tail
    assert ys[-1] == pytest.approx(612.0 - SEWING_MARGIN_PT)         # from the head
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert gaps == pytest.approx([gaps[0]] * len(gaps))
```

**Run:** `python -m pytest tests/test_marks.py -q` → red, `sewing_stations` undefined.

---

### Step 4 — Implementation: `sewing_stations`

**Write:** `deckle/core/marks.py` — the `count >= 2` branch per §3.

**Run:** `python -m pytest tests/test_marks.py -q` → green, 2 tests.
**Commit:** `feat(core): evenly spaced sewing-station geometry`

---

### Step 5 — Failing test: every station straddles the fold

**Write:** `tests/test_marks.py`

```python
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 7])
def test_every_sewing_station_straddles_the_fold(count):
    """The tick must be visible on BOTH leaves, so the awl has a target open flat."""
    for m in sewing_stations(sheet_h=612.0, fold_x=396.0, count=count):
        assert min(m.x0, m.x1) < 396.0 < max(m.x0, m.x1)
```

**Run:** `python -m pytest tests/test_marks.py -q` → green if Step 4 centred the tick on
`fold_x`; red if it anchored the tick to one side. Fixing it is the point of the step.

**Commit:** `test(core): sewing stations straddle the fold on both leaves`

---

### Step 6 — Failing test: the disable switch and the single centred station

**Write:** `tests/test_marks.py`

```python
def test_zero_sewing_stations_returns_no_marks():
    """settings.sewing_stations = 0 is the disable switch; no second boolean exists."""
    assert sewing_stations(sheet_h=612.0, fold_x=396.0, count=0) == ()
    assert sewing_stations(sheet_h=612.0, fold_x=396.0, count=-2) == ()


def test_one_sewing_station_is_centred():
    (m,) = sewing_stations(sheet_h=612.0, fold_x=396.0, count=1)
    assert (m.y0 + m.y1) / 2.0 == pytest.approx(306.0)
```

**Run:** `python -m pytest tests/test_marks.py -q` → red on the `count == 1` and `count <= 0`
branches.

---

### Step 7 — Implementation: the `count <= 0` and `count == 1` branches, plus the short-sheet clamp

**Write:** `deckle/core/marks.py`

Also handle `sheet_h <= 2 * SEWING_MARGIN_PT` by clamping the span to `0.0`. Never raise.

**Run:** `python -m pytest tests/test_marks.py -q` → green.
**Commit:** `feat(core): sewing-station disable switch, centred single station, short-sheet clamp`

---

### Step 8 — Failing test: the signature-order staircase

**Write:** `tests/test_marks.py`

```python
@pytest.mark.parametrize("sig_count", [2, 3, 5, 17])
def test_signature_order_marks_step_monotonically_down_the_spine(sig_count):
    marks = [signature_order_mark(i, sig_count, sheet_h=612.0, fold_x=396.0)
             for i in range(sig_count)]
    assert all(m.kind == "signature_order" for m in marks)
    assert all(m.x0 == m.x1 == 396.0 for m in marks)
    ys = [m.y0 for m in marks]
    assert ys == sorted(ys) and len(set(ys)) == sig_count      # strictly increasing
    assert ys[0] == pytest.approx(SEWING_MARGIN_PT)            # first at the tail margin
    assert marks[-1].y1 == pytest.approx(612.0 - SEWING_MARGIN_PT)  # last at the head margin


def test_a_single_signature_does_not_divide_by_zero():
    m = signature_order_mark(0, 1, sheet_h=612.0, fold_x=396.0)
    assert m.y0 == pytest.approx(SEWING_MARGIN_PT)
```

**Run:** `python -m pytest tests/test_marks.py -q` → red, `signature_order_mark` undefined.

---

### Step 9 — Implementation: `signature_order_mark`

**Write:** `deckle/core/marks.py` — `step = (sheet_h - 2*M - ORDER_BAR_PT) / max(1, sig_count - 1)`.
The `max(1, …)` is what makes `sig_count == 1` safe; do not replace it with a conditional that
returns early, because the tail-margin position is the correct answer for one signature.

**Run:** `python -m pytest tests/test_marks.py -q` → green.
**Commit:** `feat(core): signature-order staircase geometry`

---

### Step 10 — Failing test: no mark lands inside a cell's content box

**Write:** `tests/test_marks.py`

```python
def test_no_mark_lies_inside_a_cell_content_box():
    """Every mark is ON or ACROSS the fold, never in the printed area of a leaf.

    Letter landscape folio, cells (0, 0, 396, 612) and (396, 0, 792, 612), gutter
    54pt on the fold side, 18pt outer/head/tail. The left cell's spine is its RIGHT
    edge, so its content box is (18, 18, 342, 594); the right cell's spine is its
    LEFT edge, so its box is (450, 18, 774, 594). A mark inside either would print
    over the reader's text.
    """
    boxes = [(18.0, 18.0, 342.0, 594.0), (450.0, 18.0, 774.0, 594.0)]
    marks = [fold_line(612.0, 396.0),
             *sewing_stations(612.0, 396.0, 3),
             signature_order_mark(0, 4, 612.0, 396.0),
             signature_order_mark(3, 4, 612.0, 396.0)]
    for m in marks:
        for x0, y0, x1, y1 in boxes:
            inside = (x0 < min(m.x0, m.x1) and max(m.x0, m.x1) < x1
                      and y0 < min(m.y0, m.y1) and max(m.y0, m.y1) < y1)
            assert not inside, f"{m} lies inside content box {(x0, y0, x1, y1)}"
```

**Run:** `python -m pytest tests/test_marks.py -q` → green with the recommended
`STATION_TICK_PT = 12.0` (the tick spans 390–402, straddling the 342–450 dead band between the
two content boxes). If a larger tick was chosen and this fails, **shrink the tick** — do not
loosen the assertion. A sewing tick printed over body text is a real defect.

**Commit:** `test(core): no bindery mark lands inside a leaf's content box`

---

### Step 11 — Failing test: `Mark` is the only return type

**Write:** `tests/test_marks.py`

```python
def test_every_public_function_returns_only_Mark_values():
    assert all(isinstance(m, Mark) for m in sewing_stations(612.0, 396.0, 3))
    assert isinstance(signature_order_mark(0, 3, 612.0, 396.0), Mark)
    assert isinstance(fold_line(612.0, 396.0), Mark)
```

**Run:** `python -m pytest tests/test_marks.py -q` → green.
**Commit:** `test(core): marks module returns Mark values only`

---

### Step 12 — Purity, lint, and the full suite

**Run, in order:**

```
! grep -n "pikepdf\|ContentStreamBuilder\|Canvas\|PySide6\|open(" deckle/core/marks.py || (echo "FAIL: drawing or I/O leaked into marks geometry" && exit 1)
! grep -n "paper_thickness_pt" deckle/core/marks.py || (echo "FAIL: paper_thickness_pt reached mark geometry" && exit 1)
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests
python -m pytest -q
```

All must be clean; `python -m pytest -q` must collect no fewer than 237 tests, and
`tests/test_marks.py` must hold no fewer than 10 tests.

**Commit:** `chore(core): marks module clean under ruff, core-purity and the drawing-free grep`

---

## 5. Verification Commands

```bash
cd "C:/Users/CalebBennett/Documents/GitHub/BookBinder"

# 1. The sub-spec's own suite, milliseconds.
python -m pytest tests/test_marks.py -q

# 2. The two scope greps: no drawing, no creep.
! grep -n "pikepdf\|ContentStreamBuilder\|Canvas\|PySide6\|open(" deckle/core/marks.py || (echo "FAIL: drawing or I/O leaked into marks geometry" && exit 1)
! grep -n "paper_thickness_pt" deckle/core/marks.py || (echo "FAIL: paper_thickness_pt reached mark geometry" && exit 1)

# 3. Purity and lint.
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests

# 4. Nothing else moved.
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
| 1 | `marks.py` exposes `sewing_stations`, `signature_order_mark`, `fold_line` and `SEWING_MARGIN_PT == 36.0` | `[STRUCTURAL]` | `python -c "import sys; from deckle.core import marks as m; missing=[n for n in ('sewing_stations','signature_order_mark','fold_line') if not callable(getattr(m,n,None))]; sys.exit('FAIL: missing '+repr(missing)) if missing else None; sys.exit('FAIL: SEWING_MARGIN_PT is not 36.0') if getattr(m,'SEWING_MARGIN_PT',None)!=36.0 else print('OK')"` |
| 2 | Each function returns `Mark` values only | `[STRUCTURAL]` | `python -m pytest tests/test_spec_residue.py -q -k every_public_function_returns_only_Mark_values` |
| 3 | 3 stations, kind `sewing_station`, evenly spaced, first at the tail inset and last at the head inset (REQ-028) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k "sewing_stations_count_and_kind or sewing_stations_evenly_spaced or sewing_stations_first_and_last_margins"` |
| 4 | Every station's x-interval contains `fold_x` (REQ-028) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k sewing_stations_straddle_fold` |
| 5 | `count=0` returns `()`; `count=1` returns one centred station (REQ-028) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k "sewing_stations_zero_count_returns_empty or sewing_stations_count_one_is_centred"` |
| 6 | Order marks step monotonically; index 0 at the tail margin, last at the head margin (REQ-029) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k "signature_order_mark_monotonically_increasing or signature_order_mark_endpoints_at_margins"` |
| 7 | `sig_count == 1` does not divide by zero (REQ-029) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k signature_order_mark_single_signature_no_zero_division` |
| 8 | `fold_line` returns one `fold_line` mark with `x0 == x1 == fold_x`, spanning the full sheet height (REQ-030) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k fold_line_spans_full_height` |
| 9 | No mark lies strictly inside a cell's content box for a letter-landscape folio (REQ-028, REQ-030) | `[BEHAVIORAL]` | `python -m pytest tests/test_marks.py -q -k no_mark_lies_inside_a_cell_content_box` |
| 10 | No drawing API and no I/O in `marks.py` — drawing is SS-09 | `[MECHANICAL]` | `! grep -n "pikepdf\|ContentStreamBuilder\|Canvas\|PySide6\|open(" deckle/core/marks.py \|\| (echo "FAIL: drawing or I/O leaked into marks geometry" && exit 1)` |
| 11 | `paper_thickness_pt` never reaches mark geometry (REQ-027) | `[MECHANICAL]` | `! grep -n "paper_thickness_pt" deckle/core/marks.py \|\| (echo "FAIL: paper_thickness_pt reached mark geometry" && exit 1)` |
| 12 | `marks.py` does not depend on `layout.py`, `export.py` or `render.py` — it is leaf geometry | `[MECHANICAL]` | `! grep -nE "^[[:space:]]*(import\|from)[[:space:]]+deckle\.core\.(layout\|export\|render)" deckle/core/marks.py \|\| (echo "FAIL: marks.py depends on layout/export/render" && exit 1)` |
| 13 | No Qt import anywhere in `deckle.core` — anchored to import statements, because the unanchored form matches the "must not import PySide6" docstrings at `models.py:5` and `__init__.py:3` and fails on a clean tree | `[MECHANICAL]` | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 14 | No uncommitted settings field smuggled in as a `marks.py` name | `[MECHANICAL]` | `! grep -n "sewing_tape_width_pt\|sewing_margin_pt\|sig_order_marks\|signature_pattern" deckle/core/marks.py \|\| (echo "FAIL: uncommitted settings field named in marks.py — human-approval escalation" && exit 1)` |
| 15 | `tests/test_marks.py` passes with no fewer than 10 tests | `[MECHANICAL]` | `python -m pytest tests/test_marks.py -q && test "$(python -m pytest tests/test_marks.py --collect-only -q 2>/dev/null \| grep -c '::')" -ge 10` |
| 16 | `deckle.core` stays pure on import | `[MECHANICAL]` | `python -m pytest tests/test_core_purity.py -q` |
| 17 | Lint is clean | `[MECHANICAL]` | `python -m ruff check deckle tests` |
| 18 | Nothing else moved; no fewer than 237 tests collected | `[MECHANICAL]` | `python -m pytest -q && test "$(python -m pytest --collect-only -q 2>/dev/null \| grep -c '::')" -ge 237` |

**Executed against the current tree at authoring time:** checks 10, 11, 12, 13, 14, 16, 17 and
the `python -m pytest -q` half of 18 were run and each exited 0 (checks 10–12 and 14 exit 0
because `grep` returns 2 on a missing file and `!` inverts it — they will still exit 0, and now
meaningfully, once `marks.py` exists). Checks 1–9 and 15 exercise code this sub-spec creates
and cannot pass before it lands; their **command forms** were verified against existing modules
— the `inspect`/`getattr` form of check 1 against `deckle.core.layout`, and the
`--collect-only | grep -c '::'` counting form of check 15 against `tests/test_models.py`.
`pytest -k <missing-name>` exits 5, so checks 2–9 fail loudly rather than passing vacuously if
a test is renamed.
