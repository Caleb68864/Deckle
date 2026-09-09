"""Enforces that deckle.core stays pure: no Qt, no I/O side effects on import.

Walks every module under deckle/core/, imports it, and asserts that no
PySide6 or PyQt module ends up in sys.modules as a result of that import.

**Every check here runs in a child process, and that is not incidental.**
The question is "what did importing this newly load", and inside a pytest
session that has already imported the whole package the answer is always
"nothing" -- the import is a cache hit and executes no code. Two of these
tests therefore look redundant and are not: one states the invariant, the
other names the module that broke it, and a fourth proves that naming can
happen at all.
"""

import os
import subprocess
import sys


# The program below is the whole check, run in a child process. It walks
# the core modules of `package`, imports them one at a time, and reports
# the first one whose import newly put a Qt module into `sys.modules`.
#
# In a child, "newly loaded" means what it says. In-process it does not: an
# earlier test in the same session has already imported every core module,
# so `importlib.import_module` returns the cached object without executing
# anything and the difference is empty for every module. That was this
# file's first test until 2026-09-09 -- it passed by importing nothing,
# and it passed just as well against a `models.py` with a deliberate
# `import PySide6` in it, because some earlier test had already loaded
# both. The invariant was never unguarded (the subprocess test below is
# the real one); what was missing was the *attribution*, which is the only
# thing the per-module loop was ever for. It now runs where it means
# something.
_ATTRIBUTION_PROGRAM = "\n".join(
    [
        "import importlib, pkgutil, sys",
        "package = importlib.import_module(sys.argv[1])",
        "qt = ('PySide6', 'PyQt5', 'PyQt6')",
        "for m in pkgutil.walk_packages(",
        "        package.__path__, prefix=package.__name__ + '.'):",
        "    before = set(sys.modules)",
        "    importlib.import_module(m.name)",
        "    pulled = sorted(n for n in set(sys.modules) - before",
        "                    if n.startswith(qt))",
        "    if pulled:",
        "        print(m.name + ' ' + ','.join(pulled))",
        "        break",
    ]
)


def test_the_core_module_that_reaches_for_qt_is_named():
    """Which module, not just whether. Run in a child process, because
    'what did this import newly load' has no answer in a session that has
    already loaded everything."""
    result = subprocess.run(
        [sys.executable, "-c", _ATTRIBUTION_PROGRAM, "deckle.core"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip(), (
        f"a core module pulled in Qt: {result.stdout.strip()}"
    )


def test_the_attribution_can_actually_fail(tmp_path):
    """Proves the check above is capable of naming a culprit.

    A throwaway package with one module that puts a Qt name into
    ``sys.modules`` -- by assignment, so this needs no PySide6 installed
    and tests the check rather than the environment. Without it the
    previous test is a subprocess that always prints nothing, which is
    indistinguishable from a subprocess that never looked.
    """
    package = tmp_path / "purityprobe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "innocent.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "guilty.py").write_text(
        "import sys, types\n"
        "sys.modules['PySide6.QtWidgets'] = types.ModuleType('PySide6.QtWidgets')\n",
        encoding="utf-8",
    )

    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", _ATTRIBUTION_PROGRAM, "purityprobe"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["purityprobe.guilty", "PySide6.QtWidgets"]


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
