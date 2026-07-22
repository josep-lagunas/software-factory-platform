"""The ``User`` persistence model (MAS §9.3, ID-058).

Grounded in:
- MAS §9.3 — the Identity bounded context. ``User`` is the root entity that
  represents a resolvable platform user.
- ID-058 — plural snake_case table (``users``) in the ``business`` schema;
  identifiers as ``<entity>_id``; timestamps suffixed ``_at``.

The updated_at auto-fill-on-update mechanism is deferred to a separate
platform-wide follow-up ticket. SFP-111 mirrors the existing ``EndpointConfig``
pattern: both timestamps are insert-side ``server_default=func.now()`` only —
no trigger, no ``onupdate``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from identity.infrastructure.persistence.base import Base


class User(Base):
    """A resolvable platform user (MAS §9.3).

    The root entity of the Identity bounded context. ``user_id`` is the
    immutable primary key; external identities (``UserExternalIdentity``)
    reference this table.
    """

    __tablename__ = "users"
    __table_args__ = {"schema": "business"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        primary_key=True,
        default=uuid.uuid4,
        comment="Immutable identifier for this user.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"User(user_id={self.user_id!r})"
