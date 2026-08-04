---
type: redteam-report
generated: 2026-08-04
target: "../2026-08-04-deckle-mvp.md"
findings_count: 27
critical: 11
advisory: 16
all_patched: true
---

# Red Team Review: 2026-08-04-deckle-mvp.md

**Two passes:** master spec (6 CRITICAL / 9 ADVISORY) and six high-risk phase specs
(5 CRITICAL / 7 ADVISORY). All 27 findings patched.

# Pass 1: Master Spec

9-role adversarial review. 14 sub-specs, ~110 acceptance criteria, 30 interface contracts.
**All 15 Pass-1 findings patched.**

## CRITICAL (6) — all fixed

**C-1: The `Project` type had no owner** *(Developer, Integration Architect)*
- Location: SS-01 criteria; consumed by SS-07 `save_project(project, path)`, SS-09 `AppState.project`
- Issue: SS-01 defined seven model types but never `Project`, the central document model. Two sub-specs consumed it; nothing created it. Absent from `contracts.json` too.
- Fix applied: `Project(pages: list[SourcePage], layout: LayoutSettings, printer: str | None)` added to SS-01.

**C-2: `Imposer` could not be pure — the data model carried no page geometry** *(Developer)*
- Location: SS-03 purity check vs. SS-01 `SourceRef` fields
- Issue: `impose(pages, settings)` must compute a per-page transform from each page's own media box, but `SourceRef(path, page_index, sha256)` carried no dimensions. `impose` would have to open the PDF — violating its purity requirement *and* tripping SS-03's own `! grep "open(\|Pdf.open"` check. **As specified, SS-03 was unimplementable.**
- Evidence: SS-02 Step 6 already reads geometry (`"page count and geometry only"`) but never stored it.
- Fix applied: `width_pt`, `height_pt` added to `SourceRef`; SS-02 now required to populate them.

**C-3: `SheetPlan` carried no paper size** *(Developer)*
- Location: SS-01 `SheetPlan`; SS-04 Step 3; SS-05 `render_sheet`
- Issue: SS-04 called `add_blank_page(page_size=settings.paper)` but `settings` is not a parameter of `export(plan, out_path, sheets)`. Same gap in `render_sheet`. Both consumers needed dimensions they had no access to.
- Fix applied: `paper_pt: tuple[float, float]` added to `SheetPlan` — paper size is a property of the plan, not of a consumer.

**C-4: The fidelity invariant's tolerance was unspecified** *(QA)*
- Location: SS-10 `tests/test_preview_fidelity.py`
- Issue: "diff within tolerance" with no tolerance given, guarding the property the whole architecture exists to provide. Untestable as written.
- Fix applied: superseded entirely. The preview now renders the exported PDF (see Research Corrections below), so the test became a **routing guard** — patch `export`, assert the preview path calls it. Concrete, and strictly stronger than any pixel tolerance.

**C-5: Autosave was stated in Edge Cases but owned by no sub-spec** *(SRE)*
- Location: Edge Cases ("Crash → autosave on every mutation") vs. SS-09
- Issue: SS-09 owns `AppState.mutate`, the only correct hook, but had no autosave criterion. A stated crash-recovery behavior with no implementer silently doesn't happen.
- Fix applied: debounced (500 ms) autosave wired into `mutate`, with a kill-and-reopen test. Added as SS-09 Step 7b.

**C-6: Success Criterion 12 contradicted the packaging exclusion** *(Product)*
- Location: design doc Success Criteria #12 vs. Out of Scope
- Issue: #12 required a shipped installer while packaging was explicitly out of scope and SS-14 forbidden from depending on it. **No sub-spec could satisfy it**; a verifier would mark the MVP incomplete.
- Fix applied: restated to what the MVP can verify — no external runtime binary, no AGPL, so packaging remains achievable. Installer criterion moved to deferred/post-MVP, with `--onedir` recorded as the recommended shape.

## ADVISORY (9) — all resolved into the spec

| ID | Role | Finding | Resolution |
|---|---|---|---|
| A-1 | End User | No behavior defined for zero printers installed | Print action disabled with an explanatory message; SS-09 Step 7c |
| A-2 | Security | Untrusted PDF parsing is the entire attack surface, unaddressed | Pin `pikepdf`/`pypdfium2` minimums, document update policy, state no elevated privileges |
| A-3 | Security/Data | `.deckle` files carry paths and may be shared — traversal risk | Validate on load; confirm before opening outside user-chosen roots |
| A-4 | Data | Only *changed* sources handled; *moved/deleted* is a different failure | `SourceMissingError` + relocate action |
| A-5 | SRE | Session log append-only, unbounded | Rotate at 5 MB, retain 3 generations |
| A-6 | SRE | No `--version` / diagnostic output | Prints app + `pikepdf`/`pypdfium2`/`img2pdf`/`PySide6` versions |
| A-7 | Data | img2pdf normalization cache never cleaned | Cap 2 GB, LRU evict on startup |
| A-8 | QA | "Unbounded memory growth" and "responsive" unmeasurable | Peak RSS < 4× the 50-page baseline; no main-thread op > 100 ms |
| A-9 | Developer | Gutter unit parser unspecified | Accepts `in`/`pt`/`mm`/`cm`; bare number = points; invalid unit is a blocking error |

## Construction-Site Check: clean

SS-12 and SS-14 both trigger wiring keywords; both name concrete call sites with symbol
**and** file path (`PrintSession` / `deckle/core/print_session.py`; `deckle/__main__.py`,
`deckle/app/main.py`). No `construction-site-without-caller` findings.

## Research Corrections Applied in the Same Pass

Arriving from `Caleb's Vault/Software/Bookbinding Toolchain Comparison.md` (16 tool/library
research sets, all verified by execution):

1. **Preview now renders the actual exported PDF** rather than compositing independently.
   Called out in the research as *"the single highest-value architectural choice in the
   project"* — a preview that re-draws the layout can share a bug with the exporter and
   agree with it. SS-05 gains a dependency on SS-04; the critical path lengthens by one wave.
2. **`rotation=img2pdf.Rotation.ifvalid` is mandatory** — EXIF Orientation 0 otherwise
   raises and kills the whole batch.
3. **img2pdf's missing-DPI default is 96.0, not 72** — a wrong-size-in-print hazard.
4. **Never route images through Pillow's PDF writer** — measured 68% pixel change on a q95
   JPEG round-trip.
5. **Differentiator corrected.** Manual duplex is *over-solved* (five independent solutions,
   including vendor drivers), and Bookbinder JS does support it — the earlier prior-art claim
   was wrong. **The real gap is that no surveyed tool can control a printer.**
6. **Creep de-escalated** — sub-millimetre at 4–6 sheets per signature. Thin signatures make
   it evaporate.
7. **`pdfimpose` recorded as a dev-time test oracle** (AGPL — diff against, never embed).

## Role Scorecards
Developer: 4 | QA: 3 | End User: 1 | Architect: 2 | Scope Realist: 2 | Security: 2 | SRE: 3 | Data: 3 | Product: 1

---

# Pass 2: Six High-Risk Phase Specs

SS-03, SS-04, SS-06, SS-08, SS-11, SS-13.

## CRITICAL (5) — all fixed

**P-1: Gutter parity was inverted — the recto got no gutter** *(SS-03, Developer)*
- Location: SS-03 `fixed_gutter` criterion
- Issue: the criterion gave `tx == gutter_pt` to odd-index pages. **Index 0 is the first page, a recto**, whose spine is on the left — it must be pushed right. The gutter was going to the verso.
- Evidence: `[[pikepdf - Gutter Shift Recipe]]` Method B — `x = GUTTER if i % 2 == 0 else 0  # recto: push right off the spine`
- Why it mattered: this would have passed every numeric test in SS-03, because those tests encode the same parity assumption as the criterion. It is precisely the failure the research names — *"discovering after 200 printed pages that the gutter went the wrong way on versos."*
- Fix applied: parity inverted, plus a **geometric sign-inversion guard** (a recto's ink bbox must sit further from the binding edge than a verso's) that asserts physical intent rather than a computed value.

**P-2: 180° back-side rotation had no owner** *(SS-06, Integration)*
- Issue: the verified duplex table says long-edge flips may need 180° rotation on backs, but `PrintPass` had no field for it and no sub-spec applied it.
- Fix applied: `rotate_backs: bool` added to `PrintPass`; SS-08 applies it via `page.rotate(180, relative=True)` with `flatten_rotation()` fallback. Also documented the mapping from the four profile fields to the table's two behavioral axes.

**P-3: The calibration test sheet needs authored text; nothing in the stack does it well** *(SS-13, Developer)*
- Issue: `build_test_plan()` must draw glyphs and sheet numbers. `reportlab` was dropped; pikepdf's own docs warn its text support is rudimentary. The one sub-spec that *creates* content had no content-authoring dependency.
- Fix applied: committed to pikepdf `Canvas`/`Text` with the test-sheet design constrained to four rotationally-distinguishable geometric marks plus two digits. `reportlab` (BSD) named as the fallback if that proves insufficient — not added speculatively.

**P-4: Preview-via-export had no cache or temp lifecycle** *(SS-04, SRE)*
- Issue: adopting "preview renders the exported PDF" meant `export` ran on every sheet navigation — constant re-export, source-handle reopening, leaked temp files.
- Fix applied: `export_sheet_cached(plan, sheet_index)` with a bounded LRU cache keyed by `(sheet_index, plan_hash)`, cleared on project close.

**P-5: `binding_edge` accepted `"top"` but only `"left"` was specified** *(SS-03, QA)*
- Fix applied: enum narrowed to `left | right` for the MVP; right specified as the exact mirror of left. Top-edge binding deferred to v2 as a distinct physical workflow requiring a vertical shift.

## ADVISORY (7) — all resolved

| ID | Sub-spec | Finding | Resolution |
|---|---|---|---|
| P-A1 | SS-03 | `SheetPlan.paper_pt` not set by `impose` | Added as a `[STRUCTURAL]` criterion |
| P-A2 | SS-03 | No test could catch a sign inversion | Geometric guard added (see P-1) |
| P-A3 | SS-06 | Profile-fields → table-axes mapping unspecified | `reverse_stack` drives sheet order; `flip_axis` drives `rotate_backs` |
| P-A4 | SS-08 | No path for a printer that *has* a duplexer | Offer single-pass mode when `supportedDuplexModes()` reports real capability |
| P-A5 | SS-08 | Chunk-failure behavior undefined | A failed chunk cancels the pass's remaining chunks |
| P-A6 | SS-11 | `resume()` scope ambiguous; no session discovery | Documented as per-pass; `session_id` + `list_resumable()` added |
| P-A7 | SS-11 | Session state files never cleaned up | Successful completion deletes its state file |

## Pass 2 Role Scorecards
Developer: 4 | QA: 3 | End User: 0 | Architect: 2 | Scope Realist: 0 | Security: 0 | SRE: 2 | Data: 1 | Product: 0
