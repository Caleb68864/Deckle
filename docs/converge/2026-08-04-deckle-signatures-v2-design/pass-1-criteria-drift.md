# Pass 1 finding — criterion-name drift (systemic)

**Severity:** high. This is the single largest convergence gap in the run, and it
is invisible to the test suite.

## The finding

The phase specs embed 73 distinct `pytest -k <selector>` acceptance-criterion
commands. **49 of them match no collected test.** `pytest -k` exits **5** when a
selector matches nothing, and every one of those criteria is wrapped in
`... || (echo "FAIL: ..." && exit 1)`, so each fails on execution.

Sampled and confirmed by running them:

| Prescribed selector | Real exit |
|---|---|
| `saddle_order_is_a_permutation` | 5 |
| `saddle_order_pinned_for_eight_pages` | 5 |
| `outermost_sheet_carries_last_and_first_page` | 5 |
| `fold_reading_order_works_with_saddle_order_disabled` | 5 |
| `scale_drift` | 5 |
| `never_rotates` | 5 |

## Why it is not a functionality gap

In most cases the behaviour **is** implemented and **is** tested — the worker
simply chose its own test name. `tests/test_signatures.py` contains:

| Spec selector | Actual test |
|---|---|
| `saddle_order_is_a_permutation` | `test_saddle_order_is_permutation_for_multiples_of_4` |
| `saddle_order_pinned_for_eight_pages` | `test_saddle_order_pinned_against_vault_note_n8` |
| `saddle_order_rejects_non_multiples_of_four` | `test_saddle_order_raises_on_non_multiple_of_4` |
| `outermost_sheet_carries_last_and_first_page` | `test_saddle_order_outermost_sheet_carries_first_and_last_page` |

Note the last row: the spec says *last and first*, the test says *first and last*.
Same property, reversed phrasing — which is exactly why a substring selector
could never match.

## Why it matters anyway

1. **The spec is no longer self-verifying.** Its own stated verification
   procedure fails on a correct codebase. Anyone re-running these criteria — a
   future converge, a release check, a new contributor — reads 49 red FAILs and
   cannot tell drift from regression.
2. **It proves those criteria were never executed.** The factory marked these
   sub-specs `complete`. A criterion that exits 5 cannot have been run and
   passed. So completion was asserted by code-reading, precisely the failure mode
   the `[MECHANICAL]` tag exists to prevent.
3. **It hides real absences among the noise.** At least one selector is missing
   because the test genuinely does not exist:
   `hash_plan_distinguishes_absent_side_from_present_side` (SS-04 criterion 3).
   SS-04 is marked `complete`. Its sibling criterion,
   `hash_plan_differs_for_different_page_orderings`, *does* exist but passes
   **vacuously** — it builds old-shape `Sheet(front=<OutputPage>)` fixtures, so it
   never exercises a `Side` and never touches the code path REQ-014 is about.

## Resolution rule adopted

Not a blanket rename, and not a blanket spec rewrite. Per selector:

- **Behaviour covered by an existing, non-vacuous test** → update the spec's
  check command to the real test name. The name usually carries more information
  than the spec's guess (`pinned_against_vault_note_n8` names its source of
  truth; `pinned_for_eight_pages` does not). Keep the better name; make the
  criterion executable.
- **Behaviour not covered, or covered only vacuously** → write the test, under
  the spec's prescribed name. Do not touch the spec.

The distinguishing question for each is: *does an existing test actually assert
this property against the current model?* Only that answer decides which side
moves.
