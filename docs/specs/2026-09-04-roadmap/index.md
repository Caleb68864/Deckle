---
type: spec-index
roadmap: "../../ROADMAP.md"
date: 2026-09-04
specs: 79
---

# Deckle roadmap — implementation specs

Seventy-nine specs refined from [`docs/ROADMAP.md`](../../ROADMAP.md) at commit
`08e7f49`. Each is self-contained: an implementer who has read
[`00-environment.md`](00-environment.md) and nothing else should be able to
open one file and finish the work.

**Read first:**

1. [`00-environment.md`](00-environment.md) — how to set up a clone that can
   run the suite, the repository rules every commit must satisfy, and the
   traps in this environment. The suite does not pass on a fresh clone until
   the R0 specs land; the file says exactly what to do about that.
2. [`_TEMPLATE.md`](_TEMPLATE.md) — the eight sections every spec follows.
3. [`00-order-and-collisions.md`](00-order-and-collisions.md) — the file-level
   collision matrix for the application-layer specs.

Every spec's own §6 and §8 name the specs it collides with. Where two specs
edit the same function, only one can be in flight at a time; that is stated
in both.

---

## How to read a spec

`§1 Context` says what is wrong and, where it was verified by running code,
gives the repro and its current output. `§2 Current code` quotes the defect
verbatim with line numbers and lists every call site. `§3 Change` is a
numbered edit list with no design judgement left in it. `§4 Tests` are to be
written **before** the change and confirmed failing for the right reason.
`§5 Acceptance` is a table of shell commands that exit 0 when the work is
done. `§7` is the `docs/decisions.md` entry to paste, because the pre-commit
hook refuses a code commit without one. `§8 Traps` is what will bite you.

Four specs vary from the template deliberately: **B6**, **B19** and **B26**
carry two complete alternative branches because the choice is the owner's;
**B35** and **F12** are collections of independent sub-specs; **F4** is a
physical procedure no agent can close; **N9** is a pointer to **B10**.

---

## Blockers — nothing else is checkable until these land

The suite cannot run on a fresh clone. Do these first, in this order.

| Spec | What |
|---|---|
| [R0.1](R0.1-sample-fixture.md) | Commit `tests/fixtures/sample.pdf`. It must be 2 pages. 79 failures and 25 fixture errors without it |
| [R0.2](R0.2-numpy-out-of-the-suite.md) | One test imports numpy, which is not a dependency. Collection aborts entirely |
| [R0.3](R0.3-commit-the-pyinstaller-spec.md) | `packaging/deckle.spec` is gitignored and absent. Nobody can build on any platform |
| [R0.4](R0.4-restore-a-readable-mode.md) | A test restores a write-only mode and then reads the file. Fails on POSIX |
| [R0.5](R0.5-delete-the-stray-equals-file.md) | Delete the tracked file named `=` |
| [R0.6](R0.6-one-ci-job.md) | One GitHub Actions job. Would have caught all five above |

R0.6 depends on the other five. R0.4 depends on R0.1.

---

## Bugs

### Wrong output on paper

| Spec | What | Size |
|---|---|---|
| [B36](B36-the-back-pass-paints-backs.md) | **The desktop back pass prints the fronts again**, unturned, with the registration offset unapplied. Found while specifying B4 | S |
| [B1](B1-users-rotation-reaches-the-exporter.md) | A user's per-page rotation never reaches the exporter. Also: every rotated thumbnail raises, and the export rotation matrix turns the wrong way | S |
| [B2](B2-exporter-draws-the-half-turn.md) | The exporter silently drops a 180° rotation. B1 needs this | S |
| [B5](B5-scale-and-placement-judge-the-same-cell.md) | Folio scales against the paper and places against the cell, so landscape sources come out 23% small | S |
| [B6](B6-true-size-or-fit-to-margins.md) | Printed sheets are ~6% small and the wrong shape. **Two branches; owner picks** | S |
| [B7](B7-schedule-prints-reader-facing-page-numbers.md) | The binding schedule's page numbers are file indices | S |
| [B8](B8-one-creep-predicate.md) | Three creep comparisons, two operators, and one that reads a setting the grouping overrode | S |
| [B18](B18-nonpositive-sheets-per-signature.md) | `--sheets-per-signature 0` with balanced blanks raises `ZeroDivisionError` | S |
| [B19](B19-landscape-policy-scale-vs-letterbox.md) | `scale` and `letterbox` are byte-identical. **Two branches; owner picks** | S |
| [B20](B20-export-refuses-unknown-sheet-indices.md) | An unknown sheet selection writes a 0-page PDF that passes verification | S |

Order within this group: **B36**, then **M2 → B5 → B1** (all three edit
`_place_page`), with **B2** before B1. B6, B7, B8, B18, B19, B20 are
independent of each other.

### Lost work and desktop state

| Spec | What | Size |
|---|---|---|
| [B9](B9-autosave-path-follows-project-path.md) | Autosave is dead for any project first saved in the session | S |
| [B10 / N9](B10-N9-dirty-and-save-prompts.md) | No dirty flag, no save prompt on close or open. Work is discarded silently | M |
| [B12](B12-suspend-handlers-during-refresh.md) | Refreshing the panel mutates the project, clears redo, and rounds crop values on every open | S |
| [B13](B13-import-worker-guard-and-shutdown.md) | Import results land out of order, into an orphaned state, and run pdfium into teardown | S |
| [B14](B14-cancelled-resume-count-aborts.md) | Cancelling the resume prompt reprints the whole pass | S |
| [B11](B11-unit-change-misses-trim-and-crop.md) | Unit changes skip trim and the crop boxes | S |
| [B29](B29-thickness-decimals-and-suggestion.md) | Paper thickness is unusable in points, and one arrow press moves it 30 sheets | S |
| [B30](B30-requery-printers.md) | Printers are enumerated once at startup, so one plugged in later stays unusable | S |
| [B35](B35-miscellany.md) | Seven independent small defects, each a complete sub-spec | S each |

Order: **B9 → B14 → B16 → B30 → B12 → B13 → B35 → B10/N9**, per
[`00-order-and-collisions.md`](00-order-and-collisions.md). B11 and B29 are
subsumed by M1 if M1 is scheduled.

### Print path, CLI and I/O

| Spec | What | Size |
|---|---|---|
| [B3](B3-resume-detects-a-changed-document.md) | Resume accepts a re-imposed document with different geometry | S |
| [B4](B4-one-owner-for-the-session-log.md) | The print log is written twice, and can raise after the sheets printed | S |
| [B15](B15-push-resolved-printer-profile.md) | The preview's printer guide describes a printer you may not own | M |
| [B16](B16-print-dialog-profile-combo.md) | Every printer gets the first built-in preset | S |
| [B17](B17-off-thread-long-operations.md) | Print, export, open and auto-crop all block the GUI thread | M |
| [B21](B21-a-corrupt-profile-says-so.md) | A corrupt profile reports "no such profile", or crashes the dialog | S |
| [B22](B22-profile-without-pass-still-registers.md) | `--profile` without `--pass` silently drops the calibration | S |
| [B23](B23-every-warning-reaches-stderr.md) | A project from a newer build loses fields silently | S |
| [B24](B24-only-layout-flags-are-layout-flags.md) | Six flags are reported as ignored and then honoured | S |
| [B25](B25-paper-accepts-the-units-it-names.md) | `--paper` rejects `cm` while its error message offers it | S |
| [B26](B26-a-source-path-that-survives-the-directory.md) | Relative source paths, and an image cache that evicts live projects. **Part B is a branch** | S / M |
| [B27](B27-session-state-survives-the-reboot.md) | Crash-recovery state lives in `/tmp`, which the reboot clears | S |
| [B28](B28-read-the-version-fields.md) | Version fields are written and never read. A profile without one fails to load at all | S |

Order: `print_session.py` cluster **B4 → B3 → B28 → B27**; `cli.py` cluster
**B25 → B23 → B21 → B24 → B22**; **B16 → B15**.

### Core hygiene

| Spec | What | Size |
|---|---|---|
| [B31](B31-warning-kind-hygiene.md) | One emitted warning kind is undeclared; one declared kind is never emitted | S |
| [B32](B32-core-correctness-trio.md) | Crop-blind margin measurement, rotation-blind blank thumbnails, dpi-blind ink cache | S |
| [B33](B33-loader-memory-and-lock.md) | Hashing under the pdfium lock, and every image held in memory at once | S |
| [B34](B34-atomic-write-mode-and-profile-names.md) | Every save tightens the file to owner-only; a network printer name escapes its directory | S |

---

## Maintainability

| Spec | What | Size |
|---|---|---|
| [M1](M1-layout-panel-binding-table.md) | The layout panel's 28 controls, maintained by hand in five places, become one binding table | M |
| [M2](M2-extract-the-placement-seams.md) | Extract the placement seams so B1, B5 and B8 have somewhere to land | M |
| [M3](M3-main-window-split.md) | Split the main window into printer query, project actions and shutdown | M |
| [M4](M4-cli-package-split.md) | Split `cli.py` into a package. **Land last** among CLI specs | M |
| [M5](M5-one-paper-table.md) | One paper table, one unit conversion, one weight-to-caliper function | S |
| [M6](M6-three-second-implementations.md) | Three second implementations of things that already exist | S |
| [M7](M7-dead-scaffolding.md) | Delete dead scaffolding. Two items the roadmap listed are **not** dead | S |
| [M8](M8-frozen-means-tuples.md) | Five frozen dataclasses carry mutable lists | S |

**M2 before B5 and B1. B12 before or inside M1. M4 after every other CLI
spec.** M3 after every other `main.py` spec.

---

## Features the docs promise

| Spec | What | Depends on | Size |
|---|---|---|---|
| [F1](F1-profile-editor.md) | A printer profile picker and editor. Most of the wizard's value, a fraction of its cost | B16 done or skipped | M |
| [F2](F2-calibration-spike.md) | The calibration wizard, as the enumeration-first spike its own sub-spec demands | — | L |
| [F3](F3-registration-target.md) | A two-sided target you read the offset numbers off | F1 | M |
| [F4](F4-folded-dummy.md) | **Fold a folio dummy on paper.** `dispatch: manual`. Gates F7 and F8 | — | S + hardware |
| [F5](F5-cli-margin-parity.md) | Six `LayoutSettings` fields the GUI can set and the CLI cannot | — | S |
| [F6](F6-sheet-subset-picker.md) | Reprint an arbitrary sheet subset from the dialog | — | S |
| [F7](F7-quarto.md) | Quarto and octavo. **Also gated on B2**, undeclared in the roadmap | F4, B2 | L |
| [F8](F8-french-fold.md) | French fold: one pass, no reload, no misregistration | F4 | M |
| [F9](F9-hardware-duplex.md) | Wire the duplex path that is built, tested and uncalled | F1 + 2 decisions | S |
| [F10](F10-read-it-back.md) | Show the fold simulator's reading order before committing paper | — | S |
| [F11](F11-custom-paper-size.md) | Type a custom paper size in the GUI | — | S |
| [F12](F12-deferred-open-questions.md) | Four recorded open questions, four sub-specs | mixed | S-M each |
| [F13](F13-linux-packaging.md) | A Linux build and a `run.sh` | **R0.3** | M |

---

## Features not in any doc

| Spec | What | Size |
|---|---|---|
| [N1](N1-unsaved-autosave.md) | Autosave a project that has never been saved | M |
| [N2](N2-driver-imageable-area.md) | A real imageable area from the driver. Harder than the spike suggests | M |
| [N3](N3-proof-sheet-with-ruler.md) | A proof sheet with a ruler, from the dialog | S |
| [N4](N4-single-pass-pdf.md) | Save one pass as a PDF | S |
| [N5](N5-sewing-station-positions.md) | Sewing station *positions*, not just a count | S |
| [N6](N6-page-range-selection.md) | Page ranges at import | S |
| [N7](N7-menu-bar-and-shortcuts.md) | A menu bar and keyboard shortcuts | M |
| [N8](N8-drag-and-drop-import.md) | Drag and drop import | S |
| [N10](N10-multi-select-page-actions.md) | Multi-select rotate, skip and remove | S |
| [N11](N11-crop-composite-in-app.md) | Show the crop composite in the app | M |
| [N12](N12-schedule-under-flat-sheets.md) | The schedule under flat sheets, with the spine width it currently omits | S |
| [N13](N13-about-and-diagnostics.md) | About, and a way to reach the diagnostics folder | S |
| [N14](N14-cli-scripting-surface.md) | `--json`, `--dry-run`, `deckle profile`. Recommends dropping `deckle print` | M |
| [N15](N15-default-settings.md) | Save these settings as my default | S |

Order on the Arrange side: **N6 → N10 → N7**. On the panel side:
**N5 → N11 → M1**. On the CLI side: **N5 → N6 → N14 → M4**. In `main.py`:
**B9/B10 → N7 → N8 → N4 → N13 → M3 last**.

---

## Documentation

[D-docs-pass](D-docs-pass.md) — eleven documentation claims the code does not
support, each quoting the current sentence and giving its replacement. No
code. Run it at the end of each milestone rather than as its own item, or the
guide drifts again.

---

## Decisions the owner still owes

Six specs cannot be finished without an answer. Each writes both branches in
full, so the answer is a choice rather than a design session.

| Spec | Question |
|---|---|
| [B6](B6-true-size-or-fit-to-margins.md) | Print the sheet true size and let the border clip it, or keep fitting it to the border and state the reduction? |
| [B19](B19-landscape-policy-scale-vs-letterbox.md) | Implement `letterbox` as distinct from `scale`, or collapse the enum? |
| [B26](B26-a-source-path-that-survives-the-directory.md) | Spool image imports beside the project, or exempt referenced files from cache eviction? |
| [F9](F9-hardware-duplex.md) | Wire hardware duplex now? And does a duplexer get the hand-measured back offset? |
| [F12](F12-deferred-open-questions.md) | §B and §D are both recorded as human-approval escalations |
| [F13](F13-linux-packaging.md) | Is Linux a target, and in what format? |

One more, outside the specs: **R0.6** exposes that `pyproject.toml` claims
Python 3.11, the README claims 3.11, and `run.bat` claims 3.12. The CI matrix
needs to know which is true.

---

## Suggested first milestone

The roadmap's §6 gives the full order. The first milestone, concretely:

1. R0.1, R0.2, R0.3, R0.4, R0.5, then R0.6. Half a day. Everything after
   this is checkable.
2. B36. One afternoon, and it is the difference between the app printing a
   book and printing the fronts twice.
3. B2, then M2, then B5, then B1. The imposition core, tests first.
4. B9, B12, B13, B14. The lost-work bugs that do not need M1.
5. The D docs pass for whatever the above changed.
