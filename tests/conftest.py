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
import threading

import pytest

_SCRATCH_HOME = tempfile.mkdtemp(prefix="deckle-tests-home-")

os.environ["XDG_DATA_HOME"] = os.path.join(_SCRATCH_HOME, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH_HOME, "config")
# Windows and macOS resolve both roots from this one.
os.environ["APPDATA"] = os.path.join(_SCRATCH_HOME, "appdata")

# The one that got away. The single-sheet export cache does not live under
# either root above -- it is `<temp>/deckle_export_cache`, shared by every
# Deckle process on the machine, and the suite writes into it. Two tests
# diff a listing of that directory to prove a failed export strands
# nothing, and a shared directory makes that a diff against other people's
# residue: a file an interrupted run left behind, or one that the
# directory's own 512 MB eviction pass removed between the two listings.
# Neither is the thing being asserted, and both fail the test.
#
# Set here for the same reason as the roots above rather than through a
# fixture: the export cache is written from the preview's render worker
# threads, which outlive a function-scoped monkeypatch.
os.environ["DECKLE_EXPORT_CACHE_DIR"] = os.path.join(_SCRATCH_HOME, "export-cache")

atexit.register(shutil.rmtree, _SCRATCH_HOME, True)


def cancel_pending_autosave_timers() -> int:
    """Cancel every autosave debounce still in flight.

    ``AppState`` schedules its autosave on a daemon ``threading.Timer``
    with a 500ms debounce. A test that constructs an ``AppState`` without
    injecting a synchronous timer, mutates it, and returns leaves that
    timer running: it fires half a second later, *inside whichever test
    happens to be running then*, and writes a recovery file into whatever
    data directory is in force at that moment.

    That is not hypothetical. ``tests/test_unsaved_autosave.py`` gives
    each of its tests its own data root, so a stray timer scheduled back
    in ``tests/test_ui_surface.py`` lands a fourth ``.deckle.autosave``
    inside a test that planted exactly three and asserts on all of them.
    It failed once in nine full-suite runs and was green in isolation,
    because the flake needs a 500ms timer to land inside a
    sub-millisecond test body.

    Cancelling here fixes the cause rather than the symptom: an autosave
    timer belongs to the test that scheduled it, and stops existing when
    that test does. ``AppState`` is the only thing in ``deckle/`` that
    uses ``threading.Timer`` -- ``layout_panel`` uses Qt's ``QTimer`` --
    so this cannot cancel anything else's work.

    :returns: how many timers were cancelled, so a test can prove this
        has teeth rather than trusting that it does.
    """
    cancelled = 0
    for thread in threading.enumerate():
        if isinstance(thread, threading.Timer) and thread.is_alive():
            thread.cancel()
            thread.join(timeout=2.0)
            cancelled += 1
    return cancelled


@pytest.fixture(autouse=True)
def _no_autosave_timer_outlives_its_test():
    """Stop a test's autosave debounce leaking into the next one.

    See :func:`cancel_pending_autosave_timers` for what goes wrong
    without it.
    """
    yield
    cancel_pending_autosave_timers()


@pytest.fixture
def cancel_autosave_timers():
    """Hand a test the same guard the autouse fixture runs.

    Offered as a fixture rather than imported, because
    ``tests/test_suite_imports_only_declared_dependencies.py`` reads a
    module-level ``from tests.conftest import ...`` as a dependency on an
    undeclared distribution -- and it is right to: a conftest is reached
    through pytest, not through ``sys.path``.

    :returns: :func:`cancel_pending_autosave_timers` itself, which
        returns how many timers it cancelled.
    """
    return cancel_pending_autosave_timers
