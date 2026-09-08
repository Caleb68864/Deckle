---
type: roadmap
date: 2026-09-04
status: draft -- for discussion before any of it is scheduled
---

# Deckle roadmap

A consolidated view of what is broken, what is hard to maintain, and what is
missing, as of commit `08e7f49`. Produced by reading every module under
`deckle/` plus the docs, plans and research, and by running the full test
suite on a fresh Linux clone. Nothing had been implemented when it was
written; see the reconciliation note below for what has landed since.

**Every item here now has an implementation spec** under
[`docs/specs/2026-09-04-roadmap/`](specs/2026-09-04-roadmap/index.md) — 79 of
them, each self-contained enough to hand to an implementer who has read
nothing else. Start at that directory's `index.md`; read its
`00-environment.md` before touching code.

> **Reconciled 2026-09-08.** This roadmap was written at commit `08e7f49` and
> had drifted: fifteen of its findings had already been fixed but were still
> listed as open. Those rows are now marked ✅ with the evidence that closed
> them — R0.1-R0.6, B1, B2, B5, B7, B8, B18, B19, B20 and B36. **B9 was
> then fixed on 2026-09-08**, along with B3, B4, B11, B12, B14, B16, B21,
> B22, B25 and the new B38-B40, in the same session; **B6 was decided and
> fixed**; F1 got its picker half and N10 its rotate/skip half. Everything
> unmarked was re-checked against the tree on that date and is still open.
> Verified by running the suite on Linux: **1636 passed, 22 skipped**.
> New findings from the same audit are in `vault/` (gitignored), and the
> cross-project view is in `../../ROADMAP.md`.

Each item carries an ID so it can be referred to in that conversation.
Sizes: **S** under a day, **M** a few days, **L** a week or more, or gated on
hardware. Findings marked *verified* were reproduced by running code, not
just by reading it.

---

## 0. State of the tree on a fresh clone

**This section is closed.** A fresh clone once needed three undocumented
steps before the suite would run; all six findings landed, and the suite now
runs clean on a fresh Linux clone (1636 passed, 22 skipped). Kept for the
record.

| ID | Finding | Size |
|---|---|---|
| **R0.1** ✅ | **Landed 2026-09-08 audit:** `tests/fixtures/sample.pdf` is committed. `tests/fixtures/sample.pdf` is not in the repository (`*.pdf` under fixtures is gitignored) but `tests/fixtures/README.md` calls it "checked in" and ~100 tests depend on it. A fresh clone gets 79 failures plus 25 fixture-setup errors (collection itself succeeds; the collection abort belongs to R0.2). It must be a **2-page** PDF; `deckle dummy -o tests/fixtures/sample.pdf --pages 2` produces one that passes. Either un-ignore that one file or generate it in a session-scoped fixture. | S |
| **R0.2** ✅ | **Landed 2026-09-08 audit:** the test was rewritten onto Pillow, so numpy is no longer imported (`tests/test_repeated_source_page.py:57`). `tests/test_repeated_source_page.py` imports `numpy`, which is not in `[dev]`. Collection of the whole suite aborts. Add it to `dev` extras or rewrite the test without it. | S |
| **R0.3** ✅ | **Landed 2026-09-08 audit:** `packaging/deckle.spec` is tracked. `packaging/deckle.spec` is gitignored by the `*.spec` pattern and absent from the tree, yet `run.bat package` and `tests/test_packaging_audit.py` both read it. Nobody but the original Windows machine can build. Commit it with a negating ignore rule. | S |
| **R0.4** ✅ | **Landed 2026-09-08 audit:** restores `0o644` (`tests/test_hardening_io.py:164,191`). `tests/test_hardening_io.py::test_export_over_a_read_only_file_says_it_is_read_only` fails on Linux: the `finally` restores `S_IWRITE` (write-only), then reads the file. Restore `0o644`. | S |
| **R0.5** ✅ | **Landed 2026-09-08 audit:** the stray `=` is deleted. An empty file literally named `=` is tracked at the repo root (from commit `b549d95`, almost certainly a shell redirect typo). Delete it. | S |
| **R0.6** ✅ | **Landed 2026-09-08 audit:** `.github/workflows/test.yml`. No CI. Nothing runs the 1,500 tests on a push. A single GitHub Actions job on Linux with `QT_QPA_PLATFORM=offscreen` would have caught R0.1-R0.4. | S |

With R0.1, R0.2 and R0.4 fixed locally: **1507 passed, 22 skipped, 2 failed**
(the two being R0.3 and R0.4).

---

## 1. Bugs, ranked

### 1a. Wrong output on paper (HIGH)

These change what gets printed. They come first because paper is the
expensive thing.

| ID | Finding | Where | Size |
|---|---|---|---|
| **B36** ✅ | **Landed 2026-09-08 audit:** the back pass no longer reprints the fronts. *Verified.* **The desktop back pass prints the fronts again.** `PrintSession._submit_sheets` calls `backend.submit` with five positional arguments, so `side`, `rotate_backs` and `pass_index` take their front-side defaults on *both* passes. Pass 2 rasterises the front of every sheet, unturned, and the measured back offset is never applied. `QtPrintBackend.submit_pass` threads all three correctly and has **no caller anywhere in `deckle/`**. Invisible to tests because the `PrintBackend` Protocol declares only the five arguments and the session's fake backend mirrors it exactly. Found after the roadmap's first draft. | `core/print_session.py:559-564`, `core/printing.py:82-92`, `app/backend.py:402` | S |
| **B1** ✅ | **Landed 2026-09-08 audit:** `d16969d` — the Rotate button turns the page, not just the thumbnail. *Verified.* **A user's per-page rotation never reaches the exporter.** `_place_page` consumes `slot.rotate_deg` only to swap the dimensions for sizing, then emits `rotate_deg=0`. Under the default policies a page rotated 90° in Arrange exports unrotated and shrunk to 0.77; under `rotate` it comes out sideways and clipped; 180° is a complete no-op. No test imposes or exports a page with `rotate_deg != 0`. **Two further defects found while specifying this**, both unpinned by any test: `render._rotation_quarter_turns` hands pdfium quarter turns (0-3) where `PdfPage.render(rotation=)` wants degrees, so *every* rotated thumbnail raises `KeyError` out of the Arrange grid; and `export._rotation_matrix` builds a counter-clockwise turn while the PDF `/Rotate` convention, pdfium and the Rotate button are all clockwise. | `core/layout.py:375-376, 490-496`, `core/render.py:377-379`, `core/export.py` | S |
| **B2** ✅ | **Landed 2026-09-08 audit:** `2d10681` — the half turn is drawn, not dropped. `rotate_deg == 180` is silently dropped by the exporter (only 90/270 take the rotation branch). Latent until B1 is fixed, then live. | `core/export.py:344-380` | S |
| **B3** ✅ | **Fixed 2026-09-08.** The two answers to "is this the same plan?" are now one: `deckle/core/plan_digest.py` holds the canonical digest and both the exporter's render cache and the session's resume guard import it. `STATE_VERSION` bumped 2 → 3 for the same reason v2 was bumped — a pre-upgrade session would otherwise be refused with "the document changed" when what changed was Deckle. Original finding: **Print-session resume does not detect most "document changed" cases.** `_hash_plan` covers only sheet index, side presence and page *indices*. Change the gutter, margins, paper, crop, or open a different 16-page PDF, re-impose, resume the back pass: accepted. Backs print with different geometry than the fronts already on the paper. This is exactly what `StaleSessionError(reason="plan")` promises to catch. `export._plan_hash` already hashes content; share it. | `core/print_session.py:109-145` | S |
| **B4** ✅ | **Fixed 2026-09-08.** The session's copy is deleted; the backend's guarded call is the only writer, and `PrintBackend` now says so in the Protocol so a second backend cannot silently drop the record. The test patched only the backend's namespace, which is exactly why the duplicate survived a test file this thorough -- it now patches both, and shows 2 records before the fix and 1 after. Original finding: `log_print_job` is called by both the backend and the session, so every chunk is logged twice, and if the data dir is unwritable the session's unguarded call raises *after the sheets printed and before the cursor is saved*, the exact bug the backend comment says was fixed. The test patches only the backend copy. | `core/print_session.py:559-564`, `app/backend.py:381` | S |
| **B5** ✅ | **Landed 2026-09-08 audit:** `9262fff` — a folio leaf is scaled and placed against the same cell. Folio disagrees with itself about rotating landscape sources: `document_scale` judges against the paper (never rotates), `_place_page` against the cell (rotates). *Verified:* four landscape pages on folio Letter get scale 0.50 where 0.647 fits, plus a spurious `mixed_orientation` warning. | `core/layout.py:292-294` vs `383-387` | S |
| **B6** ✅ | **Decided and fixed 2026-09-08: print at actual size.** The owner's call, recorded in `docs/decisions.md`. `_paint_rendered_page` paints 1:1 at the paper corner; the transform is now only image pixels to device pixels by `printer.resolution() / dpi`, and `imageable_area_pt` no longer enters it. Content inside the printer's border is clipped rather than shrunk, which `clipped_by_imageable_area` already warns about. `tests/test_print_painting.py` was rewritten rather than extended — it existed to hold this question open and now holds the answer. Original finding: Printing scales the whole sheet into the imageable area with aspect ignored, so books print ~6% small and distorted with asymmetric borders. The preview never shows this. Pinned by `test_print_painting.py:167-225` as an undecided product decision, so it needs a *decision*, then a one-line fix (draw 1:1 at paper origin). | `app/backend.py:519-529` | S |
| **B7** ✅ | **Landed 2026-09-08 audit:** `b66e62d` — pages are numbered as the reader reads them. The binding schedule's "reader-facing page numbers" are `source page_index + 1`, wrong whenever pages are skipped, blanks inserted, or more than one source is imported. The bench sheet then lists file indices. | `core/schedule.py:134-146` | S |
| **B8** ✅ | **Landed 2026-09-08 audit:** `b66e62d` — creep has one opinion. Creep advisory in layout uses `settings.sheets_per_signature` even when `signature_lengths` or `blank_mode="balanced"` decided the real sizes, so layout and schedule give different answers for the same plan. *Verified.* Three creep comparisons across `paper.py`, `layout.py`, `schedule.py` also use two different operators. | `core/layout.py:800`, `paper.py:207`, `schedule.py:192` | S |

### 1b. Lost work and desktop state (HIGH)

| ID | Finding | Where | Size |
|---|---|---|---|
| **B9** ✅ | **Fixed 2026-09-08.** `autosave_path` is now a read-only property deriving from `project_path` on every read, so it can no longer drift from it — Save re-points the autosave by assigning `project_path`, and Save As moves it rather than writing the new project into the old project's autosave. Covered by `test_autosave_follows_the_path_a_first_save_gives_the_project` and `test_autosave_repoints_when_the_project_is_saved_somewhere_else`, both confirmed failing against the previous code. Original finding: **Autosave is dead for any project first saved in the session.** `autosave_path` is computed once in `AppState.__init__`; Save project sets `project_path` but never re-derives it. Import, arrange for an hour, Save, keep editing, crash: nothing was autosaved. | `app/state.py:205`, `app/main.py:1054` | S |
| **B10** | **Closing with a never-saved project silently discards everything.** No dirty flag, no `setWindowModified`, no "save before closing?", and Open project replaces the current one without asking. | `app/main.py:1129-1138, 945` | S |
| **B11** ✅ | **Fixed 2026-09-08.** `_on_unit_changed` now carries trim and all eight crop boxes, each with its range cap and its own `pt` precision -- trim and crop keep three decimals where the older boxes round to zero, because a crop inset is routinely a fraction of a point (the zero-decimal rounding on the others is B29 and is untouched). Original finding: Unit change (in/mm/cm/pt) converts gutter, margins and thickness but **not trim or the eight crop boxes**, which keep their old number and their construction-time range. Switch in→mm and a trim showing 0.125 is now read back as 0.125 mm. | `app/views/layout_panel.py:1430-1442` | S |
| **B12** ✅ | **Fixed 2026-09-08.** The hand-maintained list is gone: `refresh_from_project` blocks `self.widget.findChildren(QWidget)`, so it cannot drift again as a control is added. Original finding: `refresh_from_project` blocks signals on a hand-maintained list that omits trim, crop and paper-stock; refreshing then fires their handlers, which `mutate` the state and **clear the redo stack**. Undo a trim change and Ctrl+Y is inert; opening a project pushes up to nine spurious undo entries. | `app/views/layout_panel.py:1223-1265` | S |
| **B13** | Import results have no "is this still the current worker" guard, so two quick imports apply in completion order, and `open_project` swaps `AppState` mid-import so the import lands in an orphaned state. Import threads are also skipped by `stop_background_work`, so quitting mid-import runs pdfium into teardown, the crash class `_live_threads` exists to prevent. | `app/views/import_view.py:197-208`, `app/main.py:1004, 1160` | S |
| **B14** ✅ | **Fixed 2026-09-08.** `_default_ask_resume_count` returns `None` when the dialog is dismissed and `_offer_resume` abandons the resume without loading the session, so the offer survives to next time. `0` stays a real answer -- "nothing came out, reprint the pass" -- which is why cancel needed its own value. Original finding: Cancelling the "How many sheets came out?" prompt returns 0 and resumes from sheet 0, reprinting the whole interrupted pass. | `app/views/print_dialog.py:369-376` | S |
| **B37** | **The session cursor follows the bookkeeping, not the paper.** `PrintResult.submitted` is set carefully on both branches of `QtPrintBackend.submit` to say how many sheets physically printed, and no caller reads it. `_advance` returns on `result.error` before `sheet_cursor += len(chunk)`, so a chunk that printed and then failed to log leaves the cursor behind it and a resume reprints that paper. Fixing B4 closed the traceback route into this; the error branch is untouched. Needs a decision rather than an edit: advancing past a chunk that only *partly* printed would be worse than reprinting it, so the fix is probably to advance by `result.submitted` rather than by `len(chunk)`. Pinned by a strict `xfail`, `test_the_cursor_follows_the_paper_not_the_bookkeeping`. Found 2026-09-08 while fixing B4. | `core/print_session.py:623-629`, `core/printing.py` (`PrintResult.submitted`) | S |

### 1c. Wrong or misleading, but recoverable (MED)

| ID | Finding | Where | Size |
|---|---|---|---|
| **B15** | The preview's red "printer imageable area" guide and "Use printer margins" always use the first built-in preset (`DEFAULT_PROFILE`, 0.25in). Nothing pushes the resolved printer profile to the panel or preview, and the backend never reads the driver's printable rect (getting a real one is N2, and harder than the spike suggests). The GUIDE says the red line is "your printer's hardware limit"; it is not. | `app/main.py:442, 523`, `app/backend.py` | M |
| **B16** ✅ | **Fixed 2026-09-08.** A "Paper:" picker in the print dialog offers every builtin preset, described by the two axes that decide the reload instruction, and re-offers per printer. A calibration, where one exists, sorts first and is preselected. Original finding: The desktop app only ever resolves the *first* built-in profile (`generic_face_down_reversed`). A face-up printer owner gets the wrong reload instruction with no in-app way to pick the other preset. | `app/views/print_dialog.py:59-90` | S (see F1) |
| **B17** | Print submission, Save PDF, Open project (sha256 of every source) and Auto-crop (rasterise every page) all run **synchronously on the GUI thread** with no cancel. A 60-sheet pass freezes the window for minutes; the "Exporting..." status never paints. | `print_dialog.py:279-347`, `main.py:969, 1097`, `layout_panel.py:1629` | M |
| **B18** ✅ | **Landed 2026-09-08 audit:** `e397769` — a gathering of zero is refused. `signature_lengths`/`--sheets-per-signature 0` with `blank_mode="balanced"` raises `ZeroDivisionError` (*verified*); negatives produce empty groups. The `end` path validates cleanly. | `core/layout.py:770` | S |
| **B19** ✅ | **Landed 2026-09-08 audit:** `399f437` — `landscape_policy` collapsed to the two values it had. `landscape_policy` `scale` and `letterbox` are **identical**: nothing branches on anything but `== "rotate"`. The GUI tooltip promises two different behaviours. Either implement letterbox or collapse the enum (the same reason `scale_mode` was deleted). | `core/layout.py:294, 385` | S |
| **B20** ✅ | **Landed 2026-09-08 audit:** `e397769` — a sheet the plan does not have is refused. `export(sheets=...)` silently skips unknown indices; a fully-unknown selection writes a 0-page PDF that passes `_verify_output`. `render_sheet` caches an empty PDF for a stale index before checking the sheet exists. | `core/export.py:589-593`, `core/render.py:246` | S |
| **B21** ✅ | **Fixed 2026-09-08.** `resolve_profile` and `select_preselected_printer` now catch `ValueError` too, so one unreadable file degrades a printer to uncalibrated rather than making `PrintDialog.__init__` raise and the dialog impossible to open. Original finding: CLI `_resolve_profile` catches `ValueError` from a *corrupt* saved profile and reports "no printer profile 'X'"; the print dialog catches only `OSError` so the same file crashes it. The CLI also tells users to "calibrate a printer in the desktop app", which does not exist. | `cli.py:420-447`, `print_dialog.py:46,78` | S |
| **B22** ✅ | **Fixed 2026-09-08.** The profile is resolved whenever it is given, not only inside the `--pass` branch, so the back offset reaches a whole-document export. `--back-offset` still overrides it, and `--pass` without `--profile` is still refused — both tested, because the guard moved. Original finding: `--profile` without `--pass` is silently ignored, including its calibrated back offset. | `cli.py:976-996` | S |
| **B38** ✅ | **Fixed 2026-09-08.** `list_resumable` promised in its docstring that it never raises, and `PrintDialog.__init__` calls it with no `try` — but only `OSError`/`JSONDecodeError` were caught and the five `data[...]` lookups sat outside the `try`. A file that is valid JSON but not a session (`{}`, a list, a string, a half-written record) parsed cleanly and raised `KeyError`, so one such file in the state directory made the print dialog impossible to open. The lookups moved inside, catching `KeyError`/`TypeError`. Found 2026-09-08. | `core/print_session.py:512-533` | XS |
| **B39** ✅ | **Fixed 2026-09-08.** A printer name was used directly as a filename: `config_dir / f"{name}.json"`. A Windows queue is routinely `\\\\server\\queue`, which is an *absolute* UNC path, so the join discarded the config directory and wrote the calibration onto the print server; `../` and `/` do the same on other platforms. Names are now percent-encoded, but only for what is actually dangerous — spaces, hyphens, dots and underscores are left alone so the directory the GUIDE sends people into stays readable, and the common name encodes to exactly what it already was. `load` falls back to the pre-encoding path so no existing calibration is orphaned. Note the Linux suite cannot reproduce the UNC case (`PurePosixPath` does not read `\\` as a separator) and the test says so rather than implying coverage it does not have. Found 2026-09-08. | `core/profiles.py:174` | XS |
| **B40** ✅ | **Fixed 2026-09-08.** A second import replaced the document instead of adding to it (`replace(project, pages=page_list)`), so a book made of a scan plus a typeset title page was impossible to assemble — while README §2 promised "PDFs and image folders, interleaved" and every layer below the import verb already delivered it. `load_and_apply_import` takes `append=`, and the import bar has an "Add to the current document" checkbox. Replacing stays the default. Found 2026-09-08. | `app/views/import_view.py:48` | S |
| **B23** | `.deckle` load swallows every warning except `PathOutsideRootsAdvisory`, so a project from a newer build loses fields silently on the CLI (`UnknownLayoutFieldsWarning` exists precisely for this). | `cli.py:507-519` | S |
| **B24** | `_layout_flags_given` reports non-layout flags (`--sheets`, `--pass`, `--profile`) as "ignored" when the source is a `.deckle`, then honours them. | `cli.py:564-588` | S |
| **B25** ✅ | **Fixed 2026-09-08.** Both halves: `cm` is accepted, and so is the space, around the `x` as well as before the unit. One existing expectation changed with it — `"8.5 x 11in"` had been sitting in the *rejects* list among genuine nonsense (`"8.5*11"`, `"letterx"`) with no reason recorded for why a legible paper size belonged there. It is now an accepted value, which is what this finding asks for. Original finding: `_parse_paper` rejects `cm` while its own error message lists it; disallows the space `_parse_length_pt` allows. | `cli.py:122-132` | S |
| **B26** | The loader stores the source path exactly as typed, so `deckle impose ./book.pdf` writes a cwd-relative path that fails from any other directory. Image-folder imports store the *temp cache file* as the source, which `evict_lru_files` can later delete out from under a saved project. | `core/loader.py:368, 589-614` | S / M |
| **B27** | Print-session state lives in `tempfile.gettempdir()`, which on Linux is tmpfs and is wiped by the reboot that follows the crash it exists to survive. `_delete_state` and `_save` are unguarded against `OSError`, and `list_resumable` raises on a valid-JSON file with missing keys, hiding every other resumable session. | `core/print_session.py:101-106, 415-422, 535-554` | S |
| **B28** | Saved-file `version` fields are written but never read for `.deckle` and profiles; `print_session` checks version *after* the state check so a newer file is misreported. `printer` is not type-checked on load. | `core/project_io.py:487-564`, `core/profiles.py:145-160` | S |
| **B29** | Paper thickness in `pt` shows 0 decimals; a caliper is 0.2-0.5 pt so it displays "0" and the next nudge writes 0. Typing a thickness never refreshes the gathering suggestion or resets the stock combo to Custom. | `layout_panel.py:1439, 1714` | S |
| **B30** | Printer list is enumerated exactly once at startup; the module docstring claims a refresh on menu open that does not exist. | `app/main.py:583` | S |
| **B31** | `loader` emits warning kind `skipped_non_image_files`, which is not in `LayoutWarning.kind`'s Literal; `landscape_imageable_unverified` is declared and never emitted. Declared-but-not-honoured values, the shape the decisions log has caught five times. | `core/loader.py:552`, `core/models.py:237-248` | S |
| **B32** | `actual_margins_pt` ignores `crop_pt`; only tests call it, which is exactly when it would mislead. `_blank_thumbnail` ignores `rotate_deg`; `ink_bbox` cache key omits dpi. | `core/layout.py:559-626`, `core/render.py:355-374, 420` | S |
| **B33** | Whole-file SHA-256 is computed while holding the pdfium lock, blocking every preview during a big import; every image's PDF bytes are held in memory before merging (~2.5 GB for 500 scans). | `core/loader.py:335-378, 576-579` | S |
| **B34** | `write_text_atomic` replaces the target with a `mkstemp` file (mode 0600), tightening a shared file to owner-only on every save; no directory fsync. Profile filenames use the raw printer name, so `\\server\printer` becomes a UNC path. | `core/paths.py:253-264`, `core/profiles.py:174` | S |
| **B35** | Miscellany: thumbnail grid keeps every raw RGBA buffer (~150 MB for 300 pages); `Link margins` / `Use printer margins` push 2-3 undo entries per click; accepting autosave recovery re-prompts on every subsequent open; replaced `AppState` leaves a live debounce timer; `schedule` hashes the document before checking the output path; `assert`-based invariants are stripped under `-O`; `sewing_stations` has no guard for a sheet shorter than twice the margin. | various | S each |

---

## 2. Maintainability

The core is genuinely well layered (pure models, enforced by
`test_core_purity`; "describe, never re-derive" in `schedule`; the preview
renders the real artefact). The rot is concentrated in three places, and
most of the HIGH bugs above are direct consequences of the first two.

| ID | Problem | Proposed seam | Size |
|---|---|---|---|
| **M1** | **`LayoutPanel` (1723 lines) maintains the same ~25 controls by hand in four places**: constructor, `refresh_from_project` block-list, `_on_unit_changed` box-list, and ~20 near-identical `_on_*_changed` handlers. B11, B12 and B29 are all "a list was updated in one place and not the others". | (1) Move the pure `set_*` mutators and `recompute_plan` (lines 65-623, Qt-free) to `app/layout_mutators.py`. (2) A `LengthSpinBox` subclass that owns its unit, cap and decimals. (3) A `_Binding(field, widget, to_model, from_model)` table the panel iterates for connect / refresh / unit-change. Expect ~1050 → ~400 lines of Qt class. | M |
| **M2** | **`_place_page` (155 lines) and `SaddleStitchStrategy.impose` (215 lines)** have grown by accretion. Scale is decided against the paper while placement is decided against the cell (B5); creep is judged against a setting the grouping overrode (B8); the user's rotation is consumed for sizing but not placement (B1). The clamped-margin arithmetic is copied three times; the four `_place_page` calls in `impose` are ten identical lines each. | A `_margins(settings)` helper; `_fitted_dims(slot, settings, cell)`; a local `_place_pair`; thread `slot.rotate_deg` into `Placement`. Do this *with* B1/B5/B8, not after. | M |
| **M3** | **`MainWindow` (1205 lines) is five things**: window layout, printer enumeration, project I/O, autosave recovery, recent files, export. | `app/printer_query.py`, `app/project_actions.py`, move `autosave_recovery_offer` (pure) into `state.py`, `_live_threads`/`stop_background_work` into `app/shutdown.py`. | M |
| **M4** | **`cli.py` (1439 lines)** interleaves seven value parsers, ~300 lines of load-or-report glue, and six commands. `source` positional is defined five times, `-o` five times, `--crop*` twice with different help. `build_parser()` is rebuilt on every project load just to introspect defaults. | `cli/values.py`, `cli/report.py`, `cli/options.py` (`_add_source_arg`, `_add_output_arg`, `_add_crop_args`), `cli/commands.py`. | M |
| **M5** | **Duplicated between CLI and app, already diverged**: paper presets (GUI has 5, CLI has 3, so a GUI-saved A3 project cannot be typed at the CLI); `LETTER_PT` defined three times; paper-weight → caliper logic with near-identical error prose in both. | One `PAPER_SIZES` and one `caliper_from_weight()` in `core/paper.py`. A parity test over the two preset tables. | S |
| **M6** | Two atomic-write implementations (`export.py:597-625` hand-rolls what `paths.atomic_output` does); `render.py` re-implements `export._sides` face ordering; the loader imports the whole rasteriser to borrow a lock. | Use `atomic_output`; expose a face-index helper from `export`; a tiny `core/pdfium_lock.py`. | S |
| **M7** | Dead / scaffold code: `try/except ImportError` fallbacks for `session_log` in two modules; `except SourceChangedWarning` in `_cmd_impose`, around a function that never raises it; `try: ... finally: pass` in `render_sheet`; `_document_loaded`/`_syncing_mode` read via `getattr` defaults instead of initialised; `schedule_saved` signal used as a general status channel. Two items the first draft listed here are **not** dead and must be kept: `submit_duplex` is F9's raw material, and `DRIVERS_IGNORING_ROTATE` is an empty allow-list whose capability is reached and tested. | Delete or rename. | S |
| **M8** | "Frozen" dataclasses carry `list` fields (mutable, unhashable) while sibling types use tuples. Five of them, not two: `SheetPlan.sheets`, `SheetPlan.warnings`, `Project.pages`, `PrintPass.sheet_order`, `PreviewFrame.warnings`. | Tuples. | S |

---

## 3. Features

### 3a. Promised in docs, not built

| ID | Feature | Promised in | What exists | Size |
|---|---|---|---|---|
| **F1** ◐ | **Picker done 2026-09-08 (B16); the editor is not.** Choosing between the presets and having the choice persist now works — `PrinterProfile.save` has its first caller in `deckle/`, guarded so it declines rather than overwriting a hand-measured calibration. Setting back offset X/Y and imageable area by hand is still unbuilt, and is the rest of this item. Original finding: **Printer profile picker + editor in the app.** Choose between the two built-in presets, set back offset X/Y and imageable area, save under the printer's name. Nothing in `deckle/` ever calls `PrinterProfile.save`; the only writer of a profile today is a human with a text editor. This is most of the calibration wizard's *value* at a fraction of its cost, and it fixes B16, B21 and makes F3 usable. | GUIDE §6, README status table | Model + save/load complete | S |
| **F2** | **Calibration wizard** (MVP sub-spec 13). The spec itself says: enumerate the 16-state space in a spike first, stop and escalate if it needs more than 5 questions. | README, CHANGELOG, GUIDE §6, hardening plan WS3 | Nothing beyond F1's model | L |
| **F3** | **Registration target you read numbers off** (competitive gaps #1, "build it with the wizard"). Today: `--rule` and three rounds of trial and error. | README, GUIDE §6 | `--back-offset`, `--rule` | M |
| **F4** | **Fold the folio dummy on paper** and record it. Gates F7, F8 and formal removal of "experimental". Cheap version is README "Check 0"; formal version (signatures v2 sub-spec 13) wants a calibrated profile first. | README, GUIDE §5, v2 index | `deckle dummy`, `fold_reading_order` | S effort, hardware |
| **F5** | **CLI parity for margins**: `--margin-top/bottom/outer`, `--slack-to`, `--start-on-verso`, `--landscape-policy`. GUIDE §8 calls this "a genuine gap". `deckle impose` always writes zero-margin projects. | GUIDE §3, §8 | All are `LayoutSettings` fields | S |
| **F6** | **Sheet-subset reprint from the GUI.** Design doc SC-10: select sheets in the preview → filtered plan. Dialog exposes only a signature selector; CLI has `--sheets`. | print-prep design | `plan_passes(sheets=)`, `export(sheets=)` | S |
| **F7** | **Quarto / octavo** (competitive gaps #3). Layout is already cell-general; missing the 2×2 grid, per-cell 180° rotation, ordering table. Gated on F4. | competitive gaps, v2 design | `Cell`, `cell_geometry`, `fold_scheme` seam | M |
| **F8** | **French fold** (competitive gaps #4): single-sided printing, no pass 2, no misregistration. `Sheet.back = None` is already representable and export handles it. Gated on F4. | competitive gaps | absent-face export | M |
| **F9** | **Hardware single-pass duplex.** `submit_duplex` is complete and tested, guarded by a test asserting nothing calls it. Two open questions recorded (does a duplexer get the back offset? Linux duplex detection). | backend, decisions 2026-08-07, QPrinter spike | complete, unwired | S + decisions |
| **F10** | **Fold simulator as a "read the book back" line** in the schedule / Signatures tab (v2 open Q5: "arguably the most reassuring thing the app could show"). | v2 design | `fold_reading_order` pure and tested | S |
| **F11** | **Custom paper size typed in the GUI.** Only appears when a project already carries one. | competitive gaps "not a gap ... unless" | `_sync_paper_choices` | S |
| **F12** | Per-signature export to separate files; per-orientation imageable area; top/bottom-edge binding; `binding_edge`→`reading_direction` split (would be the first `.deckle` migration). All recorded as open questions, none urgent. | v2 design open Qs | | S-M each |
| **F13** | **Linux packaging**, a `run.sh`, README screenshots, doc build in CI. | hardening plan, README | `run.bat` only, spec file missing (R0.3) | M |

### 3b. Not in any doc, but fit the workflow

Ranked by paper and frustration saved for one person, at home, on a
duplexer-less printer.

| ID | Feature | Why | Size |
|---|---|---|---|
| **N1** | **Autosave for never-saved projects** under `data_dir()`, keyed by source hash. The recovery design table calls the unsaved case "silent"; with B9 it is the same hole twice. | Import + arrange 200 pages + crash before first Save = total loss | S-M |
| **N2** | **Pre-fill imageable area from the driver.** Closes B15 properly. Sharper than the first draft said: the spike measured the paint rectangle *under full-page mode*, where it equals the full sheet and carries no inset. The margins exist only in the driver's standard mode, which the print backend deliberately leaves, so this needs its own `QPrinter` that is never put in full-page mode. | The red guide becomes true | S-M |
| **N3** | **Proof sheet with ruler** from the print dialog (`--sheets 0 --rule` is CLI-only). One checkbox. | The only actual-size check | S |
| **N4** | **Export a single pass PDF** from the GUI (`--pass front/back`) for people who print at a shop or on a second machine. | | S |
| **N5** | **Sewing-station positions, not just a count.** Tapes need pairs at tape width; kettle stitches sit at fixed insets; long-stitch wants a pattern. `--stations 0.5in,2in,2.25in,...`. | Physical need the int cannot express | S |
| **N6** | **Page-range selection at import** (`--pages 7-312`, or "skip range" in Arrange). | Public-domain scans carry boilerplate; skipping one page at a time is the current path | S |
| **N7** | **Menu bar + keyboard shortcuts** (Ctrl+O/S/P, Save PDF, zoom, sheet navigation, Delete/R/S in the grid). Only Undo/Redo are bound. Recent files becomes a normal submenu. | Cheap accessibility | S |
| **N8** | **Drag-and-drop import.** Nothing sets `acceptDrops`. | The most common way into an imposition tool | S |
| **N9** | **Dirty indicator + save prompts** (`setWindowModified`, "save before closing / opening?"). This is B10 seen as a feature. | | S |
| **N10** ◐ | **Rotate/skip done 2026-09-08; remove is not.** Both act on the whole selection now, through `rotate_many`/`skip_many`, which apply in a *single* `mutate` — a loop over the one-page helpers would bury forty entries in a bounded undo stack, so the single Ctrl+Z the user expects would undo one page and lose the rest of their history. Skip decides once for the selection rather than toggling each page, because toggling a mixed selection inverts it instead of resolving it. The selection also survives the refresh now, without which a multi-page gesture could not be repeated. Still open: **there is no way to remove a page, only skip it.** Original finding: **Multi-select rotate/skip/remove** in Arrange. The context menu offers it for N pages; the buttons act on `currentRow()` only. There is no way to *remove* a page, only skip. | | S |
| **N11** | **Show the crop composite in-app.** The Crop & trim tab has eight spinboxes and no picture; `crop-preview` already renders the composite. | Closes the measure-look-type loop | M |
| **N12** | **Save schedule under flat sheets.** The button lives only on the Signatures tab. The flat-sheet schedule does *not* currently carry the spine width a perfect binder cuts boards against: the number is computed for every plan, and the flat branch of the text formatter returns before printing it, along with the notes. So this is an output fix plus a button move, not a button move. | | S |
| **N13** | **About dialog + "Open diagnostics folder"**. The GUIDE tells users to attach `--version` and the JSON Lines log; the app has neither. Offline-consistent. | | S |
| **N14** | `--json` for `info`/`schedule`, `--dry-run` for `export`/`impose`, `deckle profile list/show/set`, `deckle print` (the session and pass planner are Qt-free; only the backend is Qt). | The CLI's stated purpose is scripting; today that means regex-scraping | S each |
| **N15** | Persisted default settings for new projects ("save as my default"). A `.deckle` already works as a template via `impose`. | | S |

Considered and not recommended: bookmarks/PDF-A passthrough (the output is a
print artefact of sheets), auto-update / crash reporting / telemetry (MVP
exclusions: fully offline), localisation, plugin API, cover generation
(rejected in the gaps doc), batch printing, contact sheets.

---

## 4. Documentation drift

| ID | Claim | Reality |
|---|---|---|
| **D1** | README: "608 passing, 17 skipped" | 1531 collected, 1509 passing with 22 skipped on a green tree; decisions.md recorded 1416 on 2026-08-07. The README's explanation of the skips is also wrong: 14 of the 22 are packaging-audit skips, not golden-fixture ones |
| **D2** | GUIDE §4, competitive-gaps status: "the desktop app has no crop controls at all" | False since `fa7d888` (Crop & trim tab, Measure crop from the ink) |
| **D3** | GUIDE §6: "two built-in generic presets" | The app only ever resolves the first (B16) |
| **D4** | GUIDE §3, README §4: red guide is "the printer's hardware limit" | It is the generic preset's fixed 0.25in (B15) |
| **D5** | GUIDE §8 CLI reference | Omits `crop-preview`, `dummy`, `--paper-weight/-type/-grade`, `--signatures`, `--crop*`, `--auto-crop*`, `--trim`, `--sheets`, `--rule`, `--pass`, `--back-offset`, `--profile`, and that a `.deckle` is accepted as SOURCE everywhere with its stored layout winning |
| **D6** | GUIDE has no coverage of paper-by-weight, gathering suggestion, or autosave recovery (plan Task 10 called for a section) | README and CHANGELOG got it; GUIDE did not |
| **D7** | README architecture module list | Omits `paper`, `paths`, `recent`, `schema`, `locate`, `dummy` |
| **D8** | CHANGELOG Added: "CLI — impose, export, info, --version" | `crop-preview`, `dummy`, and most export flags appear only under Fixed or not at all |
| **D9** | `tests/fixtures/README.md`: sample.pdf "checked in" | Gitignored (R0.1) |
| **D10** | `main.py` docstring: printers refresh "whenever the printer menu is opened" | No such menu (B30) |
| **D11** | GUIDE §5: "Check 0" and "Check 2" are the same check | Editorial |

---

## 5. Test gaps that would have caught the above

- Impose + export a page with `rotate_deg` 90/180/270 under every landscape policy (B1, B2).
- A landscape source under folio (B5).
- ~~Resume a print session after changing gutter / paper / crop, expect `StaleSessionError` (B3).~~ ✅ Done 2026-09-08 — gutter, margins, paper and rotation as a parametrized set, a different document with identical pagination, an end-to-end refusal through `load`, and a test that the session and the exporter cannot drift apart again. All seven fail against the old hash.
- ~~Session-side `log_print_job` raising after a successful chunk (B4).~~ ✅ Done 2026-09-08.
- ~~Mutate after Save project, expect `<path>.autosave` written (B9).~~ ✅ Done 2026-09-08, plus the Save As case.
- ~~`state.can_redo` after undoing a trim/crop change; `refresh_from_project` leaves the undo stack length unchanged (B12). `test_refreshing_never_changes_the_document_in_any_unit` should include trim and crop (B11).~~ ✅ Done 2026-09-08, both exactly as specified, plus a unit-change-then-nudge test driving B11's failure end to end. Nine tests in this file fail against the pre-fix panel.
- Two overlapping imports; import during shutdown (B13).
- ~~Cancelled resume-count prompt (B14).~~ ✅ Done 2026-09-08, with its twin: `0` must still resume.
- Schedule numbering with skipped pages or two sources (B7); `signature_lengths` + creep (B8); `sheets_per_signature <= 0` under `balanced` (B18).
- A parity test over the GUI and CLI paper-preset tables (M5); an "every emitted warning kind is declared" test (B31).
- No CLI test exercises `--crop`, `--crop-even`, `--trim`, `--binding-edge`, `--blank-mode`, `--grain`, `--signatures`, `--back-offset`, or `impose --printer`.
- `list_resumable` with a valid-JSON, missing-keys file; `_delete_state` failing; profile with a missing field or a path separator in the name; `write_text_atomic` preserving mode (B27, B28, B34).

---

## 6. A suggested order

Not a commitment; a starting point for the conversation.

1. **Unblock the tree** (R0.*): fixture, numpy, spec file, Linux chmod, `=`, one CI job. Half a day. Everything after this is checkable.
2. **Paper-ruining bugs** (B36 first, then B1-B8), done together with the layout refactor **M2** because they are the same code. Add the tests in §5 first so the refactor is pinned.
3. **Lost-work bugs** (B9-B14) together with **M1**, because B11/B12/B29 are the control-table problem and fixing them one at a time re-introduces it.
4. **F1 profile editor + N2 driver imageable area + B15/B16.** Makes the print path honest without waiting on the wizard. Decide B6 here.
5. **CLI/app parity** (M5, F5, N14) and the **cli.py split** M4.
6. **Everyday desktop features** in one pass: N7, N8, N9, N10, N3, N4, N12, N13. Each is small; together they change how the app feels.
7. **F4: fold the dummy on paper.** Nothing in 1-6 depends on it; F7 and F8 do.
8. **F7 quarto → F8 French fold**, in that order, on the cell generalisation.
9. **F2/F3 calibration wizard** as the spike the spec asks for, only if F1 turns out not to be enough.
10. Docs pass (D1-D11) at the end of each milestone rather than as its own item, or the GUIDE keeps drifting.

Open decisions this order needs from the owner:
- B6: draw the sheet 1:1 and warn on clipping, or keep scaling to the imageable area?
- B19: implement `letterbox` as distinct from `scale`, or collapse the enum?
- F9: wire hardware duplex now (with the two recorded questions answered by assumption) or leave it dark?
- B26: should image-folder imports spool the normalised PDF beside the project instead of the evicting temp cache?
- F13: is Linux a target, and if so which format?
