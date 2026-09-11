"""AppState: owns the live ``Project``, undo/redo, and debounced autosave.

``AppState`` is the single owner of the in-memory ``Project`` instance and
the undo stack -- views never hold their own copy, they mutate through
``AppState.mutate`` and read back ``AppState.project``. Undo is
snapshot-based: a bounded deque of ``Project`` instances (frozen
dataclasses, so a "snapshot" is just holding a reference to the prior
value -- no deep copy needed). Default depth is 50; the 51st mutation
silently discards the oldest snapshot rather than growing forever or
raising.

Reordering, rotating, skipping, and inserting blanks all operate on the
in-memory ``list[SourcePage]`` -- plain Python value objects, never a
``pikepdf.Pdf.pages`` list. See the module docstring in
``deckle/app/views/arrange_view.py`` for why that boundary matters.

Every ``mutate`` call schedules a debounced (500ms) autosave to
``AppState.autosave_path`` so a killed process loses at most the last
half-second of edits. The debounce uses a cancel-and-reschedule timer so a
burst of mutations (e.g. dragging through several reorder steps) writes
once, not once per mutation.

This module must not import PySide6 -- it is exercised directly by
headless tests and must not require a display server or event loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from deckle.core.diagnostics import log_exception
from deckle.core.loader import apply_page_selection
from deckle.core.models import (
    BLANK_SOURCE_PATH, Project, SourcePage, SourceRef, is_blank_page,
)
from deckle.core.paths import data_dir, evict_oldest_files
from deckle.core.project_io import save_project

DEFAULT_UNDO_DEPTH = 50
DEFAULT_AUTOSAVE_DELAY_S = 0.5

UNSAVED_AUTOSAVE_DIR = "autosave"
"""Where a never-saved project's autosave lives, below ``data_dir()``.

``data_dir``, not ``config_dir``: this is data the application accumulates,
like the diagnostics log, rather than a setting the user chose.
"""

UNSAVED_AUTOSAVE_KEY_CHARS = 16

UNSAVED_AUTOSAVE_KEEP = 10
"""How many never-saved autosaves to keep, newest first.

Matching ``recent.MAX_ENTRIES`` for the same reason: long enough to cover
the jobs someone is moving between, short enough that the recovery list is
still something a person will read.
"""

UNSAVED_AUTOSAVE_MAX_AGE_S = 30 * 24 * 60 * 60

# Sentinel SourceRef.path used for a page inserted via "insert blank" --
# it references no real file on disk. Renderers/exporters that need to
# treat blanks specially can check ``ref.path == BLANK_SOURCE_PATH``.



def autosave_path_for(project_path: str | None) -> str | None:
    """Where a project's autosave lives, or ``None`` if it has never been
    saved.

    Public because the recovery prompt needs the same answer before an
    ``AppState`` exists -- it is deciding whether to build one from the
    autosave in the first place. Two spellings of `<project>.autosave`
    would be a rule expressed twice.

    :param project_path: the project file, or ``None``.
    :returns: the autosave path, or ``None``.
    """
    if project_path is None:
        return None
    return f"{project_path}.autosave"


def autosave_recovery_offer(project_path: str | None) -> str | None:
    """The autosave worth offering back, or ``None`` to stay silent.

    Lives here rather than beside the prompt that shows it because the
    answer is ``autosave_path_for``'s plus two ``mtime`` reads: putting it
    anywhere else would be a second module that knows how
    ``<project>.autosave`` is spelled. Pure, and Qt-free like the rest of
    this module, so the decision is testable without a display.

    Autosave has been written on every edit and flushed on close since the
    beginning, and nothing ever offered it back -- the file was a corpse.
    This is the decision that changes that, kept pure and out of the
    dialog so it can be tested without a display.

    **Detection cannot be "does the file exist".** ``_on_close_event``
    flushes the autosave, so it exists after every clean quit. It is
    *mtime*: offer when the autosave is newer than the project.

    That rule is chosen for the crash case and gets close-without-saving
    right as a consequence -- those edits are real, the user declined to
    save them, and offering them back is correct rather than a false
    positive. Saving makes the project newer, which is what stops the
    prompt appearing on every open.

    Ties go to silence. A spurious prompt teaches someone to dismiss
    prompts, which costs more than the rare recovery it would offer.

    :param project_path: where the project lives, or ``None`` for one
        that has never been saved and so has no autosave.
    :returns: the autosave path, or ``None``.
    """
    autosave_path = autosave_path_for(project_path)
    if autosave_path is None or not os.path.isfile(autosave_path):
        return None
    try:
        autosave_time = os.path.getmtime(autosave_path)
    except OSError:
        return None
    try:
        project_time = os.path.getmtime(project_path)
    except OSError:
        # The project is gone and the autosave is not. That is the case
        # where recovery matters most, not a reason to discard the only
        # remaining copy of the work.
        return autosave_path
    return autosave_path if autosave_time > project_time else None


def unsaved_autosave_key(pages: Sequence[SourcePage]) -> str | None:
    """A stable identity for a never-saved project, from its sources.

    A project that has never been saved has no path to hang an autosave
    off, so it is identified by **what it was made from**: the same import
    lands in the same file across crashes, rather than accumulating one
    file per launch.

    The pairs are a *set* and are sorted, so a page imported twice does not
    change the key and neither does page order -- reordering pages is
    exactly the work being protected, and it has to land in the same file.
    Blanks reference no file and are left out for the same reason.

    :param pages: the project's pages.
    :returns: 16 hex characters, or ``None`` when nothing has been
        imported. A window with nothing in it has nothing worth recovering.
    """
    pairs = {
        (os.path.normpath(os.path.abspath(page.ref.path)), page.ref.sha256)
        for page in pages
        if not is_blank_page(page)
    }
    if not pairs:
        return None
    payload = "".join(f"{path}\n{sha}\n" for path, sha in sorted(pairs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[
        :UNSAVED_AUTOSAVE_KEY_CHARS
    ]


def unsaved_autosave_path(pages: Sequence[SourcePage]) -> str | None:
    """The autosave file for a never-saved project, or ``None``.

    Suffixed ``.deckle.autosave`` so one glob finds both kinds of autosave,
    and so "an autosave is never mistaken for a save" still reads off the
    name.

    :param pages: the project's pages.
    :returns: the path, or ``None`` when nothing has been imported.
    """
    key = unsaved_autosave_key(pages)
    if key is None:
        return None
    return str(data_dir(UNSAVED_AUTOSAVE_DIR) / f"{key}.deckle.autosave")


@dataclass(frozen=True)
class UnsavedAutosave:
    """One never-saved project waiting to be recovered.

    :ivar path: the autosave file.
    :ivar modified_at: its mtime, epoch seconds.
    :ivar page_count: how many pages it holds.
    :ivar first_source: the first page's source path, or ``""`` for none.
    """

    path: str
    modified_at: float
    page_count: int
    first_source: str


def unsaved_autosave_offers(now: float | None = None) -> list[UnsavedAutosave]:
    """Never-saved autosaves worth offering back, newest first.

    Reads each file's JSON directly rather than through
    :func:`deckle.core.project_io.load_project`. That function verifies
    every source hash and raises ``SourceMissingError`` when a source has
    moved -- and a moved source is exactly when the recovery matters most,
    so routing the offer list through it would hide the offers a user needs.

    :param now: the current time, injectable for tests.
    :returns: the offers, newest first. Anything older than
        :data:`UNSAVED_AUTOSAVE_MAX_AGE_S`, or that is not readable JSON
        with a ``pages`` list, is left out silently -- a corrupt recovery
        file has nothing to offer.

    Never raises: a missing directory returns ``[]``.
    """
    directory = data_dir(UNSAVED_AUTOSAVE_DIR)
    moment = time.time() if now is None else now
    offers: list[UnsavedAutosave] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []
    for entry in entries:
        if not entry.name.endswith(".deckle.autosave"):
            continue
        try:
            if not entry.is_file():
                continue
            modified_at = entry.stat().st_mtime
            payload = json.loads(
                open(entry.path, encoding="utf-8").read()
            )
        except (OSError, ValueError):
            continue
        if moment - modified_at > UNSAVED_AUTOSAVE_MAX_AGE_S:
            continue
        if not isinstance(payload, dict):
            continue
        stored = payload.get("pages")
        if not isinstance(stored, list):
            continue
        # A stored page is flat -- `path` sits beside `rotate_deg`, not
        # under a nested `ref` -- see `project_io._page_to_dict`.
        first_source = ""
        for page in stored:
            if isinstance(page, dict) and page.get("path"):
                first_source = str(page["path"])
                break
        offers.append(
            UnsavedAutosave(
                path=entry.path,
                modified_at=modified_at,
                page_count=len(stored),
                first_source=first_source,
            )
        )
    offers.sort(key=lambda offer: offer.modified_at, reverse=True)
    return offers


def unsaved_autosave_label(offer: UnsavedAutosave) -> str:
    """A one-line description of an unsaved session, for a list.

    Pure so the wording is testable headlessly, matching how the recent
    list's label is written.

    :param offer: the session to describe.
    :returns: the label.
    """
    name = os.path.basename(offer.first_source) or "Untitled"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(offer.modified_at))
    return f"{name} -- {offer.page_count} page(s), {when}"


def make_blank_page(paper_pt: tuple[float, float]) -> SourcePage:
    """A ``SourcePage`` representing an inserted blank sheet.

    Carries no real file reference -- ``ref.path`` is ``BLANK_SOURCE_PATH``
    and ``page_index`` is ``-1``, so callers can distinguish it from any
    real imported page.

    :param paper_pt: the page size to give the blank, as
        ``(width, height)`` in points -- normally the project's paper, so
        the blank imposes like every other page.
    :returns: the blank page.
    """
    width_pt, height_pt = paper_pt
    ref = SourceRef(
        path=BLANK_SOURCE_PATH,
        page_index=-1,
        sha256="",
        width_pt=width_pt,
        height_pt=height_pt,
    )
    return SourcePage(ref=ref, rotate_deg=0, skipped=False)


def reorder_pages(project: Project, old_index: int, new_index: int) -> Project:
    """Move the page at ``old_index`` to ``new_index``, in place order.

    Operates purely on ``project.pages`` (a plain ``list[SourcePage]``) --
    never touches a PDF page-tree.

    **Not the drag-and-drop path.** The grid uses
    :func:`reorder_pages_to`, which takes the whole resulting order, for
    the reason that function's docstring gives: Qt reorders its own model
    during a drop by inserting a copy and removing the original rather
    than emitting a move, so reconstructing a ``(from, to)`` pair from
    what the view is left holding is guesswork. ``arrange_view`` used to
    carry a ``reorder(state, old, new)`` wrapper around this; it had no
    production caller and was removed, because its only effect if one had
    appeared would have been to reintroduce that guess.

    :param project: the project to derive a new one from. Never mutated;
        ``Project`` is frozen, which is what makes undo a matter of holding
        a reference rather than deep-copying.
    :param old_index: where the page is now.
    :param new_index: where it should end up.
    :returns: a new project with the reordered page list.
    :raises IndexError: ``old_index`` is out of range.
    """
    pages = list(project.pages)
    page = pages.pop(old_index)
    pages.insert(new_index, page)
    return replace(project, pages=pages)


def reorder_pages_to(project: Project, order: list[int]) -> Project:
    """Rearrange the pages into ``order``, given as old indices.

    Takes the whole resulting order rather than a single move, because
    that is what a drag-and-drop actually produces. Qt reorders its own
    model during the drop -- by inserting a copy and removing the original,
    not by emitting a move -- so the only reliable account of what happened
    is the order the view is left holding. Reconstructing a
    ``(from, to)`` pair from that is guesswork; applying it directly is not.

    :param project: the project to derive a new one from. Never mutated.
    :param order: every existing page index, exactly once, in their new
        order.
    :returns: a new project with the pages rearranged.
    :raises ValueError: ``order`` is not a permutation of the page indices.
        A partial or duplicated order would silently drop or clone pages.
    """
    if sorted(order) != list(range(len(project.pages))):
        raise ValueError(
            f"order must be a permutation of 0..{len(project.pages) - 1}, got {order!r}"
        )
    return replace(project, pages=[project.pages[i] for i in order])


def set_rotation(project: Project, index: int, rotate_deg: int) -> Project:
    """Set one page's user rotation.

    :param project: the project to derive a new one from.
    :param index: which page.
    :param rotate_deg: the new rotation, normalised into ``[0, 360)`` so a
        UI can keep adding 90 without ever needing to wrap it itself.
    :returns: a new project with that page rotated.
    :raises IndexError: ``index`` is out of range.
    """
    pages = list(project.pages)
    pages[index] = replace(pages[index], rotate_deg=rotate_deg % 360)
    return replace(project, pages=pages)


def toggle_skip(project: Project, index: int) -> Project:
    """Flip one page's ``skipped`` flag.

    A skipped page keeps its slot in the list rather than being removed, so
    un-skipping puts it back exactly where it was.

    :param project: the project to derive a new one from.
    :param index: which page.
    :returns: a new project with that page's flag flipped.
    :raises IndexError: ``index`` is out of range.
    """
    pages = list(project.pages)
    pages[index] = replace(pages[index], skipped=not pages[index].skipped)
    return replace(project, pages=pages)


def skip_pages(project: Project, indices: Sequence[int]) -> Project:
    """Mark every page in ``indices`` skipped, leaving the rest alone.

    Distinct from :func:`toggle_skip`, which flips one page, and from
    ``arrange_view.skip_many``, which decides once for a selection: a range
    is *stated*, not toggled, so naming a page that is already skipped is
    not a request to bring it back.

    :param project: the project to derive a new one from.
    :param indices: 0-based page indices.
    :returns: a new project.
    :raises ValueError: an index is outside the document.
    """
    return replace(
        project,
        pages=apply_page_selection(project.pages, skip=list(indices)),
    )


def remove_pages(project: Project, indices: Sequence[int]) -> Project:
    """Delete pages from the document.

    The only operation here that removes rather than flags. Skipping keeps
    a page in the list so un-skipping restores it where it was, which is
    right for "not in this book" and wrong for "not in this project at
    all": a 312-page scan whose sixteen pages of front matter are merely
    skipped is still 312 pages in the grid, in the ``.deckle``, and in
    every source-hash check ``load_project`` runs on open.

    Undoable like every other mutation -- it goes through
    :meth:`AppState.mutate`, which snapshots the whole frozen ``Project``
    first, so a removal is one Ctrl+Z away for as long as it is on the
    bounded stack.

    :param project: the project to derive a new one from. Never mutated.
    :param indices: which pages to remove. Order and repeats are
        irrelevant; each named page is removed once.
    :returns: a new project without them.
    :raises IndexError: an index is out of range -- matching
        :func:`set_rotation` and :func:`toggle_skip`, which raise rather
        than skipping a bad index.
    """
    doomed = set(indices)
    for index in doomed:
        if not 0 <= index < len(project.pages):
            raise IndexError(index)
    return replace(
        project,
        pages=[page for i, page in enumerate(project.pages) if i not in doomed],
    )


def insert_blank(project: Project, index: int) -> Project:
    """Insert a blank page at ``index``, sized to the project's paper.

    :param project: the project to derive a new one from.
    :param index: where the blank goes. Out-of-range values follow
        ``list.insert`` semantics and clamp rather than raising.
    :returns: a new project with the blank inserted.
    """
    pages = list(project.pages)
    pages.insert(index, make_blank_page(project.layout.paper))
    return replace(project, pages=pages)


class AppState:
    """Owns the live ``Project``, undo/redo history, and debounced autosave.

    ``project_path`` is the path the project was opened from (or will be
    saved to); autosave writes to ``f"{project_path}.autosave"`` so it
    never clobbers the user's last explicit save. When ``project_path`` is
    ``None`` -- a brand-new, never-saved project -- autosave goes to
    ``data_dir("autosave")`` under a key derived from the imported
    sources, so an hour of arranging before the first Save is not lost to
    a crash. Only a window with nothing imported writes nothing.

    :param project: the project to own.
    :param project_path: where the project lives on disk, or ``None`` for
        one that has never been saved.
    :param undo_depth: how many snapshots to keep. The oldest is discarded
        silently past this -- never grown without bound, never raised.
    :param autosave_delay_s: the debounce window. A burst of mutations
        writes once, not once per mutation.
    :param timer_factory: injected so tests can drive the debounce without
        waiting on a real timer.
    :ivar project_path: the path the project was opened from, or was most
        recently saved to. Assigning it re-points the autosave.
    :ivar autosave_path: read-only, and never stored. Derived on every read
        from ``project_path`` when there is one and from the project's own
        sources when there is not.
    """

    def __init__(
        self,
        project: Project,
        project_path: str | None = None,
        undo_depth: int = DEFAULT_UNDO_DEPTH,
        autosave_delay_s: float = DEFAULT_AUTOSAVE_DELAY_S,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self._project = project
        self.project_path = project_path
        # What was last written to the user's own file, held by identity.
        # `Project` is frozen, so undo restores the very object that was
        # saved -- which means undoing back to the saved state clears the
        # dirty flag for free, rather than leaving a window titled
        # "modified" over a document identical to the one on disk.
        self._saved_project = project
        self._undo_stack: deque[Project] = deque(maxlen=undo_depth)
        self._redo_stack: deque[Project] = deque(maxlen=undo_depth)
        self._autosave_delay_s = autosave_delay_s
        self._timer_factory = timer_factory
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        # Guards the autosave file, not the project -- see `_do_autosave`.
        self._save_lock = threading.Lock()

    @property
    def project(self) -> Project:
        """The live project.

        :returns: the current value. Views read this rather than keeping
            their own copy -- there is exactly one owner.
        """
        return self._project

    @property
    def autosave_path(self) -> str | None:
        """Where this project's autosave goes right now.

        Derived on every read rather than cached, because **both** inputs
        change underneath it. ``project_path`` does: a session almost
        always starts with no path at all (``main()`` opens a blank
        project), and Save is what gives it one, so a value computed once
        in ``__init__`` would still be ``None`` after that Save and the
        project the user had just named would go on autosaving nowhere.
        And the sources do: a never-saved project is keyed on what it was
        imported from, which an import changes.

        With a ``project_path`` this is ``<project>.autosave``, beside the
        user's own file. Without one it is
        ``data_dir("autosave")/<key>.deckle.autosave`` -- because the
        alternative was ``None``, which made every ``_schedule_autosave``
        and ``flush_autosave`` on a fresh window return immediately.
        Importing 200 pages, arranging for an hour and losing power wrote
        nothing at all.

        :returns: the autosave path, or ``None`` for a project that has
            neither a path nor an imported source.
        """
        if self.project_path is not None:
            return autosave_path_for(self.project_path)
        return unsaved_autosave_path(self._project.pages)

    def discard_unsaved_autosave(
        self, pages: Sequence[SourcePage] | None = None
    ) -> None:
        """Delete the never-saved autosave for ``pages``, if there is one.

        Called after Save project: the work now exists in a file the user
        named, so offering to recover it at the next launch would be an
        offer to recover something they already have.

        :param pages: the pages to key on, defaulting to the current ones.
            A save does not change them, so the default names the right
            file.
        :returns: nothing, and never raises -- a recovery file that will
            not delete must not turn a successful save into an error.
        """
        path = unsaved_autosave_path(
            self._project.pages if pages is None else pages
        )
        if path is None:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            log_exception("unsaved_autosave_discard_failed", exc, path=path)

    @property
    def dirty(self) -> bool:
        """Whether the project differs from the last explicit save.

        Autosave does not clear this, and must not: an autosave is
        Deckle's insurance against a crash, written to
        ``<project>.autosave`` precisely so it never touches the file the
        user named. Treating it as a save would mean the window stopped
        saying "unsaved" while the user's own file was still stale, and
        the close prompt -- the thing standing between a session's work
        and the bin -- would never appear.

        Held by identity rather than equality: `Project` is frozen, so the
        object that was saved is the object undo restores, and comparing
        identities is both cheaper and more honest than comparing two
        deeply-nested dataclasses field by field.

        :returns: whether there is work the user has not saved.
        """
        return self._project is not self._saved_project

    def mark_saved(self) -> None:
        """Record that the current project is what is now on disk.

        Called after an explicit save -- never after an autosave, for the
        reason :attr:`dirty` gives.

        :returns: nothing.
        """
        with self._lock:
            self._saved_project = self._project

    def mark_unsaved(self) -> None:
        """Declare the project different from whatever is on disk.

        For work that exists only in memory the moment it arrives:
        recovered autosave content is loaded, not saved, and a window that
        opened it clean would let the user close it again and lose the
        recovery they had just accepted.

        :returns: nothing.
        """
        with self._lock:
            self._saved_project = None

    @property
    def can_undo(self) -> bool:
        """:returns: whether there is a snapshot to go back to."""
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        """:returns: whether an undone change can be reapplied."""
        return bool(self._redo_stack)

    def mutate(self, fn: Callable[[Project], Project]) -> Project:
        """Apply ``fn`` to the current project, snapshotting first.

        The pre-mutation project is pushed onto the (bounded) undo stack
        before ``fn`` runs, and the redo stack is cleared -- a fresh
        mutation invalidates any previously-undone future.

        :param fn: a pure function from the old project to the new one.
            Every project change in the app goes through one of these, which
            is what makes undo and autosave uniform rather than per-view.
        :returns: the new project.
        :raises Exception: whatever ``fn`` raises, unchanged and with no
            snapshot pushed -- a mutation that fails must not leave a
            half-applied state on the undo stack.
        """
        with self._lock:
            previous = self._project
            new_project = fn(previous)
            self._undo_stack.append(previous)
            self._redo_stack.clear()
            self._project = new_project
        self._schedule_autosave()
        return self._project

    def undo(self) -> Project:
        """Step back one snapshot.

        :returns: the restored project, or the current one unchanged when
            there is nothing to undo. A no-op rather than an error, so a UI
            can wire this to a key without guarding it.
        """
        with self._lock:
            if not self._undo_stack:
                return self._project
            self._redo_stack.append(self._project)
            self._project = self._undo_stack.pop()
        self._schedule_autosave()
        return self._project

    def redo(self) -> Project:
        """Reapply the most recently undone change.

        :returns: the restored project, or the current one unchanged when
            there is nothing to redo.
        """
        with self._lock:
            if not self._redo_stack:
                return self._project
            self._undo_stack.append(self._project)
            self._project = self._redo_stack.pop()
        self._schedule_autosave()
        return self._project

    # -- autosave ------------------------------------------------------

    def _schedule_autosave(self) -> None:
        if self.autosave_path is None:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = self._timer_factory(self._autosave_delay_s, self._do_autosave)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _do_autosave(self) -> None:
        path = self.autosave_path
        if path is None:
            return
        # Serialised against other autosaves, because there are two writers
        # and `cancel` is not a join: a debounce timer that has already
        # fired is inside `save_project` when `flush_autosave` starts, so
        # both write this path at once. Shutdown is exactly when that
        # happens -- the window flushes while the last debounce is in
        # flight, which is the case flush exists for.
        #
        # On Windows the loser of that race does not lose quietly.
        # `os.replace` is `MoveFileEx`, which fails with `PermissionError:
        # [WinError 5]` when another handle holds the target, so one of the
        # two saves raises: on the timer thread that kills the autosave
        # silently, and on the flush it surfaces during shutdown.
        #
        # A separate lock from `self._lock` on purpose -- holding the state
        # lock across disk I/O would block every mutation for the length of
        # a write, and the UI thread is what does the mutating.
        with self._save_lock:
            # `data_dir("autosave")` does not exist on a first run, and
            # `save_project` -> `write_text_atomic` -> `tempfile.mkstemp`
            # raises `FileNotFoundError` rather than anything handled here.
            # Inside `_save_lock` and not `_lock`, for the reason above:
            # holding the state lock across disk I/O blocks every mutation
            # for the length of a write.
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            save_project(self._project, path)
            if self.project_path is None:
                evict_oldest_files(
                    data_dir(UNSAVED_AUTOSAVE_DIR),
                    UNSAVED_AUTOSAVE_KEEP,
                    pattern="*.deckle.autosave",
                    on_error=lambda event, exc, failed: log_exception(
                        event, exc, path=failed
                    ),
                )

    def flush_autosave(self) -> None:
        """Cancel any pending debounce timer and save immediately.

        Used by tests (and app shutdown) to avoid waiting out the 500ms
        debounce window.

        :returns: nothing. A no-op when the project has never been saved
            and so has no autosave path.
        :raises OSError: the autosave file cannot be written.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._do_autosave()
