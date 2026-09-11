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

The same question is asked at the other end of the run, and for the same
reason. A chunk can reach paper and *then* fail -- the session log is a
hard constraint and an unwritable one stops the job -- and at that moment
the sheets are in the output tray while the cursor still says nothing was
printed. ``ask_sheets_printed`` is the seam that asks the person who can
see the tray, and their answer moves the cursor. It is injected (a
callable, defaulted in the app layer) because this module must not import
Qt, and because a count of paper is not something to derive.

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
import logging
import os
import tempfile
import time
import uuid
import dataclasses
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from deckle.core.diagnostics import log_event, log_exception
from deckle.core.models import SheetPlan
from deckle.core.paths import write_text_atomic
from deckle.core.plan_digest import plan_digest
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
#
# 3: ``_hash_plan`` now delegates to ``plan_digest``, which covers paper size,
# placements, crops and marks rather than page indices alone. Same reasoning
# as the v2 bump, and the same lie avoided: every v2 hash is incomparable with
# a v3 one, so without this a session interrupted before the upgrade would be
# refused on resume with "the document changed" when nothing about it had.
#
# 4: the state gained ``profile_hash``. A v3 file does not carry one, so there
# is no way to tell whether the printer's calibration changed under it, and
# the resume it would allow is precisely the one this version exists to
# refuse. Same reasoning as the two bumps above, and the same lie avoided:
# the version check runs first, so such a file is reported as "a different
# version of Deckle" rather than as malformed state.
STATE_VERSION = 4

# Number of sheets submitted per chunk, mirroring SS-08's default.
DEFAULT_CHUNK_SIZE = 10


class StaleSessionError(Exception):
    """A saved session cannot safely be resumed against the current plan.

    Carries a machine-readable ``reason`` so a caller can distinguish the
    two cases without parsing prose, and a ``detail`` written for the person
    at the printer rather than for a log.

    :param session_id: the session that was refused.
    :param reason: ``"plan"``, ``"profile"``, ``"version"`` or ``"state"``.
    :param detail: the user-facing explanation.
    :ivar session_id: the session that was refused.
    :ivar reason: ``"plan"`` -- the document's layout changed;
        ``"profile"`` -- the printer's calibration changed, which decides
        the back pass's sheet order and its half turn (see
        :func:`_hash_profile`); ``"version"`` -- the state file came from
        an incompatible build; or ``"state"`` -- the state file's own
        numbers cannot drive this plan (see :func:`_check_state`).
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
    """The plan fingerprint stored with a session and checked on resume.

    Delegates to :func:`deckle.core.plan_digest.plan_digest`, which is the
    single answer to "is this the same plan?" shared with the exporter's
    render cache. This function used to have its own, much weaker one --
    sheet index, side presence and source page indices, and nothing else --
    so a session survived a change of gutter, margins, paper, crop, trim or
    scale and happily resumed onto geometry that no longer matched the
    sheets already sitting in the paper tray. Backs printed against fronts
    they no longer lined up with, and nobody found out until the stack was
    ruined. That is the precise failure ``StaleSessionError(reason="plan")``
    is for, and it was not catching it.

    Truncated to 16 hex characters. The full digest is a cache key, where a
    collision silently serves the wrong page; here a collision means a
    refusal that should have happened did not, and the value is written into
    a state file a person may read. 64 bits is far past what an accidental
    re-imposition will hit, and this comparison has no adversary -- the file
    is written and read by the same user on the same machine.

    :param plan: the plan being printed.
    :returns: the first 16 hex characters of the plan digest.
    """
    return plan_digest(plan)[:16]


def _hash_profile(profile: PrinterProfile) -> str:
    """The printer fingerprint stored with a session and checked on resume.

    ``plan_hash`` answers "is this the same document?", completely and
    correctly. It cannot answer "is this the same printer behaviour?", and
    the profile decides two things that put ink on specific paper:
    ``reverse_stack`` picks the back pass's sheet order, and ``flip_axis``
    -- against the paper's vertical edge -- picks whether every back is
    turned a half turn. Resuming a back pass
    under a different profile prints backs onto the wrong fronts, with the
    plan hash matching perfectly -- the exact failure
    ``StaleSessionError(reason="plan")`` exists to prevent, arriving by the
    one door it does not watch.

    It does not take anyone changing a setting. ``resolve_profile`` falls
    back to the first builtin when a saved profile cannot be read, and the
    two builtins differ on *both* axes -- so a calibration file that is
    deleted, corrupted, or sitting on a disconnected drive silently swaps
    one behaviour for the other.

    The whole frozen dataclass is hashed rather than the two axes alone.
    Hashing everything costs nothing and does not need revisiting when a
    third behavioural axis appears; a hand-picked pair would have to be
    remembered, which is how the pair gets out of date. The cost is a
    refusal after a change that could not have mattered -- a re-measured
    ``imageable_area_pt``, say -- and a refusal costs a reprint the
    operator was about to do anyway, which is the trade the plan guard
    already makes.

    Truncated to 16 hex characters, for the reasons :func:`_hash_plan`
    gives.

    :param profile: the printer profile the run is being driven under.
    :returns: the first 16 hex characters of the profile digest.
    """
    payload = json.dumps(asdict(profile), sort_keys=True, default=list)
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

    **There is deliberately no ``state_path``.** There was one: written by
    :meth:`PrintSession.list_resumable` from the path it had just read,
    carried through the resume picker, and read by nothing.
    :meth:`PrintSession.load` takes ``session_id`` and re-derives the path
    from it -- which is the right source, because it is the same
    derivation that *wrote* the file, and two spellings of where a
    session lives is one more than can be kept in agreement. A summary
    carrying a path nobody resumes from is an invitation to resume from
    it.
    """

    session_id: str
    printer_name: str
    started_at: float
    pass_index: int
    sheet_cursor: int


@dataclass(frozen=True)
class UnrecordedSheets:
    """A chunk that reached paper and then failed, put to the operator.

    Everything needed to say what happened before asking the question --
    which is the order it has to be said in. Someone standing at a printer
    that has just stopped needs to be told that the paper is not the
    problem before being asked to count it.

    The count of sheets is ``submitted``, taken from
    :attr:`deckle.core.printing.PrintResult.submitted`, which the backend
    sets to the number that physically printed. It is what the question is
    *about*, not its answer: the backend knows how many sheets it painted
    and handed to the spooler, and nothing in software knows how many
    landed in the tray.

    :ivar printer_name: the queue the chunk went to.
    :ivar pass_index: ``0`` fronts, ``1`` backs.
    :ivar side: which face this pass was printing.
    :ivar sheets: the chunk's sheet indices, in submission order.
    :ivar submitted: how many of them the backend says reached paper.
    :ivar error: why the run stopped, in the backend's own words.
    """

    printer_name: str
    pass_index: int
    side: Literal["front", "back"]
    sheets: tuple[int, ...]
    submitted: int
    error: str


#: Fields a state file written by an older build may legitimately lack.
#:
#: Every other field is required, and a file missing one is refused --
#: that guard is what turns a truncated or hand-edited file into a clear
#: refusal instead of a ``KeyError`` at the print dialog. But an
#: **additive** field is a different thing entirely: the file is missing
#: it because Deckle changed, not because anything is wrong with the file.
#: Refusing there would tell an operator with a half-finished job and a
#: stopped printer that their session is invalid, over a field they never
#: knew existed and whose absence means exactly what the default means.
_OPTIONAL_STATE_FIELDS = frozenset({"chunk_size"})


@dataclass
class _SessionState:
    """The full on-disk representation of a session's progress."""

    version: int
    session_id: str
    printer_name: str
    plan_hash: str
    profile_hash: str
    started_at: float
    pass_index: int
    sheet_cursor: int
    sheets: list[int]
    test_first: bool
    test_sheet_pending: bool
    dpi: int
    copies: int
    #: Sheets per submitted chunk. Persisted since 2026-09-11: it is a
    #: constructor bound, ``load`` reset it to the default, and so a
    #: non-default value **silently did not survive a resume**. The bound
    #: itself is live and doing work -- it is what limits how much paper a
    #: mid-run failure can put in question -- so a session resumed at a
    #: different chunk size would re-chunk the remainder of a run whose
    #: earlier chunks were sized differently, which is precisely the thing
    #: the bound exists to keep predictable.
    chunk_size: int = DEFAULT_CHUNK_SIZE

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "_SessionState":
        return cls(
            version=data["version"],
            session_id=data["session_id"],
            printer_name=data["printer_name"],
            plan_hash=data["plan_hash"],
            profile_hash=data["profile_hash"],
            started_at=data["started_at"],
            pass_index=data["pass_index"],
            sheet_cursor=data["sheet_cursor"],
            sheets=list(data["sheets"]),
            test_first=data["test_first"],
            test_sheet_pending=data["test_sheet_pending"],
            # `.get`, unlike every line around it, and deliberately: a file
            # written before this field existed is missing it because
            # Deckle changed. See `_OPTIONAL_STATE_FIELDS`.
            chunk_size=data.get("chunk_size", DEFAULT_CHUNK_SIZE),
            dpi=data["dpi"],
            copies=data["copies"],
        )


def _check_state(
    session_id: str, data: dict, plan: SheetPlan, profile: PrinterProfile
) -> None:
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
    :param profile: the profile being resumed under. Needed for the
        ``pass_index`` bound, which is ``plan_passes``' answer and not a
        constant kept here.
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
    missing = sorted(known - set(data) - _OPTIONAL_STATE_FIELDS)
    if missing:
        refuse(
            "this session's saved state is missing "
            + ", ".join(repr(name) for name in missing)
        )
    try:
        check_values(
            _SessionState,
            {k: data[k] for k in known if k in data},
            subject="session field",
        )
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
    # Bounded from *both* ends, like `sheet_cursor` above. Only the lower
    # bound was checked, and the upper one is the one that hangs the
    # program: `_current_pass()` returns `None` for a pass_index past the
    # end, `_submit_chunk()` returns immediately on `None`, and neither
    # `finished` nor `last_error` is ever set -- so `PrintDialog._drive`'s
    # `while not session.finished and session.last_error is None` spins
    # forever, on the GUI thread, with the window unresponsive and no way
    # to cancel. Measured: 200,000 iterations with no state change.
    #
    # The bound is asked of `plan_passes` rather than written down as 2.
    # Two is the answer today for every built-in profile, but it is
    # `plan_passes`' answer to give: a constant here would be the pass
    # count maintained in a second place, and the first sign it had drifted
    # would be this function refusing a legitimate session -- or, worse,
    # admitting the one it exists to refuse. `sheets` is validated against
    # the plan just above, so the call is safe by the time it happens.
    passes = len(plan_passes(plan, profile, sheets=data["sheets"]))
    if not 0 <= data["pass_index"] < passes:
        refuse(
            f"this session's pass number ({data['pass_index']}) is not one of "
            f"the {passes} pass(es) this document has"
        )
    if data["copies"] < 1:
        refuse(f"this session asks for {data['copies']} copies")
    if data["dpi"] <= 0:
        refuse(f"this session asks for {data['dpi']} dpi")
    # Absent is fine -- an older file -- but present and unusable is not.
    # A `chunk_size` of 0 makes `remaining[:0]` empty on a non-empty pass,
    # so `_submit_chunk` advances the pass without submitting anything and
    # the job "completes" having printed nothing.
    if "chunk_size" in data and data["chunk_size"] < 1:
        refuse(
            f"this session submits {data['chunk_size']} sheet(s) per chunk"
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
        the blast radius of a mid-run failure to one chunk. **Persisted,
        and restored by :meth:`load`** -- it used to be reset to the
        default on resume, so a non-default value did not survive one. No
        production caller sets it today; the suite does, extensively, and
        a bound that accepts a value and quietly discards it is worse than
        no bound at all.
    :param ask_sheets_printed: asked how many sheets of a failed chunk
        actually came out, when the backend reports that some did. Given an
        :class:`UnrecordedSheets`; returns the count, or ``None`` to decline
        to say. ``None`` -- and the absence of the callable altogether --
        leaves the cursor where it was, which reprints the chunk on resume;
        that is the safe direction, and it is the direction taken whenever
        nobody has answered.
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
        ask_sheets_printed: Callable[["UnrecordedSheets"], int | None] | None = None,
    ) -> None:
        self.plan = plan
        self.profile = profile
        self.backend = backend
        self.printer_name = printer_name
        self.dpi = dpi
        self.copies = copies
        self.chunk_size = chunk_size
        self.ask_sheets_printed = ask_sheets_printed

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
            profile_hash=_hash_profile(profile),
            started_at=started_at,
            pass_index=0,
            sheet_cursor=0,
            sheets=indices,
            test_first=test_first,
            test_sheet_pending=False,
            dpi=dpi,
            copies=copies,
            chunk_size=chunk_size,
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
        ask_sheets_printed: Callable[["UnrecordedSheets"], int | None] | None = None,
    ) -> "PrintSession":
        """Reconstruct a session from its on-disk state file.

        :param plan: the *current* plan, checked against the one the
            session was started with.
        :param profile: the printer profile to resume under.
        :param backend: where the remaining sheets will be submitted.
        :param session_id: which session to load, as reported by
            :meth:`list_resumable`.
        :param ask_sheets_printed: as on the constructor. A resumed run can
            fail the same way the original did, so it needs the same seam;
            a resumed session that could not ask would silently be the one
            place the app stopped asking.
        :returns: the restored session, positioned where it left off.
        :raises FileNotFoundError: no state file for ``session_id``.
        :raises json.JSONDecodeError: the state file is corrupt. Unlike
            :meth:`list_resumable`, an explicit load does not skip past
            this -- the user asked for this session by name.
        :raises StaleSessionError: if ``plan`` no longer matches the plan the
            session was started against, if ``profile`` no longer matches
            the printer calibration the run started under, or if the state
            file was written by an incompatible version of Deckle.

        The plan check is the whole reason ``plan_hash`` is stored. Resuming
        onto a re-imposed document means printing backs against fronts that
        were laid out differently -- and on a printer with no duplexer,
        where the user has already physically reloaded the stack, the first
        sign of trouble is a ruined pile of paper. Refusing costs a reprint
        the user was about to do anyway; continuing can cost the whole book.

        The profile check is here for the same reason and stops the same
        stack being ruined by the other half of the job's identity. The
        plan says what to print; the profile says in what order and which
        way up (see :func:`_hash_profile`). The caller re-reads the profile
        from disk at resume time, so a calibration run -- or a profile file
        that simply became unreadable and fell back to a builtin -- changes
        the answer with nobody touching a setting.
        """
        path = _state_dir() / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        # Before `_check_state`, which requires every field of the current
        # `_SessionState` to be present: a file from an older format is
        # missing fields *because Deckle changed*, and reporting that as
        # malformed state would send the operator looking at their own
        # file for a fault that is not there.
        found_version = data.get("version")
        if found_version != STATE_VERSION:
            log_event(
                "session_version_mismatch",
                session_id=session_id,
                found=found_version,
                expected=STATE_VERSION,
            )
            raise StaleSessionError(
                session_id=session_id,
                reason="version",
                detail=(
                    f"this session was saved by a different version of Deckle "
                    f"(state format {found_version}, this build expects "
                    f"{STATE_VERSION}); start a new print run"
                ),
            )

        _check_state(session_id, data, plan, profile)
        state = _SessionState.from_json(data)

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

        current_profile = _hash_profile(profile)
        if current_profile != state.profile_hash:
            log_event(
                "session_profile_mismatch",
                session_id=session_id,
                stored=state.profile_hash,
                current=current_profile,
            )
            raise StaleSessionError(
                session_id=session_id,
                reason="profile",
                detail=(
                    "this printer's calibration has changed since the print "
                    "run started, so the remaining sheets would be fed in a "
                    "different order or turned a different way from the ones "
                    "already printed; start a new print run"
                ),
            )

        session = cls.__new__(cls)
        session.plan = plan
        session.profile = profile
        session.backend = backend
        session.printer_name = state.printer_name
        session.dpi = state.dpi
        session.copies = state.copies
        # From the state file, not the default. Resetting it here is what
        # made `chunk_size` a bound you could set and silently not get
        # back: a session started at 3 resumed at 10, re-chunking the
        # remainder of a run whose earlier chunks were sized differently.
        session.chunk_size = state.chunk_size
        session.ask_sheets_printed = ask_sheets_printed
        session._passes = plan_passes(plan, profile, sheets=state.sheets)
        session._state = state
        session._finished = False
        session._last_error = None
        return session

    @staticmethod
    def list_resumable() -> list[SessionSummary]:
        """Enumerate every interrupted session with a state file on disk.

        :returns: one summary per readable state file, **most recently
            started first**. Empty when the state directory does not exist.

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
                summary = SessionSummary(
                    session_id=data["session_id"],
                    printer_name=data["printer_name"],
                    started_at=data["started_at"],
                    pass_index=data["pass_index"],
                    sheet_cursor=data["sheet_cursor"],
                )
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                # Skipping is right -- one corrupt file must not hide every
                # other resumable session. But a user whose state file was
                # truncated by the very crash they are trying to resume from
                # would otherwise watch the session silently not appear, with
                # no way to find out why. Record it and carry on.
                #
                # The five lookups belong inside this `try`, not after it.
                # A file that is valid JSON but not a session -- truncated
                # to `{}`, half-written, or from a build that named these
                # fields differently -- parses cleanly and then raises
                # `KeyError` (or `TypeError`, if the JSON is a list or a
                # string). This method's docstring promises it never raises,
                # and `PrintDialog.__init__` believes it: one such file in
                # the state directory made the print dialog impossible to
                # open at all, which is a far worse failure than one
                # unlisted session.
                log_exception("session_state_unreadable", exc, path=str(path))
                continue
            summaries.append(summary)
        # Newest first, not filename order. The filename is a SHA-256 of
        # `plan_hash:printer:started_at:uuid4`, so sorting by it is sorting
        # by a hash -- and the dialog's picker used to take element zero of
        # this list. "An arbitrary one of your interrupted jobs" is not an
        # answer to "which job shall I resume", and resuming the wrong one
        # prints backs against fronts from a different run.
        #
        # `started_at` was serialised, round-tripped and read nowhere in
        # `deckle/`. This is the read.
        summaries.sort(key=lambda summary: summary.started_at, reverse=True)
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

    def _sheets_that_reached_paper(
        self, pass_: PrintPass, chunk: list[int], result: PrintResult
    ) -> int:
        """How far the cursor may move past a chunk that failed.

        A chunk fails in two physically different ways and the backend
        already distinguishes them. Painting that raised leaves
        ``submitted`` at 0 -- no sheet is known to have come out, the chunk
        may have died on its first page or its last, and the cursor stays
        put so a resume reprints it. A chunk that printed and then could
        not be *recorded* leaves ``submitted`` at the full count: the paper
        is in the tray and the cursor saying otherwise is what sends the
        operator back through the machine for a second stack.

        Which of those actually happened is a question about the output
        tray, so it is asked rather than assumed -- `docs/decisions.md`,
        2026-09-09. The app supplies ``ask_sheets_printed``; declining to
        answer, or having nobody to ask, leaves the cursor where it was.
        Reprinting a chunk costs paper, and advancing past sheets that
        never came out costs the book.

        The answer is clamped to the chunk. It counts *these* sheets, so
        neither a negative nor a number larger than what was sent can mean
        anything -- and refusing at this depth would turn an answered
        question into a traceback out of :meth:`start`, which promises the
        opposite. ``resume`` raises on a negative because it is called by a
        caller that can still show the message; this is not.

        :param pass_: the pass the chunk belongs to, for the question.
        :param chunk: the sheet indices submitted.
        :param result: what the backend reported.
        :returns: how many sheets to count as done, 0 through ``len(chunk)``.
        """
        if result.submitted <= 0 or self.ask_sheets_printed is None:
            return 0

        question = UnrecordedSheets(
            printer_name=self.printer_name,
            pass_index=pass_.index,
            side=pass_.side,
            sheets=tuple(chunk),
            submitted=result.submitted,
            error=result.error or "",
        )
        answer = self.ask_sheets_printed(question)
        if answer is None:
            log_event(
                "unrecorded_sheets_undecided",
                printer=self.printer_name,
                pass_index=pass_.index,
                sheets=list(chunk),
            )
            return 0

        counted = max(0, min(int(answer), len(chunk)))
        if counted != answer:
            log_event(
                "unrecorded_sheets_count_clamped",
                level=logging.WARNING,
                printer=self.printer_name,
                answered=answer,
                counted=counted,
                chunk=len(chunk),
            )
        log_event(
            "unrecorded_sheets_counted",
            printer=self.printer_name,
            pass_index=pass_.index,
            sheets=list(chunk),
            counted=counted,
        )
        return counted

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
                # One sheet is still a sheet. A test sheet that printed and
                # then could not be recorded is the same paper in the same
                # tray as any other chunk's, so it is counted the same way.
                self._state.sheet_cursor += self._sheets_that_reached_paper(
                    pass_, first, result
                )
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
            # The cursor follows the paper. Sheets that came out are behind
            # the cursor even though the run stopped -- otherwise a resume
            # feeds them through a second time, onto a stack the operator
            # has by then already reloaded. The pass is deliberately *not*
            # advanced even if this fills it: the run is stalled, and
            # walking on to the back pass here would print backs while the
            # fronts are still an open question.
            self._state.sheet_cursor += self._sheets_that_reached_paper(
                pass_, chunk, result
            )
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
