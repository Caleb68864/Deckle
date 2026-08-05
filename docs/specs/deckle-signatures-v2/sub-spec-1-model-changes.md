---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
contracts: "./contracts.yaml"
sub_spec_id: SS-01
sub_spec_number: 1
title: "Model changes — Side, Mark, Signature, SheetPlan.signatures, LayoutSettings"
depends_on: []
date: 2026-08-04
---

# SS-01 — Model changes: `Side`, `Mark`, `Signature`, `SheetPlan.signatures`, `LayoutSettings`

## Context

**What this does:** adds the pure-data vocabulary v2 needs to `deckle/core/models.py`, and
nothing else. No behaviour, no geometry, no drawing — only shapes. Every other sub-spec in
this feature depends on this one.

**Why it is first, and why it is small:** a sheet side now carries **two** pages instead of
one, so `Sheet.front` / `Sheet.back` change type from `OutputPage | None` to `Side | None`.
That is the single real model change in v2, and the master spec's *Context* section calls it
out as such. Everything else here (`Mark`, `Signature`, `SheetPlan.signatures`, the four new
`LayoutWarning` kinds, the five new `LayoutSettings` fields) is vocabulary the later sub-specs
consume but do not get to invent.

**Contracts this sub-spec owns.** From the master spec's *Contracts* table, implement
contracts **1, 2, 3, 4, 5 and 10 verbatim** — `Side`, `Sheet`, `Signature`, `SheetPlan`,
`Mark`, and the new `LayoutSettings` fields. `contracts.yaml` names SS-01 as `owner` for
`Side`, `Sheet`, `Signature`, `SheetPlan.signatures`, `Mark` and `LayoutSettings.v2_fields`.
Deviating from any committed default is an escalation, not a decision.

**The one invariant that carries real weight.** An absent side is `None`. `Side(pages=())` is
invalid and `__post_init__` must raise `ValueError`. This is evaluation finding C-1 and it is
load-bearing rather than tidy: `deckle/core/print_session.py:79-80` hashes a plan on
`s.front is not None` / `s.back is not None`, and SS-04 extends that to each side's page
indices. If an absent side could be represented as an empty `Side`, the code would still
compile, the tests would still pass, and `_hash_plan` would be silently wrong — two materially
different plans hashing identically, and a resumed `PrintSession` binding to the wrong
document. SS-04's zero-diff and hash-correctness claims both rest on this precondition, so it
is stated here, enforced in `__post_init__`, tested behaviourally, and grepped for repo-wide.

**A fact that shapes the step ordering.** `deckle/core/models.py:9` is
`from __future__ import annotations`, so every annotation in the module is a lazily-evaluated
string. Retyping `Sheet.front` to `Side | None` therefore breaks **nothing at runtime**: the
full 237-test suite stays green through SS-01 alone. The tree only genuinely breaks in SS-02,
when `GutterShiftStrategy` starts constructing `Side` objects and `export._sides` starts
iterating `side.pages`. This is why SS-01 can and must end with a fully green
`python -m pytest -q`, and why the master spec's "this sub-spec deliberately breaks the tree"
note describes the *type* contract, not an observable runtime failure. Do not weaken any
existing test to accommodate an imagined breakage that does not occur.

**Decision-log entries that bind here.** *`slack_to` replaces the `maximize_gutter` boolean*
— a shape that cannot express the real question makes part of the answer space unreachable;
`Side.pages` is therefore a tuple of N, never "a page plus an extra". *Negative-assertion
acceptance criteria must exit 0 when the pattern is absent* — every negative check in the
*Checks* table below is written `! grep … || (echo "FAIL: …" && exit 1)` and every one of them
was executed against the current tree and confirmed to exit 0 before being written down.

## Provides / Requires

**Provides:**

| Symbol | Module | Consumed by |
|---|---|---|
| `Side` | `deckle/core/models.py` | SS-02, SS-03, SS-04, SS-08, SS-09 |
| `Sheet` (retyped `front`/`back`) | `deckle/core/models.py` | SS-02, SS-03, SS-04, SS-08, SS-09 |
| `Mark` | `deckle/core/models.py` | SS-06, SS-08, SS-09 |
| `Signature` | `deckle/core/models.py` | SS-05, SS-08, SS-11, SS-12 |
| `SheetPlan.signatures` | `deckle/core/models.py` | SS-05, SS-08, SS-11, SS-12 |
| `LayoutWarning.kind` (four new literals) | `deckle/core/models.py` | SS-08, SS-10 |
| `LayoutSettings.fold_scheme` | `deckle/core/models.py` | SS-07, SS-08, SS-11 |
| `LayoutSettings.sheets_per_signature` | `deckle/core/models.py` | SS-05, SS-08, SS-11 |
| `LayoutSettings.paper_thickness_pt` | `deckle/core/models.py` | SS-08 (`_creep_advisory` only) |
| `LayoutSettings.sewing_stations` | `deckle/core/models.py` | SS-06, SS-08, SS-11 |
| `LayoutSettings.blank_mode` | `deckle/core/models.py` | SS-08, SS-11 |

**Requires:**

| Symbol | Owner | Why |
|---|---|---|
| `OutputPage` | MVP SS-01 (`deckle/core/models.py:49-55`) | `Side.pages` is a tuple of these |
| `Placement` | MVP SS-01 (`deckle/core/models.py:15-26`) | unchanged; referenced only through `OutputPage` |
| `LayoutWarning` | MVP SS-01 (`deckle/core/models.py:67-78`) | its `kind` literal is widened here |
| `LayoutSettings` | MVP SS-01 (`deckle/core/models.py:90-153`) | gains exactly five fields |

Nothing outside `deckle/core/models.py` is required. This is the root of the v2 dependency
graph.

**Files (modify):**
- `deckle/core/models.py`
- `tests/test_models.py`

## Implementation Steps

Each step is 2–10 minutes. Follow the order; the failing test always precedes the code.

### Step 1. Write the failing `Side` tests

Add to `tests/test_models.py`, following the module's existing style — plain functions,
`dataclasses.fields` for shape assertions, `pytest.raises(dataclasses.FrozenInstanceError)`
for frozenness, and the module-local `make_source_ref()` helper at
`tests/test_models.py:20-27`. Add a local helper beside it:

```python
def make_output_page(page_index: int = 0) -> OutputPage:
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    return OutputPage(source_ref=make_source_ref(page_index), placement=placement, is_filler=False)
```

Three tests:

- `test_side_is_frozen_with_expected_fields` — asserts `dataclasses.is_dataclass(Side)`,
  `{f.name for f in dataclasses.fields(Side)} == {"pages", "marks"}`, that
  `Side(pages=(page,)).marks == ()` (the committed default), and that assigning to `.pages`
  raises `dataclasses.FrozenInstanceError`.
- `test_side_rejects_empty_pages` — asserts `pytest.raises(ValueError)` for `Side(pages=())`,
  and that the raised message mentions `None`, so the error names the invariant rather than
  restating the type. **This exact test name is a `[MECHANICAL]` acceptance criterion**
  (`-k side_rejects_empty_pages`); do not rename it.
- `test_side_holds_two_pages` — asserts `len(Side(pages=(a, b)).pages) == 2` and that
  `Side.pages` preserves order.

### Step 2. Run them and watch them fail

```bash
python -m pytest tests/test_models.py -q -k "side"
```

Expect a collection error: `ImportError: cannot import name 'Side' from 'deckle.core.models'`.
That is the correct red.

### Step 3. Implement `Mark` and `Side`

In `deckle/core/models.py`, insert both **after `OutputPage`** (currently ending at line 55)
and **before `Sheet`** (currently starting at line 58). `Mark` comes first so a reader meets
it before `Side.marks` references it.

- `Mark` — frozen dataclass, contract 5 verbatim:
  `kind: Literal["sewing_station", "signature_order", "fold_line"]`, then
  `x0: float, y0: float, x1: float, y1: float`. Docstring states: sheet points, PDF origin
  bottom-left, **a line segment in every case**. No colour, no width, no fill — stroke style
  is the renderer's concern (SS-09). Do not add a `"trim"` kind, a `filled` field or a
  `line_width_pt` field; the master spec's *Contracts* section closes all three explicitly.
- `Side` — frozen dataclass, contract 1 verbatim: `pages: tuple[OutputPage, ...]` and
  `marks: tuple[Mark, ...] = ()`, with:

```python
def __post_init__(self) -> None:
    if not self.pages:
        raise ValueError(
            "a Side must hold at least one page; an absent side is None, "
            "never Side(pages=())"
        )
```

  The docstring must state why: `print_session._hash_plan` reads side presence, and an empty
  `Side` would leave the code compiling, the tests passing, and the hash wrong.

### Step 4. Run to green

```bash
python -m pytest tests/test_models.py -q -k "side"
```

### Step 5. Write the failing `Sheet` / `Signature` / `SheetPlan` tests

Still in `tests/test_models.py`:

- Extend `test_sheet_fields_allow_none_front_and_back` (`tests/test_models.py:70-74`) — keep
  its existing assertions untouched and append a positive case:
  `Sheet(index=0, front=Side(pages=(page,)), back=None)`, asserting
  `sheet.front.pages[0] is page` and `sheet.back is None`.
- `test_sheet_sides_are_annotated_side_or_none` — reads
  `{f.name: str(f.type) for f in dataclasses.fields(Sheet)}` and asserts `"Side"` appears in
  both the `front` and `back` annotations. `deckle/core/models.py:9` is
  `from __future__ import annotations`, so `f.type` is the annotation **string** — assert on
  the string, exactly as `test_layout_settings_top_binding_edge_not_supported`
  (`tests/test_models.py:110-114`) already does.
- `test_signature_is_frozen_with_expected_fields` — asserts field names are exactly
  `{"index", "sheet_indices", "blank_count"}`, that a constructed `Signature` is frozen, and
  that **no** `source_page_count` field exists (`assert not hasattr(sig, "source_page_count")`,
  mirroring the `scale_mode` guard at `tests/test_models.py:101`).
- `test_sheet_plan_signatures_defaults_to_empty_tuple` — asserts
  `SheetPlan(sheets=[], paper_pt=(612.0, 792.0), warnings=[]).signatures == ()`. This is the
  `GutterShiftStrategy` case, and consumers must tolerate it rather than treat it as an error.

### Step 6. Run them and watch them fail

```bash
python -m pytest tests/test_models.py -q -k "signature or sheet"
```

### Step 7. Implement `Signature`, retype `Sheet`, extend `SheetPlan`

In `deckle/core/models.py`:

- Retype `Sheet` (`deckle/core/models.py:58-64`): `front: Side | None` and
  `back: Side | None`. Nothing else on `Sheet` changes — `index: int` stays, and
  `deckle/core/printing.py:121` reads only `s.index`, which is what keeps the manual-duplex
  seam zero-diff (SS-04).
- Add `Signature` — frozen dataclass, contract 3 verbatim: `index: int`,
  `sheet_indices: tuple[int, ...]`, `blank_count: int`. Docstring records the invariants from
  `contracts.yaml`: `sheet_indices` are contiguous and in binding order; across a plan they
  are gapless, non-overlapping and cover `range(len(plan.sheets))` exactly.
- Extend `SheetPlan` (`deckle/core/models.py:81-87`) with
  `signatures: tuple[Signature, ...] = ()`. It must be the **last** field, because
  `sheets`/`paper_pt`/`warnings` have no defaults and a defaulted field cannot precede them.
  Docstring: empty for `GutterShiftStrategy`; consumers tolerate an empty tuple.

### Step 8. Run to green

```bash
python -m pytest tests/test_models.py -q
```

### Step 9. Write the failing `LayoutWarning` / `LayoutSettings` tests

- `test_layout_warning_kind_accepts_v2_kinds` — asserts the `LayoutWarning.kind` annotation
  string contains all eight literals: the four existing (`clipped_by_page`,
  `clipped_by_imageable_area`, `mixed_orientation`, `mixed_dpi`) plus `sheet_orientation`,
  `signature_padding`, `creep_advisory`, `landscape_imageable_unverified`. Read it via
  `dataclasses.fields`, as above. Also construct one warning of each new kind to prove they
  are usable.
- `test_layout_settings_v2_defaults` — extends the existing
  `test_layout_settings_defaults` pattern (`tests/test_models.py:95-107`): asserts
  `fold_scheme == "none"`, `sheets_per_signature == 4`, `paper_thickness_pt == 0.0`,
  `sewing_stations == 3`, `blank_mode == "end"`.
- `test_layout_settings_adds_exactly_five_v2_fields` — computes
  `{f.name for f in dataclasses.fields(LayoutSettings)}` and asserts the difference against a
  hard-coded frozenset of the MVP's eleven field names is exactly the five committed names,
  and asserts none of `sewing_marks`, `sig_order_marks`, `fold_lines`, `signature_pattern`,
  `sewing_tape_width_pt`, `sewing_margin_pt` is present. A new `LayoutSettings` field is an
  "agent recommends, human approves" item; this test is the mechanism that stops one drifting
  in.
- `test_fold_scheme_literal_is_folio_not_folio_saddle` — asserts `"folio"` is in the
  annotation string and `"folio_saddle"` is not. The design's prose said `"folio_saddle"`; the
  committed contract says `"folio"`, and the committed contract wins.

### Step 10. Run them and watch them fail, then implement

```bash
python -m pytest tests/test_models.py -q -k "layout_warning_new_kinds or layout_settings_defaults"
```

Then in `deckle/core/models.py`:

- Widen `LayoutWarning.kind`'s `Literal` (`deckle/core/models.py:72-77`) with the four new
  values. Keep the four existing values first and in their existing order — this is an
  additive change.
- Append the five `LayoutSettings` fields **after `margins_linked`**
  (`deckle/core/models.py:147`), all defaulted, contract 10 verbatim:

```python
fold_scheme: Literal["none", "folio"] = "none"
sheets_per_signature: int = 4
paper_thickness_pt: float = 0.0
sewing_stations: int = 3
blank_mode: Literal["end", "balanced"] = "end"
```

  Document at `binding_edge` (`deckle/core/models.py:96`) that its meaning is
  strategy-dependent: under `fold_scheme="folio"` it selects **reading direction** (LTR/RTL),
  not gutter side, because a folio leaf's spine is fixed by its cell's position relative to
  the fold. Document at `paper_thickness_pt` that it **never** reaches placement geometry — it
  feeds the creep advisory only, and SS-08 confines every reference to it to a single function
  named `_creep_advisory`.

  Watch the forbidden-token hazard the master spec's *Edge Cases* records: do not name
  `sewing_marks`, `sig_order_marks`, `fold_lines`, `signature_pattern`, `sewing_tape_width_pt`
  or `sewing_margin_pt` in a comment or docstring in this file. A `[MECHANICAL]` check greps
  `deckle/core/models.py` for exactly those tokens, and it cannot tell your prose from a real
  field. Say "no additional binding-mark settings fields" instead.

### Step 11. Full suite green, then commit

```bash
python -m pytest tests/test_models.py tests/test_core_purity.py -q
python -m pytest -q
python -m ruff check deckle tests
git add -A && git commit -m "feat(SS-01): Side, Mark, Signature, SheetPlan.signatures, v2 LayoutSettings"
```

`python -m pytest -q` must still report **234 passed, 3 skipped** with at least 237 collected,
plus the new `tests/test_models.py` tests. Because `models.py` uses
`from __future__ import annotations`, retyping `Sheet.front` changes no runtime behaviour, so
nothing else in the suite may go red here. If something does, it has found a real problem —
stop and surface it rather than editing the failing test.

## Interface Contracts

### Side
- Direction: SS-01 → SS-02, SS-03, SS-04, SS-08, SS-09
- Owner: SS-01
- Shape: `@dataclass(frozen=True) class Side: pages: tuple[OutputPage, ...]; marks: tuple[Mark, ...] = ()`
- Invariant: `pages` is non-empty. `Side(pages=())` raises `ValueError` in `__post_init__`
  with a message naming the rule ("an absent side is None, never `Side(pages=())`"). An
  absent side is `None`.
- Consumers: `export._sides` iterates `side.pages`; `preview_view.clipping_warnings_for_sheet`
  iterates `side.pages` per side; `print_session._hash_plan` reads each page's
  `source_ref.page_index`.

### Sheet
- Direction: SS-01 → SS-02, SS-03, SS-04, SS-08, SS-09
- Owner: SS-01
- Shape: `@dataclass(frozen=True) class Sheet: index: int; front: Side | None; back: Side | None`
- Invariant: `index` is unchanged and remains the **only** attribute
  `deckle/core/printing.py` reads (`printing.py:121`). That is what makes the zero-diff seam
  in SS-04 possible.

### Mark
- Direction: SS-01 → SS-06, SS-08, SS-09
- Owner: SS-01
- Shape: `@dataclass(frozen=True) class Mark: kind: Literal["sewing_station","signature_order","fold_line"]; x0: float; y0: float; x1: float; y1: float`
- Invariant: sheet points, PDF origin bottom-left; a line segment in every case. No colour,
  width or fill. Kind is `"signature_order"`, never `"sig_order"`; there is no `"trim"` kind.

### Signature
- Direction: SS-01 → SS-05, SS-08, SS-11, SS-12
- Owner: SS-01
- Shape: `@dataclass(frozen=True) class Signature: index: int; sheet_indices: tuple[int, ...]; blank_count: int`
- Invariant: `sheet_indices` contiguous and in binding order. No `source_page_count` field.

### SheetPlan.signatures
- Direction: SS-01 → SS-05, SS-08, SS-11, SS-12
- Owner: SS-01
- Shape: `signatures: tuple[Signature, ...] = ()`, declared last on `SheetPlan`.
- Invariant: **empty** for `GutterShiftStrategy`. Consumers tolerate an empty tuple and must
  not treat it as an error condition.

### LayoutSettings v2 fields
- Direction: SS-01 → SS-05, SS-06, SS-07, SS-08, SS-11
- Owner: SS-01
- Shape: `fold_scheme: Literal["none","folio"] = "none"`; `sheets_per_signature: int = 4`;
  `paper_thickness_pt: float = 0.0`; `sewing_stations: int = 3`;
  `blank_mode: Literal["end","balanced"] = "end"`
- Invariant: exactly these five, with exactly these defaults. `paper_thickness_pt` never
  reaches placement geometry. `binding_edge` means reading direction under folio.

### LayoutWarning.kind
- Direction: SS-01 → SS-08, SS-10
- Owner: SS-01
- Shape: the four MVP literals plus `"sheet_orientation"`, `"signature_padding"`,
  `"creep_advisory"`, `"landscape_imageable_unverified"`.

## Verification Commands

Build check:

```bash
python -c "import deckle.core.models" || (echo "FAIL: models.py import failed" && exit 1)
python -m ruff check deckle tests
```

Test check:

```bash
python -m pytest tests/test_models.py -q
python -m pytest tests/test_core_purity.py -q
python -m pytest -q
```

Per-criterion acceptance check:

```bash
# REQ-001 / REQ-004 / REQ-005 -- the three new dataclasses exist and are frozen
for s in Side Mark Signature; do grep -q "class $s" deckle/core/models.py || { echo "FAIL: $s not defined"; exit 1; }; done

# REQ-003 -- an empty Side is rejected
python -m pytest tests/test_models.py -q -k side_rejects_empty_pages

# REQ-002 -- Sheet's sides are Side or None
grep -q "front: Side" deckle/core/models.py || (echo "FAIL: Sheet.front not retyped" && exit 1)

# REQ-006 -- SheetPlan carries signatures
grep -qE "signatures:[[:space:]]*tuple\[Signature, \.\.\.\][[:space:]]*=[[:space:]]*\(\)" deckle/core/models.py || (echo "FAIL: SheetPlan.signatures missing" && exit 1)

# REQ-008 -- exactly the five committed settings fields, no others
! grep -n "sewing_marks\|sig_order_marks\|fold_lines\|signature_pattern\|sewing_tape_width_pt\|sewing_margin_pt" deckle/core/models.py || (echo "FAIL: uncommitted LayoutSettings field added -- this is a human-approval escalation" && exit 1)

# The SS-04 precondition: no code anywhere constructs an empty Side
! grep -rnE "Side\(\s*(pages\s*=\s*)?\(\s*\)\s*\)" deckle/ tests/ || (echo "FAIL: Side(pages=()) constructed -- an absent side is None" && exit 1)
```

## Checks

Every negative check below was executed against the working tree at commit `b54194c` and
confirmed to exit 0. Positive `[STRUCTURAL]` checks assert symbols this sub-spec creates, so
they exit 1 before implementation and 0 after — that is their purpose; the shell idiom
(`grep -q … || (echo "FAIL: …" && exit 1)`) was itself verified against an existing symbol.

| # | Criterion | Type | Command |
|---|---|---|---|
| 1 | `Side` defined, frozen, with `pages`/`marks` (REQ-001) | [STRUCTURAL] | `grep -q "class Side" deckle/core/models.py && grep -q "pages: tuple\[OutputPage, \.\.\.\]" deckle/core/models.py && grep -q "marks: tuple\[Mark, \.\.\.\] = ()" deckle/core/models.py \|\| (echo "FAIL: Side shape does not match contract 1" && exit 1)` |
| 2 | `Sheet.front`/`back` are `Side` or `None` (REQ-002) | [STRUCTURAL] | `grep -q "front: Side" deckle/core/models.py && grep -q "back: Side" deckle/core/models.py \|\| (echo "FAIL: Sheet sides not retyped" && exit 1)` |
| 3 | `Side(pages=())` raises `ValueError` (REQ-003) | [MECHANICAL] | `python -m pytest tests/test_models.py -q -k side_rejects_empty_pages \|\| (echo "FAIL: empty Side is not rejected" && exit 1)` |
| 4 | No code constructs an empty `Side` (REQ-003, SS-04 precondition) | [MECHANICAL] | `! grep -rnE "Side\(\s*(pages\s*=\s*)?\(\s*\)\s*\)" deckle/ tests/ \|\| (echo "FAIL: Side(pages=()) constructed -- an absent side is None" && exit 1)` |
| 5 | `Mark` defined with the committed kinds and four floats (REQ-004) | [STRUCTURAL] | `grep -q "class Mark" deckle/core/models.py && grep -q '"sewing_station"' deckle/core/models.py && grep -q '"signature_order"' deckle/core/models.py && grep -q '"fold_line"' deckle/core/models.py \|\| (echo "FAIL: Mark shape does not match contract 5" && exit 1)` |
| 6 | `Mark` has no `sig_order` kind and no `trim` kind | [MECHANICAL] | `! grep -n '"sig_order"\|"trim"' deckle/core/models.py \|\| (echo "FAIL: uncommitted Mark kind present" && exit 1)` |
| 7 | `Signature` defined with exactly three fields (REQ-005) | [STRUCTURAL] | `grep -q "class Signature" deckle/core/models.py && grep -q "sheet_indices: tuple\[int, \.\.\.\]" deckle/core/models.py && grep -q "blank_count: int" deckle/core/models.py \|\| (echo "FAIL: Signature shape does not match contract 3" && exit 1)` |
| 8 | `Signature` has no `source_page_count` (contract conflict resolved) | [MECHANICAL] | `! grep -n "source_page_count" deckle/core/models.py \|\| (echo "FAIL: uncommitted Signature field added" && exit 1)` |
| 9 | `SheetPlan.signatures` declared, defaulting to `()` (REQ-006) | [STRUCTURAL] | `grep -qE "signatures:[[:space:]]*tuple\[Signature, \.\.\.\][[:space:]]*=[[:space:]]*\(\)" deckle/core/models.py \|\| (echo "FAIL: SheetPlan.signatures missing or not defaulted" && exit 1)` |
| 10 | `LayoutWarning.kind` gains all four v2 kinds (REQ-007) | [STRUCTURAL] | `for k in sheet_orientation signature_padding creep_advisory landscape_imageable_unverified; do grep -q "\"$k\"" deckle/core/models.py \|\| (echo "FAIL: LayoutWarning kind $k missing" && exit 1); done` |
| 11 | The four MVP warning kinds survive (REQ-007) | [STRUCTURAL] | `for k in clipped_by_page clipped_by_imageable_area mixed_orientation mixed_dpi; do grep -q "\"$k\"" deckle/core/models.py \|\| (echo "FAIL: MVP warning kind $k removed" && exit 1); done` |
| 12 | The five committed `LayoutSettings` fields, with committed defaults (REQ-008) | [STRUCTURAL] | `grep -q 'fold_scheme: Literal\["none", "folio"\] = "none"' deckle/core/models.py && grep -q "sheets_per_signature: int = 4" deckle/core/models.py && grep -q "paper_thickness_pt: float = 0.0" deckle/core/models.py && grep -q "sewing_stations: int = 3" deckle/core/models.py && grep -q 'blank_mode: Literal\["end", "balanced"\] = "end"' deckle/core/models.py \|\| (echo "FAIL: v2 LayoutSettings fields do not match contract 10" && exit 1)` |
| 13 | No uncommitted `LayoutSettings` field (REQ-008) | [MECHANICAL] | `! grep -n "sewing_marks\|sig_order_marks\|fold_lines\|signature_pattern\|sewing_tape_width_pt\|sewing_margin_pt" deckle/core/models.py \|\| (echo "FAIL: uncommitted LayoutSettings field added -- this is a human-approval escalation" && exit 1)` |
| 14 | The enum value is `folio`, not `folio_saddle` | [MECHANICAL] | `! grep -n "folio_saddle" deckle/core/models.py \|\| (echo "FAIL: fold_scheme value must be \"folio\"" && exit 1)` |
| 15 | `tests/test_models.py` passes | [MECHANICAL] | `python -m pytest tests/test_models.py -q \|\| (echo "FAIL: model tests red" && exit 1)` |
| 16 | Core purity holds (REQ-040) | [MECHANICAL] | `python -m pytest tests/test_core_purity.py -q \|\| (echo "FAIL: core purity violated" && exit 1)` |
| 17 | No Qt import in `deckle.core` (REQ-040) | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(PySide6\|PyQt)" deckle/core/ \|\| (echo "FAIL: Qt imported in deckle.core" && exit 1)` |
| 18 | `models.py` imports cleanly with stdlib only | [MECHANICAL] | `python -c "import deckle.core.models" \|\| (echo "FAIL: models.py import failed" && exit 1)` |
| 19 | Suite has not shrunk (REQ-039) | [MECHANICAL] | `test $(python -m pytest -q --collect-only 2>/dev/null \| grep -c "::") -ge 237 \|\| (echo "FAIL: suite collected fewer than 237 tests" && exit 1)` |
| 20 | Full suite green (REQ-039) | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: suite red after model changes" && exit 1)` |
| 21 | Lint clean (REQ-039) | [MECHANICAL] | `python -m ruff check deckle tests \|\| (echo "FAIL: ruff violations" && exit 1)` |

**Note on check 17 and the anchored grep:** the unanchored form `! grep -rn "PySide6" deckle/core/`
**fails on a clean tree** — `deckle/core/models.py:5` and `deckle/core/__init__.py:3` both
*mention* PySide6 in prose stating the rule. The anchored form above matches only real import
statements and was verified to exit 0 against the current tree. Do not un-anchor it.

**Note on `ruff`:** `ruff` is installed (0.15.13) but is not on `PATH` under Git Bash in this
environment; `python -m ruff check deckle tests` is the invocation verified to exit 0 here.
