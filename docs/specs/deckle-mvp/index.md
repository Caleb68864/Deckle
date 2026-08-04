---
type: phase-spec-index
master_spec: "../2026-08-04-deckle-mvp.md"
date: 2026-08-04
sub_specs: 14
---

# Deckle MVP — Phase Specs

Refined from [2026-08-04-deckle-mvp.md](../2026-08-04-deckle-mvp.md) on 2026-08-04.

| Sub-Spec | Title | Depends On | Wave | Phase Spec |
|---|---|---|---|---|
| SS-01 | Project scaffold, repo files, and core data models | — | 1 | [sub-spec-1-scaffold-core-models.md](sub-spec-1-scaffold-core-models.md) |
| SS-02 | SourceLoader — PDF and image ingestion | SS-01 | 2 | [sub-spec-2-sourceloader-ingestion.md](sub-spec-2-sourceloader-ingestion.md) |
| SS-03 | Imposer and LayoutStrategy — the gutter-shift engine | SS-01 | 2 | [sub-spec-3-imposer-layout-engine.md](sub-spec-3-imposer-layout-engine.md) |
| SS-04 | Exporter — pikepdf Form XObject composition | SS-01, SS-03 | 3 | [sub-spec-4-exporter-pikepdf-composition.md](sub-spec-4-exporter-pikepdf-composition.md) |
| SS-05 | Rasterizer — preview, thumbnails, ink bounds | SS-01, SS-03, **SS-04** | 4 | [sub-spec-5-rasterizer-preview-ink-bounds.md](sub-spec-5-rasterizer-preview-ink-bounds.md) |
| SS-06 | PrinterProfile and PassPlanner | SS-01, SS-03 | 3 | [sub-spec-6-printerprofile-passplanner.md](sub-spec-6-printerprofile-passplanner.md) |
| SS-07 | Project persistence, session log, and CLI | SS-02, SS-03, SS-04, SS-05 | 4 | [sub-spec-7-persistence-log-cli.md](sub-spec-7-persistence-log-cli.md) |
| SS-08 | QtPrintBackend and cross-platform print spike | SS-06 | 4 | [sub-spec-8-qtprintbackend-spike.md](sub-spec-8-qtprintbackend-spike.md) |
| SS-09 | App shell, ImportView, ArrangeView | SS-01, SS-02, SS-05 | 4 | [sub-spec-9-app-shell-import-arrange.md](sub-spec-9-app-shell-import-arrange.md) |
| SS-11 | PrintSession — resumable state machine | SS-06 | 4 | [sub-spec-11-printsession-state-machine.md](sub-spec-11-printsession-state-machine.md) |
| SS-10 | LayoutPanel and PreviewView | SS-03, SS-05, SS-09 | 5 | [sub-spec-10-layout-panel-preview.md](sub-spec-10-layout-panel-preview.md) |
| SS-13 | Calibration wizard (**`dispatch: manual`**) | SS-06, SS-08 | 5 | [sub-spec-13-calibration-wizard.md](sub-spec-13-calibration-wizard.md) |
| SS-12 | PrintDialog — manual-duplex print UI | SS-08, SS-10, SS-11 | 6 | [sub-spec-12-print-dialog.md](sub-spec-12-print-dialog.md) |
| SS-14 | Integration, golden fixture, license audit | SS-07, SS-09, SS-10, SS-12 | 7 | [sub-spec-14-integration-golden-fixture.md](sub-spec-14-integration-golden-fixture.md) |

## Wave Ordering

```
Wave 1   SS-01
Wave 2   SS-02   SS-03
Wave 3   SS-04   SS-06
Wave 4   SS-05   SS-08   SS-11
Wave 5   SS-07   SS-09   SS-13
Wave 6   SS-10
Wave 7   SS-12
Wave 8   SS-14
```

Cross-spec dependency audit: **clean.** No consumer sits in an earlier wave than its
producer.

**Re-waved 2026-08-04.** SS-05 now depends on SS-04 because the preview renders the actual
exported PDF rather than compositing independently. That lengthens the critical path by one
wave and is worth it — it removes the possibility of the preview and the export sharing a
bug and agreeing with each other.

## Requirement Traceability Matrix

| Requirement | Covered By |
|---|---|
| R1: PDF + image dir import, natural sort, EXIF | SS-02 |
| R2: Reorder, rotate, skip, insert blank, with undo | SS-09 |
| R3: Paper, gutter, binding edge, scale mode configurable and live | SS-03, SS-10 (split) |
| R4: One placement transform, consumed verbatim by all three consumers | SS-03 (owner), SS-04, SS-05, SS-10 (invariant test) |
| R5: Content outside the imageable area flagged and distinguished | SS-10 (owner), SS-05, SS-06 |
| R6: Printer calibrated once, profile persists | SS-13 (owner), SS-06 |
| R7: Pass 1 → reload → pass 2, collates correctly | SS-06, SS-11, SS-12 (split) |
| R8: "Test one sheet first" on every pass | SS-11 (owner), SS-12 |
| R9: Arbitrary sheet subset reprintable through the same path | SS-06, SS-11 |
| R10: Interrupted pass resumes at sheet granularity | SS-11 (owner), SS-12 |
| R11: Core imports no Qt; exercisable headlessly via CLI | SS-01, SS-07 |
| R12: Every submitted print job's parameters logged | SS-07 (owner), SS-08, SS-11 |
| R13: Zero AGPL dependencies, no external runtime binaries | SS-14 |
| R14: Pinebox golden-fixture regression passes | SS-14 |

**No orphaned requirements.** R3, R7, and R10 are split-ownership; the owner is named where
one exists.

## Notes for Workers

**Read the vault research before writing PDF code.** `Caleb's Vault/Software/` contains
verified, executed findings for every library in this stack — `pikepdf` (32 notes),
`img2pdf`, `pypdfium2`, plus disqualification write-ups for `pdfimpose` and `cpdf`. Code
samples there are real output from pikepdf 10.11.0 / libqpdf 12.3.2 on Windows 11, not
composed from documentation. Individual phase specs cite the relevant note by name.

**Two sub-specs lead with a spike, deliberately:**

- **SS-08** writes a `QPrinter` cross-platform capability report *before* implementing.
  Divergence there invalidates part of the architecture rationale and is an escalation, not
  a workaround.
- **SS-13** enumerates the calibration state space *before* designing the wizard. It is
  marked `dispatch: manual` because it needs a physical printer and a human looking at paper.

**Three hard gates enforced by tests, not convention:** no Qt in `deckle.core`
(SS-01), preview must equal export (SS-10), and no AGPL dependency (SS-14).

## Execution

```
/forge-run ../2026-08-04-deckle-mvp.md              # all phase specs
/forge-run ../2026-08-04-deckle-mvp.md --sub 3      # a single sub-spec
```

Point `/forge-run` at the master spec — it auto-detects these phase specs.
