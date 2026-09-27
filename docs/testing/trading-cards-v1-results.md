# Trading card sheets v1 implementation and verification

Date: 2026-09-27
Environment: Linux, Python 3.14.7, repository `.venv`, Qt offscreen.

## Implementation

The feature follows the design, model, import, pairing, geometry, rendering,
persistence, desktop, preview, print/export/CLI, and integration specs in
[`../specs/trading-cards-v1/index.md`](../specs/trading-cards-v1/index.md).
Card projects retain their own planner and schema version while sharing the
existing sheet, PDF export, preview, printer profile, and manual-duplex
pipeline. No runtime dependency was added.

Implemented flows include new card decks, folder-imported fronts, shared or
individual backs, filename/order pairing proposals, manual face assignment,
copy and skip counts, row moves, face rotation, card-size and sheet controls,
contain/crop fit, inside border and outside bleed allowance, cut marks,
two-sided sheet preview/export, project save/reopen, output gating, profile
passes, and headless CLI info/export. The workspace shows asynchronous face
thumbnails and selected front/back previews, supports drag reordering, and
reports thumbnail failures through the status advisory.

## Automated checks

Commands run from the repository root:

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider
2592 passed, 22 skipped, 8 warnings in 90.15s (latest full run)

QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_card_recovery.py tests/test_card_project_io.py tests/test_card_pairing.py tests/test_card_layout.py tests/test_card_layout_properties.py tests/test_card_export.py tests/test_card_cli.py tests/test_card_workspace.py tests/test_cards_integration.py tests/test_card_import.py tests/test_card_digest.py tests/test_card_models.py
37 passed

QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_docs_build.py
2 passed

.venv/bin/python -m compileall -q deckle tests
passed

git diff --check
passed
```

The full suite includes the card unit/UI/CLI/persistence/export tests and a
500-card shared-back planning smoke test. The card checks also include a
generated grid oracle and an independent long-edge duplex center simulator.
The end-to-end test pairs assets, saves/reopens the project, checks plan and
digest equality, verifies mirrored front/back identity, and opens the
resulting two-page PDF. The Sphinx API build uses warnings as errors. No test
sent a job to a physical printer.

The eight pytest warnings are the existing packaging-test invalid-escape
`SyntaxWarning` and path-outside-project advisory warnings from legacy
autosave tests.

## Physical verification

**Unverified.** No printer trial was performed. Use the physical procedure in
[`../specs/trading-cards-v1/10-integration-and-release.md`](../specs/trading-cards-v1/10-integration-and-release.md)
to check actual-size output, duplex reload/orientation, residual registration,
cut edges, driver scaling, and printer imageable-area limits. The black bleed
allowance addresses uniform translation only; it does not correct skew,
scale, inaccurate cutting, or unprintable margins.

## Release state

Software tests pass. Physical duplex alignment is still unverified. Card
projects use format version 2; older Deckle builds cannot safely edit them.
The subsequent two-pass spec audit and fixes are recorded in
[`trading-cards-v1-audit.md`](trading-cards-v1-audit.md).
