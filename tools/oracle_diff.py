"""Development-time cross-check against the ``pdfimpose`` oracle.

**Development-only.** This script is not part of the shipped application and
is not exercised by the test suite -- see ``tools/README.md``. ``pdfimpose``
is AGPL-3.0 (and pulls in AGPL PyMuPDF via ``cpdf``), so it must be installed
in a **throwaway virtualenv**, never in this project's own venv. Installing
it into the project venv will fail ``tests/test_license_audit.py`` by
design: ``FORBIDDEN_DISTRIBUTIONS`` there denylists both ``pdfimpose`` and
``cpdf`` alongside ``pymupdf``/``fitz``.

``pdfimpose`` is one of only two surveyed imposition tools that split
multi-signature booklets correctly, and it never rescales page content --
which makes it a genuinely useful reference for the one thing this script
checks: does Deckle's saddle-stitch signature math agree with a known-good
implementation on where each source page lands?

Usage (from a throwaway venv with ``pdfimpose`` installed)::

    python tools/oracle_diff.py fixture.pdf --group 4

The import of ``pdfimpose`` happens lazily, inside a function body, so that
importing this *module* never requires ``pdfimpose`` to be installed --
only actually running the diff does.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import NamedTuple

_INSTALL_MESSAGE = """\
pdfimpose is not installed in this interpreter.

pdfimpose is AGPL-3.0 and must NEVER be installed in this project's own
virtualenv -- doing so fails tests/test_license_audit.py by design (see
tools/README.md). Install it in a separate, throwaway virtualenv instead:

    python -m venv /tmp/oracle-venv
    /tmp/oracle-venv/bin/pip install pdfimpose
    /tmp/oracle-venv/bin/python tools/oracle_diff.py <fixture.pdf>
"""


class PlacementCell(NamedTuple):
    """Where one source page landed after imposition."""

    source_page: int
    sheet: int
    side: str  # "front" | "back"
    cell: int  # position within the sheet's grid, reading order


def _load_pdfimpose():
    """Imports pdfimpose lazily. Never call this at module import time."""
    try:
        import pdfimpose.schema.saddle as pdfimpose_saddle
    except ImportError:
        print(_INSTALL_MESSAGE, file=sys.stderr)
        raise
    return pdfimpose_saddle


def oracle_matrix(fixture_bytes: bytes, group: int) -> list[PlacementCell]:
    """Runs the numbered fixture through pdfimpose and returns its
    source-page -> (sheet, side, cell) placement matrix.

    pdfimpose's ``impose()`` accepts ``io.BytesIO`` at both ends, so this
    needs no temp files.
    """
    saddle = _load_pdfimpose()
    source = io.BytesIO(fixture_bytes)
    output = io.BytesIO()
    saddle.impose(
        [source],
        output,
        signature=(2, 1),
        group=group,
        bind="left",
    )
    return _matrix_from_imposed_pdf(output.getvalue())


def _matrix_from_imposed_pdf(imposed_pdf_bytes: bytes) -> list[PlacementCell]:
    """Placeholder for the pdfimpose-side matrix extraction.

    A real run reads the numbered fixture's page labels back out of the
    imposed PDF (each fixture page renders its own source-page number) and
    reconstructs (sheet, side, cell) from position on each imposed sheet.
    Left unimplemented here deliberately: the numbering scheme is tied to
    whichever fixture the developer supplies, and hardcoding one here would
    make this script silently pass against the wrong fixture. Fill this in
    against your fixture's actual page-numbering convention before relying
    on the diff.
    """
    raise NotImplementedError(
        "wire up label extraction for your numbered fixture before running the diff"
    )


def deckle_matrix(fixture_bytes: bytes, group: int) -> list[PlacementCell]:
    """Placeholder for Deckle's own source-page -> (sheet, side, cell)
    matrix, to be filled in against whatever Deckle imposition entry point
    exists at the time this script is run. Deliberately not imported from
    ``deckle`` at module scope -- see tools/README.md: nothing in tools/ is
    a dependency of the shipped package, and this keeps that true even for
    dev-time wiring.
    """
    raise NotImplementedError("wire up deckle_matrix() against Deckle's imposition entry point")


def diff_matrices(
    oracle: list[PlacementCell], deckle: list[PlacementCell]
) -> list[tuple[PlacementCell | None, PlacementCell | None]]:
    """Pairs up placements by source page and returns only the mismatches."""
    by_page_oracle = {cell.source_page: cell for cell in oracle}
    by_page_deckle = {cell.source_page: cell for cell in deckle}
    mismatches: list[tuple[PlacementCell | None, PlacementCell | None]] = []
    for page in sorted(set(by_page_oracle) | set(by_page_deckle)):
        o = by_page_oracle.get(page)
        d = by_page_deckle.get(page)
        if o != d:
            mismatches.append((o, d))
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="numbered PDF fixture to impose")
    parser.add_argument("--group", type=int, default=4, help="pages per signature group")
    args = parser.parse_args(argv)

    fixture_bytes = args.fixture.read_bytes()

    try:
        oracle = oracle_matrix(fixture_bytes, args.group)
    except ImportError:
        return 1

    deckle = deckle_matrix(fixture_bytes, args.group)

    mismatches = diff_matrices(oracle, deckle)
    if mismatches:
        print(f"{len(mismatches)} mismatch(es) between pdfimpose and Deckle:")
        for oracle_cell, deckle_cell in mismatches:
            print(f"  oracle={oracle_cell} deckle={deckle_cell}")
        return 1

    print("No mismatches: Deckle's imposition matrix agrees with pdfimpose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
