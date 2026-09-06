# M7 — Remove the scaffolding, keep the two things that only look like scaffolding

**Roadmap item:** `docs/ROADMAP.md` M7
**Depends on:** R0.1
**Blocks:** — (F9 reads §3 item 2 before wiring hardware duplex)
**Size:** S
**Decision needed first:** none.

---

## 1. Context

Seven items in one roadmap row, on the grounds that each is code that says
something the program does not do. Read one at a time, **five are dead and
two are not**, and this spec is as much about the two as about the five.

The five that go:

- Two `try/except ImportError` fallbacks for `deckle.core.session_log`,
  each defining a no-op `log_print_job` "used only until
  `deckle.core.session_log` exists". It exists, has since SS-07, is
  imported at the top of both files, and has a test file of its own.
- A `except SourceChangedWarning` around `save_project`, which cannot raise
  it — only `load_project` does.
- A `try: ... finally: pass` at the end of `render_sheet`.
- `_document_loaded` and `_syncing_mode` read with `getattr(self, ..., default)`
  and never initialised, so the default is doing real work in the panel's
  own constructor.
- A signal named `schedule_saved` that carries `"Crop: ..."`,
  `"Could not measure the ink: ..."` and `"Gatherings: ..."`. Four of its
  seven emissions have nothing to do with a schedule.

The two that stay:

- **`submit_duplex`.** Seventy lines of finished Qt printing, deliberately
  unwired, deliberately tested, and pinned by a test that fails the moment
  anything calls it. It is F9's entire raw material and its docstring is
  already honest. Only the `ImportError` scaffolding *in the same file*
  goes.
- **`DRIVERS_IGNORING_ROTATE`.** Empty, and empty on purpose with the reason
  written beside it. **The roadmap is wrong to group this with the others**;
  see §3 item 3.

Why it matters to somebody printing a book: none of this changes output. It
changes what the next person reading these files believes. An `ImportError`
fallback says "this dependency might not be there" and it is; a `getattr`
default says "this attribute might not be set" and, once initialised, it
cannot be; a signal named for one event says the panel only reports that
event. Each is a small false statement in a codebase whose decision log
records catching the same shape — a declaration nothing honours — **five
separate times** (`Mark.cut_line`, `Sheet.back`, the unreachable `.tmp`
skip, `submit_duplex`'s old docstring, and the cache-budget instruction).

## 2. Current code

### Item 1 — the `session_log` fallbacks

`deckle/app/backend.py:47-63`, verbatim:

```python
try:
    from deckle.core.session_log import log_print_job
except ImportError:  # pragma: no cover - SS-07 (persistence/log) not yet landed
    def log_print_job(
        printer: str,
        profile: PrinterProfile,
        sheets: Sequence[int],
        dpi: int,
        pass_index: int,
    ) -> None:
        """Fallback no-op used only until ``deckle.core.session_log`` exists.

        Mirrors the ``log_print_job`` shape from SS-07 exactly so callers
        never have to change once the real module lands.
        """
        return None
```

`deckle/core/print_session.py:45-61` is character-for-character the same
block.

`deckle/core/session_log.py` exists (123 lines, rotating JSON-lines log,
`log_print_job` and `session_log_path`), is covered by
`tests/test_app_directories.py:84-98` and
`tests/test_project_io.py:209-238`, and is imported without a guard by
`deckle/core/diagnostics.py:11` in prose.

The one caller in each file: `deckle/app/backend.py:381` (inside a
`try/except OSError`) and `deckle/core/print_session.py:563` (unguarded —
that asymmetry is **B4**, not this spec).

`Sequence` is imported at `backend.py:33` and `print_session.py:36` and is
still used at `backend.py:53, 147, 296, 477, 556` and
`print_session.py:51, 311` after the blocks go.

### Item 2 — `submit_duplex`, which stays

`deckle/app/backend.py:553` defines it. `deckle/app/backend.py:562-573`:

```python
        """Submit fronts and backs as one hardware-duplex Qt job.

        **Nothing calls this yet.** It is written and complete, and
        ``duplex_modes(printer_name).single_pass_duplex`` reports whether
        it would apply, but no path in Deckle reaches it: the print dialog
        and :class:`~deckle.core.print_session.PrintSession` both drive
        manual duplex unconditionally. Said outright because the previous
        wording -- "only used when ``single_pass_duplex`` is True" --
        described a condition on something that never happens, ...
```

`tests/test_duplex_submission.py:145-176`,
`test_nothing_in_deckle_calls_this_yet`, walks every `.py` under `deckle/`
and fails if any non-comment line matches `\bsubmit_duplex\s*\(` outside a
`def`. Its docstring: *"If this ever fails it is good news -- someone wired
the path up -- but the docstring and the open question about hardware
duplexers versus a hand-measured correction both need revisiting at that
moment."*

`docs/decisions.md` (2026-08-07) explains why the test exists and calls it
*"a deliberate second attempt at the kind of test retired on 2026-08-06 for
pinning an artifact rather than a behaviour -- and the difference is the
**end condition**."*

The only other mention inside `deckle/` is a comment,
`deckle/app/backend.py:282`: `# What the last submit_pass()/submit_duplex() call actually got as`.
That comment is why the guard test strips `#` before matching, and it must
keep saying `submit_duplex()`.

### Item 3 — `DRIVERS_IGNORING_ROTATE`, which also stays

`deckle/app/backend.py:70-73`, verbatim:

```python
# Print drivers known to silently ignore a PDF's /Rotate key. The spike
# report (docs/spikes/qprinter-capability-report.md) is the source of
# truth for entries here; start empty/conservative and only add a driver
# once divergence has actually been observed and reviewed.
DRIVERS_IGNORING_ROTATE: frozenset[str] = frozenset()
```

Read at `backend.py:276` (constructor default), stored at `280`, consumed at
`505`:

```python
                    printer_name in self._drivers_ignoring_rotate,
```

which becomes `_render_sheet_side`'s `ignore_rotate`, which reaches
`export.rotate_pages_180(..., flatten=True)` (`export.py:766-770`).

**That path is tested**:
`tests/test_backend.py:203-217::test_apply_rotate_backs_flattens_for_drivers_that_ignore_rotate`
calls `_apply_rotate_backs(str(src), ignore_rotate=True)` and asserts the
rotation is baked into the content and `/Rotate` is gone.

### Item 4 — the impossible `except`

`deckle/cli.py:1067-1076`, verbatim, in `_cmd_impose`:

```python
    project = Project(pages=list(pages), layout=settings, printer=args.printer)
    try:
        save_project(project, args.output)
    except SourceChangedWarning as exc:
        print(f"error: source changed: {exc.path}", file=sys.stderr)
        return 1
    except OSError as exc:
        _report_write_failure(args.output, exc)
        return 1
```

`deckle/core/project_io.py:396-426`, `save_project`, raises only `OSError`
(its own docstring says so) and calls `write_text_atomic`. The single
`raise SourceChangedWarning` in the package is `project_io.py:560`, inside
`load_project`'s source-hash check.

The other two catch sites are correct and stay: `deckle/cli.py:529` and
`deckle/app/main.py:981`, both wrapping `load_project`.

Tests: `tests/test_project_io.py:117, 174, 189` all raise it from
`load_project`. Nothing tests the `_cmd_impose` branch, because nothing can
reach it.

### Item 5 — `try: ... finally: pass`

`deckle/core/render.py:271-279`, verbatim, the tail of `render_sheet`:

```python
            finally:
                pdf.close()
    finally:
        # Deliberately no cleanup: the path belongs to the sheet cache, and
        # deleting it here would evict an entry the cache still believes it
        # holds -- the next hit would hand back a path that no longer
        # exists.
        pass
```

The outer `try:` opens at `render.py:246`, immediately after
`tmp_path = export.export_sheet_cached(plan, sheet_index)`.

### Item 6 — two attributes that are never initialised

Set: `layout_panel.py:1297` (`self._document_loaded = loaded`, inside
`set_document_loaded`), `1359` and `1363` (`self._syncing_mode = True/False`,
inside `_sync_signature_tab`).

Read with a default:

```
layout_panel.py:1353:        loaded = getattr(self, "_document_loaded", bool(self.state.project.pages))
layout_panel.py:1573:        if getattr(self, "_syncing_mode", False):
```

Both defaults are load-bearing **today**: `__init__` spans lines 687-1205
and calls `_sync_signature_tab()` at line 1182 — before
`set_document_loaded` has ever run, and before
`self.tabs.currentChanged.connect(self._on_mode_tab_changed)` at line 1199.
So on the first pass, `_document_loaded` genuinely does not exist and
`bool(self.state.project.pages)` is the answer.

`set_document_loaded` is called from `deckle/app/main.py:602` and from
`tests/test_ui_surface.py:634, 639, 646`.

### Item 7 — a signal named for one of the seven things it carries

`deckle/app/views/layout_panel.py:700-705`:

```python
        class _Signals(QObject):
            layout_changed = Signal(object)  # SheetPlan
            schedule_saved = Signal(str)  # a user-facing outcome message
```

The comment already says what it is. Every emission:

| Line | Message |
|---|---|
| 1326 | `problem` — a rejected output path for the schedule |
| 1339 | `describe_write_failure(path, exc)` — the schedule write failed |
| 1341 | `f"Saved binding schedule to {path}"` |
| 1620 | `f"Crop: {exc}"` |
| 1632 | `f"Could not measure the ink: {exc}"` |
| 1643 | (a multi-line auto-crop outcome message) |
| 1667 | `f"Gatherings: {exc}"` |

Consumers: `deckle/app/main.py:558`
(`self.layout_panel.schedule_saved.connect(self.status_bar.showMessage)`) and
`tests/test_ui_surface.py:667-669`.

## 3. Change

Seven numbered items. Items 2 and 3 are "do nothing plus one test"; the rest
are edits.

### 1. Delete both `session_log` fallbacks

1a. **`deckle/app/backend.py`** — replace lines 47-63 with:

```python
from deckle.core.session_log import log_print_job
```

Place it with the other `deckle.core` imports (after line 45's `render`
import block), so the file's import section is one block again.

1b. **`deckle/core/print_session.py`** — replace lines 45-61 with:

```python
from deckle.core.session_log import log_print_job
```

placed with the other `deckle.core` imports at lines 40-44.

Do not remove `from typing import Sequence` from either file.

### 2. `submit_duplex`: keep it, and say so

**No code change.** Add one sentence to `tests/test_duplex_submission.py`'s
module docstring, after the paragraph beginning *"The reason to test dead
code rather than delete it"*:

```
A dead-code sweep will find this method and want to delete it. It is not
scaffolding: it is the whole of F9, it is tested, and the offset trap
below is the reason a rewrite from scratch would be worse than keeping
it. ``test_nothing_in_deckle_calls_this_yet`` is what says so out loud.
```

**Do not** delete `submit_duplex`, `duplex_modes`, `DuplexModes`, or
`test_nothing_in_deckle_calls_this_yet`. **Do not** change
`deckle/app/backend.py:282`'s comment — the guard test strips comments
before matching precisely so that this sentence can exist, and rewording it
away would quietly remove the sentence that explains the guard.

### 3. `DRIVERS_IGNORING_ROTATE`: keep it, and pin the emptiness

**The roadmap's characterisation is wrong.** This is not a declared-but-not-
honoured value: the capability it gates is real, reached
(`__init__` → `_drivers_ignoring_rotate` → `_render_sheet_side` →
`rotate_pages_180(flatten=True)`) and tested at
`tests/test_backend.py:203`. It is an allow-list that is empty because no
driver has yet been observed to need it, and the comment above it says
exactly that, naming the spike report as the source of truth for additions.
Deleting it would remove a tested capability's only switch to save four
lines.

What it lacks is what `submit_duplex` has: a test that states the emptiness
is deliberate and has an end condition. Add one — see §4.

No code change.

### 4. Delete the impossible `except`

**`deckle/cli.py`**, `_cmd_impose`, lines 1068-1073. Replace:

```python
    try:
        save_project(project, args.output)
    except SourceChangedWarning as exc:
        print(f"error: source changed: {exc.path}", file=sys.stderr)
        return 1
    except OSError as exc:
```

with:

```python
    try:
        save_project(project, args.output)
    except OSError as exc:
```

Leave the `SourceChangedWarning` import at `deckle/cli.py:40` — it is still
used at line 529, around `load_project`, which does raise it.

### 5. Delete the `try: ... finally: pass`

**`deckle/core/render.py`**, `render_sheet`. Remove the outer `try:` opened
at line 246 and the `finally: pass` at lines 273-279, dedenting the body by
four spaces. Keep the explanation as a plain comment where the `finally`
was, immediately after the `with` block ends:

```python
    # Deliberately no cleanup: the path belongs to the sheet cache, and
    # deleting it here would evict an entry the cache still believes it
    # holds -- the next hit would hand back a path that no longer exists.
```

**Chosen: keep the comment, move it out of the `finally`. Rejected:
deleting it with the block.** The comment is the only place that records why
a function that makes a temp path does not remove it, and it has already
stopped somebody once.

**Dedenting is the risk in this item.** The block being dedented contains
the `with pdfium_guard():` / `pdfium.PdfDocument(...)` pair that
`tests/test_render_concurrency.py::test_every_pdfium_document_is_opened_under_the_guard`
checks by *relative indentation*. Run that file immediately after.

### 6. Initialise the two attributes

**`deckle/app/views/layout_panel.py`**, `__init__`. Immediately after
`self.state = state` (line 707) and before anything builds a widget, insert:

```python
        # Initialised here rather than defaulted at every read. Both are
        # consulted during this constructor -- `_sync_signature_tab()`
        # below runs before `set_document_loaded` ever does, and before
        # `tabs.currentChanged` is connected -- so a `getattr` default was
        # doing real work on the first pass and looked like a guard against
        # nothing.
        self._document_loaded = bool(state.project.pages)
        self._syncing_mode = False
```

`bool(state.project.pages)` is exactly the default that
`layout_panel.py:1353` used, so the first-pass behaviour is unchanged.

Then:

- **line 1353**: `loaded = getattr(self, "_document_loaded", bool(self.state.project.pages))`
  → `loaded = self._document_loaded`
- **line 1573**: `if getattr(self, "_syncing_mode", False):`
  → `if self._syncing_mode:`

### 7. Rename `schedule_saved` to `status_message`

**`deckle/app/views/layout_panel.py`**:

- line 702: `schedule_saved = Signal(str)  # a user-facing outcome message`
  → `status_message = Signal(str)  # any user-facing outcome, not only a schedule`
- line 705: `self.schedule_saved = self._signals.schedule_saved`
  → `self.status_message = self._signals.status_message`
- lines 1326, 1339, 1341, 1620, 1632, 1643, 1667: `self.schedule_saved.emit(`
  → `self.status_message.emit(`

**`deckle/app/main.py:558`**:

```python
        self.layout_panel.status_message.connect(self.status_bar.showMessage)
```

**`tests/test_ui_surface.py:667-669`** — update the two references and the
surrounding test name if it says "schedule"; read it before editing.

**Chosen: `status_message`. Rejected: `schedule_saved` plus a second
signal.** A second signal splits one status bar between two channels and
`main.py` would connect both to the same slot, which is one name too many for
one behaviour — the reasoning `docs/decisions.md` records under *"Deleted
the scale mode"*.

**Coordination.** Four sibling specs in this directory write
`self.schedule_saved.emit(...)` in code they add: `B11-unit-change-misses-trim-and-crop.md:194`,
`B17-off-thread-long-operations.md:162, 173, 593, 855`,
`N5-sewing-station-positions.md:492, 598`, and
`M1-layout-panel-binding-table.md:700, 953, 1199`. Their snippets were
written against the old name. Whoever lands second runs the §5 grep and
renames; the failure is a loud `AttributeError`, not a silent one.

## 4. Tests

### New: `tests/test_scaffolding_is_gone.py`

One file for the five deletions, because the property they share — "this
code says something that is not true" — is not attached to any one module.

```python
"""Five statements the code was making that were not true.

Two `try/except ImportError` fallbacks for a module that has existed
since SS-07 and has a test file of its own. An `except
SourceChangedWarning` around `save_project`, which raises `OSError` and
nothing else. A `try: ... finally: pass`. And two attributes read with
`getattr` defaults that the constructor could simply set.

None of it changed a byte of output, which is why it survived. The cost
is paid by the next reader, who has to work out whether the guard is
guarding something.
"""
```

| Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_the_session_log_is_imported_without_a_fallback` | read the source of `deckle/app/backend.py` and `deckle/core/print_session.py` | neither contains `"except ImportError"` | `AssertionError` naming both files |
| `test_save_project_raises_only_oserror` | `inspect.getsource(deckle.core.project_io.save_project)` | `"raise SourceChangedWarning"` not in it; and `"SourceChangedWarning"` does not appear in `inspect.getsource(deckle.cli._cmd_impose)` | the second half fails: `_cmd_impose` catches it today |
| `test_render_sheet_has_no_empty_finally` | `inspect.getsource(deckle.core.render.render_sheet)` | no line whose stripped form is `"pass"` | `AssertionError` quoting the line |
| `test_the_panel_initialises_its_own_flags` | build a `LayoutPanel` offscreen on `deckle.app.main.default_project()` (follow `tests/test_layout_panel_paper.py`'s construction) | `panel._document_loaded is False` and `panel._syncing_mode is False` **without** any `getattr` default; and `"getattr(self, \"_document_loaded\""` and `"getattr(self, \"_syncing_mode\""` do not appear in the module source | the attribute assertions pass by luck once `_sync_signature_tab` has run; the two source assertions fail |
| `test_the_status_signal_is_not_named_for_one_of_its_messages` | module source of `deckle/app/views/layout_panel.py` and `deckle/app/main.py` | `"schedule_saved"` appears in neither | it appears 9 times in the panel and once in `main.py` |

Source-reading tests are used here on purpose and against this project's own
stated caution about them: `docs/decisions.md` (2026-08-07) retired one kind
of source-reading test for *"pinning an artifact rather than a behaviour"*
and kept another because it had an **end condition**. These have one — they
fail exactly once, when somebody re-introduces the construct, and that
failure is the message.

### New, in the file it belongs to: the two keep-alives

| Test file | Test function | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|---|
| `tests/test_backend.py` | `test_no_driver_is_on_the_ignore_list_yet` | import `deckle.app.backend` | `DRIVERS_IGNORING_ROTATE == frozenset()` | passes today. It is the `test_nothing_in_deckle_calls_this_yet` pattern: an empty allow-list with an end condition, so the next dead-code sweep finds a test saying the emptiness is a decision, and adding a driver is a deliberate act that updates this and cites the spike report |
| `tests/test_backend.py` | `test_an_ignoring_driver_would_actually_flatten` | construct `QtPrintBackend(profile, drivers_ignoring_rotate=frozenset({"Fake Driver"}))`; assert `backend._drivers_ignoring_rotate` carries it | the switch is wired, so the empty default is an empty list and not a dead parameter | passes today; it is what makes the previous row honest rather than a tautology |

`tests/test_duplex_submission.py::test_nothing_in_deckle_calls_this_yet`
must **still pass unchanged** — it is the guard on item 2 and this spec adds
no caller.

### Existing tests to update

- `tests/test_ui_surface.py:667-669` — the `schedule_saved` reference
  (item 7).
- Nothing else. Any other failure means a step above was done wrong.

## 5. Acceptance

| Check | Command |
|---|---|
| No `ImportError` fallbacks remain | `! grep -rn 'except ImportError' deckle/` |
| `session_log` is imported plainly in both files | `grep -n 'from deckle.core.session_log import log_print_job' deckle/app/backend.py deckle/core/print_session.py` |
| `submit_duplex` still exists | `grep -n 'def submit_duplex' deckle/app/backend.py` |
| …and still has no callers | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest "tests/test_duplex_submission.py::test_nothing_in_deckle_calls_this_yet" -q --no-header -p no:cacheprovider` |
| `DRIVERS_IGNORING_ROTATE` still exists | `grep -n 'DRIVERS_IGNORING_ROTATE: frozenset' deckle/app/backend.py` |
| The impossible catch is gone | `test "$(grep -c 'SourceChangedWarning' deckle/cli.py)" = 2` — the import at line 40 and the `load_project` catch at 529 survive; `grep -c` returns `3` today |
| No empty `finally` in `render.py` | `! grep -nE '^\s+pass$' deckle/core/render.py` — matches `render.py:279` today and nothing else |
| No `getattr` defaults for the two flags | `! grep -n 'getattr(self, "_document_loaded"' deckle/app/views/layout_panel.py` and `! grep -n 'getattr(self, "_syncing_mode"' deckle/app/views/layout_panel.py` |
| The signal is renamed everywhere | `! grep -rn 'schedule_saved' deckle/ tests/` |
| …and the new name is connected | `grep -n 'status_message.connect' deckle/app/main.py` |
| The new file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_scaffolding_is_gone.py -q --no-header -p no:cacheprovider` |
| The touched areas pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_backend.py tests/test_duplex_submission.py tests/test_render_concurrency.py tests/test_render.py tests/test_ui_surface.py tests/test_project_cli.py tests/test_cli.py tests/test_print_session.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| `[HUMAN]` The status bar still speaks | `.venv/bin/python -m deckle`. Import a PDF; on the Crop & trim tab click **Measure crop from the ink** and watch the status bar report the result; on the Signatures tab type nonsense into the gatherings box and watch `Gatherings: ...` appear. Both go through the renamed signal. |

Verified against the current tree: `grep -rn 'except ImportError' deckle/`
matches `backend.py:49` and `print_session.py:47`;
`grep -c 'SourceChangedWarning' deckle/cli.py` returns `3`;
`grep -rn 'schedule_saved' deckle/ tests/` returns 12 lines.

## 6. Out of scope

- **B4** (`log_print_job` called by both the backend and the session, so
  every chunk is logged twice, and the session's call is unguarded). Same
  two files, and B4 edits `print_session.py:559-564` and `backend.py:381`
  while item 1 edits `print_session.py:45-61` and `backend.py:47-63`. They do
  not overlap, but do not merge them: B4 changes behaviour and this changes
  none.
- **M6** edits `render_sheet` too — Part B, the face-index lookup at
  `render.py:229-233` and `250-260`. **Item 5 removes the `try`/`finally`
  wrapping that same block.** These two are the one genuine collision in this
  spec: land M6 first if both are in flight, then re-read `render_sheet`
  before dedenting. M6's own §6 says the same thing from the other side.
- **B2** defers `DRIVERS_IGNORING_ROTATE` to this spec
  (`B2-exporter-draws-the-half-turn.md:448`); the answer is §3 item 3: keep.
- **M1** defers `_document_loaded`/`_syncing_mode` to this spec
  (`M1-layout-panel-binding-table.md:1188-1191`); the answer is item 6.
- **F9** is what wires `submit_duplex`, together with the two open questions
  `docs/decisions.md` records (does a duplexer get a hand-measured back
  offset; Linux duplex detection). Not here.
- Do not touch `flatten_rotation`, `duplex_modes`, `DuplexModes`,
  `_apply_rotate_backs`, or `export.rotate_pages_180`.
- Do not delete `deckle/app/backend.py:282`'s comment.
- Do not remove `from typing import Sequence` from either file in item 1.

## 7. decisions.md entry

```
## 2026-09-05 — Five sentences the code was making that were not true, and two it was
- Symptom: Two `try/except ImportError` fallbacks defining a no-op `log_print_job` "used only until `deckle.core.session_log` exists" -- it has existed since SS-07 and has its own test file. An `except SourceChangedWarning` around `save_project`, which raises `OSError` and nothing else; only `load_project` raises that warning. A `try: ... finally: pass`. `_document_loaded` and `_syncing_mode` read with `getattr` defaults and never initialised. And a signal called `schedule_saved` carrying "Crop: ...", "Could not measure the ink: ..." and "Gatherings: ..." -- four of its seven emissions.
- Fix: The five deleted or renamed; the signal is now `status_message`, which is what its own comment already called it. The two attributes are set in `__init__` at the value their `getattr` defaults supplied, because both are genuinely read during that constructor -- `_sync_signature_tab()` runs before `set_document_loaded` ever does. `tests/test_scaffolding_is_gone.py` pins all five.
- Surfaces: **Two of the seven items in the roadmap row are not scaffolding and both stay.** `submit_duplex` is the whole of F9, is tested, and carries a trap a rewrite would lose -- the manual path passes the calibrated back offset and this one did not, so a measured registration would have been silently dropped on exactly the printers good enough to have a duplexer. `DRIVERS_IGNORING_ROTATE` is empty on purpose, with the reason written beside it and the capability it gates reached and tested (`test_apply_rotate_backs_flattens_for_drivers_that_ignore_rotate`); deleting it would remove a tested capability's only switch to save four lines. Both now have a test that says the emptiness is a decision.
- Watch: **The difference between the five and the two is an end condition.** `test_nothing_in_deckle_calls_this_yet` is a source-reading test this log defended in August precisely because it has exactly one legitimate failure -- someone wiring the path up -- and at that moment its failure *is* the message. The new emptiness test for the driver list is the same shape. A source-reading test without an end condition pins an artifact and was retired here on 2026-08-06; with one, it is the cheapest way to stop a sweep deleting something load-bearing. Also: the `try: ... finally: pass` deletion dedents the block containing a `pdfium.PdfDocument` open, and the guard test that checks every such open reads *relative indentation* -- so it had to be re-run rather than assumed.
- Commit: <fill in>
```

## 8. Traps

- **`submit_duplex` is not dead code and this spec is where that is written
  down.** A sweep that deletes it deletes F9 and the recorded offset trap
  with it. `tests/test_duplex_submission.py::test_nothing_in_deckle_calls_this_yet`
  is the guard; if you find yourself editing that test, stop.
- **The guard test strips comments before matching**, which is what lets
  `backend.py:282` mention `submit_duplex()` in prose. Reword that comment
  and you delete the sentence explaining why the method exists.
- **Item 5 dedents a block containing `pdfium.PdfDocument(`**, and
  `tests/test_render_concurrency.py:207-221` walks *upward at shallower
  indent* looking for the guard, stopping at the first `def`/`class`. A
  botched dedent makes that test fail with a confusing message about
  unguarded pdfium documents. Run it immediately.
- **M6 edits the same function.** See §6. One of the two specs will have to
  re-read `render_sheet` rather than apply a stored diff.
- **`bool(state.project.pages)` in item 6 is not a simplification.** It is
  the exact expression the `getattr` default used, and the panel is
  constructed both with and without pages. Do not substitute `False`.
- **Item 6's insertion must precede line 1182's `_sync_signature_tab()`**,
  which is inside `__init__` (the constructor runs 687-1205). Putting it at
  the end of `__init__` reintroduces the `AttributeError` the `getattr` was
  hiding.
- **The `SourceChangedWarning` import in `cli.py` stays.** Only the
  `_cmd_impose` catch goes; line 529's catch around `load_project` is
  correct and is covered by `tests/test_project_io.py`.
- **`Sequence` stays imported** in both files in item 1.
- **The signal rename touches four sibling specs' snippets.** See §3 item 7;
  grep after landing, do not assume.
- `python -m deckle` launches the GUI — the `[HUMAN]` row is the only place
  this spec wants it.
</content>
