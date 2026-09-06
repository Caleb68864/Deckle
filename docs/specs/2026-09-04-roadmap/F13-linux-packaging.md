# F13 — Make Linux a first-class target: `run.sh`, a Linux bundle, README images

**Roadmap item:** `docs/ROADMAP.md` F13
**Depends on:** **R0.3** — `packaging/deckle.spec` is gitignored by the
`*.spec` pattern and absent from the tree, so *nobody* can build, on any
platform. Fix that first; §B here has nothing to drive without it.
R0.1/R0.2/R0.4 are needed for the acceptance rows to be checkable at all.
**Blocks:** —
**Size:** M
**Decision needed first:** ROADMAP §6 — *"F13: is Linux a target, and if so
which format?"* This spec answers **"yes, and a PyInstaller one-directory
bundle plus a tarball"**, with the reasoning in §3.B. If the owner prefers a
different format (AppImage, Flatpak, a `.deb`), §A and §C stand unchanged and
only §B is rewritten.

---

## 1. Context

Deckle is developed on Windows and every developer affordance says so.

```
$ ls run.*
run.bat
$ ls packaging/
ls: cannot access 'packaging/': No such file or directory
$ grep -n "spec" .gitignore
21:*.spec
```

The README's badge reads **`Built binaries: Windows`**. `docs/GUIDE.md` §8
documents `run.bat` and nothing else. The hardening plan's Workstream 3 says
*"**Packaging** — deferred since the MVP. Windows and Linux artifacts."* and
that half has never happened.

Three concrete costs:

**A contributor on Linux has no launcher.** The seven things `run.bat`
does — launch, `cli`, `test`, `deps`, `doctor`, `docs`, `package` — are seven
invocations to reconstruct from the source, and `doctor` in particular (which
prints the interpreter, the dependency versions and the printers Qt can see)
is exactly what a new contributor needs and cannot get.

**Nobody can build anything.** `run.bat package` reads
`packaging\deckle.spec`, which is not in the repository (R0.3), and
`tests/test_packaging_audit.py` reads the same file. The audit that exists
because a first build shipped 1.6 GB including AGPL `pymupdf` cannot run.

**The README has no picture.** It carries a considered comment saying so:

```html
<!--
  SCREENSHOTS WANTED. None exist yet; no image is linked here on purpose,
  because a broken image on the front page is worse than none.
  When they exist, put them in docs/images/ and add them here:
    1. docs/images/preview-spread.png -- the main window with a document
       loaded, "Both" toggle on, showing the front/back spread side by side
       with the red imageable-area guide and the dashed blue content box
       both visible. This is the single most persuasive image available.
    2. docs/images/print-dialog.png -- the Print dialog mid-job, showing
       the reload instruction for pass 2 in plain words.
    3. docs/images/schedule.png -- a printed binding schedule sitting on a
       bench beside a folded signature. Photograph, not a screenshot.
-->
```

That comment is the specification for §C; F13's job is to make the three
images capturable and to leave the placeholders in a state where adding one
is a one-line edit rather than a design decision.

**CI is R0.6's.** The roadmap lists *"doc build in CI"* under F13 and *"No
CI. Nothing runs the 1,500 tests on a push."* under R0.6. **They are one
job.** F13 does not create a workflow file; it specifies the two extra steps
R0.6's job should carry, and §5 references them.

## 2. Current code

`run.bat:6-14` — the seven behaviours `run.sh` mirrors:

```
rem    run.bat                 launch the GUI
rem    run.bat cli <args...>   headless CLI  (e.g. run.bat cli info book.pdf)
rem    run.bat test            run the test suite
rem    run.bat test -k layout  run a subset
rem    run.bat deps            install/refresh dependencies
rem    run.bat doctor          check the environment without launching
rem    run.bat docs            build the HTML API reference
rem    run.bat package         build the .exe bundle into dist\deckle
```

`run.bat:21-24` — interpreter discovery:

```
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist "venv\Scripts\python.exe"  set "PY=venv\Scripts\python.exe"
```

`run.bat:56-80` — the packaging block, including the reasoning `run.sh` must
copy verbatim:

```
rem Build from .buildenv, NOT from the developer's interpreter. The first
rem build ran against a global environment and produced a 1.6 GB bundle
rem containing torch, paddle, cv2 -- and pymupdf, which is AGPL and would
rem have made the artifact undistributable under MIT. PyInstaller bundles
rem what it can reach, not what you declared, so the build environment is
rem part of the licence surface.
set "BUILDPY=.buildenv\Scripts\python.exe"
if not exist "%BUILDPY%" (
    echo [deckle] no clean build environment found. Creating .buildenv...
    "%PY%" -m venv .buildenv
    ...
    "%BUILDPY%" -m pip install -e .[package]
)
"%BUILDPY%" -m PyInstaller --noconfirm --distpath dist --workpath build\pyinstaller packaging\deckle.spec
...
"%PY%" -m pytest tests\test_packaging_audit.py -q
```

`run.bat:105-115` — `doctor`, the target with the most value per line:

```
:doctor
echo [deckle] interpreter:
"%PY%" -c "import sys; print('  ', sys.executable); print('  ', sys.version.split()[0])"
echo [deckle] dependencies:
"%PY%" -c "import importlib.metadata as m; [print('   ' + n.ljust(12) + m.version(n)) for n in ('pikepdf','pypdfium2','img2pdf','natsort','Pillow','PySide6')]"
echo [deckle] printers visible to Qt:
"%PY%" -c "from PySide6.QtPrintSupport import QPrinterInfo; ps=QPrinterInfo.availablePrinters(); print('   none found') if not ps else [print('   ', p.printerName()) for p in ps]"
```

`tests/test_run_bat.py:1-16` — why a launcher gets structural tests at all:

```python
"""Structural tests for ``run.bat``.

Written after a generated edit spliced the packaging block into the MIDDLE of
the dispatch table. The Python that inserted it replaced the first occurrence
of ``:doctor`` -- which was the ``goto :doctor`` line, not the label -- so the
``docs``/``help`` dispatch and the entire default GUI-launch block were
deleted, ``doctor`` was rewired to ``:package``, and a bare ``run.bat`` fell
straight through into the packager.

The result: the dev launcher spent three minutes building executables and then
closed. Every test passed throughout, because nothing tested the launcher.

These are cheap structural checks. A batch file has no import to fail and no
exception to raise -- it silently does the wrong thing -- so its shape is what
has to be asserted.
"""
```

`tests/test_run_bat.py:27-28` — the list a parity test compares against:

```python
#: Every subcommand the header documents.
SUBCOMMANDS = ("cli", "test", "deps", "doctor", "docs", "package")
```

`tests/test_packaging_audit.py:36-46` — what the built artifact is judged
on, all of it platform-independent:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist" / "deckle"

FORBIDDEN_IN_BUNDLE = ("pymupdf", "fitz", "pdfimpose", "cpdf", "ghostscript", "poppler")

MAX_BUNDLE_MB = 700
```

and `:49-56`, the skip that keeps it green until something is built:

```python
requires_bundle = pytest.mark.skipif(
    not _bundle_present(),
    reason="no built bundle in dist/deckle -- run `run.bat package` first",
)
```

`pyproject.toml:37-43` — the extras group the Linux build uses unchanged, and
the licence sentence that makes the whole thing lawful:

```toml
# Packaging tooling only. PyInstaller is GPLv2-or-later WITH an explicit
# exception permitting distribution of programs under any licence, which
# is what makes bundling this MIT app lawful. Build-time only; it never
# enters the runtime closure test_license_audit.py walks.
package = [
    "pyinstaller>=6",
]
```

`README.md:3-6` — the badges:

```markdown
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)
[![Platform: Windows](https://img.shields.io/badge/Built%20binaries-Windows-lightgrey.svg)](#installing)
[![Status: pre-release](https://img.shields.io/badge/Status-pre--release-orange.svg)](#project-status)
```

### Existing tests

`tests/test_run_bat.py` (the model for §A's tests),
`tests/test_packaging_audit.py` (§B's gate),
`tests/test_license_audit.py`, `tests/test_docs_coverage.py`,
`tests/test_app_directories.py` and `tests/test_hardening_platform.py` (the
POSIX/Windows split already exercised elsewhere).

---

## 3. Change

Three sub-sections. §A stands alone; §B needs R0.3; §C is documentation.

---

### §A — `run.sh`, mirroring `run.bat` subcommand for subcommand

A POSIX shell script at the repository root, executable
(`git update-index --chmod=+x run.sh`), `#!/usr/bin/env bash` with
`set -euo pipefail`.

**Chosen:** a shell script mirroring `run.bat` one-to-one. **Rejected:** a
`Makefile` (a second vocabulary to learn, and `make test -k layout` does not
work) and a Python `tasks.py` (needs the interpreter working before it can
tell you the interpreter is broken, which is `doctor`'s whole job).

Header comment mirroring `run.bat:4-15` with `./run.sh` spellings.

Interpreter discovery, the POSIX counterpart of `run.bat:21-24`:

```bash
PY=python3
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
[ -x "venv/bin/python" ]  && PY="venv/bin/python"
```

`.venv` wins over `venv`, matching `run.bat`'s order. Fall back to `python3`,
not `python` — on most Linux distributions `python` is either absent or
Python 2.

Then a `case "${1:-}" in` dispatch with **exactly these seven arms**, so
`tests/test_run_sh.py` can compare the two launchers' vocabularies:

| arm | behaviour |
|---|---|
| `cli` | `shift; exec "$PY" -m deckle.cli "$@"` |
| `test` | `shift; exec "$PY" -m pytest -q "$@"` |
| `deps` | `pip install --upgrade pip`, `pip install -e ".[dev]"` |
| `doctor` | interpreter, dependency versions, printers Qt can see |
| `docs` | `sphinx -b html -W --keep-going docs/api docs/api/_build/html` |
| `package` | §B |
| *(empty)* | `exec "$PY" -m deckle` |
| `-h`/`--help`/`help` | usage |
| *(anything else)* | usage, exit 1 |

Four places `run.sh` must **differ** from `run.bat`, each with a comment
saying why:

1. **`test` sets `QT_QPA_PLATFORM=offscreen`.**
   `00-environment.md` requires it, and the hardening plan's H-3 records that
   `QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit
   127). Windows has a real display in the developer's session and Linux CI
   does not.

```bash
    test)
        shift
        # Headless by default: Linux CI and most dev boxes have no display,
        # and the suite drives real QWidgets. `QT_QPA_PLATFORM` is only set
        # if the caller has not chosen one.
        export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
        exec "$PY" -m pytest -q "$@"
        ;;
```

2. **`deps` installs `-e ".[dev]"`**, not the bare package plus `pytest
   psutil` as `run.bat:98-101` does. The `[dev]` extra is the declared truth
   (`pyproject.toml:25-31`) and `run.bat`'s hand-listed pair has already
   drifted from it — `hypothesis` is in the extra and not in `run.bat`, and
   `numpy` is in neither (R0.2). **Fix `run.bat` to match in the same
   commit**, or the two drift the other way immediately.

3. **`doctor` must not hang.** `QPrinterInfo.availablePrinters()` enumerates
   network printers and blocks per printer until it times out;
   `docs/decisions.md` records that costing 81 minutes. Run the printer probe
   as a subprocess with `timeout 10` and print
   `   printer query timed out (see docs/decisions.md, 2026-08-04)` when it
   does not return. Also print the platform and the value of `DISPLAY` /
   `WAYLAND_DISPLAY`, because "the GUI will not start" on Linux is nearly
   always that.

4. **`package` gates on the platform** — see §B.

Exit handling: no `pause`, no `exit /b`. A failing arm exits non-zero;
`set -e` plus `exec` gives that for free, and the `usage` arm exits 1.

`chmod +x` matters and is a *tracked file mode*: `git ls-files -s run.sh`
must report `100755`. A `run.sh` that has to be invoked as `bash run.sh` is
half a launcher.

#### Tests — `tests/test_run_sh.py`

Modelled on `tests/test_run_bat.py`, same voice, same reasoning: a shell
script has no import to fail and no exception to raise, so its shape is what
gets asserted.

1. `test_run_sh_exists_and_is_executable`
   The file exists and `os.access(path, os.X_OK)`. Unfixed:
   `AssertionError: run.sh does not exist`.

2. `test_it_is_tracked_as_executable`
   `git ls-files -s run.sh` starts `100755`. On a checkout with a
   permission-losing filesystem the mode bit is the only durable record.
   Skip when `git` is unavailable.

3. `test_the_two_launchers_offer_the_same_subcommands`
   Parse `SUBCOMMANDS` out of `tests/test_run_bat.py` (or import it) and
   assert every one appears as a `case` arm in `run.sh`. **This is the test
   that keeps the two from drifting**, and it is the only reason to have a
   parity test at all.

4. `test_every_documented_subcommand_is_dispatched`
   Every name in `run.sh`'s own header comment has a matching `case` arm —
   `run.bat`'s bug was a documented subcommand wired to the wrong label.

5. `test_a_bare_invocation_launches_the_gui`
   The empty-argument arm runs `-m deckle` and nothing else. The `run.bat`
   incident was a bare invocation falling through into the packager.

6. `test_the_test_arm_sets_the_offscreen_platform`
   `QT_QPA_PLATFORM` appears in the `test` arm and the assignment respects
   an existing value (`:-offscreen`).

7. `test_the_deps_arm_installs_the_declared_dev_extra`
   `run.sh` contains `.[dev]`, and `pyproject.toml`'s `dev` list is what it
   installs. Guards the drift `run.bat` already has.

8. `test_the_doctor_arm_bounds_the_printer_query`
   `timeout` appears in the `doctor` arm. The 81-minute hang is in the
   decision log; a launcher that reproduces it is worse than no launcher.

9. `test_it_prefers_a_local_virtualenv`
   `.venv/bin/python` is checked before `venv/bin/python`, and both before
   the `python3` fallback.

10. `test_shellcheck_is_clean` — `pytest.importorskip`-style skip when
    `shellcheck` is not on `PATH`; otherwise run it and assert exit 0. Cheap,
    and it catches the class of bug `tests/test_run_bat.py` was written for.

---

### §B — A Linux PyInstaller build

**Requires R0.3.** `packaging/deckle.spec` must exist and be tracked
(un-ignored with a negating rule, e.g. `!packaging/deckle.spec` after the
`*.spec` line in `.gitignore`).

#### Format

**Chosen: PyInstaller one-directory bundle in `dist/deckle/`, plus a
`deckle-<version>-linux-x86_64.tar.gz` of that directory.** Reasons, in
order:

1. It is what `packaging/deckle.spec` already produces on Windows and what
   `tests/test_packaging_audit.py` already judges — `DIST_DIR = REPO_ROOT /
   "dist" / "deckle"`, a forbidden-name list and a 700 MB ceiling, all
   platform-independent. One spec file, one audit, two platforms.
2. It needs no packaging toolchain beyond the `[package]` extra already
   declared, and no new runtime dependency for the licence audits to walk.
3. Deckle is fully offline with no update mechanism (an MVP exclusion), so a
   store format buys nothing it uses.

**Rejected:** AppImage (a second build toolchain and a second artifact for
`test_packaging_audit.py` to learn to unpack), Flatpak (a portal-mediated
sandbox around an app whose entire job is talking to CUPS and the local
filesystem), a `.deb` (per-distribution packaging for a two-person project).
Record all three in the decisions entry; the owner asked *"which format?"*
and the answer should show its work.

#### `packaging/deckle.spec`

R0.3 commits it; F13 makes it platform-aware. Two entry points as today
(`deckle` → `deckle/__main__.py`, `deckle-cli` → `deckle/cli.py`), with:

```python
import sys

_WINDOWS = sys.platform == "win32"
# `.exe` on Windows, bare names elsewhere. PyInstaller appends the suffix
# itself, so the spec must not: naming the target `deckle.exe` on Linux
# produces a Linux ELF called `deckle.exe`.
```

and a `console=False` GUI target plus a `console=True` CLI target on both
platforms. Windows-only options (`icon=`, `version=`, `uac_admin`) go behind
`if _WINDOWS`.

#### `run.sh package`

Mirrors `run.bat`'s block, including its reasoning comment verbatim — the
clean-`.buildenv` rule is a **licence** rule, not a tidiness one, and the
comment is why anyone would keep it:

```bash
    package)
        # Build from .buildenv, NOT from the developer's interpreter. The
        # first Windows build ran against a global environment and produced
        # a 1.6 GB bundle containing torch, paddle, cv2 -- and pymupdf,
        # which is AGPL and would have made the artifact undistributable
        # under MIT. PyInstaller bundles what it can reach, not what you
        # declared, so the build environment is part of the licence surface.
        BUILDPY=".buildenv/bin/python"
        if [ ! -x "$BUILDPY" ]; then
            echo "[deckle] no clean build environment found. Creating .buildenv..."
            "$PY" -m venv .buildenv
            "$BUILDPY" -m pip install --upgrade pip
            "$BUILDPY" -m pip install -e ".[package]"
        fi
        "$BUILDPY" -m PyInstaller --noconfirm \
            --distpath dist --workpath build/pyinstaller packaging/deckle.spec
        echo "[deckle] auditing the bundle..."
        QT_QPA_PLATFORM=offscreen "$PY" -m pytest tests/test_packaging_audit.py -q
        tar -czf "dist/deckle-$("$PY" -c 'import deckle;print(deckle.__version__)')-linux-$(uname -m).tar.gz" \
            -C dist deckle
        echo "[deckle] wrote dist/deckle/deckle and dist/deckle/deckle-cli"
        ;;
```

(If `deckle.__version__` does not exist, read the version the way
`cli._version_string` does — check before writing this line.)

The tarball is created **after** the audit, so an artifact that fails the
licence gate is never packaged for distribution.

#### `tests/test_packaging_audit.py`

Two additions, both small:

- The skip message reads *"run `run.bat package` first"*. Make it
  `"run `run.bat package` (Windows) or `./run.sh package` (Linux) first"`.
- A new test `test_the_bundle_carries_both_entry_points`: `dist/deckle`
  contains a `deckle` and a `deckle-cli` (with `.exe` on Windows), and both
  are executable on POSIX. The audit currently checks what must *not* be
  present; nothing checks that what must be present is.

Everything else — the forbidden-name list, the 700 MB ceiling, the
`_bundle_present` skip — is already platform-independent and must not be
touched.

#### `.gitignore`

R0.3 adds `!packaging/deckle.spec`. F13 adds `dist/` and `.buildenv/` if they
are not already ignored, and must **not** ignore `packaging/`.

---

### §C — README screenshot placeholders

Three files under `docs/images/`, and the README comment turned into linked
figures **only when the images exist**. The comment's own rule stands: *"a
broken image on the front page is worse than none."*

Concretely, F13 does three things and no more:

1. **Create `docs/images/` with a `README.md`** naming the three images, the
   exact filenames the comment already specifies
   (`preview-spread.png`, `print-dialog.png`, `schedule.png`), what each must
   show (copied verbatim from the HTML comment), and how to capture them:

   - `preview-spread.png` and `print-dialog.png`: launch with
     `./run.sh`, import `deckle dummy -o dummy.pdf --pages 16`, and capture
     the window. **Not** with `QT_QPA_PLATFORM=offscreen` — an offscreen
     grab has no window decoration and no real theme, and the point of the
     image is what the app looks like.
   - `schedule.png`: a photograph, per the comment. It is the one image that
     shows the artefact rather than the software, and F4's operator will be
     holding exactly that object.

2. **Leave the HTML comment in place**, with one line appended:
   `Capture instructions: docs/images/README.md.` Do not replace it with
   `<img>` tags for files that do not exist.

3. **Add a `test_readme_images.py`** with one test:
   `test_every_image_the_readme_links_is_present` — parse `README.md` for
   `![...](...)` and `<img src=...>` targets under `docs/`, and assert each
   file exists. It passes trivially today (there are none) and becomes the
   guard the moment someone adds one. That is the cheapest possible defence
   against the exact thing the comment is afraid of.

Also in §C, because they are one edit:

- The **Platform badge** becomes
  `[![Platform: Windows and Linux](https://img.shields.io/badge/Built%20binaries-Windows%20%7C%20Linux-lightgrey.svg)](#installing)`
  — **only once §B has produced and audited a Linux bundle.** A badge is a
  claim.
- README's *Installing* and *Running* sections gain the `./run.sh`
  equivalents beside every `run.bat` line.
- GUIDE §8's `run.bat` table gains a `run.sh` column, or a second table
  beneath it with the same seven rows.
- `docs/GUIDE.md` and `README.md`'s development instructions reference
  `./run.sh deps` for a fresh Linux clone, which is `00-environment.md`'s
  three-step setup collapsed into one command.

---

## 4. Tests

§A's ten tests, above, in `tests/test_run_sh.py`.

§B: the two additions to `tests/test_packaging_audit.py`, above, plus

- `test_the_spec_file_is_tracked` — `packaging/deckle.spec` exists and
  `git check-ignore packaging/deckle.spec` exits non-zero. R0.3 may already
  add this; if so, do not duplicate it.
- `test_the_spec_names_no_platform_suffix` — the spec does not contain the
  literal `.exe`. PyInstaller appends the extension; a hard-coded one
  produces a Linux ELF named `deckle.exe`.

§C: `tests/test_readme_images.py`, one test, above.

Cross-cutting:

- `test_the_two_launchers_document_the_same_thing` — the header comment of
  `run.sh` and the header comment of `run.bat` list the same subcommands.
  Documentation drift between two launchers is D10's shape (a docstring
  claiming a refresh that does not exist).

## 5. Acceptance

| Check | Command |
|---|---|
| `run.sh` exists and is executable | `test -x run.sh` |
| Tracked as executable | `git ls-files -s run.sh \| grep -q "^100755"` |
| Launcher tests pass | `.venv/bin/python -m pytest -q tests/test_run_sh.py tests/test_run_bat.py` |
| It actually runs the CLI | `./run.sh cli --version` |
| It actually runs the suite | `./run.sh test -q -k test_models` |
| `doctor` returns promptly | `time ./run.sh doctor` — **must return in well under 30s even with an unreachable network printer configured** |
| A bare `run.sh` does not package | `! grep -A3 '^\s*\*)' run.sh \| grep -q PyInstaller` |
| shellcheck clean | `shellcheck run.sh` (skip if not installed) |
| The spec file is tracked | `test -f packaging/deckle.spec && ! git check-ignore -q packaging/deckle.spec` |
| A Linux bundle builds and passes the audit | `./run.sh package` |
| Both entry points are present and executable | `test -x dist/deckle/deckle && test -x dist/deckle/deckle-cli` |
| The bundle carries nothing AGPL | `.venv/bin/python -m pytest -q tests/test_packaging_audit.py` (no longer skipped) |
| The bundled CLI works standalone | `./dist/deckle/deckle-cli --version` |
| The tarball exists | `ls dist/deckle-*-linux-*.tar.gz` |
| No README image is linked before it exists | `.venv/bin/python -m pytest -q tests/test_readme_images.py` |
| The images directory documents what to capture | `test -f docs/images/README.md && grep -q "preview-spread.png" docs/images/README.md` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| **[HUMAN]** The bundled GUI launches | `./dist/deckle/deckle` on a Linux desktop: the window appears, a PDF imports, the preview renders. A PyInstaller bundle that imports cleanly and cannot open a window is the failure mode the *"Added run.bat; verified the GUI genuinely launches"* decision-log entry exists for. |
| **[HUMAN]** The three images | Capture them per `docs/images/README.md` and link them. |

**CI belongs to R0.6.** That job already runs the suite on Linux with
`QT_QPA_PLATFORM=offscreen`. F13 asks it for two more steps, and they are
specified here so R0.6's spec can pick them up:

1. `./run.sh docs` — the Sphinx build with `-W`, so a docstring that drifts
   from the code fails the build. The hardening plan's Workstream 2 asked for
   *"Doc build in CI, warnings-as-errors, so drift fails the build"* and it
   has never run anywhere but a Windows developer's machine.
2. `shellcheck run.sh`, if the runner has it.

`./run.sh package` is deliberately **not** a CI step: it takes minutes, needs
a clean `.buildenv`, and its gate (`test_packaging_audit.py`) is a release
gate rather than a push gate.

Greps run against the current tree:

```
$ ls run.*
run.bat
$ ls packaging/
ls: cannot access 'packaging/': No such file or directory
$ grep -n "spec" .gitignore
21:*.spec
$ grep -c "SCREENSHOTS WANTED" README.md
1
```

## 6. Out of scope

- **R0.3** — committing `packaging/deckle.spec` and the negating ignore rule.
  §B depends on it and does not do it.
- **R0.6** — creating the CI workflow. §5 specifies two steps for it and
  writes no YAML.
- **R0.1, R0.2, R0.4, R0.5** — the fixture, `numpy`, the Linux chmod, and the
  file named `=`. Every acceptance row here assumes a suite that runs.
- **macOS.** `deckle/core/paths.py` already has the three-way platform
  answer, and `run.sh` will work there, but no `.app` bundle, no
  notarisation, no signing. If the owner wants macOS binaries that is its own
  item.
- **Signing, notarisation, auto-update, a package repository.** MVP
  exclusions: Deckle is fully offline.
- **D1** — the README's *"608 passing, 17 skipped"* count. Fix it in the docs
  pass; F13 touches the badges and the launcher sections only.
- **Changing what the packaging audit forbids.** The list and the 700 MB
  ceiling stay exactly as they are.

## 7. decisions.md entry

Two entries — §A and §B are separate commits, and §C rides with §B because
the badge and the bundle are the same claim.

```
## 2026-09-05 — run.sh, so a Linux clone has the same seven commands
- Symptom: `run.bat` was the only launcher, so a contributor on Linux had to reconstruct seven invocations from the source -- including `doctor`, which prints the interpreter, the dependency versions and the printers Qt can see, and is exactly what a new contributor needs and could not get.
- Fix: `run.sh` with the same seven arms, tested by `tests/test_run_sh.py` in the same voice as `tests/test_run_bat.py` -- a shell script has no import to fail and no exception to raise, so its shape is what gets asserted. A parity test compares the two launchers' subcommand lists so they cannot drift.
- Surfaces: four deliberate differences. `test` sets `QT_QPA_PLATFORM=offscreen` unless the caller chose one, because Linux dev boxes and CI have no display and the suite drives real QWidgets. `deps` installs the declared `[dev]` extra rather than a hand-listed pair -- `run.bat`'s list had already drifted from `pyproject.toml`, and it was fixed to match in the same commit. `doctor` bounds the printer query with `timeout`, because an unbounded one cost 81 minutes once already. `package` gates on the platform.
- Watch: the executable bit is tracked (`100755`), and a test asserts it. A `run.sh` you have to invoke as `bash run.sh` is half a launcher.
- Commit: <fill in>
```

```
## 2026-09-05 — Linux is a build target: one spec file, one audit, two platforms
- Symptom: the README badge said "Built binaries: Windows", the hardening plan's release workstream had asked for "Windows and Linux artifacts" since the MVP, and nobody could build on either platform because `packaging/deckle.spec` was gitignored by `*.spec` and absent (R0.3).
- Fix: `./run.sh package` mirrors `run.bat package` -- clean `.buildenv`, PyInstaller against the same spec file, then `tests/test_packaging_audit.py` as the gate, then a tarball. The spec is platform-aware only where it must be: PyInstaller appends the executable suffix itself, so the spec names no `.exe` and a hard-coded one would have produced a Linux ELF called `deckle.exe`.
- Surfaces: the format question was open. PyInstaller one-directory plus a tarball was chosen because `test_packaging_audit.py`'s forbidden-name list and 700 MB ceiling are already platform-independent and judge `dist/deckle` on either OS -- one artifact shape, one audit. AppImage was rejected as a second build toolchain and a second thing for the audit to learn to unpack; Flatpak as a portal sandbox around an app whose whole job is CUPS and the local filesystem; a .deb as per-distribution packaging for a two-person project.
- Watch: the clean-`.buildenv` rule is a LICENCE rule, not tidiness. The first Windows build bundled AGPL `pymupdf` from a developer's global environment while every dependency-level audit passed. The tarball is written after the audit, so an artifact that fails the gate is never packaged.
- Commit: <fill in>
```

## 8. Traps

- **`run.bat` has a real bug history.** A generated edit once replaced the
  first occurrence of `:doctor` — which was the `goto`, not the label — and
  a bare `run.bat` fell into the packager for three minutes while every test
  passed. Do not edit either launcher with a blind string replace, and read
  `tests/test_run_bat.py`'s docstring first.
- **`QPrinterInfo.availablePrinters()` can block for minutes.** `doctor` must
  bound it. The decision log records 81 minutes.
- **`QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit
  127)** — hardening plan H-3. `run.sh test` sets offscreen; `run.sh` (bare)
  must not.
- **`$TMPDIR` is empty in Git Bash on Windows** (H-3), so a script that does
  `"$TMPDIR/out.pdf"` resolves to `/out.pdf` and *hangs*. `run.sh` is a POSIX
  script and should not be run under Git Bash, but do not write anything that
  depends on `$TMPDIR` regardless.
- **PyInstaller appends the executable suffix.** Never write `.exe` in the
  spec.
- **PyInstaller bundles what it can reach, not what you declared.** Build
  from `.buildenv`. `tests/test_packaging_audit.py`'s docstring is the
  incident report.
- **`python` is often absent on Linux.** Fall back to `python3`.
- **Do not link a README image before the file exists.** The comment in the
  README says why, and `tests/test_readme_images.py` is what enforces it.
- **The Platform badge is a claim.** Change it after a Linux bundle has
  built and passed the audit, not before.
- **`git ls-files -s` is how the executable bit is checked**, not
  `os.access` alone — a clone on a filesystem without permission bits will
  still report `100755` from the index.
- **`python -m deckle` launches the GUI and blocks.** `run.sh` with no
  arguments does exactly that, on purpose; every test must use `run.sh cli`
  or `run.sh test`.
