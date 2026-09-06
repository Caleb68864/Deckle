# M5 — One paper table, one unit table, one weight→caliper function

**Roadmap item:** `docs/ROADMAP.md` M5
**Depends on:** R0.1 (several acceptance rows run CLI tests that open
`tests/fixtures/sample.pdf`)
**Blocks:** F5 and N14 (CLI/app parity), F11 (custom paper size in the GUI —
it has to know what the preset table is before it can offer "not one of
these")
**Size:** S
**Decision needed first:** none.

---

## 1. Context

The GUI and the CLI each carry their own copy of "what paper can you pick",
"how many points is a millimetre", and "how thick is 80gsm offset". The
copies have already diverged in the way copies do.

**The user-visible failure.** Set a project to A3 or Tabloid in the desktop
app, save it, and there is no way to state that paper at the command line —
the CLI's `--paper` knows three presets and the GUI knows five. Verified:

```bash
$ .venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --paper a3
```

```
usage: ...
error: argument --paper: invalid paper 'a3': expected a preset (letter, a4,
legal) or WxH with an optional unit (in, pt, mm, cm) -- e.g. 8.5x11in
```

The remedy today is to look up A3 in points and type `--paper 841.89x1190.55pt`,
which is the kind of thing a person gets subtly wrong once and then prints
sixty sheets from.

**The maintenance failure.** `LETTER_PT = (612.0, 792.0)` is written out
three times in `deckle/`. The unit-to-points map is written twice with
different key orders. And "a weight in pounds needs a grade" is enforced
twice, in two functions, with two different sentences — which is the
duplication `docs/decisions.md` records this project being bitten by
repeatedly, most recently as *"three functions computed fore-edge creep and
disagreed"* (2026-08-08) and *"the fourth copy, found by the pass that
claimed there were three"* (2026-08-07).

`deckle/core/paper.py` already exists and is already the shared home for the
weight arithmetic — `gsm_from_pounds` and `caliper_pt_from_gsm` are imported
by both front ends today. **The roadmap says "One `PAPER_SIZES` and one
`caliper_from_weight()` in `core/paper.py`" as though the module were new;
it is not, and the name `PAPER_PRESETS` is already taken there by the paper
*stock* table.** That is why the sheet-size table is called `PAPER_SIZES`.

## 2. Current code

### The two size tables

`deckle/cli.py:68-76`, verbatim:

```python
LETTER_PT = (612.0, 792.0)
A4_PT = (595.28, 841.89)
LEGAL_PT = (612.0, 1008.0)

_PAPER_PRESETS = {
    "letter": LETTER_PT,
    "a4": A4_PT,
    "legal": LEGAL_PT,
}
```

`deckle/app/views/layout_panel.py:199-211`, verbatim:

```python
#: Paper sizes offered in the UI, in points, portrait. Landscape is the
#: same tuple swapped -- see :func:`set_paper`. These mirror
#: ``deckle/cli.py``'s ``--paper`` presets so both front ends offer the
#: same stock.
PAPER_PRESETS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("Letter", (612.0, 792.0)),
    ("A4", (595.28, 841.89)),
    ("Legal", (612.0, 1008.0)),
    ("A3", (841.89, 1190.55)),
    ("Tabloid", (792.0, 1224.0)),
)

ORIENTATIONS: tuple[str, ...] = ("Portrait", "Landscape")
```

The comment says *"These mirror `deckle/cli.py`'s `--paper` presets so both
front ends offer the same stock."* They do not. That sentence is the bug
report.

### `LETTER_PT`, three definitions

```
deckle/core/dummy.py:29:LETTER_PT = (612.0, 792.0)
deckle/cli.py:68:LETTER_PT = (612.0, 792.0)
deckle/app/main.py:37:LETTER_PT = (612.0, 792.0)
```

Uses: `dummy.py:106` (`page_size: tuple[float, float] = LETTER_PT`),
`cli.py:73` / `cli.py:752` (`--paper` default) / `cli.py:1372`
(`--page-size` default), `main.py:295` (`default_project()`).

Tests define their own local `LETTER_PT` at `tests/test_print_painting.py:27`
and `tests/test_golden_pinebox.py:19`. **Those are test fixtures and stay
where they are** — a test that imports the value it is checking cannot catch
a change to it.

### The two unit tables

`deckle/cli.py:78-86`, verbatim:

```python
_ACCEPTED_LENGTH_UNITS = ("in", "pt", "mm", "cm")

_UNIT_TO_PT = {
    "in": 72.0,
    "pt": 1.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
}
```

`deckle/app/views/layout_panel.py:138-162`, verbatim:

```python
#: Display units for lengths. Values are points-per-unit, so the stored
#: model stays in PDF points and only the UI converts.
LENGTH_UNITS: dict[str, float] = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}


def to_points(value: float, unit: str) -> float:
    """Convert a displayed value in ``unit`` to PDF points.

    :param value: the displayed number.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: the value in points, which is what the model stores.
    :raises KeyError: ``unit`` is not a known unit.
    """
    return value * LENGTH_UNITS[unit]


def from_points(points: float, unit: str) -> float:
    """Convert PDF points to a displayed value in ``unit``.

    :param points: the stored value.
    :param unit: a key of :data:`LENGTH_UNITS`.
    :returns: the number to display.
    :raises KeyError: ``unit`` is not a known unit.
    """
    return points / LENGTH_UNITS[unit]
```

Same four units, same values, **different insertion order**. The order is
load-bearing in one place: `cli.py:131` renders `', '.join(_UNIT_TO_PT)` into
an error message, giving `in, pt, mm, cm` — which is what
`_ACCEPTED_LENGTH_UNITS` also says at `cli.py:103` and `cli.py:397`.

`_UNIT_TO_PT` read at `cli.py:107`, `cli.py:125`, `cli.py:131`, `cli.py:400`.
`LENGTH_UNITS` read at `layout_panel.py:151` and `162` only.
`to_points` called at `layout_panel.py:1373, 1404, 1604, 1649, 1715`;
`from_points` at `layout_panel.py:864, 866, 896, 898, 917, 943, 944, 1020,
1021, 1245, 1247, 1252, 1259, 1264, 1417, 1438, 1441, 1453, 1456, 1595, 1640`.
No test imports either directly except through `layout_panel`.

### The two weight→caliper paths

`deckle/cli.py:165-198`, verbatim:

```python
def _paper_thickness_from_args(args) -> float:
    """One sheet's caliper, from whichever way the user stated the paper.

    Two ways to say one thing is fine; a silent precedence between them is
    not, so giving both is refused rather than resolved. Someone who sets
    a weight and forgets an old `--paper-thickness` in a script would
    otherwise get a book bound to a number they did not give.

    :param args: the parsed arguments.
    :returns: the caliper in points, or ``0.0`` when unset.
    :raises ValueError: both forms were given, or pounds without a grade.
    """
    weight = getattr(args, "paper_weight", None)
    thickness = getattr(args, "paper_thickness", 0.0)
    if weight is None:
        return thickness
    if thickness:
        raise ValueError(
            "give either --paper-weight or --paper-thickness, not both: "
            "they are two ways to describe the same sheet, and there is no "
            "sensible rule for which one wins"
        )
    value, unit = weight
    if unit == "lb":
        grade = getattr(args, "paper_grade", None)
        if grade is None:
            raise ValueError(
                "--paper-weight in pounds also needs --paper-grade: a US "
                "basis weight means nothing without one, and 20lb is 75gsm "
                "as bond but 54gsm as cover. Expected one of "
                + ", ".join(sorted(GRADE_BASIS_SIZES_IN))
            )
        value = gsm_from_pounds(value, grade)
    return caliper_pt_from_gsm(value, args.paper_type)
```

`deckle/app/views/layout_panel.py:480-508`, verbatim (the roadmap calls this
`set_paper_weight`; **its real name is `set_paper_from_weight`**):

```python
def set_paper_from_weight(
    project: Project, weight: float, unit: str, paper_type: str,
    grade: str | None = None,
) -> Project:
    """Set the stock thickness from a weight off the ream wrapper.

    The escape hatch behind ``Custom...``, for a paper the preset list
    does not carry.

    :param project: the project to derive a new one from.
    :param weight: the number on the wrapper.
    :param unit: ``"gsm"`` or ``"lb"``.
    :param paper_type: which bulk to apply.
    :param grade: the basis size, required for ``"lb"`` -- 20lb is 75gsm
        as bond and 54gsm as cover, so guessing would be wrong by half.
    :returns: a new project.
    :raises ValueError: an unknown unit, type or grade, or pounds without
        a grade.
    """
    if unit == "lb":
        if grade is None:
            raise ValueError(
                "a weight in pounds also needs a paper grade: 20lb is "
                "75gsm as bond but 54gsm as cover"
            )
        weight = gsm_from_pounds(weight, grade)
    elif unit != "gsm":
        raise ValueError(f"unknown weight unit {unit!r}: expected gsm or lb")
    return set_paper_thickness_pt(project, caliper_pt_from_gsm(weight, paper_type))
```

Note the asymmetry that a shared function removes: the CLI path never
validates `unit`, and the panel path never mentions the accepted grades.

### What already lives in `deckle/core/paper.py`

`PT_PER_MM = 72.0 / 25.4` (line 21), `GRADE_BASIS_SIZES_IN` (31-37),
`gsm_from_pounds` (44), `PAPER_BULK` (76-82), `caliper_pt_from_gsm` (85),
`PaperPreset` (103-124), **`PAPER_PRESETS`** (129-135, five paper *stocks*),
`CREEP_INVISIBLE_PT` (143), `FOLD_BULK_LIMIT_MM` (155),
`SignatureSuggestion` (158), `suggest_sheets_per_signature` (178).

`layout_panel.py:26-35` already imports from it, with the collision already
recorded in a comment:

```python
# Aliased on import. `PAPER_PRESETS` in this module has meant sheet
# *sizes* (A4, Letter) since it was written, and the new list is paper
# *stock* (80gsm copier). Two meanings of "paper" in one file is how a
# reader ends up wiring a dropdown to the wrong one.
from deckle.core.paper import PT_PER_MM
from deckle.core.paper import PAPER_PRESETS as PAPER_STOCKS
```

`cli.py:33-35`:

```python
from deckle.core.paper import (
    GRADE_BASIS_SIZES_IN, PAPER_BULK, caliper_pt_from_gsm, gsm_from_pounds,
)
```

### Existing tests that touch any of this

- `tests/test_paper.py` — the whole file; lines 98-135 parametrise over
  `PAPER_PRESETS` (stocks).
- `tests/test_cli_paper.py` — the weight path end to end. Line 63 asserts
  only `"grade" in result.stderr.lower()`; lines 78-79 assert both flag names
  appear for the both-given refusal.
- `tests/test_cli_errors.py:236-278` — `_parse_paper` accept/reject tables.
  `"legal"` accepted at line 240; `"A5"` and `"letterx"` in the reject list at
  line 256. Neither `a3` nor `tabloid` appears in either list.
- `tests/test_layout_panel_paper.py` — `set_paper_stock`,
  `set_paper_from_weight`; line 83 asserts `"grade" in str(exc).lower()`.
- `tests/test_ui_surface.py:842-898` — `paper_is_landscape`,
  `preset_name_for`, and the "custom size is not snapped to a preset" case.
- `tests/test_core_purity.py` — every module under `deckle/core/` must import
  no Qt. `paper.py` passes today and must keep passing.
- `tests/test_docs_coverage.py` — every module under `deckle/` needs a page in
  `docs/api/`. `docs/api/core.paper.rst` exists; **no new module is created
  here**, so nothing to add.

## 3. Change

Everything moves into `deckle/core/paper.py`. Nothing new is invented beyond
the names below.

### The new surface, verbatim

```python
PT_PER_UNIT: dict[str, float] = {
    "in": 72.0,
    "pt": 1.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
}
"""Points per display unit. Insertion order is the order the CLI renders
into its "expected a unit (...)" messages, and matches
``cli._ACCEPTED_LENGTH_UNITS``; do not sort it."""


def to_points(value: float, unit: str) -> float: ...
def from_points(points: float, unit: str) -> float: ...
```

Both keep their current bodies (`value * PT_PER_UNIT[unit]`,
`points / PT_PER_UNIT[unit]`) and their current documented `:raises KeyError:`.
**Do not convert the `KeyError` to a `ValueError`.** `layout_panel` passes a
unit that came out of a combo box it populated itself, and `cli` only reaches
these behind a regex that already restricted the alternatives — a `KeyError`
there is a programming error and should read like one.

```python
LETTER_PT: tuple[float, float] = (612.0, 792.0)
A4_PT: tuple[float, float] = (595.28, 841.89)
LEGAL_PT: tuple[float, float] = (612.0, 1008.0)
A3_PT: tuple[float, float] = (841.89, 1190.55)
TABLOID_PT: tuple[float, float] = (792.0, 1224.0)


@dataclass(frozen=True)
class PaperSize:
    """A sheet size both front ends offer.

    Distinct from :class:`PaperPreset`, which is paper *stock* -- what the
    sheet is made of rather than how big it is. Two meanings of "paper"
    have collided in this project once already; the class names are the
    thing keeping them apart.

    :ivar name: what the desktop dropdown shows, e.g. ``"Letter"``.
    :ivar size_pt: ``(width, height)`` in points, **portrait**. Landscape
        is the same pair swapped -- see
        ``deckle.app.views.layout_panel.set_paper``.
    """

    name: str
    size_pt: tuple[float, float]

    @property
    def key(self) -> str:
        """The lowercase name ``--paper`` accepts."""
        return self.name.lower()


#: Every sheet size Deckle offers, in the order the dropdown lists them.
#: One table, because two of them diverged: the panel carried five and the
#: CLI three, so a project set to A3 in the app could not be stated at the
#: command line at all.
PAPER_SIZES: tuple[PaperSize, ...] = (
    PaperSize("Letter", LETTER_PT),
    PaperSize("A4", A4_PT),
    PaperSize("Legal", LEGAL_PT),
    PaperSize("A3", A3_PT),
    PaperSize("Tabloid", TABLOID_PT),
)


def paper_size_named(name: str) -> tuple[float, float] | None:
    """The portrait dimensions of a named sheet size, or ``None``.

    Case- and whitespace-insensitive, so the CLI's ``letter`` and the
    panel's ``Letter`` reach the same row.

    :param name: a preset name.
    :returns: ``(width, height)`` in points, or ``None`` for a name that is
        not a preset -- which is not an error: ``--paper 500x700pt`` and a
        project carrying a custom size are both legitimate.
    """


def caliper_from_weight(
    weight: float,
    unit: str,
    paper_type: str,
    grade: str | None = None,
) -> float:
    """One sheet's caliper, from the number on the ream wrapper.

    :param weight: the number on the wrapper.
    :param unit: ``"gsm"`` or ``"lb"``.
    :param paper_type: which bulk to apply -- see :data:`PAPER_BULK`.
    :param grade: the basis size, required for ``"lb"``.
    :returns: the caliper in points.
    :raises ValueError: an unknown unit, type or grade, or pounds with no
        grade.
    """
```

`caliper_from_weight`'s three messages, chosen and fixed:

| Condition | Message |
|---|---|
| `unit` is neither `"gsm"` nor `"lb"` | `f"unknown weight unit {unit!r}: expected gsm or lb"` |
| `unit == "lb"` and `grade is None` | `"a weight in pounds also needs a paper grade: a US basis weight means nothing without one, and 20lb is 75gsm as bond but 54gsm as cover. Expected one of " + ", ".join(sorted(GRADE_BASIS_SIZES_IN))` |
| unknown grade / unknown paper type | unchanged — raised by the existing `gsm_from_pounds` and `caliper_pt_from_gsm` |

Order of checks: validate `unit` **first**, then the grade, then convert.
The panel's current order validates `lb` before rejecting an unknown unit;
the new order means `caliper_from_weight(20, "kg", "offset")` says "unknown
weight unit" rather than falling through to the gsm branch.

### Steps

1. **`deckle/core/paper.py`** — add `PT_PER_UNIT`, `to_points`,
   `from_points` immediately after the existing `PT_PER_MM` (line 21). Move
   the two docstrings across verbatim, changing `:data:`LENGTH_UNITS`` to
   `:data:`PT_PER_UNIT``.

2. **`deckle/core/paper.py`** — add `LETTER_PT`, `A4_PT`, `LEGAL_PT`,
   `A3_PT`, `TABLOID_PT`, `PaperSize`, `PAPER_SIZES` and `paper_size_named`
   after the unit block and **before** the existing `GRADE_BASIS_SIZES_IN`
   comment, so the file reads size-then-stock.

3. **`deckle/core/paper.py`** — add `caliper_from_weight` immediately after
   `caliper_pt_from_gsm` (currently ends line 100). It is the composition of
   the two functions above it, so it belongs there rather than at the end.

4. **`deckle/cli.py`** — delete lines 68-76 (`LETTER_PT`, `A4_PT`,
   `LEGAL_PT`, `_PAPER_PRESETS`) and lines 80-86 (`_UNIT_TO_PT`). Keep
   `_ACCEPTED_LENGTH_UNITS` at line 78 — it is a *message* tuple, and after
   this change it is the only ordering guarantee left in that file.

5. **`deckle/cli.py`** — extend the import at lines 33-35 to:

   ```python
   from deckle.core.paper import (
       GRADE_BASIS_SIZES_IN, LETTER_PT, PAPER_BULK, PAPER_SIZES, PT_PER_UNIT,
       caliper_from_weight, caliper_pt_from_gsm, gsm_from_pounds,
       paper_size_named,
   )
   ```

   `caliper_pt_from_gsm` stays imported: `tests/test_cli_paper.py:44` reaches
   for it by name through `deckle.core.paper`, and `cli` itself no longer
   calls it — remove it from the import only if nothing in `cli.py` still
   references it after step 7 (it will not; drop it then).

6. **`deckle/cli.py`** — rewrite `_parse_paper` (lines 111-132). The preset
   lookup and the error message become:

   ```python
       preset = paper_size_named(value)
       if preset is not None:
           return preset
   ```

   and

   ```python
       raise argparse.ArgumentTypeError(
           f"invalid paper {value!r}: expected a preset "
           f"({', '.join(size.key for size in PAPER_SIZES)}) "
           f"or WxH with an optional unit ({', '.join(PT_PER_UNIT)}) -- e.g. 8.5x11in"
       )
   ```

   Every other line of the function is unchanged, **including the
   `(in|pt|mm)` regex on line 124 that omits `cm`.** That mismatch is B25 and
   is not this spec's.

7. **`deckle/cli.py`** — rewrite the tail of `_paper_thickness_from_args`
   (lines 187-198) as:

   ```python
       value, unit = weight
       if unit == "lb" and getattr(args, "paper_grade", None) is None:
           # The core function raises for this too, in front-end-neutral
           # words. This one names the flags, which is the difference
           # between advice and a diagnosis at a command line.
           raise ValueError(
               "--paper-weight in pounds also needs --paper-grade: a US "
               "basis weight means nothing without one, and 20lb is 75gsm "
               "as bond but 54gsm as cover. Expected one of "
               + ", ".join(sorted(GRADE_BASIS_SIZES_IN))
           )
       return caliper_from_weight(
           value, unit, args.paper_type, grade=getattr(args, "paper_grade", None)
       )
   ```

   The both-given refusal at lines 181-186 is unchanged: it names two CLI
   flags and has no business in `core`.

   **Chosen: the CLI keeps one flag-naming sentence. Rejected: the CLI
   catching the core `ValueError` and re-wording it.** The arithmetic, the
   unit validation and the grade validation are now single-sourced; what
   remains duplicated is a sentence that names `--paper-grade`, and a core
   module that names a command-line flag would be a worse fault than the one
   being fixed.

8. **`deckle/app/views/layout_panel.py`** — delete `LENGTH_UNITS`,
   `to_points` and `from_points` (lines 138-162) and delete `PAPER_PRESETS`
   (lines 199-209). Keep `ORIENTATIONS` (line 211).

9. **`deckle/app/views/layout_panel.py`** — extend the import block at lines
   26-35 to:

   ```python
   # Aliased on import. `PAPER_PRESETS` in `core.paper` is paper *stock*
   # (80gsm copier); `PAPER_SIZES` is sheet *size* (A4, Letter). This module
   # used to define a `PAPER_PRESETS` of its own meaning the second thing,
   # which is how a reader ends up wiring a dropdown to the wrong one.
   from deckle.core.paper import PT_PER_MM, PAPER_SIZES
   from deckle.core.paper import PAPER_PRESETS as PAPER_STOCKS
   from deckle.core.paper import (
       caliper_from_weight,
       from_points,
       paper_size_named,
       suggest_sheets_per_signature,
       to_points,
   )
   ```

   `caliper_pt_from_gsm` and `gsm_from_pounds` drop out of this import once
   step 11 lands, unless another site in the file still uses them — grep
   before deleting.

   `to_points` and `from_points` remain module attributes of `layout_panel`
   because they are imported into it, so every existing
   `layout_panel.to_points(...)` reference and every test that reaches them
   through the panel keeps working with no edit.

10. **`deckle/app/views/layout_panel.py`** — update the three sites that
    iterated the old table:

    - `preset_name_for` (lines 225-237): `for name, dimensions in
      PAPER_PRESETS:` → `for size in PAPER_SIZES:` with
      `tuple(sorted(size.size_pt)) == upright` returning `size.name`.
      **The function stays in this module.** It answers "what does the combo
      call this?", which is a UI question. Moving it to `core` was considered
      and rejected as churn that buys nothing and collides with M1.
    - `__init__` (lines 806-808): `for name, _dimensions in PAPER_PRESETS:` →
      `for size in PAPER_SIZES:` / `addItem(size.name)`, and
      `self._paper_names = [size.name for size in PAPER_SIZES]`.
    - `_current_paper_pt` (lines 1524-1531): replace the loop with
      `return paper_size_named(self.paper_combo.currentText()) or
      self.state.project.layout.paper`. Keep the existing comment about the
      custom entry.

11. **`deckle/app/views/layout_panel.py`** — rewrite `set_paper_from_weight`
    (lines 480-508) to keep its signature and docstring and delegate its body:

    ```python
        return set_paper_thickness_pt(
            project, caliper_from_weight(weight, unit, paper_type, grade)
        )
    ```

12. **`deckle/app/main.py`** — delete `LETTER_PT = (612.0, 792.0)` (line 37)
    and import it: add `from deckle.core.paper import LETTER_PT` beside the
    existing `from deckle.core import recent` (line 35). `default_project()`
    at line 295 is unchanged.

13. **`deckle/core/dummy.py`** — delete `LETTER_PT = (612.0, 792.0)` (line 29)
    and import it: `from deckle.core.paper import LETTER_PT`, beside the
    existing `from deckle.core.paths import atomic_output` (line 26).
    `make_numbered_pdf`'s default at line 106 is unchanged. No cycle:
    `core.paper` imports nothing from `deckle`.

14. **`tests/test_ui_surface.py`** and every other test: no edits expected.
    Run the suite; if a test fails, it is telling you a step above was done
    wrong, **except** for the two named in §6.

15. `docs/api/`: no change. No module was added.

## 4. Tests

### New file: `tests/test_paper_sizes.py`

The parity test the roadmap asks for. Its subject changes once there is one
table — "the two tables agree" becomes untestable and uninteresting — so it
asserts the property that *was* violated: **every sheet size the desktop
offers can be typed at the command line and produces the same numbers.**

```python
"""One sheet-size table, checked from both ends.

The panel offered five sizes and ``--paper`` accepted three, under a
comment in the panel claiming the two mirrored each other. A project set
to A3 in the app could not be stated at the command line at all; the only
route was to look A3 up in points and type it, which is a thing a person
gets subtly wrong once and then prints sixty sheets from.
"""
```

| Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_every_offered_size_is_accepted_by_the_command_line` | for each `size` in `PAPER_SIZES`, call `deckle.cli._parse_paper(size.key)` | returns `size.size_pt` exactly | `argparse.ArgumentTypeError: invalid paper 'a3': expected a preset (letter, a4, legal) ...` — `PAPER_SIZES` does not exist yet, so on the unfixed tree the test fails at import; write it against `layout_panel.PAPER_PRESETS` first if you want to see the real message |
| `test_the_desktop_dropdown_offers_exactly_the_shared_table` | `LayoutPanel` built offscreen against `default_project()` (follow `tests/test_layout_panel_paper.py`'s construction) | `[combo.itemText(i) for i in range(combo.count())] == [s.name for s in PAPER_SIZES]` | passes today by accident, since the panel is the table; it is here so that a future *third* table is caught from the UI side |
| `test_a_size_round_trips_through_the_cli_and_back_to_a_dropdown_name` | for each `size`: `preset_name_for(_parse_paper(size.key))` | equals `size.name` | as row 1 |
| `test_letter_is_defined_once` | `deckle.core.paper.LETTER_PT`, `deckle.cli.LETTER_PT`, `deckle.app.main.LETTER_PT`, `deckle.core.dummy.LETTER_PT` | all four are the **same object** (`is`), not merely equal | `assert cli.LETTER_PT is paper.LETTER_PT` fails today: three separate tuple literals |
| `test_the_two_unit_tables_are_one_table` | `deckle.core.paper.PT_PER_UNIT`, and `deckle.cli._UNIT_TO_PT` if it still exists | `not hasattr(deckle.cli, "_UNIT_TO_PT")` and `not hasattr(layout_panel, "LENGTH_UNITS")`, and `list(PT_PER_UNIT) == ["in", "pt", "mm", "cm"]` | both attributes exist today |
| `test_a_pound_weight_needs_a_grade_in_the_same_words_everywhere` | `pytest.raises(ValueError)` around `caliper_from_weight(20, "lb", "offset")` | the message contains `"grade"`, `"bond"` and `"cover"` | `caliper_from_weight` does not exist |
| `test_an_unknown_weight_unit_is_refused_before_anything_else` | `caliper_from_weight(20, "kg", "offset")` | raises `ValueError` whose message contains `"unknown weight unit"` | as above; and note the panel's current order would have taken the gsm branch |

The `is`-identity assertion in `test_letter_is_defined_once` is deliberate
and is the only form that works: four equal tuples are equal today, so
`==` would pass on the unfixed tree and prove nothing.

### Existing tests to extend

- **`tests/test_cli_errors.py:236-252`** — add `("a3", (841.89, 1190.55))`
  and `("tabloid", (792.0, 1224.0))` to `test_parse_paper_accepts`'s
  parametrisation. Leave `"A5"` and `"letterx"` in the reject list; both must
  still be refused.
- **`tests/test_layout_panel_paper.py`** — no change. Its
  `test_the_panel_and_the_command_line_agree_on_one_paper` (line 94) becomes
  true by construction rather than by coincidence, which is worth a sentence
  in its docstring but not a new assertion.

## 5. Acceptance

| Check | Command |
|---|---|
| The sheet-size table exists, in core | `grep -n 'PAPER_SIZES: tuple' deckle/core/paper.py` |
| …and nowhere else | `! grep -rn 'PAPER_SIZES: tuple' deckle/cli.py deckle/app` |
| The CLI's private table is gone | `! grep -n '_PAPER_PRESETS' deckle/cli.py` |
| The panel's size table is gone | `! grep -n '^PAPER_PRESETS' deckle/app/views/layout_panel.py` |
| `LETTER_PT` is defined in `core/paper.py` | `grep -n '^LETTER_PT' deckle/core/paper.py` |
| …and redefined nowhere | `! grep -rn '^LETTER_PT' deckle/cli.py deckle/app/main.py deckle/core/dummy.py` |
| The CLI's unit table is gone | `! grep -n '_UNIT_TO_PT' deckle/cli.py` |
| The panel's unit table is gone | `! grep -n 'LENGTH_UNITS' deckle/app/views/layout_panel.py` |
| The converters live in core | `grep -n '^def to_points' deckle/core/paper.py && grep -n '^def from_points' deckle/core/paper.py` |
| …and not in the panel | `! grep -n '^def to_points' deckle/app/views/layout_panel.py` |
| A3 is typable at the CLI | `.venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --paper a3` |
| Tabloid too | `.venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --paper tabloid` |
| A5 is still refused | `! .venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --paper A5 2>/dev/null` |
| `core` is still Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_core_purity.py -q --no-header -p no:cacheprovider` |
| The new file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_paper_sizes.py -q --no-header -p no:cacheprovider` |
| The paper and panel suites pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_paper.py tests/test_paper_bounds.py tests/test_cli_paper.py tests/test_cli_errors.py tests/test_layout_panel_paper.py tests/test_ui_surface.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` The dropdown still works | `run.bat` (or `.venv/bin/python -m deckle`). Page setup → Paper: the list reads Letter, A4, Legal, A3, Tabloid in that order; picking A3 changes the preview's sheet; reopening a project saved at 500×700pt shows `Custom (500 x 700pt)` and does not snap to a preset. |

`grep -rn '^LETTER_PT' deckle/` currently returns three lines
(`core/dummy.py:29`, `cli.py:68`, `app/main.py:37`) — verified — so that row
is meaningful in both directions.

## 6. Out of scope

- **B25** — `_parse_paper`'s `WxH` regex accepts `(in|pt|mm)` while its error
  message offers `cm`, and it rejects the space `_parse_length_pt` allows.
  Step 6 moves the message's *source* and must not change the regex. Fixing
  it here would silently widen `--paper` inside a refactor.
- **F11** (typing a custom paper size in the GUI). M5 gives it the table to
  work against; it does not add the control.
- **M1** (the `LayoutPanel` binding table) touches the same file and its
  acceptance includes an export check listing every public name the panel
  keeps. **M5 removes two names from that list: `PAPER_PRESETS` and
  `LENGTH_UNITS`.** `to_points`, `from_points` and `preset_name_for` all
  survive as panel attributes and need no coordination. Whichever spec lands
  second removes those two entries from M1's check; do not re-add the
  constants to make a stale check pass.
- **M4** (splitting `cli.py`). M5 deletes ~20 lines from it and moves
  nothing into a new CLI module.
- The paper *stock* table (`core.paper.PAPER_PRESETS`) and
  `suggest_sheets_per_signature` are untouched. Do not rename
  `PAPER_PRESETS`; `tests/test_paper.py` and `docs/decisions.md` both name it.
- Test-local `LETTER_PT` in `tests/test_print_painting.py:27` and
  `tests/test_golden_pinebox.py:19` stays. A test that imports the constant
  it checks checks nothing.
- Do not add units. Four is what both front ends offer.

## 7. decisions.md entry

```
## 2026-09-05 — The panel and the command line stopped keeping separate paper
- Symptom: Three copies of `LETTER_PT`, two unit-to-points tables with different key orders, two sheet-size tables -- five sizes in the panel and three at the CLI -- and two weight-to-caliper paths with two different sentences for "pounds need a grade". The panel's table carried a comment saying it mirrored the CLI's. It did not: a project set to A3 or Tabloid in the app could not be stated with `--paper` at all, and the only route was to look A3 up in points and type `841.89x1190.55pt`.
- Fix: `core.paper` gains `PAPER_SIZES` (a `PaperSize` table of the five), `paper_size_named`, `PT_PER_UNIT` with `to_points`/`from_points`, `LETTER_PT` and its four siblings, and `caliper_from_weight(weight, unit, paper_type, grade=None)`. `cli.py`, `layout_panel.py`, `app/main.py` and `core/dummy.py` import rather than redefine. `--paper a3` and `--paper tabloid` now work.
- Surfaces: `PaperSize` is a separate type from `PaperPreset` on purpose -- size versus stock -- because the two meanings of "paper" collided in this file once already and the class names are what keeps them apart. Two things stayed duplicated deliberately: the CLI keeps one sentence naming `--paper-grade`, because a core module must not name a command-line flag and "a paper grade" is a worse message than "`--paper-grade`" at a prompt; and the test-local `LETTER_PT`s stay, because a test that imports the constant it is checking checks nothing. The unit dict's insertion order is load-bearing -- it is rendered into the CLI's error text -- and is documented as such rather than sorted.
- Watch: **`72.0 / 2.54` and `PT_PER_MM * 10.0` differ in the last bit** (28.346456692913385 against 28.34645669291339), so the literal was moved rather than re-derived from the millimetre constant that was already in the file. Consolidating duplicated arithmetic is exactly where a "tidier" expression silently changes an answer. And the parity test had to change subject: with one table, "the two tables agree" is untestable, so it asserts instead that every size the dropdown offers is accepted by `--paper` and round-trips back to the same dropdown name.
- Commit: <fill in>
```

## 8. Traps

- **`core/paper.py` already has `PAPER_PRESETS`, and it means paper stock.**
  Do not put sheet sizes in it, do not rename it, and read the import comment
  at `layout_panel.py:26-31` before writing a new one.
- **Do not re-derive `cm` from `PT_PER_MM`.** `72.0 / 2.54` is
  `28.346456692913385`; `(72.0 / 25.4) * 10.0` is `28.34645669291339`. Move
  the literal.
- **Do not sort `PT_PER_UNIT`.** `cli.py:131` joins its keys into a message
  that is supposed to read the same as `_ACCEPTED_LENGTH_UNITS` at
  `cli.py:103`.
- **`deckle.core` must not import Qt** (`tests/test_core_purity.py`). Nothing
  proposed here is Qt-adjacent, but `layout_panel` is the file you will have
  open when you write the new `core` code.
- **`layout_panel` imports Qt lazily inside functions** and has pure helpers
  at the top; the moved functions come from the pure region and go to a pure
  module, so nothing about that pattern changes — but do not move a `set_*`
  mutator into `core` while you are there. Those are M1's.
- **`_parse_paper` returns presets *without* the `MIN_PAPER_PT` /
  `MAX_PAPER_PT` check** (`_reject_unprintable_paper` is only called on the
  `WxH` branch). Keep it that way: every entry in `PAPER_SIZES` is inside the
  bounds by construction, and `tests/test_paper_bounds.py` pins the bounds
  for typed sizes.
- **`paper_size_named` must be case-insensitive but not fuzzy.**
  `tests/test_cli_errors.py:256` requires `"letterx"` and `"A5"` to be
  rejected; a `startswith` or a prefix match breaks both.
- **`preset_name_for` compares `tuple(sorted(paper))`**, so it matches a size
  in either orientation. Keep that; `tests/test_ui_surface.py:897` asserts
  `(792.0, 612.0)` is `"Letter"`.
- `python -m deckle` launches the GUI. Use `python -m deckle.cli` for every
  check in §5 except the `[HUMAN]` row.
</content>
