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
