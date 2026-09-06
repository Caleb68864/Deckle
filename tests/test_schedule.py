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


# -- reader-facing page numbers ------------------------------------------
#
# `_page_numbers` returned `source_ref.page_index + 1`: the page's offset
# INSIDE THE FILE IT CAME FROM. That equals the reader's numbering only for
# a document that is exactly one source, imported whole, with nothing
# skipped and nothing inserted -- which is what every existing test used.
# A four-page book made of two 2-page PDFs was reported as
# "front: 2 1 / back: 2 1": pages 1 and 2 twice, pages 3 and 4 nowhere.


def _page(path: str, index: int) -> SourcePage:
    """One source page from a named file."""
    return SourcePage(
        ref=SourceRef(path=path, page_index=index, sha256="a" * 64,
                      width_pt=400.0, height_pt=600.0),
        rotate_deg=0, skipped=False,
    )


def _two_source_pages():
    """Four leaves from two different 2-page files."""
    return [
        _page("a.pdf", 0), _page("a.pdf", 1),
        _page("b.pdf", 0), _page("b.pdf", 1),
    ]


def test_two_sources_are_numbered_by_the_reader_not_by_the_file():
    """The defect. Both files contribute pages 1 and 2 by their own count.

    A binder checking sheet against schedule sees each number once, in the
    place the folded book puts it -- not each file's offsets repeated.
    """
    s = _folio_settings()
    plan = SaddleStitchStrategy().impose(_two_source_pages(), s)
    schedule = build_schedule(plan, s)

    sheet = schedule.signatures[0].sheets[0]
    assert sheet.front_pages == (4, 1)
    assert sheet.back_pages == (2, 3)


def test_every_page_number_appears_exactly_once():
    """The property that makes a schedule checkable at the bench.

    Stated over the whole document rather than one sheet, because the old
    behaviour's failure was a duplicate and an omission at the same time,
    and either alone would be caught by a weaker assertion.
    """
    s = _folio_settings()
    plan = SaddleStitchStrategy().impose(_two_source_pages(), s)
    schedule = build_schedule(plan, s)

    numbers = [
        n
        for sig in schedule.signatures
        for sheet in sig.sheets
        for n in sheet.front_pages + sheet.back_pages
        if n is not None
    ]
    assert sorted(numbers) == [1, 2, 3, 4], numbers


def test_a_skipped_page_is_not_counted_by_the_reader():
    """Skipped pages are not in the book, so they take no number.

    They never reach `plan.sheets` at all. The leaves that remain are
    numbered 1, 2, 3 by position, and the padding blank that rounds the
    signature out takes the fourth position without printing a number.
    """
    s = _folio_settings()
    pages = [_page("a.pdf", i) for i in range(4)]
    pages[1] = SourcePage(ref=pages[1].ref, rotate_deg=0, skipped=True)

    schedule = build_schedule(SaddleStitchStrategy().impose(pages, s), s)
    sheet = schedule.signatures[0].sheets[0]

    numbers = sorted(
        n for n in sheet.front_pages + sheet.back_pages if n is not None
    )
    assert numbers == [1, 2, 3]
    assert None in sheet.front_pages + sheet.back_pages


def test_a_blank_consumes_its_position():
    """A blank leaf prints as `blank` but still occupies a page number.

    A person thumbing the bound book counts blank leaves along with
    printed ones, so the page after a blank is n + 2. Numbering only the
    content pages would produce numbers that match nothing physical.
    """
    s = _folio_settings()
    pages = [_page("a.pdf", i) for i in range(3)]

    schedule = build_schedule(SaddleStitchStrategy().impose(pages, s), s)
    sheet = schedule.signatures[0].sheets[0]

    every = sheet.front_pages + sheet.back_pages
    assert len(every) == 4
    assert sorted(n for n in every if n is not None) == [1, 2, 3]


# -- one creep opinion ----------------------------------------------------
#
# Three places answered "is this creep worth mentioning" and no two agreed.
# `layout._creep_advisory` judged the REQUESTED `sheets_per_signature` even
# when `signature_lengths` or `blank_mode="balanced"` had overridden it;
# `schedule._creep_note` ignored `trim_pt` entirely; and the two used
# different operators at the boundary. Each was correct against its own
# tests. The defect was that there were three of them.


def _creep_settings(**overrides) -> LayoutSettings:
    base = dict(
        paper=LETTER_LANDSCAPE,
        gutter_pt=18.0,
        binding_edge="left",
        fold_scheme="folio",
    )
    base.update(overrides)
    return LayoutSettings(**base)


def _both_opinions(n_pages: int, **overrides):
    """What the layout and the schedule each say about one document."""
    s = _creep_settings(**overrides)
    plan = SaddleStitchStrategy().impose(_pages(n_pages), s)
    layout_warns = any(w.kind == "creep_advisory" for w in plan.warnings)
    # "Fore-edge creep", not just "creep": with no thickness set the
    # schedule emits a *different* note saying creep was not estimated,
    # which is correct and is not a warning about creep.
    schedule_warns = any(
        n.startswith("Fore-edge creep") for n in build_schedule(plan, s).notes
    )
    return layout_warns, schedule_warns


def test_the_advisory_judges_the_signatures_actually_built():
    """`signature_lengths` overrides the requested gathering size.

    Asking for one 8-sheet signature while `sheets_per_signature` still
    says 2 built 8-sheet gatherings creeping 2.8pt, and the layout judged
    the 2 it had been asked for -- so the preview was silent about a
    document the schedule warned on.
    """
    layout_warns, schedule_warns = _both_opinions(
        32, sheets_per_signature=2, signature_lengths=(8,), paper_thickness_pt=0.4
    )
    assert layout_warns is True
    assert schedule_warns is True


def test_a_planned_trim_silences_both_or_neither():
    """A binder who is going to plough the fore-edge is not told to.

    The layout has honoured `trim_pt` since it was written; the schedule
    ignored it, so the same document produced a warning on the bench sheet
    and silence in the preview.
    """
    layout_warns, schedule_warns = _both_opinions(
        32, sheets_per_signature=8, paper_thickness_pt=0.4, trim_pt=12.0
    )
    assert layout_warns is False
    assert schedule_warns is False


def test_creep_exactly_at_the_tolerance_is_absorbed_by_both():
    """The boundary the two operators disagreed about.

    Five sheets of 0.25pt stock creep exactly 1.0pt, which is exactly
    `CREEP_INVISIBLE_PT`. The layout used `<=` and stayed quiet; the
    schedule used `<` and warned. Absorbed is the right answer, because it
    is the reading `suggest_sheets_per_signature` already had.
    """
    from deckle.core.paper import CREEP_INVISIBLE_PT, creep_pt

    assert creep_pt(5, 0.25) == CREEP_INVISIBLE_PT

    layout_warns, schedule_warns = _both_opinions(
        32, sheets_per_signature=5, paper_thickness_pt=0.25
    )
    assert layout_warns is False
    assert schedule_warns is False


@pytest.mark.parametrize("caliper", [0.0, 0.1, 0.25, 0.4, 1.0])
@pytest.mark.parametrize("trim", [0.0, 12.0])
@pytest.mark.parametrize("sheets", [1, 2, 5, 8])
def test_the_two_opinions_agree_on_every_combination(caliper, trim, sheets):
    """The property, rather than the three cases that happened to break.

    Neither number is asserted here -- only that the preview and the bench
    sheet say the same thing about one document, which is the whole point
    of a single predicate.
    """
    layout_warns, schedule_warns = _both_opinions(
        32, sheets_per_signature=sheets, paper_thickness_pt=caliper, trim_pt=trim
    )
    assert layout_warns == schedule_warns, (caliper, trim, sheets)
