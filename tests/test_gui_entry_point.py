"""``python -m deckle --help`` must return.

This was found from live evidence rather than from reading: ``ps`` on the
development machine showed
``.venv/bin/python -m deckle --help`` with an elapsed time of **23 hours
54 minutes**, left behind by an earlier session. ``deckle/app/main.py``
handed ``argv`` straight to ``QApplication``, which silently ignores
arguments it does not recognise, and then called ``exec()``. There was no
argument parsing on that entry point at all -- so the single most
reflexive thing anyone does with a new command opened an invisible window
and blocked forever, printing nothing.

The asymmetry is the tell: ``python -m deckle.cli --help`` has always
worked, because ``cli/options.py`` builds a real parser. Only the GUI
entry point -- the one the README names first -- had nothing.

These tests shell out, because that is the only way to observe the
failure: it is not an exception, it is a process that does not end. Each
one runs under a timeout, and a timeout *is* the regression.

``QT_QPA_PLATFORM=offscreen`` is set deliberately. Without it a headless
box fails on the platform plugin, which is a different failure that would
mask this one; with it, a regression is the original 23-hour hang, cut
short by the timeout.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Generous enough to survive a loaded machine, short enough that a
# regression is a failed test rather than an abandoned run. The fixed
# path returns in well under a second: it never constructs QApplication.
TIMEOUT_S = 45


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        return subprocess.run(
            [sys.executable, "-m", "deckle", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`python -m deckle {' '.join(args)}` did not return within "
            f"{TIMEOUT_S}s -- it launched the GUI and blocked, which is "
            "the 23-hour process this test exists for"
        )


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_asking_for_help_prints_help_and_exits(flag):
    result = _run(flag)

    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower(), result.stdout


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_the_help_points_at_the_entry_point_that_takes_arguments(flag):
    """The GUI entry point has no flags to document, so the useful thing
    it can say is where the flags are. C4 is why someone is here: the
    ``deckle`` console script is named in a dozen documents, and
    ``python -m deckle`` is what a reader reaches for instead."""
    result = _run(flag)

    assert "python -m deckle.cli" in result.stdout, result.stdout


def test_version_is_answerable_without_a_display():
    """``--version`` is the other thing typed at an unfamiliar command,
    and the answer must not require an event loop -- a version is what a
    bug report needs from a machine where the GUI does not start.

    It must also be the *same* answer the CLI gives. The GUIDE tells
    anyone reporting a problem to send ``deckle --version``; two entry
    points that disagree about it turn one support request into two.
    """
    from deckle.core import about

    result = _run("--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == about.version_string().strip(), result.stdout


def test_help_does_not_construct_a_qapplication():
    """The interception has to happen *before* ``QApplication``, not
    inside it. Qt is what blocks, and a window that is never shown is
    still a window that keeps an event loop alive."""
    result = _run("--help")

    assert "PySide6.QtWidgets" not in result.stdout
    # Proven rather than asserted about the source: ask the child.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, runpy; sys.argv = ['deckle', '--help'];\n"
            "try:\n"
            "    runpy.run_module('deckle', run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('QT_LOADED', 'PySide6.QtWidgets' in sys.modules)",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    assert "QT_LOADED False" in probe.stdout, probe.stdout + probe.stderr
