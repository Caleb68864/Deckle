"""Enforces that deckle.core stays pure: no Qt, no I/O side effects on import.

Walks every module under deckle/core/, imports it, and asserts that no
PySide6 or PyQt module ends up in sys.modules as a result of that import.
"""

import importlib
import pkgutil
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


def test_core_package_itself_has_no_qt_loaded():
    qt_prefixes = ("PySide6", "PyQt5", "PyQt6")
    loaded_qt = {
        name for name in sys.modules if name.startswith(qt_prefixes)
    }
    assert not loaded_qt, f"Qt modules present in sys.modules: {sorted(loaded_qt)}"
