"""Every command that writes a file behaves the same way about writing it.

Five subcommands take ``-o``: ``export``, ``impose``, ``schedule``,
``crop-preview`` and ``dummy``. Each was built correctly on its own, and
by the time anyone compared them they had drifted into three different
combinations of the same three properties:

- ``export`` validated the path, reported failures, and wrote to a
  scratch file it renamed into place.
- ``schedule`` and ``crop-preview`` validated and reported, and truncated
  their target before writing.
- ``dummy`` validated, wrote straight to its target, and let a
  ``ValueError`` out as a traceback.

None of that is visible from inside any one command. A test per command
asserts what that command does; only a test *across* them asserts they
agree, which is the property a user actually relies on -- nothing about
which subcommand they typed should change whether a full disk costs them
the file that was already there.

Parametrised over the commands rather than written five times, so a sixth
inherits the checks by being added to one list.
"""

from __future__ import annotations

import contextlib
import io
import os

import pytest

from deckle.cli import main

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "tests", "fixtures", "sample.pdf")


@pytest.fixture
def torn_write():
    """The next file written under this context dies halfway through.

    Patches both ``io.open`` and ``builtins.open``: they are two names for
    the same behaviour and code reaches them differently, so arming one
    leaves the other live.
    """
    import builtins

    real_io, real_builtin = io.open, builtins.open

    @contextlib.contextmanager
    def armed():
        def wrap(real):
            def guarded(file, mode="r", *args, **kwargs):
                handle = real(file, mode, *args, **kwargs)
                if "w" in mode:
                    write = handle.write

                    def half_then_die(data):
                        write(data[: len(data) // 2])
                        handle.flush()
                        raise OSError(28, "No space left on device")

                    handle.write = half_then_die
                return handle
            return guarded

        io.open, builtins.open = wrap(real_io), wrap(real_builtin)
        try:
            yield
        finally:
            io.open, builtins.open = real_io, real_builtin

    return armed


def _run(*args) -> int:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        return main(list(args))


def _stderr(*args) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, err.getvalue()


# (name, extension, args producing output A, args producing a DIFFERENT output B)
COMMANDS = [
    pytest.param("export", ".pdf",
                 lambda o: ("export", FIXTURE, "-o", o),
                 lambda o: ("export", FIXTURE, "-o", o, "--fold-scheme", "folio"),
                 id="export"),
    pytest.param("impose", ".deckle",
                 lambda o: ("impose", FIXTURE, "-o", o),
                 lambda o: ("impose", FIXTURE, "-o", o, "--gutter", "1in"),
                 id="impose"),
    pytest.param("schedule", ".txt",
                 lambda o: ("schedule", FIXTURE, "-o", o),
                 lambda o: ("schedule", FIXTURE, "-o", o, "--fold-scheme", "folio"),
                 id="schedule"),
    pytest.param("crop-preview", ".png",
                 lambda o: ("crop-preview", FIXTURE, "-o", o, "--auto-crop"),
                 lambda o: ("crop-preview", FIXTURE, "-o", o, "--auto-crop",
                            "--auto-crop-margin", "12pt"),
                 id="crop-preview"),
    pytest.param("dummy", ".pdf",
                 lambda o: ("dummy", "--pages", "8", "-o", o),
                 lambda o: ("dummy", "--pages", "16", "-o", o),
                 id="dummy"),
]


@pytest.mark.parametrize("name,suffix,first,second", COMMANDS)
def test_the_command_writes_its_output(tmp_path, name, suffix, first, second):
    """The premise the rest of the file rests on."""
    out = os.path.join(str(tmp_path), f"out{suffix}")

    assert _run(*first(out)) == 0
    assert os.path.getsize(out) > 0


@pytest.mark.parametrize("name,suffix,first,second", COMMANDS)
def test_a_failed_write_leaves_the_previous_file(tmp_path, torn_write,
                                                 name, suffix, first, second):
    """The property that had three different answers. The second run is
    chosen to produce *different* content, or "unchanged" could not be
    told apart from "rewritten identically"."""
    out = os.path.join(str(tmp_path), f"out{suffix}")
    assert _run(*first(out)) == 0
    before = open(out, "rb").read()

    with torn_write():
        _run(*second(out))

    assert open(out, "rb").read() == before


@pytest.mark.parametrize("name,suffix,first,second", COMMANDS)
def test_a_failed_write_leaves_no_debris(tmp_path, torn_write,
                                         name, suffix, first, second):
    """Scratch files land in the user's own folder, so one per failure is
    litter they will see."""
    out = os.path.join(str(tmp_path), f"out{suffix}")
    _run(*first(out))

    with torn_write():
        _run(*second(out))

    assert [p.name for p in tmp_path.iterdir()] == [f"out{suffix}"]


@pytest.mark.parametrize("name,suffix,first,second", COMMANDS)
def test_a_folder_as_the_output_is_refused_before_any_work(
    tmp_path, name, suffix, first, second
):
    """Validated up front, so a mistyped destination costs no imposition
    time and the message names the path the user typed."""
    folder = tmp_path / "adirectory"
    folder.mkdir()

    code, err = _stderr(*first(str(folder)))

    assert code == 1
    assert "Traceback" not in err
    assert err.startswith("error:")


@pytest.mark.parametrize("name,suffix,first,second", COMMANDS)
def test_a_missing_parent_folder_is_refused_with_a_message(
    tmp_path, name, suffix, first, second
):
    out = os.path.join(str(tmp_path), "no-such-folder", f"out{suffix}")

    code, err = _stderr(*first(out))

    assert code == 1
    assert "Traceback" not in err
    assert err.startswith("error:")
