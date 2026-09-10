"""``run.bat docs`` was documented as green and had been red for a while.

The `-W` in that command is the project's stated guard against docstring
drift: `README.md`, `docs/GUIDE.md` and `docs/api/conf.py` all say a
docstring that drifts from the code fails the build instead of quietly
rotting. It exits 1 at HEAD, and had, on both Sphinx 9.1 and 7.4 -- the
whole `sphinx>=7` range the `docs` extra allows.

**The gate was invisible twice over.** No CI job built the docs, and no
test covered it; and Sphinx was not installed in the developer's `.venv`
either, so `run.bat docs` took its "Sphinx not installed" branch and
printed that instead of the two errors. A guard nothing runs on a
toolchain nobody has is not a guard.

So both halves are pinned here. The first test builds the docs when
Sphinx is present. The second asserts that *something which is not this
test* runs the same build, so the `importorskip` above it cannot quietly
become the permanent state -- which is exactly how the gate went dark the
first time.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "api")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "test.yml")


def test_the_api_reference_builds_with_warnings_as_errors(tmp_path):
    """The command `run.bat docs` runs, run here.

    Deliberately the same flags, spelled the same way: a test that built
    without `-W` would pass on the tree that made this test necessary.
    """
    pytest.importorskip("sphinx", reason="the `docs` extra is not installed")

    env = dict(os.environ)
    # autodoc imports every module under `deckle/`. None of them touches
    # Qt at import time -- that is what makes this build runnable
    # headlessly -- but a module that started to should fail on the
    # import, not on a missing display.
    env["QT_QPA_PLATFORM"] = "offscreen"

    result = subprocess.run(
        [
            sys.executable, "-m", "sphinx", "-b", "html", "-W", "--keep-going",
            DOCS, str(tmp_path / "html"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    # The build really ran, so a green result below is a build and not a
    # command that failed to start.
    assert "building [html]" in result.stdout, result.stdout + result.stderr

    problems = [
        line for line in (result.stdout + result.stderr).splitlines()
        if ": WARNING:" in line or ": ERROR:" in line
    ]
    assert result.returncode == 0, "\n".join(problems) or result.stderr


def test_something_other_than_this_test_runs_the_docs_build():
    """The `importorskip` above must not become the permanent answer.

    Sphinx is in the `docs` extra and the suite installs `.[dev]`, so on
    every machine that has not installed it by hand -- CI included --
    the test above skips. A skipped guard is the state this file exists
    to end, so the runner is asserted separately.

    Parsed as text rather than with a YAML library, the way
    `tests/test_ci_workflow.py` does and for the same reason: PyYAML is
    not a declared dependency.
    """
    with open(WORKFLOW, encoding="utf-8") as handle:
        workflow = handle.read()

    assert re.search(r"^  docs:", workflow, re.MULTILINE), (
        "no `docs` job in .github/workflows/test.yml -- nothing builds the "
        "API reference, so `-W` guards nothing"
    )
    assert '.[docs]' in workflow, (
        "the docs job must install the `docs` extra, or Sphinx is absent "
        "there too and the build prints 'Sphinx not installed'"
    )
    assert re.search(r"sphinx -b html -W --keep-going", workflow), (
        "the docs job must run the same command run.bat docs runs, `-W` "
        "included; without it the job is green on a drifted docstring"
    )
