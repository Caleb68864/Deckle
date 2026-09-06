# M2 — Extract the placement seams, changing no output

**Roadmap item:** `docs/ROADMAP.md` M2
**Depends on:** —
**Blocks:** B1, B5 (both edit `_place_page`; this reshapes it first)
**Size:** M
**Decision needed first:** none. §3.0 records the one place this spec
diverges from the roadmap's advice and why.

---

## 1. Context

Two functions in `deckle/core/layout.py` have grown past the point where
a change to one of their concerns can be made without reading all of
them:

```bash
.venv/bin/python -c "
import ast, inspect
import deckle.core.layout as m
tree = ast.parse(inspect.getsource(m))
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in ('_place_page',):
        print(node.name, node.end_lineno - node.lineno + 1, 'lines')
    if isinstance(node, ast.ClassDef) and node.name == 'SaddleStitchStrategy':
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef) and sub.name == 'impose':
                print('SaddleStitchStrategy.impose', sub.end_lineno - sub.lineno + 1, 'lines')
"
```

```
_place_page 157 lines
SaddleStitchStrategy.impose 216 lines
```

Three concrete duplications, all verifiable by reading:

**1. The clamped-margin arithmetic, copied three times.**
`content_box_size` (`deckle/core/layout.py:226-230`), `_place_page`
(`:403-425`) and `content_box_rect_pt` (`:543-549`) each clamp four
margins at zero, subtract them from the cell, and test whether anything
is left. They differ only in what they do when nothing is:
`content_box_size` returns the bare cell, `content_box_rect_pt` returns
the bare cell, `_place_page` warns *and* zeroes the margins. Three copies
of one rule is three chances to change two of them.

**2. The four `_place_page` calls in `SaddleStitchStrategy.impose` are
ten identical lines each** (`:985-1029`), differing in two arguments.

**3. The rotation decision is spelled twice, against two rectangles**
(`_rotates_to_portrait` at `:292-294` versus the inline test at
`:383-385`). That difference is a live defect — **B5** — and this spec
extracts the seam without changing which rectangle either side reads.

**Why this matters to a person printing a book.** Not directly; nothing
about the output changes. It matters because B1, B5 and B8 all land in
this code, the roadmap sequences them together, and a 157-line function
holding four unrelated decisions is where a fix to one of them
accidentally moves another. The placement pin
(`tests/test_cell_geometry.py::GUTTER_SHIFT_PLACEMENT_PIN`) exists
precisely because that has happened before, and its docstring says
regenerating it is an escalation.

## 2. Current code

### `deckle/core/layout.py:204-230` — `content_box_size`

```python
def content_box_size(
    settings: LayoutSettings, cell: Cell | None = None
) -> tuple[float, float]:
    """The content box's ``(width, height)`` in points, after all four margins.

    Falls back to the bare cell when the margins would consume it entirely;
    ``_place_page`` warns about that case, this function stays silent so it
    can be called from the scale pass without duplicating warnings.
    ...
    """
    if cell is None:
        cell = _full_sheet_cell(settings.paper)
    cx0, cy0, cx1, cy1 = cell
    cell_w = cx1 - cx0
    cell_h = cy1 - cy0
    box_w = cell_w - max(0.0, settings.gutter_pt) - max(0.0, settings.margin_outer_pt)
    box_h = cell_h - max(0.0, settings.margin_top_pt) - max(0.0, settings.margin_bottom_pt)
    if box_w <= 0.0 or box_h <= 0.0:
        return (cell_w, cell_h)
    return (box_w, box_h)
```

### `deckle/core/layout.py:403-425` — the same arithmetic inside `_place_page`

```python
    gutter = max(0.0, settings.gutter_pt)
    outer = max(0.0, settings.margin_outer_pt)
    top = max(0.0, settings.margin_top_pt)
    bottom = max(0.0, settings.margin_bottom_pt)

    box_w = cell_w - gutter - outer
    box_h = cell_h - top - bottom

    if box_w <= 0.0 or box_h <= 0.0:
        # Margins consume the whole cell. Warn and fall back to the bare
        # cell rather than producing a negative-size box and nonsense scale.
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="clipped_by_page",
                detail=(
                    "margins and gutter exceed the paper size; "
                    "ignoring margins for this sheet"
                ),
            )
        )
        gutter = outer = top = bottom = 0.0
        box_w, box_h = cell_w, cell_h
```

### `deckle/core/layout.py:538-556` — and again inside `content_box_rect_pt`

```python
    if cell is None:
        cell = _full_sheet_cell(settings.paper)
    cx0, cy0, cx1, cy1 = cell
    cell_w = cx1 - cx0
    cell_h = cy1 - cy0
    gutter = max(0.0, settings.gutter_pt)
    outer = max(0.0, settings.margin_outer_pt)
    top = max(0.0, settings.margin_top_pt)
    bottom = max(0.0, settings.margin_bottom_pt)

    if cell_w - gutter - outer <= 0.0 or cell_h - top - bottom <= 0.0:
        return (cx0, cy0, cx1, cy1)

    if spine_side is not None:
        gutter_on_left = spine_side == "left"
    else:
        gutter_on_left = _gutter_side_is_left(is_recto, settings.binding_edge)
    left, right = (gutter, outer) if gutter_on_left else (outer, gutter)
    return (cx0 + left, cy0 + bottom, cx1 - right, cy1 - top)
```

The three collapse conditions are the same predicate written three ways:
`box_w <= 0.0 or box_h <= 0.0` twice, and
`cell_w - gutter - outer <= 0.0 or cell_h - top - bottom <= 0.0` once.

### `deckle/core/layout.py:378-397` — the rotation decision inside `_place_page`

```python
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

### `deckle/core/layout.py:983-1032` — the four calls

```python
                front = Side(
                    pages=(
                        _place_page(
                            slots[page_offset + front_a],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_a,
                            spine_side=spine_a,
                        ),
                        _place_page(
                            slots[page_offset + front_b],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_b,
                            spine_side=spine_b,
                        ),
                    ),
                    marks=tuple(front_marks),
                )
                back = Side(
                    pages=(
                        _place_page(
                            slots[page_offset + back_a],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_a,
                            spine_side=spine_a,
                        ),
                        _place_page(
                            slots[page_offset + back_b],
                            0,
                            settings,
                            sheet_index,
                            warnings,
                            scale,
                            cell=cell_b,
                            spine_side=spine_b,
                        ),
                    ),
                    marks=tuple(back_marks),
                )
```

Four calls, six identical arguments each, differing only in the slot
index and the `(cell, spine_side)` pair.

### Every caller of the functions this spec reshapes

`grep -rn "_place_page\|content_box_size\|content_box_rect_pt\|_fitted_dims\|_rotates_to_portrait" --include='*.py' .`
(excluding `.venv`):

| Site | Function |
|---|---|
| `deckle/core/layout.py:665, 669, 985, 995, 1010, 1020` | `_place_page`, all six production calls |
| `deckle/core/layout.py:334` | `content_box_size`, from `document_scale` |
| `deckle/core/layout.py:299, 339` | `_source_dims` / `_fitted_dims` |
| `deckle/core/layout.py:300` | `_rotates_to_portrait` |
| `deckle/app/views/preview_view.py` | `content_box_rect_pt` — `grep -n content_box_rect_pt deckle/app/views/preview_view.py` |
| `tests/test_cell_geometry.py:34-39, 261-266, 292-306, 331, 394` | all four public functions plus `_place_page` |
| `tests/test_spine_side_precedence.py:41-43, 153, 157, 174, 175` | `_place_page`, `content_box_rect_pt`, `actual_margins_pt` |
| `tests/test_layout_saddle.py:446, 456, 460` | `_place_page`, `document_scale` |
| `tests/test_layout.py:308, 312, 459, 463-464, 492-497, 507-511` | `content_box_size`, `content_box_rect_pt`, `document_scale` |

**`_place_page`'s positional signature is used by three test modules.**
`tests/test_cell_geometry.py:266` calls
`_place_page(make_page(size=TRAVELLER), 0, se, 0, [], 1.0, cell=full)` —
six positionals. It must not change, and §3 keeps it exactly.

### The pins this spec must not move

- `tests/test_cell_geometry.py:95-232` — `GUTTER_SHIFT_PLACEMENT_PIN`,
  12 parametrised cases over 2 page sizes × 2 binding edges × 3
  `slack_to` values, asserting every `(scale_x, scale_y, tx, ty,
  rotate_deg, is_filler)` tuple to six decimal places. Its docstring:
  *"Regenerating these values is an ESCALATION, not a maintenance chore …
  drift here means the generalisation changed MVP behaviour, which is the
  one outcome SS-07 exists to prevent."*
- `tests/test_schedule.py:72-98` — the hand-written saddle-stitch
  ordering table.
- `tests/test_layout_saddle.py:507-518` — the fold-simulator round trip
  over page counts 1-40 × signature sizes 1-8 × both binding edges, and
  again for 100 and 266 pages.
- `tests/test_golden_pinebox.py` — skips without the ~30 MB fixture. If
  you have it, run it; if not, say so.
- `tests/test_imposition_properties*.py` — Hypothesis properties over
  page counts, gutters, crops, trims and explicit signature lengths.

## 3. Change

### 3.0 One divergence from the roadmap, recorded

The roadmap says of M2: *"Do this **with** B1/B5/B8, not after."* This
spec instead makes M2 **extract-only and behaviour-preserving**, and
lands B1, B5 and B8 as separate commits on top.

Reason: the pins above are the only evidence available that a refactor of
this function did not change the paper. That evidence is worth exactly as
much as its ability to stay green — and it can only stay green if nothing
in the same commit is *supposed* to change it. B5 moves `document_scale`
for folio landscape sources; B1 moves `Placement.rotate_deg` for rotated
pages. Merged with the extraction, a red pin becomes ambiguous, and the
correct response to an ambiguous pin is to read a 157-line diff.

Cost: one extra commit. Benefit: `pytest tests/test_cell_geometry.py`
going green after M2 is proof, and going red is a bug.

**Which items land where:**

| Item | Where | Why |
|---|---|---|
| **M2** | this spec, first | extract-only; every existing test green, unedited |
| **B5** | after M2 | changes `_fitted_dims`'s answer for folio; §3.1 step 3 leaves the exact seam it needs |
| **B1** | after M2 (order with B5 irrelevant) | changes `Placement.rotate_deg`; §3.1 step 3 leaves the seam |
| **B8** | any time, independent | touches `_creep_advisory`, `paper.py` and `schedule.py`; **no overlap with this spec at all** |
| **B2**, **B7**, **B18**, **B20** | any time, independent | different modules or different functions |

If the owner prefers the roadmap's single commit, the step lists compose
in the order M2 → B5 → B1, and the pin must then be checked at the M2
step by stashing the other two.

### 3.1 Numbered edits

All in `deckle/core/layout.py`. **Every step is a pure extraction: the
expression evaluated is character-for-character the same, only its
location changes.**

1. **A margin helper**, immediately after `_full_sheet_cell`
   (i.e. after line 50):

   ```python
   def _margins(settings: LayoutSettings) -> tuple[float, float, float, float]:
       """The four margins, clamped at zero: ``(gutter, outer, top, bottom)``.

       Negative margins are clamped rather than refused, on purpose and
       with a test pinning it (``tests/test_layout.py::
       test_negative_margins_are_clamped_to_zero``) -- and
       ``project_io._check_layout_values`` says so outright, which is why
       nothing validates them at load time.

       The gutter IS the inner (spine) margin, so these four describe all
       four edges of a page. Returned in spine-outer-top-bottom order to
       match the order they are unpacked at every call site.
       """
       return (
           max(0.0, settings.gutter_pt),
           max(0.0, settings.margin_outer_pt),
           max(0.0, settings.margin_top_pt),
           max(0.0, settings.margin_bottom_pt),
       )
   ```

2. **A content-box helper**, immediately after `_margins`:

   ```python
   def _box_within(
       settings: LayoutSettings, cell: Cell | None
   ) -> tuple[tuple[float, float, float, float], float, float, bool]:
       """The content box inside ``cell``: margins, size, and whether it collapsed.

       Returns ``((gutter, outer, top, bottom), box_w, box_h, collapsed)``.
       When ``collapsed`` is ``True`` the margins consume the cell entirely;
       the margins are then all zero and the box is the bare cell, so a
       caller that ignores the flag still gets a usable, positive box
       rather than a negative-size one and a nonsense scale.

       Three functions had this arithmetic copied -- ``content_box_size``,
       ``content_box_rect_pt`` and ``_place_page`` -- and they differ only
       in what they do about ``collapsed``: the first two stay silent so
       the scale pass can call them without duplicating a warning, and
       ``_place_page`` emits ``clipped_by_page``. One rule, three
       reactions, is the shape this returns.

       :param settings: supplies the four margins.
       :param cell: the region to measure inside, or ``None`` for the whole
           sheet.
       :returns: ``(margins, box_w, box_h, collapsed)``.
       """
       if cell is None:
           cell = _full_sheet_cell(settings.paper)
       cx0, cy0, cx1, cy1 = cell
       cell_w = cx1 - cx0
       cell_h = cy1 - cy0
       gutter, outer, top, bottom = _margins(settings)
       box_w = cell_w - gutter - outer
       box_h = cell_h - top - bottom
       if box_w <= 0.0 or box_h <= 0.0:
           return ((0.0, 0.0, 0.0, 0.0), cell_w, cell_h, True)
       return ((gutter, outer, top, bottom), box_w, box_h, False)
   ```

   **Equivalence check for `content_box_rect_pt`.** Its collapse test is
   `cell_w - gutter - outer <= 0.0 or cell_h - top - bottom <= 0.0`,
   which is `box_w <= 0.0 or box_h <= 0.0` with the subtraction inlined.
   Same expression, same floats, same result — no reassociation.

3. **A rotation helper**, replacing nothing yet, placed immediately after
   `_source_dims` (i.e. after line 201):

   ```python
   def _policy_rotation(
       src_w: float, src_h: float, settings: LayoutSettings, cell: Cell
   ) -> int:
       """The rotation ``landscape_policy`` adds for a page this shape, in ``cell``.

       ``90`` for landscape content in a portrait cell under
       ``landscape_policy="rotate"``, ``0`` otherwise. Judged against the
       cell rather than the sheet: under folio the sheet is landscape and
       each of its two cells is portrait, so the two questions have
       opposite answers.

       Extracted verbatim from ``_place_page``. ``_rotates_to_portrait``
       asks the same question against ``settings.paper`` and still does --
       reconciling the two is B5, not this refactor.

       :param src_w: the page's width after cropping and after the user's
           own rotation, i.e. as ``_source_dims`` returns it.
       :param src_h: the same for height.
       :param settings: read for ``landscape_policy`` only.
       :param cell: the region the leaf is placed into.
       :returns: ``90`` or ``0``.
       """
       if settings.landscape_policy != "rotate":
           return 0
       cx0, cy0, cx1, cy1 = cell
       cell_is_portrait = (cy1 - cy0) >= (cx1 - cx0)
       return 90 if cell_is_portrait and src_w > src_h else 0
   ```

   **This is the seam B5 needs.** After M2 there are two functions asking
   one question; B5 deletes `_rotates_to_portrait`, gives `_fitted_dims`
   a `cell`, and points it here. **Do not do that in this spec.**

4. **Rewrite `content_box_size`'s body** (lines 221-230) as

   ```python
       _margins_unused, box_w, box_h, _collapsed = _box_within(settings, cell)
       return (box_w, box_h)
   ```

   with the docstring's "Falls back to the bare cell…" paragraph kept
   verbatim. Name the unused values with a leading underscore rather than
   `_` twice — the tuple is four wide and a reader needs to see which two
   are being dropped.

5. **Rewrite `content_box_rect_pt`'s body** (lines 538-556) as

   ```python
       if cell is None:
           cell = _full_sheet_cell(settings.paper)
       cx0, cy0, cx1, cy1 = cell
       (gutter, outer, top, bottom), _box_w, _box_h, collapsed = _box_within(
           settings, cell
       )
       if collapsed:
           return (cx0, cy0, cx1, cy1)

       if spine_side is not None:
           gutter_on_left = spine_side == "left"
       else:
           gutter_on_left = _gutter_side_is_left(is_recto, settings.binding_edge)
       left, right = (gutter, outer) if gutter_on_left else (outer, gutter)
       return (cx0 + left, cy0 + bottom, cx1 - right, cy1 - top)
   ```

   The `cell is None` resolution is repeated here because the function
   needs `cx0..cy1` itself. That is two lines, not four; leave it.

6. **Rewrite `_place_page`'s rotation block** (lines 375-397) as

   ```python
       src_w, src_h = _source_dims(slot, settings)
       rotate_deg = 0

       # Landscape content inside a portrait cell (or vice versa) under the
       # "rotate" policy: rotate the content to match the cell's orientation
       # and warn, rather than silently clipping or shrinking it. Under folio
       # each cell is portrait-shaped even though the sheet itself is
       # landscape, so this must be judged against the cell, not the sheet.
       if _policy_rotation(src_w, src_h, settings, cell) == 90:
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

   `cell_is_portrait` and `page_is_landscape` are now unused locals; delete
   both. `cell_h` and `cell_w` are still used by step 7.

7. **Rewrite `_place_page`'s margin block** (lines 403-425) as

   ```python
       (gutter, outer, top, bottom), box_w, box_h, collapsed = _box_within(
           settings, cell
       )
       if collapsed:
           # Margins consume the whole cell. Warn and fall back to the bare
           # cell rather than producing a negative-size box and nonsense scale.
           warnings.append(
               LayoutWarning(
                   sheet_index=sheet_index,
                   kind="clipped_by_page",
                   detail=(
                       "margins and gutter exceed the paper size; "
                       "ignoring margins for this sheet"
                   ),
               )
           )
   ```

   `_box_within` already returns `(0.0, 0.0, 0.0, 0.0)` and the bare cell
   when it collapses, which is exactly what the deleted
   `gutter = outer = top = bottom = 0.0` and `box_w, box_h = cell_w, cell_h`
   did. **Check the ordering:** `cell_w`/`cell_h` are computed at
   `deckle/core/layout.py:368-369` from the resolved `cell`, and
   `_box_within` re-resolves `cell` itself. `_place_page` resolves `cell`
   at `:365-366` before either, so both see the same tuple. Leave that
   resolution where it is.

8. **A local pair helper in `SaddleStitchStrategy.impose`**, defined once
   inside the method — after `scale` is computed at line 924 and before
   the `for sig_index, group in enumerate(groups)` loop at line 934:

   ```python
           def place_pair(
               slot_a: int, slot_b: int, sheet_index: int
           ) -> tuple[OutputPage, OutputPage]:
               """One face's two leaves, `a` in its cell and `b` in the other.

               Ten identical lines, four times, differing in two arguments.
               A closure rather than a module function because it captures
               `slots`, `page_offset`, `settings`, `warnings`, `scale` and
               the cell/spine pairing -- passing all six would be longer
               than the duplication it removes.

               `output_index` is `0` for both leaves: under folio the spine
               is a function of which cell a leaf sits in, never of
               output-page parity, so `_place_page` is given `spine_side`
               and the index is inert.
               """
               return (
                   _place_page(
                       slots[page_offset + slot_a], 0, settings, sheet_index,
                       warnings, scale, cell=cell_a, spine_side=spine_a,
                   ),
                   _place_page(
                       slots[page_offset + slot_b], 0, settings, sheet_index,
                       warnings, scale, cell=cell_b, spine_side=spine_b,
                   ),
               )
   ```

   **This will not work where it is placed.** `page_offset`, `cell_a`,
   `cell_b`, `spine_a` and `spine_b` are assigned *inside* the loops
   (`:935`, `:954-959`). A closure defined before them reads the values
   at call time, which is after they are assigned — Python closures
   capture the variable, not the value, and every call happens inside the
   same iteration that set them. So it works, and it works for a reason
   that is easy to get wrong on the next edit.

   **Define it inside the `for local_idx, sheet_index in enumerate(group)`
   loop instead**, immediately after `spine_a`/`spine_b` are decided
   (i.e. after line 959), and drop the `sheet_index` parameter — it is in
   scope. That makes the capture lexically obvious and costs one function
   object per sheet, which is nothing beside four `_place_page` calls:

   ```python
                   def place_pair(slot_a: int, slot_b: int) -> tuple[OutputPage, OutputPage]:
                       """One face's two leaves: `a` in `cell_a`, `b` in `cell_b`.

                       Ten identical lines, four times, differing only in
                       which slot each leaf comes from. `output_index` is
                       `0` for both: under folio the spine is a function of
                       which cell a leaf sits in, never of output-page
                       parity, so `spine_side` is given and the index is
                       inert -- see `_place_page`.
                       """
                       return (
                           _place_page(
                               slots[page_offset + slot_a], 0, settings,
                               sheet_index, warnings, scale,
                               cell=cell_a, spine_side=spine_a,
                           ),
                           _place_page(
                               slots[page_offset + slot_b], 0, settings,
                               sheet_index, warnings, scale,
                               cell=cell_b, spine_side=spine_b,
                           ),
                       )
   ```

9. **Rewrite the two `Side` constructions** (lines 983-1032) as

   ```python
                   front = Side(pages=place_pair(front_a, front_b),
                                marks=tuple(front_marks))
                   back = Side(pages=place_pair(back_a, back_b),
                               marks=tuple(back_marks))
   ```

   **Warning order is load-bearing.** `_place_page` appends to a shared
   `warnings` list, and `plan.warnings` is a list whose order the CLI
   prints and `tests/test_layout_saddle.py` counts. `place_pair` evaluates
   its tuple left to right, and `front` is built before `back`, so the
   order is `front_a, front_b, back_a, back_b` — identical to the
   original. Do not reorder the two `Side` assignments.

10. **`docs/api/`** — nothing to add; no new module.

### 3.2 What must not change

- `_place_page`'s signature, including the six positional parameters and
  the two keyword-only ones. Three test modules call it directly.
- The public signatures of `content_box_size`, `content_box_rect_pt`,
  `document_scale`, `actual_margins_pt`, `cell_geometry`,
  `GutterShiftStrategy.impose`, `SaddleStitchStrategy.impose`.
- `_rotates_to_portrait` and `_fitted_dims`. Step 3 adds a *second*
  function asking the rotation question; it does not remove the first.
  That duplication is deliberate and temporary and B5 removes it.
- Every emitted `Placement`, `LayoutWarning` (kind, detail, sheet_index
  and **order**), `Mark`, `Signature` and `SheetPlan`.
- The four post-conditions at `deckle/core/layout.py:1042-1057`.

## 4. Tests

**Write no new behavioural tests.** The existing suite already asserts
everything this spec must preserve, and its value here is precisely that
it was written before the change. Adding tests alongside an extract-only
refactor dilutes that: a new test cannot fail on the unfixed tree,
because there is nothing wrong with it.

Three things to add, all structural:

### `tests/test_layout.py`

**`test_the_margin_helper_clamps_every_negative`**
`layout._margins(settings(gutter_pt=-50.0, margin_outer_pt=-10.0,
margin_top_pt=-10.0, margin_bottom_pt=-10.0)) == (0.0, 0.0, 0.0, 0.0)`,
and `layout._margins(settings(gutter_pt=54.0, margin_outer_pt=18.0,
margin_top_pt=36.0, margin_bottom_pt=9.0)) == (54.0, 18.0, 36.0, 9.0)`.
Pins the tuple's **order** — spine, outer, top, bottom — which three call
sites unpack positionally.
Unfixed: `AttributeError: module 'deckle.core.layout' has no attribute '_margins'`.

**`test_the_content_box_helper_reports_a_collapse_without_warning`**
`layout._box_within(settings(gutter_pt=400.0, margin_outer_pt=400.0,
margin_top_pt=500.0, margin_bottom_pt=500.0), None)` returns
`((0.0, 0.0, 0.0, 0.0), 612.0, 792.0, True)` — the bare sheet, zeroed
margins, flagged. And the same settings with ordinary margins returns
`collapsed is False`.
Unfixed: `AttributeError`.

**`test_the_three_box_users_agree_about_when_it_collapses`**
The property the extraction exists to guarantee, asserted over margin
combinations that straddle the exact boundary. On portrait LETTER with
`margin_outer_pt=18.0` and `margin_bottom_pt=18.0`, the box collapses at
`gutter_pt >= 594.0` (`612 - 594 - 18 == 0`) and at
`margin_top_pt >= 774.0` (`792 - 774 - 18 == 0`). Parametrise over
`gutter_pt` in `(0.0, 593.0, 594.0, 595.0)` and `margin_top_pt` in
`(0.0, 773.0, 774.0, 775.0)`, and for each assert that these three agree:

- `content_box_size(s) == s.paper`
- `content_box_rect_pt(s, is_recto=True) == (0.0, 0.0, 612.0, 792.0)`
- `_place_page(...)` appended a `clipped_by_page` warning whose detail
  contains `"exceed the paper size"`

**Filter on the detail, not the kind.** `clipped_by_page` is emitted for
two different reasons — margins consuming the cell
(`deckle/core/layout.py:414-423`) and content overflowing the box after
scaling (`:457-467`) — and at `gutter_pt=593.0` the box is 1×756 and the
*second* one fires while the first does not. A test that only counted
`kind` would pass on the wrong warning.

Measured on the unfixed tree: `gutter_pt=593.0` gives
`content_box_size == (1.0, 756.0)` and `content_box_rect_pt ==
(593.0, 18.0, 594.0, 774.0)`, no collapse; `594.0` and `595.0` give the
bare sheet from both and the collapse warning. Vertically, `773.0` gives
`(576.0, 1.0)`, and `774.0`/`775.0` collapse.

Unfixed: passes — the three already agree, because the arithmetic is
copied correctly. Write it anyway: it is the regression net for the
extraction, and those are the values where a `<` slipping in for a `<=`
shows.

### `tests/test_layout_saddle.py`

**`test_the_warning_order_across_a_folio_sheet_is_front_then_back`**
Impose four **792×612** pages — letter landscape, so every leaf is
landscape inside a 396×612 portrait cell and warns `mixed_orientation`
under the default `rotate` policy — as folio with
`sheets_per_signature=1`. That is one sheet, and `saddle_order(4)` is
`[3, 0, 1, 2]`, so the four warnings must name page indices
`3, 0, 1, 2` in that order: `front_a, front_b, back_a, back_b`.

```python
    order = [w.detail.split()[1]
             for w in plan.warnings if w.kind == "mixed_orientation"]
    assert order == ["3", "0", "1", "2"]
```

(`detail` is `f"page {slot.ref.page_index} of {slot.ref.path!r} is ..."`,
so `split()[1]` is the index.) Verified on the unfixed tree: it prints
`['3', '0', '1', '2']`.

**Do not use a 600×612 source.** 600 < 612, so it is portrait, nothing
rotates, and the warning list comes back empty — the test would pass on
nothing.

Unfixed: passes. It is the pin for step 9's "warning order is
load-bearing" note, and the one thing a `place_pair` rewrite can silently
transpose.

### Structural verification of the extraction

**`test_the_clamped_margin_arithmetic_appears_once`**
An AST test in the style of
`tests/test_layout_saddle.py::test_creep_references_are_isolated_to_creep_advisory`:
walk `deckle/core/layout.py`, count `ast.Call` nodes whose func is
`max` with a first argument that is the constant `0.0`, and assert they
all lie inside `_margins`'s line range.

```python
def test_the_clamped_margin_arithmetic_appears_once():
    """Three copies of `max(0.0, settings.margin_*)` is three chances to
    change two of them. B11, B12 and B29 are all that shape one layer up."""
    import ast, inspect
    import deckle.core.layout as layout_module

    tree = ast.parse(inspect.getsource(layout_module))
    margins_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_margins"
    )
    allowed = set(range(margins_fn.lineno, margins_fn.end_lineno + 1))

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "max"
        and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0.0
        and node.lineno not in allowed
    ]
    assert not offenders, f"margin clamping outside _margins at lines {offenders}"
```

Unfixed: `StopIteration` from the `next(...)` — `_margins` does not
exist. Once it does, the offenders list is
`[403, 404, 405, 406, 543, 544, 545, 546, 226, 227]` until steps 4, 5 and
7 land. Put it in `tests/test_layout.py`.

**`test_place_page_is_short_enough_to_read`** — **do not write this.**
A line-count assertion is a number somebody will raise rather than a
property. The AST test above asserts the thing that actually matters.

## 5. Acceptance

Every row below must pass **with no test file edited** except for the
three additions in §4.

| Check | Command |
|---|---|
| the margin helper exists | `grep -n "def _margins(settings: LayoutSettings)" deckle/core/layout.py` |
| the box helper exists | `grep -n "def _box_within(" deckle/core/layout.py` |
| the rotation seam exists | `grep -n "def _policy_rotation(" deckle/core/layout.py` |
| the clamping happens once | `test "$(grep -c 'max(0.0, settings\.' deckle/core/layout.py)" = 4` (**10** today — `grep -c` counts lines: 2 in `content_box_size`, 4 in `_place_page`, 4 in `content_box_rect_pt`. After, the only four are `_margins`') |
| the four calls became two | `test "$(grep -c '_place_page(' deckle/core/layout.py)" = 5` (7 today: the definition plus 6 calls; after, the definition plus 2 in `GutterShiftStrategy` plus 2 in `place_pair`) |
| `_place_page` got shorter | `.venv/bin/python -c "import ast, inspect, deckle.core.layout as m; t = ast.parse(inspect.getsource(m)); n = next(x for x in ast.walk(t) if isinstance(x, ast.FunctionDef) and x.name == '_place_page'); print(n.end_lineno - n.lineno + 1)"` — 157 today, must print under 130 |
| the new structural tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout.py tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k "margin_helper or box_helper or box_users_agree or clamped_margin or warning_order"` |
| **the placement pin has not moved** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py -q --no-header -p no:cacheprovider` |
| **the saddle-stitch ordering pin has not moved** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_schedule.py -q --no-header -p no:cacheprovider` |
| the spine-side precedence still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_spine_side_precedence.py -q --no-header -p no:cacheprovider` |
| the fold round trip still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider` |
| every property still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_imposition_properties.py tests/test_imposition_properties_settings.py tests/test_custom_signatures.py -q --no-header -p no:cacheprovider` |
| the core stays pure | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_core_purity.py -q --no-header -p no:cacheprovider` |
| **no export changed by a byte** | see the script below; it must print `identical: True` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

### The byte-for-byte check

Run this **before** the refactor to record the baseline, and again after.
`_plan_hash` is `export`'s own content hash of every placement, mark and
crop in a plan, so two equal hashes mean two identical documents.

```bash
.venv/bin/python - <<'PYEOF' > /tmp/m2-plan-hashes.txt
import itertools
from deckle.core.export import _plan_hash
from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

def pages(n, w, h):
    return [SourcePage(ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                                     width_pt=w, height_pt=h),
                       rotate_deg=0, skipped=False) for i in range(n)]

sizes = [(506.88, 672.0), (432.0, 648.0), (900.0, 600.0), (300.0, 900.0),
         (500.0, 500.0), (396.0, 612.0)]
papers = [(612.0, 792.0), (792.0, 612.0)]

for (w, h), paper, edge, slack, gutter, scheme, sps, mode, trim, policy in itertools.product(
    sizes, papers, ("left", "right"), ("gutter", "outer", "split"),
    (0.0, 18.0, 54.0), ("none", "folio"), (1, 4), ("end", "balanced"),
    (0.0, 18.0), ("rotate", "scale"),
):
    s = LayoutSettings(
        paper=paper, gutter_pt=gutter, binding_edge=edge, slack_to=slack,
        margin_outer_pt=18.0, margin_top_pt=18.0, margin_bottom_pt=9.0,
        fold_scheme=scheme, sheets_per_signature=sps, blank_mode=mode,
        trim_pt=trim, landscape_policy=policy, sewing_stations=3,
    )
    strategy = SaddleStitchStrategy() if scheme == "folio" else GutterShiftStrategy()
    for n in (1, 5, 17):
        plan = strategy.impose(pages(n, w, h), s)
        warnings = "|".join(f"{x.sheet_index}:{x.kind}" for x in plan.warnings)
        print(w, h, paper, edge, slack, gutter, scheme, sps, mode, trim, policy,
              n, _plan_hash(plan), warnings)
PYEOF
```

**20,736 lines**, covering 6 page shapes × 2 papers × 2 binding edges ×
3 slack targets × 3 gutters × 2 schemes × 2 signature sizes × 2 blank
modes × 2 trims × 2 policies × 3 page counts. It takes a couple of
minutes. Then:

```bash
diff /tmp/m2-plan-hashes-before.txt /tmp/m2-plan-hashes-after.txt \
  && echo "identical: True"
```

The trailing `warnings` column is why the plan hash alone is not enough:
`_plan_hash` does not include `SheetPlan.warnings`, and step 9's ordering
note is about exactly that.

## 6. Out of scope

- **B1, B5, B8** — §3.0's table says where each lands. In particular
  `_rotates_to_portrait` **stays** after this spec, duplicating
  `_policy_rotation`'s question against a different rectangle. That is
  B5's to remove, and removing it here would change folio output and
  invalidate the byte-for-byte check.
- **M1** — `LayoutPanel`'s control table. Same "a list was updated in one
  place and not the others" shape, different module.
- **`SaddleStitchStrategy.impose`'s marks block** (`:964-981`). It is
  another candidate for extraction and it is not duplicated, so it is not
  in the roadmap's M2 and is not here.
- **The four post-conditions** at `:1042-1057`. They stay `assert`
  statements; converting them is B35's territory.
- **`M8`** — `SheetPlan.sheets` and `warnings` being `list` on a frozen
  dataclass. Touching them would change `_plan_hash`'s inputs and the
  byte-for-byte check's meaning.
- **Renaming anything public.** `content_box_size`,
  `content_box_rect_pt`, `document_scale` and `actual_margins_pt` are
  named in `docs/specs/deckle-signatures-v2/sub-spec-7-cell-generalisation.md`
  and in four test modules.

## 7. decisions.md entry

```
## 2026-09-05 — Extracted the placement seams before touching the placements
- Symptom: `_place_page` was 157 lines holding four unrelated decisions, and `SaddleStitchStrategy.impose` was 216 with its four `_place_page` calls written out at ten identical lines each. The clamped-margin arithmetic -- four `max(0.0, ...)`, two subtractions and a collapse test -- was copied into `content_box_size`, `content_box_rect_pt` and `_place_page`, which differ only in what they do when the box collapses. Three copies of one rule is three chances to change two of them, which is the shape B11, B12 and B29 all have one layer up.
- Fix: `_margins(settings)`, `_box_within(settings, cell)` returning `(margins, box_w, box_h, collapsed)`, `_policy_rotation(src_w, src_h, settings, cell)`, and a `place_pair` closure inside the folio loop. Every extraction is character-for-character the same expression in a new place; no `Placement`, warning, mark or signature changed.
- Surfaces: Verified byte for byte over 20,736 imposed documents -- 6 page shapes x 2 papers x 2 binding edges x 3 slack targets x 3 gutters x 2 fold schemes x 2 signature sizes x 2 blank modes x 2 trims x 2 landscape policies x 3 page counts -- comparing `export._plan_hash` plus the warning list, which the hash does not cover. `GUTTER_SHIFT_PLACEMENT_PIN` and the hand-written saddle-stitch ordering table both stayed green with no edit.
- Watch: The roadmap said to do this *with* B1, B5 and B8. It was done before them instead, deliberately: the pins are the only evidence a refactor of this function did not change the paper, and they are worth exactly as much as their ability to stay green -- which requires that nothing in the same commit is *supposed* to move them. A red pin in a mixed commit is ambiguous, and the correct response to an ambiguous pin is to read a 157-line diff.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **`tests/test_cell_geometry.py::GUTTER_SHIFT_PLACEMENT_PIN` is a
  regeneration-is-an-escalation pin.** Its own docstring: "If it goes
  red, STOP and surface; do not update the numbers." For an extract-only
  refactor a red pin is unambiguously a bug in the extraction.
- **`_place_page` is called positionally by three test modules.**
  `tests/test_cell_geometry.py:266, 299-302`,
  `tests/test_spine_side_precedence.py:153-175` and
  `tests/test_layout_saddle.py:456-460` all pass six positionals. Do not
  reorder or insert a parameter.
- **Warning order is part of the output.** `plan.warnings` is a plain
  list; `tests/test_layout_saddle.py:167-175` counts them,
  `tests/test_layout.py:260` filters by kind, and the CLI prints them in
  order. Step 9's `front` before `back`, and left-to-right within each
  tuple, reproduces the current order exactly. The byte-for-byte script
  in §5 carries a warning column for this reason.
- **`_box_within` returns zeroed margins on collapse, and that is
  load-bearing in `_place_page`.** The original set
  `gutter = outer = top = bottom = 0.0` *after* warning and then used
  `gutter` in the `slack_to` arithmetic 50 lines later
  (`deckle/core/layout.py:475-481`). If the helper returned the
  *unclamped* margins alongside `collapsed=True`, that arithmetic would
  silently change and the pin's degenerate row would move.
- **`_creep_advisory` owns `paper_thickness_pt` exclusively**, enforced by
  an AST walk in `tests/test_layout_saddle.py:415-439` over the whole
  module. Nothing here touches it; do not introduce a reference while
  moving code around.
- **`deckle/core` must not import Qt** — `tests/test_core_purity.py`.
- **`tests/test_docs_coverage.py`** wants a `docs/api/` page per module.
  No new module here.
- **The `place_pair` closure must be defined inside the per-sheet loop,**
  after `cell_a`/`cell_b`/`spine_a`/`spine_b` are assigned. Defining it
  once outside also happens to work — Python closures capture the
  variable, not the value — but it works for a reason the next editor
  will not check, and moving one assignment out of the loop would then
  break it silently.
- **Run the byte-for-byte script before you start.** It takes a couple
  of minutes and it is the entire value of doing M2 as its own commit.
  Verified on the unfixed tree: it exits 0 and writes 20,736 lines.
