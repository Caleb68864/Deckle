# <ID> — <Title, one line, imperative>

**Roadmap item:** `docs/ROADMAP.md` <ID>
**Depends on:** <IDs, or "—">
**Blocks:** <IDs, or "—">
**Size:** S | M | L
**Decision needed first:** <"none" or the owner decision, quoted from ROADMAP §6>

---

## 1. Context

What is wrong or missing, and why it matters to a person printing a book.
State the concrete failure: the exact user action, and the exact wrong result.
If the finding was verified by running code, include the repro as a fenced
block the implementer can paste (a `python - <<'EOF'` script or a CLI line)
and the output it currently produces.

## 2. Current code

Quote the relevant lines verbatim with `file:line` references, enough that
an implementer who has not read the module can see the defect. Name every
other call site that reads or writes the same value (grep for it and list
them). Name the existing tests that touch this code.

## 3. Change

The precise new behaviour. For code: function signatures, dataclass fields,
return shapes, error types and messages, and a numbered step list in the
order the edits should be made. For each step, the file and the function.
Where a value is chosen (a constant, a message string, a default), state the
value; do not leave it to taste. Where two designs were possible, name the
one chosen and the one rejected in one sentence each.

## 4. Tests

Write these BEFORE the change and confirm they fail for the right reason.
For each: test file, test function name, setup, the assertion in words, and
the expected failure message on the unfixed tree. Reuse the project's
fixtures and helpers (`tests/fixtures/sample.pdf` is a 2-page Letter PDF;
`deckle dummy` makes numbered PDFs; `tests/test_loader.py::_make_pdf` makes
blank ones). GUI tests run headless with `QT_QPA_PLATFORM=offscreen` and
must not need a display.

## 5. Acceptance

A table of mechanical checks. Each row is a shell command that exits 0 when
the criterion holds. Include at minimum: the new tests pass, the full suite
passes, and a grep proving any forbidden construct is absent. Mark any row
that needs a human (hardware, eyes) as `[HUMAN]` with what they look at.

| Check | Command |
|---|---|
| ... | `...` |

## 6. Out of scope

What this spec must NOT do, especially adjacent bugs that are their own
spec. Reference their IDs.

## 7. decisions.md entry

The pre-commit hook refuses any code commit that does not touch
`docs/decisions.md`. Provide the entry, in the house format, ready to paste:

```
## YYYY-MM-DD — <one-line title in the log's voice>
- Symptom: ...
- Fix: ...
- Surfaces: ...
- Watch: ...
- Commit: <fill in>
```

## 8. Traps

Things that will go wrong for an implementer who has not read the whole
module: locks that must not be held, invariants asserted elsewhere, tests
that pin current behaviour and must be updated deliberately, Windows/POSIX
differences, the fact that `python -m deckle` launches the GUI (use
`python -m deckle.cli`).
