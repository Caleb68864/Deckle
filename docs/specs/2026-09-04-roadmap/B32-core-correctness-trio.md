# B32 — Three measurements that answer for a page other than the one asked about

**Roadmap item:** `docs/ROADMAP.md` B32
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Three small, independent defects, each of the same shape: a function derives
a number from part of a page's description and silently ignores the rest, so
it returns a confident answer about a page that does not exist.

### 1a. `actual_margins_pt` ignores `crop_pt`

`actual_margins_pt` measures where a placed page really landed, by
reconstructing its on-sheet footprint from `SourceRef.width_pt/height_pt`
and `Placement.scale_x`. But the imposer scales the **cropped** page, and
`OutputPage.crop_pt` — sitting right there on the same object — records by
how much. The measured footprint is therefore the uncropped page scaled as
though it were cropped, which is larger than what is drawn, so the fore-edge
and head margins come back negative on a job that has generous margins on
paper.

`OutputPage.crop_pt`'s own docstring already says this is the failure mode
(`deckle/core/models.py:132-136`): *"a consumer that ignores this field
draws the whole source page scaled as though it were cropped"*.

The only caller is the test suite, which the function's docstring says
plainly — and that is exactly when a wrong answer is most expensive: a
future assertion about margins under a crop would be written against the
wrong numbers and would pin them.

**Verified.** Save this as `/tmp/b32a.py` and run
`.venv/bin/python /tmp/b32a.py` from the repo root:

```python
from deckle.core.layout import GutterShiftStrategy, actual_margins_pt
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

ref = SourceRef(path="s.pdf", page_index=0, sha256="a" * 64,
                width_pt=612.0, height_pt=792.0)
pages = [SourcePage(ref=ref, rotate_deg=0, skipped=False)]
settings = LayoutSettings(
    paper=(612.0, 792.0), gutter_pt=72.0, binding_edge="left",
    margin_outer_pt=36.0, margin_top_pt=36.0, margin_bottom_pt=36.0,
    crop_odd_pt=(72.0, 72.0, 72.0, 72.0),
)
plan = GutterShiftStrategy().impose(pages, settings)
op = plan.sheets[0].front.pages[0]
print("placement:", op.placement)
print("crop_pt:  ", op.crop_pt)
print("measured: ", actual_margins_pt(op, settings.paper, is_recto=True,
                                      binding_edge="left"))
```

Current output:

```
placement: Placement(scale_x=1.0769230769230769, scale_y=1.0769230769230769, tx=72.0, ty=47.076923076923094, rotate_deg=0)
crop_pt:   (72.0, 72.0, 72.0, 72.0)
measured:  (72.0, -119.0769230769231, -108.0, 47.076923076923094)
```

The correct answer is `(72.0, 36.0, 47.0769..., 47.0769...)`: a 1-inch crop
on all four edges of a Letter page leaves 468 × 648, scaled by 504/468 to
504 × 697.85, which sits exactly on the requested 72pt gutter and 36pt
fore-edge with the vertical slack split. The function instead reports the
fore-edge overflowing by 119pt and the head by 108pt — on a job where
nothing overflows anything.

### 1b. `_blank_thumbnail` ignores `rotate_deg`

Every real page in the thumbnail grid is rasterised with
`rotation=_rotation_quarter_turns(source_page.rotate_deg)`, so rotating a
page 90° turns its thumbnail. An inserted blank takes a different path —
`_blank_thumbnail(ref, dpi)` — which is never told the rotation and builds
its white rectangle from `ref.width_pt` and `ref.height_pt` as stored.

The user action: insert a blank in Arrange (it takes the project's paper
size, `deckle/app/state.py:63-83`, so on the default Letter it is portrait),
select it, press R twice to rotate it 90°. Every real page beside it turns
landscape; the blank stays portrait. Nothing in `arrange_view.rotate`
(`deckle/app/views/arrange_view.py:126-136`) excludes blanks, so this is a
reachable state, and the grid then shows the document as it is not.

### 1c. `ink_bbox`'s cache key omits `dpi`

`ink_bbox(ref, dpi=36)` caches per `SourceRef`. `dpi` changes the answer —
it is the resolution the page is rasterised at before the content mask is
taken — but it is not part of the key, so the second call for the same page
at a different resolution silently returns the first call's answer without
rasterising.

**Verified.** With a counting patch over `render._rasterize_for_bbox`,
calling `ink_bbox(ref, dpi=36)` then `ink_bbox(ref, dpi=300)` rasterises
**once**, at 36, and returns the 36-dpi box for the 300-dpi request.

Why it matters on paper: `auto_crop_insets(pages, margin_pt=..., dpi=...)`
is what `--auto-crop` and the panel's *Measure crop from the ink* button
call, and its whole point is that a descender or a hairline rule can fall
outside a low-dpi box — which is what `margin_pt` exists to buy back. Anyone
who responds to a clipped crop by re-measuring at a higher dpi gets the
identical answer, instantly, and reasonably concludes the resolution makes
no difference.

## 2. Current code

### 1a — `deckle/core/layout.py:602-626`

```python
    if cell is None:
        cell = _full_sheet_cell(paper)
    cx0, cy0, cx1, cy1 = cell
    p = output_page.placement
    ref = output_page.source_ref
    if ref is None:
        return (0.0, 0.0, 0.0, 0.0)

    src_w, src_h = ref.width_pt, ref.height_pt
    if p.rotate_deg in (90, 270):
        src_w, src_h = src_h, src_w
    scaled_w = src_w * p.scale_x
    scaled_h = src_h * p.scale_y

    left = p.tx - cx0
    right = cx1 - p.tx - scaled_w
    bottom = p.ty - cy0
    top = cy1 - p.ty - scaled_h
```

What the imposer actually scaled, `deckle/core/layout.py:375` and
`182-201`:

```python
    src_w, src_h = _source_dims(slot, settings)
```

```python
def _source_dims(
    page: SourcePage, settings: LayoutSettings | None = None
) -> tuple[float, float]:
    ...
    width = page.ref.width_pt
    height = page.ref.height_pt
    if settings is not None:
        width, height = _cropped_dims(width, height, crop_for(page, settings))
    if page.rotate_deg in (90, 270):
        width, height = height, width
    return width, height
```

and `_cropped_dims` (`deckle/core/layout.py:156-180`) subtracts
`left + right` from the width and `bottom + top` from the height.

The exporter agrees: `deckle/core/export.py:334` takes its footprint from
`_cropped_source_box(src_page, output_page.crop_pt, ref)`, and
`deckle/core/export.py:351-352` computes `scaled_w = src_w *
placement.scale_x` from **that**. So `actual_margins_pt` is the only one of
the three that measures the uncropped page.

**Every call site of `actual_margins_pt`**, from `grep -rn
"actual_margins_pt" --include='*.py' .`:

| Site | Passes a cropped page? |
|---|---|
| `deckle/core/layout.py:559` | definition |
| `tests/test_layout.py:28, 78` | no crop |
| `tests/test_layout_saddle.py:27, 191, 194, 216, 219` | no crop |
| `tests/test_cell_geometry.py:35, 82` | no crop |
| `tests/test_spine_side_precedence.py:42, 79, 90, 103, 109, 184, 188` | no crop |
| `tests/test_side_reporting.py:36, 421, 424` | no crop |

No production code calls it — stated outright in its own docstring
(`deckle/core/layout.py:576-580`) and recorded in `docs/decisions.md:622`.
No existing test passes a cropped page, which is why nothing is red today.

### 1b — `deckle/core/render.py:326-334` and `355-374`

```python
                if is_blank_page(source_page):
                    # A blank references no file. Opening its empty path
                    # raised FileNotFoundError, which failed the WHOLE
                    # window -- one inserted blank left every thumbnail
                    # beside it missing too.
                    result.append(_blank_thumbnail(ref, dpi))
                    continue
```

```python
def _blank_thumbnail(ref: SourceRef, dpi: int) -> RenderedPage:
    """A white thumbnail matching a blank page's shape.
    ...
    """
    scale = dpi / 72.0
    width = max(1, int(round(ref.width_pt * scale)))
    height = max(1, int(round(ref.height_pt * scale)))
    return RenderedPage(
        width=width,
        height=height,
        rgba=bytes([255, 255, 255, 255]) * (width * height),
    )
```

Compare the real-page branch four lines below, `deckle/core/render.py:339-348`:

```python
                result.append(
                    _pil_to_rendered_page(
                        rasterize_page(
                            doc,
                            ref.page_index,
                            scale=dpi / 72,
                            rotation=_rotation_quarter_turns(source_page.rotate_deg),
                        )
                    )
                )
```

`source_page.rotate_deg` is in scope at the blank branch and is not passed.

**Every call site of `_blank_thumbnail`:** `deckle/core/render.py:333`
(the only one) and its definition at `:355`. Nothing outside the module
references it.

### 1c — `deckle/core/render.py:382-383, 400-445`

```python
_ink_bbox_cache: OrderedDict[SourceRef, tuple[float, float, float, float]] = OrderedDict()
_ink_bbox_cache_lock = threading.Lock()
```

```python
    with _ink_bbox_cache_lock:
        cached = _ink_bbox_cache.get(ref)
        if cached is not None:
            _ink_bbox_cache.move_to_end(ref)
            return cached

    pil_image = _rasterize_for_bbox(ref, dpi)
    ...
    with _ink_bbox_cache_lock:
        _ink_bbox_cache[ref] = result
        _ink_bbox_cache.move_to_end(ref)
        while len(_ink_bbox_cache) > _INK_BBOX_CACHE_MAX:
            _ink_bbox_cache.popitem(last=False)
    return result
```

The docstring at `:410-414` states the intent — *"Cached per ``SourceRef``
-- calling this twice for the same ref rasterizes only once"* — which is a
correct description of a key that is missing a dimension.

**Every caller of `ink_bbox`:**

- `deckle/core/render.py:613` — `auto_crop_insets`, passing its own `dpi`
  parameter (default 36).
- `tests/test_render.py:177, 178, 187, 203` — `dpi` default and `dpi=72`.
- `tests/test_hardening_limits.py:554, 568, 569, 570, 571` — all `dpi=12`.
- `tests/test_render_concurrency.py:85, 106, 122, 168` — default dpi.
- `tests/test_auto_crop.py` reaches it through `auto_crop_insets`.

**Every reader of `_ink_bbox_cache` as a mapping** — these pin the key type
and must be updated in step 8:

- `tests/test_hardening_limits.py:556-559` — `len(...) == 2`,
  `refs[0] not in ...`, `refs[1] in ...`, `refs[2] in ...`.
- `tests/test_hardening_limits.py:573-574` — `refs[0] in ...`,
  `refs[1] not in ...`.

`deckle/core/render.py:634-640` `clear_ink_bbox_cache` only calls `.clear()`
and is key-agnostic.

## 3. Change

Three independent edits, in this order. Each is self-contained; none depends
on another.

### 1a — subtract the crop before scaling

The formula, matching `_source_dims` and `export._cropped_source_box`
exactly: **crop first, in the source page's own orientation; then swap for
the placement's rotation; then scale.**

```
crop = output_page.crop_pt or (0.0, 0.0, 0.0, 0.0)
left_in, bottom_in, right_in, top_in = crop
src_w = ref.width_pt  - left_in   - right_in
src_h = ref.height_pt - bottom_in - top_in
if p.rotate_deg in (90, 270):
    src_w, src_h = src_h, src_w
scaled_w = src_w * p.scale_x
scaled_h = src_h * p.scale_y
```

The order is load-bearing and is the order `_source_dims` uses: the insets
are named in the frame of the page as it exists in the file — "left" is the
file's left edge, not the cell's — so they are subtracted before any swap.

1. **`deckle/core/layout.py`**, in `actual_margins_pt`, replace lines
   610-614 with:

   ```python
    # The imposer scales the CROPPED page (`_source_dims`), and so does the
    # exporter (`export._cropped_source_box`). Measuring the uncropped one
    # reports a footprint larger than anything that is drawn, so a job with
    # generous margins comes back with the fore-edge and head overflowing.
    # Insets are subtracted before the rotation swap because they are named
    # in the source page's own frame -- "left" is the left edge of the page
    # in the file, not of the cell it is placed in.
    crop = output_page.crop_pt or (0.0, 0.0, 0.0, 0.0)
    src_w = ref.width_pt - crop[0] - crop[2]
    src_h = ref.height_pt - crop[1] - crop[3]
    if p.rotate_deg in (90, 270):
        src_w, src_h = src_h, src_w
    scaled_w = src_w * p.scale_x
    scaled_h = src_h * p.scale_y
   ```

   Indices rather than unpacking because only two of the four are used per
   axis and `crop[0] - crop[2]` reads as the pair it is; the tuple order is
   `(left, bottom, right, top)`, documented on `LayoutSettings.crop_odd_pt`
   (`deckle/core/models.py:431-457`).

2. **`deckle/core/layout.py`**, in `actual_margins_pt`'s docstring, add to
   the `:param output_page:` entry:

   ```
    :param output_page: the placed page to measure. Its ``crop_pt`` is
        honoured -- the footprint measured is the cropped content, which is
        what the imposer scaled and what the exporter draws. A filler page
        has no content, so it measures as all zeros rather than raising.
   ```

   Note there is no `settings` parameter to consult and none is added: the
   crop that applied to *this* page is already recorded on the `OutputPage`,
   which is why `crop_pt` exists (`deckle/core/models.py:124-136`). Deriving
   it from `LayoutSettings` here was the rejected alternative — it would be a
   second implementation of `crop_for`, free to disagree with the plan.

### 1b — give the blank its rotation

3. **`deckle/core/render.py`**, change the signature to keyword-only:

   ```python
   def _blank_thumbnail(ref: SourceRef, dpi: int, *, rotate_deg: int) -> RenderedPage:
   ```

   No default. The one call site has the value in hand, and a default of 0
   would let the next caller reintroduce the bug silently.

4. **`deckle/core/render.py`**, in `_blank_thumbnail`'s body, swap the
   dimensions for a quarter turn, using the same predicate the rest of the
   codebase uses (`_source_dims`, `actual_margins_pt`, `loader.py:611-612`):

   ```python
    scale = dpi / 72.0
    width_pt, height_pt = ref.width_pt, ref.height_pt
    # A real page's thumbnail is turned by pdfium (`rotation=` in the
    # branch above), which changes the bitmap's shape. A blank is drawn
    # rather than rendered, so the swap has to be done here or a rotated
    # blank stays portrait in a row of landscape pages.
    if rotate_deg % 360 in (90, 270):
        width_pt, height_pt = height_pt, width_pt
    width = max(1, int(round(width_pt * scale)))
    height = max(1, int(round(height_pt * scale)))
   ```

   `% 360` because `arrange_view.rotate` documents its argument as
   "normalised downstream" (`deckle/app/views/arrange_view.py:131-132`), so
   `270 + 90 == 360` and `-90` both reach here.

5. **`deckle/core/render.py`**, in `_blank_thumbnail`'s docstring, add:

   ```
    :param rotate_deg: the user's rotation for this page. Keyword-only and
        without a default: the caller always has it, and a default of 0
        would let a new call site drop it silently.
   ```

6. **`deckle/core/render.py:333`**, the call site:

   ```python
                    result.append(
                        _blank_thumbnail(ref, dpi, rotate_deg=source_page.rotate_deg)
                    )
   ```

### 1c — put `dpi` in the cache key

7. **`deckle/core/render.py:382`**, widen the key:

   ```python
   _ink_bbox_cache: OrderedDict[
       tuple[SourceRef, int], tuple[float, float, float, float]
   ] = OrderedDict()
   ```

   and in `ink_bbox`, key every access on `(ref, dpi)`:

   ```python
    key = (ref, dpi)
    with _ink_bbox_cache_lock:
        cached = _ink_bbox_cache.get(key)
        if cached is not None:
            _ink_bbox_cache.move_to_end(key)
            return cached

    pil_image = _rasterize_for_bbox(ref, dpi)
    ...
    with _ink_bbox_cache_lock:
        _ink_bbox_cache[key] = result
        _ink_bbox_cache.move_to_end(key)
        while len(_ink_bbox_cache) > _INK_BBOX_CACHE_MAX:
            _ink_bbox_cache.popitem(last=False)
    return result
   ```

   A tuple of a frozen dataclass and an int hashes by value, so the cache
   keeps the property its docstring names: two `SourceRef`s describing the
   same page at the same resolution share one entry.

   The rejected alternative was rounding or normalising `dpi` into buckets
   so nearby resolutions share an entry. Rejected because `ink_bbox` returns
   points, not pixels, and a caller that asks for 300 and is handed 288 has
   no way to know — the same class of silence this fixes.

8. **`deckle/core/render.py`**, in `ink_bbox`'s docstring, replace

   ```
    Cached per ``SourceRef`` -- calling this twice for the same ref
    rasterizes only once.
   ```

   with

   ```
    Cached per ``(ref, dpi)`` -- calling this twice for the same ref at the
    same resolution rasterizes only once. ``dpi`` is part of the key
    because it changes the answer: the box comes off a raster taken at that
    resolution, and a caller who re-measures at a higher one after a crop
    clipped a descender must get a new measurement, not the old one back.
   ```

9. **`tests/test_hardening_limits.py:556-559 and 573-574`**: these read the
   cache as a mapping keyed by `ref` and go red on step 7. Both call
   `ink_bbox(..., dpi=12)` throughout, so the update is mechanical — replace
   each `refs[i]` used as a key with `(refs[i], 12)`:

   ```python
       assert len(render._ink_bbox_cache) == 2
       assert (refs[0], 12) not in render._ink_bbox_cache
       assert (refs[1], 12) in render._ink_bbox_cache
       assert (refs[2], 12) in render._ink_bbox_cache
   ```

   ```python
       assert (refs[0], 12) in render._ink_bbox_cache
       assert (refs[1], 12) not in render._ink_bbox_cache
   ```

   Add one line to each docstring recording that the key is `(ref, dpi)`, so
   the literal `12` reads as the resolution and not as a magic number.

## 4. Tests

Write all five before touching `deckle/`, and confirm each fails for the
stated reason.

### 1a

**File:** `tests/test_crop.py` (existing; it already holds the crop
arithmetic tests, imports `GutterShiftStrategy`, and supplies
`_page(index, w=400.0, h=600.0, rotate=0)` and `_settings(**kw)` at
`tests/test_crop.py:31-40`). Add `actual_margins_pt` to the
`from deckle.core.layout import ...` line at `:26`.

#### `test_measured_margins_honour_the_crop`

- **Setup:** one Letter source page — `_page(0, w=612.0, h=792.0)` —
  and `_settings(paper=(612.0, 792.0),
  gutter_pt=72.0, binding_edge="left", margin_outer_pt=36.0,
  margin_top_pt=36.0, margin_bottom_pt=36.0, crop_odd_pt=(72.0, 72.0, 72.0,
  72.0))`. Impose with `GutterShiftStrategy`, take
  `plan.sheets[0].front.pages[0]`.
- **Assertion in words:** the four measured margins are
  `(72.0, 36.0, 47.0769..., 47.0769...)` to `pytest.approx` — the requested
  gutter and fore-edge exactly, and the vertical slack split evenly between
  head and tail. Specifically: `inner == approx(72.0)`,
  `outer == approx(36.0)`, `top == approx(bottom)`, and every value `> 0`.
- **Expected failure on the unfixed tree:**
  `assert -119.0769230769231 == approx(36.0 ± 3.6e-05)` — the second value.
  The `> 0` assertion would also catch `top == -108.0`.

#### `test_measured_margins_under_a_crop_and_a_quarter_turn`

- **Setup:** the same page and settings, but `crop_odd_pt=(72.0, 36.0, 24.0,
  12.0)` — four different insets, so a swapped pair cannot pass by symmetry
  — and a landscape source (792 × 612) so `_place_page` takes its
  `rotate_deg = 90` branch under the default `landscape_policy="rotate"`.
- **Assertion in words:** `inner + outer + scaled_width == cell width` and
  `top + bottom + scaled_height == cell height`, where the scaled dimensions
  are recomputed in the test from `(792 - 72 - 24, 612 - 36 - 12)` swapped
  for the 90° rotation and multiplied by `placement.scale_x`. That is the
  identity a correct measurement satisfies and a wrong one does not, and it
  is stated in the test rather than as four constants so that it also pins
  the crop-before-swap ordering.
- **Expected failure on the unfixed tree:** the two sums come out short by
  `(crop_left + crop_right) * scale` and `(crop_bottom + crop_top) * scale`
  respectively, i.e. `assert 516.0 == approx(612.0 ± ...)`-shaped.

### 1b

**File:** `tests/test_render.py` (existing; already holds the thumbnail
tests and `_make_pdf`-style helpers).

#### `test_a_rotated_blank_thumbnail_turns_with_the_page`

- **Setup:** `pages = [make_blank_page((612.0, 792.0))]` from
  `deckle.app.state`— or, to keep `deckle.core.render`'s test free of the
  app layer, a hand-built `SourcePage(ref=SourceRef(path="", page_index=-1,
  sha256="", width_pt=612.0, height_pt=792.0), rotate_deg=90,
  skipped=False)`; `path=""` is `BLANK_SOURCE_PATH`
  (`deckle/core/models.py:62`) and is what `is_blank_page` tests. Call
  `render.thumbnails(pages, start=0, count=1, dpi=36)`.
- **Assertion in words:** the single returned page is wider than it is tall,
  because a portrait blank rotated a quarter turn is landscape — and, for a
  0° blank built the same way, taller than wide.
- **Expected failure on the unfixed tree:**
  `assert 306 > 396` — the blank comes back 306 × 396 (portrait) in both cases.
- **Notes:** must not need a display; `thumbnails` opens no document for a
  blank, so this test touches neither pdfium nor Qt.

#### `test_a_blank_and_a_real_page_rotate_the_same_way`

- **Setup:** a one-page PDF written to `tmp_path` at 612 × 792
  (`tests/test_render.py`'s existing source-writing helper), and a blank of
  the same size; both with `rotate_deg=90`; one call to
  `render.thumbnails([real, blank], start=0, count=2, dpi=36)`.
- **Assertion in words:** both results are landscape, i.e.
  `result[0].width > result[0].height` and the same for `result[1]` — the
  grid must not show two pages of the same shape and rotation as different
  shapes.
- **Expected failure on the unfixed tree:** the real page is landscape and
  the blank is portrait; `assert 306 > 396` on the second.

### 1c

**File:** `tests/test_render.py`, beside the existing
`test_ink_bbox_caches_per_source_ref` (`:164`), whose
`_rasterize_for_bbox`-counting monkeypatch this reuses verbatim.

#### `test_ink_bbox_measures_again_at_a_different_dpi`

- **Setup:** a one-page PDF with a small filled rectangle in its content
  stream (`pdf.make_stream(b"0 0 0 rg 100 100 50 30 re f\n")`, as
  `tests/test_render.py:192-203` already does); `clear_ink_bbox_cache()` via
  the file's existing autouse fixture (`:65-67`); patch
  `render._rasterize_for_bbox` with a spy that appends its `dpi` argument to
  a list and delegates. Call `ink_bbox(ref, dpi=36)`, then
  `ink_bbox(ref, dpi=144)`, then `ink_bbox(ref, dpi=36)` again.
- **Assertion in words:** the spy recorded exactly `[36, 144]` — the second
  resolution was measured rather than served from the first one's entry, and
  the third call was a hit on the 36-dpi entry rather than a third
  rasterisation.
- **Expected failure on the unfixed tree:** `assert [36] == [36, 144]`.

## 5. Acceptance

| Check | Command |
|---|---|
| The crop measurement tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_crop.py -k "measured_margins"` |
| The blank-thumbnail tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_render.py -k "rotated_blank or real_page_rotate"` |
| The dpi-key test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_render.py -k "different_dpi"` |
| Every module that measures a footprint now subtracts the crop | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_layout.py tests/test_layout_saddle.py tests/test_cell_geometry.py tests/test_spine_side_precedence.py tests/test_side_reporting.py tests/test_crop.py tests/test_crop_rotated_source.py` |
| The ink cache is still LRU-bounded | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_limits.py -k ink_bbox` |
| Renders are still serialised | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_render_concurrency.py` |
| `_blank_thumbnail` cannot be called without a rotation | `.venv/bin/python -c "import inspect; from deckle.core import render; p=inspect.signature(render._blank_thumbnail).parameters['rotate_deg']; assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty; print('OK')"` |
| The ink cache key carries the dpi | `.venv/bin/python -c "from deckle.core import render; from deckle.core.models import SourceRef; render.clear_ink_bbox_cache(); import deckle.core.render as r; r._ink_bbox_cache[(SourceRef('p',0,'a'*64,1.0,1.0), 36)]=(0.0,0.0,0.0,0.0); print('OK')"` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two R0.3/R0.4 failures from `00-environment.md` and nothing else) |

`pytest -k` with no match exits 5, so each `-k` row fails loudly if the test
was never written. The `-k` patterns above were checked against the current
tree: `-k "measured_margins"`, `-k "rotated_blank or real_page_rotate"` and
`-k "different_dpi"` currently match nothing and exit 5, and `-k ink_bbox`
currently matches the two `test_hardening_limits.py` cache tests.

## 6. Out of scope

- **B1 / B2 / M2** — the user's `SourcePage.rotate_deg` never reaching
  `Placement.rotate_deg`. `actual_margins_pt` reconstructs the footprint from
  `p.rotate_deg`, the *imposer's* rotation; the user's rotation is not
  recoverable from an `OutputPage` at all, because only `source_ref` is
  carried. Once B1 threads `slot.rotate_deg` into `Placement`, this
  function's swap becomes correct for that case too, with no further change
  here. Do not attempt to compensate for it in this spec — a correction
  layered on top of B1's defect will be wrong once B1 lands.
- **B5** — `document_scale` judging landscape against the paper while
  `_place_page` judges against the cell.
- **B20** — `render_sheet` caching an empty PDF for a stale sheet index. It
  is in `render.py` and it is a different cache.
- **B35** — the thumbnail grid retaining every raw RGBA buffer. This spec
  changes the *shape* of one blank buffer, not how many are held.
- Do not add a `settings` parameter to `actual_margins_pt`, and do not make
  any production code call it. Its docstring's statement that the test suite
  is its only caller stays true.

## 7. decisions.md entry

```
## 2026-09-05 — Three measurements answered for a page that was not on the sheet
- Symptom: `actual_margins_pt` measured the *uncropped* page scaled as though it were cropped, so a Letter job with a 1in crop and a 72pt gutter reported its fore-edge overflowing by 119pt and its head by 108pt — on a job where nothing overflows. `_blank_thumbnail` ignored `rotate_deg`, so an inserted blank rotated in Arrange stayed portrait in a row of landscape pages. And `ink_bbox` keyed its cache on `SourceRef` alone, so re-measuring a page at a higher dpi returned the low-dpi answer without rasterising — exactly the move someone makes after a crop clipped a descender.
- Fix: One rule each, all three taken from code that was already right. `actual_margins_pt` subtracts `OutputPage.crop_pt` before the rotation swap and before scaling, which is the order `_source_dims` and `export._cropped_source_box` both use — insets are named in the file's own frame, so they come off first. `_blank_thumbnail` takes a keyword-only `rotate_deg` with no default and swaps its dimensions on a quarter turn. `ink_bbox`'s key is `(ref, dpi)`.
- Surfaces: `OutputPage.crop_pt`'s docstring had already written down this exact failure — "a consumer that ignores this field draws the whole source page scaled as though it were cropped" — and the consumer that ignored it was three modules away in the same package. The crop is carried on the `OutputPage` precisely so nobody re-derives it; `actual_margins_pt` did not re-derive it, it simply did not read it, which no amount of documenting the field would have caught.
- Watch: `actual_margins_pt`'s only caller is the test suite, and no test passed a cropped page — so the wrong number was available to be asserted against and had not been yet. **A measurement helper with no production caller is not low-risk; it is a wrong answer waiting to be pinned by the first test that needs it.** The two new tests state an identity (margins plus footprint equals the cell) rather than four constants, so they also pin the crop-before-swap ordering.
- Commit: (this commit)
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
- **Crop insets are `(left, bottom, right, top)`**, not the PDF-rectangle
  order and not CSS order. Documented on `LayoutSettings.crop_odd_pt`
  (`deckle/core/models.py:431-457`) and produced by `cli._parse_crop`
  (`deckle/cli.py:260-284`). Subtracting `crop[1]` from the width is the
  mistake this ordering invites.
- **Crop before the rotation swap, never after.** `_source_dims`
  (`deckle/core/layout.py:191-194`) says why in a comment: the insets are
  named in the source page's own orientation. Reversing the two produces a
  measurement that is right for square pages and wrong for every other one,
  which is a very quiet kind of wrong.
- **`p.rotate_deg` is the imposer's rotation, not the user's.** See B1 in Out
  of scope. Do not reach for `SourcePage.rotate_deg` here — an `OutputPage`
  does not carry the `SourcePage`.
- **`_ink_bbox_cache` is read directly by two tests** in
  `tests/test_hardening_limits.py` (`:556-559`, `:573-574`). They pin the key
  type and go red on step 7; update them in the same commit (step 9). They
  are the LRU-bound guard and must not be deleted to make the change green.
- **`_INK_BBOX_CACHE_MAX` now bounds `(ref, dpi)` pairs, not pages.** A
  session that measures the same document at two resolutions fills the cache
  twice as fast. That is correct — each entry is a distinct answer — but it
  is a real change to what the 4096 ceiling means, and the constant's
  docstring (`deckle/core/render.py:43-45`) should not be left saying "one
  entry per page".
- **`tests/test_render_concurrency.py:180-227` scans the source for
  `pdfium.PdfDocument(` calls not lexically under a guard.** None of these
  edits adds or moves such a call, but do not restructure `thumbnails`'
  blank branch in a way that moves the `with _PDFIUM_LOCK:` line: the scan
  walks upward for a shallower-indented `pdfium_guard()` / `_PDFIUM_LOCK`
  line and stops at the first `def`.
- **`tests/test_render.py` has an autouse fixture that clears the ink cache**
  (`:65-67`). `tests/test_hardening_limits.py` and
  `tests/test_render_concurrency.py` have their own. A new test file
  measuring `ink_bbox` without one inherits whatever the previous test left
  behind.
