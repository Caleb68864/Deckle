# B33 — Stop the loader freezing every preview, and stop it holding the whole import in memory

**Roadmap item:** `docs/ROADMAP.md` B33
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Two independent costs in `deckle/core/loader.py`, both paid on the one
action a user takes most often at the start of a job: importing.

### 1a. The whole-file SHA-256 is computed while holding the pdfium lock

`_read_pdf_pages` opens the document inside `with pdfium_guard():` — which
is correct, and the guard's docstring explains at length why it has to span
the document's whole life. Inside that region it also calls
`_sha256_file(path)`, which reads the entire file in 1 MB chunks with the
standard library.

`pdfium_guard()` returns the **process-wide** `_PDFIUM_LOCK`
(`deckle/core/render.py:68`), the same lock every preview render, every
thumbnail window and every ink-bbox scan takes. So for the whole time a
400 MB scanned PDF is being hashed — seconds, not milliseconds — no preview
can draw. The user sees the window stop repainting during an import, which
is the symptom the lock exists to *avoid* trading for (its docstring:
"The cost is that renders no longer overlap"). Hashing is not a pdfium call
and gains nothing from the lock.

**Does the guard protect the file read as well as the pdfium calls?** No.
Read `pdfium_guard`'s docstring (`deckle/core/render.py:101-129`): the
entire stated purpose is that "pdfium is a single global library and its
state is process-wide", so "every document open, page render and close has
to be inside this". `_sha256_file` uses builtin `open` and `hashlib` and
touches nothing pdfium owns. Nor is the lock protecting the *file* against
concurrent modification: it is an in-process advisory lock over a library's
global state, `_sha256_file` opens its own handle regardless of the pdfium
one, and nothing outside this process is aware of it.

### 1b. Every image's PDF bytes are materialised before any of them is merged

`load_image_dir` builds `per_image_pdfs`, a list holding the complete PDF
bytes of *every* image, and only then opens and merges them. For the
motivating case in the roadmap — 500 scans — that is roughly 2.5 GB of
Python `bytes` held at once, plus a second copy per image inside the
`io.BytesIO` each `pikepdf.open` is handed, plus the assembled document.
A folder of scans is the input the image path exists for.

The user action: point Deckle at a folder of 500 scans. The result is an
import that allocates several gigabytes and, on a machine that cannot spare
it, an `MemoryError` or the OS killing the process — after the work of
converting every image is already done.

`deckle/core/export.py` already solved exactly this shape for the export
path, with `_BATCH_SHEETS` and `_flush_batch`, and recorded why. This item
brings the loader up to it.

## 2. Current code

### 1a — `deckle/core/loader.py:327-378`

```python
def _read_pdf_pages(path: str) -> list[SourcePage]:
    """Open ``path`` and measure every page, holding the pdfium guard.

    pdfium's state is process-global, so an import racing a preview render
    faults natively rather than raising. The guard spans the document's
    whole life, not just the open, because a render on another thread is
    just as fatal while this one is measuring pages.
    """
    with pdfium_guard():
        try:
            doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            raise _classify_pdf_open_failure(path, exc) from exc

        # The document is closed before returning. Import is metadata-only --
        # nothing downstream holds a pdfium handle, and every later read reopens
        # the file. Leaving it open kept the source LOCKED on Windows for the
        # life of the app: import a PDF and you could not move, rename or delete
        # it until Deckle exited, with no indication of what held it.
        try:
            if len(doc) == 0:
                # Reachable independently of the open failure above: some
                # zero-page files open cleanly and only reveal themselves on
                # len().
                raise EmptyPdfError(
                    path,
                    f"cannot use {path}: the PDF has no pages, so there is "
                    "nothing to impose. Check that the export that produced it "
                    "actually wrote its pages.",
                )

            sha256 = _sha256_file(path)
            pages: list[SourcePage] = []
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
        finally:
            doc.close()
```

The hash is at `:358`, twenty-three lines inside `with pdfium_guard():` at
`:335`.

`_sha256_file` (`deckle/core/loader.py:176-181`):

```python
def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

**Every caller of `_sha256_file`:** `deckle/core/loader.py:358` (under the
guard, this defect) and `deckle/core/loader.py:600` (in `load_image_dir`,
already outside any guard). Nothing else in the tree.

**Every caller of `_read_pdf_pages`:** `deckle/core/loader.py:324`, from
`load_pdf`. Nothing else.

**Everything that takes the same lock**, from
`grep -rn "pdfium_guard\|_PDFIUM_LOCK" --include='*.py' deckle/`:
`deckle/core/render.py:163` (`rasterize_page`), `:262` (`render_sheet`),
`:322` (`thumbnails`), `:392` (`_rasterize_for_bbox`),
`deckle/app/backend.py:215` (printing, on the GUI thread), and
`deckle/core/loader.py:335` (this one).

### 1b — `deckle/core/loader.py:576-600`

```python
    per_image_pdfs = [
        _single_image_pdf_or_raise(img_path, dpi)
        for img_path, dpi in zip(image_paths, dpis)
    ]

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
```

`per_image_pdfs` is complete before the first `pikepdf.open`, and every
`single` stays open until after `merged.save(out_path)` — which is
necessary, because pikepdf reads a page's content lazily from the document
it was opened over.

The pattern this should follow, `deckle/core/export.py:881-902`:

```python
def _flush_batch(
    out: pikepdf.Pdf, source_cache: dict[str, pikepdf.Pdf], tmp_path: str
) -> None:
    """Save the in-progress output and close every open source handle.
    ...
    Clearing ``source_cache`` is the load-bearing half: ...
    """
    out.remove_unreferenced_resources()
    out.save(tmp_path)
    out.close()
    log_event("export_batch_flushed", open_sources=len(source_cache))
    for src in source_cache.values():
        src.close()
    source_cache.clear()
```

**Existing tests that touch this code:**

- `tests/test_loader.py:55-68` `test_load_pdf_returns_source_pages`.
- `tests/test_loader.py:71-90` `test_load_pdf_large_document_is_fast_and_does_not_render`
  — 300 pages, asserts `elapsed < 2.0` and that nothing rendered.
- `tests/test_loader.py:93-100` `test_load_pdf_encrypted_raises_encrypted_pdf_error`.
- `tests/test_loader.py:106-163` — the four `load_image_dir` behaviours:
  natural ordering, EXIF orientation, mixed DPI, missing DPI, and that a
  cache PDF is written under the temp dir.
- `tests/test_loader.py:169-...` `test_load_image_dir_evicts_lru_cache_entries_when_over_cap`
  — redirects `tempfile.gettempdir` to an isolated directory (`:187`), which
  is the pattern the new tests reuse.
- `tests/test_project_cli.py:245` `test_importing_a_pdf_does_not_lock_the_file`.
- `tests/test_render_concurrency.py:180-227` — a source scan asserting every
  `pdfium.PdfDocument(` call sits lexically under a guard. See Traps.
- `tests/test_hardening_io.py:460-495` — the skipped-file and mixed-DPI
  warnings reaching the CLI.

## 3. Change

### 1a — split the measurement from the hash

1. **`deckle/core/loader.py`**, add `_read_pdf_page_sizes` immediately above
   `_read_pdf_pages`. It is the current body with the hash and the
   `SourcePage` construction removed, and it keeps the guard exactly where
   it is:

   ```python
   def _read_pdf_page_sizes(path: str) -> list[tuple[float, float]]:
       """Every page's ``(width, height)`` in points, holding the pdfium guard.

       pdfium's state is process-global, so an import racing a preview render
       faults natively rather than raising. The guard spans the document's
       whole life, not just the open, because a render on another thread is
       just as fatal while this one is measuring pages.

       Split from :func:`_read_pdf_pages` so the guard covers the pdfium work
       and nothing else. It used to span the file hash as well, which is a
       standard-library read of the whole file -- seconds on a large scan,
       with every preview, thumbnail and print in the process blocked behind
       it for the duration.
       """
       with pdfium_guard():
           try:
               doc = pdfium.PdfDocument(path)
           except pdfium.PdfiumError as exc:
               raise _classify_pdf_open_failure(path, exc) from exc

           # The document is closed before returning. Import is metadata-only --
           # nothing downstream holds a pdfium handle, and every later read reopens
           # the file. Leaving it open kept the source LOCKED on Windows for the
           # life of the app: import a PDF and you could not move, rename or delete
           # it until Deckle exited, with no indication of what held it.
           try:
               if len(doc) == 0:
                   # Reachable independently of the open failure above: some
                   # zero-page files open cleanly and only reveal themselves on
                   # len().
                   raise EmptyPdfError(
                       path,
                       f"cannot use {path}: the PDF has no pages, so there is "
                       "nothing to impose. Check that the export that produced it "
                       "actually wrote its pages.",
                   )

               sizes: list[tuple[float, float]] = []
               for index in range(len(doc)):
                   page = doc[index]
                   try:
                       width_pt, height_pt = page.get_size()
                   finally:
                       # Children before the parent, or their finalizers assert
                       # against a closed document -- see render.rasterize_page.
                       page.close()
                   sizes.append((float(width_pt), float(height_pt)))
               return sizes
           finally:
               doc.close()
   ```

2. **`deckle/core/loader.py`**, replace the whole of `_read_pdf_pages`
   (`:327-378`) with:

   ```python
   def _read_pdf_pages(path: str) -> list[SourcePage]:
       """Measure every page under the pdfium guard, then hash the file outside it.

       The order is the point. Hashing is a standard-library read of the
       whole file -- 1 MB at a time, seconds on a scanned book -- and it
       used to run inside the guard, which is the process-wide pdfium lock
       every preview render, thumbnail window and print rasterisation takes.
       Importing a large PDF therefore froze the whole window for the length
       of the hash, which is the cost the lock was accepted to avoid, not to
       cause. The guard exists for pdfium's global state; ``hashlib`` has no
       part in it.

       The two are no longer taken under one open handle, so a file rewritten
       between the measurement and the hash yields the new content's hash
       with the old geometry. That window already existed -- the hash always
       opened the file separately from pdfium -- and the mismatch is what
       ``project_io``'s source check is for.
       """
       sizes = _read_pdf_page_sizes(path)
       sha256 = _sha256_file(path)
       return [
           SourcePage(
               ref=SourceRef(
                   path=path,
                   page_index=index,
                   sha256=sha256,
                   width_pt=width_pt,
                   height_pt=height_pt,
               ),
               rotate_deg=0,
               skipped=False,
           )
           for index, (width_pt, height_pt) in enumerate(sizes)
       ]
   ```

   Error ordering is unchanged: a zero-page or corrupt PDF still raises from
   the sizes call, before anything is hashed. So is the returned value.

### 1b — merge one image at a time, in bounded batches

The design, mirroring `export._flush_batch`: convert an image, open it,
append its page, and drop the reference — never build a list. Every
`_MERGE_BATCH_IMAGES` images, write what has been merged so far to a scratch
file, close every open single-image PDF, and reopen the merged document from
that scratch file.

**The bound this establishes is on image bytes held at once**, not on total
output size — same wording as `export._flush_batch`, and true for the same
reason. What changes is that "at once" goes from *every* image to at most
`_MERGE_BATCH_IMAGES`.

**Chosen: a new scratch file per flush, reopened with plain
`pikepdf.open`.** Rejected: `pikepdf.open(out_path,
allow_overwriting_input=True)`, which is what `export._export_batched` uses
at `deckle/core/export.py:816`. It is legal and shorter, but it works by
reading the entire file into a Python `BytesIO` — which would put the
growing merged document on the Python heap and undo exactly the bound this
change exists to establish. `pikepdf.open(path)` on a path reads lazily from
disk. The export path can afford the other choice because its output is one
sheet per page and it is already resident; a 500-scan merge is not.

3. **`deckle/core/loader.py`**, add a module constant beside
   `_CACHE_MAX_BYTES` (`:43`):

   ```python
   # How many images are merged before the in-progress document is written
   # out and every open single-image PDF is released. 50, matching
   # `export._BATCH_SHEETS`, because it is the same bound for the same
   # reason. The trade is arithmetic: at 50 the peak holds fifty images'
   # PDF bytes (~250 MB for the 5 MB-per-scan case this was measured
   # against, down from ~2.5 GB), and a 500-image import rewrites the
   # growing document nine times. Halving it halves the memory and doubles
   # the rewrites.
   _MERGE_BATCH_IMAGES = 50
   ```

4. **`deckle/core/loader.py`**, add two helpers above `load_image_dir`:

   ```python
   def _remove_quietly(path: str) -> None:
       """Delete a scratch file, recording a failure rather than raising.

       A part file that will not delete is bounded waste inside the cache
       directory :func:`_evict_lru_cache_entries` already prunes, and it must
       not be what fails an import that has otherwise succeeded.
       """
       try:
           os.remove(path)
       except OSError as exc:
           log_exception("import_part_cleanup_failed", exc, path=path)


   def _merge_one_image(
       merged: pikepdf.Pdf, opened: list, image_path: str,
       dpi: tuple[float, float] | None,
   ) -> None:
       """Convert one image to a PDF page and append it to ``merged``.

       The single-image document stays in ``opened`` rather than being
       closed here: pikepdf reads a page's content lazily from the document
       it was opened over, so a source cannot be released until the pages
       taken from it have been written out.

       A function rather than four lines inline so a test can watch
       ``opened`` grow. How many image byte-buffers are alive at once is the
       whole property being bounded here, and it is invisible from outside
       the loop -- the same reason ``export._place_output_page`` takes its
       ``source_cache`` as a parameter.
       """
       page_pdf = pikepdf.open(io.BytesIO(_single_image_pdf_or_raise(image_path, dpi)))
       opened.append(page_pdf)
       merged.pages.extend(page_pdf.pages)


   def _flush_merge_batch(
       merged: pikepdf.Pdf, opened: list, cache_dir: str,
       previous_part: str | None,
   ) -> tuple[pikepdf.Pdf, str]:
       """Write the merge so far to a fresh scratch file and reopen it.

       Clearing ``opened`` is the load-bearing half, exactly as it is in
       ``export._flush_batch``: without it the documents are closed but the
       list keeps growing, and the bytes behind them are never released.

       A **new** scratch file each time rather than saving back over the one
       being read. ``pikepdf.open(path, allow_overwriting_input=True)`` is
       what would make that legal, and it does it by reading the whole file
       into a Python ``BytesIO`` -- putting the growing merged document on
       the Python heap and undoing the bound this function exists to
       establish. The previous part is deleted once the new one is open.

       :returns: the reopened document and the scratch path it is reading.
       """
       fd, part_path = tempfile.mkstemp(suffix=".part.pdf", dir=cache_dir)
       os.close(fd)
       merged.save(part_path)
       merged.close()
       log_event("import_merge_batch_flushed", open_images=len(opened))
       for page_pdf in opened:
           page_pdf.close()
       opened.clear()
       reopened = pikepdf.open(part_path)
       if previous_part is not None:
           _remove_quietly(previous_part)
       return reopened, part_path
   ```

   `log_event` is already imported by neighbouring core modules but **not**
   by `loader.py`, which imports only `log_exception`
   (`deckle/core/loader.py:24`). Extend that import to
   `from deckle.core.diagnostics import log_event, log_exception`.

5. **`deckle/core/loader.py`**, replace `:576-598` (the `per_image_pdfs`
   list and the merge block) with:

   ```python
    cache_dir = os.path.join(tempfile.gettempdir(), _CACHE_DIR_NAME)
    os.makedirs(cache_dir, exist_ok=True)
    _evict_lru_cache_entries(cache_dir, max_bytes)
    fd, out_path = tempfile.mkstemp(suffix=".pdf", dir=cache_dir)
    os.close(fd)

    merged = pikepdf.Pdf.new()
    opened: list = []
    part_path: str | None = None
    try:
        total = len(image_paths)
        for position, (img_path, dpi) in enumerate(zip(image_paths, dpis), start=1):
            _merge_one_image(merged, opened, img_path, dpi)
            if position % _MERGE_BATCH_IMAGES == 0 and position != total:
                merged, part_path = _flush_merge_batch(
                    merged, opened, cache_dir, part_path
                )
        merged.save(out_path)
    finally:
        for page_pdf in opened:
            page_pdf.close()
        merged.close()
        if part_path is not None:
            _remove_quietly(part_path)
   ```

   Note the boundary condition `position != total`, copied from
   `export.py:814`: flushing after the last image would rewrite the whole
   document for nothing.

   The cache directory, the eviction and the output path move **above** the
   merge loop, because a flush needs somewhere to write. That changes one
   observable ordering: eviction now runs before the images are converted
   rather than after, so an import that dies partway through conversion —
   `CorruptImageError` on image 300 of 500 — has already pruned the cache.
   That is what `load_image_dir`'s own docstring already promises
   ("before writing, least-recently-used entries are evicted"), and it is
   the better of the two orders; `tests/test_loader.py`'s eviction test does
   not depend on it either way.

6. **`deckle/core/loader.py`**, in `load_image_dir`'s docstring, add after
   the cache-budget paragraph:

   ```
    Images are converted and merged **one at a time**, and every
    ``_MERGE_BATCH_IMAGES`` the in-progress document is written out so the
    single-image PDFs behind it can be released. The bound is on how many
    images' PDF bytes are alive at once, not on the output's size -- the
    same bound, for the same reason, that ``export._flush_batch``
    establishes for the export path. Building the whole list first held
    every image's bytes simultaneously: roughly 2.5 GB for 500 scans.
   ```

## 4. Tests

Write all four before touching `deckle/core/loader.py`.

### 1a

**File:** `tests/test_loader.py`, in the `load_pdf` section (after `:100`).

#### `test_hashing_a_source_does_not_hold_the_pdfium_lock`

- **Setup:** a 3-page PDF from the file's existing `_make_pdf` helper.
  Monkeypatch `loader._sha256_file` with a spy that, **from a second
  thread**, attempts `render._PDFIUM_LOCK.acquire(blocking=False)`, records
  the result, releases if acquired, joins, and then delegates to the real
  function.
- **Why a second thread:** `_PDFIUM_LOCK` is an `RLock`
  (`deckle/core/render.py:68`, and its docstring says why), so
  `acquire(blocking=False)` from the thread that already holds it succeeds
  and would report the lock free when it is not. The same probe pattern is
  used by `tests/test_render_concurrency.py:137-142`.
- **Assertion in words:** the probe acquired the lock — i.e. no other thread
  was holding it — so the hash ran outside the guard.
- **Expected failure on the unfixed tree:**
  `AssertionError: the pdfium lock was held while hashing the source; every preview in the process is blocked for the length of the read`

#### `test_measuring_a_source_does_hold_the_pdfium_lock`

The other half, so the split cannot be "fixed" by dropping the guard.

- **Setup:** the same 3-page PDF. Monkeypatch `pdfium.PdfPage.get_size`
  with a spy that runs the same second-thread probe and then delegates.
- **Assertion in words:** the probe did **not** acquire the lock, because
  the guard is held across the document's whole life while pages are being
  measured.
- **Expected failure on the unfixed tree:** none — it passes before and
  after, which is the point: it pins the guard that must survive the change.

### 1b

**File:** `tests/test_loader.py`, in the `load_image_dir` section. Both use
the isolated-temp-dir pattern already at `tests/test_loader.py:185-187`:

```python
    fake_temp_root = tmp_path / "fake_os_temp"
    fake_temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_temp_root))
```

and the file's existing `_make_image` helper for fixtures. Put the images in
a separate `tmp_path / "scans"` directory so the fake temp root is not
scanned as part of the import.

#### `test_images_are_converted_and_merged_one_at_a_time`

- **Setup:** four images in `tmp_path / "scans"`. Record an event list by
  wrapping two functions: `loader._single_image_pdf_or_raise` (append
  `"convert"`, then delegate) and `loader.pikepdf.open` (append `"open"`,
  then delegate). Call `load_image_dir(str(scans))`.
- **Assertion in words:** the first eight events alternate —
  `["convert", "open"] * 4` — because each image is opened before the next
  is converted. (The ninth `"open"` is the read-back of the merged file at
  `deckle/core/loader.py:602`; slice to eight so the assertion does not
  depend on it.)
- **Expected failure on the unfixed tree:**
  `assert ['convert', 'convert', 'convert', 'convert', 'open', 'open', 'open', 'open'] == ['convert', 'open', 'convert', 'open', 'convert', 'open', 'convert', 'open']`
  — the four conversions all complete before the first open, which is the
  defect stated as a sequence.
- **Note:** patching `loader.pikepdf.open` patches the `pikepdf` module
  globally for the duration, so it also counts the encryption probe and the
  final read-back. Neither runs before the merge loop for an image-directory
  import, so the first eight events are the loop's.

#### `test_image_import_holds_only_one_batch_of_images_open`

The deterministic bound, in the shape of
`tests/test_hardening_limits.py:170-211`
(`test_export_bounds_concurrently_open_source_handles`).

- **Setup:** `monkeypatch.setattr(loader, "_MERGE_BATCH_IMAGES", 3)`; ten
  images in `tmp_path / "scans"`. Wrap `loader._merge_one_image` with a spy
  that delegates and then appends `len(opened)` to a high-water list.
- **Assertion in words:** the list is non-empty (the test built the right
  input); its maximum is at most `_MERGE_BATCH_IMAGES`; and its maximum is
  strictly less than the number of images — the second assertion is what
  distinguishes "bounded" from "the batch happens to equal the document".
  Additionally: the merged PDF has ten pages, in natural order, so the
  batching did not lose or reorder anything.
- **Expected failure on the unfixed tree:**
  `AttributeError: module 'deckle.core.loader' has no attribute '_merge_one_image'`
  — the loop does not exist yet. Paired with the sequencing test above,
  which fails informatively, that is the right pair of reasons.

#### `test_image_import_peak_python_heap_does_not_grow_with_the_image_count`

```python
@pytest.mark.slow
```

The `slow` marker is already registered in `pyproject.toml:49-51`
("measures real resource usage ... slower than a unit test but still
executes and asserts"), and `tests/test_export.py:292` is the existing
example.

- **Setup:** `monkeypatch.setattr(loader, "_MERGE_BATCH_IMAGES", 5)`.
  Two folders under `tmp_path`, one with 20 images and one with 100, each
  image 1200 × 1600 of random noise saved as JPEG (noise so it does not
  compress to nothing — a flat image produces a few kilobytes and the
  measurement becomes noise). Measure each import with:

  ```python
  tracemalloc.start()
  try:
      tracemalloc.reset_peak()
      load_image_dir(str(folder))
      _, peak = tracemalloc.get_traced_memory()
  finally:
      tracemalloc.stop()
  ```

  Import the 20-image folder once before measuring, as a warm-up, for the
  reason `tests/test_export.py:368-371` gives.
- **Assertion in words:** the 100-image import's peak traced allocation is
  under four times the 20-image import's, so the per-image working set does
  not scale with the document. The failure message reports both peaks and
  the ratio.
- **Expected failure on the unfixed tree:** the ratio is roughly the ratio
  of the image counts, because `per_image_pdfs` holds all of them —
  `AssertionError: peak Python-heap allocation grew 4.9x from a 20-image to a 100-image import (peak_20=..., peak_100=...); the per-image working set must stay bounded by _MERGE_BATCH_IMAGES`
- **Why `tracemalloc` here, when `tests/test_export.py:292` explicitly
  rejects it in favour of `psutil` RSS:** write this into the docstring.
  There, the dominant cost was pikepdf's C++/QPDF memory, which
  `tracemalloc` cannot see, so it produced a noisy and unrepresentative
  ratio. Here the thing being bounded is precisely what `tracemalloc` *can*
  see and RSS cannot isolate: `bytes` returned by `img2pdf.convert` and the
  `io.BytesIO` copies made from them, both on the Python heap. The assembled
  QPDF document — the part that is genuinely unbounded and is not what this
  change is about — is C++ and invisible to `tracemalloc`, which is exactly
  the separation that makes it the right instrument for this measurement and
  the wrong one for that one. Say so, and cross-reference both tests.
- **Bound, not a tight one.** 4x follows A-8's convention in
  `tests/test_export.py:377-381`; it is a blow-up alarm. The deterministic
  guard is `test_image_import_holds_only_one_batch_of_images_open`.

## 5. Acceptance

| Check | Command |
|---|---|
| The lock tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_loader.py -k "pdfium_lock"` |
| The streaming tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_loader.py -k "one_at_a_time or one_batch"` |
| The slow memory test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_loader.py -m slow` |
| The whole loader suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_loader.py` |
| No list of every image's bytes remains | `! grep -n "per_image_pdfs" deckle/core/loader.py` |
| The hash is no longer inside a guarded region | `.venv/bin/python -c "import ast,inspect; from deckle.core import loader; src=inspect.getsource(loader._read_pdf_pages); assert 'pdfium_guard' not in src, src; assert '_sha256_file' in src; print('OK')"` |
| The guard still wraps every pdfium open | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_render_concurrency.py` |
| Importing still does not lock the source on Windows | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_project_cli.py -k lock` |
| Import warnings still reach the CLI | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_io.py -k "skipped_file or non_image or mixed_dpi"` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two R0.3/R0.4 failures from `00-environment.md` and nothing else) |

Checked against the current tree: `grep -n "per_image_pdfs"
deckle/core/loader.py` currently matches `:576` and `:584`, so the `!` form
correctly fails today and passes after. `-k "pdfium_lock"` and
`-k "one_at_a_time or one_batch"` currently match nothing and exit 5.
`-m slow` currently selects only `tests/test_export.py:293`.

## 6. Out of scope

- **B26** — the loader storing the *temp cache file* as the source path for
  an image-folder import, which `evict_lru_files` can later delete out from
  under a saved project. This spec makes that file cheaper to produce; it
  does not change where it lives. The open owner decision in ROADMAP §6
  ("should image-folder imports spool the normalised PDF beside the project")
  is B26's, not this one's.
- **M6** — "the loader imports the whole rasteriser to borrow a lock", i.e.
  moving `pdfium_guard` into a `core/pdfium_lock.py`. Keep the
  `from deckle.core.render import pdfium_guard` import exactly as it is;
  moving it is a separate change with its own import-graph consequences.
- **A-8's export batching.** `export._flush_batch` is the model here, not a
  target. Do not refactor the two into one helper: they bound different
  things (open *source* handles versus open *image* documents) and the
  loader's cannot use `allow_overwriting_input`, which is the whole reason
  it needs its own scratch file.
- Do not change `_sha256_file` itself, its chunk size, or what it hashes.
  The hash is part of `SourceRef` and is compared by `project_io`.

## 7. decisions.md entry

```
## 2026-09-05 — The importer froze every preview, then held the whole folder in memory
- Symptom: Two costs on the one action a user takes first. `_read_pdf_pages` computed the source's whole-file SHA-256 **inside** `pdfium_guard()` — the process-wide lock every preview render, thumbnail window and print rasterisation takes — so importing a large scan stopped the window repainting for the length of a `hashlib` read that touches nothing pdfium owns. And `load_image_dir` built `per_image_pdfs`, the complete PDF bytes of every image, before opening any of them: roughly 2.5 GB for a folder of 500 scans, plus a second copy per image inside the `BytesIO` handed to `pikepdf.open`.
- Fix: The measurement keeps the guard, in a new `_read_pdf_page_sizes`; the hash runs after it returns. And images are converted, opened and appended **one at a time**, with the in-progress document written out every `_MERGE_BATCH_IMAGES` (50, matching `export._BATCH_SHEETS`) so the single-image PDFs behind it can be released. The bound is on image bytes alive at once, not on output size — the same bound `export._flush_batch` establishes, with the same load-bearing half: clearing the list, not merely closing the documents.
- Surfaces: The flush writes a **new** scratch file each time and reopens it with plain `pikepdf.open`, rather than `allow_overwriting_input=True` as the export path does. That flag works by reading the whole file into a Python `BytesIO`, which would have put the growing merged document on the Python heap and undone the bound. Copying the export path's shape without checking what its convenience flag costs would have produced a change that passed a batching test and saved nothing.
- Watch: The memory guard is deliberately two tests. The deterministic one watches the high-water mark of open image documents, in the shape of `test_export_bounds_concurrently_open_source_handles`. The `slow` one measures **`tracemalloc`**, not `psutil` RSS — the opposite instrument from the one `test_export.py` argues for, and for the same reason it argues for it: there the dominant cost was QPDF's C++ memory, invisible to `tracemalloc`; here the thing bounded is `bytes` and `BytesIO` on the Python heap, and the thing *not* bounded is the C++ document. **Pick the instrument that can see the thing being fixed and not the thing that is not.**
- Commit: (this commit)
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli`.
- **`tests/test_render_concurrency.py:180-227` scans the source, not the
  runtime.** It walks every `.py` under `deckle/`, finds every
  `pdfium.PdfDocument(` call, and looks *upward* for a shallower-indented
  line containing `pdfium_guard()` or `_PDFIUM_LOCK`, stopping at the first
  `def`/`class`. So the open must stay lexically inside a `with
  pdfium_guard():` in the *same function*. Splitting the guard out into a
  wrapper that calls an unguarded opener fails this check, correctly.
- **`_PDFIUM_LOCK` is an `RLock`.** Probing it with
  `acquire(blocking=False)` from the thread that holds it succeeds and tells
  you nothing. Probe from another thread —
  `tests/test_render_concurrency.py:137-142` is the precedent.
- **`opened` must not be closed before `merged` is saved.** pikepdf reads a
  page's content lazily from the document it was opened over, so closing a
  single-image PDF whose page has been appended but not yet written produces
  a document with missing content — and no exception. This is exactly why
  `_flush_merge_batch` saves *before* it closes, in that order.
- **Do not reuse `out_path` as the flush target.** `merged` is lazily
  reading the previous part file; saving onto the file being read is what
  `allow_overwriting_input` exists to permit, and permitting it costs the
  bound. Write a new part, reopen, then delete the old one.
- **Part files land in the shared import cache directory** under the OS temp
  dir, alongside the merged outputs, and count toward `_CACHE_MAX_BYTES`.
  They are deleted in the same call; a crash leaves at most two, and the
  next import's `_evict_lru_cache_entries` prunes them. Give them the
  `.part.pdf` suffix so a human looking in the directory can tell them from
  a real import.
- **`tests/test_loader.py`'s eviction test redirects `tempfile.gettempdir`**
  (`:185-187`). Any new test that imports a folder without doing the same
  writes into the *real* shared cache directory, where another test's
  eviction accounting can see it.
- **`loader.py` currently imports only `log_exception`** from
  `deckle.core.diagnostics` (`:24`). `_flush_merge_batch` needs `log_event`
  too.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`), and
  nothing here does — but note that `tracemalloc` in the new slow test must
  not be started around an import that has already loaded Qt, or the numbers
  include somebody else's allocations. Start and stop it inside the
  measurement helper, as written.
