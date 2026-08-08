"""A stored page entry that names a page the source does not have.

The layout half of this was fixed first: a stored *value* outside its
declared type or ``Literal`` is now refused, because ``binding_edge:
"middle"`` imposed a book bound on the right and reported no warnings.
``_page_from_dict`` had exactly the same gap and it is worse here, because
what goes wrong is the *content* rather than the geometry.

**A negative index silently printed a different page.**
``page_index: -2`` is a perfectly good Python index, so it reached
``src_pdf.pages[-2]`` and exported the second-from-last page of the
document. Verified on the numbered dummy: an eight-page source with an
entry naming page -2 exported a sheet reading **7**, with no warning
anywhere. Nothing about the output says it is not the page that was
asked for.

**An index past the end crashed.** ``page_index: 999`` survived loading,
survived imposition, and reached pikepdf as ``IndexError: Accessing
nonexistent PDF page number`` -- a traceback out of ``deckle export``,
which is the wrong side of the line ``cli.main`` draws.

The two are the same defect with different luck: nothing checked that a
stored index refers to a page. Both are now refused, and at different
points, because they are knowable at different points -- the sign at load
time from the entry alone, the upper bound only once the source is open.

``-1`` stays legal for a blank, which is the sentinel
``deckle.app.state.make_blank_page`` writes. That is the one negative
index the format means, so it is allowed exactly where it means something
and nowhere else.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import warnings

import pytest

from deckle.core.dummy import make_numbered_pdf
from deckle.core.models import BLANK_SOURCE_PATH
from deckle.core.project_io import load_project

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LAYOUT = {"paper": [792.0, 612.0], "gutter_pt": 36.0, "binding_edge": "left"}


@pytest.fixture
def source(tmp_path):
    """An eight-page numbered source, and its hash."""
    path = os.path.join(str(tmp_path), "src.pdf")
    make_numbered_pdf(path, pages=8, page_size=(400.0, 600.0))
    with open(path, "rb") as f:
        return path, hashlib.sha256(f.read()).hexdigest()


def _entry(source_path: str, sha: str, **over) -> dict:
    entry = {
        "path": source_path, "page_index": 0, "sha256": sha,
        "width_pt": 400.0, "height_pt": 600.0,
        "rotate_deg": 0, "skipped": False,
    }
    entry.update(over)
    return entry


def _write(tmp_path, *entries) -> str:
    path = os.path.join(str(tmp_path), "job.deckle")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "pages": list(entries), "layout": _LAYOUT}, f)
    return path


def _load(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_project(path, check_sources=False)


# -- indices that name no page ------------------------------------------


@pytest.mark.parametrize("index", [-2, -5, -100])
def test_a_negative_index_is_refused_rather_than_wrapping(tmp_path, source, index):
    """``pages[-2]`` is valid Python and silently the wrong page."""
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, page_index=index))

    with pytest.raises(ValueError) as caught:
        _load(project)

    assert "page_index" in str(caught.value)


def test_the_blank_sentinel_is_still_allowed(tmp_path):
    """``-1`` with the blank path is the one negative index the format
    means. Refusing it would make a project containing an inserted blank
    unopenable, which is a failure this file has already seen once."""
    project = _write(tmp_path, {
        "path": BLANK_SOURCE_PATH, "page_index": -1, "sha256": "",
        "width_pt": 400.0, "height_pt": 600.0,
        "rotate_deg": 0, "skipped": False,
    })

    assert len(_load(project).pages) == 1


def test_minus_one_on_a_real_source_is_still_refused(tmp_path, source):
    """The sentinel is a path *and* an index. Accepting ``-1`` on a real
    file would reopen the wrapping bug for the last page."""
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, page_index=-1))

    with pytest.raises(ValueError):
        _load(project)


def test_an_index_past_the_end_is_reported_not_raised(tmp_path, source):
    """Only knowable once the source is open, so it is caught at export
    rather than at load -- but caught, and named."""
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, page_index=999))

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", project,
         "-o", os.path.join(str(tmp_path), "out.pdf")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "999" in result.stderr


def test_the_out_of_range_message_names_the_file_and_the_count(tmp_path, source):
    """The remedy needs all three numbers: which file, which page was
    asked for, and how many it actually has."""
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, page_index=999))

    result = subprocess.run(
        [sys.executable, "-m", "deckle.cli", "export", project,
         "-o", os.path.join(str(tmp_path), "out.pdf")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=REPO,
    )

    assert "8" in result.stderr
    assert os.path.basename(path) in result.stderr


# -- entries of the wrong type or size ----------------------------------

MALFORMED = [
    pytest.param({"page_index": "first"}, "page_index", id="index-is-text"),
    pytest.param({"rotate_deg": "ninety"}, "rotate_deg", id="rotation-is-text"),
    pytest.param({"rotate_deg": 45}, "rotate_deg", id="rotation-is-oblique"),
    pytest.param({"skipped": "yes"}, "skipped", id="skipped-is-text"),
    pytest.param({"width_pt": "wide"}, "width_pt", id="width-is-text"),
    pytest.param({"width_pt": -400.0}, "width_pt", id="width-is-negative"),
    pytest.param({"width_pt": 0.0}, "width_pt", id="width-is-zero"),
    pytest.param({"height_pt": 0.0}, "height_pt", id="height-is-zero"),
    pytest.param({"path": 7}, "path", id="path-is-a-number"),
]


@pytest.mark.parametrize("override,field", MALFORMED)
def test_a_malformed_page_entry_is_refused(tmp_path, source, override, field):
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, **override))

    with pytest.raises(ValueError) as caught:
        _load(project)

    assert field in str(caught.value)


def test_an_oblique_rotation_would_otherwise_be_ignored(tmp_path, source):
    """45 is not a rotation the exporter can apply: it checks for 90 and
    270 and treats everything else as upright, so an oblique value was
    silently dropped rather than honoured or refused."""
    path, sha = source
    project = _write(tmp_path, _entry(path, sha, rotate_deg=45))

    with pytest.raises(ValueError) as caught:
        _load(project)

    assert "90" in str(caught.value)


# -- what must keep working ---------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_every_rotation_deckle_writes_is_accepted(tmp_path, source, rotation):
    path, sha = source

    project = _write(tmp_path, _entry(path, sha, rotate_deg=rotation))

    assert _load(project).pages[0].rotate_deg == rotation


def test_an_ordinary_project_still_opens(tmp_path, source):
    path, sha = source
    project = _write(tmp_path, *[
        _entry(path, sha, page_index=i) for i in range(8)
    ])

    assert len(_load(project).pages) == 8
