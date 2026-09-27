import hashlib
import json

import pikepdf
import pytest

from deckle.core.models import *
from deckle.core.project_io import FORMAT_VERSION, SourceChangedWarning, SourceMissingError, UnknownCardFieldsWarning, load_project, save_project


def make_project(tmp_path):
    path=tmp_path/"asset.pdf"; pdf=pikepdf.Pdf.new(); pdf.add_blank_page(page_size=(120,180)); pdf.save(path); pdf.close()
    raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
    asset=CardAsset("a","front.png",str(tmp_path/"front.png"),"b"*64,
                    SourcePage(SourceRef(str(path),0,sha,120,180),0,False))
    deck=CardDeck((asset,),(CardEntry("c",CardFace("a"),None,2,False),),CardSettings(border_enabled=False,bleed_pt=5),"shared",None)
    return Project([],LayoutSettings((612,792),18,"left"),None,"cards",deck),path


def test_card_roundtrip_incomplete_and_legacy_book_version(tmp_path):
    project, source=make_project(tmp_path); path=tmp_path/"deck.deckle"
    save_project(project,str(path))
    payload=json.loads(path.read_text())
    assert payload["version"]==2 and payload["kind"]=="cards"
    loaded=load_project(str(path),allowed_roots=(str(tmp_path),))
    assert loaded==project
    book=Project([],project.layout,None)
    save_project(book,str(tmp_path/"book.deckle"))
    assert json.loads((tmp_path/"book.deckle").read_text())["version"]==1


def test_card_sources_checked_but_original_image_need_not_exist(tmp_path):
    project, source=make_project(tmp_path); target=tmp_path/"deck.deckle"; save_project(project,str(target))
    assert load_project(str(target),allowed_roots=(str(tmp_path),)).kind=="cards"
    source.write_bytes(b"changed")
    with pytest.raises(SourceChangedWarning): load_project(str(target),allowed_roots=(str(tmp_path),))
    source.unlink()
    with pytest.raises(SourceMissingError): load_project(str(target),allowed_roots=(str(tmp_path),))


def test_invalid_card_kind_shapes_fail_closed(tmp_path):
    project,_=make_project(tmp_path); target=tmp_path/"deck.deckle"; save_project(project,str(target))
    payload=json.loads(target.read_text()); payload["card_deck"]["cards"][0]["copies"]=True
    target.write_text(json.dumps(payload))
    with pytest.raises(ValueError): load_project(str(target),check_sources=False)
    payload["kind"]="future"
    target.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match="unknown project kind"): load_project(str(target),check_sources=False)


def test_unknown_nested_card_fields_warn_without_discarding_known_data(tmp_path):
    project,_=make_project(tmp_path); target=tmp_path/"deck.deckle"; save_project(project,str(target))
    payload=json.loads(target.read_text())
    payload["card_deck"]["assets"][0]["future_asset_field"]="ignored"
    payload["card_deck"]["cards"][0]["future_entry_field"]="ignored"
    target.write_text(json.dumps(payload))
    with pytest.warns(UnknownCardFieldsWarning) as caught:
        loaded=load_project(str(target),check_sources=False)
    messages = [str(item.message) for item in caught]
    assert any("future_asset_field" in message for message in messages)
    assert any("future_entry_field" in message for message in messages)
    assert loaded.card_deck.cards[0].copies==2
    assert len(loaded.card_deck.assets)==1
