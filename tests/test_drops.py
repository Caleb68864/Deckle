"""What a drop onto the window means, decided without a drag.

Nothing in Deckle set ``acceptDrops`` at all, which for an imposition tool
is a missing front door: dragging a PDF onto the window is how most people
expect to start. The decision -- is this a project to open, a source to
import, or nothing Deckle takes? -- is a pure function so it can be checked
against paths that do not exist, on a machine with no display.

The drop's *consequence* on a document that already has pages is
``tests/test_gui_shell.py``'s business, because that is a question with a
person in it.
"""

from __future__ import annotations

import os

from deckle.app.exporting import suggested_pass_export_name
from deckle.app.main import DROP_REJECTED_MESSAGE, classify_drop


def _never_a_directory(_path: str) -> bool:
    return False


def _always_a_directory(_path: str) -> bool:
    return True


def test_a_dropped_pdf_is_a_source_to_import():
    drop = classify_drop(["/books/traveller.pdf"], is_dir=_never_a_directory)
    assert drop is not None
    assert drop.kind == "source"
    assert drop.path == "/books/traveller.pdf"


def test_a_dropped_project_is_opened_not_imported():
    """A ``.deckle`` is a job, not a source. Importing one would replace the
    document with nothing and lose the layout it describes."""
    drop = classify_drop(["/books/traveller.deckle"], is_dir=_never_a_directory)
    assert drop is not None
    assert drop.kind == "project"


def test_the_suffix_check_is_case_insensitive():
    """Windows and scanners both hand back ``.PDF``."""
    assert classify_drop(["/scans/BOOK.PDF"], is_dir=_never_a_directory).kind == "source"
    assert classify_drop(["/jobs/JOB.DECKLE"], is_dir=_never_a_directory).kind == "project"


def test_a_dropped_folder_is_a_source_whatever_it_is_called():
    """``load_image_dir`` is what decides whether a folder holds images, and
    it already says so in words a person can act on -- guessing from the
    folder's name here would refuse a perfectly good scan directory."""
    drop = classify_drop(["/scans/chapter-3"], is_dir=_always_a_directory)
    assert drop is not None
    assert drop.kind == "source"


def test_anything_else_is_refused():
    """A .docx, a .txt, a URL dragged out of a browser."""
    assert classify_drop(["/notes/plan.docx"], is_dir=_never_a_directory) is None
    assert classify_drop([], is_dir=_never_a_directory) is None


def test_two_files_at_once_are_refused_rather_than_guessed_at():
    """Two PDFs look like one gesture and are two imports, which the import
    view runs one background thread at a time. A queue that silently picks
    an order is worse than a refusal that names the rule."""
    assert (
        classify_drop(
            ["/books/one.pdf", "/books/two.pdf"], is_dir=_never_a_directory
        )
        is None
    )


def test_the_refusal_says_what_deckle_does_take():
    for word in ("PDF", "images", ".deckle"):
        assert word in DROP_REJECTED_MESSAGE


def test_the_default_directory_test_is_the_real_one():
    """The injected ``is_dir`` is for tests; the default has to be the
    filesystem, or a real dropped folder would be refused."""
    here = os.path.dirname(os.path.abspath(__file__))
    drop = classify_drop([here])
    assert drop is not None and drop.kind == "source"


def test_a_pass_export_names_the_side_in_the_file():
    """The whole point of the feature is handing two files to somebody who
    has never seen Deckle, and printing the back pass first ruins the
    stack. The two files must not be indistinguishable."""
    assert suggested_pass_export_name("book-deckle.pdf", "front") == (
        "book-deckle-front.pdf"
    )
    assert suggested_pass_export_name("book-deckle.pdf", "back") == (
        "book-deckle-back.pdf"
    )


def test_a_pass_export_name_survives_a_name_with_no_extension():
    assert suggested_pass_export_name("book", "front") == "book-front.pdf"
