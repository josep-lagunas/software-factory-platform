"""Tests for the SFP-120 single external webhook ingress endpoint.

Exercises the binding response contract and acceptance criteria against the
REAL landed stack — SFP-121 resolver over in-memory SQLite (attached
``operational`` schema, per the SFP-121 test precedent), the SFP-122
factory over a fake SecretProvider with the real SFP-123 registry
(``github_hmac`` — chosen because it is clock-free, so the suite never
touches a wall clock), and the real SFP-124 publisher over a spy bus:

- authenticated delivery → exactly ONE ``ExternalEventReceived`` published
  with the opaque parsed-JSON payload and ``external_id = sha256(raw body)``
  (HTTP 200);
- unknown endpoint → 404; INACTIVE endpoint → 404; the two responses are
  byte-identical (anti-enumeration);
- auth failure → 401 and ZERO publishes; malformed JSON post-auth → 400 and
  ZERO publishes; auth precedes parsing (a bad signature over garbage JSON
  is 401, never 400);
- identical redelivery → the SAME ``external_id`` and the SAME SFP-124
  ``idempotency_key`` (dedupe by construction), fresh ``message_id``;
- route discipline: only ``POST /webhooks/{endpoint_id}`` is a delivery —
  wrong method/path shapes are the shared 404 with zero publishes;
- logging carries endpoint_id and status ONLY — never secrets, header
  values, or payload bodies;
- the interface is exported from ``external_events.interfaces``.

Deterministic: no network, no clock in any assertion (the replay window
lives in the SFP-123 strategy, not here), no ordering dependence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, NamedTuple

import pytest
import sqlalchemy as sa
from external_events.application import EndpointConfigResolver, ExternalEventPublisher
from external_events.infrastructure.persistence import (
    Base,
    EndpointConfig,
    EndpointStatus,
)
from external_events.interfaces import WEBHOOK_PATH_PREFIX, WebhookIngressEndpoint
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sqlalchemy.orm import Session, sessionmaker

#: Fixed, timezone-aware seed timestamps — deterministic, and they keep the
#: PostgreSQL-only ``now()`` server default out of the SQLite round-trip.
_SEED_TS = datetime(2026, 1, 1, tzinfo=UTC)

#: The secret VALUES the fake provider hands the SFP-122 factory. Names are
#: the seeded rows' ``secret_ref`` strings (references, never values —
#: ID-016); the values below are test fixtures, not secrets.
_SECRETS: dict[str, str] = {
    "GH_WEBHOOK_SECRET": "gh-test-webhook-secret-value",
    "SLACK_SIGNING_SECRET": "slack-test-signing-secret-value",
}

#: A deliberately opaque, unicode-bearing delivery body — the ingress must
#: carry the PARSED dict verbatim without inspecting or reshaping it.
_BODY: bytes = (
    b'{"action":"opened","pull_request":{"number":42,"user":{"login":"octo"}},'
    b'"text":"\xc2\xbfqu\xc3\xa9 pasa? \xf0\x9f\x9a\x80","ok":true}'
)

#: The endpoint ids seeded per test (see :func:`_seed_configs`).
_ACTIVE_ID = "gh-main"
_INACTIVE_ID = "slack-off"
_UNKNOWN_ID = "no-such-endpoint"


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
    """The wired ingress under test: app + the observable publish sink."""

    app: WebhookIngressEndpoint
    bus: _SpyBus


# --------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------- #


@contextmanager
def _session_factory_cm() -> Iterator[Callable[[], AbstractContextManager[Session]]]:
    """A fresh in-memory database per test, ``operational`` schema attached.

    Ported from the SFP-121 resolver tests: one underlying connection stays
    open for the whole test (StaticPool) so rows planted by the seeding
    session are visible to the resolver's borrowed sessions; the schema is
    attached because ``operational.endpoint_configs`` is schema-qualified
    and SQLite only knows schemas as attached databases. Hermetic — nothing
    crosses the test's boundary.
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
    """Plant the three endpoint rows the branch tests need.

    - ``gh-main`` — ACTIVE github endpoint (``github_hmac``, clock-free).
    - ``slack-off`` — INACTIVE slack endpoint (resolution must 404 it).
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
        app = WebhookIngressEndpoint(
            EndpointConfigResolver(factory),
            _FakeSecretProvider(_SECRETS),
            ExternalEventPublisher(bus),
            bus,
        )
        yield _Stack(app=app, bus=bus)


def _github_signature(secret: str, body: bytes) -> str:
    """A valid ``X-Hub-Signature-256`` value over exactly ``body``."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _valid_headers(body: bytes) -> list[tuple[bytes, bytes]]:
    signature = _github_signature(_SECRETS["GH_WEBHOOK_SECRET"], body)
    return [(b"x-hub-signature-256", signature.encode())]


async def _post(
    app: WebhookIngressEndpoint,
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


# --------------------------------------------------------------------- #
# 1. Authenticated delivery → one opaque publish, 200
# --------------------------------------------------------------------- #


async def test_authenticated_delivery_publishes_one_event_and_returns_200(
    stack: _Stack,
) -> None:
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )

    assert response.status == 200
    assert json.loads(response.body) == {"ok": True}
    assert (b"content-type", b"application/json") in response.headers

    assert len(stack.bus.published) == 1
    envelope = stack.bus.published[0]
    assert envelope.event_type is EventType.EXTERNAL_EVENT_RECEIVED

    event = envelope.payload
    assert isinstance(event, ExternalEventReceived)
    assert event.source == "github"
    # external_id = sha256 of the RAW body — verified independently here.
    assert event.external_id == hashlib.sha256(_BODY).hexdigest()
    # Opaque carriage: the parsed JSON dict, verbatim, nothing reshaped.
    assert event.payload == json.loads(_BODY)
    # SFP-124 dedupe key derives from the event identity.
    assert envelope.idempotency_key == f"github:{hashlib.sha256(_BODY).hexdigest()}"


async def test_payload_is_carried_opaque_without_field_inspection(stack: _Stack) -> None:
    """No event-type filtering or shape checks: a body with a ``type`` field
    that is NOT a provider handshake we know (e.g. Slack's
    ``url_verification``) is still a plain authenticated delivery — 200 with
    exactly one publish, no challenge echo, no special-casing."""
    body = json.dumps({"type": "url_verification", "challenge": "echo-me-not"}).encode()
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=_valid_headers(body)
    )

    assert response.status == 200
    # No challenge echo: the response is the plain OK body, never the payload.
    assert json.loads(response.body) == {"ok": True}
    assert len(stack.bus.published) == 1
    assert stack.bus.published[0].payload.payload == {
        "type": "url_verification",
        "challenge": "echo-me-not",
    }


async def test_multi_chunk_body_is_reassembled_to_the_signed_bytes(stack: _Stack) -> None:
    """The raw ASGI stream is reassembled before auth — the signature is
    verified over the exact delivered bytes, chunk boundaries and all."""
    head, tail = _BODY[: len(_BODY) // 2], _BODY[len(_BODY) // 2 :]
    response = await _post(
        stack.app,
        path=f"/webhooks/{_ACTIVE_ID}",
        body=_BODY,
        headers=_valid_headers(_BODY),
        chunks=[head, tail],
    )

    assert response.status == 200
    assert stack.bus.published[0].payload.external_id == hashlib.sha256(_BODY).hexdigest()


async def test_malformed_header_entries_are_ignored_not_fatal(stack: _Stack) -> None:
    """Garbage header entries on an untrusted edge are skipped; the valid
    signature header still authenticates."""
    headers = [
        (b"not-a-pair",),
        ["x", "y"],
        [b"ok-name", 7],
        *_valid_headers(_BODY),
    ]
    response = await _post(
        stack.app,
        path=f"/webhooks/{_ACTIVE_ID}",
        body=_BODY,
        headers=headers,  # type: ignore[arg-type]
    )

    assert response.status == 200
    assert len(stack.bus.published) == 1


# --------------------------------------------------------------------- #
# 2. Redelivery idempotency
# --------------------------------------------------------------------- #


async def test_identical_redelivery_yields_same_external_id_and_key(stack: _Stack) -> None:
    first = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )
    second = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )

    assert first.status == second.status == 200
    # The publisher is stateless: each delivery publishes (dedupe is
    # downstream, by key) — two envelopes, one external fact.
    assert len(stack.bus.published) == 2
    first_event, second_event = stack.bus.published
    assert first_event.payload.external_id == second_event.payload.external_id
    assert first_event.idempotency_key == second_event.idempotency_key
    # A redelivery is a NEW message carrying an OLD fact.
    assert first_event.message_id != second_event.message_id


# --------------------------------------------------------------------- #
# 3. 404: unknown and INACTIVE — indistinguishable, zero publishes
# --------------------------------------------------------------------- #


async def test_unknown_endpoint_is_404_with_zero_publishes(stack: _Stack) -> None:
    response = await _post(
        stack.app, path=f"/webhooks/{_UNKNOWN_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )

    assert response.status == 404
    assert stack.bus.published == []


async def test_inactive_endpoint_is_404_with_zero_publishes(stack: _Stack) -> None:
    # No auth headers at all: INACTIVE must short-circuit at resolution —
    # before any strategy is built or secret pulled.
    response = await _post(stack.app, path=f"/webhooks/{_INACTIVE_ID}", body=_BODY, headers=[])

    assert response.status == 404
    assert stack.bus.published == []


async def test_unknown_and_inactive_404_responses_are_indistinguishable(stack: _Stack) -> None:
    unknown = await _post(
        stack.app, path=f"/webhooks/{_UNKNOWN_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )
    inactive = await _post(
        stack.app, path=f"/webhooks/{_INACTIVE_ID}", body=_BODY, headers=_valid_headers(_BODY)
    )

    assert unknown.status == inactive.status == 404
    assert unknown.body == inactive.body


# --------------------------------------------------------------------- #
# 4. 401: auth failure — zero publishes, and it precedes parsing
# --------------------------------------------------------------------- #


async def test_bad_signature_is_401_with_zero_publishes(stack: _Stack) -> None:
    headers = [(b"x-hub-signature-256", b"sha256=" + b"0" * 64)]
    response = await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=_BODY, headers=headers)

    assert response.status == 401
    assert json.loads(response.body) == {"error": "unauthorized"}
    assert stack.bus.published == []


async def test_missing_auth_header_is_401_with_zero_publishes(stack: _Stack) -> None:
    response = await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=_BODY, headers=[])

    assert response.status == 401
    assert stack.bus.published == []


async def test_auth_precedes_parse_garbage_body_with_bad_signature_is_401(
    stack: _Stack,
) -> None:
    """Deterministic order: authentication sees the bytes before the JSON
    parser does, so an unauthenticated malformed body is 401 — never 400."""
    garbage = b"this is {{{ not json"
    headers = [(b"x-hub-signature-256", b"sha256=deadbeef")]
    response = await _post(stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=garbage, headers=headers)

    assert response.status == 401
    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 5. 400: malformed JSON post-auth — zero publishes
# --------------------------------------------------------------------- #


async def test_malformed_json_after_auth_is_400_with_zero_publishes(stack: _Stack) -> None:
    garbage = b"this is {{{ not json"
    response = await _post(
        stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=garbage, headers=_valid_headers(garbage)
    )

    assert response.status == 400
    assert json.loads(response.body) == {"error": "invalid payload"}
    assert stack.bus.published == []


async def test_non_utf8_body_after_auth_is_400(stack: _Stack) -> None:
    invalid_utf8 = b'{"k": "\xff\xfe"}'
    response = await _post(
        stack.app,
        path=f"/webhooks/{_ACTIVE_ID}",
        body=invalid_utf8,
        headers=_valid_headers(invalid_utf8),
    )

    assert response.status == 400
    assert stack.bus.published == []


async def test_top_level_non_object_json_is_400(stack: _Stack) -> None:
    """Well-formed JSON that is not an object cannot satisfy the SFP-124
    ``payload: dict[str, Any]`` contract type — 400, not a carriage guess."""
    for top_level in (b"[1, 2, 3]", b'"a string"', b"42"):
        response = await _post(
            stack.app,
            path=f"/webhooks/{_ACTIVE_ID}",
            body=top_level,
            headers=_valid_headers(top_level),
        )
        assert response.status == 400
    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 6. Route discipline
# --------------------------------------------------------------------- #


async def test_only_post_webhooks_endpoint_id_is_a_delivery(stack: _Stack) -> None:
    """Wrong method and wrong path shapes share the 404 — no resolution, no
    publish."""
    cases: list[tuple[str, str]] = [
        ("GET", f"/webhooks/{_ACTIVE_ID}"),
        ("PUT", f"/webhooks/{_ACTIVE_ID}"),
        ("POST", "/webhooks/"),  # empty endpoint_id
        ("POST", "/webhooks"),  # no trailing segment
        ("POST", f"/webhooks/{_ACTIVE_ID}/extra"),  # extra path segment
        ("POST", "/other/path"),
        ("POST", "/webhooks2/abc"),  # prefix must match exactly
    ]
    for method, path in cases:
        response = await _post(
            stack.app, path=path, body=_BODY, headers=_valid_headers(_BODY), method=method
        )
        assert response.status == 404, (method, path)
    assert stack.bus.published == []


# --------------------------------------------------------------------- #
# 7. Logging discipline
# --------------------------------------------------------------------- #


async def test_logs_carry_endpoint_id_and_status_only(
    stack: _Stack, caplog: pytest.LogCaptureFixture
) -> None:
    secret_marker = _SECRETS["GH_WEBHOOK_SECRET"]
    body = json.dumps({"note": "LOG-BODY-MARKER-789"}).encode()
    headers = [
        *_valid_headers(body),
        (b"x-trace", b"LOG-HEADER-MARKER-456"),
    ]
    with caplog.at_level(logging.INFO, logger="external_events.interfaces.webhook"):
        response = await _post(
            stack.app, path=f"/webhooks/{_ACTIVE_ID}", body=body, headers=headers
        )

    assert response.status == 200
    log_text = caplog.text
    # What MUST be there: the endpoint id and the status code.
    assert f"endpoint_id={_ACTIVE_ID}" in log_text
    assert "status=200" in log_text
    # What MUST NOT be there: secrets, header values, payload bodies.
    assert secret_marker not in log_text
    assert "LOG-BODY-MARKER-789" not in log_text
    assert "LOG-HEADER-MARKER-456" not in log_text
    assert _github_signature(secret_marker, body) not in log_text


# --------------------------------------------------------------------- #
# 8. Exports
# --------------------------------------------------------------------- #


def test_interfaces_exports_the_endpoint() -> None:
    from external_events import interfaces

    assert interfaces.WebhookIngressEndpoint is WebhookIngressEndpoint
    assert WEBHOOK_PATH_PREFIX == "/webhooks/"
