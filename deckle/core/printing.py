"""The PassPlanner: turns a SheetPlan + PrinterProfile into print passes.

Manual duplex on a home/office printer means two passes: fronts, then a
manual reload, then backs. This module computes the ordered sheets for
each pass and a plain-language reload instruction -- no submission, no
Qt, no I/O beyond the profile persistence in ``deckle/core/profiles.py``.
``PrintBackend`` is a ``Protocol`` so this module never imports Qt or a
concrete print API; SS-08 supplies the real backend.

Back-pass ordering table (see ``[[pikepdf - Manual Duplex Reordering]]``)
-- this is the physical behavior ``plan_passes`` implements.

**The source of this table is outside the repository and has not been
verified here.** ``[[pikepdf - Manual Duplex Reordering]]`` is a note in
an external vault (``Caleb's Vault/Software/pikepdf/``, per
``docs/specs/deckle-mvp/sub-spec-6-printerprofile-passplanner.md``); it is
cited in four files in this tree and present in none of them. Everything
below, and the ``rotate_backs`` rule that follows from it, has been
derived twice independently and agrees with the geometry argument in the
2026-08-06 decision-log entry -- but two agreeing derivations from an
unverified premise are still one unverified premise, and the premise is a
claim about how a *physical* printer hands paper back. It wants one proof
sheet on a real printer, or the note brought into this repository. This
paragraph does not say what the note contains, because nobody here has
read it; it says only that the table's authority is external. Do not
delete this until one of those two things has happened.


| Reload behavior                                    | Back-pass order |
|------------------------------------------------------|-----------------|
| Outputs face-down, stack reversed on reload           | backs as-is     |
| Outputs face-up, stack retains order                  | backs reversed  |
| User flips about the sheet's vertical edge             | no rotation |
| User flips about the sheet's horizontal edge          | back sides need 180deg rotation |

Mapping from ``PrinterProfile`` to the two behavioral axes ``plan_passes``
consumes:

- ``profile.reverse_stack`` (derived, at calibration/preset time, from
  ``output_face`` + ``feed_edge``) drives **sheet order** on the back
  pass: ``True`` reverses it, ``False`` keeps it identical to the front
  pass.
- ``profile.flip_axis`` compared against :func:`duplex_flip_edge` drives
  **``rotate_backs``**. Neither one decides it alone, and this is the
  correction to a rule that stood here for months as
  ``flip_axis == "long"``. ``flip_axis`` is a *measured* fact about the
  printer -- which named edge the operator physically turns the stack
  about. ``duplex_flip_edge(plan.paper_pt)`` is a *geometric* fact about
  the job -- which named edge is the sheet's vertical one, and so the one
  it must turn about for the backs to land upright (see that function,
  and the 2026-08-06 decision-log entry). They are different quantities
  that happen to share a vocabulary. The backs need a half turn exactly
  when they disagree::

      portrait  (vertical edge = long)
        flip_axis "long"  -> turns about vertical   -> upright  -> no rotate
        flip_axis "short" -> turns about horizontal -> inverted -> rotate
      landscape (vertical edge = short)
        flip_axis "short" -> turns about vertical   -> upright  -> no rotate
        flip_axis "long"  -> turns about horizontal -> inverted -> rotate

  A rule reading ``flip_axis`` alone is right for exactly one orientation
  and silently upside down for the other, which is a whole run of ruined
  paper that nothing on screen reports.

Rotation, when a caller applies ``rotate_backs``, must be done with
``page.rotate(180, relative=True)`` or by assigning ``page.rotation`` --
never by setting ``page.Rotate`` directly (older qpdf has mishandled
that). If a print driver ignores ``/Rotate``, bake it in with
``page.flatten_rotation()``. This module only decides *whether* to
rotate; SS-08 applies it at submission time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from deckle.core.models import SheetPlan
from deckle.core.profiles import PrinterProfile


def duplex_flip_edge(paper_pt: tuple[float, float]) -> Literal["long", "short"]:
    """Which edge a duplexer must turn the sheet about, from its size alone.

    The spine is vertical on the sheet under every scheme Deckle imposes --
    ``binding_edge`` is left or right, and ``marks.fold_line`` runs head to
    tail -- so the back must be turned about the sheet's **vertical** edge.
    Turning it about the horizontal one lands every back upside down.

    Which *named* edge that is depends on orientation, and this is the whole
    reason the answer cannot be a constant: a portrait sheet's vertical edge
    is its long one (gutter shift), a landscape sheet's is its short one
    (folio, folded down the middle). Getting it backwards is the single
    most common way a manual duplex job is ruined, and it is not visible
    until the paper is already printed.

    :param paper_pt: the sheet size as ``(width, height)`` in points.
    :returns: ``"long"`` or ``"short"``. A square sheet answers ``"long"``:
        neither is wrong, and pinning one keeps the exported PDF from
        depending on which way a float comparison falls.
    """
    width, height = paper_pt
    return "long" if height >= width else "short"


@dataclass(frozen=True)
class PrintResult:
    """The outcome of submitting one or more passes to a print backend."""

    submitted: int
    job_id: str | None
    error: str | None


class PrintBackend(Protocol):
    """A pluggable print submission target. Never imports Qt.

    Backends must honour ``side`` -- a back pass that paints fronts is a
    ruined stack of paper. The three keyword-only parameters carry what a
    :class:`PrintPass` already knows and a bare sheet list cannot say; they
    are declared here, and not only on the concrete backend, because a
    Protocol narrower than its implementation is a Protocol its callers
    cannot use correctly. ``PrintSession`` submitted five positional
    arguments against the old signature and every back pass silently took
    the front-side defaults.

    A backend also **owns the session-log record** for what it submits:
    exactly one ``log_print_job`` call per chunk that reached paper, and a
    failure to write it reported through ``PrintResult.error`` rather than
    raised. It is declared here because it is a contract and not an
    implementation detail -- ``PrintSession`` logged each chunk a second
    time, unguarded, which double-counted every entry and turned an
    unwritable log into a reprint of the whole pass.
    """

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
        *,
        side: Literal["front", "back"] = "front",
        rotate_backs: bool = False,
        pass_index: int = 0,
    ) -> PrintResult: ...


@dataclass(frozen=True)
class PrintPass:
    """One physical pass through the printer: an ordered set of sheets."""

    index: int
    sheet_order: list[int]
    side: Literal["front", "back"]
    reload_instruction: str
    rotate_backs: bool


def _axis_word(flip_axis: str) -> str:
    return "long" if flip_axis == "long" else "short"


def _face_word(output_face: str) -> str:
    return "face down" if output_face == "down" else "face up"


def _front_instruction(profile: PrinterProfile) -> str:
    return (
        f"Load paper {_face_word(profile.output_face)}, feed edge "
        f"{profile.feed_edge}, and print pass 1 (fronts)."
    )


def _back_instruction(profile: PrinterProfile) -> str:
    stack_note = (
        "reverse the printed stack (flip the whole stack over) before reloading"
        if profile.reverse_stack
        else "reload the printed stack in the same order, without reversing it"
    )
    return (
        f"After pass 1 finishes, {stack_note}, flip each sheet on its "
        f"{_axis_word(profile.flip_axis)} edge, {_face_word(profile.output_face)}, "
        "and print pass 2 (backs)."
    )


def plan_passes(
    plan: SheetPlan,
    profile: PrinterProfile,
    sheets: Sequence[int] | None = None,
) -> list[PrintPass]:
    """Compute the front and back passes for ``sheets`` (default: all).

    Passing a narrower ``sheets`` sequence (e.g. a single reprinted sheet)
    is the normal path with a smaller input -- there is no separate
    reprint branch.

    ``rotate_backs`` needs the paper as well as the profile: see this
    module's docstring for why ``profile.flip_axis`` alone cannot answer
    it. ``plan.paper_pt`` is the only thing read off ``plan`` besides
    ``Sheet.index``, and it is a property of the plan, not of a sheet --
    the SS-04 zero-diff seam is about ``Sheet``.
    """
    indices = [s.index for s in plan.sheets] if sheets is None else list(sheets)

    front_order = list(indices)
    back_order = list(reversed(indices)) if profile.reverse_stack else list(indices)

    return [
        PrintPass(
            index=0,
            sheet_order=front_order,
            side="front",
            reload_instruction=_front_instruction(profile),
            rotate_backs=False,
        ),
        PrintPass(
            index=1,
            sheet_order=back_order,
            side="back",
            reload_instruction=_back_instruction(profile),
            rotate_backs=profile.flip_axis != duplex_flip_edge(plan.paper_pt),
        ),
    ]


@dataclass(frozen=True)
class PassExport:
    """One pass, expressed as the arguments that write it to a PDF.

    A pass printed here and a pass exported to be printed somewhere else --
    a copy shop, a second machine, a friend's laser -- are the same physical
    pass, so they must come from the same arithmetic. They did not: the CLI
    assembled the ``export`` call for ``--pass`` itself, and the desktop app
    could not export a pass at all.

    :ivar side: which face this pass carries.
    :ivar sheets: the sheet indices in the order they are fed.
    :ivar rotate_180: whether every page needs the half turn a flip about
        the sheet's *horizontal* edge demands -- which named edge that is
        depends on the paper, so it is taken from :func:`plan_passes` and
        never re-derived from ``flip_axis``.
    :ivar back_offset_pt: the profile's measured back-side correction, which
        applies to the back pass and is inert on the front.
    :ivar reload_instruction: what the person at the printer has to do
        before feeding this pass. It travels with the file because whoever
        prints it may never have seen Deckle.
    """

    side: Literal["front", "back"]
    sheets: list[int]
    rotate_180: bool
    back_offset_pt: tuple[float, float]
    reload_instruction: str


def pass_export(
    plan: SheetPlan,
    profile: PrinterProfile,
    side: Literal["front", "back"],
    sheets: Sequence[int] | None = None,
) -> PassExport:
    """The one pass named by ``side``, ready to hand to ``export``.

    Everything comes from :func:`plan_passes`; nothing is recomputed. A
    second implementation of the ordering table would be free to disagree
    with the first about the same printer, and the paper would be wrong
    while both halves looked right.

    :param plan: the imposed sheets.
    :param profile: how the printer hands paper back. Not optional and
        never defaulted -- neither the sheet order nor the half turn has a
        safe default, and guessing prints every back onto the wrong front.
    :param sheets: a narrower selection, or ``None`` for the whole plan.
    :returns: the pass, as export arguments.
    """
    passes = plan_passes(plan, profile, sheets=sheets)
    print_pass = next(p for p in passes if p.side == side)
    return PassExport(
        side=print_pass.side,
        sheets=list(print_pass.sheet_order),
        rotate_180=print_pass.side == "back" and print_pass.rotate_backs,
        back_offset_pt=(profile.back_offset_x_pt, profile.back_offset_y_pt),
        reload_instruction=print_pass.reload_instruction,
    )
