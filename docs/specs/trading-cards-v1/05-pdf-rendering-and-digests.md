# Step 05 — PDF borders, artwork clipping, and cache safety

Dependencies: 04. Requirements: R05, R06, R08, R10, R11.

## Read first

architecture.md; `deckle/core/export.py` _place_output_page, _export_batched, _apply_back_offset, export_sheet_cached; `plan_digest.py`; `render.py`; tests for render, export marks, repeated sources, rotated crops, and print-session durability.

## Deliverable and files

Extend OutputPage rendering and output_page_key. Add `tests/test_card_export.py`, `tests/test_card_digest.py`. Keep existing export API and SheetPlan interface. No card-specific PDF exporter or second preview renderer.

## Drawing contract

For each non-filler card OutputPage, emit balanced PDF graphics state operators. Paint bleed_rect solid DeviceGray black (`0 g`) when black_border is true. Paint art_rect opaque white (`1 g`) for predictable transparency/contain letterboxing. Establish a rectangular destination clip using `re W n` around art_rect, then use the existing source placement/rotation. Restore graphics state before the next cell. When border is off, art_rect equals trim_rect and no external black is painted. Filler pages draw nothing.

Card clip applies in sheet coordinates outside the source's transformation. Do not set the whole PDF page's TrimBox to one card; sheet page boxes remain the paper dimensions. Do not change source CropBox for card cover fitting. Avoid mutating cached source pages when reusing one back image across many slots.

Draw card cut ticks at 0.25 pt while preserving the existing book mark styling. Pass a private optional cut-line width override to _draw_marks for card faces (detect at least one non-None OutputPage.card); do not globally change Mark(kind='cut_line') styling. Partial card sides always contain at least one decorated page, so no extra plan kind flag is required.

Preserve order: all face artwork, then front-only ticks; then existing whole-back registration translation and pass rotation. Border and art must move together. Existing print offsets must not move the art alone. Canonical export has zero correction by default.

## Digest contract

Append a length-framed card decoration contribution only when page.card is non-None. Include every rectangle component, black flag, card_id, copy_index; placements and sources are already hashed. Keep legacy page key bytes unchanged for card=None. All rendered changes invalidate export caches and print resume. Changing only asset label/provenance without changing the plan need not invalidate the render cache. A new shared back does invalidate it for every affected slot.

## Test oracles

Use generated PDFs/images with asymmetric colored corner patches and an upward arrow/letter. Rasterize the exported PDF using the existing rasterizer; do not compare a second hand-drawn layout to itself.

- At 144 DPI, sample points comfortably away from antialiased edges: exterior bleed black; all four inside border edges black; white contain bars; art landmarks correctly oriented; neighboring cell/gutter white; unused cells white.
- Cover crops oversized artwork to art_rect and never overwrites border or neighbor. Contain preserves all four source landmarks. Transparent PNG shows white within the art rectangle and black outside it.
- Rotate 0/90/180/270 on a shared source in different cells; all show the expected landmarks and do not accumulate crop/rotation state.
- At 720 DPI, a 0.5 pt interior sample validates border on each edge; physical PDF MediaBox and trim dimensions remain exact to 1e-6 pt. Pixel assertions allow at most one-pixel antialias tolerance at boundaries and never use exact PDF byte equality for new artwork.
- Export multiple sheets, a selected sheet subset, front/back passes, and a partial final sheet. Pages are exactly the declared paper size and expected count.
- Digest changes independently for border enable, width, bleed, clip, mark, paper, face rotation, pairing, copies, order, and source SHA. Saved/reopened equivalent plans have identical digests. Existing book keys stay unchanged.
- Cache first preview, change only bleed or a shared back, preview again: raster must change. Interrupted print resume rejects changed artwork/settings through existing StaleSessionError behavior.
- Simulate back translation (2,-1) pt inside 3 pt bleed; front-defined cut perimeter remains black. Translation outside tolerance produces an uncovered strip in the synthetic fixture; tests must not imply unlimited protection.

## Gate

Run new export/digest tests with existing `test_export_marks.py`, `test_render.py`, `test_repeated_source_page.py`, `test_crop_rotated_source.py`, `test_print_session_durability.py`. Validate actual ink, not merely that a new field was serialized or an export function was called.
