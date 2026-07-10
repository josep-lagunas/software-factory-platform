"""Alembic environment for the Communication Service database (sfp_communication).

Scoped per-service (ID-058): only Communication ORM tables register against
``Base.metadata``, which is the Alembic ``target_metadata``. The live database URL
is injected from the environment at runtime; ``alembic.ini``'s ``sqlalchemy.url``
is a local-dev placeholder only.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from communication.infrastructure.persistence import Base
from sqlalchemy import engine_from_config, pool

# Alembic's runtime ``Config`` object, injected by the alembic CLI.
config = context.config

# Apply logging configuration from the ini file when invoked through the CLI.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Communication ORM metadata: the single source of truth for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render migrations to SQL without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
