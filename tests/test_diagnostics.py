"""Tests for the diagnostic log.

The load-bearing property here is *totality*: no function in
``deckle.core.diagnostics`` may raise, under any condition, ever. A
diagnostic subsystem that throws while reporting a problem converts a
degraded app into a crashed one, and does it exactly when conditions are
already bad. Most of this file is adversarial pressure on that promise.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from deckle.core import diagnostics


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    """Point every test at its own log dir and reset handler state."""
    monkeypatch.setenv("DECKLE_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("DECKLE_LOG_LEVEL", raising=False)
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


def _records(tmp_path: Path) -> list[dict]:
    path = tmp_path / "diagnostics.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_log_event_writes_one_json_line_per_call(tmp_path):
    diagnostics.log_event("first_thing", count=1)
    diagnostics.log_event("second_thing", count=2)

    records = _records(tmp_path)
    assert [r["event"] for r in records] == ["first_thing", "second_thing"]
    assert [r["count"] for r in records] == [1, 2]
    assert all("timestamp" in r for r in records)


def test_log_path_honours_the_env_override(tmp_path):
    assert diagnostics.diagnostics_log_path() == tmp_path / "diagnostics.jsonl"


def test_log_exception_records_type_and_message_without_raising(tmp_path):
    try:
        raise FileNotFoundError("no such profile: Brother HL-2270DW")
    except FileNotFoundError as exc:
        diagnostics.log_exception("profile_load_failed", exc, printer="Brother HL-2270DW")

    (record,) = _records(tmp_path)
    assert record["event"] == "profile_load_failed"
    assert record["error_type"] == "FileNotFoundError"
    assert "Brother HL-2270DW" in record["error"]
    assert record["printer"] == "Brother HL-2270DW"


def test_non_serialisable_values_are_coerced_rather_than_raising(tmp_path):
    """A diagnostic that refuses to record an unexpected object fails at the
    one moment it is most needed."""

    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque sentinel>"

    diagnostics.log_event("odd_value", payload=Opaque())

    (record,) = _records(tmp_path)
    assert record["payload"] == "<Opaque sentinel>"


def test_an_unwritable_log_directory_degrades_to_silence(tmp_path, monkeypatch):
    """The whole point: logging must not become the failure mode."""
    monkeypatch.setattr(
        diagnostics,
        "diagnostics_log_path",
        lambda: tmp_path / "nonexistent\x00dir" / "diagnostics.jsonl",
    )
    diagnostics.reset_for_tests()

    diagnostics.log_event("should_not_explode", value=1)  # must not raise


def test_log_event_never_raises_even_if_json_encoding_fails(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(diagnostics.json, "dumps", boom)

    diagnostics.log_event("still_fine")  # must not raise


def test_log_event_never_raises_if_the_handler_itself_fails(tmp_path, monkeypatch):
    logger = logging.getLogger(diagnostics._LOGGER_NAME)
    diagnostics._configure()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(logger, "log", boom)

    diagnostics.log_event("disk_is_full")  # must not raise


def test_level_override_suppresses_lower_records(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_LOG_LEVEL", "ERROR")
    diagnostics.reset_for_tests()

    diagnostics.log_event("info_event")
    diagnostics.log_event("error_event", level=logging.ERROR)

    assert [r["event"] for r in _records(tmp_path)] == ["error_event"]


def test_an_unparseable_level_falls_back_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKLE_LOG_LEVEL", "NOT_A_LEVEL")
    diagnostics.reset_for_tests()

    diagnostics.log_event("still_logged")

    assert [r["event"] for r in _records(tmp_path)] == ["still_logged"]


def test_diagnostics_does_not_propagate_to_the_root_logger(tmp_path):
    diagnostics._configure()
    assert logging.getLogger(diagnostics._LOGGER_NAME).propagate is False


def test_rotation_is_configured_with_a_bounded_number_of_generations(tmp_path):
    from logging.handlers import RotatingFileHandler

    diagnostics.log_event("open_the_handler")
    logger = logging.getLogger(diagnostics._LOGGER_NAME)
    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]

    assert handlers, "expected a rotating file handler"
    assert handlers[0].maxBytes == diagnostics._MAX_BYTES
    assert handlers[0].backupCount == diagnostics._BACKUP_COUNT
