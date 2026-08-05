"""Why an output path cannot be written, phrased for the person writing it.

Deckle has two front ends and they used to explain failure very differently.
The CLI named the file, the cause, and the remedy; the desktop app printed
whatever ``str(exc)`` happened to say -- typically ``[WinError 5] Access is
denied`` naming a temp file the user has never heard of. Same failure, same
user, two different qualities of answer.

The knowledge here is about *output paths and bookbinding output*, not about
argparse or about Qt, so it belongs in the core where both front ends can
reach it. This module is Qt-free and prints nothing: it returns strings and
lets each front end decide where they go -- stderr for the CLI, a status bar
for the app.

Every message follows the same three-part shape, because a message that
stops short of the third part leaves the user stuck:

1. what failed,
2. which path,
3. what to do about it.
"""

from __future__ import annotations

import os


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file, tolerating an absent target.

    :param a: one path.
    :param b: the other.
    :returns: ``True`` if both name the same file on disk.

    ``os.path.samefile`` is the only check that sees through symlinks,
    junctions, and ``..`` segments -- string comparison does not -- but it
    raises if either path is missing, which is the normal case for an output
    that has not been written yet. Falling back to comparing absolute paths
    keeps the common case working without losing the hard case.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def output_path_problem(out_path: str, source: str | None = None) -> str | None:
    """Why ``out_path`` cannot be written to, or ``None`` if it looks fine.

    :param out_path: the destination the user asked for.
    :param source: the document being imposed, when known. Passing it
        catches the case where the output *is* the source.
    :returns: a complete, user-facing sentence, or ``None``.

    Checked *before* any imposition work, so a mistyped destination costs no
    time and the message names the path the user typed rather than the temp
    file the exporter was about to rename into place.

    This cannot be exhaustive -- a file locked by another process passes
    every check here and still fails at the final rename -- so callers must
    also handle ``OSError`` from the write itself, via
    :func:`describe_write_failure`.
    """
    if source is not None and same_file(out_path, source):
        return (
            f"cannot write to {out_path}: that is the file being imposed. "
            "Exporting onto the source would destroy the original. Choose a "
            "different output name."
        )

    if os.path.isdir(out_path):
        return (
            f"cannot write to {out_path}: that is an existing folder, not a "
            "file. Give a file name instead, e.g. "
            f"{os.path.join(out_path, 'booklet.pdf')}."
        )

    directory = os.path.dirname(os.path.abspath(out_path)) or "."
    if not os.path.isdir(directory):
        anchor = os.path.splitdrive(os.path.abspath(out_path))[0]
        if anchor and not os.path.exists(anchor + os.sep):
            return (
                f"cannot write to {out_path}: the drive {anchor} does not "
                "exist or is not connected. Check the drive letter, or "
                "choose a folder on a drive that is available."
            )
        return (
            f"cannot write to {out_path}: the folder {directory} does not "
            "exist. Create it first, or choose a folder that does."
        )
    if not os.access(directory, os.W_OK):
        return (
            f"cannot write to {out_path}: the folder {directory} is not "
            "writable. Choose another location, or grant yourself write "
            "permission on that folder."
        )
    if os.path.exists(out_path) and not os.access(out_path, os.W_OK):
        return (
            f"cannot write to {out_path}: the file is read-only. Clear its "
            "read-only flag, or choose a different output file."
        )
    return None


def describe_write_failure(out_path: str, exc: OSError) -> str:
    """Explain an ``OSError`` raised while actually writing ``out_path``.

    :param out_path: the destination that failed.
    :param exc: the error raised by the write.
    :returns: a complete, user-facing sentence.

    The overwhelmingly common case on Windows is that the previous export is
    still open in a PDF viewer, which holds the file and makes the exporter's
    final rename fail with a bare ``[WinError 5] Access is denied`` naming a
    scratch file the user has never seen. Guessing that cause is worth it
    because it is nearly always right and is trivially checkable by the user;
    the message asks rather than asserts.
    """
    if isinstance(exc, PermissionError):
        detail = (
            "permission denied -- is the file already open in a PDF viewer? "
            "Close it and try again, or export to a different name."
        )
    else:
        detail = f"{exc.strerror or exc}. Check the path, the drive, and free disk space."
    return f"cannot write to {out_path}: {detail}"
