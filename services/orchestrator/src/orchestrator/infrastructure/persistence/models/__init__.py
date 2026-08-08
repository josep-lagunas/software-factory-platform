"""Orchestrator Service persistence models (MAS §8.4, ID-058).

Exposes the ``Ticket`` table model and the ``WorkflowStatus`` enum. Importing
this module registers ``business.tickets`` on ``Base.metadata`` so the full
surface is visible to Alembic autogenerate.
"""

from orchestrator.infrastructure.persistence.models.ticket import (
    Ticket,
    WorkflowStatus,
)

__all__ = ["Ticket", "WorkflowStatus"]
