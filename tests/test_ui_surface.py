"""SS-11 UI-surface acceptance tests: binding mutators, the binding readout,
per-cell preview guides, folio recompute, the strategy factory, and the
"selecting a signature enumerates no printers" guard.

Everything here runs headless. The Qt-free helpers are imported and called
directly; the one test that needs a real ``PrintDialog`` builds the widget
but never shows it, and drives it through the injectable callables the
dialog was written against -- the same boundary tests/test_print_dialog.py
uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deckle.app.views import layout_panel, preview_view
from deckle.core.layout import (
    GutterShiftStrategy,
    SaddleStitchStrategy,
    content_box_rect_pt,
)
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Project,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)
from deckle.core.profiles import PrinterProfile

LETTER_PORTRAIT = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)


# -- fixtures / builders --------------------------------------------------


def _ref(page_index: int, width_pt: float = 396.0, height_pt: float = 612.0) -> SourceRef:
    return SourceRef(
        path="src.pdf",
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _page(page_index: int, **ref_overrides) -> SourcePage:
    return SourcePage(ref=_ref(page_index, **ref_overrides), rotate_deg=0, skipped=False)


def _output_page(page_index: int | None = 0, is_filler: bool = False) -> OutputPage:
    return OutputPage(
        source_ref=None if page_index is None else _ref(page_index),
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=is_filler,
    )


def _settings(**overrides) -> LayoutSettings:
    base = dict(paper=LETTER_PORTRAIT, gutter_pt=36.0, binding_edge="left")
    base.update(overrides)
    return LayoutSettings(**base)


def _project(n_pages: int, **layout_overrides) -> Project:
    return Project(
        pages=[_page(i) for i in range(n_pages)],
        layout=_settings(**layout_overrides),
        printer=None,
    )


# -- 1. binding mutators are pure and return new Projects -----------------


def test_binding_mutators_return_new_projects():
    """Each of the five binding-settings mutators returns a NEW ``Project``
    carrying the new value, and leaves the original untouched."""
    cases = [
        (layout_panel.set_fold_scheme, "fold_scheme", "folio", "none"),
        (layout_panel.set_sheets_per_signature, "sheets_per_signature", 8, 4),
        (layout_panel.set_blank_mode, "blank_mode", "balanced", "end"),
        (layout_panel.set_sewing_stations, "sewing_stations", 5, 3),
        (layout_panel.set_paper_thickness_pt, "paper_thickness_pt", 0.27, 0.0),
    ]
    for mutator, field_name, new_value, original_value in cases:
        project = _project(4)
        assert getattr(project.layout, field_name) == original_value, (
            f"precondition: {field_name} should start at {original_value!r}"
        )

        updated = mutator(project, new_value)

        assert updated is not project, f"{mutator.__name__} returned the same Project object"
        assert updated.layout is not project.layout, (
            f"{mutator.__name__} returned the same LayoutSettings object"
        )
        assert getattr(updated.layout, field_name) == new_value
        # Purity: the input Project must be entirely unchanged.
        assert getattr(project.layout, field_name) == original_value, (
            f"{mutator.__name__} mutated its input Project in place"
        )
        # ... and nothing else on the layout moved.
        for changed_field in (
            "gutter_pt",
            "binding_edge",
            "paper",
            "margin_top_pt",
            "slack_to",
        ):
            assert getattr(updated.layout, changed_field) == getattr(
                project.layout, changed_field
            ), f"{mutator.__name__} also changed {changed_field}"


# -- 2. the binding readout ------------------------------------------------


def test_binding_readout_names_signatures_sheets_and_blanks():
    """A 266-page folio book is 17 signatures / 67 sheets / 2 blanks, and the
    readout names all three numbers."""
    plan = layout_panel.recompute_plan(
        _project(266, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    # Pin the arithmetic first, so a readout assertion can never pass against
    # a differently-shaped plan.
    assert len(plan.signatures) == 17
    assert len(plan.sheets) == 67
    assert sum(sig.blank_count for sig in plan.signatures) == 2

    readout = layout_panel.binding_readout_str(plan)

    assert "17" in readout
    assert "67" in readout
    assert "2" in readout
    assert "signature" in readout
    assert "sheet" in readout
    assert "blank" in readout


def test_binding_readout_on_a_plan_with_no_signatures():
    """A ``GutterShiftStrategy`` plan carries ``signatures == ()``. The
    readout must treat that as normal: a non-empty string, no exception."""
    plan = layout_panel.recompute_plan(_project(6))
    assert plan.signatures == ()

    readout = layout_panel.binding_readout_str(plan)

    assert isinstance(readout, str)
    assert readout.strip() != ""
    # It still reports the real sheet count rather than silently degrading.
    assert str(len(plan.sheets)) in readout


def test_binding_readout_is_derived_from_the_plan_not_the_settings():
    """The readout reads off the ``SheetPlan``, so two plans with different
    signature counts must not produce the same string."""
    small = layout_panel.recompute_plan(
        _project(8, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    large = layout_panel.recompute_plan(
        _project(266, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    assert len(small.signatures) != len(large.signatures)
    assert layout_panel.binding_readout_str(small) != layout_panel.binding_readout_str(large)


# -- 3. one guide per side under fold_scheme="none" ------------------------


def test_non_folio_side_produces_exactly_one_guide():
    """Under ``fold_scheme="none"`` a side yields exactly one content-box
    guide, identical to ``content_box_rect_pt(settings, is_recto=...)``.
    MVP behaviour, unchanged."""
    settings = _settings(fold_scheme="none")

    for is_recto in (True, False):
        side = Side(pages=(_output_page(0),))
        guides = preview_view.content_box_guides_for_side(
            settings, side, is_recto=is_recto
        )

        assert len(guides) == 1
        rect, page = guides[0]
        assert rect == content_box_rect_pt(settings, is_recto=is_recto)
        assert page is side.pages[0]

    # Even when a "none" side somehow holds two pages, the non-folio path
    # still draws a single whole-sheet guide -- the guard is on
    # ``fold_scheme``, not merely on the page count.
    two_page_side = Side(pages=(_output_page(0), _output_page(1)))
    guides = preview_view.content_box_guides_for_side(
        settings, two_page_side, is_recto=True
    )
    assert len(guides) == 1
    assert guides[0][0] == content_box_rect_pt(settings, is_recto=True)


def test_non_folio_side_guide_is_the_whole_sheet_not_a_folio_cell():
    """Contrast case: the same side under ``"folio"`` splits into two cells,
    which proves the single "none" guide above is a real dispatch and not a
    constant."""
    side = Side(pages=(_output_page(0), _output_page(1)))
    none_guides = preview_view.content_box_guides_for_side(
        _settings(paper=LETTER_LANDSCAPE, fold_scheme="none"), side, is_recto=True
    )
    folio_guides = preview_view.content_box_guides_for_side(
        _settings(paper=LETTER_LANDSCAPE, fold_scheme="folio"), side, is_recto=True
    )
    assert len(none_guides) == 1
    assert len(folio_guides) == 2
    # The single "none" guide spans the fold; neither folio cell does.
    (nx0, _ny0, nx1, _ny1), _ = none_guides[0]
    assert nx0 < 396.0 < nx1
    for (fx0, _fy0, fx1, _fy1), _page in folio_guides:
        assert not (fx0 < 396.0 < fx1)


# -- 4. recompute_plan under folio yields signatures -----------------------


def test_recompute_plan_under_folio_yields_signatures():
    """``recompute_plan`` on a folio project returns a plan with a populated
    ``signatures`` tuple; under ``"none"`` it returns ``signatures == ()``."""
    folio_plan = layout_panel.recompute_plan(
        _project(20, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    assert folio_plan.signatures != ()
    assert len(folio_plan.signatures) >= 1
    # The signatures actually describe this plan's sheets, rather than being
    # an unrelated non-empty tuple.
    covered = [i for sig in folio_plan.signatures for i in sig.sheet_indices]
    assert sorted(covered) == list(range(len(folio_plan.sheets)))
    assert [sig.index for sig in folio_plan.signatures] == list(
        range(len(folio_plan.signatures))
    )

    none_plan = layout_panel.recompute_plan(_project(20))
    assert none_plan.signatures == ()
    assert none_plan.sheets, "the 'none' plan should still produce sheets"


# -- 5. selecting a signature enumerates no printers -----------------------
#
# docs/decisions.md -- "Printer enumeration blocked the UI thread on launch":
# QPrinterInfo.availablePrinters() blocks per unreachable network printer, and
# once turned a 7-second suite into an 81-minute one and hung Deckle on launch.
# The signature selector is plan arithmetic and must never reach that code.


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class _StubBackend:
    def __init__(self, profile):
        self.profile = profile

    def submit(self, plan, sheets, printer_name, copies, dpi):
        from deckle.core.printing import PrintResult

        return PrintResult(submitted=len(sheets), job_id=None, error=None)


@dataclass
class _StubSession:
    """Mimics PrintSession's public surface; finishes after one advance."""

    plan: SheetPlan
    profile: PrinterProfile
    backend: object
    test_first: bool = False
    printer_name: str = ""
    sheets: tuple[int, ...] | None = None
    _finished: bool = False
    _pass_index: int = 0
    seen: list = field(default_factory=list)

    def start(self) -> None:
        self.seen.append(("start", self.sheets))

    def confirm_test_sheet(self) -> None:
        pass

    def advance(self) -> None:
        self._finished = True

    def resume(self, sheets_completed: int) -> None:
        pass

    @classmethod
    def load(cls, plan, profile, backend, session_id):
        return cls(plan=plan, profile=profile, backend=backend)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def last_error(self):
        return None

    @property
    def reload_instruction(self):
        return None

    @property
    def state(self) -> dict:
        return {"pass_index": self._pass_index, "test_sheet_pending": False}

    @staticmethod
    def list_resumable():
        return []


def _folio_plan() -> SheetPlan:
    """A real multi-signature plan, so the selector has something to select."""
    plan = layout_panel.recompute_plan(
        _project(40, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    assert len(plan.signatures) >= 2, "fixture must offer more than one signature"
    return plan


def _dialog(plan, **kwargs):
    from deckle.app.views.print_dialog import PrintDialog

    defaults = dict(
        profile_loader=lambda name: (_ for _ in ()).throw(FileNotFoundError(name)),
        session_cls=_StubSession,
        backend_cls=_StubBackend,
        resumable_lister=lambda: [],
        confirm_reload=lambda instruction: None,
        confirm_test_sheet=lambda: True,
        show_offline_error=lambda printer, error: None,
    )
    defaults.update(kwargs)
    return PrintDialog(plan, **defaults)


def test_signature_selection_enumerates_no_printers(qapp, monkeypatch):
    from deckle.app.views import print_dialog as print_dialog_module

    calls: list[str] = []

    def _spy() -> list[str]:
        calls.append("availablePrinters")
        return ["Spy Printer"]

    monkeypatch.setattr(print_dialog_module, "_available_printer_names", _spy)

    plan = _folio_plan()
    dialog = _dialog(plan, printer_names=["Printer A", "Printer B"])

    # The caller supplied the cached list (main.py's background
    # _PrinterQueryWorker does this), so construction must not enumerate.
    assert calls == [], "constructing the dialog with a cached printer list enumerated printers"

    # Now exercise the new control across every signature, and back to "All".
    for index in range(dialog.signature_combo.count()):
        dialog.signature_combo.setCurrentIndex(index)
        assert dialog.signature_combo.currentIndex() == index
    dialog.signature_combo.setCurrentIndex(0)

    assert calls == [], (
        "changing the signature selection enumerated printers -- this is the "
        "UI-thread hang from docs/decisions.md"
    )

    # Printing the selected subset must not enumerate either.
    dialog.signature_combo.setCurrentIndex(1)
    dialog.start_print()
    assert calls == [], "starting a print of one signature enumerated printers"

    # The spy is genuinely wired to the enumeration entry point: with no
    # cached list, the dialog does reach it. Without this the assertions
    # above would pass even if the patch had missed its target.
    _dialog(plan, printer_names=None)
    assert calls == ["availablePrinters"], (
        "the spy was not the real enumeration entry point"
    )


def test_signature_selection_carries_the_signature_sheet_indices(qapp):
    """Companion to the guard above: selecting signature k really does hand
    that signature's sheets to the session, so the zero-enumeration test is
    exercising a working control rather than a dead one."""
    plan = _folio_plan()
    dialog = _dialog(plan, printer_names=["Printer A"])

    assert dialog.signature_combo.count() == len(plan.signatures) + 1
    assert dialog.signature_combo.itemData(0) is None

    for k, signature in enumerate(plan.signatures):
        dialog.signature_combo.setCurrentIndex(k + 1)
        dialog.start_print()
        assert tuple(dialog._session.sheets) == tuple(signature.sheet_indices)

    dialog.signature_combo.setCurrentIndex(0)
    dialog.start_print()
    assert dialog._session.sheets is None


# -- 6. the strategy factory dispatches on fold_scheme ---------------------


def test_strategy_for_dispatches_on_fold_scheme():
    """The imposition-strategy factory returns ``SaddleStitchStrategy`` for
    ``fold_scheme="folio"`` and ``GutterShiftStrategy`` otherwise."""
    from deckle.cli import _strategy_for

    folio = _strategy_for(_settings(fold_scheme="folio"))
    assert isinstance(folio, SaddleStitchStrategy)
    assert not isinstance(folio, GutterShiftStrategy)

    default = _strategy_for(_settings())
    assert default.__class__ is GutterShiftStrategy
    assert _settings().fold_scheme == "none", "the committed default is 'none'"

    explicit_none = _strategy_for(_settings(fold_scheme="none"))
    assert explicit_none.__class__ is GutterShiftStrategy


def test_strategy_for_dispatches_on_fold_scheme_through_recompute_plan():
    """The app-layer path (``layout_panel.recompute_plan``) makes the same
    dispatch: folio plans come out saddle-stitched (signatures, two pages per
    side), "none" plans come out gutter-shifted (one page per side)."""
    folio_plan = layout_panel.recompute_plan(
        _project(20, paper=LETTER_LANDSCAPE, gutter_pt=0.0, fold_scheme="folio")
    )
    none_plan = layout_panel.recompute_plan(_project(20))

    assert folio_plan.signatures != ()
    assert len(folio_plan.sheets[0].front.pages) == 2
    assert none_plan.signatures == ()
    assert len(none_plan.sheets[0].front.pages) == 1
