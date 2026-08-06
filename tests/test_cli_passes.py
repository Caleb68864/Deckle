"""``--pass``: running a manual-duplex job from the exported PDF.

Deckle exists for printers with no duplexer, and its whole argument is that
it knows what your printer does to a stack of paper on the second pass.
That knowledge -- ``plan_passes`` and the verified back-pass ordering table
it implements -- was reachable only through the desktop app's Qt backend.
The exported PDF, which is how a real job actually got printed, interleaved
front and back and so only worked on a printer that already had a duplexer.

The two axes, from ``PrinterProfile``:

- ``reverse_stack`` decides the SHEET ORDER of the back pass.
- ``flip_axis == "long"`` decides whether the backs need a HALF TURN.

``generic_face_down_reversed`` is long-edge and reversing;
``generic_face_up_in_order`` is short-edge and order-preserving. Between
them they cover both axes in both positions.
"""

from __future__ import annotations

import os

import pikepdf
import pypdfium2
import pytest
from pikepdf import Name
from pikepdf.canvas import ContentStreamBuilder

from deckle.cli import main


def _numbered_source(tmp_path, n_pages: int) -> str:
    """A PDF whose pages say which page they are, so order is observable.

    Blank pages are indistinguishable once imposed -- every gutter-shift
    back carries the same placement geometry -- so a test asserting sheet
    ORDER cannot use them.
    """
    path = os.path.join(str(tmp_path), "numbered.pdf")
    pdf = pikepdf.Pdf.new()
    for i in range(n_pages):
        page = pdf.add_blank_page(page_size=(400.0, 600.0))
        font = pikepdf.Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
        )
        font_name = page.add_resource(font, Name.Font, prefix="Ft")
        builder = ContentStreamBuilder()
        builder.push()
        builder.begin_text()
        builder.set_text_font(font_name, 36)
        builder.move_cursor(50.0, 300.0)
        builder.show_text(f"PAGE{i + 1}")
        builder.end_text()
        builder.pop()
        page.contents_add(b"q\n" + builder.build() + b"Q\n")
    pdf.save(path)
    pdf.close()
    return path


def _page_labels(path: str) -> list[str]:
    """The ``PAGEn`` token on each output page, in document order."""
    document = pypdfium2.PdfDocument(path)
    try:
        labels = []
        for index in range(len(document)):
            text = document[index].get_textpage().get_text_bounded()
            labels.append(text.strip() or "blank")
        return labels
    finally:
        document.close()


def _rotations(path: str) -> list[int]:
    with pikepdf.open(path) as pdf:
        return [page.rotation for page in pdf.pages]


# --- the two axes -------------------------------------------------------


def test_a_front_pass_is_every_front_in_plan_order(tmp_path):
    src = _numbered_source(tmp_path, 6)  # 6 pages -> 3 sheets
    out = os.path.join(str(tmp_path), "fronts.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "front",
         "--profile", "generic_face_down_reversed"]
    )

    assert rc == 0
    # Fronts carry the odd-numbered pages under one-page-per-side.
    assert _page_labels(out) == ["PAGE1", "PAGE3", "PAGE5"]


def test_a_reversing_printer_gets_its_backs_in_reverse_sheet_order(tmp_path):
    """``output_face="down"`` stacks the output face-down, so reloading it
    feeds the last sheet first. The back pass has to match."""
    src = _numbered_source(tmp_path, 6)
    out = os.path.join(str(tmp_path), "backs.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "back",
         "--profile", "generic_face_down_reversed"]
    )

    assert rc == 0
    assert _page_labels(out) == ["PAGE6", "PAGE4", "PAGE2"]


def test_an_order_preserving_printer_gets_its_backs_in_plan_order(tmp_path):
    src = _numbered_source(tmp_path, 6)
    out = os.path.join(str(tmp_path), "backs.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "back",
         "--profile", "generic_face_up_in_order"]
    )

    assert rc == 0
    assert _page_labels(out) == ["PAGE2", "PAGE4", "PAGE6"]


def test_a_long_edge_flip_turns_the_backs_a_half_turn(tmp_path):
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    main(
        ["export", src, "-o", out, "--pass", "back",
         "--profile", "generic_face_down_reversed"]  # flip_axis="long"
    )

    assert _rotations(out) == [180, 180]


def test_a_short_edge_flip_leaves_the_backs_alone(tmp_path):
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    main(
        ["export", src, "-o", out, "--pass", "back",
         "--profile", "generic_face_up_in_order"]  # flip_axis="short"
    )

    assert _rotations(out) == [0, 0]


def test_a_front_pass_is_never_turned(tmp_path):
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "fronts.pdf")

    main(
        ["export", src, "-o", out, "--pass", "front",
         "--profile", "generic_face_down_reversed"]
    )

    assert _rotations(out) == [0, 0]


# --- what it tells the operator ----------------------------------------


def test_a_pass_prints_the_reload_instruction(tmp_path, capsys):
    """The instruction is the feature. A back pass PDF without it is a
    stack of paper and a guess about which way round it goes."""
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    main(
        ["export", src, "-o", out, "--pass", "back",
         "--profile", "generic_face_down_reversed"]
    )

    stdout = capsys.readouterr().out.lower()
    assert "reverse" in stdout
    assert "long" in stdout


# --- refusals -----------------------------------------------------------


def test_a_pass_without_a_profile_is_refused(tmp_path, capsys):
    """Neither axis has a safe default: guessing wrong prints every back
    onto the wrong front, and the paper is spent before anyone can tell."""
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    rc = main(["export", src, "-o", out, "--pass", "back"])

    assert rc == 1
    assert "--profile" in capsys.readouterr().err


def test_an_unknown_profile_names_the_ones_that_exist(tmp_path, capsys):
    src = _numbered_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "back", "--profile", "no-such-printer"]
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "no-such-printer" in err
    assert "generic_face_down_reversed" in err, "the message lists no alternatives"


def test_a_pass_can_be_narrowed_to_one_sheet_for_a_reprint(tmp_path):
    """"Reprint sheet 1" is a normal request, and `plan_passes` documents a
    narrower sheet list as the same path with a smaller input."""
    src = _numbered_source(tmp_path, 6)
    out = os.path.join(str(tmp_path), "reprint.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "back", "--sheets", "1",
         "--profile", "generic_face_down_reversed"]
    )

    assert rc == 0
    assert _page_labels(out) == ["PAGE4"]
