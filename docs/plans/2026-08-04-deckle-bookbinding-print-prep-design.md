---
date: 2026-08-04
topic: "Deckle — desktop app for PDF binding-margin prep, manual-duplex printing, and (later) bookbinding signatures"
author: Caleb Bennett
status: evaluated
evaluated_date: 2026-08-04
repo: https://github.com/Caleb68864/Deckle
license: MIT
tags:
  - design
  - deckle
  - pdf
  - bookbinding
  - printing
---

# Deckle — Design

## Summary

Deckle is a standalone, offline desktop application (Windows + Linux) that prepares
documents for hand binding and then **prints them correctly on a printer with no
duplexer**. It takes PDFs and directories of images, lets you reorder pages, applies an
alternating binding gutter so odd and even pages shift away from the spine, previews the
exact physical sheets, and drives the printer directly through a calibrated manual-duplex
workflow — eliminating the by-hand page reordering that PDF viewers force on you today.

It supersedes the existing `AddMargin2PDF` script, fixing several real bugs in it, and is
architected so that traditional bookbinding signature imposition drops in later without a
rewrite.

## Approach Selected

**Approach A — Pure Python desktop (PySide6 + pypdf + pypdfium2).**

Chosen because the project's real risk lives in cross-platform printing, and Qt's
`QPrinter`/`QPrinterInfo` abstracts the Windows print spooler *and* CUPS behind a single
API — printer enumeration, duplex capability detection, page ranges, imageable-area
querying, and device-DPI painting all work on both targets. (Tray/`PaperSource` selection
is Windows-only; the MVP does not need it.) One language, one toolchain, one build
artifact, and PySide6's LGPLv3 licensing is compatible with shipping free and open source
software.

> **Evaluation note.** Design Gaps 1 and 2 revealed that Approach A's print-module
> advantage is narrower than originally claimed — hardware margins and page composition
> are both work Deckle must do itself. This does **not** overturn the choice: A still wins
> decisively on single-toolchain packaging and install simplicity, which were the stated
> priorities. But the margin over Approach B is smaller than the original comparison
> implied, and that is worth knowing rather than discovering.

## Prior Art Research

The user's combination of features does not exist in any single tool. Three of four
features exist separately.

| Tool | Covers | Gaps |
|---|---|---|
| [Bookbinder JS](https://github.com/momijizukamori/bookbinder-js) (MPL-2.0, JS) | Signature imposition **with real signature splitting**, gutter shift, **manual duplex via `_side1`/`_side2` per signature**, sewing station marks, spine order marks, preview | Browser-only; **no print control**; no image→PDF |
| [HornPenguin Booklet](https://github.com/HornPenguin/Booklet) (BSD-3, Python) | Signatures 4–32, imposition layouts, trim/CMYK/proof marks, GUI | Signature-focused only; no gutter-shift mode; no print pipeline; no image import |
| [bookbinding-imposition](https://github.com/teastainedhouse/bookbinding-imposition) (MIT, Python) | Hobbyist signature imposition, flip-on-short-edge duplex | Narrow scope |
| [Stirling PDF](https://docs.stirlingpdf.com/Functionality/Page-Operations/) | Booklet imposition, reorder, images→PDF, split/merge | Self-hosted web app; [gutter/creep correction is an open feature request, not implemented](https://github.com/Stirling-Tools/Stirling-PDF/issues/705); no printer control |
| [PDF Arranger](https://en.wikipedia.org/wiki/PDF_Arranger) (GPLv3, Python/GTK) | Drag-drop reorder, image import, crop, booklet gen | No gutter shift; no print pipeline; no print preview |
| [Bundsteg](https://www.elstel.org/bundsteg/index.html.en) | Alternating odd/even gutter | **Disqualified on verification** — it *scales* content rather than shifting it, so type size changes. CLI only, no GUI/preview/print |
| [pdfimpose](https://framagit.org/spalax/pdfimpose) (AGPL-3.0, Python) | Seven imposition schemas; verified never to rescale content | **AGPL, and depends on AGPL PyMuPDF — cannot be embedded.** Its `--creep` is broken per the author's own help text; its margins are printer/scissors margins, not a binding gutter |
| [cpdf](https://www.coherentpdf.com/) (AGPL-3.0 or $599+/seat) | `-shift` is a verified pure translation (`1 0 0 1 tx ty cm`) — the correct gutter primitive | **AGPL or paid — cannot be embedded.** Excellent to run personally; pikepdf does the same shift in ~15 lines under MPL-2.0 |
| [Duplex Print Helper](https://apps.apple.com/us/app/duplex-print-helper/id6765874707) / [manual-duplex scripts](https://github.com/nclarius/automatic-manual-duplex-printing) | Odd/even split, top-feed vs bottom-feed handling | macOS app / Linux shell scripts; standalone, not integrated with imposition |

### Correction (2026-08-04) — the differentiator is print control, not manual duplex

An earlier revision of this document claimed manual-duplex reordering was the underserved
capability, and that Bookbinder JS lacked it. **Both were wrong**, per the sixteen-tool
synthesis at `Caleb's Vault/Software/Bookbinding Toolchain Comparison.md`.

**Manual duplex is over-solved — five independent solutions exist:** the printer's own
vendor driver (Brother and HP ship a Manual Duplex mode that already encodes their paper
path), free Acrobat Reader, Bookbinder JS's per-signature side files, PDF Arranger's Paste
As Odd/Even, and Stirling's `duplexPass`. That requirement comes off the differentiator list.

**The actual gap: not one of the nine surveyed tools can control a printer.** Bookbinder JS
cannot (browser sandbox), Stirling cannot (server), PDF Arranger has two controls and an
open orientation bug. `QtPrintSupport` — `QPrinter.setDuplex()`, `QPageLayout` margins,
`QPrinterInfo` capability queries, `QPrintPreviewDialog` — fills exactly that hole, and no
other Python toolkit offers real printer control. **That is what justifies building Deckle**:
not re-implementing Bookbinder JS, but Bookbinder JS *plus the thing none of them can do*,
in one application, with the preview rendered from the artifact that actually goes to paper.

Two related corrections: **only Bookbinder JS and pdfimpose do real signature splitting** —
Stirling and PDF Arranger impose the whole document as a single signature, so a 300-page
book becomes one 75-sheet fold, which is physically impossible. And **creep is far less
important than this document originally framed it**: absent everywhere, broken where
claimed, but at 4–6 sheets per signature it is sub-millimetre. Keep signatures thin and it
evaporates. The real disqualifier is tools that **rescale instead of shift** — which is what
killed Bundsteg.

**Practical recommendation for right now, before Deckle exists:** use Bookbinder JS for an
actual book — it is the only tool doing imposition, gutter, and single-sided output
correctly in one pass, and its sewing station marks eliminate the pricking jig. Pair it with
the printer's own manual-duplex driver mode. Calibrate the flip once with a numbered test
page and tape the answer to the printer.

### Naming

`BookBinder` collided with two established projects in the same domain (Bookbinder JS and
its Java predecessor). Renamed to **Deckle** — the rough untrimmed edge of handmade paper.
Namespace verified clean; repo claimed at `github.com/Caleb68864/Deckle`.

## Architecture

The organizing idea is a **four-level page model**. Every existing tool conflates these,
which is exactly why none of them can reprint a single jammed sheet or reason about feed
direction.

| Level | What it is |
|---|---|
| **Source pages** | Ordered imported content — PDF pages and images, interleaved and reorderable |
| **Output pages** | What lands on one *side* of paper after scaling + gutter shift |
| **Sheets** | Physical paper. Each has a front side and a back side |
| **Print passes** | The order sheets are *fed*, per pass, for manual duplex |

Once `Sheet` is a first-class object, "reprint sheet 7" becomes expressible and feed
direction becomes a property of a saved printer profile rather than something rederived by
hand every print.

```
┌──────────────────────────────────────────────────────────┐
│  deckle.app          (PySide6 — the ONLY Qt-aware code)  │
│  Import ▸ Arrange ▸ Layout ▸ Preview ▸ Print             │
└──────────────────────┬───────────────────────────────────┘
                       │ one-way: app calls core, never inverse
┌──────────────────────▼───────────────────────────────────┐
│  deckle.core         (pure Python, no Qt, unit-testable) │
│                                                           │
│   Project ──▶ Imposer ──▶ SheetPlan                       │
│      │        (layout)        │                           │
│      │                        ├──▶ Exporter ──▶ .pdf      │
│      │                        │    (pypdf)                │
│      │                        └──▶ PassPlanner            │
│      │                             │  + PrinterProfile    │
│      │                             └──▶ PrintPass[]       │
│      │                                                    │
│      └──▶ Rasterizer ──▶ preview bitmaps (pypdfium2)      │
└──────────────────────┬───────────────────────────────────┘
                       │ PrintBackend protocol (abstract)
┌──────────────────────▼───────────────────────────────────┐
│  QtPrintBackend  ──▶  Win32 spooler  |  CUPS             │
└──────────────────────────────────────────────────────────┘
```

**Why the core/app split is load-bearing, not ceremony:** imposition math and pass planning
are pure functions over data — the code that must be right and is miserable to verify
through a GUI. A Qt-free core makes them testable with plain pytest, and preserves an
escape hatch if Qt's UI ever becomes intolerable (a web-UI shell could reuse the entire
core untouched).

**Feed-direction calibration.** `PassPlanner` deliberately does *not* try to know your
printer. Deckle ships a **calibration wizard**: it prints one marked test sheet, asks two
or three illustrated questions about how the marks landed after you reloaded the stack,
and derives the flip/reverse rule. That becomes a saved `PrinterProfile`. This is the
piece that actually solves "paper feed direction confusion," and no existing tool attempts
it.

## Components

### `deckle.core` — pure Python, no Qt

**`Project`** — the document model and single source of truth.
*Owns:* the ordered `SourcePage` list, per-page overrides (rotation, skip, force-recto),
global layout settings, save/load of the `.deckle` project file.
*Does not own:* format parsing, imposition math, rendering. It holds intent, not results.

**`SourceLoader`** — ingestion.
*Owns:* format detection, natural-sort of image filenames (`img2` before `img10`), EXIF
orientation correction, DPI inference for images, lazy file handle management for large
PDFs, **and normalization of images into PDF pages via `img2pdf` at ingestion time**.
*Does not own:* ordering decisions — proposes an initial order; `Project` owns it after.

> **Where `img2pdf` runs (resolved in evaluation).** Images are converted to PDF pages
> **at ingestion**, not at export. This means `Imposer`, `Rasterizer`, and `Exporter` only
> ever see one input type — PDF pages — collapsing what would otherwise be two parallel
> code paths through the entire pipeline. `img2pdf` handles DPI and EXIF correctly and
> embeds losslessly, so nothing is given up by normalizing early.

**`Imposer`** — the layout engine, behind a `LayoutStrategy` interface.
*Owns:* scale-to-fit math, gutter shift by page parity, rotation, filler-page insertion to
reach an even count. Emits a `SheetPlan`.
*Does not own:* writing PDFs, printing, or any knowledge of signatures. `GutterShift` is
the only strategy in MVP; `Signature` and `SaddleStitch` implement the same interface in
v2. **This seam is what keeps the bookbinding roadmap from becoming a rewrite.**

**`SheetPlan` / `Sheet`** — the imposition result and the app's central noun.
*Owns:* the `Sheet[]` list, each carrying front-side and back-side `OutputPage`, plus
provenance back to originating source pages.
*Does not own:* any I/O. Pure data — which makes "reprint sheet 7" and "which source page
is this?" both trivial queries.

**`Rasterizer`** — preview and print rendering via pypdfium2.
*Owns:* bitmap generation at requested DPI, bounded LRU thumbnail cache, cancellation of
in-flight renders when scrubbing the preview, and **ink bounding-box computation with
per-page caching**.
*Does not own:* Qt image types. Returns raw RGBA buffers; the app wraps them in `QImage`.
This is what keeps the core Qt-free.

> **Thumbnails are virtualized (resolved in evaluation).** "Import is metadata-only" and
> "the arrange view shows thumbnails" are in tension at 300 pages. Resolution: thumbnail
> generation is **scroll-driven and virtualized** — only pages within (and just beyond) the
> viewport are rendered, at low DPI, into a bounded LRU cache. Import still touches no
> pixels.
>
> **Ink bbox is not free.** Neither pypdfium2 nor pypdf exposes an ink bounding box; it
> requires rasterizing at low DPI and scanning for non-background pixels. Running that
> across 300 pages eagerly would stall the UI. Resolution: compute **on demand for the
> visible sheet**, plus **one full pass during the pre-export check**, cached by page and
> invalidated only when the source changes.

**`Exporter`** — output writing.
*Owns:* producing the final PDF, whole document or a pass/sheet subset.
*Does not own:* layout decisions — renders a `SheetPlan` faithfully, makes none of its own.

> **Composition mechanism (resolved in evaluation).** Page-onto-page placement uses
> **`pypdf`'s `merge_transformed_page`**, which implements exactly this operation.
> `pikepdf` is a QPDF binding built for object-level manipulation and has **no
> high-level placement API** — using it here would mean hand-rolling Form XObject
> plumbing. `pikepdf` is retained only for structural work and damaged-file recovery.
> Both are permissively licensed, so this costs nothing.

**`PassPlanner`** — the manual-duplex brain.
*Owns:* `SheetPlan` + `PrinterProfile` → ordered `PrintPass[]`, each with its sheet
sequence *and* the human-readable reload instruction ("remove the stack, flip along the
short edge, reinsert printed-side-down").
*Does not own:* submission, or any built-in assumption about printer behavior.

**`PrinterProfile`** — the calibration result, persisted per printer name.
*Owns:* flip axis, feed order (top-feed vs bottom-feed), face-up/face-down output,
reverse-stack flag, **imageable area (the printer's physical non-printable margin)**, and
calibration provenance.
*Does not own:* the wizard UI that produces it.

> **Imageable area (added in evaluation).** Every printer has a physical non-printable
> border, typically 0.16–0.25". Content placed inside it is lost, and **no amount of
> transform control prevents this** — it is a property of the hardware. The profile
> captures it from `QPrinter.pageLayout().paintRectPixels()` and confirms it during
> calibration (the test sheet's corner glyphs reveal the true printable bounds). The
> preview renders it as a guide, and the clipping detector checks against it rather than
> against the page box. Without this, the app's central fidelity promise silently fails on
> real hardware while the preview reassures you it won't.

**`PrintBackend`** — abstract protocol: `submit(sheets, printer, copies, ranges)`.
*Owns:* nothing. Exists so the core can plan a print job without importing Qt, and so a CLI
or future non-Qt shell can reuse everything above.

### `deckle.app` — PySide6, the only Qt-aware code

**`QtPrintBackend`** — implements the protocol. Deckle rasterizes each output page at the
printer's own DPI via pypdfium2 and paints it to a `QPainter` at an exact device-space
rectangle, with `setFullPage(true)` so Qt applies no margin of its own.
**No PDF is ever handed to a viewer or driver that could apply "fit to page."**
Deckle owns the transform end to end.

> **The honest limit (added in evaluation).** Transform control guarantees fidelity
> *within the printer's imageable area* — it cannot defeat the hardware's physical
> non-printable border. The promise is therefore: **what you preview inside the imageable
> guide is what hits paper**, and anything outside it is flagged rather than silently
> promised. This is a real constraint, not a hedge, and modeling it is what separates
> Deckle from tools that let you discover it after printing.
>
> **Platform parity caveat.** `QPrinter`'s `PaperSource` (tray selection) is effectively
> Windows-only. Printer enumeration, duplex capability querying, page ranges, and
> device-DPI painting are cross-platform. Manual duplex does not require tray selection,
> so this does not affect the MVP — but the design must not come to depend on it.

**UI views** — `ImportView`, `ArrangeView` (thumbnail grid with drag reorder),
`LayoutPanel` (gutter width, paper size, scaling mode, binding edge), `PreviewView`
(sheet-by-sheet, front/back toggle), `PrintDialog`, `CalibrationWizard`.
*Own:* presentation and user-intent capture only. Every view mutates `Project` and re-reads
derived state; none compute layout.

## Data Flow

```
 IMPORT          ARRANGE           LAYOUT          IMPOSE
┌────────┐     ┌─────────┐     ┌──────────┐    ┌─────────┐
│ PDF    │     │ reorder │     │ paper    │    │         │
│ images ├────▶│ rotate  ├────▶│ gutter   ├───▶│ Imposer │
│ folder │     │ skip    │     │ scale    │    │         │
└────────┘     │ insert  │     │ bind edge│    └────┬────┘
 SourceLoader  └─────────┘     └──────────┘         │
                    │                                ▼
                    ▼                          ┌───────────┐
              Project.pages                    │ SheetPlan │
              (intent, not pixels)             │  Sheet[]  │
                                               └─────┬─────┘
                    ┌────────────────────────────────┼────────────────┐
                    ▼                                ▼                ▼
             ┌────────────┐                   ┌──────────┐    ┌──────────────┐
             │ Rasterizer │                   │ Exporter │    │ PassPlanner  │
             │  preview   │                   │  → .pdf  │    │ + Profile    │
             └────────────┘                   └──────────┘    └──────┬───────┘
                                                                     ▼
                                                              ┌──────────────┐
                                                              │ PrintPass[]  │
                                                              │ pass1 ▸ flip │
                                                              │ pass2        │
                                                              └──────┬───────┘
                                                                     ▼
                                                              QtPrintBackend
                                                              → spooler/CUPS
```

### The one property that makes this design work

`Imposer` computes a **placement transform** per output page — scale, translate, rotate —
and that single transform has **three consumers**: the preview rasterizer, the PDF
exporter, and the print backend. None recompute it; none may adjust it.

That is the guarantee that preview matches exported PDF matches printed paper. It is also
why "the PDF viewer rescaled my careful margins" becomes structurally impossible rather
than merely discouraged.

### Key transformation points

**Ingestion.** Images become `SourcePage` records carrying path, natural-sort index, EXIF
rotation, and inferred physical size from DPI metadata (defaulting to fit-to-page when
absent). PDF pages become records carrying a file reference and page index. **Nothing is
rasterized or copied at import** — import is metadata-only, so a 300-page PDF opens fast.

**Imposition recomputes eagerly; rendering does not.** Changing gutter width re-runs
`Imposer` over the whole document — arithmetic on a few hundred numbers, effectively
instant. Only the visible sheet is rasterized, on demand, cached by (sheet, side, DPI).
This split is what keeps the layout panel feeling live.

**Persistence.** The `.deckle` project file is JSON holding *references* — source paths
plus content hashes — never embedded page data. Small files, and reopening a project after
re-exporting the source PDF surfaces a clear "source changed" warning instead of silently
using stale content.

### Calibration flow

```
CalibrationWizard
   ├─▶ synthesizes a 4-sheet test plan with corner glyphs + sheet numbers
   ├─▶ prints pass 1  →  you reload the stack however you normally would
   ├─▶ prints pass 2
   ├─▶ asks 2–3 illustrated questions about how the marks landed
   └─▶ derives { flip axis, feed order, face direction, reverse flag }
            └─▶ PrinterProfile, saved under the printer's name
```

Runs once per printer, never again. Afterward every reload instruction is derived, not
guessed.

### Reprint flow

Select sheets in the preview → that subset becomes a filtered `SheetPlan` → it re-enters
`PassPlanner` through the identical path. Reprinting sheet 7 is not a special case in the
code; it is the normal path with a smaller input. That is the payoff for making `Sheet` a
first-class object.

## Error Handling

### The three failure modes that actually cost paper

**1. Content clipped by the gutter shift — or by the printer itself.** Shifting a page
sideways can push content off the opposite edge. The existing script cannot detect this —
it fits to page *height* and lets width fall where it may. Deckle computes each page's ink
bounding box and compares it against **the printer's imageable area**, not merely the page
box. Affected pages get a warning badge in the thumbnail grid and appear in a pre-export
summary: *"4 pages will lose content at the fore-edge."* **A warning, not a block** —
sometimes clipping whitespace is intended.

> **Two distinct clipping causes, and only one is Deckle's fault.** Content can fall
> outside the *page* (Deckle's transform pushed it there — fixable by reducing the gutter
> or changing scale mode) or outside the *imageable area* (the printer physically cannot
> mark there — fixable only by reducing content size or accepting the loss). The warning
> must **distinguish these**, because the remedies differ and one of them is not a bug.
> The preview draws the imageable-area boundary as a guide so the constraint is visible
> before you print, not after.

**2. Pass 2 comes out wrong.** The expensive mistake: reload, print 60 backs upside down,
discard 60 sheets. Mitigation is a **"test one sheet first"** option on every pass — print
only the pass's first sheet, verify, then commit the remainder or abort and recalibrate.

**3. Mixed page sizes and orientations.** The existing script computes scale per page but
content width from page 0 only, so any page with a different aspect ratio gets the wrong
gutter. Deckle computes the transform per page from that page's own box. Landscape pages in
a portrait document get an explicit policy — *rotate to fit*, *scale to fit*, or *letterbox*
— set globally with per-page override, and flagged at import.

### Failure handling by component

| Failure | Response |
|---|---|
| Encrypted / password PDF | Prompt for password once, retry; if owner-password-only, proceed and note the restriction |
| Corrupt or malformed PDF | Attempt pikepdf recovery pass; if it fails, name the offending page and import the rest |
| Source file moved or changed since save | Hash mismatch → warning naming the file, with *relocate* and *accept new version* actions. Never silently substitute |
| Printer offline / vanishes mid-job | Print session persists state to disk at **sheet granularity**, and jobs are submitted in bounded chunks rather than one large spool. Software cannot know how many sheets physically emerged before a jam, so resume **asks**: *"How many sheets came out?"* — then continues from there. Pass-level resume alone is not enough |
| Driver misreports duplex capability | The saved `PrinterProfile` always wins over driver-reported capabilities. Drivers are frequently wrong; the calibration sheet is ground truth |
| Very large document (500+ pages) | Metadata-only import, bounded thumbnail cache, rendering off the UI thread with cancellation |
| Images with missing or inconsistent DPI | Fall back to fit-to-page; flag the mixed-DPI condition at import rather than producing silently inconsistent sizes |
| Disk full / unwritable output path | Fail before starting the write, not halfway through a 200 MB export |
| Crash | Project autosaves on every mutation. References-only format makes continuous autosave cheap |

### How errors surface

- **Blocking** — cannot proceed (unreadable source, unwritable destination). Modal, naming
  the specific file.
- **Warning** — proceeds, but marked. Badge on the affected *sheet* in the preview, plus a
  pre-print aggregate summary. Clipping, mixed orientation, mixed DPI live here.
- **Info** — session log panel. Filler pages inserted, pages auto-rotated, defaults applied.

**Governing rule: warnings attach to the sheet they affect**, not to a global message box.
A problem on sheet 12 should be visible when looking at sheet 12.

### Parity and filler pages

Made explicit rather than emergent. The existing script inserts a filler at index 1 *and*
potentially another at the end, driven by a counter that keeps incrementing after the loop
— double-padding some documents. In Deckle, parity is a stated property of the `SheetPlan`:
you choose *start on recto*, *pad to even*, and *where fillers go*; fillers are visibly
marked in the preview as inserted rather than source content; and the plan is verified
internally consistent before any pass is planned.

## Licensing and Code Reuse

**Deckle is MIT licensed.** This permits vendoring from permissively-licensed prior art and
keeps the project maximally reusable.

### Approved reuse

| Source | License | Use |
|---|---|---|
| **HornPenguin/Booklet** | BSD-3-Clause | **Vendor the signature imposition math** (tables, fold/nest logic) into `deckle.core.layout.strategies.signature` for v2, with copyright notice preserved. **Do not take its I/O layer** — it uses abandoned PyPDF2 and pdf2image, which requires an external Poppler binary that would break the single-file install requirement |
| **teastainedhouse/bookbinding-imposition** | MIT | **Study** its duplex flip logic and GUI structure before finalizing our own |
| **bookbinder-js** | MPL-2.0 | **Algorithm reference only** — wrong language, and MPL is file-level copyleft. Read for approach; write our own code |
| **PDF Arranger** | GPLv3 | **Excluded** — viral, incompatible with MIT |

Imposition schemes (folio, quarto, octavo) are centuries-old public knowledge; the
algorithms are not the constrained part, only specific code expression is.

**Verification required:** the licenses above were read from GitHub repository pages, not
from the `LICENSE` files themselves. Confirm directly before vendoring anything.

### Dependencies

| Package | License | Role |
|---|---|---|
| `PySide6` | LGPLv3 | UI, printing (`QPrinter` abstracts Win32 spooler + CUPS) |
| `pikepdf` | MPL-2.0 | **All PDF manipulation, including page composition** via `Page.as_form_xobject()` + `Page.calc_form_xobject_placement()`. Verified locally on 10.11.0 to emit pure translation matrices (`1 0 0 1 396 0 cm`) with zero scale drift — the exact property imposition requires. QPDF underneath gives structural repair of damaged inputs for free |

> **Correction (2026-08-04).** An earlier revision of this document claimed pikepdf "has no
> high-level placement API" and routed composition through `pypdf.merge_transformed_page`.
> That was wrong. pikepdf ships `as_form_xobject` / `calc_form_xobject_placement` and a
> documentation topic titled *"Overlays, underlays, watermarks, n-up"*. `pypdf` is dropped
> entirely. Verified research lives in `Caleb's Vault/Software/pikepdf/` (32 notes, all code
> samples executed against 10.11.0 / libqpdf 12.3.2 on Windows 11).
| `pypdfium2` | BSD-3 / Apache-2.0 | Rasterization for preview and print; self-contained, no external binary |
| `img2pdf` | **LGPL-3.0** | Lossless image→PDF with correct DPI and EXIF handling |
| `natsort` | MIT | Natural filename ordering |
| `Pillow` | MIT-CMU | Image handling |

**No AGPL.** The existing script's `PyMuPDF` dependency is AGPL-3.0 and is deliberately
dropped — it would be viral across the whole application and impose network-copyleft
obligations.

**LGPL note:** `PySide6` and `img2pdf` are LGPLv3. An MIT application depending on
unmodified LGPL libraries is a standard, uncontroversial arrangement, but the *distributed
bundle* carries LGPL relinking obligations. Publishing source plus build instructions
satisfies this; the one-file vs one-folder packaging choice (Open Question 2) determines
how cleanly.

## Success Criteria

**Import & arrange**
1. A PDF and a directory of images import into one project, interleaved; image filenames
   sort naturally (`img2` before `img10`); EXIF rotation honored.
2. Pages reorder by drag, rotate, skip, and accept inserted blanks — all with undo.

**Layout**
3. Paper size (letter, legal, A4, A3, tabloid), gutter width in inches or mm, binding edge,
   and scale mode are configurable, and layout updates live.
4. Pages whose content would be clipped by the gutter are flagged before export.

**Fidelity — the core promise**
5. Preview, exported PDF, and printed sheet are pixel-consistent **within the printer's
   imageable area**, because all three consume the same placement transform. Verifiable by
   rendering the exported PDF and diffing against the preview bitmap. Content falling
   outside the imageable area is **flagged before printing**, distinguishing "Deckle's
   transform pushed it off the page" from "the printer physically cannot mark there."
6. **Regression benchmark:** Deckle reproduces `Pinebox_Middle_School_letter_margins.pdf`
   from its source at least as correctly as the existing script, with the double-padding
   and page-0-aspect-ratio bugs fixed. A golden test, not a vibe check.

**Printing — the reason this exists**
7. A printer can be calibrated once through the wizard; the profile persists across sessions.
8. A full job prints as pass 1 → derived reload instruction → pass 2, and collates correctly
   when 3-hole punched, with **no manual page reordering by hand**.
9. "Test one sheet first" is available on every pass.
10. An arbitrary subset of sheets can be reprinted through the identical path.
11. A pass that fails mid-job is resumable.

**Shipping**
12. **The dependency set contains no external runtime binary** (no Poppler, Ghostscript,
    ImageMagick) and no AGPL component — so single-artifact packaging remains achievable.
    *Packaging itself is deferred; this verifies only that nothing forecloses it.*

    > **Red-team C-6.** The original wording required a shipped installer while packaging
    > was simultaneously out of scope — unsatisfiable by any sub-spec. **Deferred to
    > post-MVP:** "installs on Windows as a single artifact and on Linux as an AppImage or
    > `.deb` with no Python required." Research recommends shipping **`--onedir`, not
    > `--onefile`**, which sidesteps the LGPL relinking question (Open Question 2) rather
    > than arguing it.
13. Zero AGPL dependencies.
14. `deckle.core` imports no Qt; imposition and pass-planning math are covered by unit tests
    that run headless.
15. Repo carries `.gitignore`, `README.md`, and `CHANGELOG.md`.

## Exclusions

Explicitly **not** in the MVP:

- **Signature imposition, saddle stitch, perfect binding, creep correction** — v2. The
  `LayoutStrategy` seam exists for them; nothing else is built.
- **N-up / multiple pages per sheet side** — arrives with signatures in v2.
- **PDF content editing** — no text, annotation, or form manipulation. Deckle is print prep,
  not an editor.
- **OCR or scanner integration.**
- **Cloud, sync, accounts, telemetry, or any network access.** Deckle is fully offline.
- **macOS** — deferred, but `PrintBackend` stays portable so it is not a rewrite.
- **PDF layer (OCG) selection** — the existing script stubbed this out because it did not
  work. Not resurrected in MVP.

## Open Questions

1. **What does "gutter width" mean, exactly?** Either *(a)* you specify the gutter (e.g.
   0.75") and content scales to fit the remainder, or *(b)* content scales to page height
   and the gutter is whatever falls out (current script behavior). (a) is predictable and
   matches how you think about a 3-hole punch; (b) maximizes content size. **Changes:**
   `Imposer` semantics and the layout panel. Likely resolution: both as modes, defaulting
   to (a).

2. **One-file or one-folder packaging?** LGPL's relinking provision is cleanly satisfied by
   one-folder builds and murkier with `--onefile`. **Changes:** distribution artifact shape
   and ongoing LGPL compliance burden.

3. **Which printer(s) will we calibrate against?** The wizard's question set must survive
   contact with real hardware. **Changes:** the `PrinterProfile` field set — specifically
   whether reverse-stack ordering is genuinely independent of flip axis or derivable from it.

   > **Escalated in evaluation: treat calibration as a spike, not a design.** This is the
   > one **Complex-domain** component in an otherwise Complicated plan — no prior art
   > attempts it, so there is no correct answer to analyze toward. The original claim that
   > "2–3 questions" suffices is unsupported: the state space is *flip axis × face
   > direction × feed edge × stack order*, up to 16 combinations, which two or three binary
   > questions cannot resolve. **Before designing the wizard, enumerate the state space and
   > derive the question count from it** — and expect the answer to be four to five
   > questions, or a smarter test sheet whose glyphs disambiguate several axes at once
   > (e.g. asymmetric corner marks that reveal both rotation and face in one look).
   > Build this as a probe with an explicit pivot criterion, not as a specified feature.

4. **Linux packaging target — AppImage, `.deb`, Flatpak, or `pipx`?** AppImage is safest for
   "no Python required"; Flatpak has better printer/portal integration on modern desktops.
   **Changes:** build pipeline only, not the app.

5. **Should the CLI be an MVP deliverable?** The Qt-free core makes a CLI nearly free and
   would let imposition math be regression-tested headless in CI against the Pinebox
   benchmark. **Changes:** small scope addition, meaningful testing gain. Inclination: yes.

6. **Vendor HornPenguin now or at v2?** Signatures are v2, but reading its imposition model
   *now* would let us shape the `LayoutStrategy` interface to fit, instead of discovering in
   v2 that the seam is the wrong shape. **Changes:** nothing shipped in MVP, but potentially
   avoids an interface rewrite.

7. **Preview rendering strategy at scale.** On-demand only, or speculative background
   rendering of nearby sheets? **Changes:** `Rasterizer` caching only — safely deferrable.

## Approaches Considered

**Approach A — Pure Python desktop (PySide6 + pikepdf + pypdfium2). SELECTED.**
Qt handles Win32 spooler and CUPS behind one API, ships built-in PDF preview widgets, and
builds to a single artifact per platform with one toolchain. Selected because three of the
four stated print pains (manual-duplex correctness, page-range reprints, feed-direction
handling) live in the print module, and this is the only approach where that module is
largely a framework feature rather than code to write and debug against real hardware on
two operating systems.

**Approach B — Tauri + Python sidecar (React/Vite/shadcn + PyInstaller'd Python over HTTP).
Rejected.** Best UI of the three, and page reordering/preview scrubbing genuinely suit web
tech. Rejected because printing must never route through the webview (it applies exactly
the "fit to page" rescaling being escaped), so the cross-platform print abstraction has to
be written by hand anyway — forfeiting Approach A's main advantage while adding three CI
toolchains, a WebView2 runtime requirement, and [known sidecar process-lifecycle
issues](https://github.com/tauri-apps/tauri/discussions/2759).

**Approach C — pywebview + Flask + React. Rejected.** Same web-stack benefit without Rust
and with in-process printing calls. Rejected primarily on Linux packaging: pywebview needs
WebKit2GTK and PyGObject as *system* packages, which fights self-contained distribution and
directly undercuts the stated "easiest for end users to install" priority.

**Note on reversibility:** the imposition core is pure Python in all three. Only the shell
differs. If Qt's UI proves intolerable, `deckle.core` ports to Approach B untouched.

## Commander's Intent

**Desired End State**

A user opens Deckle, imports a PDF or a folder of images, sets a gutter width, sees the
exact physical sheets in preview, and prints a double-sided document on a printer with no
duplexer — reloading the paper once, following an on-screen instruction derived from that
printer's own calibration — and the result collates correctly with no manual page
reordering by hand. It installs on Windows and Linux as a single artifact with no Python
required. All fifteen Success Criteria pass, including the Pinebox golden-fixture
regression.

**Purpose**

The user hand-binds books (3-hole punch today; saddle stitch on a Singer 111w101 for short
runs; traditional signatures eventually). Every existing imposition tool produces a PDF and
abandons the user at the print dialog, where viewers rescale carefully-set margins and a
duplexer-less printer forces manual page reordering. Deckle exists to close that last mile.
**When a judgment call isn't covered by this plan, favor whatever makes the printed
physical result correct** — fidelity and print correctness outrank UI polish, feature
breadth, and code elegance.

**Constraints**

- **MUST NOT** introduce any AGPL dependency, or any dependency requiring a commercial
  license. This is a hard gate — it would make the project undistributable as intended.
- **MUST NOT** import Qt anywhere inside `deckle.core`. Verified by an automated test.
- **MUST NOT** make any network request. No telemetry, no update checks, no analytics.
- **MUST NOT** allow any consumer to recompute or adjust the placement transform. `Imposer`
  computes it; `Rasterizer`, `Exporter`, and `QtPrintBackend` consume it verbatim.
- **MUST NOT** require an external binary at runtime (no Poppler, no Ghostscript, no
  ImageMagick). This is what makes the single-artifact install possible.
- **MUST** preserve upstream copyright notices in any vendored file.
- **MUST** model the printer's imageable area wherever content bounds are evaluated.
- **MUST** keep all rendering and printing off the UI thread, with cancellation.
- **MUST** log every submitted print job's full parameters (printer, profile, sheet range,
  DPI, pass index) to a session log. Print failures are the hardest class of bug in this
  app and are not reproducible after the fact without this.
- **MUST** treat warnings as sheet-attached, never as global modal dialogs.

**Freedoms — the implementing agent MAY**

- Choose internal module and file layout within `deckle.core` and `deckle.app`.
- Choose all naming, test organization, and code style.
- Choose the widget composition and layout of each view, provided information architecture
  matches the named views.
- Choose caching sizes, eviction policy, and thumbnail DPI.
- Choose the concrete threading mechanism (`QThreadPool`, `concurrent.futures`, etc.).
- Add small permissively-licensed utility dependencies without approval, provided they
  introduce no external binary and no AGPL.

### Committed interface/contract defaults

Every contract below is **committed**, not open. Use these shapes unless explicitly
overridden.

- **Placement transform** → **Default:** an immutable value object
  `Placement(scale_x: float, scale_y: float, tx: float, ty: float, rotate_deg: int)` in PDF
  points, origin bottom-left. Example: `Placement(0.92, 0.92, 54.0, 0.0, 0)`.
  _(Override only if non-uniform scaling proves unnecessary — collapsing to a single
  `scale` is acceptable.)_

- **`LayoutStrategy` interface** → **Default:**
  `def impose(self, pages: Sequence[SourcePage], settings: LayoutSettings) -> SheetPlan`.
  Stateless; all configuration arrives via `settings`. `GutterShiftStrategy` is the only
  MVP implementation. _(This signature is the v2 seam for signatures/saddle stitch — do not
  narrow it to gutter-specific parameters.)_

- **`Sheet` shape** → **Default:**
  `Sheet(index: int, front: OutputPage | None, back: OutputPage | None)`, where
  `OutputPage(source_ref: SourceRef | None, placement: Placement, is_filler: bool)`.
  A `None` side means an intentionally blank side; `is_filler=True` marks generated padding.

- **`PrintBackend` protocol** → **Default:**
  `def submit(self, plan: SheetPlan, sheets: Sequence[int], printer_name: str, copies: int, dpi: int) -> PrintResult`,
  returning `PrintResult(submitted: int, job_id: str | None, error: str | None)`.

- **`PrinterProfile` shape** → **Default:** a dataclass persisted as JSON keyed by printer
  name: `flip_axis: Literal["long","short"]`, `output_face: Literal["up","down"]`,
  `feed_edge: Literal["top","bottom"]`, `reverse_stack: bool`,
  `imageable_area_pt: tuple[float,float,float,float]`, `calibrated_at: str`,
  `calibration_version: int`.

- **`.deckle` project file** → **Default:** JSON, top-level
  `{"version": 1, "pages": [...], "layout": {...}, "printer": "..."}`. Each page entry
  carries `path`, `page_index`, `sha256`, and any per-page overrides. Version field is
  mandatory from day one so future migrations are possible.

- **`Rasterizer` return type** → **Default:**
  `RenderedPage(width: int, height: int, rgba: bytes)` — raw buffer plus dimensions. No Qt
  types, no PIL types, crossing the core boundary.

- **Undo model** → **Default:** **snapshot-based.** `Project` is small and
  JSON-serializable, so a bounded deque of serialized snapshots (default 50) is simpler and
  more obviously correct than a command pattern, and page reordering is exactly the case
  where command-pattern inverse operations get fiddly. _(Override to a command pattern only
  if snapshot memory measurably becomes a problem, which it should not at these sizes.)_

- **Error-tier representation** → **Agent-free:** any consistent representation of
  blocking / warning / info is acceptable, provided warnings carry the sheet index they
  attach to.

## Execution Guidance

**Observe** — signals to monitor during implementation:

- `pytest` pass/fail after each `deckle.core` component. The core is the part that must be
  right; a red core test is a stop signal, not a note.
- The **core purity test** — an automated assertion that no module under `deckle.core`
  transitively imports Qt. If this goes red, the architecture has been violated.
- The **Pinebox golden-fixture diff**. Rendered output drifting from the approved reference
  means the transform changed; investigate before proceeding.
- The **preview-vs-export diff test**. If the exported PDF stops matching the preview
  bitmap, the single-transform invariant has been broken somewhere.
- Dependency license audit output. A new AGPL transitive dependency is a hard stop.

**Orient** — context to maintain:

- This is a **greenfield project with no existing codebase conventions to match.** Establish
  conventions in the first module written and follow them consistently thereafter.
- The four-level page model (source → output → sheet → pass) is the shared vocabulary. Name
  things after it. Do not introduce a competing vocabulary.
- Every component's "does not own" clause in the Components section is a real boundary, not
  commentary. When tempted to reach across one, that is the signal to reconsider.
- `deckle.core` must be usable headlessly. If a core function needs a widget, it belongs in
  `deckle.app`.

**Escalate when:**

- Any dependency change would introduce AGPL, a commercial license, or an external runtime
  binary.
- Qt's cross-platform print behavior diverges from what this plan assumes (ASM-1) — that
  invalidates part of the approach rationale and is a human decision, not a workaround.
- The calibration state-space enumeration shows the wizard needs materially more than five
  questions, or that the axes are not independently determinable. That changes the UX.
- Open Question 1 (gutter semantics) is reached without a resolution — it blocks `Imposer`
  and cannot be guessed.
- Vendoring any third-party source, for licensing verification.
- Any acceptance criterion appears unachievable as written.

**Shortcuts — apply without deliberation:**

- Use `pypdf.PageObject.merge_transformed_page` for all page composition. Do not
  hand-roll Form XObjects, and do not use pikepdf for placement.
- Use `img2pdf` at ingestion for every image. Never build an image code path through the
  rest of the pipeline.
- Use `natsort.natsorted` for every filename ordering operation.
- Return raw RGBA buffers from `deckle.core`; wrap in `QImage` only inside `deckle.app`.
- Set `QPrinter.setFullPage(True)` on every print job, and take margins from the profile's
  imageable area rather than from Qt's defaults.
- Every persisted format (`.deckle`, `PrinterProfile`) carries a version integer from its
  first commit.
- Pure-function core components take data in and return data out — no I/O inside `Imposer`
  or `PassPlanner`.

## Decision Authority

**Agent decides autonomously:**

- Module/file layout, naming, code style, test organization
- Widget composition and layout within each named view
- Cache sizes, eviction policy, thumbnail DPI, threading mechanism
- Internal implementation of any single component
- Error message wording (except calibration wizard copy — see below)
- Snapshot depth for undo

**Agent recommends, human approves:**

- Any new dependency not listed in the Dependencies table
- The `.deckle` schema shape (it is a persisted format; future compatibility is at stake)
- The `PrinterProfile` field set, once the calibration state space is enumerated
- Any deviation from the four-level page model or the stated component boundaries
- Performance trade-offs that change user-visible behavior (e.g. lowering preview fidelity
  to gain speed)

**Human decides:**

- Open Question 1 — gutter semantics. Blocks `Imposer`; must not be guessed
- Open Question 2 — one-file vs one-folder packaging (LGPL compliance implications)
- Open Question 5 — whether the CLI ships in MVP (scope)
- Whether to vendor HornPenguin, and verification of its license (legal)
- Calibration wizard question wording and illustrations (UX, and the component most likely
  to confuse a user)
- Any scope change to the Success Criteria or Exclusions
- The project's final license declaration

## War-Game Results

**Most Likely Failure:** Printer hardware margins silently consume fore-edge content while
the preview reassures the user it will fit. Compounds with the original ASM-3 assumption
that transform control alone guarantees fidelity.
*Mitigation:* imageable area added to `PrinterProfile`, rendered as a preview guide, and
used by the clipping detector — with the two clipping causes reported distinctly.

**Second Most Likely Failure:** `Exporter` built on a library that cannot perform the
operation the design requires.
*Mitigation:* composition mechanism committed to `pypdf.merge_transformed_page`; pikepdf
demoted to structural work.

**Scale Stress:** A 300-page import triggering eager thumbnail generation and ink-bbox
computation stalls the UI precisely when the user is most attentive.
*Mitigation:* virtualized scroll-driven thumbnails; ink bbox computed on-demand for the
visible sheet plus one pre-export pass, cached per page.

**Dependency Risk:** Qt's print behavior diverges between the Win32 spooler and CUPS more
than assumed (ASM-1), eroding the central rationale for Approach A.
*Mitigation:* spike `QPrinter` on both platforms **before** building the print module.
Escalate rather than work around. Secondary risk: HornPenguin's imposition math may be
coupled to PyPDF2 objects (ASM-6) — a v2 concern that would invalidate a claimed time
saving, not the MVP.

**Maintenance Assessment (6-month):** **Strong.** The four-level page model gives the
codebase a consistent vocabulary, and every component carries an explicit "does not own"
clause. A developer who has never seen this code could locate a change correctly from the
design document alone. The main documentation debt to avoid is letting the calibration
wizard's derived rules become undocumented folklore — the state-space enumeration should
live in the repo, not only in someone's head.

## Evaluation Metadata

- Evaluated: 2026-08-04
- Cynefin Domain: **Complicated**, with the calibration wizard as a **Complex** pocket
  requiring experimental treatment
- Assumptions audited: 15 (2 contradicted, 4 unsupported, 4 partial, 5 confirmed)
- Critical Gaps Found: 2 (2 resolved)
- Important Gaps Found: 5 (5 resolved)
- Suggestions: 3 (3 incorporated — observability as a constraint, undo model committed,
  Qt tray-selection claim corrected)

## Next Steps

- [ ] Scaffold the repo: `.gitignore` (Python + PyInstaller + Qt), `README.md`,
      `CHANGELOG.md` (Keep a Changelog format), `LICENSE` (MIT)
- [ ] Rename the local working directory from `BookBinder` to `Deckle`, clone from
      `github.com/Caleb68864/Deckle`
- [ ] Resolve Open Question 1 (gutter semantics) — blocks `Imposer` implementation
- [ ] **Spike `QPrinter` on Windows and Linux before building the print module** — verify
      imageable-area querying, duplex capability reporting, and device-DPI painting behave
      as assumed (ASM-1). This validates the core rationale for Approach A
- [ ] **Enumerate the calibration state space** (flip axis × face × feed edge × stack
      order) and derive the wizard's question count from it — do not assume 2–3
- [ ] Verify HornPenguin and bookbinding-imposition `LICENSE` files directly before vendoring
- [ ] Capture a `PrinterProfile` for the actual target printer to validate the calibration
      wizard's question set (Open Question 3)
- [ ] Turn this design into a Forge spec (`/forge <this file>`)
- [ ] Preserve `Pinebox_Middle_School.pdf` and its old output as the golden regression
      fixture before the old script's directory changes
