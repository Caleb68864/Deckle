"""B34: making a write atomic must not change who can read the file.

``mkstemp`` creates ``0600`` and the rename carries that onto the target, so a
group-readable project became owner-only on its next save.
"""

from __future__ import annotations

import os
import stat

import pytest

from deckle.core import paths
from deckle.core.paths import atomic_output, write_text_atomic

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


def _write_text(path):
    write_text_atomic(path, "new")


def _write_output(path):
    with atomic_output(path) as scratch:
        with open(scratch, "w", encoding="utf-8") as f:
            f.write("new")


WRITERS = pytest.mark.parametrize("write", [_write_text, _write_output])


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
@WRITERS
@pytest.mark.parametrize("mode", [0o640, 0o664, 0o600])
def test_an_existing_file_keeps_its_mode(tmp_path, write, mode):
    path = tmp_path / "book.pdf"
    path.write_text("old")
    os.chmod(path, mode)

    write(path)

    assert path.read_text() == "new"
    assert _mode(path) == mode


@posix_only
@WRITERS
def test_a_new_file_gets_what_the_umask_gives(tmp_path, write):
    previous = os.umask(0o022)
    try:
        write(tmp_path / "book.pdf")
    finally:
        os.umask(previous)
    assert _mode(tmp_path / "book.pdf") == 0o644


@posix_only
@WRITERS
def test_the_directory_entry_is_flushed_after_the_rename(tmp_path, monkeypatch, write):
    calls = []
    real_replace, real_fsync = os.replace, os.fsync
    monkeypatch.setattr(
        paths.os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1]
    )

    def _fsync(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        calls.append("dir" if is_dir else "file")
        return real_fsync(fd)

    monkeypatch.setattr(paths.os, "fsync", _fsync)

    write(tmp_path / "book.pdf")

    assert calls[-2:] == ["replace", "dir"]


@posix_only
@WRITERS
def test_a_failed_directory_flush_does_not_report_a_lost_save(tmp_path, monkeypatch, write):
    real_fsync = os.fsync

    def _fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("this filesystem cannot")
        return real_fsync(fd)

    monkeypatch.setattr(paths.os, "fsync", _fsync)
    write(tmp_path / "book.pdf")
    assert (tmp_path / "book.pdf").read_text() == "new"
