@echo off
setlocal EnableDelayedExpansion

rem ===================================================================
rem  Deckle - dev launcher
rem
rem    run.bat                 launch the GUI
rem    run.bat cli <args...>   headless CLI  (e.g. run.bat cli info book.pdf)
rem    run.bat test            run the test suite
rem    run.bat test -k layout  run a subset
rem    run.bat deps            install/refresh dependencies
rem    run.bat doctor          check the environment without launching
rem    run.bat docs            build the HTML API reference
rem    run.bat package         build the .exe bundle into dist\deckle
rem ===================================================================

rem Always work from the repo root, so a double-click resolves imports.
cd /d "%~dp0"

rem Prefer a local venv if one exists; otherwise fall back to system Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist "venv\Scripts\python.exe"  set "PY=venv\Scripts\python.exe"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [deckle] Python not found on PATH.
    echo          Install Python 3.11+ or create a venv in .venv\
    goto :fail
)

set "CMD=%~1"

if /i "%CMD%"=="cli"    goto :cli
if /i "%CMD%"=="test"   goto :test
if /i "%CMD%"=="deps"   goto :deps
if /i "%CMD%"=="doctor" goto :doctor
if /i "%CMD%"=="docs"   goto :docs
if /i "%CMD%"=="package" goto :package
if /i "%CMD%"=="-h"     goto :usage
if /i "%CMD%"=="--help" goto :usage
if /i "%CMD%"=="help"   goto :usage
if not "%CMD%"=="" goto :usage

rem ---------- default: launch the GUI ----------
rem No build, no packaging: this runs the working tree directly, which is
rem what development wants. Only `run.bat package` builds anything.
echo [deckle] launching GUI...
"%PY%" -m deckle
if errorlevel 1 goto :fail
goto :done

:package
echo [deckle] building executables...
echo          This takes about two minutes. PyInstaller goes quiet
echo          while it processes the PySide6 hooks - that is normal.
rem Build from .buildenv, NOT from the developer's interpreter. The first
rem build ran against a global environment and produced a 1.6 GB bundle
rem containing torch, paddle, cv2 -- and pymupdf, which is AGPL and would
rem have made the artifact undistributable under MIT. PyInstaller bundles
rem what it can reach, not what you declared, so the build environment is
rem part of the licence surface.
set "BUILDPY=.buildenv\Scripts\python.exe"
if not exist "%BUILDPY%" (
    echo [deckle] no clean build environment found. Creating .buildenv...
    "%PY%" -m venv .buildenv
    if errorlevel 1 goto :fail
    "%BUILDPY%" -m pip install --upgrade pip
    "%BUILDPY%" -m pip install -e .[package]
    if errorlevel 1 goto :fail
)
"%BUILDPY%" -m PyInstaller --noconfirm --distpath dist --workpath build\pyinstaller packaging\deckle.spec
if errorlevel 1 goto :fail
echo [deckle] auditing the bundle...
rem Licence and size gate. A dependency-level audit cannot see what the
rem packager actually copied, so the artifact is checked directly.
"%PY%" -m pytest tests\test_packaging_audit.py -q
if errorlevel 1 goto :fail
echo [deckle] wrote dist\deckle\deckle.exe and dist\deckle\deckle-cli.exe
goto :done

:cli
rem Drop the leading "cli" token and pass the rest through.
set "ARGS=%*"
set "ARGS=!ARGS:~4!"
"%PY%" -m deckle.cli !ARGS!
if errorlevel 1 goto :fail
goto :done

:test
set "ARGS=%*"
set "ARGS=!ARGS:~5!"
"%PY%" -m pytest -q !ARGS!
if errorlevel 1 goto :fail
goto :done

:deps
echo [deckle] installing dependencies...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -e .
"%PY%" -m pip install pytest psutil
if errorlevel 1 goto :fail
goto :done

:docs
echo [deckle] building API reference...
rem -W turns warnings into errors, so a docstring that drifts from the code
rem fails this build instead of quietly rotting. --keep-going reports every
rem problem in one run rather than stopping at the first.
"%PY%" -c "import sphinx" >nul 2>&1
if errorlevel 1 (
    echo [deckle] Sphinx not installed.
    echo          Install it with: %PY% -m pip install -e .[docs]
    goto :fail
)
"%PY%" -m sphinx -b html -W --keep-going docs\api docs\api\_build\html
if errorlevel 1 goto :fail
echo [deckle] wrote docs\api\_build\html\index.html
goto :done

:doctor
echo [deckle] interpreter:
"%PY%" -c "import sys; print('  ', sys.executable); print('  ', sys.version.split()[0])"
echo [deckle] dependencies:
"%PY%" -c "import importlib.metadata as m; [print('   ' + n.ljust(12) + m.version(n)) for n in ('pikepdf','pypdfium2','img2pdf','natsort','Pillow','PySide6')]"
echo [deckle] printers visible to Qt:
"%PY%" -c "from PySide6.QtPrintSupport import QPrinterInfo; ps=QPrinterInfo.availablePrinters(); print('   none found') if not ps else [print('   ', p.printerName()) for p in ps]"
goto :done

:usage
echo.
echo   Deckle dev launcher
echo.
echo     run.bat                  launch the GUI
echo     run.bat cli ^<args...^>    headless CLI
echo     run.bat test [args]      run the test suite
echo     run.bat deps             install/refresh dependencies
echo     run.bat doctor           check environment, list printers
echo     run.bat docs             build the HTML API reference
echo.
echo   Examples:
echo     run.bat cli info book.pdf
echo     run.bat cli export book.pdf -o out.pdf --gutter 0.75in
echo     run.bat test -k layout
echo.
goto :done

:fail
echo.
echo [deckle] command failed ^(exit %errorlevel%^).
echo          Try: run.bat doctor
echo.
pause
exit /b 1

:done
endlocal
exit /b 0
