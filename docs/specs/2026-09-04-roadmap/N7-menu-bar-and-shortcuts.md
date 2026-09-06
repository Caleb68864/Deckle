# N7 — A menu bar, and keys in the grid and the preview

**Roadmap item:** `docs/ROADMAP.md` N7
**Depends on:** nothing functionally. **Collides in `deckle/app/main.py` with M3** (which splits `MainWindow` into `app/printer_query.py`, `app/project_actions.py`, `app/shutdown.py`), and with **N4**, **N8**, **N13**, **B9/B10/N9**. It also collides in `arrange_view.py` with **N6** and **N10**, and in `preview_view.py` with nothing else. Recommended order: **N7 → N8 → N10 → N13 → M3 last**, because N7 creates the menu that N13 fills and the public method names that N8 and N10 reuse, and M3 should move finished code rather than be re-merged against four specs.
**Blocks:** N13 (it needs a Help menu to put things in)
**Size:** M
**Decision needed first:** none

---

## 1. Context

Deckle has a window with fourteen buttons in a scrolling left-hand column and
exactly three keyboard shortcuts: Ctrl+Z, Ctrl+Y and Ctrl+Shift+Z, all bound in
`_install_shortcuts` (`main.py:785-800`). Ctrl+O does nothing. Ctrl+S does
nothing. Ctrl+P does nothing. There is no menu bar at all, so on macOS the
application menu is empty, on Windows there is nothing to Alt-navigate, and
every screen reader and keyboard-only user has to tab through a scroll area to
reach Print.

Recent projects is a `QPushButton` with a `QMenu` attached
(`main.py:482-491`) — a menu that exists but is not in a menu bar, with a
comment explaining that burying it "one click deeper than the dialog it exists
to save you from would defeat it". A File ▸ Open Recent submenu is exactly one
click, and it is where people look.

In the arrange grid, skipping a page means clicking it and then clicking the
Skip button; Delete does nothing. In the preview, moving between sheets means
clicking a spinbox 66 times on a 67-sheet book; PageDown does nothing.

Verified:

```bash
$ grep -c "QMenuBar\|setMenuBar" deckle/app/main.py
0
$ grep -n "QShortcut" deckle/app/main.py
794:        from PySide6.QtGui import QKeySequence, QShortcut
797:            QShortcut(QKeySequence.StandardKey.Undo, self.window, self.undo),
798:            QShortcut(QKeySequence.StandardKey.Redo, self.window, self.redo),
799:            QShortcut(QKeySequence("Ctrl+Shift+Z"), self.window, self.redo),
```

## 2. Current code

`deckle/app/main.py:785-800` — every shortcut Deckle has:

```python
    def _install_shortcuts(self) -> None:
        """Bind Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) to the history.
        ...
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        self._shortcuts = [
            QShortcut(QKeySequence.StandardKey.Undo, self.window, self.undo),
            QShortcut(QKeySequence.StandardKey.Redo, self.window, self.redo),
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self.window, self.redo),
        ]
```

`deckle/app/main.py:482-491` — the Recent projects button and its menu:

```python
        self.recent_button = QPushButton("Recent projects", controls)
        self.recent_button.setToolTip(
            "Projects you have opened or saved, most recent first.\n\n"
            "A project on a drive that is not currently connected is "
            "hidden rather than forgotten, and comes back when the "
            "drive does."
        )
        self._recent_menu = _new_menu(self.recent_button)
        self.recent_button.setMenu(self._recent_menu)
        controls_layout.addWidget(self.recent_button)
```

`deckle/app/main.py:897-921` — `_refresh_recent_menu`, which clears and
repopulates `self._recent_menu` and then calls
`self.recent_button.setEnabled(bool(paths))`. Called from `__init__:579`,
`open_project:997` and `_on_save_project_clicked:1058`.

`deckle/app/main.py:361-365` — the patchable menu seam:

```python
def _new_menu(parent):
    """A ``QMenu``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMenu

    return QMenu(parent)
```

The methods the menu will call, all already public or stable:
`_on_open_project_clicked` (`main.py:923`), `open_project` (`945`),
`_on_save_project_clicked` (`1017`), `_on_save_pdf_clicked` (`1062`),
`_on_print_clicked` (`755`), `undo`/`redo` (`802`/`812`),
`refresh_printers` (`674`), `close` (`1119`).

`deckle/app/views/import_view.py:166-183` — the two import entry points, both
private:

```python
        self.import_pdf_button.clicked.connect(self._pick_pdf)
        self.import_images_button.clicked.connect(self._pick_images)
```

```python
    def _pick_pdf(self) -> None:
        QFileDialog, *_ = _qt_widgets()
        path, _filter = QFileDialog.getOpenFileName(self.widget, "Import PDF", "", "PDF files (*.pdf)")
        if path:
            self.import_path(path)

    def _pick_images(self) -> None:
        QFileDialog, *_ = _qt_widgets()
        path = QFileDialog.getExistingDirectory(self.widget, "Import Image Directory")
        if path:
            self.import_path(path)
```

`deckle/app/views/arrange_view.py:656-671, 783-795` — the three grid actions,
all private, and all called from the context menu at `arrange_view.py:735-739`:

```python
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

`deckle/app/views/preview_view.py:612-615, 661-674, 936-941` — the zoom and
navigation entry points:

```python
        self.fit_button.clicked.connect(lambda: self._set_zoom(None))
        self.actual_button.clicked.connect(lambda: self._set_zoom(1.0))
        self.zoom_in_button.clicked.connect(lambda: self._zoom_step(1))
        self.zoom_out_button.clicked.connect(lambda: self._zoom_step(-1))
```

```python
    def go_to_first_sheet(self) -> None:
        self.sheet_spinbox.setValue(0)

    def go_to_last_sheet(self) -> None:
        self.sheet_spinbox.setValue(max(0, len(self.plan.sheets) - 1))
```

`deckle/app/views/preview_view.py:420-445` — `_make_scroll_area(parent,
on_resize, on_ctrl_wheel)`, the existing lazily-built `QScrollArea` subclass
that already overrides `wheelEvent`. This is where `keyPressEvent` goes.

`deckle/app/views/arrange_view.py:395-442` —
`_reorderable_list_widget_class()`, the existing lazily-built `QListWidget`
subclass that already overrides `dropEvent`. This is where the grid's
`keyPressEvent` goes.

**Existing call sites of the methods being renamed (grep):**

- `_pick_pdf` / `_pick_images`: `import_view.py:166,167,173,179` only. **No
  test calls either.**
- `_on_rotate_clicked` / `_on_skip_clicked`: `arrange_view.py:562,563,656,665,735,737`.
  **No test calls either.**
- `_on_insert_blank_clicked`: `arrange_view.py:564,739,783`,
  `tests/test_blank_insertion.py:98,114`, `tests/gui_workflow.py:77`.
- `_set_zoom`: `preview_view.py:612,613,936,938`,
  `tests/test_preview_paint.py:110,111,120,122`.
- `_set_sheet_index`: `preview_view.py:634,701,718`,
  `tests/test_preview_paint.py:196,217`.
- `go_to_first_sheet` / `go_to_last_sheet`: already public;
  `tests/test_preview_paint.py:249,252,264,282,292`.

**Existing tests:** `tests/test_integration.py:97-140` (AST analysis of
`MainWindow.__init__`, the pattern §4 reuses), `tests/test_ui_surface.py`,
`tests/test_preview_paint.py`, `tests/test_blank_insertion.py`,
`tests/test_gui_workflow.py` (subprocess).

**Verified shortcut resolutions on this machine** (offscreen, PySide6 6.11.2):

```
New -> ['Ctrl+N', 'New']          Open  -> ['Ctrl+O', 'Open']
Save -> ['Ctrl+S', 'Save']        Print -> ['Ctrl+P']
Quit -> []                        Undo  -> ['Ctrl+Z', 'Alt+Backspace', 'Undo']
Redo -> ['Ctrl+Y', 'Alt+Shift+Backspace', 'Ctrl+Shift+Z', 'Redo']
ZoomIn -> ['Ctrl++', 'Zoom In']   ZoomOut -> ['Ctrl+-', 'Zoom Out']
Delete -> ['Del']                 Refresh -> ['F5', 'Refresh']
HelpContents -> ['F1', 'Help']
MoveToNextPage -> ['PgDown']      MoveToPreviousPage -> ['PgUp']
MoveToStartOfDocument -> ['Ctrl+Home']  MoveToEndOfDocument -> ['Ctrl+End']
```

**`StandardKey.Quit` resolves to nothing on Linux/X11.** Quit therefore needs
an explicit `Ctrl+Q` alongside the standard key, or it silently does not exist —
which is worse than not offering it.

## 3. Change

### Where the code goes

A new module, `deckle/app/menus.py`, holding one function:

```python
def build_menu_bar(window, parent=None):
    """Build Deckle's menu bar, wired to ``window``'s existing methods.

    Takes the window rather than being a method on it for two reasons.
    ``MainWindow`` is already five things (see M3), and a menu bar is the
    sixth. And a real ``QMainWindow`` cannot be constructed under this
    project's pytest environment at all -- it exits 127 -- while a
    ``QMenuBar`` with no parent constructs fine, so a free function taking
    a duck-typed window is the difference between the menu being tested and
    not.

    Every action calls a method that already exists; this module decides
    only the wording, the grouping and the keys. Actions whose handler is
    absent are omitted rather than added disabled, so a build without N4's
    pass export shows no dead entry.

    :param window: an object exposing the handler methods named in
        :data:`MENU_SPEC`.
    :param parent: the ``QWidget`` to parent the bar to, or ``None``.
    :returns: the ``QMenuBar``, with ``.deckle_actions`` set to a
        ``dict[str, QAction]`` keyed by the stable action ids below.
    """
```

`.deckle_actions` is what makes §4 possible: a test triggers
`bar.deckle_actions["file.open_project"].trigger()` without going near a
window.

New docs page `docs/api/app.menus.rst` and a `app.menus` line in
`docs/api/app.rst`'s toctree — `tests/test_docs_coverage.py` enforces it.

### The menus

Every row: the exact label (with its `&` accelerator), the shortcut, the stable
action id, and the existing method it calls. `SK` means
`QKeySequence.StandardKey`.

**`&File`**

| Label | Shortcut | id | Calls |
|---|---|---|---|
| `&Import PDF...` | `Ctrl+I` | `file.import_pdf` | `window.import_view.pick_pdf` |
| `Import I&mages...` | `Ctrl+Shift+I` | `file.import_images` | `window.import_view.pick_images` |
| *separator* | | | |
| `&Open project...` | `SK.Open` | `file.open_project` | `window._on_open_project_clicked` |
| `Open &Recent` | — (submenu) | `file.recent` | populated by `_refresh_recent_menu` |
| `&Save project...` | `SK.Save` | `file.save_project` | `window._on_save_project_clicked` |
| *separator* | | | |
| `Save &PDF...` | `Ctrl+E` | `file.save_pdf` | `window._on_save_pdf_clicked` |
| `Save &front pass PDF...` | — | `file.save_front_pass` | `window._on_save_pass_clicked("front")` *(omitted unless N4 has landed)* |
| `Save &back pass PDF...` | — | `file.save_back_pass` | `window._on_save_pass_clicked("back")` *(omitted unless N4)* |
| *separator* | | | |
| `&Quit` | `SK.Quit` **and** `Ctrl+Q` | `file.quit` | `window.close` |

`Ctrl+E` for Save PDF, not `SK.SaveAs` (`Ctrl+Shift+S`): Save PDF is not
"save the project under another name", it is a different artefact, and putting
it on the Save-As key is how someone loses a project by muscle memory. `E` for
export.

Quit gets `setShortcuts([QKeySequence(SK.Quit), QKeySequence("Ctrl+Q")])`
because `SK.Quit` is empty on Linux (§2).

**`&Edit`**

| Label | Shortcut | id | Calls |
|---|---|---|---|
| `&Undo` | `SK.Undo` | `edit.undo` | `window.undo` |
| `&Redo` | `SK.Redo` | `edit.redo` | `window.redo` |
| *separator* | | | |
| `&Rotate 90°` | `R` | `edit.rotate` | `window.arrange_view.rotate_selection` |
| `&Skip / unskip` | `SK.Delete` | `edit.skip` | `window.arrange_view.skip_selection` |
| `Insert &blank...` | `Ctrl+B` | `edit.insert_blank` | `window.arrange_view.insert_blank` |

`R` and `Del` are bare keys, so they are given
`Qt.ShortcutContext.WidgetWithChildrenShortcut` scoped to
`window.arrange_view.widget` — see "Two kinds of key" below.

**`&View`**

| Label | Shortcut | id | Calls |
|---|---|---|---|
| `Zoom &In` | `SK.ZoomIn` **and** `Ctrl+=` | `view.zoom_in` | `window.preview_view.zoom_in` |
| `Zoom &Out` | `SK.ZoomOut` | `view.zoom_out` | `window.preview_view.zoom_out` |
| `&Actual size` | `Ctrl+0` | `view.zoom_actual` | `window.preview_view.zoom_actual` |
| `&Fit to window` | `Ctrl+9` | `view.zoom_fit` | `window.preview_view.zoom_fit` |
| *separator* | | | |
| `&Next sheet` | `Ctrl+PgDown` | `view.next_sheet` | `window.preview_view.next_sheet` |
| `&Previous sheet` | `Ctrl+PgUp` | `view.previous_sheet` | `window.preview_view.previous_sheet` |
| `F&irst sheet` | `SK.MoveToStartOfDocument` (`Ctrl+Home`) | `view.first_sheet` | `window.preview_view.go_to_first_sheet` |
| `&Last sheet` | `SK.MoveToEndOfDocument` (`Ctrl+End`) | `view.last_sheet` | `window.preview_view.go_to_last_sheet` |

`Ctrl+=` as a second shortcut on Zoom In because `SK.ZoomIn` is `Ctrl++`, which
on most keyboards is `Ctrl+Shift+=`, and the roadmap names `Ctrl+=` for the
reason everyone else does: it is the key people press.

**`&Print`**

| Label | Shortcut | id | Calls |
|---|---|---|---|
| `&Print...` | `SK.Print` | `print.print` | `window._on_print_clicked` |
| `Print a p&roof sheet with ruler` | — | `print.proof` | `window.print_proof` *(omitted unless N3 has landed)* |
| *separator* | | | |
| `&Refresh printers` | `SK.Refresh` (`F5`) | `print.refresh` | `window.refresh_printers` |

Refresh printers is worth its own item beyond the shortcut: B30 records that
the module docstring claims a refresh "whenever the printer menu is opened" and
no such menu exists. Now one does, and the claim is either made true by this
item or the docstring is corrected. **Correct the docstring** (`main.py:1-8`) to
say the list is enumerated at startup and re-enumerated from Print ▸ Refresh
printers; auto-refreshing on menu-open would put a spooler call that can block
for the OS timeout behind an ordinary hover.

**`&Help`**

| Label | Shortcut | id | Calls |
|---|---|---|---|
| `&Keyboard shortcuts` | `SK.HelpContents` (`F1`) | `help.shortcuts` | `window.show_shortcuts` |

N13 adds `About Deckle` and `Open diagnostics folder` to this menu. N7 creates
it with one entry so it is never empty.

### Two kinds of key, deliberately

Menu shortcuts are **window-scoped and all carry a modifier**, except `R` and
`Del`, which are scoped to the arrange grid. Bare navigation keys are **not**
menu shortcuts at all — they are handled by `keyPressEvent` on the widget that
owns them:

- **Arrange grid** (`_reorderable_list_widget_class`): `Delete` → skip/unskip,
  `R` → rotate 90°. Anything else falls through to `super().keyPressEvent`, so
  arrow-key navigation and type-ahead still work.
- **Preview scroll area** (`_make_scroll_area`): `PageDown`/`PageUp` → next/
  previous sheet, `Home`/`End` → first/last sheet. Anything else falls through,
  so the scroll area still scrolls with the arrow keys.

The rejected alternative was making `PageDown`, `Home` and `End` global menu
shortcuts. They would then fire while the user is inside the gathering-list
`QLineEdit` or a margin spinbox, where `Home` means "start of line" — Qt gives
an application shortcut priority over the focused widget, so the text field
would silently stop working.

### Steps

1. **`deckle/app/views/import_view.py` — make the two pickers public.** Rename
   `_pick_pdf` → `pick_pdf`, `_pick_images` → `pick_images`, update the two
   `clicked.connect` lines (166-167). Add to each docstring:
   *":returns: nothing. Cancelling the dialog imports nothing."* Nothing else
   in the tree references either name (§2).

2. **`deckle/app/views/arrange_view.py` — make the three actions public.**
   Rename `_on_rotate_clicked` → `rotate_selection`, `_on_skip_clicked` →
   `skip_selection`, `_on_insert_blank_clicked` → `insert_blank`. Update
   `arrange_view.py:562-564` and `735-739`. **Keep thin private aliases** for
   `insert_blank` only:

   ```python
       # tests/test_blank_insertion.py and tests/gui_workflow.py call the old
       # name. Kept as an alias rather than renamed in five places at once.
       _on_insert_blank_clicked = insert_blank
   ```

   (`rotate_selection` and `skip_selection` need no alias — nothing outside the
   module calls them.) If **N10** has landed, these are already the multi-select
   versions and no rename is needed; reuse whatever N10 named.

3. **`deckle/app/views/preview_view.py` — four public zoom/nav methods.** Add,
   as thin wrappers over the existing private ones so nothing about the zoom
   model changes:

   ```python
       def zoom_in(self) -> None:
           """Zoom to the next stop up. :returns: nothing."""
           self._zoom_step(1)

       def zoom_out(self) -> None:
           """Zoom to the next stop down. :returns: nothing."""
           self._zoom_step(-1)

       def zoom_actual(self) -> None:
           """Show the sheet at 100%. :returns: nothing."""
           self._set_zoom(1.0)

       def zoom_fit(self) -> None:
           """Fit the whole sheet in the pane. :returns: nothing."""
           self._set_zoom(None)

       def next_sheet(self) -> None:
           """Show the next sheet, stopping at the last.

           :returns: nothing. Clamped through the spinbox, so this is a
               no-op at the end rather than an error -- the same contract
               :meth:`go_to_last_sheet` gives.
           """
           self.go_to_sheet(self.sheet_index + 1)

       def previous_sheet(self) -> None:
           """Show the previous sheet, stopping at the first.

           :returns: nothing.
           """
           self.go_to_sheet(self.sheet_index - 1)
   ```

   `go_to_sheet` already clamps to `[0, len(plan.sheets) - 1]`
   (`preview_view.py:689-690`), so neither wrapper needs its own bounds check.
   Rewire `preview_view.py:612-615` to the four public names.

4. **`deckle/app/views/preview_view.py` — keys in the scroll area.** Extend
   `_make_scroll_area`'s signature to `(parent, on_resize, on_ctrl_wheel,
   on_key)` and add to the subclass:

   ```python
           def keyPressEvent(self, event):  # noqa: N802 - Qt naming
               if not event.modifiers() and on_key(event.key()):
                   event.accept()
                   return
               super().keyPressEvent(event)
   ```

   The `not event.modifiers()` guard keeps Ctrl+Home (the menu's First sheet)
   from being handled twice. `on_key` is a new `PreviewView._on_key(key) -> bool`:

   ```python
       def _on_key(self, key) -> bool:
           """Handle a bare navigation key. ``True`` means it was consumed.

           Bare keys are handled here rather than as menu shortcuts because
           an application shortcut outranks the focused widget: ``Home`` as
           a window shortcut would stop meaning "start of line" in every
           text field in the panel.
           """
           from PySide6.QtCore import Qt

           handlers = {
               Qt.Key.Key_PageDown: self.next_sheet,
               Qt.Key.Key_PageUp: self.previous_sheet,
               Qt.Key.Key_Home: self.go_to_first_sheet,
               Qt.Key.Key_End: self.go_to_last_sheet,
           }
           handler = handlers.get(key)
           if handler is None:
               return False
           handler()
           return True
   ```

   and the construction at `preview_view.py:606-608` becomes
   `_make_scroll_area(self.widget, self._apply_zoom, self._zoom_step, self._on_key)`.
   Also `self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)` so the
   pane can take focus by click or Tab.

5. **`deckle/app/views/arrange_view.py` — keys in the grid.** Extend
   `_reorderable_list_widget_class()`'s inner class with a signal and an
   override:

   ```python
       class ReorderableListWidget(QListWidget):
           reorder_requested = Signal(list, int)
           #: A bare key the view should act on: skip or rotate. A signal
           #: rather than a callback, because the widget is built by a
           #: factory that has no view to call back into yet.
           action_requested = Signal(str)

           def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
               if not event.modifiers():
                   if event.key() == Qt.Key.Key_Delete:
                       self.action_requested.emit("skip")
                       event.accept()
                       return
                   if event.key() == Qt.Key.Key_R:
                       self.action_requested.emit("rotate")
                       event.accept()
                       return
               super().keyPressEvent(event)
   ```

   and in `ArrangeView.__init__`, beside the other connections
   (`arrange_view.py:565-569`):

   ```python
           self.list_widget.action_requested.connect(self._on_action_key)
   ```

   ```python
       def _on_action_key(self, action: str) -> None:
           """Run a keyboard action on the selection.

           Delete toggles skip rather than deleting, because a skipped page
           keeps its place and comes back with one press -- and because
           until N10 there is nothing to delete a page *to*. The menu item
           it mirrors says "Skip / unskip" for the same reason.
           """
           if action == "skip":
               self.skip_selection()
           elif action == "rotate":
               self.rotate_selection()
   ```

   **Delete toggles skip; it does not remove.** N10 adds a Remove action, and
   when it lands the decision of what `Delete` should mean is N10's to revisit —
   §6 says so.

6. **`deckle/app/menus.py` — the new module**, with `build_menu_bar` and a
   module-level `MENU_SPEC` describing the tables above as data
   (`(menu_title, [(label, shortcut_spec, action_id, handler_path)])`), so the
   test in §4 can walk it rather than hard-coding a second copy of the tables.
   `handler_path` is a dotted string like `"import_view.pick_pdf"` resolved with
   `functools.reduce(getattr, path.split("."), window)`; a path that does not
   resolve means the action is skipped.

   Qt imported lazily inside `build_menu_bar`, per the discipline in
   `docs/api/app.rst`. `MENU_SPEC` itself is plain data and importable without
   Qt.

7. **`deckle/app/main.py` — install it.** After the status bar is set
   (`main.py:551-552`) and **before** `self._refresh_recent_menu()` at line 579:

   ```python
           self.menu_bar = build_menu_bar(self, self.window)
           self.window.setMenuBar(self.menu_bar)
           self._recent_menu = self.menu_bar.deckle_actions["file.recent"].menu()
   ```

   Delete the `recent_button` block (`main.py:482-491`) entirely, and the
   `self.recent_button.setEnabled(bool(paths))` line inside
   `_refresh_recent_menu` (`main.py:919`) becomes:

   ```python
               self.menu_bar.deckle_actions["file.recent"].setEnabled(bool(paths))
   ```

   `_refresh_recent_menu` otherwise does not change: it clears and repopulates
   `self._recent_menu`, which is now the submenu's menu. Its docstring gains one
   sentence: *"The menu is now File ▸ Open Recent rather than a button's
   dropdown; the rebuild-rather-than-append rule is unchanged."*

8. **`deckle/app/main.py` — delete `_install_shortcuts`.** Undo and Redo are
   menu items now, with the same standard keys plus Redo's `Ctrl+Shift+Z` (which
   `SK.Redo` already includes on this platform — §2 — but keep it explicit for
   the platforms where it does not). Remove the call at `main.py:582`. Two
   mechanisms for one shortcut is how one of them silently stops working.

9. **`deckle/app/main.py` — the shortcuts dialog.**

   ```python
       def show_shortcuts(self) -> None:
           """List every keyboard shortcut, in a plain dialog.

           :returns: nothing. Built from the same ``MENU_SPEC`` the menu bar
               is, so a shortcut cannot be listed here and not bound, or
               bound and not listed.
           """
           QMessageBox = _qt_message_box()
           box = QMessageBox(self.window)
           box.setWindowTitle("Keyboard shortcuts")
           box.setText(shortcuts_text(self.menu_bar))
           box.setStandardButtons(QMessageBox.StandardButton.Ok)
           box.exec()
   ```

   with `shortcuts_text(menu_bar) -> str` in `menus.py`, pure enough to test:
   it walks `menu_bar.deckle_actions`, skips actions with no shortcut, and
   renders `f"{action.text().replace('&', '')}\t{action.shortcut().toString()}"`
   grouped by menu, plus a trailing block for the two widget-scoped sets:

   ```
   In the page grid
     Skip / unskip                Del
     Rotate 90°                   R

   In the preview
     Next / previous sheet        PgDown / PgUp
     First / last sheet           Home / End
   ```

10. **`deckle/app/main.py` — enable/disable with the document.** In
    `_sync_document_actions` (`main.py:585-602`), after the existing lines:

    ```python
            for action_id in ("file.save_project", "file.save_pdf",
                              "edit.rotate", "edit.skip", "edit.insert_blank"):
                action = self.menu_bar.deckle_actions.get(action_id)
                if action is not None:
                    action.setEnabled(has_pages)
    ```

    and in `_apply_printers` (`main.py:736`), beside
    `self.print_button.setEnabled(has_printers)`:

    ```python
            action = self.menu_bar.deckle_actions.get("print.print")
            if action is not None:
                action.setEnabled(has_printers)
    ```

    `.get` with a `None` guard throughout, because `_apply_printers` is called
    unbound against a stub in `tests/test_hardening_printing.py:272` that has no
    menu bar.

## 4. Tests

A `QMenuBar` with a `None` parent constructs and its actions trigger under
`QT_QPA_PLATFORM=offscreen` — verified on this tree:

```python
bar = QMenuBar(None); m = bar.addMenu("&File")
a = m.addAction("&Open project..."); a.setShortcut(QKeySequence.StandardKey.Open)
a.triggered.connect(lambda: calls.append("open")); a.trigger()
# calls == ["open"], a.shortcut().toString() == "Ctrl+O"
```

So no `QMainWindow` and no subprocess is needed for any of it.

### `tests/test_menus.py` (new)

A `_FakeWindow` built from `SimpleNamespace`s records every call:

```python
class _Recorder:
    def __init__(self): self.calls = []
    def __call__(self, *args): self.calls.append(args)
```

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_bar_has_the_five_menus_in_order` | `[m.title() for m in bar.findChildren(QMenu)][:5]` (or walk `bar.actions()`) is `["&File", "&Edit", "&View", "&Print", "&Help"]` | `ModuleNotFoundError: No module named 'deckle.app.menus'` |
| `test_every_action_calls_its_named_method` | for each id in `MENU_SPEC`, `bar.deckle_actions[id].trigger()` and assert the recorder for that handler path fired exactly once | as above |
| `test_open_project_is_on_the_standard_open_key` | `bar.deckle_actions["file.open_project"].shortcut().toString() == "Ctrl+O"` | as above |
| `test_save_project_is_on_the_standard_save_key` | `... == "Ctrl+S"` | as above |
| `test_print_is_on_the_standard_print_key` | `... == "Ctrl+P"` | as above |
| `test_quit_has_a_shortcut_even_where_the_standard_key_is_empty` | `"Ctrl+Q" in [s.toString() for s in bar.deckle_actions["file.quit"].shortcuts()]` — this is the one §2 measured as empty on Linux | as above |
| `test_zoom_in_accepts_both_plus_and_equals` | the shortcut strings for `view.zoom_in` include `"Ctrl+="` | as above |
| `test_save_pdf_is_not_on_the_save_as_key` | `bar.deckle_actions["file.save_pdf"].shortcut().toString() != "Ctrl+Shift+S"` | as above |
| `test_an_absent_handler_omits_its_action` | a window stub with no `print_proof`; `"print.proof" not in bar.deckle_actions`, and no disabled entry appears in the Print menu | as above |
| `test_open_recent_is_a_submenu` | `bar.deckle_actions["file.recent"].menu()` is a `QMenu` | as above |
| `test_the_shortcut_list_names_every_bound_key` | for every action with a shortcut, its rendered key string appears in `shortcuts_text(bar)`; and `"Del"` and `"PgDown"` appear (the widget-scoped block) | as above |
| `test_no_two_actions_share_a_shortcut` | collect every `toString()` across `deckle_actions`, ignoring empties; no duplicates | as above |

### `tests/test_arrange_reorder.py` (extend)

`ArrangeView` constructs headless.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_delete_toggles_skip_on_the_selection` | build the view, `setCurrentRow(1)`, send a `QKeyEvent(KeyPress, Qt.Key.Key_Delete, NoModifier)` via `QApplication.sendEvent(view.list_widget, event)` | page 1 is skipped, page 0 is not, and the page count is unchanged | `AttributeError: 'ReorderableListWidget' object has no attribute 'action_requested'`, and the page stays unskipped |
| `test_r_rotates_the_selection` | same with `Qt.Key.Key_R` | `pages[1].rotate_deg == 90` | as above |
| `test_ctrl_r_is_left_to_the_list_widget` | `Qt.Key.Key_R` with `ControlModifier` | nothing rotated | as above |
| `test_the_public_action_names_exist` | `hasattr(view, "rotate_selection")`, `skip_selection`, `insert_blank` | `AssertionError` |
| `test_the_old_insert_blank_name_still_works` | `view._on_insert_blank_clicked` is `view.insert_blank` | `AssertionError` if the alias is dropped — this is what keeps `tests/test_blank_insertion.py` and `tests/gui_workflow.py` green |

### `tests/test_preview_paint.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_page_down_advances_one_sheet` | a 4-sheet plan; send `Key_PageDown` to `view.scroll_area` | `view.sheet_index == 1` | `AttributeError: 'PreviewView' object has no attribute '_on_key'`; index stays 0 |
| `test_page_up_at_the_first_sheet_is_a_no_op` | at index 0, send `Key_PageUp` | `sheet_index == 0`, no exception | as above |
| `test_home_and_end_jump_to_the_ends` | `Key_End` then `Key_Home` | `3` then `0` | as above |
| `test_ctrl_home_is_left_to_the_scroll_area` | `Key_Home` with `ControlModifier` from index 2 | `sheet_index == 2` (the menu's `Ctrl+Home` handles it, not this) | as above |
| `test_the_public_zoom_names_exist_and_step` | `view.zoom_in()`, `zoom_out()`, `zoom_actual()`, `zoom_fit()` | `current_scale()` moves up a stop, down a stop, is `1.0`, then `_zoom is None` | `AttributeError` |
| `test_next_sheet_clamps_at_the_last` | at the last sheet, `next_sheet()` | index unchanged | `AttributeError` |

### `tests/test_import_view.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_pickers_are_public` | `hasattr(view, "pick_pdf")` and `hasattr(view, "pick_images")` | `AssertionError` |

### `tests/test_integration.py` (extend — the AST pattern it already owns)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_window_installs_a_menu_bar` | `MainWindow.__init__`'s AST contains a call to `setMenuBar` | `AssertionError` |
| `test_the_recent_projects_button_is_gone` | `"recent_button"` does not appear in `deckle/app/main.py`'s source | `AssertionError` — 6 occurrences today |
| `test_shortcuts_are_bound_in_one_place` | `"QShortcut"` does not appear in `deckle/app/main.py` | `AssertionError` — 2 occurrences today |

### `tests/gui_workflow.py` + `tests/test_gui_workflow.py` (extend)

The only place a real `QMainWindow` runs. Record and assert:

- `report["menu_titles"]` — `[a.text() for a in window.menu_bar.actions()]`
- `report["recent_menu_is_a_submenu"]` —
  `window.menu_bar.deckle_actions["file.recent"].menu() is not None`
- `report["save_pdf_action_enabled_after_import"]`

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_real_window_gets_a_menu_bar` | `report["menu_titles"] == ["&File", "&Edit", "&View", "&Print", "&Help"]` | the workflow script raises `AttributeError` and the module fixture fails with a non-zero exit |

## 5. Acceptance

| Check | Command |
|---|---|
| The menu tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_menus.py` |
| The view tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_arrange_reorder.py tests/test_preview_paint.py tests/test_import_view.py tests/test_blank_insertion.py` |
| The structural tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_integration.py tests/test_gui_workflow.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The new module has a docs page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| There is a menu bar | `grep -q "setMenuBar" deckle/app/main.py` |
| The Recent button is gone | `! grep -n "recent_button" deckle/app/main.py` (today: 6 hits) |
| Shortcuts live in one place | `! grep -n "QShortcut" deckle/app/main.py` (today: 4 hits — the import at 794 and three at 797-799) |
| Bare navigation keys are not window shortcuts | `! grep -nE 'QKeySequence\("(Home\|End\|PgUp\|PgDown\|Delete\|R)"\)' deckle/app/menus.py` |
| The B30 docstring claim is now true or corrected | `grep -n "printer menu is opened" deckle/app/main.py` returns nothing, **or** `grep -q "Refresh printers" deckle/app/menus.py` |
| `[HUMAN]` The menus read correctly | Launch Deckle. Alt+F, Alt+E, Alt+V, Alt+P, Alt+H each open their menu; every accelerator letter is unique within its menu; no item is greyed with no explanation. Press F1 and check the shortcut list matches what the menus show. |

## 6. Out of scope

- **A `New project` item.** Replacing the current project needs a "save first?"
  prompt, which is **B10/N9**. Adding `Ctrl+N` before that exists would be a
  one-keystroke way to discard an hour of work.
- **N10** (multi-select rotate/skip, and a Remove action). N7 renames the three
  grid actions to public names and binds `Delete` to *skip*; when N10 lands, it
  owns the decision of whether `Delete` should mean Remove and `S` should mean
  skip. Whatever it decides, `shortcuts_text` must keep agreeing with it.
- **N13** (Help ▸ About, Help ▸ Open diagnostics folder). N7 creates the Help
  menu with one entry; N13 adds two more.
- **N3 / N4** menu entries. Specified as conditional rows above; they appear if
  and only if those specs have landed, because `build_menu_bar` omits an action
  whose handler does not resolve.
- **M3.** These handlers are exactly what M3 wants to move; `build_menu_bar`
  takes a duck-typed window precisely so M3 can move the methods without
  touching the menu.
- **B30's "enumerate printers whenever the menu opens".** N7 offers an explicit
  Refresh printers item instead, and corrects the docstring. Auto-refresh on
  hover would put an unbounded spooler call behind a mouse movement — the exact
  hang the 2026-08-04 decision-log entry records.
- **macOS menu-role handling** (`QAction.MenuRole`, so About and Quit move to
  the application menu). Worth doing, needs a Mac to verify, and F13's
  packaging question is unanswered.

## 7. decisions.md entry

```
## 2026-09-05 — Deckle had no menu bar and three shortcuts
- Symptom: Fourteen buttons in a scrolling column, `Ctrl+Z`/`Ctrl+Y`/`Ctrl+Shift+Z`, and nothing else. Ctrl+O, Ctrl+S and Ctrl+P did nothing; the macOS application menu was empty; a keyboard-only user tabbed through a scroll area to reach Print. Recent projects was a `QMenu` hanging off a `QPushButton` -- a menu that existed and was not in a menu bar. In the grid, Delete did nothing; in the preview, moving between 67 sheets meant 66 spinbox clicks.
- Fix: `deckle/app/menus.py` builds a File / Edit / View / Print / Help bar from a `MENU_SPEC` table, wired to methods that already existed -- `build_menu_bar(window)` takes a duck-typed window rather than being a method, because a real `QMainWindow` cannot be constructed under this project's pytest at all while a parentless `QMenuBar` can, which is the difference between the menu being tested and not. Recent projects became File > Open Recent. `_install_shortcuts` was deleted: two mechanisms for one key is how one of them silently stops working.
- Surfaces: Bare keys are NOT menu shortcuts. `Delete`/`R` are handled in the grid's `keyPressEvent` and `PgUp`/`PgDown`/`Home`/`End` in the preview scroll area's, because Qt gives an application shortcut priority over the focused widget -- `Home` as a window shortcut would stop meaning "start of line" in every text field in the settings panel.
- Surfaces: `QKeySequence.StandardKey.Quit` resolves to NOTHING on Linux/X11 (measured). An action bound to it alone has no shortcut and looks like it does. Quit carries an explicit `Ctrl+Q` alongside it, and a test asserts the string is there.
- Watch: `Save PDF` is on `Ctrl+E`, not `Ctrl+Shift+S`. Save-As muscle memory pointed at a different artefact is how someone overwrites a project with an imposed PDF.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Never run it from a
  script; use `python -m deckle.cli`.
- **Constructing a real `QMainWindow` under pytest exits 127 here**
  (`tests/test_gui_workflow.py:9`, `tests/test_integration.py:81-88`). That is
  the entire reason `build_menu_bar` is a free function taking a duck-typed
  window: a parentless `QMenuBar` constructs fine and its actions trigger.
- **A `QAction` with no shortcut returns an empty `QKeySequence`, not `None`.**
  `action.shortcut().toString()` is `""`; the duplicate-shortcut test must skip
  empties or every unbound action collides with every other.
- **`_apply_printers` is called unbound against a stub** with no `menu_bar`
  (`tests/test_hardening_printing.py:272-297`). Step 10's `.get` +
  `None` guard is not defensive noise; without it four existing tests break.
- **`_refresh_recent_menu` is called from `__init__` at line 579**, so the menu
  bar must be built before it. It is also written to never raise
  (`main.py:905-921`) — keep that: a convenience menu that could not be built
  must not stop the window opening.
- **Qt's shortcut ambiguity is silent.** Two actions on the same sequence
  produce a `QKeySequence::AmbiguousShortcut` and *neither* fires; nothing is
  logged. The duplicate check in §4 is the only thing that catches it.
- **`ArrangeView.refresh()` rebuilds every item and drops the selection**
  (`arrange_view.py:820-824`). A key action that calls `refresh()` leaves
  nothing selected, so holding `R` rotates one page once and then nothing. Not
  fixed here — `rotate_selection` behaves exactly as the button always has —
  but N10 should fix both together.
- **`&` in a label is an accelerator, not a literal.** `action.text()` returns
  it with the ampersand; strip it when rendering the shortcut list, and keep
  every accelerator letter unique *within* its menu or Alt-navigation picks the
  first silently.
- **`deckle/app/menus.py` must import Qt lazily**, inside `build_menu_bar`, so
  `MENU_SPEC` stays importable headlessly — the discipline `docs/api/app.rst`
  states for every module in the package.
