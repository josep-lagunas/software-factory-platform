"""Alembic environment for the Orchestrator Service database (sfp_orchestrator).

Scoped per-service (ID-058): only Orchestrator ORM tables register against
``Base.metadata``, which is the Alembic ``target_metadata``. The live database
URL is injected from the environment at runtime; ``alembic.ini``'s
``sqlalchemy.url`` is a local-dev placeholder only.

ASYNC baseline (SFP-99 divergence from Identity): migrations run through
``async_engine_from_config`` + ``connection.run_sync(...)`` driven by
``asyncio.run``, targeting the ``postgresql+asyncpg`` driver. The synchronous
``engine_from_config`` is intentionally absent.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the persistence package registers every mapped table on
# ``Base.metadata`` so the full surface is visible to Alembic. No ``models``
# submodule exists yet (SFP-83..88 are downstream); ``Base`` alone is imported.
from orchestrator.infrastructure.persistence import Base

# Alembic's runtime ``Config`` object, injected by the alembic CLI.
config = context.config

# Apply logging configuration from the ini file when invoked through the CLI.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow the live database URL to override the alembic.ini placeholder.
db_url = os.environ.get("ORCHESTRATOR_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Orchestrator ORM metadata: the single source of truth for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migrations to SQL without a live DB connection."""
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


def do_run_migrations(connection: Connection) -> None:
    """Configure Alembic context and run migrations within a transaction."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations via ``run_sync``."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live async DB connection."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
