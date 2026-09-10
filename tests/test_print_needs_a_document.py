"""Print was live on an empty document, and the run reported success.

Ctrl+P with nothing imported opened the print dialog, walked two empty
passes, and left the status label reading **"Print job complete."** for a
job that submitted nothing. Two separate gaps met:

* ``_sync_document_actions`` gates on ``has_pages`` and names four actions
  by hand -- ``print_document`` is not among them -- while
  ``_apply_printers`` gates Print on ``has_printers`` alone. Zero printers
  deliberately leaves Deckle usable as an imposition tool that writes a
  file; the converse, zero *pages*, was never considered.
* ``PrintDialog.print_proof`` handles the empty plan (*"Nothing to proof
  -- this plan has no sheets."*) and ``start_print`` did not.

Both are fixed and both are pinned, because either alone leaves the other
reachable: the dialog is constructible from a saved project, and the
button is not the only way in.

Checked and left alone, because they are correct: ``rotate_selection``,
``skip_selection`` and ``insert_blank`` also stay enabled with no pages.
The first two are no-ops on an empty selection and ``blank_insert_choices``
handles zero pages deliberately -- *"An empty document still offers one
position"*. Print was the only one of the four that misbehaved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import SheetPlan  # noqa: E402
from deckle.core.profiles import PrinterProfile  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _empty_plan() -> SheetPlan:
    return SheetPlan(sheets=[], paper_pt=(612.0, 792.0), warnings=[])


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1, flip_axis="long", output_face="down", feed_edge="top",
        reverse_stack=True, imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00", calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


class _StubBackend:
    def __init__(self, profile):
        self.profile = profile

    def submit(self, plan, sheets, printer_name, copies, dpi, **_kwargs):
        from deckle.core.printing import PrintResult

        return PrintResult(submitted=len(sheets), job_id=None, error=None)


def _recording_session_cls():
    """A ``PrintSession`` stand-in that records having been built at all.

    That is the whole assertion. The defect was not that the session
    misbehaved -- it walked two empty passes perfectly correctly and
    reported that it had finished. It is that it was constructed for a
    document with nothing in it.

    :returns: ``(built, cls)``; ``built`` fills with one entry per
        session the dialog constructs.
    """
    built: list = []

    class _Session:
        def __init__(self, plan, profile, backend, **kwargs):
            self.plan = plan
            built.append(self)

        def start(self) -> None:
            return None

        def advance(self) -> None:
            return None

        @property
        def finished(self) -> bool:
            return True

        @property
        def last_error(self):
            return None

        @property
        def reload_instruction(self):
            return None

        @property
        def state(self) -> dict:
            return {"pass_index": 0, "test_sheet_pending": False}

        @staticmethod
        def list_resumable():
            return []

    return built, _Session


def _dialog(plan, session_cls):
    from deckle.app.views.print_dialog import PrintDialog

    return PrintDialog(
        plan,
        printer_names=["Printer A"],
        profile_loader=lambda name: _profile(),
        session_cls=session_cls,
        backend_cls=_StubBackend,
        resumable_lister=lambda: [],
        confirm_reload=lambda instruction: None,
        confirm_test_sheet=lambda: True,
        show_offline_error=lambda printer, error: None,
    )


# -- the dialog ----------------------------------------------------------


def test_the_dialog_refuses_a_plan_with_no_sheets():
    built, session_cls = _recording_session_cls()
    dialog = _dialog(_empty_plan(), session_cls)

    dialog.start_print()

    assert built == [], "a print session was started for a document with no pages"


def test_it_does_not_report_a_completed_job_that_printed_nothing():
    """The worst part of the old behaviour. "Print job complete." for a
    run that submitted nothing is not a cosmetic problem: it is the app
    telling the operator that paper came out."""
    built, session_cls = _recording_session_cls()
    dialog = _dialog(_empty_plan(), session_cls)

    dialog.start_print()

    assert "complete" not in dialog.status_label.text().lower(), (
        dialog.status_label.text()
    )
    assert "no sheets" in dialog.status_label.text().lower(), dialog.status_label.text()


def test_a_plan_with_sheets_still_prints():
    """The other half of the gate, so "refuse everything" would fail too."""
    from deckle.core.models import OutputPage, Placement, Sheet, Side

    page = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,)), back=Side(pages=(page,)))],
        paper_pt=(612.0, 792.0),
        warnings=[],
    )
    built, session_cls = _recording_session_cls()
    dialog = _dialog(plan, session_cls)

    dialog.start_print()

    assert len(built) == 1


# -- the window ----------------------------------------------------------

PROBE = '''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

from dataclasses import replace
import deckle.app.main as am
import deckle.app.printer_query as pq
from deckle.core.loader import load_pdf

# One printer, synchronously. The question here is pages, and an
# enumeration thread with no event loop to finish on would never answer.
pq.available_printer_names = lambda: ["Fake Printer"]
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None: self._apply_printers(["Fake Printer"])
)

fixture = sys.argv[1]
report = {}
window = am.MainWindow()

report["printers"] = list(window._printers)
report["pages_at_start"] = len(window.state.project.pages)
report["print_enabled_empty"] = window.print_button.isEnabled()
report["menu_print_enabled_empty"] = window.menu_actions["print_document"].isEnabled()
report["print_tooltip_empty"] = window.print_button.toolTip()

# The button is not the only way in -- a shortcut, a menu, or a later
# refactor can reach the slot directly, so the slot guards too.
opened = []


class _FakeDialog:
    """Complete enough that reaching it is not itself a crash.

    A stub that blew up on use would prove the guard is missing by
    killing the probe, and a probe that dies reports as an error with no
    findings rather than as the specific thing that is wrong.
    """

    def __init__(self, *args, **kwargs):
        opened.append((args, kwargs))
        self.widget = type("W", (), {"exec": staticmethod(lambda: None)})()

    def selected_profile(self):
        return window.profile


am.PrintDialog = _FakeDialog
window.print_document()
report["dialog_built_on_empty"] = bool(opened)
report["status_on_empty"] = window.status_bar.currentMessage()

# ...and with a document it all comes back.
pages = load_pdf(fixture)
window.state.mutate(lambda project: replace(project, pages=list(pages)))
window._sync_document_actions()
report["pages_after_import"] = len(window.state.project.pages)
report["print_enabled_with_pages"] = window.print_button.isEnabled()
report["menu_print_enabled_with_pages"] = (
    window.menu_actions["print_document"].isEnabled()
)
report["print_tooltip_with_pages"] = window.print_button.toolTip()

print(json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    pytest.importorskip("PySide6")
    if not os.path.exists(FIXTURE):
        pytest.skip("tests/fixtures/sample.pdf is missing")
    workdir = tmp_path_factory.mktemp("print_needs_a_document")

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["DECKLE_LOG_DIR"] = str(workdir / "logs")
    # Its own data root, like the other window probes: a never-saved
    # autosave left in the shared scratch root by any other test makes a
    # fresh MainWindow open a recovery modal nothing here can answer.
    data_root = str(workdir / "data")
    env["XDG_DATA_HOME"] = data_root
    env["XDG_CONFIG_HOME"] = str(workdir / "config")
    env["APPDATA"] = data_root
    env["DECKLE_EXPORT_CACHE_DIR"] = str(workdir / "export-cache")

    result = subprocess.run(
        [sys.executable, "-u", "-c", PROBE, FIXTURE],
        capture_output=True, text=True, env=env, timeout=300, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"the probe did not finish (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_probe_had_a_printer_and_no_document(report):
    """The premise, asserted before anything is concluded from it. With no
    printer, Print would be disabled for the other reason and every
    assertion below would be true for nothing."""
    assert report["printers"] == ["Fake Printer"]
    assert report["pages_at_start"] == 0


def test_print_is_disabled_with_nothing_imported(report):
    assert report["print_enabled_empty"] is False
    assert report["menu_print_enabled_empty"] is False


def test_the_disabled_button_says_why(report):
    """A greyed control with no tooltip is indistinguishable from a broken
    one. The printer case already explained itself; this one did not
    exist."""
    assert report["print_tooltip_empty"], "no tooltip on a disabled Print button"


def test_reaching_the_slot_anyway_opens_no_dialog(report):
    assert report["dialog_built_on_empty"] is False
    assert report["status_on_empty"]


def test_print_comes_back_with_a_document(report):
    assert report["pages_after_import"] > 0
    assert report["print_enabled_with_pages"] is True
    assert report["menu_print_enabled_with_pages"] is True
    assert report["print_tooltip_with_pages"] == ""
