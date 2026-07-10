"""Alembic migration environment for the External Events service (ID-058).

Scoped to this service's logical database (``sfp_external_events``,
ID-055). The database URL is read from ``EXTERNAL_EVENTS_DATABASE_URL`` so
the same ``alembic.ini`` works across local, CI, and deployed environments
without embedding secrets or hostnames in the repo.

Run from the service directory:

    cd services/external-events
    alembic upgrade head

``target_metadata`` points at this service's own declarative ``Base`` — there
is no shared cross-service metadata (AP-001 / MAS §7.9), so autogenerate (and
the hand-written baselines that seed it) only ever see External-Events tables.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

# Importing the models module registers every mapped table on
# ``Base.metadata`` so the full surface is visible to Alembic.
from external_events.infrastructure.persistence import models  # noqa: F401

# This env.py lives inside the persistence package, so a plain import works
# because ``alembic.ini`` sets ``prepend_sys_path = .`` (the service dir,
# which contains ``src/`` on the path via the installed package).
from external_events.infrastructure.persistence.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL is required; refusing to start without it keeps misconfiguration loud
# rather than silently writing to a surprise default database.
config.set_main_option("sqlalchemy.url", os.environ["EXTERNAL_EVENTS_DATABASE_URL"])

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migrations to SQL without a live database connection.

    Used by ``alembic upgrade head --sql`` to emit DDL for review or for
    environments where a connection is not available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
