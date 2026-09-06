# B30 — Re-query printers when the print dialog is wanted, and fix the docstring that promised it

**Roadmap item:** `docs/ROADMAP.md` B30 (and D10)
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none

---

## 1. Context

`MainWindow` enumerates printers exactly once, from `__init__`, and never
again. The list is then cached on `self._printers` for the life of the window.

The exact user action and the exact wrong result: launch Deckle before turning
the printer on (or before the network printer answers, or before the driver
finishes installing). Print is disabled with "No printers installed --
connect a printer to enable printing." Turn the printer on. Nothing changes.
There is no menu to open, no refresh button, no re-check on any action.
The only way to make Deckle notice a printer is to quit and relaunch — on the
one path where the user has already done the thing Deckle asked them to do.

The mirror case is the same shape: the printer that *was* there is gone. The
combo still lists it, `start_print` submits to it, and the failure surfaces as
`show_offline_error` after the user has committed.

The module docstring states this is already handled:

> ``available_printer_names`` is queried once at window construction (and
> whenever the printer menu is opened)

There is no printer menu, and never was. That is roadmap D10 — a
declared-but-not-honoured claim, the shape the decision log has caught five
times.

The machinery to fix it already exists and is already bounded: `refresh_printers`
runs `_PrinterQueryWorker` on a `QThread` under a `_PrinterQuery` deadline
(`PRINTER_QUERY_TIMEOUT_MS = 5000`), specifically so enumeration can be re-run
without reintroducing the 81-minute hang recorded in the 2026-08-04 decision
entry. Nothing calls it a second time.

## 2. Current code

`deckle/app/main.py:1-8` — the docstring making the false claim:

```python
"""Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

Zero printers installed is a defined, first-class state (red-team A-1):
``available_printer_names`` is queried once at window construction (and
whenever the printer menu is opened), and when it comes back empty the
print action is disabled with an explanatory status message instead of
opening an empty/broken print dialog or raising.
"""
```

`deckle/app/main.py:583` — the one and only call, at the end of `__init__`:

```python
        self._refresh_recent_menu()
        self._sync_document_actions()
        self._sync_history_actions()
        self._install_shortcuts()
        self.refresh_printers()
```

`deckle/app/main.py:755-768` — the Print button's handler, which explicitly
declines to re-query:

```python
    def _on_print_clicked(self) -> None:
        # Use the cached list rather than re-enumerating: a second query
        # would re-introduce exactly the block this moved off the UI thread.
        printers = self._printers
        if not printers:
            # Defensive: the button should already be disabled, but never
            # open a print dialog against zero printers even if this
            # slot is reached some other way.
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)
            return
        plan = self.preview_view.plan
        self.print_dialog = PrintDialog(plan, self.window, printer_names=printers)
        self.print_dialog.widget.exec()
        self.status_bar.showMessage(f"{len(printers)} printer(s) available.")
```

That comment was true when it was written — enumeration then ran
synchronously on the UI thread. It has been false since `refresh_printers`
moved to `_PrinterQueryWorker` + `_PrinterQuery`, which is the whole point of
those two classes.

`deckle/app/main.py:674-725` — `refresh_printers`, already re-entrant-safe in
shape and already bounded:

```python
    def refresh_printers(self, *, blocking: bool = False, timeout_ms: int | None = None) -> None:
        """Re-check available printers, off the UI thread and under a deadline.
        ...
        """
        if timeout_ms is None:
            timeout_ms = PRINTER_QUERY_TIMEOUT_MS

        if blocking:
            worker = _PrinterQueryWorker()
            worker.run()
            _PrinterQuery(worker, self._apply_printers, timeout_ms).complete()
            return

        self.print_button.setEnabled(False)
        self.status_bar.showMessage("Checking for printers...")

        worker = _PrinterQueryWorker()
        query = _PrinterQuery(worker, self._apply_printers, timeout_ms)
        thread = _new_thread(self.window)
        thread.run = worker.run
        thread.finished.connect(query.complete)
        thread.finished.connect(thread.deleteLater)
        self._printer_query = query
        self._printer_thread = thread
        thread.start()
        _single_shot(timeout_ms, query.time_out)
```

Note lines 713-714: the async path **disables Print and overwrites the status
bar** with "Checking for printers...". That matters for step 3 below — calling
it from the Print button as-is would disable the button the user just clicked
and blank whatever the status bar was saying.

`deckle/app/main.py:727-753` — `_apply_printers`, the single place that
enables or disables Print:

```python
    def _apply_printers(self, printers: list[str], no_printers_message: str | None = None) -> None:
        """Enable or disable **only** the Print action.
        ...
        """
        self._printers = list(printers)
        has_printers = bool(printers)
        self.print_button.setEnabled(has_printers)
```

`deckle/app/main.py:158-201` — `_PrinterQuery.complete` / `.time_out`, which
settle **once** and log a late answer rather than applying it:

```python
    def complete(self) -> None:
        if self.settled:
            log_event(
                "printer_enumeration_late_result",
                count=len(self._worker.names),
                timeout_ms=self.timeout_ms,
            )
            return
```

Each `refresh_printers` call builds its **own** `_PrinterQuery`, so a second
refresh while the first is in flight is safe: they settle independently and
both call `_apply_printers`. The later one wins, which is the correct
ordering.

`deckle/app/views/print_dialog.py:114-117, 209` — the dialog can enumerate for
itself, and `MainWindow` deliberately passes its cached list instead:

```python
def _available_printer_names() -> list[str]:
    from PySide6.QtPrintSupport import QPrinterInfo

    return [info.printerName() for info in QPrinterInfo.availablePrinters()]
```

```python
        names = list(printer_names) if printer_names is not None else _available_printer_names()
```

That fallback is unbounded and on the UI thread; it must stay unused by
`MainWindow`. See §8.

### Every call site

`grep -rn "refresh_printers\|_printers\b\|available_printer_names" --include="*.py" deckle/ tests/`:

| Site | Kind |
|---|---|
| `deckle/app/main.py:114` | `_PrinterQueryWorker.run` calls `available_printer_names()` |
| `deckle/app/main.py:272` | `available_printer_names` definition |
| `deckle/app/main.py:568` | `self._printers: list[str] = []` initialised |
| `deckle/app/main.py:583` | the only `refresh_printers()` call |
| `deckle/app/main.py:674` | `refresh_printers` definition |
| `deckle/app/main.py:734` | `self._printers = list(printers)` in `_apply_printers` |
| `deckle/app/main.py:758` | `printers = self._printers` in `_on_print_clicked` |
| `tests/gui_workflow.py:50-53` | monkeypatches both to no-ops |
| `tests/test_shutdown.py:37-40` (inside `PROBE`) | same monkeypatch |
| `tests/test_hardening_printing.py:227` | `MainWindow.refresh_printers(window, timeout_ms=1234)` unbound, against `_FakeWindow` |
| `tests/test_hardening_printing.py:393` | `MainWindow.refresh_printers(window, blocking=True)` |
| `tests/test_view_workers.py:188, 197, 215` | `_PrinterQueryWorker` and "not called during import" |

### Existing tests that touch this code

- `tests/test_hardening_printing.py::test_refresh_printers_arms_a_deadline_against_the_worker`
  (line 217) — asserts the thread starts and a `_single_shot` deadline is
  armed. The pattern every new test here follows.
- `tests/test_hardening_printing.py` lines 172-243 — the `_PrinterQuery`
  arbitration tests.
- `tests/test_hardening_printing.py` lines 300-345 — `_apply_printers` against
  `_FakeWindow`.
- `tests/test_view_workers.py::test_printer_enumeration_is_not_called_during_import`
  (line 215) — asserts an import does not enumerate. A re-query wired to the
  wrong signal would break this, which is exactly what it is for.
- `tests/gui_workflow.py` and `tests/test_shutdown.py` both replace
  `MainWindow.refresh_printers` with
  `lambda self, blocking=False, timeout_ms=None: self._apply_printers([])`.
  **Any new keyword argument must have a default**, or both harnesses break
  with `TypeError`.

## 3. Change

Re-query at the two moments a user could be waiting on the answer: when the
window is shown, and when Print is clicked. Keep the cached list as what the
dialog is opened with, so clicking Print never blocks on the spooler.

Two designs were possible. **Chosen:** re-query on show and on click, with the
click opening the dialog against the *current* cache immediately and the
refresh landing afterwards. **Rejected:** making the Print click await the
query — that is the 81-minute hang again, five seconds at a time, on the
button the user is standing at the printer to press.

1. **`main.py:1-8`, module docstring** — replace the false clause. This is D10.

   ```python
   """Deckle's main window: wires ``AppState`` to ``ImportView``/``ArrangeView``.

   Zero printers installed is a defined, first-class state (red-team A-1):
   ``available_printer_names`` is queried at window construction, again
   whenever the window is shown, and again when Print is clicked -- always
   on a background thread under a deadline (:class:`_PrinterQuery`), never
   on the UI thread. When it comes back empty the print action is disabled
   with an explanatory status message instead of opening an empty/broken
   print dialog or raising.

   Re-querying matters because the commonest first-run sequence is "launch
   Deckle, then turn the printer on": enumerating once at construction left
   Print disabled with no way back short of relaunching.
   """
   ```

2. **`main.py`, `refresh_printers`** — add a `quiet` keyword so a refresh can
   run without commandeering the UI. Default `False`, so every existing
   caller and both test harnesses are unaffected:

   ```python
    def refresh_printers(
        self,
        *,
        blocking: bool = False,
        timeout_ms: int | None = None,
        quiet: bool = False,
    ) -> None:
   ```

   and in the async path, guard the two UI lines at 713-714:

   ```python
        if not quiet:
            # A refresh the user asked for says so. A background one must
            # not disable the button they are mid-click on, nor blank a
            # status message they are reading.
            self.print_button.setEnabled(False)
            self.status_bar.showMessage("Checking for printers...")
   ```

   Add to the docstring's parameter block:

   ```
        :param quiet: do not disable Print or show "Checking for
            printers..." while the query runs. For a refresh the user did
            not ask for -- on show, and alongside a Print click -- where
            greying out the control they are using would be worse than a
            list that is a few hundred milliseconds stale.
   ```

3. **`main.py:1112-1117`, `show`** — refresh on every show, quietly:

   ```python
    def show(self) -> None:
        """Show the window, and re-check for printers.

        :returns: nothing.

        The commonest first-run sequence is "launch Deckle, then turn the
        printer on". Enumerating only at construction left Print disabled
        with no way back short of relaunching, and the module docstring
        claimed a refresh on a menu that does not exist.
        """
        self.window.show()
        self.refresh_printers(quiet=True)
   ```

   `show()` is called once from `main()` (`main.py:1198`) today; making it a
   refresh point costs one bounded background query per show.

4. **`main.py:755-768`, `_on_print_clicked`** — re-query, then open against
   the cache. Replace the stale comment:

   ```python
    def _on_print_clicked(self) -> None:
        """Open the print dialog, re-checking the printer list first.

        The dialog is opened against the CACHED list, not the query's
        result: a printer that appeared in the last few seconds is worth
        catching, but waiting on the spooler to open a dialog is the
        81-minute hang again, five seconds at a time, on the button the
        user is standing at the printer to press. The refresh lands while
        the dialog is up and its answer is there for the next click.

        :returns: nothing.
        """
        # Bounded and off the UI thread since `_PrinterQuery` -- the old
        # comment here said re-enumerating would reintroduce the block it
        # moved off the UI thread, and that stopped being true when the
        # deadline was added.
        self.refresh_printers(quiet=True)

        printers = self._printers
        if not printers:
            # Defensive: the button should already be disabled, but never
            # open a print dialog against zero printers even if this
            # slot is reached some other way.
            self.status_bar.showMessage(NO_PRINTERS_MESSAGE)
            return
        plan = self.preview_view.plan
        self.print_dialog = PrintDialog(plan, self.window, printer_names=printers)
        self.print_dialog.widget.exec()
        self.status_bar.showMessage(f"{len(printers)} printer(s) available.")
   ```

   `PrintDialog(..., printer_names=printers)` stays: passing `None` would make
   the dialog call its own unbounded `_available_printer_names()` on the UI
   thread.

5. **`main.py`, `stop_background_work`** — no change. The printer thread is
   parented to `self.window` (line 718) and gets `deleteLater` (line 721), and
   `_PrinterQuery` discards a late answer by design (line 165-171). A
   printer query outliving the window is already handled; do not add it to the
   view loop.

6. **`docs/api/`** — no new module. No change.

### Signature

```python
def refresh_printers(
    self,
    *,
    blocking: bool = False,
    timeout_ms: int | None = None,
    quiet: bool = False,
) -> None
```

Keyword-only and defaulted, so `tests/gui_workflow.py:51-53` and
`tests/test_shutdown.py:38-40` — which replace the method with
`lambda self, blocking=False, timeout_ms=None: ...` — keep working when called
as `self.refresh_printers()`. They do **not** keep working if called as
`self.refresh_printers(quiet=True)`. Both harnesses must gain `quiet=False`;
see §4.

## 4. Tests

A real `QMainWindow` cannot be constructed under pytest here — offscreen Qt
plus `QMainWindow` exits 127, recorded at `tests/test_ui_surface.py:537-541`
and `tests/test_hardening_printing.py:18-21`. So:

- behaviour is tested with **unbound methods against `_FakeWindow`**, the
  established pattern at `tests/test_hardening_printing.py:271-297`;
- the docstring claim is tested **structurally**, the pattern at
  `tests/test_ui_surface.py:544-548`.

All of these run with `QT_QPA_PLATFORM=offscreen`;
`tests/test_hardening_printing.py` needs no display for any of them.

`_FakeWindow` needs two additions to drive the new paths. Extend it in place
at `tests/test_hardening_printing.py:279-294`:

```python
    def __init__(self, pages=()):
        self.print_button = _FakeButton()
        self.save_pdf_button = _FakeButton()
        self.status_bar = _FakeStatusBar()
        self.window = None
        self._printers = []
        self._printer_thread = None
        self._printer_query = None
        self.state = SimpleNamespace(project=SimpleNamespace(pages=list(pages)))
        self._printer_message = ""
        #: Records `refresh_printers` calls made by other MainWindow
        #: methods, so `show` and `_on_print_clicked` can be driven without
        #: a real enumeration.
        self.refreshed: list[dict] = []

    def refresh_printers(self, **kwargs):
        self.refreshed.append(kwargs)
```

Note this shadows the real method on the double, which is what lets
`_on_print_clicked` and `show` be exercised without touching a spooler.

### `test_a_quiet_refresh_does_not_disable_print_or_take_the_status_bar`

```python
def test_a_quiet_refresh_does_not_disable_print_or_take_the_status_bar(monkeypatch):
    """A refresh the user did not ask for must not grey out the control
    they are using, nor blank a message they are reading."""
    monkeypatch.setattr(app_main, "_single_shot", lambda ms, cb: None)
    monkeypatch.setattr(app_main, "_new_thread", lambda parent: _FakeThread())

    window = _FakeWindow(pages=["one page"])
    window.print_button.enabled = True
    window.status_bar.message = "Saved 12 sheet(s) to /tmp/book.pdf"

    app_main.MainWindow.refresh_printers(window, quiet=True)

    assert window.print_button.enabled is True
    assert window.status_bar.message == "Saved 12 sheet(s) to /tmp/book.pdf"
    assert window._printer_thread.started is True
```

Assertion in words: a quiet refresh still starts the enumeration thread, but
leaves the Print button and the status bar exactly as it found them.
Expected failure on the unfixed tree: `TypeError: refresh_printers() got an
unexpected keyword argument 'quiet'`.

### `test_a_loud_refresh_still_says_it_is_checking`

```python
def test_a_loud_refresh_still_says_it_is_checking(monkeypatch):
    """The construction-time refresh is the one the user IS waiting on."""
    monkeypatch.setattr(app_main, "_single_shot", lambda ms, cb: None)
    monkeypatch.setattr(app_main, "_new_thread", lambda parent: _FakeThread())

    window = _FakeWindow(pages=["one page"])
    app_main.MainWindow.refresh_printers(window)

    assert window.print_button.enabled is False
    assert window.status_bar.message == "Checking for printers..."
```

On the unfixed tree this **passes**; it is the guard that stops `quiet` from
becoming the default. Keep it.

### `test_showing_the_window_rechecks_for_printers`

```python
def test_showing_the_window_rechecks_for_printers():
    """Launch Deckle, then turn the printer on -- the commonest first-run
    sequence, and one that used to require relaunching."""
    window = _FakeWindow()
    window.window = SimpleNamespace(show=lambda: window.__dict__.setdefault("shown", True))

    app_main.MainWindow.show(window)

    assert window.shown is True
    assert window.refreshed == [{"quiet": True}]
```

Assertion in words: showing the window shows it and issues exactly one quiet
refresh.
Expected failure on the unfixed tree: `AssertionError: assert [] == [{'quiet': True}]`.

### `test_clicking_print_rechecks_for_printers_first`

```python
def test_clicking_print_rechecks_for_printers_first():
    """A printer plugged in since launch must be reachable without a
    relaunch, and one that has gone must stop being offered."""
    window = _FakeWindow(pages=["one page"])
    window._printers = []

    app_main.MainWindow._on_print_clicked(window)

    assert window.refreshed == [{"quiet": True}], "Print did not re-query"
    # With no printers it still refuses rather than opening an empty dialog.
    assert window.status_bar.message == app_main.NO_PRINTERS_MESSAGE
```

Assertion in words: the click issues one quiet refresh, and the zero-printer
guard is untouched.
Expected failure on the unfixed tree:
`AssertionError: Print did not re-query` — `window.refreshed` is `[]`.

### `test_clicking_print_opens_the_dialog_against_the_cached_list`

```python
def test_clicking_print_opens_the_dialog_against_the_cached_list(monkeypatch):
    """Not against the query's result: waiting on the spooler to open a
    dialog is the 81-minute hang again, five seconds at a time."""
    opened = []

    class _FakeDialog:
        def __init__(self, plan, parent, printer_names=None):
            opened.append(list(printer_names) if printer_names is not None else None)
            self.widget = SimpleNamespace(exec=lambda: 0)

    monkeypatch.setattr(app_main, "PrintDialog", _FakeDialog)

    window = _FakeWindow(pages=["one page"])
    window._printers = ["Brother HL-2270DW"]
    window.preview_view = SimpleNamespace(plan=object())

    app_main.MainWindow._on_print_clicked(window)

    assert opened == [["Brother HL-2270DW"]], (
        "the dialog was not given the cached list, so it enumerates on the "
        "UI thread itself"
    )
```

On the unfixed tree this **passes** (the cache is already what is passed); it
is the guard that stops the fix from becoming "await the query". Keep it.

### `test_the_module_docstring_does_not_promise_a_printer_menu`

```python
def test_the_module_docstring_does_not_promise_a_printer_menu():
    """D10: the docstring claimed a refresh 'whenever the printer menu is
    opened'. There is no printer menu, and never was -- a
    declared-but-not-honoured claim, the shape the decision log has caught
    five times."""
    import deckle.app.main as app_main

    doc = app_main.__doc__ or ""
    assert "printer menu" not in doc
    assert "when Print is clicked" in doc
```

Expected failure on the unfixed tree:
`AssertionError: assert 'printer menu' not in "Deckle's main window..."`.

### Harness updates, required

Both subprocess harnesses replace `refresh_printers` with a two-parameter
lambda. `show()` and `_on_print_clicked` now call it with `quiet=True`, which
would raise `TypeError` in both. Update:

- `tests/gui_workflow.py:51-53`:

  ```python
  app_main.MainWindow.refresh_printers = (
      lambda self, blocking=False, timeout_ms=None, quiet=False: self._apply_printers([])
  )
  ```

- `tests/test_shutdown.py:38-40`, inside `PROBE`, the same edit.

Neither harness calls `window.show()` today (`gui_workflow.py` constructs and
drives; the probe constructs and closes), but `_on_print_clicked` is reachable
and the signature must match regardless.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_printing.py -k "quiet_refresh or loud_refresh or showing_the_window or clicking_print or printer_menu"` |
| The whole printing-hardening file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_printing.py` |
| The subprocess harnesses still run | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_gui_workflow.py tests/test_shutdown.py` |
| Enumeration is still not triggered by an import | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_view_workers.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| The false docstring claim is gone | `! grep -n "printer menu is opened" deckle/app/main.py` — currently hits line 5 |
| The stale "would re-introduce the block" comment is gone | `! grep -n "would re-introduce exactly the block" deckle/app/main.py` — currently hits line 757 |
| There are now three refresh points | `test "$(grep -c 'self.refresh_printers(' deckle/app/main.py)" -eq 3` — currently 1 |
| Enumeration is never called on the UI thread from `MainWindow` | `! grep -n "available_printer_names()" deckle/app/main.py \| grep -v "_PrinterQueryWorker\|def available_printer_names"` — the only call must stay inside `_PrinterQueryWorker.run` (line 114) |
| Deckle never lets the dialog enumerate for itself | `grep -q "PrintDialog(plan, self.window, printer_names=printers)" deckle/app/main.py` |
| [HUMAN] The printer turned on after launch | Launch Deckle with the printer off. Confirm Print is disabled and the status bar explains why. Turn the printer on, wait for the OS to see it, click nothing — then close and reopen the window, or click Print. Print must become enabled within one query deadline (5s) without relaunching Deckle. |

## 6. Out of scope

- **N7 — menu bar and keyboard shortcuts.** A real printer *menu* (which
  would give the docstring's original claim something to be true about) is
  N7's. Do not add a menu bar here; the fix is to refresh at the two moments
  that already exist and to correct the docstring.
- **B15 / N2 — the preview's imageable-area guide** and reading the driver's
  printable rect. Do not push anything to `PreviewView` here.
- **B16 — the profile combo.** Different control, same dialog. See
  `B16-print-dialog-profile-combo.md`.
- **B17 — print submission running synchronously on the GUI thread.** This
  spec moves nothing new onto or off the UI thread; `refresh_printers` is
  already threaded.
- Do not add a visible "Refresh printers" button. The two refresh points are
  moments the user is already at; a button is a control for a problem the
  refresh removes.
- Do not add polling or a periodic timer. Two event-driven refresh points, not
  a heartbeat — a query every N seconds is the unbounded spooler call back on
  a schedule.

## 7. decisions.md entry

```
## 2026-09-05 — Printers were enumerated once at launch, and the docstring claimed otherwise
- Symptom: `MainWindow.refresh_printers` was called exactly once, from `__init__`. Launch Deckle before turning the printer on — the commonest first-run sequence — and Print stays disabled with no way back short of relaunching, on the one path where the user has already done what Deckle asked. The mirror case is a printer that has since gone still being offered and failing after submission. The module docstring said enumeration happened at construction "and whenever the printer menu is opened"; there is no printer menu and never was (D10).
- Fix: `refresh_printers` gains a keyword-only `quiet=False`, which skips disabling Print and taking the status bar. `show()` and `_on_print_clicked` each issue one quiet refresh. The dialog is still opened against the CACHED list, deliberately — awaiting the query to open a dialog is the 81-minute hang again, five seconds at a time. The docstring now names the three refresh points and says they are threaded and bounded.
- Surfaces: `_on_print_clicked` carried a comment saying a second query "would re-introduce exactly the block this moved off the UI thread". That was true when written and stopped being true the moment `_PrinterQuery` added a deadline; the comment outlived its reason and became the argument against fixing this.
- Watch: A comment explaining why something is NOT done is a claim with an expiry date. When the constraint it names is removed, the comment is the thing left defending the old behaviour.
- Commit: <fill in>
```

## 8. Traps

- **`refresh_printers`'s async path disables Print and overwrites the status
  bar** (lines 713-714). Calling it unguarded from `_on_print_clicked` would
  grey out the button mid-click and blank whatever the bar was saying. That is
  the entire reason for `quiet`.
- **Both subprocess harnesses replace `refresh_printers` with a lambda**
  (`tests/gui_workflow.py:51-53`, `tests/test_shutdown.py:38-40`). Adding a
  keyword the lambda does not accept breaks both with `TypeError` inside a
  subprocess, which surfaces as `exit 1` and a stderr traceback rather than a
  named assertion. Update both.
- **`PrintDialog(plan, parent, printer_names=None)` enumerates for itself**,
  unbounded, on the UI thread (`print_dialog.py:117, 209`). Never pass `None`
  from `MainWindow`.
- **`_PrinterQuery` settles once and logs a late answer** rather than applying
  it (`main.py:165-171`). Two overlapping refreshes are therefore safe: each
  has its own query object, both call `_apply_printers`, and the later one
  wins. Do not add a "cancel the previous query" guard — the abandoned thread
  is deliberately left to finish, because killing a thread parked in a driver
  call is worse than leaking one (`main.py:136-140`).
- **`self._printer_thread` is overwritten by a second refresh.** The previous
  thread is parented to `self.window` and has `deleteLater` connected, so it
  is freed on finish; this is the same shape `_live_threads` documents for the
  view workers. No leak, but do not assume `_printer_thread` is the only live
  one.
- **A real `QMainWindow` cannot be constructed under pytest here** — exit 127.
  Use unbound methods against `_FakeWindow`, or the subprocess harness. Do not
  add `MainWindow()` to `tests/test_hardening_printing.py`.
- **`python -m deckle` launches the GUI and blocks.** The `[HUMAN]` row is the
  only place a real window appears; run it by hand.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`, and refuses added lines containing `<FILL-IN>`.
</content>
