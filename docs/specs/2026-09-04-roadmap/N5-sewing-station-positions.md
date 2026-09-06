# N5 — Sewing stations at stated positions, not just a count

**Roadmap item:** `docs/ROADMAP.md` N5
**Depends on:** —. Edits `deckle/cli.py` (collides with **M4**, the cli split, and with **N6** and **N14**, which also add flags) and `deckle/app/views/layout_panel.py` (collides with **M1**, the control-table refactor, and with **N11**). Recommended: land N5 before M1 and M4 — both are refactors that want the final field list, and adding a field afterwards means editing the new binding table anyway.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`sewing_stations` is an integer, and `marks.sewing_stations` spreads that many
ticks evenly between a fixed 36pt inset at the head and the same at the tail.
That describes one binding structure — an evenly spaced pamphlet stitch — and
not the three a binder is most likely to be doing:

- **Sewing on tapes.** Each tape needs a *pair* of stations, one either side of
  it, at the tape's width. Even spacing cannot produce pairs at all.
- **Kettle stitches.** These sit at a fixed inset from head and tail (often
  half an inch), with the remaining stations spaced between them. Even spacing
  over the whole span puts the outermost stations 36pt in, whatever the book's
  height.
- **Long stitch.** The pattern is chosen for the cover, and is frequently not
  even.

There is no way to say any of it. The marks get printed, the binder ignores
them and pierces by hand against a jig — at which point the marks are not
merely unhelpful, they are wrong lines on the fold of every signature.

Verified — the count is the only input:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.core.marks import sewing_stations
for m in sewing_stations(612.0, 396.0, 4):
    print(f"y={m.y0:g}")
EOF
```

```
y=36
y=216
y=396
y=576
```

Four evenly spaced stations. To get a tape pair at 2in and 2.25in you would
need a count that produces them, and no integer does.

## 2. Current code

`deckle/core/models.py:409-411` — the field, and its two neighbours that
already show the pattern this spec follows (an `int` default plus an optional
explicit tuple that wins):

```python
    paper_thickness_pt: float = 0.0
    sewing_stations: int = 3
    blank_mode: Literal["end","balanced"] = "end"
```

`deckle/core/models.py:413-429` — `signature_lengths: tuple[int, ...] | None`,
the precedent: *"When set, this wins over `sheets_per_signature` and over
`blank_mode` -- both describe how to derive a grouping, and the user has
instead stated one."*

`deckle/core/marks.py:32-63` — the whole of the current geometry:

```python
def sewing_stations(
    sheet_h: float, fold_x: float, count: int
) -> tuple[Mark, ...]:
    """``count`` short ticks crossing the fold line, evenly spaced.
    ...
    """
    if count <= 0:
        return ()

    x0 = fold_x - STATION_TICK_PT
    x1 = fold_x + STATION_TICK_PT

    if count == 1:
        y = sheet_h / 2.0
        return (Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y),)

    span = sheet_h - 2 * SEWING_MARGIN_PT
    step = span / (count - 1)
    marks = []
    for i in range(count):
        y = SEWING_MARGIN_PT + i * step
        marks.append(Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y))
    return tuple(marks)
```

`deckle/core/layout.py:974-977` — the only call site, on the innermost sheet's
back:

```python
                if is_innermost:
                    back_marks.extend(
                        sewing_stations(paper_h, fold_x, settings.sewing_stations)
                    )
```

`deckle/core/schedule.py:118-126` — the `Schedule` fields:

```python
    signatures: tuple[SignatureInstruction, ...]
    sheets_total: int
    blank_total: int
    sewing_stations: int
    sewing_margin_pt: float
    paper_thickness_pt: float
    fold_scheme: str
```

`deckle/core/schedule.py:266-277` — where it is filled
(`sewing_stations=settings.sewing_stations`,
`sewing_margin_pt=SEWING_MARGIN_PT`).

`deckle/core/schedule.py:397-407` — what the bench sheet says today:

```python
        if schedule.sewing_stations > 0:
            lines.append(
                f"  Pierce {schedule.sewing_stations} sewing station(s) on the fold, "
                f"at the printed marks."
            )
            lines.append(
                f"  The first and last sit {schedule.sewing_margin_pt:.0f}pt "
                f"({schedule.sewing_margin_pt / 72:.2f}in) from head and tail."
            )
        else:
            lines.append("  No sewing stations were marked.")
```

`deckle/cli.py:817-820` — the flag:

```python
    parser.add_argument(
        "--sewing-stations", type=int, default=3,
        help="number of sewing station marks per signature, under --fold-scheme folio (default: 3)",
    )
```

`deckle/cli.py:718-732` — `_build_layout_settings`, which passes
`sewing_stations=args.sewing_stations`.

`deckle/app/views/layout_panel.py:336-344` — the mutator; `layout_panel.py:1143-1152`
— the spinbox; `layout_panel.py:1204, 1229, 1258, 1707-1712` — its four other
mentions (connect, block-list, refresh, handler). **These four places are M1's
whole complaint**; N5 adds a fifth control and must touch all four.

`deckle/app/views/layout_panel.py:414-454` — `set_signature_lengths(project,
text)`, the existing text-to-tuple mutator whose shape and error messages N5
copies.

**Every reader of `sewing_stations` (grep `sewing_station`):**
`deckle/core/marks.py:32,39,55,62,114`; `deckle/core/layout.py:18,857,976`;
`deckle/core/schedule.py:100,120,270,397,399`; `deckle/cli.py:725,817`;
`deckle/app/views/layout_panel.py:336,340,344,379,1143-1152,1204,1229,1258,1707`;
`deckle/core/export.py:424` (the renderer's stroke style);
`tests/test_marks.py`, `tests/test_models.py:165`, `tests/test_schedule.py:57,232`,
`tests/test_ui_surface.py:93`, `tests/test_layout_field_types.py:120`,
`tests/test_layout_panel_refresh.py:170,211,222`, `tests/test_project_cli.py:56`,
`tests/gui_workflow.py:216,229`, `tests/test_export_marks.py`.

**Existing tests:** `tests/test_marks.py` (the geometry),
`tests/test_export_marks.py` (marks reaching the PDF),
`tests/test_schedule.py::test_sewing_stations_are_described_with_their_inset`,
`tests/test_layout_field_types.py` (stored-value validation),
`tests/test_settings_roundtrip.py` (every field survives a `.deckle`).

## 3. Change

### The field

```python
    sewing_station_positions_pt: tuple[float, ...] | None = None
    """Exact station positions, measured up from the tail, or ``None``.

    ``sewing_stations`` says "this many, evenly spaced", which describes a
    pamphlet stitch and nothing else. It cannot say what the three commonest
    structures need: sewing on tapes wants a *pair* of stations either side
    of each tape at the tape's width, kettle stitches sit at a fixed inset
    from head and tail with the rest spread between them, and a long stitch
    pattern is chosen for the cover. No integer produces any of those.

    When set, this wins over ``sewing_stations`` entirely -- the same
    relationship ``signature_lengths`` has with ``sheets_per_signature``:
    both describe how to *derive* a layout, and the user has instead stated
    one.

    Positions are in points from the tail (``y = 0``), matching every other
    coordinate in :mod:`deckle.core.marks`. Stored sorted and de-duplicated,
    so the imposer never has to decide what two identical stations mean.
    """
```

**Why the `int` stays, and why this is not a migration.** `sewing_stations: int
= 3` is the default and remains the field every existing `.deckle` carries.
`_layout_from_dict` (`project_io.py:346-393`) drops keys it does not know and
defaults keys it does not find, so a project written before N5 opens with
`sewing_station_positions_pt=None` and behaves exactly as it did, and one
written after N5 opens on an older build with an
`UnknownLayoutFieldsWarning` and the old even spacing. Neither direction needs
a version bump or a converter. The rejected alternative — replacing the `int`
with the tuple — would be the first `.deckle` migration in the project's
history (F12 lists that as its own, unstarted work) in exchange for removing a
default that is correct for the commonest case.

### Validation, split in two on purpose

**At the boundary (CLI parser, GUI mutator):** shape only — every value parses
as a length, every value is `> 0`, at least one value. Then **normalise**:
`tuple(sorted(set(values)))`. Sorting and de-duplication happen here, once, so
`LayoutSettings` always holds a clean tuple and no downstream reader has to ask
what unsorted or repeated stations mean.

**At imposition (`marks.sewing_stations`):** bounds — every position must be
strictly inside the sheet. That check cannot happen at the boundary because the
sheet height is not known until the paper is; it is the same reasoning
`_parse_signature_lengths` gives for not checking the sum
(`cli.py:241-244`), and `cut_lines` already raises `ValueError` from this layer
for exactly this class of problem (`marks.py:127-133`).

### Steps

1. **`deckle/core/models.py`** — add the field after `sewing_stations: int = 3`
   with the docstring above. It must come after all the other defaulted fields
   in declaration order (it has a default, so anywhere in the defaulted block
   is legal; put it directly under the `int` it qualifies).

2. **`deckle/core/marks.py` — take positions when given.**

   ```python
   def sewing_stations(
       sheet_h: float,
       fold_x: float,
       count: int,
       *,
       positions: Sequence[float] | None = None,
   ) -> tuple[Mark, ...]:
       """Short ticks crossing the fold: ``count`` evenly spaced, or exactly
       ``positions``.

       ``positions`` wins when given, and ``count`` is then ignored -- a
       binder who has stated where the holes go has not also asked how many
       there should be.

       :param positions: exact y coordinates in points, measured up from the
           tail, already sorted and de-duplicated by whoever accepted them.
           ``None`` falls back to even spacing; ``()`` returns no marks, the
           same as ``count <= 0``.
       :raises ValueError: a position falls on or outside a sheet edge.
           Refused rather than clamped: a station at the very edge of the
           fold is a hole in nothing, and silently moving a stated position
           would print a mark somewhere the binder did not ask for.
       """
   ```

   Implementation: when `positions is not None`, return early —

   ```python
       x0 = fold_x - STATION_TICK_PT
       x1 = fold_x + STATION_TICK_PT
       if positions is not None:
           for y in positions:
               if not 0.0 < y < sheet_h:
                   raise ValueError(
                       f"sewing station at {y:g}pt is not on a {sheet_h:g}pt "
                       "sheet: positions are measured up from the tail, and "
                       "must fall between the two edges"
                   )
           return tuple(
               Mark(kind="sewing_station", x0=x0, y0=y, x1=x1, y1=y)
               for y in positions
           )
   ```

   then the existing `count` body unchanged. `Sequence` is already imported
   (`marks.py:15`).

3. **`deckle/core/layout.py:974-977`** — pass them through:

   ```python
                   if is_innermost:
                       back_marks.extend(
                           sewing_stations(
                               paper_h,
                               fold_x,
                               settings.sewing_stations,
                               positions=settings.sewing_station_positions_pt,
                           )
                       )
   ```

   The `ValueError` propagates out of `impose`, which is where
   `cli._impose_or_report` (`cli.py:923-949`) and
   `layout_panel._on_crop_changed`'s pattern already catch this class of
   settings error.

4. **`deckle/core/schedule.py` — carry and print them.** Add to `Schedule`,
   after `sewing_margin_pt`:

   ```python
       sewing_station_positions_pt: tuple[float, ...] | None = None
   ```

   with `:ivar sewing_station_positions_pt: where the holes go, in points from
   the tail, or ``None`` when they are evenly spaced.` in the class docstring.
   Fill it in `build_schedule` (`schedule.py:270`):

   ```python
           sewing_station_positions_pt=settings.sewing_station_positions_pt,
   ```

   And replace the `if schedule.sewing_stations > 0:` block
   (`schedule.py:397-407`) with:

   ```python
           positions = schedule.sewing_station_positions_pt
           if positions:
               lines.append(
                   f"  Pierce {len(positions)} sewing station(s) on the fold, "
                   "at the printed marks."
               )
               lines.append("  Measured up from the TAIL:")
               for index, y in enumerate(positions, start=1):
                   lines.append(f"    {index}.  {y:.1f}pt  ({y / 72:.2f}in)")
           elif schedule.sewing_stations > 0:
               ...unchanged...
           else:
               lines.append("  No sewing stations were marked.")
   ```

   "Measured up from the TAIL" in capitals because head-versus-tail is the one
   thing a binder can get backwards here, and an asymmetric pattern pierced
   upside down is a ruined signature.

5. **`deckle/cli.py` — the parser.** A new value parser beside
   `_parse_signature_lengths` (`cli.py:231`):

   ```python
   def _parse_station_positions(value: str) -> tuple[float, ...]:
       """A ``--stations`` value as exact positions in points, tail upward.

       Comma-separated lengths, each with an optional unit --
       ``0.5in,2in,2.25in,9.5in`` for a two-tape sewing. Sorted and
       de-duplicated here, once, so nothing downstream has to decide what
       two identical stations mean.

       Whether the positions FIT the sheet is checked later, by
       :func:`deckle.core.marks.sewing_stations`, because the sheet height
       is not known until the paper is -- the same split
       ``--signatures`` makes for its sum.

       :param value: the raw flag text.
       :returns: the positions in points, ascending, without repeats.
       :raises argparse.ArgumentTypeError: empty, unparseable, or any value
           at or below zero.
       """
       parts = [part for part in value.split(",")]
       if not value.strip() or any(not part.strip() for part in parts):
           raise argparse.ArgumentTypeError(
               f"invalid stations {value!r}: expected positions separated by "
               "commas, such as 0.5in,2in,2.25in -- measured up from the tail"
           )
       positions = [_parse_length_pt(part) for part in parts]
       if any(position <= 0 for position in positions):
           raise argparse.ArgumentTypeError(
               f"invalid stations {value!r}: a station must sit above the "
               "tail edge, so every position must be greater than zero"
           )
       return tuple(sorted(set(positions)))
   ```

   `_parse_length_pt` already raises `argparse.ArgumentTypeError` with its own
   message for a malformed length, and already accepts `in`/`pt`/`mm`/`cm`
   with an optional space — reused rather than re-specified.

   The flag, in `_add_layout_args` immediately after `--sewing-stations`
   (`cli.py:820`):

   ```python
       parser.add_argument(
           "--stations", dest="sewing_station_positions_pt",
           type=_parse_station_positions, default=None, metavar="Y,Y,Y",
           help=(
               "exactly where the sewing stations go, measured up from the "
               "tail -- e.g. 0.5in,2in,2.25in,9.5in. Use this instead of "
               "--sewing-stations when even spacing will not do: tapes need "
               "a pair either side of each tape, and kettle stitches sit at "
               "a fixed inset from head and tail. Wins over "
               "--sewing-stations when both are given"
           ),
       )
   ```

   Flag name `--stations`, exactly as the roadmap names it — short because it
   will be typed with four or six values after it, and unambiguous alongside
   `--sewing-stations`.

   And in `_build_layout_settings` (`cli.py:718-732`), after
   `sewing_stations=args.sewing_stations,`:

   ```python
           sewing_station_positions_pt=args.sewing_station_positions_pt,
   ```

6. **`deckle/app/views/layout_panel.py` — the mutator.** Beside
   `set_signature_lengths` (`layout_panel.py:414`):

   ```python
   def set_sewing_station_positions(project: Project, text: str, unit: str) -> Project:
       """State exactly where the sewing stations go, as ``0.5, 2, 2.25``.

       Values are in ``unit`` -- the panel's display unit, like every other
       length on it -- and stored in points. Sorted and de-duplicated here,
       once, so the imposer never has to decide what two identical stations
       mean.

       Only the *shape* is checked. Whether the positions fit the sheet
       cannot be known until the paper is, and
       :func:`deckle.core.marks.sewing_stations` already refuses one that
       does not with a message naming both numbers -- the same split
       :func:`set_signature_lengths` makes.

       :param project: the project to derive a new one from.
       :param text: the field's contents. Empty clears the setting and
           returns to evenly spaced ``sewing_stations``.
       :param unit: a key of :data:`LENGTH_UNITS`.
       :returns: a new project.
       :raises ValueError: the text is not a comma-separated list of
           positive numbers.
       """
   ```

   Body mirrors `set_signature_lengths` exactly: empty → `None`; per part,
   `float(part)` with `raise ValueError(f"{part!r} is not a number: give each "
   "station's distance from the tail, such as 0.5, 2, 2.25")`; `value <= 0` →
   `raise ValueError("every station must sit above the tail, got {value:g}")`;
   then `tuple(sorted({to_points(v, unit) for v in values}))`.

7. **`deckle/app/views/layout_panel.py` — the control.** On the Signatures tab,
   directly after the `sewing_stations_spinbox` row (`layout_panel.py:1152`):

   ```python
           self.station_positions_edit = QLineEdit(self.widget)
           self.station_positions_edit.setPlaceholderText("evenly spaced")
           self.station_positions_edit.setToolTip(
               "Where the holes actually go, measured up from the TAIL, in "
               "the unit above -- for example 0.5, 2, 2.25, 9.5.\n\n"
               "Leave empty and Deckle spaces 'Sewing stations' evenly, "
               "which is a pamphlet stitch. Fill it in when even spacing "
               "will not do: sewing on tapes needs a pair either side of "
               "each tape, and kettle stitches sit at a fixed inset from "
               "head and tail.\n\n"
               "This wins over the count above."
           )
           self.station_positions_edit.editingFinished.connect(
               self._on_station_positions_changed
           )
           signature_form.addRow("Station positions:", self.station_positions_edit)
   ```

   Values are in the panel's current display unit, like every other length on
   it. The rejected alternative — accepting `0.5in,2in` suffixes as the CLI
   does — would be unit-independent and would be the *only* control on the
   panel that works that way, and `00-environment.md` forbids a third copy of
   the unit table before M5.

   Set the initial text from the project, in the constructor, using the shared
   renderer from step 9.

8. **`deckle/app/views/layout_panel.py` — the handler**, beside
   `_on_signature_lengths_changed` (`layout_panel.py:1657`):

   ```python
       def _on_station_positions_changed(self) -> None:
           text = self.station_positions_edit.text()
           try:
               plan = apply_layout_change(
                   self.state,
                   lambda project: set_sewing_station_positions(
                       project, text, self._unit
                   ),
               )
           except ValueError as exc:
               self.station_positions_edit.setToolTip(str(exc))
               self.schedule_saved.emit(f"Station positions: {exc}")
               return
           self._refresh_binding_readout(plan)
           self.layout_changed.emit(plan)
   ```

   Reported where the user is looking rather than raised — the same treatment
   `_on_signature_lengths_changed` and `_on_crop_changed` already give a typo.

   A position that does not fit the sheet raises from `recompute_plan` inside
   `apply_layout_change`, which is the same `ValueError` branch, so it is
   reported the same way. **But the mutation has already been applied** by
   `AppState.mutate` at that point — see §8.

9. **`deckle/app/views/layout_panel.py` — the four other places.** This is the
   M1 tax, and skipping any one of them is B11/B12 again:

   - **Renderer**, module level, so the constructor and the refresh share it:

     ```python
     def station_positions_text(layout, unit: str) -> str:
         """The Station positions field's contents for ``layout``."""
         positions = layout.sewing_station_positions_pt
         if not positions:
             return ""
         return ", ".join(f"{from_points(p, unit):g}" for p in positions)
     ```

   - **`refresh_from_project`** (`layout_panel.py:1266-1269`, beside the
     `signature_lengths_edit` line):
     `self.station_positions_edit.setText(station_positions_text(layout, self._unit))`
     and add `self.station_positions_edit` to the `widgets` block-list at
     `layout_panel.py:1223-1231`.

   - **`_on_unit_changed`** (`layout_panel.py:1420-1442`): after the spinbox
     loop, re-render the field in the new unit **with signals blocked**:

     ```python
             self.station_positions_edit.blockSignals(True)
             self.station_positions_edit.setText(
                 station_positions_text(self.state.project.layout, unit)
             )
             self.station_positions_edit.blockSignals(False)
     ```

     This is the exact hole B11 documents for trim and the eight crop boxes.
     Without it, switching in→mm turns `0.5, 2` into positions read back as
     0.5mm and 2mm.

   - **The constructor's initial value** (step 7).

## 4. Tests

### `tests/test_marks.py` (extend)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_stated_positions_are_used_exactly` | `sewing_stations(612.0, 396.0, 3, positions=(36.0, 144.0, 162.0))` | three marks with `y0 == y1` equal to `36.0, 144.0, 162.0` in that order, and every `x0 == 396.0 - STATION_TICK_PT` | `TypeError: sewing_stations() got an unexpected keyword argument 'positions'` |
| `test_stated_positions_ignore_the_count` | `count=99, positions=(100.0,)` | exactly one mark | as above |
| `test_an_empty_position_list_marks_nothing` | `positions=()` | `== ()` | as above |
| `test_positions_none_falls_back_to_even_spacing` | `positions=None, count=4` | identical to `sewing_stations(612.0, 396.0, 4)` today: `y` values `36, 216, 396, 576` | as above |
| `test_a_position_off_the_sheet_is_refused` | `positions=(700.0,)` on a 612pt sheet | `ValueError` whose message contains `"700"` and `"612"` | as above |
| `test_a_position_on_the_edge_is_refused` | `positions=(0.0,)` and `positions=(612.0,)` | `ValueError` both times | as above |

### `tests/test_layout_saddle.py` or `tests/test_export_marks.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_stated_positions_reach_the_imposed_sheet` | impose a 8-page folio job with `sewing_station_positions_pt=(36.0, 144.0, 162.0)`; the innermost sheet's `back.marks` contains exactly three `sewing_station` marks at those `y0` values | `TypeError: LayoutSettings.__init__() got an unexpected keyword argument 'sewing_station_positions_pt'` |
| `test_positions_that_do_not_fit_refuse_the_imposition` | `sewing_station_positions_pt=(5000.0,)` | `impose` raises `ValueError` | as above |

### `tests/test_schedule.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_schedule_lists_stated_positions_from_the_tail` | `format_schedule_text(build_schedule(plan, settings))` for `(36.0, 144.0, 162.0)` contains `"Pierce 3 sewing station(s)"`, `"Measured up from the TAIL:"`, `"1.  36.0pt  (0.50in)"` | `TypeError` on the settings field |
| `test_the_schedule_still_describes_an_evenly_spaced_count` | `sewing_station_positions_pt=None` | the existing `"The first and last sit 36pt"` line is unchanged — this pins that N5 did not change the default path | passes today; keep it |

### `tests/test_cli.py` / `tests/test_cli_paper.py` (extend — no CLI test today exercises `--sewing-stations` at all)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_stations_parses_units_and_sorts` | `_parse_station_positions("2in,0.5in,2.25in")` `== (36.0, 144.0, 162.0)` | `ImportError: cannot import name '_parse_station_positions'` |
| `test_stations_dedupes` | `_parse_station_positions("36,36pt,0.5in")` `== (36.0,)` | as above |
| `test_stations_rejects_zero_and_negative` | `"0"` and `"-1"` each raise `argparse.ArgumentTypeError` naming `stations` | as above |
| `test_stations_rejects_an_empty_element` | `"36,,72"` raises, message contains `"0.5in,2in,2.25in"` | as above |
| `test_stations_reaches_the_project` | `deckle impose tests/fixtures/sample.pdf -o job.deckle --fold-scheme folio --stations 0.5in,2in`; read the JSON | `data["layout"]["sewing_station_positions_pt"] == [36.0, 144.0]` | `error: unrecognized arguments: --stations` (exit 2) |
| `test_stations_shows_up_in_the_schedule` | `deckle schedule ... --fold-scheme folio --landscape --stations 0.5in,2in` stdout | contains `"Measured up from the TAIL:"` | exit 2 as above |

### `tests/test_settings_roundtrip.py` and `tests/test_layout_field_types.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_station_positions_survive_a_project_round_trip` | `save_project` then `load_project` with `sewing_station_positions_pt=(36.0, 144.0)` | it comes back as a **tuple**, equal, and the two `LayoutSettings` compare equal | `TypeError` on the field |
| `test_a_project_written_before_stations_still_opens` | hand-write a `.deckle` whose `layout` has no `sewing_station_positions_pt` | loads, and the field is `None` | passes today; it pins the no-migration claim |
| `test_a_string_where_positions_belong_is_refused` | add `pytest.param("sewing_station_positions_pt", "0.5in", id="positions-are-text")` to the existing parametrisation at `tests/test_layout_field_types.py:120` | `StoredValueError` | `TypeError` on the field |

### `tests/test_layout_panel_settings.py` / `tests/test_layout_panel_refresh.py` (extend)

`LayoutPanel` constructs fine headless (`tests/test_layout_panel_widgets.py`
does it), so these are real-widget tests.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_typing_positions_sets_them_in_points` | panel unit `"in"`; `panel.station_positions_edit.setText("0.5, 2"); panel._on_station_positions_changed()` | `state.project.layout.sewing_station_positions_pt == (36.0, 144.0)` | `AttributeError: 'LayoutPanel' object has no attribute 'station_positions_edit'` |
| `test_clearing_the_field_returns_to_even_spacing` | set then clear | the field is `None` | as above |
| `test_a_bad_position_is_reported_not_raised` | `setText("two")` | no exception; a `schedule_saved` signal carrying a message starting `"Station positions:"` | as above |
| `test_switching_units_redisplays_the_same_positions` | positions `(36.0, 144.0)`, unit `in` → `mm`; then read the field and re-apply it | the field reads `"12.7, 50.8"` and re-applying leaves the model at `(36.0, 144.0)` to 3dp | as above — **and this is B11's exact shape; it must be written before the `_on_unit_changed` edit and seen to fail** |
| `test_refreshing_shows_the_projects_positions` | `refresh_from_project` after replacing the project | the field text matches, and `state.can_redo` is unchanged (B12's invariant) | as above |

## 5. Acceptance

| Check | Command |
|---|---|
| The geometry tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_marks.py tests/test_export_marks.py tests/test_layout_saddle.py` |
| The schedule and round-trip tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_schedule.py tests/test_settings_roundtrip.py tests/test_layout_field_types.py` |
| The CLI tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_paper.py` |
| The panel tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider -k layout_panel` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The `int` field survived | `grep -q "sewing_stations: int = 3" deckle/core/models.py` |
| The flag exists and is spelled right | `.venv/bin/python -m deckle.cli export --help \| grep -q -- "--stations"` |
| Positions are normalised exactly once | `grep -c "sorted(set(" deckle/cli.py deckle/app/views/layout_panel.py` (expect 1 each; `marks.py` must not sort) |
| `marks.py` did not grow a settings import | `! grep -n "LayoutSettings\|paper_thickness" deckle/core/marks.py` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| Unit change reaches the new field | `sed -n '/def _on_unit_changed/,/def _on_use_printer_margins/p' deckle/app/views/layout_panel.py \| grep -q station_positions_edit` |
| `[HUMAN]` The stations land where the tape does | Set `--stations` to a tape pair, export sheet 0, print it, and lay a tape across the fold. The two ticks must straddle the tape's width with the tape centred between them. |

## 6. Out of scope

- **`sewing_tape_width_pt` as a settings field.** `sub-spec-6-marks-geometry.md`
  names it explicitly as a field that MUST NOT be added, and N5 makes it
  unnecessary: a tape pair is two positions.
- **`SEWING_MARGIN_PT` becoming a setting.** It remains the committed 36pt
  constant for the even-spacing path. Stated positions simply do not consult it.
- **B35's "no guard for a sheet shorter than twice the margin"** — that is the
  even-spacing path, where `span` goes negative and the stations march
  downwards off the sheet. N5's bounds check covers only the stated-positions
  path. Fixing the other is B35's.
- **Station marks on anything but the innermost sheet's back**
  (`layout.py:974`). SS-08's predicate, unchanged.
- **M1** (the binding table that would make step 9 one line instead of four).
  N5 pays the tax; M1 removes it.
- **M5** (one unit table). Step 7 uses the panel's `to_points`/`from_points`
  and the CLI uses `_parse_length_pt`, per `00-environment.md`.
- **GUIDE §8's CLI reference** (D5). It already omits eleven flags; this makes
  twelve. Fix them together, not one at a time.

## 7. decisions.md entry

```
## 2026-09-05 — Sewing stations could only be counted, never placed
- Symptom: `sewing_stations` is an integer and `marks.sewing_stations` spreads that many ticks evenly between a fixed 36pt inset at head and tail. That is a pamphlet stitch and nothing else. Sewing on tapes needs a PAIR of stations either side of each tape at the tape's width; kettle stitches sit at a fixed inset with the rest between them; a long stitch pattern is chosen for the cover. No integer produces any of them, so the printed marks were wrong lines on the fold of every signature and binders pierced against a jig instead.
- Fix: Added `LayoutSettings.sewing_station_positions_pt: tuple[float, ...] | None`, which wins over the count when set -- the same relationship `signature_lengths` has with `sheets_per_signature`. `marks.sewing_stations` takes a keyword-only `positions`. `--stations 0.5in,2in,2.25in` on the CLI, a text field on the Signatures tab in the panel's display unit, and the schedule lists every position "measured up from the TAIL".
- Surfaces: The `int` stays and stays the default, so there is no `.deckle` migration: `_layout_from_dict` already defaults a missing key and warns on an unknown one, so projects open in both directions unchanged. Replacing the int with the tuple would have been the project's first migration in exchange for removing a default that is right for the commonest case.
- Surfaces: Validation splits at the layer that knows the answer. Shape (positive, parseable) and normalisation (sort, dedupe) happen once at each boundary; "does it fit the sheet" happens in `marks.py`, because the sheet height is not known until the paper is -- the same split `--signatures` makes for its sum.
- Watch: A new length control on `LayoutPanel` has to be added in FOUR places -- constructor, `refresh_from_project`'s block-list, `_on_unit_changed`, and its handler. Missing the third is exactly B11 (trim and the crop boxes keep their number when the unit changes). The test for it was written first and seen to fail.
- Commit: <fill in>
```

## 8. Traps

- **Positions are measured from the TAIL** (`y = 0`), because that is
  `marks.py`'s convention (`sub-spec-6-marks-geometry.md` §3: "`y = 0` is the
  **tail**"). Every message says so. Anyone who assumes head-down will produce
  an asymmetric pattern pierced upside down, and it will look plausible.
- **`_on_unit_changed` is a hand-maintained list** (`layout_panel.py:1430-1434`)
  that does not include trim or the crop boxes (B11). Adding the new field to
  it is step 9's third bullet and is the one most easily skipped.
- **`refresh_from_project`'s `widgets` block-list is also hand-maintained**
  (`layout_panel.py:1223-1231`). A widget not on it fires its handler during a
  refresh, which mutates and clears the redo stack (B12).
- **`AppState.mutate` has already applied the change when `recompute_plan`
  raises.** `apply_layout_change` calls `state.mutate(mutator)` and *then*
  recomputes (`layout_panel.py:621-622`), so an out-of-sheet position leaves
  the bad value on the project and on the undo stack. That is pre-existing
  behaviour shared with `_on_crop_changed`; match it, do not invent a rollback
  here, and note it for M1.
- **`marks.py` must not sort or de-duplicate.** It is pure geometry that
  reproduces what it is given; normalising in two places is how the CLI and the
  GUI come to disagree about the same input.
- **`marks.py` must not import `LayoutSettings`** — SS-06 has a `[MECHANICAL]`
  check for exactly that, and §5 has a grep for it. Pass `positions`, not
  `settings`.
- **`Sequence` is already imported in `marks.py`** (line 15); do not add a
  second import.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
