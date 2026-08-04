# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing released yet. The MVP is functional; the calibration wizard and
signature imposition are not built.

### Added

- Project scaffold: repository layout, packaging metadata, and the core
  pure-data models (`Placement`, `SourceRef`, `SourcePage`, `OutputPage`,
  `Sheet`, `SheetPlan`, `LayoutSettings`, `LayoutWarning`, `Project`).
- **Import** — PDFs and image folders, interleaved. Natural filename order,
  EXIF orientation, DPI inference, lossless embedding via `img2pdf`.
  Metadata-only, so a 300-page PDF opens without rasterising anything.
- **Arrange** — reorder, rotate, skip, insert blanks. Snapshot undo bounded
  to 50 steps, debounced autosave, virtualised thumbnails.
- **Layout** — four independent margins (gutter/spine, fore-edge, head,
  tail) in pt/in/cm/mm, with a link toggle; `slack_to` choosing which margin
  absorbs width variation; left or right binding edge; landscape policy.
- **Preview** — rasterises the actual exported PDF. Fit-to-window, zoom
  (buttons and Ctrl+wheel), side-by-side front/back spread, and two distinct
  guides for the printer's imageable area and your content box.
- **Export** — Save PDF via pikepdf Form XObject placement, verified to emit
  pure translations with no scale drift. Bounded LRU cache for preview
  renders; batched assembly for large documents.
- **Print** — manual-duplex pass splitting, per-printer profiles, reload
  instructions, test-one-sheet, chunked submission, sheet-granular resume,
  and a session log.
- **CLI** — `impose`, `export`, `info`, `--version`. Imports only
  `deckle.core`, so it runs headlessly.
- `run.bat` dev launcher with a `doctor` subcommand.

### Fixed

Defects found during development, each with a full write-up in
`docs/decisions.md`:

- Gutter placed on the wrong side of the verso, so switching scale modes
  looked like the binding edge flipped.
- The gutter was derived from leftover width rather than the value set, so
  asking for 0.75in silently produced 1.17in.
- Placement used two different rules per axis — anchored horizontally,
  centred vertically — making margins behave inconsistently.
- Per-page scaling reproduced a narrower cover 2.5% larger than the body.
- `imageable_area_pt` read as a rect rather than margins, yielding a 3in
  margin from a 0.25in printer border.
- Path-traversal validation refused any source outside the project
  directory, which would have broken every real project on reopen.
- Preview and thumbnail renders were never cancelled, so rapid scrubbing let
  a stale render repaint over a newer one and leaked a thread per step.
- Printer enumeration ran synchronously in `MainWindow.__init__`; with an
  unreachable network printer the Windows spooler blocks per printer, so the
  app hung on launch.

### Removed

- The `scale_mode` setting. "Fill the height, then shrink until it fits" is
  the same operation as "fit", so the two modes were identical once the
  margin model existed; the only distinct behaviour was producing output
  that could not be printed.
