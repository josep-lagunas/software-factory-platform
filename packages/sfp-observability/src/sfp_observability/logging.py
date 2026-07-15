"""Structured logging setup for the SFP platform (SFP-47 / ID-050).

Configures :mod:`structlog` to emit JSON records to ``stdout`` so the ECS
``awslogs`` logging driver can collect and forward every line (ID-050). A
single shared processor chain is exposed through :func:`get_logger`, which all
services call at startup instead of configuring ``structlog`` individually.

The terminal processor is :class:`structlog.processors.JSONRenderer`, so every
emitted line is a self-contained JSON object that downstream log ingesters can
parse without a regex or a syslog parser.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO, cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

#: The shared processor chain every service uses. Ordering matters: contextual
#: state is merged first, then standard metadata (level, timestamp, stack and
#: exception info) is stamped onto the event dict, and finally the record is
#: rendered as a single JSON line. The JSON renderer is the terminal processor
#: so the output is machine-parseable by the log driver.
_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.JSONRenderer(),
]

#: The stream structlog writes rendered records to. Kept as a module-level
#: reference so the output destination is explicit and inspectable (ID-050).
_OUTPUT_STREAM: TextIO = sys.stdout


def configure_logging(
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> None:
    """Configure ``structlog`` globally for JSON output to ``stdout``.

    Every call reapplies the same configuration, so it is safe to invoke more
    than once (e.g. at each service startup or from tests). The ``level`` (a
    :mod:`logging` level int) filters records below it via
    :func:`structlog.make_filtering_bound_logger`.

    Args:
        level: Minimum severity to emit. Defaults to :data:`logging.INFO`.
        stream: Stream to write JSON records to. Defaults to the module's
            :data:`_OUTPUT_STREAM` (``sys.stdout``) so the ``awslogs`` driver
            captures every line (ID-050).
    """
    structlog.configure(
        processors=_PROCESSORS,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or _OUTPUT_STREAM),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a configured structlog logger named ``name``.

    Ensures ``structlog`` is configured for JSON output (see
    :func:`configure_logging`) before handing back a bound logger. All services
    should obtain their logger through this factory so the processor chain and
    output destination stay uniform (ID-050).

    Args:
        name: The logger name, typically ``__name__`` of the calling module.

    Returns:
        A :data:`~structlog.typing.FilteringBoundLogger` emitting JSON to
        ``stdout``.
    """
    if not structlog.is_configured():
        configure_logging()
    return cast("FilteringBoundLogger", structlog.get_logger(name))


__all__: list[str] = ["configure_logging", "get_logger"]
