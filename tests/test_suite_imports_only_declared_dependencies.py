"""No test module imports an undeclared distribution at module level.

The bug this pins is not "numpy". It is that a test module imported, at
module level, a distribution ``pyproject.toml`` does not declare -- and
pytest's collection then aborted for the *whole* suite with a single
error, so 1531 tests ran as zero. The symptom reads like a broken clone
rather than a missing dependency, and the next one will be ``scipy`` or
``opencv`` and will read exactly the same.

The steering is toward ``pytest.importorskip`` inside the test body, as
``tests/test_export.py`` already does for ``psutil``. That degrades to a
skip of one test instead of an error for all of them, which is why only
module-level imports are checked here.
"""

from __future__ import annotations

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")

# The import names of `[project].dependencies` in pyproject.toml. Pillow
# installs as `PIL`, so the mapping is hard-coded rather than resolved:
# the list is six entries and it lives two feet away.
RUNTIME_IMPORTS = frozenset(
    {"pikepdf", "pypdfium2", "img2pdf", "natsort", "PIL", "PySide6"}
)

# `hypothesis` and `pytest` are the declared `[dev]` extras.
DEV_IMPORTS = frozenset({"pytest", "hypothesis"})

# `deckle` is the project. `packaging` arrives with setuptools and is
# imported at module level by tests/test_license_audit.py -- a
# pre-existing, deliberate case this test does not relitigate.
ALWAYS_ALLOWED = frozenset({"deckle", "packaging"})

ALLOWED = frozenset(sys.stdlib_module_names) | RUNTIME_IMPORTS | DEV_IMPORTS | ALWAYS_ALLOWED


def _test_modules() -> list[str]:
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(TESTS_DIR):
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _module_level_imports(path: str) -> set[str]:
    """Top-level package names imported by ``path``'s module body.

    Only the direct children of the module body are examined, never
    ``ast.walk``. An import guarded by ``if sys.version_info >= ...`` or
    tucked inside a test function is not what breaks collection, and
    flagging those is a separate argument to have.
    """
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_test_module_imports_an_undeclared_third_party_package_at_module_level():
    offenders: list[tuple[str, str]] = []

    for path in _test_modules():
        relative = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        for name in sorted(_module_level_imports(path)):
            if name not in ALLOWED:
                offenders.append((relative, name))

    assert not offenders, (
        f"undeclared module-level imports in the test suite: {offenders} -- "
        "import it inside the test with pytest.importorskip, or declare it "
        "in pyproject.toml"
    )
