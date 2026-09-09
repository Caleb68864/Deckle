"""The menu bar, checked without a menu bar.

``deckle.app.menus`` writes the menus down as data precisely so the two
things that go wrong with a hand-built ``QMenuBar`` can be caught by
reading it: an entry wired to a method that does not exist, and two
entries claiming the same key.

Neither is loud at runtime. Qt does not refuse an ambiguous shortcut -- it
picks one of the two actions, or neither, silently -- and a dead menu entry
looks exactly like a working one until somebody in the middle of a job
clicks it.

Everything here is Qt-free: ``deckle.app.main`` imports PySide6 lazily, so
the ``MainWindow`` *class* can be inspected with no display and no
``QApplication``. The menu bar actually being built is
``tests/test_gui_shell.py``'s job.
"""

from __future__ import annotations

from deckle.app import menus
from deckle.app.main import MainWindow


def test_every_menu_entry_names_a_method_the_window_has():
    """A menu item is a promise that the window can do the thing."""
    missing = [
        name for name in menus.action_names() if not hasattr(MainWindow, name)
    ]
    assert not missing, (
        "these menu entries call methods MainWindow does not have: "
        f"{missing}"
    )


def test_every_submenu_names_an_attribute_the_window_builds():
    """Recent projects is grafted in by attribute name, not constructed.

    Read off the constructor's source rather than off a live window: a
    ``QMainWindow`` cannot be built under pytest on this machine (see
    ``tests/test_gui_shell.py``), and the failure this guards against is a
    renamed attribute, which the source shows as well as an instance would.
    """
    import inspect

    source = inspect.getsource(MainWindow.__init__)
    for name in menus.submenu_names():
        assert f"self.{name} =" in source, (
            f"the menu grafts in self.{name}, which the constructor never "
            "assigns"
        )


def test_no_two_entries_claim_the_same_shortcut():
    """Qt resolves an ambiguous shortcut by doing one of the two things,
    or nothing, and says which never."""
    conflicts = menus.shortcut_conflicts()
    assert not conflicts, f"these key sequences are claimed twice: {conflicts}"


def test_the_three_shortcuts_a_desktop_program_owes_its_user():
    """Open, save and print. Only undo and redo were bound before this."""
    bound = {
        shortcut: item.action
        for menu in menus.MENUS
        for item in menu.items
        for shortcut in item.shortcuts
    }
    assert bound.get("Ctrl+O") == "open_project_dialog"
    assert bound.get("Ctrl+S") == "save_project"
    assert bound.get("Ctrl+P") == "print_document"


def test_zoom_and_sheet_navigation_are_bound():
    """The two things a person does constantly while checking an imposition
    and could previously only do by aiming at a small button."""
    actions = {
        item.action
        for menu in menus.MENUS
        for item in menu.items
        if item.shortcuts
    }
    assert {"zoom_in", "zoom_out", "zoom_fit", "zoom_actual"} <= actions
    assert {"next_sheet", "previous_sheet", "first_sheet", "last_sheet"} <= actions


def test_the_grid_keys_are_scoped_to_the_grid():
    """A bare ``S`` bound window-wide competes with every text field in the
    settings column. R, S and Delete belong to the page list."""
    for menu in menus.MENUS:
        for item in menu.items:
            bare_letters = [s for s in item.shortcuts if len(s) == 1 or s == "Del"]
            if bare_letters:
                assert item.scope == "grid", (
                    f"{item.label!r} claims {bare_letters} window-wide; a "
                    "bare key must belong to a widget"
                )


def test_delete_and_s_are_the_same_command():
    """Deckle has no destructive page removal -- a skipped page keeps its
    slot, which is what lets it come back -- so Delete is spelled as the
    skip toggle rather than bound to a verb the program does not have."""
    skip = next(
        item
        for menu in menus.MENUS
        for item in menu.items
        if item.action == "skip_selection"
    )
    assert "Del" in skip.shortcuts
    assert "S" in skip.shortcuts


def test_recent_projects_is_a_submenu_not_a_command():
    """It is a rebuilt-from-the-store menu the bar borrows, not a second
    copy that would drift out of step with it."""
    recent = next(
        item
        for menu in menus.MENUS
        for item in menu.items
        if item.label.replace("&", "") == "Recent projects"
    )
    assert recent.submenu == "_recent_menu"
    assert not recent.action


def test_a_separator_is_not_mistaken_for_a_command():
    assert menus.SEPARATOR.is_separator
    assert menus.MenuItem("Quit", "quit").is_separator is False
    assert "" not in menus.action_names()
