"""What Deckle says about itself, in one place.

The GUIDE tells anyone reporting a problem to send two things: the output
of ``deckle --version`` and the JSON Lines diagnostic log. The CLI could
produce the first and neither front end could point at the second, so a
desktop user following the instructions had to be told, by hand, where
their operating system keeps application data.

Both answers live here so the CLI's ``--version`` and the app's About box
cannot drift apart -- they are the same sentences, and a support request
built from one has to match a support request built from the other.

**Nothing here reaches the network.** There is no update check, no
telemetry and no "latest version" to compare against; Deckle is a program
for a room with a printer in it, and it works the same on a machine that
has never been online. The About box says so out loud, because a user who
has been trained by other software to expect a silent update check is
entitled to know this one does not make it.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
Versions are read from installed-distribution metadata rather than by
importing the packages, so asking Deckle its version never imports PySide6.
"""

from __future__ import annotations

import importlib.metadata
import platform
import sys

from deckle import __version__

#: The dependencies worth naming in a bug report: the two PDF engines, the
#: image path, and the GUI toolkit. A version mismatch in any of them
#: changes what the paper looks like.
VERSIONED_DISTRIBUTIONS = ("pikepdf", "pypdfium2", "img2pdf", "PySide6")

NOT_INSTALLED = "not installed"
"""What a distribution that is absent reports.

Absent is a legitimate answer, not an error: the CLI runs on machines with
no display libraries at all, so ``PySide6 not installed`` is a fact about a
working installation and belongs in the report.
"""


def distribution_version(dist_name: str) -> str:
    """The installed version of ``dist_name``.

    :param dist_name: the distribution (not import) name.
    :returns: the version, or :data:`NOT_INSTALLED`.
    """
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return NOT_INSTALLED


def version_lines() -> list[str]:
    """Deckle's version and its dependencies', one per line.

    :returns: the lines, Deckle first.
    """
    return [f"deckle {__version__}"] + [
        f"{dist} {distribution_version(dist)}" for dist in VERSIONED_DISTRIBUTIONS
    ]


def version_string() -> str:
    """What ``deckle --version`` prints.

    :returns: :func:`version_lines` joined by newlines.
    """
    return "\n".join(version_lines())


def platform_line() -> str:
    """The host, in the one line a bug report needs it in.

    :returns: e.g. ``"Linux-6.18.0 CPython 3.14.0"``. Not part of
        ``--version``, which is a stable contract the GUIDE quotes; the
        About box has room for it and the question "which OS?" is the
        second one every report needs answered.
    """
    return (
        f"{platform.system() or 'unknown'}-{platform.release() or 'unknown'} "
        f"{platform.python_implementation()} {platform.python_version()}"
    )


def about_lines(log_path: str) -> list[str]:
    """The About box, as lines.

    :param log_path: where the diagnostic log lives on this machine --
        passed in rather than resolved here so the caller decides whether
        the directory should be created, and so this stays a function of
        its arguments.
    :returns: the lines to show, in order.
    """
    return [
        "Deckle imposes PDFs for hand bookbinding and drives a printer "
        "that cannot duplex on its own.",
        "",
        *version_lines(),
        platform_line(),
        "",
        "Diagnostic log:",
        log_path,
        "",
        "Reporting a problem: send the version lines above and the "
        "diagnostic log file. Use Help > Open diagnostics folder to find it.",
        "",
        "Deckle works entirely offline. It never contacts the network, "
        "checks for updates, or reports anything about your documents.",
    ]


def about_text(log_path: str) -> str:
    """The About box as one block of text.

    :param log_path: where the diagnostic log lives.
    :returns: :func:`about_lines` joined by newlines.
    """
    return "\n".join(about_lines(log_path))
