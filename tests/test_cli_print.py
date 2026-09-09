"""``deckle print``: the manual-duplex run, planned headlessly.

N14. The roadmap's note is that the session and pass planner are Qt-free
and only the backend is Qt, and that is exactly right -- which is why this
command plans a run and does not submit one.

``PrintBackend`` (``deckle.core.printing``) is a ``Protocol`` with one
implementation, ``QtPrintBackend`` in ``deckle.app.backend``. Reaching it
from the CLI would make ``deckle.cli`` import ``deckle.app`` -- inverting
the dependency the CLI exists to keep, and breaking the promise
``tests/test_core_purity.py`` enforces, that the CLI runs on a machine
with no display libraries at all. There is a test below that would catch
that if anyone tried.

What *is* reachable headlessly is everything up to the spooler, and that
is what is tested here: the pass order, the half turn, the reload
instruction, and one PDF per pass with the profile's registration
correction applied. The command's own last line says nothing was
submitted, and the JSON says so in a field, because "did this reach a
printer?" is not something a caller should have to infer.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

import pikepdf
import pytest

from deckle.cli import main
from deckle.core.printing import plan_passes
from deckle.core.profiles import BUILTIN_PRESETS

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
FOLIO = ["--fold-scheme", "folio", "--landscape"]


# -- it plans, and it is honest that it only plans ------------------------


def test_print_reports_both_passes_and_their_reload_instructions(capsys):
    assert main(
        ["print", FIXTURE, "--profile", "generic_face_down_reversed"]
    ) == 0
    printed = capsys.readouterr().out

    assert "pass 1 of 2 -- fronts" in printed
    assert "pass 2 of 2 -- backs" in printed
    assert "Load paper face down, feed edge top" in printed
    assert "reverse the printed stack" in printed


def test_print_says_out_loud_that_it_sent_nothing_to_a_printer(capsys):
    """A command called ``print`` that does not print must say so.

    The alternative is a user who believes a job is spooling and finds out
    otherwise by walking to a printer with nothing in it.
    """
    assert main(
        ["print", FIXTURE, "--profile", "generic_face_up_in_order"]
    ) == 0

    assert "sent nothing to a printer" in capsys.readouterr().out


def test_print_json_carries_the_same_pass_plan_the_planner_produced(capsys):
    """The CLI must not own a second copy of the ordering table.

    One free to disagree with the desktop app about the same printer would
    make the paper wrong while both halves looked right -- so this asserts
    the reported orders are ``plan_passes``'s, not merely plausible.
    """
    assert main(
        [
            "print", FIXTURE, *FOLIO,
            "--profile", "generic_face_down_reversed", "--json",
        ]
    ) == 0
    document = json.loads(capsys.readouterr().out)

    from deckle.cli import _build_layout_settings, _load_source, _strategy_for, build_parser

    args = build_parser().parse_args(
        ["export", FIXTURE, "-o", "unused.pdf", *FOLIO]
    )
    plan = _strategy_for(_build_layout_settings(args)).impose(
        _load_source(FIXTURE), _build_layout_settings(args)
    )
    expected = plan_passes(plan, BUILTIN_PRESETS["generic_face_down_reversed"])

    assert [p["sheet_order"] for p in document["passes"]] == [
        list(p.sheet_order) for p in expected
    ]
    assert [p["reload_instruction"] for p in document["passes"]] == [
        p.reload_instruction for p in expected
    ]
    assert [p["rotate_backs"] for p in document["passes"]] == [
        p.rotate_backs for p in expected
    ]


def test_a_face_up_printer_gets_a_different_back_order_than_a_face_down_one(capsys):
    """The one decision that ruins a stack, and it comes from the profile.

    A face-down printer whose stack is reversed on reload needs the backs
    as-is; a face-up one that retains order needs them reversed. If the
    command ignored the profile both would report the same order and one
    of them would be wrong.
    """
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "generic_face_down_reversed",
         "--json"]
    ) == 0
    reversed_stack = json.loads(capsys.readouterr().out)
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "generic_face_up_in_order",
         "--json"]
    ) == 0
    in_order = json.loads(capsys.readouterr().out)

    fronts = reversed_stack["passes"][0]["sheet_order"]
    assert reversed_stack["passes"][1]["sheet_order"] == list(reversed(fronts))
    assert in_order["passes"][1]["sheet_order"] == fronts
    # And the half turn follows flip_axis, not the stack order.
    assert reversed_stack["passes"][1]["rotate_backs"] is True
    assert in_order["passes"][1]["rotate_backs"] is False


def test_print_requires_a_profile(capsys):
    """Same rule as ``export --pass``, for the same reason.

    Neither the sheet order nor the half turn has a safe default, and
    guessing wrong prints every back onto the wrong front.
    """
    with pytest.raises(SystemExit) as exit_info:
        main(["print", FIXTURE])
    assert exit_info.value.code == 2
    assert "--profile" in capsys.readouterr().err


def test_print_refuses_an_unknown_profile(capsys):
    assert main(["print", FIXTURE, "--profile", "nope"]) == 1
    assert "no printer profile 'nope'" in capsys.readouterr().err


# -- with -o, it writes one PDF per pass ----------------------------------


def test_print_writes_one_readable_pdf_per_pass(tmp_path, capsys):
    """``job.pdf`` becomes ``job.front.pdf`` and ``job.back.pdf``.

    The side goes in the filename because the two files are handled
    minutes apart by a person standing at a printer, and "which of these
    is the backs" has to be answerable without opening them.
    """
    out = tmp_path / "job.pdf"
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "generic_face_down_reversed",
         "-o", str(out)]
    ) == 0

    front = tmp_path / "job.front.pdf"
    back = tmp_path / "job.back.pdf"
    assert front.exists() and back.exists()
    assert not out.exists(), "the base name is a template, not a destination"
    for path in (front, back):
        with pikepdf.open(path) as pdf:
            assert len(pdf.pages) > 0


def test_print_writes_nothing_without_an_output(tmp_path, capsys):
    """No ``-o`` is the plan-only mode, and it must leave the disk alone."""
    assert main(
        ["print", FIXTURE, "--profile", "generic_face_down_reversed"]
    ) == 0
    assert list(tmp_path.iterdir()) == []


def test_print_checks_both_destinations_before_writing_either(tmp_path, capsys):
    """Half a job in a directory is worse than none.

    The operator cannot tell from the listing which half is there, and the
    natural next step -- run it again -- reprints the half that succeeded.
    """
    out = tmp_path / "missing-folder" / "job.pdf"

    assert main(
        ["print", FIXTURE, "--profile", "generic_face_down_reversed",
         "-o", str(out)]
    ) == 1

    assert not (tmp_path / "missing-folder").exists()
    assert "error:" in capsys.readouterr().err


def test_the_written_passes_carry_the_profiles_registration_correction(
    tmp_path, monkeypatch, capsys
):
    """The offset is why a saved profile is worth having at all.

    It cannot be seen in the output -- a shifted back looks exactly like an
    unshifted one until it is printed and held against its own front -- so
    a silent failure to apply it would survive every visual check.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert main(
        ["profile", "set", "Office", "--from", "generic_face_down_reversed",
         "--back-offset", "9,0"]
    ) == 0
    capsys.readouterr()

    plain = tmp_path / "plain.pdf"
    shifted = tmp_path / "shifted.pdf"
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "generic_face_down_reversed",
         "-o", str(plain)]
    ) == 0
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "Office", "-o", str(shifted)]
    ) == 0
    printed = capsys.readouterr().out

    assert "back faces moved +9, +0pt" in printed
    # The fronts are untouched by a back offset; the backs are not.
    assert (tmp_path / "plain.front.pdf").read_bytes() != b""
    assert (tmp_path / "shifted.back.pdf").read_bytes() != (
        (tmp_path / "plain.back.pdf").read_bytes()
    )


def test_print_json_names_the_file_written_for_each_pass(tmp_path, capsys):
    out = tmp_path / "job.pdf"
    assert main(
        ["print", FIXTURE, "--profile", "generic_face_up_in_order",
         "-o", str(out), "--json"]
    ) == 0
    document = json.loads(capsys.readouterr().out)

    assert [p["output"] for p in document["passes"]] == [
        str(tmp_path / "job.front.pdf"), str(tmp_path / "job.back.pdf")
    ]


def test_print_narrows_to_the_sheets_asked_for(tmp_path, capsys):
    assert main(
        ["print", FIXTURE, *FOLIO, "--profile", "generic_face_up_in_order",
         "--sheets", "0", "--json"]
    ) == 0
    document = json.loads(capsys.readouterr().out)

    assert [p["sheet_order"] for p in document["passes"]] == [[0], [0]]


def test_print_refuses_a_sheet_the_document_does_not_have(tmp_path, capsys):
    assert main(
        ["print", FIXTURE, "--profile", "generic_face_up_in_order",
         "--sheets", "99"]
    ) == 1
    assert "no sheet 99" in capsys.readouterr().err


# -- the seam this command deliberately does not cross --------------------


def test_the_print_command_did_not_drag_qt_into_the_cli():
    """The reason ``print`` plans instead of submitting, asserted.

    ``tests/test_core_purity.py`` and ``test_cli.py`` already check that
    importing ``deckle.cli`` loads no Qt. Neither would catch a *lazy*
    import inside the print command, which is exactly the shortcut a
    submitting ``deckle print`` would reach for -- it would keep the
    import-time promise and break the real one, that the CLI runs where
    there is no display at all.
    """
    cli_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "deckle", "cli.py",
    )
    with open(cli_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=cli_path)

    forbidden = ("deckle.app", "PySide6", "PyQt5", "PyQt6")
    modules = []
    for node in ast.walk(tree):  # every import, nested ones included
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    assert not [name for name in modules if name.startswith(forbidden)]


def test_print_runs_with_no_display(tmp_path):
    """The acceptance condition: a real subprocess, DISPLAY unset."""
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["XDG_CONFIG_HOME"] = str(tmp_path)

    result = subprocess.run(
        [
            sys.executable, "-m", "deckle.cli", "print", FIXTURE,
            "--profile", "generic_face_down_reversed",
            "-o", str(tmp_path / "job.pdf"),
        ],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "job.front.pdf").exists()
    assert (tmp_path / "job.back.pdf").exists()
