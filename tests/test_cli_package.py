"""``deckle.cli`` is a package now, and the ways that can break are quiet ones.

Splitting a module into a package changes three things no existing test was
watching. ``python -m`` stops working, because a package has no
``if __name__ == "__main__"`` -- and it fails at the shell, which most of
the suite reaches through ``subprocess`` and would report as a puzzling
non-zero exit. A name that used to be a module global becomes a re-export,
which is a *different binding*, so a ``monkeypatch.setattr`` aimed at the
package silently patches nothing. And the import graph gains cycles very
easily once five modules refer to each other.

None of those changes a single command's behaviour, which is why they need
their own tests rather than being caught by the ones that already exist.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys

import pytest

from deckle import __version__ as DECKLE_VERSION

PACKAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deckle", "cli"
)


def _sources() -> list[str]:
    return sorted(
        os.path.join(PACKAGE_DIR, name)
        for name in os.listdir(PACKAGE_DIR)
        if name.endswith(".py")
    )


def test_python_dash_m_deckle_cli_still_runs():
    """``run.bat cli`` and fourteen test files shell out to this.

    A package has no ``if __name__ == "__main__"``; ``runpy`` looks for a
    ``__main__`` submodule instead. Without ``deckle/cli/__main__.py`` this
    fails with "'deckle.cli' is a package and cannot be directly executed",
    at the shell rather than anywhere a traceback would explain it.
    """
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "--version"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert DECKLE_VERSION in result.stdout


# Every name the suite imports from ``deckle.cli``, with the file that
# imports it -- so a failure says which suite is about to break rather than
# only which name went missing.
RE_EXPORTED = [
    ("main", "tests/test_cli.py"),
    ("build_parser", "tests/test_spec_residue.py"),
    ("_make_output_encoding_safe", "tests/test_hardening_platform.py"),
    ("sys", "tests/test_hardening_platform.py (patches cli.sys.stdout)"),
    ("_version_string", "tests/test_about.py"),
    ("_parse_length_pt", "tests/test_cli.py"),
    ("_parse_paper", "tests/test_cli_errors.py"),
    ("MIN_PAPER_PT", "tests/test_cli_errors.py"),
    ("MAX_PAPER_PT", "tests/test_cli_errors.py"),
    ("_parse_sheet_selection", "tests/test_cli_sheets.py"),
    ("_parse_page_selection", "tests/test_cli_sheets.py"),
    ("_parse_station_positions", "tests/test_cli_paper.py"),
    ("_parse_offset_pair", "tests/test_registration.py"),
    ("_strategy_for", "tests/test_paper_bounds.py"),
    ("_report_rule", "tests/test_proof_sheet.py"),
    ("_resolve_profile", "docs/specs B21"),
    ("_cmd_crop_preview", "tests/test_output_command_parity.py"),
    ("_load_source", "tests/test_cli.py (see the next test)"),
]


@pytest.mark.parametrize("name,importer", RE_EXPORTED)
def test_every_name_the_tests_import_is_still_on_the_package(name, importer):
    """The split must not quietly narrow ``deckle.cli``'s surface."""
    import deckle.cli

    assert hasattr(deckle.cli, name), (
        f"deckle.cli no longer exports {name!r} ({importer} imports it)"
    )


def test_a_re_export_is_not_the_binding_a_command_calls():
    """The trap, asserted rather than remembered.

    ``deckle.cli._load_source`` and ``deckle.cli.commands._load_source`` are
    two names for one function object, and rebinding the first does not
    change what ``_load_source_or_report`` calls. A tripwire test patched at
    the package would keep passing while guarding nothing, so this states
    the property that makes such a patch wrong.
    """
    import deckle.cli
    from deckle.cli import commands

    sentinel = object()
    original = deckle.cli._load_source
    try:
        deckle.cli._load_source = sentinel
        assert commands._load_source is original, (
            "patching deckle.cli would have reached the caller -- if this "
            "ever becomes true, the warning in the module docstring is stale"
        )
    finally:
        deckle.cli._load_source = original


def test_the_cli_modules_import_in_one_direction():
    """A cycle here would not fail at import time.

    Python tolerates plenty of them; it would fail later, on whichever
    module happened to be imported first, and in a place that names none of
    the modules involved.
    """
    # Each module may import only those listed for it.
    allowed = {
        "values": set(),
        "report": {"values"},
        "commands": {"values", "report"},
        "options": {"values", "report", "commands"},
        "__init__": {"values", "report", "commands", "options"},
        "__main__": {"values", "report", "commands", "options", "__init__"},
    }

    for path in _sources():
        module = os.path.basename(path)[: -len(".py")]
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "deckle.cli":
                    imported.update(alias.name for alias in node.names)
                elif node.module.startswith("deckle.cli."):
                    imported.add(node.module.split(".")[2])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("deckle.cli."):
                        imported.add(alias.name.split(".")[2])

        # `from deckle.cli import main` names a function, not a module.
        imported &= set(allowed)
        stray = imported - allowed[module]
        assert not stray, (
            f"deckle/cli/{module}.py imports {sorted(stray)}, which is "
            "downstream of it"
        )


def test_build_parser_is_called_once_per_invocation(tmp_path, monkeypatch):
    """`_resolve_input` used to rebuild the entire parser on every
    ``.deckle`` load -- ten subparsers and every help string in the program
    -- to rediscover the subparser its own arguments had just come out of.

    Counted rather than grepped, and counted on the project path, which is
    the one that did it: a second call site added anywhere along it fails
    this, whatever it is spelled.
    """
    from deckle.cli import main, options

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    project = tmp_path / "job.deckle"
    assert main(["impose", fixture, "-o", str(project)]) == 0

    calls = []
    real = options.build_parser

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(options, "build_parser", counting)

    # `--gutter` beside a project is the case that reached the rebuild: it
    # is a layout flag the project overrides, so the "ignored" note fires.
    assert main(["info", str(project), "--gutter", "2in"]) == 0

    assert calls == [1], f"build_parser was called {len(calls)} times"
