# N14 — Make the CLI scriptable: `--json`, `--dry-run`, `deckle profile`

**Roadmap item:** `docs/ROADMAP.md` N14
**Depends on:** —. Edits `deckle/cli.py` throughout, so it collides hard with **M4** (the cli.py split into `cli/values.py`, `cli/report.py`, `cli/options.py`, `cli/commands.py`) and with **N5** and **N6**, which each add a flag. Recommended: **N5 → N6 → N14 → M4 last** — M4 should split a finished command set, not be re-merged against three specs. If M4 must go first, N14's four parts land in `cli/commands.py` and `cli/options.py` unchanged in substance.
**Blocks:** —
**Size:** M (four small parts; §3 is split so they can land one at a time)
**Decision needed first:** §3.5 recommends **dropping `deckle print`**. That is a recommendation, not a decision already taken.

---

## 1. Context

The CLI's stated purpose is scripting — `deckle/cli.py:1-9` calls it "a shipped
MVP feature, not a test harness", and it is what lets the golden-fixture
regression run headlessly. But every command speaks only prose. Getting a sheet
count out of Deckle today means:

```bash
$ .venv/bin/python -m deckle.cli info book.pdf
page count: 10
detected page sizes (pt):
  612.00 x 792.00
signature count: 0
sheet count: 5
blank count: 0
layout warnings: none
```

and then a regex over `^sheet count: (\d+)$` — a format that is not versioned,
not documented as an interface, and that four separate specs in this directory
propose to change. `layout warnings:` is worse: it is either the literal string
`layout warnings: none` or a header followed by an indented list, so a scraper
has to parse two shapes.

Three more gaps in the same class:

- **Nothing is checkable without writing a file.** `deckle export -o out.pdf`
  either writes a PDF or fails; there is no way to ask "would this work, and
  what would it produce" from a Makefile or a pre-flight check.
- **A printer profile can only be written by hand.** Nothing in `deckle/` ever
  calls `PrinterProfile.save` (that is F1's whole finding), and `_resolve_profile`
  tells the user to "Calibrate a printer in the desktop app to save one under
  its own name" — an instruction to use a feature that does not exist (B21).
- **`deckle print` does not exist**, and §3.5 recommends it stays that way.

## 2. Current code

`deckle/cli.py:865-898` — `_cmd_info` in full, which is the text shape §3.1
must reproduce exactly:

```python
def _cmd_info(args: argparse.Namespace) -> int:
    resolved = _resolve_input(args)
    if resolved is None:
        return 1
    pages, settings = resolved

    if _is_project_file(args.source):
        print(f"project: {args.source}")
    print(f"page count: {len(pages)}")
    sizes = sorted({(p.ref.width_pt, p.ref.height_pt) for p in pages})
    print("detected page sizes (pt):")
    for w, h in sizes:
        print(f"  {w:.2f} x {h:.2f}")

    import_warnings = list(getattr(pages, "warnings", []))
    plan = _impose_or_report(pages, settings)
    if plan is None:
        return 1

    # Signature breakdown -- always printed, even under the MVP
    # (fold_scheme="none") path, where there are simply zero signatures.
    blank_total = sum(sig.blank_count for sig in plan.signatures)
    print(f"signature count: {len(plan.signatures)}")
    print(f"sheet count: {len(plan.sheets)}")
    print(f"blank count: {blank_total}")

    all_warnings = import_warnings + list(plan.warnings)
    if all_warnings:
        print("layout warnings:")
        for w in all_warnings:
            print(f"  [{w.kind}] sheet {w.sheet_index}: {w.detail}")
    else:
        print("layout warnings: none")
    return 0
```

`deckle/cli.py:1080-1122` — `_cmd_schedule`, which renders
`format_schedule_text(build_schedule(plan, settings), basename(source))` to
stdout or, with `-o`, atomically to a file.

`deckle/core/schedule.py:31-131` — the four dataclasses `--json` mirrors:
`SheetInstruction(sheet_index, position, front_pages, back_pages)` with an
`is_outermost` property; `SignatureInstruction(index, sheets, blank_count)` with
`sheet_count`/`page_count`/`first_page`/`last_page`; and `Schedule(signatures,
sheets_total, blank_total, sewing_stations, sewing_margin_pt,
paper_thickness_pt, fold_scheme, spine_width_pt, duplex_flip_edge, notes)` with
`signature_count`.

`deckle/cli.py:952-1028` — `_cmd_export`, whose write happens at
`export_plan(...)` (line 1002) after `_report_output_problem`,
`_resolve_input`, `_impose_or_report`, `_emit_warnings` and
`_report_missing_sheets`. Every one of those is a check `--dry-run` wants to
keep.

`deckle/cli.py:1055-1077` — `_cmd_impose`, whose write is
`save_project(project, args.output)` (line 1069).

`deckle/cli.py:420-448` — `_resolve_profile`, and the instruction that is not
true:

```python
    print(
        f"error: no printer profile {name!r}. Built-in profiles: "
        f"{', '.join(sorted(BUILTIN_PRESETS))}. Calibrate a printer in the "
        "desktop app to save one under its own name.",
        file=sys.stderr,
    )
```

`deckle/core/profiles.py:30-160` — `PrinterProfile`: a frozen dataclass with
`version`, `flip_axis`, `output_face`, `feed_edge`, `reverse_stack`,
`imageable_area_pt`, `calibrated_at`, `calibration_version`,
`back_offset_x_pt`, `back_offset_y_pt`, plus `save(name)` and `load(name)`.
`load` is tolerant about keys and strict about values via
`schema.check_values`.

`deckle/core/profiles.py:163-175` — `_config_dir()` is
`config_dir("printer_profiles")` and `_profile_path(name)` is
`_config_dir() / f"{name}.json"`.

`deckle/cli.py:1-9` — the invariant §3.5 turns on:

```python
"""Deckle's headless CLI.
...
Imports only the pure core package -- never the Qt-based desktop app layer
or any Qt binding -- so it stays runnable in a headless container with no
display server present.
"""
```

`deckle/core/printing.py:82-92` — `PrintBackend` is a `Protocol`; the only
implementation is `deckle.app.backend.QtPrintBackend`, which imports PySide6.

`deckle/cli.py:1222-1380` — `build_parser`, and the note that it is rebuilt on
every project load just to introspect defaults (M4's complaint,
`cli.py:1222` + `_layout_flags_given` at `cli.py:564-588`).

**Call sites of `build_schedule` / `format_schedule_text` (grep):**
`deckle/core/schedule.py:201,331`, `deckle/cli.py:37,1103,1104`,
`deckle/app/views/layout_panel.py:39,1330,1331`; `tests/test_schedule.py`,
`tests/test_grain_and_spine.py`, `tests/test_ui_surface.py`,
`tests/test_custom_signatures.py`.

**Call sites of `PrinterProfile.save` (grep):** `deckle/core/profiles.py:83`
(definition) and `tests/test_config_store_durability.py`. **Nothing in
`deckle/` calls it** — F1's finding, and §3.4 is the first caller.

**Existing tests:** `tests/test_cli.py`, `tests/test_cli_errors.py`,
`tests/test_cli_sheets.py`, `tests/test_cli_passes.py`,
`tests/test_cli_paper.py`, `tests/test_cli_output_durability.py`,
`tests/test_project_cli.py`, `tests/test_output_command_parity.py`,
`tests/test_core_purity.py`, `tests/test_config_store_durability.py`.

---

## 3. Change

Four independent parts. Each can land alone; §3.5 is a recommendation to build
nothing.

### 3.1 `info --json`

```python
    info_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help=(
            "print the same facts as a single JSON object on stdout, for "
            "scripting. The text form is for reading and may be reworded; "
            "this is the interface"
        ),
    )
```

`dest="as_json"` because `args.json` would shadow the `json` module for anyone
reading the handler.

**The object, field by field, derived from what the text prints today:**

```json
{
  "schema": 1,
  "source": "book.pdf",
  "is_project": false,
  "page_count": 10,
  "page_sizes_pt": [{"width": 612.0, "height": 792.0}],
  "signature_count": 0,
  "sheet_count": 5,
  "blank_count": 0,
  "paper_pt": {"width": 612.0, "height": 792.0},
  "warnings": [
    {"kind": "signature_padding", "sheet_index": 0, "detail": "padded with 2 blank page(s) to complete the final signature"}
  ]
}
```

| Field | From | Notes |
|---|---|---|
| `schema` | literal `1` | A version on the *interface*, so a consumer can branch. The `.deckle` format's own `version` integer sat unread for a year (`project_io.FORMAT_VERSION`, and the 2026-08-04 decision entry); this one exists to be read. |
| `source` | `args.source` | Exactly as typed, matching the `project: <path>` line. |
| `is_project` | `_is_project_file(args.source)` | Replaces the *presence* of the `project:` line. A boolean, because a field that appears only sometimes is what makes the text hard to scrape. |
| `page_count` | `len(pages)` | |
| `page_sizes_pt` | `sorted({(w, h)})` | Objects, not pairs: `[612.0, 792.0]` is ambiguous about order to a reader, and the text says `x`. Full float precision, **not** the text's `:.2f` — rounding for display is a display decision. |
| `signature_count` | `len(plan.signatures)` | |
| `sheet_count` | `len(plan.sheets)` | |
| `blank_count` | `sum(sig.blank_count ...)` | |
| `paper_pt` | `plan.paper_pt` | Not in the text at all. Added because the first thing a script does with a sheet count is work out how much paper to load, and `--paper`/`--landscape` interact. |
| `warnings` | `import_warnings + plan.warnings` | Always a list, empty for none — replacing the two-shaped `layout warnings: none` / header-plus-list. Import warnings first, then layout, matching the text's order. |

**Rules that apply to every `--json` output in this spec:**

- The object goes to **stdout**, alone, with a trailing newline
  (`json.dumps(payload, indent=2)` then `print`). Warnings and errors keep going
  to stderr, which `_emit_warnings` already does (`cli.py:915`) "so
  `deckle export` keeps a clean stdout for scripting".
- `--json` suppresses the text form entirely. Both would mean neither is
  parseable.
- Errors are **not** JSON. A failure exits 1 with a message on stderr, exactly
  as today. Wrapping errors in JSON would mean a consumer has to parse stdout to
  find out whether to look at it; the exit code already answers that.
- `indent=2`, matching `save_project` and `PrinterProfile.save`
  (`project_io.py:558`, `profiles.py:106`), so every JSON Deckle writes looks
  the same.

**Step:** extract the body of `_cmd_info` after `_impose_or_report` into
`_info_payload(args, pages, plan) -> dict`, then branch:

```python
    if args.as_json:
        print(json.dumps(_info_payload(args, pages, plan), indent=2))
        return 0
```

placed **after** the plan is imposed and before the first `print`. The text
branch stays byte-for-byte identical — `tests/test_cli.py` pins it.

### 3.2 `schedule --json`

```python
    schedule_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help=(
            "write the schedule as a JSON object instead of bench text. The "
            "text is a work order for a person; this is for a script"
        ),
    )
```

Honours `-o` the same way: with `--json -o path`, the JSON is written
atomically to `path` (`write_text_atomic`, `cli.py:1116`) rather than stdout.

**The object**, mirroring `Schedule` field for field plus its derived
properties, because the properties are what the text actually prints:

```json
{
  "schema": 1,
  "source": "book.pdf",
  "fold_scheme": "folio",
  "sheets_total": 3,
  "blank_total": 2,
  "signature_count": 1,
  "sewing_stations": 3,
  "sewing_margin_pt": 36.0,
  "paper_thickness_pt": 0.288,
  "spine_width_pt": {"low": 0.9504, "high": 1.08},
  "duplex_flip_edge": "short",
  "notes": ["Fore-edge creep is about ..."],
  "signatures": [
    {
      "index": 1,
      "sheet_count": 3,
      "page_count": 12,
      "blank_count": 2,
      "first_page": 1,
      "last_page": 10,
      "sheets": [
        {
          "sheet_index": 0,
          "position": 1,
          "is_outermost": true,
          "front_pages": [12, 1],
          "back_pages": [2, 11]
        }
      ]
    }
  ]
}
```

| Field | From | Notes |
|---|---|---|
| `schema` | literal `1` | |
| `source` | `os.path.basename(args.source)` | The same value passed to `format_schedule_text` as its title. |
| `fold_scheme` … `duplex_flip_edge` | the `Schedule` fields of those names | Verbatim. |
| `spine_width_pt` | `Schedule.spine_width_pt` | An object with `low`/`high`, or `null`. A two-element array would be read as a rect by someone who has seen `imageable_area_pt`. |
| `signature_count` | `Schedule.signature_count` | A property; included because the text prints it. |
| `notes` | `Schedule.notes` | Always a list. |
| `signatures[].index` | `SignatureInstruction.index` | **1-based**, as the dataclass already documents ("a schedule is read by a person"). Said again in the CLI help. |
| `signatures[].sheet_count` / `page_count` / `first_page` / `last_page` | the properties | `first_page`/`last_page` may be `null`. |
| `signatures[].sheets[].front_pages` / `back_pages` | `SheetInstruction` | Arrays with `null` for a blank — which is exactly what `_page_numbers` produces and what the text renders as the word `blank`. |
| `signatures[].sheets[].is_outermost` | the property | The text renders it as `(outermost)` versus `position N`. |

Under `fold_scheme="none"` the object is the same shape with
`"signatures": []` — the same "always the same shape" rule as `warnings`.

**Deliberately not included:** the `AT THE PRINTER` block. It is instruction
prose for a person, it is `_printer_lines`'s to word, and a script that wants
the flip edge has `duplex_flip_edge`.

**Step:** add `_schedule_payload(schedule, source) -> dict` beside
`_cmd_schedule`, and branch on `args.as_json` before the
`format_schedule_text` call, sharing the existing `-o` write path.

### 3.3 `export --dry-run` and `impose --dry-run`

```python
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "do everything except write the output: load the source, check "
            "the destination, impose, and report what would be produced. "
            "Exits non-zero if the real run would have failed"
        ),
    )
```

Added to both `export_parser` and `impose_parser` — **not** to
`_add_layout_args`, which is shared with `info`, `schedule` and `crop-preview`,
none of which write a document.

**What it does:** everything up to and including the write, then stops. For
`export` that is `_report_output_problem`, `_resolve_input`,
`_impose_or_report`, `_emit_warnings`, `_report_missing_sheets`, and the whole
`--pass`/`--profile` resolution block — every one of which can fail, and every
one of which is what a pre-flight check is for. For `impose` it is
`_report_output_problem`, `_resolve_input`, `_impose_or_report`,
`_emit_warnings`.

**What it prints**, replacing the `wrote {path}` line:

```
would write out.pdf -- 5 sheet(s), 10 page(s), 612x792pt
```

for `export`, and

```
would write job.deckle -- 10 page(s), 5 sheet(s)
```

for `impose`. Exact format strings:

```python
DRY_RUN_EXPORT = "would write {path} -- {sheets} sheet(s), {pages} page(s), {w:g}x{h:g}pt"
DRY_RUN_IMPOSE = "would write {path} -- {pages} page(s), {sheets} sheet(s)"
```

`{sheets}` for `export` is `len(selection)` when a selection was given (so
`--sheets 0 --dry-run` reports 1), else `len(plan.sheets)`. The registration
line and the reload instruction (`cli.py:1022-1025`) are **still printed** — a
dry run of `--pass back` should tell you what the reload will be, which is
half of why you would run it.

**What it must not do:** touch the filesystem at all. `_report_output_problem`
only stats (`outputs.output_path_problem`), which is fine. The
`export_plan(...)` and `save_project(...)` calls are skipped entirely — not
redirected to a temp file, which would exercise a different code path and
report a success the real run might not have.

**Steps:** in `_cmd_export`, replace the `try: export_plan(...)` block with:

```python
    if args.dry_run:
        print(DRY_RUN_EXPORT.format(
            path=args.output,
            sheets=len(selection) if selection is not None else len(plan.sheets),
            pages=len(pages),
            w=plan.paper_pt[0], h=plan.paper_pt[1],
        ))
    else:
        try:
            export_plan(...)
        except ...:
            ...
        print(f"wrote {args.output}")
```

with the registration/reload/rule reporting after the branch, unchanged. Same
shape in `_cmd_impose` around `save_project`.

### 3.4 `deckle profile list | show | set`

A new top-level subcommand with three sub-subcommands. This is the CLI half of
**F1**, and it makes `_resolve_profile`'s message true.

```
deckle profile list [--json]
deckle profile show NAME [--json]
deckle profile set NAME [--from PRESET] [field flags...]
```

**`profile list`** — every profile that can be named, saved first then built-in:

```
saved:
  Brother HL-2270DW
built-in:
  generic_face_down_reversed
  generic_face_up_in_order
```

A section with no entries prints its header and `  (none)`. `--json` gives
`{"schema": 1, "saved": [...], "built_in": [...]}`.

Saved profiles are `sorted(p.stem for p in profiles._config_dir().glob("*.json"))`
— which needs a public helper, because reaching into a private is how a second
copy of the path rule appears:

```python
# deckle/core/profiles.py
def saved_profile_names() -> list[str]:
    """Every printer a profile has been saved for, sorted.

    :returns: the names. An unreadable or absent config directory reads as
        empty -- listing profiles must not be what fails when the config
        directory is gone.
    """
```

**`profile show NAME`** — resolves through `_resolve_profile` (saved first,
then built-in, then the error naming the alternatives), and prints one
`field: value` line per dataclass field in declaration order:

```
name: generic_face_down_reversed
source: built-in
version: 1
flip_axis: long
output_face: down
feed_edge: top
reverse_stack: true
imageable_area_pt: 18, 18, 18, 18
calibrated_at:
calibration_version: 0
back_offset_x_pt: 0
back_offset_y_pt: 0
```

`source` is `saved` or `built-in`, because which one you got is the question
`select_preselected_printer` and `resolve_profile` both silently answer today
(`print_dialog.py:44-56` even logs it for that reason). `--json` gives
`{"schema": 1, "name": ..., "source": ..., **asdict(profile)}`.

**`profile set NAME`** — one flag per settable field:

| Flag | Field | Type / choices |
|---|---|---|
| `--from PRESET` | — | a built-in name to start from; default is the saved profile if one exists, else `generic_face_down_reversed` |
| `--flip-axis {long,short}` | `flip_axis` | |
| `--output-face {up,down}` | `output_face` | |
| `--feed-edge {top,bottom}` | `feed_edge` | |
| `--reverse-stack` / `--no-reverse-stack` | `reverse_stack` | `BooleanOptionalAction` is not used — it is Python 3.9+ and reads oddly in help; two explicit `store_true`/`store_false` flags onto one `dest` with `default=None` |
| `--imageable-area L,T,R,B` | `imageable_area_pt` | four lengths via `_parse_crop`'s shape, but a new `_parse_margin_quad` so the error message says "imageable area" and names the **left, top, right, bottom** order — which is *not* `--crop`'s left, bottom, right, top, and confusing the two is how a printer's bottom margin ends up on its top edge |
| `--back-offset X,Y` | `back_offset_x_pt`, `back_offset_y_pt` | `_parse_offset_pair`, already written (`cli.py:369`) |

**Not settable:** `version` (the format's, not the user's),
`calibrated_at` and `calibration_version` — those describe a calibration run,
and `profile set` is not one. `calibrated_at` is set to
`datetime.now(timezone.utc).isoformat(timespec="seconds")` and
`calibration_version` left at the base profile's value, with a printed note:

```
note: calibrated_at recorded as <stamp>. These numbers were typed, not
measured from a printed target -- print a proof and check before
committing a stack.
```

Refuses to write when no field flag was given:

```
error: nothing to set. Give at least one of --flip-axis, --output-face,
--feed-edge, --reverse-stack/--no-reverse-stack, --imageable-area,
--back-offset.
```

Prints on success:

```
wrote <path>
```

using `profiles._profile_path(name)`'s answer, so the user can find it. **A
name containing a path separator is refused** before writing:

```python
    if os.sep in name or (os.altsep and os.altsep in name) or name in (".", ".."):
        print(
            f"error: {name!r} is not a usable profile name: it contains a "
            "path separator. Profiles are stored one file per printer name, "
            "so a name with a separator would write outside the profile "
            "directory.",
            file=sys.stderr,
        )
        return 1
```

That is **B34's second half** arriving as a guard rather than a fix — B34 owns
sanitising `\\server\printer` at the `profiles` layer. The guard here refuses
rather than mangles, so the two cannot disagree.

**Step:** `_add_profile_command(subparsers)` building the three sub-parsers,
`_cmd_profile_list`, `_cmd_profile_show`, `_cmd_profile_set`, each returning an
exit code, each set as `func` on its own sub-subparser. `profile` itself gets
`subparsers.add_parser("profile", help="list, show and write printer profiles")`
with its own `add_subparsers(dest="profile_command", required=True)`.

**And fix the lie:** `_resolve_profile`'s message (`cli.py:442-447`) becomes:

```python
    print(
        f"error: no printer profile {name!r}. Built-in profiles: "
        f"{', '.join(sorted(BUILTIN_PRESETS))}. Write one with "
        "'deckle profile set <name>', or see 'deckle profile list'.",
        file=sys.stderr,
    )
```

### 3.5 `deckle print` — recommend dropping

**Recommendation: do not build it.** Two reasons, in order.

1. **It would break the CLI's defining invariant.** `deckle/cli.py`'s module
   docstring promises it "imports only the pure core package -- never the
   Qt-based desktop app layer or any Qt binding -- so it stays runnable in a
   headless container with no display server present". The session and the pass
   planner really are Qt-free — `PrintSession` and `plan_passes` import nothing
   Qt — but `PrintBackend` is a `Protocol` (`printing.py:82`) with exactly one
   implementation, `deckle.app.backend.QtPrintBackend`, which imports PySide6
   and paints through a `QPainter`. A `deckle print` either imports
   `deckle.app` (breaking the invariant, and the headless-container promise
   with it) or ships a second backend, which is a print backend Deckle would
   then have to keep correct on two platforms with no user.

2. **The value is small and the risk is not.** The thing a scripted print
   would want is unattended operation, and manual duplex is by definition
   attended: pass 1, a human reloads the paper the right way round, pass 2.
   `PrintSession` exists to sequence a person. A CLI that starts a session and
   then blocks for a reload confirmation is a worse interface than the dialog,
   and one that does not block would print backs onto an unreloaded stack.

**If it is wanted anyway**, it belongs in the GUI package, not the CLI: a
`python -m deckle.print` entry point next to `deckle/app/`, importing
`QtPrintBackend` directly and declaring a display requirement. That keeps
`deckle.cli`'s promise intact, and `tests/test_core_purity.py`'s enforcement
with it.

**What to do instead**, and it is already specified: **N4** writes a single
manual-duplex pass to a PDF from the app, and `deckle export --pass front
--profile NAME` does it from the CLI today. A script that wants to print
without the GUI writes two PDFs and hands them to `lp`/`lpr` — which is a real
printing tool, on the platform's own terms.

---

## 4. Tests

### `tests/test_cli_json.py` (new)

Build a fixture with `deckle dummy -o d.pdf --pages 10` into `tmp_path`, run
`deckle.cli.main([...])` in-process with `capsys`.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_info_json_is_a_single_object` | `json.loads(capsys.readouterr().out)` succeeds and is a `dict` | exit 2: `unrecognized arguments: --json` |
| `test_info_json_carries_every_number_the_text_does` | for the flat 10-page dummy: `page_count == 10`, `signature_count == 0`, `sheet_count == 5`, `blank_count == 0`, `warnings == []` | as above |
| `test_info_json_reports_folio_counts` | `--fold-scheme folio --landscape`: `signature_count == 1`, `sheet_count == 3`, `blank_count == 2`, and `warnings[0]["kind"] == "signature_padding"` | as above |
| `test_info_json_page_sizes_are_named_objects` | `page_sizes_pt == [{"width": 612.0, "height": 792.0}]` | as above |
| `test_info_json_keeps_full_float_precision` | with `--paper 500.125x700.5pt`, `paper_pt["width"] == 500.125` — not the text's `:.2f` | as above |
| `test_info_json_marks_a_project_source` | against a `.deckle`: `is_project is True` | as above |
| `test_info_json_carries_a_schema_version` | `payload["schema"] == 1` | as above |
| `test_info_json_prints_no_prose` | stdout parses as JSON with nothing before or after it | as above |
| `test_info_text_is_unchanged` | without `--json`, stdout equals the exact eight lines in §1 | passes today; it pins that the text branch did not move |
| `test_schedule_json_mirrors_the_dataclass` | folio job: `sheets_total`, `blank_total`, `signature_count`, `sewing_stations`, `sewing_margin_pt == 36.0`, `duplex_flip_edge == "short"` | exit 2 |
| `test_schedule_json_signatures_are_one_based` | `payload["signatures"][0]["index"] == 1` | as above |
| `test_schedule_json_blanks_are_null` | a padded signature has `null` in a `front_pages`/`back_pages` array | as above |
| `test_schedule_json_marks_the_outermost_sheet` | `signatures[0]["sheets"][0]["is_outermost"] is True` | as above |
| `test_schedule_json_spine_is_low_and_high` | with `--paper-thickness 0.004in`: `spine_width_pt == {"low": ..., "high": ...}` with `low < high` | as above |
| `test_schedule_json_spine_is_null_without_a_thickness` | `spine_width_pt is None` | as above |
| `test_schedule_json_under_flat_sheets_has_no_signatures` | `signatures == []` and the object still has every other key | as above |
| `test_schedule_json_to_a_file` | `--json -o out.json` | the file parses as JSON, stdout says `wrote out.json` | as above |
| `test_json_never_goes_to_stderr` | with a job that warns | stdout is pure JSON, the warning is on stderr | as above |

### `tests/test_cli_dry_run.py` (new)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_export_dry_run_writes_nothing` | `export d.pdf -o out.pdf --dry-run` | exit 0, `not os.path.exists(out.pdf)` | exit 2 |
| `test_export_dry_run_says_what_it_would_write` | | stdout contains `"would write out.pdf -- 5 sheet(s), 10 page(s), 612x792pt"` | as above |
| `test_export_dry_run_counts_a_sheet_selection` | `--sheets 0 --dry-run` | `"1 sheet(s)"` | as above |
| `test_export_dry_run_still_refuses_a_bad_selection` | `--sheets 99 --dry-run` | exit 1, stderr `"no sheet 99"` | as above |
| `test_export_dry_run_still_refuses_a_bad_destination` | `-o /` | exit 1 | as above |
| `test_export_dry_run_still_reports_the_reload` | `--pass back --profile generic_face_down_reversed --dry-run` | stdout contains the reload instruction | as above |
| `test_export_dry_run_never_clobbers_an_existing_file` | write `out.pdf` with known bytes first | the bytes are unchanged | as above |
| `test_impose_dry_run_writes_no_project` | `impose d.pdf -o job.deckle --dry-run` | exit 0, no file | as above |
| `test_impose_dry_run_says_what_it_would_write` | | `"would write job.deckle -- 10 page(s), 5 sheet(s)"` | as above |
| `test_a_real_run_is_unaffected` | without the flag | the file appears and stdout says `wrote ...` | passes today |

### `tests/test_cli_profile.py` (new)

`monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))` **and**
`monkeypatch.setenv("APPDATA", str(tmp_path))` in every test —
`paths._root` reads the environment at call time, and an unguarded test writes
into the developer's real profile directory.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_profile_list_names_the_builtins` | `profile list` stdout contains both built-in names under a `built-in:` header | exit 2: `invalid choice: 'profile'` |
| `test_profile_list_says_none_when_nothing_is_saved` | | `saved:` followed by `  (none)` | as above |
| `test_profile_list_json` | `--json` | `payload["built_in"] == ["generic_face_down_reversed", "generic_face_up_in_order"]` | as above |
| `test_profile_show_a_builtin` | `profile show generic_face_down_reversed` | stdout has `flip_axis: long`, `reverse_stack: true`, `source: built-in` | as above |
| `test_profile_show_an_unknown_name_names_the_alternatives` | | exit 1, stderr lists both built-ins and mentions `deckle profile set` | today it says "Calibrate a printer in the desktop app", which does not exist (B21) |
| `test_profile_set_writes_a_file` | `profile set "My Printer" --flip-axis short` | a JSON file appears under `<config>/deckle/printer_profiles/My Printer.json`; stdout says `wrote <path>` | as above |
| `test_profile_set_round_trips_through_load` | after the above | `PrinterProfile.load("My Printer").flip_axis == "short"` | as above |
| `test_profile_set_starts_from_a_named_preset` | `--from generic_face_up_in_order --back-offset 3,-2` | `reverse_stack is False` (from the preset) and `back_offset_x_pt == 3.0` | as above |
| `test_profile_set_starts_from_the_saved_profile_when_there_is_one` | set twice, changing a different field each time | both changes survive | as above |
| `test_profile_set_parses_the_imageable_area_in_left_top_right_bottom` | `--imageable-area 0.25in,0.5in,0.25in,0.75in` | `imageable_area_pt == (18.0, 36.0, 18.0, 54.0)` | as above |
| `test_profile_set_records_when_it_was_typed` | | `calibrated_at` is a non-empty ISO stamp, and stdout carries the "typed, not measured" note | as above |
| `test_profile_set_with_no_fields_is_refused` | | exit 1, stderr `"nothing to set"`, no file written | as above |
| `test_profile_set_refuses_a_path_separator_in_the_name` | `profile set "a/b"` (and `"a\\b"` on Windows) | exit 1, nothing written anywhere under `tmp_path` | as above — today `_profile_path` would happily build the path (B34) |
| `test_profile_set_refuses_a_value_outside_its_literal` | `--flip-axis diagonal` | exit 2 from argparse's `choices` | as above |
| `test_saved_profile_names_reads_an_empty_directory` | no config dir at all | `saved_profile_names() == []`, no exception | `ImportError: cannot import name 'saved_profile_names'` |

### `tests/test_core_purity.py` — no change, but it is the acceptance check

It imports every module under `deckle.core` and fails if any pulls a Qt binding
into `sys.modules`. §3.5's recommendation is that `deckle.cli` keeps the same
property; §5 has an explicit grep.

### `tests/test_docs_coverage.py`

No new module under `deckle/` unless M4 has landed, so nothing to add.

## 5. Acceptance

| Check | Command |
|---|---|
| The JSON tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_json.py` |
| The dry-run tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_dry_run.py` |
| The profile tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_profile.py` |
| The existing CLI tests pass unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_errors.py tests/test_cli_sheets.py tests/test_cli_passes.py tests/test_project_cli.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `info --json` is machine-readable | `.venv/bin/python -m deckle.cli info tests/fixtures/sample.pdf --json \| .venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print(d['sheet_count'])"` |
| `schedule --json` is machine-readable | `.venv/bin/python -m deckle.cli schedule tests/fixtures/sample.pdf --fold-scheme folio --landscape --json \| .venv/bin/python -m json.tool > /dev/null` |
| A dry run writes nothing | `rm -f /tmp/n14.pdf && .venv/bin/python -m deckle.cli export tests/fixtures/sample.pdf -o /tmp/n14.pdf --dry-run && ! test -e /tmp/n14.pdf` |
| The CLI can write a profile | `XDG_CONFIG_HOME=/tmp/n14cfg .venv/bin/python -m deckle.cli profile set testprinter --flip-axis short && test -f "/tmp/n14cfg/deckle/printer_profiles/testprinter.json"` |
| The "calibrate in the desktop app" lie is gone | `! grep -n "Calibrate a printer in the" deckle/cli.py` (today: one hit at `cli.py:444`; the sentence is split across two source lines, so grep for the first half only) |
| **The CLI still imports no Qt** | `! grep -rnE "^(from\|import) (PySide6\|deckle\.app)" deckle/cli.py` and `.venv/bin/python -c "import deckle.cli, sys; assert not [m for m in sys.modules if m.startswith('PySide6')], 'the CLI imported Qt'"` |
| **`deckle print` was not built** | `! .venv/bin/python -m deckle.cli print --help 2>&1 \| grep -q "usage: deckle print"` — a bare `grep -w print` over `--help` is useless here: the word already appears four times in other commands' help text ("print page count", "print the binding schedule", …) |
| `[HUMAN]` The profile is believable | Write a profile with `deckle profile set "<your printer>" --imageable-area <your measured margins>`, then open the print dialog in the app: the red preview guide must move to match. This is the round trip F1 exists for. |

## 6. Out of scope

- **`deckle print`.** §3.5 recommends building nothing, and §5 asserts nothing
  was built. If the owner decides otherwise, it is a new spec against
  `deckle/app`, not against `deckle/cli`.
- **F1** (the profile picker and editor *in the app*). §3.4 is the CLI half and
  the first caller `PrinterProfile.save` has ever had; F1 is still the thing
  that fixes B16 for a GUI user.
- **B21's other half** — the print dialog catches only `OSError` from
  `PrinterProfile.load`, so a corrupt saved profile crashes it while the CLI
  reports it. Same bug, different file.
- **B34** — `write_text_atomic` tightening a shared file's mode to 0600, and
  `_profile_path` building a UNC path from `\\server\printer`. §3.4 *refuses* a
  name with a separator rather than sanitising it, so B34 remains free to fix
  the storage layer without the CLI having pre-mangled anything.
- **B22, B23, B24, B25** — four CLI defects around flags and project loading.
  All in this file, none touched here.
- **M4** (the cli.py split). N14 adds roughly 200 lines to a file M4 wants to
  break up; the recommendation in the header is to land N14 first.
- **A `--json` for `export`, `impose` or `crop-preview`.** Their output is a
  file plus one line naming it; there is nothing to parse.
- **D5** (GUIDE §8's CLI reference already omits a dozen flags). Docs pass.

## 7. decisions.md entry

```
## 2026-09-05 — The CLI's stated purpose was scripting, and it only spoke prose
- Symptom: `deckle info` prints eight lines of English and `deckle schedule` prints a bench work order, so a script wanting a sheet count regex-scraped `^sheet count: (\d+)$` -- against a format that is not versioned, not documented as an interface, and that four open specs propose to change. `layout warnings:` has two shapes ("none" inline, or a header plus an indented list), so even a scraper has to branch. Nothing could be checked without writing a file, and a printer profile could only be created by hand: nothing in `deckle/` had ever called `PrinterProfile.save`, while `_resolve_profile` told users to "calibrate a printer in the desktop app", a feature that does not exist.
- Fix: `--json` on `info` and `schedule` (one object on stdout, `schema: 1`, warnings and errors still on stderr, full float precision rather than the text's display rounding); `--dry-run` on `export` and `impose`, which performs every check including the destination and the `--pass` resolution and then does not write; and `deckle profile list|show|set`, the first caller `PrinterProfile.save` has ever had.
- Surfaces: `deckle print` was considered and NOT built. `PrintSession` and `plan_passes` are genuinely Qt-free, but the only `PrintBackend` implementation is `QtPrintBackend`, so the command would either import `deckle.app` -- breaking the module docstring's promise that the CLI runs in a headless container -- or need a second print backend with no users. And manual duplex is attended by definition: a CLI that blocks for a reload confirmation is a worse dialog, and one that does not blocks nothing and prints backs onto an unreloaded stack. `export --pass` plus `lp` is the scriptable answer.
- Surfaces: `profile set` refuses a name containing a path separator rather than sanitising it, so B34 stays free to fix the storage layer without the CLI having already mangled the name. It also stamps `calibrated_at` and says out loud that the numbers were typed, not measured off a printed target.
- Watch: A text format that nobody declared an interface still becomes one the moment it ships. `schema: 1` exists to be read -- unlike `project_io.FORMAT_VERSION`, which was written for a year and never consulted.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Every command here is
  `python -m deckle.cli`.
- **`deckle.cli` must not import Qt or `deckle.app`.** It is stated in the
  module docstring, it is what makes the headless container promise true, and
  §5 checks it two ways. This is the whole of §3.5's argument.
- **stdout must stay pure under `--json`.** `_emit_warnings` already writes to
  stderr on purpose (`cli.py:915`), but `_apply_auto_crop` prints its measured
  values to **stdout** (`cli.py:610-612`) — so `info --json --auto-crop` would
  emit two lines of prose before the object. Route those through stderr when
  `as_json` is set, or refuse the combination; the former is better and is one
  line.
- **`dest="as_json"`, not `dest="json"`.** `args.json` reads like the module in
  a handler that also calls `json.dumps`.
- **`_layout_flags_given` compares every option against its default**
  (`cli.py:564-588`) to decide what to report as ignored for a `.deckle` source.
  `--json` and `--dry-run` are `store_true` with a `False` default, so they are
  correctly silent — but check the report after adding them, because that
  function skips only `output`, `help` and `source` by name.
- **`_parse_crop` is `left, bottom, right, top`; `imageable_area_pt` is
  `left, top, right, bottom`.** They are different orders and both are four
  lengths. `preview_view.imageable_rect_pt` (`preview_view.py:44-64`) and
  `layout_panel.imageable_inset_pt` (`layout_panel.py:165-183`) both spell the
  latter out because it has been got wrong before. Use a separate parser with
  its own message.
- **`PrinterProfile.load` is strict about values** via `schema.check_values`
  and raises `StoredValueError` (a `ValueError`). `profile show` on a corrupt
  file must report it, not traceback — `_resolve_profile` already catches
  `ValueError` and falls through to the built-ins, which for `show` is the
  wrong answer: it would silently show a preset under the user's printer's
  name. Handle `show` separately from `_resolve_profile`.
- **`paths._root` reads the environment at call time**, so every profile test
  must set both `XDG_CONFIG_HOME` and `APPDATA` or it writes into the
  developer's real config directory.
- **`--dry-run` must not write to a temp file "to check".** That exercises a
  different path (a different directory, different permissions) and can report
  success where the real destination would fail.
