"""The print session log: a structured, append-only record of every submitted
print job.

Print failures are the hardest class of bug in this app and are not
reproducible after the fact, so every submitted chunk's full parameters get
written down -- this is a hard constraint from the Intent doc, not a nicety.

One JSON record per call to ``log_print_job``, appended as a single line
(JSON Lines) to a session log file under the OS data dir, so the log is
append-only-safe and trivially greppable/parseable line by line.

The log rotates: capped at 5 MB, retaining 3 rolled-over generations
(``session_log.jsonl.1`` .. ``.3``), via the stdlib's
``logging.handlers.RotatingFileHandler``. An append-only log that grows
forever is a support problem, not an observability feature.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

from deckle.core.paths import data_dir
from deckle.core.profiles import PrinterProfile

# A5: the session log is append-only, but an append-only log that grows
# forever is a support problem, not an observability feature. Cap the
# active file and retain a bounded number of rolled-over generations.
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3


def _data_dir() -> Path:
    """The OS-appropriate data directory for Deckle's session log.

    The platform answer comes from :func:`deckle.core.paths.data_dir`
    rather than being decided here. This module carried its own copy of
    that three-way ladder -- the third in the codebase -- which is exactly
    the duplication `paths` was extracted to end. The *data* root rather
    than the config one: on Linux XDG separates settings a user might edit
    from data an application accumulates, and a log is the second kind.

    ``DECKLE_SESSION_LOG_DIR`` still wins, so tests and support requests
    can put the log anywhere without touching the platform question.
    """
    override = os.environ.get("DECKLE_SESSION_LOG_DIR")
    if override:
        return Path(override)
    return data_dir()


def session_log_path() -> Path:
    """The path to the current session log file.

    :returns: ``<data dir>/session_log.jsonl``. The directory is
        ``DECKLE_SESSION_LOG_DIR`` when set, otherwise the OS-appropriate
        application data directory. May not exist yet.
    """
    return _data_dir() / "session_log.jsonl"


def log_print_job(
    printer: str,
    profile: PrinterProfile,
    sheets: Sequence[int],
    dpi: int,
    pass_index: int,
    copies: int = 1,
    side: str = "front",
    rotate_backs: bool = False,
) -> None:
    """Append one structured record describing a submitted print chunk.

    Called once per submitted chunk (see ``PrintSession._submit_sheets``).
    Includes a timestamp so the log can reconstruct exactly what was sent
    to the printer and when, after the fact.

    **The module docstring above promises "every submitted chunk's full
    parameters", and for a long time this recorded five of eight.**
    ``copies``, ``side`` and ``rotate_backs`` were all passed to
    ``PrintBackend.submit`` and none of them reached the log. The last two
    are the ones that hurt: ``rotate_backs`` is the half turn, decided by
    ``plan_passes`` from the flip axis *and* the paper, and it is the
    single most likely thing to be wrong in a report that begins "the
    backs came out upside down". It cannot be recovered from this record
    afterwards without re-running the planner against a profile that may
    since have changed.

    ``copies`` is 1 in every production path today. Recorded anyway,
    because it is **persisted in the session state file, restored by
    ``load``, and range-checked by ``_check_state``** -- a value the
    program stores, reloads and validates is a variable, whatever its
    current range, and a failure log that assumes otherwise is silent
    exactly when the assumption stops holding.

    Defaulted rather than required, because `tests/test_project_io.py`
    and two backend doubles already call this with five arguments and
    none of them are about copies or sides.

    :param printer: the printer the chunk went to.
    :param profile: the calibrated profile in force. Its five behavioural
        fields are recorded individually -- reproducing a duplex fault
        needs to know which way the stack was meant to go.
    :param sheets: the sheet indices in this chunk.
    :param dpi: the rasterization resolution used.
    :param pass_index: ``0`` for the front pass, ``1`` for the back pass.
    :param copies: copies of the chunk submitted.
    :param side: which physical face was painted, ``"front"`` or
        ``"back"``.
    :param rotate_backs: whether this chunk's pages were given a
        180-degree turn.
    :returns: nothing.
    :raises OSError: the log directory cannot be created or the file
        cannot be written. Unlike :mod:`deckle.core.diagnostics`, this log
        is a hard constraint from the Intent doc rather than a best-effort
        trace, so a failure to record a submitted job is not swallowed.
    """
    record = {
        "timestamp": time.time(),
        "printer": printer,
        "profile": {
            "flip_axis": profile.flip_axis,
            "output_face": profile.output_face,
            "feed_edge": profile.feed_edge,
            "reverse_stack": profile.reverse_stack,
            "calibration_version": profile.calibration_version,
        },
        "sheets": list(sheets),
        "dpi": dpi,
        "pass_index": pass_index,
        "copies": copies,
        "side": side,
        "rotate_backs": rotate_backs,
    }

    path = session_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # A5: rotate at 5 MB, keeping 3 rolled-over generations
    # (session_log.jsonl.1 .. .3), using the stdlib's RotatingFileHandler
    # rather than an unbounded append -- no third-party dependency added.
    handler = RotatingFileHandler(
        str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    try:
        handler.setFormatter(logging.Formatter("%(message)s"))
        log_record = logging.LogRecord(
            name="deckle.session_log",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=json.dumps(record),
            args=None,
            exc_info=None,
        )
        handler.emit(log_record)
    finally:
        handler.close()
