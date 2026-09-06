# F10 — Show what the folded book reads, before any paper is committed

**Roadmap item:** `docs/ROADMAP.md` F10
**Depends on:** — (works with folio today; picks up quarto and French fold
for free when F7 and F8 widen `fold_reading_order`)
**Blocks:** —
**Size:** S
**Decision needed first:** none.

---

## 1. Context

`fold_reading_order` exists, is pure, is tested, and **nothing outside the
test suite calls it**:

```
$ grep -rn "fold_reading_order" deckle/
deckle/core/signatures.py:17,19,21   (module docstring)
deckle/core/signatures.py:154:def fold_reading_order(plan: SheetPlan) -> list[int]:
```

Every other hit is in `tests/`.

The signatures-v2 design lists this as open question 5:

> **Should the fold simulator ship, or stay a test fixture?** As a UI feature
> it is a "read the book back" preview — **arguably the most reassuring thing
> the app could show** before committing 67 sheets. **Changes:** a preview
> mode; no core change, since the function is pure either way.

The concrete situation it addresses: folio ships marked experimental, GUIDE
§5 opens with *"What is **not** verified is the page *ordering*"*, and the
Signatures tab's own hint says *"Experimental: the page ordering is
hand-written arithmetic. Save the schedule, print onto scrap, fold it, and
check it reads correctly before committing a real book."* The app tells the
user to go and check something it could show them in a line of text.

**What F10 does not claim.** It does not verify the ordering — F4 does that,
with paper. It shows what the second, independent derivation says the folded
gathering will read, so a wrong `sheets_per_signature`, a stray
`--signatures 10,10,8`, or a document imposed with `start_on_recto=False` is
visible before printing rather than after folding.

## 2. Current code

`deckle/core/signatures.py:154-203` — the function, in full, including the
paragraph that says why its independence matters:

```python
def fold_reading_order(plan: SheetPlan) -> list[int]:
    """The reading-order page index for every physical print slot in ``plan``.

    Derived independently of ``saddle_order`` -- deliberately not by
    evaluating the same closed-form arithmetic under a different name, but
    by simulating the physical fold: each signature is a stack of sheets,
    outermost to innermost. ...

    Signatures are walked in ``plan.signatures`` order and their resulting
    slot orders concatenated, each offset by the page count of every
    signature already placed, so this function's output lines up 1:1 with
    ``saddle_order``'s per-signature output when the two are compared.

    :param plan: the imposed plan. Only ``plan.signatures`` is read -- a
        plan with no signatures yields an empty list.
    :returns: the reading-order page index for every physical print slot,
        concatenated across signatures in binding order.
    """
    order: list[int] = []
    offset = 0
    for signature in plan.signatures:
        sheet_count = len(signature.sheet_indices)
        page_count = 4 * sheet_count
        remaining = deque(range(offset, offset + page_count))
        while remaining:
            front_outer_edge = remaining.pop()
            front_spine_edge = remaining.popleft()
            back_spine_edge = remaining.popleft()
            back_outer_edge = remaining.pop()
            order += [
                front_outer_edge,
                front_spine_edge,
                back_spine_edge,
                back_outer_edge,
            ]
        offset += page_count
    return order
```

**A plan with no signatures yields an empty list**, so `fold_scheme="none"`
needs no special case at the call site.

`deckle/core/schedule.py:134-146` — how the schedule turns an
`OutputPage` into a number, and the bug it carries (B7):

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

`source_ref.page_index + 1` is the *file's* page number, which is not the
reader's whenever pages are skipped, blanks are inserted, or more than one
source is imported. **B7.** F10 must reuse this function rather than
inventing a second numbering — one wrong number is a bug, two disagreeing
wrong numbers is a mystery.

`deckle/core/schedule.py:410-441` — the tail of `format_schedule_text`, where
F10's section goes:

```python
    lines.append("AFTER SEWING")
    lines.append("-" * len("AFTER SEWING"))
    lines.append("  Stack the signatures in order, 1 first.")
    if schedule.spine_width_pt is not None:
        ...
    if schedule.notes:
        lines.append("")
        for note in schedule.notes:
            lines.append(f"  Note: {note}")
    lines.append("")
    return "\n".join(lines) + "\n"
```

`deckle/core/schedule.py:201-212` — `build_schedule`'s contract, which F10
extends by one field:

```python
def build_schedule(plan: SheetPlan, settings: LayoutSettings) -> Schedule:
    """Derive a binding schedule from an imposed plan.

    :param plan: the plan the exporter used. Read, never recomputed.
    """
```

`deckle/app/views/layout_panel.py:1155-1166` — the readout F10's label sits
beneath:

```python
        # Live readout -- "17 signatures · 67 sheets · 2 blanks" -- derived
        # from the recomputed SheetPlan, since that arithmetic is the thing
        # a binder actually decides on.
        self.binding_readout_label = QLabel("", self.widget)
        self.binding_readout_label.setToolTip(
            "What your current settings actually produce, recomputed live. "
            "This is the arithmetic a binder decides on -- how many "
            "gatherings to sew, how much paper to cut, and how many blank "
            "pages the fold count forced."
        )
        signature_form.addRow("Binding:", self.binding_readout_label)
        self.binding_readout_label.setText(binding_readout_str(recompute_plan(state.project)))
```

`deckle/app/views/layout_panel.py:586-602` and the update path:

```python
def binding_readout_str(plan: SheetPlan) -> str:
    """Render the binding readout, e.g. ``"17 signatures · 67 sheets · 2 blanks"``.
    ...
    """
    blank_count = sum(sig.blank_count for sig in plan.signatures)
    return (
        f"{len(plan.signatures)} signatures · {len(plan.sheets)} sheets · "
        f"{blank_count} blanks"
    )
```

```python
    def _refresh_binding_readout(self, plan: SheetPlan) -> None:
        self.binding_readout_label.setText(binding_readout_str(plan))
```

called from ten sites (`layout_panel.py:1286, 1545, 1559, 1582, 1599, 1654,
1669, 1680, 1699, 1704`), so one label added there updates from every
control.

### Existing tests

`tests/test_signatures.py` (`fold_reading_order` directly),
`tests/test_layout_saddle.py:83`, `tests/test_integration_signatures.py:236`
(the round trip), `tests/test_spec_residue.py:175` (the independence guard),
`tests/test_schedule.py`, `tests/test_layout_panel_widgets.py`,
`tests/test_ui_surface.py`.

## 3. Change

One pure function in `deckle/core/schedule.py`, one field on `Schedule`, one
section in the schedule text, and one label on the Signatures tab.

### 1. `deckle/core/schedule.py` — the pure readout

```python
def read_back_order(plan: SheetPlan) -> tuple[tuple[int | None, ...], ...]:
    """What each signature reads, once folded and nested, one tuple per gathering.

    Derived through :func:`deckle.core.signatures.fold_reading_order`, which
    answers "which reading position does this physical slot land in" by
    simulating the fold rather than by re-evaluating ``saddle_order``. This
    function only re-labels its answer as the page numbers a person sees.

    Entries are 1-based source page numbers, ``None`` for a blank, in the
    order a reader turns the leaves. A plan with no signatures returns
    ``()`` -- flat sheets are not folded, so there is nothing to read back.

    Page numbers come from :func:`_page_numbers`'s convention -- the source
    file's own page number -- so a document with skipped pages or more than
    one source shows file numbers here exactly as it does in the gathering
    table above. That is B7, and it is one wrong number rather than two
    disagreeing ones.
    """
```

Body:

```python
    if not plan.signatures:
        return ()
    slots = [
        page
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
        for page in side.pages
    ]
    order = fold_reading_order(plan)
    read: list[OutputPage | None] = [None] * len(order)
    for slot_index, position in enumerate(order):
        if slot_index < len(slots) and position < len(read):
            read[position] = slots[slot_index]
    numbers = tuple(
        None if page is None or page.source_ref is None
        else page.source_ref.page_index + 1
        for page in read
    )
    # Split back into gatherings so a 67-sheet book reads as 17 short lines
    # rather than one line of 1072 numbers.
    out: list[tuple[int | None, ...]] = []
    start = 0
    for signature in plan.signatures:
        length = 4 * len(signature.sheet_indices)
        out.append(numbers[start : start + length])
        start += length
    return tuple(out)
```

The `4 *` is folio's pages-per-sheet. **When F7 lands**, replace it with
`2 * len(sheet.front.pages)` read off the plan, matching the same inference
`fold_reading_order` makes; until then a comment naming F7 is enough.

The bounds checks in the loop are deliberate: `fold_reading_order` and the
slot list are two derivations of one length, and a mismatch must produce a
short readout rather than an `IndexError` inside a schedule the user asked
for.

### 2. `Schedule` gains one field

```python
    read_back: tuple[tuple[int | None, ...], ...] = ()
```

with `:ivar read_back:` — *"What each gathering reads once folded, from the
fold simulator. Empty under ``fold_scheme="none"``. Carried here rather than
computed by the formatter, per this module's describe-never-re-derive
rule."*

`build_schedule` sets `read_back=read_back_order(plan)`.

### 3. The schedule section

In `format_schedule_text`, immediately **before** `AFTER SEWING` (so it is
the last thing read before the work starts, and the last thing on screen when
someone scrolls):

```python
    if schedule.read_back:
        lines.append("READ IT BACK")
        lines.append("-" * len("READ IT BACK"))
        lines.append(
            "  Folded and nested as above, each gathering reads out in this"
        )
        lines.append("  order. If it is not the order you expect, stop here.")
        lines.append("")
        for index, pages in enumerate(schedule.read_back, start=1):
            lines.append(f"    signature {index}:  {_format_read_back(pages)}")
        lines.append("")
        lines.append(
            "  This is the fold simulator, not a measurement: it works the"
        )
        lines.append(
            "  order out a second time from how the paper folds, rather than"
        )
        lines.append(
            "  from the arithmetic that imposed the sheets. Two derivations"
        )
        lines.append(
            "  that agree are worth having and are still not paper. Fold a"
        )
        lines.append("  numbered dummy before a real book -- deckle dummy.")
        lines.append("")
```

with:

```python
#: How many leading and trailing positions a read-back line shows before it
#: elides. Eight either side fits an 80-column bench sheet and shows both
#: ends of a gathering, which is where a fold order goes wrong.
READ_BACK_SHOWN = 8


def _format_read_back(pages: tuple[int | None, ...]) -> str:
    """A gathering's reading order, elided in the middle when long."""
    def name(value: int | None) -> str:
        return "blank" if value is None else str(value)

    if len(pages) <= 2 * READ_BACK_SHOWN:
        return "  ".join(name(p) for p in pages)
    head = "  ".join(name(p) for p in pages[:READ_BACK_SHOWN])
    tail = "  ".join(name(p) for p in pages[-READ_BACK_SHOWN:])
    return f"{head}  ...  {tail}"
```

**Chosen:** per-gathering lines, elided in the middle. **Rejected:** one line
of every position (1072 numbers for the 67-sheet working case) and a
computed "in order / not in order" verdict — the verdict would have to be
derived from page numbers, and those are B7's numbers, so a document with
skipped pages would get a confident "OUT OF ORDER" that is a numbering bug
and not a fold bug. Showing the sequence and letting a person read it says
exactly as much as is true.

### 4. The Signatures-tab label

In `layout_panel.py`, a pure formatter beside `binding_readout_str`:

```python
def read_back_str(plan: SheetPlan) -> str:
    """One line of what the first gathering reads, for the Signatures tab.

    The first signature only: every gathering has the same shape, and a
    panel label is not a schedule. Empty under ``fold_scheme="none"``,
    where there is nothing folded to read.
    """
    order = read_back_order(plan)
    if not order:
        return ""
    return "reads " + _format_read_back(order[0])
```

`_format_read_back` is imported from `deckle.core.schedule` — the panel
already imports `build_schedule` and `format_schedule_text` from there
(`layout_panel.py:39`), so this adds no new dependency direction. Rename it
`format_read_back` (public) since a second module now uses it.

Widget, added directly under `binding_readout_label` in the constructor:

```python
        self.read_back_label = QLabel("", self.widget)
        self.read_back_label.setWordWrap(True)
        self.read_back_label.setToolTip(
            "What the first gathering will read once it is folded and "
            "nested, worked out a second time from how the paper folds "
            "rather than from the arithmetic that imposed it.\n\n"
            "Two derivations that agree are worth having and are still not "
            "paper. Fold a numbered dummy before committing a real book."
        )
        signature_form.addRow("Read back:", self.read_back_label)
        self.read_back_label.setText(read_back_str(recompute_plan(state.project)))
```

Row label exactly `"Read back:"`.

`_refresh_binding_readout` gains one line:

```python
    def _refresh_binding_readout(self, plan: SheetPlan) -> None:
        self.binding_readout_label.setText(binding_readout_str(plan))
        self.read_back_label.setText(read_back_str(plan))
```

That is the whole update path — all ten existing call sites pick it up.

**Do not add the label to `refresh_from_project`'s hand-maintained
block-list.** It is read-only and has no signal to block; adding it there is
how B12's list grew.

### 5. Docs

- GUIDE §7 ("Reading a binding schedule") gains a short paragraph for the
  new section, with the same two sentences about what it is and is not.
- GUIDE §5's "Check 1 — read the schedule against a manual" gains a pointer:
  the READ IT BACK block is the same check without a manual.
- No new module, so no `docs/api/*.rst` change.

## 4. Tests

New tests in `tests/test_schedule.py`:

1. `test_a_sixteen_page_folio_reads_one_to_sixteen`
   Impose 16 pages of a single source at 4 sheets per signature;
   `read_back_order(plan) == ((1, 2, 3, ..., 16),)`. Unfixed:
   `ImportError: cannot import name 'read_back_order'`.

2. `test_two_signatures_read_back_as_two_tuples`
   32 pages at 4 sheets per signature → two tuples of 16, the second running
   17-32.

3. `test_a_padded_signature_reports_its_blanks_as_blanks`
   14 pages → the last two positions are `None`, and
   `format_read_back` renders them as `blank`.

4. `test_flat_sheets_read_back_as_nothing`
   `fold_scheme="none"` → `read_back_order(plan) == ()`, and the formatted
   schedule contains no `READ IT BACK` heading. Nothing is folded, so there
   is nothing to read back — and the non-folio branch of
   `format_schedule_text` returns early at line 350, so this also guards
   against putting the block above that return.

5. `test_a_long_gathering_is_elided_in_the_middle`
   A 10-sheet signature (40 pages): the line shows the first 8 and last 8
   with `...` between, and contains `40` — the last page of the gathering is
   the one a fold order gets wrong.

6. `test_the_schedule_text_carries_the_read_back_block`
   `format_schedule_text` output contains `"READ IT BACK"`,
   `"If it is not the order you expect, stop here."` and
   `"deckle dummy"`.

7. `test_the_read_back_block_sits_before_after_sewing`
   `text.index("READ IT BACK") < text.index("AFTER SEWING")`. Position is
   the point: it is the last check before the work starts.

8. `test_read_back_survives_a_slot_count_mismatch`
   Hand-build a plan whose `signatures` claim more sheets than
   `plan.sheets` holds; `read_back_order` returns a short readout and does
   not raise. A schedule the user asked for must not die of an internal
   disagreement.

9. `test_read_back_uses_the_same_numbering_as_the_gathering_table`
   Impose a document with a skipped page; the numbers in `read_back` are
   drawn from the same `source_ref.page_index + 1` convention as
   `_page_numbers`. Pins that F10 did not invent a second numbering — it
   inherits B7 rather than adding a disagreeing sibling.

New tests in `tests/test_ui_surface.py` (or
`tests/test_layout_panel_widgets.py`, whichever already builds a panel):

10. `test_the_signatures_tab_shows_what_the_gathering_reads`
    Build a panel over a 16-page folio project;
    `panel.read_back_label.text()` starts `"reads 1  2  3"`.
    Unfixed: `AttributeError: 'LayoutPanel' object has no attribute
    'read_back_label'`.

11. `test_the_read_back_label_is_empty_for_flat_sheets`
    `fold_scheme="none"` → `""`.

12. `test_changing_sheets_per_signature_updates_the_read_back_label`
    Drive `sheets_per_signature_spinbox` from 4 to 2 and assert the label
    changed. This is the reassurance the feature exists for: the user sees
    the consequence of the control they just moved.

13. `test_refreshing_from_a_project_does_not_change_the_undo_stack`
    Extend the existing refresh test rather than adding a new one if it
    already exists: the new label is read-only and must not join the
    block-list (B12).

## 5. Acceptance

| Check | Command |
|---|---|
| Schedule tests pass | `.venv/bin/python -m pytest -q tests/test_schedule.py` |
| Panel tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_ui_surface.py tests/test_layout_panel_widgets.py` |
| A real schedule shows it | `.venv/bin/python -m deckle.cli dummy -o /tmp/f10.pdf --pages 16 --page-size 5.5x8.5in && .venv/bin/python -m deckle.cli schedule /tmp/f10.pdf --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \| grep -A4 "READ IT BACK"` |
| It reads 1..16 for the dummy | `.venv/bin/python -m deckle.cli schedule /tmp/f10.pdf --fold-scheme folio --sheets-per-signature 4 --paper letter --landscape \| grep -q "signature 1:  1  2  3  4  5  6  7  8  9  10  11  12  13  14  15  16"` |
| Flat sheets say nothing | `! .venv/bin/python -m deckle.cli schedule tests/fixtures/sample.pdf \| grep -q "READ IT BACK"` |
| The fold simulator has a non-test caller | `grep -rn "fold_reading_order" deckle/core/schedule.py` |
| The independence guard still holds | `.venv/bin/python -m pytest -q tests/test_spec_residue.py` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| [HUMAN] It reassures | Open the app, import 16 pages, switch to Signatures. The "Read back:" line reads `reads 1 2 3 … 16`. Change sheets per signature and watch it change. |

Greps run against the current tree:

```
$ grep -rn "fold_reading_order" deckle/
deckle/core/signatures.py:17:- ``fold_reading_order`` -- the same question, answered independently by
deckle/core/signatures.py:19:  arithmetic. ``saddle_order`` and ``fold_reading_order`` must never share
deckle/core/signatures.py:21:  ``fold_reading_order`` encoded identically, the round trip between them
deckle/core/signatures.py:154:def fold_reading_order(plan: SheetPlan) -> list[int]:
$ .venv/bin/python -m deckle.cli schedule /tmp/f10.pdf --fold-scheme folio \
      --sheets-per-signature 4 --paper letter --landscape | grep -c "READ IT BACK"
0
```

(The 1..16 acceptance row's exact spacing was taken from
`_format_pages`'s two-space join; run the command once and paste the real
line if the separator differs.)

## 6. Out of scope

- **B7** — the schedule's page numbers being file indices rather than
  reader-facing numbers. F10 deliberately reuses the same convention so
  there is one wrong number rather than two disagreeing ones. Fixing it fixes
  both at once.
- **F4** — folding paper. F10 shows a second derivation; it does not verify
  anything, and its own text says so.
- **Removing "experimental"** from the Signatures hint. That is F4's, and
  putting a reassuring line beside the warning must not be mistaken for
  earning the right to delete the warning.
- **A graphical fold preview** (turning leaves on screen). The design doc
  calls the feature a "preview mode"; a line of numbers delivers the
  reassurance for a fraction of the surface, and a picture can be built on the
  same pure function later.
- **N12** — the Save schedule button living only on the Signatures tab.
- **F7 / F8.** `read_back_order`'s `4 *` becomes an inference off the plan
  when quarto lands; a comment marks the spot.

## 7. decisions.md entry

```
## 2026-09-05 — The fold simulator now says what the book will read
- Symptom: `fold_reading_order` was pure, tested, and called by nothing outside the test suite, while the app told users to "save the schedule, print onto scrap, fold it, and check it reads correctly" -- a check it could have shown them in one line. The v2 design had it as open question 5 and called it "arguably the most reassuring thing the app could show".
- Fix: `read_back_order` in `schedule.py` re-labels the simulator's slot order as the page numbers a reader sees, split per gathering; a "READ IT BACK" block in the schedule text just before AFTER SEWING; a "Read back:" line on the Signatures tab that updates from every control through the existing `_refresh_binding_readout`.
- Surfaces: it reuses `_page_numbers`' numbering, which is the source file's page number and therefore wrong whenever pages are skipped (B7). Deliberate: one wrong number is a bug, two disagreeing wrong numbers is a mystery. There is no computed "in order" verdict for the same reason -- a verdict derived from B7's numbers would report a numbering bug as a fold bug.
- Watch: this is a second derivation, not a measurement, and both the schedule block and the tooltip say so and point at `deckle dummy`. Two derivations of a wrong physical assumption agree with each other; the folded dummy is still the gate.
- Commit: <fill in>
```

## 8. Traps

- **`format_schedule_text` returns early at line 350** for
  `fold_scheme != "folio"`. The READ IT BACK block must go in the folio
  branch, after it — not before the return, where it would never run, and not
  above it, where it would print an empty heading for flat sheets.
- **`read_back_order` must not raise.** It reconciles two derivations of one
  length; on a mismatch it returns a short readout. A schedule that dies of
  an internal disagreement is worse than a schedule that is one line shorter.
- **Do not invent a second page numbering.** Reuse
  `source_ref.page_index + 1`.
- **Do not add a verdict.** With B7 unfixed, a "reads out of order" line
  would fire on any document with a skipped page and would be blamed on the
  imposition.
- **`fold_reading_order` reads only `plan.signatures`**, so a flat-sheet plan
  gives `[]` and needs no guard beyond the `if not plan.signatures` early
  return.
- **The new label is read-only**; keep it out of `refresh_from_project`'s
  block-list (B12) and out of `_on_unit_changed`'s box-list (B11). It has no
  unit and no signal.
- **`_refresh_binding_readout` has ten call sites.** One line there is the
  whole update path; do not add a second refresh helper.
- **`layout_panel` already imports from `deckle.core.schedule`** (line 39).
  Extend that import; do not reach into `deckle.core.signatures` from the
  panel, or the panel gains a second opinion about the fold.
- **`python -m deckle` launches the GUI and blocks.**
