#!/usr/bin/env python3
"""Per-service ``alembic upgrade head`` runner for the local init container (SFP-79).

One-shot provisioning script (coverage-excluded, per the existing
``migrations/env.py`` precedent — see ``tool.coverage.run.omit`` in
``pyproject.toml``).

Sequential execution ordering — NEVER parallel (MAS §12.7 determinism): the
services are iterated in registry order (identity -> orchestrator ->
communication -> external-events). For each service:

  1. Import the persistence module via :func:`service_registry.load_base`.
  2. If the module cannot be imported or exposes no ``Base`` -> log at INFO and
     SKIP (log-and-continue, not fail-fast).
  3. If ``Base.metadata`` has no mapped tables -> log at INFO and SKIP.
  4. Otherwise run ``alembic upgrade head`` for that logical DB.

Idempotent: ``alembic upgrade head`` is a no-op when already at head, so the
container is safe to re-run on restart (no state cleanup on re-entry).

The live database URL is built from the compose-injected Postgres coordinates
and set both on the alembic ``Config`` main option AND as the service's
``DATABASE_URL`` environment variable, so every ``env.py`` style (identity /
orchestrator read the env var; external-events requires it; communication reads
the config main option) resolves the same URL uniformly.
"""

from __future__ import annotations

import os
import sys

# Sibling helper imports — this script runs from the init directory, so the
# module directory is on sys.path[0] and flat imports resolve.
from alembic import command
from alembic.config import Config
from logging_utils import configure_logging, log_skip, log_step
from service_registry import (
    ServiceEntry,
    ServiceLoadError,
    all_services,
    has_mapped_tables,
    load_base,
)

logger = configure_logging(os.environ.get("INIT_LOG_LEVEL", "INFO"))

#: Postgres coordinates injected by compose.yaml (host is the service DNS name).
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "sfp")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "sfp_local_dev")


def _make_alembic_config(entry: ServiceEntry, database_url: str) -> Config:
    """Build an alembic ``Config`` for ``entry`` with the live URL applied.

    Sets both the ``sqlalchemy.url`` main option (read by communication's
    ``env.py``) and the service's ``DATABASE_URL`` env var (read by identity /
    orchestrator / external-events ``env.py``), so every env.py style resolves
    the same URL.
    """
    os.environ[entry.env_var] = database_url
    config = Config(str(entry.alembic_ini_path))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_service(entry: ServiceEntry) -> None:
    """Run ``alembic upgrade head`` for one service, or log-and-skip.

    Skip (INFO, not failure) when: persistence module won't import, exposes no
    ``Base``, or has no mapped tables. Fail (raise) only on a real migration
    error — the caller aborts the init run on that.
    """
    try:
        base = load_base(entry)
    except ServiceLoadError as exc:
        log_skip(logger, entry.name, exc.reason)
        return
    if not has_mapped_tables(base):
        log_skip(logger, entry.name, "no mapped tables on Base.metadata")
        return

    database_url = entry.database_url(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )
    log_step(logger, f"alembic upgrade head: {entry.name} (db={entry.database})")
    config = _make_alembic_config(entry, database_url)
    command.upgrade(config, "head")
    logger.info("alembic: %s at head", entry.name)


def main() -> int:
    log_step(logger, "alembic upgrade: begin sequential run")
    for entry in all_services():
        upgrade_service(entry)
    log_step(logger, "alembic upgrade: all services processed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
