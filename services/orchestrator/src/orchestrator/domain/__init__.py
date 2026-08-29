"""Orchestrator Service domain layer (MAS §8–9).

Import-surface discipline: the domain is pure — pydantic and the standard
library only. Storage, messaging and vendor SDKs live in
:mod:`orchestrator.infrastructure`; the domain consumes them exclusively
through Protocols declared here (the SFP-137 DecisionSink pattern, now applied
to aggregate persistence by :mod:`orchestrator.domain.aggregate_manager`).
"""

from orchestrator.domain.aggregate_manager import (
    FIRST_WRITE,
    Aggregate,
    AggregateManager,
    AggregateRepository,
    AggregateT,
    StaleAggregateError,
)

__all__ = [
    "FIRST_WRITE",
    "Aggregate",
    "AggregateManager",
    "AggregateRepository",
    "AggregateT",
    "StaleAggregateError",
]
