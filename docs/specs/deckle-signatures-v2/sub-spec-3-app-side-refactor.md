---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
contracts: "./contracts.yaml"
sub_spec_id: SS-03
sub_spec_number: 3
title: "The Side refactor in deckle.app — per-page clipping is a correctness fix"
depends_on: ["SS-01", "SS-02"]
date: 2026-08-04
---

# SS-03 — The `Side` refactor in `deckle.app`: per-page clipping is a correctness fix

## Context

**What this does:** carries the `Side` shape into the two app-layer consumers the unamended
design missed — evaluation finding C-2. `deckle/app/backend.py` must produce a raster
containing **every** page on a side, not one. `deckle/app/views/preview_view.py` must compute
clipping warnings **per page within a side**.

**One of these two is a genuine correctness fix, not a mechanical edit.** Computed per side,
clipping warnings **under-report on a 2-up sheet**: a naive port reads the side's first page
and stops, so a folio sheet whose *right-hand* leaf overflows produces no warning at all. The
user then sees a clean preview and prints a book with a clipped column of text on every verso.
That is a silent wrong-output bug, and it is exactly the class of defect `docs/decisions.md`
records twice — *`fixed_gutter` put the reserved gutter on the wrong side of the verso* ("the
existing parity tests all asserted `tx` on the gutter-left side, where both modes agree; a bug
living entirely on the mirror side survived them") and *Rebuilt the placement math on one rule
for both axes* ("a coordinate assertion on one edge of one page can pass while the opposite
edge is wrong"). **This sub-spec therefore ships a behavioural test that fails against a
per-side implementation**, not a note asking the implementer to be careful. The test in Step 1
places a compliant page first and the overflowing page **second**; a per-side implementation
returns `[]` and the test goes red.

**What is NOT in scope here.** REQ-042 — measuring `clipped_by_page` against the **cell**
rather than the sheet under folio — belongs to SS-08, which owns cell geometry. SS-03 measures
against the sheet, exactly as the MVP does, and simply does it *for every page*. Do not
anticipate the cell.

**Contracts this sub-spec consumes.** `Side` and `Sheet` (contracts 1 and 2, owner SS-01) and
`export._sides` / `_export_batched` (owner SS-02). SS-03 owns no contract of its own; it is a
consumer.

**Verified facts to build on, read from the tree rather than inferred:**

- `deckle/app/views/preview_view.py:107` — `for side_name, output_page in (("front",
  sheet.front), ("back", sheet.back))`. This is the single line the correctness fix lives at.
- `deckle/app/views/preview_view.py:62-81` — `_output_page_bbox(output_page)` **keeps its
  `OutputPage` signature**. It is already per-page and already correct, including the
  90°/270° footprint swap at lines 77-80. Do not change it.
- `deckle/app/views/preview_view.py:44-59` — `imageable_rect_pt` is **unchanged**.
  `imageable_area_pt` is `(left, top, right, bottom)` **margins**, not a rect. That convention
  is pinned in three places and `docs/decisions.md` records what a wrong reading costs (*
  `imageable_area_pt` is margins, not a rect* — a 3-inch margin computed from a 0.25-inch
  printer border, silently clamped by a spinbox into a plausible-looking number). Leave it
  alone.
- `deckle/app/views/preview_view.py:114-137` — the two warning branches. `clipped_by_page` and
  `clipped_by_imageable_area` keep their existing, distinct `detail` text, and the
  page-escapes-sheet branch still `continue`s so the two causes are never conflated. The only
  change is where the loop iterates.
- `deckle/app/backend.py:155-158` and `deckle/app/backend.py:168` — a **second copy** of
  `render.py`'s front-then-back page-index rule. It stays correct verbatim, because one `Side`
  still yields exactly one PDF page (SS-02 pins this). Preserve it as-is.
- `deckle/app/backend.py:165` — `export.export(plan, tmp_path, sheets=[sheet_index])`, the call
  site through which `backend.py` "paints every page on a side": composition is SS-02's
  `_export_batched`, and `backend.py`'s job is to rasterize the resulting PDF page faithfully.
  This is why the acceptance criterion says *"verified by asserting the placement count rather
  than by pixel diff"* — the honest assertion is over the exported artifact, in keeping with
  `deckle/core/render.py`'s module docstring: *a rasterized artifact cannot lie, because it is
  the thing that goes to the printer.*

**Be honest about the size of the `backend.py` change.** Its two reads of a side are
**presence** checks (`sheet.front is not None`), which SS-02's `Side` shape satisfies without
edit. `backend.py` therefore requires no logic change; what SS-03 adds is the **test coverage
that proves it**, plus a comment at lines 155-158 recording *why* a 2-up side did not break the
index arithmetic. Do not manufacture a change to `backend.py` to make the diff look larger —
`docs/decisions.md` records that `ThumbnailWorker` had zero test coverage and absorbed a
missing `import threading` silently through a fully green suite. Missing coverage is the real
defect here, and coverage is the real fix.

**Files (modify):**
- `deckle/app/backend.py`
- `deckle/app/views/preview_view.py`
- `tests/test_backend.py`
- `tests/test_preview_fidelity.py`

## Provides / Requires

**Provides:**

| Symbol | Module | Consumed by |
|---|---|---|
| `preview_view.clipping_warnings_for_sheet` (per-page within a side) | `deckle/app/views/preview_view.py` | SS-08 (extends the same loop to measure against the cell, REQ-042), SS-10, SS-12 |
| `preview_view.warnings_for_plan` (unchanged shape, per-page inputs) | `deckle/app/views/preview_view.py` | SS-10, SS-12 |
| `preview_view._output_page_bbox` (unchanged `OutputPage` signature) | `deckle/app/views/preview_view.py` | SS-08 |
| `backend._render_sheet_side` front-then-back index rule, unchanged | `deckle/app/backend.py` | SS-12 |

**Requires:**

| Symbol | Owner | Why |
|---|---|---|
| `Side` | SS-01 | `sheet.front.pages` is what both consumers now iterate |
| `Sheet` with `front`/`back: Side \| None` | SS-01 | the presence checks in `backend.py` and the loop in `preview_view.py` read it |
| `Side(pages=())` is impossible | SS-01 | a present side always has at least one page, so no empty-iteration branch is needed |
| `export._export_batched` places every page of a side | SS-02 | what makes "a two-page side rasterizes both pages" true |
| `render.render_sheet` front-then-back mapping | SS-02 | `backend.py:168` mirrors it |
| `LayoutWarning` kinds `clipped_by_page` / `clipped_by_imageable_area` | MVP SS-01 | unchanged text and unchanged meaning |

## Implementation Steps

### Step 1. Write the failing per-page clipping test

Add to `tests/test_preview_fidelity.py`, reusing the module's existing helpers — `_ref`
(line 46), `_placement` (line 50), `_output_page` (line 56) and the `LETTER` constant
(line 30). Add `Side` to the `deckle.core.models` import block (lines 18-27).

`test_second_page_of_a_two_page_side_is_reported_as_clipped`:

```python
def test_second_page_of_a_two_page_side_is_reported_as_clipped():
    # A folio-shaped side: two leaves, the FIRST comfortably inside the
    # sheet and the SECOND running off it. A per-SIDE implementation reads
    # pages[0], sees no overflow and returns [] -- the exact under-report
    # this test exists to catch.
    ok = _output_page(_ref("x.pdf", width_pt=300.0, height_pt=400.0), tx=18.0, ty=18.0)
    overflowing = _output_page(_ref("x.pdf", page_index=1, width_pt=612.0, height_pt=792.0),
                               tx=100.0, ty=100.0)
    sheet = Sheet(index=0, front=Side(pages=(ok, overflowing)), back=None)

    warnings = preview_view.clipping_warnings_for_sheet(sheet, LETTER, (18.0, 18.0, 18.0, 18.0))

    assert len(warnings) == 1
    assert warnings[0].kind == "clipped_by_page"
    assert warnings[0].sheet_index == 0
```

Add two companions in the same pass:

- `test_both_pages_of_a_side_can_be_reported` — both leaves overflow, so **two**
  `clipped_by_page` warnings come back. A per-side implementation returns at most one, so this
  catches an implementation that iterates but `break`s.
- `test_two_page_side_with_no_overflow_reports_nothing` — the negative control; both leaves fit
  inside the imageable area and `warnings == []`. Without it, a broken implementation that
  always warns would pass the two positives.

Chose values against the sheet, not a cell: SS-03 measures against `paper_pt`, exactly as the
MVP does.

### Step 2. Run them and watch them fail

```bash
python -m pytest tests/test_preview_fidelity.py -q -k "two_page_side or both_pages"
```

Expect `AttributeError: 'Side' object has no attribute 'is_filler'` from
`_output_page_bbox` — the pre-fix loop hands a `Side` where an `OutputPage` is expected. That
is the correct red, and it is also the reason a "mechanical" port that merely appeases the type
would silently ship the under-report.

### Step 3. Fix the loop in `preview_view.py`

`deckle/app/views/preview_view.py`, at line 107, replace the loop head with a nested
iteration. `_output_page_bbox`, `_escapes`, both warning bodies and their `detail` text are all
unchanged:

```python
for side_name, side in (("front", sheet.front), ("back", sheet.back)):
    if side is None:
        continue
    # Per PAGE, not per side. A folio side holds two leaves; evaluating
    # only the first under-reports whenever the second is the one that
    # overflows, which is silent wrong output rather than a visible error.
    for output_page in side.pages:
        bbox = _output_page_bbox(output_page)
        if bbox is None:
            continue
        ...  # the two existing branches, verbatim
```

Update the function's docstring (lines 95-101) to say the comparison is per placed page within
a side, keeping the existing paragraph about the two causes never being conflated.

Also update the module docstring's second paragraph (lines 12-20) where it says
"compares each placed output page's on-sheet bounding box" — that sentence is now literally
true rather than aspirationally true; make it explicit that a side may hold several pages.

Do **not** touch `imageable_rect_pt` (lines 44-59) or `_output_page_bbox` (lines 62-81).

### Step 4. Run to green

```bash
python -m pytest tests/test_preview_fidelity.py -q
```

The four existing single-page clipping tests (lines 103, 116, 129, 143) and the filler test
(line 152) must all still pass with their expected values untouched. Each of them constructs
`Sheet(index=…, front=_output_page(...), back=None)`, so wrap each in `Side(pages=(…,))` — nine
call sites, at lines 80, 81, 107, 120, 131, 132, 145, 153, 165 and 166. **Change no expected
value.** If one appears to need changing, the fix was not neutral for the 1-up case — stop and
escalate.

### Step 5. Write the failing backend placement-count test

Add to `tests/test_backend.py`. The module currently imports no PDF machinery, so add
`os`, `pikepdf`, and the `deckle.core.models` types it needs; follow `tests/test_export.py`'s
`_write_source_pdf` (lines 26-33) and `_content_bytes` (lines 36-42) helpers, which already
handle the documented pikepdf trap — `page.Contents` is an `Array`, so `read_bytes()` raises
until the streams are joined.

`test_render_sheet_side_places_every_page_of_a_two_page_side`:

- Build a two-page source PDF, then a hand-built plan with one `Sheet` whose `back` is a
  `Side` holding **two** `OutputPage`s at distinct `tx` values (`0.0` and `396.0` — the vault
  recipe's recorded landscape-letter cell origins), and whose `front` is a single-page `Side`.
- Call `backend_mod._render_sheet_side(plan, 0, "back", 72, rotate_backs=True,
  ignore_rotate=False)`. `rotate_backs=True` routes through `backend.py`'s **own** export call
  at line 165 rather than delegating to `render_sheet`, so the assertion is about
  `backend.py`'s code path.
- Spy on the exported artifact by monkeypatching `backend_mod.export.export` with a
  call-through that copies the produced file to `tmp_path` before `backend.py` deletes it, in
  the style of `tests/test_preview_fidelity.py:84-90`.
- Open the copy with `pikepdf`, and assert **two** `Do` operators in the coalesced content
  stream of the page, and two `/Fx*` XObject resources on it. Two placements, one PDF page.
- Assert the returned `RenderedPage` has non-zero width and height, so the raster genuinely
  came back.

`test_render_sheet_side_back_index_is_unchanged_by_a_two_page_side`:

- Same plan; assert the exported single-sheet PDF has exactly **two** pages (one per `Side`,
  not one per placed page), which is the property that keeps `backend.py:168`'s
  `page_index = 1 if has_front else 0` correct at any page count per side.

### Step 6. Run them and watch them fail, then make them pass

```bash
python -m pytest tests/test_backend.py -q -k "two_page_side or back_index"
```

If they are **already green**, that is the expected and correct outcome, because SS-02 made
`_export_batched` place every page of a side and `backend.py`'s index arithmetic never depended
on page count. Record that in the commit message rather than inventing a change: these tests
exist because the property was previously unverified, not because it was previously wrong.

The only edit `deckle/app/backend.py` needs is a comment at lines 155-158 stating the
invariant explicitly, so the next reader does not re-derive it:

```python
# One Side yields exactly ONE PDF page in the single-sheet export, however
# many leaves that Side holds -- see export._export_batched. That is what
# keeps this front-then-back index mapping correct under 2-up folio, and
# it mirrors the identical rule in deckle/core/render.py:95-102.
```

### Step 7. Full suite, lint, commit

```bash
python -m pytest tests/test_backend.py tests/test_preview_fidelity.py -q
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "fix(SS-03): clipping warnings are per page within a side"
```

## Interface Contracts

### preview_view.clipping_warnings_for_sheet
- Direction: SS-03 → SS-08, SS-10, SS-12
- Owner: SS-03 (signature owned by MVP SS-10, unchanged)
- Shape: `clipping_warnings_for_sheet(sheet: Sheet, paper_pt: tuple[float, float],
  imageable_area_pt: tuple[float, float, float, float]) -> list[LayoutWarning]`
- Invariant: evaluated **per page within each side**, front then back, in `side.pages` order.
  A side holding N pages can produce up to N warnings. `clipped_by_page` short-circuits
  `clipped_by_imageable_area` for the *same page* only — content already off the sheet is not
  also reported as outside the (sheet-relative) imageable area.
- Invariant: `clipped_by_page` and `clipped_by_imageable_area` keep distinct `detail` text,
  unchanged from the MVP.
- Note for SS-08: REQ-042 replaces the `paper_rect` comparison with the leaf's **cell** under
  `fold_scheme="folio"`. That is SS-08's change to make inside this same loop; SS-03 leaves the
  comparison against the sheet.

### preview_view._output_page_bbox
- Direction: SS-03 → SS-08
- Owner: MVP SS-10, **unchanged by SS-03**
- Shape: `_output_page_bbox(output_page: OutputPage) -> tuple[float, float, float, float] | None`
- Invariant: takes an `OutputPage`, never a `Side`. Returns `None` for a filler. Swaps
  width/height for a 90°/270° placement, mirroring `export._place_output_page`.

### preview_view.imageable_rect_pt
- Direction: unchanged
- Owner: MVP SS-10, **frozen convention**
- Shape: `imageable_area_pt` is `(left, top, right, bottom)` **margins** from the paper edges,
  not an `(x0, y0, x1, y1)` rect. Pinned in `PrinterProfile`,
  `QtPrintBackend._paint_rendered_page` and here. Reading it as a rect is a recorded defect.

### backend._render_sheet_side
- Direction: SS-03 → SS-12
- Owner: MVP SS-08, **unchanged by SS-03**
- Shape: `_render_sheet_side(plan, sheet_index, side, dpi, rotate_backs, ignore_rotate) -> RenderedPage`
- Invariant: one `Side` is one PDF page in the single-sheet export, at any page count per side,
  so `page_index = 1 if has_front else 0` holds under folio. Composition — placing every page
  of a side — belongs to `export._export_batched`, not here.

## Verification Commands

Build check:

```bash
python -c "import deckle.app.views.preview_view, deckle.app.backend" || (echo "FAIL: app modules not importable" && exit 1)
python -m ruff check deckle tests
```

Test check:

```bash
python -m pytest tests/test_backend.py tests/test_preview_fidelity.py -q
python -m pytest tests/test_core_purity.py -q
python -m pytest -q
```

Per-criterion acceptance check:

```bash
# REQ-013 -- clipping is computed per page within a side
python -m pytest tests/test_preview_fidelity.py -q -k second_page_of_a_two_page_side_is_reported_as_clipped
grep -q "for output_page in side.pages" deckle/app/views/preview_view.py || (echo "FAIL: clipping still evaluated per side" && exit 1)

# REQ-012 -- every page on a side reaches the raster
python -m pytest tests/test_backend.py -q -k two_page_side

# REQ-011 -- the preview path still routes through export with sheets=[sheet_index]
python -m pytest tests/test_preview_fidelity.py -q

# REQ-040 -- no Qt in deckle.core, anchored to imports
! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(PySide6|PyQt)" deckle/core/ || (echo "FAIL: Qt imported in deckle.core" && exit 1)

# The seam did not move
git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py || (echo "FAIL: manual-duplex seam changed in SS-03" && exit 1)
```

## Checks

Every negative check below was executed against the working tree at commit `b54194c` and
confirmed to exit 0.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | Clipping is computed per page within a side (REQ-013) | [STRUCTURAL] | `grep -q "for output_page in side.pages" deckle/app/views/preview_view.py \|\| (echo "FAIL: clipping still evaluated per side" && exit 1)` |
| 2 | The under-report regression test exists and passes (REQ-013) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k second_page_of_a_two_page_side_is_reported_as_clipped \|\| (echo "FAIL: per-page clipping under-reports on a 2-up side" && exit 1)` |
| 3 | Both leaves of a side can be reported (REQ-013) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k both_pages_of_a_side_can_be_reported \|\| (echo "FAIL: clipping loop breaks after the first warning" && exit 1)` |
| 4 | A clean two-page side reports nothing (negative control) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k two_page_side_with_no_overflow_reports_nothing \|\| (echo "FAIL: clipping over-reports" && exit 1)` |
| 5 | `_output_page_bbox` keeps its `OutputPage` signature | [STRUCTURAL] | `grep -q "def _output_page_bbox(output_page: OutputPage)" deckle/app/views/preview_view.py \|\| (echo "FAIL: _output_page_bbox signature changed -- it is already per-page and correct" && exit 1)` |
| 6 | `imageable_rect_pt` still reads margins, not a rect | [STRUCTURAL] | `grep -q "left, top, right, bottom = imageable_area_pt" deckle/app/views/preview_view.py \|\| (echo "FAIL: imageable_area_pt convention changed -- it is (left, top, right, bottom) margins" && exit 1)` |
| 7 | The two warning kinds keep distinct text | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k clipped_by_page_and_clipped_by_imageable_area_have_different_text \|\| (echo "FAIL: warning texts converged" && exit 1)` |
| 8 | Every page of a two-page side reaches the raster (REQ-012) | [MECHANICAL] | `python -m pytest tests/test_backend.py -q -k two_page_side \|\| (echo "FAIL: backend does not place every page of a side" && exit 1)` |
| 9 | One `Side` is still one PDF page (REQ-012, backend index rule) | [MECHANICAL] | `python -m pytest tests/test_backend.py -q -k back_index \|\| (echo "FAIL: front-then-back index mapping broke under 2-up" && exit 1)` |
| 10 | Backend and preview suites green | [MECHANICAL] | `python -m pytest tests/test_backend.py tests/test_preview_fidelity.py -q \|\| (echo "FAIL: app-side suites red" && exit 1)` |
| 11 | The preview path routes through export for the visible sheet (REQ-011) | [MECHANICAL] | `python -m pytest tests/test_preview_fidelity.py -q -k render_visible_sheet_routes_through_export_with_sheet_index \|\| (echo "FAIL: preview no longer routes through export" && exit 1)` |
| 12 | No Qt import in `deckle.core` (REQ-040) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 13 | Core purity test green (REQ-040) | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity violated" && exit 1)` |
| 14 | No modal dialog in `preview_view` (warnings are badges) | [MECHANICAL] | `! grep -n "QMessageBox" deckle/app/views/preview_view.py \|\| (echo "FAIL: preview surfaced a warning as a modal dialog" && exit 1)` |
| 15 | No empty `Side` constructed anywhere (SS-01 invariant) | [MECHANICAL] | `! grep -rnE "Side\(\s*(pages\s*=\s*)?\(\s*\)\s*\)" deckle/ tests/ \|\| (echo "FAIL: Side(pages=()) constructed -- an absent side is None" && exit 1)` |
| 16 | The manual-duplex seam is untouched (REQ-015) | [MECHANICAL] | `git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py \|\| (echo "FAIL: printing.py or profiles.py changed in SS-03" && exit 1)` |
| 17 | App modules import cleanly without a display | [MECHANICAL] | `python -c "import deckle.app.views.preview_view, deckle.app.backend" \|\| (echo "FAIL: app modules not importable headlessly" && exit 1)` |
| 18 | Suite has not shrunk (REQ-039) | [MECHANICAL] | `test $(python -m pytest -q --collect-only 2>/dev/null \| grep -c "::") -ge 237 \|\| (echo "FAIL: suite collected fewer than 237 tests" && exit 1)` |
| 19 | Full suite green (REQ-039) | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: suite red after the app-side refactor" && exit 1)` |
| 20 | Lint clean (REQ-039) | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff violations" && exit 1)` |

**Note on check 12 and the anchored grep:** the master spec's own *Edge Cases* section flags
this. `! grep -rn "PySide6" deckle/core/` is **wrong** — it matches the "must not import
PySide6" prose at `deckle/core/models.py:5` and `deckle/core/__init__.py:3` and fails on a
clean tree. The anchored form matches import statements only and was verified to exit 0 here.

**Note on check 14:** `preview_view.py` genuinely contains no `QMessageBox` today, and
`tests/test_preview_fidelity.py:178-180` already asserts it by source inspection. Adding a
modal to surface the new per-page warnings would break both. Warnings are per-sheet badges.

**Note on `ruff`:** `ruff` 0.15.13 is installed but is not on `PATH` under Git Bash here;
`python -m ruff check deckle tests` is the verified invocation.
