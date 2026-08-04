# Converge Pass 1 — standard

**Mode:** standard (4 parallel scanners, sliced by sub-spec)
**References:** `docs/specs/2026-08-04-deckle-mvp.md`, `docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md`
**Base:** `main` (55 files, +7,821 lines in the change slice)
**Result:** 8 gaps → all closed. `clean_streak = 0 → 1` pending re-scan.

## Scanner verdicts vs. actual

| Slice | Self-reported | Actual | Note |
|---|---|---|---|
| SS-01–03 | CLEAN | CLEAN | 33 criteria, all Met by execution |
| SS-04–06 | CLEAN | **1 gap** | Overrode: its own table scored SS-04 memory `Partial (unverified)` |
| SS-07–09 | 4 gaps | **4 gaps** | Accurate |
| SS-10–14 | CLEAN | **3 gaps** | Overrode: 3 rows scored `Partial (unverified)` |

**Two of four scanners reported CLEAN while their own tables carried
`Partial (unverified)` rows.** Under the skill's rules a Partial is a gap. Accepting
those verdicts would have opened a false clean streak — the exact failure the
adversarial pass exists to catch, except it would have been catching an
orchestration error rather than a code defect.

## Root cause of the four real gaps

A-4, A-5, A-6 and A-9 were red-team advisories patched into the **master spec after
`/forge-prep` had already generated the phase specs**. The factory workers built from
the phase specs, so the code faithfully implements what it was given — it simply never
received the last round of fixes.

**Process lesson: patching a master spec post-prep silently desynchronizes the bundle
unless prep is re-run.** This is the drift converge exists to find, and it found it.

## Gaps and resolutions

| # | Gap | Origin | Resolution |
|---|---|---|---|
| 1 | `SourceMissingError` + relocate absent; moved files raised `SourceChangedWarning` | A-4 | Added with `expected_path` + `relocate()`; `SourceChangedWarning` now reserved for hash mismatch on existing files |
| 2 | Session log unbounded append | A-5 | `RotatingFileHandler`, 5 MB cap, 3 backups, stdlib only |
| 3 | `--version` absent (exited 2) | A-6 | Resolves app + pikepdf/pypdfium2/img2pdf/PySide6 via `importlib.metadata`; no `deckle.app` import |
| 4 | Gutter parser lacked `cm` and bare-number | A-9 | Unit optional, `cm` added. Verified `'18'`→18.0, `'5cm'`→141.732, `'0.75in'`→54.0, `'3 mm'`→8.504 |
| 5 | 500-page memory criterion never asserted | A-8 | Real peak-RSS sampling via `psutil`; measured ~1.2× against the 4× bound |
| 6 | img2pdf cache unbounded | A-7 | 2 GB cap + LRU eviction, tested with an injected small cap |
| 7 | SS-10 gutter re-render / sheet-12 modal | — | **False positives** — assertions already existed at `test_preview_fidelity.py:212-230` and `:165-177` |
| 8 | SS-14 launch reachability proven only by bare import | — | AST-based test proving `MainWindow.__init__` instantiates *and* mounts every view, plus `PrintDialog` wired to the print button |

## Regression caught and fixed within the pass

The A-3 fix (`.deckle` path traversal) initially raised `PathOutsideRootsWarning`
**unconditionally** on any source outside the project directory. That broke three
`test_app_state.py` autosave tests — and, more importantly, **would have broken
Deckle's primary use case**: source PDFs normally live in Downloads or a sync folder,
not beside the `.deckle` file, so every real project would have hard-failed on reopen.

The spec says *"prompts for confirmation rather than opening silently."* A refusal is
not a prompt. Reworked to:

- `PathOutsideRootsAdvisory(UserWarning)` — visible, non-fatal, emitted when no
  decision-maker is available.
- `on_outside_roots` callback — a UI that wants a real confirmation prompt gets a veto,
  raising `PathOutsideRootsWarning` on decline.

Three tests added covering advisory, veto/approve, and `..` traversal resolution.

## Orchestration error to avoid repeating

`deckle/core/project_io.py` was handed to **two concurrent fix agents**, violating the
skill's "parallel only where target files are disjoint" rule. They happened not to
clobber each other — the SS-07 agent noticed the other's code mid-edit and layered
around it — but that was luck, not design. Slice fix agents by file, not by topic.

## Verification

`python -m pytest -q` → **158 passed, 3 skipped, 0 failed**

Remaining warnings are `PathOutsideRootsAdvisory` raised legitimately by autosave tests
using temp paths — the mechanism working as intended, not noise.

## Frozen

**SS-13 (calibration wizard)** — `requires-human-review`, excluded from the clean-streak
calculation. Genuinely unbuilt rather than drifted: it needs a physical printer and a
human reading marks off paper, so no agent can produce verifiable work here.
