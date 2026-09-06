# B22 — Apply a profile's back offset whenever `--profile` is given

**Roadmap item:** `docs/ROADMAP.md` B22
**Depends on:** B21
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`deckle export --profile <name>` without `--pass` does nothing at all. The
flag parses, the profile is never loaded, and the export runs exactly as if
it had not been typed — including the calibrated `back_offset_x_pt` /
`back_offset_y_pt` that are the whole reason someone measured a
registration target.

A both-faces export interleaves fronts and backs into one PDF. The back
faces in that PDF *do* get a registration offset when one is supplied —
`export_plan(..., back_offset_pt=...)` applies it to backs and only to backs
— so there is nothing about the both-faces path that makes a profile
meaningless there. It is simply never consulted.

The concrete user action: a binder calibrates their printer, measures the
back sitting 3pt left and 2pt high, saves a profile, and runs

```bash
python -m deckle.cli export book.pdf -o out.pdf --profile mine
```

to send to a shop or to a machine with a duplexer. Every back in `out.pdf`
is off by the 3,-2 they measured. Nothing on stdout mentions it — the
`registration:` line only prints when `back_offset` is non-zero, and it is
zero because nothing loaded the profile.

Verified on the tree at `08e7f49`, with a saved profile carrying
`back_offset_x_pt=3.0, back_offset_y_pt=-2.0`:

```bash
python -m deckle.cli export d.pdf -o o2.pdf --profile mine
# wrote o2.pdf

python -m deckle.cli export d.pdf -o o3.pdf --profile mine --pass back
# wrote o3.pdf
# registration: back faces moved +3, -2pt (profile 'mine'). Corrects a constant offset only -- not skew or scale.
# After pass 1 finishes, reverse the printed stack ...
```

The first invocation prints no `registration:` line because the offset was
never read.

There is a second, quieter half: because `--profile` is only ever read
inside the `--pass` branch, an **unknown or corrupt** profile name is also
not diagnosed without `--pass`. `deckle export book.pdf -o out.pdf --profile
typo` exits 0.

## 2. Current code

`deckle/cli.py:971-999`, the whole pass/offset block of `_cmd_export`:

```python
    side = None
    rotate_180 = False
    print_pass = None
    back_offset = (0.0, 0.0)
    offset_source = ""
    if args.pass_side is not None:
        if args.profile is None:
            print(
                f"error: --pass {args.pass_side} needs --profile, because "
                "neither the sheet order nor the half turn has a safe "
                "default -- guessing wrong prints every back onto the wrong "
                "front. Built-in profiles: "
                f"{', '.join(sorted(BUILTIN_PRESETS))}",
                file=sys.stderr,
            )
            return 1
        profile = _resolve_profile(args.profile)
        if profile is None:
            return 1
        back_offset = (profile.back_offset_x_pt, profile.back_offset_y_pt)
        offset_source = f"profile {args.profile!r}"
        print_pass = _pass_for(plan, args.pass_side, profile, selection)
        side = args.pass_side
        selection = print_pass.sheet_order
        rotate_180 = print_pass.side == "back" and print_pass.rotate_backs

    if args.back_offset is not None:
        back_offset = args.back_offset
        offset_source = "--back-offset"
```

`args.profile` appears nowhere else in the file except its own
`add_argument` (`deckle/cli.py:1295-1304`) and the `_resolve_profile` call
above. Confirmed by `grep -n "args.profile" deckle/cli.py`, which returns
`:977`, `:987`, `:991` — all inside that `if args.pass_side is not None:`
block.

The flag's own help text, `deckle/cli.py:1295-1304`:

```python
    export_parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help=(
            "the printer profile describing your reload behaviour -- a "
            "calibrated one saved under the printer's name, or a built-in: "
            + ", ".join(sorted(BUILTIN_PRESETS))
        ),
    )
```

It promises nothing about `--pass`, so nothing warns the user that the flag
is inert alone.

The `--back-offset` help already states the precedence this spec
formalises, `deckle/cli.py:1283-1294`:

```python
    export_parser.add_argument(
        "--back-offset",
        type=_parse_offset_pair,
        default=None,
        metavar="X,Y",
        help=(
            "move back faces by X,Y so they land behind their fronts -- a "
            "front/back registration correction, e.g. 3,-2 or 0.5mm,-1mm. "
            "Overrides the value stored in --profile. Corrects a constant "
            "offset only, not skew or scale"
        ),
    )
```

**"Overrides the value stored in `--profile`"** — that is the documented
rule already, implemented at `deckle/cli.py:997-999`. It only ever had an
effect in the `--pass` case, because that is the only case where a profile
value existed to override.

The reporting helper, `deckle/cli.py:404-417`:

```python
def _report_registration(offset_pt: tuple[float, float], source: str) -> None:
    """Say that back faces were moved, and by how much.

    The correction usually comes from a saved profile rather than from
    this invocation, so without this the geometry would change for
    reasons nothing on screen mentions. It also cannot be seen in the
    output: a shifted back looks exactly like an unshifted one until it
    is printed and held up against its own front.
    """
    dx, dy = offset_pt
    print(
        f"registration: back faces moved {dx:+g}, {dy:+g}pt ({source}). "
        "Corrects a constant offset only -- not skew or scale."
    )
```

called at `deckle/cli.py:1022-1023`:

```python
    if back_offset != (0.0, 0.0):
        _report_registration(back_offset, offset_source)
```

The exporter's contract for the offset — grep
`grep -n "back_offset_pt" deckle/core/export.py` — shows it is applied to
back faces only, so it is meaningful in the both-faces export as well as in
a single back pass.

### Every call site

`args.profile`: `deckle/cli.py:977`, `:987`, `:991`.
`args.pass_side`: `deckle/cli.py:976`, `:979`, `:992`, `:993`, and the
`add_argument` at `:1272-1282`.
`_resolve_profile`: `deckle/cli.py:420` (definition), `:987` (only caller).
`_pass_for`: `deckle/cli.py:451` (definition), `:992` (only caller).
`_report_registration`: `deckle/cli.py:404` (definition), `:1023` (only
caller).

### Existing tests over this code

`tests/test_cli_passes.py` — the whole file (12 tests). Relevant:

- `::test_a_pass_without_a_profile_is_refused` — the reverse direction,
  which must keep working: `--pass` still requires `--profile`.
- `::test_an_unknown_profile_names_the_ones_that_exist` — passes
  `--pass back --profile no-such-printer`.
- `::test_a_reversing_printer_gets_its_backs_in_reverse_sheet_order` and
  the other ordering/rotation tests — all pass `--pass`.

`tests/test_registration.py` covers `--back-offset` and the profile fields;
`grep -n "profile" tests/test_registration.py` shows it exercises
`PrinterProfile.load` and the geometry, not the CLI's flag combination.

**No test gives `--profile` without `--pass`.** That is the gap.

## 3. Change

### The rule

**A profile's back offset is applied whenever `--profile` is given, with or
without `--pass`.** A saved profile is a statement about one physical
printer, and its registration correction is true of every back face that
printer will ever put down — in a single back pass or interleaved in a
both-faces PDF.

What `--pass` continues to control on its own: sheet order, the half turn,
`side`, and the reload instruction. Those are *pass* properties and have no
meaning without one, which is why the existing "`--pass` needs `--profile`"
refusal stays exactly as it is.

Rejected: warning that `--profile` alone is ignored. That is B24's shape of
answer, and it is the wrong one here — the flag has a well-defined meaning
without `--pass`, and telling the user their calibration was ignored is
worse than applying it.

Rejected: applying the whole profile without `--pass` (sheet order, half
turn). Both are pass-relative; reordering the sheets of a both-faces PDF or
turning every back in it would produce a document nobody asked for.

### How `--back-offset` and `--profile` interact

**The explicit flag wins, always.** `--back-offset` given on the command
line overrides whatever the profile stores, whether or not `--pass` is
present. This is not a new decision — `--back-offset`'s help text has said
"Overrides the value stored in --profile" since it was written, and
`deckle/cli.py:997-999` already implements it by assigning after the profile
branch. The only change is that the profile branch now runs more often, so
the rule has more occasions to apply.

`offset_source` follows: `"--back-offset"` when the flag decided, and
`f"profile {args.profile!r}"` when the profile did. The
`registration:` line therefore always names which of the two the number
came from, which is the point of that line.

`--back-offset 0,0` is an explicit zero and **suppresses** the profile's
offset. It parses to `(0.0, 0.0)`, which is not `None`, so the assignment at
`:998` takes it; and `_report_registration` is not called for a zero offset,
so nothing is printed. That is right: the user asked for no correction and
got none. Say so in the `--back-offset` help.

### Steps

1. **`deckle/cli.py`, `_cmd_export`** — restructure lines 971-999 so the
   profile is resolved once, before the pass branch:

   ```python
       side = None
       rotate_180 = False
       print_pass = None
       back_offset = (0.0, 0.0)
       offset_source = ""

       # Resolved whether or not `--pass` was given. A saved profile
       # describes one physical printer, and its registration correction is
       # true of every back face that printer lays down -- in a single back
       # pass or interleaved in a both-faces PDF. It used to be read only
       # inside the `--pass` branch, so `export --profile mine` silently
       # discarded the numbers someone had measured off a target, and did
       # not even report a misspelled profile name.
       profile = None
       if args.profile is not None:
           profile = _resolve_profile(args.profile)
           if profile is None:
               return 1
           back_offset = (profile.back_offset_x_pt, profile.back_offset_y_pt)
           offset_source = f"profile {args.profile!r}"

       if args.pass_side is not None:
           if profile is None:
               print(
                   f"error: --pass {args.pass_side} needs --profile, because "
                   "neither the sheet order nor the half turn has a safe "
                   "default -- guessing wrong prints every back onto the wrong "
                   "front. Built-in profiles: "
                   f"{', '.join(sorted(BUILTIN_PRESETS))}",
                   file=sys.stderr,
               )
               return 1
           print_pass = _pass_for(plan, args.pass_side, profile, selection)
           side = args.pass_side
           selection = print_pass.sheet_order
           rotate_180 = print_pass.side == "back" and print_pass.rotate_backs

       if args.back_offset is not None:
           # The explicit flag wins over the stored one, in both
           # directions: `--back-offset 0,0` is a deliberate "no
           # correction" and suppresses the profile's numbers.
           back_offset = args.back_offset
           offset_source = "--back-offset"
   ```

   Note the refusal's condition changes from `args.profile is None` to
   `profile is None`. Both mean "no usable profile", and using the resolved
   value is what keeps a *failed* resolution from falling through into the
   pass branch — though `_resolve_profile` returning `None` already
   `return 1`s above, so this is belt and braces rather than a behaviour
   change. Keep the message text byte-identical;
   `tests/test_cli_passes.py::test_a_pass_without_a_profile_is_refused`
   asserts `"--profile" in err`.

2. **`deckle/cli.py`, `--profile` help** (lines 1295-1304) — say what the
   flag does on its own:

   ```python
       export_parser.add_argument(
           "--profile",
           default=None,
           metavar="NAME",
           help=(
               "the printer profile describing your reload behaviour -- a "
               "calibrated one saved under the printer's name, or a built-in: "
               + ", ".join(sorted(BUILTIN_PRESETS))
               + ". On its own it supplies the profile's front/back "
               "registration correction; with --pass it also decides the "
               "sheet order and the half turn"
           ),
       )
   ```

3. **`deckle/cli.py`, `--back-offset` help** (lines 1283-1294) — add the
   explicit-zero sentence:

   ```python
               "move back faces by X,Y so they land behind their fronts -- a "
               "front/back registration correction, e.g. 3,-2 or 0.5mm,-1mm. "
               "Overrides the value stored in --profile, including "
               "--back-offset 0,0 to suppress it. Corrects a constant "
               "offset only, not skew or scale"
   ```

4. **`docs/GUIDE.md` §6**, the "Front/back registration" subsection. It
   currently shows only

   ```bash
   python -m deckle.cli export book.pdf -o out.pdf --back-offset 3,-2
   ```

   Add the profile form beside it, with the output line it now produces:

   ```bash
   python -m deckle.cli export book.pdf -o out.pdf --profile mine
   # registration: back faces moved +3, -2pt (profile 'mine').
   ```

   and one sentence: the flag wins over the profile, and `--back-offset 0,0`
   turns the correction off for one run. **D5** covers the rest of the
   GUIDE's CLI drift; this is only the line this change makes wrong.

## 4. Tests

All in `tests/test_cli_passes.py`, which already calls `main()` in-process
and has `_numbered_source` and `_page_labels` helpers. A new fixture is
needed because the file has never touched a saved profile:

```python
@pytest.fixture
def saved_profile(tmp_path, monkeypatch):
    """A calibrated profile on disk, under an isolated config root.

    `deckle.core.paths` consults `sys.platform` at call time, so the
    platform has to be pinned or this only works on Linux.
    """
```

It should `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))`,
`monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")`, and
`dataclasses.replace(BUILTIN_PRESETS["generic_face_down_reversed"],
back_offset_x_pt=3.0, back_offset_y_pt=-2.0).save("mine")`. This is the
pattern `tests/test_registration.py:106-112` uses.

### `test_a_profile_without_a_pass_still_applies_its_back_offset`

- **Setup:** `saved_profile`; a 4-page source; run
  `main(["export", src, "-o", out, "--profile", "mine"])`; read `capsys`.
- **Assertion in words:** exit 0; stdout contains
  `registration: back faces moved +3, -2pt (profile 'mine')`.
- **Unfixed tree:** stdout is just `wrote <out>`; the assertion fails with
  the `registration:` substring missing.

### `test_the_offset_reaches_the_back_faces_of_a_both_faces_export`

- **Setup:** as above, plus a control run with no `--profile` into a second
  path. Compare the two PDFs' back pages.
- **Assertion in words:** the two files differ, and specifically the back
  faces differ while the fronts do not. Read the content streams with
  `pikepdf` — the file already imports `pikepdf` at line 24 — and compare
  `bytes(pdf.pages[i].Contents.read_bytes())` for an odd (back) and an even
  (front) index. The message alone is not enough: it would still print if
  the offset were computed and then dropped on the way to `export_plan`.
- **Unfixed tree:** the two files are byte-identical; the "backs differ"
  assertion fails.

### `test_an_explicit_back_offset_beats_the_profiles_own`

- **Setup:** `saved_profile`; run with both
  `--profile mine --back-offset 1,1`.
- **Assertion in words:** stdout names `+1, +1pt` and `(--back-offset)`,
  not `+3, -2pt` and not `profile 'mine'`.
- **Unfixed tree:** passes — the flag was already the only source without
  `--pass`. Keep it: it is the test that fails if someone "fixes" the
  precedence by letting the profile win.

### `test_an_explicit_zero_back_offset_suppresses_the_profiles_own`

- **Setup:** `saved_profile`; run with `--profile mine --back-offset 0,0`;
  and a control run with `--profile mine` alone.
- **Assertion in words:** the zero run prints no `registration:` line, and
  its output PDF is byte-identical to a run with neither flag; the control
  run does print one. Pins that an explicit zero is a decision and not a
  missing value.
- **Unfixed tree:** the two runs are identical (both apply nothing), so the
  half asserting the control *does* print a `registration:` line fails.

### `test_an_unknown_profile_is_refused_even_without_a_pass`

- **Setup:** `main(["export", src, "-o", out, "--profile", "no-such-printer"])`.
- **Assertion in words:** exit 1, stderr names the profile, and no file was
  written at `out`.
- **Unfixed tree:** exit 0 and the PDF exists — the name is never looked at.

### `test_a_pass_still_requires_a_profile`

Already exists as
`tests/test_cli_passes.py::test_a_pass_without_a_profile_is_refused`. Do not
duplicate it; re-run it and confirm it is untouched.

### `test_a_pass_still_gets_the_profiles_offset_and_its_ordering`

- **Setup:** `saved_profile`; run `--profile mine --pass back` on a 6-page
  source.
- **Assertion in words:** stdout names `+3, -2pt (profile 'mine')`, the page
  labels are `["PAGE6", "PAGE4", "PAGE2"]` (the reversing order that
  `test_a_reversing_printer_gets_its_backs_in_reverse_sheet_order` pins),
  and the reload instruction is printed. The restructure moves the profile
  resolution above the pass branch; this is the test that catches it having
  taken the ordering with it.
- **Unfixed tree:** passes.

## 5. Acceptance

| Check | Command |
|---|---|
| The profile is resolved outside the pass branch | `.venv/bin/python -c "import inspect, deckle.cli as m; src = inspect.getsource(m._cmd_export); assert src.index('_resolve_profile(') < src.index('if args.pass_side is not None:'), 'profile still resolved inside the --pass branch'"` |
| The new registration behaviour | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_passes.py -k "without_a_pass or both_faces or explicit_back_offset or explicit_zero or unknown_profile_is_refused"` |
| Pass ordering and rotation unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_passes.py` |
| Registration geometry unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_registration.py` |
| The help text says what the flag does alone | `.venv/bin/python -m deckle.cli export --help \| grep -q "On its own it supplies"` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: row 1's assertion fails —
`if args.pass_side is not None:` appears at offset **620** of
`_cmd_export`'s source and `_resolve_profile(` at **1117**. Row 5's grep
already succeeds today, because `--back-offset`'s help contains
"front/back registration correction"; use the exact string
`On its own it supplies` for a check that actually discriminates.
`grep -n "args.profile" deckle/cli.py` returns exactly three lines
(`:977`, `:987`, `:991`), all inside the `--pass` branch.
`tests/test_golden_pinebox.py` contains no `profile`, so the golden
fixture is unaffected.

## 6. Out of scope

- **B21** — separating a corrupt saved profile from a missing one in
  `_resolve_profile`. This spec calls that function on a new path, which is
  precisely why B21 should land first: without it, `export --profile mine`
  on a corrupt file now exits 1 with the *wrong* message on a path where it
  previously said nothing at all.
- **B24** — `_layout_flags_given` reporting `--profile` as an ignored
  layout flag when the source is a `.deckle`. After this change `--profile`
  is genuinely honoured in more cases, so B24's list is more wrong, not
  less. It is still B24's.
- **B23**, **B25** — same file, unrelated functions.
- **F5** — CLI parity for the margin flags. Not this.
- **N4** — exporting a single pass from the GUI. Not this.
- **The desktop app.** `PrintDialog` gets its profile through
  `resolve_profile` and passes it to `QtPrintBackend`, which reads
  `back_offset_*` directly (`deckle/app/backend.py:641-644`). It is not
  affected.

## 7. decisions.md entry

```
## 2026-09-05 — A calibrated profile did nothing unless you also asked for a pass
- Symptom: `deckle export book.pdf -o out.pdf --profile mine` parsed the flag and never loaded the profile. `args.profile` was read only inside the `if args.pass_side is not None:` branch, so a both-faces export discarded the front/back registration correction the user had measured off a printed target -- silently, since the `registration:` line only prints for a non-zero offset. A misspelled profile name exited 0.
- Fix: The profile is resolved whenever `--profile` is given, and its back offset applies to the back faces of a both-faces export exactly as it does to a back pass. `--pass` still decides sheet order, the half turn and the reload instruction, and still refuses to run without a profile. `--back-offset` still wins over the stored value, including `--back-offset 0,0` as a deliberate "no correction".
- Surfaces: `--back-offset`'s own help had said "Overrides the value stored in --profile" since it was written. The precedence was correct and the case it applied to was almost never reachable.
- Watch: A flag that is read inside one branch of the command it belongs to is a flag with an undocumented dependency. `--pass` is documented as requiring `--profile`; nothing said the reverse, and nothing warned.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B21, B22, B23, B24 and B25 all edit `deckle/cli.py`.
  Recommended order: **B25 → B23 → B21 → B24 → B22.** B22 is last in the
  CLI cluster because it depends on B21 (it adds a caller of
  `_resolve_profile`) and reads better after B24 has settled what
  `--profile` means beside a `.deckle`.
- **The `--pass` refusal message must not change.**
  `tests/test_cli_passes.py::test_a_pass_without_a_profile_is_refused`
  asserts `"--profile" in err`, and
  `::test_an_unknown_profile_names_the_ones_that_exist` asserts the built-in
  names are listed.
- **`_resolve_profile` prints its own error and returns `None`.** Moving
  the call earlier moves that message earlier too — before the
  `--pass`-needs-`--profile` refusal could fire. That is the right order
  (a bad name is a bad name whether or not `--pass` was typed) but it
  changes which message a user sees for
  `--pass back --profile typo`: previously "no printer profile 'typo'",
  still "no printer profile 'typo'". No test asserts the ordering; confirm
  by running `tests/test_cli_passes.py` whole.
- **A both-faces export with an offset changes the output bytes.** Any
  golden-fixture test that runs `export` with a profile would move.
  `grep -n "profile" tests/test_golden_pinebox.py` returns nothing today,
  so the golden fixture is safe — re-run that grep rather than assuming it.
- **`--back-offset 0,0` parses to `(0.0, 0.0)`, not `None`.**
  `_parse_offset_pair` (`deckle/cli.py:369`) accepts a signed zero, and the
  `is not None` test at `:997` is what makes an explicit zero a decision
  rather than an absence. Do not "simplify" it to a truthiness test.
- **A loose grep on the help text proves nothing.** `--back-offset`'s
  existing help already contains "registration correction"; row 5 therefore
  greps for `On its own it supplies`, which only the new sentence has.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
