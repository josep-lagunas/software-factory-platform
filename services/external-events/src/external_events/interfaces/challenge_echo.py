"""url_verification challenge-echo ASGI middleware (SFP-254).

A **composition-level** wrapper around the SFP-120
:class:`~external_events.interfaces.webhook.WebhookIngressEndpoint` that
answers Slack's save-time handshake: when the Event Subscriptions Request
URL is saved, Slack POSTs ``{"type": "url_verification", "challenge": …}``
and expects the challenge value echoed back over HTTP before the URL is
accepted. Everything provider-specific stops at transport level (ID-028):
the SFP-120 endpoint stays provider-agnostic (a handshake body is just an
opaque authenticated payload there — its fence and docstring are
untouched), and this middleware is the one composition piece that knows
the handshake shape. It never touches the bus contract: a handshake still
publishes exactly one
:class:`~sfp_contracts.events.ExternalEventReceived` (the SFP-132
``slack_inbound`` consumer filters the envelope at the bus).

Mechanics (pure ASGI, no framework — the SFP-132 discipline):

- **Receive is wrapped, never pre-read.** The SFP-120 endpoint reads the
  raw body itself (its ``_read_body`` drains ``receive`` until
  ``more_body`` is false — verified against the landed loop); the wrapper
  simply records each message's ``body`` bytes as the endpoint drains
  them and returns the message unchanged. The inner app therefore sees an
  equivalent ``receive`` with no pre-read and no re-implementation, and
  signature verification still happens over the exact delivered bytes.
- **Send is wrapped, and 200s are deferred.** A started ASGI response
  cannot be unsent, so the wrapper streams **non-200** responses through
  verbatim (401 auth failures, 404 unknown/inactive endpoints — the
  security invariant: the challenge is never echoed to an unauthenticated
  caller) but holds the inner app's events for a **200** — the endpoint's
  responses are fixed and non-streaming (one ``http.response.start`` plus
  one complete ``http.response.body``), so holding them cannot alter
  streaming behavior.
- **The decision runs after the inner app completes**, only on a held
  200: if the captured request body parses as a JSON **dict** with
  ``type == "url_verification"`` and a **non-empty string** ``challenge``,
  the held events are replaced by ``200 application/json
  {"challenge": <value>}`` (plain :func:`json.dumps`, the challenge
  verbatim). Every other 200 — non-handshake bodies, malformed handshakes
  (missing/empty/non-string challenge), non-dict JSON — replays the held
  inner events verbatim, byte-identical to the unwrapped endpoint. Errors
  the endpoint itself raises (SFP-122 misconfiguration) propagate
  untouched; since a held 200 was never flushed, the server still owns
  the response.

Logging discipline (SFP-120, binding): only the ``endpoint_id`` and the
response status are ever logged — never the challenge value, never header
values, never payload bodies.

Determinism (MAS §12.7): no clock, no network, no randomness — the
decision is a pure function of the inner app's response events and the
request body bytes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

# Same-package route parser — reused (not re-derived) so the middleware
# reads the SAME ``/webhooks/{endpoint_id}`` shape the endpoint routes on;
# duplicating the shape here would drift from the endpoint's route logic.
from external_events.interfaces.webhook import _endpoint_id_from_path

__all__ = ["ChallengeEchoMiddleware"]

#: Slack's handshake envelope discriminator (ID-028 stops at this one
#: transport-level constant; no other field of the payload is interpreted
#: beyond the challenge value itself).
_HANDSHAKE_TYPE: Final[str] = "url_verification"

#: The handshake field whose value is echoed verbatim.
_CHALLENGE_KEY: Final[str] = "challenge"

#: Slack's handshake-response content type (matches the endpoint's own
#: JSON responses byte-for-byte in shape).
_JSON_CONTENT_TYPE: Final[list[tuple[bytes, bytes]]] = [(b"content-type", b"application/json")]

_LOGGER = logging.getLogger(__name__)

#: ASGI channel shapes (the loose ``dict[str, Any]`` style of the SFP-120
#: endpoint, kept identical here).
_Receive = Callable[[], Awaitable[dict[str, Any]]]
_Send = Callable[[dict[str, Any]], Awaitable[None]]
_ASGIApp = Callable[[dict[str, Any], _Receive, _Send], Awaitable[None]]


class ChallengeEchoMiddleware:
    """Echo Slack's ``url_verification`` challenge over the inner app's 200.

    Composition piece (SFP-254), not an architectural change to the
    endpoint: wrap any ASGI app — here the SFP-120
    :class:`~external_events.interfaces.webhook.WebhookIngressEndpoint` —
    and an authenticated, published, handshake-shaped delivery comes back
    as ``200 {"challenge": <value>}`` instead of the endpoint's plain OK
    body. Everything else passes through byte-identical: auth failures
    (401), unknown/inactive endpoints (404), misconfiguration errors
    (propagated), and every non-handshake or malformed-handshake body.

    The inner app is exposed read-only as :attr:`app` (the standard ASGI
    middleware convention) so composition roots and tests can assert the
    wrapping without reaching into private state.
    """

    def __init__(self, app: _ASGIApp) -> None:
        """Wrap ``app``; the middleware adds state only per request.

        Args:
            app: The inner ASGI application — for SFP-254, the SFP-120
                webhook ingress endpoint.
        """
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: _Receive,
        send: _Send,
    ) -> None:
        """Serve one ASGI request, echoing a held-200 handshake only."""
        if scope.get("type") != "http":
            # Not an HTTP request (lifespan, …): nothing to peek or echo —
            # pass the channels through untouched.
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def wrapped_receive() -> dict[str, Any]:
            # Pass-through wrapper: capture each drained body chunk, return
            # the message unchanged. No pre-read — the inner app drains the
            # channel at its own pace (and a request it never reads simply
            # leaves ``body_chunks`` empty, which can never classify as a
            # handshake).
            message = await receive()
            chunk = message.get("body")
            if chunk:
                body_chunks.append(chunk)
            return message

        # A 200 from the inner app is HELD (not flushed) until the inner
        # app completes and the handshake decision has run; anything else
        # streams through the outer ``send`` verbatim as it arrives.
        holding = False
        held: list[dict[str, Any]] = []

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal holding
            if holding:
                # The inner 200 was already held — hold every subsequent
                # event too, so the eventual replay is the exact original
                # sequence (start first, body in order).
                held.append(message)
                return
            if message.get("type") == "http.response.start" and message.get("status") == 200:
                holding = True
                held.append(message)
                return
            await send(message)

        await self.app(scope, wrapped_receive, wrapped_send)

        if not holding:
            # Non-200 (already streamed verbatim) or nothing sent — done.
            return

        challenge = _handshake_challenge(b"".join(body_chunks))
        if challenge is not None:
            await _send_challenge(send, scope.get("path"), challenge)
            return
        # Not a well-formed handshake: replay the inner app's exact events.
        for message in held:
            await send(message)


def _handshake_challenge(raw_body: bytes) -> str | None:
    """Classify ``raw_body`` as a handshake; the challenge value or ``None``.

    Echo shape (SFP-254, binding): a JSON **dict** whose ``type`` is
    ``"url_verification"`` and whose ``challenge`` is a **non-empty
    string**. Malformed JSON, a non-dict top level, another ``type``, or a
    missing/empty/non-string challenge all return ``None`` → the inner
    app's own response is replayed verbatim. JSON well-formedness plus the
    two handshake fields — nothing else in the payload is ever inspected.
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") != _HANDSHAKE_TYPE:
        return None
    challenge = parsed.get(_CHALLENGE_KEY)
    if not isinstance(challenge, str) or not challenge:
        return None
    return challenge


async def _send_challenge(send: _Send, path: object, challenge: str) -> None:
    """Send the echo response — ``200 application/json {"challenge": …}``.

    Plain :func:`json.dumps` of ``{"challenge": <value>}`` — the value
    verbatim, nothing added. Logging (SFP-120 discipline): endpoint_id and
    status ONLY; the challenge value itself is never logged.
    """
    endpoint_id = _endpoint_id_from_path(path)
    _LOGGER.info("challenge echo endpoint_id=%s status=%d", endpoint_id, 200)
    payload = json.dumps({_CHALLENGE_KEY: challenge}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": list(_JSON_CONTENT_TYPE),
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": payload,
            "more_body": False,
        }
    )
