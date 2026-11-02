"""Every file the CLI names gets the same guarantee.

``export`` has always written a PDF to a scratch file beside the target
and renamed it into place -- its docstring says so twice, and the
verification step exists precisely so "the destination is never written
and any previous export there survives".

Two other commands write files the user names, and neither did that.
``schedule -o notes.txt`` opened the target with ``"w"``, which truncates
before a byte is written, and ``crop-preview -o overlay.png`` handed the
target straight to PIL. A full disk, a dropped share or a kill partway
through either one destroyed the previous file and left a fragment.

Nothing about which command produced a file should decide whether a
failed write costs the old one. That is the whole content of this file:
the same property, asserted for all three.

The fault is injected as a torn write -- open, truncate, write some
bytes, die -- because that is what a full disk actually does, and it is
the only model that tells a truncating writer apart from an atomic one.
Injecting at the serialiser proves nothing: it runs before the file is
opened. (Learned the hard way; see the 2026-08-07 entry on the config
stores, where seven tests passed against unfixed code for that reason.)
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys

import pytest

from deckle.cli import main

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "sample.pdf",
)


def _run(*args) -> int:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        return main(list(args))


def test_a_failed_schedule_write_keeps_the_previous_one(tmp_path, torn_write):
    out = os.path.join(str(tmp_path), "notes.txt")
    assert _run("schedule", FIXTURE, "-o", out) == 0
    first = open(out, encoding="utf-8").read()
    assert first

    with torn_write():
        _run("schedule", FIXTURE, "-o", out, "--fold-scheme", "folio")

    assert open(out, encoding="utf-8").read() == first


def test_a_failed_schedule_write_reports_rather_than_raising(tmp_path, torn_write):
    out = os.path.join(str(tmp_path), "notes.txt")

    with torn_write():
        code = _run("schedule", FIXTURE, "-o", out)

    assert code == 1


def test_a_failed_overlay_write_keeps_the_previous_one(tmp_path, torn_write):
    out = os.path.join(str(tmp_path), "overlay.png")
    assert _run("crop-preview", FIXTURE, "-o", out, "--auto-crop") == 0
    first = open(out, "rb").read()
    assert first

    with torn_write():
        _run("crop-preview", FIXTURE, "-o", out, "--auto-crop",
             "--auto-crop-margin", "6pt")

    assert open(out, "rb").read() == first


def test_neither_command_leaves_debris_beside_its_output(tmp_path, torn_write):
    """A scratch file stranded next to the user's own output, one per
    failure, in a folder they do look at."""
    schedule = os.path.join(str(tmp_path), "notes.txt")
    overlay = os.path.join(str(tmp_path), "overlay.png")
    _run("schedule", FIXTURE, "-o", schedule)
    _run("crop-preview", FIXTURE, "-o", overlay, "--auto-crop")

    with torn_write():
        _run("schedule", FIXTURE, "-o", schedule, "--fold-scheme", "folio")
    with torn_write():
        _run("crop-preview", FIXTURE, "-o", overlay, "--auto-crop")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt", "overlay.png"]


def test_the_overlay_still_encodes_by_its_extension(tmp_path):
    """The scratch file keeps the target's suffix on purpose: PIL infers
    the format from it, and a ``.tmp`` path cannot be encoded at all."""
    out = os.path.join(str(tmp_path), "overlay.png")

    assert _run("crop-preview", FIXTURE, "-o", out, "--auto-crop") == 0
    with open(out, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_an_export_already_had_this_guarantee(tmp_path, torn_write):
    """Pinned alongside, because it is the standard the other two were
    brought up to rather than a new behaviour."""
    out = os.path.join(str(tmp_path), "out.pdf")
    assert _run("export", FIXTURE, "-o", out) == 0
    first = open(out, "rb").read()

    with torn_write():
        _run("export", FIXTURE, "-o", out, "--fold-scheme", "folio")

    assert open(out, "rb").read() == first


def test_a_dummy_page_count_below_one_is_reported(tmp_path):
    """``make_numbered_pdf`` refuses a count below 1 with a message naming
    the number it got. That message was reaching the user wrapped in a
    traceback, because ``_cmd_dummy`` caught only ``OSError`` -- the same
    shape as the three settings ``_impose_or_report`` was written for.
    """
    out = os.path.join(str(tmp_path), "dummy.pdf")
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "dummy", "--pages", "0", "-o", out],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")
    assert "at least 1" in result.stderr


def test_a_failed_dummy_write_keeps_the_previous_one(tmp_path, torn_write):
    """The fifth file the CLI names, brought up to the same guarantee as
    the other four."""
    out = os.path.join(str(tmp_path), "dummy.pdf")
    assert _run("dummy", "--pages", "8", "-o", out) == 0
    first = open(out, "rb").read()

    with torn_write():
        _run("dummy", "--pages", "16", "-o", out)

    assert open(out, "rb").read() == first


_WRITE_UNDER_A_FILE_SIZE_LIMIT = """
import resource, signal, sys
from deckle.cli import main
limit = int(sys.argv[1])
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
sys.exit(main(sys.argv[2:]))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_FSIZE is POSIX")
@pytest.mark.parametrize("first,second", [
    pytest.param(("export", FIXTURE), ("export", FIXTURE, "--fold-scheme", "folio"),
                 id="export"),
    pytest.param(("dummy", "--pages", "8"), ("dummy", "--pages", "16"), id="dummy"),
])
def test_a_real_write_failure_keeps_the_previous_pdf(tmp_path, tmp_path_factory,
                                                    first, second):
    """The same property with nothing patched: the kernel refuses the bytes.

    ``torn_write`` has to know how each writer reaches the disk, and being
    wrong about pikepdf is what once made it miss. A file-size limit fails
    qpdf's own native ``write`` with ``EFBIG`` however it got there, so this
    cannot go stale in that way. Out of process, because the limit applies to
    everything the process writes and cannot be lowered and raised again by
    an unprivileged test. Its own session log for the same reason: the suite's
    shared one is already over the limit, and failing to append to it is noise
    logging prints to stderr, which is why the message is looked for rather
    than the absence of a traceback.
    """
    home = tmp_path_factory.mktemp("home")
    out = os.path.join(str(tmp_path), "out.pdf")
    assert _run(*first, "-o", out) == 0
    before = open(out, "rb").read()
    limit = len(before) // 2

    result = subprocess.run(
        [sys.executable, "-c", _WRITE_UNDER_A_FILE_SIZE_LIMIT, str(limit),
         *second, "-o", out],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             "XDG_DATA_HOME": str(home / "data"),
             "XDG_CONFIG_HOME": str(home / "config")},
    )

    assert result.returncode == 1, result.stderr
    assert "error: cannot write to" in result.stderr
    assert open(out, "rb").read() == before
    assert [p.name for p in tmp_path.iterdir()] == ["out.pdf"]
