#!/usr/bin/env bash
# Deckle development launcher and Linux bundle builder.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="python3"
if [[ -x .venv/bin/python ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif [[ -x venv/bin/python ]]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
fi

usage() {
    cat <<'EOF'
Deckle development launcher

Usage:
  ./run.sh                  Launch the GUI from this working tree
  ./run.sh cli <args...>    Run the headless CLI
  ./run.sh test [args...]   Run pytest (offscreen Qt)
  ./run.sh deps             Install/refresh development dependencies
  ./run.sh doctor           Show Python, dependency, and printer details
  ./run.sh docs             Build the HTML API reference
  ./run.sh package          Build and audit Linux executables in dist/deckle
  ./run.sh help             Show this help

Examples:
  ./run.sh cli info book.pdf
  ./run.sh cli export book.pdf -o out.pdf --gutter 0.75in
  ./run.sh test -k layout
EOF
}

fail() {
    printf '[deckle] %s\n' "$*" >&2
    exit 1
}

require_python() {
    "$PYTHON" --version >/dev/null 2>&1 || fail "Python 3.11+ was not found. Install Python or create a .venv."
}

CMD="${1:-}"
if [[ -n "$CMD" ]]; then shift; fi

case "$CMD" in
    "")
        require_python
        printf '[deckle] launching GUI...\n'
        exec "$PYTHON" -m deckle
        ;;
    cli)
        require_python
        exec "$PYTHON" -m deckle.cli "$@"
        ;;
    test)
        require_python
        QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" exec "$PYTHON" -m pytest -q "$@"
        ;;
    deps)
        require_python
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install -e '.[dev]'
        "$PYTHON" -m pip install psutil
        ;;
    doctor)
        require_python
        "$PYTHON" - <<'PY'
import importlib.metadata as metadata
import sys

print("[deckle] interpreter:")
print("  ", sys.executable)
print("  ", sys.version.split()[0])
print("[deckle] dependencies:")
for name in ("pikepdf", "pypdfium2", "img2pdf", "natsort", "Pillow", "PySide6"):
    try:
        version = metadata.version(name)
    except metadata.PackageNotFoundError:
        version = "not installed"
    print("  ", name.ljust(12), version)

try:
    from PySide6.QtPrintSupport import QPrinterInfo
    printers = QPrinterInfo.availablePrinters()
except Exception as exc:
    print("[deckle] Qt printer discovery failed:", exc)
else:
    print("[deckle] printers visible to Qt:")
    if printers:
        for printer in printers:
            print("  ", printer.printerName())
    else:
        print("   none found")
PY
        ;;
    docs)
        require_python
        "$PYTHON" -c 'import sphinx' 2>/dev/null || fail "Sphinx is missing. Install it with: $PYTHON -m pip install -e '.[docs]'"
        "$PYTHON" -m sphinx -b html -W --keep-going docs/api docs/api/_build/html
        printf '[deckle] wrote docs/api/_build/html/index.html\n'
        ;;
    package)
        require_python
        BUILD_PYTHON="$ROOT_DIR/.buildenv/bin/python"
        if [[ ! -x "$BUILD_PYTHON" ]]; then
            printf '[deckle] creating clean package environment in .buildenv...\n'
            "$PYTHON" -m venv .buildenv
            BUILD_PYTHON="$ROOT_DIR/.buildenv/bin/python"
            "$BUILD_PYTHON" -m pip install --upgrade pip
            "$BUILD_PYTHON" -m pip install -e '.[package]'
        fi
        printf '[deckle] building Linux executables...\n'
        "$BUILD_PYTHON" -m PyInstaller --noconfirm --distpath dist --workpath build/pyinstaller packaging/deckle.spec
        printf '[deckle] auditing the built bundle...\n'
        QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$PYTHON" -m pytest tests/test_packaging_audit.py -q
        printf '[deckle] wrote dist/deckle/deckle and dist/deckle/deckle-cli\n'
        printf '[deckle] Linux packaging has not been previously validated in CI. Check the audited bundle before distributing it.\n'
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        fail "unknown command: $CMD"
        ;;
esac
