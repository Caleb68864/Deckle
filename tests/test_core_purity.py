"""Enforces that deckle.core stays pure: no Qt, no I/O side effects on import.

Walks every module under deckle/core/, imports it, and asserts that no
PySide6 or PyQt module ends up in sys.modules as a result of that import.
"""

import importlib
import pkgutil
import subprocess
import sys

import deckle.core


def _iter_core_module_names():
    for module_info in pkgutil.walk_packages(
        deckle.core.__path__, prefix=f"{deckle.core.__name__}."
    ):
        yield module_info.name


def test_core_modules_do_not_import_qt():
    qt_prefixes = ("PySide6", "PyQt5", "PyQt6")

    for module_name in _iter_core_module_names():
        # Snapshot sys.modules before import so we can tell what THIS
        # import newly pulled in, rather than what some earlier test
        # already loaded.
        before = set(sys.modules)
        importlib.import_module(module_name)
        after = set(sys.modules)
        newly_loaded = after - before

        qt_modules = {
            name
            for name in newly_loaded
            if name.startswith(qt_prefixes)
        }
        assert not qt_modules, (
            f"importing {module_name} pulled in Qt modules: {sorted(qt_modules)}"
        )


def test_importing_every_core_module_pulls_in_no_qt():
    """The real invariant, asked in a process that has done nothing else.

    This previously inspected the live ``sys.modules``, which asserts
    something much weaker and quite different: "nothing anywhere in this
    pytest session has loaded Qt". Any test file sorting alphabetically
    before this one and touching a Qt widget broke it, and the Qt-using
    suites passed only because their names happen to sort *after*
    ``test_core_purity``. That is luck, not a guarantee -- a new file named
    ``test_arrange_*`` fails it while changing nothing about the core.

    A subprocess imports ``deckle.core`` and nothing else, so what it
    reports is the property the module boundary actually promises.
    """
    # Import EVERY core module, not just the package. `deckle.core`'s
    # __init__ is nearly empty, so importing it alone proves almost
    # nothing -- a first version of this test passed while models.py had a
    # deliberate `import PySide6` in it.
    program = "\n".join(
        [
            "import pkgutil, importlib, sys, deckle.core",
            "for m in pkgutil.walk_packages(",
            "        deckle.core.__path__, prefix=deckle.core.__name__ + '.'):",
            "    importlib.import_module(m.name)",
            "qt = [m for m in sys.modules",
            "      if m.startswith(('PySide6', 'PyQt5', 'PyQt6'))]",
            "print(','.join(sorted(qt)))",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    leaked = [name for name in result.stdout.strip().split(",") if name]
    assert not leaked, f"importing deckle.core pulled in Qt: {leaked}"


def test_importing_the_headless_cli_pulls_in_no_qt():
    """The CLI is the other half of the promise: it must run on a server
    with no display libraries installed at all."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, deckle.cli;"
            "qt=[m for m in sys.modules if m.startswith(('PySide6','PyQt5','PyQt6'))];"
            "print(','.join(sorted(qt)))",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    leaked = [name for name in result.stdout.strip().split(",") if name]
    assert not leaked, f"importing deckle.cli pulled in Qt: {leaked}"
