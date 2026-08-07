# Contributing to Deckle

Three rules trip up newcomers more than anything else in this codebase.
Read this before your first change.

## 1. Core purity: `deckle.core` never imports Qt

`deckle/core/` is the pure engine -- data models, imposition math, PDF
composition, rendering, printer profiles, the pass planner, the session
log, and persistence. **Nothing under `deckle/core/` may import PySide6,
PyQt5, PyQt6, or any other Qt binding**, directly or transitively.

This is enforced automatically: `tests/test_core_purity.py` walks every
module under `deckle/core/`, imports it, and asserts that doing so never
pulls a Qt module into `sys.modules`. If your change to a core module
fails that test, the fix is to move the Qt-touching code into
`deckle/app/` (the desktop shell) and have it call into `deckle.core`,
never the other way around.

Why it matters: `deckle/cli.py` is a real, shipped headless entry point,
not a test harness, and the golden-fixture regression
(`tests/test_golden_pinebox.py`) and CI both run it without a display
server. A Qt import anywhere in `deckle.core` breaks that.

## 2. The five-noun page model

Every module in this codebase uses the same terms, in the same order,
for the same things. Get the vocabulary right and the rest of the code
reads itself:

1. **Source page** (`SourcePage`) -- a page as it exists in the input
   (a PDF page or an imported image, normalized to a PDF page), before any
   placement decision. Carries a `SourceRef` (path, page index, content
   hash, natural size) plus a user-applied rotation and skip flag.
2. **Output page** (`OutputPage`) -- a source page (or a blank filler)
   assigned a `Placement` on a sheet: the exact affine transform
   (`scale_x`, `scale_y`, `tx`, `ty`, `rotate_deg`) that positions its
   content. `Placement` is computed exactly once, by the imposition
   strategy (`GutterShiftStrategy` or `SaddleStitchStrategy`), and every
   downstream consumer (rasterizer, exporter, print backend) reproduces it
   verbatim -- nothing downstream ever recomputes or adjusts a
   `Placement`.
3. **Side** (`Side`) -- one printable face of a sheet: one or more
   `OutputPage`s plus any bindery `Mark`s (sewing stations, signature
   order bars, fold lines) drawn on that face. Under the MVP
   `GutterShiftStrategy`, `Sheet.front`/`Sheet.back` may still carry a bare
   `OutputPage` directly (one page per side); `SaddleStitchStrategy`
   always produces a `Side` (two folio cells per face).
4. **Sheet** (`Sheet`) -- one physical piece of paper: an optional front
   face and an optional back face.
5. **Signature** (`Signature`) -- a group of sheets folded and nested
   together as one saddle-stitch gathering (`sheet_indices`, plus how many
   of its slots are blank filler). Only `SaddleStitchStrategy` populates
   `SheetPlan.signatures`; `GutterShiftStrategy` leaves it empty.
6. **Pass** (`PrintPass`) -- one physical pass through the printer: an
   ordered set of sheets, a side (front/back), and (for manual duplex) a
   plain-language reload instruction. `plan_passes` turns a `SheetPlan`
   plus a `PrinterProfile` into passes; nothing recomputes sheet order or
   flips a stack outside that module.

Source → output → side → sheet → signature → pass. Name new code,
variables, and tests using this vocabulary rather than locally-nicer
synonyms ("page", "slot", "job") -- consistency here is worth more than a
marginally shorter identifier.

## 3. No external runtime binaries

Deckle must never shell out to, bundle, or require the user to install a
separate PDF/imaging binary -- no Poppler, no Ghostscript, no
ImageMagick. Every PDF operation goes through `pikepdf` (composition,
page metadata) and `pypdfium2` (rasterization); every image import goes
through `img2pdf` and Pillow. This keeps the app installable with nothing
beyond its own Python dependencies, and keeps the licensing story
enforceable (see below).

Related, and just as hard a constraint: **no AGPL dependency, and no
dependency requiring a commercial license.** `pikepdf`/QPDF and
`pypdfium2`/PDFium are both permissively licensed; `PyMuPDF` (imported as
`fitz`) is AGPL and must never be added, even transitively. So are
`pdfimpose` and `cpdf` -- both are excellent tools to run personally, and
both are explicitly out of scope for this project for that reason. This
is enforced by `tests/test_license_audit.py`, which enumerates the
licenses of Deckle's own dependency closure and fails on any AGPL entry
or on `PyMuPDF`/`fitz` being present, plus a grep guard for
`pdfimpose`/`cpdf` references anywhere in `deckle/` or `tests/`.

## 4. The pass-planning seam, and `paper_thickness_pt` is advisory only

`deckle/core/printing.py` (`plan_passes`) and `deckle/core/profiles.py`
(`PrinterProfile`) are the pass-planning seam. `plan_passes` accepts an
arbitrary `sheets` subsequence, so printing a single signature's sheets
(`plan_passes(plan, profile, sheets=plan.signatures[i].sheet_indices)`) is
the existing reprint/subset path rather than a new one.

**Both files are ordinary code now.** They were pinned by SHA-256 in
`tests/test_seam_zero_diff.py` while the v2 signature work was in
progress, to make that sub-spec's "these two files do not change" claim
falsifiable rather than aspirational. That sub-spec is finished, and the
pin was regenerated three times in a single day for changes that were all
deliberate and all outside the seam it guarded -- it had started catching
ordinary maintenance instead of the violation it was written for. It was
retired on 2026-08-06.

What replaced it is better: both modules are at **100% statement
coverage** from behavioural tests (`tests/test_printing.py`,
`tests/test_registration.py`). A hash tells you a file changed; those
tell you whether it still does the right thing, which is the property
anyone actually cared about.

`LayoutSettings.paper_thickness_pt` is **advisory only**. The only place
permitted to read it is `deckle/core/layout.py`'s `_creep_advisory`
helper, which turns it into a `LayoutWarning` (predicted fore-edge creep,
plus a suggested remedy) and nothing else -- no `Placement` this codebase
emits may differ because of this value. `tests/test_layout_saddle.py`
enforces this with an AST check: `paper_thickness_pt` may not be
referenced anywhere else in `deckle/core/layout.py`.

## Other useful facts

- `deckle/cli.py` is the headless entry point (`python -m deckle.cli`);
  `deckle/__main__.py` is the GUI entry point (`python -m deckle`). Both
  sit on top of the same `deckle.core` engine.
- Run the full suite with `python -m pytest -q` before opening a PR.
- `tests/test_integration.py` asserts every module under
  `deckle/app/views/` is imported by `deckle/app/main.py` -- a new view
  that isn't wired into `MainWindow` fails that test on purpose.
- Packaging (one-file vs. one-folder, installers) is an explicitly
  deferred decision for this MVP. Don't add PyInstaller specs, Inno Setup
  scripts, or AppImage recipes.
