import img2pdf
from PIL import Image
import pikepdf
import pypdfium2 as pdfium
import pytest

from deckle.core.card_import import import_card_images
from deckle.core.card_layout import impose_cards
from deckle.core.models import CardDeck, CardEntry, CardFace, CardSettings
from deckle.core.export import export
from deckle.app.exporting import suggested_export_name, suggested_pass_export_name


def test_card_export_names_use_project_stem_and_plural_passes():
    assert suggested_export_name([],card_project=True,project_path="/tmp/forest.deckle")=="forest-cards.pdf"
    assert suggested_export_name([],card_project=True)=="cards.pdf"
    assert suggested_pass_export_name("forest-cards.pdf","front",card_project=True)=="forest-cards-fronts.pdf"
    assert suggested_pass_export_name("book-deckle.pdf","front")=="book-deckle-front.pdf"


def test_pdf_has_black_bleed_and_clipped_card_art(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # Asymmetric artwork: red body with a blue top-left corner and green bottom-right.
    image=Image.new("RGB",(40,60),"red")
    pixels=image.load()
    for x in range(8):
        for y in range(8): pixels[x,y]=(0,0,255); pixels[39-x,59-y]=(0,255,0)
    source=tmp_path/"front.png"; image.save(source)
    back=tmp_path/"back.png"; Image.new("RGB",(40,60),"yellow").save(back)
    assets=import_card_images([str(source),str(back)]).assets
    by_name={a.label:a for a in assets}
    deck=CardDeck(assets,(CardEntry("one",CardFace(by_name["front.png"].id),CardFace(by_name["back.png"].id)),),CardSettings(100,120,20,20,0,0,True,9,3))
    plan=impose_cards(deck,(500,400))
    output=tmp_path/"cards.pdf"; export(plan,str(output))
    with pdfium.PdfDocument(str(output)) as pdf:
        page=pdf[0]; bitmap=page.render(scale=2); rendered=bitmap.to_pil().convert("RGB")
        # Page is 500x400pt at 144dpi. Sample inside bleed, inside border, and inside the art.
        assert rendered.getpixel((78, 280)) == (0,0,0)
        assert rendered.getpixel((90, 280)) == (0,0,0)
        assert rendered.getpixel((180, 280)) == (255,0,0)
        # The blue top-left landmark is present and the opposite corner is green.
        assert rendered.getpixel((125, 180)) == (0,0,255)
        assert rendered.getpixel((242, 362)) == (0,128,0) or rendered.getpixel((242, 362)) == (0,255,0)
        bitmap.close(); page.close()


def test_proof_ruler_is_refused_for_card_art(tmp_path):
    source=tmp_path/"source.pdf"; pdf=pikepdf.Pdf.new(); pdf.add_blank_page(page_size=(100,120)); pdf.save(source); pdf.close()
    import hashlib
    from deckle.core.models import SourcePage,SourceRef
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    from deckle.core.models import CardAsset
    asset=CardAsset("a","a.png",str(tmp_path/"a.png"),"b"*64,SourcePage(SourceRef(str(source),0,digest,100,120),0,False))
    plan=impose_cards(CardDeck((asset,),(CardEntry("c",CardFace("a"),CardFace("a")),),CardSettings()),(612,792))
    target=tmp_path/"unsafe-proof.pdf"
    with pytest.raises(ValueError,match="cannot be drawn over trading-card artwork"):
        export(plan,str(target),rule=True)
    assert not target.exists()
