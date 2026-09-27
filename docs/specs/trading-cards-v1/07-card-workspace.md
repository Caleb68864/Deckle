# Step 07 — Desktop card import, pairing, and settings

Dependencies: 01–06. Requirements: R01, R02, R03, R05, R07.

## Read first

architecture.md; `app/main.py`, `state.py`, `menus.py`, `project_actions.py`; `views/import_view.py`, `arrange_view.py`, `layout_panel.py`; existing menu, arrange, and autosave GUI tests.

## Deliverable and files

Create `deckle/app/views/cards_view.py` for asset/pair editing and `card_layout_panel.py` for card settings. Integrate into the shell using the existing patterns. Add `tests/test_cards_view.py`, `tests/test_card_layout_panel.py`, `tests/test_card_workspace.py`. Use real widgets in offscreen Qt tests where user behavior depends on signals.

## User flow

1. File > New card deck uses existing unsaved-document handling. Create empty CardDeck with defaults and kind='cards'. Keep New book available. Opening a project selects its workspace from kind.
2. Show “Import front folder…” and back choice “One shared image” / “Individual backs.” Individual import offers folder or multiple selected files. Existing drag/drop of .deckle still opens projects; in card workspace an image/folder drop presents Fronts / Backs / Cancel. It must not use book import logic.
3. New front import appends entries by default. Offer explicit “Replace all fronts…” with affected card count; confirming clears entries, retains reusable assets/back mode/settings, and creates new entries. Cancel preserves all work.
4. Pair table: position, front thumbnail + original filename, back thumbnail + original filename (or “Missing back”), copies, skip. Selection shows both faces larger, with separate rotate controls and a back picker searchable by original filename. The shared back is clearly labeled as shared; no independent row back editing is enabled in that mode.
5. “Match by filename” and “Match by order” open a proposal review showing matched/unmatched/ambiguous/unused counts and rows. Apply commits one undo step. Preserve existing pairs by default; replacing them is an explicit choice with a count. Unmatched rows remain visible and editable.
6. Support move up/down and drag reordering of whole rows; copy count 1–999; skip/unskip; remove selected entries. One asset may be assigned to several backs. Show active physical card count separately from entry count.
7. Card layout panel: paper size/orientation using existing paper controls; finished width/height with inch/mm/pt input; symmetric horizontal/vertical minimum margins; horizontal/vertical gaps; Fit entire image / Fill (crop); Black border checkbox; inside width; beyond-cut allowance; Cut marks checkbox. Show cards per sheet and total sheets computed from core.
8. Border off disables width/allowance fields but keeps values. Explanation: “Black beyond the cut helps cover small front/back shifts. More allowance may fit fewer cards on a sheet.” Show the default 3 pt allowance as approximately 1.06 mm when units are mm. No promise that the setting calibrates the printer.

## State and responsiveness

All user changes go through AppState.mutate using pure helpers. A multi-row operation is one undo action. Invalid edits leave the last valid model unchanged and show the field error. Widget refresh must block signals to prevent phantom edits/autosave loops. Unit display conversions never round the stored values on refresh.

Import/conversion and thumbnail generation run using the app's worker conventions, not in a blocking UI callback. Cancelled/failed/stale worker results cannot replace a newer project; capture project generation and ignore results from a closed/replaced workspace. Commit assets plus entries atomically. Disable only conflicting import actions while a worker is active; preserve window responsiveness.

Hide book-only controls, schedule, fold, and sewing actions in card workspace. Existing book tabs and import flow retain behavior. Share printing, project saving, and the preview shell. Do not repurpose book Arrange to show the internal asset PDF page order.

## Acceptance tests

- Create/import fronts/shared back, then switch to individual, review filename proposal, manually fix one missing pair. Table and model agree on filenames and backs.
- Shared back changes update every effective back; mode switch preserves individual mappings. Reordering and deleting rows never mismatches backs.
- Copies/skip change physical count; undo/redo restores the entire operation. Border toggles preserve saved values and finished size; grid count updates.
- Empty/unpaired decks can save; invalid fields do not mutate state. Refresh emits no mutations.
- Cancelling file dialogs, pairing review, replace confirmation, or import preserves current deck; worker completion after switching projects is ignored.
- Opening book/card projects selects the right workspace; hidden book actions cannot be triggered via keyboard in card mode.

## Gate

Run new UI tests plus existing menu, project-action, arrange, and layout-panel tests. Manually inspect one shared-back and one individually paired deck at ordinary window size. Output gating and preview are completed next; do not advertise a working print feature at this step alone.
