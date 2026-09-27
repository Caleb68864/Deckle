# Step 03 — Pairing and pure deck operations

Dependencies: 01–02. Requirements: R02, R03, R07.

## Read first

architecture.md; new card models/importer; `deckle/app/state.py` mutation and undo patterns. This step stays in core and requires no widgets.

## Deliverable and files

Create `deckle/core/card_pairing.py` and `tests/test_card_pairing.py`. Public matching API: `suggest_card_pairs(fronts, backs, method)` returns a frozen PairingProposal containing `matches: tuple[tuple[str,str],...]` of asset IDs, `unmatched_front_ids`, `unused_back_ids`, and `ambiguous_keys` tuples. method is 'filename' or 'order'. Suggestions never mutate a deck. Pure edits in cards.py return a new CardDeck.

Required edits: append_fronts, set_shared_back, set_back_mode, assign_back, apply_pairing, materialize_shared_back, reorder_cards, set_card_copies, set_card_skipped, set_face_rotation, remove_cards. Parameter names may follow local style; semantics below are binding. Each validates before returning and preserves unrelated IDs/objects. ID generation occurs only when entries/assets are first added.

## Pairing rules

Filename key: Unicode NFC normalization, casefold, remove the final file extension, then strip **one** trailing `_front`, `-front`, `_back`, or `-back`. Do not strip embedded tokens, digits, spaces, or words without a delimiter. Match only a key with exactly one front and one back. Detect duplicates on either side; leave that key unresolved and report it. Example: `Goblin_front.PNG` matches `goblin-back.jpg`; `goblin_front_copy.png` does not automatically match `goblin.png`.

Order matching zips the naturally sorted input lists up to the shorter length. Explicitly report all remaining fronts and unused backs. No cycling, broadcasting a one-element back list, or truncating cards. A single common back is selected through shared mode.

apply_pairing applies proposal matches only to currently unresolved entries by default. Replacing existing mappings requires an explicit `replace_existing=True` action invoked after a UI confirmation naming the number of mappings replaced. A proposal references front asset IDs; for duplicated entries sharing a front, it proposes the same back for each. Manual mapping is authoritative until an explicit bulk replacement. A user can assign any imported asset as a back to multiple cards.

## Edit semantics

- append_fronts adds new entries in import order with one copy, not skipped, unresolved back. Shared mode automatically resolves the shared face without changing individual back fields.
- Switching mode preserves both the shared face and individual maps. materialize_shared_back requires a non-None shared face, assigns it to all entries (including skipped), and selects individual mode in one edit.
- Reordering accepts a complete permutation of card IDs; reject duplicates, omissions, or unknown IDs. Removing cards leaves assets in the library; asset garbage collection is deferred.
- Rotations are stored on CardFace, never on shared CardAsset.page. Rotating a shared back is a shared-mode operation; a per-row back rotation in shared mode is disabled with a short explanation.
- Assigning a back may replace an existing mapping directly through deliberate selection. Clearing sets None and makes the row unresolved. User changes to front rotation never affect its paired back.
- Copies and skip operate on entries, not expanded slots. Skip is reversible. Remove doesn't touch other entries with the same source.

## Acceptance tests

- Exact, suffix, case, Unicode-normalization, ambiguous duplicate stems, missing back, surplus back, and zero-match proposals have explicit expected ID lists.
- Natural order for 1,2,10 is asserted from literal filenames. Input length 5/3 yields three suggestions, two unmatched fronts, no hidden deletion; 3/5 yields two unused backs.
- Reorder, copies, skip/unskip, remove, and face rotation preserve front/back identity. Reused backs stay shared by reference but can have independent per-entry rotations.
- Mode round trip restores prior individual pairings. Materializing a shared back is one reversible value change.
- Applying a suggestion without replacement preserves an existing manual pair. Applying an invalid proposal fails without partial state.

## Gate

Run model, importer, and pairing tests. Review that no operation derives current pairs from the back library's current sort order after assignment.
