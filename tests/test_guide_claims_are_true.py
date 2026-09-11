"""Claims in the GUIDE and README that are checked against the code.

The reload tables already have a guard of their own
(``test_reload_tables_are_true.py``) because they are the thing somebody
reads immediately before touching paper. This file does the same for four
more claims that were each found to be false, on three consecutive days:

- which flags survive being typed beside a ``.deckle``,
- which commands make ``-o`` optional,
- the name on the signature-suggestion button,
- and which warnings a worked example actually produces.

The pattern is worth stating, because it is what these two documents keep
doing wrong. **Every one of these was true when it was written.** The code
moved and the prose did not: ``--back-offset`` and ``--rule`` were added to
the honoured set, ``print`` gained an optional ``-o``, the button was
renamed, and the creep formula changed from ``sheets x caliper`` to
``(sheets - 1) x caliper``. Correcting the sentence fixes today; parsing it
and checking it against the code is what stops the next one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckle.cli.options import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPO_ROOT / "docs/GUIDE.md"
README = REPO_ROOT / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _subparsers() -> dict:
    parser = build_parser()
    found: dict = {}
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        found.update(getattr(action, "choices", {}) or {})
    assert found, "build_parser() exposed no subcommands"
    return found


# -- which flags are honoured beside a project -------------------------


def test_the_guide_lists_every_flag_honoured_beside_a_project():
    """``commands._layout_flags_given`` cites this sentence as its spec, in
    as many words, so an omission here propagates into the code's own
    reasoning about what it may ignore."""
    # Whitespace-tolerant: the sentence wraps, and where it wraps is a
    # formatting decision that must not be able to break this guard.
    text = re.sub(r"\s+", " ", _read(GUIDE))
    match = re.search(
        r"((?:`--[a-z-]+`(?:, | and )?)+) are not layout, and are honoured", text
    )
    assert match, "the GUIDE's honoured-flags sentence has moved or been reworded"
    listed = set(re.findall(r"`(--[a-z-]+)`", match.group(1)))

    export = _subparsers()["export"]
    layout_dests = export.get_default("_layout_dests")
    assert layout_dests, "export carries no _layout_dests to check against"

    honoured = {
        option
        for action in export._actions  # noqa: SLF001
        for option in action.option_strings
        if option.startswith("--")
        and action.dest not in layout_dests
        and action.dest not in {"help", "output", "json", "dry_run"}
    }
    # `--printer` lives on `impose`, not `export`, and is honoured there.
    honoured.add("--printer")

    assert listed == honoured, (
        f"the GUIDE lists {sorted(listed)}; the parser honours "
        f"{sorted(honoured)}"
    )


# -- which commands make -o optional -----------------------------------


def test_the_guide_names_every_command_whose_output_is_optional():
    text = _read(GUIDE)
    assert "Two commands make it\noptional" in text, (
        "the GUIDE's `-o` sentence has moved or been reworded"
    )

    optional = sorted(
        name
        for name, sub in _subparsers().items()
        for action in sub._actions  # noqa: SLF001
        if action.dest == "output" and not action.required
    )

    assert optional == ["print", "schedule"], (
        f"commands with an optional -o are now {optional}; the GUIDE says "
        "schedule and print"
    )
    for name in optional:
        assert f"`{name}`" in text.split("Two commands make it")[1][:400], (
            f"{name} is not named in the sentence that counts them"
        )


# -- the name on the button --------------------------------------------


def test_the_guide_calls_the_suggestion_button_what_it_says():
    """It was documented as **Apply** and reads *Use it*. Cosmetic, but it
    is a named control a reader will hunt the window for."""
    from deckle.app.views import layout_panel

    button = next(
        control
        for control in layout_panel.CONTROLS
        if control.name == "suggestion_button"
    )

    assert f"**{button.text}** button" in _read(GUIDE), (
        f"the GUIDE does not call the suggestion button {button.text!r}"
    )


# -- the worked example's warnings -------------------------------------


def test_the_guides_worked_example_shows_the_warnings_it_produces():
    """§5 step 5 quotes a sample warning block for a command it gives.

    It quoted a ``creep_advisory`` that does not fire (0.86pt of creep
    against a 1.0pt threshold), at a figure from the superseded
    ``sheets x caliper`` formula, with a remedy string no longer in the
    codebase -- and omitted the ``grain_direction`` warning the same
    command *does* emit, two paragraphs after telling the reader this is
    exactly the case that warning exists for.
    """
    from deckle.core.layout import SaddleStitchStrategy
    from deckle.core.models import LayoutSettings, SourcePage, SourceRef

    # The GUIDE's own command: 14 pages, folio, landscape letter,
    # --gutter 0.5in --sheets-per-signature 4 --grain long
    # --paper-thickness 0.004in --sewing-stations 3
    pages = [
        SourcePage(
            ref=SourceRef(
                path="x.pdf", page_index=i, sha256="0" * 64,
                width_pt=396.0, height_pt=612.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(14)
    ]
    settings = LayoutSettings(
        paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=4, grain="long",
        paper_thickness_pt=0.004 * 72.0, sewing_stations=3,
    )

    emitted = {w.kind for w in SaddleStitchStrategy().impose(pages, settings).warnings}

    block = _read(GUIDE).split("**5. Impose.**", 1)[1].split("**6. ", 1)[0]
    quoted = set(re.findall(r"\[([a-z_]+)\]", block))

    assert emitted <= quoted, (
        f"the worked example does not show {sorted(emitted - quoted)}, which "
        "its own command emits"
    )
    assert "creep_advisory" not in emitted, (
        "creep now fires for the GUIDE's example; the prose explaining why it "
        "stays quiet needs rewriting"
    )


def test_the_creep_figure_the_guide_quotes_is_the_one_the_code_computes():
    """The old sample said 1.15pt, which is ``4 x 0.288`` -- the formula
    before it became ``(sheets - 1) x caliper``. The GUIDE's own
    troubleshooting section had the new one, so the document disagreed
    with itself."""
    from deckle.core.paper import CREEP_INVISIBLE_PT, creep_pt

    caliper = 0.004 * 72.0
    text = _read(GUIDE)

    assert f"{creep_pt(4, caliper):.2f}pt" in text, (
        f"the GUIDE does not quote {creep_pt(4, caliper):.2f}pt, which is what "
        "(4 - 1) x 0.288 comes to"
    )
    assert creep_pt(4, caliper) < CREEP_INVISIBLE_PT


# -- the schedule button is not on a tab -------------------------------


def test_the_readme_does_not_put_the_schedule_on_the_signatures_tab():
    """It is built below the mode tabs, deliberately: selecting the
    Signatures tab *is* selecting folio, so a flat-sheet binder sent there
    either changes their imposition or finds nothing -- and what they miss
    is the AT THE PRINTER block, which ruins a job either way."""
    from deckle.app.views import layout_panel

    source = Path(layout_panel.__file__).read_text(encoding="utf-8")
    assert "outer.addWidget(self.save_schedule_button)" in source, (
        "the schedule button has moved; the README sentence needs rechecking"
    )

    text = _read(README)
    assert "**Save schedule** on the Signatures tab" not in text
    assert "below* the mode tabs" in text
