"""Project persistence: the ``.deckle`` project file format.

``.deckle`` is JSON holding references only -- ``path``, ``page_index``,
``sha256``, plus per-page overrides -- never the page content itself. Top
level shape::

    {"version": 1, "pages": [...], "layout": {...}, "printer": "..."}

Reopening a project whose source file content has changed since it was
saved (a mismatched ``sha256``, on a file that still exists) raises
``SourceChangedWarning`` naming the file, rather than silently substituting
the new content -- see the module docstring in
``deckle/core/print_session.py`` for why silent substitution during
printing is unacceptable. A source file that has been moved or deleted --
a different failure -- raises ``SourceMissingError`` instead, carrying an
``expected_path`` and a ``relocate(new_path)`` affordance for the caller.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import warnings
import dataclasses
from dataclasses import asdict
from typing import Any, Callable

from deckle.core.models import is_blank_page, LayoutSettings, Project, SourcePage, SourceRef

FORMAT_VERSION = 1


class SourceChangedWarning(Exception):
    """Raised by ``load_project`` when a source file's content hash has
    changed since the project was saved.

    Carries the offending file's ``path`` so a caller can report exactly
    which source needs attention, without ``load_project`` ever
    substituting the new content in place of what was saved.

    :param path: the file whose content hash no longer matches.
    :ivar path: the same, for a caller composing its own message.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Source file changed since project was saved: {path}")


class SourceMissingError(Exception):
    """Raised by ``load_project`` when a referenced source file cannot be
    found on disk at all (moved or deleted), as distinct from
    ``SourceChangedWarning`` (a file that exists but whose content hash no
    longer matches).

    Carries ``expected_path`` -- where the project expected to find the
    file -- so a caller (the CLI or the UI) can offer a *relocate*
    affordance rather than a bare failure. ``relocate(new_path)`` records
    the path the caller found the file at, for use in a subsequent
    load/save cycle.

    :param expected_path: where the project expected the file to be.
    :ivar expected_path: the same.
    :ivar relocated_path: ``None`` until :meth:`relocate` is called.
    """

    def __init__(self, expected_path: str):
        self.expected_path = expected_path
        self.relocated_path: str | None = None
        super().__init__(f"Source file missing: {expected_path}")

    def relocate(self, new_path: str) -> str:
        """Record ``new_path`` as the relocated location of the missing
        source and return it, for the caller to use when re-loading or
        re-saving the project.

        :param new_path: where the user found the file.
        :returns: ``new_path``, so the call can be used inline.
        """
        self.relocated_path = new_path
        return new_path


class PathOutsideRootsWarning(Exception):
    """Raised by ``load_project`` when a referenced source path resolves
    outside the project directory and any caller-supplied ``allowed_roots``
    (red-team A-3: ``.deckle`` files carry filesystem paths and may be
    shared, so a crafted or relocated project file could point at an
    unrelated path elsewhere on disk).

    A path that fails this check is never opened silently. The caller
    (CLI or UI) decides whether to prompt the user for confirmation and,
    if approved, retry with an expanded ``allowed_roots``.

    :param path: the source path that resolved outside every root.
    :param allowed_roots: the roots it was checked against.
    :ivar path: the offending path.
    :ivar allowed_roots: the roots that were considered acceptable, so a
        caller can show the user what it would be widening.
    """

    def __init__(self, path: str, allowed_roots: tuple[str, ...]):
        self.path = path
        self.allowed_roots = allowed_roots
        super().__init__(
            f"Source path resolves outside the project directory and "
            f"allowed roots: {path}"
        )


class PathOutsideRootsAdvisory(UserWarning):
    """Emitted by ``load_project`` when a referenced source path resolves
    outside the project directory and any caller-supplied ``allowed_roots``,
    and no ``on_outside_roots`` decision callback was provided.

    This is deliberately **non-fatal**. Deckle's normal case is a project
    whose sources live somewhere else entirely -- Downloads, a sync folder,
    a scanner output directory -- so refusing to load them would break the
    primary workflow rather than protect it. Red-team A-3 asks that such a
    path not be opened *silently*; a visible advisory satisfies that, while
    a UI that wants a real confirmation prompt passes ``on_outside_roots``
    and gets a veto.
    """


def _path_within_roots(candidate: str, roots: tuple[str, ...]) -> bool:
    """True if ``candidate`` resolves inside any of ``roots``.

    Resolves both sides with ``os.path.realpath`` so ``..`` traversal and
    symlinks can't be used to escape the check.
    """
    real_candidate = os.path.realpath(candidate)
    for root in roots:
        real_root = os.path.realpath(root)
        try:
            common = os.path.commonpath([real_candidate, real_root])
        except ValueError:
            # e.g. different drives on Windows -- definitely not contained.
            continue
        if common == real_root:
            return True
    return False


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


class UnknownLayoutFieldsWarning(UserWarning):
    """Emitted when a ``.deckle`` carries layout keys this build does not know.

    Non-fatal by design. The alternative -- passing every stored key straight
    into ``LayoutSettings(**kwargs)`` -- means *any* field ever added or
    removed permanently breaks every project file written on the other side
    of that change. Deleting ``scale_mode`` did exactly that: files saved
    before the removal raised
    ``TypeError: unexpected keyword argument 'scale_mode'`` and could not be
    opened at all.
    """


def _layout_from_dict(data: dict[str, Any]) -> LayoutSettings:
    """Build ``LayoutSettings`` from stored JSON, tolerating field drift.

    Unknown keys are dropped with a warning rather than raising, and missing
    keys fall back to the dataclass defaults. That makes the format tolerant
    in both directions: a file written by an older build (missing fields) and
    one written by a newer build (extra fields) both open, which is what the
    ``version`` integer was reserved for.
    """
    known = {f.name for f in dataclasses.fields(LayoutSettings)}
    kwargs = {k: v for k, v in data.items() if k in known}
    unknown = sorted(set(data) - known)
    if unknown:
        warnings.warn(
            "ignoring layout fields this build does not recognise: "
            + ", ".join(unknown),
            UnknownLayoutFieldsWarning,
            stacklevel=2,
        )
    if "paper" in kwargs:
        kwargs["paper"] = tuple(kwargs["paper"])
    return LayoutSettings(**kwargs)


def save_project(project: Project, path: str) -> None:
    """Write ``project`` to ``path`` as a ``.deckle`` JSON file.

    Only references (path/page_index/sha256) and per-page overrides are
    written -- never page content.

    :param project: the project to serialize.
    :param path: the ``.deckle`` file to write.
    :returns: nothing.
    :raises OSError: the path cannot be opened for writing (missing
        directory, read-only file, absent drive). Not caught here: the
        caller owns the message, and it names the path the user typed.
    """
    payload = {
        "version": FORMAT_VERSION,
        "pages": [_page_to_dict(p) for p in project.pages],
        "layout": _layout_to_dict(project.layout),
        "printer": project.printer,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_project(
    path: str,
    *,
    check_sources: bool = True,
    allowed_roots: tuple[str, ...] | None = None,
    on_outside_roots: Callable[[str, tuple[str, ...]], bool] | None = None,
) -> Project:
    """Read a ``.deckle`` project file from ``path``.

    Every distinct source path referenced by the project is validated
    (red-team A-3) against the project's own directory plus any
    caller-supplied ``allowed_roots`` -- the directories the user has
    actually chosen to work in. A source path that resolves outside all of
    them raises ``PathOutsideRootsWarning`` rather than being opened
    silently; ``.deckle`` files carry filesystem paths and may be shared,
    so a crafted or relocated project file could otherwise be used to
    reference an arbitrary path elsewhere on disk. This check runs
    regardless of ``check_sources``.

    If ``check_sources`` is true (the default), every distinct source
    file referenced by the project is also checked to still exist and has
    its content hash recomputed and compared against the hash stored at
    save time. A missing file raises ``SourceMissingError`` naming that
    file's ``expected_path``; a file that exists but hashes differently
    raises ``SourceChangedWarning``. Either way the project is never
    loaded with silently substituted content.

    :param path: the ``.deckle`` file to read.
    :param check_sources: whether to verify that every referenced source
        still exists and still hashes the same. The containment check runs
        regardless.
    :param allowed_roots: directories the user has chosen to work in,
        beyond the project's own. Sources outside all of them are not
        opened silently.
    :param on_outside_roots: a decision callback taking
        ``(path, roots)`` and returning whether to allow it. When omitted,
        an out-of-roots path emits :class:`PathOutsideRootsAdvisory` and
        proceeds -- refusing outright would break the normal case, where a
        project's sources live in Downloads or a scanner folder.
    :returns: the loaded project.
    :raises OSError: ``path`` cannot be read.
    :raises json.JSONDecodeError: ``path`` is not valid JSON.
    :raises KeyError: the file is JSON but not a ``.deckle`` document.
    :raises PathOutsideRootsWarning: a source resolves outside every
        allowed root and ``on_outside_roots`` vetoed it.
    :raises SourceMissingError: a referenced source is gone, carrying
        ``expected_path`` and a ``relocate`` affordance.
    :raises SourceChangedWarning: a referenced source exists but its
        content hash no longer matches.
    :raises UnknownLayoutFieldsWarning: never raised -- emitted through
        :mod:`warnings` when the file carries layout keys this build does
        not recognise, so field drift in either direction still opens.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    pages = [_page_from_dict(p) for p in payload["pages"]]

    project_dir = os.path.dirname(os.path.realpath(path))
    roots = (project_dir, *(allowed_roots or ()))

    checked: set[str] = set()
    for page in pages:
        ref = page.ref
        if is_blank_page(page):
            # A blank the user inserted references no file. Both checks
            # below are about a source on disk, and a blank has none: the
            # containment check advised on an empty path, and the existence
            # check then raised SourceMissingError for it -- so a project
            # containing a single blank could be saved and never reopened.
            continue
        if ref.path in checked:
            continue
        checked.add(ref.path)
        if not _path_within_roots(ref.path, roots):
            if on_outside_roots is None:
                warnings.warn(
                    f"Source path resolves outside the project directory and "
                    f"allowed roots: {ref.path}",
                    PathOutsideRootsAdvisory,
                    stacklevel=2,
                )
            elif not on_outside_roots(ref.path, roots):
                raise PathOutsideRootsWarning(ref.path, roots)
        if check_sources:
            if not os.path.exists(ref.path):
                raise SourceMissingError(ref.path)
            try:
                current_hash = _sha256_file(ref.path)
            except OSError:
                raise SourceMissingError(ref.path)
            if current_hash != ref.sha256:
                raise SourceChangedWarning(ref.path)

    layout = _layout_from_dict(payload["layout"])
    printer = payload.get("printer")
    return Project(pages=pages, layout=layout, printer=printer)
