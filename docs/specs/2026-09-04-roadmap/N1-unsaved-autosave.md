# N1 — Autosave a project that has never been saved, and offer it back

**Roadmap item:** `docs/ROADMAP.md` N1
**Depends on:** B9 (same attribute; see §3 step 1 for the combined rule — land B9 first, or land N1 and let it subsume B9)
**Blocks:** —
**Size:** M
**Decision needed first:** none

---

## 1. Context

Import 200 scanned pages, spend an hour in Arrange, and lose power. Nothing
was written. `AppState.autosave_path` is `None` for the whole session, so
`_schedule_autosave` returns immediately on every one of those mutations and
`flush_autosave` writes nothing at shutdown either.

This is not the same hole as B9 — B9 is "saved once, then autosave stops
because the path was computed in `__init__`". N1 is "never saved at all, so
there was never a path to compute". They meet in one attribute, which is why
§3 states one combined rule for both.

Repro on the current tree (no display needed):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.app.state import AppState, toggle_skip
from deckle.core.models import LayoutSettings, Project, SourcePage, SourceRef

ref = SourceRef(path="book.pdf", page_index=0, sha256="a"*64,
                width_pt=612.0, height_pt=792.0)
project = Project(pages=[SourcePage(ref=ref, rotate_deg=0, skipped=False)],
                  layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                                        binding_edge="left"),
                  printer=None)
state = AppState(project)                       # no project_path: a fresh window
state.mutate(lambda p: toggle_skip(p, 0))       # an hour of work, in one line
state.flush_autosave()                          # what shutdown does
print("autosave_path:", state.autosave_path)
EOF
```

prints

```
autosave_path: None
```

— and no file exists anywhere on disk.

## 2. Current code

`deckle/app/state.py:46-60` — the only rule for where an autosave lives:

```python
def autosave_path_for(project_path: str | None) -> str | None:
    """Where a project's autosave lives, or ``None`` if it has never been
    saved.
    ...
    """
    if project_path is None:
        return None
    return f"{project_path}.autosave"
```

`deckle/app/state.py:203-213` — computed once, in `__init__`, and stored as a
plain attribute (this is B9's half of the defect):

```python
        self._project = project
        self.project_path = project_path
        self.autosave_path = autosave_path_for(project_path)
```

`deckle/app/state.py:289-298` — the guard that makes every unsaved mutation a
no-op:

```python
    def _schedule_autosave(self) -> None:
        if self.autosave_path is None:
            return
```

`deckle/app/state.py:300-303` and `323-337` — the same guard again on the
write path and the flush path.

`deckle/app/main.py:1054` — Save project sets `project_path` and never touches
`autosave_path` (B9):

```python
        self.state.project_path = path
```

`deckle/app/main.py:309-348` — `autosave_recovery_offer(project_path)`, the
existing (pure, mtime-based) recovery decision. It takes a *project path* and
so cannot see an unsaved autosave at all.

`deckle/app/main.py:842-895` — `_recover_autosave_if_offered` / the injectable
`self.confirm_recovery`, called only from `open_project` (`main.py:994`).

**Every reader/writer of `autosave_path` (grep `autosave_path`):**

- `deckle/app/state.py:46` definition of `autosave_path_for`
- `deckle/app/state.py:205` the assignment
- `deckle/app/state.py:290, 301, 323-337` the three guards
- `deckle/app/main.py:16` import, `main.py:334` use inside
  `autosave_recovery_offer`
- `tests/test_app_state.py:139, 151, 172, 178`
- `tests/test_autosave_concurrency.py:82, 107, 149`

**Eviction helper that already exists:** `deckle/core/paths.py:99-171`
`evict_lru_files(directory, max_bytes, on_error=None)`. It is **not**
applicable here and must not be reused: it bounds a directory by *bytes*, and
it ranks by `st_atime` — so merely listing the recovery candidates at startup
(which stats and, on acceptance, reads them) would reorder the eviction queue.
A recovery store wants "keep the newest N by mtime". §3 adds a sibling helper
rather than bending this one.

**Existing tests that touch this code:** `tests/test_app_state.py`
(`test_no_project_path_means_autosave_is_a_noop`, and the autosave round-trip
tests at 139/151/172), `tests/test_autosave_concurrency.py`
(`test_an_unsaved_project_has_nothing_to_race_over`),
`tests/test_autosave_recovery.py` (the whole module — it exercises
`autosave_recovery_offer` and `_recover_autosave_if_offered`).

## 3. Change

### The key

A never-saved project is identified by **what it was made from**, so that the
same import produces the same autosave file across crashes and does not
accumulate one file per launch.

```python
UNSAVED_AUTOSAVE_KEY_CHARS = 16

def unsaved_autosave_key(pages: Sequence[SourcePage]) -> str | None:
    """A stable identity for a never-saved project, from its sources."""
```

Rule, exactly:

1. Build `pairs = {(os.path.normpath(os.path.abspath(p.ref.path)), p.ref.sha256)
   for p in pages if not is_blank_page(p)}` — a **set**, so a page imported
   twice does not change the key, and blanks (which reference no file) do not
   either.
2. If `pairs` is empty, return `None`. A window with nothing imported has
   nothing worth recovering.
3. `payload = "".join(f"{path}\n{sha}\n" for path, sha in sorted(pairs))`
4. `return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:UNSAVED_AUTOSAVE_KEY_CHARS]`

`sorted(pairs)` sorts on the path first, then the hash — page order deliberately
does **not** enter the key, because reordering pages is exactly the work being
protected and must land in the same file.

### Where it lives

```python
UNSAVED_AUTOSAVE_DIR = "autosave"

def unsaved_autosave_path(pages: Sequence[SourcePage]) -> str | None:
    """The autosave file for a never-saved project, or ``None``."""
    key = unsaved_autosave_key(pages)
    if key is None:
        return None
    return str(data_dir(UNSAVED_AUTOSAVE_DIR) / f"{key}.deckle.autosave")
```

`deckle.core.paths.data_dir` (not `config_dir`): this is data the application
accumulates, like the diagnostics log, not a setting. Suffix `.deckle.autosave`
so one glob — `*.autosave` — finds both kinds of autosave, and so the existing
"an autosave is never mistaken for a save" property still reads off the name.

### Steps

1. **`deckle/app/state.py` — make `autosave_path` a property (this is also
   B9's fix).** Delete the `self.autosave_path = ...` assignment at line 205.
   Add:

   ```python
   @property
   def autosave_path(self) -> str | None:
       """Where this project's autosave goes right now.

       Derived rather than stored, because both inputs change during a
       session: Save project sets ``project_path`` (and the autosave must
       follow it), and an import changes which sources a never-saved
       project is keyed on.
       """
       if self.project_path is not None:
           return autosave_path_for(self.project_path)
       return unsaved_autosave_path(self._project.pages)
   ```

   **Combined rule with B9, stated once:** `autosave_path` is *never* stored.
   If B9 lands first as a `project_path` setter that re-derives a stored
   attribute, replace that with this property — two mechanisms for one value
   is the shape the bug came in. If N1 lands first, B9 is already fixed and
   its spec reduces to its tests.

2. **`deckle/app/state.py` — write the parent directory.** `_do_autosave`
   currently calls `save_project(self._project, path)` straight, which raises
   `FileNotFoundError` the first time because `data_dir("autosave")` does not
   exist. Inside `_do_autosave`, before the `save_project` call and inside the
   existing `with self._save_lock:` block:

   ```python
   os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
   ```

   Add `import os` at the top of the module.

3. **`deckle/app/state.py` — discard the unsaved copy once the project is
   really saved.** Add:

   ```python
   def discard_unsaved_autosave(self, pages: Sequence[SourcePage] | None = None) -> None:
       """Delete the never-saved autosave for ``pages``, if there is one.

       Called after Save project: the work now exists in a file the user
       named, so a recovery offer for it at the next launch would be an
       offer to recover something they already have.

       :returns: nothing, and never raises -- a recovery file that will not
           delete must not turn a successful save into an error.
       """
   ```

   Implementation: resolve `unsaved_autosave_path(pages or self._project.pages)`,
   `os.remove` it inside `try/except OSError` with
   `log_exception("unsaved_autosave_discard_failed", exc, path=path)`.
   `deckle/app/state.py` may import `deckle.core.diagnostics` (it is Qt-free).

4. **`deckle/core/paths.py` — add the count-based eviction helper**, directly
   below `evict_lru_files`:

   ```python
   def evict_oldest_files(directory: str | os.PathLike[str], keep: int,
                          pattern: str = "*", on_error=None) -> None:
       """Delete all but the ``keep`` most recently MODIFIED matching files.

       A sibling of :func:`evict_lru_files`, not a variant of it. That one
       bounds a *cache* by bytes and ranks by access time, which is right
       for a cache and wrong here twice over: a recovery store's budget is
       "how many offers is a person willing to read", and merely listing
       the offers at startup touches every file, which would reorder an
       atime ranking.
       """
   ```

   Same total contract as `evict_lru_files`: a missing directory is not an
   error, it never raises, and failures are reported through `on_error(event,
   exception, path)` with events `"autosave_scan_failed"`,
   `"autosave_entry_stat_failed"` and `"autosave_eviction_failed"`. Ranking is
   `st_mtime`, newest first; `keep <= 0` deletes everything matching.

   New public function in an existing module — no new `docs/api/*.rst` needed.

5. **`deckle/app/state.py` — evict on write.** At the end of `_do_autosave`,
   only when `self.project_path is None`:

   ```python
   evict_oldest_files(data_dir(UNSAVED_AUTOSAVE_DIR), UNSAVED_AUTOSAVE_KEEP,
                      pattern="*.deckle.autosave")
   ```

   with `UNSAVED_AUTOSAVE_KEEP = 10` as a module constant (matching
   `recent.MAX_ENTRIES`, for the same reason: long enough to cover the jobs
   someone is moving between, short enough to read).

6. **`deckle/app/state.py` — enumerate what can be offered back.** Add a
   frozen dataclass and a pure lister:

   ```python
   @dataclass(frozen=True)
   class UnsavedAutosave:
       """One never-saved project waiting to be recovered.

       :ivar path: the autosave file.
       :ivar modified_at: its mtime, epoch seconds.
       :ivar page_count: how many pages it holds.
       :ivar first_source: the first page's source path, or "" for none.
       """
       path: str
       modified_at: float
       page_count: int
       first_source: str

   UNSAVED_AUTOSAVE_MAX_AGE_S = 30 * 24 * 60 * 60

   def unsaved_autosave_offers(now: float | None = None) -> list[UnsavedAutosave]:
       """Never-saved autosaves worth offering back, newest first."""
   ```

   Reads every `*.deckle.autosave` under `data_dir(UNSAVED_AUTOSAVE_DIR)`,
   skipping any whose mtime is more than `UNSAVED_AUTOSAVE_MAX_AGE_S` old and
   any that cannot be parsed. `page_count`/`first_source` come from parsing the
   file's JSON `pages` array directly (`json.loads(...)["pages"]`), **not** from
   `load_project` — `load_project` verifies every source hash and raises
   `SourceMissingError` when a source has moved, and a moved source is exactly
   when the recovery matters most. A file that is not readable JSON, or whose
   `pages` is not a list, is skipped silently (it is a corrupt recovery file;
   nothing can be offered from it). Never raises: a missing directory returns
   `[]`.

7. **`deckle/app/main.py` — offer them at startup.** In `MainWindow.__init__`,
   after `self.confirm_recovery = self._default_confirm_recovery`
   (`main.py:572`), add:

   ```python
   self.confirm_unsaved_recovery = self._default_confirm_unsaved_recovery
   ```

   and immediately before `self.refresh_printers()` (`main.py:583`), add:

   ```python
   if project_path is None:
       self._offer_unsaved_recovery()
   ```

   New methods on `MainWindow`:

   ```python
   def _offer_unsaved_recovery(self) -> None:
       """Offer back work from a session that never got as far as Save."""
   ```

   - `offers = unsaved_autosave_offers()`; return immediately if empty.
   - `chosen = self.confirm_unsaved_recovery(offers)`; `None` declines.
   - **Declining deletes nothing.** Stated here and rejected deliberately:
     `_recover_autosave_if_offered` deletes on decline because the work also
     exists in the user's `.deckle`. Here it does not — this file is the only
     copy — so a mis-click must not be destructive. `UNSAVED_AUTOSAVE_MAX_AGE_S`
     and `UNSAVED_AUTOSAVE_KEEP` are what stop the list growing instead.
   - On acceptance: `load_project(chosen.path, on_outside_roots=lambda _p, _r: True)`
     inside `try/except Exception`, reporting
     `f"Could not read the recovered work: {exc}"` on the status bar and
     `log_exception("unsaved_autosave_recovery_failed", exc, path=chosen.path)`.
     `on_outside_roots` returning `True` rather than passing `allowed_roots`,
     because the sources of an unsaved project are wherever the user imported
     from and there is no project directory to reason from.
   - Then adopt it: `clear_sheet_cache()`, `self.state = AppState(project,
     project_path=None)`, rewire `import_view.state` / `arrange_view.state` /
     `layout_panel.state`, `self.arrange_view.refresh()`,
     `self.layout_panel.refresh_from_project()`, `self._on_pages_changed()`,
     `self.status_bar.showMessage(f"Recovered {len(project.pages)} unsaved page(s).")`,
     `log_event("unsaved_autosave_recovered", path=chosen.path, pages=len(project.pages))`.
     The new `AppState` re-derives the same key from the same sources, so
     continuing to edit keeps writing to the same file.

   ```python
   def _default_confirm_unsaved_recovery(self, offers):
       """Ask which unsaved session to pick up, if any."""
   ```

   Uses `QInputDialog.getItem` (the same shape `ArrangeView` already uses for
   "Move to..."), imported lazily:

   - title: `"Recover unsaved work?"`
   - label: `"Deckle closed with work that was never saved. Pick it up?"`
   - items: `[unsaved_autosave_label(offer) for offer in offers] + ["Start a new project"]`
   - `current=0`, `editable=False`
   - returns the matching `UnsavedAutosave`, or `None` for the last entry or a
     cancelled dialog.

   And one pure label function in `state.py` (pure so the wording is testable
   headlessly, matching `_recent_label`'s reasoning in `main.py:351`):

   ```python
   def unsaved_autosave_label(offer: UnsavedAutosave) -> str:
       """A one-line description of an unsaved session, for a list."""
   ```

   Returns exactly
   `f"{os.path.basename(offer.first_source) or 'Untitled'} -- {offer.page_count} page(s), {time.strftime('%Y-%m-%d %H:%M', time.localtime(offer.modified_at))}"`.

8. **`deckle/app/main.py` — drop the unsaved copy on Save project.** In
   `_on_save_project_clicked`, capture the pages before the path is set and
   call the discard after `self.state.project_path = path` (`main.py:1054`):

   ```python
        self.state.project_path = path
        self.state.discard_unsaved_autosave()
   ```

   `discard_unsaved_autosave` re-derives the key from the current pages, which
   are unchanged by the save, so it names the right file.

9. **`deckle/app/main.py` — flush before the window goes.** No change needed:
   `close()` and `_on_close_event` already call `self.state.flush_autosave()`
   (`main.py:1125, 1136`), which now has somewhere to write.

## 4. Tests

Every test below monkeypatches the data root before touching anything —
`monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))` **and**
`monkeypatch.setenv("APPDATA", str(tmp_path))` — because `paths._root` reads
the environment at call time and an unguarded test would write into the
developer's real `~/.local/share/deckle`.

### `tests/test_unsaved_autosave.py` (new)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_an_unsaved_project_still_gets_an_autosave` | `AppState(project)` with two real-path pages, `timer_factory=_ImmediateTimer` (copy the class from `tests/test_app_state.py:41`); `mutate` once | `state.autosave_path` is under `<tmp>/deckle/autosave/`, the file exists, and `load_project(path, check_sources=False).pages` has 2 entries | `AssertionError: assert None is not None` — `autosave_path` is `None` |
| `test_the_key_is_the_same_for_the_same_sources` | two separate `AppState`s over projects with the same refs in **different page order**, one with an extra inserted blank | both `.autosave_path` values are equal | `AttributeError`/`None` — no `unsaved_autosave_key` to import |
| `test_the_key_changes_when_a_source_changes` | two projects, identical but for one `sha256` | paths differ | import error: `unsaved_autosave_key` does not exist |
| `test_an_empty_project_has_no_unsaved_autosave` | `AppState(Project(pages=[], ...))`; `mutate`; `flush_autosave()` | `autosave_path is None` and `list((tmp_path/"deckle"/"autosave").glob("*")) == []` (directory may not exist) | passes today for the wrong reason — keep it, it pins the "blank window writes nothing" rule |
| `test_a_project_of_only_blanks_has_no_unsaved_autosave` | one `make_blank_page` page | `autosave_path is None` | as above |
| `test_saving_the_project_moves_the_autosave_and_deletes_the_unsaved_one` | mutate (writes unsaved file), then `state.project_path = str(tmp_path/"job.deckle")`, `state.discard_unsaved_autosave()`, `state.flush_autosave()` | `<tmp>/job.deckle.autosave` exists and the `<key>.deckle.autosave` does not | `AttributeError: 'AppState' object has no attribute 'discard_unsaved_autosave'` |
| `test_only_the_newest_ten_unsaved_autosaves_survive` | write 13 files named `<hex>.deckle.autosave` into the autosave dir with staggered `os.utime` mtimes, then drive one real autosave | exactly 10 `*.deckle.autosave` files remain, and the 3 oldest by mtime are gone | `ImportError: cannot import name 'evict_oldest_files'` |
| `test_offers_are_newest_first_and_skip_stale_ones` | three valid autosave JSON files, one with mtime 31 days old | `[o.path for o in unsaved_autosave_offers()]` is the two fresh ones, newest first | `ImportError: cannot import name 'unsaved_autosave_offers'` |
| `test_an_offer_survives_a_source_that_has_moved` | write an autosave whose page path points at a file that does not exist | one offer, `page_count == 2`, `first_source` is the recorded path | `ImportError` as above; this is the case `load_project` would have refused |
| `test_a_corrupt_autosave_is_skipped_not_raised` | one file containing `not json` beside one valid file | exactly one offer, the valid one | `ImportError` as above |
| `test_the_offer_label_names_the_source_and_the_page_count` | `UnsavedAutosave(path="x", modified_at=<fixed epoch>, page_count=7, first_source="/tmp/scan/book.pdf")` | `unsaved_autosave_label(...)` starts `"book.pdf -- 7 page(s), "` | `ImportError` |

### `tests/test_unsaved_autosave.py`, `MainWindow` half

Constructing a real `QMainWindow` under pytest exits 127 in this environment
(see `tests/test_gui_workflow.py:9` and `tests/test_integration.py:81-88`), so
drive the new method the way `tests/test_hardening_printing.py:271-297` drives
`_apply_printers`: a `_FakeWindow` stub carrying `state`, `status_bar`,
`import_view`, `arrange_view`, `layout_panel` (each a `SimpleNamespace` with the
methods `_offer_unsaved_recovery` calls) and
`confirm_unsaved_recovery = lambda offers: offers[0]`, then call
`app_main.MainWindow._offer_unsaved_recovery(window)` unbound.

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_startup_offers_the_newest_unsaved_session` | after the unbound call, `window.state.project.pages` has the recovered page count and the status bar says `"Recovered 2 unsaved page(s)."` | `AttributeError: type object 'MainWindow' has no attribute '_offer_unsaved_recovery'` |
| `test_declining_leaves_the_file_on_disk` | `confirm_unsaved_recovery = lambda offers: None`; the autosave file still exists afterwards and `window.state` is unchanged | as above |
| `test_a_window_opened_on_a_project_never_offers_unsaved_work` | AST check on `deckle/app/main.py`: the call to `_offer_unsaved_recovery` in `__init__` is inside an `if project_path is None:` | as above |

### Existing tests that must be updated deliberately

Both currently assert the bug:

- `tests/test_app_state.py::test_no_project_path_means_autosave_is_a_noop`
  (line 176) — `assert state.autosave_path is None` over a project with three
  real-path pages. Rewrite as
  `test_a_project_with_no_sources_has_no_autosave`, building
  `_make_project(0)`, and add the positive case to the new file.
- `tests/test_autosave_concurrency.py::test_an_unsaved_project_has_nothing_to_race_over`
  (line 139) — `assert fresh.autosave_path is None` over six real-path pages,
  plus `assert list(tmp_path.iterdir()) == []`. Both stop being true. Rewrite
  to point `XDG_DATA_HOME` at `tmp_path`, and assert the flush wrote exactly
  one file under `tmp_path/"deckle"/"autosave"` — which is the property the
  test's docstring actually wants ("flushing must not be an error").

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_unsaved_autosave.py` |
| The two rewritten tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py tests/test_autosave_concurrency.py tests/test_autosave_recovery.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect 2 failures, both R0.3/R0.4) |
| `autosave_path` is never assigned anywhere | `! grep -rn "\.autosave_path *=" deckle tests` (today: 1 hit, `deckle/app/state.py:205`) |
| The byte-budget helper was not bent into service | `! grep -n "evict_lru_files" deckle/app/state.py` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| Recovery is offered only for a fresh window | `grep -n -B2 "_offer_unsaved_recovery()" deckle/app/main.py \| grep -q "project_path is None"` |

## 6. Out of scope

- **B9** if it lands first — but §3 step 1 is the same edit, so do not write a
  second mechanism.
- **B10 / N9** (dirty flag, "save before closing?"). N1 makes losing work
  survivable; N9 makes it less likely. Neither replaces the other.
- **B35's "accepting autosave recovery re-prompts on every subsequent open"** —
  that is the *saved-project* path (`autosave_recovery_offer`) and stays as it
  is.
- **B26** (image-folder imports record the temp cache file as the source). An
  unsaved autosave keyed on a path `evict_lru_files` may delete is a real
  interaction, and it is B26's to fix: the key stays correct, the *source*
  behind it is what goes missing, and `unsaved_autosave_offers` is written to
  survive that (§3 step 6).
- **B13** (import results with no "still current" guard). An import that lands
  in an orphaned `AppState` will key an autosave off the orphan's pages. That
  is B13's bug, not this one's.

## 7. decisions.md entry

```
## 2026-09-05 — Work done before the first Save was never autosaved at all
- Symptom: `AppState.autosave_path` was `None` for any project that had never been saved, so every `_schedule_autosave` and `flush_autosave` on a fresh window returned immediately. Import 200 pages, arrange for an hour, lose power: nothing on disk. The recovery design called the unsaved case "silent" and it was.
- Fix: `autosave_path` became a derived property. With a `project_path` it is `<project>.autosave` as before (which also re-derives after Save, closing B9); without one it is `data_dir("autosave")/<key>.deckle.autosave`, where `<key>` is the first 16 hex of the sha256 of the project's sorted, de-duplicated (source path, sha256) pairs -- so the same import lands in the same file across crashes and page order does not change it. `paths.evict_oldest_files` keeps the newest 10. Startup offers them back in a list dialog; declining deletes nothing, because that file is the only copy.
- Surfaces: `evict_lru_files` looked like the eviction helper and is not: it budgets bytes and ranks by atime, and merely listing the recovery offers touches every file. A count-and-mtime sibling was cheaper than a flag on it. `unsaved_autosave_offers` reads the JSON directly rather than through `load_project`, because `load_project` refuses a project whose source has moved -- which is the case recovery exists for.
- Watch: Two tests asserted the bug (`test_no_project_path_means_autosave_is_a_noop`, `test_an_unsaved_project_has_nothing_to_race_over`). A test that pins "this does nothing" is indistinguishable from a test that pins "this must not do anything", and only the docstring tells you which.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for anything scripted.
- **Constructing a real `QMainWindow` under pytest exits 127 here.** Use the
  unbound-method + stub pattern of `tests/test_hardening_printing.py:271`, the
  AST pattern of `tests/test_integration.py:97`, or the subprocess pattern of
  `tests/gui_workflow.py`.
- **`paths._root` reads the environment at call time.** Any test that does not
  set `XDG_DATA_HOME` *and* `APPDATA` writes into the developer's real data
  directory and leaves recovery offers behind that the next launch will show
  them.
- **`_do_autosave` holds `self._save_lock`, not `self._lock`.** The directory
  creation and the eviction belong inside `_save_lock` and must not reach for
  `_lock` — `state.py:317-321` explains why holding the state lock across disk
  I/O is wrong.
- **`autosave_path` becomes a property, so an assignment to it now raises
  `AttributeError`.** Grep before you edit; §5 has the check.
- **`data_dir("autosave")` does not exist on a first run**, and `save_project`
  → `write_text_atomic` → `tempfile.mkstemp(dir=...)` raises
  `FileNotFoundError`, not `OSError` handled anywhere. Step 2 is not optional.
- **The autosave timer is a daemon thread.** A test that writes files and then
  asserts on the directory must drive the debounce synchronously
  (`_ImmediateTimer`) or call `flush_autosave()`; sleeping past 500 ms makes the
  suite slow and flaky.
- **`deckle/app/state.py` must not import PySide6** — its module docstring says
  so and headless tests depend on it. `deckle.core.paths` and
  `deckle.core.diagnostics` are both fine.
