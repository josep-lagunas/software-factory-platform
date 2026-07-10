"""SQLAlchemy persistence layer for the External Events service (ID-058).

This package owns the service-scoped declarative ``Base``, the persistence
models, and the Alembic migration environment for the ``sfp_external_events``
logical database.

Persistence models live here, never in ``domain/`` or in shared contracts,
preserving the contract/domain/persistence separation (ID-007). Endpoint
configuration is *operational* state (MAS §9.2, §10.14) and lives in the
``operational`` PostgreSQL schema.
"""

from __future__ import annotations

from external_events.infrastructure.persistence.base import Base
from external_events.infrastructure.persistence.models import (
    EndpointConfig,
    EndpointStatus,
)

__all__ = ["Base", "EndpointConfig", "EndpointStatus"]
