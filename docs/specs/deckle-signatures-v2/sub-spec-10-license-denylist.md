---
type: phase-spec
master_spec: "../2026-08-04-deckle-signatures-v2.md"
sub_spec_id: SS-10
sub_spec_number: 10
title: "License-audit denylist, and the pdfimpose oracle that cannot become a dependency"
depends_on: []
date: 2026-08-04
---

# SS-10 — License-audit denylist, and the `pdfimpose` oracle that cannot become a dependency

## Context

`pdfimpose` is genuinely good at the one thing this feature needs checked. It is one of only
two surveyed tools that split signatures correctly, and it never rescales content — so its
source-page → (sheet, side, cell) matrix is a real oracle for Deckle's own. It is also
**AGPL-3.0, and it pulls AGPL PyMuPDF.** Deckle is MIT. The MVP spec names this as *a live
temptation, not a hypothetical*.

The resolution is already decided and is not open for re-litigation
(`docs/specs/2026-08-04-deckle-signatures-v2.md`, *Decided — no further escalation*):
**`pdfimpose` is a development-time oracle only, in a throwaway virtualenv, denylisted in the
license audit so it can never become a dependency.**

**The ordering is the point.** The denylist lands **before** the oracle exists. A developer who
reads `tools/README.md`, gets excited, and runs `pip install pdfimpose` in the project venv
must get a **red test** — not a shipped AGPL dependency discovered at release time. This
sub-spec has `depends_on: []` precisely so it can land first, in the earliest wave, before
anyone has a reason to install anything.

**Read before writing code:**

1. `tests/test_license_audit.py` in full — **follow its existing shape**; this sub-spec extends
   it, it does not rewrite it.
2. `docs/specs/2026-08-04-deckle-signatures-v2.md` — REQ-033, REQ-034, REQ-040, and the
   Must-Nots ("MUST NOT introduce AGPL"; "MUST NOT add an external runtime binary").
3. `docs/specs/deckle-signatures-v2/contracts.yaml`, the `license_denylist` contract.
4. `docs/decisions.md`, *Negative-assertion acceptance criteria must exit 0 when the pattern is
   absent* — this sub-spec is almost entirely negative assertions.

### What the existing audit already does, and what stays

`tests/test_license_audit.py` (129 lines, 3 tests, currently green) walks Deckle's **own**
dependency closure — BFS from `pyproject.toml`'s `[project].dependencies` through each
distribution's `requires`, via `importlib.metadata` — and asserts two things:

| Test | Line | What it checks |
|---|---|---|
| `test_dependency_closure_is_non_empty` | 98 | A sanity guard on the audit itself. An empty closure would make every assertion below **vacuously true** and silently stop auditing anything. Keep it. |
| `test_no_agpl_dependency` | 105 | `"agpl"` substring over `License` field + `Classifier` metadata. Keep it **separate** — it catches AGPL distributions nobody thought to name. |
| `test_no_pymupdf_or_fitz_dependency` | 114 | The **dual** check: distribution name in `FORBIDDEN_DISTRIBUTIONS`, **or** an intersection with that distribution's `top_level.txt` module names. The `top_level.txt` half is what catches PyMuPDF hiding behind the import name `fitz`. Keep both halves. |

The audit is deliberately scoped to Deckle's own closure rather than every distribution
installed on the machine — an unrelated package sitting in the same environment for some other
project must never fail this project's build. **Preserve that scoping**; it is what makes the
audit usable rather than something people learn to skip.

### The one-line change, and the test that proves it is wired

`tests/test_license_audit.py:33` currently reads:

```python
FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz"}
```

It becomes:

```python
FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz", "pdfimpose", "cpdf"}
```

A one-line edit is trivially reviewable and trivially **wrong-in-a-silent-way**: a denylist
that is declared but never consulted looks identical in a diff to one that works. So this
sub-spec's real deliverable is the *second* test — one that injects a fake distribution named
`pdfimpose` into a closure and asserts the offender detection **catches it**. That requires
extracting the assertion body into a pure, testable helper. This is the same reasoning as
`test_dependency_closure_is_non_empty`: a guard on the guard.

### Why `cpdf` is on the list too

`cpdf` (Coherent PDF) is not AGPL — it is a **commercial** licence with a non-commercial-only
free tier. It is on the denylist for the same structural reason: it is a tool that is tempting
to reach for during imposition work and that Deckle cannot ship. Grouping it with the AGPL
entries keeps one list rather than two; the separate `test_no_agpl_dependency` still carries
the AGPL-specific meaning.

### `tools/` — a directory that cannot become part of the product

`tools/oracle_diff.py` is a manual, development-time script:

- It lives **outside `tests/`**, so pytest never collects it (`pyproject.toml` sets
  `testpaths = ["tests"]`).
- It is **absent from `pyproject.toml`** — neither a dependency nor packaged
  (`[tool.setuptools.packages.find] include = ["deckle*"]` already excludes it from the wheel).
- It is **imported by no shipped or tested module**, enforced by a negative grep over
  `deckle/` and `tests/`.
- It imports `pdfimpose` **inside a function body**, never at module scope, so merely opening
  or linting the file cannot pull an AGPL import into anything.

What it does: drives
`pdfimpose.schema.saddle.impose(files, output, signature=(2, 1), group=N, bind="left")` on a
numbered fixture and diffs pdfimpose's resulting source-page → (sheet, side, cell) matrix
against Deckle's `SaddleStitchStrategy`. `pdfimpose`'s `impose()` accepts `io.BytesIO` at both
ends, so the diff needs no temp files.

**It is an oracle for the arithmetic only, and it is not the gate.** The gate is SS-13 — a
physical folded dummy. `C:\Users\CalebBennett\Documents\Notes\Caleb's Vault\Software\pikepdf\pikepdf - Imposition and Signature Recipe.md`
says it plainly: *"the pikepdf half is confirmed, the bindery half is yours… Validate the fold
order against a physical folded dummy before trusting it for a real print run."* Agreement with
`pdfimpose` is corroboration, not proof.

## Provides

| Symbol | Shape | Consumed by |
|---|---|---|
| `FORBIDDEN_DISTRIBUTIONS` | `{"pymupdf", "fitz", "pdfimpose", "cpdf"}` in `tests/test_license_audit.py` | SS-12 (the four-gates command) |
| `_forbidden_offenders(closure)` | pure helper; the denylist logic, now testable | `tests/test_license_audit.py` itself |
| `tools/oracle_diff.py` | manual dev script; **no importers, ever** | humans, in a throwaway venv |
| `tools/README.md` | states the AGPL rule and the by-design failure | humans |

## Requires

| From | Symbol | Why |
|---|---|---|
| — | nothing | `depends_on: []`. This sub-spec must be able to land in the first wave, before anyone has a reason to install the oracle. |
| SS-08 (optional, later) | `SaddleStitchStrategy` | `tools/oracle_diff.py` compares against it. Write the script so it **fails with a clear message** rather than an `ImportError` traceback if the strategy is not present yet — the script is never run by CI, so this ordering costs nothing. |

## Implementation Steps

Each step is 2–10 minutes: write the failing test, run it and see it fail, implement the
minimum, run it green, commit.

### Step 1. Failing test: the denylist names the oracle

Add to `tests/test_license_audit.py`:

```python
def test_denylist_names_every_undistributable_tool():
    assert {"pymupdf", "fitz", "pdfimpose", "cpdf"} <= FORBIDDEN_DISTRIBUTIONS
```

Run `python -m pytest tests/test_license_audit.py -q -k denylist_names`. Expect failure:
`pdfimpose` and `cpdf` are absent.

### Step 2. Extend the set, green

Edit line 33 to `FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz", "pdfimpose", "cpdf"}` and
update the comment above it to name the two reasons (AGPL; commercial-licensed) rather than
only PyMuPDF. Re-run; green. Commit:
`test(SS-10): denylist pdfimpose and cpdf alongside pymupdf and fitz`.

### Step 3. Failing test: the denylist is wired, not merely declared

This is the sub-spec's real deliverable. A declared-but-unconsulted denylist is invisible in a
diff.

```python
class _FakeDist:
    """Minimal importlib.metadata.Distribution stand-in for the audit's two reads."""
    def __init__(self, top_level: str = ""):
        self._top_level = top_level
    def read_text(self, name):
        return self._top_level if name == "top_level.txt" else None


def test_denylist_catches_a_distribution_by_name():
    closure = {"pikepdf": _FakeDist(), "pdfimpose": _FakeDist()}
    assert _forbidden_offenders(closure) == ["pdfimpose"]


def test_denylist_catches_a_distribution_by_its_module_name():
    # PyMuPDF ships under the distribution name "PyMuPDF" but imports as "fitz";
    # the top_level.txt half of the dual check is what catches that.
    closure = {"innocuously-named": _FakeDist(top_level="fitz\n")}
    assert _forbidden_offenders(closure) == ["innocuously-named"]
```

Run `python -m pytest tests/test_license_audit.py -q -k denylist_catches`. Expect
`NameError: _forbidden_offenders`.

### Step 4. Extract the helper, keeping the dual check identical

Move the body of `test_no_pymupdf_or_fitz_dependency` into a module-level pure function,
leaving the existing test as a thin caller so its meaning and name are unchanged:

```python
def _forbidden_offenders(closure) -> list[str]:
    """Names in ``closure`` that are denylisted, by distribution name OR module name."""
    offenders = []
    for name, dist in closure.items():
        if name in FORBIDDEN_DISTRIBUTIONS:
            offenders.append(name)
            continue
        if FORBIDDEN_DISTRIBUTIONS & _top_level_names(dist):
            offenders.append(name)
    return offenders


def test_no_pymupdf_or_fitz_dependency(dependency_closure):
    offenders = _forbidden_offenders(dependency_closure)
    assert not offenders, f"forbidden AGPL PDF tooling present in dependency closure: {offenders}"
```

Do **not** touch `_dependency_closure`, `_license_text`, `_top_level_names`,
`test_no_agpl_dependency` or `test_dependency_closure_is_non_empty`. Run the whole file green.
Commit: `test(SS-10): prove the denylist is wired by injecting a fake distribution`.

### Step 5. Failing check: the oracle is not declared as a dependency

Run, from the repo root:

```bash
! grep -n "pdfimpose\|cpdf\|pymupdf\|fitz" pyproject.toml \
  || (echo "FAIL: AGPL/oracle distribution declared as a dependency" && exit 1)
```

This already exits 0 on the current tree. Run it now anyway and record the result — it is the
check that must still exit 0 *after* `tools/` exists, and running it before is how you know a
later failure is a real regression.

### Step 6. Create `tools/README.md`

Its **first paragraph** must state, unambiguously:

- `tools/` is **development-only**. Nothing in it is shipped, packaged, imported by
  `deckle/`, or collected by pytest.
- `pdfimpose` is **AGPL-3.0** and pulls AGPL PyMuPDF. Deckle is MIT.
- Install it **only in a throwaway virtualenv**, never the project venv.
- Installing it in the project venv **fails `tests/test_license_audit.py` by design.** That is
  the denylist working, not a broken test — do not "fix" it by editing
  `FORBIDDEN_DISTRIBUTIONS`.

Then document the throwaway-venv invocation and what the diff means, including the caveat that
agreement with `pdfimpose` corroborates the arithmetic but does **not** substitute for SS-13's
physical folded dummy.

Commit: `docs(SS-10): tools/ is development-only and pdfimpose is AGPL`.

### Step 7. Create `tools/oracle_diff.py` with the import quarantined

```python
"""Development-time oracle: diff Deckle's folio imposition against pdfimpose.

Run MANUALLY, in a THROWAWAY virtualenv. pdfimpose is AGPL-3.0; installing it
into the project venv fails tests/test_license_audit.py by design. This module
is imported by nothing in deckle/ or tests/, and the AGPL import below lives
inside a function body so opening or linting this file pulls in nothing.
"""

def _load_oracle():
    try:
        from pdfimpose.schema import saddle
    except ImportError:
        raise SystemExit(
            "pdfimpose is not installed. Install it in a THROWAWAY virtualenv, "
            "never the project venv -- it is AGPL-3.0 and the project's license "
            "audit denylists it by design."
        )
    return saddle
```

**Anchoring note for this file specifically.** The check that the AGPL import is *not* at
module scope must anchor at **column 0** — `^(import|from)\s+pdfimpose` — because the legal,
quarantined form is *indented inside a function body*. The usual `^[[:space:]]*(import|from)`
form matches that indented line and therefore **fails on correct code**; this was confirmed by
running both forms against a stub at authoring time.

The comparison itself: build the source-page → (sheet, side, cell) matrix from
`saddle.impose(files, output, signature=(2, 1), group=N, bind="left")` (both ends `io.BytesIO`,
so no temp files) and from `SaddleStitchStrategy().impose(...)`, then print a per-slot diff.
Import `deckle.core.layout` **inside a function too**, and exit with a clear message if
`SaddleStitchStrategy` does not exist yet — SS-10 may land before SS-08.

### Step 8. Confirm the oracle cannot leak

```bash
! grep -rn "oracle_diff" deckle/ tests/ \
  || (echo "FAIL: the dev-time oracle is imported by shipped or tested code" && exit 1)
! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(pdfimpose|fitz|cpdf)" deckle/ tests/ \
  || (echo "FAIL: AGPL oracle imported by shipped or tested code" && exit 1)
! grep -rnE "^[[:space:]]*(import|from)[[:space:]]+tools" deckle/ tests/ \
  || (echo "FAIL: dev-only tools package imported by shipped or tested code" && exit 1)
```

All three exit 0 on the current tree (verified at authoring time) and must still exit 0 after
`tools/` exists. Note the **anchored** form of the last two: the unanchored
`! grep -rn "pdfimpose" tests/` would match the `FORBIDDEN_DISTRIBUTIONS` literal this
sub-spec just added, and would therefore fail on correct code. Anchor to
`^\s*(import|from)\s+`.

Commit: `feat(SS-10): tools/oracle_diff.py, quarantined behind a function-body import`.

### Step 9. Full gate, then commit

```bash
python -m pytest tests/test_license_audit.py -q
python -m pytest -q
python -m ruff check deckle tests tools
git add -A && git commit -m "feat(SS-10): license denylist and the quarantined pdfimpose oracle"
```

`python -m ruff check deckle tests` exits 0 on the current tree; the `tools` argument becomes
valid only once step 6/7 have created the directory.

## Interface Contracts

### FORBIDDEN_DISTRIBUTIONS
- Direction: SS-10 → SS-12 (the four-gates command)
- Owner: SS-10 (`contracts.yaml: license_denylist`)
- Shape: `FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "fitz", "pdfimpose", "cpdf"}` in
  `tests/test_license_audit.py`. Consulted by **distribution name OR** by intersection with
  that distribution's `top_level.txt` module names. The separate AGPL substring test over
  license text and classifiers remains independent and unmodified.

### _forbidden_offenders
- Direction: internal to `tests/test_license_audit.py`
- Owner: SS-10
- Shape: `_forbidden_offenders(closure: Mapping[str, Distribution]) -> list[str]`. Pure over
  its argument — no `importlib.metadata` lookups of its own — so a synthetic closure can prove
  the denylist is wired rather than merely declared. Reads only `dist.read_text("top_level.txt")`
  (via `_top_level_names`), which is why a two-line stub suffices as a fake distribution.

### tools/oracle_diff.py
- Direction: none. **This module has no consumers and must never acquire one.**
- Owner: SS-10
- Shape: a `__main__`-style script outside `tests/`, absent from `pyproject.toml`, importing
  `pdfimpose` (and `deckle.core.layout`) **inside function bodies only**, printing a clear
  install message on `ImportError` rather than raising a traceback.

### tools/README.md
- Direction: SS-10 → humans
- Owner: SS-10
- Shape: first paragraph states that `tools/` is development-only, that `pdfimpose` is
  AGPL-3.0, that it belongs only in a throwaway virtualenv, and that installing it in the
  project venv **fails `tests/test_license_audit.py` by design**.

## Verification Commands

```bash
python -m pytest tests/test_license_audit.py -q
python -m pytest tests/test_core_purity.py -q
python -m pytest -q
python -m ruff check deckle tests tools
```

## Checks

Every command exits **0** when the criterion passes. Rows marked *(pre-verified)* were executed
against the working tree at authoring time and confirmed to exit 0 — see `docs/decisions.md`,
*Negative-assertion acceptance criteria must exit 0 when the pattern is absent*.

| Criterion | Type | Command |
|---|---|---|
| Denylist names all four distributions | [STRUCTURAL] | `grep -qE 'FORBIDDEN_DISTRIBUTIONS = \{.*"pdfimpose".*\}' tests/test_license_audit.py \|\| (echo "FAIL: pdfimpose not on the denylist" && exit 1)` |
| `cpdf` on the denylist | [STRUCTURAL] | `grep -qE 'FORBIDDEN_DISTRIBUTIONS = \{.*"cpdf".*\}' tests/test_license_audit.py \|\| (echo "FAIL: cpdf not on the denylist" && exit 1)` |
| Existing AGPL substring test retained *(pre-verified)* | [STRUCTURAL] | `grep -q "def test_no_agpl_dependency" tests/test_license_audit.py \|\| (echo "FAIL: the AGPL substring test was removed" && exit 1)` |
| Empty-closure guard retained *(pre-verified)* | [STRUCTURAL] | `grep -q "def test_dependency_closure_is_non_empty" tests/test_license_audit.py \|\| (echo "FAIL: the vacuous-pass guard was removed" && exit 1)` |
| `top_level.txt` half of the dual check retained *(pre-verified)* | [STRUCTURAL] | `grep -q "_top_level_names" tests/test_license_audit.py \|\| (echo "FAIL: module-name half of the dual check removed" && exit 1)` |
| Denylist is wired, not merely declared | [STRUCTURAL] | `python -m pytest tests/test_license_audit.py -q -k denylist_catches \|\| (echo "FAIL: denylist declared but not consulted" && exit 1)` |
| Audit suite green, at least 3 tests | [MECHANICAL] | `python -m pytest tests/test_license_audit.py -q --collect-only 2>/dev/null \| grep -qE "^([3-9]\|[0-9]{2,}) tests? collected" \|\| (echo "FAIL: fewer than 3 license-audit tests" && exit 1)` |
| Audit suite passes | [MECHANICAL] | `python -m pytest tests/test_license_audit.py -q \|\| (echo "FAIL: license audit red" && exit 1)` |
| No oracle or AGPL distribution declared as a dependency *(pre-verified)* | [MECHANICAL] | `! grep -n "pdfimpose\|cpdf\|pymupdf\|fitz" pyproject.toml \|\| (echo "FAIL: AGPL/oracle distribution declared as a dependency" && exit 1)` |
| Oracle imported by no shipped or tested module *(pre-verified)* | [MECHANICAL] | `! grep -rn "oracle_diff" deckle/ tests/ \|\| (echo "FAIL: the dev-time oracle is imported by shipped or tested code" && exit 1)` |
| No AGPL import in shipped or tested code (anchored) *(pre-verified)* | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+(pdfimpose\|fitz\|cpdf)" deckle/ tests/ \|\| (echo "FAIL: AGPL oracle imported by shipped or tested code" && exit 1)` |
| No AGPL import, MVP form *(pre-verified)* | [MECHANICAL] | `! grep -rn "PyMuPDF\|import fitz\|import pdfimpose" deckle/ tests/ \|\| (echo "FAIL: AGPL import present" && exit 1)` |
| `tools` not imported by shipped or tested code *(pre-verified)* | [MECHANICAL] | `! grep -rnE "^[[:space:]]*(import\|from)[[:space:]]+tools" deckle/ tests/ \|\| (echo "FAIL: dev-only tools package imported by shipped or tested code" && exit 1)` |
| `tools/oracle_diff.py` exists outside `tests/` | [STRUCTURAL] | `test -f tools/oracle_diff.py \|\| (echo "FAIL: tools/oracle_diff.py missing" && exit 1)` |
| Oracle's AGPL import is not at module scope | [MECHANICAL] | `! grep -nE "^(import\|from)[[:space:]]+pdfimpose" tools/oracle_diff.py \|\| (echo "FAIL: pdfimpose imported at module scope" && exit 1)` |
| Oracle prints an install message on ImportError | [MECHANICAL] | `grep -q "throwaway" tools/oracle_diff.py \|\| (echo "FAIL: no throwaway-venv guidance on ImportError" && exit 1)` |
| README states the AGPL rule | [STRUCTURAL] | `grep -qi "AGPL" tools/README.md \|\| (echo "FAIL: README does not name the AGPL licence" && exit 1)` |
| README states the throwaway-venv rule | [STRUCTURAL] | `grep -qi "throwaway" tools/README.md \|\| (echo "FAIL: README does not require a throwaway virtualenv" && exit 1)` |
| README states the failure is by design | [STRUCTURAL] | `grep -qi "by design" tools/README.md \|\| (echo "FAIL: README does not say the audit failure is by design" && exit 1)` |
| Full suite still green | [MECHANICAL] | `python -m pytest -q \|\| (echo "FAIL: full suite red" && exit 1)` |
| Lint clean including `tools` | [MECHANICAL] | `python -m ruff check deckle tests tools \|\| (echo "FAIL: ruff findings" && exit 1)` |

**Note on anchoring.** After this sub-spec lands, `tests/test_license_audit.py` legitimately
*contains the strings* `pdfimpose` and `cpdf` — that is the denylist. Any negative grep over
`tests/` must therefore be anchored to `^\s*(import|from)\s+`, or it will fail on correct code.
The same hazard has already been realised twice in this repo (`deckle/cli.py:32-33`;
`deckle/core/models.py:5`).

**Note on `ruff`.** Invoked as `python -m ruff check`; bare `ruff` is not on the Git Bash
`PATH` in this environment (verified: exit 127 bare, exit 0 via `python -m`).
