# SS-05 — `deckle/core/signatures.py`: the arithmetic, and the fold simulator

**Parent spec:** `docs/specs/2026-08-04-deckle-signatures-v2.md` (section 5)
**Contracts:** `docs/specs/deckle-signatures-v2/contracts.yaml` — `split_signatures`, `saddle_order`, `fold_reading_order`
**Phase:** run
**Depends on:** SS-01
**Satisfies:** REQ-016, REQ-017, REQ-018, REQ-019

---

## 1. Context

**What this sub-spec does:**
Adds one new pure module, `deckle/core/signatures.py`, holding three I/O-free functions:

- `split_signatures(sheet_count, sheets_per_signature)` — carves a sheet count into contiguous
  gatherings.
- `saddle_order(n)` — the fold order of an `n`-page nested (saddle) signature, as a flat list
  of source indices, two per sheet side.
- `fold_reading_order(plan)` — the **fold simulator**: given a `SheetPlan`, fold it back up in
  your head and report the source page indices in the order a reader encounters them.

**Why it matters, and why it is the highest-risk sub-spec in the project:**
`saddle_order` is not library code. The vault note that supplies it
(`C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition and Signature Recipe.md`)
says so in as many words at line 101:

> **`saddle_order` is my own function, not pikepdf's.** pikepdf has no imposition logic; it
> gives you placement primitives. Validate the fold order against a physical folded dummy
> before trusting it for a real print run. Verify the imposition math independently of the
> pikepdf mechanics — the pikepdf half is confirmed, the bindery half is yours.

The pikepdf mechanics in that note are executed, verified code (pikepdf 10.11.0 / libqpdf
12.3.2, Windows 11, 2026-08-04). The **bindery arithmetic is not verified**. Everything else
in v2 — cells, marks, export, UI — is in service of `saddle_order` being right, and nothing in
the automated suite can prove it. Only SS-13's physical folded dummy can.

**Therefore this sub-spec's central engineering requirement is independence.**
`fold_reading_order` is the only automated check on `saddle_order`, and it is worthless if the
two share an implementation. If `saddle_order` is subtly wrong and `fold_reading_order` encodes
the same wrongness, the round-trip test in SS-08 passes green on a book that reads out of
order. That is the design war-game's single most expensive failure mode. So:

- **Write `fold_reading_order` FIRST**, from the physical description of folding and nesting,
  **before `saddle_order` exists in the file at all.** You cannot accidentally reuse a function
  that has not been written yet. This mirrors the MVP's SS-03
  write-the-regression-test-before-the-fix precedent.
- Its independence is then **pinned by two mechanical checks** that survive later edits: an AST
  test (no static reference to `saddle_order` anywhere in `fold_reading_order`'s
  `FunctionDef`), and a runtime monkeypatch test (replace
  `deckle.core.signatures.saddle_order` with a stub that raises, and assert
  `fold_reading_order` still returns the right answer — this catches indirect reuse the AST
  test would miss).

**Boundaries:** no I/O, no Qt, no pikepdf, no import of `deckle.core.layout` (that would be a
cycle: SS-08's `layout.py` imports *this* module). This module imports from
`deckle.core.models` only.

`blank_mode="balanced"` is **not** implemented here. Padding and blank distribution are
applied by the caller at the page-slot level in SS-08.

---

## 2. Provides / Requires

**Provides:**

| Symbol | Kind | Module | Consumed by |
|---|---|---|---|
| `split_signatures(sheet_count: int, sheets_per_signature: int) -> list[tuple[int, ...]]` | function | `deckle/core/signatures.py` | SS-08 (`SaddleStitchStrategy.impose`) |
| `saddle_order(n: int) -> list[int]` | function | `deckle/core/signatures.py` | SS-08 (`SaddleStitchStrategy.impose`) |
| `fold_reading_order(plan: SheetPlan, *, binding_edge: str = "left") -> list[int \| None]` | function | `deckle/core/signatures.py` | SS-08 (round-trip test), `tests/test_layout_saddle.py` |

**Requires:**

| Symbol | Kind | Owner | Used for |
|---|---|---|---|
| `Side` (`pages: tuple[OutputPage, ...]`) | frozen dataclass | SS-01 (`deckle/core/models.py`) | reading a sheet side's two leaves in cell order |
| `Sheet` (`front: Side \| None`, `back: Side \| None`) | frozen dataclass | SS-01 | unfolding a sheet |
| `Signature` (`index`, `sheet_indices`, `blank_count`) | frozen dataclass | SS-01 | grouping sheets into gatherings |
| `SheetPlan.signatures: tuple[Signature, ...]` | field | SS-01 | walking the plan's gatherings |
| `OutputPage.source_ref`, `OutputPage.is_filler` | fields | MVP `models.py` | mapping a placed leaf back to a source page index |

**Explicitly NOT required:** `deckle.core.layout`, `LayoutSettings`, `pikepdf`, `PySide6`.
`fold_reading_order` reads the plan's own structure, never the settings that produced it.

---

## 3. Interface Contracts

### split_signatures

**Direction:** `signatures.py` → SS-08 `SaddleStitchStrategy.impose`
**Owner:** SS-05
**Shape:**

```python
def split_signatures(sheet_count: int, sheets_per_signature: int) -> list[tuple[int, ...]]:
    """Contiguous sheet-index groups; the remainder lands in the FINAL group."""
```

**Invariants:**

- Groups are contiguous ascending runs of sheet indices.
- Concatenated in order, the groups equal `list(range(sheet_count))` exactly — no gaps, no
  overlaps, every sheet covered once.
- No group is ever empty.
- Only the final group may be shorter than `sheets_per_signature`.
- `sheets_per_signature >= sheet_count` yields exactly one group covering every sheet.
- `sheet_count == 0` yields `[]`.
- `sheets_per_signature <= 0` is clamped to `1` (it arrives from a UI spinbox; per the spec's
  *Edge Cases*, strict at the type boundary, permissive above it). `sheet_count < 0` raises
  `ValueError`.

### saddle_order

**Direction:** `signatures.py` → SS-08 `SaddleStitchStrategy.impose`
**Owner:** SS-05
**Shape:**

```python
def saddle_order(n: int) -> list[int]:
    """Fold order for a saddle-stitched signature of n pages (n % 4 == 0).

    A flat list of source indices, two per sheet side, read in pairs:
    (sheet 0 front left, sheet 0 front right, sheet 0 back left, sheet 0 back right, ...).
    """
```

**Implementation — transcribed verbatim from the vault note, lines 35–44:**

```python
seq = []
lo, hi = 0, n - 1
while lo < hi:
    seq += [hi, lo, lo + 1, hi - 1]
    lo += 2
    hi -= 2
return seq
```

**Invariants:**

- `n` must be a positive multiple of 4; otherwise `ValueError`. This is a programming error,
  not user input, so it is strict.
- Returns a permutation of `range(n)`.
- **PINNED:** `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]` — the executed output recorded at
  line 63 of the vault note, `order: [7, 0, 1, 6, 5, 2, 3, 4]`.
- `seq[:2] == [n - 1, 0]` — the outermost sheet carries the last and the first page. This is
  the defining property of a **nested** (saddle) gathering; a **stacked** gathering fails it.

**Provenance, to be reproduced in the module docstring:** executed end to end against pikepdf
10.11.0 / libqpdf 12.3.2 on this machine, 2026-08-04, producing 8 half-letter pages on 4
landscape letter sheets. The pikepdf half is confirmed; **the bindery half is not** — the note
requires validation against a physical folded dummy (SS-13) before a real print run.

### fold_reading_order

**Direction:** `signatures.py` → SS-08's round-trip test and `tests/test_layout_saddle.py`
**Owner:** SS-05
**Shape:**

```python
def fold_reading_order(
    plan: SheetPlan, *, binding_edge: Literal["left", "right"] = "left"
) -> list[int | None]:
    """Fold the plan back up: source page indices in the order a reader meets them.

    A filler leaf yields None.
    """
```

> **Shape note — read before implementing.** The committed contract is
> `fold_reading_order(plan: SheetPlan) -> list[int]`. A `SheetPlan` carries `sheets`,
> `paper_pt`, `warnings` and `signatures` — it does **not** carry `binding_edge`, and an
> RTL plan is the exact geometric mirror of an LTR plan, so reading direction is genuinely
> unobservable from the plan alone. REQ-019 nevertheless requires the round trip to hold for
> both binding edges. The resolution is a **keyword-only parameter with a default**, so
> `fold_reading_order(plan)` remains a valid positional call exactly as the contract writes it
> and no committed call form changes. This is a shape-preserving extension, not a contract
> change. **If a reviewer reads it otherwise, stop and escalate rather than inferring the
> handedness from the page indices already placed in the plan** — inferring it from
> `source_ref.page_index` would make the simulator assume the answer it is supposed to check.

**Derivation — the physical fold, written out so the implementer never reaches for
`saddle_order`.** Do not read `saddle_order`'s source while writing this. Work only from what
follows.

Take one landscape sheet and fold it once down its vertical centreline. It now has four
leaf-faces: front-left, front-right, back-left, back-right. Nest `k` such folded sheets one
inside the next; the first sheet of the gathering is the **outermost**, the last is the
**innermost**. The gathering has `4k` leaves, read `1 … 4k`.

Now open the gathering flat and read off which leaf-face carries which reading position. For a
left-bound (LTR) gathering, sheet at nesting position `s` (0-based, `s = 0` is outermost),
1-based leaf numbers:

```
front-right  =  2s + 1
back-left    =  2s + 2
back-right   =  4k - 2s - 1
front-left   =  4k - 2s
```

Sanity, `k = 2` (8 leaves): `s = 0` gives front `(8, 1)`, back `(2, 7)`; `s = 1` gives front
`(6, 3)`, back `(4, 5)`. The outermost sheet carries leaves 1 and 8 — nesting, not stacking.

Inverted to 0-based **reading slots** (this is the direction to code, because it writes each
observed leaf into its reading position):

```
slot[2s]          <- sheet s, FRONT side, RIGHT cell
slot[2s + 1]      <- sheet s, BACK  side, LEFT  cell
slot[4k - 2s - 2] <- sheet s, BACK  side, RIGHT cell
slot[4k - 2s - 1] <- sheet s, FRONT side, LEFT  cell
```

Under `binding_edge="right"` (RTL) the two cells swap roles: read LEFT where the table says
RIGHT and vice versa. Nothing else changes.

**Algorithm:**

1. For each `Signature` in `plan.signatures`, in `index` order:
   1. `k = len(sig.sheet_indices)`; allocate `slots = [None] * (4 * k)`.
   2. For `s, sheet_index in enumerate(sig.sheet_indices)` — `sheet_indices` are in binding
      order, so position `s` **is** the nesting position, outermost first.
   3. Read `plan.sheets[sheet_index].front` and `.back`. A side's `pages` tuple is in cell
      order, left cell first. An absent side is `None` and contributes nothing.
   4. Write the four leaves into the four slots per the table above.
   5. Append `slots` to the result.
2. A plan with `signatures == ()` (a `GutterShiftStrategy` plan) returns `[]` — consumers must
   tolerate the empty tuple and must not treat it as an error.
3. Map each placed `OutputPage` to `page.source_ref.page_index`, or to `None` when
   `page.is_filler` or `page.source_ref is None`.

**Independence invariants — both mechanically enforced:**

- **Static:** an AST walk of `fold_reading_order`'s `FunctionDef` finds no `ast.Name` with
  `id == "saddle_order"` and no `ast.Attribute` with `attr == "saddle_order"`.
- **Runtime:** with `deckle.core.signatures.saddle_order` monkeypatched to a stub that raises
  `AssertionError`, `fold_reading_order` still returns the correct answer. The static check
  alone can be defeated by an indirect call (`globals()["saddle_order"]`, a helper that calls
  it, a module-level alias); the runtime check cannot.

---

## 4. Implementation Steps

Each step is 2–10 minutes. Run the stated command, see the stated result, then move on. Commit
at the end of each numbered step with the stated message.

> **Reminder for every step:** run commands from the repository root
> (`C:\Users\CalebBennett\Documents\GitHub\BookBinder`). `ruff` is not on `PATH` in Git Bash on
> this machine; invoke it as `python -m ruff`.

---

### Step 1 — Failing test: the physical-fold fixture for an 8-page gathering

**Write:** `tests/test_signatures.py`

Create the file with a module docstring stating that the fixtures in it are transcribed from
the **physical description of a folded, nested folio gathering** (SS-05 §3), not from
`saddle_order`'s source, and a helper that builds a `SheetPlan` by hand.

Add the test:

```python
def test_fold_reading_order_reads_a_hand_built_two_sheet_gathering_in_order():
    # One signature, 2 sheets, 8 leaves. Cell order within a Side is
    # (left cell, right cell). Transcribed from the physical fold:
    #   sheet 0 (outermost): front (8, 1), back (2, 7)
    #   sheet 1 (innermost): front (6, 3), back (4, 5)
    # ...expressed 0-based below.
    plan = make_plan([
        ((7, 0), (1, 6)),   # sheet 0: (front left, front right), (back left, back right)
        ((5, 2), (3, 4)),   # sheet 1
    ])
    assert fold_reading_order(plan) == [0, 1, 2, 3, 4, 5, 6, 7]
```

**Assertion:** `fold_reading_order(plan) == [0, 1, 2, 3, 4, 5, 6, 7]`.

> **Note on the fixture, and why it is not circular.** These four pairs are derived from the
> nesting table in §3, which was written from the physical fold. They *happen to agree*
> with the vault note's recorded executed output (`[7, 0, 1, 6, 5, 2, 3, 4]` read two at a
> time). That agreement is corroboration between two independently written descriptions and is
> worth having — but it is **not proof**, because both descriptions are the same author's
> mental model of a folded sheet. Only SS-13's physical dummy is proof. Say this in the test's
> docstring.

**Run:** `python -m pytest tests/test_signatures.py -q`
**Expect:** collection error — `deckle.core.signatures` does not exist. This is the red.

---

### Step 2 — Minimal implementation: `fold_reading_order`, and nothing else in the file

**Write:** `deckle/core/signatures.py`

Module docstring: pure signature arithmetic, no I/O, no Qt, no pikepdf, imports
`deckle.core.models` only. Cite the vault note by name and reproduce its warning that the
bindery half is unverified and gated on SS-13.

Implement `fold_reading_order` exactly per §3's slot table. **`saddle_order` must not exist in
this file yet** — that is the mechanism that makes the independence real rather than
aspirational.

**Run:** `python -m pytest tests/test_signatures.py -q`
**Expect:** green, 1 test.
**Commit:** `feat(core): fold simulator, written from the physical fold before saddle_order exists`

---

### Step 3 — Failing test: RTL, fillers, empty plan, and a three-sheet gathering

**Write:** `tests/test_signatures.py`

Four tests:

- `test_fold_reading_order_mirrors_under_right_binding` — the same 8-leaf gathering with both
  cells swapped on every side, called as `fold_reading_order(plan, binding_edge="right")`,
  returns `[0, 1, 2, 3, 4, 5, 6, 7]`.
- `test_fold_reading_order_reports_fillers_as_none` — a leaf built with `is_filler=True` and
  `source_ref=None` appears as `None` at its reading slot.
- `test_fold_reading_order_of_a_plan_with_no_signatures_is_empty` — a plan with
  `signatures=()` returns `[]` and does not raise. (Guards the `GutterShiftStrategy` case;
  consumers must tolerate an empty tuple.)
- `test_fold_reading_order_reads_a_three_sheet_gathering_in_order` — `k = 3`, 12 leaves, built
  from the §3 table by hand, returns `list(range(12))`.

**Assertion:** all four as stated.
**Run:** `python -m pytest tests/test_signatures.py -q` → red on the RTL and filler cases.

---

### Step 4 — Implementation: RTL swap, filler mapping, empty-plan guard

**Write:** `deckle/core/signatures.py`

**Run:** `python -m pytest tests/test_signatures.py -q` → green, 5 tests.
**Commit:** `feat(core): fold simulator handles RTL, fillers and signature-free plans`

---

### Step 5 — Failing test: static independence (AST)

**Write:** `tests/test_signatures.py`

```python
def test_fold_reading_order_does_not_call_saddle_order():
    """fold_reading_order must be derived from the physical fold, not from saddle_order.

    If both share an implementation they can encode the SAME error and agree, and
    the SS-08 round trip goes green on a book that reads out of order. That is the
    design war-game's most expensive failure mode. See REQ-018.
    """
    src = Path(deckle.core.signatures.__file__).read_text(encoding="utf-8")
    fns = [n for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.FunctionDef) and n.name == "fold_reading_order"]
    assert fns, "fold_reading_order not found in deckle/core/signatures.py"
    offenders = [n for f in fns for n in ast.walk(f)
                 if (isinstance(n, ast.Name) and n.id == "saddle_order")
                 or (isinstance(n, ast.Attribute) and n.attr == "saddle_order")]
    assert not offenders, "fold_reading_order references saddle_order"
```

**Assertion:** `offenders == []`.
**Run:** `python -m pytest tests/test_signatures.py -q -k fold_reading_order_does_not_call_saddle_order`
**Expect:** green immediately — `saddle_order` does not exist yet, which is the point. Prove the
test can fail before trusting it: temporarily add `_ = saddle_order` inside
`fold_reading_order`, re-run, see it go red, then remove it. **Do this; a check never observed
failing is not a check.**

**Commit:** `test(core): pin fold_reading_order's static independence from saddle_order`

---

### Step 6 — Failing test: `saddle_order`'s pinned value and its ValueError

**Write:** `tests/test_signatures.py`

- `test_saddle_order_pinned_for_eight_pages` —
  `assert saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`, with a docstring citing
  `[[pikepdf - Imposition and Signature Recipe]]` line 63 as executed output against pikepdf
  10.11.0 / libqpdf 12.3.2 on 2026-08-04, and noting SS-13 is the arbiter.
- `test_saddle_order_rejects_non_multiples_of_four` — `pytest.raises(ValueError)` for
  `0, -4, 1, 2, 3, 6, 10`.

**Run:** `python -m pytest tests/test_signatures.py -q` → red, `saddle_order` undefined.

---

### Step 7 — Implementation: `saddle_order`

**Write:** `deckle/core/signatures.py`

Transcribe the vault note's loop verbatim (§3). Add the `n <= 0 or n % 4` guard raising
`ValueError`. Docstring carries the provenance and the unverified-bindery warning.

**Run:** `python -m pytest tests/test_signatures.py -q` → green, 7 tests.
**Commit:** `feat(core): saddle_order, transcribed from the executed vault recipe`

---

### Step 8 — Failing test: runtime independence, by monkeypatch

**Write:** `tests/test_signatures.py`

```python
def test_fold_reading_order_works_with_saddle_order_disabled(monkeypatch):
    """Catches indirect reuse the AST test cannot see."""
    def _boom(*a, **kw):
        raise AssertionError("fold_reading_order must not use saddle_order")
    monkeypatch.setattr(deckle.core.signatures, "saddle_order", _boom)
    plan = make_plan([((7, 0), (1, 6)), ((5, 2), (3, 4))])
    assert deckle.core.signatures.fold_reading_order(plan) == list(range(8))
```

**Assertion:** returns `list(range(8))` without raising.
**Run:** `python -m pytest tests/test_signatures.py -q` → green if Step 2 was honest. If it is
**red**, `fold_reading_order` is reusing `saddle_order` indirectly — rewrite it from §3's
table. Do not weaken the test.

**Commit:** `test(core): runtime independence check for the fold simulator`

---

### Step 9 — Failing test: `saddle_order`'s structural properties

**Write:** `tests/test_signatures.py`

- `test_saddle_order_is_a_permutation` — parametrised over every multiple of 4 from 4 to 128:
  `sorted(saddle_order(n)) == list(range(n))`.
- `test_saddle_order_outermost_sheet_carries_last_and_first_page` — same parametrisation:
  `saddle_order(n)[:2] == [n - 1, 0]`, with a docstring saying this is the property that
  distinguishes a **nested** gathering from a **stacked** one, and that a stacked ordering
  fails it.

**Run:** `python -m pytest tests/test_signatures.py -q` → green (both hold for the transcribed
implementation). Confirm the parametrisation actually generated 32 cases each with
`--collect-only`, so a typo in the range does not silently reduce coverage to one case.

**Commit:** `test(core): permutation and nested-gathering properties for saddle_order`

---

### Step 10 — Failing test: `split_signatures` as properties, not golden values

**Write:** `tests/test_signatures.py`

One parametrised test over sheet counts `{1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 25, 67, 100}` ×
sheets-per-signature `{1, 2, 3, 4, 6, 8}` asserting, per the spec's *Preferences* (properties
over golden values):

- every group is a contiguous ascending run;
- `[i for g in groups for i in g] == list(range(sheet_count))` — this single assertion covers
  gapless, non-overlapping and complete at once;
- no group is empty;
- every group except the last has length `sheets_per_signature`;
- the last group's length is in `1..sheets_per_signature`.

Plus edge tests: `split_signatures(0, 4) == []`;
`split_signatures(3, 10) == [(0, 1, 2)]` (one group covering every sheet);
`split_signatures(8, 0)` behaves as `split_signatures(8, 1)` (clamped, not raised);
`split_signatures(-1, 4)` raises `ValueError`.

**Run:** `python -m pytest tests/test_signatures.py -q` → red, `split_signatures` undefined.

---

### Step 11 — Implementation: `split_signatures`

**Write:** `deckle/core/signatures.py`

**Run:** `python -m pytest tests/test_signatures.py -q` → green.
**Commit:** `feat(core): split_signatures, contiguous groups with the remainder last`

---

### Step 12 — Cross-check: `saddle_order` against `fold_reading_order`, locally

**Write:** `tests/test_signatures.py`

`test_saddle_order_round_trips_through_the_fold_simulator` — parametrised over
`sheets_per_signature` `{1, 2, 3, 4, 8}`:

1. `order = saddle_order(4 * k)`;
2. build a `SheetPlan` by hand, laying `order` into sides two at a time exactly as SS-08 will —
   `Side(pages=(left, right))` on each front, then each back — with one `Signature` covering
   all `k` sheets;
3. assert `fold_reading_order(plan) == list(range(4 * k))`.

This is SS-08's REQ-019 round trip in miniature, available a whole wave earlier and without
`layout.py`. Its value comes entirely from the independence pinned in Steps 5 and 8.

**Escalation:** if this fails, **do not "fix" the simulator to agree.** Per the spec's
*Escalation Triggers*, a disagreement between `fold_reading_order` and the imposition without
an obvious single-sided bug is a stop-and-surface. Editing the simulator until it matches
destroys the only independent check in the feature.

**Run:** `python -m pytest tests/test_signatures.py -q` → green.
**Commit:** `test(core): local round trip between saddle_order and the fold simulator`

---

### Step 13 — Purity, lint, and the full suite

**Run, in order:**

```
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests
python -m pytest -q
```

All three must be clean; `python -m pytest -q` must collect no fewer than 237 tests. If `ruff`
would need a suppression, or the collected count would fall below 237, stop and surface.

**Commit:** `chore(core): signatures module clean under ruff and core-purity`

---

## 5. Verification Commands

```bash
cd "C:/Users/CalebBennett/Documents/GitHub/BookBinder"

# 1. The sub-spec's own suite, milliseconds.
python -m pytest tests/test_signatures.py -q

# 2. The two independence checks, named individually.
python -m pytest tests/test_signatures.py -q -k fold_reading_order_does_not_call_saddle_order
python -m pytest tests/test_signatures.py -q -k fold_reading_order_works_with_saddle_order_disabled

# 3. The pinned vault value.
python -m pytest tests/test_signatures.py -q -k saddle_order_pinned_against_vault_note_n8

# 4. Purity and lint.
python -m pytest tests/test_core_purity.py -q
python -m ruff check deckle tests

# 5. Nothing else moved.
python -m pytest -q
```

---

## 6. Checks

Every command below exits 0 on the passing case. Negative assertions are written
`! grep … || (echo "FAIL: …" && exit 1)` per `docs/decisions.md`, *Negative-assertion
acceptance criteria must exit 0 when the pattern is absent* — a bare `grep` with prose saying
"returns nothing" deferred two sub-specs in factory run `c46e15e3` and cascaded into seven
more. Run from the repository root in Git Bash. `ruff` is invoked as `python -m ruff`; the bare
`ruff` binary is not on `PATH` in Git Bash on this machine.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | `signatures.py` exposes the three functions, and `fold_reading_order(plan)` is a valid positional call exactly as the contract writes it | `[STRUCTURAL]` | `python -c "import inspect,sys; from deckle.core import signatures as s; missing=[n for n in ('split_signatures','saddle_order','fold_reading_order') if not callable(getattr(s,n,None))]; sys.exit('FAIL: missing '+repr(missing)) if missing else None; p=list(inspect.signature(s.fold_reading_order).parameters.values()); sys.exit('FAIL: first param is not a positional plan') if p[0].name!='plan' or p[0].kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD else print('OK')"` |
| 2 | `split_signatures` properties: contiguous, gapless, non-overlapping, remainder last | `[BEHAVIORAL]` | `python -m pytest tests/test_signatures.py -q -k split_signatures` |
| 3 | `saddle_order(n)` is a permutation of `range(n)` for every multiple of 4 up to 128 | `[BEHAVIORAL]` | `python -m pytest tests/test_signatures.py -q -k saddle_order_is_permutation_for_multiples_of_4` |
| 4 | `saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]`, pinned to the vault note's executed output | `[BEHAVIORAL]` | `python -m pytest tests/test_signatures.py -q -k saddle_order_pinned_against_vault_note_n8` |
| 5 | `saddle_order(n)[:2] == [n - 1, 0]` — nested, not stacked | `[BEHAVIORAL]` | `python -m pytest tests/test_signatures.py -q -k saddle_order_outermost_sheet_carries_first_and_last_page` |
| 6 | `saddle_order` raises `ValueError` for non-positive-multiples of 4 | `[BEHAVIORAL]` | `python -m pytest tests/test_signatures.py -q -k "saddle_order_raises_on_non_multiple_of_4 or saddle_order_raises_on_non_positive"` |
| 7 | **`fold_reading_order` contains no static reference to `saddle_order`** (REQ-018) | `[MECHANICAL]` | `python -m pytest tests/test_signatures.py -q -k fold_reading_order_does_not_call_saddle_order` |
| 8 | Same check, standalone, without pytest — for a reviewer with only a shell | `[MECHANICAL]` | `python -c "import ast,sys; src=open('deckle/core/signatures.py',encoding='utf-8').read(); fns=[n for n in ast.walk(ast.parse(src)) if isinstance(n,ast.FunctionDef) and n.name=='fold_reading_order']; sys.exit('FAIL: fold_reading_order not found') if not fns else None; bad=[n for f in fns for n in ast.walk(f) if (isinstance(n,ast.Name) and n.id=='saddle_order') or (isinstance(n,ast.Attribute) and n.attr=='saddle_order')]; sys.exit('FAIL: fold_reading_order references saddle_order') if bad else print('OK')"` |
| 9 | **`fold_reading_order` does not reuse `saddle_order` at runtime either** (REQ-018) | `[MECHANICAL]` | `python -m pytest tests/test_signatures.py -q -k fold_reading_order_works_with_saddle_order_disabled` |
| 10 | No I/O and no Qt in `signatures.py` (REQ-040) | `[MECHANICAL]` | `! grep -nE "^import (os\|io)$\|open\(\|requests\|urllib\|socket\|PySide6" deckle/core/signatures.py \|\| (echo "FAIL: I/O or Qt in signatures.py" && exit 1)` |
| 11 | No Qt import anywhere in `deckle.core` — anchored to import statements, because the unanchored form matches the "must not import PySide6" docstrings at `models.py:5` and `__init__.py:3` and fails on a clean tree | `[MECHANICAL]` | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 12 | `signatures.py` does not import `layout.py` — SS-08's `layout.py` imports this module, so the reverse edge is a cycle | `[MECHANICAL]` | `! grep -nE "^[[:space:]]*(import\|from)[[:space:]]+deckle\.core\.layout" deckle/core/signatures.py \|\| (echo "FAIL: signatures.py imports layout.py" && exit 1)` |
| 13 | No pikepdf and no drawing API in this pure-arithmetic module | `[MECHANICAL]` | `! grep -nE "^[[:space:]]*(import\|from)[[:space:]]+pikepdf" deckle/core/signatures.py \|\| (echo "FAIL: pikepdf imported into pure arithmetic" && exit 1)` |
| 14 | `tests/test_signatures.py` passes with no fewer than 12 tests | `[MECHANICAL]` | `python -m pytest tests/test_signatures.py -q && test "$(python -m pytest tests/test_signatures.py --collect-only -q 2>/dev/null \| grep -c '::')" -ge 12` |
| 15 | `deckle.core` stays pure on import | `[MECHANICAL]` | `python -m pytest tests/test_core_purity.py -q` |
| 16 | Lint is clean | `[MECHANICAL]` | `python -m ruff check deckle tests` |
| 17 | Nothing else moved; no fewer than 237 tests collected | `[MECHANICAL]` | `python -m pytest -q && test "$(python -m pytest --collect-only -q 2>/dev/null \| grep -c '::')" -ge 237` |

**Executed against the current tree at authoring time:** checks 10, 11, 12, 13, 15, 16 and the
`python -m pytest -q` half of 17 were run and each exited 0. Checks 1–9 and 14 exercise code
this sub-spec creates and therefore cannot pass before it lands; their **command forms** were
verified against existing modules — the AST walk of check 8 was run against
`deckle/core/layout.py::document_scale` and exited 0, the `inspect` form of check 1 against
`deckle.core.layout`, and the `--collect-only | grep -c '::'` counting form of check 14 against
`tests/test_models.py`. `pytest -k <missing-name>` exits 5, so checks 2–7 and 9 fail loudly
rather than passing vacuously if a test is renamed.
