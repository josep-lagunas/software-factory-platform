"""Orchestrator workflow domain (MAS §8.4–8.8, SFP-137).

Exposes the workflow state machine: the §8.4 states (:mod:`.states`), the
explicit transition table + pure :func:`~.state_machine.transition` core and
the :class:`~.state_machine.WorkflowDecision` record
(:mod:`.state_machine`), and the thin bus-emitting
:class:`~.state_machine.WorkflowTransitionPublisher` wrapper.
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

__all__ = [
    "ACTIVE_STATES",
    "STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "IllegalTransitionError",
    "TransitionDriver",
    "TransitionPolicy",
    "WorkflowDecision",
    "WorkflowState",
    "WorkflowTransitionPublisher",
    "transition",
]
