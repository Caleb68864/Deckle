"""What Deckle says about itself, and where it keeps its log.

The GUIDE tells anyone reporting a problem to send two things: the output
of ``deckle --version`` and the JSON Lines diagnostic log. The CLI could
produce the first; nothing could point at the second, so a desktop user
following those instructions had to be talked through their operating
system's data directory by hand.

Both answers now come from ``deckle.core.about``, which is also what
``--version`` prints -- so a report built in the app is comparable with a
report built at the shell.

``open_diagnostics_folder`` is exercised against a stand-in window rather
than a real one, the way ``tests/test_hardening_printing.py`` drives the
printer paths: constructing a ``QMainWindow`` under pytest on this machine
kills the process outright.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import deckle.app.main as app_main
from deckle import __version__
from deckle.core import about


def test_the_version_string_names_deckle_and_its_engines():
    text = about.version_string()
    assert text.splitlines()[0] == f"deckle {__version__}"
    for dist in ("pikepdf", "pypdfium2", "img2pdf", "PySide6"):
        assert dist in text


def test_the_cli_and_the_about_box_read_the_same_version():
    """Two spellings of a version number is two bug reports that cannot be
    compared."""
    from deckle.cli import _version_string

    assert _version_string() == about.version_string()


def test_a_missing_dependency_is_a_fact_not_an_error():
    """The CLI ships without Qt, so ``PySide6 not installed`` is what a
    working headless installation says about itself."""
    assert about.distribution_version("no-such-distribution-xyz") == (
        about.NOT_INSTALLED
    )


def test_the_about_box_names_the_log_the_guide_asks_for():
    text = about.about_text("/somewhere/diagnostics.jsonl")
    assert "/somewhere/diagnostics.jsonl" in text
    assert "Open diagnostics folder" in text


def test_the_about_box_carries_the_versions_a_report_needs():
    text = about.about_text("/somewhere/diagnostics.jsonl")
    for line in about.version_lines():
        assert line in text
    assert about.platform_line() in text


def test_the_about_box_promises_nothing_that_touches_the_network():
    """Deckle is a program for a room with a printer in it. There is no
    update check to offer and no URL to publish, and an About box that
    quietly listed one would be the first network surface in the app."""
    text = about.about_text("/somewhere/diagnostics.jsonl")
    assert not re.search(r"https?://", text), (
        "the About box publishes a URL; the next step from there is a "
        "button that fetches it"
    )
    assert "offline" in text.lower()
    assert "never contacts the network" in text.lower()


def test_asking_deckle_its_version_pulls_in_nothing_that_can_reach_out():
    """The same shape as ``tests/test_core_purity.py``'s Qt check, for the
    module the About box and ``--version`` are both built on: importing it
    must not load a network stack."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import deckle.core.about; "
            "print([m for m in sys.modules if m.split('.')[0] in "
            "{'urllib', 'http', 'socket', 'ssl', 'requests', 'httpx'}])",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing deckle.core.about loaded {result.stdout.strip()}"
    )


def test_opening_the_diagnostics_folder_asks_for_the_log_directory(monkeypatch, tmp_path):
    """The point of the menu entry: the user is shown the directory the
    log is actually written to, not a guess at where their OS puts it."""
    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path / "logs"))
    asked: list[str] = []
    monkeypatch.setattr(
        app_main, "_open_folder", lambda path: asked.append(path) or True
    )

    window = SimpleNamespace(status_bar=_FakeStatusBar())
    assert app_main.MainWindow.open_diagnostics_folder(window) is True

    assert asked == [str(tmp_path / "logs")]
    assert str(tmp_path / "logs") in window.status_bar.message


def test_the_folder_is_created_so_it_can_be_opened_before_anything_is_logged(
    monkeypatch, tmp_path
):
    """A user asking where the log lives before anything has gone wrong
    should be shown an empty folder, not told there isn't one."""
    target = tmp_path / "never-written"
    monkeypatch.setenv("DECKLE_LOG_DIR", str(target))
    monkeypatch.setattr(app_main, "_open_folder", lambda path: True)

    app_main.MainWindow.open_diagnostics_folder(
        SimpleNamespace(status_bar=_FakeStatusBar())
    )

    assert target.is_dir()


def test_a_desktop_that_refuses_still_tells_the_user_the_path(monkeypatch, tmp_path):
    """A machine with no file manager -- a bare window manager, a locked
    kiosk -- must not leave the user with nothing. The path is the answer
    they needed; the folder opening was only a convenience."""
    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_main, "_open_folder", lambda path: False)

    window = SimpleNamespace(status_bar=_FakeStatusBar())
    assert app_main.MainWindow.open_diagnostics_folder(window) is False
    assert str(tmp_path / "logs") in window.status_bar.message


class _FakeStatusBar:
    def __init__(self) -> None:
        self.message = ""

    def showMessage(self, text: str) -> None:  # noqa: N802 - Qt naming
        self.message = text

    def clearMessage(self) -> None:  # noqa: N802 - Qt naming
        self.message = ""


@pytest.fixture(autouse=True)
def _isolate_diagnostics():
    """Never write into a developer's real log directory."""
    from deckle.core import diagnostics

    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()
