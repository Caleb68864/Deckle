# Criteria drift classification — deckle-signatures-v2

Date: 2026-08-04 · HEAD `56e5af4` · suite 318 passed / 3 skipped / 0 failed
(read-only audit; no source or test file was modified)

## Method

- Selectors extracted with
  `grep -rhoE '\-k [a-z_][a-z0-9_]+' docs/specs/deckle-signatures-v2/*.md | sed 's/-k //' | sort -u`
  → 73 distinct selectors.
- Collected names from `python -m pytest --collect-only -q` → 321 tests.
- Substring-matching the two sets leaves **48** selectors that match no collected test.
  (The brief said 49; `hash_plan_distinguishes_absent_side_from_present_side` now matches
  `tests/test_print_session.py::test_hash_plan_distinguishes_absent_side_from_present_side`,
  so it has already been closed.)
- One caveat on the extraction: the grep pattern is lowercase-only, so
  `every_public_function_returns_only_Mark_values` is truncated to
  `every_public_function_returns_only_`. It is classified against the full criterion text.
- The selector `spine_side` is declared by two different sub-specs against two different
  properties (SS-07 criterion 8 and SS-08 "spine derived from cell position"). It appears
  twice below and gets two independent verdicts.
- All candidate tests cited as COVERED were run and pass:
  `python -m pytest tests/test_signatures.py tests/test_marks.py tests/test_export_marks.py tests/test_layout_saddle.py tests/test_integration_signatures.py tests/test_preview_fidelity.py tests/test_print_session.py tests/test_printing.py tests/test_license_audit.py -q` →
  `104 passed in 1.91s`.

## Classification

| sub-spec | spec selector | verdict | real test (name + file:line) | note |
|---|---|---|---|---|
| SS-02 | `placements_are_identical` | ABSENT | — | Spec Step 1 asks for `test_gutter_shift_placements_are_identical_across_aspect_ratios_and_binding_edges` as a *characterisation* test taken before the `Side` refactor. No such test exists in `tests/test_layout.py`; the refactor's only guard is the grep-based structural criteria. |
| SS-03 | `second_page_of_a_two_page_side_is_reported_as_clipped` | COVERED | `test_second_page_of_a_two_page_side_overflowing_is_reported` — `tests/test_preview_fidelity.py:165` | Builds a 2-page `Side` where only the second page overflows and asserts exactly one `clipped_by_page` warning. Reverting to a per-side (not per-page) check yields zero warnings and fails. |
| SS-03 | `both_pages_of_a_side_can_be_reported` | ABSENT | — | No test constructs a `Side` where **both** pages overflow. `tests/test_preview_fidelity.py:165` deliberately makes the first page in-bounds, so a `break` after the first warning would still pass every existing test. This is the exact "clipping loop breaks after the first warning" failure the criterion names. |
| SS-03 | `two_page_side_with_no_overflow_reports_nothing` | ABSENT | — | The negative controls that exist (`test_content_fully_within_imageable_area_has_no_warning` :148, `test_filler_page_produces_no_clipping_warning` :157) both use **one-page** sides. No clean two-page side is tested, so per-page over-reporting is unguarded. |
| SS-03 | `back_index` | ABSENT | — | The page-count half ("one `Side` is one PDF page") is covered by `tests/test_export_marks.py:252` and `tests/test_integration_signatures.py:156`. The stated property — the front-then-back **index mapping** under 2-up — is asserted nowhere: `tests/test_render.py:112` renders both sides but only asserts `width > 0`, and `deckle/app/backend.py:137` `_render_sheet_side` has no test that would catch a front/back swap. |
| SS-04 | `per_signature_subset` | COVERED | `test_end_to_end_folio_signature_impose_export_fold_and_print` — `tests/test_integration_signatures.py:136` (lines 167-181); reinforced by `test_plan_passes_with_explicit_sheets_covers_only_those_sheets` — `tests/test_printing.py:78` | Prints only `plan.signatures[1].sheet_indices` through the unmodified `plan_passes(..., sheets=...)` path and asserts both passes carry exactly those sheets in forward/reverse order. |
| SS-04 | `reads_only_sheet_index` | ABSENT | — | No structural/AST test constrains which `Sheet` attributes `plan_passes` touches. `tests/test_seam_zero_diff.py` contains only the SHA-256 module pin (`test_printing_and_profiles_modules_are_unchanged_by_the_seam` :35), which would flag *any* edit to `printing.py` but asserts nothing about attribute access. |
| SS-05 | `saddle_order_is_a_permutation` | COVERED | `test_saddle_order_is_permutation_for_multiples_of_4` — `tests/test_signatures.py:54` | `sorted(saddle_order(n)) == list(range(n))` for every multiple of 4 up to 128 — the criterion verbatim. |
| SS-05 | `saddle_order_pinned_for_eight_pages` | COVERED | `test_saddle_order_pinned_against_vault_note_n8` — `tests/test_signatures.py:60` | Pins `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`, the vault note's executed output. |
| SS-05 | `outermost_sheet_carries_last_and_first_page` | COVERED | `test_saddle_order_outermost_sheet_carries_first_and_last_page` — `tests/test_signatures.py:64` | Asserts `result[:2] == [n - 1, 0]` across all multiples of 4 up to 128 — nested, not stacked. |
| SS-05 | `saddle_order_rejects_non_multiples_of_four` | COVERED | `test_saddle_order_raises_on_non_multiple_of_4` — `tests/test_signatures.py:70`; plus `test_saddle_order_raises_on_non_positive` :76 | Together they cover the criterion's "non-positive-multiples of 4". |
| SS-05 | `fold_reading_order_works_with_saddle_order_disabled` | ABSENT | — | Criterion 9 is the **runtime** independence check, distinct from criterion 7's static one. Only the static AST check exists (`test_fold_reading_order_does_not_call_saddle_order` :133). No test monkeypatches/disables `saddle_order` and re-runs `fold_reading_order`. Worse, the positive tests (:103, :108) compute their *expected* values by calling `saddle_order`, so a shared-bug failure mode is invisible. |
| SS-06 | `every_public_function_returns_only_` (`…_Mark_values`) | ABSENT | — | No test enumerates `deckle.core.marks`' public functions and asserts every return value is a `Mark`. Existing tests assert `.kind` strings on the objects they happen to call, which does not constrain the return type or catch a newly added public function. |
| SS-06 | `three_sewing_stations_are_evenly_spaced_between_the_insets` | COVERED | `test_sewing_stations_count_and_kind` :24, `test_sewing_stations_evenly_spaced` :30, `test_sewing_stations_first_and_last_margins` :37 — all `tests/test_marks.py` | The three together assert count 3, kind `sewing_station`, equal gaps, and first/last midpoints at `SEWING_MARGIN_PT` / `sheet_h - SEWING_MARGIN_PT` — every clause of the criterion. |
| SS-06 | `every_sewing_station_straddles_the_fold` | COVERED | `test_sewing_stations_straddle_fold` — `tests/test_marks.py:44` | Asserts `min(x0,x1) <= fold_x <= max(x0,x1)` for every station. |
| SS-06 | `signature_order_marks_step_monotonically_down_the_spine` | COVERED | `test_signature_order_mark_monotonically_increasing` :65 and `test_signature_order_mark_endpoints_at_margins` :74 — `tests/test_marks.py` | Strict monotonicity across all indices, plus index 0 at the tail margin and the last at the head margin. |
| SS-06 | `a_single_signature_does_not_divide_by_zero` | COVERED | `test_signature_order_mark_single_signature_no_zero_division` — `tests/test_marks.py:82` | Calls `signature_order_mark(0, 1, …)`; a naive `/(sig_count - 1)` raises `ZeroDivisionError` and the test errors. |
| SS-06 | `fold_line_is_a_single_segment_on_the_cell_boundary` | COVERED | `test_fold_line_spans_full_height` — `tests/test_marks.py:94` | Asserts `kind == "fold_line"`, `x0 == x1 == fold_x`, and `sorted((y0, y1)) == [0.0, sheet_h]`. "Single segment" is guaranteed by the return type (one `Mark`). |
| SS-06 | `no_mark_lies_inside_a_cell_content_box` | **VACUOUS** | `test_no_mark_inside_content_box_for_letter_landscape` — `tests/test_marks.py:101` | The test never computes a content box. It builds marks, then asserts `lo <= FOLD_X <= hi` — i.e. re-asserts fold-straddling, which `test_sewing_stations_straddle_fold` :44 already covers. No `LayoutSettings`, no cell, no `content_box_rect_pt` call, no folio layout is involved despite the name. Concretely: with `gutter_pt = 0` the content box abuts the fold, so a mark that straddles the fold *does* lie inside the content box — and this test still passes. It cannot fail for the reason it claims to exist. |
| SS-07 | `accept_a_full_sheet_cell` | ABSENT | — | `document_scale`, `content_box_rect_pt` and `actual_margins_pt` all take `cell: Cell \| None = None` (`deckle/core/layout.py:134, 320, 363`), but no test passes a full-sheet cell to any of them. |
| SS-07 | `actual_margins_are_measured_against_the_cell` | COVERED | `test_front_left_cell_inner_margin_is_on_its_right_edge` — `tests/test_layout_saddle.py:185` | Calls `actual_margins_pt(..., cell=left_cell)` / `cell=right_cell` on a real folio plan and asserts both leaves measure inner margin `18.0` against the fold. Measuring against the sheet instead of the cell gives `0.0` on one leaf, so the test fails. |
| SS-07 | `byte_identical_to_the_pin` | ABSENT | — | No pin of `GutterShiftStrategy`'s pre-SS-07 placements exists. `tests/test_golden_pinebox.py` asserts padding, per-page aspect handling and page-count parity (:41, :65, :97) and *skips* when the fixture is absent — it is not a placement pin. |
| SS-07 | `content_box_rect_is_measured_inside_the_cell` | ABSENT | — | Every `content_box_rect_pt` call in the suite (`tests/test_layout.py:434, 435, 454, 468, 482`) passes only `is_recto=`. The `cell=` parameter is never exercised, so the criterion's pinned value (`x0 == 396 + gutter_pt` for cell `(396, 0, 792, 612)`) is unasserted. |
| SS-07 | `document_scale_against_a_half_width_cell` | ABSENT | — | `document_scale`'s `cell` parameter is never passed in any test. `tests/test_layout_saddle.py:336` calls `document_scale([page], s)` *deliberately without* a cell (to construct an overflow); nothing asserts a half-width cell yields a smaller, still single-valued scale. |
| SS-07 | `full_sheet_cell_is_identical` | ABSENT | — | Same gap as the next row; see below. |
| SS-07 | `full_sheet_cell_is_identical_to_omitting_the_cell` | ABSENT | — | No test compares `cell=_full_sheet_cell(paper)` against `cell=None`, let alone over 4 aspect ratios × 2 binding edges × 3 `slack_to`. The backward-compatibility guarantee of the whole cell generalisation is unasserted. |
| SS-07 | `half_width_cell` | ABSENT | — | Step-5 selector for the same `document_scale` gap above. |
| SS-07 | `measured_inside_the_cell` | ABSENT | — | Step-6 selector for the `content_box_rect_pt` cell gap above. (The similarly-spelled `measured_against_the_cell` **does** match `test_clipped_by_page_measured_against_the_cell` `tests/test_layout_saddle.py:329` — a different criterion, and genuinely covered.) |
| SS-07 | `spine_side` (crit. 8: explicit `spine_side` **bypasses output-page parity**) | **VACUOUS** | closest: `test_front_left_cell_inner_margin_is_on_its_right_edge` — `tests/test_layout_saddle.py:185` | That test passes `spine_side=` but never passes `is_recto=`, so `spine_side` is the only signal available. The criterion is about **precedence** — explicit `spine_side` winning over parity. Reverse the precedence in `actual_margins_pt` / `content_box_rect_pt` and every existing test still passes, because no test ever supplies both. |
| SS-08 | `never_rotates` | ABSENT | — | Nothing asserts `rotate_deg == 0` for folio leaves. `_place_page` can emit `rotate_deg = 90` (`deckle/core/layout.py:206`); `tests/test_layout_saddle.py` never inspects `placement.rotate_deg`, and the portrait test (:265) asserts only the warning and `paper_pt`. |
| SS-08 | `spine_side` (spine derived from cell position on both sides) | COVERED | `test_front_left_cell_inner_margin_is_on_its_right_edge` — `tests/test_layout_saddle.py:185` | Both leaves of the front side measure inner margin `18.0` against the fold; a spine derived from output-page parity rather than cell position gives `0.0` on the left leaf. **Caveat:** its sibling `test_back_side_spine_also_faces_the_fold` :202 asserts only `>= 0.0` on both leaves and is itself vacuous — it would pass under almost any placement, so the *back*-side half of this criterion rests on nothing. |
| SS-08 | `thickness_never_moves` | COVERED | `test_creep_never_affects_placement_geometry` — `tests/test_layout_saddle.py:289` | Imposes the same 32 pages at `paper_thickness_pt` 0.0 and 2.0 and asserts every `Placement` compares equal. Any creep leaking into geometry fails it. |
| SS-09 | `additive` | COVERED | `test_side_with_no_marks_exports_no_stroke_ops` — `tests/test_export_marks.py:83` | Exports a `Side(marks=())` and asserts zero `s` operators in the page content stream. |
| SS-09 | `dashed` | ABSENT | — | No test inspects the PDF dash pattern (`d` operator) or line style at all. Fold lines and sewing stations are only ever counted by stroke operator (:100), so making both solid — or both dashed — breaks nothing. |
| SS-09 | `scale_drift` | COVERED | `test_two_page_side_shares_identical_scale_no_drift` — `tests/test_export_marks.py:172` | Parses both `cm` matrices out of the coalesced content stream and asserts `a == d == 2.0` in each; drift between the two cells fails it. |
| SS-10 | `denylist_names` | ABSENT | — | Spec Step 1 asks for `assert {"pymupdf","fitz","pdfimpose","cpdf"} <= FORBIDDEN_DISTRIBUTIONS`. No such test exists. The set at `tests/test_license_audit.py:41` does contain all four, but only the grep-based STRUCTURAL criteria in the same table check that; `cpdf` in particular is named by no test. |
| SS-10 | `denylist_catches` | **VACUOUS** | `test_injected_pdfimpose_fails_the_denylist_check` — `tests/test_license_audit.py:160` | The spec's design was to extract `_forbidden_offenders` and have both the real audit and the injection test call it. Instead `_check_forbidden` (`tests/test_license_audit.py:144`) is a **hand-copied duplicate** of the body of `test_no_pymupdf_or_fitz_dependency` (:121). The injection test exercises the copy, not the audit: delete or weaken the check inside `test_no_pymupdf_or_fitz_dependency` and `test_injected_pdfimpose_fails_the_denylist_check` still passes. The criterion is "wired, not merely declared" and the wiring is precisely what is not proven. Additionally, the fake distribution is named `pdfimpose`, so it is caught by the name branch and `continue`s — the `top_level.txt` (module-name) branch the spec explicitly wanted covered is never reached. |
| SS-11 | `binding_mutators_return_new_projects` | ABSENT | — | `deckle/app/views/layout_panel.py` exposes 11 mutators (`set_gutter_pt` :36 … `set_paper_thickness_pt` :132). Only `set_gutter_pt` is called anywhere in the suite (`tests/test_preview_fidelity.py:228`), and that call asserts nothing about identity or `replace`. There is no `tests/test_layout_panel_binding.py`. |
| SS-11 | `binding_readout` | ABSENT | — | `layout_panel.binding_readout_str` (`deckle/app/views/layout_panel.py:150`) is referenced by no test. Nothing checks it names signatures/sheets/blanks. |
| SS-11 | `non_folio_side_produces_exactly_one_guide` | ABSENT | — | `preview_view.content_box_guides_for_side` (`deckle/app/views/preview_view.py:175`) — whose own docstring states the one-guide-under-`"none"` contract — has no test. |
| SS-11 | `plan_passes_over_one_signature_covers_only_that_signature` | COVERED | `test_plan_passes_with_explicit_sheets_covers_only_those_sheets` — `tests/test_printing.py:78`; against a real signature: `tests/test_integration_signatures.py:169-173` | The integration test feeds `plan.signatures[1].sheet_indices` into `plan_passes` and asserts `passes[0].sheet_order == list(second_sig_sheets)`. |
| SS-11 | `recompute_plan_under_folio_yields_signatures` | ABSENT | — | The only `recompute_plan` tests (`tests/test_preview_fidelity.py:219, 236`) use `LayoutSettings` without `fold_scheme`, i.e. the default `"none"` path. Nothing drives the panel under folio or asserts the resulting plan has signatures. |
| SS-11 | `signature_selection_enumerates_no_printers` | ABSENT | — | `print_dialog.py:179-186` builds the signature combo and `:212` reads `currentData()`; no test in `tests/test_print_dialog.py` mentions signatures. The nearest test, `tests/test_view_workers.py::test_printer_enumeration_is_not_called_during_import`, covers import, not signature selection. |
| SS-11 | `strategy_for_dispatches_on_fold_scheme` | ABSENT | — | `_strategy_for` exists only in `deckle/cli.py:126`, not in `layout_panel` where the criterion places it, and no test calls it directly or asserts which strategy a given `fold_scheme` selects. |
| SS-12 | `cli_accepts_folio_flags` | ABSENT | — | The four flags are implemented (`deckle/cli.py:151-166`) and the sub-spec's own `--help`-grep criteria cover them, but `tests/test_cli.py` contains no folio test — nothing asserts the parsed values land on the namespace or reach `LayoutSettings`. |
| SS-12 | `end_to_end_folio_impose_export_reopen_and_print` | COVERED | `test_end_to_end_folio_signature_impose_export_fold_and_print` — `tests/test_integration_signatures.py:136` | Asserts 8 sheets, 2 signatures, 16 reopened PDF pages, and exactly 2 XObjects (two folio cells) per page — the criterion's three numbers verbatim — then submits through a stub backend. |
| SS-12 | `fold_reading_order_closes_over_the_exported_plan` | COVERED | `tests/test_integration_signatures.py:163-165` (inside :136), plus `test_fold_reading_order_matches_saddle_order_independently_for_the_real_plan` :236 | Scatters the exported plan's print-slot `OutputPage`s through `fold_reading_order` and asserts the reconstruction equals `range(32)`; the second test repeats it for 30 pages with 2 padding blanks. |
| SS-12 | `signatures_and_marks_are_actually_invoked` | COVERED | `test_signatures_and_marks_modules_are_invoked_and_marks_consumed_by_export` — `tests/test_integration_signatures.py:187` | `mock.patch.object(..., wraps=...)` on `split_signatures`, `saddle_order`, `fold_line`, `sewing_stations`, `signature_order_mark` and `export._draw_marks`, asserting each was called and that `_draw_marks` saw non-empty marks. Orphaning any module fails it. |

## Summary counts

| verdict | count |
|---|---|
| COVERED | 20 |
| VACUOUS | 3 |
| ABSENT | 25 |
| **total** | **48** |

## VACUOUS — tests that exist but prove nothing

These are the dangerous ones: a green test standing in front of an unverified requirement.

1. **`no_mark_lies_inside_a_cell_content_box`** (SS-06) —
   `tests/test_marks.py:101`. Asserts fold-straddling, not content-box containment; never
   constructs a cell or calls `content_box_rect_pt`. At `gutter_pt = 0` the stated property is
   false while the test is green. Fully duplicative of `tests/test_marks.py:44`.
2. **`spine_side` / SS-07 criterion 8** ("explicit `spine_side` bypasses output-page parity") —
   nearest test `tests/test_layout_saddle.py:185`. No test ever passes `is_recto` and
   `spine_side` together, so the precedence rule could be inverted with the suite still green.
3. **`denylist_catches`** (SS-10) — `tests/test_license_audit.py:160`. Exercises
   `_check_forbidden` (:144), a hand-copied duplicate of the real audit body at :121, so it
   proves nothing about whether the denylist is consulted. The module-name (`top_level.txt`)
   branch the spec called out is never reached by the fake distribution.

Honourable mention (not on the drift list, found while auditing):
**`tests/test_layout_saddle.py:202` `test_back_side_spine_also_faces_the_fold`** asserts only
`inner_margin >= 0.0` on both leaves. It is vacuous by the same standard and is the sole
back-side evidence for SS-08's spine criterion.

## ABSENT — no test asserts the property under any name

Grouped by the risk they leave open.

### Clipping loop (SS-03) — the same class of bug that motivated this audit
- `both_pages_of_a_side_can_be_reported` — a `break` after the first per-page warning would
  pass the entire suite.
- `two_page_side_with_no_overflow_reports_nothing` — no clean two-page negative control.
- `back_index` — front-then-back index mapping under 2-up is unasserted.

### Cell generalisation (SS-07) — the feature is implemented, the contract is untested
- `accept_a_full_sheet_cell`
- `byte_identical_to_the_pin`
- `content_box_rect_is_measured_inside_the_cell`
- `measured_inside_the_cell`
- `document_scale_against_a_half_width_cell`
- `half_width_cell`
- `full_sheet_cell_is_identical`
- `full_sheet_cell_is_identical_to_omitting_the_cell`

  The `cell=` parameter on `document_scale` and `content_box_rect_pt` is passed by **zero**
  tests. The backward-compatibility guarantee (full-sheet cell ≡ no cell) is entirely
  unverified; only `actual_margins_pt` has real cell coverage, via `test_layout_saddle.py`.

### UI surface (SS-11) — the least-covered sub-spec; `tests/test_layout_panel_binding.py` does not exist
- `binding_mutators_return_new_projects`
- `binding_readout`
- `recompute_plan_under_folio_yields_signatures`
- `strategy_for_dispatches_on_fold_scheme`
- `non_folio_side_produces_exactly_one_guide`
- `signature_selection_enumerates_no_printers`

  Ten of `layout_panel`'s eleven mutators, `binding_readout_str`,
  `content_box_guides_for_side`, and the print dialog's signature combo are referenced by no
  test. The entire folio path through the UI is untested.

### Rendering and marks
- `dashed` (SS-09) — no dash-pattern assertion anywhere; fold lines and stations are
  indistinguishable to the suite.
- `never_rotates` (SS-08) — `placement.rotate_deg` is never inspected on the folio path.
- `every_public_function_returns_only_Mark_values` (SS-06).

### Independence and structural guards
- `fold_reading_order_works_with_saddle_order_disabled` (SS-05) — the runtime half of the
  independence claim. Compounded by the fact that the positive `fold_reading_order` tests
  compute expected values *by calling* `saddle_order`, so the two derivations are not actually
  independent in the test suite.
- `reads_only_sheet_index` (SS-04).
- `placements_are_identical` (SS-02) — the pre-refactor characterisation test was never
  written.
- `denylist_names` (SS-10) — `cpdf` is named by no test.
- `cli_accepts_folio_flags` (SS-12) — flags implemented, no test.

## Highest-priority gaps

If only a handful of these get closed, close these:

1. `both_pages_of_a_side_can_be_reported` + `two_page_side_with_no_overflow_reports_nothing` —
   same bug class as the one that started this audit, and cheap to write.
2. `full_sheet_cell_is_identical_to_omitting_the_cell` — the load-bearing
   backward-compatibility claim of SS-07, currently unverified.
3. `denylist_catches` (rewrite) — extract `_forbidden_offenders` as the spec intended so the
   injection test exercises the real audit rather than a copy.
4. `no_mark_lies_inside_a_cell_content_box` (rewrite) — make it compute a real content box.
5. `fold_reading_order_works_with_saddle_order_disabled` — the two permutation derivations are
   supposed to be independent; nothing currently proves it at runtime.
