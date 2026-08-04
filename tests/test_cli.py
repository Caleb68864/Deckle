"""Tests for deckle.cli -- the headless CLI."""

from __future__ import annotations

import ast
import os
import subprocess
import sys

import pikepdf
import pytest

from deckle import __version__ as DECKLE_VERSION
from deckle.cli import _parse_length_pt, main

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


def test_export_produces_a_readable_pdf(tmp_path, capsys):
    out_path = os.path.join(str(tmp_path), "out.pdf")
    rc = main(["export", FIXTURE, "-o", out_path, "--gutter", "0.75in"])
    assert rc == 0
    assert os.path.exists(out_path)

    with pikepdf.open(out_path) as pdf:
        assert len(pdf.pages) > 0


def test_info_prints_page_count_sizes_and_warnings(capsys):
    rc = main(["info", FIXTURE])
    assert rc == 0
    out = capsys.readouterr().out
    assert "page count:" in out
    assert "detected page sizes" in out
    assert "layout warnings" in out


def test_impose_writes_a_deckle_project(tmp_path):
    out_path = os.path.join(str(tmp_path), "proj.deckle")
    rc = main(["impose", FIXTURE, "-o", out_path])
    assert rc == 0
    assert os.path.exists(out_path)


def test_cli_imports_only_deckle_core_not_app_or_qt():
    """Statically verify deckle/cli.py never imports deckle.app or a Qt binding.

    This is what keeps the CLI runnable in a headless CI container with no
    display server present.
    """
    cli_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deckle", "cli.py")
    with open(cli_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=cli_path)

    forbidden_prefixes = ("deckle.app", "PySide6", "PyQt5", "PyQt6")
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        assert not name.startswith(forbidden_prefixes), f"forbidden import: {name}"


def test_version_flag_works_without_a_subcommand(capsys):
    """A-6: `deckle --version` must work standalone, even though every
    subcommand (impose/export/info) is otherwise required."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0


def test_version_flag_reports_app_and_dependency_versions():
    """A-6: prints the Deckle app version plus the resolved versions of
    pikepdf, pypdfium2, img2pdf, and PySide6."""
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert DECKLE_VERSION in out
    for dependency in ("pikepdf", "pypdfium2", "img2pdf", "PySide6"):
        assert dependency in out


def test_version_flag_does_not_import_pyside6():
    """A-6: resolved via importlib.metadata, never by importing PySide6
    itself -- deckle.cli must not import deckle.app or Qt."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import deckle.cli; "
            "assert not any(m.startswith('PySide6') for m in sys.modules), "
            "'PySide6 was imported'; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("18", 18.0),
        ("5cm", 5.0 * 72.0 / 2.54),
        ("0.75in", 54.0),
        ("3 mm", 3.0 * 72.0 / 25.4),
    ],
)
def test_parse_length_pt_accepts_bare_numbers_and_all_units(raw, expected):
    """A-9: accepts in/pt/mm/cm, an optional space before the unit, and
    treats a bare number as points."""
    assert _parse_length_pt(raw) == pytest.approx(expected, abs=0.01)


def test_parse_length_pt_rejects_invalid_unit_naming_accepted_set():
    """A-9: invalid units are a blocking error naming the accepted set."""
    import argparse

    with pytest.raises(argparse.ArgumentTypeError) as exc_info:
        _parse_length_pt("5furlongs")
    message = str(exc_info.value)
    for unit in ("in", "pt", "mm", "cm"):
        assert unit in message


def test_cli_runs_headlessly_as_a_subprocess():
    """The exact command from the sub-spec's acceptance criteria."""
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", FIXTURE],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "page count:" in result.stdout
