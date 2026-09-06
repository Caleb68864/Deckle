# B21 — Tell a corrupt printer profile apart from a missing one, and stop naming a wizard that does not exist

**Roadmap item:** `docs/ROADMAP.md` B21
**Depends on:** —
**Blocks:** B22
**Size:** S
**Decision needed first:** none

---

## 1. Context

Two defects around the same three lines of code, on opposite sides of the
app.

**a. The CLI reports a corrupt profile as a missing one.** `_resolve_profile`
catches `(OSError, ValueError, KeyError, TypeError)` in a single `except`
whose comment says "No saved profile, or one that cannot be read". Those are
not the same thing to the person at the keyboard. A profile that *is* saved
but has, say, `"flip_axis": "diagonal"` in it — a real hand-edit, since
GUIDE §6 tells users to hand-edit these files — produces:

```
error: no printer profile 'mine'. Built-in profiles: generic_face_down_reversed,
generic_face_up_in_order. Calibrate a printer in the desktop app to save one
under its own name.
```

That is false on both counts: the profile exists, and the remedy offered is
not the one. `deckle.core.schema.StoredValueError` had already been written
to produce exactly the right message —
`printer profile field 'flip_axis' is 'diagonal', but this build of Deckle
accepts one of 'long', 'short'` — and this `except` throws it away.

**b. The print dialog crashes on the same file.** `resolve_profile` catches
`(FileNotFoundError, OSError)` only. `StoredValueError` is a `ValueError`,
so it escapes into `PrintDialog.start_print` and `_offer_resume`, neither of
which has a `try`. The user gets a traceback where the CLI got a (wrong)
message.

**c. The message names a feature that does not exist.** "Calibrate a printer
in the desktop app to save one under its own name" — nothing in `deckle/`
ever calls `PrinterProfile.save`. The calibration wizard is **F2**, unbuilt;
the profile editor is **F1**, unbuilt. Today the only way to get a saved
profile is to write the JSON by hand, which GUIDE §6 documents and this
message does not mention.

Verified on the tree at `08e7f49`:

```bash
python - <<'EOF'
import json, os, tempfile
os.environ["XDG_CONFIG_HOME"] = cfg = tempfile.mkdtemp()
import deckle.core.paths as paths; paths.sys.platform = "linux"
d = os.path.join(cfg, "deckle", "printer_profiles"); os.makedirs(d)
json.dump({"version": 1, "flip_axis": "diagonal", "output_face": "down",
           "feed_edge": "top", "reverse_stack": True,
           "imageable_area_pt": [18, 18, 18, 18], "calibrated_at": "",
           "calibration_version": 0},
          open(os.path.join(d, "mine.json"), "w"))

from deckle.cli import _resolve_profile
print("CLI  ->", _resolve_profile("mine"))

from deckle.app.views.print_dialog import resolve_profile
try:
    print("GUI  ->", resolve_profile("mine"))
except Exception as exc:
    print("GUI  -> RAISED", type(exc).__name__, exc)
EOF
```

Current output:

```
error: no printer profile 'mine'. Built-in profiles: generic_face_down_reversed, generic_face_up_in_order. Calibrate a printer in the desktop app to save one under its own name.
CLI  -> None
GUI  -> RAISED StoredValueError printer profile field 'flip_axis' is 'diagonal', but this build of Deckle accepts one of 'long', 'short'
```

Why this matters on paper: `flip_axis` decides whether every back side is
rotated 180°. A user whose profile is silently ignored gets the built-in
preset's answer instead of their own calibrated one, and on a printer that
disagrees with the preset, **every back side prints upside down** — the
exact failure `deckle/core/schema.py:17-20` was written to stop.

## 2. Current code

`deckle/cli.py:420-448`:

```python
def _resolve_profile(name: str):
    """The printer profile called ``name``: saved first, then built-in.

    A saved profile wins because it came from a calibration run against
    that actual printer, and a built-in preset is a generic stand-in.
    Names are printer names, which is how the desktop app stores them.

    :param name: the profile or printer name asked for.
    :returns: the :class:`~deckle.core.profiles.PrinterProfile`, or
        ``None`` if nothing matched -- in which case a message naming the
        alternatives has already been printed.
    """
    try:
        return PrinterProfile.load(name)
    except (OSError, ValueError, KeyError, TypeError):
        # No saved profile, or one that cannot be read. Either way the
        # built-ins are the next place to look, and a corrupt saved file
        # should not be more fatal than a missing one.
        pass
    preset = BUILTIN_PRESETS.get(name)
    if preset is not None:
        return preset
    print(
        f"error: no printer profile {name!r}. Built-in profiles: "
        f"{', '.join(sorted(BUILTIN_PRESETS))}. Calibrate a printer in the "
        "desktop app to save one under its own name.",
        file=sys.stderr,
    )
    return None
```

`deckle/app/views/print_dialog.py:59-82`:

```python
def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> PrinterProfile:
    """A profile for ``printer_name``: saved, else a builtin default.
    ...
    :returns: the saved profile, else the first builtin preset. Never
        raises for a missing profile -- a printer with no calibration is
        the normal case, not an error.
    """
    try:
        return profile_loader(printer_name)
    except (FileNotFoundError, OSError):
        pass
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    return next(iter(presets.values()))
```

and the same shape in the neighbouring helper,
`deckle/app/views/print_dialog.py:44-56`:

```python
    for name in printer_names:
        try:
            profile_loader(name)
        except (FileNotFoundError, OSError) as exc:
            # No calibration profile for this printer -- try the next one.
            # Worth recording: the fallback silently lands on
            # printer_names[0], and a user wondering why Deckle picked the
            # "wrong" printer has no other way to see this happened.
            log_exception("printer_profile_unavailable", exc, printer=name)
            continue
        else:
            return name
    return printer_names[0] if printer_names else None
```

(`FileNotFoundError` is a subclass of `OSError`, so the tuple is redundant
as written; it is kept in the rewrite because naming the missing-file case
first is what the new branch is about.)

What `PrinterProfile.load` actually raises,
`deckle/core/profiles.py:136-143`:

```python
        :raises OSError: no profile is stored for that printer.
        :raises json.JSONDecodeError: the stored file is not valid JSON.
        :raises deckle.core.schema.StoredValueError: a stored value is not
            one this build can honour. A ``ValueError``, so a caller with
            a ``ValueError`` branch already reports it cleanly.
```

`json.JSONDecodeError` is a `ValueError` subclass, so it lands in the same
branch as `StoredValueError`. `TypeError` arrives from `cls(**kwargs)` when
a required field is absent (see **B28**, which removes the `version` case);
`KeyError` is not reachable from `load` today but is caught defensively.

The config directory the new message must name,
`deckle/core/profiles.py:163-175`:

```python
def _config_dir() -> Path:
    """The OS-appropriate config directory for Deckle's printer profiles.
    ...
    """
    return config_dir("printer_profiles")


def _profile_path(name: str) -> Path:
    return _config_dir() / f"{name}.json"
```

### Every call site

`_resolve_profile` (CLI): `deckle/cli.py:420` (definition),
`deckle/cli.py:987` (inside `_cmd_export`'s `--pass` branch). That is the
only caller.

`resolve_profile` (dialog): `deckle/app/views/print_dialog.py:59`
(definition), called at `deckle/app/views/print_dialog.py:249` from
`PrintDialog._resolve_profile` (defined at `:248`), which is itself called
at `deckle/app/views/print_dialog.py:266` (`start_print`) and `:289`
(`_offer_resume`). Also imported by `tests/test_print_dialog.py` and
`tests/test_ui_surface.py`.

`select_preselected_printer`: `deckle/app/views/print_dialog.py:27`
(definition), called at `deckle/app/views/print_dialog.py:211` inside
`PrintDialog`'s printer-combo setup, and at
`tests/test_print_dialog.py:181` and `:192`.

`config_dir`: `deckle/core/paths.py:63` (definition),
`deckle/core/profiles.py:171`, `deckle/core/recent.py`,
`tests/test_app_directories.py`.

### Existing tests over this code

- `tests/test_cli_passes.py::test_an_unknown_profile_names_the_ones_that_exist`
  — asserts the message contains the name asked for and
  `generic_face_down_reversed`. **Does not assert the "Calibrate" sentence**,
  so the wording is free to change.
- `tests/test_cli_passes.py::test_a_pass_without_a_profile_is_refused`
- `tests/test_config_store_durability.py` — 8 `PrinterProfile.load` cases
  including corrupt values; asserts on the exception, not on either
  resolver
- `tests/test_print_dialog.py` — drives `resolve_profile` and
  `select_preselected_printer` with injected loaders
- `tests/test_registration.py::test_a_profile_from_an_older_build_keeps_working`,
  `::test_a_profile_from_a_newer_build_loads_instead_of_raising`

No test exercises either resolver against a profile file that exists and is
corrupt. That is the gap.

## 3. Change

### The rule

`FileNotFoundError` means **there is no saved profile**: fall back to a
built-in, silently, because a printer with no calibration is the normal
case. Anything else means **there is a saved profile and it cannot be
used**: say so, naming the file and the reason, and do **not** substitute a
built-in behind the user's back — a wrong `flip_axis` is a stack of
upside-down backs, and quietly using a generic preset instead of the
calibration the user measured is how that happens without anyone being
told.

The CLI stops (returns `None`, exit 1). The dialog cannot stop — it returns
a `PrinterProfile` by contract and its callers have no `try` — so it falls
back to the built-in **and logs**, which is what
`select_preselected_printer` already does for its own fallback and for the
same stated reason ("a user wondering why Deckle picked the 'wrong' printer
has no other way to see this happened").

Rejected: making the dialog raise. `resolve_profile`'s docstring promises
"Never raises for a missing profile", its two callers are Qt slots with no
error path, and adding one is F1's job when the profile picker exists.

Rejected: making the CLI fall back silently like the dialog. The CLI is
what a script drives, and a script that prints a book with the wrong flip
axis because a file it named was unreadable is the worst available outcome.

### The message

One string, defined once in `deckle/cli.py` and reused, so the CLI and any
future caller cannot drift:

```python
PROFILE_HELP = (
    "Saved profiles are JSON files under {directory}, one per printer, "
    "named after the printer. There is no calibration wizard yet -- see "
    "docs/GUIDE.md section 6 for the fields and what they mean."
)
```

used as `PROFILE_HELP.format(directory=config_dir("printer_profiles"))`.

The "Calibrate a printer in the desktop app to save one under its own name"
sentence is deleted outright. **F1** (the profile picker and editor) is what
makes a version of that sentence true; when it lands it supersedes this
wording, and the `PROFILE_HELP` constant is the single place to change.
Note that in a comment above the constant.

### Steps

1. **`deckle/cli.py`** — add `config_dir` to the existing `paths` import at
   line 36:

   ```python
   from deckle.core.paths import atomic_output, config_dir, write_text_atomic
   ```

2. **`deckle/cli.py`** — define the shared help string immediately above
   `_resolve_profile` (before line 420):

   ```python
   # Where a saved profile comes from, said once. There is no wizard and no
   # editor: F1 adds them, and when it does this sentence is the one place
   # that has to change. Until then the honest answer is "a text editor and
   # GUIDE section 6", which is what the GUIDE already documents.
   PROFILE_HELP = (
       "Saved profiles are JSON files under {directory}, one per printer, "
       "named after the printer. There is no calibration wizard yet -- see "
       "docs/GUIDE.md section 6 for the fields and what they mean."
   )
   ```

3. **`deckle/cli.py`** — rewrite `_resolve_profile` (lines 420-448):

   ```python
   def _resolve_profile(name: str):
       """The printer profile called ``name``: saved first, then built-in.

       A saved profile wins because it came from a calibration run against
       that actual printer, and a built-in preset is a generic stand-in.
       Names are printer names, which is how the desktop app stores them.

       **A profile that is missing and a profile that is broken are
       different answers.** They used to share one ``except``, so a
       hand-edited file with ``"flip_axis": "diagonal"`` in it was reported
       as "no printer profile 'mine'" -- false, and it sent the user to
       create a file that already exists. ``flip_axis`` decides whether
       every back side is turned a half turn, so falling through to a
       generic preset there is not a small substitution: on a printer the
       preset disagrees with, every back prints upside down.

       :param name: the profile or printer name asked for.
       :returns: the :class:`~deckle.core.profiles.PrinterProfile`, or
           ``None`` if nothing matched or the saved file could not be
           used -- in which case a message has already been printed.
       """
       try:
           return PrinterProfile.load(name)
       except FileNotFoundError:
           # No saved profile for this name. The ordinary case: fall
           # through to the built-ins.
           pass
       except (OSError, ValueError, KeyError, TypeError) as exc:
           # A saved profile that exists and cannot be used. Never
           # substituted with a built-in: the whole reason to save one is
           # that the generic answer was wrong for this printer.
           print(
               f"error: cannot use the saved printer profile {name!r}: "
               f"{exc}. "
               + PROFILE_HELP.format(directory=config_dir("printer_profiles")),
               file=sys.stderr,
           )
           log_exception("printer_profile_unreadable", exc, printer=name)
           return None
       preset = BUILTIN_PRESETS.get(name)
       if preset is not None:
           return preset
       print(
           f"error: no printer profile {name!r}. Built-in profiles: "
           f"{', '.join(sorted(BUILTIN_PRESETS))}. "
           + PROFILE_HELP.format(directory=config_dir("printer_profiles")),
           file=sys.stderr,
       )
       return None
   ```

   `log_exception` is already imported at `deckle/cli.py:29`.

   Note the ordering: `except FileNotFoundError` **must** come before
   `except OSError`, since it is a subclass.

4. **`deckle/app/views/print_dialog.py`** — rewrite `resolve_profile`
   (lines 59-82):

   ```python
   def resolve_profile(
       printer_name: str,
       profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
       builtin_presets: dict[str, PrinterProfile] | None = None,
   ) -> PrinterProfile:
       """A profile for ``printer_name``: saved, else a builtin default.

       Until calibration (SS-13) exists, this is the only way a print run
       ever gets a ``PrinterProfile`` without a prior calibration pass.

       Two ways there is no usable saved profile, and they are recorded
       differently. **Missing** is the ordinary case -- most printers have
       never been calibrated -- and is silent. **Present but unreadable**
       is not: the file was written on purpose, and the fallback replaces
       the user's measured ``flip_axis`` with a generic one, which on a
       disagreeing printer turns every back side upside down. That used to
       escape as a ``StoredValueError`` (a ``ValueError``, so outside this
       ``except``) into ``start_print``, which has no ``try`` -- a
       traceback for the same file the CLI merely misreported.

       :param printer_name: the printer to load a profile for.
       :param profile_loader: how to load a saved profile.
       :param builtin_presets: the fallback presets, or ``None`` for
           ``BUILTIN_PRESETS``.
       :returns: the saved profile, else the first builtin preset. Never
           raises -- a printer with no calibration is the normal case, and
           a printer with a broken one still has to print.
       """
       try:
           return profile_loader(printer_name)
       except FileNotFoundError:
           pass
       except (OSError, ValueError, KeyError, TypeError) as exc:
           log_exception("printer_profile_unreadable", exc, printer=printer_name)
       presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
       return next(iter(presets.values()))
   ```

5. **`deckle/app/views/print_dialog.py`** — widen
   `select_preselected_printer`'s `except` the same way (lines 44-56). It
   already logs, so the only change is the exception tuple:

   ```python
           except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
   ```

   Extend its comment by one sentence: a profile that exists but cannot be
   read is not a reason to preselect that printer either, and it is worth
   the same log line.

6. **`deckle/app/views/print_dialog.py`** — the module docstring (lines
   1-15) does not mention profile resolution. No change.

7. **`docs/GUIDE.md` §6** — the "The printer profile" subsection already
   says profiles are "persisted per printer name as JSON under the OS
   config directory (`%APPDATA%\Deckle\printer_profiles` on Windows), so a
   hand-edited one survives." Add the Linux and macOS paths beside it, so
   the message's `{directory}` and the GUIDE agree on all three platforms:
   `~/.config/deckle/printer_profiles` (or `$XDG_CONFIG_HOME/deckle/...`)
   and `~/Library/Application Support/Deckle/printer_profiles`.

## 4. Tests

### `tests/test_cli_passes.py`

**`test_a_corrupt_saved_profile_is_not_reported_as_a_missing_one`**

- **File / function:** `tests/test_cli_passes.py::test_a_corrupt_saved_profile_is_not_reported_as_a_missing_one`
- **Setup:** `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))`;
  `monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")` (the
  pattern `tests/test_registration.py:106-108` uses); write
  `tmp_path/"deckle"/"printer_profiles"/"mine.json"` with a complete
  profile whose `flip_axis` is `"diagonal"`; a 4-page numbered source from
  the file's own `_numbered_source`; call
  `main(["export", src, "-o", out, "--pass", "back", "--profile", "mine"])`
  and read `capsys`.
- **Assertion in words:** exit code 1; stderr contains `"mine"`,
  `"flip_axis"` and `"'long', 'short'"` (the `StoredValueError` text); and
  stderr does **not** contain `"no printer profile"`.
- **Unfixed tree:** stderr reads
  `error: no printer profile 'mine'. Built-in profiles: ...`, so
  `assert "no printer profile" not in err` fails, and so does
  `assert "flip_axis" in err`.

**`test_a_corrupt_saved_profile_does_not_silently_become_a_builtin`**

- **Setup:** as above but name the file `generic_face_down_reversed.json`,
  so a saved profile shadows a built-in of the same name.
- **Assertion in words:** exit code 1, and no PDF was written at `out`. A
  broken saved profile must not fall through to the preset it was saved to
  override.
- **Unfixed tree:** exit code 0 and the PDF exists, because the corrupt
  file falls through to `BUILTIN_PRESETS.get(name)` and finds a match.
  This is the most damaging half of the bug and nothing tests it today.

**`test_the_message_names_the_directory_and_the_guide_not_a_wizard`**

- **Setup:** `main(["export", src, "-o", out, "--pass", "back", "--profile", "no-such-printer"])`.
- **Assertion in words:** stderr contains `"printer_profiles"` and
  `"GUIDE"`, and does not contain `"Calibrate"`.
- **Unfixed tree:** fails on `assert "Calibrate" not in err`.

`tests/test_cli_passes.py::test_an_unknown_profile_names_the_ones_that_exist`
must keep passing unchanged — it asserts the name and
`generic_face_down_reversed` are both present, which the new message
preserves.

### `tests/test_print_dialog.py`

**`test_a_corrupt_profile_falls_back_instead_of_raising`**

- **Setup:** a `profile_loader` that raises
  `StoredValueError("printer profile field 'flip_axis' is 'diagonal', ...")`;
  call `resolve_profile("P", profile_loader=loader, builtin_presets=BUILTIN_PRESETS)`.
- **Assertion in words:** returns the first built-in preset; no exception.
- **Unfixed tree:** `StoredValueError` propagates out of the call; the test
  errors rather than failing.

**`test_a_corrupt_profile_is_recorded_in_the_diagnostic_log`**

- **Setup:** as above, with `monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path))`
  (the env var `tests/test_hardening_platform.py:113` uses) and the same
  raising loader.
- **Assertion in words:** the diagnostics log file contains
  `printer_profile_unreadable` and the printer name. The fallback is the
  only silent substitution left in the path, and the log is where a user
  wondering why their calibration did nothing can find out.
- **Unfixed tree:** exception propagates before anything is logged.

**`test_a_missing_profile_falls_back_without_a_log_line`**

- **Setup:** a loader raising `FileNotFoundError`.
- **Assertion in words:** returns the built-in, and
  `printer_profile_unreadable` is **not** in the log — the ordinary case
  stays silent.
- **Unfixed tree:** passes. Keep it; it is the guard against "widen the
  except" turning into "log every printer that was never calibrated".

**`test_preselection_skips_a_printer_whose_profile_is_corrupt`**

- **Setup:** `select_preselected_printer(["A", "B"], loader)` where the
  loader raises `StoredValueError` for `"A"` and returns a profile for
  `"B"`.
- **Assertion in words:** returns `"B"`.
- **Unfixed tree:** `StoredValueError` propagates out of the loop; the test
  errors.

## 5. Acceptance

| Check | Command |
|---|---|
| The wizard sentence is gone | `! grep -rn "Calibrate a printer in the" --include="*.py" deckle/` |
| The CLI message names the directory | `grep -n "printer_profiles" deckle/cli.py` |
| Both resolvers separate the missing case | `test "$(grep -rn "except FileNotFoundError:" deckle/cli.py deckle/app/views/print_dialog.py \| wc -l)" -eq 2` |
| The dialog catches `ValueError` | `grep -n "ValueError" deckle/app/views/print_dialog.py` |
| A corrupt profile no longer escapes the dialog | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py -k "corrupt or missing_profile or preselection_skips"` |
| The CLI reports it as a corrupt profile | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_passes.py -k "corrupt or names_the_directory"` |
| The unknown-profile message still lists the built-ins | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_passes.py` |
| Profile loading itself is unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_config_store_durability.py tests/test_registration.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: row 1's grep returns **1** hit
(`deckle/cli.py:444`) and must return none — note the sentence is split
across two source lines, so grep for `Calibrate a printer in the` and not
for the whole phrase. Rows 2, 3 and 4 all return nothing today; rows 2 and
4 must return at least one line afterwards and row 3 exactly two.

## 6. Out of scope

- **B22** — `--profile` without `--pass` being ignored. Same file, same
  command, and it *calls* `_resolve_profile`. Do B21 first; see §8.
- **B16** — the desktop app only ever resolving the *first* built-in
  preset. `resolve_profile`'s `next(iter(presets.values()))` is that bug,
  and it is untouched here: this spec changes which exceptions reach the
  fallback, not which preset the fallback picks. B16 is folded into **F1**.
- **F1 / F2** — the profile picker/editor and the calibration wizard. This
  spec's `PROFILE_HELP` constant is written to be the single place F1
  changes.
- **B28** — reading `version` in `PrinterProfile.load`. It removes the
  `TypeError`-on-missing-`version` case from the set of exceptions this
  spec routes; the two are independent and either order works.
- **D3** — GUIDE §6's "two built-in generic presets" claim. Step 7 adds
  platform paths to that section and nothing else.
- **The diagnostics log's own directory.** `log_exception` already handles
  its own failures.

## 7. decisions.md entry

```
## 2026-09-05 — A broken printer profile was reported as an absent one, or as a traceback
- Symptom: `cli._resolve_profile` caught `OSError`, `ValueError`, `KeyError` and `TypeError` in one `except` and reported all of them as "no printer profile 'mine'" -- for a file that exists, sending the user to create it again. Worse, it then fell through to `BUILTIN_PRESETS`, so a corrupt profile saved under a built-in's own name silently printed with the generic answer the user had saved a profile to override. `print_dialog.resolve_profile` caught only `OSError`, so `StoredValueError` (a `ValueError`) escaped into `start_print`, which has no `try`.
- Fix: `FileNotFoundError` means "not calibrated" and falls back silently. Anything else means "calibrated and unusable": the CLI prints the loader's own message -- which already names the field, the value and the accepted set -- and stops; the dialog logs `printer_profile_unreadable` and falls back, because it must return a profile and its callers are Qt slots. `select_preselected_printer` got the same widening.
- Surfaces: The message told users to "calibrate a printer in the desktop app", which does not exist -- nothing in `deckle/` calls `PrinterProfile.save`. Replaced with the true answer: a JSON file under `config_dir("printer_profiles")` and GUIDE section 6. F1 supersedes it, from one constant.
- Watch: `flip_axis` decides whether every back side is turned a half turn. A fallback that substitutes a generic value for a measured one is not a small silent default -- it is a whole stack printed upside down. `schema.StoredValueError` was written to say exactly that and a bare `except` was discarding it.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B21, B22, B23, B24 and B25 all edit `deckle/cli.py`.
  Recommended order: **B25 → B23 → B21 → B24 → B22.** B21 comes before B22
  because B22 adds a second call to `_resolve_profile`, and before B24
  because B24 decides whether `--profile` is reported as an ignored layout
  flag. B25 and B23 are disjoint from all three.
- **`FileNotFoundError` is a subclass of `OSError`.** Its `except` must come
  first in both files or it is unreachable.
- **`json.JSONDecodeError` is a subclass of `ValueError`**, and
  `StoredValueError` is too. Both land in the "present but unusable" branch,
  which is correct — a profile that is not valid JSON is a file that exists
  and cannot be used.
- **`resolve_profile` must not start raising.** Its two callers
  (`PrintDialog.start_print` at `print_dialog.py:266` and `_offer_resume`
  at `:288`) are Qt slots with no `try`, which is the whole reason (b) is a
  bug.
- **`tests/test_print_dialog.py` injects `profile_loader`**, so tests there
  do not need a real config directory. `tests/test_cli_passes.py` calls
  `main()` in-process, so its tests **do** need
  `monkeypatch.setenv("XDG_CONFIG_HOME", ...)` *and*
  `monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")` — the
  platform ladder is consulted at call time, and without pinning the
  platform the test only passes on Linux.
- **`tests/test_cli_paper.py` and `tests/test_project_cli.py` shell out to
  a subprocess**, while `tests/test_cli_passes.py` calls `main()` directly.
  New tests here should follow the file they live in.
- **The message is asserted by substring in two places.** Changing it later
  means grepping the tests, which is why it is one constant.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
