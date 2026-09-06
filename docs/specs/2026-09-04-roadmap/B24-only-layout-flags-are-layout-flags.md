# B24 — Report only the *layout* flags as ignored beside a `.deckle`

**Roadmap item:** `docs/ROADMAP.md` B24
**Depends on:** —
**Blocks:** B22
**Size:** S
**Decision needed first:** none

---

## 1. Context

When the source is a `.deckle`, the project's own layout wins and any layout
flag typed alongside it is reported as ignored. That is right, and
`_resolve_input`'s docstring explains why:

> silently overriding it from flag defaults would mean
> `deckle export project.deckle` produced a different book from the one the
> project describes.

`_layout_flags_given` decides which flags to name by walking **every**
option on the subparser and excluding three dests by hand (`output`,
`help`, `source`). So on `export` it also picks up `--sheets`, `--rule`,
`--pass`, `--back-offset` and `--profile` — none of which are layout, all
of which are honoured. The command prints a note saying they were ignored
and then does exactly what they asked.

Verified on the tree at `08e7f49`:

```bash
python -m deckle.cli dummy -o d.pdf --pages 4
python -m deckle.cli impose d.pdf -o p.deckle
python -m deckle.cli export p.deckle -o o.pdf --sheets 0 --pass back \
    --profile generic_face_down_reversed
```

Current output:

```
note: --pass, --profile, --sheets ignored -- p.deckle carries its own layout.
wrote o.pdf
After pass 1 finishes, reverse the printed stack (flip the whole stack over) before reloading, flip each sheet on its long edge, face down, and print pass 2 (backs).
```

The reload instruction on the third line is proof that `--pass` and
`--profile` were honoured; `o.pdf` contains only sheet 0's back, which is
proof that `--sheets` was too.

Why it matters beyond tidiness: a note that is wrong is worse than no note.
A user who reads "`--pass` ignored" and reruns without it gets a
both-faces PDF and prints it on a printer with no duplexer. And it damages
the note that *is* true — the layout half — by making the whole line
untrustworthy.

`--rule` and `--back-offset` are affected identically; they simply did not
appear in the example because they were not typed.

## 2. Current code

`deckle/cli.py:564-588`:

```python
def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Which layout options the user actually typed, as flag names.

    :param args: the parsed arguments.
    :param parser: the parser they came from, for its defaults.
    :returns: the flags whose value differs from the default.

    Used to warn rather than silently ignore. Comparing against defaults is
    approximate -- typing the default value looks like not typing it -- but
    it errs toward silence, which is the right direction for a warning.
    """
    sub = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(getattr(args, "_command", ""))
            break
    if sub is None:
        return []
    given = []
    for action in sub._actions:
        if not action.option_strings or action.dest in ("output", "help", "source"):
            continue
        if getattr(args, action.dest, action.default) != action.default:
            given.append(action.option_strings[0])
    return sorted(given)
```

Its only caller, `deckle/cli.py:635-647`:

```python
    if _is_project_file(args.source):
        project = _load_project_or_report(args.source)
        if project is None:
            return None
        ignored = _layout_flags_given(args, build_parser())
        if ignored:
            print(
                "note: "
                + ", ".join(ignored)
                + f" ignored -- {args.source} carries its own layout.",
                file=sys.stderr,
            )
        return list(project.pages), project.layout
```

`_add_layout_args`, which owns the actual layout flags,
`deckle/cli.py:746-862` — it returns `None` and registers, in order:
`--gutter`, `--paper`, `--landscape`, `--binding-edge`, `--fold-scheme`,
`--sheets-per-signature`, `--blank-mode`, `--grain`, `--paper-thickness`,
`--paper-weight`, `--paper-type`, `--paper-grade`, `--signatures` (dest
`signature_lengths`), `--sewing-stations`, `--crop`, `--auto-crop`,
`--auto-crop-margin`, `--crop-even`, `--trim` (dest `trim_pt`).

```python
def _add_layout_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gutter", type=_parse_length_pt, default=0.0,
        help="gutter width, e.g. 0.75in, 18pt, 5mm (default: 0)",
    )
    ...
```

The five callers of `_add_layout_args`, `deckle/cli.py:1247`, `:1305`,
`:1310`, `:1324`, and — note — **not** `crop-preview` or `dummy`, which
declare their own `--crop`/`--auto-crop`/`--auto-crop-margin`:

```python
    _add_layout_args(impose_parser)     # :1247
    _add_layout_args(export_parser)     # :1305
    _add_layout_args(info_parser)       # :1310
    _add_layout_args(schedule_parser)   # :1324
```

The non-layout options each subparser adds for itself:

| Subparser | Non-layout options | Lines |
|---|---|---|
| `impose` | `source`, `-o/--output`, `--printer` | `:1244-1246` |
| `export` | `source`, `-o/--output`, `--sheets`, `--rule`, `--pass`, `--back-offset`, `--profile` | `:1251-1304` |
| `info` | `source` | `:1309` |
| `schedule` | `source`, `-o/--output` | `:1317-1323` |
| `crop-preview` | everything (no `_add_layout_args`) | `:1331-1357` |
| `dummy` | everything (no `_add_layout_args`) | `:1364-1377` |

So `--printer` on `impose` is a fifth flag wrongly reported, alongside
`export`'s four. `impose project.deckle -o out.deckle --printer HP` prints
"`--printer` ignored" and then writes `Project(..., printer=args.printer)`
at `deckle/cli.py:1067`.

### Every call site

`_layout_flags_given`: `deckle/cli.py:564` (definition), `deckle/cli.py:639`
(only caller). No test calls it directly —
`grep -rn "_layout_flags_given" --include="*.py" .` returns those two lines
only.

`_add_layout_args`: `deckle/cli.py:746` (definition), `:1247`, `:1305`,
`:1310`, `:1324`. `grep -rn "_add_layout_args" --include="*.py" .` returns
those five lines only.

`build_parser`: `deckle/cli.py:1222` (definition), called at `:639` (inside
`_resolve_input`, rebuilt on every project load purely to reach the
defaults — see **M4**) and `:1433` (in `main`), plus
`tests/test_cli.py` and others.

### Existing tests over this code

- `tests/test_project_cli.py::test_layout_flags_are_reported_as_ignored_not_silently_applied`
  (line 114) — runs `info project.deckle --gutter 2in` and asserts
  `"--gutter" in result.stderr` and `"ignored" in result.stderr`. This is
  the only test of the note, and it uses `info`, which has no non-layout
  flags, so it cannot see the bug.
- `tests/test_project_cli.py::test_the_saved_layout_is_what_gets_imposed`
- `tests/test_cli_sheets.py` — `--sheets` behaviour, on a PDF source
- `tests/test_cli_passes.py` — `--pass`/`--profile`, on a PDF source

**No test combines a `.deckle` source with a non-layout flag.** That is the
gap.

## 3. Change

### The rule

**`_add_layout_args` returns the set of `dest` strings it registered, and
`_layout_flags_given` compares only those.** The list of layout flags is
maintained in exactly one place — the function that creates them — so a
flag added there is covered without anyone remembering to update a second
list, and a flag added to a subparser directly is correctly left out.

Rejected: extending the hand-maintained exclusion tuple to
`("output", "help", "source", "printer", "sheets", "rule", "pass_side",
"back_offset", "profile")`. That is the same structure the bug is made of —
a list in one place, added to in another — and it is the failure **M1**
names three times over in `LayoutPanel`. It would also be wrong the moment
`export` grows a sixth flag.

Rejected: tagging actions with a custom attribute
(`action.is_layout = True`). It works, but it reaches into an argparse
`Action` object to store state argparse does not know about, and the set of
dests is a plain value the caller can hold.

### Signature

```python
def _add_layout_args(parser: argparse.ArgumentParser) -> set[str]:
```

and

```python
def _layout_flags_given(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    layout_dests: set[str],
) -> list[str]:
```

`build_parser` must therefore keep the set somewhere `_resolve_input` can
reach it. The cleanest place, and the one that needs no new plumbing, is a
module-level constant computed once:

```python
LAYOUT_DESTS: set[str] = set()
```

populated by `_add_layout_args` on its first call. That is a mutable global
initialised as a side effect, which is exactly the kind of thing this
codebase avoids — so instead, **compute it eagerly at import** from a
single declarative source. See step 1.

### Steps

1. **`deckle/cli.py`** — make `_add_layout_args` return what it registered,
   by collecting the return value of each `add_argument` (which is the
   `Action`, carrying `.dest`):

   ```python
   def _add_layout_args(parser: argparse.ArgumentParser) -> set[str]:
       """Add the layout options to ``parser`` and name what was added.

       The returned ``dest`` set is the point of the return value.
       ``_layout_flags_given`` has to know which options are *layout*
       options in order to say they were overridden by a project's own
       layout, and it used to work that out by walking every option on the
       subparser and excluding three dests by hand. So ``export``'s
       ``--sheets``, ``--rule``, ``--pass``, ``--back-offset`` and
       ``--profile``, and ``impose``'s ``--printer``, were all reported as
       ignored and then honoured -- a note that was wrong about five flags
       and thereby untrustworthy about the fifteen it was right about.

       One list, in the function that creates them, so a flag added here
       is covered and a flag added to a subparser is not.

       :param parser: the subparser to add the options to.
       :returns: the ``dest`` of every option this function registered.
       """
       actions = [
           parser.add_argument(
               "--gutter", type=_parse_length_pt, default=0.0,
               help="gutter width, e.g. 0.75in, 18pt, 5mm (default: 0)",
           ),
           parser.add_argument(
               "--paper", type=_parse_paper, default=LETTER_PT,
               help="paper size: a preset (letter, a4, legal) or WxH[unit] (default: letter)",
           ),
           ...
       ]
       return {action.dest for action in actions}
   ```

   Every existing `parser.add_argument(...)` call becomes an element of that
   list, unchanged in its arguments. Nothing else about them moves.

   Rejected alternative shape: keep the calls as statements and append each
   `.dest` by hand. Same two-lists problem at one remove.

2. **`deckle/cli.py`** — change `_layout_flags_given` to take the set:

   ```python
   def _layout_flags_given(
       args: argparse.Namespace,
       parser: argparse.ArgumentParser,
       layout_dests: set[str],
   ) -> list[str]:
       """Which *layout* options the user actually typed, as flag names.

       :param args: the parsed arguments.
       :param parser: the parser they came from, for its defaults.
       :param layout_dests: what :func:`_add_layout_args` registered. Only
           these are considered: the rest of a subparser's options are not
           layout and are not overridden by a project's stored layout, so
           naming them as ignored was both false and, since they were then
           honoured, actively misleading.
       :returns: the flags whose value differs from the default.

       Comparing against defaults is approximate -- typing the default
       value looks like not typing it -- but it errs toward silence, which
       is the right direction for a warning.
       """
       sub = None
       for action in parser._actions:
           if isinstance(action, argparse._SubParsersAction):
               sub = action.choices.get(getattr(args, "_command", ""))
               break
       if sub is None:
           return []
       given = []
       for action in sub._actions:
           if not action.option_strings or action.dest not in layout_dests:
               continue
           if getattr(args, action.dest, action.default) != action.default:
               given.append(action.option_strings[0])
       return sorted(given)
   ```

   The `action.dest in ("output", "help", "source")` exclusion disappears
   entirely: `help` and `source` were never in `layout_dests`, and neither
   is `output`.

3. **`deckle/cli.py`, `build_parser`** — capture and stash the sets. The
   four `_add_layout_args(...)` calls at `:1247`, `:1305`, `:1310` and
   `:1324` each return the same set (the flags are identical on all four),
   so one module-level record keyed by command is more machinery than the
   problem needs. Instead set it as a parser default alongside `_command`,
   which is how `_layout_flags_given` already finds its subparser:

   ```python
       impose_parser.set_defaults(
           func=_cmd_impose, _command="impose",
           _layout_dests=_add_layout_args(impose_parser),
       )
   ```

   **No** — `_add_layout_args` must run before `set_defaults` so the
   options exist, and putting a call inside a `set_defaults` argument list
   makes the ordering invisible. Write it plainly:

   ```python
       layout_dests = _add_layout_args(impose_parser)
       impose_parser.set_defaults(
           func=_cmd_impose, _command="impose", _layout_dests=layout_dests,
       )
   ```

   and the same for `export_parser` (`:1305-1306`), `info_parser`
   (`:1310-1311`) and `schedule_parser` (`:1324-1325`).

   `crop-preview` and `dummy` do not call `_add_layout_args` and do not get
   `_layout_dests`. Neither accepts a `.deckle` source in a way that reaches
   `_resolve_input` (`crop-preview` calls `_load_source_or_report`
   directly at `:1133`; `dummy` takes no source), so they never reach
   `_layout_flags_given`.

4. **`deckle/cli.py:639`** — pass the set through:

   ```python
           ignored = _layout_flags_given(
               args, build_parser(), getattr(args, "_layout_dests", set())
           )
   ```

   `getattr` with a default rather than `args._layout_dests`, because
   `_resolve_input` is reachable from any command that has a `source`, and
   an empty set means "name nothing", which is the safe direction for a
   warning.

5. **`_resolve_input`'s docstring** (`deckle/cli.py:622-634`) — the closing
   sentence says "Flags typed alongside a project are reported as ignored".
   Narrow it: "**Layout** flags typed alongside a project are reported as
   ignored. The rest — `--sheets`, `--rule`, `--pass`, `--back-offset`,
   `--profile`, `--printer` — are not layout and are honoured, which is why
   naming them was worse than saying nothing."

6. **The note's own wording** at `deckle/cli.py:641-646` — unchanged.
   `tests/test_project_cli.py:114-121` asserts `"ignored"` and `"--gutter"`
   appear, and both still do.

## 4. Tests

All in `tests/test_project_cli.py`, which has the `_cli` subprocess helper
and the `project` fixture (a `.deckle` with `--gutter 0.75in --fold-scheme
folio --landscape --sewing-stations 5`).

### `test_non_layout_flags_are_not_reported_as_ignored`

- **File / function:** `tests/test_project_cli.py::test_non_layout_flags_are_not_reported_as_ignored`
- **Setup:** the `project` fixture; run
  `_cli("export", str(project), "-o", str(out), "--sheets", "0", "--pass",
  "back", "--profile", "generic_face_down_reversed")`.
- **Assertion in words:** exit 0, and stderr contains none of `--sheets`,
  `--pass`, `--profile`. Docstring: they were honoured, so naming them as
  ignored told the user the opposite of what happened.
- **Expected failure on the unfixed tree:**
  `note: --pass, --profile, --sheets ignored -- <path> carries its own layout.`
  is on stderr, so `assert "--pass" not in result.stderr` fails.

### `test_the_ones_that_are_reported_were_really_ignored`

- **Setup:** the same run as above, plus `--gutter 2in`.
- **Assertion in words:** stderr names `--gutter` and nothing else; and the
  exported PDF's page geometry still reflects the project's 0.75in gutter,
  not 2in. The second half is what makes the first half mean something —
  it asserts the note is true rather than merely shorter. Read the first
  page's `MediaBox` with `pikepdf` as
  `test_the_saved_layout_is_what_gets_imposed` (line 86) already does.
- **Unfixed tree:** stderr also names `--pass`, `--profile`, `--sheets`, so
  the "nothing else" assertion fails.

### `test_the_printer_flag_is_not_reported_as_ignored_by_impose`

- **Setup:** `_cli("impose", str(project), "-o", str(out2), "--printer", "HP")`.
- **Assertion in words:** exit 0; stderr does not contain `--printer`; and
  the written `.deckle`'s `printer` field is `"HP"` — proof it was honoured.
- **Unfixed tree:** stderr reads
  `note: --printer ignored -- <path> carries its own layout.` while the
  file records `"printer": "HP"`.

### `test_a_pass_still_works_from_a_project`

- **Setup:** `_cli("export", str(project), "-o", str(out), "--pass", "back",
  "--profile", "generic_face_down_reversed")`.
- **Assertion in words:** exit 0 and stdout contains the reload
  instruction. Pins that removing the (wrong) note did not remove the
  (correct) behaviour.
- **Unfixed tree:** passes.

### `test_every_layout_flag_is_still_reported`

- **Setup:** parametrise over one representative flag from each group
  `_add_layout_args` registers: `("--gutter", "2in")`,
  `("--paper", "a4")`, `("--binding-edge", "right")`,
  `("--fold-scheme", "none")`, `("--blank-mode", "balanced")`,
  `("--grain", "long")`, `("--paper-thickness", "0.1mm")`,
  `("--signatures", "1")`, `("--sewing-stations", "7")`,
  `("--crop", "1,1,1,1")`, `("--crop-even", "1,1,1,1")`,
  `("--trim", "0.25in")`, `("--auto-crop-margin", "3pt")`, and the two
  store-true flags `("--landscape",)` and `("--auto-crop",)`.
- **Assertion in words:** for each, running `info project.deckle <flag>
  [value]` puts that flag's name in stderr. This is the test that catches a
  `dest` accidentally dropped from the returned set, and it is why the set
  comes from `_add_layout_args` rather than being retyped.
- **Unfixed tree:** passes for all of them. Keep it — it is the regression
  guard on the mechanism this spec introduces.

  Note `--fold-scheme none` and `--grain long`: the default for
  `--fold-scheme` **is** `"none"` and for `--grain` **is** `"unknown"`, so
  `--fold-scheme none` compares equal to its default and is *not*
  reported. That is the documented approximation
  ("typing the default value looks like not typing it"). Use
  `--fold-scheme folio` and `--grain long` instead — `folio` and `long`
  both differ from their defaults. Check each parametrised value against
  the `default=` in `_add_layout_args` before writing the case.

### `test_a_pdf_source_reports_nothing`

- **Setup:** `_cli("export", FIXTURE, "-o", str(out), "--gutter", "2in",
  "--sheets", "0")`.
- **Assertion in words:** stderr contains no `ignored` note at all — the
  whole mechanism only fires for a `.deckle`.
- **Unfixed tree:** passes.

## 5. Acceptance

| Check | Command |
|---|---|
| The hand-maintained exclusion tuple is gone | `! grep -n '"output", "help", "source"' deckle/cli.py` |
| `_add_layout_args` returns a set | `.venv/bin/python -c "import argparse, deckle.cli as m; d = m._add_layout_args(argparse.ArgumentParser()); assert isinstance(d, set) and 'gutter' in d and 'output' not in d, d"` |
| Every layout dest is in the set and nothing else | `.venv/bin/python -c "import argparse, deckle.cli as m; d = m._add_layout_args(argparse.ArgumentParser()); assert d == {'gutter','paper','landscape','binding_edge','fold_scheme','sheets_per_signature','blank_mode','grain','paper_thickness','paper_weight','paper_type','paper_grade','signature_lengths','sewing_stations','crop','auto_crop','auto_crop_margin','crop_even','trim_pt'}, sorted(d)"` |
| The export subparser's own flags are not in it | `.venv/bin/python -c "import deckle.cli as m; p = m.build_parser(); a = p.parse_args(['export','x.deckle','-o','y.pdf']); assert not {'sheets','rule','pass_side','back_offset','profile','printer','output','source'} & a._layout_dests"` |
| Non-layout flags are no longer named | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py -k "not_reported_as_ignored or really_ignored or printer_flag or pass_still_works or every_layout_flag or pdf_source_reports_nothing"` |
| The existing note test still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py` |
| Parser construction is unchanged for every command | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_errors.py tests/test_cli_sheets.py tests/test_cli_passes.py tests/test_cli_paper.py tests/test_output_command_parity.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: row 1's grep returns **1** hit
(`deckle/cli.py:584`) and must return none. Rows 2, 3 and 4 all fail today
— `_add_layout_args` returns `None`, so row 2 raises
`TypeError: argument of type 'NoneType' is not iterable` and row 4 raises
`AttributeError: 'Namespace' object has no attribute '_layout_dests'`.

The dest list in row 3 was read off `_add_layout_args`'s 19
`add_argument` calls at `deckle/cli.py:747-862`; re-derive it rather than
trusting this spec if the function has changed.

## 6. Out of scope

- **M4** — splitting `cli.py`, and the fact that `build_parser()` is
  rebuilt on every project load (`deckle/cli.py:639`) purely to introspect
  defaults. This spec keeps that call exactly as it is; a `_layout_dests`
  parser default is one more thing that later makes the rebuild
  unnecessary, but removing it is M4's.
- **B22** — `--profile` without `--pass`. It is the flag most obviously
  mislabelled by this bug, and after B22 it is honoured in *more* cases
  still. Do B24 first; see §8.
- **B23** — the warning filter in `_load_project_or_report`, which writes
  to the same stderr stream immediately before this note. Independent.
- **B21**, **B25** — same file, unrelated functions.
- **F5** — CLI parity for `--margin-top/bottom/outer`, `--slack-to`,
  `--start-on-verso`, `--landscape-policy`. When those land in
  `_add_layout_args` they are covered automatically, which is the point of
  the returned set.
- **The approximation itself.** "Typing the default value looks like not
  typing it" stays; fixing it properly means sentinel defaults across
  nineteen options and is not worth it for a warning.

## 7. decisions.md entry

```
## 2026-09-05 — The note said five flags were ignored and then honoured them
- Symptom: `_layout_flags_given` walked every option on a subparser and excluded three dests by hand, so `export project.deckle --sheets 0 --pass back --profile X` printed "note: --pass, --profile, --sheets ignored -- p.deckle carries its own layout" and then printed the reload instruction for the back pass it had just said it ignored. `impose --printer HP` did the same and wrote `"printer": "HP"`. A user who believed the note and reran without `--pass` would get a both-faces PDF for a printer with no duplexer.
- Fix: `_add_layout_args` returns the set of dests it registered; `build_parser` stashes it as a `_layout_dests` parser default; `_layout_flags_given` compares only those. The hand-maintained `("output", "help", "source")` exclusion is gone.
- Surfaces: The one test of the note ran `info`, which has no non-layout flags at all, so the bug was structurally invisible to it. Testing the well-behaved command is how a list-based defect survives.
- Watch: A warning that is wrong is worse than no warning -- it does not merely fail to help, it argues against the correct half of the same sentence. And "walk everything and exclude the ones I know about" is the same list-in-two-places shape that M1 records three times in `LayoutPanel`; the fix is always to ask the thing that created the list.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B21, B22, B23, B24 and B25 all edit `deckle/cli.py`.
  Recommended order: **B25 → B23 → B21 → B24 → B22.** B24 must land before
  B22: B22 makes `--profile` meaningful without `--pass`, and if the note
  still calls it ignored the two changes contradict each other in the same
  command's output.
- **`_add_layout_args` is called four times** (`:1247`, `:1305`, `:1310`,
  `:1324`) and returns the same set each time. Do not memoise it into a
  module global — it takes a parser and its whole job is registering on
  that parser.
- **`crop-preview` and `dummy` never call `_add_layout_args`** and declare
  their own `--crop`, `--auto-crop`, `--auto-crop-margin`. They therefore
  have no `_layout_dests`, which is why step 4 uses `getattr(..., set())`.
  Neither reaches `_resolve_input`, but the default keeps that from being a
  latent `AttributeError`.
- **`argparse.Action.dest` is not the flag string.** `--signatures` has dest
  `signature_lengths`, `--trim` has `trim_pt`, `--pass` has `pass_side`.
  The set holds dests; the note prints `action.option_strings[0]`. Do not
  mix them.
- **`parser.add_argument` returns the `Action`.** That is what makes step 1
  work without a second list; it is documented argparse behaviour, not an
  implementation detail.
- **A flag whose typed value equals its default is not reported.** That is
  deliberate and documented in the docstring; a parametrised test that
  passes `--fold-scheme none` or `--grain unknown` will fail for that
  reason and not because of anything this spec changed.
- **`build_parser()` is called inside `_resolve_input`** to get a *second*
  parser object, distinct from the one that parsed `args`. The
  `_layout_dests` value comes from `args` (set by the first parser), so the
  two do not need to agree — but if a later change reads the set off the
  rebuilt parser instead, it will silently work and then break the moment
  the two parsers differ.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
