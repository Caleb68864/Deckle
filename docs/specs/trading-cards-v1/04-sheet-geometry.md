# Step 04 — Card grid and physical front/back placement

Dependencies: 01–03. Requirements: R04, R05, R06, R08.

## Read first

architecture.md geometry is authoritative; `deckle/core/layout.py` Placement rotation conventions; `models.py`, `marks.py`, `printing.py` duplex_flip_edge; existing `tests/test_imposition_properties.py`, `test_cell_geometry.py`.

## Deliverable and files

Create `deckle/core/card_layout.py` with the actual planner; cards.impose_cards may delegate via a local import to avoid cycles. Connect planning.impose_project. Add `tests/test_card_layout.py` and `tests/test_card_layout_properties.py`. No PDF drawing in this step.

## Work sequence

1. Validate values/readiness and compute active copy count before allocating output slots. Expand active entries in deck order, each copy consecutively. Resolve shared/individual backs before allocation.
2. Implement grid capacity and centered origins exactly as architecture.md. Preserve physical card size; never shrink cards automatically to fit another row. Derive C/R from paper orientation; no automatic rotation of images or cells.
3. Create complete front and back slot arrays for each sheet. Fill fronts from top left; place corresponding backs at mirrored column indices. Produce undecorated fillers in every unused slot on both faces. Filler Placement can be identity at origin; no ink is associated with it.
4. Each active face yields OutputPage with the asset SourceRef, equal scales, source rotation, centered placement, no source crop, and CardDecoration containing trim/art/bleed rectangles. Each copy's card_id/copy_index agrees across faces.
5. Compute contain/cover using source dimensions after explicit rotation. Artwork footprint may exceed its art rectangle for cover, but exporter clipping is mandatory. Never swap or mirror source pixels.
6. Add optional front-only cut ticks as specified. No fold, signature, sewing, grain, or book trim marks/warnings. Both sides exist even on a partial last sheet.
7. In cards.py expose grid summary (columns, rows, active copies, sheet count) from the same geometry helper, avoiding a separate UI layout algorithm. Return validation failure when no card fits.

## Fixed numeric oracles

- Synthetic paper 500×400 pt, card 100×120, margins 20, gaps 10, border off, cut marks off: C=4,R=2, grid=430×250, origin=(35,75). First front trim=(35,205,100,120); its back=(365,205,100,120). Last front in row is x=365. These literal numbers are test expectations, not recomputed using production helpers.
- For two columns/two rows, front labels top-to-bottom are `[A,B; C,D]`; back labels are `[B,A; D,C]`. With only A,B,C: front `[A,B; C,_]`, back `[B,A; _,C]`. Back artwork's top arrow still points up.
- Default settings on Letter portrait (612×792): cell=186×258, C=3,R=2, capacity=6. A4 (595.275590551×841.889763780): C=3,R=3, capacity=9. Do not promise nine Letter cards with default bleed/margins.
- Default border off on Letter: C=3,R=3, capacity=9, finished card remains 180×252. Toggling on returns to six slots and 9 pt inside border/3 pt beyond cut.
- First oracle with border=5, bleed=2, cut marks off: art size=90×110; black size=104×124. Border toggle does not change w/h.

## Property and error tests

For generated valid dimensions, prove no allocated cells overlap, ink rectangles fit in their cells, centered grid satisfies minimum margins, reflecting a trim twice returns its original rectangle, each active copy occurs once per face, pair centers map across paper, and fillers mirror. Independently test a grid with unequal columns/rows and more than one sheet.

Test exactly fitting cells, near-boundary fit tolerance, paper too small, huge margin, zero/negative dimensions, all skipped, unresolved back, cap exceeded, all four rotations, mixed source aspect ratios, contain and cover. Increasing copies cannot change an existing earlier pair's identity; increasing border cannot change finished trim size.

## Gate

Run new layout/property tests plus existing imposition/property and model suites. Compare book plans before/after the new dispatcher for existing flat and folio fixtures. No default behavior for book projects changes.
