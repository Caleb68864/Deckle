# B16 — Let the print dialog choose which printer profile to use

**Roadmap item:** `docs/ROADMAP.md` B16
**Depends on:** —
**Blocks:** —
**Superseded by:** F1 (profile editor). See §6.
**Size:** S
**Decision needed first:** none

---

## 1. Context

`deckle/core/profiles.py` ships two built-in presets, covering the two reload
behaviours in the verified back-pass ordering table:

- `generic_face_down_reversed` — the printer stacks face down and reverses
  the stack.
- `generic_face_up_in_order` — face up, in order.

Which one a printer matches decides the **reload instruction** shown between
the front pass and the back pass, and getting it wrong means every back side
prints on the wrong sheet or upside down, for the whole stack, with nothing on
screen to suggest it.

The desktop app only ever resolves the **first** one. `resolve_profile` tries
`PrinterProfile.load(printer_name)` and, on `FileNotFoundError`/`OSError`,
returns `next(iter(presets.values()))` — dict insertion order, which is
`generic_face_down_reversed`. Nothing in the print dialog lets a user say
otherwise, and nothing in `deckle/` ever calls `PrinterProfile.save`, so the
only way to get the other preset is to write JSON into the config directory by
hand.

The exact user action and the exact wrong result: an owner of a face-up
printer opens the print dialog, prints the front pass, and is told to reload
the stack the way a face-down printer wants. They follow the instruction, and
every back side lands on the wrong sheet. GUIDE §6 says Deckle has "two
built-in generic presets" (roadmap D3); the app resolves one.

The CLI already has this: `deckle export --profile generic_face_up_in_order`
(`cli.py:439`, `cli.py:983`, `cli.py:1302`). The desktop app is the front end
without the control.

**This is the narrow fix ahead of F1.** F1 adds a real editor — back offset
X/Y, imageable area, save under the printer's name — and supersedes the combo
this spec adds with a picker plus an Edit button. What this spec commits to is
the *selection*, so a face-up printer owner can print a correct book today.

## 2. Current code

`deckle/app/views/print_dialog.py:59-83` — the resolver:

```python
def resolve_profile(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> PrinterProfile:
    """A profile for ``printer_name``: saved, else a builtin default.

    Until calibration (SS-13) exists, this is the only way a print run
    ever gets a ``PrinterProfile`` without a prior calibration pass.

    :param printer_name: the printer to load a profile for.
    :param profile_loader: how to load a saved profile.
    :param builtin_presets: the fallback presets, or ``None`` for
        ``BUILTIN_PRESETS``.
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

`next(iter(presets.values()))` at line 82 is the whole of the defect.

`deckle/app/views/print_dialog.py:248-249` — the dialog's wrapper:

```python
    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
        return resolve_profile(printer_name, self._profile_loader, self._builtin_presets)
```

`deckle/app/views/print_dialog.py:265-266` and `:289` — the two callers:

```python
        printer_name = self.printer_combo.currentText()
        profile = self._resolve_profile(printer_name)
```

```python
        profile = self._resolve_profile(chosen.printer_name)
```

`deckle/app/views/print_dialog.py:206-215` — the printer row, which is the
model for the new row:

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
```

`deckle/core/profiles.py:181-202` — the presets, and their order:

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

`deckle/core/profiles.py:108-160` — `PrinterProfile.load` raises `OSError` for
a missing file and `deckle.core.schema.StoredValueError` (a `ValueError`) for
a stored value this build cannot honour. `resolve_profile` catches only the
first pair; the second is B21's, not this spec's.

### How `resolve_profile` is tested today

**It is not.** `grep -rn "resolve_profile" --include="*.py" deckle/ tests/`
returns only:

| Site | |
|---|---|
| `deckle/app/views/print_dialog.py:59` | definition |
| `deckle/app/views/print_dialog.py:248-249` | the dialog's wrapper |
| `deckle/app/views/print_dialog.py:266, 289` | its two callers |
| `deckle/cli.py:420, 987` | a *different* function, `_resolve_profile`, in the CLI |

No test file imports it. It is exercised only indirectly: every
`_make_dialog(...)` in `tests/test_print_dialog.py` (line 165) injects

```python
        profile_loader=lambda name: (_ for _ in ()).throw(FileNotFoundError(name)),
```

so every dialog test takes the fallback branch and lands on
`generic_face_down_reversed` without ever asserting which preset it got. The
function that decides how the paper is reloaded has zero direct coverage.
That is worth fixing here regardless of the combo.

The related pure helper `select_preselected_printer` **is** tested, at
`tests/test_print_dialog.py:180-197` — two tests. Follow their shape.

### Existing tests that touch this code

- `tests/test_print_dialog.py::test_dialog_lists_printers_and_preselects_saved_profile`
  (line 200) — asserts the printer combo's items and current text. The new row
  must not disturb it.
- Every other test in `tests/test_print_dialog.py`, via `_make_dialog`.
- `tests/test_ui_surface.py:341-420` — constructs a `PrintDialog` for the
  signature selector tests; it passes `printer_names` and a raising
  `profile_loader` too.
- `tests/test_printing.py:102-110` — asserts `len(BUILTIN_PRESETS) >= 2` and
  properties of every preset.
- `tests/test_registration.py:62` — iterates `BUILTIN_PRESETS.items()`.

## 3. Change

Add a **Profile** combo below the Printer combo, listing every key of
`BUILTIN_PRESETS` plus, when the selected printer has one, a `Saved for
<printer>` entry. Default to the saved entry when it exists, else to the first
built-in — which preserves today's behaviour for every printer that has a
saved profile and for anyone who does not touch the combo.

Two designs were possible. **Chosen:** a combo whose items carry the
`PrinterProfile` as item data, so `start_print` reads `currentData()` and
`resolve_profile` keeps its current signature. **Rejected:** a combo of names
plus a lookup in `_resolve_profile` — it would need the "saved" case to have a
reserved name that could collide with a preset key.

### Numbered edits

1. **`print_dialog.py`, module level, after `resolve_profile`** — the pure
   helper that builds the list, Qt-free and directly testable the way
   `select_preselected_printer` is:

   ```python
   SAVED_PROFILE_LABEL = "Saved for this printer"
   """The combo entry naming a printer's own calibrated profile.

   Not the printer's name: the combo already sits under a Printer combo, and
   repeating the name in the row below reads as a second printer choice.
   """


   def profile_choices(
       printer_name: str,
       profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
       builtin_presets: dict[str, PrinterProfile] | None = None,
   ) -> tuple[list[tuple[str, PrinterProfile]], int]:
       """Every profile offerable for ``printer_name``, and which to select.

       The desktop app resolved only the FIRST built-in preset, so an owner
       of a face-up printer was given the face-down reload instruction with
       no way in the app to say otherwise -- and every back side landed on
       the wrong sheet. The CLI has had `--profile` since the beginning.

       Pure and Qt-free, so the ordering and default are unit-testable
       without a dialog -- the same reason
       :func:`select_preselected_printer` is.

       :param printer_name: the printer the profiles are for.
       :param profile_loader: how to load a saved profile. Raising means
           "no saved profile", which is the normal case.
       :param builtin_presets: the presets to offer, or ``None`` for
           ``BUILTIN_PRESETS``.
       :returns: ``(choices, default_index)`` -- ``(label, profile)`` pairs
           in offer order, and which one to select. The saved profile comes
           FIRST and is the default when it exists, because a calibrated
           answer outranks a guess; otherwise the built-ins are offered in
           ``BUILTIN_PRESETS`` order and the first is the default, which is
           exactly what `resolve_profile` returns today.
       """
       presets = builtin_presets if builtin_presets is not None else BUILTIN_PRESETS
       choices = [(name, profile) for name, profile in presets.items()]
       try:
           saved = profile_loader(printer_name)
       except (FileNotFoundError, OSError) as exc:
           log_exception("printer_profile_unavailable", exc, printer=printer_name)
           return choices, 0
       return [(SAVED_PROFILE_LABEL, saved), *choices], 0
   ```

   `log_exception` is already imported at `print_dialog.py:21`.

2. **`print_dialog.py:215`, in `__init__`, immediately after
   `layout.addLayout(printer_row)`** — the new row:

   ```python
        # Which reload behaviour the printer has. Getting this wrong means
        # every back side prints on the wrong sheet, for the whole stack,
        # with nothing on screen to suggest it -- and until now the app
        # resolved only the first built-in preset while the GUIDE promised
        # two. F1 replaces this with a picker plus an editor.
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Printer profile:", self.widget))
        self.profile_combo = QComboBox(self.widget)
        self.profile_combo.setToolTip(
            "How your printer stacks paper, which decides how Deckle tells "
            "you to reload it between the front pass and the back pass.\n\n"
            "generic_face_down_reversed: pages come out face down and the "
            "stack ends up reversed. The common case for a laser printer.\n"
            "generic_face_up_in_order: pages come out face up, in order.\n\n"
            "If the back sides come out upside down or on the wrong sheets, "
            "this is the setting to change."
        )
        self._populate_profile_combo(self.printer_combo.currentText())
        self.printer_combo.currentTextChanged.connect(self._populate_profile_combo)
        profile_row.addWidget(self.profile_combo)
        layout.addLayout(profile_row)
   ```

3. **`print_dialog.py`, new method** placed in the
   `# -- printer / profile helpers ---` section, above `_resolve_profile`:

   ```python
    def _populate_profile_combo(self, printer_name: str) -> None:
        """Refill the profile combo for ``printer_name``.

        Re-run whenever the printer changes, because "saved for this
        printer" is a different file for each one.

        :param printer_name: the printer now selected.
        :returns: nothing.
        """
        choices, default_index = profile_choices(
            printer_name, self._profile_loader, self._builtin_presets
        )
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for label, profile in choices:
                self.profile_combo.addItem(label, profile)
            self.profile_combo.setCurrentIndex(default_index)
        finally:
            self.profile_combo.blockSignals(False)
   ```

4. **`print_dialog.py:248-249`, `_resolve_profile`** — read the combo when it
   has an answer, fall back to the resolver otherwise:

   ```python
    def _resolve_profile(self, printer_name: str) -> PrinterProfile:
        """The profile to print ``printer_name`` with.

        The combo wins when it has a selection, which is every case in the
        running app. `resolve_profile` remains the fallback for a resume
        offered for a printer that is not the one currently selected --
        the combo describes the selected printer, and a resumed run names
        its own.

        :param printer_name: the printer to print to.
        :returns: the profile.
        """
        chosen = self.profile_combo.currentData()
        if chosen is not None and printer_name == self.printer_combo.currentText():
            return chosen
        return resolve_profile(printer_name, self._profile_loader, self._builtin_presets)
   ```

   This keeps `_offer_resume`'s call at line 289 correct: a resumed session
   names the printer it started on, which may not be the one selected, and in
   that case the saved-or-first-builtin answer is the right one.

5. **`print_dialog.py:59-83`, `resolve_profile`** — unchanged in behaviour,
   but its docstring gains one sentence so the next reader knows the combo
   exists:

   ```
       :returns: the saved profile, else the first builtin preset. Never
           raises for a missing profile -- a printer with no calibration is
           the normal case, not an error. The print dialog's profile combo
           overrides this for the currently selected printer; this is the
           answer for anything that has no combo (a resumed run naming a
           different printer, and the preview's seed profile).
   ```

6. **`print_dialog.py:150-152`, the `:ivar:` block** — add:

   ```
    :ivar profile_combo: the printer-profile picker. Items carry the
        ``PrinterProfile`` as item data.
   ```

7. **`docs/GUIDE.md` §6** — no change in this spec. D3 ("two built-in generic
   presets" — the app only resolves one) becomes true as a consequence, but
   the GUIDE pass is roadmap §6 item 10, done per milestone.

8. **`docs/api/`** — no new module. No change.

### Signatures

```python
SAVED_PROFILE_LABEL: str = "Saved for this printer"

def profile_choices(
    printer_name: str,
    profile_loader: Callable[[str], PrinterProfile] = PrinterProfile.load,
    builtin_presets: dict[str, PrinterProfile] | None = None,
) -> tuple[list[tuple[str, PrinterProfile]], int]
```

`PrintDialog` gains `self.profile_combo` and `_populate_profile_combo`.
`resolve_profile`'s signature is unchanged.

## 4. Tests

`PrintDialog` constructs fine under pytest. Reuse `tests/test_print_dialog.py`'s
module-scoped `qapp` fixture, its `_profile(**overrides)` factory (line 60)
and `_make_dialog(**kwargs)` (line 160). The file sets
`QT_QPA_PLATFORM=offscreen` at line 19.

Add a new section headed `# -- profile selection ---` after the existing
`# -- printer listing / preselection ---` section.

### `test_profile_choices_offers_every_builtin_preset`

```python
def test_profile_choices_offers_every_builtin_preset():
    """The app resolved only the first preset while the GUIDE promised two."""
    from deckle.core.profiles import BUILTIN_PRESETS
    from deckle.app.views.print_dialog import profile_choices

    choices, default = profile_choices(
        "Printer A", lambda name: (_ for _ in ()).throw(FileNotFoundError(name))
    )

    assert [label for label, _p in choices] == list(BUILTIN_PRESETS)
    assert len(choices) >= 2
    assert default == 0
```

Assertion in words: with no saved profile, both built-ins are offered in
`BUILTIN_PRESETS` order and the first is the default — today's behaviour, now
selectable.
Expected failure on the unfixed tree: `ImportError: cannot import name
'profile_choices' from 'deckle.app.views.print_dialog'`.

### `test_profile_choices_puts_a_saved_profile_first_and_defaults_to_it`

```python
def test_profile_choices_puts_a_saved_profile_first_and_defaults_to_it():
    """A calibrated answer outranks a guess."""
    from deckle.app.views.print_dialog import SAVED_PROFILE_LABEL, profile_choices

    saved = _profile(flip_axis="short")
    choices, default = profile_choices("Printer A", lambda name: saved)

    assert choices[0][0] == SAVED_PROFILE_LABEL
    assert choices[0][1] is saved
    assert default == 0
```

Expected failure on the unfixed tree: `ImportError`.

### `test_the_dialog_offers_both_presets_and_prints_with_the_chosen_one`

```python
def test_the_dialog_offers_both_presets_and_prints_with_the_chosen_one():
    """B16: a face-up printer owner got the face-down reload instruction
    with no in-app way to say otherwise, and every back side landed on the
    wrong sheet."""
    from deckle.core.profiles import BUILTIN_PRESETS

    dialog = _make_dialog()
    labels = [
        dialog.profile_combo.itemText(i) for i in range(dialog.profile_combo.count())
    ]
    assert labels == list(BUILTIN_PRESETS)

    dialog.profile_combo.setCurrentIndex(labels.index("generic_face_up_in_order"))
    dialog.start_print()

    assert dialog._session.profile is BUILTIN_PRESETS["generic_face_up_in_order"]
```

Setup: the standard `_make_dialog`, whose `profile_loader` always raises, so
there is no saved entry.
Assertion in words: both presets are listed, and choosing the second one is
what the `PrintSession` is constructed with.
Expected failure on the unfixed tree: `AttributeError: 'PrintDialog' object
has no attribute 'profile_combo'`.

### `test_the_default_profile_is_unchanged_when_nobody_touches_the_combo`

```python
def test_the_default_profile_is_unchanged_when_nobody_touches_the_combo():
    """The regression guard: adding a control must not change what a user
    who ignores it gets."""
    from deckle.core.profiles import BUILTIN_PRESETS

    dialog = _make_dialog()
    dialog.start_print()

    assert dialog._session.profile is next(iter(BUILTIN_PRESETS.values()))
```

On the unfixed tree this **passes**; it is the guard that stops the combo from
quietly changing the default. Keep it.

### `test_a_saved_profile_is_offered_and_is_the_default`

```python
def test_a_saved_profile_is_offered_and_is_the_default():
    from deckle.app.views.print_dialog import SAVED_PROFILE_LABEL

    saved = _profile(flip_axis="short", calibrated_at="2026-02-02T00:00:00")

    def loader(name):
        if name == "Printer B":
            return saved
        raise FileNotFoundError(name)

    dialog = _make_dialog(profile_loader=loader)
    # `select_preselected_printer` already preselects Printer B.
    assert dialog.printer_combo.currentText() == "Printer B"
    assert dialog.profile_combo.currentText() == SAVED_PROFILE_LABEL

    dialog.start_print()
    assert dialog._session.profile is saved
```

Expected failure on the unfixed tree: `AttributeError: 'PrintDialog' object
has no attribute 'profile_combo'`.

### `test_switching_printers_refills_the_profile_combo`

```python
def test_switching_printers_refills_the_profile_combo():
    """"Saved for this printer" is a different file per printer."""
    from deckle.app.views.print_dialog import SAVED_PROFILE_LABEL

    saved = _profile(flip_axis="short")

    def loader(name):
        if name == "Printer B":
            return saved
        raise FileNotFoundError(name)

    dialog = _make_dialog(profile_loader=loader)
    assert dialog.profile_combo.currentText() == SAVED_PROFILE_LABEL

    dialog.printer_combo.setCurrentText("Printer A")

    labels = [
        dialog.profile_combo.itemText(i) for i in range(dialog.profile_combo.count())
    ]
    assert SAVED_PROFILE_LABEL not in labels
```

Expected failure on the unfixed tree: `AttributeError`.

### `test_resolve_profile_returns_the_saved_one_when_there_is_one`

The direct coverage `resolve_profile` has never had. Two tests, matching the
shape of the `select_preselected_printer` pair at line 180:

```python
def test_resolve_profile_returns_the_saved_one_when_there_is_one():
    from deckle.app.views.print_dialog import resolve_profile

    saved = _profile()
    assert resolve_profile("Printer A", lambda name: saved) is saved


def test_resolve_profile_falls_back_to_the_first_builtin():
    """Documented, because the fallback is what B16 is about: it is the
    first preset in BUILTIN_PRESETS order, not a choice."""
    from deckle.core.profiles import BUILTIN_PRESETS
    from deckle.app.views.print_dialog import resolve_profile

    def loader(name):
        raise FileNotFoundError(name)

    assert resolve_profile("Printer A", loader) is next(iter(BUILTIN_PRESETS.values()))
```

Both **pass** on the unfixed tree. They are the missing regression floor under
a function that decides how paper gets reloaded, and they must be written even
though they are green — the acceptance table below asserts they exist.

### Existing test to check, not change

`tests/test_print_dialog.py::test_dialog_lists_printers_and_preselects_saved_profile`
(line 200) asserts the *printer* combo's contents. Confirm it still passes;
the new row is a sibling layout, not a change to that one.

## 5. Acceptance

Run from the repository root.

| Check | Command |
|---|---|
| The new profile tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py -k "profile_choices or profile_combo or chosen_one or saved_profile_is_offered or switching_printers or resolve_profile"` |
| The whole print-dialog file passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py` |
| The other dialog consumers still pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_ui_surface.py tests/test_printing.py tests/test_registration.py` |
| Full suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` (expect only the two known R0.3/R0.4 failures) |
| `resolve_profile` now has direct coverage | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_print_dialog.py -k resolve_profile` exits 0 (it currently exits **5**, "no tests ran", which is the proof it had none) |
| The combo exists and carries profiles | `grep -q "self.profile_combo.addItem(label, profile)" deckle/app/views/print_dialog.py` |
| The dialog no longer hard-codes one preset for the selected printer | `grep -n "currentData()" deckle/app/views/print_dialog.py` returns a line inside `_resolve_profile` |
| [HUMAN] The reload instruction changes with the profile | Open the print dialog, print a 2-sheet job with `generic_face_down_reversed`, note the reload text between passes; repeat with `generic_face_up_in_order`. The two instructions must differ, and the face-up one must match what the printer actually does. |

## 6. Out of scope

- **F1 — the profile editor.** This spec supersedes nothing and is
  superseded by F1, which adds: editing back offset X/Y and the imageable
  area, `PrinterProfile.save` under the printer's name (nothing in `deckle/`
  calls it today), and an Edit button beside this combo. When F1 lands, this
  combo stays and gains a neighbour; the `profile_choices` helper is the seam
  it builds on.
- **B15 / N2 — the preview's red imageable-area guide** still uses
  `main.py:78`'s `DEFAULT_PROFILE`. Do not push the chosen profile to
  `PreviewView` or `LayoutPanel` here; that is B15's, and it needs the
  driver's printable rect (N2) to be honest.
- **B21** — `resolve_profile` catches only `FileNotFoundError`/`OSError`, so
  a *corrupt* saved profile raises `StoredValueError` straight out of the
  dialog while the CLI reports it cleanly. Same function, different bug. Do
  not widen the `except` here.
- **B6** — whether printing draws 1:1 or scales into the imageable area.
- **F2/F3 — the calibration wizard and registration target.**
- Do not add a "Save this profile" button; that is F1's, and it needs the
  editor to have anything to save.

## 7. decisions.md entry

```
## 2026-09-05 — The print dialog resolved one built-in preset and offered no choice
- Symptom: `resolve_profile` falls back to `next(iter(BUILTIN_PRESETS.values()))`, which is `generic_face_down_reversed`, and nothing in the app let a user pick the other one. An owner of a face-up printer therefore got the face-down reload instruction between passes, followed it, and every back side landed on the wrong sheet — for the whole stack, with nothing on screen to suggest it. The CLI has had `--profile` since the beginning; GUIDE §6 promised "two built-in generic presets".
- Fix: A "Printer profile:" combo below the printer combo, filled by a new pure `profile_choices(printer_name, loader, presets) -> (choices, default_index)` helper — every `BUILTIN_PRESETS` key, plus a "Saved for this printer" entry first when the printer has one. Items carry the `PrinterProfile` as item data; `_resolve_profile` reads `currentData()` for the selected printer and falls back to `resolve_profile` for a resumed run naming a different one. The default is unchanged for anyone who ignores the combo.
- Surfaces: `resolve_profile` had **no direct test** — every dialog test injected a raising `profile_loader` and so exercised the fallback branch without asserting which preset came out. A function that decides how paper is reloaded had zero coverage; it now has two tests that were green when written, deliberately.
- Watch: F1 supersedes this with an editor. The combo is the selection half only; nothing in `deckle/` calls `PrinterProfile.save`, so the only writer of a profile today is still a human with a text editor.
- Commit: <fill in>
```

## 8. Traps

- **`_offer_resume` resolves a profile for `chosen.printer_name`**
  (`print_dialog.py:289`), which is whatever printer the interrupted run
  started on — not necessarily the one in the combo. The `printer_name ==
  self.printer_combo.currentText()` condition in `_resolve_profile` is what
  keeps that correct; do not drop it.
- **`_offer_resume` runs from `__init__`** (line 241-244), so the profile
  combo must be built *before* the resumable check at the bottom of
  `__init__`. Step 2 places it at line 215, which is well before; do not move
  it lower.
- **`profile_combo.clear()` fires `currentTextChanged`/`currentIndexChanged`.**
  `_populate_profile_combo` blocks signals around the refill for that reason.
  Nothing is connected to the profile combo today, but the block is what
  makes it safe to connect one later.
- **`_make_dialog`'s default `profile_loader` raises for every printer**, so
  most existing tests will see exactly the two built-ins and no saved entry.
  A test that wants a saved entry must pass its own loader, as
  `test_dialog_lists_printers_and_preselects_saved_profile` already does.
- **`tests/test_print_dialog.py` imports PySide6 only inside the `qapp`
  fixture**, on purpose — `tests/test_core_purity.py` and
  `tests/test_backend.py` assert Qt is not loaded in the same session. Keep
  every new import inside a function.
- **`BUILTIN_PRESETS` is ordered by dict insertion**, and `test_printing.py:102`
  asserts only `len(...) >= 2`. If a third preset is added, the combo grows
  automatically and `test_profile_choices_offers_every_builtin_preset` still
  holds — it compares against `list(BUILTIN_PRESETS)`, not a literal.
- **`python -m deckle` launches the GUI and blocks.** The `[HUMAN]` row is
  the only place a real window appears.
- **The pre-commit hook** refuses a code commit that does not also change
  `docs/decisions.md`.
</content>
