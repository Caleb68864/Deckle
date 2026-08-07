"""Where Deckle keeps the state that outlives a project.

Printer profiles and the recent-projects list both live under the OS
config directory, and both need the same three-way answer about where
that is. This module is the one place that decides, so the two cannot
drift apart and leave a user's profiles somewhere their recent list is
not.

Read from the environment at call time rather than at import, so tests
can point the whole thing at a temporary directory.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import os
import sys
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
