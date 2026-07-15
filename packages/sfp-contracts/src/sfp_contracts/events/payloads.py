"""Typed payloads for the 11 events (MAS §5.4 / ID-031 / SFP-39).

Each event carries one of these as its ``payload``. The field lists are the
minimal natural keys/state of each event — enough to route and act on the
event without front-loading the richer per-domain schemas those consumers will
own later. Every payload rejects unknown fields (``extra='forbid'``) so schema
drift between producer and consumer surfaces immediately.

Grounded in MAS §5.4 (envelope + payload shape), ID-031 (the event names) and
ID-072 (producer ownership, documented on the events in :mod:`.models`).
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class _Payload(BaseModel):
    """Base for payloads: ``extra='forbid'`` is the only shared rule.

    Subclasses are flat (no envelope fields) — the envelope lives on
    :class:`~sfp_contracts.events.envelope.EventEnvelope`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class ExternalEventReceivedPayload(_Payload):
    """An inbound signal from an external system (webhook, poller, etc.)."""

    source: str
    external_id: str


class TicketUpdatedPayload(_Payload):
    """A Jira ticket changed status. Producer: Orchestrator (ID-072)."""

    ticket_id: str
    status: str


class PRSpecificationsUpdatedPayload(_Payload):
    """A PR-spec was created or revised. Producer: Orchestrator (ID-072)."""

    pr_spec_id: str
    change: str


class CodingJobUpdatedPayload(_Payload):
    """A Coder coding-job changed state (queued, running, complete, ...)."""

    job_id: str
    status: str


class ReviewUpdatedPayload(_Payload):
    """A PR review verdict landed (approved / changes-requested / blocked)."""

    pr_number: int
    review_status: str


class UserInputReceivedPayload(_Payload):
    """Free-form input text received from a human in a session."""

    session_id: str
    text: str


class UserInteractionUpdatedPayload(_Payload):
    """A human-interaction session's state changed."""

    session_id: str
    state: str


class UserQueryReceivedPayload(_Payload):
    """A natural-language query received from a human."""

    session_id: str
    query: str


class MergeUpdatedPayload(_Payload):
    """A PR merge changed state (requested, in-flight, merged, failed)."""

    pr_number: int
    merge_status: str


class DeploymentUpdatedPayload(_Payload):
    """A deployment changed state. Producer: Orchestrator (ID-072)."""

    deployment_id: str
    status: str


class WorkflowUpdatedPayload(_Payload):
    """An SFP workflow changed state. Producer: Orchestrator (ID-072)."""

    workflow_id: str
    status: str
