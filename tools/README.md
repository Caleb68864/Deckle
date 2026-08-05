# `tools/` — development-only, never shipped

Everything in this directory is **development-only**. Nothing here is imported by
`deckle/` or `tests/`, nothing here is a project dependency, and nothing here is
part of the distributed application.

`tools/oracle_diff.py` drives [`pdfimpose`](https://framagit.org/spalax/pdfimpose),
which is **AGPL-3.0** (and pulls in AGPL PyMuPDF). It must be installed **only in a
throwaway virtualenv** that you create and destroy for the sole purpose of running
this script — never in this project's own virtualenv.

**Installing `pdfimpose` in the project venv fails `tests/test_license_audit.py` by
design.** `FORBIDDEN_DISTRIBUTIONS` in that test denylists `pdfimpose` (and `cpdf`,
its underlying AGPL dependency) alongside `pymupdf`/`fitz`, so the moment either
distribution lands in Deckle's own dependency closure, the license audit goes red.
That's not a bug to work around — it's the audit doing its job. If you see that
failure, you installed the oracle in the wrong place; remove it from the project
venv and reinstall it in a separate throwaway one.

## Why keep `pdfimpose` around at all

`pdfimpose` is one of only two surveyed imposition tools that split multi-signature
booklets correctly, and it never rescales page content -- which makes it a genuinely
good reference for checking Deckle's own saddle-stitch signature math. `oracle_diff.py`
uses it as a manual, developer-run cross-check: it imposes a numbered fixture with
`pdfimpose.schema.saddle.impose(..., signature=(2, 1), group=N, bind="left")` and
diffs the resulting source-page -> (sheet, side, cell) matrix against Deckle's own
imposition output. It is not, and must never become, part of the test suite or the
shipped application.

## Usage

```sh
# In a throwaway venv, NOT the project venv:
python -m venv /tmp/oracle-venv
/tmp/oracle-venv/bin/pip install pdfimpose

/tmp/oracle-venv/bin/python tools/oracle_diff.py path/to/fixture.pdf --group 4
```

If `pdfimpose` isn't importable, the script prints instructions for installing it
into a throwaway venv and exits non-zero -- it never attempts to fall back to
anything that would make `pdfimpose` a real dependency of this project.
