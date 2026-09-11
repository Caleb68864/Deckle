"""Which menu entries go grey, and where that list is written down.

``_sync_document_actions`` gated four commands by naming them in a tuple
inside its own body::

    for name in ("save_pdf", "save_project", "save_project_as",
                 "export_single_pass"):
        action = self.menu_actions.get(name)
        if action is not None:
            action.setEnabled(has_pages)

and ``_sync_print_action`` named a fifth, ``print_document``, the same way.
So the set of commands that need a document lived in two places that had
to agree with a third, :data:`deckle.app.menus.MENUS`, and the
``.get(...) is not None`` pair turned any disagreement into silence: a
renamed action -- or a typo in either tuple -- left the command
permanently enabled with nothing failing anywhere.

That is the shape that produced B11, B12 and B29 in this repo: one list of
controls maintained in several places. ``build_menu_bar`` already fails
loudly on an entry naming a method the window does not have; the enable
rules, which must agree with the same data, failed quietly.

The gate now lives on the menu entry itself (``MenuItem.gate``), so it
travels with a rename and there is no second list to keep in step. These
tests call the real ``_sync_document_actions`` -- unbound, against a stub
window, the way ``test_hardening_printing.py`` already drives it -- so
nothing here is a re-implementation of the rule it is checking.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from deckle.app import menus
from deckle.app.main import MainWindow


# -- a window stand-in ---------------------------------------------------
#
# The subject is `_sync_document_actions` itself, called for real. Only its
# collaborators are stubbed, and each one is a single attribute. If the
# method grows a dependency this stub does not carry, it raises
# AttributeError and the test errors -- it cannot quietly pass.


class _Action:
    def __init__(self) -> None:
        self.enabled: bool | None = None

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt spelling
        self.enabled = bool(value)


class _Widget:
    def __init__(self) -> None:
        self.enabled: bool | None = None
        self.tooltip: str | None = None

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt spelling
        self.enabled = bool(value)

    def setToolTip(self, value: str) -> None:  # noqa: N802 - Qt spelling
        self.tooltip = value


class _LayoutPanel:
    def __init__(self) -> None:
        self.loaded: bool | None = None

    def set_document_loaded(self, value: bool) -> None:
        self.loaded = bool(value)


class _Project:
    def __init__(self, pages) -> None:
        self.pages = pages


class _State:
    def __init__(self, pages) -> None:
        self.project = _Project(pages)


class _Window:
    """Everything ``_sync_document_actions`` reaches for, and nothing else."""

    def __init__(self, menu_data, *, pages, printers) -> None:
        self.state = _State(pages)
        self._menus = menu_data
        self.menu_actions = {
            name: _Action() for name in menus.action_names(menu_data)
        }
        self.save_pdf_button = _Widget()
        self.save_project_button = _Widget()
        self.print_button = _Widget()
        self.layout_panel = _LayoutPanel()
        self._printers = list(printers)
        self._printer_message = ""

    # The real methods, borrowed rather than re-implemented: the gating
    # this file is about is spread across the three, and a stand-in for
    # any of them would be a replica of the subject.
    _sync_print_action = MainWindow._sync_print_action
    _set_gated_actions = MainWindow._set_gated_actions

    def sync(self) -> None:
        MainWindow._sync_document_actions(self)

    def enabled(self, name: str) -> bool | None:
        return self.menu_actions[name].enabled


def _synced(menu_data, *, pages, printers=("Fake Printer",)) -> _Window:
    window = _Window(menu_data, pages=pages, printers=printers)
    window.sync()
    return window


# -- the refusal machinery -----------------------------------------------


def test_the_stub_window_actually_reaches_the_gating_code():
    """The premise every assertion below rests on.

    A stub missing an attribute would raise; a stub the method never
    writes to would leave every action at ``None`` and make "disabled"
    indistinguishable from "not visited". Both are checked here so the
    rest of the file is reading a real answer.
    """
    window = _synced(menus.MENUS, pages=[])

    assert window.save_pdf_button.enabled is False
    assert window.layout_panel.loaded is False
    touched = [name for name, a in window.menu_actions.items() if a.enabled is not None]
    assert touched, "_sync_document_actions set no menu action at all"


def test_a_command_that_needs_nothing_is_left_alone():
    """The control that must NOT be disabled.

    Without it, a gate that greyed out the entire menu bar would satisfy
    every other assertion in this file.
    """
    window = _synced(menus.MENUS, pages=[])

    assert window.enabled("open_project_dialog") is not False
    assert window.enabled("import_pdf") is not False
    assert window.enabled("quit") is not False


# -- the gate, stated as data --------------------------------------------


def test_the_menus_declare_which_commands_need_a_document():
    declared = set(menus.actions_gated_by("document"))

    assert declared, "no menu entry is marked as needing a document"
    assert declared <= set(menus.action_names())
    assert {"save_pdf", "save_project", "save_project_as", "export_single_pass"} <= (
        declared | set(menus.actions_gated_by("document+printer"))
    )


def test_print_is_declared_as_needing_a_printer_too():
    assert menus.actions_gated_by("document+printer") == ["print_document"]


def test_an_unknown_gate_is_refused_rather_than_matching_nothing():
    """A typo in a caller's gate name must not read as "gate nothing".

    That is precisely how the old tuple failed: a name nobody recognised
    silently selected no action.
    """
    with pytest.raises(ValueError):
        menus.actions_gated_by("documnet")


def test_a_gate_name_the_menus_use_is_accepted():
    """The control for the guard above: a *refusing* check needs a case it
    lets through, or it could be refusing everything."""
    for gate in sorted(menus.GATES):
        menus.actions_gated_by(gate)


# -- the defect, behaviourally -------------------------------------------


def _renamed(old: str, new: str) -> tuple[menus.Menu, ...]:
    """``MENUS`` with one action renamed, everything else identical."""
    out = []
    for menu in menus.MENUS:
        items = tuple(
            replace(item, action=new) if item.action == old else item
            for item in menu.items
        )
        out.append(replace(menu, items=items))
    return tuple(out)


@pytest.mark.parametrize(
    "action", ["save_pdf", "save_project", "save_project_as", "export_single_pass"]
)
def test_a_renamed_command_keeps_its_enable_rule(action: str):
    """The regression, stated directly.

    Rename the command in the menu description and the gating must follow
    it. Against the hand-written tuple it did not: the old name was gated
    (and absent), the new name was never mentioned, and the renamed
    command stayed live on an empty document with the whole suite green.
    """
    renamed = _renamed(action, "renamed_command")
    assert "renamed_command" in menus.action_names(renamed)

    window = _synced(renamed, pages=[])

    assert window.enabled("renamed_command") is False, (
        f"{action!r} was renamed in menus.MENUS and lost its enable rule; "
        "the gating names it somewhere other than the menu description"
    )


def test_a_renamed_print_command_keeps_its_enable_rule():
    renamed = _renamed("print_document", "renamed_command")

    window = _synced(renamed, pages=[])

    assert window.enabled("renamed_command") is False


def test_the_gated_commands_come_back_with_a_document():
    """The other half, so "disable everything, always" would fail too."""
    window = _synced(menus.MENUS, pages=["a page"])

    for name in menus.actions_gated_by("document"):
        assert window.enabled(name) is True, name
    for name in menus.actions_gated_by("document+printer"):
        assert window.enabled(name) is True, name


def test_print_still_needs_a_printer_as_well_as_a_document():
    window = _synced(menus.MENUS, pages=["a page"], printers=())

    assert window.enabled("print_document") is False
    for name in menus.actions_gated_by("document"):
        assert window.enabled(name) is True, name


def test_a_window_with_no_menu_bar_still_syncs():
    """``tests/test_hardening_printing.py`` drives these methods on a fake
    window whose ``menu_actions`` is ``{}``. That has to keep working, and
    it is why the lookup tolerates a missing action at all."""
    window = _Window(menus.MENUS, pages=[], printers=["Fake Printer"])
    window.menu_actions = {}

    window.sync()

    assert window.save_pdf_button.enabled is False
