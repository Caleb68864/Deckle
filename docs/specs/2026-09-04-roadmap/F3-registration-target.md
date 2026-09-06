# F3 — Print a registration target you read the two numbers off

**Roadmap item:** `docs/ROADMAP.md` F3
**Depends on:** **F1** — the numbers this target produces have to go
somewhere, and until F1 that somewhere is a JSON file the app never mentions.
`--back-offset` alone is not enough: it is per-invocation and the GUIDE tells
users to write the result into the profile by hand.
**Blocks:** — (F2's wizard can adopt it later as an extra screen, but does
not need it)
**Size:** M
**Decision needed first:** none.

---

## 1. Context

Consumer printers do not put the back exactly behind the front, and a
manual-duplex reload is worse than a duplexer because the stack is
re-registered by hand against the paper guides. `PrinterProfile` carries the
correction (`back_offset_x_pt`, `back_offset_y_pt`) and `export` applies it.
What is missing is any way to **measure** it.

GUIDE §6, verbatim, is the whole of the current procedure:

> **Finding your two numbers.** The calibration wizard is not built, so this
> is currently iterative:
>
> 1. Print one sheet, both sides, with a ruler on each face:
>    `--sheets 0 --rule`, once with `--pass front` and once with `--pass back`.
> 2. Hold the sheet to a bright light. The two rules should sit exactly on top
>    of one another. Note which way the back is off, and by how much.
> 3. Try that as `--back-offset`, reprint, and look again. If it got worse,
>    flip the sign — the through-the-paper view mirrors one axis, and one
>    reprint settles the direction faster than reasoning about it does.
> 4. Two or three rounds is normal. Write the result into the profile.

Two sheets of paper per round, two or three rounds, and step 3 is the
project telling the user to guess a sign. The competitive-gaps research calls
this out as Recommended #1 and the one registration idea worth having:

> **The one genuinely useful registration idea — front-to-back alignment — is
> Recommended #1 above, and it is a different feature that happens to share a
> word.**

**The concrete failure.** `--rule` draws the *same* ruler on both faces. Two
identical rulers held to a light tell you they are offset; they do not tell
you by how much, and they do not tell you which sign to type, because the
through-the-paper view mirrors one axis. So the user reads "about a
millimetre and a bit, leftish", types `-3,0`, reprints two more sheets, finds
it got worse, types `3,0`, reprints two more. Six sheets to learn two
numbers.

**What F3 does:** the front face gets a *labelled graduated scale*; the back
face gets a *plain pointer*. Hold the sheet front-toward-you against a light,
see which labelled tick the pointer lands on, and **the label is the number
to type** — sign included, mirroring already accounted for, no arithmetic and
no second round.

Repro of what exists today:

```bash
.venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/f.pdf \
    --sheets 0 --rule --pass front --profile generic_face_down_reversed
.venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/b.pdf \
    --sheets 0 --rule --pass back --profile generic_face_down_reversed
```

Both PDFs carry the identical 7-inch ruler at the same place on the page.
Nothing in either names an offset, a direction or a unit of misregistration.

## 2. Current code

`deckle/core/export.py:444-472` — everything `--rule` draws, and its only
sizing rule:

```python
PT_PER_INCH = 72.0

# Clear space kept at each end of the rule, so it does not run into the
# sheet edge or the printer's non-printable border.
_RULE_END_CLEARANCE_PT = 0.5 * PT_PER_INCH

_RULE_BASELINE_PT = 0.5 * PT_PER_INCH
_RULE_TICK_PT = 6.0
_RULE_END_TICK_PT = 10.0
_RULE_LABEL_SIZE_PT = 8.0


def proof_rule_length_pt(paper_width_pt: float) -> float:
    """The length of the printed rule for a sheet this wide, in points.

    A whole number of inches, because the entire point is that a person
    reads it against a tape measure ...
    """
    usable = paper_width_pt - 2 * _RULE_END_CLEARANCE_PT
    whole_inches = math.floor(usable / PT_PER_INCH)
    if whole_inches < 1:
        return 0.0
    return whole_inches * PT_PER_INCH
```

`deckle/core/export.py:475-525` — `_draw_proof_rule`. Note it is
**face-blind**: `_export_batched` calls it for every face, front and back
alike, with no parameter distinguishing them:

```python
def _draw_proof_rule(dest_page: pikepdf.Page, paper_pt: tuple[float, float]) -> None:
    """Draw a ruler of known length, labelled with that length.
    ...
    """
    width, _height = paper_pt
    length = proof_rule_length_pt(width)
    if length <= 0.0:
        return

    inches = int(round(length / PT_PER_INCH))
    x0 = (width - length) / 2.0
    y = _RULE_BASELINE_PT

    builder = ContentStreamBuilder()
    builder.push()
    builder.set_line_width(0.5)
    builder.set_dashes(None)
    builder.line(x0, y, x0 + length, y)
    builder.stroke_and_close()
    for step in range(inches + 1):
        tick = _RULE_END_TICK_PT if step in (0, inches) else _RULE_TICK_PT
        tick_x = x0 + step * PT_PER_INCH
        builder.line(tick_x, y, tick_x, y + tick)
        builder.stroke_and_close()
    builder.pop()

    font = pikepdf.Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
    )
    font_name = dest_page.add_resource(font, Name.Font, prefix="Ft")
    builder.push()
    builder.begin_text()
    builder.set_text_font(font_name, _RULE_LABEL_SIZE_PT)
    builder.move_cursor(x0, y - _RULE_LABEL_SIZE_PT - 3.0)
    builder.show_text(
        f"{inches} in exactly -- if this measures short, the printer scaled "
        "the page"
    )
    builder.end_text()
    builder.pop()

    dest_page.contents_add(b"q\n" + builder.build() + b"Q\n")
```

`deckle/core/export.py:798-812` — the per-face loop F3 hooks into, and the
existing `_is_back_face` predicate that already knows which face is which:

```python
        for i, sheet in enumerate(selected, start=1):
            faces = _sides(sheet, side)
            for position, face in enumerate(faces):
                dest_page = out.add_blank_page(page_size=plan.paper_pt)
                if face is not None:
                    for output_page in face.pages:
                        _place_output_page(out, dest_page, output_page, source_cache)
                    _draw_marks(dest_page, face.marks)
                if rule:
                    _draw_proof_rule(dest_page, plan.paper_pt)
                if _is_back_face(sheet, side, position):
                    _apply_back_offset(dest_page, back_offset_pt, rotate_180)
```

`deckle/core/export.py:415-441` — the mark renderer F3 reuses verbatim:

```python
_DASHED_MARK_KINDS = frozenset({"fold_line"})


def _draw_marks(dest_page: pikepdf.Page, marks: Sequence[Mark]) -> None:
    """Draw ``marks`` as vector line segments via ``ContentStreamBuilder``.
    ...
    """
    if not marks:
        return
    builder = ContentStreamBuilder()
    for mark in marks:
        builder.push()
        builder.set_line_width(0.5)
        if mark.kind in _DASHED_MARK_KINDS:
            builder.set_dashes([3, 3])
        else:
            builder.set_dashes(None)
        builder.line(mark.x0, mark.y0, mark.x1, mark.y1)
        builder.stroke_and_close()
        builder.pop()
    content_stream = b"q\n" + builder.build() + b"Q\n"
    dest_page.contents_add(content_stream)
```

`deckle/core/models.py:157` — the `Mark.kind` domain F3 extends by one:

```python
    kind: Literal["sewing_station","signature_order","fold_line","cut_line"]
```

`deckle/core/export.py:643-677` — the offset F3's labels are calibrated
against, including the rotation interaction:

```python
def _apply_back_offset(
    dest_page: pikepdf.Page,
    offset_pt: tuple[float, float],
    rotate_180: bool,
) -> None:
    """Shift a whole back face by the registration correction.
    ...
    **The rotation interaction is the subtle part.** A long-edge back pass
    is turned a half turn, and a point reflection maps a translation to
    its negation -- so a correction measured on the paper has to be
    inverted in page space to survive the turn. ...
    """
    dx, dy = offset_pt
    if rotate_180:
        dx, dy = -dx, -dy
    if dx == 0.0 and dy == 0.0:
        return
    dest_page.contents_add(f"q\n1 0 0 1 {dx:g} {dy:g} cm\n".encode("latin-1"), prepend=True)
    dest_page.contents_add(b"Q\n")
```

`deckle/cli.py:1264-1271` — the flag F3 sits beside:

```python
    export_parser.add_argument(
        "--rule",
        action="store_true",
        help=(
            "draw a ruler of known length on every sheet, to check whether "
            "the printer scaled the page. Use on a proof, not on the job"
        ),
    )
```

`deckle/cli.py:1031-1052` — `_report_rule`, the model for F3's stdout block.

### Call sites

```
$ grep -rn "proof_rule_length_pt\|_draw_proof_rule\|args.rule\|rule=" deckle/
deckle/cli.py:25:from deckle.core.export import export as export_plan, proof_rule_length_pt
deckle/cli.py:1006:            rule=args.rule,
deckle/cli.py:1026:    if args.rule:
deckle/cli.py:1040:    length = proof_rule_length_pt(paper_width_pt)
deckle/core/export.py:456:def proof_rule_length_pt(paper_width_pt: float) -> float:
deckle/core/export.py:475:def _draw_proof_rule(dest_page: pikepdf.Page, paper_pt: tuple[float, float]) -> None:
deckle/core/export.py:489:    length = proof_rule_length_pt(width)
deckle/core/export.py:604:            rule=rule,
deckle/core/export.py:810:                    _draw_proof_rule(dest_page, plan.paper_pt)
```

(`rule: bool = False` also appears as a parameter at `export.py:532` and
`export.py:786`; the pattern above does not match a bare annotation.)

Nothing consumes `Mark.kind` exhaustively — the only two readers are
`export._mark_key` (line 65, a cache-key string) and the dash lookup at line
433 — so adding a member is additive.

### Existing tests

- `tests/test_registration.py` — the back-offset path end to end, including
  the `rotate_180` negation and the profile-vs-flag precedence. **Read this
  file first**; it is the closest existing model for F3's tests and it
  already has the `XDG_CONFIG_HOME` monkeypatch pair.
- `tests/test_export_marks.py` — marks reaching the PDF content stream.
- `tests/test_export.py` — the rule.
- `tests/test_models.py:105-106` — `Mark.kind` round trip.
- `tests/test_spec_residue.py:230` — a list of mark kinds; check whether it
  needs the new member.

## 3. Change

A new pure module `deckle/core/registration.py` computes the target's
geometry and its labels; `export.py` draws the marks through the existing
`_draw_marks` and the labels through a small helper factored out of
`_draw_proof_rule`; `deckle export --registration` turns it on; the number
the user reads is typed into F1's **Back offset X/Y** spinboxes (or
`--back-offset`).

**Chosen:** labelled scale on the front, plain pointer on the back.
**Rejected:** the same graduated grid on both faces — it is symmetric, so it
cannot say which face moved, and every label would have to be read backwards
through the paper.

**Chosen:** a new module rather than an addition to `deckle/core/marks.py`.
`marks.py`'s functions produce geometry that is attached to `Side.marks`
during imposition and travels with the plan; the registration target is a
proof-time overlay selected by an export flag and never part of a plan —
which is exactly why `proof_rule_length_pt` does not live in `marks.py`
either.

### 1. `deckle/core/models.py` — one new `Mark.kind`

```python
    kind: Literal["sewing_station","signature_order","fold_line","cut_line","registration"]
```

Add to the `:ivar kind:` docstring: *"``registration`` is a proof-only
overlay: a graduated scale on the front and a pointer on the back, for
measuring how far the printer puts the second side out of place."*

This member is **emitted**, unlike `landscape_imageable_unverified` (B31), so
it does not repeat that pattern. Solid, not dashed — do not add it to
`_DASHED_MARK_KINDS`.

### 2. New module `deckle/core/registration.py`

Module docstring: what the target is for, why the front carries the labels
(you read the face you are looking at), and the sentence that the two sign
constants below are *derived, not measured*, with the self-check in §5 as
what settles them.

Committed constants:

```python
REGISTRATION_STEP_PT = 3.0
"""One tick. A manual reload is out by 0-2mm on a home printer; 3pt is finer
than the eye resolves through paper and coarse enough that adjacent ticks do
not merge at 0.5pt stroke width."""

REGISTRATION_STEPS = 6
"""Ticks either side of centre, so the scale spans +/-18pt (+/-0.25in). An
error larger than that is not a registration offset, it is the wrong paper
guide."""

REGISTRATION_TICK_PT = 12.0
REGISTRATION_CENTRE_TICK_PT = 24.0
REGISTRATION_POINTER_PT = 30.0
REGISTRATION_LABEL_SIZE_PT = 6.0
REGISTRATION_ARM_PT = 90.0
"""Half-length of each scale's arm along its own axis, so the X scale and the
Y scale do not overlap at the centre."""

REGISTRATION_X_SIGN = 1.0
REGISTRATION_Y_SIGN = -1.0
"""Which way a reading converts to a ``back_offset`` value, per axis.

Derived, not measured. The operator's reload flips the sheet about one edge,
which mirrors exactly one axis between the back page's own coordinates and
where that content physically lands relative to the front -- and
``back_offset_x_pt``/``back_offset_y_pt`` are applied in page space. So one
axis inverts and the other does not, which is the "flip the sign and reprint"
step GUIDE section 6 currently asks the user to perform on every calibration.
Doing it once, here, is the feature.

If the self-check in this spec's acceptance table reads back the same sign it
was given instead of its negation, one of these two constants is inverted.
That is a single character, in one place, and no other code encodes the
convention."""
```

Functions:

```python
def registration_marks_front(paper_pt: tuple[float, float]) -> tuple[Mark, ...]:
    """The graduated scale drawn on a front face."""

def registration_labels_front(
    paper_pt: tuple[float, float],
) -> tuple[tuple[float, float, str], ...]:
    """``(x, y, text)`` for every scale label, in sheet points."""

def registration_marks_back(paper_pt: tuple[float, float]) -> tuple[Mark, ...]:
    """The pointer drawn on a back face."""

def registration_offset_from_reading(
    x_steps: int, y_steps: int
) -> tuple[float, float]:
    """The ``--back-offset`` pair for a reading of ``x_steps``/``y_steps``."""
```

Geometry, with `cx, cy = paper_w / 2.0, paper_h / 2.0`:

**Front, X scale.** For `k` in `range(-REGISTRATION_STEPS, REGISTRATION_STEPS + 1)`:
a vertical tick at `x = cx + k * REGISTRATION_STEP_PT`, from `y = cy` up to
`y = cy + (REGISTRATION_CENTRE_TICK_PT if k == 0 else REGISTRATION_TICK_PT)`.
Plus one horizontal baseline
`Mark(kind="registration", x0=cx - REGISTRATION_ARM_PT, y0=cy, x1=cx + REGISTRATION_ARM_PT, y1=cy)`.

**Front, Y scale.** For the same `k`: a horizontal tick at
`y = cy + k * REGISTRATION_STEP_PT`, from `x = cx` **leftward** to
`x = cx - (centre or normal tick length)`, plus a vertical baseline
`(cx, cy - ARM)` to `(cx, cy + ARM)`. Left and up, so the two scales occupy
different quadrants and their ticks never coincide.

**Front labels.** For each `k`, two entries:

- X: `(cx + k * STEP - 4.0, cy + CENTRE_TICK + 2.0, label_for_step("x", k))`
- Y: `(cx - CENTRE_TICK - 16.0, cy + k * STEP - 2.0, label_for_step("y", k))`

where

```python
def label_for_step(axis: str, k: int) -> str:
    sign = REGISTRATION_X_SIGN if axis == "x" else REGISTRATION_Y_SIGN
    return f"{sign * k * REGISTRATION_STEP_PT:+.0f}"
```

so the label reads `+0`… use `"0"` for `k == 0` (special-case it: `"+0"` and
`"-0"` both invite a stare). Every other label carries an explicit sign.

Only every **second** label is emitted (`k % 2 == 0`) — thirteen 6pt numbers
at 3pt spacing collide. Ticks stay at every step; labels at ±0, ±6, ±12, ±18
points. State this; a reader between labels counts one tick.

**Back, the pointer.** Four marks forming a cross with one asymmetric arm, so
a 180°-turned back is distinguishable from an unturned one:

- horizontal `(cx - POINTER, cy) -> (cx + POINTER, cy)`
- vertical `(cx, cy - POINTER) -> (cx, cy + POINTER)`
- a chevron on the **up** arm only: `(cx - 6, cy + POINTER - 8) -> (cx, cy + POINTER)`
  and `(cx, cy + POINTER) -> (cx + 6, cy + POINTER - 8)`

**`registration_offset_from_reading`:**

```python
    return (
        REGISTRATION_X_SIGN * x_steps * REGISTRATION_STEP_PT,
        REGISTRATION_Y_SIGN * y_steps * REGISTRATION_STEP_PT,
    )
```

`label_for_step` and `registration_offset_from_reading` must return the same
number for the same step — assert it in a test (§4.4) so a label can never
drift from the arithmetic behind it.

### 3. `deckle/core/export.py` — draw it

Factor the text half of `_draw_proof_rule` into a reusable helper, placed
directly above it:

```python
def _draw_labels(
    dest_page: pikepdf.Page,
    labels: Sequence[tuple[float, float, str]],
    size_pt: float,
) -> None:
    """Draw ``(x, y, text)`` labels in Helvetica at ``size_pt``.

    Base-14, so nothing is embedded and no font licence is involved -- the
    same reason ``deckle.core.dummy`` uses it.
    """
```

Body: one `add_resource` for the font, then one `ContentStreamBuilder` with
a `push/begin_text/set_text_font/move_cursor/show_text/end_text/pop` per
label, and a single `contents_add`. Rewrite `_draw_proof_rule`'s text block
to call it, so there is one text path and not two.

New drawing function:

```python
def _draw_registration_target(
    dest_page: pikepdf.Page, paper_pt: tuple[float, float], is_back: bool
) -> None:
    """The graduated scale (front) or the pointer (back).

    Drawn through ``_draw_marks`` -- the geometry is pure and lives in
    ``deckle.core.registration``; this only chooses which face gets which.
    """
    if is_back:
        _draw_marks(dest_page, registration_marks_back(paper_pt))
        return
    _draw_marks(dest_page, registration_marks_front(paper_pt))
    _draw_labels(dest_page, registration_labels_front(paper_pt), REGISTRATION_LABEL_SIZE_PT)
```

`export()` gains `registration: bool = False`, passed through to
`_export_batched`, documented as:

```
:param registration: draw the front/back registration target -- a labelled
    graduated scale on every front face and a pointer on every back face.
    Hold the printed sheet front-toward-you against a light: the tick the
    pointer lands on is labelled with the ``back_offset`` value to use. For
    proofs; print sheet 0 only.
```

In `_export_batched`'s face loop, **after** the `rule` block and **before**
the `_apply_back_offset` block:

```python
                if registration:
                    _draw_registration_target(
                        dest_page, plan.paper_pt, _is_back_face(sheet, side, position)
                    )
```

Order matters: the target must be **inside** the back offset's translation,
so a proof printed with a non-zero offset shows the *residual*. That is what
makes the self-check in §5 work, and it is what makes a second round
converge instead of oscillate.

### 4. `deckle/cli.py` — the flag

After `--rule` in `build_parser`:

```python
    export_parser.add_argument(
        "--registration",
        action="store_true",
        help=(
            "draw a front/back registration target: a labelled scale on "
            "every front, a pointer on every back. Hold the sheet to a "
            "light, read the tick the pointer lands on, and type that "
            "number as --back-offset or into the printer profile. Use with "
            "--sheets 0 and both --pass runs"
        ),
    )
```

Thread `registration=args.registration` into the `export_plan(...)` call at
`cli.py:1002-1010`.

After the `if args.rule:` block at `cli.py:1026-1027`, add
`if args.registration: _report_registration_target()`, with:

```python
def _report_registration_target() -> None:
    """The procedure for reading the target, printed where the user is."""
    print(
        "registration target: print this sheet's front and back "
        "(--pass front then --pass back, same --profile), then hold it "
        "front-toward-you against a light. The back's pointer lands on one "
        f"tick of each scale; ticks are {REGISTRATION_STEP_PT:g}pt apart and "
        "every second one is labelled."
    )
    print(
        "  Type the number beside the horizontal scale's tick as the X "
        "back-offset, and the vertical scale's as the Y. The labels are "
        "already the correction -- no sign to work out."
    )
    print(
        "  Dead centre means the printer is already in register and both "
        "numbers are 0."
    )
```

`--registration` is **not** added to `_add_layout_args`: it is an export
behaviour, like `--rule`, not a layout setting. It must therefore not appear
in `_layout_flags_given`'s "ignored" list for a `.deckle` source — see B24,
which is the bug where `--sheets`/`--pass`/`--profile` already do.

### 5. F1's editor is where the number lands

No code change in `profile_editor.py`. Add to the two back-offset
spinboxes' tooltip, after the existing sentence:

`" Measure it with deckle export --sheets 0 --registration."`

### 6. Docs

- `docs/api/core.registration.rst` (copy `docs/api/core.marks.rst`'s shape),
  plus a `docs/api/core.rst` toctree entry after `core.marks`.
- GUIDE §6, **"Finding your two numbers"**: replace the four-step iterative
  list with the target procedure. Keep the closing block quote (*"It corrects
  a constant offset"*) verbatim. Add a line: *"If the two scales are not
  parallel to the pointer's arms, that is skew, and this will not fix it."*
- GUIDE §8's layout-options table: `--registration` belongs in the
  export-only options, which §8 does not currently have a section for —
  add one alongside `--sheets`, `--rule`, `--pass`, `--back-offset`,
  `--profile`. That overlaps F5's GUIDE work; coordinate.

## 4. Tests

New file `tests/test_registration_target.py`. Pure geometry tests need no
Qt, no display and no printer.

1. `test_the_scale_spans_the_committed_range_at_the_committed_step`
   `registration_marks_front((612.0, 792.0))`: the X ticks' x-coordinates are
   exactly `{306.0 + k * 3.0 for k in range(-6, 7)}` and there are 13 of
   them. Unfixed: `ModuleNotFoundError: No module named
   'deckle.core.registration'`.

2. `test_the_centre_tick_is_the_long_one`
   The tick at `x == cx` is `REGISTRATION_CENTRE_TICK_PT` tall; every other
   is `REGISTRATION_TICK_PT`. A scale whose zero is not findable at a glance
   is unreadable through paper.

3. `test_the_two_scales_do_not_overlap`
   No X tick and Y tick share both coordinates; the X ticks are all at
   `y >= cy` and the Y ticks all at `x <= cx`.

4. `test_a_label_is_the_offset_the_reading_derives`
   For every labelled `k`, `float(label_for_step("x", k))` equals
   `registration_offset_from_reading(k, 0)[0]`, and likewise for `y`. **This
   is the test that stops a printed label drifting from the arithmetic.**

5. `test_the_centre_label_has_no_sign`
   `label_for_step("x", 0) == "0"` — not `"+0"`.

6. `test_the_two_axes_carry_opposite_signs`
   `registration_offset_from_reading(1, 1) == (3.0, -3.0)`. Pins the one
   asymmetry, with a docstring naming it as the "flip the sign and reprint"
   step being done once in code. If Phase-5 hardware says otherwise, this
   test and one constant change together.

7. `test_the_back_pointer_is_not_symmetric_under_a_half_turn`
   Rotate every back mark 180° about `(cx, cy)` and assert the resulting set
   of segments differs from the original. A symmetric pointer cannot tell a
   turned back from an untuned one — the same lesson `dummy.py:44-56` and
   MVP SS-13 Step 4 both record.

8. `test_every_registration_mark_is_the_registration_kind`
   Both front and back. Guards against a copy-paste `"cut_line"` reaching a
   proof.

Additions to `tests/test_export.py` (or a new
`tests/test_registration_export.py` if that file is long):

9. `test_registration_draws_a_scale_on_fronts_and_a_pointer_on_backs`
   Export a 1-sheet plan with `registration=True`, both faces. Assert page 0's
   content stream contains the label text `"+18"` and page 1's does not, and
   that page 1 has strictly fewer stroked segments than page 0. Unfixed:
   `TypeError: export() got an unexpected keyword argument 'registration'`.

10. `test_the_target_moves_with_the_back_offset`
    Export twice, `back_offset_pt=(0.0, 0.0)` and `(6.0, 0.0)`, with
    `side="back"`. Assert the second document's back page carries the
    `1 0 0 1 6 0 cm` prepended translation and the pointer geometry is
    unchanged in the stream — i.e. the target is *inside* the offset, not
    beside it. **This is the property the whole procedure rests on**: if the
    target were drawn outside the translation, a second round would never
    converge.

11. `test_registration_and_rule_can_be_drawn_together`
    Both flags; the rule's label text and a scale label both appear. They
    occupy different parts of the sheet (`_RULE_BASELINE_PT` is 36pt from
    the bottom; the target is centred), so neither has to be exclusive.

Additions to `tests/test_cli.py` (or `tests/test_cli_sheets.py`, which
already exercises `--sheets`):

12. `test_export_registration_flag_reaches_the_exporter`
    Monkeypatch `deckle.cli.export_plan` to record kwargs; run
    `main(["export", <sample>, "-o", str(tmp), "--registration"])`; assert
    `registration=True`. Unfixed:
    `error: unrecognized arguments: --registration` and exit 2.

13. `test_export_registration_prints_the_reading_procedure`
    `capsys`: stdout contains `"registration target:"` and
    `"The labels are already the correction"`.

14. `test_registration_is_not_reported_as_an_ignored_layout_flag`
    `main(["export", "<project>.deckle", "-o", ..., "--registration"])` on a
    `.deckle` source: stderr must not name `--registration`. Documents that
    it is an export behaviour, not a layout setting. (B24 is the wider bug;
    this test only pins the new flag.)

## 5. Acceptance

| Check | Command |
|---|---|
| Geometry tests pass | `.venv/bin/python -m pytest -q tests/test_registration_target.py` |
| The label/arithmetic agreement specifically | `.venv/bin/python -m pytest -q -k label_is_the_offset_the_reading_derives` |
| The target is inside the offset | `.venv/bin/python -m pytest -q -k target_moves_with_the_back_offset` |
| CLI flag wired | `.venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/reg.pdf --sheets 0 --registration \| grep -q "registration target:"` |
| The new kind is emitted, not just declared | `grep -q '"registration"' deckle/core/registration.py && grep -q 'registration' deckle/core/models.py` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Only one text-drawing path in export | `test "$(grep -c 'begin_text' deckle/core/export.py)" = "1"` |
| New module documented | `test -f docs/api/core.registration.rst && grep -q core.registration docs/api/core.rst` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| **[HUMAN] the sign self-check** | Print sheet 0 front and back through one profile with `--registration` and `--back-offset 0,0`; read `(a, b)`. Reprint with `--back-offset` set to exactly `(a, b)`. The target must now read `0, 0`. If it reads `(2a, 2b)`, `REGISTRATION_X_SIGN`/`REGISTRATION_Y_SIGN` are inverted — flip the one that doubled, and update test 6. |
| **[HUMAN] the scale is readable** | Through 20lb paper against a window, the pointer's position is unambiguous to within one tick, and the labels are legible at 6pt. If not, raise `REGISTRATION_LABEL_SIZE_PT` and drop to every fourth label. |

Greps run against the current tree, for the implementer to diff:

```
$ grep -c 'begin_text' deckle/core/export.py
1
$ ls deckle/core/registration.py
ls: cannot access 'deckle/core/registration.py': No such file or directory
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/x.pdf --registration
usage: deckle [-h] [--version]
              {impose,export,info,schedule,crop-preview,dummy} ...
deckle: error: unrecognized arguments: --registration
```

(The `begin_text` count is already 1 today because `_draw_proof_rule` is the
only text drawer; the acceptance row asserts it is *still* 1 after
`_draw_labels` is factored out — i.e. that `_draw_proof_rule` was rewritten
to call it rather than keeping its own copy.)

## 6. Out of scope

- **Skew and scale.** The target measures a constant translation. If the
  pointer's arms are not parallel to the scales, or the scale spacing does
  not match, that is a different problem and this does not fix it — the
  wording already exists in `PrinterProfile.back_offset_x_pt`'s docstring and
  GUIDE §6; reuse it, do not soften it.
- **F2's wizard.** F3 gives the wizard a better screen 5 later; it does not
  add one now.
- **B20** — `export(sheets=...)` silently skipping unknown indices. A
  registration proof of a nonexistent sheet still writes an empty PDF.
- **B22** — `--profile` without `--pass` being ignored. The procedure tells
  the user to give both; the bug is B22's.
- **Auto-reading the target from a scan.** Deckle is offline and has no
  camera pipeline; the whole design is "a person looks at paper".
- **A GUI button for the proof.** That is N3 ("proof sheet with ruler from
  the print dialog"); when N3 lands it should offer this checkbox too.

## 7. decisions.md entry

```
## 2026-09-05 — A registration target you read the number off, instead of guessing the sign
- Symptom: `--rule` draws the same ruler on both faces, so holding a sheet to the light says "they are offset" and nothing else. GUIDE section 6's procedure was: guess a number, reprint two sheets, and if it got worse flip the sign, two or three rounds. Six sheets to learn two numbers, and step 3 was the documentation telling the user to guess.
- Fix: `deckle/core/registration.py` computes a labelled graduated scale for front faces and a plain pointer for back faces, +/-18pt in 3pt ticks. All text is on the front, which is the face you are looking at; the pointer shows through the paper. The label IS the `back_offset` value, per axis, so the axis mirroring is resolved once in two module constants rather than by the user on every calibration. Drawn through the existing `_draw_marks`; the target is composed inside `_apply_back_offset`'s translation, so a second round reads the residual and converges.
- Surfaces: the labels and `registration_offset_from_reading` are two expressions of one convention, and a test asserts they agree for every step -- a printed number that disagrees with the arithmetic behind it is worse than no number.
- Watch: `REGISTRATION_X_SIGN`/`REGISTRATION_Y_SIGN` are derived from where a flipped sheet puts back-page coordinates, not measured. The self-check is: apply the offset the target reports, reprint, and it must read zero. If it reads double, one constant is inverted -- one character, one place.
- Commit: <fill in>
```

## 8. Traps

- **Draw the target inside the back offset.** `_export_batched` applies
  `_apply_back_offset` after the per-face drawing; put the
  `if registration:` block before it. A target drawn outside the translation
  reads the *original* error every round and the procedure never converges.
- **`_apply_back_offset` negates the offset under `rotate_180`.** Do not
  duplicate that logic in the target; print the proof through the same
  `--pass`/`--profile` the real job uses and let the existing code handle it.
- **`_draw_proof_rule` is face-blind and must stay that way.** F3 adds a
  face-aware drawer; it does not make the rule face-aware. The rule's job is
  scale, and scale is the same on both sides.
- **`ContentStreamBuilder` text needs a font resource on the page.**
  `add_resource(font, Name.Font, prefix="Ft")` returns the name to pass to
  `set_text_font`. Reuse `_draw_proof_rule`'s exact incantation; a
  hand-written `/F1` will render nothing and raise nothing.
- **Every mark is a segment with no width and no colour** — `Mark`'s
  docstring is explicit, and `_draw_marks` strokes everything at 0.5pt. You
  cannot make the centre tick heavier; make it *longer*, which is what the
  spec does.
- **13 labels at 3pt spacing collide.** Emit every second one. A test that
  asserts 13 labels will have been written against the wrong design.
- **`tests/test_docs_coverage.py`** fails on a new module with no `.rst`.
- **`export._mark_key` (line 65) is part of the sheet-cache key.** It already
  includes `mark.kind`, so a new kind is cache-safe; but the *registration
  overlay is not in `Side.marks`*, so it is not in the cache key at all —
  which means `export_sheet_cached` would happily hand back a cached
  non-registration render. The preview does not draw the target and the CLI
  path does not use the cache, so this is latent; note it and do not wire the
  target into `render.py`.
- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
