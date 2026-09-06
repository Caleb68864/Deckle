# M4 — Split `deckle/cli.py` into a package, without changing one byte of its behaviour

**Roadmap item:** `docs/ROADMAP.md` M4
**Depends on:** —
**Blocks:** B21, B22, B23, B24, B25, F5, N14 (see Traps — they either land before this or are rebased onto the package)
**Size:** M
**Decision needed first:** none

---

## 1. Context

`deckle/cli.py` is 1439 lines holding four unrelated jobs in one file:

- seven `argparse` `type=` callables and their regexes and unit tables
  (lines 68-138, 165-401);
- about 300 lines of load-or-report glue that turns a loader or project
  exception into a message on stderr and a log line (336-361, 404-448,
  464-561, 591-707);
- six subcommand bodies (865-1219);
- one 158-line `build_parser` (1222-1380).

Three duplications follow from that shape, all confirmed by grep:

- **`source` is defined five times**, character for character:
  `grep -n '"source", help=' deckle/cli.py` → lines 1244, 1251, 1309, 1317,
  1331, every one of them
  `add_argument("source", help="a PDF file or a directory of images")`.
- **`-o/--output` is defined five times**, with five different help strings
  and two different `required=` values (1245, 1252, 1319, 1333, 1365).
- **The `--crop` family is defined twice** with different prose: the long
  form under `_add_layout_args` (821-853) and a short form under
  `crop-preview` (1335-1346).

And one live inefficiency the roadmap names: **`build_parser()` is rebuilt
on every project load**, at `deckle/cli.py:639`:

```python
        ignored = _layout_flags_given(args, build_parser())
```

`_resolve_input` needs the subparser only to read its actions' defaults, and
constructs a complete second parser — six subparsers, every flag, every help
string — to get at one it already came from. It then walks
`parser._actions` looking for an `argparse._SubParsersAction` to index by
`args._command`, which is private argparse API used to rediscover a thing
the namespace could simply have carried.

None of this is broken today. What it costs is every item that has to edit
this file: B21 through B25 each touch a different hundred-line region, F5
adds six flags to `_add_layout_args`, and N14 adds two subcommands and a
`--json` flag to three more. Every one of them merges against the same file.

**This spec must not change behaviour.** Not an exit code, not a message,
not a help string, not the shape of the parsed `Namespace`. The acceptance
is the full suite plus `tests/test_output_command_parity.py` — the test that
asserts all five `-o` commands agree about validation, reporting and atomic
writes — passing **unchanged**.

## 2. Current code

### The dispatch chain

`deckle/cli.py:1416-1439`:

```python
def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI invocation.
    ...
    """
    _make_output_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

The `if __name__ == "__main__"` block at `:1438-1439` is what makes
`python -m deckle.cli` work. **A package cannot carry one**; `runpy` looks
for `deckle/cli/__main__.py`. That file does not exist yet and must be
created, or `python -m deckle.cli` fails with
`No module named deckle.cli.__main__; 'deckle.cli' is a package and cannot
be directly executed`. It is used by `run.bat:86` and by fourteen test
files through `subprocess.run([sys.executable, "-m", "deckle.cli", ...])`.

### The rebuilt parser

`deckle/cli.py:635-647`:

```python
    if _is_project_file(args.source):
        project = _load_project_or_report(args.source)
        if project is None:
            return None
        ignored = _layout_flags_given(args, build_parser())
        if ignored:
            print(
                "note: "
                + ", ".join(ignored)
                + f" ignored -- {args.source} carries its own layout.",
                file=sys.stderr,
            )
        return list(project.pages), project.layout
```

`deckle/cli.py:564-588`:

```python
def _layout_flags_given(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    """Which layout options the user actually typed, as flag names.
    ...
    """
    sub = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(getattr(args, "_command", ""))
            break
    if sub is None:
        return []
    given = []
    for action in sub._actions:
        if not action.option_strings or action.dest in ("output", "help", "source"):
            continue
        if getattr(args, action.dest, action.default) != action.default:
            given.append(action.option_strings[0])
    return sorted(given)
```

`_command` is already stashed on the namespace by every subparser —
`impose_parser.set_defaults(func=_cmd_impose, _command="impose")`
(`:1248`, and the same at `:1306, 1311, 1325, 1358, 1378`) — which is the
precedent step 6 below extends.

### The duplicated argparse

`deckle/cli.py:1243-1252` (impose and export, showing the shape all five
share):

```python
    impose_parser = subparsers.add_parser("impose", help="impose a source into a .deckle project")
    impose_parser.add_argument("source", help="a PDF file or a directory of images")
    impose_parser.add_argument("-o", "--output", required=True, help="path to write the .deckle project")
    impose_parser.add_argument("--printer", default=None, help="printer name to record in the project")
    _add_layout_args(impose_parser)
    impose_parser.set_defaults(func=_cmd_impose, _command="impose")

    export_parser = subparsers.add_parser("export", help="impose and export a source directly to PDF")
    export_parser.add_argument("source", help="a PDF file or a directory of images")
    export_parser.add_argument("-o", "--output", required=True, help="path to write the exported PDF")
```

The five `-o` help strings, verbatim:

| Subcommand | Line | `required=` | `help=` |
|---|---|---|---|
| `impose` | 1245 | `True` | `"path to write the .deckle project"` |
| `export` | 1252 | `True` | `"path to write the exported PDF"` |
| `schedule` | 1318-1323 | absent (so `False`), `default=None` | `"write the schedule to a file instead of stdout"` |
| `crop-preview` | 1332-1334 | `True` | `"image to write (e.g. overlay.png)"` |
| `dummy` | 1364-1366 | `True` | `"path to write the PDF"` |

The two `--crop` families, `deckle/cli.py:821-862` and `:1335-1346`. Both
are quoted in full in step 4, because their prose must survive verbatim.

### Everything tests reach for

From `grep -rn "from deckle.cli import" tests/` and
`grep -rn "\bcli\b" tests/`:

| Name | Imported by |
|---|---|
| `main` | `test_cli.py:14`, `test_cli_output_durability.py:34`, `test_cli_passes.py:30`, `test_cli_sheets.py:18`, `test_registration.py:271`, `test_dummy.py:101,121`, `test_hardening_io.py:29`, `test_auto_crop.py:172`, `test_composite.py:180`, `test_output_command_parity.py:33` |
| `build_parser` | `test_spec_residue.py:21` |
| `_parse_length_pt` | `test_cli.py:14` |
| `_parse_paper` | `test_cli_errors.py:248, 261, 273` |
| `MIN_PAPER_PT`, `MAX_PAPER_PT` | `test_cli_errors.py:285` |
| `_parse_sheet_selection` | `test_cli_sheets.py:18` |
| `_parse_offset_pair` | `test_registration.py:271` |
| `_strategy_for` | `test_paper_bounds.py:157`, `test_ui_surface.py:425`, `test_repeated_source_page.py:39` |
| `cli.sys` (attribute) | `test_hardening_platform.py:57-58, 70-71` |
| `cli._make_output_encoding_safe` | `test_hardening_platform.py:60, 73` |
| `cli._load_source` (**monkeypatched**) | `test_hardening_io.py:243` |

Plus `tests/test_cli.py:45-64`, which opens `deckle/cli.py` **as a file** and
AST-walks it for forbidden imports:

```python
    cli_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deckle", "cli.py")
    with open(cli_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=cli_path)
```

That path stops existing. Step 12 rewrites it.

### Everything else that names the module

- `run.bat:86` — `"%PY%" -m deckle.cli !ARGS!`. Unchanged by this spec,
  **provided** `deckle/cli/__main__.py` exists.
- `deckle/__main__.py:3` — docstring says "``deckle/cli.py`` remains the
  headless entry point".
- `README.md:410` — architecture list, `deckle/cli.py   headless entry point,
  imports only deckle.core`.
- `docs/CONTRIBUTING.md:20, 120` — both name `deckle/cli.py`.
- `deckle/app/views/layout_panel.py:201` — a comment pointing at
  "``deckle/cli.py``'s ``--paper`` presets".
- `docs/api/cli.rst` — `.. automodule:: deckle.cli`, listed in
  `docs/api/index.rst`'s toctree.
- `tests/test_core_purity.py:82-99` — imports `deckle.cli` **by module
  name** in a subprocess and asserts no Qt. Works either way.
- `tests/test_packaging_audit.py` — **does not** walk `deckle.cli` by module
  name; it only inspects `dist/deckle/` and `run.bat`, and every test in it
  that touches the bundle is `@requires_bundle`-skipped without one.
  Confirmed by reading the file.

### Packaging

There is **no `[project.scripts]` in `pyproject.toml`**, and no `setup.py`
or `setup.cfg` — confirmed. The `deckle` / `deckle-cli` executables
`tests/test_packaging_audit.py:126-135` looks for are produced by
PyInstaller from `packaging/deckle.spec`, which is absent from the tree
(roadmap R0.3). So there is no console-script entry point in `pyproject.toml`
to re-resolve; see Traps for what R0.3 must do instead.

`pyproject.toml:45-46` is:

```toml
[tool.setuptools.packages.find]
include = ["deckle*"]
```

`deckle.cli` matches `deckle*` under `fnmatchcase`, which is what setuptools
filters dotted package names with — verified:
`fnmatchcase('deckle.cli', 'deckle*')` is `True`. **No `pyproject.toml`
change is needed.**

### Documentation coverage

`tests/test_docs_coverage.py:29-45` maps `docs/api/<name>.rst` to
`deckle.<name>` and requires one page per module, each reachable from some
toctree. `pkgutil.walk_packages` yields packages as well as modules —
verified: it currently yields `deckle.app.views` — so after the split it
yields `deckle.cli`, `deckle.cli.values`, `deckle.cli.report`,
`deckle.cli.options` and `deckle.cli.commands`. `deckle.cli.__main__` is
skipped by the `if "__main__" in info.name: continue` filter at `:33-34`.
Four new `.rst` files are therefore required.

## 3. Change

Five modules, one `__main__`, five documentation pages, three test edits and
four prose edits. **Move code verbatim.** The only bodies that change are
the three named in steps 4 and 6; everything else is cut and pasted with its
docstring and comments intact.

### The module boundary, stated once

- **`values.py`** — turn one flag's text into a value. Every function here
  is an `argparse` `type=` callable or a helper of one, and the module
  imports nothing from `deckle`. That property is worth keeping: it is the
  layer M5 will lift `LETTER_PT` and `_UNIT_TO_PT` out of.
- **`report.py`** — turn a failure or a measurement into text on
  stdout/stderr and a log line. Every function here writes output; none of
  them loads, imposes or exports anything.
- **`commands.py`** — the six subcommand bodies and the glue they share.
  The `*_or_report` wrappers live here rather than in `report.py`, because
  each of them *performs* the operation as well as reporting it, and the
  operation is what decides where it belongs.
- **`options.py`** — declare flags. `_add_*` and `build_parser`, nothing
  else.
- **`__init__.py`** — `main`, its one-line preamble, and the re-exports.

The import graph is a chain with no cycles:
`values ← report ← commands ← options ← __init__`.

### Step-by-step

1. **`git mv deckle/cli.py deckle/cli/__init__.py`** — as the first commit
   of the change, so the diff of every later step is a move rather than a
   delete-and-add. (`mkdir deckle/cli` first.)

2. **Create `deckle/cli/values.py`.** Cut from `__init__.py`, in this
   order, keeping every docstring and comment:

   | From `cli.py` | Symbol |
   |---|---|
   | 68-70 | `LETTER_PT`, `A4_PT`, `LEGAL_PT` |
   | 72-76 | `_PAPER_PRESETS` |
   | 78 | `_ACCEPTED_LENGTH_UNITS` |
   | 80-85 | `_UNIT_TO_PT` |
   | 87-89 | `_LENGTH_RE` (with its A-9 comment) |
   | 92-108 | `_parse_length_pt` |
   | 111-132 | `_parse_paper` |
   | 135-138 | `MIN_PAPER_PT`, `MAX_PAPER_PT` (with their comment) |
   | 141-162 | `_reject_unprintable_paper` |
   | 201-228 | `_parse_paper_weight` |
   | 231-257 | `_parse_signature_lengths` |
   | 260-284 | `_parse_crop` |
   | 287-333 | `_parse_sheet_selection` |
   | 364-366 | `_SIGNED_LENGTH_RE` |
   | 369-401 | `_parse_offset_pair` |

   Imports: `from __future__ import annotations`, `argparse`, `re`. Nothing
   from `deckle`.

   Module docstring:

   ```python
   """Turning one flag's text into a value.

   Everything here is an ``argparse`` ``type=`` callable or a helper of one:
   given the string a user typed, either return the value or raise
   ``argparse.ArgumentTypeError`` with a message naming what was expected.
   No file is opened and nothing is printed -- a parse failure is argparse's
   to report, on the flag it happened to, while the user is still looking at
   what they typed.

   This module imports nothing from ``deckle``. That is deliberate and worth
   keeping: the unit table and the paper presets here are duplicated in
   ``deckle/app/views/layout_panel.py`` and are what spec M5 consolidates
   into ``deckle.core.paper``, and a leaf module is the easiest thing to
   lift out from under.
   """
   ```

3. **Create `deckle/cli/report.py`.** Cut:

   | From `cli.py` | Symbol |
   |---|---|
   | 336-361 | `_report_missing_sheets` |
   | 404-417 | `_report_registration` |
   | 420-448 | `_resolve_profile` |
   | 616-618 | `_format_insets` |
   | 684-696 | `_report_output_problem` |
   | 699-706 | `_report_write_failure` |
   | 901-920 | `_emit_warnings` |
   | 1031-1052 | `_report_rule` |

   Imports: `sys`; `from deckle.core.diagnostics import log_event,
   log_exception`; `from deckle.core.export import proof_rule_length_pt`;
   `from deckle.core.outputs import describe_write_failure,
   output_path_problem`; `from deckle.core.profiles import BUILTIN_PRESETS,
   PrinterProfile`.

   Module docstring:

   ```python
   """Saying what went wrong, or what was measured, and recording it.

   Every function here writes to stdout or stderr and, where the event is
   worth a bug report, calls into ``deckle.core.diagnostics``. None of them
   loads, imposes or exports anything -- the ``*_or_report`` wrappers that
   do both live in :mod:`deckle.cli.commands`, beside the operation they
   wrap.

   The split that matters to a user is which stream a line goes to.
   Warnings, notes and errors go to **stderr** so ``deckle export`` keeps a
   clean stdout for scripting; the one thing a command has to say about what
   it did -- ``wrote out.pdf`` -- goes to stdout. Keeping both in one module
   is what makes that rule checkable by reading it.
   """
   ```

4. **Create `deckle/cli/options.py`.** Cut `_VERSIONED_DISTRIBUTIONS`
   (46-51), `_distribution_version` (54-58), `_version_string` (61-66),
   `_add_layout_args` (746-862) and `build_parser` (1222-1380). Then add the
   three new helpers **above** `_add_layout_args`:

   ```python
   def _add_source_arg(parser: argparse.ArgumentParser) -> None:
       """The positional every command that reads a document takes.

       Five subcommands defined this identically, which is five places to
       forget when a ``.deckle`` becomes acceptable somewhere new -- it
       already is acceptable everywhere, and the help string is the only
       thing that says so.
       """
       parser.add_argument("source", help="a PDF file or a directory of images")


   def _add_output_arg(
       parser: argparse.ArgumentParser, *, required: bool, help_text: str
   ) -> None:
       """``-o/--output``, whose only real variation is whether it is required.

       The help text differs per command and is passed in rather than
       generated: what ``-o`` produces is the most useful thing a subcommand's
       ``--help`` can say about it, and ``schedule`` writing to stdout when it
       is omitted is not derivable from anything here.

       :param required: ``False`` only for ``schedule``, which prints to
           stdout when the flag is absent.
       :param help_text: the command's own wording, verbatim.
       """
       parser.add_argument(
           "-o", "--output", required=required, default=None, help=help_text
       )


   def _add_crop_args(
       parser: argparse.ArgumentParser,
       *,
       crop_help: str,
       auto_crop_help: str,
       auto_crop_margin_help: str,
       crop_even_help: str | None,
   ) -> None:
       """The ``--crop`` family: the same four flags, twice, worded differently.

       What was duplicated is the plumbing -- ``type=_parse_crop``,
       ``default=None``, ``metavar="L,B,R,T"`` -- not the prose. The two call
       sites are asking genuinely different questions: on a layout command
       ``--crop`` changes the book, and on ``crop-preview`` it only draws a
       rectangle on a picture. So every help string is passed in, verbatim,
       and this function decides nothing a user can see.

       :param crop_even_help: ``None`` omits ``--crop-even`` entirely, which
           is what ``crop-preview`` wants: it composites one parity at a time,
           so a second rectangle would have nothing to be drawn over.
       """
       parser.add_argument(
           "--crop", type=values._parse_crop, default=None, metavar="L,B,R,T",
           help=crop_help,
       )
       parser.add_argument("--auto-crop", action="store_true", help=auto_crop_help)
       parser.add_argument(
           "--auto-crop-margin", type=values._parse_length_pt, default=0.0,
           metavar="LENGTH", help=auto_crop_margin_help,
       )
       if crop_even_help is not None:
           parser.add_argument(
               "--crop-even", type=values._parse_crop, default=None,
               metavar="L,B,R,T", help=crop_even_help,
           )
   ```

   **Ordering is behaviour here.** `--help` lists options in the order they
   were added, and `tests/test_cli_errors.py` and `test_project_cli.py`
   assert on message text. In `_add_layout_args` the four crop flags
   currently appear at positions `--crop`, `--auto-crop`,
   `--auto-crop-margin`, `--crop-even` — lines 821, 830, 838, 846 — i.e.
   between `--sewing-stations` (817) and `--trim` (854), and in exactly the
   order `_add_crop_args` adds them. So replacing lines 821-853 with a single
   `_add_crop_args(...)` call at that point preserves the listing exactly.
   In `crop-preview` the order is `--crop`, `--auto-crop`,
   `--auto-crop-margin` (1335, 1339, 1343), then `--parity` and `--dpi`,
   which is also preserved.

   The two call sites, with the existing prose moved verbatim:

   ```python
       _add_crop_args(
           parser,
           crop_help=(
               "remove space from every source page before imposing -- insets "
               "from the left, bottom, right and top, e.g. 0.5in,0.25in,"
               "0.5in,0.25in. Cropping a scan's wide margins is what lets the "
               "type stay readable at a small trim size"
           ),
           auto_crop_help=(
               "measure the crop from where the ink actually is, instead of "
               "typing it. Odd and even pages are measured separately. Prints "
               "the values it found so you can pin them with --crop"
           ),
           auto_crop_margin_help=(
               "keep this much back from every edge found by --auto-crop, "
               "for descenders and hairline rules a low-dpi scan can miss"
           ),
           crop_even_help=(
               "a different crop for even-numbered pages, for a scan whose "
               "gutter swaps sides every leaf. Without this, --crop applies "
               "to the whole document"
           ),
       )
   ```

   ```python
       _add_crop_args(
           preview_parser,
           crop_help="draw this crop on the composite",
           auto_crop_help="measure the crop and draw what it found",
           auto_crop_margin_help="keep this much back from every measured edge",
           crop_even_help=None,
       )
   ```

   And the five `-o` sites become, in place:

   ```python
       _add_output_arg(impose_parser, required=True,
                       help_text="path to write the .deckle project")
       _add_output_arg(export_parser, required=True,
                       help_text="path to write the exported PDF")
       _add_output_arg(schedule_parser, required=False,
                       help_text="write the schedule to a file instead of stdout")
       _add_output_arg(preview_parser, required=True,
                       help_text="image to write (e.g. overlay.png)")
       _add_output_arg(dummy_parser, required=True,
                       help_text="path to write the PDF")
   ```

   with `_add_source_arg(<parser>)` replacing each of the five `source`
   lines. `dummy` takes no source and gets none.

   Imports: `argparse`, `importlib.metadata`; `from deckle import __version__
   as _DECKLE_VERSION`; `from deckle.core.paper import GRADE_BASIS_SIZES_IN,
   PAPER_BULK`; `from deckle.core.profiles import BUILTIN_PRESETS`;
   `from deckle.cli import commands, values`.

   Module docstring:

   ```python
   """Declaring the flags -- and only declaring them.

   One subcommand's options are the closest thing Deckle has to a contract
   with a script, so this module holds nothing but ``add_argument`` calls,
   the ``_add_*`` helpers that group them, and ``build_parser``. Anything
   that inspects a value belongs in :mod:`deckle.cli.values`; anything that
   acts on one belongs in :mod:`deckle.cli.commands`.

   ``--help`` lists options in the order they were added, so the order of
   the calls here is user-visible and is not free to be tidied.
   """
   ```

5. **Create `deckle/cli/commands.py`.** Cut:

   | From `cli.py` | Symbol |
   |---|---|
   | 165-198 | `_paper_thickness_from_args` |
   | 451-461 | `_pass_for` |
   | 464-475 | `_load_source` |
   | 478 | `PROJECT_SUFFIX` |
   | 481-491 | `_is_project_file` |
   | 494-561 | `_load_project_or_report` |
   | 564-588 | `_layout_flags_given` (body changed in step 6) |
   | 591-613 | `_apply_auto_crop` |
   | 621-664 | `_resolve_input` (one line changed in step 6) |
   | 667-681 | `_load_source_or_report` |
   | 709-732 | `_build_layout_settings` |
   | 735-743 | `_strategy_for` |
   | 865-898 | `_cmd_info` |
   | 923-949 | `_impose_or_report` |
   | 952-1028 | `_cmd_export` |
   | 1055-1077 | `_cmd_impose` |
   | 1080-1122 | `_cmd_schedule` |
   | 1125-1189 | `_cmd_crop_preview` |
   | 1192-1219 | `_cmd_dummy` |

   Imports: `argparse`, `dataclasses`, `json`, `os`, `sys`, `warnings`;
   `from deckle.core.diagnostics import log_event, log_exception`;
   `from deckle.core.export import export as export_plan`;
   `from deckle.core.layout import GutterShiftStrategy, LayoutStrategy,
   SaddleStitchStrategy`; `from deckle.core.loader import SourceLoadError,
   load_image_dir, load_pdf`; `from deckle.core.models import LayoutSettings,
   Project, SourcePage`; `from deckle.core.outputs import
   describe_write_failure`; `from deckle.core.paper import
   GRADE_BASIS_SIZES_IN, caliper_pt_from_gsm, gsm_from_pounds`;
   `from deckle.core.paths import atomic_output, write_text_atomic`;
   `from deckle.core.printing import plan_passes`;
   `from deckle.core.project_io import PathOutsideRootsAdvisory,
   SourceChangedWarning, SourceMissingError, load_project, save_project`;
   `from deckle.core.schedule import build_schedule, format_schedule_text`;
   `from deckle.cli import report`.

   **Keep the two function-local imports exactly where they are**:
   `from deckle.core.render import auto_crop_insets` inside `_apply_auto_crop`
   (`:599`), and `from PIL import Image` / `from deckle.core.render import
   auto_crop_insets, composite_pages` inside `_cmd_crop_preview`
   (`:1127-1129`), and `from deckle.core.dummy import make_numbered_pdf`
   inside `_cmd_dummy` (`:1194`). `deckle.core.render` imports `pypdfium2`;
   hoisting it to module scope makes every `deckle info` pay for the
   rasteriser.

   Every reference to a moved report function becomes `report.<name>` —
   `report._emit_warnings(pages, plan)`, `report._impose_or_report` **no**
   (that one moves here), `report._report_output_problem(...)`,
   `report._report_write_failure(...)`, `report._report_missing_sheets(...)`,
   `report._resolve_profile(...)`, `report._report_registration(...)`,
   `report._report_rule(...)`, `report._format_insets(...)`. Similarly
   `values._parse_*` where a command names one — grep the moved bodies for
   each name rather than trusting this list.

   Module docstring:

   ```python
   """The six subcommands, and the glue they share.

   Every ``_cmd_*`` here has the same shape and it is worth stating once:
   check the destination before doing any work, resolve the input, impose,
   say what was noticed, write, and say what was written. The order is not
   arbitrary -- validating ``-o`` first is why imposing a 300-page book and
   *then* discovering the output folder does not exist is not something
   Deckle does.

   The ``*_or_report`` wrappers live here rather than in
   :mod:`deckle.cli.report` because each of them performs the operation as
   well as reporting it, and the operation is what decides where it belongs.
   """
   ```

6. **Remove the rebuilt parser.** Three edits, all in `commands.py`:

   a. In `build_parser` (`options.py`), extend each subparser's existing
      `set_defaults` with a reference to itself:

      ```python
       impose_parser.set_defaults(
           func=commands._cmd_impose, _command="impose", _subparser=impose_parser
       )
      ```

      and the same for `export_parser`, `info_parser`, `schedule_parser`,
      `preview_parser` and `dummy_parser`. The namespace already carries
      `_command` this way, so this is the established mechanism, not a new
      one.

   b. `_layout_flags_given` loses its `parser` argument and its walk of
      `parser._actions`:

      ```python
      def _layout_flags_given(args: argparse.Namespace) -> list[str]:
          """Which layout options the user actually typed, as flag names.

          :param args: the parsed arguments. The subparser they came from is
              on the namespace as ``_subparser``, put there by
              ``build_parser``.
          :returns: the flags whose value differs from the default.

          Used to warn rather than silently ignore. Comparing against defaults
          is approximate -- typing the default value looks like not typing it
          -- but it errs toward silence, which is the right direction for a
          warning.

          This used to take a parser and be handed a **freshly built one**:
          six subparsers and every flag and help string in the program,
          constructed on every ``.deckle`` load, then walked for an
          ``argparse._SubParsersAction`` and indexed by ``args._command`` to
          rediscover the subparser the arguments had just come out of.
          ``set_defaults`` carries the subparser itself instead, which is how
          ``_command`` already reached here.
          """
          sub = getattr(args, "_subparser", None)
          if sub is None:
              return []
          given = []
          for action in sub._actions:
              if not action.option_strings or action.dest in ("output", "help", "source"):
                  continue
              if getattr(args, action.dest, action.default) != action.default:
                  given.append(action.option_strings[0])
          return sorted(given)
      ```

   c. In `_resolve_input`, `deckle/cli.py:639` becomes:

      ```python
              ignored = _layout_flags_given(args)
      ```

   The result is identical: the same subparser object, the same actions, the
   same defaults, compared the same way. `_subparser` is a `set_defaults`
   value and therefore never appears in `sub._actions`, so it cannot report
   itself as a given flag.

7. **Reduce `deckle/cli/__init__.py`** to the docstring, `main`,
   `_make_output_encoding_safe`, and the re-exports:

   ```python
   """Deckle's headless CLI.

   A shipped MVP feature, not a test harness -- it is also what lets the
   golden-fixture regression run headlessly in CI, since ``deckle.core`` is
   Qt-free. Subcommands: ``impose``, ``export``, ``info``, ``schedule``,
   ``crop-preview``, ``dummy``.

   Imports only the pure core package -- never the Qt-based desktop app layer
   or any Qt binding -- so it stays runnable in a headless container with no
   display server present. ``tests/test_cli.py`` asserts that statically over
   every file in this package.

   Four modules, in the order a command travels through them:
   :mod:`~deckle.cli.values` turns a flag's text into a value,
   :mod:`~deckle.cli.options` declares the flags,
   :mod:`~deckle.cli.commands` does the work, and
   :mod:`~deckle.cli.report` says what happened. This module holds ``main``
   and re-exports the names that were importable from ``deckle.cli`` when it
   was one file, so ``from deckle.cli import main`` still means what it
   always did.

   ``python -m deckle.cli`` runs it; ``python -m deckle`` launches the GUI
   and blocks. See :mod:`deckle.cli.__main__`.
   """

   from __future__ import annotations

   import sys
   from typing import Sequence

   from deckle.cli import commands, options, report, values
   from deckle.cli.commands import (
       PROJECT_SUFFIX,
       _build_layout_settings,
       _is_project_file,
       _load_source,
       _resolve_input,
       _strategy_for,
   )
   from deckle.cli.options import build_parser
   from deckle.cli.values import (
       A4_PT,
       LEGAL_PT,
       LETTER_PT,
       MAX_PAPER_PT,
       MIN_PAPER_PT,
       _parse_crop,
       _parse_length_pt,
       _parse_offset_pair,
       _parse_paper,
       _parse_paper_weight,
       _parse_sheet_selection,
       _parse_signature_lengths,
   )
   ```

   followed by `_make_output_encoding_safe` (cut from `:1383-1413`, keeping
   its whole docstring) and `main` (cut from `:1416-1435`), and **no**
   `if __name__ == "__main__":` block — a package's is `__main__.py`.

   `import sys` is load-bearing beyond `main`'s use of it:
   `tests/test_hardening_platform.py:57-58, 70-71` monkeypatch
   `cli.sys.stdout`. That resolves through this module's `sys` binding to the
   real `sys` module, so it keeps working — but only if the name exists here.

   The re-export list is exactly the union of what tests import today
   (section 2's table) and the module-level names a reader would reasonably
   expect from the old single file. `_load_source` is re-exported for
   completeness but see step 11: a re-export is a *different binding* and
   cannot be monkeypatched.

8. **Create `deckle/cli/__main__.py`**, verbatim:

   ```python
   """``python -m deckle.cli`` -- the headless entry point.

   ``deckle.cli`` used to be a single module and carried its own
   ``if __name__ == "__main__"`` block. A package cannot: ``runpy`` looks for
   a ``__main__`` submodule instead, and without this file
   ``python -m deckle.cli`` fails with "'deckle.cli' is a package and cannot
   be directly executed" -- which would break ``run.bat cli`` and every test
   that shells out to the CLI.

   ``python -m deckle`` launches the GUI and blocks; this is the one to run
   from a script or from CI.
   """

   from __future__ import annotations

   import sys

   from deckle.cli import main

   if __name__ == "__main__":
       sys.exit(main())
   ```

9. **Replace `docs/api/cli.rst`** with a package overview carrying a
   toctree, in the shape of `docs/api/core.rst` and `docs/api/app.rst`:

   ```rst
   deckle.cli -- the headless command line
   =======================================

   .. automodule:: deckle.cli
      :members:
      :show-inheritance:

   Reading order
   -------------

   The modules below are listed in the order one invocation travels through
   them: the flags are declared (:mod:`~deckle.cli.options`), each flag's
   text becomes a value (:mod:`~deckle.cli.values`), a subcommand does the
   work (:mod:`~deckle.cli.commands`), and whatever it noticed is said out
   loud (:mod:`~deckle.cli.report`).

   .. toctree::
      :maxdepth: 1

      cli.options
      cli.values
      cli.commands
      cli.report
   ```

10. **Create four pages**, each in the two-line form every other module page
    uses (`docs/api/core.paths.rst` is the model):

    `docs/api/cli.values.rst`:

    ```rst
    deckle.cli.values
    =================

    .. automodule:: deckle.cli.values
       :members:
       :show-inheritance:
    ```

    and the same for `cli.options.rst`, `cli.commands.rst`, `cli.report.rst`
    with their own titles and underlines. `docs/api/index.rst` needs no
    change: it already lists `cli`, which is now the overview page.

11. **`tests/test_hardening_io.py:233-249`** —
    `test_a_rejected_output_path_costs_no_imposition_work` monkeypatches
    `cli._load_source`. `_load_source_or_report` calls `_load_source` as a
    module global of `deckle.cli.commands`, and `deckle.cli._load_source` is
    a *separate binding* created by the re-export, so patching it would bind
    nothing and the tripwire would silently stop tripping — the test asserts
    `rc == 1` and a message that arrive before the loader is reached, so it
    would keep passing while guarding nothing. Repoint it:

    ```python
        from deckle.cli import commands

        def fail_if_called(path):
            raise AssertionError("the source was loaded despite a bad output path")

        monkeypatch.setattr(commands, "_load_source", fail_if_called)
    ```

    and add one sentence to its docstring saying the patch must target the
    module that *calls* the name, not the package that re-exports it.

12. **`tests/test_cli.py:45-64`** —
    `test_cli_imports_only_deckle_core_not_app_or_qt` opens
    `deckle/cli.py`. Rewrite it to walk every `.py` under `deckle/cli/`:

    ```python
    def test_cli_imports_only_deckle_core_not_app_or_qt():
        """Statically verify no file under deckle/cli/ imports deckle.app or Qt.

        This is what keeps the CLI runnable in a headless CI container with no
        display server present. It walks the package rather than naming one
        file: ``deckle/cli.py`` became ``deckle/cli/``, and a check that names
        a single path would have gone quietly vacuous instead of failing.
        """
        package_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "deckle", "cli"
        )
        sources = sorted(
            os.path.join(package_dir, name)
            for name in os.listdir(package_dir)
            if name.endswith(".py")
        )
        assert sources, "no modules found under deckle/cli/"

        forbidden_prefixes = ("deckle.app", "PySide6", "PyQt5", "PyQt6")
        for path in sources:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            imported_names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_names.append(node.module)
            for name in imported_names:
                assert not name.startswith(forbidden_prefixes), (
                    f"forbidden import in {os.path.basename(path)}: {name}"
                )
    ```

    The `assert sources` line is the point: the previous shape would have
    passed on an empty file list.

13. **New test file `tests/test_cli_package.py`** — see section 4.

14. **Prose, four files.** None of these is optional: they name a path that
    stops existing.

    - `deckle/__main__.py:3` — "``deckle/cli.py`` remains the headless entry
      point" → "``deckle/cli/`` remains the headless entry point
      (``python -m deckle.cli``)".
    - `README.md:410` — `deckle/cli.py   headless entry point, imports only
      deckle.core` → `deckle/cli/     headless entry point, imports only
      deckle.core`, keeping the column alignment of the block it sits in.
    - `docs/CONTRIBUTING.md:20` and `:120` — both `deckle/cli.py` →
      `deckle/cli/`.
    - `deckle/app/views/layout_panel.py:201` — the comment pointing at
      "``deckle/cli.py``'s ``--paper`` presets" → "``deckle/cli/values.py``'s
      ``--paper`` presets". (M5 moves them again; leaving the comment wrong
      in the meantime is how the next reader concludes the presets are not
      duplicated.)

## 4. Tests

The suite is the specification here: **1507 passing before, 1507 passing
after, with only the three edits in steps 11-12 and no assertion changed.**
Three new tests pin the properties a file-to-package move can break without
any existing test noticing.

**File:** `tests/test_cli_package.py` (new)

```python
"""``deckle.cli`` is a package now, and the ways that can break are quiet ones.

Splitting a module into a package changes three things no existing test was
watching. ``python -m`` stops working, because a package has no
``if __name__ == "__main__"`` -- and it fails at the shell, which most of the
suite reaches through ``subprocess`` and would report as a puzzling
non-zero exit. A name that used to be a module global becomes a re-export,
which is a *different binding*, so a ``monkeypatch.setattr`` aimed at the
package silently patches nothing. And the import graph gains cycles very
easily once four modules refer to each other.

None of those changes a single command's behaviour, which is why they need
their own tests rather than being caught by the ones that already exist.
"""
```

### `test_python_dash_m_deckle_cli_still_runs`

- **Setup:** `subprocess.run([sys.executable, "-m", "deckle.cli",
  "--version"], capture_output=True, text=True)`.
- **Assertion in words:** the exit code is 0 and the output names the Deckle
  version — the package is executable, so `run.bat cli` and the fourteen
  test files that shell out still work.
- **Expected failure without `deckle/cli/__main__.py`:** exit code 1 and
  stderr `No module named deckle.cli.__main__; 'deckle.cli' is a package and
  cannot be directly executed`.

### `test_every_name_the_tests_import_is_still_on_the_package`

- **Setup:** none; import `deckle.cli`.
- **Assertion in words:** each of `main`, `build_parser`,
  `_make_output_encoding_safe`, `_parse_length_pt`, `_parse_paper`,
  `_parse_sheet_selection`, `_parse_offset_pair`, `_strategy_for`,
  `MIN_PAPER_PT`, `MAX_PAPER_PT` and `sys` is an attribute of the
  `deckle.cli` module. Written as an explicit list with a message naming the
  test file that imports each one, so a failure says which suite is about to
  break rather than only which name is missing.
- **Expected failure on a partial split:** `AssertionError: deckle.cli no
  longer exports '_parse_offset_pair' (tests/test_registration.py:271
  imports it)`.

### `test_the_cli_modules_import_in_one_direction`

- **Setup:** AST-walk each `.py` under `deckle/cli/`, collecting every
  `deckle.cli.*` module it imports.
- **Assertion in words:** the edges respect the declared order —
  `values` imports none of the others; `report` imports at most `values`;
  `commands` imports at most `values` and `report`; `options` imports at
  most `values`, `report` and `commands`. A cycle here would not fail at
  import time (Python tolerates plenty of them) and would fail later, on
  whichever module happened to be imported first.
- **Expected failure if `commands` imports `options`:**
  `AssertionError: deckle/cli/commands.py imports deckle.cli.options, which is downstream of it`

### The behaviour-preservation checks

These are existing files, run unchanged. Name them explicitly in the commit
message, because "the suite passes" is the entire acceptance:

- `tests/test_output_command_parity.py` — the five `-o` commands agreeing
  about path validation, failure reporting and atomic writes. If
  `_add_output_arg` got `required=` wrong for one command, or
  `_report_output_problem` moved but a caller was missed, this is what says
  so.
- `tests/test_project_cli.py:114-122` — `_layout_flags_given` reaching the
  user: `deckle info project.deckle --gutter 2in` must still print
  `--gutter` and `ignored` on stderr. This is the direct test of step 6.
- `tests/test_spec_residue.py:450-475` — `build_parser()` accepting the
  folio flags on every layout subcommand.
- `tests/test_cli_errors.py`, `tests/test_cli_paper.py`,
  `tests/test_cli_sheets.py`, `tests/test_cli_passes.py`,
  `tests/test_hardening_platform.py`, `tests/test_core_purity.py`.

## 5. Acceptance

| Check | Command |
|---|---|
| The package is executable | `.venv/bin/python -m deckle.cli --version` |
| Every previously importable name still is | `.venv/bin/python -c "import deckle.cli as c; [getattr(c, n) for n in ('main','build_parser','_make_output_encoding_safe','_parse_length_pt','_parse_paper','_parse_sheet_selection','_parse_offset_pair','_strategy_for','MIN_PAPER_PT','MAX_PAPER_PT','sys')]; print('OK')"` |
| The old single file is gone | `! test -f deckle/cli.py` |
| The five modules exist | `test -f deckle/cli/__init__.py -a -f deckle/cli/__main__.py -a -f deckle/cli/values.py -a -f deckle/cli/report.py -a -f deckle/cli/options.py -a -f deckle/cli/commands.py` |
| No module rebuilds the parser to read defaults | `! grep -rn "_layout_flags_given(args, build_parser())" deckle/` |
| `build_parser` is called exactly once per invocation | `! grep -rn "build_parser()" deckle/cli/commands.py deckle/cli/report.py deckle/cli/values.py` |
| `source` is declared once | `test "$(grep -rho '"source", help=' deckle/cli/ \| wc -l)" = "1"` |
| `-o` is declared once | `test "$(grep -rho '"-o", "--output"' deckle/cli/ \| wc -l)" = "1"` |
| The new package tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_package.py` |
| Command parity is unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_output_command_parity.py` |
| Every CLI suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli.py tests/test_cli_errors.py tests/test_cli_paper.py tests/test_cli_passes.py tests/test_cli_sheets.py tests/test_cli_output_durability.py tests/test_project_cli.py tests/test_spec_residue.py tests/test_hardening_io.py tests/test_hardening_platform.py tests/test_core_purity.py` |
| Every module has a documentation page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| The packaging audit is unaffected | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_packaging_audit.py tests/test_run_bat.py` |
| No prose still names the old path | `! grep -rn "deckle/cli\.py" README.md docs/CONTRIBUTING.md deckle/ tests/` |
| The wheel still picks the subpackage up | `.venv/bin/python -c "from fnmatch import fnmatchcase; assert fnmatchcase('deckle.cli','deckle*'); print('OK')"` |
| `--help` output is byte-identical | see below |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two R0.3/R0.4 failures from `00-environment.md` and nothing else) |

**The `--help` check, run before and after the change.** This is the
strongest single statement of "behaviour-preserving", because `--help` is
the one output that depends on argparse's internal ordering and on every
help string:

```bash
# BEFORE the change, on a clean checkout of the parent commit:
for c in "" impose export info schedule crop-preview dummy; do
  echo "== $c"; .venv/bin/python -m deckle.cli $c --help
done > /tmp/deckle-help-before.txt 2>&1

# AFTER:
for c in "" impose export info schedule crop-preview dummy; do
  echo "== $c"; .venv/bin/python -m deckle.cli $c --help
done > /tmp/deckle-help-after.txt 2>&1

diff -u /tmp/deckle-help-before.txt /tmp/deckle-help-after.txt
```

`diff` must print nothing and exit 0. Capture `before` first — once
`deckle/cli.py` is gone it cannot be regenerated without a checkout.

Checked against the current tree: `grep -rn "deckle/cli\.py" README.md
docs/CONTRIBUTING.md deckle/ tests/` currently matches six lines
(`docs/CONTRIBUTING.md:20,120`, `README.md:410`, `deckle/__main__.py:3`,
`deckle/app/views/layout_panel.py:201`, `tests/test_cli.py:46`), so the `!`
form correctly fails today. `grep -rn "_layout_flags_given(args,
build_parser())" deckle/` currently matches `deckle/cli.py:639`.
`grep -rho '"source", help=' deckle/ | wc -l` currently gives 5. The `-o`
grep currently gives **4**, not 5, because `schedule`'s is split across two
lines (`deckle/cli.py:1318-1323`) — which is itself a small demonstration
that counting occurrences of a formatting accident is not the same as
counting declarations. Both rows are still worth having: after the change
each must be exactly 1, and 1 is what the grep sees however the call is
wrapped.

## 6. Out of scope

Everything that would change what the program does. Specifically:

- **B21** (`_resolve_profile`'s message and the dialog's narrower `except`),
  **B22** (`--profile` without `--pass`), **B23** (`.deckle` load swallowing
  warnings), **B24** (`_layout_flags_given` reporting non-layout flags),
  **B25** (`_parse_paper` rejecting `cm`). Each of these lands in a function
  this spec *moves*. Move it unchanged; fix it in its own commit. In
  particular do **not** fix B24 while rewriting `_layout_flags_given` in
  step 6 — the dest exclusion list stays exactly
  `("output", "help", "source")`.
- **F5** — the six missing margin flags. `_add_layout_args` is moved, not
  extended.
- **N14** — `--json`, `--dry-run`, `deckle profile`, `deckle print`.
- **M5** — one `PAPER_SIZES` and one `caliper_from_weight()` in
  `core/paper.py`. `LETTER_PT`, `_UNIT_TO_PT` and `_PAPER_PRESETS` move into
  `values.py` unchanged; M5 is what lifts them out of the CLI entirely.
- **M7** — `_command` on the namespace becomes unread once step 6 lands
  (`_layout_flags_given` was its only reader). Leave it: it is part of the
  parsed `Namespace`'s observable shape, and removing it is a behaviour
  change, however small. If it is still dead when M7 runs, that is M7's call.
- **R0.3** — committing `packaging/deckle.spec`. See Traps for the one line
  in it this spec affects.
- Do not add `[project.scripts]` to `pyproject.toml`. There is none today
  and adding one is a packaging decision (F13), not a consequence of moving
  files.
- Do not rename any private function. `_cmd_export` staying `_cmd_export`
  is what makes the move reviewable as a move.

## 7. decisions.md entry

```
## 2026-09-05 — cli.py became a package, and nothing it does changed
- Symptom: 1439 lines holding four unrelated jobs — seven argparse value parsers, ~300 lines of load-or-report glue, six command bodies and a 158-line `build_parser`. `source` was declared five times character for character, `-o` five times with five help strings, and the `--crop` family twice with different prose. `_resolve_input` built a **complete second parser** on every `.deckle` load — six subparsers, every flag — then walked `parser._actions` for a private `_SubParsersAction` and indexed it by `args._command`, to rediscover the subparser the arguments had just come out of.
- Fix: `deckle/cli/` — `values` (text to value, importing nothing from deckle), `report` (say what happened), `commands` (do the work), `options` (declare the flags), with `main` and the old module-level names re-exported from `__init__`. `_add_source_arg`, `_add_output_arg(required=, help_text=)` and `_add_crop_args(crop_help=, ...)` deduplicate the plumbing and pass every help string through verbatim, because the prose is what differs and is user-visible. `build_parser` now stashes each subparser on its own namespace via `set_defaults(_subparser=...)`, the same mechanism that already carried `_command`, so `_layout_flags_given` takes only the args.
- Surfaces: A package has no `if __name__ == "__main__"`, so `python -m deckle.cli` needed `deckle/cli/__main__.py` — without it `run.bat cli` and fourteen test files that shell out fail at the shell. And a re-export is a **different binding**: `tests/test_hardening_io.py` monkeypatched `cli._load_source` as a tripwire, and after the move that patch would have bound a name nothing calls — the test asserts an error that arrives *before* the loader, so it would have kept passing while guarding nothing. Repointed at `deckle.cli.commands`. `tests/test_cli.py`'s Qt-import check read `deckle/cli.py` as a file and now walks the package, with an `assert sources` so it cannot go vacuous the same way twice.
- Watch: The acceptance was the suite, unchanged, plus a byte-for-byte `diff` of `--help` for the top level and all six subcommands, captured before the move and again after. `--help` is the one output that depends on argparse's internal ordering and on every help string at once, which is exactly what a "pure move" of a parser is most likely to disturb. **Nothing else in the change would have caught a reordered flag.**
- Commit: (this commit)
```

## 8. Traps

- **Landing order. This is the important one.** M4 moves every line of
  `deckle/cli.py`. Every other spec that edits that file — **B21**
  (`:420-447`), **B22** (`:976-996`), **B23** (`:507-519`), **B24**
  (`:564-588`), **B25** (`:122-132`), **B35 §5** (`_cmd_schedule`,
  `:1090-1096`), **F5** (`_add_layout_args`), **N14** (new subcommands), and
  **M5** (`:68-85`) — must land **either before M4, or be rebased onto the
  package layout**. There is no third option: a diff
  written against `deckle/cli.py` will not apply to a tree where that file
  does not exist, and git will not resolve it for you. If those items are in
  flight in parallel, land them first and rebase M4, which is the cheaper
  direction — M4 is a mechanical move and reapplying it is a matter of
  re-cutting the same blocks, while reapplying a behaviour fix means
  re-deciding it. The destination for each is in section 5's table:
  `_resolve_profile` → `report.py`, `_cmd_export` → `commands.py`,
  `_load_project_or_report` → `commands.py`, `_layout_flags_given` →
  `commands.py`, `_cmd_schedule` → `commands.py`, `_parse_paper` →
  `values.py`, `_add_layout_args` → `options.py`.

  Note that **B35 §5's acceptance row is an `awk` over `deckle/cli.py`**
  (`docs/specs/2026-09-04-roadmap/B35-miscellany.md:1560`), which stops
  matching anything once the file is a package. If B35 lands first, M4 must
  repoint that row at `deckle/cli/commands.py`; if M4 lands first, B35 must
  be written against the new path. Either way it is a silent pass, not a
  failure, so it will not announce itself.
- **`python -m deckle` launches the GUI and blocks.** Every command in this
  spec uses `python -m deckle.cli`.
- **A package cannot carry `if __name__ == "__main__"`.** Step 8 is not
  optional and is not covered by any existing test — that is why
  `tests/test_cli_package.py` exists.
- **A re-export is a different binding.** `deckle.cli._load_source` and
  `deckle.cli.commands._load_source` are two names for one function object,
  and rebinding the first does not affect what `_load_source_or_report`
  calls. Step 11 is the one known case; grep for `monkeypatch.setattr(cli,`
  before finishing, and note that such a test typically keeps *passing*
  while guarding nothing, so a green suite is not evidence you found them
  all.
- **`--help` ordering is user-visible.** argparse lists options in the order
  they were added. `_add_crop_args` must be called at the exact point in
  `_add_layout_args` where `--crop` currently appears (between
  `--sewing-stations` and `--trim`), or the listing changes and the change
  is no longer behaviour-preserving. The `diff` in section 5 is what proves
  it.
- **Keep the function-local imports local.** `deckle.core.render` pulls in
  `pypdfium2`, and `PIL` and `deckle.core.dummy` are similarly deferred.
  Hoisting them to module scope while "tidying imports" makes every
  `deckle info` pay for the rasteriser and would be a real regression that
  no test asserts against.
- **`deckle/cli/__init__.py` must `import sys`.**
  `tests/test_hardening_platform.py:57-58, 70-71` monkeypatch
  `cli.sys.stdout`; the attribute has to resolve.
- **`tests/test_docs_coverage.py` needs four new `.rst` files and each must
  be reachable from a toctree.** `deckle.cli` itself stays in
  `_package_modules()` — `pkgutil.walk_packages` yields packages, verified
  against `deckle.app.views` — so `docs/api/cli.rst` remains valid and must
  **not** be added to the stale-page exemption set at
  `tests/test_docs_coverage.py:68`.
- **`run.bat docs` builds with `-W`**, so a malformed `.rst` or a
  `:mod:` reference to a module with no page fails the build. The four new
  pages and the toctree in `cli.rst` are what keep the cross-references in
  the new module docstrings resolvable.
- **R0.3's `packaging/deckle.spec` is absent from the tree**, and its
  `deckle-cli` `Analysis` almost certainly names `deckle/cli.py` as its
  script. Whoever lands R0.3 must point it at `deckle/cli/__main__.py`
  instead. Cannot be verified here because the file is gitignored and not
  present; flag it in the R0.3 commit. `tests/test_packaging_audit.py`
  itself needs no change — it inspects `dist/` and `run.bat`, never the
  module tree.
- **There is no console-script entry point in `pyproject.toml`** to check —
  no `[project.scripts]`, no `setup.py`, no `setup.cfg`. The two executables
  come from PyInstaller. `[tool.setuptools.packages.find] include =
  ["deckle*"]` already matches `deckle.cli` under `fnmatchcase`, so an
  editable or wheel install picks the subpackage up with no change.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`) and
  neither must `deckle.cli`, which that file checks in a subprocess by
  module name (`:82-99`) — unaffected by the split, but the *static* check
  in `tests/test_cli.py` is affected and is step 12.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`. A
  multi-commit move needs the entry on the last one, or a `--no-verify` you
  should not use.
