# B27 — Put print-session state where a reboot cannot wipe it, and stop it raising

**Roadmap item:** `docs/ROADMAP.md` B27
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Three separate defects in one small area, all of them about the file whose
entire job is to survive a crash.

**a. The state file lives in the system temp directory.** On Linux
`/tmp` is usually `tmpfs` — RAM. A kernel panic or a power cut mid-print is
exactly the event `PrintSession` exists to survive, and it is also exactly
the event that empties `/tmp` on the way back up. `systemd-tmpfiles` clears
`/tmp` on many distributions even without tmpfs. On Windows,
`%LOCALAPPDATA%\Temp` survives a reboot but is a Disk Cleanup target.
Deckle already has a correct answer for "state that outlives a session" —
`deckle.core.paths.data_dir` — and the session log, the diagnostics log and
the recent-projects list all use it. The print session does not.

The concrete user action: printing pass 1 of a 60-sheet job, the machine
hard-locks after sheet 40, they reboot, reopen Deckle and open the print
dialog. On Linux nothing is offered to resume. They reprint 40 sheets.

**b. `_save` and `_delete_state` raise `OSError` into a caller with no
`try`.** `PrintDialog.start_print` and `PrintDialog._drive` call
`session.start()` and `session.advance()` bare. A disk that fills between
two chunks turns into a traceback, after the sheets have printed.
`_delete_state` is worse in kind: it runs on the *successful* completion of
the whole job, so an unremovable state file turns a finished book into an
exception.

**c. `list_resumable` raises on a state file with missing keys.** Its
docstring says "Never raises", and it catches `OSError` and
`json.JSONDecodeError` — but then reads `data["session_id"]`,
`data["printer_name"]`, `data["started_at"]`, `data["pass_index"]` and
`data["sheet_cursor"]` outside the `try`. One hand-edited or
partially-written file therefore hides **every** resumable session,
including the one the user is looking for. This is the same file the
docstring's own comment says must not be allowed to do that:

> one corrupt file must not hide every other resumable session

Verified on the tree at `08e7f49`:

```bash
python - <<'EOF'
import json, os, tempfile
os.environ["DECKLE_SESSION_STATE_DIR"] = d = tempfile.mkdtemp()
from deckle.core.print_session import PrintSession
# One good-looking file that is missing a key the summary reads.
open(os.path.join(d, "aaaa.json"), "w").write(json.dumps({"version": 2}))
# One perfectly resumable file.
open(os.path.join(d, "bbbb.json"), "w").write(json.dumps({
    "version": 2, "session_id": "bbbb", "printer_name": "P",
    "started_at": 0.0, "pass_index": 0, "sheet_cursor": 3,
    "sheets": [0, 1, 2], "test_first": False, "test_sheet_pending": False,
    "dpi": 300, "copies": 1}))
print(PrintSession.list_resumable())
EOF
```

Current output: `KeyError: 'session_id'`. The resumable job `bbbb` is
unreachable, and the dialog that called `list_resumable` gets an exception
rather than a list.

## 2. Current code

`deckle/core/print_session.py:101-106`:

```python
def _state_dir() -> Path:
    """The directory holding interrupted sessions' state files."""
    override = os.environ.get("DECKLE_SESSION_STATE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "deckle" / "print_sessions"
```

`deckle/core/print_session.py:402-422`:

```python
    def _save(self) -> None:
        # Through the shared writer rather than a fourth hand-rolled
        # temp-and-rename. ...
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(self._state.to_json(), indent=2))

    def _delete_state(self) -> None:
        path = self.state_path
        if path.exists():
            path.unlink()
```

`deckle/core/print_session.py:512-555` — the listing, with the `except`
that stops one line too early:

```python
        directory = _state_dir()
        if not directory.exists():
            return []
        summaries: list[SessionSummary] = []
        for path in sorted(directory.glob("*.json")):
            # There was a `path.suffix == ".tmp"` skip here. ...
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                # Skipping is right -- one corrupt file must not hide every
                # other resumable session. ...
                log_exception("session_state_unreadable", exc, path=str(path))
                continue
            summaries.append(
                SessionSummary(
                    session_id=data["session_id"],
                    printer_name=data["printer_name"],
                    started_at=data["started_at"],
                    pass_index=data["pass_index"],
                    sheet_cursor=data["sheet_cursor"],
                    state_path=str(path),
                )
            )
        return summaries
```

The `state_path` docstring that names the temp dir,
`deckle/core/print_session.py:365-373`:

```python
    @property
    def state_path(self) -> Path:
        """Where this session's state file lives.

        :returns: ``<state dir>/<session_id>.json``. The directory is
            ``DECKLE_SESSION_STATE_DIR`` when set, otherwise a
            ``deckle/print_sessions`` folder under the OS temp dir.
        """
        return _state_dir() / f"{self._state.session_id}.json"
```

The existing helper this spec adopts, `deckle/core/paths.py:78-96`:

```python
def data_dir(*parts: str) -> Path:
    """The OS-appropriate *data* directory, plus any sub-path given.

    Distinct from :func:`config_dir` on one platform only. Windows and
    macOS keep both under the same root, so the two answers are identical
    there. Linux is where the split is real: XDG separates settings a user
    might edit or copy between machines (``XDG_CONFIG_HOME``) from data an
    application accumulates (``XDG_DATA_HOME``), and the session log is
    squarely the second kind.
    ...
    """
    return _root("XDG_DATA_HOME", Path.home() / ".local" / "share").joinpath(*parts)
```

and the platform ladder it sits on, `deckle/core/paths.py:39-60`:

```python
def _root(xdg_variable: str, xdg_fallback: Path) -> Path:
    ...
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Deckle"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Deckle"
    base = os.environ.get(xdg_variable) or str(xdg_fallback)
    # Lowercase on Linux and capitalised elsewhere: each is that
    # platform's convention, not an inconsistency.
    return Path(base) / "deckle"
```

So `data_dir("print_sessions")` resolves to:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Deckle\print_sessions` (roaming, not `Temp`) |
| macOS | `~/Library/Application Support/Deckle/print_sessions` |
| Linux / other | `$XDG_DATA_HOME/deckle/print_sessions`, else `~/.local/share/deckle/print_sessions` |

Nothing is created by asking — `data_dir` creates no directories, and
`tests/test_app_directories.py::test_nothing_is_created_by_asking` pins
that. `_save` already does its own `mkdir(parents=True, exist_ok=True)`.

### Every call site

`_state_dir`:

- `deckle/core/print_session.py:101` (definition)
- `deckle/core/print_session.py:373` — `state_path`
- `deckle/core/print_session.py:458` — `PrintSession.load`
- `deckle/core/print_session.py:523` — `list_resumable`
- `tests/test_print_session.py:504`, `:508`, `:529`, `:533` — imported and
  used to reach into the state file

`_save`: `deckle/core/print_session.py:402` (definition), called at `:583`,
`:586`, `:598`, `:618`, `:623`, `:635`, `:690`.

`_delete_state`: `deckle/core/print_session.py:419` (definition), called at
`:633` only (`_advance_pass`, on the last pass).

`list_resumable`:

- `deckle/core/print_session.py:513` (definition)
- `deckle/app/views/print_dialog.py:241` — `lister = resumable_lister or self._session_cls.list_resumable`
- `tests/test_print_session.py:224`, `:240`, `:395`, `:422`
- `tests/test_print_dialog.py:156` and `tests/test_ui_surface.py:327` —
  stubbed `list_resumable` on a fake session class

`DECKLE_SESSION_STATE_DIR` is set by four test modules:
`tests/test_print_session.py:90`, `tests/test_print_log_failure.py:54`,
`tests/test_print_session_state_validation.py:52`,
`tests/test_print_session_durability.py:84`, `tests/test_integration.py:258`.

### Existing tests over this code

- `tests/test_print_session_durability.py::test_a_failed_save_leaves_the_state_file_parseable`
  — **wraps `session.advance()` in `pytest.raises(OSError)`**
- `tests/test_print_session_durability.py::test_a_failed_save_leaves_no_scratch_file_beside_the_state`
  — **same**
- `tests/test_print_session_durability.py::test_every_session_gets_its_own_state_file`
- `tests/test_print_session_durability.py::test_a_completed_run_removes_its_state_file`
- `tests/test_print_session.py::test_list_resumable_enumerates_interrupted_sessions`
- `tests/test_print_session.py::test_successful_completion_deletes_state_file`
- `tests/test_print_session.py::test_backend_error_leaves_session_resumable_not_discarded`
- `tests/test_hardening_platform.py::test_temp_dirs_come_from_gettempdir_not_a_raw_env_var`
  — reads `print_session`'s **source text** for `environ.get("TMPDIR")` and
  `environ["TMPDIR"]`. Neither appears now and neither will after; the
  test is a text scan, so removing `tempfile` entirely does not break it.
- `tests/test_app_directories.py` — the whole file, for `data_dir`'s
  per-platform behaviour.

## 3. Change

### 3.1 The directory

1. **`deckle/core/print_session.py`** — replace `_state_dir` (lines
   101-106) with:

   ```python
   def _state_dir() -> Path:
       """The directory holding interrupted sessions' state files.

       Under :func:`deckle.core.paths.data_dir`, alongside the session log
       and the diagnostics log, rather than under the OS temp dir. The
       temp dir was wrong for the one reason this file exists: on Linux
       ``/tmp`` is commonly ``tmpfs``, so a power cut or a kernel panic --
       precisely the event a resumable session is for -- takes the state
       file with it, and the user reprints forty sheets they already have.
       Windows keeps its temp dir on disk but hands it to Disk Cleanup.

       ``DECKLE_SESSION_STATE_DIR`` still wins, matching
       ``DECKLE_SESSION_LOG_DIR`` and ``DECKLE_LOG_DIR``, so tests and
       support requests can put it anywhere without touching the platform
       question.

       :returns: the directory. Nothing is created here -- :meth:`_save`
           makes its own parents, and :meth:`list_resumable` tolerates the
           directory not existing.
       """
       override = os.environ.get("DECKLE_SESSION_STATE_DIR")
       if override:
           return Path(override)
       return data_dir("print_sessions")
   ```

2. **Imports.** Add `data_dir` to the existing `paths` import at
   `deckle/core/print_session.py:40`:

   ```python
   from deckle.core.paths import data_dir, write_text_atomic
   ```

   Delete `import tempfile` (line 30) — it has no other use in the module.
   Confirm with `grep -n "tempfile" deckle/core/print_session.py`, which
   must return nothing afterwards.

3. **`state_path`'s docstring** (lines 365-373) — change the last clause
   from "otherwise a ``deckle/print_sessions`` folder under the OS temp
   dir" to "otherwise ``print_sessions`` under
   :func:`deckle.core.paths.data_dir`".

4. **The module docstring** at `deckle/core/print_session.py:8-12` says the
   state is persisted "so an interrupted job survives a crash or a printer
   disappearing". Add one sentence: that it lives under the OS data
   directory rather than the temp dir, because on Linux the temp dir does
   not survive the crash it is there for.

### 3.2 Sessions left in the old temp location

**They are ignored, and nothing migrates them.** `list_resumable` walks the
new directory only, so any `<tempdir>/deckle/print_sessions/*.json` left by
a previous build is simply never offered again. Three reasons, in order:

- On the platform where it matters most (Linux/tmpfs) there is almost never
  anything there to migrate — that is the bug.
- The temp directory is world-writable on POSIX. Reading a state file from
  it and handing its `sheets` list to a print backend would mean trusting a
  file any local user can write. `_check_state` bounds it, but the right
  answer to "should we read this?" is no.
- A session old enough to have been written by a previous build is old
  enough that the operator no longer knows how many sheets came out, and
  `resume` takes that count as ground truth.

Say this in the `_state_dir` docstring? No — say it in `docs/decisions.md`
(§7) and in the spec. A docstring that documents what a function does *not*
do with a directory it does not name is noise. Nothing sweeps the old
directory either: deleting files under a path the previous build chose is
not this change's business, and the OS reclaims them.

### 3.3 `_save` must not raise

The constraint that pins the design: `PrintDialog.start_print` and
`PrintDialog._drive` call `session.start()`, `session.advance()`,
`session.resume()` and `session.confirm_test_sheet()` with no `try`. Any
`OSError` from `_save` is a traceback in front of a user whose paper has
already come out. The `PrintResult`/`last_error` channel exists exactly for
that shape, and `_drive` already checks `session.last_error` after every
call.

The behaviour that must **not** change: a failed save still stops the run.
That is what
`tests/test_print_session_durability.py::test_a_failed_save_leaves_the_state_file_parseable`
asserts today (by exception), and it stays true (by error).

5. **Add a module-level helper** just above `class PrintSession`, after
   `_check_state`:

   ```python
   def _combined_error(printer_error: str | None, save_error: str | None) -> str | None:
       """One message when both the printer and the state file failed.

       Both are worth saying: the first tells the operator why the paper
       stopped, the second tells them the resume point is behind where the
       paper actually is. Reporting only one of them is how a resume
       reprints sheets nobody expected it to.
       """
       if printer_error and save_error:
           return f"{printer_error}. Also: {save_error}"
       return printer_error or save_error
   ```

6. **Rewrite `_save`** (lines 402-417) to return the failure rather than
   raise it. Keep the existing comment block verbatim — it explains the
   `fsync`, which is unrelated and still true — and add the guard:

   ```python
   def _save(self) -> str | None:
       """Persist the cursor. Returns why it could not be written, or ``None``.

       Reported rather than raised, for the same reason
       ``QtPrintBackend.submit`` reports a logging failure: by the time
       this runs the sheets are physically out, and the print dialog calls
       ``start``/``advance``/``resume`` with no ``try``. An unwritable
       data directory used to produce a traceback in front of someone
       holding a stack of paper.

       A failed save still stops the run -- every caller returns on a
       non-``None`` result. What changes is only how it is said.
       """
       # <existing comment block, unchanged>
       path = self.state_path
       try:
           path.parent.mkdir(parents=True, exist_ok=True)
           write_text_atomic(path, json.dumps(self._state.to_json(), indent=2))
       except OSError as exc:
           log_exception("session_state_save_failed", exc, path=str(path))
           return (
               f"the sheets were submitted, but this run's resume point "
               f"could not be saved to {path} ({exc}). Resuming would start "
               "from the last position that was saved, so check the stack "
               "before you do."
           )
       return None
   ```

7. **Update all seven `_save` call sites.** Each currently ignores the
   return value; each must now stop on a non-`None`.

   - `start()`, test-sheet branch, lines 580-587:

     ```python
             result = self._submit_sheets(pass_, first)
             saved = self._save() if result.error else None   # see below
     ```

     Written out plainly, replacing lines 577-587:

     ```python
             if self._state.test_first:
                 self._state.test_sheet_pending = True
                 first = pass_.sheet_order[:1]
                 result = self._submit_sheets(pass_, first)
                 if result.error:
                     self._last_error = _combined_error(result.error, self._save())
                     return
                 self._state.sheet_cursor = 1
                 self._last_error = self._save()
                 return
     ```

   - `confirm_test_sheet()`, lines 596-599:

     ```python
             self._state.test_first = False
             self._state.test_sheet_pending = False
             self._last_error = self._save()
             if self._last_error is not None:
                 return
             self._submit_chunk()
     ```

   - `_submit_chunk()`, lines 615-626:

     ```python
             chunk = remaining[: self.chunk_size]
             result = self._submit_sheets(pass_, chunk)
             if result.error:
                 self._last_error = _combined_error(result.error, self._save())
                 return

             self._state.sheet_cursor += len(chunk)
             self._last_error = self._save()
             if self._last_error is not None:
                 return

             if self._state.sheet_cursor >= len(pass_.sheet_order):
                 self._advance_pass()
     ```

     Note the existing `self._last_error = None` on line 621 is replaced by
     the assignment from `_save()`, which is `None` on success. Same
     meaning, one statement.

   - `_advance_pass()`, lines 628-635:

     ```python
         def _advance_pass(self) -> None:
             self._state.pass_index += 1
             self._state.sheet_cursor = 0
             if self._state.pass_index >= len(self._passes):
                 self._finished = True
                 self._delete_state()
             else:
                 self._last_error = self._save()
     ```

   - `resume()`, lines 688-694:

     ```python
             self._state.sheet_cursor = sheets_completed
             self._state.test_sheet_pending = False
             self._last_error = self._save()
             if self._last_error is not None:
                 return
             if self._state.sheet_cursor >= len(pass_.sheet_order):
                 self._advance_pass()
             else:
                 self._submit_chunk()
     ```

8. **Extend `last_error`'s docstring** (lines 705-713). It currently says
   "the backend's error text". It is now "the backend's error text, the
   reason the resume point could not be saved, or both". Keep the closing
   sentence about the state file still being on disk, but qualify it: after
   a save failure the state file holds an *older* cursor, which is why the
   message says to check the stack.

### 3.4 `_delete_state` must never raise

9. **Rewrite `_delete_state`** (lines 419-422):

   ```python
   def _delete_state(self) -> None:
       """Remove a finished run's state file. Never raises.

       This runs on the *successful* completion of the whole job, so
       anything that escapes it turns a finished book into a traceback. A
       state file that cannot be removed is litter in a directory the user
       does not open; ``load`` would refuse it anyway once the plan moves
       on, and ``list_resumable`` offering one stale entry costs a
       "no thanks" rather than paper.

       ``missing_ok=True`` rather than an ``exists()`` test: the check and
       the unlink were two calls with a window between them, and losing
       that race raised ``FileNotFoundError`` for a file that was already
       in the state we wanted.
       """
       try:
           self.state_path.unlink(missing_ok=True)
       except OSError as exc:
           log_exception("session_state_delete_failed", exc, path=str(self.state_path))
   ```

### 3.5 `list_resumable` must skip a file it cannot read *fields* from

10. **Move the `SessionSummary` construction inside the `try`, and widen
    the `except`.** Replace lines 535-554 with:

    ```python
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                summaries.append(
                    SessionSummary(
                        session_id=data["session_id"],
                        printer_name=data["printer_name"],
                        started_at=data["started_at"],
                        pass_index=data["pass_index"],
                        sheet_cursor=data["sheet_cursor"],
                        state_path=str(path),
                    )
                )
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                # Skipping is right -- one corrupt file must not hide every
                # other resumable session -- and the reading of the fields
                # has to be inside the guard, not after it. It was not:
                # the `except` caught a file that would not parse and then
                # `data["session_id"]` raised `KeyError` one line later,
                # for a file that parsed into JSON but not into a session.
                # A single hand-edited or half-written file therefore hid
                # every other resumable job, which is the exact outcome
                # this comment was written to prevent.
                #
                # `TypeError` as well as `KeyError`: valid JSON whose top
                # level is a list or a string subscripts with a `TypeError`
                # rather than a `KeyError`, and both mean the same thing --
                # this is not a session file.
                log_exception("session_state_unreadable", exc, path=str(path))
                continue
    ```

    Keep the existing `.tmp`-skip comment block above the `try` verbatim;
    it is still true and still explains an absence.

11. **Update `list_resumable`'s docstring** (lines 514-522). "A state file
    that cannot be read or parsed is skipped" becomes "A state file that
    cannot be read, cannot be parsed, or does not carry the fields a
    summary needs is skipped".

## 4. Tests

### `tests/test_print_session_durability.py` — two deliberate updates

`test_a_failed_save_leaves_the_state_file_parseable` (line 129) and
`test_a_failed_save_leaves_no_scratch_file_beside_the_state` (line 144)
both do:

```python
    with torn_write(), pytest.raises(OSError):
        session.advance()
```

Change both to:

```python
    with torn_write():
        session.advance()
```

and add to each an assertion that the failure was still reported:

```python
    assert session.last_error is not None
    assert "resume point" in session.last_error
```

Add a sentence to each docstring saying why the raise became an error: the
print dialog calls `advance()` with no `try`, and the paper is already out.
Add the same to the module docstring, which currently describes the
`fsync`/cleanup work only.

Both properties they were written for — the previous state stays parseable,
no scratch file is stranded — are unchanged and stay asserted.

### New tests

**`tests/test_print_session_durability.py::test_a_failed_save_stops_the_run_rather_than_raising`**

- **Setup:** `_session(chunk_size=1)`; `session.start()`; then under
  `torn_write()`, `session.advance()`; record
  `len(session.backend.calls)`; call `session.advance()` again outside the
  armed context is *not* part of this test.
- **Assertion in words:** `session.last_error` is not `None`, the session
  is not `finished`, and exactly one further chunk was submitted (the one
  whose save failed) — the run stopped instead of continuing to feed a
  printer whose cursor is no longer being recorded.
- **Unfixed tree:** `session.advance()` raises `OSError(28)`, so the test
  errors with `OSError: [Errno 28] No space left on device` before
  reaching any assertion.

**`tests/test_print_session_durability.py::test_a_completed_run_that_cannot_delete_its_state_still_finishes`**

- **Setup:** `_session(chunk_size=10)`; `session.start()`;
  `monkeypatch.setattr(Path, "unlink", exploding)` where `exploding`
  raises `OSError(13, "Permission denied")`; `session.advance()`.
- **Assertion in words:** no exception, `session.finished` is `True`. A
  book that printed correctly must not end in a traceback because a file
  in a cache directory would not delete.
- **Unfixed tree:** raises `PermissionError` out of `advance()`; the test
  fails with that error.

**`tests/test_print_session.py::test_list_resumable_skips_a_file_missing_its_fields`**

- **Setup:** start and fail one real session (as
  `test_list_resumable_enumerates_interrupted_sessions` does, with
  `StubBackend(fail_on_call_index=0)`), so one valid state file exists.
  Then write `_state_dir() / "aaaa.json"` containing `'{"version": 3}'` —
  valid JSON, no session fields. The name sorts before the real session's
  hex id often enough not to rely on it; assert on the *content* of the
  result, not on order.
- **Assertion in words:** `list_resumable()` returns exactly one summary,
  and its `printer_name` is the real session's. The broken file is skipped
  and the good one is still found.
- **Unfixed tree:** `KeyError: 'session_id'` raised out of
  `list_resumable`; the test errors.

**`tests/test_print_session.py::test_list_resumable_skips_a_file_that_is_not_an_object`**

- **Setup:** as above but the extra file contains `'[1, 2, 3]'`.
- **Assertion in words:** same — one summary, the real one.
- **Unfixed tree:** `TypeError: list indices must be integers or slices,
  not str`.

**`tests/test_print_session.py::test_the_state_directory_is_the_data_dir_not_the_temp_dir`**

- **Setup:** `monkeypatch.delenv("DECKLE_SESSION_STATE_DIR", raising=False)`
  (the autouse fixture at line 88 sets it, so this test must undo it);
  `monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")`;
  `monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))`.
- **Assertion in words:** `_state_dir() == tmp_path / "deckle" / "print_sessions"`,
  and `tempfile.gettempdir()` does not appear anywhere in `str(_state_dir())`.
- **Unfixed tree:** the assertion fails with the temp path on the left.
  Follows the pattern of `tests/test_app_directories.py`, which patches
  `deckle.core.paths.sys.platform` the same way.

**`tests/test_print_session.py::test_the_state_directory_override_still_wins`**

- **Setup:** `monkeypatch.setenv("DECKLE_SESSION_STATE_DIR", str(tmp_path / "elsewhere"))`.
- **Assertion in words:** `_state_dir() == tmp_path / "elsewhere"`. Pins
  the seam every other test in the suite depends on.
- **Unfixed tree:** passes. Keep it — it is the test that fails if someone
  "simplifies" `_state_dir` down to `data_dir("print_sessions")`.

## 5. Acceptance

| Check | Command |
|---|---|
| No temp dir in the session module | `! grep -n "tempfile\|gettempdir" deckle/core/print_session.py` |
| The state dir comes from `paths` | `grep -n 'data_dir("print_sessions")' deckle/core/print_session.py` |
| Linux resolution | `.venv/bin/python -c "import os; os.environ.pop('DECKLE_SESSION_STATE_DIR', None); os.environ['XDG_DATA_HOME']='/dat'; import deckle.core.paths as p; p.sys.platform='linux'; from deckle.core.print_session import _state_dir; assert str(_state_dir()) == '/dat/deckle/print_sessions', _state_dir()"` |
| Windows resolution | `.venv/bin/python -c "import os; os.environ.pop('DECKLE_SESSION_STATE_DIR', None); os.environ['APPDATA']='C:\\\\AD'; import deckle.core.paths as p; p.sys.platform='win32'; from deckle.core.print_session import _state_dir; assert _state_dir().parts[-2:] == ('Deckle','print_sessions'), _state_dir()"` |
| The override still wins | `DECKLE_SESSION_STATE_DIR=/x .venv/bin/python -c "from deckle.core.print_session import _state_dir; assert str(_state_dir()) == '/x'"` |
| `_save` returns instead of raising | `.venv/bin/python -c "import inspect, deckle.core.print_session as m; src = inspect.getsource(m.PrintSession._save); assert 'except OSError' in src and 'return' in src"` |
| `_delete_state` is guarded | `.venv/bin/python -c "import inspect, deckle.core.print_session as m; assert 'except OSError' in inspect.getsource(m.PrintSession._delete_state)"` |
| `list_resumable` never raises on a fieldless file | `.venv/bin/python -c "import json,os,tempfile; d=tempfile.mkdtemp(); os.environ['DECKLE_SESSION_STATE_DIR']=d; open(os.path.join(d,'a.json'),'w').write('{\"version\": 3}'); open(os.path.join(d,'b.json'),'w').write('[1,2]'); from deckle.core.print_session import PrintSession; assert PrintSession.list_resumable() == []"` |
| New and updated durability tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session_durability.py` |
| Session and dialog tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session.py tests/test_print_session_state_validation.py tests/test_print_dialog.py tests/test_integration.py` |
| The temp-dir source scan still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_platform.py tests/test_app_directories.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Row 1's grep currently returns **2** lines
(`deckle/core/print_session.py:30` `import tempfile` and `:106`
`Path(tempfile.gettempdir())`); it must return none. Row 2's grep currently
returns nothing. Row 8's one-liner currently ends in
`KeyError: 'session_id'`. All verified on the unfixed tree.

## 6. Out of scope

- **B3** — the plan hash. Same file; bumps `STATE_VERSION`, which this spec
  must not touch.
- **B4** — the duplicated `log_print_job` in `_submit_sheets`. Same file,
  and `_submit_sheets` is called from two of the functions this spec edits.
  See §8 for ordering.
- **B28** — reading the `version` field before `_check_state`. Same file,
  different method.
- **B14** — cancelling the "How many sheets came out?" prompt returns 0 and
  resumes from sheet 0. That is `print_dialog.py:369-376`; this spec does
  not touch the dialog.
- **Sweeping the old temp directory.** Deliberately not done; see §3.2.
- **`paths.write_text_atomic`'s 0600 mode and missing directory fsync**
  (B34). This spec makes `_save` tolerate a failure; it does not change how
  the write is performed.
- **N1** — autosave for never-saved projects under `data_dir()`. Different
  store, same root.

## 7. decisions.md entry

```
## 2026-09-05 — The file that exists to survive a crash lived in tmpfs
- Symptom: Three faults in one small area. `PrintSession`'s state file lived under `tempfile.gettempdir()`, which on Linux is normally tmpfs -- so the power cut a resumable session exists to survive is the same event that deletes it, and the operator reprints forty sheets. `_save` and `_delete_state` raised `OSError` into `PrintDialog`, which calls `start`/`advance`/`resume` with no `try`, so a full disk was a traceback in front of someone holding printed paper, and an undeletable file turned a finished book into an exception. And `list_resumable`, whose docstring says "Never raises", read `data["session_id"]` one line outside its own `except` -- so a single hand-edited state file raised `KeyError` and hid every other resumable job.
- Fix: `paths.data_dir("print_sessions")`, alongside the session log and the diagnostics log, with `DECKLE_SESSION_STATE_DIR` still winning. `_save` returns a message instead of raising and every caller stops on it, so a failed save still halts the run but says so through `last_error` like every other failure. `_delete_state` logs and carries on. `list_resumable` builds the summary inside the guard and catches `KeyError` and `TypeError` as well.
- Surfaces: Sessions left in the old temp directory are ignored rather than migrated -- on the platform where this mattered there is nothing left to migrate, and the temp directory is world-writable on POSIX, so reading a state file from it means handing a sheet list any local user can write to a print backend.
- Watch: The `except` was one line short of the code that needed it, and the comment *inside* that `except` described exactly the outcome the missing line produced. A comment explaining why a guard exists is not evidence the guard covers what it claims; check where the block actually ends.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B3, B4, B27 and B28 all edit
  `deckle/core/print_session.py`. Recommended order: **B4 → B3 → B28 →
  B27.** B27 is last because it rewrites the largest span (`_state_dir`,
  `_save`, `_delete_state`, `list_resumable` and seven call sites) and
  because its `_submit_sheets` call-site edits in §3.3 step 7 must be
  written against B4's two-argument signature, not the current
  three-argument one. If B4 has *not* landed, those snippets read
  `self._submit_sheets(pass_, first)` / `(pass_, chunk)` instead.
- **Two durability tests pin `pytest.raises(OSError)`.** They must be
  updated deliberately, not deleted. Both properties they assert (the old
  state stays parseable; no scratch file is stranded) survive the change
  and must keep being asserted.
- **`torn_write` in `tests/test_print_session_durability.py` patches
  `io.open` only**, while the one in `tests/test_output_command_parity.py`
  patches `io.open` *and* `builtins.open`. `write_text_atomic` reaches the
  file through `os.fdopen`, which routes through `io.open`, so the
  session's fixture works. Do not "improve" it to match the other one
  without checking what else it would arm.
- **`tests/test_hardening_platform.py::test_temp_dirs_come_from_gettempdir_not_a_raw_env_var`
  iterates `(export, loader, print_session)` and reads their source text.**
  It asserts the *absence* of `environ["TMPDIR"]`, not the presence of
  `gettempdir`, so removing `tempfile` from `print_session` keeps it green.
  Read it before assuming otherwise.
- **`data_dir` creates nothing.** `_save` must keep its
  `mkdir(parents=True, exist_ok=True)`, and `list_resumable` must keep its
  `if not directory.exists(): return []`.
- **`Path.unlink(missing_ok=True)`** needs Python 3.8+.
  `pyproject.toml:11` declares `requires-python = ">=3.11"`, so it is
  available; no fallback needed.
- **Existing state files move, silently.** Anyone testing the fix by hand
  on Linux should look in `~/.local/share/deckle/print_sessions`, not
  `/tmp/deckle/print_sessions`.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
