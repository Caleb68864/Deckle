"""Where Deckle keeps the state that outlives a project, and how it is written.

Printer profiles and the recent-projects list both live under the OS
config directory, and both need the same three-way answer about where
that is. This module is the one place that decides, so the two cannot
drift apart and leave a user's profiles somewhere their recent list is
not.

It also owns *how* that state reaches disk. Every store Deckle keeps --
project files, profiles, the recent list -- is a small JSON document
rewritten whole, and the obvious way to do that destroys the previous
generation before writing the next. :func:`write_text_atomic` is the one
implementation, for the same reason ``config_dir`` is: three copies of a
durability decision is three chances to get one of them wrong.

Read from the environment at call time rather than at import, so tests
can point the whole thing at a temporary directory.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def config_dir(*parts: str) -> Path:
    """The OS-appropriate config directory, plus any sub-path given.

    Windows uses ``%APPDATA%\\Deckle``, macOS
    ``~/Library/Application Support/Deckle``, and everything else honours
    ``XDG_CONFIG_HOME`` and falls back to ``~/.config/deckle``. The
    lowercase name on Linux and the capitalised one elsewhere are each
    that platform's convention, not an inconsistency.

    :param parts: sub-directories or a filename below the config root.
    :returns: the path. Nothing is created -- callers that write make
        their own parents, and callers that only read must tolerate the
        directory not existing yet.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        root = Path(base) / "Deckle"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Deckle"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        root = Path(base) / "deckle"
    return root.joinpath(*parts)


def write_text_atomic(path: str | os.PathLike[str], text: str, *,
                      encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` so a failure leaves the old file intact.

    Writes a temporary file beside the target, flushes it to the platter,
    then renames it over the target. ``os.replace`` is atomic on POSIX and
    on Windows, so a reader racing the writer sees the old document or the
    new one and never a prefix of either -- and a write that dies partway
    leaves the previous generation exactly as it was.

    The temp file goes in the *target's own directory* rather than the
    system temp dir, because a rename across filesystems is not atomic and
    on most platforms not even permitted. It is named with a leading dot
    and the target's own name, so debris from a hard kill is at least
    identifiable as Deckle's.

    :param path: the file to end up with.
    :param text: its complete new content. This is a whole-document write;
        there is no append form, because every store that uses it is a
        small JSON document rewritten whole.
    :param encoding: the text encoding. Newline translation is left at the
        platform default, matching what these files have always contained.
    :returns: nothing.
    :raises OSError: the directory does not exist, is not writable, or the
        write or rename fails. The temporary file is removed first, so a
        caller that retries does not accumulate debris.
    """
    target = Path(path)
    directory = target.parent if str(target.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            # Ordering, not just durability: without this the rename can
            # reach the platter ahead of the content it is renaming, and a
            # crash leaves the target pointing at a file of zeros.
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # Best-effort: the write already failed, and failing to clean up
        # after it must not replace the error that explains why.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
