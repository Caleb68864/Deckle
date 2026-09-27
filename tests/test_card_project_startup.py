"""Startup project choice and card workspace sizing."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

import deckle.app.main as app_main
from deckle.app.state import AppState
from deckle.core.models import (
    CardAsset, CardDeck, CardEntry, CardFace, CardSettings, LayoutSettings,
    Project, SourcePage, SourceRef,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_startup_choice_can_create_card_deck_and_widen_sidebar(app, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(app_main.MainWindow, "refresh_printers",
                        lambda self, **_kwargs: self._apply_printers([]))
    clicked = {}
    add_button = QMessageBox.addButton

    def record_button(box, text, role):
        button = add_button(box, text, role)
        if text == "Trading card deck":
            clicked["card"] = button
        return button

    monkeypatch.setattr(QMessageBox, "addButton", record_button)
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda _box: clicked.get("card"))

    window = app_main.MainWindow()
    try:
        assert window.state.project.kind == "book"
        window.window.show()
        app.processEvents()
        window.offer_project_type_choice()
        app.processEvents()
        assert window.state.project.kind == "cards"
        assert window.controls_scroll.minimumWidth() >= 520
        assert window.window.minimumWidth() >= 1040
        assert window.splitter.sizes()[0] >= 520
        assert window.output_splitter.isHidden()
        assert window.controls_scroll.width() >= 900

        assets = []
        for asset_id in ("front", "back"):
            ref = SourceRef(f"/{asset_id}.pdf", 0, asset_id[0] * 64, 100, 140)
            assets.append(CardAsset(asset_id, asset_id, f"/{asset_id}.png", "c" * 64,
                                    SourcePage(ref, 0, False)))
        deck = CardDeck(tuple(assets),
                        (CardEntry("one", CardFace("front"), CardFace("back")),),
                        CardSettings(), "individual")
        state = AppState(Project([], LayoutSettings((612, 792), 18, "left"),
                                 None, "cards", deck))
        window.state = state
        window.cards_view.state = state
        window.card_layout_panel.state = state
        window._on_pages_changed()
        assert not window.output_splitter.isHidden()
    finally:
        window.window.close()
