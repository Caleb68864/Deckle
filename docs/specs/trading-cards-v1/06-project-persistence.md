# Step 06 — Card projects, source checks, undo recovery identity

Dependencies: 01–05. Requirements: R07, R10, R11.

## Read first

architecture.md persistence contract; `core/project_io.py`, `schema.py`, `paths.py`; `app/state.py`, `project_actions.py`; `tests/test_project_file_shape.py`, `test_project_io.py`, `test_autosave_recovery.py`, `test_unsaved_autosave.py`, `test_hardening_io.py`.

## Deliverable and files

Extend existing .deckle reader/writer and card-aware state helpers; add `tests/test_card_project_io.py` and `tests/test_card_recovery.py`. No new project extension. Use version 2 only for card saves and retain existing book serialization shape/version 1. The reader's supported format constant becomes 2; existing tests that use a literal future version must become relative to that constant only where their intent is testing future versions.

## Payload contract

Top level for cards: version=2, kind='cards', pages=[], layout=existing layout JSON, printer=existing nullable string, card_deck=object. card_deck has assets/cards/settings/back_mode/shared_back exactly as architecture.md defines. For CardAsset.page use the existing SourcePage wire format through its established encoder/decoder. CardFace is {asset_id,rotate_deg}; unresolved backs encode null. Serialize all listed CardSettings fields. Booleans must be JSON booleans and copies JSON integers, not permissively coerced strings.

Missing optional settings use model defaults; required identity/reference fields must be present. Unknown card settings or object fields produce an advisory naming the fields, consistent with existing unknown-layout policy. Invalid known values or an unknown kind are errors. A malformed card_deck cannot fall back to a book. Missing kind with card_deck present is an error. A legacy file without either field opens as a book.

## Work sequence

1. Add focused card encoders/decoders, using schema helpers where suitable; validate the completed deck without requiring complete pairing. Serialize primitive dict/list values only.
2. Extend source enumeration to asset PDFs, including unused assets; deduplicate disk/hash checks by PDF path. Continue existing outside-root advisories and user callback behavior. With check_sources=False, structural validation still runs but no source disk validation occurs.
3. Verify converted-source SHA on normal load. Missing/changed PDF errors name the file and present original asset label in UI context when possible. Original image changes are not live changes; the UI's explicit reimport replaces a selected face only after successful import.
4. Preserve atomic project writes. Introduce a card-aware unsaved recovery key based on project kind and stable deck entry/asset identities, so pages=[] cannot disable autosave or make every deck collide. Retain the current helper behavior for books; add a project-level wrapper and migrate autosave callers. A never-saved deck keeps its initial recovery identity through later edits; only successful explicit save/clean discard removes it.
5. Ensure project action missing-source relink traverses asset PDFs, not only Project.pages. Relink a missing conversion to an identical PDF only after hash validation. Reimporting a changed original is a separate user edit, not silent relinking.
6. Undo/redo retains the complete Project object, including card settings, mappings, and mode. An autosave write does not clear dirty state. Recovery offers and recent-project labels identify card decks and entry counts.

## Acceptance tests

- Round-trip an incomplete deck and a complete mixed deck with shared and retained individual backs, copies, skips, rotations, non-default dimensions and border off with saved positive widths. After reopen, resolved pairs and plan digest equal pre-save values.
- Legacy version 1 book loads and saves with its existing shape; positional Project calls still work. A future version emits the existing advisory. An unsupported kind fails explicitly.
- Invalid JSON shapes, duplicate IDs, missing asset, bool copies, NaN numbers, invalid rotations, and incompatible kind/pages/deck combinations fail with actionable messages.
- Missing original image still loads from imported PDF; missing/changed converted PDF fails; a shared PDF is hashed once. check_sources=False avoids source reads.
- Two different never-saved decks have distinct recovery records; edits to one update its existing record. Pair-only and border-only edits become dirty, undo and redo correctly, and recover after restart.
- Interrupted/failing save leaves the old file intact. Source-relink cancel does not mutate current project. Relink cannot silently replace an image with different content.

## Gate

Run new project/recovery tests plus existing project, shape, atomic-write, autosave, and newer-version tests. Document old-build limitations in step 10; do not modify the repository's established future-version advisory policy for books.
