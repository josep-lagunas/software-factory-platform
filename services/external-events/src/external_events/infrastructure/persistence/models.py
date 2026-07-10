"""Persistence models for the External Events service (MAS §9.2, ID-058).

The External Events service owns endpoint configuration — the operational
state that lets it authenticate and route inbound webhook requests. The
``EndpointConfig`` model is the ORM projection of that configuration.

Column derivation (MAS §9.2 "Endpoint Configuration resolves"):
  ``endpoint_id``   — immutable identifier, also the URL path key
                      (``/webhooks/{endpoint_id}``); the natural primary key.
  ``provider``      — the external provider the endpoint fronts.
  ``auth_strategy`` — the authentication strategy key resolved by the
                      Authentication Strategy Factory; stored open-ended as a
                      string because the valid strategy set is owned by that
                      factory (a separate concern), not by this persistence
                      model.
  ``secret_ref``    — an opaque reference to the endpoint's authentication
                      secret (ID-016); the secret *value* is never persisted,
                      only the reference.
  ``status``        — endpoint lifecycle status (see :class:`EndpointStatus`),
                      checked during ingress-level validation (MAS §9.2).
  ``endpoint_metadata`` — optional free-form provider metadata.
  ``created_at`` / ``updated_at`` — audit timestamps (ID-058 ``_at`` suffix).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from external_events.infrastructure.persistence.base import Base

# The ``operational`` PostgreSQL schema holds operational state such as the
# outbox, idempotency ledger, and endpoint configuration (ID-058, MAS §10.14).
OPERATIONAL_SCHEMA = "operational"


class EndpointStatus(enum.Enum):
    """Lifecycle status of a webhook endpoint.

    The External Events service checks this during ingress-level validation
    (MAS §9.2): only ``ACTIVE`` endpoints accept inbound requests. The set is
    intentionally small — it models the accept/reject decision the ingress
    validation performs, nothing more. Additional lifecycle states (e.g. a
    soft ``deprecated``) are deferred until a concrete requirement exists, to
    avoid baking an underspecified taxonomy into the schema.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"

    def __str__(self) -> str:
        return self.value


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist the enum's ``.value`` (not its name) in the database."""
    return [member.value for member in enum_cls]


class EndpointConfig(Base):
    """ORM model for the ``operational.endpoint_configs`` table.

    One row per configured webhook endpoint. Endpoint configuration is
    administrative/bootstrap state for v0 (ID, "performed out-of-band").
    """

    __tablename__ = "endpoint_configs"
    __table_args__ = {"schema": OPERATIONAL_SCHEMA}

    endpoint_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    auth_strategy: Mapped[str] = mapped_column(String, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[EndpointStatus] = mapped_column(
        SAEnum(
            EndpointStatus,
            native_enum=False,
            values_callable=_enum_values,
            length=16,
            name="endpoint_status",
        ),
        nullable=False,
    )
    endpoint_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    )

    def __repr__(self) -> str:
        return (
            f"EndpointConfig(endpoint_id={self.endpoint_id!r}, "
            f"provider={self.provider!r}, status={self.status})"
        )
