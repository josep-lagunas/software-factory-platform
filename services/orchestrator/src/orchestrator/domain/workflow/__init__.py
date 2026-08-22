"""Orchestrator workflow domain (MAS §8.4–8.8, §8.14; SFP-137/SFP-142).

Exposes the workflow state machine: the §8.4 states (:mod:`.states`), the
explicit transition table + pure :func:`~.state_machine.transition` core and
the :class:`~.state_machine.WorkflowDecision` record
(:mod:`.state_machine`), and the thin bus-emitting
:class:`~.state_machine.WorkflowTransitionPublisher` wrapper. On top of it,
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

__all__ = [
    "ACTIVE_STATES",
    "NO_TRANSITION",
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
    "evaluate",
    "evaluate_policy_set",
    "transition",
]
