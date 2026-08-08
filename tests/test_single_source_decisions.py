"""Decisions that must exist in exactly one place.

Some rules in this codebase are not hard to get right individually --
which directory an OS keeps application data in, how to replace a file
without destroying the old one. They are hard to keep *consistent*, and
every copy is a chance for one to drift out of step with the others while
each still looks correct on its own.

The platform-directory ladder proved the point four times over. It was
extracted into ``paths`` on 2026-08-06 because two modules had it. A
third turned up in ``session_log``, and a fourth in ``diagnostics`` --
and the pass that consolidated the third *asserted there were only three*
on the strength of a grep that had silently missed one.

That is the argument for a structural test rather than another careful
sweep. A sweep is a claim about a moment; this is a claim that holds
until someone deliberately changes it, and a new copy fails it on the
commit that introduces it rather than a month later.

Both guards name the offending file and line, because the failure they
report is "you wrote something reasonable in the wrong place" and the
remedy is a one-line import.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "deckle")


def _package_lines():
    """Every (relative path, line number, code) in the package.

    Comments are stripped: this file's own subject is a *rule about where
    code lives*, and a comment explaining that rule must not be mistaken
    for a breach of it.
    """
    for folder, _, names in os.walk(PACKAGE):
        if "__pycache__" in folder:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, start=1):
                    yield os.path.relpath(path, ROOT), number, line.split("#", 1)[0]


def test_only_paths_decides_where_an_os_keeps_application_data():
    """``sys.platform`` branching belongs in ``deckle/core/paths.py``.

    Four modules once carried the same three-way ladder. None was wrong;
    the risk is a fifth appearing and answering differently, which would
    put a user's profiles somewhere their recent list is not.

    Platform checks that are not about *directories* -- a Windows-only
    API, a path-separator quirk -- are a different thing and are allowed
    to live where they are used. Those read from ``sys.platform`` too, so
    this test is narrowed to the ladder's shape: a comparison against a
    platform name.
    """
    offenders = [
        f"{path}:{number}"
        for path, number, code in _package_lines()
        if re.search(r"sys\.platform\s*==\s*[\"'](win32|darwin|linux)[\"']", code)
        and not path.replace("\\", "/").endswith("deckle/core/paths.py")
    ]

    assert offenders == [], (
        "platform-directory branching outside deckle/core/paths.py -- use "
        f"config_dir() or data_dir() instead: {offenders}"
    )


def test_only_paths_replaces_a_file_in_place():
    """``os.replace`` onto a target belongs behind ``paths``.

    Every store Deckle keeps is written by rename so a failure leaves the
    previous copy intact, and the guarantee is only as good as its least
    careful implementation. ``export`` is the documented exception: it
    builds a PDF through pikepdf rather than writing bytes it holds, so it
    does its own scratch-and-rename and says so in its docstring.
    """
    allowed = {"deckle/core/paths.py", "deckle/core/export.py"}
    offenders = [
        f"{path}:{number}"
        for path, number, code in _package_lines()
        if re.search(r"\bos\.replace\s*\(", code)
        and path.replace("\\", "/") not in allowed
    ]

    assert offenders == [], (
        "file replacement outside paths.py -- use write_text_atomic() or "
        f"atomic_output() instead: {offenders}"
    )


def test_the_guards_would_notice_a_breach():
    """The scanner must actually read the package.

    A guard that silently walked an empty tree would pass forever, which
    is the failure mode both tests above are written to prevent in the
    code they watch -- so it is worth ruling out here too.
    """
    lines = list(_package_lines())

    assert len(lines) > 5000, len(lines)
    assert any(
        path.replace("\\", "/") == "deckle/core/paths.py"
        and "sys.platform" in code
        for path, _, code in lines
    ), "the scanner never saw the one file that is allowed to branch"
