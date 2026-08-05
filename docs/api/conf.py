"""Sphinx configuration for Deckle's API reference.

The reference is generated from the source tree by ``autodoc``. Deckle's
docstrings are the documentation -- this directory holds no hand-written
prose about the code, only the structure that renders it. The narrative
documents under ``docs/`` (``decisions.md``, ``plans/``, ``specs/``,
``converge/``) are hand-written and are deliberately *not* part of this
build.

Two conventions are load-bearing here:

* **Warnings are errors.** ``run.bat docs`` passes ``-W``, so a docstring
  that references a parameter the function no longer takes fails the build
  instead of quietly rotting.
* **The build imports the package.** ``autodoc`` documents live objects, so
  every module under ``deckle/`` must be importable in the build
  environment. That is why no module in this project touches Qt at import
  time -- ``deckle.core`` must not (``tests/test_core_purity.py``), and
  ``deckle.app`` merely chooses not to, which is what lets this build run
  headlessly with no display server.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from deckle import __version__ as _deckle_version  # noqa: E402

# -- Project information -------------------------------------------------

project = "Deckle"
author = "Caleb Bennett"
copyright = "2026, Caleb Bennett"  # noqa: A001 -- Sphinx's own config name
release = _deckle_version
version = _deckle_version

# -- General configuration -----------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

templates_path: list[str] = []
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Every ``:param:``/``:returns:`` in this codebase is written as a plain RST
# field list, which Sphinx understands natively. Napoleon is enabled anyway
# so a future Google/NumPy-style docstring renders rather than appearing as
# a wall of unparsed text.
napoleon_google_docstring = True
napoleon_numpy_docstring = True

# -- autodoc -------------------------------------------------------------

autodoc_member_order = "bysource"
"""Source order, not alphabetical.

These modules are written to be read top to bottom -- ``loader.py``'s
exception hierarchy, ``print_session.py``'s state machine -- and
alphabetising them would scramble an ordering the author chose.
"""

autodoc_typehints = "description"
autodoc_typehints_description_target = "documented_params"
"""Signatures stay readable; types land in the parameter descriptions.

``documented_params`` means a type is only injected for a parameter that
already has a ``:param:`` entry. Without it, autodoc emits a bare type-only
field for every undocumented argument, which is exactly the ``:param x: the
x`` noise this documentation effort exists to avoid.
"""

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}

# ``from __future__ import annotations`` is on in every module, so autodoc
# sees string annotations. Keeping them unevaluated avoids resolution
# failures on the union/`Literal` syntax used throughout.
autodoc_preserve_defaults = True

# -- intersphinx ---------------------------------------------------------

# The Python inventory is fetched from the network when available and falls
# back to the vendored copy otherwise, so a doc build on a machine with no
# network still succeeds under ``-W``.
_local_python_inv = os.path.join(
    os.path.dirname(__file__), "_inventories", "python.inv"
)
intersphinx_mapping = {
    "python": (
        "https://docs.python.org/3",
        (None, _local_python_inv) if os.path.exists(_local_python_inv) else None,
    ),
}
intersphinx_timeout = 10

# -- HTML output ---------------------------------------------------------

html_theme = "alabaster"
html_static_path: list[str] = []
html_title = f"Deckle {release} API reference"


# -- docstring preprocessing ---------------------------------------------

_PIPE_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _demote_pipe_tables(lines: list[str]) -> list[str]:
    """Render Markdown-style pipe tables as literal blocks.

    ``deckle/core/printing.py`` carries the verified back-pass ordering
    table as a Markdown pipe table -- and that module is contractually
    frozen (``tests/test_seam_zero_diff.py``), so the docstring cannot be
    rewritten into RST grid-table syntax. Left alone, docutils reads the
    ``|---|---|`` separator row as an undefined substitution reference and
    the build fails under ``-W``.

    Demoting the table to a literal block keeps the author's alignment and
    every character of the content, and costs only the table borders that a
    reader of the source sees anyway.
    """
    result: list[str] = []
    index = 0
    while index < len(lines):
        if not _PIPE_TABLE_ROW.match(lines[index]):
            result.append(lines[index])
            index += 1
            continue

        end = index
        while end < len(lines) and _PIPE_TABLE_ROW.match(lines[end]):
            end += 1

        # A literal block needs its introducer on its own line, and a blank
        # line on each side, or docutils folds it back into the paragraph.
        if result and result[-1].strip():
            result.append("")
        result.append("::")
        result.append("")
        result.extend("    " + lines[i].strip() for i in range(index, end))
        result.append("")
        index = end

    return result


def _process_docstring(app, what, name, obj, options, lines):
    lines[:] = _demote_pipe_tables(lines)


def setup(app):
    app.connect("autodoc-process-docstring", _process_docstring)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
