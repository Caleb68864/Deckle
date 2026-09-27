import hashlib
import json

import pikepdf
from deckle.cli import main
from deckle.core.models import *
from deckle.core.project_io import save_project


def saved_deck(tmp_path, paired=True):
    assets=[]
    for name in ("front","back"):
        path=tmp_path/f"{name}.pdf"; pdf=pikepdf.Pdf.new(); pdf.add_blank_page(page_size=(100,140)); pdf.save(path);pdf.close()
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append(CardAsset(name,name+".png",str(tmp_path/(name+".png")),"b"*64,SourcePage(SourceRef(str(path),0,sha,100,140),0,False)))
    entry=CardEntry("one",CardFace("front"),CardFace("back") if paired else None)
    project=Project([],LayoutSettings((612,792),18,"left"),None,"cards",CardDeck(tuple(assets),(entry,),CardSettings()))
    path=tmp_path/"cards.deckle";save_project(project,str(path));return path,assets


def test_cli_card_info_json_and_export(tmp_path,capsys):
    path,assets=saved_deck(tmp_path)
    assert main(["info",str(path),"--json"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["kind"]=="cards" and data["status"]=="ready" and data["active_copy_count"]==1
    assert len(data["slot_mappings"])>0
    output=tmp_path/"out.pdf"
    assert main(["export",str(path),"-o",str(output)])==0
    assert output.exists()
    assert main(["export",str(path),"-o",assets[0].page.ref.path])==1
    before=open(assets[0].page.ref.path,"rb").read()
    assert main(["impose",str(path),"-o",assets[0].page.ref.path])==1
    assert open(assets[0].page.ref.path,"rb").read()==before


def test_cli_incomplete_info_succeeds_but_export_fails(tmp_path,capsys):
    path,_=saved_deck(tmp_path,False)
    assert main(["info",str(path),"--json"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["unresolved_count"]==1 and data["problems"]
    assert main(["export",str(path),"-o",str(tmp_path/"bad.pdf")])==1
    assert not (tmp_path/"bad.pdf").exists()


def test_card_schedule_is_explicitly_unsupported(tmp_path,capsys):
    path,_=saved_deck(tmp_path)
    assert main(["schedule",str(path)])==1
    assert "not available for card decks" in capsys.readouterr().err


def test_card_print_writes_plural_pass_names_and_blocks_rule_overlay(tmp_path,capsys):
    path,_=saved_deck(tmp_path)
    base=tmp_path/"proof.pdf"
    assert main(["print",str(path),"--profile","generic_face_down_reversed","-o",str(base)])==0
    assert (tmp_path/"proof-fronts.pdf").exists()
    assert (tmp_path/"proof-backs.pdf").exists()
    assert main(["export",str(path),"-o",str(tmp_path/"ruled.pdf"),"--rule"])==1
    assert "--rule" in capsys.readouterr().err


def test_card_info_incomplete_declares_status_and_pages_are_rejected(tmp_path,capsys):
    path,_=saved_deck(tmp_path,False)
    assert main(["info",str(path),"--json"])==0
    assert json.loads(capsys.readouterr().out)["status"]=="incomplete"
    assert main(["export",str(path),"-o",str(tmp_path/"bad.pdf"),"--pages","1"])==1
    assert "--pages" in capsys.readouterr().err
