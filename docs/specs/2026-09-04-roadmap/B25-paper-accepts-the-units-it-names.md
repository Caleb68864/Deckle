# B25 — `--paper` must accept the units its own error message lists

**Roadmap item:** `docs/ROADMAP.md` B25
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`_parse_paper`'s pattern accepts `in`, `pt` and `mm`. Its rejection message
lists `in, pt, mm, cm` — it is built from `_UNIT_TO_PT`, which has four
entries. So `--paper 5x7cm` is refused by a message that names `cm` as
acceptable:

```
invalid paper '5x7cm': expected a preset (letter, a4, legal) or WxH with an
optional unit (in, pt, mm, cm) -- e.g. 8.5x11in
```

The second half is the same defect at one remove: `_parse_length_pt` allows
an optional space before the unit (`"3 mm"`, `"5 cm"`) and documents it as
part of the interface —

> A-9: unit is optional (a bare number means points), and an optional space
> is allowed between the number and the unit -- e.g. "18", "5cm", "3 mm".

— while `_parse_paper`, written for the same user typing the same kind of
value, does not. `--gutter "5 cm"` works and `--paper "8.5x11 in"` does not.

The concrete user action: someone outside the US sizing a book to a
centimetre trim types `--paper 12.5x19cm`, reads a message telling them
`cm` is accepted, and has no way to tell what is wrong with what they
typed. The only route is to convert to millimetres by hand.

Verified on the tree at `08e7f49`:

```bash
python - <<'EOF'
from deckle.cli import _parse_paper
for value in ["5x7cm", "8.5x11 in", "letter", "8.5x11in", "216x279mm"]:
    try:
        print(repr(value), _parse_paper(value))
    except Exception as exc:
        print(repr(value), "ERR", exc)
EOF
```

Current output:

```
'5x7cm' ERR invalid paper '5x7cm': expected a preset (letter, a4, legal) or WxH with an optional unit (in, pt, mm, cm) -- e.g. 8.5x11in
'8.5x11 in' ERR invalid paper '8.5x11 in': expected a preset (letter, a4, legal) or WxH with an optional unit (in, pt, mm, cm) -- e.g. 8.5x11in
'letter' (612.0, 792.0)
'8.5x11in' (612.0, 792.0)
'216x279mm' (612.2834645669292, 790.8661417322835)
```

## 2. Current code

`deckle/cli.py:111-132`:

```python
def _parse_paper(value: str) -> tuple[float, float]:
    """A ``--paper`` value as ``(width_pt, height_pt)``.

    :param value: a preset name, or ``WxH`` with an optional unit.
    :returns: the dimensions in points.
    :raises argparse.ArgumentTypeError: unparseable, or a size no PDF can
        represent.
    """
    preset = _PAPER_PRESETS.get(value.lower())
    if preset is not None:
        return preset
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)x([0-9]*\.?[0-9]+)(in|pt|mm)?\s*$", value, re.IGNORECASE)
    if match:
        w, h, unit = match.groups()
        factor = _UNIT_TO_PT[(unit or "pt").lower()]
        paper = (float(w) * factor, float(h) * factor)
        _reject_unprintable_paper(paper, value)
        return paper
    raise argparse.ArgumentTypeError(
        f"invalid paper {value!r}: expected a preset ({', '.join(_PAPER_PRESETS)}) "
        f"or WxH with an optional unit ({', '.join(_UNIT_TO_PT)}) -- e.g. 8.5x11in"
    )
```

The alternation is `(in|pt|mm)?` and the message interpolates
`', '.join(_UNIT_TO_PT)`, which is four keys.

The tables it disagrees with, `deckle/cli.py:78-89`:

```python
_ACCEPTED_LENGTH_UNITS = ("in", "pt", "mm", "cm")

_UNIT_TO_PT = {
    "in": 72.0,
    "pt": 1.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
}

# A-9: unit is optional (a bare number means points), and an optional space
# is allowed between the number and the unit -- e.g. "18", "5cm", "3 mm".
_LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$", re.IGNORECASE)
```

and the signed variant, `deckle/cli.py:364-366`:

```python
_SIGNED_LENGTH_RE = re.compile(
    r"^\s*([+-]?[0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$", re.IGNORECASE
)
```

Both of those carry `cm` and both carry `\s*` before the unit.
`_parse_paper`'s inline pattern is the only one of the three that does not,
and it is the only one written inline rather than as a module-level
compiled constant.

`_reject_unprintable_paper`, `deckle/cli.py:141-162`, is unchanged by this
spec but runs on every accepted match, so a `cm` value that is out of PDF's
range still gets the specific message.

### Every call site

`_parse_paper`:

- `deckle/cli.py:111` (definition)
- `deckle/cli.py:752` — `--paper`'s `type=` inside `_add_layout_args`
- `deckle/cli.py:1372` — `--page-size`'s `type=` on the `dummy` subparser
- `tests/test_cli_errors.py:248`, `:261`, `:273` — direct calls
- `tests/test_paper_bounds.py` — via the CLI

`_PAPER_PRESETS`: `deckle/cli.py:72` (definition), `:119`, `:130`.
`_UNIT_TO_PT`: `deckle/cli.py:80` (definition), `:107`, `:125`, `:131`,
`:400`.
`_ACCEPTED_LENGTH_UNITS`: `deckle/cli.py:78` (definition), `:103`, `:397`.
`_LENGTH_RE`: `deckle/cli.py:89` (definition), `:99`.
`_SIGNED_LENGTH_RE`: `deckle/cli.py:364` (definition), `:392`.

`grep -rn "_parse_paper" --include="*.py" .` returns exactly the six lines
above.

### Existing tests over this code

`tests/test_cli_errors.py:234-278`:

- `::test_parse_paper_accepts` — parametrised over `letter`, `LETTER`,
  `a4`, `legal`, `8.5x11in`, `612x792`, `612x792pt`, `3x3pt`. **No `cm`
  case, no `mm` case, no spaces.**
- `::test_parse_paper_rejects_nonsense` — parametrised over `""`, `"x"`,
  `"8.5x"`, `"x11"`, `"8.5*11"`, `"letterx"`, `"A5"`, **`"8.5 x 11in"`**,
  `"-1x5in"`.
- `::test_parse_paper_rejects_sizes_no_pdf_can_hold` — `0x0`, `1x1pt`,
  `2.9x11pt`, `300x300in`, `1x20000pt`.
- `::test_the_limits_are_exactly_pikepdfs`

`tests/test_cli_errors.py:51-90` covers the same ground through the CLI,
including `::test_usable_paper_sizes_are_still_accepted` over `letter`,
`a4`, `legal`, `8.5x11in`, `612x792`, `216x279mm`.

**`"8.5 x 11in"` — a space around the `x` — is pinned as a rejection.**
That is load-bearing for §3.

## 3. Change

### The rule

`_parse_paper` accepts, for the `WxH` form:

- an optional leading and trailing run of whitespace (already accepted);
- a non-negative decimal number;
- the literal `x` or `X`, with **no** whitespace around it;
- a second non-negative decimal number;
- an optional run of whitespace;
- an optional unit from `in`, `pt`, `mm`, `cm`, case-insensitive;
- an optional trailing run of whitespace.

No unit means points, as before.

**Whitespace around the `x` stays rejected.**
`tests/test_cli_errors.py:258` pins `"8.5 x 11in"` as nonsense, and that is
the right call rather than an accident to work around: `x` is the separator
between two values, not a unit suffix on one of them, and a value with
spaces in the middle has to be quoted for the shell anyway. This spec
changes what the *unit* may be preceded by, which is the thing
`_parse_length_pt` already allows and `_parse_paper` did not. The rejection
test stays exactly as it is.

Rejected: allowing spaces around `x` too, to "make the two parsers agree".
They are not parsing the same grammar — one takes a length, the other takes
a pair — and it would delete a pinned assertion for no user need.

Rejected: routing `_parse_paper` through `_parse_length_pt` by splitting on
`x` first. It reads well until the unit: `"8.5x11in"` splits into `"8.5"`
and `"11in"`, and feeding `"8.5"` to `_parse_length_pt` yields **8.5
points**, not 8.5 inches. Making the trailing unit apply to both halves
means re-parsing anyway, and it would silently accept `"8.5mmx11in"`. The
single regex is the honest shape. **M5** consolidates unit handling into
`core/paper.py`; when it does, this pattern goes with it.

### The pattern

Lifted to a module-level compiled constant beside its two siblings, so
there are three named patterns rather than two named and one inline:

```python
# The `WxH` half of a `--paper` value. A module constant beside
# `_LENGTH_RE` and `_SIGNED_LENGTH_RE` rather than inline in the function:
# it was the only one of the three written inline, and it was the only one
# that had drifted -- missing `cm` (which its own error message lists) and
# missing the optional space before the unit that `_LENGTH_RE` documents.
#
# Whitespace is allowed before the unit and not around the `x`. The `x` is
# the separator between two values, not a suffix on one of them, and
# `tests/test_cli_errors.py::test_parse_paper_rejects_nonsense` pins
# `"8.5 x 11in"` as a refusal.
_PAPER_RE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+)x([0-9]*\.?[0-9]+)\s*(in|pt|mm|cm)?\s*$",
    re.IGNORECASE,
)
```

### Steps

1. **`deckle/cli.py`** — add `_PAPER_RE` immediately after `_LENGTH_RE`
   (after line 89), with the comment above.

2. **`deckle/cli.py:122`** — replace the inline `re.match(...)` with
   `_PAPER_RE.match(value)`:

   ```python
       match = _PAPER_RE.match(value)
   ```

3. **`_parse_paper`'s docstring** (lines 112-118) — the `:param value:`
   line says "a preset name, or ``WxH`` with an optional unit". Name the
   units and the space, matching `_parse_length_pt`'s docstring style:

   ```python
       """A ``--paper`` value as ``(width_pt, height_pt)``.

       :param value: a preset name (``letter``, ``a4``, ``legal``), or
           ``WxH`` with an optional unit -- ``in``, ``pt``, ``mm`` or
           ``cm``, with an optional space before it (``8.5x11in``,
           ``216x279mm``, ``12.5x19 cm``). No unit means points.
           Whitespace around the ``x`` is not accepted: it separates two
           values rather than suffixing one.
       :returns: the dimensions in points.
       :raises argparse.ArgumentTypeError: unparseable, or a size no PDF
           can represent.
       """
   ```

4. **The error message** (lines 129-132) — unchanged. It was already
   correct; the pattern was what disagreed with it. Do not "fix" it to list
   three units.

5. **`--paper`'s help text** (`deckle/cli.py:751-754`) — currently
   `"paper size: a preset (letter, a4, legal) or WxH[unit] (default:
   letter)"`. Leave it; `WxH[unit]` is accurate and the full list is one
   `--paper garbage` away. (Stated so the implementer does not change it
   and break a substring assertion by accident.)

6. **`docs/GUIDE.md`** — two places name the `WxH` form and neither lists
   the units:

   - `docs/GUIDE.md:53-54`: "Any other size goes in as `WxH` with a unit —
     `--paper 500x700pt`, `--paper 210x297mm`."
   - `docs/GUIDE.md:757` (the §8 reference table): "`letter`, `a4`,
     `legal`, or `WxH[unit]` such as `500x700pt`."

   Add the four units to the table row: "`WxH[unit]` — `in`, `pt`, `mm` or
   `cm`, with an optional space before the unit — such as `500x700pt` or
   `12.5x19 cm`." Leave line 53-54 alone; it is prose with two examples,
   not a list claiming to be complete. **D5** records §8's larger omissions
   and is not this spec's.

## 4. Tests

The roadmap asks for unit tests over `_parse_paper` covering **every**
accepted form. They go in `tests/test_cli_errors.py`, extending the two
existing parametrised tests at lines 234-264 rather than adding a third
list — the file already has the right shape and a second list of accepted
values is the thing to avoid.

### `test_parse_paper_accepts` — extend the parametrisation

Add to the existing list at `tests/test_cli_errors.py:235-245`, so it
covers every accepted form exactly once:

| Value | Expected | Form it covers |
|---|---|---|
| `"letter"` | `(612.0, 792.0)` | preset (already present) |
| `"LETTER"` | `(612.0, 792.0)` | preset, case-insensitive (already present) |
| `"a4"` | `(595.28, 841.89)` | preset (already present) |
| `"legal"` | `(612.0, 1008.0)` | preset (already present) |
| `"612x792"` | `(612.0, 792.0)` | no unit → points (already present) |
| `"612x792pt"` | `(612.0, 792.0)` | explicit `pt` (already present) |
| `"3x3pt"` | `(3.0, 3.0)` | the lower bound (already present) |
| `"8.5x11in"` | `(612.0, 792.0)` | `in` (already present) |
| **`"216x279mm"`** | `(612.28, 790.87)` | **`mm`, never unit-tested** |
| **`"21.6x27.9cm"`** | `(612.28, 790.87)` | **`cm`, the bug** |
| **`"8.5x11 in"`** | `(612.0, 792.0)` | **space before the unit** |
| **`"216x279 mm"`** | `(612.28, 790.87)` | space + `mm` |
| **`"21.6x27.9 cm"`** | `(612.28, 790.87)` | space + `cm` |
| **`"8.5X11IN"`** | `(612.0, 792.0)` | uppercase unit |
| **`"612x792 "`** | `(612.0, 792.0)` | trailing space, no unit |
| **`" 612x792"`** | `(612.0, 792.0)` | leading space |
| **`"612.x792."`** | `(612.0, 792.0)` | trailing decimal point (the `[0-9]*\.?[0-9]+` shape accepts `612.`? **check it** — it does not: the pattern requires at least one digit after the dot. Use `".5x.5in"` instead, which it does accept, and assert `(36.0, 36.0)`) |
| **`"20x30cm"`** | `(566.93, 850.39)` | a real metric book size |

The comparison is already `pytest.approx`, so the `mm`/`cm` conversions do
not need exact reprs.

- **Expected failure on the unfixed tree:** the five `cm` cases and the four
  space cases fail with
  `argparse.ArgumentTypeError: invalid paper '21.6x27.9cm': expected a preset ...`.
  The `mm` and case cases pass.

### `test_parse_paper_rejects_nonsense` — extend, do not weaken

Keep every existing case, **including `"8.5 x 11in"`**. Add:

| Value | Why |
|---|---|
| `"8.5x11 furlongs"` | an unknown unit is still refused |
| `"8.5x11i n"` | a space *inside* the unit |
| `"8.5x 11in"` | space after the `x` — the same rule as before it |
| `"cm"` | a bare unit |
| `"8.5x11cm extra"` | trailing junk |

- **Unfixed tree:** all pass (everything is rejected today).

### `test_the_paper_units_match_the_message_that_names_them`

- **File / function:** `tests/test_cli_errors.py::test_the_paper_units_match_the_message_that_names_them`
- **Setup:** for each key in `deckle.cli._UNIT_TO_PT`, call
  `_parse_paper(f"100x100{unit}")`.
- **Assertion in words:** none of them raises. The message is built from
  `_UNIT_TO_PT`, so the pattern must accept everything in it — this is the
  test that fails when the two drift again, which is what happened.
  Docstring: the defect was not that `cm` was missing, it was that a
  hand-written alternation and a generated message could disagree at all.
- **Expected failure on the unfixed tree:**
  `argparse.ArgumentTypeError: invalid paper '100x100cm': ...` for the `cm`
  key.

### `test_the_three_length_patterns_accept_the_same_units`

- **Setup:** for each of `_LENGTH_RE`, `_SIGNED_LENGTH_RE`, `_PAPER_RE`,
  extract the unit alternation group from `pattern.pattern` — or, simpler
  and less brittle, assert behaviourally: every unit in `_UNIT_TO_PT` is
  accepted by `_parse_length_pt(f"1{u}")`, `_parse_offset_pair(f"1{u},1{u}")`
  and `_parse_paper(f"100x100{u}")`, with and without a space before the
  unit.
- **Assertion in words:** all three accept all four units, spaced and
  unspaced. This is the parity check the roadmap's §5 asks for in spirit,
  scoped to the one file that has three copies of the question.
- **Expected failure on the unfixed tree:** the `_parse_paper` half fails on
  `cm` and on every spaced form.

### `test_a_centimetre_paper_size_works_from_the_command_line`

- **File / function:** `tests/test_cli_errors.py::test_a_centimetre_paper_size_works_from_the_command_line`
- **Setup:** the file's `_cli` helper;
  `_cli("export", FIXTURE, "-o", str(out), "--paper", "21.6x27.9cm")`.
- **Assertion in words:** exit 0, and the output PDF's first `MediaBox` is
  approximately 612.28 × 790.87 points. The unit test proves the parser;
  this proves the value reaches paper.
- **Unfixed tree:** exit 2 from argparse, with the message naming `cm` as
  an accepted unit.

### Tests that must keep passing untouched

- `tests/test_cli_errors.py::test_parse_paper_rejects_sizes_no_pdf_can_hold`
  — the bounds check runs after the match, so a `cm` value out of range
  still gets the specific message. Consider adding `"0.01x0.01cm"` to it.
- `tests/test_cli_errors.py::test_usable_paper_sizes_are_still_accepted`
- `tests/test_paper_bounds.py` — the whole file.
- `tests/test_dummy.py` — `--page-size` uses the same parser.

## 5. Acceptance

| Check | Command |
|---|---|
| The pattern is a named constant, not inline | `grep -n "_PAPER_RE = re.compile" deckle/cli.py` |
| No inline paper regex remains | `! grep -n 'x(\[0-9\]\*' deckle/cli.py` |
| Every unit the message names is accepted | `.venv/bin/python -c "from deckle.cli import _UNIT_TO_PT, _parse_paper; [_parse_paper(f'100x100{u}') for u in _UNIT_TO_PT]"` |
| A space before the unit is accepted | `.venv/bin/python -c "from deckle.cli import _parse_paper; assert _parse_paper('8.5x11 in') == (612.0, 792.0)"` |
| A space around the `x` is still refused | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider "tests/test_cli_errors.py::test_parse_paper_rejects_nonsense"` |
| Centimetres reach the page box | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_errors.py -k "centimetre or units_match_the_message or three_length_patterns"` |
| Every paper test still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_errors.py tests/test_paper_bounds.py tests/test_cli_paper.py tests/test_dummy.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: row 1's grep returns nothing. Row 3 exits
non-zero with
`argparse.ArgumentTypeError: invalid paper '100x100cm': expected a preset
(letter, a4, legal) or WxH with an optional unit (in, pt, mm, cm) -- e.g.
8.5x11in`. Row 4 exits non-zero the same way. Row 5 exits 0 today and must
keep exiting 0.

Row 2's grep currently returns `deckle/cli.py:122`, the inline pattern, and
must return nothing.

The proposed `_PAPER_RE` was run against every value named in §4 before
this spec was written. It accepts `.5x.5in`, `8.5x11 in`, `21.6x27.9cm`,
`8.5X11IN`, `612x792 ` and ` 612x792`; it rejects `612.x792.`,
`8.5 x 11in`, `8.5x 11in`, `8.5x11 furlongs` and `cm`.

## 6. Out of scope

- **M5** — consolidating `_UNIT_TO_PT`, `to_points`/`from_points` and the
  paper presets into `deckle/core/paper.py`. `00-environment.md` is
  explicit: until M5 lands, use the copy in the layer you are editing and
  do not add a third. This spec adds no new conversion table; it makes an
  existing pattern agree with the table already beside it.
- **The GUI/CLI paper-preset divergence** (`_PAPER_PRESETS` has 3 entries,
  the panel has 5, so a GUI-saved A3 project cannot be typed at the CLI).
  Also M5.
- **F11** — typing a custom paper size in the GUI.
- **B21**, **B22**, **B23**, **B24** — same file, unrelated functions.
- **Negative dimensions.** `"-1x5in"` stays rejected; a sheet has a
  magnitude, and `_reject_unprintable_paper` would refuse it anyway.

## 7. decisions.md entry

```
## 2026-09-05 — `--paper` refused a unit its own error message offered
- Symptom: `_parse_paper`'s inline pattern accepted `in|pt|mm` while its rejection message was built from `_UNIT_TO_PT`, which has four keys -- so `--paper 5x7cm` was refused by a sentence listing `cm` as acceptable, with nothing to tell the user what was actually wrong. The same pattern also disallowed the optional space before the unit that `_parse_length_pt` documents and accepts, so `--gutter "5 cm"` worked and `--paper "8.5x11 in"` did not.
- Fix: `_PAPER_RE`, a module-level compiled constant beside `_LENGTH_RE` and `_SIGNED_LENGTH_RE`, carrying `cm` and `\s*` before the unit. Whitespace around the `x` stays rejected -- it separates two values rather than suffixing one, and a test already pins it. The error message was already right and is unchanged.
- Surfaces: Three patterns for the same question, two of them named constants and one written inline inside the function -- and the inline one was the one that had drifted. The unit tests over `_parse_paper` covered `in` and `pt` and neither `mm` nor `cm`, so half the table was never exercised.
- Watch: An error message generated from a table, beside a parser written by hand, cannot be trusted to agree with itself. The regression test asserts the two directly: everything in `_UNIT_TO_PT` must parse.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B21, B22, B23, B24 and B25 all edit `deckle/cli.py`.
  Recommended order: **B25 → B23 → B21 → B24 → B22.** B25 is first because
  it is the smallest and touches only lines 89-132, which none of the other
  four go near.
- **`"8.5 x 11in"` must stay rejected.**
  `tests/test_cli_errors.py:258` pins it. A pattern with `\s*` on both
  sides of the `x` would break that test, and the right response is to keep
  the pattern narrow rather than to delete the assertion.
- **`[0-9]*\.?[0-9]+` requires a digit after the decimal point.** `".5"`
  parses; `"612."` does not. Check any new accepted-value case against the
  pattern before adding it, and do not add `"612.x792."`.
- **`_parse_paper` is `type=` for two flags** — `--paper` (all four
  layout-bearing subparsers) and `dummy --page-size`. Widening it widens
  both, which is correct and worth knowing.
- **Do not route `_parse_paper` through `_parse_length_pt`.** Splitting on
  `x` first makes `"8.5x11in"` yield 8.5 **points** for the width.
- **`re.IGNORECASE` covers the `x` too**, so `8.5X11in` already parses.
  That is existing behaviour, not something to remove.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
