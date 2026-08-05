"""PrintSession: the resumable print state machine.

Pure state machine behind a print run -- pass sequencing, chunked
submission, disk-backed state, and sheet-granular resume. **No Qt.**
Split out of the print dialog so the logic that must be correct is
unit-testable headlessly, separate from the UI that must be usable.

``PrintSession`` persists its state to disk after every chunk so an
interrupted job survives a crash or a printer disappearing. On resume the
caller supplies the completed sheet count -- software cannot know how many
sheets physically emerged, so the session takes it as input rather than
inferring it.

"Test one sheet" is a session mode, not a UI behavior: ``start(...,
test_first=True)`` submits exactly one sheet and parks the session
awaiting confirmation.

All submission goes through the injected ``PrintBackend`` Protocol
(``deckle.core.printing``), so tests use a stub and never touch a printer.

This module must not import Qt bindings -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import SheetPlan
from deckle.core.printing import PrintBackend, PrintPass, PrintResult, plan_passes
from deckle.core.profiles import PrinterProfile

try:
    from deckle.core.session_log import log_print_job
except ImportError:  # pragma: no cover - SS-07 (persistence/log) not yet landed
    def log_print_job(
        printer: str,
        profile: PrinterProfile,
        sheets: Sequence[int],
        dpi: int,
        pass_index: int,
    ) -> None:
        """Fallback no-op used only until ``deckle.core.session_log`` exists.

        Mirrors the ``log_print_job`` shape from SS-07 exactly so callers
        never have to change once the real module lands.
        """
        return None


# Bumped whenever the on-disk state shape changes incompatibly.
#
# 2: ``_hash_plan``'s payload gained the full ordered page sequence per side
# (``front_pages``/``back_pages`` replacing ``front_page``/``back_page``), so
# every v1 hash is incomparable with a v2 one. Without the bump, a v1 session
# would be reported to the user as "the document changed" -- which is a lie,
# and a confusing one, when what actually changed was Deckle.
STATE_VERSION = 2

# Number of sheets submitted per chunk, mirroring SS-08's default.
DEFAULT_CHUNK_SIZE = 10


class StaleSessionError(Exception):
    """A saved session cannot safely be resumed against the current plan.

    Carries a machine-readable ``reason`` so a caller can distinguish the
    two cases without parsing prose, and a ``detail`` written for the person
    at the printer rather than for a log.

    :ivar session_id: the session that was refused.
    :ivar reason: ``"plan"`` -- the document's layout changed; or
        ``"version"`` -- the state file came from an incompatible build.
    :ivar detail: a user-facing explanation ending in what to do next.
    """

    def __init__(self, session_id: str, reason: str, detail: str) -> None:
        self.session_id = session_id
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


def _state_dir() -> Path:
    """The directory holding interrupted sessions' state files."""
    override = os.environ.get("DECKLE_SESSION_STATE_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "deckle" / "print_sessions"


def _hash_plan(plan: SheetPlan) -> str:
    """A stable hash identifying a plan's sheet content.

    Covers sheet index, side presence, and the **full ordered sequence** of
    source page indices on each side. Presence plus a single index per side
    was not enough: a ``Side`` carries as many ``OutputPage``s as the
    imposition puts on that physical face -- two under ``fold_scheme="folio"``
    -- so recording only the first collided two plans that laid the same
    pages down in a different order. A resumed session could then bind to a
    document that had since been re-imposed. See REQ-014.

    A filler page has no ``source_ref`` and hashes as ``None``. An absent
    side is ``None`` -- never ``Side(pages=())``, which ``Side.__post_init__``
    rejects precisely so presence and content stay unambiguous here: the
    ``front``/``back`` presence booleans are what keep an absent side
    distinct from a side whose only page is filler.
    """
    payload = json.dumps(
        [
            {
                "index": s.index,
                "front": s.front is not None,
                "front_pages": None if s.front is None else [
                    None if p.source_ref is None else p.source_ref.page_index
                    for p in s.front.pages
                ],
                "back": s.back is not None,
                "back_pages": None if s.back is None else [
                    None if p.source_ref is None else p.source_ref.page_index
                    for p in s.back.pages
                ],
            }
            for s in plan.sheets
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SessionSummary:
    """A lightweight description of a resumable session, for a picker UI."""

    session_id: str
    printer_name: str
    started_at: float
    pass_index: int
    sheet_cursor: int
    state_path: str


@dataclass
class _SessionState:
    """The full on-disk representation of a session's progress."""

    version: int
    session_id: str
    printer_name: str
    plan_hash: str
    started_at: float
    pass_index: int
    sheet_cursor: int
    sheets: list[int]
    test_first: bool
    test_sheet_pending: bool
    dpi: int
    copies: int

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "_SessionState":
        return cls(
            version=data["version"],
            session_id=data["session_id"],
            printer_name=data["printer_name"],
            plan_hash=data["plan_hash"],
            started_at=data["started_at"],
            pass_index=data["pass_index"],
            sheet_cursor=data["sheet_cursor"],
            sheets=list(data["sheets"]),
            test_first=data["test_first"],
            test_sheet_pending=data["test_sheet_pending"],
            dpi=data["dpi"],
            copies=data["copies"],
        )


class PrintSession:
    """A resumable, disk-backed state machine driving a manual-duplex print run.

    ``resume(sheets_completed)`` takes the count of sheets completed **within
    the pass that was interrupted, not cumulative across the whole job** --
    the count refers only to sheets that physically emerged during the
    interrupted pass. This is deliberate: a caller resuming pass 2 after 30
    of 60 sheets emerged supplies ``30``, not some running total that also
    includes pass 1's sheets. Guessing the wrong convention here reprints or
    skips sheets, so it is documented explicitly rather than left implicit.
    """

    def __init__(
        self,
        plan: SheetPlan,
        profile: PrinterProfile,
        backend: PrintBackend,
        sheets: Sequence[int] | None = None,
        test_first: bool = False,
        printer_name: str = "",
        dpi: int = 300,
        copies: int = 1,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.plan = plan
        self.profile = profile
        self.backend = backend
        self.printer_name = printer_name
        self.dpi = dpi
        self.copies = copies
        self.chunk_size = chunk_size

        self._passes: list[PrintPass] = plan_passes(plan, profile, sheets=sheets)
        indices = (
            [s.index for s in plan.sheets] if sheets is None else list(sheets)
        )
        plan_hash = _hash_plan(plan)
        started_at = time.time()
        session_id = hashlib.sha256(
            f"{plan_hash}:{printer_name}:{started_at}".encode("utf-8")
        ).hexdigest()[:16]

        self._state = _SessionState(
            version=STATE_VERSION,
            session_id=session_id,
            printer_name=printer_name,
            plan_hash=plan_hash,
            started_at=started_at,
            pass_index=0,
            sheet_cursor=0,
            sheets=indices,
            test_first=test_first,
            test_sheet_pending=False,
            dpi=dpi,
            copies=copies,
        )
        self._finished = False
        self._last_error: str | None = None

    # -- state file plumbing -------------------------------------------------

    @property
    def state_path(self) -> Path:
        return _state_dir() / f"{self._state.session_id}.json"

    @property
    def state(self) -> dict:
        """A read-only snapshot of the session's persisted state."""
        return self._state.to_json()

    def _current_pass(self) -> PrintPass | None:
        if self._state.pass_index >= len(self._passes):
            return None
        return self._passes[self._state.pass_index]

    @property
    def reload_instruction(self) -> str | None:
        """The reload instruction for the pass currently in progress, if any."""
        current = self._current_pass()
        return current.reload_instruction if current else None

    def _save(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(self._state.to_json(), indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _delete_state(self) -> None:
        path = self.state_path
        if path.exists():
            path.unlink()

    # -- construction from disk ----------------------------------------------

    @classmethod
    def load(
        cls,
        plan: SheetPlan,
        profile: PrinterProfile,
        backend: PrintBackend,
        session_id: str,
    ) -> "PrintSession":
        """Reconstruct a session from its on-disk state file.

        :raises StaleSessionError: if ``plan`` no longer matches the plan the
            session was started against, or the state file was written by an
            incompatible version of Deckle.

        The plan check is the whole reason ``plan_hash`` is stored. Resuming
        onto a re-imposed document means printing backs against fronts that
        were laid out differently -- and on a printer with no duplexer,
        where the user has already physically reloaded the stack, the first
        sign of trouble is a ruined pile of paper. Refusing costs a reprint
        the user was about to do anyway; continuing can cost the whole book.
        """
        path = _state_dir() / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        state = _SessionState.from_json(data)

        if state.version != STATE_VERSION:
            log_event(
                "session_version_mismatch",
                session_id=session_id,
                found=state.version,
                expected=STATE_VERSION,
            )
            raise StaleSessionError(
                session_id=session_id,
                reason="version",
                detail=(
                    f"this session was saved by a different version of Deckle "
                    f"(state format {state.version}, this build expects "
                    f"{STATE_VERSION}); start a new print run"
                ),
            )

        current_hash = _hash_plan(plan)
        if current_hash != state.plan_hash:
            log_event(
                "session_plan_mismatch",
                session_id=session_id,
                stored=state.plan_hash,
                current=current_hash,
            )
            raise StaleSessionError(
                session_id=session_id,
                reason="plan",
                detail=(
                    "the document's layout has changed since this print run "
                    "started, so the remaining sheets no longer line up with "
                    "the pages already printed; start a new print run"
                ),
            )

        session = cls.__new__(cls)
        session.plan = plan
        session.profile = profile
        session.backend = backend
        session.printer_name = state.printer_name
        session.dpi = state.dpi
        session.copies = state.copies
        session.chunk_size = DEFAULT_CHUNK_SIZE
        session._passes = plan_passes(plan, profile, sheets=state.sheets)
        session._state = state
        session._finished = False
        session._last_error = None
        return session

    @staticmethod
    def list_resumable() -> list[SessionSummary]:
        """Enumerate every interrupted session with a state file on disk."""
        directory = _state_dir()
        if not directory.exists():
            return []
        summaries: list[SessionSummary] = []
        for path in sorted(directory.glob("*.json")):
            if path.suffix == ".tmp":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                # Skipping is right -- one corrupt file must not hide every
                # other resumable session. But a user whose state file was
                # truncated by the very crash they are trying to resume from
                # would otherwise watch the session silently not appear, with
                # no way to find out why. Record it and carry on.
                log_exception("session_state_unreadable", exc, path=str(path))
                continue
            summaries.append(
                SessionSummary(
                    session_id=data["session_id"],
                    printer_name=data["printer_name"],
                    started_at=data["started_at"],
                    pass_index=data["pass_index"],
                    sheet_cursor=data["sheet_cursor"],
                    state_path=str(path),
                )
            )
        return summaries

    # -- submission -----------------------------------------------------------

    def _submit_sheets(self, pass_: PrintPass, sheets: list[int]) -> PrintResult:
        result = self.backend.submit(
            self.plan, sheets, self.printer_name, self.copies, self.dpi
        )
        log_print_job(self.printer_name, self.profile, sheets, self.dpi, pass_.index)
        return result

    def start(self) -> None:
        """Begin the session: submit the first chunk (or test sheet) of pass 1."""
        pass_ = self._current_pass()
        if pass_ is None:
            return
        if self._state.test_first:
            self._state.test_sheet_pending = True
            first = pass_.sheet_order[:1]
            result = self._submit_sheets(pass_, first)
            if result.error:
                self._last_error = result.error
                self._save()
                return
            self._state.sheet_cursor = 1
            self._save()
            return
        self._submit_chunk()

    def confirm_test_sheet(self) -> None:
        """Acknowledge the test sheet printed correctly and resume the pass."""
        self._state.test_first = False
        self._state.test_sheet_pending = False
        self._save()
        self._submit_chunk()

    def _submit_chunk(self) -> None:
        pass_ = self._current_pass()
        if pass_ is None:
            return
        if self._state.test_sheet_pending:
            # Awaiting confirm_test_sheet(); no further submission.
            return

        remaining = pass_.sheet_order[self._state.sheet_cursor :]
        if not remaining:
            self._advance_pass()
            return

        chunk = remaining[: self.chunk_size]
        result = self._submit_sheets(pass_, chunk)
        if result.error:
            self._last_error = result.error
            self._save()
            return

        self._last_error = None
        self._state.sheet_cursor += len(chunk)
        self._save()

        if self._state.sheet_cursor >= len(pass_.sheet_order):
            self._advance_pass()

    def _advance_pass(self) -> None:
        self._state.pass_index += 1
        self._state.sheet_cursor = 0
        if self._state.pass_index >= len(self._passes):
            self._finished = True
            self._delete_state()
        else:
            self._save()

    def advance(self) -> None:
        """Submit the next chunk of sheets in the current pass.

        A no-op once the session has finished, or while a test sheet is
        pending confirmation.
        """
        if self._finished:
            return
        self._submit_chunk()

    def resume(self, sheets_completed: int) -> None:
        """Resume an interrupted pass, given the sheets that actually emerged.

        ``sheets_completed`` is **per-pass, not cumulative** -- it is the
        number of sheets that physically emerged during the pass that was
        interrupted, since software cannot observe that count directly and
        must take it as caller-supplied ground truth.
        """
        pass_ = self._current_pass()
        if pass_ is None:
            return
        self._state.sheet_cursor = sheets_completed
        self._state.test_sheet_pending = False
        self._save()
        if self._state.sheet_cursor >= len(pass_.sheet_order):
            self._advance_pass()
        else:
            self._submit_chunk()

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def last_error(self) -> str | None:
        return self._last_error
