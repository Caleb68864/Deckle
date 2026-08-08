"""A ``.deckle`` that is valid JSON but not a project object.

``load_project`` documents ``:raises KeyError: the file is JSON but not a
``.deckle`` document``, and the CLI has a carefully written branch for
exactly that -- it exists because ``str(KeyError)`` is the bare key, so
the default branch used to produce "error: cannot open job.deckle:
'pages'", which names the problem only to someone who already knows the
format.

That contract held for one shape of wrong file and not the others. A
document whose top level is an object missing ``pages`` raised
``KeyError`` and got the good message. A document whose top level is an
*array*, a string, a number, ``null`` or ``true`` reached
``payload["pages"]`` and raised ``TypeError`` instead -- caught by
nothing, so the CLI printed a traceback and the desktop app showed the
user "list indices must be integers or slices, not str".

Both are the wrong side of the line this program draws. ``cli.main``'s
own docstring puts it plainly: a user's problem gets a message naming the
remedy, and a traceback is a bug report. A hand-edited or truncated
project file is a user's problem.

It is also untrusted input. Red-team A-3 already establishes that
``.deckle`` files carry filesystem paths and may be shared, which is why
``load_project`` checks source paths against allowed roots -- so a
one-byte file crashing the tool is a gap in something already treated as
adversarial.

Fixed in ``load_project`` rather than in the CLI's exception ladder,
because there are two callers and only one of them was being fixed: the
desktop app has its own ladder, and a rule about what a project file *is*
belongs to the format, not to one of its readers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from deckle.core.project_io import load_project

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every JSON top-level type that is not an object. Enumerated rather than
# sampled: each one produces a differently-worded `TypeError`, and the
# defect was that none of them were considered at all.
NOT_OBJECTS = [
    pytest.param("[]", id="empty-array"),
    pytest.param('[{"path": "a.pdf"}]', id="array-of-pages"),
    pytest.param('"a string"', id="string"),
    pytest.param("123", id="number"),
    pytest.param("null", id="null"),
    pytest.param("true", id="boolean"),
]


def _write(tmp_path, body: str) -> str:
    path = os.path.join(str(tmp_path), "job.deckle")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


@pytest.mark.parametrize("body", NOT_OBJECTS)
def test_a_json_document_that_is_not_an_object_is_not_a_project(tmp_path, body):
    """The documented contract, extended to the shapes it did not cover."""
    path = _write(tmp_path, body)

    with pytest.raises(KeyError):
        load_project(path, check_sources=False)


@pytest.mark.parametrize("body", NOT_OBJECTS)
def test_the_cli_reports_it_rather_than_printing_a_traceback(tmp_path, body):
    path = _write(tmp_path, body)

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")


def test_the_message_says_what_to_compare_against(tmp_path):
    """The remedy, not just the diagnosis -- the reason the ``KeyError``
    branch was written by hand instead of falling through to the default."""
    path = _write(tmp_path, "[]")

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert "not a complete Deckle project" in result.stderr


def test_an_object_missing_pages_still_raises_the_same_way(tmp_path):
    """Unchanged behaviour, pinned so the new check cannot swallow it."""
    path = _write(tmp_path, json.dumps({"version": 1, "layout": {}}))

    with pytest.raises(KeyError):
        load_project(path, check_sources=False)


_LAYOUT = {"paper": [792.0, 612.0], "gutter_pt": 36.0, "binding_edge": "left"}

# The same defect one level down. Fixing only the top level would have left
# every one of these printing a traceback, which is why they are enumerated
# here rather than trusted to the outer guard.
MALFORMED_MEMBERS = [
    pytest.param({"version": 1, "pages": "abc", "layout": _LAYOUT},
                 "text", id="pages-is-text"),
    pytest.param({"version": 1, "pages": 5, "layout": _LAYOUT},
                 "number", id="pages-is-a-number"),
    pytest.param({"version": 1, "pages": [1, 2], "layout": _LAYOUT},
                 "number", id="pages-holds-numbers"),
    pytest.param({"version": 1, "pages": [], "layout": "x"},
                 "text", id="layout-is-text"),
    pytest.param({"version": 1, "pages": [], "layout": []},
                 "list", id="layout-is-a-list"),
]


@pytest.mark.parametrize("body,expected_shape", MALFORMED_MEMBERS)
def test_a_member_of_the_wrong_shape_is_reported_not_raised(
    tmp_path, body, expected_shape
):
    path = _write(tmp_path, json.dumps(body))

    with pytest.raises(ValueError) as caught:
        load_project(path, check_sources=False)

    assert "not a Deckle project" in str(caught.value)


@pytest.mark.parametrize("body,expected_shape", MALFORMED_MEMBERS)
def test_the_message_names_the_shape_the_author_can_see(
    tmp_path, body, expected_shape
):
    """``"<class 'dict'>"`` describes Python. Someone looking at the file in
    a text editor sees an object, a list, or a piece of text."""
    path = _write(tmp_path, json.dumps(body))

    with pytest.raises(ValueError) as caught:
        load_project(path, check_sources=False)

    assert expected_shape in str(caught.value)


@pytest.mark.parametrize("body,expected_shape", MALFORMED_MEMBERS)
def test_the_cli_reports_a_malformed_member_without_a_traceback(
    tmp_path, body, expected_shape
):
    path = _write(tmp_path, json.dumps(body))

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "info", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:")


def test_a_page_missing_a_field_still_reports_which_field(tmp_path):
    """An object of the right shape but the wrong contents keeps the older,
    more specific message -- the new guard checks shape, not contents."""
    path = _write(tmp_path, json.dumps(
        {"version": 1, "pages": [{}], "layout": _LAYOUT}
    ))

    with pytest.raises(KeyError):
        load_project(path, check_sources=False)


def test_a_well_formed_project_is_unaffected(tmp_path):
    """The guard rejects a shape, not a document -- a real project with an
    object at the top must still open."""
    path = _write(tmp_path, json.dumps({
        "version": 1,
        "pages": [],
        "layout": {"paper": [792.0, 612.0], "gutter_pt": 36.0,
                   "binding_edge": "left"},
        "printer": None,
    }))

    project = load_project(path, check_sources=False)

    assert project.pages == []
    assert project.layout.paper == (792.0, 612.0)
