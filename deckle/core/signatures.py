"""Pure arithmetic for grouping sheets into signatures and ordering pages.

Three independent pieces:

- ``split_signatures`` -- how sheets are grouped into signatures.
- ``saddle_order`` -- the closed-form permutation that answers "given n
  pages folded and nested as one signature, which page goes in which
  physical print slot". This is the executed, verified recipe from
  ``[[pikepdf - Imposition and Signature Recipe]]`` (vault path
  ``C:\\Users\\CalebBennett\\Documents\\Notes\\Caleb's Vault\\Software\\pikepdf\\pikepdf - Imposition and Signature Recipe.md``,
  lines 35-44), which ran end to end against pikepdf 10.11.0 / libqpdf
  12.3.2 on this machine. That note's bindery half -- the physical folding
  and trimming steps beyond the page-order math -- is explicitly flagged
  there as unverified; only the ``saddle_order`` function itself, and its
  ``n=8 -> [7, 0, 1, 6, 5, 2, 3, 4]`` executed output, are relied on here.
- ``fold_reading_order`` -- the same question, answered independently by
  simulating the physical fold instead of reusing the closed-form
  arithmetic. ``saddle_order`` and ``fold_reading_order`` must never share
  an implementation: if the closed form were subtly wrong in a way that
  ``fold_reading_order`` encoded identically, the round trip between them
  would falsely pass and the printed book would come out of order. Two
  independently-derived answers that agree are the only useful check.

No I/O, no Qt -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

from collections import deque

from deckle.core.models import SheetPlan


def split_signatures(
    sheet_count: int, sheets_per_signature: int
) -> list[tuple[int, ...]]:
    """Group sheet indices ``0..sheet_count-1`` into contiguous signatures.

    Every group but the last has exactly ``sheets_per_signature`` sheets;
    the final group carries the remainder and may be shorter. Concatenating
    every group's contents, in order, reproduces ``range(sheet_count)``
    exactly -- no gaps, no overlaps, no reordering.

    ``blank_mode="balanced"`` padding is a caller concern (page-padding
    happens at a higher level) -- this function only ever groups the sheet
    indices it is given.

    :param sheet_count: how many sheets there are. ``0`` yields no groups.
    :param sheets_per_signature: the size of every group but the last.
    :returns: the groups, in binding order.
    :raises ValueError: if ``sheet_count`` is negative, or
        ``sheets_per_signature`` is not a positive integer.
    """

    if sheet_count < 0:
        raise ValueError(f"sheet_count must be >= 0, got {sheet_count}")
    if sheets_per_signature <= 0:
        raise ValueError(
            f"sheets_per_signature must be a positive integer, got {sheets_per_signature}"
        )

    groups: list[tuple[int, ...]] = []
    for start in range(0, sheet_count, sheets_per_signature):
        end = min(start + sheets_per_signature, sheet_count)
        groups.append(tuple(range(start, end)))
    return groups


def saddle_order(n: int) -> list[int]:
    """The saddle-stitch page-to-slot permutation for an ``n``-page signature.

    Executed, verified recipe from ``[[pikepdf - Imposition and Signature
    Recipe]]`` (lines 35-44). Reading two positions at a time gives each
    sheet's front then back: sheet 1 front is ``(p_n, p_1)``, sheet 1 back
    is ``(p_2, p_{n-1})``, and so on inward. The outermost sheet -- the
    first four entries of the result -- carries the very first and very
    last pages of the document. That is the defining property of a
    *nested* (saddle) gathering, as opposed to a *stacked* one where the
    outermost sheet would carry consecutive pages instead.

    Raises ``ValueError`` unless ``n`` is a positive multiple of 4 -- a
    saddle signature always folds to a whole number of four-page sheets.

    :param n: the signature's page count.
    :returns: for each physical print slot in order, the reading-order page
        index that belongs in it. ``n=8`` gives
        ``[7, 0, 1, 6, 5, 2, 3, 4]``.
    :raises ValueError: unless ``n`` is a positive multiple of 4.
    """

    if n <= 0 or n % 4 != 0:
        raise ValueError(f"n must be a positive multiple of 4, got {n}")

    seq: list[int] = []
    lo, hi = 0, n - 1
    while lo < hi:
        seq += [hi, lo, lo + 1, hi - 1]
        lo += 2
        hi -= 2
    return seq


def fold_reading_order(plan: SheetPlan) -> list[int]:
    """The reading-order page index for every physical print slot in ``plan``.

    Derived independently of ``saddle_order`` -- deliberately not by
    evaluating the same closed-form arithmetic under a different name, but
    by simulating the physical fold: each signature is a stack of sheets,
    outermost to innermost. Folding and nesting that stack means the
    outermost sheet is the one whose two faces wrap every other sheet, so
    physically it is peeled off both ends of the page run at once: its
    front carries the very first and very last page of the signature, its
    back carries the next-in and next-from-last page. Peeling the next
    sheet off the (now shorter) remaining run gives the next sheet inward,
    and so on until the run is exhausted -- which is exactly "unfolding
    each sheet into its four leaves in the order a reader encounters them,
    outermost to innermost".

    Signatures are walked in ``plan.signatures`` order and their resulting
    slot orders concatenated, each offset by the page count of every
    signature already placed, so this function's output lines up 1:1 with
    ``saddle_order``'s per-signature output when the two are compared.

    :param plan: the imposed plan. Only ``plan.signatures`` is read -- a
        plan with no signatures yields an empty list.
    :returns: the reading-order page index for every physical print slot,
        concatenated across signatures in binding order.
    """

    order: list[int] = []
    offset = 0
    for signature in plan.signatures:
        sheet_count = len(signature.sheet_indices)
        page_count = 4 * sheet_count
        remaining = deque(range(offset, offset + page_count))

        # Peel one sheet's worth of leaves off both ends of the run per
        # loop: this sheet is outermost relative to whatever is left.
        while remaining:
            front_outer_edge = remaining.pop()
            front_spine_edge = remaining.popleft()
            back_spine_edge = remaining.popleft()
            back_outer_edge = remaining.pop()
            order += [
                front_outer_edge,
                front_spine_edge,
                back_spine_edge,
                back_outer_edge,
            ]

        offset += page_count
    return order
