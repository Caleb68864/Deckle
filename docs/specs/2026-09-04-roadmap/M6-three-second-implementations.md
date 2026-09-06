# M6 — Delete three second implementations: the atomic write, the face order, the pdfium lock's import

**Roadmap item:** `docs/ROADMAP.md` M6
**Depends on:** R0.1 (the acceptance rows run CLI and export tests that open
`tests/fixtures/sample.pdf`)
**Blocks:** —
**Size:** S
**Decision needed first:** none.

---

## 1. Context

Three places where one idea is written twice. None of them is a live bug
today; each is the shape that produced a live bug elsewhere in this
codebase, and `docs/decisions.md` has the receipts — *"the fourth copy,
found by the pass that claimed there were three"* (2026-08-07, platform
directory ladders) and *"three functions computed fore-edge creep and
disagreed"* (2026-08-08).

1. **`export.export` hand-rolls what `paths.atomic_output` does.** Both
   `mkstemp` beside the target, write, `os.replace`, clean up on failure.
   `tests/test_single_source_decisions.py::test_only_paths_replaces_a_file_in_place`
   already enforces "only `paths` renames onto a target" and carries
   `deckle/core/export.py` as a hand-written exception in its allow-list.
   The exception's stated reason — *"it builds a PDF through pikepdf rather
   than writing bytes it holds"* — is exactly the case `atomic_output` was
   written for: it yields a **path** for a caller that produces its own
   bytes, which is why `dummy.make_numbered_pdf` and `cli`'s crop-preview
   already use it.
2. **`render.render_sheet` re-derives `export`'s face ordering.** `export`
   writes one PDF page per face that exists, front first — so a sheet with a
   back and no front puts that back at page **0**. `render_sheet` works that
   out again from `has_front`/`has_back`, under a comment pointing at the
   code it is duplicating. Two implementations of "which page is the back"
   is how a preview shows the wrong face on the one sheet shape nobody
   tests.
3. **`loader` imports the rasteriser to borrow a lock.** `deckle/core/loader.py:27`
   is `from deckle.core.render import pdfium_guard`, and `render` imports
   `deckle.core.export`, which pulls in pikepdf, the printing module and the
   diagnostics log. The import graph says "importing a PDF depends on
   rendering and exporting", which is not true and reads as though it were.

## 2. Current code

### 1 — the hand-rolled atomic write

`deckle/core/export.py:595-625`, verbatim:

```python
    _check_writable(out_path)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(out_path)) or None)
    os.close(tmp_fd)
    try:
        _export_batched(
            plan,
            selected,
            tmp_path,
            rule=rule,
            side=side,
            # By keyword: these two are both "extra behaviour flags" and
            # positionally interchangeable to the reader, so an argument
            # order that drifts would swap a tuple and a bool silently.
            back_offset_pt=back_offset_pt,
            rotate_180=rotate_180,
        )
        if rotate_180:
            rotate_pages_180(tmp_path)
        # Checked on the scratch file, before it is renamed into place: a
        # file that fails this must never reach the destination, and a
        # previous good export sitting at that path has to survive.
        _verify_output(
            tmp_path,
            expected_pages=sum(len(_sides(sheet, side)) for sheet in selected),
            paper_pt=plan.paper_pt,
        )
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            _safe_remove(tmp_path)
```

`deckle/core/paths.py:174-210`, verbatim:

```python
def atomic_output(path: str | os.PathLike[str]):
    """Yield a scratch path to write, renamed over ``path`` on success.

    For output written by something that wants a filename of its own --
    an image encoder, a serialiser -- where :func:`write_text_atomic` does
    not fit because the caller, not this module, produces the bytes.

    The scratch file keeps the target's **extension**, because the writer
    usually infers its format from it: a PIL ``Image.save`` handed a
    ``.tmp`` path cannot tell what it is being asked to encode.

    The guarantee is the one :func:`deckle.core.export.export` already
    gives for PDFs -- a write that fails partway leaves whatever was at
    ``path`` untouched -- so every output Deckle names behaves the same
    way rather than depending on which command produced it.

    :param path: the file to end up with.
    :returns: a context manager yielding the scratch path to write to.
    :raises OSError: the scratch file cannot be created, or the rename
        fails. The scratch file is removed first either way.
    """
    target = Path(path)
    directory = target.parent if str(target.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{target.stem}.", suffix=target.suffix
    )
    os.close(fd)
    try:
        yield tmp_name
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
```

Note its docstring already cites `export` as the standard it was built to.

Existing callers of `atomic_output`: `deckle/cli.py:1176` (crop-preview),
`deckle/core/dummy.py:139`. Imported at `deckle/cli.py:36` and
`deckle/core/dummy.py:25`.

`deckle/core/export.py:971-981`, the cleanup helper that must **stay**
(still used at lines 953, 956, 965, 968, 1053 for the sheet cache):

```python
def _safe_remove(path: str) -> None:
    """Delete a temp file, tolerating failure.

    Failing to clean up must never fail the export that succeeded -- but
    silent failures accumulate, and a user who runs out of disk after a
    long session deserves a trail explaining where the space went.
    """
    try:
        os.remove(path)
    except OSError as exc:
        log_exception("temp_file_cleanup_failed", exc, path=path)
```

`tests/test_single_source_decisions.py:79-99`, the enforcement that has to
be tightened:

```python
def test_only_paths_replaces_a_file_in_place():
    """``os.replace`` onto a target belongs behind ``paths``.

    Every store Deckle keeps is written by rename so a failure leaves the
    previous copy intact, and the guarantee is only as good as its least
    careful implementation. ``export`` is the documented exception: it
    builds a PDF through pikepdf rather than writing bytes it holds, so it
    does its own scratch-and-rename and says so in its docstring.
    """
    allowed = {"deckle/core/paths.py", "deckle/core/export.py"}
```

Other `os.replace` sites in the package: none outside those two (that test
passes today, which is what proves it).

`export`'s own docstring text that becomes stale,
`deckle/core/export.py:546-551`:

```
    The scratch file is removed on every exit path -- success, exception, or
    cancellation upstream. Cleanup itself never raises: on Windows the
    scratch file can still be held briefly by a handle the failing export
    was using, and letting that ``PermissionError`` escape the ``finally``
    would replace the real cause of the failure with a misleading one.
```

### 2 — the duplicated face order

`deckle/core/export.py:384-412`, the authority:

```python
def _sides(sheet: Sheet, side: str | None = None) -> list[Side | None]:
    """The faces of ``sheet`` to write, front then back.
    ...
    """
    if side is None:
        return [face for face in (sheet.front, sheet.back) if face is not None]
    return [sheet.front if side == "front" else sheet.back]
```

`deckle/core/render.py:250-260`, the copy:

```python
        # The single-sheet export contains only the sides that exist, in
        # front-then-back order -- see export._export_batched/_sides.
        if side == "front":
            if not has_front:
                return _empty_rendered_page()
            page_index = 0
        else:
            if not has_back:
                return _empty_rendered_page()
            page_index = 1 if has_front else 0
```

with, at `deckle/core/render.py:229-233`:

```python
    by_index = {sheet.index: sheet for sheet in plan.sheets}
    sheet = by_index.get(sheet_index)
    has_front = sheet is not None and sheet.front is not None
    has_back = sheet is not None and sheet.back is not None
```

`_sides` call sites: `export.py:619`, `export.py:799`, and
`export.py:629`'s docstring. `render` never calls it — it re-derives it.

### 3 — the lock, and who imports it

`deckle/core/render.py:68-98` defines `_PDFIUM_LOCK = threading.RLock()`
with a 30-line docstring on why it exists (a native access violation, "one
in thirty concurrent renders", "reads to a user as *Deckle randomly
closes*"), and `deckle/core/render.py:101-129` defines `pdfium_guard()`
returning it.

`_PDFIUM_LOCK` is used at `render.py:129, 163, 262, 322, 392`.

Cross-module consumers of the guard:

```
deckle/app/backend.py:44:    pdfium_guard, rasterize_page, RenderedPage, render_sheet,
deckle/app/backend.py:215:        with pdfium_guard():
deckle/core/loader.py:27:from deckle.core.render import pdfium_guard
deckle/core/loader.py:335:    with pdfium_guard():
```

`loader` imports **nothing else** from `render`. `render.py:34` is
`from deckle.core import export`, so `loader` → `render` → `export` →
`pikepdf`, `deckle.core.printing`, `deckle.core.diagnostics`.

Tests that touch the lock:

```
tests/test_render_concurrency.py:44:    _PDFIUM_LOCK, clear_ink_bbox_cache, ink_bbox, rasterize_page, thumbnails,
tests/test_render_concurrency.py:137:    assert _PDFIUM_LOCK.acquire(blocking=False)
tests/test_render_concurrency.py:139:        assert _PDFIUM_LOCK.acquire(blocking=False), "not reentrant"
tests/test_render_concurrency.py:140:        _PDFIUM_LOCK.release()
tests/test_render_concurrency.py:142:        _PDFIUM_LOCK.release()
tests/test_render_concurrency.py:216:                    if "pdfium_guard()" in previous or "_PDFIUM_LOCK" in previous:
```

Line 216 is inside `test_every_pdfium_document_is_opened_under_the_guard`,
which **reads the source of every `.py` under `deckle/`** and, for each line
matching `pdfium\.PdfDocument\s*\(`, walks upward for a line at shallower
indent containing `pdfium_guard()` or `_PDFIUM_LOCK`, stopping at a `def` or
`class`. Any edit here has to keep that scan satisfied.

`tests/test_core_purity.py` walks every module under `deckle/core/` and
imports it; `tests/test_docs_coverage.py` requires a `docs/api/<name>.rst`
for every module under `deckle/`.

## 3. Change

Three independent edits. They can be made in one commit or three; the order
below is smallest-blast-radius first.

### Part A — `export` uses `atomic_output`

1. **`deckle/core/export.py`**, imports (line 40). Change

   ```python
   from deckle.core.paths import evict_lru_files
   ```

   to

   ```python
   from deckle.core.paths import atomic_output, evict_lru_files
   ```

   Leave `import os` and `import tempfile` — both are still used by
   `export_sheet_cached` and `_cache_dir`.

2. **`deckle/core/export.py`**, replace lines 595-625 (quoted in §2) with:

   ```python
       _check_writable(out_path)

       with atomic_output(out_path) as tmp_path:
           _export_batched(
               plan,
               selected,
               tmp_path,
               rule=rule,
               side=side,
               # By keyword: these two are both "extra behaviour flags" and
               # positionally interchangeable to the reader, so an argument
               # order that drifts would swap a tuple and a bool silently.
               back_offset_pt=back_offset_pt,
               rotate_180=rotate_180,
           )
           if rotate_180:
               rotate_pages_180(tmp_path)
           # Checked on the scratch file, before it is renamed into place: a
           # file that fails this must never reach the destination, and a
           # previous good export sitting at that path has to survive.
           _verify_output(
               tmp_path,
               expected_pages=sum(len(_sides(sheet, side)) for sheet in selected),
               paper_pt=plan.paper_pt,
           )
   ```

   `_check_writable` stays and stays **outside** the context manager: it is
   the pre-flight that makes the docstring's "raises before any bytes are
   written" true, and `atomic_output` creates a file the moment it is
   entered.

3. **`deckle/core/export.py`**, replace the now-stale paragraph at lines
   546-551 with:

   ```
       The scratch file is removed on every exit path -- success, exception, or
       cancellation upstream -- by :func:`deckle.core.paths.atomic_output`,
       which is the single implementation of write-then-rename for every
       output Deckle names. Cleanup itself never raises: on Windows the
       scratch file can still be held briefly by a handle the failing export
       was using, and letting that ``PermissionError`` escape would replace
       the real cause of the failure with a misleading one.
   ```

4. **`tests/test_single_source_decisions.py`**, line 89: change

   ```python
       allowed = {"deckle/core/paths.py", "deckle/core/export.py"}
   ```

   to

   ```python
       allowed = {"deckle/core/paths.py"}
   ```

   and rewrite the docstring's last sentence — the exception it documents no
   longer exists:

   ```
       ``export`` was the documented exception until it stopped being one:
       it builds a PDF through pikepdf rather than writing bytes it holds,
       which is precisely the case ``atomic_output`` yields a path for.
   ```

   **This is the test edit that makes Part A a real change rather than a
   cosmetic one.** Do not leave the allow-list at two entries.

Behaviour deltas, both deliberate and both worth knowing:

- The scratch file is renamed from `tmpXXXXXXXX.pdf` to
  `.<stem>.XXXXXXXX.pdf` — a dot-file, which is what every other Deckle
  output already leaves behind while it is being written.
  `tests/test_cli_output_durability.py::test_neither_command_leaves_debris_beside_its_output`
  only inspects `schedule` and `crop-preview` outputs and is unaffected.
- A failed cleanup no longer logs `temp_file_cleanup_failed`; `atomic_output`
  swallows the `OSError`. Nothing asserts that event
  (`grep -rn temp_file_cleanup_failed` matches only `export.py:981`), and
  `_safe_remove` keeps logging for the sheet cache, which is the path where
  accumulation actually happens.

### Part B — one face-order function

5. **`deckle/core/export.py`**, add immediately after `_sides` (which ends at
   line 412):

   ```python
   def face_page_index(sheet: Sheet, side: str) -> int | None:
       """Which page of a whole-sheet export carries ``side``.

       ``export`` with ``side=None`` writes one page per face that
       *exists*, front first, so a sheet with a back and no front puts
       that back at page ``0`` and not page ``1``. Anything reading one
       face back out of an exported sheet has to know that, and
       :func:`deckle.core.render.render_sheet` knew it a second time --
       under a comment naming this module, which is the tell.

       :param sheet: the sheet as exported.
       :param side: ``"front"`` or ``"back"``.
       :returns: the zero-based page index, or ``None`` when that face
           does not exist on this sheet and there is nothing to read.
       """
       if side == "front":
           return 0 if sheet.front is not None else None
       if sheet.back is None:
           return None
       return 1 if sheet.front is not None else 0
   ```

   Public (no leading underscore) because it crosses a module boundary,
   which is the same reason `pdfium_guard` is public.

   **Chosen: a positional computation. Rejected: `_sides(sheet, None).index(face)`.**
   `Side` is a frozen dataclass, so two structurally identical faces compare
   equal and `.index` would return `0` for a back that matches its own front.
   That is not a hypothetical for a blank-both-sides sheet.

6. **`deckle/core/render.py`**, replace lines 229-233 and 250-260 with:

   ```python
       by_index = {sheet.index: sheet for sheet in plan.sheets}
       sheet = by_index.get(sheet_index)
       page_index = None if sheet is None else export.face_page_index(sheet, side)
   ```

   before the `export_sheet_cached` call, and inside the `try`:

   ```python
           if page_index is None:
               return _empty_rendered_page()
   ```

   in place of the ten quoted lines. `has_front` and `has_back` disappear;
   grep the function to confirm nothing else reads them.

   Keep the `try: ... finally: pass` at the end of `render_sheet` exactly as
   it is. It is dead scaffolding and it is **M7's**, not this spec's; deleting
   it here would make two specs conflict in one function.

### Part C — `deckle/core/pdfium_lock.py`

7. **New file `deckle/core/pdfium_lock.py`.** Move `_PDFIUM_LOCK` (render.py
   lines 68-98, docstring and all) and `pdfium_guard` (lines 101-129,
   docstring and all) into it, verbatim, plus `import threading` and a module
   docstring:

   ```python
   """The one lock every pdfium call in the process holds.

   A module of its own because of who needs it. ``loader`` touches pdfium
   to measure an imported document's pages, and reached the guard by
   importing ``deckle.core.render`` -- which imports ``deckle.core.export``,
   which imports pikepdf and the printing module. Importing a PDF does not
   depend on rendering or exporting one, and an import graph that says it
   does is read as though it were true.

   Nothing here imports pypdfium2. The lock is a ``threading.RLock`` and
   the rule about it is a rule about callers, so this module is three
   lines of code and a page of reasons.

   Qt-free, like everything under ``deckle.core`` -- see
   ``tests/test_core_purity.py``.
   """
   ```

   The existing `pdfium_guard` docstring's sentence *"Three callers outside
   this module need it"* is now wrong by one — every caller is outside this
   module. Change that sentence to *"Every caller is outside this module, and
   the printing one is the reason it is public rather than private."* and
   leave the rest of the paragraph.

8. **`deckle/core/render.py`** — delete lines 68-129 (the lock, its
   docstring, and `pdfium_guard`) and add to the import block at lines 32-35:

   ```python
   from deckle.core.pdfium_lock import pdfium_guard
   ```

   Keep `import threading` — `_ink_bbox_cache_lock` at line 383 still needs
   it.

   Re-exporting `pdfium_guard` from `render` this way is deliberate:
   `deckle/app/backend.py:44` imports it from `render` alongside four other
   names, and that import is honest — the backend does rasterise.

9. **`deckle/core/render.py`** — replace `with _PDFIUM_LOCK:` with
   `with pdfium_guard():` at the four remaining sites (163, 262, 322, 392 in
   the current numbering). This keeps
   `test_every_pdfium_document_is_opened_under_the_guard`'s source scan
   satisfied, which matches on the literal text `pdfium_guard()`.

10. **`deckle/core/loader.py`**, line 27: change

    ```python
    from deckle.core.render import pdfium_guard
    ```

    to

    ```python
    from deckle.core.pdfium_lock import pdfium_guard
    ```

11. **`tests/test_render_concurrency.py`**, line 44: drop `_PDFIUM_LOCK` from
    the `deckle.core.render` import and add
    `from deckle.core.pdfium_lock import pdfium_guard` at the top. Lines
    137-142 become:

    ```python
        assert pdfium_guard().acquire(blocking=False)
        try:
            assert pdfium_guard().acquire(blocking=False), "not reentrant"
            pdfium_guard().release()
        finally:
            pdfium_guard().release()
    ```

    That is a better test than the one it replaces: reentrancy is a promise
    `pdfium_guard`'s docstring makes to callers, and callers only ever see
    the function.

    Leave line 216's `or "_PDFIUM_LOCK"` alternative alone. It is now dead
    but harmless, and the scan's job is to be generous about how the guard is
    spelled.

12. **`docs/api/core.pdfium_lock.rst`** — new file:

    ```rst
    deckle.core.pdfium_lock
    =======================

    .. automodule:: deckle.core.pdfium_lock
       :members:
       :show-inheritance:
    ```

13. **`docs/api/core.rst`** — add `core.pdfium_lock` to the toctree,
    immediately after `core.render`. Also extend the "Reading order"
    paragraph's `(:mod:`~deckle.core.export`, :mod:`~deckle.core.render`)`
    to `(:mod:`~deckle.core.export`, :mod:`~deckle.core.render`,
    :mod:`~deckle.core.pdfium_lock`)`.

## 4. Tests

### Part A

| Test file | Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|---|
| `tests/test_single_source_decisions.py` | `test_only_paths_replaces_a_file_in_place` (edited, not new) | the existing package-source scan, with `allowed = {"deckle/core/paths.py"}` | no `os.replace(` outside `paths.py` | `AssertionError: file replacement outside paths.py -- use write_text_atomic() or atomic_output() instead: ['deckle/core/export.py:622']` |
| `tests/test_cli_output_durability.py` | `test_an_export_already_had_this_guarantee` (existing, unchanged) | `torn_write` fixture | a torn export leaves the previous file byte-identical | passes before and after; it is the regression guard for Part A, not a new assertion |
| `tests/test_export.py` | `test_a_torn_export_leaves_no_scratch_file_beside_the_target` (new) | export to `tmp_path/"out.pdf"` with `_verify_output` monkeypatched to raise `ExportVerificationError` | `sorted(p.name for p in tmp_path.iterdir()) == []` — the scratch file is gone and the target was never created | passes before and after; it pins the guarantee across the implementation swap, which is the whole risk of Part A |

### Part B

| Test file | Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|---|
| `tests/test_export.py` | `test_face_page_index_matches_what_export_writes` (new) | build three one-sheet plans by hand: front+back, front only, **back only** (`Sheet(index=0, front=None, back=<Side>)`); export each with `side=None`; open with pikepdf | for each face that exists, `face_page_index(sheet, side)` equals the page position `_sides(sheet, None)` put it at, and the total page count equals the number of non-`None` entries | `face_page_index` does not exist — `AttributeError` |
| `tests/test_export.py` | `test_face_page_index_is_none_for_a_face_that_does_not_exist` (new) | the front-only and back-only sheets | `face_page_index(front_only, "back") is None` and `face_page_index(back_only, "front") is None` | as above |
| `tests/test_render.py` | `test_a_sheet_with_no_front_renders_its_back` (new) | a plan whose single sheet has `front=None` and a real `back`; `render_sheet(plan, 0, "back", dpi=36)` | the returned `RenderedPage` has non-zero `width` and `height` | passes today (both implementations agree); it is here because it is the *one sheet shape* where the two could disagree, and after Part B there is only one implementation to keep right |

`_sides`' own docstring says the `back=None` branch is unreachable from any
plan Deckle currently produces (`_pad_to_even` sees to it), so these tests
construct `Sheet`s directly rather than going through a strategy. That is
correct and is why the tests are worth having.

### Part C

| Test file | Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|---|
| `tests/test_render_concurrency.py` | `test_the_guard_is_reentrant` (edited, see step 11) | `pdfium_guard()` twice on one thread | the second acquire succeeds | passes; the edit is to stop reaching through a private name |
| `tests/test_render_concurrency.py` | `test_every_pdfium_document_is_opened_under_the_guard` (existing, unchanged) | source scan | every `pdfium.PdfDocument(` is under a guard | must stay green through step 9 — run it after every edit to `render.py` |
| `tests/test_single_source_decisions.py` | `test_importing_a_pdf_does_not_import_the_exporter` (new) | `subprocess.run([sys.executable, "-c", "import deckle.core.loader, sys; print('deckle.core.export' in sys.modules)"])` in a fresh interpreter | prints `False` | prints `True` today: `loader` → `render` → `export` |

The last one is the test that states Part C's point. Run it in a
**subprocess**, not in-process: by the time pytest reaches this file some
other test has almost certainly imported `export` already, and an in-process
`sys.modules` check would assert nothing — the mistake
`tests/test_core_purity.py:44` documents having made and fixed.

## 5. Acceptance

| Check | Command |
|---|---|
| `export` no longer renames by hand | `! grep -n 'os.replace' deckle/core/export.py` |
| …and uses the shared helper | `grep -n 'with atomic_output(out_path)' deckle/core/export.py` |
| The allow-list is down to one entry | `grep -n 'allowed = {"deckle/core/paths.py"}' tests/test_single_source_decisions.py` |
| The face-order helper exists | `grep -n 'def face_page_index' deckle/core/export.py` |
| …and `render` uses it | `grep -n 'export.face_page_index' deckle/core/render.py` |
| `render` no longer re-derives it | `! grep -n 'has_front' deckle/core/render.py` |
| The lock module exists | `test -f deckle/core/pdfium_lock.py` |
| The lock is defined once | `! grep -n '_PDFIUM_LOCK = threading.RLock' deckle/core/render.py` |
| `loader` no longer imports the rasteriser | `! grep -n 'from deckle.core.render import' deckle/core/loader.py` |
| Importing the loader does not import the exporter | `.venv/bin/python -c "import sys, deckle.core.loader; raise SystemExit('deckle.core.export' in sys.modules)"` |
| The new module has a docs page | `test -f docs/api/core.pdfium_lock.rst && grep -n 'core.pdfium_lock' docs/api/core.rst` |
| Docs coverage passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_docs_coverage.py -q --no-header -p no:cacheprovider` |
| `core` is still Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_core_purity.py -q --no-header -p no:cacheprovider` |
| Every pdfium open is still guarded | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_render_concurrency.py -q --no-header -p no:cacheprovider` |
| The touched areas pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_export.py tests/test_render_concurrency.py tests/test_single_source_decisions.py tests/test_cli_output_durability.py tests/test_hardening_limits.py tests/test_loader.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` The preview still shows both faces | `.venv/bin/python -m deckle`, import `tests/fixtures/sample.pdf`, scrub between front and back on sheet 1. Both draw. The one thing an arithmetic test cannot check is that the picture is the face the label says it is. |

`grep -n 'os.replace' deckle/core/export.py` currently matches line 622, and
`grep -n 'has_front' deckle/core/render.py` currently matches lines 233, 254
and 260 — both rows are meaningful in both directions.

## 6. Out of scope

- **M7's dead code**, which lives in three of the same files. In particular
  the `try: ... finally: pass` at the end of `render_sheet`
  (`render.py:272-279`) sits directly under Part B's edit and **must be left
  alone**; and the `try/except ImportError` around `session_log` in
  `app/backend.py:48-63` is in a file Part C touches only by import. Two
  specs editing one function is how a merge loses a fix.
- **B20** — `export(sheets=...)` silently skipping unknown indices and
  writing a 0-page PDF that passes `_verify_output`, and `render_sheet`
  caching an empty PDF for a stale index. Part B stops next to that code and
  does not fix it.
- **B33** — the loader hashing whole files under the pdfium lock. Part C
  moves where the lock is *defined*, not when it is *held*.
- Do not delete `_safe_remove`; the sheet cache still uses it at five sites.
- Do not change `_check_writable`, `_verify_output`, `_sides`, or the sheet
  cache.
- Do not make `pdfium_lock.py` import `pypdfium2`. It is a lock, and a module
  that only holds a lock should be importable by anything.

## 7. decisions.md entry

```
## 2026-09-05 — Three ideas that were written twice
- Symptom: `export.export` hand-rolled `mkstemp`/`os.replace`/cleanup that `paths.atomic_output` already does -- and was carried as a named exception in the very test that enforces "only `paths` renames onto a target". `render.render_sheet` re-derived `export`'s front-then-back face ordering from `has_front`/`has_back`, under a comment naming the code it was duplicating. And `loader` imported `deckle.core.render` for one function, dragging in `export`, pikepdf and the printing module, so the import graph said importing a PDF depends on rendering and exporting one.
- Fix: `export` uses `atomic_output` and comes off the allow-list, which is now one entry. `export.face_page_index(sheet, side)` is the single answer to "which page of an exported sheet is this face", and `render` calls it. `deckle/core/pdfium_lock.py` holds the lock and `pdfium_guard`; `render` re-exports the guard for the backend, and `loader` imports the small module.
- Surfaces: `face_page_index` computes positionally rather than doing `_sides(sheet, None).index(face)` -- `Side` is a frozen dataclass, so a sheet whose two faces are structurally identical would find the front and report page 0 for the back. The interesting shape is a sheet with a back and no front, whose back is at page 0 and not page 1; `_sides` says no plan Deckle currently produces reaches it, so the new tests build the `Sheet` by hand. `_safe_remove` stays for the sheet cache, which is where temp files actually accumulate: measured once at 8,297 files.
- Watch: **The import-graph test has to run in a subprocess.** By the time pytest reaches it, some earlier test has imported `export`, and an in-process `sys.modules` check would assert nothing -- the same weakness `test_core_purity` already records having found and fixed in itself. And `test_every_pdfium_document_is_opened_under_the_guard` reads source text rather than behaviour, so moving the lock meant spelling every call site `with pdfium_guard():`; a rename that the scan does not recognise disables it silently.
- Commit: <fill in>
```

## 8. Traps

- **`_check_writable` must stay outside the `with atomic_output(...)`.**
  `atomic_output` creates the scratch file on entry, so moving the check
  inside would create a file before deciding the destination is unwritable —
  and the docstring promises the opposite.
- **`atomic_output` is a `@contextlib.contextmanager` generator.** It renames
  on normal exit and unlinks on **any** `BaseException`, including
  `KeyboardInterrupt`. That is the same net behaviour as the `finally` it
  replaces; do not add a second `finally`.
- **`tests/test_render_concurrency.py:216` matches source text.** If you
  spell a lock acquisition any way other than `pdfium_guard()` or
  `_PDFIUM_LOCK`, the scan stops recognising it and silently passes. Run that
  file after every `render.py` edit.
- **`import threading` stays in `render.py`** for `_ink_bbox_cache_lock`
  (line 383). Removing it is a `NameError` that only fires on the ink-bbox
  path.
- **A new module under `deckle/` needs a `docs/api/*.rst`** or
  `tests/test_docs_coverage.py` fails, and it must also be in
  `docs/api/core.rst`'s toctree or the Sphinx build warns — and `run.bat
  docs` builds with `-W`.
- **`deckle.core` must not import Qt.** `pdfium_lock.py` imports only
  `threading`; keep it that way.
- **`render.py` imports `deckle.core.export`, and `export` must not import
  `render`.** Part B adds a call from `render` into `export`, which is the
  existing direction. Do not "tidy" it the other way.
- `python -m deckle` launches the GUI; the `[HUMAN]` row is the only place
  this spec wants it.
</content>
