---
type: phase-spec
master_spec: "../2026-08-04-deckle-mvp.md"
sub_spec_id: SS-07
sub_spec_number: 7
title: "Project persistence, session log, and CLI"
depends_on: ['SS-02', 'SS-03', 'SS-04', 'SS-05']
date: 2026-08-04
---

# SS-07 — Project persistence, session log, and CLI

## Context

Three things that together make the core usable and observable without a GUI.

**The CLI is a shipped MVP feature, not a test harness.** It is also what lets the Pinebox
golden-fixture regression (SS-14) run headlessly in CI. Because `deckle.core` is Qt-free,
the CLI comes nearly free — that was the point of the architecture.

**The session log is a hard constraint, not a nicety.** Print failures are the hardest class
of bug in this app and are not reproducible after the fact. Every submitted job's full
parameters get written down.

**`.deckle` holds references, never content.** Paths plus content hashes. Small files, cheap
continuous autosave, and reopening after the source changed produces a clear warning instead
of silently using stale content.

## Provides

| Symbol | Consumed by |
|---|---|
| `save_project` / `load_project` | SS-09, SS-14 |
| `SourceChangedWarning` | SS-09 |
| `log_print_job(...)` | SS-08, SS-11 |
| `deckle.cli` entry point | SS-14 golden fixture, CI |

## Requires

- `load_pdf`, `load_image_dir` from SS-02
- `GutterShiftStrategy.impose` from SS-03
- `export` from SS-04
- `render_sheet` from SS-05

## Implementation Steps

### Step 1. Write the failing round-trip test

`tests/test_project_io.py`: save a project with reordered, rotated, and skipped pages; load
it; assert every page's state is preserved exactly.

### Step 2. Run and confirm failure

```bash
python -m pytest tests/test_project_io.py -q
```

### Step 3. Implement project_io

Create `deckle/core/project_io.py`. JSON, top level
`{"version": 1, "pages": [...], "layout": {...}, "printer": "..."}`.

Each page entry carries `path`, `page_index`, `sha256`, and per-page overrides. **Never
embed page data.**

### Step 4. Implement source-change detection

On load, recompute each source's `sha256`. On mismatch, surface `SourceChangedWarning`
naming the file. **Never silently substitute the new content** — that is how someone prints
80 sheets of the wrong revision.

### Step 5. Implement the session log

Create `deckle/core/session_log.py` exposing
`log_print_job(printer, profile, sheets, dpi, pass_index)`. One structured record per call,
appended to a session log file under the OS data dir. Include a timestamp.

### Step 6. Write failing CLI tests

`tests/test_cli.py`: assert `export` produces a readable PDF, and `info` prints page count,
detected page sizes, and layout warnings.

### Step 7. Implement the CLI

Create `deckle/cli.py` with subcommands `impose`, `export`, `info`. Accept `--gutter` with
unit suffixes (`0.75in`, `18pt`, `5mm`), `--paper`, `--scale-mode`, `--binding-edge`.

**`deckle/cli.py` imports only `deckle.core`.** Never `deckle.app`, never Qt. Add a test
asserting this — it is what keeps the CLI runnable in a headless CI container.

### Step 8. Verify headless operation

```bash
python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/out.pdf --gutter 0.75in
python -m deckle.cli info tests/fixtures/sample.pdf
```

Both must work with no display server present.

### Step 9. Commit

```bash
python -m pytest tests/test_project_io.py tests/test_cli.py -q
git add -A && git commit -m "feat(SS-07): project persistence, session log, and CLI"
```

## Interface Contracts

### save_project / load_project
- Direction: SS-07 → SS-09, SS-14
- Owner: SS-07
- Shape: `save_project(project, path) -> None`, `load_project(path) -> Project`.
  Written files always carry a top-level `"version": 1`.

### log_print_job
- Direction: SS-07 → SS-08, SS-11
- Owner: SS-07
- Shape: `log_print_job(printer: str, profile: PrinterProfile, sheets: Sequence[int], dpi: int, pass_index: int) -> None`.
  Called once per submitted chunk. **Required by the Intent constraints — not optional.**

## Verification Commands

```bash
python -m pytest tests/test_project_io.py tests/test_cli.py -q
python -m deckle.cli info tests/fixtures/sample.pdf
```

## Checks

| Criterion | Type | Command |
|---|---|---|
| Project IO exposed | [STRUCTURAL] | `grep -q "def save_project" deckle/core/project_io.py && grep -q "def load_project" deckle/core/project_io.py \|\| (echo "FAIL: project IO missing" && exit 1)` |
| Project format is versioned | [STRUCTURAL] | `grep -q "version" deckle/core/project_io.py \|\| (echo "FAIL: .deckle format not versioned" && exit 1)` |
| SourceChangedWarning defined | [STRUCTURAL] | `grep -q "SourceChangedWarning" deckle/core/project_io.py \|\| (echo "FAIL: SourceChangedWarning missing" && exit 1)` |
| log_print_job exposed | [STRUCTURAL] | `grep -q "def log_print_job" deckle/core/session_log.py \|\| (echo "FAIL: log_print_job missing" && exit 1)` |
| CLI has the three subcommands | [STRUCTURAL] | `for c in impose export info; do grep -q "$c" deckle/cli.py \|\| (echo "FAIL: CLI subcommand $c missing" && exit 1); done` |
| CLI never imports the app layer | [MECHANICAL] | `! grep -n "deckle.app\|PySide6" deckle/cli.py \|\| (echo "FAIL: CLI imports the Qt app layer" && exit 1)` |
| CLI export runs headlessly | [MECHANICAL] | `python -m deckle.cli info tests/fixtures/sample.pdf >/dev/null \|\| (echo "FAIL: CLI info failed" && exit 1)` |
| Persistence and CLI tests pass | [MECHANICAL] | `python -m pytest tests/test_project_io.py tests/test_cli.py -q \|\| (echo "FAIL: SS-07 tests failed" && exit 1)` |
