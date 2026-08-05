# Decision Log

## 2026-08-04 — .deckle could not survive a LayoutSettings field change
- Symptom: Red-teaming the signatures v2 spec surfaced a live data-loss bug in shipped code. `_layout_from_dict` passed every stored key straight into `LayoutSettings(**kwargs)`, so **any** field added or removed permanently broke every project file written on the other side of that change. Deleting `scale_mode` earlier the same day did exactly that: a legacy `.deckle` raises `TypeError: unexpected keyword argument 'scale_mode'` and cannot be opened at all.
- Fix: Filter to known dataclass fields, default the missing, and emit a non-fatal `UnknownLayoutFieldsWarning` for extras — tolerant in both directions. Four regression tests: retired field, future field, no spurious warning on a minimal dict, and a full round trip.
- Surfaces: The design had reasoned that no migration was needed because *"`SheetPlan` is never persisted"*. That is true and irrelevant — **`LayoutSettings` is persisted**, and it is exactly what the feature changes. Signatures v2 adds five more fields, so every v2-saved project would have been unopenable by any build without them.
- Watch: A `version` integer in a file format is not migration tolerance; it is a place to *record* a version. This one had been present since SS-01 and unused. Any `Type(**stored_dict)` is a latent break on the next field change — the constructor is a schema contract whether or not you intended one.
- Commit: (this commit)


## 2026-08-04 — Printer enumeration blocked the UI thread on launch
- Symptom: With the internet down, `python -m pytest -q` took **81 minutes** instead of 7 seconds. Same 231 tests, all passing.
- Fix: `QPrinterInfo.availablePrinters()` enumerates **network** printers, and the Windows spooler blocks per printer until it times out when one is unreachable. `MainWindow.refresh_printers()` ran synchronously inside `__init__`, so **Deckle hung on launch whenever a networked printer was offline** — measured at 21ms with the network up and effectively unbounded without it. Moved to a background `_PrinterQueryWorker`; the window now constructs in 338ms with Print disabled and a "Checking for printers..." message, enabling when the query returns. `_on_print_clicked` reads the cached list rather than re-enumerating.
- Surfaces: Any OS-level enumeration that can reach the network — printers, drives, fonts, Bluetooth. It is fast enough to look synchronous-safe on a healthy machine and unbounded on an unhealthy one.
- Watch: A **slow test suite was the only symptom**. Nothing failed, so nothing drew attention to it; the duration was the signal. Treat a large unexplained change in suite runtime as a defect report, not an environment quirk.
- Commit: (this commit)

## 2026-08-04 — Superseded background renders raced and leaked
- Symptom: `PreviewView.refresh()` and `ArrangeView.request_visible_thumbnails()` each spawned a `QThread` per user action and overwrote `self._thread` without stopping the previous one. Three consequences: the **last thread to finish won**, which is not necessarily the sheet being viewed; threads accumulated for the life of the widget, one per sheet scrubbed past; and the `cancel` token that `render_sheet` already accepted was never created or passed, so superseded renders ran to completion.
- Fix: Both workers now own a `threading.Event`, cancelled when superseded and threaded into `render_sheet`. `_on_frame_ready` ignores any worker that is not the current one. Threads get `deleteLater` on finish. Verified live: 20 rapid sheet changes leave exactly one live worker, on the sheet actually displayed.
- Surfaces: Any "fire a background job per user action" pattern. The bug is invisible when jobs are fast — it only appears under a large document, which is when it matters.
- Watch: **`ThumbnailWorker` had zero test coverage**, which is how a missing `import threading` in `arrange_view.py` passed a fully green suite. A class with no direct test can absorb a `NameError` silently as long as nothing constructs it. `tests/test_view_workers.py` now covers both workers' cancellation contract.
- Commit: (this commit)

## 2026-08-04 — slack_to replaces the maximize_gutter boolean
- Symptom: Asked for a toggle so the gutter could vary by default but be held constant on demand. A toggle already existed — `maximize_gutter` — but its two positions were "slack to the gutter" and "slack split evenly". **Neither produced a constant gutter**, which was the option actually wanted.
- Fix: Replaced the boolean with `slack_to: Literal["gutter", "outer", "split"]`, default `"gutter"`. It names the real question — when source pages differ in width, which margin absorbs the difference, and therefore which one stays identical through the book. `gutter`: fore-edge exact, gutter varies. `outer`: gutter exact, fore-edge varies. `split`: both vary, requested difference preserved.
- Surfaces: On the real Traveller distribution (cover 12.5pt narrower than the body), gutter 0.75in / fore-edge 0.25in gives inner `[0.93, 0.75, 0.75, 0.75]` under `gutter`, `[0.75, 0.75, 0.75, 0.75]` under `outer`, `[0.84, ...]` under `split`. `outer` is the one to use with a fixed punch or sewing template.
- Watch: A two-state boolean encoded a three-state question, so one third of the answer space was simply unreachable. The name also described the mechanism ("maximize") rather than the decision ("where does spare width go"), which is what hid the gap. When a setting's off-state needs a paragraph to explain, check whether it is really a boolean.
- Commit: (this commit)

## 2026-08-04 — Uniform document-wide scale; per-page scaling resized the text
- Symptom: Asked why, with gutter 0, the left preview sheet showed a gutter and the right did not — and guessed correctly that it was a page-size difference. It was: the Traveller cover is 506.88pt wide against 519.36pt for the body, so at the same height the cover left 14.6pt of slack while the body pages had none, and `maximize_gutter` put all of it on the cover's spine.
- Fix: The far more consequential problem the question exposed was that **every page was being scaled independently**. Harmless at gutter 0 (all scales landed within 0.02%), but with a 0.75in gutter it made the cover's text **2.5% larger than the body's** — a real defect in a bound book. Added `document_scale()`, computing one scale for the whole document (the largest that fits every page) and applying it to all of them. Verified across all 266 pages: exactly one distinct content scale.
- Surfaces: Page-box survey of the real book — 264 pages at 519.36x672, one cover at 506.88, one at 527.28; a 0.283in spread. Only 2 of 266 pages are outliers, which is exactly why per-page scaling looked fine until someone set a gutter.
- Watch: This is **not** a reversal of the predecessor script's defect fix. That bug applied page 0's *aspect ratio* to every page's geometry. Per-page **geometry** remains correct — each page's own media box still determines its scaled size, slack and placement. Only the **scale factor** is shared. Conflating the two is easy and the tests now name the distinction explicitly.
- Also: a test asserting one distinct scale failed because filler pages carry a neutral `scale_x=1.0`. Fillers are blank and have no content to size; assertions over "the document's scales" must exclude them.
- Commit: (this commit)

## 2026-08-04 — Two preview guides, and maximize_gutter
- Symptom: Asked what the black lines were and why content would not align with them. They are the printer's **imageable area** — a hardware limit — while content is positioned by the **gutter and margins**, a different rectangle. The two coincide only when every margin happens to equal the printer's inset, so the question was reasonable and the preview was answering a different one. Separately: with a small gutter the content looked centred rather than pushed off the spine.
- Fix: The preview now draws BOTH rectangles distinctly — solid red for the imageable area, dashed blue for the content box (`content_box_rect_pt`, which mirrors between recto and verso). Added `LayoutSettings.maximize_gutter`, defaulting **on**: all horizontal slack goes to the spine, so the fore-edge margin is exact and `gutter_pt` becomes a minimum. Off restores equal sharing. Vertical slack is always shared — neither head nor tail has a binding to accommodate.
- Surfaces: Content can only touch the content box on whichever axis binds; on the other it is inset by the aspect-ratio slack. Traveller's source aspect is 0.754 against a box aspect of 0.714, so width binds and there is 40pt of vertical slack. It cannot touch all four edges, and no setting will make it.
- Watch: A real consequence of maximize_gutter — with a zero fore-edge margin, changing the gutter moves **nothing** until it is large enough to force a rescale, because content is already flush against the fore-edge. `test_export_sheet_cached_invalidates_on_layout_change` failed on exactly this: the two plans were genuinely identical, so the cache was right not to invalidate. The test's premise (any gutter change alters the plan) was what had become false.
- Commit: (this commit)

## 2026-08-04 — Deleted the scale mode; there is one scale rule
- Symptom: Asked "can't fill height just scale down until the book doesn't clip?" — and the answer is yes, but that operation *is* `fit`. Making `fill_height` scale down would make the two modes byte-for-byte identical, leaving a control that changes nothing.
- Fix: Removed `LayoutSettings.scale_mode`, `SCALE_MODES`, `set_scale_mode`, the CLI `--scale-mode` flag and the radio buttons. Content is always scaled to the largest size fitting the content box in both dimensions — which fills the page height whenever height is the binding constraint, and scales down when width is. `fill_height`'s only distinct behaviour was overflowing the page, i.e. producing output that cannot be printed.
- Surfaces: Concretely for the Traveller book — at full letter height content is 597.4pt wide, and the widest box achievable is 558pt even with a **zero** outer margin (612 − 54 gutter). It is 39pt too wide no matter what; scaling down is the only option, so the mode offered a choice that was never real.
- Watch: The mode predated the margin model. It existed because "fill the height" was once the only way to get a large page; once four margins and a fit rule existed it was redundant, but it survived because it had tests and a UI control. A feature having tests is not evidence it should exist.
- Commit: (this commit)

## 2026-08-04 — Rebuilt the placement math on one rule for both axes
- Symptom: Reported from use — "the math is all over the place", height mode leaving no gutter and fixed mode misbehaving. Correct diagnosis: the horizontal axis **anchored** to the gutter (all slack to the fore-edge) while the vertical axis **centred** (slack split). Two different rules in one function, so identical inputs behaved differently per axis and neither matched intuition.
- Fix: One rule, both axes. **Margins are minimums**; spare space inside the content box is shared equally between opposing margins so their difference is preserved exactly; when content *overflows* there is no slack to share, so the specified margin is held and the overflow lands on the opposite edge — the gutter is never eaten by content that does not fit. Expressed as `offset = max(0, slack) / 2` applied identically to x and y. Added `actual_margins_pt()` returning measured `(inner, outer, top, bottom)` so tests and UI read the same numbers the exporter uses.
- Also renamed the modes: `fixed_gutter`→**`fit`** (fits both dimensions, never clips, now the default) and `fit_height`→**`fill_height`** (fills box height, may overflow). The old names described implementation, and the old default could silently push content off the page.
- Surfaces: Verified on the real Traveller Core Rulebook — `fit` yields inner 0.750in and outer 0.250in exactly as requested, with leftover shared top/bottom; `fill_height` holds the gutter and reports the fore-edge overflow rather than hiding it.
- Watch: The old suite asserted raw `tx`/`ty` on one edge of one page, which is how a verso-only bug survived it. The rewritten suite (49 tests) measures **all four margins of both sides**, parametrised over 4 aspect ratios x 2 binding edges x 2 modes, plus degenerate cases (margins exceeding the sheet, negative margins, zero gutter). Assert measured margins, not coordinates.
- Commit: (this commit)

## 2026-08-04 — The gutter was derived from leftover width, not from the setting
- Symptom: Reported from use — "gutter ends up with extra from margin". In `fit_height`, `gutter = paper_w - margin - scaled_w`, so the gutter was whatever width happened to remain. Asking for 0.75in on the Traveller page box silently produced 1.17in; `gutter_pt` was effectively ignored in that mode.
- Fix: The gutter is now honoured exactly in both modes and any slack lands on the **fore-edge**, never the spine. Also split the single `margin_pt` into `margin_top_pt` / `margin_bottom_pt` / `margin_outer_pt` — with `gutter_pt` as the inner margin, that describes all four page edges — plus a persisted `margins_linked` flag driving a "Link margins" toggle in the panel.
- Surfaces: The consequence is that `fit_height` can now genuinely overflow. Traveller content at full letter height is ~597pt wide; with a 54pt gutter it needs 651pt on a 612pt sheet, so the fore-edge goes to −39.4pt and the detector reports `clipped_by_page`. That is the honest outcome — the old behaviour hid an impossible request by quietly shrinking the gutter.
- Watch: Head and tail are **minimums**, not exact values — content centres in the vertical box, so when width limits the scale both grow by an equal share of the slack. The invariant to assert is their *difference*, not either value. A test asserting `tail == 9.0` failed for that reason and was wrong, not the code.
- Commit: (this commit)

## 2026-08-04 — Save PDF, spread preview, and verification against a real 266-page book
- Symptom: The app could only print, and the preview showed one side at a time — so checking that the gutter mirrored correctly meant toggling Front/Back and holding two images in your head.
- Fix: Added a "Save PDF..." button wired to the existing `export()` (SS-04), sharing the exact `SheetPlan` the preview is showing, and enabled independently of Print so it works with no printer. Default filename is `<source>-deckle.pdf`, never the source name, so a careless Save cannot overwrite the input. Added a "Both" toggle rendering front and back side by side; both halves come from one background pass so a stale front can never appear beside a fresh back, and both go through the same `_frame_pixmap` so the imageable-area guide cannot drift between views.
- Surfaces: Verified against the real Traveller Core Rulebook (266pp, 172MB). Exported placement matrices confirm the mirror: recto `1.06534 0 0 1.06534 54 38.045 cm`, verso `1.03974 0 0 1.03974 18 46.647 cm`. The differing scales are correct — those source pages are 506.88pt and 519.36pt wide, so this is the per-page-aspect fix (defect 1 of the predecessor script) working on real data.
- Watch: Inspecting the exported PDF hit the documented pikepdf trap — `page.Contents` is an Array of streams, so `read_bytes()` raises "operation for stream attempted on object of type array" until `contents_coalesce()` runs. The trap is real and it bites tooling, not just application code.
- Commit: (this commit)

## 2026-08-04 — imageable_area_pt is margins, not a rect
- Symptom: "Use printer margins" set a 3in margin from a 0.25in printer border. The value was 216pt — exactly the margin spinbox's cap, so a nonsense number had been silently clamped into a plausible-looking one.
- Fix: `imageable_area_pt` is `(left, top, right, bottom)` **margins** from the paper edges — the convention `PrinterProfile`, `QtPrintBackend._paint_rendered_page` and `preview_view.imageable_rect_pt` all already used correctly. The new `imageable_inset_pt` helper read it as an `(x0, y0, x1, y1)` rect and computed `612 - 18 = 594`. Now `max(*imageable_area_pt, 0.0)`, with three regression tests pinning the convention.
- Surfaces: Any four-float geometry field. `(left, top, right, bottom)` and `(x0, y0, x1, y1)` are indistinguishable by type, both plausible, and a wrong reading produces large-but-not-obviously-invalid numbers rather than an error.
- Watch: The spinbox `setRange` cap turned a 594pt bug into a 216pt value that looked like a deliberate setting. Clamping hid the defect. When a computed value lands exactly on a range bound, suspect the computation before the bound.
- Commit: (this commit)

## 2026-08-04 — Added a margin concept; content had no head or tail margin at all
- Symptom: Real-world test (Traveller Core Rulebook, 506.88x672pt on letter) reported `clipped_by_imageable_area` on both sides of every sheet. Correct: scaled to letter height the content is 597x792 — literally edge-to-edge vertically, 0pt head and tail. No printer can mark there, so the output would have been unusable.
- Fix: Added `LayoutSettings.margin_pt` — a uniform margin on the three non-spine edges (head, tail, fore-edge); the gutter still owns the spine side. `fit_height` now fits the *content box* height rather than the paper height, and `ty` centres within the margin box, so a margin actually shrinks content instead of being averaged away. Defaults to 0.0, preserving previous behaviour and all 161 existing tests.
- Surfaces: The layout model only ever expressed one inset (the gutter). Any binding style needs at least two — spine and fore-edge — and printing needs head/tail as well.
- Watch: The imposer is pure and never sees the `PrinterProfile`, so it cannot infer the printer's dead border itself. "Use printer margins" bridges that deliberately in the UI layer rather than leaking the profile into the core.
- Commit: (this commit)

## 2026-08-04 — fixed_gutter put the reserved gutter on the wrong side of the verso
- Symptom: Switching from `fit_height` to `fixed_gutter` in the UI looked like the binding edge flipped. Reported from real use, not caught by any test.
- Fix: `_place_page` computed `tx = gutter if gutter_on_left else 0.0`. The bare `0.0` silently assumes the scaled content exactly fills `paper_w - gutter` — true in `fit_height`, where the gutter *is* the leftover, but false in `fixed_gutter` whenever height is the binding constraint. With a 6x9in source on letter and a 0.75in gutter: recto got left=54/right=30 while verso got left=0/right=84, so the verso's spine gutter was 84pt and its content sat flush against the fore-edge. Now `tx = paper_w - gutter - scaled_w` on the gutter-right side, which reduces to 0.0 in `fit_height` so one rule serves both modes. Three regression tests added, including a right-binding mirror check.
- Surfaces: Any two-mode geometry where one mode's invariant ("the leftover IS the gutter") is quietly relied on by shared downstream code. The shared line was correct for the mode it was written against and wrong for the other.
- Watch: The existing parity tests all asserted `tx` on the *gutter-left* side, where both modes agree. A bug living entirely on the mirror side survived them. When testing a mirrored layout, assert both margins of both sides, not one coordinate.
- Commit: (this commit)

## 2026-08-04 — Added run.bat; verified the GUI genuinely launches
- Symptom: The app had never actually been run. 158 tests passed and an AST test proved every view is instantiated and mounted, but no one had started the Qt event loop — so "it works" was inference, not observation.
- Fix: Added `run.bat` (GUI / `cli` passthrough / `test` / `deps` / `doctor`) and verified a real windowed launch: title "Deckle", visible, 292x631, event loop exits rc=0. `doctor` also enumerates printers through `QPrinterInfo`, which is the assumption SS-08's spike rests on — 7 printers resolve, including the Brother HL-L2350DW.
- Surfaces: Any headless verification of a GUI app. `QT_QPA_PLATFORM=offscreen` construct-then-`show()` reproducibly kills the process here (exit 127) while the identical code succeeds on a real display — a platform-plugin artifact, not an application defect. A second agent hit the same wall independently and worked around it with AST analysis.
- Watch: Do not treat an offscreen crash as evidence of a broken app, and do not treat passing tests as evidence that an app launches. Two batch-file traps also cost a cycle: `%` inside an inline `python -c` gets mangled by cmd escaping (use `.ljust()` / f-strings, never `%` formatting), and cmd does not search the current directory, so the script must be invoked as `.\run.bat`.
- Commit: (this commit)

## 2026-08-04 — Path-traversal validation must advise, not refuse
- Symptom: The red-team A-3 fix made `load_project` raise `PathOutsideRootsWarning` unconditionally for any source path outside the project directory. Three `test_app_state.py` autosave tests failed immediately — but the real damage was larger: Deckle's normal case is source PDFs living in Downloads or a sync folder, not beside the `.deckle` file, so **every real project would have hard-failed on reopen**.
- Fix: The spec says "prompts for confirmation rather than opening silently" — a refusal is not a prompt. Split into `PathOutsideRootsAdvisory(UserWarning)` (visible, non-fatal, emitted when no decision-maker is available) and an `on_outside_roots` callback that lets a UI veto, raising `PathOutsideRootsWarning` on decline. Three tests added: advisory, veto/approve, and `..` traversal resolution.
- Surfaces: Any security-flavoured acceptance criterion whose remedy is "confirm" or "warn". Implementing it as a hard raise is the easy reading and the wrong one — it converts a usability affordance into an outage. Check the criterion's verb before choosing the mechanism.
- Watch: A security fix that makes the primary workflow fail is almost always over-implemented. If a validation rejects the common case rather than the crafted one, the threshold is wrong.
- Commit: (this commit)

## 2026-08-04 — Patching a master spec after prep desynchronizes the phase-spec bundle
- Symptom: Converge pass 1 found four confirmed gaps (`SourceMissingError`, session-log rotation, `--version`, gutter-parser `cm`/bare-number). All four were red-team advisories A-4/A-5/A-6/A-9. The code was not wrong — the workers had faithfully implemented the phase specs, which never contained those advisories.
- Fix: Implemented all four in converge pass 1. The structural lesson is ordering: red-team patched the *master* spec after `/forge-prep` had already expanded the *phase* specs, and the factory dispatches from phase specs.
- Surfaces: Any forge chain where red-team (or a manual edit) touches the master spec after prep has run. The bundle silently keeps the pre-patch requirements; nothing errors, the work just quietly omits the fixes.
- Watch: If a master spec is edited post-prep, either re-run `/forge-prep` or expect converge to surface the delta as gaps. The factory's own `--trust-anyway` flag exists because it can detect this drift — treat that flag as a warning sign, not a convenience.
- Commit: (this commit)

## 2026-08-04 — Negative-assertion acceptance criteria must exit 0 when the pattern is absent
- Symptom: Factory run `c46e15e3` deferred SS-05 and SS-06 with `idempotency-strong-build-gate: grep -n "Placement" deckle/core/render.py [real_failure]`, despite both having correct, complete implementations on disk. The cascade blocked SS-07 through SS-14 — seven sub-specs that never dispatched.
- Fix: Ten acceptance criteria in the master spec were written as a bare `` `grep ...` `` followed by the prose "returns nothing". `grep` exits 1 when it finds no match, so the build gate read a *passing* assertion as a real failure. Rewrote all ten as `! grep ... || (echo "FAIL: forbidden pattern present" && exit 1)`, which exits 0 when the forbidden pattern is absent. Commit `d296d01`.
- Surfaces: Any spec authored by `/forge` or patched by `/forge-red-team` that expresses a *negative* structural assertion ("X must not appear in Y"). The phase-spec Checks tables already used the correct negated form — only the master-spec ACs were wrong, and the factory extracts from the master. SS-02 and SS-04 carried the same defect and happened to survive it, so passing runs are not evidence of correctness here.
- Watch: Any `[MECHANICAL]` criterion whose prose says "returns nothing", "is absent", "does not appear", or "must not contain" while the backticked command is a bare `grep`. Prefer `! grep -q ... || (echo FAIL && exit 1)`. Verify by running the command standalone and checking `$?` is 0 on the *passing* case.
- Commit: d296d01

## 2026-08-04 — Worker success without commit leaves verified code stranded
- Symptom: After fixing the gate defect, retrying SS-05 and SS-06 with `--rebuild-prompt` produced `worker-success-without-commit` and a second downgrade to `deferred_manual`. Dispatch evidence reported the declared files as "Missing" on the canonical branch — true of git (untracked) but not of disk, where the previous run had already written them.
- Fix: Committed the existing worker output under the factory's per-sub-spec tag so the commit-coverage gate can see it, after verifying the code independently: 63/63 tests pass and both previously-failing negative assertions now hold.
- Surfaces: Any retry of a sub-spec whose prior dispatch wrote files but failed a gate before committing. The worker sees correct code already present, has nothing to write, reports success, and never reaches its commit step — so the gate that failed the first time fails again for a different reason.
- Watch: `worker-success-without-commit` immediately following a gate-failure deferral. Check `git status` for untracked files matching the sub-spec's declared Files list before assuming the worker did nothing. Run the test suite before committing stranded output — do not assume it is good just because it exists.
- Commit: (this commit)

## 2026-08-04 — SS-06 stranded output committed (same root cause as SS-05)
- Symptom: `deckle/core/printing.py`, `deckle/core/profiles.py`, and `tests/test_printing.py` sat untracked after two dispatch attempts — first deferred by the negative-assertion gate defect, then by `worker-success-without-commit`.
- Fix: Committed under the SS-06 factory tag after verifying independently — 63/63 tests pass and `! grep -rn "PySide6" deckle/core/printing.py deckle/core/profiles.py` exits 0.
- Surfaces: Identical to the SS-05 entry above; both sub-specs failed the same gate in the same run for the same reason.
- Watch: The decision-log hook appends a fresh scaffold on every commit *attempt*, so a blocked multi-commit recovery accumulates unfilled scaffolds that each block the next commit. Two traps: strip stale scaffolds before retrying, and never write the hook's placeholder token literally in prose — the hook string-matches it and will block on your own documentation.
- Commit: (this commit)


## 2026-08-04 - SS-12 stranded output committed; crash dump untracked
- Symptom: The factory's PHASE-CLOSER deferred on E_COMMIT_COVERAGE_VIOLATION because SS-12 produced zero commits. Its work was on disk and correct -- tests/test_integration_signatures.py passes 3/3 -- but the worker's own check command interpolated an empty TMPDIR, so it never reached its commit step.
- Fix: Verified the stranded output independently, then committed it under the SS-12 factory tag. Also ran `git rm --cached bash.exe.stackdump`: SS-01's worker force-added a Cygwin crash dump even though `*.stackdump` has been in .gitignore since line 31.
- Surfaces: Any check command that interpolates an environment variable the worker's shell does not define. Git Bash on Windows leaves TMPDIR empty, so "$TMPDIR/out.pdf" resolves to /out.pdf -- an unwritable MSYS root -- and the command hangs rather than failing loudly.
- Watch: Check commands referencing $TMPDIR, $TMP, or $HOME subpaths. Prefer a repo-relative path under a .gitignored scratch dir, which exists identically on every platform. Also audit `git status` for tracked files that .gitignore already covers -- ignore rules do not apply retroactively to files a worker force-added.
- Commit: (this commit)

## 2026-08-04 - SS-02's Side refactor was half-applied; the default path was broken
- Symptom: 14 tests failed with `AttributeError: 'OutputPage' object has no attribute 'pages'` from export.py:301. The obvious reading -- stale test fixtures -- was wrong for 10 of them.
- Fix: `GutterShiftStrategy.impose` (layout.py:446) still emitted `Sheet(front=<OutputPage>)`; only `SaddleStitchStrategy` (layout.py:707) wrapped sides in `Side(...)`. Wrapped the gutter-shift sides, then migrated the three consumers that treated a side as a page: `flat_output_pages`, test_layout.py:469, test_export.py:158. 318 passed / 3 skipped / 0 failed.
- Surfaces: `fold_scheme="none"` -- the default, and every existing user's path. Every export, preview render and print went through the broken branch. The saddle path worked because it was the one the feature author was looking at.
- Watch: SS-02 called its own change "atomic across layout.py, export.py and render.py" and predicted this exact tree state, then was marked complete having done half of it. When a sub-spec argues for exceeding its file budget because a change cannot be split, verify every named file actually moved -- the argument is evidence the author knew the risk, not that they discharged it. Tests passing is not evidence here: they asserted the un-migrated shape, so the green suite was measuring the bug.
- Commit: (this commit)

## 2026-08-04 - Proved refactor neutrality against main when the golden fixture was absent
- Symptom: SS-02 names the Pinebox golden as its own proof of zero behaviour change, but that fixture is not in the repo -- it is the suite's 3 skips. 318 passing tests are not the byte comparison the sub-spec asked for.
- Fix: Built the comparison the sub-spec wanted out of what was available. A `git worktree` of main, the same PDF exported through both trees, then compared every page's MediaBox and every `cm` matrix from the content streams. Identical across 4 settings on the sample fixture and 2 on the 300-page mixed-size Traveller book.
- Surfaces: Any refactor claiming behavioural neutrality when its nominated golden is missing. Also the zero-diff seam claim, which `git diff --stat main -- deckle/core/printing.py deckle/core/profiles.py` settles directly and more convincingly than the spec's grep guard.
- Watch: Do not byte-compare PDFs -- creation timestamps and document IDs differ on every run, so identical documents have different hashes. Compare MediaBox plus the ordered `cm` operators. And prefer a mixed-page-size source: uniform pages cannot reveal a per-page-vs-per-document scale regression, which is precisely the defect `document_scale` exists to prevent.
- Commit: (this commit)
