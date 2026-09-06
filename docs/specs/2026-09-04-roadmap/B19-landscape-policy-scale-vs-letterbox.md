# B19 — `landscape_policy`: make `letterbox` real, or delete it

**Roadmap item:** `docs/ROADMAP.md` B19
**Depends on:** B5 if B5 lands first (both touch the one place
`landscape_policy` is read)
**Blocks:** —
**Size:** S either way
**Decision needed first:** **YES.** `docs/ROADMAP.md` §6: *"B19: implement
`letterbox` as distinct from `scale`, or collapse the enum?"* §3A and §3B
are the two complete answers. **The owner picks one; implement exactly
one.** §3.0 sets out the argument each way.

---

## 1. Context

`LayoutSettings.landscape_policy` is
`Literal["rotate", "scale", "letterbox"]`. Nothing in Deckle branches on
anything but `== "rotate"`:

```bash
grep -rn "landscape_policy" --include='*.py' deckle/ | grep -v "def \|:ivar\|addItems\|setCurrentText\|setToolTip"
```

```
deckle/core/layout.py:294:    return settings.landscape_policy == "rotate" and paper_h >= paper_w and src_w > src_h
deckle/core/layout.py:385:    if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
deckle/app/views/layout_panel.py:287:    return replace(project, layout=replace(project.layout, landscape_policy=landscape_policy))
deckle/app/views/layout_panel.py:1059:        self.landscape_policy_combo.setCurrentText(state.project.layout.landscape_policy)
deckle/app/views/layout_panel.py:1255:            self.landscape_policy_combo.setCurrentText(layout.landscape_policy)
```

Two comparisons, both `== "rotate"`. `"scale"` and `"letterbox"` are
**the same value** with two spellings.

Verified — identical placements, byte for byte:

```bash
.venv/bin/python - <<'PYEOF'
from dataclasses import replace
from deckle.core.layout import GutterShiftStrategy
from deckle.core.loader import load_pdf
from deckle.core.models import LayoutSettings

pages = list(load_pdf("tests/fixtures/sample.pdf"))
out = {}
for policy in ("scale", "letterbox", "rotate"):
    s = LayoutSettings(paper=(612.0, 792.0), gutter_pt=36.0,
                       binding_edge="left", landscape_policy=policy)
    plan = GutterShiftStrategy().impose(
        [replace(pages[0], rotate_deg=90), pages[1]], s)
    out[policy] = [p.placement
                   for sheet in plan.sheets
                   for side in (sheet.front, sheet.back) if side
                   for p in side.pages]
print("scale == letterbox:", out["scale"] == out["letterbox"])
print("scale == rotate   :", out["scale"] == out["rotate"])
PYEOF
```

```
scale == letterbox: True
scale == rotate   : False
```

The GUI promises otherwise. `deckle/app/views/layout_panel.py:1060-1066`:

```python
        self.landscape_policy_combo.setToolTip(
            "What to do with a landscape page in a portrait book.\n\n"
            "rotate: turn it 90 degrees so it fills the page (the default "
            "-- the reader turns the book).\n"
            "scale: shrink it to fit upright.\n"
            "letterbox: leave it upright with bands above and below."
        )
```

**Why it matters to a person printing a book.** A user with a wide plate
in a portrait book reads that tooltip, decides bands above and below are
what they want, picks `letterbox`, and gets whatever `scale` does. It
happens to *be* letterboxing — see §1.1 — so the output is not wrong;
what is wrong is that Deckle offers a choice it does not have, and a
control that changes nothing is the shape `docs/decisions.md` has already
deleted once, under *Deleted the scale mode; there is one scale rule*:

> Making `fill_height` scale down would make the two modes byte-for-byte
> identical, leaving a control that changes nothing. … A feature having
> tests is not evidence it should exist.

### 1.1 Why they cannot differ under the current fit rule

Deckle scales content to the largest size fitting the content box **in
both dimensions** (`document_scale`, and `LayoutSettings`'s own comment
at `deckle/core/models.py:326-331`). For a landscape page in a portrait
box, width binds: the scaled width equals the box width exactly, so
`slack_w == 0` and `slack_h > 0`. Vertical slack is always split
(`deckle/core/layout.py:472-474, 481`). The page therefore fills the
box's width and sits centred vertically — **which is letterboxing**.

So `scale` already letterboxes, and no positioning change can separate
the two. Any real distinction has to change either the **scale** or the
**fit rule**. That is what makes this a decision rather than a bug fix.

### 1.2 What the original spec said `letterbox` meant

`docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md:130-133`:

> `rotate` sets `placement.rotate_deg = 90` and emits a
> `mixed_orientation` warning; `scale` fits within the portrait box;
> **`letterbox` centers without scaling.** Global setting, per-page
> override.

"Centers without scaling" is a real, distinct behaviour — scale 1.0, not
"fit". It was never implemented. §3B is that sentence, made precise.

`docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md:385-386`
lists the three as "*rotate to fit*, *scale to fit*, or *letterbox*"
without defining the third.

## 2. Current code

### `deckle/core/models.py:333` — the field

```python
    landscape_policy: Literal["rotate", "scale", "letterbox"] = "rotate"
```

and its documentation at `deckle/core/models.py:305-306`:

```python
    :ivar landscape_policy: what to do with a landscape page in a portrait
        cell. ``rotate`` turns it and warns.
```

Note it documents only `rotate`. The other two have never had a written
contract in the model.

### `deckle/core/layout.py:292-302` — the scale pass

```python
def _rotates_to_portrait(src_w: float, src_h: float, settings: LayoutSettings) -> bool:
    paper_w, paper_h = settings.paper
    return settings.landscape_policy == "rotate" and paper_h >= paper_w and src_w > src_h


def _fitted_dims(slot: SourcePage, settings: LayoutSettings) -> tuple[float, float]:
    """A page's upright dimensions after any landscape rotation."""
    src_w, src_h = _source_dims(slot, settings)
    if _rotates_to_portrait(src_w, src_h, settings):
        src_w, src_h = src_h, src_w
    return src_w, src_h
```

### `deckle/core/layout.py:378-397` — the placement pass

```python
    # Landscape content inside a portrait cell (or vice versa) under the
    # "rotate" policy: rotate the content to match the cell's orientation
    # and warn, rather than silently clipping or shrinking it. Under folio
    # each cell is portrait-shaped even though the sheet itself is
    # landscape, so this must be judged against the cell, not the sheet.
    cell_is_portrait = cell_h >= cell_w
    page_is_landscape = src_w > src_h
    if settings.landscape_policy == "rotate" and cell_is_portrait and page_is_landscape:
        rotate_deg = 90
        src_w, src_h = src_h, src_w
        warnings.append(
            LayoutWarning(
                sheet_index=sheet_index,
                kind="mixed_orientation",
                detail=(
                    f"page {slot.ref.page_index} of {slot.ref.path!r} is "
                    "landscape inside a portrait document; rotated 90deg"
                ),
            )
        )
```

### `deckle/app/views/layout_panel.py:43, 278-287, 1057-1067, 1195, 1228, 1255, 1468-1469`

```python
LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale", "letterbox")
```

```python
def set_landscape_policy(
    project: Project, landscape_policy: Literal["rotate", "scale", "letterbox"]
) -> Project:
    """Set what happens to a landscape page in a portrait cell.

    :param project: the project to derive a new one from.
    :param landscape_policy: ``"rotate"`` turns the content and warns.
    :returns: a new project.
    """
    return replace(project, layout=replace(project.layout, landscape_policy=landscape_policy))
```

```python
        self.landscape_policy_combo = QComboBox(self.widget)
        self.landscape_policy_combo.addItems(list(LANDSCAPE_POLICIES))
```

### `deckle/core/schema.py:83-84` — what a stored value is checked against

```python
    if origin is typing.Literal:
        return value in typing.get_args(hint)
```

So removing a member of the `Literal` makes every `.deckle` carrying it
**unopenable** with `StoredValueError: layout setting 'landscape_policy'
is 'letterbox', but this build of Deckle accepts one of 'rotate',
'scale'`. Branch A must migrate; see §3A step 3.

### There is no CLI flag

`grep -n "landscape" deckle/cli.py` returns only `--landscape` (a paper
*orientation* flag, `deckle/cli.py:711-713, 756-758`), which is unrelated.
`landscape_policy` is GUI-and-`.deckle`-only. F5 lists
`--landscape-policy` as a gap; whichever branch is chosen, F5 must offer
the surviving set.

### Every test that names a policy

`grep -rn "landscape_policy\|letterbox" tests/`:

| Site | Value |
|---|---|
| `tests/test_layout.py:426, 432` | `"rotate"` |
| `tests/test_layout_field_types.py:67` | `"squash"` — the rejected-value case |
| `tests/test_layout_panel_refresh.py:167` | `"scale"` |
| `tests/test_layout_panel_refresh.py:208, 220` | **`"letterbox"`** — `test_every_control_follows_the_loaded_layout` |
| `tests/test_project_io.py:58` | `"rotate"` |
| `tests/test_models.py:161` | asserts the default is `"rotate"` |

`tests/test_settings_roundtrip.py` walks `LayoutSettings`' annotations
rather than naming values; check it with
`grep -n "get_args\|Literal" tests/test_settings_roundtrip.py` before
either branch — a `Literal`-driven round trip will exercise whatever set
survives.

## 3. Change

### 3.0 The argument

**For A (collapse).** The precedent is written down and recent:
`docs/decisions.md`, *Deleted the scale mode; there is one scale rule* —
two modes that were byte-for-byte identical, one control deleted. The
same log's *SS-06* note puts it as a rule: "a control that is already
expressible must not gain a second control expressing it". `letterbox`'s
promised behaviour ("bands above and below") is what `scale` already
produces, which is the definition of already expressible. A is a smaller
tree, one fewer thing to document, and one fewer thing to get wrong in
F5's CLI flag.

**For B (implement).** The MVP sub-spec did define it — "centers without
scaling" — and that is a behaviour `scale` genuinely cannot produce: a
page reproduced at exactly 1:1, so a plate whose physical size matters
(a map with a scale bar, a facsimile, a printed pattern) comes out at
the size it was drawn. Nothing else in Deckle offers 1:1, and no other
setting can be made to.

**Pick one and delete the other section from this file before starting.**
Do not implement both; they contradict.

---

## 3A. BRANCH A — collapse the enum

### 3A.1 What survives

`Literal["rotate", "scale"]`, default `"rotate"`. `"scale"` survives
rather than a rename to `"fit"`, because `"scale"` is already in users'
`.deckle` files and in `tests/test_layout_panel_refresh.py:167`, and a
rename would migrate two values instead of one for no gain. Rejected:
`Literal["rotate", "fit"]`, matching the `scale_mode` deletion's naming
of the surviving fit rule.

### 3A.2 Numbered edits

1. **`deckle/core/models.py:333`**, replace

   ```python
       landscape_policy: Literal["rotate", "scale", "letterbox"] = "rotate"
   ```

   with

   ```python
       landscape_policy: Literal["rotate", "scale"] = "rotate"
   ```

2. **`deckle/core/models.py:305-306`**, replace the field's docs with

   ```python
       :ivar landscape_policy: what to do with a landscape page in a portrait
           cell. ``rotate`` turns it 90 degrees so it fills the cell, and
           warns. ``scale`` leaves it upright, which -- because content is
           always fitted to the box in both dimensions -- means it fills the
           box's width and sits centred vertically, with bands above and
           below.

           There used to be a third value, ``letterbox``, whose promised
           behaviour was that last sentence. It never branched on anything:
           the only comparison in the imposer is ``== "rotate"``, so
           ``scale`` and ``letterbox`` produced byte-identical placements.
           A ``.deckle`` carrying it loads as ``scale``.
   ```

3. **`deckle/core/project_io.py`**, in `_layout_from_dict`, **before** the
   `_check_layout_values(kwargs)` call (currently line 375), insert:

   ```python
       # `letterbox` was a third `landscape_policy` value that never
       # branched on anything -- `scale` and `letterbox` produced identical
       # placements -- and was removed. `check_values` refuses a value
       # outside a field's `Literal`, correctly and harshly, so without
       # this a project saved by any earlier build would simply not open.
       # Mapped rather than dropped: the two meant the same thing, so this
       # loses nothing, which is exactly when a silent migration is
       # allowed.
       if kwargs.get("landscape_policy") == "letterbox":
           kwargs["landscape_policy"] = "scale"
   ```

   Placement is load-bearing: `_check_layout_values` raises
   `StoredValueError` on an out-of-`Literal` value, and the tuple
   conversion below it would not help.

4. **`deckle/app/views/layout_panel.py:43`**, replace

   ```python
   LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale", "letterbox")
   ```

   with

   ```python
   LANDSCAPE_POLICIES: tuple[str, ...] = ("rotate", "scale")
   ```

5. **`deckle/app/views/layout_panel.py:279`**, narrow the annotation:

   ```python
       project: Project, landscape_policy: Literal["rotate", "scale"]
   ```

6. **`deckle/app/views/layout_panel.py:1060-1066`**, replace the tooltip:

   ```python
        self.landscape_policy_combo.setToolTip(
            "What to do with a landscape page in a portrait book.\n\n"
            "rotate: turn it 90 degrees so it fills the page (the default "
            "-- the reader turns the book).\n"
            "scale: leave it upright, filling the width, with bands above "
            "and below."
        )
   ```

7. **`deckle/app/views/layout_panel.py:1255`**, `refresh_from_project`:
   `setCurrentText(layout.landscape_policy)` on a combo that no longer
   offers `"letterbox"` is a silent no-op in Qt, leaving the combo on its
   previous value. Step 3 makes the stored value impossible, so no code
   change is needed — but add the guard as a test
   (§4A, `test_a_legacy_letterbox_project_shows_scale_in_the_combo`).

8. **`tests/test_layout_panel_refresh.py:208, 220`**, change
   `landscape_policy="letterbox"` → `"scale"` and the assertion
   `== "letterbox"` → `== "scale"`. **Deliberate**: the test asserts the
   combo follows the loaded layout, and the value it happened to use no
   longer exists.

9. **`docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md:132`**
   and **`docs/specs/deckle-mvp/sub-spec-1-scaffold-core-models.md:142`**
   are historical records of a shipped spec. **Do not edit them.** The
   decisions-log entry is where the change is recorded; rewriting a
   delivered spec to match a later decision is how the spec tree stops
   being evidence.

10. **`docs/api/`** — nothing to add.

### 3A.3 What does not change

Every placement, for every existing project. `"scale"` and `"letterbox"`
were the same code path; after this there is one spelling of it.

---

## 3B. BRANCH B — implement `letterbox` as "centre at 1:1"

### 3B.1 The definition

> Under `landscape_policy="letterbox"`, a page whose orientation
> **disagrees with its cell** — landscape content in a portrait cell — is
> placed at **scale exactly 1.0**, centred on both axes within the cell's
> content box, and is excluded from `document_scale`. Every other page in
> the document is unaffected, and keeps the one document-wide scale
> computed from the pages that agree with their cell.

Four consequences, all intended:

- A page reproduced at 1:1 comes out the size it was drawn. That is the
  one thing `scale` can never produce and the reason to keep the setting.
- One wide plate no longer shrinks the whole book. Under `scale` it
  participates in `document_scale` and drags every portrait page down
  with it.
- Centred on both axes, ignoring `slack_to`. A plate that is not bound
  into the text block's measure has no gutter to respect; centring is the
  only rule that does not need one.
- A page wider than its content box **overflows and is clipped**, and
  says so with the existing `clipped_by_page` warning. That is the price
  of 1:1 and it is stated rather than silently avoided.

This is `docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md:132`'s
"centers without scaling", made precise. Rejected: `min(1.0, fit)` —
"never enlarge, otherwise fit" — which is a strictly better default but
is not 1:1, and so is `scale` with a cap rather than a distinct
behaviour.

**This branch breaks the uniform-scale invariant for
mixed-orientation documents only.** `docs/decisions.md`, *Uniform
document-wide scale; per-page scaling resized the text*, is about **body
text changing size mid-book**, and the pages this exempts are by
construction not body text — they are the pages whose orientation
disagrees with the book's. The decisions entry in §7B says so explicitly,
because a future reader will otherwise read this as a reversal.

### 3B.2 Numbered edits

All in `deckle/core/layout.py` unless stated.

1. **New helper**, immediately after `_source_dims` (i.e. after line 201):

   ```python
   def _is_letterboxed(
       src_w: float, src_h: float, settings: LayoutSettings, cell: Cell
   ) -> bool:
       """Whether this page is placed at 1:1 rather than fitted to its cell.

       True for landscape content in a portrait cell (or the reverse) under
       ``landscape_policy="letterbox"``. Judged against the **cell**, for
       the reason ``_place_page``'s rotation branch is: under folio the
       sheet is landscape and each cell is portrait, so the sheet's answer
       is the wrong one.
       """
       if settings.landscape_policy != "letterbox":
           return False
       cx0, cy0, cx1, cy1 = cell
       cell_is_portrait = (cy1 - cy0) >= (cx1 - cx0)
       page_is_landscape = src_w > src_h
       # The two disagree: a landscape page in a portrait cell, or a
       # portrait page in a landscape cell.
       return cell_is_portrait == page_is_landscape
   ```

   The final comparison reads oddly and is right: `True == True` is a
   landscape page in a portrait cell, `False == False` is a portrait page
   in a landscape cell, and the mixed cases are the pages that agree with
   their cell and are left alone.

2. **`document_scale` (lines 336-342)**, replace the loop body's guard:

   ```python
       for slot in pages:
           if slot is None or slot.skipped:
               continue
           src_w, src_h = _fitted_dims(slot, settings, cell)
           if src_w <= 0 or src_h <= 0:
               continue
           if _is_letterboxed(src_w, src_h, settings, cell or _full_sheet_cell(settings.paper)):
               # Placed at 1:1 and centred, so it constrains nothing. A
               # single wide plate used to drag every portrait page in the
               # book down to the scale that fitted it.
               continue
           scales.append(min(box_w / src_w, box_h / src_h))
   ```

   (`_fitted_dims(slot, settings, cell)` is **B5's** signature; without
   B5 it is `_fitted_dims(slot, settings)`. Either works here.)

3. **`_place_page`**, after the rotation block and **before** the content
   box arithmetic at line 403, insert:

   ```python
       letterboxed = _is_letterboxed(src_w, src_h, settings, cell)
   ```

4. **`_place_page`, line 431-432**, replace

   ```python
       scaled_w = src_w * scale
       scaled_h = src_h * scale
   ```

   with

   ```python
       # `letterbox` places at 1:1 -- the one thing `scale` cannot do, and
       # what makes the two policies different behaviours rather than two
       # spellings of one. A page bigger than its box overflows and is
       # reported by the clipping warning below; that is the price of
       # reproducing something at the size it was drawn.
       page_scale = 1.0 if letterboxed else scale
       scaled_w = src_w * page_scale
       scaled_h = src_h * page_scale
   ```

5. **`_place_page`, lines 475-481**, replace the slack distribution with

   ```python
       if letterboxed:
           # Centred on both axes, ignoring `slack_to`: a plate placed at
           # 1:1 is not set in the text block's measure, so there is no
           # gutter for it to respect.
           inner_actual = slack_w / 2.0
           bottom_actual = slack_h / 2.0
       else:
           if settings.slack_to == "outer":
               inner_actual = gutter                    # spine exact; fore-edge varies
           elif settings.slack_to == "split":
               inner_actual = gutter + slack_w / 2.0    # both vary, difference kept
           else:  # "gutter" (default)
               inner_actual = gutter + slack_w          # fore-edge exact; spine varies
           bottom_actual = bottom + slack_h / 2.0
   ```

   Note `inner_actual = slack_w / 2.0` measures from the **cell** edge,
   not from the gutter: centring in the cell, not in the content box, is
   what "centred" means for a page that is not inside the measure. If
   the owner prefers centring in the *content box* (i.e.
   `gutter + slack_w / 2.0`, which is `slack_to="split"`), say so — but
   then `letterbox` and `slack_to="split"` coincide for these pages,
   which is the collision this branch exists to avoid. **Chosen: centre
   in the cell.**

6. **`_place_page`, line 490-496**, replace the `Placement` construction's
   scale arguments:

   ```python
       placement = Placement(
           scale_x=page_scale,
           scale_y=page_scale,
           tx=tx,
           ty=ty,
           rotate_deg=rotate_deg,
       )
   ```

7. **`deckle/core/models.py:305-306`**, replace the field's docs with

   ```python
       :ivar landscape_policy: what to do with a page whose orientation
           disagrees with its cell -- landscape content in a portrait cell.

           ``rotate`` (the default) turns it 90 degrees so it fills the
           cell, and warns. ``scale`` leaves it upright and fits it to the
           content box like every other page, so it fills the box's width
           and sits centred vertically. ``letterbox`` places it at **1:1**,
           centred in its cell, and excludes it from ``document_scale``:
           the page comes out the size it was drawn, one wide plate no
           longer shrinks the whole book, and a page too big for its cell
           overflows and is reported as ``clipped_by_page``.

           ``letterbox`` is therefore the **only** setting under which two
           pages of one document are reproduced at different scales, and
           only ever for the pages whose orientation disagrees with the
           book's. See ``document_scale`` for why that is otherwise
           forbidden.
   ```

8. **`document_scale`'s docstring**, append to the paragraph beginning
   "Taking the minimum means no page overflows":

   ```
       The one exception is ``landscape_policy="letterbox"``, under which a
       page whose orientation disagrees with its cell is placed at 1:1 and
       so constrains nothing. That is a deliberate, narrow break of the
       uniform-scale rule: the rule exists so body text does not change
       size mid-book, and a page that is sideways to the book is not body
       text.
   ```

9. **`deckle/app/views/layout_panel.py:1060-1066`**, replace the tooltip:

   ```python
        self.landscape_policy_combo.setToolTip(
            "What to do with a landscape page in a portrait book.\n\n"
            "rotate: turn it 90 degrees so it fills the page (the default "
            "-- the reader turns the book).\n"
            "scale: shrink it to fit upright, like every other page.\n"
            "letterbox: print it at actual size, centred, with bands "
            "around it -- and let it overflow if it does not fit. The "
            "only setting that reproduces a page at 1:1."
        )
   ```

10. **`docs/api/`** — nothing to add.

### 3B.3 What does not change

- Every document whose pages all agree with their cell, under any policy.
- Every document under `rotate` or `scale`.
- `GUTTER_SHIFT_PLACEMENT_PIN` — its settings use the default `rotate`.

---

## 4. Tests

### 4A. BRANCH A tests

**`tests/test_models.py::test_landscape_policy_offers_two_values`**
`typing.get_args(typing.get_type_hints(LayoutSettings)["landscape_policy"])
== ("rotate", "scale")`.
Unfixed: the tuple has three members.

**`tests/test_layout_field_types.py::test_letterbox_is_no_longer_accepted_as_a_value`**
The module's existing `OUTSIDE_LITERAL` list (`:63-70`) gains
`pytest.param("landscape_policy", "letterbox", id="landscape-policy-letterbox")`?
**No** — `letterbox` must *not* be refused; step 3A.3 migrates it. Write
instead:

**`tests/test_project_io.py::test_a_legacy_letterbox_project_loads_as_scale`**
Write a `.deckle` with `"landscape_policy": "letterbox"`, `load_project`
it, and assert `project.layout.landscape_policy == "scale"` and that no
`UnknownLayoutFieldsWarning` was emitted (it is a known *key* with a
retired *value*).
Unfixed: passes trivially — the value loads as itself. It is the guard on
step 3, so write it and watch it fail *after* step 1 and before step 3.
**Order matters here**: do step 1, run it, see
`StoredValueError: layout setting 'landscape_policy' is 'letterbox', but
this build of Deckle accepts one of 'rotate', 'scale'`, then do step 3.

**`tests/test_ui_surface.py` (or `tests/test_layout_panel_refresh.py`)
`::test_a_legacy_letterbox_project_shows_scale_in_the_combo`**
Load a project as above through the panel and assert
`panel.landscape_policy_combo.currentText() == "scale"`. Headless with
`QT_QPA_PLATFORM=offscreen`.
Unfixed: `"letterbox"`.

**`tests/test_layout.py::test_the_two_non_rotating_spellings_are_gone`**
Assert `LANDSCAPE_POLICIES` (imported from
`deckle.app.views.layout_panel`) has no `"letterbox"` — **no**, that
imports Qt into a core test. Put it in `tests/test_ui_surface.py`
instead, which already imports the panel.

**Edited, deliberately:** `tests/test_layout_panel_refresh.py:208, 220`
(§3A step 8). Note it in the commit message.

### 4B. BRANCH B tests

All in `tests/test_layout.py`, extending the `# ----- landscape` block.
`make_page` takes a `size` (`:41`) and `WIDE = (900.0, 600.0)` and
`DIGEST = (432.0, 648.0)` already exist (`:37-38`).

**`test_letterbox_places_a_disagreeing_page_at_one_to_one`**
`impose([make_page(0, size=WIDE), make_page(1, size=DIGEST)],
settings(landscape_policy="letterbox", gutter_pt=18.0, margin_outer_pt=18.0))`.
Assert the WIDE page's `placement.scale_x == 1.0` exactly (not
`approx`) and `scale_y == 1.0`.
Unfixed: it carries the document scale.

**`test_letterbox_and_scale_are_not_the_same_policy`**
Impose the same two pages under `"scale"` and under `"letterbox"`;
assert the two placement lists differ. This is the test whose *absence*
is the bug.
Unfixed: `assert [...] != [...]` fails — they are equal.

**`test_letterbox_does_not_shrink_the_rest_of_the_book`**
Impose `[make_page(0, size=WIDE)] + make_pages(3, size=DIGEST)` under
`"letterbox"`, and separately impose `make_pages(3, size=DIGEST)` alone
under `"letterbox"`. Assert the DIGEST pages' `scale_x` is the same in
both — i.e. the wide plate constrained nothing.
Unfixed: the WIDE page drags the document scale down, so the two differ.

**`test_scale_does_shrink_the_rest_of_the_book`**
The control: the same comparison under `"scale"` asserts the two
**differ**. Passes today and after; it is what makes the previous test
mean something.

**`test_a_letterboxed_page_is_centred_in_its_cell`**
One `make_page(0, size=DIGEST)` in a **landscape** cell — use
`settings(paper=(792.0, 612.0), landscape_policy="letterbox",
gutter_pt=54.0, margin_outer_pt=18.0)` so a portrait page disagrees with
a landscape sheet. Assert `placement.tx == pytest.approx((792 - 432) / 2)`
and `placement.ty == pytest.approx((612 - 648) / 2)` — negative, because
648 > 612 and 1:1 overflows. Assert a `clipped_by_page` warning fires.
Unfixed: the page is fitted and there is no overflow.

**`test_letterbox_ignores_slack_to`**
The same page under all three `slack_to` values; assert the three
`placement.tx` values are identical.
Unfixed: they differ.

**`test_letterbox_leaves_agreeing_pages_alone`**
`impose(make_pages(4, size=DIGEST), settings(landscape_policy="letterbox"))`
— every page is portrait in a portrait cell, so nothing is letterboxed.
Assert one distinct scale and that it equals the scale the same document
gets under `"scale"`.
Passes today and after; the guard that the branch does not fire on
agreeing pages.

**`tests/test_layout_saddle.py::test_letterbox_is_judged_against_the_folio_cell`**
`impose(make_pages(4, w=792.0, h=612.0), settings(landscape_policy="letterbox"))`
— landscape sources, folio, landscape sheet, portrait cells. Assert every
placed leaf has `scale_x == 1.0` and that a `clipped_by_page` warning
fires (a 792pt-wide page at 1:1 does not fit a 396pt cell).
Unfixed: the leaves are fitted at the document scale.

**`tests/test_imposition_properties_settings.py`** — note in §6B that
`test_a_crop_never_pushes_content_off_the_sheet` and
`test_crop_trim_and_folio_together_keep_content_on_the_sheet` assert
content stays on the sheet. Neither generates a `landscape_policy`, so
both keep using the default `"rotate"` and stay green. **Do not** add
`letterbox` to their strategies: under B, overflowing is legal and the
property is deliberately false.

## 5. Acceptance

### 5A. BRANCH A

| Check | Command |
|---|---|
| the value is gone from the model | `! grep -n '"letterbox"' deckle/core/models.py` (matches `deckle/core/models.py:333` today) |
| and from the panel's list | `grep -n 'LANDSCAPE_POLICIES: tuple\[str, ...\] = ("rotate", "scale")' deckle/app/views/layout_panel.py` |
| and from the tooltip | `! grep -n "letterbox:" deckle/app/views/layout_panel.py` |
| the migration exists | `grep -n 'landscape_policy"\] = "scale"' deckle/core/project_io.py` |
| the only remaining mentions are the migration and the decisions log | `grep -rn letterbox deckle/ \| grep -v project_io.py \| grep -v models.py` prints nothing but the docstring |
| a legacy project still opens | `.venv/bin/python -c "import json,tempfile,os; from deckle.core.project_io import load_project; d=tempfile.mkdtemp(); p=os.path.join(d,'j.deckle'); json.dump({'version':1,'pages':[],'layout':{'paper':[612.0,792.0],'gutter_pt':36.0,'binding_edge':'left','landscape_policy':'letterbox'}}, open(p,'w')); print(load_project(p, check_sources=False).layout.landscape_policy)"` — must print `scale` |
| the new tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_models.py tests/test_project_io.py tests/test_ui_surface.py -q --no-header -p no:cacheprovider -k "letterbox or landscape_policy"` |
| every placement is unchanged | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py tests/test_layout.py tests/test_layout_saddle.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

### 5B. BRANCH B

| Check | Command |
|---|---|
| the policy is read in more than one place | `test "$(grep -c 'landscape_policy' deckle/core/layout.py)" -ge 3` (2 today) |
| the 1:1 scale exists | `grep -n "page_scale = 1.0 if letterboxed else scale" deckle/core/layout.py` |
| `letterbox` is judged against the cell | `.venv/bin/python -c "import inspect, deckle.core.layout as m; s=inspect.getsource(m._is_letterboxed); assert 'settings.paper' not in s, 'letterbox must be judged against the cell'"` |
| the repro from §1 now differs | paste §1's fenced block; it must print `scale == letterbox: False` |
| the new layout tests pass | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout.py -q --no-header -p no:cacheprovider -k letterbox` |
| the new saddle test passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout_saddle.py -q --no-header -p no:cacheprovider -k letterbox` |
| **the placement pin has not moved** | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_cell_geometry.py -q --no-header -p no:cacheprovider` |
| `rotate` and `scale` are untouched | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_layout.py tests/test_layout_saddle.py tests/test_golden_pinebox.py -q --no-header -p no:cacheprovider` |
| the properties still hold | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_imposition_properties.py tests/test_imposition_properties_settings.py -q --no-header -p no:cacheprovider` |
| the full suite passes | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --no-header -p no:cacheprovider` |

## 6. Out of scope

**Both branches:**

- **B5** — the scale pass judging rotation against the paper. It edits
  `_rotates_to_portrait`/`_fitted_dims`, which Branch B also touches
  (step 2) and Branch A does not. Land B5 first; Branch B's step 2 then
  reads `_fitted_dims(slot, settings, cell)`.
- **B1** — the user's own rotation. Independent of both branches.
- **F5** — a `--landscape-policy` CLI flag. Whichever branch wins, F5
  offers the surviving set; do not add the flag here.
- **The per-page landscape override** the MVP design mentions
  (`docs/plans/2026-08-04-deckle-bookbinding-print-prep-design.md:385-386`,
  "set globally with per-page override"). It has never existed and is not
  added here.
- **`docs/specs/deckle-mvp/*`** — historical records of a delivered spec.
  Not edited by either branch.

**Branch A only:**

- Renaming `"scale"` to `"fit"`. Rejected in §3A.1.
- Removing `landscape_policy` altogether. `rotate` versus not-rotate is a
  real choice with real different output; only the third value is empty.

**Branch B only:**

- Making `letterbox` the default. It is not; `rotate` stays.
- Capping the 1:1 scale so it never overflows. That would be
  `min(1.0, fit)`, which §3B.1 rejects as `scale` with a cap.
- Applying `letterbox` to *agreeing* pages. The setting's name and its
  tooltip are both about the disagreeing case.

## 7. decisions.md entry

### 7A. BRANCH A

```
## 2026-09-05 — landscape_policy had three values and two behaviours
- Symptom: `landscape_policy` is `Literal["rotate", "scale", "letterbox"]` and the imposer's only two comparisons are both `== "rotate"`. Verified: imposing one rotated page under `scale` and under `letterbox` produced byte-identical `Placement` lists. The GUI tooltip promised "scale: shrink it to fit upright" and "letterbox: leave it upright with bands above and below" -- which, under a fit rule that fits both dimensions, are the same picture: a landscape page in a portrait box fills the width exactly and the vertical slack is always split.
- Fix: `Literal["rotate", "scale"]`. A `.deckle` carrying `letterbox` is mapped to `scale` in `_layout_from_dict` before `check_values` sees it -- the two meant the same thing, so the migration loses nothing, which is exactly when a silent migration is allowed. `check_values` refuses an out-of-`Literal` value harshly and correctly, so without the mapping every project saved by an earlier build would simply not open.
- Surfaces: This is the third value in a `Literal` that never branched. `docs/decisions.md` has the precedent under *Deleted the scale mode*: "Making `fill_height` scale down would make the two modes byte-for-byte identical, leaving a control that changes nothing." `letterbox` did not even need a change to become identical; it was born that way, from an MVP sub-spec line ("`letterbox` centers without scaling") that was never implemented.
- Watch: A declared-but-not-honoured value is a shape this log has caught five times. The cheap check is a grep for the field name: if the number of comparisons is smaller than the number of values, one of them is decorative. `deckle/core/models.py`'s `LayoutWarning.kind` has the same problem today (B31).
- Commit: <fill in>
```

### 7B. BRANCH B

```
## 2026-09-05 — letterbox became a behaviour instead of a synonym
- Symptom: `landscape_policy` is `Literal["rotate", "scale", "letterbox"]` and the imposer's only two comparisons are both `== "rotate"`. Verified: `scale` and `letterbox` produced byte-identical `Placement` lists. The tooltip promised two behaviours and the code had one.
- Fix: `letterbox` now places a page whose orientation disagrees with its cell at **scale exactly 1.0**, centred in the cell on both axes, and excludes it from `document_scale`. That is `docs/specs/deckle-mvp/sub-spec-3-imposer-layout-engine.md`'s original definition -- "letterbox centers without scaling" -- made precise. It is the only way in Deckle to reproduce a page at 1:1, which matters for a plate whose physical size is the point: a map with a scale bar, a facsimile, a printed pattern. A page too big for its cell overflows and is reported as `clipped_by_page`; that is the price of 1:1 and it is said out loud.
- Surfaces: This is a deliberate, narrow break of the uniform-scale rule. `docs/decisions.md`, *Uniform document-wide scale; per-page scaling resized the text*, exists because body text changed size mid-book on the Traveller Core Rulebook. The pages `letterbox` exempts are by construction not body text -- they are the ones lying sideways to the book -- and every page that agrees with its cell still shares the one document scale. A second effect falls out of the same change: one wide plate no longer drags the whole book's scale down with it.
- Watch: `letterbox` and `scale` were the same code path for the life of the project, and the only reason anyone noticed is that a roadmap pass grepped the field and counted two comparisons against three values. When a `Literal` has more members than the code has branches, one of them is decorative -- and the tooltip will still describe it confidently.
- Commit: <fill in>
```

## 8. Traps

- **`python -m deckle` launches the GUI and blocks.** Use
  `python -m deckle.cli` headless; the panel tests need
  `QT_QPA_PLATFORM=offscreen`.
- **Implement exactly one branch.** They contradict: A removes the value,
  B gives it a meaning. Delete the other section from this file before
  starting so a later reader is not left choosing again.
- **Branch A: removing the `Literal` member breaks every legacy
  `.deckle` unless step 3 lands with it.** `core.schema.check_values`
  raises `StoredValueError` for a value outside a field's `Literal`, and
  it does so deliberately and harshly
  (`deckle/core/schema.py:139-156`: "a value outside a field's declared
  set is the document asking for something this build cannot do -- and
  guessing is how `binding_edge: 'middle'` came to mean `'right'`"). This
  migration is the exception because the two values were provably the
  same behaviour; do not use it as precedent for guessing at any other
  out-of-set value.
- **Branch A: the migration must run before `_check_layout_values`.**
  Placing it after is a no-op — the exception has already been raised.
- **Branch A edits an existing test.**
  `tests/test_layout_panel_refresh.py:208, 220` uses `"letterbox"` as an
  arbitrary distinguishable value. Changing it to `"scale"` is correct
  and must be called out in the commit message, not slipped in.
- **Branch B: `tests/test_cell_geometry.py::GUTTER_SHIFT_PLACEMENT_PIN`
  is a regeneration-is-an-escalation pin.** Its settings use the default
  `rotate`, so it must stay green. If it goes red, `_is_letterboxed` is
  returning True for the default policy — check the first guard.
- **Branch B: `tests/test_imposition_properties_settings.py` asserts
  content stays on the sheet.** Under B, a letterboxed page legally
  overflows. Neither of those property tests generates a
  `landscape_policy`, so both stay green — but **do not** widen their
  strategies to include it "for coverage"; the property is deliberately
  false for that value.
- **Branch B: the exclusion in `document_scale` and the 1:1 in
  `_place_page` must agree.** They are two functions asking the same
  question, which is exactly the shape of B5. `_is_letterboxed` is one
  function called by both for that reason; do not inline either copy.
- **Branch B: `document_scale` returns `1.0` for an empty `scales` list**
  (`deckle/core/layout.py:343`). A document consisting *entirely* of
  letterboxed pages therefore gets `scale = 1.0`, which every page then
  ignores anyway. Correct, and worth a comment.
- **`deckle/core` must not import Qt** (`tests/test_core_purity.py`), and
  `deckle/app/views/layout_panel.py` keeps its pure helpers at module
  level with Qt imported lazily inside functions — follow the file's
  existing shape when editing `LANDSCAPE_POLICIES` and
  `set_landscape_policy`.
