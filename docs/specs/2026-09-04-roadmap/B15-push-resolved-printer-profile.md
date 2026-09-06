# B15 — Push the resolved printer profile to the panel and the preview

**Roadmap item:** `docs/ROADMAP.md` B15
**Depends on:** —
**Blocks:** N2, F1 (both need a way to get a profile onto the preview; this is it)
**Size:** M
**Decision needed first:** none

---

## 1. Context

The preview draws a solid red rectangle and the GUIDE says what it is:

> **Solid red — the imageable area.** Your printer's hardware limit; the
> border your printer physically cannot mark.
> — `docs/GUIDE.md:174`

> **Two guides, drawn distinctly.** Solid red is the printer's *imageable
> area* — a hardware limit, the border your printer physically cannot mark.
> — `README.md:331-332`

It is not. It is `BUILTIN_PRESETS["generic_face_down_reversed"]`'s
`imageable_area_pt`, which is `(18.0, 18.0, 18.0, 18.0)`
(`deckle/core/profiles.py:181-191`) — a flat quarter inch on every edge,
the same number for every printer ever made. `MainWindow` computes it
once at import:

```python
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))
```

(`deckle/app/main.py:75-78`), hands it to `LayoutPanel` at construction
(`:442`) and to `PreviewView` at construction (`:521-526`), and **never
touches either again**. Nothing in `deckle/app/` ever assigns
`preview_view.profile` or `layout_panel.profile` after that point:

```
$ grep -rn "\.profile = " --include="*.py" deckle
deckle/app/views/preview_view.py:549:        self.profile = profile
deckle/app/views/print_dialog.py: (none)
deckle/app/backend.py:278:        self.profile = profile
deckle/core/print_session.py:500:        session.profile = profile
```

Meanwhile the print dialog *does* resolve a real profile — saved for that
printer if one exists, else the same builtin — every time a run starts
(`print_dialog.py:248-249, 266, 289`). That answer reaches the backend
and is used to place ink on paper. It never reaches the two places that
show the user where the ink can go.

Three concrete consequences for someone printing a book:

1. A user who has a saved `PrinterProfile` — today that means a
   hand-written JSON in the config dir, which is the only way a profile
   ever gets written (nothing in `deckle/` calls `PrinterProfile.save`;
   that is F1) — sees the *generic* guide, and "Use printer margins"
   sets the *generic* inset, while their print run uses their real one.
   The preview and the paper disagree by construction.
2. Selecting a different printer in the Print dialog changes nothing on
   screen, even though it changes which profile the run will use.
3. The `clipped_by_imageable_area` warning
   (`preview_view.py:141-151`) is computed against the generic rectangle,
   so a sheet flagged as safe may not be, and vice versa.

**What this spec does not do.** It does not make the red line true. The
number in a builtin preset is still a guess; making it the *driver's*
number means reading `QPrinter.pageLayout().paintRectPixels()`, which the
spike confirmed works identically on Windows and Linux
(`docs/spikes/qprinter-capability-report.md`, "Imageable area via
`pageLayout().paintRectPixels()` — confirmed" on both platforms) — and
that is **N2**, a separate spec. This one builds the pipe. Afterwards the
red line is "the profile that will actually be used for this run", which
is a true statement; N2 and F1 are what make that profile a measurement
rather than a default. Say so in the commit and leave D4's GUIDE wording
to the documentation pass.

## 2. Current code

### The seed, and its two consumers

`deckle/app/main.py:75-78`:

```python
# Used to seed PreviewView before any printer/profile has been chosen -- the
# same fallback resolve_profile() reaches for when a printer has no saved
# PrinterProfile yet (see deckle/app/views/print_dialog.py).
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))
```

`deckle/app/main.py:442`:

```python
        self.layout_panel = LayoutPanel(self.state, controls, profile=DEFAULT_PROFILE)
```

`deckle/app/main.py:521-526`:

```python
        self.preview_view = PreviewView(
            recompute_plan(self.state.project),
            DEFAULT_PROFILE,
            output,
            layout_settings=self.state.project.layout,
        )
```

### What the panel does with it — `deckle/app/views/layout_panel.py:1444-1456`

```python
    def _on_use_printer_margins(self) -> None:
        """Set the margin to the active printer's non-printable inset."""
        profile = getattr(self, "profile", None)
        if profile is None:
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        # Set every margin, regardless of link state -- the printer's dead
        # border applies to all four edges, so a partial application would
        # leave some edge still unprintable.
        self.margin_spinboxes["margin_top_pt"].setValue(from_points(inset, self._unit))
        if not self.link_margins_check.isChecked():
            for field in ("margin_bottom_pt", "margin_outer_pt"):
                self.margin_spinboxes[field].setValue(from_points(inset, self._unit))
```

The button's tooltip (`layout_panel.py:1028-1031`) says "Set the margin to
the printer's non-printable inset" and names no printer.

### What the preview does with it

`deckle/app/views/preview_view.py:870-879` — the red rectangle:

```python
        painter = QPainter(pixmap)
        try:
            imageable = QPen(QColor(220, 40, 40))
            imageable.setWidth(2)
            painter.setPen(imageable)
            painter.drawRect(
                to_image_rect(
                    imageable_rect_pt(self.plan.paper_pt, self.profile.imageable_area_pt)
                )
            )
```

and `preview_view.py:755-757` — the profile is handed to every render, so
the warnings recompute with it:

```python
        worker = PreviewWorker(
            self.plan, self.profile, self.sheet_index, self.side, sides=sides
        )
```

`PreviewView` has no setter; `self.profile` is assigned once at
`preview_view.py:549`.

### Where a real profile is resolved — `deckle/app/views/print_dialog.py:59-82, 248-249`

```python
def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> PrinterProfile:
    """A profile for ``printer_name``: saved, else a builtin default.
    ...
    :returns: the saved profile, else the first builtin preset. Never
        raises for a missing profile -- a printer with no calibration is
        the normal case, not an error.
    """
    try:
        return profile_loader(printer_name)
    except (FileNotFoundError, OSError):
        pass
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    return next(iter(presets.values()))
```

```python
    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
        return resolve_profile(printer_name, self._profile_loader, self._builtin_presets)
```

Called at `print_dialog.py:266` (`start_print`) and `:289`
(`_offer_resume`). The dialog also preselects a printer at construction
(`:209-214`) **without** resolving a profile for it.

`PrintDialog` is a plain class, not a `QObject`, and currently exposes no
signals at all.

### The backend, for the boundary

`deckle/app/backend.py:515-529` places ink using
`self.profile.imageable_area_pt` and nothing else. It never asks the
driver:

```python
    def _paint_rendered_page(self, painter, printer, rendered: RenderedPage) -> None:
        if rendered.width == 0 or rendered.height == 0:
            return
        image = _qimage(rendered.rgba, rendered.width, rendered.height)
        left_pt, top_pt, right_pt, bottom_pt = self.profile.imageable_area_pt
        dpi_scale = printer.resolution() / 72.0
        target_x = left_pt * dpi_scale
        target_y = top_pt * dpi_scale
        target_w = max(0.0, printer.width() - (left_pt + right_pt) * dpi_scale)
        target_h = max(0.0, printer.height() - (top_pt + bottom_pt) * dpi_scale)
```

Changing that to read `pageLayout().paintRectPixels()` is N2. **Do not
touch `backend.py` in this spec.** (`_paint_rendered_page`'s
aspect-ignoring scale is B6, a third separate thing, pinned by
`tests/test_print_painting.py:167-225` as an undecided product decision.)

### Every use of `DEFAULT_PROFILE` and `BUILTIN_PRESETS`

`grep -rn "DEFAULT_PROFILE\|BUILTIN_PRESETS" --include="*.py" deckle tests`:

| Site | Use |
|---|---|
| `deckle/app/main.py:34, 78` | the import and the definition |
| `deckle/app/main.py:442, 523` | the two consumers this spec changes |
| `deckle/app/views/print_dialog.py:24, 81` | `resolve_profile`'s fallback |
| `deckle/cli.py:27, 439, 444, 1302` | `_resolve_profile` and its error text |
| `deckle/core/profiles.py:181-201` | the two presets themselves |
| `tests/test_view_workers.py:21, 47`, `tests/test_preview_paint.py:24, 63`, `tests/test_print_log_failure.py:49, 81`, `tests/test_registration.py:29, 62`, `tests/test_printing.py:15, 102-110`, `tests/test_print_session_state_validation.py:37, 68`, `tests/test_config_store_durability.py:35, 117, 310` | test fixtures |

`DEFAULT_PROFILE` itself is referenced only inside `main.py`. No test
imports it.

### Existing tests that touch this

- `tests/test_preview_paint.py` builds `PreviewView(plan, profile, None,
  layout_settings=...)` with `next(iter(BUILTIN_PRESETS.values()))`
  (`:63`) and renders through `_render_current` (`:66-81`), which
  constructs a `PreviewWorker` from `view.profile`.
- `tests/test_preview_fidelity.py` asserts the preview's render path is
  the export path, and that this module imports no dialog widget.
- `tests/test_print_dialog.py:200-330` drives `PrintDialog` with a stub
  session and injected confirmations; `:200-213` covers preselection.
- `tests/test_ui_surface.py:355-370` monkeypatches
  `print_dialog._available_printer_names` with a spy.
- Nothing anywhere asserts that a resolved profile reaches the preview.
  `grep -rn "preview_view.profile\|layout_panel.profile" tests/` returns
  nothing.

## 3. Change

Chosen: a signal on `PrintDialog` carrying `(printer_name, profile)`,
connected by `MainWindow`. Rejected: having `PrintDialog` reach into
`window.preview_view` directly — the dialog is constructed with a bare
`parent` and is deliberately drivable headlessly with every dependency
injected (`print_dialog.py:11-15`), and giving it a window to poke would
undo that.

Rejected: pushing only the profile. The name is what lets both consumers
say *whose* number this is, and "0.25 in, from the generic preset" is a
materially different statement from "0.25 in, from Brother HL-2270DW".

1. **`deckle/app/views/print_dialog.py` — add signals.** Follow
   `LayoutPanel`'s pattern (`layout_panel.py:699-705`): a private
   `QObject` subclass built inside `__init__`, with the signal exposed as
   an attribute on the plain class.

   In `_qt_widgets()`'s neighbourhood add:

   ```python
   def _qt_core():
       from PySide6.QtCore import QObject, Signal

       return QObject, Signal
   ```

   and near the top of `PrintDialog.__init__`, before the widgets are
   built:

   ```python
        QObject, Signal = _qt_core()

        class _Signals(QObject):
            # (printer name, PrinterProfile). The name travels with the
            # profile because both consumers say whose number they are
            # showing, and "0.25in, from the generic preset" is a
            # materially different statement from "0.25in, from Brother
            # HL-2270DW".
            profile_resolved = Signal(str, object)

        self._signals = _Signals()
        #: Emitted whenever this dialog settles on a PrinterProfile: at
        #: construction for the preselected printer, when the user picks
        #: a different one, and at the start of every run and resume.
        #: `MainWindow` listens so the preview's red imageable-area guide
        #: and "Use printer margins" describe the printer the job will
        #: actually go to, rather than a fixed builtin preset.
        self.profile_resolved = self._signals.profile_resolved
   ```

2. **`print_dialog.py` — emit from `_resolve_profile`.** One place, so
   every caller emits:

   ```python
    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
        """The profile for ``printer_name``, announced as it is resolved.

        :param printer_name: the printer to resolve for.
        :returns: the saved profile, else a builtin preset.
        """
        profile = resolve_profile(
            printer_name, self._profile_loader, self._builtin_presets
        )
        self.profile_resolved.emit(printer_name, profile)
        return profile
   ```

3. **`print_dialog.py` — resolve at construction and on selection.**
   After the printer combo is populated and preselected
   (`print_dialog.py:209-214`), add:

   ```python
        self.printer_combo.currentTextChanged.connect(self._on_printer_changed)
        if names:
            # Announce the preselected printer's profile straight away.
            # Choosing a printer is what decides the imageable area, and
            # the user has now chosen one -- waiting until Print is
            # pressed would mean the preview showed a generic guide right
            # up to the moment paper moved.
            self._resolve_profile(self.printer_combo.currentText())
   ```

   and:

   ```python
    def _on_printer_changed(self, printer_name: str) -> None:
        """Re-resolve when the user picks a different printer.

        :param printer_name: the newly selected printer.
        :returns: nothing. The resolution is announced through
            ``profile_resolved``; nothing is printed.
        """
        self._resolve_profile(printer_name)
   ```

   Note the emission at construction happens **before**
   `self._resumable`/`_offer_resume` at `print_dialog.py:241-244`, which
   is correct: a resume then re-emits for the resumed session's printer,
   which may differ from the preselected one.

4. **`deckle/app/views/preview_view.py` — `PreviewView.set_profile`.**
   Add after `on_layout_changed` (which ends at `:737`):

   ```python
    def set_profile(self, profile: PrinterProfile) -> None:
        """Adopt a new printer profile and redraw the visible sheet.

        The red guide and the ``clipped_by_imageable_area`` warnings are
        both computed from ``profile.imageable_area_pt``, so a profile
        that changed without a re-render would leave the view stating the
        previous printer's limits.

        :param profile: the profile now in force.
        :returns: nothing. A no-op when it is the profile already in use
            -- ``PrinterProfile`` is a frozen dataclass, so equality is
            by value, and re-rendering a sheet nobody asked to change is
            a wasted pdfium pass on the one lock every render shares.
        """
        if profile == self.profile:
            return
        self.profile = profile
        self.refresh()
   ```

5. **`deckle/app/views/layout_panel.py` — `LayoutPanel.set_profile`.**
   Add beside `set_document_loaded` (`layout_panel.py:1288-1298`), which
   is the existing "the window tells the panel something" method:

   ```python
    def set_profile(self, profile, printer_name: str = "") -> None:
        """Adopt the printer profile "Use printer margins" applies.

        Called by the window rather than watched from here, matching
        :meth:`set_document_loaded`: there is one place that decides which
        printer the job is going to.

        :param profile: a ``PrinterProfile``, or ``None`` for none.
        :param printer_name: whose profile it is, for the tooltip. Empty
            when it is a builtin default rather than a real printer's.
        :returns: nothing.
        """
        self.profile = profile
        self.use_printer_margins_button.setEnabled(profile is not None)
        if profile is None:
            self.use_printer_margins_button.setToolTip(
                "No printer profile is in force yet -- open Print and "
                "choose a printer."
            )
            return
        inset = imageable_inset_pt(profile.imageable_area_pt)
        whose = f"{printer_name}'s" if printer_name else "the generic preset's"
        self.use_printer_margins_button.setToolTip(
            "Set the margin to the printer's non-printable inset, so "
            "content clears the dead border on every edge.\n\n"
            f"Currently {inset:g}pt, from {whose} profile."
        )
   ```

   The `getattr(self, "profile", None)` guard in `_on_use_printer_margins`
   (`layout_panel.py:1446`) stays as it is: `profile` is set in
   `__init__` and may legitimately be `None`.

6. **`deckle/app/main.py` — hold the profile and connect.**

   Update the comment on `DEFAULT_PROFILE` (`main.py:75-78`) to say it is
   a **seed**, replaced the moment a printer is resolved:

   ```python
   # Seeds LayoutPanel and PreviewView before any printer has been
   # resolved -- the same fallback resolve_profile() reaches for when a
   # printer has no saved PrinterProfile (see print_dialog.py). It is
   # replaced by `_on_profile_resolved` as soon as one is, so the red
   # imageable-area guide describes the printer the job will go to rather
   # than a fixed 0.25in that belongs to no printer at all.
   DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))
   ```

   In `__init__`, after `self._printer_message = ""` (`main.py:578`):

   ```python
        #: The profile in force: what the preview's red guide draws and
        #: what "Use printer margins" applies. Seeded with the builtin
        #: default and replaced as soon as a printer resolves one.
        self.profile = DEFAULT_PROFILE
        self.profile_printer_name = ""
   ```

   Add the slot, beside `_on_layout_changed` (`main.py:604-607`):

   ```python
    def _on_profile_resolved(self, printer_name: str, profile) -> None:
        """Adopt the profile a printer selection settled on.

        The preview's red guide and the panel's "Use printer margins"
        both describe a printer's non-printable border, and until now
        both described a builtin preset's fixed quarter inch for the
        life of the session -- while the print run itself used whatever
        `resolve_profile` returned. One number on paper, a different one
        on screen.

        :param printer_name: the printer it belongs to.
        :param profile: the resolved ``PrinterProfile``.
        :returns: nothing.
        """
        self.profile = profile
        self.profile_printer_name = printer_name
        self.layout_panel.set_profile(profile, printer_name)
        self.preview_view.set_profile(profile)
        log_event(
            "printer_profile_adopted",
            printer=printer_name,
            imageable_area_pt=list(profile.imageable_area_pt),
        )
   ```

   And in `_on_print_clicked` (`main.py:755-768`), connect before
   showing:

   ```python
        self.print_dialog = PrintDialog(plan, self.window, printer_names=printers)
        self.print_dialog.profile_resolved.connect(self._on_profile_resolved)
        self.print_dialog.widget.exec()
   ```

   Because `PrintDialog.__init__` emits for the preselected printer
   *during construction*, that first emission happens before the connect
   and is lost. Resolve it in the window instead of reordering the
   dialog: after connecting, push the dialog's current selection through
   once —

   ```python
        self.print_dialog.profile_resolved.connect(self._on_profile_resolved)
        # The dialog resolves for its preselected printer inside
        # __init__, before this connection exists, so ask again rather
        # than deferring the dialog's own resolution -- a resume offer
        # fires from the constructor too and must not be delayed.
        self.print_dialog.announce_current_profile()
   ```

   with, in `print_dialog.py`:

   ```python
    def announce_current_profile(self) -> None:
        """Re-emit ``profile_resolved`` for the printer now selected.

        For a listener that connected after construction. Resolving twice
        is a file read, not a print.

        :returns: nothing. A no-op when there are no printers.
        """
        name = self.printer_combo.currentText()
        if name:
            self._resolve_profile(name)
   ```

7. **Adopt a profile at startup, once the printer list arrives.** Without
   this the guide stays generic until the user opens Print for the first
   time, which for someone who only ever exports a PDF is never.
   In `_apply_printers` (`main.py:727-753`), at the end of the
   `if has_printers:` branch:

   ```python
        if has_printers:
            self.print_button.setToolTip("")
            self._printer_message = ""
            self._adopt_startup_profile(printers)
   ```

   and:

   ```python
    def _adopt_startup_profile(self, printers: list[str]) -> None:
        """Show the profile of the printer a print run would preselect.

        Opening the Print dialog is not the moment a user's imageable
        area becomes true -- someone checking margins in the preview may
        never open it at all.

        :param printers: the enumerated printer names.
        :returns: nothing, and never raises. A profile that cannot be
            read must not be what stops the printer list arriving; the
            builtin default already in force is the safe fallback, and
            the print dialog reports the same file properly when the user
            gets there.
        """
        from deckle.core.profiles import PrinterProfile
        from deckle.app.views.print_dialog import (
            resolve_profile,
            select_preselected_printer,
        )

        try:
            name = select_preselected_printer(printers, PrinterProfile.load)
            if name is None:
                return
            self._on_profile_resolved(name, resolve_profile(name))
        except Exception as exc:  # noqa: BLE001 -- degraded, not fatal
            log_exception("printer_profile_startup_failed", exc)
   ```

   The broad `except` is not defensive padding. `PrinterProfile.load`
   raises `json.JSONDecodeError` for a truncated file and
   `deckle.core.schema.StoredValueError` for a value this build cannot
   honour (`profiles.py:139-147`), and `resolve_profile` catches only
   `(FileNotFoundError, OSError)` — so a corrupt saved profile propagates.
   That is **B21**'s second half ("the print dialog catches only
   `OSError` so the same file crashes it"), and fixing it properly
   belongs there. Until then this call site must not turn a corrupt
   config file into a window that cannot finish enumerating printers.

8. **`tests/test_hardening_printing.py`'s `_FakeWindow` needs one line.**
   `_apply_printers` is called as an unbound method against that stand-in
   (`tests/test_hardening_printing.py:270-330`), and step 7 adds a call
   to `self._adopt_startup_profile`. Add to the class body, beside the
   two existing borrowed methods at `:294-295`:

   ```python
       #: Borrowed too: _apply_printers now resolves a profile for the
       #: preselected printer, which needs a real config directory. What
       #: these tests are about is which button gets enabled.
       _adopt_startup_profile = lambda self, printers: None  # noqa: E731
   ```

   Three tests call `_apply_printers` on it
   (`test_zero_printers_disables_print_but_leaves_save_pdf_alone`,
   `test_with_no_document_the_status_bar_names_the_first_step_not_the_printer`,
   `test_a_document_with_printers_present_clears_the_status_bar`) and
   only the third reaches the new branch.

## 4. Tests

### New: `tests/test_profile_plumbing.py`

Headless; offscreen platform set before any Qt import; a module-scoped
`QApplication` fixture as in `tests/test_print_dialog.py:31-37`. Reuse
that file's `_make_plan`, `_profile` and `_StubBackend` helpers by
importing them, or copy the four short ones.

| Test | Setup | Assertion in words | Failure on the unfixed tree |
|---|---|---|---|
| `test_the_dialog_announces_the_profile_it_resolves` | `PrintDialog(plan, printer_names=["A"], profile_loader=lambda n: saved, ...)`; collect `profile_resolved` emissions | at least one emission, `(("A", saved))` | `AttributeError: 'PrintDialog' object has no attribute 'profile_resolved'` |
| `test_choosing_another_printer_announces_its_profile` | two printers, a loader returning a distinct profile per name; `dialog.printer_combo.setCurrentText("B")` | the last emission is `("B", profile_b)` | same |
| `test_starting_a_run_announces_the_profile_it_will_print_with` | drive `start_print()` with the stub session | an emission whose profile is the one handed to `session_cls` | same |
| `test_a_resume_announces_the_resumed_printers_profile` | a resumable summary naming printer `"B"` while `"A"` is preselected | an emission for `"B"` | same |
| `test_announcing_again_re_emits_for_a_late_listener` | connect after construction, call `announce_current_profile()` | exactly one emission, for the preselected printer | `AttributeError: ... 'announce_current_profile'` |
| `test_the_preview_redraws_when_the_profile_changes` | `PreviewView(plan, profile_a, None)`; `_render_current(view)`; `view.set_profile(profile_b)` where `profile_b` has `imageable_area_pt=(72,72,72,72)` | `view.profile is profile_b`, and the imageable rect the guide would draw (`imageable_rect_pt(plan.paper_pt, view.profile.imageable_area_pt)`) has moved | `AttributeError: 'PreviewView' object has no attribute 'set_profile'` |
| `test_setting_the_same_profile_does_not_re_render` | patch `view.refresh` with a counter; `view.set_profile(same)` | the counter is 0 | same |
| `test_the_panel_adopts_the_inset_it_is_given` | `LayoutPanel(state)`; `panel.set_profile(profile_72, "Brother")`; click `use_printer_margins_button` | `state.project.layout.margin_top_pt == approx(72.0)` | `AttributeError: 'LayoutPanel' object has no attribute 'set_profile'` |
| `test_the_panel_names_whose_inset_it_is_offering` | as above | the button's tooltip contains `"Brother"` and `"72"` | same |
| `test_the_panel_disables_the_button_with_no_profile` | `panel.set_profile(None)` | the button is disabled and its tooltip says to choose a printer | same |
| `test_a_corrupt_saved_profile_does_not_break_printer_enumeration` | a `_FakeWindow`-style stand-in with a real `_adopt_startup_profile`, with `PrinterProfile.load` patched to raise `StoredValueError` | `_apply_printers(window, ["A"])` returns, the print button is enabled, and `printer_profile_startup_failed` is in the diagnostics log | `AttributeError: ... '_adopt_startup_profile'`. This is the guard against B21 crossing into the launch path. |

### Extend: `tests/test_preview_paint.py`

Add one test in the file that already follows the pixels to
`image_label`, because "the guide moved" is the assertion that is worth
having in pixels rather than in coordinates:

- `test_the_imageable_guide_follows_the_resolved_profile`: render with
  `imageable_area_pt=(18,18,18,18)`, capture
  `view._source_pixmap.toImage()`, `set_profile` a `(72,72,72,72)`
  profile, `_render_current(view)` again, and assert the two images
  differ. Fails today at `set_profile`.

### Extend: `tests/test_ui_surface.py`

- `test_the_window_pushes_a_resolved_profile_to_both_views`: a structural
  test in the style of the `_main_source()` family already there
  (`tests/test_ui_surface.py:544-618`), because a real `MainWindow`
  cannot be constructed under pytest. Assert `main.py`'s source contains
  `self.layout_panel.set_profile(` and `self.preview_view.set_profile(`,
  and that `profile_resolved.connect` appears. On the unfixed tree all
  three are absent.

## 5. Acceptance

| Check | Command |
|---|---|
| The dialog has the signal | `grep -q "profile_resolved = Signal(str, object)" deckle/app/views/print_dialog.py` |
| Every resolution announces itself | `sed -n '/    def _resolve_profile/,/^    def /p' deckle/app/views/print_dialog.py \| grep -q "profile_resolved.emit"` |
| The preview has a setter | `grep -qE "^    def set_profile\(" deckle/app/views/preview_view.py` |
| The panel has a setter | `grep -qE "^    def set_profile\(" deckle/app/views/layout_panel.py` |
| The window pushes to both | `grep -q "self.layout_panel.set_profile(" deckle/app/main.py && grep -q "self.preview_view.set_profile(" deckle/app/main.py` |
| The backend was not touched (N2's boundary) | `! grep -q "paintRect" deckle/app/backend.py` |
| The preview still imports no dialog widget | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_preview_fidelity.py -q --no-header -p no:cacheprovider` |
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_profile_plumbing.py -q --no-header -p no:cacheprovider` |
| The print-dialog suite still passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_print_dialog.py tests/test_hardening_printing.py -q --no-header -p no:cacheprovider` |
| The full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

Verified on the unfixed tree — the first five rows exit 1, the sixth
exits 0:

```
$ grep -c "paintRect" deckle/app/backend.py
0
$ grep -rn "\.profile = " --include="*.py" deckle/app/views/preview_view.py deckle/app/views/layout_panel.py
deckle/app/views/preview_view.py:549:        self.profile = profile
```

## 6. Out of scope

- **N2 — pre-fill the imageable area from the driver**
  (`QPrinter.pageLayout().paintRectPixels()`, confirmed on both platforms
  by `docs/spikes/qprinter-capability-report.md`). That is what makes the
  red line *true*. This spec makes it *the profile that will be used*,
  which is a different and smaller claim. Nothing in `backend.py` changes
  here.
- **F1 — a profile picker and editor in the app.** Nothing in `deckle/`
  calls `PrinterProfile.save`; the only writer of a profile today is a
  person with a text editor. Until F1, the only users who see a
  difference from this spec are those who wrote one by hand.
- **B16** — the app only ever resolves the *first* builtin preset, so a
  face-up printer owner gets the wrong reload instruction. `resolve_profile`'s
  `next(iter(presets.values()))` is unchanged here.
- **B21** — `resolve_profile` catches only `(FileNotFoundError, OSError)`
  while `PrinterProfile.load` also raises `json.JSONDecodeError` and
  `StoredValueError`. Step 7 guards the *new* call site against it; the
  print dialog's own crash on the same file is B21's to fix.
- **B6** — the backend scales the whole sheet into the imageable area
  with aspect ignored. Pinned by `tests/test_print_painting.py:167-225`
  as an undecided product decision (roadmap §6, first open question).
- **D4** — the GUIDE and README calling the red line "your printer's
  hardware limit". Still not strictly true after this; leave the wording
  to the documentation pass, which should land with N2.

### Collisions with the other specs in this directory

| Spec | Overlap | Order |
|---|---|---|
| `B16-print-dialog-profile-combo.md` | edits `PrintDialog.__init__` (right after the printer row, `print_dialog.py:215`) and `_resolve_profile` (`:248-249`) — **the same two places this spec edits**, and it adds a *preset* combo whose selection also changes the resolved profile | **B16 first.** Then this spec's `_resolve_profile` emit sits on top of B16's combo read, and `_on_printer_changed` gains a sibling `_on_preset_changed` that also announces. Doing B15 first means writing `_resolve_profile` twice. |
| `B21-a-corrupt-profile-says-so.md` | widens what `resolve_profile` catches | either order. If B21 lands first, step 7's broad `except` is belt-and-braces rather than load-bearing — keep it anyway and say so in the comment. |
| `N2-driver-imageable-area.md` | the successor: fills a profile's imageable area from `pageLayout().paintRectPixels()` | **B15 first** — N2 needs somewhere to push its number, and this is it |
| `F1-profile-editor.md` | makes `PrinterProfile.save` reachable, so a saved profile exists to resolve | B15 first; F1 then emits through the same signal after a save |
| `B30-requery-printers.md` | `MainWindow._on_print_clicked` | either order; different lines of the same method |
| `M3-main-window-split.md` | `_apply_printers`, `_on_print_clicked`, `__init__` all stay in `main.py` under M3 | M3 first, so this spec is not written against code that is about to move |

## 7. decisions.md entry

```
## 2026-09-XX — The preview's red line described a printer nobody owns
- Symptom: The GUIDE calls the solid red guide "your printer's hardware limit". It was `BUILTIN_PRESETS["generic_face_down_reversed"].imageable_area_pt` -- a flat 0.25in on every edge -- computed once at import as `DEFAULT_PROFILE`, handed to `LayoutPanel` and `PreviewView` at construction, and never replaced for the life of the session. Meanwhile the print dialog resolved a real profile for the selected printer on every run and sent it to the backend, so the number placing ink on paper and the number drawn on screen were different objects and nothing connected them. "Use printer margins" applied the generic inset for the same reason, and the `clipped_by_imageable_area` warning was computed against the generic rectangle.
- Fix: `PrintDialog` gained a `profile_resolved(str, object)` signal, emitted from the one method that resolves -- so construction, a change of printer, a run and a resume all announce. `PreviewView.set_profile` re-renders the visible sheet; `LayoutPanel.set_profile` adopts the inset and names whose it is in the button's tooltip. `MainWindow` also adopts a profile as soon as printer enumeration returns, because someone checking margins in the preview may never open Print at all.
- Surfaces: This does NOT make the red line true, and the commit says so. The number in a builtin preset is still a guess; making it the driver's number means `pageLayout().paintRectPixels()`, which the QPrinter spike confirmed on both platforms, and that is N2. What changes is that the guide now shows the profile the job will actually use, which is a claim the code can keep. The startup adoption is wrapped in a broad `except` on purpose: `PrinterProfile.load` raises `JSONDecodeError` and `StoredValueError` that `resolve_profile` does not catch (B21), and a corrupt config file must not be what stops the printer list arriving.
- Watch: The dialog resolves for its preselected printer inside `__init__`, before any caller can connect -- so a signal alone would have silently dropped the first and most important emission, the one that fires when the dialog opens. `announce_current_profile()` exists for that, and the test for it asserts a late listener gets exactly one emission rather than none. A signal emitted during construction is a signal nobody has subscribed to yet.
- Commit: <fill in>
```

## 8. Traps

- **`PrintDialog` is not a `QObject`.** It is a plain class holding a
  `QDialog` at `self.widget` (`print_dialog.py:120-152, 201`). Signals
  need the inner-`QObject` pattern `LayoutPanel` uses
  (`layout_panel.py:699-705`); a `Signal` declared on a plain class does
  nothing and raises nothing.
- **`_Signals` must be kept alive.** Assign it to `self._signals`, as
  `LayoutPanel` does. A `QObject` created and dropped takes its signals
  with it and the connection silently stops working.
- **The constructor emits before anyone can connect.** Step 6's
  `announce_current_profile()` is the fix; do not "solve" it by deferring
  the dialog's own resolution to after `exec()`, because
  `_offer_resume` also runs from the constructor
  (`print_dialog.py:241-244`) and resolves a profile of its own.
- **`PrinterProfile` is a frozen dataclass** (`profiles.py:30-54`), so
  `==` is value equality and `set_profile`'s no-op guard works. Do not
  write `is` there: `resolve_profile` returns a freshly-loaded instance
  each time, so `is` would re-render on every emission — and every render
  takes the process-wide pdfium lock (`core/render.py:68`).
- **`PreviewView.set_profile` must call `refresh()`, not just assign.**
  The profile is read in two places that only run during a render: the
  worker's `build_preview_frame` (`preview_view.py:505-507`) and the
  guide in `_frame_pixmap` (`:877`). An assignment alone changes nothing
  on screen until the user happens to scrub a sheet.
- **`tests/test_hardening_printing.py::_FakeWindow` will raise
  `AttributeError`** the moment `_apply_printers` calls a new method on
  `self`. Three tests use it. The one-line stub in step 8 is not
  optional.
- **`resolve_profile` does not catch everything `PrinterProfile.load`
  raises.** `json.JSONDecodeError` and `StoredValueError` are both
  `ValueError` subclasses and both escape (`print_dialog.py:77-80`
  against `profiles.py:139-147`). The startup path must guard; the dialog
  path is B21.
- **Do not touch `backend.py`.** `_paint_rendered_page` reading
  `pageLayout().paintRectPixels()` is N2; changing its scaling is B6, and
  `tests/test_print_painting.py:167-225` deliberately pins the current
  behaviour as an open product question.
- **Ordering against M3 and B17.** All three edit `main.py`. Recommended
  order **M1 → M3 → B15 → B17**: after M3, `_apply_printers` and
  `_on_print_clicked` are still in `main.py` (M3 keeps them there
  deliberately), so this spec's edits land in the same place either way —
  but doing B15 first means M3 has to move code that was written a day
  ago.
- `python -m deckle` launches the GUI and blocks. A real `QMainWindow`
  under pytest exits 127 here (`tests/test_ui_surface.py:534-541`), which
  is why step 6's window wiring is asserted structurally from source
  rather than by construction.
