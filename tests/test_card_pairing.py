from deckle.core.card_pairing import suggest_card_pairs
from deckle.core.models import CardAsset, SourcePage, SourceRef


def asset(name):
    import hashlib
    from pathlib import Path
    path = str(Path(__file__).resolve())
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ref = SourceRef(path, 0, digest, 200, 300)
    return CardAsset(name, name, path, digest, SourcePage(ref, 0, False))


def test_filename_and_ambiguity():
    f = [asset("f1"), asset("f2")]
    f[0] = __import__('dataclasses').replace(f[0], label="Goblin_front.PNG")
    f[1] = __import__('dataclasses').replace(f[1], label="wolf.png")
    b = [__import__('dataclasses').replace(asset("b1"), label="goblin-back.jpg"),
         __import__('dataclasses').replace(asset("b2"), label="wolf-back.jpeg")]
    result = suggest_card_pairs(f, b, "filename")
    assert set(result.matches) == {("f1", "b1"), ("f2", "b2")}


def test_order_is_natural_and_reports_remainders():
    f = [__import__('dataclasses').replace(asset(str(i)), label=f"card{i}.png") for i in (10, 2, 1)]
    b = [asset("b1"), asset("b2")]
    result = suggest_card_pairs(f, b, "order")
    assert result.matches == (("1", "b1"), ("2", "b2"))
    assert result.unmatched_front_ids == ("10",)
