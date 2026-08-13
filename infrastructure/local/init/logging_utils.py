"""Logging utilities for the local init container (SFP-79).

A thin, stdlib-only logging facade so every provisioning step — bring-up,
readiness polling, ``pulumi up``, per-service ``alembic upgrade`` — emits through
one named logger with a stable format. The skip-and-continue behaviour required
by the PRSpec (services with no persistence ``Base`` or no mapped tables are
logged at INFO and skipped) routes through :func:`log_skip` so the decision is
always surfaced, never silent.

Library module (coverage-gated). No third-party imports — importable anywhere.
"""

from __future__ import annotations

import logging
import sys

#: The single named logger every init step logs through.
LOGGER_NAME = "sfp.init"

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the shared ``sfp.init`` logger.

    Idempotent: repeated calls reuse the existing handler and only adjust the
    level. Writes to stdout (container logs are consumed from stdout) with a
    stable timestamped format. The logger never propagates to the root logger,
    so messages are not duplicated if the host also configures logging.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    return logger


def log_step(logger: logging.Logger, step: str) -> None:
    """Log a sequential provisioning step at INFO.

    Sequential ordering is mandatory (MAS §12.7 determinism — NEVER parallel);
    each step is announced so the ordering is visible in the container log.
    """
    logger.info("step: %s", step)


def log_skip(logger: logging.Logger, service: str, reason: str) -> None:
    """Log a log-and-continue skip at INFO.

    Used when a service has no persistence ``Base``, no mapped tables, or its
    persistence module fails to import: the service is skipped rather than
    failing the whole init run (the only hard failures are real migration
    errors). Surfaced at INFO so a skip is never silent.
    """
    logger.info("skip: service=%s reason=%s", service, reason)
