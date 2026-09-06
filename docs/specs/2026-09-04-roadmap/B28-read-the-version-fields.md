# B28 — Read the `version` field that every saved file already carries

**Roadmap item:** `docs/ROADMAP.md` B28
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Deckle writes a `version` integer into all three of its persisted formats
and reads it in one of them, in the wrong order.

**`.deckle` projects.** `save_project` writes
`{"version": FORMAT_VERSION, ...}`. `load_project` never looks at it. A
project written by a future build — one that has, say, a `binding_edge`
split into `reading_direction`, or a new page-entry field — opens silently
with those fields dropped by `_layout_from_dict`'s tolerant path. The user
gets a book that is not the one they saved, and nothing on screen says so.
The decision log's own 2026-08-04 entry ends with the sentence this defect
contradicts:

> A `version` integer in a file format is not migration tolerance; it is a
> place to *record* a version. This one had been present since SS-01 and
> unused.

It is still unused.

**Printer profiles.** `PrinterProfile.version` is a dataclass field with no
default, so a profile file that omits `version` — a hand-edited one, which
the GUIDE §6 explicitly tells users to write — does not open at all. It
raises `TypeError` from `cls(**kwargs)`, which the CLI happens to catch
(and misreports as "no printer profile 'X'"), and which the print dialog
does not catch at all. A profile from a *newer* build loads with no comment.

**Print sessions.** `load` does check `version`, but only after
`_check_state` has already run. `_check_state` validates against *this*
build's `_SessionState` field set, so a state file from a build with an
extra field, a renamed field, or a field whose type changed is refused as
`reason="state"` with a message about "this session's saved state is
missing 'x'". The truth is that another build wrote it. The two get
different remedies and the version answer is the actionable one — which is
the reasoning the `STATE_VERSION` comment block already spells out for the
v1→v2 case.

**`printer` is not type-checked.** `payload.get("printer")` goes straight
into `Project.printer`, annotated `str | None`. A `.deckle` carrying
`"printer": {"a": 1}` loads and the dict reaches
`impose_parser`'s `--printer` round trip and, in the app, the printer
combo.

Verified on the tree at `08e7f49`:

```bash
python - <<'EOF'
import json, os, tempfile

# 1. A profile with no `version` cannot be opened at all.
os.environ["XDG_CONFIG_HOME"] = cfg = tempfile.mkdtemp()
d = os.path.join(cfg, "deckle", "printer_profiles"); os.makedirs(d)
base = {"flip_axis": "long", "output_face": "down", "feed_edge": "top",
        "reverse_stack": True, "imageable_area_pt": [18, 18, 18, 18],
        "calibrated_at": "", "calibration_version": 0}
open(os.path.join(d, "NoVer.json"), "w").write(json.dumps(base))
from deckle.core.profiles import PrinterProfile
try:
    PrinterProfile.load("NoVer")
except Exception as exc:
    print("no version ->", type(exc).__name__, exc)

# 2. A profile from a newer build loads with no comment.
open(os.path.join(d, "Newer.json"), "w").write(json.dumps({**base, "version": 99}))
print("version 99 ->", PrinterProfile.load("Newer").version)
EOF
```

Current output:

```
no version -> TypeError PrinterProfile.__init__() missing 1 required positional argument: 'version'
version 99 -> 99
```

and, for the project half (run from a directory holding a `.deckle` and its
source):

```bash
python - <<'EOF'
import json, warnings
d = json.load(open("p.deckle")); d["version"] = 99; d["printer"] = {"a": 1}
json.dump(d, open("pv.deckle", "w"))
from deckle.core.project_io import load_project
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    project = load_project("pv.deckle")
print("printer =", repr(project.printer), "warnings:", [w.category.__name__ for w in caught])
EOF
```

Current output: `printer = {'a': 1} warnings: []`.

## 2. Current code

### `.deckle` — `deckle/core/project_io.py`

The constant, `deckle/core/project_io.py:38`:

```python
FORMAT_VERSION = 1
```

Written at `deckle/core/project_io.py:418-423`:

```python
    payload = {
        "version": FORMAT_VERSION,
        "pages": [_page_to_dict(p) for p in project.pages],
        "layout": _layout_to_dict(project.layout),
        "printer": project.printer,
    }
```

Never read. `load_project`'s body reads `payload["pages"]`
(`:501`), `payload["layout"]` (`:517`, `:562`) and `payload.get("printer")`
(`:563`), and nothing else. The tail, `deckle/core/project_io.py:487-499`
and `:562-564`:

```python
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        # `KeyError` rather than the `TypeError` that `payload["pages"]`
        # would raise a line later, because "JSON but not a `.deckle`
        # document" is what this function already documents `KeyError` to
        # mean ...
        raise KeyError("pages")

    stored_pages = payload["pages"]
```

```python
    layout = _layout_from_dict(payload["layout"])
    printer = payload.get("printer")
    return Project(pages=pages, layout=layout, printer=printer)
```

The existing precedent for a tolerant, non-fatal warning class,
`deckle/core/project_io.py:296-306`:

```python
class UnknownLayoutFieldsWarning(UserWarning):
    """Emitted when a ``.deckle`` carries layout keys this build does not know.

    Non-fatal by design. The alternative -- passing every stored key straight
    into ``LayoutSettings(**kwargs)`` -- means *any* field ever added or
    removed permanently breaks every project file written on the other side
    of that change. ...
    """
```

and the existing shape-naming helper used by every "this is not a Deckle
project" message, `deckle/core/project_io.py:153-177`:

```python
_JSON_SHAPE_NAMES = {
    type(None): "null",
    bool: "a true/false value",
    int: "a number",
    float: "a number",
    str: "a piece of text",
    list: "a list",
    dict: "an object",
}


def _shape_of(value: Any) -> str:
    """Name a JSON value's type the way the file's author would recognise it.
    ...
    """
```

### Profiles — `deckle/core/profiles.py`

`version` is a required field, `deckle/core/profiles.py:44`:

```python
    version: int
```

`load`, `deckle/core/profiles.py:144-160`:

```python
        path = _profile_path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {field.name for field in dataclasses.fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        check_values(cls, kwargs, subject="printer profile field")
        # Every list back to a tuple, not just `imageable_area_pt`. ...
        kwargs = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in kwargs.items()
        }
        return cls(**kwargs)
```

Both `BUILTIN_PRESETS` entries carry `version=1`
(`deckle/core/profiles.py:183`, `:193`). There is no module-level version
constant.

### Sessions — `deckle/core/print_session.py`

`load`, `deckle/core/print_session.py:458-496`, in the order the checks run:

```python
        path = _state_dir() / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _check_state(session_id, data, plan)
        state = _SessionState.from_json(data)

        if state.version != STATE_VERSION:
            log_event(
                "session_version_mismatch",
                session_id=session_id,
                found=state.version,
                expected=STATE_VERSION,
            )
            raise StaleSessionError(
                session_id=session_id,
                reason="version",
                detail=(
                    f"this session was saved by a different version of Deckle "
                    f"(state format {state.version}, this build expects "
                    f"{STATE_VERSION}); start a new print run"
                ),
            )
```

and the check that pre-empts it, `deckle/core/print_session.py:246-256`:

```python
    known = {f.name for f in dataclasses.fields(_SessionState)}
    missing = sorted(known - set(data))
    if missing:
        refuse(
            "this session's saved state is missing "
            + ", ".join(repr(name) for name in missing)
        )
    try:
        check_values(_SessionState, {k: data[k] for k in known}, subject="session field")
    except StoredValueError as exc:
        refuse(str(exc))
```

`_check_state` refuses with `reason="state"`
(`deckle/core/print_session.py:239-244`).

### Every call site

`FORMAT_VERSION`: `deckle/core/project_io.py:38` (definition), `:419`
(write). No test reads the constant; `tests/test_project_cli.py:54` asserts
`data["version"] >= 1` on a saved file.

`load_project`: `deckle/cli.py:515`, `deckle/app/main.py:969`,
`tests/test_project_io.py` (many), `tests/test_atomic_writes.py:52`,
`tests/test_project_cli.py:213`, `:242`, `tests/test_hardening_io.py`,
`tests/test_autosave_recovery.py`. `grep -rn "load_project" --include="*.py" .`
returns 46 lines.

`PrinterProfile.load`: `deckle/cli.py:433`,
`deckle/app/views/print_dialog.py:61` and `:160` (as a default argument),
`tests/test_config_store_durability.py` (8 uses),
`tests/test_registration.py:73`, `:100`, `:129`,
`tests/test_printing.py:138`.

`STATE_VERSION`: `deckle/core/print_session.py:70` (definition), `:347`,
`:463-476`; `tests/test_print_session.py:504`, `:511`, `:529`, `:536`.

`_check_state`: `deckle/core/print_session.py:211` (definition), `:460`
(the only call). No test calls it directly;
`tests/test_print_session_state_validation.py` drives it through `load`.

### Existing tests over this code

- `tests/test_project_cli.py::test_a_saved_project_records_pages_layout_and_a_version`
- `tests/test_project_io.py` — the round-trip and drift suites
- `tests/test_registration.py::test_a_profile_from_an_older_build_keeps_working`
  (line ~85, writes `"version": 1` explicitly)
- `tests/test_registration.py::test_a_profile_from_a_newer_build_loads_instead_of_raising`
  (line 106, writes `"version": 1` plus an unknown key
  `skew_correction_deg`) — **note this test's "newer build" means an
  unknown *key*, not a higher `version`; it must keep passing unchanged**
- `tests/test_config_store_durability.py` — the whole file
- `tests/test_print_session.py::test_load_refuses_a_state_file_from_an_incompatible_version`
- `tests/test_print_session.py::test_the_version_check_runs_before_the_plan_check`
- `tests/test_print_session_state_validation.py` — 12 parametrised
  `UNDRIVEABLE` cases plus 6 others, all driven through `load`

## 3. Change

### The rule, stated once and applied three times

For every stored format, with `N` the version this build writes:

| Stored `version` | Behaviour |
|---|---|
| `== N` | load, silently |
| `> N` (an `int`) | **warn** with a named `UserWarning` subclass, then load whatever this build understands — the tolerant path that already exists |
| `< N`, missing, or not an `int` | load as version 1, silently |

**Why the newer case warns rather than refuses.** Refusing is the wrong
trade for a document. The 2026-08-04 entry records what happened the last
time a `.deckle` could not be opened across a field change: files "could not
be opened at all", which is total loss for a user who has one build. A
warning names the risk and still gives them their pages. Refusing *is* the
right trade for the print session, and that is why the session already
refuses — there the cost of continuing is a stack of ruined paper rather
than a dropped setting.

**Why the missing/older/non-int cases are silent.** A missing `version` is
the hand-edited profile the GUIDE tells people to write; a lower one is a
file this build is a strict superset of; a non-`int` is a file whose
`version` says nothing, and the *shape* checks that follow will reject it if
it is actually broken. Only one case is actionable, so only one case
speaks. The rejected alternative — raise on a non-`int` version — was
rejected because it would refuse a file this build can read perfectly on
the strength of a field it otherwise ignores.

### 3.1 `deckle/core/project_io.py`

1. **Add the warning class**, immediately after
   `UnknownLayoutFieldsWarning` (after line 306), in that class's voice:

   ```python
   class NewerProjectVersionWarning(UserWarning):
       """Emitted when a ``.deckle`` says it was written by a later format.

       Non-fatal, and the asymmetry with ``PrintSession.load`` -- which
       *refuses* a foreign state version -- is deliberate. What a session
       carries is a position in a stack of paper, and continuing against a
       misread one ruins the stack. What a project carries is a
       description, and refusing it costs the user their document: the
       2026-08-04 entry records exactly that outcome, where deleting one
       field made every older ``.deckle`` unopenable.

       So the file opens and the fields this build does not know are
       dropped -- which is what ``_layout_from_dict`` already does -- and
       this says so out loud instead of leaving it silent.
       """
   ```

2. **Read the version**, in `load_project`, immediately after the
   `isinstance(payload, dict)` guard at line 499 and before
   `stored_pages = payload["pages"]`:

   ```python
       stored_version = payload.get("version")
       if isinstance(stored_version, int) and not isinstance(stored_version, bool) \
               and stored_version > FORMAT_VERSION:
           warnings.warn(
               f"{path} was written by a newer version of Deckle (project "
               f"format {stored_version}, this build reads {FORMAT_VERSION}); "
               "anything it does not recognise has been left out",
               NewerProjectVersionWarning,
               stacklevel=2,
           )
   ```

   `bool` is excluded before `int` for the reason
   `deckle/core/schema.py:103-106` gives: `True` is an `int` and
   `True > 1` is `False`, so it would pass silently either way, but the
   check reads as a type test and must behave like one.

3. **Type-check `printer`**, replacing line 563:

   ```python
       printer = payload.get("printer")
       if printer is not None and not isinstance(printer, str):
           raise ValueError(
               f"'printer' is {_shape_of(printer)}, not a printer name, so "
               "this is not a Deckle project"
           )
   ```

   `ValueError` and `_shape_of` to match the two neighbouring messages at
   `:503-506` and `:509-512`, which is what makes the CLI's existing
   `except (OSError, ValueError)` branch
   (`deckle/cli.py:558-561`) report it as `error: cannot open <path>: ...`
   and the app's blanket handler (`deckle/app/main.py:990`) show it in the
   status bar. No new `except` anywhere.

4. **Update `load_project`'s docstring** (lines 436-486): add
   `:raises ValueError:` mention of a non-text `printer`, and a
   `:raises NewerProjectVersionWarning: never raised -- emitted through
   :mod:`warnings` when the file's ``version`` is higher than this build
   writes`, matching the existing `UnknownLayoutFieldsWarning` line at
   `:483-485`.

5. **Update the module docstring** (lines 1-19). The top-level shape block
   shows `{"version": 1, ...}`; add a sentence saying what the integer now
   means: equal loads, higher warns and loads what is understood, lower or
   missing loads as version 1.

### 3.2 `deckle/core/profiles.py`

6. **Add the constant and the warning class**, after the imports (after
   line 27):

   ```python
   # The profile format this build writes. `PrinterProfile.version` is the
   # per-file copy of it.
   PROFILE_VERSION = 1


   class NewerProfileVersionWarning(UserWarning):
       """Emitted when a stored profile says it was written by a later format.

       Non-fatal, for the reason ``load`` is already tolerant about keys: a
       calibration is the most expensive data Deckle holds -- it comes from
       printing a target and measuring it by hand -- and refusing to read
       one because of an integer would throw that away. What this build
       does not understand is dropped, and this says so.
       """
   ```

7. **Read the version in `load`.** Between the `kwargs` filter (line 148)
   and `check_values` (line 149):

   ```python
           stored_version = data.get("version")
           if isinstance(stored_version, int) and not isinstance(stored_version, bool) \
                   and stored_version > PROFILE_VERSION:
               warnings.warn(
                   f"the profile for {name!r} was written by a newer version "
                   f"of Deckle (profile format {stored_version}, this build "
                   f"reads {PROFILE_VERSION}); anything it does not recognise "
                   "has been left out",
                   NewerProfileVersionWarning,
                   stacklevel=2,
               )
           kwargs.setdefault("version", PROFILE_VERSION)
   ```

   `setdefault` **before** `check_values`, so the defaulted value is
   type-checked like any other. This is what fixes the `TypeError` on a
   hand-edited profile with no `version`: the field has no dataclass
   default and cannot be given one without changing the constructor's
   contract for callers that build a profile in code, so the *reader*
   supplies it. Add `import warnings` to the module's imports.

8. **Update `load`'s docstring** (lines 109-143). It already says
   "Tolerates field drift in both directions"; add the version sentence and
   a `:raises NewerProfileVersionWarning: never raised -- emitted through
   :mod:`warnings`` line. Note explicitly that a profile with no `version`
   is read as version 1 rather than refused, and why (GUIDE §6 tells users
   to hand-edit these files).

9. **Update the module docstring** (lines 1-16) with one sentence on the
   same rule.

### 3.3 `deckle/core/print_session.py`

10. **Reorder, and read `version` from the raw dict.** Replace
    `deckle/core/print_session.py:458-478` with:

    ```python
            path = _state_dir() / f"{session_id}.json"
            data = json.loads(path.read_text(encoding="utf-8"))

            # `version` first, and read off the raw dict rather than off a
            # constructed `_SessionState`. `_check_state` validates against
            # *this* build's field set, so a state file written by another
            # build was refused with "this session's saved state is missing
            # 'x'" -- true, and the wrong answer. What changed was Deckle,
            # not the file, and the two refusals send the user to different
            # places. The same reasoning is already recorded on
            # `STATE_VERSION` for the v1 -> v2 bump.
            if not isinstance(data, dict):
                raise StaleSessionError(
                    session_id=session_id, reason="state",
                    detail=(
                        "this session's saved state is not a Deckle session "
                        "file; start a new print run"
                    ),
                )
            stored_version = data.get("version")
            if stored_version != STATE_VERSION:
                log_event(
                    "session_version_mismatch",
                    session_id=session_id,
                    found=stored_version,
                    expected=STATE_VERSION,
                )
                found = "missing" if stored_version is None else repr(stored_version)
                raise StaleSessionError(
                    session_id=session_id,
                    reason="version",
                    detail=(
                        f"this session was saved by a different version of "
                        f"Deckle (state format {found}, this build expects "
                        f"{STATE_VERSION}); start a new print run"
                    ),
                )

            _check_state(session_id, data, plan)
            state = _SessionState.from_json(data)
    ```

    The session keeps **refusing** rather than warning, on both the newer
    and the older side, and that asymmetry with the other two formats is the
    point: what is at stake here is a position in a physical stack of
    paper, and the existing docstring already records the trade — "refusing
    costs a reprint the user was about to do anyway; continuing can cost
    the whole book".

11. **Remove `version` from `_check_state`'s remit? No.** Leave
    `_check_state` exactly as it is. It still validates `version`'s *type*
    as part of `check_values`, which is harmless once the equality check
    has already passed, and removing the field from `_SessionState` would
    change the on-disk shape.

12. **Update `load`'s docstring** (lines 434-456): the `:raises
    StaleSessionError:` clause already covers both cases; reorder its two
    sentences so the version one comes first, matching the code.

13. **Update `_check_state`'s docstring** (lines 211-238). Its opening
    paragraph says "``version`` and ``plan_hash`` were already guarded" —
    still true, but now both run *before* it. Change "were already guarded"
    to "are guarded before this runs, so anything reaching here is a file
    this build's own format" and keep the rest.

## 4. Tests

### `tests/test_project_io.py`

**`test_a_project_from_a_newer_build_warns_and_still_opens`**

- **Setup:** save a project with `save_project`, reload the JSON, set
  `"version": 99`, write it back; `load_project(path, check_sources=False)`
  inside `pytest.warns(NewerProjectVersionWarning)`.
- **Assertion in words:** the warning is emitted, its message names both
  `99` and `1`, and the returned project still has its pages and layout.
- **Unfixed tree:** `pytest.warns` fails with
  `DID NOT WARN. No warnings of type ... were emitted.`

**`test_a_project_from_an_older_or_unversioned_build_opens_silently`**

- **Setup:** parametrise over `{"version": 0}`, a payload with the
  `version` key deleted, and `{"version": "1"}`. Load each under
  `warnings.catch_warnings(record=True)` with
  `simplefilter("always")`.
- **Assertion in words:** no `NewerProjectVersionWarning` in the recorded
  list, and the project loads. Pins the "only one case speaks" rule.
- **Unfixed tree:** passes (nothing warns at all). Keep it — it is the
  guard against a later "tighten the version check" that starts refusing
  files this build reads fine.

**`test_a_project_whose_printer_is_not_text_is_refused`**

- **Setup:** parametrise over `{"a": 1}`, `[1, 2]`, `7`, `True`; write each
  into the `printer` field.
- **Assertion in words:** `load_project` raises `ValueError` whose message
  contains `'printer'` and the phrase `_shape_of` produced for that value
  (`an object`, `a list`, `a number`, `a true/false value`).
- **Unfixed tree:** no exception; `assert` fails as
  `DID NOT RAISE <class 'ValueError'>`.

**`test_a_project_with_no_printer_still_opens`**

- **Setup:** `"printer": None`, and a payload with the key absent.
- **Assertion in words:** `project.printer is None`, no exception. The
  ordinary case, pinned because the new guard is one `is not None` away
  from breaking it.
- **Unfixed tree:** passes.

### `tests/test_registration.py` (profiles live here and in `test_config_store_durability.py`)

Put the new profile tests in `tests/test_config_store_durability.py`, which
is where profile *reading* is already exercised, and follow its existing
`monkeypatch.setenv("XDG_CONFIG_HOME", ...)` +
`monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")` fixture
shape (see `tests/test_registration.py:106-112` for the pattern).

**`test_a_profile_with_no_version_is_read_as_version_one`**

- **Setup:** write a profile JSON with every field except `version`.
- **Assertion in words:** `PrinterProfile.load(name).version == 1`, and no
  exception. The GUIDE tells users to hand-edit these files; forgetting a
  bookkeeping integer must not make the printer unusable.
- **Unfixed tree:**
  `TypeError: PrinterProfile.__init__() missing 1 required positional argument: 'version'`.

**`test_a_profile_from_a_newer_format_warns_and_still_loads`**

- **Setup:** a complete profile with `"version": 99` plus one unknown key.
- **Assertion in words:** under `pytest.warns(NewerProfileVersionWarning)`,
  the profile loads and its behavioural fields (`flip_axis`,
  `back_offset_x_pt`) are the stored ones.
- **Unfixed tree:** `DID NOT WARN`.

**`test_a_profile_with_an_older_version_loads_silently`**

- **Setup:** `"version": 0`.
- **Assertion in words:** loads, no `NewerProfileVersionWarning` recorded.
- **Unfixed tree:** passes.

`tests/test_registration.py::test_a_profile_from_a_newer_build_loads_instead_of_raising`
must keep passing **unchanged** — it writes `"version": 1` with an unknown
*key*, which is a different axis and stays silent.

### `tests/test_print_session_state_validation.py`

**`test_a_state_file_from_another_build_is_a_version_problem_not_a_state_one`**

- **Setup:** the file's `saved` fixture, called as
  `saved(version=STATE_VERSION + 1, dpi=None)` — a higher version *and* a
  field this build cannot drive, so the two checks disagree about which
  refusal applies.
- **Assertion in words:** the raised `StaleSessionError` has
  `reason == "version"`, and its `detail` contains "version of Deckle" and
  not "missing".
- **Unfixed tree:** `_check_state` runs first and refuses with
  `reason == "state"`; the assertion fails as `assert 'state' == 'version'`.

**`test_a_state_file_with_no_version_is_a_version_problem`**

- **Setup:** rewrite the state file with the `version` key removed. The
  `saved` fixture builds from a `dict(original)` and `update`s it, so this
  test needs a small local variant that `pop`s the key — add a
  `remove` parameter to the fixture's `reload` helper, or write the file
  directly with `path.write_text`.
- **Assertion in words:** `reason == "version"` and the detail says
  `state format missing`.
- **Unfixed tree:** `_check_state` refuses first with
  `reason == "state"` and `"this session's saved state is missing 'version'"`.

**`test_a_state_file_that_is_not_an_object_is_refused`**

- **Setup:** write `'[1, 2, 3]'` into the state file and call
  `PrintSession.load`.
- **Assertion in words:** raises `StaleSessionError` with
  `reason == "state"` — not `TypeError`, not `AttributeError`.
- **Unfixed tree:** `_check_state`'s `set(data)` over a list of ints
  succeeds, `known - set(data)` reports every field missing, and it happens
  to refuse as `reason="state"` — **so this test passes today**. Keep it:
  after step 10 the `data.get("version")` call would raise `AttributeError`
  on a list without the `isinstance` guard, and this is the test that
  catches its omission.

### Tests that must keep passing untouched

- `tests/test_print_session.py::test_load_refuses_a_state_file_from_an_incompatible_version`
  — writes `STATE_VERSION - 1`; still a mismatch, still `reason="version"`.
- `tests/test_print_session.py::test_the_version_check_runs_before_the_plan_check`
  — unchanged in meaning; the version check now runs before *both* other
  checks. Extend its docstring to say so.
- All 18 tests in `tests/test_print_session_state_validation.py` — every
  `UNDRIVEABLE` case leaves `version` alone, so `_check_state` still gets
  to refuse them.

## 5. Acceptance

| Check | Command |
|---|---|
| The project loader reads its version | `grep -n 'payload.get("version")' deckle/core/project_io.py` |
| The project loader type-checks `printer` | `grep -n "not a printer name" deckle/core/project_io.py` |
| The profile loader reads its version | `grep -n 'data.get("version")\|PROFILE_VERSION' deckle/core/profiles.py` |
| A versionless profile now loads | `.venv/bin/python -c "import json,os,tempfile; c=tempfile.mkdtemp(); os.environ['XDG_CONFIG_HOME']=c; import deckle.core.paths as p; p.sys.platform='linux'; d=os.path.join(c,'deckle','printer_profiles'); os.makedirs(d); json.dump({'flip_axis':'long','output_face':'down','feed_edge':'top','reverse_stack':True,'imageable_area_pt':[18,18,18,18],'calibrated_at':'','calibration_version':0}, open(os.path.join(d,'X.json'),'w')); from deckle.core.profiles import PrinterProfile; assert PrinterProfile.load('X').version == 1"` |
| The session checks version before state | `.venv/bin/python -c "import inspect, deckle.core.print_session as m; src = inspect.getsource(m.PrintSession.load); assert src.index('STATE_VERSION') < src.index('_check_state('), 'version check still runs after _check_state'"` |
| A non-object state file is a `StaleSessionError` | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session_state_validation.py -k "not_an_object"` |
| New project tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_io.py tests/test_project_cli.py tests/test_atomic_writes.py` |
| New profile tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_config_store_durability.py tests/test_registration.py tests/test_printing.py` |
| Session refusals still sorted correctly | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session.py tests/test_print_session_state_validation.py` |
| Nothing else regressed on the CLI/app project paths | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_errors.py tests/test_hardening_io.py tests/test_autosave_recovery.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: rows 1, 2 and 3 return nothing. Row 4 exits
non-zero with `TypeError: PrinterProfile.__init__() missing 1 required
positional argument: 'version'`. Row 5 fails its assertion — in
`PrintSession.load`'s source, `_check_state(` is at offset 1714 and
`STATE_VERSION` at 1826, so the version check runs second. Row 6 exits 5
(no test matches `-k not_an_object` yet); it is the one row here that
already holds behaviourally on the unfixed tree, and it is in the table
because step 10's `data.get("version")` would raise `AttributeError` on a
JSON array without the `isinstance` guard.

## 6. Out of scope

- **Actually migrating anything.** No format has a second version yet;
  `FORMAT_VERSION` and `PROFILE_VERSION` both stay at `1`. **F12** lists
  `binding_edge` → `reading_direction` as "would be the first `.deckle`
  migration" — this spec is what that migration would hang off, not the
  migration.
- **B3** — the plan hash, which bumps `STATE_VERSION` to 3. Same file, same
  method. See §8.
- **B4**, **B27** — same file, different functions.
- **B21** — the CLI's and print dialog's `except` clauses around
  `PrinterProfile.load`. This spec removes one way `load` can raise
  (`TypeError` on a missing `version`); B21 is what makes the remaining
  ways report properly. They are complementary and independent.
- **B34** — `write_text_atomic`'s 0600 mode and the UNC-path risk in
  `_profile_path`. Same file (`profiles.py`), different defect.
- **The app's blanket `except Exception` in `open_project`**
  (`deckle/app/main.py:990`). It catches the new `ValueError` correctly;
  whether it should be narrowed is not this spec's.

## 7. decisions.md entry

```
## 2026-09-05 — Three formats wrote a version field and none of them read it usefully
- Symptom: `save_project` writes `{"version": 1}` and `load_project` never looks at it, so a `.deckle` from a newer build opens with its unknown fields silently dropped. `PrinterProfile.version` has no default, so a hand-edited profile that omits it -- which GUIDE section 6 tells users to write -- raises `TypeError` and the print dialog does not catch it. And `PrintSession.load` checked `version` *after* `_check_state`, so a state file from another build was refused as "this session's saved state is missing 'x'" rather than "another build wrote this". `printer` was not type-checked at all: `"printer": {"a": 1}` loaded and the dict reached the printer combo.
- Fix: One rule in three places -- equal loads, higher warns through a named `UserWarning` and loads what is understood, lower or missing or non-integer loads as version 1. The session keeps *refusing* instead of warning, and its version check moved ahead of `_check_state` and now reads the raw dict. `printer` must be text or absent, refused with the same `_shape_of` wording as its neighbours.
- Surfaces: The 2026-08-04 entry closed with "a `version` integer in a file format is not migration tolerance; it is a place to *record* a version. This one had been present since SS-01 and unused." It was still unused sixteen months of commits later. The asymmetry between the three is deliberate and worth stating: a project is a description and refusing it costs the document; a session is a position in a physical stack and continuing costs the paper.
- Watch: Check order is behaviour. Two guards that both fire on the same file give the user whichever message runs first, and the specific one has to win -- `_check_state` was right about the field being absent and wrong about why.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B3, B4, B27 and B28 all edit
  `deckle/core/print_session.py`, and **B3 and B28 both edit
  `PrintSession.load`** — B3 the plan-hash comparison at the tail, B28 the
  version check at the head. Recommended order: **B4 → B3 → B28 → B27.**
  If B3 lands first, `STATE_VERSION` is `3` and step 10's snippet is
  unchanged; if it has not, it is `2` and still unchanged. Only the diff
  context moves.
- **`bool` is a subclass of `int`.** Both new version checks must exclude
  it explicitly, for the reason `deckle/core/schema.py:103-106` records.
- **`stacklevel=2` on both new `warnings.warn` calls**, matching the two
  existing ones at `project_io.py:370` and `:548`. Without it the warning
  points at Deckle rather than at the caller, which is the complaint
  `deckle/cli.py:512-514` already records about advisory rendering.
- **`profiles.py` currently imports no `warnings`.** Add it, and keep the
  import block's existing order (stdlib, then `deckle.core`).
- **`kwargs.setdefault("version", PROFILE_VERSION)` goes before
  `check_values`.** `setdefault` does nothing when the key is present, so
  the ordering only decides one thing: whether the value this reader
  supplies is itself type-checked. It should be — every other value in that
  dict is.
- **`tests/test_registration.py::test_a_profile_from_a_newer_build_loads_instead_of_raising`
  is about an unknown *key*, not a higher `version`.** Its payload has
  `"version": 1`. It must not start warning; if it does, the version check
  is reading the wrong thing.
- **`tests/test_print_session_state_validation.py`'s `saved` fixture builds
  its payload with `dict(original)` + `update`**, so it can only *set*
  keys, not remove them. A test for a missing `version` needs a different
  path to disk.
- **`_check_state`'s `refuse()` closure logs `session_state_invalid`.** The
  new non-dict guard in `load` does not go through it, so if you want that
  event logged for a non-object file, call `log_event` explicitly.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
