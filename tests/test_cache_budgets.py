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
import tempfile

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
    directory = os.path.join(str(tmp_path), "cache")
    monkeypatch.setenv("DECKLE_EXPORT_CACHE_DIR", directory)
    os.makedirs(directory, exist_ok=True)
    _fill(directory, 40, size=1024)
    monkeypatch.setattr(export_mod, "_CACHE_MAX_BYTES", 8 * 1024)

    export_mod._cache_dir()

    assert _total(directory) <= 8 * 1024


# -- and it can be pointed somewhere that is not shared -------------------


def test_the_export_cache_can_be_pointed_somewhere_else(tmp_path, monkeypatch):
    """``<temp>/deckle_export_cache`` is shared by every Deckle on the
    machine, which is right for a cache and wrong for anything that reads
    the directory back. ``DECKLE_EXPORT_CACHE_DIR`` is the same seam
    ``DECKLE_SESSION_STATE_DIR`` already provides next door."""
    elsewhere = os.path.join(str(tmp_path), "somewhere-else")
    monkeypatch.setenv("DECKLE_EXPORT_CACHE_DIR", elsewhere)

    assert export_mod._cache_dir() == elsewhere
    assert os.path.isdir(elsewhere), "asking for it creates it"


def test_the_override_is_read_every_time_not_cached(tmp_path, monkeypatch):
    """A value read once at import would be set too late to help anything
    -- including the suite's own conftest, which runs after the module may
    already have been imported by an earlier plugin."""
    first = os.path.join(str(tmp_path), "first")
    second = os.path.join(str(tmp_path), "second")

    monkeypatch.setenv("DECKLE_EXPORT_CACHE_DIR", first)
    assert export_mod._cache_dir() == first
    monkeypatch.setenv("DECKLE_EXPORT_CACHE_DIR", second)
    assert export_mod._cache_dir() == second


def test_the_suite_does_not_share_the_machines_export_cache():
    """The point of the override, stated as the property the suite needs.

    Two tests in ``tests/test_hardening_limits.py`` diff a listing of this
    directory. Against the machine's real one they are diffing shared
    state: residue from an interrupted run on the same machine, and files
    the 512 MB eviction pass deletes between the two listings. Both fail,
    and neither failure is about the cleanup path being tested.
    """
    shared = os.path.join(tempfile.gettempdir(), export_mod._CACHE_DIR_NAME)

    assert os.path.realpath(export_mod._cache_dir()) != os.path.realpath(shared)


def test_without_the_override_it_is_still_the_shared_temp_directory(monkeypatch):
    """The product default is unchanged: a cache belongs in temp, and a
    user who never sets the variable gets exactly what they had."""
    monkeypatch.delenv("DECKLE_EXPORT_CACHE_DIR", raising=False)

    assert export_mod._cache_dir() == os.path.join(
        tempfile.gettempdir(), export_mod._CACHE_DIR_NAME
    )


def test_the_export_cache_has_a_budget_at_all():
    """The constant is the whole point -- an unbounded cache in temp was
    the defect. Pinned so a refactor cannot quietly drop it."""
    assert export_mod._CACHE_MAX_BYTES > 0


def test_clearing_the_cache_still_removes_its_files(tmp_path, monkeypatch):
    """``clear_sheet_cache`` is now called when a project is replaced, so
    it has to actually reclaim rather than only forget."""
    monkeypatch.setenv("DECKLE_EXPORT_CACHE_DIR", os.path.join(str(tmp_path), "cache"))
    export_mod.clear_sheet_cache()
    directory = export_mod._cache_dir()
    made = _fill(directory, 3, prefix="sheet")
    for index, path in enumerate(made):
        export_mod._cache.put((index, "planhash"), path)

    export_mod.clear_sheet_cache()

    assert all(not os.path.exists(p) for p in made)


# -- keeping the newest N, by modification time ---------------------------
#
# `evict_oldest_files` is a sibling of `evict_lru_files`, not a variant.
# The cache one budgets BYTES and ranks by ACCESS time, both of which are
# wrong for a recovery store: its budget is "how many offers is a person
# willing to read", and merely listing the offers at startup stats and
# reads every file, which would reorder an atime ranking and evict the
# ones the user was about to pick.


def _plant(directory, names, base_mtime=1_000_000):
    import os as _os

    made = []
    for index, name in enumerate(names):
        path = directory / name
        path.write_text("x" * 16, encoding="utf-8")
        _os.utime(path, (base_mtime + index, base_mtime + index))
        made.append(path)
    return made


def test_a_store_under_the_count_is_left_alone(tmp_path):
    from deckle.core.paths import evict_oldest_files

    planted = _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(3)])

    evict_oldest_files(tmp_path, keep=10, pattern="*.deckle.autosave")

    assert all(path.exists() for path in planted)


def test_the_oldest_by_modification_time_go_first(tmp_path):
    from deckle.core.paths import evict_oldest_files

    planted = _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(6)])

    evict_oldest_files(tmp_path, keep=2, pattern="*.deckle.autosave")

    assert [p.name for p in sorted(tmp_path.iterdir())] == [
        "4.deckle.autosave", "5.deckle.autosave",
    ]
    assert not planted[0].exists()


def test_reading_a_file_does_not_save_it_from_eviction(tmp_path):
    """The atime trap `evict_lru_files` would have walked into: the startup
    scan reads every offer, so ranking by access time would keep whichever
    the scan happened to touch last."""
    from deckle.core.paths import evict_oldest_files

    planted = _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(4)])
    planted[0].read_text(encoding="utf-8")  # the oldest, just read

    evict_oldest_files(tmp_path, keep=2, pattern="*.deckle.autosave")

    assert not planted[0].exists()


def test_the_pattern_confines_the_eviction(tmp_path):
    from deckle.core.paths import evict_oldest_files

    _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(5)])
    bystander = tmp_path / "notes.txt"
    bystander.write_text("keep me", encoding="utf-8")

    evict_oldest_files(tmp_path, keep=1, pattern="*.deckle.autosave")

    assert bystander.exists()
    assert len(list(tmp_path.glob("*.deckle.autosave"))) == 1


def test_keeping_none_deletes_everything_matching(tmp_path):
    from deckle.core.paths import evict_oldest_files

    _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(3)])

    evict_oldest_files(tmp_path, keep=0, pattern="*.deckle.autosave")

    assert list(tmp_path.glob("*.deckle.autosave")) == []


def test_a_missing_store_is_not_an_error(tmp_path):
    from deckle.core.paths import evict_oldest_files

    evict_oldest_files(tmp_path / "nope", keep=3)


def test_an_undeletable_offer_is_reported_and_the_rest_still_go(
    tmp_path, monkeypatch
):
    import os as _os

    from deckle.core.paths import evict_oldest_files

    planted = _plant(tmp_path, [f"{i}.deckle.autosave" for i in range(4)])
    real_remove = _os.remove
    stubborn = str(planted[0])

    def remove(path):
        if str(path) == stubborn:
            raise PermissionError(path)
        real_remove(path)

    monkeypatch.setattr(_os, "remove", remove)
    seen = []

    evict_oldest_files(
        tmp_path, keep=2, pattern="*.deckle.autosave",
        on_error=lambda event, exc, path: seen.append(event),
    )

    assert seen == ["autosave_eviction_failed"]
    assert planted[0].exists()
    assert not planted[1].exists()
