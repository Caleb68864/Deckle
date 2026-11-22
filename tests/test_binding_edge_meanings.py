"""F12 §D: ``binding_edge`` means two things, and both surfaces now say so.

The v2 design asked whether to split it into ``reading_direction`` -- a
``LayoutSettings`` change and the first ``.deckle`` migration. Decided by
agent 2026-11-21 (owner may revisit): **not split**. The field and the file
format stay as they are, so no saved project is rewritten. The confusion the
design worried about was in the words, not the model, so the words are
what changed: the app's tooltip said "this mirrors which side the gutter
falls on for every page", which under folio is false -- the fold is the
spine and the field sets reading direction -- and the CLI's help said only
"which edge the gutter shifts toward".
"""

from __future__ import annotations

import os

import pytest

from deckle.cli.options import build_parser


def _help_for(flag: str) -> str:
    parser = build_parser()
    export = parser._subparsers._group_actions[0].choices["export"]  # noqa: SLF001
    action = next(a for a in export._actions if flag in a.option_strings)  # noqa: SLF001
    return action.help


def test_the_cli_help_names_the_folio_meaning():
    text = _help_for("--binding-edge")

    assert "folio" in text
    assert "reading direction" in text
    assert "left to right" in text


def test_the_app_tooltip_no_longer_says_it_always_moves_the_gutter():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from deckle.app.views.layout_panel import CONTROLS

    tooltip = next(c for c in CONTROLS if c.name == "binding_edge_combo").tooltip

    assert "One page per side" in tooltip
    assert "Signatures (folio)" in tooltip
    assert "which way the book reads" in tooltip


def test_the_stored_field_is_unchanged():
    """No migration: the decision is to keep the file format."""
    from dataclasses import fields

    from deckle.core.models import LayoutSettings
    from deckle.core.project_io import FORMAT_VERSION

    names = {f.name for f in fields(LayoutSettings)}
    assert "binding_edge" in names and "reading_direction" not in names
    assert FORMAT_VERSION in (1, 2)
