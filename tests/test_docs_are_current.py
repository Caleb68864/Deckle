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
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
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


# -- the changelog is a list of things the code owns too ------------------
#
# A third countable inventory, and the one nobody re-reads. The CHANGELOG's
# CLI entry named six subcommands while the parser had eight -- `print` and
# `profile` shipped without reaching it -- and spelled the six it did name
# as `deckle <command>`. `deckle` is the GUI console script; the CLI one is
# `deckle-cli`. Every such line in the README and the GUIDE was corrected
# when `[project.scripts]` was added; the CHANGELOG was not, so the file
# that is supposed to be the record of what changed disagreed with the
# packaging about what the program is called.
#
# Subcommands only, not flags. The GUIDE's section 8 is the reference and
# is already checked flag by flag; demanding the same of a changelog would
# turn it into a second reference manual, which is the thing that makes
# both of them go stale.


def _cli_commands() -> set[str]:
    from deckle.cli import build_parser

    parser = build_parser()
    commands: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        commands.update(getattr(action, "choices", {}) or {})
    assert commands, "build_parser() exposed no subcommands to check against"
    return commands


def test_the_changelog_names_every_cli_command():
    """A command that shipped without reaching the changelog never shipped,
    as far as the only file that claims to record what shipped knows."""
    text = _read(CHANGELOG)
    missing = sorted(name for name in _cli_commands() if f"`{name}`" not in text)

    assert not missing, f"CLI commands absent from CHANGELOG.md: {missing}"


def test_the_changelog_spells_a_command_the_way_it_is_installed():
    """`deckle info` is not a command anybody can run.

    ``[project.scripts]`` installs ``deckle`` (the GUI, which answers
    ``--help`` and then opens a window) and ``deckle-cli`` (the headless
    one). A changelog line reading ``deckle info`` sends the reader to the
    GUI entry point with a subcommand it has never parsed.
    """
    commands = _cli_commands()
    text = _read(CHANGELOG)

    pattern = r"(?<![\w.\-])deckle\s+(" + "|".join(sorted(commands)) + r")\b"
    wrong = [
        f"line {text[: m.start()].count(chr(10)) + 1}: {m.group(0)!r}"
        for m in re.finditer(pattern, text)
    ]

    assert not wrong, (
        "CHANGELOG.md gives the GUI console script a CLI subcommand; the "
        f"headless one is `deckle-cli`: {wrong}"
    )


def test_the_right_spelling_is_not_flagged_by_that_check():
    """The control the refusing check needs.

    A pattern that also matched ``deckle-cli export`` or
    ``python -m deckle.cli export`` would be unsatisfiable -- there would
    be no way to write the line correctly -- and the test above would be
    refusing everything rather than the one spelling that is wrong.
    """
    commands = _cli_commands()
    pattern = r"(?<![\w.\-])deckle\s+(" + "|".join(sorted(commands)) + r")\b"

    for accepted in (
        "`deckle-cli export book.pdf`",
        "`python -m deckle.cli info book.pdf`",
        "run `deckle-cli print` to plan a run",
    ):
        assert not re.search(pattern, accepted), accepted

    # ...and it does still catch the spelling it is for, so a pattern that
    # matched nothing at all could not pass this file.
    assert re.search(pattern, "`deckle export book.pdf`")


# -- the test count the README publishes ---------------------------------
#
# The README said "1,720 passing, 22 skipped at `8e2e8d2`" against a tree
# collecting 2,340, and justified the number with *"GitHub Actions runs the
# same command ... so this number is checked rather than remembered"*. That
# sentence was the worst part: nothing in `tests/` asserted a count, so the
# claim of a check was itself the stalest thing on the page.
#
# What is checked here is deliberately one-directional. Demanding equality
# would put a README edit on every commit that adds a test, and a guard
# that fails on almost every commit gets worked around rather than
# honoured -- unlike the module list and the CLI reference above, which
# change a few times a year. What matters to a reader is that the number is
# not INFLATED: a suite that has shrunk under a README still advertising
# the old size is the claim that misleads. Understating is a snapshot
# ageing, which the commit named beside it already discloses.


def _readme_test_counts() -> tuple[int, int]:
    """The passing and skipped counts the README publishes.

    :returns: ``(passing, skipped)``.
    :raises AssertionError: the sentence is not there or does not parse.
        Refusing rather than returning zeros: a guard that silently found
        no number would pass forever, which is the failure it replaced.
    """
    text = _read(README)
    match = re.search(
        r"([\d,]+)\s+passing,\s+([\d,]+)\s+skipped", text
    )
    assert match, (
        "the README no longer states a 'N passing, M skipped' count; this "
        "guard would otherwise check nothing"
    )
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _collected_test_count() -> int:
    """How many tests this suite collects, asked of pytest itself.

    Collection rather than a run: it is a few seconds instead of ninety,
    and it is the same number on every interpreter in the CI matrix --
    every ``importorskip`` in this suite is inside a fixture or a test
    body, so nothing is skipped at collection time and no module
    contributes a different count on 3.11 than on 3.14.

    :returns: the collected test count.
    :raises AssertionError: pytest did not report one. A parse that
        quietly yielded 0 would make the comparison below vacuous.
    """
    import subprocess
    import sys

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only",
         "-p", "no:cacheprovider", ROOT],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=600,
    )
    match = re.search(r"(\d+)\s+tests? collected", result.stdout)
    assert match, (
        "pytest did not report a collected count (exit "
        f"{result.returncode}):\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
    return int(match.group(1))


def test_the_readme_does_not_advertise_more_tests_than_exist():
    """The direction that misleads a reader.

    A README claiming a suite larger than the one in the repository is a
    claim about how well the code is guarded, and it is the one a
    newcomer has no way to check before trusting it.
    """
    passing, skipped = _readme_test_counts()
    collected = _collected_test_count()

    assert passing + skipped <= collected, (
        f"README advertises {passing} passing + {skipped} skipped = "
        f"{passing + skipped} tests; the suite collects {collected}"
    )


def test_the_readme_skip_breakdown_adds_up():
    """The README breaks the skips down by reason. Four numbers that must
    sum to the fifth -- checkable without running anything, and wrong in
    exactly the way a hand-maintained tally goes wrong."""
    _, skipped = _readme_test_counts()
    text = _read(README)

    reasons = re.search(
        # \s+ rather than a space: the sentence wraps, so two of the four
        # counts are separated from their reason by a newline.
        r"(\w+)\s+of the skips are packaging.*?(\w+)\s+are Windows-only.*?"
        r"(\w+)\s+are the golden.*?(\w+)\s+needs",
        text,
        re.DOTALL,
    )
    assert reasons, (
        "the README's skip breakdown has been reworded; this guard reads "
        "it by shape and would otherwise check nothing"
    )

    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    }
    counted = 0
    for group in reasons.groups():
        key = group.lower()
        assert key in words or key.isdigit(), f"unreadable skip count {group!r}"
        counted += words.get(key, 0) or int(group)

    assert counted == skipped, (
        f"the README's skip breakdown sums to {counted}, but it claims "
        f"{skipped} skipped"
    )


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
