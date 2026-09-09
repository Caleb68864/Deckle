"""The ``--json`` documents are a contract, and this is what pins it.

N14. The CLI's stated purpose is scripting, and before ``--json`` existed
that meant regex-scraping prose -- ``page count: 12``, ``  [kind] sheet 0:
detail``. Prose is allowed to be reworded, and when it is, a script that
greps it fails *silently*: the pattern stops matching and the result reads
as "this document has no warnings" rather than as "your script is broken".

Which means the value of the flag is not that it emits JSON. It is that
the key names do not move. So the key set at every level of every
document is written out below as a literal and compared with ``==``, and
the test fails if a key is **renamed** (one appears, one vanishes),
**dropped** (one vanishes), or **added** without being recorded here.

The last of those is deliberate even though ``REPORT_VERSION``'s own
policy allows additions without a bump. A reader that indexes the keys it
knows genuinely cannot be broken by a new sibling -- but a key that
appears with nobody noticing is how a shape drifts into something nobody
designed, and adding a line to this file is the cost of one line of
attention. The version rule and this test answer two different questions:
"may a consumer be broken?" and "did a human agree to this shape?"
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from deckle import __version__ as DECKLE_VERSION
from deckle.cli import main
from deckle.core.report import REPORT_VERSION

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")

# -- the contract ---------------------------------------------------------
#
# Every document shares this envelope; each shape adds its own keys.
ENVELOPE = {"report", "report_version", "deckle_version"}

SIZE_KEYS = {"width_pt", "height_pt"}
PAGE_SIZE_KEYS = {"width_pt", "height_pt", "count"}
WARNING_KEYS = {"kind", "sheet_index", "detail"}
PROFILE_ENTRY_KEYS = {"name", "origin", "path", "error", "profile"}
PROFILE_FIELD_KEYS = {
    "version",
    "flip_axis",
    "output_face",
    "feed_edge",
    "reverse_stack",
    "imageable_area_pt",
    "calibrated_at",
    "calibration_version",
    "back_offset_x_pt",
    "back_offset_y_pt",
}

INFO_KEYS = ENVELOPE | {
    "source",
    "source_kind",
    "page_count",
    "skipped_page_count",
    "page_sizes_pt",
    "paper_pt",
    "sheet_count",
    "signature_count",
    "blank_count",
    "warnings",
}

SCHEDULE_KEYS = ENVELOPE | {
    "source",
    "fold_scheme",
    "sheets_total",
    "blank_total",
    "signature_count",
    "sewing_stations",
    "sewing_margin_pt",
    "paper_thickness_pt",
    "spine_width_pt",
    "duplex_flip_edge",
    "notes",
    "signatures",
}
SIGNATURE_KEYS = {"index", "sheet_count", "page_count", "blank_count", "sheets"}
SHEET_KEYS = {
    "sheet_index", "position", "is_outermost", "front_pages", "back_pages",
}
SPINE_KEYS = {"low_pt", "high_pt"}

PROFILE_KEYS = ENVELOPE | PROFILE_ENTRY_KEYS
PROFILE_LIST_KEYS = ENVELOPE | {"profiles"}

PRINT_PLAN_KEYS = ENVELOPE | {
    "source",
    "submitted",
    "printer_profile",
    "paper_pt",
    "sheet_count",
    "grain",
    "passes",
    "warnings",
}
PASS_KEYS = {
    "index",
    "side",
    "sheet_count",
    "sheet_order",
    "rotate_backs",
    "reload_instruction",
    "output",
}


def _run(capsys, *argv) -> dict:
    """Run one CLI invocation and parse its stdout as a single document."""
    assert main(list(argv)) == 0
    out = capsys.readouterr().out
    return json.loads(out)


def _folio(*extra: str) -> list[str]:
    """Arguments producing a real folio job, so signatures are non-empty."""
    return [FIXTURE, "--fold-scheme", "folio", "--landscape", *extra]


# -- envelope -------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected_report",
    [
        (["info", FIXTURE, "--json"], "info"),
        (["schedule", FIXTURE, "--json"], "schedule"),
        (["profile", "list", "--json"], "profile_list"),
        (
            ["profile", "show", "generic_face_up_in_order", "--json"],
            "profile",
        ),
        (
            ["print", FIXTURE, "--profile", "generic_face_up_in_order", "--json"],
            "print_plan",
        ),
    ],
)
def test_every_json_document_names_its_own_shape_and_version(
    capsys, argv, expected_report
):
    """A reader must be able to refuse a document it was not written for.

    Without ``report``/``report_version`` the only way to tell one shape
    from another is to guess from which keys happen to be present, which
    is exactly the fragility ``--json`` exists to remove.
    """
    document = _run(capsys, *argv)
    assert document["report"] == expected_report
    assert document["report_version"] == REPORT_VERSION
    assert document["deckle_version"] == DECKLE_VERSION


# -- info -----------------------------------------------------------------


def test_info_json_has_exactly_the_documented_keys(capsys):
    document = _run(capsys, "info", FIXTURE, "--json")

    assert set(document) == INFO_KEYS
    assert set(document["paper_pt"]) == SIZE_KEYS
    assert document["page_sizes_pt"]
    for size in document["page_sizes_pt"]:
        assert set(size) == PAGE_SIZE_KEYS


def test_info_json_warning_objects_have_exactly_the_documented_keys(capsys):
    """``kind`` is the field a script branches on, and it must be there.

    Folio onto portrait paper is the job that produces a real warning --
    it is the one ``test_cli.py`` already uses for the stderr version --
    so this asserts the shape against a document that genuinely has one
    rather than against an empty list.
    """
    document = _run(
        capsys, "info", FIXTURE, "--fold-scheme", "folio", "--json"
    )

    assert document["warnings"], "this job should produce a layout warning"
    for warning in document["warnings"]:
        assert set(warning) == WARNING_KEYS
    assert "sheet_orientation" in {w["kind"] for w in document["warnings"]}


def test_info_json_agrees_with_the_prose_it_replaces(capsys):
    """The two forms describe the same document, or one of them is lying."""
    document = _run(capsys, "info", FIXTURE, "--json")

    assert main(["info", FIXTURE]) == 0
    prose = capsys.readouterr().out

    assert f"page count: {document['page_count']}" in prose
    assert f"sheet count: {document['sheet_count']}" in prose
    assert f"blank count: {document['blank_count']}" in prose
    assert f"signature count: {document['signature_count']}" in prose


def test_info_json_counts_each_distinct_page_size(capsys):
    """The prose form emits a bare set; a script needs the counts.

    "Which size is this document mostly" has no answer from a set, and a
    book of 264 body pages plus two differently-sized covers is the
    ordinary case rather than an exotic one.
    """
    document = _run(capsys, "info", FIXTURE, "--json")

    assert sum(size["count"] for size in document["page_sizes_pt"]) == (
        document["page_count"]
    )


# -- schedule -------------------------------------------------------------


def test_schedule_json_has_exactly_the_documented_keys(capsys):
    document = _run(capsys, "schedule", *_folio(), "--json")

    assert set(document) == SCHEDULE_KEYS
    assert document["signatures"], "a folio job should have signatures"
    for signature in document["signatures"]:
        assert set(signature) == SIGNATURE_KEYS
        assert signature["sheets"]
        for sheet in signature["sheets"]:
            assert set(sheet) == SHEET_KEYS


def test_schedule_json_spine_width_is_an_object_when_thickness_is_known(capsys):
    """``null`` when unset, a named low/high pair when known.

    A bare two-element array is how the pair gets read the wrong way
    round, and the spine width is what a perfect binder cuts boards
    against.
    """
    thin = _run(capsys, "schedule", *_folio(), "--json")
    assert thin["spine_width_pt"] is None

    thick = _run(
        capsys, "schedule", *_folio("--paper-thickness", "0.004in"), "--json"
    )
    assert set(thick["spine_width_pt"]) == SPINE_KEYS
    assert thick["spine_width_pt"]["low_pt"] <= thick["spine_width_pt"]["high_pt"]


def test_schedule_json_keeps_null_for_a_blank_leaf(capsys):
    """A blank is the absence of a page number, so it is JSON's ``null``.

    Encoding it as ``0`` or ``""`` would make the reader guess which
    sentinel Deckle picked, and ``0`` is a plausible page number.
    """
    document = _run(capsys, "schedule", *_folio(), "--json")

    leaves = [
        leaf
        for signature in document["signatures"]
        for sheet in signature["sheets"]
        for leaf in sheet["front_pages"] + sheet["back_pages"]
    ]
    assert None in leaves, "the fixture pads with blanks under folio"
    assert all(leaf is None or isinstance(leaf, int) for leaf in leaves)


def test_schedule_json_goes_to_the_output_file_too(tmp_path, capsys):
    """``--json`` says what the schedule *is*, not where it goes.

    A ``--json -o`` that quietly wrote the bench sheet would be the worse
    of the two possible surprises: the file looks right until something
    parses it.
    """
    out = tmp_path / "schedule.json"
    assert main(["schedule", *_folio(), "--json", "-o", str(out)]) == 0

    document = json.loads(out.read_text(encoding="utf-8"))
    assert set(document) == SCHEDULE_KEYS


# -- profiles -------------------------------------------------------------


def test_profile_list_json_has_exactly_the_documented_keys(capsys):
    document = _run(capsys, "profile", "list", "--json")

    assert set(document) == PROFILE_LIST_KEYS
    assert document["profiles"]
    for entry in document["profiles"]:
        assert set(entry) == PROFILE_ENTRY_KEYS
        assert set(entry["profile"]) == PROFILE_FIELD_KEYS


def test_profile_show_json_has_exactly_the_documented_keys(capsys):
    document = _run(
        capsys, "profile", "show", "generic_face_down_reversed", "--json"
    )

    assert set(document) == PROFILE_KEYS
    assert set(document["profile"]) == PROFILE_FIELD_KEYS
    assert document["origin"] == "builtin"
    assert len(document["profile"]["imageable_area_pt"]) == 4


def test_profile_json_distinguishes_measured_from_generic(tmp_path, monkeypatch, capsys):
    """``origin`` is the field that says whether a number was measured.

    A saved profile came from printing a target and reading it with a
    ruler; a built-in is a stand-in for a measurement nobody has made.
    A listing that did not distinguish them would let a script pick the
    stand-in believing it had the calibration.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(
        ["profile", "set", "Mine", "--from", "generic_face_up_in_order"]
    ) == 0
    capsys.readouterr()

    document = _run(capsys, "profile", "list", "--json")
    origins = {entry["name"]: entry["origin"] for entry in document["profiles"]}

    assert origins["Mine"] == "saved"
    assert origins["generic_face_up_in_order"] == "builtin"
    saved = next(e for e in document["profiles"] if e["name"] == "Mine")
    assert saved["path"] is not None and saved["error"] is None


# -- print ----------------------------------------------------------------


def test_print_json_has_exactly_the_documented_keys(capsys):
    document = _run(
        capsys, "print", FIXTURE, "--profile", "generic_face_down_reversed",
        "--json",
    )

    assert set(document) == PRINT_PLAN_KEYS
    assert set(document["printer_profile"]) == PROFILE_ENTRY_KEYS
    assert set(document["printer_profile"]["profile"]) == PROFILE_FIELD_KEYS
    assert set(document["paper_pt"]) == SIZE_KEYS
    assert len(document["passes"]) == 2
    for print_pass in document["passes"]:
        assert set(print_pass) == PASS_KEYS


def test_print_json_says_in_the_document_that_nothing_was_submitted(capsys):
    """"Did this reach a printer?" must never be something a script infers.

    The prose says so on its last line; a machine reader cannot see prose,
    and a caller that assumed otherwise would think a job had been sent.
    """
    document = _run(
        capsys, "print", FIXTURE, "--profile", "generic_face_down_reversed",
        "--json",
    )

    assert document["submitted"] is False


# -- the promise stdout makes ---------------------------------------------


def _stdout_of(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "deckle.cli", *argv],
        capture_output=True,
        text=True,
    )


def test_json_stdout_carries_the_document_and_nothing_else(tmp_path):
    """The whole point of ``--json`` is stdout you can pipe into ``jq``.

    ``--auto-crop`` reports the insets it measured as it measures them.
    That line is worth keeping -- it is the whole output of the flag, the
    numbers you pin with ``--crop`` afterwards -- and it does not belong
    inside a JSON object. Before ``_json_stdout`` it was printed straight
    into the middle of one, so the document did not parse at all: a
    failure that appears only on the runs that have something to say.

    Layout warnings are a different case and are checked elsewhere: under
    ``--json`` they are *in* the document rather than moved to stderr,
    because the report is what the reader gets.
    """
    result = _stdout_of(
        "info", FIXTURE, "--fold-scheme", "folio", "--auto-crop", "--json"
    )

    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)  # the assertion: it parses at all
    assert set(document) == INFO_KEYS
    # Moved, not suppressed.
    assert "auto-crop: --crop" in result.stderr
    assert "auto-crop" not in result.stdout


def test_a_failing_json_command_leaves_stdout_empty():
    """On failure stdout stays empty, as section 8 of the GUIDE promises.

    A half-written document is worse than none: a reader that got a
    partial object would act on it.
    """
    result = _stdout_of("info", "no-such-file.pdf", "--json")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
