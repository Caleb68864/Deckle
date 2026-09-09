"""Hardening pass 8 -- resource limits on the gutter-shift export path.

Very large documents, the bounds on the module-level caches in
:mod:`deckle.core.export`, cancellation checkpoints in
:mod:`deckle.core.render`, and temp-file cleanup when things go wrong.

Every test here fails against the pre-hardening code: each one names, in
its docstring, the specific way the resource was previously unbounded, left
behind, or silently wrong.
"""

from __future__ import annotations

import os
import tempfile
import threading
from unittest import mock

import pikepdf
import pytest

from deckle.core import export, paths, render
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")

LETTER = (612.0, 792.0)


# --- helpers ----------------------------------------------------------


def _write_source_pdf(tmp_path, n_pages: int, name: str = "src.pdf") -> str:
    path = os.path.join(str(tmp_path), name)
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(400.0, 600.0))
    pdf.save(path)
    pdf.close()
    return path


def _ref(path: str, page_index: int = 0) -> SourceRef:
    return SourceRef(
        path=path, page_index=page_index, sha256="a" * 64, width_pt=400.0, height_pt=600.0
    )


def _plan_from_source(tmp_path, n_pages: int) -> SheetPlan:
    path = _write_source_pdf(tmp_path, n_pages)
    pages = [
        SourcePage(ref=_ref(path, i), rotate_deg=0, skipped=False) for i in range(n_pages)
    ]
    settings = LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    return GutterShiftStrategy().impose(pages, settings)


def _identity() -> Placement:
    return Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)


def _one_side_plan(refs: tuple[SourceRef, ...]) -> SheetPlan:
    pages = tuple(
        OutputPage(source_ref=r, placement=_identity(), is_filler=False) for r in refs
    )
    return SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=pages), back=None)],
        paper_pt=LETTER,
        warnings=[],
    )


@pytest.fixture(autouse=True)
def _isolate_caches():
    export.clear_sheet_cache()
    render.clear_ink_bbox_cache()
    yield
    export.clear_sheet_cache()
    render.clear_ink_bbox_cache()


# --- CACHE KEY: a stale/wrong render is worse than a miss -------------


def test_plan_hash_separates_pages_that_would_otherwise_concatenate():
    """The cache key must not let one page's content impersonate two.

    ``_output_page_key`` is built from ``SourceRef.path`` -- an arbitrary,
    user-supplied string -- followed by ``:``/``|``-separated fields, and
    the per-page keys used to be fed into the digest end to end with no
    framing. A path containing those separators therefore reproduces, by
    itself, the exact bytes two different pages contribute, so a
    two-page side and a one-page side hash identically.

    That is not a theoretical collision: ``export_sheet_cached`` is keyed
    on this hash, so the second plan is handed the *first* plan's exported
    PDF -- a preview showing pages that are not in the document.
    """
    first = SourceRef(path="a", page_index=0, sha256="s1", width_pt=1.0, height_pt=2.0)
    second = SourceRef(path="b", page_index=1, sha256="s2", width_pt=3.0, height_pt=4.0)
    # Exactly `first`'s key text, then `second`'s leading path field. The
    # trailing `|None` is the crop, which is part of the key because a
    # plan differing only by crop must not reuse another's render -- so
    # the colliding path has to reproduce that field too.
    merged = SourceRef(
        path="a:0:s1:1.0:2.0|1.0:1.0:0.0:0.0:0|False|Noneb",
        page_index=1,
        sha256="s2",
        width_pt=3.0,
        height_pt=4.0,
    )

    two_pages = _one_side_plan((first, second))
    one_page = _one_side_plan((merged,))

    # This crafted path used to reproduce, byte for byte, the two real
    # pages' concatenated key text -- that assertion stood here as a
    # precondition, and it no longer holds: `_output_page_key` now
    # length-prefixes the path, so a path can no longer impersonate the
    # fields that follow it. Recorded rather than deleted, because the
    # collision it describes is the reason both framings exist.
    assert export._output_page_key(
        two_pages.sheets[0].front.pages[0]
    ) + export._output_page_key(two_pages.sheets[0].front.pages[1]) != (
        export._output_page_key(one_page.sheets[0].front.pages[0])
    )

    # The conclusion is unchanged and now holds for two independent
    # reasons: the per-page keys are length-prefixed into the digest, and
    # the path is length-prefixed within its own key. Either alone is
    # sufficient; the test passes if either survives a refactor.
    assert export._plan_hash(two_pages) != export._plan_hash(one_page)


def test_plan_hash_separates_marks_from_following_marks():
    """The same framing requirement applies to the mark list."""
    from deckle.core.models import Mark

    def plan_with(marks):
        page = OutputPage(source_ref=_ref("s"), placement=_identity(), is_filler=False)
        return SheetPlan(
            sheets=[Sheet(index=0, front=Side(pages=(page,), marks=marks), back=None)],
            paper_pt=LETTER,
            warnings=[],
        )

    one = plan_with((Mark(kind="fold_line", x0=0.0, y0=0.0, x1=1.0, y1=2.0),))
    two = plan_with(
        (
            Mark(kind="fold_line", x0=0.0, y0=0.0, x1=1.0, y1=2.0),
            Mark(kind="fold_line", x0=0.0, y0=0.0, x1=1.0, y1=2.0),
        )
    )
    assert export._plan_hash(one) != export._plan_hash(two)


# --- BATCHING: the bound is on concurrently open source handles -------


def test_export_bounds_concurrently_open_source_handles(tmp_path, monkeypatch):
    """Batching must actually release source handles, not merely close them.

    ``_flush_batch`` closes every open source PDF *and clears the dict*.
    Without the clear, the dict keeps growing and a project assembled from
    one PDF per page ends the export holding an entry for every page in the
    document -- which is exactly the shape the 300-page working case takes
    when pages come from separate files.

    Asserted by watching the live ``source_cache`` that ``_export_batched``
    threads through ``_place_output_page``: its high-water mark must stay
    inside one batch, not grow to the number of distinct source files.
    """
    monkeypatch.setattr(export, "_BATCH_SHEETS", 3)

    n_sources = 24  # -> 24 pages -> 12 sheets -> 4 batch boundaries
    paths = [
        _write_source_pdf(tmp_path, 1, name=f"src_{i}.pdf") for i in range(n_sources)
    ]
    pages = [
        SourcePage(ref=_ref(p, 0), rotate_deg=0, skipped=False) for p in paths
    ]
    plan = GutterShiftStrategy().impose(
        pages, LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    )

    high_water = []
    real_place = export._place_output_page

    def spy(sheet_pdf, dest_page, output_page, source_cache):
        real_place(sheet_pdf, dest_page, output_page, source_cache)
        high_water.append(len(source_cache))

    monkeypatch.setattr(export, "_place_output_page", spy)

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export.export(plan, out_path)

    assert high_water, "no pages were placed -- the test built the wrong plan"
    # One page per side, two sides per sheet, three sheets per batch.
    assert max(high_water) <= 2 * export._BATCH_SHEETS
    assert max(high_water) < n_sources


def test_export_flushes_once_per_batch_boundary(tmp_path, monkeypatch):
    """The batch boundary fires on the interval, and never on the last sheet.

    Flushing after the final sheet would save and reopen the whole output
    for nothing.
    """
    monkeypatch.setattr(export, "_BATCH_SHEETS", 3)
    plan = _plan_from_source(tmp_path, 24)  # 12 sheets
    assert len(plan.sheets) == 12

    flushes = []
    real_flush = export._flush_batch

    def spy(out, source_cache, tmp):
        flushes.append(len(source_cache))
        real_flush(out, source_cache, tmp)

    monkeypatch.setattr(export, "_flush_batch", spy)
    export.export(plan, os.path.join(str(tmp_path), "out.pdf"))

    # Sheets 3, 6, 9 -- not 12, which is the last.
    assert len(flushes) == 3


# --- TEMP FILES: nothing left behind on any abnormal exit -------------


def test_export_removes_scratch_file_when_composition_fails(tmp_path, monkeypatch):
    """An exception mid-composition must not strand the scratch PDF.

    The scratch file lives beside the requested output, so a leaked one is
    visible to the user in their own output directory.
    """
    plan = _plan_from_source(tmp_path, 4)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path = str(out_dir / "out.pdf")

    def boom(*args, **kwargs):
        raise RuntimeError("composition exploded")

    monkeypatch.setattr(export, "_export_batched", boom)

    with pytest.raises(RuntimeError, match="composition exploded"):
        export.export(plan, out_path)

    assert os.listdir(str(out_dir)) == []


def test_export_cleanup_failure_does_not_mask_the_real_error(tmp_path, monkeypatch):
    """A locked scratch file must not replace the real cause of a failure.

    On Windows the scratch PDF can still be held by a handle the failing
    export was using, so removing it raises ``PermissionError`` -- and
    that, not the actual fault, is what reaches the user and the log.

    The guarantee is unchanged; only its owner moved. ``export`` used to
    do its own scratch-and-rename and swallow the cleanup failure through
    ``_safe_remove``; it now goes through
    :func:`deckle.core.paths.atomic_output`, whose unlink is wrapped in
    ``except OSError: pass`` before the original exception is re-raised.
    So this patches ``os.unlink`` where the previous version patched
    ``os.remove``, and asserts the same thing about the same export.
    """
    plan = _plan_from_source(tmp_path, 4)
    out_path = os.path.join(str(tmp_path), "out.pdf")

    def boom(*args, **kwargs):
        raise RuntimeError("the actual problem")

    monkeypatch.setattr(export, "_export_batched", boom)

    real_unlink = os.unlink
    removed = []

    def stubborn_unlink(path):
        if str(path).endswith(".pdf") and "out.pdf" not in str(path):
            removed.append(path)
            raise PermissionError("file is in use by another process")
        return real_unlink(path)

    monkeypatch.setattr(paths.os, "unlink", stubborn_unlink)

    with pytest.raises(RuntimeError, match="the actual problem"):
        export.export(plan, out_path)

    assert removed, "the scratch file cleanup path was never exercised"


def test_export_sheet_cached_removes_its_temp_file_when_export_fails(
    tmp_path, monkeypatch
):
    """A failed cached export must not strand a file in the cache dir.

    ``export_sheet_cached`` creates the temp file *before* exporting into
    it. If the export raises, the file never reaches the LRU -- so neither
    eviction nor ``clear_sheet_cache()`` can ever find it, and it survives
    for the life of the machine's temp directory.

    The two listings are a diff of a **shared** directory, which is why
    ``DECKLE_EXPORT_CACHE_DIR`` exists and why ``tests/conftest.py`` sets
    it. Against the machine's real temp directory this reads another
    process's residue, and its own eviction pass can delete a file between
    the two calls; both fail the test while saying nothing about the
    cleanup path it is here to check.
    """
    plan = _plan_from_source(tmp_path, 4)
    cache_dir = export._cache_dir()
    before = set(os.listdir(cache_dir))

    def boom(*args, **kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr(export, "export", boom)

    with pytest.raises(RuntimeError, match="export failed"):
        export.export_sheet_cached(plan, 0)

    assert set(os.listdir(cache_dir)) == before


# --- THE LRU CACHE: bounded, and evictions really delete --------------


def test_lru_cache_evicts_beyond_maxsize_and_deletes_the_backing_file(tmp_path):
    cache = export._LRUCache(maxsize=2)
    paths = []
    for i in range(3):
        p = str(tmp_path / f"c{i}.pdf")
        open(p, "wb").close()
        paths.append(p)
        cache.put((i, "h"), p)

    assert len(cache) == 2
    assert not os.path.exists(paths[0]), "evicted entry's temp file was left on disk"
    assert os.path.exists(paths[1]) and os.path.exists(paths[2])


def test_lru_cache_put_retires_the_file_it_displaces_without_deleting_it(tmp_path):
    """Two threads racing the same key each make a temp file, and both
    call ``put``.

    The loser's file must survive the call. Its exporter has already been
    handed the path and is about to open it, so deleting it on the spot is
    a use-after-free -- which is exactly what it was, surfacing as a
    ``FileNotFoundError`` raised from inside pdfium against a scratch file
    the user never saw. It must not be *stranded* either: the cache stops
    referring to it, so only ``clear()`` can still reclaim it.
    """
    cache = export._LRUCache(maxsize=8)
    first = str(tmp_path / "first.pdf")
    second = str(tmp_path / "second.pdf")
    open(first, "wb").close()
    open(second, "wb").close()

    cache.put((0, "h"), first)
    cache.put((0, "h"), second)

    assert os.path.exists(first), "the displaced file was deleted while still in use"
    assert cache.get((0, "h")) == second, "the winner must be the live entry"

    cache.clear()
    assert not os.path.exists(second)
    assert not os.path.exists(first), "the displaced file was stranded"


def test_retired_files_are_bounded_so_racing_renders_cannot_fill_the_disk(tmp_path):
    """Retiring rather than deleting trades a use-after-free for a leak if
    it is unbounded. Anything this far back is long since closed."""
    cache = export._LRUCache(maxsize=8)
    paths = []
    for i in range(export._MAX_RETIRED + 10):
        p = str(tmp_path / f"race{i}.pdf")
        open(p, "wb").close()
        paths.append(p)
        cache.put((0, "h"), p)

    survivors = [p for p in paths if os.path.exists(p)]
    assert len(survivors) <= export._MAX_RETIRED + 1, (
        f"retired exports are unbounded: {len(survivors)} files still on disk"
    )
    assert os.path.exists(paths[-1]), "the live entry must survive"
    assert not os.path.exists(paths[0]), "the oldest retired file was never reclaimed"


def test_lru_cache_put_of_the_same_path_twice_keeps_the_file(tmp_path):
    """Re-putting an identical path must not delete the file it points at."""
    cache = export._LRUCache(maxsize=8)
    path = str(tmp_path / "only.pdf")
    open(path, "wb").close()

    cache.put((0, "h"), path)
    cache.put((0, "h"), path)

    assert os.path.exists(path)
    assert cache.get((0, "h")) == path


def test_export_sheet_cache_is_bounded_at_its_declared_size():
    """The module-level cache is finite, not an unbounded dict."""
    assert export._cache._maxsize == export._DEFAULT_CACHE_SIZE
    assert export._DEFAULT_CACHE_SIZE > 0


# --- CANCELLATION -----------------------------------------------------


def test_render_sheet_cancel_after_export_skips_rasterization(tmp_path, monkeypatch):
    """A cancel arriving during the export must not still pay for the raster.

    Rasterization scales with ``dpi`` squared and is the most expensive
    step in ``render_sheet``. There was no checkpoint between opening the
    exported PDF and rendering it, so a cancel that arrived while the
    export was running was ignored until the bitmap was already produced.
    """
    src = _write_source_pdf(tmp_path, 1)
    plan = _one_side_plan((_ref(src, 0),))
    cancel = threading.Event()

    real_doc = render.pdfium.PdfDocument
    rendered = []

    def cancelling_open(path, *args, **kwargs):
        cancel.set()
        return real_doc(path, *args, **kwargs)

    def spy_convert(image):
        rendered.append(True)
        return render._pil_to_rendered_page(image)

    monkeypatch.setattr(render.pdfium, "PdfDocument", cancelling_open)
    monkeypatch.setattr(render, "_pil_to_rendered_page", spy_convert)

    result = render.render_sheet(plan, 0, "front", dpi=144, cancel=cancel)

    assert result.width == 0 and result.height == 0 and result.rgba == b""
    assert not rendered, "rasterized despite the cancel"


def test_render_sheet_cancellation_leaves_no_temp_file(tmp_path, monkeypatch):
    """Cancelling mid-render must not leak the scratch PDF."""
    src = _write_source_pdf(tmp_path, 1)
    plan = _one_side_plan((_ref(src, 0),))
    cancel = threading.Event()

    made: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        made.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)

    real_doc = render.pdfium.PdfDocument

    def cancelling_open(path, *args, **kwargs):
        cancel.set()
        return real_doc(path, *args, **kwargs)

    monkeypatch.setattr(render.pdfium, "PdfDocument", cancelling_open)

    render.render_sheet(plan, 0, "front", dpi=72, cancel=cancel)

    assert made, "no temp file was created -- the test proved nothing"
    # The invariant is that nothing is STRANDED, not that nothing survives.
    # The exported sheet now belongs to the bounded sheet cache, which is
    # the point of having one: a cancelled render leaves the work ready for
    # the next request instead of discarding it. What must not happen is a
    # file that no cache knows about and no cleanup can ever reach.
    survivors = [path for path in made if os.path.exists(path)]
    export.clear_sheet_cache()
    stranded = [path for path in survivors if os.path.exists(path)]
    assert not stranded, f"temp file the cache cannot reclaim: {stranded}"


def test_render_sheet_removes_temp_file_when_export_fails(tmp_path, monkeypatch):
    src = _write_source_pdf(tmp_path, 1)
    plan = _one_side_plan((_ref(src, 0),))

    made: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        made.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(
        render.export,
        "export",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
    )

    with pytest.raises(RuntimeError, match="nope"):
        render.render_sheet(plan, 0, "front", dpi=72)

    assert made
    for path in made:
        assert not os.path.exists(path)


def test_thumbnails_stops_at_the_cancel_checkpoint(tmp_path, monkeypatch):
    """Scrubbing a long document queues windows faster than they render.

    Without a cancel checkpoint the rasterizer works through every page of
    a window the user has already scrolled past.
    """
    src = _write_source_pdf(tmp_path, 5)
    pages = [
        SourcePage(ref=_ref(src, i), rotate_deg=0, skipped=False) for i in range(5)
    ]
    cancel = threading.Event()

    real_convert = render._pil_to_rendered_page

    def cancelling_convert(image):
        cancel.set()
        return real_convert(image)

    monkeypatch.setattr(render, "_pil_to_rendered_page", cancelling_convert)

    result = render.thumbnails(pages, start=0, count=5, dpi=36, cancel=cancel)

    assert len(result) == 1


def test_thumbnails_without_cancel_renders_the_whole_window(tmp_path):
    """The new parameter must be inert when the caller omits it."""
    src = _write_source_pdf(tmp_path, 5)
    pages = [
        SourcePage(ref=_ref(src, i), rotate_deg=0, skipped=False) for i in range(5)
    ]
    assert len(render.thumbnails(pages, start=0, count=5, dpi=36)) == 5


# --- INK BBOX CACHE: bounded -----------------------------------------


def test_ink_bbox_cache_is_lru_bounded(tmp_path, monkeypatch):
    """The ink cache is module-level and outlives a project.

    Unbounded, a session that opens several long documents accumulates one
    entry per page of every document it has ever seen, and nothing but an
    explicit ``clear_ink_bbox_cache()`` ever releases them.
    """
    src = _write_source_pdf(tmp_path, 3)
    monkeypatch.setattr(render, "_INK_BBOX_CACHE_MAX", 2)

    refs = [_ref(src, i) for i in range(3)]
    for ref in refs:
        render.ink_bbox(ref, dpi=12)

    assert len(render._ink_bbox_cache) == 2
    assert refs[0] not in render._ink_bbox_cache
    assert refs[1] in render._ink_bbox_cache
    assert refs[2] in render._ink_bbox_cache


def test_ink_bbox_recent_use_survives_eviction(tmp_path, monkeypatch):
    """Eviction is by least-recent *use*, not least-recent insertion."""
    src = _write_source_pdf(tmp_path, 3)
    monkeypatch.setattr(render, "_INK_BBOX_CACHE_MAX", 2)

    refs = [_ref(src, i) for i in range(3)]
    render.ink_bbox(refs[0], dpi=12)
    render.ink_bbox(refs[1], dpi=12)
    render.ink_bbox(refs[0], dpi=12)  # refresh refs[0]
    render.ink_bbox(refs[2], dpi=12)

    assert refs[0] in render._ink_bbox_cache
    assert refs[1] not in render._ink_bbox_cache


# -- pdfium object lifetimes ---------------------------------------------


def test_rasterize_page_closes_the_page_and_bitmap_before_returning():
    """pdfium children must not outlive their document.

    Every child object pdfium hands out carries a finalizer asserting its
    parent is still open. Render with the obvious shape --

        page = doc[i]; bitmap = page.render(...); doc.close()

    -- and both are still referenced by locals when the document closes.
    Whenever the collector reaches them afterwards, the assertion fires and
    Python prints "Exception ignored in: <finalize object at ...>" with a
    traceback pointing at weakref.py rather than at us. Reported from the
    GUI while dragging a page to reorder it.
    """
    import pypdfium2 as pdfium

    from deckle.core.render import rasterize_page

    doc = pdfium.PdfDocument(FIXTURE)
    closed = {"page": False, "bitmap": False}

    real_getitem = type(doc).__getitem__

    def tracking_getitem(self, index):
        page = real_getitem(self, index)
        real_page_close = page.close
        real_render = page.render

        def watched_page_close(*a, **kw):
            closed["page"] = True
            return real_page_close(*a, **kw)

        def watched_render(*a, **kw):
            bitmap = real_render(*a, **kw)
            real_bitmap_close = bitmap.close

            def watched_bitmap_close(*ba, **bkw):
                closed["bitmap"] = True
                return real_bitmap_close(*ba, **bkw)

            bitmap.close = watched_bitmap_close
            return bitmap

        page.close = watched_page_close
        page.render = watched_render
        return page

    try:
        with mock.patch.object(type(doc), "__getitem__", tracking_getitem):
            image = rasterize_page(doc, 0, scale=0.25)
        assert image is not None, "a page must still come back"
        assert closed["bitmap"], "the bitmap was left for the garbage collector"
        assert closed["page"], "the page was left for the garbage collector"
    finally:
        doc.close()


def test_rasterized_image_survives_closing_the_document():
    """``to_pil`` must copy, not alias.

    Closing the page and bitmap eagerly is only safe if the image does not
    point into the bitmap's buffer -- otherwise the fix would trade a noisy
    finalizer for silent garbage pixels, which is far worse.
    """
    import pypdfium2 as pdfium

    from deckle.core.render import rasterize_page

    doc = pdfium.PdfDocument(FIXTURE)
    image = rasterize_page(doc, 0, scale=0.25)
    doc.close()

    # Touching the pixels after everything upstream is closed must work.
    assert image.size[0] > 0 and image.size[1] > 0
    assert image.convert("RGB").getpixel((0, 0)) is not None


def test_no_render_path_leaves_pdfium_children_to_the_collector():
    """Structural: every pdfium page render goes through the helper.

    Asked with an AST walk rather than a string search, because the helper's
    own docstring quotes the wrong shape as the thing not to do -- a grep
    matches its own counter-example and reports a failure that is really a
    citation.
    """
    import ast
    from pathlib import Path

    import deckle.app.backend as backend_module
    import deckle.core.render as render_module

    for module in (render_module, backend_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        helper = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "rasterize_page"
            ),
            None,
        )
        inside_helper = set()
        if helper is not None:
            inside_helper = {id(n) for n in ast.walk(helper)}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "render"):
                continue
            if isinstance(func.value, ast.Name) and func.value.id == "page":
                assert id(node) in inside_helper, (
                    f"{module.__name__} line {node.lineno} renders a pdfium page "
                    "outside rasterize_page, so its children outlive the document"
                )

def test_a_crafted_source_path_cannot_forge_a_cache_key_boundary():
    """The plan hash must not confuse two different pages.

    ``_update_delimited`` already length-prefixes each page's whole
    contribution, and its docstring explains why: a cache that returns the
    wrong page is worse than no cache at all. Inside that contribution the
    fields were joined with bare colons, and no collision was constructible
    -- but only because every field after the path is an integer, a hex
    digest, a float repr or a bool, none of which can contain a colon.

    That is safety by field type, not by framing. This pins the framing,
    so a ``SourceRef`` gaining a second free-text field cannot remove it
    silently. ``.deckle`` files carry these paths and may be shared, which
    is the reason red-team A-3 treats them as untrusted.
    """
    from deckle.core.export import _output_page_key
    from deckle.core.models import OutputPage, Placement, SourceRef

    def page(path: str, index: int) -> OutputPage:
        return OutputPage(
            source_ref=SourceRef(path=path, page_index=index, sha256="0" * 64,
                                 width_pt=400.0, height_pt=600.0),
            placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0,
                                rotate_deg=0),
            is_filler=False,
        )

    # The second path is the first one with the next field's text appended,
    # which is the shape a bare join is vulnerable to.
    assert _output_page_key(page("a", 1)) != _output_page_key(page("a:1", 1))
    assert _output_page_key(page("a", 12)) != _output_page_key(page("a:1", 2))


def test_the_page_key_still_separates_ordinary_pages():
    """The framing change must not make two distinct pages collide, nor
    two identical ones differ."""
    from deckle.core.export import _output_page_key
    from deckle.core.models import OutputPage, Placement, SourceRef

    def page(path: str, index: int) -> OutputPage:
        return OutputPage(
            source_ref=SourceRef(path=path, page_index=index, sha256="0" * 64,
                                 width_pt=400.0, height_pt=600.0),
            placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0,
                                rotate_deg=0),
            is_filler=False,
        )

    assert _output_page_key(page("book.pdf", 0)) == _output_page_key(page("book.pdf", 0))
    assert _output_page_key(page("book.pdf", 0)) != _output_page_key(page("book.pdf", 1))
    assert _output_page_key(page("a.pdf", 0)) != _output_page_key(page("b.pdf", 0))
