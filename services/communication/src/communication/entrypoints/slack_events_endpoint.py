"""Slack Events API receiver — the inbound HTTP half of Slack integration (SFP-132).

An ASGI application exposing ``POST /slack/events`` that authenticates Slack's
Events API deliveries (``app_mention`` and ``message`` events) and publishes
each verified event as one ``ExternalEventReceived``
:class:`~sfp_contracts.events.envelope.EventEnvelope` onto the injected
:class:`~sfp_messaging.bus.MessageBus` for downstream interpretation (SFP-244).

Grounded in:
- MAS §5.5 — the ingress boundary: an external system delivers a raw,
  authenticated request body to an SFP endpoint. This endpoint authenticates
  and envelopes; it does **not** interpret the body beyond the minimal fields
  Slack's protocol requires for identity and downstream routing.
- ID-026 / ID-041 — the owning service interprets the body; this adapter
  stops at extraction of the identity/preview fields (``text`` / ``channel``
  / ``ts`` / ``thread_ts``), which are surfaced for the downstream consumer
  (SFP-244).
- SFP-132 — the implementation ticket.
- PR #136 — the outbound sibling precedent
  (:mod:`communication.interfaces.slack_outbound`).

Security contract (Slack's v0 signing scheme):
1. **Freshness** — ``X-Slack-Request-Timestamp`` older than 5 minutes
   (300 s) is rejected with ``401`` (replay protection). Reading the clock is
   allowed here: the entrypoint is infrastructure; AP-011 governs domain
   purity, not the HTTP edge. The clock is injected so tests pin it rather
   than relying on wall-clock coincidence.
2. **Signature** — ``basestring = "v0:{timestamp}:{raw_body}"``,
   ``expected = "v0=" + hex(HMAC-SHA256(SLACK_SIGNING_SECRET, basestring))``,
   compared with :func:`hmac.compare_digest`. Missing/invalid → ``401`` with
   **zero bus publishes**. The timestamp is read from the header (never
   re-derived), so the signed basestring and the verified basestring are the
   same string.
3. **Parse** — a body that is not a valid JSON object → ``400`` (checked
   after the signature, so unsigned garbage is rejected on authentication
   grounds first).

Protocol contract (why unhandled events are ``200``, never an error): Slack
retries non-2xx deliveries **indefinitely**. So ``url_verification``
challenges echo back the ``challenge`` field (bus untouched), and any event
type/subtype this receiver does not handle returns ``200`` with **no
publish** — a non-2xx would wedge Slack's retry queue forever.

Identity discipline (mirrors ``WorkflowTransitionPublisher``'s
``EnvelopeFactory`` seam, SFP-137): the endpoint **never invents**
``message_id`` / ``idempotency_key`` / ``correlation_id`` / ``causation_id`` /
``occurred_at``. It delegates envelope construction to the injected
``envelope_factory``, which derives ``idempotency_key`` deterministically from
the external id (the Slack ``ts``) so duplicate Slack redeliveries produce the
same key and downstream dedupe works. The endpoint itself is stateless.

Secrets: ``SLACK_SIGNING_SECRET`` is resolved from
``SecretRef(name="SLACK_SIGNING_SECRET")`` through the injected
:class:`~sfp_config.SecretProvider` at request time — never a literal, never
logged (ID-016; SFP-86). An unresolvable secret is a server-side
misconfiguration, not an authentication outcome: it yields ``500`` with no
publish (an attacker cannot distinguish it from any other rejection).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import uuid4

from sfp_config import SecretProvider, SecretRef
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType

if TYPE_CHECKING:
    from sfp_messaging.bus import MessageBus

__all__ = [
    "SLACK_EVENTS_PATH",
    "SLACK_SIGNING_SECRET_REF",
    "EventEnvelopeFactory",
    "SlackEventsEndpoint",
    "make_external_event_envelope",
]

#: The ASGI path this application serves.
SLACK_EVENTS_PATH: Final[str] = "/slack/events"

#: SecretRef for the Slack signing secret (SFP-86).
SLACK_SIGNING_SECRET_REF: Final[SecretRef] = SecretRef(name="SLACK_SIGNING_SECRET")

#: Slack's signing-scheme version prefix (v0).
_SIGNATURE_VERSION: Final[str] = "v0"

#: Slack signature header, lowercase (ASGI headers are always lowercase).
_HEADER_SIGNATURE: Final[str] = "x-slack-signature"

#: Slack request-timestamp header, lowercase (ASGI headers are always lowercase).
_HEADER_TIMESTAMP: Final[str] = "x-slack-request-timestamp"

#: Replay window: requests with a timestamp older than this are rejected
#: (Slack's documented recommendation: 5 minutes).
_MAX_AGE_SECONDS: Final[int] = 300

#: Largest accepted body, bytes (Slack event payloads are a few KB; this
#: bounds signature computation and JSON parsing on untrusted input).
_MAX_BODY_BYTES: Final[int] = 1_048_576

#: Event types this receiver handles (anything else → 200, no publish).
_HANDLED_EVENT_TYPES: Final[frozenset[str]] = frozenset({"app_mention", "message"})

#: The ``source`` stamped on every published ``ExternalEventReceived``.
_EXTERNAL_SOURCE: Final[str] = "slack"


class EventEnvelopeFactory(Protocol):
    """Seam: envelope construction for ``ExternalEventReceived`` events.

    Mirrors ``WorkflowTransitionPublisher``'s ``EnvelopeFactory`` discipline
    (SFP-137): identity (``message_id`` / ``idempotency_key`` /
    ``correlation_id`` / ``causation_id`` / ``occurred_at``) is runtime
    policy — the endpoint never invents it. The factory must derive
    ``idempotency_key`` deterministically from the event's ``external_id``
    so duplicate Slack redeliveries of the same ``ts`` produce the same key
    (the endpoint is stateless; dedupe is by construction, not by memory).
    """

    def __call__(self, event: ExternalEventReceived) -> EventEnvelope:
        """Return the envelope for one verified external event."""
        ...  # pragma: no cover


def make_external_event_envelope(event: ExternalEventReceived) -> EventEnvelope:
    """Reference envelope factory (SFP-132): deterministic idempotency.

    ``idempotency_key`` is derived from the event identity —
    ``{source}:{external_id}`` — so a Slack redelivery of the same ``ts``
    maps to the same key and downstream dedupe works by construction.
    ``message_id`` / ``occurred_at`` are fresh per invocation, which is
    correct: a redelivery is a *new* message carrying an *old* fact — the
    payload identity dedupes, not the envelope identity. ``correlation_id``
    anchors on the external id; ``causation_id`` is empty (this is the first
    hop — no prior SFP message caused an external delivery).

    Serves as the dev runner's default and as a concrete reference for
    callers wiring their own factory; inject your own for full identity
    control.
    """
    return EventEnvelope(
        message_id=f"evt-{uuid4()}",
        idempotency_key=f"{event.source}:{event.external_id}",
        correlation_id=event.external_id,
        causation_id="",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type=EventType.EXTERNAL_EVENT_RECEIVED,
        producer="communication",
        payload=event,
    )


class SlackEventsEndpoint:
    """ASGI application serving ``POST /slack/events`` (SFP-132).

    Args:
        bus: The :class:`~sfp_messaging.bus.MessageBus` verified events are
            published to (in-memory today; SFP-101 re-plumbs).
        secret_provider: Resolves ``SecretRef(name="SLACK_SIGNING_SECRET")``
            at request time (local dev: env/``secrets.local`` via
            :class:`~sfp_config.LocalSecretProvider`; prod: SFP-78).
        envelope_factory: Builds the ``ExternalEventReceived``
            :class:`~sfp_contracts.events.envelope.EventEnvelope`. Identity
            comes from here — the endpoint never invents it. Defaults to
            :func:`make_external_event_envelope`.
        clock: Injectable time source (Unix seconds). Defaults to
            :func:`time.time`. Tests inject a fixed clock so the freshness
            check is deterministic, never wall-clock coincidence.
    """

    def __init__(
        self,
        bus: MessageBus,
        secret_provider: SecretProvider,
        *,
        envelope_factory: EventEnvelopeFactory | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._bus = bus
        self._secret_provider = secret_provider
        self._envelope_factory = envelope_factory or make_external_event_envelope
        self._clock = clock or time.time

    async def __call__(  # noqa: PLR0913 — plain ASGI signature
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """ASGI entry point: route by method + path, handle event posts."""
        if scope["type"] != "http":  # pragma: no cover — no lifespan protocol
            return
        if scope.get("method") == "POST" and scope.get("path") == SLACK_EVENTS_PATH:
            await self._handle_events(scope, receive, send)
            return
        await self._send_json(send, 404, {"error": "not found"})

    # ------------------------------------------------------------------ #
    # Request handling
    # ------------------------------------------------------------------ #

    async def _handle_events(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Authenticate one delivery, then dispatch it by payload type."""
        raw_body = await self._read_body(receive)
        headers = _header_map(scope)

        if not self._signature_valid(raw_body, headers):
            await self._send_json(send, 401, {"error": "invalid signature"})
            return

        body = _parse_json(raw_body)
        if body is None:
            await self._send_json(send, 400, {"error": "invalid payload"})
            return

        payload_type = body.get("type")
        if payload_type == "url_verification":
            # Slack's one-time handshake: echo the challenge, bus untouched.
            challenge = body.get("challenge")
            await self._send_json(send, 200, {"challenge": challenge})
            return

        if payload_type == "event_callback":
            await self._handle_event_callback(body.get("event"), send)
            return

        # Any other payload type (e.g. ``retry_after`` advisories): ack so
        # Slack never wedges its retry queue, publish nothing.
        await self._send_json(send, 200, {"ok": True})

    async def _handle_event_callback(
        self,
        event: object,
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Publish one ``ExternalEventReceived`` for a handled event type.

        An ``event`` that is not a dict, is of an unhandled type, or lacks
        the handled shape (``text`` / ``channel`` / ``ts``) is acked with
        ``200`` and no publish — same rationale as unknown subtypes: a
        non-2xx would make Slack retry forever, and a partial event cannot
        be identified.
        """
        fields = _extract_event_fields(event)
        if fields is None:
            await self._send_json(send, 200, {"ok": True})
            return

        external = ExternalEventReceived(
            source=_EXTERNAL_SOURCE,
            external_id=fields["ts"],
            payload=fields,
        )
        await self._bus.publish(self._envelope_factory(external))
        await self._send_json(send, 200, {"ok": True})

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #

    def _signature_valid(self, raw_body: bytes, headers: dict[str, str]) -> bool:
        """Authenticate one delivery against Slack's v0 signing scheme.

        Order: freshness first (a stale timestamp is rejected before the
        secret is even resolved — the replay window is the cheaper check and
        shedding replays early is the point), then the constant-time HMAC
        comparison. Any failure — missing headers, non-numeric timestamp,
        stale timestamp, wrong signature — is a single ``False``; the caller
        maps it to ``401`` and publishes nothing.
        """
        signature = headers.get(_HEADER_SIGNATURE)
        timestamp = headers.get(_HEADER_TIMESTAMP)
        if not signature or not timestamp:
            return False

        try:
            ts = int(timestamp)
        except ValueError:
            return False

        if abs(self._clock() - ts) > _MAX_AGE_SECONDS:
            return False

        secret = self._secret_provider.resolve(SLACK_SIGNING_SECRET_REF)
        basestring = (
            f"{_SIGNATURE_VERSION}:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
        )
        expected = (
            _SIGNATURE_VERSION
            + "="
            + hmac.new(
                secret.encode("utf-8"),
                basestring.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------ #
    # ASGI plumbing
    # ------------------------------------------------------------------ #

    async def _read_body(self, receive: Callable[[], Awaitable[dict[str, Any]]]) -> bytes:
        """Read the full request body from the ASGI receive channel.

        Caps at :data:`_MAX_BODY_BYTES`; an over-limit body is truncated,
        which can only make its signature mismatch (→ ``401``) or its JSON
        unparseable (→ ``400``) — never a memory hazard.
        """
        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            received += len(chunk)
            if received <= _MAX_BODY_BYTES:
                chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    @staticmethod
    async def _send_json(
        send: Callable[[dict[str, Any]], Awaitable[None]], status: int, body: dict[str, Any]
    ) -> None:
        """Send a JSON HTTP response through the ASGI send channel."""
        payload = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": payload,
                "more_body": False,
            }
        )


def _header_map(scope: dict[str, Any]) -> dict[str, str]:
    """Flatten ASGI's raw ``headers`` list into a lowercase str→str map."""
    raw = scope.get("headers", [])
    # Narrow defensively: ASGI headers are a sequence of (bytes, bytes) pairs,
    # but this is an untrusted edge — never index into it blindly.
    result: dict[str, str] = {}
    if isinstance(raw, (list, tuple)):
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:  # noqa: PLR2004
                name, value = pair
                if isinstance(name, (bytes, bytearray)) and isinstance(value, (bytes, bytearray)):
                    result[bytes(name).decode("latin-1").lower()] = bytes(value).decode("latin-1")
    return result


def _parse_json(raw_body: bytes) -> dict[str, Any] | None:
    """Parse the body as a JSON object; ``None`` on anything else.

    A top-level non-object (list, string, number) is treated as malformed —
    Slack's Events API always posts an object, so anything else is not a
    Slack delivery.
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_event_fields(event: object) -> dict[str, str] | None:
    """Extract the identity/preview fields from a Slack ``event`` object.

    Returns ``None`` unless the event is a dict of a handled type
    (``app_mention`` / ``message``) carrying non-empty string ``text``,
    ``channel`` and ``ts`` — the field set the PRSpec names as the handled
    shape. ``thread_ts`` is part of that shape check (a thread reply carries
    it) and is included in the carried dict only when present.

    On success returns the carried dict — the checked fields verbatim
    (``text`` / ``channel`` / ``ts`` / optional ``thread_ts``) — which the
    published :class:`~sfp_contracts.events.payloads.ExternalEventReceived`
    now carries as its ``payload`` (SFP-124); ``ts`` doubles as the
    ``external_id``. Everything Slack adds beyond the checked fields (user
    ids, edited flags, file attachments, …) is left behind, and deeper
    interpretation of the body remains the owning service's (SFP-244;
    ID-026 / ID-041).
    """
    if not isinstance(event, dict):
        return None
    if event.get("type") not in _HANDLED_EVENT_TYPES:
        return None
    text = event.get("text")
    channel = event.get("channel")
    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    valid_optional = thread_ts is None or isinstance(thread_ts, str)
    if not (
        valid_optional
        and isinstance(text, str)
        and text
        and isinstance(channel, str)
        and channel
        and isinstance(ts, str)
        and ts
    ):
        return None
    fields: dict[str, str] = {"text": text, "channel": channel, "ts": ts}
    if thread_ts is not None:
        fields["thread_ts"] = thread_ts
    return fields
