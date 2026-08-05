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
error, resume offer) are all injectable callables so headless tests can
drive the full flow without blocking on a real modal event loop.
"""

from __future__ import annotations

from typing import Callable, Sequence

from deckle.core.diagnostics import log_exception
from deckle.core.models import SheetPlan
from deckle.core.print_session import PrintSession, SessionSummary, StaleSessionError
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
        except (FileNotFoundError, OSError) as exc:
            # No calibration profile for this printer -- try the next one.
            # Worth recording: the fallback silently lands on
            # printer_names[0], and a user wondering why Deckle picked the
            # "wrong" printer has no other way to see this happened.
            log_exception("printer_profile_unavailable", exc, printer=name)
            continue
        else:
            return name
    return printer_names[0] if printer_names else None


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
    except (FileNotFoundError, OSError):
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
        ask_resume_count: Callable[[SessionSummary], int] | None = None,
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

        self.test_first_checkbox = QCheckBox("Test one sheet first", self.widget)
        layout.addWidget(self.test_first_checkbox)

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

    # -- starting a fresh print run ------------------------------------------

    def start_print(self) -> None:
        """Construct a fresh ``PrintSession`` for the selected printer and run it.

        The signature combo supplies a ``sheets=`` subset and nothing more
        -- reprinting one gathering is the normal path with a smaller
        input, and the pass/sheet-order arithmetic stays in
        ``plan_passes``/``PrintSession``.

        :returns: nothing. A printer that is offline or unreachable is
            surfaced through ``show_offline_error`` and leaves a resumable
            session on disk, rather than raising.
        """
        printer_name = self.printer_combo.currentText()
        profile = self._resolve_profile(printer_name)
        backend = self._backend_cls(profile)
        sheets = self.signature_combo.currentData()
        kwargs = {} if sheets is None else {"sheets": sheets}
        session = self._session_cls(
            self.plan,
            profile,
            backend,
            test_first=self.test_first_checkbox.isChecked(),
            printer_name=printer_name,
            **kwargs,
        )
        self._session = session
        session.start()
        self._drive(session)

    # -- resume -----------------------------------------------------------

    def _offer_resume(self) -> None:
        chosen = self._confirm_resume(self._resumable)
        if chosen is None:
            return
        count = self._ask_resume_count(chosen)
        profile = self._resolve_profile(chosen.printer_name)
        backend = self._backend_cls(profile)
        try:
            session = self._session_cls.load(
                self.plan, profile, backend, chosen.session_id
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
        if not resumable:
            return None
        summary = resumable[0]
        box = self._QMessageBox(self.widget)
        box.setWindowTitle("Resume print job?")
        box.setText(f"An interrupted print job to {summary.printer_name!r} was found. Resume it?")
        box.setStandardButtons(self._QMessageBox.StandardButton.Yes | self._QMessageBox.StandardButton.No)
        answer = box.exec()
        return summary if answer == self._QMessageBox.StandardButton.Yes else None

    def _default_ask_resume_count(self, summary: SessionSummary) -> int:
        from PySide6.QtWidgets import QInputDialog

        count, _ok = QInputDialog.getInt(
            self.widget,
            "Resume print job",
            "How many sheets came out?",
            0,
            0,
        )
        return count

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
