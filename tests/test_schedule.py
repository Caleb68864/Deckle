"""Tests for the binding schedule.

The load-bearing test here is
``test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern``.
It pins the page ordering against the arrangement any bookbinding manual
prints, written out by hand rather than derived from the code under test --
so it fails if ``saddle_order`` ever changes meaning, and it does not merely
agree with whatever the imposer happens to produce.

That matters because the page ordering is the one part of folio that
automated tests cannot fully settle: software cannot tell you which way paper
folds. The schedule makes the ordering *readable*, which is the next best
thing -- a binder can check "16 and 1 on the outside, 8 and 9 in the middle"
at a glance, without folding anything.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from deckle.core.layout import GutterShiftStrategy, SaddleStitchStrategy
from deckle.core.models import LayoutSettings, SourcePage, SourceRef
from deckle.core.schedule import build_schedule, format_schedule_text

LETTER_LANDSCAPE = (792.0, 612.0)


def _pages(n: int) -> list[SourcePage]:
    """``n`` distinct source pages, so page numbers in the output mean
    something rather than repeating."""
    return [
        SourcePage(
            ref=SourceRef(
                path="book.pdf",
                page_index=i,
                sha256="a" * 64,
                width_pt=400.0,
                height_pt=600.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]


def _folio_settings(**overrides) -> LayoutSettings:
    base = dict(
        paper=LETTER_LANDSCAPE,
        gutter_pt=36.0,
        binding_edge="left",
        fold_scheme="folio",
        sheets_per_signature=4,
        sewing_stations=3,
    )
    base.update(overrides)
    return LayoutSettings(**base)


def _folio_schedule(n_pages: int, **overrides):
    settings = _folio_settings(**overrides)
    plan = SaddleStitchStrategy().impose(_pages(n_pages), settings)
    return build_schedule(plan, settings)


# -- the ordering, pinned against the manual ----------------------------


def test_a_sixteen_page_signature_matches_the_classic_saddle_stitch_pattern():
    """Written out by hand from the standard arrangement, not derived.

    A 16-page signature is four nested sheets. The outermost carries the
    first and last pages; each sheet inward moves one page in from each
    end, until the innermost carries the two middle pages facing each
    other. Every bookbinding manual prints this table.
    """
    schedule = _folio_schedule(16, sheets_per_signature=4)

    assert schedule.signature_count == 1
    signature = schedule.signatures[0]
    assert signature.sheet_count == 4

    expected = [
        # (front pages, back pages), outermost sheet first
        ((16, 1), (2, 15)),
        ((14, 3), (4, 13)),
        ((12, 5), (6, 11)),
        ((10, 7), (8, 9)),
    ]
    actual = [(s.front_pages, s.back_pages) for s in signature.sheets]
    assert actual == expected, (
        "the saddle-stitch page ordering no longer matches the standard "
        "arrangement -- a book bound from this would read out of order"
    )


def test_the_outermost_sheet_is_first_because_that_is_gathering_order():
    schedule = _folio_schedule(16)
    sheets = schedule.signatures[0].sheets

    assert sheets[0].is_outermost is True
    assert sheets[0].position == 1
    assert [s.position for s in sheets] == [1, 2, 3, 4]
    # Position 1 wraps the rest, so it carries page 1 and the last page.
    assert 1 in sheets[0].front_pages


def test_the_innermost_sheet_carries_the_two_middle_pages():
    """The fold's centre. If these are not adjacent, the fold is wrong."""
    schedule = _folio_schedule(16)
    innermost = schedule.signatures[0].sheets[-1]

    middle = innermost.back_pages
    assert middle == (8, 9), f"innermost back should face 8|9, got {middle}"


# -- multiple signatures -------------------------------------------------


def test_signatures_are_numbered_from_one_for_the_bench():
    """``Signature.index`` counts from 0; a person counts from 1."""
    schedule = _folio_schedule(64, sheets_per_signature=4)

    assert schedule.signature_count == 4
    assert [s.index for s in schedule.signatures] == [1, 2, 3, 4]


def test_each_signature_reports_the_page_span_it_contains():
    schedule = _folio_schedule(64, sheets_per_signature=4)

    first, second = schedule.signatures[0], schedule.signatures[1]
    assert (first.first_page, first.last_page) == (1, 16)
    assert (second.first_page, second.last_page) == (17, 32)


def test_a_short_final_signature_is_reported_not_hidden():
    """20 pages at 4 sheets per signature is one full signature plus one
    sheet -- the remainder is normal and the schedule says so."""
    schedule = _folio_schedule(20, sheets_per_signature=4)

    counts = [s.sheet_count for s in schedule.signatures]
    assert counts[0] == 4
    assert counts[-1] < 4
    assert any("last signature is shorter" in note for note in schedule.notes)


def test_padding_blanks_are_counted():
    """14 pages cannot fold; it rounds up to 16."""
    schedule = _folio_schedule(14, sheets_per_signature=4)

    assert schedule.blank_total == 2
    blanks = [
        p
        for sig in schedule.signatures
        for sheet in sig.sheets
        for p in sheet.front_pages + sheet.back_pages
        if p is None
    ]
    assert len(blanks) == 2


# -- creep ---------------------------------------------------------------


def test_creep_is_estimated_when_paper_thickness_is_known():
    schedule = _folio_schedule(64, sheets_per_signature=8, paper_thickness_pt=0.5)

    creep_notes = [n for n in schedule.notes if "creep" in n.lower()]
    assert creep_notes, f"expected a creep advisory, got {schedule.notes}"
    # 8 sheets nested, 0.5pt each -> about 3.5pt on the innermost leaf.
    assert "3.5" in creep_notes[0]


def test_an_unset_paper_thickness_says_so_rather_than_estimating_zero():
    """Silence would read as 'no creep', which is a different claim."""
    schedule = _folio_schedule(64, paper_thickness_pt=0.0)

    assert any("thickness is not set" in note for note in schedule.notes)
    assert not any("creep is about" in note for note in schedule.notes)


def test_a_single_sheet_signature_has_no_creep():
    schedule = _folio_schedule(4, sheets_per_signature=1, paper_thickness_pt=0.5)

    assert not any("creep is about" in note for note in schedule.notes)


# -- the gutter path has no schedule ------------------------------------


def test_the_gutter_path_produces_no_signatures_rather_than_inventing_them():
    """One page per side is not a folded book. Confident instructions for
    work nobody is doing would be worse than none."""
    settings = LayoutSettings(paper=(612.0, 792.0), gutter_pt=36.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(_pages(8), settings)

    schedule = build_schedule(plan, settings)

    assert schedule.signature_count == 0
    text = format_schedule_text(schedule)
    assert "nothing to gather or sew" in text
    assert "SIGNATURE 1" not in text


# -- the rendered text ---------------------------------------------------


def test_the_text_names_gathering_order_explicitly():
    """'first listed is the OUTSIDE' is the sentence that prevents a
    signature being assembled inside out."""
    text = format_schedule_text(_folio_schedule(16), "book.pdf")

    assert "OUTSIDE of the fold" in text
    assert "outermost" in text


def test_blanks_are_named_in_the_text_not_left_as_gaps():
    text = format_schedule_text(_folio_schedule(14))

    assert "blank" in text


def test_the_text_carries_the_manual_duplex_reload_instruction():
    text = format_schedule_text(_folio_schedule(16))

    assert "reload" in text.lower()


def test_sewing_stations_are_described_with_their_inset():
    text = format_schedule_text(_folio_schedule(16, sewing_stations=3))

    assert "3 sewing station" in text
    assert "from head and tail" in text


def test_zero_sewing_stations_says_none_were_marked():
    text = format_schedule_text(_folio_schedule(16, sewing_stations=0))

    assert "No sewing stations" in text


def test_the_title_appears_when_given():
    text = format_schedule_text(_folio_schedule(16), "Traveller.pdf")

    assert "Traveller.pdf" in text


# -- the CLI -------------------------------------------------------------


FIXTURE = "tests/fixtures/sample.pdf"


def test_cli_schedule_prints_to_stdout():
    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "schedule", FIXTURE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert "BINDING SCHEDULE" in result.stdout


def test_cli_schedule_writes_a_file_with_output(tmp_path):
    out = tmp_path / "schedule.txt"
    result = subprocess.run(
        [
            sys.executable, "-m", "deckle.cli", "schedule", FIXTURE,
            "-o", str(out),
            "--fold-scheme", "folio",
            "--paper", "792x612pt",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert "BINDING SCHEDULE" in out.read_text(encoding="utf-8")


def test_cli_schedule_rejects_a_bad_output_path_before_doing_the_work(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "deckle.cli", "schedule", FIXTURE,
            "-o", str(tmp_path),  # a directory
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 1
    assert "existing folder" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("sheets_per_signature", [1, 2, 4, 8])
def test_every_page_appears_exactly_once_across_the_schedule(sheets_per_signature):
    """The property that matters most and is easy to lose: imposition is a
    permutation. A page duplicated or dropped means a misbound book."""
    n_pages = 32
    schedule = _folio_schedule(n_pages, sheets_per_signature=sheets_per_signature)

    seen = [
        p
        for sig in schedule.signatures
        for sheet in sig.sheets
        for p in sheet.front_pages + sheet.back_pages
        if p is not None
    ]
    assert sorted(seen) == list(range(1, n_pages + 1))


# -- at the printer ------------------------------------------------------
#
# The schedule is what the user has in hand when the paper goes in, so it
# is the right place to say the two things that ruin a job before a single
# fold: a viewer that rescales the sheet, and a duplexer turning it about
# the wrong edge. Both apply to the gutter path as much as to folio, and
# the gutter path is the one the schedule used to fall silent on.


def _gutter_schedule(n_pages: int = 8, paper=(612.0, 792.0)):
    settings = LayoutSettings(paper=paper, gutter_pt=36.0, binding_edge="left")
    plan = GutterShiftStrategy().impose(_pages(n_pages), settings)
    return build_schedule(plan, settings)


def test_a_gutter_schedule_still_says_how_much_paper_the_job_needs():
    text = format_schedule_text(_gutter_schedule(8))

    assert "Sheets to print: 4" in text


def test_a_gutter_schedule_tells_the_user_to_print_at_actual_size():
    text = format_schedule_text(_gutter_schedule(8))

    assert "actual size" in text.lower()
    assert "fit to page" in text.lower()


def test_a_folio_schedule_tells_the_user_to_print_at_actual_size():
    text = format_schedule_text(_folio_schedule(16))

    assert "actual size" in text.lower()


def test_a_portrait_gutter_schedule_names_the_long_edge_flip():
    text = format_schedule_text(_gutter_schedule(8, paper=(612.0, 792.0)))

    assert "long edge" in text.lower()
    assert "short edge" not in text.lower()


def test_a_landscape_folio_schedule_names_the_short_edge_flip():
    # Folio imposes onto a landscape sheet; a long-edge flip lands every
    # back upside down.
    text = format_schedule_text(_folio_schedule(16))

    assert "short edge" in text.lower()
    assert "long edge" not in text.lower()


def test_every_schedule_says_to_proof_one_sheet_before_the_stack():
    """Named by the number the rest of Deckle uses. Sheets are 0-based
    everywhere -- the warnings, the gathering list, ``--sheets`` -- so a
    proof instruction saying "sheet 1" points at the second sheet."""
    for text in (
        format_schedule_text(_gutter_schedule(8)),
        format_schedule_text(_folio_schedule(16)),
    ):
        assert "print sheet 0 on its own first" in text.lower()


def test_the_flip_edge_is_carried_on_the_schedule_not_re_derived_by_the_text():
    """``schedule.py`` describes; it never re-derives. A formatter that
    worked the flip edge out for itself would be free to disagree with the
    ``/Duplex`` value the exporter wrote into the PDF."""
    assert _gutter_schedule(8, paper=(612.0, 792.0)).duplex_flip_edge == "long"
    assert _gutter_schedule(8, paper=(792.0, 612.0)).duplex_flip_edge == "short"
