"""Phase-A dev composition — the closed Slack dogfood loop (SFP-257).

**Phase-A dev glue — in-process imports across services are a dev monolith
convenience, NOT a MAS boundary change** (MAS §9.4 is the reference sequence,
not a redesign target). This module composes the already-landed Communication
components (SFP-129 interaction lifecycle, SFP-132 Slack inbound consumer,
SFP-134 CommunicationAgent, SFP-135 CONFIRM flow, SFP-136 notifications
handler, SFP-244 ``SlackOutboundClient``) into the external-events dev webhook
runner so a real Slack message produces a bot reply. It introduces NO new
domain behavior anywhere — every effect is a landed component's.

Wiring (all in-process, reusing the runner's existing bus + in-memory SQLite
idioms — no second persistence stack):

1. The runner's :func:`~external_events.entrypoints.dev_webhook.build_dev_app`
   still owns ingress: resolver + publisher + bus + the challenge-echo-wrapped
   webhook endpoint (SFP-120/132/254).
2. Communication gets its own in-memory SQLite engine with the ``business``
   schema attached (the SFP-129 test recipe, the same StaticPool idiom the
   runner uses for ``operational``) and an
   :class:`~communication.application.interaction_service.InteractionService`
   over it.
3. The :class:`~communication.application.communication_agent.CommunicationAgent`
   runs over a dev GLM ``AgentRuntime`` (:class:`GlmAgentRuntime`) configured
   from the SFP_-prefixed settings the repo already carries
   (:class:`DevAgentSettings`).
4. The SFP-135 :class:`~communication.application.confirm_flow.ConfirmFlow`,
   the SFP-136 :class:`~communication.application.notifications.NotificationService`
   over a **dev-only** :class:`DevSlackDestinationResolver` (session_id →
   channel/thread mapping — explicitly NOT the SFP-128 identity query) and the
   SFP-244 ``SlackOutboundClient`` as the delivery port.
5. A :class:`DevCommunicationRouter` — a thin ``SlackInboundConsumer``
   subclass — routes each inbound outcome to its user-visible effect, then
   ``set_slack_inbound_consumer(router)`` binds it, strictly BEFORE the runner
   serves traffic (SFP-132).

Fail-loud startup: a missing Slack secret, a missing/malformed GLM setting or
an unreachable GLM endpoint raises :class:`DevCompositionError`; ``main()``
prints the reason to stderr and exits NON-ZERO instead of serving a runner
that 500s on every delivery.

Graceful degradation at DELIVERY time (fail-loud is startup-only): a GLM 200
whose body holds no JSON object is retried exactly once with a stricter
re-prompt inside :class:`GlmAgentRuntime`; a summary that still fails gets a
short in-thread apology (:data:`SUMMARIZATION_APOLOGY`) and a 200 — Slack
must never retry-storm a 500ing webhook. Only PLAIN messages count as user
input: anything sub-typed or bot-authored (the app's own ``bot_message``
echo, and the ``message_replied`` parent sub-event every bot reply into a
thread emits, which carries the human's user id) is dropped at inbound
interpretation (:func:`~communication.interfaces.slack_inbound.\
parse_slack_message`, SFP-257 whitelist) so no reply can re-feed the loop.
And the webhook ACKS BEFORE PROCESSING (:class:`AckThenProcessPublisher`,
SFP-257 round 3): the 200 returns immediately after signature verification
while the publish/consume chain (including the GLM round-trip) runs as a
background task — Slack's ~3s Events API delivery timeout can no longer
turn a slow summarize into a retry storm of legitimately-shaped
redeliveries, which are additionally deduped on the SFP-124 idempotency
key.

Usage::

    uv run python -m external_events.entrypoints.dev_composition [--port 8789]

Live smoke (the closed dogfood loop, manual — the only step not automated):

1. Configure the Slack app's Event Subscriptions request URL to the tunnel's
   ``/webhooks/slack-dev`` path (cloudflared/ngrok → this runner) — the
   SFP-254 challenge echo passes the save-time ``url_validation``.
2. Export ``SLACK_SIGNING_SECRET``, ``SLACK_BOT_TOKEN``,
   ``SFP_ANTHROPIC_BASE_URL``, ``SFP_DEFAULT_MODEL`` and
   ``SFP_LLM_PROVIDER_SECRET_REF`` (or put them in ``secrets.local`` / ``.env``).
3. ``uv run python -m external_events.entrypoints.dev_composition`` — a
   non-zero exit here means the composition could not be bound (fail-loud).
4. Post a message in the registered thread: the bot replies with the
   GLM-generated summary and a ``CONFIRM`` request; reply ``CONFIRM`` → the
   runner logs the published ``UserInputReceived`` and completes the
   interaction; a further reply gets the "please start a new thread" message,
   and a brand-new thread carries the prior summary as context.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Final

import httpx
import sqlalchemy as sa
from communication.application.communication_agent import (
    ClosedInteractionOutcome,
    CommunicationAgent,
    SummarizationError,
)
from communication.application.confirm_flow import ConfirmFlow, ConfirmOutcome, CorrectionOutcome
from communication.application.interaction_service import (
    Clock,
    InteractionService,
    InteractionTransitionError,
    SessionFactory,
    session_scope,
)
from communication.application.notifications import (
    NotificationOutcome,
    NotificationService,
    SlackDestination,
)
from communication.infrastructure.persistence import Base as CommunicationBase
from communication.interfaces.outbound import OutboundMessagePort, ProviderError
from communication.interfaces.slack_inbound import (
    SlackInboundConsumer,
    SlackProviderMessage,
    parse_slack_message,
    set_slack_inbound_consumer,
)
from communication.interfaces.slack_outbound import SlackOutboundClient
from pydantic import ValidationError, field_validator
from pydantic_settings import SettingsConfigDict
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult, AgentRuntime
from sfp_config import (
    LocalSecretProvider,
    SecretProvider,
    SecretRef,
    SecretResolutionError,
    Settings,
)
from sfp_contracts.commands import NotifyUser
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from external_events.application.publisher import make_external_event_envelope

__all__ = [
    "ACCEPT_ACTION_ID",
    "ACCEPT_ACTION_VALUE",
    "BLOCK_ACTIONS_TYPE",
    "CONFIRMED_ACTIONS_BLOCK",
    "CONFIRM_HINTS",
    "CONFIRM_REQUEST_SUFFIX",
    "CLOSED_THREAD_MESSAGE",
    "DECISION_BLOCK_ID",
    "DevAgentSettings",
    "DevCommunicationRouter",
    "DevComposition",
    "DevCompositionError",
    "DevSlackDestinationResolver",
    "GlmAgentRuntime",
    "LANGUAGE_REQUESTS",
    "SUMMARIZATION_APOLOGY",
    "build_dev_composition",
    "decision_actions_block",
    "detect_language_request",
    "hint_for",
    "main",
    "summary_blocks",
]

logger = logging.getLogger(__name__)

#: The message delivered to a Slack thread whose interaction is closed
#: (MAS §9.4 Closed Interactions: request the user to start a new thread).
CLOSED_THREAD_MESSAGE: Final[str] = (
    "This thread is closed — please start a new thread and I'll carry the context over."
)

#: Appended to a regenerated summary so the user knows the ID-069 gate is open.
CONFIRM_REQUEST_SUFFIX: Final[str] = (
    "\n\nReply CONFIRM to accept this summary, or reply with a correction."
)

#: Posted to the thread when summarization fails even after the dev runtime's
#: retry — the composition degrades gracefully (the webhook returns 200 so
#: Slack does not retry-storm) instead of 500ing. Fail-loud stays
#: STARTUP-only, as the ticket specifies (SFP-257 live smoke, 2026-09-14).
SUMMARIZATION_APOLOGY: Final[str] = "Sorry — I couldn't summarize that message. Please retry."

# --------------------------------------------------------------------------- #
# Block Kit decision UX (SFP-258 — the one-button confirm)
# --------------------------------------------------------------------------- #

#: ``block_id`` of the owner's decision actions block — VERBATIM per the
#: SFP-258 Requirements (single primary button; the two-button variant is
#: superseded by the 2026-09-15 one-button product decision).
DECISION_BLOCK_ID: Final[str] = "decision_buttons"

#: ``action_id`` of the ✅ Confirm button (the structured decision the
#: block_actions router dispatches on).
ACCEPT_ACTION_ID: Final[str] = "accept_action"

#: ``value`` carried by the ✅ Confirm button.
ACCEPT_ACTION_VALUE: Final[str] = "task_accepted"

#: The top-level ``type`` of a Slack interactive payload (NOT an Events API
#: ``event_callback`` — routed as a structured decision, never a message).
BLOCK_ACTIONS_TYPE: Final[str] = "block_actions"

#: The terminal state the actions block is replaced with once the
#: interaction is confirmed — a completed thread cannot be re-confirmed
#: (late clicks land on the closed-interaction path instead).
CONFIRMED_ACTIONS_BLOCK: Final[dict[str, Any]] = {
    "type": "context",
    "block_id": DECISION_BLOCK_ID,
    "elements": [
        {"type": "mrkdwn", "text": ":white_check_mark: *Confirmed* — this summary was accepted."}
    ],
}


def decision_actions_block() -> dict[str, Any]:
    """Build the owner's EXACT decision actions block (SFP-258 Requirements).

    ``block_id`` ``decision_buttons``; ONE primary button: ``plain_text``
    ``✅ Confirm``, ``action_id`` ``accept_action``, ``value``
    ``task_accepted``. A fresh dict per call — the block is embedded in a
    chat payload and must never be shared-mutable module state.
    """
    return {
        "type": "actions",
        "block_id": DECISION_BLOCK_ID,
        "elements": [
            {
                "type": "button",
                "action_id": ACCEPT_ACTION_ID,
                "style": "primary",
                "text": {"type": "plain_text", "text": "✅ Confirm", "emoji": True},
                "value": ACCEPT_ACTION_VALUE,
            }
        ],
    }


def summary_blocks(summary_text: str, hint: str) -> list[dict[str, Any]]:
    """Build the Block Kit layout of a summary reply: summary + the button."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{summary_text}{hint}"}},
        decision_actions_block(),
    ]


# --------------------------------------------------------------------------- #
# Language adaptation (SFP-258 — language-adaptive summaries and hints)
# --------------------------------------------------------------------------- #

#: The language the summary/hint/button copy defaults to when the user
#: never asked for one.
DEFAULT_LANGUAGE: Final[str] = "en"

#: The hint line per supported language — the ID-069 gate re-request that
#: replaced the hardcoded-English ``CONFIRM_REQUEST_SUFFIX`` append. The
#: English entry IS the legacy suffix (back-compat, and the button label
#: stays the fixed '✅ Confirm' — language-adapting it is impractical at
#: Block Kit render time here and the ticket allows "when practical").
CONFIRM_HINTS: Final[dict[str, str]] = {
    "en": CONFIRM_REQUEST_SUFFIX,
    "ca": "\n\nRespon CONFIRM per acceptar aquest resum, o respon amb una correcció.",
    "es": "\n\nResponde CONFIRM para aceptar este resumen, o responde con una corrección.",
    "fr": "\n\nRépondez CONFIRM pour accepter ce résumé, ou répondez avec une correction.",
    "de": "\n\nAntworte mit CONFIRM, um diese Zusammenfassung zu akzeptieren, "
    "oder antworte mit einer Korrektur.",
}

#: Deterministic language-request detection: a lowercase substring of the
#: adjust message maps the thread to that output language (SFP-258). The
#: LLM honors the request inside the summary (prompt instruction); this map
#: deterministically adapts the HINT line. First match in insertion order
#: wins — Catalan before Spanish (``català``/``catalan`` do not collide),
#: English last so an explicit "in English" resets a prior request.
LANGUAGE_REQUESTS: Final[tuple[tuple[str, str], ...]] = (
    ("català", "ca"),
    ("catalan", "ca"),
    ("español", "es"),
    ("castellano", "es"),
    ("spanish", "es"),
    ("français", "fr"),
    ("francais", "fr"),
    ("french", "fr"),
    ("deutsch", "de"),
    ("german", "de"),
    ("inglés", "en"),
    ("ingles", "en"),
    ("english", "en"),
)


def detect_language_request(text: str) -> str | None:
    """The language explicitly requested in ``text``, or ``None``.

    Pure and deterministic (MAS §12.7): a case-folded substring scan over
    :data:`LANGUAGE_REQUESTS` — first match wins, no model call.
    """
    folded = text.casefold()
    for needle, language in LANGUAGE_REQUESTS:
        if needle in folded:
            return language
    return None


def hint_for(language: str | None) -> str:
    """The confirm hint line for ``language`` (unknown → English)."""
    return CONFIRM_HINTS.get(language or DEFAULT_LANGUAGE, CONFIRM_HINTS[DEFAULT_LANGUAGE])


def _block_actions_facts(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The carrier facts a block_actions payload carries (defensive parse).

    Returns ``None`` when the body is not a well-formed interactive
    payload. Slack's interactive schema: ``channel.id``, ``message.ts``,
    ``message.thread_ts``, ``actions[]`` with ``action_id`` — read
    defensively (the dev runner must never 500 on a provider body) and
    typed (no provider knowledge leaks past this function).
    """
    if payload.get("type") != BLOCK_ACTIONS_TYPE:
        return None
    raw_channel = payload.get("channel")
    channel: dict[str, Any] = raw_channel if isinstance(raw_channel, dict) else {}
    raw_message = payload.get("message")
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    channel_id = channel.get("id") or payload.get("channel_id")
    message_ts = message.get("ts")
    thread_root = message.get("thread_ts") or message_ts
    actions = payload.get("actions")
    if (
        not isinstance(channel_id, str)
        or not isinstance(message_ts, str)
        or not isinstance(thread_root, str)
        or not isinstance(actions, list)
    ):
        return None
    return {
        "channel_id": channel_id,
        "message_ts": message_ts,
        "thread_root": thread_root,
        "action_ids": [
            action.get("action_id")
            for action in actions
            if isinstance(action, dict) and isinstance(action.get("action_id"), str)
        ],
    }


#: The fail-closed error for an HTTP 200 whose text blocks hold no JSON
#: object — the sentinel the dev runtime's single retry keys on.
_NO_JSON_OBJECT_ERROR: Final[str] = "glm returned no JSON object"

#: The stricter re-prompt appended for the single retry after an unparseable
#: output (live smoke 2026-09-14: GLM intermittently answers 200 with prose
#: for certain message contents).
_STRICTER_REPROMPT: Final[str] = (
    "Your previous reply contained no parseable JSON object. "
    "Reply again with ONLY the JSON object — no prose before or after it and "
    "no code fences: the reply must start with '{' and end with '}'."
)


class DevCompositionError(RuntimeError):
    """The dev composition cannot be bound — fail-loud startup (SFP-257).

    Raised (never swallowed) for any configuration problem that would leave
    the runner serving deliveries it can only 500 on: a missing Slack
    signing secret / bot token, a missing or malformed GLM setting, or an
    unreachable GLM endpoint. ``main()`` turns it into a stderr message plus
    a non-zero exit, BEFORE uvicorn serves.
    """


# --------------------------------------------------------------------------- #
# Dev GLM settings + runtime (the AgentRuntime seam's dev adapter)
# --------------------------------------------------------------------------- #


class DevAgentSettings(Settings):
    """The SFP_-prefixed GLM endpoint/model/secret settings (dev composition).

    Reuses the repo's base :class:`~sfp_config.Settings` env prefix (``SFP_``)
    and the same three knobs the workspace worker carries:
    ``SFP_ANTHROPIC_BASE_URL`` (the Anthropic-compatible GLM endpoint,
    ID-019), ``SFP_DEFAULT_MODEL`` and ``SFP_LLM_PROVIDER_SECRET_REF`` (a JSON
    ``{"name": "..."}`` :class:`SecretRef` — never a raw credential, ID-016).
    A missing/blank endpoint or model fails construction (pydantic) — the
    composition root turns that into a fail-loud
    :class:`DevCompositionError`.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="SFP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_base_url: str
    default_model: str
    llm_provider_secret_ref: SecretRef

    @field_validator("anthropic_base_url", "default_model")
    @classmethod
    def _check_non_empty(cls, value: str) -> str:
        """Reject missing / whitespace-only endpoint and model (ID-020 mirror)."""
        if not value.strip():
            raise ValueError("must be a non-empty, non-whitespace string")
        return value


class GlmAgentRuntime:
    """A minimal Anthropic-compatible (GLM) ``AgentRuntime`` for dev only.

    One synchronous ``POST {base_url}/v1/messages`` per attempt (ID-051
    httpx; the token is resolved from the injected
    :class:`~sfp_config.SecretProvider` at call time and never logged —
    ID-016). The response's text blocks are joined and parsed as a JSON
    object — the structured output
    :class:`~communication.application.communication_agent.CommunicationAgent`
    validates. An HTTP 200 whose body holds no parseable JSON object is
    retried EXACTLY ONCE with a stricter re-prompt (the pipeline
    ``ClaudeAgentRuntime``'s retry-on-empty-stream spirit, dev-sized —
    live smoke 2026-09-14: GLM intermittently answers 200 with prose, which
    otherwise 500s the webhook into Slack's retry storm). Any transport
    failure, non-2xx response, or output still unparseable after the retry
    yields ``AgentRunResult(success=False, error=...)`` — fail-closed, never
    a raise (the agent already maps failures to :class:`~communication.\
application.communication_agent.SummarizationError`).

    Tests inject an ``httpx.Client`` built on ``httpx.MockTransport`` — no
    live endpoint is ever contacted by the test suite (MAS §12.7).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        secret_provider: SecretProvider,
        secret_ref: SecretRef,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._secret_provider = secret_provider
        self._secret_ref = secret_ref
        self._client = client
        self._timeout = timeout

    def probe(self) -> None:
        """Verify the GLM endpoint is reachable at startup (fail-loud).

        One minimal request; a transport failure or an HTTP 5xx raises
        :class:`DevCompositionError` so the runner exits instead of serving.
        Any completed HTTP response below 500 counts as reachable (the
        endpoint's 4xx semantics are the provider's business).
        """
        try:
            response = self._post([{"role": "user", "content": "ping"}], max_tokens=1)
        except httpx.HTTPError as exc:
            raise DevCompositionError(
                f"GLM endpoint unreachable ({type(exc).__name__}) at {self._base_url}"
            ) from exc
        if response.status_code >= 500:
            raise DevCompositionError(
                f"GLM endpoint unhealthy (HTTP {response.status_code}) at {self._base_url}"
            )

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run one structured-output request (the ``AgentRuntime`` Protocol).

        An HTTP 200 whose body holds no parseable JSON object is retried
        exactly once with the stricter re-prompt; only that failure mode
        retries (transport / HTTP errors fail immediately — the strict
        re-prompt cannot fix a dead endpoint).
        """
        prompt = self._build_prompt(request)
        first = self._run_once(request, prompt)
        if first.success or first.error != _NO_JSON_OBJECT_ERROR:
            return first
        retry = self._run_once(request, f"{prompt}\n\n{_STRICTER_REPROMPT}")
        if retry.success:
            return retry
        return AgentRunResult(
            agent=request.agent,
            ticket_id=request.ticket_id,
            success=False,
            error=f"{_NO_JSON_OBJECT_ERROR} (after 1 retry)",
        )

    def _build_prompt(self, request: AgentRunRequest) -> str:
        """Assemble the structured-output prompt from the run request."""
        prompt = (
            f"{request.prompt}\n\n"
            "Respond with ONLY a JSON object.\n\nCONTEXT:\n"
            f"{request.ticket_id}\n"
        )
        for key in sorted(request.context):
            prompt += f"{key}: {request.context[key]!s}\n"
        return prompt

    def _run_once(self, request: AgentRunRequest, prompt: str) -> AgentRunResult:
        """One POST + parse attempt; fail-closed mapping, no retries here."""
        try:
            response = self._post([{"role": "user", "content": prompt}])
        except httpx.HTTPError as exc:
            return AgentRunResult(
                agent=request.agent,
                ticket_id=request.ticket_id,
                success=False,
                error=f"glm transport failure ({type(exc).__name__})",
            )
        if response.status_code >= 400:
            return AgentRunResult(
                agent=request.agent,
                ticket_id=request.ticket_id,
                success=False,
                error=f"glm HTTP {response.status_code}",
            )
        output = self._parse_output(response)
        if output is None:
            return AgentRunResult(
                agent=request.agent,
                ticket_id=request.ticket_id,
                success=False,
                error=_NO_JSON_OBJECT_ERROR,
            )
        return AgentRunResult(
            agent=request.agent,
            ticket_id=request.ticket_id,
            success=True,
            output=output,
        )

    def _post(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,
    ) -> httpx.Response:
        """POST one ``/v1/messages`` request and return the response."""
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        headers = {"Authorization": f"Bearer {self._secret_provider.resolve(self._secret_ref)}"}
        if self._client is not None:
            return self._client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
        with httpx.Client(timeout=self._timeout) as client:
            return client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=headers,
            )

    def _parse_output(self, response: httpx.Response) -> dict[str, Any] | None:
        """Parse the response's text blocks into a JSON object (or ``None``)."""
        body = response.json()
        blocks = body.get("content")
        if not isinstance(blocks, list):
            return None
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            output = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return output if isinstance(output, dict) else None


# --------------------------------------------------------------------------- #
# Ack-then-process ingress publisher (SFP-257 round 3)
# --------------------------------------------------------------------------- #


class AckThenProcessPublisher:
    """**Dev-only** ack-then-process ingress publisher (SFP-257 round 3).

    The webhook's 200 must NEVER wait on the consume chain: Slack's Events
    API aborts a delivery after ~3 seconds and RETRIES it, and a retry is a
    full redelivery of the original event — a legitimate fresh user message
    (``subtype`` absent, human ``user``, no ``bot_id``) that passes every
    interpretation filter. While the endpoint processed synchronously
    (signature → publish → GLM summarize ~5-6s → reply → 200), every retry
    re-entered summarize: one human message kept re-summarizing and
    replying, with accumulated retries arriving in bursts (live smoke
    round 3, 2026-09-14: FIVE identical deliveries within 3ms; rounds 1-2's
    echo storms were the same timeout loop seen through bot-shaped events).

    This wrapper (duck-compatible with the SFP-124
    :class:`~external_events.application.publisher.ExternalEventPublisher`
    the dev webhook wires by default) moves only the publish/dispatch off
    the request path:

    1. build the SFP-124 envelope deterministically (pure, no I/O — the
       reference :func:`~external_events.application.publisher.\
make_external_event_envelope`);
    2. dedupe redeliveries (below);
    3. ``asyncio.create_task`` the bus dispatch and return the envelope —
       the endpoint answers 200 immediately.

    Fail-loud is unchanged elsewhere: startup binding stays fail-loud, and
    signature verification still rejects synchronously with 401 BEFORE this
    publisher is ever reached. A background dispatch failure is LOGGED,
    never silently dropped and never re-raised (the ack already went out;
    the known summarize failure mode already degrades gracefully in-band
    with :data:`SUMMARIZATION_APOLOGY`).

    Dedup (dev-only in-memory seen-set — production dedupe belongs to the
    Phase-B durable transport, SFP-118/101; the in-memory transport
    deliberately performs none, SFP-46): the SFP-124
    ``idempotency_key`` (``{source}:{sha256(body)}``) collides for
    byte-identical redeliveries by construction, and Slack's top-level
    ``event_id`` is included as a second key so a retry whose body drifted
    still dedupes. The set grows with distinct deliveries for the process
    lifetime — acceptable for a dev runner.
    """

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._seen: set[str] = set()
        #: Background dispatch tasks not yet finished (dev observability;
        #: tests await them so assertions are deterministic).
        self.in_flight: list[asyncio.Task[None]] = []

    async def publish(
        self, source: str, external_id: str, payload: dict[str, Any]
    ) -> EventEnvelope:
        """Envelope now, dispatch later — the ack-then-process seam."""
        envelope = make_external_event_envelope(
            ExternalEventReceived(source=source, external_id=external_id, payload=payload)
        )
        keys = {envelope.idempotency_key, *self._provider_event_keys(source, payload)}
        if keys & self._seen:
            logger.info(
                "dev runner: duplicate delivery dropped (source=%s, external_id=%s)",
                source,
                external_id,
            )
            return envelope
        self._seen |= keys
        task: asyncio.Task[None] = asyncio.create_task(self._dispatch(envelope))
        self.in_flight.append(task)
        task.add_done_callback(self.in_flight.remove)
        return envelope

    async def _dispatch(self, envelope: EventEnvelope) -> None:
        """Publish one envelope on the bus in the background."""
        try:
            await self._bus.publish(envelope)
        except Exception as exc:  # noqa: BLE001 - acked already: log, never raise
            logger.error(
                "dev runner: background dispatch failed (%s: %s) — delivery was acked",
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def _provider_event_keys(source: str, payload: dict[str, Any]) -> set[str]:
        """Extra dedup keys from the provider body (dev-only; Slack today).

        Reads ONE top-level routing field (``event_id``) — a dev-composition
        allowance for redelivery bodies that are not byte-identical, never
        a license for ingress interpretation (MAS §9.2).
        """
        if source != "slack":
            return set()
        event_id = payload.get("event_id")
        if isinstance(event_id, str) and event_id:
            return {f"slack-event:{event_id}"}
        return set()


# --------------------------------------------------------------------------- #
# Dev-only destination resolver (the SFP-128 stand-in)
# --------------------------------------------------------------------------- #


class DevSlackDestinationResolver:
    """**Dev-only** session_id → channel/thread mapping (NOT the SFP-128 query).

    The simplest correct v0 stand-in until SFP-128 lands the real identity
    query: the router remembers, per thread reference, the channel the inbound
    message actually arrived on, and :meth:`resolve` maps the session id (v0:
    session_id IS the provider thread reference) to that channel + thread.
    A session that was never seen raises :class:`LookupError`.
    """

    def __init__(self) -> None:
        self._channels: dict[str, str] = {}

    def remember(self, session_id: str, channel: str) -> None:
        """Record the channel a thread reference was seen on (dev-only)."""
        self._channels[session_id] = channel

    def resolve(self, session_id: str) -> SlackDestination:
        """Map the session id to its remembered Slack destination."""
        channel = self._channels.get(session_id)
        if channel is None:
            raise LookupError(f"no dev Slack destination known for session_id={session_id!r}")
        return SlackDestination(channel_ref=channel, thread_ref=session_id)


# --------------------------------------------------------------------------- #
# The inbound router: landed consumer + outcome routing (SFP-257)
# --------------------------------------------------------------------------- #


class DevCommunicationRouter(SlackInboundConsumer):
    """The landed SFP-132 consumer plus its user-visible outcome routing.

    Subclassing (not wrapping) keeps the SFP-132 contract intact:
    ``set_slack_inbound_consumer`` still receives a
    :class:`~communication.interfaces.slack_inbound.SlackInboundConsumer`, and
    ``consume`` runs the landed MAS §9.4 inbound sequence verbatim —
    interaction find-or-create, the ``last_message_*`` update, and the ID-076
    ``UserInputReceived`` publication. On top of that, the reply is routed
    through the SFP-135
    :class:`~communication.application.confirm_flow.ConfirmFlow` and each
    outcome maps to its user-visible effect:

    - :class:`~communication.application.confirm_flow.CorrectionOutcome` →
      the regenerated summary is delivered back to the thread with a
      ``CONFIRM`` re-request (persisted via the agent's ``update_summary``
      delegation);
    - :class:`~communication.application.confirm_flow.ConfirmOutcome` → the
      ``UserInputReceived`` carrying the confirmed summary was published on
      the bus and the interaction completed — the dev runner LOGS it as the
      observable while the Orchestrator does not yet consume it;
    - a closed interaction (``InteractionTransitionError`` from the landed
      ``create()``, AP-005) → the
      :class:`~communication.application.communication_agent.ClosedInteractionOutcome`
      delivers the "please start a new thread" message, and the thread's
      prior summary is remembered as the contextual context reference for the
      user's follow-up interaction (the SFP-131 remainder).

    A message on a brand-new thread from a user with remembered context seeds
    the new interaction's summary with that prior summary as a contextual
    reference. All of this is composition routing only — no new domain
    behavior, no provider knowledge, no identity logic.
    """

    def __init__(
        self,
        *,
        bus: Any,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        confirm_flow: ConfirmFlow,
        notifications: NotificationService,
        resolver: DevSlackDestinationResolver,
        clock: Clock | None = None,
        outbound: Any | None = None,
    ) -> None:
        super().__init__(
            bus=bus,
            interaction_service=interaction_service,
            session_factory=session_factory,
            clock=clock,
        )
        self._confirm_flow = confirm_flow
        self._notifications = notifications
        self._resolver = resolver
        #: The delivery port, for the SFP-258 Block Kit sends and the
        #: ``chat.update`` button teardown. ``None`` falls back to the
        #: notifications service with plain text (a port without the Block
        #: Kit surface — e.g. the pre-SFP-258 fakes — degrades gracefully).
        self._outbound = outbound
        #: The InteractionService the context carry-over write goes through.
        self._interactions = interaction_service
        self._known_references: set[str] = set()
        self._pending_context: dict[str, str] = {}
        #: Per-thread requested output language (SFP-258); absent = English.
        self._thread_language: dict[str, str] = {}
        #: The ts of the message that last carried a thread's summary + the
        #: decision button — the teardown target for a TYPED confirmation
        #: (a button click carries its own message ts in the payload).
        self._summary_message_ts: dict[str, str] = {}

    async def consume(self, event: ExternalEventReceived) -> None:
        """Run the landed inbound sequence, then route the outcome.

        A ``block_actions`` payload (Slack interactive — SFP-258) is routed
        as a STRUCTURED DECISION before the message interpretation: it is
        not a message event, the whitelist/dedup/ack-then-process invariants
        (SFP-257) are untouched, and the decision reaches the same ID-069
        gate a typed confirmation does.
        """
        facts = _block_actions_facts(event.payload)
        if facts is not None:
            await self._handle_block_actions(facts)
            return

        message = parse_slack_message(event.payload)
        if message is None:
            await super().consume(event)
            return

        reference = message.provider_reference
        is_new = reference not in self._known_references
        self._known_references.add(reference)
        self._resolver.remember(reference, message.channel)
        requested = detect_language_request(message.text)
        if requested is not None:
            self._thread_language[reference] = requested
        try:
            await super().consume(event)
        except InteractionTransitionError:
            await self._handle_closed(message)
            return

        if is_new:
            await self._carry_over_context(message)
        try:
            await self._route_reply(message)
        except SummarizationError as exc:
            # Graceful degradation (SFP-257 live smoke, 2026-09-14): a
            # summary that still fails after the runtime's retry must NOT
            # 500 the webhook — Slack would hammer retries (measured: five
            # consecutive 500s). Apologize in-thread and complete the
            # delivery with a 200. Fail-loud stays STARTUP-only.
            logger.warning(
                "dev runner: summarization failed for %s (%s) — apologizing in-thread",
                reference,
                exc,
            )
            await self._notify(reference, SUMMARIZATION_APOLOGY)

    async def _handle_closed(self, message: SlackProviderMessage) -> None:
        """A reply to a closed interaction: request a new thread (AP-005)."""
        outcome = await self._confirm_flow.handle(message)
        if not isinstance(outcome, ClosedInteractionOutcome):  # pragma: no cover —
            # unreachable by construction: InteractionTransitionError fires only
            # for a derived non-ACTIVE status, and the flow re-derives the same
            # status, so the closed branch always returns the closed outcome.
            logger.warning("closed-thread flow returned unexpected outcome %r", outcome)
            return
        self._pending_context[message.user] = outcome.prior_summary
        await self._notify(
            message.provider_reference,
            f"{CLOSED_THREAD_MESSAGE} (closing summary: {outcome.prior_summary})",
        )

    async def _carry_over_context(self, message: SlackProviderMessage) -> None:
        """Seed a brand-new interaction with the user's prior summary (SFP-131)."""
        prior = self._pending_context.pop(message.user, None)
        if prior is None:
            return
        await self._interactions.update_summary(
            message.provider_reference,
            f"[Context carried over from the user's closed thread: {prior}]\n{message.text}",
        )

    async def _route_reply(self, message: SlackProviderMessage) -> None:
        """Route one inbound reply through the ID-069 gate to its effect."""
        result = await self._confirm_flow.handle(message)
        if isinstance(result, CorrectionOutcome):
            await self._deliver_summary(message.provider_reference, result.regenerated_summary)
        elif isinstance(result, ConfirmOutcome):
            await self._complete_confirmation(message.provider_reference, result)
        else:
            await self._handle_closed(message)

    async def _handle_block_actions(self, facts: dict[str, Any]) -> None:
        """Route a verified block_actions payload as a structured decision.

        Only the ✅ Confirm decision acts (:data:`ACCEPT_ACTION_ID`); the
        click is EXACTLY equivalent to typed confirmation — same
        ``UserInputReceived`` publication shape, same completion — via the
        SFP-135 flow's :meth:`ConfirmFlow.confirm_interaction`. The actions
        block is then torn down to its terminal state so the completed
        thread cannot be re-confirmed; a late click on a completed
        interaction takes the closed-interaction path (zero mutations, zero
        publishes) and still renders the terminal state.
        """
        channel = facts["channel_id"]
        thread_root = facts["thread_root"]
        self._resolver.remember(thread_root, channel)
        if ACCEPT_ACTION_ID not in facts["action_ids"]:
            logger.info(
                "dev runner: block_actions on %s carried no %s decision (ignored)",
                thread_root,
                ACCEPT_ACTION_ID,
            )
            return
        try:
            outcome = await self._confirm_flow.confirm_interaction(thread_root)
        except LookupError:
            logger.warning(
                "dev runner: block_actions click on unknown thread %s — ignored", thread_root
            )
            return
        if isinstance(outcome, ConfirmOutcome):
            logger.info(
                "dev runner: decision button click confirmed interaction %s (UserInputReceived "
                "published with the confirmed summary)",
                outcome.interaction_id,
            )
            self._teardown_actions_block(
                channel=channel,
                message_ts=facts["message_ts"],
                thread_root=thread_root,
                summary_text=outcome.confirmed_summary,
            )
            return
        logger.info(
            "dev runner: late block_actions click on closed interaction %s — "
            "closed-interaction handling, no re-confirmation",
            outcome.interaction_id,
        )
        self._teardown_actions_block(
            channel=channel,
            message_ts=facts["message_ts"],
            thread_root=thread_root,
            summary_text=outcome.prior_summary,
        )

    async def _complete_confirmation(self, reference: str, outcome: ConfirmOutcome) -> None:
        """The typed-confirmation effect: log + tear the button down."""
        logger.info(
            "dev runner: UserInputReceived published (session_id=%s, text=%r); "
            "interaction %s completed",
            reference,
            outcome.confirmed_summary,
            outcome.interaction_id,
        )
        teardown_ts = self._summary_message_ts.get(reference)
        if teardown_ts is None:
            logger.warning(
                "dev runner: no known summary message ts for %s — button teardown skipped",
                reference,
            )
            return
        destination = self._resolver.resolve(reference)
        self._teardown_actions_block(
            channel=destination.channel_ref,
            message_ts=teardown_ts,
            thread_root=reference,
            summary_text=outcome.confirmed_summary,
        )

    def _teardown_actions_block(
        self,
        *,
        channel: str,
        message_ts: str,
        thread_root: str,
        summary_text: str,
    ) -> None:
        """Replace the decision actions block with its terminal state.

        Uses ``chat.update`` on the message that carried the button (the
        button click's payload carries the message ts; a typed confirmation
        uses the recorded summary-delivery ts). A transport failure is
        LOGGED, never raised — the confirmation itself already succeeded and
        the delivery was acked (the ack-then-process discipline).
        """
        update = getattr(self._outbound, "update_message", None)
        if update is None:
            logger.info(
                "dev runner: outbound port has no update_message — teardown skipped for %s",
                thread_root,
            )
            return
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": summary_text}},
            CONFIRMED_ACTIONS_BLOCK,
        ]
        try:
            receipt = update(
                channel_ref=channel,
                ts=message_ts,
                text=summary_text,
                blocks=blocks,
                thread_ref=thread_root,
            )
        except ProviderError as exc:
            logger.warning(
                "dev runner: button teardown for %s failed (%s) — the confirmation stands",
                thread_root,
                exc,
            )
            return
        logger.info("dev runner: decision buttons replaced with terminal state → %r", receipt)

    async def _deliver_summary(self, reference: str, summary_text: str) -> None:
        """Deliver a regenerated summary as Block Kit + the decision button.

        The layout is the summary text section (with the language-adaptive
        confirm hint line) plus the owner's EXACT actions block. A port
        without the Block Kit surface falls back to the plain-text
        notifications path — the pre-SFP-258 behavior.
        """
        hint = hint_for(self._thread_language.get(reference))
        blocks = summary_blocks(summary_text, hint)
        send = getattr(self._outbound, "send_message", None) if self._outbound else None
        if send is None:
            await self._notify(reference, f"{summary_text}{hint}")
            return
        destination = self._resolver.resolve(reference)
        try:
            receipt = send(
                f"{summary_text}{hint}",
                channel_ref=destination.channel_ref,
                thread_ref=destination.thread_ref,
                blocks=blocks,
            )
        except TypeError:
            # Port predates the blocks kwarg — plain-text fallback.
            await self._notify(reference, f"{summary_text}{hint}")
            return
        if receipt.provider_message_id:
            # The teardown target for a later TYPED confirmation.
            self._summary_message_ts[reference] = receipt.provider_message_id
        logger.info("dev runner: Block Kit summary delivered to %s → %r", reference, receipt)

    async def _notify(self, session_id: str, text: str) -> NotificationOutcome:
        """Deliver ``text`` to the thread via the SFP-136 handler (SFP-244 port)."""
        outcome = await self._notifications.handle_notify_user(
            NotifyUser(session_id=session_id, message=text)
        )
        logger.info("dev runner: outbound to %s → %r", session_id, outcome)
        return outcome


# --------------------------------------------------------------------------- #
# The composition root (dev)
# --------------------------------------------------------------------------- #


def _dev_communication_session_factory() -> SessionFactory:
    """Build a committing session factory over a ``business``-schema dev DB.

    The SFP-129 test recipe (StaticPool in-memory SQLite + ``ATTACH … AS
    business``), the same idiom the runner uses for ``operational`` — no
    second persistence stack. The engine is disposed at process exit via
    :mod:`atexit`.
    """
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @sa.event.listens_for(engine, "connect")
    def _attach_business(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS business")  # type: ignore[attr-defined]

    CommunicationBase.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def factory() -> Iterator[Session]:
        with session_scope(maker) as session:
            yield session

    atexit.register(engine.dispose)
    return factory


@dataclass(frozen=True)
class DevComposition:
    """The wired dev graph (observability surface for tests and the runner)."""

    router: DevCommunicationRouter
    agent: CommunicationAgent
    confirm_flow: ConfirmFlow
    notifications: NotificationService
    resolver: DevSlackDestinationResolver
    interactions: InteractionService
    runtime: AgentRuntime


def _resolve_required_secret(secret_provider: SecretProvider, name: str) -> None:
    """Fail-loud: ``name`` must resolve to a non-empty value (ID-016)."""
    try:
        value = secret_provider.resolve(SecretRef(name=name))
    except SecretResolutionError as exc:
        raise DevCompositionError(
            f"missing required Slack secret {name!r} — set it in the environment or secrets.local"
        ) from exc
    if not value.strip():
        raise DevCompositionError(f"Slack secret {name!r} resolved to an empty value")


def build_dev_composition(
    *,
    bus: Any,
    secret_provider: SecretProvider | None = None,
    session_factory: SessionFactory | None = None,
    runtime: AgentRuntime | None = None,
    outbound: OutboundMessagePort | None = None,
    clock: Clock | None = None,
    probe_runtime: bool = False,
) -> DevComposition:
    """Wire the Communication graph over the runner's bus (fail-loud).

    Args:
        bus: The runner's :class:`~sfp_messaging.transport.in_memory.InMemoryTransport`
            — events published here reach the bound consumer (registry
            dispatch) and record every publication for observability.
        secret_provider: Resolves ``SLACK_SIGNING_SECRET`` (ingress auth) and
            ``SLACK_BOT_TOKEN`` (outbound); a missing one is fail-loud.
        session_factory: The committing unit-of-work factory; defaults to the
            dev in-memory ``business`` schema one.
        runtime: The ``AgentRuntime`` seam; defaults to :class:`GlmAgentRuntime`
            over :class:`DevAgentSettings`.
        outbound: The delivery port; defaults to the landed
            :class:`~communication.interfaces.slack_outbound.SlackOutboundClient`.
        clock: The single time seam (MAS §12.7); defaults to now(UTC).
        probe_runtime: When true, verify the GLM endpoint is reachable at
            startup (``main()`` passes true; tests pass false).

    Returns:
        The wired :class:`DevComposition` — NOT yet bound; the caller must
        ``set_slack_inbound_consumer(composition.router)`` before serving.

    Raises:
        DevCompositionError: Any fail-loud configuration problem (missing
            Slack secret, missing/malformed GLM setting, unreachable GLM
            endpoint when ``probe_runtime``).
    """
    provider = secret_provider if secret_provider is not None else LocalSecretProvider()
    _resolve_required_secret(provider, "SLACK_SIGNING_SECRET")
    _resolve_required_secret(provider, "SLACK_BOT_TOKEN")

    try:
        settings = DevAgentSettings()  # type: ignore[call-arg]  # env/`.env`-sourced
    except ValidationError as exc:
        raise DevCompositionError(
            "missing/invalid GLM agent settings (SFP_ANTHROPIC_BASE_URL, SFP_DEFAULT_MODEL, "
            f"SFP_LLM_PROVIDER_SECRET_REF): {exc.error_count()} error(s)"
        ) from exc

    if runtime is None:
        runtime = GlmAgentRuntime(
            base_url=settings.anthropic_base_url,
            model=settings.default_model,
            secret_provider=provider,
            secret_ref=settings.llm_provider_secret_ref,
        )
    if probe_runtime and isinstance(runtime, GlmAgentRuntime):
        runtime.probe()

    factory = (
        session_factory if session_factory is not None else _dev_communication_session_factory()
    )
    wall_clock: Clock = clock if clock is not None else (lambda: datetime.now(UTC))
    interactions = InteractionService(bus=bus, session_factory=factory, clock=wall_clock)
    agent = CommunicationAgent(runtime=runtime, interactions=interactions)
    confirm_flow = ConfirmFlow(
        bus=bus,
        interactions=interactions,
        agent=agent,
        session_factory=factory,
        clock=wall_clock,
    )
    resolver = DevSlackDestinationResolver()
    delivery_port = outbound if outbound is not None else SlackOutboundClient(provider)
    notifications = NotificationService(
        outbound=delivery_port,
        interactions=interactions,
        resolver=resolver,
    )
    router = DevCommunicationRouter(
        bus=bus,
        interaction_service=interactions,
        session_factory=factory,
        confirm_flow=confirm_flow,
        notifications=notifications,
        resolver=resolver,
        clock=clock,
        outbound=delivery_port,
    )
    return DevComposition(
        router=router,
        agent=agent,
        confirm_flow=confirm_flow,
        notifications=notifications,
        resolver=resolver,
        interactions=interactions,
        runtime=runtime,
    )


def main(argv: list[str] | None = None) -> None:
    """Bind the composition, THEN serve the webhook (fail-loud startup).

    Any :class:`DevCompositionError` prints to stderr and exits non-zero —
    a runner that cannot be bound must never serve traffic that would 500
    on every delivery (SFP-132 / SFP-257).
    """
    import argparse

    from external_events.entrypoints.dev_webhook import (
        DEFAULT_PORT,
        _resolve_endpoint_id,
        _resolve_port,
        build_dev_app,
    )

    parser = argparse.ArgumentParser(
        prog="dev_composition",
        description=(
            "Serve the external-events webhook with the Communication dev "
            "composition bound (SFP-257) for the closed Slack dogfood loop."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (default {DEFAULT_PORT}, env EXTERNAL_WEBHOOK_PORT).",
    )
    parser.add_argument(
        "--endpoint-id",
        type=str,
        default=None,
        help="The seeded endpoint id served at /webhooks/{endpoint_id}.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    endpoint_id = _resolve_endpoint_id(args.endpoint_id)
    port = _resolve_port(args.port)
    app, bus, _resolver = build_dev_app(
        endpoint_id,
        # Ack-then-process (SFP-257 round 3): the 200 must never wait on the
        # consume chain — Slack aborts a delivery after ~3s and retries it,
        # and each retry is a legitimate fresh user message that would
        # re-enter summarize. The GLM round-trip runs in the background;
        # redeliveries dedupe on the SFP-124 idempotency key.
        ingress_publisher_factory=AckThenProcessPublisher,
    )

    try:
        composition = build_dev_composition(bus=bus, probe_runtime=True)
    except DevCompositionError as exc:
        print(f"dev runner startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # Bind BEFORE serving — importing slack_inbound without a bound consumer
    # turns every delivery into a synchronous 500 (SFP-132; measured
    # 2026-09-13).
    set_slack_inbound_consumer(composition.router)
    logger.info("SFP-257 dev composition bound BEFORE serving (SFP-132)")

    import uvicorn

    logger.info(
        "SFP-257 dev external-events webhook: POST http://127.0.0.1:%d/webhooks/%s",
        port,
        endpoint_id,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
