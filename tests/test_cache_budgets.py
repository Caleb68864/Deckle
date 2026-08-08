"""Both on-disk caches are bounded by a budget, not by a cleanup call.

Deckle keeps two caches in the system temp directory: the img2pdf import
normalisation cache and the single-sheet export cache. The import one has
been bounded since red-team advisory A-7 -- 2 GB, evicted least-recently-
used by whoever next writes to it, with the reasoning that heavy image
import "otherwise accumulates silently in temp".

The export cache had the same exposure and none of the defence. Its
in-memory LRU bounds what it will hand *back*; it does nothing about a
file a killed process left behind, or one whose deletion failed. And
``clear_sheet_cache`` -- whose docstring said "call on project close" --
**had no callers at all**, in the product or in the app.

Measured on one development machine before the fix: **8,297 files, 50 MB**
in ``%TEMP%\\deckle_export_cache``, against an in-memory LRU holding 20.

The bound belongs on the directory and is enforced by the next writer,
for the reason the import cache's already is: a cleanup call at shutdown
only runs on the exits that reach it, and the leak is largest on the ones
that do not.
"""

from __future__ import annotations

import os

import pytest

from deckle.core import export as export_mod
from deckle.core.paths import evict_lru_files


def _fill(directory, count, size=1024, prefix="f"):
    paths = []
    for i in range(count):
        path = os.path.join(str(directory), f"{prefix}{i}.bin")
        with open(path, "wb") as handle:
            handle.write(b"\0" * size)
        # Distinct access times, oldest first, so eviction order is defined.
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
        paths.append(path)
    return paths


def _total(directory):
    return sum(
        os.path.getsize(os.path.join(str(directory), n))
        for n in os.listdir(str(directory))
    )


# -- the shared helper ---------------------------------------------------


def test_a_cache_under_budget_is_left_alone(tmp_path):
    _fill(tmp_path, 4, size=100)

    evict_lru_files(tmp_path, max_bytes=10_000)

    assert len(os.listdir(str(tmp_path))) == 4


def test_a_cache_over_budget_is_pruned_to_fit(tmp_path):
    _fill(tmp_path, 20, size=1000)

    evict_lru_files(tmp_path, max_bytes=5_000)

    assert _total(tmp_path) <= 5_000


def test_the_least_recently_used_go_first(tmp_path):
    """Recency is last *access*, so a file that was merely read still
    counts as recently used -- a cache that evicted by age would drop the
    sheet the user is looking at."""
    paths = _fill(tmp_path, 10, size=1000)

    evict_lru_files(tmp_path, max_bytes=3_000)

    survivors = set(os.listdir(str(tmp_path)))
    assert survivors <= {os.path.basename(p) for p in paths[-4:]}


def test_a_missing_directory_is_not_an_error(tmp_path):
    evict_lru_files(os.path.join(str(tmp_path), "nope"), max_bytes=0)


def test_an_undeletable_file_does_not_stop_the_others(tmp_path, monkeypatch):
    """One stuck file must not leave the whole cache over budget."""
    paths = _fill(tmp_path, 6, size=1000)
    stuck = paths[0]
    real_remove = os.remove

    def refuse(path, *args, **kwargs):
        if os.path.abspath(path) == os.path.abspath(stuck):
            raise PermissionError(5, "Access is denied")
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", refuse)
    seen = []
    evict_lru_files(tmp_path, max_bytes=1_000,
                    on_error=lambda e, exc, p: seen.append(e))

    assert os.path.exists(stuck)
    assert len(os.listdir(str(tmp_path))) < 6
    assert "cache_eviction_failed" in seen


def test_eviction_never_raises(tmp_path, monkeypatch):
    """A cache that cannot be pruned must not be what stops an export."""
    _fill(tmp_path, 3)

    def explode(*args, **kwargs):
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(os, "scandir", explode)
    evict_lru_files(tmp_path, max_bytes=0)


# -- the export cache actually uses it -----------------------------------


def test_the_export_cache_directory_is_bounded(tmp_path, monkeypatch):
    """Asserted through ``_cache_dir``, the function every cached export
    calls, rather than by inspecting the module for a budget constant."""
    monkeypatch.setattr(export_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    directory = os.path.join(str(tmp_path), export_mod._CACHE_DIR_NAME)
    os.makedirs(directory, exist_ok=True)
    _fill(directory, 40, size=1024)
    monkeypatch.setattr(export_mod, "_CACHE_MAX_BYTES", 8 * 1024)

    export_mod._cache_dir()

    assert _total(directory) <= 8 * 1024


def test_the_export_cache_has_a_budget_at_all():
    """The constant is the whole point -- an unbounded cache in temp was
    the defect. Pinned so a refactor cannot quietly drop it."""
    assert export_mod._CACHE_MAX_BYTES > 0


def test_clearing_the_cache_still_removes_its_files(tmp_path, monkeypatch):
    """``clear_sheet_cache`` is now called when a project is replaced, so
    it has to actually reclaim rather than only forget."""
    monkeypatch.setattr(export_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    export_mod.clear_sheet_cache()
    directory = export_mod._cache_dir()
    made = _fill(directory, 3, prefix="sheet")
    for index, path in enumerate(made):
        export_mod._cache.put((index, "planhash"), path)

    export_mod.clear_sheet_cache()

    assert all(not os.path.exists(p) for p in made)
