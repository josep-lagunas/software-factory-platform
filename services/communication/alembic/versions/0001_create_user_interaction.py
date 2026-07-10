"""create user_interactions baseline table.

Revision ID: 0001
Revises:
Create Date: 2026-07-10 00:00:00.000000

Scope: Communication Service database (sfp_communication). Creates the
``business.user_interactions`` table backing the ``UserInteraction`` ORM model
(MAS §9.4, ID-058). This is the baseline migration: it has no predecessor.
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
    op.create_table(
        "user_interactions",
        sa.Column(
            "interaction_id",
            sa.Uuid(),
            primary_key=True,
            comment="Immutable identifier for this interaction.",
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "Optional; set when the user is resolvable. Plain identifier "
                "column, no cross-service FK (AP-001, ID-058)."
            ),
        ),
        sa.Column(
            "origin",
            sa.String(length=20),
            nullable=False,
            comment="Whether the interaction was initiated inbound or outbound.",
        ),
        sa.Column(
            "type",
            sa.String(length=50),
            nullable=False,
            comment="Interaction type / category.",
        ),
        sa.Column(
            "response_required",
            sa.Boolean(),
            nullable=False,
            comment="Whether a response to the user is required.",
        ),
        sa.Column(
            "channel",
            sa.String(length=50),
            nullable=False,
            comment="Provider channel, e.g. 'slack'.",
        ),
        sa.Column(
            "provider_reference",
            sa.String(length=255),
            nullable=False,
            comment="1:1 provider thread reference (e.g. Slack thread ts / channel).",
        ),
        sa.Column(
            "question",
            sa.Text(),
            nullable=False,
            comment="The communication objective that opened the interaction.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="Durable summary of the interaction. NEVER a transcript (AP-009).",
        ),
        sa.Column(
            "last_message_emissor",
            sa.String(length=50),
            nullable=False,
            comment="Who sent the most recent message (MAS §9.4 canonical term).",
        ),
        sa.Column(
            "last_message_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "Time of the most recent message; anchors the inactivity timer "
                "(MAS §9.4 canonical term)."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=("8-hour inactivity deadline, measured from last_message_timestamp."),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when the interaction completes.",
        ),
        sa.Column(
            "previous_interaction_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "Optional soft context reference to a prior closed interaction "
                "(MAS §9.4 'Closed Interactions'). Plain column, no hard FK."
            ),
        ),
        schema="business",
    )


def downgrade() -> None:
    op.drop_table("user_interactions", schema="business")
