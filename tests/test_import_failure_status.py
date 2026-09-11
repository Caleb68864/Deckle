"""A failed import was the one failure that never reached the status bar.

``ImportView`` declares two outcomes and emits both: ``imported`` carries
the pages, ``failed`` carries an error message. ``MainWindow.__init__``
connected the first and not the second, so a failing import updated the
import row's own small label and nothing else -- while the status bar went
on saying *"Import a PDF or a folder of images to begin."*, an instruction
the user had just tried to carry out and been refused for.

Every other failure in the app already reaches the status bar: a schedule
that could not be saved, a project whose source is missing or has changed
(``project_actions.py``), a drop of something Deckle does not take. Import
was the gap, and it is the *first* thing anyone does.

**Out of process**, like ``test_gui_shell.py``, ``test_gui_workflow.py``,
``test_shutdown.py`` and ``test_open_project_flush.py``, and for a reason
that was measured here rather than inherited. The second scan cleared
in-process ``MainWindow`` construction -- "constructs in-process,
offscreen, in about a second" -- and that is true *standalone*. It is not
true inside the full suite: an in-process version of this file passed
alone in 7.7s and hung the run at around 37%, because a `MainWindow`
built after other tests have written into the shared scratch data root
finds a never-saved autosave and offers it back through a modal that
nothing in a pytest process is going to answer. Its own data root, and
its own process, is what makes that impossible rather than unlikely.

One probe answers every question, so the suite pays for one window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MESSAGE = "Cannot import /scans/book.pdf: it is not a PDF."
ADVISORY = (
    "4 file(s) in '/scans' were not imported because they are not images: "
    "Thumbs.db, notes.txt, readme.md, scan.tiffx"
)

PROBE = '''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

import deckle.app.main as am
import deckle.app.printer_query as pq

# No enumeration thread: this probe has no event loop, and the point here
# is the status bar, which `_apply_printers` also writes.
pq.available_printer_names = lambda: []
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers([])
)

message = sys.argv[1]
ADVISORY = sys.argv[2]
report = {}
window = am.MainWindow()

# Qt's SIGNAL() spelling for `failed`, which is `Signal(str)`.
# `QObject.receivers` takes this string form; a SignalInstance is not an
# accepted argument.
report["failed_receivers"] = window.import_view._signals.receivers(
    "2failed(QString)"
)
report["no_document_message"] = am.NO_DOCUMENT_MESSAGE
report["no_printers_message"] = am.NO_PRINTERS_MESSAGE
report["before"] = window.status_bar.currentMessage()

# A direct connection on one thread runs the slot inside emit(), so no
# event loop is needed for any of this.
window.import_view.failed.emit(message)
report["after_failure"] = window.status_bar.currentMessage()

# Printer enumeration finishes on a background thread and ends in a
# `_refresh_status_message()`. A failure written straight to the bar would
# be wiped by that, with nothing left to say it ever appeared.
window._refresh_status_message()
report["after_a_refresh"] = window.status_bar.currentMessage()

# The rung below it: a printer fault is a standing condition, a refused
# import is the answer to something the user did a second ago. Back to no
# outstanding failure first, so `printer_fault_alone` is measured in the
# state its name claims.
window._import_message = ""
window._printer_message = am.NO_PRINTERS_MESSAGE
window._refresh_status_message()
report["printer_fault_alone"] = window.status_bar.currentMessage()
window.import_view.failed.emit(message)
report["failure_over_printer_fault"] = window.status_bar.currentMessage()

# ...and it is the newest thing only until something newer happens.
window.import_view.imported.emit([], [])
report["after_a_success"] = window.status_bar.currentMessage()

# An import that SUCCEEDED while leaving files behind. `imported` carries
# the loader's advisories and `_on_imported` read neither argument, so this
# is the case that reached the user on the command line and nowhere else.
from deckle.core.models import LayoutWarning

advisory = LayoutWarning(
    sheet_index=0,
    kind="skipped_non_image_files",
    detail=ADVISORY,
)
window.import_view.imported.emit([], [advisory])
report["after_an_advisory"] = window.status_bar.currentMessage()
window._refresh_status_message()
report["advisory_after_a_refresh"] = window.status_bar.currentMessage()
window.import_view.imported.emit([], [])
report["after_a_clean_import"] = window.status_bar.currentMessage()

print(json.dumps(report))
sys.stdout.flush()
# Leave without unwinding, like the other probes: the offscreen platform
# dies during interpreter teardown and would turn a completed run into a
# spurious failure.
os._exit(0)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    pytest.importorskip("PySide6")
    workdir = tmp_path_factory.mktemp("import_failure_status")

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["DECKLE_LOG_DIR"] = str(workdir / "logs")
    # Its own data root. The suite's scratch root is shared with every
    # other test in the run, and anything that leaves a never-saved
    # autosave there makes a fresh MainWindow open a recovery modal this
    # probe has no event loop to answer -- which is a hang, not a failure.
    data_root = str(workdir / "data")
    env["XDG_DATA_HOME"] = data_root
    env["XDG_CONFIG_HOME"] = str(workdir / "config")
    env["APPDATA"] = data_root
    env["DECKLE_EXPORT_CACHE_DIR"] = str(workdir / "export-cache")

    result = subprocess.run(
        [sys.executable, "-u", "-c", PROBE, MESSAGE, ADVISORY],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"the probe did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_window_is_listening_to_a_failed_import(report):
    """Asserted before the behaviour, so a green result below cannot come
    from a signal nobody is connected to in a window nobody built."""
    assert report["failed_receivers"] >= 1, (
        "ImportView.failed is emitted and connected to nothing; the only "
        "thing a failed import updates is the import row's own label"
    )


def test_a_failed_import_says_so_in_the_status_bar(report):
    assert report["after_failure"] == MESSAGE


def test_the_next_step_hint_is_not_what_a_failure_leaves_up(report):
    """The specific misbehaviour, named.

    ``_refresh_status_message`` shows *"Import a PDF or a folder of images
    to begin."* whenever the document is empty -- which it still is after
    an import fails. Telling someone to do the thing they just did and
    were refused for is worse than saying nothing.
    """
    assert report["before"] == report["no_document_message"], (
        "the probe did not reach the state this test is about"
    )
    assert report["after_failure"] != report["no_document_message"]


def test_the_next_status_refresh_does_not_wipe_it(report):
    """The race that makes a bare ``showMessage`` the wrong fix.

    Printer enumeration runs on a background thread and ends in
    ``_apply_printers``, which calls ``_refresh_status_message``. An import
    that failed while that query was in flight would have its message
    replaced a moment later by an unrelated one. So the failure is *state*
    the refresh reads, not a message written past it.
    """
    assert report["after_a_refresh"] == MESSAGE


def test_it_outranks_the_printer_fault_while_it_is_the_newest_thing(report):
    """``_refresh_status_message`` documents its own precedence, and this
    adds a rung above the top one. A printer fault is a standing condition
    the user cannot otherwise see; a refused import is an answer to
    something they did a second ago, and it is the only one of the two
    they can act on with no document loaded."""
    assert report["printer_fault_alone"] == report["no_printers_message"], (
        "the probe did not reach the state this test is about"
    )
    assert report["failure_over_printer_fault"] == MESSAGE


def test_a_successful_import_clears_it(report):
    """It is the *newest* thing only until something newer happens. An
    error about a file the user has since replaced is worse than silence,
    and the printer fault underneath it has to come back."""
    assert report["after_a_success"] == report["no_printers_message"]


# -- the import that succeeded and still had something to say -------------


def test_an_import_that_left_files_behind_says_so(report):
    """The other half of ``imported``, which nothing read.

    ``ImportView`` emits ``(pages, warnings)`` and
    ``load_and_apply_import`` documents the second one as being there "so
    a caller can report what it just added". ``_on_imported`` took both
    and read neither, so a folder of scans containing four files Deckle
    will not take imported three pages and said nothing at all -- while
    ``deckle-cli`` printed the same advisory for the same folder.

    It is not cosmetic: those are pages the user believes they scanned,
    and the plan, the preview and the exported PDF are all consistent
    around the gap. The way to find out is to count the printed book.
    """
    assert report["after_an_advisory"] == ADVISORY


def test_the_advisory_survives_the_next_status_refresh(report):
    """Same race as the failure above: printer enumeration finishes on a
    background thread and ends in ``_refresh_status_message``, so this has
    to be state the refresh reads rather than a message written past it."""
    assert report["advisory_after_a_refresh"] == ADVISORY


def test_an_import_with_nothing_to_report_clears_the_advisory(report):
    """And the printer fault underneath comes back, exactly as it does
    after a refusal. An advisory about a folder the user has since
    replaced is worse than silence."""
    assert report["after_a_clean_import"] == report["no_printers_message"]
