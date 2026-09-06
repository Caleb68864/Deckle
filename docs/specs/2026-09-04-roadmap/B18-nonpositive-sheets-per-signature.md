# B18 — Refuse a non-positive `sheets_per_signature` the same way on both paths

**Roadmap item:** `docs/ROADMAP.md` B18
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none. §3 picks the message and the exception
type by copying the one `blank_mode="end"` already raises.

---

## 1. Context

`_signature_sheet_groups` has two branches. The `"end"` branch delegates
to `signatures.split_signatures`, which validates and raises a clear
`ValueError`. The `"balanced"` branch does its own ceiling division and
validates nothing:

```python
    n_groups = -(-sheet_count // sheets_per_signature)  # ceil division
```

With `sheets_per_signature == 0` that is `ZeroDivisionError`. With a
negative it produces `n_groups <= 0`, then `sizes` is an empty list,
`groups` is `[]`, and imposition continues with a plan whose sheets
belong to no signature at all.

Verified:

```bash
.venv/bin/python - <<'PYEOF'
from deckle.core.layout import SaddleStitchStrategy, _signature_sheet_groups
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

def pages(n):
    return [SourcePage(ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                                     width_pt=396.0, height_pt=612.0),
                       rotate_deg=0, skipped=False) for i in range(n)]

for sps, mode in ((0, "balanced"), (0, "end"), (-3, "balanced"), (-3, "end")):
    try:
        print(f"groups  {sps:3} {mode:9} ->", _signature_sheet_groups(4, sps, mode))
    except Exception as exc:
        print(f"groups  {sps:3} {mode:9} -> {type(exc).__name__}: {exc}")

for sps, mode in ((0, "balanced"), (0, "end"), (-3, "balanced"), (-3, "end")):
    s = LayoutSettings(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                       fold_scheme="folio", sheets_per_signature=sps,
                       blank_mode=mode)
    try:
        plan = SaddleStitchStrategy().impose(pages(16), s)
        print(f"impose  {sps:3} {mode:9} ->",
              [len(x.sheet_indices) for x in plan.signatures])
    except Exception as exc:
        print(f"impose  {sps:3} {mode:9} -> {type(exc).__name__}: {exc}")
PYEOF
```

Output:

```
groups    0 balanced  -> ZeroDivisionError: division by zero
groups    0 end       -> ValueError: sheets_per_signature must be a positive integer, got 0
groups   -3 balanced  -> []
groups   -3 end       -> ValueError: sheets_per_signature must be a positive integer, got -3
impose    0 balanced  -> ZeroDivisionError: division by zero
impose    0 end       -> ValueError: sheets_per_signature must be a positive integer, got 0
impose   -3 balanced  -> AssertionError: signature slot counts must sum to the padded page count
impose   -3 end       -> ValueError: sheets_per_signature must be a positive integer, got -3
```

**Two corrections to the roadmap.** It says "negatives produce empty
groups"; at the *`impose`* level a negative is caught 130 lines later by
one of the strategy's four post-conditions
(`deckle/core/layout.py:1050-1052`) and surfaces as an
`AssertionError` naming slot counts — a message about the wrong thing,
from the wrong layer. And because those are `assert` statements, under
`python -O` they vanish and a negative silently produces a plan with
sheets and **no signatures**, which the schedule then renders as
"nothing to gather or sew" for a document that is folded into
signatures. (`assert`-based invariants under `-O` is B35's general note;
this is one concrete instance of it.)

**Where the value comes from.** Three routes, and only one of them is
guarded:

- **The CLI.** `deckle/cli.py` declares `--sheets-per-signature` as
  `type=int` with no bound — `grep -n "sheets-per-signature" deckle/cli.py`.
  `deckle impose book.pdf -o out.deckle --fold-scheme folio
  --sheets-per-signature 0 --blank-mode balanced` reaches
  `ZeroDivisionError` and prints a traceback.
- **A `.deckle` file.** `core.schema.check_values` checks the field is an
  `int` and nothing more (`deckle/core/schema.py:105-107`), and
  `project_io._check_layout_values` deliberately bounds only `paper`,
  saying so at `deckle/core/project_io.py:330-338`: "`sheets_per_signature`
  and `signature_lengths` are already validated where they are used, with
  messages naming the numbers involved." That claim is true for
  `blank_mode="end"` and false for `"balanced"`.
- **The GUI.** `layout_panel`'s spin box has a minimum, so the desktop
  app cannot produce it.

**Why it matters to a person printing a book.** Under `-O` — or under any
future build that ships without assertions — a typo in a saved project
turns a 67-sheet folio job into a document with no gatherings, and the
schedule prints "This document is imposed one page per side, not folded
into signatures" over a plan that plainly is. Everything else about the
sheets is correct, so the mistake is invisible until the paper is folded.

## 2. Current code

### `deckle/core/layout.py:745-780` — the grouping

```python
def _signature_sheet_groups(
    sheet_count: int,
    sheets_per_signature: int,
    blank_mode: str,
    lengths: Sequence[int] | None = None,
) -> list[tuple[int, ...]]:
    """Group sheet indices ``0..sheet_count-1`` into signatures.

    ``"end"`` reuses ``split_signatures`` directly -- contiguous groups with
    the whole remainder in the final group. ``"balanced"`` keeps the same
    number of groups but distributes the remainder across the *front*
    groups, so the tail groups -- which is where the padding blanks land,
    since sheets are always assigned to groups in ascending order -- are
    never more than one sheet thinner than their neighbours.
    """
    if sheet_count <= 0:
        return []
    if lengths:
        # An explicit list wins over both of the other two: they
        # describe how to DERIVE a grouping, and the user has
        # stated one instead.
        return split_signatures_at(sheet_count, lengths)
    if blank_mode != "balanced":
        return split_signatures(sheet_count, sheets_per_signature)

    n_groups = -(-sheet_count // sheets_per_signature)  # ceil division
    base = sheet_count // n_groups
    extra = sheet_count % n_groups
    sizes = [base + 1] * extra + [base] * (n_groups - extra)

    groups: list[tuple[int, ...]] = []
    start = 0
    for size in sizes:
        groups.append(tuple(range(start, start + size)))
        start += size
    return groups
```

Line 770 is the defect. Note also that `lengths` is checked first, so
`signature_lengths` shadows the problem entirely — `split_signatures_at`
validates its own input.

### `deckle/core/signatures.py:36-68` — the message to copy

```python
def split_signatures(
    sheet_count: int, sheets_per_signature: int
) -> list[tuple[int, ...]]:
    """Group sheet indices ``0..sheet_count-1`` into contiguous signatures.
    ...
    :raises ValueError: if ``sheet_count`` is negative, or
        ``sheets_per_signature`` is not a positive integer.
    """

    if sheet_count < 0:
        raise ValueError(f"sheet_count must be >= 0, got {sheet_count}")
    if sheets_per_signature <= 0:
        raise ValueError(
            f"sheets_per_signature must be a positive integer, got {sheets_per_signature}"
        )

    groups: list[tuple[int, ...]] = []
    for start in range(0, sheet_count, sheets_per_signature):
        end = min(start + sheets_per_signature, sheet_count)
        groups.append(tuple(range(start, end)))
    return groups
```

### `deckle/core/layout.py:910-916` — the only production call

```python
        sheets_n = len(slots) // 4
        groups = _signature_sheet_groups(
            sheets_n,
            settings.sheets_per_signature,
            settings.blank_mode,
            settings.signature_lengths,
        )
```

### `deckle/core/layout.py:1042-1057` — the post-conditions a negative trips

```python
        # Invariants, verified rather than trusted -- the SS-03 precedent.
        all_sheet_indices = [i for sig in signatures for i in sig.sheet_indices]
        assert all_sheet_indices == list(range(len(sheets))), (
            "signature sheet_indices must be contiguous, gapless, and cover "
            "every sheet exactly once, in binding order"
        )
        assert all(len(sig_slice) % 4 == 0 for sig_slice in signature_slices), (
            "every signature's slot count must be a multiple of 4"
        )
        assert sum(len(sig_slice) for sig_slice in signature_slices) == len(slots), (
            "signature slot counts must sum to the padded page count"
        )
```

### Every call site

`grep -rn "_signature_sheet_groups" --include='*.py' .` (excluding
`.venv`):

- `deckle/core/layout.py:745` — the definition.
- `deckle/core/layout.py:911` — `SaddleStitchStrategy.impose`.
- `tests/test_layout_saddle.py:26` — imported.
- `tests/test_layout_saddle.py:326-331` —
  `test_signature_sheet_groups_balanced_shaves_the_tail`, the only direct
  test, calling `_signature_sheet_groups(70, 8, "end")` and
  `(70, 8, "balanced")`.

`grep -rn "sheets_per_signature" deckle/cli.py deckle/app/views/layout_panel.py`
gives the two producers named in §1.

### Existing tests

- `tests/test_layout_saddle.py:313-331` — the three `blank_mode` tests.
- `tests/test_imposition_properties.py:83` —
  `sheets_per_signature = st.integers(min_value=1, max_value=8)`. The
  property tests deliberately never generate `0`; §4 adds the negative
  case as an example test rather than widening the strategy, because a
  refusal is not a property of a plan.
- `tests/test_custom_signatures.py` — `signature_lengths`, which shadows
  this path.
- **Nothing anywhere passes `0` or a negative.**
  `grep -rn "sheets_per_signature=0\|sheets_per_signature=-" tests/`
  returns nothing.

## 3. Change

### The chosen design

`_signature_sheet_groups` validates `sheets_per_signature` **before**
branching on `blank_mode`, raising the identical `ValueError`
`split_signatures` already raises, so the two branches refuse the same
input with the same words.

Rejected: clamping to `max(1, sheets_per_signature)`. A gathering size of
zero is not a preference Deckle can honour by guessing — it is a typo or
a bad file, and the `"end"` path has raised on it since SS-03. Two paths
answering "0" differently is the defect; making both guess would only
move it.

Rejected: validating in `project_io._check_layout_values`. That function
says outright why it does not (`deckle/core/project_io.py:330-338`) —
`sheets_per_signature` is "already validated where it is used, with
messages naming the numbers involved". This spec makes that sentence
true rather than contradicting it.

### The message

Byte-identical to `split_signatures`':

```
sheets_per_signature must be a positive integer, got {sheets_per_signature}
```

so a user who hits it from either branch searches for one string, and a
test can assert one string.

### Numbered edits

All in `deckle/core/layout.py`, function `_signature_sheet_groups`.

1. **After line 762** (`return split_signatures_at(sheet_count, lengths)`)
   and **before line 763** (`if blank_mode != "balanced":`), insert:

   ```python
       if sheets_per_signature <= 0:
           # Checked here rather than only inside `split_signatures`,
           # because the "balanced" branch below never reaches it: `0` was
           # a ZeroDivisionError out of the ceil division, and a negative
           # produced `n_groups <= 0`, an empty `sizes`, and a plan whose
           # sheets belonged to no signature at all -- caught 130 lines
           # later by a post-condition about slot counts, and not caught at
           # all under `python -O`. Same message as `split_signatures`, so
           # both modes refuse the same input in the same words.
           raise ValueError(
               "sheets_per_signature must be a positive integer, got "
               f"{sheets_per_signature}"
           )
   ```

   Placed **after** the `lengths` branch on purpose: an explicit
   `signature_lengths` wins over `sheets_per_signature`
   (`deckle/core/models.py:413-429`, "When set, this wins over
   `sheets_per_signature` and over `blank_mode`"), so a project that
   states its groupings must not be refused for a value it is not using.

2. **Extend the docstring**, replacing the closing `"""` block's last
   paragraph with an added `:raises:` entry:

   ```python
       :param sheet_count: how many sheets there are. ``0`` or fewer yields
           no groups.
       :param sheets_per_signature: the derived group size. Ignored when
           ``lengths`` is given.
       :param blank_mode: ``"end"`` or ``"balanced"``.
       :param lengths: an explicit grouping, which wins over the other two.
       :returns: the groups, in binding order.
       :raises ValueError: ``sheets_per_signature`` is not a positive
           integer and no ``lengths`` were given. Both modes raise this;
           ``"balanced"`` used to divide by it instead.
   ```

3. **`deckle/core/models.py:314-315`**, the `sheets_per_signature` field
   doc, replace

   ```python
       :ivar sheets_per_signature: how many sheets are nested into one
           gathering under ``folio``.
   ```

   with

   ```python
       :ivar sheets_per_signature: how many sheets are nested into one
           gathering under ``folio``. Must be a positive integer; imposition
           raises ``ValueError`` otherwise, under either ``blank_mode``.
           Ignored entirely when ``signature_lengths`` is set.
   ```

4. **`docs/api/`** — nothing to add.

### What does not change

- `sheet_count <= 0` still returns `[]` before any validation. An empty
  document is not an error, and the early return at line 759 stays first.
- `signature_lengths` still shadows the check.
- `split_signatures`' own validation stays; it is reachable from
  elsewhere and this is not a reason to remove a guard.
- The four post-conditions at `deckle/core/layout.py:1042-1057` stay.
  They now become unreachable for this input, which is what a
  post-condition should be.

## 4. Tests

Write these first. `tests/test_layout_saddle.py` already imports
`_signature_sheet_groups` (`:26`) and has `make_pages` / `settings` /
`impose` helpers (`:37-60`).

### `tests/test_layout_saddle.py`

**`test_a_zero_signature_size_is_refused_under_every_blank_mode`**
Parametrised over `blank_mode` in `("end", "balanced")`:
`pytest.raises(ValueError)` around `_signature_sheet_groups(4, 0, mode)`,
and assert `"positive integer"` and `"0"` are in the message.
Unfixed: `"balanced"` raises `ZeroDivisionError: division by zero`, which
`pytest.raises(ValueError)` does not catch — the test errors.

**`test_a_negative_signature_size_is_refused_under_every_blank_mode`**
The same with `-3`; assert the message names `-3`.
Unfixed: `"balanced"` returns `[]` and nothing raises —
`DID NOT RAISE <class 'ValueError'>`.

**`test_both_blank_modes_refuse_a_bad_size_in_the_same_words`**
Capture both messages via `pytest.raises(...).value` and assert they are
equal strings.
Unfixed: only one of the two is a `ValueError`.

**`test_imposing_with_a_bad_signature_size_names_the_setting`**
`pytest.raises(ValueError)` around
`impose(make_pages(16), settings(sheets_per_signature=0, blank_mode="balanced"))`,
and assert `"sheets_per_signature"` is in the message.
Unfixed: `ZeroDivisionError: division by zero` — a message that names
neither the setting nor the value.

**`test_a_negative_size_does_not_reach_the_post_conditions`**
`impose(make_pages(16), settings(sheets_per_signature=-3, blank_mode="balanced"))`
raises `ValueError`, **not** `AssertionError`. Assert on the type
explicitly with `pytest.raises(ValueError)` and additionally that
`"positive integer"` is in the message — an `AssertionError` is also
caught by a bare `except Exception`, so the type is the assertion.
Unfixed: `AssertionError: signature slot counts must sum to the padded
page count`.

**`test_explicit_signature_lengths_win_over_a_bad_size`**
`_signature_sheet_groups(4, 0, "balanced", lengths=[4])` returns
`[(0, 1, 2, 3)]` without raising, and
`impose(make_pages(16), settings(sheets_per_signature=0, blank_mode="balanced", signature_lengths=(4,)))`
succeeds with one signature of 4 sheets.
Unfixed: passes for the direct call (the `lengths` branch already returns
first) and passes for `impose` — this is the guard that step 1 is
inserted *after* the `lengths` branch, not before it. Keep it.

**`test_an_empty_document_is_still_not_an_error`**
`_signature_sheet_groups(0, 0, "balanced") == []`. The `sheet_count <= 0`
early return must stay ahead of the new check.
Unfixed: passes (`sheet_count <= 0` already returns first). Keep it as
the ordering guard.

### `tests/test_cli_errors.py`

That module already has the `_cli(*args)` helper (`:30-40`), which runs
`python -m deckle.cli` out of process with `PYTHONPATH` set, and its
docstring is exactly this territory: "everything either handles itself or
fails with something a person can act on."

**`test_a_zero_signature_size_is_reported_not_traced`**

```python
def test_a_zero_signature_size_is_reported_not_traced(tmp_path):
    result = _cli(
        "impose", FIXTURE, "-o", os.path.join(str(tmp_path), "out.deckle"),
        "--fold-scheme", "folio",
        "--sheets-per-signature", "0",
        "--blank-mode", "balanced",
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "sheets_per_signature must be a positive integer" in result.stderr
```

**No CLI edit is needed.** `_impose_or_report`
(`deckle/cli.py:923-949`) already wraps `impose` in
`except ValueError as exc: print(f"error: {exc}", file=sys.stderr)` and
returns `None`, which every command turns into exit 1 — its docstring
says it exists precisely because "a plain typo produced a traceback". A
`ZeroDivisionError` is not a `ValueError`, so it walked straight past it.
Changing the exception type is the whole fix.

Verified on the unfixed tree:

```
$ .venv/bin/python -m deckle.cli impose tests/fixtures/sample.pdf \
    -o /tmp/b18.deckle --fold-scheme folio \
    --sheets-per-signature 0 --blank-mode balanced
Traceback (most recent call last):
  ...
  File ".../deckle/core/layout.py", line 770, in _signature_sheet_groups
    n_groups = -(-sheet_count // sheets_per_signature)  # ceil division
ZeroDivisionError: division by zero
$ echo $?
1
```

So the exit code is already 1 and only the presentation is wrong —
`assert "Traceback" not in result.stderr` is the assertion that fails.

## 5. Acceptance

| Check | Command |
|---|---|
| the check exists | `grep -n "sheets_per_signature must be a positive integer" deckle/core/layout.py` |
| both modules say it identically | `test "$(grep -rho 'sheets_per_signature must be a positive integer, got' deckle/ \| sort -u \| wc -l)" = 1` |
| the check sits after the `lengths` branch | `.venv/bin/python -c "import inspect, deckle.core.layout as m; s = inspect.getsource(m._signature_sheet_groups); assert s.index('split_signatures_at') < s.index('must be a positive integer'), 'the guard must not shadow explicit signature_lengths'"` |
| the repro from §1 refuses cleanly | paste §1's fenced block; all eight lines must read `ValueError: sheets_per_signature must be a positive integer, got …` except the two `sheet_count`-independent `lengths`-free `end` rows, which already do |
| the new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k "signature_size or blank_mode or lengths_win or empty_document"` |
| the CLI does not traceback | `! .venv/bin/python -m deckle.cli impose tests/fixtures/sample.pdf -o /tmp/b18.deckle --fold-scheme folio --sheets-per-signature 0 --blank-mode balanced 2>&1 \| grep -q Traceback` |
| …and says what is wrong | `.venv/bin/python -m deckle.cli impose tests/fixtures/sample.pdf -o /tmp/b18.deckle --fold-scheme folio --sheets-per-signature 0 --blank-mode balanced 2>&1 \| grep -q 'must be a positive integer'` |
| the new CLI test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cli_errors.py -q --no-header -p no:cacheprovider -k signature_size` |
| the post-conditions still hold everywhere else | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py tests/test_custom_signatures.py -q --no-header -p no:cacheprovider` |
| the properties still hold | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_imposition_properties.py tests/test_imposition_properties_settings.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

- **Bounding the value at the CLI's `argparse` layer.** `--sheets-per-signature`
  could take a bounded `type=`, and `--sewing-stations`,
  `--paper-thickness` and the rest have the same shape. That is a
  parser-wide decision and belongs with **M4**'s `cli/values.py`. One
  well-placed refusal in the core is what makes every entry point safe;
  the CLI's job is only to report it without a traceback.
- **Bounding it in `project_io`.** `_check_layout_values` deliberately
  bounds only `paper` and explains why
  (`deckle/core/project_io.py:330-338`). Do not add a second check there;
  that comment would then be wrong in the other direction.
- **B35's general `-O` point** — that `assert`-based invariants vanish
  under optimisation. This spec removes one input that depended on them;
  it does not convert the four post-conditions into raises.
- **B8** — the creep advisory. It reads `groups`, which this spec can
  make raise before producing. The interaction is benign in both orders:
  B8's `max(..., default=0)` handles an empty `groups`, and after this
  spec an empty `groups` from a bad size is unreachable.
- **`signature_lengths` validation.** `split_signatures_at` already
  refuses an empty list, a value below one, and a sum that does not
  match, with messages naming the numbers
  (`deckle/core/signatures.py:98-110`), and
  `tests/test_imposition_properties_settings.py:226-238` covers it.

## 7. decisions.md entry

```
## 2026-09-05 — blank_mode="balanced" divided by a number nobody had checked
- Symptom: `_signature_sheet_groups` validated `sheets_per_signature` only on the `"end"` path, by delegating to `split_signatures`. The `"balanced"` branch did its own ceil division: `sheets_per_signature=0` raised `ZeroDivisionError: division by zero`, and a negative produced an empty `sizes`, an empty `groups`, and a plan whose sheets belonged to no signature -- surfacing 130 lines later as `AssertionError: signature slot counts must sum to the padded page count`, a message about the wrong thing from the wrong layer, and not surfacing at all under `python -O`.
- Fix: One check in `_signature_sheet_groups`, before the `blank_mode` branch and after the `signature_lengths` branch, raising the identical `ValueError` message `split_signatures` already raises. Explicit lengths still win, so a project that states its groupings is not refused for a value it does not use.
- Surfaces: The value reaches the core from `--sheets-per-signature` (`type=int`, unbounded) and from a `.deckle`, where `core.schema.check_values` checks the type and nothing more. `project_io._check_layout_values` bounds only `paper` and says why: "`sheets_per_signature` and `signature_lengths` are already validated where they are used, with messages naming the numbers involved." That was true for one of the two modes. It is now true for both.
- Watch: A guard reached by delegation is a guard on one branch. `"end"` was safe because it called a validating helper; `"balanced"` reimplemented the arithmetic and inherited nothing. When one branch of an enum delegates and the other inlines, the validation asymmetry is invisible in review -- both branches look like two lines of correct arithmetic.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **Insert the guard *after* the `lengths` branch.** Putting it at the top
  of the function refuses a project that supplies `signature_lengths`
  with a stale `sheets_per_signature` — a combination
  `deckle/core/models.py:413-429` explicitly says is legal, and one the
  GUI can produce by typing lengths without clearing the spin box.
  `test_explicit_signature_lengths_win_over_a_bad_size` and the
  `inspect.getsource` acceptance row both exist to catch that.
- **`sheet_count <= 0` must stay the first thing in the function.** An
  empty document with a bad signature size returns `[]` rather than
  raising, because there is nothing to group and no imposition to refuse.
- **`SaddleStitchStrategy.impose` builds `slots` before it groups**
  (`deckle/core/layout.py:900-916`), so the padding warning is already in
  `warnings` when the raise happens. That list is discarded with the
  exception; there is no partial plan to leak.
- **The four post-conditions are `assert` statements** and vanish under
  `python -O`. Do not treat one of them going green as evidence the new
  check works — run the tests without `-O`, which is the default.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`). The
  new code adds no import at all.
- **`tests/test_imposition_properties.py:83` bounds its Hypothesis
  strategy at `min_value=1`.** Do not widen it to include 0 to "cover"
  this: every other property in that file asserts a plan's shape, and a
  refusal has no plan. The example tests in §4 are the right home.
