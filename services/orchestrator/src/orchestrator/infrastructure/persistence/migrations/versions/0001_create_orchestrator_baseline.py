"""create orchestrator baseline schemas (business, operational).

Revision ID: 0001
Revises:
Create Date: 2026-07-28 00:00:00.000000

Scope: Orchestrator Service database (sfp_orchestrator). Creates the
``business`` and ``operational`` schemas that subsequent Orchestrator model
migrations (SFP-83..88) will populate with tables. This is the baseline
migration: it has no predecessor and creates no tables — schema-only.

Diverges from the Identity baseline (SFP-99) by establishing TWO schemas
(``business`` + ``operational``) rather than one.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create both schemas idempotently so the baseline is re-runnable in fresh
    # logical databases that may not yet carry them.
    op.execute("CREATE SCHEMA IF NOT EXISTS business")
    op.execute("CREATE SCHEMA IF NOT EXISTS operational")


def downgrade() -> None:
    # Drop both schemas in reverse creation order (operational, then business).
    op.execute("DROP SCHEMA IF EXISTS operational")
    op.execute("DROP SCHEMA IF EXISTS business")
