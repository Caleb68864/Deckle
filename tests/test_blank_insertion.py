"""Inserting a blank page: where it goes, and how it is labelled.

Blanks are the one page in a document with no source file behind them, so
they are also the one page that looks identical to a page whose thumbnail
has not rendered yet. Naming them is not decoration.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.state import (  # noqa: E402
    AppState,
    insert_blank,
    make_blank_page,
)
from deckle.core.models import BLANK_SOURCE_PATH, is_blank_page  # noqa: E402
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef  # noqa: E402

LETTER = (612.0, 792.0)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _project(n: int) -> Project:
    pages = [
        SourcePage(
            ref=SourceRef(
                path="book.pdf", page_index=i, sha256="a" * 64,
                width_pt=400.0, height_pt=600.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]
    return Project(
        pages=pages,
        layout=LayoutSettings(paper=LETTER, gutter_pt=0.0, binding_edge="left"),
        printer=None,
    )


# -- where it goes -------------------------------------------------------


def test_choices_describe_positions_by_the_pages_they_fall_between():
    """"After the title page" is how someone reads a stack. "Index 1" is not."""
    from deckle.app.views.arrange_view import blank_insert_choices

    choices = blank_insert_choices(3)

    assert [label for label, _ in choices] == [
        "Before page 1",
        "Between pages 1 and 2",
        "Between pages 2 and 3",
        "After page 3 (at the end)",
    ]
    assert [index for _, index in choices] == [0, 1, 2, 3]


def test_an_empty_document_still_offers_one_position():
    """A control that can present an empty list will eventually be handed
    one."""
    from deckle.app.views.arrange_view import blank_insert_choices

    assert blank_insert_choices(0) == [("As the only page", 0)]


@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_the_blank_lands_exactly_where_it_was_asked_for(position):
    project = insert_blank(_project(3), position)

    assert len(project.pages) == 4
    assert is_blank_page(project.pages[position])
    # And every real page keeps its order around it.
    real = [p.ref.page_index for p in project.pages if not is_blank_page(p)]
    assert real == [0, 1, 2]


def test_cancelling_inserts_nothing():
    """An accidental blank in a 266-page document is tedious to find."""
    from deckle.app.views.arrange_view import ArrangeView

    state = AppState(_project(3))
    view = ArrangeView(state, choose_blank_position=lambda choices: None)

    view.insert_blank_at_choice()

    assert len(state.project.pages) == 3


def test_choosing_a_position_inserts_there():
    from deckle.app.views.arrange_view import ArrangeView

    state = AppState(_project(3))
    seen = {}

    def choose(choices):
        seen["choices"] = choices
        return 2  # between pages 2 and 3

    view = ArrangeView(state, choose_blank_position=choose)
    view.insert_blank_at_choice()

    assert len(state.project.pages) == 4
    assert is_blank_page(state.project.pages[2])
    assert seen["choices"][0][0] == "Before page 1", "the picker got real choices"


# -- how it is labelled --------------------------------------------------


def test_a_blank_is_named_in_the_page_list():
    """A blank thumbnail and a not-yet-rendered thumbnail look identical.
    Only one of them is deliberate."""
    from deckle.app.views.arrange_view import ArrangeView

    state = AppState(insert_blank(_project(3), 1))
    view = ArrangeView(state, choose_blank_position=lambda choices: None)
    view.refresh()

    labels = [
        view.list_widget.item(i).text() for i in range(view.list_widget.count())
    ]
    assert labels[0] == "page 1"
    assert labels[1] == "page 2 _blank"
    assert labels[2] == "page 3"


def test_a_skipped_blank_says_both_things():
    from dataclasses import replace

    from deckle.app.views.arrange_view import ArrangeView

    project = insert_blank(_project(2), 0)
    project = replace(
        project,
        pages=[replace(project.pages[0], skipped=True)] + list(project.pages[1:]),
    )
    view = ArrangeView(AppState(project), choose_blank_position=lambda c: None)
    view.refresh()

    assert view.list_widget.item(0).text() == "page 1 _blank (skipped)"


# -- the marker itself ---------------------------------------------------


def test_is_blank_page_distinguishes_blanks_from_imported_pages():
    blank = make_blank_page(LETTER)
    real = _project(1).pages[0]

    assert is_blank_page(blank) is True
    assert is_blank_page(real) is False
    assert blank.ref.path == BLANK_SOURCE_PATH
    assert blank.ref.page_index == -1, "a blank indexes no file"


def test_a_blank_takes_the_projects_paper_size():
    """So it imposes like every other page rather than as a stray size."""
    blank = make_blank_page((792.0, 612.0))

    assert (blank.ref.width_pt, blank.ref.height_pt) == (792.0, 612.0)


# -- a missing source must not raise inside the worker thread ------------


def test_an_unreadable_source_degrades_to_placeholders(tmp_path, monkeypatch):
    """Found while writing the tests above: the fixture's fake source path
    made the worker raise, and the traceback surfaced on the console.

    Qt has nowhere to deliver an exception raised inside a QThread, so it
    prints a trace the user cannot act on and the grid stays empty --
    indistinguishable from thumbnails that are merely slow. The commonest
    cause is mundane: reopening a project whose source PDF has moved.
    """
    from deckle.app.views.arrange_view import ThumbnailWorker
    from deckle.core import diagnostics

    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path))
    diagnostics.reset_for_tests()

    worker = ThumbnailWorker(_project(3).pages, 0, 3)
    worker.run()  # must not raise: the source path does not exist

    assert worker.failed is True
    assert worker.rendered == [], "no partial results from a failed fetch"

    import json

    log = tmp_path / "diagnostics.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text().splitlines() if line]
    assert "thumbnail_render_failed" in events

    diagnostics.reset_for_tests()


def test_a_successful_fetch_is_not_marked_failed(tmp_path):
    """The flag must mean something -- always-true is the same as absent."""
    from deckle.app.views.arrange_view import ThumbnailWorker
    from deckle.core.loader import load_pdf

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    worker = ThumbnailWorker(load_pdf(fixture), 0, 2)
    worker.run()

    assert worker.failed is False
    assert worker.rendered, "a readable source should produce thumbnails"


# -- an inserted blank must behave like a blank everywhere ---------------


@pytest.fixture(scope="module")
def narrow_book(tmp_path_factory):
    """A REAL pdf whose pages are narrower than the paper.

    Narrower matters: a blank is sized to the paper, so on a document that
    is already letter-sized the blank is invisible in the arithmetic. Real
    books are not letter-sized. And it has to exist on disk -- the first
    version of these tests used a fake path, so every page failed to open
    and the tests failed for a reason that had nothing to do with blanks.
    """
    import pikepdf

    path = tmp_path_factory.mktemp("blank") / "book.pdf"
    pdf = pikepdf.Pdf.new()
    for _ in range(8):
        pdf.add_blank_page(page_size=(506.88, 719.28))
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _narrow_project(path: str, n: int) -> Project:
    pages = [
        SourcePage(
            ref=SourceRef(
                path=path, page_index=i, sha256="a" * 64,
                width_pt=506.88, height_pt=719.28,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n)
    ]
    return Project(
        pages=pages,
        layout=LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left"),
        printer=None,
    )


def test_a_blank_does_not_resize_the_rest_of_the_book(narrow_book):
    """The blank is sized to the PAPER, so measuring it as an ordinary page
    made it the widest thing in the document. One inserted blank shrank
    every real page by 15%."""
    from deckle.core.layout import GutterShiftStrategy

    project = _narrow_project(narrow_book, 8)
    settings = project.layout

    before = GutterShiftStrategy().impose(project.pages, settings)
    after = GutterShiftStrategy().impose(insert_blank(project, 1).pages, settings)

    def first_scale(plan):
        return plan.sheets[0].front.pages[0].placement.scale_x

    assert first_scale(after) == pytest.approx(first_scale(before)), (
        "inserting a blank changed the scale of the whole document"
    )


def test_exporting_a_document_with_a_blank_succeeds(narrow_book, tmp_path):
    """It raised FileNotFoundError on the blank's empty path -- Save PDF
    failed outright the moment a blank existed."""
    import pikepdf

    from deckle.core.export import export
    from deckle.core.layout import GutterShiftStrategy

    project = insert_blank(_narrow_project(narrow_book, 4), 1)
    plan = GutterShiftStrategy().impose(project.pages, project.layout)
    out = tmp_path / "with-blank.pdf"

    export(plan, str(out))

    with pikepdf.open(str(out)) as pdf:
        contents = []
        for page in pdf.pages:
            raw = page.get("/Contents")
            if raw is None:
                contents.append(b"")
            elif isinstance(raw, pikepdf.Array):
                contents.append(b"".join(bytes(s.read_bytes()) for s in raw))
            else:
                contents.append(bytes(raw.read_bytes()))

    # The blank side carries no placement at all; its neighbours do.
    placements = [c.count(b" Do") for c in contents]
    assert placements[0] == 1
    assert placements[1] == 0, "the blank should place nothing"
    assert placements[2] == 1


def test_a_blank_gets_a_white_thumbnail_rather_than_failing_the_window(narrow_book):
    """Opening the blank's empty path raised, which failed the WHOLE fetch:
    one blank left every thumbnail beside it missing too."""
    from deckle.core.render import thumbnails

    project = insert_blank(_narrow_project(narrow_book, 3), 1)

    rendered = thumbnails(project.pages, start=0, count=4, dpi=24)

    assert len(rendered) == 4, "every page in the window should render"
    blank = rendered[1]
    assert blank.width > 0 and blank.height > 0, (
        "a zero-size page means 'nothing to show', which draws as no "
        "thumbnail -- indistinguishable from one still loading"
    )
    assert set(blank.rgba) == {255}, "a blank sheet should look blank, not black"
