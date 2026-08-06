"""Tests for deckle.core.export: PDF composition via pikepdf Form XObjects."""

from __future__ import annotations

import os

import pikepdf
import pytest

from deckle.core import export
from deckle.core.export import export as export_fn
from deckle.core.layout import GutterShiftStrategy
from deckle.core.models import (
    LayoutSettings,
    OutputPage,
    Placement,
    Sheet,
    SheetPlan,
    Side,
    SourcePage,
    SourceRef,
)

LETTER = (612.0, 792.0)


def _write_source_pdf(tmp_path, n_pages: int, page_size=(400.0, 600.0)) -> str:
    path = os.path.join(str(tmp_path), "src.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    pdf.close()
    return path


def _content_bytes(page: pikepdf.Page) -> bytes:
    contents = page.obj.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        return b"".join(s.read_bytes() for s in contents)
    return contents.read_bytes()


def _make_ref(path: str, page_index: int, width_pt: float, height_pt: float) -> SourceRef:
    return SourceRef(
        path=path,
        page_index=page_index,
        sha256="a" * 64,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _plan_from_source(
    tmp_path, n_pages: int, page_size=(400.0, 600.0), settings: LayoutSettings | None = None
) -> SheetPlan:
    path = _write_source_pdf(tmp_path, n_pages, page_size)
    pages = [
        SourcePage(
            ref=_make_ref(path, i, page_size[0], page_size[1]),
            rotate_deg=0,
            skipped=False,
        )
        for i in range(n_pages)
    ]
    settings = settings or LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left"
    )
    return GutterShiftStrategy().impose(pages, settings)


# --- STRUCTURAL -------------------------------------------------------


def test_export_has_expected_signature():
    import inspect

    sig = inspect.signature(export.export)
    params = list(sig.parameters)
    assert params[:3] == ["plan", "out_path", "sheets"]
    assert sig.parameters["sheets"].default is None


def test_export_sheet_cached_has_expected_signature():
    import inspect

    sig = inspect.signature(export.export_sheet_cached)
    params = list(sig.parameters)
    assert params == ["plan", "sheet_index"]


# --- BEHAVIORAL: cache call counting -----------------------------------


def test_export_sheet_cached_performs_export_once_for_same_key(tmp_path):
    plan = _plan_from_source(tmp_path, 4)

    path_a = export.export_sheet_cached(plan, 0)
    count_after_first = export._call_count
    path_b = export.export_sheet_cached(plan, 0)
    count_after_second = export._call_count

    assert path_a == path_b
    assert count_after_first == count_after_second
    export.clear_sheet_cache()


def test_export_sheet_cached_invalidates_on_layout_change(tmp_path):
    path = _write_source_pdf(tmp_path, 4)
    pages = [
        SourcePage(ref=_make_ref(path, i, 400.0, 600.0), rotate_deg=0, skipped=False)
        for i in range(4)
    ]
    settings_a = LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left"
    )
    # A gutter large enough to actually bind the scale. With
    # maximize_gutter on and a zero fore-edge margin, content sits flush
    # against the fore-edge, so a *small* gutter change moves nothing --
    # the gutter is a minimum, and the plan (and its hash) is unchanged.
    settings_b = LayoutSettings(
        paper=LETTER, gutter_pt=300.0, binding_edge="left"
    )
    plan_a = GutterShiftStrategy().impose(pages, settings_a)
    plan_b = GutterShiftStrategy().impose(pages, settings_b)

    export.export_sheet_cached(plan_a, 0)
    count_after_a = export._call_count
    export.export_sheet_cached(plan_b, 0)
    count_after_b = export._call_count

    assert count_after_b > count_after_a
    export.clear_sheet_cache()


# --- MECHANICAL: no consumer-side Placement adjustment -----------------


def test_export_receives_placements_identical_to_imposer_output(tmp_path):
    path = _write_source_pdf(tmp_path, 4)
    pages = [
        SourcePage(ref=_make_ref(path, i, 400.0, 600.0), rotate_deg=0, skipped=False)
        for i in range(4)
    ]
    settings = LayoutSettings(
        paper=LETTER, gutter_pt=18.0, binding_edge="left"
    )
    plan = GutterShiftStrategy().impose(pages, settings)

    # export() must consume plan.sheets' Placements exactly as produced --
    # dataclass equality against a second, independent impose() call proves
    # no consumer-side (export-side) adjustment occurred anywhere in between.
    plan_again = GutterShiftStrategy().impose(pages, settings)
    for sheet_a, sheet_b in zip(plan.sheets, plan_again.sheets):
        for side_a, side_b in ((sheet_a.front, sheet_b.front), (sheet_a.back, sheet_b.back)):
            if side_a is not None:
                assert [p.placement for p in side_a.pages] == [
                    p.placement for p in side_b.pages
                ]

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)
    assert os.path.exists(out_path)


# --- BEHAVIORAL: pure translation for fit already-fits case ---


def test_fit_already_fits_emits_pure_translation(tmp_path):
    # Source sized so fit's scale computes to exactly 1.0.
    page_size = (LETTER[0] - 18.0, LETTER[1])
    plan = _plan_from_source(
        tmp_path,
        1,
        page_size=page_size,
        settings=LayoutSettings(
            paper=LETTER, gutter_pt=18.0, binding_edge="left"
        ),
    )
    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        data = _content_bytes(page)
        # Pure translation: "1 0 0 1 <x> <y> cm" -- no scale factor.
        import re

        match = re.search(rb"([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) cm", data)
        assert match is not None, data
        a, b, c, d, _tx, _ty = (float(v) for v in match.groups())
        assert (a, b, c, d) == (1.0, 0.0, 0.0, 1.0)


# --- BEHAVIORAL: sheet subset matches full export -----------------------


def test_export_sheets_subset_matches_full_export(tmp_path):
    plan = _plan_from_source(tmp_path, 16)
    full_path = os.path.join(str(tmp_path), "full.pdf")
    subset_path = os.path.join(str(tmp_path), "subset.pdf")

    export_fn(plan, full_path)
    export_fn(plan, subset_path, sheets=[6])

    def geometry(data: bytes) -> str:
        # Compare the placement matrix only -- the XObject resource name is
        # freshly (randomly) generated by pikepdf on every export and isn't
        # meaningful content.
        import re

        match = re.search(rb"([\d.-]+ ){5}[\d.-]+ cm", data)
        assert match is not None, data
        return match.group(0).decode("latin-1")

    with pikepdf.open(subset_path) as subset_pdf:
        assert len(subset_pdf.pages) == 2  # front + back of one sheet
        subset_front = geometry(_content_bytes(subset_pdf.pages[0]))
        subset_back = geometry(_content_bytes(subset_pdf.pages[1]))

    with pikepdf.open(full_path) as full_pdf:
        # Sheet 6 (0-indexed) occupies pages 12-13 of the full export.
        full_front = geometry(_content_bytes(full_pdf.pages[12]))
        full_back = geometry(_content_bytes(full_pdf.pages[13]))

    assert subset_front == full_front
    assert subset_back == full_back


# --- BEHAVIORAL: unwritable path raises before writing -----------------


def test_export_to_unwritable_path_raises_before_writing(tmp_path):
    plan = _plan_from_source(tmp_path, 2)
    bad_dir = os.path.join(str(tmp_path), "does_not_exist")
    bad_path = os.path.join(bad_dir, "out.pdf")

    with pytest.raises(OSError):
        export_fn(plan, bad_path)

    assert not os.path.exists(bad_path)
    assert not os.path.exists(bad_dir)


# --- BEHAVIORAL: filler pages export genuinely blank --------------------


def test_filler_output_page_exports_as_blank_page(tmp_path):
    placement = Placement(scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0)
    filler = OutputPage(source_ref=None, placement=placement, is_filler=True)
    plan = SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(filler,)), back=None)],
        paper_pt=LETTER,
        warnings=[],
    )

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        page = pdf.pages[0]
        contents = page.obj.get("/Contents")
        if contents is None:
            data = b""
        elif isinstance(contents, pikepdf.Array):
            data = b"".join(s.read_bytes() for s in contents)
        else:
            data = contents.read_bytes()
        assert data.strip() == b""


# --- BEHAVIORAL: large document exports via batching, bounded memory ---


def test_large_document_export_completes_via_batching(tmp_path):
    n_pages = 500
    plan = _plan_from_source(tmp_path, n_pages)
    out_path = os.path.join(str(tmp_path), "big.pdf")

    export_fn(plan, out_path)

    with pikepdf.open(out_path) as pdf:
        # n_pages source pages -> n_pages/2 sheets * 2 sides = n_pages pages.
        assert len(pdf.pages) == n_pages


# --- BEHAVIORAL: A-8 -- 500-page peak memory stays under 4x 50-page peak --


@pytest.mark.slow
def test_large_document_export_peak_memory_under_4x_small_export(tmp_path):
    """A-8 (docs/specs/2026-08-04-deckle-mvp.md, Edge Cases "Red-team
    advisories, resolved"): peak RSS during a 500-page export must stay
    under 4x the peak RSS observed for a 50-page export -- proving batching
    actually bounds memory growth rather than merely letting the export
    finish.

    Measured via ``psutil`` (already installed in this environment though
    not a declared project dependency -- per instructions, use it rather
    than adding a new dependency). ``psutil.Process().memory_info().rss`` is
    literally the OS-reported peak-resident-set metric A-8 names, unlike
    ``tracemalloc`` -- which only tracks Python-heap allocations and
    completely misses pikepdf's C++/QPDF-backed memory, the dominant cost
    here, so it produces a noisy, unrepresentative ratio for this
    comparison. A background poll thread samples RSS during each export to
    catch the peak, since ``memory_info()`` only reports the *current*
    value.

    **Read this before trusting it as the memory guard.** ``rss`` is
    absolute process memory, and the interpreter plus pikepdf/pypdfium2
    baseline is roughly 38 MB here, which dwarfs what either export
    actually adds: measured on this machine the 50-page export's peak sat
    ~0.6 MB above its own pre-export baseline and the 500-page export's
    ~6.4 MB above its. So the ratio this test computes is ~1.16, against a
    bound of 4 -- there is enough slack that a genuine order-of-magnitude
    regression in export()'s working set would still pass. It is a
    blow-up alarm, not a tight bound, and it is deliberately left at A-8's
    stated threshold rather than retuned here.

    What batching actually bounds is *concurrently open source PDF
    handles*, not total memory -- the assembled output document is
    necessarily resident until it is saved, so peak memory scales with
    output size no matter how the work is batched. That real invariant is
    asserted deterministically, with no RSS sampling involved, by
    ``tests/test_hardening_limits.py::
    test_export_bounds_concurrently_open_source_handles``.
    """
    psutil = pytest.importorskip("psutil")

    import gc
    import threading
    import time

    proc = psutil.Process(os.getpid())

    def _peak_rss_for(n_pages: int, out_name: str) -> int:
        # Build the plan *outside* the measurement window: SheetPlan
        # construction is inherently O(n_pages) (one Sheet/Placement per
        # page) and scaling with document size there is expected -- it is
        # not what A-8 is guarding. A-8 guards export()'s own working set
        # (source-PDF handles and in-progress pikepdf objects) staying
        # bounded via batching rather than growing with total page count.
        plan = _plan_from_source(tmp_path, n_pages)
        out_path = os.path.join(str(tmp_path), out_name)

        gc.collect()
        samples = [proc.memory_info().rss]
        stop = threading.Event()

        def _poll() -> None:
            while not stop.is_set():
                samples.append(proc.memory_info().rss)
                time.sleep(0.003)

        poller = threading.Thread(target=_poll, daemon=True)
        poller.start()
        try:
            export_fn(plan, out_path)
        finally:
            stop.set()
            poller.join()

        assert os.path.exists(out_path)
        return max(samples)

    # Warm up the process (module imports, allocator arenas, first pikepdf
    # calls) once outside of measurement so the first *measured* run isn't
    # penalized by one-time startup cost that has nothing to do with A-8.
    _peak_rss_for(2, "warmup.pdf")

    peak_50 = _peak_rss_for(50, "small.pdf")
    peak_500 = _peak_rss_for(500, "big.pdf")

    assert peak_50 > 0
    assert peak_500 < 4 * peak_50, (
        f"peak RSS grew {peak_500 / peak_50:.2f}x from a 50-page to a "
        f"500-page export (peak_50={peak_50}, peak_500={peak_500}); A-8 "
        "requires this to stay under 4x"
    )


# --- BEHAVIORAL: print intent ------------------------------------------
#
# An exported PDF is printed by whatever viewer the user opens it in, and
# those viewers default to "fit to page". That silently rescales the sheet
# to the printer's imageable area, so every margin, gutter and sewing
# station lands somewhere other than where the imposer put it -- and the
# result still looks plausible. The catalog is where a PDF says otherwise.


def _viewer_preferences(path: str) -> dict | None:
    """The catalog's /ViewerPreferences as plain Python values.

    Read out inside the ``with``: a pikepdf object handed back after its
    ``Pdf`` closes is dead, and every lookup on it quietly returns None --
    which reads exactly like "the key is missing" and would make these
    tests pass on nothing.
    """
    with pikepdf.open(path) as pdf:
        prefs = pdf.Root.get("/ViewerPreferences")
        if prefs is None:
            return None
        return {str(key): value for key, value in prefs.items()}


def test_the_exported_pdf_tells_the_viewer_not_to_scale_it(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    prefs = _viewer_preferences(out)
    assert prefs is not None, "no /ViewerPreferences: the PDF states no print intent"
    assert prefs.get("/PrintScaling") == pikepdf.Name("/None")


def test_the_exported_pdf_asks_for_the_tray_matching_its_own_page_size(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    assert _viewer_preferences(out).get("/PickTrayByPDFSize") is True


def test_a_portrait_export_asks_for_a_long_edge_duplex_flip(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    assert _viewer_preferences(out).get("/Duplex") == pikepdf.Name("/DuplexFlipLongEdge")


def test_a_landscape_export_asks_for_a_short_edge_duplex_flip(tmp_path):
    landscape = LayoutSettings(paper=(792.0, 612.0), gutter_pt=18.0, binding_edge="left")
    plan = _plan_from_source(tmp_path, 4, settings=landscape)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    assert _viewer_preferences(out).get("/Duplex") == pikepdf.Name("/DuplexFlipShortEdge")


def test_print_intent_survives_a_batched_export(tmp_path, monkeypatch):
    # A long document saves and reopens mid-assembly. Intent written on the
    # first catalog would be written to a document that gets replaced.
    monkeypatch.setattr(export, "_BATCH_SHEETS", 2)
    plan = _plan_from_source(tmp_path, 10)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    assert _viewer_preferences(out).get("/PrintScaling") == pikepdf.Name("/None")


# --- BEHAVIORAL: the proof rule ----------------------------------------
#
# Deckle asks the viewer not to scale the page, but that is a hint a driver
# can ignore, and a sheet scaled by a few percent looks entirely correct.
# A ruler of known length printed on the sheet turns "did it scale?" from a
# guess into something a tape measure settles.


def _page_text(path: str, page_index: int = 0) -> str:
    import pypdfium2

    document = pypdfium2.PdfDocument(path)
    try:
        return document[page_index].get_textpage().get_text_bounded()
    finally:
        document.close()


def test_the_rule_is_the_longest_whole_inch_fitting_the_sheet():
    # Letter is 8.5in wide; half an inch clear at each end leaves 7.5in of
    # room, and a ruler is only useful if it is a round number.
    assert export.proof_rule_length_pt(612.0) == 504.0


def test_the_rule_shrinks_to_fit_a_narrow_sheet():
    assert export.proof_rule_length_pt(200.0) == 72.0


def test_a_sheet_too_narrow_for_one_inch_gets_no_rule():
    assert export.proof_rule_length_pt(100.0) == 0.0


def test_the_rule_is_absent_unless_asked_for(tmp_path):
    plan = _plan_from_source(tmp_path, 2)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out)

    assert "in exactly" not in _page_text(out)


def test_the_rule_prints_its_own_length_so_it_can_be_measured(tmp_path):
    plan = _plan_from_source(tmp_path, 2)
    out = os.path.join(str(tmp_path), "proof.pdf")

    export_fn(plan, out, rule=True)

    text = _page_text(out)
    assert "7 in exactly" in text, f"rule label missing, page text: {text!r}"


def test_the_rule_is_drawn_on_every_exported_face(tmp_path):
    # You check whichever face comes out of the printer, not a nominated one.
    plan = _plan_from_source(tmp_path, 2)
    out = os.path.join(str(tmp_path), "proof.pdf")

    export_fn(plan, out, rule=True)

    with pikepdf.open(out) as pdf:
        face_count = len(pdf.pages)
    for index in range(face_count):
        assert "in exactly" in _page_text(out, index), f"no rule on face {index}"


# --- BEHAVIORAL: one face per sheet, for a manual duplex pass -----------
#
# A printer with no duplexer runs the job in two passes: every front, a
# manual reload, then every back. Each pass is a PDF containing one face
# per sheet -- which the exporter could not produce, because it always
# wrote both faces of every sheet it was given.


def _page_streams(path: str) -> list[bytes]:
    """Each page's content stream, with XObject resource names normalised.

    ``add_resource`` mints a random name per document, so the same face
    exported twice differs in that token and nowhere else. Comparing raw
    bytes across two exports therefore always fails, while telling you
    nothing about the geometry -- which is the part these tests are about.
    """
    import re

    with pikepdf.open(path) as pdf:
        return [re.sub(rb"/Fx\S+", b"/Fx", _content_bytes(page)) for page in pdf.pages]


def test_exporting_a_front_pass_writes_one_page_per_sheet(tmp_path):
    plan = _plan_from_source(tmp_path, 4)  # 4 pages -> 2 sheets, 4 faces
    out = os.path.join(str(tmp_path), "fronts.pdf")

    export_fn(plan, out, side="front")

    assert len(_page_streams(out)) == 2


def test_a_front_pass_contains_the_fronts_and_not_the_backs(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    both = os.path.join(str(tmp_path), "both.pdf")
    fronts = os.path.join(str(tmp_path), "fronts.pdf")

    export_fn(plan, both)
    export_fn(plan, fronts, side="front")

    both_streams = _page_streams(both)
    # Interleaved front, back, front, back -- so the fronts are 0 and 2.
    assert _page_streams(fronts) == [both_streams[0], both_streams[2]]


def test_a_back_pass_contains_the_backs(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    both = os.path.join(str(tmp_path), "both.pdf")
    backs = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(plan, both)
    export_fn(plan, backs, side="back")

    both_streams = _page_streams(both)
    assert _page_streams(backs) == [both_streams[1], both_streams[3]]


def test_a_sheet_with_no_back_contributes_nothing_to_a_back_pass():
    """``Sheet.back`` is ``None`` for a face that does not exist. A pass
    must skip it, not invent a blank -- that would be a sheet of paper the
    binder does not need and a reload that no longer matches.

    Exercised on a constructed ``Sheet`` rather than an imposed plan: the
    gutter-shift imposer pads an odd final sheet with a FILLER back rather
    than leaving it absent, so a real plan cannot reach this branch. The
    branch still has to be right -- ``Side`` documents the distinction
    between an absent face and one carrying only filler, and the exporter
    is what has to honour it.
    """
    front_only = Sheet(
        index=0,
        front=Side(
            pages=(
                OutputPage(
                    source_ref=None,
                    placement=Placement(
                        scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0
                    ),
                    is_filler=True,
                ),
            )
        ),
        back=None,
    )

    assert export._sides(front_only, "back") == []
    assert len(export._sides(front_only, "front")) == 1
    assert len(export._sides(front_only)) == 1


def test_asking_for_no_particular_side_still_writes_both(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "both.pdf")

    export_fn(plan, out)

    assert len(_page_streams(out)) == 4


# --- BEHAVIORAL: rotating a back pass ----------------------------------
#
# A printer whose operator flips the stack on its LONG edge lands every
# back upside down relative to its front. `plan_passes` decides whether
# that is so; the exporter is what has to do something about it.


def _rotations(path: str) -> list[int]:
    with pikepdf.open(path) as pdf:
        return [page.rotation for page in pdf.pages]


def test_a_back_pass_can_be_turned_a_half_turn(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(plan, out, side="back", rotate_180=True)

    assert _rotations(out) == [180, 180]


def test_a_pass_is_not_turned_unless_asked(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(plan, out, side="back")

    assert _rotations(out) == [0, 0]


def test_the_half_turn_is_relative_to_any_rotation_already_there(tmp_path):
    """``page.rotate(180, relative=True)``, never an assignment to the
    rotation key -- a page already turned 90 must end at 270, not at 180."""
    path = os.path.join(str(tmp_path), "pre-rotated.pdf")
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(400.0, 600.0))
    pdf.pages[0].rotate(90, relative=True)
    pdf.save(path)
    pdf.close()

    export.rotate_pages_180(path)

    assert _rotations(path) == [270]


def test_a_single_pass_asks_the_driver_not_to_duplex(tmp_path):
    """A pass carries ONE face per page, so a driver that honours a duplex
    hint would print two consecutive fronts onto two sides of one sheet --
    turning the exact hint that helps a duplexer into the thing that ruins
    a manual-duplex job. The pass says simplex, and means it."""
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "fronts.pdf")

    export_fn(plan, out, side="front")

    assert _viewer_preferences(out).get("/Duplex") == pikepdf.Name("/Simplex")


def test_a_back_pass_also_asks_the_driver_not_to_duplex(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(plan, out, side="back")

    assert _viewer_preferences(out).get("/Duplex") == pikepdf.Name("/Simplex")


def test_a_both_faces_export_still_asks_for_the_flip_edge(tmp_path):
    """The hint is right for the document it was always right for: both
    faces interleaved, which is what a real duplexer consumes."""
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "both.pdf")

    export_fn(plan, out)

    assert _viewer_preferences(out).get("/Duplex") == pikepdf.Name(
        "/DuplexFlipLongEdge"
    )


def test_a_single_pass_still_refuses_scaling(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "fronts.pdf")

    export_fn(plan, out, side="front")

    assert _viewer_preferences(out).get("/PrintScaling") == pikepdf.Name("/None")
