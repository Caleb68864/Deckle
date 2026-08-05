"""The preview's render-to-screen chain.

``preview_view.py`` was the least-covered module in the app at 35%, and it
is the one the README leads with: *the preview rasterises the real exported
PDF, not a redrawing of it*. That claim is worth testing.

The specific shape of bug being hunted here is the one that already shipped
twice this session: a pipeline whose parts each work while the last step
never happens. Thumbnails rendered and were never drawn; arrange edits
applied and were never announced. So these tests follow the pixels all the
way to ``image_label``, rather than stopping at "a worker produced a frame".
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.core.layout import GutterShiftStrategy  # noqa: E402
from deckle.core.models import LayoutSettings, SourcePage, SourceRef  # noqa: E402
from deckle.core.profiles import BUILTIN_PRESETS  # noqa: E402

LETTER = (612.0, 792.0)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(scope="module", autouse=True)
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _settings(**overrides) -> LayoutSettings:
    base = dict(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    base.update(overrides)
    return LayoutSettings(**base)


def _plan(n_pages: int = 4, **overrides):
    pages = [
        SourcePage(
            ref=SourceRef(
                path=FIXTURE, page_index=i % 2, sha256="a" * 64,
                width_pt=612.0, height_pt=792.0,
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n_pages)
    ]
    return GutterShiftStrategy().impose(pages, _settings(**overrides))


def _view(plan=None, *, n_pages: int = 4, **overrides):
    from deckle.app.views.preview_view import PreviewView

    plan = plan if plan is not None else _plan(n_pages, **overrides)
    profile = next(iter(BUILTIN_PRESETS.values()))
    return PreviewView(plan, profile, None, layout_settings=_settings(**overrides))


def _render_current(view):
    """Run the preview's own worker synchronously and hand back the frames.

    The real path spawns a QThread; running ``worker.run()`` directly is the
    same code without the scheduling, which is what lets these tests assert
    on pixels rather than on timing.
    """
    from deckle.app.views.preview_view import PreviewWorker

    sides = ("front", "back") if view._spread else (view.side,)
    worker = PreviewWorker(
        view.plan, view.profile, view.sheet_index, view.side, sides=sides
    )
    worker.run()
    view._worker = worker
    view._on_frame_ready(worker)
    return worker


# -- the pixels reach the screen ----------------------------------------


def test_a_rendered_frame_becomes_a_visible_pixmap():
    """The end of the chain. A frame that renders and never reaches the
    label is exactly the thumbnail bug in a different file."""
    view = _view()

    _render_current(view)

    pixmap = view.image_label.pixmap()
    assert pixmap is not None and not pixmap.isNull(), (
        "the preview rendered a frame and never painted it"
    )
    assert pixmap.width() > 0 and pixmap.height() > 0


def test_the_full_resolution_render_is_kept_as_the_source():
    """Zoom scales a copy. Scaling the source in place would lose detail
    permanently on the first zoom out."""
    view = _view()
    _render_current(view)

    source_before = (view._source_pixmap.width(), view._source_pixmap.height())
    view._set_zoom(0.25)
    view._set_zoom(4.0)

    assert (view._source_pixmap.width(), view._source_pixmap.height()) == source_before


def test_zooming_changes_what_is_displayed_without_re_rendering():
    view = _view()
    _render_current(view)

    view._set_zoom(0.5)
    small = view.image_label.pixmap().width()
    view._set_zoom(2.0)
    large = view.image_label.pixmap().width()

    assert large > small
    assert large == pytest.approx(small * 4, rel=0.05)


# -- superseded renders ---------------------------------------------------


def test_a_superseded_render_never_repaints_over_a_newer_one():
    """Scrubbing sheets quickly left several threads racing, and the last to
    finish won -- not necessarily the one being looked at."""
    view = _view(n_pages=8)
    stale = _render_current(view)
    # cacheKey identifies the underlying pixmap; PySide6 hands back a fresh
    # wrapper object on every call, so `is` would never hold.
    before = view.image_label.pixmap().cacheKey()

    # A newer render supersedes it, then the stale one finally returns.
    view._worker = object()
    view._on_frame_ready(stale)

    assert view.image_label.pixmap().cacheKey() == before, (
        "a superseded render repainted the view"
    )


def test_a_cancelled_worker_is_ignored_even_if_it_is_still_current():
    view = _view()
    worker = _render_current(view)
    before = view.image_label.pixmap().cacheKey()

    worker.cancel.set()
    view._on_frame_ready(worker)

    assert view.image_label.pixmap().cacheKey() == before


# -- spread mode ----------------------------------------------------------


def test_spread_mode_composes_both_faces_side_by_side():
    """Front and back on one canvas, so a sheet can be checked as a sheet."""
    view = _view()
    _render_current(view)
    single_width = view._source_pixmap.width()

    view._set_spread(True)
    _render_current(view)

    assert view._source_pixmap.width() > single_width * 1.8, (
        "the spread is not appreciably wider than one face"
    )


def test_leaving_spread_mode_returns_to_one_face():
    view = _view()
    view._set_spread(True)
    _render_current(view)
    spread_width = view._source_pixmap.width()

    view._set_spread(False)
    _render_current(view)

    assert view._source_pixmap.width() < spread_width


# -- following the document ----------------------------------------------


def test_changing_sheet_index_renders_that_sheet():
    view = _view(n_pages=8)

    view._set_sheet_index(1)
    _render_current(view)

    assert view.sheet_index == 1
    assert view.image_label.pixmap() is not None


def test_on_layout_changed_adopts_the_new_plan_and_settings():
    """The preview must follow the document, not a copy of it taken once."""
    view = _view()
    wider = _plan(n_pages=8, gutter_pt=72.0)

    view.on_layout_changed(wider, _settings(gutter_pt=72.0))

    assert view.plan is wider
    assert view.layout_settings.gutter_pt == 72.0


def test_a_sheet_index_past_the_end_does_not_raise():
    """Deleting pages can leave the index beyond the new plan."""
    view = _view(n_pages=4)
    view._set_sheet_index(99)

    _render_current(view)  # must not raise


# -- warnings -------------------------------------------------------------


def test_clipping_warnings_are_surfaced_on_the_badge():
    """Content outside the printer's imageable area is the warning that
    saves paper, and it has to be visible without opening anything."""
    view = _view(gutter_pt=0.0, margin_top_pt=0.0, margin_bottom_pt=0.0)

    _render_current(view)

    # The fixture is exactly letter-sized with no margins, so it necessarily
    # sits inside the printer's non-printable border.
    assert view.warning_label.text() != "", (
        "a clipped sheet produced no visible warning"
    )


# -- jumping to the ends --------------------------------------------------


def test_first_and_last_buttons_jump_to_the_ends():
    """Stepping a spinbox to the end of a 67-sheet book is 66 clicks, and
    the ends are what a binder checks: the cover and the final leaf."""
    view = _view(n_pages=12)
    last_index = len(view.plan.sheets) - 1
    assert last_index > 0, "the fixture must have several sheets"

    view.go_to_last_sheet()
    assert view.sheet_index == last_index

    view.go_to_first_sheet()
    assert view.sheet_index == 0


def test_each_end_button_disables_at_its_own_end():
    """A control that responds to a click by doing nothing is
    indistinguishable from one that is broken."""
    view = _view(n_pages=12)

    assert view.first_button.isEnabled() is False, "already at the first sheet"
    assert view.last_button.isEnabled() is True

    view.go_to_last_sheet()
    assert view.first_button.isEnabled() is True
    assert view.last_button.isEnabled() is False


def test_the_readout_says_how_many_sheets_there_are():
    view = _view(n_pages=12)

    assert view.sheet_count_label.text() == f"of {len(view.plan.sheets)}"


def test_the_ends_follow_a_changed_plan():
    """Re-imposing changes the sheet count; the buttons and readout must
    not go on describing the old document."""
    view = _view(n_pages=4)
    view.on_layout_changed(_plan(n_pages=24), _settings())

    assert view.sheet_count_label.text() == f"of {len(view.plan.sheets)}"
    view.go_to_last_sheet()
    assert view.sheet_index == len(view.plan.sheets) - 1


def test_an_empty_plan_lands_on_zero_rather_than_minus_one():
    from deckle.core.layout import GutterShiftStrategy

    empty = GutterShiftStrategy().impose([], _settings())
    view = _view(plan=empty)

    view.go_to_last_sheet()

    assert view.sheet_index == 0
    assert "no sheets" in view.sheet_count_label.text()
