"""Tests for the SFP-124 ``ExternalEventPublisher``.

Exercises the acceptance criteria:
- the published ``ExternalEventReceived`` carries the payload dict **verbatim**
  (opaque, unmodified) alongside ``source`` and ``external_id``;
- ``idempotency_key`` is deterministic — ``f"{source}:{external_id}"`` — while
  ``message_id`` is fresh per publish (two publishes of the same event identity
  share the key, never the message id);
- ALL envelope identity (``message_id`` / ``idempotency_key`` /
  ``correlation_id`` / ``causation_id`` / ``occurred_at``) comes from the
  injected envelope factory — the publisher never constructs it;
- the reference factory mirrors SFP-132's ``make_external_event_envelope``
  (with this service as ``producer``);
- the publisher is exported from ``external_events.application``.

Deterministic: no network, no wall clock in any assertion — fresh uuid4 /
``occurred_at`` values are asserted to differ or to be non-empty, never to
equal a specific instant.
"""

from __future__ import annotations

from typing import Any

from external_events.application import (
    ExternalEventPublisher,
    make_external_event_envelope,
)
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType


class SpyBus:
    """Records every published envelope; publishes never raise."""

    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the publisher")


#: A deliberately opaque body — nested, mixed types, unicode — the publisher
#: must carry it without inspecting, filtering, or reshaping it (MAS §9.2).
BODY: dict[str, Any] = {
    "action": "opened",
    "pull_request": {"number": 42, "user": {"login": "octo"}, "labels": ["sfp", "ready"]},
    "count": 3,
    "ok": True,
    "none": None,
    "text": "¿qué pasa? 🚀",
}


# --------------------------------------------------------------------- #
# 1. Verbatim payload carriage
# --------------------------------------------------------------------- #


async def test_publish_carries_payload_verbatim() -> None:
    """One publish → one envelope whose event carries the dict verbatim."""
    bus = SpyBus()
    publisher = ExternalEventPublisher(bus)

    await publisher.publish("github", "sha-abc123", BODY)

    assert len(bus.published) == 1
    envelope = bus.published[0]
    assert envelope.event_type is EventType.EXTERNAL_EVENT_RECEIVED
    event = envelope.payload
    assert isinstance(event, ExternalEventReceived)
    assert event.source == "github"
    assert event.external_id == "sha-abc123"
    assert event.payload == BODY


async def test_publish_returns_the_published_envelope() -> None:
    bus = SpyBus()
    publisher = ExternalEventPublisher(bus)

    returned = await publisher.publish("slack", "1712345678.123456", {"text": "hi"})

    assert bus.published == [returned]


# --------------------------------------------------------------------- #
# 2. Idempotency: deterministic key, fresh message id
# --------------------------------------------------------------------- #


async def test_same_event_identity_same_key_fresh_message_id() -> None:
    """Two publishes of the same (source, external_id) share the
    idempotency_key but never the message_id."""
    bus = SpyBus()
    publisher = ExternalEventPublisher(bus)

    first = await publisher.publish("slack", "1712345678.123456", {"text": "hi"})
    second = await publisher.publish("slack", "1712345678.123456", {"text": "hi"})

    assert first.idempotency_key == second.idempotency_key == "slack:1712345678.123456"
    assert first.message_id != second.message_id
    assert first.occurred_at  # ISO string, non-empty (value is runtime policy)
    assert len(bus.published) == 2


def test_reference_factory_fields() -> None:
    """The reference factory mirrors SFP-132's, with this service as producer."""
    event = ExternalEventReceived(source="github", external_id="wh-9", payload={"k": "v"})
    envelope = make_external_event_envelope(event)

    assert envelope.idempotency_key == "github:wh-9"
    assert envelope.correlation_id == "wh-9"
    assert envelope.causation_id == ""
    assert envelope.message_id.startswith("evt-")
    assert envelope.event_type is EventType.EXTERNAL_EVENT_RECEIVED
    assert envelope.producer == "external-events"
    assert envelope.payload is event


def test_reference_factory_is_deterministic_in_key_only() -> None:
    """Same event identity → same key, fresh message_id (mirror of SFP-132)."""
    first = make_external_event_envelope(
        ExternalEventReceived(source="slack", external_id="ts-1", payload={})
    )
    second = make_external_event_envelope(
        ExternalEventReceived(source="slack", external_id="ts-1", payload={})
    )

    assert first.idempotency_key == second.idempotency_key == "slack:ts-1"
    assert first.message_id != second.message_id


# --------------------------------------------------------------------- #
# 3. Identity comes only from the injected factory
# --------------------------------------------------------------------- #


async def test_identity_comes_from_injected_factory() -> None:
    """The publisher must delegate ALL identity to the factory seam."""
    bus = SpyBus()
    factory_events: list[ExternalEventReceived] = []

    def factory(event: ExternalEventReceived) -> EventEnvelope:
        factory_events.append(event)
        return EventEnvelope(
            message_id="fixed-message-id",
            idempotency_key="fixed-idem-key",
            correlation_id="fixed-correlation",
            causation_id="fixed-causation",
            occurred_at="2026-09-12T00:00:00+00:00",
            event_type=EventType.EXTERNAL_EVENT_RECEIVED,
            producer="external-events",
            payload=event,
        )

    publisher = ExternalEventPublisher(bus, envelope_factory=factory)
    returned = await publisher.publish("github", "sha-abc123", BODY)

    assert len(factory_events) == 1
    # The factory received the built event, payload verbatim.
    received = factory_events[0]
    assert received.source == "github"
    assert received.external_id == "sha-abc123"
    assert received.payload == BODY
    # The published (and returned) envelope IS the factory's — the publisher
    # constructed no identity of its own.
    assert bus.published == [returned]
    envelope = bus.published[0]
    assert envelope.message_id == "fixed-message-id"
    assert envelope.idempotency_key == "fixed-idem-key"
    assert envelope.correlation_id == "fixed-correlation"
    assert envelope.causation_id == "fixed-causation"
    assert envelope.occurred_at == "2026-09-12T00:00:00+00:00"


# --------------------------------------------------------------------- #
# 4. Package surface
# --------------------------------------------------------------------- #


def test_publisher_exported_from_application_package() -> None:
    from external_events import application

    assert application.ExternalEventPublisher is ExternalEventPublisher
    assert application.make_external_event_envelope is make_external_event_envelope
    assert callable(application.ExternalEventPublisher)
    assert "ExternalEventPublisher" in application.__all__
    assert "EventEnvelopeFactory" in application.__all__
    assert "make_external_event_envelope" in application.__all__
