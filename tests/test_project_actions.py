"""The project actions, driven without a ``QMainWindow``.

``tests/test_ui_surface.py`` records why this file could not exist before:
a real ``QMainWindow`` under pytest hard-kills the interpreter here, so
opening, saving and the recent menu were reachable only through the
out-of-process probes. Now that they are free functions over a
window-shaped object, a five-line fake is enough.

The one that matters is ``refresh_recent_menu``. Its whole body sits
inside ``except Exception`` -- deliberately, because a convenience menu
that cannot be built must not be what stops the window opening -- which
means *any* mistake in it is silent. A ``NameError`` introduced while
moving the function out of ``MainWindow`` produced an empty Recent menu, a
line in the diagnostic log nobody reads, and a green suite.
"""

from __future__ import annotations

from deckle.app import project_actions


class _FakeAction:
    def __init__(self, label: str) -> None:
        self.label = label
        self.tooltip = ""
        self.triggered = _FakeSignal()

    def setToolTip(self, text: str) -> None:
        self.tooltip = text


class _FakeSignal:
    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, slot) -> None:
        self.slots.append(slot)


class _FakeMenu:
    def __init__(self) -> None:
        self.actions: list[_FakeAction] = []
        self.enabled: bool | None = None

    def clear(self) -> None:
        self.actions = []

    def addAction(self, label: str) -> _FakeAction:
        action = _FakeAction(label)
        self.actions.append(action)
        return action

    def setEnabled(self, value: bool) -> None:
        self.enabled = value


class _FakeWindow:
    def __init__(self) -> None:
        self._recent_menu = _FakeMenu()
        self.opened: list[str] = []

    def open_project_with_prompt(self, path: str) -> bool:
        self.opened.append(path)
        return True


def test_the_recent_menu_lists_what_the_store_holds(monkeypatch):
    """The failure this file exists for: an exception inside
    ``refresh_recent_menu`` is swallowed, so an empty menu is the only
    symptom."""
    paths = ["/books/traveller.deckle", "/jobs/a/book.deckle"]
    monkeypatch.setattr(project_actions.recent, "existing", lambda: paths)
    window = _FakeWindow()

    project_actions.refresh_recent_menu(window)

    assert [a.label for a in window._recent_menu.actions] == [
        project_actions.recent_label(path) for path in paths
    ]
    assert window._recent_menu.enabled is True
    # The full path is the tooltip because the label is deliberately not
    # one -- two projects called book.deckle are a normal thing to have.
    assert [a.tooltip for a in window._recent_menu.actions] == paths


def test_an_empty_store_leaves_the_recent_menu_disabled(monkeypatch):
    monkeypatch.setattr(project_actions.recent, "existing", lambda: [])
    window = _FakeWindow()

    project_actions.refresh_recent_menu(window)

    assert window._recent_menu.actions == []
    assert window._recent_menu.enabled is False


def test_choosing_a_recent_entry_opens_that_project_and_not_the_last_one(
    monkeypatch,
):
    """The classic closure bug: every lambda capturing the loop variable
    would open whichever project happened to be last."""
    paths = ["/one.deckle", "/two.deckle", "/three.deckle"]
    monkeypatch.setattr(project_actions.recent, "existing", lambda: paths)
    window = _FakeWindow()

    project_actions.refresh_recent_menu(window)
    for action in window._recent_menu.actions:
        action.triggered.slots[0]()

    assert window.opened == paths


def test_a_recent_label_names_the_file_then_its_folder():
    label = project_actions.recent_label("/books/1900s/traveller.deckle")

    assert "traveller.deckle" in label
    assert "/books/1900s" in label
