"""Tests for deckle.core.export: PDF composition via pikepdf Form XObjects."""

from __future__ import annotations

import os

import pikepdf
import pytest

from deckle.core import export, render
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

    # The old version of this compared two `impose()` calls to each other and
    # then checked the output file existed. That proves `impose` is
    # deterministic -- a pure function against itself -- and says nothing
    # about `export`, which cannot mutate a frozen `Placement` in any case.
    # Stubbing `_place_output_page` to emit no content at all left it green.
    #
    # So: hold the plan to being unchanged *by the export*, and hold the
    # export to actually placing something.
    before = [
        [p.placement for p in side.pages]
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
    ]

    out_path = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out_path)

    after = [
        [p.placement for p in side.pages]
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
    ]
    assert after == before, "export changed the placements it was handed"

    # Every face has to carry a form XObject and a content stream that draws
    # it. This is the half that was missing: a `_place_output_page` that
    # emitted nothing produced a valid, empty PDF and a passing test.
    import pikepdf

    with pikepdf.open(out_path) as pdf:
        assert len(pdf.pages) == sum(
            1
            for sheet in plan.sheets
            for side in (sheet.front, sheet.back)
            if side is not None
        )
        for index, page in enumerate(pdf.pages):
            resources = page.get("/Resources", {})
            xobjects = resources.get("/XObject", {}) if resources else {}
            assert len(xobjects) >= 1, f"face {index} places no form"
            # `/Contents` is a stream or an array of them, and the array
            # form is as valid as the single. Joined rather than coalesced
            # because coalescing rewrites the page being inspected.
            contents = page.get("/Contents")
            parts = contents if isinstance(contents, pikepdf.Array) else [contents]
            stream = b"".join(part.read_bytes() for part in parts)
            assert b"Do" in stream, f"face {index} draws nothing"


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


def _sheet_without_a_back(index: int = 0) -> Sheet:
    return Sheet(
        index=index,
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


def test_a_both_faces_export_omits_a_face_that_does_not_exist(tmp_path):
    """Nothing to print, so no page. This is the interleaved document a
    real duplexer consumes, and a blank there is a wasted side."""
    plan = SheetPlan(
        sheets=[_sheet_without_a_back(0)], paper_pt=LETTER, warnings=[]
    )
    out = os.path.join(str(tmp_path), "both.pdf")

    export_fn(plan, out)

    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1, "a blank was invented for an absent face"


def test_a_pass_keeps_one_page_per_sheet_even_where_a_face_is_absent(tmp_path):
    """A pass PDF's pages map one-to-one onto the sheets being fed. Drop a
    page for a sheet that lacks that face and every later back lands on the
    wrong front -- the whole stack ruined, discovered after the paper is
    spent.

    So a pass pads where a both-faces export omits. The two are different
    documents answering different questions: "what is there to print" for
    the duplexer, "what goes through the printer on this pass" for a
    manual reload.
    """
    plan = SheetPlan(
        sheets=[_sheet_without_a_back(0), _sheet_without_a_back(1)],
        paper_pt=LETTER,
        warnings=[],
    )
    out = os.path.join(str(tmp_path), "backs.pdf")

    export_fn(plan, out, side="back")

    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2, "a pass must not shift its own registration"


# --- BEHAVIORAL: one answer to "which page is this face" ---------------
#
# `export` writes one page per face that EXISTS, front first, so a sheet
# with a back and no front puts that back at page 0 and not page 1.
# `render.render_sheet` used to work that out a second time from its own
# `has_front`/`has_back` pair, under a comment naming this module. Two
# implementations of a fold/face order can share a bug and agree -- unlike
# `saddle_order` and the fold simulator in `tests/test_layout_saddle.py`,
# which are two DELIBERATELY independent derivations checked against each
# other, this pair was never checked against anything. It is now one
# function, and the tests below are what would have caught them drifting.


def _sheet_without_a_front(index: int = 0) -> Sheet:
    return Sheet(
        index=index,
        front=None,
        back=Side(
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
    )


@pytest.mark.parametrize(
    "sheet_factory",
    [_sheet_without_a_back, _sheet_without_a_front],
    ids=["front-only", "back-only"],
)
def test_face_page_index_matches_what_export_writes(tmp_path, sheet_factory):
    """The helper's answer is checked against the artifact, not asserted.

    A both-faces export is opened and every face that exists is looked up
    through ``face_page_index``; the page count is the number of faces
    that exist. The back-only sheet is the shape where a positional guess
    and the real page numbering disagree, and it is the one no plan
    Deckle currently produces -- ``_pad_to_even`` sees to it -- so the
    ``Sheet`` is built by hand.
    """
    sheet = sheet_factory(0)
    plan = SheetPlan(sheets=[sheet], paper_pt=LETTER, warnings=[])
    out = os.path.join(str(tmp_path), "both.pdf")

    export_fn(plan, out)

    faces = export._sides(sheet, None)
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == len(faces)
        for side in ("front", "back"):
            face = getattr(sheet, side)
            if face is None:
                continue
            index = export.face_page_index(sheet, side)
            assert index is not None
            assert index < len(pdf.pages)
            assert faces[index] is face


def test_face_page_index_is_none_for_a_face_that_does_not_exist():
    """``None`` means "nothing to read", not "page 0"."""
    assert export.face_page_index(_sheet_without_a_back(0), "back") is None
    assert export.face_page_index(_sheet_without_a_front(0), "front") is None


def test_face_page_index_does_not_confuse_two_identical_faces():
    """``Side`` is a frozen dataclass, so a sheet blank on both sides has
    two faces that compare equal. A lookup by value -- ``_sides(sheet,
    None).index(face)`` -- would find the front and report page 0 for the
    back, on the one sheet shape where nobody would notice."""
    blank = Side(
        pages=(
            OutputPage(
                source_ref=None,
                placement=Placement(
                    scale_x=1.0, scale_y=1.0, tx=0.0, ty=0.0, rotate_deg=0
                ),
                is_filler=True,
            ),
        )
    )
    sheet = Sheet(index=0, front=blank, back=blank)

    assert sheet.front == sheet.back
    assert export.face_page_index(sheet, "front") == 0
    assert export.face_page_index(sheet, "back") == 1


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


# --- BEHAVIORAL: verifying what was actually written -------------------
#
# Every check in this module runs on the PLAN. Nothing had ever looked at
# the file, so a composition that silently dropped or mis-sized a page
# would be reported as a successful export -- and the first symptom would
# be paper. The two properties worth confirming are cheap and total: one
# page per face written, every page the size the plan was laid out for.


def test_verify_output_accepts_a_correct_export(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out)

    export._verify_output(out, expected_pages=4, paper_pt=LETTER)


def test_verify_output_rejects_a_missing_page(tmp_path):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out)

    with pytest.raises(export.ExportVerificationError) as excinfo:
        export._verify_output(out, expected_pages=5, paper_pt=LETTER)

    assert "5" in str(excinfo.value) and "4" in str(excinfo.value)


def test_verify_output_rejects_a_page_of_the_wrong_size(tmp_path):
    plan = _plan_from_source(tmp_path, 2)
    out = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out)

    with pytest.raises(export.ExportVerificationError) as excinfo:
        export._verify_output(out, expected_pages=2, paper_pt=(595.28, 841.89))

    assert "612" in str(excinfo.value), "the message does not say what it found"


def test_verify_output_tolerates_floating_point_noise(tmp_path):
    """Page sizes round-trip through the PDF as decimal strings, so an
    exact float comparison would reject a correct file."""
    plan = _plan_from_source(tmp_path, 2)
    out = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out)

    export._verify_output(
        out, expected_pages=2, paper_pt=(612.0 + 1e-9, 792.0 - 1e-9)
    )


def _break_composition(export_mod, monkeypatch) -> None:
    """Make composition lose its last page, and nothing else.

    Injected at the composition boundary rather than at ``_sides``:
    ``export`` derives the expected page count from ``_sides`` too, so
    patching that moves the answer and the question together and the
    check can never fail. The fault has to land somewhere the expectation
    does not read.
    """
    real_batched = export_mod._export_batched

    def losing_a_page(plan, selected, path, **kwargs):
        # **kwargs rather than the real parameter list: this stub only
        # needs to pass everything through, and spelling out the signature
        # makes it break every time composition grows an option -- which
        # it has twice already.
        real_batched(plan, selected, path, **kwargs)
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            del pdf.pages[-1]
            pdf.save(path)

    monkeypatch.setattr(export_mod, "_export_batched", losing_a_page)


def test_an_export_that_loses_a_page_fails_instead_of_reporting_success(
    tmp_path, monkeypatch
):
    """The whole point: a wrong file must not be handed over as a right
    one. Verified before the scratch file is renamed into place, so the
    destination is never briefly wrong and a previous good export at that
    path survives."""
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")
    export_fn(plan, out)  # a good file already at the destination

    _break_composition(export, monkeypatch)

    with pytest.raises(export.ExportVerificationError):
        export_fn(plan, out)

    # The good file is still there and still complete.
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 4, "a failed export overwrote a good file"


def test_a_failed_verification_leaves_no_scratch_file_behind(tmp_path, monkeypatch):
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    _break_composition(export, monkeypatch)

    with pytest.raises(export.ExportVerificationError):
        export_fn(plan, out)

    # `src.pdf` is this test's own source. Anything else ending in .pdf is
    # a scratch file the failed export failed to reclaim -- including the
    # destination, which must not exist at all: verification runs before
    # the rename, so nothing should ever have been put there.
    leftovers = [
        n for n in os.listdir(str(tmp_path)) if n.endswith(".pdf") and n != "src.pdf"
    ]
    assert leftovers == [], f"failed export left files behind: {leftovers}"


# -- the half turn -------------------------------------------------------
#
# `Placement.rotate_deg` is documented as 0/90/180/270, and the exporter
# branched on `rotate_deg in (90, 270)`. A half turn therefore fell into the
# translation path and was discarded in silence -- no exception, no warning,
# and ink in exactly the pixels an unrotated placement produces. Latent
# while `_place_page` emitted only 0 and 90; B1 makes it reachable, because
# a page the user turns upside down is the commonest scanner mistake there
# is.


def _write_corner_marked_pdf(tmp_path, n_pages: int,
                             page_size=(400.0, 600.0)) -> str:
    """A source whose ink is a black square in each page's BOTTOM-LEFT.

    Asymmetric on both axes on purpose. A blank page -- which is what
    `_write_source_pdf` makes -- looks identical under every rotation, so a
    test built on one can assert nothing about which way a page turned.
    """
    path = os.path.join(str(tmp_path), "corner.pdf")
    pdf = pikepdf.Pdf.new()
    for _ in range(n_pages):
        page = pdf.add_blank_page(page_size=page_size)
        page.contents_add(b"q\n0 0 0 rg\n0 0 100 100 re\nf\nQ\n")
    pdf.save(path)
    pdf.close()
    return path


def _ink_bbox_px(rendered) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` of non-white pixels, image coords."""
    xs: list[int] = []
    ys: list[int] = []
    for i in range(0, len(rendered.rgba), 4):
        if rendered.rgba[i] < 250:
            pixel = i // 4
            xs.append(pixel % rendered.width)
            ys.append(pixel // rendered.width)
    assert xs, "the rasterised sheet has no ink at all"
    return (min(xs), min(ys), max(xs), max(ys))


def _one_page_plan(ref, rotate_deg: int, tx=100.0, ty=100.0) -> SheetPlan:
    page = OutputPage(
        source_ref=ref,
        placement=Placement(scale_x=1.0, scale_y=1.0, tx=tx, ty=ty,
                            rotate_deg=rotate_deg),
        is_filler=False,
    )
    return SheetPlan(
        sheets=[Sheet(index=0, front=Side(pages=(page,)), back=None)],
        paper_pt=LETTER, warnings=[],
    )


def test_a_half_turn_placement_is_actually_turned(tmp_path):
    """The defect, measured in ink rather than in matrices.

    Scale 1.0, footprint 400x600 at (100, 100), so the source's
    bottom-left square covers sheet points x 100..200, y 100..200. A half
    turn about (300, 400) maps that to x 400..500, y 600..700; at 36 dpi
    on a 612x792 sheet (306x396 px) that is x 200..250 and y 46..96.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)

    try:
        rendered = render.render_sheet(_one_page_plan(ref, 180), 0, "front", 36)
        assert _ink_bbox_px(rendered) == (200, 46, 249, 95)
    finally:
        export.clear_sheet_cache()


def test_an_unrotated_placement_is_the_control_for_the_half_turn(tmp_path):
    """Proves the measurement, so the 180 assertion is not an accident.

    Passes before and after the fix. Its job is to show that
    `_ink_bbox_px` reads what it claims, and to name the exact pixels a
    dropped half turn produced -- because that was the bug: 180 rendered
    byte-for-byte identically to this.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)

    try:
        rendered = render.render_sheet(_one_page_plan(ref, 0), 0, "front", 36)
        assert _ink_bbox_px(rendered) == (50, 296, 99, 345)
    finally:
        export.clear_sheet_cache()


def test_a_half_turn_pivots_on_the_unswapped_footprint_centre(tmp_path):
    """A page turned 180 covers the rectangle it covered upright.

    Only the quarter turns transpose the footprint. This asserts the
    emitted point reflection `-1 0 0 -1 2cx 2cy cm` with cx = 100 + 400/2
    and cy = 100 + 600/2. A half turn that wrongly transposed its
    footprint would pivot on (400, 300) and emit `... 800.0 600.0 cm` --
    still on the sheet, and entirely plausible to the eye.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)
    out = os.path.join(str(tmp_path), "out.pdf")

    try:
        export_fn(_one_page_plan(ref, 180), out)
        with pikepdf.open(out) as pdf:
            stream = _content_bytes(pdf.pages[0])
    finally:
        export.clear_sheet_cache()

    # Parsed rather than string-matched: the point reflection is
    # `-1 0 0 -1 2cx 2cy`, and whether the two zeros print as `0.0` or
    # `-0.0` depends on the sign of the angle handed to math.sin -- which
    # B1 flips when it makes the matrix clockwise. Signed zero is not
    # geometry, and a test that fails on it is a test that will be
    # "fixed" by pasting in whatever the code now emits.
    matrices = [
        line for line in stream.decode("latin-1").splitlines()
        if line.endswith(" cm")
    ]
    assert matrices, stream
    a, b, c, d, e, f = (float(v) for v in matrices[0].split()[:6])
    assert (a, b, c, d) == (-1.0, 0.0, 0.0, -1.0), matrices[0]
    assert (e, f) == (600.0, 800.0), matrices[0]


def test_a_quarter_turn_still_transposes_its_footprint(tmp_path):
    """The half turn's sibling, so the 180 branch cannot be inverted.

    A 400x600 page turned a quarter has a 600x400 footprint, so at the
    sheet origin it covers sheet points x 0..600, y 0..400 -- image pixels
    x 0..300, y 196..396 on a 306x396 raster. Asserted as containment
    rather than as a corner because B1 changes a quarter turn from
    counter-clockwise to clockwise, which moves the corner and leaves the
    footprint alone.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)

    try:
        rendered = render.render_sheet(
            _one_page_plan(ref, 90, tx=0.0, ty=0.0), 0, "front", 36
        )
        left, top, right, bottom = _ink_bbox_px(rendered)
    finally:
        export.clear_sheet_cache()

    assert 0 <= left <= right <= 300, (left, right)
    assert 196 <= top <= bottom <= 396, (top, bottom)


# -- which way a page turns, measured in ink -----------------------------
#
# `Placement.rotate_deg` is degrees CLOCKWISE, matching PDF /Rotate and
# pdfium. `_rotation_matrix` was counter-clockwise, which nothing noticed
# while its only producer was the landscape policy -- both directions look
# equally plausible on a page nobody asked to turn. Composed with a
# rotation the user chose in Arrange, it is the difference between a
# corrected scan and one turned the wrong way twice.


def _corner_of_ink(rendered) -> str:
    """Which quadrant of the image the ink sits in."""
    left, top, right, bottom = _ink_bbox_px(rendered)
    mid_x = (left + right) / 2 / rendered.width
    mid_y = (top + bottom) / 2 / rendered.height
    return ("top" if mid_y < 0.5 else "bottom") + "-" + (
        "left" if mid_x < 0.5 else "right"
    )


@pytest.mark.parametrize(
    "rotate_deg,expected_corner",
    [
        (0, "bottom-left"),
        (90, "top-left"),
        (180, "top-right"),
        (270, "bottom-right"),
    ],
)
def test_the_exported_sheet_turns_clockwise(tmp_path, rotate_deg, expected_corner):
    """A mark in the source's bottom-left walks clockwise round the sheet.

    bottom-left, top-left, top-right, bottom-right is what clockwise means
    for a corner, and it is what PDF /Rotate and pdfium both do. The
    exporter's matrix used to turn the other way, so 90 and 270 were
    swapped relative to the thumbnail of the same page.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)

    try:
        rendered = render.render_sheet(
            _one_page_plan(ref, rotate_deg), 0, "front", 36
        )
        assert _corner_of_ink(rendered) == expected_corner
    finally:
        export.clear_sheet_cache()


def test_the_rotation_matrix_turns_clockwise():
    """The direction, asserted on the matrix rather than on a picture.

    A quarter turn clockwise about the origin sends (1, 0) to (0, -1), so
    the `b` component is -1 and `c` is +1. Counter-clockwise is the
    transpose, and that is what this emitted for the whole life of the
    project.
    """
    a, b, c, d = (float(v) for v in export._rotation_matrix(90, 0.0, 0.0).split()[:4])
    assert (a, b, c, d) == (0.0, -1.0, 1.0, 0.0)


def test_the_sheet_and_the_thumbnail_turn_the_same_way(tmp_path):
    """The two pictures of one page must agree.

    The thumbnail grid is what the user turns the page in; the exported
    sheet is what reaches paper. They were rendered by different code
    turning opposite ways -- and for rotated pages the thumbnail did not
    render at all, so nobody could see the disagreement.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)
    page = SourcePage(ref=ref, rotate_deg=90, skipped=False)

    try:
        thumbnail = render.thumbnails([page], 0, 1, dpi=36)[0]
        sheet = render.render_sheet(_one_page_plan(ref, 90), 0, "front", 36)
    finally:
        export.clear_sheet_cache()

    assert _corner_of_ink(thumbnail) == _corner_of_ink(sheet)


@pytest.mark.parametrize("rotate_deg", [0, 90, 180, 270, -90, 450, 45])
def test_a_rotated_page_still_renders_a_thumbnail(tmp_path, rotate_deg):
    """The grid must survive every rotation, including the nonsensical ones.

    `_rotation_quarter_turns` divided by 90 before handing the value to
    pypdfium2, whose `render(rotation=)` takes DEGREES and looks them up in
    {0: 0, 90: 1, 180: 2, 270: 3}. Every non-zero rotation therefore raised
    `KeyError: 1` out of the thumbnail worker: the one control Arrange
    offers for a sideways scan broke the grid rather than turning the page.
    """
    path = _write_corner_marked_pdf(tmp_path, 1)
    ref = _make_ref(path, 0, 400.0, 600.0)
    page = SourcePage(ref=ref, rotate_deg=rotate_deg, skipped=False)

    rendered = render.thumbnails([page], 0, 1, dpi=36)[0]

    assert rendered.width > 0 and rendered.height > 0


# -- a selection that names a sheet the plan does not have ----------------
#
# `export` filtered unknown indices out with `if i in by_index`, so a
# fully-unknown selection wrote a 0-page PDF -- which then PASSED
# `_verify_output`, because the expected page count came from the same
# filtered list and the check agreed with itself. The mixed case was
# quieter and worse: `--sheets 0,99,1` wrote two sheets under a name the
# user believed held three.


def test_a_selection_naming_a_missing_sheet_is_refused(tmp_path):
    """The whole selection, not the part of it that happens to exist.

    A selection is a statement about which sheets to print. Honouring
    three quarters of it produces a stack that is wrong in a way nobody
    can see until it is collated.
    """
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    with pytest.raises(ValueError) as exc_info:
        export_fn(plan, out, sheets=[0, 99, 1])

    message = str(exc_info.value)
    assert "99" in message
    assert "sheets 0-1" in message, message
    assert not os.path.exists(out), "refused before anything was written"


def test_every_missing_index_is_named_once(tmp_path):
    """Sorted and de-duplicated, so a repeated typo is reported once."""
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    with pytest.raises(ValueError) as exc_info:
        export_fn(plan, out, sheets=[99, 12, 99])

    assert "sheet(s) 12, 99 are not in this plan" in str(exc_info.value)


def test_a_fully_unknown_selection_no_longer_writes_an_empty_pdf(tmp_path):
    """The case `_verify_output` could not catch.

    Expected pages were derived from the filtered list, so zero expected
    against zero written passed -- a check that agreed with itself about
    a file containing nothing.
    """
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    with pytest.raises(ValueError):
        export_fn(plan, out, sheets=[99])

    assert not os.path.exists(out)


def test_a_valid_selection_is_unaffected(tmp_path):
    """The guard that this refuses only what it should.

    Order is preserved too: a selection is in the order given, because
    that is the order the sheets feed.
    """
    plan = _plan_from_source(tmp_path, 4)
    out = os.path.join(str(tmp_path), "out.pdf")

    export_fn(plan, out, sheets=[1, 0])

    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 4  # two sheets, both faces


def test_the_preview_of_a_missing_sheet_shows_nothing_rather_than_raising(tmp_path):
    """The preview asks for whatever the user last looked at.

    A shorter document is an ordinary thing to arrive at, so an absent
    sheet is not an error here -- but the check has to happen BEFORE the
    export, which previously cached an empty PDF under the stale key and
    would now raise into a render worker.
    """
    plan = _plan_from_source(tmp_path, 2)

    try:
        rendered = render.render_sheet(plan, 99, "front", 36)
    finally:
        export.clear_sheet_cache()

    assert rendered.width == 0 and rendered.height == 0
