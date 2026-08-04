# Test fixtures

## `sample.pdf`

A small, checked-in PDF used by the fast unit and integration tests
(`tests/test_loader.py`, `tests/test_integration.py`, etc.). No setup
required.

## `Pinebox_Middle_School.pdf` (golden fixture, not checked in)

`tests/test_golden_pinebox.py` reproduces the predecessor script's output
against its original source PDF, `Pinebox_Middle_School.pdf`, and asserts
both of that predecessor's known defects are fixed:

- **Defect 1 (per-page aspect handling):** a page whose aspect ratio
  differs from the rest of the document gets a transform derived from its
  own media box, not one borrowed from a neighboring page.
- **Defect 2 (double padding):** an odd-length source gains exactly one
  filler page, never two.

This file is **not checked into the repository** -- it is roughly 30 MB and
currently lives outside version control, under a Resilio Sync-managed
directory rather than a durable, generally-reachable location.

**Expected local path:** the test looks for the fixture at

```
tests/fixtures/Pinebox_Middle_School.pdf
```

relative to the repository root. Set the `DECKLE_PINEBOX_FIXTURE`
environment variable to point at a copy stored elsewhere (e.g. a synced
Resilio folder) instead of copying the file into the repo.

**If the fixture is absent**, `tests/test_golden_pinebox.py` **skips** with
a message naming the expected path -- it never fails the suite or blocks
CI on a missing 30 MB file. To run the golden-fixture regression locally,
obtain `Pinebox_Middle_School.pdf` from the project's original source
material (ask the project owner if you don't already have a copy) and
place it at the path above, or point `DECKLE_PINEBOX_FIXTURE` at it.
