"""The print session log: a structured, append-only record of every submitted
print job.

Print failures are the hardest class of bug in this app and are not
reproducible after the fact, so every submitted chunk's full parameters get
written down -- this is a hard constraint from the Intent doc, not a nicety.

One JSON record per call to ``log_print_job``, appended as a single line
(JSON Lines) to a session log file under the OS data dir, so the log is
append-only-safe and trivially greppable/parseable line by line.

This module must not import Qt bindings -- see ``tests/test_core_purity.py``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from deckle.core.profiles import PrinterProfile


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
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record))
        f.write("\n")
