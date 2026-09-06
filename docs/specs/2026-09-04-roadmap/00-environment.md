# 00 — Environment every spec in this directory assumes

Read this before any other file here.

## Setup on a fresh clone (Linux or macOS)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install numpy            # until R0.2 lands
.venv/bin/python -m deckle.cli dummy -o tests/fixtures/sample.pdf --pages 2   # until R0.1 lands
```

On Windows use `run.bat deps` and `run.bat test`; the fixture line is the same
with `.venv\Scripts\python.exe`.

## Running tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider
```

Expected on the unfixed tree at commit `08e7f49` with the setup above:
**1507 passed, 22 skipped, 2 failed** (`test_packaging_audit.py::test_the_spec_records_why_pyinstallers_licence_permits_this`
is R0.3; `test_hardening_io.py::test_export_over_a_read_only_file_says_it_is_read_only`
is R0.4). Any other failure is yours.

`pytest -k <pattern>` with no match exits 5, not 0. A spec's acceptance row
that relies on `-k` therefore fails loudly if the test was never written.

## Entry points

- `python -m deckle` **launches the GUI** and blocks. Never run it from a script.
- `python -m deckle.cli ...` is the headless CLI. Use it for `dummy`, `export`,
  `impose`, `info`, `schedule`, `crop-preview`.

## Repository rules that apply to every commit

- The pre-commit hook (`scripts/hooks/pre-commit`) refuses a code commit that
  does not also change `docs/decisions.md`, and refuses one whose added lines
  still contain `<FILL-IN>`. Every spec here ends with the entry to paste.
- `deckle/core` must not import Qt or `deckle.app`; `tests/test_core_purity.py`
  enforces it. `deckle/app` imports Qt lazily inside functions where the
  module also has pure helpers at top; follow the pattern of the file you edit.
- `tests/test_docs_coverage.py` checks that every module under `deckle/` has a
  page under `docs/api/`. A new module needs a new `.rst` there.
- `tests/test_license_audit.py` and `test_packaging_audit.py` walk the runtime
  dependency closure. Do not add a runtime dependency without reading them.
- Naming and prose style: read three neighbouring docstrings before writing
  one. The codebase records *why* in docstrings and comments, not what.

## Coordinates and units

Everything in `deckle.core` is in PDF points, origin bottom-left. Unit
conversion is currently duplicated: `to_points`/`from_points` in
`deckle/app/views/layout_panel.py:143-160` and `_UNIT_TO_PT` in
`deckle/cli.py:80`. Spec M5 consolidates them into `deckle/core/paper.py`;
until it lands, use the one in the layer you are editing and do not add a
third copy.
