# B20 — Refuse an unknown sheet index instead of writing an empty PDF

**Roadmap item:** `docs/ROADMAP.md` B20
**Depends on:** —
**Blocks:** F6 (sheet-subset reprint from the GUI, which will pass user
selections straight into `export(sheets=...)`)
**Size:** S
**Decision needed first:** none. §3 chooses "refuse the whole selection,
naming every index the plan does not have" and names the rejected
alternatives.

---

## 1. Context

`export(plan, out_path, sheets=[...])` maps the requested indices through
`{s.index: s for s in plan.sheets}` and **drops** anything not present.
A selection that is entirely unknown selects nothing, composes nothing,
and writes a 0-page PDF — which then passes `_verify_output`, because the
expected page count is derived from the same empty selection. The check
that exists to catch "the file does not match the plan" agrees with
itself.

Separately, `render.render_sheet` calls `export.export_sheet_cached`
*before* it checks whether the sheet exists, so a stale index costs a
full export and permanently occupies a cache slot with an empty PDF.

Both verified:

```bash
.venv/bin/python - <<'PYEOF'
import os, tempfile, pikepdf
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core import export, render

d = tempfile.mkdtemp()
src = os.path.join(d, "src.pdf")
pdf = pikepdf.Pdf.new()
for _ in range(4):
    pdf.add_blank_page(page_size=(400.0, 600.0))
pdf.save(src); pdf.close()

pages = [SourcePage(ref=SourceRef(path=src, page_index=i, sha256="a" * 64,
                                  width_pt=400.0, height_pt=600.0),
                    rotate_deg=0, skipped=False) for i in range(4)]
s = LayoutSettings(paper=(612.0, 792.0), gutter_pt=18.0, binding_edge="left")
plan = GutterShiftStrategy().impose(pages, s)

out = os.path.join(d, "o.pdf")
export.export(plan, out, sheets=[99])
with pikepdf.open(out) as f:
    print("fully-unknown selection ->", len(f.pages), "page(s), and it verified")

out2 = os.path.join(d, "o2.pdf")
export.export(plan, out2, sheets=[0, 99, 1])
with pikepdf.open(out2) as f:
    print("mixed selection [0,99,1] ->", len(f.pages), "page(s); the 99 vanished")

rendered = render.render_sheet(plan, 99, "front", 36)
print("render_sheet(99) ->", rendered.width, "x", rendered.height)
print("cache entries after that:", len(export._cache))
print("cached path for sheet 99:",
      export._cache.get((99, export._plan_hash(plan))))
export.clear_sheet_cache()
PYEOF
```

Output:

```
fully-unknown selection -> 0 page(s), and it verified
mixed selection [0,99,1] -> 4 page(s); the 99 vanished
render_sheet(99) -> 0 x 0
cache entries after that: 1
cached path for sheet 99: /tmp/deckle_export_cache/tmpXXXXXXXX.pdf
```

**Why it matters to a person printing a book.** `--sheets` is the flag
the schedule itself tells users to reach for: *"Print sheet 0 on its own
first — `deckle export --sheets 0`"*
(`deckle/core/schedule.py:322-326`). It is also how a reprint of a
damaged sheet is done. Type `--sheets 12` for a document that has 12
sheets numbered 0-11 — an off-by-one anybody makes, because the schedule
prints sheet numbers and the paper does not — and Deckle reports success
and writes a file with nothing in it. Send that to a shop, or open it
in a viewer that shows a grey void, and the only clue is that the job did
not print.

`export`'s own docstring documents the behaviour as intentional:

> `:param sheets:` the sheet indices to export, in the order given, or
> `None` for the whole plan. **An index not present in the plan is skipped
> rather than raising.**

So this is a decision to reverse, not an oversight — and the reason to
reverse it is `_verify_output`, which was added later and which the
skipping silently defeats.

### The CLI already knows how to say this

`deckle/cli.py` has `_report_missing_sheets`, called from `_cmd_export`:

```bash
grep -n "_report_missing_sheets" -A 20 deckle/cli.py | head -40
```

It runs **before** `export` and returns 1 with a message. So
`deckle export --sheets 99` is already reported cleanly today — the hole
is in the library function, which the GUI (F6), `render_sheet`, and any
script calling `deckle.core.export` reach directly.

## 2. Current code

### `deckle/core/export.py:556-559` — the documented contract

```python
    :param sheets: the sheet indices to export, in the order given, or
        ``None`` for the whole plan. An index not present in the plan is
        skipped rather than raising.
```

### `deckle/core/export.py:589-593` — the selection

```python
    target_indices = (
        [s.index for s in plan.sheets] if sheets is None else list(sheets)
    )
    by_index = {s.index: s for s in plan.sheets}
    selected = [by_index[i] for i in target_indices if i in by_index]
```

`if i in by_index` is the drop.

### `deckle/core/export.py:617-621` — the check that agrees with itself

```python
        _verify_output(
            tmp_path,
            expected_pages=sum(len(_sides(sheet, side)) for sheet in selected),
            paper_pt=plan.paper_pt,
        )
```

`expected_pages` is computed from `selected`, the already-filtered list.
An empty `selected` expects 0 pages and gets 0.

### `deckle/core/export.py:698-742` — what `_verify_output` promises

```python
def _verify_output(path: str, expected_pages: int, paper_pt: tuple[float, float]) -> None:
    """Check the written file against what the plan said it would be.

    Every other check in this module runs on the ``SheetPlan``. Nothing had
    ever looked at the artifact, so a composition that dropped a page or
    sized one wrongly would be reported as a successful export and the
    first symptom would be paper coming out of a printer.
    ...
    - **One page per face.** Catches a face silently lost, and a pass that
      lost its one-to-one mapping onto the sheets being fed.
```

"A composition that dropped a page … would be reported as a successful
export" is exactly what happens; the drop is upstream of where the
expectation is taken.

### `deckle/core/render.py:228-260` — the cache-before-check

```python
    if cancel is not None and cancel.is_set():
        return _empty_rendered_page()

    by_index = {sheet.index: sheet for sheet in plan.sheets}
    sheet = by_index.get(sheet_index)
    has_front = sheet is not None and sheet.front is not None
    has_back = sheet is not None and sheet.back is not None

    # Route through the cache rather than exporting to a fresh temp file
    # every time. ...
    tmp_path = export.export_sheet_cached(plan, sheet_index)
    try:
        if cancel is not None and cancel.is_set():
            return _empty_rendered_page()

        # The single-sheet export contains only the sides that exist, in
        # front-then-back order -- see export._export_batched/_sides.
        if side == "front":
            if not has_front:
                return _empty_rendered_page()
```

`sheet` is looked up at `:232`, `has_front`/`has_back` are computed at
`:233-234`, and the `not has_front` check that returns a degenerate page
is at `:254` — **after** the export at `:246`.

### `deckle/core/export.py:1010-1056` — `export_sheet_cached`

```python
    key = (sheet_index, _plan_hash(plan))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    fd, out_path = tempfile.mkstemp(suffix=".pdf", dir=_cache_dir())
    os.close(fd)
    with _call_count_lock:
        _call_count += 1
    try:
        export(plan, out_path, sheets=[sheet_index])
    except BaseException as exc:
        log_exception(
            "cached_sheet_export_failed", exc, sheet_index=sheet_index, path=out_path
        )
        _safe_remove(out_path)
        raise
    _cache.put(key, out_path)
    return out_path
```

Note the `except BaseException` / `_safe_remove` / `raise`: once `export`
raises for an unknown index, this already cleans up correctly and
propagates. That is why `render_sheet` must check *before* calling it.

### Every call site of `export(..., sheets=...)` and `render_sheet`

`grep -rn "sheets=\[\|sheets=selection\|sheets=args.sheets\|export_sheet_cached\|render_sheet(" --include='*.py' .`
(excluding `.venv`):

| Site | Passes |
|---|---|
| `deckle/core/export.py:1048` | `sheets=[sheet_index]` from `export_sheet_cached` |
| `deckle/core/render.py:246` | `export_sheet_cached(plan, sheet_index)` — **changed** |
| `deckle/cli.py` `_cmd_export` | `args.sheets`, **already guarded** by `_report_missing_sheets` |
| `deckle/app/views/preview_view.py` | `render_sheet` on a background worker — `grep -n "render_sheet" deckle/app/views/preview_view.py` |
| `deckle/app/backend.py` | `render_sheet` for printing — `grep -n "render_sheet" deckle/app/backend.py` |
| `tests/test_export.py:206` | `export_fn(plan, subset_path, sheets=[6])` |
| `tests/test_export.py:1048` region, `tests/test_render.py`, `tests/test_cli_sheets.py` | subset and cache tests |

Run those greps and read every hit before editing: an index that is
currently silently dropped in the GUI becomes an exception, and each
caller has to be checked for whether it is inside a `try`.

### Existing tests

- `tests/test_export.py:200-229` —
  `test_export_sheets_subset_matches_full_export`, `sheets=[6]` on a
  16-page (8-sheet) plan. Valid; unaffected.
- `tests/test_export.py:97-135` — the cache tests, `sheet_index=0`.
- `tests/test_cli_sheets.py` — the CLI's `--sheets` handling, including
  `_report_missing_sheets`. Read it before touching anything:
  `grep -n "^def test" tests/test_cli_sheets.py`.
- `tests/test_render.py` — `render_sheet` tests. Check whether any passes
  an out-of-range index: `grep -n "render_sheet" tests/test_render.py`.
- **Nothing asserts the skip-silently behaviour.**
  `grep -rn "sheets=\[99\]\|sheets=\[999\]" tests/` returns nothing.

## 3. Change

### The chosen design

`export` raises `ValueError` when **any** requested index is absent from
the plan, naming every missing index and the range the plan does have.
All-or-nothing: a selection is a statement about which sheets to print,
and honouring three quarters of it produces a stack that is wrong in a
way nobody can see until it is collated.

Rejected: **skipping but raising only when the result is empty.** That
fixes the 0-page file and leaves `--sheets 0,99,1` writing two sheets
under a name the user believes holds three — the mixed case in §1, which
is the one that actually ruins a reprint.

Rejected: **warning via `LayoutWarning`.** `export` takes a plan, not a
warnings list, and has no channel to return one; the CLI prints warnings
from the *plan*, which this is not part of.

Rejected: **clamping to the valid range.** Guessing which sheet the user
meant is the failure mode `docs/decisions.md` records under
`binding_edge: "middle"`.

### The message

```
sheet(s) 12, 99 are not in this plan, which has sheets 0-11
```

and, for a plan with no sheets at all:

```
sheet(s) 0 are not in this plan, which has no sheets
```

Indices are reported **sorted and de-duplicated**, so
`--sheets 99,99,12` names each once. The range is expressed as
`min-max` when the plan's indices are contiguous from 0 (which every
shipped strategy produces) and as an explicit sorted list otherwise —
`SaddleStitchStrategy` asserts contiguity
(`deckle/core/layout.py:1043-1046`), so the list form is defensive.

`ValueError`, not a new exception class: it is a caller error like the
page-index check 280 lines above it
(`deckle/core/export.py:310-315`), which is also a `ValueError` naming
all three numbers the remedy needs. `ExportVerificationError` is
explicitly reserved for "a defect in Deckle"
(`deckle/core/export.py:680-690`) and this is not one.

### Numbered edits

**`deckle/core/export.py`**

1. **New helper**, immediately above `export` (i.e. before line 528):

   ```python
   def _select_sheets(plan: SheetPlan, sheets: Sequence[int] | None) -> list[Sheet]:
       """The sheets ``sheets`` names, in the order given.

       :param plan: the imposed plan.
       :param sheets: indices to select, or ``None`` for every sheet in
           plan order.
       :returns: the selected sheets.
       :raises ValueError: any index is not in the plan, naming every one
           that is missing and what the plan does have.

       **All or nothing.** This used to drop an unknown index silently, so
       a fully-unknown selection wrote a 0-page PDF that then *passed*
       ``_verify_output`` -- the expected page count was derived from the
       same filtered list, so the check agreed with itself. The mixed case
       was worse and quieter: ``--sheets 0,99,1`` wrote two sheets under a
       name the user believed held three, which is only visible once the
       stack is collated.
       """
       by_index = {sheet.index: sheet for sheet in plan.sheets}
       if sheets is None:
           return list(plan.sheets)
       requested = list(sheets)
       missing = sorted({i for i in requested if i not in by_index})
       if missing:
           known = sorted(by_index)
           if not known:
               have = "no sheets"
           elif known == list(range(known[0], known[-1] + 1)):
               have = f"sheets {known[0]}-{known[-1]}"
           else:
               have = "sheets " + ", ".join(str(i) for i in known)
           raise ValueError(
               f"sheet(s) {', '.join(str(i) for i in missing)} are not in "
               f"this plan, which has {have}"
           )
       return [by_index[i] for i in requested]
   ```

   `Sequence` and `Sheet` are already imported
   (`deckle/core/export.py:32, 39`).

2. **Replace `export`'s lines 589-593** with

   ```python
       selected = _select_sheets(plan, sheets)
   ```

   Note this also changes the `sheets is None` path from
   `[by_index[i] for i in [s.index for s in plan.sheets]]` to
   `list(plan.sheets)`. Those are the same list unless two sheets share an
   index, in which case the old code silently exported the *later* one
   twice and the new code exports both — which is the honest answer and is
   unreachable from either strategy.

3. **Replace `export`'s `:param sheets:` (lines 556-559)** with

   ```python
       :param sheets: the sheet indices to export, in the order given, or
           ``None`` for the whole plan. An index the plan does not have is
           refused -- see :func:`_select_sheets`.
   ```

4. **Add to `export`'s `:raises:` block** (after the `:raises OSError:`
   entry at line 577):

   ```python
       :raises ValueError: ``sheets`` names an index the plan does not
           have. Refused before the scratch file is created, so nothing is
           written.
   ```

   The raise happens before `_check_writable` and `tempfile.mkstemp`, so
   the "raises before any bytes are written" promise in the docstring's
   third paragraph continues to hold. **Order matters**: put
   `_select_sheets` before `_check_writable` at line 595.

**`deckle/core/render.py`**

5. **Move the existence check ahead of the export.** Replace
   `deckle/core/render.py:231-246`:

   ```python
       by_index = {sheet.index: sheet for sheet in plan.sheets}
       sheet = by_index.get(sheet_index)
       has_front = sheet is not None and sheet.front is not None
       has_back = sheet is not None and sheet.back is not None

       # Route through the cache rather than exporting to a fresh temp file
       # every time. The cache was built, bounded, tested -- and never called,
       # so scrubbing back and forth across a book re-exported every sheet on
       # every visit, and returning to a sheet cost exactly as much as seeing
       # it the first time.
       #
       # The cache owns the file it hands back, so nothing here deletes it;
       # `clear_sheet_cache` and the LRU eviction are what remove entries.
       # Its key includes the plan hash, so any layout change invalidates
       # rather than returning a stale sheet.
       tmp_path = export.export_sheet_cached(plan, sheet_index)
   ```

   with

   ```python
       by_index = {sheet.index: sheet for sheet in plan.sheets}
       sheet = by_index.get(sheet_index)
       has_front = sheet is not None and sheet.front is not None
       has_back = sheet is not None and sheet.back is not None

       # Asked *before* exporting, not after. A stale index -- the preview
       # still on sheet 40 of a document that just shrank to 20 -- used to
       # pay for a full export and permanently occupy a cache slot with an
       # empty PDF, and now `export` refuses the index outright, so the
       # check has to come first or a routine scrub raises.
       if sheet is None or not (has_front if side == "front" else has_back):
           return _empty_rendered_page()

       # Route through the cache rather than exporting to a fresh temp file
       # every time. The cache was built, bounded, tested -- and never called,
       # so scrubbing back and forth across a book re-exported every sheet on
       # every visit, and returning to a sheet cost exactly as much as seeing
       # it the first time.
       #
       # The cache owns the file it hands back, so nothing here deletes it;
       # `clear_sheet_cache` and the LRU eviction are what remove entries.
       # Its key includes the plan hash, so any layout change invalidates
       # rather than returning a stale sheet.
       tmp_path = export.export_sheet_cached(plan, sheet_index)
   ```

6. **Simplify what is now dead** in the same function
   (`deckle/core/render.py:250-260`): the `if not has_front` / `if not
   has_back` returns are unreachable after step 5. Replace

   ```python
           # The single-sheet export contains only the sides that exist, in
           # front-then-back order -- see export._export_batched/_sides.
           if side == "front":
               if not has_front:
                   return _empty_rendered_page()
               page_index = 0
           else:
               if not has_back:
                   return _empty_rendered_page()
               page_index = 1 if has_front else 0
   ```

   with

   ```python
           # The single-sheet export contains only the sides that exist, in
           # front-then-back order -- see export._export_batched/_sides.
           page_index = 0 if side == "front" else (1 if has_front else 0)
   ```

7. **`render_sheet`'s docstring**, add to the `:raises:` block after the
   `pypdfium2.PdfiumError` entry (line 226):

   ```python
       :raises ValueError: never for an unknown ``sheet_index`` -- that
           returns a degenerate page. ``export`` refuses one, so the check
           above it is what keeps a stale preview index cheap and quiet.
   ```

**`deckle/cli.py`**

8. **`_report_missing_sheets`' docstring (lines 336-350) cites the old
   behaviour and becomes false.** Replace its second paragraph

   ```python
       :func:`deckle.core.export.export` skips an unknown index rather than
       raising, which is right for a library and wrong for a command: asking
       for sheet 99 of a four-sheet book would write a PDF with nothing in it
       and print ``wrote proof.pdf``. A file that exists and is empty, from a
       command that reported success, is the worst available outcome.
   ```

   with

   ```python
       :func:`deckle.core.export.export` refuses an unknown index too, and
       has since B20 -- it used to skip one, so asking for sheet 99 of a
       four-sheet book wrote a PDF with nothing in it and printed ``wrote
       proof.pdf``. This stays in front of it because the two messages are
       for different readers: the library names indices and the range the
       plan holds, and a user who typed ``--sheets 99`` needs to be told
       about ``--sheets``. It also runs before the output path is touched,
       which the library's own refusal now does as well.
   ```

   **No code change** in `cli.py` — the function still runs first and
   still returns 1.

9. **`docs/api/`** — nothing to add.

### What does not change

- `deckle/cli.py`'s `_report_missing_sheets` keeps running first, so the
  CLI's message — which names the flag — is what a CLI user sees.
  `tests/test_cli_sheets.py:77`,
  `test_asking_for_a_sheet_the_document_does_not_have_is_an_error`,
  pins it and must stay green with no edit. Two guards is correct here:
  one reports in the user's vocabulary, one makes the library safe for
  every other caller.
- `_verify_output` is untouched. It stops agreeing with itself because
  the empty selection can no longer happen.
- The LRU cache, its bounds, and `clear_sheet_cache`.

## 4. Tests

### `tests/test_export.py`

Extend the `# --- BEHAVIORAL: sheet subset matches full export` section.

**`test_a_fully_unknown_selection_is_refused_rather_than_written`**
`_plan_from_source(tmp_path, 4)` (2 sheets), then
`pytest.raises(ValueError)` around `export_fn(plan, out, sheets=[99])`.
Assert `"99"` and `"0-1"` are in the message, and that `out` does not
exist afterwards.
Unfixed: no exception, and `pikepdf.open(out)` has 0 pages —
`DID NOT RAISE`.

**`test_a_partly_unknown_selection_is_refused_as_a_whole`**
The same plan, `sheets=[0, 99, 1]`. Assert `ValueError`, that the message
names `99` and not `0` or `1`, and that `out` does not exist.
Unfixed: writes a 4-page file (both faces of sheets 0 and 1) — the case
that ruins a reprint.
Unfixed failure: `DID NOT RAISE`.

**`test_a_refused_selection_leaves_no_scratch_file`**
Mirror of the existing
`test_a_failed_verification_leaves_no_scratch_file_behind`
(`tests/test_export.py:829-845`): after the raise, the only `.pdf` in
`tmp_path` is `src.pdf`.
Unfixed: the destination file exists, so the leftovers list is
`["o.pdf"]`.

**`test_an_unknown_selection_is_refused_before_the_output_is_touched`**
Write a valid 4-page export to `out` first, then call with
`sheets=[99]` and assert the `ValueError` **and** that `out` still has
its 4 pages. Same guarantee the module already gives for
`ExportVerificationError`.
Unfixed: `out` is replaced with a 0-page file — `assert 0 == 4`.

**`test_the_message_names_every_missing_index_once`**
`sheets=[99, 12, 99]`; assert the message contains `"12, 99"` and
`message.count("99") == 1`.
Unfixed: `DID NOT RAISE`.

**`test_a_selection_naming_every_sheet_is_still_the_whole_plan`**
`export_fn(plan, out, sheets=[0, 1])` writes 4 pages, and the streams
equal `export_fn(plan, out2)` with no `sheets`. Uses the existing
`_page_streams` helper (`tests/test_export.py:531-542`). Passes today and
after — the guard that step 2's `sheets is None` change did not move the
default path.

**`test_an_empty_selection_still_writes_an_empty_document`**
`export_fn(plan, out, sheets=[])` writes 0 pages and does **not** raise:
an empty list names no missing index, and "export nothing" is a request a
caller can legitimately make (F6's "no sheets selected" state).
Passes today; pinned so the fix does not over-reach into the one empty
case that is not a mistake.

### `tests/test_render.py`

**`test_a_stale_sheet_index_costs_nothing`**
Build a 2-sheet plan, call `export.clear_sheet_cache()`, record
`export._call_count`, call `render.render_sheet(plan, 99, "front", 36)`,
and assert: the result is a degenerate `0 × 0` page, `export._call_count`
is unchanged, and `len(export._cache) == 0`.
Unfixed: `_call_count` increments and the cache holds one entry —
`assert 1 == 0`.

**`test_a_stale_sheet_index_does_not_raise`**
The same call must return a degenerate page rather than propagating the
new `ValueError`. This is the test that pins step 5's ordering; without
it, B20's export half breaks the preview.
Unfixed: passes (it already returns a degenerate page). Keep it — it is
the regression net for the fix, not for the bug.

**`test_asking_for_an_absent_back_costs_nothing_either`**
Reuse the module's own `_one_sheet_plan(front_ref, None)`
(`tests/test_render.py:54`). `render_sheet(plan, 0, "back", 36)` returns
a degenerate page **and** leaves `export._call_count` unchanged.

The existing `test_render_sheet_back_side_missing_returns_empty`
(`tests/test_render.py:100-109`) already asserts the *result*; this one
asserts the *cost*, and they are different claims. Keep both.

Unfixed: it exports the sheet (for the front's sake) and then discards
it. `assert 1 == 0`.

**`test_a_real_sheet_still_renders`**
The control: `render_sheet(plan, 0, "front", 36)` has non-zero width and
`_call_count` increments by exactly 1. Passes today and after.

Every test that touches the cache must call `export.clear_sheet_cache()`
in a `finally` — it is module-level and its files live in
`/tmp/deckle_export_cache`.

### `tests/test_cli_sheets.py`

**No new test.** `test_asking_for_a_sheet_the_document_does_not_have_is_an_error`
(`tests/test_cli_sheets.py:77`) already covers it and must stay green
with no edit — `_report_missing_sheets` runs first, so a CLI user sees
the CLI's message (`error: no sheet 99 in this document -- it has 4
sheet(s), numbered 0 to 3`) and never the library's. If it goes red, the
library's refusal has been placed ahead of the CLI's, which is the wrong
way round.

## 5. Acceptance

| Check | Command |
|---|---|
| the silent drop is gone | `! grep -n "for i in target_indices if i in by_index" deckle/core/export.py` (matches `deckle/core/export.py:593` today) |
| the docstring no longer promises it | `! grep -n "skipped rather than raising" deckle/core/export.py` (matches `deckle/core/export.py:558` today) |
| the CLI's docstring no longer cites it | `! grep -n "skips an unknown index rather than" deckle/cli.py` (matches `deckle/cli.py:339-340` today) |
| the selection is one function | `test "$(grep -c 'def _select_sheets' deckle/core/export.py)" = 1` |
| it runs before anything is written | `.venv/bin/python -c "import inspect, deckle.core.export as m; s = inspect.getsource(m.export); assert s.index('_select_sheets') < s.index('_check_writable') < s.index('mkstemp'), 'the refusal must precede every write'"` |
| the preview checks before exporting | `.venv/bin/python -c "import inspect, deckle.core.render as m; s = inspect.getsource(m.render_sheet); assert s.index('if sheet is None or not') < s.index('export_sheet_cached'), 'the existence check must precede the export'"` |
| the repro from §1 now refuses | paste §1's fenced block; the first two `export.export` calls must raise `ValueError`, and `cache entries after that` must read `0` |
| the new export tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_export.py -q --no-header -p no:cacheprovider -k "unknown_selection or missing_index or empty_selection or whole_plan"` |
| the new render tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_render.py -q --no-header -p no:cacheprovider -k "stale or absent_back or still_renders"` |
| the CLI's own message is unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cli_sheets.py -q --no-header -p no:cacheprovider` |
| the GUI does not raise while scrubbing | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_surface.py tests/test_view_workers.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` the preview does not flash an error when a document shrinks | Open a 20-sheet document in the GUI, scrub the preview to the last sheet, remove pages until the plan has 5 sheets, and watch the preview: it must show an empty sheet, not a traceback dialog or a frozen last render. |

## 6. Out of scope

- **`deckle/cli.py`'s `_report_missing_sheets`.** It already works and it
  speaks the CLI's vocabulary. Do not delete it in favour of catching the
  new `ValueError`: the library's message names indices, the CLI's names
  the flag, and a user who typed `--sheets 12` needs the second one.
- **B2 and B1** — same module (`export.py`), different function
  (`_place_output_page`, not `export`). Mergeable in any order.
- **B32's `ink_bbox` cache key omitting dpi.** Same module
  (`render.py`), different function.
- **F6** — sheet-subset reprint from the GUI. This spec is what makes
  passing a user's selection straight into `export` safe; the dialog
  itself is F6's.
- **The `_LRUCache`'s bounds, `_MAX_RETIRED`, or `_cache_dir`'s disk
  budget.** Untouched. This spec removes one way an entry gets created,
  not the machinery.
- **A plan with duplicate `Sheet.index` values.** Step 2 changes what
  `sheets=None` does for one, from "export the last one twice" to
  "export both". Neither strategy produces one and no test covers it;
  noted so the change is not a surprise.

## 7. decisions.md entry

```
## 2026-09-05 — An export of nothing reported success, and verified
- Symptom: `export(sheets=...)` filtered its selection through `if i in by_index`, silently dropping any index the plan did not have. A fully-unknown selection wrote a 0-page PDF -- and it *passed* `_verify_output`, because `expected_pages` is summed over the same filtered list, so the one check that looks at the artifact agreed with itself. The mixed case was quieter and worse: `--sheets 0,99,1` wrote two sheets under a name the user believed held three. Separately, `render_sheet` called `export_sheet_cached` before checking the sheet existed, so a stale preview index paid for a full export and permanently occupied a cache slot with an empty PDF.
- Fix: `export._select_sheets` refuses the whole selection when any index is absent, with a `ValueError` naming every missing index and the range the plan has, raised before `_check_writable` and before the scratch file exists. `render_sheet` now asks whether the sheet and the requested face exist *before* exporting -- which it has to, since the export would otherwise raise on a routine scrub past the end of a shrunken document.
- Surfaces: The old behaviour was documented -- ":param sheets: ... An index not present in the plan is skipped rather than raising" -- so this reverses a decision rather than fixing an oversight. What changed under it is `_verify_output`, added later, whose whole purpose is that "a composition that dropped a page ... would be reported as a successful export". The skip is upstream of where the expectation is taken, so it defeated the check without touching it. `deckle/cli.py`'s `_report_missing_sheets` already guarded the CLI; the hole was in the library, which the GUI, the preview and any script reach directly.
- Watch: A check whose expected value is derived from the same filtered input as its actual value is not a check. `expected_pages=sum(len(_sides(sheet, side)) for sheet in selected)` reads as thorough and can only ever pass. When writing a verification, take the expectation from the *request*, not from the intermediate the code already built.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **Step 5 is not optional and must land in the same commit as step 2.**
  Without it, `render_sheet` propagates the new `ValueError` out of a
  background worker every time the preview is on a sheet index that no
  longer exists — which happens whenever a page is removed while the
  preview is near the end. Both `deckle/app/views/preview_view.py` and
  `deckle/app/backend.py` call `render_sheet`; check what each does with
  an exception before assuming it is caught.
- **`export_sheet_cached` already handles the raise correctly.** Its
  `except BaseException: _safe_remove(out_path); raise`
  (`deckle/core/export.py:1049-1054`) removes the placeholder temp file
  and re-raises. Do not add a second cleanup.
- **The empty list is not the empty selection.** `sheets=[]` names no
  missing index and must keep writing a 0-page document without raising;
  `sheets=[99]` must raise. `test_an_empty_selection_still_writes_an_empty_document`
  is the pin. Writing `if not selected: raise` instead of checking
  `missing` conflates them.
- **`/tmp/deckle_export_cache` persists between runs.** `export._cache`
  is a module-level LRU whose files live there, and `_cache_dir` evicts
  only by total size. Clear it (`rm -rf /tmp/deckle_export_cache`) before
  measuring `_call_count` by hand, and call
  `export.clear_sheet_cache()` in every test that touches it.
- **`export._call_count` is a module global** guarded by
  `_call_count_lock` (`deckle/core/export.py:985-986`). Read it, never
  reset it; tests compare before/after.
- **`tests/test_render.py:77-98`,
  `test_render_sheet_routes_through_export`, spies on
  `export_module.export` and asserts it is called exactly once with
  `sheets == [0]`.** Step 5 moves the existence check ahead of that call
  for an index the plan *does* have, so the spy still fires and the test
  stays green — but it is the first thing to check if it goes red, and it
  is why the check must be `if sheet is None or not (...)` rather than an
  unconditional early return.
- **`_verify_output` is not the guard here.** It runs on the scratch file
  and cannot see a selection that was never composed. Do not try to fix
  B20 inside it.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`).
