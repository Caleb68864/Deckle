# N15 — Save the layout you always use as your defaults

**Roadmap item:** `docs/ROADMAP.md` N15
**Depends on:** —. Edits `deckle/app/main.py` (collides with **M3**, **N4**, **N7**, **N8**, **N13**, **B9/B10/N9**), `deckle/app/views/layout_panel.py` (collides with **M1**, **N5**, **N11**, **N12**) and `deckle/core/project_io.py` (collides with **B23**/**B28**, which are about the same reader). Adds one module. Recommended: land after **N12** (which also adds a button below the mode tabs) and before **M1**.
**Blocks:** —
**Size:** S

**Decision needed first:** none. But §3 makes one decision worth reading before
implementing: **the CLI deliberately does not read the defaults file.**

---

## 1. Context

Every new Deckle window opens on US Letter, portrait, zero gutter, zero
margins, flat sheets, `slack_to="gutter"`, no grain, no thickness. For the
person this application is for — one person, at home, binding books on the
paper they always buy — every one of those is wrong every time, and there are
about a dozen of them to reset before the first useful preview.

```python
def default_project() -> Project:
    """An empty project on US Letter, for a freshly launched window.

    :returns: a project with no pages, no gutter and no printer.
    """
    layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)
```

`LETTER_PT` is a module constant. There is no persistence of any layout
preference anywhere:

```bash
$ grep -rn "config_dir(" deckle
deckle/core/paths.py:63:def config_dir(*parts: str) -> Path:
deckle/core/profiles.py:171:    return config_dir("printer_profiles")
deckle/core/recent.py:35:    return config_dir(_STORE_NAME)
```

Two stores — printer profiles and the recent list — and neither is a setting
the user chose about how their books are made.

The roadmap notes that a `.deckle` already works as a template via
`deckle impose`. That is true and it is the *CLI's* answer; it requires
naming a file on every invocation, and there is no GUI equivalent at all.

## 2. Current code

`deckle/app/main.py:37` and `290-296` — the hard-coded default, and its only
caller at `main.py:412`:

```python
LETTER_PT = (612.0, 792.0)
```

```python
def default_project() -> Project:
    """An empty project on US Letter, for a freshly launched window.

    :returns: a project with no pages, no gutter and no printer.
    """
    layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)
```

```python
        self.state = AppState(default_project(), project_path=project_path)
```

`deckle/core/project_io.py:286-293` — the serialiser to reuse, and why it is
shaped this way:

```python
def _layout_to_dict(layout: LayoutSettings) -> dict[str, Any]:
    # No per-field conversion on the way out, deliberately mirroring
    # `_layout_from_dict`: `json` serialises a tuple as an array already,
    # so naming `paper` here achieved nothing that the encoder was not
    # doing for every other tuple field anyway. Naming one field was how
    # the read side came to be wrong; leaving the same shape here would
    # invite someone to "fix" the asymmetry by adding the other three.
    return asdict(layout)
```

`deckle/core/project_io.py:346-393` — the reader, which is the *entire* reason
this spec is small:

```python
def _layout_from_dict(data: dict[str, Any]) -> LayoutSettings:
    """Build ``LayoutSettings`` from stored JSON, tolerating field drift.

    Unknown keys are dropped with a warning rather than raising, and missing
    keys fall back to the dataclass defaults. That makes the format tolerant
    in both directions: a file written by an older build (missing fields) and
    one written by a newer build (extra fields) both open, which is what the
    ``version`` integer was reserved for.

    Tolerant about *keys*, strict about *values* -- see
    :func:`_check_layout_values` for why those pull in opposite directions.
    """
    known = {f.name for f in dataclasses.fields(LayoutSettings)}
    kwargs = {k: v for k, v in data.items() if k in known}
    unknown = sorted(set(data) - known)
    if unknown:
        warnings.warn(..., UnknownLayoutFieldsWarning, stacklevel=2)
    _check_layout_values(kwargs)
    kwargs = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in kwargs.items()
    }
    return LayoutSettings(**kwargs)
```

`deckle/core/project_io.py:309-320` — `_check_layout_values`, which routes to
`schema.check_values` and additionally refuses non-positive paper.

`deckle/core/paths.py:63-76` — `config_dir(*parts)`; `paths.py:213-272` —
`write_text_atomic`, and its warning that it is safe against failure, not
against a second writer.

`deckle/core/recent.py:48-76` — the model for a small, never-fatal JSON store:

```python
def load() -> list[str]:
    """...
    :returns: the paths. An unreadable or malformed store reads as empty:
        this is a convenience list, and losing it is not worth a
        traceback in front of someone trying to open a file.
    """
    path = _store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
```

`deckle/app/views/layout_panel.py:1207-1286` — `refresh_from_project`, which
re-reads every control from the project; `layout_panel.py:760-763` — the mode
tabs, below which a mode-independent action belongs (see N12).

**Call sites of `_layout_to_dict` / `_layout_from_dict` (grep):**
`project_io.py:286,346` (definitions), `project_io.py:421` (inside
`save_project`), `project_io.py:562` (inside `load_project`);
`tests/test_project_io.py:330,339,355,364,372,376,381,388`.

**Call sites of `default_project` (grep):** `deckle/app/main.py:290`
(definition), `main.py:412`; `tests/test_integration.py:29,273`.

**Existing tests:** `tests/test_project_io.py:320-390` (drift tolerance in both
directions, and the exhaustive round trip), `tests/test_settings_roundtrip.py`,
`tests/test_layout_field_types.py` (stored-value strictness),
`tests/test_app_directories.py` (`config_dir`/`data_dir` per platform),
`tests/test_config_store_durability.py` (atomic profile writes),
`tests/test_recent.py`, `tests/test_integration.py:273`.

## 3. Change

### The file

`config_dir("defaults.json")` — `config_dir`, not `data_dir`, because this is a
setting the user chose, exactly like a printer profile. Shape mirrors
`.deckle`:

```json
{
  "version": 1,
  "layout": { ... every persisted LayoutSettings field ... }
}
```

### What is a default and what is not

Three `LayoutSettings` fields are **excluded**, because they describe *this
document* rather than how the user works:

| Field | Why excluded |
|---|---|
| `crop_odd_pt` | Measured off one scan's margins. Carried into the next project it would silently crop a document it was never measured against — the "confidently wrong" failure `cli._cmd_crop_preview` documents. |
| `crop_even_pt` | Same. |
| `signature_lengths` | `7,7,6,6` is chosen for one book's page count and its chapter breaks. `split_signatures_at` refuses lengths that do not sum to the sheet count, so a carried-over value would make the *next* import fail to impose with a message about numbers the user never typed. |

Everything else persists, including `paper_thickness_pt`, `grain`,
`sewing_stations`, `trim_pt` and (if **N5** has landed)
`sewing_station_positions_pt` — those are properties of the paper someone buys
and the way they sew, which is precisely what a default is for.

```python
EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {"crop_odd_pt", "crop_even_pt", "signature_lengths"}
)
```

### New module `deckle/core/defaults.py`

Pure, Qt-free, needs `docs/api/core.defaults.rst` plus a `core.defaults` line
in `docs/api/core.rst`'s toctree (`tests/test_docs_coverage.py` enforces it).

```python
"""The layout a new project starts from, when the user has said what it is.

Every new window opened on US Letter, portrait, zero gutter, zero margins,
flat sheets -- about a dozen settings to reset before the first useful
preview, for someone who buys the same paper every time. This is where
that answer is remembered.

Stored as ``config_dir("defaults.json")`` in the same shape as a
``.deckle``: a ``version`` and a ``layout`` object, read back through
``project_io``'s own layout reader so it inherits that reader's tolerance
of field drift in both directions -- a defaults file written before a
field existed, or after it was removed, still opens.

Three fields are deliberately NOT persisted -- the two crops and
``signature_lengths``. They describe one document rather than how someone
works, and carrying them forward would crop the next scan against margins
measured off a different one, or refuse to impose it with a gathering list
the user never typed.

Nothing here raises. A defaults file that cannot be read is a preference
lost, not a reason a window fails to open -- the same rule
:mod:`deckle.core.recent` states for the recent list.

This module must not import any Qt binding -- see
``tests/test_core_purity.py``.
"""

DEFAULTS_VERSION = 1
DEFAULTS_FILENAME = "defaults.json"


def defaults_path() -> Path:
    """Where the saved defaults live. Nothing is created."""


def save_defaults(layout: LayoutSettings) -> None:
    """Persist ``layout`` as the starting point for new projects.

    Written atomically, so a save that dies partway leaves the previous
    defaults rather than a truncated file that reads as none.

    :param layout: the settings to remember. The excluded fields are
        dropped here rather than at the call site, so there is one answer
        to "what is a default".
    :returns: nothing.
    :raises OSError: the config directory cannot be written. Raised, not
        swallowed: the user asked for this explicitly and a silent failure
        would have them believe it worked.
    """


def load_defaults() -> LayoutSettings | None:
    """The user's saved defaults, or ``None``.

    :returns: the settings, or ``None`` when nothing is saved, the file is
        unreadable, or a stored value is one this build cannot honour.
        Never raises: this runs while a window is being built, and a
        preference that cannot be read must not be what stops Deckle
        opening.
    """


def forget_defaults() -> bool:
    """Delete the saved defaults.

    :returns: whether a file was actually removed. ``False`` for "there was
        nothing saved", which is a normal answer and not an error.
    :raises OSError: never -- a defaults file that will not delete is
        reported by the caller, not raised at it.
    """
```

Implementations:

- `defaults_path()` → `config_dir(DEFAULTS_FILENAME)`.
- `save_defaults` → build
  `{k: v for k, v in layout_to_dict(layout).items() if k not in EXCLUDED_FIELDS}`,
  wrap as `{"version": DEFAULTS_VERSION, "layout": ...}`,
  `path.parent.mkdir(parents=True, exist_ok=True)`, `write_text_atomic(path,
  json.dumps(payload, indent=2))` — `indent=2`, matching `save_project` and
  `PrinterProfile.save`.
- `load_defaults` → read, `json.loads`; require a `dict` with a `dict` under
  `"layout"`; call `layout_from_dict` inside
  `warnings.catch_warnings(record=True)` so an `UnknownLayoutFieldsWarning`
  from a newer file does not print a bare warning naming a line inside Deckle
  (the same treatment `main.open_project` gives `PathOutsideRootsAdvisory`,
  `main.py:961-973`); catch `(OSError, ValueError, TypeError, KeyError)` and
  return `None`, recording `log_exception("defaults_unreadable", exc, path=...)`.
- `forget_defaults` → `os.remove` inside `try/except OSError` returning
  `False`.

**`version` is read, not just written.** A file whose `version` is greater than
`DEFAULTS_VERSION` returns `None` and logs `defaults_version_unsupported`. That
is the opposite of what `.deckle` and the profiles do — B28 records that their
`version` fields "are written but never read" — and it is cheap here because
there is exactly one writer and losing a preference costs nothing. Say so in the
docstring.

### `project_io` — make the two helpers public

`_layout_to_dict` and `_layout_from_dict` become `layout_to_dict` and
`layout_from_dict`. Two internal call sites (`project_io.py:421,562`) and eight
test references (`tests/test_project_io.py`) update. **Keep private aliases**
so the existing tests keep passing while they are updated in the same commit:

```python
# The names the drift-tolerance tests import. Kept so a rename does not
# have to happen in two files at once.
_layout_to_dict = layout_to_dict
_layout_from_dict = layout_from_dict
```

The alternative — a second serialiser in `defaults.py` — is exactly the "three
copies of a rule" shape `paths` and `schema` were both extracted to stop.

### `deckle/app/main.py` — new projects start from them

```python
def default_project() -> Project:
    """An empty project for a freshly launched window.

    Uses the user's saved defaults when there are any, and US Letter with
    no gutter and no margins when there are not. Someone who buys the same
    paper every time should not reset a dozen controls before the first
    useful preview.

    :returns: a project with no pages and no printer.
    """
    layout = load_defaults()
    if layout is None:
        layout = LayoutSettings(paper=LETTER_PT, gutter_pt=0.0, binding_edge="left")
    return Project(pages=[], layout=layout, printer=None)
```

`from deckle.core.defaults import load_defaults` at the top of `main.py`.

Nothing else in the window changes: `MainWindow.__init__` already builds every
control from `state.project.layout` (`layout_panel.py:810, 826, 865, 944, ...`),
so a default paper of A4-landscape simply arrives selected.

### `deckle/app/views/layout_panel.py` — the two actions

Below the mode tabs, in the mode-independent region (the same place N12 puts
Save schedule):

```python
        defaults_row = QHBoxLayout()
        self.save_defaults_button = QPushButton("Save as my defaults", self.widget)
        self.save_defaults_button.setToolTip(SAVE_DEFAULTS_TOOLTIP)
        self.forget_defaults_button = QPushButton("Forget my defaults", self.widget)
        self.forget_defaults_button.setToolTip(FORGET_DEFAULTS_TOOLTIP)
        defaults_row.addWidget(self.save_defaults_button)
        defaults_row.addWidget(self.forget_defaults_button)
        outer.addLayout(defaults_row)
```

`QHBoxLayout` needs adding to `_qt_widgets()`'s import and return tuple
(`layout_panel.py:646-667`).

Exact labels: `"Save as my defaults"` and `"Forget my defaults"`.

**"Forget", not "Reset".** The roadmap calls it "Reset defaults", which is
ambiguous between two actions: forget the saved file, or reset *this project's*
settings back to the defaults. The second is destructive, has no confirmation,
and is already what Ctrl+Z is for — so it is not built, and the button is named
for the one it does.

```python
SAVE_DEFAULTS_TOOLTIP = (
    "Remember these settings and start every new project from them.\n\n"
    "Paper, orientation, grain, thickness, margins, gutter, how it folds, "
    "trim and sewing -- everything on this panel except the crop boxes and "
    "the gathering list, which are measured from one document and mean "
    "nothing on the next.\n\n"
    "Affects new projects only. Opening a saved .deckle always uses that "
    "project's own settings."
)

FORGET_DEFAULTS_TOOLTIP = (
    "Delete your saved defaults, so new projects go back to Deckle's own: "
    "US Letter, portrait, no gutter, no margins, flat sheets.\n\n"
    "Does not change the project you have open."
)
```

Handlers, reporting through the existing `schedule_saved` signal — which
`MainWindow` connects straight to the status bar (`main.py:558`) and whose
docstring already calls it "a user-facing outcome message":

```python
    def _on_save_defaults_clicked(self) -> None:
        """Remember the current settings for new projects.

        :returns: nothing. A config directory that cannot be written is
            reported in the wording :mod:`deckle.core.outputs` owns, so it
            reads the same as any other failed write.
        """
        try:
            save_defaults(self.state.project.layout)
        except OSError as exc:
            log_exception("defaults_write_failed", exc, path=str(defaults_path()))
            self.schedule_saved.emit(describe_write_failure(str(defaults_path()), exc))
            return
        log_event("defaults_saved", path=str(defaults_path()))
        self.schedule_saved.emit(
            "Saved these settings as your defaults for new projects."
        )

    def _on_forget_defaults_clicked(self) -> None:
        """Delete the saved defaults.

        :returns: nothing. Not confirmed: it discards a preference, not
            work, and saving them again is one click.
        """
        if forget_defaults():
            self.schedule_saved.emit(
                "Forgot your defaults. New projects start from Deckle's own."
            )
        else:
            self.schedule_saved.emit("You have no saved defaults.")
```

`describe_write_failure` is already imported (`layout_panel.py:38`).

### The CLI deliberately does not read `defaults.json`

`deckle export book.pdf -o out.pdf` must produce the same book on two machines
and in CI. A machine-local defaults file silently changing the paper size,
gutter and fold scheme of every headless run is the opposite of what a
scriptable tool is for, and it would make the golden-fixture regression
(`tests/test_golden_pinebox.py`) depend on the developer's config directory.

The CLI's template mechanism already exists and is explicit: a `.deckle` is
accepted as SOURCE by every command and its stored layout wins
(`cli._resolve_input`, `cli.py:635-647`). That is a file the user names, in the
invocation, and it is reproducible.

Record this in `deckle/core/defaults.py`'s module docstring:

> **The CLI does not read this file.** `deckle export book.pdf` must produce
> the same book on two machines; a machine-local default silently changing the
> paper and the fold scheme of every headless run would make that untrue, and
> would make the golden-fixture regression depend on a developer's config
> directory. The CLI's template is a `.deckle` named in the invocation, which
> is explicit and reproducible.

## 4. Tests

Every test sets **both** `XDG_CONFIG_HOME` and `APPDATA` to a `tmp_path` —
`paths._root` reads the environment at call time, and an unguarded test writes
into the developer's real config directory and changes what their next launch
does.

### `tests/test_defaults.py` (new)

| Test | Setup | Assertion | Failure on the unfixed tree |
|---|---|---|---|
| `test_nothing_saved_means_no_defaults` | empty config dir | `load_defaults() is None` | `ModuleNotFoundError: No module named 'deckle.core.defaults'` |
| `test_a_saved_layout_comes_back_equal` | a `LayoutSettings` with every persisted field set to a non-default value | `load_defaults() == expected`, where `expected` is the same settings with the three excluded fields at their dataclass defaults | as above |
| `test_tuples_come_back_as_tuples` | `paper=(841.89, 595.28)` | `load_defaults().paper` is a `tuple`, and the whole object is hashable — the exact regression `_layout_from_dict`'s comment records | as above |
| `test_the_crops_are_not_saved` | `crop_odd_pt=(1,2,3,4)`, `crop_even_pt=(5,6,7,8)` | both are `None` on the way back, and neither key is in the written JSON | as above |
| `test_the_gathering_list_is_not_saved` | `signature_lengths=(7,7,6)` | `None` on the way back | as above |
| `test_the_paper_stock_is_saved` | `paper_thickness_pt=0.288`, `grain="short"`, `sewing_stations=5`, `trim_pt=18.0` | all four survive | as above |
| `test_the_file_lives_in_the_config_directory` | | `defaults_path() == Path(tmp_path)/"deckle"/"defaults.json"` on Linux | as above |
| `test_the_file_is_versioned` | | the written JSON has `"version": 1` and a `"layout"` object | as above |
| `test_a_corrupt_file_reads_as_none` | write `not json` | `load_defaults() is None`, no exception | as above |
| `test_a_file_that_is_not_an_object_reads_as_none` | write `[1, 2, 3]` | `None` | as above |
| `test_a_file_with_no_layout_reads_as_none` | `{"version": 1}` | `None` | as above |
| `test_a_bad_stored_value_reads_as_none` | `{"version": 1, "layout": {"binding_edge": "middle"}}` | `None` — `check_values` refuses it and `load_defaults` swallows | as above |
| `test_a_newer_version_reads_as_none` | `{"version": 99, "layout": {...}}` | `None` | as above |
| `test_a_file_from_an_older_build_still_opens` | `{"version": 1, "layout": {"paper": [612, 792], "gutter_pt": 18, "binding_edge": "left"}}` — no other keys | loads, every other field at its dataclass default | as above |
| `test_a_file_from_a_newer_build_still_opens` | add `{"future_field": 1}` to the layout | loads, and **no warning escapes to the caller** | as above |
| `test_saving_is_atomic` | pre-write a valid file, then patch `write_text_atomic` to raise partway | the previous file is intact | as above |
| `test_forget_removes_the_file` | save then forget | `defaults_path()` does not exist, `forget_defaults()` returned `True` | as above |
| `test_forgetting_nothing_is_not_an_error` | empty config dir | returns `False`, no exception | as above |
| `test_an_unwritable_config_directory_raises` | `chmod 0o500` the config dir (skip on Windows) | `save_defaults` raises `OSError` — deliberately, unlike `load_defaults` | as above |

### `tests/test_project_io.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_layout_serialiser_is_public` | `from deckle.core.project_io import layout_to_dict, layout_from_dict` succeeds and round-trips | `ImportError` |
| The eight existing `_layout_from_dict` / `_layout_to_dict` references | still pass via the aliases | pass today; they pin that the rename kept them working |

### `tests/test_integration.py` (extend — it already imports `default_project`)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_a_new_project_uses_the_saved_defaults` | save defaults with `paper=(841.89, 595.28)`, `gutter_pt=36.0`, `fold_scheme="folio"`; call `default_project()` | its layout matches on all three | `AssertionError` — Letter, 0, `"none"` |
| `test_a_new_project_falls_back_to_letter` | empty config dir | `paper == (612.0, 792.0)`, `gutter_pt == 0.0`, `fold_scheme == "none"` | passes today; it pins the fallback |
| `test_an_unreadable_defaults_file_does_not_stop_a_new_project` | write `not json` | `default_project()` returns a Letter project without raising | `AssertionError`/exception |

### `tests/test_layout_panel_widgets.py` (extend)

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_panel_offers_saving_defaults` | `hasattr(panel, "save_defaults_button")` and `forget_defaults_button` | `AttributeError` |
| `test_saving_defaults_writes_the_current_layout` | set the gutter, click | `load_defaults().gutter_pt` matches, and a `schedule_saved` signal carried `"Saved these settings as your defaults"` | as above |
| `test_forgetting_defaults_says_so` | save, then click Forget | the file is gone and the message is `"Forgot your defaults. New projects start from Deckle's own."` | as above |
| `test_forgetting_nothing_says_so` | click Forget with nothing saved | `"You have no saved defaults."` | as above |
| `test_the_defaults_buttons_are_not_on_a_mode_tab` | `save_defaults_button.parentWidget() is panel.widget` | as above |
| `test_a_failed_save_is_reported_not_raised` | patch `save_defaults` to raise `OSError` | no exception, a `schedule_saved` message naming the path | as above |

### `tests/test_cli.py` (extend) — the decision, pinned

| Test | Assertion | Failure on the unfixed tree |
|---|---|---|
| `test_the_cli_ignores_saved_defaults` | save defaults with `paper=(841.89, 595.28)` under a `tmp_path` `XDG_CONFIG_HOME`; run `deckle info d.pdf --json` | the reported `paper_pt` is Letter, not A4 | passes today, and must keep passing — it is the guard on §3's decision |

(If **N14** has not landed, assert on the text output's absence of A4 instead.)

## 5. Acceptance

| Check | Command |
|---|---|
| The new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_defaults.py` |
| The integration and panel tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_integration.py tests/test_project_io.py -k "layout_panel or integration or project_io"` |
| Full suite still at baseline | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |
| The new module has a docs page | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_docs_coverage.py` |
| Core stays Qt-free | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider tests/test_core_purity.py` |
| There is one layout serialiser | `test "$(grep -rho 'asdict(layout)' deckle \| wc -l)" = 1` (today: the single occurrence at `deckle/core/project_io.py:293`) |
| The CLI never reads the defaults | `! grep -rn "core.defaults\|load_defaults\|defaults_path" deckle/cli.py` — **not** a bare `grep defaults`, which already matches ten argparse `default=` occurrences |
| Defaults go in the config root, not data | `grep -q 'config_dir(DEFAULTS_FILENAME)' deckle/core/defaults.py` |
| The excluded fields are excluded in one place | `grep -c "EXCLUDED_FIELDS" deckle/core/defaults.py` returns `2` (the definition and its one use) |
| `[HUMAN]` It saves the right dozen things | Set paper to A4 landscape, gutter 0.75in, all three margins 0.5in, grain short, thickness from an 80gsm preset, Signatures with 5 sheets and 5 stations. Click **Save as my defaults**. Quit and relaunch: every one of those is set, and the crop boxes are all zero. |

## 6. Out of scope

- **Per-project overrides of a default.** A `.deckle` always wins; opening one
  never consults `defaults.json`. That is the existing rule
  (`cli._resolve_input`'s docstring: "A `.deckle` carries its own layout, and
  that layout wins") and N15 does not change it.
- **A "reset this project to my defaults" action.** The roadmap's "Reset
  defaults" is read here as forgetting the file; resetting the open project is
  destructive, has no confirmation, and is what undo is for. If it is wanted it
  is a separate action with its own prompt.
- **Defaults for anything but `LayoutSettings`** — printer choice, window
  geometry, splitter positions, the unit combo. The unit is tempting (it is a
  pure display preference and it is on the same panel) and it is not a
  `LayoutSettings` field, so it needs its own store; that is a second spec.
- **B28** (`version` fields written and never read for `.deckle` and profiles).
  N15's own file reads its version; the two existing stores are still B28's.
- **B23** (`.deckle` load swallows every warning except
  `PathOutsideRootsAdvisory`, so `UnknownLayoutFieldsWarning` is lost on the
  CLI). `load_defaults` swallows it deliberately and says so — a preference file
  from a newer build is the normal case, not a data-loss event. Do not
  "consistent-ify" the two.
- **M1.** Two more buttons on a panel M1 wants to shrink; they are not part of
  the ~25-control table.
- **D6** (the GUIDE has no coverage of autosave recovery or paper-by-weight, and
  will now also lack defaults). Docs pass.

## 7. decisions.md entry

```
## 2026-09-05 — Every new project started from settings nobody uses
- Symptom: `default_project()` hard-coded US Letter, portrait, zero gutter, zero margins, flat sheets. For the one person Deckle is built for -- at home, binding on the paper they always buy -- roughly a dozen controls had to be reset before the first useful preview, every single time. `grep -rn "config_dir("` found two stores, printer profiles and the recent list, and neither is a setting about how books are made.
- Fix: `deckle/core/defaults.py` writes `config_dir("defaults.json")` -- a `version` and a `layout`, the same shape as a `.deckle` -- through `project_io`'s own layout serialiser, which was made public rather than copied. New projects start from it; two buttons on the panel save and forget it. Reading inherits `_layout_from_dict`'s drift tolerance for free, so a defaults file written before or after a field existed still opens.
- Surfaces: Three fields are deliberately not persisted. The two crops are measured off one scan and would silently crop the next document against margins never measured against it; `signature_lengths` is chosen for one book's page count, and carrying it forward would make the NEXT import refuse to impose with a message about numbers the user never typed.
- Surfaces: The CLI does not read this file, on purpose. `deckle export book.pdf` must produce the same book on two machines, and a machine-local default silently changing the paper and the fold scheme of every headless run would make the golden-fixture regression depend on a developer's config directory. The CLI's template is a `.deckle` named in the invocation.
- Watch: This file's `version` is actually READ -- a newer version returns None rather than being parsed hopefully. `.deckle` and the printer profiles both write a version and never read it (B28); this one is cheap to get right because there is one writer and losing a preference costs nothing.
- Watch: "Forget my defaults", not "Reset defaults". The roadmap's wording is ambiguous between forgetting the file and resetting the open project, and only one of those is non-destructive.
- Commit: <fill in>
```

## 8. Traps

- **`paths._root` reads the environment at call time.** A test that does not
  set both `XDG_CONFIG_HOME` and `APPDATA` writes into the developer's real
  config directory and silently changes what their next launch does.
- **`load_defaults` must never raise.** It runs inside `default_project()`,
  which runs inside `MainWindow.__init__`. An exception there is a window that
  does not open, over a preference. `recent.load` states the same rule
  (`recent.py:53-57`).
- **`save_defaults` must raise.** It is an explicit user action; swallowing an
  `OSError` would leave someone believing their settings were remembered. The
  asymmetry is deliberate and both docstrings say so.
- **`_layout_from_dict` emits `UnknownLayoutFieldsWarning` through `warnings`**
  (`project_io.py:366-371`). Unguarded, Python prints a bare warning naming a
  line inside Deckle every time an older build opens a newer defaults file —
  the exact complaint `main.open_project` records for `PathOutsideRootsAdvisory`
  (`main.py:963-968`). Wrap the call in `warnings.catch_warnings(record=True)`.
- **JSON has one sequence type and Python has two.** `layout_from_dict`
  converts every list back to a tuple, and the comment at `project_io.py:377-388`
  records what happens when it does not: the reloaded layout no longer equals
  the saved one, the frozen dataclass stops being hashable, and the export cache
  keys on `repr` so identical geometry produces two entries. Reuse that reader;
  do not write a second one.
- **`_check_layout_values` refuses non-positive paper** (`project_io.py:337-344`)
  and every value outside its field's `Literal`. That strictness is why
  `load_defaults` catches `ValueError` — `StoredValueError` is a `ValueError`
  subclass, chosen precisely so existing callers report it cleanly.
- **`refresh_from_project`'s widget block-list is hand-maintained**
  (`layout_panel.py:1223-1231`, B12). The two new buttons carry no value and do
  not belong on it — but confirm that before adding anything else.
- **Constructing a real `QMainWindow` under pytest exits 127 here**
  (`tests/test_gui_workflow.py:9`). `default_project()` is a plain function and
  `LayoutPanel` constructs fine, which is why every test above avoids one.
- **`python -m deckle` launches the GUI and blocks.** Use `python -m deckle.cli`.
