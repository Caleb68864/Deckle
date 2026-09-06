"""What is in the repository, as opposed to what it does.

A file whose entire name is ``=`` sat tracked at the root from commit
b549d95 until this one. It came from a shell redirect -- ``>=`` is ``>``
followed by ``=``, and the commit that introduced it is the one whose
message says an assertion "asserted only '>= 0.0'". Nothing failed,
because nothing was looking, and a zero-byte file with an unreadable
name is exactly the kind of thing every subsequent reader leaves alone
in case it matters.

The rule is on the *shape* of names rather than on that one name, because
the next redirect will land in a file called ``2`` or ``>``. No space and
no non-ASCII either: this tree is cloned onto Windows, Linux and macOS,
bundled by PyInstaller, and walked by ``os.walk`` in three separate tests,
and ``tests/test_packaging_audit.py`` already records what one invisible
character in a path cost this project.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SAFE_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")


def test_no_tracked_path_needs_shell_quoting():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )

    offenders = sorted(
        {
            path
            for path in result.stdout.split("\0")
            if path
            for part in path.split("/")
            if not SAFE_COMPONENT.fullmatch(part)
        }
    )

    assert not offenders, (
        f"tracked paths that need shell quoting: {offenders}. A file like "
        'this usually came from a shell redirect (">=" is ">" then "="); '
        "delete it rather than ignoring it."
    )
