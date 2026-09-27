# Step 10 — Integration, user documentation, and physical trial

Dependencies: 01–09. Requirements: R01–R12.

## Read first

All files in this spec package; `tests/conftest.py`, `tests/gui_workflow.py`; `docs/GUIDE.md`, README.md, CHANGELOG.md; existing documentation/build and packaging tests.

## Deliverable and files

Add `tests/test_cards_integration.py`, reusable generated fixtures under the normal test helpers, and `docs/testing/trading-cards-v1-results.md` with actual outcomes. Update user guide, feature overview, changelog, and relevant API documentation. Do not mark paper verification as passed until a person actually performs it.

## End-to-end automated scenario

1. Generate ten asymmetrical front images and at least three different backs, with visible IDs, upward arrows, and different corner colors. Generate one mismatched filename and a duplicate stem to force manual pairing.
2. New card deck; import fronts; use one common back; print/export readiness becomes valid. Switch to individual mode; run filename pairing review; resolve missing/ambiguous pairs manually; assign one back to several fronts.
3. Reorder entries, rotate different faces, make copies of one card, skip another; verify resolved identities using literal expected IDs. Enable border at two widths and bleed values; assert finished dimensions remain constant.
4. Save, close, reopen; compare pairs/settings/plan digest. Make an unsaved pair edit, simulate recovery, verify dirty state and recovered mapping. Undo/redo one bulk assignment.
5. Export canonical PDF and front/back profile passes; inspect page dimensions, sheet count, empty slots, ink boundaries, marks, front/back physical mapping. Feed the same plan to the recorded print backend; verify output and resume checks.
6. Run equivalent CLI info/export on the saved project. Reopen a legacy book and run a flat and folio export to catch routing regressions.
7. Exercise incomplete deck and corrupt/missing imported source failures: save allowed where structural state is valid, output blocked, no stale preview or partial destination.

## Automated release gate

Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` from repository root after targeted checks pass. Use the repo's documentation build command and existing packaging validation when those assets are available. Record commit, environment, commands, pass/fail/skip counts, and reasons for skips. Do not reuse README's historical count as a measured result.

Include a 500-card shared-back smoke run: all pages generated, no missing assets, no unbounded per-card open PDF handles, and UI import/preview workers remain responsive. Use existing cache-budget/resource testing conventions; avoid arbitrary time limits that fail on slow CI. Verify no newly declared dependency and no Qt import in CLI/core.

## Physical test procedure (manual, not a CI dependency)

1. Use cheap paper first. Create six or more cards with matching large IDs on both faces, upward arrows, four different corner symbols, and a measured 180×252 pt trim rectangle. Use enough copies for two sheets, one partial.
2. Export/print at actual size/100%; disable driver/viewer fit-to-page. Measure a finished card width and height and record error in mm. Record printer, driver, paper size/orientation, scaling, duplex method, flip edge, profile, and offsets.
3. Print fronts then backs following Deckle's reload instruction, or hardware duplex. Hold each sheet to light: verify every front/back ID, top direction, empty-slot location, and sheet order. Cut against front marks/trim edges and inspect each finished card.
4. Repeat with border enabled at b=9 pt and bleed e=3 pt; record measured residual displacement. All four cut edges should stay black when displacement stays within e and below b. Repeat with a larger bleed and note changed capacity. Toggle border off and confirm no added black remains.
5. Test nonzero profile correction with measured displacement, then test a controlled exported back displacement on sacrificial paper. Verify correction direction and that border/art move together. Test portrait and landscape if supported; do not infer one from the other.
6. Reprint a selected physical sheet; confirm its matching back. Perform test-one-sheet before a full run.

Record each criterion as pass/fail/unverified and attach permitted local evidence paths if available. Skew/scale and cut error observations are recorded separately from translation. If no printer/operator is available, mark physical verification **unverified**, complete the software gate, and report the remaining physical check plainly. Never report that black borders guarantee alignment on all printers.

## User documentation checklist

Explain front folder, shared versus individual backs, matching rules and ambiguity review, manual pairing, reused backs, copies/skip/reorder, units, default card size, and contain versus Fill (crop). Show inside border versus beyond-cut allowance with a small diagram. State default Letter capacity six/A4 capacity nine with current defaults and that turning bleed off can increase capacity. Explain front-only cut marks and capacity cost.

Distinguish canonical combined PDF from profile-specific passes and actual print path. Give 100% printing guidance, paper/flip selection, existing offset calibration, and test-one-sheet advice. Explain that images are imported snapshots held in a local store, that .deckle does not embed artwork, and that old builds cannot safely edit card projects. Name current limitations without advertising deferred features.

## Handoff completion record

Report implementation commit(s), automated results, manual paper status, known failures/limitations, and requirement coverage. A failing pairing, clipping, or physical orientation test blocks software acceptance; an unavailable printer is an explicitly recorded verification limit, not an invented passing result.
