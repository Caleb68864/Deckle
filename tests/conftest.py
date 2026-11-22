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

Out-of-process probes are not covered by this. A subprocess inherits the
parent's environment and so lands in this same scratch root, which is shared
with every test in the run. An autosave debounce that fires before its test
ends leaves a recovery file there, and a probe's ``MainWindow()`` then blocks
for good in the modal prompt offering it back. So a probe takes its own data
root, through :func:`probe_env` or by hand, and
``tests/test_probes_own_their_store.py`` holds every probe to that.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def settle_qt_threads(timeout_ms: int = 10000) -> int:
    """Wait for every ``QThread`` still running under a top-level widget.

    The views parent their render threads to their own widget (see
    ``deckle.app.shutdown.live_threads``), and the app waits for them in
    ``MainWindow.stop_background_work`` when it closes. A test that builds
    an ``ArrangeView`` and simply ends does neither. The view's thumbnail
    render keeps running, the widget lives on in the module-level
    ``QApplication``, and at interpreter exit PySide deletes the
    application, the widget, and the thread inside pdfium with it. Qt
    answers ``QThread: Destroyed while thread is still running`` with a
    fatal abort: exit 134, after every test had passed. It hit about one
    run in five of ``test_blank_insertion.py``, ``test_gui_workflow.py``
    and ``test_arrange_reorder.py`` together, depending only on whether
    the last view's render had finished.

    A no-op until something has imported Qt, so a Qt-free test pays
    nothing.

    :param timeout_ms: how long to wait for each thread.
    :returns: how many threads were still running, so a check can prove
        this has teeth.
    """
    if "PySide6.QtWidgets" not in sys.modules:
        return 0
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return 0
    running = 0
    for widget in app.topLevelWidgets():
        for thread in widget.findChildren(QThread):
            if thread.isRunning():
                running += 1
                thread.wait(timeout_ms)
    return running


@pytest.fixture(autouse=True)
def _no_qt_thread_outlives_its_test():
    """See :func:`settle_qt_threads`."""
    yield
    settle_qt_threads()


def pytest_sessionfinish(session, exitstatus):
    """Say so if a render thread is still running when the session ends.

    The autouse fixture should have left none. If one survives, it is the
    thread that would abort the process at exit, and a message here names
    the cause where a bare exit 134 names nothing.
    """
    leaked = settle_qt_threads(timeout_ms=30000)
    if leaked:
        print(f"\nconftest: {leaked} QThread(s) were still running at session end "
              "and were waited for; a test is not settling its views.", file=sys.stderr)


@pytest.fixture
def qt_thread_settler():
    """:func:`settle_qt_threads`, for a test that checks it (see
    :func:`cancel_autosave_timers` for why it is a fixture)."""
    return settle_qt_threads


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


@pytest.fixture
def probe_env(tmp_path):
    """The environment for a subprocess that opens a real window.

    Its data root is its own, so its recovery store is empty whatever else ran
    first. See the module docstring for the hang this prevents.

    :returns: a copy of ``os.environ`` with the probe's settings applied.
    """
    data_root = str(tmp_path / "probe-data")
    return {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": REPO_ROOT,
        "XDG_DATA_HOME": data_root,
        "APPDATA": data_root,
    }


@pytest.fixture
def torn_write():
    """The next file written under this context dies halfway through.

    The output-durability tests share this rather than each keeping a copy,
    because the copies went wrong together. Two doors have to be guarded:

    - **Python's ``open``.** Both ``io.open`` and ``builtins.open``: they are
      two names for the same behaviour and code reaches them differently, so
      arming one leaves the other live.
    - **``pikepdf.Pdf.save``.** pikepdf hands qpdf the file descriptor where
      it can, so C++ does the writing and no Python ``write`` is ever called.
      Wrapping the handle's ``write`` caught PDFs only for as long as pikepdf
      happened to write through Python (10.12 did, 10.17 does not). Once it
      stopped, the fault silently never fired, the "failed" export succeeded,
      and the tests reported a durability bug that did not exist. The tear
      happens *on whatever target
      ``save`` was handed*, so a writer aimed straight at the user's file
      still loses it; this is not the injection-at-the-serialiser mistake
      that ``tests/test_cli_output_durability.py`` warns about.

    **Leaving the context without a tear fails the test.** A fault that did
    not fire proves nothing, and that is the failure this fixture must not be
    able to have again: on a run where the PDF save slipped past, the
    no-debris tests for ``export`` and ``dummy`` were passing without any
    write having failed. A real kernel-level fault, with no patching at all,
    is exercised by ``test_a_real_write_failure_keeps_the_previous_pdf``.
    """
    import builtins
    import contextlib
    import io

    import pikepdf

    real_io, real_builtin, real_save = io.open, builtins.open, pikepdf.Pdf.save

    @contextlib.contextmanager
    def armed():
        fired = []

        def die():
            fired.append(True)
            return OSError(28, "No space left on device")

        def wrap(real):
            def guarded(file, mode="r", *args, **kwargs):
                handle = real(file, mode, *args, **kwargs)
                if "w" in mode:
                    write = handle.write

                    def half_then_die(data):
                        write(data[: len(data) // 2])
                        handle.flush()
                        raise die()

                    handle.write = half_then_die
                return handle
            return guarded

        def tearing_save(self, target, *args, **kwargs):
            whole = io.BytesIO()
            real_save(self, whole, *args, **kwargs)
            half = whole.getvalue()[: len(whole.getvalue()) // 2]
            if hasattr(target, "write"):
                target.write(half)
                target.flush()
            else:
                with real_builtin(target, "wb") as handle:
                    handle.write(half)
            raise die()

        io.open, builtins.open = wrap(real_io), wrap(real_builtin)
        pikepdf.Pdf.save = tearing_save
        try:
            yield
        finally:
            io.open, builtins.open = real_io, real_builtin
            pikepdf.Pdf.save = real_save
        assert fired, "torn_write never fired: nothing here tested a failed write"

    return armed
