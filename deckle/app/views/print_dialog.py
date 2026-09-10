"""PrintDialog: the manual-duplex print UI.

Presentation only. Every sequencing decision -- pass order, sheet
cursors, reload text -- lives in ``deckle.core.print_session.PrintSession``
(SS-11) and ``deckle.core.printing`` (SS-08's ordering table). This module
constructs a ``PrintSession`` backed by ``deckle.app.backend.QtPrintBackend``
and drives it through its public API only: ``start``, ``confirm_test_sheet``,
``advance``, ``resume``, ``finished``, ``last_error``, ``reload_instruction``,
and ``PrintSession.list_resumable`` / ``PrintSession.load``. It never
recomputes sheet order or flips a stack itself.

The confirmation prompts (reload-between-passes, resume count, offline
error, resume offer, unrecorded sheet count) are all injectable callables
so headless tests can drive the full flow without blocking on a real modal
event loop. Two of them ask the same question -- how many sheets came out
-- because it is the same question: software cannot see the output tray,
and the person at the printer can. The wording is shared on purpose.
"""

from __future__ import annotations

import time
from typing import Callable, Sequence

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.export import proof_rule_advice
from deckle.core.models import SheetPlan
from deckle.core.print_session import (
    PrintSession,
    SessionSummary,
    StaleSessionError,
    UnrecordedSheets,
)
from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile


def select_preselected_printer(
    printer_names: Sequence[str],
    profile_loader: Callable[[str], PrinterProfile],
) -> str | None:
    """The printer name to preselect: the first with a saved profile.

    Falls back to the first available printer (if any) when none of them
    has a saved ``PrinterProfile`` yet. Pure and Qt-free so it is directly
    unit-testable.

    :param printer_names: the printers to choose among, in the order Qt
        reported them.
    :param profile_loader: called with a printer name; raising means "no
        saved profile".
    :returns: the printer to preselect, or ``None`` when there are no
        printers at all.
    """
    for name in printer_names:
        try:
            profile_loader(name)
        except (FileNotFoundError, OSError, ValueError) as exc:
            # No calibration profile for this printer -- try the next one.
            # Worth recording: the fallback silently lands on
            # printer_names[0], and a user wondering why Deckle picked the
            # "wrong" printer has no other way to see this happened.
            log_exception("printer_profile_unavailable", exc, printer=name)
            continue
        else:
            return name
    return printer_names[0] if printer_names else None


def unrecorded_sheets_prompt(question: UnrecordedSheets) -> str:
    """What the operator reads when a chunk printed but was not recorded.

    The situation comes first and the question last, because that is the
    order it has to arrive in for someone standing at a stopped printer.
    They already know something went wrong -- what they do not know, and
    what changes what they do next, is that **the paper is fine**. Leading
    with the question would have them counting sheets before they know
    why, and reaching for the reprint they do not need.

    Pure and Qt-free so the wording is directly testable, the way
    :func:`select_preselected_printer` is. The prompt is prose a person
    acts on at the moment a stack of paper is at stake; it is worth a test
    of its own rather than being buried inside a modal.

    :param question: the chunk, the pass, and what the backend said.
    :returns: the label text for the count dialog.
    """
    n = question.submitted
    sheets = "sheet" if n == 1 else "sheets"
    where = f" to {question.printer_name!r}" if question.printer_name else ""
    return (
        f"{n} {sheets} of the {question.side} pass "
        f"(pass {question.pass_index + 1}) went{where} and printed, but the "
        f"run could not be recorded:\n\n"
        f"    {question.error}\n\n"
        "The printing is done; only the record of it is missing. Deckle "
        "cannot see the output tray and will not guess -- counting a sheet "
        "that never came out leaves a hole in the book, and not counting "
        "one that did prints it twice.\n\n"
        "Look at the tray. How many sheets came out?\n\n"
        "Cancel to leave it undecided: the job stays resumable and you will "
        "be asked again."
    )


def suggested_resume_count(summary: SessionSummary) -> int:
    """What the resume prompt's count field opens on.

    The number Deckle already recorded for the interrupted pass -- which is
    where an answer to :func:`unrecorded_sheets_prompt` ends up. That popup
    asks the operator how many sheets came out and writes the answer to
    ``sheet_cursor``; the resume prompt is the *same question about the
    same tray*, asked once more because time and a reload have passed since.

    It used to open on ``0`` and read nothing of the summary it was handed.
    ``PrintSession.resume`` assigns the cursor absolutely, so ``0`` did not
    mean "no new information" -- it meant "nothing came out", and it
    overwrote the count the operator had already given. The chunk they had
    just finished counting went through the machine a second time.

    A default, not an answer: the operator is the one who can see the tray
    and can still correct it. But a field reset to zero, offered to someone
    who has just counted that tray out loud, is a worse starting point than
    what Deckle wrote down.

    :param summary: the interrupted session, as :meth:`list_resumable`
        reports it.
    :returns: the sheet count to pre-fill, per-pass like ``resume()``'s own
        argument.
    """
    return summary.sheet_cursor


def resume_count_prompt(summary: SessionSummary) -> str:
    """What the operator reads when an interrupted job is resumed.

    Says what the pre-filled number is before asking the question, for the
    same reason :func:`unrecorded_sheets_prompt` puts the situation first:
    a number appearing in a field with no account of where it came from
    cannot be told apart from a guess, and the operator cannot know whether
    to trust it or clear it.

    Pure and Qt-free so the wording is directly testable.

    :param summary: the interrupted session being resumed.
    :returns: the label text for the count dialog.
    """
    n = summary.sheet_cursor
    sheets = "sheet" if n == 1 else "sheets"
    return (
        f"Deckle recorded {n} {sheets} as printed in pass "
        f"{summary.pass_index + 1} before this run stopped, so the count "
        "below starts there.\n\n"
        "Look at the tray. How many sheets came out?\n\n"
        "Cancel to leave the job as it is: it stays resumable and you will "
        "be asked again."
    )


def resumable_label(summary: SessionSummary) -> str:
    """One line that tells two interrupted print jobs apart.

    The picker used to say *"An interrupted print job to 'Laser' was
    found."* and nothing else. With two interrupted runs -- the ordinary
    case on a printer that jams -- that sentence is true of both, and the
    operator has no way to know which one they are about to feed paper
    into. Resuming the wrong one prints backs against fronts from a
    different run, onto a stack they have already reloaded, and they find
    out when the stack is ruined.

    Everything here comes off :class:`SessionSummary`, which has carried
    it since it was written under a docstring saying it exists "for a
    picker UI". ``started_at`` in particular was serialised,
    round-tripped and read nowhere in ``deckle/`` -- and it is the only
    field that separates two runs of the same document on the same
    printer.

    The pass is numbered the way the operator counts, from one, matching
    :func:`resume_count_prompt`.

    Pure and Qt-free so the wording is directly testable.

    :param summary: the interrupted session to describe.
    :returns: the label.
    """
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(summary.started_at))
    n = summary.sheet_cursor
    sheets = "sheet" if n == 1 else "sheets"
    return (
        f"{summary.printer_name} -- pass {summary.pass_index + 1}, "
        f"{n} {sheets} recorded -- started {when}"
    )


PROOF_DPI = 300
"""Rasterization resolution for a proof sheet.

The same default a :class:`~deckle.core.print_session.PrintSession` uses,
because a proof that is rendered differently from the job proves something
about a sheet nobody is going to print.
"""


def proof_sheet_index(plan: SheetPlan, sheets: Sequence[int] | None = None) -> int | None:
    """The sheet to proof: the first one that will actually be printed.

    ``--sheets 0 --rule`` is the CLI's spelling and sheet 0 is almost always
    the answer, but the dialog can be narrowed to one signature -- and
    proofing a sheet outside the selection would measure paper the user is
    not about to run.

    Pure and Qt-free, so the choosing is testable without a display.

    :param plan: the imposed sheets.
    :param sheets: the narrowed selection, or ``None`` for the whole plan.
    :returns: the sheet index, or ``None`` when there is nothing to print.
    """
    if sheets is not None:
        selected = list(sheets)
        return selected[0] if selected else None
    return plan.sheets[0].index if plan.sheets else None


def describe_profile(profile: PrinterProfile) -> str:
    """A one-line description of how a printer hands paper back.

    Derived from the profile's own fields rather than from a preset's key,
    so a saved calibration describes itself in the same words a builtin
    does. These two axes are the whole of what ``plan_passes`` consumes,
    and between them they decide the reload instruction -- which is the
    single most consequential sentence Deckle prints.

    :param profile: the profile to describe.
    :returns: a label for a picker.
    """
    face = "face up" if profile.output_face == "up" else "face down"
    order = "stack reversed" if profile.reverse_stack else "order kept"
    return f"Comes out {face}, {order}"


def profile_choices(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> tuple[list[tuple[str, PrinterProfile, bool]], int]:
    """The profiles offerable for ``printer_name``, and which to preselect.

    A saved calibration, when there is one, comes first and is preselected:
    it was measured against this actual printer, and no generic preset
    should quietly outrank it. The builtins follow in declaration order.

    Pure and Qt-free so the choosing is testable without a display.

    :param printer_name: the printer being printed to.
    :param profile_loader: how to load a saved profile; raising means none.
    :param builtin_presets: the presets to offer, or ``None`` for
        ``BUILTIN_PRESETS``.
    :returns: ``(choices, index)`` where each choice is
        ``(label, profile, is_saved)``.
    """
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    choices: list[tuple[str, PrinterProfile, bool]] = []
    try:
        saved = profile_loader(printer_name)
    except (FileNotFoundError, OSError, ValueError):
        # ValueError covers a corrupt stored profile, which the CLI already
        # treats as "no profile" -- see B21. An unreadable calibration must
        # not make the picker unopenable.
        saved = None
    if saved is not None:
        choices.append((f"{describe_profile(saved)} (calibrated)", saved, True))
    for profile in presets.values():
        choices.append((describe_profile(profile), profile, False))
    return choices, 0


def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> PrinterProfile:
    """A profile for ``printer_name``: saved, else a builtin default.

    Until calibration (SS-13) exists, this is the only way a print run
    ever gets a ``PrinterProfile`` without a prior calibration pass.

    :param printer_name: the printer to load a profile for.
    :param profile_loader: how to load a saved profile.
    :param builtin_presets: the fallback presets, or ``None`` for
        ``BUILTIN_PRESETS``.
    :returns: the saved profile, else the first builtin preset. Never
        raises for a missing profile -- a printer with no calibration is
        the normal case, not an error.
    """
    try:
        return profile_loader(printer_name)
    except (FileNotFoundError, OSError, ValueError):
        # `ValueError` covers a corrupt stored profile: `StoredValueError`
        # for a value this build cannot honour, `JSONDecodeError` for a file
        # that is not JSON at all. Both are `ValueError` subclasses, which
        # is why `PrinterProfile.load`'s docstring says so. The CLI's
        # `_resolve_profile` already caught them and reported "no printer
        # profile"; this path did not, and it runs inside
        # `PrintDialog.__init__` -- so a single unreadable file did not
        # degrade one printer to uncalibrated, it made the print dialog
        # impossible to open.
        pass
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    return next(iter(presets.values()))


# -- Qt wiring ---------------------------------------------------------
# Imported lazily so this module stays importable (and the pure helpers
# above stay callable) without PySide6/a display.


def _qt_widgets():
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )

    return (
        QCheckBox,
        QComboBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )


def _available_printer_names() -> list[str]:
    from PySide6.QtPrintSupport import QPrinterInfo

    return [info.printerName() for info in QPrinterInfo.availablePrinters()]


class PrintDialog:
    """A dialog that submits ``plan`` through a ``PrintSession``.

    All dependencies (printer list, profile loading, the session/backend
    classes, and every user-facing confirmation) are injectable so tests
    can drive the full pass/resume/offline flow headlessly, without a
    real blocking modal.

    :param plan: the imposed sheets to print.
    :param parent: the parent ``QWidget``, or ``None``.
    :param printer_names: the printers to offer, or ``None`` to enumerate
        them. ``MainWindow`` passes its cached list, because re-enumerating
        would reintroduce exactly the spooler block it moved off the UI
        thread.
    :param profile_loader: how to load a saved ``PrinterProfile``.
    :param builtin_presets: fallback presets, or ``None`` for the builtins.
    :param session_cls: the session class to construct. Injected for tests.
    :param backend_cls: the print backend class, or ``None`` to import
        ``QtPrintBackend`` lazily.
    :param resumable_lister: how to find interrupted sessions.
    :param confirm_resume: asked which interrupted session to resume, if
        any; returning ``None`` declines.
    :param ask_resume_count: asked how many sheets physically emerged
        during the interrupted pass. Software cannot observe that, so it
        is taken as ground truth from the person holding the stack.
        Returning ``None`` declines the resume and leaves the session
        untouched; ``0`` is a genuine count, not a refusal.
    :param ask_sheets_printed: asked how many sheets of a chunk that
        printed and then failed to be recorded actually came out. Handed to
        the ``PrintSession``, which is where the answer lands -- on the
        sheet cursor. Same three-way answer as ``ask_resume_count``:
        a count, ``0``, or ``None`` for "I would rather not say".
    :param confirm_reload: shown the reload instruction between passes.
    :param confirm_test_sheet: asked whether the test sheet printed
        correctly.
    :param show_offline_error: shown ``(printer_name, error)`` when a run
        stalls, and reused verbatim for a refused resume.
    :ivar widget: the ``QDialog`` to show. This class is not itself a
        widget.
    """

    def __init__(
        self,
        plan: SheetPlan,
        parent=None,
        *,
        printer_names: Sequence[str] | None = None,
        profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
        builtin_presets: dict[str, PrinterProfile] | None = None,
        session_cls: type = PrintSession,
        backend_cls=None,
        resumable_lister: Callable[[], list[SessionSummary]] | None = None,
        confirm_resume: Callable[[list[SessionSummary]], SessionSummary | None] | None = None,
        ask_resume_count: Callable[[SessionSummary], int | None] | None = None,
        ask_sheets_printed: Callable[[UnrecordedSheets], int | None] | None = None,
        confirm_reload: Callable[[str], None] | None = None,
        confirm_test_sheet: Callable[[], bool] | None = None,
        show_offline_error: Callable[[str, str], None] | None = None,
    ) -> None:
        if backend_cls is None:
            from deckle.app.backend import QtPrintBackend

            backend_cls = QtPrintBackend

        self.plan = plan
        self._profile_loader = profile_loader
        self._builtin_presets = builtin_presets
        self._session_cls = session_cls
        self._backend_cls = backend_cls
        self._session: PrintSession | None = None

        self._confirm_resume = confirm_resume or self._default_confirm_resume
        self._ask_resume_count = ask_resume_count or self._default_ask_resume_count
        self._ask_sheets_printed = (
            ask_sheets_printed or self._default_ask_sheets_printed
        )
        self._confirm_reload = confirm_reload or self._default_confirm_reload
        self._confirm_test_sheet = confirm_test_sheet or self._default_confirm_test_sheet
        self._show_offline_error = show_offline_error or self._default_show_offline_error

        (
            QCheckBox,
            QComboBox,
            QDialog,
            QHBoxLayout,
            QLabel,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
        ) = _qt_widgets()
        self._QMessageBox = QMessageBox

        self.widget = QDialog(parent)
        self.widget.setWindowTitle("Print")

        layout = QVBoxLayout(self.widget)

        printer_row = QHBoxLayout()
        printer_row.addWidget(QLabel("Printer:", self.widget))
        self.printer_combo = QComboBox(self.widget)
        names = list(printer_names) if printer_names is not None else _available_printer_names()
        self.printer_combo.addItems(names)
        preselected = select_preselected_printer(names, self._profile_loader)
        if preselected is not None:
            self.printer_combo.setCurrentIndex(names.index(preselected))
        printer_row.addWidget(self.printer_combo)
        layout.addLayout(printer_row)

        # Which reload instruction this run will give. The app resolved the
        # *first* builtin preset and offered no way to say otherwise, so a
        # face-up printer was told to reload as though it were face-down --
        # and the reload instruction is the one sentence that decides
        # whether the backs land on the right fronts. B16.
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Paper:", self.widget))
        self.profile_combo = QComboBox(self.widget)
        self.profile_combo.setToolTip(
            "How this printer hands paper back. It decides the reload "
            "instruction between the two passes. A calibrated profile is "
            "preselected when one exists."
        )
        profile_row.addWidget(self.profile_combo)
        layout.addLayout(profile_row)
        self._sync_profile_choices(self.printer_combo.currentText())
        self.printer_combo.currentTextChanged.connect(self._sync_profile_choices)

        self.test_first_checkbox = QCheckBox("Test one sheet first", self.widget)
        layout.addWidget(self.test_first_checkbox)

        # The only actual-size check Deckle offers. Sheets print at actual
        # size (B6) -- an inch of the design is an inch of paper -- but that
        # is a claim about someone else's printer, and a driver preset
        # saying "fit to page" falsifies it silently. This prints one sheet
        # with a ruler across it: measure the rule, and you know.
        self.proof_checkbox = QCheckBox(
            "Proof sheet only -- one sheet with a ruler", self.widget
        )
        self.proof_checkbox.setToolTip(
            "Prints ONE sheet with a ruler of known length drawn on it, "
            "and does not print the job.\n\n"
            "Measure the rule against a tape. If it is short, the printer "
            'scaled the page -- turn off "fit to page" and try again. This '
            "is the same check as the CLI's --sheets 0 --rule."
        )
        layout.addWidget(self.proof_checkbox)

        # Signature selector: "All" (the default -- prints the whole plan)
        # or one signature by index, so a binder can reprint a single
        # gathering without touching pass/sheet-order arithmetic. That
        # arithmetic already lives in `plan_passes`/`PrintSession` -- this
        # combo only ever supplies a `sheets=` subset, never computes one.
        signature_row = QHBoxLayout()
        signature_row.addWidget(QLabel("Signature:", self.widget))
        self.signature_combo = QComboBox(self.widget)
        self.signature_combo.addItem("All", None)
        for signature in self.plan.signatures:
            self.signature_combo.addItem(f"Signature {signature.index}", signature.sheet_indices)
        signature_row.addWidget(self.signature_combo)
        layout.addLayout(signature_row)

        self.status_label = QLabel("", self.widget)
        layout.addWidget(self.status_label)

        self.print_button = QPushButton("Print", self.widget)
        self.print_button.clicked.connect(self.start_print)
        layout.addWidget(self.print_button)

        lister = resumable_lister or self._session_cls.list_resumable
        self._resumable = lister()
        if self._resumable:
            self._offer_resume()

    # -- printer / profile helpers ------------------------------------------

    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
        return resolve_profile(printer_name, self._profile_loader, self._builtin_presets)

    def _sync_profile_choices(self, printer_name: str) -> None:
        """Refill the profile combo for ``printer_name``.

        Re-run whenever the printer changes, because a saved calibration
        belongs to one printer and offering another printer's is worse than
        offering none.

        :param printer_name: the newly selected printer.
        :returns: nothing.
        """
        choices, preselect = profile_choices(
            printer_name, self._profile_loader, self._builtin_presets
        )
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for label, profile, is_saved in choices:
            self.profile_combo.addItem(label, (profile, is_saved))
        self.profile_combo.setCurrentIndex(preselect)
        self.profile_combo.blockSignals(False)

    def _selected_profile(self, printer_name: str) -> PrinterProfile:
        """The profile this print run will use.

        The combo when there is one, falling back to :func:`resolve_profile`
        so the pure path (and any caller constructing the dialog headlessly)
        behaves as it always did.

        :param printer_name: the printer being printed to.
        :returns: the profile to print with.
        """
        data = self.profile_combo.currentData()
        if data is None:
            return self._resolve_profile(printer_name)
        profile, _is_saved = data
        return profile

    def selected_profile(self) -> PrinterProfile:
        """The profile the dialog is currently set to print with.

        Public because the *window* needs it too: the preview's red
        imageable-area guide, its clipping warnings and "Use printer
        margins" are all this same profile, and the dialog is where a user
        picks one (B16). Reading it back is how that choice reaches the
        rest of the app instead of ending when the dialog closes.

        :returns: the profile, never raising -- an unreadable calibration
            degrades to a preset, as everywhere else.
        """
        return self._selected_profile(self.printer_combo.currentText())

    def _remember_profile_choice(self, printer_name: str) -> None:
        """Persist a picked preset so the choice sticks for this printer.

        Only when the printer has no stored profile yet. A saved profile is
        a *calibration* -- printed, measured by hand, and reprinted when the
        numbers were wrong, which `PrinterProfile.save` calls the most
        expensive data Deckle holds. Overwriting one with a generic preset
        because a combo happened to be showing it would destroy that for a
        gesture the user did not think of as destructive, so this declines
        rather than clobbers, and the calibration stays preselected anyway.

        Failing to remember is not worth interrupting a print for: the run
        is already correct, only the memory of the choice is lost.

        :param printer_name: the printer to key the profile by.
        :returns: nothing.
        """
        data = self.profile_combo.currentData()
        if data is None:
            return
        profile, is_saved = data
        if is_saved:
            return
        try:
            self._profile_loader(printer_name)
        except (FileNotFoundError, OSError, ValueError):
            pass
        else:
            return  # A stored profile exists. Never overwrite it from here.
        try:
            profile.save(printer_name)
        except OSError as exc:
            log_exception("profile_save_failed", exc, printer=printer_name)

    # -- starting a fresh print run ------------------------------------------

    def start_print(self) -> None:
        """Construct a fresh ``PrintSession`` for the selected printer and run it.

        The signature combo supplies a ``sheets=`` subset and nothing more
        -- reprinting one gathering is the normal path with a smaller
        input, and the pass/sheet-order arithmetic stays in
        ``plan_passes``/``PrintSession``.

        :returns: nothing. A printer that is offline or unreachable is
            surfaced through ``show_offline_error`` and leaves a resumable
            session on disk, rather than raising. A plan with no sheets is
            refused before a session exists.
        """
        printer_name = self.printer_combo.currentText()
        profile = self._selected_profile(printer_name)
        self._remember_profile_choice(printer_name)
        backend = self._backend_cls(profile)
        sheets = self.signature_combo.currentData()
        if self.proof_checkbox.isChecked():
            self.print_proof(backend, printer_name, sheets)
            return
        if not self.plan.sheets:
            # After the proof branch, which has always refused an empty
            # plan in its own words and says the more specific thing.
            # `start_print` said nothing: a session over an empty plan
            # walks both passes, finishes, and reports "Print job
            # complete." for a run that submitted nothing. Refused here as
            # well as at the button, because the dialog is reachable from
            # more than the button.
            self.status_label.setText("Nothing to print -- this plan has no sheets.")
            return
        kwargs = {} if sheets is None else {"sheets": sheets}
        session = self._session_cls(
            self.plan,
            profile,
            backend,
            test_first=self.test_first_checkbox.isChecked(),
            printer_name=printer_name,
            ask_sheets_printed=self._ask_sheets_printed,
            **kwargs,
        )
        self._session = session
        session.start()
        self._drive(session)

    def print_proof(self, backend, printer_name: str, sheets=None) -> None:
        """Print one sheet with a ruler, and say what to measure.

        No :class:`~deckle.core.print_session.PrintSession`: a proof has one
        face, no reload and no back pass, and leaving a resumable run on
        disk would mean the next print dialog offering to "finish" it. See
        :meth:`deckle.app.backend.QtPrintBackend.submit_proof`.

        :param backend: the print backend to submit through.
        :param printer_name: the target queue.
        :param sheets: the narrowed sheet selection, or ``None``.
        :returns: nothing. A failure is reported the same way a stalled run
            is, through ``show_offline_error``.
        """
        sheet_index = proof_sheet_index(self.plan, sheets)
        if sheet_index is None:
            self.status_label.setText("Nothing to proof -- this plan has no sheets.")
            return
        result = backend.submit_proof(self.plan, sheet_index, printer_name, PROOF_DPI)
        if result.error is not None:
            self._show_offline_error(printer_name, result.error)
            return
        log_event("proof_requested", printer=printer_name, sheet=sheet_index)
        self.status_label.setText(
            f"Proof of sheet {sheet_index} sent -- "
            f"{proof_rule_advice(self.plan.paper_pt[0])}"
        )

    # -- resume -----------------------------------------------------------

    def _offer_resume(self) -> None:
        chosen = self._confirm_resume(self._resumable)
        if chosen is None:
            return
        count = self._ask_resume_count(chosen)
        if count is None:
            # Cancelled at "how many sheets came out?". The session is left
            # on disk exactly as it was, so the offer comes back next time;
            # guessing a number here is the one thing that cannot be undone.
            return
        profile = self._resolve_profile(chosen.printer_name)
        backend = self._backend_cls(profile)
        try:
            session = self._session_cls.load(
                self.plan,
                profile,
                backend,
                chosen.session_id,
                ask_sheets_printed=self._ask_sheets_printed,
            )
        except StaleSessionError as exc:
            # Refusing is the safe direction. The user has already reloaded
            # the paper stack by this point, so resuming onto a re-imposed
            # document would print backs against fronts that no longer
            # match -- and they would not find out until the stack was
            # ruined. Say why, and leave them on a fresh run.
            log_exception(
                "resume_refused", exc, session_id=exc.session_id, reason=exc.reason
            )
            self._show_offline_error("Cannot resume this print run", exc.detail)
            return
        self._session = session
        session.resume(count)
        self._drive(session)

    # -- the shared submit/confirm loop --------------------------------------

    def _drive(self, session: PrintSession) -> None:
        """Advance ``session`` through chunks and pass boundaries.

        Reads only the public state ``PrintSession`` already exposes
        (``finished``, ``last_error``, ``reload_instruction``, and the
        ``pass_index``/``test_sheet_pending`` fields of ``session.state``)
        -- never recomputes sheet order or a reload instruction itself.

        :param session: the session to drive.
        :returns: nothing. Returns early on any failure, leaving the
            session resumable rather than unwinding it.
        """
        if session.last_error is not None:
            self._show_offline_error(session.printer_name, session.last_error)
            return

        if session.state["test_sheet_pending"]:
            if self._confirm_test_sheet():
                session.confirm_test_sheet()
            else:
                return

        prior_pass_index = session.state["pass_index"]
        while not session.finished and session.last_error is None:
            session.advance()
            if session.last_error is not None:
                self._show_offline_error(session.printer_name, session.last_error)
                return
            if session.finished:
                self.status_label.setText("Print job complete.")
                return
            if session.state["pass_index"] != prior_pass_index:
                prior_pass_index = session.state["pass_index"]
                instruction = session.reload_instruction
                if instruction:
                    self._confirm_reload(instruction)
                # "Test one sheet first" is offered again for this new
                # pass -- the checkbox stays enabled/visible throughout,
                # and start()-time test_first handling is PrintSession's
                # own concern (SS-11), not recomputed here.

    # -- default (real Qt) confirmation implementations ----------------------

    def _default_confirm_resume(self, resumable: list[SessionSummary]) -> SessionSummary | None:
        """Which interrupted job to resume, if any.

        This took ``resumable[0]`` and showed a Yes/No box naming the
        printer. ``list_resumable`` returned the state files in *filename*
        order and the filename is a SHA-256, so "the first one" was
        effectively random: with two interrupted runs the operator was
        offered an arbitrary one, could not reach the other, and was told
        nothing that would let them tell the two apart. Resuming the wrong
        one prints backs against fronts from a different run.

        One job stays a yes-or-no question -- a list box with one row in
        it is a worse way to ask -- but it now carries the same
        description the list would have shown. More than one gets a
        picker, newest first, which is the order ``list_resumable`` now
        returns.

        :param resumable: the interrupted sessions, newest first.
        :returns: the one to resume, or ``None`` to decline.
        """
        if not resumable:
            return None
        if len(resumable) == 1:
            summary = resumable[0]
            box = self._QMessageBox(self.widget)
            box.setWindowTitle("Resume print job?")
            box.setText(
                "An interrupted print job was found. Resume it?\n\n"
                f"{resumable_label(summary)}"
            )
            box.setStandardButtons(
                self._QMessageBox.StandardButton.Yes
                | self._QMessageBox.StandardButton.No
            )
            answer = box.exec()
            return summary if answer == self._QMessageBox.StandardButton.Yes else None

        from PySide6.QtWidgets import QInputDialog

        labels = [resumable_label(summary) for summary in resumable]
        chosen, ok = QInputDialog.getItem(
            self.widget,
            "Resume print job?",
            f"{len(resumable)} interrupted print jobs were found.\n"
            "Which one do you want to finish?\n\n"
            "Cancel to start a fresh run; the others stay resumable.",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        # By position, not by label: two runs a minute apart on the same
        # printer at the same cursor produce the same text, and picking by
        # string would silently resume whichever came first.
        return resumable[labels.index(chosen)]

    def _default_ask_resume_count(self, summary: SessionSummary) -> int | None:
        """How many sheets emerged, or ``None`` if the question was cancelled.

        ``0`` and cancel are different answers and must not collapse into
        one. Zero is a real, useful reply -- the interrupted pass produced
        nothing, resume the lot -- so returning it for a dismissed dialog
        meant Cancel reprinted the entire interrupted pass onto a stack the
        operator had already reloaded.

        The field opens on :func:`suggested_resume_count` and the label
        explains where that number came from. Opening on zero discarded
        whatever the operator had already told the "printed, but not
        recorded" popup, because :meth:`PrintSession.resume` writes the
        count it is given straight into the cursor.
        """
        from PySide6.QtWidgets import QInputDialog

        count, ok = QInputDialog.getInt(
            self.widget,
            "Resume print job",
            resume_count_prompt(summary),
            suggested_resume_count(summary),
            0,
        )
        return count if ok else None

    def _default_ask_sheets_printed(self, question: UnrecordedSheets) -> int | None:
        """How many of a failed chunk's sheets came out, or ``None``.

        Deliberately the same widget and the same closing sentence as
        :meth:`_default_ask_resume_count`: it is the same question about
        the same tray, and a program that asks it two ways teaches the
        operator that the two answers mean different things. What differs
        is everything before the question -- see
        :func:`unrecorded_sheets_prompt`.

        The spin box opens on the backend's own count and is bounded by it.
        That number is the likeliest answer, so it is the default; it is
        not the answer, so it is not assumed. Nothing above it is
        meaningful -- more sheets cannot come out than went in.
        """
        from PySide6.QtWidgets import QInputDialog

        count, ok = QInputDialog.getInt(
            self.widget,
            "Printed, but not recorded",
            unrecorded_sheets_prompt(question),
            question.submitted,
            0,
            question.submitted,
        )
        return count if ok else None

    def _default_confirm_reload(self, instruction: str) -> None:
        box = self._QMessageBox(self.widget)
        box.setWindowTitle("Reload paper")
        box.setText(instruction)
        box.setStandardButtons(self._QMessageBox.StandardButton.Ok)
        box.exec()

    def _default_confirm_test_sheet(self) -> bool:
        box = self._QMessageBox(self.widget)
        box.setWindowTitle("Test sheet")
        box.setText("Did the test sheet print correctly?")
        box.setStandardButtons(self._QMessageBox.StandardButton.Yes | self._QMessageBox.StandardButton.No)
        answer = box.exec()
        return answer == self._QMessageBox.StandardButton.Yes

    def _default_show_offline_error(self, printer_name: str, error: str) -> None:
        box = self._QMessageBox(self.widget)
        box.setWindowTitle("Printer unavailable")
        box.setText(f"{printer_name or 'The selected printer'} is offline or unreachable:\n{error}")
        box.setStandardButtons(self._QMessageBox.StandardButton.Ok)
        box.exec()
        self.status_label.setText(f"Paused -- {printer_name} unavailable. The job can be resumed.")
