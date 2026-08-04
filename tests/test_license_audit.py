"""License audit: enforces the project's hardest constraint automatically.

Enumerates the licenses of Deckle's own dependency closure (starting from
``pyproject.toml``'s direct dependencies and walking transitive requires via
``importlib.metadata``) and fails if any distribution in that closure is
AGPL-licensed, or if the AGPL PDF-rendering distribution (module name
``fitz``) is present anywhere
in it. Deliberately scoped to Deckle's own dependency closure rather than
every distribution installed on the machine -- an unrelated package sitting
in the same Python environment for some other project must never fail this
project's build.
"""

from __future__ import annotations

import importlib.metadata as metadata
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires Python >=3.11
    import tomli as tomllib

from packaging.requirements import Requirement

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Distribution/import names that would make Deckle undistributable if they
# ever crept into the dependency closure -- see docs/CONTRIBUTING.md.
FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz"}


def _direct_dependencies() -> list[str]:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return [Requirement(dep).name for dep in data["project"]["dependencies"]]


def _dependency_closure(roots: list[str]) -> dict[str, "metadata.Distribution"]:
    """BFS over ``roots`` and their transitive ``requires``, by distribution name.

    Distributions that can't be resolved (e.g. optional/extra-only
    requirements not actually installed) are skipped rather than failing --
    this audit only judges what is actually present in Deckle's own closure.
    """
    found: dict[str, metadata.Distribution] = {}
    queue = list(roots)
    while queue:
        name = queue.pop()
        key = name.lower()
        if key in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        found[key] = dist
        for req_str in dist.requires or []:
            try:
                req = Requirement(req_str)
            except Exception:
                continue
            if req.marker is not None:
                try:
                    if not req.marker.evaluate({"extra": ""}):
                        # Only follow requirements that apply unconditionally
                        # (or that only depend on features Deckle doesn't
                        # opt into via extras) -- an extras-only requirement
                        # isn't part of Deckle's actual installed closure.
                        continue
                except Exception:
                    pass
            queue.append(req.name)
    return found


def _license_text(dist: "metadata.Distribution") -> str:
    license_field = dist.metadata.get("License") or ""
    classifiers = " ".join(dist.metadata.get_all("Classifier") or [])
    return f"{license_field} {classifiers}"


def _top_level_names(dist: "metadata.Distribution") -> set[str]:
    try:
        raw = dist.read_text("top_level.txt") or ""
    except Exception:
        return set()
    return {line.strip().lower() for line in raw.splitlines() if line.strip()}


@pytest.fixture(scope="module")
def dependency_closure() -> dict[str, "metadata.Distribution"]:
    return _dependency_closure(_direct_dependencies())


def test_dependency_closure_is_non_empty(dependency_closure):
    # A sanity guard on the audit itself: an empty closure (e.g. because
    # nothing is actually installed) would make every assertion below
    # vacuously true and silently stop auditing anything.
    assert dependency_closure


def test_no_agpl_dependency(dependency_closure):
    offenders = [
        name
        for name, dist in dependency_closure.items()
        if "agpl" in _license_text(dist).lower()
    ]
    assert not offenders, f"AGPL-licensed distribution(s) in dependency closure: {offenders}"


def test_no_pymupdf_or_fitz_dependency(dependency_closure):
    offenders = []
    for name, dist in dependency_closure.items():
        if name in FORBIDDEN_DISTRIBUTIONS:
            offenders.append(name)
            continue
        if FORBIDDEN_DISTRIBUTIONS & _top_level_names(dist):
            offenders.append(name)
    assert not offenders, f"forbidden AGPL PDF tooling present in dependency closure: {offenders}"
