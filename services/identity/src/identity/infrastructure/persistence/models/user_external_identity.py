"""The ``UserExternalIdentity`` persistence model (MAS §9.3, ID-058).

Grounded in:
- MAS §9.3 — a user may be linked to one or more external identity providers.
  ``UserExternalIdentity`` is the association record that maps a
  ``(provider, provider_user_id)`` pair to a platform ``User``.
- ID-058 — plural snake_case table (``user_external_identities``) in the
  ``business`` schema; identifiers as ``<entity>_id``; timestamps suffixed
  ``_at``; intra-service FK to ``business.users.user_id`` is permitted.

The updated_at auto-fill-on-update mechanism is deferred to a separate
platform-wide follow-up ticket. SFP-111 mirrors the existing ``EndpointConfig``
pattern: both timestamps are insert-side ``server_default=func.now()`` only —
no trigger, no ``onupdate``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from identity.infrastructure.persistence.base import Base


class UserExternalIdentity(Base):
    """Maps an external identity provider user to a platform ``User``.

    One row per ``(provider, provider_user_id)`` pair. The natural key
    ``(provider, provider_user_id)`` is unique; ``user_id`` is a foreign key
    to ``business.users.user_id``.
    """

    __tablename__ = "user_external_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["business.users.user_id"],
            name="fk_user_external_identities_user_id",
        ),
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_provider_provider_user_id",
        ),
        {"schema": "business"},
    )

    external_identity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        primary_key=True,
        default=uuid.uuid4,
        comment="Immutable surrogate primary key.",
    )
    provider: Mapped[str] = mapped_column(
        String(),
        nullable=False,
        comment="External identity provider, e.g. 'github'.",
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(),
        nullable=False,
        comment="The user's identifier as known to the provider.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        nullable=False,
        comment="The platform user this external identity maps to.",
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
        return (
            f"UserExternalIdentity(external_identity_id={self.external_identity_id!r}, "
            f"provider={self.provider!r}, provider_user_id={self.provider_user_id!r})"
        )
