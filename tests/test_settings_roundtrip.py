"""Every ``LayoutSettings`` field survives a ``.deckle`` round trip intact.

JSON has one sequence type and Python has two, so a tuple field written to
a project file comes back as a list unless something converts it. The
loader converted ``paper`` and nothing else, which was correct while
``paper`` was the only tuple. Three tuple fields were added in one day --
``crop_odd_pt``, ``crop_even_pt``, ``signature_lengths`` -- and each one
silently came back as a list.

The consequences are quiet rather than loud, which is why this needs a
test rather than a bug report: the settings still *work* (a list unpacks
and indexes just like a tuple), but the reloaded project is no longer
equal to the one that was saved, ``LayoutSettings`` stops being hashable,
and the export cache keys on ``repr`` -- so ``[10.0, 20.0]`` and
``(10.0, 20.0)`` are different cache entries for identical geometry.

Guarded generically rather than field by field: the loader now converts
any stored list back to a tuple, so the next tuple field added cannot
reintroduce this. No ``LayoutSettings`` field is genuinely a list.
"""

from __future__ import annotations

import dataclasses
import os

import pikepdf
import pytest

from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef
from deckle.core.project_io import load_project, save_project


def _project(tmp_path, **layout_kwargs) -> tuple[Project, str]:
    src = os.path.join(str(tmp_path), "s.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(8):
        pdf.add_blank_page(page_size=(400.0, 600.0))
    pdf.save(src)
    pdf.close()
    pages = [
        SourcePage(
            ref=SourceRef(path=src, page_index=i, sha256="a" * 64,
                          width_pt=400.0, height_pt=600.0),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(8)
    ]
    base = dict(paper=(792.0, 612.0), gutter_pt=36.0, binding_edge="left")
    base.update(layout_kwargs)
    return Project(pages=pages, layout=LayoutSettings(**base), printer=None), src


def _roundtrip(tmp_path, project) -> LayoutSettings:
    path = os.path.join(str(tmp_path), "job.deckle")
    save_project(project, path)
    return load_project(path, check_sources=False).layout


def test_a_crop_survives_as_a_tuple(tmp_path):
    project, _ = _project(tmp_path, crop_odd_pt=(10.0, 20.0, 30.0, 40.0))

    loaded = _roundtrip(tmp_path, project)

    assert loaded.crop_odd_pt == (10.0, 20.0, 30.0, 40.0)
    assert isinstance(loaded.crop_odd_pt, tuple)


def test_an_even_crop_survives_as_a_tuple(tmp_path):
    project, _ = _project(tmp_path, crop_even_pt=(1.0, 2.0, 3.0, 4.0))

    loaded = _roundtrip(tmp_path, project)

    assert isinstance(loaded.crop_even_pt, tuple)


def test_signature_lengths_survive_as_a_tuple(tmp_path):
    project, _ = _project(
        tmp_path, fold_scheme="folio", signature_lengths=(1, 1)
    )

    loaded = _roundtrip(tmp_path, project)

    assert loaded.signature_lengths == (1, 1)
    assert isinstance(loaded.signature_lengths, tuple)


def test_the_reloaded_layout_equals_the_one_that_was_saved(tmp_path):
    """The property that actually matters, and the one a per-field check
    would keep missing as fields are added."""
    project, _ = _project(
        tmp_path,
        fold_scheme="folio",
        crop_odd_pt=(10.0, 20.0, 30.0, 40.0),
        crop_even_pt=(30.0, 20.0, 10.0, 40.0),
        signature_lengths=(1, 1),
        trim_pt=18.0,
    )

    loaded = _roundtrip(tmp_path, project)

    assert loaded == project.layout


def test_the_reloaded_layout_is_still_hashable(tmp_path):
    """A frozen dataclass holding a list is not hashable, and the failure
    surfaces far from here -- in a set, a dict key, or a cache."""
    project, _ = _project(tmp_path, crop_odd_pt=(10.0, 20.0, 30.0, 40.0))

    hash(_roundtrip(tmp_path, project))


def test_no_layout_field_round_trips_as_a_list(tmp_path):
    """Exhaustive over the dataclass, so a tuple field added later is
    covered without anyone remembering to add a case here."""
    tuple_defaults = {
        "paper": (792.0, 612.0),
        "crop_odd_pt": (1.0, 2.0, 3.0, 4.0),
        "crop_even_pt": (4.0, 3.0, 2.0, 1.0),
        "signature_lengths": (1, 1),
        "sewing_station_positions_pt": (36.0, 144.0),
    }
    project, _ = _project(tmp_path, fold_scheme="folio", **{
        k: v for k, v in tuple_defaults.items() if k != "paper"
    })

    loaded = _roundtrip(tmp_path, project)

    for field in dataclasses.fields(LayoutSettings):
        value = getattr(loaded, field.name)
        assert not isinstance(value, list), (
            f"{field.name} came back as a list: {value!r}"
        )


def test_station_positions_survive_a_project_round_trip(tmp_path):
    project, _ = _project(
        tmp_path, fold_scheme="folio", sewing_station_positions_pt=(36.0, 144.0)
    )

    loaded = _roundtrip(tmp_path, project)

    assert loaded.sewing_station_positions_pt == (36.0, 144.0)
    assert isinstance(loaded.sewing_station_positions_pt, tuple)
    assert loaded == project.layout


def test_a_project_written_before_stations_still_opens(tmp_path):
    """The no-migration claim, pinned. A `.deckle` from before the field
    existed loads with it at None and behaves exactly as it did."""
    import json

    project, _ = _project(tmp_path, fold_scheme="folio")
    path = os.path.join(str(tmp_path), "old.deckle")
    save_project(project, path)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    del data["layout"]["sewing_station_positions_pt"]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    loaded = load_project(path, check_sources=False).layout

    assert loaded.sewing_station_positions_pt is None
    assert loaded.sewing_stations == 3
