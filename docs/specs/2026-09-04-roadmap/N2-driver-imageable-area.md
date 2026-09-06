# N2 — Read the imageable area off the driver instead of guessing 0.25in

**Roadmap item:** `docs/ROADMAP.md` N2
**Depends on:** —. **Pairs with B15** (which plumbs a resolved profile from the printer query to `LayoutPanel` and `PreviewView`); N2 supplies the *number* B15 plumbs. Land N2 first so B15 has something true to push, or land them together. Touches `deckle/app/main.py`, so it also collides with **M3** (which moves `_PrinterQueryWorker` into `app/printer_query.py`) — if M3 is scheduled, land N2 first and let M3 move the finished worker.
**Blocks:** B15 (properly), F1 (a profile editor wants a pre-filled imageable area)
**Size:** M
**Decision needed first:** none

---

## 1. Context

The preview's solid red rectangle is documented — in `preview_view.py:14-20`
and in GUIDE §3 — as "the printer's imageable area … a hardware limit". It is
not. It is `(18.0, 18.0, 18.0, 18.0)` points, hard-coded in the first built-in
preset, and every window in Deckle shows the same rectangle regardless of which
printer is attached or whether one is attached at all. "Use printer margins"
sets 0.25in for the same reason.

A user with a printer whose bottom margin is 0.55in (common on inkjets, where
the trailing edge is the worst one) sets margins to what the red line promises,
prints, and finds the tail of every page clipped. The preview showed no
warning, because `clipping_warnings_for_sheet` was measuring against the same
fiction.

The number is available. The SS-08 spike measured `pageLayout()` on both
platforms and marked "Imageable area query" **confirmed** on Windows and Linux.
Verified again on this tree, offscreen, with no printer installed:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from PySide6.QtWidgets import QApplication
app = QApplication([])
from PySide6.QtPrintSupport import QPrinter
p = QPrinter(QPrinter.PrinterMode.HighResolution)
lay = p.pageLayout()
print("mode        ", lay.mode())
print("resolution  ", p.resolution())
print("fullRectPx  ", lay.fullRectPixels(p.resolution()))
print("paintRectPx ", lay.paintRectPixels(p.resolution()))
p.setFullPage(True)
print("fullpage paintRectPoints", p.pageLayout().paintRectPoints())
EOF
```

produces

```
mode         Mode.StandardMode
resolution   1200
fullRectPx   PySide6.QtCore.QRect(0, 0, 9916, 14033)
paintRectPx  PySide6.QtCore.QRect(167, 167, 9582, 13699)
fullpage paintRectPoints PySide6.QtCore.QRect(0, 0, 595, 842)
```

**Read the last line before writing any code.** The spike's confirmed row says
`paintRectPixels()` under `setFullPage(True)` returns *the full physical
sheet* — that is what it was checked for, because `QtPrintBackend` needs Qt not
to reapply a margin of its own. Under full page there is no inset to read. The
margins only exist in `StandardMode`, which is the default and which
`QtPrintBackend._submit_chunk` explicitly leaves behind
(`backend.py:490`). So the roadmap's "confirmed by the spike" is confirmed for
the *call*, not for the *mode*: N2 must query a `QPrinter` it has not put into
full-page mode, and must not disturb the one the backend paints on.

## 2. Current code

`deckle/app/main.py:76-78` — the fiction, seeded into every view:

```python
# Used to seed PreviewView before any printer/profile has been chosen -- the
# same fallback resolve_profile() reaches for when a printer has no saved
# PrinterProfile yet (see deckle/app/views/print_dialog.py).
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))
```

`deckle/core/profiles.py:181-192` — what that resolves to:

```python
BUILTIN_PRESETS: dict[str, PrinterProfile] = {
    "generic_face_down_reversed": PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
```

`deckle/app/main.py:442` and `main.py:521-526` — the two places it is handed
out, and never replaced afterwards:

```python
        self.layout_panel = LayoutPanel(self.state, controls, profile=DEFAULT_PROFILE)
```

```python
        self.preview_view = PreviewView(
            recompute_plan(self.state.project),
            DEFAULT_PROFILE,
            output,
            layout_settings=self.state.project.layout,
        )
```

`deckle/app/main.py:91-121` — the worker that already runs off-thread, and
which currently returns nothing but names:

```python
class _PrinterQueryWorker:
    def __init__(self) -> None:
        self.names: list[str] = []

    def run(self) -> None:
        try:
            self.names = available_printer_names()
        except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
            log_exception("printer_enumeration_failed", exc)
            self.names = []
```

`deckle/app/main.py:158-181` — `_PrinterQuery.complete`, which calls
`self._apply(names)`; `main.py:727-753` — `_apply_printers(printers,
no_printers_message=None)`, which stores `self._printers` and touches only the
Print button.

`deckle/app/views/print_dialog.py:59-83` — `resolve_profile(printer_name,
profile_loader, builtin_presets)`, the only path from a printer name to a
profile. It returns a saved profile if there is one, else
`next(iter(presets.values()))` — the same 0.25in preset.

`deckle/app/backend.py:515-529` — `_paint_rendered_page`, which reads
`self.profile.imageable_area_pt` as `(left, top, right, bottom)` margins.

`deckle/app/views/preview_view.py:44-64` — `imageable_rect_pt`, the other
consumer, with the same `(left, top, right, bottom)` convention spelled out.

`deckle/app/views/layout_panel.py:165-183` — `imageable_inset_pt`, behind "Use
printer margins", which takes the largest of the four.

**Everything that reads `imageable_area_pt` (grep, 51 hits across
`deckle` + `tests`; the ones that matter):** `profiles.py:49` (the field),
`profiles.py:188,198` (the two presets), `backend.py:519`,
`preview_view.py:63,98,117,172,877`, `layout_panel.py:165-183,1449`,
`cli.py` — none, and `tests/test_preview_paint.py`,
`tests/test_print_painting.py`, `tests/test_ui_surface.py`,
`tests/test_registration.py`, `tests/test_hardening_printing.py`.

**Existing tests:** `tests/test_hardening_printing.py:220-400` drives
`refresh_printers` / `_apply_printers` through a `_FakeWindow` stub;
`tests/test_print_dialog.py` covers `resolve_profile`;
`tests/test_preview_paint.py` covers `imageable_rect_pt`.

## 3. Change

### The new pure function

New module `deckle/app/printer_capabilities.py` — `deckle/app`, not
`deckle/core`, because it touches Qt. Needs a new page at
`docs/api/app.printer_capabilities.rst` and an entry in `docs/api/app.rst`'s
toctree (`tests/test_docs_coverage.py` enforces this).

```python
def imageable_area_from_layout(
    full_rect: tuple[int, int, int, int],
    paint_rect: tuple[int, int, int, int],
    resolution_dpi: int,
) -> tuple[float, float, float, float] | None:
    """The driver's non-printable margins, as ``(left, top, right, bottom)``
    in points.

    Both rects are ``(x, y, width, height)`` in device pixels at
    ``resolution_dpi``, exactly as ``QPageLayout.fullRectPixels`` and
    ``paintRectPixels`` return them, with ``paint_rect``'s origin measured
    from ``full_rect``'s.
    """
```

Arithmetic, exactly:

```python
    if resolution_dpi <= 0:
        return None
    fx, fy, fw, fh = full_rect
    px, py, pw, ph = paint_rect
    to_pt = 72.0 / resolution_dpi
    margins = (
        (px - fx) * to_pt,
        (py - fy) * to_pt,
        (fx + fw - px - pw) * to_pt,
        (fy + fh - py - ph) * to_pt,
    )
    if any(m < 0 for m in margins):
        return None           # a paint rect outside its own page: unusable
    if all(m == 0.0 for m in margins):
        return None           # full-page mode, or a driver that reports none
    return margins
```

`None` for the all-zero case is the load-bearing decision: it is exactly what a
full-page `QPrinter` and a driver that declines to answer both produce, and
"this printer can print to the paper edge" is a claim no consumer printer can
honour. `None` means "unknown", and every caller falls back to the preset.
The rejected alternative — returning `(0,0,0,0)` and letting the preview draw
the red line on the paper edge — would replace a wrong number with a *more*
confident wrong number.

### The Qt query

Same module:

```python
def query_imageable_area_pt(printer_name: str) -> tuple[float, float, float, float] | None:
    """Ask the driver for ``printer_name``'s non-printable margins.

    Queried in ``StandardMode``, deliberately. ``QtPrintBackend`` calls
    ``setFullPage(True)`` so Qt applies no margin of its own to the
    painting -- and under full page ``paintRect`` *is* ``fullRect``, so the
    margins are only visible on a printer object that has not been put into
    that mode. This constructs its own ``QPrinter`` and never touches the
    one the backend paints on.

    :returns: ``(left, top, right, bottom)`` in points, or ``None`` when the
        driver reports nothing usable -- which is the normal answer for a
        virtual printer and for a queue Qt does not recognise.
    """
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

    info = QPrinterInfo.printerInfo(printer_name)
    if info.isNull():
        return None
    printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    layout = printer.pageLayout()
    resolution = printer.resolution()
    full = layout.fullRectPixels(resolution)
    paint = layout.paintRectPixels(resolution)
    return imageable_area_from_layout(
        (full.x(), full.y(), full.width(), full.height()),
        (paint.x(), paint.y(), paint.width(), paint.height()),
        resolution,
    )
```

and one profile-shaping helper, pure:

```python
def profile_with_driver_margins(
    profile: PrinterProfile,
    imageable_area_pt: tuple[float, float, float, float] | None,
) -> PrinterProfile:
    """``profile`` with the driver's margins substituted in, if there are any.

    Only ``imageable_area_pt`` is replaced. Every other field -- the flip
    axis, the reload behaviour, the measured back offset -- describes what
    the *operator* does with the paper and is not something a driver knows.

    **Never saved.** A ``PrinterProfile`` on disk is a calibration: it came
    from printing a target and measuring it by hand. A number read off a
    driver has not been checked against paper, and writing it into
    ``config_dir`` would make an uncalibrated printer indistinguishable
    from a calibrated one for every later reader.
    """
    if imageable_area_pt is None:
        return profile
    return dataclasses.replace(profile, imageable_area_pt=imageable_area_pt)
```

### Steps

1. **New file `deckle/app/printer_capabilities.py`** with the three functions
   above. PySide6 imported lazily inside `query_imageable_area_pt` only, so the
   module (and its two pure functions) stay importable headlessly — the same
   discipline `docs/api/app.rst` describes for every other module in the
   package.

2. **`docs/api/app.printer_capabilities.rst`** (copy the shape of
   `docs/api/core.recent.rst`), and add `app.printer_capabilities` to the
   toctree in `docs/api/app.rst` after `app.backend`.

3. **`deckle/app/main.py` — carry the answers on `_PrinterQueryWorker`.** Add
   to `__init__`:

   ```python
           self.imageable_areas: dict[str, tuple[float, float, float, float]] = {}
   ```

   and, in `run`, after `self.names = available_printer_names()`:

   ```python
           for name in self.names:
               try:
                   area = query_imageable_area_pt(name)
               except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
                   log_exception("printer_imageable_query_failed", exc, printer=name)
                   continue
               if area is not None:
                   self.imageable_areas[name] = area
   ```

   Inside the existing worker, on the existing background thread: this is
   another spooler call and belongs on the same side of the deadline as
   enumeration. A per-printer `try` rather than one around the loop, so one
   unreachable network queue does not cost the margins of every other printer.
   Update the class docstring's `:ivar:` block.

4. **`deckle/app/main.py` — carry them through the query.** `_PrinterQuery`
   already holds the worker (`main.py:151-156`). In `complete`, pass them on:

   ```python
           self._apply(names, None, dict(self._worker.imageable_areas))
   ```

   and in `time_out`:

   ```python
           self._apply([], PRINTER_TIMEOUT_MESSAGE, {})
   ```

   `_apply_printers` gains a third parameter with a default so the stubs in
   `tests/test_hardening_printing.py` keep working:

   ```python
       def _apply_printers(
           self,
           printers: list[str],
           no_printers_message: str | None = None,
           imageable_areas: dict[str, tuple[float, float, float, float]] | None = None,
       ) -> None:
   ```

   Its first line becomes `self._imageable_areas = dict(imageable_areas or {})`,
   and `self._imageable_areas: dict = {}` is initialised beside
   `self._printers: list[str] = []` at `main.py:568`.

5. **`deckle/app/main.py` — resolve a profile for the preselected printer.**
   At the end of `_apply_printers`, after `self._refresh_status_message()`:

   ```python
           self._apply_resolved_profile()
   ```

   ```python
       def _apply_resolved_profile(self) -> None:
           """Show the selected printer's real geometry, not the preset's.

           The red imageable-area guide and "Use printer margins" both read a
           profile, and until now that profile was always the first built-in
           preset -- a fixed 0.25in on every edge, on every machine. The
           GUIDE calls the red line the printer's hardware limit; this is
           what makes that true.
           """
           name = select_preselected_printer(self._printers, PrinterProfile.load)
           if name is None:
               return
           profile = profile_with_driver_margins(
               resolve_profile(name), self._imageable_areas.get(name)
           )
           self.set_printer_profile(profile)
   ```

   `select_preselected_printer` and `resolve_profile` are imported from
   `deckle.app.views.print_dialog` (they are already the pure, Qt-free half of
   that module).

6. **`deckle/app/main.py` — one setter, so there is one writer.**

   ```python
       def set_printer_profile(self, profile: PrinterProfile) -> None:
           """Push a resolved profile to the panel and the preview.

           :returns: nothing. Both views re-read it immediately, so the red
               guide and the clipping warnings change together rather than
               one refresh apart.
           """
           self.profile = profile
           self.layout_panel.profile = profile
           self.preview_view.profile = profile
           self.preview_view.refresh()
   ```

   `self.profile = DEFAULT_PROFILE` is initialised in `__init__` beside
   `self._printers`. **This method is B15's seam** — B15 adds nothing to
   `main.py` beyond calling it from the print dialog's printer combo. If B15
   lands first, reuse whatever it named rather than adding a second setter.

7. **`deckle/app/views/print_dialog.py` — the dialog uses it too.** In
   `start_print`, replace `profile = self._resolve_profile(printer_name)` with:

   ```python
           profile = profile_with_driver_margins(
               self._resolve_profile(printer_name),
               query_imageable_area_pt(printer_name),
           )
   ```

   Two calls (also in `_offer_resume`, line 289) — factor them into
   `PrintDialog._resolve_profile` itself, which both already go through. Add an
   injectable `imageable_area_query: Callable[[str], tuple | None] | None = None`
   constructor parameter defaulting to `query_imageable_area_pt`, matching how
   every other dependency in that class is injected for headless tests.

   This is the half that reaches paper: `QtPrintBackend._paint_rendered_page`
   scales the sheet into `profile.imageable_area_pt`, so a wrong number here has
   always been a wrong *print*, not just a wrong picture.

8. **`deckle/app/views/layout_panel.py` — nothing.** `imageable_inset_pt` and
   `_on_use_printer_margins` already read `self.profile`; step 6 replaces the
   object they read.

## 4. Tests

### `tests/test_printer_capabilities.py` (new, all headless)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_margins_are_the_gap_between_the_two_rects` | `full=(0,0,9916,14033)`, `paint=(167,167,9582,13699)`, `resolution=1200` | result is `(10.02, 10.02, 10.02, 10.02)` to 2dp — i.e. `167 * 72/1200` on every edge | `ModuleNotFoundError: No module named 'deckle.app.printer_capabilities'` |
| `test_asymmetric_margins_are_reported_per_edge` | `full=(0,0,1200,1800)`, `paint=(12,24,1140,1716)`, `resolution=300` | `(2.88, 5.76, 11.52, 14.4)` — left/top/right/bottom, each computed independently | as above |
| `test_a_full_page_layout_reports_no_margins` | `full == paint == (0,0,9916,14033)` | returns `None` | as above; this is the case the SS-08 spike actually measured |
| `test_a_zero_resolution_reports_nothing` | `resolution=0` | returns `None`, no `ZeroDivisionError` | as above |
| `test_a_paint_rect_outside_its_page_reports_nothing` | `paint=(-5,0,...)` | returns `None` | as above |
| `test_the_driver_margins_replace_only_the_imageable_area` | `profile_with_driver_margins(BUILTIN_PRESETS["generic_face_down_reversed"], (1.0,2.0,3.0,4.0))` | `.imageable_area_pt == (1.0,2.0,3.0,4.0)` and `.flip_axis`, `.reverse_stack`, `.back_offset_x_pt` are unchanged | as above |
| `test_no_driver_answer_leaves_the_profile_alone` | second argument `None` | the identical object's fields come back unchanged | as above |
| `test_reading_the_driver_never_writes_a_profile` | `monkeypatch.setenv("XDG_CONFIG_HOME", tmp_path)`; call `profile_with_driver_margins` | `list(tmp_path.rglob("*.json")) == []` | as above |

### `tests/test_hardening_printing.py` (extend; it already owns the stub)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_worker_collects_an_imageable_area_per_printer` | patch `app_main.available_printer_names` to `["A", "B"]` and `app_main.query_imageable_area_pt` to `{"A": (1,1,1,1)}.get`; run `_PrinterQueryWorker().run()`; `worker.imageable_areas == {"A": (1.0,1.0,1.0,1.0)}` | `AttributeError: '_PrinterQueryWorker' object has no attribute 'imageable_areas'` |
| `test_one_unreachable_printer_does_not_cost_the_others` | the patched query raises for `"A"` and answers for `"B"` | `worker.imageable_areas == {"B": ...}` and `worker.names == ["A", "B"]` | as above |
| `test_applying_printers_pushes_the_driver_margins_to_the_views` | `_FakeWindow` gaining `layout_panel`/`preview_view` `SimpleNamespace`s and a `set_printer_profile` bound from `MainWindow`; call `MainWindow._apply_printers(window, ["A"], None, {"A": (1,2,3,4)})` | `window.preview_view.profile.imageable_area_pt == (1.0,2.0,3.0,4.0)` | `TypeError: _apply_printers() takes from 2 to 3 positional arguments but 4 were given` |
| `test_a_printer_with_no_driver_answer_keeps_the_preset` | same, with `{}` | `...imageable_area_pt == (18.0, 18.0, 18.0, 18.0)` | as above |

### `tests/test_print_dialog.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_print_dialog_uses_the_drivers_imageable_area` | build a `PrintDialog` with `printer_names=["A"]`, a `profile_loader` that raises, a fake `session_cls`/`backend_cls` (the module's existing fakes), and `imageable_area_query=lambda name: (1.0, 2.0, 3.0, 4.0)`; call `start_print()` | the profile handed to `backend_cls` has `imageable_area_pt == (1.0,2.0,3.0,4.0)` | `TypeError: __init__() got an unexpected keyword argument 'imageable_area_query'` |

### Not a test, a `[HUMAN]` check

Nothing in the suite can prove the number matches the paper — no printer is
attached. See §5.

## 5. Acceptance

| Check | Command |
|---|---|
| The new module's tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_printer_capabilities.py` |
| The extended tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_hardening_printing.py tests/test_print_dialog.py` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The new module has a docs page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| The query is never made in full-page mode | `! grep -n "setFullPage" deckle/app/printer_capabilities.py` |
| A driver-read profile is never persisted | `! grep -n "\.save(" deckle/app/printer_capabilities.py` |
| Core stays Qt-free (the new module is under `app`, not `core`) | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| The preset is no longer the only source of an imageable area | `grep -c "profile_with_driver_margins" deckle/app/main.py deckle/app/views/print_dialog.py` (expect ≥1 each) |
| `[HUMAN]` The red line is true | Attach a real printer. Launch Deckle, import `tests/fixtures/sample.pdf`, click **Use printer margins**, and compare the margin the spinbox lands on against the printer's published non-printable border (or against a full-bleed test print measured with a ruler). They should agree to about a millimetre. |

## 6. Out of scope

- **B15** — pushing a profile when the user *changes* printer in the print
  dialog combo, and keeping panel/preview in step thereafter. N2 provides
  `set_printer_profile`; B15 wires the combo's `currentTextChanged` to it.
- **B16** (the app only ever resolves the first built-in preset, so a face-up
  printer gets the wrong reload instruction) — that is `flip_axis` /
  `reverse_stack`, which a driver does not report. **F1** is the fix.
- **B6** (printing scales the sheet into the imageable area, aspect ignored).
  N2 makes the rectangle correct; whether to scale into it at all is still an
  open owner decision.
- **F12's per-orientation imageable area.** `query_imageable_area_pt` reads the
  printer's *default* page size and orientation. On a driver whose margins
  differ between portrait and landscape, a landscape folio job gets the
  portrait answer. Record it, do not fix it here — the fix needs a decision
  about which paper the query is made against and when it is re-made.
- **The `landscape_imageable_unverified` warning kind** (declared in
  `models.py:246`, never emitted — B31). Tempting to emit here; it belongs with
  B31, which is about the whole declared-but-unproduced set.

## 7. decisions.md entry

```
## 2026-09-05 — The "printer's hardware limit" was a hard-coded 0.25in
- Symptom: The preview's red imageable-area guide, the clipping warnings and "Use printer margins" all read `DEFAULT_PROFILE`, the first built-in preset, whose `imageable_area_pt` is a fixed `(18, 18, 18, 18)`. Every Deckle on every machine drew the same rectangle, with or without a printer attached, while the GUIDE called it "your printer's hardware limit". A printer with a 0.55in trailing-edge margin clipped the tail of every page with no warning anywhere.
- Fix: `deckle/app/printer_capabilities.py` derives `(left, top, right, bottom)` in points from `QPageLayout.fullRectPixels`/`paintRectPixels` at the printer's own resolution, queried per printer inside the existing off-thread `_PrinterQueryWorker`. `profile_with_driver_margins` substitutes only that field into the resolved profile and never saves it -- a profile on disk is a calibration measured against paper, and a driver reading has not been. `MainWindow.set_printer_profile` is the single writer that pushes it to the panel and the preview.
- Surfaces: The SS-08 spike marked "Imageable area via paintRectPixels()" confirmed, but what it confirmed was the opposite behaviour: under `setFullPage(True)` -- which `QtPrintBackend` sets and this must not -- `paintRect` *is* `fullRect` and there is no inset to read. The margins live only in `StandardMode`. An all-zero answer is treated as "unknown", not as "prints to the edge", because no consumer printer does.
- Surfaces: `QtPrintBackend._paint_rendered_page` scales the rendered sheet into `imageable_area_pt`, so this was never only a preview defect -- the wrong rectangle was reaching paper.
- Watch: A capability spike answers the question it was asked. This one was asked "does Qt reapply its own margin under full page", answered "no", and was then cited for two years as evidence that the imageable area was readable. Re-read the *notes* column, not the verdict.
- Commit: <fill in>
```

## 8. Traps

- **`setFullPage(True)` erases the answer.** The query needs its own
  `QPrinter` in `StandardMode`. Never call `setFullPage` on it, and never reach
  into the one `QtPrintBackend._submit_chunk` builds — that one must stay
  full-page (`backend.py:488-490` says why).
- **`imageable_area_pt` is `(left, top, right, bottom)` *margins*, not an
  `(x0, y0, x1, y1)` rect.** `layout_panel.imageable_inset_pt`'s docstring
  (lines 165-183) records that reading it as a rect yields a ~600pt "inset". The
  arithmetic in §3 produces margins; keep it that way.
- **`paintRectPixels`'s origin is relative to `fullRect`'s**, which is why the
  formula subtracts `fx`/`fy` rather than assuming zero. On every platform
  measured `fullRect` starts at `(0, 0)`, but the subtraction costs nothing and
  the assumption is not documented by Qt.
- **`QPrinterInfo.printerInfo(name)` for a queue that has gone away returns a
  null info, not an exception.** `info.isNull()` is the check;
  `backend.printer_is_available` uses the same one.
- **Enumeration is already bounded by a 5s deadline** (`PRINTER_QUERY_TIMEOUT_MS`,
  `main.py:80`) and this adds one Qt call *per printer* inside it. That is the
  right side of the deadline — but a `_PrinterQuery` that times out must apply
  `{}`, not a half-filled dict, and step 4's `time_out` does exactly that.
- **`_apply_printers` is called unbound from `tests/test_hardening_printing.py`
  with two positional arguments.** The new parameter must have a default, or
  four existing tests break for no reason.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
- **Constructing a real `QMainWindow` under pytest exits 127 here** — see
  `tests/test_gui_workflow.py:9`. Every GUI test above uses the stub /
  direct-widget patterns instead.
