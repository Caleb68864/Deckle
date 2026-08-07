"""Pins the SHA-256 of the manual-duplex seam modules untouched by SS-04.

SS-04's design claims that widening ``print_session._hash_plan`` to be
content-aware (REQ-014) requires touching exactly one module --
``deckle/core/print_session.py`` -- and that ``deckle/core/printing.py``
and ``deckle/core/profiles.py`` are unaffected. This test makes that claim
falsifiable instead of aspirational: it hashes both files from disk and
compares against a pin captured at implementation time.

**Regenerating a pin is an escalation, not a maintenance chore.** If this
test fails, that means ``printing.py`` or ``profiles.py`` changed. Before
updating the pin, confirm the change was deliberate and in scope for the
sub-spec you are implementing -- do not silently re-pin to make a red test
green. An unexplained change to either file is exactly the zero-diff
violation this test exists to catch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Captured 2026-08-04 at SS-04 implementation time, before any edits to
# either file in this sub-spec's run.
#
# printing.py re-pinned 2026-08-06: `duplex_flip_edge` was added, deriving
# the flip edge from sheet orientation so the exporter's /Duplex entry and
# the printed schedule give one answer instead of two. Deliberate, and
# outside the SS-04 seam this test guards -- `_hash_plan` and the pass
# planner are untouched, and profiles.py still matches its original pin.
PINNED_SHA256 = {
    "deckle/core/printing.py": "41a0d2ff4a3a9ad9a10ef1ef4b918c0c1656f774badc4bbd5757de6d27f07922",
# profiles.py re-pinned 2026-08-06: `back_offset_x_pt`/`back_offset_y_pt`
# were added for the front/back registration correction, and `load` was
# made tolerant of field drift in both directions. The tolerance is not
# incidental to that change -- `cls(**data)` is a schema contract, and
# adding a field is exactly what breaks it, as the 2026-08-04 entry for
# `LayoutSettings` records. Deliberate, and outside the SS-04 seam this
# test guards: the pass planner and `_hash_plan` are untouched.
    "deckle/core/profiles.py": "eeef0cc989d24d3acd10e7fe05ce1c16e0d049ec2542fdca4d8d48ff3d8a9e62",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_printing_and_profiles_modules_are_unchanged_by_the_seam():
    root = _repo_root()
    for relative_path, expected_sha256 in PINNED_SHA256.items():
        contents = (root / relative_path).read_bytes()
        actual_sha256 = hashlib.sha256(contents).hexdigest()
        assert actual_sha256 == expected_sha256, (
            f"{relative_path} changed (sha256 {actual_sha256} != pinned "
            f"{expected_sha256}). Regenerating this pin is an escalation, "
            "not a maintenance chore -- confirm the change is deliberate "
            "and in scope before updating it."
        )
