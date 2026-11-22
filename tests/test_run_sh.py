"""Structural tests for ``run.sh``, the Linux counterpart of ``run.bat``.

F13 (Linux as a target; decided by agent 2026-11-21, owner may revisit).
``run.sh`` arrived with the trading-card work and had no test at all, while
``run.bat`` has had one since a generated edit spliced its packager into the
dispatch table and a bare ``run.bat`` started building executables. A shell
script, like a batch file, has no import to fail and no exception to raise,
so its shape is what is asserted -- plus, here, that it actually runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "run.sh"

#: run.bat's subcommands, read from its own dispatch table rather than
#: imported from its test, so the comparison is between the two launchers.
SUBCOMMANDS = tuple(
    name for name in re.findall(
        r'if /i "%CMD%"=="(\w+)"\s+goto :\1\b',
        (ROOT / "run.bat").read_text(encoding="utf-8"),
    )
    if name != "help"
)


def _text() -> str:
    return RUN_SH.read_text(encoding="utf-8")


def _arms() -> dict[str, str]:
    """``case`` arm name -> its body, for the one top-level dispatch."""
    body = _text().split('case "$CMD" in', 1)[1].split("\nesac", 1)[0]
    arms: dict[str, str] = {}
    for match in re.finditer(r"^    ([^\s()][^)]*)\)\n(.*?)^        ;;", body, re.M | re.S):
        for name in match.group(1).split("|"):
            arms[name.strip().strip('"')] = match.group(2)
    return arms


def test_run_sh_exists_and_is_executable():
    assert RUN_SH.exists()
    assert os.access(RUN_SH, os.X_OK)


def test_it_is_tracked_as_executable():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    out = subprocess.run(["git", "ls-files", "-s", "run.sh"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    if not out:
        pytest.skip("not a git checkout")
    assert out.startswith("100755")


def test_the_two_launchers_offer_the_same_subcommands():
    """The test that keeps them from drifting apart."""
    assert set(SUBCOMMANDS) == {"cli", "test", "deps", "doctor", "docs", "package"}
    arms = _arms()
    missing = [name for name in SUBCOMMANDS if name not in arms]
    assert missing == []


def test_every_subcommand_it_documents_is_dispatched():
    documented = set(re.findall(r"^  \./run\.sh (\w+)", _text(), re.M))
    assert documented >= set(SUBCOMMANDS)
    assert documented - set(_arms()) == set()


def test_a_bare_invocation_launches_the_gui_and_nothing_else():
    bare = _arms()[""]
    assert "-m deckle\n" in bare
    assert "PyInstaller" not in bare


def test_the_test_arm_runs_offscreen_unless_told_otherwise():
    assert 'QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"' in _arms()["test"]


def test_the_deps_arm_installs_the_declared_dev_extra():
    assert ".[dev]" in _arms()["deps"]


def test_the_doctor_arm_bounds_the_printer_query():
    """Printer enumeration once blocked for 81 minutes on an unreachable
    network printer. A launcher that reproduces that is worse than none."""
    doctor = _arms()["doctor"]
    assert re.search(r"timeout \d+ ", doctor)
    assert "QPrinterInfo" in doctor.split("timeout", 1)[1]


def test_the_doctor_arm_says_which_display_qt_would_use():
    doctor = _arms()["doctor"]
    assert "DISPLAY" in doctor and "WAYLAND_DISPLAY" in doctor


def test_the_package_arm_archives_only_after_the_audit():
    package = _arms()["package"]
    audit = package.index("test_packaging_audit.py")
    archive = package.index("tar -czf")
    assert audit < archive
    assert "-linux-$(uname -m).tar.gz" in package


def test_it_prefers_a_local_virtualenv():
    text = _text()
    assert text.index(".venv/bin/python") < text.index("venv/bin/python", text.index(".venv/bin/python") + 1)
    assert text.index('PYTHON="python3"') < text.index(".venv/bin/python")


def test_an_unknown_subcommand_fails():
    result = subprocess.run([str(RUN_SH), "nonsense"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1
    assert "unknown command" in result.stderr


def test_it_actually_runs_the_cli():
    result = subprocess.run([str(RUN_SH), "cli", "--version"], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert "deckle" in (result.stdout + result.stderr).lower()


def test_doctor_returns_promptly():
    started = time.monotonic()
    result = subprocess.run([str(RUN_SH), "doctor"], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert time.monotonic() - started < 30
    assert "printers visible to Qt" in result.stdout


def test_shellcheck_is_clean():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    result = subprocess.run(["shellcheck", str(RUN_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
