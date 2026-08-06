"""The PassPlanner: turns a SheetPlan + PrinterProfile into print passes.

Manual duplex on a home/office printer means two passes: fronts, then a
manual reload, then backs. This module computes the ordered sheets for
each pass and a plain-language reload instruction -- no submission, no
Qt, no I/O beyond the profile persistence in ``deckle/core/profiles.py``.
``PrintBackend`` is a ``Protocol`` so this module never imports Qt or a
concrete print API; SS-08 supplies the real backend.

Verified back-pass ordering table (see
``[[pikepdf - Manual Duplex Reordering]]``) -- this is the physical
behavior ``plan_passes`` implements:

| Reload behavior                                    | Back-pass order |
|------------------------------------------------------|-----------------|
| Outputs face-down, stack reversed on reload           | backs as-is     |
| Outputs face-up, stack retains order                  | backs reversed  |
| User flips on the long edge                           | back sides may also need 180deg rotation |
| User flips on the short edge                          | usually no rotation |

Mapping from ``PrinterProfile`` to the two behavioral axes ``plan_passes``
consumes:

- ``profile.reverse_stack`` (derived, at calibration/preset time, from
  ``output_face`` + ``feed_edge``) drives **sheet order** on the back
  pass: ``True`` reverses it, ``False`` keeps it identical to the front
  pass.
- ``profile.flip_axis == "long"`` drives **``rotate_backs``**: a long-edge
  flip needs the back sides rotated 180 degrees to land right-side up;
  a short-edge flip does not.

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
    """A pluggable print submission target. Never imports Qt."""

    def submit(
        self,
        plan: SheetPlan,
        sheets: Sequence[int],
        printer_name: str,
        copies: int,
        dpi: int,
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
            rotate_backs=profile.flip_axis == "long",
        ),
    ]
