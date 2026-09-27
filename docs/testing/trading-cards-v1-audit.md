# Trading card feature spec audit

Date: 2026-09-27
Scope: all implementation and test requirements in
[`../specs/trading-cards-v1/index.md`](../specs/trading-cards-v1/index.md),
including its ten numbered steps and cross-cutting test plan. The requested
ten review passes stopped after two consecutive clean passes.

## Pass log

| Audit pass | Result | Findings or evidence |
|---|---|---|
| 1 | Not clean; fixes applied | Found unstable/absent identity for empty unsaved card recovery; unvalidated reorder/remove/pair edit results; unreported nested unknown fields; cut ticks placed against the wrong edge; missing per-folder backs and explicit shared-back selection; missing pair review, thumbnails, previews, drag reorder and active count; missing selected-card preview guide and clipping status; card CLI status/unsupported flag/source overwrite/name gaps; ruler could draw over card art. The first focused run also exposed a test fixture that failed to write its mutated JSON and a CLI test passing an invalid page selector. Added recovery, pairing, import, UI, geometry, CLI, export and warning fixes, plus missing summary and independent geometry checks. |
| 2 | Clean | 37 focused card/model/CLI/UI/integration tests passed; 2 docs builds passed; `compileall` passed; `git diff --check` passed. |
| 3 | Clean | Full suite: 2,591 passed, 22 skipped, 8 existing warnings in 89.83s. |
| 4 | Not clean; fix applied | A final stale-worker review found that an in-flight thumbnail could arrive after switching projects and cache an asset whose ID had been reused. Thumbnail completion and failure callbacks now check current asset identity. This reset the consecutive-clean-pass count. |
| 5 | Clean | Repeated the 37 focused card/model/CLI/UI/integration tests, 2 docs builds, `compileall`, and `git diff --check`; all passed. |
| 6 | Clean | Full suite: 2,591 passed, 22 skipped, 8 existing warnings in 90.55s. Two consecutive clean passes (5 and 6) achieved; audit stopped before pass 7. |

## Fixes completed during the audit

- Give each unsaved card project a stable unique autosave path, including an
  empty deck; preserve that path when reopening its recovery file.
- Validate reorder, remove and pairing helper input/output; warn when nested
  card asset or entry fields are unknown.
- Put cut ticks 1 pt outside the effective bleed edge and add an independent
  generated grid oracle and long-edge duplex center simulator.
- Support folder imports for individual backs and a single image for shared
  back mode; present proposed matches for review, distinguish applying only
  missing pairs from replacing existing pairs, and reject stale import results.
- Add asynchronous thumbnails, selected face previews, drag reorder and the
  active card copy count. Limit the thumbnail cache to 80 entries and report
  thumbnail rendering failures. Reject stale thumbnail results if the
  project changes or an asset ID points at another source while rendering.
- Show selection/guide state and clipping warnings in preview, including
  cut-mark bounds. Refuse card-art ruler overlays.
- Report ready/incomplete status in CLI info, reject book-only card flags,
  protect source files from output overwrite, and use plural card pass names.
- Correct and extend tests whose setup/argument errors masked the expected
  warning and unsupported-flag behavior.

## Outstanding verification

The software suite and spec audit are clean. Physical printer verification is
still unverified: no actual-size duplex paper trial was performed. This audit
does not claim that physical alignment, printer imageable margins, driver
scaling, or cut registration passed.
