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
import uuid
import dataclasses
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import SheetPlan
from deckle.core.paths import write_text_atomic
from deckle.core.printing import PrintBackend, PrintPass, PrintResult, plan_passes
from deckle.core.schema import StoredValueError, check_values
from deckle.core.profiles import PrinterProfile

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

    :param session_id: the session that was refused.
    :param reason: ``"plan"``, ``"version"`` or ``"state"``.
    :param detail: the user-facing explanation.
    :ivar session_id: the session that was refused.
    :ivar reason: ``"plan"`` -- the document's layout changed;
        ``"version"`` -- the state file came from an incompatible build;
        or ``"state"`` -- the state file's own numbers cannot drive this
        plan (see :func:`_check_state`).
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
    """A lightweight description of a resumable session, for a picker UI.

    Deliberately read straight off the state file's JSON rather than by
    constructing a :class:`PrintSession`, so listing resumable sessions
    never needs the plan they belong to.

    :ivar session_id: the identifier :meth:`PrintSession.load` takes.
    :ivar printer_name: the printer the run was submitted to.
    :ivar started_at: Unix timestamp of when the run began.
    :ivar pass_index: which pass was in progress -- ``0`` fronts, ``1``
        backs.
    :ivar sheet_cursor: how far into that pass the session had submitted.
    :ivar state_path: the state file on disk.
    """

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


def _check_state(session_id: str, data: dict, plan: SheetPlan) -> None:
    """Refuse a state file that would drive the printer somewhere real.

    The fourth reader of stored data to get this check, and the only one
    at the end of a wire. ``version`` and ``plan_hash`` were already
    guarded, and both are about the *document*; nothing looked at the
    numbers that decide what actually gets fed. A state file naming sheet
    99 of a three-sheet plan, or sheet ``-1``, or the string ``"a"``,
    passed both existing guards and was handed straight to the backend --
    as were ``copies: -3`` and ``dpi: "high"``, which go on to a printer
    driver that has no reason to expect either.

    Every bound here is checkable because ``load`` holds the plan, which
    is the whole difference from the readers that can only check types:
    "is 99 a sheet" has an answer at this point, and it is no.

    Raised as :class:`StaleSessionError`, the way this method already
    refuses a stale plan or a foreign format version, so the print dialog
    needs no new branch. The trade it records there applies unchanged:
    refusing costs a reprint the user was about to do anyway, continuing
    can cost the whole book.

    :param session_id: the session being loaded, for the message.
    :param data: the decoded state file.
    :param plan: the plan being resumed onto.
    :returns: nothing.
    :raises StaleSessionError: the state cannot drive this plan.
    """
    def refuse(detail: str) -> None:
        log_event("session_state_invalid", session_id=session_id, detail=detail)
        raise StaleSessionError(
            session_id=session_id, reason="state",
            detail=f"{detail}; start a new print run",
        )

    known = {f.name for f in dataclasses.fields(_SessionState)}
    missing = sorted(known - set(data))
    if missing:
        refuse(
            "this session's saved state is missing "
            + ", ".join(repr(name) for name in missing)
        )
    try:
        check_values(_SessionState, {k: data[k] for k in known}, subject="session field")
    except StoredValueError as exc:
        refuse(str(exc))

    valid_sheets = {sheet.index for sheet in plan.sheets}
    unknown = [index for index in data["sheets"] if index not in valid_sheets]
    if unknown:
        refuse(
            f"this session names sheet(s) {unknown} that the document does "
            f"not have (it has {len(plan.sheets)})"
        )
    if not 0 <= data["sheet_cursor"] <= len(data["sheets"]):
        refuse(
            f"this session's position ({data['sheet_cursor']}) is outside its "
            f"own list of {len(data['sheets'])} sheet(s)"
        )
    if data["pass_index"] < 0:
        refuse(f"this session's pass number ({data['pass_index']}) is negative")
    if data["copies"] < 1:
        refuse(f"this session asks for {data['copies']} copies")
    if data["dpi"] <= 0:
        refuse(f"this session asks for {data['dpi']} dpi")


class PrintSession:
    """A resumable, disk-backed state machine driving a manual-duplex print run.

    ``resume(sheets_completed)`` takes the count of sheets completed **within
    the pass that was interrupted, not cumulative across the whole job** --
    the count refers only to sheets that physically emerged during the
    interrupted pass. This is deliberate: a caller resuming pass 2 after 30
    of 60 sheets emerged supplies ``30``, not some running total that also
    includes pass 1's sheets. Guessing the wrong convention here reprints or
    skips sheets, so it is documented explicitly rather than left implicit.

    :param plan: the imposed sheets to print.
    :param profile: the printer's calibrated manual-duplex behaviour, which
        decides pass order and the reload instruction.
    :param backend: where sheets are submitted. Injected as a
        ``PrintBackend`` Protocol so tests never touch a printer.
    :param sheets: a subset of sheet indices to print, or ``None`` for the
        whole plan. This is how a single signature gets reprinted -- the
        normal path with a smaller input, not a separate branch.
    :param test_first: submit exactly one sheet and park awaiting
        :meth:`confirm_test_sheet` before continuing.
    :param printer_name: recorded in the state file and the session log.
    :param dpi: rasterization resolution handed to the backend.
    :param copies: copies per submitted chunk.
    :param chunk_size: sheets submitted per chunk. Chunking is what bounds
        the blast radius of a mid-run failure to one chunk.
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
        # `uuid4` because the other three ingredients do not distinguish two
        # sessions and were never going to. `time.time()` on Windows moves
        # in ~15ms steps, so 200 sessions of one plan to one printer
        # produced *five* distinct ids, one group of them 70 deep -- and a
        # shared id means a shared state file, so one job silently
        # overwrites another's resume point and `list_resumable` reports one
        # where there are two. Nothing derives this id from a plan; it is
        # handed out by `list_resumable` and passed back to `load`, which
        # checks `plan_hash` separately, so uniqueness is the only property
        # it ever needed.
        session_id = hashlib.sha256(
            f"{plan_hash}:{printer_name}:{started_at}:{uuid.uuid4()}".encode("utf-8")
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
        """Where this session's state file lives.

        :returns: ``<state dir>/<session_id>.json``. The directory is
            ``DECKLE_SESSION_STATE_DIR`` when set, otherwise a
            ``deckle/print_sessions`` folder under the OS temp dir.
        """
        return _state_dir() / f"{self._state.session_id}.json"

    @property
    def state(self) -> dict:
        """A read-only snapshot of the session's persisted state.

        :returns: the same JSON-shaped dict written to disk. Callers read
            ``pass_index`` and ``test_sheet_pending`` from here rather than
            recomputing either.
        """
        return self._state.to_json()

    def _current_pass(self) -> PrintPass | None:
        if self._state.pass_index >= len(self._passes):
            return None
        return self._passes[self._state.pass_index]

    @property
    def reload_instruction(self) -> str | None:
        """The reload instruction for the pass currently in progress, if any.

        :returns: the plain-language instruction computed by
            ``deckle.core.printing.plan_passes``, or ``None`` once every
            pass is done. The UI shows this verbatim; it never composes its
            own.
        """
        current = self._current_pass()
        return current.reload_instruction if current else None

    def _save(self) -> None:
        # Through the shared writer rather than a fourth hand-rolled
        # temp-and-rename. This one already had the rename, which is why a
        # failed save here always left the previous state parseable; what
        # it lacked was the `fsync` before it, and that matters more here
        # than anywhere else in Deckle. The other stores are written during
        # ordinary use and their worst realistic failure is a full disk.
        # This file exists specifically to survive a crash -- and a crash
        # is precisely when an unsynced rename can reach the platter ahead
        # of the content it renames, leaving zeros where a resumable job
        # was. It also strands nothing now: the old temp file was left
        # behind whenever the write failed, one per failure, beside the
        # state it was meant to replace.
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(self._state.to_json(), indent=2))

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

        :param plan: the *current* plan, checked against the one the
            session was started with.
        :param profile: the printer profile to resume under.
        :param backend: where the remaining sheets will be submitted.
        :param session_id: which session to load, as reported by
            :meth:`list_resumable`.
        :returns: the restored session, positioned where it left off.
        :raises FileNotFoundError: no state file for ``session_id``.
        :raises json.JSONDecodeError: the state file is corrupt. Unlike
            :meth:`list_resumable`, an explicit load does not skip past
            this -- the user asked for this session by name.
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
        _check_state(session_id, data, plan)
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
        """Enumerate every interrupted session with a state file on disk.

        :returns: one summary per readable state file, in filename order.
            Empty when the state directory does not exist.

        Never raises. A state file that cannot be read or parsed is skipped
        and logged rather than failing the whole listing -- one corrupt
        file must not hide every other resumable session.
        """
        directory = _state_dir()
        if not directory.exists():
            return []
        summaries: list[SessionSummary] = []
        for path in sorted(directory.glob("*.json")):
            # There was a `path.suffix == ".tmp"` skip here. It could never
            # fire: the glob only yields names ending `.json`, so `suffix`
            # is always `.json`, and the scratch file it meant to exclude
            # (`<id>.json.tmp`, and now `.<id>.json.<random>.tmp`) does not
            # match the glob in the first place. Dead code that reads as a
            # live precaution is the same failure as a docstring that
            # describes a capability nothing implements.
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
        """Submit one chunk of ``pass_``, on that pass's own side.

        The session chunks rather than calling ``backend.submit_pass``,
        because the cursor must advance per chunk for resume to land on a
        sheet rather than on a whole pass. The side, the turn and the pass
        index therefore have to be threaded through by hand -- omitting
        them takes the backend's front-side defaults and prints the fronts
        twice, which is what this did until 2026-09-06.

        Writing the session-log record is the backend's job and not this
        method's. It used to be both: the chunk was logged here as well,
        so every chunk appeared in the log twice, and this copy was
        unguarded. `log_print_job` raises rather than swallowing on
        purpose, and the raise landed *after* the sheets were painted and
        *before* the cursor was saved -- so a full-disk log turned a
        successful chunk into a traceback out of `session.start()` and a
        resume that reprinted every sheet of it. That is precisely the bug
        the guard in `QtPrintBackend.submit` was written to fix, sitting
        one call up the stack from the fix.
        """
        return self.backend.submit(
            self.plan,
            sheets,
            self.printer_name,
            self.copies,
            self.dpi,
            side=pass_.side,
            rotate_backs=pass_.rotate_backs,
            pass_index=pass_.index,
        )

    def start(self) -> None:
        """Begin the session: submit the first chunk (or test sheet) of pass 1.

        :returns: nothing. A submission failure is recorded on
            :attr:`last_error` and persisted, not raised -- the point of the
            state file is that a failed run can be resumed rather than
            restarted.
        """
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
        """Acknowledge the test sheet printed correctly and resume the pass.

        :returns: nothing. Clears the pending-test flag and submits the
            next chunk immediately.
        """
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

        :returns: nothing. Check :attr:`last_error` and :attr:`finished`
            afterwards; neither condition is signalled by an exception.
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

        A count at or beyond the pass length means the pass finished, and
        advances to the next one. A **negative** count means nothing at
        all and is refused: it used to be written straight into
        ``sheet_cursor``, where `-1` made `_submit_chunk` slice
        ``sheet_order[-1:]`` and resubmit exactly one sheet -- so a
        three-sheet back pass reprinted one sheet, treated the other two
        as done, and said nothing. `load` already rejects a cursor outside
        its own sheet list; this is the same bound on the path that
        *writes* the cursor rather than the one that reads it.

        The default dialog cannot produce a negative -- its spin box
        starts at zero -- but ``ask_resume_count`` is an injected seam, so
        the rule belongs with the state it protects rather than with one
        of its callers.

        :param sheets_completed: sheets that emerged **during the
            interrupted pass**, not cumulative across the job.
        :returns: nothing. Submission resumes immediately, or the pass
            advances if the count already covers it.
        :raises ValueError: ``sheets_completed`` is negative.
        """
        if sheets_completed < 0:
            raise ValueError(
                f"sheets_completed must be 0 or more, got {sheets_completed}: "
                "it counts the sheets that physically emerged during the "
                "interrupted pass"
            )
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
        """Whether every pass has been submitted.

        :returns: ``True`` once the last pass completed, at which point the
            state file has been deleted -- a finished job is not resumable.
        """
        return self._finished

    @property
    def last_error(self) -> str | None:
        """The most recent submission failure, if the run is stalled.

        :returns: the backend's error text, or ``None`` if the last
            submission succeeded. A non-``None`` value means the state file
            is still on disk and the run can be resumed.
        """
        return self._last_error
