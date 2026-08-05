"""Every module reaches the generated documentation.

``run.bat docs`` builds with ``-W``, so a docstring that drifts from the
code fails the build. That catches a *broken* page and cannot catch a
*missing* one: the ``.rst`` files are written by hand, and a module with
no ``.rst`` is simply absent from the output. Sphinx has nothing to warn
about, so the build stays green while the documentation quietly stops
describing the code.

Which is what happened. ``core.outputs`` and ``core.schedule`` were added
during the hardening passes and ``core.locate`` for click-to-preview; all
three were written with full docstrings, and none of them appeared in the
built docs. The promise was documentation that stays current as the code
changes, and nothing was enforcing it.
"""

from __future__ import annotations

import os
import pkgutil

import deckle

DOCS_API = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "api"
)


def _package_modules() -> set[str]:
    """Every importable module in the package, by dotted name."""
    names = set()
    for info in pkgutil.walk_packages(deckle.__path__, prefix="deckle."):
        if "__main__" in info.name:
            continue
        names.add(info.name)
    return names


def _documented_modules() -> set[str]:
    """Every module with a page of its own, by dotted name."""
    return {
        "deckle." + name[: -len(".rst")]
        for name in os.listdir(DOCS_API)
        if name.endswith(".rst") and name != "index.rst"
    }


def test_every_module_has_a_documentation_page():
    """A module with no page is invisible in the built docs, and the build
    succeeds anyway -- there is no reference to resolve and so nothing for
    ``-W`` to reject."""
    missing = _package_modules() - _documented_modules()
    # Namespace packages carry no API of their own; their contents are
    # documented individually and listed in the parent's toctree.
    missing = {name for name in missing if name != "deckle.app.views"}

    assert not missing, (
        "these modules are absent from the generated documentation -- "
        f"add docs/api/<name>.rst and list it in the parent toctree: {sorted(missing)}"
    )


def test_no_page_describes_a_module_that_no_longer_exists():
    """The other direction: a page left behind after a rename builds fine
    and documents nothing."""
    stale = _documented_modules() - _package_modules()
    # Package overview pages, which have a toctree rather than a module.
    stale -= {"deckle.core", "deckle.app"}

    assert not stale, f"documentation pages with no module: {sorted(stale)}"


def test_every_page_is_reachable_from_a_toctree():
    """A page nobody links to is a page nobody finds. Sphinx warns about
    this one, but only when the file exists -- so it is checked here too,
    where the failure names the fix."""
    listed = set()
    for name in os.listdir(DOCS_API):
        if not name.endswith(".rst"):
            continue
        with open(os.path.join(DOCS_API, name), encoding="utf-8") as handle:
            for line in handle:
                entry = line.strip()
                if entry and not entry.startswith((".", ":", "#")):
                    listed.add(entry)

    pages = {
        name[: -len(".rst")]
        for name in os.listdir(DOCS_API)
        if name.endswith(".rst") and name != "index.rst"
    }
    orphans = pages - listed

    assert not orphans, f"pages not listed in any toctree: {sorted(orphans)}"
