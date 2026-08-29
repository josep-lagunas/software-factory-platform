"""Orchestrator Service persistence layer (ID-058).

Exposes the per-service ``Base`` and the ``Ticket`` table model (+ the
``WorkflowStatus`` alias). All Orchestrator ORM tables register against
``Base.metadata``; importing ``Base`` from this package also imports the
``models`` submodule, so every mapped table is registered and visible to the
Alembic ``target_metadata`` (see ``migrations/env.py``).

``WorkflowStatus`` is an alias of the domain-owned
:class:`orchestrator.domain.workflow.states.WorkflowState` (SFP-147 inverted
the SFP-137 arrangement): the canonical definition lives in the domain, and
this layer imports it *from* there — never the other way around.
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
