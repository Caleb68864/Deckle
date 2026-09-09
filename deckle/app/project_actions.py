"""Opening and saving the job itself: the ``.deckle`` file and its recovery.

Free functions over a window-shaped object rather than methods on
``MainWindow``, because that is what they already were. Each reads a
handful of the window's attributes -- ``state``, ``status_bar``, the three
views -- and writes the status bar; none of them touches a splitter, a
tooltip or a menu action. The tests had worked this out first: they build a
five-line fake window and bind an unbound method to it.

The window keeps a thin method for every one of these, because a menu entry
(:data:`deckle.app.menus.MENUS` names ``MainWindow`` methods by string), a
button's ``clicked`` signal or a test reaches each by name -- and because
the functions here call back through ``window.<method>()`` rather than each
other, so overriding one on the window still wins.

The **modal prompts stay on the window**: ``confirm_recovery``,
``confirm_unsaved_recovery`` and ``confirm_discard_changes`` are injected
attributes, and their defaults raise a ``QMessageBox`` parented to
``window.window``. What lives here is the decision that consumes the answer.

The two ways a project outlives its sources are reported differently on
purpose. A missing file is obvious once named. A source that still exists
but has *changed* is the dangerous one: nothing looks wrong, and imposing
it would use content the user has never reviewed.

PySide6 is imported inside the two functions that open a file dialog, so
importing this module needs neither Qt nor a display.
"""

from __future__ import annotations

import os
import warnings

from deckle.app.state import (
    AppState,
    autosave_recovery_offer,
    unsaved_autosave_offers,
)
from deckle.core import recent
from deckle.core.diagnostics import log_event, log_exception
from deckle.core.export import clear_sheet_cache
from deckle.core.models import Project
from deckle.core.outputs import describe_write_failure, output_path_problem
from deckle.core.project_io import (
    PathOutsideRootsAdvisory,
    SourceChangedWarning,
    SourceMissingError,
    load_project,
)
# Aliased: this module has its own `save_project`, which is the window's
# Save command rather than the serialiser it eventually calls.
from deckle.core.project_io import save_project as save_project_file

NOTHING_TO_SAVE_MESSAGE = "Nothing to save yet -- import a PDF or images first."

UNTITLED_PROJECT_NAME = "This document"
"""What the prompts call a project that has never been saved."""


def recent_label(path: str) -> str:
    """A menu label for a recent project: its name, then its folder.

    Two projects called ``book.deckle`` in different folders are a normal
    thing to have, and a list showing the same word twice would be worse
    than no list.
    """
    return f"{os.path.basename(path)}  --  {os.path.dirname(path)}"


def refresh_recent_menu(window) -> None:
    """Rebuild the Recent projects menu from the store.

    Rebuilt rather than appended to, so an entry cannot appear twice
    after a project is reopened and so a file that has since gone
    drops out without any bookkeeping to keep in step.

    :returns: nothing, and never raises. A convenience menu that
        could not be built must not be what stops the window opening.
    """
    menu = getattr(window, "_recent_menu", None)
    if menu is None:
        return
    try:
        menu.clear()
        paths = recent.existing()
        for path in paths:
            action = menu.addAction(recent_label(path))
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, target=path: window.open_project_with_prompt(
                    target
                )
            )
        menu.setEnabled(bool(paths))
    except Exception as exc:  # noqa: BLE001 -- convenience, never fatal
        log_exception("recent_menu_refresh_failed", exc)


def confirm_discard(window, action: str) -> bool:
    """Ask about unsaved work, and act on the answer.

    :param action: what is about to happen, as a phrase that completes
        "Save them before ...?" -- ``"closing"``, ``"opening another
        project"``.
    :returns: whether to go ahead. ``False`` means the user cancelled,
        or asked to save and the save did not happen -- a failed save
        must not be followed by the discard it was meant to prevent.
    """
    if not window.has_unsaved_changes():
        return True
    path = window.state.project_path
    name = os.path.basename(path) if path else UNTITLED_PROJECT_NAME
    answer = window.confirm_discard_changes(name, action)
    if answer == "cancel":
        return False
    if answer == "discard":
        log_event("unsaved_changes_discarded", pages=len(window.state.project.pages))
        return True
    return window.save_project()


def recover_autosave_if_offered(window, path: str, project: Project) -> Project:
    """Offer a newer autosave in place of the project just loaded.

    Autosave was written on every edit and flushed on close and never
    offered back. This is where it stops being a corpse.

    Declining **deletes** the autosave. Leaving it would bring the
    prompt back on every subsequent open, which trains someone to
    dismiss it -- and the one that matters is the one they then
    dismiss without reading.

    A failure to load the autosave leaves the project as it was: the
    recovery file is the damaged one by definition here, so falling
    back to the saved project is the safe direction.

    :param path: the project file just opened.
    :param project: what was loaded from it.
    :returns: the recovered project, or ``project`` unchanged.
    """
    autosave_path = autosave_recovery_offer(path)
    if autosave_path is None:
        return project
    if not window.confirm_recovery(os.path.basename(path)):
        try:
            os.remove(autosave_path)
        except OSError as exc:  # noqa: BLE001 -- declined, never fatal
            log_exception("autosave_discard_failed", exc, path=autosave_path)
        return project
    try:
        recovered = load_project(
            autosave_path, allowed_roots=(os.path.dirname(path),)
        )
    except Exception as exc:  # noqa: BLE001 -- reported, never a crash
        window.status_bar.showMessage(
            f"Could not read the recovered changes for "
            f"{os.path.basename(path)}: {exc}"
        )
        log_exception("autosave_recovery_failed", exc, path=autosave_path)
        return project
    log_event("autosave_recovered", path=path, pages=len(recovered.pages))
    return recovered


def offer_unsaved_recovery(window) -> None:
    """Offer back work from a session that never got as far as Save.

    :returns: nothing, and never raises. A recovery store that cannot
        be read must not be what stops a window opening.

    **Declining deletes nothing.** :func:`recover_autosave_if_offered`
    deletes on decline because the work also exists in the user's own
    ``.deckle``; here it does not -- this file is the only copy -- so a
    mis-click must not be destructive. ``UNSAVED_AUTOSAVE_MAX_AGE_S``
    and ``UNSAVED_AUTOSAVE_KEEP`` are what stop the list growing
    instead.
    """
    offers = unsaved_autosave_offers()
    if not offers:
        return
    chosen = window.confirm_unsaved_recovery(offers)
    if chosen is None:
        return
    try:
        # `check_sources=False` for the same reason
        # `unsaved_autosave_offers` reads the JSON directly: a source
        # that has moved raises `SourceMissingError`, and a moved
        # source is exactly when this recovery matters most. There is
        # no other copy of the arrangement to fall back on, so
        # refusing to load it would be refusing the whole feature at
        # the moment it is needed. Thumbnails degrade to placeholders
        # and the import view can point at the file again.
        #
        # `on_outside_roots` returns True rather than passing
        # `allowed_roots`: the sources of an unsaved project are
        # wherever the user imported from, and there is no project
        # directory to reason from.
        project = load_project(
            chosen.path,
            check_sources=False,
            on_outside_roots=lambda _path, _roots: True,
        )
    except Exception as exc:  # noqa: BLE001 -- a window is opening
        window.status_bar.showMessage(f"Could not read the recovered work: {exc}")
        log_exception(
            "unsaved_autosave_recovery_failed", exc, path=chosen.path
        )
        return
    clear_sheet_cache()
    # `project_path=None` deliberately: the work is still unsaved, and
    # the new `AppState` re-derives the same key from the same sources,
    # so continuing to edit keeps writing to the same file.
    window.state = AppState(project, project_path=None)
    window.import_view.state = window.state
    window.arrange_view.state = window.state
    window.layout_panel.state = window.state
    window.arrange_view.refresh()
    window.layout_panel.refresh_from_project()
    window._on_pages_changed()
    window.status_bar.showMessage(
        f"Recovered {len(project.pages)} unsaved page(s)."
    )
    log_event(
        "unsaved_autosave_recovered",
        path=chosen.path,
        pages=len(project.pages),
    )


def choose_and_open_project(window) -> None:
    """Open a saved project, replacing whatever is loaded.

    :returns: nothing. Every failure is reported in the status bar; a
        project that cannot be opened leaves the current one alone
        rather than half-replacing it.
    """
    from PySide6.QtWidgets import QFileDialog

    # Start where the last project came from. An empty string here
    # meant every open began wherever the OS thought best, which is
    # rarely the folder holding the job you are working on.
    path, _ = QFileDialog.getOpenFileName(
        window.window,
        "Open project",
        recent.last_directory(),
        "Deckle projects (*.deckle)",
    )
    if not path:
        return
    window.open_project_with_prompt(path)


def open_project_with_prompt(window, path: str) -> bool:
    """Open ``path``, offering to save the document it replaces.

    :param path: the ``.deckle`` to open.
    :returns: whether it opened.
    """
    if not window._confirm_discard("opening another project"):
        return False
    return window.open_project(path)


def open_project(window, path: str) -> bool:
    """Load ``path`` into the window.

    :param path: the ``.deckle`` to open.
    :returns: whether it opened.

    Separated from the dialog so the whole flow is drivable without a
    modal -- the same seam ``PrintDialog`` uses.

    The two ways a project outlives its sources are reported
    differently on purpose. A missing file is obvious once named. A
    source that still exists but has CHANGED is the dangerous one:
    nothing looks wrong, and imposing it would use content the user has
    never reviewed.
    """
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Sources normally live somewhere other than the project --
            # Downloads, a scanner folder. Without swallowing the
            # advisory, Python prints a bare warning naming a line
            # inside Deckle, which tells the user nothing they can act
            # on. The containment check still runs; only its rendering
            # changes.
            project = load_project(path, allowed_roots=(os.path.dirname(path),))
        for warning in caught:
            if issubclass(warning.category, PathOutsideRootsAdvisory):
                log_event("project_source_outside_roots", path=path,
                          detail=str(warning.message))
    except SourceMissingError as exc:
        window.status_bar.showMessage(
            f"Cannot open {os.path.basename(path)}: a source file is "
            f"missing -- {exc.expected_path}."
        )
        log_exception("project_source_missing", exc, path=path)
        return False
    except SourceChangedWarning as exc:
        window.status_bar.showMessage(
            f"Cannot open {os.path.basename(path)}: {os.path.basename(exc.path)} "
            "has changed since the project was saved. Re-import it to "
            "accept the new version."
        )
        log_exception("project_source_changed", exc, path=path)
        return False
    except Exception as exc:  # noqa: BLE001 -- reported, never a crash
        window.status_bar.showMessage(f"Cannot open {os.path.basename(path)}: {exc}")
        log_exception("project_open_failed", exc, path=path)
        return False

    loaded = project
    project = window._recover_autosave_if_offered(path, project)
    recovered = project is not loaded

    recent.record(path)
    window._refresh_recent_menu()
    # The outgoing project's cached sheet renders are unreachable the
    # moment the plan changes -- their key is the plan hash -- so they
    # are dead weight in temp until something removes them. Removed
    # here rather than only at exit, because scrubbing one long
    # document and then opening another is an ordinary session.
    clear_sheet_cache()
    # The outgoing AppState is about to become unreachable while it is
    # still holding up to `autosave_delay_s` of edits behind a debounce
    # timer, and dropping the reference does not cancel that timer.
    # Both halves of that are bugs. The user loses the last half-second
    # of work on the project they are leaving -- the interval autosave
    # exists to protect -- and the orphaned daemon timer then fires
    # against the *old* project and writes it to the old project's
    # autosave, minutes after the user moved on, so the next open of
    # that project offers back a file whose mtime says "you crashed".
    #
    # Flushing does both jobs at once: it cancels the timer and writes
    # what the timer was holding. Same call `close()` makes, for the
    # same reason -- swapping the project out is a close as far as the
    # outgoing state is concerned.
    #
    # After `recover_autosave_if_offered`, not before: the offer is
    # decided on the autosave's mtime, and flushing first would make
    # reopening the currently-loaded project always look like a crash.
    window._flush_outgoing_state()
    window.state = AppState(project, project_path=path)
    window.import_view.state = window.state
    window.arrange_view.state = window.state
    window.layout_panel.state = window.state
    window.arrange_view.refresh()
    window.layout_panel.refresh_from_project()
    window._on_pages_changed()
    # A project just read off disk is not modified. A recovered
    # autosave is: that work exists nowhere but in memory until the
    # user saves it, which is exactly what the marker is for -- and
    # without this, accepting a recovery and closing the window would
    # throw it away a second time.
    if recovered:
        window.state.mark_unsaved()
    window._sync_title()
    window.status_bar.showMessage(
        f"Opened {os.path.basename(path)} -- {len(project.pages)} page(s)."
    )
    log_event("project_opened", path=path, pages=len(project.pages))
    return True


def flush_outgoing_state(window) -> None:
    """Write and disarm the ``AppState`` that is about to be replaced.

    :returns: nothing, and never raises. An autosave that cannot be
        written must not be what stops the user opening another
        project -- they asked for the new project, and refusing it
        would lose the new work as well as the old.
    """
    try:
        window.state.flush_autosave()
    except OSError as exc:
        log_exception(
            "autosave_flush_failed", exc, path=window.state.autosave_path
        )


def save_project(window) -> bool:
    """Save the job back to the file it came from.

    The Ctrl+S a person's hands already know: a project that has a
    file writes to it without a dialog. One that has never been saved
    has nowhere to go, so it falls through to
    :meth:`save_project_as`.

    :returns: whether the project was written. ``False`` covers a
        cancelled dialog and a destination that could not be written,
        because both leave the work unsaved -- and this answer is what
        the close prompt uses to decide whether it may proceed.
    """
    if not window.state.project.pages:
        window.status_bar.showMessage(NOTHING_TO_SAVE_MESSAGE)
        return False
    if window.state.project_path is None:
        return window.save_project_as()
    return write_project(window, window.state.project_path)


def save_project_as(window) -> bool:
    """Ask where to save the job, and save it there.

    :returns: whether the project was written; ``False`` for a
        cancelled dialog.
    """
    from PySide6.QtWidgets import QFileDialog

    if not window.state.project.pages:
        window.status_bar.showMessage(NOTHING_TO_SAVE_MESSAGE)
        return False

    source = window.state.project.pages[0].ref.path
    suggested = window.state.project_path or os.path.join(
        os.path.dirname(source) or os.getcwd(),
        os.path.splitext(os.path.basename(source))[0] + ".deckle",
    )
    path, _ = QFileDialog.getSaveFileName(
        window.window, "Save project", suggested, "Deckle projects (*.deckle)"
    )
    if not path:
        return False
    if not path.lower().endswith(".deckle"):
        path += ".deckle"
    return write_project(window, path)


def write_project(window, path: str) -> bool:
    """Write the project to ``path`` and record that it is saved.

    :param path: where to write.
    :returns: whether it was written. Uses the same wording as the CLI
        for a destination it cannot write -- see
        :mod:`deckle.core.outputs`.
    """
    problem = output_path_problem(path)
    if problem is not None:
        window.status_bar.showMessage(problem)
        log_event("project_path_rejected", path=path, detail=problem)
        return False

    try:
        save_project_file(window.state.project, path)
    except OSError as exc:
        window.status_bar.showMessage(describe_write_failure(path, exc))
        log_exception("project_write_failed", exc, path=path)
        return False
    window.state.project_path = path
    # The work now exists in a file the user named, so the never-saved
    # copy under `data_dir("autosave")` would only be an offer to
    # recover something they already have. Re-derives the key from the
    # current pages, which the save did not change.
    window.state.discard_unsaved_autosave()
    # This, and only this, is what clears the modified marker. An
    # autosave does not: it writes `<project>.autosave` precisely so it
    # never touches the file the user named, and treating it as a save
    # would stop the window saying "unsaved" while the user's own file
    # was still stale.
    window.state.mark_saved()
    # Saving is how a project first comes into existence, so it
    # belongs in the list as much as opening one does.
    recent.record(path)
    window._refresh_recent_menu()
    window._sync_title()
    window.status_bar.showMessage(f"Saved project to {path}")
    log_event("project_saved", path=path, pages=len(window.state.project.pages))
    return True
