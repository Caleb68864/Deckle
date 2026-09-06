# B9 — Make `autosave_path` follow `project_path` instead of freezing at construction

**Roadmap item:** `docs/ROADMAP.md` B9
**Depends on:** —
**Blocks:** B10/N9 (the dirty flag's "last saved" bookkeeping sits beside this), B35 §4 (cancelling a replaced state's timer needs the same lock discipline), N1
**Size:** S
**Decision needed first:** none

---

## 1. Context

Autosave is dead for every project that is first saved during the session.

`AppState.autosave_path` is computed once, in `__init__`, from the
`project_path` handed to the constructor. `MainWindow` always constructs its
`AppState` with `project_path=None` (a fresh window has no project file yet),
so `autosave_path` is `None`. Saving the project assigns
`self.state.project_path = path` and nothing re-derives `autosave_path`, so it
stays `None` for the life of that `AppState` — and `_schedule_autosave`
returns immediately on `None`.

The user action, exactly: launch Deckle, Import PDF, arrange 266 pages for an
hour, **Save project...**, keep editing, lose the process. Everything after
the save is gone, and — worse — the file the recovery prompt reads
(`<project>.autosave`) was never written at all, so `autosave_recovery_offer`
stays silent and Deckle does not even know work was lost.

Verified:

```bash
.venv/bin/python - <<'EOF'
import os, tempfile
from dataclasses import replace
from deckle.app.state import AppState
from deckle.core.models import LayoutSettings, Project

class T:  # run the debounce synchronously
    def __init__(self, i, f, args=None, kwargs=None): self.f = f; self.daemon = False
    def start(self): self.f()
    def cancel(self): pass

p = Project(pages=[], layout=LayoutSettings(paper=(612.0, 792.0), gutter_pt=0.0,
                                            binding_edge="left"), printer=None)
s = AppState(p, project_path=None, timer_factory=T)
d = tempfile.mkdtemp(); path = os.path.join(d, "book.deckle")
s.project_path = path                     # exactly what main.py:1054 does
s.mutate(lambda pr: replace(pr, layout=replace(pr.layout, gutter_pt=36.0)))
print("autosave_path:", s.autosave_path)
print("dir:", os.listdir(d))
EOF
```

Current output:

```
autosave_path: None
dir: []
```

Expected after this spec: `autosave_path: /tmp/.../book.deckle.autosave` and
`dir: ['book.deckle.autosave']`.

## 2. Current code

`deckle/app/state.py:195-213` — the two attributes are set independently and
only once:

```python
    def __init__(
        self,
        project: Project,
        project_path: str | None = None,
        undo_depth: int = DEFAULT_UNDO_DEPTH,
        autosave_delay_s: float = DEFAULT_AUTOSAVE_DELAY_S,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self._project = project
        self.project_path = project_path
        self.autosave_path = autosave_path_for(project_path)
        self._undo_stack: deque[Project] = deque(maxlen=undo_depth)
        self._redo_stack: deque[Project] = deque(maxlen=undo_depth)
        self._autosave_delay_s = autosave_delay_s
        self._timer_factory = timer_factory
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        # Guards the autosave file, not the project -- see `_do_autosave`.
        self._save_lock = threading.Lock()
```

`deckle/app/state.py:46-60` — the rule itself, already factored out and
public:

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

`deckle/app/main.py:1048-1060` — the only writer of `state.project_path`
outside `AppState.__init__`, in `_on_save_project_clicked`:

```python
        try:
            save_project(self.state.project, path)
        except OSError as exc:
            self.status_bar.showMessage(describe_write_failure(path, exc))
            log_exception("project_write_failed", exc, path=path)
            return
        self.state.project_path = path
        # Saving is how a project first comes into existence, so it
        # belongs in the list as much as opening one does.
        recent.record(path)
```

### Every writer of `state.project_path`

`grep -rn "\.project_path = " --include="*.py" deckle/ tests/` — two hits,
both real:

| Site | What it does |
|---|---|
| `deckle/app/state.py:204` | the constructor's initial assignment |
| `deckle/app/main.py:1054` | Save project, after a successful `save_project` — **the bug** |

`MainWindow.__init__` (`main.py:412`) and `open_project` (`main.py:1004`) pass
`project_path=` to the constructor rather than assigning, so they are already
correct and stay correct.

### Every reader of `autosave_path`

`grep -rn "autosave_path" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `deckle/app/state.py:290` | `_schedule_autosave` — `if self.autosave_path is None: return` |
| `deckle/app/state.py:301` | `_do_autosave` — `path = self.autosave_path` |
| `tests/test_app_state.py:139, 151, 172` | reads it to load the autosave back |
| `tests/test_app_state.py:178` | `assert state.autosave_path is None` for a never-saved project |
| `tests/test_autosave_concurrency.py:82, 107` | reads it to load/open the autosave |
| `tests/test_autosave_concurrency.py:149` | `assert fresh.autosave_path is None` |

`deckle/app/main.py:334` and `:861` use *local* variables of the same name,
computed from `autosave_path_for(...)` / `autosave_recovery_offer(...)`; they
never touch the attribute.

### Existing tests that touch this code

- `tests/test_app_state.py::test_mutate_schedules_debounced_autosave`
- `tests/test_app_state.py::test_autosave_survives_kill_and_reopen`
- `tests/test_app_state.py::test_flush_autosave_cancels_pending_timer_and_saves_immediately`
- `tests/test_app_state.py::test_no_project_path_means_autosave_is_a_noop`
- every test in `tests/test_autosave_concurrency.py`
- `tests/test_autosave_recovery.py` (the pure `autosave_recovery_offer`, which
  is unaffected)

All read `autosave_path`; none writes `project_path` after construction, which
is precisely why the suite is green over a dead feature.

## 3. Change

`autosave_path` becomes a **read-only property derived from
`project_path`**, and `project_path` becomes a property whose setter
re-arms the debounce. Rejected alternative: a `set_project_path()` method that
recomputes — it works, but it leaves the plain attribute assignable, so the
next caller who writes `state.project_path = x` reintroduces exactly this bug.
A property makes the wrong spelling impossible.

1. **`deckle/app/state.py`, `AppState.__init__`** — replace the two
   assignments at lines 204-205 with a single backing field, and move it
   *after* `self._lock` / `self._timer` are created (the setter touches both):

   ```python
   self._project = project
   self._undo_stack: deque[Project] = deque(maxlen=undo_depth)
   self._redo_stack: deque[Project] = deque(maxlen=undo_depth)
   self._autosave_delay_s = autosave_delay_s
   self._timer_factory = timer_factory
   self._timer: threading.Timer | None = None
   self._lock = threading.RLock()
   # Guards the autosave file, not the project -- see `_do_autosave`.
   self._save_lock = threading.Lock()
   self._project_path = project_path
   ```

   Note the reordering: `self._project_path` is assigned **last**, and as the
   private field, so construction does not run the setter's timer logic.

2. **`deckle/app/state.py`, new property pair on `AppState`**, placed
   immediately after `__init__` and before the existing `project` property:

   ```python
   @property
   def project_path(self) -> str | None:
       """Where the project lives on disk, or ``None`` if never saved.

       :returns: the path.
       """
       return self._project_path

   @project_path.setter
   def project_path(self, path: str | None) -> None:
       """Point the project -- and therefore its autosave -- at ``path``.

       Assigning this re-derives :attr:`autosave_path`, which is the whole
       reason it is a property and not a plain attribute. Save project is
       the moment a never-saved project acquires a path, and computing the
       autosave path once in ``__init__`` meant every project first saved
       during a session autosaved nowhere for the rest of that session --
       silently, because ``_schedule_autosave`` returns on ``None``.

       A debounce already in flight is **rescheduled, not cancelled**: the
       edits it was going to write are still unwritten, and they belong at
       the new path. Cancelling would drop them; letting the old timer fire
       would write them beside the old path, which for a Save-project is
       ``None`` and so nowhere at all.

       :param path: the project file, or ``None``.
       :returns: nothing.
       """
       with self._lock:
           previous = self._project_path
           self._project_path = path
           had_pending = self._timer is not None
           if self._timer is not None:
               self._timer.cancel()
               self._timer = None
       if path != previous and had_pending:
           self._schedule_autosave()

   @property
   def autosave_path(self) -> str | None:
       """``f"{project_path}.autosave"``, or ``None``.

       Derived rather than stored so it can never disagree with
       :attr:`project_path`.

       :returns: the autosave path.
       """
       return autosave_path_for(self._project_path)
   ```

   The pending-timer rule, stated once so an implementer does not have to
   infer it: **assigning `project_path` cancels any pending debounce timer
   and, if there was one and the path actually changed, immediately schedules
   a fresh one against the new path.** Net effect for Save project: the
   in-flight debounce that was about to write nowhere now writes
   `<path>.autosave` half a second later. Nothing is lost and nothing is
   written to the old location.

3. **`deckle/app/state.py`, class docstring (lines 174-193)** — update the
   `:ivar:` block, which currently promises a plain attribute:

   ```
   :ivar project_path: the path the project was opened from, or last saved
       to. Assigning it re-derives :attr:`autosave_path`.
   :ivar autosave_path: ``f"{project_path}.autosave"``, or ``None``.
       Derived, not stored.
   ```

4. **`deckle/app/state.py`, module docstring (line 17-21)** — no change
   needed; it already says autosave goes to `AppState.autosave_path`.

5. **`deckle/app/main.py`, `_on_save_project_clicked`** — no code change.
   Line 1054 keeps reading `self.state.project_path = path`; it now does the
   right thing. Add one line of comment above it recording why the assignment
   matters:

   ```python
           # Assigning this re-derives `state.autosave_path` -- a project
           # first saved in this session had no autosave path until now.
           self.state.project_path = path
   ```

6. **`deckle/app/main.py`, `_on_save_project_clicked`** — add **no** flush
   after the assignment. This step exists because "just call
   `flush_autosave()` after Save" is the obvious wrong move: it would write
   `<path>.autosave` *after* `<path>`, making the autosave the newer of the
   two by mtime, which `autosave_recovery_offer` reads as "offer it back" —
   a recovery prompt on every single open, the exact false positive that
   function's "ties go to silence" rule exists to prevent. The first real
   edit after the save schedules the autosave on its own, which is the
   behaviour B9 restores. The chosen design is therefore "derive the path at
   Save, write on the next mutation"; the rejected one is "derive and flush
   at Save".

7. **`docs/api/`** — no new module, so no new `.rst`.
   `tests/test_docs_coverage.py` stays green.

### Signatures

```python
class AppState:
    project_path: str | None            # property, get + set
    autosave_path: str | None           # property, get only
```

No other signature changes. `AppState.__init__`'s parameter list is unchanged.

### Error behaviour

Assigning `autosave_path` now raises `AttributeError: property 'autosave_path'
of 'AppState' object has no setter`. Nothing in `deckle/` or `tests/` does
that (verified by the grep in §2), and the failure is loud rather than silent,
which is the point.

## 4. Tests

All headless; `deckle.app.state` imports no Qt, so no `QT_QPA_PLATFORM` is
needed for these. Add to `tests/test_app_state.py`, using the file's existing
`_ImmediateTimer` and `_make_project` helpers.

### `test_saving_a_project_starts_autosaving_beside_it`

```python
def test_saving_a_project_starts_autosaving_beside_it(tmp_path):
    """B9: autosave was computed once in __init__, so every project first
    saved during a session autosaved nowhere for the rest of it."""
    state = AppState(_make_project(3), timer_factory=_ImmediateTimer)
    assert state.autosave_path is None

    path = str(tmp_path / "book.deckle")
    state.project_path = path

    assert state.autosave_path == path + ".autosave"

    state.mutate(lambda p: set_rotation(p, 0, 180))

    reopened = load_project(state.autosave_path, check_sources=False)
    assert reopened.pages[0].rotate_deg == 180
```

Setup: an `AppState` with no path, as `MainWindow.__init__` builds one.
Assertion in words: after assigning a project path, the autosave path is
derived from it, and the next mutation actually lands on disk there.
Expected failure on the unfixed tree:
`AssertionError: assert None == '/tmp/.../book.deckle.autosave'` at the
second assert.

### `test_autosave_path_is_derived_not_stored`

```python
def test_autosave_path_is_derived_not_stored(tmp_path):
    """Two spellings of the same rule is how they drift apart."""
    state = AppState(_make_project(1), project_path=str(tmp_path / "a.deckle"))

    state.project_path = str(tmp_path / "b.deckle")

    assert state.autosave_path == str(tmp_path / "b.deckle") + ".autosave"

    state.project_path = None
    assert state.autosave_path is None
```

Assertion in words: `autosave_path` tracks every reassignment of
`project_path`, in both directions.
Expected failure on the unfixed tree:
`AssertionError: assert '/tmp/.../a.deckle.autosave' == '/tmp/.../b.deckle.autosave'`.

### `test_a_pending_debounce_is_rescheduled_against_the_new_path`

```python
def test_a_pending_debounce_is_rescheduled_against_the_new_path(tmp_path):
    """The half-second of edits between the last mutation and Save project
    belongs at the NEW path -- not dropped, and not written beside the old
    one."""
    started = []

    class _Recording(_ImmediateTimer):
        def start(self):
            started.append(True)   # record, do NOT fire

    state = AppState(_make_project(2), timer_factory=_Recording)
    state.mutate(lambda p: toggle_skip(p, 0))     # no path yet: no timer
    assert started == []

    path = str(tmp_path / "book.deckle")
    state.project_path = path
    state.mutate(lambda p: toggle_skip(p, 1))
    state.flush_autosave()

    loaded = load_project(state.autosave_path, check_sources=False)
    assert loaded.pages[0].skipped is True
    assert loaded.pages[1].skipped is True
```

Assertion in words: both the edit made before the save and the edit made
after it are present in the autosave file. Expected failure on the unfixed
tree: `TypeError: expected str, bytes or os.PathLike object, not NoneType`
from `load_project(None, ...)`, because `state.autosave_path` is still `None`.

### `test_assigning_the_autosave_path_directly_is_refused`

```python
def test_assigning_the_autosave_path_directly_is_refused(tmp_path):
    """One rule, one spelling. A settable autosave_path is a second place
    for it to be wrong."""
    import pytest

    state = AppState(_make_project(1), project_path=str(tmp_path / "a.deckle"))
    with pytest.raises(AttributeError):
        state.autosave_path = str(tmp_path / "elsewhere")
```

Expected failure on the unfixed tree: `Failed: DID NOT RAISE
<class 'AttributeError'>` — today it is a plain attribute.

### Existing tests that must keep passing unchanged

`tests/test_app_state.py::test_no_project_path_means_autosave_is_a_noop` and
`tests/test_autosave_concurrency.py::test_an_unsaved_project_has_nothing_to_race_over`
both assert `autosave_path is None` for a never-saved project; the derived
property returns `None` for a `None` path, so they hold. Do not edit them.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new tests exist and pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py -k "autosave_path or saving_a_project_starts or pending_debounce"` |
| Autosave and recovery suites still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_app_state.py tests/test_autosave_concurrency.py tests/test_autosave_recovery.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect the two known R0.3/R0.4 failures and no others) |
| `autosave_path` is never stored | `! grep -rn "self\.autosave_path *=" --include="*.py" deckle/` — currently hits `deckle/app/state.py:205`; must find nothing |
| `project_path` has exactly one writer left, and it is Save project | `test "$(grep -rn '\.project_path = ' --include='*.py' deckle/ \| wc -l)" -eq 1 && grep -q 'main.py' <(grep -rn '\.project_path = ' --include='*.py' deckle/)` — the grep currently returns 2 lines (`state.py:204`, `main.py:1054`); after the change `state.py` assigns `self._project_path` and only `main.py:1054` remains |
| `deckle.core` still imports no Qt | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| Repro script now writes the file | paste the `python - <<'EOF'` block from §1; expect `dir: ['book.deckle.autosave']` |

## 6. Out of scope

- **N1 — autosave for never-saved projects** under `data_dir()`, keyed by
  source hash. This spec restores autosave for a project that *has* a path;
  the never-saved hole is N1's.
- **B10/N9 — the dirty flag and save prompts.** Do not add
  `setWindowModified`, a title marker, or any close-time prompt here.
- **B35 §4 — the replaced `AppState`'s live debounce timer.** `open_project`
  swapping `self.state` leaves the outgoing state's timer armed; that is
  B35's sub-section, not this one. Do not add a `cancel_autosave()` here.
- **B13** — cancelling an in-flight import when `AppState` is replaced.
- Do not touch `autosave_recovery_offer` or the recovery prompt.

## 7. decisions.md entry

```
## 2026-09-05 — Autosave was dead for any project first saved in the session
- Symptom: `AppState.autosave_path` was computed once in `__init__` from the constructor's `project_path`. `MainWindow` always constructs with `project_path=None`, and Save project assigned `state.project_path` without re-deriving the autosave path — so it stayed `None`, `_schedule_autosave` returned immediately, and nothing was ever written. Import, arrange for an hour, Save, keep editing, crash: everything after the Save is gone, and `autosave_recovery_offer` stays silent because the file it reads was never created.
- Fix: `project_path` is now a property whose setter re-derives the autosave path, and `autosave_path` is a read-only property over `autosave_path_for(self._project_path)` — derived, never stored, so the two cannot disagree. Assigning a new path cancels a pending debounce and reschedules it against the new path, so the last half-second of edits lands beside the file the user just saved rather than nowhere. Assigning `autosave_path` now raises `AttributeError`.
- Surfaces: Any value cached in `__init__` from a mutable sibling attribute. The whole autosave suite was green over a dead feature because no test ever assigned `project_path` after construction — the one thing the application does.
- Watch: Deliberately did NOT flush the autosave at Save time. That would make the autosave newer than the project by mtime, which `autosave_recovery_offer` reads as "offer it back" — a recovery prompt on every single open, the false positive that function's "ties go to silence" rule exists to prevent.
- Commit: <fill in>
```

## 8. Traps

- **`_schedule_autosave` takes `self._lock`.** The `project_path` setter must
  not call it while already holding that lock. `_lock` is an `RLock`, so a
  re-entrant call would not deadlock — but `_schedule_autosave` starts a timer
  under the lock, and a fired timer's `_do_autosave` takes `_save_lock` and
  then does disk I/O. Release `_lock` before rescheduling, as written in §3
  step 2.
- **Assign the private field last in `__init__`.** If `self._project_path` is
  set before `self._lock` and `self._timer` exist, the setter is never
  involved (it is a private-field assignment), but an implementer who writes
  `self.project_path = project_path` in `__init__` will get
  `AttributeError: '_lock'`. Use `self._project_path = project_path`.
- **`MainWindow.__init__` and `open_project` pass `project_path=` to the
  constructor.** They are already correct and must not be changed to
  post-construction assignment; the constructor path skips the setter's timer
  logic deliberately, because there is no timer yet.
- **`tests/test_autosave_concurrency.py` drives 40 concurrent flushes** and
  asserts no scratch files survive. The setter's cancel-and-reschedule must
  not introduce a second writer; it only re-arms the existing single timer.
- **`python -m deckle` launches the GUI and blocks.** Every repro and
  acceptance line here uses `python -m deckle.cli` or a `python - <<'EOF'`
  script; never run `python -m deckle` from a script.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`. Paste
  §7 and fill the commit line.
</content>
</invoke>
