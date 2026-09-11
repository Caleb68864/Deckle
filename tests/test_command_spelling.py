"""The program must not tell anyone to run the GUI as if it were the CLI.

`pyproject.toml` installs two console scripts. `deckle` is the desktop app:
`deckle/__main__.py` answers `--help` and `--version` and hands everything
else to `QApplication`, which silently ignores what it does not recognise
and then enters an event loop. `deckle-cli` is the headless one.

So `deckle export --sheets 0` is not a command that fails. It opens a
window, prints nothing, and never returns -- which is exactly the 23-hour
hung process that turned up on the developer's own machine.

That sentence was printed on **every binding schedule**, the page a binder
takes to the printer:

    Print sheet 0 on its own first -- deckle export --sheets 0.

and by `deckle-cli profile list` when no printer has been calibrated. A
dozen more sat in docstrings, which `run.bat docs` publishes.

`tests/test_docs_are_current.py` already guards the CHANGELOG against this
exact mistake, for the reason recorded there: it "sends the reader to the
GUI entry point with a subcommand it has never parsed". Nothing guarded the
program's own output, which is the copy a user is most likely to act on.

The subcommand list is read off the real parser, so a new subcommand is
covered the day it is added rather than the day somebody remembers.
"""

from __future__ import annotations

import os
import re

import pytest

from deckle.cli import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "deckle")


def _subcommands() -> set[str]:
    names: set[str] = set()
    for action in build_parser()._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(str(name) for name in choices)
    assert names, "build_parser() exposed no subcommands; this test read nothing"
    assert "export" in names, (
        "the walker is no longer finding the subcommand table, so every "
        "verdict below is about an empty pattern"
    )
    return names


def _sources() -> list[str]:
    found = []
    for folder, _dirs, files in os.walk(PACKAGE):
        found.extend(
            os.path.join(folder, name) for name in files if name.endswith(".py")
        )
    assert found, "no sources found under deckle/"
    return sorted(found)


def test_nothing_in_the_package_spells_a_subcommand_after_bare_deckle():
    """`(?<!-)` is the whole subtlety: `deckle-cli export` must not match."""
    pattern = re.compile(r"(?<!-)\bdeckle (" + "|".join(sorted(_subcommands())) + r")\b")

    offenders = []
    for path in _sources():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if pattern.search(line):
                    offenders.append(
                        f"{os.path.relpath(path, ROOT)}:{number}: {line.strip()}"
                    )

    assert not offenders, (
        "these spell a CLI subcommand after the desktop app's console "
        "script, which ignores it and opens a window instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_pattern_would_catch_the_line_that_was_wrong():
    """A guard whose pattern silently stopped matching is worse than none.

    The exact string that shipped on every binding schedule, kept here so
    the check above cannot go quiet by accident.
    """
    pattern = re.compile(r"(?<!-)\bdeckle (" + "|".join(sorted(_subcommands())) + r")\b")

    assert pattern.search("  Print sheet 0 on its own first -- deckle export --sheets 0.")
    assert pattern.search("start from a built-in with `deckle profile set NAME`")
    # ...and does not fire on the spelling that is correct.
    assert not pattern.search("deckle-cli export --sheets 0")
    assert not pattern.search("python -m deckle.cli export")
    # ...nor on the GUI's own name used as the GUI.
    assert not pattern.search("run `deckle` to open the desktop app")


@pytest.mark.parametrize(
    "text",
    [
        "  Print sheet 0 on its own first -- deckle-cli export --sheets 0.",
    ],
)
def test_the_schedule_still_says_how_to_proof_a_sheet(text):
    """It is worth proofing one sheet before committing a stack, and the
    schedule is where that advice belongs. Fixing the spelling must not
    have deleted the sentence."""
    from deckle.core.schedule import build_schedule, format_schedule_text
    from deckle.core.models import LayoutSettings, OutputPage, Placement, Sheet, \
        SheetPlan, Side

    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    plan = SheetPlan(
        sheets=[Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
                for i in range(4)],
        paper_pt=(792.0, 612.0),
        warnings=[],
        signatures=(),
    )
    settings = LayoutSettings(
        paper=(792.0, 612.0), gutter_pt=0.0, binding_edge="left",
        fold_scheme="folio", sheets_per_signature=4,
    )
    rendered = format_schedule_text(build_schedule(plan, settings))
    assert text in rendered, rendered
