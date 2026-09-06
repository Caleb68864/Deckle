# N6 — Choose a page range at import, and skip a range in Arrange

**Roadmap item:** `docs/ROADMAP.md` N6
**Depends on:** —. Edits `deckle/cli.py` (collides with **M4**, and with **N5** and **N14**, which also add flags) and `deckle/app/views/arrange_view.py` (collides with **N10**, which adds actions to the same toolbar, and with **N7**, which binds keys in the same grid). Recommended: N5 → N6 → N14 on the CLI side, and N6 → N10 → N7 on the Arrange side; M4 last.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

A public-domain scan is not a book. Archive.org PDFs open with a scanner target,
a library bookplate, two blank leaves, a title page and a copyright notice, and
close with a colophon, an advertisement page and another scanner target. A
296-page book arrives as a 312-page PDF.

Deckle's answer today is to click Skip once per page: select page 1, click
Skip, select page 2, click Skip — sixteen times, in a grid where every click
also fires a thumbnail fetch and re-imposes the whole document. There is no
range, no multi-select action (N10), and nothing on the CLI at all.

Verified — no page selection exists anywhere:

```bash
$ .venv/bin/python -m deckle.cli export --help | grep -c -- "--pages"
0
$ grep -rn "skipped=True" deckle/core/loader.py deckle/cli.py
```

(the second returns nothing: the loader always produces `skipped=False`, and
nothing in the CLI ever changes it).

The consequence is not merely tedium. A blank leaf left in a folio job shifts
every subsequent page onto the wrong side of every fold, and a scanner target
imposed as page 1 becomes the recto of the title spread.

## 2. Current code

`deckle/cli.py:287-333` — the grammar to reuse, in full:

```python
def _parse_sheet_selection(value: str) -> list[int]:
    """A ``--sheets`` value as the sheet indices it names, in order.

    Accepts single numbers and inclusive ranges, comma-separated:
    ``0``, ``2,0``, ``1-3``, ``0,2-4``. Indices are **0-based**, matching
    every other sheet number Deckle prints -- the layout warnings, the
    schedule's gathering list, ``Sheet.index``. A 1-based flag would
    disagree with all three.
    ...
    """
    selection: list[int] = []
    items = [item.strip() for item in value.split(",")]
    if not value.strip() or any(not item for item in items):
        raise argparse.ArgumentTypeError(
            f"invalid sheets {value!r}: expected sheet numbers like 0, 2,0 "
            "or 0,2-4 -- counting from 0, as the schedule and the warnings do"
        )
    for item in items:
        bounds = [part.strip() for part in item.split("-")]
        if len(bounds) > 2 or any(not part.isdigit() for part in bounds):
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: {item!r} is not a sheet number "
                "or an inclusive range like 2-4"
            )
        if len(bounds) == 1:
            selection.append(int(bounds[0]))
            continue
        start, end = int(bounds[0]), int(bounds[1])
        if end < start:
            raise argparse.ArgumentTypeError(
                f"invalid sheets {value!r}: the range {item!r} runs backwards"
            )
        selection.extend(range(start, end + 1))
    return selection
```

`deckle/cli.py:336-361` — `_report_missing_sheets(plan, selection, total)`, the
precedent for refusing a selection that names something the document does not
have:

```python
    have = {sheet.index for sheet in plan.sheets}
    missing = sorted({index for index in selection if index not in have})
    if not missing:
        return False
    named = ", ".join(str(index) for index in missing)
    print(
        f"error: no sheet {named} in this document -- it has {total} "
        f"sheet(s), numbered 0 to {total - 1}",
        file=sys.stderr,
    )
    return True
```

`deckle/cli.py:621-664` — `_resolve_input`, where a source becomes pages:

```python
    if _is_project_file(args.source):
        project = _load_project_or_report(args.source)
        ...
        return list(project.pages), project.layout

    pages = _load_source_or_report(args.source)
    if pages is None:
        return None
    try:
        settings = _build_layout_settings(args)
    ...
    if getattr(args, "auto_crop", False):
        settings = _apply_auto_crop(pages, settings, args)
    return pages, settings
```

`deckle/core/models.py:91-107` — the flag N6 sets:

```python
@dataclass(frozen=True)
class SourcePage:
    """...
    :ivar skipped: whether the page is excluded from imposition entirely.
        A skipped page keeps its position in ``Project.pages`` so
        un-skipping restores it where it was.
    """
    ref: SourceRef
    rotate_deg: int
    skipped: bool
```

`deckle/app/state.py:145-158` — `toggle_skip(project, index)`, the existing
single-page mutator.

`deckle/app/views/arrange_view.py:139-147` — `skip(state, index)`;
`arrange_view.py:553-564` — the toolbar the new button joins:

```python
        toolbar = QHBoxLayout()
        self.rotate_button = QPushButton("Rotate", self.widget)
        self.skip_button = QPushButton("Skip", self.widget)
        self.insert_blank_button = QPushButton("Insert Blank", self.widget)
        toolbar.addWidget(self.rotate_button)
        toolbar.addWidget(self.skip_button)
        toolbar.addWidget(self.insert_blank_button)
        outer.addLayout(toolbar)
```

`deckle/app/views/arrange_view.py:461-476` — the injectable-chooser pattern
(`choose_blank_position`, `choose_move_target`) that the new dialog follows so
it can be driven headlessly.

`deckle/core/loader.py:156-174` — `ImportedPages(list)`, which carries
`.warnings`; `loader.py:307-325` — `load_pdf(path) -> list[SourcePage]`.

**Call sites of `_parse_sheet_selection` (grep):** `deckle/cli.py:287`
(definition), `cli.py:1255` (the `--sheets` argument);
`tests/test_cli_sheets.py:18,27,31,35,39,43,47,58`.

**Call sites of `toggle_skip` (grep):** `deckle/app/state.py:145` (definition),
`arrange_view.py:37,147`; `tests/test_app_state.py`,
`tests/test_arrange_reorder.py`, `tests/test_blank_insertion.py`.

**Existing tests:** `tests/test_cli_sheets.py` (the grammar, thoroughly),
`tests/test_arrange_reorder.py` (the grid's mutators),
`tests/test_loader.py` (`_make_pdf` helper).

## 3. Change

### Skipped, not deleted

`--pages` and "Skip range..." both set `skipped=True` on the pages they exclude
and **never remove them from the list**. Three reasons, in the order they
matter:

1. **It is reversible in Arrange.** The excluded pages are still there, greyed
   and labelled `(skipped)` by `ArrangeView.refresh` (`arrange_view.py:597-598`),
   and one click puts any of them back. A deletion is only reversible through
   undo, and only until the undo stack rolls past it.
2. **`skipped` is already the imposer's contract.** `SourcePage.skipped` says
   "excluded from imposition entirely" and every downstream consumer honours it
   already — `layout`, `composite_pages` (`render.py:512`), `auto_crop_insets`.
   Deletion would be a second mechanism for the same outcome.
3. **A `.deckle` records it.** A project saved with `--pages` keeps the whole
   source and the decision, so re-opening it shows what was excluded rather
   than a document that mysteriously starts at page 7.

### Two flags, two directions, deliberately

- **CLI `--pages 7-312,400` is a KEEP list**: the named pages survive, every
  other page is marked skipped. The user is stating the book they want out of a
  scan they have not looked at page by page.
- **GUI "Skip range..." is a SKIP list**: the named pages are marked skipped,
  the rest untouched. The user is looking at a document on screen where most
  pages are keepers and a few are not.

They are inverses because the situations are. Naming both `--pages` would make
the GUI's most common action ("drop these sixteen") require typing 296 numbers.

### Numbering

`--pages` is **1-based**. `--sheets` is 0-based and documents why
(`cli.py:291-295`: it matches `Sheet.index`, the warnings and the schedule's
gathering list). Pages are the opposite case, and the codebase already draws
this exact line — `schedule._page_numbers` (`schedule.py:134-140`):

> Page numbers are 1-based because the schedule is read by a person holding the
> book, and `source_ref.page_index` is 0-based because it indexes a file.

A user types what their PDF viewer's page counter shows. The help text says so
in its first clause.

### Steps

1. **`deckle/cli.py` — generalise the grammar.** Rename the body of
   `_parse_sheet_selection` to a shared parser and keep two thin callers, so
   there is one grammar and two vocabularies:

   ```python
   def _parse_index_selection(value: str, *, noun: str, example: str,
                              offset: int = 0) -> list[int]:
       """Comma-separated numbers and inclusive ranges, as a list of indices.

       One grammar, two flags. ``--sheets`` counts from 0 because it names
       Deckle's own artefact (``Sheet.index``, the warnings, the schedule);
       ``--pages`` counts from 1 because it names the user's document and a
       person types what their PDF viewer shows. ``offset`` is what
       reconciles them: it is subtracted from every number, so the caller
       states the base once instead of every consumer remembering it.

       Order is preserved and repeats are kept, because
       :func:`deckle.core.export.export` documents both for its ``sheets``
       argument. A caller that does not care (``--pages`` sets flags, so it
       does not) may ignore that.

       Open-ended ranges (``2-``) are deliberately not accepted: neither the
       sheet count nor the page count is known when argparse runs.

       :raises argparse.ArgumentTypeError: empty, malformed, a range that
           runs backwards, or a number below ``offset``.
       """
   ```

   Body is the current one with `"sheets"` replaced by `noun`, the two example
   strings replaced by `example`, `selection.append(int(bounds[0]) - offset)`,
   `range(start - offset, end - offset + 1)`, and one new check after parsing
   each item:

   ```python
           if start < offset:
               raise argparse.ArgumentTypeError(
                   f"invalid {noun} {value!r}: {noun} are numbered from "
                   f"{offset}"
               )
   ```

   Then:

   ```python
   def _parse_sheet_selection(value: str) -> list[int]:
       """A ``--sheets`` value as the sheet indices it names, in order.

       0-based, matching ``Sheet.index``, the layout warnings and the
       schedule's gathering list. See :func:`_parse_index_selection`.
       """
       return _parse_index_selection(
           value, noun="sheets",
           example="sheet numbers like 0, 2,0 or 0,2-4 -- counting from 0, "
                   "as the schedule and the warnings do",
       )


   def _parse_page_selection(value: str) -> list[int]:
       """A ``--pages`` value as 0-based page indices, in order.

       1-based on the way in, because a person types what their PDF
       viewer's page counter shows -- the same reason the binding schedule
       prints 1-based page numbers over 0-based ``page_index`` values.
       """
       return _parse_index_selection(
           value, noun="pages",
           example="page numbers like 7, 1,3 or 7-312,400 -- counting from "
                   "1, as your PDF viewer does",
           offset=1,
       )
   ```

   Every existing `tests/test_cli_sheets.py` assertion must still pass
   unchanged — that is the check that the generalisation changed nothing.

2. **`deckle/core/loader.py` — the pure selection.** Beside `ImportedPages`:

   ```python
   def apply_page_selection(
       pages: Sequence[SourcePage],
       *,
       keep: Sequence[int] | None = None,
       skip: Sequence[int] | None = None,
   ) -> list[SourcePage]:
       """Mark pages skipped, by keeping some or by skipping some.

       Skipped, never removed. A skipped page holds its place in the list,
       shows as ``(skipped)`` in the arrange grid, is excluded from
       imposition by every consumer of ``SourcePage.skipped``, and is one
       click from coming back -- none of which is true of a page that was
       deleted. It is also what makes a saved project honest: it records
       what was left out, rather than a document that mysteriously starts
       at page 7.

       :param pages: the loaded pages, in document order.
       :param keep: 0-based indices to KEEP. Every other page is marked
           skipped. A page already skipped stays skipped even if kept --
           this narrows a document, it never un-skips.
       :param skip: 0-based indices to mark skipped, leaving the rest as
           they are.
       :returns: a new list. The input is never mutated; ``SourcePage`` is
           frozen.
       :raises ValueError: both ``keep`` and ``skip`` were given, or an
           index is outside the document. Refused rather than ignored: a
           range that names nothing is a typo, and silently producing a
           different book than the one asked for is the failure this whole
           feature exists to prevent.
       """
   ```

   Body:

   ```python
       if keep is not None and skip is not None:
           raise ValueError(
               "give either keep or skip, not both: they are opposite ways "
               "of naming the same selection"
           )
       named = list(keep if keep is not None else (skip or ()))
       out_of_range = sorted({i for i in named if not 0 <= i < len(pages)})
       if out_of_range:
           raise ValueError(
               "no page "
               + ", ".join(str(i + 1) for i in out_of_range)
               + f" in this document -- it has {len(pages)} page(s), "
               f"numbered 1 to {len(pages)}"
           )
       if keep is None and skip is None:
           return list(pages)
       chosen = set(named)
       return [
           replace(page, skipped=True)
           if (index not in chosen if keep is not None else index in chosen)
           else page
           for index, page in enumerate(pages)
       ]
   ```

   The message renders 1-based numbers because that is what the user typed;
   `dataclasses.replace` needs `from dataclasses import replace` added to
   `loader.py`'s imports.

   A `keep` that keeps nothing is legal here (it produces an all-skipped
   document) and is refused one layer up, in step 3, where there is a message
   worth giving.

3. **`deckle/cli.py` — the flag and where it applies.** In `_add_layout_args`,
   after `--trim` (`cli.py:854-862`):

   ```python
       parser.add_argument(
           "--pages", dest="page_selection",
           type=_parse_page_selection, default=None, metavar="SPEC",
           help=(
               "use only these pages of the source, counting from 1 as your "
               "PDF viewer does -- e.g. 7-312,400. A public-domain scan "
               "carries a scanner target, a bookplate and a colophon, and "
               "none of them belong in the book. The rest are marked "
               "skipped rather than deleted, so they are still there if you "
               "open the project. Ignored for a .deckle source, which "
               "carries its own"
           ),
       )
   ```

   Then in `_resolve_input`, immediately after
   `pages = _load_source_or_report(args.source)` succeeds and **before**
   `_build_layout_settings`:

   ```python
       selection = getattr(args, "page_selection", None)
       if selection is not None:
           try:
               pages = apply_page_selection(pages, keep=selection)
           except ValueError as exc:
               print(f"error: {exc}", file=sys.stderr)
               log_exception("page_selection_rejected", exc)
               return None
           if all(page.skipped for page in pages):
               print(
                   "error: --pages kept no pages, so there would be nothing "
                   "to impose",
                   file=sys.stderr,
               )
               return None
   ```

   Before `_build_layout_settings` and therefore before `_apply_auto_crop`
   (`cli.py:662-663`) — deliberately, so `--auto-crop` measures the ink of the
   pages that will actually be printed. `auto_crop_insets` already excludes
   skipped pages (`render.py`), so a scanner target's black calibration bar
   cannot widen the measured extent of the whole book.

   `apply_page_selection` returns a plain `list`, which drops
   `ImportedPages.warnings` — so preserve them:

   ```python
               selected = apply_page_selection(pages, keep=selection)
               warnings_carried = list(getattr(pages, "warnings", []))
               pages = ImportedPages(selected, warnings_carried)
   ```

   with `ImportedPages` and `apply_page_selection` imported from
   `deckle.core.loader` at `cli.py:30`. Losing them would silently drop the
   mixed-DPI advisory for every image-directory import that used `--pages`.

   Note the `.deckle` branch returns earlier (`cli.py:635-647`) and is
   untouched: a project already carries its own skips, and `_layout_flags_given`
   will report `--pages` as ignored alongside the layout flags — which is
   correct here and is *not* B24's complaint (B24 is about `--sheets`/`--pass`/
   `--profile`, which are honoured despite being reported).

4. **`deckle/app/state.py` — the GUI mutator.** Beside `toggle_skip`:

   ```python
   def skip_pages(project: Project, indices: Sequence[int]) -> Project:
       """Mark every page in ``indices`` skipped, leaving the rest alone.

       Distinct from :func:`toggle_skip`, which flips one page: a range is
       stated, not toggled, so naming a page that is already skipped is
       not a request to bring it back.

       :param project: the project to derive a new one from.
       :param indices: 0-based page indices.
       :returns: a new project.
       :raises ValueError: an index is outside the document.
       """
       return replace(
           project,
           pages=apply_page_selection(project.pages, skip=list(indices)),
       )
   ```

5. **`deckle/app/views/arrange_view.py` — the button.** After
   `self.insert_blank_button` (`arrange_view.py:556`):

   ```python
           self.skip_range_button = QPushButton("Skip range...", self.widget)
           self.skip_range_button.setToolTip(
               "Mark a range of pages skipped in one go -- 1-6, 309-312.\n\n"
               "Page numbers count from 1, as your PDF viewer does. A "
               "scanned book usually opens with a scanner target, a "
               "bookplate and two blank leaves and closes with a colophon; "
               "skipping them one at a time is sixteen clicks.\n\n"
               "Skipped pages stay in the list and can be brought back."
           )
           toolbar.addWidget(self.skip_range_button)
   ```

   Exact label: `"Skip range..."`. Wired at `arrange_view.py:562-564`:

   ```python
           self.skip_range_button.clicked.connect(self._on_skip_range_clicked)
   ```

6. **`deckle/app/views/arrange_view.py` — the chooser and the handler.** Add a
   constructor parameter beside the other two (`arrange_view.py:461-468`):

   ```python
           ask_skip_range=None,
   ```

   ```python
           self._ask_skip_range = ask_skip_range or self._default_ask_skip_range
   ```

   ```python
       def _default_ask_skip_range(self, page_count: int) -> str | None:
           """Ask which pages to skip. Replaceable for tests."""
           from PySide6.QtWidgets import QInputDialog

           text, ok = QInputDialog.getText(
               self.widget,
               "Skip a range of pages",
               f"Which pages should be skipped?  (1 to {page_count}, "
               "e.g. 1-6, 309-312)",
           )
           return text if ok else None
   ```

   ```python
       def _on_skip_range_clicked(self) -> None:
           """Mark a stated range skipped, then re-impose.

           :returns: nothing. A malformed range is reported on the button's
               tooltip and does nothing else -- it is a typo, and the grid
               is not a place to raise.
           """
           count = len(self.state.project.pages)
           if count == 0:
               return
           text = self._ask_skip_range(count)
           if not text:
               return
           try:
               indices = parse_page_range(text, count)
           except ValueError as exc:
               self.skip_range_button.setToolTip(str(exc))
               return
           self.state.mutate(lambda project: skip_pages(project, indices))
           self.refresh()
           self.pages_changed.emit()
   ```

   One `mutate` call for the whole range, so a skip-range is one step of undo
   rather than sixteen — the same reason `reorder_to` exists beside `reorder`
   (`arrange_view.py:112-123`). Import `skip_pages` from `deckle.app.state`
   alongside the other mutators at `arrange_view.py:31-38`.

7. **`deckle/app/views/arrange_view.py` — the grammar, once more, Qt-free.**
   The CLI's parser raises `argparse.ArgumentTypeError` and lives in a module
   `deckle/app` must not import (`cli.py` imports only `deckle.core`; importing
   it from a view would invert the dependency). So add a pure parser at module
   level in `arrange_view.py`, above the Qt wiring:

   ```python
   def parse_page_range(text: str, page_count: int) -> list[int]:
       """A ``1-6, 309-312`` range as 0-based page indices.

       The same grammar ``deckle export --sheets`` and
       ``deckle export --pages`` accept -- numbers and inclusive ranges,
       comma-separated -- and 1-based for the same reason ``--pages`` is:
       a person types what their PDF viewer shows.

       Written here rather than imported from :mod:`deckle.cli`, because
       the app must not depend on the CLI: ``deckle.cli`` sits above
       ``deckle.core`` alone and never touches ``deckle.app`` (see
       ``docs/api/index.rst``). M4's CLI split is where the two could
       finally share one implementation, in ``deckle/core``.

       :param text: the raw text the user typed.
       :param page_count: how many pages the document has, for bounds.
       :returns: 0-based indices, ascending, without repeats.
       :raises ValueError: empty, malformed, backwards, or out of range.
           The message is shown to the user, so it names the remedy.
       """
   ```

   Implementation: split on `,`; each item split on `-` into one or two parts;
   every part must satisfy `str.isdigit()`; a bound of `0` raises
   `ValueError("pages are numbered from 1")`; `end < start` raises
   `ValueError(f"the range {item!r} runs backwards")`; an index at or above
   `page_count` raises
   `ValueError(f"no page {n} in this document -- it has {page_count} page(s)")`;
   returns `sorted(set(indices))`.

   A duplicated grammar is a real cost and is recorded as such: §6 names M4 as
   where it is paid off, and §4 has a parity test that fails the moment the two
   diverge.

## 4. Tests

### `tests/test_cli_sheets.py` (extend — it already owns the grammar)

Every existing assertion must pass unchanged after step 1. Add:

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_pages_are_one_based` | `_parse_page_selection("1")` `== [0]`; `_parse_page_selection("7-9")` `== [6, 7, 8]` | `ImportError: cannot import name '_parse_page_selection'` |
| `test_pages_rejects_zero` | `_parse_page_selection("0")` raises `argparse.ArgumentTypeError` containing `"numbered from 1"` | as above |
| `test_pages_rejects_a_backwards_range` | `"9-7"` raises, message contains `"runs backwards"` | as above |
| `test_pages_and_sheets_share_one_grammar` | for each of `"0"`, `"2,0"`, `"1-3"`, `"0,2-4"`, `" 0 , 2 - 3 "`, `[i + 1 for i in _parse_sheet_selection(v)]` equals `_parse_page_selection(v.replace(...))` shifted — assert equal after adding 1 to every sheet index and re-parsing the same shape | as above |

### `tests/test_page_selection.py` (new)

Build pages with `tests/test_loader.py::_make_pdf` + `load_pdf`, or the
`SourcePage` builders in `tests/test_ui_surface.py`.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_keeping_a_range_skips_everything_else` | 10 pages, `keep=[6,7,8]` | pages 6-8 have `skipped is False`, all others `True`, and `len(result) == 10` | `ImportError: cannot import name 'apply_page_selection'` |
| `test_skipping_a_range_leaves_the_rest_alone` | `skip=[0,1]` | pages 0-1 `True`, 2-9 `False` | as above |
| `test_nothing_is_ever_removed` | either direction | `len(result) == len(pages)` and every `ref.page_index` is in the same position | as above |
| `test_the_input_is_not_mutated` | `keep=[0]` | the original list's pages all still have `skipped is False` | as above |
| `test_keeping_never_unskips` | a page pre-marked `skipped=True` included in `keep` | it stays skipped | as above |
| `test_both_directions_at_once_is_refused` | `keep=[0], skip=[1]` | `ValueError` containing `"not both"` | as above |
| `test_a_page_the_document_does_not_have_is_refused` | 10 pages, `keep=[400]` | `ValueError` containing `"no page 401"` and `"10 page(s)"` — the message is 1-based | as above |

### `tests/test_cli.py` (extend) — end to end

Uses `tests/fixtures/sample.pdf` (2 pages) and `deckle dummy` for longer ones.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_pages_narrows_the_document` | `deckle dummy -o d.pdf --pages 8`, then `deckle impose d.pdf -o job.deckle --pages 3-6`; read the JSON | entries 2-5 have `"skipped": false`, the other four `true`, and there are still 8 entries | `error: unrecognized arguments: --pages` (exit 2) |
| `test_pages_naming_a_page_that_is_not_there_fails_cleanly` | `--pages 400` on the 8-page dummy | exit 1, stderr contains `"no page 400"` and `"8 page(s)"`, and no output file was written | exit 2, argparse error about an unrecognised flag |
| `test_pages_that_keep_nothing_fails_cleanly` | `--pages 9` on the 8-page dummy — wait, that is out of range; use a document where the kept set is empty by construction: `--pages 1` on a source whose page 1 is already skipped is impossible from the CLI, so assert the guard directly by calling `_resolve_input` with a patched loader returning all-skipped pages | exit 1, stderr contains `"kept no pages"` | `AttributeError`/exit 2 |
| `test_pages_is_reported_as_ignored_for_a_project_source` | `deckle export job.deckle -o out.pdf --pages 1-2` | exit 0 and stderr contains `"--pages"` and `"carries its own layout"` | exit 2 |
| `test_pages_narrows_what_auto_crop_measures` | an 8-page dummy where page 1 carries ink far outside the others' extent; compare `--auto-crop` output with and without `--pages 2-8` | the reported `--crop` values differ, and the `--pages` run's are tighter | exit 2 |
| `test_the_import_warnings_survive_a_page_selection` | image-directory import with mixed DPI plus `--pages 1-2` | stderr still carries the `mixed_dpi` warning | exit 2 |

### `tests/test_arrange_reorder.py` (extend)

`ArrangeView` constructs headless (the module's existing tests do it).

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_parse_page_range_matches_the_cli_grammar` | for `"1-6, 309-312"` with `page_count=312`, `parse_page_range` returns `list(range(0,6)) + list(range(308,312))` | `ImportError: cannot import name 'parse_page_range'` |
| `test_parse_page_range_refuses_zero_and_out_of_range` | `"0"` and `"313"` with `page_count=312` each raise `ValueError` with a message naming the remedy | as above |
| `test_skip_range_marks_the_named_pages` | `ArrangeView(state, ask_skip_range=lambda n: "1-2")`; click `skip_range_button` | pages 0-1 skipped, the rest not; exactly one entry was pushed on the undo stack | `AttributeError: 'ArrangeView' object has no attribute 'skip_range_button'` |
| `test_a_cancelled_skip_range_changes_nothing` | `ask_skip_range=lambda n: None` | `state.can_undo is False` | as above |
| `test_a_malformed_skip_range_reports_on_the_button` | `ask_skip_range=lambda n: "one to six"` | no exception, `state.can_undo is False`, and `skip_range_button.toolTip()` names the remedy | as above |
| `test_skip_range_is_one_undo_step` | `"1-6"` on a 10-page document, then `state.undo()` | every page is unskipped again | as above |

## 5. Acceptance

| Check | Command |
|---|---|
| The grammar tests pass, old and new | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_cli_sheets.py` |
| The new selection tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_page_selection.py tests/test_cli.py tests/test_arrange_reorder.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The flag exists | `.venv/bin/python -m deckle.cli export --help \| grep -q -- "--pages"` |
| Nothing deletes pages | `! grep -n "del pages\|pages.pop\|pages.remove" deckle/core/loader.py deckle/cli.py` |
| The app does not import the CLI | `! grep -rn "^from deckle.cli\|^import deckle.cli" deckle/app` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| The selection is applied before auto-crop | `sed -n '/def _resolve_input/,/^def /p' deckle/cli.py \| grep -n "page_selection\|_apply_auto_crop"` — `page_selection` must appear on an earlier line |
| The button label is exactly as specified | `grep -q 'QPushButton("Skip range\.\.\."' deckle/app/views/arrange_view.py` |

## 6. Out of scope

- **N10** (multi-select rotate/skip/remove, and a Remove action that really
  deletes). N6 deliberately never deletes; N10 adds deletion as its own,
  separately undoable action with its own reasoning.
- **M4's CLI split.** The duplicated range grammar (`cli._parse_index_selection`
  and `arrange_view.parse_page_range`) is a real cost, incurred because
  `deckle.app` must not import `deckle.cli`. M4 is where it moves to
  `deckle/core` and both call one implementation. The parity test in §4 is what
  keeps them honest until then.
- **B24** (`_layout_flags_given` reporting non-layout flags as ignored and then
  honouring them). `--pages` is genuinely ignored for a `.deckle`, so it is
  correctly reported; the flags B24 is about are unaffected.
- **B26** (the loader stores the source path exactly as typed). Unrelated, and
  N6 changes no paths.
- **A `--skip` flag on the CLI**, or a "Keep only range..." action in Arrange.
  Each direction is offered where it is the common case; adding both to both
  doubles the surface for a case nobody has asked for.
- **GUIDE §8's CLI reference** (D5).

## 7. decisions.md entry

```
## 2026-09-05 — Trimming a scan's front and back matter was one click per page
- Symptom: A 296-page public-domain book arrives as a 312-page PDF: scanner target, bookplate, two blanks, colophon, advertisement, second target. Deckle's only answer was to select each one and click Skip, sixteen times, each click re-imposing the whole document -- and the CLI had no answer at all (`--pages` did not exist). Leaving one blank leaf in a folio job shifts every later page onto the wrong side of every fold.
- Fix: `--pages 7-312,400` on every CLI command, and a "Skip range..." button in Arrange. Both mark `skipped=True` and neither deletes: a skipped page holds its place, shows as `(skipped)`, is excluded by every consumer of `SourcePage.skipped`, and is one click from coming back. `_parse_sheet_selection` was generalised into `_parse_index_selection(noun, example, offset)`, so `--sheets` (0-based, naming `Sheet.index`) and `--pages` (1-based, naming the user's document) share one grammar and differ only by the base -- the same split `schedule._page_numbers` already draws.
- Surfaces: The selection is applied before `--auto-crop`, so a scanner target's calibration bar cannot widen the measured ink extent of the whole book. `apply_page_selection` returns a plain list, which silently drops `ImportedPages.warnings`; the CLI rewraps them, or every mixed-DPI advisory would vanish the moment someone used `--pages`.
- Watch: The range grammar now exists twice -- once in `cli.py` and once in `arrange_view.py` -- because `deckle.app` must not import `deckle.cli`. A parity test pins them together, and M4 is where they finally share one implementation in `deckle.core`.
- Commit: <fill in>
```

## 8. Traps

- **`deckle.app` must not import `deckle.cli`.** `docs/api/index.rst` states the
  dependency direction: `deckle.cli` sits above `deckle.core` alone. Step 7's
  duplicate parser is deliberate, not an oversight, and §5 greps for the
  violation.
- **`_resolve_input` returns `ImportedPages`, not a plain list**, for an
  image-directory source — and `_emit_warnings` reads `.warnings` off it
  (`cli.py:912`). `apply_page_selection` returns a plain list, so rewrap or the
  warnings vanish. `cli._load_source`'s docstring (line 464-471) records that
  `list()`-wrapping this result already dropped them once.
- **`--pages` is 1-based and `--sheets` is 0-based, in the same command.** That
  is deliberate and documented in both help texts. Anyone "fixing" the
  inconsistency will silently shift every user's page ranges by one.
- **`apply_page_selection` must not un-skip.** A page the user already skipped
  in Arrange and then names in a `keep` range stays skipped; this narrows a
  document, it does not restore one.
- **Skipping every page produces a plan with no sheets**, and `export` then
  writes a 0-page PDF that passes `_verify_output` (B20). Step 3's "kept no
  pages" guard is what stops that; do not drop it.
- **`ArrangeView.refresh()` rebuilds every item and drops the selection**
  (`arrange_view.py:820-824` explains why `move_pages` re-selects afterwards).
  A skip-range does not need to preserve a selection, but it does need the
  `refresh()` + `pages_changed.emit()` pair, or the preview keeps showing the
  pre-skip plan and Save PDF exports it.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
