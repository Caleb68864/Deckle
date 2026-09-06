# B1 — Carry the user's per-page rotation into `Placement` and onto paper

**Roadmap item:** `docs/ROADMAP.md` B1
**Depends on:** B2 (the exporter drops `rotate_deg == 180`; B1 makes 180
reachable for the first time, so B2 must land first or in the same commit)
**Blocks:** —
**Size:** S (the layout edit) + S (the two direction fixes found while
verifying it). Together: M.
**Decision needed first:** none. Two conventions were unstated and are
chosen in §3.1 (rotation is **clockwise**, everywhere) and §3.2
(composition is `(user + policy) % 360`).

---

## 1. Context

A person imports a scan, sees one page lying on its side, selects it in
Arrange and presses **Rotate**. `SourcePage.rotate_deg` becomes 90. The
imposer reads that value only to swap the page's width and height for
sizing, then emits `Placement(rotate_deg=0)`. Nothing downstream ever
learns the page was turned.

What comes out of the printer, by policy:

| `landscape_policy` | source `rotate_deg` | printed result |
|---|---|---|
| `scale` / `letterbox` | 90 or 270 | **upright and shrunk to 0.77** — the page is sized as though turned and drawn as though not |
| `rotate` | 90 or 270 | **sideways and clipped** — the plan's footprint is 612×792, the exporter's is 792×612, on a 612pt-wide sheet |
| any | 180 | **a complete no-op** |

Verified on the unfixed tree, from the repo root:

```bash
.venv/bin/python - <<'PYEOF'
from deckle.core.loader import load_pdf
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings
from dataclasses import replace
pages = list(load_pdf("tests/fixtures/sample.pdf"))
for rot in (90, 180, 270):
    for policy in ("scale", "letterbox", "rotate"):
        ps = [replace(pages[0], rotate_deg=rot), pages[1]]
        s = LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                           binding_edge="left", landscape_policy=policy)
        p = GutterShiftStrategy().impose(ps, s).sheets[0].front.pages[0].placement
        print(rot, policy, p.rotate_deg, round(p.scale_x, 3),
              round(p.tx, 2), round(p.ty, 2))
PYEOF
```

Output:

```
90 scale 0 0.773 0.0 159.55
90 letterbox 0 0.773 0.0 159.55
90 rotate 90 1.0 0.0 0.0
180 scale 0 1.0 0.0 0.0
180 letterbox 0 1.0 0.0 0.0
180 rotate 0 1.0 0.0 0.0
270 scale 0 0.773 0.0 159.55
270 letterbox 0 0.773 0.0 159.55
270 rotate 90 1.0 0.0 0.0
```

Every `rotate_deg` in that first column should have been the user's
rotation composed with the policy's. None of them is.

**The same thing measured on paper.** A 400×600 source with a 100×100
black square in its bottom-left corner, one page rotated by the user, the
sheet exported and rasterised through `deckle.core.render`:

```bash
.venv/bin/python - <<'PYEOF'
import os, tempfile, pikepdf
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core import render, export

d = tempfile.mkdtemp()
src = os.path.join(d, "corner.pdf")
pdf = pikepdf.Pdf.new()
for _ in range(2):
    p = pdf.add_blank_page(page_size=(400.0, 600.0))
    p.contents_add(b"q\n0 0 0 rg\n0 0 100 100 re\nf\nQ\n")
pdf.save(src); pdf.close()

def ink(rp):
    xs = []; ys = []
    for i in range(0, len(rp.rgba), 4):
        if rp.rgba[i] < 250:
            q = i // 4; xs.append(q % rp.width); ys.append(q // rp.width)
    return (min(xs), min(ys), max(xs), max(ys))

for rot in (0, 90, 180, 270):
    pages = [SourcePage(ref=SourceRef(path=src, page_index=i, sha256="a" * 64,
                                      width_pt=400.0, height_pt=600.0),
                        rotate_deg=(rot if i == 0 else 0), skipped=False)
             for i in range(2)]
    s = LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                       binding_edge="left", landscape_policy="scale")
    plan = GutterShiftStrategy().impose(pages, s)
    print(rot, ink(render.render_sheet(plan, 0, "front", 36)))
    export.clear_sheet_cache()
PYEOF
```

Output — the sheet is 306×396 px at 36 dpi:

```
0 (42, 330, 107, 395)
90 (0, 249, 50, 299)
180 (42, 330, 107, 395)
270 (0, 249, 50, 299)
```

Rotating 180 puts the ink in **exactly the same pixels** as not rotating
at all. Rotating 90 leaves it at the bottom-left of its footprint, where
a clockwise quarter turn would have put it at the top-left.

### Two further defects found while verifying this

**(a) The thumbnail does not "honour the rotation" — it raises.** The
roadmap says "Thumbnails honour the rotation, so the user sees one thing
and prints another". On the pinned environment they do not:

```bash
.venv/bin/python - <<'PYEOF'
import os, tempfile, pikepdf
from deckle.core.models import SourcePage, SourceRef
from deckle.core import render
d = tempfile.mkdtemp(); src = os.path.join(d, "c.pdf")
pdf = pikepdf.Pdf.new(); pdf.add_blank_page(page_size=(400.0, 600.0))
pdf.save(src); pdf.close()
sp = [SourcePage(ref=SourceRef(path=src, page_index=0, sha256="a" * 64,
                               width_pt=400.0, height_pt=600.0),
                 rotate_deg=90, skipped=False)]
render.thumbnails(sp, 0, 1, dpi=36)
PYEOF
```

```
  File ".../pypdfium2/_helpers/page.py", line 477, in render
    pos_args = (..., pdfium_i.RotationToConst[rotation])
KeyError: 1
```

`render._rotation_quarter_turns` divides by 90 before handing the value
to pypdfium2, whose `render(rotation=)` takes **degrees**:

```bash
.venv/bin/python -c "import pypdfium2.internal as i; print(i.RotationToConst)"
# {0: 0, 90: 1, 180: 2, 270: 3}
```

So every non-zero user rotation crashes the Arrange grid's render
window. No test passes a rotated page to `thumbnails` (see §2).

**(b) Deckle turns two ways at once.** pdfium's `rotation` and PDF's
`/Rotate` are both **clockwise**; `export._rotation_matrix` builds a
**counter-clockwise** matrix. Confirmed:

```bash
.venv/bin/python -c "
from deckle.core.export import _rotation_matrix
print(_rotation_matrix(90, 100.0, 200.0))"
# 0.0 1.0 -1.0 0.0 300.0 100.0 cm      -- maps (1,0) to (0,1): counter-clockwise
```

Today that only reaches paper through `landscape_policy="rotate"`, whose
direction nothing asserts. The moment the user's own rotation flows
through the same field, the two must agree or a page turned right in
Arrange comes out turned left.

**Why it matters to a person printing a book.** A scanned foldout, a
sideways plate, a cover imported upside down: the one correction Arrange
offers for any of them silently does nothing, or does something worse
than nothing. The preview renders the real exported artefact
(`render.render_sheet`), so the preview is *also* wrong in the same way —
there is no surface anywhere in Deckle that shows the truth.

## 2. Current code

### `deckle/core/layout.py:182-201` — `_source_dims`

```python
def _source_dims(
    page: SourcePage, settings: LayoutSettings | None = None
) -> tuple[float, float]:
    """The upright (unrotated-by-us) width/height of a source page's content.

    With ``settings``, this is the size *after* cropping -- the single
    place the cropped size is derived, so ``document_scale`` and
    ``_place_page`` cannot disagree about how big a page is.

    The crop is applied before the rotation swap, because the insets are
    named in the source page's own orientation: "left" is the left edge of
    the page as it exists in the file, not of the cell Deckle puts it in.
    """
    width = page.ref.width_pt
    height = page.ref.height_pt
    if settings is not None:
        width, height = _cropped_dims(width, height, crop_for(page, settings))
    if page.rotate_deg in (90, 270):
        width, height = height, width
    return width, height
```

This is the **only** consumer of `SourcePage.rotate_deg` in the whole of
`deckle/core/layout.py` (`grep -n "rotate_deg" deckle/core/layout.py`
returns lines 199, 372, 376, 386, 495, 611 — 199 is this one, 611 is
`actual_margins_pt` reading `Placement.rotate_deg`, and the rest are the
imposer's own value).

Note the literal membership test `in (90, 270)`: `-90` and `450` fall
through as though the page were upright. `app.state.set_rotation`
normalises with `% 360`, but a `.deckle` file is checked only for `int`
(`deckle/core/schema.py:105-107`), so a stored `-90` reaches here intact.

### `deckle/core/layout.py:375-397` — `_place_page`, the rotation decision

```python
    src_w, src_h = _source_dims(slot, settings)
    rotate_deg = 0

    # Landscape content inside a portrait cell (or vice versa) under the
    # "rotate" policy: rotate the content to match the cell's orientation
    # and warn, rather than silently clipping or shrinking it. Under folio
    # each cell is portrait-shaped even though the sheet itself is
    # landscape, so this must be judged against the cell, not the sheet.
    cell_is_portrait = cell_h >= cell_w
    page_is_landscape = src_w > src_h
    if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
        rotate_deg = 90
        src_w, src_h = src_h, src_w
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="mixed_orientation",
                detail=(
                    f"page {slot.ref.page_index} of {slot.ref.path!r} is "
                    "landscape inside a portrait document; rotated 90deg"
                ),
            )
        )
```

`rotate_deg` starts at `0`, not at the user's value. Line 376 is the
whole defect.

### `deckle/core/layout.py:490-496` — what is emitted

```python
    placement = Placement(
        scale_x=scale,
        scale_y=scale,
        tx=tx,
        ty=ty,
        rotate_deg=rotate_deg,
    )
```

### `deckle/core/models.py:29-31` — the field's contract, direction unstated

```python
    :ivar rotate_deg: rotation applied about the placement's own footprint
        centre. ``0``, ``90``, ``180`` or ``270``; ``tx``/``ty`` already
        describe the *post*-rotation footprint.
```

### `deckle/core/models.py:96-98` — the source field's contract

```python
    :ivar rotate_deg: the user's own rotation for this page, applied on top
        of whatever the source file says. Distinct from
        ``Placement.rotate_deg``, which is the imposer's decision.
```

### `deckle/core/export.py:341-381` — the exporter's rotation branch

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

`src_w, src_h` here come from `_cropped_source_box`
(`deckle/core/export.py:223-281`), which returns the page's **displayed,
cropped** size — that is, the size *before* any user rotation. That is
the correct natural size for the form; the problem is only that
`placement.rotate_deg` never described the user's turn.

### `deckle/core/export.py:186-193` — the matrix, counter-clockwise

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

### `deckle/core/render.py:343-347, 377-379` — the thumbnail's rotation

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

```python
def _rotation_quarter_turns(rotate_deg: int) -> int:
    """pdfium's ``rotation`` is quarter turns clockwise, not degrees."""
    return (rotate_deg // 90) % 4
```

`deckle/core/render.py:138` repeats the claim: `:param rotation: quarter
turns clockwise.`

### Every call site that reads either field

`grep -rn "\.rotate_deg" --include='*.py' deckle/`:

| Site | Reads | Note |
|---|---|---|
| `deckle/core/layout.py:199` | `SourcePage.rotate_deg` | `_source_dims`; **changed by this spec** |
| `deckle/core/layout.py:611` | `Placement.rotate_deg` | `actual_margins_pt`; becomes correct for free |
| `deckle/core/render.py:345` | `SourcePage.rotate_deg` | `thumbnails`; **changed by this spec** |
| `deckle/core/export.py:142` | `Placement.rotate_deg` | `_output_page_key` — the cache key already includes it, so no cache change is needed |
| `deckle/core/export.py:342` | `Placement.rotate_deg` | `_place_output_page`; **changed by B2, read by this spec** |
| `deckle/core/project_io.py:196` | `SourcePage.rotate_deg` | persisted verbatim; unchanged |
| `deckle/app/views/arrange_view.py:660` | `SourcePage.rotate_deg` | `current + 90` on the Rotate button; unchanged |
| `deckle/app/views/preview_view.py:82` | `Placement.rotate_deg` | `_output_page_bbox` already swaps for 90/270 and becomes correct for free |
| `deckle/app/state.py:141` | writes `rotate_deg % 360` | `set_rotation`; unchanged |

### Existing tests that touch this

- `tests/test_cell_geometry.py:142-177` — `GUTTER_SHIFT_PLACEMENT_PIN`,
  the `"WIDE"` rows, pin `rotate_deg == 90` for a 900×600 source on
  portrait letter under the `rotate` policy. Every page in the pin has
  `rotate_deg=0`, so **the pin must not move.**
- `tests/test_layout.py:424-439` — `test_landscape_page_is_rotated_and_warned`,
  `test_rotated_landscape_still_honours_margins`. Both use pages with
  `rotate_deg=0`.
- `tests/test_spec_residue.py:288-315` — `test_folio_never_rotates_a_leaf`
  asserts `placement.rotate_deg == 0` for every folio leaf. Its
  `_source_pages` helper builds pages with `rotate_deg=0`, so it stays
  green; see §8.
- `tests/test_imposition_properties_settings.py:117-120, 269-274` —
  swap `source_w`/`source_h` when `placement.rotate_deg in (90, 270)`.
  All generated pages have `rotate_deg=0`.
- `tests/test_golden_pinebox.py:85-89` — swaps for
  `source_page.rotate_deg in (90, 270)`. Skips without the fixture.

**No test in the repository imposes or exports a page with
`SourcePage.rotate_deg != 0`.** `grep -rn "rotate_deg=90\|rotate_deg=180\|rotate_deg=270" tests/`
returns only `tests/test_models.py:55` (constructs one) and
`tests/test_project_io.py:39,49` (round-trips the field). **No test
passes a rotated page to `render.thumbnails`** either —
`grep -rn "thumbnails(" tests/` returns nine call sites, none with a
rotated page.

## 3. Change

### 3.1 The convention, stated once

**`SourcePage.rotate_deg` and `Placement.rotate_deg` are both degrees
clockwise.** Chosen because PDF `/Rotate` ("the page shall be rotated
clockwise when displayed"), pypdfium2's `render(rotation=)` and the
Arrange button's `current + 90` are all clockwise already; the only
counter-clockwise thing in Deckle is `_rotation_matrix`, and it is the
one with no test and no user-visible history. Rejected: keeping the
matrix counter-clockwise and negating at the call site — that leaves two
conventions in the codebase and a conversion somebody will delete.

Confirmed clockwise for pdfium (a 100×100 square in a 400×600 page's
bottom-left corner, `rotation=90`, 36 dpi → ink at image pixels
`(0, 0)-(49, 49)`, i.e. the top-left):

```bash
.venv/bin/python - <<'PYEOF'
import os, tempfile, pikepdf, pypdfium2 as pdfium
d = tempfile.mkdtemp(); src = os.path.join(d, "c.pdf")
pdf = pikepdf.Pdf.new()
p = pdf.add_blank_page(page_size=(400.0, 600.0))
p.contents_add(b"q\n0 0 0 rg\n0 0 100 100 re\nf\nQ\n")
pdf.save(src); pdf.close()
doc = pdfium.PdfDocument(src)
for rot in (0, 90, 180, 270):
    img = doc[0].render(scale=0.5, rotation=rot).to_pil().convert("L")
    px = img.load(); xs = []; ys = []
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            if px[x, y] < 250: xs.append(x); ys.append(y)
    print(rot, img.size, (min(xs), min(ys), max(xs), max(ys)))
doc.close()
PYEOF
# 0   (200, 300) (0, 250, 49, 299)
# 90  (300, 200) (0, 0, 49, 49)
# 180 (200, 300) (150, 0, 199, 49)
# 270 (300, 200) (250, 150, 299, 199)
```

### 3.2 The composition rule, stated once

```
placement.rotate_deg = (normalised user rotation + policy rotation) % 360
```

where the policy rotation is `90` when
`landscape_policy == "rotate"` and the page — **already turned by the
user** — is landscape inside a portrait cell, and `0` otherwise.

Consequences, all intended:

| user | policy fires | total | meaning |
|---|---|---|---|
| 0 | yes | 90 | today's landscape-policy behaviour, unchanged |
| 90 | no | 90 | the user's turn, honoured |
| 90 | yes | 180 | a portrait page turned 90 by the user is landscape, so the policy turns it back the *other* way round — net a half turn |
| 180 | no | 180 | previously a total no-op |
| 270 | yes | 0 | a portrait page turned 270 becomes landscape; the policy's 90 CW brings it back upright, which is the right answer |

### 3.3 Dimensions: 90/270 swap, 180 does not

`_source_dims` swaps `width`/`height` when the **normalised user
rotation** is 90 or 270, and leaves them alone for 0 and 180. The policy
branch in `_place_page` swaps again when it fires. Composing the two
swaps is already arithmetically identical to "swap iff the *total*
rotation is 90 or 270", because 90+90 = 180 (two swaps = none) and
0+90 = 90 (one swap). **No dimension arithmetic changes in this spec.**
This is worth stating because it is the part an implementer will assume
is broken and rewrite: it is not.

The exporter's footprint is derived from `src_w, src_h` =
`_cropped_source_box(...)`, the page's **displayed, cropped, un-user-
rotated** size, and the total rotation:

| `placement.rotate_deg` | on-sheet footprint | `prerotate_rect` |
|---|---|---|
| `0` | `(src_w·s, src_h·s)` | the `_rect_for_placement` rect; no rotation block |
| `90` | `(src_h·s, src_w·s)` | `src_w·s × src_h·s`, centred on the footprint centre |
| `180` | `(src_w·s, src_h·s)` | the `_rect_for_placement` rect, then rotated (**B2**) |
| `270` | `(src_h·s, src_w·s)` | as for `90` |

The crop needs no new handling: `_insets_in_stored_space`
(`deckle/core/export.py:196-220`) converts the user's displayed-frame
insets into the source file's stored frame using the file's own
`/Rotate`, and the user's rotation is applied afterwards, as a transform
wrapping the already-cropped form. Do not touch it.

### 3.4 Numbered edits

1. **`deckle/core/layout.py`** — new module-level helper, placed
   immediately above `_source_dims` (i.e. before line 182):

   ```python
   def _page_rotation(page: SourcePage) -> int:
       """The user's rotation for ``page``, normalised to 0/90/180/270.

       Clockwise, matching PDF ``/Rotate`` and pdfium -- see
       ``Placement.rotate_deg``. Normalised here rather than trusted
       because ``app.state.set_rotation`` takes ``% 360`` but a stored
       ``.deckle`` is only checked for *int* (``core.schema``), so ``-90``
       and ``450`` both reach the imposer. The old membership test
       ``in (90, 270)`` treated both as upright.
       """
       return (round(page.rotate_deg / 90.0) * 90) % 360
   ```

   `round(x / 90) * 90` rather than `x % 360`: a stored `45` is not a
   quarter turn and there is no correct answer, so it is snapped to the
   nearest one (`0`) rather than crashing pikepdf's matrix or pdfium's
   lookup table. `round` is banker's rounding, so `45` → `0` and `135` →
   `180`; both are arbitrary and both are safe.

2. **`deckle/core/layout.py:199`**, in `_source_dims`, replace

   ```python
       if page.rotate_deg in (90, 270):
   ```

   with

   ```python
       if _page_rotation(page) in (90, 270):
   ```

3. **`deckle/core/layout.py:376`**, in `_place_page`, replace

   ```python
       rotate_deg = 0
   ```

   with

   ```python
       # The user's own rotation is where this starts, not zero. It was
       # read by `_source_dims` to swap the page's dimensions for sizing
       # and then dropped, so a page rotated in Arrange was measured as
       # turned and drawn as upright.
       rotate_deg = _page_rotation(slot)
   ```

4. **`deckle/core/layout.py:386`**, in the same function, replace

   ```python
           rotate_deg = 90
   ```

   with

   ```python
           rotate_deg = (rotate_deg + 90) % 360
   ```

   Leave the dimension swap on the next line and the warning below it
   exactly as they are.

5. **`deckle/core/models.py:29-31`**, `Placement.rotate_deg`: replace the
   docstring paragraph with

   ```python
       :ivar rotate_deg: rotation applied about the placement's own footprint
           centre, in degrees **clockwise** -- the same direction PDF
           ``/Rotate`` and pdfium's ``rotation`` both mean, so the preview,
           the thumbnail grid and the exported sheet cannot disagree about
           which way a page turned. ``0``, ``90``, ``180`` or ``270``;
           ``tx``/``ty`` already describe the *post*-rotation footprint.

           This is the **total** rotation: the user's own
           ``SourcePage.rotate_deg`` composed with whatever
           ``landscape_policy`` added, as ``(user + policy) % 360``. It
           used to carry the policy's decision alone, so a page the user
           turned in Arrange exported unturned.
   ```

6. **`deckle/core/models.py:96-98`**, `SourcePage.rotate_deg`: replace with

   ```python
       :ivar rotate_deg: the user's own rotation for this page, in degrees
           clockwise, applied on top of whatever the source file says.
           Distinct from ``Placement.rotate_deg``, which is this value
           composed with the imposer's ``landscape_policy`` decision.
   ```

7. **`deckle/core/export.py:186-193`**, `_rotation_matrix`: negate the
   angle and say why.

   ```python
   def _rotation_matrix(rotate_deg: int, cx: float, cy: float) -> str:
       """A ``cm`` matrix rotating ``rotate_deg`` CLOCKWISE about ``(cx, cy)``.

       Clockwise because that is what ``Placement.rotate_deg`` means, which
       in turn is what PDF ``/Rotate`` and pdfium's ``rotation`` both mean.
       A positive angle in the PDF's own coordinate system turns
       counter-clockwise, so the angle is negated here -- once, in the one
       place that builds the matrix, rather than at each call site.
       """
       theta = math.radians(-rotate_deg)
       cos_t = round(math.cos(theta), 10)
       sin_t = round(math.sin(theta), 10)
       e = cx - cx * cos_t + cy * sin_t
       f = cy - cx * sin_t - cy * cos_t
       return f"{cos_t} {sin_t} {-sin_t} {cos_t} {e} {f} cm"
   ```

   For `rotate_deg=90, cx=100, cy=200` this changes the emitted string
   from `0.0 1.0 -1.0 0.0 300.0 100.0 cm` to
   `-0.0 -1.0 1.0 -0.0 -100.0 300.0 cm` (what `270` produced before).
   `180` is unchanged: `-1.0 0.0 -0.0 -1.0 200.0 400.0 cm`.

8. **`deckle/core/render.py:377-379`**: replace `_rotation_quarter_turns`
   with

   ```python
   def _pdfium_rotation(rotate_deg: int) -> int:
       """``rotate_deg`` as pypdfium2's ``rotation`` argument wants it.

       **Degrees, not quarter turns.** ``pypdfium2.internal.RotationToConst``
       is ``{0: 0, 90: 1, 180: 2, 270: 3}`` and the helper it feeds does the
       division itself, so pre-dividing here handed it ``1`` and every
       rotated page raised ``KeyError: 1`` out of the thumbnail worker --
       i.e. the one control Arrange offers for a sideways scan broke the
       grid rather than turning the page.

       Snapped to the nearest quarter turn for the same reason
       ``layout._page_rotation`` is: a stored ``45`` is not a rotation
       pdfium can perform, and a KeyError is not the way to say so.
       """
       return (round(rotate_deg / 90.0) * 90) % 360
   ```

9. **`deckle/core/render.py:345`**: replace
   `rotation=_rotation_quarter_turns(source_page.rotate_deg),`
   with
   `rotation=_pdfium_rotation(source_page.rotate_deg),`

10. **`deckle/core/render.py:138`**: replace
    `:param rotation: quarter turns clockwise.` with
    `:param rotation: degrees clockwise -- 0, 90, 180 or 270, as pypdfium2 wants them.`

11. **`docs/api/`** — nothing to add. No new module; `core.layout.rst`,
    `core.render.rst`, `core.export.rst` and `core.models.rst` all exist.

## 4. Tests

Write all of these before touching `deckle/`, and confirm each fails for
the reason given.

### `tests/test_layout.py`

Extend the existing `# ----- landscape` block. `make_page` already takes
`rotate_deg` (`tests/test_layout.py:41`), so no new helper is needed.

**`test_a_users_rotation_reaches_the_placement`**
Impose `[make_page(0, size=DIGEST, rotate_deg=90), make_page(1, size=DIGEST)]`
under `settings(landscape_policy="scale")`. Assert
`flat_output_pages(plan)[0].placement.rotate_deg == 90`.
Unfixed: `assert 0 == 90`.

**`test_a_half_turn_is_not_silently_dropped`**
The same with `rotate_deg=180`, parametrised over all three policies.
Assert the placement's `rotate_deg == 180` in every case.
Unfixed: `assert 0 == 180`.

**`test_the_users_rotation_composes_with_the_landscape_policy`**
`make_page(0, size=DIGEST, rotate_deg=90)` under
`settings(landscape_policy="rotate")`. DIGEST is 432×648 (portrait); the
user's quarter turn makes it 648×432, which is landscape in a portrait
cell, so the policy adds another 90. Assert
`placement.rotate_deg == 180`, and that a `mixed_orientation` warning was
raised.
Unfixed: `assert 90 == 180`.

**`test_a_three_quarter_turn_under_the_rotate_policy_lands_upright`**
`make_page(0, size=DIGEST, rotate_deg=270)`, `landscape_policy="rotate"`,
`gutter_pt=0.0`. Assert `placement.rotate_deg == 0` — `(270 + 90) % 360` —
and that the placement's `scale_x`/`tx`/`ty` equal those of a second
`impose` over unrotated DIGEST pages: turning a page 270 and then back
90 the other way is the identity, and the *geometry* already agrees
(`1.2222, 84.0, 0.0` both ways on the unfixed tree). Only the rotation
disagrees.
Unfixed: `assert 90 == 0`.

**`test_a_stored_rotation_outside_zero_to_360_is_normalised`**
`make_page(0, size=DIGEST, rotate_deg=-90)` and `rotate_deg=450`, policy
`"scale"`, `gutter_pt=0.0`. Assert `placement.rotate_deg` is `270` and
`90` respectively, and that `placement.scale_x == pytest.approx(0.94444)`
in both cases — the same scale the equivalent in-range `270`/`90` pages
get, i.e. the dimensions were swapped.
Unfixed: both produce `Placement(scale_x=1.2222…, tx=84.0, ty=0.0,
rotate_deg=0)` — the unswapped, upright answer — against the in-range
`Placement(scale_x=0.9444…, tx=0.0, ty=192.0, rotate_deg=0)`. So
`assert 0 == 270` *and* `assert 1.2222 == approx(0.94444)`.

**`test_the_pin_is_unmoved_by_the_rotation_fix`** — not a new test. The
existing `tests/test_cell_geometry.py::test_gutter_shift_placements_are_byte_identical_to_the_pin`
covers it and must stay green untouched.

### `tests/test_layout.py` — measured margins

**`test_measured_margins_use_the_post_rotation_footprint`**
Impose `[make_page(0, size=TRAVELLER, rotate_deg=90), make_page(1, size=TRAVELLER)]`
under `settings(gutter_pt=0.0, margin_outer_pt=0.0, margin_top_pt=0.0, margin_bottom_pt=0.0, landscape_policy="scale")`
and measure page 0 with
`actual_margins_pt(page, LETTER, is_recto=True, binding_edge="left")`.
Assert `top == pytest.approx(bottom)` — vertical slack is *always* split,
which `tests/test_layout.py:553` already states as an invariant — and
that every value is `>= -1e-6`.
`actual_margins_pt` (`deckle/core/layout.py:610-614`) swaps the source
dimensions when `p.rotate_deg in (90, 270)`, so it measures the real
footprint only once the placement carries the rotation.
Measured on the unfixed tree the four margins are
`(0.0, 150.377, 14.811, 165.189)` — `assert 14.811 == approx(165.189)`.
Fixed they are `(0.0, 0.0, 165.189, 165.189)`.

### `tests/test_export.py`

New section at the end, `# --- BEHAVIORAL: the user's rotation reaches the ink ---`.
Two module-level helpers:

```python
def _write_corner_marked_pdf(tmp_path, n_pages: int,
                             page_size=(400.0, 600.0)) -> str:
    """A source whose ink is a black square in each page's BOTTOM-LEFT.

    Asymmetric on both axes on purpose: a blank page, or one marked in the
    middle, looks identical under every rotation, which is exactly how a
    rotation defect survives a test that only counts pixels.
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
```

and a fixture-free plan builder that mirrors the repro in §1 (paper
`LETTER`, `gutter_pt=0.0`, all margins 0, `landscape_policy="scale"`,
two pages, only page 0 rotated). Every test calls
`export.clear_sheet_cache()` in a `finally`, because
`render.render_sheet` routes through `export_sheet_cached` and the plan
hash is the same across parametrised runs only if the placements are
(they are not, but clear it anyway — `tests/test_export.py:107` sets the
precedent).

**`test_an_unrotated_page_puts_its_ink_in_the_bottom_left`**
The control. `rotate_deg=0` → `_ink_bbox_px(render_sheet(plan, 0, "front", 36))`
is `(42, 330, 107, 395)` on a 306×396 sheet. Passes on the unfixed tree;
its job is to prove the harness measures what it claims.

**`test_a_half_turn_moves_the_ink_to_the_opposite_corner`**
`rotate_deg=180` → the ink bbox is `(240, 0, 305, 65)`, the top-right.
Derivation: scale 1.32, footprint 528×792 at `tx=84, ty=0`; the square
occupies sheet points `x 84..216, y 0..132`; a half turn about the
footprint centre `(348, 396)` maps it to `x 480..612, y 660..792`; at
36 dpi that is `x 240..306, y 0..66`.
Unfixed: the bbox is `(42, 330, 107, 395)` — byte-identical to the
control, which is the defect stated as an assertion.

**`test_a_quarter_turn_puts_the_ink_where_a_clockwise_turn_puts_it`**
`rotate_deg=90` → the bbox is `(0, 96, 51, 147)`.
Derivation: turned dims 600×400, scale `min(612/600, 792/400) = 1.02`,
footprint 612×408 at `tx=0, ty=192`; the square lands at the footprint's
**top-left**, sheet points `x 0..102, y 498..600`; at 36 dpi that is
`x 0..51, y 96..147`.
Unfixed: `(0, 249, 50, 299)` — the bottom-left. `assert (0, 249, 50, 299) == (0, 96, 51, 147)`.

**`test_the_sheet_and_the_thumbnail_turn_the_same_way`**
For `rotate_deg` in `(0, 90, 180, 270)`, render the *source* page through
`render.thumbnails([page], 0, 1, dpi=36)` and the *sheet* through
`render.render_sheet(plan, 0, "front", 36)`, reduce each ink bbox to
which quadrant of its own image it sits in (compare each edge against the
image's mid-line), and assert the two quadrants agree.
Unfixed: `render.thumbnails` raises `KeyError: 1` for 90, `2` for 180 and
`3` for 270 — the test errors rather than failing, which is the correct
signal for defect (a).

### `tests/test_render.py`

**`test_a_rotated_page_still_renders_a_thumbnail`**
Parametrised over `(0, 90, 180, 270)`: build a one-page PDF with
`tests/test_loader.py::_make_pdf` (or `pikepdf` inline as
`tests/test_render.py` already does), a `SourcePage` with that rotation,
and assert `render.thumbnails(...)[0].width > 0`. Also assert the
rendered aspect flips for 90/270: `width > height` for a portrait source.
Unfixed: `KeyError: 1` from `pypdfium2/_helpers/page.py`.

**`test_a_rotation_that_is_not_a_quarter_turn_does_not_raise`**
`rotate_deg=45` → a thumbnail is produced (snapped to 0).
Unfixed: `KeyError: 0` — `45 // 90 == 0`, so this one happens to pass
today; keep it as the guard on the `round` in `_pdfium_rotation`, whose
naive `% 360` alternative would raise.

### `tests/test_export.py` — the matrix direction

**`test_the_rotation_matrix_turns_clockwise`**
Direct unit test of `export._rotation_matrix`: assert
`_rotation_matrix(90, 0.0, 0.0)` maps the point `(1, 0)` to `(0, -1)` —
parse the six numbers out of the returned string and apply them.
Unfixed: it maps to `(0, 1)`; `assert (0.0, 1.0) == (0.0, -1.0)`.

## 5. Acceptance

| Check | Command |
|---|---|
| the imposer no longer starts from zero | `! grep -n "^    rotate_deg = 0$" deckle/core/layout.py` (matches `deckle/core/layout.py:376` today) |
| the quarter-turn helper is gone | `! grep -rn "_rotation_quarter_turns" deckle/ tests/` (matches `deckle/core/render.py:345,377` today) |
| the "quarter turns" claim is gone from render.py | `! grep -n "quarter turns" deckle/core/render.py` (matches lines 138 and 378 today) |
| the matrix negates once, in one place | `grep -n "math.radians(-rotate_deg)" deckle/core/export.py` |
| the repro from §1 now composes | paste §1's first fenced block and check the first two columns of every line: `90 scale 90`, `90 letterbox 90`, `90 rotate 180`, `180 * 180`, `270 scale 270`, `270 letterbox 270`, `270 rotate 0` |
| the new layout tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout.py -q --no-header -p no:cacheprovider -k "rotation or half_turn or quarter"` |
| the new export tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_export.py -q --no-header -p no:cacheprovider -k "ink or clockwise or thumbnail"` |
| the new render tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_render.py -q --no-header -p no:cacheprovider -k "rotated or quarter_turn"` |
| **the placement pin has not moved** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py -q --no-header -p no:cacheprovider` |
| folio still places by pure translation | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_spec_residue.py -q --no-header -p no:cacheprovider -k never_rotates` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (1507 passed + the new tests, 22 skipped, the same 2 R0.3/R0.4 failures) |

## 6. Out of scope

- **B2** — the exporter's `rotate_deg == 180` branch. It is a dependency,
  not part of this spec: B1 makes 180 reachable and B2 makes it draw.
  Land B2 first or in the same commit; a B1 without B2 turns the
  180 case from "silently ignored" into "silently ignored", i.e. it fixes
  nothing there.
- **B5** — `document_scale` judging rotation against the paper while
  `_place_page` judges it against the cell. Both specs edit `_place_page`;
  see §8.
- **B19** — whether `scale` and `letterbox` should differ at all. This
  spec treats them as the single non-rotating branch they currently are
  and must not change that.
- **B32** — `actual_margins_pt` ignoring `crop_pt`, and
  `_blank_thumbnail` ignoring `rotate_deg`. The second is adjacent (an
  inserted blank still renders unrotated after this spec) and is
  deliberately left: a blank is white on every side, so its rotation is
  unobservable.
- The `mixed_orientation` warning's wording. After this spec it can fire
  for a page that is landscape *only because the user turned it*, and its
  text still says "is landscape inside a portrait document". That is
  arguably imprecise and it is not worth a message change here; if it is
  changed, `tests/test_layout.py:428` greps only the `kind`.
- **Multi-select rotate in Arrange** (N10) and the per-page landscape
  override the MVP design mentions. Neither exists; neither is added.

## 7. decisions.md entry

```
## 2026-09-05 — The Rotate button did nothing, and Deckle turned two ways at once
- Symptom: A page rotated in Arrange exported unrotated. `_place_page` read `slot.rotate_deg` only to swap the page's width and height for sizing, then emitted `Placement(rotate_deg=0)`. Measured on a 2-page letter fixture: 90 degrees under the default policies exported upright at scale 0.773 (sized as turned, drawn as not); 90 under `rotate` exported sideways and clipped, because the plan's footprint was 612x792 and the exporter's was 792x612 on a 612pt sheet; 180 was a complete no-op. An exported sheet with the page rotated 180 was byte-identical in ink position to one with no rotation at all.
- Fix: `placement.rotate_deg` is now `(user rotation + policy rotation) % 360`, normalised through a new `layout._page_rotation` that snaps to the nearest quarter turn -- `in (90, 270)` treated a stored `-90` as upright, and `.deckle` only type-checks the field. The dimension arithmetic did not change: `_source_dims` swaps for a 90/270 *user* rotation and the policy branch swaps again when it fires, which already composes to "swap iff the total is 90 or 270".
- Surfaces: Two more defects fell out of verifying it. `render._rotation_quarter_turns` pre-divided by 90 before handing the value to pypdfium2, whose `render(rotation=)` takes degrees (`RotationToConst == {0: 0, 90: 1, 180: 2, 270: 3}`) -- so **every** non-zero rotation raised `KeyError: 1` out of the thumbnail grid, and the roadmap's claim that "thumbnails honour the rotation" was wrong in the other direction. And `export._rotation_matrix` built a counter-clockwise matrix while PDF `/Rotate`, pdfium and the Rotate button are all clockwise. Both are now clockwise, stated on `Placement.rotate_deg`.
- Watch: The field existed, was persisted, was read, and was thrown away one line later -- and no test in 1507 ever imposed or exported a page with a non-zero rotation, or passed one to `thumbnails`. A value that is *read* is not a value that is *used*. The new export tests assert where the ink lands in the rasterised sheet, not what the placement numbers say, because the placement numbers were self-consistent the whole time.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for anything headless.
- **`tests/test_cell_geometry.py::GUTTER_SHIFT_PLACEMENT_PIN` is a
  regeneration-is-an-escalation pin.** Its `"WIDE"` rows contain
  `rotate_deg == 90` and its own docstring says "If it goes red, STOP and
  surface; do not update the numbers." Every page in it has
  `rotate_deg=0`, so a correct implementation of this spec leaves it
  green. If it goes red you have changed the *policy* branch, not the
  *user* branch — check step 4, which must be `(rotate_deg + 90) % 360`
  and not `= 90`.
- **`tests/test_spec_residue.py::test_folio_never_rotates_a_leaf` is
  narrower than it looks.** It asserts folio leaves carry
  `rotate_deg == 0`, and it stays green only because its own fixture
  pages are unrotated. After this spec a folio leaf whose *source page*
  the user rotated will carry a non-zero rotation, and that is correct:
  SS-08's "folio never rotates" is a claim about the folio *decision*
  (both placement matrices are pure translations), not about content the
  user turned. Do not widen that test to cover user rotation, and do not
  suppress the composition to keep it narrow.
- **`_source_dims` is called twice per page** — once from
  `document_scale` via `_fitted_dims` (`deckle/core/layout.py:299`) and
  once from `_place_page` (`:375`). Both must see the same normalised
  rotation or the scale and the placement disagree. That is why the
  normalisation lives in a helper rather than being inlined at line 199.
- **`_creep_advisory` owns `paper_thickness_pt` exclusively**, enforced by
  an AST walk in
  `tests/test_layout_saddle.py::test_creep_references_are_isolated_to_creep_advisory`.
  It walks *all* `ast.Name`/`ast.Attribute` nodes named
  `paper_thickness_pt`; nothing in this spec touches it, but do not add a
  reference to that field while you are in the file.
- **`deckle/core` must not import Qt** —
  `tests/test_core_purity.py`. Everything here is in `core`.
- **`_rotation_matrix` is also used by nothing else.** `grep -n
  "_rotation_matrix" deckle/` returns only its definition
  (`export.py:186`) and one call (`export.py:368`). Negating inside it is
  therefore safe; do not also negate at the call site.
- **The export cache key already includes `rotate_deg`**
  (`deckle/core/export.py:141-143`), so a plan whose rotations changed
  hashes differently and no cache invalidation work is needed. It does
  *not* include the `_rotation_matrix` implementation, so a stale
  `/tmp/deckle_export_cache` entry from before the direction change will
  be handed back with the old matrix. Run
  `rm -rf /tmp/deckle_export_cache` once after implementing, or every
  ink-position test you run by hand will show you the pre-fix answer.
- **Rounding is banker's.** `round(45 / 90.0) * 90` is `0` and
  `round(135 / 90.0) * 90` is `180` — `round(0.5)` is `0` and
  `round(1.5)` is `2` in Python. Both are acceptable; do not "fix" them
  into `math.floor(x + 0.5)`, which changes the 45 case for no reason.
