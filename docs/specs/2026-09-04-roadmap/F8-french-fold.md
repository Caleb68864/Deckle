# F8 — French fold: print one side, fold at the fore-edge, bind the open edge

**Roadmap item:** `docs/ROADMAP.md` F8
**Depends on:**
- **F4 — hard gate.** The roadmap records F8 as *"Gated on F4"* for the same
  reason F7 is: a fold order nobody has folded is a hypothesis. Do not merge
  while `docs/verification/*-folio-dummy.md`'s reading-order verdict is
  `untested` or `diverged`.
- **F7 — recommended, not required.** F8 reuses the cell generalisation and
  the `fold_scheme` widening F7 does. Doing F7 first makes F8 a small spec;
  doing F8 first means doing half of F7's plumbing under a different name.
  The roadmap's order is *"F7 quarto → F8 French fold, in that order"*.

**Blocks:** —
**Size:** M
**Decision needed first:** none.

---

## 1. Context

A French fold prints on **one side of the paper only**. Each sheet is folded
in half with the printed faces **outward**; the fold becomes the fore-edge and
the two open edges come together at the spine, where the block is stab-sewn or
glued. The blank side of the paper ends up hidden inside every leaf.

Competitive gaps #4, which recommends it:

> **Why a hand binder wants it, and why *this* user especially.** It removes
> the manual-duplex second pass entirely. No reload, no flip calibration, no
> pass-2 misregistration, no risk of discarding sixty sheets printed upside
> down — which the design document names as Deckle's second-most-expensive
> failure mode. It also solves show-through: on cheap 20lb paper, dense text
> bleeds visibly through to the back, and French fold hides the back
> completely. The cost is exactly double the paper.

and on the fit:

> **Fit with Deckle.** Strong, and slightly ironic: it is the one imposition
> scheme whose value proposition is *not needing* Deckle's print pipeline. It
> is still worth having, because "print this one single-sided" is a
> legitimate answer to "my printer's back pass is unreliable" and Deckle is
> the tool that knows that about your printer.

**What exists.** `Sheet.back` is already `Side | None` and `export` already
handles an absent face — but **no strategy has ever produced one**, which the
model says outright (`models.py:199-205`). F8 is the first producer, so every
consumer of `sheet.back` is newly reachable and has to be audited. That audit
is the real work in this spec; the geometry is a mirror of folio's.

```
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/f.pdf \
      --fold-scheme french
usage: deckle export [-h] -o OUTPUT ...
                     source
deckle export: error: argument --fold-scheme: invalid choice: 'french' (choose from 'none', 'folio')
```

## 2. Current code

`deckle/core/models.py:189-217` — the field F8 first uses, and the paragraph
that says nobody uses it:

```python
@dataclass(frozen=True)
class Sheet:
    """One physical piece of paper with an optional front and back.

    :ivar index: the sheet's position in the plan, and the identifier every
        pass, export subset and cache key refers to it by.
    :ivar front: the first face through the printer, or ``None``.
    :ivar back: the second face, or ``None`` for a face that does not
        exist -- as distinct from one carrying only filler, which is a
        ``Side`` whose pages are all blank. See ``Side.pages``.

        **No strategy currently produces ``None`` here.** This previously
        claimed an odd final sheet under gutter shift has no back, which
        is not true: ``layout._pad_to_even`` rounds the slot count up
        before sheets are built, so every gutter-shift sheet gets both
        faces and the last back is filler rather than absent. Saddle
        stitch pads to a multiple of four for the same reason.

        The field stays because the distinction is real and the exporter
        has to honour it either way -- and the two consumers want opposite
        things. A both-faces export omits an absent face; a manual-duplex
        pass pads it with a blank, because a pass's pages map one-to-one
        onto the sheets being fed and dropping one shifts every later back
        onto the wrong front.
    """

    index: int
    front: Side | None
    back: Side | None
```

`deckle/core/export.py:384-412` — the absent-face handling, already correct,
including the sentence F8 makes obsolete:

```python
def _sides(sheet: Sheet, side: str | None = None) -> list[Side | None]:
    """The faces of ``sheet`` to write, front then back.
    ...
    - **Both faces** (``side is None``) omit it. This is the interleaved
      document a real duplexer consumes; there is nothing to print, so a
      page there is a wasted side of paper.

    - **A pass** pads it with a blank. A pass PDF's pages map one-to-one
      onto the sheets being fed, so dropping one shifts every later back
      onto the wrong front -- the entire stack ruined, and not discovered
      until the paper is spent. A blank sheet through the printer costs a
      pass; a mis-registered stack costs the job.

    No plan Deckle currently produces reaches the padding branch --
    ``_pad_to_even`` gives gutter shift an even slot count, so its
    ``back=None`` case is unreachable. It is written correctly anyway
    because the cost of the two branches is so lopsided.
    """
    if side is None:
        return [face for face in (sheet.front, sheet.back) if face is not None]
    return [sheet.front if side == "front" else sheet.back]
```

`deckle/core/printing.py:134-165` — `plan_passes`, which always returns two
passes regardless of what the sheets carry:

```python
    front_order = list(indices)
    back_order = list(reversed(indices)) if profile.reverse_stack else list(indices)

    return [
        PrintPass(
            index=0,
            sheet_order=front_order,
            side="front",
            reload_instruction=_front_instruction(profile),
            rotate_backs=False,
        ),
        PrintPass(
            index=1,
            sheet_order=back_order,
            side="back",
            reload_instruction=_back_instruction(profile),
            rotate_backs=profile.flip_axis == "long",
        ),
    ]
```

`deckle/core/printing.py:114-118`, the wording that changes for a one-pass
job:

```python
def _front_instruction(profile: PrinterProfile) -> str:
    return (
        f"Load paper {_face_word(profile.output_face)}, feed edge "
        f"{profile.feed_edge}, and print pass 1 (fronts)."
    )
```

`deckle/core/layout.py:708-721` — the folio geometry F8 mirrors, and
`layout.py:954-959`, the folio spine assignment F8 inverts:

```python
                if settings.binding_edge == "right":
                    cell_a, spine_a = cells[1], "left"
                    cell_b, spine_b = cells[0], "right"
                else:
                    cell_a, spine_a = cells[0], "right"
                    cell_b, spine_b = cells[1], "left"
```

Folio's spines point **inward**, at the fold. F8's point **outward**, at the
open edges.

### Every consumer of `sheet.back`, which F8 makes reachable for the first time

```
$ grep -rn "\.back\b" deckle/ | grep -v "back_offset\|back_order\|back_marks\|back_a\|back_b\|_is_back\|backend"
deckle/core/schedule.py:229:                    back_pages=_page_numbers(sheet.back),
deckle/core/locate.py:118:        for name, side in (("front", sheet.front), ("back", sheet.back)):
deckle/app/views/preview_view.py:120:    for side_name, side in (("front", sheet.front), ("back", sheet.back)):
deckle/app/views/preview_view.py:890:                    sheet.back if sheet else None
deckle/core/render.py:234:    has_back = sheet is not None and sheet.back is not None
deckle/core/export.py:101:        for side_name, side in (("front", sheet.front), ("back", sheet.back)):
deckle/core/export.py:411:        return [face for face in (sheet.front, sheet.back) if face is not None]
deckle/core/export.py:412:    return [sheet.front if side == "front" else sheet.back]
deckle/core/print_session.py:135:                "back": s.back is not None,
deckle/core/print_session.py:136:                "back_pages": None if s.back is None else [...]
```

`schedule.py:229` passes `None` to `_page_numbers`, which returns `()` at
line 138-139 — safe. `export.py:101` is `_plan_hash`; `print_session.py:135`
is `_hash_plan`; both already branch. `render.py:234` computes `has_back`
and must be read to see what it does with it. `preview_view.py:120` and
`:890` are the two the audit is for.

### Existing tests

`tests/test_side_reporting.py` (absent faces, per its name — read it first),
`tests/test_export.py`, `tests/test_print_session.py`,
`tests/test_preview_fidelity.py`, `tests/test_render.py`,
`tests/test_printing.py`, `tests/test_layout.py`.

## 3. Change

### 3.1 The imposition, derived from folding a sheet

**Paper.** Landscape, like folio: two portrait cells side by side, folded once
about the vertical centreline. Letter landscape (792 × 612) gives 396 × 612
leaves.

**The fold, as prose for the schedule and the GUIDE:**

> 1. Lay the sheet **printed side up, landscape**.
> 2. **Fold the right half behind the left half** — take the right edge,
>    carry it away from you and across to the left edge. The printed side
>    stays on the outside of both halves; the blank side is now hidden
>    inside.
> 3. The crease is the **fore-edge**. The two edges that have come together —
>    the sheet's original left and right edges — are the **spine**.
> 4. Stack every folded sheet in order, folds all at the fore-edge, and bind
>    through the open edge: stab-sew, or glue.

**Deriving the two pages.** With the right half folded behind, the surface
facing you is the **left** cell and the surface facing away is the **right**
cell. Stack sheet 1 on top and read from the top: sheet 1's up-facing surface
is page 1, its down-facing surface is page 2, sheet 2's up-facing surface is
page 3, and so on. So for sheet `j` (0-based), in 0-based page indices:

```
left cell  = 2j        (page 1, 3, 5, ... -- a recto)
right cell = 2j + 1    (page 2, 4, 6, ... -- a verso)
```

**Consecutive pairs, no nesting.** That is the whole ordering, and it is why
the gaps research calls it *"much simpler than saddle stitch"*.

**The spine is on the outer cell edges.** After folding, both original outer
edges coincide at the footprint's left edge, which is the spine. So:

| cell | page | spine edge | rotation |
|---|---|---|---|
| left (`0 .. W/2`) | `2j` | its **left** edge (`x = 0`) | none |
| right (`W/2 .. W`) | `2j + 1` | its **right** edge (`x = W`) | none |

**Neither cell is rotated.** The fold is about a vertical axis, which mirrors
horizontally and leaves "up" alone; the right cell is simply viewed from the
other side of the paper, head still at the top.

**Check.** The left cell's spine is on its left, which is where a recto's
gutter belongs in a left-bound book; the right cell's is on its right, which
is where a verso's belongs. Under `binding_edge == "right"` (right-bound,
RTL) both mirror, exactly as folio's do.

**Verification of the geometry, in one sentence:** fold any sheet in half
printed-side-out and the two printed faces' *outer* edges are the ones that
meet. If a printed spine margin ends up at the fold, the strategy has folio's
spine assignment and not this one — which is the single defect F8 can have,
and §5's dummy is what catches it.

### 3.2 `deckle/core/models.py`

```python
    fold_scheme: Literal["none","folio","quarto","french"] = "none"
```

(`"quarto"` only if F7 has landed; if F8 goes first, add just `"french"`.)

Extend the `:ivar fold_scheme:` docstring: *"``french`` prints one side only
and folds each sheet printed-face-out, so the fold is the fore-edge and the
spine is the open edge. It is the only scheme that produces
``Sheet.back is None``."*

Delete the paragraph in `Sheet.back`'s docstring beginning **"No strategy
currently produces `None` here."** and replace it with:

```
        ``FrenchFoldStrategy`` is the one strategy that produces ``None``
        here: a French fold prints on one side of the paper only. Every
        other scheme pads to an even slot count and gives every sheet both
        faces, so a back that carries nothing is filler rather than absent.
```

A sentence in a docstring asserting a capability the program does not have is
the shape `docs/decisions.md` has named five times; this one becoming true is
the moment to update it.

### 3.3 `deckle/core/layout.py` — `FrenchFoldStrategy`

Reuses `cell_geometry` unchanged — the cells are the same two halves; only the
spine assignment and the absent back differ.

```python
class FrenchFoldStrategy:
    """Single-sided imposition: two leaves per sheet, folded printed-face-out.

    The mirror of :class:`SaddleStitchStrategy`'s geometry with one face
    removed. Folio folds printed-face-in, so its spines meet at the fold;
    a French fold folds printed-face-out, so the fold is the fore-edge and
    the spines are the sheet's two outer edges. There is no nesting and no
    second pass: sheet ``j`` carries pages ``2j`` and ``2j+1``, in order.

    Costs exactly double the paper, and buys the removal of the entire
    manual-duplex reload -- no flip convention, no pass-2 misregistration,
    and no show-through on cheap stock, because the back of every leaf is
    folded inside.
    """

    def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan:
```

Body, following `SaddleStitchStrategy.impose`'s shape:

1. `active = [None if is_blank_page(p) else p for p in pages if not p.skipped]`,
   then `_lead_for_recto(active, settings)`.
2. `grain_warning(settings)` — and widen `layout.py:275`'s
   `folded = settings.fold_scheme == "folio"` to include `"french"` (and
   `"quarto"` if F7 landed). The grain rule is "grain parallel to the spine";
   under French fold the *spine* is an open edge, but the *fold* still runs
   vertically and still cracks across the grain, so the warning is right and
   its `"fold"` wording is right.
3. The **landscape** expectation, same as folio's, with the scheme named:

```python
        if paper_h >= paper_w:
            warnings.append(
                LayoutWarning(
                    sheet_index=0,
                    kind="sheet_orientation",
                    detail=(
                        'fold_scheme="french" expects landscape paper wider '
                        "than it is tall; proceeding with the paper as given"
                    ),
                )
            )
```

4. `slots, padded = _pad_to_even(active)`. One trailing blank at most, and a
   `signature_padding` warning when `padded` — reuse the existing message
   with the count `1`.
5. `scale = document_scale(active, settings, cell=cells[0])`.
6. Per sheet `j = i // 2`:

```python
            # Printed-face-OUT, so the spine is the sheet's outer edge and
            # the fold in the middle is the fore-edge -- the exact inverse of
            # folio, and the one thing this strategy can get wrong.
            if settings.binding_edge == "right":
                cell_a, spine_a = cells[1], "right"
                cell_b, spine_b = cells[0], "left"
            else:
                cell_a, spine_a = cells[0], "left"
                cell_b, spine_b = cells[1], "right"
```

   with `a` = `slots[i]` and `b` = `slots[i + 1]`.
7. Marks, on the front only:

```python
            marks = [
                fold_line(paper_h, fold_x),
                # Head and tail only. The fore-edge is the fold and is never
                # cut -- cutting it is what a French fold exists to avoid --
                # and the spine is bound, not trimmed.
                *cut_lines(paper_w, paper_h, settings.trim_pt, ()),
            ]
```

   **No sewing stations and no signature-order mark.** Reasons, both recorded
   in the code: a French fold is bound through the open edge, so its holes go
   through the whole block and are measured from the finished spine, not
   straddling a fold — that is N5's geometry, not `marks.sewing_stations`'s.
   And there is no gathering, so there is no order to encode.
8. `sheets.append(Sheet(index=j, front=Side(pages=(a_page, b_page), marks=tuple(marks)), back=None))`
9. `return SheetPlan(sheets=sheets, paper_pt=settings.paper, warnings=warnings, signatures=())`
   — **empty signatures**, like `fold_scheme="none"`: nothing is gathered.

### 3.4 `deckle/core/signatures.py` — `fold_reading_order` and the honest note

`fold_reading_order` must not raise on a back-less plan, because F10 puts its
output in the schedule. Add, before the per-signature loop:

```python
    if plan.sheets and plan.sheets[0].back is None:
        # A French fold's slot order is the identity: sheet j carries pages
        # 2j and 2j+1 and nothing is nested. Returned rather than refused so
        # the "read the book back" line works for every scheme -- but say
        # plainly that this derivation carries no information. The check that
        # matters for a French fold is geometric (the spine is on the OUTER
        # cell edges) and physical, not arithmetic.
        return list(range(2 * len(plan.sheets)))
```

Say the same thing in the function's docstring and in the module docstring's
`fold_reading_order` bullet. **Do not pretend the round trip validates
anything here**; the whole value of that pairing is that two derivations
disagree when one is wrong, and an identity permutation cannot disagree with
anything.

### 3.5 `deckle/core/printing.py` — one pass when there is nothing on the back

```python
def plan_passes(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheets: Sequence[int] | None = None,
) -> list[PrintPass]:
    """Compute the front and back passes for ``sheets`` (default: all).
    ...
    A plan whose selected sheets all have ``back is None`` -- a French fold,
    which prints on one side of the paper -- returns **one** pass. Planning a
    reload for a pass with nothing to print would send the operator to the
    printer, have them flip a whole stack, and then print nothing.
    """
```

Implementation: after computing `indices`,

```python
    by_index = {s.index: s for s in plan.sheets}
    single_sided = bool(indices) and all(
        by_index[i].back is None for i in indices if i in by_index
    )
```

and when `single_sided`, return `[front_pass]` only, with

```python
def _single_pass_instruction(profile: PrinterProfile) -> str:
    return (
        f"Load paper {_face_word(profile.output_face)}, feed edge "
        f"{profile.feed_edge}, and print. This document prints on one side "
        "of the paper, so there is no second pass."
    )
```

used as the front pass's `reload_instruction`.

**Chosen:** the pass planner decides, because it is already the one place
that decides how many passes there are and what to say between them.
**Rejected:** `PrintSession` skipping an empty pass, which would leave
`plan_passes` claiming a pass that never runs and would put the same rule in
two places.

This is enough to make the print dialog collapse: `PrintSession` builds
`self._passes` from `plan_passes` and `PrintDialog._drive` only prompts a
reload when `session.state["pass_index"]` changes, which it never does with
one pass. No change in either file.

### 3.6 `deckle/cli.py`

- `_strategy_for`: `if settings.fold_scheme == "french": return FrenchFoldStrategy()`.
- `--fold-scheme` choices gain `"french"`; help gains *"'french' (French
  fold — printed on one side only, folded printed-face-out, bound through
  the open edge)"*.
- `_cmd_export`: refuse a back pass on a single-sided document, before any
  work, next to the existing `--pass`/`--profile` check at lines 976-986:

```python
    if args.pass_side == "back" and all(s.back is None for s in plan.sheets):
        print(
            "error: this document prints on one side of the paper, so there "
            "is no back pass. Export it without --pass, or with --pass front.",
            file=sys.stderr,
        )
        return 1
```

  Without this, `_sides(sheet, "back")` returns `[None]` per sheet and the
  export writes a PDF of blank pages that passes `_verify_output` — the
  "a file that exists and is empty, from a command that reported success"
  failure `_report_missing_sheets`'s docstring names.

### 3.7 `deckle/core/schedule.py`

`format_schedule_text` currently has two branches: folio, and everything
else. Add a third, chosen on `schedule.fold_scheme == "french"`, before the
existing `!= "folio"` test at line 350:

```
This document prints on ONE side of the paper. There is no second pass and
no reload.

Sheets to print: {sheets_total}

FOLDING
-------
  Lay each sheet printed side up, landscape.
  Fold the right half BEHIND the left half, so the printed side stays on
  the outside and the blank side is hidden inside the fold.
  The crease is the FORE-EDGE. The two edges that meet are the SPINE.

  Stack the folded sheets in order, folds all at the fore-edge, and bind
  through the open edge -- stab-sewn, or glued.

  No sewing stations are marked: a French fold is bound through the open
  edge, not through a fold, so the holes go through the whole block and are
  measured from the finished spine.
```

followed by `_printer_lines(schedule)` with its duplex paragraph suppressed —
add a `single_sided: bool = False` field to `Schedule`, set from
`all(s.back is None for s in plan.sheets)` in `build_schedule`, and skip the
`Duplex:` block when it is true. Telling someone which edge to flip a stack
they will never reload is the same defect as a control that does nothing.

### 3.8 `deckle/app`

- `layout_panel.py:45` `FOLD_SCHEMES` gains `"french"`;
  `set_fold_scheme`'s `Literal` annotation (line 300) widens; the tab→scheme
  mapping gains a fourth tab, `"French fold"`, with a hint label mirroring
  the other two:

```
"Printed on ONE side of the paper. Each sheet is folded printed-face-out, "
"so the fold is the fore-edge and the blank side is hidden inside. Bind "
"through the open edge -- stab-sewn, or glued.\n\n"
"No reload, no flip, no second pass, and no show-through on thin paper. "
"It costs exactly twice the paper."
```

  Sewing stations, sheets-per-signature, gatherings and blank mode are all
  meaningless here; `_sync_signature_tab` (line 1344) already disables the
  signature controls off-tab, so extend its predicate rather than adding a
  second one.
- `preview_view.py:226` — the cell-guide branch. French fold has two cells
  like folio but the **opposite** spines; add the branch rather than widening
  folio's condition, because getting the spine wrong here would draw a guide
  that disagrees with the exported sheet and the disagreement is invisible.
- `preview_view.py:118-122` and `:884-892` — the two places that enumerate
  `("front", sheet.front), ("back", sheet.back)`. Confirm a `None` back
  produces **no frame at all** rather than a blank one; a phantom back page
  in the preview of a single-sided job is a wrong picture of the artefact,
  which is the one thing the preview promises not to be.
- `render.py:234` — read what `has_back` does and confirm a back-less sheet
  rasterises one page.

### 3.9 Docs

GUIDE: a new path section, "Path D — French fold", with the fold prose, the
"twice the paper" trade, and the show-through argument. GUIDE §8's
`--fold-scheme` row. README feature table and CHANGELOG.

## 4. Tests

New file `tests/test_layout_french.py`.

1. `test_each_sheet_carries_two_consecutive_pages_in_order`
   Impose 8 pages: 4 sheets; sheet `j`'s front pages are source indices
   `2j`, `2j+1`. Unfixed: `ImportError: cannot import name
   'FrenchFoldStrategy'`.

2. `test_every_sheet_has_no_back`
   `all(s.back is None for s in plan.sheets)`. **The first plan in Deckle's
   history for which this is true.**

3. `test_the_spine_is_on_the_outer_cell_edges`
   Through `content_box_rect_pt(settings, spine_side=..., cell=...)` or
   `actual_margins_pt`: with `gutter_pt=72` and `margin_outer_pt=0`, the left
   cell's content starts 72pt from `x = 0` and the right cell's content ends
   72pt before `x = paper_w`. **This is the assertion that distinguishes a
   French fold from a folio**; if it is written the other way round the test
   passes on `SaddleStitchStrategy` and proves nothing.

4. `test_binding_edge_right_mirrors_the_spines`
   The exact inverse of test 3.

5. `test_neither_cell_is_rotated`
   Every `placement.rotate_deg == 0`. A French fold's fold is about a
   vertical axis and leaves "up" alone.

6. `test_an_odd_page_count_gets_one_trailing_blank`
   7 pages → 4 sheets, the last sheet's second page `is_filler`, and one
   `signature_padding` warning.

7. `test_no_sewing_stations_or_order_marks_are_drawn`
   Even with `sewing_stations=3`: no mark of kind `sewing_station` or
   `signature_order` anywhere in the plan. Exactly one `fold_line` per sheet.

8. `test_cut_lines_are_head_and_tail_only`
   With `trim_pt=18`: two `cut_line` marks, both horizontal. The fore-edge is
   the fold and the spine is bound; cutting either destroys the book.

9. `test_portrait_paper_warns`
   Letter portrait emits `sheet_orientation` naming `french`.

10. `test_the_plan_has_no_signatures`
    `plan.signatures == ()`. Nothing is gathered.

**Printing**

11. `test_a_single_sided_plan_gets_one_pass` — in `tests/test_printing.py`.
    `len(plan_passes(french_plan, profile)) == 1`, its `side == "front"`, and
    its `reload_instruction` contains `"no second pass"`. Unfixed: the
    assertion fails with `2 != 1`.

12. `test_a_two_sided_plan_still_gets_two_passes`
    The regression guard for every existing plan.

13. `test_a_subset_of_a_two_sided_plan_still_gets_two_passes`
    `sheets=[0]` on a folio plan. Guards against the `all(...)` predicate
    reading an empty or wrong index set.

14. `test_the_print_dialog_never_prompts_a_reload_for_a_single_sided_plan` —
    in `tests/test_print_dialog.py`, with the real `plan_passes` shape driven
    through the stub session: `confirm_reload` is never called.

**Export and the CLI**

15. `test_a_both_faces_export_writes_one_page_per_sheet` — in
    `tests/test_export.py`. 4 sheets → 4 PDF pages, and `_verify_output`
    accepts it.

16. `test_a_back_pass_of_a_single_sided_document_is_refused` — in
    `tests/test_cli.py`. `--fold-scheme french --pass back --profile
    generic_face_down_reversed` exits 1 with
    `"there is no back pass"` on stderr and **no file written**. Unfixed the
    CLI writes a PDF of blank pages and prints `wrote ...`.

17. `test_fold_scheme_french_is_accepted`
    `--fold-scheme french` exits 0.

**The absent-face audit** — new file `tests/test_absent_back_face.py`,
one test per consumer found by the grep in §2:

18. `test_the_schedule_reports_no_back_pages`
19. `test_the_plan_hash_distinguishes_a_missing_back_from_a_blank_one`
    Two plans identical but for `back=None` versus a filler `Side` hash
    differently — under both `export._plan_hash` and
    `print_session._hash_plan`. This is what stops a resumed session from
    treating a re-imposed single-sided job as the same document.
20. `test_render_sheet_rasterises_one_page_for_a_back_less_sheet`
21. `test_locate_finds_pages_on_a_back_less_sheet`
22. `test_the_preview_shows_one_frame_per_back_less_sheet` — headless, via
    `preview_view`'s frame enumeration, not by painting.
23. `test_a_print_session_over_a_single_sided_plan_finishes_after_one_pass`
    Drive a real `PrintSession` with a stub backend to completion; assert
    `finished` after one pass and that the backend was never asked for a
    `side="back"` submission.

24. In `tests/test_settings_roundtrip.py`:
    `test_french_survives_a_deckle_round_trip`.

## 5. Acceptance

| Check | Command |
|---|---|
| French layout tests pass | `.venv/bin/python -m pytest -q tests/test_layout_french.py` |
| The spine assertion specifically | `.venv/bin/python -m pytest -q -k spine_is_on_the_outer_cell_edges` |
| The absent-face audit | `.venv/bin/python -m pytest -q tests/test_absent_back_face.py` |
| One pass, and only for single-sided plans | `.venv/bin/python -m pytest -q -k "single_sided_plan_gets_one_pass or two_sided_plan_still_gets_two_passes"` |
| A both-faces export is one page per sheet | `.venv/bin/python -m deckle.cli dummy -o /tmp/f8.pdf --pages 8 --page-size 5.5x8.5in && .venv/bin/python -m deckle.cli export /tmp/f8.pdf -o /tmp/f8out.pdf --fold-scheme french --paper letter --landscape && .venv/bin/python -c "import pikepdf;assert len(pikepdf.open('/tmp/f8out.pdf').pages)==4"` |
| A back pass is refused | `! .venv/bin/python -m deckle.cli export /tmp/f8.pdf -o /tmp/f8b.pdf --fold-scheme french --paper letter --landscape --pass back --profile generic_face_down_reversed` |
| The model no longer claims nothing produces `back=None` | `! grep -q "No strategy currently produces" deckle/core/models.py` |
| Folio and gutter shift unchanged | `.venv/bin/python -m pytest -q tests/test_layout.py tests/test_layout_saddle.py tests/test_side_reporting.py` |
| The folio verdict is in and confirmed | `grep -A2 "^## Reading order" docs/verification/*-folio-dummy.md \| grep -q "^Verdict: confirmed"` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| **[HUMAN] fold a French dummy** | `deckle dummy -o /tmp/f8.pdf --pages 8 --page-size 5.5x8.5in`, export `--fold-scheme french --paper letter --landscape --gutter 0.75in`, print the one pass, fold each sheet by §3.1's steps, stack, and read 1→8. **Then check the gutter:** the wide margin must be at the *open* edge on every leaf. If it is at the *fold*, the spine assignment is folio's and every leaf's text runs into the binding. Record it in `docs/verification/2026-MM-DD-french-dummy.md` with F4's four-verdict shape (the awl and staircase sections read `not applicable`). |
| **[HUMAN] show-through** | Print one sheet on the thinnest stock you own and confirm the folded leaf hides the blank side. This is half the feature's stated value and costs one sheet. |

Greps run against the current tree:

```
$ grep -c "No strategy currently produces" deckle/core/models.py
1
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/f.pdf --fold-scheme french
deckle export: error: argument --fold-scheme: invalid choice: 'french' (choose from 'none', 'folio')
```

## 6. Out of scope

- **Stab-binding hole patterns.** A French fold is bound through the open
  edge, and the holes are a line inset from the finished spine, not ticks
  straddling a fold. That is **N5** (`--stations 0.5in,2in,...`), and it
  applies to more than this scheme.
- **F7 quarto**, if it has not landed. F8 does not need the 2×2 grid.
- **B19, B1, B2.** F8 emits `rotate_deg=0` everywhere, so none of the
  rotation bugs are on its path.
- **N4** — exporting a single pass from the GUI. A French fold has only one
  pass, so the button is not what makes it printable.
- **Duplex hardware (F9).** A single-sided job never wants a duplexer;
  `plan_passes` returning one pass is the whole interaction.
- **Cut-and-stack, perfect binding.** Different physical operations.

## 7. decisions.md entry

```
## 2026-09-05 — French fold: the first plan with no back face
- Symptom: `--fold-scheme {none,folio}` only, so the one scheme that removes the manual-duplex reload entirely -- no flip convention, no pass-2 misregistration, and no show-through on cheap paper, at exactly double the paper -- was not offered. `Sheet.back` had been `Side | None` since SS-01 and its docstring said outright that no strategy produced `None`.
- Fix: `FrenchFoldStrategy` in `layout.py`, reusing `cell_geometry` with the spines on the OUTER cell edges instead of at the fold, `Sheet.back=None`, empty `signatures`, and consecutive pairs for an ordering. `plan_passes` now returns ONE pass when every selected sheet has no back, so the print dialog collapses with no change of its own; `deckle export --pass back` on such a document is refused rather than writing a PDF of blank pages that reports success.
- Surfaces: this is the first producer of an absent back face, so ten consumers of `sheet.back` became reachable at once -- two plan hashes, the schedule, the locator, the renderer, two preview enumerations and the pass planner. Each got a test; none needed a fix, which is what the field's careful docstring was for.
- Watch: the one defect this strategy can have is folio's spine assignment. Folio folds printed-face-IN so its spines meet at the fold; a French fold folds printed-face-OUT so its spines are the sheet's outer edges. Both look correct on screen and only the folded dummy tells them apart, which is why the gutter check is a named [HUMAN] row.
- Commit: <fill in>
```

## 8. Traps

- **The spine is on the outer edges.** This is the inverse of folio and it is
  the only thing F8 can get wrong. A folio-shaped spine assignment produces a
  plan that renders, exports, previews and passes every arithmetic test, and
  puts the gutter at the fore-edge of every leaf.
- **`Sheet.back is None` has never happened before.** Work the grep in §2 and
  test every consumer, including the two in `preview_view.py`. A phantom
  blank back frame in the preview is a wrong picture of the artefact.
- **`_sides(sheet, "back")` pads with a blank** — deliberately, so a
  manual-duplex pass stays in register. On an all-single-sided plan that
  produces a document of blank pages that `_verify_output` accepts. The CLI
  guard in §3.6 is the only thing between a user and printing it.
- **Do not "fix" `_sides`.** Its padding branch is right for a mixed plan and
  the two consumers genuinely want opposite things; the docstring says so.
- **`plan_passes` is described as a frozen seam** (`cli.py:451-461`: *"a
  second implementation of the ordering table would be free to disagree"*).
  Changing the *number* of passes is a real change to that seam, and it is
  the right place — but it must be the only place. Do not also add a skip in
  `PrintSession`.
- **`fold_reading_order` returns the identity here and that check is
  vacuous.** Say so in the docstring. Two derivations that cannot disagree
  are not two derivations, and letting the round-trip test look green for
  French fold would be the *exact* failure mode the folio design is built to
  prevent, dressed as reassurance.
- **`Schedule` gains a field.** It is a frozen dataclass with a default;
  check `tests/test_schedule.py` for a field-count assertion before adding.
- **`grain_warning`'s `folded` flag** picks the word "fold" or "spine". A
  French fold has a fold, so `"french"` belongs in that set.
- **`python -m deckle` launches the GUI and blocks.**
