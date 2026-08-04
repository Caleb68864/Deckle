# Decision Log

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
