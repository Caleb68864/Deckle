"""Project persistence: the ``.deckle`` project file format.

``.deckle`` is JSON holding references only -- ``path``, ``page_index``,
``sha256``, plus per-page overrides -- never the page content itself. Top
level shape::

    {"version": 1, "pages": [...], "layout": {...}, "printer": "..."}

Reopening a project whose source file content has changed since it was
saved (a mismatched ``sha256``) raises ``SourceChangedWarning`` naming the
file, rather than silently substituting the new content -- see the module
docstring in ``deckle/core/print_session.py`` for why silent substitution
during printing is unacceptable.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef

FORMAT_VERSION = 1


class SourceChangedWarning(Exception):
    """Raised by ``load_project`` when a source file's content hash has
    changed since the project was saved.

    Carries the offending file's ``path`` so a caller can report exactly
    which source needs attention, without ``load_project`` ever
    substituting the new content in place of what was saved.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Source file changed since project was saved: {path}")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_to_dict(page: SourcePage) -> dict[str, Any]:
    ref = page.ref
    return {
        "path": ref.path,
        "page_index": ref.page_index,
        "sha256": ref.sha256,
        "width_pt": ref.width_pt,
        "height_pt": ref.height_pt,
        "rotate_deg": page.rotate_deg,
        "skipped": page.skipped,
    }


def _page_from_dict(data: dict[str, Any]) -> SourcePage:
    ref = SourceRef(
        path=data["path"],
        page_index=data["page_index"],
        sha256=data["sha256"],
        width_pt=data["width_pt"],
        height_pt=data["height_pt"],
    )
    return SourcePage(
        ref=ref,
        rotate_deg=data["rotate_deg"],
        skipped=data["skipped"],
    )


def _layout_to_dict(layout: LayoutSettings) -> dict[str, Any]:
    data = asdict(layout)
    data["paper"] = list(layout.paper)
    return data


def _layout_from_dict(data: dict[str, Any]) -> LayoutSettings:
    kwargs = dict(data)
    kwargs["paper"] = tuple(data["paper"])
    return LayoutSettings(**kwargs)


def save_project(project: Project, path: str) -> None:
    """Write ``project`` to ``path`` as a ``.deckle`` JSON file.

    Only references (path/page_index/sha256) and per-page overrides are
    written -- never page content.
    """
    payload = {
        "version": FORMAT_VERSION,
        "pages": [_page_to_dict(p) for p in project.pages],
        "layout": _layout_to_dict(project.layout),
        "printer": project.printer,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_project(path: str, *, check_sources: bool = True) -> Project:
    """Read a ``.deckle`` project file from ``path``.

    If ``check_sources`` is true (the default), every distinct source
    file referenced by the project has its content hash recomputed and
    compared against the hash stored at save time. On the first mismatch,
    raises ``SourceChangedWarning`` naming that file -- the project is
    never loaded with silently substituted content.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    pages = [_page_from_dict(p) for p in payload["pages"]]

    if check_sources:
        checked: set[str] = set()
        for page in pages:
            ref = page.ref
            if ref.path in checked:
                continue
            checked.add(ref.path)
            try:
                current_hash = _sha256_file(ref.path)
            except OSError:
                raise SourceChangedWarning(ref.path)
            if current_hash != ref.sha256:
                raise SourceChangedWarning(ref.path)

    layout = _layout_from_dict(payload["layout"])
    printer = payload.get("printer")
    return Project(pages=pages, layout=layout, printer=printer)
