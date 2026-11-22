import os
from pathlib import Path

from PIL import Image

from deckle.core.card_import import import_card_folder, import_card_images


def test_folder_natural_order_and_individual_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    folder = tmp_path / "cards"; folder.mkdir()
    for name in ("card10.png", "card2.png", "card1.png", "readme.txt"):
        if name.endswith("png"):
            Image.new("RGB", (40, 60), "red").save(folder / name)
        else:
            (folder / name).write_text("ignored")
    result = import_card_folder(str(folder))
    assert [a.label for a in result.assets] == ["card1.png", "card2.png", "card10.png"]
    assert result.warnings and result.warnings[0].kind == "skipped_non_image_files"
    assert all(os.path.isfile(a.page.ref.path) for a in result.assets)
    assert [(round(a.page.ref.width_pt),round(a.page.ref.height_pt)) for a in result.assets] == [(40,60)]*3
    again = import_card_folder(str(folder))
    assert [a.page.ref.path for a in again.assets] == [a.page.ref.path for a in result.assets]


def test_selected_dedup_and_reject_animated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    image = tmp_path / "one.png"; Image.new("RGB", (20, 30), "blue").save(image)
    result = import_card_images([str(image), str(image)])
    assert len(result.assets) == 1
    animated = tmp_path / "animated.tiff"
    a, b = Image.new("RGB", (4, 4), "red"), Image.new("RGB", (4, 4), "blue")
    a.save(animated, save_all=True, append_images=[b])
    try:
        import_card_images([str(animated)])
    except ValueError as exc:
        assert "multi-frame" in str(exc)
    else:
        raise AssertionError("multi-frame image should be rejected")


# -- artwork larger than a PDF page --------------------------------------


def _half_black(path, size):
    width, height = size
    image = Image.new("RGB", size, "white")
    image.paste((0, 0, 0), (0, 0, width // 2, height))
    image.save(path)


def test_artwork_wider_than_a_pdf_page_is_scaled_to_fit_and_says_its_true_size(tmp_path, monkeypatch):
    """One point per pixel made a 15000px image a 15000pt page, past the
    14400pt PDF limit. img2pdf answered with /UserUnit 10, which Deckle does
    not read, so the stored page claimed to be 1500pt: a tenth of the size,
    right only by accident of aspect. It is scaled to the limit instead."""
    import pikepdf

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    wide = tmp_path / "wide.png"
    _half_black(wide, (15000, 200))

    ref = import_card_images([str(wide)]).assets[0].page.ref

    assert (ref.width_pt, ref.height_pt) == (14400.0, 192.0)
    with pikepdf.open(ref.path) as pdf:
        page = pdf.pages[0]
        assert "/UserUnit" not in page.obj
        assert [float(v) for v in page.MediaBox] == [0.0, 0.0, 14400.0, 192.0]


def test_tall_artwork_is_scaled_on_its_long_side(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tall = tmp_path / "tall.png"
    _half_black(tall, (300, 18000))

    ref = import_card_images([str(tall)]).assets[0].page.ref

    assert (ref.width_pt, ref.height_pt) == (240.0, 14400.0)


def test_the_scaled_page_still_shows_the_whole_image(tmp_path, monkeypatch):
    import pypdfium2 as pdfium

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    wide = tmp_path / "wide.png"
    _half_black(wide, (15000, 200))
    ref = import_card_images([str(wide)]).assets[0].page.ref

    pdf = pdfium.PdfDocument(ref.path)
    try:
        bitmap = pdf[0].render(scale=0.05).to_pil().convert("L")
    finally:
        pdf.close()
    width, height = bitmap.size
    assert bitmap.getpixel((width // 4, height // 2)) < 50
    assert bitmap.getpixel((3 * width // 4, height // 2)) > 200


def test_ordinary_card_art_is_stored_exactly_as_before(tmp_path, monkeypatch):
    """Only the oversized case changed, so a normal card keeps its stored
    file and its one-point-per-pixel page."""
    import hashlib

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    card = tmp_path / "card.png"
    _half_black(card, (750, 1050))

    asset = import_card_images([str(card)]).assets[0]

    assert (asset.page.ref.width_pt, asset.page.ref.height_pt) == (750.0, 1050.0)
    key = hashlib.sha256((asset.original_sha256 + ":card-import-v1").encode("ascii")).hexdigest()
    assert os.path.basename(asset.page.ref.path) == f"card-{key}.pdf"
