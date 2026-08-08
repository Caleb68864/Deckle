"""Deckle's diagnostic log: structured, rotating, and never fatal.

Deckle does a lot of work that is invisible while it succeeds and
unreproducible once it fails -- a spooler that returned nothing, a session
file truncated by a crash, a temp file that could not be deleted. Every one
of those paths is *correctly* handled today: the app degrades instead of
dying. What none of them did was leave a trace, so a user reporting "the
printer list was empty" gave us nothing to work from.

This module is that trace. It follows the conventions already established by
:mod:`deckle.core.session_log` rather than inventing a second scheme:

* one JSON object per line (JSON Lines), so the log is greppable and
  parseable line by line, and an interrupted write costs one record;
* :class:`~logging.handlers.RotatingFileHandler`, capped and with a bounded
  number of generations -- an append-only log that grows forever is a
  support problem, not an observability feature;
* the same OS-appropriate data directory as the session log, resolved
  once in :func:`deckle.core.paths.data_dir`.

**Logging must never become a failure mode of its own.** A diagnostic
subsystem that raises while reporting a problem turns a degraded app into a
crashed one, and it does so precisely when things are already going wrong --
a read-only data directory, a full disk, a locked file. So every public
function here is total: it swallows its own errors, falls back to doing
nothing, and never propagates. That is the one place in this codebase where
a bare ``except Exception`` is the correct design rather than a smell, and
it is why this module has no other error handling to speak of.

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
from typing import Any

from deckle.core.paths import data_dir as app_data_dir

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB, matching session_log
_BACKUP_COUNT = 3

_LOGGER_NAME = "deckle.diagnostics"
_configured = False

DEFAULT_LEVEL = logging.INFO
"""Default verbosity. Override with ``DECKLE_LOG_LEVEL`` (e.g. ``DEBUG``).

``INFO`` rather than ``WARNING`` because the events worth recording here are
mostly *successful degradations* -- the spooler returned nothing, a session
file was skipped -- which are not warnings about this run so much as facts a
later investigation will want. They are cheap: these paths are rare by
construction.
"""


def data_dir() -> Path:
    """The OS-appropriate directory for Deckle's logs.

    ``DECKLE_LOG_DIR`` overrides it outright, which is what the tests use so
    they never touch a developer's real log.

    :returns: the directory, which may not exist yet.
    """
    override = os.environ.get("DECKLE_LOG_DIR")
    if override:
        return Path(override)
    # The platform answer comes from `paths`, which exists to hold it once.
    # This was the *fourth* copy of that ladder -- profiles, the recent
    # list, the session log and here -- and the fourth is the one that
    # shows the pattern: each was added by someone who needed a directory
    # and wrote the obvious thing, none of them wrong on its own.
    return app_data_dir()


def diagnostics_log_path() -> Path:
    """The path to the current diagnostic log file.

    :returns: ``<data_dir()>/diagnostics.jsonl``.
    """
    return data_dir() / "diagnostics.jsonl"


def _resolve_level() -> int:
    """The configured level, falling back to :data:`DEFAULT_LEVEL`.

    An unrecognised ``DECKLE_LOG_LEVEL`` is ignored rather than raising --
    a typo in an environment variable must not stop the app from starting.
    """
    raw = os.environ.get("DECKLE_LOG_LEVEL")
    if not raw:
        return DEFAULT_LEVEL
    level = logging.getLevelName(raw.strip().upper())
    return level if isinstance(level, int) else DEFAULT_LEVEL


def _configure() -> logging.Logger:
    """Attach the rotating file handler exactly once.

    Falls back to a logger with no file handler if the log cannot be opened
    -- a read-only or missing data directory must degrade to silence, never
    to an exception on the caller's path.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(_resolve_level())
    # Diagnostics are Deckle's own channel; letting them propagate to the
    # root logger would duplicate every record into whatever handler an
    # embedding application happens to have installed.
    logger.propagate = False

    try:
        path = diagnostics_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    except Exception:  # noqa: BLE001 -- see module docstring: never fatal
        logger.addHandler(logging.NullHandler())

    _configured = True
    return logger


def reset_for_tests() -> None:
    """Drop the configured handlers so the next call re-resolves the path.

    Without this, the first test to log would pin the log location for the
    whole session and later ``DECKLE_LOG_DIR`` changes would be ignored.

    :returns: nothing, and raises nothing -- a handler that refuses to
        close is skipped, per the module docstring.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 -- never fatal
            pass
    _configured = False


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Append one structured diagnostic record.

    :param event: a stable, greppable identifier for *what happened* -- e.g.
        ``"printer_enumeration_failed"``. Keep these stable across releases;
        they are what a support query greps for, so renaming one silently
        breaks every saved search.
    :param level: standard :mod:`logging` level. Defaults to ``INFO``.
    :param fields: arbitrary JSON-serialisable context. Include whatever a
        future investigator would need and cannot recover afterwards --
        paths, counts, the exception text.
    :returns: nothing, ever raises nothing. See the module docstring.

    Values that are not JSON-serialisable are coerced with :func:`repr`
    rather than raising, because a diagnostic that refuses to record an
    unexpected object is failing at the one moment it is most needed.
    """
    try:
        logger = _configure()
        if not logger.isEnabledFor(level):
            return
        record = {"timestamp": time.time(), "event": event, **fields}
        logger.log(level, json.dumps(record, default=repr))
    except Exception:  # noqa: BLE001 -- see module docstring: never fatal
        return


def log_exception(event: str, exc: BaseException, **fields: Any) -> None:
    """Record a handled exception without re-raising it.

    For the deliberate-degradation sites: the ones that catch, continue, and
    until now left nothing behind. The exception's type and message are
    recorded as fields rather than a formatted traceback, so records stay
    one line and stay parseable.

    :param event: stable event identifier, as for :func:`log_event`.
    :param exc: the exception being handled.
    :param fields: additional context.
    :returns: nothing, ever raises nothing.
    """
    log_event(
        event,
        level=logging.WARNING,
        error_type=type(exc).__name__,
        error=str(exc),
        **fields,
    )
