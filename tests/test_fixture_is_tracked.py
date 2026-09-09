"""The sample fixture is tracked, not merely present.

A fresh clone got 79 failures and 25 fixture-setup errors because
``tests/fixtures/sample.pdf`` was matched by ``tests/fixtures/*.pdf`` in
``.gitignore`` and had never been committed, while
``tests/fixtures/README.md`` described it as checked in. Every existing
test that uses the fixture passes on a machine that happens to have one,
so none of them could catch this; the thing to assert is what ``git``
knows, not what the filesystem knows.
"""

from __future__ import annotations

import os
import subprocess

import pikepdf
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")

LETTER_MEDIABOX = (0.0, 0.0, 612.0, 792.0)


def _requires_git_checkout() -> None:
    """Skip in a source tarball, but never because ``git`` is missing.

    A CI image without ``git`` is a CI bug worth seeing as an error rather
    than hiding as a skip.

    ``exists``, not ``isdir``: in a **git worktree** ``.git`` is a *file*
    holding a ``gitdir:`` pointer, not a directory. The directory test read
    that as "not a git checkout" and skipped all three of these tests --
    silently, and in exactly the setup used to develop features in parallel,
    which is when a fixture is most likely to go missing. A test that cannot
    fail is worse than no test, and this one guards the file ~100 other tests
    depend on (R0.1).
    """
    if not os.path.exists(os.path.join(REPO_ROOT, ".git")):
        pytest.skip("not a git checkout")


def test_the_sample_fixture_is_tracked_by_git():
    _requires_git_checkout()

    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "tests/fixtures/sample.pdf"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "tests/fixtures/sample.pdf is not tracked by git, so a fresh clone "
        f"cannot run the suite. git said: {result.stderr.strip()!r}"
    )


def test_the_sample_fixture_is_not_ignored():
    _requires_git_checkout()

    result = subprocess.run(
        ["git", "check-ignore", "-v", "tests/fixtures/sample.pdf"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    # `check-ignore -q` exits 1 when the path is NOT ignored, which is the
    # state this test wants. `-v` is used here so the failure message names
    # the offending .gitignore line.
    quiet = subprocess.run(
        ["git", "check-ignore", "-q", "tests/fixtures/sample.pdf"],
        cwd=REPO_ROOT,
    )

    assert quiet.returncode == 1, (
        "tests/fixtures/sample.pdf is ignored, so it will not survive a "
        f"clone. Matching rule: {result.stdout.strip()!r}"
    )


def test_the_sample_fixture_is_two_letter_pages():
    """Pins the shape, so a future regeneration at A4 or 4 pages is caught.

    Several tests assert a page count of 2 (``test_import_view``,
    ``test_project_cli``, ``test_gui_workflow``) and the golden export
    tests measure Letter geometry, so both properties are load-bearing.
    """
    with pikepdf.open(FIXTURE) as pdf:
        assert len(pdf.pages) == 2

        for index, page in enumerate(pdf.pages):
            box = tuple(float(value) for value in page.mediabox)
            assert box == pytest.approx(LETTER_MEDIABOX, abs=1e-6), (
                f"page {index} is {box}, not US Letter"
            )
