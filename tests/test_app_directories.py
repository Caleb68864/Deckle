"""Config and data roots, decided in one place.

`paths` was extracted on 2026-08-06 because the three-way platform answer
existed twice and two copies could drift, leaving a user's profiles
somewhere their recent list was not. There was a third copy:
``session_log._data_dir`` carried the same ladder, differing only in
which XDG variable Linux consults.

The difference is real and had to survive the consolidation. On Windows
and macOS settings and data live under one root, so the two answers are
identical; on Linux XDG separates settings a user might edit or copy
between machines from data an application accumulates, and a log is the
second kind. So this is two functions over one ladder, rather than one
function or two ladders.

Both are tested per platform rather than on whichever machine happens to
run the suite, because the whole point of the module is the branch --
running it on Windows only would leave the XDG half unexercised, which is
precisely the half where the two roots differ.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deckle.core.paths import config_dir, data_dir
from deckle.core.session_log import session_log_path


@pytest.fixture
def on_platform(monkeypatch):
    def use(name: str, **env):
        monkeypatch.setattr("deckle.core.paths.sys.platform", name)
        for key in ("APPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                    "DECKLE_SESSION_LOG_DIR"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    return use


def test_linux_keeps_settings_and_data_apart(on_platform):
    """The one platform where the split is real, and the reason the
    consolidation could not simply collapse the two."""
    on_platform("linux", XDG_CONFIG_HOME="/cfg", XDG_DATA_HOME="/dat")

    assert config_dir() == Path("/cfg/deckle")
    assert data_dir() == Path("/dat/deckle")


def test_linux_falls_back_to_the_xdg_defaults(on_platform):
    on_platform("linux")

    assert config_dir() == Path.home() / ".config" / "deckle"
    assert data_dir() == Path.home() / ".local" / "share" / "deckle"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macos_use_one_root_for_both(on_platform, platform):
    """Asserted rather than assumed: if these ever diverge, a user's log
    moves without anyone deciding that it should."""
    on_platform(platform, APPDATA=r"C:\\Roaming")

    assert config_dir() == data_dir()


def test_windows_honours_appdata(on_platform):
    on_platform("win32", APPDATA=r"C:\\Roaming")

    assert config_dir() == Path(r"C:\\Roaming") / "Deckle"


def test_sub_paths_hang_off_the_right_root(on_platform):
    on_platform("linux", XDG_CONFIG_HOME="/cfg", XDG_DATA_HOME="/dat")

    assert config_dir("printer_profiles", "ink.json") == Path(
        "/cfg/deckle/printer_profiles/ink.json"
    )
    assert data_dir("session_log.jsonl") == Path("/dat/deckle/session_log.jsonl")


def test_the_session_log_lives_on_the_data_side(on_platform):
    """The reason ``data_dir`` exists at all. A log is not a setting, and
    on Linux that is not a stylistic distinction -- it decides whether the
    file follows a user's dotfiles between machines."""
    on_platform("linux", XDG_CONFIG_HOME="/cfg", XDG_DATA_HOME="/dat")

    assert session_log_path() == Path("/dat/deckle/session_log.jsonl")


def test_the_session_log_override_still_wins(on_platform):
    """Support requests and tests need to put the log somewhere without
    touching the platform question."""
    on_platform("linux", XDG_DATA_HOME="/dat", DECKLE_SESSION_LOG_DIR="/tmp/logs")

    assert session_log_path() == Path("/tmp/logs/session_log.jsonl")


def test_nothing_is_created_by_asking(on_platform, tmp_path):
    """Both roots are pure answers. A caller that only reads must tolerate
    the directory not existing, and asking must not be what creates it."""
    on_platform("linux", XDG_CONFIG_HOME=str(tmp_path / "c"),
                XDG_DATA_HOME=str(tmp_path / "d"))

    config_dir("profiles")
    data_dir("logs")

    assert not (tmp_path / "c").exists()
    assert not (tmp_path / "d").exists()
