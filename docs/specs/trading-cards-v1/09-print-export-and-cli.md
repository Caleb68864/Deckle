# Step 09 — Duplex printing, pass export, and headless access

Dependencies: 06–08. Requirements: R04, R08, R09, R10, R11.

## Read first

architecture.md output contract; `core/printing.py`, `print_session.py`, `profiles.py`, `app/backend.py`, `views/print_dialog.py`, `app/exporting.py`; `cli/options.py`, `commands.py`, `report.py`; tests for duplex submission, pass exports, output-command parity, and CLI projects.

## Deliverable and files

Connect cards to existing full PDF, pass export, proof/test-one-sheet, manual duplex, hardware duplex, subset/reprint, and session resume. Add `tests/test_card_printing.py` and `tests/test_card_cli.py`. Do not create a second card-specific pass scheduler or alter plan_passes' Sheet.index-only dependency.

## Print behavior

- Whole-sheet pass order and 180° rotation are owned solely by existing printing.py. Card planner mirrors within-sheet column positions exactly once. Hardware duplex uses existing duplex_flip_edge: long for portrait, short for landscape.
- Use pass_export for profile-specific front/back PDFs and the same arguments for desktop/CLI. A portable combined PDF interleaves front/back sheets and is uncorrected by default, as in architecture.md. UI/CLI report this clearly; pass export reports reload instructions and selected profile/correction.
- Maintain back registration translation of the complete side including border; preserve _apply_back_offset sign reversal for a subsequently rotated back pass.
- Reprint sheet k includes its fronts and paired backs, not entry k or PDF page k. Partial sheets retain fillers; no omission changes later stack alignment.
- Existing proof ruler placement can overlap card ink because book layouts assume available space. Audit it: for cards, place a ruler only in a verified unused sheet region, or use the existing separate calibration sheet. The actual card test-one-sheet flow must print an unchanged selected card sheet. Never silently draw a measurement ruler across card artwork.
- Resume rejects changed source, pair, card rotation, layout, copies/order, or printer correction. Profile identity validation remains in existing session code.

## CLI scope and exact public behavior

Support card `.deckle` inputs through existing `info`, `impose`, `export`, and `print` commands with their current source/output argument conventions. The CLI loader currently returns only pages/layout in places: retain the full Project through resolution so kind/deck cannot be dropped. Non-project PDF/image-directory inputs keep their existing book interpretation.

No new card creation flags are required in v1; decks are created in the desktop and can be exported/printed headlessly. `info` and `impose` JSON/text reporting must contain a card-specific section with kind, entry_count, active_copy_count, unresolved_count, columns, rows, sheets, and slot mappings by sheet/side/card_id/copy_index/front-label/back-label. Info on an incomplete deck succeeds with status='incomplete', explanatory problems, and null grid fields if dimensions cannot fit. Output commands fail nonzero without traceback or partial artifact when pairing/layout is invalid. Use existing command exit code conventions.

Book-specific project overrides (fold scheme, book margins/crop, signatures, binding, schedule/dummy operations) must not silently change a card deck. Reject explicitly supplied incompatible options with a concise error; distinguish actual CLI arguments from parser defaults. Permit established paper-size/orientation and printer/pass/subset/output options that have clear card meaning; paper override changes project.layout.paper before planning. Document exact existing option spellings after inspecting parser help rather than introducing aliases speculatively.

CLI schedule for a card project returns a friendly unsupported-operation error; a card inventory report is the info section, not a fake binding schedule. Keep CLI Qt-free and packaging dependency boundaries intact.

## Independent acceptance matrix

Use asymmetric labeled cards spanning at least three sheets and an incomplete final sheet. Cross portrait/landscape, long/short manual flip profiles, reverse_stack true/false, nonzero offsets, and sheet subsets. Assert physical pairings after simulating feed/turn operations, not just that frontend/backend agree.

- Each profile's submitted sheet order matches literal expected orders for [0,1,2]; reverse stack gives backs [2,1,0]. A requested subset [2,0] gives front [2,0] and reversed back [0,2] where appropriate.
- Canonical back positions plus profile half turn land the correct back behind each front. Actual pixel orientation stays readable after the physical turn.
- Direct-print recorded backend pages and profile pass exports have equal dimensions, order, rotations, offsets, and pixel content. Hardware duplex requests correct edge for each paper orientation.
- Shared back and mixed individual/reused backs work for partial grids. One-sheet test leaves the rest of the job unsubmitted.
- CLI full export and desktop full export rasterize equivalently from the same saved project. CLI info works for incomplete deck; output fails cleanly. Print tests use recorded backend, never a real printer automatically.
- Interrupted run can resume unchanged; each enumerated material mutation refuses resume. Book print/export tests stay green.

## Gate

Run new CLI/printing tests plus existing duplex, pass, output parity, session, CLI-project, and core-purity suites. Real paper remains a separate step 10 check.
