"""Typed payloads for the 11 events (MAS §5.4 / MAS §4.7 / ID-031 / SFP-219).

Each event carries one of these as its ``payload`` (MAS §4.7: the payload half
of the message envelope). The field lists are the minimal natural keys/state of
each event — enough to route and act on the event without front-loading the
richer per-domain schemas those consumers will own later. Every payload rejects
unknown fields (``extra='forbid'``) so schema drift between producer and
consumer surfaces immediately.

The concrete event names live HERE now (SFP-219): the former per-message
envelope subclasses are gone, and each event is modelled by its payload class
(dropping the ``…Payload`` suffix, past-tense grammar). Every payload subclasses
the public :class:`EventPayload` base. The event discriminator (``event_type``)
and the envelope live on
:class:`~sfp_contracts.events.envelope.EventEnvelope`.

Grounded in MAS §5.4 (event catalogue), MAS §4.7 (envelope + payload shape),
ID-031 (the event names) and ID-072 (producer ownership, documented on the
relevant payloads below).
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class EventPayload(BaseModel):
    """Base for event payloads: ``extra='forbid'`` is the only shared rule.

    Subclasses are flat (no envelope fields) — the envelope lives on
    :class:`~sfp_contracts.events.envelope.EventEnvelope`. The concrete event
    names are the payload classes themselves (MAS §4.7 / SFP-219).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class ExternalEventReceived(EventPayload):
    """An inbound signal from an external system (webhook, poller, etc.)."""

    source: str
    external_id: str


class TicketUpdated(EventPayload):
    """A Jira ticket changed status. Producer: Orchestrator (ID-072)."""

    ticket_id: str
    status: str


class PRSpecificationsUpdated(EventPayload):
    """A PR-spec was created or revised. Producer: Orchestrator (ID-072)."""

    pr_spec_id: str
    change: str


class CodingJobUpdated(EventPayload):
    """A Coder coding-job changed state (queued, running, complete, ...)."""

    job_id: str
    status: str


class ReviewUpdated(EventPayload):
    """A PR review verdict landed (approved / changes-requested / blocked)."""

    pr_number: int
    review_status: str


class UserInputReceived(EventPayload):
    """Free-form input text received from a human in a session."""

    session_id: str
    text: str


class UserInteractionUpdated(EventPayload):
    """A human-interaction session's state changed."""

    session_id: str
    state: str


class UserQueryReceived(EventPayload):
    """A natural-language query received from a human."""

    session_id: str
    query: str


class MergeUpdated(EventPayload):
    """A PR merge changed state (requested, in-flight, merged, failed)."""

    pr_number: int
    merge_status: str


class DeploymentUpdated(EventPayload):
    """A deployment changed state. Producer: Orchestrator (ID-072)."""

    deployment_id: str
    status: str


class WorkflowUpdated(EventPayload):
    """An SFP workflow changed state. Producer: Orchestrator (ID-072)."""

    workflow_id: str
    status: str
