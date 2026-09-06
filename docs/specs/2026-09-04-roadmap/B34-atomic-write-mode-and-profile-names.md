# B34 — An atomic save must not change the file's permissions, and a printer name must not become a path

**Roadmap item:** `docs/ROADMAP.md` B34
**Depends on:** —
**Blocks:** F1 (the profile editor is the first thing that will call `PrinterProfile.save` in anger)
**Size:** S
**Decision needed first:** none

---

## 1. Context

### 1a. Every atomic write tightens its target to owner-only

`write_text_atomic` is the one implementation of "rewrite a small document
without destroying the previous generation", and five stores go through it:

| Caller | What it writes |
|---|---|
| `deckle/cli.py:1116` | `deckle schedule -o schedule.txt` — a file the **user named, in the user's own folder** |
| `deckle/core/project_io.py:426` | `.deckle` projects and `.deckle.autosave` |
| `deckle/core/profiles.py:106` | printer profiles |
| `deckle/core/recent.py:76` | the recent-projects list |
| `deckle/core/print_session.py:417` | print-session resume state |

It creates its scratch file with `tempfile.mkstemp`, which is `0600` by
construction and correct for a scratch file — and then renames that file
*over the target*. So the permissions of every one of those files become
`0600` on every save, whatever they were before.

The concrete failure: a user runs `deckle schedule book.pdf -o schedule.txt`
in a shared project folder and `chmod 664 schedule.txt` so a collaborator on
the same machine can read it. The next `deckle schedule` run silently makes
it owner-only again. Same for a `.deckle` shared through a group-writable
directory. Nothing reports it, because nothing failed: the write succeeded
and the content is right.

The general statement of the bug: **making a write atomic changed the
answer to "what permissions does this file have", and it should not have.**

### 1b. There is no directory fsync after the rename

`write_text_atomic` fsyncs the *file* before the rename, with a comment
saying why (`deckle/core/paths.py:260-263`: ordering, so a crash cannot
leave the target pointing at a file of zeros). It does not fsync the
*directory* after it. On most Unix filesystems the directory entry the
rename creates is metadata that can still be in a write cache; a crash in
that window leaves the directory pointing at the old inode, which undoes the
guarantee the file's own fsync was taken for. This matters most for the one
store whose entire purpose is surviving a crash — the print session
(`deckle/core/print_session.py:417`) — and `docs/ROADMAP.md` B27 records
that its state directory is already the wrong one.

### 1c. A printer name is used as a filename, raw

`_profile_path` is one line:

```python
def _profile_path(name: str) -> Path:
    return _config_dir() / f"{name}.json"
```

Printer names on Windows routinely contain path separators. `\\server\printer`
is what a shared queue is called. **Verified** — the profile does not merely
land in an odd place, it leaves the config directory entirely:

```bash
.venv/bin/python -c "
from pathlib import PureWindowsPath
b = PureWindowsPath(r'C:\Users\x\AppData\Roaming\Deckle\printer_profiles')
print(b / (r'\\\\server\printer' + '.json'))"
```

```
\\server\printer.json\
```

`Path.__truediv__` with a UNC path discards the base entirely, so on Windows
`PrinterProfile.save(r"\\server\printer")` tries to write to the root of a
network share, and `load` looks for it there. A forward slash is milder and
still wrong: `HP/LaserJet` becomes `printer_profiles/HP/LaserJet.json`, a
subdirectory that `save` happily creates with
`path.parent.mkdir(parents=True, exist_ok=True)` (`deckle/core/profiles.py:101`)
and that scatters one profile per manufacturer through the config tree.

Nothing else about the name survives either. A profile file is named for a
printer and there is no record *inside* it of which printer that is, so once
the filename is encoded at all, the human-readable name is gone.

**Migration surface: none.** `PrinterProfile.save` has no caller anywhere in
`deckle/` — confirmed by `grep -rn "PrinterProfile" --include='*.py' deckle/`,
which finds only type annotations and `PrinterProfile.load`; the roadmap
records the same under F1 ("the only writer of a profile today is a human
with a text editor"). And a human with a text editor cannot have created a
filename containing `/` on POSIX. So there is no existing file whose name
this change makes unreachable.

## 2. Current code

### 1a and 1b — `deckle/core/paths.py:250-272`

```python
    target = Path(path)
    directory = target.parent if str(target.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            # Ordering, not just durability: without this the rename can
            # reach the platter ahead of the content it is renaming, and a
            # crash leaves the target pointing at a file of zeros.
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # Best-effort: the write already failed, and failing to clean up
        # after it must not replace the error that explains why.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
```

`deckle/core/paths.py:30-36` — the module's imports, which gain `stat`:

```python
import contextlib
import os
import sys
import tempfile
from pathlib import Path
```

### 1c — `deckle/core/profiles.py:163-175`

```python
def _config_dir() -> Path:
    """The OS-appropriate config directory for Deckle's printer profiles.
    ...
    """
    return config_dir("printer_profiles")


def _profile_path(name: str) -> Path:
    return _config_dir() / f"{name}.json"
```

and its two callers, `deckle/core/profiles.py:100-106` and `:145-146`:

```python
        path = _profile_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # No per-field conversion: `json` serialises a tuple as an array
        # already, ...
        write_text_atomic(path, json.dumps(asdict(self), indent=2))
```

```python
        path = _profile_path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
```

The load side that makes an extra JSON key free, `deckle/core/profiles.py:147-149`:

```python
        known = {field.name for field in dataclasses.fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        check_values(cls, kwargs, subject="printer profile field")
```

**Every caller of `_profile_path`:** `deckle/core/profiles.py:100`
(`save`) and `:145` (`load`). Nothing outside the module.

**Every caller of `PrinterProfile.save`:** `tests/test_printing.py:136`
(`"My Test Printer"`), `tests/test_registration.py:71` (`"Calibrated"`),
`tests/test_config_store_durability.py:176` (`"ink"`). None in `deckle/`.

**Every place that catches what `load` raises** — this constrains the design
in step 8:

- `deckle/cli.py:434` — `except (OSError, ValueError, KeyError, TypeError)`.
- `deckle/app/views/print_dialog.py:47` — `except (FileNotFoundError, OSError)`.
- `deckle/app/views/print_dialog.py:78` — `except (FileNotFoundError, OSError)`.

**Existing tests that touch this code:**

- `tests/test_atomic_writes.py:124-154` — the four direct
  `write_text_atomic` tests: replacement, unwritable directory, and no
  debris when the rename fails (which monkeypatches `os.replace`).
- `tests/test_atomic_writes.py:55-121` — the same guarantee asserted through
  `save_project`.
- `tests/test_printing.py:131-144` — asserts the profile file is at
  `tmp_path / "deckle" / "printer_profiles" / "My Test Printer.json"`. **This
  pins that an ordinary printer name is not churned** and must stay green.
- `tests/test_registration.py:67-120` — save/load round trip under
  `"Calibrated"`, plus older- and newer-build profiles read from
  `Old.json`/hand-written JSON.
- `tests/test_config_store_durability.py:160-190` — asserts the config
  directory holds exactly `["ink.json", "recent_projects.json"]` after a
  save, and that a profile round-trips **equal** to the one saved.
- `tests/test_app_directories.py:78-79` — `config_dir("printer_profiles",
  "ink.json")` composition.
- `tests/test_single_source_decisions.py:79-99` — `os.replace` is allowed
  only in `deckle/core/paths.py` and `deckle/core/export.py`.
- `tests/test_print_session_durability.py` — the session's own durability,
  whose docstring already names `write_text_atomic` as the model.

## 3. Change

### 1a — preserve the target's mode

1. **`deckle/core/paths.py`**, add `import stat` to the import block
   (alphabetical: after `sys`... no — the block is
   `contextlib, os, sys, tempfile`; insert `stat` between `os` and `sys`).

2. **`deckle/core/paths.py`**, add a private helper above
   `write_text_atomic`:

   ```python
   def _replacement_mode(target: Path) -> int:
       """The permission bits a rewrite of ``target`` should end up with.

       ``mkstemp`` creates its file ``0600``, which is right for a scratch
       file and wrong for the file that replaces the target. Renaming the
       scratch file over the target carries the scratch file's bits with it,
       so a schedule or a project a user had made group-readable became
       owner-only on the next save, silently -- **making a write atomic
       changed the answer to "what permissions does this file have", which
       is not a durability decision and should not have been one.**

       An existing target keeps exactly its own bits. A file that does not
       exist yet gets ``0o666`` masked by the process umask, which is what
       an ordinary ``open(path, "w")`` would have produced.

       Reading the umask means setting it and putting it back, because the
       C library offers no way to ask. That window is two calls wide, and
       Deckle does write from a background autosave timer as well as the
       GUI thread, so the race is real rather than theoretical -- but what
       is lost to it is a newly created file getting the wrong permission
       bits, not a torn or missing file, and every language's standard
       answer to this question has the same window.

       :param target: the file being replaced. It need not exist.
       :returns: the mode to apply to the scratch file before the rename.
       """
       try:
           return stat.S_IMODE(os.stat(target).st_mode)
       except OSError:
           current = os.umask(0)
           os.umask(current)
           return 0o666 & ~current
   ```

3. **`deckle/core/paths.py`**, in `write_text_atomic`, insert one line
   between the `with` block and `os.replace`:

   ```python
            os.chmod(tmp_name, _replacement_mode(target))
            os.replace(tmp_name, target)
   ```

   It is inside the existing `try`, so a `chmod` failure still removes the
   scratch file and re-raises.

### 1b — flush the directory entry

4. **`deckle/core/paths.py`**, add a second private helper beside the first:

   ```python
   def _fsync_directory(directory: Path) -> None:
       """Flush the directory entry a rename just created. POSIX only.

       ``os.replace`` publishes the new name, but on most Unix filesystems
       that name is directory metadata which can still be sitting in a write
       cache. A crash in that window leaves the directory pointing at the
       old inode -- undoing the guarantee the file's own ``fsync`` was taken
       for, which is the whole reason this module fsyncs at all.

       Windows offers no directory handle to sync (``os.open`` on a
       directory raises ``PermissionError``) and ``MoveFileEx`` does not
       need one, so this is a no-op there. Branched on ``os.name`` rather
       than ``sys.platform``, because
       ``tests/test_single_source_decisions.py`` reserves ``sys.platform ==``
       comparisons for the directory ladder above.

       Best effort. The bytes are already durable and the rename has already
       returned, so a failure here must not turn a completed save into an
       ``OSError`` the caller reports to the user as a lost file.

       :param directory: the directory the rename happened in.
       :returns: nothing, and raises nothing.
       """
       if os.name != "posix":
           return
       try:
           fd = os.open(str(directory), os.O_RDONLY)
       except OSError:
           return
       try:
           os.fsync(fd)
       except OSError:
           pass
       finally:
           os.close(fd)
   ```

5. **`deckle/core/paths.py`**, call it as the last statement of
   `write_text_atomic`, **after** the `try/except BaseException` block so it
   only runs on a completed rename:

   ```python
    except BaseException:
        ...
        raise
    _fsync_directory(directory)
   ```

6. **`deckle/core/paths.py`**, extend `write_text_atomic`'s docstring. After
   the paragraph about the temp file living in the target's own directory,
   add:

   ```
    **The target's permissions are preserved.** The scratch file is created
    ``0600`` by ``mkstemp`` and would otherwise carry those bits onto the
    target through the rename, so a file the user had made group-readable
    became owner-only on every save. An existing target keeps its own mode;
    a new one gets ``0o666`` masked by the umask, which is what a plain
    ``open(path, "w")`` would have given it. See :func:`_replacement_mode`.

    The directory entry is flushed after the rename on POSIX. Fsyncing the
    file and not the directory leaves the *name* in a write cache, so the
    crash the file's own fsync was taken against can still find the
    directory pointing at the old inode. See :func:`_fsync_directory`.
   ```

   and add to the `:raises OSError:` entry:

   ```
    ... The directory flush after the rename is best-effort and never
    raises: the write has already succeeded by then.
   ```

### 1c — encode the printer name, and record it inside the file

**Chosen: percent-encode every byte outside a small safe set, uppercase
hex.** Reversible, unambiguous (`%` is itself encoded, so a `%XX` sequence
can only be an escape), and readable — `My Test Printer` is unchanged, so no
existing file is renamed and `tests/test_printing.py:141` stays green.
Rejected: hashing the name, which is unambiguous and unreadable, and would
make the config directory a list of hex strings; and stripping unsafe
characters, which is readable and *not* injective — `HP/1` and `HP1` would
collide and one printer's calibration would silently load for another.

7. **`deckle/core/profiles.py`**, add above `_profile_path`:

   ```python
   _SAFE_PROFILE_NAME_CHARS = frozenset(
       "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ._-"
   )
   """Characters a printer name may keep in its filename.

   Deliberately small and ASCII. Space is in it because printer names are
   full of spaces and ``HP%20LaserJet%201020.json`` would be worse to read
   than the problem it solves; ``%`` is deliberately out, so a ``%XX`` in a
   stored filename can only ever be an escape this module wrote.
   """

   _WINDOWS_DEVICE_NAMES = frozenset(
       ["CON", "PRN", "AUX", "NUL"]
       + [f"COM{digit}" for digit in "123456789"]
       + [f"LPT{digit}" for digit in "123456789"]
   )
   """Names Windows resolves to a device whatever suffix follows them.

   ``CON.json`` is the console, not a file. Far-fetched for a printer name
   and three lines to rule out.
   """


   def _encode_profile_name(name: str) -> str:
       """``name`` as a filename stem: percent-encoded, and still readable.

       A printer name is not a filename and Windows printer names are
       routinely paths. ``\\\\server\\printer`` used as one does not merely
       land somewhere odd -- ``Path.__truediv__`` discards the base when the
       right-hand side is a UNC path, so the profile was written to the root
       of a network share and read back from there. A forward slash is
       milder and still wrong: ``save`` creates the subdirectory, and one
       profile per manufacturer scatters through the config tree.

       Percent-encoding rather than stripping, because stripping is not
       injective: ``HP/1`` and ``HP1`` would collide, and one printer's
       calibration would load for another with nothing to show for it. Every
       byte outside :data:`_SAFE_PROFILE_NAME_CHARS` becomes ``%XX`` over the
       name's UTF-8, so the mapping is reversible and an ordinary name such
       as ``My Test Printer`` is unchanged -- which is also why no profile
       already on disk is renamed by this.

       :param name: the printer name, as the operating system spells it.
       :returns: a single path component, safe on Windows and POSIX.
       """
       encoded = "".join(
           chr(byte) if chr(byte) in _SAFE_PROFILE_NAME_CHARS else f"%{byte:02X}"
           for byte in name.encode("utf-8")
       )
       # Windows silently strips trailing dots and spaces from a filename,
       # so "HP " and "HP" would resolve to one file and one printer's
       # calibration would overwrite the other's. Encoding the whole
       # trailing run also disposes of "." and "..".
       tail = ""
       while encoded and encoded[-1] in " .":
           tail = f"%{ord(encoded[-1]):02X}" + tail
           encoded = encoded[:-1]
       encoded += tail
       if encoded.upper() in _WINDOWS_DEVICE_NAMES:
           encoded = f"%{ord(encoded[0]):02X}" + encoded[1:]
       return encoded
   ```

8. **`deckle/core/profiles.py`**, `_profile_path` becomes:

   ```python
   def _profile_path(name: str) -> Path:
       return _config_dir() / f"{_encode_profile_name(name)}.json"
   ```

   **`_encode_profile_name` must not raise, for any input.** The empty name
   encodes to the empty string, giving `.json` — a legal filename on both
   platforms, and unambiguous, because no other name encodes to nothing.
   Raising `ValueError` on an empty name was considered and rejected:
   `deckle/app/views/print_dialog.py:47,78` catch only
   `(FileNotFoundError, OSError)`, so a new exception type out of `load`
   would crash the print dialog. That the dialog catches too narrowly is
   B21's bug; this spec must not widen it.

9. **`deckle/core/profiles.py`**, in `PrinterProfile.save`, write the
   display name into the document:

   ```python
           path = _profile_path(name)
           path.parent.mkdir(parents=True, exist_ok=True)
           # No per-field conversion: `json` serialises a tuple as an array
           # already, so naming `imageable_area_pt` here did nothing the
           # encoder was not doing anyway -- and naming one field on the way
           # out is what made it look correct to name one field on the way in.
           payload = asdict(self)
           # The filename is percent-encoded, so a shared Windows queue is
           # stored as `%5C%5Cserver%5Cprinter.json`. The name as the printer
           # actually spells it goes inside the document, so a human opening
           # the file -- or a `deckle profile list` -- does not have to decode
           # a filename to find out whose calibration this is. `load` drops
           # every key it does not recognise, so it costs nothing coming back.
           payload["printer_name"] = name
           write_text_atomic(path, json.dumps(payload, indent=2))
   ```

   **Chosen: an extra JSON key, not a dataclass field.** Adding
   `printer_name` to `PrinterProfile` would give the object and the filename
   two places to disagree, force `dataclasses.replace` inside `save`, put a
   free-text field through `schema.check_values`, and change equality — which
   `tests/test_config_store_durability.py:173-177` asserts across a save and
   load. The load side already filters to known field names
   (`deckle/core/profiles.py:147-148`), so an extra key is dropped and the
   round trip stays exactly equal.

10. **`deckle/core/profiles.py`**, in `save`'s docstring, add:

    ```
     The file is named for the printer with unsafe characters
     percent-encoded (see :func:`_encode_profile_name`), and the name as
     given is also written into the document under ``printer_name`` --
     a key :meth:`load` ignores, so it is there for a person or a future
     ``deckle profile list`` rather than for this class.
    ```

    and in `load`'s docstring, after the "Tolerates field drift" paragraph:

    ```
     ``printer_name``, which :meth:`save` writes for a human's benefit, is
     one of the keys dropped here -- the name is an argument to this method,
     not a property of the profile.
    ```

## 4. Tests

### 1a and 1b

**File:** `tests/test_atomic_writes.py`, appended after the existing helper
tests (`:124-154`). Needs `import stat` added at `:24`.

All four permission tests are POSIX-only:
`@pytest.mark.skipif(os.name != "posix", reason="Windows has no POSIX mode bits to preserve")`.

#### `test_a_rewrite_keeps_the_targets_existing_permissions`

- **Setup:** `write_text_atomic(path, "first")`, then
  `os.chmod(path, 0o640)`, then `write_text_atomic(path, "second")`.
- **Assertion in words:** the file's mode is still `0o640`, and its content
  is `"second"` — a save must change the bytes and nothing else.
- **Expected failure on the unfixed tree:** `assert 0o600 == 0o640`, printed
  by pytest as `assert 384 == 416`.

#### `test_a_new_file_gets_the_umask_default_not_owner_only`

- **Setup:** set the process umask to `0o022` with `os.umask`, restoring the
  previous value in a `finally`; `write_text_atomic(tmp_path / "new.json",
  "x")`.
- **Assertion in words:** the new file's mode is `0o644` — what
  `open(path, "w")` would have produced under that umask — not `0o600`.
- **Expected failure on the unfixed tree:** `assert 384 == 420`.

#### `test_the_directory_entry_is_flushed_after_the_rename`

- **Setup:** monkeypatch `os.fsync` with a recorder that appends
  `stat.S_ISDIR(os.fstat(fd).st_mode)` for each fd and then delegates. Call
  `write_text_atomic(tmp_path / "f.json", "x")`.
- **Assertion in words:** the recorded list contains both `False` (the
  file's own fsync, which already existed) and `True` (a directory fsync
  after the rename) — asserted on the real file type behind the descriptor,
  not on a call count, so it cannot pass by fsyncing the file twice.
- **Expected failure on the unfixed tree:** `assert True in [False]`.

#### `test_a_failed_directory_flush_does_not_fail_a_completed_save`

- **Setup:** monkeypatch `os.fsync` to raise `OSError(5, "Input/output
  error")` when `stat.S_ISDIR(os.fstat(fd).st_mode)` and to delegate
  otherwise. Call `write_text_atomic(path, "second")` over an existing file.
- **Assertion in words:** the call returns normally and the file contains
  `"second"` — the bytes were already durable and the rename had already
  returned, so a metadata flush cannot be what reports a lost file.
- **Expected failure on the unfixed tree:** none — it passes before and
  after, and exists so a later "make the fsync strict" change is a
  deliberate one.

Note that `test_the_helper_leaves_no_debris_when_the_rename_fails`
(`tests/test_atomic_writes.py:140-154`) monkeypatches `os.replace` to raise;
the directory flush is after the rename and never runs on that path, so that
test is unaffected. Confirm it stays green rather than assuming it.

### 1c

**File:** `tests/test_config_store_durability.py`, in its profile section
(after `:190`). Its existing fixture sets `XDG_CONFIG_HOME` and pins
`deckle.core.paths.sys.platform` to `"linux"`; reuse it, and add
`tests/test_registration.py:73-74`'s two `monkeypatch` lines where a test
needs its own directory.

#### `test_a_unc_printer_name_stays_inside_the_profile_directory`

- **Setup:** `_calibrated().save(r"\\server\printer")`.
- **Assertion in words:** the profiles directory contains exactly one entry;
  it is a file, not a directory; its name is
  `"%5C%5Cserver%5Cprinter.json"`; and `PrinterProfile.load(r"\\server\printer")`
  returns a profile equal to the one saved.
- **Expected failure on the unfixed tree:** on Linux the file is created as
  the single literal component `\\server\printer.json`, so the name
  assertion fails with
  `assert '\\\\server\\printer.json' == '%5C%5Cserver%5Cprinter.json'`. (On
  Windows the same call escapes the config directory entirely and the
  directory-listing assertion fails first. The test asserts the name so it
  is meaningful on both.)

#### `test_a_printer_name_with_a_path_separator_does_not_make_a_subdirectory`

- **Setup:** `_calibrated().save("HP/LaserJet 1020")`.
- **Assertion in words:** the profiles directory holds exactly one entry, it
  is a file named `"HP%2FLaserJet 1020.json"`, and the profile loads back
  equal.
- **Expected failure on the unfixed tree:**
  `assert [PosixPath('.../printer_profiles/HP')] == [...]` — the entry is a
  directory called `HP`, because `save` creates the parent.

#### `test_an_ordinary_printer_name_is_stored_unchanged`

The guard against churn, and the reason the safe set includes the space.

- **Setup:** `_calibrated().save("My Test Printer")`.
- **Assertion in words:** the file is exactly `"My Test Printer.json"` — a
  profile already on disk under an ordinary name is still found after this
  change.
- **Expected failure on the unfixed tree:** none. It duplicates
  `tests/test_printing.py:131-144`'s path assertion on purpose, next to the
  encoding tests, so that anyone widening or narrowing the safe set sees
  what it costs.

#### `test_two_names_windows_would_confuse_get_two_files`

- **Setup:** save one profile under `"HP"` and a different one (a distinct
  `back_offset_x_pt`) under `"HP "` — trailing space.
- **Assertion in words:** two files exist, `"HP.json"` and `"HP%20.json"`,
  and each name loads back its own profile. Windows strips a trailing space
  from a filename, so without the encoding the second save would overwrite
  the first.
- **Expected failure on the unfixed tree:** on Linux both files exist
  (`"HP.json"` and `"HP .json"`) so the *name* assertion fails —
  `assert 'HP .json' in {...}`. The overwrite itself is Windows-only, which
  is why the test asserts the encoded name rather than trying to reproduce
  the platform behaviour.

#### `test_the_display_name_is_stored_in_the_profile`

- **Setup:** `_calibrated().save(r"\\server\printer")`, then read the JSON
  file directly with `json.loads(path.read_text(encoding="utf-8"))`.
- **Assertion in words:** `data["printer_name"] == r"\\server\printer"` —
  the file says whose calibration it is, without anyone having to decode a
  filename.
- **Expected failure on the unfixed tree:** `KeyError: 'printer_name'`.

#### `test_the_stored_display_name_does_not_reach_the_loaded_profile`

- **Setup:** save under `"ink"`, then `PrinterProfile.load("ink")`.
- **Assertion in words:** the loaded profile equals the saved one and has no
  `printer_name` attribute — the extra key is for a human, and `load`'s
  known-fields filter is what keeps it out of the model.
- **Expected failure on the unfixed tree:** none; it pins the property that
  makes the extra key free, and would fail if someone later promoted it to a
  dataclass field without thinking about equality.

## 5. Acceptance

| Check | Command |
|---|---|
| The mode and fsync tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_atomic_writes.py` |
| The profile-name tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_config_store_durability.py` |
| Ordinary profile names are not churned | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_printing.py tests/test_registration.py tests/test_app_directories.py` |
| The stores that go through the helper still work | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_io.py tests/test_recent.py tests/test_print_session_durability.py tests/test_autosave_concurrency.py` |
| `os.replace` is still only in `paths` and `export` | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_single_source_decisions.py` |
| A rewrite preserves the mode, end to end | `.venv/bin/python -c "import os,stat,tempfile; from deckle.core.paths import write_text_atomic; d=tempfile.mkdtemp(); p=os.path.join(d,'f.json'); write_text_atomic(p,'a'); os.chmod(p,0o664); write_text_atomic(p,'b'); m=stat.S_IMODE(os.stat(p).st_mode); assert m==0o664, oct(m); print('OK')"` |
| No printer name can escape the profile directory | `.venv/bin/python -c "from deckle.core.profiles import _encode_profile_name as e; bad=[n for n in ('\\\\\\\\server\\\\printer','HP/LaserJet','..','.','CON','HP ','a%b') if '/' in e(n) or '\\\\' in e(n) or e(n) in ('.','..') or e(n).upper() in ('CON','PRN','AUX','NUL') or e(n)[-1:] in (' ','.')]; assert not bad, bad; print('OK')"` |
| The encoding is injective on the cases that matter | `.venv/bin/python -c "from deckle.core.profiles import _encode_profile_name as e; names=['HP','HP ','HP.','HP/1','HP1','My Test Printer','a%b','a%2Fb']; assert len({e(n) for n in names})==len(names); assert e('My Test Printer')=='My Test Printer'; print('OK')"` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two R0.3/R0.4 failures from `00-environment.md` and nothing else) |

The last two rows fail today with `ImportError: cannot import name
'_encode_profile_name'`, which is the right failure for a helper that does
not exist yet. The mode row fails today with `AssertionError: 0o600`.

## 6. Out of scope

- **`atomic_output`** (`deckle/core/paths.py:174-210`) has the identical
  `mkstemp`-0600 defect and no directory fsync, and it is what
  `deckle crop-preview` and `deckle dummy` write PNGs and PDFs through.
  It is **not** in B34 and is not fixed here. Note it in the decisions
  entry so it is a known gap rather than an oversight, and do not
  opportunistically fix it — its scratch file is handed to a third-party
  encoder, so the mode has to be applied at a different point and it
  deserves its own tests.
- **B27** — print-session state living in `tempfile.gettempdir()`, which a
  reboot wipes. A directory fsync does not help a directory that is about to
  be deleted; that is a different fix.
- **B28** — `version` fields written and never read, including profiles'.
- **B21** — `print_dialog` catching only `OSError` where the CLI catches
  `ValueError` too. Step 8 is deliberately shaped so this spec does not need
  it fixed first.
- **F1 / N14** — the profile editor and `deckle profile list/show/set`.
  `printer_name` is written for them; nothing reads it yet, and nothing
  should be built here to read it.
- Do not add a decoder (`_decode_profile_name`) with no caller. When
  `deckle profile list` needs one it will read `printer_name` out of the
  file, which is why the key is being written.

## 7. decisions.md entry

```
## 2026-09-05 — Making a write atomic quietly changed the file's permissions, and a printer name was used as a path
- Symptom: `write_text_atomic` renames a `mkstemp` scratch file over its target, and `mkstemp` creates 0600 — so every schedule, project, autosave, profile and print-session file became owner-only on every save, whatever the user had set. `chmod 664 schedule.txt` for a collaborator survived exactly until the next `deckle schedule`. Separately, `_profile_path` interpolated the printer name straight into a filename: verified with `PureWindowsPath`, `save(r"\\server\printer")` does not land somewhere odd inside the config directory, it **leaves it entirely** — `Path.__truediv__` discards the base for a UNC path, so the profile was written to the root of a network share.
- Fix: The scratch file is chmod'd to the target's own mode before the rename, or to `0o666 & ~umask` when there is no target — atomicity is a durability decision and must not also be a permissions decision. The directory entry is fsynced after the rename on POSIX, best-effort, because fsyncing the file and not the directory leaves the *name* in a write cache and the crash the file's fsync was taken against can still find the old inode. And a printer name is percent-encoded into a single path component, with the name as the printer spells it written inside the JSON under `printer_name` — a key `load` already drops, so the round trip stays exactly equal.
- Surfaces: Percent-encoding rather than stripping, because stripping is not injective: `HP/1` and `HP1` would collide and one printer's calibration would load for another with nothing on screen to say so. Space stays in the safe set so `My Test Printer.json` is unchanged and no profile already on disk is renamed — and the trailing run of dots and spaces is encoded anyway, because Windows strips those from a filename and `HP ` and `HP` would otherwise be one file. `_encode_profile_name` raises for no input at all: `print_dialog.resolve_profile` catches only `(FileNotFoundError, OSError)`, so a new `ValueError` out of `load` would have crashed the dialog.
- Watch: `PrinterProfile.save` has no caller anywhere in `deckle/` — the only writer of a profile today is a human with a text editor — which is why a filename bug this total survived. **A code path with no production caller has no production evidence; it is not low-risk, it is untested in the field.** `atomic_output` still has the same 0600 defect and no directory flush, deliberately left for its own change because its scratch file is handed to a third-party encoder and the mode has to be applied at a different point.
- Commit: (this commit)
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
- **`os.umask` has no read-only form.** Reading it means `current =
  os.umask(0); os.umask(current)`. Restore it immediately and unconditionally
  — leaking a `0` umask into a process that also writes autosaves would make
  every subsequent new file world-writable, which is a worse bug than the one
  being fixed.
- **The `chmod` must be on the scratch file, before the rename** — not on
  the target after it. Chmod-after-rename leaves a window in which the file
  exists at its final name with the wrong bits, and a reader racing the
  writer is exactly the case this function is documented against.
- **`_fsync_directory` must not be inside the `try/except BaseException`.**
  Inside it, an `OSError` from the flush would trigger the cleanup branch,
  which unlinks `tmp_name` — a path that no longer exists after a successful
  rename — and then re-raises, turning a completed save into a reported
  failure.
- **`tests/test_atomic_writes.py:140-154` monkeypatches `os.replace`.** The
  new directory flush sits after it and must not run on that path, or the
  test's "no debris" assertion sees a different failure.
- **`tests/test_single_source_decisions.py:79-99` allows `os.replace` only in
  `paths.py` and `export.py`, and reserves `sys.platform ==` comparisons for
  `paths.py`'s directory ladder.** Both new helpers live in `paths.py`, and
  `_fsync_directory` branches on `os.name` — do not reach for `sys.platform`
  out of habit.
- **On Windows `os.chmod` only toggles the read-only bit** and
  `stat.S_IMODE(os.stat(...).st_mode)` returns `0o666` or `0o444`. Preserving
  a `0o444` target means chmod-ing the scratch file read-only before the
  rename; renaming a read-only file is permitted, and the target staying
  read-only afterwards is the correct preservation. The permission tests are
  POSIX-only for this reason, not because the change is.
- **`Path("dir") / "\\\\server\\x.json"` on Windows returns
  `\\server\x.json\`, discarding `dir`.** This is the whole of 1c and it is
  not visible from a POSIX machine, where the backslashes are ordinary
  characters in a single filename. Verified with `PureWindowsPath`; do not
  "confirm it does not reproduce" on Linux and conclude it is not a bug.
- **`PrinterProfile.load` filters to known dataclass field names**
  (`deckle/core/profiles.py:147-148`). That is what makes `printer_name` free
  — and it is also what would silently swallow the key if someone later
  renamed it, with the round-trip tests still green. If `printer_name` ever
  becomes load-bearing it needs a test that reads it back.
- **`tests/test_config_store_durability.py:160-168` asserts the config
  directory contains exactly `["ink.json", "recent_projects.json"]`.** A new
  test that saves under an encoded name in the *shared* fixture directory
  will break it; give each new test its own `XDG_CONFIG_HOME`, as
  `tests/test_registration.py:73-74` does.
