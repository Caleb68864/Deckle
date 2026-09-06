# F12 — Four deferred open questions, each small, none urgent

**Roadmap item:** `docs/ROADMAP.md` F12
**Depends on:** §B (per-orientation imageable area) wants **F4**'s measured
numbers to justify itself and **F1** to edit them. §D (`binding_edge` →
`reading_direction`) wants **F7**/**F8** to have landed, because the case for
splitting the field is strongest once three schemes overload it.
§A and §C depend on nothing.
**Blocks:** —
**Size:** S–M each; **do not do all four in one commit.** Four sub-sections,
four commits, four `docs/decisions.md` entries.
**Decision needed first:** §B and §D are both recorded as *human-approval*
items in the v2 design (open questions 2 and 3). Get the owner's answer
before implementing either; §A and §C need no decision.

---

## 1. Context

The signatures-v2 design closes with six open questions. Four are still open
and none is urgent; the roadmap bundles them as F12 because each is small and
they share one property — every one is a change nobody is blocked on, so each
can be done or dropped without disturbing the others.

- **§A. Per-signature export files.** Design open question 6: *"Does
  per-signature export want its own files (`sig-01_side1.pdf`), matching
  Bookbinder JS's desk procedure, or is in-app per-signature printing
  enough? **Changes:** a CLI/UI affordance over `export(..., sheets=...)`;
  no core change."*
- **§B. Per-orientation imageable area.** Design open question 3: *"Is the
  imageable area orientation-dependent on the target printer? … **Changes:**
  `PrinterProfile` would need `imageable_area_pt` **per orientation**, which
  is a profile-shape change and therefore a human-approval item."*
  `LayoutWarning.kind` already declares `landscape_imageable_unverified`
  and **nothing emits it** (B31).
- **§C. Top- and bottom-edge binding.** Excluded from v2 (*"Still deferred
  from the MVP (red-team P-5)"*) and rejected in the gaps research as
  *"low value, note as trivially cheap"*:

  > Deckle has per-page rotation and a global landscape policy, so this is
  > achievable manually today. … **If the layout panel ever grows a rotation
  > preset dropdown, add these two entries then — it is a few lines. Do not
  > schedule it on its own.**

- **§D. `binding_edge` → `reading_direction`.** Design open question 2:
  *"Does `binding_edge` overloading survive contact with use? Under folio it
  means reading direction, not gutter side. **Changes:** if it confuses in
  practice, split into `reading_direction` and deprecate `binding_edge` for
  folio — a `LayoutSettings` field change and a `.deckle` migration (**the
  first one this project would have**, so the `version` integer finally
  earns its keep)."*

## 2. Current code

`deckle/core/project_io.py:38` — the version that has never moved:

```python
FORMAT_VERSION = 1
```

`deckle/core/project_io.py:346-393` — the loader's whole tolerance story,
which §D's migration hooks into:

```python
def _layout_from_dict(data: dict[str, Any]) -> LayoutSettings:
    """Build ``LayoutSettings`` from stored JSON, tolerating field drift.

    Unknown keys are dropped with a warning rather than raising, and missing
    keys fall back to the dataclass defaults. That makes the format tolerant
    in both directions: a file written by an older build (missing fields) and
    one written by a newer build (extra fields) both open, which is what the
    ``version`` integer was reserved for.

    Tolerant about *keys*, strict about *values* ...
    """
    known = {f.name for f in dataclasses.fields(LayoutSettings)}
    kwargs = {k: v for k, v in data.items() if k in known}
    unknown = sorted(set(data) - known)
    if unknown:
        warnings.warn(
            "ignoring layout fields this build does not recognise: "
            + ", ".join(unknown),
            UnknownLayoutFieldsWarning,
            stacklevel=2,
        )
    _check_layout_values(kwargs)
    ...
    return LayoutSettings(**kwargs)
```

`deckle/core/project_io.py:418-423` — where the version is written:

```python
    payload = {
        "version": FORMAT_VERSION,
        "pages": [_page_to_dict(p) for p in project.pages],
        "layout": _layout_to_dict(project.layout),
        "printer": project.printer,
    }
```

**B28 records that this `version` is written and never read for `.deckle`.**
§D is what makes it load-bearing.

`deckle/core/models.py:325` and `:299-302` — the overloaded field:

```python
    binding_edge: Literal["left", "right"]
```

```python
    :ivar binding_edge: which edge the book is bound on. Under
        ``fold_scheme="folio"`` this changes meaning to *reading
        direction* -- see ``deckle.core.layout.SaddleStitchStrategy``.
```

`deckle/core/layout.py:838-846` — the overloading, stated at length:

```python
    The spine of a leaf is a function of **which cell it sits in**, not of
    output-page parity: ... Consequently ``settings.binding_edge``
    changes meaning under folio: it no longer selects which side of a page
    gets the gutter, it selects **reading direction** -- ``"left"`` is
    left-bound / LTR, ``"right"`` is right-bound / RTL -- which mirrors
    which folio cell (not which position in the print-order pair) each
    source slot lands in.
```

`deckle/core/profiles.py:49` — the field §B splits:

```python
    imageable_area_pt: tuple[float, float, float, float]
```

`deckle/core/models.py:245` — the declared-and-never-emitted warning kind
§B makes real:

```python
        "landscape_imageable_unverified",
```

`deckle/cli.py:1253-1263` — the flag §A sits beside:

```python
    export_parser.add_argument(
        "--sheets",
        type=_parse_sheet_selection,
        default=None,
        metavar="SPEC",
        help=(
            "export only these sheets, counting from 0 -- e.g. 0, 2,0 or "
            "0,2-4. Print sheet 0 on its own to proof a job before "
            "committing the stack"
        ),
    )
```

### Call sites

```
$ grep -rn "binding_edge" deckle/ | wc -l    # run it; ~20, across 6 files
$ grep -rn "imageable_area_pt" deckle/ tests/
deckle/core/profiles.py:49, 154 (comment), 188, 198
deckle/app/views/layout_panel.py:165-186 (imageable_inset_pt), 1698 (approx)
deckle/app/views/preview_view.py:44-66 (imageable_rect_pt)
deckle/app/backend.py  (paint path)
tests/  (several)
$ grep -rn "landscape_imageable_unverified" deckle/ tests/
deckle/core/models.py:245
```

Run all three before starting; each sub-section's change list is one of them.

### Existing tests

`tests/test_project_io.py`, `tests/test_project_file_shape.py`,
`tests/test_settings_roundtrip.py`, `tests/test_layout_field_types.py`
(§D); `tests/test_printing.py`, `tests/test_preview_paint.py`,
`tests/test_print_painting.py` (§B); `tests/test_cli_sheets.py`,
`tests/test_export.py` (§A); `tests/test_layout.py`,
`tests/test_spine_side_precedence.py` (§C).

---

## 3. Change

Four independent sub-sections. Each states its own scope, steps, tests and
acceptance. **One commit each.**

---

### §A — Per-signature export to separate files

**Size:** S. **Depends on:** nothing.

#### Behaviour

A new `deckle export` flag:

```python
    export_parser.add_argument(
        "--signature",
        type=int,
        default=None,
        metavar="N",
        help=(
            "export one signature, counting from 1 as the binding schedule "
            "does. With --split, write every signature to its own file "
            "instead. Requires --fold-scheme folio"
        ),
    )
    export_parser.add_argument(
        "--split",
        action="store_true",
        help=(
            "write one file per signature beside -o, named "
            "<output>-sig-01.pdf, <output>-sig-02.pdf and so on -- one "
            "gathering per file, so a stack of files matches the stack of "
            "gatherings on the bench"
        ),
    )
```

**1-based, unlike `--sheets`.** `--sheets` is 0-based *because* it matches
`Sheet.index`, the layout warnings and `plan.signatures`' own index; the
schedule prints `SIGNATURE 1` (`schedule.py:234`, `index=signature.index + 1`),
and that is the number on the paper in front of the binder. Two numbering
conventions in one command is bad; a flag disagreeing with the printed
artefact it names is worse. **Say this in the help and in the decisions
entry.**

Filenames: `Path(output).with_name(f"{stem}-sig-{n:02d}{suffix}")`. Zero-padded
to two digits so `ls` sorts them, and the working case in this project's own
docs is 17 signatures.

Refusals, each before any work:

- `--signature` with no signatures in the plan:
  `error: this document has no signatures -- add --fold-scheme folio, or use --sheets to pick sheets.`
- `--signature N` out of range:
  `error: no signature N in this document -- it has M, numbered 1 to M.`
  (Mirrors `_report_missing_sheets`'s shape.)
- `--signature` **and** `--split` together:
  `error: give either --signature or --split, not both: one writes a chosen gathering, the other writes all of them.`
  (Mirrors `_paper_thickness_from_args`'s refusal to resolve two ways of
  saying one thing.)
- `--split` with `-o` naming an existing directory: rejected by the existing
  `output_path_problem`.

Under `--split`, `_report_output_problem` runs once per generated path
**before** the first export, so a run that would fail on file 9 of 17 fails
before file 1 is written.

#### Steps

1. `deckle/cli.py`: the two `add_argument` calls above.
2. `deckle/cli.py`, a helper beside `_report_missing_sheets`:

```python
def _signature_sheets(plan, number: int) -> list[int] | None:
    """The sheet indices of 1-based signature ``number``, or ``None``.

    ``None`` means the request is impossible and a message naming the
    alternatives has already been printed.
    """
```

3. `_cmd_export`: after `_emit_warnings`, resolve `--signature` into
   `selection` (it composes with `--sheets` by intersection — or refuse the
   combination; **refuse**, with
   `error: give either --sheets or --signature, not both.`), and branch to a
   `_export_split(plan, args)` loop for `--split`.
4. The split loop reuses `export_plan(...)` per signature with the same
   `side`/`rotate_180`/`back_offset_pt` the single-file path computes, and
   prints one `wrote <path>` line per file. **No new export code**; the
   design doc's own note is *"a CLI/UI affordance over
   `export(..., sheets=...)`; no core change."*
5. GUIDE §8's export options table.

Deliberately **no GUI affordance**: the print dialog already has a signature
selector, and F6 adds a sheets box. `--split` is for printing at a shop.

#### Tests — `tests/test_cli_signature_export.py`

- `test_exporting_one_signature_writes_only_its_sheets` — a 32-page folio at
  4 sheets/signature; `--signature 2` gives a PDF of 8 pages (4 sheets × 2
  faces).
- `test_signature_numbering_starts_at_one_like_the_schedule` —
  `--signature 1` and `--sheets 0-3` produce byte-identical page counts and
  the same first page.
- `test_a_signature_the_document_does_not_have_is_refused` — exit 1, stderr
  names the count, **no file written**.
- `test_signature_on_an_unfolded_document_is_refused`
- `test_signature_and_split_together_are_refused`
- `test_sheets_and_signature_together_are_refused`
- `test_split_writes_one_file_per_signature_zero_padded` — 17 signatures →
  `out-sig-01.pdf` … `out-sig-17.pdf`, and `sorted(glob)` is binding order.
- `test_split_checks_every_path_before_writing_any` — make
  `out-sig-03.pdf` a read-only file; nothing at all is written and exit is 1.
- `test_split_carries_the_pass_and_offset_flags` — with `--pass back
  --profile ...`, every file is a back pass and the registration line is
  printed once.

#### Acceptance

| Check | Command |
|---|---|
| Tests pass | `.venv/bin/python -m pytest -q tests/test_cli_signature_export.py` |
| It works end to end | `.venv/bin/python -m deckle.cli dummy -o /tmp/a.pdf --pages 32 --page-size 5.5x8.5in && .venv/bin/python -m deckle.cli export /tmp/a.pdf -o /tmp/a-out.pdf --fold-scheme folio --paper letter --landscape --split && ls /tmp/a-out-sig-0*.pdf` |
| No core change | `git diff --stat deckle/core/export.py \| grep -q "" ; test $? -ne 0` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

#### decisions.md entry

```
## 2026-09-05 — deckle export --signature and --split
- Symptom: v2 open question 6 -- printing one gathering from Deckle worked, but there was no way to hand a print shop one file per signature, which is Bookbinder JS's desk procedure and the shape a bench works in.
- Fix: `--signature N` and `--split` on `deckle export`, both pure affordances over `export(..., sheets=...)`; no change to `deckle/core/export.py`. `--split` checks every output path before writing any file, so a run that would fail on file 9 of 17 fails before file 1.
- Surfaces: `--signature` is 1-based where `--sheets` is 0-based. `--sheets` is 0-based because it matches `Sheet.index`, the layout warnings and `plan.signatures`' own index; signatures are 1-based because that is the number the binding schedule prints and therefore the number on the paper in front of the binder. A flag that disagrees with the artefact it names is worse than two conventions in one command, and the help text says which is which.
- Watch: `--signature` refuses to combine with `--sheets` and with `--split` rather than resolving a precedence, the same way `--paper-weight` refuses to combine with `--paper-thickness`.
- Commit: <fill in>
```

---

### §B — Per-orientation imageable area

**Size:** M. **Depends on:** F4 (the measurement), F1 (somewhere to edit it).
**Human-approval item** — do not implement without the owner's answer.

#### Behaviour

`PrinterProfile` gains one optional field:

```python
    landscape_imageable_area_pt: tuple[float, float, float, float] | None = None
    """The imageable area when the sheet feeds landscape, or ``None``.

    ``imageable_area_pt`` is measured with the paper fed portrait. Most
    printers have a different unprintable border on the leading edge, so a
    landscape feed -- which is what folio and French fold use -- does not
    simply transpose those numbers. ``None`` means "not measured", and every
    consumer then falls back to the portrait figures exactly as it did
    before this field existed, which is what keeps an uncalibrated printer
    behaving identically.

    Not a transposition and not a guess: the only way to fill this in is to
    print a landscape sheet and measure the four borders. See
    ``docs/verification/*-folio-dummy.md``, whose "Landscape imageable area"
    section is where those four numbers are recorded.
    """
```

and one selector, also on `PrinterProfile`:

```python
    def imageable_area_for(
        self, paper_pt: tuple[float, float]
    ) -> tuple[float, float, float, float]:
        """The imageable area for a sheet of this shape.

        Falls back to the portrait measurement when the landscape one was
        never taken -- the pre-existing behaviour, and the honest one.
        """
        landscape = paper_pt[0] > paper_pt[1]
        if landscape and self.landscape_imageable_area_pt is not None:
            return self.landscape_imageable_area_pt
        return self.imageable_area_pt
```

**Migration: none needed.** `PrinterProfile.load` already drops unknown keys
and defaults missing ones (`profiles.py:108-160`), and the field's default is
`None`. A profile written by a build with the field opens on one without it,
and vice versa. **`calibration_version` is not bumped** — the field's
presence is what says whether it was measured. Say this explicitly; it is the
whole reason the profile loader was made tolerant.

#### The warning that finally fires

`landscape_imageable_unverified` is declared in `LayoutWarning.kind` and
emitted by nothing (B31). Emit it from `grain_warning`'s neighbourhood in
`layout.py` — a new `landscape_imageable_warning(settings, profile)`:

- fires when the paper is landscape **and** the profile's
  `landscape_imageable_area_pt is None`,
- detail:
  `"this sheet feeds landscape and the printer profile's imageable area was measured portrait; the unprintable border on the leading edge is usually different. Measure it and set landscape_imageable_area_pt, or expect clipping near one edge."`

**Problem, and its resolution:** `layout.py` is given `LayoutSettings`, never
a `PrinterProfile`. Do **not** thread a profile into the imposer — that
couples pure layout arithmetic to printer state and breaks the strategy
signature the whole v2 seam rests on. Emit it instead from the two places
that *do* have both: `deckle/app/backend.py`'s paint path and
`deckle/cli.py`'s `_emit_warnings` when `--profile` was given. Keep it a
`LayoutWarning` so the existing surfaces render it.

#### Consumers to update

Every reader of `imageable_area_pt` becomes a call to
`profile.imageable_area_for(plan.paper_pt)`:

- `deckle/app/views/preview_view.py:44-66` `imageable_rect_pt` — needs the
  paper it already has.
- `deckle/app/views/layout_panel.py:165-186` `imageable_inset_pt` and the
  "Use printer margins" button.
- `deckle/app/backend.py`'s paint path.

**B15 overlaps here** — the preview's red guide uses `DEFAULT_PROFILE`, not
the resolved one, so §B makes the *right* number available to a consumer
that is reading the wrong profile. Do B15 first or accept that §B is
invisible in the preview until it lands. Say which in the commit.

#### Tests — `tests/test_imageable_orientation.py`

- `test_a_portrait_sheet_uses_the_portrait_measurement`
- `test_a_landscape_sheet_uses_the_landscape_measurement_when_it_exists`
- `test_a_landscape_sheet_falls_back_when_it_was_never_measured` — the
  behaviour of every profile written before this field.
- `test_a_square_sheet_counts_as_portrait` — `paper[0] > paper[1]` is
  strict, matching `layout_panel.paper_is_landscape`'s documented choice.
- `test_a_profile_without_the_field_still_loads` and
  `test_a_profile_with_the_field_survives_a_round_trip` — the tolerance the
  loader already promises, now exercised.
- `test_the_landscape_warning_fires_once_for_an_unmeasured_landscape_job`
- `test_the_landscape_warning_is_silent_once_measured`
- `test_every_declared_warning_kind_is_emitted_somewhere` — the general
  guard the roadmap's §5 asks for; `landscape_imageable_unverified` is the
  last one that was not.

#### Acceptance

| Check | Command |
|---|---|
| Tests pass | `.venv/bin/python -m pytest -q tests/test_imageable_orientation.py` |
| The declared kind is now emitted | `test "$(grep -rl 'landscape_imageable_unverified' deckle/ \| wc -l)" -ge 2` |
| Old profiles still load | `.venv/bin/python -m pytest -q tests/test_printing.py tests/test_config_store_durability.py` |
| Nothing threaded a profile into the imposer | `! grep -q "PrinterProfile" deckle/core/layout.py` |
| Core purity | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| [HUMAN] It matched the paper | The four numbers came from F4's record, not from transposing the portrait ones. |

#### decisions.md entry

```
## 2026-09-05 — Imageable area is per orientation, and the warning that said so finally fires
- Symptom: `PrinterProfile.imageable_area_pt` is measured with the paper fed portrait, and folio feeds landscape. Most printers have a different unprintable border on the leading edge, so the stored numbers were wrong for exactly the scheme that most needs them -- and `LayoutWarning.kind` had declared `landscape_imageable_unverified` since v2 with nothing emitting it, the declared-but-not-honoured shape the log has caught six times now.
- Fix: an optional `landscape_imageable_area_pt` and an `imageable_area_for(paper_pt)` selector on `PrinterProfile`, defaulting to `None` so an unmeasured printer behaves exactly as before. No migration: the profile loader has been tolerant in both directions since the back-offset fields landed, which is what that tolerance was for. The warning is emitted from the backend and the CLI, never from `layout.py` -- the imposer is not given a `PrinterProfile` and must not be, or the strategy seam stops being pure arithmetic.
- Surfaces: `calibration_version` is deliberately NOT bumped. The field's presence is what says whether the measurement was taken; a version number would be a second answer to the same question.
- Watch: this makes the right number available to consumers that (per B15) are still reading `DEFAULT_PROFILE` rather than the resolved one, so the preview's red guide will not show it until B15 lands.
- Commit: <fill in>
```

---

### §C — Top- and bottom-edge binding

**Size:** S. **Depends on:** nothing. **Do not schedule it on its own** —
the gaps research is explicit that it is worth doing *when the layout panel
grows a rotation preset control, and not before*. This sub-section exists so
that when someone does add one, the design is already written.

#### Behaviour

**Not** a `binding_edge` value. `binding_edge` selects which vertical edge
carries the gutter (and, under folio, reading direction); a top- or
bottom-bound book has its spine on a *horizontal* edge, which is a different
axis and would make every consumer of `binding_edge` handle four cases where
it handles two.

Instead: **a source-rotation preset**, exactly as the gaps research
describes it —

> Bookbinder JS `Source Manipulation` offers `odd pages 90° clockwise, even
> pages 90° anti-clockwise` and its inverse, for "books with bound edge at
> bottom of the page" and at the top.

A new `LayoutSettings` field:

```python
    source_rotation: Literal["none", "top_bound", "bottom_bound"] = "none"
    """A parity-aware quarter turn applied to every source page.

    ``top_bound`` turns odd pages 90 degrees clockwise and even pages 90
    anti-clockwise; ``bottom_bound`` is the inverse. Both exist so a
    calendar or a flip-style notebook -- a book whose spine is on a
    horizontal edge -- can be imposed by an imposer whose spine is always
    vertical.

    Deliberately not a fourth ``binding_edge`` value: ``binding_edge``
    selects which *vertical* edge carries the gutter, and a horizontal
    spine is a different axis. Making it four-valued would make every
    consumer handle four cases to serve a small fraction of hand binding.
    """
```

Applied in `_place_page`, composed into `Placement.rotate_deg` alongside the
landscape rotation and (after F7) the cell rotation:

```python
    parity_deg = _source_rotation_deg(settings.source_rotation, output_index)
    ...
        rotate_deg=(rotate_deg + cell_rotate_deg + parity_deg) % 360,
```

with

```python
def _source_rotation_deg(mode: str, output_index: int) -> int:
    """The parity-aware quarter turn for this leaf, in degrees."""
    if mode == "top_bound":
        return 270 if output_index % 2 == 0 else 90
    if mode == "bottom_bound":
        return 90 if output_index % 2 == 0 else 270
    return 0
```

**This makes `output_index` load-bearing under folio**, where it is currently
passed as `0` for every leaf (`layout.py:987, 997, 1013, 1022`). That is a
real problem and it is why §C is not free: the folio call sites pass `0`
because `spine_side` bypasses parity entirely. Fix by passing the leaf's
**reading position within the signature** instead of `0` — which the strategy
already knows (`page_offset + front_a` and friends) — and documenting that
`output_index` means "reading position", not "output page number".

`--source-rotation {none,top_bound,bottom_bound}` on the CLI; a
`Source rotation:` combo on the panel's Margins tab, with the two entries
labelled `"None"`, `"Bound at the top"`, `"Bound at the bottom"`.

#### Tests — additions to `tests/test_layout.py`

- `test_top_bound_turns_odd_and_even_pages_opposite_ways`
- `test_bottom_bound_is_the_inverse_of_top_bound`
- `test_source_rotation_composes_with_the_landscape_rotation`
- `test_source_rotation_composes_with_a_quarto_cell_turn` (after F7)
- `test_folio_leaves_carry_their_reading_position_as_output_index` — the
  change that makes parity reachable under folio at all
- `test_none_is_byte_identical_to_before` — export a plan with
  `source_rotation="none"` and diff the content streams against a
  pre-change golden. The neutrality method
  `docs/converge/.../neutrality-proof.md` describes.

#### Acceptance

| Check | Command |
|---|---|
| Tests pass | `.venv/bin/python -m pytest -q tests/test_layout.py` |
| Rotation reaches the PDF | `.venv/bin/python -m pytest -q -k source_rotation` (needs **B2** for 180 and the existing 90/270 branch for these) |
| Default is a no-op | `.venv/bin/python -m pytest -q -k none_is_byte_identical_to_before` |
| Round trip | `.venv/bin/python -m pytest -q tests/test_settings_roundtrip.py` |

#### decisions.md entry

```
## 2026-09-05 — Top- and bottom-edge binding, as a source rotation rather than a binding edge
- Symptom: a calendar or a flip-style notebook has its spine on a horizontal edge, and Deckle's imposer puts the spine on a vertical one. Deferred from the MVP (red-team P-5) and from v2.
- Fix: `LayoutSettings.source_rotation` with `top_bound`/`bottom_bound`, a parity-aware quarter turn composed into `Placement.rotate_deg`. NOT a third `binding_edge` value: `binding_edge` selects which vertical edge carries the gutter, a horizontal spine is a different axis, and making it four-valued would make every consumer handle four cases to serve a small fraction of hand binding.
- Surfaces: this made `output_index` load-bearing under folio, where all four `_place_page` calls passed 0 because `spine_side` bypassed parity. They now pass the leaf's reading position within the signature, and `output_index` is documented as meaning that.
- Watch: the default is `"none"` and a golden-content-stream test pins that it changes no byte of any existing export.
- Commit: <fill in>
```

---

### §D — `binding_edge` → `reading_direction`, and the first `.deckle` migration

**Size:** M. **Depends on:** F7/F8 recommended. **Human-approval item.**

#### Behaviour

Split the overloaded field in two:

```python
    binding_edge: Literal["left", "right"] = "left"
    """Which vertical edge carries the gutter, under ``fold_scheme="none"``.

    Under every folded scheme the spine is a function of which cell a leaf
    sits in, never of page parity, so this field says nothing there --
    ``reading_direction`` does.
    """

    reading_direction: Literal["ltr", "rtl"] = "ltr"
    """Which way the book reads, under every folded scheme.

    ``ltr`` is left-bound, ``rtl`` right-bound; it mirrors which cell each
    source slot lands in. Split out of ``binding_edge`` because that field
    meant two different things depending on ``fold_scheme``, and a setting
    whose meaning depends on another setting is one nobody can reason about
    from its name.
    """
```

`SaddleStitchStrategy` (and `QuartoStrategy`, `FrenchFoldStrategy`) read
`settings.reading_direction == "rtl"` where they read
`settings.binding_edge == "right"` today. `GutterShiftStrategy` keeps
`binding_edge`.

#### The migration — the first one

1. `FORMAT_VERSION = 2` in `deckle/core/project_io.py`.
2. A pure function, beside `_layout_from_dict`:

```python
def _migrate_layout(data: dict[str, Any], version: int) -> dict[str, Any]:
    """Bring a stored ``layout`` object up to ``FORMAT_VERSION``.

    Pure and total: it takes the stored dict and the version it was written
    at, and returns a dict this build's ``_layout_from_dict`` understands.
    It never raises for an old file -- an unopenable project is the failure
    this whole format's tolerance exists to prevent.

    Version 1 -> 2: ``binding_edge`` meant *reading direction* under
    ``fold_scheme="folio"`` and *gutter side* otherwise. A v1 folio project
    therefore carries its reading direction in ``binding_edge``, and this
    copies it into ``reading_direction`` before the field's meaning
    narrows. A v1 flat-sheet project's ``binding_edge`` means what it still
    means, and ``reading_direction`` takes its default.
    """
    if version >= 2:
        return data
    migrated = dict(data)
    if data.get("fold_scheme") == "folio":
        migrated["reading_direction"] = (
            "rtl" if data.get("binding_edge") == "right" else "ltr"
        )
        # `binding_edge` is left as stored: under folio this build ignores
        # it, and rewriting it would lose information a downgrade needs.
    return migrated
```

3. `load_project` reads `payload.get("version", 1)` and calls
   `_migrate_layout(payload["layout"], version)` before `_layout_from_dict`.
   **B28 records that this version is written and never read**; §D is what
   makes it read.
4. A file from a *newer* build (version > `FORMAT_VERSION`) keeps the
   existing tolerant behaviour — unknown keys dropped with
   `UnknownLayoutFieldsWarning`. Do **not** start refusing; the loader's
   docstring commits to opening in both directions.
5. Saving always writes version 2 and both fields.

#### The rest

- `--binding-edge` keeps its meaning and gains a sibling
  `--reading-direction {ltr,rtl}` (default `ltr`).
- The panel's `Binding edge:` combo gains a `Reading direction:` sibling,
  shown on the folded-mode tabs only (`_sync_signature_tab` already
  enables/disables per tab).
- `deckle/core/layout.py:838-846`'s paragraph about the overloading is
  deleted and replaced with one sentence naming `reading_direction`.
  `models.py:299-302`'s `:ivar binding_edge:` likewise.

#### Tests — `tests/test_project_migration.py`

- `test_a_version_1_folio_project_keeps_its_reading_direction` — a hand-built
  v1 payload with `fold_scheme="folio"`, `binding_edge="right"` loads with
  `reading_direction == "rtl"`.
- `test_a_version_1_flat_project_keeps_its_gutter_side` —
  `fold_scheme="none"`, `binding_edge="right"` loads with
  `binding_edge == "right"` and `reading_direction == "ltr"`.
- `test_a_version_1_project_imposes_to_the_same_sheets_after_migration` —
  **the assertion that matters**: impose the migrated project and compare the
  `Placement`s against a golden recorded from the pre-change build. A
  migration that opens the file and changes the book is worse than one that
  refuses.
- `test_a_version_2_project_is_untouched_by_the_migration`
- `test_a_version_from_the_future_still_opens_with_a_warning`
- `test_saving_writes_version_two_and_both_fields`
- `test_the_migration_is_pure` — call it twice on the same dict; the input is
  not mutated.
- `test_round_tripping_a_v1_file_upgrades_it_exactly_once`

#### Acceptance

| Check | Command |
|---|---|
| Migration tests pass | `.venv/bin/python -m pytest -q tests/test_project_migration.py` |
| The imposition is unchanged | `.venv/bin/python -m pytest -q -k imposes_to_the_same_sheets_after_migration` |
| The version is read, not just written | `grep -n "version" deckle/core/project_io.py \| grep -q "get(\"version\""` |
| `FORMAT_VERSION` moved | `grep -q "^FORMAT_VERSION = 2" deckle/core/project_io.py` |
| Nothing reads `binding_edge` in a folded strategy | `! grep -n "binding_edge" deckle/core/layout.py \| grep -iE "saddle\|quarto\|french"` |
| Round trip | `.venv/bin/python -m pytest -q tests/test_settings_roundtrip.py tests/test_project_io.py tests/test_project_file_shape.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

#### decisions.md entry

```
## 2026-09-05 — Split binding_edge into reading_direction, and read the .deckle version for the first time
- Symptom: v2 open question 2. `binding_edge` meant "which vertical edge carries the gutter" under flat sheets and "which way the book reads" under folio -- one field, two meanings, chosen by another field. A setting whose meaning depends on another setting cannot be reasoned about from its name, and quarto and French fold made it three schemes overloading it.
- Fix: `reading_direction: Literal["ltr","rtl"]` for the folded schemes; `binding_edge` narrows to the flat-sheet gutter side. `FORMAT_VERSION` went to 2 and `_migrate_layout` copies a v1 folio project's `binding_edge` into `reading_direction` before the meaning narrows -- the project's first migration, and the first time the `version` integer written since SS-01 has ever been read (B28).
- Surfaces: the migration leaves `binding_edge` as stored rather than rewriting it, so a downgrade loses nothing. A file from a newer build still opens with `UnknownLayoutFieldsWarning` rather than being refused: the loader's tolerance runs in both directions and that is what it is for.
- Watch: the test that matters is not that the file opens, it is that the migrated project imposes to byte-identical placements. A migration that opens a file and quietly changes the book is worse than one that refuses.
- Commit: <fill in>
```

---

## 4. Tests

Per sub-section, above. One additional cross-cutting test, wherever the suite
keeps its structural checks (`tests/test_project_file_shape.py` is the
closest):

`test_every_layout_settings_field_round_trips_through_a_deckle`
Iterate `dataclasses.fields(LayoutSettings)`, set each to a non-default
value, save, load, and assert equality. `tests/test_settings_roundtrip.py`
may already do this — read it first; if it does, extend its field list rather
than adding a second walk.

## 5. Acceptance

Per sub-section, above. In addition, for the whole item:

| Check | Command |
|---|---|
| Four commits, not one | `git log --oneline -4 \| grep -c "F12"` |
| Each touched `docs/decisions.md` | `git log --format=%H -4 \| while read h; do git show --stat "$h" \| grep -q "docs/decisions.md" \|\| { echo "FAIL: $h did not touch the decision log"; exit 1; }; done` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

- **The other two v2 open questions.** Question 1 (which sheet and side
  carries the sewing stations) is **F4**'s to answer with an awl. Question 4
  (where `blank_mode="balanced"` puts its blanks) is a separate decision and
  is entangled with B18.
- **B15 / N2** — pushing the resolved profile to the preview and reading the
  driver's printable rect. §B makes a better number available; it does not
  fix who reads it.
- **B31** in general — the loader's undeclared `skipped_non_image_files`
  kind. §B fixes the other half (a declared kind nobody emits) and leaves
  this one.
- **B28** in general — `print_session` checking version after the state
  check, and `printer` not being type-checked on load. §D reads the
  `.deckle` version; it does not fix the session's ordering.
- **A GUI for `--split`.** The print dialog prints; `--split` is for taking
  files elsewhere.
- **Octavo**, and any further `fold_scheme`. §D's migration is written for
  one field split, not as a general framework.

## 7. decisions.md entry

Four entries, one per sub-section, given inline above. Do not combine them:
the pre-commit hook wants a decision entry per code commit, and these are
four unrelated decisions that happen to share a roadmap ID.

## 8. Traps

- **Do not do all four at once.** They share nothing but an ID. A single
  commit touching `PrinterProfile`, `LayoutSettings`, `project_io` and the
  CLI is unreviewable and unbisectable.
- **§A: `--signature` is 1-based and `--sheets` is 0-based.** That is
  deliberate and must be in the help text of both, or it is just an
  inconsistency.
- **§B: never give `layout.py` a `PrinterProfile`.** The strategy signature
  is the v2 seam; `tests/test_core_purity.py` does not enforce this
  particular boundary, so nothing will stop you but the design.
- **§B: `imageable_area_pt` is `(left, top, right, bottom)` margins**, not a
  rect. `layout_panel.imageable_inset_pt`'s docstring records what reading it
  as `(x0, y0, x1, y1)` produces: a ~600pt "inset".
- **§C: the folio `_place_page` calls pass `output_index=0`** for every leaf
  (`layout.py:987, 997, 1013, 1022`). Parity is unreachable there until they
  pass the reading position, and a test that only exercises gutter shift will
  not notice.
- **§C needs B2** for any 180° component, and B1 if the user's own
  `slot.rotate_deg` is ever to compose with it.
- **§D: `_layout_from_dict` filters to known fields and warns.** A migration
  that adds a key must run *before* that filter, or the key it adds is
  dropped and warned about.
- **§D: do not rewrite `binding_edge` during the migration.** Leaving it
  makes a downgrade lossless; overwriting it makes the migration one-way for
  no benefit.
- **§D: the version was written and never read** (B28). Read it with
  `payload.get("version", 1)` — a file with no version key at all is a v1
  file, and refusing it would be the opposite of what the format's tolerance
  is for.
- The pre-commit hook refuses a code commit that does not also touch
  `docs/decisions.md`, and one whose added lines contain `<FILL-IN>`.
