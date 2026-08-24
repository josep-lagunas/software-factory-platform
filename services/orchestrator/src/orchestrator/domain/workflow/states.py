"""The MAS §8.4 workflow states (SFP-137).

Grounded in:
- MAS §8.4 — the pinned list of the 10 high-level workflow states. These
  represent *business* workflow states, independent from implementation.
- SFP-114 (PR #114 / DOC#84) — the ``WorkflowStatus`` persistence enum already
  enumerates exactly the §8.4 states, in the pinned order, on the ``Ticket``
  model.

Decision (per the PRSpec's first acceptance criterion): the workflow domain
reuses the landed persistence enum **directly** rather than introducing a
divergent duplicate. A second enum with the same ten members would be a copy
that can silently drift; the PRSpec explicitly allows either "map exactly" or
"reuse directly", and reuse is the stronger guarantee. ``WorkflowState`` is a
domain-side alias so the workflow package can be read without importing
persistence vocabulary, and so later tickets (SFP-121..127, SFP-131) depend on
the domain name while the identity remains single-sourced.

This module performs NO I/O and holds no state beyond constants.
"""

from __future__ import annotations

from orchestrator.infrastructure.persistence import WorkflowStatus

# The single source of truth for the MAS §8.4 states is the persistence enum
# (PR #114). The domain alias below points at it 1:1; tests assert the exact
# §8.4 member set and order so any future divergence fails loudly here.
WorkflowState = WorkflowStatus

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
