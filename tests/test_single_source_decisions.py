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
import subprocess
import sys

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
    careful implementation. ``export`` was the documented exception until
    it stopped being one: it builds a PDF through pikepdf rather than
    writing bytes it holds, which is precisely the case ``atomic_output``
    yields a path for.
    """
    allowed = {"deckle/core/paths.py"}
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


def test_importing_a_pdf_does_not_import_the_exporter():
    """``loader`` reached the pdfium lock through the rasteriser.

    ``from deckle.core.render import pdfium_guard`` was one function's
    worth of need that pulled in ``deckle.core.export`` -- and with it
    pikepdf, the printing module and the diagnostics log -- because
    ``render`` imports the exporter. Importing a PDF does not depend on
    rendering or exporting one, and an import graph that says it does is
    read as though it were true. The lock now lives in
    ``deckle.core.pdfium_lock``, which imports ``threading`` and nothing
    else.

    Run in a **subprocess**. By the time pytest reaches this file some
    earlier test has certainly imported ``export`` already, and an
    in-process ``sys.modules`` check would assert nothing -- the mistake
    ``tests/test_core_purity.py`` records having made and fixed in itself.
    """
    probe = (
        "import sys, deckle.core.loader;"
        "print(sorted(m for m in ('deckle.core.export', 'deckle.core.render')"
        " if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        "importing deckle.core.loader dragged in the rasteriser/exporter: "
        f"{result.stdout.strip()}"
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
