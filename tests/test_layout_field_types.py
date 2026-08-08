"""A stored layout value of the wrong type, or outside its allowed set.

``UnknownLayoutFieldsWarning`` already tells a user that a stored *key* is
not one this build knows. There was no equivalent for a stored *value*,
and the two failures are not symmetric in severity -- an unknown key is
dropped and announced, while a bad value was accepted in silence.

Two shapes, and the second is much worse than the first.

**A wrong type crashed later, somewhere else.** ``{"paper": "big"}``
loaded without complaint and failed inside the imposer with
``unsupported operand type(s) for -: 'str' and 'float'`` -- a traceback,
from a module that has nothing to do with reading files, about a problem
that was fully visible at load time.

**A value outside its ``Literal`` was accepted and changed the book.**
``binding_edge`` is declared ``Literal["left", "right"]`` and nothing
enforced it at runtime, so ``"middle"`` loaded fine and imposed *bound on
the right* -- the opposite of the default -- and ``deckle info`` reported
"layout warnings: none". ``fold_scheme: "quarto"`` fell back to flat
imposition just as quietly. That is the failure this project keeps naming
as the one that matters: a confident, plausible result that is wrong on
paper, with nothing on screen to suggest it.

The command line was never exposed to either. ``argparse`` validates
these with ``choices=`` and with the length parser, so both only reach
the model through a ``.deckle`` -- which is the input red-team A-3
already treats as untrusted, and the one that had no validation at all.

Checked against the dataclass's own resolved annotations rather than a
hand-written schema, so a field added to ``LayoutSettings`` is covered
without anyone remembering to add it here -- the same reasoning as
``tests/test_settings_roundtrip.py``, and for the same failure mode.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import pytest

from deckle.core.models import LayoutSettings
from deckle.core.project_io import load_project

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE = {"paper": [792.0, 612.0], "gutter_pt": 36.0, "binding_edge": "left"}


def _write(tmp_path, **layout) -> str:
    path = os.path.join(str(tmp_path), "job.deckle")
    body = {"version": 1, "pages": [], "layout": {**_BASE, **layout}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f)
    return path


# -- values outside a Literal -------------------------------------------

OUTSIDE_LITERAL = [
    pytest.param("binding_edge", "middle", id="binding-edge"),
    pytest.param("fold_scheme", "quarto", id="fold-scheme"),
    pytest.param("grain", "diagonal", id="grain"),
    pytest.param("landscape_policy", "squash", id="landscape-policy"),
    pytest.param("slack_to", "middle", id="slack-to"),
    pytest.param("blank_mode", "start", id="blank-mode"),
]


@pytest.mark.parametrize("field,value", OUTSIDE_LITERAL)
def test_a_value_outside_its_allowed_set_is_refused(tmp_path, field, value):
    path = _write(tmp_path, **{field: value})

    with pytest.raises(ValueError) as caught:
        load_project(path, check_sources=False)

    assert field in str(caught.value)


@pytest.mark.parametrize("field,value", OUTSIDE_LITERAL)
def test_the_message_lists_what_was_allowed(tmp_path, field, value):
    """The remedy is the list of accepted values, and it is knowable --
    withholding it turns a one-line fix into a search through the source.
    """
    path = _write(tmp_path, **{field: value})

    with pytest.raises(ValueError) as caught:
        load_project(path, check_sources=False)

    allowed = LayoutSettings.__dataclass_fields__[field]
    del allowed  # only used to assert the field exists
    message = str(caught.value)
    assert value in message
    import typing
    for permitted in typing.get_args(typing.get_type_hints(LayoutSettings)[field]):
        assert repr(permitted) in message or str(permitted) in message


def test_the_binding_edge_typo_no_longer_imposes_the_other_way(tmp_path):
    """The specific silent wrong: ``"middle"`` was imposed as though it
    said ``"right"``, so a hand-edited typo produced a book bound on the
    wrong side and reported no warnings."""
    path = _write(tmp_path, binding_edge="middle")

    with pytest.raises(ValueError):
        load_project(path, check_sources=False)


# -- values of the wrong type -------------------------------------------

WRONG_TYPE = [
    pytest.param("paper", "big", id="paper-is-text"),
    pytest.param("paper", [792.0], id="paper-has-one-number"),
    pytest.param("paper", [792.0, 612.0, 100.0], id="paper-has-three-numbers"),
    pytest.param("paper", ["792", "612"], id="paper-holds-text"),
    pytest.param("gutter_pt", "wide", id="gutter-is-text"),
    pytest.param("sewing_stations", "three", id="stations-is-text"),
    pytest.param("start_on_recto", "yes", id="recto-is-text"),
    pytest.param("trim_pt", None, id="trim-is-null"),
    pytest.param("crop_odd_pt", [1.0, 2.0], id="crop-has-two-numbers"),
    pytest.param("signature_lengths", ["a"], id="lengths-hold-text"),
]


@pytest.mark.parametrize("field,value", WRONG_TYPE)
def test_a_value_of_the_wrong_type_is_refused_at_load(tmp_path, field, value):
    path = _write(tmp_path, **{field: value})

    with pytest.raises(ValueError) as caught:
        load_project(path, check_sources=False)

    assert field in str(caught.value)


@pytest.mark.parametrize("field,value", WRONG_TYPE)
def test_the_cli_reports_it_without_a_traceback(tmp_path, field, value):
    path = _write(tmp_path, **{field: value})

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")


# -- what must keep working ---------------------------------------------


def test_a_whole_number_still_satisfies_a_float_field(tmp_path):
    """JSON does not distinguish ``36`` from ``36.0``, and a project
    written by hand -- or by a future encoder -- may carry either."""
    path = _write(tmp_path, gutter_pt=36)

    assert load_project(path, check_sources=False).layout.gutter_pt == 36


def test_an_optional_field_still_accepts_null(tmp_path):
    path = _write(tmp_path, crop_odd_pt=None, signature_lengths=None)

    layout = load_project(path, check_sources=False).layout

    assert layout.crop_odd_pt is None
    assert layout.signature_lengths is None


def test_every_default_value_satisfies_its_own_declared_type(tmp_path):
    """The check has to accept what Deckle itself writes, and the cheapest
    way to be sure is to round-trip the defaults through it."""
    defaults = {
        field.name: getattr(LayoutSettings(**{
            "paper": (792.0, 612.0), "gutter_pt": 36.0,
            "binding_edge": "left",
        }), field.name)
        for field in dataclasses.fields(LayoutSettings)
    }
    defaults["paper"] = list(defaults["paper"])
    path = _write(tmp_path, **defaults)

    load_project(path, check_sources=False)


def test_an_unknown_key_is_still_only_a_warning(tmp_path):
    """Unchanged, and the distinction is deliberate: an unknown *key* is
    field drift between builds and must stay openable, while an unknown
    *value* is a document asking for something this build cannot do."""
    from deckle.core.project_io import UnknownLayoutFieldsWarning

    path = _write(tmp_path, some_future_setting=1)

    with pytest.warns(UnknownLayoutFieldsWarning):
        load_project(path, check_sources=False)
