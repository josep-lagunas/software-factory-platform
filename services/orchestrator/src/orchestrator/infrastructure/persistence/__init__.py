"""Orchestrator Service persistence layer (ID-058).

Exposes the per-service ``Base`` and the ``Ticket`` table model (+ the
``WorkflowStatus`` enum). All Orchestrator ORM tables register against
``Base.metadata``; importing ``Base`` from this package also imports the
``models`` submodule, so every mapped table is registered and visible to the
Alembic ``target_metadata`` (see ``migrations/env.py``).
"""

from orchestrator.infrastructure.persistence.aggregate_repository import (
    AggregateVersionRow,
    SessionFactory,
    SqlAlchemyAggregateRepository,
    session_scope,
)
from orchestrator.infrastructure.persistence.base import Base
from orchestrator.infrastructure.persistence.models import (
    Ticket,
    WorkflowStatus,
)

__all__ = [
    "AggregateVersionRow",
    "Base",
    "SessionFactory",
    "SqlAlchemyAggregateRepository",
    "Ticket",
    "WorkflowStatus",
    "session_scope",
]
