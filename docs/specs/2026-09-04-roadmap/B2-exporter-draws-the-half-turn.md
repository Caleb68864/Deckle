# B2 — Make the exporter draw a `rotate_deg == 180` placement

**Roadmap item:** `docs/ROADMAP.md` B2
**Depends on:** —
**Blocks:** B1 (which is the spec that first makes 180 reachable; B2 must
land first or in the same commit, or B1 fixes nothing for a half turn)
**Size:** S
**Decision needed first:** none.

---

## 1. Context

`Placement.rotate_deg` is documented as taking `0`, `90`, `180` or `270`
(`deckle/core/models.py:29-31`). The exporter branches on
`rotate_deg in (90, 270)` and sends everything else — including `180` —
down the no-rotation path, where the placement is drawn as a plain
translation. A half turn is therefore **silently discarded**: no
exception, no warning, no visible difference from a placement that asked
for no rotation at all.

Today no strategy emits `180` (`layout._place_page` emits `0` or `90`),
so this is latent. **B1 makes it live** — a user's own 180 rotation, and
the composition `user 90 + landscape policy 90`, both land here.

Verified: the half turn is a no-op end to end. A 400×600 source with a
100×100 black square in its bottom-left corner, imposed one-up onto
letter, exported and rasterised at 36 dpi:

```bash
.venv/bin/python - <<'PYEOF'
import os, tempfile, pikepdf
from deckle.core.models import (LayoutSettings, OutputPage, Placement, Sheet,
                                SheetPlan, Side, SourceRef)
from deckle.core import render, export

d = tempfile.mkdtemp()
src = os.path.join(d, "corner.pdf")
pdf = pikepdf.Pdf.new()
p = pdf.add_blank_page(page_size=(400.0, 600.0))
p.contents_add(b"q\n0 0 0 rg\n0 0 100 100 re\nf\nQ\n")
pdf.save(src); pdf.close()

ref = SourceRef(path=src, page_index=0, sha256="a" * 64,
                width_pt=400.0, height_pt=600.0)

def ink(rp):
    xs = []; ys = []
    for i in range(0, len(rp.rgba), 4):
        if rp.rgba[i] < 250:
            q = i // 4; xs.append(q % rp.width); ys.append(q // rp.width)
    return (min(xs), min(ys), max(xs), max(ys))

for rot in (0, 180):
    page = OutputPage(source_ref=ref, is_filler=False,
                      placement=Placement(scale_x=1.0, scale_y=1.0,
                                          tx=100.0, ty=100.0, rotate_deg=rot))
    plan = SheetPlan(sheets=[Sheet(index=0, front=Side(pages=(page,)), back=None)],
                     paper_pt=(612.0, 792.0), warnings=[])
    print(rot, ink(render.render_sheet(plan, 0, "front", 36)))
    export.clear_sheet_cache()
PYEOF
```

Output:

```
0 (50, 296, 99, 345)
180 (50, 296, 99, 345)
```

Identical. The content should have turned about `(100 + 200, 100 + 300)`
= `(300, 400)` and landed at sheet points `x 400..500, y 600..700`,
i.e. image pixels `(200, 46)-(249, 95)`.

**Why it matters to a person printing a book.** A page imported upside
down — the single most common scanner mistake — cannot be corrected. The
correction is offered in the UI, accepted, persisted into the `.deckle`
file, shown in the plan hash, and then thrown away by the last function
before ink.

## 2. Current code

`deckle/core/export.py:341-381`, verbatim:

```python
    placement = output_page.placement
    rotate_deg = placement.rotate_deg % 360

    if rotate_deg in (90, 270):
        # The final on-sheet footprint (tx/ty/width/height in Placement) is
        # already expressed post-rotation. Place the form at its natural
        # (pre-rotation) orientation centered on that same footprint, then
        # rotate the whole thing about the footprint's center -- this keeps
        # the placement rect's own scale exact while the wrapping transform
        # supplies the rotation Imposer decided on.
        scaled_w = src_w * placement.scale_x
        scaled_h = src_h * placement.scale_y
        footprint_w, footprint_h = scaled_h, scaled_w
        cx = placement.tx + footprint_w / 2.0
        cy = placement.ty + footprint_h / 2.0
        prerotate_rect = Rectangle(
            cx - scaled_w / 2.0, cy - scaled_h / 2.0, cx + scaled_w / 2.0, cy + scaled_h / 2.0
        )
        allow_shrink, allow_expand = _scale_flags_for(placement.scale_x)
        inner = dest_page.calc_form_xobject_placement(
            formx,
            name,
            prerotate_rect,
            invert_transformations=True,
            allow_shrink=allow_shrink,
            allow_expand=allow_expand,
        )
        rotation = _rotation_matrix(rotate_deg, cx, cy)
        content_stream = f"q\n{rotation}\n{inner.decode('latin-1')}\nQ\n".encode("latin-1")
    else:
        rect = _rect_for_placement(placement, src_w, src_h)
        allow_shrink, allow_expand = _scale_flags_for(placement.scale_x)
        content_stream = dest_page.calc_form_xobject_placement(
            formx,
            name,
            rect,
            invert_transformations=True,
            allow_shrink=allow_shrink,
            allow_expand=allow_expand,
        )
    dest_page.contents_add(content_stream)
```

Line 344 — `if rotate_deg in (90, 270):` — is the whole defect. `180`
falls into the `else`, which draws a pure translation.

The two supporting helpers, both already correct for 180:

`deckle/core/export.py:166-183`:

```python
def _rect_for_placement(placement: Placement, src_w: float, src_h: float) -> Rectangle:
    """The destination rectangle a source page's content is placed into.

    ``invert_transformations=True`` means pikepdf's own placement math
    expects the *destination* rectangle in final (post-scale) sheet
    coordinates, derived here directly from ``Placement`` without any
    reinterpretation of scale or translation. Sized to the page's own
    (unrotated) natural dimensions -- rotation, if any, is applied as a
    separate wrapping transform around a pivot, see ``_place_output_page``.
    """
    scaled_w = src_w * placement.scale_x
    scaled_h = src_h * placement.scale_y
    return Rectangle(
        placement.tx,
        placement.ty,
        placement.tx + scaled_w,
        placement.ty + scaled_h,
    )
```

`deckle/core/export.py:186-193`:

```python
def _rotation_matrix(rotate_deg: int, cx: float, cy: float) -> str:
    """A ``cm`` matrix string rotating ``rotate_deg`` about ``(cx, cy)``."""
    theta = math.radians(rotate_deg)
    cos_t = round(math.cos(theta), 10)
    sin_t = round(math.sin(theta), 10)
    e = cx - cx * cos_t + cy * sin_t
    f = cy - cx * sin_t - cy * cos_t
    return f"{cos_t} {sin_t} {-sin_t} {cos_t} {e} {f} cm"
```

For 180 it already produces the right thing, and it is direction-agnostic
(a half turn is its own inverse), so B1's clockwise change does not
affect it:

```bash
.venv/bin/python -c "
from deckle.core.export import _rotation_matrix
print(_rotation_matrix(180, 100.0, 200.0))"
# -1.0 0.0 -0.0 -1.0 200.0 400.0 cm
```

### Every call site that reads `Placement.rotate_deg`

`grep -rn "rotate_deg" deckle/core/export.py`:

- `deckle/core/export.py:142` — `_output_page_key`, the cache key. Already
  includes the value, so a 180 placement already hashes distinctly from a
  0 one and no cache work is needed.
- `deckle/core/export.py:272, 279` — `_cropped_source_box`, reading the
  *source page's* `/Rotate`, not the placement. Untouched.
- `deckle/core/export.py:342, 344` — this defect.

Elsewhere: `deckle/app/views/preview_view.py:82`
(`_output_page_bbox`) already treats 180 correctly — it swaps only for
90/270, which is right — and `deckle/core/layout.py:611`
(`actual_margins_pt`) does the same.

### Existing tests that touch this

- `tests/test_export.py:171-194` —
  `test_fit_already_fits_emits_pure_translation` regexes the first `cm`
  operator out of the stream and asserts `(a, b, c, d) == (1, 0, 0, 1)`.
  Its placement has `rotate_deg=0` and must stay on the `else` branch.
- `tests/test_export.py:200-229` —
  `test_export_sheets_subset_matches_full_export` regexes the first `cm`.
  Unrotated.
- `tests/test_spec_residue.py:352-393` — the dashed-marks test regexes
  mark blocks, not placement blocks. Unaffected.
- **Nothing exports a placement with `rotate_deg` 90, 180 or 270.**
  `grep -rn "rotate_deg=90\|rotate_deg=180\|rotate_deg=270" tests/`
  returns only `tests/test_models.py:55` and
  `tests/test_project_io.py:39,49`, none of which export.

## 3. Change

### The geometry

For a half turn, the on-sheet footprint is **unswapped** — a page turned
180 occupies exactly the same rectangle it occupied upright — so:

```
scaled_w  = src_w * placement.scale_x
scaled_h  = src_h * placement.scale_y
cx        = placement.tx + scaled_w / 2.0
cy        = placement.ty + scaled_h / 2.0
prerotate_rect = Rectangle(placement.tx, placement.ty,
                           placement.tx + scaled_w, placement.ty + scaled_h)
              == _rect_for_placement(placement, src_w, src_h)
matrix    = _rotation_matrix(180, cx, cy)
          == "-1.0 0.0 -0.0 -1.0 {2*cx} {2*cy} cm"
```

which is the general `-1 0 0 -1 2cx 2cy cm` — the point reflection about
`(cx, cy)`. The rotation branch's existing code already computes exactly
this once `footprint_w, footprint_h` is not swapped, because
`cx - scaled_w/2 == placement.tx` when `cx == placement.tx + scaled_w/2`.
So the change is one line of footprint arithmetic plus the branch
condition — no new pivot math.

### Numbered edits

All in `deckle/core/export.py`, function `_place_output_page`.

1. **Line 344**, replace

   ```python
       if rotate_deg in (90, 270):
   ```

   with

   ```python
       if rotate_deg in (90, 180, 270):
   ```

2. **Lines 345-353**, replace the comment and the footprint assignment

   ```python
           # The final on-sheet footprint (tx/ty/width/height in Placement) is
           # already expressed post-rotation. Place the form at its natural
           # (pre-rotation) orientation centered on that same footprint, then
           # rotate the whole thing about the footprint's center -- this keeps
           # the placement rect's own scale exact while the wrapping transform
           # supplies the rotation Imposer decided on.
           scaled_w = src_w * placement.scale_x
           scaled_h = src_h * placement.scale_y
           footprint_w, footprint_h = scaled_h, scaled_w
   ```

   with

   ```python
           # The final on-sheet footprint (tx/ty/width/height in Placement) is
           # already expressed post-rotation. Place the form at its natural
           # (pre-rotation) orientation centered on that same footprint, then
           # rotate the whole thing about the footprint's center -- this keeps
           # the placement rect's own scale exact while the wrapping transform
           # supplies the rotation Imposer decided on.
           #
           # A HALF TURN does not swap the footprint: a page turned 180
           # covers the rectangle it covered upright. Only the quarter
           # turns transpose it. This branch used to test `in (90, 270)`
           # and drop 180 into the translation path below, where it was
           # discarded in silence -- the exported ink landed in exactly the
           # pixels an unrotated placement produced.
           scaled_w = src_w * placement.scale_x
           scaled_h = src_h * placement.scale_y
           if rotate_deg == 180:
               footprint_w, footprint_h = scaled_w, scaled_h
           else:
               footprint_w, footprint_h = scaled_h, scaled_w
   ```

   Nothing else in the branch changes: `cx`, `cy`, `prerotate_rect`,
   `_scale_flags_for`, `calc_form_xobject_placement` and
   `_rotation_matrix` are all already correct for 180 given the right
   footprint.

3. **`deckle/core/models.py:29-31`** — no edit here. `Placement.rotate_deg`
   already documents 180 as legal; B1 rewrites this docstring for the
   direction convention.

No new module, so no `docs/api/` page.

## 4. Tests

All in `tests/test_export.py`, extending the existing structure. Write
them first; each fails as stated on the unfixed tree.

### Helpers

Add at module scope, beside the existing `_write_source_pdf`
(`tests/test_export.py:27-34`):

```python
def _write_corner_marked_pdf(tmp_path, n_pages: int,
                             page_size=(400.0, 600.0)) -> str:
    """A source whose ink is a black square in each page's BOTTOM-LEFT.

    Asymmetric on both axes on purpose. A blank page -- which is what
    `_write_source_pdf` makes -- looks identical under every rotation, so
    a test built on one can assert nothing about which way a page turned.
    """
    path = os.path.join(str(tmp_path), "corner.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        page = pdf.add_blank_page(page_size=page_size)
        page.contents_add(b"q\n0 0 0 rg\n0 0 100 100 re\nf\nQ\n")
    pdf.save(path)
    pdf.close()
    return path


def _ink_bbox_px(rendered) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` of non-white pixels, image coords."""
    xs: list[int] = []
    ys: list[int] = []
    for i in range(0, len(rendered.rgba), 4):
        if rendered.rgba[i] < 250:
            pixel = i // 4
            xs.append(pixel % rendered.width)
            ys.append(pixel // rendered.width)
    assert xs, "the rasterised sheet has no ink at all"
    return (min(xs), min(ys), max(xs), max(ys))


def _one_page_plan(ref, rotate_deg: int, tx=100.0, ty=100.0) -> SheetPlan:
    page = OutputPage(
        source_ref=ref,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=tx, ty=ty,
                            rotate_deg=rotate_deg),
        is_filler=False,
    )
    return SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,)), back=None)],
        paper_pt=LETTER, warnings=[],
    )
```

`OutputPage`, `Placement`, `Sheet`, `SheetPlan`, `Side` and `SourceRef`
are already imported at `tests/test_export.py:13-22`. `render` is not;
add `from deckle.core import render`.

Every test that rasterises must call `export.clear_sheet_cache()` in a
`finally` — `render.render_sheet` routes through `export_sheet_cached`
and the cache is module-level and survives between tests
(`tests/test_export.py:107` sets the precedent).

### `test_a_half_turn_placement_is_actually_turned`

Build a corner-marked source, `_one_page_plan(ref, 180)`, rasterise
`render.render_sheet(plan, 0, "front", 36)` and assert
`_ink_bbox_px(...) == (200, 46, 249, 95)`.

Derivation: scale 1.0, footprint 400×600 at `(100, 100)`; the square
occupies sheet points `x 100..200, y 100..200`; a half turn about
`(300, 400)` maps it to `x 400..500, y 600..700`; at 36 dpi on a
612×792 sheet (306×396 px) that is `x 200..250`, `y (792-700)/2 = 46`
to `(792-600)/2 = 96`.

Unfixed: `(50, 296, 99, 345)` — the untouched original position.
`assert (50, 296, 99, 345) == (200, 46, 249, 95)`.

### `test_an_unrotated_placement_is_the_control_for_the_half_turn`

The same with `rotate_deg=0`; assert `(50, 296, 99, 345)`. Passes today
and after; its job is to prove `_ink_bbox_px` measures what it claims and
that the 180 test is not asserting an accident.

### `test_a_half_turn_pivots_on_the_unswapped_footprint_centre`

Export `_one_page_plan(ref, 180)` with `export_fn`, read the page's
content stream with the existing `_content_bytes`
(`tests/test_export.py:37-43`), and assert
`b"-1.0 0.0 -0.0 -1.0 600.0 800.0 cm"` appears in it.

That string is `-1 0 0 -1 2cx 2cy cm` — the point reflection — with
`cx = 100 + 400/2 = 300` and `cy = 100 + 600/2 = 400`. It is the *whole*
test for step 2's footprint line as well as step 1's branch: a
half turn that wrongly transposed its footprint would pivot on
`cx = 100 + 600/2 = 400`, `cy = 100 + 400/2 = 300` and emit
`… 800.0 600.0 cm`, which is on the sheet and looks entirely plausible.

Unfixed: the stream is
`b'q\n1 0 0 1 100 100 cm\n/Fx… Do\nQ\n'` — a pure translation with no
`-1.0` matrix at all.

### `test_a_quarter_turn_still_transposes_its_footprint`

The half turn's sibling, so the `if rotate_deg == 180` cannot be
inverted. `_one_page_plan(ref, 90, tx=0.0, ty=0.0)` on the 400×600
corner-marked source at scale 1.0: the post-rotation footprint is
600×400 at the sheet origin, i.e. sheet points `x 0..600, y 0..400`, i.e.
image pixels `x 0..300`, `y 196..396` on a 306×396 raster. Assert the ink
bbox lies inside that box.

Containment, not a corner: **B1 changes 90 from counter-clockwise to
clockwise**, which moves the corner but not the footprint, so writing it
this way means B1 does not have to edit this test. Passes today — the ink
bbox measures `(250, 346, 299, 395)` — and after.

## 5. Acceptance

| Check | Command |
|---|---|
| the branch admits 180 | `grep -n "if rotate_deg in (90, 180, 270):" deckle/core/export.py` |
| the old two-value branch is gone | `! grep -n "if rotate_deg in (90, 270):" deckle/core/export.py` (matches `deckle/core/export.py:344` today; note `:279` in `_cropped_source_box` is a *different* line testing the source page's `/Rotate` and must survive — the grep above includes the leading `if ` and so does not match it) |
| the footprint special-case exists | `grep -n "if rotate_deg == 180:" deckle/core/export.py` |
| the repro from §1 now differs | paste §1's fenced block; the two lines must differ, printing `0 (50, 296, 99, 345)` and `180 (200, 46, 249, 95)` |
| the new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_export.py -q --no-header -p no:cacheprovider -k "half_turn or unrotated_placement or footprint"` |
| the pure-translation test is untouched | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_export.py -q --no-header -p no:cacheprovider -k pure_translation` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (1507 passed + the new tests, 22 skipped, the same 2 R0.3/R0.4 failures) |

## 6. Out of scope

- **B1** — making the imposer emit 180 in the first place. This spec
  makes the exporter honour a value nothing currently produces; on its
  own it changes no output. That is deliberate: it is a self-contained,
  independently testable half of a two-part fix, and it must merge first
  so B1's half-turn test can be red for the right reason.
- **The rotation direction.** `_rotation_matrix` is counter-clockwise
  today and B1 makes it clockwise. A half turn is its own inverse, so
  this spec's expected matrix (`-1.0 0.0 -0.0 -1.0 2cx 2cy cm`) and every
  pixel value in §4 are identical before and after that change. Do not
  pre-empt B1 by touching `_rotation_matrix` here.
- **B20** — `export(sheets=...)` skipping unknown indices. Same module,
  different function (`export`, not `_place_output_page`).
- **`flatten_rotation`** and the `DRIVERS_IGNORING_ROTATE` scaffold
  (M7). Not touched.

## 7. decisions.md entry

```
## 2026-09-05 — The exporter dropped a half turn without saying so
- Symptom: `Placement.rotate_deg` documents 0, 90, 180 and 270, and `_place_output_page` branched on `rotate_deg in (90, 270)`. A 180 placement fell into the translation path and was drawn upright. Measured on a 400x600 source with a black square in its bottom-left corner: exported at `rotate_deg=180` the ink landed in image pixels `(50, 296, 99, 345)` -- byte-identical to `rotate_deg=0`, when it should have been at `(200, 46, 249, 95)`.
- Fix: The branch now takes `(90, 180, 270)`, and the footprint is swapped only for the quarter turns -- a page turned 180 covers the rectangle it covered upright. Everything else in the branch was already right for 180: the pivot is the footprint centre either way, and `_rotation_matrix(180, cx, cy)` already yields the point reflection `-1 0 0 -1 2cx 2cy cm`.
- Surfaces: Latent until B1, because no strategy emitted 180 -- `_place_page` emitted 0 or 90. It is the shape this log has caught repeatedly: a value declared in a `Literal`, persisted, hashed into the export cache key, and then not honoured by the one function that turns it into ink. `deckle/core/models.py` said 180 was legal; `deckle/core/export.py` said nothing and did nothing.
- Watch: The `else` was a silent default. A branch whose condition enumerates *some* of a declared value set needs the remainder to be an error or to be handled, not to fall through to the neutral case -- because the neutral case always looks plausible. The new export tests assert where the ink lands in the rasterised sheet rather than what the content stream says, so a future third interpretation of the same field cannot pass by agreeing with itself.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **There are two `in (90, 270)` tests in `export.py`.**
  `deckle/core/export.py:279` is inside `_cropped_source_box` and reads
  the *source file's* `/Rotate` to transpose the cropped size. It is
  correct and must not be changed: 180 genuinely does not transpose a
  page box. Only `:344` moves.
- **`render.render_sheet` goes through the module-level sheet cache.**
  `export._cache` is keyed on `(sheet_index, _plan_hash(plan))` and
  survives across tests and across process runs (the files live in
  `/tmp/deckle_export_cache`). Call `export.clear_sheet_cache()` in every
  test that rasterises, and `rm -rf /tmp/deckle_export_cache` once after
  implementing, or a hand-run repro will show you the pre-fix picture.
- **`Side(pages=())` raises.** `deckle/core/models.py:181-186` refuses an
  empty side; a hand-built plan needs at least one `OutputPage`.
- **`_verify_output` checks page size, not content.** A wrong rotation
  passes it. That is why §4 rasterises.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`), and
  the new test helpers must not either — `deckle.core.render` returns raw
  RGBA buffers precisely so a test can read pixels without Qt.
