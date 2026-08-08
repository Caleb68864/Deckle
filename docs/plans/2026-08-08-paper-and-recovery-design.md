# Paper specification, signature sizing, and autosave recovery

**Date:** 2026-08-08
**Status:** design approved, not implemented

Four pieces of product work, all verifiable without printing anything.
They share one theme: Deckle's engine already knows things the person
using it cannot get at.

---

## Why these four

A hardening review found that the gap between Deckle and something an
everyday user can bind a book with is not robustness — core sits at
91–100% coverage — but reach. Specifically:

- The layout panel exposes 16 of `LayoutSettings`' 20 fields. The four it
  does not are `crop_odd_pt`, `crop_even_pt`, `trim_pt` and
  `signature_lengths`. Cropping is the feature the README calls "what
  keeps type readable at a small trim size", and a GUI-only user cannot
  do it at all.
- Paper thickness is asked for as a caliper in a four-decimal spin box.
  Nobody knows their paper's caliper. Everyone knows its weight, because
  it is printed on the ream wrapper.
- Autosave is written on every edit and flushed on close, and **nothing
  ever offers it back**. The file is a corpse.

The engine already computes fore-edge creep and spine bulk. What is
missing is the inverse — *given this paper, how big should a gathering
be?* — and a way to state the paper without instruments.

---

## 1. What limits a signature

Two constraints, and the suggestion is the smaller of them.

| Constraint | Formula | Binds when |
| --- | --- | --- |
| Fore-edge creep | `(n − 1) × caliper ≤ tolerance` | little or no trim planned |
| Fold bulk at the spine | `2n × caliper ≤ 1.8 mm` | thick paper, or generous trim |

`n` is sheets per signature. A folded gathering of `n` nested sheets has
`2n` layers of paper at the fold, which is what the second constraint
measures.

**Tolerance is `trim_pt` when the binder has set one, else 1.0pt** — the
threshold `schedule._creep_note` already treats as invisible. This is the
load-bearing choice: creep is *absorbed by trimming*, so a binder who
plans to plough the fore-edge can carry far more of it than one who does
not.

Creep alone is not enough. 80gsm paper with a 0.25in trim tolerates 62
sheets before creep is visible, and nobody hand-sews a 62-sheet
gathering. The fold-bulk cap is what makes the answer physical.

### Calibration

The constants were checked against traditional signature sizes rather
than chosen to produce them:

| Paper | Caliper | No trim | With 0.25in trim |
| --- | --- | --- | --- |
| 80gsm copier | 0.104 mm | 4 sheets (16pp) | **8 sheets (32pp)** |
| 100gsm offset | 0.130 mm | 3 (12pp) | 6 (24pp) |
| 120gsm cartridge | 0.156 mm | 3 (12pp) | 5 (20pp) |
| 160gsm card | 0.208 mm | 2 (8pp) | 4 (16pp) |

80gsm landing on a 32-page signature — the standard trade gathering — is
the evidence that 1.8mm is an honest cap rather than a tuned one. Had it
produced 7 or 11, the constant would be wrong.

### What the UI says

The suggestion names **which constraint bound it**, because the remedy
differs: creep-bound means "plan a trim", bulk-bound means "this paper is
thick". A suggestion the binder cannot act on is a number, not advice.

---

## 2. Weight to caliper

`caliper_mm = gsm × bulk / 1000`, where bulk is the paper's specific
volume in cm³/g.

| Type | Bulk |
| --- | --- |
| Copier / office | 1.30 |
| Laser / smooth | 1.25 |
| Offset / book uncoated | 1.30 |
| Bulky book | 1.70 |
| Coated / gloss | 0.90 |

US basis weights convert by basis size, derived rather than tabulated:

| Grade | Basis size | Factor |
| --- | --- | --- |
| Bond / writing | 17 × 22 in | `gsm = lb × 3.760` |
| Text / book | 25 × 38 in | `gsm = lb × 1.480` |
| Cover | 20 × 26 in | `gsm = lb × 2.704` |
| Index | 25.5 × 30.5 in | `gsm = lb × 1.808` |
| Tag | 24 × 36 in | `gsm = lb × 1.627` |

Unit-tested against known pairs — 20lb bond = 75gsm, 70lb text = 104gsm,
65lb cover = 176gsm — so a transcription error in a factor fails rather
than silently shifting every caliper.

**Bulk varies about ±10% between manufacturers**, so a derived caliper is
an estimate and must be presented as one. `spine_width_pt` already
reports a range for exactly this reason; the same honesty carries here.
The preset list shows its caliper so a binder with calipers can see
whether the estimate matches their stock.

---

## 3. Autosave recovery

`_on_close_event` flushes the autosave, so **the file exists after every
clean quit**. Detection cannot be "does it exist".

Detection is **mtime**: offer recovery when the autosave is newer than
the project file.

| Situation | Result |
| --- | --- |
| Crash mid-edit | autosave newer → offer |
| Closed without saving | autosave newer → offer (correct: those edits are real) |
| Saved, then closed | project newer → silent |
| Never saved (`project_path is None`) | no autosave exists → silent |

This needs no dirty flag and no new state, and it gets the
close-without-saving case right by accident of being correct about the
crash case.

The decision is a pure function taking two paths and returning an offer
or `None`; only the dialog is Qt. That is how `main.py` already separates
its logic, and it is what makes this testable in a suite that cannot open
a window.

Declining must **delete** the autosave, or the prompt returns on every
subsequent open and trains the user to dismiss it.

---

## 4. Reaching the last four settings

Wiring, following the panel's existing `set_*` pure-function pattern:

- **Crop** — odd and even insets, four edges each, plus an auto-crop
  button that fills them from `render.auto_crop_insets`.
- **Trim** — one depth, in the panel's current unit.
- **Gatherings** — a text field taking `10,10,8`, validated against the
  imposed sheet count with the message `split_signatures_at` already
  produces.

---

## Components

| Module | Contents |
| --- | --- |
| `deckle/core/paper.py` *(new)* | presets, `caliper_from_weight`, `suggest_sheets_per_signature` |
| `deckle/app/main.py` | pure recovery decision + prompt on open |
| `deckle/app/views/layout_panel.py` | paper picker, crop, trim, gatherings |
| `deckle/cli.py` | `--paper-weight`/`--paper-type` as an alternative to `--paper-thickness` |

`paper.py` is core and Qt-free, so the CLI reaches it too — a binder
scripting a job should not have to compute a caliper by hand either.

## Testing

Everything above is verifiable without a printer.

- Weight conversions against published pairs.
- Suggestion against the calibration table, including which constraint
  bound each answer.
- Recovery decision across all four mtime situations.
- Panel wiring through the existing pure `set_*` functions, which the
  suite already exercises headlessly.

The one thing tests cannot settle is whether 1.8mm is the right cap. That
is a judgement calibrated against traditional signature sizes, and it is
recorded here so it can be argued with rather than discovered in the
code.
