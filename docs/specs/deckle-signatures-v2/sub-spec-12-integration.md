---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-12
sub_spec_number: 12
title: "Integration, the end-to-end fold round trip, and the full-suite gate"
depends_on: ['SS-04', 'SS-08', 'SS-09', 'SS-10', 'SS-11']
dispatch: auto
date: 2026-08-04
---

# SS-12 — Integration, the end-to-end fold round trip, and the full-suite gate

## Context

This is the sub-spec that turns parts into a feature. Eleven sub-specs each proved a piece in
isolation; SS-12 wires them into the two entry points a user actually reaches (the CLI and,
via SS-11, the app), drives the whole path end to end against real bytes, and holds the line
on every constraint in the master spec's *Constraints* section.

**The end-to-end fold round trip is the point.** The master spec closes with:

> `fold_reading_order(impose(pages, settings)) == list(range(len(pages))) + [None] * blanks`
> — fold the plan back up and you get the book.

SS-05 asserts that over synthetic plans; SS-08 asserts it over the strategy's output. SS-12
asserts it over the **exported, reopened** plan — the one that actually became a PDF. If
`fold_reading_order` and the imposition disagree here and the discrepancy is not obviously a
bug in exactly one of them, **stop and escalate**. Do not "fix" the simulator to agree; that
is the shared-wrong-assumption failure the whole design is structured to prevent.

**No orphaned modules.** `deckle/core/signatures.py` and `deckle/core/marks.py` can both be
fully green and completely unreachable from a real impose-and-export. `tests/test_integration.py`
already carries the precedent for catching exactly this: it enumerates every module under
`deckle/app/views/` and asserts each is imported by `main.py`
(`test_orphaned_views_every_view_module_is_imported_by_main`), and it AST-parses
`MainWindow.__init__` to prove each view is *constructed and mounted*, not merely imported.
Apply the same standard to the two new core modules.

**The full-suite gate, stated exactly.** The tree at authoring time collects **237** tests:
**234 pass, 3 skip.** The three skips are `tests/test_golden_pinebox.py`, which skips cleanly
when `DECKLE_PINEBOX_FIXTURE` is unset and the fixture is absent
(`tests/test_golden_pinebox.py:30-38`) — this is the case on this machine and it is **by
design**, not a failure. The gate is therefore:

- `python -m pytest -q` exits 0 (zero failures; skips are fine), **and**
- collection reports **no fewer than 237** tests.

A criterion asserting *237 pass* would be wrong and would fail on a correct tree. Do not write
one. Verified on the current tree: `234 passed, 3 skipped in 8.32s`, `237 tests collected`.

**Environment notes, verified here:**

- `ruff` is not on `PATH` in this environment's Git Bash. `python -m ruff check …` works and
  exits 0 on the current tree.
- `$TMPDIR` is **unset**, and Git Bash's `/tmp` is a shell mount that a Python subprocess does
  not resolve — `pikepdf.open("/tmp/sig.pdf")` raises `FileNotFoundError` even after the
  shell wrote it. Every check below that needs a scratch file uses a repo-relative
  `.deckle-check-tmp/` directory and removes it afterwards. This was confirmed by executing
  both forms.
- `deckle/cli.py` is invoked as `python -m deckle.cli` (it has an `if __name__ ==
  "__main__"` guard at line 235).

## Files

**Files (modify):**
- `deckle/cli.py`
- `docs/CONTRIBUTING.md`
- `tests/test_cli.py`

**Files (new):**
- `tests/test_integration_signatures.py`

Every `(modify)` path was confirmed present on the current tree. `tests/test_integration.py`
is **not** modified — its orphan-view and end-to-end tests stay exactly as they are, and the
new folio flow lives in its own file.

## Provides

| Symbol | Module | Consumed by |
|---|---|---|
| `--fold-scheme`, `--sheets-per-signature`, `--blank-mode`, `--sewing-stations` | `deckle/cli.py` | `impose`, `export`, `info` subcommands |
| signature breakdown in `info` output | `deckle/cli.py` | headless verification, the user |
| `tests/test_integration_signatures.py` | tests | the feature gate |
| five-noun page model, zero-diff seam rule, advisory-only rule | `docs/CONTRIBUTING.md` | future maintainers |

## Requires

| Symbol | From | Note |
|---|---|---|
| `SaddleStitchStrategy` | SS-08 | reached via `--fold-scheme folio` |
| `split_signatures`, `saddle_order`, `fold_reading_order` | SS-05 | `fold_reading_order` closes the round trip |
| `sewing_stations`, `signature_order_mark`, `fold_line` | SS-06 | consumed by `export.py` |
| mark drawing via `ContentStreamBuilder` | SS-09 | two `q…Q` `Do` blocks per folio page |
| `tests/test_seam_zero_diff.py`, `_hash_plan` content-awareness | SS-04 | one of the four gates |
| `FORBIDDEN_DISTRIBUTIONS`, `tools/oracle_diff.py` | SS-10 | `ruff check … tools` needs `tools/` to exist |
| `strategy_for`, `binding_readout` | SS-11 | CLI wording may reuse the readout |

## Implementation Steps

### Step 1. Failing test — CLI accepts the four folio flags

Add `test_cli_accepts_folio_flags` to `tests/test_cli.py`: call
`build_parser().parse_args(["info", SAMPLE_PDF, "--fold-scheme", "folio",
"--sheets-per-signature", "4", "--blank-mode", "balanced", "--sewing-stations", "0"])` and
assert each attribute lands on the namespace.

```bash
python -m pytest tests/test_cli.py -q -k cli_accepts_folio_flags
```

Expect a `SystemExit(2)` — argparse rejects the unknown options.

### Step 2. Implement the flags

Extend `_add_layout_args` (`cli.py:122-135`) with the four options, and
`_build_layout_settings` (`cli.py:114-119`) to pass them into `LayoutSettings`. `--paper-thickness`
is **not** added: `paper_thickness_pt` is an advisory input the user supplies in the app, and
adding a CLI flag for it invites the misreading that it changes geometry. Choices are pinned to
the committed literals: `--fold-scheme {none,folio}`, `--blank-mode {end,balanced}`.

Run green, commit.

### Step 3. Failing test — the CLI dispatches to the right strategy

Add `test_cli_export_under_folio_uses_the_saddle_strategy`: run
`main(["export", SAMPLE_PDF, "-o", str(tmp_path / "out.pdf"), "--fold-scheme", "folio"])` and
assert the exported PDF's page count matches a folio imposition, not a gutter-shift one.

### Step 4. Implement strategy dispatch in the CLI

`_cmd_info` (`cli.py:152`), `_cmd_export` (`cli.py:172`) and `_cmd_impose` each hardcode
`GutterShiftStrategy()`. Add one module-level `_strategy_for(settings)` in `cli.py` and route
all three through it. **`deckle/cli.py` must keep importing only `deckle.core`** — never
`deckle.app`, never a Qt binding. Do not import SS-11's `strategy_for` from
`deckle/app/views/layout_panel.py`; duplicate the two-line dispatch in `cli.py` instead. The
two copies are a dispatch table, not a geometry rule, and the import constraint outranks the
duplication preference here.

Run green, commit.

### Step 5. Failing test — `info` reports the signature breakdown

Add `test_cli_info_reports_signature_breakdown` using `capsys`: under `--fold-scheme folio`
the output contains a signature count, a sheet count and a blank count; under the default
`--fold-scheme none` it does not print a signature line at all (`signatures == ()` must not
be reported as "0 signatures" noise on the MVP path).

### Step 6. Implement the `info` breakdown

In `_cmd_info`, after `plan` is computed, print the breakdown when `plan.signatures` is
non-empty. Emit a line that greps cleanly, e.g. `signatures: 17` / `sheets: 67` /
`blanks: 2`.

Run green, commit.

### Step 7. Failing test — the end-to-end folio flow

Create `tests/test_integration_signatures.py` with
`test_end_to_end_folio_impose_export_reopen_and_print`. Model it on
`tests/test_integration.py::test_end_to_end_import_arrange_layout_preview_export_print`,
including its `_StubPrintBackend` and `_isolated_session_state_dir` fixture patterns. Drive:

1. Load a numbered **32-page** fixture (generate it in `tmp_path` if one is not committed).
2. `LayoutSettings(paper=(792.0, 612.0), fold_scheme="folio", sheets_per_signature=4)`.
3. `SaddleStitchStrategy().impose(pages, settings)` → assert **8 sheets** and **2 signatures**.
4. `export(plan, out_path)` → reopen with `pikepdf.open`.
5. Assert **16 PDF pages** (one per `Side`) and **two placements per page** — parse the
   coalesced content stream and count `Do` operators. `page.Contents` may be an `Array`;
   call `contents_coalesce()` before `read_bytes()`.
6. `plan_passes(plan, profile, sheets=plan.signatures[1].sheet_indices)`.
7. Submit through the stubbed `PrintBackend` and assert the received sheet order covers
   exactly signature 1's sheets and no others.

### Step 8. Implement nothing; make it pass

This test should pass against SS-08 + SS-09 + SS-11 as delivered. **If it does not, the defect
is in the sub-spec that owns the failing assertion, not here.** Fix it there, or escalate if
it is a `printing.py` / `profiles.py` diff.

Commit.

### Step 9. Failing test — the fold round trip over the *real* exported plan

Add `test_fold_reading_order_closes_over_the_exported_plan`: on the same plan built in Step 7,
assert `fold_reading_order(plan) == list(range(32))`.

**This is the highest-value assertion in the feature.** It asserts physical intent, not a
computed value, and `fold_reading_order` is written from the physical fold description with no
reference to `saddle_order` (SS-05, enforced by an AST test). If it fails and the cause is not
an obvious one-sided bug, **stop and escalate** per the master spec's Escalation Triggers.

### Step 10. Failing test — no orphaned core modules

Add `test_signatures_and_marks_are_actually_invoked_during_a_folio_impose_and_export`: patch
the public functions of `deckle.core.signatures` (`split_signatures`, `saddle_order`) and
`deckle.core.marks` (`sewing_stations`, `signature_order_mark`, `fold_line`) with recording
wrappers, run a full folio impose **and** export, and assert each recorder fired. Also assert
`deckle.core.marks`' output reaches `deckle/core/export.py` — the marks drawn must be the
`Mark` objects `marks.py` produced, not re-derived in the exporter.

This is the core-module analogue of the existing orphan-view test.

### Step 11. Extend `docs/CONTRIBUTING.md`

Its §2 is currently *"The four-level page vocabulary"*. Extend it to the six-noun model:
**source → output → side → sheet → signature → pass**, naming `side` and `signature` as the
two nouns v2 adds. Add a short section stating:

- `deckle/core/printing.py` and `deckle/core/profiles.py` are **zero-diff**, pinned by a
  SHA-256 test in `tests/test_seam_zero_diff.py`; regenerating a pin is an **escalation**,
  not a maintenance chore.
- `paper_thickness_pt` is **advisory only**. It reaches the `creep_advisory` warning path and
  nothing else — never placement geometry. Enforced by an AST test confining every reference
  to `_creep_advisory`.

### Step 12. The four constraint gates, the full suite, and lint

```bash
python -m pytest tests/test_license_audit.py tests/test_core_purity.py tests/test_seam_zero_diff.py tests/test_golden_pinebox.py -q
python -m pytest -q
python -m ruff check deckle tests tools
```

`ruff check … tools` requires `tools/` to exist (SS-10). Commit.

## Interface Contracts

### CLI folio flags
- Direction: SS-12 → `deckle.core` only
- Owner: SS-12
- Shape: `--fold-scheme {none,folio}` (default `none`), `--sheets-per-signature INT`
  (default 4), `--blank-mode {end,balanced}` (default `end`), `--sewing-stations INT`
  (default 3), on `impose`, `export` and `info`. Values map straight onto the committed
  `LayoutSettings` fields with no translation.
- Constraint: `deckle/cli.py` imports **only** `deckle.core`. No `deckle.app`, no Qt binding.

### `info` signature breakdown
- Direction: SS-12 → the operator, and to SS-13's fixture preparation
- Owner: SS-12
- Shape: prints a signature count, sheet count and blank count when `plan.signatures` is
  non-empty; prints no signature line when it is empty.

### The round trip
- Direction: SS-05 ↔ SS-08, closed here
- Owner: SS-12 asserts it; SS-05 owns `fold_reading_order`
- Shape: `fold_reading_order(plan) == list(range(len(pages)))` for the exported 32-page
  folio plan. **Frozen semantics** — a disagreement is an escalation, never a simulator edit.

### Unchanged and frozen
- `deckle/core/printing.py`, `deckle/core/profiles.py`: zero diff.
- `LayoutStrategy.impose`'s signature.
- The `.deckle` project format — `SheetPlan` is never persisted.

## Verification Commands

```bash
python -m pytest tests/test_integration_signatures.py -q
python -m pytest tests/test_cli.py tests/test_integration.py -q
python -m pytest tests/test_license_audit.py tests/test_core_purity.py tests/test_seam_zero_diff.py tests/test_golden_pinebox.py -q
python -m pytest -q
python -m ruff check deckle tests tools
```

## Checks

Every command exits **0** on the passing case, and each was executed in its unescaped form
against the current tree before being written here.

**Markdown escaping:** `|` inside a table cell is written `\|`. Unescape before running:
`\|\|` is the shell's `||`; `\|` inside a `grep -E` pattern is `|` (alternation).

**Loop form:** loops use `|| { echo …; exit 1; }` with **braces, not a subshell**. A
`|| ( … exit 1 )` inside a `for` loop exits only the subshell, so the loop continues and its
status is that of the *last* iteration — a missing middle token passes silently. Verified
empirically. Do not convert these.

| Criterion | Type | Command |
|---|---|---|
| CLI accepts the four folio flags | [MECHANICAL] | `for f in --fold-scheme --sheets-per-signature --blank-mode --sewing-stations; do python -m deckle.cli info --help \| grep -q -- "$f" \|\| { echo "FAIL: deckle info does not accept $f"; exit 1; }; done` |
| The same flags on `export` and `impose` | [MECHANICAL] | `for c in export impose; do python -m deckle.cli $c --help \| grep -q -- "--fold-scheme" \|\| { echo "FAIL: deckle $c does not accept --fold-scheme"; exit 1; }; done` |
| `info` under folio prints the breakdown | [MECHANICAL] | `python -m deckle.cli info tests/fixtures/sample.pdf --fold-scheme folio --sheets-per-signature 4 \| grep -qiE "signature" \|\| (echo "FAIL: info printed no signature breakdown" && exit 1)` |
| `info` prints counts for signatures, sheets and blanks | [MECHANICAL] | `OUT="$(python -m deckle.cli info tests/fixtures/sample.pdf --fold-scheme folio --sheets-per-signature 4)"; for t in signature sheet blank; do echo "$OUT" \| grep -qi "$t" \|\| { echo "FAIL: info output omits '$t'"; exit 1; }; done` |
| `export` under folio produces a readable PDF | [MECHANICAL] | `OUT="$(pwd)/.deckle-check-tmp" && mkdir -p "$OUT" && python -m deckle.cli export tests/fixtures/sample.pdf -o "$OUT/sig.pdf" --fold-scheme folio --sheets-per-signature 4 >/dev/null && python -c "import pikepdf,sys; pikepdf.open(sys.argv[1])" "$OUT/sig.pdf" && rm -rf "$OUT" \|\| (echo "FAIL: CLI folio export did not produce a readable PDF" && rm -rf "$OUT" && exit 1)` |
| No app or Qt import in the CLI (REQ-040) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(deckle\.app\|PySide6\|PyQt)" deckle/cli.py \|\| (echo "FAIL: app or Qt import in the CLI" && exit 1)` |
| End-to-end folio flow: 8 sheets, 16 PDF pages, two placements per page (REQ-010, REQ-032, REQ-035) | [INTEGRATION] | `python -m pytest tests/test_integration_signatures.py -q -k end_to_end_folio_impose_export_reopen_and_print` |
| Fold round trip closes over the exported plan (REQ-019) | [INTEGRATION] | `python -m pytest tests/test_integration_signatures.py -q -k fold_reading_order_closes_over_the_exported_plan` |
| No orphaned core modules (REQ-021, REQ-031) | [INTEGRATION] | `python -m pytest tests/test_integration_signatures.py -q -k signatures_and_marks_are_actually_invoked` |
| `layout.py` imports `signatures` and `marks` | [STRUCTURAL] | `for m in signatures marks; do grep -qE "^from deckle\.core\.$m import\|^import deckle\.core\.$m" deckle/core/layout.py \|\| { echo "FAIL: deckle/core/layout.py never imports deckle.core.$m -- orphaned module"; exit 1; }; done` |
| `export.py` consumes `Mark` | [STRUCTURAL] | `grep -qE "^from deckle\.core\.models import .*Mark" deckle/core/export.py \|\| (echo "FAIL: export.py does not consume Mark -- marks are computed but never drawn" && exit 1)` |
| Zero failures, no fewer than 237 tests collected (REQ-039) | [MECHANICAL] | `python -c "import subprocess,sys,re; out=subprocess.run([sys.executable,'-m','pytest','-q','--collect-only'],capture_output=True,text=True).stdout; m=re.search(r'(\d+) tests? collected',out); n=int(m.group(1)) if m else 0; print('collected',n); sys.exit(0 if n>=237 else 1)" \|\| (echo "FAIL: fewer than 237 tests collected" && exit 1)` |
| Full suite green — skips are expected, failures are not (REQ-039) | [MECHANICAL] | `python -m pytest -q` |
| The four constraint gates in one command (REQ-009, REQ-015, REQ-033, REQ-040) | [MECHANICAL] | `python -m pytest tests/test_license_audit.py tests/test_core_purity.py tests/test_seam_zero_diff.py tests/test_golden_pinebox.py -q` |
| `CONTRIBUTING.md` documents the extended page model, the seam rule and the advisory rule | [STRUCTURAL] | `for t in signature side "zero-diff" paper_thickness_pt advisory; do grep -qi -- "$t" docs/CONTRIBUTING.md \|\| { echo "FAIL: docs/CONTRIBUTING.md does not document '$t'"; exit 1; }; done` |
| Lint clean across shipped code, tests and tools (REQ-039) | [MECHANICAL] | `python -m ruff check deckle tests tools` |

**Note on `ruff`:** the executable is not on `PATH` in this environment's Git Bash;
`python -m ruff check …` is the invocation that works and was verified to exit 0 on the
current tree for `deckle tests`. Adding `tools` requires SS-10 to have landed.

**Note on the test-count criterion:** it asserts **collected ≥ 237** and, separately, that
`python -m pytest -q` exits 0. It deliberately does **not** assert 237 *passed*: 3 tests skip
by design on a machine without the Pinebox fixture, and the current tree reports
`234 passed, 3 skipped`.
