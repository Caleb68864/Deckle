"""The signature schedule: a work order for the bench, not a data dump.

Once a document is imposed into folded, nested signatures, the file tells you
nothing about what to *do* with the paper. Which sheets gather together?
Which way round does each go? Where do the blanks fall, and will the reader
notice? Where do the sewing holes go? A binder standing at a bench with a
stack of freshly printed sheets needs those answers in the order the work
happens, and needs them on paper, because hands covered in PVA do not scroll.

So this module derives a schedule from a ``SheetPlan`` and formats it for
printing. It is pure: no I/O, no Qt, no rendering. It reads what the imposer
already decided and says it in the vocabulary of the bench -- gathering,
nesting, folding, sewing -- rather than the vocabulary of the model.

**It describes; it never re-derives.** Every number here comes from the plan
the exporter used. A schedule that computed its own sheet order would be a
second implementation of the imposition, free to disagree with the PDF in the
user's hand -- and the paper would be wrong while both halves looked right.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deckle.core.marks import SEWING_MARGIN_PT
from deckle.core.paper import creep_is_worth_reporting, creep_pt
from deckle.core.models import LayoutSettings, SheetPlan
from deckle.core.printing import duplex_flip_edge
from deckle.core.signatures import fold_reading_order


@dataclass(frozen=True)
class SheetInstruction:
    """One physical sheet's place in a signature.

    :ivar sheet_index: the sheet's index in the plan, which is also the
        order it comes off the printer.
    :ivar position: 1 for the outermost sheet of its signature, counting
        inward. This is gathering order: the binder stacks position 1 first
        and every later sheet on top of it.
    :ivar front_pages: reader-facing page numbers on the front, left to
        right. ``None`` marks a blank.
    :ivar back_pages: the same for the back.
    """

    sheet_index: int
    position: int
    front_pages: tuple[int | None, ...]
    back_pages: tuple[int | None, ...]

    @property
    def is_outermost(self) -> bool:
        """Whether this sheet wraps all the others in its signature."""
        return self.position == 1


@dataclass(frozen=True)
class SignatureInstruction:
    """One signature: what to gather, and what it will contain.

    :ivar index: the signature's number, counting from 1 for the bench.
        (``Signature.index`` counts from 0; a schedule is read by a person.)
    :ivar sheets: its sheets, outermost first -- gathering order.
    :ivar blank_count: how many blank pages padding forced into it.
    """

    index: int
    sheets: tuple[SheetInstruction, ...]
    blank_count: int = 0

    @property
    def sheet_count(self) -> int:
        """How many pieces of paper this signature uses."""
        return len(self.sheets)

    @property
    def page_count(self) -> int:
        """How many book pages it yields once folded -- four per sheet."""
        return self.sheet_count * 4

    @property
    def first_page(self) -> int | None:
        """The lowest real page number in the signature, or ``None``."""
        pages = [p for s in self.sheets for p in s.front_pages + s.back_pages if p is not None]
        return min(pages) if pages else None

    @property
    def last_page(self) -> int | None:
        """The highest real page number in the signature, or ``None``."""
        pages = [p for s in self.sheets for p in s.front_pages + s.back_pages if p is not None]
        return max(pages) if pages else None


@dataclass(frozen=True)
class Schedule:
    """A complete work order for binding one document.

    :ivar signatures: in the order they are gathered and sewn.
    :ivar sheets_total: pieces of paper the whole job needs.
    :ivar blank_total: blank pages padding forced across the document.
    :ivar sewing_stations: how many holes per signature, 0 for none.
    :ivar sewing_margin_pt: how far the first and last holes sit from the
        head and tail. Only meaningful for evenly spaced stations; stated
        positions do not consult it.
    :ivar sewing_station_positions_pt: where the holes go, in points from
        the tail, or ``None`` when they are evenly spaced.
    :ivar paper_thickness_pt: the stock thickness used for the creep
        estimate, or 0 if unset.
    :ivar fold_scheme: the imposition this schedule describes.
    :ivar spine_width_pt: likely sewn-block thickness at the spine as a
        ``(low, high)`` range in points, or ``None`` when paper thickness is
        unset. What you cut boards against.
    :ivar duplex_flip_edge: ``"long"`` or ``"short"`` -- which edge the
        sheet must be turned about for the backs to land upright. Carried
        here rather than worked out by the formatter so it is the same
        answer the exporter wrote into the PDF's ``/Duplex`` entry; see
        this module's "describes, never re-derives" rule.
    :ivar notes: advisories worth reading before cutting paper.
    """

    signatures: tuple[SignatureInstruction, ...]
    sheets_total: int
    blank_total: int
    sewing_stations: int
    sewing_margin_pt: float
    paper_thickness_pt: float
    fold_scheme: str
    sewing_station_positions_pt: tuple[float, ...] | None = None
    spine_width_pt: tuple[float, float] | None = None
    duplex_flip_edge: str = "long"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def signature_count(self) -> int:
        """How many gatherings the job breaks into."""
        return len(self.signatures)


def _page_numbers(
    side, reading_order: list[int], first_slot: int
) -> tuple[int | None, ...]:
    """Reader-facing page numbers for one side, left to right.

    ``None`` for a blank. A **reader-facing page number** is the leaf's
    1-based position in the finished book's reading order -- the order a
    person turns the pages -- counting every imposed slot, blanks
    included. A blank leaf still prints as ``blank`` rather than as its
    number, because there is nothing on it to check; it consumes its
    position all the same, so the page after it is ``n + 2``, which is
    what the reader will call it.

    This used to be ``source_ref.page_index + 1``, the page's offset
    **inside the file it came from**. The two agree only for a document
    that is exactly one source, imported whole, with nothing skipped and
    nothing inserted. A four-page book made of two 2-page PDFs was
    reported as "front: 2 1 / back: 2 1" -- pages 1 and 2 twice, pages 3
    and 4 nowhere.

    :param side: the ``Side`` to number, or ``None`` for a face that does
        not exist.
    :param reading_order: :func:`~deckle.core.signatures.fold_reading_order`
        over the whole plan.
    :param first_slot: this side's first leaf's index into
        ``reading_order``.
    :returns: one entry per leaf, left to right.
    """
    if side is None:
        return ()
    return tuple(
        None if page.source_ref is None else reading_order[first_slot + i] + 1
        for i, page in enumerate(side.pages)
    )


#: Sewing thread accumulates in every fold, so a sewn block is thicker at the
#: spine than at the fore-edge. This is "swell". The fraction is a working
#: rule of thumb, not a measurement -- thread weight, sewing style and how
#: hard the block is pressed all move it, which is why the schedule reports a
#: RANGE and names the assumption rather than printing a single number.
SWELL_FRACTION_LOW = 0.10
SWELL_FRACTION_HIGH = 0.25


def block_width_pt(sheet_count: int, thickness_pt: float) -> float | None:
    """The thickness of an unsewn stack of ``sheet_count`` sheets.

    :param sheet_count: pieces of paper in the whole job.
    :param thickness_pt: caliper of one sheet, in points.
    :returns: the thickness in points, or ``None`` if thickness or sheet
        count is unset.

    A single number rather than the range :func:`spine_width_pt` gives,
    because that range is **swell** -- thread accumulating in a fold -- and
    a flat-sheet job has neither folds nor thread. Reporting a sewn range
    over a glued or side-sewn block would overstate it by up to a quarter,
    which is a recut set of boards.

    Still an estimate: paper caliper varies a few percent with humidity,
    and a perfect binder's glue adds a little. Measure the real block
    before covering, which is what the schedule says.
    """
    if thickness_pt <= 0 or sheet_count <= 0:
        return None
    return sheet_count * thickness_pt


def spine_width_pt(sheet_count: int, thickness_pt: float) -> tuple[float, float] | None:
    """The likely thickness of the sewn block at the spine, as a range.

    :param sheet_count: pieces of paper in the whole book.
    :param thickness_pt: caliper of one sheet, in points.
    :returns: ``(low, high)`` in points, or ``None`` if thickness is unset.

    You cut the boards and the spine piece *before* the block is finished,
    so this is the number you need early and cannot measure yet. Getting it
    wrong means recutting boards.

    Reported as a range on purpose. The block is folded, so its thickness is
    ``sheets x caliper``; sewing then adds swell at the spine that the
    fore-edge does not have. Paper caliper itself varies a few percent with
    humidity. A single confident number here would be false precision about
    something the binder will measure again before gluing.
    """
    if thickness_pt <= 0 or sheet_count <= 0:
        return None
    block = sheet_count * thickness_pt
    return (block * (1.0 + SWELL_FRACTION_LOW), block * (1.0 + SWELL_FRACTION_HIGH))


def _creep_note(
    signature_sheets: int, thickness_pt: float, trim_pt: float = 0.0
) -> str | None:
    """An advisory about fore-edge creep, or ``None`` if it will not matter.

    Nested sheets push each other outward at the fore-edge: the innermost
    leaf protrudes by roughly (sheets - 1) x thickness. Under about a point
    it is invisible; past that the fore-edge wants trimming, and a binder
    would rather know before folding than after.
    """
    if not creep_is_worth_reporting(signature_sheets, thickness_pt, trim_pt):
        return None
    creep = creep_pt(signature_sheets, thickness_pt)
    return (
        f"Fore-edge creep is about {creep:.1f}pt "
        f"({creep / 72:.2f}in) on the innermost leaf. Trim the fore-edge "
        "after sewing, or reduce sheets per signature."
    )


def build_schedule(plan: SheetPlan, settings: LayoutSettings) -> Schedule:
    """Derive a binding schedule from an imposed plan.

    :param plan: the plan the exporter used. Read, never recomputed.
    :param settings: the layout it was imposed with.
    :returns: a :class:`Schedule` describing the work.

    Under ``fold_scheme="none"`` a plan has no signatures at all, and the
    result is an empty schedule rather than an invented one. One page per
    side is not a folded book, and pretending otherwise would produce
    confident instructions for work nobody is doing.
    """
    by_index = {sheet.index: sheet for sheet in plan.sheets}
    signatures: list[SignatureInstruction] = []

    # The reader's own numbering, taken from the fold simulator rather than
    # from any file offset. `fold_reading_order` walks `plan.signatures` in
    # the same order this loop does and emits four slots per sheet --
    # front's two leaves, then back's two -- so a single cursor keeps the
    # two in step.
    reading_order = fold_reading_order(plan)
    slot_total = sum(
        len(side.pages)
        for sheet in plan.sheets
        for side in (sheet.front, sheet.back)
        if side is not None
    )
    if plan.signatures and len(reading_order) != slot_total:
        raise ValueError(
            f"this plan has {slot_total} print slot(s) but its signatures "
            f"describe {len(reading_order)}; a schedule cannot number "
            "pages it cannot place in reading order"
        )
    slot = 0

    for signature in plan.signatures:
        sheets: list[SheetInstruction] = []
        # `sheet_indices` is already outermost-first: that is the order the
        # imposer nested them, and the order they must be gathered.
        for position, sheet_index in enumerate(signature.sheet_indices, start=1):
            sheet = by_index.get(sheet_index)
            if sheet is None:
                # A signature naming a sheet the plan does not carry.
                # Skipped as before -- but the cursor still advances,
                # because `fold_reading_order` counted four slots for it
                # and every later sheet's numbers would otherwise slide.
                slot += 4
                continue
            front_pages = _page_numbers(sheet.front, reading_order, slot)
            back_pages = _page_numbers(sheet.back, reading_order, slot + 2)
            slot += 4
            sheets.append(
                SheetInstruction(
                    sheet_index=sheet_index,
                    position=position,
                    front_pages=front_pages,
                    back_pages=back_pages,
                )
            )
        signatures.append(
            SignatureInstruction(
                index=signature.index + 1,
                sheets=tuple(sheets),
                blank_count=signature.blank_count,
            )
        )

    notes: list[str] = []
    # Outside the `if signatures:` guard: a flat-sheet job needs the block
    # thickness to cut boards against just as much as a sewn one needs a
    # spine width, and this note used to be hidden behind a guard that
    # every flat-sheet schedule failed.
    if settings.paper_thickness_pt <= 0:
        notes.append(
            "Paper thickness is not set, so the block thickness is not "
            "estimated. Measure your stock and set it if you are cutting "
            "boards."
        )
    if signatures:
        widest = max(sig.sheet_count for sig in signatures)
        # Creep stays inside the guard: it is a folding phenomenon, and a
        # flat sheet has none.
        creep = _creep_note(widest, settings.paper_thickness_pt, settings.trim_pt)
        if creep is not None:
            notes.append(creep)
        uneven = len(signatures) > 1 and len({s.sheet_count for s in signatures}) > 1
        if uneven and settings.signature_lengths:
            # The binder stated these lengths. Explaining their own choice
            # back to them as an accident of arithmetic would be a
            # confident falsehood, which is worse than saying nothing.
            notes.append(
                "The signatures are different sizes because you asked for "
                f"{', '.join(str(n) for n in settings.signature_lengths)}."
            )
        elif uneven:
            notes.append(
                "The last signature is shorter than the others. That is "
                "normal -- the page count did not divide evenly."
            )

    return Schedule(
        signatures=tuple(signatures),
        sheets_total=len(plan.sheets),
        blank_total=sum(sig.blank_count for sig in plan.signatures),
        sewing_stations=settings.sewing_stations,
        sewing_margin_pt=SEWING_MARGIN_PT,
        paper_thickness_pt=settings.paper_thickness_pt,
        fold_scheme=settings.fold_scheme,
        sewing_station_positions_pt=settings.sewing_station_positions_pt,
        spine_width_pt=spine_width_pt(len(plan.sheets), settings.paper_thickness_pt),
        duplex_flip_edge=duplex_flip_edge(plan.paper_pt),
        notes=tuple(notes),
    )


def _format_pages(pages: tuple[int | None, ...]) -> str:
    """Page numbers as a binder reads them, with blanks named."""
    if not pages:
        return "(nothing)"
    return "  ".join("blank" if p is None else str(p) for p in pages)


def _printer_lines(schedule: Schedule) -> list[str]:
    """The settings to get right before any paper is committed.

    Two of them, and both ruin the job in a way that is not visible until
    it is too late to matter.

    Scaling is the worse one. Every PDF viewer defaults to "fit to page",
    which shrinks the sheet a few percent to clear the printer's
    non-printable border -- and moves the gutter, the margins and the
    sewing stations off every number printed above, while the sheet itself
    still looks entirely correct. The exporter asks for actual size in the
    PDF, but that is a hint a driver preset can override, so it is said
    here too, where the user is looking when they press print.

    The flip edge is the other. It is named one way only: telling a user
    both edges and expecting them to pick is how the wrong one gets picked.
    """
    edge = "LONG" if schedule.duplex_flip_edge == "long" else "SHORT"
    shape = "portrait" if schedule.duplex_flip_edge == "long" else "landscape"
    lines = ["AT THE PRINTER", "-" * len("AT THE PRINTER")]
    lines.append("  Print at ACTUAL SIZE.")
    lines.append('    Turn off "fit to page", "shrink oversized pages", and')
    lines.append("    every other scaling option in the print dialog.")
    lines.append("    Scaling moves the gutter, the margins and every")
    lines.append("    printed mark off the geometry Deckle computed, and the")
    lines.append("    sheet still looks right -- which is why this is the")
    lines.append("    setting worth checking twice. Deckle asks for actual")
    lines.append("    size in the PDF itself, but a driver preset can")
    lines.append("    override it.")
    lines.append("")
    lines.append(f"  Duplex: turn the sheet about its {edge} edge.")
    lines.append(f"    The sheet is {shape} and the spine runs head to tail,")
    lines.append(f"    so its {edge.lower()} edge is the vertical one. Turning it")
    lines.append("    about the other edge lands every back upside down.")
    lines.append("")
    # `deckle-cli`, not `deckle`: the second is the desktop app's console
    # script, which hands its argv to QApplication and silently ignores
    # anything it does not recognise -- so this line, printed on every
    # binding schedule a binder takes to the printer, told them to open a
    # window that prints nothing and never returns.
    lines.append("  Print sheet 0 on its own first -- deckle-cli export --sheets 0.")
    lines.append("    Check the back is upright and the spine margin falls on")
    lines.append("    the bound edge before committing the rest of the stack.")
    lines.append("    Add --rule to print a measurable ruler on it: if the")
    lines.append("    ruler is short, the printer scaled the page.")
    lines.append("")
    return lines


def _binding_the_stack_lines(schedule: Schedule) -> list[str]:
    """What to do with a flat stack once it is printed.

    The counterpart to the folio path's AFTER SEWING block, and the reason
    a flat-sheet schedule is worth reaching at all. A flat-sheet job is
    bound by gluing, punching or side-sewing, and the one number needed
    before any of those -- and not measurable until it is too late to
    matter -- is how thick the block will be. It is what boards and a spine
    piece are cut against.

    Deliberately does **not** reuse :func:`spine_width_pt`. That range is
    swell from sewing thread; there is no thread here, and a 10-25% range
    over a glued block is a recut set of boards.

    :param schedule: the schedule being rendered.
    :returns: the block's lines, ending in a blank one.
    """
    block = block_width_pt(schedule.sheets_total, schedule.paper_thickness_pt)
    lines = ["BINDING THE STACK", "-" * len("BINDING THE STACK")]
    lines.append("  Collate the sheets in the order they came off the")
    lines.append("  printer. The gutter alternates side by side, so the")
    lines.append("  spine margins line up once the stack is in order.")
    lines.append("")
    if block is None:
        lines.append(
            "  Block thickness is not estimated -- set paper thickness to"
        )
        lines.append("  get a figure to cut boards against.")
    else:
        lines.append(
            f"  Block thickness: about {block / 72:.2f}in ({block:.0f}pt)."
        )
        lines.append(
            f"    {schedule.sheets_total} sheets at "
            f"{schedule.paper_thickness_pt:.3f}pt. No swell is added:"
        )
        lines.append(
            "    swell is thread accumulating in a fold, and there are"
        )
        lines.append("    no folds here.")
        lines.append(
            "    Cut boards and spine against this, then measure the real"
        )
        lines.append("    block before covering.")
    lines.append("")
    return lines


def format_schedule_text(schedule: Schedule, title: str | None = None) -> str:
    """Render a schedule as plain text, for printing or piping.

    :param schedule: the schedule to render.
    :param title: the document's name, if known.
    :returns: the full schedule as text, ending in a newline.

    Written to be read at a bench: steps in the order the work happens,
    every signature on its own, and no jargon that is not either standard
    bookbinding vocabulary or defined on the spot.
    """
    lines: list[str] = []
    heading = "BINDING SCHEDULE"
    if title:
        heading += f" -- {title}"
    lines.append(heading)
    lines.append("=" * len(heading))
    lines.append("")

    if schedule.fold_scheme != "folio" or not schedule.signatures:
        lines.append(
            "This document is imposed one page per side, not folded into "
            "signatures, so there is nothing to gather or sew. The binding "
            "is the gutter: every sheet carries its spine margin on the "
            "edge that will be bound, alternating side so the margins line "
            "up once the stack is collated in order."
        )
        lines.append("")
        lines.append(f"Sheets to print: {schedule.sheets_total}")
        lines.append("")
        # A job with no folding is still a job, and the two settings that
        # ruin it are the same ones folio has to get right.
        lines.extend(_printer_lines(schedule))
        lines.extend(_binding_the_stack_lines(schedule))
        if schedule.notes:
            for note in schedule.notes:
                lines.append(f"  Note: {note}")
            lines.append("")
        return "\n".join(lines) + "\n"

    lines.append(
        f"{schedule.signature_count} signature(s) - "
        f"{schedule.sheets_total} sheet(s) of paper - "
        f"{schedule.blank_total} blank page(s)"
    )
    lines.append("")
    lines.append("Print all fronts, reload the stack, then print all backs.")
    lines.append("Keep the sheets in the order they emerge.")
    lines.append("")
    lines.extend(_printer_lines(schedule))

    for signature in schedule.signatures:
        span = ""
        if signature.first_page is not None:
            span = f"  (pages {signature.first_page}-{signature.last_page})"
        header = f"SIGNATURE {signature.index}{span}"
        lines.append(header)
        lines.append("-" * len(header))
        lines.append(
            f"  {signature.sheet_count} sheet(s), {signature.page_count} pages"
            + (f", {signature.blank_count} blank" if signature.blank_count else "")
        )
        lines.append("")
        lines.append("  Gather in this order -- first listed is the OUTSIDE of the fold:")
        for sheet in signature.sheets:
            marker = "outermost" if sheet.is_outermost else f"position {sheet.position}"
            lines.append(f"    sheet {sheet.sheet_index}  ({marker})")
            lines.append(f"        front:  {_format_pages(sheet.front_pages)}")
            lines.append(f"        back:   {_format_pages(sheet.back_pages)}")
        lines.append("")
        lines.append("  Fold the gathered stack in half along the printed fold line.")
        positions = schedule.sewing_station_positions_pt
        if positions:
            lines.append(
                f"  Pierce {len(positions)} sewing station(s) on the fold, "
                "at the printed marks."
            )
            # In capitals because head-versus-tail is the one thing a
            # binder can get backwards here, and an asymmetric pattern
            # pierced upside down is a ruined signature.
            lines.append("  Measured up from the TAIL:")
            for index, y in enumerate(positions, start=1):
                lines.append(f"    {index}.  {y:.1f}pt  ({y / 72:.2f}in)")
        elif schedule.sewing_stations > 0:
            lines.append(
                f"  Pierce {schedule.sewing_stations} sewing station(s) on the fold, "
                f"at the printed marks."
            )
            lines.append(
                f"  The first and last sit {schedule.sewing_margin_pt:.0f}pt "
                f"({schedule.sewing_margin_pt / 72:.2f}in) from head and tail."
            )
        else:
            lines.append("  No sewing stations were marked.")
        lines.append("")

    lines.append("AFTER SEWING")
    lines.append("-" * len("AFTER SEWING"))
    lines.append("  Stack the signatures in order, 1 first.")
    if schedule.spine_width_pt is not None:
        low, high = schedule.spine_width_pt
        lines.append("")
        lines.append(
            f"  Spine thickness: about {low / 72:.2f}-{high / 72:.2f}in "
            f"({low:.0f}-{high:.0f}pt)."
        )
        lines.append(
            f"    {schedule.sheets_total} sheets at "
            f"{schedule.paper_thickness_pt:.3f}pt, plus "
            f"{int(SWELL_FRACTION_LOW * 100)}-{int(SWELL_FRACTION_HIGH * 100)}% "
            "swell from the sewing thread."
        )
        lines.append(
            "    Cut boards and spine against this, then measure the real "
            "block before covering."
        )
    else:
        lines.append("")
        lines.append(
            "  Spine thickness is not estimated -- set paper thickness to "
            "get a figure to cut boards against."
        )
    if schedule.notes:
        lines.append("")
        for note in schedule.notes:
            lines.append(f"  Note: {note}")
    lines.append("")
    return "\n".join(lines) + "\n"
