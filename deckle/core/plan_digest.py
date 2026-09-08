"""One canonical hash of a ``SheetPlan``: what the sheets will actually look like.

Two things need to ask "is this the same plan as before?", for reasons that
look unrelated and are not:

- :mod:`deckle.core.export` caches a rendered sheet against it. Handing back
  a cached PDF for a plan that has changed prints the wrong page.
- :mod:`deckle.core.print_session` stores it when a print run begins and
  compares on resume. Accepting a changed plan prints backs against fronts
  that no longer match -- a ruined stack of expensive paper, discovered
  after the fact.

They had two different answers. The exporter hashed everything that reaches
paper; the session hashed sheet indices, side presence and source page
*indices*, and nothing else. So a session survived a change of gutter,
margins, paper size, crop, trim or scale, and resumed onto geometry that no
longer matched the sheets already printed -- which is the exact failure
``StaleSessionError(reason="plan")`` exists to prevent. Two implementations
of "the same plan" will always drift, and the one that drifts silently is
the one nobody is looking at.

So there is one implementation, here, and both import it.

**Framing.** Every variable-length component is length-prefixed rather than
concatenated -- see :func:`update_delimited`. Running variable-length keys
together makes the boundary between them recoverable from their content, so
a path containing the separators could reproduce the bytes two different
pages would contribute, and two genuinely different plans would hash alike.
"""

from __future__ import annotations

import hashlib

from deckle.core.models import Mark, OutputPage, SheetPlan


def mark_key(mark: Mark) -> str:
    """One mark's contribution to the digest.

    :param mark: the mark to describe.
    :returns: its stable key. Every field is a float or a short enum-like
        string, none of which can contain the separator.
    """
    return f"{mark.kind}:{mark.x0}:{mark.y0}:{mark.x1}:{mark.y1}"


def update_delimited(digest: "hashlib._Hash", tag: str, value: str) -> None:
    """Feed ``value`` into ``digest`` length-prefixed, not just concatenated.

    Running variable-length keys together makes the boundary between them
    recoverable from their content rather than fixed by the framing: a
    ``SourceRef.path`` that happens to contain the field separators can
    reproduce, on its own, the exact bytes that two *different* pages would
    contribute, so two genuinely different plans hash identically and the
    second one is handed the first one's cached PDF.

    A cache that returns the wrong page is worse than no cache at all, so
    every variable-length component announces its own byte length and no
    content can straddle a boundary.

    :param digest: the hash to update, in place.
    :param tag: a short field name, fixed by the caller and never
        user-controlled.
    :param value: the variable-length component.
    :returns: nothing.
    """
    encoded = value.encode("utf-8")
    digest.update(f"|{tag}[{len(encoded)}]:".encode("utf-8"))
    digest.update(encoded)


def output_page_key(page: OutputPage) -> str:
    """One page's contribution to the plan digest.

    The **path is length-prefixed** and everything else is joined plainly.
    That asymmetry is the point: :func:`update_delimited` explains why
    running variable-length keys together lets content forge a field
    boundary, and the path is the only free-text field here -- the rest are
    an integer, a 64-character hex digest, two float reprs and a bool, none
    of which can contain the separator.

    So no collision was constructible before this, and that was **a property
    of the field types rather than of the framing**: a ``SourceRef`` gaining
    any second string field would have removed it silently. The framing now
    does not depend on what the other fields happen to be, which matters
    because ``.deckle`` files carry these paths and may be shared
    (red-team A-3).

    :param page: the placed page to describe.
    :returns: its stable key.
    """
    ref = page.source_ref
    ref_key = (
        "none"
        if ref is None
        else (
            f"path[{len(ref.path.encode('utf-8'))}]:{ref.path}"
            f":{ref.page_index}:{ref.sha256}:{ref.width_pt}:{ref.height_pt}"
        )
    )
    placement = page.placement
    placement_key = (
        f"{placement.scale_x}:{placement.scale_y}:{placement.tx}:"
        f"{placement.ty}:{placement.rotate_deg}"
    )
    return f"{ref_key}|{placement_key}|{page.is_filler}|{page.crop_pt}"


def plan_digest(plan: SheetPlan) -> str:
    """A stable hash of everything about ``plan`` that affects paper.

    Deliberately built from the plan's own field values (not Python's
    ``id()`` or ``hash()``, which are unstable across processes) so the same
    layout settings always produce the same key in any process, on any run.

    Covers the paper size, and for every sheet: its index, which sides exist,
    and for each side every page's source reference, placement, filler flag
    and crop, followed by every mark. An absent side is distinct from an
    empty one -- ``Side.__post_init__`` rejects ``Side(pages=())`` precisely
    so presence and content stay unambiguous here.

    :param plan: the plan to digest.
    :returns: a 64-character hex digest.
    """
    digest = hashlib.sha256()
    digest.update(repr(plan.paper_pt).encode("utf-8"))
    for sheet in plan.sheets:
        digest.update(f"|sheet:{sheet.index}".encode("utf-8"))
        for side_name, side in (("front", sheet.front), ("back", sheet.back)):
            digest.update(f"|{side_name}:".encode("utf-8"))
            if side is None:
                digest.update(b"none")
                continue
            for page in side.pages:
                update_delimited(digest, "page", output_page_key(page))
            for mark in side.marks:
                update_delimited(digest, "mark", mark_key(mark))
    return digest.hexdigest()
