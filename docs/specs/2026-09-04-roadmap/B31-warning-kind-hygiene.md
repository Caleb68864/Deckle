# B31 — Make `LayoutWarning.kind`'s Literal say exactly what Deckle emits

**Roadmap item:** `docs/ROADMAP.md` B31
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`LayoutWarning.kind` is documented as "a stable identifier for the class of
problem". Two consumers read it as one: the CLI prints `[{w.kind}] sheet
{w.sheet_index}: {w.detail}` and `deckle.core.diagnostics.log_event` records
it as a structured field, which is what a bug report is grepped for. The
declared set and the emitted set have drifted apart in both directions.

**Emitted but not declared.** `deckle/core/loader.py:552` emits
`kind="skipped_non_image_files"` when a folder of scans also holds a
`Thumbs.db` or a `README`. That value is not in the Literal, so a type
checker reading `LayoutWarning` is told a value the program produces on an
entirely ordinary import cannot exist.

**Declared but not emitted.** `landscape_imageable_unverified`
(`deckle/core/models.py:245`) has never been emitted by anything. It was
added by signatures-v2 REQ-007 for a feature that was then deferred (see
Traps), and `tests/test_models.py:174` constructs one, which is the only
place in the tree it appears outside the declaration.

This is the shape `docs/decisions.md` records catching repeatedly — a
declared-but-not-honoured value set. Verified on the current tree:

```bash
.venv/bin/python - <<'PY'
import ast, pathlib, typing
kinds = set()
for p in pathlib.Path("deckle").rglob("*.py"):
    for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"), filename=str(p))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LayoutWarning":
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kinds.add(kw.value.value)
from deckle.core.models import LayoutWarning
declared = set(typing.get_args(typing.get_type_hints(LayoutWarning)["kind"]))
print("emitted not declared:", sorted(kinds - declared))
print("declared not emitted:", sorted(declared - kinds))
PY
```

Output on commit `08e7f49`:

```
emitted not declared: ['skipped_non_image_files']
declared not emitted: ['landscape_imageable_unverified']
```

What it costs a person printing a book: nothing today, directly. What it
costs is the guarantee. The next person adding a warning has no way to know
whether the list they are editing is the authority or a stale copy, and the
first time the two disagree in a way that *does* matter — a UI that
switches on `kind` to pick an icon, a `--json` output (N14) that documents
its own vocabulary — the failure is silent.

## 2. Current code

The declaration, `deckle/core/models.py:236-248`:

```python
    sheet_index: int
    kind: Literal[
        "clipped_by_page",
        "clipped_by_imageable_area",
        "mixed_orientation",
        "mixed_dpi",
        "sheet_orientation",
        "signature_padding",
        "creep_advisory",
        "landscape_imageable_unverified",
        "grain_direction",
    ]
    detail: str
```

The undeclared emission, `deckle/core/loader.py:544-560`:

```python
    if skipped_names:
        # Advisory, not fatal: a folder of scans routinely also holds a
        # Thumbs.db or a README. Said out loud anyway, because the other
        # reason for a skipped file is a scan in a format Deckle does not
        # read -- and that silently drops a page out of the book.
        warnings.append(
            LayoutWarning(
                sheet_index=-1,  # not yet applicable at import time
                kind="skipped_non_image_files",
                detail=(
                    f"{len(skipped_names)} file(s) in {path!r} were not "
                    f"imported because they are not images: "
                    f"{', '.join(skipped_names[:5])}"
                    + ("..." if len(skipped_names) > 5 else "")
                ),
            )
        )
```

**Every `LayoutWarning(...)` construction under `deckle/`**, from
`grep -rn "LayoutWarning(" --include='*.py' deckle/`:

| Site | `kind=` |
|---|---|
| `deckle/core/loader.py:550` | `skipped_non_image_files` (`:552`) |
| `deckle/core/loader.py:566` | `mixed_dpi` (`:568`) |
| `deckle/core/layout.py:277` | `grain_direction` (`:279`) |
| `deckle/core/layout.py:389` | `mixed_orientation` (`:391`) |
| `deckle/core/layout.py:415` | `clipped_by_page` (`:417`) |
| `deckle/core/layout.py:459` | `clipped_by_page` (`:461`) |
| `deckle/core/layout.py:818` | `creep_advisory` (`:820`) |
| `deckle/core/layout.py:890` | `sheet_orientation` (`:892`) |
| `deckle/core/layout.py:903` | `signature_padding` (`:905`) |
| `deckle/app/views/preview_view.py:130` | `clipped_by_page` (`:132`) |
| `deckle/app/views/preview_view.py:143` | `clipped_by_imageable_area` (`:145`) |

Nine distinct emitted values. Note that `preview_view.py` emits two of them
from the app layer, so a check that walks only `deckle/core/` would miss
`clipped_by_imageable_area` and wrongly call it dead.

**Every reader of `.kind`**, from
`grep -rn "\.kind" --include='*.py' deckle/ tests/`:

- `deckle/cli.py:895` — `deckle info`, prints `[{w.kind}] sheet ...` to stdout.
- `deckle/cli.py:917-920` — `_emit_warnings`, prints to stderr and calls
  `log_event("layout_warning", kind=w.kind, ...)`.
- `deckle/app/views/preview_view.py` — collects warnings for the badge; it
  does not branch on the value.
- `tests/test_hardening_io.py:471, 489, 490` — assert the literal string
  `"skipped_non_image_files"` appears in `deckle info` stdout and
  `deckle export` stderr.

Nothing anywhere branches on `kind`, so changing the declared set cannot
change program behaviour. It changes what a type checker and a reader are
promised.

**Existing tests that touch this code:**

- `tests/test_models.py:119-127` `test_layout_warning_fields` — constructs
  one with `clipped_by_page`.
- `tests/test_models.py:169-177` `test_layout_warning_new_kinds` — loops over
  `("sheet_orientation", "signature_padding", "creep_advisory",
  "landscape_imageable_unverified")` and constructs each. **This is the test
  that must be updated deliberately** — see step 3.
- `tests/test_hardening_io.py:460-490` — the two end-to-end assertions that
  `skipped_non_image_files` reaches the user.
- `tests/test_loader.py:133-153` — mixed-DPI warnings from `load_image_dir`.

## 3. Change

Make the Literal the exact set of values `deckle/` emits, and add a test
that keeps it that way in both directions.

**The fate of `landscape_imageable_unverified`: remove it.** Nothing emits
it, and the feature it was reserved for — a per-orientation imageable area
in `PrinterProfile` — is roadmap **F12**, recorded there as an open question
with nothing built. When F12 lands it will re-add the value in the same
commit as the code that emits it, which is the only ordering under which the
declaration is ever true. Keeping a reserved value "so F12 does not have to
add it back" is exactly the trade this item exists to refuse: one line saved
later, against a declared vocabulary that lies in the meantime. The
alternative considered and rejected was keeping it with a comment marking it
reserved — rejected because the new test in step 4 would then need an
exemption list, and an exemption list is where the next stale value hides.

The resulting set is nine values: the eight already declared and emitted,
plus `skipped_non_image_files`.

### Steps

1. **`deckle/core/models.py`**, in `LayoutWarning`: delete the line
   `"landscape_imageable_unverified",` (currently `:245`) and add
   `"skipped_non_image_files",` after `"mixed_dpi",`. The Literal becomes,
   verbatim:

   ```python
    kind: Literal[
        "clipped_by_page",
        "clipped_by_imageable_area",
        "mixed_orientation",
        "mixed_dpi",
        "skipped_non_image_files",
        "sheet_orientation",
        "signature_padding",
        "creep_advisory",
        "grain_direction",
    ]
   ```

   Order: `skipped_non_image_files` sits beside `mixed_dpi` because they are
   the two import-time kinds — the only two that carry `sheet_index=-1` — and
   grouping them says so without a comment.

2. **`deckle/core/models.py`**, in `LayoutWarning`'s docstring: extend the
   `:ivar kind:` line to name the invariant the new test enforces, so a
   reader editing the list knows what will fail. Replace

   ```python
    :ivar kind: a stable identifier for the class of problem.
   ```

   with

   ```python
    :ivar kind: a stable identifier for the class of problem. The Literal
        is the exact set of values ``deckle/`` emits, in both directions:
        ``tests/test_warning_kinds.py`` walks every ``LayoutWarning(...)``
        call in the package and fails on a value that is emitted and not
        declared, and on one that is declared and never emitted. A value
        reserved for a feature that is not built yet therefore belongs in
        that feature's commit, not in this list.
   ```

3. **`tests/test_models.py:169-177`**: `test_layout_warning_new_kinds`
   constructs `landscape_imageable_unverified`. Drop that entry from the
   tuple, leaving:

   ```python
   def test_layout_warning_new_kinds():
       for kind in (
           "sheet_orientation",
           "signature_padding",
           "creep_advisory",
       ):
           warning = LayoutWarning(sheet_index=0, kind=kind, detail="x")
           assert warning.kind == kind
   ```

   A frozen dataclass does not validate a `Literal` at runtime, so this test
   passes either way — which is precisely why it has to be edited by hand
   rather than left to fail. Leaving it would keep a live reference to a
   value the model no longer declares.

4. **New file `tests/test_warning_kinds.py`** — the test described in
   section 4. Write it before step 1 and confirm it fails.

## 4. Tests

Write `tests/test_warning_kinds.py` first, run it, and confirm both new
tests fail on the unfixed tree for the two different reasons below.

**File:** `tests/test_warning_kinds.py` (new)

Module docstring, in the house voice:

```python
"""Every warning kind Deckle emits is declared, and every declared one is emitted.

``LayoutWarning.kind`` is the only stable identifier a warning has: the CLI
prints it, ``log_event`` records it as a structured field, and a bug report
is grepped for it. Nothing branches on the value, which is exactly why the
declaration drifted -- a wrong list costs nothing until something reads it,
and by then the list is old.

Both directions matter and they fail differently. A kind emitted and not
declared makes the type a lie about code that already runs; a kind declared
and never emitted is a promise to a reader that some path produces it. This
walks the source rather than importing and calling, so a warning raised on a
path no test exercises is still counted.
"""
```

**Helper:**

```python
def _emitted_kinds() -> dict[str, list[str]]:
    """Every ``kind=`` string literal passed to ``LayoutWarning(...)``.

    Keyed by kind, valued by the ``file:line`` of each construction, so a
    failure names where to look rather than only what is wrong.

    Matched on the callee's name rather than by importing: the app layer
    emits two kinds the core never does, and a walk that only imported
    ``deckle.core`` would report both as dead.
    """
```

Implementation: `pathlib.Path(REPO_ROOT / "deckle").rglob("*.py")`, skipping
`__pycache__`; `ast.parse` each; for every `ast.Call` whose
`getattr(node.func, "id", None) == "LayoutWarning"`, take the keyword `kind`
whose value is an `ast.Constant` and record `f"{relpath}:{node.lineno}"`.
`REPO_ROOT = Path(__file__).resolve().parents[1]`.

```python
def _declared_kinds() -> set[str]:
    """The Literal's members, resolved through ``get_type_hints``.

    ``models.py`` uses ``from __future__ import annotations``, so the
    annotation is a string until something resolves it -- reading
    ``dataclasses.fields(...)[i].type`` gives back source text, not a type.
    """
    return set(typing.get_args(typing.get_type_hints(LayoutWarning)["kind"]))
```

### `test_every_emitted_warning_kind_is_declared`

- **Setup:** none.
- **Assertion in words:** the set of `kind=` literals passed to
  `LayoutWarning(...)` anywhere under `deckle/` is a subset of the Literal's
  members; the failure message names each offending kind together with the
  `file:line` that emits it and says to add it to `LayoutWarning.kind`.
- **Expected failure on the unfixed tree:**
  `AssertionError: emitted but not declared by LayoutWarning.kind: {'skipped_non_image_files': ['core/loader.py:550']} -- add each to the Literal in deckle/core/models.py`

### `test_every_declared_warning_kind_is_emitted`

- **Setup:** none.
- **Assertion in words:** every member of the Literal is emitted by at least
  one `LayoutWarning(...)` call under `deckle/`; the failure message names
  the dead values and says that a kind reserved for unbuilt work belongs in
  that work's commit.
- **Expected failure on the unfixed tree:**
  `AssertionError: declared by LayoutWarning.kind but never emitted: ['landscape_imageable_unverified'] -- delete it, or emit it from the code that needs it`

### `test_the_import_time_kinds_carry_no_sheet_index`

A third, cheap test that pins the one semantic distinction the new grouping
in step 1 relies on, so the ordering is not just cosmetic.

- **Setup:** a `tmp_path` folder holding one image (`tests/test_loader.py`'s
  `_make_image` pattern: `PIL.Image.new("RGB", (200, 100))` saved as
  `page1.jpg` with `dpi=(150, 150)`) and one non-image (`Thumbs.db` with
  arbitrary bytes). Call `deckle.core.loader.load_image_dir(str(folder))`.
- **Assertion in words:** the returned `ImportedPages.warnings` contains a
  warning whose `kind` is `"skipped_non_image_files"` and whose
  `sheet_index` is `-1`, because at import time there are no sheets for a
  warning to point at.
- **Expected failure on the unfixed tree:** none — this one passes before
  and after. It exists to keep the declaration's grouping honest if someone
  later gives import warnings a real sheet index.

Do **not** widen the walk to `Mark.kind`. `deckle/core/marks.py` emits
`sewing_station`, `signature_order`, `cut_line` and `fold_line`, and all
four are declared (`deckle/core/models.py:157`) — there is nothing to fix,
and a second AST walk in this spec is scope creep.

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_warning_kinds.py` |
| The model tests still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_models.py tests/test_loader.py tests/test_hardening_io.py` |
| The dead value is gone from the package | `! grep -rn "landscape_imageable_unverified" --include='*.py' deckle/ tests/` |
| The emitted value is declared | `grep -q '"skipped_non_image_files",' deckle/core/models.py` |
| Emitted and declared sets agree | `.venv/bin/python -c "import ast,pathlib,typing; from deckle.core.models import LayoutWarning; k={kw.value.value for p in pathlib.Path('deckle').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='LayoutWarning' for kw in n.keywords if kw.arg=='kind' and isinstance(kw.value,ast.Constant)}; d=set(typing.get_args(typing.get_type_hints(LayoutWarning)['kind'])); assert k==d, (sorted(k-d), sorted(d-k)); print('OK')"` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two R0.3/R0.4 failures from `00-environment.md` and nothing else) |

The third row is written with `!` rather than `-c 0` because a grep that
finds nothing exits 1; `! grep ...` therefore exits 0 exactly when the value
is absent. Confirmed against the current tree: the same grep without `!`
finds `deckle/core/models.py:245` and `tests/test_models.py:174`.

## 6. Out of scope

- **B15/F12** — per-orientation imageable area, the feature
  `landscape_imageable_unverified` was reserved for. When it is built it
  re-adds the value alongside the code that emits it.
- **B19** — `landscape_policy`'s `scale`/`letterbox` being identical. It
  emits `mixed_orientation` from the `rotate` branch only; that is a
  behaviour question, not a vocabulary one.
- **N14** — a `--json` output for `info`/`schedule` that would publish this
  vocabulary. This spec makes that vocabulary correct; it does not publish it.
- Do not add validation of `kind` at construction time. `LayoutWarning` is a
  frozen dataclass with no `__post_init__` beyond `Side`'s, and
  `deckle.core.schema.check_values` is for values read off disk. A warning is
  constructed in-process from a literal; the check belongs in the test suite,
  not in a hot path.

## 7. decisions.md entry

```
## 2026-09-05 — The warning vocabulary had drifted in both directions at once
- Symptom: `LayoutWarning.kind`'s `Literal` and the set of kinds Deckle actually emits had diverged both ways. `loader` emitted `skipped_non_image_files` on any folder of scans containing a `Thumbs.db` — an undeclared value on an entirely ordinary import — while `landscape_imageable_unverified` had been declared since signatures v2 and emitted by nothing, reserved for a per-orientation imageable area that was deferred. Nothing branches on `kind`, which is exactly why neither half was noticed.
- Fix: The Literal is now the exact emitted set, nine values. `skipped_non_image_files` joins it beside `mixed_dpi` — the two import-time kinds, the only ones carrying `sheet_index=-1`. `landscape_imageable_unverified` is deleted; the feature that needs it will add it in the same commit as the code that emits it, which is the only ordering under which the declaration is ever true.
- Surfaces: The check is `tests/test_warning_kinds.py`, an AST walk over every `LayoutWarning(...)` call under `deckle/` asserted against the Literal in both directions. It walks the **package**, not `deckle.core`: `preview_view` emits two kinds the core never does, and a core-only walk would have reported `clipped_by_imageable_area` as dead and deleted a live value.
- Watch: A frozen dataclass does not validate a `Literal` at runtime, so nothing failed while the two lists disagreed and nothing fails when they are corrected — `tests/test_models.py` had to be edited by hand to stop constructing the deleted kind. **A reserved-but-unemitted value is a promise with no test behind it.** This is the same declared-but-not-honoured shape the log has recorded before; the difference is that this time there is a mechanical check.
- Commit: (this commit)
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for anything headless.
- **`dataclasses.fields(LayoutWarning)[1].type` is the string
  `'Literal[...]'`, not a type.** `deckle/core/models.py` starts with
  `from __future__ import annotations`, so every annotation is deferred.
  Resolve with `typing.get_type_hints(LayoutWarning)["kind"]` then
  `typing.get_args(...)`. Verified working on this tree.
- **The AST walk must cover `deckle/app/`, not just `deckle/core/`.**
  `deckle/app/views/preview_view.py:130,143` are the only emitters of
  `clipped_by_imageable_area`. Walking the source rather than importing also
  avoids pulling Qt into the test process.
- **`tests/test_models.py:169-177` pins the current behaviour and will not
  fail on its own.** It constructs `landscape_imageable_unverified`
  explicitly; a frozen dataclass accepts it regardless of the Literal. Update
  it in the same commit or it becomes a live reference to a deleted value.
- **REQ-007 in the signatures-v2 spec tree names the removed value**
  (`docs/specs/2026-08-04-deckle-signatures-v2.md:185,255`,
  `docs/specs/deckle-signatures-v2/sub-spec-1-model-changes.md:344,403`,
  `docs/specs/deckle-signatures-v2/traceability.md:18`). Those are historical
  records of a delivered spec and are **not** to be rewritten here; the
  decisions.md entry is where the reversal is recorded. None of them is
  executed by a test — `tests/test_spec_residue.py` covers other criteria and
  does not grep `models.py` for kinds; verified.
- **`tests/test_hardening_io.py:471,489-490` assert the literal string
  `"skipped_non_image_files"` in CLI output.** They must keep passing: this
  spec changes only the declaration, never the emitted string. If one of them
  goes red, the kind was renamed rather than declared.
- The pre-commit hook refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`.
