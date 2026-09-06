# F1 — Give the app a printer-profile picker and editor

**Roadmap item:** `docs/ROADMAP.md` F1
**Depends on:** B16 — either fixed first, or **skipped as superseded**. F1 is
the full fix for B16 and does the same work properly; doing B16 first is
wasted effort. If B16 is already merged, delete whatever preset-selection
control it added (see §8) rather than leaving two ways to choose a preset.
**Blocks:** F3 (needs somewhere to type the measured offset), F9 (needs a
per-printer place to put the duplexer checkbox's assumptions)
**Size:** M
**Decision needed first:** none. F1 deliberately does **not** touch B6
("draw the sheet 1:1 or keep scaling to the imageable area?"): it edits the
stored numbers, not what the backend does with them.

---

## 1. Context

`PrinterProfile` is complete: it validates on load, tolerates field drift in
both directions, and writes itself atomically. **Nothing in `deckle/` ever
calls `PrinterProfile.save`.** The only writers today are three tests:

```
$ grep -rn "\.save(" deckle/ tests/ | grep -i profile
tests/test_registration.py:71:    _profile(back_offset_x_pt=3.5, back_offset_y_pt=-2.25).save("Calibrated")
tests/test_printing.py:136:    profile.save("My Test Printer")
tests/test_config_store_durability.py:176:    profile.save("ink")
```

So the user-facing path to a calibrated printer is: open a JSON file the app
never mentions, in a directory the GUIDE names only as
`%APPDATA%\Deckle\printer_profiles`, and hand-edit eight fields.

Three concrete failures follow from that.

**A face-up printer gets the wrong reload instruction, with no way to fix
it.** `resolve_profile` falls back to `next(iter(presets.values()))`, which
is always `generic_face_down_reversed`. A user whose printer stacks face-up
and keeps its order is told to "reverse the printed stack (flip the whole
stack over) before reloading" and to "flip each sheet on its long edge". Do
that and pass 2 prints every back onto the wrong front, upside down. This is
B16, and D3 records the GUIDE claiming two presets are available when only
one is reachable.

**The measured registration offset has no home in the app.** GUIDE §6 tells
the user to "store them on the profile once you know them, as
`back_offset_x_pt` and `back_offset_y_pt` in the printer's JSON file". F3
exists to make those numbers readable off a printed target; without F1 there
is nowhere to put them without a text editor.

**A corrupt profile crashes the print dialog.** `resolve_profile` catches
only `FileNotFoundError`/`OSError`, so `StoredValueError` (a `ValueError`)
from a hand-edited `"flip_axis": "diagonal"` propagates out of
`PrintDialog.start_print`. That is B21's dialog half; F1 does not fix it (see
§6) but the editor is what stops users hand-editing in the first place.

Repro of the reachable-preset half, on the unfixed tree:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from deckle.app.views.print_dialog import resolve_profile
p = resolve_profile("no such printer", profile_loader=lambda n: (_ for _ in ()).throw(FileNotFoundError(n)))
print(p.output_face, p.flip_axis, p.reverse_stack)
EOF
```

prints `down long True` — and there is no argument, setting, widget or file
you can supply to make it print `up short False` short of writing the JSON
by hand.

## 2. Current code

`deckle/app/views/print_dialog.py:59-82` — the whole of profile resolution:

```python
def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> PrinterProfile:
    """A profile for ``printer_name``: saved, else a builtin default.

    Until calibration (SS-13) exists, this is the only way a print run
    ever gets a ``PrinterProfile`` without a prior calibration pass.
    ...
    """
    try:
        return profile_loader(printer_name)
    except (FileNotFoundError, OSError):
        pass
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    return next(iter(presets.values()))
```

`deckle/app/views/print_dialog.py:206-239` — the dialog's whole widget set.
There is a printer combo, a checkbox, a signature combo, a status label and
a Print button; nothing about profiles:

```python
        printer_row = QHBoxLayout()
        printer_row.addWidget(QLabel("Printer:", self.widget))
        self.printer_combo = QComboBox(self.widget)
        names = list(printer_names) if printer_names is not None else _available_printer_names()
        self.printer_combo.addItems(names)
        preselected = select_preselected_printer(names, self._profile_loader)
        if preselected is not None:
            self.printer_combo.setCurrentIndex(names.index(preselected))
        printer_row.addWidget(self.printer_combo)
        layout.addLayout(printer_row)

        self.test_first_checkbox = QCheckBox("Test one sheet first", self.widget)
        layout.addWidget(self.test_first_checkbox)
```

`deckle/core/profiles.py:30-52` — the dataclass the editor mirrors:

```python
@dataclass(frozen=True)
class PrinterProfile:
    version: int
    flip_axis: Literal["long", "short"]
    output_face: Literal["up", "down"]
    feed_edge: Literal["top", "bottom"]
    reverse_stack: bool
    imageable_area_pt: tuple[float, float, float, float]
    calibrated_at: str
    calibration_version: int

    back_offset_x_pt: float = 0.0
    back_offset_y_pt: float = 0.0
```

`deckle/core/profiles.py:181-202` — the two presets, in this order:

```python
BUILTIN_PRESETS: dict[str, PrinterProfile] = {
    "generic_face_down_reversed": PrinterProfile(
        version=1,
        flip_axis="long",
        output_face="down",
        feed_edge="top",
        reverse_stack=True,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    ),
    "generic_face_up_in_order": PrinterProfile(
        version=1,
        flip_axis="short",
        output_face="up",
        feed_edge="bottom",
        reverse_stack=False,
        imageable_area_pt=(18.0, 18.0, 18.0, 18.0),
        calibrated_at="",
        calibration_version=0,
    ),
}
```

`deckle/core/profiles.py:83-106` — `save`, the function nothing calls:

```python
    def save(self, name: str) -> None:
        path = _profile_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(asdict(self), indent=2))
```

`deckle/app/views/layout_panel.py:140-162` — the unit conversion the editor
reuses (do **not** add a third copy; see `00-environment.md`):

```python
LENGTH_UNITS: dict[str, float] = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}


def to_points(value: float, unit: str) -> float:
    ...
    return value * LENGTH_UNITS[unit]


def from_points(points: float, unit: str) -> float:
    ...
    return points / LENGTH_UNITS[unit]
```

`deckle/app/views/layout_panel.py:795-799` — where the panel's current unit
lives, and its default:

```python
        self.unit_combo = QComboBox(self.widget)
        self.unit_combo.addItems(["pt", "in", "cm", "mm"])
        self.unit_combo.setCurrentText("in")
        self._unit = "in"
```

### Every call site of the symbols this spec touches

```
$ grep -rn "resolve_profile" deckle/ tests/
deckle/app/views/print_dialog.py:59:def resolve_profile(
deckle/app/views/print_dialog.py:248:    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
deckle/app/views/print_dialog.py:249:        return resolve_profile(printer_name, self._profile_loader, self._builtin_presets)
deckle/app/views/print_dialog.py:266:        profile = self._resolve_profile(printer_name)
deckle/app/views/print_dialog.py:289:        profile = self._resolve_profile(chosen.printer_name)
deckle/app/main.py:76:# same fallback resolve_profile() reaches for when a printer has no saved
deckle/cli.py:420:def _resolve_profile(name: str):
deckle/cli.py:987:        profile = _resolve_profile(args.profile)
```

`deckle/cli.py:_resolve_profile` is a **different function** with a different
contract (it returns `None` and prints an error). Do not unify them here.

```
$ grep -rn "BUILTIN_PRESETS" deckle/ tests/ | cut -d: -f1 | sort -u
deckle/app/main.py
deckle/app/views/print_dialog.py
deckle/cli.py
deckle/core/profiles.py
tests/test_config_store_durability.py
tests/test_preview_paint.py
tests/test_printing.py
tests/test_print_log_failure.py
tests/test_print_session_state_validation.py
tests/test_registration.py
tests/test_view_workers.py
```

`deckle/app/main.py:78` is the one that pins the "first preset" assumption:

```python
DEFAULT_PROFILE = next(iter(BUILTIN_PRESETS.values()))
```

used at `main.py:442` (seeds `LayoutPanel`) and `main.py:523` (seeds
`PreviewView`). Both are B15's territory, not F1's.

### Existing tests that touch this code

- `tests/test_print_dialog.py` — the whole file. `_make_dialog` (line 160)
  injects `profile_loader`, `session_cls`, `backend_cls` and every
  confirmation callable; reuse it.
- `tests/test_print_dialog.py:180-197` — `select_preselected_printer` tests.
- `tests/test_printing.py:131-144` —
  `test_printer_profile_save_and_load_roundtrip`, which shows the
  `XDG_CONFIG_HOME` + `sys.platform` monkeypatch pair that redirects profile
  storage into `tmp_path`.
- `tests/test_ui_surface.py` — asserts the app's widget surface; a new dialog
  may need a row there. Read it before adding one.
- `tests/test_docs_coverage.py` — a new module under `deckle/` needs a
  `docs/api/*.rst` page.

## 3. Change

A new modal dialog, `ProfileEditorDialog`, in a new module
`deckle/app/views/profile_editor.py`, reached from a new button on the print
dialog. It edits exactly the `PrinterProfile` fields and saves under the
selected printer's name.

**Chosen:** one editor dialog owning all eight fields, opened from the print
dialog, seeded from `resolve_profile`. **Rejected:** a preset combo directly
on the print dialog (B16's minimal shape) — it makes the preset a per-print
choice when it is really a per-printer fact, and leaves the back offset and
imageable area still unreachable.

### 1. `deckle/app/views/print_dialog.py` — `resolve_profile` gains a preset name

Change the signature to:

```python
def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
    preset_name: str | None = None,
) -> PrinterProfile:
```

and the fallback body to:

```python
    presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
    if preset_name is not None and preset_name in presets:
        return presets[preset_name]
    return next(iter(presets.values()))
```

`preset_name` is consulted **only** when no saved profile loaded, and an
unknown name falls through to the first preset rather than raising — a stale
name in a combo must not be able to stop a print run. Every existing call
site passes three positional arguments and is unchanged.

Update the docstring's `:returns:` line to read: *"the saved profile; else
the named builtin preset; else the first builtin preset."* Add a
`:param preset_name:` entry: *"which builtin preset to fall back to. The
profile editor passes it so 'load this preset's values' goes through the one
function that already knows how to choose a profile."* Delete the sentence
"Until calibration (SS-13) exists, this is the only way a print run ever gets
a `PrinterProfile` without a prior calibration pass" — after F1 it is not.

### 2. New module `deckle/app/views/profile_editor.py`

Module docstring, in the house voice: state that the profile is the most
expensive data Deckle holds, that this dialog is the first thing in the
program to write one, and that it edits stored numbers only — it never
measures anything (F2/F3 do that).

Qt is imported lazily inside `_qt_widgets()`, mirroring
`print_dialog.py:90-111`. Nothing at module scope imports PySide6.

Pure helper, at the top of the file, above the Qt wiring:

```python
def profile_from_fields(
    *,
    preset: PrinterProfile,
    flip_axis: str,
    output_face: str,
    feed_edge: str,
    reverse_stack: bool,
    back_offset_pt: tuple[float, float],
    imageable_area_pt: tuple[float, float, float, float],
    calibrated_at: str,
) -> PrinterProfile:
    """A ``PrinterProfile`` from edited field values, keeping ``preset``'s
    non-edited fields."""
```

It returns
`dataclasses.replace(preset, version=1, flip_axis=..., output_face=...,
feed_edge=..., reverse_stack=..., imageable_area_pt=...,
back_offset_x_pt=..., back_offset_y_pt=..., calibrated_at=calibrated_at,
calibration_version=0)`.

`calibration_version=0` is the committed value and means **"not produced by
the calibration wizard"**. F2 writes `1`. Record that sentence in the
docstring; it is the only thing that distinguishes a hand-set profile from a
measured one, and F2's spike depends on being able to tell them apart.

### 3. `ProfileEditorDialog.__init__`

```python
class ProfileEditorDialog:
    def __init__(
        self,
        printer_name: str,
        parent=None,
        *,
        unit: str = "in",
        profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
        builtin_presets: dict[str, PrinterProfile] | None = None,
        saver: Callable[[PrinterProfile, str], None] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
```

- `saver` defaults to `lambda profile, name: profile.save(name)`. Injected so
  the headless test can assert on the written JSON without patching a
  method on a frozen dataclass.
- `now` defaults to
  `lambda: datetime.datetime.now().isoformat(timespec="seconds")`. Injected
  so the test's expected JSON is exact.
- `unit` is the panel's current unit; the caller passes
  `layout_panel._unit`. Not read from the panel here — this module must not
  import `layout_panel`'s widget class.

`self.widget` is a `QDialog` (this class is not itself a widget, matching
`PrintDialog`). Window title: `"Printer profile"`.

Widgets, in this order, in a `QFormLayout`, with these **exact** labels:

| Row label | Widget attribute | Type | Contents / range |
|---|---|---|---|
| `Printer:` | `printer_label` | `QLabel` | `printer_name`, or `"(no printer selected)"` when empty |
| `Start from:` | `preset_combo` | `QComboBox` | one item per `BUILTIN_PRESETS` key, in dict order, `addItem(key, key)` |
| `Flip edge:` | `flip_axis_combo` | `QComboBox` | items `"long"`, `"short"` |
| `Sheets land:` | `output_face_combo` | `QComboBox` | items `"up"`, `"down"` |
| `Feed edge:` | `feed_edge_combo` | `QComboBox` | items `"top"`, `"bottom"` |
| *(no label)* | `reverse_stack_check` | `QCheckBox` | text `"Reverse the stack before reloading"` |
| `Back offset X:` | `back_offset_x_spin` | `QDoubleSpinBox` | 3 decimals, range ±`from_points(72.0, unit)`, suffix `f" {unit}"` |
| `Back offset Y:` | `back_offset_y_spin` | `QDoubleSpinBox` | same |
| `Imageable left:` | `imageable_spins["left"]` | `QDoubleSpinBox` | 3 decimals, range `0.0` to `from_points(144.0, unit)` |
| `Imageable top:` | `imageable_spins["top"]` | `QDoubleSpinBox` | same |
| `Imageable right:` | `imageable_spins["right"]` | `QDoubleSpinBox` | same |
| `Imageable bottom:` | `imageable_spins["bottom"]` | `QDoubleSpinBox` | same |
| *(no label)* | `status_label` | `QLabel` | starts empty |
| *(no label)* | `save_button` | `QPushButton` | text `"Save"` |
| *(no label)* | `cancel_button` | `QPushButton` | text `"Cancel"` |

The `imageable_spins` dict is keyed `"left"`, `"top"`, `"right"`, `"bottom"`
— the same order and meaning as `PrinterProfile.imageable_area_pt`, which is
`(left, top, right, bottom)` **margins from the paper edges**, not an
`(x0, y0, x1, y1)` rect. Say so in a comment; `layout_panel.imageable_inset_pt`
(line 165-177) records that reading it as a rect yields a ~600pt nonsense
inset.

Tooltips (exact strings, one per control that is not self-evident):

- `flip_axis_combo`: `"Which edge you turn each sheet about when you reload it. Long-edge flips need the back faces rotated 180 degrees; short-edge flips do not."`
- `output_face_combo`: `"Whether printed sheets land face up or face down in the output tray."`
- `feed_edge_combo`: `"Which edge of the sheet enters the printer first."`
- `reverse_stack_check`: `"Tick this if the printed stack comes out in reverse order, so the back pass must be fed in reverse to match."`
- `back_offset_x_spin` and `back_offset_y_spin`: `"How far to move back-side content so it lands behind the front. This is the correction, not the error: a back sitting 3pt left of where it belongs is corrected with +3. Corrects a constant offset only, not skew or scale."`
- each `imageable_spins` box: `"How far in from that paper edge your printer can actually print. Content outside it is clipped by the hardware, whatever the PDF says."`

### 4. Seeding the form

A method `_load_into_form(self, profile: PrinterProfile) -> None` sets every
control from `profile`, with `blockSignals(True)`/`False` around the whole
body — the panel's `refresh_from_project` bug (B12) is exactly what happens
when that is done per-widget from a hand-maintained list. Convert with
`from_points(value, self._unit)`.

Constructor calls
`self._load_into_form(resolve_profile(printer_name, profile_loader, builtin_presets))`.

`preset_combo.currentIndexChanged` connects to `_on_preset_changed`, which
calls
`resolve_profile(self._printer_name, _no_saved, self._builtin_presets, preset_name=self.preset_combo.currentData())`
where `_no_saved` is a local function that raises `FileNotFoundError` — the
user has explicitly asked for a preset's values, so the saved profile must
not win here — and feeds the result to `_load_into_form`. Then it sets
`status_label` to
`f"Loaded {name} — nothing is saved until you press Save."`.

### 5. Validation and Save

`save_button.clicked` connects to `save`:

```python
    def save(self) -> bool:
        """Write the edited profile. ``True`` when it was written."""
```

Order of checks, each returning `False` after setting `status_label`:

1. **No printer.** `if not self._printer_name:` →
   `"Choose a printer before editing its profile."` The button is also
   disabled at construction in this case; the check is belt and braces
   because the dialog is constructible headlessly.
2. **Imageable area collapses the sheet.** If
   `left + right >= 612.0` or `top + bottom >= 792.0` (US Letter, the
   smallest paper the presets assume) →
   `"Those imageable margins leave no printable area on a Letter sheet."`
   This is the only cross-field rule; every other bound is enforced by the
   spinbox range, which is the house pattern (`layout_panel.py:864, 896, 917,
   943, 1020`) and makes an invalid value unrepresentable rather than
   rejected after the fact.
3. Build the profile with `profile_from_fields(...)`, `to_points`-converting
   the six length spinboxes, with
   `preset=self._builtin_presets_or_default()[self.preset_combo.currentData()]`
   as the base.
4. `try: self._saver(profile, self._printer_name)` /
   `except OSError as exc:` → `log_exception("profile_save_failed", exc,
   printer=self._printer_name)` and status
   `f"Could not save the profile for {self._printer_name!r}: {exc}"`, return
   `False`. Import `log_exception` from `deckle.core.diagnostics`, as
   `print_dialog.py:21` does.
5. On success: `self.saved_profile = profile`, status
   `f"Saved the profile for {self._printer_name!r}."`, `self.widget.accept()`,
   return `True`.

`cancel_button.clicked` connects to `self.widget.reject`.

`self.saved_profile: PrinterProfile | None = None` is set to `None` in
`__init__` so a caller can tell a save from a cancel without reading the
dialog result code.

### 6. `deckle/app/views/print_dialog.py` — the button

After the printer row and before `test_first_checkbox` (so it reads as part
of "which printer"), add:

```python
        self.edit_profile_button = QPushButton("Edit profile...", self.widget)
        self.edit_profile_button.setToolTip(
            "Record how this printer behaves on the reload: which edge you "
            "flip on, which way the stack comes out, and the registration "
            "offset that puts the back behind the front."
        )
        self.edit_profile_button.clicked.connect(self.edit_profile)
        printer_row.addWidget(self.edit_profile_button)
```

`QPushButton` is already unpacked from `_qt_widgets()` at line 189-198.

New method on `PrintDialog`:

```python
    def edit_profile(self) -> None:
        """Open the profile editor for the selected printer.

        The dialog writes the profile itself; nothing is cached here, so the
        next ``start_print`` re-resolves and picks up whatever was saved.
        """
        printer_name = self.printer_combo.currentText()
        editor = self._profile_editor_cls(
            printer_name,
            self.widget,
            profile_loader=self._profile_loader,
            builtin_presets=self._builtin_presets,
        )
        editor.widget.exec()
        if editor.saved_profile is not None:
            self.status_label.setText(f"Saved the profile for {printer_name!r}.")
```

Add a constructor parameter `profile_editor_cls=None` (keyword-only,
documented in the class docstring alongside `session_cls`/`backend_cls`),
stored as `self._profile_editor_cls`, defaulting via a lazy import inside
`__init__`:

```python
        if profile_editor_cls is None:
            from deckle.app.views.profile_editor import ProfileEditorDialog

            profile_editor_cls = ProfileEditorDialog
```

placed beside the existing `backend_cls` lazy import at lines 171-174.

`unit` is deliberately **not** threaded from `MainWindow` into `PrintDialog`
in this spec: `PrintDialog` has no reference to the layout panel, and adding
one to carry a display preference would be a new coupling for a cosmetic
gain. The editor defaults to `"in"`. Record this in the decisions entry.

### 7. Docs

- New file `docs/api/app.views.profile_editor.rst`, copying the shape of
  `docs/api/app.views.print_dialog.rst`.
- Add `app.views.profile_editor` to the toctree in `docs/api/app.rst`, after
  `app.views.print_dialog`.
- `docs/GUIDE.md` §6, "The printer profile": replace the paragraph beginning
  **"The calibration wizard is not built. Until it is, Deckle uses two
  built-in generic presets…"** with text that says the profile is editable
  from the print dialog's **Edit profile...** button, that the two presets
  are starting points, and that a saved profile is keyed by printer name.
  Keep the sentence about the wizard not being built — F2 is still open.
  This closes D3.
- `README.md` line 378, the **Calibration wizard** status row: leave "Not
  built", and add a following sentence: *"The profile it would write can be
  set by hand from the print dialog's Edit profile... button."*

## 4. Tests

New file `tests/test_profile_editor.py`. Module docstring: this is the first
code in Deckle that writes a `PrinterProfile`, so the test asserts on the
**bytes on disk**, not on the dataclass — a profile that round-trips through
`asdict` but lands somewhere the loader does not look is the failure mode
worth catching.

Header boilerplate to copy from `tests/test_print_dialog.py:12-37`:
`os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before any PySide6
import, and the module-scoped `qapp` fixture with
`pytest.importorskip("PySide6")`.

1. `test_the_editor_writes_every_edited_field_to_the_profile_json`
   Setup: `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))` and
   `monkeypatch.setattr("deckle.core.paths.sys.platform", "linux")` (the pair
   from `tests/test_printing.py:132-133`). Construct
   `ProfileEditorDialog("Ink Jet", unit="pt", profile_loader=<raises FileNotFoundError>, now=lambda: "2026-09-05T12:00:00")`.
   Set `flip_axis_combo` to `"short"`, `output_face_combo` to `"up"`,
   `feed_edge_combo` to `"bottom"`, uncheck `reverse_stack_check`, set
   `back_offset_x_spin` to `3.0` and `back_offset_y_spin` to `-1.5`, set the
   four imageable spinboxes to `12, 13, 14, 15`. Call `dialog.save()`.
   Assert: it returned `True`; the file
   `tmp_path / "deckle" / "printer_profiles" / "Ink Jet.json"` exists; its
   parsed JSON equals
   `{"version": 1, "flip_axis": "short", "output_face": "up",
   "feed_edge": "bottom", "reverse_stack": False,
   "imageable_area_pt": [12.0, 13.0, 14.0, 15.0],
   "calibrated_at": "2026-09-05T12:00:00", "calibration_version": 0,
   "back_offset_x_pt": 3.0, "back_offset_y_pt": -1.5}`.
   Unfixed tree: `ModuleNotFoundError: No module named
   'deckle.app.views.profile_editor'` at import.

2. `test_a_saved_profile_reloads_through_printer_profile_load`
   Same setup; after `save()`, `PrinterProfile.load("Ink Jet")` returns a
   profile equal to `dialog.saved_profile`, and `imageable_area_pt` is a
   **tuple**, not a list. Guards the `_profile_path` naming and the loader's
   list→tuple conversion together. Same import failure unfixed.

3. `test_values_are_entered_in_the_panels_unit`
   Construct with `unit="in"`, set `back_offset_x_spin` to `0.5`, save,
   assert the JSON's `back_offset_x_pt` is `36.0`. Fails unfixed on import;
   fails on a naive implementation that stores the displayed number.

4. `test_choosing_a_preset_loads_its_values_without_saving_anything`
   Construct with a `profile_loader` that raises. Set `preset_combo` to
   `"generic_face_up_in_order"`. Assert `flip_axis_combo.currentText() ==
   "short"`, `output_face_combo.currentText() == "up"`,
   `reverse_stack_check.isChecked() is False`, and that no file exists under
   `tmp_path`. **This is the B16 regression test**; unfixed it fails at
   import, and on a wrong implementation that saves on preset change it fails
   on the "no file exists" assertion.

5. `test_a_saved_profile_wins_over_the_preset_when_the_dialog_opens`
   `profile_loader` returns a profile with `flip_axis="short"`,
   `back_offset_x_pt=7.0`; assert the form shows those, not the first
   preset's `long`/`0.0`.

6. `test_imageable_margins_that_leave_no_letter_sheet_are_refused`
   Set `imageable_spins["left"]` and `["right"]` to `320` points each with
   `unit="pt"`. `dialog.save()` returns `False`, `status_label.text()` is
   `"Those imageable margins leave no printable area on a Letter sheet."`,
   and no file was written.

7. `test_a_save_that_cannot_be_written_is_reported_not_raised`
   Inject `saver=<raises OSError("read-only file system")>`. `save()` returns
   `False`, `status_label.text()` starts with `"Could not save the profile
   for 'Ink Jet'"`, and no exception escapes.

8. `test_an_empty_printer_name_disables_save`
   Construct with `""`. `save_button.isEnabled() is False`; `dialog.save()`
   returns `False` with status
   `"Choose a printer before editing its profile."`.

Additions to `tests/test_print_dialog.py`:

9. `test_the_print_dialog_offers_an_edit_profile_button`
   `dialog = _make_dialog()`; assert
   `dialog.edit_profile_button.text() == "Edit profile..."`. Unfixed:
   `AttributeError: 'PrintDialog' object has no attribute
   'edit_profile_button'`.

10. `test_edit_profile_opens_the_editor_for_the_selected_printer`
    Inject a `profile_editor_cls` stub recording its `printer_name` and
    exposing `widget.exec()` and `saved_profile = None`. Select
    `"Printer B"`, call `dialog.edit_profile()`, assert the stub was
    constructed with `"Printer B"`. Unfixed:
    `TypeError: __init__() got an unexpected keyword argument
    'profile_editor_cls'`.

11. `test_a_saved_profile_is_reported_in_the_status_line`
    Stub returns a `saved_profile`; after `edit_profile()`,
    `dialog.status_label.text() == "Saved the profile for 'Printer A'."`

Additions to `tests/test_printing.py` (or a new
`tests/test_profile_resolution.py` if that file is already long):

12. `test_resolve_profile_honours_a_named_preset_when_nothing_is_saved`
    `resolve_profile("nobody", <raises FileNotFoundError>, None,
    "generic_face_up_in_order").output_face == "up"`. Unfixed:
    `TypeError: resolve_profile() takes from 1 to 3 positional arguments but
    4 were given`.

13. `test_an_unknown_preset_name_falls_back_rather_than_raising`
    `preset_name="typo"` returns the first preset. Guards against a stale
    combo entry stopping a print run.

14. `test_a_saved_profile_still_wins_over_a_named_preset`
    A loader that succeeds beats `preset_name`.

## 5. Acceptance

| Check | Command |
|---|---|
| Editor tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_profile_editor.py` |
| Print-dialog tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_print_dialog.py` |
| The B16 regression specifically | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -k choosing_a_preset_loads_its_values` |
| Full suite still green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| Something in `deckle/` now calls `PrinterProfile.save` | `grep -rn "\.save(" deckle/app/views/profile_editor.py` |
| The editor imports no Qt at module scope | `.venv/bin/python -c "import sys, deckle.app.views.profile_editor; assert 'PySide6.QtWidgets' not in sys.modules"` |
| The core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_core_purity.py` |
| The new module is documented | `test -f docs/api/app.views.profile_editor.rst && grep -q "app.views.profile_editor" docs/api/app.rst` |
| Docs coverage test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_docs_coverage.py` |
| No third unit table was added | `test "$(grep -rlE '^LENGTH_UNITS\|^_UNIT_TO_PT' deckle/ \| wc -l)" = "2"` |
| GUIDE no longer claims the presets are unreachable | `grep -c "Edit profile" docs/GUIDE.md` |
| [HUMAN] The dialog is usable | Launch `.venv/bin/python -m deckle`, import any PDF, press Print, press **Edit profile...**, change the preset, press Save, reopen — the values persist. |

Greps run against the current tree, for the implementer to diff against:

```
$ grep -rn "\.save(" deckle/ | grep -i profile
(no output — exit 1)
$ grep -rlE '^LENGTH_UNITS|^_UNIT_TO_PT' deckle/
deckle/cli.py
deckle/app/views/layout_panel.py
```

(The pattern is anchored to the *definitions*, so a module that merely
imports `to_points`/`from_points` does not raise the count.)

## 6. Out of scope

- **B15 / N2** — pre-filling the imageable area from the driver's
  `pageLayout().paintRectPixels()`. F1 gives the field a home; N2 gives it a
  measured default. The `DEFAULT_PROFILE` constant at `main.py:78` and the
  preview's red guide stay exactly as they are.
- **B21** — `PrintDialog` catching only `OSError` while `StoredValueError`
  is a `ValueError`. A corrupt profile still crashes `start_print` after F1.
  Fix it in B21's own spec; do not widen the `except` here, because widening
  it silently is precisely how a corrupt calibration becomes an unnoticed
  generic preset.
- **B34** — `_profile_path` uses the raw printer name, so a printer called
  `\\server\printer` writes to a UNC path. F1 makes that reachable from the
  GUI for the first time; it is still B34's fix.
- **F2** — measuring any of these numbers. F1 only stores what a human
  types.
- **F3** — the registration target that produces the back-offset numbers.
- **B6** — whether the backend draws 1:1 or scales into the imageable area.
- Threading the layout panel's unit into the print dialog.

## 7. decisions.md entry

```
## 2026-09-05 — Nothing in the program had ever written a printer profile
- Symptom: `PrinterProfile.save` was complete, atomic, and called by three tests and no shipped code. A user whose printer stacks face-up got `generic_face_down_reversed`'s reload instruction with no in-app way to say otherwise, and the GUIDE's "two built-in generic presets" described one reachable preset. The measured back-offset the GUIDE tells people to record had nowhere to go but a JSON file in a directory the app never names.
- Fix: `ProfileEditorDialog` in `deckle/app/views/profile_editor.py`, opened from a new "Edit profile..." button on the print dialog. It mirrors the eight `PrinterProfile` fields, enters lengths in the panel's unit, and writes through `PrinterProfile.save`. `resolve_profile` gained a `preset_name` fallback so "load this preset's values" goes through the one function that already knows how to choose a profile, rather than a second lookup in the dialog. `calibration_version=0` is the committed marker for "typed, not measured"; the wizard will write 1.
- Surfaces: This is the shape of B16, D3 and half of F3's blocker at once — a complete, tested, documented model with no writer. Grep for `def save` with no caller before believing a persistence feature exists.
- Watch: The dialog does not validate against the project's paper, because it does not have one; the one cross-field rule is a Letter-sized sanity check. A per-orientation imageable area (v2 open question 3, roadmap F12) would change this form's shape, not just its values.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` for anything scripted, and `QT_QPA_PLATFORM=offscreen`
  plus the module-scoped `qapp` fixture for tests. `QT_QPA_PLATFORM=offscreen`
  with a real `show()` hard-kills the process (exit 127) — see the hardening
  plan's H-3. Never call `show()` in a test; construct and drive.
- **`PrinterProfile` is frozen.** Build a new one with
  `dataclasses.replace`; do not try to assign fields.
- **`imageable_area_pt` is four margins, not a rect.** `(left, top, right,
  bottom)` insets from the paper edges. `layout_panel.imageable_inset_pt`
  records what reading it as `(x0, y0, x1, y1)` produces.
- **`back_offset_*` are the correction, not the error**, and they are signed.
  A spinbox with a `0.0` minimum silently makes half the answer space
  unreachable — the same defect `_parse_offset_pair` at `cli.py:369` exists
  to avoid.
- **Block signals once, around the whole form load.** B12 is the bug where a
  hand-maintained per-widget block list drifted and refresh handlers wrote
  back into the model.
- **`tests/test_core_purity.py` and `tests/test_backend.py` assert Qt is not
  in `sys.modules`** at collection time. Import PySide6 lazily, inside
  functions, and inside the test fixture body — not at module scope. Follow
  the comment at `tests/test_print_dialog.py:25-28`.
- **`tests/test_docs_coverage.py` fails on a new module with no `.rst`.**
- **If B16 was already implemented**, it will have added a preset control to
  `print_dialog.py`. Remove it in the same commit. Two controls for one
  decision is exactly what the layout panel's mode tabs were built to stop
  (`layout_panel.py:754-759`).
- **Profile storage is redirected in tests by two monkeypatches together** —
  `XDG_CONFIG_HOME` *and* `deckle.core.paths.sys.platform`. Setting only the
  environment variable does nothing on a Windows or macOS runner.
- The pre-commit hook refuses a code commit that does not also touch
  `docs/decisions.md`, and one whose added lines contain `<FILL-IN>`.
