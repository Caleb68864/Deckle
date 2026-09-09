"""Keep the suite out of the developer's real application directories.

``deckle.core.paths._root`` reads ``XDG_DATA_HOME`` / ``XDG_CONFIG_HOME``
(and ``APPDATA``) at **call time**, deliberately, so that tests can point
the whole thing at a temporary directory. Until now most of them did not
have to: what landed in ``~/.local/share/deckle`` was a diagnostics line
and a cached import, both harmless.

That stopped being true when a never-saved project gained an autosave. A
test that constructs an ``AppState`` with no ``project_path`` and mutates
it now writes a recovery file into whatever data directory is in force --
and the next time the developer launches Deckle, it offers them their test
fixtures back. Worse, an offscreen probe that constructs a ``MainWindow``
would open a modal recovery prompt with nobody to answer it, and hang.

**Set at import, in ``os.environ``, rather than through an autouse
``monkeypatch`` fixture.** The autosave debounce is a daemon
``threading.Timer``: a test that does not inject a synchronous timer
leaves one in flight, it fires half a second later -- after the test
function, and therefore after a function-scoped ``monkeypatch`` has put the
environment back -- and it resolves the data directory *then*. A
per-test fixture is the intuitive shape and it leaks anyway, one file per
such test. A process-wide value does not, because the timer thread is in
this process and sees it.

A test that wants its own directory still sets one: ``monkeypatch.setenv``
inside a test wins for the duration of that test, and
``tests/test_app_directories.py``, which is *about* these variables,
deletes them itself.

Out-of-process probes are not covered by this -- a subprocess inherits the
parent's environment, so it would land in this same scratch root, which is
fine, but each one sets its own anyway so that its store is empty.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_SCRATCH_HOME = tempfile.mkdtemp(prefix="deckle-tests-home-")

os.environ["XDG_DATA_HOME"] = os.path.join(_SCRATCH_HOME, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH_HOME, "config")
# Windows and macOS resolve both roots from this one.
os.environ["APPDATA"] = os.path.join(_SCRATCH_HOME, "appdata")

atexit.register(shutil.rmtree, _SCRATCH_HOME, True)
