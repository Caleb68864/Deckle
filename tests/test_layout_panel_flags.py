"""The two ``LayoutPanel`` flags, pinned by behaviour before they were declared.

``_document_loaded`` and ``_syncing_mode`` used to be read through
``getattr`` defaults rather than initialised (roadmap M7). Each default
was doing work, so these pin what it did before the flags became
ordinary attributes:

- **Before the window says whether a document is open**, the schedule
  button follows the project's own pages. Once
  :meth:`LayoutPanel.set_document_loaded` has answered, that answer wins.
- **Selecting the mode tab from code never writes the scheme back.** The
  constructor and :meth:`refresh_from_project` both move the tab to match
  ``fold_scheme``; neither may push an undo entry.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from deckle.app.state import AppState  # noqa: E402
from deckle.core.models import (  # noqa: E402
    LayoutSettings, Project, SourcePage, SourceRef,
)


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _project(n_pages: int, fold_scheme: str = "none") -> Project:
    pages = [
        SourcePage(
            ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0, skipped=False,
        )
        for i in range(n_pages)
    ]
    return Project(
        pages=pages,
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=36.0,
                              binding_edge="left", fold_scheme=fold_scheme,
                              sheets_per_signature=1),
        printer=None,
    )


def _panel(project: Project):
    from deckle.app.views.layout_panel import LayoutPanel

    return LayoutPanel(AppState(project))


def test_before_the_window_answers_a_project_with_pages_can_be_scheduled(qt_app):
    panel = _panel(_project(8))

    assert panel.save_schedule_button.isEnabled() is True


def test_before_the_window_answers_an_empty_project_cannot_be_scheduled(qt_app):
    panel = _panel(_project(0))

    assert panel.save_schedule_button.isEnabled() is False


def test_the_windows_answer_outranks_the_pages(qt_app):
    """Told "no document", a project that still has pages is not
    schedulable; told "a document", an empty one is."""
    with_pages = _panel(_project(8))
    with_pages.set_document_loaded(False)
    assert with_pages.save_schedule_button.isEnabled() is False

    empty = _panel(_project(0))
    empty.set_document_loaded(True)
    assert empty.save_schedule_button.isEnabled() is True


@pytest.mark.parametrize("scheme", ["none", "folio"])
def test_constructing_the_panel_selects_the_tab_without_an_undo_entry(qt_app, scheme):
    panel = _panel(_project(8, scheme))

    wanted = (
        panel._signature_tab_index if scheme == "folio" else panel._single_tab_index
    )
    assert panel.tabs.currentIndex() == wanted
    assert panel.state.project.layout.fold_scheme == scheme
    assert panel.state.can_undo is False


def test_refreshing_onto_the_other_scheme_moves_the_tab_and_writes_nothing(qt_app):
    panel = _panel(_project(8, "none"))
    # Put a different layout under the panel, as opening a project does
    # (the same convention as ``test_layout_panel_refresh._load``).
    panel.state._project = _project(8, "folio")

    panel.refresh_from_project()

    assert panel.tabs.currentIndex() == panel._signature_tab_index
    assert panel.state.project.layout.fold_scheme == "folio"
    assert panel.state.can_undo is False
    # And a person's click on the tab still does write it.
    panel.tabs.setCurrentIndex(panel._single_tab_index)
    assert panel.state.project.layout.fold_scheme == "none"
    assert panel.state.can_undo is True
