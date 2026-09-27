import dataclasses
import json

from deckle.app import state as app_state
from deckle.app.state import AppState, unsaved_autosave_offers
from deckle.core.models import CardDeck, CardSettings, LayoutSettings, Project
from deckle.core.project_io import load_project


class Timer:
    def __init__(self, *args, **kwargs): pass
    def start(self): pass
    def cancel(self): pass


def empty_card_project():
    return Project([], LayoutSettings((612, 792), 18, "left"), None,
                   "cards", CardDeck((), (), CardSettings()))


def test_empty_card_decks_have_distinct_stable_recovery_files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_state, "data_dir", lambda name: tmp_path / name)
    first = AppState(empty_card_project(), timer_factory=Timer)
    second = AppState(empty_card_project(), timer_factory=Timer)
    assert first.autosave_path and second.autosave_path
    assert first.autosave_path != second.autosave_path

    first.mark_unsaved()
    first.flush_autosave()
    initial_path = first.autosave_path
    first.mutate(lambda project: dataclasses.replace(
        project, card_deck=dataclasses.replace(
            project.card_deck,
            settings=dataclasses.replace(project.card_deck.settings, bleed_pt=4.0),
        ),
    ))
    first.flush_autosave()
    assert first.autosave_path == initial_path
    assert len(unsaved_autosave_offers()) == 1

    recovered_project = load_project(initial_path, check_sources=False)
    recovered = AppState(recovered_project, recovery_path=initial_path, timer_factory=Timer)
    recovered.mark_unsaved()
    assert recovered.dirty
    recovered.mutate(lambda project: dataclasses.replace(
        project, card_deck=dataclasses.replace(
            project.card_deck,
            settings=dataclasses.replace(project.card_deck.settings, border_width_pt=8.0),
        ),
    ))
    recovered.flush_autosave()
    assert recovered.autosave_path == initial_path
    assert load_project(initial_path, check_sources=False).card_deck.settings.border_width_pt == 8.0

    recovered.discard_unsaved_autosave()
    assert not __import__("os").path.exists(initial_path)
