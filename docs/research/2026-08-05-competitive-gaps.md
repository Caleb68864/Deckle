---
type: research
date: 2026-08-05
topic: "What comparable imposition tools offer that Deckle does not"
author: research agent
status: draft
tags:
  - deckle
  - research
  - competitive-analysis
  - bookbinding
---

# Competitive gaps — Deckle vs. comparable tools

## How this was researched, and what is *not* verified

Deckle's current surface was read from `README.md`, the design document at
`docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md`, and directly
from source (`deckle/core/models.py`, `layout.py`, `marks.py`, `schedule.py`,
`signatures.py`, `cli.py`) so that nothing below is claimed as a gap that
Deckle already has.

Bookbinder JS's feature set was read from its **actual source** — the UI
partials under `src/html/` and `src/constants.js` on `main` — not from a
review or a blog post. Quoted labels are verbatim from those files. That is
the strongest evidence in this document.

Everything else is weaker and is marked where it matters:

- **Scribus.** Verified that its PDF export has a Pre-Press tab covering crop,
  bleed and registration marks. **Not verified:** the exact option list
  (colour bars, page information, offset). The Scribus wiki and the official
  manual both returned 403/404 to automated fetches. I did verify the more
  important fact — that **Scribus has no built-in imposition**, only a
  never-merged 2007 GSoC plugin and an external Laidout round-trip.
- **Create Booklet 2 / BookletMaker / BookletCreator.** Feature lists come
  from vendor marketing pages and App Store copy. Vendor claims, not tested.
  `bookletmaker.app` returned 403 to automated fetch, so its feature list here
  is second-hand from a search summary and should be treated as **unverified**.
- **Quite Imposing Plus.** Features taken from vendor documentation PDFs. Its
  own manual says shingling/bottling is *not* easily supported, which is a
  useful negative result.
- **pdfimpose.** Schema list verified from its docs. **Option semantics
  (`--mark`, `--creep`) not verified** — the CLI reference page 404'd.
- **Bookbinding communities.** I could not retrieve r/bookbinding or
  r/DIY_Bookbinding threads directly; search returned only adjacent Adobe
  forum threads and vendor blogs. The community-demand claims below are
  therefore rated *weak evidence* and flagged individually. Treat "binders
  ask for this" as my inference from the physical task, not as a survey.

---

## Status since this was written

This is a point-in-time analysis and is not revised as work lands, so the
ranking below still reads as though nothing has been built. What has:

- **#1 front/back registration offset** — shipped 2026-08-06, CLI and desktop
  print path. Measuring the two numbers is still manual; the calibration
  wizard is what closes that.
- **#2 cut lines** — shipped 2026-08-06, `--trim`.
- **#6 source cropping** — shipped 2026-08-06, `--crop`/`--crop-even`, plus
  `--auto-crop` for krop's "trim margins" equivalent, measured off the
  existing ink-bbox cache, and `deckle crop-preview` for the briss-style
  overlay. The overlay is a written image, not an interactive in-app
  editor -- when this was written the desktop app had no crop controls at
  all, so an in-app preview would have meant building those first. The
  controls shipped 2026-09-04 (`fa7d888`, the Crop & trim tab); showing the
  composite inside the app is the part still outstanding.
- **#9 page-number stamping** — shipped 2026-08-06 as the small version the
  analysis recommends: `deckle dummy` generates a numbered source rather
  than stamping user content. Built to make the folio check cheap.
- **#7 custom per-signature lengths** — shipped 2026-08-06, `--signatures`.
- **#5 grain direction** — shipped 2026-08-05 (`7d7b3a2`).
- **#8 spine width in the binding schedule** — shipped 2026-08-05 (`7d7b3a2`).

Everything else below is still open. The closing recommendation — *verify
folio on paper* — is also still open, and still gates #3, #4 and #6.

## Recommended

Ranked by value to one person, at home, on a duplexer-less printer, binding by
hand.

### 1. Front/back registration offset in the printer profile — SMALL/MEDIUM

**What it is.** Two numbers per printer — an X and Y offset in points —
measured once from a printed target and applied to every *back-side*
placement, so the back of a sheet lands exactly behind the front.

**Who has it.** **Nobody.** This is the one genuinely open gap in the field.
Every surveyed tool ends at a PDF, so none of them can know or correct for a
specific printer's duplex misregistration. Bookbinder JS's own instruction
copy concedes the problem without being able to fix it — verbatim from
[`src/html/sig_format.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/sig_format.html):

> "Note that when folding, it's important for the **center of the folios to
> line up**. That's what I measure against (less so fretting about folding
> exactly at the edge). Remember! Printer skew is a real thing!"

Duplex misregistration is a well-documented consumer-printer complaint —
see the Adobe community threads on
[duplex misalignment](https://community.adobe.com/t5/indesign-discussions/how-to-fix-misalignment-issues-in-duplex-printing/m-p/15218272)
and [booklet manual duplex](https://community.adobe.com/questions-671/booklet-print-spreads-manual-duplex-903631) —
and it is *worse* on a manual-duplex reload than on a real duplexer, because
the stack is re-registered by hand against the paper guides.

**Why a hand binder wants it.** Fold a folio sheet down the middle and the
error is doubled and made visible: the spine margin on the verso differs from
the recto by the misregistration amount, and when you trim the fore-edge of
the finished block, the text block is visibly off-centre on every other page.
On a gutter-shift job the same error means one side of every leaf has a
narrower gutter than the other, and a 3-hole punch eats into text on the
tighter side.

**Fit with Deckle.** Excellent — this is precisely the payoff of Deckle being
the only tool that owns the print path. `PrinterProfile` already exists and
already persists per printer; add `back_offset_x_pt` / `back_offset_y_pt`.
`PassPlanner` (or the backend, wherever pass-2 placements are materialised)
adds the offset to back-side placements. The calibration wizard, which is
already scoped and already prints a marked test sheet, prints a target with a
graduated scale on both sides; the user reads two numbers off it.

**Size.** SMALL if it is added to the existing calibration wizard while that
wizard is being built; MEDIUM if the wizard has to be reopened later. **Build
it at the same time as the wizard.** This is a scheduling argument as much as
a feature argument.

**Licence.** No new dependency. Pure arithmetic on `Placement`.

**Caveat worth stating in the UI.** This corrects a *constant* offset. It
cannot correct rotational skew or scale error, and it should say so rather
than implying it fixes all misregistration.

---

### 2. Cut lines — SMALL

**What it is.** A fourth `Mark.kind` — a line showing where to cut, as
distinct from where to fold.

**Who has it.**
[Bookbinder JS](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/crop_box.html)
treats them as separate first-class checkboxes: `Add Foldlines:` ("Adds
folding guidelines (ordered thickest to thinnest)") and `Add Cutlines:`
("Adds cutting guidelines"). It also has a third, `Add PDF bounds indicators
(spine)` — "small marks on the outermost folio of the signatures indicating
the top and bottom of the PDF".
[Create Booklet 2](https://www.thekeptpromise.com/CreateBooklet/) advertises
fold *and* cut marks. Quite Imposing adds crop marks
([manual](https://www.quite.com/docs/qi6/en/qi6_manual/qi6_guide.pdf)).

**What Deckle has.** `deckle/core/models.py:114` —
`kind: Literal["sewing_station","signature_order","fold_line"]`. Fold lines
only.

**Why a hand binder wants it.** Two distinct physical operations. First, on
any layout with more than one leaf per cell axis you must *cut* the sheet
before you can fold it — that is a cut line, and drawing it as a fold line is
actively wrong. Second, and true even for plain folio: after sewing, the
fore-edge of the block is trimmed square, and a cut line printed at the trim
depth tells you where the plough or knife goes and warns you if text is inside
it. Deckle's schedule already computes a fore-edge creep estimate; a cut line
is that estimate made physical.

**Size.** SMALL. `deckle/core/marks.py` is pure geometry returning `Mark`
tuples and the renderer already draws whatever it is handed; this is one new
literal value, one geometry function, and one setting.

**Licence.** Clean.

---

### 3. Quarto (and octavo) — four or eight pages per side — MEDIUM

**What it is.** Fold the sheet twice instead of once. Bookbinder JS's
`Style:` dropdown, verbatim from
[`src/html/page_layout.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/page_layout.html):

> `Folio - two pages per side of sheet` / `Quarto - four pages per side of
> sheet` / `Octavo - eight pages per side of sheet` / `Sextodecimo - sixteen
> pages per side of sheet`

with per-cell rotation tables in
[`src/constants.js`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/constants.js)
(`PAGE_LAYOUTS`, giving `rows`, `cols`, `per_sheet`, and a `rotations` matrix
per scheme) and page-order tables in `BOOKLET_LAYOUTS` for signature sizes
4/8/16/32.

**What Deckle has.** `--fold-scheme {none,folio}` only.

**Why a hand binder wants it.** This is the single biggest determinant of
finished book size on a home printer. Letter paper folded once gives a
5.5×8.5 book — large, and it eats a sheet of paper every four pages. Folded
twice it gives 4.25×5.5, a pocket-size book, at half the paper. For anyone
printing a long text at home, quarto is the difference between a 100-sheet
book and a 50-sheet book. Octavo goes further but demands a bone folder and
patience; sextodecimo on letter paper is a novelty.

**Size.** MEDIUM, and **smaller than it looks**, because Deckle's layout core
is already cell-general: `deckle/core/layout.py:29` defines
`Cell = tuple[float, float, float, float]`, and `content_box_size`,
`document_scale` and the placement function all accept an arbitrary cell,
with `GutterShiftStrategy` passing the whole sheet and `SaddleStitchStrategy`
passing one of two folio cells. Quarto is a 2×2 cell grid, a per-cell rotation
(and `Placement` already carries `rotate_deg`), and a page-order table. The
work is the ordering table and proving it — not the geometry.

**The hard part is the same hard part folio already has.** The README is
honest that folio's ordering is unverified until someone folds a dummy. Quarto
multiplies that: the fold sequence is non-obvious enough that Bookbinder JS
ships a paragraph of prose explaining it ("top to bottom, fold points away
from you, left to right, fold points towards you"). **Do not ship quarto until
folio has been verified on paper.** Verifying folio is the prerequisite, and
`deckle schedule` already exists to make that check cheap.

**Licence.** Clean — the ordering tables are centuries-old public knowledge,
and the design document already establishes that. Bookbinder JS is MPL-2.0
(file-level copyleft): read it, do not paste it. HornPenguin/Booklet is BSD-3
and vendorable with notice, per the design doc, though its licence still needs
direct verification.

---

### 4. French fold (single-sided printing, fold at the fore-edge) — MEDIUM

**What it is.** Print on **one side of the paper only**, fold each sheet in
half with the printed faces outward, and gather with the folds at the
*fore-edge* and the open edges at the spine. The blank sides end up hidden
inside each folded leaf. Bind the open edge — stab binding, or glue.

**Who has it.** Not offered as a named mode by any tool I checked. Bookbinder
JS's `Single Sheet Zine - 8 per side` is a cousin (single-sided, folded, no
cut) but not a general French fold. **I could not verify that any tool
implements this as a general imposition scheme** — treat that as an unverified
negative rather than a confirmed one.

**Why a hand binder wants it, and why *this* user especially.** It removes the
manual-duplex second pass entirely. No reload, no flip calibration, no pass-2
misregistration, no risk of discarding sixty sheets printed upside down —
which the design document names as Deckle's second-most-expensive failure
mode. It also solves show-through: on cheap 20lb paper, dense text bleeds
visibly through to the back, and French fold hides the back completely. The
cost is exactly double the paper.

**Fit with Deckle.** Strong, and slightly ironic: it is the one imposition
scheme whose value proposition is *not needing* Deckle's print pipeline. It is
still worth having, because "print this one single-sided" is a legitimate
answer to "my printer's back pass is unreliable" and Deckle is the tool that
knows that about your printer.

**Size.** MEDIUM. Two cells per sheet, one side only, spine on the *outer*
edges of the cells rather than the inner — which is a mirror of the existing
folio geometry — plus its own ordering (much simpler than saddle stitch:
consecutive pairs, no nesting). `Sheet.back` is already `Side | None`, so a
back-less sheet is already representable.

**Licence.** Clean.

---

### 5. Grain-direction warning — SMALL

**What it is.** One setting on the paper — `grain: long | short | unknown` —
and a `LayoutWarning` when the fold axis runs *across* the grain instead of
along it.

**Who has it.** **Nobody.** No imposition tool I found models paper grain.

**Why a hand binder wants it.** Grain direction is described across the
bookbinding literature as the single most consequential material property:
[iBookBinding](https://www.ibookbinding.com/blog/paper-grain-direction-and-cardboard-grain-direction/)
and [Papercraft Panda](https://blog.papercraftpanda.com/the-most-important-rule-in-bookbinding-grain-direction/)
both state the rule as *grain must run parallel to the spine*, for the text
block, the endpapers, the spine lining and the boards alike. Fold against the
grain and the fold cracks and feathers instead of creasing cleanly; the book
will not open flat, the pages cockle when glue introduces moisture, and the
spine warps as humidity changes. Standard 8.5×11 office paper is **long
grain** — grain along the 11" dimension. Fold it in half the usual way for a
5.5×8.5 book and the fold runs across the 11" axis, i.e. **against** the
grain. Binders buy short-grain letter stock specifically to fix this
([Papercraft Panda](https://blog.papercraftpanda.com/two-easy-methods-to-find-grain-direction-in-paper/)).

**Why it belongs in Deckle specifically.** Deckle already knows the paper
dimensions, the sheet orientation, and — under folio — the fold axis. It is
the only piece of information needed to derive the warning, and Deckle already
has a warning channel that attaches to sheets and a schedule that talks to the
binder at the bench. The advice is cheap to give and expensive to discover
after gluing.

**Size.** SMALL. One enum on `LayoutSettings`, one new `LayoutWarning.kind`,
one comparison. Default `unknown` and stay silent, so it never nags.

**Licence.** Clean.

---

### 6. Source cropping — MEDIUM

**What it is.** Trim the source pages before imposing: set a crop box, with an
overlay preview and separate odd/even rectangles.

**Who has it.**
[briss](https://sourceforge.net/projects/briss/) — its distinguishing feature
is the overlay view that superimposes all pages so you can see the real
content extent, with separate odd/even crop regions (because scanned books
alternate margins) and page-range exclusion.
[krop](https://arminstraub.com/software/krop) offers a Trim Margins action
that auto-derives the crop.
[Quite Imposing Plus](https://www.quite.com/docs/qi6/en/qi6_manual/qi6_guide.pdf)
has "Trim And Shift".
[Create Booklet 2](https://www.thekeptpromise.com/CreateBooklet/) has crop box
support.

**What Deckle has.** Scale and four margins, which *add* space. There is no
way to *remove* existing space that is baked into the source.

**Why a hand binder wants it.** The most common source for a hand-bound book
is a scan or a public-domain PDF typeset for a different trim size, carrying
1.5" of white margin on every side. Scaling it to fit a 5.5×8.5 cell scales
the *margins* too, so the type ends up tiny and the page mostly empty.
Cropping first is the only way to get readable type at a small trim size — and
small trim size is exactly what quarto (gap 3) is for. **These two gaps
compound**: quarto without cropping produces books with unreadably small type.

**Size.** MEDIUM. A new per-page (or global, with odd/even variants) crop
rectangle on the model, consumed by `document_scale` and the placement
function; a preview overlay in the app; a CLI flag. pikepdf sets `CropBox`
directly, and Deckle already rasterises pages for preview, so a briss-style
composite overlay is achievable without new machinery.

**Licence.** Clean — pikepdf (MPL-2.0) and pypdfium2 already present. **Do
not** reach for briss or krop as libraries; briss is Java, and krop is a
PyQt/PyPDF tool whose licence would need checking. Reimplement.

---

### 7. Custom per-signature lengths — SMALL

**What it is.** Instead of one uniform sheets-per-signature, an explicit list.

**Who has it.** Bookbinder JS, verbatim from
[`src/html/sig_format.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/sig_format.html):
`Custom signatures` with the hint "Specify as a set of numbers seperated by
commas, such as: 10, 10, 8".

**What Deckle has.** `--sheets-per-signature` (uniform) plus `--blank-mode
{end,balanced}`, which distributes *padding* but cannot express a deliberately
uneven gathering.

**Why a hand binder wants it.** Two real cases. A book whose page count
divides badly: rather than accept six blank leaves at the end, you make the
last signature shorter. And chapter-aligned gatherings, where you want a
chapter break to land at a signature boundary so the book opens flat there.
The current `balanced` mode gets partway to the first case but the binder
cannot state the answer directly.

**Size.** SMALL. `split_signatures` in `deckle/core/signatures.py` already
takes a sheet count and produces groups; accepting an explicit list is a
parameter change plus validation that the lengths sum correctly. Add a CLI
flag and a text field.

**Licence.** Clean.

---

### 8. Spine width in the binding schedule — SMALL

**What it is.** Print the computed text-block thickness on the schedule:
`sheets × caliper`, plus a stated swell allowance for sewing thread.

**Who has it.** Not integrated into any imposition tool I found — it lives in
standalone calculators such as
[PrintNinja's](https://printninja.com/spine-width-calculator/), which use
`(page count / 2) × paper caliper + binding allowance`, and in cover-design
workflows.

**Why a hand binder wants it.** You cut the spine piece, the boards and the
case *before* the block is finished, and the number you need is the block
thickness. Sewing adds swell at the spine — thread accumulates in every fold —
so the spine measurement is not the fore-edge measurement, and guidance
recommends adding an allowance (one source suggests ~1.5× board thickness for
the covering material to wrap without bowing). Getting this wrong means
recutting boards.

**Why it belongs in Deckle.** `LayoutSettings` already carries
`paper_thickness_pt` and Deckle already knows the sheet count per signature and
in total; `deckle schedule` is already a printed work order for the bench. This
is three lines of arithmetic added to a document that already exists. Report it
as a range with the assumption stated, not as a precise number — paper caliper
varies 2–5% with humidity.

**Size.** SMALL.

**Licence.** Clean.

---

### 9. Page-number stamping — SMALL/MEDIUM

**What it is.** Overprint a sequence number on each imposed leaf.

**Who has it.** BookletMaker lists page numbers among its features
([bookletmaker.app](https://bookletmaker.app/) — **unverified**, the site
refused automated fetch; claim comes from a search summary). Bookbinder JS
ships a test fixture for exactly this purpose: `docs/example_page_numbers.pdf`,
described in its
[README](https://github.com/momijizukamori/bookbinder-js/blob/main/README.md)
as "a basic PDF with just the numbers 1-120 writ large, used for figuring out
page ordering."

**Why a hand binder wants it.** Two uses, and the second is the better
argument. For content: many scans have no printed folios, and after folding
you cannot tell a misplaced leaf from a correct one. For **verification**:
stamping sequence numbers on a scrap print is exactly how you check that an
imposition scheme folds correctly — which is the open question hanging over
Deckle's folio mode and the prerequisite for quarto. A built-in "print a
numbered dummy" would let a user verify folio in five minutes on scrap paper.

**Size.** SMALL if scoped to a *numbered test document generator* (synthesise
an N-page PDF of large numerals and run it through the normal pipeline —
Deckle's calibration wizard already synthesises a test plan, so this is the
same trick). MEDIUM if scoped as general folio stamping on user content, which
drags in font embedding, position rules, and whether numbers land inside the
imageable area. **Do the small version.**

**Licence.** Clean, if the numerals are drawn as vector paths or use a base-14
PDF font rather than embedding a font file.

---

## Considered and rejected

### Prepress marks: bleed, registration marks, colour bars, page information

**Who has it.** [Scribus](https://www.linuxjournal.com/content/exporting-pdf-scribus)
covers crop, bleed and registration marks in its PDF export Pre-Press tab and
has document-bleed settings; Quite Imposing adds and removes crop marks.

**Reject.** These exist so a commercial press can trim, align plates and check
ink density. A home inkjet or laser printer cannot print to the sheet edge at
all — Deckle already models this correctly as the imageable area, which is the
*opposite* constraint. Bleed on a printer with a 0.16–0.25" non-printable
border is a contradiction in terms. Registration marks for plate alignment are
meaningless for a single-device job. **The one genuinely useful registration
idea — front-to-back alignment — is Recommended #1 above, and it is a
different feature that happens to share a word.**

Note also that **Scribus does not have imposition at all** — the
[imposition plugin was a 2007 GSoC proposal](https://wiki.scribus.net/canvas/Imposition_Plugin_Discussion)
that was never merged, and the practical workaround is a
[Laidout round-trip](https://laidout.org/scribus/) whose own documentation
admits Scribus bleed values are "basically ignored". Scribus is a layout
application that exports press-ready PDFs; it is not a peer to Deckle and
should not be treated as a source of feature ideas.

### Creep / shingling compensation

**Who has it.** [Quite Imposing Plus](https://www.quite.com/docs/qi6/en/qi6_manual/qi6_guide.pdf)
via Trim And Shift, with the option to compensate by scaling instead of
shifting so no content is lost. [Create Booklet 2](https://www.thekeptpromise.com/CreateBooklet/)
and BookletMaker both advertise creep compensation.

**Reject for now — keep the advisory.** The design document already reached
this conclusion and the arithmetic supports it: at 4–6 sheets per signature on
office paper, creep is well under a millimetre. Deckle already estimates it and
says so (`_creep_note` in `deckle/core/schedule.py` stays silent below 1pt),
which is the right amount of attention. Note that Quite Imposing's own manual
says shingling and bottling are "not easy to allow for" *in their own tool* —
this is hard for professionals with a plate press, and it is not where a home
binder's error budget goes.

**Revisit if and only if** octavo or thick signatures (10+ sheets) ship. Then
it becomes a real millimetre-scale effect. **Do not implement it as scaling** —
that reintroduces exactly the type-size drift that disqualified Bundsteg.

### Bookbinder JS's "Wacky Small Layouts"

**Who has it.** Bookbinder JS, extensively: `6 per side`, `Petite - 16 per
side`, `Single Sheet Zine - 8 per side`, `Small - 18 per side`, `Little - 20
per side`, `Tiny - 32 per side`, `Mini - 60 per side`, each with hand-written
folding prose and diagram images
([`src/html/sig_format.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/sig_format.html)).

**Reject.** These are charming and clearly loved, but they are a combinatorial
pile of bespoke geometries whose correctness is provable only by folding each
one, and each ships a paragraph of instructions and a diagram. Bookbinder JS's
own UI carries the warning that they "do not currently work with" its PDF
markup features — a maintenance smell. Deckle cannot yet verify the *one*
scheme it has. Adding seven unverifiable ones inverts the project's stated
priority that the printed physical result must be correct.

### Perfect binding (flat single leaves, glued spine)

**Who has it.** Bookbinder JS has a `Perfectbound` radio and a full
`PERFECTBOUND_LAYOUTS` ordering table for 4/8/16/32 in
[`src/constants.js`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/constants.js).

**Reject — but only just, and reconsider later.** This is the closest call in
this document. Perfect binding is genuinely popular with home binders who do
not want to sew, and Bookbinder JS's tables show the work is bounded. But
Deckle's default gutter-shift mode *already produces exactly what a perfect
binding needs* — one page per side, alternating gutter, ready to guillotine and
glue — and does so without cutting or folding at all. Bookbinder JS needs a
perfect-bound imposition mode because it is signature-first; Deckle is not.
What Deckle's gutter-shift mode does not give you is 2-up-then-cut, which
halves the paper. That is worth having, but it is really "quarto without the
folds" and should be built on top of the same cell generalisation as gap 3 —
after it, not instead of it.

### Cover wraps, case templates, endpaper generation

**Reject.** A cover wrap is a single large sheet with spine width, board
overhang, turn-in allowance and corner mitres. It is a drawing task, not an
imposition task, and Scribus and Inkscape do it well. Deckle should contribute
the *number* (Recommended #8) and stop there. Building a case-design surface
would double the app's scope and its UI vocabulary.

### Cut-and-stack n-up for multiple copies

**Who has it.** pdfimpose has `cutstackfold` and `copycutfold` among its seven
schemas ([docs](https://pdfimpose.readthedocs.io/en/latest/)).

**Reject.** This is production imposition — printing many copies efficiently on
oversized stock and guillotining the stack. A person hand-binding at home on
letter paper prints one copy. Wrong problem.

### Coptic binding, and binding styles generally

**Reject as a feature.** Coptic, long-stitch, and stab bindings differ in how
the sewing is done, not in how the pages are imposed. Coptic uses ordinary
folded signatures — already covered by folio — and its distinguishing needs
(station spacing, no spine glue) are already served by Deckle's configurable
sewing-station count. Stab binding wants French fold, which is Recommended #4.
There is no separate "coptic mode" worth building.

### Flyleaves as a dedicated setting

**Who has it.** Bookbinder JS: `Add flyleafs:` — "Extra blank pages at the
start and end of the book"
([`src/html/flyleaf.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/flyleaf.html)).

**Reject as redundant.** Deckle already supports inserting blanks anywhere with
undo. A dedicated counter is convenience, not capability, and Deckle's arrange
view makes the blanks visible — which is better than a number in a box.

### Parity-aware source rotation for top- and bottom-edge binding

**Who has it.** Bookbinder JS `Source Manipulation` offers `odd pages 90°
clockwise, even pages 90° anti-clockwise` and its inverse, for "books with
bound edge at bottom of the page" and at the top
([`src/html/source_manip.html`](https://github.com/momijizukamori/bookbinder-js/blob/main/src/html/source_manip.html)).

**Reject as low value, note as trivially cheap.** Deckle has per-page rotation
and a global landscape policy, so this is achievable manually today. Top- and
bottom-bound books (calendars, flip-style notebooks) are a small fraction of
hand binding. If the layout panel ever grows a rotation preset dropdown, add
these two entries then — it is a few lines. Do not schedule it on its own.

### Custom paper sizes — **not a gap**

Bookbinder JS has a CUSTOM paper entry. So does Deckle: `--paper` accepts
`WxH[unit]` as well as presets (`deckle/cli.py`). The GUI's list is shorter
(Letter, A4, Legal, A3, Tabloid) — if a custom entry is missing there, that is
a small UI gap, not a capability gap.

---

## Per-tool feature table

Columns are the features discussed above. **Y** = has it, **n** = does not,
**~** = partial, **?** = could not verify. Deckle's row is read from source.

| Feature | Deckle | Bookbinder JS | Create Booklet 2 | BookletMaker | Quite Imposing Plus | pdfimpose | briss / krop | Scribus |
|---|---|---|---|---|---|---|---|---|
| Drives a printer directly | **Y** | n | Y | ? | n | n | n | Y (layout app) |
| Manual duplex pass splitting | **Y** | ~ (side1/side2 files) | Y | ? | n | n | n | n |
| Sheet-granular resume / reprint | **Y** | n | n | n | n | n | n | n |
| Front/back registration offset | **n** | n | n | n | n | n | n | n |
| Per-printer calibration profile | ~ (planned) | n | n | n | n | n | n | n |
| Imageable-area modelling | **Y** | n | ? | ? | ? | n | n | ~ |
| Alternating binding gutter | **Y** | Y | ? | ? | Y (Trim And Shift) | ~ (margins) | n | n |
| Four independent margins | **Y** | Y | ? | ? | Y | ~ | n | n |
| Folio (2-up) | **Y** (experimental) | Y | Y | Y | Y | Y (saddle) | n | n |
| Quarto (4-up) | **n** | Y | Y | Y (4-up) | Y | Y | n | n |
| Octavo / sextodecimo | **n** | Y | ~ (mini booklet) | n | Y | n | n | n |
| French fold | **n** | ~ (zine only) | ? | n | ? | ~ (onepagezine) | n | n |
| Perfect-bound imposition | ~ (via gutter shift) | Y | n | n | Y | Y (hardcover) | n | n |
| Custom per-signature lengths | **n** | Y | n | n | ? | Y (`--signature`) | n | n |
| Fold lines | **Y** | Y | Y | ? | n | Y (`--mark`?) | n | n |
| Cut / crop marks | **n** | Y | Y | Y | Y | Y (`--mark`?) | n | Y |
| Sewing station marks | **Y** | Y | n | n | n | n | n | n |
| Signature order marks | **Y** | Y | n | n | n | n | n | n |
| Staple marks | n | n | Y | n | n | n | n | n |
| Creep compensation | ~ (advisory only) | n | Y | Y | Y | ~ (`--creep`, author-flagged broken) | n | n |
| Grain-direction warning | **n** | n | n | n | n | n | n | n |
| Spine-width calculation | **n** | n | n | n | n | n | n | n |
| Source cropping / trim | **n** | ~ (crop box UI) | Y | n | Y | n | **Y** | n |
| Page-number stamping | **n** | n | n | Y (?) | Y | n | n | Y |
| Bleed / registration / colour bars | n | n | n | n | ~ | n | n | **Y** |
| Image folder import | **Y** | n | n | n | n | n | n | Y |
| Page reorder with undo | **Y** | n | n | n | Y | n | n | Y |
| Headless CLI | **Y** | n | n | n | ~ (Quite Hot) | **Y** | ~ (krop) | ~ (scripts) |
| Binding schedule / work order | **Y** | ~ (sig arrangement summary) | n | n | n | n | n | n |
| Licence | MIT | MPL-2.0 | proprietary | proprietary | proprietary | **AGPL-3.0** | GPL / ? | GPL |

Cells marked `?` for the proprietary Mac apps reflect that their feature lists
come from vendor pages, not from testing. Quite Imposing's `~` for bleed
reflects that it adds and removes crop marks but is a plugin inside Acrobat,
where bleed is Acrobat's concern.

---

## Licence hazards to avoid

Nothing recommended above requires a new dependency. For completeness, the
tools that would be tempting to reach for and must not be:

- **pdfimpose** — AGPL-3.0, and depends on AGPL PyMuPDF. Its schema list is a
  useful *map*; its code is untouchable.
- **cpdf** — AGPL-3.0 or commercial.
- **PyMuPDF, Ghostscript, Poppler** — AGPL and/or external binary. Already
  excluded by the licence-audit test.
- **Bookbinder JS** — MPL-2.0, file-level copyleft. Reading its UI and its
  ordering tables to understand *what* a feature is, as this document does, is
  fine. Translating its JS into Python files is not; those files would carry
  MPL. Ordering tables for centuries-old fold schemes can be derived
  independently, and `deckle schedule` already exists to check that a derived
  table matches a bookbinding manual.
- **briss** (Java, GPL) and **krop** — reimplement the *idea* of an overlay
  crop; do not vendor.
- **HornPenguin/Booklet** — BSD-3 and vendorable per the design document, but
  its `LICENSE` file still needs direct verification before anything is copied,
  and its I/O layer (PyPDF2 + pdf2image + Poppler) must not come with it.

---

## The short version

If only three things get built: **front/back registration offset** (nobody has
it, only Deckle can), **cut lines** (a one-literal change to a module that
already exists), and **grain direction** (free, and it is the rule every
bookbinding text calls the most important one).

Then **verify folio on paper** — because quarto, French fold, cropping and
everything else downstream all inherit its unverified ordering, and every one
of them is worth more once that question is closed.
