"""The Orchestrator application layer: command emitters and their seams.

The application layer turns Orchestrator *decisions* into inter-agent commands
(MAS §5.3 / ID-072): it constructs the command envelope and publishes it on
the injected bus. It performs no state transition of its own (MAS §8.6) —
commands carry intent; the workflow advances only on events and user
decisions.

The eight emitters complete the command catalogue (ID-031 / SFP-219):
``EXECUTE_CODING_JOB`` (SFP-152) plus its seven siblings (SFP-245) — review,
synchronize, merge, user-input, notify, and the two cancels.

``ReadinessGateHost`` (SFP-149) is the orchestrator-side hosting of the
Readiness Gate: it runs the layer-2 model evaluation through the injected
AgentRuntime seam and routes exhaustively on the verdict, ahead of planning.

The layer also owns the first concrete decision sink: the durable
:class:`~orchestrator.application.decision_recorder.DecisionRecorder`
(SFP-148), which persists every engine-produced
:class:`~orchestrator.domain.workflow.state_machine.WorkflowDecision`
append-only behind the SFP-147 aggregate boundary.
"""

from orchestrator.application.command_emitters import (
    CancelCodingJobEmitter,
    CancelReviewJobEmitter,
    ExecuteCodingJobEmitter,
    NotifyUserEmitter,
    RequestMergeEmitter,
    RequestUserInputEmitter,
    ReviewPullRequestEmitter,
    SynchronizePullRequestEmitter,
)
from orchestrator.application.context_resolver_host import (
    ContextResolverHost,
    MissingContextError,
)
from orchestrator.application.decision_recorder import (
    DecisionRecorder,
    TicketWorkflowAggregate,
)
from orchestrator.application.readiness_host import ReadinessGateHost

__all__ = [
    "CancelCodingJobEmitter",
    "CancelReviewJobEmitter",
    "ContextResolverHost",
    "DecisionRecorder",
    "ExecuteCodingJobEmitter",
    "MissingContextError",
    "NotifyUserEmitter",
    "RequestMergeEmitter",
    "RequestUserInputEmitter",
    "ReadinessGateHost",
    "ReviewPullRequestEmitter",
    "SynchronizePullRequestEmitter",
    "TicketWorkflowAggregate",
]
