"""A probe that opens a real window must not share the suite's recovery store.

Three probes did, and hung until their timeout whenever an earlier test's
autosave debounce fired before that test ended: ``MainWindow()`` offered the
file back in a modal prompt, and a subprocess has nobody to answer one. It
needed a stall of 500ms at the wrong moment, so it looked like a slow runner.
"""

import glob
import json
import os

from deckle.app.state import unsaved_autosave_offers

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def test_every_window_probe_takes_its_own_data_root():
    unisolated = []
    for path in glob.glob(os.path.join(TESTS_DIR, "test_*.py")):
        if os.path.basename(path) == os.path.basename(__file__):
            continue
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        if "MainWindow()" not in source or "subprocess." not in source:
            continue
        if "XDG_DATA_HOME" not in source and "probe_env" not in source:
            unisolated.append(os.path.basename(path))
    assert unisolated == []


def test_probe_env_does_not_see_an_autosave_in_the_shared_store(tmp_path, monkeypatch, request):
    shared = tmp_path / "shared"
    store = shared / "deckle" / "autosave"
    store.mkdir(parents=True)
    (store / "stray.deckle.autosave").write_text(json.dumps({"pages": [{"path": "/x/stray.pdf"}]}))
    monkeypatch.setenv("XDG_DATA_HOME", str(shared))
    monkeypatch.setenv("APPDATA", str(shared))
    assert len(unsaved_autosave_offers()) == 1, "the planted autosave must count as an offer"

    env = request.getfixturevalue("probe_env")
    monkeypatch.setenv("XDG_DATA_HOME", env["XDG_DATA_HOME"])
    monkeypatch.setenv("APPDATA", env["APPDATA"])

    assert unsaved_autosave_offers() == []
