"""The 11 concrete event models (MAS §5.4 / ID-031 / SFP-39).

Each model subclasses :class:`~sfp_contracts.events.envelope.EventEnvelope`,
fixes its ``event_type`` to exactly one :class:`EventType` member (enforced by
the inherited validator) and carries one typed
:mod:`~sfp_contracts.events.payloads` payload.

Producer ownership (ID-072) is documented on the relevant events; it is not
enforced here because ``producer`` identity is runtime policy, not schema.
"""

from typing import ClassVar

from pydantic import Field

from .envelope import EventEnvelope, EventType
from .payloads import (
    CodingJobUpdatedPayload,
    DeploymentUpdatedPayload,
    ExternalEventReceivedPayload,
    MergeUpdatedPayload,
    PRSpecificationsUpdatedPayload,
    ReviewUpdatedPayload,
    TicketUpdatedPayload,
    UserInputReceivedPayload,
    UserInteractionUpdatedPayload,
    UserQueryReceivedPayload,
    WorkflowUpdatedPayload,
)


class ExternalEventReceived(EventEnvelope):
    """An inbound signal from an external system (webhook/poller)."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.EXTERNAL_EVENT_RECEIVED
    event_type: EventType = Field(default=EventType.EXTERNAL_EVENT_RECEIVED)
    payload: ExternalEventReceivedPayload


class TicketUpdated(EventEnvelope):
    """A Jira ticket changed status. Producer: Orchestrator (ID-072)."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.TICKET_UPDATED
    event_type: EventType = Field(default=EventType.TICKET_UPDATED)
    payload: TicketUpdatedPayload


class PRSpecificationsUpdated(EventEnvelope):
    """A PR-spec was created or revised. Producer: Orchestrator (ID-072)."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.PR_SPECIFICATIONS_UPDATED
    event_type: EventType = Field(default=EventType.PR_SPECIFICATIONS_UPDATED)
    payload: PRSpecificationsUpdatedPayload


class CodingJobUpdated(EventEnvelope):
    """A Coder coding-job changed state."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.CODING_JOB_UPDATED
    event_type: EventType = Field(default=EventType.CODING_JOB_UPDATED)
    payload: CodingJobUpdatedPayload


class ReviewUpdated(EventEnvelope):
    """A PR review verdict landed."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.REVIEW_UPDATED
    event_type: EventType = Field(default=EventType.REVIEW_UPDATED)
    payload: ReviewUpdatedPayload


class UserInputReceived(EventEnvelope):
    """Free-form input text received from a human."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.USER_INPUT_RECEIVED
    event_type: EventType = Field(default=EventType.USER_INPUT_RECEIVED)
    payload: UserInputReceivedPayload


class UserInteractionUpdated(EventEnvelope):
    """A human-interaction session's state changed."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.USER_INTERACTION_UPDATED
    event_type: EventType = Field(default=EventType.USER_INTERACTION_UPDATED)
    payload: UserInteractionUpdatedPayload


class UserQueryReceived(EventEnvelope):
    """A natural-language query received from a human."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.USER_QUERY_RECEIVED
    event_type: EventType = Field(default=EventType.USER_QUERY_RECEIVED)
    payload: UserQueryReceivedPayload


class MergeUpdated(EventEnvelope):
    """A PR merge changed state."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.MERGE_UPDATED
    event_type: EventType = Field(default=EventType.MERGE_UPDATED)
    payload: MergeUpdatedPayload


class DeploymentUpdated(EventEnvelope):
    """A deployment changed state. Producer: Orchestrator (ID-072)."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.DEPLOYMENT_UPDATED
    event_type: EventType = Field(default=EventType.DEPLOYMENT_UPDATED)
    payload: DeploymentUpdatedPayload


class WorkflowUpdated(EventEnvelope):
    """An SFP workflow changed state. Producer: Orchestrator (ID-072)."""

    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = EventType.WORKFLOW_UPDATED
    event_type: EventType = Field(default=EventType.WORKFLOW_UPDATED)
    payload: WorkflowUpdatedPayload
