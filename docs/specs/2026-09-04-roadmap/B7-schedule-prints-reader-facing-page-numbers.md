# B7 — Number the schedule by reading position, not by file index

**Roadmap item:** `docs/ROADMAP.md` B7
**Depends on:** —
**Blocks:** F10 (the "read the book back" line, which needs the same
numbering)
**Size:** S
**Decision needed first:** none. §3.1 defines "reader-facing page number"
precisely and says why blanks are counted.

---

## 1. Context

`SheetInstruction.front_pages` and `.back_pages` are documented as
"reader-facing page numbers … as a reader counts them"
(`deckle/core/schedule.py:40-42, 134-140`). They are computed as
`source_ref.page_index + 1` — the position of the page **inside the file
it came from**. Those two coincide only when the document is exactly one
source, imported whole, with nothing skipped and nothing inserted.

Three ways they diverge, all verified:

```bash
.venv/bin/python - <<'PYEOF'
from deckle.core.layout import SaddleStitchStrategy
from deckle.core.schedule import build_schedule
from deckle.core.signatures import fold_reading_order
from deckle.core.models import (LayoutSettings, SourcePage, SourceRef,
                                BLANK_SOURCE_PATH)

def pg(path, i, skipped=False):
    return SourcePage(
        ref=SourceRef(path=path, page_index=i, sha256="a" * 64,
                      width_pt=396.0, height_pt=612.0),
        rotate_deg=0, skipped=skipped)

s = LayoutSettings(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                   fold_scheme="folio", sheets_per_signature=1)

def show(label, ps):
    plan = SaddleStitchStrategy().impose(ps, s)
    sch = build_schedule(plan, s)
    rows = [(x.front_pages, x.back_pages)
            for sig in sch.signatures for x in sig.sheets]
    print(f"{label:16} {rows}   reading order {fold_reading_order(plan)}")

show("two sources", [pg("a.pdf", 0), pg("a.pdf", 1),
                     pg("b.pdf", 0), pg("b.pdf", 1)])
show("one skipped", [pg("a.pdf", i, skipped=(i == 1)) for i in range(5)])
show("inserted blank", [
    pg("a.pdf", 0),
    SourcePage(ref=SourceRef(path=BLANK_SOURCE_PATH, page_index=-1, sha256="",
                             width_pt=396.0, height_pt=612.0),
               rotate_deg=0, skipped=False),
    pg("a.pdf", 1), pg("a.pdf", 2)])
PYEOF
```

Output:

```
two sources      [((2, 1), (2, 1))]   reading order [3, 0, 1, 2]
one skipped      [((5, 1), (3, 4))]   reading order [3, 0, 1, 2]
inserted blank   [((3, 1), (None, 2))]   reading order [3, 0, 1, 2]
```

Every one of those is a four-page book, so the only correct answers are
`((4, 1), (2, 3))` and — for the inserted blank, which sits at reading
position 2 — `((4, 1), (None, 3))`.

What the binder is actually told:

- **Two sources.** *"front: 2 1 / back: 2 1"*. Pages 1 and 2 each appear
  twice and pages 3 and 4 do not exist. The `SIGNATURE 1 (pages 1-2)`
  header is wrong too, and
  `test_every_page_appears_exactly_once_across_the_schedule`
  (`tests/test_schedule.py:307`) would fail on this input if it were
  parametrised over more than one source.
- **One page skipped.** *"front: 5 1 / back: 3 4"* for a book with four
  leaves. There is no page 5.
- **An inserted blank.** *"front: 3 1 / back: blank 2"*. The blank is
  reported, but the pages around it are numbered as though it were not
  there, so nothing in the schedule adds up.

**Why it matters to a person printing a book.** The schedule is what
somebody holds at a bench, hands covered in PVA, while gathering
sixty-seven sheets in order. Its whole purpose is to be checkable against
the paper: "16 and 1 on the outside, 8 and 9 in the middle" is the check.
When the numbers are file indices, the check silently stops working —
the numbers still look like page numbers, they are still monotonic, and
they are still four per sheet. A miscollated signature is only visible
after it is sewn.

The module's own opening rule makes this a contradiction rather than an
omission: *"It describes; it never re-derives"* — and yet the one number
it prints is derived from a file offset the reader has never seen.

## 2. Current code

### `deckle/core/schedule.py:134-146` — the whole defect

```python
def _page_numbers(side) -> tuple[int | None, ...]:
    """Reader-facing page numbers for one side, left to right.

    ``None`` for a blank. Page numbers are 1-based because the schedule is
    read by a person holding the book, and ``source_ref.page_index`` is 0-based
    because it indexes a file.
    """
    if side is None:
        return ()
    return tuple(
        None if page.source_ref is None else page.source_ref.page_index + 1
        for page in side.pages
    )
```

The docstring names the distinction it then fails to make: "0-based
because it indexes a file" is exactly why `+ 1` is not a page number.

### `deckle/core/schedule.py:36-48` — the fields' documented contract

```python
    :ivar sheet_index: the sheet's index in the plan, which is also the
        order it comes off the printer.
    :ivar position: 1 for the outermost sheet of its signature, counting
        inward. This is gathering order: the binder stacks position 1 first
        and every later sheet on top of it.
    :ivar front_pages: reader-facing page numbers on the front, left to
        right. ``None`` marks a blank.
    :ivar back_pages: the same for the back.
```

### `deckle/core/schedule.py:213-238` — the only caller

```python
    by_index = {sheet.index: sheet for sheet in plan.sheets}
    signatures: list[SignatureInstruction] = []

    for signature in plan.signatures:
        sheets: list[SheetInstruction] = []
        # `sheet_indices` is already outermost-first: that is the order the
        # imposer nested them, and the order they must be gathered.
        for position, sheet_index in enumerate(signature.sheet_indices, start=1):
            sheet = by_index.get(sheet_index)
            if sheet is None:
                continue
            sheets.append(
                SheetInstruction(
                    sheet_index=sheet_index,
                    position=position,
                    front_pages=_page_numbers(sheet.front),
                    back_pages=_page_numbers(sheet.back),
                )
            )
```

Note the `if sheet is None: continue` at `:222-223`: a signature naming a
sheet the plan does not carry is tolerated, silently. Step 4 in §3 keeps
that tolerance and makes it not desynchronise the numbering.

### `deckle/core/signatures.py:154-204` — what the fix reads instead

```python
def fold_reading_order(plan: SheetPlan) -> list[int]:
    """The reading-order page index for every physical print slot in ``plan``.

    Derived independently of ``saddle_order`` -- deliberately not by
    evaluating the same closed-form arithmetic under a different name, but
    by simulating the physical fold: ...

    Signatures are walked in ``plan.signatures`` order and their resulting
    slot orders concatenated, each offset by the page count of every
    signature already placed, so this function's output lines up 1:1 with
    ``saddle_order``'s per-signature output when the two are compared.

    :param plan: the imposed plan. Only ``plan.signatures`` is read -- a
        plan with no signatures yields an empty list.
    :returns: the reading-order page index for every physical print slot,
        concatenated across signatures in binding order.
    """
```

It returns `4 × (total sheets across all signatures)` entries, one per
physical print slot, in signature order — front's two leaves then back's
two leaves, sheet by sheet. It is pure, already shipped, and already
covered by
`tests/test_layout_saddle.py::test_fold_simulator_round_trip_small_matrix`
(every page count 1-40 × every signature size 1-8 × both binding edges)
and by
`tests/test_imposition_properties.py::test_fold_reading_order_is_a_permutation_of_the_print_slots`.

### Every reader of these fields

`grep -rn "front_pages\|back_pages\|_page_numbers\|build_schedule" --include='*.py' .`
(excluding `.venv`):

| Site | Use |
|---|---|
| `deckle/core/schedule.py:83, 89` | `SignatureInstruction.first_page` / `last_page` — `min`/`max` over the numbers, so the `SIGNATURE n (pages a-b)` header is wrong wherever the numbers are |
| `deckle/core/schedule.py:393-394` | `format_schedule_text`, the `front:` / `back:` lines |
| `deckle/cli.py:1103-1105` | `deckle schedule` — no `try` around it |
| `deckle/app/views/layout_panel.py:1330-1333` | the Signatures tab's *Save binding schedule* button — no `try` around it |
| `deckle/core/print_session.py:131-137` | writes its **own** `"front_pages"`/`"back_pages"` keys into the session state from `page.source_ref.page_index`. A different structure with the same field names; **not** touched by this spec |
| `tests/test_schedule.py:93, 108, 116, 159, 318` | assertions over the tuples |
| `tests/test_custom_signatures.py:214-231` | builds schedules over explicit `signature_lengths` |
| `tests/test_ui_surface.py:954-965` | builds a schedule through the panel |
| `tests/test_grain_and_spine.py:157, 172` | formats a schedule's text |

### Existing tests that pin the numbering

- `tests/test_schedule.py:72-98` —
  `test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern`.
  The load-bearing test in the file, written out by hand from a
  bookbinding manual: `((16, 1), (2, 15))`, `((14, 3), (4, 13))`,
  `((12, 5), (6, 11))`, `((10, 7), (8, 9))`. Its 16 pages come from one
  source, unskipped, so **the fix reproduces it exactly** — see §3.3.
- `tests/test_schedule.py:131-137` —
  `test_each_signature_reports_the_page_span_it_contains`: 64 pages,
  signatures spanning 1-16 and 17-32. Reproduced exactly.
- `tests/test_schedule.py:150-163` — `test_padding_blanks_are_counted`:
  14 pages, two `None`s. Reproduced exactly.
- `tests/test_schedule.py:307-321` —
  `test_every_page_appears_exactly_once_across_the_schedule`, 32 pages,
  one source. Reproduced exactly.

## 3. Change

### 3.1 The definition

> A **reader-facing page number** is the leaf's 1-based position in the
> finished book's reading order: the order a person turns the pages,
> counting **every** imposed slot — content pages, blanks the user
> inserted, the leading filler `start_on_recto=False` adds, and the
> trailing padding blanks that round a signature out.

**Blanks are counted.** A person thumbing through the bound book counts
the blank leaves along with the printed ones — that is what "as a reader
counts them" means — so the page after a blank is `n + 2`, not `n + 1`.
The blank *slot itself* still prints as `blank` rather than as its
number, because it carries no content the binder can check against; but
it consumes its position, so every later number stays true to the object
in the binder's hands.

Rejected: numbering only the content pages, so blanks are transparent and
the printed numbers run 1, 2, 3 … with no gaps. That produces numbers
that match nothing physical — the schedule would say "page 5" about a
leaf the reader would call page 7 — and it makes the blank count
invisible in the one document whose job is to describe the paper.

**Skipped pages are not counted**, because they are not in the book. They
never reach `plan.sheets`: `SaddleStitchStrategy.impose` drops them at
`deckle/core/layout.py:876`.

### 3.2 The mechanism

`fold_reading_order(plan)` already answers exactly this question, for
every print slot, and was written independently of `saddle_order`
specifically so that it can be trusted as a second opinion. Using it here
keeps `schedule.py`'s stated rule — *describe, never re-derive* — because
it reads the plan rather than recomputing the imposition.

The plan's slots are walked in **signature order**, which is the order
`build_schedule` already walks and the order `fold_reading_order`
concatenates in. Under folio every sheet has both faces and every face
has two leaves, so slot `k` of sheet `p` in signature order is at index
`4·p + (0, 1)` for the front and `4·p + (2, 3)` for the back.

### 3.3 Why the existing 16-page test is unchanged

`fold_reading_order` for one signature of four sheets is
`[15, 0, 1, 14, 13, 2, 3, 12, 11, 4, 5, 10, 9, 6, 7, 8]`. Adding one
gives `[16, 1, 2, 15, 14, 3, 4, 13, 12, 5, 6, 11, 10, 7, 8, 9]`, which
grouped four at a time is `(16, 1), (2, 15)` / `(14, 3), (4, 13)` /
`(12, 5), (6, 11)` / `(10, 7), (8, 9)` — the manual's table, and the
tuple already written out at `tests/test_schedule.py:86-92`. For a
single-source, unskipped, unpadded document, reading position `+ 1` and
`page_index + 1` are the same number by construction. **No existing
assertion in the suite changes.**

### 3.4 Numbered edits

All in `deckle/core/schedule.py`.

1. **Import**, beside the existing core imports at `:25-28`:

   ```python
   from deckle.core.signatures import fold_reading_order
   ```

   `deckle.core.signatures` imports only `SheetPlan` from
   `deckle.core.models`, so this introduces no cycle: `schedule` already
   imports `models`, `marks`, `paper` and `printing`, and `signatures`
   imports none of those.

2. **Replace `_page_numbers` (lines 134-146)** with:

   ```python
   def _page_numbers(
       side, reading_order: list[int], first_slot: int
   ) -> tuple[int | None, ...]:
       """Reader-facing page numbers for one side, left to right.

       ``None`` for a blank. A **reader-facing page number** is the leaf's
       1-based position in the finished book's reading order -- the order
       a person turns the pages -- counting every imposed slot, blanks
       included. A blank leaf still prints as ``blank`` rather than as its
       number, because there is nothing on it to check; it consumes its
       position all the same, so the page after it is ``n + 2``, which is
       what the reader will call it.

       This used to be ``source_ref.page_index + 1``, the page's offset
       **inside the file it came from**. The two agree only for a document
       that is exactly one source, imported whole, with nothing skipped and
       nothing inserted. A four-page book made of two 2-page PDFs was
       reported as "front: 2 1 / back: 2 1" -- pages 1 and 2 twice, pages 3
       and 4 nowhere.

       :param side: the ``Side`` to number, or ``None`` for a face that
           does not exist.
       :param reading_order: :func:`~deckle.core.signatures.fold_reading_order`
           over the whole plan.
       :param first_slot: this side's first leaf's index into
           ``reading_order``.
       :returns: one entry per leaf, left to right.
       """
       if side is None:
           return ()
       return tuple(
           None if page.source_ref is None else reading_order[first_slot + i] + 1
           for i, page in enumerate(side.pages)
       )
   ```

3. **In `build_schedule`**, immediately after
   `by_index = {sheet.index: sheet for sheet in plan.sheets}` (line 213),
   insert:

   ```python
       # The reader's own numbering, taken from the fold simulator rather
       # than from any file offset. `fold_reading_order` walks
       # `plan.signatures` in the same order this loop does and emits four
       # slots per sheet -- front's two leaves, then back's two -- so a
       # single cursor keeps the two in step.
       reading_order = fold_reading_order(plan)
       slot_total = sum(
           len(side.pages)
           for sheet in plan.sheets
           for side in (sheet.front, sheet.back)
           if side is not None
       )
       if plan.signatures and len(reading_order) != slot_total:
           raise ValueError(
               f"this plan has {slot_total} print slot(s) but its signatures "
               f"describe {len(reading_order)}; a schedule cannot number "
               "pages it cannot place in reading order"
           )
       slot = 0
   ```

   The guard is unreachable from either shipped strategy —
   `SaddleStitchStrategy` gives every sheet two two-leaf faces and asserts
   the signature partition four ways
   (`deckle/core/layout.py:1042-1057`), and `GutterShiftStrategy` produces
   no signatures at all, so `plan.signatures` is empty and the check is
   skipped. It exists because the alternative to raising is zipping two
   lists of different lengths and printing confident numbers off the
   misalignment, which is the one thing this module refuses to do (see its
   note at `:252-258` about "a confident falsehood, which is worse than
   saying nothing"). A `ValueError` is the right shape: it is what
   `split_signatures_at` already raises for a grouping that does not add
   up.

4. **Replace the two `_page_numbers` calls and the tolerance branch**
   (lines 220-231) with:

   ```python
           for position, sheet_index in enumerate(signature.sheet_indices, start=1):
               sheet = by_index.get(sheet_index)
               if sheet is None:
                   # A signature naming a sheet the plan does not carry.
                   # Skipped as before -- but the cursor still advances,
                   # because `fold_reading_order` counted four slots for it
                   # and every later sheet's numbers would otherwise slide.
                   slot += 4
                   continue
               front_pages = _page_numbers(sheet.front, reading_order, slot)
               back_pages = _page_numbers(sheet.back, reading_order, slot + 2)
               slot += 4
               sheets.append(
                   SheetInstruction(
                       sheet_index=sheet_index,
                       position=position,
                       front_pages=front_pages,
                       back_pages=back_pages,
                   )
               )
   ```

5. **`deckle/core/schedule.py:40-42`**, the `SheetInstruction` field docs,
   replace

   ```python
       :ivar front_pages: reader-facing page numbers on the front, left to
           right. ``None`` marks a blank.
       :ivar back_pages: the same for the back.
   ```

   with

   ```python
       :ivar front_pages: reader-facing page numbers on the front, left to
           right -- each leaf's 1-based position in the finished book's
           reading order, counting blanks. ``None`` marks a blank, which
           holds its position without printing a number.
       :ivar back_pages: the same for the back.
   ```

6. **`deckle/core/schedule.py:81-90`**, `first_page` / `last_page`, append
   one sentence to each docstring: `"Reader-facing, so a signature's span
   is what the reader will find in it."` No code change — `min`/`max` over
   the corrected numbers is already right.

7. **`docs/api/`** — nothing to add; `core.schedule.rst` exists.

## 4. Tests

Write these first; all fail on the unfixed tree. `tests/test_schedule.py`
already has `_pages(n)`, `_folio_settings(**overrides)` and
`_folio_schedule(n_pages, **overrides)` (`:31-66`); add a
`_folio_schedule_from(pages, **overrides)` that takes an explicit page
list, since every new case needs one.

### `tests/test_schedule.py`

**`test_pages_from_two_sources_are_numbered_through_the_book`**
Four pages: two from `a.pdf` (indices 0, 1) and two from `b.pdf`
(indices 0, 1), `sheets_per_signature=1`. Assert the single sheet's
`(front_pages, back_pages) == ((4, 1), (2, 3))`.
Unfixed: `assert ((2, 1), (2, 1)) == ((4, 1), (2, 3))`.

**`test_a_skipped_page_does_not_leave_a_hole_in_the_numbering`**
Five pages from one source with index 1 skipped, `sheets_per_signature=1`.
Assert `((4, 1), (2, 3))`, and that no number exceeds 4 — the book has
four leaves.
Unfixed: `assert ((5, 1), (3, 4)) == ((4, 1), (2, 3))`.

**`test_an_inserted_blank_takes_a_page_number_without_printing_one`**
Four slots: `a.pdf` page 0, a `BLANK_SOURCE_PATH` page, `a.pdf` pages 1
and 2; `sheets_per_signature=1`. Assert `((4, 1), (None, 3))` — the blank
sits at reading position 2 and prints as `None`, and the leaf after it is
3, not 2.
Unfixed: `assert ((3, 1), (None, 2)) == ((4, 1), (None, 3))`.

**`test_padding_blanks_take_the_last_page_numbers`**
`_folio_schedule(14, sheets_per_signature=4)` — 14 content pages padded
to 16. Assert the set of non-`None` numbers is exactly `set(range(1, 15))`
and that the two `None`s occupy the two positions that would have been 15
and 16, i.e. that both `15` and `16` are absent from the printed numbers.
Passes today (a single unskipped source), and pins the counting rule
against a "renumber so blanks are transparent" regression, which would
print 1-14 with the blanks anywhere.

**`test_the_signature_span_is_what_the_reader_will_find_in_it`**
Two sources of 8 pages each, `sheets_per_signature=2` (two signatures of
8 pages). Assert `(first_page, last_page)` is `(1, 8)` and `(9, 16)`.
Unfixed: both signatures report `(1, 8)`, because each source restarts at
index 0. `assert (1, 8) == (9, 16)`.

**`test_the_classic_sixteen_page_pattern_is_unchanged`** — not a new test.
`test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern`
(`tests/test_schedule.py:72`) is the pin and must stay green untouched.

**`test_every_page_appears_exactly_once_across_two_sources`**
The existing `test_every_page_appears_exactly_once_across_the_schedule`
(`:307`), widened: same parametrisation over `sheets_per_signature` in
`[1, 2, 4, 8]`, but with 32 pages drawn from **four** 8-page sources.
Assert `sorted(seen) == list(range(1, 33))`.
Unfixed: `seen` is `[1..8] * 4`; `assert [1,1,1,1,2,2,2,2,…] == [1,2,3,…,32]`.

**`test_a_schedule_whose_signatures_do_not_match_its_sheets_is_refused`**
Hand-build a `SheetPlan` with one `Signature(index=0,
sheet_indices=(0, 1), blank_count=0)` but only one `Sheet`, each face
carrying two filler `OutputPage`s. Assert `build_schedule` raises
`ValueError` whose message contains both counts (`4` and `8`).
Unfixed: no exception — `_page_numbers` never looks at the plan — so
`pytest.raises(ValueError)` fails with `DID NOT RAISE`.

### `tests/test_custom_signatures.py`

**`test_stated_signature_lengths_keep_the_reading_numbering_contiguous`**
Reuse that file's `_settings(signature_lengths=(4, 2))` fixture
(`:214-231`). With `6 × 4 = 24` pages, assert the concatenation of every
sheet's `front_pages + back_pages` across both signatures, with `None`s
dropped, is a permutation of `range(1, 25)` and that signature 1 spans
1-16 and signature 2 spans 17-24.
Passes today (one source) and pins the offsetting across
non-uniform signatures, which `fold_reading_order` does with a running
`offset` (`deckle/core/signatures.py:182, 203`).

## 5. Acceptance

| Check | Command |
|---|---|
| the file index is no longer the page number | `! grep -n "page.source_ref.page_index + 1" deckle/core/schedule.py` (matches `deckle/core/schedule.py:144` today) |
| the fold simulator is the source | `grep -n "from deckle.core.signatures import fold_reading_order" deckle/core/schedule.py` |
| the mismatch guard exists | `grep -n "cannot number" deckle/core/schedule.py` |
| the repro from §1 now reads through the book | paste §1's fenced block; all three lines must show `[((4, 1), (2, 3))]` except `inserted blank`, which must show `[((4, 1), (None, 3))]` |
| the new schedule tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_schedule.py -q --no-header -p no:cacheprovider -k "two_sources or skipped_page or inserted_blank or padding_blanks or signature_span or refused"` |
| **the manual's ordering pin is unchanged** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_schedule.py -q --no-header -p no:cacheprovider -k classic_saddle_stitch` |
| the custom-signature tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_custom_signatures.py -q --no-header -p no:cacheprovider` |
| the CLI still writes a schedule | `.venv/bin/python -m deckle.cli schedule tests/fixtures/sample.pdf --fold-scheme folio --paper 792x612pt \| grep -q "BINDING SCHEDULE"` |
| `schedule.py` stays pure | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_core_purity.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

- **The gutter-shift path.** `GutterShiftStrategy` emits no signatures,
  so `build_schedule` never enters the loop and `format_schedule_text`
  takes the "nothing to gather or sew" branch
  (`deckle/core/schedule.py:350-364`). This spec changes nothing there.
  A flat-sheet schedule that listed page numbers would be a feature
  (N12's neighbourhood), not this fix.
- **B8** — the creep note, `deckle/core/schedule.py:181-198`. Same file,
  different function; the two can be written in parallel and merged in
  either order.
- **`print_session._hash_plan`'s own `"front_pages"`/`"back_pages"` keys**
  (`deckle/core/print_session.py:131-137`). Same names, different
  structure, different purpose — they are part of a session-state hash,
  not a bench instruction. **B3** owns that code; do not touch it here,
  and in particular do not "unify" the two.
- **F10** — the "read the book back" line in the Signatures tab. It wants
  the same numbering and is unblocked by this, but is its own feature.
- **The CLI's and panel's lack of a `try` around `build_schedule`.** The
  new `ValueError` is unreachable from either shipped strategy; adding
  error handling for it would be handling an error that cannot occur. If
  a third strategy ever produces such a plan, that is when the callers
  grow a branch.

## 7. decisions.md entry

```
## 2026-09-05 — The binding schedule printed file offsets and called them page numbers
- Symptom: `SheetInstruction.front_pages` is documented as "reader-facing page numbers ... as a reader counts them" and was computed as `source_ref.page_index + 1` -- the page's offset inside the file it came from. Measured on three four-page books: two 2-page sources reported "front: 2 1 / back: 2 1", so pages 1 and 2 appeared twice and 3 and 4 nowhere; one skipped page reported "front: 5 1" for a book with four leaves; an inserted blank was reported but the leaves around it were numbered as though it were absent. The `SIGNATURE n (pages a-b)` headers were wrong with them.
- Fix: The number is now the leaf's 1-based position in the finished book's reading order, taken from `signatures.fold_reading_order(plan)` -- which already answers exactly that, for every print slot, and was written independently of `saddle_order` so it can be trusted as a second opinion. Blanks are counted: a reader thumbing the bound book counts blank leaves too, so the page after a blank is n + 2. A blank slot still prints as `blank`, since there is nothing on it to check.
- Surfaces: For a single-source, unskipped, unpadded document, reading position and file index are the same number by construction -- which is why the load-bearing pin (`test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern`, written out by hand from a bookbinding manual) reproduces byte for byte and no existing assertion moved. That is also why the defect survived: every schedule test in the suite used exactly one whole source.
- Watch: This module opens with "It describes; it never re-derives", and the one number it printed was derived from an offset the reader had never seen. The docstring of the broken function even named the distinction -- "0-based because it indexes a file" -- one line above using it as a page number. `fold_reading_order` was already pure, already tested over every page count 1-40 x every signature size 1-8 x both binding edges, and had exactly one caller.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli schedule ...` headless.
- **`tests/test_schedule.py::test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern`
  is the pin.** Its expected table was written out by hand from a
  bookbinding manual, deliberately not derived from the code. It must
  stay green with no edit. If it goes red, the cursor arithmetic in step
  4 is wrong — most likely `slot + 2` for the back, which is right only
  because a folio face carries exactly two leaves.
- **`fold_reading_order` walks `plan.signatures`, not `plan.sheets`.** It
  reads only the *count* of each signature's `sheet_indices`, never their
  values, and offsets by page count. `build_schedule`'s loop walks
  signatures in the same order, which is what makes one shared cursor
  correct. Do not rewrite step 4 to iterate `plan.sheets` and index by
  `sheet.index`: `SaddleStitchStrategy` happens to make those identical,
  and the shared invariant would then be undocumented rather than
  enforced.
- **An inserted blank and a padding filler are indistinguishable in the
  plan.** `layout` turns `is_blank_page(p)` into the same `None` slot the
  padding uses (`deckle/core/layout.py:876`, and the comment at `:871-875`
  says why). Both therefore print as `blank` and both consume a reading
  position. That is correct and intentional; do not add a field to tell
  them apart.
- **`Signature.blank_count` is unchanged by this spec** and counts slots,
  not numbers. `Schedule.blank_total` sums it. Neither moves.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`) and
  `schedule.py` must stay I/O-free (its module docstring: "It is pure: no
  I/O, no Qt, no rendering"). `deckle.core.signatures` is pure — it
  imports `deque`, `typing` and `SheetPlan` — so the new import is safe.
- **`tests/test_docs_coverage.py`** requires a `docs/api/` page per
  module. No new module here, so nothing to add.
