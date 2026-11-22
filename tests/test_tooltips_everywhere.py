"""Every control in the app says what it does when hovered.

The 2026-11-21 consistency audit found 64 controls without a tooltip across
the main window and the four dialogs, beside panels where every control had
one: the crop boxes, the zoom buttons ("-" and "+"), every button in the
card list, the whole calibration wizard and the custom paper dialog. A
control explained in one place and silent in the next teaches people that
hovering is not worth trying.

The rule this holds: every combo box, spin box, line edit, check box and
button has a tooltip, except a dialog's standard buttons, whose label is
the whole story (:data:`STANDARD_BUTTONS`). Run in a subprocess, as the
other whole-window tests are, so a window that leaves threads behind cannot
take the suite down with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")

#: Labels that need no explanation, on any dialog.
STANDARD_BUTTONS = ("OK", "Cancel", "Save", "Back", "Next")

PROBE = r'''
import json, os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from dataclasses import replace
from PySide6.QtWidgets import (QApplication, QAbstractButton, QAbstractSpinBox,
                               QComboBox, QLabel, QLineEdit, QTabBar, QWidget)
app = QApplication([])
import deckle.app.main as am
am.MainWindow.refresh_printers = (
    lambda self, blocking=False, timeout_ms=None, quiet=False: self._apply_printers([]))
from deckle.app.workers import run_inline
from deckle.core.loader import load_pdf
from deckle.core.planning import impose_project
from deckle.core.profiles import BUILTIN_PRESETS

STANDARD = set(json.loads(sys.argv[2]))

def silent(root):
    found = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit)):
            continue
        if isinstance(w.parent(), (QAbstractSpinBox, QComboBox, QTabBar)):
            continue  # a spin box's or combo's own line edit; a tab bar's arrows
        if w.objectName().startswith("qt_"):
            continue  # Qt's own, e.g. a table's corner button
        text = w.text() if isinstance(w, QAbstractButton) else ""
        if text in STANDARD or w.toolTip():
            continue
        buddy = next((l.text() for l in root.findChildren(QLabel) if l.buddy() is w), "")
        found.append(f"{type(w).__name__} {text or buddy or w.objectName()!r}")
    return found

report = {}
window = am.MainWindow()
pages = load_pdf(sys.argv[1])
window.state.mutate(lambda p: replace(p, pages=list(pages) * 4))
window._on_imported(pages, [])
report["main window"] = silent(window.window)

from deckle.app.views.print_dialog import PrintDialog
plan = impose_project(window.state.project)
report["print dialog"] = silent(PrintDialog(
    plan, printer_names=["Printer A"],
    profile_loader=lambda n: BUILTIN_PRESETS["generic_face_down_reversed"],
    resumable_lister=lambda: [], job_runner=run_inline).widget)

from deckle.app.views.profile_editor import ProfileEditorDialog
report["profile editor"] = silent(
    ProfileEditorDialog("Brother", saver=lambda p, n: None, now=lambda: "x").widget)

from deckle.app.views.calibration_wizard import CalibrationWizard
report["calibration wizard"] = silent(CalibrationWizard(
    printer_names=["Brother"], sheet_writer=lambda *a: None, opener=lambda p: True,
    profile_loader=lambda n: None, saver=lambda p, n: None,
    ask_overwrite=lambda n: True, now=lambda: "x", sheet_dir=sys.argv[3]).widget)

from deckle.app.views.custom_paper import CustomPaperDialog
report["custom paper"] = silent(CustomPaperDialog((612.0, 792.0), unit="in").widget)

print("REPORT " + json.dumps(report))
sys.stdout.flush()
os._exit(0)
'''


def test_no_control_in_the_app_is_silent_on_hover(tmp_path, probe_env):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, FIXTURE, json.dumps(STANDARD_BUTTONS), str(tmp_path)],
        capture_output=True, text=True, timeout=120, env=probe_env,
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("REPORT ")]
    assert lines, result.stdout + result.stderr
    report = json.loads(lines[0][len("REPORT "):])

    assert report == {surface: [] for surface in report}
    assert set(report) == {"main window", "print dialog", "profile editor",
                           "calibration wizard", "custom paper"}


def test_enter_in_the_print_dialog_neither_prints_nor_opens_the_profile_editor():
    """The 2026-11-21 audit's other finding. A QDialog with no default button
    promotes its first auto-default push button when shown, which here was
    *Edit profile...*: Enter after typing a sheet range opened the editor.
    Print must not take its place -- Enter is not a request for paper."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from deckle.app.views.print_dialog import PrintDialog
    from deckle.app.workers import run_inline
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project
    from deckle.core.planning import impose_project
    from deckle.core.profiles import BUILTIN_PRESETS

    QApplication.instance() or QApplication([])
    pages = load_pdf(FIXTURE)
    plan = impose_project(Project(pages * 4, LayoutSettings((612.0, 792.0), 0.0, "left"), None))
    dialog = PrintDialog(
        plan, printer_names=["Printer A"],
        profile_loader=lambda n: BUILTIN_PRESETS["generic_face_down_reversed"],
        resumable_lister=lambda: [], job_runner=run_inline,
    )

    buttons = dialog.widget.findChildren(QPushButton)
    assert buttons
    assert [b.text() for b in buttons if b.autoDefault() or b.isDefault()] == []


# -- tab order -------------------------------------------------------------


def _layout_order(widget):
    """The input controls of ``widget`` in the order its layouts show them."""
    from PySide6.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QStackedWidget,
        QTabBar, QWidget,
    )

    kinds = (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit)
    found = []

    def visit(w):
        if isinstance(w, QTabBar):
            return
        if isinstance(w, kinds) and not isinstance(w.parent(), (QAbstractSpinBox, QComboBox)):
            found.append(w)
            return
        if isinstance(w, QStackedWidget):
            for i in range(w.count()):
                visit(w.widget(i))
            return
        layout = w.layout()
        if layout is None:
            for child in w.children():
                if isinstance(child, QWidget):
                    visit(child)
        else:
            walk(layout)

    def walk(layout):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is not None:
                visit(item.widget())
            elif item.layout() is not None:
                walk(item.layout())

    visit(widget)
    return found


def _tab_order(widget, controls):
    from PySide6.QtCore import Qt

    wanted, order = set(controls), []
    current = widget.nextInFocusChain()
    while current is not widget and len(order) < 1000:
        if current in wanted and current.focusPolicy() & Qt.FocusPolicy.TabFocus:
            order.append(current)
        current = current.nextInFocusChain()
    return list(dict.fromkeys(order))


def _surfaces(tmp_path):
    from PySide6.QtWidgets import QApplication, QTabWidget

    from deckle.app.state import AppState
    from deckle.app.views.calibration_wizard import CalibrationWizard
    from deckle.app.views.custom_paper import CustomPaperDialog
    from deckle.app.views.layout_panel import LayoutPanel
    from deckle.app.views.print_dialog import PrintDialog
    from deckle.app.views.profile_editor import ProfileEditorDialog
    from deckle.app.workers import run_inline
    from deckle.core.loader import load_pdf
    from deckle.core.models import LayoutSettings, Project
    from deckle.core.planning import impose_project
    from deckle.core.profiles import BUILTIN_PRESETS

    QApplication.instance() or QApplication([])
    project = Project(load_pdf(FIXTURE) * 4, LayoutSettings((612.0, 792.0), 0.0, "left"), None)
    yield "print dialog", PrintDialog(
        impose_project(project), printer_names=["A"],
        profile_loader=lambda n: BUILTIN_PRESETS["generic_face_down_reversed"],
        resumable_lister=lambda: [], job_runner=run_inline).widget
    yield "profile editor", ProfileEditorDialog("B", saver=lambda p, n: None, now=lambda: "x").widget
    yield "calibration wizard", CalibrationWizard(
        printer_names=["B"], sheet_writer=lambda *a: None, opener=lambda p: True,
        profile_loader=lambda n: None, saver=lambda p, n: None,
        ask_overwrite=lambda n: True, now=lambda: "x", sheet_dir=str(tmp_path)).widget
    yield "custom paper", CustomPaperDialog((612.0, 792.0), unit="in").widget
    panel = LayoutPanel(AppState(project))
    for tabs in panel.widget.findChildren(QTabWidget):
        for i in range(tabs.count()):
            yield f"layout tab {tabs.tabText(i)}", tabs.widget(i)


def test_tab_moves_through_every_form_in_the_order_it_reads(tmp_path):
    """Audited 2026-11-21 and found right everywhere; pinned so a control
    added out of order -- created after its neighbours but laid out between
    them -- is caught rather than discovered with the keyboard."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    wrong = {}
    for name, widget in _surfaces(tmp_path):
        laid_out = _layout_order(widget)
        tabbed = _tab_order(widget, laid_out)
        expected = [w for w in laid_out if w in set(tabbed)]
        if tabbed != expected:
            wrong[name] = (len(expected), len(tabbed))
    assert wrong == {}
