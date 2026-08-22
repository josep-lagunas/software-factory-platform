"""Orchestrator workflow domain (MAS §8.4–8.8, SFP-137).

Exposes the workflow state machine: the §8.4 states (:mod:`.states`), the
explicit transition table + pure :func:`~.state_machine.transition` core and
the :class:`~.state_machine.WorkflowDecision` record
(:mod:`.state_machine`), the thin bus-emitting
:class:`~.state_machine.WorkflowTransitionPublisher` wrapper, and the pure
per-stage drivers (:mod:`.transitions`, SFP-138).
"""

from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    IllegalTransitionError,
    TransitionDriver,
    TransitionPolicy,
    WorkflowDecision,
    WorkflowTransitionPublisher,
    transition,
)
from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    STATES,
    TERMINAL_STATES,
    WorkflowState,
)
from orchestrator.domain.workflow.transitions import (
    PLANNER_AGENT,
    SPEC_STAGE_POLICY,
    SPEC_STAGE_SOURCE,
    SPEC_STAGE_TARGET,
    drive_spec_stage,
    spec_stage_fact_is_successful,
)

__all__ = [
    "ACTIVE_STATES",
    "PLANNER_AGENT",
    "SPEC_STAGE_POLICY",
    "SPEC_STAGE_SOURCE",
    "SPEC_STAGE_TARGET",
    "STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "IllegalTransitionError",
    "TransitionDriver",
    "TransitionPolicy",
    "WorkflowDecision",
    "WorkflowState",
    "WorkflowTransitionPublisher",
    "drive_spec_stage",
    "spec_stage_fact_is_successful",
    "transition",
]
