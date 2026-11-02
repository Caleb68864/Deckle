"""F12 section A: one signature, or one file per signature, from ``export``."""

from __future__ import annotations

import glob
import os

import pikepdf
import pytest

from deckle.cli import main


@pytest.fixture
def book(tmp_path):
    """A 32-page document: two gatherings of four sheets as folio."""
    path = tmp_path / "book.pdf"
    assert main(["dummy", "-o", str(path), "--pages", "32"]) == 0
    return str(path)


FOLIO = ["--fold-scheme", "folio", "--landscape", "--sheets-per-signature", "4"]


def _page_count(path):
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def _first_page_pixels(path):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].render(scale=0.25).to_pil().tobytes()
    finally:
        doc.close()


def test_exporting_one_signature_writes_only_its_sheets(book, tmp_path):
    out = tmp_path / "two.pdf"
    assert main(["export", book, "-o", str(out), *FOLIO, "--signature", "2"]) == 0
    assert _page_count(out) == 8


def test_signature_numbering_starts_at_one_like_the_schedule(book, tmp_path):
    by_signature = tmp_path / "a.pdf"
    by_sheets = tmp_path / "b.pdf"
    assert main(["export", book, "-o", str(by_signature), *FOLIO, "--signature", "1"]) == 0
    assert main(["export", book, "-o", str(by_sheets), *FOLIO, "--sheets", "0-3"]) == 0
    assert _page_count(by_signature) == _page_count(by_sheets)
    assert _first_page_pixels(by_signature) == _first_page_pixels(by_sheets)


@pytest.mark.parametrize("number", ["0", "3", "-1"])
def test_a_signature_the_document_does_not_have_is_refused(book, tmp_path, capsys, number):
    out = tmp_path / "x.pdf"
    assert main(["export", book, "-o", str(out), *FOLIO, f"--signature={number}"]) == 1
    assert "it has 2, numbered 1 to 2" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("flags", [["--signature", "1"], ["--split"]])
def test_an_unfolded_document_is_refused(book, tmp_path, capsys, flags):
    out = tmp_path / "x.pdf"
    assert main(["export", book, "-o", str(out), *flags]) == 1
    assert "no signatures" in capsys.readouterr().err
    assert glob.glob(str(tmp_path / "x*")) == []


@pytest.mark.parametrize(
    "flags, says",
    [
        (["--signature", "1", "--split"], "either --signature or --split"),
        (["--sheets", "0", "--signature", "1"], "either --sheets or --signature"),
        (["--sheets", "0", "--split"], "either --sheets or --split"),
    ],
)
def test_two_ways_of_saying_one_thing_are_refused(book, tmp_path, capsys, flags, says):
    out = tmp_path / "x.pdf"
    assert main(["export", book, "-o", str(out), *FOLIO, *flags]) == 1
    assert says in capsys.readouterr().err
    assert glob.glob(str(tmp_path / "x*")) == []


def test_split_writes_one_file_per_signature_in_binding_order(book, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    assert main(["export", book, "-o", str(out), *FOLIO, "--split"]) == 0

    written = sorted(glob.glob(str(tmp_path / "out*.pdf")))
    assert [os.path.basename(p) for p in written] == ["out-sig-01.pdf", "out-sig-02.pdf"]
    assert [_page_count(p) for p in written] == [8, 8]
    assert not out.exists()
    stdout = capsys.readouterr().out
    assert stdout.index("out-sig-01.pdf") < stdout.index("out-sig-02.pdf")


def test_split_checks_every_path_before_writing_any(book, tmp_path, capsys):
    (tmp_path / "out-sig-02.pdf").mkdir()

    assert main(["export", book, "-o", str(tmp_path / "out.pdf"), *FOLIO, "--split"]) == 1

    assert not (tmp_path / "out-sig-01.pdf").exists()
    assert "out-sig-02.pdf" in capsys.readouterr().err


def test_split_carries_the_pass_and_says_the_instruction_once(book, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    args = [
        "export", book, "-o", str(out), *FOLIO, "--split", "--pass", "back",
        "--profile", "generic_face_down_reversed", "--back-offset", "1pt,1pt",
    ]
    assert main(args) == 0

    assert [_page_count(p) for p in sorted(glob.glob(str(tmp_path / "out-sig-*.pdf")))] == [4, 4]
    captured = capsys.readouterr()
    assert captured.out.count("wrote ") == 2


def test_split_on_a_dry_run_writes_nothing(book, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    assert main(["export", book, "-o", str(out), *FOLIO, "--split", "--dry-run"]) == 0
    stdout = capsys.readouterr().out
    assert "out-sig-01.pdf" in stdout and "out-sig-02.pdf" in stdout
    assert glob.glob(str(tmp_path / "out*")) == []
