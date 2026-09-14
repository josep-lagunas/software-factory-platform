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
from communication.interfaces.outbound import OutboundMessagePort
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
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

__all__ = [
    "CONFIRM_REQUEST_SUFFIX",
    "CLOSED_THREAD_MESSAGE",
    "DevAgentSettings",
    "DevCommunicationRouter",
    "DevComposition",
    "DevCompositionError",
    "DevSlackDestinationResolver",
    "GlmAgentRuntime",
    "build_dev_composition",
    "main",
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

    One synchronous ``POST {base_url}/v1/messages`` per run (ID-051 httpx;
    the token is resolved from the injected :class:`~sfp_config.SecretProvider`
    at call time and never logged — ID-016). The response's text blocks are
    joined and parsed as a JSON object — the structured output
    :class:`~communication.application.communication_agent.CommunicationAgent`
    validates. Any transport failure, non-2xx response or unparseable body
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
        """Run one structured-output request (the ``AgentRuntime`` Protocol)."""
        prompt = (
            f"{request.prompt}\n\n"
            "Respond with ONLY a JSON object.\n\nCONTEXT:\n"
            f"{request.ticket_id}\n"
        )
        for key in sorted(request.context):
            prompt += f"{key}: {request.context[key]!s}\n"
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
                error="glm returned no JSON object",
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
        #: The InteractionService the context carry-over write goes through.
        self._interactions = interaction_service
        self._known_references: set[str] = set()
        self._pending_context: dict[str, str] = {}

    async def consume(self, event: ExternalEventReceived) -> None:
        """Run the landed inbound sequence, then route the outcome."""
        message = parse_slack_message(event.payload)
        if message is None:
            await super().consume(event)
            return

        reference = message.provider_reference
        is_new = reference not in self._known_references
        self._known_references.add(reference)
        self._resolver.remember(reference, message.channel)
        try:
            await super().consume(event)
        except InteractionTransitionError:
            await self._handle_closed(message)
            return

        if is_new:
            await self._carry_over_context(message)
        await self._route_reply(message)

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
            await self._notify(
                message.provider_reference,
                f"{result.regenerated_summary}{CONFIRM_REQUEST_SUFFIX}",
            )
        elif isinstance(result, ConfirmOutcome):
            logger.info(
                "dev runner: UserInputReceived published (session_id=%s, text=%r); "
                "interaction %s completed",
                message.provider_reference,
                result.confirmed_summary,
                result.interaction_id,
            )
        else:
            await self._handle_closed(message)

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
    notifications = NotificationService(
        outbound=outbound if outbound is not None else SlackOutboundClient(provider),
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
    app, bus, _resolver = build_dev_app(endpoint_id)

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
