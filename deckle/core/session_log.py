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

from deckle.core.profiles import PrinterProfile

# A5: the session log is append-only, but an append-only log that grows
# forever is a support problem, not an observability feature. Cap the
# active file and retain a bounded number of rolled-over generations.
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3


def _data_dir() -> Path:
    """The OS-appropriate data directory for Deckle's session log."""
    override = os.environ.get("DECKLE_SESSION_LOG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Deckle"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Deckle"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "deckle"


def session_log_path() -> Path:
    """The path to the current session log file."""
    return _data_dir() / "session_log.jsonl"


def log_print_job(
    printer: str,
    profile: PrinterProfile,
    sheets: Sequence[int],
    dpi: int,
    pass_index: int,
) -> None:
    """Append one structured record describing a submitted print chunk.

    Called once per submitted chunk (see ``PrintSession._submit_sheets``).
    Includes a timestamp so the log can reconstruct exactly what was sent
    to the printer and when, after the fact.
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
