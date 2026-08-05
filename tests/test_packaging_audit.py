"""Licence audit of the *built artifact*, not just the declared dependencies.

``tests/test_license_audit.py`` reads ``pyproject.toml`` and walks the
declared dependency closure. That is necessary and it is not sufficient: it
says nothing about what the packager actually copies into ``dist/``.

The gap was not theoretical. The first PyInstaller build of Deckle produced a
**1.6 GB** bundle containing ``torch``, ``paddle``, ``cv2``, ``transformers``
-- and ``pymupdf``, which is **AGPL-3.0**. Deckle is MIT. Shipping that
artifact would have been a licence violation, and every dependency-level
audit passed the whole time, because none of those packages is a Deckle
dependency. They were simply present in the developer's global environment,
and PyInstaller bundles what it can reach, not what you declared.

Hence two rules, both enforced here:

1. Build from a clean virtual environment holding only Deckle's real
   dependencies. Excluding offenders by name is whack-a-mole against an
   environment you do not control.
2. Audit the artifact itself before shipping it.

These tests skip when no build is present, so the suite stays fast and green
for ordinary development. They are the gate for a release, and
``run.bat package`` is what produces something for them to judge.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist" / "deckle"

#: Distribution and module names that must never appear in a shipped bundle.
#: ``pymupdf`` imports as ``fitz``; ``pdfimpose`` sits on ``cpdf``. All are
#: AGPL-3.0 and all are useful enough to be plausibly present in a working
#: environment, which is exactly why the artifact is checked and not trusted.
FORBIDDEN_IN_BUNDLE = ("pymupdf", "fitz", "pdfimpose", "cpdf", "ghostscript", "poppler")

#: Anything above this is a symptom, not a size. A Deckle bundle is Python,
#: Qt, and three PDF libraries; the real one measures a few hundred MB. The
#: 1.6 GB build that motivated this file was 4x over.
MAX_BUNDLE_MB = 700


def _bundle_present() -> bool:
    return DIST_DIR.is_dir() and any(DIST_DIR.iterdir())


requires_bundle = pytest.mark.skipif(
    not _bundle_present(),
    reason="no built bundle in dist/deckle -- run `run.bat package` first",
)


def _bundle_entries() -> list[str]:
    """Every path inside the bundle, lowercased and repo-relative."""
    entries = []
    for root, dirs, files in os.walk(DIST_DIR):
        rel_root = Path(root).relative_to(DIST_DIR)
        for name in list(dirs) + list(files):
            entries.append(str(rel_root / name).lower().replace("\\", "/"))
    return entries


@requires_bundle
@pytest.mark.parametrize("forbidden", FORBIDDEN_IN_BUNDLE)
def test_no_agpl_library_is_bundled(forbidden: str):
    """The check that a dependency-level audit cannot make.

    An AGPL library reaching ``dist/`` makes the artifact undistributable
    under MIT, regardless of what ``pyproject.toml`` declares.
    """
    hits = [
        entry
        for entry in _bundle_entries()
        # Match a path component, not a substring: "fitz" must not fire on
        # "fitzgerald.ttf", and this file's own name contains none of them.
        if any(part == forbidden or part.startswith(forbidden + "-") or
               part.startswith(forbidden + ".")
               for part in entry.split("/"))
    ]
    assert not hits, (
        f"{forbidden!r} is AGPL and was bundled into dist/: {hits[:5]}. "
        "Build from a clean virtualenv containing only Deckle's declared "
        "dependencies -- PyInstaller bundles what it can reach, not what "
        "you declared."
    )


@requires_bundle
def test_the_bundle_is_not_absurdly_large():
    """Size is the cheap signal that the build environment leaked."""
    total = sum(
        (Path(root) / name).stat().st_size
        for root, _dirs, files in os.walk(DIST_DIR)
        for name in files
    )
    megabytes = total / (1024 * 1024)
    assert megabytes <= MAX_BUNDLE_MB, (
        f"the bundle is {megabytes:.0f} MB, over the {MAX_BUNDLE_MB} MB ceiling. "
        "Something from the build environment has leaked in -- check for "
        "torch, cv2, paddle, transformers and friends."
    )


@requires_bundle
@pytest.mark.parametrize("leaked", ["torch", "paddle", "cv2", "transformers", "scipy", "yt_dlp"])
def test_no_unrelated_heavy_package_is_bundled(leaked: str):
    """Named for the ones that actually leaked, so the failure is legible.

    Not a licence problem -- a "you built from the wrong environment"
    problem, which is what produces the licence problem.
    """
    hits = [entry for entry in _bundle_entries() if entry.split("/")[0] == leaked]
    assert not hits, (
        f"{leaked!r} was bundled and is not a Deckle dependency. Build from "
        "a clean virtualenv."
    )


@requires_bundle
def test_both_executables_are_produced():
    """The GUI and the headless CLI ship together.

    The CLI is built without Qt on purpose (``deckle.core`` is Qt-free), so
    it stays runnable on a server with no display libraries at all.
    """
    names = {p.name.lower() for p in DIST_DIR.iterdir() if p.is_file()}
    expected = {"deckle.exe", "deckle-cli.exe"} if os.name == "nt" else {"deckle", "deckle-cli"}
    missing = expected - names
    assert not missing, f"missing executables in dist/: {sorted(missing)}"


def test_the_spec_records_why_pyinstallers_licence_permits_this():
    """PyInstaller is GPLv2-or-later; bundling MIT code is lawful only
    because of its explicit linking exception. If that reasoning is not
    written down, the next person has to rediscover it."""
    spec = (REPO_ROOT / "packaging" / "deckle.spec").read_text(encoding="utf-8")
    assert "exception" in spec.lower()
    assert "GPL" in spec
