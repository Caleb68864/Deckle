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
    # The real PrintSession takes this and calls it when a chunk printed
    # but could not be recorded (B37). The stub only records that the
    # dialog handed it over -- the *answer* landing on the sheet cursor is
    # PrintSession's own business, tested in test_print_log_failure.py.
    ask_sheets_printed: object = None
    passes: list = field(default_factory=lambda: [
        {"reload_instruction": "Load pass 1 fronts."},
        {"reload_instruction": "Reload and print pass 2 backs."},
    ])
    fail_printer: str | None = None
    _pass_index: int = 0
    # Carried because the dialog reads it, and a double that leaves a field
    # off the state dict is how B36 stayed invisible: every test double
    # faithfully reproduces the gap in the seam it is standing in for. The
    # dialog indexes this rather than `.get(..., 0)`ing it, so a stub that
    # drops it fails here instead of silently deciding it is zero.
    _sheet_cursor: int = 0
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
        # What the real `resume` does with the answer: it assigns the
        # cursor absolutely. A resumed run mid-pass has fed paper already.
        self._sheet_cursor = sheets_completed

    @classmethod
    def load(cls, plan, profile, backend, session_id, ask_sheets_printed=None):
        return cls(plan=plan, profile=profile, backend=backend,
                   printer_name="Resumed Printer",
                   ask_sheets_printed=ask_sheets_printed)

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
        return {
            "pass_index": self._pass_index,
            "sheet_cursor": self._sheet_cursor,
            "test_sheet_pending": self._test_sheet_pending,
        }

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


def test_cancelling_the_sheet_count_abandons_the_resume():
    """Cancel is not "zero sheets came out".

    The prompt asks the one thing software cannot observe, so it has to be
    answerable with "I do not know" -- and the dialog it uses reports that
    as a separate flag beside the number, which was being discarded. Cancel
    therefore read as ``0``: resume from the very start of the interrupted
    pass and reprint all of it, onto a stack the operator has by this point
    already reloaded. Nothing about that is recoverable once the paper is
    through the machine.
    """
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )
    loaded = []

    class WatchfulSession(_StubSession):
        @classmethod
        def load(cls, plan, profile, backend, session_id, ask_sheets_printed=None):
            loaded.append(session_id)
            return super().load(plan, profile, backend, session_id,
                                ask_sheets_printed=ask_sheets_printed)

    dialog = _make_dialog(
        session_cls=WatchfulSession,
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: None,
    )

    assert loaded == [], "a declined resume must not even load the session"
    assert dialog._session is None


def test_zero_sheets_is_a_real_answer_and_still_resumes():
    """The other half of the same rule. "Nothing came out" is exactly what
    someone says when the printer jammed on the first sheet, and it must
    still resume -- from 0, reprinting the pass on purpose."""
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )

    dialog = _make_dialog(
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: 0,
    )

    assert dialog._session is not None
    assert dialog._session.resumed_with == 0


def test_the_real_resume_dialog_opens_on_the_count_already_recorded(monkeypatch):
    """The wiring, not just the arithmetic.

    B37 asks how many sheets came out and writes the answer to the state
    file, where it becomes ``SessionSummary.sheet_cursor``. The resume
    prompt is handed that summary and used to read none of it -- opening on
    ``0`` and passing ``0`` to ``resume()``, which assigns the cursor
    absolutely and threw the answer away. The chunk went through the
    machine a second time.
    """
    from PySide6.QtWidgets import QInputDialog

    seen = {}

    def fake_get_int(parent, title, label, value=0, minimum=0, *args, **kwargs):
        seen.update(label=label, value=value, minimum=minimum)
        return value, True

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(fake_get_int))

    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=1,
        sheet_cursor=10,
        state_path="/tmp/abc123.json",
    )
    dialog = _make_dialog()

    assert dialog._default_ask_resume_count(summary) == 10
    assert seen["value"] == 10, "the field opened on zero again"
    assert "How many sheets came out?" in seen["label"]


def test_cancel_is_still_not_an_answer_even_with_a_pre_filled_count(monkeypatch):
    """Pre-filling the field must not turn Cancel into "resume from ten"."""
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        QInputDialog, "getInt",
        staticmethod(lambda *args, **kwargs: (10, False)),
    )

    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=1,
        sheet_cursor=10,
        state_path="/tmp/abc123.json",
    )

    assert _make_dialog()._default_ask_resume_count(summary) is None


# -- printed, but not recorded (B37) ---------------------------------------


def _unrecorded(**overrides):
    from deckle.core.print_session import UnrecordedSheets

    base = dict(
        printer_name="Printer A",
        pass_index=0,
        side="front",
        sheets=(0, 1, 2),
        submitted=3,
        error="3 sheet(s) printed, but the print could not be recorded "
              "in the session log (No space left on device).",
    )
    base.update(overrides)
    return UnrecordedSheets(**base)


def test_the_prompt_says_what_happened_before_it_asks_anything():
    """Order matters more than wording here. Someone at a stopped printer
    reaches for a reprint unless they are told the paper is fine first, so
    the situation has to arrive before the question does."""
    from deckle.app.views.print_dialog import unrecorded_sheets_prompt

    text = unrecorded_sheets_prompt(_unrecorded())

    said_it_printed = text.lower().index("printed")
    asked = text.lower().index("how many sheets came out")
    assert said_it_printed < asked, f"the question came first:\n{text}"


def test_the_prompt_carries_the_count_the_printer_the_pass_and_the_reason():
    from deckle.app.views.print_dialog import unrecorded_sheets_prompt

    text = unrecorded_sheets_prompt(_unrecorded())

    assert "3 sheets" in text
    assert "Printer A" in text
    assert "front pass" in text
    assert "No space left on device" in text


def test_the_prompt_says_what_cancel_does():
    """Cancel is a real answer with a real consequence -- the job stays
    resumable and the question comes back -- and an operator will not
    press it unless the dialog says so."""
    from deckle.app.views.print_dialog import unrecorded_sheets_prompt

    text = unrecorded_sheets_prompt(_unrecorded()).lower()

    assert "cancel" in text
    assert "resumable" in text or "asked again" in text


def test_the_prompt_asks_the_same_question_the_resume_prompt_asks():
    """One question about one output tray. Two spellings of it would teach
    the operator that the two answers mean different things.

    Asserted on the text both prompts actually produce, not on the source
    of the method that raises one of them -- the wording moved into
    ``resume_count_prompt`` so it could be tested at all.
    """
    from deckle.app.views import print_dialog as dialog_mod
    from deckle.core.print_session import SessionSummary

    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )

    assert "How many sheets came out?" in dialog_mod.resume_count_prompt(summary)
    assert "How many sheets came out?" in dialog_mod.unrecorded_sheets_prompt(
        _unrecorded()
    )


def test_one_sheet_is_not_asked_about_as_sheets():
    from deckle.app.views.print_dialog import unrecorded_sheets_prompt

    text = unrecorded_sheets_prompt(_unrecorded(sheets=(0,), submitted=1))

    assert "1 sheet of" in text


def test_the_dialog_hands_the_question_to_a_fresh_session():
    """The seam is useless unless the session gets it. The dialog is the
    only thing that knows how to raise a modal, and the session is the
    only thing that knows where the answer goes."""
    asked = []
    dialog = _make_dialog(ask_sheets_printed=lambda question: asked.append(question))

    dialog.start_print()

    assert dialog._session.ask_sheets_printed is dialog._ask_sheets_printed


def test_the_dialog_hands_the_question_to_a_resumed_session_too():
    """A resumed run fails the same way the first one did. ``load``
    rebuilds the session field by field, which is exactly where a seam
    goes missing on one path only."""
    summary = SessionSummary(
        session_id="abc123",
        printer_name="Printer A",
        started_at=0.0,
        pass_index=0,
        sheet_cursor=3,
        state_path="/tmp/abc123.json",
    )

    dialog = _make_dialog(
        resumable_lister=lambda: [summary],
        confirm_resume=lambda resumable: resumable[0],
        ask_resume_count=lambda chosen: 1,
        ask_sheets_printed=lambda question: 0,
    )

    assert dialog._session is not None
    assert dialog._session.ask_sheets_printed is dialog._ask_sheets_printed


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
        def load(cls, plan, profile, backend, session_id, ask_sheets_printed=None):
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


# -- picking how the paper comes out (B16) --------------------------------
#
# The app resolved the *first* builtin preset and offered nothing else, so
# a face-up printer was handed the face-down reload instruction and no way
# to say otherwise. The reload instruction is the one sentence standing
# between a correct book and sixty sheets printed upside down.


def _preset(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    )
    base.update(overrides)
    return PrinterProfile(**base)


FACE_DOWN = _preset()
FACE_UP = _preset(output_face="up", reverse_stack=False)
PRESETS = {"generic_face_down_reversed": FACE_DOWN, "generic_face_up_in_order": FACE_UP}


def test_describe_profile_names_both_axes_that_decide_the_reload():
    from deckle.app.views.print_dialog import describe_profile

    assert describe_profile(FACE_DOWN) == "Comes out face down, stack reversed"
    assert describe_profile(FACE_UP) == "Comes out face up, order kept"


def test_profile_choices_offers_every_preset_when_nothing_is_calibrated():
    from deckle.app.views.print_dialog import profile_choices

    choices, preselect = profile_choices(
        "Printer A",
        profile_loader=lambda name: (_ for _ in ()).throw(FileNotFoundError(name)),
        builtin_presets=PRESETS,
    )

    assert [profile for _label, profile, _saved in choices] == [FACE_DOWN, FACE_UP]
    assert preselect == 0


def test_a_calibration_outranks_every_preset_and_is_preselected():
    """It was measured against this actual printer. No generic preset
    should quietly beat it."""
    from deckle.app.views.print_dialog import profile_choices

    calibrated = _preset(output_face="up", reverse_stack=False,
                         calibrated_at="2026-01-01T00:00:00", calibration_version=1)
    choices, preselect = profile_choices(
        "Printer A", profile_loader=lambda name: calibrated, builtin_presets=PRESETS,
    )

    assert preselect == 0
    label, profile, is_saved = choices[0]
    assert profile is calibrated and is_saved
    assert "calibrated" in label


def test_a_corrupt_stored_profile_does_not_make_the_picker_unopenable():
    from deckle.app.views.print_dialog import profile_choices

    def corrupt(name):
        raise ValueError("not a profile")

    choices, _preselect = profile_choices(
        "Printer A", profile_loader=corrupt, builtin_presets=PRESETS,
    )

    assert [profile for _l, profile, _s in choices] == [FACE_DOWN, FACE_UP]


def test_the_chosen_profile_is_the_one_that_prints():
    """The whole point: selecting face-up must reach the backend."""
    dialog = _make_dialog(builtin_presets=PRESETS)
    face_up_index = next(
        i for i in range(dialog.profile_combo.count())
        if dialog.profile_combo.itemData(i)[0] is FACE_UP
    )
    dialog.profile_combo.setCurrentIndex(face_up_index)

    dialog.start_print()

    assert dialog._session.profile is FACE_UP


def test_choosing_a_preset_remembers_it_for_an_uncalibrated_printer():
    """`PrinterProfile.save` had no caller anywhere in the app, so a choice
    like this could not be expressed at all, let alone survive a restart."""
    saved: list[tuple] = []

    class Remembering(PrinterProfile):
        pass

    dialog = _make_dialog(builtin_presets=PRESETS)
    face_up_index = next(
        i for i in range(dialog.profile_combo.count())
        if dialog.profile_combo.itemData(i)[0] is FACE_UP
    )
    dialog.profile_combo.setCurrentIndex(face_up_index)
    profile, _is_saved = dialog.profile_combo.currentData()
    object.__setattr__(profile, "save", lambda name: saved.append((name, profile)))

    dialog.start_print()

    assert saved == [("Printer A", FACE_UP)]


def test_a_calibration_is_never_overwritten_by_the_picker():
    """The destructive case, and the reason `_remember_profile_choice`
    declines rather than clobbers.

    A calibration is not derived from anything -- it comes from printing a
    target, measuring it by hand, and reprinting when the numbers are
    wrong. Writing a generic preset over one because a combo happened to be
    showing it would destroy that, for a gesture nobody thinks of as
    destructive.
    """
    calibrated = _preset(calibrated_at="2026-01-01T00:00:00", calibration_version=1)
    writes: list[str] = []
    object.__setattr__(FACE_UP, "save", lambda name: writes.append(name))

    dialog = _make_dialog(
        builtin_presets=PRESETS,
        profile_loader=lambda name: calibrated,
    )
    face_up_index = next(
        i for i in range(dialog.profile_combo.count())
        if dialog.profile_combo.itemData(i)[0] is FACE_UP
    )
    dialog.profile_combo.setCurrentIndex(face_up_index)

    dialog.start_print()

    assert writes == [], "the picker overwrote a hand-measured calibration"
    assert dialog._session.profile is FACE_UP, "but the run still honours the choice"


def test_changing_printer_reoffers_that_printer_s_own_profile():
    """A calibration belongs to one printer; offering another printer's is
    worse than offering none."""
    calibrated = _preset(output_face="up", reverse_stack=False,
                         calibrated_at="2026-01-01T00:00:00", calibration_version=1)

    def loader(name):
        if name == "Printer B":
            return calibrated
        raise FileNotFoundError(name)

    dialog = _make_dialog(builtin_presets=PRESETS, profile_loader=loader)
    # The dialog opens on Printer B: `select_preselected_printer` prefers a
    # printer that has been calibrated, which is the whole reason it exists.
    assert dialog.printer_combo.currentText() == "Printer B"
    assert dialog.profile_combo.itemData(0) == (calibrated, True)

    dialog.printer_combo.setCurrentText("Printer A")

    assert all(
        not dialog.profile_combo.itemData(i)[1]
        for i in range(dialog.profile_combo.count())
    ), "Printer A was offered Printer B's calibration"
    assert [dialog.profile_combo.itemData(i)[0]
            for i in range(dialog.profile_combo.count())] == [FACE_DOWN, FACE_UP]


def test_a_corrupt_stored_profile_does_not_stop_the_dialog_opening():
    """B21. `PrinterProfile.load` raises `StoredValueError`/`JSONDecodeError`
    for a corrupt file -- both `ValueError` subclasses, which its docstring
    says in as many words so "a caller with a `ValueError` branch already
    reports it cleanly". The CLI had that branch; this path did not, and it
    runs inside `PrintDialog.__init__`. So one unreadable file did not
    degrade a printer to uncalibrated, it made printing impossible."""
    def corrupt(name):
        raise ValueError("flip_axis: 'diagonal' is not one this build can honour")

    dialog = _make_dialog(profile_loader=corrupt, builtin_presets=PRESETS)

    assert dialog.profile_combo.count() == len(PRESETS)
    dialog.start_print()
    assert dialog._session is not None
