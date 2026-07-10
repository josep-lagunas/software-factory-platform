"""The ``UserInteraction`` persistence model (MAS §9.4, AP-009).

Grounded in:
- MAS §9.4 — the authoritative field list and lifecycle. One ``UserInteraction``
  maps 1:1 to a Slack thread in v0 and owns the communication lifecycle.
- AP-009 — persist the durable *summary*, never conversation transcripts. The
  ``summary`` column is mandatory and holds the summary only; this is a design
  invariant enforced by convention, not a runtime guard on column content.
- ID-058 — plural snake_case table (``user_interactions``) in the ``business``
  schema; no cross-service foreign keys; identifiers as ``<entity>_id``;
  timestamps suffixed ``_at``.

Note on naming: ``last_message_emissor`` and ``last_message_timestamp`` are the
canonical terms *frozen during the architecture design* (MAS §9.4) and
intentionally do NOT follow the ``_at`` suffix convention; they are kept exactly
as the spec fixes them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class UserInteraction(Base):
    """One bounded business communication (MAS §9.4).

    The authoritative representation of a communication that maps 1:1 to a Slack
    thread in v0. Holds a durable *summary* — never a conversation transcript
    (AP-009). ``expires_at`` drives the 8-hour inactivity timer, measured from
    ``last_message_timestamp``; every inbound or outbound message resets it.

    Completed and expired interactions are immutable and never reopened.
    """

    __tablename__ = "user_interactions"
    __table_args__ = {"schema": "business"}

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        comment="Immutable identifier for this interaction.",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        comment="Optional; set when the user is resolvable. Plain identifier "
        "column, no cross-service FK (AP-001, ID-058).",
    )
    origin: Mapped[str] = mapped_column(
        String(20), comment="Whether the interaction was initiated inbound or outbound."
    )
    type: Mapped[str] = mapped_column(String(50), comment="Interaction type / category.")
    response_required: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Whether a response to the user is required."
    )
    channel: Mapped[str] = mapped_column(String(50), comment="Provider channel, e.g. 'slack'.")
    provider_reference: Mapped[str] = mapped_column(
        String(255),
        comment="1:1 provider thread reference (e.g. Slack thread ts / channel).",
    )
    question: Mapped[str] = mapped_column(
        Text, comment="The communication objective that opened the interaction."
    )
    summary: Mapped[str] = mapped_column(
        Text,
        comment="Durable summary of the interaction. NEVER a transcript (AP-009).",
    )
    last_message_emissor: Mapped[str] = mapped_column(
        String(50),
        comment="Who sent the most recent message (MAS §9.4 canonical term).",
    )
    last_message_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        comment="Time of the most recent message; anchors the inactivity timer "
        "(MAS §9.4 canonical term).",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        comment="8-hour inactivity deadline, measured from last_message_timestamp.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Set when the interaction completes."
    )
    previous_interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        comment="Optional soft context reference to a prior closed interaction "
        "(MAS §9.4 'Closed Interactions'). Plain column, no hard FK.",
    )

    def __repr__(self) -> str:
        return (
            f"UserInteraction(interaction_id={self.interaction_id!r}, "
            f"channel={self.channel!r}, provider_reference={self.provider_reference!r})"
        )
