"""The ``Ticket`` persistence model + ``WorkflowStatus`` enum (MAS §8.4, ID-058).

Grounded in:
- MAS §8.4 — the Orchestrator workflow state machine. ``WorkflowStatus``
  enumerates exactly the pinned §8.4 states, in the pinned order.
- MAS §6.5 — cross-context references are plain identifier columns, NOT foreign
  keys. ``project_id`` references the parent Project by identifier only (no FK
  to a projects table); SFP-100 owns the Project model.
- MAS §6.6 — ``ticket_id`` is the immutable, globally-unique primary key.
- ID-058 — plural snake_case table (``tickets``) in the ``business`` schema;
  identifiers as ``<entity>_id``; timestamps suffixed ``_at``.

Mirrors the Identity ``User`` model (ID-058) for structure and conventions. The
``updated_at`` auto-fill-on-update mechanism is deferred to a separate
platform-wide follow-up ticket: both timestamps are insert-side
``server_default=func.now()`` only — no trigger, no ``onupdate``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from orchestrator.infrastructure.persistence.base import Base


class WorkflowStatus(enum.Enum):
    """Orchestrator workflow states, pinned to MAS §8.4 (order is significant).

    This enum only enumerates the §8.4 states; it does NOT encode transition
    semantics — those are downstream of this PR.
    """

    READY_FOR_PR_SPECIFICATION = enum.auto()
    READY_FOR_CODING = enum.auto()
    CODING_IN_PROGRESS = enum.auto()
    REVIEW_IN_PROGRESS = enum.auto()
    WAITING_FOR_USER = enum.auto()
    READY_FOR_MERGE = enum.auto()
    MERGING = enum.auto()
    DEPLOYING = enum.auto()
    COMPLETED = enum.auto()
    FAILED = enum.auto()


class Ticket(Base):
    """A workflow ticket tracked by the Orchestrator (MAS §8.4).

    One row per source ticket (e.g. a Jira issue). ``ticket_id`` is the immutable
    primary key (MAS §6.6); ``project_id`` is an identifier reference to the
    parent Project with NO foreign key (MAS §6.5). Per ID-058's bidirectional
    deferral protocol, ``project_id`` is a *declared deferral* — the target
    ``business.projects`` table lands in SFP-100, at which point SFP-100 MUST
    add the real FK (close-on-landing). The in-code ``info`` marker below is how
    a deferral is declared (never silent) and is what ``tools/fk_lint.py``
    recognizes.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        # Base has no metadata.naming_convention, so constraint names are
        # supplied explicitly (mirrors the Identity explicit-name convention).
        # A single unique index satisfies both the UNIQUE and INDEX requirements
        # on external_ref without a redundant separate constraint.
        Index("ix_tickets_external_ref", "external_ref", unique=True),
        {"schema": "business"},
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        primary_key=True,
        default=uuid.uuid4,
        comment="Immutable primary key (UUID, globally unique per MAS §6.6).",
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        nullable=False,
        # ID-058 deferral marker (recognized by tools/fk_lint.py, SFP-235):
        # declares this plain column as a tracked same-service deferral, NOT a
        # silent omission. `deferred_fk` is the FK obligation to be closed when
        # the target lands; `blocked_on` is the ticket delivering that target.
        # `info` is SQLAlchemy's tooling dict and does not change runtime
        # behavior. SFP-100 owns the Project model and MUST add the real
        # ``business.tickets.project_id -> business.projects(project_id)`` FK.
        info={
            "deferred_fk": "business.projects.project_id",
            "blocked_on": "SFP-100",
        },
        comment="Identifier reference to parent Project (no FK, MAS §6.5); declared "
        "deferral per ID-058 — target business.projects lands in SFP-100.",
    )
    workflow_status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus),
        nullable=False,
        comment="MAS §8.4 workflow state.",
    )
    external_ref: Mapped[str] = mapped_column(
        String(),
        nullable=False,
        comment="Source Jira issue key, e.g. SFP-101.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation timestamp.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row last-update timestamp (no onupdate; deferred per ID-058).",
    )

    def __repr__(self) -> str:
        return f"Ticket(ticket_id={self.ticket_id!r}, external_ref={self.external_ref!r})"
