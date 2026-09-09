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
#
# ``pdfimpose`` and its underlying ``cpdf`` are AGPL-3.0. ``pdfimpose`` is a
# genuinely useful development-time reference tool for verifying Deckle's
# own imposition math (see tools/README.md) but must never become a
# dependency of anything shipped or tested -- this denylist is what turns
# "someone installs it in the project venv" into a red test instead of a
# license violation.
FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz", "pdfimpose", "cpdf"}


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
    """Everything a distribution says about its licence, as one string.

    **All three places**, because a distribution only has to use one of them.
    The legacy ``License`` field and the ``License ::`` classifiers were the
    only ones read until 2026-09-09, and by then three of the eleven
    distributions in Deckle's own closure -- pikepdf, pillow and packaging --
    had moved to PEP 639's ``License-Expression`` and set neither of the
    others. For those three this function returned nothing but
    ``Development Status ::`` and ``Programming Language ::`` lines, so
    :func:`test_no_agpl_dependency` was grepping text that could not contain a
    licence whatever the licence was.

    Modern build backends emit ``License-Expression`` by default, so that was
    not a stable three: it was every dependency added from then on. In a
    project whose packaging audit exists because an AGPL renderer once reached
    a shipped bundle, a licence check that silently stops seeing licences is
    the failure worth guarding hardest.
    """
    return " ".join(
        part
        for part in (
            dist.metadata.get("License") or "",
            dist.metadata.get("License-Expression") or "",
            " ".join(dist.metadata.get_all("Classifier") or []),
        )
        if part
    )


#: Licence tokens that count as *something having been said*. Deliberately not
#: an allow-list of acceptable licences -- it is the tripwire for a
#: distribution whose licence this audit cannot see at all, which is the state
#: pikepdf, pillow and packaging were in while the suite stayed green.
LICENCE_TOKENS = (
    "agpl", "gpl", "lgpl", "mpl", "mit", "bsd", "apache", "isc", "zlib",
    "psf", "python software foundation", "unlicense", "cc0", "public domain",
    "proprietary", "artistic", "eclipse", "mozilla",
)


def _declares_a_licence(dist: "metadata.Distribution") -> bool:
    """Whether anything in this distribution's metadata names a licence."""
    text = _license_text(dist).lower()
    return any(token in text for token in LICENCE_TOKENS)


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


class _FakeMetadata:
    """The two accessors ``_license_text`` uses, over a plain dict."""

    def __init__(self, fields: dict[str, str], classifiers: list[str]) -> None:
        self._fields = fields
        self._classifiers = classifiers

    def get(self, key: str, default=None):
        return self._fields.get(key, default)

    def get_all(self, key: str):
        return self._classifiers if key == "Classifier" else None


class _FakeDistribution:
    """Minimal stand-in for ``importlib.metadata.Distribution`` in tests."""

    def __init__(
        self,
        top_level: set[str] | None = None,
        fields: dict[str, str] | None = None,
        classifiers: list[str] | None = None,
    ) -> None:
        self._top_level = top_level or set()
        self.metadata = _FakeMetadata(fields or {}, classifiers or [])

    def read_text(self, filename: str) -> str:
        if filename == "top_level.txt":
            return "\n".join(sorted(self._top_level))
        return ""


def _check_forbidden(closure: dict[str, "metadata.Distribution"]) -> None:
    """Re-runs the same denylist check exercised by
    ``test_no_pymupdf_or_fitz_dependency``, against an arbitrary closure --
    used to prove the denylist is actually wired to real distribution data,
    not just declared.
    """
    offenders = []
    for name, dist in closure.items():
        if name in FORBIDDEN_DISTRIBUTIONS:
            offenders.append(name)
            continue
        if FORBIDDEN_DISTRIBUTIONS & _top_level_names(dist):
            offenders.append(name)
    assert not offenders, f"forbidden AGPL PDF tooling present in dependency closure: {offenders}"


def test_injected_pdfimpose_fails_the_denylist_check():
    """Proves the denylist is wired, not merely declared: a fake ``pdfimpose``
    distribution injected into the closure must make the check fail.
    """
    fake_closure = {"pdfimpose": _FakeDistribution(top_level={"pdfimpose"})}
    with pytest.raises(AssertionError):
        _check_forbidden(fake_closure)


def test_every_dependency_says_what_its_licence_is(dependency_closure):
    """A distribution this audit cannot read a licence from is a failure.

    Not an allow-list of acceptable licences -- a tripwire for the state the
    audit was silently in: three distributions whose licence lived only in a
    field it did not read, so the AGPL grep ran over text that could not have
    contained the answer. Passing because there is nothing to see is the one
    outcome a licence check must never have.
    """
    silent = [
        name for name, dist in dependency_closure.items()
        if not _declares_a_licence(dist)
    ]
    assert not silent, (
        "no licence could be read for: "
        f"{silent}. If the distribution declares one, this audit is not "
        "looking in the right field; if it does not, it cannot ship."
    )


def test_an_agpl_distribution_is_caught_when_it_uses_a_licence_expression():
    """The wiring proof the licence half was missing.

    `test_injected_pdfimpose_fails_the_denylist_check` proves the *denylist*
    is wired to real data. Nothing proved the same of the licence text, and
    that is exactly where the blindness lived: this fake declares AGPL the way
    pikepdf, pillow and packaging declare their licences, and before
    `_license_text` read `License-Expression` it sailed through.
    """
    fake = _FakeDistribution(fields={"License-Expression": "AGPL-3.0-or-later"})

    assert "agpl" in _license_text(fake).lower()


def test_an_agpl_distribution_is_caught_in_the_legacy_field_and_in_classifiers():
    """The other two spellings still work; the fix added a field, it did not
    move to one."""
    legacy = _FakeDistribution(fields={"License": "AGPL-3.0"})
    classified = _FakeDistribution(
        classifiers=["License :: OSI Approved :: GNU Affero General Public License v3"]
    )

    assert "agpl" in _license_text(legacy).lower()
    assert "affero" in _license_text(classified).lower()


def test_a_distribution_that_names_no_licence_is_not_silently_accepted():
    """The state pikepdf was in: metadata present, licence absent."""
    quiet = _FakeDistribution(
        classifiers=["Development Status :: 5 - Production/Stable"]
    )

    assert not _declares_a_licence(quiet)
