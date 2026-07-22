"""create identity baseline tables (business.users, business.user_external_identities).

Revision ID: 0001
Revises:
Create Date: 2026-07-23 00:00:00.000000

Scope: Identity Service database (sfp_identity). Creates the ``business.users``
and ``business.user_external_identities`` tables backing the ``User`` and
``UserExternalIdentity`` ORM models (MAS §9.3, ID-058). This is the baseline
migration: it has no predecessor.

The updated_at auto-fill-on-update mechanism (BEFORE UPDATE trigger /
set_updated_at()) is OUT OF SCOPE for SFP-111 — it is deferred to a separate
platform-wide follow-up ticket. This baseline mirrors the existing
``EndpointConfig`` pattern: both timestamps are insert-side
``server_default=func.now()`` only. No trigger, no function, no ``onupdate``.
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


def upgrade() -> None:
    # Create the business schema idempotently so the baseline is re-runnable
    # in fresh logical DBs that may not yet carry it.
    op.execute("CREATE SCHEMA IF NOT EXISTS business")

    op.create_table(
        "users",
        sa.Column(
            "user_id",
            sa.Uuid(),
            primary_key=True,
            comment="Immutable identifier for this user.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="business",
    )

    op.create_table(
        "user_external_identities",
        sa.Column(
            "external_identity_id",
            sa.Uuid(),
            primary_key=True,
            comment="Immutable surrogate primary key.",
        ),
        sa.Column(
            "provider",
            sa.String(),
            nullable=False,
            comment="External identity provider, e.g. 'github'.",
        ),
        sa.Column(
            "provider_user_id",
            sa.String(),
            nullable=False,
            comment="The user's identifier as known to the provider.",
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
            comment="The platform user this external identity maps to.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["business.users.user_id"],
            name="fk_user_external_identities_user_id",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_provider_provider_user_id",
        ),
        schema="business",
    )


def downgrade() -> None:
    op.drop_table("user_external_identities", schema="business")
    op.drop_table("users", schema="business")
