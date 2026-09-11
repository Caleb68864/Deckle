"""`Project.printer` was written, persisted, round-tripped and read by nothing.

``deckle-cli impose book.pdf -o book.deckle --printer 'Brother HL-L2350DW'``
is documented as "printer name to record in the project". It was recorded:
``project_io`` writes it into the ``.deckle`` and ``load_project`` hands it
back. And then no code path in ``deckle/`` read it. Measured before the fix
by grepping every ``.printer`` site under ``deckle/``: the only two left
after excluding the serialiser were ``args.printer``, the CLI echoing back
what the user had just typed.

So opening that project and pressing Print preselected whichever printer
happened to have a saved calibration first -- a different machine, and with
it a different reload instruction and a different measured back offset, for
a printer the user had not chosen. The value the user supplied was stored
and ignored, which is the shape a persisted field is most able to hide.

The fix is a preference, not an override: a recorded printer that is not
installed on this machine is ignored, because a project made elsewhere
naming a queue this computer does not have is ordinary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from deckle.app.views.print_dialog import select_preselected_printer
from deckle.core.profiles import PrinterProfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _profile() -> PrinterProfile:
    return PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )


def _no_profile(name):
    raise FileNotFoundError(name)


def _only(calibrated):
    def loader(name):
        if name == calibrated:
            return _profile()
        raise FileNotFoundError(name)

    return loader


# -- the choosing rule ----------------------------------------------------


def test_the_printer_the_project_names_is_the_one_preselected():
    names = ["Some Other Printer", "Brother HL-L2350DW"]
    assert select_preselected_printer(
        names, _no_profile, "Brother HL-L2350DW"
    ) == "Brother HL-L2350DW"


def test_it_outranks_another_printers_calibration():
    """A saved profile says *this printer has been measured*. The project
    says *this book is for that printer*, which the user typed, about this
    document. The second is the more specific answer to the same question.
    """
    names = ["Calibrated One", "Recorded One"]
    assert select_preselected_printer(
        names, _only("Calibrated One"), "Recorded One"
    ) == "Recorded One"
    # ...and without the record, the calibration still wins, as before.
    assert select_preselected_printer(
        names, _only("Calibrated One"), None
    ) == "Calibrated One"


def test_a_recorded_printer_that_is_not_installed_is_ignored():
    """A project made on another machine is ordinary, not an error."""
    names = ["Calibrated One", "Another"]
    assert select_preselected_printer(
        names, _only("Calibrated One"), "A Printer On Someone Else's Desk"
    ) == "Calibrated One"


def test_no_printers_is_still_no_answer():
    assert select_preselected_printer([], _no_profile, "Recorded One") is None


def test_an_empty_recorded_name_is_not_a_choice():
    """`Project.printer` is `str | None`, and a `.deckle` written by hand
    can carry `""`. An empty string must not be looked up."""
    names = ["Calibrated One", "Another"]
    assert select_preselected_printer(
        names, _only("Calibrated One"), ""
    ) == "Calibrated One"


# -- and the preview draws against the same choice ------------------------


def test_the_preview_profile_follows_the_recorded_printer_too():
    """`profile_for_printers` and the dialog have to agree. A preview
    drawing one printer's border while the dialog is about to preselect
    another's is the lie B16 was about."""
    import deckle.app.main as app_main

    recorded = _profile()
    other = PrinterProfile(
        version=1,
        flip_axis="short",
        output_face="up",
        feed_edge="bottom",
        reverse_stack=False,
        imageable_area_pt=(36.0, 36.0, 36.0, 36.0),
        calibrated_at="2026-01-01T00:00:00",
        calibration_version=1,
    )

    def loader(name):
        if name == "Recorded One":
            return recorded
        if name == "Calibrated One":
            return other
        raise FileNotFoundError(name)

    names = ["Calibrated One", "Recorded One"]
    assert app_main.profile_for_printers(names, loader) == other
    assert app_main.profile_for_printers(
        names, loader, recorded_printer="Recorded One"
    ) == recorded


# -- the CLI end, run rather than read ------------------------------------


def test_the_cli_really_stores_the_name_and_the_project_gives_it_back(tmp_path):
    """The premise of everything above, asserted against the real CLI.

    Without this the rows above could all be green while ``--printer``
    silently stored nothing, and the preselection would be choosing from a
    value no user can produce.
    """
    source = tmp_path / "book.pdf"
    project = tmp_path / "book.deckle"
    name = "Brother HL-L2350DW"

    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    made = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "dummy", "-o", str(source),
         "--pages", "4"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=300,
    )
    assert made.returncode == 0, made.stderr

    imposed = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "impose", str(source),
         "-o", str(project), "--printer", name],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=300,
    )
    assert imposed.returncode == 0, imposed.stderr

    assert json.loads(project.read_text())["printer"] == name

    from deckle.core import project_io

    loaded = project_io.load_project(str(project), check_sources=False)
    assert loaded.printer == name

    # ...and that is the value the dialog now preselects on.
    assert select_preselected_printer(
        ["Some Other Printer", name], _no_profile, loaded.printer
    ) == name


@pytest.mark.parametrize("flag", ["--printer"])
def test_the_flag_is_still_spelled_the_way_this_file_assumes(flag):
    """`impose --printer` is what writes the field. If it is renamed, the
    round-trip test above would start proving something about a flag
    nobody has."""
    from deckle.cli import build_parser

    impose = build_parser()._actions  # noqa: SLF001
    flags = set()

    def walk(parser, depth=0):
        assert depth < 5
        for action in parser._actions:  # noqa: SLF001
            flags.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for sub in choices.values():
                    if hasattr(sub, "_actions"):
                        walk(sub, depth + 1)

    assert impose
    walk(build_parser())
    assert flag in flags
