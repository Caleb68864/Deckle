# F11 — Let a custom paper size be typed in the app

**Roadmap item:** `docs/ROADMAP.md` F11
**Depends on:** —
**Blocks:** —
**Size:** S
**Decision needed first:** none.

---

## 1. Context

The CLI accepts any sheet size: `--paper 500x700pt`, `--paper 8.5x11in`. The
desktop app offers five presets and no way to type one. A custom size appears
in the combo **only when the project already carries it** — imposed from the
CLI, or opened from a `.deckle` someone else made.

Competitive gaps, under *"Custom paper sizes — **not a gap**"*:

> Bookbinder JS has a CUSTOM paper entry. So does Deckle: `--paper` accepts
> `WxH[unit]` as well as presets (`deckle/cli.py`). The GUI's list is shorter
> (Letter, A4, Legal, A3, Tabloid) — **if a custom entry is missing there,
> that is a small UI gap, not a capability gap.**

It is missing. The concrete failure: a binder with 200 × 280 mm stock, or
half-Letter cut down, or A5, opens the app, finds five presets, and has to
leave the GUI, run `deckle impose book.pdf -o book.deckle --paper 200x280mm`,
and open the result — at which point the combo *does* show
`Custom (567 x 794pt)`, because the display path already exists. The only
missing piece is a way to type the numbers.

Repro:

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from deckle.app.views.layout_panel import PAPER_PRESETS
print([name for name, _ in PAPER_PRESETS])"
['Letter', 'A4', 'Legal', 'A3', 'Tabloid']
```

No sixth entry, and nothing in `layout_panel.py` opens a dialog.

## 2. Current code

`deckle/app/views/layout_panel.py:203-209` — the five presets:

```python
PAPER_PRESETS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("Letter", (612.0, 792.0)),
    ("A4", (595.28, 841.89)),
    ("Legal", (612.0, 1008.0)),
    ("A3", (841.89, 1190.55)),
    ("Tabloid", (792.0, 1224.0)),
)
```

`deckle/app/views/layout_panel.py:225-237` — how a size is matched:

```python
def preset_name_for(paper: tuple[float, float]) -> str | None:
    """The preset whose dimensions match ``paper`` in either orientation.

    :param paper: ``(width, height)`` in points.
    :returns: the preset's name, or ``None`` for a custom size -- a project
        imposed from the CLI with ``--paper 500x700pt`` is legitimate and
        must not be silently snapped to the nearest preset.
    """
    upright = tuple(sorted(paper))
    for name, dimensions in PAPER_PRESETS:
        if tuple(sorted(dimensions)) == upright:
            return name
    return None
```

`deckle/app/views/layout_panel.py:1484-1522` — **the display path F11
reuses**, in full:

```python
    def _sync_paper_choices(self, paper: tuple[float, float]) -> str:
        """Make sure the combo can name ``paper``, and say what to select.

        A custom size -- from the CLI's ``--paper``, or an older project --
        matches no preset, so it gets an entry of its own naming its
        dimensions. Offering it beats snapping it to the nearest preset,
        which would silently resize the user's book.

        This was worked out once, in the constructor, and never again.
        ``refresh_from_project`` therefore left the combo showing the
        PREVIOUS job's paper when the newly opened one was custom: the
        panel said "A4" over a 500x700 sheet. ...
        """
        preset = preset_name_for(paper)
        wanted = None
        if preset is None:
            width, height = sorted(paper)
            wanted = f"Custom ({width:.0f} x {height:.0f}pt)"

        # Drop a custom entry that no longer describes anything, so a
        # project opened after it does not inherit a stale size.
        if self._custom_paper_label is not None and self._custom_paper_label != wanted:
            index = self._paper_names.index(self._custom_paper_label)
            self.paper_combo.removeItem(index)
            self._paper_names.pop(index)
            self._custom_paper_label = None

        if wanted is not None and self._custom_paper_label is None:
            self.paper_combo.addItem(wanted)
            self._paper_names.append(wanted)
            self._custom_paper_label = wanted

        return wanted if wanted is not None else preset
```

`deckle/app/views/layout_panel.py:1524-1546`:

```python
    def _current_paper_pt(self) -> tuple[float, float]:
        """The paper dimensions the combos currently describe."""
        name = self.paper_combo.currentText()
        for preset_name, dimensions in PAPER_PRESETS:
            if preset_name == name:
                return dimensions
        # The custom entry: keep whatever the project already has.
        return self.state.project.layout.paper

    def _on_paper_changed(self, _name: str) -> None:
        """Apply a new sheet size, preserving the chosen orientation.

        :returns: nothing.
        """
        landscape = self.orientation_combo.currentText() == "Landscape"
        plan = apply_layout_change(
            self.state,
            lambda project: set_paper(
                project, self._current_paper_pt(), landscape=landscape
            ),
        )
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)
```

`deckle/app/views/layout_panel.py:805-814` — construction, which is where
the sentinel is added:

```python
        self.paper_combo = QComboBox(self.widget)
        for name, _dimensions in PAPER_PRESETS:
            self.paper_combo.addItem(name)
        self._paper_names = [name for name, _ in PAPER_PRESETS]
        self._custom_paper_label = None
        self.paper_combo.setCurrentIndex(
            self._paper_names.index(
                self._sync_paper_choices(state.project.layout.paper)
            )
        )
```

`deckle/cli.py:135-162` — the bounds and the message F11 shares:

```python
# The PDF page-size limits pikepdf enforces, in points. Outside them there
# is no page to make, so there is no point imposing one.
MIN_PAPER_PT = 3.0
MAX_PAPER_PT = 14400.0


def _reject_unprintable_paper(paper: tuple[float, float], typed: str) -> None:
    """Refuse a sheet size no PDF can represent, while the user is still
    looking at what they typed.

    ``--paper 0x0`` was accepted here, imposed, and then failed inside
    pikepdf with "Page size must be between 3 and 14400 PDF units" -- a
    library traceback that names neither ``--paper`` nor the value the
    user gave, after doing all the work. The limit is real; the place to
    apply it is the boundary the value came in through.
    """
    for length in paper:
        if not MIN_PAPER_PT <= length <= MAX_PAPER_PT:
            raise argparse.ArgumentTypeError(...)
```

`deckle/app/views/layout_panel.py:140-162` — the unit table and converters
the dialog reuses (`LENGTH_UNITS`, `to_points`, `from_points`). **Do not add
a third copy** — `00-environment.md` names the two that exist.

### Call sites

```
$ grep -rn "MIN_PAPER_PT\|MAX_PAPER_PT" deckle/ tests/
deckle/cli.py:137,138,156,159,160
tests/test_cli_errors.py:285,288,289,291

$ grep -rn "_sync_paper_choices\|_current_paper_pt\|preset_name_for\|PAPER_PRESETS" deckle/ tests/ | grep -v "core/paper.py"
deckle/app/views/layout_panel.py:203,234,806,812,1236,1484,1503,1527,1542
tests/test_layout_panel_paper.py  (the whole file)
tests/test_layout_panel_refresh.py
tests/test_ui_surface.py
```

`deckle/core/paper.py:129` also defines a `PAPER_PRESETS` — **a different
thing**, paper *stock* (80gsm copier), aliased on import as `PAPER_STOCKS`
with a comment at `layout_panel.py:26-31` explaining that two meanings of
"paper" in one file is how a reader wires a dropdown to the wrong one. Read
that comment before touching either.

### Existing tests

`tests/test_layout_panel_paper.py` — the closest file; read it first.
`tests/test_layout_panel_refresh.py` (the `_sync_paper_choices` stale-entry
regression), `tests/test_cli_paper.py`, `tests/test_paper_bounds.py`,
`tests/test_ui_surface.py`.

## 3. Change

A permanent `"Custom..."` entry at the end of the paper combo that opens a
small width/height/unit dialog, feeding the **existing** `Custom (W x Hpt)`
display path.

**Chosen:** a sentinel combo entry that opens a modal. **Rejected:** two
always-visible spin boxes beside the combo, which would be two controls for
one decision (a preset *and* a size) and would have to be kept in sync with
the combo on every change — the shape B11/B12/B29 all come from.

### 1. `deckle/core/paper.py` — the bounds move down

Move `MIN_PAPER_PT` and `MAX_PAPER_PT` verbatim, with their comment, from
`cli.py:135-138` into `paper.py`, and add:

```python
def paper_size_problem(paper: tuple[float, float]) -> str | None:
    """Why this sheet size cannot be made, or ``None`` if it can.

    The PDF page-size limits pikepdf enforces. Outside them there is no
    page to make, so there is no point imposing one -- and the place to say
    so is the boundary the numbers came in through, whether that is
    ``--paper`` or a dialog, which is why the sentence lives here rather
    than in either front end.

    :param paper: ``(width, height)`` in points.
    :returns: a user-facing sentence, or ``None``.
    """
    for length in paper:
        if not MIN_PAPER_PT <= length <= MAX_PAPER_PT:
            return (
                f"a PDF page must be between {MIN_PAPER_PT:g} and "
                f"{MAX_PAPER_PT:g}pt ({MIN_PAPER_PT / 72:.2g}in to "
                f"{MAX_PAPER_PT / 72:g}in) on each side; that is "
                f"{paper[0]:g}x{paper[1]:g}pt"
            )
    return None
```

`cli.py` imports both constants and the function, and
`_reject_unprintable_paper` becomes:

```python
def _reject_unprintable_paper(paper: tuple[float, float], typed: str) -> None:
    """... unchanged docstring ..."""
    problem = paper_size_problem(paper)
    if problem is not None:
        raise argparse.ArgumentTypeError(f"invalid paper {typed!r}: {problem}")
```

The CLI's message text is **unchanged character for character** —
`tests/test_cli_errors.py:285-291` and `tests/test_paper_bounds.py` pin it.
Keep `MIN_PAPER_PT`/`MAX_PAPER_PT` importable from `deckle.cli` (re-export)
so those tests' `from deckle.cli import MAX_PAPER_PT, MIN_PAPER_PT` keeps
working.

### 2. New module `deckle/app/views/custom_paper.py`

Qt imported lazily inside functions, as every other view module does.
Module docstring: this is the app's half of `--paper WxH[unit]`, it stores
points like everything else in the core, and the bounds sentence comes from
`deckle.core.paper` so the dialog and the flag refuse the same sizes for the
same stated reason.

```python
class CustomPaperDialog:
    """Type a sheet size in whatever unit suits the stock.

    :param paper: the size to start from, in points -- the project's
        current paper, so "Custom..." opens on what you already have
        rather than on an arbitrary default.
    :param unit: the panel's current unit, so the numbers on screen match
        every other length in the panel.
    :param parent: the parent ``QWidget``, or ``None``.
    :ivar widget: the ``QDialog`` to show. This class is not itself a
        widget.
    :ivar paper_pt: the accepted size in points, or ``None`` if the dialog
        was cancelled.
    """

    def __init__(self, paper: tuple[float, float], *, unit: str = "in", parent=None) -> None:
```

Widgets, in a `QFormLayout`, with these **exact** labels:

| Row label | Attribute | Type | Range / contents |
|---|---|---|---|
| `Units:` | `unit_combo` | `QComboBox` | `["pt", "in", "cm", "mm"]`, set to `unit` |
| `Width:` | `width_spin` | `QDoubleSpinBox` | 3 decimals, `from_points(MIN_PAPER_PT, unit)` to `from_points(MAX_PAPER_PT, unit)` |
| `Height:` | `height_spin` | `QDoubleSpinBox` | same |
| *(none)* | `status_label` | `QLabel` | starts empty |
| *(none)* | `ok_button` | `QPushButton` | text `"OK"` |
| *(none)* | `cancel_button` | `QPushButton` | text `"Cancel"` |

Window title: `"Custom paper size"`.

Changing `unit_combo` re-converts both spin boxes **and their ranges**,
inside one `blockSignals(True)/False` around the whole update — this is
exactly B11, where a unit change converted some values and not others, and
left the boxes with their construction-time ranges. Do it once, correctly,
in twelve lines, and write a test for it.

`accept()`:

1. `paper = (to_points(width_spin.value(), unit), to_points(height_spin.value(), unit))`
2. `problem = paper_size_problem(paper)`; if not `None`, set
   `status_label` to `problem` and return without closing. (The spin-box
   ranges make this unreachable from the UI; it is here because the ranges
   are a second expression of the same rule and the two must not be able to
   disagree.)
3. `self.paper_pt = paper`; `self.widget.accept()`.

`cancel_button` → `self.widget.reject()`, leaving `paper_pt` as `None`.

No orientation control: the panel's `Orientation:` combo already owns that
and `set_paper(..., landscape=...)` applies it. A second orientation control
would be the two-controls-for-one-decision shape again.

### 3. `layout_panel.py` — the sentinel entry

```python
CUSTOM_PAPER_LABEL = "Custom..."
"""The paper-combo entry that opens the size dialog.

Distinct from the generated ``Custom (W x Hpt)`` entry, which *names* a
size the project already has. This one is a verb.
"""
```

Construction (replacing lines 805-814):

```python
        self.paper_combo = QComboBox(self.widget)
        for name, _dimensions in PAPER_PRESETS:
            self.paper_combo.addItem(name)
        self._paper_names = [name for name, _ in PAPER_PRESETS]
        self._custom_paper_label = None
        # The sentinel stays LAST for the life of the panel; the generated
        # "Custom (W x Hpt)" entry is inserted before it, so its index is
        # always len(PAPER_PRESETS).
        self.paper_combo.addItem(CUSTOM_PAPER_LABEL)
        self._paper_names.append(CUSTOM_PAPER_LABEL)
        self.paper_combo.setCurrentIndex(
            self._paper_names.index(
                self._sync_paper_choices(state.project.layout.paper)
            )
        )
```

`_sync_paper_choices`'s two mutation blocks change from `addItem` to
`insertItem` so the sentinel stays last:

```python
        if wanted is not None and self._custom_paper_label is None:
            index = len(PAPER_PRESETS)
            self.paper_combo.insertItem(index, wanted)
            self._paper_names.insert(index, wanted)
            self._custom_paper_label = wanted
```

The removal block is unchanged — it already looks the label up by name.
Everything else in that method, including its whole docstring, stays.

### 4. `_current_paper_pt` learns the sentinel

```python
    def _current_paper_pt(self) -> tuple[float, float]:
        """The paper dimensions the combos currently describe."""
        name = self.paper_combo.currentText()
        if name == CUSTOM_PAPER_LABEL:
            # The sentinel names no size. `_on_paper_changed` intercepts it
            # before this is reached; returning the current paper keeps a
            # stray call a no-op rather than a resize.
            return self.state.project.layout.paper
        for preset_name, dimensions in PAPER_PRESETS:
            if preset_name == name:
                return dimensions
        # The generated custom entry: keep whatever the project already has.
        return self.state.project.layout.paper
```

### 5. `_on_paper_changed` intercepts the sentinel

```python
    def _on_paper_changed(self, name: str) -> None:
        """Apply a new sheet size, preserving the chosen orientation.

        The ``Custom...`` entry is a verb, not a size: it opens the dialog
        and then selects whatever entry names the result -- which is the
        generated ``Custom (W x Hpt)`` one, produced by the same
        `_sync_paper_choices` path a CLI-imposed project already uses.
        """
        if name == CUSTOM_PAPER_LABEL:
            self._prompt_custom_paper()
            return
        landscape = self.orientation_combo.currentText() == "Landscape"
        plan = apply_layout_change(...)   # unchanged
```

```python
    def _prompt_custom_paper(self) -> None:
        current = self.state.project.layout.paper
        dialog = self._custom_paper_dialog_cls(
            current, unit=self._unit, parent=self.widget
        )
        dialog.widget.exec()
        paper = dialog.paper_pt
        if paper is None:
            # Cancelled. Put the combo back on the entry that names the
            # paper we still have, without firing this handler again --
            # setCurrentIndex emits currentTextChanged.
            self._select_paper_entry(current)
            return
        landscape = self.orientation_combo.currentText() == "Landscape"
        plan = apply_layout_change(
            self.state,
            lambda project: set_paper(project, paper, landscape=landscape),
        )
        self._select_paper_entry(self.state.project.layout.paper)
        self._refresh_binding_readout(plan)
        self.layout_changed.emit(plan)

    def _select_paper_entry(self, paper: tuple[float, float]) -> None:
        """Point the combo at the entry naming ``paper``, silently."""
        label = self._sync_paper_choices(paper)
        self.paper_combo.blockSignals(True)
        self.paper_combo.setCurrentIndex(self._paper_names.index(label))
        self.paper_combo.blockSignals(False)
```

`self._custom_paper_dialog_cls` is set in `__init__` from a new keyword-only
constructor parameter `custom_paper_dialog_cls=None`, defaulting to a lazy
import of `CustomPaperDialog` — the same injection pattern `PrintDialog`
uses for `session_cls`/`backend_cls`, so the headless test never opens a
modal.

**`blockSignals` around `setCurrentIndex` is load-bearing.** Without it,
cancelling re-enters `_on_paper_changed`, and accepting fires a second
`set_paper` with the same value, which pushes a spurious undo entry — B12's
exact shape.

### 6. `refresh_from_project`

Line 1235-1236 already does
`self.paper_combo.setCurrentIndex(self._paper_names.index(self._sync_paper_choices(layout.paper)))`
inside the signal-blocked section. It keeps working: `_sync_paper_choices`
now inserts before the sentinel and never returns `CUSTOM_PAPER_LABEL`.

### 7. Docs

- GUIDE §2, "Size and orientation": name the five presets and the
  **Custom...** entry, and say it takes the same numbers `--paper` does.
- `docs/api/app.views.custom_paper.rst`, plus the `docs/api/app.rst`
  toctree entry after `app.views.print_dialog`.
- `docs/research/2026-08-05-competitive-gaps.md`'s *"Custom paper sizes —
  not a gap"* section: append one sentence recording that the GUI entry now
  exists. It is a research document, not a spec; a dated one-line note is
  the right amount.

## 4. Tests

New file `tests/test_custom_paper.py` (headless; `QT_QPA_PLATFORM=offscreen`
plus the module-scoped `qapp` fixture from
`tests/test_print_dialog.py:19-37`).

1. `test_the_dialog_returns_points_from_the_typed_unit`
   `CustomPaperDialog((612.0, 792.0), unit="mm")`; set width 200, height
   280; `accept()`; `paper_pt == pytest.approx((566.9, 793.7), abs=0.1)`.
   Unfixed: `ModuleNotFoundError: No module named
   'deckle.app.views.custom_paper'`.

2. `test_the_dialog_opens_on_the_current_paper`
   Constructed with `(612.0, 792.0)` and `unit="in"`, the spin boxes read
   `8.5` and `11.0`.

3. `test_changing_the_unit_converts_both_values_and_both_ranges`
   Start in `in` at 8.5 × 11; switch to `mm`; the boxes read 215.9 × 279.4
   **and** `width_spin.maximum()` is `from_points(MAX_PAPER_PT, "mm")`.
   **This is B11 as a test** — that bug is a unit change that converted the
   value and left the range.

4. `test_cancel_leaves_no_size`
   `reject()` → `paper_pt is None`.

5. `test_a_size_outside_the_pdf_limits_is_refused_with_the_shared_sentence`
   Bypass the spin-box range (set the value programmatically after widening
   `setMaximum`), call `accept()`; the dialog stays open and
   `status_label.text() == paper_size_problem(paper)`.

New tests in `tests/test_layout_panel_paper.py`:

6. `test_the_paper_combo_offers_a_custom_entry_last`
   The last item is `"Custom..."`. Unfixed: it is `"Tabloid"`.

7. `test_choosing_custom_opens_the_dialog_and_applies_the_size`
   Inject a stub `custom_paper_dialog_cls` returning `(400.0, 600.0)`;
   select `"Custom..."`; the project's paper is `(400.0, 600.0)` and the
   combo now reads `"Custom (400 x 600pt)"` — **the existing generated
   label**, unchanged in format.

8. `test_cancelling_custom_restores_the_previous_entry_and_changes_nothing`
   Stub returns `None`; the project's paper is untouched, the combo reads
   the preset it read before, and `state.undo_depth` (or the equivalent the
   file already uses) is unchanged. Guards the `blockSignals` requirement.

9. `test_the_sentinel_stays_last_when_a_custom_size_is_added`
   After a custom size is applied, `paper_combo.itemText(count - 1) ==
   "Custom..."` and the generated label sits at index
   `len(PAPER_PRESETS)`.

10. `test_a_stale_custom_entry_is_still_dropped`
    The existing `_sync_paper_choices` regression, re-run through the new
    layout: apply 400×600, then select `"A4"`; the `Custom (400 x 600pt)`
    entry is gone, the sentinel is still last, and the combo has
    `len(PAPER_PRESETS) + 1` items.

11. `test_orientation_survives_a_custom_size`
    With `Orientation: Landscape` selected, applying 400×600 gives
    `layout.paper == (600.0, 400.0)`. `set_paper`'s `landscape=` argument is
    what does it; the dialog has no orientation control on purpose.

12. `test_choosing_custom_does_not_enumerate_printers` — in
    `tests/test_ui_surface.py`, the same guard every new control gets.

New test in `tests/test_paper_bounds.py`:

13. `test_the_cli_and_the_dialog_refuse_the_same_sizes`
    For `(0, 0)`, `(2.9, 100)`, `(14401, 100)`:
    `paper_size_problem(...)` is not `None`, and
    `deckle.cli._parse_paper(f"{w}x{h}pt")` raises
    `argparse.ArgumentTypeError` whose message ends with that same sentence.
    One rule, two front ends.

14. `tests/test_cli_errors.py:285-291` must keep passing unchanged — it
    imports `MIN_PAPER_PT`/`MAX_PAPER_PT` from `deckle.cli`, so the
    re-export is not optional.

## 5. Acceptance

| Check | Command |
|---|---|
| Dialog tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_custom_paper.py` |
| Panel paper tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_layout_panel_paper.py tests/test_layout_panel_refresh.py` |
| The unit-conversion guard specifically | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -k changing_the_unit_converts_both_values_and_both_ranges` |
| CLI paper messages unchanged | `.venv/bin/python -m pytest -q tests/test_cli_paper.py tests/test_cli_errors.py tests/test_paper_bounds.py` |
| The bounds live in one place | `test "$(grep -rl 'MAX_PAPER_PT = ' deckle/ \| wc -l)" = "1" && grep -q "MAX_PAPER_PT" deckle/core/paper.py` |
| No third unit table | `test "$(grep -rlE '^LENGTH_UNITS\|^_UNIT_TO_PT' deckle/ \| wc -l)" = "2"` |
| The new module is documented | `test -f docs/api/app.views.custom_paper.rst && grep -q "app.views.custom_paper" docs/api/app.rst` |
| Docs coverage | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_docs_coverage.py` |
| Core stays Qt-free | `.venv/bin/python -m pytest -q tests/test_core_purity.py` |
| Full suite green | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| [HUMAN] It reads as a verb | Launch the app, open Page setup → Paper, and confirm **Custom...** is last, opens on the current size, and that cancelling leaves the previous entry selected. |

Greps run against the current tree:

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from deckle.app.views.layout_panel import PAPER_PRESETS
print([name for name, _ in PAPER_PRESETS])"
['Letter', 'A4', 'Legal', 'A3', 'Tabloid']
$ grep -rn "MIN_PAPER_PT\|MAX_PAPER_PT" deckle/
deckle/cli.py:137, 138, 156, 159, 160
$ grep -rlE '^LENGTH_UNITS|^_UNIT_TO_PT' deckle/
deckle/cli.py
deckle/app/views/layout_panel.py
```

(Anchored to the *definitions*, so `custom_paper.py` importing
`to_points`/`from_points` does not raise the count.)

## 6. Out of scope

- **M5** — one `PAPER_SIZES` table shared by the GUI (5 presets) and the CLI
  (3), and a parity test over them. F11 moves the *bounds*, not the presets.
  A user with A3 stock still cannot type `--paper a3`; that is M5's.
- **B11** — the panel's own unit change not converting trim or the crop
  boxes. F11's dialog gets it right for its own two values and does not
  touch the panel's `_on_unit_changed`.
- **B25** — `_parse_paper` rejecting `cm` while its message lists it, and
  disallowing a space `_parse_length_pt` allows. F11 does not change
  `_parse_paper`'s pattern.
- **Remembering recent custom sizes.** A `.deckle` already works as a
  template (N15).
- **Naming a custom size.** The generated label is its dimensions, which is
  what makes it self-describing when a project is reopened.

## 7. decisions.md entry

```
## 2026-09-05 — A custom paper size could be opened but not typed
- Symptom: the app offered five preset sheet sizes and no way to enter another, while `--paper 500x700pt` had always worked. The panel already knew how to *display* a custom size -- `_sync_paper_choices` generates a "Custom (W x Hpt)" entry for a project that carries one -- so the only missing piece was a way to produce the numbers. A binder with 200x280mm stock had to leave the GUI, run `deckle impose --paper 200x280mm`, and open the result.
- Fix: a permanent "Custom..." sentinel at the end of the paper combo that opens `CustomPaperDialog` (width, height, unit), feeding the existing display path unchanged. The dialog has no orientation control -- the panel's Orientation combo already owns that -- and no size of its own: it opens on the paper the project already has.
- Surfaces: `MIN_PAPER_PT`/`MAX_PAPER_PT` and the sentence explaining them moved from `cli.py` to `core/paper.py` as `paper_size_problem`, so the flag and the dialog refuse the same sizes for the same stated reason. The CLI's message text is unchanged character for character, and both constants are still importable from `deckle.cli` because a test imports them from there.
- Watch: `setCurrentIndex` emits `currentTextChanged`, so the cancel path and the accept path both put the combo back with signals blocked. Without that, cancelling re-enters the handler and accepting pushes a second identical `set_paper` -- a spurious undo entry, which is B12's shape.
- Commit: <fill in>
```

## 8. Traps

- **Two things are called `PAPER_PRESETS`.** `layout_panel.PAPER_PRESETS` is
  sheet *sizes*; `core.paper.PAPER_PRESETS` is paper *stock*, aliased on
  import as `PAPER_STOCKS`. The comment at `layout_panel.py:26-31` exists
  because wiring a dropdown to the wrong one is the obvious mistake.
- **`_sync_paper_choices` indexes `_paper_names` by name.** Keep the list and
  the combo in lockstep on every insert and remove, and keep the sentinel
  last, or `.index()` and `removeItem()` will disagree.
- **`setCurrentIndex` fires `currentTextChanged`.** Block signals around
  every programmatic selection, or the sentinel re-opens the dialog on
  cancel.
- **The generated label's format is `f"Custom ({width:.0f} x {height:.0f}pt)"`
  with `width, height = sorted(paper)`** — the *upright* dimensions, so a
  landscape 600×400 sheet shows `Custom (400 x 600pt)`. Do not "fix" that;
  `preset_name_for` matches on the sorted pair for the same reason.
- **Do not add a third unit conversion table.** `00-environment.md` names the
  two that exist and says which to use.
- **The dialog is not a widget.** `self.widget` is the `QDialog`, matching
  `PrintDialog`. `QT_QPA_PLATFORM=offscreen` plus `show()` hard-kills the
  process (exit 127); tests construct and drive, never show.
- **`tests/test_docs_coverage.py`** fails on a new module with no `.rst`.
- **`tests/test_cli_errors.py` imports the bounds from `deckle.cli`.** Keep
  the re-export.
- **`python -m deckle` launches the GUI and blocks.**
