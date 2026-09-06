# B26 — Store a source path that survives leaving the directory

**Roadmap item:** `docs/ROADMAP.md` B26
**Depends on:** —
**Blocks:** —
**Size:** S (part A) / M (part B)
**Decision needed first:** **B (image-cache half) only** — ROADMAP §6:
"should image-folder imports spool the normalised PDF beside the project
instead of the evicting temp cache?" Part A needs no decision and can land
alone.

---

## 1. Context

Two ways a `.deckle` ends up naming a file that is not there, both created
by the loader at the moment a `SourceRef` is built.

### Part A — the path is stored exactly as typed

`load_pdf` writes `path=path` into every `SourceRef`, with whatever the
caller passed. `deckle impose ./book.pdf -o book.deckle` therefore records
`"path": "./book.pdf"`, which resolves against the *current working
directory* of whoever opens it next. Open the project from anywhere else
and every source is missing.

`load_project` resolves the path with `os.path.realpath` for the
containment check (`deckle/core/project_io.py:140`) but hands the raw
string to `os.path.exists` (`:553`) and to `_sha256_file` (`:556`), both of
which are cwd-relative. So the failure is a `SourceMissingError` naming a
path that looks fine.

Verified on the tree at `08e7f49`:

```bash
cd /tmp/work
python -m deckle.cli dummy -o d.pdf --pages 4
python -m deckle.cli impose ./d.pdf -o rel.deckle
python -c "import json; print(json.load(open('rel.deckle'))['pages'][0]['path'])"
cd /
python -m deckle.cli info /tmp/work/rel.deckle
```

Current output:

```
./d.pdf
error: cannot open /tmp/work/rel.deckle: a source file is missing -- ./d.pdf.
Restore it, or re-import from its new location.
```

The file is right there beside the project. Nothing has moved.

This bites the desktop app too — `AppState`'s autosave and Save project both
go through `save_project`, and a project saved while the process's cwd was
one thing and reopened when it was another has the same problem. It is
worst on the CLI, where `./` and bare relative names are the normal way to
type a path.

### Part B — an image import stores the temp cache file

`load_image_dir` merges a folder of scans into one normalised PDF under
`<tempdir>/deckle_import_cache/`, and points every `SourceRef` at *that*
file. The folder of images the user actually chose is never recorded.

Two consequences:

1. `evict_lru_files` prunes that directory to 2 GB on every subsequent
   import (`deckle/core/loader.py:591`), by least-recently-used. A project
   saved months ago, whose cache entry has not been read since, is deleted
   out from under it — by Deckle, during an unrelated import.
2. The OS empties the temp directory anyway, on a reboot or a cleanup pass.

Either way the project opens to `SourceMissingError` naming a path like
`/tmp/deckle_import_cache/tmpq4k1z0.pdf`, which means nothing to the user
and cannot be relocated to, because the file it named never existed
anywhere they chose.

Part B is what ROADMAP §6 leaves open, and §3B below writes both branches.

## 2. Current code

### Part A

`deckle/core/loader.py:360-378`, inside `_read_pdf_pages`:

```python
            for index in range(len(doc)):
                page = doc[index]
                try:
                    width_pt, height_pt = page.get_size()
                finally:
                    # Children before the parent, or their finalizers assert
                    # against a closed document -- see render.rasterize_page.
                    page.close()
                ref = SourceRef(
                    path=path,
                    page_index=index,
                    sha256=sha256,
                    width_pt=float(width_pt),
                    height_pt=float(height_pt),
                )
                pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))
            return pages
```

`path` is `_read_pdf_pages`'s parameter, passed straight through from
`load_pdf(path)` (`deckle/core/loader.py:307`, `:324`), which passes
whatever the caller gave.

The persistence side, `deckle/core/project_io.py:188-198`:

```python
def _page_to_dict(page: SourcePage) -> dict[str, Any]:
    ref = page.ref
    return {
        "path": ref.path,
        ...
    }
```

and the readers, `deckle/core/project_io.py:552-560`:

```python
        if check_sources:
            if not os.path.exists(ref.path):
                raise SourceMissingError(ref.path)
            try:
                current_hash = _sha256_file(ref.path)
            except OSError:
                raise SourceMissingError(ref.path)
            if current_hash != ref.sha256:
                raise SourceChangedWarning(ref.path)
```

The containment check that *does* resolve,
`deckle/core/project_io.py:134-150`:

```python
def _path_within_roots(candidate: str, roots: tuple[str, ...]) -> bool:
    """True if ``candidate`` resolves inside any of ``roots``.

    Resolves both sides with ``os.path.realpath`` so ``..`` traversal and
    symlinks can't be used to escape the check.
    """
    real_candidate = os.path.realpath(candidate)
```

`BLANK_SOURCE_PATH` — the sentinel for an inserted blank — must **not** be
absolutised. **It is the empty string** (`deckle/core/models.py:62`:
`BLANK_SOURCE_PATH = ""`), and `os.path.abspath("")` returns the current
working directory, so putting a blank through the new call would turn the
sentinel into a real directory path and break every project holding one.
Blanks are built in `deckle/app/state.py:77`, outside the loader, so they
never reach it — but the margin is one function call wide. The reader that
depends on the sentinel, `deckle/core/project_io.py:530-538`:

```python
    for page in pages:
        ref = page.ref
        if is_blank_page(page):
            # A blank the user inserted references no file. ...
            continue
```

and the check itself, `deckle/core/models.py:88`:

```python
    return page.ref.path == BLANK_SOURCE_PATH
```

### Part B

`deckle/core/loader.py:37-43`:

```python
_CACHE_DIR_NAME = "deckle_import_cache"
...
_CACHE_MAX_BYTES = 2 * 1024 ** 3
```

`deckle/core/loader.py:581-620`, the tail of `load_image_dir`:

```python
    merged = pikepdf.Pdf.new()
    opened = []
    try:
        for pdf_bytes in per_image_pdfs:
            single = pikepdf.open(io.BytesIO(pdf_bytes))
            opened.append(single)
            merged.pages.extend(single.pages)

        cache_dir = os.path.join(tempfile.gettempdir(), _CACHE_DIR_NAME)
        os.makedirs(cache_dir, exist_ok=True)
        _evict_lru_cache_entries(cache_dir, max_bytes)
        fd, out_path = tempfile.mkstemp(suffix=".pdf", dir=cache_dir)
        os.close(fd)
        merged.save(out_path)
    finally:
        for single in opened:
            single.close()
        merged.close()

    sha256 = _sha256_file(out_path)
    pages: list[SourcePage] = []
    with pikepdf.open(out_path) as result:
        for index, page in enumerate(result.pages):
            box = page.mediabox
            width_pt = float(box[2]) - float(box[0])
            height_pt = float(box[3]) - float(box[1])
            # img2pdf encodes EXIF rotation as a page /Rotate flag rather
            # than baking it into the media box -- swap dimensions here so
            # SourceRef reports the actual upright (displayed) size, which
            # is what Imposer needs.
            rotate = int(page.get("/Rotate", 0)) % 360
            if rotate in (90, 270):
                width_pt, height_pt = height_pt, width_pt
            ref = SourceRef(
                path=out_path,
                page_index=index,
                sha256=sha256,
                width_pt=width_pt,
                height_pt=height_pt,
            )
            pages.append(SourcePage(ref=ref, rotate_deg=0, skipped=False))

    return ImportedPages(pages, warnings)
```

`load_image_dir`'s signature and docstring, `deckle/core/loader.py:496-521`:

```python
def load_image_dir(
    path: str, cache_max_bytes: int | None = None
) -> list[SourcePage]:
    """Import a directory of images as one normalized PDF, metadata-only.

    Writes a single normalized PDF (one page per image, in natural sort
    order) into a cache directory under the OS temp dir, and returns
    ``SourcePage``\\ s whose ``SourceRef``\\ s point at that cached PDF.
    ...
```

The eviction it is subject to, `deckle/core/paths.py:99-171`
(`evict_lru_files`), reached through `loader._evict_lru_cache_entries`
(`deckle/core/loader.py:55-63`).

### Every call site

`load_pdf`: `deckle/core/loader.py:307` (definition); `deckle/cli.py:30`
(import), `:475` (`_load_source`); `deckle/app/views/import_view.py`;
`tests/test_loader.py`, `tests/test_project_cli.py:203`, `:255`, `:260`,
and ~20 other test files. `grep -rln "load_pdf" --include="*.py" .` is the
full list.

`load_image_dir`: `deckle/core/loader.py:496` (definition);
`deckle/cli.py:30` (import), `:474` (`_load_source`);
`deckle/app/views/import_view.py`; `tests/test_loader.py`,
`tests/test_hardening_io.py`, `tests/test_cache_budgets.py`.

`SourceRef`: `deckle/core/models.py` (definition), constructed at
`deckle/core/loader.py:368` and `:614`, and at
`deckle/core/project_io.py:272` (`_page_from_dict`, reading back what was
stored). Nothing else builds one in `deckle/`.

`_CACHE_DIR_NAME` / `_CACHE_MAX_BYTES`: `deckle/core/loader.py:37`, `:43`,
`:507`, `:524`, `:589`; `tests/test_cache_budgets.py`.

`_evict_lru_cache_entries`: `deckle/core/loader.py:55` (definition), `:591`
(only caller).

### Existing tests over this code

- `tests/test_loader.py` — the whole file, including `_make_pdf`
- `tests/test_cache_budgets.py` — eviction of the import cache
- `tests/test_project_cli.py::test_a_project_whose_source_moved_says_which_file`
  (line 127) — copies the fixture into `tmp_path` and passes an **absolute**
  path, so it cannot see part A
- `tests/test_project_cli.py::test_each_page_records_a_content_hash`
- `tests/test_project_io.py` — round trips, all with absolute paths
- `tests/test_hardening_io.py::test_load_image_dir_*`

**No test imposes with a relative path and then opens the project from
another directory.** That is part A's gap.

## 3A. Change — absolute source paths

### The rule

`SourceRef.path` is always absolute. It is normalised at the one place a
`SourceRef` is built from a filesystem path, using `os.path.abspath`.

**`os.path.abspath`, not `os.path.realpath`.** `abspath` joins against the
cwd and normalises `.` and `..` lexically; `realpath` additionally resolves
every symlink. Resolving symlinks here would be wrong: a user whose scans
live under a symlinked `~/Books` would find their project recording
`/mnt/volume-3/…`, which is a different name for the same file until the
mount point changes, and it is not the name they chose. The containment
check at `deckle/core/project_io.py:140` still uses `realpath` on both
sides, where resolving is exactly right because the question is "is this
the same file".

### Steps

1. **`deckle/core/loader.py`, `_read_pdf_pages`** — absolutise once, above
   the loop, so every page in the document shares one string:

   ```python
   def _read_pdf_pages(path: str) -> list[SourcePage]:
       """Open ``path`` and measure every page, holding the pdfium guard.
       ...
       """
       # The path a `SourceRef` records is written into `.deckle` files and
       # resolved by whoever opens one next, from whatever directory they
       # happen to be in. Stored as typed, `deckle impose ./book.pdf`
       # recorded `./book.pdf` and the project could not be opened from
       # anywhere but the directory it was made in -- reported as
       # "a source file is missing", naming a file that had not moved.
       #
       # `abspath` rather than `realpath`: joining against the cwd is the
       # fix, and resolving symlinks as well would record a name the user
       # did not choose. `project_io._path_within_roots` still uses
       # `realpath` on both sides, where "is this the same file" is the
       # actual question.
       stored_path = os.path.abspath(path)
       with pdfium_guard():
           ...
   ```

   and inside the loop, `path=stored_path` in place of `path=path`
   (`deckle/core/loader.py:369`).

   **Everything else keeps using `path`**: the pdfium open at `:337`, the
   error messages built by `_classify_pdf_open_failure`, the
   `EmptyPdfError` message at `:353-357`, and `_sha256_file(path)` at
   `:359`. Those name what the user typed, which is what they should say.
   Confirm nothing else in the function was changed with
   `git diff deckle/core/loader.py`.

2. **`load_pdf`'s docstring** (`deckle/core/loader.py:307-323`) — add to the
   `:returns:` line: "one `SourcePage` per PDF page, each carrying the
   **absolute** path, so a project saved from one directory opens from
   another."

3. **`deckle/core/models.py`, `SourceRef.path`** — its docstring is the
   place the invariant belongs. Read the three neighbouring field
   docstrings first, then state: the path is absolute for a real source,
   and `BLANK_SOURCE_PATH` for an inserted blank. If `SourceRef` has no
   per-field docstrings, put it on the class docstring's `:ivar path:`
   line.

4. **Part B's `SourceRef` at `deckle/core/loader.py:614-615`** —
   `out_path` comes from `tempfile.mkstemp`, which already returns an
   absolute path. No change needed for part A. Leave it to §3B.

5. **Blanks are untouched, and this is the sharp edge.**
   `deckle/app/state.py:77` builds a `SourceRef` with
   `path=BLANK_SOURCE_PATH` directly and never goes through the loader, so
   nothing absolutises it. That matters more than it sounds:
   `BLANK_SOURCE_PATH` is `""` (`deckle/core/models.py:62`) and
   `os.path.abspath("")` is the current working directory, so a single
   misplaced call would convert every blank into a reference to whatever
   folder Deckle happened to be launched from — and `is_blank_page`
   (`deckle/core/models.py:88`) tests for equality with `""`, so
   `load_project` would stop skipping the existence check and every project
   with a blank would refuse to open. Put the `abspath` call in
   `_read_pdf_pages` and nowhere else.

6. **No migration for existing projects.** A `.deckle` already holding
   `./book.pdf` keeps holding it, and keeps working from the directory it
   was made in. Rewriting stored paths on load would mean `load_project`
   silently editing the document, and rewriting them on save would change a
   file the user did not ask to change. The next `impose` or Save writes an
   absolute path; that is the whole migration. Say so in `docs/decisions.md`.

## 3B. Change — the image-folder cache

**This half needs the owner's decision** (ROADMAP §6). Both branches are
written out below; implement exactly one. Neither branch changes part A.

### Branch B1 — spool the normalised PDF beside the project

**The idea:** `load_image_dir` writes its merged PDF next to where the
project will be saved, not into the evicting temp cache, so the file the
project references is a file the user owns.

**The problem it has to solve first:** at import time there is no project
path. `deckle impose <imagedir> -o book.deckle` knows the output; the
desktop app's Import does not — the user may never save. So the spool
location has to be a parameter with a defined fallback.

Steps:

1. **`deckle/core/loader.py`** — add a keyword-only parameter:

   ```python
   def load_image_dir(
       path: str,
       cache_max_bytes: int | None = None,
       *,
       spool_dir: str | None = None,
   ) -> list[SourcePage]:
   ```

   `spool_dir` is where the normalised PDF is written. `None` keeps the
   current temp-cache behaviour, so every existing caller and every
   existing test is unaffected until it is passed.

2. **The filename must be stable and identifiable**, not `mkstemp`'s random
   name: `<basename of the image folder>.deckle-pages.pdf`, e.g.
   `scans.deckle-pages.pdf`. A user who finds it beside their project can
   tell what it is and that deleting it breaks the project. Collisions with
   an existing file of that name are resolved by overwriting **only if**
   its sha256 matches what we were about to write, and otherwise by
   appending `-2`, `-3`. Write it through
   `deckle.core.paths.atomic_output`, which is the house answer for "an
   encoder that wants a filename of its own" and gives the same
   failure-leaves-the-old-file guarantee every other output has.

3. **`_evict_lru_cache_entries` is not called** when `spool_dir` is given.
   The whole point is that this file is not a cache.

4. **`deckle/cli.py:464-475`, `_load_source`** — pass the output's
   directory:

   ```python
   def _load_source(path: str, spool_dir: str | None = None) -> list[SourcePage]:
       if os.path.isdir(path):
           return load_image_dir(path, spool_dir=spool_dir)
       return load_pdf(path)
   ```

   and thread it from `_load_source_or_report` /
   `_resolve_input`, which have `args.output` in hand for `impose` and
   `export`. `info` and `schedule` write nothing, so they pass `None` and
   keep the temp cache — which is right, because a command that produces no
   project has no project to spool beside.

5. **The desktop app** (`deckle/app/views/import_view.py`) passes `None`
   until the project has a path, and re-spools on Save As. That is the
   expensive part of this branch and the reason it is sized **M**: it means
   `save_project` (or something above it) rewriting `SourceRef.path` for
   every page after moving a file, which is a mutation of the document
   during a save.

6. **Tests:** import an image folder with `spool_dir`, assert the PDF
   lands there with the expected name, assert `SourceRef.path` points at
   it, assert `evict_lru_files` on the temp cache does not delete it,
   assert a second import with the same folder name and different content
   does not silently overwrite.

**Cost:** a real file appears beside the user's project that they did not
create and must not delete. **Benefit:** the project is self-contained and
portable, which is what "references, not content" promises.

### Branch B2 — exempt referenced files from eviction

**The idea:** the temp cache stays where it is, but a file a saved project
references is never evicted.

Steps:

1. **`deckle/core/paths.py`, `evict_lru_files`** — add a keyword-only
   `protected: frozenset[str] = frozenset()` of absolute paths to skip.
   Document it as "files something else still names"; the function stays
   ignorant of what a project is.

2. **`deckle/core/loader.py`, `_evict_lru_cache_entries`** — take and
   forward the same set.

3. **Who knows the set:** the recent-projects list
   (`deckle/core/recent.py`) is the only durable record of which projects
   exist. Reading every recent `.deckle` on every import to collect its
   source paths is I/O on a hot path, so instead keep a sidecar
   `<cachedir>/referenced.json` — a list of absolute cache paths — that
   `save_project` appends to and that `_evict_lru_cache_entries` reads.
   That makes `project_io` write into the loader's cache directory, which
   is a new coupling between two modules that currently share nothing.

4. **Nothing protects against the OS.** A reboot on Linux still empties
   `/tmp`. This branch therefore fixes the eviction half of the bug and
   **not** the temp-directory half. It must be paired with moving the
   import cache under `paths.data_dir("import_cache")` — the same move
   **B27** makes for print-session state, for the same reason — or it does
   not survive a reboot.

5. **Tests:** a project referencing a cache file; an import that would
   evict it; assert it survives. A cache file nothing references; assert it
   is still evicted. `tests/test_cache_budgets.py` is where these belong.

**Cost:** an unbounded set of pinned files, a sidecar index that can go
stale (a deleted project leaves its entry behind forever), and a new
dependency from `project_io` to the loader's cache. **Benefit:** nothing
appears in the user's folders.

### The recommendation, for the owner

**B1.** The roadmap's own framing — "which `evict_lru_files` can later
delete out from under a saved project" — describes a cache being asked to
be storage. B2 makes it storage with a pinning index; B1 stops pretending.
B1 also solves the reboot case for free, and matches what the format
already promises: a `.deckle` holds references to files the user has, and
under B2 it holds a reference to a file only Deckle knows about.

But B1 is the one that puts a file the user did not create into their
folder, and that is a product decision, not a technical one. It is why this
is here rather than decided.

## 4. Tests

### Part A

**`tests/test_project_cli.py::test_a_project_imposed_with_a_relative_path_opens_from_elsewhere`**

- **Setup:** copy `FIXTURE` into `tmp_path` as `book.pdf`. Run
  `_cli("impose", "./book.pdf", "-o", "book.deckle", cwd=str(tmp_path))` —
  the helper already takes `cwd`. Then run
  `_cli("info", str(tmp_path / "book.deckle"), cwd=REPO)`.
- **Assertion in words:** the second command exits 0 and prints
  `page count: 2`. The project opens from a directory that is not the one
  it was made in.
- **Expected failure on the unfixed tree:** exit 1 with
  `error: cannot open <path>: a source file is missing -- ./book.pdf.`

**`tests/test_project_cli.py::test_a_saved_project_records_an_absolute_source_path`**

- **Setup:** as above; read the `.deckle`'s JSON.
- **Assertion in words:** `os.path.isabs(data["pages"][0]["path"])`, and the
  path resolves to the same file as `tmp_path / "book.pdf"` (compare with
  `os.path.samefile`). The structural half — the behavioural test above
  could also be satisfied by a loader that resolved relative paths at read
  time, which would be the wrong fix.
- **Unfixed tree:** `assert os.path.isabs('./book.pdf')` fails.

**`tests/test_loader.py::test_load_pdf_records_an_absolute_path`**

- **Setup:** `_make_pdf` into `tmp_path`; `monkeypatch.chdir(tmp_path)`;
  `load_pdf("blank.pdf")` with the bare name.
- **Assertion in words:** every returned page's `ref.path` is absolute and
  `os.path.samefile(ref.path, tmp_path / "blank.pdf")`.
- **Unfixed tree:** `assert os.path.isabs('blank.pdf')` fails.

**`tests/test_loader.py::test_load_pdf_does_not_resolve_symlinks`**

- **Setup:** `_make_pdf` at `tmp_path/"real"/"b.pdf"`; symlink
  `tmp_path/"link"` → `tmp_path/"real"`; `load_pdf(str(tmp_path / "link" / "b.pdf"))`.
- **Assertion in words:** `ref.path` contains `link`, not `real`. Pins
  `abspath` over `realpath` — a user's symlinked scan folder must keep the
  name they used.
- **Unfixed tree:** passes (nothing resolves anything). Keep it; it is the
  guard against "fixing" this with `realpath`.
- **Skip on Windows** where creating a symlink needs a privilege:
  `@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a
  privilege on Windows")`. `tests/test_loader.py` has no `skipif` today
  (`grep -n "skipif" tests/test_loader.py` returns nothing), so this
  introduces the idiom there; `tests/test_hardening_io.py` has examples of
  the house wording.

**`tests/test_loader.py::test_the_error_messages_still_name_what_was_typed`**

- **Setup:** `monkeypatch.chdir(tmp_path)`; `load_pdf("nope.pdf")`.
- **Assertion in words:** the `MissingSourceError`'s message contains
  `nope.pdf` and does **not** contain the absolute prefix. The user gets
  told about the thing they typed.
- **Unfixed tree:** passes. Keep it; step 1 explicitly leaves the message
  paths alone and this is what says so.

**`tests/test_project_cli.py::test_a_blank_page_keeps_its_sentinel_path`**

- **Setup:** the existing
  `test_a_project_containing_a_blank_can_be_reopened` (line 191) already
  builds one; extend it or add a sibling that asserts
  `reopened.pages[1].ref.path == BLANK_SOURCE_PATH` after a save/load
  round trip.
- **Assertion in words:** the blank's sentinel is untouched — it is not a
  filesystem path and must not become one.
- **Unfixed tree:** passes. This is the regression guard on step 5.

### Part B

Write the tests named in §3B under whichever branch is chosen. Both
branches need one shared test:

**`tests/test_cache_budgets.py::test_a_project_can_still_be_opened_after_an_unrelated_import`**

- **Setup:** import an image folder; save a project from the result;
  perform a second import large enough to force eviction (the
  `cache_max_bytes` parameter exists for exactly this —
  `deckle/core/loader.py:506-510`); reopen the project.
- **Assertion in words:** the project opens.
- **Unfixed tree:** `SourceMissingError` naming the evicted temp file. This
  is the test that states the bug regardless of which branch fixes it, and
  it should be written first.

## 5. Acceptance

Part A only. Part B's rows depend on the branch chosen.

| Check | Command |
|---|---|
| The loader absolutises | `grep -n "os.path.abspath" deckle/core/loader.py` |
| It does not resolve symlinks | `! grep -n "realpath" deckle/core/loader.py` |
| A relative import records an absolute path | `cd "$(mktemp -d)" && cp "$OLDPWD/tests/fixtures/sample.pdf" b.pdf && "$OLDPWD/.venv/bin/python" -m deckle.cli impose ./b.pdf -o r.deckle >/dev/null && "$OLDPWD/.venv/bin/python" -c "import json,os,sys; p=json.load(open('r.deckle'))['pages'][0]['path']; sys.exit(0 if os.path.isabs(p) else 1)"` |
| The project opens from elsewhere | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py -k "relative_path_opens_from_elsewhere or absolute_source_path"` |
| Loader invariants | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_loader.py` |
| Project IO unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_io.py tests/test_project_cli.py tests/test_atomic_writes.py tests/test_project_file_shape.py` |
| Import and hardening paths unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_io.py tests/test_cache_budgets.py tests/test_import_view.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree: rows 1 and 2 both return nothing — the loader
uses neither `abspath` nor `realpath` today, so row 2 passes now and must
keep passing. Row 3 exits 1.

Row 3 uses `$OLDPWD`, so run it from the repository root; it changes
directory deliberately, which is the whole point of the check.

## 6. Out of scope

- **Existing `.deckle` files.** No migration; see §3A step 6.
- **`SourceMissingError.relocate`.** It already exists
  (`deckle/core/project_io.py:80-89`) and is the right affordance for a
  file that genuinely moved. This spec reduces how often it is needed for a
  file that did not.
- **B33** — the loader computing sha256 while holding the pdfium lock, and
  holding every image's PDF bytes in memory before merging. Same file, same
  functions, different defect. If part B (branch B1) is being done, note
  that B33's memory half touches the same lines and the two are worth
  sequencing.
- **B34** — `write_text_atomic` tightening a shared file's mode. Not this.
- **N1** — autosave for never-saved projects. Part B branch B1's "there is
  no project path at import time" problem is adjacent to it, but N1 is
  about `.deckle` autosaves and this is about a normalised PDF.
- **The desktop app's Save As re-spooling** under branch B1 — sized into
  that branch and not attempted under B2 or under part A alone.
- **`deckle/core/project_io.py`.** Part A changes nothing there. The
  loader is the one place a `SourceRef` is built from a filesystem path,
  and normalising at the boundary is why nothing downstream needs to know.

## 7. decisions.md entry

For part A alone:

```
## 2026-09-05 — A project remembered the path you typed, not the file
- Symptom: `load_pdf` wrote the caller's string into every `SourceRef`, so `deckle impose ./book.pdf -o book.deckle` recorded `./book.pdf` and the project could be opened only from the directory it was made in. From anywhere else it failed with "a source file is missing -- ./book.pdf", naming a file sitting right beside the project. `load_project` resolves the path with `realpath` for the containment check and then hands the raw string to `os.path.exists`, so the check that could have caught it was the only one that resolved.
- Fix: `os.path.abspath` at the one place a `SourceRef` is built from a filesystem path. `abspath` and not `realpath`: joining against the cwd is what was missing, and resolving symlinks as well would record a name the user never chose -- a scan folder reached through `~/Books` would come back as `/mnt/volume-3/...`. Error messages keep naming what was typed.
- Surfaces: No migration. An existing `.deckle` keeps its relative path and keeps working where it was made; the next save writes an absolute one. Rewriting stored paths on load would mean the reader editing the document.
- Watch: The test that would have caught this used an absolute path, because tests are written with `tmp_path`, and `tmp_path` is absolute. A whole class of relative-path bugs is invisible to a suite that never types a relative path.
- Commit: <fill in>
```

## 8. Traps

- **No file collision with the rest of this slice.** B26 touches
  `deckle/core/loader.py` and nothing else; B3, B4, B27 and B28 are in
  `deckle/core/print_session.py` and B21-B25 in `deckle/cli.py`. It can be
  done at any point, in parallel.
- **`abspath` is cwd-dependent, which is the point and also the hazard.**
  It must be called at import time, in the process that has the user's cwd
  — not lazily inside `project_io`, where the cwd at *load* time is a
  different and irrelevant directory.
- **`os.path.abspath` does not touch the filesystem.** It cannot fail and
  cannot raise for a path that does not exist, so it is safe above the
  `pdfium_guard()` block.
- **Do not absolutise the error-message paths.** `_require_readable_file`,
  `_classify_pdf_open_failure`, `EmptyPdfError` and the rest name what the
  user typed, which is what they should say. One test in §4 pins that.
- **`BLANK_SOURCE_PATH` is the empty string, and `os.path.abspath("")` is
  the cwd.** Blanks are built at `deckle/app/state.py:77`, outside the
  loader, and `is_blank_page` (`deckle/core/models.py:88`) tests for
  equality with `""` — which is what `load_project` uses at
  `deckle/core/project_io.py:532` to skip the containment and existence
  checks. One `abspath` in the wrong place turns every blank into a
  reference to the launch directory and every project holding one becomes
  unopenable.
- **`os.path.samefile` raises if either path is missing.** Tests that use
  it must create the file first.
- **Windows symlinks need a privilege.** The symlink test must skip there.
- **Part B changes the shape of `load_image_dir`'s return contract** — the
  path in a `SourceRef` becomes a file the user can see.
  `tests/test_loader.py:187` monkeypatches `tempfile.gettempdir` to point
  the import cache at a fake root, and `tests/test_cache_budgets.py:126`
  and `:146` do the same for the *export* cache. Read all three before
  starting branch B1: the first one moves, the other two must not.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
- `python -m deckle` launches the GUI and blocks; use `python -m deckle.cli`.
