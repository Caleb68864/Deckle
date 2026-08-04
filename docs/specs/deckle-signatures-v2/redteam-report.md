---
type: redteam-report
generated: 2026-08-04
target: "../2026-08-04-deckle-signatures-v2.md"
findings_count: 9
critical: 3
advisory: 6
all_patched: true
---

# Red Team Review: 2026-08-04-deckle-signatures-v2.md

9-role adversarial review of the master spec plus its 13 phase specs.
42 REQ-IDs, 138 typed acceptance criteria, 7 waves.

## CRITICAL (3) — all fixed

### C-1 — `.deckle` cannot survive a `LayoutSettings` field change *(Data/Migration Steward)*

**This was a live data-loss bug in shipped code, not a spec defect.**

`_layout_from_dict` passed every stored key straight into `LayoutSettings(**kwargs)`:

```python
kwargs = dict(data)
kwargs["paper"] = tuple(data["paper"])
return LayoutSettings(**kwargs)
```

So **any** field ever added or removed permanently broke every project file written on the
other side of that change. Deleting `scale_mode` earlier today did exactly that — verified:

```
legacy .deckle with scale_mode: RAISES -> TypeError:
  LayoutSettings.__init__() got an unexpected keyword argument 'scale_mode'
```

Signatures v2 adds **five** more fields (`fold_scheme`, `sheets_per_signature`,
`paper_thickness_pt`, `sewing_stations`, `blank_mode`), so every v2-saved project would have
been unopenable by any build without them, and vice versa.

The design explicitly reasoned that no migration was needed because *"`SheetPlan` is never
persisted"* — true, and irrelevant. **`LayoutSettings` is persisted**, and it is precisely
what this feature changes. The `version` integer was reserved for this and never used.

**Fixed:** `_layout_from_dict` now filters to known fields, falls back to dataclass defaults
for missing ones, and emits a non-fatal `UnknownLayoutFieldsWarning` for extras. Tolerant in
both directions. Four regression tests added covering the retired field, the future field,
the no-spurious-warning case, and a full round trip.

### C-2 — `ContentStreamBuilder` method names were wrong *(Developer)*

The spec named `line_width` and `dashes` and claimed *"confirmed present in the installed
pikepdf"*. Only the **class** had been checked. Enumerating the real API:

```
set_line_width, set_dashes, line, stroke_and_close, append_rectangle, push, pop, build
```

There is no `line_width` and no `dashes`. A worker following SS-09 would have hit an
`AttributeError` on first run. **Fixed:** all 8 names verified by enumeration and written
into the spec, plus a note that `set_stroke_color` takes three floats rather than a `Color`.

### C-3 — Subshell exit inside a `for` loop makes checks pass while printing FAIL *(QA)*

`cmd || (echo "FAIL: …" && exit 1)` inside a `for` loop exits only the **subshell**. The
loop continues and returns its last iteration's status. Verified with a deliberately missing
middle token:

```
subshell form exit = 0   <-- prints FAIL, passes anyway
brace form   exit = 1   <-- correct
```

**Fixed:** converted to `|| { echo "FAIL: …"; exit 1; }` across all specs; none remain.

## ADVISORY (6) — all resolved

| ID | Role | Finding | Resolution |
|---|---|---|---|
| A-1 | Developer | `^\s*(import\|from)\s+PySide6` matches the **legitimately lazy** Qt imports inside app-view functions, failing on correct code | Module-scope checks anchor `^(import\|from)`; `^\s*` retained only for `deckle.core`, where Qt is banned outright |
| A-2 | QA | bare `ruff check` exits **127** — not on PATH in this Git Bash | All rows use `python -m ruff check`. Found independently by two writers |
| A-3 | Integration | `tests/test_print_dialog.py` builds `Sheet(front=OutputPage(...))` and feeds it to `PrintSession`, but appeared in **no** sub-spec's Files list — SS-04's `_hash_plan` change would have reddened it as a phantom SS-04 defect | Added to SS-02's Files with justification |
| A-4 | QA | Positive `[STRUCTURAL]` greps for not-yet-created symbols pass vacuously if the runner tolerates empty selection | `pytest -k <missing>` verified to exit **5**, so they fail loudly; command *forms* probed against existing symbols instead |
| A-5 | Architect | `SheetPlan` carries no `binding_edge`, so `fold_reading_order(plan)` cannot know handedness — REQ-019's both-edges round trip is otherwise unachievable | Keyword-only `binding_edge="left"` added; the positional contract `fold_reading_order(plan)` is unchanged. Escalated rather than inferred from `source_ref.page_index`, which would assume the answer |
| A-6 | Developer | A negative grep naming a symbol broadly matches the **docstring stating the rule** (`marks.py` purity check forbade the substring `pikepdf` anywhere) | Anchored to the construct forbidden. Third occurrence of this pattern in the project |

## Construction-Site Check: clean

Every sub-spec matching a wiring keyword names a concrete call site with both symbol and
file path. SS-11 pins `PreviewView`'s supersede-and-cancel guard at `preview_view.py:499,
516-520` and holds printer enumeration to its single existing call site; SS-12 names the CLI
and integration entry points. No `construction-site-without-caller` findings.

## The risk no review can close

`saddle_order` is hand-written. The vault note states it plainly: *"saddle_order is my own
function, not pikepdf's… the pikepdf half is confirmed, the bindery half is yours."*

The failure mode is that `saddle_order` and `fold_reading_order` encode the **same** wrong
physical assumption and therefore agree — a green round-trip on a book that reads out of
order. SS-05 attacks this three ways (AST walk, standalone one-liner, and a monkeypatch
runtime test that catches indirect reuse the AST cannot see) and enforces independence
structurally by writing `fold_reading_order` from a physical nesting table **before**
`saddle_order` exists in the file.

That is the best software can do. **SS-13 — the physical folded dummy — remains the only
real check**, is `dispatch: manual`, and its phase spec states that every mechanical row can
be green while it is entirely unsatisfied.

## Role Scorecards

Developer: 3 | QA: 3 | End User: 0 | Architect: 1 | Scope Realist: 0 | Security: 0 | SRE: 0 | Data: 1 | Product: 0
