"""The duplex calibration sheet: what it contains, and what it must not use.

``PrinterProfile``'s four paper-behaviour fields -- ``flip_axis``,
``reverse_stack``, ``output_face``, ``feed_edge`` -- are not reported by
any driver. Until this sheet existed the only way to set them was to
guess a built-in preset and find out on a finished book, which is why
``PrinterProfile``'s calibration fields are deliberately CLI-only: wrong
values ruin a stack of paper with no way to preview the damage.

**The load-bearing test in this file is the last one.** The sheet's value
rests entirely on its independence: if it were produced by running pages
through ``plan_passes``, the operator's report would say only that Deckle
agrees with itself. Everything else here is legibility, which matters
too, but a legible sheet that encodes the rule under test is worth
nothing.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from deckle.cli.options import build_parser
from deckle.core import calibration_sheet
from deckle.core.calibration_sheet import (
    SHEETS,
    make_calibration_pdf,
    page_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The committed artifacts. They exist so the sheet can simply be printed
#: -- an owner with a printer and no checkout of the toolchain should not
#: have to run anything to get paper out of this.
ARTIFACTS = (
    REPO_ROOT / "docs/calibration/deckle-calibration-letter.portrait.pdf",
    REPO_ROOT / "docs/calibration/deckle-calibration-letter.landscape.pdf",
)


def _page_text(path, index: int) -> str:
    document = pdfium.PdfDocument(str(path))
    try:
        return document[index].get_textpage().get_text_range()
    finally:
        document.close()


def _page_count(path) -> int:
    document = pdfium.PdfDocument(str(path))
    try:
        return len(document)
    finally:
        document.close()


def _page_size(path, index: int) -> tuple[float, float]:
    document = pdfium.PdfDocument(str(path))
    try:
        page = document[index]
        return (page.get_width(), page.get_height())
    finally:
        document.close()


# -- the page order ----------------------------------------------------


def test_four_sheets_eight_faces_and_a_cover():
    """Two sheets cannot tell a reversed stack from a preserved one: with
    only sheets 1 and 2, "the backs came out in the other order" and "the
    printer handed them back as it received them" produce the same
    stack."""
    assert SHEETS == 4

    plan = page_plan()

    assert len(plan) == 9
    assert plan[0] == ("cover", 0, "")
    assert [entry[1:] for entry in plan[1:]] == [
        (1, "FRONT"), (2, "FRONT"), (3, "FRONT"), (4, "FRONT"),
        (1, "BACK"), (2, "BACK"), (3, "BACK"), (4, "BACK"),
    ]


def test_the_faces_are_in_plain_ascending_order_in_both_passes():
    """Stated separately from the literal above because it is the property
    that matters: no reversal, in either pass. A sheet that emitted the
    backs in the order ``plan_passes`` would emit them could not be used
    to check ``plan_passes``."""
    plan = page_plan(6)
    fronts = [number for kind, number, side in plan if side == "FRONT"]
    backs = [number for kind, number, side in plan if side == "BACK"]

    assert fronts == sorted(fronts) == [1, 2, 3, 4, 5, 6]
    assert backs == sorted(backs) == [1, 2, 3, 4, 5, 6]


def test_too_few_sheets_is_refused_rather_than_written():
    """A two-sheet sheet would look confident and answer nothing."""
    with pytest.raises(ValueError, match="at least 2"):
        make_calibration_pdf("unused.pdf", sheets=1)


# -- what is on the paper ----------------------------------------------


@pytest.fixture(scope="module")
def portrait(tmp_path_factory):
    path = tmp_path_factory.mktemp("cal") / "portrait.pdf"
    make_calibration_pdf(str(path))
    return path


@pytest.fixture(scope="module")
def landscape(tmp_path_factory):
    path = tmp_path_factory.mktemp("cal") / "landscape.pdf"
    make_calibration_pdf(str(path), landscape=True)
    return path


def test_portrait_and_landscape_differ_in_the_way_that_matters(portrait, landscape):
    """Non-negotiable, because the flip rule **inverts** between them: a
    portrait sheet's vertical edge is its long one, a landscape sheet's is
    its short one. One orientation establishes half the answer and invites
    the operator to generalise the other half backwards -- which is what
    the README and the GUIDE both did until 2026-09-11."""
    assert _page_size(portrait, 0) == (612.0, 792.0)
    assert _page_size(landscape, 0) == (792.0, 612.0)


def test_every_face_is_identifiable_without_interpretation(portrait):
    """A large sheet number and the word FRONT or BACK, on all eight."""
    assert _page_count(portrait) == 9

    for index, (_kind, number, side) in enumerate(page_plan()):
        if index == 0:
            continue
        text = _page_text(portrait, index)
        assert "SHEET" in text, f"page {index + 1} does not say which sheet"
        assert str(number) in text, f"page {index + 1} does not carry its number"
        assert side in text, f"page {index + 1} does not say which side"


def test_every_face_names_its_edges(portrait):
    """So the operator can report which edge the turn happened about from
    the paper in their hand, rather than describing it in their own words
    for somebody else to interpret."""
    for index in range(1, 9):
        text = _page_text(portrait, index)
        for edge in ("TOP EDGE", "BOTTOM EDGE", "LEFT", "RIGHT"):
            assert edge in text, f"page {index + 1} does not name its {edge}"


def test_sheet_one_front_carries_the_instructions(portrait):
    """Whoever picks the stack out of the tray has the essentials in hand.
    The full instructions and the grid are on the cover, and the strip
    says so."""
    strip = _page_text(portrait, 1)

    assert "PASS 1" in strip
    assert "pages 2-5" in strip and "pages 6-9" in strip
    assert "page 1" in strip


def test_the_cover_asks_for_observations_not_confirmations(portrait):
    """A sheet that said "the backs should be upright" would be answered
    "yes" by a tired person at a printer, and the one observation the
    exercise exists to collect would be lost. Every question names what to
    look at and offers the alternatives, and none of them names an
    expected answer."""
    cover = _page_text(portrait, 0)

    assert "Answer what you SEE" in cover
    for banned in ("should be", "should come", "ought to", "correct result", "expect"):
        assert banned not in cover.lower(), f"the cover states an expectation: {banned!r}"


def test_the_cover_maps_every_answer_onto_a_profile_field(portrait):
    """So the reply is a handful of observations rather than prose, and
    turns into a profile without a conversation."""
    cover = _page_text(portrait, 0)

    for field in (
        "output_face",
        "feed_edge",
        "reverse_stack",
        "flip_axis",
        "back_offset_x_pt",
        "back_offset_y_pt",
    ):
        assert field in cover, f"the grid does not resolve {field}"


def test_the_cover_says_to_do_both_orientations(portrait, landscape):
    for path in (portrait, landscape):
        cover = _page_text(path, 0)
        assert "portrait file AND the landscape file" in cover


def test_each_file_says_which_orientation_it_is(portrait, landscape):
    assert "PORTRAIT" in _page_text(portrait, 0)
    assert "LANDSCAPE" in _page_text(landscape, 0)


# -- the committed artifacts -------------------------------------------


@pytest.mark.parametrize("path", ARTIFACTS, ids=lambda p: p.name)
def test_the_committed_artifact_is_present_and_current(path):
    """Committed so the sheet can be printed without running anything.

    Checked by content rather than by byte-comparing a regeneration:
    ``pikepdf`` does not promise byte-identical output across versions,
    and a test that fails on a dependency bump teaches people to delete
    it.
    """
    assert path.exists(), (
        f"{path.relative_to(REPO_ROOT)} is missing -- regenerate with "
        "`deckle-cli calibration-sheet -o "
        "docs/calibration/deckle-calibration-letter.pdf`"
    )
    assert _page_count(path) == 9
    cover = _page_text(path, 0)
    assert "DECKLE DUPLEX CALIBRATION SHEET" in cover
    assert "flip_axis" in cover


def test_the_two_artifacts_are_the_two_orientations():
    portrait_path, landscape_path = ARTIFACTS
    width, height = _page_size(portrait_path, 0)
    assert height > width
    width, height = _page_size(landscape_path, 0)
    assert width > height


# -- the command -------------------------------------------------------


def test_the_command_writes_one_file_per_orientation(tmp_path, capsys):
    parser = build_parser()
    args = parser.parse_args(
        ["calibration-sheet", "-o", str(tmp_path / "cal.pdf")]
    )

    assert args.func(args) == 0

    assert (tmp_path / "cal.portrait.pdf").exists()
    assert (tmp_path / "cal.landscape.pdf").exists()
    out = capsys.readouterr().out
    assert "inverts between portrait and landscape" in out


def test_one_orientation_can_be_asked_for_alone(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        ["calibration-sheet", "-o", str(tmp_path / "cal.pdf"),
         "--orientation", "portrait"]
    )

    assert args.func(args) == 0

    assert (tmp_path / "cal.portrait.pdf").exists()
    assert not (tmp_path / "cal.landscape.pdf").exists()


def test_a_bad_destination_writes_neither_file(tmp_path):
    """A half-written pair is worse than none: the operator has no way to
    see which half is missing, and the flip rule needs both."""
    parser = build_parser()
    args = parser.parse_args(
        ["calibration-sheet", "-o", str(tmp_path / "no-such-folder" / "cal.pdf")]
    )

    assert args.func(args) == 1

    assert list(tmp_path.rglob("*.pdf")) == []


def test_the_parsers_sheet_default_matches_the_modules():
    """``options.py`` spells the number out rather than importing it --
    building the parser must not pull in pikepdf, and it happens on every
    invocation including ``--help``. This is the seam that keeps the two
    honest."""
    parser = build_parser()
    args = parser.parse_args(["calibration-sheet", "-o", "x.pdf"])

    assert args.sheets == SHEETS


# -- the one that the whole thing rests on -----------------------------


def test_the_calibration_sheet_never_routes_through_the_imposition_path():
    """**The load-bearing guard.**

    The sheet is evidence only because it is produced independently of the
    code it is used to check. Emitting it through ``plan_passes`` -- or
    through the imposer, or the exporter, or a ``PrinterProfile`` -- would
    make the operator's report a statement that Deckle agrees with itself.
    This session has twice caught probes that replicated their own
    subject; a printed one costs paper as well as time.

    Checked on the module's imports and on every name it mentions, not on
    a list of banned modules: the point is that *nothing* in the print or
    layout chain is reachable from here.
    """
    source = Path(inspect.getfile(calibration_sheet)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "deckle.core.printing",
        "deckle.core.print_session",
        "deckle.core.profiles",
        "deckle.core.layout",
        "deckle.core.signatures",
        "deckle.core.export",
        "deckle.core.marks",
        "deckle.core.models",
    }
    assert not (imported & forbidden), (
        f"the calibration sheet imports {sorted(imported & forbidden)} -- it "
        "must not be produced by the code it is printed to check"
    )

    # Belt and braces: the names themselves, so a lazy import inside a
    # function is caught too.
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned in ("plan_passes", "impose", "plan_sheets", "export_pdf"):
        assert banned not in called, (
            f"the calibration sheet calls {banned!r} -- see this test's docstring"
        )


def test_the_generator_applies_no_reversal_and_no_rotation():
    """The other half of the independence claim, on the output rather than
    on the imports: an eight-face document whose faces are in order and
    whose markers are identical face to face. Any half turn Deckle would
    apply has to happen in the *printer*, not in this file."""
    source = Path(inspect.getfile(calibration_sheet)).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    assert "reversed(" not in code, "the generator reverses something"
    assert "[::-1]" not in code, "the generator reverses something"
    # No page-level transform: the faces are drawn upright and identically,
    # so the only thing that can turn one is the printer.
    assert "/Rotate" not in code and "Rotate" not in code
    assert ".cm(" not in code, (
        "a coordinate transform on a face would be a rotation by another name"
    )

    # And the faces really are identical apart from their labels: the same
    # drawing function, called the same way, for all eight.
    plan = page_plan()
    assert len({side for _kind, _n, side in plan[1:]}) == 2
    assert all(entry[0] == "face" for entry in plan[1:])
