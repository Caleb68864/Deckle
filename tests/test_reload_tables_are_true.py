"""The two documented tables that tell an operator what to do with paper.

`README.md` and `docs/GUIDE.md` each carry a small truth table: which order
the backs are fed in, and whether the back faces get a half turn. They are
the only thing in this repository a person reads *before touching a stack*,
and both had drifted out of agreement with the code:

* the README had the stack-order rows **inverted** -- it said a face-down
  printer means "feed the backs as printed", when `generic_face_down_reversed`
  carries `reverse_stack=True` and `plan_passes` feeds `[3, 2, 1, 0]`;
* **both** documents carried the pre-fix rotation rule, `flip_axis: long`
  -> rotated. That rule was replaced by
  `rotate_backs = flip_axis != duplex_flip_edge(plan.paper_pt)` precisely
  because a per-printer constant is right for one orientation and upside
  down for the other -- and on portrait paper, which is the default
  flat-sheet path, `long` now means *not* rotated. The code changed; the
  two tables teaching the old rule did not.

That is the drift shape this repository keeps finding: a rule maintained in
more than one place with nothing making the copies agree. So rather than
correcting the tables and leaving them free to drift again, the tables are
**parsed and checked against `plan_passes` itself**. Edit the rule in the
code and these go red. Edit a table wrongly and these go red.

Deliberately narrow, like `test_docs_are_current.py`: it checks the rows
that state a mechanical fact, not the prose around them.

**Mutations it catches**, run rather than claimed: flipping one README row
back to the old answer reddens it; changing `plan_passes` back to
`flip_axis == "long"` reddens it in both documents.

**Mutations it does not catch**, which matters more:

* prose *around* the tables that contradicts them. The tripwire at the end
  covers the two exact rows that were wrong and nothing else, so a fresh
  wrong sentence in English goes unnoticed.
* a table moved into a third document. Only these two files are read.
* `reverse_stack` drifting away from `output_face`: the pairing is asserted
  for the two shipped presets and nowhere else, because nothing in the code
  derives one from the other -- a calibration supplies both.
* the *physical* premise underneath all of it. The rotation rule agrees
  with `plan_passes`; whether `plan_passes` agrees with a real printer rests
  on a note cited at `core/printing.py:10-19` that is not in this
  repository, and no test can close that. It wants one proof sheet.
"""

from __future__ import annotations

import dataclasses
import os
import re

import pytest

from deckle.core.models import OutputPage, Placement, Sheet, SheetPlan, Side
from deckle.core.printing import plan_passes
from deckle.core.profiles import BUILTIN_PRESETS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = {
    "README.md": os.path.join(ROOT, "README.md"),
    "docs/GUIDE.md": os.path.join(ROOT, "docs", "GUIDE.md"),
}

PAPER = {"portrait": (612.0, 792.0), "landscape": (792.0, 612.0)}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _plan(paper: tuple[float, float]) -> SheetPlan:
    blank = OutputPage(
        source_ref=None,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0),
        is_filler=True,
    )
    sheets = [
        Sheet(index=i, front=Side(pages=(blank,)), back=Side(pages=(blank,)))
        for i in range(4)
    ]
    return SheetPlan(sheets=sheets, paper_pt=paper, warnings=[])


#: ``| portrait | `long` | not rotated |`` and its three siblings.
_ROTATION_ROW = re.compile(
    r"^\|\s*(portrait|landscape)\s*\|\s*`(long|short)`\s*\|\s*"
    r"(not rotated|rotated 180°)\s*\|\s*$",
    re.MULTILINE,
)

#: ``| `true` | ... | in **reverse** order |`` and its sibling.
_STACK_ROW = re.compile(
    r"^\|\s*`(true|false)`\s*\|[^|]*\|\s*in the \*\*(same)\*\* order\s*\|\s*$"
    r"|^\|\s*`(true|false)`\s*\|[^|]*\|\s*in \*\*(reverse)\*\* order\s*\|\s*$",
    re.MULTILINE,
)


def _rotation_rows(text: str) -> list[tuple[str, str, bool]]:
    return [
        (orientation, axis, verdict == "rotated 180°")
        for orientation, axis, verdict in _ROTATION_ROW.findall(text)
    ]


def _stack_rows(text: str) -> list[tuple[bool, bool]]:
    rows = []
    for match in _STACK_ROW.finditer(text):
        flag = match.group(1) or match.group(3)
        order = match.group(2) or match.group(4)
        rows.append((flag == "true", order == "reverse"))
    return rows


@pytest.mark.parametrize("name", sorted(DOCS))
def test_the_rotation_table_is_complete(name):
    """All four combinations, or the table is teaching half a rule.

    The defect it replaced was exactly a two-row table -- "long: rotated,
    short: not" -- which cannot express an answer that depends on the paper.
    """
    rows = _rotation_rows(_read(DOCS[name]))
    assert len(rows) == 4, (
        f"{name} states {len(rows)} of the four paper x flip_axis "
        f"combinations; found {rows}"
    )
    assert {(o, a) for o, a, _ in rows} == {
        (o, a) for o in PAPER for a in ("long", "short")
    }


@pytest.mark.parametrize("name", sorted(DOCS))
def test_the_rotation_table_matches_plan_passes(name):
    """Every row, computed rather than remembered."""
    rows = _rotation_rows(_read(DOCS[name]))
    assert rows, f"{name} has no rotation table for this test to check"

    preset = BUILTIN_PRESETS["generic_face_down_reversed"]
    for orientation, axis, documented in rows:
        profile = dataclasses.replace(preset, flip_axis=axis)
        back = plan_passes(_plan(PAPER[orientation]), profile)[1]
        assert back.rotate_backs is documented, (
            f"{name} says {orientation} paper with flip_axis={axis!r} gives "
            f"{'a 180° rotation' if documented else 'no rotation'}, but "
            f"plan_passes returns rotate_backs={back.rotate_backs}. This is "
            "the sentence an operator follows before touching a stack."
        )


@pytest.mark.parametrize("name", sorted(DOCS))
def test_the_stack_order_table_matches_plan_passes(name):
    """The half the README had inverted outright."""
    rows = _stack_rows(_read(DOCS[name]))
    assert len(rows) == 2, (
        f"{name} states {len(rows)} of the two reverse_stack rows; found {rows}"
    )

    preset = BUILTIN_PRESETS["generic_face_down_reversed"]
    for reverse_stack, documented_reversed in rows:
        profile = dataclasses.replace(preset, reverse_stack=reverse_stack)
        back = plan_passes(_plan(PAPER["portrait"]), profile)[1]
        actually_reversed = back.sheet_order == list(reversed(range(4)))
        assert actually_reversed is documented_reversed, (
            f"{name} says reverse_stack={reverse_stack} feeds the backs in "
            f"{'reverse' if documented_reversed else 'the same'} order, but "
            f"plan_passes feeds {back.sheet_order}"
        )


def test_the_shipped_presets_are_the_ones_the_tables_describe():
    """The tables talk about `reverse_stack` and the prose ties it to which
    face the printer ejects. That tie is a claim about the built-ins, and it
    is the claim the README got backwards, so it is asserted here rather
    than left to the reader."""
    assert BUILTIN_PRESETS["generic_face_down_reversed"].output_face == "down"
    assert BUILTIN_PRESETS["generic_face_down_reversed"].reverse_stack is True
    assert BUILTIN_PRESETS["generic_face_up_in_order"].output_face == "up"
    assert BUILTIN_PRESETS["generic_face_up_in_order"].reverse_stack is False


@pytest.mark.parametrize("name", sorted(DOCS))
def test_neither_document_still_teaches_the_superseded_rule(name):
    """The exact sentence that was wrong, kept as a tripwire.

    The table checks above would pass on a document that states the four
    rows correctly *and* repeats the old two-row rule somewhere else in the
    same section. That is how the rule survived its own fix the first time.
    """
    text = _read(DOCS[name])
    for stale in (
        "| `flip_axis: long` | Back faces are **rotated 180°** |",
        "| You flip each sheet on its **long** edge | Back sides need a 180° rotation |",
    ):
        assert stale not in text, (
            f"{name} still carries the pre-fix rule {stale!r}, which is "
            "upside down for portrait paper -- the default flat-sheet path"
        )
