"""Alembic environment for the Identity Service database (sfp_identity).

Scoped per-service (ID-058): only Identity ORM tables register against
``Base.metadata``, which is the Alembic ``target_metadata``. The live database
URL is injected from the environment at runtime; ``alembic.ini``'s
``sqlalchemy.url`` is a local-dev placeholder only.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

# Importing the persistence package registers every mapped table on
# ``Base.metadata`` so the full surface is visible to Alembic.
from identity.infrastructure.persistence import (
    Base,
    models,  # noqa: F401
)
from sqlalchemy import engine_from_config, pool

# Alembic's runtime ``Config`` object, injected by the alembic CLI.
config = context.config

# Apply logging configuration from the ini file when invoked through the CLI.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow the live database URL to override the alembic.ini placeholder.
db_url = os.environ.get("IDENTITY_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Identity ORM metadata: the single source of truth for autogenerate.
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


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
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
