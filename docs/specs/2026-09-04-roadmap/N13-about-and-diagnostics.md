# N13 — Help ▸ About, and Help ▸ Open diagnostics folder

**Roadmap item:** `docs/ROADMAP.md` N13
**Depends on:** **N7** (it creates the Help menu these two items go into, and `build_menu_bar`'s "omit an action whose handler does not resolve" rule is what lets N13 be a pure addition). Edits `deckle/app/main.py`, so it collides with **M3**, **N4**, **N8** and **B9/B10/N9**. Recommended order: **N7 → N8 → N4 → N13 → M3 last**.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

GUIDE §9 tells a user reporting a problem to attach two things: the output of
`deckle --version`, and the JSON Lines diagnostic log. The desktop app has
neither.

- **The version.** `_version_string()` exists and prints the app version plus
  the resolved versions of pikepdf, pypdfium2, img2pdf and PySide6 — "the first
  thing anyone asks for in a bug report" (`cli.py:46-51`). It is reachable only
  as `python -m deckle.cli --version`. A GUI user who installed a packaged
  build has no CLI to run.
- **The log.** `diagnostics.jsonl` is written under an OS-appropriate data
  directory that differs on three platforms and that the user has never opened.
  Nothing in the app names it, and telling someone to find
  `~/.local/share/deckle` (or `%APPDATA%\Deckle`, or
  `~/Library/Application Support/Deckle`) in a support thread is how a bug
  report ends.

Verified:

```bash
$ grep -rn "_version_string\|__version__" deckle/app
$ grep -rn "diagnostics_log_path" deckle/app
```

Both return nothing. And `_version_string` lives in `deckle/cli.py`, which
`deckle.app` must not import — `docs/api/index.rst` states the direction:
"`deckle.cli` sits above `deckle.core` alone and never touches `deckle.app`",
and importing it the other way would invert that.

## 2. Current code

`deckle/cli.py:46-66` — the version string, and the reasoning for how it is
resolved:

```python
# A-6: `deckle --version` prints the app version plus the resolved versions
# of its key third-party dependencies -- the first thing anyone asks for in
# a bug report. Resolved via importlib.metadata (installed-distribution
# metadata) rather than importing the packages themselves, so this never
# imports PySide6 -- and so deckle.cli never has to import deckle.app.
_VERSIONED_DISTRIBUTIONS = ("pikepdf", "pypdfium2", "img2pdf", "PySide6")


def _distribution_version(dist_name: str) -> str:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _version_string() -> str:
    parts = [f"deckle {_DECKLE_VERSION}"]
    parts.extend(
        f"{dist} {_distribution_version(dist)}" for dist in _VERSIONED_DISTRIBUTIONS
    )
    return "\n".join(parts)
```

`deckle/cli.py:1237-1240` — its only consumer:

```python
    parser.add_argument(
        "--version", action="version", version=_version_string(),
        help="print the Deckle app version and key dependency versions",
    )
```

`deckle/__init__.py`:

```python
"""Deckle: impose and print booklets from PDF and image sources."""

__version__ = "0.1.0"
```

`pyproject.toml:6-13` — `name = "deckle"`, `version = "0.1.0"`,
`license = "MIT"`, `{ name = "Caleb Bennett" }`. There is a `LICENSE` file at
the repository root.

`deckle/core/diagnostics.py:63-87` — where the log lives, and the override:

```python
def data_dir() -> Path:
    """The OS-appropriate directory for Deckle's logs.

    ``DECKLE_LOG_DIR`` overrides it outright, which is what the tests use so
    they never touch a developer's real log.

    :returns: the directory, which may not exist yet.
    """
    override = os.environ.get("DECKLE_LOG_DIR")
    if override:
        return Path(override)
    return app_data_dir()


def diagnostics_log_path() -> Path:
    """The path to the current diagnostic log file.

    :returns: ``<data_dir()>/diagnostics.jsonl``.
    """
    return data_dir() / "diagnostics.jsonl"
```

`deckle/core/diagnostics.py:121-131` — the directory is created lazily, on the
first successful log write, and a failure degrades to a `NullHandler`. So it
may not exist when the user asks to open it.

`deckle/app/main.py:368-372` — the message-box seam N13 reuses:

```python
def _qt_message_box():
    """``QMessageBox``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMessageBox

    return QMessageBox
```

`deckle/app/main.py:884-895` — `_default_confirm_recovery`, the pattern for a
modal built through that seam.

`deckle/app/main.py:1-8` — the module docstring, which N7 already has to
correct for B30.

**Call sites of `_version_string` (grep):** `deckle/cli.py:61` (definition),
`cli.py:1238`. No test calls it directly.

**Call sites of `diagnostics_log_path` (grep):**
`deckle/core/diagnostics.py:82` (definition), `diagnostics.py:122`;
`tests/test_diagnostics.py:49,83`.

**Existing tests:** `tests/test_diagnostics.py` (the log path and the
`DECKLE_LOG_DIR` override), `tests/test_cli.py` (`--version`),
`tests/test_license_audit.py` and `tests/test_packaging_audit.py` (the runtime
dependency closure), `tests/test_docs_coverage.py` (every module needs a page).

## 3. Change

### Where the version string lives

Move it to `deckle/core/version.py`. Not duplicated in the app, and not
imported from the CLI — one answer to "what version am I", used by
`deckle --version` and by About, so a bug report's two halves cannot disagree.

`deckle.core` is the right home: it is the shared, Qt-free layer both front
ends already depend on, and `_version_string` imports nothing but
`importlib.metadata` and `deckle.__version__`. It keeps its "resolved via
`importlib.metadata` rather than importing the packages" property, which is
what stops the CLI pulling in PySide6.

```python
"""What version of Deckle this is, and of the four libraries it leans on.

The first thing a bug report needs, and the answer has to be the same in
both front ends: ``deckle --version`` and the desktop app's About dialog
are two renderings of one fact, and a support thread where they disagree
is worse than one where only the CLI can answer.

Versions are resolved through ``importlib.metadata`` -- installed
distribution metadata -- rather than by importing the packages themselves.
That is what lets the CLI report PySide6's version on a machine with no
display libraries, and it is why this module can live in ``deckle.core``
at all.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""
```

Public surface:

```python
VERSIONED_DISTRIBUTIONS: tuple[str, ...] = ("pikepdf", "pypdfium2", "img2pdf", "PySide6")

def distribution_version(dist_name: str) -> str:
    """The installed version of ``dist_name``, or ``"not installed"``."""

def version_string() -> str:
    """Deckle's version and its key dependencies', one per line.

    :returns: e.g. ``"deckle 0.1.0\\npikepdf 9.4.0\\n..."``. Never raises:
        a dependency that cannot be resolved reports "not installed"
        rather than making the version flag fail.
    """
```

### Steps

1. **New file `deckle/core/version.py`** with the module docstring and the
   three names above, moved verbatim from `cli.py:46-66` and made public
   (`_VERSIONED_DISTRIBUTIONS` → `VERSIONED_DISTRIBUTIONS`, etc.).

2. **`docs/api/core.version.rst`** (copy `docs/api/core.recent.rst`'s shape) and
   a `core.version` line in `docs/api/core.rst`'s toctree, after
   `core.diagnostics`. `tests/test_docs_coverage.py` enforces this.

3. **`deckle/cli.py`** — delete lines 46-66 and import instead:

   ```python
   from deckle.core.version import version_string
   ```

   `cli.py:1238` becomes `version=version_string(),`. The
   `from deckle import __version__ as _DECKLE_VERSION` import at `cli.py:24`
   and the `import importlib.metadata` at `cli.py:16` become unused — remove
   both if nothing else in the file uses them (grep first; `importlib` appears
   only in those lines).

4. **`deckle/app/main.py` — the About text, pure.** Beside the other
   module-level helpers:

   ```python
   ABOUT_TITLE = "About Deckle"

   def about_text() -> str:
       """What Deckle is, what version, under what licence.

       Pure and Qt-free so the wording is testable headlessly, the way
       ``autosave_recovery_offer`` and ``_recent_label`` are. The version
       block is the same string ``deckle --version`` prints, verbatim,
       because a bug report quotes one of them and a maintainer reads the
       other.
       """
       return (
           "Deckle -- impose and print booklets from PDF and image sources.\n\n"
           f"{version_string()}\n\n"
           "MIT licence. Copyright Caleb Bennett.\n\n"
           "Deckle works entirely offline: it never checks for updates, "
           "reports crashes, or sends anything anywhere."
       )
   ```

   The last paragraph is not decoration. Offline-only is an MVP exclusion the
   roadmap names explicitly ("auto-update / crash reporting / telemetry (MVP
   exclusions: fully offline)"), and an About dialog is where a user looks to
   find out whether an application phones home.

   `from deckle.core.version import version_string` at the top of `main.py`.

5. **`deckle/app/main.py` — the About dialog.**

   ```python
       def show_about(self) -> None:
           """Show what Deckle is, what version, and where the Guide is.

           :returns: nothing. The Guide button is offered only when the
               Guide is actually on disk -- a packaged build may not ship
               the docs, and a button that opens nothing is worse than an
               absent one.
           """
           QMessageBox = _qt_message_box()
           box = QMessageBox(self.window)
           box.setWindowTitle(ABOUT_TITLE)
           box.setText(about_text())
           box.setStandardButtons(QMessageBox.StandardButton.Ok)
           guide = guide_path()
           if guide is not None:
               guide_button = box.addButton(
                   "Open the Guide", QMessageBox.ButtonRole.ActionRole
               )
           else:
               guide_button = None
           box.exec()
           if guide_button is not None and box.clickedButton() is guide_button:
               open_local_path(str(guide))
   ```

6. **`deckle/app/main.py` — finding the Guide, purely.**

   ```python
   def guide_path() -> Path | None:
       """Where ``docs/GUIDE.md`` is, or ``None`` if it is not shipped.

       Two places, in order: beside the package (a source checkout or an
       editable install, ``<repo>/docs/GUIDE.md``), then inside it (a
       packaged build that copied the docs in, ``deckle/docs/GUIDE.md``).

       A local file and nothing else. Deckle is offline by design, so
       falling back to a URL would make the one dialog that promises "never
       sends anything anywhere" the one that opens a browser.

       :returns: the path, or ``None``.
       """
       package_dir = Path(__file__).resolve().parent.parent
       candidates = (
           package_dir.parent / "docs" / "GUIDE.md",
           package_dir / "docs" / "GUIDE.md",
       )
       for candidate in candidates:
           if candidate.is_file():
               return candidate
       return None
   ```

   `from pathlib import Path` added to `main.py`'s imports.

7. **`deckle/app/main.py` — opening a path in the desktop, one seam.**

   ```python
   def open_local_path(path: str) -> bool:
       """Hand ``path`` to the desktop to open. Patchable seam.

       ``QDesktopServices.openUrl`` with a ``file:`` URL is the only
       cross-platform way to say "show this in the file manager / open this
       document", and it is the only thing here that leaves the process.

       :param path: a local file or directory.
       :returns: whether the desktop accepted it. ``False`` is a normal
           answer -- a headless session, a locked-down desktop -- and the
           caller reports it rather than raising.
       """
       from PySide6.QtCore import QUrl
       from PySide6.QtGui import QDesktopServices

       return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
   ```

8. **`deckle/app/main.py` — the diagnostics folder.**

   ```python
       def open_diagnostics_folder(self) -> None:
           """Open the folder holding ``diagnostics.jsonl``.

           GUIDE section 9 asks a user reporting a problem to attach the
           log, and the directory it lives in is different on three
           platforms and one the user has never opened. This is the answer
           to "where is it".

           The directory is created if it is not there. It is made lazily,
           on the first successful log write, and a session with nothing to
           report has none -- opening a file manager on a path that does
           not exist reads as "the log is missing", which is a different
           and more alarming answer than "nothing has been logged".

           :returns: nothing. A desktop that will not open it is reported
               with the path spelled out, so the user can navigate there by
               hand.
           """
           folder = diagnostics_log_path().parent
           try:
               folder.mkdir(parents=True, exist_ok=True)
           except OSError as exc:
               log_exception("diagnostics_folder_create_failed", exc,
                             path=str(folder))
           if not open_local_path(str(folder)):
               self.status_bar.showMessage(
                   f"Could not open the folder. The diagnostic log is at "
                   f"{folder}"
               )
               return
           self.status_bar.showMessage(f"Opened {folder}")
   ```

   `from deckle.core.diagnostics import diagnostics_log_path, log_event,
   log_exception` — `main.py:17` already imports `log_event` and
   `log_exception`; add `diagnostics_log_path` to that line.

9. **`deckle/app/menus.py` (N7) — two more rows in `&Help`**, after
   `&Keyboard shortcuts`:

   | Label | Shortcut | id | Calls |
   |---|---|---|---|
   | *separator* | | | |
   | `Open &diagnostics folder` | — | `help.diagnostics` | `window.open_diagnostics_folder` |
   | *separator* | | | |
   | `&About Deckle` | — | `help.about` | `window.show_about` |

   No shortcuts: neither is used often enough to spend a key on, and About in
   particular must not shadow anything.

   `About Deckle` last and alone below a separator, which is where every
   platform's convention puts it.

10. **`docs/GUIDE.md` §9** — replace the instruction to run
    `deckle --version` and hunt for the log with:

    > In the desktop app, **Help ▸ About Deckle** shows the same version
    > block, and **Help ▸ Open diagnostics folder** opens the folder holding
    > `diagnostics.jsonl`. From a terminal, `python -m deckle.cli --version`
    > prints the version block.

    This is the one GUIDE edit N13 owns; the wider drift (D1-D11) is the docs
    pass.

## 4. Tests

`about_text`, `guide_path` and `version_string` are pure. `show_about` and
`open_diagnostics_folder` are driven unbound against a stub, the way
`tests/test_hardening_printing.py:271-297` drives `_apply_printers` — no
`QMainWindow`, which exits 127 under pytest here.

### `tests/test_version.py` (new)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_version_block_starts_with_deckles_own_version` | `version_string().splitlines()[0] == f"deckle {deckle.__version__}"` | `ModuleNotFoundError: No module named 'deckle.core.version'` |
| `test_every_key_dependency_is_named` | each of `"pikepdf"`, `"pypdfium2"`, `"img2pdf"`, `"PySide6"` appears in `version_string()` | as above |
| `test_a_missing_distribution_reports_not_installed` | `distribution_version("no-such-distribution-xyz")` `== "not installed"` | as above |
| `test_the_version_module_imports_no_qt` | after `importlib.reload`, `"PySide6" not in sys.modules` — or simply let `tests/test_core_purity.py` cover it, which it will automatically | `tests/test_core_purity.py` cannot see a module that does not exist |

### `tests/test_cli.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_cli_version_flag_still_prints_the_block` | `deckle --version` stdout equals `version_string()` | `ImportError` on the new module |

### `tests/test_about_dialog.py` (new)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_about_names_the_application_and_the_licence` | | `about_text()` contains `"Deckle"`, `"MIT licence"` and `"Caleb Bennett"` | `ImportError: cannot import name 'about_text' from 'deckle.app.main'` |
| `test_about_carries_the_same_version_block_as_the_cli` | | `version_string()` is a substring of `about_text()` | as above |
| `test_about_says_deckle_is_offline` | | contains `"never checks for updates"` | as above |
| `test_the_guide_is_found_in_a_source_checkout` | run from the repo | `guide_path()` is not `None` and ends with `docs/GUIDE.md` | as above |
| `test_a_missing_guide_reports_none` | monkeypatch `Path.is_file` to `False` | `guide_path() is None` | as above |
| `test_the_guide_is_never_a_url` | | `str(guide_path())` does not start with `"http"`, and `"http" not in inspect.getsource(main.guide_path)` | as above |

### `tests/test_about_dialog.py`, the diagnostics half

Set `DECKLE_LOG_DIR` to a `tmp_path` subdirectory that does **not** yet exist,
and call `deckle.core.diagnostics.reset_for_tests()` first — `tests/test_diagnostics.py`
records that without it the first test to log pins the location for the session.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_opening_the_folder_creates_it_if_it_is_missing` | patch `main.open_local_path` with a recorder returning `True`; call `MainWindow.open_diagnostics_folder(stub)` | the directory now exists, and the recorder was called once with its path | `AttributeError: type object 'MainWindow' has no attribute 'open_diagnostics_folder'` |
| `test_it_opens_the_folder_not_the_file` | as above | the recorded path is the directory, not `.../diagnostics.jsonl` | as above |
| `test_it_honours_the_log_directory_override` | `DECKLE_LOG_DIR` set | the recorded path is that directory | as above |
| `test_a_desktop_that_refuses_names_the_path` | recorder returns `False` | `stub.status_bar.message` contains the folder path and `"Could not open"` | as above |
| `test_a_folder_that_cannot_be_created_still_tries_to_open` | patch `Path.mkdir` to raise `PermissionError` | no exception escapes, and `open_local_path` was still called | as above |
| `test_about_offers_the_guide_only_when_it_exists` | patch `main.guide_path` to `None` and to a real path; drive `show_about` with a fake `QMessageBox` class through `_qt_message_box` | `addButton` called zero times, then once | `AttributeError: ... 'show_about'` |

### `tests/test_menus.py` (N7's, extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_help_carries_about_and_diagnostics` | `"help.about"` and `"help.diagnostics"` are in `bar.deckle_actions`, and triggering each calls the matching recorder once | `KeyError` |
| `test_about_is_the_last_item_in_help` | the Help menu's last non-separator action is `help.about` | `AssertionError` |
| `test_neither_help_item_takes_a_shortcut` | both `.shortcut().toString()` are `""` | `AssertionError` |

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_version.py tests/test_about_dialog.py` |
| The menu and CLI tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_menus.py tests/test_cli.py tests/test_diagnostics.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The new module has a docs page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| The app does not import the CLI | `! grep -rn "^from deckle.cli\|^import deckle.cli" deckle/app` |
| There is exactly one version string | `grep -rn "def version_string\|def _version_string" deckle \| wc -l` returns `1` |
| Both front ends print the same block | `diff <(.venv/bin/python -m deckle.cli --version) <(QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from deckle.core.version import version_string; print(version_string())")` |
| Nothing reaches the network | `! grep -nE "https?://" deckle/app/main.py` |
| No new runtime dependency | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_license_audit.py` |
| `[HUMAN]` The two items work | Launch Deckle. **Help ▸ About Deckle** shows the version block, the MIT licence, and the offline statement; the Guide button opens `docs/GUIDE.md` in the default markdown handler. **Help ▸ Open diagnostics folder** opens a file manager showing `diagnostics.jsonl`. |

## 6. Out of scope

- **N7.** It creates the Help menu; N13 fills it. Without N7 there is nowhere
  for these to go and the spec cannot land.
- **F13** (Linux packaging, README screenshots, docs in CI). `guide_path`'s
  second candidate — `deckle/docs/GUIDE.md` inside the package — anticipates a
  packaged build that copies the docs in. **Nothing does that today**, and
  making the packaging do it is F13's, not N13's. Until then the second
  candidate never matches and `guide_path` returns the checkout copy.
- **A "Copy diagnostics to clipboard" or "Save a support bundle" action.** Both
  are reasonable and both are larger: a bundle has to decide what to redact
  from a log full of filesystem paths.
- **Crash reporting, update checks, telemetry.** MVP exclusions the roadmap
  names, and the About text says so out loud.
- **`README.md`'s architecture module list** (D7), which omits several modules
  and will now omit `core.version` too. Docs pass.
- **D5** (GUIDE §8's CLI reference). §3 step 10 edits §9 only.

## 7. decisions.md entry

```
## 2026-09-05 — The app could not answer either question a bug report asks
- Symptom: GUIDE section 9 tells a user reporting a problem to attach `deckle --version` and the JSON Lines diagnostic log. The desktop app had neither: `grep -rn "_version_string\|diagnostics_log_path" deckle/app` returned nothing. `_version_string` lived in `deckle/cli.py`, which `deckle.app` must not import, and the log lives under a data directory that differs on three platforms and that the user has never opened.
- Fix: `_version_string` moved to `deckle/core/version.py` as `version_string()`, so both front ends render one fact -- a support thread where the CLI and the About dialog disagree is worse than one where only the CLI can answer. Help > About Deckle shows that block plus the MIT licence and, when `docs/GUIDE.md` is actually on disk, a button that opens it. Help > Open diagnostics folder creates the directory if it is missing and hands it to `QDesktopServices.openUrl`.
- Surfaces: The log directory is made lazily, on the first successful write, so a clean session has none. Opening a file manager on a path that does not exist reads as "the log is missing", which is a different and more alarming answer than "nothing has been logged" -- so the folder is created first.
- Surfaces: The Guide is opened as a local file or not offered at all. Deckle is offline by design, and the one dialog that promises "never sends anything anywhere" must not be the one that opens a browser. A grep for `https?://` in `main.py` is an acceptance check.
- Watch: `deckle.cli` sits above `deckle.core` alone and never touches `deckle.app`. Anything both front ends need belongs in `core`, not copied into the second one -- two copies of a version string is two versions.
- Commit: <fill in>
```

## 8. Traps

- **`deckle.app` must not import `deckle.cli`.** That is why `version_string`
  moves to `deckle.core` rather than being imported across. §5 greps for it.
- **The diagnostics directory may not exist.** `diagnostics._configure`
  (`diagnostics.py:121-131`) creates it on the first successful write and
  degrades to a `NullHandler` if it cannot. Create it before opening, and do not
  let a `PermissionError` there stop the open attempt.
- **`DECKLE_LOG_DIR` overrides the data directory outright**
  (`diagnostics.py:71-73`), and `diagnostics.reset_for_tests()` exists because
  "the first test to log would pin the log location for the whole session". Any
  test asserting on the path must call it.
- **`paths._root` reads the environment at call time.** A test that sets only
  `DECKLE_LOG_DIR` is safe here, but one that reaches `paths.data_dir` directly
  must also set `XDG_DATA_HOME` and `APPDATA`.
- **`QDesktopServices.openUrl` returns `False` rather than raising** on a
  headless or locked-down session, and under `QT_QPA_PLATFORM=offscreen` it may
  do nothing at all. Every test patches `open_local_path`; none should call the
  real one.
- **`QMessageBox.addButton` + `clickedButton()`** is how a third button is read
  back; `box.exec()`'s return value does not identify a custom button. Getting
  this wrong makes the Guide button look inert.
- **`importlib.metadata.version` raises `PackageNotFoundError`, not
  `ImportError`**, and `_distribution_version` already catches exactly that.
  Keep it — an editable install of a dependency can legitimately be absent from
  distribution metadata.
- **Constructing a real `QMainWindow` under pytest exits 127 here**
  (`tests/test_gui_workflow.py:9`). Drive `show_about` and
  `open_diagnostics_folder` unbound against a stub.
- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli --version`.
