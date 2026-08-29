"""The MAS §8.4 workflow states (SFP-137; ownership inverted in SFP-147).

Grounded in:
- MAS §8.4 — the pinned list of the 10 high-level workflow states. These
  represent *business* workflow states, independent from implementation.
- SFP-114 (PR #114 / DOC#84) — the ``WorkflowStatus`` persistence enum
  originally enumerated the §8.4 states on the ``Ticket`` model; SFP-137
  aliased the domain name onto it to stay single-sourced.

Decision (inverted by SFP-147): the **domain owns the enum**. The SFP-137
arrangement — ``WorkflowState = WorkflowStatus`` imported *from persistence* —
made every domain import pull the whole SQLAlchemy/Alembic stack in, which the
SFP-147 domain-purity gate (``test_domain_import_chain_introduces_no_
persistence_modules``) correctly forbids: the domain importing infrastructure
is a layering violation regardless of how few names cross the seam. The
canonical definition now lives here, and persistence imports/aliases it
*from* the domain (``WorkflowStatus = WorkflowState``), so the dependency
arrow points infrastructure -> domain, the member set remains
single-sourced, and every existing ``WorkflowStatus`` consumer (models,
tests) keeps working unchanged.

This module performs NO I/O and holds no state beyond constants.
"""

from __future__ import annotations

import enum


class WorkflowState(enum.StrEnum):
    """The 10 MAS §8.4 workflow states, in their pinned declaration order.

    A ``StrEnum`` so the member's plain name is its string value — exactly
    what the persistence layer's ``sa.Enum`` column stores (the migration
    pinned the same names), keeping the DB representation identical to the
    pre-inversion ``enum.auto()``-based persistence enum. The enum only
    enumerates the §8.4 states; transition semantics live in
    :mod:`orchestrator.domain.workflow.transitions`.
    """

    READY_FOR_PR_SPECIFICATION = "READY_FOR_PR_SPECIFICATION"
    READY_FOR_CODING = "READY_FOR_CODING"
    CODING_IN_PROGRESS = "CODING_IN_PROGRESS"
    REVIEW_IN_PROGRESS = "REVIEW_IN_PROGRESS"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    READY_FOR_MERGE = "READY_FOR_MERGE"
    MERGING = "MERGING"
    DEPLOYING = "DEPLOYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: The 10 MAS §8.4 states, in their pinned declaration order. Exposed as an
#: immutable tuple (not the enum itself) so downstream code can iterate the
#: canonical order without relying on ``enum.auto()`` values.
STATES: tuple[WorkflowState, ...] = tuple(WorkflowState)

#: States that can transition directly to ``FAILED`` (MAS §8.8). FAILED is
#: reachable from the *active* states — the ones where the factory is doing
#: work that can observably fail, including READY_FOR_MERGE (an unrecoverable
#: merge-queue failure, ID-068, is a genuine factory-blocked condition).
#: Terminal (COMPLETED) and failure (FAILED) states are themselves never
#: sources of a further FAILED move. WAITING_FOR_USER is not in this set — it
#: reaches FAILED only via an explicit user REJECT (ID-069), which the
#: transition table adds separately, because waiting itself is not failing
#: (ID-068).
ACTIVE_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        WorkflowState.READY_FOR_CODING,
        WorkflowState.CODING_IN_PROGRESS,
        WorkflowState.REVIEW_IN_PROGRESS,
        WorkflowState.READY_FOR_MERGE,
        WorkflowState.MERGING,
        WorkflowState.DEPLOYING,
    }
)

#: Terminal states: once reached, no further transition is legal (the workflow
#: restarts as a *new* workflow, not by moving out of a terminal state).
TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
    }
)

__all__ = [
    "ACTIVE_STATES",
    "STATES",
    "TERMINAL_STATES",
    "WorkflowState",
]
