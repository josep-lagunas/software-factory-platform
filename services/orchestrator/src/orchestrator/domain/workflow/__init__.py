"""Orchestrator workflow domain (MAS §8.4–8.8, §8.14; SFP-137/SFP-142).

Exposes the workflow state machine: the §8.4 states (:mod:`.states`), the
explicit transition table + pure :func:`~.state_machine.transition` core and
the :class:`~.state_machine.WorkflowDecision` record
(:mod:`.state_machine`), the thin bus-emitting
:class:`~.state_machine.WorkflowTransitionPublisher` wrapper, and the pure
per-stage drivers (:mod:`.transitions`, SFP-138/SFP-139 — spec stage plus the
coding/review stages and the ID-068 rework loop). On top of it,
the pure policy engine (:mod:`.policy_engine`, MAS §8.14) evaluates pluggable
policies into typed :class:`~.policy_engine.PolicyOutcome` values — decide
only; the state machine remains the executor/guard.
"""

from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    PolicyDecision,
    PolicyOutcome,
    WorkflowPolicy,
    evaluate,
    evaluate_policy_set,
)
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
    CHANGES_REQUESTED_STATUS,
    CODER_AGENT,
    CODING_JOB_RUNNING_STATUS,
    CODING_STAGE_POLICY,
    CODING_STAGE_SOURCE,
    CODING_STAGE_TARGET,
    PLANNER_AGENT,
    REVIEW_STAGE_POLICY,
    REVIEW_STAGE_SOURCE,
    REVIEW_STAGE_TARGET,
    REWORK_POLICY,
    REWORK_SOURCE,
    REWORK_TARGET,
    changes_requested_review_fact,
    coding_job_started_fact,
    drive_coding_stage,
    drive_review_stage,
    drive_rework_loop,
    drive_spec_stage,
    pr_created_fact,
    spec_stage_fact_is_successful,
)

__all__ = [
    "ACTIVE_STATES",
    "CHANGES_REQUESTED_STATUS",
    "CODER_AGENT",
    "CODING_JOB_RUNNING_STATUS",
    "CODING_STAGE_POLICY",
    "CODING_STAGE_SOURCE",
    "CODING_STAGE_TARGET",
    "NO_TRANSITION",
    "PLANNER_AGENT",
    "REVIEW_STAGE_POLICY",
    "REVIEW_STAGE_SOURCE",
    "REVIEW_STAGE_TARGET",
    "REWORK_POLICY",
    "REWORK_SOURCE",
    "REWORK_TARGET",
    "STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "IllegalTransitionError",
    "PolicyDecision",
    "PolicyOutcome",
    "TransitionDriver",
    "TransitionPolicy",
    "WorkflowDecision",
    "WorkflowPolicy",
    "WorkflowState",
    "WorkflowTransitionPublisher",
    "changes_requested_review_fact",
    "coding_job_started_fact",
    "drive_coding_stage",
    "drive_rework_loop",
    "drive_review_stage",
    "drive_spec_stage",
    "evaluate",
    "evaluate_policy_set",
    "pr_created_fact",
    "spec_stage_fact_is_successful",
    "transition",
]
