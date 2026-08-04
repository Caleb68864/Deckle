"""Tests for deckle.cli -- the headless CLI."""

from __future__ import annotations

import ast
import os
import subprocess
import sys

import pikepdf
import pytest

from deckle.cli import main

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
