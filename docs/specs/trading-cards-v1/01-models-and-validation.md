# Step 01 — Models, validation, and project routing

Dependencies: none. Requirements: R01, R03, R07, R11.

## Read first

architecture.md; `deckle/core/models.py`, `schema.py`, `defaults.py`; `tests/test_models.py`, `test_schema.py`, `test_core_purity.py`, `test_layout_field_types.py`.

## Deliverable and files

Extend models.py exactly as architecture.md specifies. Create `deckle/core/cards.py` with validation and resolution helpers, and `deckle/core/planning.py` with content/source helpers and book dispatch. Add `tests/test_card_models.py`. The card branch of impose_project is connected to the real planner in step 04; do not expose a card output command before then. No GUI or storage change in this step.

## Work sequence

1. Add frozen card dataclasses before Project. Keep new fields appended with defaults so existing Project and OutputPage construction works. Rectangle values use `(x,y,width,height)`, not opposite corners.
2. Validate at public construction/edit/load boundaries through `validate_card_deck`; simple dataclasses need not duplicate the complete validator. Reject duplicate/empty asset or card IDs, unknown asset references (including retained individual backs), invalid face rotations, invalid mode/fit strings, and malformed tuple members.
3. Require finite numbers, reject bool in numeric fields, positive width/height, nonnegative margins/gaps/bleed, and `0 <= border_width_pt < min(width,height)/2`. Validate saved border values even when disabled. copies must be an integer from 1 through 999. Reject NaN/infinity and zero/negative paper dimensions in planning. Add a named maximum of 10,000 active copies; validate the total before expanding anything. Empty decks and incomplete backs are allowed for editing and saving. back_mode=shared with shared_back=None is an editable incomplete state, not a structural error. Validate that non-None shared_back references an existing asset even when individual mode makes it inactive.
4. Validate CardAsset.page as a single imported page with index 0, positive finite dimensions, rotate_deg=0, skipped=False, nonempty PDF path and valid SHA-256. Original path is absolute import provenance; original_sha256 is valid SHA-256. File existence is an I/O concern, not a dataclass validator.
5. Define resolve_card_back according to mode. Define card_output_problems to name empty/all-skipped decks and unresolved active entries using the front asset filename. Do not treat unused back assets as an error.
6. Implement project_has_content for save/recovery/menu purposes independently of printable readiness. Implement project_source_pages to include all assets exactly once by (path,page_index), preserving first occurrence. Do not flatten pairs into Project.pages.
7. Route book projects to existing GutterShiftStrategy or SaddleStitchStrategy using the existing fold_scheme decision. Require kind/deck/pages invariants at dispatch. Consumers migrate later, so existing callers remain functional throughout this step.

## Acceptance tests

- Existing three-argument Project and five-argument OutputPage constructions retain their values; no Qt import enters core.
- All model defaults equal the architecture table. Frozen edits fail; collections are tuples.
- Parameterized validation covers bool, float copies, copies=0/1000, NaN, infinity, negative gaps, width=0, border exactly half a card, duplicate IDs, bad rotations, and dangling references.
- Empty deck validates but cannot output. An unmatched skipped card does not block a matched active card. 10,000 active copies passes; 10,001 fails before allocation.
- Shared mode resolves the shared back and leaves stored individual mappings intact; switching modes restores those mappings.
- project_has_content returns true for unpaired and all-skipped decks. project_source_pages includes a reused back once, plus unused assets, and preserves book blank handling.

## Gate

Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_card_models.py tests/test_models.py tests/test_schema.py tests/test_core_purity.py tests/test_layout_field_types.py`. Record any absent equivalent test path before substituting the current one. No unfinished public output path may be exposed.
