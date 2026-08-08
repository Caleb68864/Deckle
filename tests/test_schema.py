"""The stored-value checker, tested against annotation shapes directly.

``schema`` was extracted from three callers and immediately had a hole
that none of them could reveal: ``describes`` fell through to
``isinstance(value, hint)`` for any annotation it did not recognise, and
``isinstance(x, list[int])`` raises ``TypeError: isinstance() argument 2
cannot be a parameterized generic``. It would have crashed on the first
``list`` field it was pointed at. It passed everything only because the
three dataclasses it was extracted from all happen to use ``tuple``.

That is the argument for this file. A helper factored out of three
callers gets tested against three shapes, and the shape that breaks it is
the fourth -- which is precisely the one it was extracted to serve. So
the coverage here is over the *annotation vocabulary*, not over the
classes that currently use it.

The second thing pinned here is the pair of deliberate looseness
decisions. JSON's type system is coarser than Python's in exactly two
ways that matter, and if either allowance were removed Deckle would start
rejecting files it wrote itself -- a failure that would look like
corruption to the user and would reach them through every reader at once.
"""

from __future__ import annotations

import dataclasses
from typing import Literal, Optional

import pytest

from deckle.core.schema import (
    StoredValueError, check_values, describe, describes,
)


# -- the annotation vocabulary ------------------------------------------

ACCEPTED = [
    pytest.param(3, int, id="int"),
    pytest.param(3.5, float, id="float"),
    pytest.param(True, bool, id="bool"),
    pytest.param("x", str, id="str"),
    pytest.param(None, type(None), id="none"),
    pytest.param("left", Literal["left", "right"], id="literal"),
    pytest.param([1, 2], list[int], id="list"),
    pytest.param([], list[int], id="empty-list"),
    pytest.param([1.0, 2.0], tuple[float, float], id="fixed-tuple"),
    pytest.param([1, 2, 3], tuple[int, ...], id="variadic-tuple"),
    pytest.param(None, Optional[tuple[int, ...]], id="optional-none"),
    pytest.param([1], Optional[tuple[int, ...]], id="optional-value"),
    pytest.param([[1], [2]], list[list[int]], id="nested-list"),
]

REJECTED = [
    pytest.param("3", int, id="text-for-int"),
    pytest.param(3.5, int, id="fraction-for-int"),
    pytest.param("x", float, id="text-for-float"),
    pytest.param("yes", bool, id="text-for-bool"),
    pytest.param(7, str, id="number-for-text"),
    pytest.param("middle", Literal["left", "right"], id="outside-literal"),
    pytest.param(["a"], list[int], id="wrong-item-in-list"),
    pytest.param("abc", list[int], id="text-for-list"),
    pytest.param([1.0], tuple[float, float], id="short-fixed-tuple"),
    pytest.param([1.0, 2.0, 3.0], tuple[float, float], id="long-fixed-tuple"),
    pytest.param("wide", tuple[float, float], id="text-for-tuple"),
    pytest.param(["a"], tuple[int, ...], id="wrong-item-in-variadic"),
    pytest.param("x", Optional[tuple[int, ...]], id="text-for-optional"),
    pytest.param([["a"]], list[list[int]], id="wrong-item-nested"),
]


@pytest.mark.parametrize("value,hint", ACCEPTED)
def test_a_value_that_satisfies_its_annotation_is_accepted(value, hint):
    assert describes(value, hint)


@pytest.mark.parametrize("value,hint", REJECTED)
def test_a_value_that_does_not_is_rejected(value, hint):
    assert not describes(value, hint)


@pytest.mark.parametrize("value,hint", ACCEPTED + REJECTED)
def test_no_annotation_shape_makes_the_checker_raise(value, hint):
    """The defect this file exists for: a shape the checker does not
    recognise must answer the question, not crash on it. ``isinstance``
    against a parameterized generic raises, and the fallback used to reach
    it."""
    describes(value, hint)


# -- JSON is coarser than Python, in exactly two ways -------------------


def test_a_whole_number_satisfies_a_float_field():
    """``36`` and ``36.0`` are one number in JSON. Rejecting the first
    would make Deckle refuse files it wrote itself."""
    assert describes(36, float)


def test_a_fraction_does_not_satisfy_an_int_field():
    """The allowance runs one way only: 4.5 is not a sheet count."""
    assert not describes(4.5, int)


def test_a_list_satisfies_a_tuple_field():
    """Every tuple field arrives from JSON as a list and is converted back
    afterwards, so the check has to run against the list."""
    assert describes([792.0, 612.0], tuple[float, float])


@pytest.mark.parametrize("hint", [int, float])
def test_true_is_not_a_number(hint):
    """``bool`` is a subclass of ``int``, so ``True`` would otherwise pass
    for a page count or a measurement."""
    assert not describes(True, hint)


def test_a_number_is_not_a_true_or_false():
    assert not describes(1, bool)


# -- the message is the remedy ------------------------------------------


def test_a_literal_is_spelled_out_in_full():
    """The whole value of ``describe``: the fix for ``binding_edge:
    "middle"`` *is* the list of accepted values."""
    text = describe(Literal["left", "right"])

    assert "'left'" in text and "'right'" in text


@pytest.mark.parametrize("hint,expected", [
    (int, "whole number"), (float, "number"), (bool, "true or false"),
    (str, "text"), (list[int], "list"), (tuple[float, float], "list of 2"),
])
def test_each_shape_is_named_in_terms_the_files_author_sees(hint, expected):
    assert expected in describe(hint)


def test_an_optional_names_both_arms():
    text = describe(Optional[int])

    assert "whole number" in text and "null" in text


# -- check_values over a dataclass --------------------------------------


@dataclasses.dataclass(frozen=True)
class _Example:
    count: int
    size: tuple[float, float]
    edge: Literal["left", "right"]
    tags: list[str]
    note: str | None = None


def test_a_conforming_set_of_values_passes():
    check_values(_Example, {
        "count": 2, "size": [1.0, 2.0], "edge": "left",
        "tags": ["a"], "note": None,
    }, subject="example field")


@pytest.mark.parametrize("field,value", [
    ("count", "two"), ("size", "big"), ("edge", "middle"),
    ("tags", [1]), ("note", 7),
])
def test_a_bad_value_names_the_field_it_was_read_into(field, value):
    values = {"count": 2, "size": [1.0, 2.0], "edge": "left", "tags": ["a"]}
    values[field] = value

    with pytest.raises(StoredValueError) as caught:
        check_values(_Example, values, subject="example field")

    assert field in str(caught.value)


def test_the_message_carries_the_subject_it_was_given():
    """Four readers share this check, so the message has to say which one
    is complaining -- "layout setting", "printer profile field"."""
    with pytest.raises(StoredValueError) as caught:
        check_values(_Example, {"count": "two"}, subject="printer profile field")

    assert "printer profile field" in str(caught.value)


def test_it_is_a_value_error_so_existing_branches_catch_it():
    """Every caller already had a ``ValueError`` branch that reports
    cleanly. Making this a subclass is what let all four adopt the check
    without growing a new ``except``."""
    with pytest.raises(ValueError):
        check_values(_Example, {"count": "two"}, subject="example field")


def test_only_the_values_given_are_checked():
    """Callers filter to known keys first, and a field left out is one the
    dataclass default will supply -- checking it here would reject every
    project written by an older build."""
    check_values(_Example, {"count": 1}, subject="example field")
