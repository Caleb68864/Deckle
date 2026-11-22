"""No two entries in one menu share an Alt-key mnemonic.

The File menu had three on P (Import &PDF, Save &PDF, Save one &pass as PDF)
and two on R (&Recent projects, P&rint). Qt does not refuse a clash; it
cycles focus between the entries on each press and activates neither, so
Alt+F, P printed nothing and saved nothing -- the keyboard route to Print
simply did not work.
"""

from __future__ import annotations

import re

import pytest

from deckle.app.menus import MENUS

_MNEMONIC = re.compile(r"&(?!&)(.)")


def mnemonic(label: str) -> str | None:
    """The letter after the first lone ``&``, lower-cased; ``&&`` is a literal."""
    match = _MNEMONIC.search(label)
    return match.group(1).lower() if match else None


@pytest.mark.parametrize("menu", MENUS, ids=[m.title.replace("&", "") for m in MENUS])
def test_no_two_entries_in_a_menu_share_a_mnemonic(menu):
    seen: dict[str, list[str]] = {}
    for item in menu.items:
        if item.is_separator:
            continue
        key = mnemonic(item.label)
        if key is not None:
            seen.setdefault(key, []).append(item.label)

    clashes = {key: labels for key, labels in seen.items() if len(labels) > 1}
    assert clashes == {}


def test_no_two_menus_on_the_bar_share_a_mnemonic():
    keys = [mnemonic(menu.title) for menu in MENUS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("menu", MENUS, ids=[m.title.replace("&", "") for m in MENUS])
def test_every_command_has_a_mnemonic(menu):
    """An entry without one cannot be reached by Alt-key at all."""
    missing = [i.label for i in menu.items if not i.is_separator and mnemonic(i.label) is None]
    assert missing == []


def test_print_is_on_p_as_every_other_program_has_it():
    file_menu = next(m for m in MENUS if m.title == "&File")
    print_entry = next(i for i in file_menu.items if i.action == "print_document")
    assert mnemonic(print_entry.label) == "p"
