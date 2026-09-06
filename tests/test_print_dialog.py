"""Tests for PrintDialog: printer preselection, reload confirm, test-sheet
offer, resume-on-reopen, and the offline-printer modal.

The dialog owns no print logic, so these tests drive it with a stub
``PrintSession``-shaped class that mimics the public surface
(``start``/``advance``/``confirm_test_sheet``/``resume``/``finished``/
``last_error``/``reload_instruction``/``state``) instead of a real Qt
print backend -- exactly the boundary ``PrintDialog`` itself is written
against.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.print_session import SessionSummary
from deckle.core.profiles import PrinterProfile

# PySide6 is imported lazily, only inside this fixture's body (which runs
# at first test call, not at collection time) -- importing it at module
# scope would pollute sys.modules before test_core_purity.py's and
# test_backend.py's "Qt not loaded" assertions run in the same session.


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


def _make_plan(n_sheets: int = 2) -> SheetPlan:
    sheets = [
        Sheet(
            index=i,
            front=Side(pages=(_blank_output_page(),)),
            back=Side(pages=(_blank_output_page(),)),
        )
        for i in range(n_sheets)
    ]
    return SheetPlan(sheets=sheets, paper_pt=(612.0, 792.0), warnings=[])


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )
    base.update(overrides)
    return PrinterProfile(**base)


class _StubBackend:
    """A no-op PrintBackend stand-in; PrintDialog constructs one of these."""

    def __init__(self, profile):
        self.profile = profile

    def submit(self, plan, sheets, printer_name, copies, dpi, **_kwargs):
        from deckle.core.printing import PrintResult

        return PrintResult(submitted=len(sheets), job_id=None, error=None)


@dataclass
class _StubSession:
    """Mimics PrintSession's public surface, driven by two hand-fed passes."""

    plan: SheetPlan
    profile: PrinterProfile
    backend: object
    test_first: bool = False
    printer_name: str = ""
    passes: list = field(default_factory=lambda: [
        {"reload_instruction": "Load pass 1 fronts."},
        {"reload_instruction": "Reload and print pass 2 backs."},
    ])
    fail_printer: str | None = None
    _pass_index: int = 0
    _finished: bool = False
    _last_error: str | None = None
    _test_sheet_pending: bool = False
    started_with_test_first: bool = False
    resumed_with: int | None = None
    confirmed_test_sheet: bool = False

    def start(self) -> None:
        if self.fail_printer:
            self._last_error = f"{self.fail_printer} is offline"
            return
        if self.test_first:
            self._test_sheet_pending = True
            self.started_with_test_first = True

    def confirm_test_sheet(self) -> None:
        self._test_sheet_pending = False
        self.confirmed_test_sheet = True

    def advance(self) -> None:
        if self.fail_printer:
            self._last_error = f"{self.fail_printer} is offline"
            return
        self._pass_index += 1
        if self._pass_index >= len(self.passes):
            self._finished = True

    def resume(self, sheets_completed: int) -> None:
        self.resumed_with = sheets_completed
        self._pass_index = 1

    @classmethod
    def load(cls, plan, profile, backend, session_id):
        return cls(plan=plan, profile=profile, backend=backend, printer_name="Resumed Printer")

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def reload_instruction(self) -> str | None:
        if self._pass_index >= len(self.passes):
            return None
        return self.passes[self._pass_index]["reload_instruction"]

    @property
    def state(self) -> dict:
        return {"pass_index": self._pass_index, "test_sheet_pending": self._test_sheet_pending}

    @staticmethod
    def list_resumable():
        return []


def _make_dialog(**kwargs):
    from deckle.app.views.print_dialog import PrintDialog

    defaults = dict(
        printer_names=["Printer A", "Printer B"],
        profile_loader=lambda name: (_ for _ in ()).throw(FileNotFoundError(name)),
        session_cls=_StubSession,
        backend_cls=_StubBackend,
        resumable_lister=lambda: [],
        confirm_reload=lambda instruction: None,
        confirm_test_sheet=lambda: True,
        show_offline_error=lambda printer, error: None,
    )
    defaults.update(kwargs)
    return PrintDialog(_make_plan(), **defaults)


# -- printer listing / preselection --------------------------------------


def test_select_preselected_printer_picks_first_with_saved_profile():
    from deckle.app.views.print_dialog import select_preselected_printer

    def loader(name):
        if name == "Printer B":
            return _profile()
        raise FileNotFoundError(name)

    assert select_preselected_printer(["Printer A", "Printer B"], loader) == "Printer B"


def test_select_preselected_printer_falls_back_to_first_when_no_saved_profile():
    from deckle.app.views.print_dialog import select_preselected_printer

    def loader(name):
        raise FileNotFoundError(name)

    assert select_preselected_printer(["Printer A", "Printer B"], loader) == "Printer A"


def test_dialog_lists_printers_and_preselects_saved_profile():
    def loader(name):
        if name == "Printer B":
            return _profile()
        raise FileNotFoundError(name)

    dialog = _make_dialog(profile_loader=loader)
    names = [dialog.printer_combo.itemText(i) for i in range(dialog.printer_combo.count())]
    assert names == ["Printer A", "Printer B"]
    assert dialog.printer_combo.currentText() == "Printer B"


# -- reload-between-passes and test-first ----------------------------------


def test_reload_instruction_shown_verbatim_and_blocks_before_next_pass():
    seen = []

    def confirm_reload(instruction):
        seen.append(instruction)

    dialog = _make_dialog(confirm_reload=confirm_reload)
    dialog.start_print()

    assert seen == ["Reload and print pass 2 backs."]
    assert dialog._session.finished


def test_test_first_checkbox_maps_to_session_test_first_true():
    dialog = _make_dialog(confirm_test_sheet=lambda: True)
    dialog.test_first_checkbox.setChecked(True)
    dialog.start_print()

    assert dialog._session.test_first is True
    assert dialog._session.started_with_test_first is True
    assert dialog._session.confirmed_test_sheet is True


def test_test_first_checkbox_present_and_enabled_before_and_after_pass_boundary():
    dialog = _make_dialog()
    assert dialog.test_first_checkbox.isEnabled()
    dialog.start_print()
    # The checkbox is never hidden/disabled as passes progress -- it stays
    # offered for the next print run regardless of how many passes ran.
    assert dialog.test_first_checkbox.isEnabled()


# -- resume on reopen -------------------------------------------------------


def test_reopening_with_interrupted_session_offers_resume_and_prompts_sheet_count():
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )
    prompted = []

    def confirm_resume(resumable):
        return resumable[0]

    def ask_resume_count(chosen):
        prompted.append(chosen)
        return 7

    dialog = _make_dialog(
        resumable_lister=lambda: [summary],
        confirm_resume=confirm_resume,
        ask_resume_count=ask_resume_count,
    )

    assert prompted == [summary]
    assert dialog._session.resumed_with == 7


def test_no_resumable_sessions_means_no_resume_prompt():
    calls = []
    dialog = _make_dialog(
        resumable_lister=lambda: [],
        confirm_resume=lambda resumable: calls.append(resumable),
    )
    assert calls == []
    assert dialog._session is None


# -- printer offline at submission -------------------------------------------


def test_printer_offline_at_submission_shows_blocking_modal_naming_printer():
    shown = []

    dialog = _make_dialog(
        session_cls=lambda *a, **k: _StubSession(*a, **k, fail_printer="Printer A"),
        show_offline_error=lambda printer, error: shown.append((printer, error)),
    )
    dialog.start_print()

    assert shown, "offline error modal was not shown"
    printer_name, error = shown[0]
    assert printer_name == "Printer A"
    assert "offline" in error
    # The session is left as-is (not finished, error still set) so it
    # remains resumable afterward.
    assert dialog._session.finished is False
    assert dialog._session.last_error is not None


# -- refusing a stale session (hardening pass 7) -------------------------


def test_resume_is_refused_and_explained_when_the_plan_has_changed():
    """The dialog must surface the refusal, not propagate the exception.

    ``PrintSession.load`` now raises ``StaleSessionError`` when the document
    has been re-imposed since the run started. Letting that escape would
    take down the dialog at the moment the user is standing at the printer
    with a half-printed stack.
    """
    from deckle.core.print_session import StaleSessionError

    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )
    shown: list[tuple[str, str]] = []

    class RefusingSession(_StubSession):
        @classmethod
        def load(cls, plan, profile, backend, session_id):
            raise StaleSessionError(
                session_id=session_id,
                reason="plan",
                detail=(
                    "the document's layout has changed since this print run "
                    "started, so the remaining sheets no longer line up with "
                    "the pages already printed; start a new print run"
                ),
            )

    dialog = _make_dialog(
        session_cls=RefusingSession,
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: 7,
        show_offline_error=lambda title, detail: shown.append((title, detail)),
    )

    assert dialog._session is None, "a refused session must not be adopted"
    assert len(shown) == 1
    title, detail = shown[0]
    assert "resume" in title.lower()
    assert "layout has changed" in detail
    assert "start a new print run" in detail
