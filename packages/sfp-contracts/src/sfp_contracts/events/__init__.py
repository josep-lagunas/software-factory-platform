"""Event contracts: the inter-agent / external event catalogue (MAS §5.4 / MAS §4.7).

This package models the platform's event bus messages. Every event shares a
common :class:`~sfp_contracts.events.envelope.EventEnvelope` (itself a
:class:`~sfp_contracts.messages.MessageEnvelope`, carrying ``message_id`` — the
former ``event_id``, renamed in SFP-219 — plus ``idempotency_key``,
``correlation_id``, ``causation_id``, ``occurred_at``) plus an ``event_type``
discriminator, a ``producer``, and a per-event typed ``payload``. The 11
concrete events are the payload classes (MAS §4.7 / SFP-219); their
discriminable ``event_type`` values are fixed by MAS §5.4 / ID-031; producer
ownership is fixed by ID-072.

Concrete event names and the :class:`EventPayload` base are re-exported from
:mod:`sfp_contracts.events.payloads`; the envelope and discriminator are
re-exported from :mod:`sfp_contracts.events.envelope`. The standalone §5.5
:class:`~sfp_contracts.events.external.ExternalIngressEvent` is NOT re-exported
here (it is a separate ingress model that does not subclass
:class:`EventEnvelope`).
"""

from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_contracts.events.payloads import (
    CodingJobUpdated,
    DeploymentUpdated,
    EventPayload,
    ExternalEventReceived,
    MergeUpdated,
    PRSpecificationsUpdated,
    ReviewUpdated,
    TicketUpdated,
    UserInputReceived,
    UserInteractionUpdated,
    UserQueryReceived,
    WorkflowUpdated,
)

__all__ = [
    "CodingJobUpdated",
    "DeploymentUpdated",
    "EventEnvelope",
    "EventPayload",
    "EventType",
    "ExternalEventReceived",
    "MergeUpdated",
    "PRSpecificationsUpdated",
    "ReviewUpdated",
    "TicketUpdated",
    "UserInputReceived",
    "UserInteractionUpdated",
    "UserQueryReceived",
    "WorkflowUpdated",
]
