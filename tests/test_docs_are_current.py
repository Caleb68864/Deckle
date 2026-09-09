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
CONTRIBUTING = os.path.join(ROOT, "docs", "CONTRIBUTING.md")
RUN_BAT = os.path.join(ROOT, "run.bat")


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


# -- the documented install can run the documented command ----------------
#
# A third countable claim, and the one with the worst first-contact cost.
# `pip install -e .` installs neither `pytest` nor `hypothesis`; both are in
# the `dev` extra, and `.[dev]` appeared in no document a human reads --
# only in `.github/workflows/test.yml`. So the README told a new contributor
# to install, then told them to run `python -m pytest -q` to check a test
# count, and the second instruction could not work after the first.
#
# Checked by name rather than by prose, the way the two lists above are: the
# extra is a string in `pyproject.toml`, and whether a file names it is
# decidable.


def _dev_extra_name() -> str:
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - project requires Python >=3.11
        import tomli as tomllib

    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
        data = tomllib.load(handle)
    extras = data["project"]["optional-dependencies"]
    assert "dev" in extras, f"the dev extra was renamed: {sorted(extras)}"
    return "dev"


def test_the_suite_needs_more_than_the_runtime_dependencies():
    """The guard's own premise, asserted rather than assumed.

    If the test suite ever became runnable from a bare `pip install -e .`,
    the three tests below would be enforcing a pointless instruction. They
    are worth keeping only while this is true.
    """
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib

    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
        data = tomllib.load(handle)

    runtime = {
        re.split(r"[<>=!\[; ]", dep, maxsplit=1)[0].lower()
        for dep in data["project"]["dependencies"]
    }

    assert "pytest" not in runtime
    assert "hypothesis" not in runtime, (
        "imported at module level by tests/test_imposition_properties.py, "
        "so its absence is a collection error, not a skip"
    )


def _names_the_dev_extra(text: str) -> bool:
    """Whether ``text`` carries an install command naming the dev extra.

    Accepts the quoted and unquoted spellings and either separator, because
    `pip install -e ".[dev]"`, `pip install -e .[dev]` and
    `python -m pip install -e '.[dev]'` are the same instruction and a test
    that demanded one of them would be about punctuation.
    """
    return bool(re.search(r"""pip install[^\n]*\.\[\s*dev\s*\]""", text))


def test_the_readme_says_how_to_install_what_its_own_test_command_needs():
    _dev_extra_name()
    text = _read(README)

    assert "python -m pytest" in text, (
        "this guard exists because the README tells the reader to run the "
        "suite; if it stopped, revisit the guard rather than the README"
    )
    assert _names_the_dev_extra(text), (
        "README tells the reader to run `python -m pytest -q` but never "
        "names `.[dev]`, so following it from the top gives neither pytest "
        "nor hypothesis"
    )


def test_contributing_says_how_to_install_what_it_asks_contributors_to_run():
    _dev_extra_name()
    text = _read(CONTRIBUTING)

    assert "python -m pytest" in text
    assert _names_the_dev_extra(text), (
        "CONTRIBUTING.md is where a new contributor is pointed, and it asks "
        "for a full suite run without ever naming `.[dev]`"
    )


def test_run_bat_deps_installs_what_run_bat_test_runs():
    """`run.bat deps` then `run.bat test` is the whole Windows path.

    It installed `pytest` and `psutil` by hand and never the extra, so
    `hypothesis` was missing and `run.bat test` opened with two collection
    errors.
    """
    _dev_extra_name()
    text = _read(RUN_BAT)

    assert _names_the_dev_extra(text), (
        "run.bat installs test tooling by hand instead of `.[dev]`, so the "
        "extra and the batch file are two lists that must agree"
    )
