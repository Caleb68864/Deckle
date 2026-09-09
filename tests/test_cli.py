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


def test_export_surfaces_layout_warnings_on_stderr(tmp_path):
    """Hardening: ``export`` used to compute layout warnings and drop them.

    Only ``info`` printed them, so ``export --fold-scheme folio`` onto
    portrait paper squeezed two pages onto every portrait sheet and said
    nothing. Warnings belong on stderr so stdout stays clean for scripting,
    and they must not change the exit code -- a warning is advice.
    """
    out = tmp_path / "out.pdf"
    result = subprocess.run(
        [
            sys.executable, "-m", "deckle.cli", "export", FIXTURE,
            "-o", str(out),
            "--fold-scheme", "folio",
            "--sheets-per-signature", "4",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "sheet_orientation" in result.stderr
    assert "layout warnings:" in result.stderr
    # stdout stays scriptable: the path, not the commentary.
    assert "sheet_orientation" not in result.stdout
    assert out.exists()


def test_export_says_nothing_extra_when_there_are_no_warnings(tmp_path):
    out = tmp_path / "clean.pdf"
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", FIXTURE, "-o", str(out)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "layout warnings" not in result.stderr


# --- --pages: narrowing a scan to the book inside it ---------------------
#
# Skipped, never deleted. A `.deckle` written with `--pages` keeps the whole
# source and the decision, so reopening it shows what was excluded rather
# than a document that mysteriously starts at page 7.


def _dummy(tmp_path, pages: int = 8):
    path = tmp_path / "d.pdf"
    assert main(["dummy", "-o", str(path), "--pages", str(pages)]) == 0
    return str(path)


def test_pages_narrows_the_document(tmp_path):
    import json

    source = _dummy(tmp_path, 8)
    out = tmp_path / "job.deckle"

    rc = main(["impose", source, "-o", str(out), "--pages", "3-6"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["pages"]) == 8, "pages were deleted, not skipped"
    assert [p["skipped"] for p in data["pages"]] == [
        True, True, False, False, False, False, True, True
    ]


def test_pages_naming_a_page_that_is_not_there_fails_cleanly(tmp_path, capsys):
    source = _dummy(tmp_path, 8)
    out = tmp_path / "job.deckle"

    rc = main(["impose", source, "-o", str(out), "--pages", "400"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no page 400" in err
    assert "8 page(s)" in err
    assert not out.exists(), "a refused selection still wrote a project"


def test_pages_that_keep_nothing_fails_cleanly(tmp_path, capsys, monkeypatch):
    """Skipping every page produces a plan with no sheets, and `export`
    would then write a 0-page PDF and report success -- the worst pair.

    Reached by patching the loader, because the CLI cannot otherwise hand
    `--pages` a document whose kept set is already empty.
    """
    from dataclasses import replace

    import deckle.cli as cli

    source = _dummy(tmp_path, 4)
    real = cli._load_source

    def all_skipped(path):
        return [replace(page, skipped=True) for page in real(path)]

    monkeypatch.setattr(cli, "_load_source", all_skipped)
    out = tmp_path / "job.deckle"

    rc = main(["impose", source, "-o", str(out), "--pages", "1-4"])

    assert rc == 1
    assert "kept no pages" in capsys.readouterr().err


def test_pages_is_reported_as_ignored_for_a_project_source(tmp_path, capsys):
    source = _dummy(tmp_path, 4)
    project = tmp_path / "job.deckle"
    assert main(["impose", source, "-o", str(project)]) == 0
    out = tmp_path / "out.pdf"

    rc = main(["export", str(project), "-o", str(out), "--pages", "1-2"])

    assert rc == 0
    err = capsys.readouterr().err
    assert "--pages" in err
    assert "carries its own layout" in err


def test_pages_narrows_what_auto_crop_measures(tmp_path, capsys, monkeypatch):
    """The selection is applied BEFORE `--auto-crop`, so a scanner target's
    calibration bar cannot widen the measured ink extent of the book."""
    import deckle.cli as cli

    source = _dummy(tmp_path, 4)
    seen: list[list[bool]] = []

    def record(pages, margin_pt=0.0):
        seen.append([page.skipped for page in pages])
        return (None, None)

    monkeypatch.setattr("deckle.core.render.auto_crop_insets", record)
    out = tmp_path / "job.deckle"

    assert main([
        "impose", source, "-o", str(out), "--auto-crop", "--pages", "2-4",
    ]) == 0

    assert seen == [[True, False, False, False]], (
        "auto-crop measured pages the selection had already excluded"
    )
    assert cli is not None


def test_the_import_warnings_survive_a_page_selection(tmp_path, capsys):
    """`apply_page_selection` returns a plain list, which drops
    `ImportedPages.warnings`. The CLI rewraps them, or every mixed-DPI
    advisory vanishes the moment someone uses --pages."""
    from PIL import Image

    folder = tmp_path / "scan"
    folder.mkdir()
    for index, dpi in enumerate((72, 300)):
        image = Image.new("RGB", (200, 300), "white")
        image.save(folder / f"p{index}.png", dpi=(dpi, dpi))
    out = tmp_path / "job.deckle"

    rc = main(["impose", str(folder), "-o", str(out), "--pages", "1-2"])

    assert rc == 0
    assert "mixed_dpi" in capsys.readouterr().err


def test_schedule_under_flat_sheets_reports_the_block(capsys):
    """`Schedule.spine_width_pt` was computed for every plan and the flat
    branch of the formatter returned before printing anything about
    thickness. A perfect binder cuts boards against that number."""
    rc = main(["schedule", FIXTURE, "--paper-thickness", "0.004in"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "BINDING THE STACK" in out
    assert "Block thickness" in out
    assert "swell from the sewing thread" not in out


def test_the_cli_ignores_saved_defaults(tmp_path, monkeypatch, capsys):
    """`deckle export book.pdf` must produce the same book on two machines.

    A machine-local defaults file silently changing the paper size, gutter
    and fold scheme of every headless run is the opposite of what a
    scriptable tool is for, and it would make the golden-fixture regression
    depend on the developer's config directory. The CLI's template is a
    `.deckle` named in the invocation, which is explicit and reproducible.
    """
    from deckle.core.defaults import save_defaults
    from deckle.core.models import LayoutSettings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_defaults(LayoutSettings(
        paper=(841.89, 595.28), gutter_pt=144.0, binding_edge="right",
        fold_scheme="folio",
    ))

    assert main(["info", FIXTURE]) == 0

    out = capsys.readouterr().out
    assert "841" not in out, "the CLI read a machine-local defaults file"


def test_the_cli_module_never_reaches_for_the_defaults_store():
    """A grep, not a behaviour check: the decision above is easy to undo by
    accident and hard to notice once undone."""
    import deckle.cli

    source = open(deckle.cli.__file__, encoding="utf-8").read()

    assert "core.defaults" not in source
    assert "load_defaults" not in source
    assert "defaults_path" not in source
