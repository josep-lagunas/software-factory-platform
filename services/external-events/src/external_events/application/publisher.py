"""The formal ``ExternalEventReceived`` publisher (SFP-124).

Wraps a caller-supplied payload in an :class:`ExternalEventReceived` and
publishes it, enveloped, on the injected
:class:`~sfp_messaging.bus.MessageBus` — the "wrap authenticated provider
payloads in ExternalEventReceived" + "publish authenticated external events"
capability of MAS §9.2 / §5.5.

Grounded in:
- MAS §5.5 / §9.2 — the External Events Service wraps and publishes; it
  **never interprets provider payloads**. This publisher never inspects,
  filters, or shape-checks ``payload`` — it stores the dict verbatim on the
  event.
- SFP-137 / SFP-132 — the envelope-factory seam discipline (mirrors
  ``WorkflowTransitionPublisher``'s ``EnvelopeFactory`` and the communication
  Slack endpoint's reference factory): ``message_id`` / ``idempotency_key`` /
  ``correlation_id`` / ``causation_id`` / ``occurred_at`` are runtime policy —
  the publisher never invents them. It delegates envelope construction to the
  injected :class:`EventEnvelopeFactory`, defaulting to the reference
  :func:`make_external_event_envelope`.
- ID-026 / ID-041 — payload opacity: interpretation belongs to the owning
  service (SFP-244), not here.

Design choices:
- ``external_id`` is caller-supplied. The publisher never generates or guesses
  it; hashing a raw body into an external id is a transport-level concern of
  the webhook endpoint (SFP-120), which calls this publisher.
- The reference factory derives ``idempotency_key`` deterministically from the
  event identity — ``f"{source}:{external_id}"`` — so a redelivery of the same
  external fact yields the same key and downstream dedupe works by
  construction (the publisher is stateless). ``message_id`` / ``occurred_at``
  are fresh per invocation: a redelivery is a *new* message carrying an *old*
  fact — the payload identity dedupes, not the envelope identity.
- Transport-agnostic: the only dependency is the ``MessageBus`` Protocol
  (in-memory today; SFP-118/SFP-101 re-plumb). No SNS/SQS/boto3 import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import uuid4

from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType

if TYPE_CHECKING:
    from sfp_messaging.bus import MessageBus

__all__ = [
    "EventEnvelopeFactory",
    "ExternalEventPublisher",
    "make_external_event_envelope",
]

#: The ``producer`` stamped on every envelope the reference factory builds —
#: the External Events Service is the exclusive producer of
#: ``ExternalEventReceived`` (MAS §5.4 / §9.2).
_PRODUCER: Final[str] = "external-events"


class EventEnvelopeFactory(Protocol):
    """Seam: envelope construction for ``ExternalEventReceived`` events.

    Mirrors the ``WorkflowTransitionPublisher`` ``EnvelopeFactory`` discipline
    (SFP-137) and the communication Slack endpoint's seam (SFP-132): identity
    (``message_id`` / ``idempotency_key`` / ``correlation_id`` /
    ``causation_id`` / ``occurred_at``) is runtime policy — the publisher never
    invents it. A factory must derive ``idempotency_key`` deterministically
    from the event identity so duplicate redeliveries of the same
    ``(source, external_id)`` produce the same key (the publisher is
    stateless; dedupe is by construction, not by memory).
    """

    def __call__(self, event: ExternalEventReceived) -> EventEnvelope:
        """Return the envelope for one external event."""
        ...  # pragma: no cover


def make_external_event_envelope(event: ExternalEventReceived) -> EventEnvelope:
    """Reference envelope factory (SFP-124): deterministic idempotency.

    Mirrors :func:`communication.entrypoints.slack_events_endpoint.\
make_external_event_envelope` (SFP-132). ``idempotency_key`` is derived from
    the event identity — ``{source}:{external_id}`` — so a redelivery of the
    same external fact maps to the same key and downstream dedupe works by
    construction. ``message_id`` / ``occurred_at`` are fresh per invocation,
    which is correct: a redelivery is a *new* message carrying an *old* fact —
    the payload identity dedupes, not the envelope identity.
    ``correlation_id`` anchors on the external id; ``causation_id`` is empty
    (this is the first hop — no prior SFP message caused an external delivery).

    Serves as the publisher's default and as a concrete reference for callers
    wiring their own factory; inject your own for full identity control.
    """
    return EventEnvelope(
        message_id=f"evt-{uuid4()}",
        idempotency_key=f"{event.source}:{event.external_id}",
        correlation_id=event.external_id,
        causation_id="",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type=EventType.EXTERNAL_EVENT_RECEIVED,
        producer=_PRODUCER,
        payload=event,
    )


class ExternalEventPublisher:
    """Wrap a payload in ``ExternalEventReceived`` and publish it (SFP-124).

    Constructor-injected seams:

    - ``bus`` — the vendor-neutral :class:`~sfp_messaging.bus.MessageBus`
      (in-memory today; SFP-118 / SFP-101 re-plumb). Each ``publish`` call
      publishes exactly one envelope.
    - ``envelope_factory`` — optional callable producing the
      ``ExternalEventReceived`` :class:`~sfp_contracts.events.envelope.\
EventEnvelope`. Identity comes from here — the publisher never invents it.
      Defaults to :func:`make_external_event_envelope` (deterministic
      ``idempotency_key`` = ``f"{source}:{external_id}"``, fresh
      ``message_id``).

    The publisher never interprets the payload (MAS §9.2): ``payload`` is
    stored verbatim on the event — no inspection, filtering, or shape checks.
    It never generates or guesses ``external_id``: the caller supplies it
    (SFP-120's endpoint derives it from the raw body, e.g. by hashing).
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: EventEnvelopeFactory | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory or make_external_event_envelope

    async def publish(
        self, source: str, external_id: str, payload: dict[str, Any]
    ) -> EventEnvelope:
        """Build one ``ExternalEventReceived`` and publish it on the bus.

        Flow: (1) build the event carrying ``payload`` verbatim;
        (2) obtain the envelope from the injected factory (all identity —
        ``message_id`` / ``idempotency_key`` / ``correlation_id`` /
        ``causation_id`` / ``occurred_at`` — comes from there, never from
        here); (3) publish on the injected bus. Bus failures propagate to the
        caller.

        Args:
            source: The originating external system (e.g. ``"github"``).
            external_id: Caller-supplied stable id of the external fact.
            payload: The carried body — stored verbatim, never interpreted.

        Returns:
            The published envelope.
        """
        event = ExternalEventReceived(source=source, external_id=external_id, payload=payload)
        envelope = self._envelope_factory(event)
        await self._bus.publish(envelope)
        return envelope
