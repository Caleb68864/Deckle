---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
contracts: "./contracts.yaml"
sub_spec_id: SS-04
sub_spec_number: 4
title: "_hash_plan content-awareness, and the zero-diff seam assertion"
depends_on: ["SS-01", "SS-02"]
date: 2026-08-04
---

# SS-04 — `_hash_plan` content-awareness, and the zero-diff seam assertion

## Context

**What this does, in two halves.**

*Half one — a latent MVP defect, surfaced by folio.* `deckle/core/print_session.py:73-86`
hashes a plan on sheet index and side **presence** only:

```python
[{"index": s.index, "front": s.front is not None, "back": s.back is not None} for s in plan.sheets]
```

Two different page orderings over the same sheet count therefore hash identically. A
`PrintSession` persists that hash (`print_session.py:175`, `print_session.py:185`) and a
resumed session could bind to a document whose content has changed underneath it — the user
reloads the stack and prints the wrong backs onto the right fronts. This is evaluation finding
I-1. It is latent in the MVP because a 1-up sheet carries one page per side; folio doubles the
content behind each hash and makes reorderings routine, because saddle imposition *is* a
reordering.

This is a **deliberate, scoped exception** to the zero-diff criterion. `print_session.py`
changes in **exactly one place** — `_hash_plan` — and `PrintSession`'s public surface and
behaviour are unchanged. Anything beyond that one function is an escalation.

*Half two — the design's central claim, made falsifiable.* The master spec's Intent ranks
"the seam holds" third and states that a diff in `deckle/core/printing.py` or
`deckle/core/profiles.py` "means the design's central claim failed and is an escalation, not a
fix". SS-04 installs the automated proof: a SHA-256 pin test, not an inspection. `contracts.yaml`
records this seam as `frozen: true`.

**The verified fact the whole zero-diff claim rests on.** `plan_passes` reads **only**
`Sheet.index`. Its sole `Sheet` attribute access is `deckle/core/printing.py:121`:

```python
indices = [s.index for s in plan.sheets] if sheets is None else list(sheets)
```

Everything after that line (`printing.py:123-141`) operates on plain `int` sheet indices and on
`PrinterProfile` fields. `Sheet.index` is untouched by SS-01, so the `Side` refactor cannot
reach `printing.py`. Confirmed by reading the file, not inferred. This is also what makes
REQ-035 true without a new branch: per-signature printing is
`plan_passes(plan, profile, sheets=sig.sheet_indices)` through the **existing** subset path.

**Why the SS-01 invariant is load-bearing here specifically.** `_hash_plan` keeps
`s.front is not None` presence in the payload alongside the new page-index tuple. If an absent
side could be spelled `Side(pages=())`, presence would be `True` and the page tuple would be
`[]` — indistinguishable from a legitimately empty side, and the hash would be wrong in exactly
the direction it must not be. `Side.__post_init__`'s `ValueError` (SS-01) is what keeps this
sub-spec's correctness claim true, so a check for it appears in this Checks table too.

**Contracts this sub-spec owns.** `manual_duplex_seam` (`contracts.yaml`, owner SS-04,
`frozen: true`): `printing.py` and `profiles.py` zero-diff; `print_session.py` changes in
exactly one place.

**One extra file, justified.** `tests/test_print_session.py:24-29` builds
`Sheet(index=i, front=_blank_output_page(), back=_blank_output_page())` and must be wrapped in
`Side(...)`, because the new `_hash_plan` reads `side.pages`. `tests/test_print_dialog.py:41-50`
has the identical fixture and is assigned to **SS-02** (its phase spec carries the
justification), so it is already wrapped by the time SS-04 runs. If it is not — if SS-04 finds
`tests/test_print_dialog.py` red on `AttributeError: 'OutputPage' object has no attribute
'pages'` — wrap it here and note the ordering slip; do not weaken `_hash_plan` to accommodate
it.

**Decision-log entries that bind here.** *Negative-assertion acceptance criteria must exit 0
when the pattern is absent* — every negative check below was executed against the tree and
confirmed to exit 0. *Worker success without commit leaves verified code stranded* — the SHA-256
pins in `tests/test_seam_zero_diff.py` are computed **from disk at implementation time**; the
values below are the ones on disk at commit `b54194c` and should match exactly unless something
already went wrong.

**Files (modify):**
- `deckle/core/print_session.py`
- `tests/test_print_session.py`

**Files (new):**
- `tests/test_seam_zero_diff.py`

## Provides / Requires

**Provides:**

| Symbol | Module | Consumed by |
|---|---|---|
| `print_session._hash_plan` (content-aware) | `deckle/core/print_session.py` | SS-12 (print dialog constructs sessions), SS-13 |
| `tests/test_seam_zero_diff.py` SHA-256 pins | `tests/test_seam_zero_diff.py` | every subsequent sub-spec — it is the standing regression guard on the seam |
| `PrintSession` public surface, asserted unchanged | `deckle/core/print_session.py` | SS-11, SS-12 |
| `plan_passes(plan, profile, sheets=sig.sheet_indices)` per-signature path, unchanged | `deckle/core/printing.py` (zero diff) | SS-12 (signature selector), SS-13 |

**Requires:**

| Symbol | Owner | Why |
|---|---|---|
| `Side` with non-empty `pages` | SS-01 | `_hash_plan` reads `side.pages`; the non-empty invariant is what makes presence + page-tuple unambiguous |
| `Sheet.front`/`back: Side \| None` | SS-01 | the payload keeps presence and adds content |
| `OutputPage.source_ref` (`SourceRef \| None`) | MVP SS-01 (`models.py:49-55`) | a filler's `source_ref` is `None` and hashes as a `null` sentinel |
| `SourceRef.page_index` | MVP SS-01 (`models.py:29-37`) | the value that distinguishes two orderings |
| `plan_passes` | MVP SS-06 (`printing.py:110-141`) | consumed unchanged; must stay byte-identical |
| `PrinterProfile` | MVP SS-06 (`profiles.py`) | consumed unchanged; must stay byte-identical |
| `Side`-wrapped test fixtures in `tests/test_print_dialog.py` | SS-02 | otherwise the new `_hash_plan` reddens a file SS-04 does not own |

## Implementation Steps

### Step 1. Write the failing hash-distinguishes-content test

`tests/test_print_session.py` — add `Side` to the `deckle.core.models` import block (line 10)
and `_hash_plan` to the `deckle.core.print_session` import (line 11). Add a helper beside
`_blank_output_page` (lines 16-21):

```python
def _page(page_index: int) -> OutputPage:
    ref = SourceRef(path="src.pdf", page_index=page_index, sha256="a" * 64,
                    width_pt=612.0, height_pt=792.0)
    return OutputPage(source_ref=ref,
                      placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
                      is_filler=False)
```

`test_hash_plan_differs_for_different_page_orderings`:

```python
def test_hash_plan_differs_for_different_page_orderings():
    # Same sheet count, same side presence, different pages on the sides.
    # The MVP hash covered index + presence only, so these collided.
    a = SheetPlan(sheets=[Sheet(index=0, front=Side(pages=(_page(0), _page(1))),
                                back=Side(pages=(_page(2), _page(3))))],
                  paper_pt=(792.0, 612.0), warnings=[])
    b = SheetPlan(sheets=[Sheet(index=0, front=Side(pages=(_page(3), _page(2))),
                                back=Side(pages=(_page(1), _page(0))))],
                  paper_pt=(792.0, 612.0), warnings=[])
    assert _hash_plan(a) != _hash_plan(b)
```

Three companions in the same pass:

- `test_hash_plan_is_stable_across_identical_construction` — two independently constructed but
  equal plans hash the **same**. Without this the fix could be satisfied by hashing `id()`,
  which `export._plan_hash`'s docstring (`export.py:52-56`) already warns against.
- `test_hash_plan_distinguishes_absent_side_from_present_side` — a plan whose `back` is `None`
  hashes differently from the same plan with a `back`. This pins that presence stays in the
  payload rather than being replaced by the page tuple.
- `test_hash_plan_handles_filler_pages_without_a_source_ref` — a side holding a filler
  (`source_ref=None`) hashes without raising, and differs from a side holding a real page.
  `_blank_output_page` (line 16) already produces exactly this shape, and every existing
  fixture in the file uses it, so this is the path all ten existing tests take.

### Step 2. Run them and watch them fail

```bash
python -m pytest tests/test_print_session.py -q -k hash_plan
```

Expect `test_hash_plan_differs_for_different_page_orderings` to fail on
`assert '…' != '…'` — the two hashes are equal. That failure **is** evaluation finding I-1,
reproduced. The other three should pass or error on the missing import; only the first one is
the defect.

### Step 3. Change `_hash_plan`, and only `_hash_plan`

`deckle/core/print_session.py`, lines 73-86. Keep the function name, signature, docstring
position and return shape (`hexdigest()[:16]`). Add the per-side page-index tuple **inline**,
so the whole change is one contiguous diff hunk — do not add a module-level helper, which
would make it two:

```python
def _hash_plan(plan: SheetPlan) -> str:
    """A stable hash identifying a plan's sheet content.

    Covers sheet index, side PRESENCE, and each side's source page indices.
    Presence alone was not enough: two different page orderings over the
    same sheet count hashed identically, so a resumed session could bind to
    a document whose content had changed. Folio doubles the content behind
    each hash, which is what made a latent defect worth fixing.

    A filler page has no ``source_ref`` and hashes as ``None``. An absent
    side is ``None`` -- never ``Side(pages=())``, which is rejected in
    ``Side.__post_init__`` precisely so presence and content stay
    unambiguous here.
    """
    payload = json.dumps(
        [
            {
                "index": s.index,
                "front": s.front is not None,
                "back": s.back is not None,
                "front_pages": None if s.front is None else [
                    None if p.source_ref is None else p.source_ref.page_index
                    for p in s.front.pages
                ],
                "back_pages": None if s.back is None else [
                    None if p.source_ref is None else p.source_ref.page_index
                    for p in s.back.pages
                ],
            }
            for s in plan.sheets
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
```

**Touch nothing else in the module.** Not `__init__` (lines 151-196), not `start` (296),
not `confirm_test_sheet` (314), not `_submit_chunk` (321), not `_advance_pass` (348), not
`advance` (357), not `resume` (367), not `load` (234), not `list_resumable` (261), not
`_SessionState` (101-136), not `STATE_VERSION` (59). `STATE_VERSION` stays at `1`: the on-disk
*shape* is unchanged, only the value of one field it stores, and an old state file whose
`plan_hash` no longer matches is already handled by the resume flow taking the completed sheet
count as caller-supplied ground truth.

### Step 4. Run the session suite to green

```bash
python -m pytest tests/test_print_session.py -q
```

Ten existing tests plus the four new ones. Wrap `_make_plan` (lines 24-29) as
`front=Side(pages=(_blank_output_page(),)), back=Side(pages=(_blank_output_page(),))`. Change
no expected value in the existing ten — they assert chunking, resume cursors and state files,
none of which depends on the hash's value.

### Step 5. Write the public-surface test

Still in `tests/test_print_session.py`:

`test_print_session_public_surface_is_unchanged` — asserts each of the eleven public members
exists on `PrintSession` and, for the callables, that
`inspect.signature(...)` matches its MVP shape exactly:

| Member | Kind | MVP shape |
|---|---|---|
| `start` | method | `(self) -> None` |
| `advance` | method | `(self) -> None` |
| `confirm_test_sheet` | method | `(self) -> None` |
| `resume` | method | `(self, sheets_completed: int) -> None` |
| `load` | classmethod | `(plan, profile, backend, session_id) -> "PrintSession"` |
| `list_resumable` | staticmethod | `() -> list[SessionSummary]` |
| `state` | property | `-> dict` |
| `state_path` | property | `-> Path` |
| `reload_instruction` | property | `-> str \| None` |
| `finished` | property | `-> bool` |
| `last_error` | property | `-> str \| None` |

Check the four properties with `isinstance(inspect.getattr_static(PrintSession, name),
property)` so a property silently becoming a method is caught rather than passing on a bare
`hasattr`. This is REQ-015's "asserted by a test rather than by inspection".

### Step 6. Run it, then write the zero-diff pin test

```bash
python -m pytest tests/test_print_session.py -q -k public_surface
```

Create `tests/test_seam_zero_diff.py`. Its module docstring must state, in as many words:

> Regenerating a pin in this file is an **escalation, not a maintenance chore.**
> `deckle/core/printing.py` and `deckle/core/profiles.py` are zero-diff by design — the whole
> signatures-v2 feature enters through the `LayoutStrategy` seam and `plan_passes` reads only
> `Sheet.index` (`deckle/core/printing.py:121`). A hash mismatch here means the seam did not
> hold. Stop, surface it, and get a human decision. Do not update the constant.

Contents:

```python
PINNED_SHA256 = {
    "deckle/core/printing.py": "cbf1a0e81404cf85794242b0da466e6be82f9f0116ba111fdba803123d47607e",
    "deckle/core/profiles.py": "390ebd76acd340aec2fd327d1edf3e7c01250026e6b35068d3c85e268c88ae95",
}
```

Those two values were read from disk at commit `b54194c` and are the expected ones. Recompute
them at implementation time with the command in *Verification Commands* below; if they differ,
something already changed the seam and that is the escalation, not a reason to re-pin.

Four tests:

- `test_printing_py_is_unchanged` and `test_profiles_py_is_unchanged` — parametrised over
  `PINNED_SHA256`, resolving paths relative to the repo root (`Path(__file__).resolve().parents[1]`),
  reading bytes and comparing `hashlib.sha256(...).hexdigest()`. The assertion message must
  name the escalation, not suggest re-pinning.
- `test_plan_passes_reads_only_sheet_index` — an AST test over `deckle/core/printing.py`
  asserting that the only attribute accessed on an element of `plan.sheets` inside
  `plan_passes` is `index`. This is the *reason* the pin can hold, expressed as a test, so a
  future reader gets the mechanism and not just the assertion.
- `test_per_signature_subset_uses_the_existing_sheets_path` — REQ-035: build a plan of 8
  sheets, call `plan_passes(plan, profile, sheets=(4, 5, 6, 7))`, and assert both passes'
  `sheet_order` cover exactly those four indices and nothing else, with the back pass reversed
  when `profile.reverse_stack` is true. This proves per-signature printing needs no new branch.
  Reuse `tests/test_printing.py`'s existing profile/plan fixture style.

### Step 7. Run the pin test, then the full suite, lint, commit

```bash
python -m pytest tests/test_seam_zero_diff.py -q
python -m pytest tests/test_print_session.py tests/test_print_dialog.py tests/test_printing.py -q
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "fix(SS-04): _hash_plan covers side content; pin the zero-diff seam"
```

Before committing, confirm the one-hunk property holds:

```bash
git diff --unified=0 -- deckle/core/print_session.py | grep -c "^@@"
```

It must print `1`. More than one hunk means something outside `_hash_plan` was touched — revert
that part.

## Interface Contracts

### print_session._hash_plan
- Direction: SS-04 → SS-12, SS-13
- Owner: SS-04
- Shape: `_hash_plan(plan: SheetPlan) -> str` — a 16-character hex digest, unchanged in name,
  arity and return type.
- Payload, per sheet: `index`, `front` presence (bool), `back` presence (bool), `front_pages`
  and `back_pages` — each `None` for an absent side, otherwise the list of
  `page.source_ref.page_index` for every page in `side.pages`, with `None` as the sentinel for
  a filler whose `source_ref` is `None`.
- Invariant: two plans with the same sheet count and side presence but different pages hash
  **differently**; two equal plans hash the **same**; an absent side still hashes distinctly
  from a present one.
- Invariant: this is the **only** change in `deckle/core/print_session.py`, and it is one
  contiguous diff hunk.

### PrintSession public surface
- Direction: SS-04 → SS-11, SS-12
- Owner: MVP SS-11, **frozen by SS-04's test**
- Shape: `start`, `advance`, `confirm_test_sheet`, `resume(sheets_completed)`,
  `load(plan, profile, backend, session_id)`, `list_resumable()`, and the properties `state`,
  `state_path`, `reload_instruction`, `finished`, `last_error`.
- Invariant: `resume`'s `sheets_completed` is **per-pass, not cumulative**
  (`print_session.py:367-374`). Changing this surface is a named escalation trigger.

### manual_duplex_seam
- Direction: SS-04 → every subsequent sub-spec
- Owner: SS-04, `frozen: true`
- Shape: `deckle/core/printing.py` and `deckle/core/profiles.py` have **zero diff**, pinned by
  SHA-256 in `tests/test_seam_zero_diff.py`. `deckle/core/print_session.py` changes in exactly
  one place.
- Invariant: `plan_passes` reads only `Sheet.index` (`printing.py:121`). Per-signature printing
  is `plan_passes(plan, profile, sheets=signature.sheet_indices)` through the existing subset
  path — **no new branch in `printing.py`**.
- Escalation: a hash mismatch is a design failure, not a maintenance task. Do not re-pin.

## Verification Commands

Build check:

```bash
python -c "import deckle.core.print_session" || (echo "FAIL: print_session not importable" && exit 1)
python -m ruff check deckle tests
```

Recompute the pins (implementation time only, to confirm the constants above):

```bash
python -c "import hashlib,pathlib; [print(p, hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()) for p in ('deckle/core/printing.py','deckle/core/profiles.py')]"
```

Test check:

```bash
python -m pytest tests/test_print_session.py -q
python -m pytest tests/test_seam_zero_diff.py -q
python -m pytest tests/test_printing.py tests/test_print_dialog.py -q
python -m pytest -q
```

Per-criterion acceptance check:

```bash
# REQ-014 -- two orderings over the same sheet count hash differently
python -m pytest tests/test_print_session.py -q -k hash_plan_differs_for_different_page_orderings

# Stability -- a plan and its copy hash the same
python -m pytest tests/test_print_session.py -q -k hash_plan_is_stable_across_identical_construction

# REQ-015 -- the seam is unchanged, asserted by a test not by inspection
python -m pytest tests/test_seam_zero_diff.py -q
git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py || (echo "FAIL: seam files diverged from the v2 baseline" && exit 1)

# REQ-015 -- PrintSession's public surface is unchanged
python -m pytest tests/test_print_session.py -q -k public_surface

# REQ-035 -- per-signature printing needs no new branch
python -m pytest tests/test_printing.py tests/test_integration_signatures.py -q -k "plan_passes_with_explicit_sheets_covers_only_those_sheets or end_to_end_folio_signature_impose_export_fold_and_print"

# print_session.py changed in exactly one place
test $(git diff --unified=0 HEAD -- deckle/core/print_session.py | grep -c "^@@") -le 1 || (echo "FAIL: print_session.py changed in more than one place" && exit 1)
```

## Checks

Every negative check below was executed against the working tree at commit `b54194c` and
confirmed to exit 0.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | Two page orderings hash differently (REQ-014) | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q -k hash_plan_differs_for_different_page_orderings \|\| (echo "FAIL: _hash_plan still ignores side content" && exit 1)` |
| 2 | A plan and its copy hash the same | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q -k hash_plan_is_stable_across_identical_construction \|\| (echo "FAIL: _hash_plan is not stable across construction" && exit 1)` |
| 3 | An absent side still hashes distinctly (REQ-014) | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q -k hash_plan_distinguishes_absent_side_from_present_side \|\| (echo "FAIL: side presence dropped from the hash payload" && exit 1)` |
| 4 | `_hash_plan` reads each page's `source_ref.page_index` (REQ-014) | [STRUCTURAL] | `grep -q "source_ref.page_index" deckle/core/print_session.py \|\| (echo "FAIL: _hash_plan does not read page indices" && exit 1)` |
| 5 | `source_ref` is referenced nowhere but `_hash_plan` | [MECHANICAL] | `python -c "import ast,pathlib,sys; t=ast.parse(pathlib.Path('deckle/core/print_session.py').read_text(encoding='utf-8')); sys.exit(1 if [1 for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name!='_hash_plan' for s in ast.walk(n) if isinstance(s,ast.Attribute) and s.attr=='source_ref'] else 0)" \|\| (echo "FAIL: source_ref referenced outside _hash_plan" && exit 1)` |
| 6 | `print_session.py` changed in at most one place | [MECHANICAL] | `test $(git diff --unified=0 HEAD -- deckle/core/print_session.py \| grep -c "^@@") -le 1 \|\| (echo "FAIL: print_session.py changed in more than one place" && exit 1)` |
| 7 | `STATE_VERSION` is unchanged (no on-disk shape change) | [STRUCTURAL] | `grep -q "^STATE_VERSION = 1$" deckle/core/print_session.py \|\| (echo "FAIL: STATE_VERSION bumped -- the on-disk shape did not change" && exit 1)` |
| 8 | `PrintSession`'s public surface is unchanged (REQ-015) | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q -k public_surface \|\| (echo "FAIL: PrintSession public surface moved" && exit 1)` |
| 9 | The SHA-256 pins match the files on disk (REQ-015) | [MECHANICAL] | `python -m pytest tests/test_seam_zero_diff.py -q \|\| (echo "FAIL: zero-diff seam broken -- ESCALATE, do not re-pin" && exit 1)` |
| 10 | The seam files are byte-identical to the v2 baseline (REQ-015) | [MECHANICAL] | `git diff --quiet b54194c -- deckle/core/printing.py deckle/core/profiles.py \|\| (echo "FAIL: printing.py or profiles.py diverged from the v2 baseline -- ESCALATE" && exit 1)` |
| 11 | The pinned digests are the ones on disk (REQ-015) | [MECHANICAL] | `python -c "import hashlib,pathlib,sys; P={'deckle/core/printing.py':'cbf1a0e81404cf85794242b0da466e6be82f9f0116ba111fdba803123d47607e','deckle/core/profiles.py':'390ebd76acd340aec2fd327d1edf3e7c01250026e6b35068d3c85e268c88ae95'}; sys.exit(1 if [p for p,h in P.items() if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h] else 0)" \|\| (echo "FAIL: seam file digest changed -- ESCALATE, do not re-pin" && exit 1)` |
| 12 | No signature/side concept leaked into the seam (REQ-015) | [MECHANICAL] | `! grep -n "\.front\|\.back\|Side\|Signature\|Mark\|marks" deckle/core/printing.py deckle/core/profiles.py \|\| (echo "FAIL: signature/side concepts leaked into the manual-duplex seam" && exit 1)` |
| 13 | Per-signature printing uses the existing subset path (REQ-035) | [MECHANICAL] | `python -m pytest tests/test_printing.py tests/test_integration_signatures.py -q -k "plan_passes_with_explicit_sheets_covers_only_those_sheets or end_to_end_folio_signature_impose_export_fold_and_print" \|\| (echo "FAIL: per-signature subset path broken" && exit 1)` |
| 14 | `plan_passes` reads only `Sheet.index` (REQ-035) | [MECHANICAL] | `python -m pytest tests/test_seam_zero_diff.py -q -k reads_only_sheet_index \|\| (echo "FAIL: plan_passes touches a Sheet attribute other than index" && exit 1)` |
| 15 | The pin file names re-pinning as an escalation | [STRUCTURAL] | `grep -qi "escalation" tests/test_seam_zero_diff.py \|\| (echo "FAIL: pin file does not state that re-pinning is an escalation" && exit 1)` |
| 16 | No Qt in `print_session.py` | [MECHANICAL] | `! grep -rn "PySide6\|QtWidgets" deckle/core/print_session.py \|\| (echo "FAIL: Qt in print_session" && exit 1)` |
| 17 | No Qt import anywhere in `deckle.core` (REQ-040) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 18 | No empty `Side` constructed anywhere (SS-01 invariant this fix rests on) | [MECHANICAL] | `! grep -rnE "Side\(\s*(pages\s*=\s*)?\(\s*\)\s*\)" deckle/ tests/ \|\| (echo "FAIL: Side(pages=()) constructed -- presence and content would be ambiguous in _hash_plan" && exit 1)` |
| 19 | `tests/test_print_session.py` green with at least 10 tests | [MECHANICAL] | `python -m pytest tests/test_print_session.py -q && test $(python -m pytest tests/test_print_session.py -q --collect-only 2>/dev/null \| grep -c "::") -ge 10 \|\| (echo "FAIL: print-session coverage shrank below 10" && exit 1)` |
| 20 | Session-adjacent suites green | [MECHANICAL] | `python -m pytest tests/test_printing.py tests/test_print_dialog.py -q \|\| (echo "FAIL: session-adjacent suites red" && exit 1)` |
| 21 | Core purity test green (REQ-040) | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity violated" && exit 1)` |
| 22 | Suite has not shrunk (REQ-039) | [MECHANICAL] | `test $(python -m pytest -q --collect-only 2>/dev/null \| grep -c "::") -ge 237 \|\| (echo "FAIL: suite collected fewer than 237 tests" && exit 1)` |
| 23 | Full suite green (REQ-039) | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: suite red after the _hash_plan change" && exit 1)` |
| 24 | Lint clean (REQ-039) | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff violations" && exit 1)` |

**Note on check 6:** `git diff --unified=0 HEAD` compares against the last commit, so this
check is meaningful *before* the SS-04 commit and trivially satisfied after it. Run it as the
final pre-commit gate (Step 7), not as a post-hoc audit. Check 10, which pins against the fixed
baseline commit `b54194c`, is the durable form and stays meaningful indefinitely.

**Note on check 12:** the pattern includes `Side`, `Signature`, `Mark` and `marks` and exits 0
against the current `printing.py`/`profiles.py`. It is a redundancy on top of the SHA pin — a
digest mismatch tells you *that* the seam moved, this tells you *how*.

**Note on check 17 and the anchored grep:** `! grep -rn "PySide6" deckle/core/` is **wrong** —
it matches the "must not import PySide6" prose at `deckle/core/models.py:5` and
`deckle/core/__init__.py:3` and fails on a clean tree. Check 16 greps only
`print_session.py`, whose docstring says "**No Qt.**" without naming the module, so the
unanchored form is safe *there* and was verified to exit 0; check 17 spans the package and must
stay anchored.

**Note on `ruff`:** `ruff` 0.15.13 is installed but is not on `PATH` under Git Bash here;
`python -m ruff check deckle tests` is the verified invocation.
