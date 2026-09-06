# B3 — Make print-session resume detect a changed document

**Roadmap item:** `docs/ROADMAP.md` B3
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`PrintSession` stores a `plan_hash` so that resuming an interrupted
manual-duplex job onto a document that has been re-imposed underneath it is
refused rather than printed. The whole argument for storing it is in
`load`'s own docstring (`deckle/core/print_session.py:450-456`):

> Resuming onto a re-imposed document means printing backs against fronts
> that were laid out differently -- and on a printer with no duplexer,
> where the user has already physically reloaded the stack, the first sign
> of trouble is a ruined pile of paper.

The hash does not do that. `_hash_plan` covers sheet index, side presence
and the ordered list of source **page indices** per side, and nothing else.
Change the gutter, the margins, the paper size or the crop; or open a
different PDF with the same page count and the same page size; re-impose;
resume the back pass. Every one of those is accepted, and the backs print
with geometry that does not match the fronts already on the paper.

The concrete user action: print pass 1 of a 60-sheet folio, notice the
gutter is too tight, change it in the Layout panel from 36pt to 54pt, and
resume the interrupted session when the dialog offers it. Deckle resumes.
The backs land 18pt from where their fronts are.

Verified on the tree at `08e7f49`:

```bash
python -m deckle.cli dummy -o /tmp/d.pdf --pages 4
python - <<'EOF'
import dataclasses
from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings
from deckle.core.layout import GutterShiftStrategy
from deckle.core.print_session import _hash_plan
from deckle.core.export import _plan_hash

pages = load_pdf('/tmp/d.pdf')
base = LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0, binding_edge='left')
plan = lambda s: GutterShiftStrategy().impose(pages, s)
p0 = plan(base)
for name, s in {
    'gutter 54pt': dataclasses.replace(base, gutter_pt=54.0),
    'paper a4':    dataclasses.replace(base, paper=(595.28, 841.89)),
    'crop':        dataclasses.replace(base, crop_odd_pt=(36.0,)*4),
    'margins':     dataclasses.replace(base, margin_top_pt=36.0),
}.items():
    p = plan(s)
    print(f'{name:12} session_hash_same={_hash_plan(p) == _hash_plan(p0)} '
          f'content_hash_same={_plan_hash(p) == _plan_hash(p0)}')
EOF
```

Current output:

```
gutter 54pt  session_hash_same=True content_hash_same=False
paper a4     session_hash_same=True content_hash_same=False
crop         session_hash_same=True content_hash_same=False
margins      session_hash_same=True content_hash_same=False
```

The same check with two *different* 4-page Letter documents written to the
same path (so the plan differs only in `SourceRef.sha256`) also reports
`session_hash_same=True content_hash_same=False`.

`deckle.core.export._plan_hash` — the export cache key — already answers
every one of those correctly, because a cache that hands back the wrong
sheet is the same defect wearing different clothes.

## 2. Current code

`deckle/core/print_session.py:109-145`:

```python
def _hash_plan(plan: SheetPlan) -> str:
    """A stable hash identifying a plan's sheet content.

    Covers sheet index, side presence, and the **full ordered sequence** of
    source page indices on each side. ...
    """
    payload = json.dumps(
        [
            {
                "index": s.index,
                "front": s.front is not None,
                "front_pages": None if s.front is None else [
                    None if p.source_ref is None else p.source_ref.page_index
                    for p in s.front.pages
                ],
                "back": s.back is not None,
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

Note what is absent: `plan.paper_pt`, `Placement`, `SourceRef.path`,
`SourceRef.sha256`, `OutputPage.crop_pt`, `Side.marks`.

The state-format version constant, `deckle/core/print_session.py:63-70`:

```python
# Bumped whenever the on-disk state shape changes incompatibly.
#
# 2: ``_hash_plan``'s payload gained the full ordered page sequence per side
# (``front_pages``/``back_pages`` replacing ``front_page``/``back_page``), so
# every v1 hash is incomparable with a v2 one. Without the bump, a v1 session
# would be reported to the user as "the document changed" -- which is a lie,
# and a confusing one, when what actually changed was Deckle.
STATE_VERSION = 2
```

The two call sites of `_hash_plan`, `deckle/core/print_session.py:330` and
`:480-481`:

```python
        plan_hash = _hash_plan(plan)
```

```python
        current_hash = _hash_plan(plan)
        if current_hash != state.plan_hash:
```

The hash that is already correct, `deckle/core/export.py:87-110`:

```python
def _plan_hash(plan: SheetPlan) -> str:
    """A stable hash of everything that affects rendered output.
    ...
    """
    digest = hashlib.sha256()
    digest.update(repr(plan.paper_pt).encode("utf-8"))
    for sheet in plan.sheets:
        digest.update(f"|sheet:{sheet.index}".encode("utf-8"))
        for side_name, side in (("front", sheet.front), ("back", sheet.back)):
            digest.update(f"|{side_name}:".encode("utf-8"))
            if side is None:
                digest.update(b"none")
                continue
            for page in side.pages:
                _update_delimited(digest, "page", _output_page_key(page))
            for mark in side.marks:
                _update_delimited(digest, "mark", _mark_key(mark))
    return digest.hexdigest()
```

and its two helpers, `deckle/core/export.py:64-65` and `:68-85`:

```python
def _mark_key(mark: Mark) -> str:
    return f"{mark.kind}:{mark.x0}:{mark.y0}:{mark.x1}:{mark.y1}"
```

```python
def _update_delimited(digest: "hashlib._Hash", tag: str, value: str) -> None:
    """Feed ``value`` into ``digest`` length-prefixed, not just concatenated.
    ...
    """
    encoded = value.encode("utf-8")
    digest.update(f"|{tag}[{len(encoded)}]:".encode("utf-8"))
    digest.update(encoded)
```

and `deckle/core/export.py:113-142`:

```python
def _output_page_key(page: OutputPage) -> str:
    """One page's contribution to the plan hash.
    ...
    """
    ref = page.source_ref
    ref_key = (
        "none"
        if ref is None
        else (
            f"path[{len(ref.path.encode('utf-8'))}]:{ref.path}"
            f":{ref.page_index}:{ref.sha256}:{ref.width_pt}:{ref.height_pt}"
        )
    )
    placement = page.placement
    placement_key = (
        f"{placement.scale_x}:{placement.scale_y}:{placement.tx}:"
        f"{placement.ty}:{placement.rotate_deg}"
    )
    return f"{ref_key}|{placement_key}|{page.is_filler}|{page.crop_pt}"
```

### Every call site of the symbols this spec moves

`_plan_hash`:

- `deckle/core/export.py:87` (definition)
- `deckle/core/export.py:1034` — `key = (sheet_index, _plan_hash(plan))`
- `tests/test_hardening_limits.py:142` — `export._plan_hash(two_pages) != export._plan_hash(one_page)`
- `tests/test_hardening_limits.py:164` — `export._plan_hash(one) != export._plan_hash(two)`
- `tests/test_crop.py:193` — `export_mod._plan_hash(plain) != export_mod._plan_hash(cropped)`

`_output_page_key`:

- `deckle/core/export.py:113` (definition), used at `deckle/core/export.py:107`,
  and named in the docstring at `:117`
- `tests/test_hardening_limits.py:132`, `:134`, `:135` — `export._output_page_key(...)`
- `tests/test_hardening_limits.py:712` — `from deckle.core.export import _output_page_key`,
  used at `:726` and `:727`
- `tests/test_hardening_limits.py:733` — same import, used at `:745`, `:746`, `:747`

`_mark_key`: `deckle/core/export.py:64` (definition), used at `:109`. No test
imports it.

`_update_delimited`: `deckle/core/export.py:68` (definition), used at `:107`
and `:109`, and named in docstrings at `:95` and `:117`. No test imports it;
its behaviour is asserted indirectly by
`tests/test_hardening_limits.py::test_a_crafted_source_path_cannot_forge_a_cache_key_boundary`.

`_hash_plan`:

- `deckle/core/print_session.py:109` (definition), used at `:330` and `:480`
- `tests/test_print_session.py:12` — `from deckle.core.print_session import ... _hash_plan`
  and used at `:291`, `:321`, `:342-344`, `:359-360`, `:382`

`STATE_VERSION`:

- `deckle/core/print_session.py:70` (definition), used at `:347` and `:463-476`
- `tests/test_print_session.py:504`, `:511`, `:529`, `:536`

### Existing tests over this code

- `tests/test_print_session.py::test_hash_plan_differs_for_different_page_orderings`
- `tests/test_print_session.py::test_hash_plan_distinguishes_absent_side_from_present_side`
- `tests/test_print_session.py::test_hash_plan_handles_filler_pages_without_a_source_ref`
  — **asserts `len(filler) == 16`**
- `tests/test_print_session.py::test_hash_plan_is_stable_across_identical_construction`
- `tests/test_print_session.py::test_load_resumes_normally_when_the_plan_is_unchanged`
- `tests/test_print_session.py::test_load_refuses_a_session_whose_plan_has_changed`
- `tests/test_print_session.py::test_load_refuses_a_state_file_from_an_incompatible_version`
- `tests/test_print_session.py::test_the_version_check_runs_before_the_plan_check`
- `tests/test_hardening_limits.py::test_plan_hash_separates_pages_that_would_otherwise_concatenate`
- `tests/test_hardening_limits.py::test_plan_hash_separates_marks_from_following_marks`
- `tests/test_crop.py::test_the_crop_is_part_of_the_export_cache_key` (line 193)

## 3. Change

### The payload, field by field

`_hash_plan` becomes a thin caller of one shared implementation. The payload
it hashes is, in order:

| Position | Value | Why it must be in |
|---|---|---|
| once, first | `repr(plan.paper_pt)` | a paper change re-lays every sheet; a back pass onto the old stack is off by the difference on every sheet |
| per sheet | `sheet.index` | already covered; the sheet number is what a pass feeds |
| per side | `"front"` / `"back"` and the literal `none` for an absent side | keeps "this face does not exist" distinct from "this face is blank filler", which `Side.__post_init__` exists to preserve |
| per page | `SourceRef.path`, byte-length-prefixed | a different file at the same index is a different book |
| per page | `SourceRef.page_index` | already covered |
| per page | `SourceRef.sha256` | **the same path re-exported with different content**; the case no other field catches |
| per page | `SourceRef.width_pt`, `SourceRef.height_pt` | a re-scan at a different page box changes the fitted scale |
| per page | `Placement.scale_x`, `scale_y`, `tx`, `ty`, `rotate_deg` | this is where gutter, margins, `slack_to`, `binding_edge` and `landscape_policy` all land. They are not hashed as settings, they are hashed as the geometry those settings produced — which is the only thing the paper sees |
| per page | `OutputPage.is_filler` | a filler and a real page with no `source_ref` are otherwise identical |
| per page | `OutputPage.crop_pt` | `Placement` already describes the *cropped* content, but two different crops can produce the same placement on a page whose slack absorbs the difference |
| per mark | `Mark.kind`, `x0`, `y0`, `x1`, `y1` | see below |

Every variable-length component is length-prefixed by `update_delimited`,
for the reason that function's docstring already gives.

**Marks are included, deliberately.** A mark is ink on the sheet: a fold
line, a sewing station, a signature-order bar, a trim cut line. Changing
`sewing_stations` from 3 to 5, or `trim_pt` from 0 to 0.25in, between the
two passes puts different marks on the backs than are on the fronts, and a
binder awls through the stations on the sheet in front of them. Excluding
them would also mean writing a second hash function rather than reusing the
one that exists, which is the thing this spec is for. The rejected
alternative — hash only the content and let marks drift — was rejected
because `Side.marks` is part of `Side`, and the session's promise is about
the *sheet*, not about the pages on it.

**Not in the payload:** `SheetPlan.warnings` (advisory text, not ink) and
`SheetPlan.signatures` (a grouping over sheets already hashed individually;
a regrouping that changes no sheet changes no paper). Both are already
absent from `export._plan_hash` and stay absent.

### Where the code lives

New module `deckle/core/plan_hash.py`, importing only `hashlib` and
`deckle.core.models`. Both `export` and `print_session` import from it.

Rejected: `print_session` importing `deckle.core.export._plan_hash`
directly. It is a one-line change and creates no import cycle (`export`
imports `diagnostics`, `models`, `paths`, `printing`; none of those reaches
`print_session`, and `render` imports `export` rather than the reverse —
**verified**, see §5). It was rejected for two reasons: it is a private
name crossing a module boundary, and it would pull `pikepdf` into the
import graph of a module whose docstring calls it a pure state machine.

Rejected: putting the helpers in `models.py`. `models` holds value objects
and imports nothing; a hashing policy is not a value object.

### Steps

1. **New file `deckle/core/plan_hash.py`.** Module docstring in the house
   voice: this is the one answer to "are these two plans the same piece of
   paper", asked by the export cache (a wrong answer hands back the wrong
   sheet) and by print-session resume (a wrong answer prints backs onto
   mismatched fronts); it is one module because two copies would be two
   chances to omit a field, and the omission is invisible until it costs
   paper. Close with the `tests/test_core_purity.py` no-Qt line the other
   core modules carry.

   Move, verbatim except for the leading underscore, from
   `deckle/core/export.py`:

   - `_mark_key` → `mark_key` (was `export.py:64-65`)
   - `_update_delimited` → `update_delimited` (was `export.py:68-85`)
   - `_output_page_key` → `output_page_key` (was `export.py:113-142`)
   - `_plan_hash` → `plan_hash` (was `export.py:87-110`)

   Keep every existing docstring. Add to `plan_hash`'s docstring one
   paragraph naming its second consumer: `print_session` compares it across
   a manual-duplex reload, so a field left out of it is a field a resumed
   job is blind to.

   `plan_hash` keeps returning the **full 64-character** hexdigest.

   Declare `__all__ = ["mark_key", "output_page_key", "plan_hash", "update_delimited"]`.

2. **`deckle/core/export.py`.** Delete the four moved functions. Add
   `from deckle.core.plan_hash import plan_hash` to the `deckle.core`
   import block (after `from deckle.core.paths import evict_lru_files`, so
   the block stays alphabetical). At `export.py:1034` change
   `key = (sheet_index, _plan_hash(plan))` to
   `key = (sheet_index, plan_hash(plan))`. Check whether `Mark` and
   `OutputPage` are still used elsewhere in `export.py` and trim the
   `deckle.core.models` import line if either has become unused
   (`grep -n "Mark\|OutputPage" deckle/core/export.py`).

3. **`deckle/core/print_session.py`.** Add
   `from deckle.core.plan_hash import plan_hash` to the import block.
   Replace the body of `_hash_plan` (lines 109-145) with:

   ```python
   def _hash_plan(plan: SheetPlan) -> str:
       """The plan fingerprint stored in a session's state file.

       :func:`deckle.core.plan_hash.plan_hash`, truncated to 16 hex
       characters. Shared with the export cache rather than computed here,
       because the two questions are the same one: "is this the same piece
       of paper?". The version that lived here answered it with sheet
       index, side presence and source page indices only -- so changing the
       gutter, the margins, the paper, the crop, or opening a different
       document of the same length all resumed happily, and the backs
       printed with geometry the fronts on the stack do not have.

       Truncated because this value is written into a state file a person
       may have to read, and 64 bits is far past the point where two plans
       of one document collide. The export cache, which is asked the same
       question thousands of times per session, keeps the full digest.
       """
       return plan_hash(plan)[:16]
   ```

   Remove the now-unused `import hashlib`? **No** — `hashlib` is still used
   at `print_session.py:342` to derive `session_id`. Leave it. `import json`
   is still used by `_save`/`load`; leave it.

4. **Bump the state version.** `deckle/core/print_session.py:63-70`, set
   `STATE_VERSION = 3` and append to the comment block, in the same voice
   as the `2:` entry:

   ```python
   # 3: ``_hash_plan`` now delegates to ``deckle.core.plan_hash``, which
   # covers paper size, placement geometry, crop insets, the source file's
   # own hash and the sheet's marks -- everything a v2 hash left out. Every
   # v2 hash is incomparable with a v3 one, and reporting an old session as
   # "the document changed" would be a lie about which thing changed.
   ```

5. **Migration.** There is no migration and none is possible: a v2 hash
   cannot be recomputed into a v3 one without the plan that produced it,
   and if the caller had that plan they would not need the stored hash.
   Every state file written before this change therefore becomes
   unresumable. `load` reports it as
   **`StaleSessionError(reason="version")`**, not `"plan"` — the existing
   version branch at `print_session.py:463-478` already fires first for
   exactly this reason, and its message ("this session was saved by a
   different version of Deckle") is the true one. The cost is one
   interrupted job, on the single upgrade, reprinted from the start; the
   alternative is telling the user their document changed when it did not.

6. **`docs/api/core.plan_hash.rst`** — new page, matching
   `docs/api/core.export.rst` exactly in shape:

   ```rst
   deckle.core.plan_hash
   =====================

   .. automodule:: deckle.core.plan_hash
      :members:
      :show-inheritance:
   ```

   Add `core.plan_hash` to the toctree in `docs/api/core.rst`, immediately
   after `core.export`. `tests/test_docs_coverage.py` has three assertions
   that fail without both halves.

7. **Update the five test references to the moved names** (mechanical, no
   behaviour change):

   - `tests/test_hardening_limits.py:142` and `:164` —
     `export._plan_hash(...)` → `plan_hash(...)`, adding
     `from deckle.core.plan_hash import output_page_key, plan_hash` to that
     file's imports (line 22 area, beside
     `from deckle.core import export, render`).
   - `tests/test_hardening_limits.py:132`, `:134`, `:135` —
     `export._output_page_key(...)` → `output_page_key(...)`.
   - `tests/test_hardening_limits.py:712` and `:733` —
     `from deckle.core.export import _output_page_key` →
     `from deckle.core.plan_hash import output_page_key`, and the call
     sites that follow each (`:726`, `:727` and `:745`, `:746`, `:747`).
   - `tests/test_crop.py:193` — `export_mod._plan_hash(...)` →
     `plan_hash(...)`, with the import added at the top of that test
     function in the same style the file already uses.

   Docstring prose in `tests/test_hardening_limits.py:98`, `:128` and
   `:700` names these helpers; update the names there too. The test
   *function* names (`test_plan_hash_separates_...`) stay as they are —
   they name the property, not the symbol.

8. **New tests** — see §4.

## 4. Tests

Write these first; every one of them passes today and must fail after step 1
is reverted, so run them against the unfixed tree before changing anything.

All four go in `tests/test_print_session.py`, in a new section after the
existing stale-session block (after line 543), with a section comment in the
file's voice explaining that these are the cases a page-index-only hash
could not see.

They need a real imposed plan rather than the file's hand-built
`_make_plan`, because the point is that *layout settings* reach the hash.
Add one module-level helper beside `_make_plan`:

```python
def _imposed(tmp_path, settings_overrides: dict, pages_pdf: str | None = None):
    """A real plan, so a settings change reaches the hash the way it does
    in the app -- `_make_plan` builds sheets by hand and cannot."""
```

Build the source with `tests/test_loader.py::_make_pdf` (blank pages, 4 of
them, Letter) written into `tmp_path`, load it with
`deckle.core.loader.load_pdf`, and impose with
`deckle.core.layout.GutterShiftStrategy`. `tests/fixtures/sample.pdf` also
works and is 2 pages of Letter.

### `test_resume_refuses_a_session_after_the_gutter_changed`

- **Setup:** impose the 4-page source with `gutter_pt=0.0`, start a session
  (`_started_session_id`), then impose the *same pages* with
  `gutter_pt=54.0` and call `PrintSession.load` with the second plan.
- **Assertion:** raises `StaleSessionError` with `reason == "plan"` and
  `"layout has changed" in detail`.
- **Unfixed tree:** no exception; `load` returns a `PrintSession`, and the
  test fails with `DID NOT RAISE <class 'deckle.core.print_session.StaleSessionError'>`.

### `test_resume_refuses_a_session_after_the_paper_changed`

- **Setup:** as above, but `paper=(612.0, 792.0)` then `paper=(595.28, 841.89)`.
- **Assertion:** same.
- **Unfixed tree:** same failure. This one is the most damning: `paper_pt`
  is a `SheetPlan` field the old payload never looked at at all.

### `test_resume_refuses_a_session_after_the_crop_changed`

- **Setup:** as above, but `crop_odd_pt=None` then `crop_odd_pt=(36.0, 36.0, 36.0, 36.0)`.
- **Assertion:** same.
- **Unfixed tree:** same failure.

### `test_resume_refuses_a_different_document_of_the_same_length`

- **Setup:** write two different 4-page Letter PDFs (different content, so
  different `sha256`) at two paths in `tmp_path`; impose each with the same
  `LayoutSettings`; start a session on the first; `load` with the second.
- **Assertion:** same. Docstring should say plainly that this is the case
  the page-index payload was structurally incapable of seeing — both plans
  name pages `[0]`, `[1]`, `[2]`, `[3]`.
- **Unfixed tree:** same failure.

### `test_resume_still_accepts_an_unchanged_plan`

- **Setup:** impose once, start a session, impose the *same* pages with the
  *same* settings into a second `SheetPlan` object, and `load` with it.
- **Assertion:** returns a session whose `_state.session_id` matches; no
  exception. This is the guard against a hash that is merely
  identity-based; `test_load_resumes_normally_when_the_plan_is_unchanged`
  only re-uses the same object.
- **Unfixed tree:** passes. Keep it anyway — it is the test that fails if
  someone hashes something non-deterministic (a `dict` iteration order, an
  `id()`, a timestamp).

### Existing tests that must keep passing unchanged

`test_hash_plan_handles_filler_pages_without_a_source_ref` asserts
`len(filler) == 16`. The `[:16]` truncation in step 3 is what keeps it
green; do not drop it.

`test_load_refuses_a_state_file_from_an_incompatible_version` and
`test_the_version_check_runs_before_the_plan_check` write
`STATE_VERSION - 1` into the state file. With `STATE_VERSION = 3` that is
`2`, still a mismatch, so both keep passing without edits.

## 5. Acceptance

| Check | Command |
|---|---|
| The new module exists and imports nothing heavy | `.venv/bin/python -c "import sys; import deckle.core.plan_hash; assert 'pikepdf' not in sys.modules"` |
| No import cycle: `plan_hash` reaches neither `export` nor `print_session` | `.venv/bin/python -c "import sys; import deckle.core.plan_hash as m; assert not [n for n in sys.modules if n in ('deckle.core.export','deckle.core.print_session')]"` |
| `print_session` does not import `export` | `! grep -n "from deckle.core.export\|from deckle.core import export" deckle/core/print_session.py` |
| `print_session` does not import pikepdf, directly or transitively | `.venv/bin/python -c "import sys; import deckle.core.print_session; assert 'pikepdf' not in sys.modules, sorted(n for n in sys.modules if 'pike' in n)"` |
| The old page-index payload is gone | `! grep -n "front_pages\|back_pages" deckle/core/print_session.py` |
| The state version was bumped | `.venv/bin/python -c "from deckle.core.print_session import STATE_VERSION; assert STATE_VERSION == 3, STATE_VERSION"` |
| The session hash is still 16 characters | `.venv/bin/python -c "from deckle.core.print_session import _hash_plan; from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side; p=SheetPlan(sheets=[Sheet(index=0, front=Side(pages=(OutputPage(source_ref=None, placement=Placement(scale_x=1.0,scale_y=1.0,tx=0.0,ty=0.0,rotate_deg=0), is_filler=True),)), back=None)], paper_pt=(612.0,792.0), warnings=[]); assert len(_hash_plan(p)) == 16"` |
| No stale references to the moved private names | `! grep -rn 'export\._plan_hash\|export\._output_page_key\|export_mod\._plan_hash\|from deckle.core.export import _output_page_key' --include="*.py" deckle/ tests/` |
| The four helpers really left `export.py` | `! grep -n "^def _plan_hash\|^def _output_page_key\|^def _mark_key\|^def _update_delimited" deckle/core/export.py` |
| New tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_session.py -k "refuses_a_session_after or same_length or still_accepts_an_unchanged"` |
| The three files that hash-test `export` still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_limits.py tests/test_crop.py tests/test_export.py` |
| Docs coverage | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| Core purity | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

The "no stale references" grep currently returns **8 hits**, all in
`tests/test_hardening_limits.py` (6) and `tests/test_crop.py` (2); it must
return none afterwards. The "four helpers really left `export.py`" grep
currently returns **4 hits**. The full-suite row's expected result is the
baseline from `00-environment.md`: **1507 passed, 22 skipped, 2 failed**,
plus the new tests, minus nothing.

## 6. Out of scope

- **B4** — the duplicated `log_print_job` call in `_submit_sheets`. Same
  file, different function. See the ordering note in §8.
- **B27** — moving the state directory off `tempfile.gettempdir()`, and
  guarding `_save`/`_delete_state`. Same file.
- **B28** — reading the `version` field before `_check_state`. Same file,
  same method (`load`), and the only real collision in this cluster.
- **B20** — `export(sheets=...)` skipping unknown indices, and
  `render_sheet` caching an empty PDF. Same file as step 2 but a different
  region; do not fix it here.
- **M6** — the second atomic-write implementation in `export.py`. Leave it.
- Anything about *which side* the backend prints. `PrintSession` submits
  through the five-argument `PrintBackend` Protocol, which carries no
  `side`, so `QtPrintBackend.submit` defaults to `side="front"` for both
  passes. That is a real defect and is not in the roadmap; it is not this
  spec's.

## 7. decisions.md entry

```
## 2026-09-05 — Resume checked the page numbers and nothing else
- Symptom: `PrintSession`'s `plan_hash` covered sheet index, side presence and source page indices. Change the gutter, the margins, the paper, or the crop between pass 1 and pass 2 -- or open a different document of the same length -- and resume accepted it, then printed the backs with geometry the fronts on the stack do not have. Verified on a four-page dummy: the session hash was byte-identical across a 54pt gutter change, an A4 paper change, a 36pt crop, and a swapped source file, while the export cache key differed on every one.
- Fix: One `deckle/core/plan_hash.py`, used by both the export cache and the session. It covers paper size, placement, crop insets, the source file's own sha256, and the sheet's marks. `STATE_VERSION` 2 -> 3, so every pre-existing state file is refused as `reason="version"` rather than misreported as "the document changed".
- Surfaces: The export cache had asked and answered the identical question -- "are these two plans the same piece of paper" -- since before the session existed. Two answers to one question is two chances to omit a field, and the omission is invisible until it costs a stack of paper.
- Watch: The hash that guards the *cheap* failure (a wrong cached thumbnail) was the thorough one; the hash that guards the *expensive* failure (sixty ruined sheets) was the thin one. When two places ask the same question, check that the one with the worse failure mode is not the one written in a hurry.
- Commit: <fill in>
```

## 8. Traps

- **File collision.** B3, B4, B27 and B28 all edit
  `deckle/core/print_session.py`. B3 and B28 both edit
  `PrintSession.load` — B3 the plan-hash comparison at the tail, B28 the
  version check at the head. Recommended order for the whole cluster:
  **B4 → B3 → B28 → B27.** B4 is smallest and touches only
  `_submit_sheets`; B3 and B28 must be sequential; B27 then rewrites
  `_state_dir`, `_save`, `_delete_state` and `list_resumable`, none of
  which B3 touches.
- **`_hash_plan` must stay 16 characters.**
  `test_hash_plan_handles_filler_pages_without_a_source_ref` asserts it,
  and the state files on disk carry it.
- **Do not renumber `STATE_VERSION` past 3.** Two tests write
  `STATE_VERSION - 1` and expect a mismatch; that holds for any value, but
  the comment block is a running history and skipping a number makes it a
  lie.
- **`export._plan_hash` is referenced from three test files by module
  attribute.** `from deckle.core.export import _output_page_key` in
  particular is an *import*, so it fails at collection rather than in an
  assertion — a missed rename shows up as `ImportError` in
  `tests/test_hardening_limits.py`, not as a failing test.
- **`tests/test_docs_coverage.py` has three separate assertions.** A new
  module without a page fails the first; a page not listed in a toctree
  fails the third. Do both in the same commit.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses one whose added lines contain
  `<FILL-IN>`.
- `python -m deckle` launches the GUI and blocks. Use
  `python -m deckle.cli` for anything scripted.
- `pytest -k <pattern>` with no match exits 5, not 0, so an acceptance row
  that relies on `-k` fails loudly if the test was never written.
