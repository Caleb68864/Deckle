# B8 — One creep predicate, judged against the signatures actually made

**Roadmap item:** `docs/ROADMAP.md` B8
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none. §3 names the predicate, its home
(`paper.py`), its operator (`>`, so "creep equal to the tolerance is
absorbed") and its three call sites.

---

## 1. Context

Fore-edge creep — how far the innermost leaf of a nested gathering
protrudes — is judged in three places, and no two of them agree.

| Where | Sheets it uses | Tolerance | Operator |
|---|---|---|---|
| `layout._creep_advisory` | `settings.sheets_per_signature` | `trim_pt or 1.0` | silent when `creep <= tolerance` |
| `schedule._creep_note` | the widest signature **actually built** | always `1.0` | silent when `creep < 1.0` |
| `paper.suggest_sheets_per_signature` | solves for the sheet count | `trim_pt or 1.0` | admits `n` while `(n-1)·caliper <= tolerance` |

So `layout` asks about a setting the grouping may have overridden,
`schedule` ignores the planned trim entirely, and the two disagree at the
boundary because one uses `<=` and the other `<`.

All three disagreements verified on the same document, imposed once:

```bash
.venv/bin/python - <<'PYEOF'
from deckle.core.layout import SaddleStitchStrategy
from deckle.core.schedule import build_schedule
from deckle.core.models import LayoutSettings, SourcePage, SourceRef

def pages(n):
    return [SourcePage(ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                                     width_pt=396.0, height_pt=612.0),
                       rotate_deg=0, skipped=False) for i in range(n)]

def report(label, n, **over):
    base = dict(paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
                fold_scheme="folio")
    base.update(over)
    s = LayoutSettings(**base)
    plan = SaddleStitchStrategy().impose(pages(n), s)
    layout_says = [w.detail for w in plan.warnings if w.kind == "creep_advisory"]
    sched_says = [x for x in build_schedule(plan, s).notes if "creep" in x.lower()]
    print(f"{label}\n  signature sizes: {[len(g.sheet_indices) for g in plan.signatures]}"
          f"\n  layout:   {layout_says}\n  schedule: {sched_says}\n")

report("A: signature_lengths overrides sheets_per_signature", 32,
       sheets_per_signature=2, signature_lengths=(8,), paper_thickness_pt=0.27)
report("B: a planned trim silences one and not the other", 32,
       sheets_per_signature=8, paper_thickness_pt=0.27, trim_pt=36.0)
report("C: creep exactly at the 1.0pt threshold", 20,
       sheets_per_signature=5, paper_thickness_pt=0.25)
PYEOF
```

Output:

```
A: signature_lengths overrides sheets_per_signature
  signature sizes: [8]
  layout:   []
  schedule: ['Fore-edge creep is about 1.9pt (0.03in) on the innermost leaf. Trim the fore-edge after sewing, or reduce sheets per signature.']

B: a planned trim silences one and not the other
  signature sizes: [4]
  layout:   []
  schedule: ['Fore-edge creep is about 1.9pt (0.03in) on the innermost leaf. Trim the fore-edge after sewing, or reduce sheets per signature.']

C: creep exactly at the 1.0pt threshold
  signature sizes: [4]
  layout:   []
  schedule: ['Fore-edge creep is about 1.0pt (0.01in) on the innermost leaf. Trim the fore-edge after sewing, or reduce sheets per signature.']
```

- **A.** The binder asked for one 8-sheet signature. `layout` computed
  creep from `sheets_per_signature=2` — `(2-1) × 0.27 = 0.27pt`, below
  the threshold, silent. `schedule` used the 8 sheets that were actually
  built and reported 1.9pt. Same plan, two answers.
- **B.** A 36pt trim is planned. `layout` treats that as the tolerance
  and stays quiet; `schedule` does not know about `trim_pt` at all and
  advises trimming — which is what the user already asked for.
- **C.** Creep is exactly 1.0pt. `layout` is silent (`1.0 <= 1.0`);
  `schedule` speaks (`1.0 < 1.0` is false). One point of physical
  difference cannot make a warning both necessary and unnecessary.

**Why it matters to a person printing a book.** Creep is the difference
between a fore-edge you plough once and a fore-edge that comes out
visibly stepped. Deckle answers the question twice on the same screen —
once as a warning badge in the preview, once in the schedule the binder
prints — and the two contradict each other. The user has to decide which
Deckle to believe, which means believing neither. `layout`'s own comment
already names this:

> This used to be `sheets * caliper`, which made it a third opinion on
> one physical quantity — `schedule._creep_note` and
> `paper.suggest_sheets_per_signature` both use this formula, and three
> numbers for one measurement is worse than none.

Case A is the same defect one level up: the *formula* was unified and the
*inputs* were not.

## 2. Current code

### `deckle/core/paper.py:138-143, 178-215` — the constant and the solver

```python
# Below this, fore-edge creep is invisible and needs no trimming. Shared
# with `schedule._creep_note`, which imports it rather than repeating the
# literal -- one physical threshold expressed twice is the duplication
# this codebase has already been bitten by four times over one platform
# ladder.
CREEP_INVISIBLE_PT = 1.0
```

```python
def suggest_sheets_per_signature(
    caliper_pt: float, trim_pt: float = 0.0
) -> SignatureSuggestion | None:
    ...
    if caliper_pt <= 0:
        return None
    tolerance = trim_pt if trim_pt > 0 else CREEP_INVISIBLE_PT
    by_creep = int(tolerance // caliper_pt) + 1
    by_fold = int((FOLD_BULK_LIMIT_MM * PT_PER_MM) // (2 * caliper_pt))
    sheets = max(1, min(by_creep, by_fold))
    return SignatureSuggestion(
        sheets=sheets,
        pages=sheets * 4,
        creep_pt=(sheets - 1) * caliper_pt,
        limited_by="creep" if by_creep <= by_fold else "fold",
    )
```

`by_creep = int(tolerance // caliper) + 1` is the largest `n` with
`(n - 1)·caliper <= tolerance` — i.e. it treats creep exactly equal to
the tolerance as acceptable. That is the `>` reading, and it is the one
the other two must adopt (§3).

### `deckle/core/layout.py:783-827` — the advisory

```python
def _creep_advisory(warnings: list[LayoutWarning], settings: LayoutSettings) -> None:
    """A never-applied advisory: predicted fore-edge creep, and the remedy.

    The **only** function in this module permitted to reference
    ``paper_thickness_pt`` -- enforced by an AST test in
    ``tests/test_layout_saddle.py``. Creep is measured and reported, never
    compensated in placement geometry: no ``Placement`` this module emits
    may differ because of this value.
    """
    if settings.paper_thickness_pt <= 0.0:
        return
    # `(sheets - 1) * caliper`: the outermost leaf is not pushed out by
    # anything, so a gathering of one sheet creeps by nothing. This used
    # to be `sheets * caliper`, which made it a third opinion on one
    # physical quantity -- `schedule._creep_note` and
    # `paper.suggest_sheets_per_signature` both use this formula, and
    # three numbers for one measurement is worse than none.
    creep = (settings.sheets_per_signature - 1) * settings.paper_thickness_pt
    tolerance = settings.trim_pt if settings.trim_pt > 0 else CREEP_INVISIBLE_PT
    if creep <= tolerance:
        return
    # The remedy used to be "halve it", which is not derived from anything
    # and says the same thing however many times it is taken. It is now
    # the real answer, from the same function the panel offers -- so the
    # advisory and the suggestion cannot disagree about one document.
    suggestion = suggest_sheets_per_signature(
        settings.paper_thickness_pt, trim_pt=settings.trim_pt
    )
    remedy = (
        f"; {suggestion.sheets} sheets per signature would keep it inside "
        + ("the trim" if settings.trim_pt > 0 else "what is visible")
        if suggestion and suggestion.sheets < settings.sheets_per_signature
        else ""
    )
    warnings.append(
        LayoutWarning(
            sheet_index=0,
            kind="creep_advisory",
            detail=(
                f"predicted fore-edge creep of {creep:.2f}pt over "
                f"{settings.sheets_per_signature} sheets per signature"
                f"{remedy}"
            ),
        )
    )
```

`settings.sheets_per_signature` appears **four** times: in the creep
arithmetic, in the remedy's comparison, and twice in the message. All
four are the requested number, not the built one.

### `deckle/core/layout.py:1039` — where it is called

```python
        _creep_advisory(warnings, settings)
```

Called after the `for sig_index, group in enumerate(groups)` loop, so
`groups` — the real signature sizes, from `_signature_sheet_groups` at
`:911-916` — is in scope and unused by it.

### `deckle/core/schedule.py:181-198` — the note

```python
def _creep_note(signature_sheets: int, thickness_pt: float) -> str | None:
    """An advisory about fore-edge creep, or ``None`` if it will not matter.

    Nested sheets push each other outward at the fore-edge: the innermost
    leaf protrudes by roughly (sheets - 1) x thickness. Under about a point
    it is invisible; past that the fore-edge wants trimming, and a binder
    would rather know before folding than after.
    """
    if thickness_pt <= 0 or signature_sheets <= 1:
        return None
    creep = (signature_sheets - 1) * thickness_pt
    if creep < CREEP_INVISIBLE_PT:
        return None
    return (
        f"Fore-edge creep is about {creep:.1f}pt "
        f"({creep / 72:.2f}in) on the innermost leaf. Trim the fore-edge "
        "after sewing, or reduce sheets per signature."
    )
```

No `trim_pt` parameter, and `<` where `layout` has `<=`.

### `deckle/core/schedule.py:240-249` — where it is called

```python
    notes: list[str] = []
    if signatures:
        widest = max(sig.sheet_count for sig in signatures)
        creep = _creep_note(widest, settings.paper_thickness_pt)
        if creep is not None:
            notes.append(creep)
        elif settings.paper_thickness_pt <= 0:
            notes.append(
                "Paper thickness is not set, so creep is not estimated. "
                "Measure your stock and set it if the fore-edge matters."
            )
```

`widest` is the correct input — the widest signature actually built. This
is the value `layout` should be using too.

### Every call site

`grep -rn "CREEP_INVISIBLE_PT\|suggest_sheets_per_signature\|_creep_note\|_creep_advisory" --include='*.py' .`
(excluding `.venv`):

| Site | Use |
|---|---|
| `deckle/core/paper.py:143` | `CREEP_INVISIBLE_PT` definition |
| `deckle/core/paper.py:206` | tolerance inside `suggest_sheets_per_signature` |
| `deckle/core/paper.py:178` | `suggest_sheets_per_signature` definition |
| `deckle/core/layout.py:21` | imports both from `paper` |
| `deckle/core/layout.py:801` | tolerance |
| `deckle/core/layout.py:808` | remedy |
| `deckle/core/layout.py:783, 1039` | `_creep_advisory` and its call |
| `deckle/core/schedule.py:26` | imports `CREEP_INVISIBLE_PT` |
| `deckle/core/schedule.py:192` | threshold |
| `deckle/core/schedule.py:181, 243` | `_creep_note` and its call |
| `deckle/app/views/layout_panel.py` | `suggest_sheets_per_signature` for the gathering suggestion — `grep -n suggest_sheets_per_signature deckle/app/views/layout_panel.py` |
| `tests/test_layout_saddle.py:370-377` | `test_the_creep_remedy_is_the_size_the_panel_would_suggest` |
| `tests/test_paper.py` | the solver's own tests |

### Existing tests that pin this

- `tests/test_layout_saddle.py:348-363` —
  `test_creep_advisory_names_trim_and_remedy`: 32 pages,
  `paper_thickness_pt=0.27`, `sheets_per_signature=8`. Asserts `"1.89"`
  and `"sheets per signature"` in the detail. 32 pages is 8 sheets and
  `sheets_per_signature=8`, so the requested and the built number are the
  same — the test is blind to case A by construction.
- `tests/test_layout_saddle.py:365-377` —
  `test_the_creep_remedy_is_the_size_the_panel_would_suggest`.
- `tests/test_layout_saddle.py:380-387` —
  `test_negligible_creep_says_nothing_at_all` (`0.01pt` caliper).
- `tests/test_layout_saddle.py:390-397` —
  `test_a_planned_trim_absorbs_the_creep_and_silences_the_advisory`
  (`trim_pt=36.0`). **This is case B from `layout`'s side**, and it is
  the assertion that makes `schedule`'s silence on `trim_pt` a
  contradiction rather than an oversight.
- `tests/test_layout_saddle.py:400-402` —
  `test_zero_paper_thickness_emits_no_creep_advisory`.
- `tests/test_layout_saddle.py:405-412` —
  `test_creep_never_affects_placement_geometry`.
- `tests/test_layout_saddle.py:415-439` —
  `test_creep_references_are_isolated_to_creep_advisory`, an AST walk
  asserting every `paper_thickness_pt` reference in `layout.py` sits
  inside `_creep_advisory`. **§8.**
- `tests/test_schedule.py:168-188` — `test_creep_is_estimated_when_paper_thickness_is_known`
  (asserts `"3.5"`), `test_an_unset_paper_thickness_says_so_rather_than_estimating_zero`,
  `test_a_single_sheet_signature_has_no_creep`.

## 3. Change

### 3.1 The predicate

One function, in `deckle/core/paper.py`, beside `CREEP_INVISIBLE_PT` and
`suggest_sheets_per_signature` — the module that already owns the
threshold, the caliper arithmetic and the solver that inverts it.
`layout` and `schedule` both already import from it, so no new dependency
edge appears and `deckle/core/paper.py` gains no import at all.

```python
def creep_pt(sheets_per_signature: int, caliper_pt: float) -> float:
    """How far the innermost leaf of a nested gathering protrudes, in points.

    ``(sheets - 1) * caliper``: the outermost leaf is pushed out by
    nothing, so a gathering of one sheet creeps by nothing.

    :param sheets_per_signature: sheets nested into one gathering.
    :param caliper_pt: one sheet's thickness.
    :returns: the protrusion in points, ``0.0`` when either input is
        non-positive -- there is nothing to estimate, and a negative
        answer would read as "the fore-edge is inset".
    """
    if sheets_per_signature <= 1 or caliper_pt <= 0.0:
        return 0.0
    return (sheets_per_signature - 1) * caliper_pt


def creep_tolerance_pt(trim_pt: float = 0.0) -> float:
    """How much creep this job can absorb before it is worth mentioning.

    A planned fore-edge trim absorbs creep, so someone who is going to
    plough the block can carry far more of it than someone who is not;
    without a trim the threshold is the point below which it is simply
    invisible.

    :param trim_pt: the planned fore-edge trim, or ``0.0`` for none.
    :returns: the tolerance in points.
    """
    return trim_pt if trim_pt > 0 else CREEP_INVISIBLE_PT


def creep_is_worth_reporting(
    sheets_per_signature: int, caliper_pt: float, trim_pt: float = 0.0
) -> bool:
    """Whether this gathering's creep should be said out loud.

    **The single predicate.** Three places used to answer this and no two
    agreed: ``layout._creep_advisory`` judged the *requested*
    ``sheets_per_signature`` even when ``signature_lengths`` or
    ``blank_mode="balanced"`` had overridden it, ``schedule._creep_note``
    ignored ``trim_pt`` entirely, and the two used different operators at
    the boundary -- so a 0.25pt stock in 5-sheet gatherings, creeping
    exactly ``CREEP_INVISIBLE_PT``, produced a warning in the schedule and
    silence in the layout.

    **Strictly greater than.** Creep exactly equal to the tolerance is
    absorbed, not reported -- which is the reading
    :func:`suggest_sheets_per_signature` already had built into
    ``int(tolerance // caliper_pt) + 1``, and a suggestion that recommended
    a gathering its own advisory then warned about would be the fourth
    opinion this consolidation exists to remove.

    :param sheets_per_signature: sheets in the gathering **as built**, not
        as requested.
    :param caliper_pt: one sheet's thickness.
    :param trim_pt: the planned fore-edge trim, or ``0.0``.
    :returns: whether to report.
    """
    return creep_pt(sheets_per_signature, caliper_pt) > creep_tolerance_pt(trim_pt)
```

### 3.2 The three call sites

1. **`paper.suggest_sheets_per_signature`** — uses `creep_tolerance_pt`
   and `creep_pt` so the solver and the predicate cannot drift.
2. **`layout._creep_advisory`** — takes the built signature sizes and
   judges the **widest** one, the same input `schedule` already uses.
3. **`schedule._creep_note`** — takes `trim_pt` and calls the predicate.

### 3.3 Numbered edits

**`deckle/core/paper.py`**

1. Insert `creep_pt`, `creep_tolerance_pt` and `creep_is_worth_reporting`
   (as written in §3.1) immediately after the `CREEP_INVISIBLE_PT`
   definition at line 143 and before the `FOLD_BULK_LIMIT_MM` comment at
   line 145.

2. In `suggest_sheets_per_signature`, replace lines 206 and 213:

   ```python
       tolerance = trim_pt if trim_pt > 0 else CREEP_INVISIBLE_PT
   ```
   →
   ```python
       tolerance = creep_tolerance_pt(trim_pt)
   ```

   ```python
           creep_pt=(sheets - 1) * caliper_pt,
   ```
   →
   ```python
           creep_pt=creep_pt(sheets, caliper_pt),
   ```

   **Name collision:** `SignatureSuggestion.creep_pt` is a field and
   `creep_pt` is now a module function, so inside the
   `SignatureSuggestion(...)` constructor call the keyword `creep_pt=` and
   the function `creep_pt(...)` appear on one line. That is legal Python
   (the keyword is not a name lookup) and it reads badly. Rename the
   **function** to `creep_pt` and keep the field: the field is public API
   (`SignatureSuggestion.creep_pt` is documented at
   `deckle/core/paper.py:165-166` and read by the layout panel), and a
   module-level function shadowing nothing is fine. If the collision is
   judged too subtle, the alternative is to leave line 213 as the literal
   arithmetic — **do not** do that; it re-creates the duplication.

**`deckle/core/layout.py`**

3. Line 21, extend the import:

   ```python
   from deckle.core.paper import CREEP_INVISIBLE_PT, suggest_sheets_per_signature
   ```
   →
   ```python
   from deckle.core.paper import (
       creep_is_worth_reporting,
       creep_pt,
       suggest_sheets_per_signature,
   )
   ```

   `CREEP_INVISIBLE_PT` is no longer referenced in `layout.py` after step
   4; drop it. (Confirm with the grep in §5.)

4. Replace `_creep_advisory` (lines 783-827) with:

   ```python
   def _creep_advisory(
       warnings: list[LayoutWarning],
       settings: LayoutSettings,
       groups: Sequence[Sequence[int]],
   ) -> None:
       """A never-applied advisory: predicted fore-edge creep, and the remedy.

       The **only** function in this module permitted to reference
       ``paper_thickness_pt`` -- enforced by an AST test in
       ``tests/test_layout_saddle.py``. Creep is measured and reported, never
       compensated in placement geometry: no ``Placement`` this module emits
       may differ because of this value.

       Judged against the **widest signature actually built**, not against
       ``settings.sheets_per_signature``. Those differ whenever
       ``signature_lengths`` states a grouping or ``blank_mode="balanced"``
       reshapes one -- and the schedule has always used the built sizes, so
       a binder who asked for one 8-sheet signature with
       ``sheets_per_signature=2`` got a schedule warning about 1.9pt of
       creep and a preview that said nothing at all.

       :param warnings: appended to in place.
       :param settings: read for caliper and trim.
       :param groups: the signature sheet groupings, as
           ``_signature_sheet_groups`` returned them.
       """
       sheets = max((len(group) for group in groups), default=0)
       creep = creep_pt(sheets, settings.paper_thickness_pt)
       if not creep_is_worth_reporting(
           sheets, settings.paper_thickness_pt, settings.trim_pt
       ):
           return
       # The remedy used to be "halve it", which is not derived from anything
       # and says the same thing however many times it is taken. It is now
       # the real answer, from the same function the panel offers -- so the
       # advisory and the suggestion cannot disagree about one document.
       suggestion = suggest_sheets_per_signature(
           settings.paper_thickness_pt, trim_pt=settings.trim_pt
       )
       remedy = (
           f"; {suggestion.sheets} sheets per signature would keep it inside "
           + ("the trim" if settings.trim_pt > 0 else "what is visible")
           if suggestion and suggestion.sheets < sheets
           else ""
       )
       warnings.append(
           LayoutWarning(
               sheet_index=0,
               kind="creep_advisory",
               detail=(
                   f"predicted fore-edge creep of {creep:.2f}pt over "
                   f"{sheets} sheets per signature"
                   f"{remedy}"
               ),
           )
       )
   ```

   The `paper_thickness_pt <= 0.0` early return is gone: `creep_pt`
   returns `0.0` for a non-positive caliper and `0.0 > 1.0` is false, so
   the predicate covers it. `default=0` on the `max` covers an empty
   `groups`, which `_signature_sheet_groups` returns for `sheet_count <= 0`.

5. Line 1039, replace

   ```python
           _creep_advisory(warnings, settings)
   ```

   with

   ```python
           _creep_advisory(warnings, settings, groups)
   ```

   `groups` is in scope from line 911.

**`deckle/core/schedule.py`**

6. Line 26, replace

   ```python
   from deckle.core.paper import CREEP_INVISIBLE_PT
   ```

   with

   ```python
   from deckle.core.paper import creep_is_worth_reporting, creep_pt
   ```

7. Replace `_creep_note` (lines 181-198) with:

   ```python
   def _creep_note(
       signature_sheets: int, thickness_pt: float, trim_pt: float = 0.0
   ) -> str | None:
       """An advisory about fore-edge creep, or ``None`` if it will not matter.

       Nested sheets push each other outward at the fore-edge: the innermost
       leaf protrudes by roughly (sheets - 1) x thickness. Under about a point
       it is invisible; past that the fore-edge wants trimming, and a binder
       would rather know before folding than after.

       ``trim_pt`` is what this used to be missing. A binder who has
       already planned to plough the fore-edge is told to plough the
       fore-edge, which is not advice -- and the layout's advisory has
       honoured the trim since it was written, so the same document
       produced a warning in the schedule and silence in the preview.
       """
       if not creep_is_worth_reporting(signature_sheets, thickness_pt, trim_pt):
           return None
       creep = creep_pt(signature_sheets, thickness_pt)
       return (
           f"Fore-edge creep is about {creep:.1f}pt "
           f"({creep / 72:.2f}in) on the innermost leaf. Trim the fore-edge "
           "after sewing, or reduce sheets per signature."
       )
   ```

8. Line 243, replace

   ```python
           creep = _creep_note(widest, settings.paper_thickness_pt)
   ```

   with

   ```python
           creep = _creep_note(
               widest, settings.paper_thickness_pt, settings.trim_pt
           )
   ```

9. **`docs/api/`** — nothing to add; `core.paper.rst` exists.

### 3.4 What changes, per repro case

| Case | `layout` before → after | `schedule` before → after |
|---|---|---|
| A (`signature_lengths=(8,)`, requested 2) | silent → **warns, 1.89pt over 8 sheets** | warns 1.9pt → warns 1.9pt |
| B (`trim_pt=36.0`) | silent → silent | warns → **silent** |
| C (creep exactly 1.0pt) | silent → silent | warns → **silent** |
| `test_creep_advisory_names_trim_and_remedy` (8 requested, 8 built) | warns 1.89 → warns 1.89 | — |
| `test_creep_is_estimated_when_paper_thickness_is_known` (8 built, no trim, 3.5pt) | — | warns 3.5 → warns 3.5 |

Only the disagreements move.

## 4. Tests

Write these first; each fails as stated on the unfixed tree.

### `tests/test_paper.py`

**`test_creep_is_the_gap_the_innermost_leaf_opens`**
`creep_pt(8, 0.27) == pytest.approx(1.89)`; `creep_pt(1, 0.27) == 0.0`;
`creep_pt(0, 0.27) == 0.0`; `creep_pt(8, 0.0) == 0.0`;
`creep_pt(8, -1.0) == 0.0`.
Unfixed: `AttributeError: module 'deckle.core.paper' has no attribute 'creep_pt'`.

**`test_creep_equal_to_the_tolerance_is_absorbed_not_reported`**
`creep_is_worth_reporting(5, 0.25) is False` — `(5-1)×0.25 == 1.0 ==
CREEP_INVISIBLE_PT` — and `creep_is_worth_reporting(6, 0.25) is True`
(1.25pt).
Unfixed: `AttributeError`.

**`test_a_planned_trim_raises_the_tolerance`**
`creep_is_worth_reporting(8, 0.27) is True` and
`creep_is_worth_reporting(8, 0.27, trim_pt=36.0) is False`.
Unfixed: `AttributeError`.

**`test_the_suggestion_never_recommends_a_gathering_it_would_warn_about`**
The consistency property, over a grid: for `caliper` in
`(0.05, 0.1, 0.2, 0.27, 0.5, 1.0, 2.0)` × `trim` in `(0.0, 9.0, 36.0)`,
take `s = suggest_sheets_per_signature(caliper, trim_pt=trim)` and assert
`creep_is_worth_reporting(s.sheets, caliper, trim) is False`.
This is the test that makes `>` load-bearing: with `>=` the suggestion
recommends a size its own advisory then warns about, wherever
`tolerance % caliper == 0`.
Unfixed: `AttributeError`.

### `tests/test_layout_saddle.py`

**`test_the_advisory_judges_the_signatures_that_were_actually_built`**
`impose(make_pages(32), settings(sheets_per_signature=2, signature_lengths=(8,), paper_thickness_pt=0.27))`.
Assert exactly one `creep_advisory` warning and `"1.89"` and
`"8 sheets per signature"` in its detail.
Unfixed: `assert 0 == 1` — there is no warning at all.

**`test_balanced_grouping_is_judged_as_grouped`**
`impose(make_pages(40), settings(sheets_per_signature=8, blank_mode="balanced", paper_thickness_pt=0.27))`.
10 sheets in groups of at most 8, balanced → sizes `[5, 5]`. Assert the
advisory's detail names `5 sheets per signature`, not 8, and that the
creep figure is `(5-1) × 0.27 = 1.08`.
Unfixed: the detail names 8 sheets and 1.89pt.

**`test_creep_exactly_at_the_threshold_is_silent`**
`impose(make_pages(20), settings(sheets_per_signature=5, paper_thickness_pt=0.25))`
— creep is exactly 1.0pt. Assert no `creep_advisory` warning **and**, via
`build_schedule(plan, settings)`, no creep note either. The whole point
is that the two agree.
Unfixed: `layout` is silent and `schedule` is not, so the schedule half
fails: `assert ['Fore-edge creep is about 1.0pt …'] == []`.

**`test_creep_advisory_names_trim_and_remedy`** — the existing test at
`:348`. `sheets_per_signature=8` and 32 pages give 8 built sheets, so it
is unchanged and must stay green.

### `tests/test_schedule.py`

**`test_a_planned_trim_silences_the_creep_note`**
`_folio_schedule(32, sheets_per_signature=8, paper_thickness_pt=0.27, trim_pt=36.0)`.
Assert no note contains `"creep"`. The binder already asked to plough the
fore-edge; telling them to plough the fore-edge is not advice.
Unfixed: the note is present. `assert ['Fore-edge creep is about 1.9pt …'] == []`.

**`test_the_schedule_and_the_layout_agree_about_creep`**
The invariant, parametrised over a grid that hits every disagreement:
`(sheets_per_signature, signature_lengths, blank_mode, trim_pt, thickness)`
across at least
`(2, (8,), "end", 0.0, 0.27)`, `(8, None, "end", 36.0, 0.27)`,
`(5, None, "end", 0.0, 0.25)`, `(8, None, "balanced", 0.0, 0.27)` and
`(4, None, "end", 0.0, 0.0)`. For each, impose once and assert
`bool([w for w in plan.warnings if w.kind == "creep_advisory"])` equals
`bool([n for n in build_schedule(plan, s).notes if "creep is about" in n])`.
Unfixed: fails on the first three rows.

**`test_creep_is_estimated_when_paper_thickness_is_known`** — the
existing test at `:168`. 64 pages at 8 per signature is 16 sheets in
groups of 8, so both readings give 8 and it is unchanged.

## 5. Acceptance

| Check | Command |
|---|---|
| the predicate exists once | `test "$(grep -c 'def creep_is_worth_reporting' deckle/core/paper.py)" = 1` |
| and nowhere else | `! grep -n 'def creep_is_worth_reporting' deckle/core/layout.py deckle/core/schedule.py` |
| `layout` no longer knows the threshold | `! grep -n "CREEP_INVISIBLE_PT" deckle/core/layout.py` (matches `deckle/core/layout.py:21,801` today) |
| `schedule` no longer knows the threshold | `! grep -n "CREEP_INVISIBLE_PT" deckle/core/schedule.py` (matches `deckle/core/schedule.py:26,192` today) |
| only `paper.py` names the constant | `test "$(grep -rln CREEP_INVISIBLE_PT deckle/ \| wc -l)" = 1` |
| there is one creep formula | `! grep -rn -- "- 1) \* thickness_pt\|- 1) \* settings.paper_thickness_pt\|- 1) \* caliper_pt" deckle/` (matches `deckle/core/layout.py:800`, `deckle/core/schedule.py:191` and `deckle/core/paper.py:213` today) |
| the advisory takes the built groups | `grep -n "_creep_advisory(warnings, settings, groups)" deckle/core/layout.py` |
| the note takes the trim | `grep -n "settings.paper_thickness_pt, settings.trim_pt" deckle/core/schedule.py` |
| the repro from §1 agrees on all three cases | paste §1's fenced block; A must show a `layout:` entry naming `1.89pt over 8 sheets`, B and C must show `layout: []` **and** `schedule: []` |
| the new paper tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_paper.py -q --no-header -p no:cacheprovider -k creep` |
| the new layout tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k creep` |
| the new schedule tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_schedule.py -q --no-header -p no:cacheprovider -k creep` |
| the AST isolation guard still holds | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k creep_references_are_isolated` |
| creep still never moves a placement | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k never_affects_placement` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

- **B18** — `sheets_per_signature <= 0` under `blank_mode="balanced"`.
  Same file, and `_signature_sheet_groups` produces the `groups` this
  spec now reads, but a different function. Note the interaction: after
  B8, a `groups` of `[]` gives `max(..., default=0)` → `creep_pt(0, …)` →
  `0.0` → silent, which is correct. Write B8 so that holds whether or not
  B18 has landed.
- **B7** — the schedule's page numbering. Same file, different function.
- **The `FOLD_BULK_LIMIT_MM` cap** and the `limited_by` field. Untouched.
- **The advisory's `sheet_index=0`.** It is a document-level warning
  attached to sheet 0 so the preview badge has somewhere to put it; that
  is a separate question and not this one.
- **The panel's gathering suggestion** (`layout_panel`, B29's territory).
  It calls `suggest_sheets_per_signature`, whose behaviour is unchanged
  by this spec — only its internals are refactored onto the shared
  helpers.
- **`schedule`'s "Paper thickness is not set" note** at
  `deckle/core/schedule.py:246-250`. Its `elif` still keys on
  `settings.paper_thickness_pt <= 0` and stays exactly as it is.

## 7. decisions.md entry

```
## 2026-09-05 — Three creep opinions became one predicate
- Symptom: `layout._creep_advisory` judged `settings.sheets_per_signature` even when `signature_lengths` or `blank_mode="balanced"` had decided the real sizes; `schedule._creep_note` used the sizes actually built but ignored `trim_pt`; and the two compared against the threshold with different operators. Measured on one imposed document each time: 32 pages with `signature_lengths=(8,)` and `sheets_per_signature=2` gave layout silence and a schedule warning of 1.9pt; a planned 36pt trim silenced layout and not schedule; creep of exactly 1.0pt was silent in layout (`<=`) and reported in schedule (`<`).
- Fix: `paper.creep_pt`, `paper.creep_tolerance_pt` and `paper.creep_is_worth_reporting` -- one formula, one tolerance, one operator -- in the module that already owned `CREEP_INVISIBLE_PT` and the solver that inverts it. `_creep_advisory` now takes the `groups` the imposer actually built and judges the widest; `_creep_note` takes `trim_pt`. The operator is strictly greater than, because `suggest_sheets_per_signature`'s `int(tolerance // caliper) + 1` already meant that, and a suggestion that recommends a gathering its own advisory then warns about would be the fourth opinion.
- Surfaces: The comment already standing in `_creep_advisory` says "three numbers for one measurement is worse than none" -- written when the *formula* was unified. The inputs were not, which is the same defect one level up: `(sheets - 1) * caliper` agreed everywhere while `sheets` meant three different things.
- Watch: A shared constant is not a shared decision. `CREEP_INVISIBLE_PT` was imported by both modules with a comment explaining why the literal must not be repeated -- and the comparison around it was still written twice, with different operators, against different inputs. Extract the predicate, not the number. `tests/test_schedule.py::test_the_schedule_and_the_layout_agree_about_creep` is now the guard, parametrised over the five shapes that disagreed.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless.
- **`tests/test_layout_saddle.py::test_creep_references_are_isolated_to_creep_advisory`
  is an AST walk over the whole of `layout.py`** and flags *any*
  `ast.Name` or `ast.Attribute` node called `paper_thickness_pt` outside
  `_creep_advisory`'s line range. Step 4 keeps every reference inside the
  function, which is why `_creep_advisory` takes `settings` and `groups`
  rather than a pre-computed caliper. **Do not** hoist
  `settings.paper_thickness_pt` up to the call site at line 1039 — the
  test will go red and it is right to.
- **`groups` is `list[tuple[int, ...]]`** from `_signature_sheet_groups`
  (`deckle/core/layout.py:745-780`), and it can be empty
  (`sheet_count <= 0` returns `[]`). `max(..., default=0)` is required.
- **`Sequence` is already imported** into `layout.py`
  (`deckle/core/layout.py:13`), so the new parameter annotation needs no
  new import.
- **The `remedy` clause's comparison changes meaning.** It was
  `suggestion.sheets < settings.sheets_per_signature`; it becomes
  `suggestion.sheets < sheets`, i.e. against the built size. Under
  `signature_lengths` those differ and the built size is the one the
  remedy is about.
- **`schedule._creep_note` gains a third parameter with a default.** The
  default exists so the function reads sensibly on its own, not so a
  caller can omit it — there is exactly one caller and step 8 passes the
  trim. If a second caller ever appears without it, the schedule silently
  goes back to disagreeing with the layout.
- **`deckle/core/paper.py` must not import Qt** and currently imports
  only `dataclasses` and `typing` (`tests/test_core_purity.py`). Add
  nothing.
- **Do not "simplify" `creep_is_worth_reporting` into `layout` or
  `schedule`.** The whole point is that it has one home. A `[MECHANICAL]`
  grep in §5 enforces it.
