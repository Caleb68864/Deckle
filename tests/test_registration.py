"""Front/back registration offset: making the back land behind the front.

Every comparable tool ends at a PDF, so none of them can know what a
specific printer does to the second side. Deckle owns the print path, so
it can: two numbers measured once from a printed target, applied to every
back-side face.

What it corrects, precisely: a CONSTANT translation. Consumer duplex
misregistration is mostly that, and it is worse on a manual-duplex reload
because the stack is re-registered by hand against the paper guides. It
cannot correct rotational skew or scale error, and nothing here should
imply otherwise.

Sign convention, fixed here so the rest of the code can rely on it: the
stored numbers are the CORRECTION, the distance the back-side content is
moved when printing, in PDF points with +x right and +y up. A target that
shows the back sitting 3pt to the left of where it belongs is corrected
with ``back_offset_x_pt = +3``.
"""

from __future__ import annotations

import json
import os

import pikepdf
import pytest

from deckle.core.profiles import BUILTIN_PRESETS, PrinterProfile


def _profile(**overrides) -> PrinterProfile:
    base = dict(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    )
    base.update(overrides)
    return PrinterProfile(**base)


# --- the profile carries the numbers ------------------------------------


def test_a_profile_defaults_to_no_correction():
    """An uncalibrated printer must behave exactly as it did before the
    feature existed -- zero is the identity, not a guess."""
    profile = _profile()

    assert profile.back_offset_x_pt == 0.0
    assert profile.back_offset_y_pt == 0.0


def test_every_builtin_preset_defaults_to_no_correction():
    """The presets are generic stand-ins for printers nobody measured.
    Shipping a nonzero correction in one would be inventing a measurement."""
    for name, preset in BUILTIN_PRESETS.items():
        assert preset.back_offset_x_pt == 0.0, name
        assert preset.back_offset_y_pt == 0.0, name


def test_the_offsets_survive_a_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")

    _profile(back_offset_x_pt=3.5, back_offset_y_pt=-2.25).save("Calibrated")

    loaded = PrinterProfile.load("Calibrated")
    assert loaded.back_offset_x_pt == 3.5
    assert loaded.back_offset_y_pt == -2.25


def test_a_profile_written_before_this_feature_still_loads(tmp_path, monkeypatch):
    """Adding a field must not orphan every profile already on disk."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    directory = tmp_path / "deckle" / "printer_profiles"
    directory.mkdir(parents=True)
    (directory / "Old.json").write_text(
        json.dumps(
            {
                "version": 1,
                "flip_axis": "long",
                "output_face": "down",
                "feed_edge": "top",
                "reverse_stack": True,
                "imageable_area_pt": [18.0, 18.0, 18.0, 18.0],
                "calibrated_at": "",
                "calibration_version": 0,
            }
        ),
        encoding="utf-8",
    )

    loaded = PrinterProfile.load("Old")

    assert loaded.back_offset_x_pt == 0.0
    assert loaded.flip_axis == "long"


def test_a_profile_from_a_newer_build_loads_instead_of_raising(tmp_path, monkeypatch):
    """`cls(**data)` is a schema contract whether or not it was meant as
    one -- the same latent break already fixed once for LayoutSettings.
    Adding a field today is exactly the change that trips it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    directory = tmp_path / "deckle" / "printer_profiles"
    directory.mkdir(parents=True)
    payload = {
        "version": 1,
        "flip_axis": "short",
        "output_face": "up",
        "feed_edge": "bottom",
        "reverse_stack": False,
        "imageable_area_pt": [18.0, 18.0, 18.0, 18.0],
        "calibrated_at": "",
        "calibration_version": 0,
        "back_offset_x_pt": 1.0,
        "back_offset_y_pt": 2.0,
        "skew_correction_deg": 0.4,  # a field from some future build
    }
    (directory / "Future.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = PrinterProfile.load("Future")

    assert loaded.back_offset_x_pt == 1.0
    assert loaded.flip_axis == "short"


# --- applying it to the sheet -------------------------------------------

import re  # noqa: E402

from pikepdf.canvas import ContentStreamBuilder  # noqa: E402

from deckle.core import export  # noqa: E402
from deckle.core.export import export as export_fn  # noqa: E402
from deckle.core.layout import GutterShiftStrategy  # noqa: E402
from deckle.core.models import LayoutSettings, SourcePage, SourceRef  # noqa: E402

LETTER = (612.0, 792.0)


def _plan(tmp_path, n_pages=4):
    src = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        page = pdf.add_blank_page(page_size=(400.0, 600.0))
        # Real ink. A blank page rasterizes to the same white bitmap
        # wherever it is placed, so a shift would be undetectable and the
        # render-level tests below would pass on nothing.
        builder = ContentStreamBuilder()
        builder.push()
        builder.append_rectangle(40.0, 40.0, 320.0, 520.0)
        builder.fill()
        builder.pop()
        page.contents_add(b"q\n" + builder.build() + b"Q\n")
    pdf.save(src)
    pdf.close()
    pages = [
        SourcePage(
            ref=SourceRef(
                path=src, page_index=i, sha256="a" * 64, width_pt=400.0, height_pt=600.0
            ),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n_pages)
    ]
    return GutterShiftStrategy().impose(
        pages, LayoutSettings(paper=LETTER, gutter_pt=18.0, binding_edge="left")
    )


def _streams(path):
    with pikepdf.open(path) as pdf:
        out = []
        for page in pdf.pages:
            contents = page.obj.get("/Contents")
            raw = (
                b"".join(s.read_bytes() for s in contents)
                if isinstance(contents, pikepdf.Array)
                else contents.read_bytes()
            )
            out.append(re.sub(rb"/Fx\S+", b"/Fx", raw))
        return out


def _shifts(stream: bytes) -> list[tuple[float, float]]:
    """Every whole-face translation in a content stream, as (dx, dy)."""
    return [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(rb"1 0 0 1 (-?[\d.]+) (-?[\d.]+) cm", stream)
    ]


def test_the_correction_moves_the_back_face(tmp_path):
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(_plan(tmp_path), out, back_offset_pt=(5.0, -3.0))

    # Pages interleave front, back, front, back.
    assert _shifts(_streams(out)[1]) == [(5.0, -3.0)]


def test_the_correction_never_moves_the_front_face(tmp_path):
    """The front is the reference the back is being aligned TO. Moving it
    too would correct nothing and shift the whole book on the paper."""
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(_plan(tmp_path), out, back_offset_pt=(5.0, -3.0))

    assert _shifts(_streams(out)[0]) == []


def test_no_correction_leaves_the_page_byte_for_byte_unchanged(tmp_path):
    """Zero is the identity, not a no-op translation. An uncalibrated
    printer must produce exactly the file it produced before."""
    plan = _plan(tmp_path)
    without = os.path.join(str(tmp_path), "a.pdf")
    zeroed = os.path.join(str(tmp_path), "b.pdf")

    export_fn(plan, without)
    export_fn(plan, zeroed, back_offset_pt=(0.0, 0.0))

    assert _streams(without) == _streams(zeroed)


def test_a_half_turned_back_pass_negates_the_correction(tmp_path):
    """A flip about the sheet's horizontal edge rotates the back 180, and
    a point reflection maps a translation to its negation -- so a
    correction expressed on the PAPER has to be inverted in page space to
    survive the turn. Applying it unchanged would move the back exactly
    twice as far wrong."""
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(
        _plan(tmp_path), out, side="back", rotate_180=True, back_offset_pt=(5.0, -3.0)
    )

    assert _shifts(_streams(out)[0]) == [(-5.0, 3.0)]


def test_an_unturned_back_pass_applies_the_correction_as_given(tmp_path):
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(_plan(tmp_path), out, side="back", back_offset_pt=(5.0, -3.0))

    assert _shifts(_streams(out)[0]) == [(5.0, -3.0)]


def test_a_corrected_face_still_verifies_and_keeps_its_page_size(tmp_path):
    """The correction moves content, never the media box -- a shifted
    sheet is still the sheet the plan was laid out for."""
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(_plan(tmp_path), out, back_offset_pt=(5.0, -3.0))

    with pikepdf.open(out) as pdf:
        for page in pdf.pages:
            box = [float(v) for v in page.mediabox]
            assert (box[2] - box[0], box[3] - box[1]) == LETTER


# --- reaching it from the command line ----------------------------------

from deckle.cli import _parse_offset_pair, main  # noqa: E402


def _numbered_source(tmp_path, n=4) -> str:
    path = os.path.join(str(tmp_path), "book.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n):
        pdf.add_blank_page(page_size=(400.0, 600.0))
    pdf.save(path)
    pdf.close()
    return path


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3,-2", (3.0, -2.0)),
        ("3pt,-2pt", (3.0, -2.0)),
        ("0,0", (0.0, 0.0)),
        ("-0.5in,0.25in", (-36.0, 18.0)),
        (" 1 , 2 ", (1.0, 2.0)),
    ],
)
def test_an_offset_pair_parses_with_units_and_signs(text, expected):
    """Signed, because a correction goes both ways -- and the existing
    length parser rejects a minus sign outright."""
    assert _parse_offset_pair(text) == expected


@pytest.mark.parametrize("text", ["", "3", "3,", "a,b", "1,2,3", "3in"])
def test_a_malformed_offset_pair_is_rejected(text):
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_offset_pair(text)


def test_the_flag_shifts_the_back_of_an_ordinary_export(tmp_path):
    """A hardware duplexer misregisters too, so the correction is not
    limited to a manual pass."""
    src = _numbered_source(tmp_path)
    out = os.path.join(str(tmp_path), "out.pdf")

    rc = main(["export", src, "-o", out, "--back-offset", "5,-3"])

    assert rc == 0
    assert _shifts(_streams(out)[1]) == [(5.0, -3.0)]
    assert _shifts(_streams(out)[0]) == []


def test_a_calibrated_profile_supplies_the_correction(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    # ``flip_axis="long"`` on the default PORTRAIT paper turns the sheet
    # about its vertical edge, so the back pass is not half-turned and the
    # correction survives unnegated. This test is about the correction
    # reaching the export, not about the turn -- which is tested on its
    # own below.
    _profile(
        flip_axis="long", reverse_stack=False, back_offset_x_pt=4.0,
        back_offset_y_pt=1.5,
    ).save("Measured")
    src = _numbered_source(tmp_path)
    out = os.path.join(str(tmp_path), "backs.pdf")

    rc = main(
        ["export", src, "-o", out, "--pass", "back", "--profile", "Measured"]
    )

    assert rc == 0
    assert _shifts(_streams(out)[0]) == [(4.0, 1.5)]


def test_the_flag_overrides_the_profile(tmp_path, monkeypatch):
    """Trying a value has to be possible without editing a JSON file in
    the OS config directory between every attempt."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")
    # Portrait paper plus a long-edge flip: no half turn, so the flag's
    # value reaches the page unnegated and the override is readable.
    _profile(
        flip_axis="long", reverse_stack=False, back_offset_x_pt=4.0,
        back_offset_y_pt=1.5,
    ).save("Measured")
    src = _numbered_source(tmp_path)
    out = os.path.join(str(tmp_path), "backs.pdf")

    main(
        ["export", src, "-o", out, "--pass", "back", "--profile", "Measured",
         "--back-offset", "9,9"]
    )

    assert _shifts(_streams(out)[0]) == [(9.0, 9.0)]


def test_a_correction_is_reported_so_it_is_never_silent(tmp_path, capsys):
    """A geometry change nobody asked for on this invocation -- it came
    from a file -- must not be applied without saying so."""
    monkeypatch_free_src = _numbered_source(tmp_path)
    out = os.path.join(str(tmp_path), "out.pdf")

    main(["export", monkeypatch_free_src, "-o", out, "--back-offset", "5,-3"])

    stdout = capsys.readouterr().out.lower()
    assert "registration" in stdout and "5" in stdout


def test_no_correction_says_nothing(tmp_path, capsys):
    src = _numbered_source(tmp_path)
    out = os.path.join(str(tmp_path), "out.pdf")

    main(["export", src, "-o", out])

    assert "registration" not in capsys.readouterr().out.lower()


# --- the desktop print path ---------------------------------------------
#
# The backend already holds a PrinterProfile, so leaving these fields
# unread there would mean a measured calibration silently doing nothing on
# the path the README calls Deckle's whole argument.


def test_the_print_path_applies_the_correction_to_a_back(tmp_path):
    from deckle.app import backend as backend_mod

    plan = _plan(tmp_path)

    plain = backend_mod._render_sheet_side(plan, 0, "back", 72, False, False)
    shifted = backend_mod._render_sheet_side(
        plan, 0, "back", 72, False, False, back_offset_pt=(12.0, 0.0)
    )

    assert plain.rgba != shifted.rgba, "the correction never reached the render"


def test_the_print_path_leaves_fronts_alone(tmp_path):
    from deckle.app import backend as backend_mod

    plan = _plan(tmp_path)

    plain = backend_mod._render_sheet_side(plan, 0, "front", 72, False, False)
    shifted = backend_mod._render_sheet_side(
        plan, 0, "front", 72, False, False, back_offset_pt=(12.0, 0.0)
    )

    assert plain.rgba == shifted.rgba


def test_the_print_path_is_unchanged_when_uncalibrated(tmp_path):
    from deckle.app import backend as backend_mod

    plan = _plan(tmp_path)

    plain = backend_mod._render_sheet_side(plan, 0, "back", 72, False, False)
    zeroed = backend_mod._render_sheet_side(
        plan, 0, "back", 72, False, False, back_offset_pt=(0.0, 0.0)
    )

    assert plain.rgba == zeroed.rgba


# --- the turned back pass, where the two halves have to agree -----------
#
# Every test above this line leaves `rotate_backs` False, so none of them
# ever exercises the one combination that bites: a half turn AND a
# non-zero correction. Either builtin preset produces that combination on
# a calibrated printer -- `generic_face_down_reversed` (long-edge) on a
# LANDSCAPE sheet, `generic_face_up_in_order` (short-edge) on a PORTRAIT
# one -- because the turn is the flip axis compared against the paper's
# vertical edge, not a property of the preset alone.


def _rasterized(path: str, page_index: int, dpi: int = 72):
    """Rasterize one page of ``path``, the way the print backend does."""
    import pypdfium2 as pdfium

    from deckle.core.render import rasterize_page

    pdf = pdfium.PdfDocument(path)
    try:
        image = rasterize_page(pdf, page_index, scale=dpi / 72).convert("RGBA")
        return image.size, image.tobytes()
    finally:
        pdf.close()


def test_a_turned_back_pass_prints_what_the_exported_pass_prints(tmp_path):
    """The desktop print path and ``pass_export`` describe the same
    physical pass of the same paper through the same printer, so they must
    put the ink in the same place.

    ``export`` negates the correction when it is told the face will be
    turned. A caller that turns the page itself, afterwards, without
    telling ``export``, gets the correction applied at twice its size in
    the wrong direction -- and the file a copy shop is handed disagrees
    with what Deckle prints itself.
    """
    from deckle.app import backend as backend_mod

    plan = _plan(tmp_path)
    offset = (5.0, -3.0)

    printed = backend_mod._render_sheet_side(
        plan, 0, "back", 72, True, False, back_offset_pt=offset
    )

    exported = os.path.join(str(tmp_path), "pass-back.pdf")
    export_fn(
        plan, exported, sheets=[0], side="back", rotate_180=True, back_offset_pt=offset
    )
    (width, height), rgba = _rasterized(exported, 0)

    assert (printed.width, printed.height) == (width, height)
    assert printed.rgba == rgba, (
        "the desktop print path and the exported back pass disagree about "
        "where the registration correction goes"
    )


def test_a_turned_back_pass_negates_the_correction_on_the_print_path(tmp_path):
    """Stated without reference to the exporter's own arithmetic: on a
    turned pass the ink lands where the NEGATED correction puts it.

    Applied unturned it would move the back exactly twice as far wrong as
    leaving the printer uncalibrated, which is worse than the defect the
    correction exists to remove.
    """
    from deckle.app import backend as backend_mod

    plan = _plan(tmp_path)

    printed = backend_mod._render_sheet_side(
        plan, 0, "back", 72, True, False, back_offset_pt=(5.0, -3.0)
    )

    # The same face, shifted by hand by the negation and then turned --
    # no `rotate_180=` anywhere, so `export` never negates anything.
    expected = os.path.join(str(tmp_path), "negated.pdf")
    export_fn(plan, expected, sheets=[0], side="back", back_offset_pt=(-5.0, 3.0))
    export.rotate_pages_180(expected)

    assert printed.rgba == _rasterized(expected, 0)[1]
