"""Single external webhook ingress endpoint (SFP-120).

An ASGI application serving ``POST /webhooks/{endpoint_id}`` — the one
public ingress that composes the landed stack into the MAS §9.2 capability:
resolve the endpoint's configuration (SFP-121), authenticate the **raw**
request bytes via the SFP-122 factory + SFP-123 strategy, and publish one
:class:`~sfp_contracts.events.ExternalEventReceived` via the SFP-124
publisher with the parsed JSON body as an **opaque** payload. Everything
provider-specific stops at transport level (ID-028): no event-type
filtering, no payload shape checks, no provider handshake interpretation
(e.g. Slack's ``url_verification`` challenge echo is SFP-132's concern, not
this endpoint's — a handshake body is just an opaque authenticated payload
here).

Framework choice (per the PRSpec's implementation note): the binding part
is the response contract, not the framework, and dependency-lightness is
preferred — so this endpoint follows the raw-ASGI discipline of the landed
SFP-132 receiver (:mod:`communication.entrypoints.slack_events_endpoint`):
no web-framework dependency, exact request bytes read straight off the ASGI
``receive`` channel before any parsing.

Deterministic handler order (MAS §9.2, binding):

1. **Read the RAW body bytes** via ASGI ``receive`` — before resolution,
   authentication, and any JSON parsing. Signature verification (SFP-123)
   needs the exact signed bytes; framework body parsing that re-serializes
   the request would break every signature, so nothing parses the body
   before the strategy has seen it.
2. **Resolve** ``endpoint_id`` via the injected SFP-121 resolver.
   :class:`~external_events.application.EndpointConfigNotFoundError` (no
   such endpoint) and a resolved ``INACTIVE`` status both produce the
   **same** ``404`` — identical status *and* body, by design, so the
   response leaks nothing about which endpoints exist.
3. **Authenticate**: build the strategy via the SFP-122 factory (bound at
   construction over the injected :class:`~sfp_config.providers.\
SecretProvider` and registry) for the resolved endpoint's
   ``(auth_strategy, secret_ref)``, then call
   ``authenticate(raw_body, headers)``. ``False`` → ``401`` with **zero**
   publishes, returning before any parsing or publication. Factory
   misconfiguration errors (SFP-122 ``UnknownAuthStrategyError``, an
   unresolvable secret from the provider) are server-side faults this
   PRSpec's binding response contract does not map: they propagate to the
   ASGI server's error handling (a 5xx), still with zero publishes.
4. **Parse** ``json.loads(raw_body)`` — only now that auth has seen the
   exact signed bytes. Malformed JSON (or a top-level non-object, which
   cannot satisfy the ``ExternalEventReceived.payload: dict[str, Any]``
   contract type — a type-compatibility check, not interpretation) →
   ``400`` with zero publishes.
5. **external_id = sha256(raw_body)** — hashing is not interpretation
   (MAS §9.2): it is deterministic across redeliveries of the same bytes,
   which is exactly what makes the SFP-124 ``idempotency_key``
   (``f"{source}:{external_id}"``) dedupe them.
6. **Publish** one ``ExternalEventReceived`` via the injected SFP-124
   publisher: ``source`` = the resolved endpoint's ``provider``,
   ``external_id`` = the hash, ``payload`` = the parsed body, verbatim.
7. **Respond** ``200``.

Logging discipline (binding): only ``endpoint_id`` and the response status
are ever logged — never secrets, never header values, never payload bodies.

Determinism (MAS §12.7): no clock is read here (the replay window lives in
the slack_signature strategy, SFP-123, behind its injected clock), no
network beyond the injected seams, no ordering dependence.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Final

from sfp_config.providers import SecretProvider

from external_events.application import (
    AUTH_STRATEGY_REGISTRY,
    AuthenticationStrategy,
    EndpointConfigNotFoundError,
    EndpointConfigResolver,
    ExternalEventPublisher,
    StrategyRegistry,
    build_authentication_strategy,
)
from external_events.infrastructure.persistence import EndpointStatus

if TYPE_CHECKING:
    from sfp_messaging.bus import MessageBus

__all__ = [
    "WEBHOOK_PATH_PREFIX",
    "WebhookIngressEndpoint",
]

#: Path prefix of the single ingress route: ``POST /webhooks/{endpoint_id}``.
WEBHOOK_PATH_PREFIX: Final[str] = "/webhooks/"

#: The 404 body — ONE constant shared by the unknown-endpoint and
#: INACTIVE-endpoint branches so the two responses are byte-identical (the
#: anti-enumeration requirement: no endpoint-existence leak).
_NOT_FOUND_BODY: Final[dict[str, str]] = {"error": "not found"}

_UNAUTHORIZED_BODY: Final[dict[str, str]] = {"error": "unauthorized"}

_BAD_PAYLOAD_BODY: Final[dict[str, str]] = {"error": "invalid payload"}

_OK_BODY: Final[dict[str, bool]] = {"ok": True}

#: Largest accepted body, bytes — the SFP-132 discipline, ported: bounds
#: hashing/parsing work on untrusted input. An over-limit body is truncated,
#: which can only make its signature mismatch (→ 401) or its JSON
#: unparseable (→ 400) — never a memory hazard.
_MAX_BODY_BYTES: Final[int] = 1_048_576

_LOGGER = logging.getLogger(__name__)

#: The bound SFP-122 factory call shape the endpoint makes per request:
#: ``(auth_strategy, secret_ref) -> AuthenticationStrategy`` (the injected
#: ``SecretProvider`` and registry are bound at construction via
#: :func:`functools.partial`).
_StrategyBuilder = Callable[[str, str], AuthenticationStrategy]


class WebhookIngressEndpoint:
    """ASGI application serving ``POST /webhooks/{endpoint_id}`` (SFP-120).

    Constructor-injected collaborators (all four named by the PRSpec):

    - ``resolver`` — the SFP-121 :class:`EndpointConfigResolver`; an unknown
      id raises, an INACTIVE id resolves, and both map to the same 404.
    - ``secret_provider`` — the SFP-86 seam the bound SFP-122 factory
      resolves ``secret_ref`` through (strategies never load secrets,
      ID-029).
    - ``publisher`` — the SFP-124 :class:`ExternalEventPublisher`; the one
      publish target. Every publish goes through it — this endpoint never
      touches the bus directly.
    - ``bus`` — the :class:`~sfp_messaging.bus.MessageBus` the publisher
      publishes onto (in-memory satisfies the Protocol; zero AWS — SFP-118
      is Phase-B). Held as the wiring-point reference so the composition
      root completes the full transport wiring at the ingress and the
      SFP-118 re-plumb changes no constructor.
    - ``registry`` (keyword) — the SFP-122 strategy registry the factory
      selects from; defaults to :data:`AUTH_STRATEGY_REGISTRY`, populated
      with the v0 strategies by importing ``external_events.application``.

    Any other method, path, or path shape is answered with the shared 404
    and no resolution, no secret pull, no publish.
    """

    def __init__(
        self,
        resolver: EndpointConfigResolver,
        secret_provider: SecretProvider,
        publisher: ExternalEventPublisher,
        bus: MessageBus,
        *,
        registry: StrategyRegistry = AUTH_STRATEGY_REGISTRY,
    ) -> None:
        self._resolver = resolver
        self._publisher = publisher
        self._bus = bus
        self._build_strategy: _StrategyBuilder = partial(
            build_authentication_strategy,
            secret_provider=secret_provider,
            registry=registry,
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """ASGI entry point: route ``POST /webhooks/{endpoint_id}`` only."""
        if scope["type"] != "http":  # pragma: no cover — no lifespan protocol
            return
        endpoint_id = _endpoint_id_from_path(scope.get("path"))
        if scope.get("method") == "POST" and endpoint_id is not None:
            await self._handle_delivery(endpoint_id, scope, receive, send)
            return
        await _send_json(send, 404, _NOT_FOUND_BODY)

    # ------------------------------------------------------------------ #
    # Delivery handling (deterministic order, MAS §9.2)
    # ------------------------------------------------------------------ #

    async def _handle_delivery(
        self,
        endpoint_id: str,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Run one delivery through read → resolve → auth → parse → publish."""
        # (1) RAW body first — the exact signed bytes, before anything else.
        raw_body = await _read_body(receive)
        headers = _header_map(scope)

        # (2) Resolve; unknown and INACTIVE produce the SAME 404 (the shared
        # constant guarantees byte-identical status and body).
        try:
            resolved = self._resolver.resolve(endpoint_id)
        except EndpointConfigNotFoundError:
            await self._respond(send, endpoint_id, 404, _NOT_FOUND_BODY)
            return
        if resolved.status is not EndpointStatus.ACTIVE:
            await self._respond(send, endpoint_id, 404, _NOT_FOUND_BODY)
            return

        # (3) Authenticate on (raw_body, headers); reject → 401, no publish.
        strategy = self._build_strategy(resolved.auth_strategy, resolved.secret_ref)
        if not strategy.authenticate(raw_body, headers):
            await self._respond(send, endpoint_id, 401, _UNAUTHORIZED_BODY)
            return

        # (4) Parse — only after auth has seen the exact signed bytes.
        payload = _parse_json_object(raw_body)
        if payload is None:
            await self._respond(send, endpoint_id, 400, _BAD_PAYLOAD_BODY)
            return

        # (5)+(6) Opaque publish: hashing is not interpretation (MAS §9.2),
        # and identical redeliveries collide on the SFP-124 idempotency_key.
        external_id = hashlib.sha256(raw_body).hexdigest()
        await self._publisher.publish(resolved.provider, external_id, payload)

        # (7) Published → 200.
        await self._respond(send, endpoint_id, 200, _OK_BODY)

    async def _respond(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        endpoint_id: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        """Send one response and log it — endpoint_id and status ONLY.

        Never secrets, never header values, never payload bodies (binding
        logging discipline of the PRSpec).
        """
        _LOGGER.info("webhook ingress endpoint_id=%s status=%d", endpoint_id, status)
        await _send_json(send, status, body)


# ---------------------------------------------------------------------- #
# ASGI plumbing — the SFP-132 discipline, ported verbatim in spirit
# ---------------------------------------------------------------------- #


async def _read_body(receive: Callable[[], Awaitable[dict[str, Any]]]) -> bytes:
    """Read the full request body from the ASGI receive channel.

    Caps at :data:`_MAX_BODY_BYTES`; an over-limit body is truncated, which
    can only make its signature mismatch (→ ``401``) or its JSON unparseable
    (→ ``400``) — never a memory hazard.
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


def _endpoint_id_from_path(path: object) -> str | None:
    """Extract ``{endpoint_id}`` from ``/webhooks/{endpoint_id}`` else ``None``.

    Strict shape: exactly one non-empty segment after the prefix —
    ``/webhooks/abc/extra``, ``/webhooks/``, and bare ``/webhooks`` are all
    route misses (404), never deliveries.
    """
    if not isinstance(path, str) or not path.startswith(WEBHOOK_PATH_PREFIX):
        return None
    remainder = path.removeprefix(WEBHOOK_PATH_PREFIX)
    if not remainder or "/" in remainder:
        return None
    return remainder


def _parse_json_object(raw_body: bytes) -> dict[str, Any] | None:
    """Parse the body as a JSON object; ``None`` on anything else.

    Malformed JSON (``JSONDecodeError`` / ``UnicodeDecodeError`` /
    ``ValueError``) and a well-formed top-level non-object alike return
    ``None`` → the caller's ``400``. The non-object rule is not
    interpretation: ``ExternalEventReceived.payload`` is typed
    ``dict[str, Any]`` (SFP-124 contract), so an array/scalar/str top level
    is a body this service has no way to carry — JSON well-formedness plus
    contract-type compatibility, nothing more. No field of the object is
    ever inspected.
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
