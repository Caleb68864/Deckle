"""Where Deckle keeps the state that outlives a project, and how it is written.

Printer profiles, the recent-projects list and the session log all live
under an OS-appropriate directory, and all three need the same three-way
platform answer about where that is. This module is the one place that
decides, so they cannot drift apart and leave a user's profiles somewhere
their recent list is not.

Two roots, not one: :func:`config_dir` for settings and :func:`data_dir`
for what the application accumulates. On Windows and macOS they are the
same directory; on Linux XDG separates them, and the session log belongs
on the data side. The platform ladder is written once even so -- the
session log used to carry its own copy, which is the third copy of a
decision this module exists to hold.

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

import contextlib
import os
import sys
import tempfile
from pathlib import Path


def _root(xdg_variable: str, xdg_fallback: Path) -> Path:
    """The platform's application directory, given the Linux answer.

    Written once for both roots. Windows and macOS put settings and data
    in the same place, so the only thing that varies between
    :func:`config_dir` and :func:`data_dir` is which XDG variable Linux
    consults -- and duplicating the whole three-way ladder to express that
    one difference is what this module was extracted to stop.

    :param xdg_variable: the environment variable Linux should honour.
    :param xdg_fallback: where Linux goes when it is unset.
    :returns: the application's root directory on this platform.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Deckle"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Deckle"
    base = os.environ.get(xdg_variable) or str(xdg_fallback)
    # Lowercase on Linux and capitalised elsewhere: each is that
    # platform's convention, not an inconsistency.
    return Path(base) / "deckle"


def config_dir(*parts: str) -> Path:
    """The OS-appropriate config directory, plus any sub-path given.

    Windows uses ``%APPDATA%\\Deckle``, macOS
    ``~/Library/Application Support/Deckle``, and everything else honours
    ``XDG_CONFIG_HOME`` and falls back to ``~/.config/deckle``.

    :param parts: sub-directories or a filename below the config root.
    :returns: the path. Nothing is created -- callers that write make
        their own parents, and callers that only read must tolerate the
        directory not existing yet.
    """
    return _root("XDG_CONFIG_HOME", Path.home() / ".config").joinpath(*parts)


def data_dir(*parts: str) -> Path:
    """The OS-appropriate *data* directory, plus any sub-path given.

    Distinct from :func:`config_dir` on one platform only. Windows and
    macOS keep both under the same root, so the two answers are identical
    there. Linux is where the split is real: XDG separates settings a user
    might edit or copy between machines (``XDG_CONFIG_HOME``) from data an
    application accumulates (``XDG_DATA_HOME``), and the session log is
    squarely the second kind.

    A second function rather than an argument to the first, because the
    answer genuinely differs and a caller has to say which it wants --
    "profiles" versus "log" is not a judgement this module can make.

    :param parts: sub-directories or a filename below the data root.
    :returns: the path. Nothing is created -- callers that write make
        their own parents.
    """
    return _root("XDG_DATA_HOME", Path.home() / ".local" / "share").joinpath(*parts)


@contextlib.contextmanager
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

    **This makes a write safe against failure, not against a second
    writer.** ``os.replace`` is ``MoveFileEx`` on Windows, which fails with
    ``PermissionError: [WinError 5]`` when another handle holds the target
    -- so two callers renaming onto one path at the same moment do not
    quietly pick a winner, they make one of them raise. The file is never
    corrupted either way, which is what this function promises; but a
    caller with more than one writer must serialise them itself, as
    :class:`deckle.app.state.AppState` does for autosave, where the
    debounce timer and ``flush_autosave`` both write during shutdown.

    :param path: the file to end up with.
    :param text: its complete new content. This is a whole-document write;
        there is no append form, because every store that uses it is a
        small JSON document rewritten whole.
    :param encoding: the text encoding. Newline translation is left at the
        platform default, matching what these files have always contained.
    :returns: nothing.
    :raises OSError: the directory does not exist, is not writable, or the
        write or rename fails -- including a concurrent rename onto the
        same target on Windows. The temporary file is removed first, so a
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
