"""Composition tests for the Phase-A dev composition (SFP-257).

Covers the closed Slack dogfood loop wired in
:mod:`external_events.entrypoints.dev_composition`:

- **bind-before-serve** — ``main()`` binds the Slack inbound consumer strictly
  before uvicorn serves, and any bind failure exits NON-ZERO without serving;
- **end-to-end synthetic message** — a synthetic Slack delivery (the verbatim
  Events API payload; signature verification is ingress's, already covered by
  the SFP-120/123 tests) creates the interaction, persists the summary via
  ``update_summary``, and produces an observable outbound effect through a
  fake outbound port;
- **closed-interaction delivery** — a reply to a closed thread delivers the
  "please start a new thread" message and the follow-up interaction carries
  the prior summary as contextual context reference (SFP-131 remainder);
- **CONFIRM** — the literal publishes ``UserInputReceived`` and completes the
  interaction; a correction regenerates (SFP-135 semantics exercised through
  the composition);
- **self-echo guard** (SFP-257 live-smoke rounds 1+2) — the bot's own
  outbound post echoing back as a bot-authored ``message`` event, and the
  ``message_replied`` parent sub-events every bot reply emits, produce ZERO
  outbound effects (the infinite reply-loop reproductions);
- **graceful degradation** (SFP-257 live-smoke finding) — a summary that
  fails even after the dev runtime's single stricter retry gets a short
  in-thread apology and completes the delivery (never a webhook 500);
- **ack-then-process** (SFP-257 live-smoke round 3) — through the real ASGI
  app with a Slack-signed delivery: the webhook returns 200 BEFORE a
  deliberately slow consumer completes, and five byte-identical
  redeliveries dedupe to exactly ONE summarize (Slack's ~3s delivery
  timeout can no longer produce a retry storm);
- the dev GLM ``AgentRuntime`` adapter and the dev-only destination resolver,
  over ``httpx.MockTransport`` / fakes — no live Slack or GLM anywhere.

Deterministic throughout (MAS §12.7): injected clock, no network. ONE
deliberate exception — the ack-then-process runtime stand-in blocks for a
scaled 0.25s ``time.sleep`` because the blocking GLM round-trip is exactly
what the 200 must not wait on (round 3's live finding).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from communication.application.interaction_service import (
    InteractionService,
    InteractionStatus,
    session_scope,
)
from communication.infrastructure.persistence import Base, UserInteraction
from communication.interfaces.outbound import DeliveryReceipt
from communication.interfaces.slack_inbound import (
    handle_external_event_received,
    set_slack_inbound_consumer,
)
from external_events.entrypoints import dev_composition
from external_events.entrypoints.dev_composition import (
    CLOSED_THREAD_MESSAGE,
    CONFIRM_REQUEST_SUFFIX,
    SUMMARIZATION_APOLOGY,
    AckThenProcessPublisher,
    DevCompositionError,
    DevSlackDestinationResolver,
    GlmAgentRuntime,
    build_dev_composition,
)
from external_events.entrypoints.dev_webhook import build_dev_app
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_contracts.events import ExternalEventReceived
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging import get_default_registry
from sfp_messaging.transport.in_memory import InMemoryTransport
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Deterministic fixtures
# --------------------------------------------------------------------------- #

#: Fixed wall clock (MAS §12.7): every injected clock reads this instant.
NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

#: The summary the fake runtime "produces" for every summarize run.
RUNTIME_SUMMARY = "Deploy finished; the user asked to re-run the checks."

#: The Slack channel every synthetic message lands in.
CHANNEL = "C0DEV"

#: The thread root of the synthetic interaction (v0: one thread = one row).
THREAD_ROOT = "1757428800.000100"

#: A second thread root (the follow-up interaction's new thread).
THREAD_ROOT_2 = "1757428900.000200"

#: The Slack user id of the synthetic sender.
USER = "U0DEV"

#: The app's own bot identity — what the bot's outbound posts echo back as
#: (the self-loop guard's input; SFP-257 live smoke).
BOT_ID = "B0DEVAPP"

#: The bot's Slack user id as Events API echoes carry it.
BOT_USER = "U0DEVBOT"


def _slack_event(
    *,
    channel: str = CHANNEL,
    thread_root: str = THREAD_ROOT,
    text: str = "the deploy finished, rerun the checks",
    user: str = USER,
    ts: str = "1757428801.000300",
    subtype: str | None = None,
    bot_id: str | None = None,
) -> ExternalEventReceived:
    """Build one synthetic Slack delivery (the verbatim Events API body)."""
    event: dict[str, Any] = {
        "type": "message",
        "text": text,
        "channel": channel,
        "ts": ts,
        "user": user,
        "thread_ts": thread_root,
    }
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id is not None:
        event["bot_id"] = bot_id
    return ExternalEventReceived(
        source="slack",
        external_id=ts,
        payload={"type": "event_callback", "event": event},
    )


class FakeRuntime:
    """Deterministic ``AgentRuntime`` stand-in: records requests, replays one result."""

    def __init__(self, result: AgentRunResult | None = None) -> None:
        self.requests: list[AgentRunRequest] = []
        self._result = result or AgentRunResult(
            agent="communication",
            ticket_id="x",
            success=True,
            output={"summary": RUNTIME_SUMMARY},
        )

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        return self._result


class FakeOutbound:
    """A fake ``OutboundMessagePort``: records sends, always delivers."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []

    def send_message(
        self,
        text: str,
        *,
        channel_ref: str | None = None,
        thread_ref: str | None = None,
    ) -> DeliveryReceipt:
        self.sends.append({"text": text, "channel_ref": channel_ref, "thread_ref": thread_ref})
        return DeliveryReceipt(
            provider_message_id=f"ts-{len(self.sends)}",
            channel_ref=channel_ref or "",
            thread_ref=thread_ref,
            provider_reference=f"slack://channel/{channel_ref}",
            ok=True,
            error=None,
        )


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    """An in-memory SQLite engine with the ``business`` schema attached."""
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as conn:
        conn.execute(sa.text("ATTACH DATABASE ':memory:' AS business"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: sa.Engine) -> Any:
    """A committing unit-of-work factory over the engine."""
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: session_scope(maker)


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The minimal secret/settings env the composition needs (no live values)."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "dev-signing-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-dev-token")
    monkeypatch.setenv("SFP_ANTHROPIC_BASE_URL", "https://glm.example.test")
    monkeypatch.setenv("SFP_DEFAULT_MODEL", "glm-4-dev")
    monkeypatch.setenv("SFP_LLM_PROVIDER_SECRET_REF", '{"name": "GLM_TOKEN"}')


@pytest.fixture
def registry_binding() -> Iterator[None]:
    """Bind the SFP-132 handler in the (process-global) registry, then unbind.

    The default registry is process-global and other suites clear it; this
    fixture makes the composition tests self-contained regardless of run
    order, and unbinds the consumer afterwards so nothing leaks.
    """
    get_default_registry().register(ExternalEventReceived, handle_external_event_received)
    yield
    set_slack_inbound_consumer(None)


@pytest.fixture
def composition(
    secrets: None,
    session_factory: Any,
    registry_binding: None,
) -> tuple[Any, InMemoryTransport, FakeOutbound, FakeRuntime]:
    """The wired composition over a real InMemoryTransport + fake runtime/port."""
    bus = InMemoryTransport()
    runtime = FakeRuntime()
    outbound = FakeOutbound()
    comp = build_dev_composition(
        bus=bus,
        session_factory=session_factory,
        runtime=runtime,
        outbound=outbound,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return comp, bus, outbound, runtime


def _load_interaction(session_factory: Any, reference: str) -> UserInteraction:
    """Read the interaction row for ``reference`` (deterministic lookup)."""
    with session_factory() as session:
        return session.scalars(
            select(UserInteraction)
            .where(UserInteraction.provider_reference == reference)
            .order_by(UserInteraction.created_at.desc())
            .limit(1)
        ).one()


def _publish(bus: InMemoryTransport, event: ExternalEventReceived) -> None:
    """Publish one event as the ingress would: an envelope on the bus.

    The transport dispatches on ``type(envelope.payload)`` — the payload IS the
    ``ExternalEventReceived``; publishing the payload object itself would key
    the registry lookup on ``dict`` and dispatch nothing.
    """
    envelope = EventEnvelope(
        message_id=f"evt-{event.external_id}",
        idempotency_key=f"test:{event.external_id}",
        correlation_id=event.external_id,
        causation_id="",
        occurred_at=NOW.isoformat(),
        event_type=EventType.EXTERNAL_EVENT_RECEIVED,
        producer="external-events",
        payload=event,
    )
    asyncio.run(bus.publish(envelope))


# --------------------------------------------------------------------------- #
# Wiring / fail-loud startup
# --------------------------------------------------------------------------- #


class TestBuildDevComposition:
    def test_wires_the_whole_graph(self, secrets: None, session_factory: Any) -> None:
        comp = build_dev_composition(
            bus=InMemoryTransport(), session_factory=session_factory, clock=lambda: NOW
        )
        assert isinstance(comp.interactions, InteractionService)
        assert isinstance(comp.router, dev_composition.DevCommunicationRouter)
        assert isinstance(comp.resolver, DevSlackDestinationResolver)
        assert isinstance(comp.runtime, GlmAgentRuntime)
        # Binding is the caller's job — the builder never touches the global.
        from communication.interfaces import slack_inbound

        assert slack_inbound._bound_consumer is None

    def test_missing_bot_token_fails_loud(
        self, secrets: None, session_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        with pytest.raises(DevCompositionError, match="SLACK_BOT_TOKEN"):
            build_dev_composition(bus=InMemoryTransport(), session_factory=session_factory)

    def test_missing_signing_secret_fails_loud(
        self, secrets: None, session_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        with pytest.raises(DevCompositionError, match="SLACK_SIGNING_SECRET"):
            build_dev_composition(bus=InMemoryTransport(), session_factory=session_factory)

    def test_blank_glm_model_fails_loud(
        self, secrets: None, session_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty/whitespace value is as good as missing (ID-020 mirror).
        monkeypatch.setenv("SFP_DEFAULT_MODEL", "   ")
        with pytest.raises(DevCompositionError, match="SFP_DEFAULT_MODEL"):
            build_dev_composition(bus=InMemoryTransport(), session_factory=session_factory)


class TestGlmAgentRuntime:
    def _runtime(self, handler: Any) -> GlmAgentRuntime:
        class _Provider:
            def resolve(self, ref: Any) -> str:
                return "dev-token"

        return GlmAgentRuntime(
            base_url="https://glm.example.test/",
            model="glm-4-dev",
            secret_provider=_Provider(),
            secret_ref={"name": "GLM_TOKEN"},  # type: ignore[arg-type]
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    def test_run_extracts_json_from_the_text_block(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["Authorization"]
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": 'noise {"summary": "s"} tail'}]},
            )

        result = self._runtime(handler).run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))
        assert result.success is True
        assert result.output == {"summary": "s"}
        assert seen["url"] == "https://glm.example.test/v1/messages"
        assert seen["auth"] == "Bearer dev-token"

    def test_run_maps_http_error_to_failure(self) -> None:
        runtime = self._runtime(lambda request: httpx.Response(500, json={}))
        result = runtime.run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))
        assert result.success is False
        assert result.error is not None and "500" in result.error

    def test_run_maps_non_json_output_to_failure(self) -> None:
        runtime = self._runtime(
            lambda request: httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})
        )
        result = runtime.run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))
        assert result.success is False

    def test_run_retries_once_with_a_stricter_prompt_on_unparseable_output(self) -> None:
        """A 200-with-prose answer gets ONE stricter retry (SFP-257 finding 2)."""
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return httpx.Response(
                    200, json={"content": [{"type": "text", "text": "no JSON object here"}]}
                )
            return httpx.Response(
                200, json={"content": [{"type": "text", "text": '{"summary": "recovered"}'}]}
            )

        result = self._runtime(handler).run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))

        assert result.success is True
        assert result.output == {"summary": "recovered"}
        # Exactly one retry, and it carried the stricter re-prompt.
        assert len(bodies) == 2
        first_prompt = bodies[0]["messages"][0]["content"]
        retry_prompt = bodies[1]["messages"][0]["content"]
        assert retry_prompt.startswith(first_prompt)
        assert "no parseable JSON object" in retry_prompt
        assert "no parseable JSON object" not in first_prompt

    def test_run_fails_after_exactly_one_retry(self) -> None:
        posts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(str(request.url))
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})

        result = self._runtime(handler).run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))

        assert result.success is False
        assert result.error is not None and "no JSON object" in result.error
        assert len(posts) == 2

    def test_http_error_is_not_retried(self) -> None:
        """Only the unparseable-output path retries — a stricter prompt
        cannot fix a dead endpoint."""
        posts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(str(request.url))
            return httpx.Response(500, json={})

        result = self._runtime(handler).run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))

        assert result.success is False
        assert result.error is not None and "500" in result.error
        assert len(posts) == 1

    def test_run_maps_transport_failure_to_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        result = self._runtime(handler).run(AgentRunRequest(agent="a", ticket_id="t", prompt="p"))
        assert result.success is False
        assert result.error is not None and "ConnectError" in result.error

    def test_probe_accepts_a_reachable_endpoint(self) -> None:
        self._runtime(lambda request: httpx.Response(200, json={})).probe()

    def test_probe_fails_loud_on_unhealthy_endpoint(self) -> None:
        runtime = self._runtime(lambda request: httpx.Response(503, json={}))
        with pytest.raises(DevCompositionError, match="unhealthy"):
            runtime.probe()

    def test_probe_fails_loud_on_unreachable_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(DevCompositionError, match="unreachable"):
            self._runtime(handler).probe()


class TestDevSlackDestinationResolver:
    def test_resolves_the_remembered_channel_and_thread(self) -> None:
        resolver = DevSlackDestinationResolver()
        resolver.remember(THREAD_ROOT, CHANNEL)
        destination = resolver.resolve(THREAD_ROOT)
        assert destination.channel_ref == CHANNEL
        assert destination.thread_ref == THREAD_ROOT

    def test_unknown_session_raises(self) -> None:
        with pytest.raises(LookupError):
            DevSlackDestinationResolver().resolve("unknown")


# --------------------------------------------------------------------------- #
# End-to-end composition behavior
# --------------------------------------------------------------------------- #


class TestEndToEndSyntheticMessage:
    def test_creates_interaction_persists_summary_and_replies(self, composition: Any) -> None:
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())

        # Interaction created; the inbound message recorded.
        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.origin == "inbound"
        assert interaction.last_message_emissor == "user"

        # Summary persisted via the agent's update_summary delegation.
        assert interaction.summary == RUNTIME_SUMMARY
        assert len(runtime.requests) == 1

        # Observable outbound effect via the fake port: the regenerated
        # summary + a CONFIRM re-request, threaded to the right channel.
        assert len(outbound.sends) == 1
        send = outbound.sends[0]
        assert send["channel_ref"] == CHANNEL
        assert send["thread_ref"] == THREAD_ROOT
        assert send["text"] == RUNTIME_SUMMARY + CONFIRM_REQUEST_SUFFIX

        # The bus carries the consumer's UserInputReceived + the interaction
        # updates (observable while the Orchestrator is not wired).
        published_types = [type(m.payload).__name__ for m in bus.published_messages]
        assert "UserInputReceived" in published_types
        assert "UserInteractionUpdated" in published_types


def session_factory_of(comp: Any) -> Any:
    """The composition's unit-of-work factory (exposed for row assertions)."""
    return comp.router._session_factory  # noqa: SLF001 - test observation seam


class TestConfirmFlow:
    def test_confirmation_publishes_and_completes(self, composition: Any) -> None:
        comp, bus, outbound, _runtime = composition
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())  # summary regenerated
        _publish(bus, _slack_event(text="CONFIRM"))

        factory = session_factory_of(comp)
        interaction = _load_interaction(factory, THREAD_ROOT)
        assert interaction.completed_at is not None
        assert asyncio.run(comp.interactions.status(THREAD_ROOT)) is InteractionStatus.COMPLETED

        # The CONFIRM published UserInputReceived carrying the confirmed summary.
        inputs = [
            m.payload
            for m in bus.published_messages
            if type(m.payload).__name__ == "UserInputReceived"
        ]
        # The consumer publishes one per inbound reply; the CONFIRM flow
        # publishes the confirmed summary when the gate passes.
        assert [i.text for i in inputs] == [
            "the deploy finished, rerun the checks",
            "CONFIRM",
            RUNTIME_SUMMARY,
        ]
        # A CONFIRM is not itself a delivery.
        assert len(outbound.sends) == 1

    def test_correction_regenerates_again(self, composition: Any) -> None:
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text="no — mention the rollback too"))

        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.summary == RUNTIME_SUMMARY
        assert len(runtime.requests) == 2
        assert len(outbound.sends) == 2


class TestClosedInteraction:
    def test_closed_thread_gets_new_thread_request_and_context_carries_over(
        self, composition: Any
    ) -> None:
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text="CONFIRM"))
        # The thread is closed now; another reply must NOT mutate it.
        _publish(bus, _slack_event(text="one more thing", ts="1757428802.000400"))

        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.completed_at is not None
        # The "please start a new thread" message was delivered to the thread.
        assert CLOSED_THREAD_MESSAGE in outbound.sends[-1]["text"]
        assert RUNTIME_SUMMARY in outbound.sends[-1]["text"]

        # The user starts a NEW thread: the follow-up interaction carries the
        # prior summary as a contextual context reference (SFP-131 remainder).
        _publish(
            bus,
            _slack_event(
                text="starting fresh: deploy the fix",
                thread_root=THREAD_ROOT_2,
                ts="1757428950.000500",
            ),
        )
        follow_up = _load_interaction(session_factory_of(comp), THREAD_ROOT_2)
        # The regenerated summary is the durable one (landed SFP-135 semantics);
        # the prior summary rode in as the agent's contextual context reference.
        assert follow_up.summary == RUNTIME_SUMMARY
        assert RUNTIME_SUMMARY in runtime.requests[-1].context["prior_summary"]
        assert "starting fresh: deploy the fix" in runtime.requests[-1].context["prior_summary"]

    def test_non_message_events_pass_through_untouched(self, composition: Any) -> None:
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)

        _publish(bus, ExternalEventReceived(source="github", external_id="g1", payload={"k": "v"}))
        _publish(
            bus,
            ExternalEventReceived(
                source="slack", external_id="u1", payload={"type": "url_verification"}
            ),
        )
        # Neither delivery reached the router: no outbound effect, no agent
        # run, and no interaction row exists.
        assert outbound.sends == []
        assert runtime.requests == []
        with session_factory_of(comp)() as session:
            assert session.scalars(select(UserInteraction)).all() == []
        assert all(type(m.payload).__name__ != "UserInputReceived" for m in bus.published_messages)


class TestSelfEchoGuard:
    """The SFP-257 self-loop findings (live smoke rounds 1+2, 2026-09-14):

    - round 1: the bot's own outbound posts echo back as whole-channel
      ``message`` events; interpreting them as user input fed an infinite
      reply loop (~142 ``chat.postMessage`` calls in ~90s, zero LLM runs).
    - round 2: each bot reply into a thread ALSO emits a ``message_replied``
      sub-event for the PARENT message — carrying the HUMAN's user id and no
      bot markers, invisible to a bot-marker blacklist — re-summarizing the
      original message on every bot reply (9 GLM calls / 8 posts over ~6
      minutes).

    The guard is a whitelist at inbound interpretation (a message is user
    input ONLY with no ``subtype`` and no ``bot_id``), so every echo shape
    produces NO outbound effect.
    """

    def test_bot_own_echo_on_a_closed_thread_produces_zero_outbound(self, composition: Any) -> None:
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)
        _publish(bus, _slack_event())  # opens the thread (1 reply)
        _publish(bus, _slack_event(text="CONFIRM"))  # completes the interaction
        sends_before = len(outbound.sends)
        runs_before = len(runtime.requests)
        inputs_before = [
            m for m in bus.published_messages if type(m.payload).__name__ == "UserInputReceived"
        ]

        # The bot's own closed-thread reply arrives back as a whole-channel
        # message event (bot_message subtype + bot_id) — the exact echo that
        # previously fed the loop. Without the guard this delivery would post
        # the closed-thread message again, ping-pong forever.
        _publish(
            bus,
            _slack_event(
                text=f"{CLOSED_THREAD_MESSAGE} (closing summary: {RUNTIME_SUMMARY})",
                user=BOT_USER,
                ts="1757428810.000600",
                subtype="bot_message",
                bot_id=BOT_ID,
            ),
        )

        # Zero outbound, zero LLM runs, zero new publications — the loop
        # cannot start.
        assert len(outbound.sends) == sends_before
        assert len(runtime.requests) == runs_before
        inputs_after = [
            m for m in bus.published_messages if type(m.payload).__name__ == "UserInputReceived"
        ]
        assert len(inputs_after) == len(inputs_before)

    def test_round2_one_human_message_with_bot_thread_replies_summarizes_exactly_once(
        self, composition: Any
    ) -> None:
        """The round-2 incident regression (SFP-257 re-smoke, 2026-09-14).

        One human message produced 8 evolving bot replies over ~6 minutes
        because every bot thread-reply re-triggered a fresh summarize of the
        ORIGINAL message via the ``message_replied`` parent sub-event. With
        the whitelist, one human message + any number of bot replies (and
        their parent sub-events) means EXACTLY ONE summarize run and one
        reply — no re-summaries.
        """
        comp, bus, outbound, runtime = composition
        set_slack_inbound_consumer(comp.router)

        # One human message → exactly one summarize + one bot reply.
        _publish(bus, _slack_event())
        assert len(runtime.requests) == 1
        assert len(outbound.sends) == 1

        # Every bot reply into the thread ALSO emits a message_replied
        # sub-event for the PARENT message — the human's user id, no bot
        # markers, the parent's text. None of them may re-enter summarize.
        for index in range(3):
            _publish(
                bus,
                _slack_event(
                    text="the deploy finished, rerun the checks",
                    user=USER,
                    ts=f"175742882{index}.00070{index}",
                    subtype="message_replied",
                ),
            )

        # Exactly the round-1 count: no re-summaries, no extra replies.
        assert len(runtime.requests) == 1
        assert len(outbound.sends) == 1


class TestSummarizationFailureDegrades:
    """The SFP-257 graceful-degradation finding: a summary that still fails
    after the runtime's retry must NOT 500 the webhook (Slack retry-storms
    a 500ing delivery — measured: five consecutive 500s). The composition
    posts a short apology to the thread and completes the delivery.
    """

    def test_failed_summary_apologizes_in_thread_instead_of_raising(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        bus = InMemoryTransport()
        runtime = FakeRuntime(
            AgentRunResult(
                agent="communication",
                ticket_id="x",
                success=False,
                error="glm returned no JSON object (after 1 retry)",
            )
        )
        outbound = FakeOutbound()
        comp = build_dev_composition(
            bus=bus,
            session_factory=session_factory,
            runtime=runtime,
            outbound=outbound,  # type: ignore[arg-type]
            clock=lambda: NOW,
        )
        set_slack_inbound_consumer(comp.router)

        # Must NOT raise: the publish completing means the webhook returns
        # 200, so Slack does not hammer retries.
        _publish(bus, _slack_event())

        # Exactly one delivery: the short apology, threaded to the sender.
        assert [s["text"] for s in outbound.sends] == [SUMMARIZATION_APOLOGY]
        assert outbound.sends[0]["channel_ref"] == CHANNEL
        assert outbound.sends[0]["thread_ref"] == THREAD_ROOT
        # Nothing was persisted as a summary — the interaction keeps its
        # opening question (create() seeds summary from it).
        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.summary == "the deploy finished, rerun the checks"


# --------------------------------------------------------------------------- #
# Ack-then-process (SFP-257 round 3): the 200 must never wait on the chain
# --------------------------------------------------------------------------- #


#: The signing secret the ``secrets`` fixture plants (mirrors the live app).
_SIGNING_SECRET = "dev-signing-secret"

#: The dev endpoint id ``build_dev_app`` seeds.
_ENDPOINT_ID = "slack-dev"


class SlowRuntime:
    """A blocking ``AgentRuntime`` stand-in — the ~5s GLM round-trip, scaled."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.requests: list[AgentRunRequest] = []
        self.completed = 0

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        time.sleep(self.delay)  # the blocking stand-in is the point of the test
        self.completed += 1
        return AgentRunResult(
            agent="communication",
            ticket_id="x",
            success=True,
            output={"summary": RUNTIME_SUMMARY},
        )


def _slack_delivery_body(*, ts: str = "1757428801.000300") -> bytes:
    """One verbatim Slack Events API delivery body (a plain human message)."""
    return json.dumps(
        {
            "token": "xoxb-verification-token",
            "team_id": "T06SFP",
            "api_app_id": "A06SFP",
            "event_id": f"Ev{ts.replace('.', '')}",
            "type": "event_callback",
            "event": {
                "type": "message",
                "text": "the deploy finished, rerun the checks",
                "channel": CHANNEL,
                "ts": ts,
                "user": USER,
                "thread_ts": THREAD_ROOT,
                "event_ts": ts,
                "channel_type": "channel",
            },
        }
    ).encode("utf-8")


def _sign_slack(secret: str, body: bytes) -> list[tuple[bytes, bytes]]:
    """A valid current-timestamp Slack v0 signature over exactly ``body``."""
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    digest = hmac.new(
        secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return [
        (b"x-slack-signature", f"v0={digest}".encode()),
        (b"x-slack-request-timestamp", timestamp.encode()),
    ]


async def _post_asgi(
    app: Any, *, path: str, body: bytes, headers: list[tuple[bytes, bytes]]
) -> int:
    """Drive one signed POST through the app; return the response status."""
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        assert not delivered, "receive called after the body was fully delivered"
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {"type": "http", "method": "POST", "path": path, "headers": headers},
        receive,
        send,
    )
    start = next(m for m in sent if m["type"] == "http.response.start")
    return int(start["status"])


def _build_ack_app(
    session_factory: Any, runtime: Any, outbound: FakeOutbound
) -> tuple[Any, Any, AckThenProcessPublisher]:
    """``main()``'s wiring over injected seams: app + composition + wrapper.

    Mirrors :func:`external_events.entrypoints.dev_composition.main` exactly
    — the app is built with the ack-then-process ingress publisher, then the
    Communication composition is bound to the SAME bus before serving.
    """
    holder: dict[str, AckThenProcessPublisher] = {}

    def factory(bus: Any) -> AckThenProcessPublisher:
        wrapper = AckThenProcessPublisher(bus)
        holder["wrapper"] = wrapper
        return wrapper

    app, bus, _resolver = build_dev_app(_ENDPOINT_ID, ingress_publisher_factory=factory)
    composition = build_dev_composition(
        bus=bus,
        session_factory=session_factory,
        runtime=runtime,
        outbound=outbound,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    set_slack_inbound_consumer(composition.router)
    return app, bus, holder["wrapper"]


class TestAckThenProcess:
    """SFP-257 round 3: Slack aborts a delivery after ~3s and retries it —
    and a retry is a legitimate fresh user message that re-enters summarize.
    The webhook must ACK (200) before the consume chain runs, and identical
    redeliveries must dedupe on the SFP-124 idempotency key.
    """

    def test_webhook_acks_before_the_slow_consumer_completes(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        runtime = SlowRuntime(delay=0.25)  # the blocking GLM round-trip
        outbound = FakeOutbound()
        app, _bus, wrapper = _build_ack_app(session_factory, runtime, outbound)
        body = _slack_delivery_body()

        async def scenario() -> None:
            status = await _post_asgi(
                app,
                path=f"/webhooks/{_ENDPOINT_ID}",
                body=body,
                headers=_sign_slack(_SIGNING_SECRET, body),
            )
            # The ack went out while the consumer was still mid-run —
            # Slack's ~3s delivery timeout can no longer produce retries.
            assert status == 200
            assert runtime.completed == 0
            assert outbound.sends == []
            await asyncio.gather(*wrapper.in_flight)

        asyncio.run(scenario())

        # The background dispatch completed exactly once afterwards.
        assert runtime.completed == 1
        assert len(outbound.sends) == 1

    def test_identical_redeliveries_summarize_exactly_once(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        runtime = SlowRuntime(delay=0.0)
        outbound = FakeOutbound()
        app, bus, wrapper = _build_ack_app(session_factory, runtime, outbound)
        body = _slack_delivery_body()

        async def scenario() -> None:
            # Five byte-identical redeliveries (the live round-3 burst),
            # each freshly signed — all acked with 200.
            statuses = [
                await _post_asgi(
                    app,
                    path=f"/webhooks/{_ENDPOINT_ID}",
                    body=body,
                    headers=_sign_slack(_SIGNING_SECRET, body),
                )
                for _ in range(5)
            ]
            assert statuses == [200] * 5
            await asyncio.gather(*wrapper.in_flight)

        asyncio.run(scenario())

        # Exactly ONE summarize, ONE reply, ONE event on the bus — the
        # other four deliveries deduped on the SFP-124 idempotency key.
        assert len(runtime.requests) == 1
        assert len(outbound.sends) == 1
        externals = [
            m for m in bus.published_messages if type(m.payload).__name__ == "ExternalEventReceived"
        ]
        assert len(externals) == 1


# --------------------------------------------------------------------------- #
# main() — bind before serve, fail loud (uvicorn stubbed, no socket bound)
# --------------------------------------------------------------------------- #


def _stub_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Install a fake ``uvicorn`` module recording ``run`` calls."""
    calls: list[dict[str, Any]] = []
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda app, **kw: calls.append({"app": app, **kw})  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return calls


class TestMain:
    def test_binds_the_consumer_strictly_before_serving(
        self, secrets: None, monkeypatch: pytest.MonkeyPatch, registry_binding: None
    ) -> None:
        serve_calls = _stub_uvicorn(monkeypatch)
        real_build = dev_composition.build_dev_composition

        def build_without_probe(**kwargs: Any) -> Any:
            # The fake endpoint URL would fail the live reachability probe;
            # binding ORDER is what this test pins.
            return real_build(**{**kwargs, "probe_runtime": False})

        order: list[str] = []
        real_bind = dev_composition.set_slack_inbound_consumer

        def bind_recorder(consumer: Any) -> None:
            assert not serve_calls, "consumer must be bound BEFORE uvicorn.run"
            order.append("bind")
            real_bind(consumer)

        monkeypatch.setattr(dev_composition, "build_dev_composition", build_without_probe)
        monkeypatch.setattr(dev_composition, "set_slack_inbound_consumer", bind_recorder)

        dev_composition.main(["--port", "9911"])

        assert order == ["bind"]
        assert len(serve_calls) == 1
        assert serve_calls[0]["port"] == 9911

    def test_bind_failure_exits_non_zero_without_serving(
        self, secrets: None, monkeypatch: pytest.MonkeyPatch, registry_binding: None
    ) -> None:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        serve_calls = _stub_uvicorn(monkeypatch)

        with pytest.raises(SystemExit) as excinfo:
            dev_composition.main([])

        assert excinfo.value.code == 1
        assert serve_calls == []


# --------------------------------------------------------------------------- #
# SFP-258 — the one-button Block Kit confirm UX (click ≡ typed, multilingual)
# --------------------------------------------------------------------------- #

#: The Catalan summary the language-aware runtime "produces" for an adjust
#: message explicitly requesting Catalan (fixture — the live behavior is an
#: LLM prompt instruction, so determinism here is fixture-based, MAS §12.7).
CATALAN_SUMMARY = "Resum: el desplegament ha acabat; cal repetir les comprovacions."


class FakeBlockOutbound(FakeOutbound):
    """A Block Kit-capable fake port: records ``blocks`` and ``chat.update``."""

    def __init__(self) -> None:
        super().__init__()
        self.updates: list[dict[str, Any]] = []

    def send_message(
        self,
        text: str,
        *,
        channel_ref: str | None = None,
        thread_ref: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> DeliveryReceipt:
        self.sends.append(
            {"text": text, "channel_ref": channel_ref, "thread_ref": thread_ref, "blocks": blocks}
        )
        return DeliveryReceipt(
            provider_message_id=f"msg-{len(self.sends)}",
            channel_ref=channel_ref or "",
            thread_ref=thread_ref,
            provider_reference=f"slack://channel/{channel_ref}",
            ok=True,
            error=None,
        )

    def update_message(
        self,
        *,
        channel_ref: str,
        ts: str,
        text: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
        thread_ref: str | None = None,
    ) -> DeliveryReceipt:
        self.updates.append(
            {
                "channel_ref": channel_ref,
                "ts": ts,
                "text": text,
                "blocks": blocks,
                "thread_ref": thread_ref,
            }
        )
        return DeliveryReceipt(
            provider_message_id=ts,
            channel_ref=channel_ref,
            thread_ref=thread_ref,
            provider_reference=f"slack://channel/{channel_ref}/thread/{thread_ref}",
            ok=True,
            error=None,
        )


class LanguageRuntime:
    """A runtime that answers in Catalan when the adjust message asks for it."""

    def __init__(self) -> None:
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        if "català" in str(request.context.get("current_message", "")):
            summary = CATALAN_SUMMARY
        else:
            summary = RUNTIME_SUMMARY
        return AgentRunResult(
            agent="communication", ticket_id="x", success=True, output={"summary": summary}
        )


def _block_actions_event(
    *,
    thread_root: str = THREAD_ROOT,
    message_ts: str = "1757428805.000900",
    action_id: str = "accept_action",
    channel: str = CHANNEL,
) -> ExternalEventReceived:
    """One verbatim-shaped Slack interactive (block_actions) delivery."""
    return ExternalEventReceived(
        source="slack",
        external_id=f"ba-{message_ts}",
        payload={
            "type": "block_actions",
            "team": {"id": "T0DEV"},
            "user": {"id": USER},
            "channel": {"id": channel},
            "message": {
                "type": "message",
                "ts": message_ts,
                "thread_ts": thread_root,
                "text": RUNTIME_SUMMARY,
            },
            "actions": [
                {"action_id": action_id, "block_id": "decision_buttons", "value": "task_accepted"}
            ],
            "response_url": "https://hooks.slack.com/actions/T0DEV/XXX",
        },
    )


def _block_composition(
    secrets: None,
    session_factory: Any,
    runtime: Any | None = None,
) -> tuple[Any, InMemoryTransport, FakeBlockOutbound, Any]:
    """The composition wired with the Block Kit-capable fake port."""
    bus = InMemoryTransport()
    runtime = runtime if runtime is not None else FakeRuntime()
    outbound = FakeBlockOutbound()
    comp = build_dev_composition(
        bus=bus,
        session_factory=session_factory,
        runtime=runtime,
        outbound=outbound,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return comp, bus, outbound, runtime


def _user_inputs(bus: InMemoryTransport) -> list[Any]:
    """Every ``UserInputReceived`` payload published on the bus."""
    return [
        m.payload for m in bus.published_messages if type(m.payload).__name__ == "UserInputReceived"
    ]


class TestBlockKitConfirmUX:
    """The SFP-258 one-button UX, end-to-end through the composition."""

    def test_summary_reply_renders_the_exact_decision_actions_block(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, _runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())

        assert len(outbound.sends) == 1
        blocks = outbound.sends[0]["blocks"]
        assert blocks is not None
        # Section: the summary + the (English default) hint line.
        assert blocks[0] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": RUNTIME_SUMMARY + CONFIRM_REQUEST_SUFFIX},
        }
        # The owner's EXACT actions block (verbatim per the Requirements).
        assert blocks[1] == {
            "type": "actions",
            "block_id": "decision_buttons",
            "elements": [
                {
                    "type": "button",
                    "action_id": "accept_action",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "✅ Confirm", "emoji": True},
                    "value": "task_accepted",
                }
            ],
        }

    def test_clicking_the_button_confirms_tears_down_and_completes(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _block_actions_event(message_ts="1757428805.000900"))

        # Confirmation: UserInputReceived with the confirmed summary; the
        # interaction completed; the click caused NO regeneration (the only
        # runtime run is the initial summarize).
        assert len(runtime.requests) == 1
        inputs = _user_inputs(bus)
        assert inputs[-1].session_id == THREAD_ROOT
        assert inputs[-1].text == RUNTIME_SUMMARY
        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.completed_at is not None

        # Teardown: chat.update replaced the actions block with the
        # terminal state on the message that carried the button.
        assert len(outbound.updates) == 1
        update = outbound.updates[0]
        assert update["channel_ref"] == CHANNEL
        assert update["ts"] == "1757428805.000900"
        assert update["thread_ref"] == THREAD_ROOT
        assert update["blocks"][1] == dev_composition.CONFIRMED_ACTIONS_BLOCK
        assert update["blocks"][1]["block_id"] == "decision_buttons"

    def test_click_and_typed_confirmation_publish_the_same_payload_shape(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, _outbound, _runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        # Thread 1: typed confirmation; thread 2: the button click.
        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text="CONFIRM"))
        second = "1757428950.000700"
        _publish(bus, _slack_event(thread_root=second, ts="1757428950.000750"))
        _publish(bus, _block_actions_event(thread_root=second, message_ts="1757428950.000800"))

        inputs = _user_inputs(bus)
        confirms = [i for i in inputs if i.text == RUNTIME_SUMMARY]
        assert len(confirms) == 2
        # The SAME payload shape (session_id, text) either way.
        assert confirms[0].session_id == THREAD_ROOT
        assert confirms[1].session_id == second
        assert type(confirms[0]) is type(confirms[1])

    def test_late_click_on_a_completed_interaction_is_closed_not_reconfirmed(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text="CONFIRM"))  # interaction completed
        confirmed_count = len([i for i in _user_inputs(bus) if i.text == RUNTIME_SUMMARY])
        click_count = len(outbound.updates)
        _publish(bus, _block_actions_event(message_ts="1757428805.000999"))

        # No second confirmation, no regeneration — closed stays closed.
        confirmed_after = len([i for i in _user_inputs(bus) if i.text == RUNTIME_SUMMARY])
        assert confirmed_after == confirmed_count == 1
        # The only runtime run is the initial summarize — no regeneration.
        assert len(runtime.requests) == 1
        # The stale button still gets replaced with the terminal state so
        # the completed thread cannot LOOK re-confirmable.
        assert len(outbound.updates) == click_count + 1
        assert outbound.updates[-1]["blocks"][1] == dev_composition.CONFIRMED_ACTIONS_BLOCK

    def test_typed_confirmation_tears_down_the_recorded_summary_message(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, _runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())  # summary delivered as msg-1
        _publish(bus, _slack_event(text="CONFIRM"))

        assert len(outbound.updates) == 1
        update = outbound.updates[0]
        assert update["ts"] == "msg-1"  # the recorded summary-delivery ts
        assert update["channel_ref"] == CHANNEL
        assert update["blocks"][1] == dev_composition.CONFIRMED_ACTIONS_BLOCK

    @pytest.mark.parametrize(
        "word",
        ["confirm", "Confirm", "CONFIRM", "confirmo", "vale", "D'acord", "OUI", "Bestätigt"],
    )
    def test_multilingual_typed_confirmation_confirms_without_a_model_call(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
        word: str,
    ) -> None:
        comp, bus, outbound, runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text=word))

        confirms = [i for i in _user_inputs(bus) if i.text == RUNTIME_SUMMARY]
        assert len(confirms) == 1
        interaction = _load_interaction(session_factory_of(comp), THREAD_ROOT)
        assert interaction.completed_at is not None

    def test_non_confirmation_sentence_is_adjust_input_not_a_confirmation(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(bus, _slack_event(text="sounds good, but add the rollback plan"))

        # Regenerated (a second summarize), NOT confirmed, button re-rendered.
        assert len(runtime.requests) == 2
        assert [i.text for i in _user_inputs(bus)].count(RUNTIME_SUMMARY) == 0
        assert interaction_open(session_factory_of(comp), THREAD_ROOT)
        assert len(outbound.sends) == 2

    def test_catalan_language_request_is_honored_in_summary_and_hint(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, runtime = _block_composition(
            secrets, session_factory, runtime=LanguageRuntime()
        )
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        assert RUNTIME_SUMMARY in outbound.sends[0]["text"]  # English default
        assert outbound.sends[0]["text"].endswith(CONFIRM_REQUEST_SUFFIX)

        _publish(bus, _slack_event(text="Si-us-plau, resumeix-ho en català", ts="1757428802.0"))

        assert len(runtime.requests) == 2
        second = outbound.sends[1]
        assert CATALAN_SUMMARY in second["text"]
        # The hint line follows the requested language (Catalan, not English).
        assert "acceptar aquest resum" in second["text"]
        assert CONFIRM_REQUEST_SUFFIX not in second["text"]

    def test_non_accept_action_click_is_ignored(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        comp, bus, outbound, _runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _slack_event())
        _publish(
            bus,
            _block_actions_event(message_ts="1757428805.000901", action_id="some_other_action"),
        )

        assert outbound.updates == []
        assert interaction_open(session_factory_of(comp), THREAD_ROOT)

    def test_block_actions_never_enters_the_message_path(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        """A block_actions delivery is a structured decision, NOT a message
        event: with no thread summary to confirm it produces NO interaction,
        NO summarize, NO message-path publication (whitelist untouched)."""
        comp, bus, outbound, runtime = _block_composition(secrets, session_factory)
        set_slack_inbound_consumer(comp.router)

        _publish(bus, _block_actions_event())

        assert runtime.requests == []
        assert outbound.sends == []
        assert outbound.updates == []
        with session_factory_of(comp)() as session:  # type: ignore[misc]
            rows = session.scalars(sa.select(UserInteraction)).all()
        assert rows == []


def interaction_open(session_factory: Any, reference: str) -> bool:
    """True when the interaction for ``reference`` has NOT been completed."""
    return _load_interaction(session_factory, reference).completed_at is None


class TestBlockActionsIngress:
    """block_actions flows through the SAME signature-verified ingress chain
    (SFP-254): unsigned → 401 before anything runs; signed → 200 ack, then
    the decision processes in the background (ack-then-process untouched)."""

    def _block_actions_body(self, *, thread_root: str = THREAD_ROOT) -> bytes:
        return json.dumps(
            {
                "type": "block_actions",
                "team": {"id": "T06SFP"},
                "user": {"id": USER},
                "channel": {"id": CHANNEL},
                "message": {
                    "type": "message",
                    "ts": "1757428805.000900",
                    "thread_ts": thread_root,
                },
                "actions": [{"action_id": "accept_action", "value": "task_accepted"}],
            }
        ).encode("utf-8")

    def test_unsigned_block_actions_is_rejected_with_401(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        runtime = SlowRuntime(delay=0.0)
        outbound = FakeOutbound()
        app, _bus, wrapper = _build_ack_app(session_factory, runtime, outbound)
        body = self._block_actions_body()

        async def scenario() -> int:
            status = await _post_asgi(app, path=f"/webhooks/{_ENDPOINT_ID}", body=body, headers=[])
            await asyncio.gather(*wrapper.in_flight)
            return status

        status = asyncio.run(scenario())
        assert status == 401
        # Nothing ran — no summarize, no outbound effect.
        assert runtime.completed == 0
        assert outbound.sends == []

    def test_signed_block_actions_is_acked_then_processed(
        self,
        secrets: None,
        session_factory: Any,
        registry_binding: None,
    ) -> None:
        runtime = SlowRuntime(delay=0.0)
        outbound = FakeOutbound()
        app, _bus, wrapper = _build_ack_app(session_factory, runtime, outbound)
        body = self._block_actions_body()

        async def scenario() -> int:
            status = await _post_asgi(
                app,
                path=f"/webhooks/{_ENDPOINT_ID}",
                body=body,
                headers=_sign_slack(_SIGNING_SECRET, body),
            )
            assert status == 200
            assert runtime.completed == 0  # ack BEFORE the decision ran
            await asyncio.gather(*wrapper.in_flight)
            return status

        assert asyncio.run(scenario()) == 200
        assert runtime.completed == 0  # a decision never summarizes
