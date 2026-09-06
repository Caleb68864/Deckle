# 00b — Landing order and file collisions

Read after `00-environment.md`. This covers only the application-state,
main-window, import-worker and print-dialog slice: **B9, B10/N9, B12, B13,
B14, B16, B30, B35**.

## Which specs touch which files

| File | B9 | B10/N9 | B12 | B13 | B14 | B16 | B30 | B35 |
|---|---|---|---|---|---|---|---|---|
| `deckle/app/state.py` | ● heavy | ● | | | | | | ● §4 |
| `deckle/app/main.py` | ○ 1 line | ● heavy | | ● | | | ● | ● §3 §4 |
| `deckle/app/views/layout_panel.py` | | | ● heavy | | | | | ● §2 |
| `deckle/app/views/import_view.py` | | ● | | ● heavy | | | | |
| `deckle/app/views/arrange_view.py` | | | | | | | | ● §1 |
| `deckle/app/views/print_dialog.py` | | | | | ● | ● | | |
| `deckle/core/layout.py` | | | | | | | | ● §6 |
| `deckle/core/marks.py` | | | | | | | | ● §7 |
| `deckle/cli.py` | | | | | | | | ● §5 |

● = substantive edit ○ = one line

**Four specs edit `main.py`** (B10, B13, B30, B35 — plus one comment line from
B9), and they touch different methods:

| Spec | Methods in `main.py` |
|---|---|
| B9 | `_on_save_project_clicked` (a comment only) |
| B10/N9 | module docstring header, `__init__`, `_sync_document_actions`, `_on_layout_changed`, `_after_history_change`, `_recover_autosave_if_offered`, `_default_confirm_recovery`, `_refresh_recent_menu`, `_on_open_project_clicked`, `open_project`, `_on_save_project_clicked`, `close`, `_on_close_event`, plus new module-level `SaveAnswer` and constants |
| B13 | `open_project` (3 lines), `stop_background_work` (2 lines + docstring) |
| B30 | module docstring, `refresh_printers`, `show`, `_on_print_clicked` |
| B35 | `_recover_autosave_if_offered` (§3), `open_project` (§4) |

The genuine overlaps are:

- **`open_project`** — B10 step 13, B13 step 9, B35 §4 step 3 all insert
  lines into the same block around `self.state = AppState(project, ...)`.
- **`_recover_autosave_if_offered`** — B35 §3 changes the accept branch;
  B10 step 17 rewrites the decline branch into three answers.
- **`_on_save_project_clicked`** — B9 step 5 adds a comment; B10 step 9
  changes the return type.
- **`layout_panel.py`** — B12 rewrites how the handlers are *connected*
  (`_bind`, `_suspend_handlers`); B35 §2 rewrites what two of those handlers
  *do*. B12 first, then re-read the connection block.

Nothing else conflicts. B14, B16 and B30 are disjoint from everything else in
the slice.

## Recommended order

Serial, if one agent is doing all of it:

1. **B9** — `state.py`. Smallest, and B10 and B35 §4 both build on the
   `project_path` property.
2. **B14** — `print_dialog.py`. Disjoint; land it any time.
3. **B16** — `print_dialog.py`. Disjoint from everything except B14, and
   they touch different methods (`_default_ask_resume_count` /
   `_offer_resume` vs. `__init__` / `_resolve_profile`).
4. **B30** — `main.py`. Only spec touching `refresh_printers`, `show`,
   `_on_print_clicked`.
5. **B12** — `layout_panel.py`. Must precede B35 §2.
6. **B13** — `main.py` + `import_view.py`. Adds three lines to
   `open_project`.
7. **B35** — the seven sub-sections. §2 needs B12; §4 needs B9; §3 must
   precede B10.
8. **B10/N9** — largest `main.py` change, and it reads `project_path` (B9),
   rewrites the recovery branch B35 §3 just edited, and gates the
   `open_project` block B13 just added to.

## Running in parallel

Three streams that do not collide:

- **Stream A (print dialog):** B14 → B16. Touches nothing else.
- **Stream B (layout panel):** B12 → B35 §2. Touches nothing else.
- **Stream C (state / window):** B9 → B30 → B13 → B35 §1 §3 §4 §5 §6 §7 →
  B10/N9. Strictly serial within itself.

B35's seven sub-sections are independent of each other and can be split
further: §1 (`arrange_view.py`), §5 (`cli.py`), §6 (`core/layout.py`) and §7
(`core/marks.py`) collide with nothing in this slice at all and can be done by
anyone at any time.

## Shared test files

Two specs each add tests to `tests/test_app_state.py` (B9, B10, B35 §4),
`tests/test_print_dialog.py` (B14, B16), `tests/test_import_view.py` (B13,
B10) and `tests/test_autosave_recovery.py` (B35 §3, B10). Append to the
section the spec names rather than reorganising the file, and re-run the whole
file rather than only `-k` your own tests — `pytest -k <pattern>` with no
match exits 5, not 0.

Two harnesses are edited by more than one spec and must be kept consistent:

- `tests/gui_workflow.py` — B30 (the `refresh_printers` lambda gains
  `quiet=False`), B10 (title assertions).
- `tests/test_shutdown.py`'s `PROBE` — B13 (a new `import_running` branch),
  B30 (the same lambda change), B10 (a `confirm_discard` stub, without which
  the probe hangs to its 180-second timeout).
