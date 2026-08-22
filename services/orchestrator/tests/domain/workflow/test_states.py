"""Tests for the workflow domain states module (MAS §8.4, SFP-137).

Asserts the exact §8.4 ten-state set in the pinned order, the 1:1 alignment
with the landed ``WorkflowStatus`` persistence enum (PR #114 — no divergent
duplicate), and the ACTIVE/TERMINAL classifications the transition table is
built from.
"""

from __future__ import annotations

import enum

from orchestrator.domain.workflow import states
from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    STATES,
    TERMINAL_STATES,
    WorkflowState,
)
from orchestrator.infrastructure.persistence import WorkflowStatus

# The MAS §8.4 states, pinned order (verbatim from the spec list).
_MAS_8_4_STATES: list[str] = [
    "READY_FOR_PR_SPECIFICATION",
    "READY_FOR_CODING",
    "CODING_IN_PROGRESS",
    "REVIEW_IN_PROGRESS",
    "WAITING_FOR_USER",
    "READY_FOR_MERGE",
    "MERGING",
    "DEPLOYING",
    "COMPLETED",
    "FAILED",
]


def test_states_exactly_the_ten_mas_8_4_states_in_order() -> None:
    assert [state.name for state in STATES] == _MAS_8_4_STATES
    assert len(STATES) == 10


def test_workflow_state_is_the_landed_persistence_enum_not_a_duplicate() -> None:
    # Alignment strategy: reuse the persistence enum directly. The alias must
    # BE WorkflowStatus (single source of truth), not a same-shaped copy.
    assert WorkflowState is WorkflowStatus
    assert states.WorkflowState is WorkflowStatus


def test_workflow_state_is_a_plain_enum_whose_names_are_the_mas_names() -> None:
    assert issubclass(WorkflowState, enum.Enum)
    assert {member.name for member in WorkflowState} == set(_MAS_8_4_STATES)


def test_active_states_are_the_working_stages() -> None:
    # The stages where the factory is doing work that can observably fail
    # (§8.8) — including READY_FOR_MERGE, where a merge-queue outcome decides
    # the next move. WAITING_FOR_USER is treated separately (it is not
    # "failing", ID-068) but can also reach FAILED via REJECT.
    assert ACTIVE_STATES == frozenset(
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


def test_terminal_states_are_completed_and_failed() -> None:
    assert TERMINAL_STATES == frozenset({WorkflowState.COMPLETED, WorkflowState.FAILED})


def test_active_and_terminal_partition_the_non_waiting_states() -> None:
    # Every state is active, terminal, or the user-gated WAITING_FOR_USER —
    # and the three groups are disjoint.
    waiting = {WorkflowState.WAITING_FOR_USER}
    assert set(ACTIVE_STATES) | set(TERMINAL_STATES) | waiting == set(STATES)
    assert not (set(ACTIVE_STATES) & set(TERMINAL_STATES))
    assert not (set(ACTIVE_STATES) & waiting)
    assert not (set(TERMINAL_STATES) & waiting)
