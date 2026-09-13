"""Tests for the SFP-254 url_verification challenge-echo middleware.

Exercises :class:`~external_events.interfaces.challenge_echo.\
ChallengeEchoMiddleware` around the REAL landed SFP-120 stack — the same
harness recipe as ``test_webhook.py``: SFP-121 resolver over in-memory
SQLite (attached ``operational`` schema), the SFP-122 factory over a fake
SecretProvider with the real SFP-123 registry (``github_hmac`` — chosen
because it is clock-free), and the real SFP-124 publisher over a spy bus.
One test additionally drives a genuine Slack v0-signed handshake through
the real ``slack_signature`` strategy with its clock pinned (SFP-123 seam),
so the motivating provider path is covered deterministically.

Acceptance criteria (PRSpec SFP-254-1), one per section below:

- signed ``url_verification`` → ``200`` echo of the verbatim challenge
  (plain ``json.dumps``, ``application/json``) — including a non-ASCII
  challenge and a multi-chunk request body;
- unsigned handshake → ``401`` verbatim, NO echo (auth gates the echo);
- unknown/inactive endpoint handshake → ``404`` byte-identical to the
  unwrapped endpoint, NO echo;
- signed non-handshake → byte-identical 200 ``{"ok": true}`` with exactly
  ONE publish (middleware fully transparent);
- malformed handshakes (missing/empty/non-string challenge; non-dict or
  malformed JSON) take the endpoint's existing path verbatim;
- a handshake still publishes one ``ExternalEventReceived`` (bus contract
  unchanged — SFP-132 filters at the bus);
- SFP-122 misconfiguration errors propagate with no response sent;
- ``build_dev_app`` serves the middleware-wrapped endpoint;
- logging carries endpoint_id + status ONLY — never the challenge value.

Byte-identity is asserted by driving the SAME request through the wrapped
and the unwrapped endpoint and comparing the captured ASGI responses
(status, body, headers) plus the publish counts. Deterministic throughout
(MAS §12.7): no network, no wall clock, no ordering dependence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from functools import partial
from typing import Any, NamedTuple

import pytest
import sqlalchemy as sa
from external_events.application import (
    EndpointConfigResolver,
    ExternalEventPublisher,
    UnknownAuthStrategyError,
)
from external_events.application.strategies import SlackSignatureStrategy
from external_events.entrypoints.dev_webhook import build_dev_app
from external_events.infrastructure.persistence import (
    Base,
    EndpointConfig,
    EndpointStatus,
)
from external_events.interfaces import ChallengeEchoMiddleware, WebhookIngressEndpoint
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging.transport.in_memory import InMemoryTransport
from sqlalchemy.orm import Session, sessionmaker

#: Fixed, timezone-aware seed timestamps — deterministic, and they keep the
#: PostgreSQL-only ``now()`` server default out of the SQLite round-trip.
_SEED_TS = datetime(2026, 1, 1, tzinfo=UTC)

#: The secret VALUES the fake provider hands the SFP-122 factory (names are
#: the seeded rows' ``secret_ref`` references, never values — ID-016).
_SECRETS: dict[str, str] = {
    "GH_WEBHOOK_SECRET": "gh-test-webhook-secret-value",
    "SLACK_SIGNING_SECRET": "slack-test-signing-secret-value",
}

#: A representative Slack save-time challenge (a public test fixture, not a
#: secret — challenges are single-use nonce values).
_CHALLENGE = "3e89br12eZt5cYbF9jHR-save-time"

#: Fixed "now" for the pinned Slack replay-window clock (SFP-123 seam).
_SLACK_NOW = 1_800_000_000.0

#: The endpoint ids seeded per test (see :func:`_seed_configs`).
_ACTIVE_ID = "gh-main"
_INACTIVE_ID = "slack-off"
_UNKNOWN_ID = "no-such-endpoint"
_MISCONFIGURED_ID = "bogus-auth"

#: Any ASGI app under test (the wrapped or the unwrapped endpoint).
_App = ChallengeEchoMiddleware | WebhookIngressEndpoint


class _FakeSecretProvider:
    """Mapping-backed SecretProvider stand-in for the SFP-86 seam."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def resolve(self, ref: Any) -> str:
        return self._secrets[ref.name]


class _SpyBus:
    """Records every published envelope; publishes never raise."""

    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the ingress")


class _Response(NamedTuple):
    """One captured ASGI response."""

    status: int
    body: bytes
    headers: list[tuple[bytes, bytes]]


class _Stack(NamedTuple):
    """The ingress under test: wrapped + unwrapped app + the publish sink."""

    app: ChallengeEchoMiddleware
    raw: WebhookIngressEndpoint
    bus: _SpyBus


# --------------------------------------------------------------------- #
# Harness — the test_webhook.py recipe, plus the wrapped/raw pair
# --------------------------------------------------------------------- #


@contextmanager
def _session_factory_cm() -> Iterator[Callable[[], AbstractContextManager[Session]]]:
    """A fresh in-memory database per test, ``operational`` schema attached.

    One underlying connection stays open for the whole test (StaticPool) so
    rows planted by the seeding session are visible to the resolver's
    borrowed sessions. Hermetic — nothing crosses the test's boundary.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sa.pool.StaticPool,
    )

    @sa.event.listens_for(engine, "connect")
    def _attach_operational(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS operational")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    connection = engine.connect()
    maker = sessionmaker(bind=connection, expire_on_commit=False)

    @contextmanager
    def factory() -> Iterator[Session]:
        session = maker()
        try:
            yield session
        finally:
            session.close()

    yield factory

    connection.close()
    engine.dispose()


def _seed_configs(factory: Callable[[], AbstractContextManager[Session]]) -> None:
    """Plant the endpoint rows the branch tests need.

    - ``gh-main`` — ACTIVE github endpoint (``github_hmac``, clock-free).
    - ``slack-off`` — INACTIVE slack endpoint (resolution must 404 it).
    - ``bogus-auth`` — ACTIVE endpoint naming a strategy the registry does
      not know (SFP-122 ``UnknownAuthStrategyError`` propagation).
    - ``no-such-endpoint`` — deliberately NOT planted (unknown-id branch).
    """
    rows = [
        EndpointConfig(
            endpoint_id=_ACTIVE_ID,
            provider="github",
            auth_strategy="github_hmac",
            secret_ref="GH_WEBHOOK_SECRET",
            status=EndpointStatus.ACTIVE,
            created_at=_SEED_TS,
            updated_at=_SEED_TS,
        ),
        EndpointConfig(
            endpoint_id=_INACTIVE_ID,
            provider="slack",
            auth_strategy="slack_signature",
            secret_ref="SLACK_SIGNING_SECRET",
            status=EndpointStatus.INACTIVE,
            created_at=_SEED_TS,
            updated_at=_SEED_TS,
        ),
        EndpointConfig(
            endpoint_id=_MISCONFIGURED_ID,
            provider="github",
            auth_strategy="bogus",
            secret_ref="GH_WEBHOOK_SECRET",
            status=EndpointStatus.ACTIVE,
            created_at=_SEED_TS,
            updated_at=_SEED_TS,
        ),
    ]
    with factory() as session:
        session.add_all(rows)
        session.commit()


@pytest.fixture
def stack() -> Iterator[_Stack]:
    """The full real stack (resolver, factory, publisher) over a spy bus."""
    with _session_factory_cm() as factory:
        _seed_configs(factory)
        bus = _SpyBus()
        raw = WebhookIngressEndpoint(
            EndpointConfigResolver(factory),
            _FakeSecretProvider(_SECRETS),
            ExternalEventPublisher(bus),
            bus,
        )
        yield _Stack(app=ChallengeEchoMiddleware(raw), raw=raw, bus=bus)


def _github_signature(secret: str, body: bytes) -> str:
    """A valid ``X-Hub-Signature-256`` value over exactly ``body``."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _valid_headers(body: bytes) -> list[tuple[bytes, bytes]]:
    signature = _github_signature(_SECRETS["GH_WEBHOOK_SECRET"], body)
    return [(b"x-hub-signature-256", signature.encode())]


def _handshake_body(challenge: object) -> bytes:
    """A handshake-shaped body with the given ``challenge`` value."""
    return json.dumps({"type": "url_verification", "challenge": challenge}).encode()


async def _post(
    app: _App,
    *,
    path: str,
    body: bytes,
    headers: list[tuple[bytes, bytes]],
    method: str = "POST",
    chunks: list[bytes] | None = None,
) -> _Response:
    """Drive one ASGI request through the app and capture the response."""
    if chunks is None:
        chunks = [body]
    requests = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]

    async def receive() -> dict[str, Any]:
        if not requests:
            raise AssertionError("receive called after the body was fully delivered")
        return requests.pop(0)

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {"type": "http", "method": method, "path": path, "headers": headers},
        receive,
        send,
    )

    start = next(m for m in sent if m["type"] == "http.response.start")
    body_msg = next(m for m in sent if m["type"] == "http.response.body")
    return _Response(
        status=start["status"],
        body=body_msg["body"],
        headers=list(start["headers"]),
    )


async def _identical_to_unwrapped(
    stack: _Stack,
    *,
    path: str,
    body: bytes,
    headers: list[tuple[bytes, bytes]],
    method: str = "POST",
    chunks: list[bytes] | None = None,
) -> _Response:
    """Assert wrapped == unwrapped: response byte-identity AND equal publishes.

    Drives the same request through the wrapped app and then the raw
    SFP-120 endpoint; the middleware is correct exactly when the two runs
    are indistinguishable from the outside.
    """
    stack.bus.published.clear()
    wrapped = await _post(
        stack.app, path=path, body=body, headers=headers, method=method, chunks=chunks
    )
    wrapped_publishes = len(stack.bus.published)
    stack.bus.published.clear()
    raw = await _post(
        stack.raw, path=path, body=body, headers=headers, method=method, chunks=chunks
    )
    raw_publishes = len(stack.bus.published)
    assert wrapped == raw, (wrapped, raw)
    assert wrapped_publishes == raw_publishes
    return wrapped


# --------------------------------------------------------------------- #
# 1. Signed handshake → 200 echo, verbatim challenge
# --------------------------------------------------------------------- #


async def test_signed_handshake_is_echoed_verbatim_as_plain_json(stack: _Stack) -> None:
    body = _handshake_body(_CHALLENGE)
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 200
    # Plain json.dumps of {"challenge": <value>} — the exact bytes.
    assert response.body == json.dumps({"challenge": _CHALLENGE}).encode("utf-8")
    assert (b"content-type", b"application/json") in response.headers


async def test_signed_unicode_challenge_is_echoed_verbatim(stack: _Stack) -> None:
    """The challenge value is echoed VERBATIM — non-ASCII included — with
    plain ``json.dumps`` escaping (no re-encoding, no truncation)."""
    challenge = "chålle-ngë-𝟘🎉-value"
    body = _handshake_body(challenge)
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 200
    assert response.body == json.dumps({"challenge": challenge}).encode("utf-8")
    assert json.loads(response.body) == {"challenge": challenge}


async def test_multi_chunk_handshake_is_echoed(stack: _Stack) -> None:
    """The receive wrapper captures chunks as the inner app drains them —
    a handshake delivered across chunk boundaries (including an EMPTY
    middle chunk) is reassembled and echoed; the inner app still saw an
    equivalent receive (it authenticated and published)."""
    body = _handshake_body(_CHALLENGE)
    head, tail = body[: len(body) // 2], body[len(body) // 2 :]
    response = await _post(
        stack.app,
        path=f"/webhooks/{_ACTIVE_ID}",
        body=body,
        headers=_valid_headers(body),
        chunks=[head, b"", tail],
    )

    assert response.status == 200
    assert response.body == json.dumps({"challenge": _CHALLENGE}).encode("utf-8")


# --------------------------------------------------------------------- #
# 2. Unsigned handshake → 401 verbatim, NO echo
# --------------------------------------------------------------------- #


async def test_unsigned_handshake_is_401_without_echo(stack: _Stack) -> None:
    """Auth still gates the handshake: a bad signature never gets the
    challenge echoed back — the 401 passes through verbatim."""
    body = _handshake_body(_CHALLENGE)
    headers = [(b"x-hub-signature-256", b"sha256=" + b"0" * 64)]
    response = await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=headers)

    assert response.status == 401
    assert json.loads(response.body) == {"error": "unauthorized"}
    assert b"challenge" not in response.body
    assert stack.bus.published == []


async def test_missing_signature_handshake_is_401_without_echo(stack: _Stack) -> None:
    body = _handshake_body(_CHALLENGE)
    response = await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=[])

    assert response.status == 401
    assert b"challenge" not in response.body
    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 3. Unknown/inactive endpoint → 404 byte-identical, NO echo
# --------------------------------------------------------------------- #


async def test_unknown_endpoint_handshake_is_404_byte_identical_no_echo(
    stack: _Stack,
) -> None:
    body = _handshake_body(_CHALLENGE)
    response = await _identical_to_unwrapped(
        stack, path=f"/webhooks/{_UNKNOWN_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 404
    assert json.loads(response.body) == {"error": "not found"}
    assert b"challenge" not in response.body


async def test_inactive_endpoint_handshake_is_404_byte_identical_no_echo(
    stack: _Stack,
) -> None:
    body = _handshake_body(_CHALLENGE)
    response = await _identical_to_unwrapped(
        stack, path=f"/webhooks/{_INACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 404
    assert b"challenge" not in response.body


# --------------------------------------------------------------------- #
# 4. Non-handshake → byte-identical 200 with exactly one publish
# --------------------------------------------------------------------- #


async def test_signed_non_handshake_is_byte_identical_with_one_publish(
    stack: _Stack,
) -> None:
    """The middleware is fully transparent for ordinary deliveries: the
    wrapped response equals the unwrapped endpoint's byte-for-byte and
    exactly ONE publish happened on the wrapped run."""
    body = b'{"action":"opened","pull_request":{"number":42},"text":"\xc2\xbfqu\xc3\xa9?"}'
    response = await _identical_to_unwrapped(
        stack, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 200
    assert json.loads(response.body) == {"ok": True}
    assert b"challenge" not in response.body
    # Exactly one publish — asserted on the wrapped run itself.
    stack.bus.published.clear()
    again = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )
    assert again == response
    assert len(stack.bus.published) == 1


async def test_route_miss_passes_through_byte_identical(stack: _Stack) -> None:
    """Non-POST routes are the endpoint's shared 404 — untouched."""
    body = _handshake_body(_CHALLENGE)
    response = await _identical_to_unwrapped(
        stack,
        path=f"/webhooks/{_ACTIVE_ID}",
        body=body,
        headers=_valid_headers(body),
        method="GET",
    )

    assert response.status == 404
    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 5. Malformed handshakes → the endpoint's existing path, verbatim
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "expected_publishes"),
    [
        # Handshake-shaped but no usable challenge → plain 200 delivery.
        (json.dumps({"type": "url_verification"}).encode(), 1),
        (_handshake_body(""), 1),
        (_handshake_body(123), 1),
        (_handshake_body(None), 1),
        (_handshake_body({"x": 1}), 1),
        (_handshake_body(["a"]), 1),
        # Not a handshake type → plain 200 delivery.
        (json.dumps({"type": "event_callback", "challenge": "no-echo"}).encode(), 1),
        # Not even a JSON object → the endpoint's own 400, zero publishes.
        (b"[1, 2, 3]", 0),
        (b"42", 0),
        (b"this is {{{ not json", 0),
    ],
)
async def test_malformed_handshake_variants_take_the_existing_path_verbatim(
    stack: _Stack, body: bytes, expected_publishes: int
) -> None:
    """Every malformed-handshake shape passes through byte-identically to
    the unwrapped endpoint — dict-shaped ones publish (the existing
    authenticated-delivery path), non-dict/malformed ones take the
    endpoint's 400 — and NONE is ever answered with an echo."""
    response = await _identical_to_unwrapped(
        stack, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert b"challenge" not in response.body
    # Publish count on the wrapped run equals the existing path's count.
    stack.bus.published.clear()
    await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body))
    assert len(stack.bus.published) == expected_publishes


async def test_middleware_alone_replays_non_handshake_200_from_any_inner_app() -> None:
    """Standalone-contract check (the middleware is a REUSABLE composition
    piece, not SFP-120-specific): whatever 200 the inner app produced, only
    a handshake-shaped body is echoed — malformed or non-dict JSON replays
    the inner events verbatim. Unreachable through the SFP-120 endpoint
    itself (it 400s those bodies before any 200); covered here against a
    minimal always-200 stub inner app."""

    async def ok_inner(
        scope: dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[dict[str, Any]], Any],
    ) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner-ok", "more_body": False})

    for body in (b"this is {{{ not json", b"[1, 2, 3]", b""):
        response = await _post(
            ChallengeEchoMiddleware(ok_inner), path="/webhooks/anything", body=body, headers=[]
        )
        assert response.status == 200
        assert response.body == b"inner-ok"


# --------------------------------------------------------------------- #
# 6. Handshake still publishes ONE ExternalEventReceived (bus unchanged)
# --------------------------------------------------------------------- #


async def test_handshake_still_publishes_one_external_event(stack: _Stack) -> None:
    body = _handshake_body(_CHALLENGE)
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 200  # the echo — and still exactly one publish
    assert len(stack.bus.published) == 1
    envelope = stack.bus.published[0]
    assert envelope.event_type is EventType.EXTERNAL_EVENT_RECEIVED
    event = envelope.payload
    assert isinstance(event, ExternalEventReceived)
    assert event.source == "github"
    assert event.external_id == hashlib.sha256(body).hexdigest()
    assert event.payload == {"type": "url_verification", "challenge": _CHALLENGE}
    assert envelope.idempotency_key == f"github:{hashlib.sha256(body).hexdigest()}"


# --------------------------------------------------------------------- #
# 7. Misconfiguration errors propagate — no response, no echo
# --------------------------------------------------------------------- #


async def test_auth_misconfiguration_propagates_without_response(stack: _Stack) -> None:
    """An endpoint naming an unknown strategy raises through the middleware
    (the server owns the 5xx) — nothing was echoed, nothing published."""
    body = _handshake_body(_CHALLENGE)
    with pytest.raises(UnknownAuthStrategyError):
        await _post(
            stack.app,
            path=f"/webhooks/{_MISCONFIGURED_ID}",
            body=body,
            headers=_valid_headers(body),
        )

    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 8. The motivating provider: a genuine Slack v0-signed handshake
# --------------------------------------------------------------------- #


async def test_slack_v0_signed_handshake_is_echoed() -> None:
    """End-to-end on the real provider path: a Slack-signature endpoint
    (clock pinned via the SFP-123 seam) receiving a correctly v0-signed
    ``url_verification`` POST gets the challenge echoed."""
    challenge = "slack-save-time-challenge-2468"
    body = json.dumps({"type": "url_verification", "challenge": challenge}).encode()
    timestamp = "1800000000"
    basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    signature = (
        "v0="
        + hmac.new(
            _SECRETS["SLACK_SIGNING_SECRET"].encode("utf-8"),
            basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    headers = [
        (b"x-slack-signature", signature.encode()),
        (b"x-slack-request-timestamp", timestamp.encode()),
    ]

    with _session_factory_cm() as factory:
        with factory() as session:
            session.add(
                EndpointConfig(
                    endpoint_id="slack-live",
                    provider="slack",
                    auth_strategy="slack_signature",
                    secret_ref="SLACK_SIGNING_SECRET",
                    status=EndpointStatus.ACTIVE,
                    created_at=_SEED_TS,
                    updated_at=_SEED_TS,
                )
            )
            session.commit()
        bus = _SpyBus()
        # Pinned-clock slack strategy through the factory's one-argument
        # construction shape — deterministic freshness (MAS §12.7).
        registry = {"slack_signature": partial(SlackSignatureStrategy, clock=lambda: _SLACK_NOW)}
        raw = WebhookIngressEndpoint(
            EndpointConfigResolver(factory),
            _FakeSecretProvider(_SECRETS),
            ExternalEventPublisher(bus),
            bus,
            registry=registry,  # type: ignore[arg-type]
        )
        response = await _post(
            ChallengeEchoMiddleware(raw), path="/webhooks/slack-live", body=body, headers=headers
        )

    assert response.status == 200
    assert response.body == json.dumps({"challenge": challenge}).encode("utf-8")
    assert len(bus.published) == 1


# --------------------------------------------------------------------- #
# 9. dev_webhook serves the wrapped app
# --------------------------------------------------------------------- #


async def test_dev_app_serves_the_middleware_wrapped_endpoint() -> None:
    """``build_dev_app`` returns the challenge-echo middleware AROUND the
    real SFP-120 endpoint, over the real InMemoryTransport bus (SFP-254)."""
    app, bus, _resolver = build_dev_app()

    assert isinstance(app, ChallengeEchoMiddleware)
    assert isinstance(app.app, WebhookIngressEndpoint)
    assert isinstance(bus, InMemoryTransport)


# --------------------------------------------------------------------- #
# 10. Logging discipline — endpoint_id + status ONLY
# --------------------------------------------------------------------- #


async def test_logging_carries_endpoint_id_and_status_only(
    stack: _Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """A handshake run logs endpoint_id and the status — the challenge
    VALUE never appears (SFP-120 discipline, binding for SFP-254)."""
    marker_challenge = "LOG-CHALLENGE-MARKER-2468"
    body = _handshake_body(marker_challenge)
    with caplog.at_level(logging.INFO, logger="external_events.interfaces"):
        response = await _post(
            stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
        )

    assert response.status == 200
    assert response.body == json.dumps({"challenge": marker_challenge}).encode("utf-8")
    log_text = caplog.text
    assert f"endpoint_id={_ACTIVE_ID}" in log_text
    assert "status=200" in log_text
    assert marker_challenge not in log_text


# --------------------------------------------------------------------- #
# 11. ASGI plumbing edge — non-http scopes pass through untouched
# --------------------------------------------------------------------- #


async def test_non_http_scope_passes_channels_through_untouched() -> None:
    """Lifespan (or any non-http) scope: the middleware wraps nothing — the
    inner app receives the ORIGINAL receive/send callables."""

    async def inner(
        scope: dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[dict[str, Any]], Any],
    ) -> None:
        seen.append((scope, receive, send))

    seen: list[tuple[dict[str, Any], Callable[[], Any], Callable[[dict[str, Any]], Any]]] = []
    middleware = ChallengeEchoMiddleware(inner)

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        return None

    scope: dict[str, Any] = {"type": "lifespan"}
    await middleware(scope, receive, send)

    assert len(seen) == 1
    assert seen[0][0] is scope
    assert seen[0][1] is receive
    assert seen[0][2] is send
