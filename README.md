# Deckle

Prepare PDFs and image folders for hand bookbinding, and print them on a
printer that has no duplexer.

Deckle adds an alternating binding gutter, shows you the exact physical
sheets before you commit paper, and drives the printer directly — splitting a
double-sided job into two passes with a reload instruction derived from your
printer's own calibrated behaviour.

**Status: not yet released.** The gutter-shift path is the proven one and
is what everything below describes unless marked otherwise. Saddle-stitch
signature imposition exists but is **experimental** — see below. The
calibration wizard does not exist yet. Expect breaking changes.

## Why it exists

Every imposition tool surveyed produces a PDF and then abandons you at the
print dialog. None of them can control a printer — Bookbinder JS is sandboxed
in a browser, Stirling PDF is a server, PDF Arranger has two controls and an
open orientation bug. `QtPrintSupport` fills exactly that gap.

Manual duplex on its own is well served elsewhere; your printer's own driver
is often the best option. What is *not* served is doing the gutter, the
preview and the printing in one place, with the preview showing the artifact
that actually reaches the paper.

## The four-level page model

The vocabulary the whole codebase uses. Most tools conflate these, which is
why they cannot express "reprint sheet 7".

| Level | What it is |
|---|---|
| **Source page** | Imported content — a PDF page or image, before any placement decision |
| **Output page** | A source page (or blank filler) with a placement transform, bound for one side of a sheet |
| **Sheet** | One physical piece of paper, with a front and a back |
| **Print pass** | The order sheets are *fed*, per pass, for manual duplex |

## What it does

- Import PDFs and image folders together; natural filename ordering, EXIF
  orientation, lossless embedding via `img2pdf`
- Reorder, rotate, skip and insert blanks, with undo and continuous autosave
- Four independent margins — gutter (spine), fore-edge, head, tail — entered
  in pt / in / cm / mm
- One document-wide scale, so body text never changes size between pages
- Preview the real exported PDF, with fit-to-window, zoom, and a side-by-side
  front/back spread
- Two guides drawn distinctly: the printer's **imageable area** (a hardware
  limit) and your **content box** (your margins)
- Warnings that distinguish *why* content is clipped — off the page, versus
  inside the printer's non-printable border
- Save the imposed PDF, or print it with manual-duplex pass splitting,
  test-one-sheet, and sheet-granular resume
- A headless CLI: `impose`, `export`, `info`
- **Experimental:** saddle-stitch signature imposition (`--fold-scheme folio`),
  with fold lines, sewing-station marks and per-signature printing

## Experimental: folio (saddle stitch)

`--fold-scheme folio` imposes pages two-up per side and groups sheets into
folded, nested signatures. It is implemented, unit-tested, and proven to
produce two independent placements per side with no scale drift between them.

It is nevertheless marked **experimental**, for one specific reason: the page
*ordering* — which source page lands in which cell of which sheet so that the
stack reads correctly once folded — is hand-written arithmetic, and the only
check that actually proves it is folding a physical dummy and reading it. That
has not been done. Every automated test verifies the ordering is *consistent
and self-inverse*; none of them verifies it is *the right ordering*, because
software cannot tell you which way the paper folds.

So: print folio onto scrap, fold it, and read it before committing a real book.
If it reads correctly, the arithmetic is right and it will stay right.

By contrast the default (`--fold-scheme none`, gutter shift) is verified
placement-identical against the pre-refactor implementation across six setting
combinations and two documents, including a 300-page book with mixed page
sizes.

## Not built yet

- **Calibration wizard** — printing uses built-in printer presets rather than
  a profile measured from your own printer
- **Verified signature imposition** — `--fold-scheme folio` is implemented and
  tested, but see *Experimental: folio* below before trusting it with paper
- Packaging and installers; macOS support

## Running it

```
run.bat                  launch the GUI
run.bat cli <args...>    headless CLI
run.bat test [args]      pytest, with arg passthrough
run.bat deps             install/refresh dependencies
run.bat doctor           interpreter, dependency versions, visible printers
```

From a terminal use `.\run.bat` — cmd does not search the current directory.
Double-clicking from Explorer works as-is.

```
python -m deckle                                    GUI
python -m deckle.cli info book.pdf
python -m deckle.cli export book.pdf -o out.pdf --gutter 0.75in
```

## Architecture

`deckle.core` is pure Python and **imports no Qt** — enforced by an automated
test. Imposition and print planning are pure functions over data, so they are
testable without a display or a printer, and the app layer stays replaceable.

```
deckle/core/    models, loader, layout, export, render, printing,
                profiles, print_session, project_io, session_log
deckle/app/     PySide6 shell, views, Qt print backend
deckle/cli.py   headless entry point
```

The preview **rasterises the exported PDF** rather than re-drawing the layout.
Two implementations of imposition can share a bug and agree with each other; a
rasterised artifact cannot lie about what will print.

## Licensing

MIT. Every dependency is permissive or weak-copyleft, and **no AGPL component
is permitted** — enforced by a license-audit test. That rules out PyMuPDF,
pdfimpose, cpdf, Poppler and Ghostscript, all of which are otherwise
attractive for this problem.

| Package | License | Role |
|---|---|---|
| `pikepdf` | MPL-2.0 | All PDF manipulation and composition |
| `pypdfium2` | BSD-3 / Apache-2.0 | Rasterisation for preview and print |
| `img2pdf` | LGPL-3.0 | Lossless image → PDF at ingestion |
| `PySide6` | LGPLv3 | UI and printing |
| `natsort`, `Pillow` | MIT | Filename ordering, image metadata |

Deckle itself is released under the [MIT License](LICENSE).

## Documentation

- `docs/plans/` — design documents
- `docs/specs/` — the MVP spec and its phase specs
- `docs/decisions.md` — defects found, and what each one generalises to
