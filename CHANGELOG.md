# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing released yet. The gutter-shift path is functional and hardened.
Saddle-stitch signature imposition is implemented but **experimental** --
see the README. The calibration wizard is not built.

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
- **Signature imposition (experimental)** — `--fold-scheme folio` imposes
  two-up saddle-stitch signatures with fold lines, sewing-station marks and
  per-signature printing. Marked experimental because the page *ordering* is
  hand-written arithmetic whose only real check is folding a physical dummy,
  which has not been done. The default gutter-shift path is unaffected.
- **Binding schedules** — `deckle schedule`, and Save schedule on the
  Signatures tab. A printable work order derived from the imposed plan:
  gathering order per signature, which pages land on each sheet side, where
  padding blanks fall, sewing-station guidance, and a fore-edge creep
  estimate. It reads the plan the exporter used and never recomputes the
  imposition, so it cannot disagree with the PDF in your hands.
- **Diagnostic log** — `deckle.core.diagnostics`: rotating JSON Lines under
  the OS data dir, following the existing session-log conventions. Every
  path that degrades instead of failing now records why. The module is
  total: it can never raise, because a diagnostic that throws while
  reporting a problem turns a degraded app into a crashed one.
- **Actionable failures** — every I/O and malformed-input path now names
  what failed, which file, and the remedy: unavailable drive letters, output
  paths that are folders, missing parent folders, files that are not PDFs
  despite their name, corrupt and zero-page PDFs, empty image directories.
  Errors go to stderr with exit 1; stdout stays empty so it remains
  scriptable.
- **Bounded printer enumeration** — a 5-second deadline, after which the app
  proceeds as it does with no printers rather than waiting on the spooler.
- `export` and `impose` now print layout warnings to stderr. Previously only
  `info` did.

- **Paper by weight** — `deckle.core.paper` turns what is printed on the
  ream wrapper into a caliper, so nobody has to own calipers. A stock
  dropdown in the app, `--paper-weight 80gsm` (or `24lb` with
  `--paper-grade`) on the command line. Bulk varies about 10% between
  manufacturers, so the caliper is an estimate and is used only to predict
  fore-edge creep and spine width, never to place a page.
- **Signature size suggested from the paper** — how many sheets a
  gathering should hold, from the caliper and the planned trim, naming
  which constraint decided it: fore-edge creep, or a fold too thick to lie
  flat. Creep alone is not enough — 80gsm with a quarter-inch trim
  tolerates 62 sheets before creep shows, and nobody hand-sews that.
- **Crash recovery** — autosave has always been written on every edit and
  was never offered back. Reopening a project Deckle did not close cleanly
  now asks whether to recover the unsaved changes.
- **Crop, trim and explicit gatherings in the desktop app** — the last
  four layout settings that were reachable only from the command line,
  including a *Measure crop from the ink* button.

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
- **The default `fold_scheme="none"` path was broken** by a half-applied
  refactor: the imposer emitted bare output pages into an exporter that
  expected sides, so every export, preview and print on the default path
  raised. The test suite stayed green because the tests asserted the
  un-migrated shape.
- `_hash_plan` read an attribute that no longer existed, and recorded only
  one page per sheet side, so two different page orderings hashed
  identically.
- **Resuming a print run never checked whether the document had changed.**
  The layout fingerprint was stored and never compared, so resuming after a
  re-imposition printed the new layout's sheets at the old cursor — backs
  onto the wrong fronts, discovered only once the paper was ruined. Resume
  now refuses, and says why.
- **A non-ASCII output filename crashed Deckle after the export succeeded.**
  The PDF was written correctly, then reporting its name raised
  `UnicodeEncodeError` on the Windows console's legacy code page, producing
  a traceback and a failure exit code for a job that had worked.
- Exporting onto the source file was refused, but blamed a PDF viewer
  holding the file rather than the actual cause.
- 25 acceptance criteria described behaviour that no test asserted,
  including the front/back page-index mapping — nothing would have caught a
  transposition, the failure that ruins a manual-duplex print run.
- **`--crop` cut the wrong edge of any page a scanner had straightened.**
  A `/Rotate` flag means the stored left edge may be what you see at the
  top, and the exporter was the one layer working in stored rather than
  displayed coordinates — so it took the inset off the wrong side and
  scaled the placement from a width and height the wrong way round.
  `--auto-crop` measured in the displayed frame, so it was guaranteed to
  produce numbers that would be applied to the wrong edges.
- **A saved layout could ask for something this build cannot do, and get
  it.** `binding_edge: "middle"` is outside its declared `Literal` and
  nothing enforced it, so it imposed a book bound on the *right* — the
  opposite of the default — while `deckle info` reported no warnings.
  `flip_axis: "diagonal"` planned the back pass unturned, printing every
  back side upside down on a long-edge printer. `page_index: -2` is a
  valid Python index and printed the second-from-last page instead.
- Every JSON store — project files, printer profiles, the recent list, the
  print session — was written by truncating the target first, so a write
  that died partway destroyed the previous copy. Worst for autosave, whose
  whole promise is surviving a killed process, and for a calibration, which
  is measured by hand and derived from nothing.
- A `.deckle` that was valid JSON but not a project object printed a
  traceback rather than a message, on a file type that may be shared.
- Print sessions hashed `time.time()` into their id, which steps ~15ms on
  Windows, so runs of one document to one printer collided and shared a
  state file — one job silently overwriting another's resume point.
- **Two render threads could take the whole app down.** pdfium's rendering
  is not thread-safe and fails as a native access violation, not an
  exception — no traceback, nothing in the log, the process simply gone.
  Scrubbing the preview reached it on its own, because both rendering
  views start a new thread without waiting for the one they just
  cancelled; so did printing, which rasterises on the GUI thread. Every
  pdfium call is now serialised.
- The same source page used twice was cropped twice — the second copy lost
  double the insets, the fourth quadruple, with the numeral on a test
  document clipped in half by the fourth.
- A print chunk that reached paper but could not be written to the session
  log threw past the print dialog, and left the resume cursor reading
  zero — so the sheets printed, the user got a traceback, and resuming
  reprinted every one of them.
- Autosave's debounce timer and its shutdown flush could write the same
  file at once, which on Windows fails the rename outright rather than
  picking a winner.
- `schedule -o` and `crop-preview -o` truncated their output before
  writing, so a failed write destroyed the previous file. `export` had
  always written to a scratch file and renamed it; now all five commands
  that take `-o` do, and all five validate the path before doing any work
  and report a write failure rather than raising it.
- The single-sheet render cache grew without limit in the system temp
  directory — 8,297 files on one development machine — because its
  in-memory bound governs what it hands back, not what a killed process
  leaves behind, and the cleanup function its own docstring told callers
  to use had no callers.
- `deckle dummy --pages 0` answered a typo with a traceback, wrapping a
  message that already said exactly what was wrong.
- Resuming a print run with a negative sheet count silently reprinted one
  sheet and treated the rest of the pass as finished.

### Removed

- The `scale_mode` setting. "Fill the height, then shrink until it fits" is
  the same operation as "fit", so the two modes were identical once the
  margin model existed; the only distinct behaviour was producing output
  that could not be printed.
