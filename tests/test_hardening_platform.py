"""Cross-platform hardening (pass 9).

Windows is the awkward platform here and most of this file is about it:
legacy console encodings, drive letters, reserved filenames, and a data
directory whose resolution is duplicated across three modules.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


# -- console encoding ----------------------------------------------------


def test_a_non_ascii_output_path_does_not_crash_the_cli(tmp_path):
    """The bug this pass found: success, then a traceback.

    The Windows console is cp1252 by default and cannot encode most
    non-ASCII characters. ``print(f"wrote {path}")`` therefore raised
    ``UnicodeEncodeError`` *after* the PDF had been written correctly --
    a traceback and a non-zero exit for a job that succeeded. Accented
    characters in a person's name are enough to hit it.
    """
    out = tmp_path / "booklet-日本語-café.pdf"

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", FIXTURE, "-o", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert out.exists(), "the PDF itself must be written under its real name"


def test_output_encoding_helper_never_raises_on_an_odd_stream(monkeypatch):
    """Hardening must not become the thing that breaks startup."""
    from deckle import cli

    class Hostile(io.StringIO):
        def reconfigure(self, **kwargs):
            raise OSError("this stream refuses to be reconfigured")

    monkeypatch.setattr(cli.sys, "stdout", Hostile())
    monkeypatch.setattr(cli.sys, "stderr", Hostile())

    cli._make_output_encoding_safe()  # must not raise


def test_output_encoding_helper_tolerates_a_stream_without_reconfigure(monkeypatch):
    """A plain redirected stream has no ``reconfigure`` at all."""
    from deckle import cli

    class Plain:
        pass

    monkeypatch.setattr(cli.sys, "stdout", Plain())
    monkeypatch.setattr(cli.sys, "stderr", Plain())

    cli._make_output_encoding_safe()  # must not raise


# -- data directory resolution ------------------------------------------


def test_the_three_data_dir_resolvers_agree_on_the_base_directory():
    """``diagnostics``, ``session_log`` and ``profiles`` each resolve the OS
    data directory independently, with near-identical copied logic.

    ``profiles`` is a frozen module, so they cannot be unified. This test is
    the substitute: it fails if any copy drifts from the others, which is the
    realistic failure -- someone fixes a Windows path bug in one of three
    places and the other two keep the bug.
    """
    from deckle.core import diagnostics, profiles, session_log

    diag = diagnostics.data_dir()
    log = session_log._data_dir()
    prof = profiles._profiles_dir() if hasattr(profiles, "_profiles_dir") else None

    assert diag == log, (
        f"diagnostics and session_log disagree: {diag} vs {log}. "
        "Their data-dir logic is duplicated; one copy has drifted."
    )

    if prof is not None:
        # profiles deliberately nests under the same root (and on Linux uses
        # XDG_CONFIG_HOME rather than XDG_DATA_HOME, since a calibration
        # profile is configuration). Assert only the relationship that must
        # hold on this platform.
        if sys.platform == "win32":
            assert prof.parent == diag, (
                f"profiles dir {prof} is not under the shared data dir {diag}"
            )


def test_data_dir_honours_its_environment_override(tmp_path, monkeypatch):
    from deckle.core import diagnostics

    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path / "custom"))
    assert diagnostics.data_dir() == tmp_path / "custom"


def test_data_dir_is_absolute_on_this_platform():
    """A relative data dir would resolve against the working directory,
    putting logs wherever the user happened to launch Deckle from."""
    from deckle.core import diagnostics, session_log

    assert diagnostics.data_dir().is_absolute()
    assert session_log._data_dir().is_absolute()


# -- temp directory -----------------------------------------------------


def test_temp_dirs_come_from_gettempdir_not_a_raw_env_var():
    """``$TMPDIR`` is EMPTY in Git Bash on Windows.

    Reading it directly resolves to ``/sig.pdf`` -- the unwritable MSYS root
    -- and *hangs* rather than failing. That trap cost this project a
    deferred sub-spec (SS-12), whose work sat uncommitted because its own
    verification command interpolated ``$TMPDIR``. ``tempfile.gettempdir()``
    is the portable answer and is what the code must keep using.
    """
    import tempfile

    from deckle.core import export, loader, print_session

    for module in (export, loader, print_session):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert 'environ.get("TMPDIR")' not in source, (
            f"{module.__name__} reads $TMPDIR directly; use tempfile.gettempdir()"
        )
        assert 'environ["TMPDIR"]' not in source, (
            f"{module.__name__} reads $TMPDIR directly; use tempfile.gettempdir()"
        )

    assert Path(tempfile.gettempdir()).is_absolute()


# -- windows path shapes -------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_a_path_with_an_illegal_character_fails_with_a_message_not_a_traceback(tmp_path):
    """``:`` is legal on POSIX and illegal in a Windows filename."""
    out = tmp_path / "what:ever.pdf"

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", FIXTURE, "-o", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")
    assert result.stdout == "", "stdout stays clean on failure"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters")
def test_an_unavailable_drive_letter_is_named(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", FIXTURE, "-o", r"Q:\out.pdf"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Q:" in result.stderr
