"""Tests for ``sfp_observability.logging`` (SFP-47 / ID-050).

Covers the acceptance criteria:
- (a) :func:`get_logger` returns a usable bound logger;
- (b) a log call produces JSON parseable as JSON to ``stdout`` with the expected
  fields (``event``, ``level``, ``timestamp``) and any bound key/values
  (ID-050);
- (c) the factory names each logger;
- (d) :func:`configure_logging` honours the level filter;
- (e) :func:`get_logger` configures ``structlog`` lazily when it is not yet
  configured.

Field-level assertions use :func:`structlog.testing.capture_logs` so they do
not depend on real ``stdout`` capture; the JSON-rendering criterion (b) is
additionally driven against the live ``stdout`` to prove the ``awslogs`` driver
sees valid JSON (ID-050).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import pytest
import structlog
from sfp_observability.logging import configure_logging, get_logger
from structlog.testing import capture_logs


def setup_module() -> None:
    """Ensure a known JSON configuration before each module's tests run."""
    configure_logging()


def _restore() -> None:
    """Reset structlog back to the package's standard JSON configuration."""
    configure_logging()


# --------------------------------------------------------------------------- #
# (a) get_logger returns a usable bound logger
# --------------------------------------------------------------------------- #


def test_get_logger_returns_usable_bound_logger() -> None:
    """(a) The factory hands back a logger exposing the standard log methods."""
    logger = get_logger("svc.usable")
    assert logger is not None
    for method in ("debug", "info", "warning", "error", "critical"):
        assert callable(getattr(logger, method))


def test_get_logger_binds_and_emits_extra_values() -> None:
    """(a) The returned logger is a real bound logger: bind() + log() work."""
    with capture_logs() as events:
        get_logger("svc.bind").bind(request_id="r-9").info("processing")
    assert events == [{"request_id": "r-9", "event": "processing", "log_level": "info"}]


# --------------------------------------------------------------------------- #
# (b) a log call emits JSON to stdout with the expected fields
# --------------------------------------------------------------------------- #


def test_log_call_emits_expected_fields() -> None:
    """(b) capture_logs records event, log_level and bound kwargs (ID-050).

    ``capture_logs`` records the level under ``log_level`` (it disables the
    real processor chain); the live ``stdout`` test below proves the
    ``add_log_level`` processor emits ``level`` in the rendered JSON.
    """
    with capture_logs() as events:
        get_logger("svc.fields").info("hello", component="orchestrator")
    assert events == [{"component": "orchestrator", "event": "hello", "log_level": "info"}]


def test_json_output_to_stdout_is_parseable(capsys: Any) -> None:
    """(b) The live stdout line is valid JSON carrying the expected fields.

    ``configure_logging`` is pointed at the capsys-replaced ``sys.stdout`` so
    the record is captured, proving the ``awslogs`` driver would receive valid
    JSON on stdout (ID-050).
    """
    configure_logging(stream=sys.stdout)
    try:
        get_logger("svc.json").info("ping", component="svc")
    finally:
        _restore()
    line = capsys.readouterr().out.strip().splitlines()[-1]

    record = json.loads(line)
    assert record["event"] == "ping"
    assert record["level"] == "info"
    assert record["component"] == "svc"
    assert "timestamp" in record  # the TimeStamper ran before JSONRenderer


def test_json_output_is_one_line_per_record(capsys: Any) -> None:
    """(b) Every emitted record is exactly one JSON line on stdout (ID-050)."""
    configure_logging(stream=sys.stdout)
    try:
        log = get_logger("svc.lines")
        log.info("first")
        log.error("second")
    finally:
        _restore()
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # each line independently parses as JSON


# --------------------------------------------------------------------------- #
# (c) the factory names each logger
# --------------------------------------------------------------------------- #


def test_get_logger_names_loggers() -> None:
    """(c) The name passed to get_logger reaches structlog's logger factory.

    Binding is lazy with ``cache_logger_on_first_use``, so emitting a record is
    what triggers the factory call.
    """
    seen: list[tuple[Any, ...]] = []

    def factory(*args: Any) -> structlog.PrintLogger:
        seen.append(args)
        return structlog.PrintLogger()

    configure_logging()
    structlog.configure(logger_factory=factory)
    try:
        get_logger("svc.orchestrator").info("named")
    finally:
        _restore()
    assert seen and seen[-1] == ("svc.orchestrator",)


@pytest.mark.parametrize("name", ["svc.a", "svc.b", ""])
def test_get_logger_accepts_any_name(name: str) -> None:
    """(c) The factory never rejects a logger name (incl. the empty string)."""
    logger = get_logger(name)
    assert logger is not None
    with capture_logs() as events:
        logger.info("ok")
    assert events[0]["event"] == "ok"


# --------------------------------------------------------------------------- #
# (d) configure_logging honours the level filter
# --------------------------------------------------------------------------- #


def test_configure_logging_filters_below_level() -> None:
    """(d) Records below the configured level are dropped."""
    configure_logging(level=logging.WARNING)
    try:
        logger = get_logger("svc.level")
        with capture_logs() as events:
            logger.debug("dropped")
            logger.warning("kept")
        assert events == [{"event": "kept", "log_level": "warning"}]
    finally:
        _restore()


def test_configure_logging_is_idempotent() -> None:
    """(d) Re-applying the configuration does not raise and stays configured."""
    configure_logging()
    configure_logging(level=logging.INFO)
    assert structlog.is_configured()


# --------------------------------------------------------------------------- #
# (e) get_logger configures structlog lazily when not yet configured
# --------------------------------------------------------------------------- #


def test_get_logger_configures_when_unconfigured() -> None:
    """(e) get_logger applies the JSON config if structlog is unconfigured."""
    structlog.reset_defaults()
    try:
        assert not structlog.is_configured()
        get_logger("svc.lazy")
        assert structlog.is_configured()
    finally:
        _restore()
