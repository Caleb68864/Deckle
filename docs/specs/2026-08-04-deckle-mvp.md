# Deckle MVP — PDF Binding-Margin Prep and Manual-Duplex Printing

## Meta
- Client: Personal
- Project: Deckle
- Repo: https://github.com/Caleb68864/Deckle
- Date: 2026-08-04
- Author: Caleb Bennett
- Status: draft
- Design source: `docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md` (evaluated)
- License: MIT
- Quality scores: Outcome 5 / Scope 5 / Decisions 5 / Edges 5 / Criteria 5 / Decomposition 5 / Purpose 5 — **35/35**
- Revised 2026-08-04 after pikepdf research review: composition library corrected to
  pikepdf Form XObjects, verified API traps folded into Edge Cases, SS-11 split into
  core `PrintSession` + Qt `PrintDialog`.

## Outcome

A standalone, fully offline desktop application (Windows + Linux) that imports PDFs and
image folders, applies an alternating binding gutter so odd and even pages shift away from
the spine, previews the exact physical sheets, and prints double-sided on a printer with no
duplexer — reloading paper once, guided by an instruction derived from that printer's own
calibration — producing a stack that collates correctly with no manual page reordering.

Done means: all 15 Success Criteria from the design pass, including the Pinebox
golden-fixture regression, and `deckle.core` contains no Qt import.

## Intent

**Trade-off hierarchy** — when valid approaches conflict, prefer in this order:

1. **Printed physical correctness** over everything. If a choice makes the paper come out
   right, take it.
2. **Honest reporting** over reassuring UI. Flagging a limitation beats hiding it. Never
   promise fidelity the hardware cannot deliver.
3. **Core purity and testability** over convenience. A Qt import in `deckle.core` is never
   worth the shortcut it buys.
4. **Simple and obviously correct** over clever or general. This is a personal tool that
   must work, not a framework.
5. **Consistency with the four-level page model** over locally-nicer naming.

**Decide autonomously:** module/file layout, naming, code style, test organization, widget
composition within a named view, cache sizes, thumbnail DPI, threading mechanism, undo
snapshot depth, error message wording.

**Stop and ask when:**
- A dependency change would introduce AGPL, a commercial license, or an external runtime
  binary.
- Qt's cross-platform print behavior diverges from what this spec assumes (see SS-08
  spike) — that invalidates part of the approach rationale.
- The calibration state-space enumeration (SS-13) shows the wizard needs materially more
  than five questions, or that the axes are not independently determinable.
- Any acceptance criterion appears unachievable as written.
- Vendoring any third-party source (licensing verification).

**Decided (no further escalation):**
- Scale modes: **both** `FitHeight` and `FixedGutter` ship; **`FitHeight` is the default.**
- Composition library: **`pikepdf`**, via `Page.as_form_xobject()` +
  `Page.calc_form_xobject_placement()`. **Not `pypdf`, and never `add_overlay`.**
  Verified locally on pikepdf 10.11.0 / libqpdf 12.3.2: this path emits pure translation
  matrices (`1 0 0 1 396 0 cm`) with no scale drift, which is exactly what imposition
  requires. `add_overlay` centers and best-fits — correct for watermarks, wrong here.
  Reference: `[[pikepdf - Form XObject Placement]]`, `[[pikepdf - Gutter Shift Recipe]]`
  (Method B).
- Scale mode maps onto placement flags: `fixed_gutter` with a source that already fits uses
  `allow_shrink=False, allow_expand=False` for exact 1:1; `fit_height` sizes the placement
  rect to the scaled target and permits shrink/expand.
- Image handling: **`img2pdf` at ingestion**, never a parallel image path downstream.
- Undo: **snapshot-based**, bounded deque, default depth 50.
- CLI: **ships in MVP.**
- Packaging shape (one-file vs one-folder): **deferred to build time.** Do not spec, block
  on, or implement packaging in this MVP. SS-14 must not depend on it.

## Context

Supersedes an existing personal script, `AddMargin2PDF/__main__.py` (pypdf + reportlab +
PyMuPDF), which scales pages to letter height and alternates horizontal offset. That script
has three known defects this spec must not reproduce:

1. Content width is computed once from page 0's aspect ratio while scale is recomputed per
   page — any page with a different aspect ratio gets the wrong gutter.
2. Odd-page-count padding inserts a filler at index 1 *and* potentially another at the end,
   double-padding some documents.
3. `PyMuPDF` is AGPL-3.0, which would make the project undistributable as MIT.

Prior art was researched (Bookbinder JS, HornPenguin Booklet, bookbinding-imposition,
Stirling PDF, PDF Arranger, Bundsteg, Duplex Print Helper). No existing tool combines
gutter shift + sheet-accurate preview + manual-duplex print control + image import. The
manual-duplex print path is the genuinely underserved capability.

The user hand-binds: 3-hole punch today, saddle stitch on a Singer 111w101 for 10–20 page
books, traditional sewn signatures eventually. Signature imposition is v2 — but the
`LayoutStrategy` seam in SS-03 exists to receive it without a rewrite.

**Greenfield.** The project directory is empty and not a git repo. There are no existing
codebase conventions to match; SS-01 establishes them.

**Verified library research exists and is authoritative for this spec.** `Caleb's Vault`
holds `Software/pikepdf/` (32 notes), plus `Software/img2pdf/`, `Software/pypdfium2/`,
`Software/pdfimpose/`, `Software/cpdf/`, `Software/PDF Arranger/`, and
`Software/Stirling PDF/`. Every pikepdf code sample there is **executed output** against
pikepdf 10.11.0 / libqpdf 12.3.2 on Windows 11, not composed API. A full 8-page
saddle-stitch imposition runs end to end in `[[pikepdf - Imposition and Signature Recipe]]`.
Workers should read the relevant note before writing PDF code — these recipes are the
reference implementation, and three independent research threads converged on the same
stack (pikepdf + img2pdf + pypdfium2), one of them by overruling pikepdf's own docs, which
recommend AGPL-licensed PyMuPDF for rendering.

## Requirements

1. A PDF and a directory of images import into a single project, interleaved, with images
   naturally sorted and EXIF orientation honored.
2. Pages reorder by drag, rotate, skip, and accept inserted blanks, with undo.
3. Paper size, gutter width, binding edge, and scale mode are configurable and update the
   layout live.
4. `Imposer` computes one placement transform per output page, consumed verbatim by the
   exporter — no consumer recomputes or adjusts it. **The preview and the print path both
   render the exported PDF rather than re-drawing the layout**, so there is exactly one
   implementation of imposition in the codebase and nothing to drift against.
5. Content falling outside the printer's imageable area is flagged before printing, and
   distinguished from content pushed off the page by Deckle's own transform.
6. A printer is calibrated once; the resulting profile persists and governs all reload
   instructions for that printer.
7. A job prints as pass 1 → derived reload instruction → pass 2 and collates correctly.
8. "Test one sheet first" is available on every pass.
9. Any subset of sheets can be reprinted through the same code path as a full job.
10. A pass interrupted mid-job resumes at sheet granularity.
11. `deckle.core` imports no Qt and is exercisable headlessly via a CLI.
12. Every submitted print job's full parameters are written to a session log.
13. Zero AGPL dependencies; no external runtime binaries.
14. The Pinebox golden-fixture regression passes.

## Sub-Specs

---
sub_spec_id: SS-01
phase: run
depends_on: []
---

### 1. Project scaffold, repo files, and core data models

- **Scope:** Establish the repository and the pure-data vocabulary every other sub-spec
  depends on. This sub-spec sets the conventions (naming, typing, test layout) that all
  later sub-specs follow. No behavior, only shapes.
- **Files (new):**
  - `.gitignore`
  - `README.md`
  - `CHANGELOG.md`
  - `pyproject.toml`
  - `deckle/__init__.py`
  - `deckle/core/__init__.py`
  - `deckle/core/models.py`
  - `tests/__init__.py`
  - `tests/test_models.py`
  - `tests/test_core_purity.py`
- **Decisions:** All model types are frozen dataclasses with full type annotations.
  **`LICENSE` already exists** at the repo root — MIT, copyright "Caleb Bennett", committed
  with GitHub's initial commit. Do not create or overwrite it; verify only.
  `CHANGELOG.md` follows Keep a Changelog with an `## [Unreleased]` section. `.gitignore`
  covers Python, PyInstaller (`build/`, `dist/`, `*.spec`), Qt (`*.qmlc`),
  `tests/fixtures/*.pdf`, and `*.stackdump` (Git Bash leaves these in the working tree).
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/models.py` defines `Placement(scale_x: float, scale_y: float, tx: float, ty: float, rotate_deg: int)` as a frozen dataclass, in PDF points, origin bottom-left.
  - `[STRUCTURAL]` `deckle/core/models.py` defines `SourceRef(path: str, page_index: int, sha256: str, width_pt: float, height_pt: float)`, `SourcePage(ref: SourceRef, rotate_deg: int, skipped: bool)`, `OutputPage(source_ref: SourceRef | None, placement: Placement, is_filler: bool)`, `Sheet(index: int, front: OutputPage | None, back: OutputPage | None)`, and `SheetPlan(sheets: list[Sheet], paper_pt: tuple[float, float], warnings: list[LayoutWarning])`.
  - `[STRUCTURAL]` `deckle/core/models.py` defines `Project(pages: list[SourcePage], layout: LayoutSettings, printer: str | None)` — the document model consumed by SS-07 and SS-09.

  > **Two fields added after red-team (C-2, C-3), both load-bearing:**
  > `SourceRef.width_pt` / `height_pt` carry each page's own media-box geometry. Without them
  > `Imposer` cannot compute a per-page transform without opening the PDF — which would
  > violate its purity requirement *and* trip its own no-I/O check. SS-02 already reads this
  > geometry at load; it simply has to store it.
  > `SheetPlan.paper_pt` carries the output sheet size. Without it `Exporter` cannot call
  > `add_blank_page(page_size=...)` and `Rasterizer` cannot size its canvas — neither
  > receives `LayoutSettings`. Paper size is a property of the plan, not of a consumer.
  - `[STRUCTURAL]` `deckle/core/models.py` defines `LayoutSettings` with fields `paper: tuple[float, float]`, `gutter_pt: float`, `binding_edge: Literal["left","right"]`, `scale_mode: Literal["fit_height","fixed_gutter"]`, `start_on_recto: bool`, `landscape_policy: Literal["rotate","scale","letterbox"]`, and that `scale_mode` defaults to `"fit_height"`. (Red-team P-5: `"top"` removed — top-edge binding needs a vertical shift that no sub-spec specified, and it is a different physical workflow. Deferred to v2.)
  - `[STRUCTURAL]` `deckle/core/models.py` defines `LayoutWarning(sheet_index: int, kind: Literal["clipped_by_page","clipped_by_imageable_area","mixed_orientation","mixed_dpi"], detail: str)`.
  - `[MECHANICAL]` `python -m pytest tests/test_core_purity.py` passes — the test walks every module under `deckle/core/`, imports it, and asserts no `PySide6` or `PyQt` module appears in `sys.modules` attributable to it.
  - `[MECHANICAL]` `python -c "import deckle.core.models"` exits 0 with no third-party import required.
  - `[STRUCTURAL]` `pyproject.toml` declares dependencies `pikepdf`, `pypdfium2`, `img2pdf`, `natsort`, `Pillow`, `PySide6` and declares `license = "MIT"`. It does **not** declare `pypdf` — pikepdf is the sole PDF manipulation library.
  - `[STRUCTURAL]` `README.md` states the project purpose, the four-level page model (source → output → sheet → pass), the MIT license, and a "not yet released" status.

---
sub_spec_id: SS-02
phase: run
depends_on: ['SS-01']
---

### 2. SourceLoader — PDF and image ingestion

- **Scope:** Turn files on disk into `SourcePage` records. Images are normalized to PDF
  pages here via `img2pdf` so nothing downstream ever sees an image. Import is
  metadata-only — no rasterization, no page copying.
- **Files (new):**
  - `deckle/core/loader.py`
  - `tests/test_loader.py`
- **Decisions:** `load_pdf(path: str) -> list[SourcePage]` and
  `load_image_dir(path: str) -> list[SourcePage]` are the public entry points.
  `load_image_dir` writes one normalized PDF per import into a cache directory under the
  OS temp dir and returns `SourceRef`s pointing at it. Both loaders populate
  `SourceRef.width_pt` / `height_pt` from each page's own media box — `Imposer` depends on
  this and cannot read it itself.

  **Three verified img2pdf requirements** (`[[img2pdf Research Hub]]`), each a real failure
  mode rather than a preference:

  1. **`rotation=img2pdf.Rotation.ifvalid` is mandatory.** EXIF Orientation 0 — common from
     phones and scanners — otherwise raises and **kills the entire batch**, not just the one
     image.
  2. **Missing DPI silently defaults to 96.0, not 72.** Left unhandled this is a
     wrong-size-in-print hazard that survives all the way to paper. Detect absent DPI
     explicitly and apply the fit-to-page fallback rather than inheriting 96.0.
  3. **Never let Pillow write the PDF.** A q95 JPEG round-tripped through Pillow's
     `PdfImagePlugin` was measured to change **68% of pixels**; `quality='keep'` does not
     help. Pass original bytes to img2pdf; materialize through Pillow only for pages that
     genuinely need pixel work.

  **img2pdf always centers images with symmetric borders — the gutter cannot come from it.**
  Gutter placement is `Imposer`'s job (SS-03) and `Exporter`'s (SS-04), applied afterward.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/loader.py` exposes `load_pdf(path: str) -> list[SourcePage]` and `load_image_dir(path: str) -> list[SourcePage]`.
  - `[BEHAVIORAL]` `load_image_dir` on a directory containing `img1.jpg`, `img2.jpg`, `img10.jpg` returns pages in the order 1, 2, 10 — not 1, 10, 2.
  - `[BEHAVIORAL]` An image with EXIF orientation 6 (rotate 90° CW) produces a page whose rendered content is upright.
  - `[BEHAVIORAL]` `load_pdf` on a 300-page PDF completes in under 2 seconds and allocates no page bitmaps (verified by asserting no `pypdfium2` render call occurs).
  - `[BEHAVIORAL]` `load_pdf` on a password-protected PDF raises `EncryptedPdfError` carrying the path, rather than a bare library exception.
  - `[BEHAVIORAL]` A directory of images with differing DPI values produces pages plus at least one `LayoutWarning` of kind `mixed_dpi`.
  - `[MECHANICAL]` `grep -rn "PIL\|Image.open" deckle/core/layout.py deckle/core/export.py` returns nothing — image handling exists only in the loader.

---
sub_spec_id: SS-03
phase: run
depends_on: ['SS-01']
---

### 3. Imposer and LayoutStrategy — the gutter-shift engine

- **Scope:** The layout math. Given source pages and settings, produce a `SheetPlan`. This
  is the highest-value testable code in the project and must be a pure function. Both scale
  modes ship. The `LayoutStrategy` interface is the v2 seam for signature imposition.
- **Files (new):**
  - `deckle/core/layout.py`
  - `tests/test_layout.py`
- **Decisions:** `LayoutStrategy` is a Protocol with the single method
  `impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`.
  Strategies are stateless; all configuration arrives via `settings`.
  `GutterShiftStrategy` is the only MVP implementation. **Do not narrow the signature to
  gutter-specific parameters** — signatures and saddle stitch implement this same
  interface in v2.
  Scale modes: `fit_height` scales content to page height and the gutter is the remaining
  width (default). `fixed_gutter` reserves `settings.gutter_pt` and scales content to fit
  the remainder. Parity handling is explicit: `start_on_recto` and pad-to-even are separate
  settings, fillers are marked `is_filler=True`, and **exactly one padding pass runs**.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/layout.py` defines `class LayoutStrategy(Protocol)` with `def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan: ...`.
  - `[STRUCTURAL]` `deckle/core/layout.py` defines `class GutterShiftStrategy` implementing that Protocol, and `impose` is free of file, network, and print I/O.
  - `[BEHAVIORAL]` With `scale_mode="fixed_gutter"`, `gutter_pt=54` (0.75"), letter paper, **left binding edge**: **even-index** output pages (index 0, 2, 4 — the **rectos**) have `placement.tx == 54.0`, and **odd-index** (versos) have `placement.tx == 0.0`. Right binding edge is the mirror.

  > **Red-team P-1 — this criterion was inverted, and it is the single most expensive
  > possible bug in this project.** Index 0 is the first page, a **recto**, whose spine is on
  > the **left** — so it must be pushed **right** by the gutter. The earlier wording gave the
  > gutter to index 1 instead. Verified against `[[pikepdf - Gutter Shift Recipe]]` Method B:
  > `x = GUTTER if i % 2 == 0 else 0   # recto: push right off the spine`.
  >
  > This would have passed every numeric test in this sub-spec, because the tests encode the
  > same parity assumption as the criterion. That is exactly the failure mode the research
  > warns about: *"discovering after 200 printed pages that the gutter went the wrong way on
  > versos."* Hence the geometric assertion below.
  - `[BEHAVIORAL]` **Sign-inversion guard (not a numeric assertion):** after imposition, a recto's content bounding box sits **further from the binding edge** than a verso's. This catches a parity inversion that arithmetic tests cannot, because it asserts physical intent rather than a computed value.
  - `[BEHAVIORAL]` `binding_edge="right"` produces the exact mirror of `"left"` — rectos shifted left, versos flush right.
  - `[STRUCTURAL]` `impose` populates `SheetPlan.paper_pt` from `settings.paper`.
  - `[BEHAVIORAL]` With `scale_mode="fit_height"`, content scales to exactly the page height and the resulting gutter equals `page_width - (page_height * source_aspect)`.
  - `[BEHAVIORAL]` **Regression for defect 1:** a document whose page 3 has a different aspect ratio than page 1 produces a `Placement` for page 3 derived from page 3's own media box, not page 1's.
  - `[BEHAVIORAL]` **Regression for defect 2:** a 7-page document with `start_on_recto=True` and pad-to-even produces exactly 8 output pages with exactly one filler — never two.
  - `[BEHAVIORAL]` Every generated filler `OutputPage` has `is_filler=True` and `source_ref=None`.
  - `[BEHAVIORAL]` A landscape page inside a portrait document under `landscape_policy="rotate"` produces `placement.rotate_deg == 90`, and emits a `mixed_orientation` warning.
  - `[MECHANICAL]` `python -m pytest tests/test_layout.py -q` passes with at least 12 test cases.

---
sub_spec_id: SS-04
phase: run
depends_on: ['SS-01', 'SS-03']
---

### 4. Exporter — PDF composition via pikepdf Form XObjects

- **Scope:** Render a `SheetPlan` to a PDF on disk. Whole document or an arbitrary sheet
  subset, through one code path.
- **Files (new):**
  - `deckle/core/export.py`
  - `tests/test_export.py`
- **Decisions:** Composition follows **`[[pikepdf - Gutter Shift Recipe]]` Method B**,
  verified working on pikepdf 10.11.0: build the output sheet with
  `out.add_blank_page(page_size=...)`, obtain the source as
  `out.copy_foreign(Page(src).as_form_xobject())`, register it with
  `sheet.add_resource(formx, Name.XObject, prefix="Fx")`, then append
  `sheet.calc_form_xobject_placement(formx, name, rect, invert_transformations=True, ...)`
  via `sheet.contents_add()`.
  `allow_shrink` / `allow_expand` are **False** for `fixed_gutter` (exact 1:1 translation,
  no drift) and **True** for `fit_height` (rect sized to the scaled target).
  **Never use `add_overlay`** — it centers and best-fits, which is wrong for imposition.
  Call `out.remove_unreferenced_resources()` before save.
  The exporter **must not** modify, recompute, or clamp any `Placement`.
  **Memory constraint:** source PDFs stay open once content is copied out
  (`[[pikepdf - Performance and Memory]]`), so assembly is batched — save and reopen
  between chunks rather than holding one handle across a 500-page document.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/export.py` exposes `export(plan: SheetPlan, out_path: str, sheets: Sequence[int] | None = None) -> None`, where `sheets=None` means all.
  - `[STRUCTURAL]` `deckle/core/export.py` exposes `export_sheet_cached(plan: SheetPlan, sheet_index: int) -> str` — returns a path to a single-sheet PDF, cached by `(sheet_index, plan_hash)` in a bounded temp cache (default 200 entries), evicted LRU and cleared on project close.
  - `[BEHAVIORAL]` Requesting the same `(sheet_index, plan_hash)` twice performs the export **once** (call-counter test). Changing any layout setting invalidates the cache.

  > **Red-team P-4.** Adopting "the preview renders the exported PDF" (SS-05) means `export`
  > now runs on **every sheet navigation**. Without a cache and a defined temp lifecycle,
  > scrubbing a 300-page document means constant re-export, source-handle reopening, and
  > leaked temp files. This is the cost of the architecture change, paid explicitly rather
  > than discovered.
  - `[MECHANICAL]` `grep -n "calc_form_xobject_placement" deckle/core/export.py` returns at least one match.
  - `[MECHANICAL]` `grep -n "add_overlay\|merge_transformed_page" deckle/core/export.py` returns nothing.
  - `[MECHANICAL]` `grep -n "remove_unreferenced_resources" deckle/core/export.py` returns at least one match.
  - `[BEHAVIORAL]` For a `fixed_gutter` plan whose source already fits, the emitted content stream segment contains a pure translation of the form `1 0 0 1 <x> <y> cm` — no scale factor. Assert by parsing the generated bytes.
  - `[BEHAVIORAL]` Exporting a plan with `sheets=[6]` produces a 1-sheet (2-page) PDF whose content matches sheet 6 of the full export.
  - `[BEHAVIORAL]` Exporting to an unwritable path raises before any bytes are written — a partial file never appears on disk.
  - `[BEHAVIORAL]` A filler `OutputPage` exports as a genuinely blank page carrying no source content.
  - `[BEHAVIORAL]` Exporting a 500-page document completes without unbounded memory growth, using batched save-and-reopen.
  - `[MECHANICAL]` A test asserts the `Placement` objects passed into `export` are equal (by dataclass equality) to those produced by `Imposer` — proving no consumer-side adjustment.

---
sub_spec_id: SS-05
phase: run
depends_on: ['SS-01', 'SS-03', 'SS-04']
---

### 5. Rasterizer — preview rendering, thumbnails, and ink bounds

> **Architecture change adopted from research (2026-08-04). Read this before implementing.**
>
> **The preview renders the actual exported PDF. It never re-draws the layout independently.**
>
> `render_sheet` calls `export(plan, <temp>, sheets=[sheet_index])` and rasterizes *that
> artifact*. It does not composite from `Placement` objects in parallel with the exporter.
>
> Why this beats the original design: the failure mode of an imposition tool is discovering
> after 200 printed pages that the gutter went the wrong way on versos. A preview that
> re-draws the layout can be wrong in exactly the same way the exporter is wrong, and agree
> with it — two implementations of the same bug reassure you. **A rasterized artifact cannot
> lie**, because it *is* the thing that goes to the printer.
>
> This supersedes the earlier "one transform, three consumers plus a fidelity diff test"
> approach. It is strictly stronger: identity rather than agreement-within-tolerance, and it
> deletes a whole class of drift. SS-10's fidelity test correspondingly becomes a **guard
> that the preview path actually routes through `export`**, not a pixel diff.
>
> This is why SS-05 now depends on SS-04.

- **Scope:** Turn a `SheetPlan` sheet into pixels for preview, generate virtualized
  thumbnails, and compute ink bounding boxes for clipping detection. Returns raw buffers —
  no Qt types cross this boundary.
- **Files (new):**
  - `deckle/core/render.py`
  - `tests/test_render.py`
- **Decisions:** Rendering uses `pypdfium2`. Thumbnails are **scroll-driven and
  virtualized** — the API takes an explicit page range, never "all pages". Ink bbox is
  computed by rasterizing at low DPI (default 36) and scanning for non-background pixels;
  it is **cached per `SourceRef` and computed on demand**, never eagerly across a document.
  Cancellation is cooperative via a `threading.Event` token.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/render.py` defines `RenderedPage(width: int, height: int, rgba: bytes)` and exposes `render_sheet(plan: SheetPlan, sheet_index: int, side: Literal["front","back"], dpi: int, cancel: threading.Event | None = None) -> RenderedPage`.
  - `[BEHAVIORAL]` `render_sheet` produces its bitmap by exporting the requested sheet via `export(plan, <temp>, sheets=[sheet_index])` and rasterizing that PDF. Verified by patching `export` and asserting it is called — **a preview that does not route through the exporter is the defect this criterion exists to catch.**
  - `[MECHANICAL]` `grep -n "Placement" deckle/core/render.py` returns nothing in `render_sheet`'s body — the renderer consumes the exported artifact, not the transform.
  - `[STRUCTURAL]` `deckle/core/render.py` exposes `thumbnails(pages: Sequence[SourcePage], start: int, count: int, dpi: int = 36) -> list[RenderedPage]` — an explicit range, not a whole-document call.
  - `[STRUCTURAL]` `deckle/core/render.py` exposes `ink_bbox(ref: SourceRef, dpi: int = 36) -> tuple[float, float, float, float]` with per-`SourceRef` caching.
  - `[MECHANICAL]` `grep -rn "PySide6\|QImage\|QPixmap" deckle/core/render.py` returns nothing.
  - `[BEHAVIORAL]` `ink_bbox` called twice for the same `SourceRef` rasterizes only once (verified by call counter).
  - `[BEHAVIORAL]` `render_sheet` with an already-set cancel event returns promptly without completing a full render.
  - `[BEHAVIORAL]` A page that is entirely blank returns an empty/degenerate ink bbox rather than raising.

---
sub_spec_id: SS-06
phase: run
depends_on: ['SS-01', 'SS-03']
---

### 6. PrinterProfile and PassPlanner — the manual-duplex brain

- **Scope:** The pure logic that converts a `SheetPlan` plus a printer's calibrated
  behavior into ordered print passes with human reload instructions. No submission, no Qt,
  no I/O beyond profile persistence.
- **Files (new):**
  - `deckle/core/printing.py`
  - `deckle/core/profiles.py`
  - `tests/test_printing.py`
- **Decisions:** `PrinterProfile` is a dataclass persisted as JSON keyed by printer name
  under the OS config dir, carrying `flip_axis`, `output_face`, `feed_edge`,
  `reverse_stack`, `imageable_area_pt`, `calibrated_at`, `calibration_version`, and a
  mandatory `version` integer. Until SS-13 lands, profiles may also be selected from a
  small set of built-in presets — the planner must not require a calibration run to
  function. `PrintBackend` is a Protocol so this module never imports Qt.

  **Verified back-pass ordering table** (from `[[pikepdf - Manual Duplex Reordering]]`) —
  this is the mapping `plan_passes` implements, and it is physical behavior, not a guess:

  | Reload behavior | Back-pass order |
  |---|---|
  | Outputs face-down, stack reversed on reload | backs **as-is** |
  | Outputs face-up, stack retains order | backs **reversed** |
  | User flips on the long edge | back sides may also need 180° rotation |
  | User flips on the short edge | usually no rotation |

  Rotation is applied with `page.rotate(180, relative=True)` or by assigning
  `page.rotation` — **never by setting `page.Rotate` directly**, which older qpdf has
  mishandled when transforming pages. If a print driver ignores `/Rotate`, bake it in with
  `page.flatten_rotation()`.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/printing.py` defines `class PrintBackend(Protocol)` with `def submit(self, plan: SheetPlan, sheets: Sequence[int], printer_name: str, copies: int, dpi: int) -> PrintResult: ...` and `PrintResult(submitted: int, job_id: str | None, error: str | None)`.
  - `[STRUCTURAL]` `deckle/core/printing.py` exposes `plan_passes(plan: SheetPlan, profile: PrinterProfile, sheets: Sequence[int] | None = None) -> list[PrintPass]`, where `PrintPass(index: int, sheet_order: list[int], side: Literal["front","back"], reload_instruction: str, rotate_backs: bool)`.
  - `[STRUCTURAL]` The mapping from the four profile fields to the two behavioral axes is explicit and documented in the module: `reverse_stack` (derived from `output_face` + `feed_edge`) drives **sheet order**; `flip_axis == "long"` drives **`rotate_backs`**. The verified table describes behavior; this mapping is the implementation of it.
  - `[BEHAVIORAL]` A profile with `flip_axis="long"` yields `rotate_backs=True` on the back pass; `flip_axis="short"` yields `False`.

  > **Red-team P-2.** The verified table states long-edge flips *"may also need 180-degree
  > rotation"* on back sides — but `PrintPass` had no field to carry that, and no sub-spec
  > applied it. A specified behavior with nowhere to live. `rotate_backs` closes it; SS-08
  > applies it at submission with `page.rotate(180, relative=True)` — **never**
  > `page.Rotate =` — falling back to `flatten_rotation()` when a driver ignores `/Rotate`.
  - `[STRUCTURAL]` `deckle/core/profiles.py` defines `PrinterProfile` with fields `version: int`, `flip_axis: Literal["long","short"]`, `output_face: Literal["up","down"]`, `feed_edge: Literal["top","bottom"]`, `reverse_stack: bool`, `imageable_area_pt: tuple[float,float,float,float]`, `calibrated_at: str`, `calibration_version: int`, plus `save(name)` / `load(name)` JSON persistence.
  - `[BEHAVIORAL]` For a profile with `reverse_stack=True`, pass 2's `sheet_order` is the reverse of pass 1's.
  - `[BEHAVIORAL]` For a profile with `reverse_stack=False`, pass 2's `sheet_order` equals pass 1's.
  - `[BEHAVIORAL]` `plan_passes` with `sheets=[7]` produces passes covering only sheet 7 — the reprint path is the normal path with a smaller input, not a separate branch.
  - `[BEHAVIORAL]` Every `PrintPass` carries a non-empty `reload_instruction` naming the flip axis and the face direction in plain language.
  - `[STRUCTURAL]` At least two built-in presets exist so a user can print before SS-13 exists, covering at minimum the face-down/reversed and face-up/in-order reload behaviors from the verified table above.
  - `[MECHANICAL]` `grep -rn "PySide6" deckle/core/printing.py deckle/core/profiles.py` returns nothing.

---
sub_spec_id: SS-07
phase: run
depends_on: ['SS-02', 'SS-03', 'SS-04', 'SS-05']
---

### 7. Project persistence, session log, and CLI

- **Scope:** The `.deckle` project format, the print/session log required by the Intent
  constraints, and a headless CLI. The CLI is what makes the golden-fixture regression
  runnable in CI and is a shipped MVP feature.
- **Files (new):**
  - `deckle/core/project_io.py`
  - `deckle/core/session_log.py`
  - `deckle/cli.py`
  - `tests/test_project_io.py`
  - `tests/test_cli.py`
- **Decisions:** `.deckle` is JSON, top level
  `{"version": 1, "pages": [...], "layout": {...}, "printer": "..."}`, holding **references
  only** — `path`, `page_index`, `sha256`, plus per-page overrides. Never embed page data.
  Reopening with a changed `sha256` surfaces a `SourceChangedWarning` rather than silently
  substituting. CLI subcommands: `impose`, `export`, `info`. The CLI imports only
  `deckle.core` — never `deckle.app`.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/project_io.py` exposes `save_project(project, path)` / `load_project(path)` and every written file contains a top-level `"version": 1`.
  - `[BEHAVIORAL]` A round-trip save→load of a project with reordered, rotated, and skipped pages preserves all page state exactly.
  - `[BEHAVIORAL]` Loading a project whose source file hash no longer matches raises/returns a `SourceChangedWarning` naming the file, and does not substitute the new content silently.
  - `[STRUCTURAL]` `deckle/core/session_log.py` exposes `log_print_job(printer, profile, sheets, dpi, pass_index)` writing one structured record per call to a session log file.
  - `[MECHANICAL]` `python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/out.pdf --gutter 0.75in` exits 0 and produces a readable PDF.
  - `[MECHANICAL]` `grep -rn "deckle.app\|PySide6" deckle/cli.py` returns nothing.
  - `[MECHANICAL]` `python -m deckle.cli info tests/fixtures/sample.pdf` prints page count, detected page sizes, and any layout warnings.

---
sub_spec_id: SS-08
phase: run
depends_on: ['SS-06']
---

### 8. QtPrintBackend and the cross-platform print spike

- **Scope:** The only Qt code outside the UI. Implements `PrintBackend` by rasterizing at
  the printer's DPI and painting to `QPainter`. **Begins with a spike** validating the
  design's assumptions about Qt printing on both Windows and Linux before the
  implementation is trusted.
- **Files (new):**
  - `deckle/app/__init__.py`
  - `deckle/app/backend.py`
  - `docs/spikes/qprinter-capability-report.md`
  - `tests/test_backend.py`
- **Decisions:** Always call `QPrinter.setFullPage(True)`; margins come from the profile's
  `imageable_area_pt`, never from Qt's defaults. Jobs are submitted in **bounded chunks**
  (default 10 sheets) so a failure loses at most one chunk. Do **not** rely on
  `QPrinter.PaperSource` — tray selection is effectively Windows-only and the MVP does not
  need it.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `docs/spikes/qprinter-capability-report.md` records, for Windows and Linux, whether each of the following behaves as assumed: printer enumeration, `supportedDuplexModes` reporting, imageable-area querying via `pageLayout().paintRectPixels()`, device-DPI painting, and page-range submission. Each entry is marked confirmed / diverged / untested.
  - `[STRUCTURAL]` `deckle/app/backend.py` defines `class QtPrintBackend` implementing the `PrintBackend` Protocol from SS-06 with the exact `submit` signature.
  - `[MECHANICAL]` `grep -n "setFullPage(True)" deckle/app/backend.py` returns at least one match.
  - `[MECHANICAL]` `grep -n "PaperSource" deckle/app/backend.py` returns nothing.
  - `[BEHAVIORAL]` Submitting a 25-sheet pass with a chunk size of 10 produces 3 submissions, and a simulated failure on the second reports `submitted == 10`.
  - `[BEHAVIORAL]` Every `submit` call writes one record via `log_print_job` before returning.
  - `[BEHAVIORAL]` `rotate_backs=True` on a `PrintPass` applies `page.rotate(180, relative=True)` to every back side before submission, falling back to `page.flatten_rotation()` when the target driver is known to ignore `/Rotate`. `page.Rotate` is never assigned directly.
  - `[BEHAVIORAL]` A failed chunk **cancels the remaining chunks of that pass** rather than continuing to submit into a jammed or offline printer, and reports the count actually submitted.
  - `[STRUCTURAL]` If `QPrinterInfo.supportedDuplexModes()` reports real duplex capability, the backend offers a **single-pass duplex mode** alongside manual duplex. Deckle exists for duplexer-less printers, but it should not be *worse* than the driver on a printer that has one — and the user may not own this printer forever.
  - `[HUMAN REVIEW]` The spike report's diverged entries (if any) have been reviewed and either accepted or escalated per the Intent escalation triggers.

---
sub_spec_id: SS-09
phase: run
depends_on: ['SS-01', 'SS-02', 'SS-05']
---

### 9. Application shell, ImportView, and ArrangeView

- **Scope:** The main window, import flows, and the thumbnail grid with drag reorder,
  rotate, skip, insert-blank, and undo. All rendering off the UI thread.
- **Files (new):**
  - `deckle/app/main.py`
  - `deckle/app/state.py`
  - `deckle/app/views/__init__.py`
  - `deckle/app/views/import_view.py`
  - `deckle/app/views/arrange_view.py`
  - `tests/test_app_state.py`
- **Decisions:** Undo is **snapshot-based** — a bounded deque of serialized `Project`
  snapshots, default depth 50. `deckle/app/state.py` owns the `Project` instance and the
  undo stack; views mutate through it and never hold their own copy. Thumbnails are
  requested for the visible range only, via `render.thumbnails(pages, start, count)`.
  **Reordering operates on the in-memory `list[SourcePage]`** — plain Python value objects,
  never a `pikepdf.Pdf.pages` list. This sidesteps the slice-assignment corruption trap
  entirely (see Edge Cases); the constraint exists so it is never introduced later.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/app/state.py` exposes `AppState` with `project`, `undo()`, `redo()`, and `mutate(fn)` — where `mutate` snapshots before applying.
  - `[BEHAVIORAL]` Reordering pages then calling `undo()` restores the previous order exactly.
  - `[BEHAVIORAL]` Performing 60 mutations leaves the undo stack at its bounded depth of 50, and the oldest snapshots are discarded without error.
  - `[BEHAVIORAL]` Importing a 300-page PDF populates the arrange grid and requests thumbnails only for the visible range — verified by asserting `thumbnails` is called with `count` well under 300.
  - `[BEHAVIORAL]` The UI thread remains responsive during a 300-page import — asserted concretely as: no synchronous `render_sheet` or `thumbnails` call occurs on the main thread, and no single main-thread operation exceeds 100 ms.
  - `[BEHAVIORAL]` **Autosave:** every `AppState.mutate` schedules a debounced (500 ms) `save_project` to the project's autosave path. Killing the process after a mutation and reopening restores the last mutated state. (Red-team C-5: autosave was stated in Edge Cases but owned by no sub-spec — `mutate` is the only correct hook for it.)
  - `[BEHAVIORAL]` Zero printers installed is a defined state: the print action is disabled with an explanatory message rather than opening an empty dialog or raising. (Red-team A-1.)
  - `[STRUCTURAL]` `ArrangeView` supports drag reorder, per-page rotate, per-page skip, and insert-blank, each routed through `AppState.mutate`.

---
sub_spec_id: SS-10
phase: run
depends_on: ['SS-03', 'SS-05', 'SS-09']
---

### 10. LayoutPanel and PreviewView with imageable-area guide

- **Scope:** Live layout controls and the sheet-accurate preview. This is where the app's
  central honesty requirement is enforced: the imageable area is drawn, and the two
  distinct clipping causes are reported separately.
- **Files (new):**
  - `deckle/app/views/layout_panel.py`
  - `deckle/app/views/preview_view.py`
  - `tests/test_preview_fidelity.py`
- **Decisions:** Changing any layout setting re-runs `Imposer` over the whole document
  immediately (arithmetic only, no rendering) and re-renders only the visible sheet.
  Warnings attach to the sheet they affect and appear as a badge on that sheet — **never a
  global modal**. `LayoutPanel` exposes the scale-mode toggle with `fit_height` preselected.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `PreviewView` renders sheet-by-sheet with a front/back toggle and draws the imageable-area boundary from the active `PrinterProfile` as a visible guide.
  - `[BEHAVIORAL]` Content extending past the page box produces a warning of kind `clipped_by_page`; content inside the page but outside the imageable area produces `clipped_by_imageable_area`. The two are reported with different text.
  - `[BEHAVIORAL]` Changing the gutter width updates the preview without a full-document re-render (assert only the visible sheet is rasterized).
  - `[BEHAVIORAL]` A warning on sheet 12 is visible when viewing sheet 12 and does not raise a modal dialog.
  - `[STRUCTURAL]` `LayoutPanel` offers both scale modes with `fit_height` selected by default.
  - `[MECHANICAL]` `python -m pytest tests/test_preview_fidelity.py` passes. It asserts the preview path **routes through `export`** — patch `deckle.core.export.export`, render a sheet, assert it was called with `sheets=[sheet_index]`. **The preview is the exported PDF rasterized, so there is nothing to diff.** (Red-team C-4: the earlier pixel-tolerance formulation was untestable as written — no tolerance was ever specified — and the routing guard is both concrete and strictly stronger.)

---
sub_spec_id: SS-11
phase: run
depends_on: ['SS-06']
---

### 11. PrintSession — the resumable print state machine

- **Scope:** The pure state machine behind a print run: pass sequencing, chunked
  submission, disk-backed state, and sheet-granular resume. **No Qt.** Split out of the
  print dialog so the logic that must be correct is unit-testable headlessly, separate from
  the UI that must be usable.
- **Files (new):**
  - `deckle/core/print_session.py`
  - `tests/test_print_session.py`
- **Decisions:** `PrintSession` persists its state to disk after every chunk so an
  interrupted job survives a crash or a printer disappearing. On resume the caller supplies
  the completed sheet count — **software cannot know how many sheets physically emerged, so
  the session takes it as input rather than inferring it.** "Test one sheet" is a session
  mode, not a UI behavior: `start(..., test_first=True)` submits exactly one sheet and
  parks the session awaiting confirmation. All submission goes through the injected
  `PrintBackend` Protocol, so tests use a stub and never touch a printer.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/core/print_session.py` exposes `PrintSession(plan, profile, backend, sheets=None, test_first=False)` with `start()`, `advance()`, `confirm_test_sheet()`, `resume(sheets_completed: int)`, and a `state` property.
  - `[STRUCTURAL]` Session state persists to disk as JSON carrying a `version` integer, the pass index, the sheet cursor, and the printer name.
  - `[MECHANICAL]` `grep -rn "PySide6\|QtWidgets" deckle/core/print_session.py` returns nothing.
  - `[BEHAVIORAL]` A full run against a stub backend emits pass 1, then exposes the `reload_instruction` from `PrintPass`, then pass 2 — in that order.
  - `[BEHAVIORAL]` With `test_first=True`, exactly 1 sheet is submitted and no further submission occurs until `confirm_test_sheet()` is called.
  - `[BEHAVIORAL]` Discarding the session object mid-pass and reconstructing it from disk, then calling `resume(30)` on a 60-sheet pass, continues at sheet 31.
  - `[BEHAVIORAL]` `sheets=[7, 9]` submits only those sheets, routed through `plan_passes` — not a separate reprint branch.
  - `[BEHAVIORAL]` A backend returning `PrintResult(error=...)` leaves the session in a resumable state rather than discarding it.
  - `[STRUCTURAL]` `resume(sheets_completed: int)` is **per-pass, not cumulative** — the count refers to sheets emerged during the interrupted pass only. Documented in the docstring, since the ambiguity is genuine and guessing wrong misprints the remainder.
  - `[STRUCTURAL]` Each session carries a stable `session_id` derived from `(plan_hash, printer_name, started_at)`. `list_resumable() -> list[SessionSummary]` enumerates them, so a caller facing several interrupted sessions can choose rather than being handed "the" one.
  - `[BEHAVIORAL]` A session that completes successfully deletes its state file. Only interrupted sessions persist.
  - `[INTEGRATION]` Each submitted chunk writes one `log_print_job` record with printer, profile, sheet range, DPI, and pass index populated.

---
sub_spec_id: SS-12
phase: run
depends_on: ['SS-08', 'SS-10', 'SS-11']
---

### 12. PrintDialog — the manual-duplex print UI

- **Scope:** The Qt front end for a print run: choose printer and sheets, drive
  `PrintSession`, present the reload instruction between passes, and offer resume on
  reopening an interrupted job. **Presentation only — all sequencing lives in SS-11.**
- **Files (new):**
  - `deckle/app/views/print_dialog.py`
  - `tests/test_print_dialog.py`
- **Decisions:** The dialog owns no print logic. It constructs a `PrintSession` with a
  `QtPrintBackend` and calls its methods; it never computes pass order, sheet cursors, or
  reload text. The resume prompt asks **"How many sheets came out?"** and passes the answer
  straight to `PrintSession.resume()`.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `deckle/app/views/print_dialog.py` constructs `PrintSession` with a `QtPrintBackend` and contains no pass-ordering or sheet-cursor arithmetic.
  - `[MECHANICAL]` `grep -n "reverse\|sheet_order\|% 2" deckle/app/views/print_dialog.py` returns nothing — ordering logic must not leak into the UI.
  - `[BEHAVIORAL]` The dialog lists available printers and preselects the one with a saved `PrinterProfile`, if any.
  - `[BEHAVIORAL]` Between passes the dialog displays the `reload_instruction` verbatim from `PrintPass` and blocks until the user confirms.
  - `[BEHAVIORAL]` "Test one sheet first" is offered on every pass and maps to `test_first=True`.
  - `[BEHAVIORAL]` Reopening the app with an interrupted session on disk offers to resume and prompts for the completed sheet count.
  - `[BEHAVIORAL]` A printer offline at submission surfaces a blocking modal naming the printer; the session remains resumable afterward.

---
sub_spec_id: SS-13
phase: run
depends_on: ['SS-06', 'SS-08']
dispatch: manual
---

### 13. Calibration wizard — state-space enumeration then implementation

- **Scope:** The novel component. **This is a spike before it is a feature.** The design
  classifies it as the one Complex-domain element: no prior art attempts it, and the
  original "2–3 questions" claim is unsupported. Enumerate the state space first, derive
  the question count from it, then build.
- **Files (new):**
  - `docs/spikes/calibration-state-space.md`
  - `deckle/core/calibration.py`
  - `deckle/app/views/calibration_wizard.py`
  - `tests/test_calibration.py`
- **Decisions:** Marked `dispatch: manual` because it requires a physical printer and a
  human looking at paper — an agent cannot complete it unattended. The state space is
  *flip axis × output face × feed edge × stack order* (up to 16 combinations); two or three
  binary questions cannot resolve that. Expect four to five questions, **or** a smarter
  test sheet whose asymmetric corner glyphs disambiguate several axes in a single look.
  The enumeration document must live in the repo, not in someone's head.
- **Acceptance criteria:**
  - `[STRUCTURAL]` `docs/spikes/calibration-state-space.md` enumerates every reachable combination of `flip_axis`, `output_face`, `feed_edge`, and `reverse_stack`, marks which are physically distinguishable from a printed test sheet, and states the derived minimum question count.
  - `[STRUCTURAL]` `deckle/core/calibration.py` exposes `build_test_plan() -> SheetPlan` producing a 4-sheet plan with asymmetric per-corner glyphs and visible sheet numbers on both sides, with `paper_pt` set.
  - `[STRUCTURAL]` Test-sheet content is authored with **pikepdf's `Canvas`/`Text`**, and the design is constrained to what it can render reliably: simple geometric corner marks (filled triangle, open square, filled circle, open circle — chosen so each is distinguishable at a glance and none is rotationally symmetric with another) plus one- or two-digit sheet numbers.

  > **Red-team P-3.** This is the only sub-spec that *authors* PDF content rather than
  > transforming it, and the dependency set has no strong text engine — `reportlab` was
  > dropped, and pikepdf's own docs warn its text support is rudimentary (*"You've been
  > warned"*). Resolution: constrain the test sheet to what pikepdf can do well. Glyphs and
  > two digits are well within it. **If it proves insufficient, add `reportlab` — it is BSD
  > and creates no licensing problem** — but do not add it speculatively.
  >
  > **Rotational asymmetry is the functional requirement**, not decoration: a symmetric mark
  > cannot distinguish a 180° rotation from none, which is exactly the ambiguity being
  > resolved.
  - `[STRUCTURAL]` `deckle/core/calibration.py` exposes `derive_profile(answers: Mapping[str, str]) -> PrinterProfile` — pure, no Qt, no I/O.
  - `[BEHAVIORAL]` `derive_profile` maps each enumerated answer combination to exactly one profile, with no combination producing an ambiguous or unreachable result (test iterates the full enumeration).
  - `[BEHAVIORAL]` The wizard captures the observed imageable area from the test sheet and writes it into `PrinterProfile.imageable_area_pt`.
  - `[BEHAVIORAL]` Completing the wizard persists a profile that survives an application restart.
  - `[HUMAN REVIEW]` A real calibration run against the user's actual printer produces a profile that yields correctly-collated double-sided output.

---
sub_spec_id: SS-14
phase: run
depends_on: ['SS-07', 'SS-09', 'SS-10', 'SS-12']
---

### 14. Integration wiring, golden-fixture regression, and dependency audit

- **Scope:** Wire every module into its entry points, prove the end-to-end flow, and gate
  the licensing constraint automatically. This is the sub-spec that turns parts into an
  application.
- **Files (new):**
  - `deckle/__main__.py`
  - `tests/test_integration.py`
  - `tests/test_golden_pinebox.py`
  - `tests/test_license_audit.py`
  - `tests/fixtures/README.md`
  - `docs/CONTRIBUTING.md`
- **Decisions:** `deckle/__main__.py` is the GUI entry point (`python -m deckle`);
  `deckle/cli.py` remains the headless one. The Pinebox source PDF is ~30 MB and lives
  outside the repo — `tests/fixtures/README.md` documents the expected local path and the
  test **skips with a clear message** when the fixture is absent, rather than failing CI.
  Packaging is explicitly **not** in scope here (deferred decision) — do not add
  PyInstaller specs, installers, or AppImage recipes.
- **Acceptance criteria:**
  - `[INTEGRATION]` `python -m deckle` launches the application, and every view from SS-09, SS-10, SS-12, and SS-13 is reachable through the UI — no orphaned view modules.
  - `[INTEGRATION]` An end-to-end test drives: import a PDF → import an image directory → reorder → set gutter → impose → preview → export → plan passes → submit through a stubbed `PrintBackend`, asserting the final `SheetPlan` and the stub's received sheet order.
  - `[MECHANICAL]` A test asserts every module under `deckle/app/views/` is imported by `deckle/app/main.py` — catching orphaned views.
  - `[BEHAVIORAL]` `tests/test_golden_pinebox.py` reproduces the Pinebox output from its source and asserts: no double-padding (defect 2), per-page aspect handling (defect 1), and a page count matching the expected parity. It **skips with a clear message** when the fixture is unavailable.
  - `[MECHANICAL]` `python -m pytest tests/test_license_audit.py` passes: it enumerates installed distribution licenses and fails if any is AGPL, or if `PyMuPDF` or `fitz` is present.
  - `[MECHANICAL]` `grep -rn "PyMuPDF\|import fitz" deckle/ tests/` returns nothing.
  - `[MECHANICAL]` `python -m pytest -q` passes with zero failures across the full suite.
  - `[STRUCTURAL]` `docs/CONTRIBUTING.md` documents the core-purity rule, the four-level page model, and the "no external runtime binaries" constraint.

## Edge Cases

**Disambiguations** (abstract language resolved so agents don't guess):

- **"handles mixed page sizes"** → *Permissive.* Never reject a document. Compute each
  page's transform from its own media box, apply `landscape_policy`, and warn.
- **"validates the source file"** → *Permissive at import, strict at export.* Import what
  can be read and report the rest; refuse to export when the output path is unwritable.
- **"error handling"** → Three tiers: **blocking** (modal, names the file), **warning**
  (badge on the affected sheet, aggregated pre-print), **info** (session log). Warnings are
  never modal.
- **"supports manual duplex"** → Two passes with a reload instruction derived from a saved
  profile. Not a driver duplex flag.

**pikepdf API traps** — all four verified against 10.11.0 / libqpdf 12.3.2 on Windows 11.
Each costs roughly an afternoon if met late. Source: `[[pikepdf - Gotchas and Limitations]]`.

1. **`page.mediabox` returns an `Array`, not a `Rectangle`.** `.width` raises. Always wrap:
   `Rectangle(page.mediabox)`.
2. **`page.Contents` may be an `Array` of streams.** Call `contents_coalesce()` before
   reading or appending.
3. **Never reorder `pdf.pages` by tuple-swap or slice assignment.** pikepdf treats both as
   copies — it duplicates pages, assigns fresh `objgen`s, and breaks bookmarks, links, and
   named destinations. Correct pattern is detach-then-reinsert (`del` then `insert`), which
   preserves `objgen`; or build a fresh `Pdf` and append in target order. Deckle's own page
   reordering operates on the in-memory `list[SourcePage]`, so it avoids this by
   construction — the rule exists to keep it that way.
4. **`add_overlay` centers and best-fits.** Right for watermarks, wrong for imposition. Use
   the `as_form_xobject` + `calc_form_xobject_placement` path.

Also: **gutter-shift sign convention is inverted from intuition.** Shifting the MediaBox
*left* (`llx - g`) makes content appear to move *right* — you are moving the window, not
the picture. Deckle uses Method B (place onto a new sheet) rather than MediaBox shifting,
so this does not apply to the export path, but it will bite anyone reading the recipe notes
and reaching for Method A.

**Red-team advisories, resolved:**

- **A-2 — untrusted PDF parsing is the app's entire attack surface.** Deckle opens arbitrary
  downloaded PDFs through QPDF and PDFium, both of which have a CVE history. Pin minimum
  versions of `pikepdf` and `pypdfium2` in `pyproject.toml`, document the update policy in
  `CONTRIBUTING.md`, and state that Deckle never requires elevated privileges. There is no
  network path, so the residual risk is local parser exploitation only.
- **A-3 — `.deckle` files carry filesystem paths and may be shared.** Validate paths on load;
  a path resolving outside the project directory or the user's chosen roots prompts for
  confirmation rather than opening silently.
- **A-4 — a moved or deleted source is a different failure from a changed one.**
  `SourceChangedWarning` covers hash mismatch. Add `SourceMissingError` with a *relocate*
  action — this is the more common case and the design promised it.
- **A-5 — the session log rotates.** Cap at 5 MB with 3 generations retained. An append-only
  log that grows forever is a support problem, not an observability feature.
- **A-6 — `deckle --version` prints the app version plus `pikepdf`, `pypdfium2`, `img2pdf`,
  and `PySide6` versions.** First thing anyone asks for in a bug report.
- **A-7 — the img2pdf normalization cache is bounded and cleaned.** Cap at 2 GB, evict
  least-recently-used on startup. Heavy image import otherwise accumulates silently in temp.
- **A-8 — "without unbounded memory growth" is measurable:** peak RSS during a 500-page
  export stays under 4× the peak observed for a 50-page export.
- **A-9 — the gutter unit parser accepts `in`, `pt`, `mm`, `cm`** with an optional space, and
  a bare number is interpreted as points. Invalid units are a blocking error naming the
  accepted set.

**Scenarios and handling:**

- Encrypted PDF → prompt for password once; owner-password-only proceeds with a note.
- Corrupt PDF → attempt recovery; on failure name the offending page and import the rest.
- Source moved or changed → `SourceChangedWarning` naming the file, with relocate / accept
  options. Never silently substitute.
- Driver misreports duplex capability → the saved `PrinterProfile` always wins. Drivers are
  frequently wrong; the calibration sheet is ground truth.
- 500-page document → metadata-only import, virtualized thumbnails, on-demand ink bbox,
  bounded caches, all rendering off the UI thread with cancellation.
- Mixed or missing image DPI → fall back to fit-to-page and warn at import.
- Disk full → fail before writing, not halfway through.
- Crash → autosave on every mutation; references-only format keeps it cheap.
- Blank page → degenerate ink bbox, not an exception.
- Printer offline mid-job → chunked submission bounds the loss; session state stays
  resumable; resume asks how many sheets emerged.

## Out of Scope

- Signature imposition, saddle stitch, perfect binding, creep correction (v2 — the
  `LayoutStrategy` seam exists for them, nothing else is built)
- N-up / multiple pages per sheet side (arrives with signatures)
- PDF content editing: text, annotations, forms
- OCR and scanner integration
- Cloud, sync, accounts, telemetry, or any network access
- macOS support (`PrintBackend` stays portable, but no macOS work)
- PDF layer (OCG) selection — the prior script stubbed it out because it did not work
- **Packaging and installers** — one-file vs one-folder is a deferred decision. No
  PyInstaller specs, Inno Setup scripts, or AppImage recipes in this MVP.
- Vendoring HornPenguin's signature math (v2, and requires license verification first)
- Embedding `pdfimpose` or `cpdf` — both AGPL, see Constraints

**Carried forward to v2 — creep, correctly sized.** No surveyed tool has working
creep/bottling compensation: `pdfimpose --creep` exists but its own help text reads *"This
option is broken"* ([issue #36](https://framagit.org/spalax/pdfimpose/-/issues/36)), and
Stirling's is an open request. But **creep matters much less than its prominence suggests**
— at 4–6 sheets per signature it is sub-millimetre. **Keep signatures thin and it
evaporates.** Treat it as a refinement for thick signatures, not a v2 blocker. The genuine
disqualifier is tools that *rescale instead of shift*, which is a property Deckle already
has correct via pikepdf.

**v2 idea worth capturing now:** Bookbinder JS emits **sewing station marks** and
**signature order marks**, which eliminate the pricking jig and make mis-collation visible
before sewing. That is a small feature with outsized physical payoff for hand binding.

**v2 test oracle:** `pdfimpose` is AGPL so it cannot be embedded — but it can be used as a
**development-time oracle** to diff Deckle's imposition matrix against. Dev tooling only,
never a shipped or test-suite dependency.

## Constraints

**Musts:**
- `deckle.core` imports no Qt, enforced by an automated test (SS-01).
- One placement transform per output page, computed by `Imposer`, consumed verbatim by
  rasterizer, exporter, and print backend.
- The printer's imageable area is modeled wherever content bounds are evaluated.
- Every submitted print job logs its full parameters.
- All rendering and printing run off the UI thread with cancellation.
- Warnings attach to the sheet they affect.
- Every persisted format carries a version integer from its first commit.
- Upstream copyright notices preserved in any vendored file.

**Must-Nots:**
- No AGPL dependency, and no dependency requiring a commercial license. Hard gate.
- No network request of any kind — no telemetry, update checks, or analytics.
- No external runtime binary (no Poppler, Ghostscript, or ImageMagick).
- No consumer may recompute or adjust a `Placement`.
- No parallel image code path downstream of `SourceLoader`.
- **Never reorder a `pikepdf.Pdf.pages` list via tuple-swap or slice assignment.** pikepdf
  cannot distinguish reorder from copy in those forms; it duplicates pages, assigns new
  `objgen`s, and silently breaks internal references. Build a fresh `Pdf` and append in
  target order, or detach-and-reinsert (`del` then `insert`).
- **Never use `pikepdf.add_overlay` for imposition** — it centers and best-fits.
- **Never set `page.Rotate` directly** — use `page.rotate(..., relative=True)` or assign
  `page.rotation`.
- No use of `pypdf` — pikepdf is the sole PDF manipulation library.
- **Never import or bundle `pdfimpose` or `cpdf`.** Both are AGPL-3.0 (pdfimpose also pulls
  in AGPL PyMuPDF), so either would make Deckle AGPL. They are excellent tools to run
  personally, and a worker looking to "not reinvent the wheel" will find them — this rule
  exists because that is a live temptation, not a hypothetical one.
- No reliance on `QPrinter.PaperSource`.

**Preferences:**
- Printed physical correctness over everything else.
- Honest reporting over reassuring UI.
- Core purity and testability over convenience.
- Simple and obviously correct over clever or general.
- Consistency with the four-level page model over locally-nicer naming.

**Escalation triggers:**
- A dependency change would introduce AGPL, a commercial license, or an external binary.
- Qt print behavior diverges from SS-08's assumptions on either platform.
- The SS-13 state-space enumeration shows more than five questions are needed, or that the
  axes are not independently determinable.
- Any acceptance criterion appears unachievable as written.
- Vendoring any third-party source.

## Phase Specs

Refined by `/forge-prep` on 2026-08-04.

| Sub-Spec | Wave | Phase Spec |
|---|---|---|
| SS-01. Scaffold, repo files, core data models | 1 | `docs/specs/deckle-mvp/sub-spec-1-scaffold-core-models.md` |
| SS-02. SourceLoader — PDF and image ingestion | 2 | `docs/specs/deckle-mvp/sub-spec-2-sourceloader-ingestion.md` |
| SS-03. Imposer and LayoutStrategy | 2 | `docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md` |
| SS-04. Exporter — pikepdf Form XObjects | 3 | `docs/specs/deckle-mvp/sub-spec-4-exporter-pikepdf-composition.md` |
| SS-05. Rasterizer — preview, thumbnails, ink bounds | 3 | `docs/specs/deckle-mvp/sub-spec-5-rasterizer-preview-ink-bounds.md` |
| SS-06. PrinterProfile and PassPlanner | 3 | `docs/specs/deckle-mvp/sub-spec-6-printerprofile-passplanner.md` |
| SS-07. Persistence, session log, and CLI | 4 | `docs/specs/deckle-mvp/sub-spec-7-persistence-log-cli.md` |
| SS-08. QtPrintBackend and print spike | 4 | `docs/specs/deckle-mvp/sub-spec-8-qtprintbackend-spike.md` |
| SS-09. App shell, ImportView, ArrangeView | 4 | `docs/specs/deckle-mvp/sub-spec-9-app-shell-import-arrange.md` |
| SS-11. PrintSession state machine | 4 | `docs/specs/deckle-mvp/sub-spec-11-printsession-state-machine.md` |
| SS-10. LayoutPanel and PreviewView | 5 | `docs/specs/deckle-mvp/sub-spec-10-layout-panel-preview.md` |
| SS-13. Calibration wizard (`dispatch: manual`) | 5 | `docs/specs/deckle-mvp/sub-spec-13-calibration-wizard.md` |
| SS-12. PrintDialog | 6 | `docs/specs/deckle-mvp/sub-spec-12-print-dialog.md` |
| SS-14. Integration, golden fixture, license audit | 7 | `docs/specs/deckle-mvp/sub-spec-14-integration-golden-fixture.md` |

Index: `docs/specs/deckle-mvp/index.md`
Interface contracts: `docs/specs/deckle-mvp/contracts.json` (30 contracts)

## Verification

End-to-end confirmation that the MVP is complete:

1. `python -m pytest -q` passes with zero failures (SS-14).
2. `python -m pytest tests/test_core_purity.py` confirms no Qt in the core (SS-01).
3. `python -m pytest tests/test_license_audit.py` confirms no AGPL dependency (SS-14).
4. `python -m pytest tests/test_preview_fidelity.py` confirms preview and export agree
   within tolerance — the single-transform invariant (SS-10).
5. `python -m pytest tests/test_golden_pinebox.py` reproduces the Pinebox regression with
   both original defects fixed (SS-14).
6. `python -m deckle.cli export <source> -o out.pdf --gutter 0.75in` produces a correct PDF
   headlessly (SS-07).
7. `python -m deckle` launches, every view is reachable, and the end-to-end integration
   test passes (SS-14).
8. **Manual, requires hardware:** calibrate the real printer via SS-13, print a multi-sheet
   document double-sided, and confirm the stack collates correctly when 3-hole punched with
   no manual reordering.
