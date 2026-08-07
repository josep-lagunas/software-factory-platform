"""create business.tickets (Ticket persistence model).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07 00:00:00.000000

Scope: Orchestrator Service database (sfp_orchestrator). Creates the
``business.tickets`` table for the ``Ticket`` persistence model (MAS §8.4,
ID-058), mirroring the Identity baseline's hand-written ``op.create_table``
style. The ``business`` schema is created idempotently by the 0001 baseline, so
this migration does not recreate it.

Notable shape:
- ``project_id`` is a plain identifier column with NO foreign key (MAS §6.5).
- ``workflow_status`` is a native PostgreSQL enum (``workflowstatus``) whose
  values are exactly the MAS §8.4 state names.
- ``external_ref`` carries a single named unique index
  (``ix_tickets_external_ref``) — explicit name because the Orchestrator Base
  has no ``metadata.naming_convention``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("ticket_id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "workflow_status",
            sa.Enum(
                "READY_FOR_PR_SPECIFICATION",
                "READY_FOR_CODING",
                "CODING_IN_PROGRESS",
                "REVIEW_IN_PROGRESS",
                "WAITING_FOR_USER",
                "READY_FOR_MERGE",
                "MERGING",
                "DEPLOYING",
                "COMPLETED",
                "FAILED",
                name="workflowstatus",
            ),
            nullable=False,
        ),
        sa.Column("external_ref", sa.String(), nullable=False),
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
    op.create_index(
        "ix_tickets_external_ref",
        "tickets",
        ["external_ref"],
        unique=True,
        schema="business",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tickets_external_ref",
        table_name="tickets",
        schema="business",
    )
    op.drop_table("tickets", schema="business")
