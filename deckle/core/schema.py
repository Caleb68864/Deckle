"""Checking stored values against the dataclass that declares them.

Three of Deckle's readers turned a JSON object straight into a frozen
dataclass -- ``LayoutSettings``, ``PrinterProfile``, and a project's page
entries -- and none of them checked anything on the way. The 2026-08-04
entry already named ``Type(**stored_dict)`` as the shape that breaks on
the next field change. What it did not say, and three separate defects
have since shown, is that it does not only break on drift: **it accepts
anything**, and a frozen dataclass will hold a string where it declared a
float without a word.

Each one produced a confidently wrong result rather than an error:

- ``binding_edge: "middle"`` is outside ``Literal["left", "right"]``, and
  imposed a book bound on the *right* while ``deckle info`` reported no
  warnings.
- ``flip_axis: "diagonal"`` is outside ``Literal["long", "short"]``, and
  planned the back pass as though the operator flips on the short edge --
  so on a printer that flips long-edge, every back side prints upside
  down. A whole stack of paper, silently.
- ``page_index: -2`` is a valid Python index, and printed the
  second-from-last page of the document instead of failing.

This module is that check, written once. It reads the target class's own
resolved annotations rather than a hand-maintained schema, so a field
added to any of those dataclasses is covered without anyone remembering
to register it here -- the same reasoning as the exhaustive round-trip
tests, and against the same failure.

It exists as its own module for the reason ``paths`` does: three copies
of a rule is three chances for one of them to drift, and here the three
copies would each be guarding a different dataclass, so the drift would
be invisible until it mattered.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

from __future__ import annotations

import types
import typing
from typing import Any

__all__ = ["describes", "describe", "check_values", "StoredValueError"]


class StoredValueError(ValueError):
    """A stored value cannot be honoured by the field it was read into.

    A ``ValueError`` subclass on purpose: both readers of stored data
    already have a ``ValueError`` branch that reports cleanly, so this
    reaches the user as a message rather than as a traceback without
    either of them growing a new ``except``.
    """


def describes(value: Any, hint: Any) -> bool:
    """Whether ``value``, as JSON decoded it, satisfies the annotation ``hint``.

    Handles the four annotation shapes Deckle's stored dataclasses use --
    a plain class, a ``Literal``, a ``tuple[...]`` (fixed or variadic),
    and a union with ``None``.

    Two places where JSON's type system is coarser than Python's, and both
    have to be allowed or Deckle would reject documents it wrote itself:

    - **A whole number decodes as ``int``.** ``36`` and ``36.0`` are the
      same number in JSON, so an ``int`` satisfies a ``float`` field. The
      reverse is not true: ``4.5`` is not a sheet count.
    - **A tuple decodes as a list.** Every tuple field arrives as a list
      and is converted back afterwards, so a list satisfies a ``tuple``
      annotation here.

    ``bool`` is checked before ``int`` throughout, because it is a
    subclass of ``int`` and ``True`` would otherwise pass for a count.

    :param value: the decoded JSON value.
    :param hint: the resolved annotation to check it against.
    :returns: whether the value is acceptable.
    """
    origin = typing.get_origin(hint)
    if origin is typing.Literal:
        return value in typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        return any(describes(value, arm) for arm in typing.get_args(hint))
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            return False
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return all(describes(item, args[0]) for item in value)
        return len(value) == len(args) and all(
            describes(item, arm) for item, arm in zip(value, args)
        )
    if hint is bool:
        return isinstance(value, bool)
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if hint is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if hint is type(None):
        return value is None
    return isinstance(value, hint)


def describe(hint: Any) -> str:
    """Name what a field accepts, in the terms the file's author sees.

    A ``Literal`` is spelled out in full, and that is the whole value of
    this function: the remedy for ``binding_edge: "middle"`` *is* the list
    of accepted values, and withholding it turns a one-line fix into a
    search through the source.

    :param hint: the resolved annotation.
    :returns: a phrase that completes "this build accepts ...".
    """
    origin = typing.get_origin(hint)
    if origin is typing.Literal:
        return "one of " + ", ".join(repr(arg) for arg in typing.get_args(hint))
    if origin in (typing.Union, types.UnionType):
        return " or ".join(describe(arm) for arm in typing.get_args(hint))
    if origin is tuple:
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return f"a list of {describe(args[0])}"
        return f"a list of {len(args)} {describe(args[0])}"
    return {
        bool: "true or false", int: "a whole number",
        float: "a number", str: "a piece of text", type(None): "null",
    }.get(hint, getattr(hint, "__name__", str(hint)))


def check_values(cls: type, values: dict[str, Any], *, subject: str) -> None:
    """Reject a stored value that ``cls`` cannot honour.

    Deliberately harsher than the treatment of an unknown *key*, which
    every one of these readers drops with a warning so a project or
    profile stays openable across field drift. The asymmetry is the point:
    a key this build does not know is a setting it can safely ignore,
    while a value outside a field's declared set is the document asking
    for something this build cannot do -- and guessing is how
    ``binding_edge: "middle"`` came to mean ``"right"``.

    :param cls: the dataclass the values are destined for. Its resolved
        annotations are the schema.
    :param values: stored values, already filtered to known field names.
    :param subject: what to call the thing in the message -- "layout
        setting", "printer profile field".
    :returns: nothing.
    :raises StoredValueError: a value does not satisfy its field, naming
        the field, what was found, and what would have been accepted.
    """
    hints = typing.get_type_hints(cls)
    for name, value in values.items():
        hint = hints[name]
        if not describes(value, hint):
            raise StoredValueError(
                f"{subject} {name!r} is {value!r}, but this build of Deckle "
                f"accepts {describe(hint)}"
            )
