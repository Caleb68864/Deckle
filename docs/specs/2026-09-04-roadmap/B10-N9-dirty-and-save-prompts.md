# B10 / N9 — A dirty flag, a title marker, and Save / Discard / Cancel before anything destroys work

**Roadmap item:** `docs/ROADMAP.md` B10 (the bug) and N9 (the same thing seen as a feature)
**Depends on:** B9 (`project_path` must be a live property before "has this been saved" can be asked), B35 §3 (recovery re-prompt — same method), B35 §4 (`cancel_autosave`)
**Blocks:** N7 (a Ctrl+Q / Ctrl+W binding must go through the same prompt)
**Size:** M
**Decision needed first:** none

---

## 1. Context

Deckle has no idea whether the job on screen has been saved, and never asks.

Three ways to lose an afternoon, all silent:

1. **Close.** Import 266 pages, arrange them for an hour, click the X.
   `_on_close_event` flushes the autosave, stops the threads and accepts the
   event. For a project that has never been saved, `project_path` is `None`,
   so `autosave_path` is `None` and `flush_autosave` is a no-op — there is no
   file, nothing to recover from, and no prompt. The work is simply gone.

2. **Open project.** `open_project` replaces `self.state` with a fresh
   `AppState` without asking anything. The same afternoon's work, discarded
   by a menu action the user thought was additive.

3. **Import.** `ImportView.import_path` runs
   `state.mutate(lambda project: replace(project, pages=page_list))` —
   replacing every page in the document. Undo can bring them back, but only
   for the 50-mutation window and only if the user realises in time.

And nothing on screen distinguishes a saved job from an unsaved one. The
window is titled `"Deckle"`, permanently, whichever project is open and
whatever state it is in.

There is a fourth, smaller one in the same family. The autosave recovery
prompt is Yes/No, and **No permanently deletes the autosave**
(`main.py:864-869`) — a destructive answer behind a button labelled "No",
with no third option for "not now".

## 2. Current code

### The close path

`deckle/app/main.py:1119-1138`:

```python
    def close(self) -> None:
        """Flush the pending autosave and close the window.

        :returns: nothing. The flush is not optional: the debounce window
            is exactly the interval a closing app would otherwise lose.
        """
        self.state.flush_autosave()
        self.stop_background_work()
        self.window.close()

    def _on_close_event(self, event) -> None:
        """Shut down cleanly however the window was closed.

        :param event: the ``QCloseEvent``; always accepted. Refusing to
            close because a render is running would trap the user.
        :returns: nothing.
        """
        self.state.flush_autosave()
        self.stop_background_work()
        event.accept()
```

`event.accept()` is unconditional, and the docstring's reason ("refusing to
close because a render is running would trap the user") is about *renders* —
it does not cover refusing on the user's own Cancel.

### The open path

`deckle/app/main.py:945-1015`, `open_project` — the state swap at 1004 with
nothing before it:

```python
        project = self._recover_autosave_if_offered(path, project)

        recent.record(path)
        self._refresh_recent_menu()
        ...
        clear_sheet_cache()
        self.state = AppState(project, project_path=path)
```

and its entry point, `main.py:923-943`, `_on_open_project_clicked`, which
opens a file dialog and calls `open_project(path)` with no prior question.

### The import path

`deckle/app/views/import_view.py:173-200`:

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

    def import_path(self, source_path: str) -> None:
```

and the replacement itself, `import_view.py:48`:

```python
    state.mutate(lambda project: replace(project, pages=page_list))
```

### The title

`deckle/app/main.py:415`:

```python
        self.window.setWindowTitle("Deckle")
```

`grep -rn "setWindowTitle\|setWindowModified\|isWindowModified" deckle/ tests/`
finds `setWindowTitle` at `main.py:415` and five in `print_dialog.py` (dialog
and message-box titles). **`setWindowModified` and `isWindowModified` appear
nowhere.**

### The save path, whose success is not reported

`deckle/app/main.py:1017-1060`, `_on_save_project_clicked` — five exits, all
returning `None`:

```python
        if not self.state.project.pages:
            self.status_bar.showMessage(NOTHING_TO_SAVE_MESSAGE)
            return
        ...
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Save project", suggested, "Deckle projects (*.deckle)"
        )
        if not path:
            return
        ...
        problem = output_path_problem(path)
        if problem is not None:
            self.status_bar.showMessage(problem)
            log_event("project_path_rejected", path=path, detail=problem)
            return

        try:
            save_project(self.state.project, path)
        except OSError as exc:
            self.status_bar.showMessage(describe_write_failure(path, exc))
            log_exception("project_write_failed", exc, path=path)
            return
        self.state.project_path = path
```

A prompt whose Save button leads to a cancelled file dialog has to know that
the save did not happen.

### The recovery prompt

`deckle/app/main.py:884-895`, the injected seam — the pattern to follow, and
the thing to replace:

```python
    def _default_confirm_recovery(self, project_name: str) -> bool:
        """Ask whether to take the autosave. Replaceable for tests."""
        QMessageBox = _qt_message_box()
        answer = QMessageBox.question(
            self.window,
            "Recover unsaved changes?",
            f"{project_name} has changes that were never saved -- Deckle "
            "either closed unexpectedly or was closed without saving.\n\n"
            "Recover them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
```

wired at `main.py:570-572`:

```python
        # Injected so the recovery prompt can be driven headlessly, the
        # way `print_dialog` injects `_confirm_resume`.
        self.confirm_recovery = self._default_confirm_recovery
```

and `_qt_message_box`, `main.py:368-372`:

```python
def _qt_message_box():
    """``QMessageBox``. Patchable seam, like :func:`_new_thread`."""
    from PySide6.QtWidgets import QMessageBox

    return QMessageBox
```

### The history, which is *not* the answer

`deckle/app/state.py:224-232`:

```python
    @property
    def can_undo(self) -> bool:
        """:returns: whether there is a snapshot to go back to."""
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        """:returns: whether an undone change can be reapplied."""
        return bool(self._redo_stack)
```

`deckle/app/state.py:206-207` — bounded:

```python
        self._undo_stack: deque[Project] = deque(maxlen=undo_depth)
        self._redo_stack: deque[Project] = deque(maxlen=undo_depth)
```

`DEFAULT_UNDO_DEPTH = 50` (`state.py:37`).

### Existing tests that touch this code

- `tests/test_autosave_recovery.py:95-107` — the `_Window` double, which
  builds `self.confirm_recovery = lambda name: answer` with `answer` a
  **bool**, and binds
  `MainWindow._recover_autosave_if_offered.__get__(self)`. Six tests use it
  (lines 141, 150, 159, 171, 189). **All must be updated deliberately.**
- `tests/test_shutdown.py` — the `PROBE` calls `window.close()` and
  `window.window.closeEvent(QCloseEvent())` and expects both to exit 0.
- `tests/gui_workflow.py:222-236` — builds a second `MainWindow` and calls
  `reopened.open_project(project_path)`; `tests/test_gui_workflow.py:194-209`
  asserts on the result.
- `tests/test_hardening_printing.py:271-297` — the `_FakeWindow` pattern for
  driving unbound `MainWindow` methods.
- `tests/test_import_view.py` — 17 tests, none of which go through
  `_pick_pdf`/`_pick_images`.

## 3. Change

### 3.1 What "dirty" means

**Dirty is: the current `Project` object is not the one that was last written
to (or read from) disk.** `AppState` records the project object at
construction and at every explicit save, and `is_dirty` is one identity
comparison.

```python
self._saved_project is not self._project
```

**Chosen: object identity.** `Project` is a frozen dataclass and every
mutation goes through `AppState.mutate`, which builds a **new** object via
`dataclasses.replace`. So a different object means an edit happened, and the
same object means one did not — including after undoing back to the saved
state, because `undo` restores the *identical* snapshot object rather than
reconstructing it. The check is O(1), which matters because it runs on every
mutation to keep the title marker live.

**Rejected: `==` on the project.** `Project` is a dataclass with `eq=True`, so
value comparison works, but it is O(pages) — a deep compare of a 266-element
page list every time the title is refreshed. Its only advantage is calling a
project clean after a user types `36`, then types `18` back; identity calls
that dirty. That errs toward one extra prompt, which is the safe direction.

**Rejected: a hash of the project.** `Project` carries `list` fields
(roadmap M8), so it is unhashable; a hash would mean serialising it, which is
the save it is trying to decide about.

**Rejected: `can_undo`.** Four reasons, the second decisive:

1. It answers a different question. `can_undo` describes the *history*;
   dirtiness describes the relationship between the current project and the
   file on disk. Those coincide only by accident.
2. **It never goes back to `False` after a save.** Saving does not clear the
   undo stack and must not — throwing away 50 steps of history on Ctrl+S
   would be a worse bug than the one being fixed. So Import → Save → close
   would prompt every time, on a project with nothing to lose. A dirty flag
   that is always on is a dirty flag nobody reads.
3. **The stack is bounded at 50.** Past 50 mutations the oldest snapshots are
   discarded, so the stack cannot be a record of what was saved even in
   principle.
4. It is wrong in the other direction too: `open_project` builds a fresh
   `AppState` with an empty stack, so a project recovered from an autosave —
   which genuinely differs from the file on disk — would read as clean.

### 3.2 `AppState` gains the flag

1. **`state.py`, `AppState.__init__`** — record the constructed project as the
   saved one, after the `_project_path` assignment B9 introduces:

   ```python
        #: The project as it last existed on disk. Compared by IDENTITY:
        #: `Project` is frozen and every edit goes through `mutate`, which
        #: builds a new object, so a different object means an edit
        #: happened. `undo` restores the identical snapshot, so undoing
        #: back to the saved state correctly reads as clean again.
        self._saved_project = project
   ```

2. **`state.py`, new members** on `AppState`, placed after the `can_redo`
   property:

   ```python
    @property
    def is_dirty(self) -> bool:
        """Whether the project differs from what is on disk.

        Deliberately NOT :attr:`can_undo`. Saving does not clear the undo
        stack -- throwing away 50 steps of history on Ctrl+S would be a
        worse bug -- so `can_undo` never returns to ``False`` after a save,
        and a dirty flag that is always on is one nobody reads. The stack
        is also bounded at 50, so it cannot record what was saved even in
        principle.

        :returns: whether closing now would lose work.
        """
        return self._saved_project is not self._project

    def mark_saved(self) -> None:
        """Record the current project as the one on disk.

        Called after an explicit Save, never after an autosave: the
        autosave is a crash net, not the user's file, and treating it as a
        save would make the title stop saying there is work to lose.

        :returns: nothing.
        """
        with self._lock:
            self._saved_project = self._project

    def mark_dirty(self) -> None:
        """Record that the project differs from disk without editing it.

        For exactly one caller: a project built from a recovered autosave.
        It was never written to the ``.deckle`` the user is about to close,
        so it is unsaved work even though no mutation has happened in this
        session.

        :returns: nothing.
        """
        with self._lock:
            self._saved_project = None
   ```

   `None` is safe as the "definitely dirty" sentinel: `self._project` is
   never `None`.

3. **`state.py`, class docstring** — add to the ivar block:

   ```
    :ivar is_dirty: whether the project differs from what is on disk.
        Identity against the last saved snapshot; see the property.
   ```

### 3.3 The prompt: an enum and one method per question

4. **`main.py`, module level**, after the message constants at line 73 —
   pure Python, no Qt, so it stays importable headlessly:

   ```python
   class SaveAnswer(enum.Enum):
       """What a user chose when told they have unsaved work.

       An enum rather than a bool because there are three answers and only
       one of them is "carry on": Cancel means the action the user asked
       for does not happen at all. Returned by a METHOD on ``MainWindow``
       rather than read out of an inline ``QMessageBox``, so headless tests
       can replace the method -- the same seam ``confirm_recovery`` and
       ``PrintDialog._confirm_resume`` already use.
       """

       SAVE = "save"
       DISCARD = "discard"
       CANCEL = "cancel"
   ```

   with `import enum` added to the module imports at line 12.

5. **`main.py`, module level** — the prompt wording, as constants so a test
   can assert on them without duplicating strings:

   ```python
   UNSAVED_TITLE = "Save changes?"

   UNSAVED_CONSEQUENCE = {
       "close": "Closing Deckle now discards them.",
       "open": "Opening another project discards them.",
       "import": "Importing replaces this document and discards them.",
   }
   """What the user is about to lose, per action.

   Named per action rather than written once, because "discard" is
   abstract until it says WHAT: someone who has spent an hour ordering 266
   pages needs to read that this is the click that throws it away.
   """

   NEVER_SAVED_NAME = "This job"
   """Stands in for a filename when the project has never been saved.

   Not "Untitled": a project with no file is exactly the case where
   closing loses everything, and a name that looks like a filename invites
   the reader to assume there is a file.
   """
   ```

6. **`main.py`, `MainWindow`, new method** — the default prompt, beside
   `_default_confirm_recovery`:

   ```python
    def _default_confirm_discard(self, action: str) -> SaveAnswer:
        """Ask whether to save before ``action`` discards the work.

        :param action: one of ``"close"``, ``"open"``, ``"import"`` -- the
            key into :data:`UNSAVED_CONSEQUENCE`.
        :returns: the user's answer.

        Replaceable for tests, the way :meth:`_default_confirm_recovery`
        is. Three buttons, not two: Cancel has to be distinguishable from
        Discard, because one of them is the button someone reaches for when
        they realise mid-click that they did not mean it.
        """
        QMessageBox = _qt_message_box()
        name = (
            os.path.basename(self.state.project_path)
            if self.state.project_path
            else NEVER_SAVED_NAME
        )
        box = QMessageBox(self.window)
        box.setWindowTitle(UNSAVED_TITLE)
        box.setText(
            f"{name} has unsaved changes.\n\n{UNSAVED_CONSEQUENCE[action]}"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return SaveAnswer.SAVE
        if answer == QMessageBox.StandardButton.Discard:
            return SaveAnswer.DISCARD
        return SaveAnswer.CANCEL
   ```

   `Cancel` is the fall-through: a dialog dismissed with Escape or the window
   manager's X must mean "do nothing", never "discard".

7. **`main.py:570-573`, `__init__`** — inject it beside `confirm_recovery`:

   ```python
        # Injected so the recovery prompt can be driven headlessly, the
        # way `print_dialog` injects `_confirm_resume`.
        self.confirm_recovery = self._default_confirm_recovery
        self.confirm_discard = self._default_confirm_discard
```

8. **`main.py`, `MainWindow`, new method** — the shared gate every
   destructive action goes through:

   ```python
    def _allow_discarding_work(self, action: str) -> bool:
        """Ask before ``action`` throws away unsaved work.

        :param action: ``"close"``, ``"open"`` or ``"import"``.
        :returns: whether the caller may proceed.

        Silent when there is nothing to lose -- a clean project, or an
        empty one. A prompt on a document with no edits is what teaches
        someone to dismiss prompts, and the one that matters is the one
        they then dismiss without reading.

        Choosing Save and then cancelling the file dialog counts as
        Cancel: the work is still unsaved, so proceeding would discard
        exactly what the user just tried to keep.
        """
        if not self.state.is_dirty or not self.state.project.pages:
            return True
        answer = self.confirm_discard(action)
        if answer is SaveAnswer.CANCEL:
            return False
        if answer is SaveAnswer.DISCARD:
            return True
        return self._on_save_project_clicked()
   ```

### 3.4 Wiring the three actions

9. **`main.py:1017`, `_on_save_project_clicked`** — return `bool`, and mark
   the state saved. Change the signature and every `return` to
   `return False`, except the final path:

   ```python
    def _on_save_project_clicked(self) -> bool:
        """Save the current job as a ``.deckle``.

        :returns: whether it was written. The unsaved-changes prompt's Save
            button needs to know: a save the user then cancelled at the
            file dialog has not happened, and proceeding would discard
            exactly what they tried to keep.
        """
   ```

   and after `self.state.project_path = path`:

   ```python
        self.state.mark_saved()
        recent.record(path)
        self._refresh_recent_menu()
        self.status_bar.showMessage(f"Saved project to {path}")
        log_event("project_saved", path=path, pages=len(self.state.project.pages))
        self._sync_title()
        return True
   ```

   The five early returns become `return False`: no pages (1026), no path
   from the dialog (1038), `output_path_problem` (1046), `OSError` (1053).
   Note `self.save_project_button.clicked.connect(self._on_save_project_clicked)`
   at line 564 is unaffected — Qt ignores a slot's return value.

10. **`main.py:1129-1138`, `_on_close_event`** — the only place the close is
    refused:

    ```python
    def _on_close_event(self, event) -> None:
        """Shut down cleanly however the window was closed.

        :param event: the ``QCloseEvent``. Accepted unless the user
            themselves cancelled at the unsaved-changes prompt.
        :returns: nothing.

        A render still running is NOT a reason to refuse -- that would trap
        the user, and `stop_background_work` settles the threads instead.
        Unsaved work is a different question, and the refusal is the user's
        own Cancel rather than the window overriding them.
        """
        if not self._allow_discarding_work("close"):
            event.ignore()
            return
        self.state.flush_autosave()
        self.stop_background_work()
        event.accept()
    ```

11. **`main.py:1119-1127`, `close()`** — unchanged in behaviour, documented:

    ```python
    def close(self) -> None:
        """Flush the pending autosave and close the window, unconditionally.

        :returns: nothing.

        The PROGRAMMATIC path: no unsaved-changes prompt, because the
        caller has already decided. `_on_close_event` is the user path and
        is where the prompt lives. Anything new that closes on a user's
        behalf -- N7's Ctrl+Q -- must go through
        `_allow_discarding_work("close")` first.

        The flush is not optional: the debounce window is exactly the
        interval a closing app would otherwise lose.
        """
    ```

12. **`main.py:923-943`, `_on_open_project_clicked`** — ask before the file
    dialog, so the user is not made to pick a file and *then* told they
    cannot have it:

    ```python
        if not self._allow_discarding_work("open"):
            return

        from PySide6.QtWidgets import QFileDialog
    ```

    `open_project(path)` itself is **not** gated — it is the drivable seam
    that `tests/gui_workflow.py:223` and the Recent-projects menu use, and
    gating it would put a modal inside a method whose docstring says it exists
    to be driven without one. The Recent menu's `action.triggered` at
    `main.py:917` therefore needs the gate too:

    ```python
                action.triggered.connect(
                    lambda _checked=False, target=path: (
                        self._allow_discarding_work("open") and self.open_project(target)
                    )
                )
    ```

13. **`main.py`, `open_project`** — a recovered project is unsaved work.
    Capture what was loaded, and mark the state dirty when recovery replaced
    it:

    ```python
        loaded = project
        project = self._recover_autosave_if_offered(path, loaded)
        ...
        self.state = AppState(project, project_path=path)
        if project is not loaded:
            # Recovered from the autosave: this is NOT what is in the
            # .deckle, so closing without saving would lose it again --
            # which is exactly the trip the user just took.
            self.state.mark_dirty()
        self.import_view.state = self.state
    ```

    and at the end of the method, `self._sync_title()`.

14. **`import_view.py`, `ImportView.__init__`** — an injectable gate,
    defaulting to "yes", so the view stays usable standalone:

    ```python
        #: Asked before an import REPLACES the current document. Returns
        #: whether to proceed. `MainWindow` points this at its
        #: unsaved-changes prompt; a bare `ImportView` (and every test that
        #: builds one) just imports.
        self.confirm_replace = lambda: True
    ```

    documented in the class docstring's ivar block.

15. **`import_view.py`, `_pick_pdf` and `_pick_images`** — ask before the file
    dialog:

    ```python
    def _pick_pdf(self) -> None:
        if not self.confirm_replace():
            return
        QFileDialog, *_ = _qt_widgets()
        ...
    ```

    the same two lines in `_pick_images`. **Not** in `import_path`:
    that is the programmatic seam, called directly by
    `tests/test_shutdown.py`'s probe and by anything scripted, and it must
    stay promptless for the same reason `open_project` does.

16. **`main.py`, `__init__`**, after `self.import_view = ImportView(...)` at
    line 439:

    ```python
        self.import_view.confirm_replace = lambda: self._allow_discarding_work("import")
    ```

17. **`main.py`, `_recover_autosave_if_offered`** — three answers, so
    declining is no longer a destructive "No". Replace the bool branch:

    ```python
        answer = self.confirm_recovery(os.path.basename(path))
        if answer is SaveAnswer.CANCEL:
            # Leave the autosave where it is and open the saved project.
            # "Not now" was unreachable before: No DELETED the file.
            return project
        if answer is SaveAnswer.DISCARD:
            try:
                os.remove(autosave_path)
            except OSError as exc:  # noqa: BLE001 -- declined, never fatal
                log_exception("autosave_discard_failed", exc, path=autosave_path)
            return project
    ```

    and the accept branch continues as it does (plus B35 §3's rename).

18. **`main.py:884-895`, `_default_confirm_recovery`** — return a
    `SaveAnswer`:

    ```python
    def _default_confirm_recovery(self, project_name: str) -> SaveAnswer:
        """Ask whether to take the autosave. Replaceable for tests.

        :param project_name: the project's filename.
        :returns: ``SAVE`` to recover, ``DISCARD`` to delete the autosave,
            ``CANCEL`` to leave it and be asked again next time.

        Three buttons, because the old Yes/No prompt hid a destructive
        answer behind "No": declining DELETED the autosave, permanently,
        with nothing saying so. "Not now" was unreachable.
        """
        QMessageBox = _qt_message_box()
        box = QMessageBox(self.window)
        box.setWindowTitle("Recover unsaved changes?")
        box.setText(
            f"{project_name} has changes that were never saved -- Deckle "
            "either closed unexpectedly or was closed without saving.\n\n"
            "Recover them?"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Save).setText("Recover")
        box.button(QMessageBox.StandardButton.Discard).setText("Delete them")
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return SaveAnswer.SAVE
        if answer == QMessageBox.StandardButton.Discard:
            return SaveAnswer.DISCARD
        return SaveAnswer.CANCEL
    ```

### 3.5 The title marker

19. **`main.py:415`** — `[*]` is Qt's placeholder for the modified marker; it
    must be present in the title or `setWindowModified` prints a warning and
    shows nothing:

    ```python
        self.window.setWindowTitle("Deckle[*]")
    ```

20. **`main.py`, `MainWindow`, new method**:

    ```python
    def _sync_title(self) -> None:
        """Name the open project in the title, and mark it modified.

        ``[*]`` is Qt's placeholder for the modified marker and MUST be in
        the title string -- ``setWindowModified`` with no ``[*]`` warns and
        shows nothing. Qt renders it as the platform's convention: an
        asterisk on Windows and Linux, a dot in the close button on macOS.

        :returns: nothing.
        """
        name = (
            os.path.basename(self.state.project_path)
            if self.state.project_path
            else None
        )
        self.window.setWindowTitle(f"{name} -- Deckle[*]" if name else "Deckle[*]")
        self.window.setWindowModified(self.state.is_dirty)
    ```

21. **`main.py`, call `_sync_title()`** from every point the project or the
    path can change. Four sites, chosen so no mutation path is missed:

    - `_sync_document_actions` (line 585), last line — this already runs from
      `_on_imported` and `_on_pages_changed`;
    - `_on_layout_changed` (line 604), last line — the layout panel's
      mutations arrive only here;
    - `_after_history_change` (line 822), last line — undo and redo;
    - `_on_save_project_clicked` and `open_project`, per steps 9 and 13.

    And once at the end of `__init__`, after `self._sync_document_actions()`
    at line 580 (which now calls it), so a window opened on a project path
    is titled correctly from the start.

22. **`docs/api/`** — no new module. `SaveAnswer` lives in `main.py`, which
    has a page. `tests/test_docs_coverage.py` stays green.

### 3.6 Signatures

```python
class SaveAnswer(enum.Enum):  # SAVE / DISCARD / CANCEL

class AppState:
    is_dirty: bool                      # property, get only
    def mark_saved(self) -> None
    def mark_dirty(self) -> None

class MainWindow:
    confirm_discard: Callable[[str], SaveAnswer]      # injected attribute
    confirm_recovery: Callable[[str], SaveAnswer]     # WAS -> bool
    def _default_confirm_discard(self, action: str) -> SaveAnswer
    def _allow_discarding_work(self, action: str) -> bool
    def _sync_title(self) -> None
    def _on_save_project_clicked(self) -> bool        # WAS -> None

class ImportView:
    confirm_replace: Callable[[], bool]               # new attribute
```

## 4. Tests

Three layers, because a real `QMainWindow` cannot be constructed under pytest
here — offscreen Qt plus `QMainWindow` exits 127, recorded at
`tests/test_ui_surface.py:537-541`:

- **`AppState`** — plain headless tests, no Qt at all.
- **`MainWindow`'s routing** — unbound methods against a `_Window` double,
  the pattern at `tests/test_autosave_recovery.py:95-107` and
  `tests/test_hardening_printing.py:271-297`. The prompt is a *method*
  returning `SaveAnswer` precisely so the double can supply one.
- **The title and the end-to-end flow** — `tests/gui_workflow.py`, in a
  subprocess, which is the one place a real window exists.

Everything runs with `QT_QPA_PLATFORM=offscreen`.

### `tests/test_app_state.py` — the flag

```python
def test_a_fresh_state_is_not_dirty():
    state = AppState(_make_project(3))
    assert state.is_dirty is False


def test_a_mutation_makes_it_dirty():
    state = AppState(_make_project(3))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    assert state.is_dirty is True


def test_marking_saved_clears_it():
    state = AppState(_make_project(3))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.mark_saved()
    assert state.is_dirty is False


def test_undoing_back_to_the_saved_state_is_clean_again():
    """`undo` restores the IDENTICAL snapshot object, which is what makes
    identity the right comparison."""
    state = AppState(_make_project(3))
    state.mark_saved()
    state.mutate(lambda p: set_rotation(p, 0, 90))
    assert state.is_dirty is True

    state.undo()

    assert state.is_dirty is False


def test_dirtiness_is_not_can_undo():
    """The decisive difference: saving does not clear the undo stack, and
    must not -- so `can_undo` never returns to False and a dirty flag built
    on it would always be on."""
    state = AppState(_make_project(3))
    state.mutate(lambda p: set_rotation(p, 0, 90))
    state.mark_saved()

    assert state.can_undo is True
    assert state.is_dirty is False


def test_marking_dirty_without_editing():
    """For a project built from a recovered autosave: unsaved work with no
    mutation behind it."""
    state = AppState(_make_project(3))
    state.mark_dirty()
    assert state.is_dirty is True


def test_the_stack_bound_does_not_confuse_dirtiness():
    """`_undo_stack` is bounded at 50, so it cannot record what was saved
    even in principle."""
    state = AppState(_make_project(1))
    state.mark_saved()
    for _ in range(60):
        state.mutate(lambda p: set_rotation(p, 0, (p.pages[0].rotate_deg + 90) % 360))

    assert len(state._undo_stack) == 50
    assert state.is_dirty is True
```

Expected failure on the unfixed tree, all seven:
`AttributeError: 'AppState' object has no attribute 'is_dirty'`.

### `tests/test_save_prompts.py` — a new file for the routing

Modelled on `tests/test_autosave_recovery.py`'s `_Window`. No display needed
for any of it; the double never touches Qt.

```python
"""Being asked before work is thrown away.

Closing with a never-saved project discarded everything in silence: no
dirty flag, no `setWindowModified`, no prompt, and Open project replaced
the current job without asking. This drives the three destructive actions
through `MainWindow`'s own methods, unbound against a stand-in, because a
real QMainWindow under the offscreen platform kills the process (exit 127)
-- the same limitation `tests/test_ui_surface.py` and
`tests/test_hardening_printing.py` work around.

The prompt is a METHOD returning `SaveAnswer` rather than an inline
QMessageBox for exactly this reason: a test can supply the answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import deckle.app.main as app_main
from deckle.app.main import SaveAnswer
from deckle.app.state import AppState
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef


def _project(pages=2):
    return Project(
        pages=[
            SourcePage(
                ref=SourceRef(path="b.pdf", page_index=i, sha256="a" * 64,
                              width_pt=400.0, height_pt=600.0),
                rotate_deg=0, skipped=False,
            )
            for i in range(pages)
        ],
        layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                              binding_edge="left"),
        printer=None,
    )


class _Window:
    """Enough of MainWindow to drive the unsaved-changes gate."""

    def __init__(self, answer=SaveAnswer.CANCEL, pages=2, saved=False):
        self.state = AppState(_project(pages))
        if not saved:
            self.state.mark_dirty()
        self.asked = []
        self.answer = answer
        self.saved = []
        self.confirm_discard = lambda action: (
            self.asked.append(action) or self.answer
        )

    def _on_save_project_clicked(self):
        self.saved.append(True)
        return self.save_succeeds

    save_succeeds = True

    _allow_discarding_work = app_main.MainWindow._allow_discarding_work


def test_a_clean_project_is_never_asked_about():
    """A prompt on a document with no edits is what teaches someone to
    dismiss prompts."""
    window = _Window(saved=True)

    assert window._allow_discarding_work("close") is True
    assert window.asked == []


def test_an_empty_project_is_never_asked_about():
    window = _Window(pages=0)

    assert window._allow_discarding_work("close") is True
    assert window.asked == []


@pytest.mark.parametrize("action", ["close", "open", "import"])
def test_cancel_stops_the_action(action):
    window = _Window(answer=SaveAnswer.CANCEL)

    assert window._allow_discarding_work(action) is False
    assert window.asked == [action]
    assert window.saved == []


def test_discard_proceeds_without_saving():
    window = _Window(answer=SaveAnswer.DISCARD)

    assert window._allow_discarding_work("close") is True
    assert window.saved == []


def test_save_saves_and_then_proceeds():
    window = _Window(answer=SaveAnswer.SAVE)

    assert window._allow_discarding_work("close") is True
    assert window.saved == [True]


def test_save_that_the_user_cancels_at_the_file_dialog_stops_the_action():
    """Proceeding would discard exactly what they just tried to keep."""
    window = _Window(answer=SaveAnswer.SAVE)
    window.save_succeeds = False

    assert window._allow_discarding_work("close") is False
```

Expected failure on the unfixed tree: `ImportError: cannot import name
'SaveAnswer' from 'deckle.app.main'`.

### `tests/test_save_prompts.py` — the close event

```python
class _CloseWindow(_Window):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.flushed = []
        self.stopped = []
        self.state.flush_autosave = lambda: self.flushed.append(True)

    def stop_background_work(self, timeout_ms=5000):
        self.stopped.append(True)

    _on_close_event = app_main.MainWindow._on_close_event


class _Event:
    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_cancelling_at_the_prompt_keeps_the_window_open():
    """The regression. `event.accept()` was unconditional, so a never-saved
    project was discarded in silence."""
    window = _CloseWindow(answer=SaveAnswer.CANCEL)
    event = _Event()

    window._on_close_event(event)

    assert event.accepted is False
    assert window.stopped == [], "the threads were torn down for a cancelled close"


def test_discarding_at_the_prompt_closes_and_settles_the_threads():
    window = _CloseWindow(answer=SaveAnswer.DISCARD)
    event = _Event()

    window._on_close_event(event)

    assert event.accepted is True
    assert window.flushed == [True]
    assert window.stopped == [True]


def test_a_clean_project_closes_with_no_prompt():
    window = _CloseWindow(saved=True)
    event = _Event()

    window._on_close_event(event)

    assert event.accepted is True
    assert window.asked == []
```

Expected failure on the unfixed tree for the first:
`AssertionError: assert True is False` — the close is accepted regardless.

### `tests/test_import_view.py` — the import gate

```python
def test_picking_a_pdf_asks_before_replacing_the_document():
    """Import replaces every page. Undo can bring them back, but only
    within the 50-mutation window and only if the user notices."""
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    started = []
    view.import_path = lambda path: started.append(path)
    view.confirm_replace = lambda: False

    view._pick_pdf()

    assert started == []


def test_a_bare_import_view_still_just_imports():
    """The gate defaults to yes, so the view stays usable standalone --
    and every test in this file that never sets it keeps working."""
    from deckle.app.views.import_view import ImportView

    view = ImportView(_state())
    assert view.confirm_replace() is True
```

The first needs `QFileDialog.getOpenFileName` never to be reached, which the
`confirm_replace` returning `False` guarantees — the early return is before
the dialog. Expected failure on the unfixed tree:
`AttributeError: 'ImportView' object has no attribute 'confirm_replace'`.

### `tests/gui_workflow.py` + `tests/test_gui_workflow.py` — the title

The subprocess harness is where a real `MainWindow` exists. Add to
`tests/gui_workflow.py`, right after the import block at line 68:

```python
    report["title_clean"] = window.window.windowTitle()
    report["modified_after_import"] = window.window.isWindowModified()
```

and after the project is saved at line 217, replacing the direct
`save_project` call with the real button path is out of scope — instead add:

```python
    window.state.mark_saved()
    window._sync_title()
    report["modified_after_save"] = window.window.isWindowModified()
```

and in `tests/test_gui_workflow.py`:

```python
def test_the_title_carries_the_modified_marker(report):
    """N9: the window was titled "Deckle", permanently, whichever project
    was open and whatever state it was in."""
    assert "[*]" in report["title_clean"], (
        "setWindowModified needs [*] in the title or it warns and shows nothing"
    )
    assert report["modified_after_import"] is True
    assert report["modified_after_save"] is False
```

Expected failure on the unfixed tree: the subprocess raises
`AttributeError: 'MainWindow' object has no attribute '_sync_title'`, so the
module-scoped `report` fixture fails with the traceback in stderr.

### Existing tests that must be updated deliberately

**`tests/test_autosave_recovery.py`.** `_Window.__init__` (line 98) builds
`self.confirm_recovery = lambda name: answer` with a bool, and six tests pass
`answer=True` / `answer=False`. `confirm_recovery` now returns a `SaveAnswer`.
Update the double:

```python
    def __init__(self, answer):
        from deckle.app.main import MainWindow

        self.confirm_recovery = lambda name: answer
```

and the call sites:

| Line | Was | Becomes |
|---|---|---|
| 143 `test_accepting_returns_the_autosaved_project` | `_Window(answer=True)` | `_Window(answer=SaveAnswer.SAVE)` |
| 152 `test_declining_keeps_the_saved_project` | `answer=False` | `answer=SaveAnswer.DISCARD` |
| 163 `test_declining_deletes_the_autosave` | `answer=False` | `answer=SaveAnswer.DISCARD` |
| 181 `test_nothing_is_asked_when_there_is_nothing_to_recover` | `answer=True` | `answer=SaveAnswer.SAVE` |
| 197 `test_an_unreadable_autosave_falls_back_to_the_saved_project` | `answer=True` | `answer=SaveAnswer.SAVE` |

and add the new third answer, which had no button before:

```python
def test_cancelling_the_recovery_keeps_both(tmp_path):
    """"Not now" was unreachable: the old prompt was Yes/No and No
    permanently deleted the autosave."""
    project_path, autosave_path = _project_with_autosave(tmp_path, 36.0, 72.0)
    window = _Window(answer=SaveAnswer.CANCEL)

    kept = window._recover(project_path, _loaded(project_path))

    assert kept.layout.gutter_pt == 36.0
    assert os.path.exists(autosave_path), "Cancel must not delete anything"
    assert autosave_recovery_offer(project_path) == autosave_path
```

**`tests/test_shutdown.py`.** The `PROBE` calls `window.close()` (unchanged —
no prompt) and `window.window.closeEvent(QCloseEvent())`. The latter now goes
through `_allow_discarding_work("close")`. The probe imports and mutates
pages, so the state **is** dirty and the default `confirm_discard` would open
a real modal in a subprocess and hang until the 180s timeout. Add to the
probe's setup block, next to the printer stubs:

```python
window.confirm_discard = lambda action: am.SaveAnswer.DISCARD
```

This is not weakening the test — its subject is thread teardown, and the
prompt is covered in `tests/test_save_prompts.py`.

**`tests/gui_workflow.py`.** It never closes the window (`os._exit(0)` at line
247) and never calls `_pick_pdf`, so no stub is required. It does call
`reopened.open_project(project_path)` at line 223, which is ungated by design.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The dirty flag | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py -k "dirty or marking_saved or undoing_back or stack_bound"` |
| The new prompt file | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_save_prompts.py` |
| Recovery, with the three-answer prompt | `.venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_autosave_recovery.py` |
| The import gate | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_import_view.py` |
| The title, end to end | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_gui_workflow.py -k modified_marker` |
| Shutdown still exits cleanly | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_shutdown.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| `can_undo` is not the dirty flag | `! grep -n "can_undo" deckle/app/state.py \| grep -i dirty` and `! grep -rn "is_dirty.*can_undo\|can_undo.*is_dirty" --include="*.py" deckle/` |
| The title carries Qt's marker | `grep -q 'setWindowTitle("Deckle\[\*\]")' deckle/app/main.py` and `grep -q "setWindowModified" deckle/app/main.py` |
| No inline QMessageBox decides a destructive action | `! grep -n "QMessageBox.question(" deckle/app/main.py` — currently hits line 887; both prompts must go through a `_default_confirm_*` method returning `SaveAnswer` |
| The close event can refuse | `grep -q "event.ignore()" deckle/app/main.py` |
| Save reports success | `grep -q "def _on_save_project_clicked(self) -> bool:" deckle/app/main.py` |
| The three consequences are named | `test "$(grep -c '\"close\":\|\"open\":\|\"import\":' deckle/app/main.py)" -ge 3` |
| [HUMAN] The marker on real hardware | Launch Deckle, import a PDF, and look at the title bar: it must show the modified marker in the platform's convention (an asterisk on Windows/Linux, a dot in the close button on macOS). Save the project; the marker must clear and the title must name the file. Edit again; it must come back. |
| [HUMAN] Cancel really cancels | With unsaved work, click the window's X and press Cancel. The window must stay open and the document must be untouched. Repeat with Open project and with Import PDF. |

## 6. Out of scope

- **N1 — autosave for never-saved projects** under `data_dir()`. This spec
  makes Deckle *ask* before discarding a never-saved project; it does not give
  that project an autosave. The two together close the hole; N1 is the other
  half.
- **N7 — menu bar and keyboard shortcuts.** Ctrl+S / Ctrl+O / Ctrl+Q are N7's.
  When they land they must go through `_allow_discarding_work`; step 11's
  docstring says so.
- **B17 — Open project and Save PDF running synchronously on the GUI thread.**
  The Save button in the prompt inherits whatever `_on_save_project_clicked`
  does today, including its synchronous `save_project`.
- **B9** — the autosave path. Land it first; `_default_confirm_discard` and
  `_sync_title` both read `state.project_path` and expect it to be current.
- **B35 §3** — moving the autosave aside after a successful recovery. Same
  method as step 17; land B35 §3 first and this spec's `SaveAnswer.SAVE`
  branch is the one that gets the rename.
- **B35 §4** — `cancel_autosave`. `open_project`'s gate (step 12) is about
  the user's work; the timer is a separate defect.
- **B13** — the import worker's guards. Step 15 adds a gate *before*
  `import_path`; B13 changes what `import_path` does.
- Do not add a "Don't ask me again" checkbox. A prompt that only appears when
  there is genuinely work to lose does not need one, and the suppression
  setting would be read exactly once, by the person who has just lost a book.
- Do not prompt on `close()`, `open_project(path)` or `import_path(...)` —
  those are the drivable seams, and putting a modal inside them is what would
  make the harnesses hang.

## 7. decisions.md entry

```
## 2026-09-05 — Deckle discarded unsaved work in silence, three different ways
- Symptom: No dirty flag, no `setWindowModified`, no prompt. Import 266 pages, arrange them for an hour, click the X: `_on_close_event` flushed an autosave that does not exist (a never-saved project has `project_path is None`, so `autosave_path` is None and the flush is a no-op) and accepted the event. Open project replaced `AppState` without asking. Import replaced every page. The window was titled "Deckle", permanently, whichever project was open and whatever state it was in. And the recovery prompt hid a destructive answer behind "No": declining DELETED the autosave, with no third option for "not now".
- Fix: `AppState` records the project object it last read from or wrote to disk and compares by IDENTITY — `Project` is frozen and every edit goes through `mutate`, which builds a new object, so a different object means an edit and `undo` restoring the identical snapshot correctly reads as clean again. `MainWindow` gained `_allow_discarding_work(action)`, gating close, Open project (both the button and the Recent menu) and Import behind a three-answer Save / Discard / Cancel prompt; `_on_save_project_clicked` now returns `bool` so a Save the user then cancels at the file dialog counts as Cancel. Title is `"<name> -- Deckle[*]"` with `setWindowModified`. The recovery prompt became Recover / Delete them / Cancel.
- Surfaces: `can_undo` was the obvious dirty flag and is wrong. Saving does not clear the undo stack and must not — throwing away 50 steps of history on Ctrl+S would be a worse bug — so `can_undo` never returns to False after a save, and a flag that is always on is one nobody reads. The stack is also bounded at 50, so it cannot record what was saved even in principle, and `open_project` builds a fresh `AppState` with an empty stack, so a project recovered from an autosave would have read as clean.
- Watch: Both prompts are METHODS returning a `SaveAnswer` enum, injected on the instance the way `confirm_recovery` and `PrintDialog._confirm_resume` already are — never an inline `QMessageBox`. A real `QMainWindow` cannot be constructed under pytest here (exit 127 with the offscreen platform), so a prompt that is not a replaceable seam is a prompt with no test.
- Commit: <fill in>
```

## 8. Traps

- **`setWindowModified` needs `[*]` in the window title.** Without it Qt logs
  `QWidget::setWindowModified: The window title does not contain a '[*]'
  placeholder` and shows nothing. Step 19 is not optional.
- **`Project` is unhashable.** It carries `list` fields (roadmap M8), so any
  design that hashes the project fails at `TypeError: unhashable type:
  'list'`. Identity, not hashing.
- **`AppState.mark_saved` must not be called from `_do_autosave`.** The
  autosave is a crash net, not the user's file; treating it as a save makes
  the title stop saying there is work to lose, which is the bug wearing the
  fix's clothes.
- **`tests/test_shutdown.py`'s `PROBE` calls `closeEvent` on a dirty
  window.** Without the `window.confirm_discard = lambda action:
  am.SaveAnswer.DISCARD` stub it opens a real modal inside a subprocess with
  no one to click it, and the test fails at the 180-second timeout rather than
  with an assertion. Add the stub.
- **`tests/test_autosave_recovery.py` passes bools to `confirm_recovery`.**
  Six call sites, listed in §4. Under the new code a bare `True` compares
  unequal to every `SaveAnswer` member, so `_recover_autosave_if_offered`
  falls through to the CANCEL branch and the tests fail confusingly rather
  than loudly. Update all six.
- **The Recent-projects menu is rebuilt from a lambda** (`main.py:917`). Its
  `triggered` connection needs the gate too, or Recent becomes the one
  unguarded way to replace the document.
- **Gate before the file dialog, not after.** Making someone pick a file and
  then telling them they cannot have it is worse than the silence being
  fixed.
- **`close()` and `open_project(path)` and `import_path(...)` stay
  promptless.** They are the drivable seams — `open_project`'s docstring says
  so explicitly ("Separated from the dialog so the whole flow is drivable
  without a modal") — and `tests/gui_workflow.py:223` and
  `tests/test_shutdown.py` depend on it.
- **A `QMessageBox` dismissed with Escape returns a rejected role.**
  `_default_confirm_discard` falls through to `SaveAnswer.CANCEL` for
  anything that is not Save or Discard, which is the only safe default.
- **A real `QMainWindow` cannot be constructed under pytest here** (exit 127).
  Use the `_Window` doubles and unbound methods; the only real window is in
  `tests/gui_workflow.py`'s subprocess.
- **`python -m deckle` launches the GUI and blocks.** The `[HUMAN]` rows are
  the only places a real window appears; run them by hand.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`.
</content>
