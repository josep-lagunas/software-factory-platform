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
- **self-echo guard** (SFP-257 live-smoke finding) — the bot's own outbound
  post echoing back as a bot-authored ``message`` event produces ZERO
  outbound effects (the infinite reply-loop reproduction);
- **graceful degradation** (SFP-257 live-smoke finding) — a summary that
  fails even after the dev runtime's single stricter retry gets a short
  in-thread apology and completes the delivery (never a webhook 500);
- the dev GLM ``AgentRuntime`` adapter and the dev-only destination resolver,
  over ``httpx.MockTransport`` / fakes — no live Slack or GLM anywhere.

Deterministic throughout (MAS §12.7): injected clock, no sleeps, no network.
"""

from __future__ import annotations

import asyncio
import json
import sys
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
    DevCompositionError,
    DevSlackDestinationResolver,
    GlmAgentRuntime,
    build_dev_composition,
)
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
