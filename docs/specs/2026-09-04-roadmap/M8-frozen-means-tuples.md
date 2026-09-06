# M8 — Make "frozen" mean frozen: tuples in every frozen dataclass

**Roadmap item:** `docs/ROADMAP.md` M8
**Depends on:** R0.1
**Blocks:** —
**Size:** S
**Decision needed first:** none.

---

## 1. Context

`SheetPlan` and `Project` are declared `@dataclass(frozen=True)` and then
given `list` fields. `frozen=True` stops you rebinding `plan.sheets`; it
does nothing at all about `plan.sheets.append(...)`. Every sibling value
type in the same file — `Side.pages`, `Side.marks`, `Signature.sheet_indices`,
`LayoutSettings.signature_lengths` — uses tuples.

Two concrete consequences.

**Neither type can be hashed, and the code has already had to work around
it.** `deckle/core/export.py` maintains a plan-hash for its sheet cache and
`deckle/core/print_session.py` maintains another for stale-session
detection; both build a digest by hand because the obvious `hash(plan)`
raises. Verified, with the change applied to stand-in types built from the
real `Sheet`, `Side`, `LayoutWarning`, `SourcePage` and `LayoutSettings`
values:

```
plan-like hash: 8158280862168732953
project-like hash: -7997760866570836993
```

Both become hashable the moment the three fields are tuples. Nothing else in
either object's field graph is mutable.

**The type says a thing is safe to share, and it is not.** `AppState` keeps
every past `Project` in an undo stack and hands the current one to four
views. A `list` field means any of them could reorder the document under the
others, and nothing would raise. Nothing does today —
`grep -rn "\.pages\.\(append\|insert\|pop\|remove\|extend\|sort\|reverse\|clear\)"`
over `deckle/` and `tests/` finds only `deckle/core/loader.py:587`, which is
a `pikepdf.Pdf.pages`, not a `Project.pages`. The point is that the type
permits it while claiming not to.

This is not a bug a person printing a book can see today. It is a promise
the type system is making on behalf of code that has not been written yet.

## 2. Current code

`deckle/core/models.py:269-285`, verbatim:

```python
@dataclass(frozen=True)
class SheetPlan:
    """The full set of sheets produced by imposing a project's pages.

    :ivar sheets: every sheet, in print order.
    :ivar paper_pt: the paper size the sheets were laid out for, as
        ``(width, height)`` in points.
    :ivar warnings: everything non-fatal noticed while planning. Advisory,
        never a failure -- but callers are expected to surface them.
    :ivar signatures: the signature grouping, empty under
        ``fold_scheme="none"`` where there is nothing to gather.
    """

    sheets: list[Sheet]
    paper_pt: tuple[float, float]
    warnings: list[LayoutWarning]
    signatures: tuple[Signature, ...] = ()
```

`signatures` is already a tuple, one line below two that are not.

`deckle/core/models.py:479-493`, verbatim:

```python
@dataclass(frozen=True)
class Project:
    """The document model: source pages plus layout and printer settings.

    :ivar pages: the ordered page list, including skipped pages and
        inserted blanks. Plain Python value objects -- never a
        ``pikepdf.Pdf.pages`` proxy; see
        ``deckle.app.views.arrange_view`` for why that boundary matters.
    :ivar layout: the imposition settings.
    :ivar printer: the printer name recorded with the project, or ``None``.
    """

    pages: list[SourcePage]
    layout: LayoutSettings
    printer: str | None
```

`deckle/core/models.py:164-186` is the precedent for the fix — `Side` is a
frozen dataclass in the same file with tuple fields **and** a
`__post_init__`:

```python
@dataclass(frozen=True)
class Side:
    ...
    pages: tuple[OutputPage, ...]
    marks: tuple[Mark, ...] = ()

    def __post_init__(self) -> None:
        if not self.pages:
            raise ValueError(
                "Side.pages must not be empty -- an absent side is None, "
                "never Side(pages=())"
            )
```

### The complete set of offenders

`grep -rn ": list\[" deckle/` over dataclass *fields* (not locals or
parameters) finds **five**, in four files. The roadmap names two of them.

| Field | File:line | Frozen? |
|---|---|---|
| `SheetPlan.sheets` | `deckle/core/models.py:282` | yes |
| `SheetPlan.warnings` | `deckle/core/models.py:284` | yes |
| `Project.pages` | `deckle/core/models.py:491` | yes |
| `PrintPass.sheet_order` | `deckle/core/printing.py:100` | yes |
| `PreviewFrame.warnings` | `deckle/app/views/preview_view.py:316` | yes |

and one that is **not** an offender and must be left alone:
`_SessionState.sheets` (`deckle/core/print_session.py:184`) is a plain
`@dataclass`, deliberately mutable — it is the on-disk print cursor, and
`sheet_cursor` is advanced on it as sheets go through the printer.

### Construction sites under `deckle/`

`SheetPlan`: `deckle/core/layout.py:700` and `deckle/core/layout.py:1059`.
Both build local `list`s (`layout.py:654, 658, 878, 926, 927`), append to
them, and pass them at construction. No append happens after construction —
`grep -rn "\.warnings\.\(append\|extend\)" deckle/` finds nothing on a
`SheetPlan`.

`Project`: `deckle/app/main.py:296` (`pages=[]`),
`deckle/cli.py:1067` (`pages=list(pages)`),
`deckle/core/project_io.py:564` (`pages=pages`).

`replace(project, pages=...)`: `deckle/app/state.py:103, 127, 142, 158, 171`
and `deckle/app/views/import_view.py:48`. Every one of them builds a fresh
`list` (`state.py:100, 140, 156, 169` are all `pages = list(project.pages)`),
mutates the copy, and passes it — the copy-on-write discipline is already
right; only the container type is wrong.

`PrintPass`: `deckle/core/printing.py:153, 160`.
`PreviewFrame`: `deckle/app/views/preview_view.py:342`.

### Read sites that would break with tuples

Almost none, because nothing indexes or concatenates these. The three
patterns that do matter:

- `deckle/app/backend.py:435`: `_chunked(print_pass.sheet_order, self.chunk_size)`.
  `_chunked` (`backend.py:147-151`) is `[list(items[i:i+size]) for ...]`
  over a `Sequence[int]` and already returns lists. Safe.
- `deckle/core/print_session.py:579, 609`: `pass_.sheet_order[:1]` and
  `pass_.sheet_order[cursor:]`. Slices of a tuple are tuples; both feed
  `_submit_sheets(pass_, sheets)`, then `backend.submit(..., sheets, ...)`
  (`Sequence[int]`) and `log_print_job(..., sheets, ...)`. Safe.
- `deckle/core/print_session.py:203`: `sheets=list(data["sheets"])` — that is
  `_SessionState`, which is not changing.

`json.dump` serialises a tuple as an array, so nothing persisted changes
shape.

### Documentation that becomes false

`deckle/app/state.py:87-90`:

```python
    Operates purely on ``project.pages`` (a plain ``list[SourcePage]``) --
```

### Tests that assert the container type

Found with
`grep -rn "\.pages == \|\.sheets == \|\.warnings == \|== \[\]" tests/`:

| Test file:line | Assertion | Becomes |
|---|---|---|
| `tests/test_models.py:132` | `assert plan.sheets == []` | `== ()` |
| `tests/test_models.py:134` | `assert plan.warnings == []` | `== ()` |
| `tests/test_models.py:196` | `assert project.pages == [page]` | `== (page,)` |
| `tests/test_layout.py:405` | `assert impose([], settings(start_on_recto=False)).sheets == []` | `== ()` |
| `tests/test_printing.py:72` | `assert back.sheet_order == list(reversed(front.sheet_order))` | `== tuple(reversed(front.sheet_order))` |
| `tests/test_printing.py:80` | `assert back.sheet_order == front.sheet_order` | unchanged |
| `tests/test_printing.py:87` | `assert p.sheet_order == [7]` | `== (7,)` |

Every other test **constructs** with lists (roughly forty `SheetPlan(...)`
call sites across `tests/`, e.g. `tests/test_print_dialog.py:57`,
`tests/test_render.py:60`, `tests/test_export_marks.py:87`) and never
asserts the container type. §3 keeps all of those working.

## 3. Change

### The coercion, and why

**Chosen: change the annotations and add a `__post_init__` that coerces via
`object.__setattr__`. Rejected: change the annotations and fix every caller
to pass a tuple.**

The rejected option touches roughly fifty construction sites, forty of them
in tests that have no opinion about the container, and it leaves the
invariant unenforced anyway — the next caller to pass a list gets a
`SheetPlan` whose `sheets` is a list and whose annotation says otherwise,
which is exactly today's situation with extra steps.

Coercion makes the annotation true for every caller that has ever existed
and every one that will. `Side.__post_init__` in the same file is the
precedent. `dataclasses.replace` constructs through `__init__`, so the five
`replace(project, pages=[...])` sites in `state.py` are covered without
edits.

### Steps

1. **`deckle/core/models.py`**, `SheetPlan` (lines 282-285). Change the two
   annotations and add the coercion:

   ```python
       sheets: tuple[Sheet, ...]
       paper_pt: tuple[float, float]
       warnings: tuple[LayoutWarning, ...]
       signatures: tuple[Signature, ...] = ()

       def __post_init__(self) -> None:
           # Coerced rather than required. `frozen=True` stops a caller
           # rebinding these; it says nothing about `plan.sheets.append(...)`,
           # and a plan is held in an undo stack and handed to four views at
           # once. Every producer builds a local list and appends to it --
           # which is the right way to build one -- so the conversion belongs
           # here rather than at fifty call sites, where the next one added
           # would simply forget.
           object.__setattr__(self, "sheets", tuple(self.sheets))
           object.__setattr__(self, "warnings", tuple(self.warnings))
           object.__setattr__(self, "signatures", tuple(self.signatures))
   ```

   Also update the two `:ivar:` lines to say `tuple`? No — they describe
   meaning, not container. Leave the docstring's prose alone except for
   step 6.

2. **`deckle/core/models.py`**, `Project` (line 491):

   ```python
       pages: tuple[SourcePage, ...]
       layout: LayoutSettings
       printer: str | None

       def __post_init__(self) -> None:
           object.__setattr__(self, "pages", tuple(self.pages))
   ```

   and change the `:ivar pages:` line from *"the ordered page list"* to
   *"the ordered pages"*, keeping the rest of that paragraph verbatim — the
   sentence about never being a `pikepdf.Pdf.pages` proxy is the important
   half and stays.

3. **`deckle/core/printing.py`**, `PrintPass` (line 100):

   ```python
       sheet_order: tuple[int, ...]
   ```

   plus the same `__post_init__` coercion for that one field.

   `deckle.core.printing` is described elsewhere as "a frozen seam"
   (`deckle/app/backend.py:265`). That phrase means its *API shape* was
   settled — `PrintResult` carries a count and nothing more — not that its
   field types are untouchable. Narrowing a container from `list` to `tuple`
   removes a capability nobody uses and adds none.

4. **`deckle/app/views/preview_view.py`**, `PreviewFrame` (line 316):

   ```python
       warnings: tuple[LayoutWarning, ...]
   ```

   plus the same coercion. `build_preview_frame` at line 342 passes a list
   built at line 119; leave it as it is.

5. **`deckle/app/state.py:89`** — replace

   ```
       Operates purely on ``project.pages`` (a plain ``list[SourcePage]``) --
   ```

   with

   ```
       Operates purely on ``project.pages`` (a ``tuple[SourcePage, ...]`` of
       plain value objects) --
   ```

   Read the whole sentence before editing; it continues past the line break.

6. **`deckle/core/models.py`**, `SheetPlan`'s class docstring — add one line
   after the existing `:ivar signatures:` entry:

   ```
       Every sequence field is a tuple, coerced in ``__post_init__``:
       ``frozen=True`` prevents rebinding, not mutation, and a plan is
       shared by the undo stack, the preview, the schedule and the
       exporter at the same time.
   ```

7. The four test edits from §2's table, plus the three in `test_printing.py`.

8. No new module, so no `docs/api/` change.

## 4. Tests

### New: `tests/test_frozen_means_frozen.py`

```python
"""``frozen=True`` stops rebinding, not mutation.

`SheetPlan` and `Project` were declared frozen and given `list` fields,
one line below sibling fields that were tuples. Nothing mutated them --
but a plan is held in an undo stack and handed to four views at once, and
the type said that was safe when it was not. Neither type could be
hashed, either, which is why two modules build plan digests by hand.
"""
```

| Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_no_frozen_dataclass_declares_a_list_field` | `pkgutil.walk_packages(deckle.__path__, prefix="deckle.")`, skipping any name containing `__main__`; import each; for every class in the module's `__dict__` that `dataclasses.is_dataclass` and whose `__dataclass_params__.frozen` is true, look at `str(f.type)` for each of `dataclasses.fields(cls)` | no field's type string starts with `list[` | `AssertionError: frozen dataclasses with list fields: ['deckle.core.models.SheetPlan.sheets', 'deckle.core.models.SheetPlan.warnings', 'deckle.core.models.Project.pages', 'deckle.core.printing.PrintPass.sheet_order', 'deckle.app.views.preview_view.PreviewFrame.warnings']` |
| `test_a_plan_built_from_lists_holds_tuples` | `SheetPlan(sheets=[sheet], paper_pt=(612.0, 792.0), warnings=[warning])` — lists, the way forty existing call sites do it | `isinstance(plan.sheets, tuple)` and `isinstance(plan.warnings, tuple)` and `isinstance(plan.signatures, tuple)` | `AssertionError` — they are lists |
| `test_a_project_built_from_a_list_holds_a_tuple` | `Project(pages=[page], layout=..., printer=None)` | `isinstance(project.pages, tuple)` | as above |
| `test_replace_still_coerces` | `dataclasses.replace(project, pages=[page, page])` | the result's `pages` is a tuple of length 2 | as above — `replace` goes through `__init__`, so this is the assertion that proves `state.py`'s five `replace(project, pages=[...])` sites are covered |
| `test_a_plan_can_be_hashed` | build a `SheetPlan` with one real `Sheet`, one real `LayoutWarning` | `hash(plan)` returns an int, and two structurally equal plans hash equal | `TypeError: unhashable type: 'list'` |
| `test_a_project_can_be_hashed` | build a `Project` with one real `SourcePage` | same | same |

`test_no_frozen_dataclass_declares_a_list_field` has no allow-list, and that
is the point: an exception in it would be the same "documented exception"
that `tests/test_single_source_decisions.py` carried for `export.py` until
M6 removed it. If a future frozen type genuinely needs a mutable field, it
is not frozen.

The hash tests use `str(f.type)` / real values rather than stand-ins;
`str(list[int])` is `"list[int]"` whether or not the defining module uses
`from __future__ import annotations`, so the scan works either way.

Build values with the real constructors — note the signatures, which are
easy to get wrong from memory:
`SourceRef(path, page_index, sha256, width_pt, height_pt)`,
`SourcePage(ref, rotate_deg, skipped)`,
`Placement(scale_x, scale_y, tx, ty, rotate_deg)`,
`OutputPage(source_ref, placement, is_filler)`,
`Side(pages=(output_page,))` (never empty — `Side.__post_init__` raises),
`Sheet(index, front, back)`,
`LayoutWarning(sheet_index, kind, detail)` with `kind` one of the `Literal`
members.

### Existing tests to change

Exactly seven assertions, all listed in §2's last table. Nothing else. **If
any other test fails, a step in §3 was done wrong** — most likely a
`__post_init__` that forgot a field.

## 5. Acceptance

| Check | Command |
|---|---|
| The five list fields are gone | `! grep -nE '^    [a-z_]+: list\[[A-Za-z]+\]$' deckle/core/models.py deckle/core/printing.py deckle/app/views/preview_view.py` — matches exactly those five today and nothing else; the leading four-space and trailing `$` anchors are what keep it off the locals, defaults and parameters in the same files |
| The plan's fields are tuples | `grep -n 'sheets: tuple\[Sheet, ...\]' deckle/core/models.py` |
| The project's field is a tuple | `grep -n 'pages: tuple\[SourcePage, ...\]' deckle/core/models.py` |
| The mutable session state is untouched | `grep -nE '^    sheets: list\[int\]$' deckle/core/print_session.py` |
| A plan hashes | `.venv/bin/python -c "from deckle.core.models import SheetPlan; hash(SheetPlan(sheets=[], paper_pt=(612.0,792.0), warnings=[]))"` |
| The new file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_frozen_means_frozen.py -q --no-header -p no:cacheprovider` |
| The models, layout and printing suites pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_models.py tests/test_layout.py tests/test_layout_saddle.py tests/test_printing.py tests/test_print_session.py tests/test_app_state.py tests/test_view_workers.py tests/test_project_io.py -q --no-header -p no:cacheprovider` |
| `core` is still Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_core_purity.py -q --no-header -p no:cacheprovider` |
| A saved project still round-trips | `.venv/bin/python -m deckle.cli impose tests/fixtures/sample.pdf -o /tmp/m8.deckle && .venv/bin/python -m deckle.cli info /tmp/m8.deckle` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` Arrange still reorders | `.venv/bin/python -m deckle`, import a multi-page PDF, drag a page in the grid, undo, redo. The undo stack holds `Project`s and this spec changes what one of their fields is; the arithmetic tests cannot see a stack that has started sharing an object between entries. |

The first row currently matches `models.py:282`, `models.py:284`,
`models.py:491`, `printing.py:100` and `preview_view.py:316` — verified —
so it is meaningful in both directions.

## 6. Out of scope

- **`_SessionState`** (`deckle/core/print_session.py:184`). Not frozen, and
  deliberately not: it is the on-disk print cursor and is advanced in place.
  Leave `sheets: list[int]` alone; the acceptance table has a row that fails
  if you do not.
- **`export._plan_hash` and `print_session._hash_plan`.** Both become
  *replaceable* by `hash(plan)` after this change and neither should be
  replaced here. `_plan_hash` feeds a **disk** cache key and must be stable
  across processes, which `hash()` is not (`PYTHONHASHSEED`); `_hash_plan`
  is **B3**, which changes what it covers. Do not touch either.
- **B3** (`print_session._hash_plan` missing gutter/paper/crop changes) and
  **B4**. Same files, different functions.
- **M6** touches `deckle/core/export.py` and `deckle/core/render.py`; this
  spec touches neither.
- Do not convert local `list` variables, function parameters annotated
  `list[...]`, or the non-dataclass attributes at `backend.py:287-289`,
  `arrange_view.py:261`, `import_view.py:95` and `main.py:568`. Those are
  mutable working state on live objects and are correct.
- Do not add `eq=False`, `slots=True`, or `__hash__` overrides. The
  dataclass-generated hash is what §4 tests.

## 7. decisions.md entry

```
## 2026-09-05 — "Frozen" meant you could not rebind the list you could still append to
- Symptom: `SheetPlan.sheets`, `SheetPlan.warnings` and `Project.pages` were `list` fields on `@dataclass(frozen=True)` types, one line below `signatures: tuple[Signature, ...]`. `frozen=True` prevents rebinding and does nothing about `plan.sheets.append(...)` -- and a plan is held in an undo stack and handed to the preview, the schedule and the exporter at the same time. Neither type could be hashed. Two more of the same, found by grepping for the shape rather than trusting the two the roadmap named: `PrintPass.sheet_order` and `PreviewFrame.warnings`.
- Fix: tuples on all five, coerced in `__post_init__` with `object.__setattr__`, following `Side` in the same file. Both types now hash. `tests/test_frozen_means_frozen.py` walks the package and fails on any frozen dataclass with a `list` field, with no allow-list.
- Surfaces: Coerced rather than required, because every producer builds a local list and appends to it -- which is the right way to build one -- and requiring tuples would have edited about fifty construction sites, forty of them in tests with no opinion about the container, while still leaving the invariant unenforced for the next caller. `dataclasses.replace` goes through `__init__`, so the five `replace(project, pages=[...])` sites in `state.py` needed no edit; there is a test that says so. Exactly seven existing assertions compared against a list literal and were changed. `_SessionState` keeps its `list` and is not frozen: it is the on-disk print cursor and is advanced in place.
- Watch: **Hashability is now available and must not be used for the two plan digests.** `export._plan_hash` keys a disk cache and has to be stable across processes, which `hash()` is not; `print_session._hash_plan` is being changed by B3 to cover more. A newly-hashable type is an invitation to replace a hand-rolled digest, and both of these would be wrong to replace. Also: the roadmap named two of the five offenders, and the other three came out of one grep for `": list["` over dataclass fields -- the same "count them, do not remember them" lesson as the fourth platform-directory ladder.
- Commit: <fill in>
```

## 8. Traps

- **`object.__setattr__` is required.** `self.sheets = tuple(...)` inside
  `__post_init__` of a frozen dataclass raises
  `FrozenInstanceError: cannot assign to field 'sheets'`.
- **Coerce `signatures` too**, even though it is already annotated as a
  tuple. Its default is `()` but a caller can pass a list, and a field that
  is a tuple only when the caller was careful is the bug being fixed.
- **`Side.__post_init__` already exists** and raises on empty `pages`.
  `SheetPlan` and `Project` are different classes, so there is nothing to
  merge — but read it first, because it is the house pattern for this.
- **`SheetPlan` has a default field** (`signatures: tuple[...] = ()`).
  `__post_init__` goes after all the field declarations, not between them.
- **Do not change `_SessionState`.** It is `@dataclass` without `frozen`,
  and `_advance` mutates it.
- **Do not "simplify" `export._plan_hash` or `print_session._hash_plan`
  into `hash(plan)`.** See §6. `hash()` of a str is salted per process by
  default, and `_plan_hash` names files on disk that outlive the process.
- **`tests/test_printing.py:72`** compares against
  `list(reversed(front.sheet_order))`. Changing only the left side of that
  comparison leaves it failing; the `list(...)` on the right has to become
  `tuple(...)`.
- **`json.dump` writes a tuple as an array**, so no saved `.deckle` or
  session file changes shape — but `json.load` gives lists back, and
  `project_io._project_from_dict` builds a list and passes it, which the
  coercion handles. Do not add a second conversion there.
- `python -m deckle` launches the GUI; only the `[HUMAN]` row wants it.
</content>
