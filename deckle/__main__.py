"""GUI entry point: ``python -m deckle`` launches ``MainWindow``.

``deckle/cli/`` remains the headless entry point (``python -m deckle.cli``
/ the ``deckle`` console script) -- two entry points, one Qt-free core. This
module is the only place that starts the Qt event loop for the desktop app.

``--help``, ``-h`` and ``--version`` are answered **here**, before
``QApplication`` is constructed. They used to reach Qt, which silently
ignores arguments it does not recognise, and then ``exec()``, which does
not return: a ``python -m deckle --help`` was found on the development
machine having run for 23 hours and 54 minutes, printing nothing. The
first thing anyone types at an unfamiliar command must not be the thing
that hangs their terminal.

Everything else is still passed through to ``QApplication`` untouched.
Qt owns its own command line -- ``-platform``, ``-style``,
``-stylesheet`` and the rest -- and a strict parser here would reject
arguments that work today for the sake of arguments nobody passes. The
three flags intercepted are the three that have an answer this side of
the event loop.
"""

from __future__ import annotations

import sys

#: The flags answered without starting Qt. Deliberately a small, closed
#: set rather than a parser: see the module docstring.
HELP_FLAGS = ("-h", "--help")
VERSION_FLAGS = ("--version",)

USAGE = """usage: python -m deckle [--help] [--version]

Deckle's desktop app. Launches the imposition and printing window; there
is nothing else to pass it, and it runs until you close the window.

The headless entry point is the one that takes arguments:

    python -m deckle.cli --help

It imposes, exports, schedules and plans print passes from a terminal,
with no display server, and it is what to use from a script or from CI.

  -h, --help     show this message and exit
  --version      show Deckle's version and its dependencies', and exit
"""


def _early_answer(argv: list[str]) -> str | None:
    """The text to print instead of launching, if any.

    :param argv: the process arguments, ``argv[0]`` included.
    :returns: what to print, or ``None`` to launch the app.
    """
    flags = argv[1:]
    if any(flag in HELP_FLAGS for flag in flags):
        return USAGE
    if any(flag in VERSION_FLAGS for flag in flags):
        # Imported here, not at module scope: `about` reads versions from
        # installed-distribution metadata rather than by importing the
        # packages, so asking Deckle its version never imports PySide6 --
        # and this path must not be the exception to that.
        from deckle.core import about

        return about.version_string()
    return None


def run(argv: list[str] | None = None) -> int:
    """Answer ``--help``/``--version``, or launch the desktop app.

    :param argv: the process arguments, ``argv[0]`` included, or ``None``
        to read ``sys.argv``.
    :returns: the process exit code.
    """
    args = list(sys.argv if argv is None else argv)
    answer = _early_answer(args)
    if answer is not None:
        print(answer)
        return 0
    from deckle.app.main import main

    return main(args)


if __name__ == "__main__":
    sys.exit(run())
