# F5 — Give the CLI the six layout settings only the app can set

**Roadmap item:** `docs/ROADMAP.md` F5
**Depends on:** —
**Blocks:** — (M4's `cli.py` split will move these; do F5 first, it is six
`add_argument` calls and M4 is a refactor)
**Size:** S
**Decision needed first:** none. **B19** (`landscape_policy`'s `scale` and
`letterbox` being identical) is an open owner decision and this spec
deliberately does not pre-empt it — see §3, step 6.

---

## 1. Context

`LayoutSettings` has twenty fields
(`len(dataclasses.fields(LayoutSettings)) == 20`, verified). The CLI's
`_build_layout_settings` sets **thirteen**. Of the seven it does not, one —
`margins_linked` — is presentational by its own docstring. The other **six**
are the head margin, the tail margin, the fore-edge margin, where horizontal
slack goes, whether the first page is a recto, and what to do with a
landscape page. Those six are this spec.

The GUIDE says so twice, and calls it a gap in its own voice.
`docs/GUIDE.md:156-160`:

> > **Note.** `slack_to` and the three non-gutter margins are configurable in
> > the desktop app and stored in the `.deckle` project file, but the CLI
> > currently exposes only `--gutter`. If you need specific head, tail or
> > fore-edge margins from the command line, set them in the app and save the
> > project. **This is a genuine gap, not an omission from this guide.**

and `docs/GUIDE.md:770-772`:

> **Not available from the CLI:** head, tail and fore-edge margins;
> `slack_to`; `start_on_recto`; landscape policy. Those are app-and-project-file
> settings today.

**The concrete failure.** `deckle impose` builds its `LayoutSettings` from
flags only, so it always writes a project with **zero head, tail and
fore-edge margins**. `LayoutSettings.margin_outer_pt`'s own docstring says
what that produces:

> All default to 0.0, which reproduces edge-to-edge behaviour -- rarely what
> you want on a real printer. Content scaled to full page height has no head
> or tail margin at all, so it necessarily falls inside the printer's
> non-printable border and triggers ``clipped_by_imageable_area``.

So the documented workflow "impose from the CLI, then open the project in the
app" hands the app a project that will clip on every sheet, and there is no
flag to prevent it. Verified on the current tree:

```bash
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf \
      -o /tmp/x.pdf --margin-top 0.5in
usage: deckle [-h] [--version]
              {impose,export,info,schedule,crop-preview,dummy} ...
deckle: error: unrecognized arguments: --margin-top 0.5in
```

Same for `--margin-bottom`, `--margin-outer`, `--slack-to`,
`--start-on-verso` and `--landscape-policy`.

## 2. Current code

`deckle/cli.py:709-732` — every field the CLI can currently set. Six of
`LayoutSettings`'s fields are simply absent from the constructor call, so
each takes its dataclass default:

```python
def _build_layout_settings(args: argparse.Namespace) -> LayoutSettings:
    paper = args.paper
    if getattr(args, "landscape", False):
        # Turn whatever was asked for, rather than assuming the preset came
        # out portrait: `--paper 792x612pt --landscape` must stay landscape
        # instead of being flipped back.
        short, long = sorted(paper)
        paper = (long, short)
    thickness_pt = _paper_thickness_from_args(args)
    return LayoutSettings(
        paper=paper,
        gutter_pt=args.gutter,
        binding_edge=args.binding_edge,
        fold_scheme=args.fold_scheme,
        sheets_per_signature=args.sheets_per_signature,
        blank_mode=args.blank_mode,
        sewing_stations=args.sewing_stations,
        paper_thickness_pt=thickness_pt,
        grain=args.grain,
        trim_pt=args.trim_pt,
        crop_odd_pt=args.crop,
        crop_even_pt=args.crop_even,
        signature_lengths=args.signature_lengths,
    )
```

`deckle/cli.py:746-763` — the head of `_add_layout_args`, and the house
pattern the six new flags follow (`type=_parse_length_pt`, a default stated
in the help, prose that says *why*):

```python
def _add_layout_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gutter", type=_parse_length_pt, default=0.0,
        help="gutter width, e.g. 0.75in, 18pt, 5mm (default: 0)",
    )
    parser.add_argument(
        "--paper", type=_parse_paper, default=LETTER_PT,
        help="paper size: a preset (letter, a4, legal) or WxH[unit] (default: letter)",
    )
    parser.add_argument(
        "--landscape", action="store_true",
        help="turn the sheet on its side. Signatures want this: two portrait "
        "book pages sit side by side on one landscape sheet",
    )
    parser.add_argument(
        "--binding-edge", choices=["left", "right"], default="left",
        help="which edge the gutter shifts toward (default: left)",
    )
```

`deckle/cli.py:92-108` — the parser the three margins reuse. Note it is
**unsigned by design**; that is correct here, since a margin is a magnitude:

```python
def _parse_length_pt(value: str) -> float:
    """Parse a length to points.

    Accepts ``in``, ``pt``, ``mm``, or ``cm`` as the unit, with an optional
    space before it (``0.75in``, ``18pt``, ``5mm``, ``5 cm``). A bare
    number with no unit (``"18"``) is interpreted as points.
    """
    match = _LENGTH_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid length {value!r}: expected a number optionally followed "
            f"by a unit ({', '.join(_ACCEPTED_LENGTH_UNITS)}); a bare number "
            "is interpreted as points"
        )
    number, unit = match.groups()
    factor = _UNIT_TO_PT[unit.lower()] if unit else 1.0
    return float(number) * factor
```

`deckle/cli.py:564-588` — `_layout_flags_given`, which decides what a
`.deckle` source reports as ignored. It works off the subparser's actions and
a three-name skip list, so **anything added to `_add_layout_args` is
automatically reported**:

```python
def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Which layout options the user actually typed, as flag names.
    ...
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

`deckle/core/models.py:332-352` — the six fields, and the two whose defaults
are not zero:

```python
    start_on_recto: bool = True
    landscape_policy: Literal["rotate", "scale", "letterbox"] = "rotate"
    margin_top_pt: float = 0.0
    margin_bottom_pt: float = 0.0
    margin_outer_pt: float = 0.0
    ...
    slack_to: Literal["gutter", "outer", "split"] = "gutter"
```

`deckle/core/layout.py:292-294` and `383-385` — the whole of
`landscape_policy`'s behaviour, which is why its help text must not promise
three behaviours (B19):

```python
def _rotates_to_portrait(src_w: float, src_h: float, settings: LayoutSettings) -> bool:
    paper_w, paper_h = settings.paper
    return settings.landscape_policy == "rotate" and paper_h >= paper_w and src_w > src_h
```

```python
    cell_is_portrait = cell_h >= cell_w
    page_is_landscape = src_w > src_h
    if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
```

Nothing anywhere branches on `"scale"` versus `"letterbox"`.

`deckle/core/layout.py:95-116` — what `start_on_recto=False` actually does,
so the flag's help can be accurate:

```python
def _lead_for_recto(
    active: list[SourcePage | None], settings: LayoutSettings
) -> list[SourcePage | None]:
    """Prepend a filler when the first content page must fall on a verso.
    ...
    An empty document gets no filler: a leading blank is a position for
    content, and there is no content to position.
    """
    if settings.start_on_recto or not active:
        return active
    return [None, *active]
```

`deckle/app/views/layout_panel.py:115-119` — the panel's `slack_to` labels,
which the CLI's `choices` must agree with as *values*:

```python
SLACK_TARGETS: tuple[tuple[str, str], ...] = (
    ("gutter", "Gutter"),
    ("outer", "Fore-edge"),
    ("split", "Split evenly"),
)
```

### Call sites and existing tests

```
$ grep -rn "_build_layout_settings\|_add_layout_args\|_layout_flags_given" deckle/
deckle/cli.py:564:def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
deckle/cli.py:639:        ignored = _layout_flags_given(args, build_parser())
deckle/cli.py:653:        settings = _build_layout_settings(args)
deckle/cli.py:709:def _build_layout_settings(args: argparse.Namespace) -> LayoutSettings:
deckle/cli.py:746:def _add_layout_args(parser: argparse.ArgumentParser) -> None:
deckle/cli.py:1247:    _add_layout_args(impose_parser)
deckle/cli.py:1305:    _add_layout_args(export_parser)
deckle/cli.py:1310:    _add_layout_args(info_parser)
deckle/cli.py:1324:    _add_layout_args(schedule_parser)
```

`_add_layout_args` is called by **four** subparsers, so all six flags appear
on `impose`, `export`, `info` and `schedule` at once — which is the guarantee
GUIDE §8 makes: *"Every command above accepts all of these — so a plan you
inspect with `info` is the plan `export` writes."* `crop-preview` and `dummy`
deliberately do not get them.

Existing tests: `tests/test_cli.py` (flag parsing and dispatch),
`tests/test_cli_paper.py` (`--paper`/`--landscape`),
`tests/test_project_cli.py` (a `.deckle` as SOURCE, and the "ignored" note),
`tests/test_settings_roundtrip.py` (every `LayoutSettings` field through
`.deckle`), `tests/test_output_command_parity.py` (info/export/schedule
agreeing about one plan). The roadmap's §5 notes that **no CLI test exercises
`--crop`, `--trim`, `--binding-edge`, `--blank-mode`, `--grain`,
`--signatures` or `--back-offset`** — F5 does not fix that, but its own six
tests should be written so they can be copied for those.

## 3. Change

Six `add_argument` calls in `_add_layout_args`, six lines in
`_build_layout_settings`, no new parsers, no change to
`_layout_flags_given`.

Insert the three margins **immediately after `--gutter`** (they are the other
three sides of the same box; GUIDE §3's whole point is that they are one
concept), and the other three after `--binding-edge`.

### 1. `--margin-top`

```python
    parser.add_argument(
        "--margin-top", dest="margin_top_pt", type=_parse_length_pt, default=0.0,
        metavar="LENGTH",
        help="head margin -- the space above the text, e.g. 0.5in (default: 0)",
    )
```

### 2. `--margin-bottom`

```python
    parser.add_argument(
        "--margin-bottom", dest="margin_bottom_pt", type=_parse_length_pt, default=0.0,
        metavar="LENGTH",
        help="tail margin -- the space below the text, e.g. 0.5in (default: 0)",
    )
```

### 3. `--margin-outer`

```python
    parser.add_argument(
        "--margin-outer", dest="margin_outer_pt", type=_parse_length_pt, default=0.0,
        metavar="LENGTH",
        help=(
            "fore-edge margin -- the space on the edge you see when the book "
            "is closed. The fourth edge is the spine, and its margin is "
            "--gutter (default: 0, which prints to the paper edge and will "
            "be clipped by the printer)"
        ),
    )
```

The parenthetical is not decoration. Zero is the dataclass default and it is
*wrong on every real printer*; the field's own docstring says so, and the
only place a CLI user will ever read that is the `--help` output.

### 4. `--slack-to`

```python
    parser.add_argument(
        "--slack-to", dest="slack_to",
        choices=["gutter", "outer", "split"], default="gutter",
        help=(
            "where spare horizontal width goes when a page is narrower than "
            "its box, and therefore which margin stays identical through the "
            "book. gutter: the fore-edge is exact and the gutter varies. "
            "outer: the gutter is exact and the fore-edge varies -- use it "
            "with a fixed punch or a sewing template. split: half each, "
            "content visually centred (default: gutter)"
        ),
    )
```

Values, not labels: `gutter`/`outer`/`split` match
`LayoutSettings.slack_to`'s `Literal` and the panel's `SLACK_TARGETS` keys.
The panel's user-facing label for `outer` is "Fore-edge"; the CLI keeps the
field value so a `--slack-to` in a script and a `slack_to` in a `.deckle`
read the same.

### 5. `--start-on-verso`

```python
    parser.add_argument(
        "--start-on-verso", dest="start_on_verso", action="store_true",
        help=(
            "begin the book on a left-hand page instead of a right-hand one. "
            "Costs one leading blank leaf, which is what pushes the first "
            "page onto a verso (default: off -- page 1 is a recto)"
        ),
    )
```

**Named for the negative on purpose.** The model field is
`start_on_recto: bool = True`, and a `store_true` flag cannot turn a
default-`True` setting off. `--start-on-recto` with
`action="store_false"`/`default=True` would parse, but then
`_layout_flags_given` — which reports any value differing from the action
default — would never report it, so typing it against a `.deckle` would be
silently ignored with no note. **Rejected** for that reason. The mapping is
one negation in `_build_layout_settings`.

### 6. `--landscape-policy`

```python
    parser.add_argument(
        "--landscape-policy", dest="landscape_policy",
        choices=["rotate", "scale", "letterbox"], default="rotate",
        help=(
            "what to do with a landscape page in a portrait book. rotate: "
            "turn it 90 degrees so it fills the page, and warn (the default "
            "-- the reader turns the book). scale and letterbox both leave "
            "it upright and fit it to the width; they do not currently "
            "differ (default: rotate)"
        ),
    )
```

**The last clause is the whole of B19, said out loud rather than promised
away.** `deckle/core/layout.py` branches on `== "rotate"` in exactly two
places and nowhere on `"scale"` versus `"letterbox"`. The GUI's tooltip at
`layout_panel.py:1060-1066` currently promises two different behaviours and
is wrong; this spec does **not** change the tooltip (that is B19's decision:
implement letterbox, or collapse the enum) but it must not add a second copy
of the same false promise. A test pins the help text against it.

### 7. `_build_layout_settings` — six lines

Add, in the constructor call, after `binding_edge=args.binding_edge`:

```python
        margin_top_pt=args.margin_top_pt,
        margin_bottom_pt=args.margin_bottom_pt,
        margin_outer_pt=args.margin_outer_pt,
        slack_to=args.slack_to,
        start_on_recto=not args.start_on_verso,
        landscape_policy=args.landscape_policy,
```

`not args.start_on_verso` is the only transformation. Put a one-line comment
above it: `# The flag is the negative because store_true cannot clear a`
`# default-True field, and a store_false flag would never be reported as`
`# "ignored" against a .deckle source.`

Use `args.<dest>` directly, not `getattr(args, ..., default)`: all four
subparsers that reach `_build_layout_settings` call `_add_layout_args`, so
the attribute is always present. (`_paper_thickness_from_args` uses `getattr`
because it is also reachable from paths that do not.)

### 8. `_layout_flags_given` — no change, and a test that says so

All six live in `_add_layout_args`, all six have `option_strings`, and none
of their `dest`s is `output`, `help` or `source`. They are therefore reported
automatically when a `.deckle` is the source, which is correct: they *are*
layout settings and the project's own layout wins. **Do not add them to the
skip tuple.** B24 is the separate bug that `--sheets`, `--pass` and
`--profile` are wrongly reported by the same mechanism; fixing that is B24's,
and it must not sweep these six out with it.

### 9. `docs/GUIDE.md`

- §3, delete the whole `> **Note.**` block at lines 156-160. It exists only
  to apologise for this gap.
- §8's **Layout options** table, six new rows appended after
  `--sewing-stations N`:

| Option | Default | Notes |
|---|---|---|
| `--margin-top LENGTH` | `0` | Head margin. |
| `--margin-bottom LENGTH` | `0` | Tail margin. |
| `--margin-outer LENGTH` | `0` | Fore-edge margin. The fourth edge is the spine, and its margin is `--gutter`. Zero prints to the paper edge, which a printer will clip. |
| `--slack-to {gutter,outer,split}` | `gutter` | Which margin absorbs spare width, and therefore which one stays identical through the book. See [§3](#which-margin-absorbs-the-slack). |
| `--start-on-verso` | off | Begin on a left-hand page. Costs one leading blank leaf. |
| `--landscape-policy {rotate,scale,letterbox}` | `rotate` | What to do with a landscape page in a portrait book. `scale` and `letterbox` do not currently differ. |

- §8, replace the paragraph at lines 770-772 with:
  **"Not available from the CLI:** `margins_linked`, which is a
  presentation-only setting the desktop app uses to decide whether it edits
  the three margins as one value."

  That is now the whole of the gap, and it is deliberate:
  `LayoutSettings.margins_linked`'s docstring says *"Purely presentational --
  the imposer always reads the three fields independently."*

- D5 also lists `crop-preview`, `dummy`, `--paper-weight/-type/-grade`,
  `--signatures`, `--crop*`, `--trim`, `--sheets`, `--rule`, `--pass`,
  `--back-offset`, `--profile` as missing from §8. **Out of scope here** —
  fix D5 in the docs pass; F5 only adds its own six rows and removes its own
  two apologies.

## 4. Tests

New file `tests/test_cli_margins.py`. Every test drives `main()` and asserts
on the `LayoutSettings` that reached the imposer, by monkeypatching the
strategy — which is more direct than reading a written PDF and is the pattern
that generalises to the untested flags listed in roadmap §5.

Shared helper:

```python
def _settings_for(monkeypatch, argv) -> LayoutSettings:
    """Run `main(argv)` and return the LayoutSettings the imposer was given."""
    seen = {}
    real = GutterShiftStrategy.impose
    def spy(self, pages, settings):
        seen["settings"] = settings
        return real(self, pages, settings)
    monkeypatch.setattr(GutterShiftStrategy, "impose", spy)
    assert main(argv) == 0
    return seen["settings"]
```

1. `test_margin_top_reaches_the_layout_in_points`
   `--margin-top 0.5in` → `settings.margin_top_pt == 36.0`.
   Unfixed: `SystemExit: 2`, stderr
   `deckle: error: unrecognized arguments: --margin-top 0.5in`.

2. `test_margin_bottom_reaches_the_layout_in_points` — `--margin-bottom 18pt`
   → `18.0`. Same failure.

3. `test_margin_outer_reaches_the_layout_in_points` — `--margin-outer 1cm`
   → `28.346...` (`pytest.approx`). Same failure.

4. `test_the_three_margins_default_to_zero`
   No flags → all three are `0.0`. Passes today; it is the regression guard
   that adding the flags did not change the default.

5. `test_slack_to_reaches_the_layout`
   Parametrised over `gutter`, `outer`, `split`; `settings.slack_to` equals
   the value. Unfixed: `unrecognized arguments: --slack-to`.

6. `test_slack_to_rejects_an_unknown_target`
   `--slack-to fore-edge` exits 2 and stderr names the three choices. The
   panel's *label* for `outer` is "Fore-edge", so this is the mistake a user
   who has used the app will actually make.

7. `test_start_on_verso_clears_start_on_recto`
   `--start-on-verso` → `settings.start_on_recto is False`; without it,
   `True`. Unfixed: `unrecognized arguments: --start-on-verso`.

8. `test_start_on_verso_adds_exactly_one_leading_blank`
   Impose a 2-page source with and without the flag; the flagged plan's first
   output page `is_filler` and the plan has one more slot. Asserts the flag
   *does something*, which is the failure mode `_lead_for_recto`'s docstring
   was written about: *"the setting was accepted, persisted into the
   `.deckle` file, and then ignored -- a control that silently does nothing
   is worse than one that is missing."*

9. `test_landscape_policy_reaches_the_layout`
   Parametrised over the three values. Unfixed:
   `unrecognized arguments: --landscape-policy`.

10. `test_the_landscape_policy_help_does_not_promise_a_difference_between_scale_and_letterbox`
    Build the parser, find the `--landscape-policy` action, and assert its
    help contains `"do not currently differ"`. Ugly as a test and correct as
    a contract: the GUI tooltip already makes the false promise (B19) and a
    second copy in the CLI would make it twice as expensive to retract.

11. `test_every_layout_settings_field_is_reachable_from_the_cli_or_named_as_presentational`
    Iterate `dataclasses.fields(LayoutSettings)`; every field must either be
    set by a non-default value in `_build_layout_settings`'s output for some
    argv, or be in an explicit allow-list `{"margins_linked"}`. **This is the
    test that stops F5 happening again**: the next `LayoutSettings` field
    added will fail it until someone either wires a flag or declares the
    field presentational.

Additions to `tests/test_project_cli.py`:

12. `test_a_layout_flag_typed_beside_a_deckle_source_is_reported_as_ignored`
    `main(["export", "<proj>.deckle", "-o", ..., "--margin-top", "0.5in"])`;
    stderr contains `--margin-top ignored` (the note's exact shape is
    `"note: --margin-top ignored -- <path> carries its own layout."`).
    Confirms the six join the reported set with no code change. Unfixed:
    exit 2.

13. `test_start_on_verso_beside_a_deckle_source_is_reported`
    The `store_true` case specifically — this is the one a `store_false`
    design would silently drop, and the reason for the flag's name.

Additions to `tests/test_output_command_parity.py`:

14. `test_info_export_and_schedule_agree_about_a_margined_plan`
    Run all three with the same six flags; the sheet count and warning kinds
    match. Guards that `_add_layout_args`'s four call sites all got them.

## 5. Acceptance

| Check | Command |
|---|---|
| New margin tests pass | `.venv/bin/python -m pytest -q tests/test_cli_margins.py` |
| The coverage guard specifically | `.venv/bin/python -m pytest -q -k every_layout_settings_field_is_reachable` |
| Project-source reporting | `.venv/bin/python -m pytest -q tests/test_project_cli.py` |
| All six flags parse on `export` | `.venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/f5.pdf --margin-top 0.5in --margin-bottom 0.5in --margin-outer 0.25in --slack-to outer --start-on-verso --landscape-policy scale` |
| All six flags parse on `info` | `.venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --margin-top 0.5in --margin-bottom 0.5in --margin-outer 0.25in --slack-to outer --start-on-verso --landscape-policy scale` |
| All six flags parse on `impose` and `schedule` | `.venv/bin/python -m deckle.cli impose tests/fixtures/sample.pdf -o /tmp/f5.deckle --margin-top 0.5in --slack-to split --start-on-verso && .venv/bin/python -m deckle.cli schedule tests/fixtures/sample.pdf --margin-outer 0.25in --landscape-policy letterbox >/dev/null` |
| The margins survive `impose` into the project | `.venv/bin/python -c "import json;d=json.load(open('/tmp/f5.deckle'));assert d['layout']['margin_top_pt']==36.0, d['layout']" ` |
| The GUIDE no longer apologises | `! grep -q "This is a genuine gap, not an omission from this guide" docs/GUIDE.md` |
| The GUIDE lists all six | `for f in --margin-top --margin-bottom --margin-outer --slack-to --start-on-verso --landscape-policy; do grep -q -- "$f" docs/GUIDE.md \|\| { echo "FAIL: $f missing from the GUIDE"; exit 1; }; done` |
| No new parser was invented | `test "$(grep -c 'def _parse_' deckle/cli.py)" = "7"` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Greps run against the current tree:

```
$ grep -c 'def _parse_' deckle/cli.py
7
$ .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/x.pdf --margin-top 0.5in
usage: deckle [-h] [--version]
              {impose,export,info,schedule,crop-preview,dummy} ...
deckle: error: unrecognized arguments: --margin-top 0.5in
$ grep -n "This is a genuine gap" docs/GUIDE.md
160:> project. This is a genuine gap, not an omission from this guide.
```

## 6. Out of scope

- **B19** — deciding whether `letterbox` becomes distinct from `scale` or the
  enum collapses. F5 exposes the setting and describes it truthfully; it does
  not implement or delete anything.
- **B24** — `_layout_flags_given` wrongly reporting `--sheets`, `--pass` and
  `--profile` as ignored. F5 relies on the same mechanism working correctly
  for genuine layout flags, which it does.
- **B25** — `_parse_paper` rejecting `cm` while its message lists it. The
  three margin flags use `_parse_length_pt`, which handles all four units.
- **M4** — splitting `cli.py`. Six `add_argument` calls now; the split moves
  them later.
- **M5** — one `PAPER_SIZES` table shared by the GUI and CLI.
- **D5** — the rest of GUIDE §8's missing rows.
- **`margins_linked`.** Presentational; deliberately CLI-invisible, and named
  as such in the new GUIDE sentence and in test 11's allow-list.
- **A `--margin` shorthand** setting all three at once. The panel has "Link
  margins" for that, and a fourth way to write three numbers is a control
  that changes nothing new.

## 7. decisions.md entry

```
## 2026-09-05 — The CLI could not set six of the twenty layout settings
- Symptom: `--gutter` was the only margin flag, so `deckle impose` always wrote a project with zero head, tail and fore-edge margins -- which `margin_outer_pt`'s own docstring calls "rarely what you want on a real printer" and guarantees a `clipped_by_imageable_area` warning. `slack_to`, `start_on_recto` and `landscape_policy` were equally unreachable. The GUIDE apologised for it in two places and called it "a genuine gap, not an omission from this guide".
- Fix: six flags in `_add_layout_args` -- `--margin-top`, `--margin-bottom`, `--margin-outer`, `--slack-to`, `--start-on-verso`, `--landscape-policy` -- and six lines in `_build_layout_settings`. No new parsers. `--start-on-verso` is named for the negative because `store_true` cannot clear a default-True field, and a `store_false` flag would never be reported as ignored against a `.deckle` source.
- Surfaces: `_add_layout_args` feeds four subparsers, so one addition reaches `impose`, `export`, `info` and `schedule` together, and `_layout_flags_given` picks them up with no change. A new test walks `dataclasses.fields(LayoutSettings)` and fails on any field that is neither CLI-reachable nor declared presentational, so the next field added cannot repeat this.
- Watch: `--landscape-policy`'s help says outright that `scale` and `letterbox` do not currently differ, because they do not -- `layout.py` branches on `== "rotate"` and nothing else. The GUI tooltip still promises two behaviours; that is B19's to settle, and a second copy of the promise would have doubled the cost of retracting it.
- Commit: <fill in>
```

## 8. Traps

- **`_add_layout_args` is called four times** (`cli.py:1247, 1305, 1310,
  1324`). One addition reaches every one of them; do not add per-subparser
  copies.
- **`_parse_length_pt` refuses a minus sign**, deliberately — every length it
  was written for is a magnitude, and `_parse_offset_pair` exists separately
  for the one signed value. A negative margin would place content outside its
  own box. Do not reach for `_SIGNED_LENGTH_RE` here.
- **`dest=` matters.** `--margin-top` would otherwise land on
  `args.margin_top`, and `_build_layout_settings` reads
  `args.margin_top_pt`. Setting `dest` to the field name is the house
  pattern already used by `--trim`/`trim_pt` and `--signatures`/
  `signature_lengths`.
- **`--start-on-verso` inverts.** `start_on_recto=not args.start_on_verso`.
  Getting this backwards produces a project whose first page is a verso when
  nobody asked, and `_lead_for_recto` will dutifully insert the blank.
- **`_layout_flags_given` compares against `action.default`, not against the
  dataclass default.** They must match, or a `.deckle` source will report a
  flag as ignored that the user never typed. All six defaults above are the
  `LayoutSettings` defaults; keep them in step.
- **`build_parser()` is rebuilt on every project load** just to introspect
  defaults (`cli.py:639`, M4's complaint). Six more actions is six more
  objects on that path; it is still cheap, but do not add anything expensive
  to a parser default.
- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
- The pre-commit hook refuses a code commit that does not also touch
  `docs/decisions.md`.
