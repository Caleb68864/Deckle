"""Numbers the CLI took and should have refused at the prompt (BH3, BH4).

Found by feeding every subcommand out-of-range values (2026-11-21). Both
passed argparse as plain ``int``: ``crop-preview --dpi`` went on to pdfium,
which answered ``0`` with *"Crop exceeds page dimensions"*, ``-1`` with
*"Width and height must be >= 0"*, and ``100000`` by exhausting memory until
the kernel killed the process; ``--sewing-stations -3`` was written into the
project and read as "no stations" everywhere, silently.

Checked at parse time, so none of these tests rasterises anything -- which
matters for ``100000``, which on the old code cannot be run safely.
"""

from __future__ import annotations

import pytest

from deckle.cli.options import build_parser
MAX_PREVIEW_DPI = 600  # deckle.cli.values.MAX_PREVIEW_DPI, spelled out so the old tree collects

SOURCE = "book.pdf"  # never opened: parsing is all that is under test


def _refused(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(list(argv))
    assert caught.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize("dpi", ["0", "-1", "100000", str(MAX_PREVIEW_DPI + 1), "72.5", "x"])
def test_crop_preview_refuses_a_dpi_it_cannot_draw(capsys, dpi):
    err = _refused(capsys, "crop-preview", SOURCE, "-o", "o.png", f"--dpi={dpi}")
    assert "invalid dpi" in err
    assert f"1 to {MAX_PREVIEW_DPI}" in err


@pytest.mark.parametrize("dpi", ["1", "72", str(MAX_PREVIEW_DPI)])
def test_crop_preview_still_takes_a_sensible_dpi(dpi):
    args = build_parser().parse_args(["crop-preview", SOURCE, "-o", "o.png", "--dpi", dpi])
    assert args.dpi == int(dpi)


@pytest.mark.parametrize("command", ["impose", "export", "schedule"])
def test_a_negative_station_count_is_refused(capsys, command):
    output = [] if command == "schedule" else ["-o", "out"]
    err = _refused(capsys, command, SOURCE, *output, "--sewing-stations=-3")
    assert "invalid sewing stations" in err


@pytest.mark.parametrize("count", ["0", "3", "20"])
def test_zero_still_means_no_stations(count):
    args = build_parser().parse_args(["schedule", SOURCE, "--sewing-stations", count])
    assert args.sewing_stations == int(count)


def test_the_documented_ceiling_is_the_one_enforced():
    from deckle.cli.values import MAX_PREVIEW_DPI as enforced

    assert enforced == MAX_PREVIEW_DPI
