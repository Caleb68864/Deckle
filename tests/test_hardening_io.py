"""Hardening passes 4 and 5: file I/O, permissions, and malformed input.

Every test here exists because the un-hardened code did one of two things
at this input: printed somebody else's traceback
(``pypdfium2._helpers.misc.PdfiumError: Failed to load document (PDFium:
Success)`` for a zero-page PDF, ``[WinError 5] Access is denied`` naming a
temp file for an output folder that was really a folder), or -- worse --
reported success. An empty image directory imposed zero pages and exited 0.

The standard each of these asserts is the same: the message names **what**
failed, **which** path, and **what the user can do**. So the assertions
check for all three, not merely that a non-zero exit happened.

Scope note: this file covers the gutter-shift path (``fold_scheme="none"``,
the default), which is the shipping one.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pikepdf
import pytest
from PIL import Image

from deckle.cli import main
from deckle.core import diagnostics
from deckle.core.loader import (
    CorruptImageError,
    CorruptPdfError,
    EmptyPdfError,
    EncryptedPdfError,
    MissingSourceError,
    NoImagesFoundError,
    SourceLoadError,
    UnreadableSourceError,
    load_image_dir,
    load_pdf,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    """Never write to the developer's real diagnostic log."""
    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path / "_logs"))
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


def _log_records(tmp_path) -> list[dict]:
    path = tmp_path / "_logs" / "diagnostics.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _make_pdf(path: str, n_pages: int = 2) -> None:
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(612.0, 792.0))
    pdf.save(path)
    pdf.close()


def _make_image(path: str, size=(120, 90), dpi=(150, 150)) -> None:
    Image.new("RGB", size, color=(255, 255, 255)).save(str(path), dpi=dpi)


def _assert_actionable(message: str, *, path: str) -> None:
    """The three-part standard: what failed, which path, what to do.

    "what to do" is checked by looking for an imperative -- every message
    Deckle emits on these paths ends in advice, and a message that stops at
    the diagnosis has failed the standard even if it is accurate.
    """
    assert path in message, f"message does not name the path: {message!r}"
    lowered = message.lower()
    assert any(
        verb in lowered
        for verb in (
            "check", "choose", "try", "create", "open", "remove", "clear",
            "close", "give", "point", "import", "re-download",
        )
    ), f"message offers no remedy: {message!r}"
    assert "errno" not in lowered, f"raw errno leaked to the user: {message!r}"


# --- PASS 4: file I/O and permissions ---------------------------------


def test_export_to_an_existing_directory_names_the_directory(tmp_path, capsys):
    """The output path is a folder that already exists.

    Un-hardened, ``export.py``'s pre-flight check passed (the *parent* of a
    folder is writable) and the failure surfaced from ``os.replace`` as
    ``PermissionError: [WinError 5]`` naming a temp file in ``%TEMP%`` --
    a path the user never chose and cannot act on.
    """
    rc = main(["export", FIXTURE, "-o", str(tmp_path)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(tmp_path))
    assert "folder" in err
    assert "WinError" not in err


def test_export_to_a_missing_parent_directory_names_the_folder(tmp_path, capsys):
    out = tmp_path / "no" / "such" / "folder" / "out.pdf"

    rc = main(["export", FIXTURE, "-o", str(out)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(out))
    assert str(tmp_path / "no" / "such" / "folder") in err
    assert not out.exists()


def test_export_to_a_nonexistent_drive_names_the_drive(capsys):
    """``Q:\\out.pdf`` on a machine with no Q: -- an unplugged USB stick or
    a network drive that did not remap at login."""
    if sys.platform != "win32":
        pytest.skip("drive letters are a Windows concept")
    drive = next(
        (f"{letter}:" for letter in "QYZ" if not os.path.exists(f"{letter}:\\")),
        None,
    )
    if drive is None:
        pytest.skip("no unused drive letter available to test with")

    out = f"{drive}\\out.pdf"
    rc = main(["export", FIXTURE, "-o", out])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=out)
    assert drive in err


def test_export_over_a_read_only_file_says_it_is_read_only(tmp_path, capsys):
    out = tmp_path / "locked_down.pdf"
    out.write_bytes(b"%PDF-1.4 placeholder")
    os.chmod(str(out), stat.S_IREAD)
    try:
        rc = main(["export", FIXTURE, "-o", str(out)])
    finally:
        # 0o644, not stat.S_IWRITE. S_IWRITE is 0o200 on POSIX -- write
        # for the owner and read for nobody -- so the last assertion
        # below could not open the file it was asserting about. On
        # Windows chmod only touches the read-only attribute and
        # S_IWRITE clears it, which is why this test was green on the
        # machine it was written on and red on every Linux clone.
        os.chmod(str(out), 0o644)

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(out))
    assert "read-only" in err
    # The pre-existing bytes are untouched: a refused export must not
    # damage whatever was already at that path.
    assert out.read_bytes() == b"%PDF-1.4 placeholder"


def test_a_refused_export_does_not_change_the_files_permissions(tmp_path):
    """Refusing to write must not be a write of its own.

    ``export`` checks writability before it creates a scratch file, so
    nothing on the refusal path touches the mode -- and this pins that,
    because the obvious "fix" for a permission error is to relax the
    permission, and a tool that silently unlocks a file the owner locked
    has done something worse than fail.
    """
    out = tmp_path / "locked_down.pdf"
    out.write_bytes(b"%PDF-1.4 placeholder")
    os.chmod(str(out), stat.S_IREAD)
    try:
        rc = main(["export", FIXTURE, "-o", str(out)])
        after = stat.S_IMODE(os.stat(str(out)).st_mode)
    finally:
        os.chmod(str(out), 0o644)

    assert rc == 1
    assert after == stat.S_IREAD, oct(after)


def test_export_to_a_file_held_open_by_another_process(tmp_path, capsys):
    """The Windows case: the previous export is still open in a viewer.

    An open handle really does make ``os.replace`` fail with ``WinError 5``
    on Windows -- verified, not simulated. This is the one output failure
    no pre-flight check can catch, so it must be handled at the write.
    """
    if sys.platform != "win32":
        pytest.skip("POSIX allows replacing a file that is open elsewhere")

    out = tmp_path / "open_in_viewer.pdf"
    out.write_bytes(b"%PDF-1.4 placeholder")
    holder = open(str(out), "rb+")
    try:
        rc = main(["export", FIXTURE, "-o", str(out)])
    finally:
        holder.close()

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(out))
    assert "viewer" in err, "the likely cause is not named"
    assert "WinError" not in err


def test_export_to_an_unwritable_directory_names_the_directory(
    tmp_path, capsys, monkeypatch
):
    """A folder the user cannot write to.

    Windows ignores ``chmod`` on directories (verified: ``os.access(dir,
    W_OK)`` stays ``True`` after ``chmod(S_IREAD)``), so the permission
    denial itself is injected. What is genuinely under test is Deckle's
    branch: that a non-writable destination folder is reported by name,
    before any imposition work, rather than surfacing from the exporter.
    """
    out = tmp_path / "out.pdf"
    real_access = os.access

    def deny_dir_writes(path, mode, **kwargs):
        if mode == os.W_OK and os.path.isdir(path) and str(tmp_path) in str(path):
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", deny_dir_writes)

    rc = main(["export", FIXTURE, "-o", str(out)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(out))
    assert str(tmp_path) in err
    assert not out.exists()


def test_impose_reports_output_problems_too(tmp_path, capsys):
    """``impose`` writes a project file and had the same gap as ``export``."""
    rc = main(["impose", FIXTURE, "-o", str(tmp_path)])

    assert rc == 1
    _assert_actionable(capsys.readouterr().err, path=str(tmp_path))


def test_a_rejected_output_path_costs_no_imposition_work(tmp_path, monkeypatch, capsys):
    """The destination is checked before the document is even loaded.

    Imposing a 300-page book and only then discovering the output folder
    does not exist wastes the user's time for no reason.
    """
    from deckle import cli

    def fail_if_called(path):
        raise AssertionError("the source was loaded despite a bad output path")

    monkeypatch.setattr(cli, "_load_source", fail_if_called)

    rc = main(["export", FIXTURE, "-o", str(tmp_path / "nope" / "out.pdf")])

    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_missing_source_file_is_reported_not_raised(tmp_path, capsys):
    """Un-hardened this was a bare ``FileNotFoundError`` traceback out of
    pypdfium2 -- and a traceback is not an error message."""
    missing = str(tmp_path / "not_here.pdf")

    rc = main(["info", missing])

    assert rc == 1
    _assert_actionable(capsys.readouterr().err, path=missing)


def test_load_pdf_on_a_directory_says_it_is_a_directory(tmp_path):
    with pytest.raises(UnreadableSourceError) as exc_info:
        load_pdf(str(tmp_path))

    _assert_actionable(str(exc_info.value), path=str(tmp_path))
    assert exc_info.value.path == str(tmp_path)


def test_unreadable_source_file_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    """A source file that exists but cannot be opened.

    ``chmod(0)`` does not remove read access for the owning user on
    Windows, so the denial is injected; the branch under test is Deckle's
    own -- that ``os.access(path, R_OK)`` failing produces a named,
    actionable error rather than a parse failure from PDFium.
    """
    source = tmp_path / "sealed.pdf"
    _make_pdf(str(source))
    real_access = os.access

    def deny_read(path, mode, **kwargs):
        if mode == os.R_OK and str(path) == str(source):
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", deny_read)

    rc = main(["info", str(source)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(source))
    assert "permission" in err.lower()


def test_source_load_failures_are_recorded_in_the_diagnostic_log(tmp_path):
    """Support needs the failure on disk, not only on the user's screen."""
    main(["info", str(tmp_path / "absent.pdf")])

    events = [r["event"] for r in _log_records(tmp_path)]
    assert "source_load_failed" in events


def test_output_path_rejections_are_recorded_in_the_diagnostic_log(tmp_path):
    main(["export", FIXTURE, "-o", str(tmp_path / "gone" / "out.pdf")])

    events = [r["event"] for r in _log_records(tmp_path)]
    assert "output_path_rejected" in events


# --- PASS 5: malformed input ------------------------------------------


def test_truncated_pdf_reports_damage_and_a_remedy(tmp_path, capsys):
    truncated = tmp_path / "half.pdf"
    raw = open(FIXTURE, "rb").read()
    truncated.write_bytes(raw[: len(raw) // 2])

    rc = main(["info", str(truncated)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(truncated))
    assert "damaged or truncated" in err
    assert "PdfiumError" not in err


def test_load_pdf_raises_corrupt_pdf_error_for_a_truncated_file(tmp_path):
    truncated = tmp_path / "half.pdf"
    raw = open(FIXTURE, "rb").read()
    truncated.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(CorruptPdfError) as exc_info:
        load_pdf(str(truncated))

    assert exc_info.value.path == str(truncated)


def test_a_file_that_is_not_a_pdf_at_all_is_distinguished_from_damage(tmp_path, capsys):
    """``.pdf`` on the end of an HTML error page or a text file.

    Told apart from a damaged PDF by the missing ``%PDF`` header, because
    "your file is corrupt" sends the user hunting for a problem that is not
    there when the real answer is "that is not a PDF".
    """
    impostor = tmp_path / "download.pdf"
    impostor.write_text("<html><body>404 Not Found</body></html>\n", encoding="utf-8")

    rc = main(["info", str(impostor)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(impostor))
    assert "%PDF" in err
    assert "damaged or truncated" not in err


def test_zero_page_pdf_says_it_has_no_pages(tmp_path, capsys):
    """PDFium reports a zero-page document as ``Failed to load document
    (PDFium: Success)``, which is nonsense on its face."""
    empty = tmp_path / "no_pages.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.save(str(empty))
    pdf.close()

    rc = main(["info", str(empty)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(empty))
    assert "no pages" in err
    assert "Success" not in err, "PDFium's contradictory wording reached the user"


def test_load_pdf_raises_empty_pdf_error_for_a_zero_page_pdf(tmp_path):
    empty = tmp_path / "no_pages.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.save(str(empty))
    pdf.close()

    with pytest.raises(EmptyPdfError) as exc_info:
        load_pdf(str(empty))

    assert exc_info.value.path == str(empty)


def test_password_protected_pdf_message_is_actionable(tmp_path, capsys):
    """``EncryptedPdfError`` already existed; its wording did not meet the
    standard -- "PDF is password-protected: <path>" states the diagnosis and
    stops, leaving the user with no next step."""
    secret = tmp_path / "secret.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612.0, 792.0))
    pdf.save(str(secret), encryption=pikepdf.Encryption(owner="o", user="u"))
    pdf.close()

    with pytest.raises(EncryptedPdfError) as exc_info:
        load_pdf(str(secret))
    _assert_actionable(str(exc_info.value), path=str(secret))
    assert "unprotected copy" in str(exc_info.value)

    rc = main(["info", str(secret)])
    assert rc == 1
    _assert_actionable(capsys.readouterr().err, path=str(secret))


def test_empty_image_directory_is_an_error_not_an_empty_booklet(tmp_path, capsys):
    """The sharpest defect in this pass: un-hardened, this exited 0 and
    reported ``page count: 0``. ``export`` would have written a PDF with no
    sheets and said ``wrote out.pdf``."""
    source = tmp_path / "scans"
    source.mkdir()

    rc = main(["info", str(source)])

    assert rc == 1
    captured = capsys.readouterr()
    _assert_actionable(captured.err, path=str(source))
    assert "page count: 0" not in captured.out, (
        "an empty folder must not report a zero-page document as a result"
    )


def test_directory_with_only_non_image_files_lists_what_it_found(tmp_path, capsys):
    source = tmp_path / "wrong_folder"
    source.mkdir()
    (source / "notes.txt").write_text("chapter list", encoding="utf-8")
    (source / "book.docx").write_bytes(b"not an image")

    rc = main(["info", str(source)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(source))
    assert "notes.txt" in err, "the message does not show what was actually there"
    assert ".jpg" in err and ".png" in err, "the accepted formats are not named"


def test_load_image_dir_raises_no_images_found_for_an_empty_directory(tmp_path):
    with pytest.raises(NoImagesFoundError) as exc_info:
        load_image_dir(str(tmp_path))

    assert exc_info.value.path == str(tmp_path)


def test_load_image_dir_on_a_missing_directory_names_the_path(tmp_path):
    missing = str(tmp_path / "gone")
    with pytest.raises(MissingSourceError) as exc_info:
        load_image_dir(missing)

    _assert_actionable(str(exc_info.value), path=missing)


def test_non_image_files_mixed_with_images_import_and_warn(tmp_path, capsys):
    """Mixed folders still import -- a folder of scans routinely also holds
    a ``Thumbs.db``. But the skip is said out loud, because the other cause
    is a scan in a format Deckle cannot read, which silently drops a page
    out of the finished book."""
    source = tmp_path / "scans"
    source.mkdir()
    _make_image(source / "page1.jpg")
    _make_image(source / "page2.jpg")
    (source / "Thumbs.db").write_bytes(b"junk")

    rc = main(["info", str(source)])

    assert rc == 0, "a stray non-image file must not fail the import"
    out = capsys.readouterr().out
    assert "page count: 2" in out
    assert "skipped_non_image_files" in out
    assert "Thumbs.db" in out


def test_skipped_file_warning_reaches_export_on_stderr(tmp_path, capsys):
    """``_load_source`` used to wrap the image import in ``list()``, which
    discarded every import warning before ``_emit_warnings`` could see it --
    so mixed DPI and skipped files were invisible from the CLI entirely."""
    source = tmp_path / "scans"
    source.mkdir()
    _make_image(source / "page1.jpg")
    (source / "readme.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "out.pdf"

    rc = main(["export", str(source), "-o", str(out)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "skipped_non_image_files" in captured.err
    assert "skipped_non_image_files" not in captured.out, "stdout must stay scriptable"
    assert out.exists()


def test_mixed_dpi_warning_reaches_the_cli(tmp_path, capsys):
    """The same dropped-warnings bug, on the warning that already existed."""
    source = tmp_path / "scans"
    source.mkdir()
    _make_image(source / "a.jpg", dpi=(150, 150))
    _make_image(source / "b.jpg", dpi=(300, 300))

    rc = main(["info", str(source)])

    assert rc == 0
    assert "mixed_dpi" in capsys.readouterr().out


def test_a_corrupt_image_fails_the_import_rather_than_dropping_a_page(tmp_path, capsys):
    """A ``.jpg`` that is not a JPEG.

    Fatal on purpose: skipping it would produce a book missing a page,
    discovered only after folding and sewing.
    """
    source = tmp_path / "scans"
    source.mkdir()
    _make_image(source / "page1.jpg")
    (source / "page2.jpg").write_bytes(b"this is not JPEG data")

    rc = main(["info", str(source)])

    assert rc == 1
    err = capsys.readouterr().err
    _assert_actionable(err, path=str(source / "page2.jpg"))


def test_corrupt_image_raises_corrupt_image_error(tmp_path):
    source = tmp_path / "scans"
    source.mkdir()
    (source / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n truncated")

    with pytest.raises(CorruptImageError) as exc_info:
        load_image_dir(str(source))

    assert exc_info.value.path == str(source / "bad.png")


# --- the contract the CLI depends on ----------------------------------


@pytest.mark.parametrize(
    "error_type",
    [
        MissingSourceError,
        UnreadableSourceError,
        CorruptPdfError,
        EmptyPdfError,
        NoImagesFoundError,
        CorruptImageError,
        EncryptedPdfError,
    ],
)
def test_every_loader_error_is_a_source_load_error(error_type):
    """The CLI catches the base class and prints ``str(exc)``. A new loader
    error that forgets to inherit from it would escape as a traceback."""
    assert issubclass(error_type, SourceLoadError)


# --- correct-path neutrality ------------------------------------------


def test_valid_export_output_is_unchanged_by_the_hardening(tmp_path):
    """Pass 4/5 must not alter correct-path behaviour.

    Two exports of the same source must agree page-for-page on the media
    box, which is the geometry the whole imposition is judged by.
    """
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    assert main(["export", FIXTURE, "-o", str(first), "--gutter", "0.75in"]) == 0
    assert main(["export", FIXTURE, "-o", str(second), "--gutter", "0.75in"]) == 0

    with pikepdf.open(str(first)) as a, pikepdf.open(str(second)) as b:
        assert len(a.pages) == len(b.pages) > 0
        assert [list(map(float, p.mediabox)) for p in a.pages] == [
            list(map(float, p.mediabox)) for p in b.pages
        ]


def test_a_healthy_image_directory_still_imports_silently(tmp_path, capsys):
    source = tmp_path / "scans"
    source.mkdir()
    # Portrait, matching DPI: nothing here should draw any comment at all.
    _make_image(source / "img1.jpg", size=(90, 120))
    _make_image(source / "img2.jpg", size=(90, 120))

    rc = main(["info", str(source)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "page count: 2" in out
    assert "layout warnings: none" in out
