"""What the CI workflow is allowed to cancel.

`.github/workflows/test.yml` holds two jobs with very different costs.
`pytest` is about a minute, runs on every push and pull request, and
genuinely should not queue behind itself. `package-audit` is a
thirty-minute Windows build whose whole purpose is a licence and size gate
on the produced artifact -- the gate that exists because the first
PyInstaller build produced a 1.6 GB bundle containing torch, paddle, cv2
and pymupdf, AGPL in an MIT program, while every dependency-level audit
passed.

`concurrency` was declared at **workflow** level with
`cancel-in-progress: true`, which cancels the whole in-flight run, that job
included. `package-audit` runs only on non-pull-request events, so its only
trigger is `push: branches: [main]` -- and this project merges locally and
pushes, with commits reaching `main` minutes apart. Any push landing while
the Windows build was running killed it.

A cancelled run is neither green nor red. Nobody is told, nothing is
marked, and the gate can go months without executing -- exactly as it did
before, for a different reason.

Parsed as text rather than with a YAML library: PyYAML is not a declared
dependency, and `tests/test_suite_imports_only_declared_dependencies.py`
is right to refuse one for this. The two properties below are decidable
from indentation, which is what YAML nesting is.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "test.yml")


def _read() -> str:
    with open(WORKFLOW, encoding="utf-8") as handle:
        return handle.read()


def _job_block(text: str, name: str) -> str:
    """The lines of one job, from its key to the next job's key.

    Jobs are the keys indented two spaces under ``jobs:``; anything deeper
    belongs to the job above it.
    """
    lines = text.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line == f"  {name}:"
    )
    for end in range(start + 1, len(lines)):
        line = lines[end]
        if line.strip() and not line.startswith("    ") and not line.startswith("#"):
            return "\n".join(lines[start:end])
    return "\n".join(lines[start:])


def test_the_workflow_declares_no_cancelling_concurrency_group():
    """A workflow-level group cancels every job in the run, including the
    thirty-minute release gate that only ever runs on `main`."""
    text = _read()

    top_level = [
        line for line in text.splitlines() if line.startswith("concurrency:")
    ]

    assert not top_level, (
        "concurrency is declared at workflow level, so the next push to "
        "main cancels the whole in-flight run -- package-audit with it. "
        "Declare it on the pytest job instead."
    )


def test_the_short_job_still_stops_queueing_behind_itself():
    """The property the workflow-level group was there for is kept, not
    dropped -- moving it up a level and losing it would trade one problem
    for another."""
    block = _job_block(_read(), "pytest")

    assert re.search(r"^    concurrency:$", block, re.M), (
        "the pytest job has no concurrency group, so a burst of pushes "
        "queues a ~60s suite behind itself"
    )
    assert re.search(r"^      cancel-in-progress: true$", block, re.M)


def test_the_matrix_legs_do_not_cancel_each_other():
    """A job-level group is shared by every leg of the job's matrix.

    Without a matrix value in the group expression, 3.11, 3.12 and 3.14
    would land in one group inside a single run and cancel each other --
    turning `fail-fast: false` (which is there so all three report) into
    exactly the behaviour it was written to prevent. This is the hazard
    that comes with moving the group down a level, so it is guarded at the
    same time.
    """
    block = _job_block(_read(), "pytest")
    group = re.search(r"^      group: (.+)$", block, re.M)

    assert group is not None
    assert "matrix." in group.group(1), (
        "the pytest job runs a matrix and its concurrency group names no "
        f"matrix value: {group.group(1)!r}"
    )


def test_the_release_gate_is_never_cancelled_in_progress():
    """Wherever a group is declared for it, it must not cancel it."""
    block = _job_block(_read(), "package-audit")

    assert "cancel-in-progress: true" not in block, (
        "package-audit is a 30-minute licence and size gate; a cancelled "
        "run of it shows grey rather than red, so nobody finds out it did "
        "not run"
    )
