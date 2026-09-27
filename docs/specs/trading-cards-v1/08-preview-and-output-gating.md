# Step 08 — Preview the actual card PDF and gate invalid output

Dependencies: 05–07. Requirements: R04, R05, R06, R08, R10.

## Read first

architecture.md output contract; `app/main.py` layout refresh and action enablement; `views/preview_view.py`; `app/exporting.py`; existing preview-profile, menu-gating, render-concurrency, and print-needs-document tests.

## Deliverable and files

Integrate impose_project into desktop planning, extend preview card guides/labels and clipping checks, add `tests/test_card_preview.py` and `tests/test_card_output_gating.py`. The underlying preview still rasterizes exporter PDFs through existing workers/cache.

## Work sequence

1. Replace page-count assumptions with project_has_content for save/dirty and with card_output_problems plus successful planning for output. Search all `project.pages`, `bool(pages)`, strategy selection, and suggested export naming in desktop modules. Audit each occurrence explicitly; do not globally substitute lists.
2. An unresolved or empty active deck clears the visible old plan and disables Print/Save PDF/Export pass/proof actions. Show an actionable reason such as “2 cards need backs.” Saving the project and editing mappings remain enabled. No stale plan may export after an error.
3. Existing sheet navigation works at sheet granularity. Show fronts and backs side-by-side with labels “Front — viewed directly” and “Back — viewed directly; positions reversed for duplex.” Do not display the back as a transparent through-paper mirror in the normal view.
4. Add optional nonprinting trim guides using CardDecoration.trim_rect and selection highlights by (card_id,copy_index). Distinguish finished cut, bleed edge, and printer printable area with a legend. Nonprinting overlays must never appear in exported pixels. Hide book content-box/gutter/signature labels for cards.
5. Label canonical preview “Before printer correction.” The selected printer can still supply imageable-area warnings. For each active card, warning coverage includes the entire black bleed (or trim when border off), not merely the source artwork footprint. Assess back ink after the profile translation in canonical back coordinates; respect the existing offset convention. If the view shows corrected output instead, its pixels must come from the matching exported pass and its label must say so.
6. Warn when printed border/bleed/cut marks exceed paper/imageable bounds. Clip warnings are advisory under current app policy; impossible geometry and missing pairs are blocking. The UI describes the distinction clearly.
7. Protect output against a late/stale preview worker result after pairing, border, paper, or project changes. Use current request generation/digest checks. Retain existing lock, worker lifecycle, shutdown, and cache budget conventions.
8. Set default export name to the saved project's stem + '-cards.pdf', otherwise 'cards.pdf'; pass names append '-fronts'/'-backs'. Protect every referenced asset PDF from overwrite using existing output-path checks. Use source iteration, not only pages=[].

## Acceptance tests

- A three-card partial 2×2 sheet visibly shows fronts `[A,B;C,_]` and backs `[B,A;_,C]` with readable upright landmarks.
- Preview pixels equal canonical exported pixels before overlays. Changing only pairing, border width, bleed, or rotation visibly updates the relevant face.
- Overlay on/off leaves actual PDF bytes/content semantics unchanged. Card selection labels map the back slot to the same card identity as front.
- Border may exceed imageable area while artwork fits: warning must still occur. Profile changes recalculate warnings and do not modify card dimensions or pairing.
- Remove a required back while a preview is pending: old worker cannot reenable output or restore obsolete sheet display. Error recovery recomputes a fresh plan.
- Save remains enabled for meaningful incomplete decks; Print, export, proof, and keyboard/menu variants remain disabled. Book empty-document behavior stays intact.
- Attempt export onto a converted front/back PDF, including a reused shared back, is refused without altering the source.

## Gate

Run new preview/gating tests plus existing preview, menu, overwrite-protection, render-concurrency, and shutdown tests. Manually check legend/readability at fit-to-window and zoomed view.
