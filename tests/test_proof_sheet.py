"""The proof sheet: one sheet, one ruler, no job.

Sheets print at actual size -- an inch of the design is an inch of paper
(roadmap B6, settled 2026-09-08). That is a claim about someone else's
printer, made by a program that cannot see it, and a driver preset saying
"fit to page" falsifies it silently. ``deckle export --sheets 0 --rule``
has been the only way to check since the beginning, which means the check
was unavailable to everyone who uses the desktop app -- the people the
app exists for.

The dialog is driven through the same injectable boundary
``tests/test_print_dialog.py`` uses, with a backend stand-in: what is under
test here is that ticking the box prints a *proof* and not the job.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.views.print_dialog import PROOF_DPI, proof_sheet_index
from deckle.core.export import (
    RULE_TOO_NARROW_NOTE,
    proof_rule_advice,
    proof_rule_length_pt,
)
from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.printing import PrintResult
from deckle.core.profiles import PrinterProfile


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _blank_output_page() -> OutputPage:
    return OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )


def _make_plan(n_sheets: int = 3, paper=(612.0, 792.0)) -> SheetPlan:
    sheets = [
        Sheet(
            index=i,
            front=Side(pages=(_blank_output_page(),)),
            back=Side(pages=(_blank_output_page(),)),
        )
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=paper, warnings=[])


def _profile() -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )


class _RecordingBackend:
    """A backend that records what it was asked to put on paper."""

    proofs: list[tuple] = []
    jobs: list[tuple] = []

    def __init__(self, profile):
        self.profile = profile

    def submit(self, plan, sheets, printer_name, copies, dpi, **kwargs):
        _RecordingBackend.jobs.append((list(sheets), printer_name, kwargs))
        return PrintResult(submitted=len(sheets), job_id=None, error=None)

    def submit_pass(self, plan, print_pass, printer_name, copies, dpi):
        _RecordingBackend.jobs.append((list(print_pass.sheet_order), printer_name, {}))
        return PrintResult(
            submitted=len(print_pass.sheet_order), job_id=None, error=None
        )

    def submit_proof(self, plan, sheet_index, printer_name, dpi):
        _RecordingBackend.proofs.append((sheet_index, printer_name, dpi))
        return PrintResult(submitted=1, job_id=None, error=None)


class _FailingProofBackend(_RecordingBackend):
    def submit_proof(self, plan, sheet_index, printer_name, dpi):
        return PrintResult(submitted=0, job_id=None, error="printer is offline")


def _dialog(plan, backend_cls=_RecordingBackend, **overrides):
    from deckle.app.views.print_dialog import PrintDialog

    kwargs = dict(
        printer_names=["Laser"],
        profile_loader=lambda name: _profile(),
        backend_cls=backend_cls,
        resumable_lister=lambda: [],
    )
    kwargs.update(overrides)
    return PrintDialog(plan, None, **kwargs)


@pytest.fixture(autouse=True)
def _clear_backend_record():
    _RecordingBackend.proofs = []
    _RecordingBackend.jobs = []
    yield


# -- the pure choice ---------------------------------------------------


def test_the_proof_is_the_first_sheet_of_the_whole_plan():
    assert proof_sheet_index(_make_plan(3)) == 0


def test_the_proof_follows_a_narrowed_signature_selection():
    """Proofing a sheet outside the selection would measure paper the user
    is not about to run."""
    assert proof_sheet_index(_make_plan(6), [2, 3]) == 2


def test_a_plan_with_no_sheets_has_nothing_to_proof():
    assert proof_sheet_index(_make_plan(0)) is None
    assert proof_sheet_index(_make_plan(3), []) is None


# -- what the user is told to measure ----------------------------------


def test_the_advice_names_a_whole_number_of_inches():
    """"Is this line 7 inches?" is a question anyone with a tape can
    answer. "Is this line 7.43 inches?" is not."""
    advice = proof_rule_advice(612.0)
    inches = int(proof_rule_length_pt(612.0) / 72)
    assert f"{inches} in exactly" in advice
    assert "fit to page" in advice


def test_a_sheet_too_narrow_for_a_rule_says_so_instead_of_lying():
    """A proof with no rule on it looks like every other sheet; a user
    measuring nothing would conclude their printer was fine."""
    assert proof_rule_advice(36.0) == RULE_TOO_NARROW_NOTE


def test_the_cli_and_the_dialog_give_the_same_advice(capsys):
    """The same check, so a user who has done one should recognise the
    other. Two wordings would be two checks as far as anyone reading them
    is concerned."""
    from deckle.cli import _report_rule

    _report_rule(612.0)
    printed = capsys.readouterr().out.strip()

    assert printed == proof_rule_advice(612.0)


# -- the dialog --------------------------------------------------------


def test_the_dialog_offers_the_proof_as_one_checkbox():
    dialog = _dialog(_make_plan(3))
    assert dialog.proof_checkbox.isChecked() is False
    assert "ruler" in dialog.proof_checkbox.text().lower()


def test_ticking_it_prints_one_sheet_with_a_rule_and_not_the_job():
    dialog = _dialog(_make_plan(4))
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert _RecordingBackend.proofs == [(0, "Laser", PROOF_DPI)]
    assert _RecordingBackend.jobs == [], "the whole job was printed as well"


def test_a_proof_leaves_no_resumable_session_behind():
    """A proof is not a job. A session on disk would have the next print
    dialog offering to "finish" it, which is how a proof turns into a
    ruined stack of paper."""
    dialog = _dialog(_make_plan(4))
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert dialog._session is None


def test_the_proof_follows_the_signature_the_dialog_is_set_to():
    plan = SheetPlan(
        sheets=_make_plan(4).sheets,
        paper_pt=(612.0, 792.0),
        warnings=[],
        signatures=[],
    )
    dialog = _dialog(plan)
    # Stand in for a signature selection: the combo supplies a sheet
    # subset and nothing else, which is exactly what the proof reads.
    dialog.signature_combo.addItem("Signature 1", [2, 3])
    dialog.signature_combo.setCurrentIndex(dialog.signature_combo.count() - 1)
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert _RecordingBackend.proofs == [(2, "Laser", PROOF_DPI)]


def test_the_dialog_says_what_to_measure_afterwards():
    dialog = _dialog(_make_plan(3))
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert proof_rule_advice(612.0) in dialog.status_label.text()


def test_an_offline_printer_during_a_proof_is_reported_like_any_other():
    shown = []
    dialog = _dialog(
        _make_plan(3),
        backend_cls=_FailingProofBackend,
        show_offline_error=lambda printer, error: shown.append((printer, error)),
    )
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert shown == [("Laser", "printer is offline")]


def test_an_empty_plan_refuses_rather_than_submitting_nothing():
    dialog = _dialog(_make_plan(0))
    dialog.proof_checkbox.setChecked(True)
    dialog.start_print()

    assert _RecordingBackend.proofs == []
    assert "Nothing to proof" in dialog.status_label.text()


# -- the rule actually reaches the paper -------------------------------


def test_the_ruled_render_is_not_the_same_image_as_the_plain_one():
    """The whole feature is a line on a sheet. Everything above this test
    would pass with `rule=True` quietly ignored somewhere between the
    checkbox and pdfium, and the user would measure a sheet with nothing
    on it to measure."""
    from deckle.app.backend import _render_ruled_sheet_side
    from deckle.core.render import render_sheet

    plan = _make_plan(1)
    ruled = _render_ruled_sheet_side(plan, 0, "front", 72)
    plain = render_sheet(plan, 0, "front", 72)

    assert ruled.width == plain.width and ruled.height == plain.height
    assert ruled.rgba != plain.rgba, "the proof render carries no rule"


def test_a_proof_submits_through_the_ruled_path(monkeypatch):
    """The wiring between `submit_proof` and the renderer that draws the
    rule -- the one join the dialog tests above cannot see."""
    from deckle.app import backend as backend_module

    asked = []
    monkeypatch.setattr(
        backend_module,
        "_render_ruled_sheet_side",
        lambda plan, sheet_index, side, dpi: asked.append((sheet_index, side)) or
        backend_module.RenderedPage(width=0, height=0, rgba=b""),
    )
    monkeypatch.setattr(backend_module, "printer_is_available", lambda name: True)
    monkeypatch.setattr(backend_module, "_new_qprinter", _StubPrinter)
    monkeypatch.setattr(backend_module, "_new_qpainter", _StubPainter)

    backend = backend_module.QtPrintBackend(_profile())
    result = backend.submit_proof(_make_plan(3), 1, "Laser", PROOF_DPI)

    assert result.error is None
    assert asked == [(1, "front")], (
        "the proof did not go through the renderer that draws the rule"
    )


class _StubPrinter:
    def setPrinterName(self, name):  # noqa: N802 - Qt naming
        pass

    def setCopyCount(self, count):  # noqa: N802 - Qt naming
        pass

    def setFullPage(self, on):  # noqa: N802 - Qt naming
        pass

    def setPageLayout(self, layout):  # noqa: N802 - Qt naming
        # The proof goes on the plan's paper like any other job; what it
        # is set to is pinned in tests/test_print_paper_size.py.
        return True

    def newPage(self):  # noqa: N802 - Qt naming
        pass

    def resolution(self):
        return 300


class _StubPainter:
    def begin(self, printer):
        return True

    def end(self):
        pass

    def drawImage(self, *args):  # noqa: N802 - Qt naming
        pass
