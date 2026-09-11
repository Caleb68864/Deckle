"""Structural tests for ``run.bat``.

Written after a generated edit spliced the packaging block into the MIDDLE of
the dispatch table. The Python that inserted it replaced the first occurrence
of ``:doctor`` -- which was the ``goto :doctor`` line, not the label -- so the
``docs``/``help`` dispatch and the entire default GUI-launch block were
deleted, ``doctor`` was rewired to ``:package``, and a bare ``run.bat`` fell
straight through into the packager.

The result: the dev launcher spent three minutes building executables and then
closed. Every test passed throughout, because nothing tested the launcher.

These are cheap structural checks. A batch file has no import to fail and no
exception to raise -- it silently does the wrong thing -- so its shape is what
has to be asserted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RUN_BAT = Path(__file__).resolve().parents[1] / "run.bat"

#: Every subcommand the header documents.
SUBCOMMANDS = ("cli", "test", "deps", "doctor", "docs", "package")


def _text() -> str:
    return RUN_BAT.read_text(encoding="utf-8")


def _labels() -> list[str]:
    return re.findall(r"^:(\w+)", _text(), re.MULTILINE)


def test_every_label_is_defined_exactly_once():
    """A duplicated label means cmd jumps to the first and the second block
    becomes unreachable -- silently. The bad edit produced two ``:doctor``."""
    labels = _labels()
    duplicates = {name for name in labels if labels.count(name) > 1}
    assert not duplicates, f"duplicate labels in run.bat: {sorted(duplicates)}"


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_documented_subcommand_dispatches_to_its_own_label(command: str):
    """``doctor`` was rewired to ``:package``. A dispatch target must match
    the subcommand's own name."""
    match = re.search(rf'if /i "%CMD%"=="{command}"\s+goto :(\w+)', _text())
    assert match, f"no dispatch line for {command!r}"
    assert match.group(1) == command, (
        f"{command!r} dispatches to :{match.group(1)}, not :{command}"
    )


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_dispatch_target_has_a_matching_label(command: str):
    assert command in _labels(), f"run.bat dispatches to :{command} but never defines it"


def test_a_bare_invocation_launches_the_gui_and_builds_nothing():
    """The regression, stated directly.

    With no argument the launcher must reach the GUI block, and must not
    fall through into packaging -- three minutes of build for someone who
    typed ``run.bat`` to look at their change.
    """
    text = _text()
    dispatch_start = text.index('set "CMD=%~1"')
    tail = text[dispatch_start:]
    first_label = re.search(r"^:\w+", tail, re.MULTILINE)
    assert first_label, "no labels after the dispatch table"
    default_block = tail[: first_label.start()]

    assert "-m deckle" in default_block, "the default path does not launch the GUI"
    assert "PyInstaller" not in default_block, (
        "a bare run.bat reaches PyInstaller -- it should only launch the GUI"
    )
    assert "goto :done" in default_block, (
        "the default path does not terminate, so it falls into the next label"
    )


def test_the_package_block_is_only_reachable_by_its_own_label():
    text = _text()
    package_at = text.index("\n:package")
    assert "PyInstaller" not in text[:package_at], (
        "PyInstaller is invoked before the :package label, so some other path "
        "reaches it"
    )


def test_every_branch_terminates_rather_than_falling_through():
    """Batch files fall through label boundaries. A block that forgets its
    ``goto`` runs the next one too -- which is how ``doctor`` could end up
    building executables."""
    blocks = re.split(r"^:(\w+)", _text(), flags=re.MULTILINE)[1:]
    terminal = {"usage", "fail", "done"}
    for name, body in zip(blocks[::2], blocks[1::2]):
        if name in terminal:
            continue
        assert "goto :" in body, f":{name} never jumps anywhere; it falls through"


def test_the_header_documents_exactly_the_subcommands_that_exist():
    """A launcher whose help lies is worse than one with no help."""
    header = _text().split("rem ===", 2)[1]
    for command in SUBCOMMANDS:
        assert f"run.bat {command}" in header, f"{command!r} is undocumented"


# -- the failure path must return ----------------------------------------
#
# `:fail` ended `pause` then `exit /b 1`. `pause` reads the *console*, not
# stdin, so on a runner without one its behaviour is not something to rely
# on; if it blocks, a failed build waits for a keypress nobody will press
# and burns the whole job timeout instead of failing in two minutes. Same
# family as `python -m deckle --help` launching the GUI: an entry point
# that never returns. The keypress is worth keeping for the developer who
# double-clicked run.bat and would otherwise watch the window vanish, so
# it is guarded rather than deleted.


def _block(label: str) -> str:
    """The body of one ``:label`` block, up to the next label.

    :param label: the label to read, without its colon.
    :returns: the lines between that label and the next one.
    :raises AssertionError: the label is not in the file. Refusing rather
        than returning ``""``: an empty block would make every assertion
        about its contents vacuously true.
    """
    text = _text()
    match = re.search(rf"^:{label}$", text, re.MULTILINE)
    assert match, f"run.bat has no :{label} block to check"
    tail = text[match.end():]
    nxt = re.search(r"^:\w+", tail, re.MULTILINE)
    body = tail[: nxt.start()] if nxt else tail
    assert body.strip(), f":{label} is empty"
    return body


def _pause_lines(text: str) -> list[str]:
    """Every line that runs ``pause`` as a command.

    Matches ``pause`` as a whole word at the end of a command, so a
    guarded ``if not defined CI pause`` is found too -- the question this
    file asks is whether the guard is *there*, not whether pause is.
    ``rem`` lines are skipped so the comment explaining the guard does
    not read as a second call.
    """
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("rem "):
            continue
        if re.search(r"(?<![\w:])pause\s*$", stripped, re.IGNORECASE):
            found.append(stripped)
    return found


def test_the_failure_path_still_reports_and_exits_nonzero():
    """The premise. Without it, "no unguarded pause" would be satisfied by
    a :fail block that had been deleted."""
    body = _block("fail")

    assert "exit /b 1" in body, ":fail no longer exits nonzero"
    assert "failed" in body.lower(), ":fail no longer says anything failed"


def test_the_failure_path_does_not_block_on_a_keypress_unattended():
    """The regression, stated directly.

    A bare ``pause`` on the failure path means an unattended run -- CI, a
    scheduled build, anything with no console -- can wait forever for a
    key nobody is at the keyboard to press.
    """
    unguarded = [
        line
        for line in _pause_lines(_text())
        if not re.match(r"if\s", line, re.IGNORECASE)
    ]

    assert not unguarded, (
        "run.bat runs pause unconditionally, so a failed unattended build "
        f"waits for a keypress: {unguarded}"
    )


def test_the_guard_is_the_variable_every_runner_sets():
    """``CI`` by name, not just "some condition".

    A guard on a variable nothing sets is the same hang with more words,
    and the failure would be invisible: the build simply stops.
    """
    paused = _pause_lines(_text())
    if not paused:
        return  # nothing to guard; the test above already allows this
    assert any("defined CI" in line for line in paused), (
        f"pause is guarded, but not on CI: {paused}"
    )


def test_the_keypress_is_still_available_to_a_person():
    """The control that must be *accepted*.

    Deleting ``pause`` outright would pass both tests above and quietly
    remove the thing it is there for: a developer who double-clicked
    run.bat, whose window would otherwise close before the error is
    readable. The guard has to be a guard, not a removal.
    """
    body = _block("fail")

    assert _pause_lines(body), (
        ":fail no longer pauses at all, so a double-clicked run.bat closes "
        "its window before the error can be read"
    )
