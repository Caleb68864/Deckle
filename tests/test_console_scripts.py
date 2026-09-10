"""The commands the documentation tells people to type.

Deckle's documentation has always been written as if two executables were
installed -- ``deckle`` for the desktop app and ``deckle-cli`` for the
headless one. Both exist in a *packaged* build:
``packaging/deckle.spec`` builds them by exactly those names, and
``test_packaging_audit.py`` requires both to be present in ``dist/``. Only
a packaged build had them. ``pyproject.toml`` declared no
``[project.scripts]`` at all, so ``pip install -e .`` -- the one install
command any human-facing document gives -- produced neither, and every
command line in the README and the GUIDE was a command that did not
exist. That is why a reader reaches for ``python -m deckle`` instead,
which is how S9 was found.

Two things are pinned here, because getting either one alone is worse
than getting neither:

1. The scripts exist, and their **names and meanings match the frozen
   executables**. A pip install where ``deckle`` runs the CLI and a bundle
   where ``deckle`` runs the GUI is a worse state than no scripts at all:
   the same word means two things on two machines.
2. No document invokes a CLI subcommand as ``deckle <command>``. It is
   ``deckle-cli <command>``, as the GUIDE's own CLI reference has always
   said ("Or, packaged, ``deckle-cli``"), and the prose elsewhere had
   drifted away from it.

``deckle --version`` is *not* an error and is deliberately not caught by
the scan below: since S9 the GUI entry point answers it with the same
lines the CLI prints, which is what the GUIDE's bug-report instructions
depend on.
"""

from __future__ import annotations

import ast
import os
import re
import tomllib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
SPEC = os.path.join(ROOT, "packaging", "deckle.spec")

#: Documents a reader copies commands out of. The CHANGELOG is excluded
#: on purpose -- it records what past releases said, and editing it to
#: match today is how a changelog stops being evidence.
PROSE = ("README.md", os.path.join("docs", "GUIDE.md"))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _scripts() -> dict[str, str]:
    with open(PYPROJECT, "rb") as handle:
        return tomllib.load(handle).get("project", {}).get("scripts", {})


def _frozen_executable_names() -> set[str]:
    """The ``name=`` of every ``EXE(...)`` in the PyInstaller spec.

    Parsed rather than pattern-matched, and rather than hard-coded here:
    a list in this file that agreed with the spec on the day it was
    written and never again is the drift this test exists to catch.
    """
    tree = ast.parse(_read(SPEC), filename=SPEC)
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "EXE":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                names.add(keyword.value.value)
    return names


def test_the_spec_really_does_build_two_named_executables():
    """The premise of everything below, asserted rather than assumed."""
    assert _frozen_executable_names() == {"deckle", "deckle-cli"}


def test_pyproject_declares_console_scripts():
    assert _scripts(), (
        "pyproject.toml declares no [project.scripts], so `pip install -e .` "
        "installs no `deckle` and no `deckle-cli` -- while README.md, "
        "docs/GUIDE.md and packaging/deckle.spec all describe both"
    )


def test_the_console_scripts_are_named_after_the_frozen_executables():
    """One word, one meaning, however Deckle was installed."""
    assert set(_scripts()) == _frozen_executable_names()


@pytest.mark.parametrize(
    "script,module,attribute",
    [
        ("deckle", "deckle.__main__", "run"),
        ("deckle-cli", "deckle.cli", "main"),
    ],
)
def test_each_script_points_at_something_that_exists(script, module, attribute):
    """A ``[project.scripts]`` target is a string until something resolves
    it, and the thing that resolves it is an installer -- so a typo there
    survives every test in this suite and fails on a user's machine."""
    import importlib

    target = _scripts().get(script)
    assert target == f"{module}:{attribute}", target

    resolved = getattr(importlib.import_module(module), attribute)
    assert callable(resolved)


def test_the_gui_script_is_the_gui_and_the_cli_script_is_the_cli():
    """The half of the contract a name cannot carry.

    The frozen ``deckle`` is built ``console=False`` from
    ``deckle/__main__.py`` and the frozen ``deckle-cli`` ``console=True``
    from ``deckle/cli/__main__.py``. If the console scripts were wired the
    other way round, both tests above would still pass and a Windows user
    would have a CLI that prints nowhere.
    """
    assert _scripts()["deckle"].startswith("deckle.__main__")
    assert _scripts()["deckle-cli"].startswith("deckle.cli")


# -- the prose ------------------------------------------------------------


def _cli_subcommands() -> set[str]:
    from deckle.cli import build_parser

    parser = build_parser()
    commands: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        commands.update(getattr(action, "choices", {}) or {})
    return commands


@pytest.mark.parametrize("document", PROSE)
def test_no_document_invokes_a_cli_subcommand_as_bare_deckle(document):
    """``deckle schedule`` is not a command anyone can run.

    ``deckle`` is the desktop app; it takes no subcommands. Every one of
    these is a line a reader would copy, paste, and be told
    ``unrecognized arguments`` by -- or, before S9, would be answered by a
    window opening.
    """
    text = _read(os.path.join(ROOT, document))
    pattern = re.compile(
        r"(?<![\w-])deckle\s+(" + "|".join(sorted(_cli_subcommands())) + r")(?![\w-])"
    )
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            hits.append(f"{document}:{number}: {match.group(0)}")

    assert not hits, (
        "these name a command that does not exist; the CLI is `deckle-cli` "
        f"(or `python -m deckle.cli`): {hits}"
    )
