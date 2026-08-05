---
type: plan
topic: Deckle hardening, documentation, and release readiness
date: 2026-08-04
status: draft
---

# Deckle — hardening, documentation, release

Three workstreams, requested together: (1) ten hardening passes so every failure
is either handled or logged debuggably, (2) Sphinx-level documentation that
regenerates as the code changes, (3) release preparation.

## Reconnaissance findings (evidence, not guesswork)

### H-0 — There is effectively no logging

`deckle/core/session_log.py` is the **only** module that imports `logging`.
Every other module in `deckle/` is silent. When something goes wrong at a
customer's desk there is nothing to read.

This is the root of the request and the spine of the plan.

### H-1 — The swallows are correct, but invisible

Seven sites catch and continue. Reviewed individually, **all seven are
deliberate and every one carries a comment justifying it.** This is good code.
The defect is not the control flow — it is that none of them leave a trace:

| Site | Behaviour | What the user sees today |
|---|---|---|
| `app/main.py:48` | Spooler failure → zero printers | Printer list is empty; no reason given |
| `core/loader.py:119` | Non-password open failure → "not encrypted" | Original error surfaces, but the probe's own failure is lost |
| `core/export.py:378` | `os.remove` failure on temp file | Temp files accumulate silently |
| `core/project_io.py:126` | Cross-drive path → not contained | Correct, but a surprising path rejection is unexplained |
| `core/print_session.py:297` | Corrupt/unreadable session JSON → skipped | **A resumable session silently vanishes from the list** |
| `app/views/print_dialog.py:40` | Profile load failure → try next printer | Falls back to `printer_names[0]` with no hint why |
| `app/backend.py:234,386` | Print exception → `PrintResult.error` | Surfaced, but not recorded for later diagnosis |

`print_session.py:297` is the sharpest: a user whose session file got truncated
by a crash — exactly when they most need to resume — sees the session simply not
appear, with no way to find out why.

### H-2 — `deckle export` discards layout warnings

`cli.py:194-200` prints layout warnings, but that block lives in `_cmd_info`.
`_cmd_export` computes a plan and never surfaces `plan.warnings` at all.

Concretely: `deckle export --fold-scheme folio` on portrait paper emits the
`sheet_orientation` warning — *"folio expects landscape paper wider than it is
tall; proceeding with the paper as given"* — and the user never sees it. They get
a 134-side PDF with two pages squeezed onto each portrait sheet and no
indication anything was unusual. Verified by running it.

The same silence applies to `signature_padding`, `creep_advisory`,
`clipped_by_imageable_area`, and `landscape_imageable_unverified`.

### H-3 — Known environment traps to encode as guards

Learned the hard way during the factory and converge runs; all belong in the
hardening pass as real defences, not lore:

- `$TMPDIR` is **empty** in Git Bash on Windows. `"$TMPDIR/out.pdf"` resolves to
  `/out.pdf`, an unwritable MSYS root, and *hangs* rather than failing loudly.
- Printer enumeration on the UI thread hung the app for 81 minutes during a
  network outage, and hung it on launch with an unreachable network printer.
  Already moved to `_PrinterQueryWorker`; needs a timeout and a log line.
- `QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the process (exit 127).

## Workstream 1 — ten hardening passes

Each pass is scoped, verified, and committed separately.

1. **Diagnostic log foundation.** Extend the `session_log.py` convention — JSON
   Lines, `RotatingFileHandler`, OS data dir, Qt-free — into a general
   `deckle.core.diagnostics` logger. Reuse; do not invent a second scheme.
2. **Instrument the seven swallows** (H-1). Every one keeps its behaviour and
   gains a log line with enough context to diagnose. Behaviour change: none.
3. **Surface layout warnings in `export`** (H-2), and audit every other CLI
   subcommand for the same drop.
4. **File I/O and permissions.** Unwritable output paths, missing parents, paths
   on absent drives, read-only targets, disk-full. Fail with an actionable
   message naming the path.
5. **Malformed input.** Corrupt PDFs, zero-page PDFs, encrypted PDFs, empty
   image directories, non-image files in an image directory, mixed/absurd page
   sizes.
6. **Printer and spooler faults.** Enumeration timeout, printer disappearing
   mid-job, offline printer, driver rejection. Never block the UI thread.
7. **Session robustness.** Truncated/corrupt session files, sessions whose
   source document has changed — this is where the frozen REQ-014 decision lands
   once made — and concurrent access.
8. **Resource limits.** Very large documents (the 300-page Traveller book is the
   working case), memory ceilings, cancellation paths, temp-file cleanup on
   abnormal exit.
9. **Cross-platform.** Windows/Linux path handling, CRLF, drive letters, the
   `$TMPDIR` trap, OS data-dir differences, filename character restrictions.
10. **Adversarial sweep.** A fresh pass whose only job is to find what the first
    nine rationalised away — with a bias toward "prove it fails gracefully" by
    execution, not by reading.

**Rule for every pass:** a hardening change must not alter correct-path
behaviour. The neutrality method from `docs/converge/.../neutrality-proof.md`
(compare MediaBox + `cm` matrices against a known-good tree) is the check.

## Workstream 2 — documentation

Deckle's docstrings are already unusually good: they explain **why**, not just
what. `_hash_plan` records why presence-only hashing was insufficient;
`LayoutSettings.margin_outer_pt` explains that the gutter *is* the inner margin.
That voice is an asset.

So: **add Sphinx structure, preserve the prose.** Do not let a generator flatten
existing explanations into `:param x: the x` boilerplate.

- `sphinx` + `autodoc` + `napoleon` + `sphinx-autodoc-typehints`.
- `docs/api/` generated from the source tree; narrative docs stay hand-written.
- A `run.bat docs` target so regeneration is one command.
- Doc build in CI, warnings-as-errors, so drift fails the build.
- Every public symbol gets `:param:`, `:returns:`, `:raises:`. **`:raises:` is
  the one that matters** — it is the contract the hardening passes are creating.

## Workstream 3 — release

- **Packaging** — deferred since the MVP. Windows and Linux artifacts.
- **Licence audit** — already enforced by `tests/test_license_audit.py`; MIT app,
  no AGPL ever. Re-verify against the final dependency set.
- **Calibration wizard (SS-13)** — never built.
- **The folded dummy** — SS-13's physical check remains `manual_pending`. It is
  the only real verification that `saddle_order` folds into a readable book. No
  amount of automated hardening substitutes for folding one. Either do it before
  release, or ship folio marked experimental.
- **CHANGELOG, README, version.**

## Open decisions (author's, not the implementer's)

1. **Stale-session policy** (frozen REQ-014 finding): when a resumed session no
   longer matches the document, should Deckle refuse, warn and continue, or
   offer re-imposition?
2. **Folio release status**: gated on the folded dummy, or shipped experimental?
