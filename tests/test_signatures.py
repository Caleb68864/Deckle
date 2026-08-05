"""Tests for deckle.core.signatures: split_signatures, saddle_order,
fold_reading_order.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from deckle.core.models import Signature, SheetPlan
from deckle.core.signatures import fold_reading_order, saddle_order, split_signatures

SHEET_COUNTS = [1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 25, 67, 100]
SHEETS_PER_SIGNATURE = [1, 2, 3, 4, 6, 8]


def test_split_signatures_properties_hold_across_counts():
    for sheet_count in SHEET_COUNTS:
        for sheets_per_signature in SHEETS_PER_SIGNATURE:
            groups = split_signatures(sheet_count, sheets_per_signature)

            # Contiguous: every group is a run of consecutive integers.
            for group in groups:
                assert list(group) == list(range(group[0], group[0] + len(group)))

            # Concatenated, groups reproduce range(sheet_count) exactly:
            # no gaps, no overlaps, no reordering.
            flattened = [i for group in groups for i in group]
            assert flattened == list(range(sheet_count))

            # Only the final group may be short.
            for group in groups[:-1]:
                assert len(group) == sheets_per_signature
            if groups:
                assert 1 <= len(groups[-1]) <= sheets_per_signature


def test_split_signatures_single_group_when_sheets_per_signature_exceeds_count():
    groups = split_signatures(3, 8)
    assert groups == [(0, 1, 2)]


def test_split_signatures_empty_when_sheet_count_zero():
    assert split_signatures(0, 4) == []


def test_split_signatures_rejects_non_positive_sheets_per_signature():
    with pytest.raises(ValueError):
        split_signatures(10, 0)


def test_saddle_order_is_permutation_for_multiples_of_4():
    for n in range(4, 129, 4):
        result = saddle_order(n)
        assert sorted(result) == list(range(n))


def test_saddle_order_pinned_against_vault_note_n8():
    assert saddle_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]


def test_saddle_order_outermost_sheet_carries_first_and_last_page():
    for n in range(4, 129, 4):
        result = saddle_order(n)
        assert result[:2] == [n - 1, 0]


def test_saddle_order_raises_on_non_multiple_of_4():
    for bad_n in (1, 2, 3, 5, 6, 7, 9, 10, 11):
        with pytest.raises(ValueError):
            saddle_order(bad_n)


def test_saddle_order_raises_on_non_positive():
    for bad_n in (0, -4, -8):
        with pytest.raises(ValueError):
            saddle_order(bad_n)


def _plan_with_signatures(sheet_counts_per_signature):
    """Build a minimal SheetPlan whose signatures have the given sheet counts.

    Sheets themselves are irrelevant to fold_reading_order -- only
    signature.sheet_indices' length is used -- so `sheets` is left empty.
    """

    signatures = []
    start = 0
    for index, sheet_count in enumerate(sheet_counts_per_signature):
        sheet_indices = tuple(range(start, start + sheet_count))
        signatures.append(Signature(index=index, sheet_indices=sheet_indices, blank_count=0))
        start += sheet_count
    return SheetPlan(
        sheets=[],
        paper_pt=(612.0, 792.0),
        warnings=[],
        signatures=tuple(signatures),
    )


def test_fold_reading_order_matches_saddle_order_for_single_signature():
    plan = _plan_with_signatures([2])
    assert fold_reading_order(plan) == saddle_order(8)


def test_fold_reading_order_concatenates_across_signatures_with_offset():
    plan = _plan_with_signatures([2, 1])
    result = fold_reading_order(plan)

    first_signature = result[:8]
    second_signature = result[8:]

    assert first_signature == saddle_order(8)
    # Second signature's slots are saddle_order(4) shifted up by the first
    # signature's page count (8).
    assert second_signature == [p + 8 for p in saddle_order(4)]


def test_fold_reading_order_returns_permutation_of_range():
    plan = _plan_with_signatures([3, 2, 1])
    result = fold_reading_order(plan)
    total_pages = 4 * (3 + 2 + 1)
    assert sorted(result) == list(range(total_pages))


def test_fold_reading_order_empty_plan_returns_empty_list():
    plan = _plan_with_signatures([])
    assert fold_reading_order(plan) == []


def test_fold_reading_order_does_not_call_saddle_order():
    from deckle.core import signatures as signatures_module

    source = inspect.getsource(signatures_module.fold_reading_order)
    tree = ast.parse(source)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    assert func_def.name == "fold_reading_order"

    for node in ast.walk(func_def):
        if isinstance(node, ast.Name):
            assert node.id != "saddle_order"
        if isinstance(node, ast.Attribute):
            assert node.attr != "saddle_order"
