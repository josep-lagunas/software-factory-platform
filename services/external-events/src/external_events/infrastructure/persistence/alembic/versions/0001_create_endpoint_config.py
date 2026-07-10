"""create operational.endpoint_configs

Baseline migration for the External Events service: creates the
``operational.endpoint_configs`` table that backs
:class:`external_events.infrastructure.persistence.models.EndpointConfig`
(ID-058, MAS §9.2 "Endpoint Configuration resolves").

The column set mirrors the ORM model exactly. Endpoint configuration is
administrative/bootstrap state for v0 (MAS §9.2 ID, "performed out-of-band"),
so this baseline is created empty and seeded by an operator.

Revision ID: 0001
Revises:
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The ``operational`` PostgreSQL schema holds operational state such as the
# outbox, idempotency ledger, and endpoint configuration (ID-058, MAS §10.14).
# ``endpoint_status`` is stored as a non-native VARCHAR enum to keep it
# decoupled from a Postgres-level type and portable across the service's
# logical database; the accepted values are owned by the model's
# ``EndpointStatus`` enum.
_STATUS_ENUM = sa.Enum(
    "active",
    "inactive",
    name="endpoint_status",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    # Created idempotently so the baseline is re-runnable in fresh logical DBs
    # that may not yet carry the ``operational`` schema.
    op.execute("CREATE SCHEMA IF NOT EXISTS operational")

    op.create_table(
        "endpoint_configs",
        sa.Column("endpoint_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("auth_strategy", sa.String(), nullable=False),
        sa.Column("secret_ref", sa.String(), nullable=False),
        sa.Column("status", _STATUS_ENUM, nullable=False),
        sa.Column("endpoint_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("endpoint_id"),
        schema="operational",
    )


def downgrade() -> None:
    op.drop_table("endpoint_configs", schema="operational")
