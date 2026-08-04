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

## 2. The four-level page vocabulary

Every module in this codebase uses the same four terms, in the same
order, for the same things. Get the vocabulary right and the rest of the
code reads itself:

1. **Source page** (`SourcePage`) -- a page as it exists in the input
   (a PDF page or an imported image, normalized to a PDF page), before any
   placement decision. Carries a `SourceRef` (path, page index, content
   hash, natural size) plus a user-applied rotation and skip flag.
2. **Output page** (`OutputPage`) -- a source page (or a blank filler)
   assigned a `Placement` on a sheet: the exact affine transform
   (`scale_x`, `scale_y`, `tx`, `ty`, `rotate_deg`) that positions its
   content. `Placement` is computed exactly once, by `Imposer`, and every
   downstream consumer (rasterizer, exporter, print backend) reproduces it
   verbatim -- nothing downstream ever recomputes or adjusts a
   `Placement`.
3. **Sheet** (`Sheet`) -- one physical piece of paper: an optional front
   `OutputPage` and an optional back `OutputPage`.
4. **Pass** (`PrintPass`) -- one physical pass through the printer: an
   ordered set of sheets, a side (front/back), and (for manual duplex) a
   plain-language reload instruction. `PassPlanner` turns a `SheetPlan`
   plus a `PrinterProfile` into passes; nothing recomputes sheet order or
   flips a stack outside that module.

Source page → output page → sheet → pass. Name new code, variables, and
tests using this vocabulary rather than locally-nicer synonyms
("page", "slot", "job") -- consistency here is worth more than a
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
