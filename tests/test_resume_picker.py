"""Two interrupted jobs, and the operator could reach only one of them.

``_default_confirm_resume`` took ``resumable[0]`` and showed a Yes/No box
naming the printer and nothing else. ``list_resumable`` returned
``sorted(directory.glob("*.json"))``, and the filename is a SHA-256 of
``plan_hash:printer:started_at:uuid4`` -- so "the first one" was
effectively random. With two interrupted runs the operator was offered an
arbitrary one, could not reach the other, and was told nothing that would
let them tell the two apart.

``SessionSummary`` has carried ``started_at``, ``pass_index`` and
``sheet_cursor`` since it was written, with a docstring saying it exists
"for a picker UI". The B37 fix taught the *count* prompt to read two of
them. ``started_at`` was still read nowhere in ``deckle/``, and the
picker still did not pick.

Resuming the wrong job is not a recoverable mistake: it prints backs
against fronts that belong to a different run, onto paper the operator
has already reloaded, and they find out when the stack is ruined. The
whole `plan_hash` guard exists to stop the same thing happening a
different way.

The ordering and the label are pure and tested directly. The dialog is
driven through the injectable seam ``tests/test_print_dialog.py`` uses.
"""

from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.print_session import PrintSession, SessionSummary  # noqa: E402

NOW = 1789000000.0


def _summary(session_id: str, printer: str, started_at: float,
             pass_index: int = 0, sheet_cursor: int = 0) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        printer_name=printer,
        started_at=started_at,
        pass_index=pass_index,
        sheet_cursor=sheet_cursor,
        state_path=f"/tmp/{session_id}.json",
    )


# -- the ordering --------------------------------------------------------


def test_resumable_sessions_come_back_newest_first(tmp_path, monkeypatch):
    """Filename order is SHA order, which is no order at all.

    Written through the real ``list_resumable`` against real state files,
    with names chosen so that filename order and time order disagree --
    a probe that sorted its own list would agree with itself forever.
    """
    import deckle.core.print_session as ps

    monkeypatch.setattr(ps, "_state_dir", lambda: tmp_path)

    # "aaa" is the oldest and sorts first; "zzz" is the newest and sorts
    # last. Filename order would hand back the oldest.
    for name, started in (("aaa", NOW - 7200.0), ("mmm", NOW - 60.0),
                          ("zzz", NOW - 1800.0)):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "session_id": name,
            "printer_name": f"Printer {name}",
            "started_at": started,
            "pass_index": 0,
            "sheet_cursor": 0,
        }), encoding="utf-8")

    listed = PrintSession.list_resumable()

    assert [s.session_id for s in listed] == ["mmm", "zzz", "aaa"], (
        "resumable sessions are not ordered by when they started"
    )


def test_the_ordering_survives_a_state_file_that_cannot_be_read(tmp_path, monkeypatch):
    """One corrupt file must not hide every other resumable session --
    the property `list_resumable` already promised, re-checked because
    sorting is a new place to raise."""
    import deckle.core.print_session as ps

    monkeypatch.setattr(ps, "_state_dir", lambda: tmp_path)
    (tmp_path / "good.json").write_text(json.dumps({
        "session_id": "good", "printer_name": "P", "started_at": NOW,
        "pass_index": 0, "sheet_cursor": 0,
    }), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    listed = PrintSession.list_resumable()

    assert [s.session_id for s in listed] == ["good"]


# -- the label -----------------------------------------------------------


def test_the_label_names_everything_that_tells_two_jobs_apart():
    from deckle.app.views.print_dialog import resumable_label

    label = resumable_label(
        _summary("s1", "Laser Upstairs", NOW - 600.0, pass_index=1, sheet_cursor=7)
    )

    assert "Laser Upstairs" in label
    # Which pass, in the operator's numbering rather than the index.
    assert "2" in label
    assert "7" in label
    # And when, which is the only thing that distinguishes two runs of the
    # same document on the same printer.
    assert time.strftime("%H:%M", time.localtime(NOW - 600.0)) in label


def test_two_runs_of_the_same_job_on_the_same_printer_read_differently():
    """The case that makes the timestamp load-bearing rather than
    decorative: everything else about them is identical."""
    from deckle.app.views.print_dialog import resumable_label

    early = resumable_label(_summary("a", "Laser", NOW - 7200.0))
    late = resumable_label(_summary("b", "Laser", NOW - 60.0))

    assert early != late


# -- the picker ----------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _dialog(**kwargs):
    from deckle.app.views.print_dialog import PrintDialog
    from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side

    page = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,)), back=Side(pages=(page,)))],
        paper_pt=(612.0, 792.0), warnings=[],
    )
    defaults = dict(
        printer_names=["Printer A"],
        profile_loader=lambda name: (_ for _ in ()).throw(FileNotFoundError(name)),
        resumable_lister=lambda: [],
        confirm_reload=lambda instruction: None,
        confirm_test_sheet=lambda: True,
        show_offline_error=lambda printer, error: None,
    )
    defaults.update(kwargs)
    return PrintDialog(plan, **defaults)


def _stub_message_box(dialog, monkeypatch, answer):
    """Replace the dialog's ``QMessageBox`` with one that answers itself.

    Not optional and not tidiness. Before the fix, ``_default_confirm_resume``
    reached ``QMessageBox.exec()`` for *any* non-empty list, which is a
    modal with nobody to answer it -- so these tests would hang instead of
    failing, and a test that hangs proves nothing about what it names.
    """
    from PySide6.QtWidgets import QMessageBox

    shown = {}

    class _Box:
        StandardButton = QMessageBox.StandardButton

        def __init__(self, parent=None):
            pass

        def setWindowTitle(self, text):
            shown["title"] = text

        def setText(self, text):
            shown["text"] = text

        def setStandardButtons(self, buttons):
            pass

        def exec(self):
            shown["asked"] = True
            return answer

    monkeypatch.setattr(dialog, "_QMessageBox", _Box)
    return shown


def test_the_picker_offers_every_interrupted_job(monkeypatch):
    """The defect, named. ``resumable[0]`` cannot reach the second one."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    offered = []

    def _fake_get_item(parent, title, label, items, current, editable):
        offered.extend(items)
        return items[1], True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(_fake_get_item))
    dialog = _dialog()
    _stub_message_box(dialog, monkeypatch, QMessageBox.StandardButton.Yes)
    summaries = [
        _summary("newer", "Laser Upstairs", NOW - 60.0, sheet_cursor=3),
        _summary("older", "Inkjet", NOW - 7200.0, pass_index=1, sheet_cursor=11),
    ]

    chosen = dialog._default_confirm_resume(summaries)

    assert len(offered) == 2, (
        "the picker was not shown both interrupted jobs, so one of them is "
        f"unreachable: {offered}"
    )
    assert chosen is summaries[1], "the operator's choice was not the one returned"


def test_declining_the_picker_resumes_nothing(monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("", False)),
    )
    dialog = _dialog()
    _stub_message_box(dialog, monkeypatch, QMessageBox.StandardButton.No)

    assert dialog._default_confirm_resume([
        _summary("a", "Laser", NOW), _summary("b", "Inkjet", NOW - 10.0),
    ]) is None


def test_one_interrupted_job_is_still_a_yes_or_no_question(monkeypatch):
    """A list box with one row in it is a worse way to ask than a
    question. The single-job path keeps the message box it had -- with the
    fuller description, which is the part it was missing."""
    from PySide6.QtWidgets import QMessageBox

    dialog = _dialog()
    shown = _stub_message_box(dialog, monkeypatch, QMessageBox.StandardButton.Yes)
    only = _summary("solo", "Laser Upstairs", NOW - 600.0, pass_index=1,
                    sheet_cursor=7)

    chosen = dialog._default_confirm_resume([only])

    assert chosen is only
    assert "Laser Upstairs" in shown["text"]
    assert "7" in shown["text"], (
        "the single-job prompt still names the printer and nothing else: "
        f"{shown['text']!r}"
    )


def test_no_interrupted_jobs_asks_nothing():
    dialog = _dialog()

    assert dialog._default_confirm_resume([]) is None
