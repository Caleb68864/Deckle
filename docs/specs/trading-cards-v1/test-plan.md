# Test strategy and requirement traceability

Status: planned tests, not tests executed by this documentation change. Every numbered step owns the implementation of its tests. This file defines cross-cutting oracles and release evidence.

## Requirement matrix

| ID | User-visible requirement | Development steps | Required evidence |
|---|---|---|---|
| R01 | Import a folder of front images in natural order | 01,02,07 | Named assets, 1/2/10 ordering, EXIF and failure tests |
| R02 | One shared back or several back images | 02,03,07 | Shared resolution, reused backs, mode round trip |
| R03 | Explicit front/back pairs survive edits | 01,03,07 | Ambiguity review; reorder/copies/skip/rotation identity tests |
| R04 | Correct duplex sheet positions, including partial sheets | 04,08,09 | Literal slot maps, independent physical simulator, raster landmarks |
| R05 | Toggle and adjust the visible black border | 01,04,05,07,08 | Numeric trim/art rectangles; exported edge pixels; toggle value retention |
| R06 | Extra black beyond cut covers small translation errors | 04,05,08 | Shifted-back cut-edge pixel samples; tolerance limits; printer-area warning |
| R07 | Save/load and undo/recover the entire deck | 01,03,06,07 | Versioned round trip, source checks, incomplete project recovery |
| R08 | Preview, full PDF, passes, and print use the same card plan | 04,05,08,09 | Rasterized PDF preview, command parity, output gating |
| R09 | Manual/hardware duplex, subsets, test sheet, and reprints | 09 | Recorded backend and physical feed/turn simulation |
| R10 | No stale preview/export/resume after material edits | 05,06,08,09 | Digest mutations, stale worker rejection, session refusal |
| R11 | Existing book workflows continue to work | 01,04,05,06,07,09,10 | Legacy project/digest fixtures and full regression suite |
| R12 | Printable physical size and useful registration allowance | 10 | Recorded actual-size paper trial or explicit unverified status |

## Independent geometry and physical oracles

Never validate a planner by calling that same planner to compute expected answers. The fixed examples in step 04 are hand-specified fixtures. In addition, implement a small test-only physical simulator which accepts **exported slot locations and raster landmarks**, paper dimensions, and a described paper-turn/feed operation. It must not import card_layout or call its coordinate helpers.

For canonical vertical turning, a visible back point `(xb,yb)` corresponds to front-paper point `(W-xb,yb)`; use card centers to avoid rectangle edge ambiguity. A whole-page half turn maps a point to `(W-x,H-y)` and reverses its up vector. A horizontal physical turn maps back to front-paper coordinates `(x,H-y)` and also reverses the relevant orientation. Compose the literal physical operations in test data. Do not infer the intended turn from the production `duplex_flip_edge` helper: explicitly say portrait-long=vertical, portrait-short=horizontal, landscape-short=vertical, landscape-long=horizontal. Use square paper as a separately pinned canonical vertical/long case.

For each front center, the physical simulator must find exactly one corresponding back with the same card ID and copy index, then check the source's upright orientation after viewing the opposite face in the declared manner. Pixel mirroring is forbidden: an asymmetric corner-color sequence/letter must retain handedness. The simulator also tracks a literal physical stack through reversed and unreversed feed order so correct per-sheet geometry cannot hide backs printed on the wrong sheets.

Use two separate inputs: the planner's rectangle metadata for precise geometry, and actual rendered color/arrow positions for proof the exporter respected it. Metadata-only equality is insufficient.

## Fixture matrix

| Dimension | Required cases |
|---|---|
| Pairing | Shared; all unique; reused subset; unmatched; ambiguous; surplus backs |
| Number | 0; 1; capacity-1; capacity; capacity+1; 3+ sheets |
| Paper | Letter portrait/landscape; A4; custom; too small; exact fit |
| Artwork | Portrait; landscape; square; transparent; EXIF; explicit 0/90/180/270 |
| Fit | Contain and centered cover with asymmetric landmarks |
| Border | Off; width zero; typical; fractional; invalid half-size; toggle with saved values |
| Bleed | Zero; 3 pt; larger capacity-changing value; before/after toggle |
| Marks | Off; on; partial sheet; outside bleed and inside reserved cell |
| State | Save/reopen; undo/redo; recovered autosave; missing/changed conversion |
| Output | Canonical both faces; front pass; back pass; subset; recorded print |
| Printer | Long/short turn; reverse stack on/off; offsets zero/nonzero; duplex auto/manual |

Test the full cross product only where it exercises physical orientation/order (step 09). For other dimensions, focused representative combinations plus property tests are sufficient; avoid hundreds of redundant widget cases.

## Border translation test construction

Use a white central art image, black border b=9 pt and bleed e=3 pt. Render at 720 DPI so 1 pt spans 10 pixels. Model the back as translated relative to fixed front cut lines. For shifts (0,0), (2,-1), (-2,2), and (2.5,2.5) pt, sample the fixed front perimeter at least 0.5 pt inside trim edges: all samples must be black, including near corners. Verify source artwork is absent from those samples.

Separately assert exact vector coverage and remaining-border formula at abs(dx)=e and abs(dy)=e; pixels at a mathematically exact edge are antialiased and are not a reliable strict-color oracle. With shift 4 pt on x, a point just inside the exposed front trim edge must show paper white. With zero bleed and nonzero shift, demonstrate the exposed strip even when the inside border is wide. This proves why both settings exist.

Check b=0 separately: external black can cover exterior displacement but a positive interior border is not guaranteed. UI must not describe zero inside width as retaining a visible border. Printer dead zones and simulated rotation/scale are outside the uniform translation guarantee and should not be asserted as covered.

## Gates and evidence

- Every implementation step records targeted commands and actual outcomes. Fix failures before dependent output features are enabled.
- End-to-end fixtures use original filenames and asymmetric artwork, never just blank identical pages; identical shared backs alone cannot detect a pairing bug.
- Full regression gate is the repository pytest suite under offscreen Qt. Existing license, core-purity, CLI purity, cache budget, atomic I/O, and docs checks remain mandatory.
- Do not auto-send jobs to a real printer in tests. Use existing recording/mocked backends; step 10 defines the separate manual paper trial.
- Do not claim passed tests in spec files. Implementation completion should add a dated result record with commit, environment, results, and physical verification status.
