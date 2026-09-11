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
a different failure -- raises ``SourceMissingError`` instead, carrying the
``expected_path`` so a caller can name the file it could not find.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import warnings
import dataclasses
from dataclasses import asdict
from typing import Any, Callable

from deckle.core.models import (
    BLANK_SOURCE_PATH, is_blank_page, LayoutSettings, Project, SourcePage, SourceRef
)
from deckle.core.paths import write_text_atomic
from deckle.core.schema import check_values, describe, describes

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
    file -- so a caller can name it. Both catchers do:
    ``cli.commands`` and ``app.project_actions`` each report it and
    return.

    This class used to also carry a ``relocate(new_path)`` method and a
    ``relocated_path`` attribute, documented as letting a caller "offer a
    *relocate* affordance rather than a bare failure". They were removed
    because nothing could consume them: ``relocate`` set an attribute on
    an exception object that its catcher then discarded, and
    :func:`load_project` has no parameter that would accept a substitute
    source. A relocate affordance is a real feature and a reasonable one
    -- it belongs in ``load_project``'s signature, where a caller could
    pass the path the user pointed at, and not on the exception that
    announces the problem.

    :param expected_path: where the project expected the file to be.
    :ivar expected_path: the same.
    """

    def __init__(self, expected_path: str):
        self.expected_path = expected_path
        super().__init__(f"Source file missing: {expected_path}")


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


_JSON_SHAPE_NAMES = {
    type(None): "null",
    bool: "a true/false value",
    int: "a number",
    float: "a number",
    str: "a piece of text",
    list: "a list",
    dict: "an object",
}


def _shape_of(value: Any) -> str:
    """Name a JSON value's type the way the file's author would recognise it.

    ``"<class 'dict'>"`` describes Python; someone looking at a ``.deckle``
    in a text editor sees an object, a list or a piece of text. The message
    has to name the thing they can see, because the remedy is to compare
    the file against one Deckle wrote.
    """
    # `bool` before `int`: it is a subclass, and "a number" for `true` would
    # send someone looking for a digit that is not there.
    for kind, name in _JSON_SHAPE_NAMES.items():
        if type(value) is kind:
            return name
    return "not what this format expects"


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


_PAGE_FIELD_TYPES = {
    "path": str,
    "page_index": int,
    "sha256": str,
    "width_pt": float,
    "height_pt": float,
    "rotate_deg": int,
    "skipped": bool,
}


def _check_page_entry(index: int, data: dict[str, Any]) -> None:
    """Reject a stored page entry that names no page, or names it wrongly.

    Same reasoning as :func:`_check_layout_values`, and the consequences
    here are worse: what a bad layout value corrupts is the geometry, and
    what a bad page entry corrupts is *which page gets printed*.

    ``page_index: -2`` was the one that mattered. It is a perfectly good
    Python index, so it reached ``src_pdf.pages[-2]`` and exported the
    second-from-last page -- verified on the numbered dummy, where an
    eight-page source with an entry naming page -2 produced a sheet
    reading **7**, with no warning anywhere and nothing in the output to
    say it was not what was asked for.

    ``-1`` stays legal for a blank, because that is the sentinel
    :func:`deckle.app.state.make_blank_page` writes. It is allowed only
    together with ``BLANK_SOURCE_PATH``, so it means something exactly
    where the format intends it and nowhere else -- accepting a bare
    ``-1`` on a real source would reopen the wrapping bug for the last
    page of every document.

    Only the *sign* is checkable here. Whether an index is past the end
    depends on the source, which may not even be present at load time --
    see :mod:`deckle.core.export` for the other half.

    :param index: which entry, for the message.
    :param data: the stored page object.
    :returns: nothing.
    :raises ValueError: the entry cannot describe a page.
    """
    for name, expected in _PAGE_FIELD_TYPES.items():
        if not describes(data[name], expected):
            raise ValueError(
                f"page {index}: {name!r} is {data[name]!r}, but a page's "
                f"{name!r} must be {describe(expected)}"
            )

    is_blank = data["path"] == BLANK_SOURCE_PATH
    if data["page_index"] < 0 and not (is_blank and data["page_index"] == -1):
        raise ValueError(
            f"page {index}: 'page_index' is {data['page_index']}, but a page "
            "index counts from 0. A negative index is a valid Python index, "
            "so this would have printed a different page rather than failing."
        )
    for name in ("width_pt", "height_pt"):
        if data[name] <= 0:
            raise ValueError(
                f"page {index}: {name!r} is {data[name]!r}, but a page must "
                "have a positive size"
            )
    if data["rotate_deg"] % 90 != 0:
        raise ValueError(
            f"page {index}: 'rotate_deg' is {data['rotate_deg']}, but a page "
            "rotation must be a multiple of 90. The exporter applies 90 and "
            "270 and treats everything else as upright, so an oblique value "
            "would be dropped rather than honoured."
        )


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


def layout_to_dict(layout: LayoutSettings) -> dict[str, Any]:
    # No per-field conversion on the way out, deliberately mirroring
    # `layout_from_dict`: `json` serialises a tuple as an array already,
    # so naming `paper` here achieved nothing that the encoder was not
    # doing for every other tuple field anyway. Naming one field was how
    # the read side came to be wrong; leaving the same shape here would
    # invite someone to "fix" the asymmetry by adding the other three.
    return asdict(layout)


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


def _check_layout_values(kwargs: dict[str, Any]) -> None:
    """Reject a stored layout value this build cannot honour.

    See :func:`deckle.core.schema.check_values` -- the rule is shared with
    the printer-profile reader, because both had the same defect.

    :param kwargs: stored values, already filtered to known field names.
    :returns: nothing.
    :raises StoredValueError: a value does not satisfy its field.
    :raises ValueError: ``paper`` is two numbers but not a sheet.
    """
    check_values(LayoutSettings, kwargs, subject="layout setting")

    # `check_values` asks whether `paper` is two numbers. It is not asking
    # whether those numbers describe a sheet, and `[-792, -612]` is two
    # perfectly good numbers -- which the imposer turns into a placement
    # with a scale of -1.98, i.e. a mirror, while `[0, 0]` gives a scale of
    # exactly zero and blank paper. Positive scale is already an invariant
    # asserted for the neighbouring case (margins larger than the sheet,
    # which warns and falls back "rather than a negative-size box and a
    # nonsense scale"); non-positive paper walked past it.
    #
    # Only `paper` is bounded here, and the omissions are deliberate:
    # negative margins and gutters are clamped to zero *by the imposer, on
    # purpose*, with a test pinning it, so rejecting them would contradict
    # a decision already made; and `sheets_per_signature` and
    # `signature_lengths` are already validated where they are used, with
    # messages naming the numbers involved.
    paper = kwargs.get("paper")
    if paper is not None and not all(value > 0 for value in paper):
        width, height = paper
        raise ValueError(
            f"layout setting 'paper' is {width:g}x{height:g}pt, but a sheet "
            "must have a positive width and height"
        )


def layout_from_dict(data: dict[str, Any]) -> LayoutSettings:
    """Build ``LayoutSettings`` from stored JSON, tolerating field drift.

    Unknown keys are dropped with a warning rather than raising, and missing
    keys fall back to the dataclass defaults. That makes the format tolerant
    in both directions: a file written by an older build (missing fields) and
    one written by a newer build (extra fields) both open, which is what the
    ``version`` integer was reserved for.

    Tolerant about *keys*, strict about *values* -- see
    :func:`_check_layout_values` for why those pull in opposite directions.

    :param data: the stored ``layout`` object.
    :returns: the settings.
    :raises ValueError: a stored value does not satisfy its field's type.
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
    # `letterbox` was a third `landscape_policy` value that never branched
    # on anything -- `scale` and `letterbox` produced identical placements
    # -- and was removed. `_check_layout_values` refuses a value outside a
    # field's `Literal`, correctly and harshly, so without this a project
    # saved by any earlier build would simply not open. Mapped rather than
    # dropped: the two meant the same thing, so this loses nothing, which
    # is exactly when a silent migration is allowed.
    if kwargs.get("landscape_policy") == "letterbox":
        kwargs["landscape_policy"] = "scale"

    # Before the tuple conversion below, not after: `tuple("big")` succeeds
    # and yields `('b', 'i', 'g')`, so checking afterwards would let a
    # string through as a three-element tuple and move the failure further
    # from its cause rather than closer.
    _check_layout_values(kwargs)
    # JSON has one sequence type and Python has two, so every tuple field
    # comes back as a list unless something converts it. This used to name
    # `paper` specifically, which was right while `paper` was the only
    # tuple -- and then three more arrived in a day and each silently
    # became a list: still usable (a list unpacks and indexes the same),
    # but the reloaded layout no longer equalled the saved one, the frozen
    # dataclass stopped being hashable, and the export cache keys on
    # `repr`, so identical geometry produced two entries.
    #
    # Converting every list rather than a named few is what keeps the next
    # tuple field from reintroducing it. No LayoutSettings field is
    # genuinely a list.
    kwargs = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in kwargs.items()
    }
    return LayoutSettings(**kwargs)


# The private names these two were introduced under. Kept because they are
# what the drift-tolerance tests import, and because a rename is not worth
# a second edit in a second file -- but there is one implementation, and
# `deckle.core.defaults` calls the public one rather than growing a second
# layout serialiser of its own.
_layout_to_dict = layout_to_dict
_layout_from_dict = layout_from_dict


def save_project(project: Project, path: str) -> None:
    """Write ``project`` to ``path`` as a ``.deckle`` JSON file.

    Only references (path/page_index/sha256) and per-page overrides are
    written -- never page content.

    The write is atomic (see :func:`deckle.core.paths.write_text_atomic`):
    a save that fails partway leaves the previous file intact. That matters
    most for the copy nobody is watching. Autosave runs unattended on a
    daemon timer thread, and its whole promise is that a killed process
    loses at most the last half-second of edits -- but a truncating write
    killed mid-flight destroyed the recovery file itself, so the one
    failure autosave exists to survive was the one it could not.

    :param project: the project to serialize.
    :param path: the ``.deckle`` file to write.
    :returns: nothing.
    :raises OSError: the path cannot be written (missing directory,
        read-only file, absent drive). Not caught here: the caller owns the
        message, and it names the path the user typed. The previous
        contents of ``path`` are still there.
    """
    payload = {
        "version": FORMAT_VERSION,
        "pages": [_page_to_dict(p) for p in project.pages],
        "layout": layout_to_dict(project.layout),
        "printer": project.printer,
    }
    buffer = io.StringIO()
    json.dump(payload, buffer, indent=2)
    write_text_atomic(path, buffer.getvalue())


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
    :raises KeyError: the file is JSON but not a ``.deckle`` document --
        either an object missing a key this format requires, or a top
        level that is not an object at all (an array, string, number,
        ``null`` or ``true``). Both are the same problem to a user and get
        the same answer, rather than the second one arriving as a
        ``TypeError`` nothing catches.
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

    if not isinstance(payload, dict):
        # `KeyError` rather than the `TypeError` that `payload["pages"]`
        # would raise a line later, because "JSON but not a `.deckle`
        # document" is what this function already documents `KeyError` to
        # mean -- and both callers have a branch for it that names the
        # remedy. Without this, an array, string, number, `null` or `true`
        # at the top level got a `TypeError` that nothing caught: the CLI
        # printed a traceback and the desktop app told the user "list
        # indices must be integers or slices, not str".
        raise KeyError("pages")

    stored_pages = payload["pages"]
    if not isinstance(stored_pages, list):
        raise ValueError(
            f"'pages' is {_shape_of(stored_pages)}, not a list of pages, so "
            "this is not a Deckle project"
        )
    for index, entry in enumerate(stored_pages):
        if not isinstance(entry, dict):
            raise ValueError(
                f"page {index} is {_shape_of(entry)}, not a page object, so "
                "this is not a Deckle project"
            )
        missing = sorted(set(_PAGE_FIELD_TYPES) - set(entry))
        if missing:
            raise KeyError(", ".join(repr(name) for name in missing))
        _check_page_entry(index, entry)
    stored_layout = payload["layout"]
    if not isinstance(stored_layout, dict):
        raise ValueError(
            f"'layout' is {_shape_of(stored_layout)}, not a set of layout "
            "settings, so this is not a Deckle project"
        )

    pages = [_page_from_dict(p) for p in stored_pages]

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

    layout = layout_from_dict(payload["layout"])
    printer = payload.get("printer")
    return Project(pages=pages, layout=layout, printer=printer)
