---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-09
sub_spec_number: 9
title: "Mark rendering in export.py via pikepdf.canvas.ContentStreamBuilder"
depends_on: ['SS-02', 'SS-06']
date: 2026-08-04
---

# SS-09 — Mark rendering in `export.py` via `pikepdf.canvas.ContentStreamBuilder`

## Context

SS-06 produced bindery marks as pure geometry — `Mark` value objects in sheet points, PDF
origin bottom-left, **a line segment in every case**. SS-08 attached them to sides. This
sub-spec is the only place they become ink.

**Because the preview rasterises the exported artifact** (MVP SS-05: `render.py` never
re-draws from `Placement`), marks are **visible in preview by construction**. There is no
second drawing path to keep in sync, and that is deliberate: Bookbinder JS carries an open bug
([#135](https://github.com/momijizukamori/bookbinder-js/issues/135)) where signature order
marks are wrong under page rotation, which is exactly the class of defect a second drawing
path invites.

**Read before writing code:**

1. `deckle/core/export.py` in full — particularly `_place_output_page` (line 134),
   `_sides` (line 198, `-> list[Side]` after SS-02), `_export_batched` (line 249), and the
   module docstring, which already explains *why* the whole-page overlay/watermark helper is
   never used here.
2. `docs/specs/2026-08-04-deckle-signatures-v2.md` — the `export_content_stream` contract in
   *Contracts*, REQ-010, REQ-031, REQ-032, and the *Edge Cases* entries on `page.Contents`,
   `page.mediabox` and `pdf.pages` reordering.
3. `docs/specs/deckle-signatures-v2/contracts.yaml`, `export_content_stream`.
4. `docs/decisions.md`, entry *Save PDF, spread preview, and verification against a real
   266-page book* — its **Watch** line is the trap that bites this sub-spec directly.
5. **`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition
   and Signature Recipe.md`** — working, executed code, run end to end against pikepdf
   10.11.0 / libqpdf 12.3.2 on this machine. It produces exactly this sub-spec's target
   artifact: 8 half-letter pages onto 4 landscape letter sheets, 2-up. Its recorded coalesced
   content stream for sheet 1 (note lines 66–69) is the assertion this sub-spec pins:

   ```
   b'\nq\n1 0 0 1 0 0 cm\n/Fxolv1V_Lrc-nsrHo4iXCiyg Do\nQ\n
      q\n1 0 0 1 396 0 cm\n/FxFxLw_rUR07xdZ58EhaaQmQ Do\nQ\n'
   ```

   *"`1 0 0 1 396 0 cm` — **pure translation, no scale factor.** That is the proof the
   placement is exact 1:1 and not best-fit centered."*

   One caveat on that note: its "Adding gutter, bleed, and crop marks" section suggests
   drawing marks with the Canvas API and compositing the result with the whole-page
   overlay/watermark helper. **Deckle does not do that.** That helper centres and best-fits,
   which would move the marks off the fold. Deckle appends the mark bytes to the sheet's own
   content stream with `contents_add`, in the sheet's own coordinate space — the same call the
   note's `place_exact` already uses for placements. Composition stays `as_form_xobject` +
   `calc_form_xobject_placement`, never the overlay helper.

### Three traps, each already realised in this repo or verified against the installed library

**Trap 1 — `page.Contents` may be an `Array`.** `read_bytes()` raises *"operation for stream
attempted on object of type array"* until `contents_coalesce()` runs. `docs/decisions.md`
records this biting **tooling, not application code** — which is precisely what
`tests/test_export_marks.py` is. Every byte-parsing assertion in this sub-spec must call
`Page(pdf.pages[i]).contents_coalesce()` before reading. No test in the current suite does
this yet; this sub-spec introduces the pattern.

**Trap 2 — never the whole-page overlay/watermark helper.** It centres and best-fits.
Correct for watermarks, wrong for imposition, where `Placement` dictates exact geometry.
`Canvas.draw_image` is likewise never used (a measured 12.6× file-size blowup; irrelevant for
vectors, but the temptation exists the moment someone wants a logo).

**Trap 3 — no text, ever.** Every `Mark` is a line segment, so pikepdf's own docs' *"rudimentary
interface. You've been warned"* text engine never enters the path and `reportlab` stays out of
the dependency set entirely. This is also why `Mark` carries no label field: a signature's
identity is communicated by the *position* of its order bar down the spine (a staircase across
a collated stack), not by a printed number.

### The installed API, verified in this environment

`pikepdf.canvas.ContentStreamBuilder` exists in pikepdf 10.11.0. Its actual method names were
enumerated at authoring time and **differ from the master spec's prose** — use these:

| Master spec prose | Actual method on pikepdf 10.11.0 |
|---|---|
| `line_width` | `set_line_width(width)` |
| `dashes` | `set_dashes(array=None, phase=0)` |
| `append_rectangle` | `append_rectangle(...)` — present, but not needed; marks are line segments |
| — | `line(x1, y1, x2, y2)` — exactly a `Mark` |
| — | `stroke_and_close()`, `push()`, `pop()`, `build() -> bytes` |
| — | `set_stroke_color(r, g, b)` — three **floats** |

Executed in this environment at authoring time:

```python
ContentStreamBuilder().push().set_line_width(0.5).line(396, 36, 396, 576) \
                      .stroke_and_close().pop().build()
# -> b'q\n0.5 w\n396 36 m\n396 576 l\ns\nQ\n'
```

A balanced `q…Q` block, `s` as the stroke operator, and **no `Do`** — so counting `q…Q` blocks
that carry a `Do` is a stable way to assert "exactly two Form XObject placements per page"
regardless of how many marks are drawn.

**On colour.** The contract says *"Colours are `pikepdf.canvas.Color`, never tuples"* — that
rule governs the higher-level `Canvas`/`Text` API, which this sub-spec never touches.
`ContentStreamBuilder.set_stroke_color` takes three floats. The simplest correct thing here is
to **omit colour entirely** and stroke in the PDF default (black), which sidesteps the trap
rather than navigating it. If a colour is ever needed, use `set_stroke_color(0.0, 0.0, 0.0)`.

### The full 2-up-plus-marks stream, executed on this machine

Reproduced at authoring time against `tests/fixtures/sample.pdf`, composing two placements at
`x = 0` and `x = 396` on a 792 × 612 sheet and then appending one dashed fold line:

```
b'\nq\n1 0 0 1 0 0 cm\n/FxzB0RjwVqpE4h6U6uBZrMow Do\nQ\n
   q\n1 0 0 1 396 0 cm\n/FxnnMWsRPwx69pO8qbvyMpWw Do\nQ\n
   q\n0.5 w\n[ 3 3 ] 0 d\n396 36 m\n396 576 l\ns\nQ\n'
```

Two pure-translation `Do` blocks, then one balanced marks block. **This is the target output
of this sub-spec, verbatim.** The marks block appends *after* every placement, so the
two-XObject assertion is unaffected by however many marks a side carries.

## Provides

| Symbol | Shape | Consumed by |
|---|---|---|
| `_draw_marks(dest_page, marks)` | private; appends one balanced `q…Q` block of stroked line segments | `_export_batched` only |
| `export(plan, out_path, sheets=None)` | unchanged public signature, now mark-aware | SS-11 (preview, Save PDF), SS-12 (integration, CLI) |

`export`'s public signature does **not** change. Marks arrive inside the plan, on `Side.marks`.

## Requires

| From | Symbol | Why |
|---|---|---|
| SS-01 | `Mark`, `Side` | `Side.marks: tuple[Mark, ...]`; a mark is `kind, x0, y0, x1, y1` in sheet points. |
| SS-02 | `_sides(sheet) -> list[Side]`, one `add_blank_page` per `Side`, one `_place_output_page` per page in `side.pages` | The loop this sub-spec appends to. |
| SS-06 | `sewing_stations`, `signature_order_mark`, `fold_line` | Produce the geometry; this sub-spec only strokes it. |
| SS-08 | populated `Side.marks` | The data. Tests here may construct `Side.marks` by hand, so this sub-spec does **not** hard-depend on SS-08 landing first. |
| pikepdf 10.11.0 | `pikepdf.canvas.ContentStreamBuilder`, `Page.contents_add`, `Page.contents_coalesce` | Verified present in this environment. |

## Implementation Steps

Each step is 2–10 minutes: write the failing test, run it and see it fail, implement the
minimum, run it green, commit.

### Step 1. Failing test: the coalesce helper, and two pure-translation blocks

Create `tests/test_export_marks.py` with the parsing helper the whole file will share — this
is the `contents_coalesce` trap, encoded once:

```python
import re
import pikepdf
from pikepdf import Page

_Q_BLOCK = re.compile(rb"q\n(.*?)\nQ", re.S)

def coalesced(path, page_index=0) -> bytes:
    with pikepdf.open(path) as pdf:
        page = Page(pdf.pages[page_index])
        page.contents_coalesce()          # page.Contents may be an Array; read_bytes() raises without this
        return page.obj.Contents.read_bytes()

def placement_blocks(stream: bytes) -> list[bytes]:
    return [b for b in _Q_BLOCK.findall(stream) if b" Do" in b]
```

First test:

```python
def test_two_pure_translation_placements_per_folio_page(tmp_path):
    out = tmp_path / "folio.pdf"
    export(_folio_plan_two_up(), str(out))
    blocks = placement_blocks(coalesced(str(out)))
    assert len(blocks) == 2
    for b in blocks:
        assert re.search(rb"^1 0 0 1 -?[\d.]+ -?[\d.]+ cm$", b.splitlines()[0])
```

Run `python -m pytest tests/test_export_marks.py -q -k pure_translation`. If SS-02 landed
correctly this may already pass — that is fine and expected; it is a **regression pin** on the
vault-recorded output, not a driver for new code. If it fails, SS-02 is broken; stop and fix
there, not here.

Commit: `test(SS-09): pin two pure-translation Form XObject placements per folio page`.

### Step 2. Failing test: a side with no marks strokes nothing

```python
def test_marks_are_additive_never_mandatory(tmp_path):
    out = tmp_path / "bare.pdf"
    export(_folio_plan_two_up(marks=()), str(out))
    stream = coalesced(str(out))
    assert b"\ns\n" not in stream and b"\nS\n" not in stream
```

Run it. It should pass on the current tree — pin it before adding the drawing code, so the
"no marks means no ink" property is protected by a test that predates the feature.

### Step 3. Failing test: five marks become five stroke operations

```python
def test_five_marks_stroke_five_segments(tmp_path):
    marks = (
        *[Mark(kind="sewing_station", x0=390, y0=y, x1=402, y1=y) for y in (108, 306, 504)],
        Mark(kind="signature_order", x0=390, y0=40, x1=402, y1=40),
        Mark(kind="fold_line", x0=396, y0=0, x1=396, y1=612),
    )
    out = tmp_path / "marked.pdf"
    export(_folio_plan_two_up(marks=marks), str(out))
    stream = coalesced(str(out))
    assert stream.count(b"\ns\n") == 5
    assert len(placement_blocks(stream)) == 2      # placements unaffected by marks
```

Run `python -m pytest tests/test_export_marks.py -q -k five_marks`. Expect failure: 0 strokes.

### Step 4. Implement `_draw_marks`

In `deckle/core/export.py`:

```python
from pikepdf.canvas import ContentStreamBuilder

_MARK_LINE_WIDTH_PT = 0.5
_FOLD_DASH = [3, 3]

def _draw_marks(dest_page: pikepdf.Page, marks: Sequence[Mark]) -> None:
    """Stroke bindery marks as vectors in one balanced q...Q block.

    Appended AFTER every page placement, in the sheet's own coordinate space,
    so the two-Form-XObject-per-page invariant is unaffected by mark count.
    Marks are line segments only: no text, so pikepdf's text engine and
    reportlab both stay out of this path entirely.
    """
    if not marks:
        return
    b = ContentStreamBuilder().push().set_line_width(_MARK_LINE_WIDTH_PT)
    for mark in marks:
        if mark.kind == "fold_line":
            b.set_dashes(_FOLD_DASH, 0)
        else:
            b.set_dashes()                       # reset to solid: emits "[ ] 0 d"
        b.line(mark.x0, mark.y0, mark.x1, mark.y1).stroke_and_close()
    dest_page.contents_add(b.pop().build())
```

Call it in `_export_batched`, once per `Side`, **after** the `_place_output_page` loop:

```python
for side in _sides(sheet):
    dest_page = out.add_blank_page(page_size=plan.paper_pt)
    for output_page in side.pages:
        _place_output_page(out, dest_page, output_page, source_cache)
    _draw_marks(dest_page, side.marks)
```

Run step 3's command; green. Commit: `feat(SS-09): stroke bindery marks via ContentStreamBuilder`.

### Step 5. Failing test: fold lines dash, stations and order bars do not

```python
def test_fold_line_is_dashed_and_stations_are_solid(tmp_path):
    out = tmp_path / "dash.pdf"
    export(_folio_plan_two_up(marks=(_FOLD, _STATION)), str(out))
    stream = coalesced(str(out))
    assert b"[ 3 3 ] 0 d" in stream          # the fold line
    assert b"[ ] 0 d" in stream              # reset to solid for the station
```

Adjust `_draw_marks` until green. Commit.

### Step 6. Failing test: scale is carried identically in both cells

```python
def test_no_scale_drift_between_the_two_cells(tmp_path):
    out = tmp_path / "scaled.pdf"
    export(_folio_plan_two_up(scale=0.82), str(out))
    mats = [b.splitlines()[0].split() for b in placement_blocks(coalesced(str(out)))]
    assert len(mats) == 2
    a0, d0 = float(mats[0][0]), float(mats[0][3])
    a1, d1 = float(mats[1][0]), float(mats[1][3])
    assert a0 == pytest.approx(d0) and a1 == pytest.approx(d1)   # square, no aspect skew
    assert a0 == pytest.approx(a1)                                # one scale, both cells
```

`_place_output_page` and `_scale_flags_for` already guarantee this; the test pins it. Green.
Commit.

### Step 7. Failing test: a filler leaf does not corrupt its sibling

```python
def test_filler_leaf_exports_blank_while_its_sibling_places(tmp_path):
    out = tmp_path / "padded.pdf"
    export(_folio_plan_two_up(filler_index=1), str(out))
    assert len(placement_blocks(coalesced(str(out)))) == 1
```

A padded final signature must not be corrupted: the filler contributes no content and the
sibling still places. `_place_output_page` already returns early for a filler. Green. Commit.

### Step 8. Failing test: the artifact reopens cleanly

```python
def test_exported_folio_reopens_with_the_expected_page_count(tmp_path):
    plan = _folio_plan(sheets=4)
    out = tmp_path / "sig.pdf"
    export(plan, str(out))
    with pikepdf.open(str(out)) as pdf:
        assert len(pdf.pages) == 8              # one PDF page per Side
```

Green. Commit.

### Step 9. Confirm the constraints carried forward are still intact

No new code — run the negative greps and the existing export suite:

```bash
! grep -n "add_overlay\|draw_image\|show_text\|set_font\|reportlab" deckle/core/export.py \
  || (echo "FAIL: forbidden drawing API or text engine present" && exit 1)
grep -q "remove_unreferenced_resources" deckle/core/export.py
python -m pytest tests/test_export.py tests/test_export_marks.py -q
```

`remove_unreferenced_resources()` still runs before every save; the batched save-and-reopen at
`_BATCH_SHEETS` is untouched; `pdf.pages` is never reordered by tuple-swap or slice assignment.

### Step 10. Full gate, then commit

```bash
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "feat(SS-09): bindery mark rendering via pikepdf ContentStreamBuilder"
```

## Interface Contracts

### _draw_marks
- Direction: internal to `deckle/core/export.py`
- Owner: SS-09
- Shape: `_draw_marks(dest_page: pikepdf.Page, marks: Sequence[Mark]) -> None`. Appends
  **one** balanced `q…Q` block containing one stroked line segment per `Mark`, in the sheet's
  own coordinate space, via `dest_page.contents_add(...)`. Returns without emitting anything
  when `marks` is empty. Draws no text and creates no XObject, so it can never perturb the
  two-`Do`-blocks-per-page invariant.

### export (unchanged public surface)
- Direction: SS-09 → SS-11, SS-12
- Owner: SS-04 (MVP); SS-09 extends the body only
- Shape: `export(plan: SheetPlan, out_path: str, sheets: Sequence[int] | None = None) -> None`.
  One PDF page per `Side`; one Form XObject placement per page in `side.pages`; `side.marks`
  stroked afterward. Consumes `Placement` verbatim — never modifies, recomputes or clamps it.

### export_content_stream (the asserted artifact)
- Direction: SS-09 → SS-12 (integration), SS-11 (preview, which rasterises this artifact)
- Owner: SS-09 (`contracts.yaml: export_content_stream`)
- Shape: per folio PDF page, exactly **two** balanced `q…Q` blocks carrying a `Do` operator.
  At document scale 1.0 each is a pure `1 0 0 1 <tx> <ty> cm` translation with no scale factor,
  matching the vault's recorded `1 0 0 1 0 0 cm` / `1 0 0 1 396 0 cm`. At any other scale, both
  carry the same value in the `a` and `d` positions. Marks follow in their own block.
  **Readers must call `contents_coalesce()` before `read_bytes()`** — `page.Contents` may be
  an `Array`.

## Verification Commands

```bash
python -m pytest tests/test_export_marks.py -q
python -m pytest tests/test_export.py tests/test_render.py -q
python -m pytest tests/test_preview_fidelity.py -q
python -m pytest -q
python -m ruff check deckle tests
```

## Checks

Every command exits **0** when the criterion passes. Rows marked *(pre-verified)* were executed
against the working tree at authoring time and confirmed to exit 0.

| Criterion | Type | Command |
|---|---|---|
| `ContentStreamBuilder` is the drawing API | [MECHANICAL] | `grep -q "ContentStreamBuilder" deckle/core/export.py \|\| (echo "FAIL: ContentStreamBuilder not used" && exit 1)` |
| No overlay helper, no image draw, no text engine, no reportlab *(pre-verified)* | [MECHANICAL] | `! grep -n "add_overlay\|draw_image\|show_text\|set_font\|reportlab" deckle/core/export.py \|\| (echo "FAIL: forbidden drawing API or text engine present" && exit 1)` |
| No forbidden composition API *(pre-verified)* | [MECHANICAL] | `! grep -n "add_overlay\|merge_transformed_page\|draw_image" deckle/core/export.py \|\| (echo "FAIL: forbidden composition API present" && exit 1)` |
| Exact-placement API retained *(pre-verified)* | [MECHANICAL] | `grep -q "calc_form_xobject_placement" deckle/core/export.py \|\| (echo "FAIL: exact placement API removed" && exit 1)` |
| Form XObject source path retained *(pre-verified)* | [MECHANICAL] | `grep -q "as_form_xobject" deckle/core/export.py \|\| (echo "FAIL: as_form_xobject removed" && exit 1)` |
| Marks appended via `contents_add` *(pre-verified)* | [MECHANICAL] | `grep -q "contents_add" deckle/core/export.py \|\| (echo "FAIL: contents_add missing" && exit 1)` |
| Orphan resources still cleaned *(pre-verified)* | [MECHANICAL] | `grep -q "remove_unreferenced_resources" deckle/core/export.py \|\| (echo "FAIL: remove_unreferenced_resources missing" && exit 1)` |
| Tests coalesce before reading bytes | [MECHANICAL] | `grep -q "contents_coalesce" tests/test_export_marks.py \|\| (echo "FAIL: byte assertions will raise on an Array Contents" && exit 1)` |
| Two pure-translation `Do` blocks per folio page | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k pure_translation \|\| (echo "FAIL: not two pure-translation placements per page" && exit 1)` |
| No scale drift between the two cells | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k two_page_side_shares_identical_scale_no_drift \|\| (echo "FAIL: scale drifted between cells" && exit 1)` |
| Five marks stroke five segments | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k five_marks \|\| (echo "FAIL: mark stroke count wrong" && exit 1)` |
| Fold lines dashed, stations solid | [STRUCTURAL] | `python -m pytest tests/test_spec_residue.py -q -k "fold_lines_are_dashed_and_other_marks_are_not or markless_side_emits_no_dashed_or_solid_dash_operator" \|\| (echo "FAIL: dash pattern wrong" && exit 1)` |
| Marks are additive, never mandatory | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k side_with_no_marks_exports_no_stroke_ops \|\| (echo "FAIL: a markless side emitted strokes" && exit 1)` |
| Filler leaf does not corrupt its sibling | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k filler \|\| (echo "FAIL: padded signature corrupted" && exit 1)` |
| Artifact reopens with the expected page count | [STRUCTURAL] | `python -m pytest tests/test_export_marks.py -q -k reopens \|\| (echo "FAIL: exported PDF does not reopen cleanly" && exit 1)` |
| Export suites green | [MECHANICAL] | `python -m pytest tests/test_export.py tests/test_export_marks.py -q \|\| (echo "FAIL: export tests red" && exit 1)` |
| Renderer still reads the artifact, not `Placement` *(pre-verified form)* | [MECHANICAL] | `! grep -n "Placement" deckle/core/render.py \|\| (echo "FAIL: renderer reads Placement instead of the exported artifact" && exit 1)` |
| No Qt in `deckle.core` (anchored) *(pre-verified)* | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| Full suite still green | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: full suite red" && exit 1)` |
| Lint clean | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff findings" && exit 1)` |

**Note.** The forbidden-token grep over `export.py` passes on the current tree **only because**
the module docstring says *"the whole-page overlay/watermark helper"* instead of naming the
API. Preserve that phrasing when editing the docstring. A criterion that fails on correct code
is indistinguishable from one that caught a real defect.

**Note on `ruff`.** Invoked as `python -m ruff check`; bare `ruff` is not on the Git Bash
`PATH` in this environment (verified: exit 127 bare, exit 0 via `python -m`).
