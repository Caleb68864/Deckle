"""The recent-projects list.

Opening a project used to start from nowhere: the file dialog was given an
empty start directory, so every open began wherever the OS thought best
rather than at the folder the last few projects came from.

Persisted as JSON beside the printer profiles, most recent first. Pure
bookkeeping -- no Qt, and nothing here can fail in a way that stops a
project opening.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from deckle.core.diagnostics import log_exception
from deckle.core.paths import config_dir, write_text_atomic

MAX_ENTRIES = 10
"""How many projects to remember.

Long enough to cover the jobs someone is actually moving between, short
enough that the list stays readable without scrolling.
"""

_STORE_NAME = "recent_projects.json"


def _store_path() -> Path:
    return config_dir(_STORE_NAME)


def _normalise(path: str) -> str:
    """An absolute, normalised path, so one file is one entry.

    The same project reached as ``./job.deckle`` and by absolute path is
    the same project, and a list that showed it twice would be reporting
    on how it was typed rather than on what was opened.
    """
    return os.path.normpath(os.path.abspath(path))


def load() -> list[str]:
    """Every remembered project, most recent first.

    Includes paths that no longer resolve -- see :func:`existing` for why
    that is deliberate.

    :returns: the paths. An unreadable or malformed store reads as empty:
        this is a convenience list, and losing it is not worth a
        traceback in front of someone trying to open a file.
    """
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, str)]


def _write(entries: list[str]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, so a write that dies partway leaves yesterday's list rather
    # than a truncated file. `load` reads a malformed store as empty by
    # design, which means a torn write would not fail loudly -- it would
    # silently erase the history, which is the one thing `existing` and
    # `forget` are carefully arranged not to do.
    write_text_atomic(path, json.dumps(entries, indent=2))


def record(path: str) -> None:
    """Remember ``path`` as the most recently used project.

    Re-recording something already known moves it to the front rather than
    adding a second entry.

    :param path: the project file. Normalised before storing.
    :returns: nothing, and never raises. This is bookkeeping that happens
        alongside opening a project, and a config directory that cannot be
        written must not be what stops the project opening.
    """
    normalised = _normalise(path)
    entries = [entry for entry in load() if _normalise(entry) != normalised]
    entries.insert(0, normalised)
    try:
        _write(entries[:MAX_ENTRIES])
    except OSError as exc:
        log_exception("recent_projects_write_failed", exc, path=str(_store_path()))


def forget(path: str) -> None:
    """Drop ``path`` from the list.

    The only thing that removes an entry -- and **nothing calls it
    today**, so in practice the store only ever grows until
    :data:`MAX_ENTRIES` pushes the oldest off the end. Said plainly
    because "the only thing that removes an entry" reads as a live
    description of a mechanism in use, and it is a description of a
    policy instead: :func:`existing` filters for display and never
    prunes, for the reason its own docstring gives, so *something* has
    to be the deletion and this is it.

    The caller it is waiting for is a deliberate user action -- a
    "Remove from this list" beside an entry in the Recent menu. That
    control does not exist, and this function is deliberately not wired
    to anything else in the meantime: the two automatic callers that
    suggest themselves, pruning on a failed open and pruning on a
    listing, are both the behaviour :func:`existing` was written to
    prevent. A project on an unplugged drive would be erased by either.

    :param path: the project to forget.
    :returns: nothing, and never raises.
    """
    normalised = _normalise(path)
    entries = [entry for entry in load() if _normalise(entry) != normalised]
    try:
        _write(entries)
    except OSError as exc:
        log_exception("recent_projects_write_failed", exc, path=str(_store_path()))


def existing() -> list[str]:
    """The remembered projects that can currently be found, most recent first.

    Filtering rather than pruning is the point. A path on a disconnected
    network share or an unplugged USB drive is temporarily unreachable,
    not retired -- and a store that deleted those entries would quietly
    erase real history the first time a drive was unmounted, with no way
    to get it back. Hide it now, show it again when the drive returns.

    :returns: the reachable paths.
    """
    return [entry for entry in load() if os.path.isfile(entry)]


def last_directory() -> str:
    """The folder to open a file dialog in, or ``""`` if there is none.

    Taken from the most recent entry whose directory still exists, so an
    unmounted drive at the top of the list does not send the dialog
    somewhere that cannot be browsed.

    :returns: a directory path, or ``""`` to let the dialog choose.
    """
    for entry in load():
        directory = os.path.dirname(entry)
        if os.path.isdir(directory):
            return directory
    return ""
