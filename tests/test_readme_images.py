"""Every image the README links is in the repository.

The README's "SCREENSHOTS WANTED" comment refuses to link images that do
not exist, because a broken image on the front page is worse than none.
This holds it to that once someone adds one (F13 §C). It passes trivially
while none are linked; ``docs/images/README.md`` says what to capture.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _linked_images(text: str) -> list[str]:
    markdown = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    html = re.findall(r"<img[^>]+src=[\"']([^\"']+)", text)
    return [t for t in markdown + html if not re.match(r"[a-z]+://", t)]


def test_every_image_the_readme_links_is_present():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # Not inside an HTML comment: the wanted-list names files on purpose.
    visible = re.sub(r"<!--.*?-->", "", readme, flags=re.S)
    missing = [t for t in _linked_images(visible) if not (ROOT / t).exists()]
    assert missing == []


def test_the_capture_instructions_name_all_three_images():
    guide = (ROOT / "docs" / "images" / "README.md").read_text(encoding="utf-8")
    for name in ("preview-spread.png", "print-dialog.png", "schedule.png"):
        assert name in guide


def test_the_finder_sees_a_link_when_there_is_one():
    """The guard is only worth having if it would notice."""
    assert _linked_images("![x](docs/images/a.png) <img src='docs/b.png'>") == [
        "docs/images/a.png", "docs/b.png"]
