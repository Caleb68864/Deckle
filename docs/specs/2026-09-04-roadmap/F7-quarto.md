# F7 — Quarto: four pages per side, eight per sheet

**Roadmap item:** `docs/ROADMAP.md` F7
**Depends on:**
- **F4 — hard gate.** The competitive-gaps research is explicit: *"Do not
  ship quarto until folio has been verified on paper. Verifying folio is the
  prerequisite."* Folio's ordering is one hand-written derivation checked
  against a second hand-written derivation; quarto multiplies that. Do not
  merge F7 while `docs/verification/*-folio-dummy.md`'s reading-order verdict
  is `untested` or `diverged`.
- **B2 — hard gate, and not recorded in the roadmap.** `_place_output_page`
  takes the rotation branch only for `rotate_deg in (90, 270)`; a `180` falls
  into the `else` and is **silently dropped**. Quarto's whole top row is a
  180° rotation, so on the current exporter half of every quarto sheet would
  print upside down with no error. See §2.
- **B1 / M2** are strongly advised in the same pass: `_place_page` is where
  the per-cell rotation has to be threaded, and it already drops
  `slot.rotate_deg`.

**Blocks:** F8 (French fold reuses the cell generalisation), and the
perfect-binding "2-up then cut" idea the gaps doc defers behind this one.
**Size:** L
**Decision needed first:** none, once F4 and B2 are closed.

---

## 1. Context

Quarto is *"the single biggest determinant of finished book size on a home
printer"* (competitive gaps #3). Letter folded once gives a 5.5 × 8.5 book and
eats a sheet every four pages; folded twice it gives 4.25 × 5.5 at half the
paper. For a long text at home that is a 100-sheet book versus a 50-sheet
book.

Deckle offers `--fold-scheme {none,folio}` and nothing else:

```
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/q.pdf \
      --fold-scheme quarto
usage: deckle export [-h] -o OUTPUT ...
                     source
deckle export: error: argument --fold-scheme: invalid choice: 'quarto' (choose from 'none', 'folio')
```

The gaps research also says the work is *"smaller than it looks"* because the
layout core is already cell-general — which is true of the **geometry** and
not of the two things that actually decide whether paper comes out readable:
the ordering table, and a 180° rotation the exporter cannot currently draw.

**The ordering table is derived here from folding a sheet, in §3.1, and is
not copied from anywhere.** Bookbinder JS's `PAGE_LAYOUTS`/`BOOKLET_LAYOUTS`
are MPL-2.0 (file-level copyleft) — read them if you like, paste nothing. The
derivation below stands on its own and the fold prose in §3.1 is what a human
checks it against with a sheet of scrap.

## 2. Current code

### The cell machinery, which genuinely is ready

`deckle/core/layout.py:40-50`:

```python
Cell = tuple[float, float, float, float]
"""A cell within a sheet: ``(x0, y0, x1, y1)`` in sheet points.

``GutterShiftStrategy`` passes the full sheet as its cell (the default,
``None``, means exactly that). ``SaddleStitchStrategy`` splits the sheet
into two cells at the fold -- see ``cell_geometry``.
"""


def _full_sheet_cell(paper: tuple[float, float]) -> Cell:
    return (0.0, 0.0, paper[0], paper[1])
```

`deckle/core/layout.py:708-721`:

```python
def cell_geometry(paper: tuple[float, float]) -> tuple[Cell, Cell]:
    """Split a sheet at its vertical centreline into the two folio cells.

    For letter landscape (792 x 612) this returns ``(0, 0, 396, 612)`` and
    ``(396, 0, 792, 612)`` -- matching the vault recipe's recorded
    ``1 0 0 1 0 0 cm`` / ``1 0 0 1 396 0 cm``.
    """
    paper_w, paper_h = paper
    fold_x = paper_w / 2.0
    return (0.0, 0.0, fold_x, paper_h), (fold_x, 0.0, paper_w, paper_h)
```

### The rotation the exporter drops — B2

`deckle/core/export.py:341-381`, verbatim. Note the branch condition:

```python
    placement = output_page.placement
    rotate_deg = placement.rotate_deg % 360

    if rotate_deg in (90, 270):
        # The final on-sheet footprint (tx/ty/width/height in Placement) is
        # already expressed post-rotation. Place the form at its natural
        # (pre-rotation) orientation centered on that same footprint, then
        # rotate the whole thing about the footprint's center ...
        scaled_w = src_w * placement.scale_x
        scaled_h = src_h * placement.scale_y
        footprint_w, footprint_h = scaled_h, scaled_w
        cx = placement.tx + footprint_w / 2.0
        cy = placement.ty + footprint_h / 2.0
        ...
        rotation = _rotation_matrix(rotate_deg, cx, cy)
        content_stream = f"q\n{rotation}\n{inner.decode('latin-1')}\nQ\n".encode("latin-1")
    else:
        rect = _rect_for_placement(placement, src_w, src_h)
        allow_shrink, allow_expand = _scale_flags_for(placement.scale_x)
        content_stream = dest_page.calc_form_xobject_placement(
            formx, name, rect, invert_transformations=True,
            allow_shrink=allow_shrink, allow_expand=allow_expand,
        )
    dest_page.contents_add(content_stream)
```

`180 % 360 == 180`, which is not in `(90, 270)`, so it takes the `else` and
draws the page **unrotated**. `_rotation_matrix` handles 180 correctly
(`cos=-1, sin=0` gives `-1 0 0 -1 2cx 2cy`); nothing calls it with 180.

### Where the folio strategy does the equivalent work

`deckle/core/layout.py:934-1033` — the per-sheet body of
`SaddleStitchStrategy.impose`. The parts F7 mirrors:

```python
        for sig_index, group in enumerate(groups):
            page_offset = group[0] * 4
            page_count = 4 * len(group)
            sig_slots = slots[page_offset : page_offset + page_count]
            signature_slices.append(sig_slots)
            sig_blank_count = sum(1 for s in sig_slots if s is None)

            order = saddle_order(page_count)

            for local_idx, sheet_index in enumerate(group):
                pos = local_idx * 4
                front_a, front_b = order[pos], order[pos + 1]
                back_a, back_b = order[pos + 2], order[pos + 3]
                ...
                if settings.binding_edge == "right":
                    cell_a, spine_a = cells[1], "left"
                    cell_b, spine_b = cells[0], "right"
                else:
                    cell_a, spine_a = cells[0], "right"
                    cell_b, spine_b = cells[1], "left"

                is_outermost = local_idx == 0
                is_innermost = local_idx == len(group) - 1

                front_marks: list = [fold_line(paper_h, fold_x)]
                back_marks: list = [fold_line(paper_h, fold_x)]
                folio_cuts = cut_lines(
                    paper_w, paper_h, settings.trim_pt, ("left", "right")
                )
                front_marks.extend(folio_cuts)
                back_marks.extend(folio_cuts)
                if is_innermost:
                    back_marks.extend(
                        sewing_stations(paper_h, fold_x, settings.sewing_stations)
                    )
                if is_outermost:
                    front_marks.append(
                        signature_order_mark(sig_index, sig_count, paper_h, fold_x)
                    )
```

`deckle/core/layout.py:346-364` and `483-502` — `_place_page`'s signature and
its placement tail, which is where the per-cell rotation has to be threaded:

```python
def _place_page(
    slot: SourcePage | None,
    output_index: int,
    settings: LayoutSettings,
    sheet_index: int,
    warnings: list[LayoutWarning],
    scale: float,
    *,
    cell: Cell | None = None,
    spine_side: Literal["left", "right"] | None = None,
) -> OutputPage:
```

```python
    if spine_side is not None:
        gutter_on_left = spine_side == "left"
    else:
        gutter_on_left = _gutter_side_is_left(_is_recto(output_index), settings.binding_edge)
    tx = cx0 + (inner_actual if gutter_on_left else cell_w - inner_actual - scaled_w)
    ty = cy0 + bottom_actual

    placement = Placement(
        scale_x=scale,
        scale_y=scale,
        tx=tx,
        ty=ty,
        rotate_deg=rotate_deg,
    )
```

and lines 403-410, the margins F7 must swap for a turned cell:

```python
    gutter = max(0.0, settings.gutter_pt)
    outer = max(0.0, settings.margin_outer_pt)
    top = max(0.0, settings.margin_top_pt)
    bottom = max(0.0, settings.margin_bottom_pt)

    box_w = cell_w - gutter - outer
    box_h = cell_h - top - bottom
```

### The two derivations that must stay independent

`deckle/core/signatures.py:120-151` (`saddle_order`, closed form) and
`154-203` (`fold_reading_order`, the peel). The module docstring, lines
17-23:

```
- ``fold_reading_order`` -- the same question, answered independently by
  simulating the physical fold instead of reusing the closed-form
  arithmetic. ``saddle_order`` and ``fold_reading_order`` must never share
  an implementation: if the closed form were subtly wrong in a way that
  ``fold_reading_order`` encoded identically, the round trip between them
  would falsely pass and the printed book would come out of order. Two
  independently-derived answers that agree are the only useful check.
```

`fold_reading_order`'s loop, lines 186-201, which assumes four pages per
sheet:

```python
        remaining = deque(range(offset, offset + page_count))

        # Peel one sheet's worth of leaves off both ends of the run per
        # loop: this sheet is outermost relative to whatever is left.
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
```

### The marks that need a second axis

`deckle/core/marks.py:32-63` (`sewing_stations`, hard-wired to
`0 .. sheet_h`), `66-87` (`signature_order_mark`, same), `90-148`
(`cut_lines`, head/tail plus vertical fore-edges only), `151-158`
(`fold_line`, vertical only).

### Everything that switches on `fold_scheme`

```
$ grep -rn "fold_scheme" deckle/ | grep -v "core/models.py"
deckle/cli.py:722:        fold_scheme=args.fold_scheme,
deckle/cli.py:736:    """Pick the imposition strategy named by ``settings.fold_scheme``.
deckle/cli.py:741:    if settings.fold_scheme == "folio":
deckle/cli.py:885:    # (fold_scheme="none") path, where there are simply zero signatures.
deckle/core/dummy.py:4:``fold_scheme="folio"`` ships marked experimental because its page
deckle/core/schedule.py:105:    :ivar fold_scheme: the imposition this schedule describes.
deckle/core/schedule.py:123:    fold_scheme: str
deckle/core/schedule.py:208:    Under ``fold_scheme="none"`` a plan has no signatures at all, and the
deckle/core/schedule.py:273:        fold_scheme=settings.fold_scheme,
deckle/core/schedule.py:350:    if schedule.fold_scheme != "folio" or not schedule.signatures:
deckle/app/views/preview_view.py:195,212,226,251
deckle/core/layout.py:275:    folded = settings.fold_scheme == "folio"
deckle/core/layout.py:361, 894
deckle/core/print_session.py:115  (docstring: "two under fold_scheme=\"folio\"")
deckle/app/views/layout_panel.py:191, 300, 304, 308, 575, 583, 597, 720,
    749, 1072, 1179, 1276, 1352, 1568, 1576, 1578, 1580, 1581
```

Note `deckle/cli.py:765` (`choices=["none", "folio"]`) and
`deckle/app/views/layout_panel.py:45` (`FOLD_SCHEMES`) do not contain the
string `fold_scheme` and are missed by that grep — find them with
`grep -rn "folio" deckle/`. `layout_panel.py:300`'s
`set_fold_scheme(project, fold_scheme: Literal["none", "folio"])` annotation
must widen too.

Run both greps yourself before starting — together they are the change list.

### Existing tests

`tests/test_layout_saddle.py` (the folio strategy, the mark predicate at
lines 470-501, the fold round trip at line 83), `tests/test_cell_geometry.py`,
`tests/test_signatures.py`, `tests/test_integration_signatures.py`,
`tests/test_dummy.py`, `tests/test_imposition_properties.py:260`,
`tests/test_spec_residue.py:175-200` (the "`fold_reading_order` must not use
`saddle_order`" monkeypatch guard — **F7's quarto version must be added
there**).

## 3. Change

### 3.1 The ordering table, derived from folding a sheet

**This section is the specification's core. Read it with a sheet of paper.**

**Paper orientation.** Two folds halve both dimensions, so a sheet `W × H`
yields leaves `W/2 × H/2`. For portrait leaves you need `H > W`: **quarto
wants PORTRAIT stock**, exactly inverting folio's landscape requirement.
Letter portrait (612 × 792) gives 306 × 396 pt leaves = 4.25 × 5.5 in, which
is the pocket size the gaps research names.

**The fold, stated as prose an operator can follow.** Commit this wording; it
goes in the schedule and the GUIDE.

> 1. Lay the sheet **printed side up, portrait**.
> 2. **Fold the top half down and behind** — take the top edge, carry it away
>    from you and down to the bottom edge, so the crease runs left-to-right
>    across the middle and the top half ends up *behind* the bottom half.
> 3. **Fold the left half behind the right half** — take the left edge, carry
>    it away from you and across to the right edge, so the crease runs
>    top-to-bottom and the left half ends up *behind*.
> 4. The packet is now four leaves. The **spine** is the second crease (the
>    vertical one, now the packet's left edge). The **head** is the first
>    crease (the horizontal one, now the packet's top edge) — it is a *bolt*
>    and must be slit **after sewing**, never before.

**Naming the eight surfaces.** Front-face quadrants `A` = top-left,
`B` = top-right, `C` = bottom-left, `D` = bottom-right. Back-page quadrants
`a` = top-left, `b` = top-right, `c` = bottom-left, `d` = bottom-right, in
the *back page's own* coordinates.

**The physical mirror.** The sheet is turned about its **vertical** axis
between passes — which is what `duplex_flip_edge` already says for a portrait
sheet (`"long"`, and a portrait sheet's long edge is the vertical one) and
what folio's existing geometry already assumes (its front-right cell carries
page 1 and its back-left cell carries page 2, which only lines up under an
x-mirror). So back-page point `(x, y)` lies physically behind front point
`(W − x, y)`:

| back quadrant | sits behind front quadrant |
|---|---|
| `a` (back top-left) | `B` (front top-right) |
| `b` (back top-right) | `A` (front top-left) |
| `c` (back bottom-left) | `D` (front bottom-right) |
| `d` (back bottom-right) | `C` (front bottom-left) |

**Folding it.** After fold 2 the packet's surfaces, from the reader's side
inwards, are:

| depth | surface | face | quadrant | upright? |
|---|---|---|---|---|
| 1 | `D` | front | bottom-right | yes |
| 2 | `c` | back | bottom-left | yes |
| 3 | `a` | back | top-left | **inverted** |
| 4 | `B` | front | top-right | **inverted** |
| 5 | `A` | front | top-left | **inverted** |
| 6 | `b` | back | top-right | **inverted** |
| 7 | `d` | back | bottom-right | yes |
| 8 | `C` | front | bottom-left | yes |

Depth *n* is reader page *n*. So for a **one-sheet** quarto signature
(8 pages), 1-based:

```
p1 = D   p2 = c   p3 = a   p4 = B   p5 = A   p6 = b   p7 = d   p8 = C
```

**Four checks that this is right**, each of which fails loudly if a step
above was wrong:

1. **Leaves are consecutive pairs.** Leaf 1 = surfaces 1,2 = `D`,`c`, and `c`
   sits behind `D` — one piece of paper, pages 1 and 2. Leaf 2 = `a`,`B`,
   pages 3 and 4, and `a` sits behind `B`. Leaf 3 = `A`,`b` = 5,6. Leaf 4 =
   `d`,`C` = 7,8. All four leaves carry `(2k−1, 2k)`.
2. **The cover pair is on one face, across the spine.** `p1 = D` and
   `p8 = C` are both on the **front**, bottom row, adjacent across the
   vertical fold. Reading the sheet's bottom row left to right gives
   **`8 | 1`** — character-for-character the folio face-0 line GUIDE §5
   prints.
3. **The centre spread is on one face, across the spine.** `p4 = B` and
   `p5 = A` are both on the front, top row, adjacent across the vertical
   fold. Any imposition where the centre spread is not adjacent on one face
   is wrong.
4. **Two spine folds and two head bolts.** The vertical fold joins leaf 1 to
   leaf 4 (outer) and leaf 2 to leaf 3 (inner); the horizontal fold joins
   leaf 1 to leaf 2 and leaf 3 to leaf 4 at the head. That is a nested
   gathering — a thread through the spine catches both spine folds — and it
   is why the head bolt must not be slit until after sewing.

**Nesting m sheets.** Sheet `j` (0-based, outermost first) contributes reader
leaves `2j+1, 2j+2` from the front of the run and `4m−2j−1, 4m−2j` from the
back. In 0-based *page* indices within the signature, its eight packet
surfaces are:

```
P1 = 4j        P2 = 4j+1      P3 = 4j+2      P4 = 4j+3
P5 = 8m-4j-4   P6 = 8m-4j-3   P7 = 8m-4j-2   P8 = 8m-4j-1
```

Check, m = 2 (a 16-page quarto signature): sheet 0 gets pages 1,2,3,4 and
13,14,15,16; sheet 1 gets 5,6,7,8 and 9,10,11,12. Nested correctly.

**The imposition table.** Cells named `TL, TR, BL, BR`:

| face | cell | packet surface | 0-based index | rotated |
|---|---|---|---|---|
| front | TL | `A` = p5 | `8m-4j-4` | **180°** |
| front | TR | `B` = p4 | `4j+3` | **180°** |
| front | BL | `C` = p8 | `8m-4j-1` | 0° |
| front | BR | `D` = p1 | `4j` | 0° |
| back | TL | `a` = p3 | `4j+2` | **180°** |
| back | TR | `b` = p6 | `8m-4j-3` | **180°** |
| back | BL | `c` = p2 | `4j+1` | 0° |
| back | BR | `d` = p7 | `8m-4j-2` | 0° |

**The whole top row is turned 180°; the bottom row is not.** That is the
per-cell rotation matrix, and it has one entry, not four.

Worked example, 16 pages (m = 2), 1-based page numbers:

```
sheet 0 (outermost)          sheet 1 (innermost)
  front  13 |  4               front   9 |  8
         16 |  1                       12 |  5
  back    3 | 14               back     7 | 10
          2 | 15                        6 | 11
```

(The top row of each block is printed upside down.)

### 3.2 `deckle/core/signatures.py` — `quarto_order`

```python
def quarto_order(n: int) -> list[int]:
    """The quarto page-to-slot permutation for an ``n``-page signature.

    Slots are emitted eight per sheet, outermost sheet first, in the order
    ``front TL, front TR, front BL, front BR, back TL, back TR, back BL,
    back BR`` -- reading order across each face, top row then bottom row,
    which is also the order ``QuartoStrategy`` builds its ``Side.pages`` in.

    Derived in ``docs/specs/2026-09-04-roadmap/F7-quarto.md`` section 3.1 by
    folding a sheet twice and reading the resulting stack, not copied from a
    published table. ``fold_reading_order`` answers the same question by
    peeling leaves off a run, and the two must never share an
    implementation -- see this module's docstring.

    :param n: the signature's page count.
    :returns: the reading-order page index for each physical print slot.
        ``n=8`` gives ``[4, 3, 7, 0, 2, 5, 1, 6]``.
    :raises ValueError: unless ``n`` is a positive multiple of 8. A quarto
        signature always folds to a whole number of eight-page sheets.
    """
    if n <= 0 or n % 8 != 0:
        raise ValueError(f"n must be a positive multiple of 8, got {n}")
    sheets = n // 8
    seq: list[int] = []
    for j in range(sheets):
        seq += [
            n - 4 * j - 4,   # front TL  (p5)
            4 * j + 3,       # front TR  (p4)
            n - 4 * j - 1,   # front BL  (p8)
            4 * j,           # front BR  (p1)
            4 * j + 2,       # back TL   (p3)
            n - 4 * j - 3,   # back TR   (p6)
            4 * j + 1,       # back BL   (p2)
            n - 4 * j - 2,   # back BR   (p7)
        ]
    return seq
```

`quarto_order(8) == [4, 3, 7, 0, 2, 5, 1, 6]` is the **pinned value**,
analogous to `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`. If a physical
dummy contradicts it, that is an escalation to a human, not an edit by
whoever noticed.

### 3.3 `deckle/core/signatures.py` — `fold_reading_order` learns quarto

`fold_reading_order` takes only a `SheetPlan`, so it must infer the scheme
**from the plan itself** rather than from settings it is not given — which
also keeps it from importing anything new:

```python
    leaves_per_face = len(plan.sheets[0].front.pages) if plan.sheets and plan.sheets[0].front else 2
```

Branch on it: `2` → the existing four-at-a-time peel, unchanged; `4` → the
new eight-at-a-time peel; anything else → `ValueError(f"fold_reading_order does not know a scheme with {leaves_per_face} leaves per face")`.

The quarto peel, written from the fold description and **not** from
`quarto_order`:

```python
        # Each sheet is a packet of four leaves: two peeled off the front of
        # the remaining run and two off the back, because a nested quarto's
        # outermost sheet wraps every sheet inside it. Within the packet the
        # eight surfaces sit in the cells the second fold puts them in -- the
        # bottom row carries the outermost leaf's two faces, the top row the
        # innermost, and the top row is upside down.
        while remaining:
            p1 = remaining.popleft()
            p2 = remaining.popleft()
            p3 = remaining.popleft()
            p4 = remaining.popleft()
            p8 = remaining.pop()
            p7 = remaining.pop()
            p6 = remaining.pop()
            p5 = remaining.pop()
            order += [p5, p4, p8, p1, p3, p6, p2, p7]
```

The final line is the *cell assignment* from §3.1's stack table, spelled with
the surface names. It is the one thing the two derivations share, and that is
the same degree of independence folio already has: `saddle_order` and the
folio peel both encode "the outermost sheet's front carries the first and
last page". **Paper is still the only real check** — F4's gate, and quarto's
own dummy fold in §5.

Also change `page_count = 4 * sheet_count` to
`page_count = 2 * leaves_per_face * sheet_count`.

### 3.4 `deckle/core/models.py`

```python
    fold_scheme: Literal["none","folio","quarto"] = "none"
```

Extend the `:ivar fold_scheme:` docstring: *"``quarto`` folds the sheet twice
for eight pages per sheet, on **portrait** stock — the mirror of folio's
landscape requirement."*

### 3.5 `deckle/core/marks.py` — a second axis

Four additions, each a generalisation with the existing function re-expressed
through it so there is one implementation per idea:

```python
def sewing_stations_between(
    y_low: float, y_high: float, fold_x: float, count: int
) -> tuple[Mark, ...]:
    """``count`` ticks crossing the fold, inset ``SEWING_MARGIN_PT`` from
    ``y_low`` and ``y_high``.

    Quarto's spine fold runs only through the top band of the sheet -- the
    band the second fold turns into the centre spread -- so the stations
    cannot be spread over the whole sheet height the way folio's are.
    """
```

Body: the current `sewing_stations` body with `sheet_h` replaced by the band,
i.e. `span = (y_high - y_low) - 2 * SEWING_MARGIN_PT`, positions
`y_low + SEWING_MARGIN_PT + i * step`, and the `count == 1` case at
`(y_low + y_high) / 2.0`. Then:

```python
def sewing_stations(sheet_h, fold_x, count):
    """... unchanged docstring ..."""
    return sewing_stations_between(0.0, sheet_h, fold_x, count)
```

Likewise `signature_order_mark_between(sig_index, sig_count, y_low, y_high, fold_x)`
with `signature_order_mark` delegating to it over `0.0, sheet_h`.

```python
def fold_line_across(sheet_w: float, fold_y: float) -> Mark:
    """The horizontal fold, fore-edge to fore-edge. Quarto's first fold,
    which becomes the head bolt and is slit after sewing."""
    return Mark(kind="fold_line", x0=0.0, y0=fold_y, x1=sheet_w, y1=fold_y)
```

```python
def quarto_cut_lines(sheet_w: float, sheet_h: float, trim_pt: float) -> tuple[Mark, ...]:
    """Six cut lines: the two tails, the two fore-edges, and the head bolt
    trimmed on both sides of the first fold.

    A quarto's head is a *fold*, not an open edge, so it is trimmed at
    ``sheet_h/2 +/- trim_pt`` -- two lines, one for each band, because both
    bands have their head at the middle of the sheet.
    """
```

Body: `trim_pt <= 0` → `()`. Raise the same `ValueError` shape as `cut_lines`
when `4 * trim_pt >= min(sheet_w, sheet_h)` (four cuts across the height now,
not two). Then the six marks:
`y=trim`, `y=sheet_h-trim`, `y=sheet_h/2 - trim`, `y=sheet_h/2 + trim`
(all full-width), `x=trim`, `x=sheet_w-trim` (both full-height).

### 3.6 `deckle/core/layout.py` — geometry

```python
def quarto_cell_geometry(paper: tuple[float, float]) -> tuple[Cell, Cell, Cell, Cell]:
    """Split a sheet into the four quarto cells: TL, TR, BL, BR.

    For letter portrait (612 x 792) this returns
    ``(0, 396, 306, 792)``, ``(306, 396, 612, 792)``,
    ``(0, 0, 306, 396)``, ``(306, 0, 612, 396)`` -- in the same order
    ``quarto_order`` emits its slots.
    """
    paper_w, paper_h = paper
    fold_x = paper_w / 2.0
    fold_y = paper_h / 2.0
    return (
        (0.0, fold_y, fold_x, paper_h),
        (fold_x, fold_y, paper_w, paper_h),
        (0.0, 0.0, fold_x, fold_y),
        (fold_x, 0.0, paper_w, fold_y),
    )
```

### 3.7 `deckle/core/layout.py` — `_place_page` learns a cell rotation

Add a keyword-only parameter:

```python
    cell_rotate_deg: int = 0,
```

documented as: *"A half turn applied to this cell's content because the cell
itself is upside down in the folded book — quarto's top row. Composed with
any landscape rotation, and it swaps the head and tail margins, because the
cell's physical bottom edge is the reader's head. It does **not** swap the
spine side: the gutter is physical space beside the spine and stays on the
cell edge the spine is on, whichever way the content faces."*

Three edits inside the function:

1. After the margin clamps at lines 403-406:

```python
    if cell_rotate_deg == 180:
        # A turned cell's physical bottom edge is the reader's head, so the
        # head margin has to be reserved there. The spine side is NOT
        # swapped: `gutter` is blank space beside a physical fold.
        top, bottom = bottom, top
```

2. The `Placement` at lines 490-496:

```python
        rotate_deg=(rotate_deg + cell_rotate_deg) % 360,
```

3. The filler branch at line 372 keeps `rotate_deg=0` — a blank has no
   orientation, and giving it one would change nothing but the cache key.

**Only 0 and 180 are accepted.** Assert it at the top:
`assert cell_rotate_deg in (0, 180), cell_rotate_deg`. `assert` matches the
house style here (`layout.py:1043-1057`), and note that asserts vanish under
`-O` (B35).

### 3.8 `deckle/core/layout.py` — `QuartoStrategy`

A new class beside `SaddleStitchStrategy`, same `LayoutStrategy` shape, taking
no constructor arguments. It differs from folio in exactly seven places, and
they should be visible as seven differences and not a fork:

1. `_pad_to_slots` → a `_pad_to_slots(active, multiple)` parameterised on 4
   or 8 (rename `_ceil4_total` to `_ceil_multiple(n, m)`; folio passes 4,
   quarto 8). Padding warning text unchanged.
2. `sheets_n = len(slots) // 8`.
3. `page_offset = group[0] * 8`, `page_count = 8 * len(group)`.
4. `order = quarto_order(page_count)`; `pos = local_idx * 8`; eight slot
   indices per sheet.
5. **Portrait check**, the inverse of folio's:

```python
        if paper_w > paper_h:
            warnings.append(
                LayoutWarning(
                    sheet_index=0,
                    kind="sheet_orientation",
                    detail=(
                        'fold_scheme="quarto" expects portrait paper taller '
                        "than it is wide; proceeding with the paper as given"
                    ),
                )
            )
```

6. **Cells, spines and rotations.** `cells = quarto_cell_geometry(settings.paper)`
   in `(TL, TR, BL, BR)` order. Spine side is the column, not the row:

```python
                # The spine is the second fold, at the vertical centreline,
                # so the left column's spine is on its right edge and the
                # right column's on its left -- on every row and both faces.
                # `binding_edge` mirrors the columns, exactly as it mirrors
                # the two folio cells: under folio it means reading
                # direction, and it means the same here.
                if settings.binding_edge == "right":
                    columns = ("left", "right")      # TL/BL spine left, TR/BR spine right
                    cell_order = (cells[1], cells[0], cells[3], cells[2])
                else:
                    columns = ("right", "left")
                    cell_order = (cells[0], cells[1], cells[2], cells[3])
                spines = (columns[0], columns[1], columns[0], columns[1])
                rotations = (180, 180, 0, 0)
```

   so slot *i* of a face uses `cell_order[i]`, `spines[i]`, `rotations[i]`.
7. **Marks.**

```python
                fold_x = settings.paper[0] / 2.0
                fold_y = settings.paper[1] / 2.0
                shared = [
                    fold_line(paper_h, fold_x),          # the spine fold
                    fold_line_across(paper_w, fold_y),   # the head bolt
                    *quarto_cut_lines(paper_w, paper_h, settings.trim_pt),
                ]
                front_marks = list(shared)
                back_marks = list(shared)
                if is_innermost:
                    # Opening a folded quarto at its centre shows the FRONT
                    # face's top row -- the surfaces the second fold joins --
                    # so that is where the awl goes in. Folio's stations are
                    # on the innermost BACK; this is the derived difference,
                    # and F4's successor dummy is what confirms it.
                    front_marks.extend(
                        sewing_stations_between(
                            fold_y, paper_h, fold_x, settings.sewing_stations
                        )
                    )
                if is_outermost:
                    # The closed book's visible spine is the fold between the
                    # cover pair, which is the front face's BOTTOM row.
                    front_marks.append(
                        signature_order_mark_between(
                            sig_index, sig_count, 0.0, fold_y, fold_x
                        )
                    )
```

The invariant assertions at lines 1041-1057 carry over with `% 4` becoming
`% 8`.

`grain_warning` (`layout.py:275`) currently reads
`folded = settings.fold_scheme == "folio"`; change to
`settings.fold_scheme in ("folio", "quarto")`. A quarto has two folds, but
the *spine* fold is still the vertical one and the grain rule is still
"grain parallel to the spine", so the existing message is correct as written.

### 3.9 Strategy selection

`deckle/cli.py:735-743`:

```python
def _strategy_for(settings: LayoutSettings) -> LayoutStrategy:
    if settings.fold_scheme == "quarto":
        return QuartoStrategy()
    if settings.fold_scheme == "folio":
        return SaddleStitchStrategy()
    return GutterShiftStrategy()
```

`deckle/cli.py:765` — `choices=["none", "folio", "quarto"]`, help gains
*"'quarto' (folded twice, eight pages per sheet, on portrait stock)"*.

`deckle/app/views/layout_panel.py:45` — `FOLD_SCHEMES` gains `"quarto"`, and
the panel's strategy lookup (grep `recompute_plan`) mirrors `_strategy_for`.
**Do not** add a fold-scheme dropdown: `layout_panel.py:749-759` records that
the tabs *are* the mode. Add a third tab, `"Quarto"`, beside `"Flat sheets"`
and `"Signatures"`, with `_on_mode_tab_changed` mapping tab index → scheme
through a dict rather than the current `if index == self._signature_tab_index`
ternary at line 1575.

### 3.10 Schedule

`deckle/core/schedule.py:350` — `if schedule.fold_scheme != "folio"` becomes
`not in ("folio", "quarto")`.

`format_schedule_text`'s per-sheet block prints
`front:  {_format_pages(sheet.front_pages)}`. With four pages per side that
is four numbers on one line and unreadable. Under quarto print two lines per
face:

```
        front:  13   4      (top row, printed upside down)
                16   1
        back:    3  14      (top row, printed upside down)
                 2  15
```

Implemented as: when `schedule.fold_scheme == "quarto"`, slice the tuple
`[:2]` and `[2:]` and emit two lines, the first suffixed
`"      (top row, printed upside down)"`.

Add to the folding instructions, after *"Fold the gathered stack in half
along the printed fold line"*, the four-step fold prose from §3.1 and this
sentence, which is the one that saves a book:

> **Slit the head fold only after sewing.** A quarto's leaves are joined at
> the head; cutting them open first turns the gathering into loose leaves.

### 3.11 Preview

`deckle/app/views/preview_view.py:226-241` handles folio's two cells. Add the
quarto branch: four guides from `quarto_cell_geometry`, paired with
`side.pages` in slot order, each `content_box_rect_pt(settings,
spine_side=..., cell=...)`. The guide rectangle is the *footprint*, which a
180° rotation does not move, so no rotation handling is needed here.

### 3.12 Docs

- GUIDE: a new §5b, "Path C — quarto", mirroring §5's structure, carrying the
  fold prose, the portrait-stock requirement, the head-bolt warning, and the
  same "fold a dummy first" framing.
- GUIDE §8: `--fold-scheme` row gains `quarto`.
- README feature table and CHANGELOG.
- No new module, so no new `docs/api/*.rst`.

## 4. Tests

New file `tests/test_layout_quarto.py`, modelled on
`tests/test_layout_saddle.py`.

**Ordering**

1. `test_quarto_order_of_one_sheet_is_the_derived_table`
   `quarto_order(8) == [4, 3, 7, 0, 2, 5, 1, 6]`. The pinned value.
   Unfixed: `ImportError: cannot import name 'quarto_order'`.

2. `test_quarto_order_rejects_a_count_that_is_not_a_multiple_of_eight`
   `4`, `12`, `0`, `-8` all raise `ValueError` naming the number.

3. `test_every_leaf_carries_a_consecutive_page_pair`
   For `n` in `8, 16, 32`: group `quarto_order(n)` into sheets of 8, map each
   to its `(front TL..BR, back TL..BR)` slots, and assert the four
   front/back pairs that share a physical leaf — `(BR, BL)`, `(TR, TL)`,
   `(TL, TR)`, `(BL, BR)` per the mirror table — differ by exactly 1 and are
   `(even, odd)`. **This is check 1 of §3.1 as an assertion**, and it fails
   on any single transcription slip in the table.

4. `test_the_cover_pair_is_the_first_and_last_page_of_the_signature`
   For the outermost sheet, front BL is `n-1` and front BR is `0`.

5. `test_the_centre_spread_is_adjacent_on_one_face`
   For the innermost sheet, front TR and front TL are `n/2 - 1` and `n/2`.

6. `test_quarto_order_is_a_permutation`
   `sorted(quarto_order(n)) == list(range(n))` for 8, 16, 32. No page
   printed twice, none dropped.

**The two-derivation round trip**

7. `test_fold_reading_order_reproduces_quarto_order_for_a_real_plan`
   Impose 16 numbered pages with `QuartoStrategy`; walk the plan's slots in
   emission order collecting `source_ref.page_index`; assert it equals
   `fold_reading_order(plan)`. The folio equivalent is
   `tests/test_layout_saddle.py:83`.

8. `test_fold_reading_order_still_refuses_to_use_the_closed_form` — extend
   `tests/test_spec_residue.py:175`'s monkeypatch guard to raise from
   `quarto_order` as well as `saddle_order`, then call `fold_reading_order`
   on a quarto plan.

9. `test_fold_reading_order_refuses_a_shape_it_does_not_know`
   A hand-built plan whose front side has 3 pages raises `ValueError`.

**Geometry and rotation**

10. `test_quarto_cells_tile_the_sheet_exactly`
    The four cells are disjoint, their areas sum to the sheet area, and their
    union bounding box is the sheet.

11. `test_letter_portrait_gives_the_recorded_cells`
    `quarto_cell_geometry((612.0, 792.0))` equals the four tuples in §3.6.

12. `test_the_top_row_is_turned_and_the_bottom_row_is_not`
    Impose 8 pages; every `OutputPage` in slots 0-1 of each face has
    `placement.rotate_deg == 180`, slots 2-3 have `0`.
    **Unfixed this test cannot even be written**; on a tree with `QuartoStrategy`
    but not B2, it passes while the PDF is wrong — which is why test 14
    exists.

13. `test_a_turned_cell_swaps_the_head_and_tail_margins`
    `margin_top_pt=72`, `margin_bottom_pt=0`. In a bottom-row cell the
    content's top edge is 72pt below the cell's top; in a top-row cell it is
    72pt above the cell's bottom. Asserted through `actual_margins_pt`.

14. `test_a_turned_page_actually_comes_out_turned_in_the_pdf` — **in
    `tests/test_export.py`.** Export a one-sheet quarto of `deckle dummy`
    output and assert the front page's content stream contains a
    `-1 0 0 -1 ` matrix (the 180° form `_rotation_matrix` produces). **This
    is the B2 gate**: on the current exporter it fails with no such matrix
    present, and it is the only test in this spec that distinguishes "the
    plan says 180" from "the paper is turned".

**Marks**

15. `test_both_folds_are_drawn_on_every_face`
    Each face carries exactly two `fold_line` marks, one vertical at
    `paper_w/2` spanning the full height and one horizontal at `paper_h/2`
    spanning the full width.

16. `test_sewing_stations_are_on_the_innermost_front_top_band_only`
    Three stations, all with `paper_h/2 <= y <= paper_h`, all straddling
    `paper_w/2`, only on the innermost sheet's `front.marks`, and absent from
    every other face. The mirror of
    `tests/test_layout_saddle.py:478-484`, with `front` and `back` swapped —
    **swap deliberately, and say so in the test's docstring**, because the
    difference from folio is derived and F4's successor dummy is what
    confirms it.

17. `test_the_order_mark_is_on_the_outermost_front_bottom_band`
    One `signature_order` mark, `0 <= y <= paper_h/2`, on the outermost
    sheet's front only.

18. `test_quarto_cut_lines_include_the_head_bolt`
    With `trim_pt=18`, six `cut_line` marks; two of them at
    `paper_h/2 ± 18`. Without them the head is never trimmed and the book
    stays bolted shut.

19. `test_a_trim_that_would_cross_at_the_head_bolt_is_refused`
    `trim_pt` such that `4 * trim >= paper_h` raises `ValueError`.

**Padding, signatures, plumbing**

20. `test_pages_are_padded_to_a_multiple_of_eight`
    A 10-page document gives 2 sheets, 6 blanks, and one
    `signature_padding` warning.

21. `test_portrait_paper_is_expected_and_landscape_warns`
    Letter landscape under quarto emits `sheet_orientation` naming
    `quarto`; Letter portrait emits none. The exact inverse of
    `tests/test_layout_saddle.py`'s folio check.

22. `test_the_signature_invariants_hold_for_quarto`
    The four assertions at `layout.py:1041-1057`, with `% 8`.

23. In `tests/test_settings_roundtrip.py`:
    `test_quarto_survives_a_deckle_round_trip` — `fold_scheme="quarto"`
    saves and loads. `LayoutSettings` is validated by
    `deckle.core.schema.check_values` against its `Literal`, so a project
    written by a quarto build and opened by a folio build refuses cleanly
    rather than silently imposing folio. Assert that too.

24. In `tests/test_cli.py`:
    `test_fold_scheme_quarto_is_accepted` — `--fold-scheme quarto` exits 0.
    Unfixed: `SystemExit: 2`, `invalid choice: 'quarto'`.

25. In `tests/test_schedule.py`:
    `test_a_quarto_schedule_prints_two_rows_per_face_and_the_bolt_warning` —
    the text contains `"(top row, printed upside down)"` and
    `"Slit the head fold only after sewing."`

26. In `tests/test_ui_surface.py`:
    `test_the_quarto_tab_sets_the_fold_scheme` — selecting the third mode tab
    puts `"quarto"` on the project, and selecting it twice pushes one undo
    entry, not two (the `_syncing_mode` guard at `layout_panel.py:1573`).

## 5. Acceptance

| Check | Command |
|---|---|
| Quarto layout tests pass | `.venv/bin/python -m pytest -q tests/test_layout_quarto.py` |
| The pinned table specifically | `.venv/bin/python -m pytest -q -k quarto_order_of_one_sheet_is_the_derived_table` |
| The two-derivation round trip | `.venv/bin/python -m pytest -q -k fold_reading_order_reproduces_quarto_order` |
| The independence guard | `.venv/bin/python -m pytest -q tests/test_spec_residue.py` |
| **B2 is actually fixed** | `.venv/bin/python -m pytest -q -k turned_page_actually_comes_out_turned` |
| Folio is unchanged | `.venv/bin/python -m pytest -q tests/test_layout_saddle.py tests/test_dummy.py tests/test_integration_signatures.py` |
| CLI accepts it | `.venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --fold-scheme quarto` |
| Eight-page dummy imposes to one sheet | `.venv/bin/python -m deckle.cli dummy -o /tmp/q8.pdf --pages 8 --page-size 4.25x5.5in && .venv/bin/python -m deckle.cli info /tmp/q8.pdf --fold-scheme quarto --paper letter \| grep -q "^sheet count: 1"` |
| Nothing was pasted from Bookbinder JS | `! grep -rnE "PAGE_LAYOUTS|BOOKLET_LAYOUTS|PERFECTBOUND_LAYOUTS|per_sheet" deckle/` |
| The folio verdict is in and confirmed | `grep -A2 "^## Reading order" docs/verification/*-folio-dummy.md \| grep -q "^Verdict: confirmed"` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| **[HUMAN] fold a quarto dummy** | `deckle dummy -o /tmp/q8.pdf --pages 8 --page-size 4.25x5.5in`, export `--fold-scheme quarto --paper letter --sewing-stations 3`, print both passes, fold by §3.1's four steps, and read 1→8. Record it in `docs/verification/2026-MM-DD-quarto-dummy.md` with F4's four-verdict shape. **F7 does not ship on a green suite alone**, for the reason F4 exists. |
| **[HUMAN] the head bolt** | On the folded dummy, confirm the leaves are joined at the head and that slitting that fold *after* nesting opens the book. If they are joined at the *tail* instead, the fold prose in §3.1 step 2 folds the wrong half and the whole table is mirrored — escalate, do not patch. |

Greps run against the current tree:

```
$ .venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --fold-scheme quarto
usage: deckle info [-h] ...
deckle info: error: argument --fold-scheme: invalid choice: 'quarto' (choose from 'none', 'folio')
$ grep -rnE "PAGE_LAYOUTS|BOOKLET_LAYOUTS|PERFECTBOUND_LAYOUTS|per_sheet" deckle/
(no output — exit 1)
```

(A plain `grep -i bookbinder deckle/` matches five *prose* references —
`marks.py:8,22`, `dummy.py:8`, `loader.py:75`, `cli.py:234` — which are
citations, not code. The narrower pattern above is the one that means
"a table was pasted".)

## 6. Out of scope

- **Octavo and sextodecimo.** Three and four folds. The gaps research says
  octavo *"demands a bone folder and patience"* and sextodecimo on letter is
  a novelty. The cell machinery generalises; the ordering table does not
  generalise itself, and each new scheme needs its own folded dummy.
- **F8 (French fold).** Next, on the same generalisation.
- **B1 / M2** — threading `slot.rotate_deg` into `Placement`, and the
  `_place_page` refactor. F7 adds a *cell* rotation; the *user's* page
  rotation is still dropped. Do them together if you can; the two rotations
  compose in the same expression.
- **B19** — `landscape_policy`'s dead `letterbox`. Quarto's cells are
  portrait like folio's, so nothing changes here.
- **Creep compensation.** The gaps doc says to revisit it *"if and only if
  octavo or thick signatures (10+ sheets) ship"*. Quarto at four sheets is
  32 pages a gathering and creep is still an advisory.
- **Perfect binding / "quarto without the folds"** (2-up then cut). The gaps
  doc defers it behind this cell generalisation; it is a separate scheme.
- **A `deckle dummy` change.** `--pages 8 --page-size 4.25x5.5in` already
  produces the right fixture.

## 7. decisions.md entry

```
## 2026-09-05 — Quarto, derived from folding a sheet rather than from a table
- Symptom: `--fold-scheme {none,folio}` only. Letter folded once gives a 5.5x8.5 book and a sheet every four pages; folded twice it gives 4.25x5.5 at half the paper, which for a long text at home is a 100-sheet book versus a 50-sheet one.
- Fix: `quarto_order` in `signatures.py`, `quarto_cell_geometry` and `QuartoStrategy` in `layout.py`, a `cell_rotate_deg` parameter on `_place_page`, and second-axis variants of the three mark functions. The ordering table was derived by folding a sheet twice and reading the eight surfaces off the resulting stack (the derivation is in the spec, with four self-checks); nothing was taken from Bookbinder JS, which is MPL-2.0.
- Surfaces: quarto wants PORTRAIT stock, exactly inverting folio's landscape requirement, because two folds halve both dimensions. Its whole top row prints 180 degrees turned, which meant fixing B2 first -- `_place_output_page` took the rotation branch only for 90 and 270, so a 180 was silently drawn unrotated and half of every quarto sheet would have printed upside down with a green suite. Its sewing stations are on the innermost sheet's FRONT top band, not the back, because opening a quarto at its centre shows the front face's top row.
- Watch: a quarto's leaves are joined at the head. Slitting that bolt before sewing turns the gathering into loose leaves, so the schedule says so in one sentence of its own. And the head bolt is why `quarto_cut_lines` has six lines where folio has four.
- Commit: <fill in>
```

## 8. Traps

- **B2 first, or half of every sheet prints upside down and every test
  passes.** `rotate_deg % 360 == 180` falls into `_place_output_page`'s
  `else` branch. `_rotation_matrix(180, cx, cy)` is already correct; the
  branch condition is the bug. Test 14 is the only thing in this spec that
  catches it.
- **Quarto is portrait, folio is landscape.** Every default, warning and
  GUIDE sentence that says "signatures want landscape" is now scheme-specific.
  `duplex_flip_edge` already returns the right answer for both because it
  reads the paper, not the scheme — do not "fix" it.
- **`spine_side` is not swapped by the cell rotation.** The gutter is blank
  space next to a physical fold, and a 180° turn about the footprint centre
  does not move the footprint. The *margins* that swap are head and tail.
  Getting this backwards puts the gutter on the fore-edge for half the book,
  which is the exact defect `docs/decisions.md` records under *fixed_gutter
  put the reserved gutter on the wrong side of the verso*.
- **`fold_reading_order` must not call `quarto_order`.**
  `tests/test_spec_residue.py:175-186` monkeypatches `saddle_order` to raise;
  extend it rather than working around it. Two derivations of one wrong
  assumption agree with each other.
- **`Side.pages` order is the contract.** `schedule._page_numbers`,
  `preview_view` guides, `export`'s composition order and
  `fold_reading_order`'s reconstruction all read `side.pages` positionally.
  Emit TL, TR, BL, BR everywhere or they disagree silently.
- **`_creep_advisory` is the only function in `layout.py` allowed to touch
  `paper_thickness_pt`**, enforced by an AST test in
  `tests/test_layout_saddle.py`. `QuartoStrategy` must not reference it.
- **The invariant asserts vanish under `-O`** (B35). They are still worth
  having; do not convert them to `if/raise` in this spec.
- **`check_values` validates `LayoutSettings` on load.** A `.deckle` written
  with `fold_scheme="quarto"` refuses to open on an older build with a clear
  `StoredValueError` rather than silently imposing folio — verify that, do
  not defeat it.
- **Do not add a fold-scheme dropdown.** `layout_panel.py:749-759` records
  why the tabs *are* the mode: a dropdown plus tabs meant the tab could look
  active while the dropdown said otherwise.
- **`python -m deckle` launches the GUI and blocks.**
