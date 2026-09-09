"""Two documentation claims that are lists of things the code owns.

The README's module list had drifted by six modules and the GUIDE's CLI
reference by two commands and fourteen options, because both are
inventories of something that keeps growing. Everything else in the
documentation pass is a sentence somebody has to read; these two are
countable, so they are counted.

Deliberately narrow. This does not check that the prose is *true* -- no
test can -- only that neither list has silently stopped naming everything
the package contains, which is the failure mode both of them actually had.
It is the same shape as ``test_docs_coverage.py``, which checks that every
module reaches the generated API docs, and for the same reason: a hand-
maintained list of things the code adds to will drift, and the drift is
invisible until a reader acts on it.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "deckle", "core")
GUIDE = os.path.join(ROOT, "docs", "GUIDE.md")
README = os.path.join(ROOT, "README.md")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _core_modules() -> set[str]:
    return {
        name[: -len(".py")]
        for name in os.listdir(CORE)
        if name.endswith(".py") and name != "__init__.py"
    }


def _cli_reference() -> str:
    """The GUIDE's section 8, and nothing else.

    Sliced rather than searched, so a command named in passing somewhere
    else in the document does not count as documented in the reference.
    The separator is U+00B7 (a middle dot), which is why this file is read
    as UTF-8 explicitly.
    """
    text = _read(GUIDE)
    assert "\n## 8 · CLI reference" in text, (
        "the GUIDE's CLI reference heading has moved or been renamed; this "
        "test slices on it and would otherwise silently check nothing"
    )
    section = text.split("\n## 8 · CLI reference", 1)[1]
    return section.split("\n## 9 ", 1)[0]


def test_the_readme_lists_every_core_module():
    """``deckle.core`` is the part of the README a reader navigates by."""
    text = _read(README)
    block = text.split("deckle/core/", 1)[1].split("```", 1)[0]
    listed = set(re.findall(r"[a-z_]+", block))

    missing = sorted(_core_modules() - listed)

    assert not missing, (
        "modules missing from README's architecture list: " f"{missing}"
    )


def test_the_guide_documents_every_cli_command():
    from deckle.cli import build_parser

    parser = build_parser()
    commands = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        commands.update(getattr(action, "choices", {}) or {})

    section = _cli_reference()
    missing = sorted(name for name in commands if name not in section)

    assert not missing, f"CLI commands absent from GUIDE section 8: {missing}"


def test_the_guide_documents_every_cli_option():
    """Every ``--flag`` any subcommand accepts appears in the reference.

    ``--version``, ``--landscape`` and ``-o``/``--output`` are documented
    outside the option tables, under Global and Commands, so this asserts
    *presence in the section* rather than membership of a table -- a test
    that demanded the table would make the GUIDE worse to write.
    """
    from deckle.cli import build_parser

    parser = build_parser()
    options = set()
    for group in parser._subparsers._group_actions:  # noqa: SLF001
        for subparser in (getattr(group, "choices", {}) or {}).values():
            for action in subparser._actions:  # noqa: SLF001
                options.update(
                    flag
                    for flag in action.option_strings
                    if flag.startswith("--") and flag != "--help"
                )

    section = _cli_reference()
    missing = sorted(flag for flag in options if flag not in section)

    assert not missing, f"CLI options absent from GUIDE section 8: {missing}"
