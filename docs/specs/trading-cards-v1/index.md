# Printable RPG trading cards — implementation plan

Status: **specified, not implemented**. Prepared 2026-09-27 from the current repository. This change set contains documentation only.

## User outcome

Create a deck from a folder of front images; select one shared back or import multiple backs; review and edit explicit front/back pairs; arrange those cards on physical sheets; preview, export, and print both faces. A switchable black border has an adjustable width and an adjustable extension beyond the cut edge to tolerate modest registration errors.

The user's second occurrence of “front” is interpreted as “back.” No RPG system, card editor, text generation, or image generation is required. Images already contain the card artwork and text.

## Read before implementing

1. Read [architecture.md](architecture.md), including coordinate conventions and the schema contract.
2. Read [test-plan.md](test-plan.md) for independent physical checks.
3. Implement the numbered steps below in order. Read their listed existing files before editing. New paths are proposed files, not claims that they already exist.
4. Keep each step independently reviewable. Run its targeted tests, record results, and leave existing behavior green. Do not skip tests because the GUI looks correct.
5. If the checked-out code differs, locate the current equivalent symbol. Resolve routine implementation details locally; update this contract before intentionally changing an API or physical behavior. Do not weaken regression tests to accommodate a new design.

These instructions deliberately pin defaults, object ownership, algorithms, error behavior, and test oracles so a GPT Luna implementation session can work on one step without inventing the architecture.

## Steps

| Step | Deliverable | Dependencies |
|---|---|---|
| [01](01-models-and-validation.md) | Card models, validation, project dispatch seam | None |
| [02](02-image-ingestion.md) | Durable image assets with original filenames | 01 |
| [03](03-pairing-and-deck-edits.md) | Explicit pairs, matching, pure edit operations | 01–02 |
| [04](04-sheet-geometry.md) | Grid, mirrored back positions, border geometry | 01–03 |
| [05](05-pdf-rendering-and-digests.md) | PDF artwork, clipping, black bleed, cache identity | 04 |
| [06](06-project-persistence.md) | Save/load, recovery, source validation | 01–05 |
| [07](07-card-workspace.md) | Import and pairing workspace, settings, undo | 01–06 |
| [08](08-preview-and-output-gating.md) | Real PDF previews, guides, warnings, gating | 05–07 |
| [09](09-print-export-and-cli.md) | Existing duplex pipeline and headless access | 06–08 |
| [10](10-integration-and-release.md) | End-to-end checks, documentation, paper trial | 01–09 |

## Completion criteria

Every requirement R01–R12 in test-plan.md has passing automated coverage. Desktop and CLI produce equivalent physical plans. Existing book projects still load, preview, export, and print. A paper trial is separately recorded as passed or explicitly unverified; mocked printing cannot establish printer registration accuracy.

## Scope decisions

- Default finished card: 2.5 × 3.5 in (180 × 252 pt); customizable positive dimensions. Card orientation comes from these dimensions; no automatic card rotation for packing.
- One project contains either book pages or a card deck. “New card deck” creates a new project through the existing unsaved-work flow. Switching does not reinterpret book pages as pairs.
- Front folder plus shared back image, back folder, or multiple selected back images. Explicit mappings can reuse a back across several fronts.
- Pair by matching filename stems or by natural order, followed by visible review; manual reassignment always available. Never silently cycle backs or drop unmatched fronts.
- Copies per card, skip, reorder, rotate either face, and remove are included. File watching, folder recursion, CSV import, per-card border styles, horizontal-turn canonical layouts, and duplex calibration wizard work are deferred.
- Existing raster formats are reused; multi-frame images are rejected for this feature with a clear message. PDF import as a card face is deferred.
- Projects reference durable local image conversions; they do not bundle artwork. Portable deck packaging is deferred and must not be promised.
