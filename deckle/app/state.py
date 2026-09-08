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

import threading
from collections import deque
from dataclasses import replace
from typing import Callable

from deckle.core.models import BLANK_SOURCE_PATH, Project, SourcePage, SourceRef
from deckle.core.project_io import save_project

DEFAULT_UNDO_DEPTH = 50
DEFAULT_AUTOSAVE_DELAY_S = 0.5

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
    ``None`` (a brand-new, never-saved project) autosave is a no-op.

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
    :ivar autosave_path: ``f"{project_path}.autosave"``, or ``None`` --
        derived from ``project_path`` on every read, never cached.
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
        """Where this project's autosave goes, or ``None`` if it has never
        been saved.

        Derived on every read rather than cached, because ``project_path``
        changes underneath it: a session almost always starts with no path
        at all (``main()`` opens a blank project), and Save is what gives it
        one. A value computed once in ``__init__`` would still be ``None``
        after that Save, so the project the user has just named would go on
        autosaving nowhere -- which is precisely the session autosave exists
        to protect. One rule, one place; see :func:`autosave_path_for`.

        :returns: the autosave path, or ``None``.
        """
        return autosave_path_for(self.project_path)

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
            save_project(self._project, path)

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
