# D1-D11 — The documentation pass: eleven sentences that are not true

**Roadmap item:** `docs/ROADMAP.md` §4, D1 through D11
**Depends on:** R0.1 (D9 is *fixed by* it, not here), and — for D3 and D4 —
whichever of B15/B16/F1 has landed at the time this runs; see §3.
**Blocks:** —
**Size:** M (eleven edits, no code)
**Decision needed first:** none.

---

## 1. Context

`docs/ROADMAP.md` §6 item 10 says the docs pass belongs *"at the end of each
milestone rather than as its own item, or the GUIDE keeps drifting."* This
file is the list of what has already drifted, so that whoever runs the pass
does not have to rediscover it.

Every item is a sentence a reader would act on and be wrong. Two of them are
about paper directly: the GUIDE tells the reader the solid red line in the
preview is *"your printer's hardware limit"*, so somebody sets margins from
it and prints into their printer's dead border; and it says the app has *"no
crop controls at all"*, so somebody drops to the command line for a job the
Crop & trim tab has done since `fa7d888`. The rest cost trust rather than
paper, which is its own currency for a tool whose whole pitch is that it
tells you the truth before you commit a ream.

Nothing here changes code. Nothing here is optional judgement: every edit
below quotes the current text and gives the replacement.

**Read §3's ordering note before starting.** Two edits (D3, D4) describe
behaviour that three other specs are actively changing, and getting them
wrong in the *other* direction is just as bad.

## 2. Current code

Verified against the tree at `08e7f49`. Line numbers are from that tree;
confirm each quote with `grep -n` before editing, because five of these files
have other specs landing in them.

### The measurements this pass needs

Full suite, this machine, this commit:

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider
2 failed, 1507 passed, 22 skipped, 3 warnings in 54.29s
```

The two failures are R0.3 and R0.4. With those fixed and nothing else
changed, the tree is **1509 passed, 22 skipped**. Collection reports
`1531 tests collected`.

The 22 skips, by reason (`pytest -rs`):

| Count | Reason |
|---|---|
| 14 | `tests/test_packaging_audit.py` — "no built bundle in dist/deckle" |
| 4 | Windows-only: drive letters, path semantics, a file held open by another process |
| 3 | `tests/test_golden_pinebox.py` — the 30 MB Pinebox fixture is not committed |
| 1 | `tests/test_export.py` — `psutil` not installed |

**`docs/ROADMAP.md` D1 says "1531 collected; decisions.md recorded 1416 on
2026-08-07"** — the collected count is right; the number a green run
*passes* is 1509, and that is the number a README sentence about "passing"
needs.

### D1 — `README.md:392-394`

```
The test suite is **608 passing, 17 skipped** — verified by running
`python -m pytest -q` at the repository root. The skips are golden-fixture
comparisons whose fixture is not committed.
```

Both numbers and the explanation of the skips are wrong: 14 of the 22 are
packaging-audit tests waiting on a built bundle, not golden-fixture
comparisons.

### D2 — two files

`docs/GUIDE.md:310-311`:

```
There is no interactive crop editor in the desktop app; it has no crop controls
at all. This is a picture you look at, then a `--crop` you type.
```

`docs/research/2026-08-05-competitive-gaps.md:66-68`:

```
  overlay. The overlay is a written image, not an interactive in-app
  editor -- the desktop app has no crop controls at all, so an in-app
  preview would mean building those first.
```

False since `fa7d888` ("Reach the last four settings, and give creep one
opinion"), which added the Crop & trim tab with eight spin boxes and a
**Measure crop from the ink** button. `deckle/app/views/layout_panel.py`
carries `set_crop`, `_crop_from_boxes`, `crop_spinboxes` and the auto-crop
handler at lines 1600-1650. What is still true is that there is no
*interactive* editor — no picture in the app to drag a rectangle on. That is
N11.

`docs/decisions.md:530` contains the same phrase and **must not be touched**:
it is a dated log entry recording the reasoning at the time, and the log is
never rewritten.

### D3 — `docs/GUIDE.md:521-527`

```
**The calibration wizard is not built.** Until it is, Deckle uses two built-in
generic presets covering the two common reload behaviours: a face-down printer
whose stack comes out reversed, and a face-up printer that keeps its order.
Profiles are persisted per printer name as JSON under the OS config directory
(`%APPDATA%\Deckle\printer_profiles` on Windows), so a hand-edited one
survives.
```

Two presets exist (`deckle.core.profiles.BUILTIN_PRESETS`, and `--profile`
accepts both names — confirmed in `deckle.cli export --help`), but the
**desktop app only ever resolves the first**
(`deckle/app/views/print_dialog.py:59-90`). A face-up printer's owner has no
in-app way to pick the other one. That is B16, and F1 is the profile picker
that fixes it.

### D4 — two files

`docs/GUIDE.md:174-177`:

```
- **Solid red — the imageable area.** Your printer's hardware limit; the
  border it physically cannot mark, typically 0.16–0.25 inches. Deckle cannot
  infer this from the imposition, because the imposer never sees the printer;
  "Use printer margins" in the app bridges that deliberately.
```

`README.md:331-335`:

```
- **Two guides, drawn distinctly.** Solid red is the printer's *imageable
  area* — a hardware limit, the border your printer physically cannot mark.
  Dashed blue is your *content box* — your margins. They coincide only by
  coincidence, and showing one while the user is asking about the other is how
  a preview lies.
```

The red guide is drawn from `DEFAULT_PROFILE`'s fixed 0.25in inset
(`deckle/app/main.py:442, 523`); nothing pushes a resolved printer profile to
the panel or the preview, and the backend never reads the driver's printable
rect. So the line is a *generic default*, not a measurement of the attached
printer. That is B15, and N2 is the fix that makes the sentence true.

The last clause of the README bullet — *"showing one while the user is
asking about the other is how a preview lies"* — is exactly the standard this
row fails.

### D5 — `docs/GUIDE.md:717-782`, the CLI reference

The full section is quoted in §3 where it is edited. What it omits, checked
against `python -m deckle.cli <cmd> --help` on this tree:

- **Two commands:** `crop-preview` and `dummy`.
- **Nine layout options** every command already accepts: `--paper-weight`,
  `--paper-type`, `--paper-grade`, `--signatures`, `--crop`, `--crop-even`,
  `--auto-crop`, `--auto-crop-margin`, `--trim`.
- **Five `export`-only options:** `--sheets`, `--rule`, `--pass`,
  `--profile`, `--back-offset`.
- **That a `.deckle` project is accepted as `SOURCE`** by `info`, `export`,
  `impose` and `schedule`, and that its stored layout wins over any layout
  flag typed beside it. `deckle/cli.py:621-639`'s `_resolve_input` docstring
  states this explicitly: *"A `.deckle` carries its own layout, and that
  layout wins... Flags typed alongside a project are reported as ignored."*

What the section gets **right** and must keep: the closing paragraph
*"**Not available from the CLI:** head, tail and fore-edge margins;
`slack_to`; `start_on_recto`; landscape policy."* Confirmed — none of
`--margin-top`, `--margin-bottom`, `--margin-outer`, `--slack-to`,
`--start-on-verso` or `--landscape-policy` appears in any subcommand's help.
That is F5.

### D6 — `docs/GUIDE.md` has no coverage of three shipped features

```
$ grep -ci gsm docs/GUIDE.md          → 0
$ grep -ci autosave docs/GUIDE.md     → 0
$ grep -ci recover docs/GUIDE.md      → 0
$ grep -ci suggest docs/GUIDE.md      → 0
```

`README.md:384-387` and `CHANGELOG.md:66-81` both cover paper-by-weight, the
signature-size suggestion and crash recovery. The GUIDE — the long-form
how-to, and the file `README.md:453` points at for exactly this — does not
mention any of them. `docs/plans/2026-08-08-paper-and-recovery-plan.md:483`
("Task 10") called for the section and it was not written.

`docs/GUIDE.md:102-108` is the nearest thing, and it tells the reader to
measure a caliper:

```
### Caliper

`--paper-thickness 0.004in` (or `0.1mm`, or whatever you measure). It affects
**no placement Deckle emits** — not one. It exists purely so the binding
schedule can predict fore-edge creep and spine thickness, both of which you
need before the block is finished and cannot measure yet.
```

### D7 — `README.md:409-412`

```
deckle/core/    models, loader, layout, export, render, printing, profiles,
                marks, schedule, signatures, print_session, project_io,
                outputs, session_log, diagnostics
```

Fifteen names. The package has twenty-one:

```
$ ls deckle/core/*.py
diagnostics dummy export layout loader locate marks models outputs paper
paths print_session printing profiles project_io recent render schedule
schema session_log signatures
```

Missing: `dummy`, `locate`, `paper`, `paths`, `recent`, `schema` — exactly
the six the roadmap names.

### D8 — `CHANGELOG.md:36-37`

```
- **CLI** — `impose`, `export`, `info`, `--version`. Imports only
  `deckle.core`, so it runs headlessly.
```

Four of the six commands. `schedule` appears later under its own "Binding
schedules" bullet; `crop-preview` and `dummy` appear **only under Fixed**
(`CHANGELOG.md:168` and `:178`), as bugs that were repaired in commands the
Added section never announced. And no export flag — `--sheets`, `--rule`,
`--pass`, `--profile`, `--back-offset`, `--trim`, `--signatures` — appears
anywhere in the file:

```
$ grep -c -- "--sheets" CHANGELOG.md   → 0
$ grep -c -- "--rule" CHANGELOG.md     → 0
$ grep -c -- "--pass" CHANGELOG.md     → 0
$ grep -c -- "--profile" CHANGELOG.md  → 0
$ grep -c -- "--back-offset" CHANGELOG.md → 0
$ grep -c -- "--trim" CHANGELOG.md     → 0
$ grep -c -- "--signatures" CHANGELOG.md → 0
```

### D9 — `tests/fixtures/README.md:1-8`

```
# Test fixtures

## `sample.pdf`

A small, checked-in PDF used by the fast unit and integration tests
(`tests/test_loader.py`, `tests/test_integration.py`, etc.). No setup
required.
```

**R0.1 makes this true rather than this pass making it false.** See §3.

### D10 — `deckle/app/main.py:1-7`

```python
"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
"""
```

There is no printer menu. `refresh_printers` is called once, from
`__init__`; `deckle/app/main.py:583` is the only enumeration site, and
`_on_print_clicked` reads the cached list. That is B30.

**This one is a `.py` file**, so it is the only item in this spec that trips
the pre-commit hook's decisions.md requirement. See §7.

### D11 — `docs/GUIDE.md:366-421`

The preamble at `docs/GUIDE.md:366`:

```
> Two ways to close the question yourself, both cheap. Do one.
```

is followed by **three** headings — `docs/GUIDE.md:368`, `:399` and `:420`:

```
**Check 0 — fold a numbered dummy.** Five minutes, and the only one of these
that answers the question outright.
```

```
**Check 1 — read the schedule against a manual.** Thirty seconds.
```

```
**Check 2 — fold a dummy.** Five minutes, and it is the one that actually
settles it. Print one signature onto scrap, fold it, read it.
```

Check 0 and Check 2 are the same check, and Check 0 is the fuller version —
it gives the commands, explains the `HEAD` label and the underline, and
prints the expected eight-page face table. Check 2 adds nothing but a second
number.

Note for the reader of the roadmap: **`docs/ROADMAP.md` F4 says the cheap
version of the fold check is `README` "Check 0". It is not in the README** —
`grep -n "Check 0" README.md` returns nothing. It is GUIDE §5.

### No test touches any of this

`tests/test_docs_coverage.py` checks that every module has a `docs/api/*.rst`
page; it says nothing about `README.md`, `CHANGELOG.md` or `docs/GUIDE.md`.
`tests/test_spec_residue.py` checks acceptance criteria against behaviour,
not prose. §4 adds the first two mechanical checks over these files.

## 3. Change

### Ordering note, read first

**D3 and D4 describe behaviour that three other specs change.** Run this pass
*after* the milestone, and check which of these has landed before writing
either edit:

| If landed | Then |
|---|---|
| **B15** (push the resolved printer profile to panel and preview) and/or **N2** (pre-fill the imageable area from the driver) | D4's replacement is wrong — the red line *has* become the printer's real border. Restore the original sentences and add "when the driver reports one; otherwise a 0.25in generic default." |
| **B16** or **F1** (profile picker in the print dialog) | D3's replacement is wrong — the app *can* reach both presets. Restore the original paragraph and drop the added sentence. |

Everything else in this file is independent of the code and can be done at
any time.

---

### Edit 1 — D1: the test-suite count (`README.md`)

Replace `README.md:392-394`:

```
The test suite is **608 passing, 17 skipped** — verified by running
`python -m pytest -q` at the repository root. The skips are golden-fixture
comparisons whose fixture is not committed.
```

with:

```
The test suite is **1,509 passing, 22 skipped** at `08e7f49` — verified by
running `python -m pytest -q` at the repository root (on Linux, with
`QT_QPA_PLATFORM=offscreen`). Fourteen of the skips are packaging-audit tests
that need a built bundle in `dist/`, four are Windows-only path cases, three
are the golden-fixture comparison whose 30 MB fixture is not committed, and
one needs `psutil`.
```

**Measure before you write.** Run the command and use the numbers it prints,
not the ones above: every spec in this directory adds tests. Keep the
commit hash of whatever you measured — a count with no commit beside it is
the sentence being replaced.

If R0.6 has landed, add one more sentence:

```
GitHub Actions runs the same command on Linux under CPython 3.12 and 3.14 on
every push, so this number is checked rather than remembered.
```

### Edit 2 — D2a: the GUIDE's crop claim (`docs/GUIDE.md`)

Replace `docs/GUIDE.md:310-311`:

```
There is no interactive crop editor in the desktop app; it has no crop controls
at all. This is a picture you look at, then a `--crop` you type.
```

with:

```
The desktop app has the same numbers on its **Crop & trim** tab — eight spin
boxes, odd and even pages separately, and a **Measure crop from the ink**
button that does what `--auto-crop` does. What it does not have is an
interactive editor: there is no picture in the app to drag a rectangle on.
So this is still a composite you look at, then numbers you type — into the
tab or into a `--crop`, whichever you are already in.
```

### Edit 3 — D2b: the gaps document (`docs/research/2026-08-05-competitive-gaps.md`)

Replace lines 66-68:

```
  overlay. The overlay is a written image, not an interactive in-app
  editor -- the desktop app has no crop controls at all, so an in-app
  preview would mean building those first.
```

with:

```
  overlay. The overlay is a written image, not an interactive in-app
  editor -- when this was written the desktop app had no crop controls at
  all, so an in-app preview would have meant building those first. The
  controls shipped 2026-09-04 (`fa7d888`, the Crop & trim tab); showing the
  composite inside the app is the part still outstanding.
```

Past tense plus a dated correction, because this file is a research record
and rewriting it into the present would erase the reasoning it exists to
hold.

**Do not touch `docs/decisions.md:530`**, which carries the same phrase. The
decision log is append-only.

### Edit 4 — D3: two presets, one reachable (`docs/GUIDE.md`)

Insert one sentence into `docs/GUIDE.md:521-527`, after *"...a face-up
printer that keeps its order."* and before *"Profiles are persisted..."*:

```
Today only the **first** of the two is reachable from the desktop app: it
resolves `generic_face_down_reversed` and offers no way to choose the other.
A face-up printer's owner needs the CLI's `--profile generic_face_up_in_order`,
or a hand-written profile file saved under the printer's own name, which the
app will then find.
```

Change nothing else in that paragraph.

### Edit 5 — D4a: the red guide, in the GUIDE (`docs/GUIDE.md`)

Replace `docs/GUIDE.md:174-177`:

```
- **Solid red — the imageable area.** Your printer's hardware limit; the
  border it physically cannot mark, typically 0.16–0.25 inches. Deckle cannot
  infer this from the imposition, because the imposer never sees the printer;
  "Use printer margins" in the app bridges that deliberately.
```

with:

```
- **Solid red — the imageable area.** The border a printer cannot mark,
  typically 0.16–0.25 inches. **Today this is a generic 0.25in default, not
  a measurement of your printer.** Deckle does not yet read the printable
  rectangle from the driver, and nothing pushes a calibrated profile to the
  preview, so treat the red line as a conservative guide rather than your
  machine's real limit — and check a proof sheet before trusting a margin
  that sits close to it. "Use printer margins" in the app applies that same
  0.25in.
```

### Edit 6 — D4b: the red guide, in the README (`README.md`)

Replace `README.md:331-335`:

```
- **Two guides, drawn distinctly.** Solid red is the printer's *imageable
  area* — a hardware limit, the border your printer physically cannot mark.
  Dashed blue is your *content box* — your margins. They coincide only by
  coincidence, and showing one while the user is asking about the other is how
  a preview lies.
```

with:

```
- **Two guides, drawn distinctly.** Solid red is the *imageable area* — the
  border a printer cannot mark, drawn today from a generic 0.25in default
  rather than from your driver. Dashed blue is your *content box* — your
  margins. They coincide only by coincidence, and showing one while the user
  is asking about the other is how a preview lies.
```

### Edit 7 — D5: the CLI reference (`docs/GUIDE.md` §8)

Four changes inside §8.

**7a.** In the Commands table (`docs/GUIDE.md:736-744`), after the
`schedule` row, add:

```
| `crop-preview SOURCE -o OUT.png` | Write a composite of every page with a proposed crop drawn on it, to look at before you commit to the numbers. `--parity odd`/`--parity even` for a scan whose gutter alternates; `--dpi` sets the rasterisation resolution (default `72`). |
| `dummy -o OUT.pdf` | Write a numbered document whose only content is its own page order, for checking how an imposition folds on scrap. `--pages N` (default `16`), `--page-size WxH` (default `letter`). Takes no `SOURCE`. |
```

**7b.** Replace the paragraph at `docs/GUIDE.md:746-748`:

```
`SOURCE` is a PDF file **or a directory of images**. Image folders are ordered
naturally by filename (`page2` before `page10`), EXIF orientation is honoured,
DPI is inferred, and images are embedded losslessly.
```

with:

```
`SOURCE` is a PDF file, **a directory of images**, or **a `.deckle` project
you saved earlier**. Image folders are ordered naturally by filename (`page2`
before `page10`), EXIF orientation is honoured, DPI is inferred, and images
are embedded losslessly.

A `.deckle` carries its own layout, and **that layout wins**: it is the one
you set up, previewed and saved, and silently overriding it from flag defaults
would make `deckle export project.deckle` produce a different book from the
one the project describes. Layout flags typed beside a project are reported as
ignored on stderr rather than quietly dropped — and rather than applied, which
would be worse. `--sheets`, `--pass`, `--profile` and `--printer` are not
layout, and are honoured. (`dummy` is the exception to all of this: it has no
`SOURCE`.)
```

**7c.** In the Layout options table (`docs/GUIDE.md:754-766`), after the
`--paper-thickness` row and before `--sewing-stations`, add:

```
| `--paper-weight WEIGHT` | unset | What the ream wrapper says — `80gsm`, or `24lb` with `--paper-grade`. An alternative to `--paper-thickness` for anyone without calipers; giving both is refused rather than resolved. |
| `--paper-type {bulky,coated,copier,laser,offset}` | `offset` | How bulky the stock is, which is what separates two papers of the same weight. |
| `--paper-grade {bond,cover,index,tag,text}` | unset | Which basis size a pound weight is quoted against. Required with a `lb` `--paper-weight`: 20lb is 75gsm as bond and 54gsm as cover. |
| `--signatures N,N,N` | unset | Each gathering's sheet count — `10,10,8` — instead of one uniform `--sheets-per-signature`. Must add up to the document's sheet count. |
| `--crop L,B,R,T` | none | Remove space from every source page before imposing: insets from left, bottom, right and top. |
| `--crop-even L,B,R,T` | none | A different crop for even-numbered pages, for a scan whose gutter swaps sides every leaf. Without it, `--crop` applies to the whole document. |
| `--auto-crop` | off | Measure the crop from where the ink actually is. Odd and even pages are measured separately, and the values found are printed so you can pin them with `--crop`. |
| `--auto-crop-margin LENGTH` | `0` | Keep this much back from every edge `--auto-crop` found, for descenders and hairline rules a low-dpi scan can miss. |
| `--trim LENGTH` | `0` | Draw cut lines this far in from head, tail and fore-edge, where the block is trimmed square after sewing. The spine is never cut. |
```

**7d.** After the "Lengths accept..." paragraph (`docs/GUIDE.md:768-769`) and
before the "**Not available from the CLI:**" paragraph, insert a new
subsection:

```
### `export` only

| Option | Default | Notes |
|---|---|---|
| `--sheets SPEC` | every sheet | Export only these, counting from 0 — `0`, `2,0`, `0,2-4`. Print sheet 0 on its own to proof a job before committing the stack. |
| `--rule` | off | Draw a ruler of known length on every sheet, to check whether the printer scaled the page. Use it on a proof, not on the job. |
| `--pass {front,back}` | both faces | Write one manual-duplex pass instead of both: every front, or every back in the order your printer's reload behaviour demands. Requires `--profile`. |
| `--profile NAME` | none | The printer profile describing your reload behaviour: one saved under the printer's name, or a built-in — `generic_face_down_reversed` or `generic_face_up_in_order`. |
| `--back-offset X,Y` | the profile's | Move back faces by X,Y so they land behind their fronts — `3,-2`, or `0.5mm,-1mm`. Overrides the value stored in the profile. Corrects a constant offset only, not skew or scale. |

`impose` additionally takes `--printer NAME`, to record a printer with the
project.
```

Leave the "**Not available from the CLI:**" paragraph exactly as it is. It is
still true, and it is F5.

### Edit 8 — D6: three shipped features the GUIDE never mentions

Replace the whole of `docs/GUIDE.md:102-108`, the `### Caliper` subsection:

```
### Caliper

`--paper-thickness 0.004in` (or `0.1mm`, or whatever you measure). It affects
**no placement Deckle emits** — not one. It exists purely so the binding
schedule can predict fore-edge creep and spine thickness, both of which you
need before the block is finished and cannot measure yet.
```

with:

```
### Thickness, and how many sheets a gathering should hold

Deckle needs one number about your paper: the caliper of a single sheet. It
affects **no placement Deckle emits** — not one. It exists so the binding
schedule can predict fore-edge creep and spine thickness, both of which you
need before the block is finished and cannot measure yet.

Three ways to give it, in the order most people can:

- **Pick the stock.** In the app, the paper dropdown on Page setup —
  `80gsm copier`, `100gsm offset`, `160gsm card` and two others.
- **Type the weight off the ream wrapper.** `--paper-weight 80gsm`, or
  `--paper-weight 24lb --paper-grade bond`. A US basis weight means nothing
  without the grade: 20lb is 75gsm as bond and 54gsm as cover, so Deckle
  refuses a pound weight without one rather than guessing wrong by half.
  `--paper-type` says how bulky the stock is, which is what separates two
  papers of the same weight; it defaults to `offset`.
- **Measure it.** `--paper-thickness 0.004in`, or `0.1mm`, or whatever your
  calipers say.

**A derived caliper is an estimate.** Bulk varies about 10% between
manufacturers, which is why the schedule reports spine width as a range
rather than a number, and why this value never moves a page.

Once Deckle knows the thickness it will suggest a gathering size: how many
sheets to nest, and **which constraint decided it**. The remedy differs —
`creep` means plan a fore-edge trim, `fold` means the paper is thick and the
gathering has to be smaller. 80gsm with a quarter-inch trim comes out at 8
sheets, a 32-page gathering, which is the standard trade signature. In the
app the suggestion appears under *Sheets per signature* with an **Apply**
button, and disappears once you have taken it.
```

Then add a new subsection at the end of §4 (Path A), immediately before the
`## 5 · Path B` heading at `docs/GUIDE.md:343`:

```
### If Deckle stops unexpectedly

Deckle autosaves beside the project on every edit. Reopen a project it did
not close cleanly and it asks whether to recover the unsaved changes.

Detection is by modification time, not by whether an autosave exists — a
clean quit flushes one too, so "does the file exist" would prompt on every
single open, and a prompt that always fires is one people learn to dismiss
without reading. Saving makes the project newer than its autosave, which is
what keeps it quiet.

Declining deletes the autosave. That is deliberate: leaving it would bring
the prompt back on every subsequent open of the same project.
```

Placement is a judgement the spec is making for you: recovery is not
signature-specific, and §4 is where a reader working through their first job
already is.

### Edit 9 — D7: the module list (`README.md`)

Replace `README.md:409-412`:

```
deckle/core/    models, loader, layout, export, render, printing, profiles,
                marks, schedule, signatures, print_session, project_io,
                outputs, session_log, diagnostics
```

with:

```
deckle/core/    models, loader, layout, export, render, printing, profiles,
                marks, schedule, signatures, print_session, project_io,
                outputs, paper, paths, locate, schema, recent, dummy,
                session_log, diagnostics
```

Twenty-one names, matching `ls deckle/core/*.py`. If M6 has landed there is a
twenty-second, `pdfium_lock`; add it after `render`. Re-run the check in §5
rather than trusting this list.

### Edit 10 — D8: the CHANGELOG's Added entry (`CHANGELOG.md`)

Replace `CHANGELOG.md:36-37`:

```
- **CLI** — `impose`, `export`, `info`, `--version`. Imports only
  `deckle.core`, so it runs headlessly.
```

with:

```
- **CLI** — `impose`, `export`, `info`, `schedule`, `crop-preview` and
  `dummy`, plus `--version`. Imports only `deckle.core`, so it runs
  headlessly. `export` additionally takes `--sheets` (a subset, for proofs),
  `--rule` (a printed ruler to check the printer's scaling), and
  `--pass`/`--profile`/`--back-offset` for manual duplex. Every command
  accepts a saved `.deckle` as its source, whose stored layout wins over any
  layout flag typed beside it.
```

Do not touch the Fixed section. `crop-preview` and `dummy` keep their bug
entries at lines 168 and 178; those are true and dated.

### Edit 11 — D10: the main-window docstring (`deckle/app/main.py`)

Replace `deckle/app/main.py:3-6`:

```
Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
```

with:

```
Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once, on a background worker, at
window construction; when it comes back empty the print action is disabled
with an explanatory status message instead of opening an empty/broken print
dialog or raising. There is no printer menu and no re-query -- a printer
plugged in after launch is not seen until Deckle restarts.
```

If **B30** (re-query printers) has landed, this is wrong: describe whatever
B30 actually built instead.

### Edit 12 — D11: three checks that are two (`docs/GUIDE.md`)

Delete `docs/GUIDE.md:420-421` entirely:

```
**Check 2 — fold a dummy.** Five minutes, and it is the one that actually
settles it. Print one signature onto scrap, fold it, read it.
```

Then renumber the two that remain, so they match the preamble's *"Two ways
to close the question yourself, both cheap. Do one."*

`docs/GUIDE.md:368`:

```
**Check 0 — fold a numbered dummy.** Five minutes, and the only one of these
that answers the question outright.
```

becomes:

```
**Check 1 — fold a numbered dummy.** Five minutes, and the only one of the
two that answers the question outright.
```

`docs/GUIDE.md:399`:

```
**Check 1 — read the schedule against a manual.** Thirty seconds.
```

becomes:

```
**Check 2 — read the schedule against a manual.** Thirty seconds, and it
checks the arithmetic rather than the fold.
```

Check the blank lines around the deletion: `### Step by step` at
`docs/GUIDE.md:423` must keep exactly one blank line above it.

### D9 — no edit

`tests/fixtures/README.md`'s "checked-in" sentence is made **true** by R0.1,
which un-ignores and commits `tests/fixtures/sample.pdf`. Do not edit it here
and do not weaken it to "generated". If R0.1 has not landed when this pass
runs, leave D9 open and say so; editing the README to describe the broken
state would remove the pressure to fix it.

## 4. Tests

Prose is not testable, and eleven hand-checked edits is how prose drifts back.
Two of these eleven are mechanically checkable, and those two are the ones
that will drift again first, because they are lists of things the code keeps
adding to.

New file: **`tests/test_docs_are_current.py`**.

```python
"""Two documentation claims that are lists of things the code owns.

The README's module list had drifted by six modules and the GUIDE's CLI
reference by two commands and fourteen options, because both are
inventories of something that grows. Everything else in the D pass is a
sentence somebody has to read; these two are countable, so they are
counted.
"""
```

| Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_the_readme_lists_every_core_module` | read `README.md`; take the fenced block that starts `deckle/core/`; compare against `{p.stem for p in Path("deckle/core").glob("*.py")} - {"__init__"}` | every module name appears in the block | `AssertionError: modules missing from README's architecture list: ['dummy', 'locate', 'paper', 'paths', 'recent', 'schema']` |
| `test_the_guide_documents_every_cli_command` | build the parser with `deckle.cli.build_parser()`, walk its subparsers action for `choices`, read `docs/GUIDE.md` | every subcommand name appears in the GUIDE's `## 8 · CLI reference` section | `AssertionError: CLI commands absent from GUIDE section 8: ['crop-preview', 'dummy']` |
| `test_the_guide_documents_every_cli_option` | for each subparser, collect every `option_strings` entry beginning `--` except `--help`; read the same section | every option string appears, **or** appears in the section's explicit "Not available from the CLI" paragraph | `AssertionError: CLI options absent from GUIDE section 8: ['--auto-crop', '--auto-crop-margin', '--back-offset', '--crop', '--crop-even', '--paper-grade', '--paper-type', '--paper-weight', '--pass', '--profile', '--rule', '--sheets', '--signatures', '--trim']` |

Notes for whoever writes them:

- Slice §8 by splitting `docs/GUIDE.md` on `"\n## 8 · CLI reference"` and then
  on `"\n## 9 "`. The `·` is U+00B7; read the file with
  `encoding="utf-8"`.
- Do **not** assert on `--version`, `--landscape` or `-o`/`--output`: they are
  documented outside the option tables (Global, Commands) and a test that
  demands table membership rather than *presence* would make the GUIDE worse
  to write.
- No test for the test-suite count in D1. A test asserting the README's own
  number would either be a tautology (it re-runs the suite) or a maintenance
  trap. CI (R0.6) is what keeps that honest; the README sentence carries the
  commit it was measured at so a reader can tell how stale it is.

Confirm all three fail before making the edits — the third one is the whole
of D5 restated as an assertion, and if it passes on the unfixed tree the
section-slicing is wrong.

## 5. Acceptance

| Check | Command |
|---|---|
| The old test count is gone | `! grep -n '608 passing' README.md` |
| The new count names a commit | `grep -nE '\*\*[0-9,]+ passing, [0-9]+ skipped\*\* at' README.md` |
| No "no crop controls at all" outside the decision log | `! grep -rn 'no crop controls at all' README.md docs/GUIDE.md docs/research` |
| The GUIDE says which preset the app reaches | `grep -n 'generic_face_up_in_order' docs/GUIDE.md` |
| Neither file still calls the red line a hardware limit | `! grep -rn 'hardware limit' README.md docs/GUIDE.md` |
| The GUIDE's CLI reference names both missing commands | `grep -n 'crop-preview SOURCE' docs/GUIDE.md && grep -n 'dummy -o OUT.pdf' docs/GUIDE.md` |
| …and the paper-weight options | `grep -n 'paper-weight WEIGHT' docs/GUIDE.md` |
| …and the export-only options | `grep -n 'export. only' docs/GUIDE.md && grep -n 'back-offset X,Y' docs/GUIDE.md` |
| …and that a `.deckle` is a SOURCE | `grep -n 'a .deckle. project' docs/GUIDE.md` (the sentence wraps, so grep a fragment that stays on one line) |
| The GUIDE covers paper by weight | `grep -ni gsm docs/GUIDE.md` |
| …and crash recovery | `grep -ni autosave docs/GUIDE.md` |
| The README lists all six missing modules | `grep -n 'paper, paths, locate, schema, recent, dummy' README.md` |
| The CHANGELOG names all six commands | `grep -n 'crop-preview' CHANGELOG.md` returns line 168 **and** a line in the Added section — check by eye, or `.venv/bin/python -c "import sys;t=open('CHANGELOG.md',encoding='utf-8').read();a=t.split('### Added',1)[1].split('### Fixed',1)[0];sys.exit(0 if 'crop-preview' in a and 'dummy' in a and 'schedule' in a else 1)"` |
| The main-window docstring no longer invents a menu | `! grep -n 'printer menu is opened' deckle/app/main.py` |
| There is no third check in GUIDE §5 | `! grep -n 'Check 0' docs/GUIDE.md` and `test "$(grep -c 'Check 2 — ' docs/GUIDE.md)" = 1` |
| The fixture README was not weakened | `grep -n 'checked-in PDF' tests/fixtures/README.md` |
| The decision log was not rewritten | `test -z "$(git diff --name-only -- docs/decisions.md)"` before staging the entry from §7 |
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_docs_are_current.py -q --no-header -p no:cacheprovider` |
| No code changed except one docstring | `git diff --stat -- deckle` shows only `deckle/app/main.py` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` The GUIDE still reads as one document | Read §2 (Choosing paper), §5's two checks, and §8 end to end. Edits 8 and 12 change the shape of a section, not just a sentence; the surrounding prose has to still lead into them. |

Verified on the current tree: `grep -n '608 passing' README.md` matches line
392; `grep -rn 'no crop controls at all' README.md docs/GUIDE.md docs/research`
matches `docs/GUIDE.md:310` and
`docs/research/2026-08-05-competitive-gaps.md:67`;
`grep -rn 'hardware limit' README.md docs/GUIDE.md` matches `README.md:332`
and `docs/GUIDE.md:174`; `grep -ni gsm docs/GUIDE.md` returns nothing;
`grep -n 'Check 0' docs/GUIDE.md` matches line 368.

## 6. Out of scope

- **`docs/decisions.md`.** Append-only. It contains the same "no crop
  controls at all" phrase at line 530 and several other statements that were
  true when written. Never edit a past entry.
- **`docs/plans/`, `docs/specs/`, `docs/converge/`, `docs/research/` beyond
  edit 3.** These are dated artefacts. Edit 3 is included only because that
  one bullet is written as a *current status list*, and it gets a dated
  correction rather than a rewrite.
- **The features the sentences describe.** D3 is documentation for B16/F1,
  D4 for B15/N2, D10 for B30. Writing the honest sentence is this spec; making
  the sentence obsolete is theirs.
- **D9.** Fixed by R0.1. See §3.
- **N11** (show the crop composite in the app) — edit 2 says it does not
  exist, which is true, and stops there.
- **F5.** The GUIDE's "Not available from the CLI" paragraph is correct and
  stays.
- Do not restructure `docs/GUIDE.md`'s numbering, add a table of contents, or
  rename sections. Eleven edits, no reorganisation.
- Do not add a README badge; that is R0.6's if anyone's.

## 7. decisions.md entry

Ten of these twelve edits touch only `.md` files, which the pre-commit hook
ignores. **Edit 11 touches `deckle/app/main.py`**, so the hook will fire.
Write the entry regardless: a documentation pass that leaves no trace in the
log is one nobody can tell happened.

```
## 2026-09-05 — Eleven documented claims that the program had stopped honouring
- Symptom: The README advertised "608 passing, 17 skipped" against a suite of 1,509. The GUIDE told readers the desktop app has "no crop controls at all" -- false since `fa7d888` -- and that the solid red preview guide is "your printer's hardware limit", when it is a fixed 0.25in generic default that no driver has ever been asked about. Its CLI reference omitted two of six commands, fourteen options, and the fact that a saved `.deckle` is accepted as SOURCE everywhere. It said Deckle has two built-in printer presets without saying the app can only reach one. It documented none of paper-by-weight, the gathering-size suggestion or crash recovery, all shipped. The README's module list was six modules short. The CHANGELOG announced four of six CLI commands, with `crop-preview` and `dummy` appearing only under Fixed. `main.py`'s docstring described a printer menu that does not exist. And GUIDE §5 offered "two ways, do one" under three headings, two of which were the same check.
- Fix: Eleven edits, quoted and replaced. Plus `tests/test_docs_are_current.py`, which asserts the two claims that are inventories rather than sentences: every module under `deckle/core/` appears in the README's architecture block, and every CLI command and option appears in GUIDE section 8, read from `build_parser()`.
- Surfaces: Two of the eleven are worth paper. A reader who believes the red line is their printer's real limit sets a margin against it and prints into the dead border; a reader who believes there are no crop controls drops to a terminal for a job the Crop & trim tab has done for weeks. Three of the sentences are documentation *for* open specs -- B15/N2 make the red-guide sentence obsolete, B16/F1 the preset sentence, B30 the docstring -- so each replacement names the spec that will make it wrong again, in the right direction. `docs/decisions.md` carries the same crop phrase at line 530 and was left alone: the log is append-only. The competitive-gaps bullet got a dated past-tense correction rather than a rewrite, because it is a research record.
- Watch: **The two claims that had drifted furthest were both lists**, and lists are the part of documentation the code keeps adding to without telling anyone. Those are the two the new test covers; the rest are prose and were checked by reading. The test-suite count deliberately gets no test -- one would either re-run the suite to compare against a sentence, or pin a number -- and instead now carries the commit it was measured at, so a reader can see how stale it is. CI is what actually keeps it honest.
- Commit: <fill in>
```

## 8. Traps

- **Measure D1, do not copy it.** Every other spec in this directory adds
  tests. `1,509 passed, 22 skipped` is this tree at `08e7f49` with R0.3 and
  R0.4 fixed and nothing else. Run the suite and write what it says, with the
  hash beside it.
- **`docs/decisions.md` is append-only.** It contains phrases this pass is
  correcting elsewhere. Leave every past entry exactly as it is.
- **Check what has landed before writing D3, D4 and D10.** §3's table. Three
  other specs make those sentences wrong in the other direction, and a
  correction that arrives after the fix is worse than the original error,
  because it reads as freshly verified.
- **`docs/GUIDE.md` uses `·` (U+00B7) in its `##` headings** and en dashes and
  em dashes throughout. Match the surrounding punctuation; do not normalise to
  ASCII, and read the file as UTF-8.
- **The GUIDE's tables have a leading and trailing `|`** and the option tables
  are three columns (`Option | Default | Notes`). The Global and `run.bat`
  tables are two columns with an empty header. Copy the shape of the table you
  are adding to.
- **Edit 12 renumbers, and the numbers appear only in those three lines** —
  `grep -n "Check " docs/GUIDE.md` after editing and confirm you have exactly
  `Check 1` and `Check 2`, once each.
- **`docs/ROADMAP.md` F4 credits the fold check to the README.** It is in the
  GUIDE. Do not go looking for a "Check 0" in the README to fix.
- **Do not weaken `tests/fixtures/README.md`.** See §3, D9.
- `python -m deckle` launches the GUI. The only reason to run anything here is
  `python -m deckle.cli <cmd> --help`, which is how the D5 tables were built
  and how they should be re-checked.
</content>
