# B6 — The printed sheet is 6% small and the wrong shape

**Roadmap item:** `docs/ROADMAP.md` B6
**Depends on:** —
**Blocks:** F4 (a folded dummy measured off a 6%-short sheet proves nothing about millimetres), F3 (a registration target you read numbers off must be true size)
**Size:** S
**Decision needed first:** **yes, and it is the whole spec.** Branch A prints true size and clips; Branch B keeps today's scaling and stops distorting. Read §1, pick one, implement only that branch.

---

## 1. Context

Deckle rasterises the **whole sheet** — 612×792 pt for Letter, with the
imposer's margins already inside it — and then paints that image into the
printer's *imageable area*, which is the sheet minus the hardware border.
A sheet designed at 612 pt wide is drawn 576 pt wide on a printer with 18 pt
margins. Every measurement in the finished book is about 6% short.

Two things follow, and the second is worse:

1. **Everything shrinks by the border.** At 18 pt margins a Letter sheet
   prints at ~94% of its designed size. A hand binder cuts boards to
   measured dimensions, so a text block 6% smaller than designed is a text
   block that does not fit the case made for it.
2. **The shape changes.** `QImage.scaled(w, h)` ignores aspect ratio by
   default. Real printers have a deeper border on the feed edge, so the
   printed sheet is stretched relative to the design. Equal margins hide
   this; no real printer has equal margins.

Neither is visible before you commit paper, because **the preview does not
model it.** The preview rasterises the exported PDF and shows it whole. That
is the project's central honesty claim — "the preview rasterises the real
exported PDF... a rasterised PDF cannot lie about what will print" — and on
the desktop print path it is currently false. The PDF is honest; the painter
is what changes it.

**This is already pinned as a decision, not discovered here.**
`tests/test_print_painting.py:167-225` contains two tests whose docstrings
lay out the trade-off and assert today's behaviour with numbers, ending:
*"Which is right is a product decision, not a bug to fix quietly."* This spec
is that decision being made. Whichever branch is chosen, those two tests are
rewritten deliberately, and the docstrings are what tell the implementer they
are allowed to.

**Verify on the unfixed tree:**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/test_print_painting.py -k "scaled_to_the_imageable_area or aspect_ratio_changes" -v
```

Both pass, and their names state the defect.

### Choosing

**Branch A — draw 1:1 at the paper origin, warn on clipping.** The sheet
prints at true size; content that falls inside the hardware border is clipped
by the printer. Deckle already computes exactly that warning
(`clipped_by_imageable_area`), and the layout panel already lets the user set
margins that keep content out of the border. Recommended: it makes the
preview true again, it is what "the preview cannot lie" requires, and it is
what a binder measuring boards needs. The cost is that a user with a
zero-margin layout loses the outer few millimetres, having been warned.

**Branch B — keep fitting to the imageable area, but preserve aspect and
tell the truth.** Scale uniformly (`KeepAspectRatio`), centre within the
imageable area, and surface the scale factor in the print dialog and the
preview so the number is never a surprise. Choose this only if the owner
would rather lose 6% of size than any ink at the edges. It keeps the preview
approximate by construction, so the preview must then display the factor.

The branches are mutually exclusive. Do not implement both behind a setting:
a control that is already expressible must not gain a second control
expressing it, per `docs/decisions.md`, *Deleted the scale mode*.

---

## 2. Current code

`deckle/app/backend.py:514-529`, the whole defect:

```python
    def _paint_rendered_page(self, painter, printer, rendered: RenderedPage) -> None:
        if rendered.width == 0 or rendered.height == 0:
            return
        image = _qimage(rendered.rgba, rendered.width, rendered.height)
        left_pt, top_pt, right_pt, bottom_pt = self.profile.imageable_area_pt
        dpi_scale = printer.resolution() / 72.0
        target_x = left_pt * dpi_scale
        target_y = top_pt * dpi_scale
        target_w = max(0.0, printer.width() - (left_pt + right_pt) * dpi_scale)
        target_h = max(0.0, printer.height() - (top_pt + bottom_pt) * dpi_scale)
        painter.drawImage(
            int(target_x),
            int(target_y),
            image.scaled(int(target_w) or 1, int(target_h) or 1),
        )
```

`image.scaled(w, h)` with two ints is the aspect-ignoring overload. That is
defect 2. Fitting `target_w`/`target_h` to the imageable area rather than the
page is defect 1.

`deckle/app/backend.py:487-490`, why the full sheet is the right raster and
must stay so:

```python
        # Margins come from the profile's imageable_area_pt, never Qt's
        # own defaults -- setFullPage(True) is what makes that true.
        printer.setFullPage(True)
```

Full-page mode means `printer.width()`/`height()` are the **whole sheet** in
device pixels, and the origin is the sheet corner. Both branches depend on
this; neither changes it.

**Call sites.**

| Symbol | Where | Note |
|---|---|---|
| `_paint_rendered_page` | `backend.py:514` | called only from `_submit_chunk` (`backend.py:511`) and the duplex path (`backend.py:643`) |
| `_submit_chunk` | `backend.py:474` | the manual-duplex painter |
| `submit_duplex` | `backend.py:553` | uncalled today (F9); paints through the same method |
| `imageable_area_pt` | `profiles.py:49` | also drives the preview guide (`preview_view`) and `clipped_by_imageable_area` |
| `clipped_by_imageable_area` | `core/layout.py`, `models.py` warning kind | already computed; Branch A relies on it |

**Tests that pin current behaviour and must be rewritten, not deleted:**
`tests/test_print_painting.py:167` (the 94% assertion) and `:200` (the aspect
assertion). Every other test in that file asserts placement or guards against
zero-size images and stays as it is.

---

## 3A. Change — Branch A, true size and clip

1. **`deckle/app/backend.py`**, `_paint_rendered_page`: replace the body
   after the `_qimage` call with a 1:1 draw at the sheet origin.

   ```python
       def _paint_rendered_page(self, painter, printer, rendered: RenderedPage) -> None:
           """Draw the sheet at true size, at the sheet's own origin.

           The raster is the whole sheet and the printer is in full-page
           mode, so the paper corner is (0, 0) and one sheet point is
           ``resolution() / 72`` device pixels. Fitting the sheet into the
           imageable area instead would print every book about 6% small and,
           with the asymmetric borders real printers have, the wrong shape --
           and the preview, which rasterises the exported PDF whole, would
           have no way to show either. Content inside the hardware border is
           clipped by the printer; ``clipped_by_imageable_area`` is the
           warning that says so before the paper is committed.
           """
           if rendered.width == 0 or rendered.height == 0:
               return
           image = _qimage(rendered.rgba, rendered.width, rendered.height)
           dpi_scale = printer.resolution() / 72.0
           target_w = max(1, int(round(rendered.sheet_width_pt * dpi_scale)))
           target_h = max(1, int(round(rendered.sheet_height_pt * dpi_scale)))
           painter.drawImage(
               0,
               0,
               image.scaled(target_w, target_h, _KEEP_ASPECT, _SMOOTH),
           )
       ```

   The raster was produced at `dpi`, so `rendered.width` is already
   `sheet_width_pt * dpi / 72`. Scaling to `target_w`/`target_h` converts
   from the raster's dpi to the printer's resolution and is the identity when
   they match. Do not skip it: `dpi` and `printer.resolution()` differ
   routinely (300 vs 600).

2. **`deckle/app/backend.py`**: if `RenderedPage` does not already carry the
   sheet size in points, do not add fields to it — derive the target from the
   printer instead, which in full-page mode is the same sheet:

   ```python
           target_w = max(1, printer.width())
           target_h = max(1, printer.height())
   ```

   Check `deckle/core/render.py` for `RenderedPage`'s fields first and use
   whichever form is available. Prefer the printer form: it needs no model
   change, and in full-page mode `printer.width()` **is** the sheet.

3. **`deckle/app/backend.py`**, module scope: add the two Qt enum aliases
   beside the other lazy Qt helpers, so the scaling call reads clearly and
   the enum import stays lazy like the rest of the module:

   ```python
   def _keep_aspect():
       from PySide6.QtCore import Qt
       return Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
   ```

   Call it inside `_paint_rendered_page` rather than at import time. The
   module's existing pattern is lazy Qt imports inside functions; follow the
   file you are editing.

4. **`self.profile.imageable_area_pt` is now unused in this method.** Do not
   delete the field: it still drives the preview's red guide and the
   `clipped_by_imageable_area` warning, which is precisely what makes Branch
   A safe. Leave the import and the attribute alone.

5. **`tests/test_print_painting.py:167`**: rewrite
   `test_the_sheet_is_scaled_to_the_imageable_area_not_printed_true_size` as
   `test_the_sheet_prints_at_true_size`, asserting `call["w"] ==
   printer.width()` and `call["x"] == 0`, with a docstring recording the
   decision and pointing at this spec.

6. **`tests/test_print_painting.py:200`**: rewrite
   `test_the_aspect_ratio_changes_when_the_borders_are_asymmetric` as
   `test_asymmetric_borders_do_not_change_the_printed_shape`, same fixture,
   asserting `printed_aspect == pytest.approx(sheet_aspect, abs=1e-3)`.

7. **`docs/GUIDE.md`**, §3: the red guide's meaning becomes load-bearing
   rather than advisory. Add one sentence: content outside the red line is
   clipped by the printer, and the layout margins are how you keep content
   inside it. Coordinate with `D-docs-pass.md` D4, which is editing the same
   paragraph for a different reason.

## 3B. Change — Branch B, uniform scale, centred, and stated

1. **`deckle/app/backend.py`**, `_paint_rendered_page`: keep the imageable
   target, scale uniformly, and centre the result in it.

   ```python
           left_pt, top_pt, right_pt, bottom_pt = self.profile.imageable_area_pt
           dpi_scale = printer.resolution() / 72.0
           box_x = left_pt * dpi_scale
           box_y = top_pt * dpi_scale
           box_w = max(1.0, printer.width() - (left_pt + right_pt) * dpi_scale)
           box_h = max(1.0, printer.height() - (top_pt + bottom_pt) * dpi_scale)
           scaled = image.scaled(int(box_w), int(box_h), _KEEP_ASPECT, _SMOOTH)
           painter.drawImage(
               int(box_x + (box_w - scaled.width()) / 2.0),
               int(box_y + (box_h - scaled.height()) / 2.0),
               scaled,
           )
   ```

2. **Expose the factor.** Add a pure helper in `deckle/core/printing.py` so
   both the dialog and the preview can state the same number without
   importing Qt:

   ```python
   def print_scale_factor(paper_pt: tuple[float, float],
                          imageable_area_pt: tuple[float, float, float, float]) -> float:
       """How much a sheet shrinks when fitted into the imageable area."""
   ```

   Return `min((w - l - r) / w, (h - t - b) / h)`, clamped to `(0.0, 1.0]`.

3. **`deckle/app/views/print_dialog.py`**: show it above the Print button,
   exactly: `Sheets print at 94% of designed size (your printer's border).`
   with the integer percent from step 2. Omit the line entirely when the
   factor rounds to 100%.

4. **`deckle/app/views/preview_view.py`**: the preview must stop implying
   true size. Add the same sentence under the sheet, or the preview keeps
   the lie this branch accepts.

5. **`tests/test_print_painting.py:167`**: rewrite to assert the factor
   equals `print_scale_factor(...)` rather than a hard-coded 94%, so the
   number lives in one place.

6. **`tests/test_print_painting.py:200`**: rewrite as
   `test_asymmetric_borders_do_not_change_the_printed_shape` — identical to
   Branch A step 6. Aspect distortion is a defect under either branch.

7. **`docs/GUIDE.md`** and **`README.md`**: state that printed sheets are
   reduced to fit the printer's border, and by roughly how much. Under this
   branch the README's "the preview rasterises the real exported PDF"
   paragraph needs a qualifying clause about the print path.

---

## 4. Tests

Under **both** branches:

1. **`tests/test_print_painting.py::test_asymmetric_borders_do_not_change_the_printed_shape`**
   Profile `imageable_area_pt=(12.0, 48.0, 12.0, 12.0)`, a 612×792 sheet, a
   600 dpi fake printer. Assert `call["w"] / call["h"] ==
   pytest.approx(612 / 792, abs=1e-3)`.
   *Fails now with:* the existing inverse assertion at
   `test_print_painting.py:200` proves it — today the aspect differs, so the
   new test fails on `assert 0.766 == approx(0.773)`.

Branch A only:

2. **`tests/test_print_painting.py::test_the_sheet_prints_at_true_size`**
   18 pt margins, 600 dpi, Letter. Assert `call["x"] == 0`, `call["y"] == 0`
   and `call["w"] == printer.width()`.
   *Fails now with:* `assert 150 == 0` on the x offset (18 pt at 600 dpi).

3. **`tests/test_print_painting.py::test_a_zero_margin_profile_prints_identically`**
   The same sheet through a profile with `imageable_area_pt=(0,0,0,0)` and
   through one with 18 pt margins produce the same `call["w"]`. This is the
   test that says the border no longer changes the geometry, only what the
   printer clips.
   *Fails now with:* the two widths differ by 36 pt of device pixels.

Branch B only:

4. **`tests/test_print_painting.py::test_the_sheet_is_centred_in_the_imageable_area`**
   With `imageable_area_pt=(12.0, 48.0, 12.0, 12.0)`, assert the drawn image's
   left inset from the box equals its right inset within one pixel.
   *Fails now with:* today's draw is flush to the top-left of the box.

5. **`tests/test_printing.py::test_print_scale_factor_matches_the_painter`**
   Pure test of the step-2 helper against the same numbers the painter test
   uses.
   *Fails now with:* `ImportError: cannot import name 'print_scale_factor'`.

The fake printer and `_paint` harness at the top of
`tests/test_print_painting.py` already provide everything these need. Do not
build a new one.

---

## 5. Acceptance

| Check | Command |
|---|---|
| The new painting tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_print_painting.py` |
| The aspect-ignoring overload is gone | `! grep -n 'image.scaled(int(target_w) or 1, int(target_h) or 1)' deckle/app/backend.py` |
| **A only** — the painter no longer offsets by the profile margins | `! grep -n 'target_x = left_pt \* dpi_scale' deckle/app/backend.py` |
| **A only** — the old decision test is gone by name | `! grep -rn 'test_the_sheet_is_scaled_to_the_imageable_area' tests/` |
| **B only** — the factor has one home | `grep -n 'def print_scale_factor' deckle/core/printing.py && grep -rn 'print_scale_factor' deckle/app/ \| wc -l` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` Print one sheet on scrap with a 6-inch line imposed on it. Measure it with a ruler. **A:** it reads 6 inches. **B:** it reads what the dialog said it would. | — |

The human row is the only check that closes this. Every mechanical row above
asserts device pixels against a fake printer; whether a real driver honours
full-page mode is a question only paper answers, and it is the same question
F4 exists to ask.

---

## 6. Out of scope

- **B36** (the back pass paints fronts) is the other defect in
  `_paint_rendered_page`'s caller and lands independently. If both are
  scheduled, do B36 first: there is no point measuring a back pass that is
  printing fronts.
- **B15 / N2** (the profile reaching the preview, and a real imageable area
  from the driver). Branch A makes the margins matter *more*, so N2 becomes
  more valuable, but neither is required here.
- **F9** (hardware duplex) paints through the same method and inherits
  whichever branch is chosen. No extra work.
- The imposer's own margins are not touched. This spec changes only how a
  finished sheet reaches paper.

---

## 7. decisions.md entry

Branch A:

```
## 2026-09-05 — The printed sheet was 6% small, and the preview could not say so
- Symptom: _paint_rendered_page fitted the whole rasterised sheet into the printer's
  imageable area with QImage.scaled's aspect-ignoring overload. A letter sheet on an
  18pt-border printer came out at 94% of designed size, and asymmetric borders -- which
  every real printer has -- stretched it as well. The preview rasterises the exported
  PDF whole, so neither was visible before the paper was committed.
- Fix: draw the sheet 1:1 at the paper origin in full-page mode, uniformly scaled only
  to convert raster dpi to printer resolution. Content inside the hardware border is
  clipped by the printer, and clipped_by_imageable_area is the warning that says so
  first. Chose true size over fit-to-border because boards are cut to measured
  dimensions: a text block 6% short does not fit the case made for it.
- Surfaces: deckle/app/backend.py (_paint_rendered_page), tests/test_print_painting.py
  (two pinned decision tests rewritten), docs/GUIDE.md §3.
- Watch: the two rewritten tests were written as a deliberate record of an undecided
  question, with the numbers in their docstrings. That is why this was a decision to
  make rather than a bug to find -- and why the tests changed rather than the code
  changing quietly underneath them.
- Commit: <fill in>
```

Branch B: same Symptom; Fix reads *"kept the fit-to-imageable-area behaviour
but scaled uniformly and centred it, and surfaced the scale factor in the
print dialog and the preview so the reduction is stated rather than
discovered"*; Surfaces adds `deckle/core/printing.py`,
`deckle/app/views/print_dialog.py`, `deckle/app/views/preview_view.py`,
`README.md`.

---

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
- **`setFullPage(True)` is load-bearing and must not be touched.** It is why
  `printer.width()` is the whole sheet and the origin is the paper corner.
  Removing it makes Qt apply its own margins on top of the profile's, and
  both branches' arithmetic silently becomes wrong. The comment at
  `backend.py:487-489` says so; leave it.
- **Do not confuse the raster's dpi with the printer's resolution.** The
  sheet is rasterised at `dpi` (300 by default) and painted onto a device at
  `printer.resolution()` (often 600). Branch A's scale step is what bridges
  them; deleting it as a no-op prints at half size on a 600 dpi printer.
- **Do not add a setting.** Both branches were considered; one is chosen. A
  toggle here re-creates the `scale_mode` mistake the decisions log records.
- **`submit_duplex` paints through this method too** and is currently
  uncalled. It needs no edit, but do not assume `_paint_rendered_page` has
  one caller.
- The `_FakePrinter` in `tests/test_print_painting.py` reports `width()`/
  `height()` in device pixels from `paper_pt` and `dpi`. If an assertion of
  yours needs points, convert; do not change the fake.
