# N10 — Rotate, skip and remove every selected page

**Roadmap item:** `docs/ROADMAP.md` N10
**Depends on:** —. Edits `deckle/app/views/arrange_view.py` and `deckle/app/state.py`. Collides with **N6** (which adds a "Skip range..." button to the same toolbar and a `skip_pages` mutator beside the new one) and with **N7** (which renames these handlers to public names and binds `Delete`/`R` to them). Recommended order: **N6 → N10 → N7**, so N7 binds keys to the finished multi-select methods and does not have to rename them twice.
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

Select twelve pages in the arrange grid — the grid is in `ExtendedSelection`
mode, so this works, and there is a comment saying Qt's default
`SingleSelection` "made every multi-page path below unreachable"
(`arrange_view.py:538-542`). Now click **Rotate**.

One page rotates: the current one. The other eleven are untouched, and nothing
says so. The same for **Skip**. The context menu is worse than merely
inconsistent — it *offers* the multi-page action by name and then does not
perform it:

```
Rotate 90°          →  rotates one page
Skip / unskip       →  skips one page
Move 12 pages to... →  moves twelve
```

Only Move honours the selection. The menu literally counts the selection to
build the Move label (`arrange_view.py:721-723`) and then hands Rotate and Skip
to a method that reads `currentRow()`.

And there is no way to **remove** a page at all. A scan's sixteen pages of front
matter can be skipped but never deleted, so a 312-page project stays 312 pages
in the arrange grid, in the `.deckle`, and in every source-hash check
`load_project` performs on open.

Verified:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
import inspect
from deckle.app.views import arrange_view
print(inspect.getsource(arrange_view.ArrangeView._on_rotate_clicked))
EOF
```

```python
    def _on_rotate_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        current = self.state.project.pages[index].rotate_deg
        rotate(self.state, index, current + 90)
        self.refresh()
        self.pages_changed.emit()
```

`_selected_index` is `currentRow()`, singular.

## 2. Current code

`deckle/app/views/arrange_view.py:652-671` — the two single-page handlers, and
the selection reader they use:

```python
    def _selected_index(self) -> int | None:
        row = self.list_widget.currentRow()
        return row if row >= 0 else None

    def _on_rotate_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        current = self.state.project.pages[index].rotate_deg
        rotate(self.state, index, current + 90)
        self.refresh()
        self.pages_changed.emit()

    def _on_skip_clicked(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        skip(self.state, index)
        self.refresh()
        self.pages_changed.emit()
```

`deckle/app/views/arrange_view.py:694-696` — the multi-selection reader that
already exists and is used only by Move:

```python
    def _selected_indices(self) -> list[int]:
        """Every selected row, in document order."""
        return sorted(index.row() for index in self.list_widget.selectedIndexes())
```

`deckle/app/views/arrange_view.py:698-739` — the context menu, which counts the
selection for Move and then routes Rotate/Skip to the singular handlers:

```python
        row = self.list_widget.indexAt(point).row()
        if row < 0:
            return
        # A right-click outside the selection acts on what was clicked,
        # which is what every file manager does.
        if row not in self._selected_indices():
            self.list_widget.setCurrentRow(row)
        rows = self._selected_indices() or [row]

        menu = QMenu(self.widget)
        move_action = menu.addAction(
            "Move to..." if len(rows) == 1 else f"Move {len(rows)} pages to..."
        )
        menu.addSeparator()
        rotate_action = menu.addAction("Rotate 90°")
        skip_action = menu.addAction("Skip / unskip")
        blank_action = menu.addAction("Insert blank...")

        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(point))
        if chosen is None:
            return
        if chosen is move_action:
            self._move_selection_via_dialog(rows)
        elif chosen is rotate_action:
            self._on_rotate_clicked()
        elif chosen is skip_action:
            self._on_skip_clicked()
        elif chosen is blank_action:
            self._on_insert_blank_clicked()
```

`deckle/app/views/arrange_view.py:536-549` — the selection mode, and the
comment recording that this was fixed once already:

```python
        # Several pages at once: a chapter dragged to the front, or moved
        # together through "Move to...". Qt's default is SingleSelection,
        # which made every multi-page path below unreachable.
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
```

`deckle/app/views/arrange_view.py:553-560` — the toolbar the Remove button
joins:

```python
        toolbar = QHBoxLayout()
        self.rotate_button = QPushButton("Rotate", self.widget)
        self.skip_button = QPushButton("Skip", self.widget)
        self.insert_blank_button = QPushButton("Insert Blank", self.widget)
```

`deckle/app/views/arrange_view.py:797-825` — `move_pages(rows, target)`, the
model for a correct multi-page action: it filters out-of-range rows, drops a
no-op, mutates **once**, refreshes, **re-selects the moved rows**, and emits:

```python
        count = len(self.state.project.pages)
        rows = [row for row in rows if 0 <= row < count]
        if not rows:
            return
        order = move_rows_to(count, rows, target)
        if order == list(range(count)):
            self.refresh()
            return
        reorder_to(self.state, order)
        self.refresh()
        # Keep hold of the pages that moved. `refresh` rebuilds every item,
        # which drops the selection -- so without this the moved page is
        # deselected the instant it lands, the preview stops following it,
        # and dragging it again means finding and re-clicking it first.
        self._select_rows([order.index(row) for row in rows])
        self.pages_changed.emit()
```

`deckle/app/state.py:130-171` — the pure mutators. There is `set_rotation`,
`toggle_skip`, `insert_blank`, `reorder_pages`, `reorder_pages_to` — and
**nothing that removes a page**.

`deckle/app/views/arrange_view.py:100-147` — the `AppState.mutate` wrappers
(`reorder`, `reorder_to`, `rotate`, `skip`), each a one-liner.

**Call sites of `_selected_index` (grep):** `arrange_view.py:652` (definition),
`657`, `666`, `765`. No test calls it.

**Call sites of `_selected_indices` (grep):** `arrange_view.py:694`
(definition), `716`, `718`. No test calls it.

**Call sites of `set_rotation` / `toggle_skip` (grep):**
`deckle/app/state.py:130,145` (definitions), `arrange_view.py:31-38,136,147`;
`tests/test_app_state.py`, `tests/test_arrange_reorder.py`,
`tests/test_blank_insertion.py`.

**Existing tests:** `tests/test_arrange_reorder.py` (the grid, headless),
`tests/test_blank_insertion.py` (`_on_insert_blank_clicked` and the injectable
chooser), `tests/test_app_state.py` (the pure mutators),
`tests/gui_workflow.py:77` (calls `_on_insert_blank_clicked`).

## 3. Change

### Multi-select, and what the selection is

Every action reads `_selected_indices()`. When the selection is empty it falls
back to `_selected_index()` (the current row), so clicking one page and pressing
Rotate keeps working exactly as it does now — `currentRow()` can be set without
a selection, and a keyboard user arriving via arrow keys is in that state.

```python
    def _acting_rows(self) -> list[int]:
        """The pages an action should act on: the selection, else the
        current row.

        Every toolbar action, context-menu item and keyboard shortcut goes
        through this, so "what does this act on" has exactly one answer.
        The fallback matters: ``currentRow()`` can be set with nothing
        selected -- a keyboard user arriving by arrow key is in that state
        -- and an action that did nothing there would look broken.

        :returns: page indices in document order, possibly empty.
        """
        rows = self._selected_indices()
        if rows:
            return rows
        index = self._selected_index()
        return [] if index is None else [index]
```

### Rotate

Rotating a mixed selection has two possible meanings and only one is useful.
**Each page advances by 90° from its own current rotation** — chosen, because
that is what "rotate these" means and it makes four presses a full turn for
every page regardless of where each started. Rejected: setting them all to a
single common angle, which would silently discard rotations the user had
already applied one page at a time.

### Remove

A new pure mutator, and a new action. It **deletes** — this is the one place in
Deckle that does.

```python
def remove_pages(project: Project, indices: Sequence[int]) -> Project:
    """Delete pages from the document.

    The only operation here that removes rather than flags. Skipping keeps
    a page in the list so un-skipping restores it where it was, which is
    right for "not in this book" and wrong for "not in this project at
    all": a 312-page scan whose sixteen pages of front matter are merely
    skipped is still 312 pages in the grid, in the ``.deckle``, and in
    every source-hash check ``load_project`` runs on open.

    Undoable like every other mutation -- it goes through
    ``AppState.mutate``, which snapshots the whole frozen ``Project``
    first, so a removal is one Ctrl+Z away for as long as it is on the
    50-deep stack.

    :param project: the project to derive a new one from. Never mutated.
    :param indices: which pages to remove. Order and repeats are
        irrelevant; each named page is removed once.
    :returns: a new project without them.
    :raises IndexError: an index is out of range -- matching
        :func:`set_rotation` and :func:`toggle_skip`, which raise rather
        than skipping a bad index.
    """
    doomed = set(indices)
    for index in doomed:
        if not 0 <= index < len(project.pages):
            raise IndexError(index)
    return replace(
        project,
        pages=[page for i, page in enumerate(project.pages) if i not in doomed],
    )
```

### Steps

1. **`deckle/app/state.py`** — add `remove_pages` above `class AppState`, beside
   `insert_blank` (`state.py:161`). `Sequence` needs adding to the `typing`
   import at `state.py:32`.

2. **`deckle/app/views/arrange_view.py`** — add the `mutate` wrapper beside the
   others (`arrange_view.py:139-147`):

   ```python
   def remove(state: AppState, indices: Sequence[int]) -> None:
       """Delete pages, through ``AppState.mutate``.

       One mutation for the whole set, so removing a chapter is one step of
       undo rather than one per page.

       :param state: the app state to mutate.
       :param indices: which pages to remove.
       :returns: nothing.
       :raises IndexError: an index is out of range.
       """
       state.mutate(lambda project: remove_pages(project, indices))
   ```

   and `remove_pages` to the `from deckle.app.state import (...)` block at
   `arrange_view.py:31-38`.

3. **`deckle/app/views/arrange_view.py` — `_acting_rows`**, as above, placed
   directly after `_selected_indices` (`arrange_view.py:696`).

4. **`deckle/app/views/arrange_view.py` — rotate every selected page.** Replace
   `_on_rotate_clicked`'s body (keeping the name, or the public
   `rotate_selection` name if **N7** has landed):

   ```python
       def rotate_selection(self) -> None:
           """Turn every selected page 90° clockwise from where it is now.

           Each page advances from its OWN rotation rather than all of them
           being set to one angle -- four presses is a full turn for every
           page regardless of where each started, and a common angle would
           silently discard rotations applied one page at a time.

           :returns: nothing. One mutation for the whole selection, so it is
               one step of undo.
           """
           rows = self._acting_rows()
           if not rows:
               return
           self.state.mutate(
               lambda project: _rotated(project, rows, 90)
           )
           self.refresh()
           self._select_rows(rows)
           self.pages_changed.emit()
   ```

   with a module-level pure helper beside `move_rows_to`:

   ```python
   def _rotated(project, rows: Sequence[int], by_deg: int):
       """``project`` with each page in ``rows`` turned a further ``by_deg``."""
       result = project
       for row in rows:
           result = set_rotation(
               result, row, result.pages[row].rotate_deg + by_deg
           )
       return result
   ```

   `set_rotation` already normalises into `[0, 360)` (`state.py:141`), so
   nothing here wraps.

5. **`deckle/app/views/arrange_view.py` — skip every selected page.**

   ```python
       def skip_selection(self) -> None:
           """Flip the skipped flag on every selected page.

           A toggle, not a set: this is the multi-page spelling of the
           button that has always toggled, and a selection of one behaves
           identically. A mixed selection therefore inverts -- which is what
           "toggle these" means, and is undone by pressing it again.

           :returns: nothing. One mutation, so one step of undo.
           """
           rows = self._acting_rows()
           if not rows:
               return
           self.state.mutate(lambda project: _toggled(project, rows))
           self.refresh()
           self._select_rows(rows)
           self.pages_changed.emit()
   ```

   with the same shape of helper over `toggle_skip`. **Toggle, not set-all**,
   because that is what the control has always done and because N6's "Skip
   range..." already provides the *stating* spelling (`skip_pages`, which sets
   rather than flips). Two actions, two meanings, both named for what they do.

6. **`deckle/app/views/arrange_view.py` — the Remove action.** After
   `self.insert_blank_button` (`arrange_view.py:556`, or after N6's
   `skip_range_button`):

   ```python
           self.remove_button = QPushButton("Remove", self.widget)
           self.remove_button.setToolTip(
               "Delete the selected pages from the project.\n\n"
               "Different from Skip: a skipped page stays in the list and "
               "comes back with one click, which is what you want for a "
               "page that is not in THIS book. Remove is for a page that "
               "should not be in the project at all.\n\n"
               "Undoable with Ctrl+Z. It does not touch the source file."
           )
           toolbar.addWidget(self.remove_button)
   ```

   Exact label: `"Remove"`. Wired at `arrange_view.py:562-564`:

   ```python
           self.remove_button.clicked.connect(self.remove_selection)
   ```

   ```python
       def remove_selection(self) -> None:
           """Delete the selected pages, after confirming.

           Confirmed because it is the only destructive action in the grid
           and the only one whose result is not visible as a change to
           something still on screen -- an accidental Remove of twelve pages
           in a 312-page document looks exactly like nothing happening.

           :returns: nothing. One mutation, so one Ctrl+Z brings them all
               back.
           """
           rows = self._acting_rows()
           if not rows:
               return
           if not self._confirm_remove(len(rows)):
               return
           remove(self.state, rows)
           self.refresh()
           # Land the selection where the removed pages were, so the next
           # action has somewhere obvious to act. `refresh` drops it, as
           # `move_pages` records.
           if self.state.project.pages:
               self.list_widget.setCurrentRow(
                   min(rows[0], len(self.state.project.pages) - 1)
               )
           self.pages_changed.emit()
   ```

7. **`deckle/app/views/arrange_view.py` — the injectable confirmation.** A
   fourth constructor parameter beside `choose_blank_position` and
   `choose_move_target` (`arrange_view.py:461-468`), for the same stated reason
   ("the default opens a modal, which a test cannot"):

   ```python
           confirm_remove=None,
   ```

   ```python
           self._confirm_remove = confirm_remove or self._default_confirm_remove
   ```

   ```python
       def _default_confirm_remove(self, count: int) -> bool:
           """Ask before deleting pages. Replaceable for tests."""
           from PySide6.QtWidgets import QMessageBox

           answer = QMessageBox.question(
               self.widget,
               "Remove pages?",
               f"Remove {count} page(s) from the project?\n\n"
               "Skip instead if you only want them left out of this book -- "
               "a skipped page stays in the list. This can be undone with "
               "Ctrl+Z, and it does not change the source file.",
               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
           )
           return answer == QMessageBox.StandardButton.Yes
   ```

8. **`deckle/app/views/arrange_view.py` — the context menu says what it will
   do.** Replace the three fixed labels (`arrange_view.py:725-727`) with
   counted ones, matching how Move already builds its label:

   ```python
           count = len(rows)
           rotate_action = menu.addAction(
               "Rotate 90°" if count == 1 else f"Rotate {count} pages 90°"
           )
           skip_action = menu.addAction(
               "Skip / unskip" if count == 1
               else f"Skip / unskip {count} pages"
           )
           blank_action = menu.addAction("Insert blank...")
           menu.addSeparator()
           remove_action = menu.addAction(
               "Remove page" if count == 1 else f"Remove {count} pages"
           )
   ```

   and add the branch:

   ```python
           elif chosen is remove_action:
               self.remove_selection()
   ```

   Remove goes below a separator, at the bottom, away from Skip — they are one
   click apart and one of them is destructive.

9. **`deckle/app/views/arrange_view.py` — the module docstring.** Its first
   paragraph lists "drag reorder, rotate, skip, insert-blank". Make it
   "drag reorder, rotate, skip, remove, insert-blank", and add to the body:

   > Removing is the one operation here that deletes rather than flags.
   > Everything else keeps the page in the list, because a skipped page
   > un-skips where it was; Remove is for a page that should not be in the
   > project at all. It still goes through ``AppState.mutate``, so it is one
   > Ctrl+Z away.

## 4. Tests

`ArrangeView` constructs headless — `tests/test_arrange_reorder.py` and
`tests/test_blank_insertion.py` both do it. No subprocess needed.

### `tests/test_app_state.py` (extend — it owns the pure mutators)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_remove_pages_deletes_them` | `_make_project(5)`, `remove_pages(p, [1, 3])` | 3 pages left, with `ref.page_index` `0, 2, 4` | `ImportError: cannot import name 'remove_pages'` |
| `test_remove_pages_ignores_order_and_repeats` | `[3, 1, 3]` | same result as `[1, 3]` | as above |
| `test_remove_pages_does_not_mutate_the_original` | | the input project still has 5 pages | as above |
| `test_remove_pages_rejects_a_bad_index` | `[9]` on 5 pages | `IndexError` | as above |
| `test_removing_every_page_leaves_an_empty_document` | `list(range(5))` | `pages == []`, no exception | as above |
| `test_remove_is_undoable` | `AppState(_make_project(5))`, `state.mutate(lambda p: remove_pages(p, [0, 1]))`, then `state.undo()` | 5 pages again, in the original order | as above |

### `tests/test_arrange_reorder.py` (extend)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_rotate_turns_every_selected_page` | select rows 0, 2, 4 via `_select_rows`; call `view.rotate_selection()` | pages 0, 2, 4 have `rotate_deg == 90`; 1 and 3 are `0` | only the current row rotated: `AssertionError` (and `AttributeError` for the method name if N7 has not landed — use `_on_rotate_clicked` until it has) |
| `test_rotate_advances_each_page_from_its_own_angle` | page 0 pre-set to 90, page 1 to 0; select both; rotate | `180` and `90` | `AssertionError` |
| `test_rotate_is_one_undo_step` | select three, rotate, `state.undo()` | all three back to `0` | `AssertionError` — today it is one page, so undo restores one |
| `test_rotate_keeps_the_selection` | select three, rotate | `view._selected_indices() == [0, 2, 4]` | `AssertionError`: `refresh()` drops it |
| `test_skip_flips_every_selected_page` | select 0 and 1, `skip_selection()` | both skipped | `AssertionError` |
| `test_skip_inverts_a_mixed_selection` | page 0 pre-skipped, page 1 not; select both; skip | page 0 unskipped, page 1 skipped | `AssertionError` |
| `test_an_action_with_no_selection_uses_the_current_row` | `setCurrentRow(2)`, `clearSelection()`, rotate | page 2 rotated | passes today; it pins the fallback |
| `test_an_action_with_nothing_selected_at_all_does_nothing` | empty document | no exception, `state.can_undo is False` | passes today; pins it |
| `test_remove_deletes_the_selection` | `ArrangeView(state, confirm_remove=lambda n: True)`; select 0 and 1; `remove_selection()` | 3 pages left, `pages_changed` emitted once | `AttributeError: 'ArrangeView' object has no attribute 'remove_selection'` |
| `test_remove_asks_first` | `confirm_remove=lambda n: False` | nothing removed, `state.can_undo is False` | as above |
| `test_remove_is_told_how_many` | a recording `confirm_remove`; select three | it was called once with `3` | as above |
| `test_remove_is_one_undo_step` | remove three, `state.undo()` | all five back, in order | as above |
| `test_remove_leaves_the_cursor_where_the_pages_were` | 5 pages, remove rows 1 and 2 | `list_widget.currentRow() == 1` | as above |
| `test_removing_everything_leaves_no_current_row` | remove all | no exception; `len(state.project.pages) == 0` | as above |
| `test_the_context_menu_counts_the_selection` | build the menu labels for a 3-row selection through whatever seam `_on_context_menu` exposes (or assert on the label-building expression extracted to a pure helper) | labels read `"Rotate 3 pages 90°"`, `"Skip / unskip 3 pages"`, `"Remove 3 pages"` | `AssertionError` — today they are always singular |

If the context-menu labels are hard to reach without `menu.exec`, extract the
three label expressions into a module-level pure function
`context_menu_labels(count) -> dict[str, str]` and test that; the menu then
reads its labels from it. That is the cheaper of the two and is the
recommended shape.

### `tests/gui_workflow.py` + `tests/test_gui_workflow.py` (extend)

Record after the existing arrange block:

- `report["multi_rotate_applied"]` — select two pages, `rotate_selection()`,
  count how many pages have `rotate_deg == 90`
- `report["remove_reduced_the_document"]` — page count before and after a
  `remove_selection()` with `confirm_remove` replaced

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_real_grid_acts_on_the_whole_selection` | `report["multi_rotate_applied"] == 2` | the workflow raises `AttributeError`; the module fixture fails with a non-zero exit |

## 5. Acceptance

| Check | Command |
|---|---|
| The mutator tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py` |
| The grid tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_arrange_reorder.py tests/test_blank_insertion.py` |
| The workflow test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_gui_workflow.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| No action reads a single row directly any more | `! grep -n "_selected_index()" <(sed -n '/def rotate_selection/,$p' deckle/app/views/arrange_view.py)` — the only remaining caller is `_acting_rows` and `_default_choose_blank_position` |
| No action mutates in a loop | `! grep -nE "for .*:\s*$" -A3 deckle/app/views/arrange_view.py \| grep -E "^[0-9]+-\s+(rotate\|skip)\(self\.state"` — the per-row folding happens inside `_rotated`/`_toggled`, which return a project, not inside a loop over `AppState.mutate` |
| Each new handler mutates exactly once | `for f in rotate_selection skip_selection remove_selection; do test "$(sed -n "/def $f/,/^    def /p" deckle/app/views/arrange_view.py \| grep -c 'state.mutate\|remove(self.state')" = 1 \|\| exit 1; done` |
| Remove exists and is confirmed | `grep -q 'QPushButton("Remove"' deckle/app/views/arrange_view.py && grep -q "_confirm_remove" deckle/app/views/arrange_view.py` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| `[HUMAN]` It reads correctly | Launch Deckle, import a 16-page dummy (`python -m deckle.cli dummy -o d.pdf --pages 16`), rubber-band-select six thumbnails and click Rotate: all six turn, and they stay selected. Right-click: the menu says "Rotate 6 pages 90°". Click Remove: the prompt says 6, and Ctrl+Z brings all six back in place. |

## 6. Out of scope

- **N6's "Skip range..."**, which *states* a skip rather than toggling one.
  Both exist deliberately: `skip_selection` flips what is selected,
  `skip_pages` sets a stated range. Do not merge them.
- **N7's keyboard bindings.** N7 binds `Delete` to skip-toggle and `R` to
  rotate, both routed through these methods. Whether `Delete` should mean
  *Remove* once Remove exists is a real question and N7's §6 hands it to
  whichever spec lands second — if N10 lands after N7, revisit it then; the
  answer recommended here is **no**: `Delete` on a page grid that mostly wants
  skipping should stay non-destructive, and Remove keeps its confirmed button.
- **Removing a page from the *source file*.** Nothing here touches a PDF;
  `Project.pages` is a list of references (`models.py:479-493`).
- **B13** (imports with no "still current" guard). Unrelated, same view family.
- **M1's control-table refactor.** `ArrangeView` is not the 1,723-line panel M1
  is about.
- **A "select all skipped" or "invert selection" action.** Useful, unasked for,
  and each is one more thing that must agree with `_acting_rows`.

## 7. decisions.md entry

```
## 2026-09-05 — Rotate and Skip ignored the selection they were shown
- Symptom: The arrange grid is in ExtendedSelection mode -- a comment records that Qt's SingleSelection default "made every multi-page path below unreachable" -- and the context menu counts the selection to build "Move 12 pages to...". Then Rotate and Skip both call a handler that reads `currentRow()`, so twelve selected pages produce one rotated page and no message. And there was no way to remove a page at all: a 312-page scan whose front matter is merely skipped is still 312 pages in the grid, in the `.deckle`, and in every source-hash check `load_project` runs on open.
- Fix: One `_acting_rows()` -- the selection, falling back to the current row so a keyboard user with nothing selected is not stuck -- and every toolbar button, menu item and shortcut goes through it. Rotate advances each page from its OWN angle, so four presses is a full turn wherever each started. Skip toggles, matching what the button has always done. A new `state.remove_pages` mutator deletes, behind an injectable confirmation, below a separator at the bottom of the menu, undoable like everything else. The menu labels now count, the way Move's already did.
- Surfaces: Every handler mutates ONCE for the whole selection, so rotating six pages is one Ctrl+Z rather than six -- the same reason `reorder_to` exists beside `reorder`. And each re-selects afterwards, because `refresh()` rebuilds every item and drops the selection; `move_pages` had already learned that and nothing else had.
- Watch: Skip and Remove now sit near each other and only one is destructive. Remove is separated, counted, confirmed, and says in its prompt that Skip is probably what you meant.
- Commit: <fill in>
```

## 8. Traps

- **`refresh()` rebuilds every item and drops the selection.**
  `move_pages` records this at `arrange_view.py:820-824`. Every new action must
  re-select afterwards or the second press of Rotate does nothing.
- **`_select_rows` must be called after `refresh()`, not before**, and it calls
  `setCurrentRow(rows[0])` *first* because that method "selects that row alone,
  discarding anything selected before it" (`arrange_view.py:836-838`).
- **`currentRow()` and `selectedIndexes()` are different things.** A row can be
  current without being selected. `_acting_rows` prefers the selection and falls
  back — reversing that order would make a rubber-band selection act on one page.
- **`selectedIndexes()` returns a `QModelIndex` per selected cell.** The grid is
  single-column so one per row, and `_selected_indices` already sorts and reads
  `.row()`; do not assume it is de-duplicated if a column is ever added.
- **`set_rotation` normalises with `% 360`** (`state.py:141`), so `_rotated`
  must not wrap as well or 270 + 90 becomes 0 twice.
- **Chaining pure mutators inside one `mutate` is deliberate.** `_rotated`
  folds `set_rotation` over the rows and returns one project; calling
  `rotate(state, row, ...)` in a loop would push one undo entry per page and
  schedule N autosaves.
- **Constructing a real `QMainWindow` under pytest exits 127 here**
  (`tests/test_gui_workflow.py:9`), but `ArrangeView` alone constructs fine.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
