# -*- mode: python ; coding: utf-8 -*-
#
# Deckle's PyInstaller spec. Hand-maintained source, not generated output --
# `.gitignore`'s `*.spec` is for the ones PyInstaller writes for you, and
# this file is negated out of it. It was untracked for the first month of
# the project's life, which meant exactly one machine on earth could produce
# a release build or run the licence gate that guards one.
#
# LICENCE, and why this bundling is lawful. PyInstaller is licensed
# GPLv2-or-later WITH an explicit exception permitting the distribution of
# bundled programs under any licence the author chooses. Deckle is MIT.
# Without that exception, freezing an MIT app with a GPL tool and shipping
# the result would be a licence violation, so the reasoning is written here
# rather than rediscovered by whoever next wonders. PyInstaller is
# build-time only -- it is in the `package` extra, never in
# `[project].dependencies`, and never enters the runtime closure that
# `tests/test_license_audit.py` walks.
#
# NOT a place to fix a fat bundle. The first build produced 1.6 GB
# containing torch, paddle, cv2 and pymupdf -- pymupdf being AGPL-3.0, which
# would have made the artifact undistributable. The fix is to build from
# `.buildenv`, a clean virtualenv holding only the declared dependencies;
# see `run.bat package`. Excluding offenders by name here is whack-a-mole
# against an environment you do not control, and the next machine has a
# different set. The `excludes` below are for one thing only: keeping Qt out
# of the headless CLI binary.

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# Third-party submodules PyInstaller's static analysis does not reach.
# Deckle's own modules are NOT listed: the analysis traces those from the
# entry point, which is why adding `core/schema.py` needed no change here.
_HIDDEN = (
    collect_submodules("pikepdf")
    + collect_submodules("pypdfium2")
    + collect_submodules("img2pdf")
)

# `deckle --version` reads installed-distribution metadata rather than
# importing the packages, so that the CLI never has to import PySide6. A
# frozen build ships no .dist-info by default, and the lookup then returns
# the string "not installed" for every dependency -- silently wrong output
# from the one command whose entire job is to answer "what am I running"
# for a bug report.
_METADATA = (
    copy_metadata("pikepdf")
    + copy_metadata("pypdfium2")
    + copy_metadata("img2pdf")
    + copy_metadata("PySide6")
    + copy_metadata("natsort")
    + copy_metadata("Pillow")
)

gui_analysis = Analysis(
    ["../deckle/__main__.py"],
    pathex=[".."],
    binaries=[],
    datas=_METADATA,
    hiddenimports=list(_HIDDEN),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# The CLI is built Qt-free on purpose: `deckle.core` imports no Qt binding
# (`tests/test_core_purity.py` enforces it) and `deckle.cli` never imports
# `deckle.app`, so the headless binary runs on a server with no display
# libraries installed at all. The excludes are belt and braces -- if one of
# them ever starts mattering, something has imported Qt from the core and
# `test_core_purity` should have caught it first.
cli_analysis = Analysis(
    ["../deckle/cli/__main__.py"],
    pathex=[".."],
    binaries=[],
    datas=_METADATA,
    hiddenimports=list(_HIDDEN),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "shiboken6", "PyQt5", "PyQt6", "tkinter"],
    noarchive=False,
)

# Deliberately no MERGE(). It would de-duplicate the two analyses' shared
# binaries, and it also rewrites where each executable expects to find them
# -- which is a thing to get wrong on a machine that cannot run the result.
# COLLECT already writes each destination name once, and the bundle is
# nowhere near the 700 MB ceiling (the known-good Windows build was 160 MB).

gui_pyz = PYZ(gui_analysis.pure)
cli_pyz = PYZ(cli_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="deckle",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed: the GUI must not open a console behind itself. The CLI
    # below is the opposite, and getting these two the wrong way round
    # produces a GUI with a stray black window and a CLI that prints
    # nowhere.
    console=False,
)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="deckle-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

# One folder, not one file. `tests/test_packaging_audit.py` walks
# `dist/deckle/` and requires both executables as files directly inside it,
# and a one-file build would leave nothing there to audit -- the whole
# bundle would be sealed inside two self-extracting binaries and the licence
# gate would have nothing to look at.
COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="deckle",
)
