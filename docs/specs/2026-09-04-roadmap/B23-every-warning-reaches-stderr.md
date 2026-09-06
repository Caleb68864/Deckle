# B23 — Print every warning a `.deckle` load raises, not one class of them

**Roadmap item:** `docs/ROADMAP.md` B23
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`_load_project_or_report` wraps `load_project` in
`warnings.catch_warnings(record=True)` so the advisory about a source path
outside the project roots can be rendered as `note: …` on stderr instead of
as Python's default warning format, which points at a line inside Deckle.
Good — but the loop that re-emits them filters to
`PathOutsideRootsAdvisory` and drops everything else on the floor.

`catch_warnings(record=True)` suppresses **all** warnings raised inside the
block. So `UnknownLayoutFieldsWarning` — the class
`deckle/core/project_io.py:296-306` exists precisely to announce that a
project carries layout keys this build does not understand — is caught,
filtered out, and never seen. A project made in a newer build silently
loses fields on the command line, which is the exact failure the warning
was written for:

> Non-fatal by design. The alternative … means *any* field ever added or
> removed permanently breaks every project file written on the other side
> of that change.

Non-fatal, yes. Silent, no.

The concrete user action: someone shares a `.deckle` produced by a later
build (or hand-edits one). `deckle info book.deckle` reports the page count,
the sheet count and `layout warnings: none`, and the layout it used is not
the layout in the file.

Verified on the tree at `08e7f49`:

```bash
python - <<'EOF'
import json
d = json.load(open("p.deckle"))
d["layout"]["future_field"] = 1
json.dump(d, open("p2.deckle", "w"))
EOF
python -m deckle.cli info p2.deckle
```

Current output — no mention of `future_field` anywhere, on stdout or stderr:

```
project: p2.deckle
page count: 4
detected page sizes (pt):
  612.00 x 792.00
signature count: 0
sheet count: 2
blank count: 0
layout warnings: none
```

The `layout warnings: none` line makes it worse: the command explicitly
says there was nothing to report.

## 2. Current code

`deckle/cli.py:507-519`:

```python
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # The directory the user is working in counts as chosen. Without
            # it, the ordinary case -- a project saved beside its output,
            # sources in Downloads -- advises on every single load, and Python
            # renders a bare warning with a file and line number that points
            # at Deckle rather than at anything the user did.
            project = load_project(path, allowed_roots=(os.getcwd(),))
        for warning in caught:
            if issubclass(warning.category, PathOutsideRootsAdvisory):
                print(f"note: {warning.message}", file=sys.stderr)
        return project
```

The two warnings `load_project` can emit,
`deckle/core/project_io.py:544-549`:

```python
            if not _path_within_roots(ref.path, roots):
                if on_outside_roots is None:
                    warnings.warn(
                        f"Source path resolves outside the project directory and "
                        f"allowed roots: {ref.path}",
                        PathOutsideRootsAdvisory,
                        stacklevel=2,
                    )
```

and `deckle/core/project_io.py:364-371`:

```python
    unknown = sorted(set(data) - known)
    if unknown:
        warnings.warn(
            "ignoring layout fields this build does not recognise: "
            + ", ".join(unknown),
            UnknownLayoutFieldsWarning,
            stacklevel=2,
        )
```

`load_project`'s own docstring already documents the second one as part of
the contract, `deckle/core/project_io.py:483-485`:

```python
    :raises UnknownLayoutFieldsWarning: never raised -- emitted through
        :mod:`warnings` when the file carries layout keys this build does
        not recognise, so field drift in either direction still opens.
```

The CLI's import line, `deckle/cli.py:38-44`, brings in exactly one warning
class:

```python
from deckle.core.project_io import (
    PathOutsideRootsAdvisory,
    SourceChangedWarning,
    SourceMissingError,
    load_project,
    save_project,
)
```

The desktop app has the same shape, `deckle/app/main.py:960-972` — it
records `PathOutsideRootsAdvisory` to the diagnostics log and drops the
rest. That is a separate surface with a separate remedy (a status-bar
message, not stderr) and is **out of scope**; see §6.

### Every call site

`_load_project_or_report`: `deckle/cli.py:494` (definition),
`deckle/cli.py:636` (the only caller, inside `_resolve_input`).

`_resolve_input`: `deckle/cli.py:621` (definition), called from
`_cmd_info` (`:866`), `_cmd_export` (`:955`), `_cmd_impose` (`:1058`),
`_cmd_schedule` (`:1090`).

`PathOutsideRootsAdvisory`: `deckle/core/project_io.py:119` (definition),
`:546` (raise), `deckle/cli.py:39` (import), `:517` (filter),
`deckle/app/main.py:28` (import), `:971` (filter), plus
`tests/test_project_io.py` and `tests/test_hardening_io.py`.

`UnknownLayoutFieldsWarning`: `deckle/core/project_io.py:296` (definition),
`:369` (raise), `:483` (docstring). **No production module outside
`project_io` names it** — confirmed by
`grep -rn "UnknownLayoutFieldsWarning" --include="*.py" .`, which returns
`deckle/core/project_io.py` (3 lines), `tests/test_project_io.py:330`,
`:338`, `:355`, `:363`, `:372`, `:377` and
`tests/test_layout_field_types.py:3`, `:193`, `:197`. Every one of those
asserts it is emitted *by `load_project` / `_layout_from_dict`*; none
asserts a user ever sees it.

Ordering note: `_layout_from_dict` warns about unknown keys at
`deckle/core/project_io.py:366-371` **before** `_check_layout_values` can
raise at `:376`. So a project with both an unknown key and a bad value
will, after this change, print a `note:` line and then an `error:` line —
relevant to the `startswith("error:")` assertions listed under §8.

`warnings.catch_warnings`: `deckle/cli.py:508`, `deckle/app/main.py:961`.

### Existing tests over this code

- `tests/test_project_cli.py::test_layout_flags_are_reported_as_ignored_not_silently_applied`
  — asserts on stderr from `info` on a project
- `tests/test_project_cli.py` — the whole "opening it" and "when the
  sources have moved" sections
- `tests/test_project_io.py` — the drift suite, which asserts
  `UnknownLayoutFieldsWarning` is emitted **by `load_project`**, not that
  the CLI shows it
- `tests/test_atomic_writes.py:50-52` — `_load` wraps `load_project` in
  `pytest.warns(Warning)`, which is why every test in that file expects at
  least one warning

**No test asserts the CLI reports a warning other than the advisory.** That
is the gap.

## 3. Change

### The rule

**Every warning caught inside the block is re-emitted as `note: …` on
stderr, regardless of class.** No filter, no allow-list.

Three reasons this is the right shape rather than "add
`UnknownLayoutFieldsWarning` to the `if`":

1. An allow-list of warning classes is a list maintained in one place and
   added to in another, which is the failure this codebase has recorded
   repeatedly (the `refresh_from_project` block-list, the `_on_unit_changed`
   box-list — see **M1**). The next warning `project_io` grows would be
   dropped exactly the same way.
2. Every warning `load_project` can raise is by construction something the
   user should know: the whole reason it is a warning and not an exception
   is that the load continued *with something changed*.
3. A warning from anywhere else in the stack that happens to fire during a
   project load — a `DeprecationWarning` out of a dependency, say — is
   noise the user can ignore on stderr, and is strictly better than being
   swallowed. `catch_warnings(record=True)` suppresses it either way; the
   only question is whether it is visible.

Rejected: dropping `catch_warnings` and letting Python render the warnings
itself. The comment at `deckle/cli.py:511-514` records why that was wrong —
Python's default format names a file and line inside Deckle, which tells
the user nothing they can act on.

Rejected: raising the exit code for a warning. `main`'s docstring
(`deckle/cli.py:1420-1423`) is explicit: "Layout warnings go to stderr and
never change this: a warning is advice, not a failure."

### `note:` prefix and stderr

Both are unchanged and both are already the house pattern:
`_emit_warnings` (`deckle/cli.py:901-920`) sends layout warnings to stderr
"so `deckle export` keeps a clean stdout for scripting", and
`_apply_auto_crop` (`:603-608`) and `_report_rule` (`:1041-1046`) both use
`note: ` on stderr for advisories.

### Steps

1. **`deckle/cli.py:516-518`** — replace the filtered loop with an
   unfiltered one:

   ```python
           for warning in caught:
               # Every one of them, not one class. `catch_warnings(record=True)`
               # suppresses everything raised inside the block, so a filter
               # here is a filter on what the user is allowed to find out --
               # and `UnknownLayoutFieldsWarning`, which exists precisely to
               # say "this file carries settings this build dropped", was on
               # the wrong side of it. A project from a newer build lost
               # fields silently while `deckle info` printed
               # "layout warnings: none".
               #
               # An allow-list would be a list maintained here and added to
               # in `project_io`, which is the shape that produced this bug.
               print(f"note: {warning.message}", file=sys.stderr)
   ```

2. **`deckle/cli.py:38-44`** — remove `PathOutsideRootsAdvisory` from the
   import list; nothing in `cli.py` references it after step 1. Confirm
   with `grep -n "PathOutsideRootsAdvisory" deckle/cli.py`, which must
   return nothing.

3. **`_load_project_or_report`'s docstring** (`deckle/cli.py:495-506`) —
   add a paragraph:

   ```
       Warnings raised during the load are re-emitted as ``note:`` lines on
       stderr rather than through Python's own renderer, which names a file
       and line inside Deckle. All of them, not a chosen class: the load
       continued *with something changed* in every case, and which changes
       are worth mentioning is not a decision this function is in a
       position to make.
   ```

4. **`docs/GUIDE.md` §9 (Troubleshooting)** — add a short entry for the
   `note: ignoring layout fields this build does not recognise: …` line:
   what it means (the project was written by a build that knows settings
   this one does not), and what to do (open it in the newer build, or
   accept that those settings are not applied). Two sentences. **D6**
   covers the GUIDE's larger gaps; this is only the line this change makes
   newly visible.

## 4. Tests

All in `tests/test_project_cli.py`, which already has the `_cli` subprocess
helper and a `project` fixture that writes a `.deckle` with a non-default
layout.

### `test_a_layout_field_this_build_does_not_know_is_reported`

- **File / function:** `tests/test_project_cli.py::test_a_layout_field_this_build_does_not_know_is_reported`
- **Setup:** use the `project` fixture; read its JSON; add
  `data["layout"]["future_field"] = 1`; write it to a second path in
  `tmp_path`; run `_cli("info", str(second))`.
- **Assertion in words:** exit 0 (a warning is advice, not a failure);
  stderr contains `note:`, `future_field`, and the phrase
  `does not recognise`.
- **Expected failure on the unfixed tree:** stderr is empty, so
  `assert "future_field" in result.stderr` fails with
  `assert 'future_field' in ''`.

### `test_a_dropped_layout_field_is_reported_by_every_command_that_opens_a_project`

- **Setup:** as above; parametrise over the four commands that call
  `_resolve_input`: `("info", src)`, `("export", src, "-o", out)`,
  `("schedule", src)`, and `("impose", src, "-o", out2)`.
- **Assertion in words:** each exits 0 and each prints the `note:` line.
  The warning is emitted once per load, and every command loads.
- **Unfixed tree:** all four fail on the missing substring.

### `test_the_out_of_roots_advisory_is_still_reported`

- **Setup:** a project whose source lives outside both the project
  directory and the working directory — build it by saving a `.deckle` in
  `tmp_path/"a"` whose source is in `tmp_path/"b"`, and run `_cli` with
  `cwd=str(tmp_path / "c")`. Use `check_sources` implicitly (the source
  must still exist, so copy `FIXTURE` into `tmp_path/"b"`).
- **Assertion in words:** stderr contains `note:` and
  `outside the project directory`. Pins that removing the filter did not
  remove the case the filter was written for.
- **Unfixed tree:** passes. Keep it — this is the regression guard on the
  change itself.

### `test_a_warning_does_not_change_the_exit_code`

- **Setup:** the `future_field` project; `_cli("info", str(path))`.
- **Assertion in words:** `returncode == 0`. `main`'s docstring promises
  it; nothing asserted it for this path.
- **Unfixed tree:** passes.

### `test_a_clean_project_prints_no_notes`

- **Setup:** the plain `project` fixture, run from `REPO` so the source is
  inside the working directory and no advisory fires.
- **Assertion in words:** `"note:" not in result.stderr`. The guard against
  an unfiltered loop turning every ordinary load into noise — which is the
  real risk of this change and the reason the `allowed_roots=(os.getcwd(),)`
  argument exists.
- **Unfixed tree:** passes.

### Tests that must keep passing untouched

`tests/test_project_cli.py::test_layout_flags_are_reported_as_ignored_not_silently_applied`
asserts `"ignored" in result.stderr`. The new
`UnknownLayoutFieldsWarning` text contains "ignoring", not "ignored", so
there is no accidental cross-match — but the two messages now share a
stream, so read that test before assuming it is unaffected.

## 5. Acceptance

| Check | Command |
|---|---|
| The filter is gone | `! grep -n "issubclass(warning.category" deckle/cli.py` |
| The advisory class is no longer imported by the CLI | `! grep -n "PathOutsideRootsAdvisory" deckle/cli.py` |
| An unknown layout field is reported | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py -k "does_not_know or every_command_that_opens or still_reported or no_notes"` |
| Project opening is otherwise unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py tests/test_project_io.py tests/test_atomic_writes.py` |
| Every `startswith("error:")` assertion still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_output_durability.py tests/test_paper_bounds.py tests/test_layout_field_types.py tests/test_hardening_platform.py tests/test_output_command_parity.py tests/test_project_file_shape.py` |
| The CLI's other stderr contracts hold | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_errors.py tests/test_hardening_io.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: row 1's grep returns **1** hit
(`deckle/cli.py:517`) and must return none. Row 2's grep returns **2** hits
(`deckle/cli.py:39` and `:517`) and must return none.

## 6. Out of scope

- **The desktop app's identical filter** at `deckle/app/main.py:971`. It
  logs the advisory to diagnostics rather than printing it, and the right
  surface there is the status bar or a dialog, not `print`. It is the same
  defect and it needs a UI decision this spec does not make. Not in the
  roadmap under its own ID; note it when this lands.
- **B24** — `_layout_flags_given` reporting non-layout flags as ignored.
  Same file, same command path, different function, and it writes to the
  same stderr stream. Independent.
- **B21**, **B22**, **B25** — same file, unrelated functions.
- **Making a warning change the exit code.** Explicitly refused by `main`'s
  docstring.
- **D6** — the GUIDE's missing coverage of paper-by-weight, the gathering
  suggestion and autosave recovery. Step 4 adds one troubleshooting entry
  and nothing else.

## 7. decisions.md entry

```
## 2026-09-05 — The CLI caught every warning and showed one of them
- Symptom: `_load_project_or_report` wraps `load_project` in `warnings.catch_warnings(record=True)` -- which suppresses everything raised inside it -- and then re-emitted only `PathOutsideRootsAdvisory`. So `UnknownLayoutFieldsWarning`, the class written precisely to announce that a project carries settings this build dropped, never reached the user. A `.deckle` from a newer build lost fields silently while `deckle info` printed "layout warnings: none".
- Fix: Re-emit every caught warning as `note:` on stderr. No allow-list: every warning `load_project` can raise means the load continued with something changed, and a list of blessed classes maintained in `cli.py` and added to in `project_io.py` is the shape that produced this.
- Surfaces: `load_project`'s docstring already documented `UnknownLayoutFieldsWarning` as part of its contract. The contract was honoured by the library and discarded by its caller. `deckle/app/main.py:971` has the identical filter and needs its own answer, because a status bar is not stderr.
- Watch: `catch_warnings(record=True)` is a suppressor, not a collector. Any code that opens one owes the user a decision about everything it caught, not just the class it was opened for.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B21, B22, B23, B24 and B25 all edit `deckle/cli.py`.
  Recommended order: **B25 → B23 → B21 → B24 → B22.** B23 is early because
  it is confined to `_load_project_or_report` and touches nothing the other
  four do.
- **`catch_warnings` is not thread-safe and is process-global while open.**
  The block is small and synchronous here; do not widen it.
- **`warning.message` is a `Warning` instance, not a string.** The f-string
  calls `str()` on it, which yields the message text. That is what the
  existing line does; keep it.
- **Removing the import must not remove `SourceChangedWarning` or
  `SourceMissingError`**, which are still caught as exceptions at
  `deckle/cli.py:520` and `:529`.
- **`tests/test_atomic_writes.py:50-52` wraps `load_project` in
  `pytest.warns(Warning)`** and would fail if `load_project` ever stopped
  warning. This spec does not change `load_project`; that test is a
  reminder that the warning is load-bearing somewhere.
- **New stderr output can break substring assertions elsewhere.**
  `grep -rn 'startswith("error:")' tests/` currently finds **nine**
  assertions, in `tests/test_cli_output_durability.py:182`,
  `tests/test_paper_bounds.py:103`, `tests/test_project_cli.py:176`,
  `tests/test_layout_field_types.py:150`,
  `tests/test_hardening_platform.py:172`,
  `tests/test_output_command_parity.py:168` and `:181`, and
  `tests/test_project_file_shape.py:89` and `:173`. Each breaks if the
  project it loads emits *any* warning before the error. Verified: none of
  them writes a project with an unknown layout key, and all nine pass
  today (so none of them trips the out-of-roots advisory either). Run all
  of those files whole after the change, not just the new tests.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
