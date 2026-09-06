# B5 — Judge the landscape rotation against the cell in both passes

**Roadmap item:** `docs/ROADMAP.md` B5
**Depends on:** M2 if M2 is done first (both edit `_place_page`; see §6)
**Blocks:** —
**Size:** S
**Decision needed first:** none. §3 states the chosen design — **the cell
is the authority** — and names the rejected alternative.

---

## 1. Context

Imposition decides a page's rotation twice, in two functions, against two
different rectangles.

`document_scale` — the pass that computes the one scale the whole
document is reproduced at — asks `_rotates_to_portrait`, which judges
against `settings.paper`. Under folio the paper is *landscape* (that is
the point: it folds down the middle), so the answer is always "no
rotation", for every page.

`_place_page` — the pass that positions each leaf — judges against the
**cell**, and under folio each cell is half a landscape sheet and
therefore portrait. So it rotates.

The result is a page rotated to fill a cell, at a scale computed on the
assumption that it would not be.

Verified: four letter-landscape pages (792×612) imposed as folio onto
letter landscape.

```bash
.venv/bin/python - <<'PYEOF'
from deckle.core.layout import (SaddleStitchStrategy, document_scale,
                                cell_geometry, _rotates_to_portrait, _fitted_dims)
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

def pages(n, w, h):
    return [SourcePage(ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                                     width_pt=w, height_pt=h),
                       rotate_deg=0, skipped=False) for i in range(n)]

s = LayoutSettings(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                   fold_scheme="folio", sheets_per_signature=4)
ps = pages(4, 792.0, 612.0)
plan = SaddleStitchStrategy().impose(ps, s)
op = plan.sheets[0].front.pages[0]
cells = cell_geometry(s.paper)
print("emitted scale ", round(op.placement.scale_x, 4),
      " rotate", op.placement.rotate_deg)
print("_rotates_to_portrait (judges the PAPER):",
      _rotates_to_portrait(792.0, 612.0, s))
print("_fitted_dims                           :", _fitted_dims(ps[0], s))
print("scale the rotated page could have had  :",
      round(min(396.0 / 612.0, 612.0 / 792.0), 4))
print("warnings:", sorted({w.kind for w in plan.warnings}))
PYEOF
```

Output:

```
emitted scale  0.5  rotate 90
_rotates_to_portrait (judges the PAPER): False
_fitted_dims                           : (792.0, 612.0)
scale the rotated page could have had  : 0.6471
warnings: ['mixed_orientation']
```

The leaf is placed rotated, and it is placed at 0.50 when 0.6471 fits —
**29% smaller than it needed to be, on every page of the book.** The
scale pass fitted the unrotated 792×612 into the 396×612 cell
(`min(396/792, 612/612) = 0.5`); the placement pass then turned the page
so the binding dimension was 612 against 396, which would have allowed
0.6471.

**Why it matters to a person printing a book.** A landscape source — a
scanned atlas, a wide-format zine, anything typeset for a spread — bound
as folio comes out at two thirds of the size it should, on every leaf,
with no warning distinguishing that from "your source is simply large".
The margins are all honoured; the sheet looks correct; the type is small.

### One correction to the roadmap

The roadmap adds "plus a spurious `mixed_orientation` warning". The
warning is not spurious: the page genuinely is rotated, and the warning
correctly reports it. What is wrong is only the scale. The warning is
*surprising* from the scale pass's point of view — that pass concluded no
rotation was needed — but it describes what happened.

## 2. Current code

### `deckle/core/layout.py:292-302` — the scale pass's rotation test

```python
def _rotates_to_portrait(src_w: float, src_h: float, settings: LayoutSettings) -> bool:
    paper_w, paper_h = settings.paper
    return settings.landscape_policy == "rotate" and paper_h >= paper_w and src_w > src_h


def _fitted_dims(slot: SourcePage, settings: LayoutSettings) -> tuple[float, float]:
    """A page's upright dimensions after any landscape rotation."""
    src_w, src_h = _source_dims(slot, settings)
    if _rotates_to_portrait(src_w, src_h, settings):
        src_w, src_h = src_h, src_w
    return src_w, src_h
```

`paper_h >= paper_w` — the sheet. Note the function has no `cell`
parameter at all, while its caller has had one since SS-07.

### `deckle/core/layout.py:305-343` — `document_scale`, which already takes the cell

```python
def document_scale(
    pages: Sequence[SourcePage], settings: LayoutSettings, cell: Cell | None = None
) -> float:
    ...
    :param cell: the region pages are fitted into, or ``None`` for the
        whole sheet. Under folio every cell is identical, so any one of
        them gives the document-wide answer.
    ...
    """
    box_w, box_h = content_box_size(settings, cell)
    scales = []
    for slot in pages:
        if slot is None or slot.skipped:
            continue
        src_w, src_h = _fitted_dims(slot, settings)
        if src_w <= 0 or src_h <= 0:
            continue
        scales.append(min(box_w / src_w, box_h / src_h))
    return min(scales) if scales else 1.0
```

Line 334 passes `cell` to `content_box_size`. Line 339 does **not** pass
it to `_fitted_dims`. That one omission is the defect: half of the
calculation is cell-aware and half is not.

### `deckle/core/layout.py:375-397` — the placement pass's rotation test

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

The comment at `:380-382` states the rule the *other* function does not
follow: "this must be judged against the cell, not the sheet."

### `deckle/core/layout.py:924` — folio's one call to `document_scale`

```python
        scale = document_scale(active, settings, cell=cells[0])
```

The cell is already threaded here. It reaches `content_box_size` and
stops.

### `deckle/core/layout.py:660` — gutter shift's call

```python
        scale = document_scale(active, settings)
```

`cell=None`, i.e. the full sheet, so `cell_h >= cell_w` degenerates to
`paper_h >= paper_w` and this path's behaviour is unchanged by the fix.

### Every call site

`grep -rn "_rotates_to_portrait\|_fitted_dims\|document_scale" --include='*.py' .`
(excluding `.venv`):

| Site | What it is |
|---|---|
| `deckle/core/layout.py:292` | `_rotates_to_portrait` definition — **deleted** by this spec |
| `deckle/core/layout.py:300` | its only call, inside `_fitted_dims` — **changed** |
| `deckle/core/layout.py:297` | `_fitted_dims` definition — **signature changes** |
| `deckle/core/layout.py:339` | its only call, inside `document_scale` — **changed** |
| `deckle/core/layout.py:305` | `document_scale` definition — docstring only |
| `deckle/core/layout.py:660` | `GutterShiftStrategy.impose` — unchanged (`cell=None`) |
| `deckle/core/layout.py:924` | `SaddleStitchStrategy.impose` — unchanged (already passes `cells[0]`) |
| `tests/test_layout.py:308-314` | `test_uniform_scale_is_the_largest_that_fits_every_page` — calls `document_scale(pages, s)` |
| `tests/test_cell_geometry.py:262, 293` | `document_scale(pages, se, cell=full) == document_scale(pages, se)` |
| `tests/test_cell_geometry.py:312-339` | `test_document_scale_against_a_half_width_cell_is_smaller_and_still_single_valued` |
| `tests/test_layout_saddle.py:446-461` | `test_clipped_by_page_measured_against_the_cell` — calls `document_scale([page], s)` with no cell, deliberately |

`_rotates_to_portrait` and `_fitted_dims` are **private and have no test
of their own** and no caller outside `layout.py`. Both are free to change
shape.

### Existing tests that pin this behaviour

- `tests/test_cell_geometry.py:95-232` — `GUTTER_SHIFT_PLACEMENT_PIN`.
  The `"WIDE"` rows (900×600 on portrait letter, `rotate` policy) are
  exactly the case `_rotates_to_portrait` gets *right* today, because
  gutter shift's cell is the whole sheet. **Must not move.**
- `tests/test_layout.py:424-439` — `test_landscape_page_is_rotated_and_warned`
  and `test_rotated_landscape_still_honours_margins`, both gutter shift.
- `tests/test_layout_saddle.py:445-461` —
  `test_clipped_by_page_measured_against_the_cell` calls `_place_page`
  directly with `cell=cell_geometry(s.paper)[0]` and a scale computed
  against the full sheet, and asserts the *cell* warns while the sheet
  does not. Its source is 600×612 — landscape by a whisker — and its
  settings use the default `landscape_policy="rotate"`, so this spec
  changes what `document_scale([page], s)` returns for it. See §8.
- `tests/test_spec_residue.py:288-315` — `test_folio_never_rotates_a_leaf`
  uses 396×612 **portrait** sources, so no rotation is triggered on
  either path and it stays green.
- `tests/test_imposition_properties.py:252-277` —
  `test_folio_leaves_stay_inside_their_own_cell` uses 400×600 portrait
  sources. Green either way.

## 3. Change

### The chosen design

**A page's rotation is decided against the cell it is placed into, in
both passes.** `_fitted_dims` gains a `cell` parameter, `document_scale`
threads its own `cell` into it, and `_rotates_to_portrait` is deleted in
favour of a single predicate that both passes call.

Rejected: making `_place_page` judge against the paper instead. That
would make folio never rotate a landscape source at all — it would place
a 792×612 page unrotated into a 396×612 cell at scale 0.5 and call that
correct — and it directly contradicts the comment already standing at
`deckle/core/layout.py:380-382` ("Under folio each cell is portrait-shaped
even though the sheet itself is landscape, so this must be judged against
the cell, not the sheet"), which is the rule folio was designed to.

### The predicate

```
_policy_rotation(src_w, src_h, settings, cell) -> int

    90  when  settings.landscape_policy == "rotate"
              and (cell_h := cell[3] - cell[1]) >= (cell_w := cell[2] - cell[0])
              and src_w > src_h
    0   otherwise
```

`src_w`/`src_h` are the page's dimensions **after** cropping and after
the user's own rotation — i.e. the output of `_source_dims(slot,
settings)`. Returning an `int` rather than a `bool` because B1 composes
it: `(user_rotation + policy_rotation) % 360`.

For a full-sheet cell, `cell_h >= cell_w` is `paper_h >= paper_w`
verbatim, so **gutter shift is bit-identical** and the placement pin
cannot move.

### Numbered edits

All in `deckle/core/layout.py`.

1. **Replace `_rotates_to_portrait` (lines 292-294) entirely** with:

   ```python
   def _policy_rotation(
       src_w: float, src_h: float, settings: LayoutSettings, cell: Cell
   ) -> int:
       """The rotation ``landscape_policy`` adds for a page this shape, in ``cell``.

       ``90`` for landscape content in a portrait cell under
       ``landscape_policy="rotate"``, ``0`` otherwise.

       **Judged against the cell, never the sheet.** Under folio the sheet
       is landscape and each of its two cells is portrait, so the two
       questions have opposite answers -- and the scale pass used to ask
       about the paper while the placement pass asked about the cell. Four
       letter-landscape pages imposed as folio were then placed rotated at
       a scale computed for an unrotated page: 0.50 where 0.6471 fitted, on
       every leaf of the book.

       Returns degrees rather than a bool because ``_place_page`` composes
       this with the user's own ``SourcePage.rotate_deg``.

       :param src_w: the page's width after cropping and after the user's
           own rotation -- i.e. as ``_source_dims`` returns it.
       :param src_h: the same for height.
       :param settings: read for ``landscape_policy`` only.
       :param cell: the region the leaf is placed into. Pass
           ``_full_sheet_cell(settings.paper)`` for the whole sheet.
       :returns: ``90`` or ``0``.
       """
       if settings.landscape_policy != "rotate":
           return 0
       cx0, cy0, cx1, cy1 = cell
       cell_is_portrait = (cy1 - cy0) >= (cx1 - cx0)
       return 90 if cell_is_portrait and src_w > src_h else 0
   ```

2. **Replace `_fitted_dims` (lines 297-302)** with:

   ```python
   def _fitted_dims(
       slot: SourcePage, settings: LayoutSettings, cell: Cell | None = None
   ) -> tuple[float, float]:
       """A page's upright dimensions after any landscape rotation, in ``cell``.

       ``cell`` defaults to the whole sheet, matching ``document_scale``'s
       own default. It is not optional in spirit: this and ``_place_page``
       must agree about whether a page rotates, and they disagreed for as
       long as one of them read the paper.
       """
       src_w, src_h = _source_dims(slot, settings)
       if cell is None:
           cell = _full_sheet_cell(settings.paper)
       if _policy_rotation(src_w, src_h, settings, cell) == 90:
           src_w, src_h = src_h, src_w
       return src_w, src_h
   ```

3. **`deckle/core/layout.py:339`**, in `document_scale`, replace

   ```python
           src_w, src_h = _fitted_dims(slot, settings)
   ```

   with

   ```python
           src_w, src_h = _fitted_dims(slot, settings, cell)
   ```

   `cell` at that point is still the caller's argument (possibly `None`),
   which `_fitted_dims` resolves to the full sheet — the same resolution
   `content_box_size` performs at line 334. Do **not** hoist a
   `cell = cell or _full_sheet_cell(...)` above line 334: `content_box_size`
   already handles `None` and duplicating the default is how the two got
   out of step in the first place.

4. **`deckle/core/layout.py:383-385`**, in `_place_page`, replace

   ```python
       cell_is_portrait = cell_h >= cell_w
       page_is_landscape = src_w > src_h
       if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
   ```

   with

   ```python
       if _policy_rotation(src_w, src_h, settings, cell) == 90:
   ```

   Leave the comment above it, the `rotate_deg` assignment, the dimension
   swap and the warning exactly as they are. (B1 changes the assignment
   from `= 90` to `= (rotate_deg + 90) % 360`; that is B1's edit, not
   this one.)

5. **`deckle/core/layout.py:326-330`**, `document_scale`'s `:param cell:`
   entry, replace

   ```python
       :param cell: the region pages are fitted into, or ``None`` for the
           whole sheet. Under folio every cell is identical, so any one of
           them gives the document-wide answer.
   ```

   with

   ```python
       :param cell: the region pages are fitted into, or ``None`` for the
           whole sheet. Under folio every cell is identical, so any one of
           them gives the document-wide answer. It decides **both** halves
           of the calculation -- the content box and whether
           ``landscape_policy`` rotates the page -- because a scale fitted
           to one orientation and a placement made in the other is how a
           folio landscape source came out 29% small.
   ```

6. **`docs/api/`** — nothing to add.

### What changes, numerically

For the §1 repro, after the fix:

- `_fitted_dims(ps[0], s, cells[0])` returns `(612.0, 792.0)` (swapped).
- `document_scale(active, s, cell=cells[0])` returns
  `min(396/612, 612/792) = 0.6471`.
- The emitted placement is `scale_x == 0.6471`, `rotate_deg == 90`, and
  the `mixed_orientation` warning still fires.

Gutter shift, and any folio document whose sources are portrait, produce
byte-identical output.

## 4. Tests

Write these first. All fail on the unfixed tree.

### `tests/test_layout_saddle.py`

Add a `# ----- landscape sources under folio` section. The module's
`make_pages(n, w, h)` helper already takes explicit dimensions
(`tests/test_layout_saddle.py:37`).

**`test_a_landscape_source_is_scaled_for_the_cell_it_is_rotated_into`**
`impose(make_pages(4, w=792.0, h=612.0), settings(gutter_pt=0.0, margin_outer_pt=0.0, margin_top_pt=0.0, margin_bottom_pt=0.0))`.
Assert the first placed leaf has `rotate_deg == 90` **and**
`scale_x == pytest.approx(min(396.0 / 612.0, 612.0 / 792.0))`, i.e.
0.6471. Derive the expected value from the cell's own dimensions in the
test body rather than writing `0.6471`, so the test says *why* that
number.
Unfixed: `assert 0.5 == approx(0.6471…)`.

**`test_the_scale_pass_and_the_placement_pass_agree_about_rotation`**
The invariant behind the bug, stated directly. For every leaf in
`impose(make_pages(4, w=792.0, h=612.0), settings())`: if
`placement.rotate_deg % 180 == 90`, then the *scaled* footprint must fit
the cell's content box on both axes — `612 * scale <= cell_box_w + 1e-6`
and `792 * scale <= cell_box_h + 1e-6` — and it must fit **snugly**, i.e.
one of the two must be within 1e-6 of the box. A scale computed for the
other orientation fits, but never snugly.
Unfixed: at scale 0.5 the rotated footprint is 306×396 in a 396×612 box —
neither dimension binds. `assert 306.0 == approx(396.0) or 396.0 == approx(612.0)`
fails.

**`test_a_portrait_source_under_folio_is_unaffected`**
`impose(make_pages(8), settings())` (the module default, 396×612
portrait). Assert no `mixed_orientation` warning, every
`placement.rotate_deg == 0`, and one distinct scale. Passes today and
must keep passing — the guard that the fix does not start rotating
things.

**`test_the_landscape_warning_still_fires_once_per_leaf`**
`impose(make_pages(4, w=792.0, h=612.0), settings())`; assert exactly
four `mixed_orientation` warnings — one per placed leaf. Passes today;
pinned so the fix does not silence a true warning while changing the
scale.

### `tests/test_cell_geometry.py`

**`test_a_half_width_cell_decides_the_rotation_as_well_as_the_box`**
Sibling of the existing
`test_document_scale_against_a_half_width_cell_is_smaller_and_still_single_valued`
(`tests/test_cell_geometry.py:312`), and the case that isolates the fix
from `_place_page` entirely. Use
`se = settings(paper=LETTER_LANDSCAPE, gutter_pt=0.0)` — both constants
already exist at `:43` and `:64` — `pages = make_pages(4, size=WIDE)`
(900×600, `:47`), and `half_cell = (0.0, 0.0, 396.0, 612.0)`. The content
boxes are then `(792, 612)` and `(396, 612)`; assert both with
`content_box_size` first, so the numbers below say why. Then assert:

- `document_scale(pages, se) == pytest.approx(min(792 / 900, 612 / 600))`
  — `0.88`. The full-sheet cell is landscape, so nothing rotates.
- `document_scale(pages, se, cell=half_cell) == pytest.approx(min(396 / 600, 612 / 900))`
  — `0.66`. The half cell is portrait, so the page rotates and the
  scale is fitted to the *turned* dimensions.

Unfixed: the second returns `min(396 / 900, 612 / 600) == 0.44`, the
unrotated fit. `assert 0.44 == approx(0.66)`.

**`test_a_full_sheet_cell_still_changes_nothing`** — not a new test. The
existing parametrised
`test_full_sheet_cell_is_identical_to_omitting_the_cell`
(`tests/test_cell_geometry.py:272-306`) already covers all four
placement functions over `WIDE` and both binding edges, and must stay
green untouched.

### `tests/test_layout.py`

**`test_gutter_shift_rotation_is_unchanged_by_the_cell_rule`** — not a
new test. `test_landscape_page_is_rotated_and_warned`
(`tests/test_layout.py:424`) and the placement pin already cover it.

## 5. Acceptance

| Check | Command |
|---|---|
| the paper-judging predicate is gone | `! grep -rn "_rotates_to_portrait" deckle/ tests/` (matches `deckle/core/layout.py:292,300` today) |
| nobody judges orientation off `settings.paper` any more | `! grep -n "paper_h >= paper_w" deckle/core/layout.py` (matches `deckle/core/layout.py:294` today; note `deckle/core/layout.py:884` uses `paper_h >= paper_w` for the *sheet orientation warning*, which is a genuinely paper-level question and must survive — it is spelled `if paper_h >= paper_w:` there, so grep the exact `return`-side form: `! grep -n "and paper_h >= paper_w" deckle/core/layout.py`) |
| there is one rotation predicate | `test "$(grep -c 'def _policy_rotation' deckle/core/layout.py)" = 1` |
| both passes call it | `test "$(grep -c '_policy_rotation(' deckle/core/layout.py)" = 3` (definition + `_fitted_dims` + `_place_page`) |
| the cell reaches `_fitted_dims` | `grep -n "_fitted_dims(slot, settings, cell)" deckle/core/layout.py` |
| the repro from §1 now agrees | paste §1's fenced block; `emitted scale` must read `0.6471` |
| the new saddle tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k "landscape or rotation"` |
| the new cell test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py -q --no-header -p no:cacheprovider -k half_width` |
| **the placement pin has not moved** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py -q --no-header -p no:cacheprovider` |
| gutter shift is unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout.py tests/test_golden_pinebox.py -q --no-header -p no:cacheprovider` |
| the properties still hold | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_imposition_properties.py tests/test_imposition_properties_settings.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

- **B1** — composing the user's own rotation into `Placement.rotate_deg`.
  It edits the same three lines of `_place_page` that step 4 touches.
  Land one, then the other; §8 says how they compose.
- **B19** — whether `scale` and `letterbox` should differ. This spec
  reads `landscape_policy` only to test `== "rotate"`, exactly as today.
  If B19's collapse branch is taken, `_policy_rotation` is where the
  enum is read and nowhere else, which makes B19 smaller.
- **M2** — the extraction of `_margins` and `_place_pair`. `_policy_rotation`
  is a natural part of that refactor and M2's step list includes it as an
  extract-only step (no behaviour change); this spec is the behaviour
  change. If M2 lands first, steps 1 and 4 here are already done and only
  steps 2, 3 and 5 remain.
- **The uniform-scale rule.** One scale for the whole document is not
  negotiable here (`docs/decisions.md`, *Uniform document-wide scale*).
  This spec changes *which* scale, never *how many*.
- **Folio on portrait paper.** `SaddleStitchStrategy` warns
  (`sheet_orientation`) and proceeds; after this fix its cells are
  306×792, still portrait, so nothing changes there.

## 7. decisions.md entry

```
## 2026-09-05 — Scale and placement asked about different rectangles
- Symptom: `_rotates_to_portrait` judged a page's landscape rotation against `settings.paper`; `_place_page` judged it against the *cell*. Under folio the sheet is landscape and each cell is portrait, so the two answers were always opposite. Measured on four letter-landscape pages imposed as folio onto letter landscape: the leaves were placed rotated 90 at scale 0.50, where the rotated fit was 0.6471 -- 29% small, on every page, with all four margins honoured and the sheet looking entirely correct.
- Fix: One predicate, `_policy_rotation(src_w, src_h, settings, cell)`, called by both passes. `_fitted_dims` gained the `cell` parameter and `document_scale` threads its own into it -- half that calculation (`content_box_size`) was already cell-aware and half was not, which is exactly the seam the disagreement lived in. `_rotates_to_portrait` is deleted.
- Surfaces: For a full-sheet cell `cell_h >= cell_w` is `paper_h >= paper_w` verbatim, so gutter shift is bit-identical and `GUTTER_SHIFT_PLACEMENT_PIN` -- including its two `rotate_deg == 90` rows -- did not move.
- Watch: The comment stating the correct rule was already in the file, four lines above the code that followed it: "Under folio each cell is portrait-shaped even though the sheet itself is landscape, so this must be judged against the cell, not the sheet." It was written for `_place_page` and nobody carried it to the other function that answers the same question. A rule worth a comment is a rule worth a shared function.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **`tests/test_cell_geometry.py::GUTTER_SHIFT_PLACEMENT_PIN` is a
  regeneration-is-an-escalation pin** whose own docstring says "If it
  goes red, STOP and surface; do not update the numbers." A correct
  implementation of this spec leaves it green, because gutter shift's
  cell *is* the sheet. If it goes red you have changed the default
  resolution somewhere — check that `_fitted_dims` still resolves
  `cell=None` to `_full_sheet_cell(settings.paper)` and that
  `document_scale` still passes its raw `cell` through.
- **`tests/test_layout_saddle.py::test_clipped_by_page_measured_against_the_cell`
  (`:445-461`) is the one existing test this fix moves numbers under.**
  It builds a 600×612 page — landscape by 12pt — with the default
  `landscape_policy="rotate"`, calls `document_scale([page], s)` with
  **no cell** (deliberately, "fits the FULL sheet, not the half-cell")
  and then `_place_page` with the half cell. After this spec the no-cell
  `document_scale` still judges against the full landscape sheet, so it
  still returns the unrotated fit and the test's premise survives. Verify
  it, do not assume it: run
  `pytest tests/test_layout_saddle.py -k clipped_by_page` explicitly and,
  if it goes red, fix the *test's* scale argument rather than the
  predicate — the test's whole point is a deliberate mismatch.
- **`SaddleStitchStrategy` asserts four invariants after imposing**
  (`deckle/core/layout.py:1042-1057`). None involves scale, so this fix
  cannot trip them — but they are `assert` statements and vanish under
  `python -O` (B35).
- **`_creep_advisory` owns `paper_thickness_pt` exclusively**, enforced by
  an AST walk in `tests/test_layout_saddle.py:415-439` that flags *any*
  `Name` or `Attribute` node with that identifier outside the function.
  Nothing here touches it; do not introduce one.
- **`deckle/core` must not import Qt** — `tests/test_core_purity.py`.
- **`Cell` is `tuple[float, float, float, float]` as `(x0, y0, x1, y1)`**
  (`deckle/core/layout.py:40-46`), not `(x, y, w, h)`. `_policy_rotation`
  must subtract, not read indices 2 and 3 as extents.
- **The `sheet_orientation` warning at `deckle/core/layout.py:884` also
  tests `paper_h >= paper_w`** and is *correctly* about the paper — folio
  wants a landscape sheet. Do not fold it into `_policy_rotation`.
