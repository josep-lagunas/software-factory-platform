"""Tests for the Slack Events receiver (SFP-132).

Partitions, per the PRSpec's acceptance criteria:

1. **Authentication** — valid v0 signature accepted; invalid signature,
   missing signature, and stale timestamp all → ``401`` with **zero bus
   publishes**.
2. **Protocol** — ``url_verification`` echoes the challenge (bus untouched);
   unknown payload types / event subtypes → ``200`` with zero publishes
   (never an error — Slack retries non-2xx indefinitely).
3. **Publishing** — ``app_mention`` and ``message`` events each produce
   exactly one ``ExternalEventReceived`` envelope with ``source="slack"`` /
   ``external_id=ts`` and identity supplied by the injected factory (the
   endpoint never invents identity).
4. **Idempotency** — duplicate delivery of the same ``ts`` maps to the same
   ``idempotency_key`` (by construction; the endpoint is stateless).
5. **Malformed input** — unparseable body → ``400``.

The ASGI app is driven through a minimal hand-rolled ``__call__`` harness
(the endpoint is framework-light by design); signatures are computed in-test
with Slack's documented v0 algorithm so the fixtures are self-verifying. The
clock is injected and fixed — freshness is asserted against a chosen
timestamp, never wall-clock coincidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from typing import Any

import pytest
from communication.entrypoints.slack_events_endpoint import (
    SlackEventsEndpoint,
    make_external_event_envelope,
)
from sfp_config import SecretRef
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType

SIGNING_SECRET = "test-signing-secret-abcdef"
SECRET_REF = SecretRef(name="SLACK_SIGNING_SECRET")
PATH = "/slack/events"

#: Fixed "now" for the injected clock — tests choose timestamps relative to
#: it, so freshness never depends on wall-clock coincidence.
NOW = 1_800_000_000.0


class FakeSecretProvider:
    """In-memory ``SecretProvider``: fixed signing secret for the ref."""

    def resolve(self, ref: SecretRef) -> str:
        if ref.name == SECRET_REF.name:
            return SIGNING_SECRET
        raise AssertionError(f"unexpected secret ref: {ref.name}")


class SpyBus:
    """Records every published envelope; publishes never raise."""

    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the endpoint")


def sign(timestamp: str, body: str) -> str:
    """Compute Slack's v0 signature exactly as the endpoint expects it."""
    basestring = f"v0:{timestamp}:{body}"
    digest = hmac.new(
        SIGNING_SECRET.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


def make_endpoint(
    bus: SpyBus | None = None,
    *,
    envelope_factory: Any = None,
    clock: Any = None,
) -> SlackEventsEndpoint:
    return SlackEventsEndpoint(
        bus if bus is not None else SpyBus(),
        FakeSecretProvider(),
        envelope_factory=envelope_factory,
        clock=clock if clock is not None else (lambda: NOW),
    )


async def call_app(
    app: SlackEventsEndpoint,
    *,
    method: str = "POST",
    path: str = PATH,
    body: str | bytes = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Drive one request through the ASGI ``__call__`` interface.

    Returns ``(status, headers, body)`` — the response as plain data.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")

    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
    }
    body_stream: list[dict[str, Any]] = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return body_stream.pop(0) if body_stream else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)  # type: ignore[arg-type]

    start = next(m for m in sent if m["type"] == "http.response.start")
    body_messages = [m for m in sent if m["type"] == "http.response.body"]
    response_body = b"".join(bytes(m.get("body", b"")) for m in body_messages)
    response_headers = {
        bytes(k).decode("latin-1"): bytes(v).decode("latin-1") for k, v in start.get("headers", [])
    }
    return start["status"], response_headers, response_body


def signed_event_delivery(
    payload: dict[str, Any],
    *,
    timestamp: int | None = None,
    secret: str = SIGNING_SECRET,
) -> tuple[str, dict[str, str]]:
    """Serialize ``payload`` and build a valid v0 header set for it."""
    body = json.dumps(payload)
    ts = int(NOW) if timestamp is None else timestamp
    basestring = f"v0:{ts}:{body}"
    digest = hmac.new(
        secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return body, {
        "x-slack-signature": f"v0={digest}",
        "x-slack-request-timestamp": str(ts),
    }


def app_mention_payload(ts: str = "1712345678.123456") -> dict[str, Any]:
    return {
        "token": "redacted",
        "team_id": "T0001",
        "api_app_id": "A0001",
        "event": {
            "type": "app_mention",
            "text": "<@U0001> what is the status?",
            "user": "U0002",
            "ts": ts,
            "channel": "C0001",
            "event_ts": ts,
        },
        "type": "event_callback",
        "event_id": "Ev0001",
        "event_time": 1712345678,
    }


def message_payload(ts: str = "1712345999.654321") -> dict[str, Any]:
    return {
        "token": "redacted",
        "team_id": "T0001",
        "api_app_id": "A0001",
        "event": {
            "type": "message",
            "text": "ship it",
            "user": "U0003",
            "ts": ts,
            "channel": "C0002",
            "thread_ts": "1712345998.000000",
            "event_ts": ts,
        },
        "type": "event_callback",
        "event_id": "Ev0002",
        "event_time": 1712345999,
    }


# --------------------------------------------------------------------- #
# 1. Authentication
# --------------------------------------------------------------------- #


async def test_valid_signature_accepted_and_published() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload())

    status, _, response_body = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert len(bus.published) == 1


async def test_invalid_signature_rejected_401_no_publish() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload())

    headers = {**headers, "x-slack-signature": "v0=" + "0" * 64}
    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 401
    assert bus.published == []


async def test_missing_signature_headers_rejected_401_no_publish() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, _ = signed_event_delivery(app_mention_payload())

    status, _, _ = await call_app(app, body=body, headers={})

    assert status == 401
    assert bus.published == []


async def test_signature_signed_with_wrong_secret_rejected() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload(), secret="a-different-secret")

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 401
    assert bus.published == []


async def test_non_numeric_timestamp_rejected_401() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload())
    headers = {**headers, "x-slack-request-timestamp": "not-a-number"}

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 401
    assert bus.published == []


@pytest.mark.parametrize("age_seconds", [301, 3600, 86_400])
async def test_stale_timestamp_rejected_401_no_publish(age_seconds: int) -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    stale_ts = int(NOW) - age_seconds
    body, headers = signed_event_delivery(app_mention_payload(), timestamp=stale_ts)

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 401
    assert bus.published == []


@pytest.mark.parametrize("age_seconds", [0, 1, 60, 299])
async def test_fresh_timestamp_accepted(age_seconds: int) -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload(), timestamp=int(NOW) - age_seconds)

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert len(bus.published) == 1


async def test_future_timestamp_beyond_window_rejected() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload(), timestamp=int(NOW) + 600)

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 401
    assert bus.published == []


# --------------------------------------------------------------------- #
# 2. Protocol: url_verification + unknown types
# --------------------------------------------------------------------- #


async def test_url_verification_echoes_challenge_bus_untouched() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    payload = {"type": "url_verification", "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gqDLu"}
    body, headers = signed_event_delivery(payload)

    status, _, response_body = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert json.loads(response_body) == {"challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gqDLu"}
    assert bus.published == []


async def test_unknown_event_subtype_200_no_publish() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    payload = {
        "type": "event_callback",
        "event": {
            "type": "reaction_added",
            "user": "U0003",
            "reaction": "eyes",
            "item": {"ts": "1712345678.123456", "channel": "C0001"},
        },
    }
    body, headers = signed_event_delivery(payload)

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert bus.published == []


async def test_unknown_payload_type_200_no_publish() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery({"type": "something_else"})

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert bus.published == []


async def test_event_callback_without_event_200_no_publish() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery({"type": "event_callback"})

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert bus.published == []


async def test_message_event_missing_fields_200_no_publish() -> None:
    """A ``message`` event without text/channel/ts cannot be identified."""
    bus = SpyBus()
    app = make_endpoint(bus)
    payload = {
        "type": "event_callback",
        "event": {"type": "message", "channel": "C0002"},  # no text, no ts
    }
    body, headers = signed_event_delivery(payload)

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert bus.published == []


# --------------------------------------------------------------------- #
# 3. Publishing: app_mention / message → ExternalEventReceived
# --------------------------------------------------------------------- #


async def test_app_mention_publishes_one_external_event_received() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(app_mention_payload(ts="1712345678.123456"))

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert len(bus.published) == 1
    envelope = bus.published[0]
    assert isinstance(envelope, EventEnvelope)
    assert envelope.event_type == EventType.EXTERNAL_EVENT_RECEIVED
    payload = envelope.payload
    assert isinstance(payload, ExternalEventReceived)
    assert payload.source == "slack"
    assert payload.external_id == "1712345678.123456"


async def test_message_event_publishes_one_external_event_received() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body, headers = signed_event_delivery(message_payload(ts="1712345999.654321"))

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 200
    assert len(bus.published) == 1
    payload = bus.published[0].payload
    assert isinstance(payload, ExternalEventReceived)
    assert payload.source == "slack"
    assert payload.external_id == "1712345999.654321"


async def test_identity_comes_from_injected_factory_not_endpoint() -> None:
    """The endpoint must delegate ALL identity to the factory seam."""
    bus = SpyBus()
    factory_calls: list[ExternalEventReceived] = []

    def factory(event: ExternalEventReceived) -> EventEnvelope:
        factory_calls.append(event)
        return EventEnvelope(
            message_id="fixed-message-id",
            idempotency_key="fixed-idem-key",
            correlation_id="fixed-correlation",
            causation_id="fixed-causation",
            occurred_at="2026-09-07T00:00:00+00:00",
            event_type=EventType.EXTERNAL_EVENT_RECEIVED,
            producer="communication",
            payload=event,
        )

    app = make_endpoint(bus, envelope_factory=factory)
    body, headers = signed_event_delivery(app_mention_payload())

    await call_app(app, body=body, headers=headers)

    assert len(factory_calls) == 1
    envelope = bus.published[0]
    assert envelope.message_id == "fixed-message-id"
    assert envelope.idempotency_key == "fixed-idem-key"
    assert envelope.correlation_id == "fixed-correlation"
    assert envelope.causation_id == "fixed-causation"
    assert envelope.occurred_at == "2026-09-07T00:00:00+00:00"


async def test_default_factory_derives_idempotency_from_external_id() -> None:
    """The reference factory keys idempotency on source+external_id."""
    event = ExternalEventReceived(source="slack", external_id="1712345678.123456")
    envelope = make_external_event_envelope(event)

    assert envelope.idempotency_key == "slack:1712345678.123456"
    assert envelope.event_type == EventType.EXTERNAL_EVENT_RECEIVED
    assert envelope.payload is event
    assert envelope.message_id.startswith("evt-")
    assert envelope.occurred_at  # ISO string, non-empty


# --------------------------------------------------------------------- #
# 4. Idempotency: duplicate ts → same idempotency_key
# --------------------------------------------------------------------- #


async def test_duplicate_delivery_same_idempotency_key() -> None:
    """Endpoint is stateless; dedupe is by construction via the factory."""
    bus = SpyBus()
    app = make_endpoint(bus)  # default factory: {source}:{external_id}
    body, headers = signed_event_delivery(app_mention_payload(ts="1712345678.123456"))

    await call_app(app, body=body, headers=headers)
    await call_app(app, body=body, headers=headers)

    assert len(bus.published) == 2  # both deliveries published (endpoint stateless)
    keys = {e.idempotency_key for e in bus.published}
    assert keys == {"slack:1712345678.123456"}


# --------------------------------------------------------------------- #
# 5. Malformed input
# --------------------------------------------------------------------- #


async def test_malformed_body_400() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body = "this is not json {{{"
    ts = int(NOW)
    headers = {
        "x-slack-signature": sign(str(ts), body),
        "x-slack-request-timestamp": str(ts),
    }

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 400
    assert bus.published == []


async def test_json_non_object_body_400() -> None:
    bus = SpyBus()
    app = make_endpoint(bus)
    body = '["a", "list"]'
    ts = int(NOW)
    headers = {
        "x-slack-signature": sign(str(ts), body),
        "x-slack-request-timestamp": str(ts),
    }

    status, _, _ = await call_app(app, body=body, headers=headers)

    assert status == 400
    assert bus.published == []


# --------------------------------------------------------------------- #
# Routing + dev runner wiring
# --------------------------------------------------------------------- #


async def test_wrong_method_404() -> None:
    app = make_endpoint()
    status, _, _ = await call_app(app, method="GET", headers={})

    assert status == 404


async def test_wrong_path_404() -> None:
    app = make_endpoint()
    status, _, _ = await call_app(app, path="/other", headers={})

    assert status == 404


def test_build_dev_app_wiring() -> None:
    from communication.entrypoints.dev_slack_events import build_dev_app

    app, bus = build_dev_app()

    assert isinstance(app, SlackEventsEndpoint)
    assert bus.published_messages == []


def test_port_resolution() -> None:
    from communication.entrypoints.dev_slack_events import (
        DEFAULT_PORT,
        _resolve_port,
    )

    assert _resolve_port(None) == DEFAULT_PORT
    assert _resolve_port(9999) == 9999
    assert _resolve_port(None) == 8788


def test_port_resolution_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from communication.entrypoints.dev_slack_events import _resolve_port

    monkeypatch.setenv("SLACK_EVENTS_PORT", "9000")
    assert _resolve_port(None) == 9000
    assert _resolve_port(1234) == 1234  # CLI wins over env

    monkeypatch.setenv("SLACK_EVENTS_PORT", "not-a-number")
    assert _resolve_port(None) == 8788  # falls back to default


def test_main_serves_endpoint_on_resolved_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` wires the dev app and hands it to uvicorn — stubbed.

    uvicorn is imported *inside* ``main`` (so importing the wiring helpers
    never drags the server in); the stub asserts the handoff (app + port)
    without binding a socket. Deterministic: no real listen, no real clock
    dependence.
    """
    import types

    import communication.entrypoints.dev_slack_events as dev_module

    uvicorn_calls: list[dict[str, Any]] = []
    fake_uvicorn = types.ModuleType("uvicorn")

    def fake_run(app: Any, **kwargs: Any) -> None:
        uvicorn_calls.append({"app": app, **kwargs})

    fake_uvicorn.run = fake_run  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(dev_module.logger, "info", lambda *a, **k: None)

    dev_module.main(["--port", "9911"])

    assert len(uvicorn_calls) == 1
    call = uvicorn_calls[0]
    assert isinstance(call["app"], SlackEventsEndpoint)
    assert call["port"] == 9911
    assert call["host"] == "127.0.0.1"


def test_main_defaults_to_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLI port, no env → :data:`DEFAULT_PORT` (8788)."""
    import types

    import communication.entrypoints.dev_slack_events as dev_module

    monkeypatch.delenv("SLACK_EVENTS_PORT", raising=False)
    uvicorn_calls: list[dict[str, Any]] = []
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda app, **kw: uvicorn_calls.append(  # type: ignore[assignment]
        {"app": app, **kw}
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(dev_module.logger, "info", lambda *a, **k: None)

    dev_module.main([])

    assert len(uvicorn_calls) == 1
    assert uvicorn_calls[0]["port"] == dev_module.DEFAULT_PORT
