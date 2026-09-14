"""Tests for the Slack inbound consumer (SFP-132, MAS §9.4, ID-076).

Covers the PRSpec acceptance criteria end-to-end, deterministically (MAS
§12.7):

- **Provider schema** — the local pydantic schema parses the VERBATIM Slack
  Events API body (extra provider fields ignored) into
  text/channel/ts/thread_ts/user; a thread reply maps its provider
  reference to ``thread_ts``, a top-level message to its own ``ts``;
  anything unhandled (``url_verification``, non-``event_callback`` bodies,
  missing ``event``, unhandled event types, missing/empty fields, wrong
  types) yields ``None`` — never an exception.
- **Consumer** — non-Slack sources and unhandled payloads return early (no
  row, no publish); an inbound-first message opens the interaction
  (``origin="inbound"``, ``response_required=False``), updates the two
  ``last_message_*`` fields MAS §9.4 names (NOT ``expires_at`` — SFP-130),
  and publishes ``UserInputReceived``; the ID-076 binding rule routes on
  the SFP-112 ``response_required`` column (``True`` → ``UserQueryReceived``
  with the message text); find-or-create does not republish
  ``UserInteractionUpdated``; a terminal interaction refuses (AP-005) with
  nothing persisted by the consumer and nothing published.
- **Registry dispatch** — the ``@event_handler(ExternalEventReceived)``
  binding resolves through the ``FakeBus`` (SFP-43/46) to the bound
  consumer; unbound raises the pinned not-wired error.
- **No outbound / no interpreter** — the consumer module imports neither
  the SFP-244 interpreter nor any Slack-outbound/httpx surface (ID-076).
- **Drift removal** — the PR #163 receiver modules are gone and their
  exports no longer exist; ingress is solely external-events' route.

Determinism: a fixed ``FakeClock`` (no sleeps, no wall clock), a fixed
in-memory SQLite database (StaticPool + ``ATTACH … AS business``), and the
``FakeBus`` from sfp-testing. The same inputs always yield the same outcome.

Registry hygiene: the sfp-messaging tests clear the process-global default
registry, which can wipe this module's import-time ``@event_handler``
registration depending on collection order. The ``_registered_handlers``
fixture therefore RE-APPLIES the exact same binding (idempotent,
last-write-wins) before every test in this module — deterministic regardless
of run order. This module never *clears* the registry, so it breaks no other
test.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from communication.application.interaction_service import (
    InteractionService,
    InteractionTransitionError,
    SessionFactory,
    session_scope,
)
from communication.infrastructure.persistence import Base, UserInteraction
from communication.interfaces import slack_inbound
from communication.interfaces.slack_inbound import (
    SlackInboundConsumer,
    handle_external_event_received,
    make_user_input_received_envelope,
    make_user_query_received_envelope,
    parse_slack_message,
    set_slack_inbound_consumer,
)
from sfp_contracts.events import (
    ExternalEventReceived,
    UserInputReceived,
    UserInteractionUpdated,
    UserQueryReceived,
)
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging import get_default_registry
from sfp_testing import FakeBus
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# --- Deterministic fixtures ---------------------------------------------------

#: The pinned start of every test's clock (MAS §12.7 — a literal, not now()).
T0 = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

#: The AP-009 / MAS §9.4 inactivity window mirrored from the service constant.
EIGHT_HOURS = timedelta(hours=8)


class FakeClock:
    """A controllable stand-in for the consumer's single clock seam.

    Starts at a pinned instant; ``advance`` moves it deterministically. No
    wall-clock read, no sleep — the ONLY way time passes in these tests.
    """

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


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
def session_factory(engine: sa.Engine) -> SessionFactory:
    """A committing unit-of-work factory over the engine."""
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: session_scope(maker)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def interaction_service(
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> InteractionService:
    """The real SFP-129 service the consumer delegates find-or-create to."""
    return InteractionService(bus=bus, session_factory=session_factory, clock=clock)


@pytest.fixture
def consumer(
    bus: FakeBus,
    interaction_service: InteractionService,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> SlackInboundConsumer:
    """The consumer under test — every seam injected, nothing real touched."""
    return SlackInboundConsumer(
        bus=bus,
        interaction_service=interaction_service,
        session_factory=session_factory,
        clock=clock,
    )


@pytest.fixture(autouse=True)
def _registered_handlers() -> Iterator[None]:
    """Re-apply the module's ``@event_handler`` binding before each test.

    The default registry is process-global and other suites clear it; this
    exact-key re-registration is idempotent (last-write-wins) and makes
    dispatch deterministic regardless of collection order. Never cleared.
    """
    get_default_registry().register(ExternalEventReceived, handle_external_event_received)
    yield


@pytest.fixture
def bound_consumer(consumer: SlackInboundConsumer) -> Iterator[SlackInboundConsumer]:
    """Bind the consumer for the registry-dispatch tests and unbind after."""
    set_slack_inbound_consumer(consumer)
    yield consumer
    set_slack_inbound_consumer(None)


# --- Test helpers ---------------------------------------------------------------


def event_callback_body(
    *,
    event_type: str = "message",
    text: Any = "please continue the run",
    channel: str = "C06SFPDOG",
    ts: str = "1757000000.000100",
    user: str = "U08OWNER",
    thread_ts: str | None = None,
    subtype: str | None = None,
    bot_id: str | None = None,
) -> dict[str, Any]:
    """A VERBATIM-shaped Slack Events API ``event_callback`` body.

    Carries the top-level envelope fields Slack really sends (``token``,
    ``team_id``, ``api_app_id``, ``event_id``, …) and in-event extras
    (``event_ts``, ``channel_type``) so the schema's ``extra="ignore"``
    behavior is exercised against a realistic body, not a minimal stub.
    """
    event: dict[str, Any] = {
        "type": event_type,
        "text": text,
        "channel": channel,
        "ts": ts,
        "user": user,
        "event_ts": ts,
        "channel_type": "channel",
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id is not None:
        event["bot_id"] = bot_id
    return {
        "token": "xoxb-verification-token",
        "team_id": "T06SFP",
        "context_team_id": "T06SFP",
        "context_enterprise_id": None,
        "api_app_id": "A06SFP",
        "is_ext_shared_channel": False,
        "event": event,
        "type": "event_callback",
        "event_id": f"Ev{ts.replace('.', '')}",
        "event_time": 1757000000,
        "authorizations": [{"enterprise_id": None, "team_id": "T06SFP", "user_id": "U08BOT"}],
        "is_retry": False,
    }


def external_envelope(payload: ExternalEventReceived) -> EventEnvelope:
    """A deterministic ingress envelope (SFP-124 reference identity shape)."""
    return EventEnvelope(
        message_id="evt-ext-1",
        idempotency_key=f"{payload.source}:{payload.external_id}",
        correlation_id=payload.external_id,
        causation_id="",
        occurred_at="2026-07-10T12:00:00+00:00",
        event_type=EventType.EXTERNAL_EVENT_RECEIVED,
        producer="external-events",
        payload=payload,
    )


def slack_event(
    payload: dict[str, Any] | None = None, *, source: str = "slack"
) -> ExternalEventReceived:
    """An ``ExternalEventReceived`` carrying the given verbatim payload."""
    return ExternalEventReceived(
        source=source, external_id="sha256-verbatim-body", payload=payload or {}
    )


def load_one(session_factory: SessionFactory, provider_reference: str) -> UserInteraction | None:
    """Load the single interaction for a reference through a FRESH session."""
    with session_factory() as session:
        stmt = select(UserInteraction).where(
            UserInteraction.provider_reference == provider_reference
        )
        return session.scalars(stmt).first()


def count_rows(session_factory: SessionFactory) -> int:
    with session_factory() as session:
        return len(session.scalars(select(UserInteraction)).all())


def published(bus: FakeBus, payload_type: type) -> list[EventEnvelope]:
    """The envelopes published with the given payload type, in publish order."""
    return [
        m  # type: ignore[misc]
        for m in bus.published_messages
        if type(m.payload) is payload_type  # type: ignore[attr-defined]
    ]


def as_utc(moment: datetime) -> datetime:
    """Normalize a SQLite round-tripped naive timestamp to aware UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


# --------------------------------------------------------------------------- #
# The local Slack provider schema (ID-026 / ID-041)
# --------------------------------------------------------------------------- #


class TestSlackProviderSchema:
    def test_parses_verbatim_body_and_ignores_extra_fields(self) -> None:
        message = parse_slack_message(event_callback_body())

        assert message is not None
        assert message.type == "message"
        assert message.text == "please continue the run"
        assert message.channel == "C06SFPDOG"
        assert message.ts == "1757000000.000100"
        assert message.user == "U08OWNER"
        assert message.thread_ts is None

    def test_top_level_message_references_own_ts(self) -> None:
        """No ``thread_ts`` → the message IS its own thread root (MAS §9.4)."""
        message = parse_slack_message(event_callback_body())

        assert message is not None
        assert message.provider_reference == "1757000000.000100"

    def test_thread_reply_references_thread_root(self) -> None:
        """A reply's ``thread_ts`` groups it into the root's interaction."""
        message = parse_slack_message(
            event_callback_body(ts="1757000000.000900", thread_ts="1757000000.000100")
        )

        assert message is not None
        assert message.thread_ts == "1757000000.000100"
        assert message.provider_reference == "1757000000.000100"

    def test_app_mention_is_a_handled_event_type(self) -> None:
        message = parse_slack_message(event_callback_body(event_type="app_mention"))

        assert message is not None
        assert message.type == "app_mention"

    def test_url_verification_handshake_is_not_a_message(self) -> None:
        assert parse_slack_message({"type": "url_verification", "challenge": "abc123"}) is None

    def test_non_event_callback_body_is_not_a_message(self) -> None:
        """Slack interactivity payloads share the route but not the shape."""
        assert parse_slack_message({"type": "block_actions", "actions": []}) is None

    def test_event_callback_without_event_is_not_a_message(self) -> None:
        assert parse_slack_message({"type": "event_callback"}) is None

    def test_unhandled_event_type_is_not_a_message(self) -> None:
        """A reaction carries all the fields but is not a human message."""
        body = event_callback_body(event_type="reaction_added")
        assert parse_slack_message(body) is None

    @pytest.mark.parametrize("field", ["text", "channel", "ts", "user", "type"])
    def test_missing_required_field_yields_none(self, field: str) -> None:
        body = event_callback_body()
        assert isinstance(body["event"], dict)
        del body["event"][field]
        assert parse_slack_message(body) is None

    def test_empty_text_yields_none(self) -> None:
        """Subtype edit events (``text=""``) are not messages (v0 scope)."""
        assert parse_slack_message(event_callback_body(text="")) is None

    def test_wrongly_typed_fields_yield_none(self) -> None:
        body = event_callback_body(text=12345)
        assert parse_slack_message(body) is None

    def test_event_of_wrong_type_yields_none(self) -> None:
        body = event_callback_body()
        body["event"] = "not-a-dict"
        assert parse_slack_message(body) is None

    def test_bot_message_subtype_is_not_a_user_message(self) -> None:
        """The app's own posts echo back as ``bot_message`` — not user input.

        The round-1 SFP-257 self-loop guard: interpreting the app's own
        outbound echo as a user message is an infinite reply loop (live
        smoke 2026-09-14).
        """
        body = event_callback_body(subtype="bot_message", bot_id="B08SFPAPP")
        assert parse_slack_message(body) is None

    def test_bot_id_alone_is_not_a_user_message(self) -> None:
        """Either bot marker alone disqualifies the event (belt-and-braces)."""
        body = event_callback_body(bot_id="B08SFPAPP")
        assert parse_slack_message(body) is None

    def test_message_replied_parent_echo_is_not_user_input(self) -> None:
        """The round-2 SFP-257 incident shape — the blacklist's blind spot.

        A bot reply posted into a thread makes Slack emit a ``message_replied``
        sub-event for the PARENT message: it carries the HUMAN's ``user`` id
        and NO bot markers, yet is not new input. Passing it re-summarizes
        the original message on every bot reply (measured: one human message
        → 9 GLM calls / 8 posts over ~6 minutes).
        """
        body = event_callback_body(subtype="message_replied")
        assert parse_slack_message(body) is None

    @pytest.mark.parametrize(
        "subtype",
        [
            "message_changed",
            "message_deleted",
            "channel_topic",
            "thread_broadcast",
            "tombstone",
        ],
    )
    def test_subtyped_state_events_are_not_user_input(self, subtype: str) -> None:
        """Every sub-typed event is Slack state on an existing message.

        ``thread_broadcast`` included: a broadcast is a duplicate rendering
        of a reply already delivered as a plain thread event — letting it
        through would double-count that input.
        """
        assert parse_slack_message(event_callback_body(subtype=subtype)) is None

    def test_plain_human_message_is_the_only_user_input_shape(self) -> None:
        message = parse_slack_message(event_callback_body())

        assert message is not None
        assert message.subtype is None
        assert message.bot_id is None
        assert message.is_bot_authored is False
        assert message.is_user_input is True


# --------------------------------------------------------------------------- #
# The consumer — filtering, advancement, and the ID-076 routing rule
# --------------------------------------------------------------------------- #


class TestSlackInboundConsumer:
    async def test_non_slack_source_returns_early(
        self, consumer: SlackInboundConsumer, session_factory: SessionFactory, bus: FakeBus
    ) -> None:
        await consumer.consume(slack_event(event_callback_body(), source="github"))

        assert count_rows(session_factory) == 0
        assert bus.published_messages == []

    async def test_unhandled_payload_returns_early(
        self, consumer: SlackInboundConsumer, session_factory: SessionFactory, bus: FakeBus
    ) -> None:
        await consumer.consume(slack_event({"type": "url_verification", "challenge": "abc123"}))

        assert count_rows(session_factory) == 0
        assert bus.published_messages == []

    async def test_bot_authored_echo_returns_early(
        self, consumer: SlackInboundConsumer, session_factory: SessionFactory, bus: FakeBus
    ) -> None:
        """A bot-authored echo never advances an interaction (SFP-257)."""
        await consumer.consume(
            slack_event(event_callback_body(subtype="bot_message", bot_id="B08SFPAPP"))
        )

        assert count_rows(session_factory) == 0
        assert bus.published_messages == []

    async def test_message_replied_parent_echo_returns_early(
        self, consumer: SlackInboundConsumer, session_factory: SessionFactory, bus: FakeBus
    ) -> None:
        """A sub-typed parent echo carrying the HUMAN's id never advances an
        interaction (SFP-257 round 2 — no bot markers present)."""
        await consumer.consume(slack_event(event_callback_body(subtype="message_replied")))

        assert count_rows(session_factory) == 0
        assert bus.published_messages == []

    async def test_inbound_first_message_opens_interaction_and_publishes_user_input(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        bus: FakeBus,
    ) -> None:
        body = event_callback_body()
        text = body["event"]["text"]
        assert isinstance(text, str)

        await consumer.consume(slack_event(body))

        row = load_one(session_factory, "1757000000.000100")
        assert row is not None
        assert row.origin == "inbound"
        assert row.channel == "slack"
        assert row.type == "user_input"
        assert row.response_required is False
        assert row.question == text
        assert row.summary == text
        assert row.last_message_emissor == "user"
        assert as_utc(row.last_message_timestamp) == T0
        assert as_utc(row.expires_at) == T0 + EIGHT_HOURS

        # create() published its UserInteractionUpdated (ACTIVE)…
        assert bus.published_count(UserInteractionUpdated) == 1
        # …and the consumer routed to UserInputReceived (no open question).
        assert bus.published_count(UserQueryReceived) == 0
        inputs = published(bus, UserInputReceived)
        assert len(inputs) == 1
        event = inputs[0].payload
        assert isinstance(event, UserInputReceived)
        assert event.session_id == "1757000000.000100"
        assert event.text == text
        assert inputs[0].event_type is EventType.USER_INPUT_RECEIVED
        assert inputs[0].producer == "communication"
        assert inputs[0].idempotency_key == f"user-input:1757000000.000100:{text}"

    async def test_thread_reply_advances_the_thread_interaction(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
    ) -> None:
        """The platform asked a question in a thread; the user replies in it."""
        await interaction_service.create(
            "1757000000.000100",
            interaction_type="user_input_request",
            question="Which ticket should run next?",
            response_required=True,
        )

        reply = event_callback_body(
            text="run SFP-132 next", ts="1757000000.000900", thread_ts="1757000000.000100"
        )
        await consumer.consume(slack_event(reply))

        assert count_rows(session_factory) == 1  # find-or-create: no second row
        row = load_one(session_factory, "1757000000.000100")
        assert row is not None
        assert row.last_message_emissor == "user"
        assert row.response_required is True

    async def test_response_required_true_publishes_user_query(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        bus: FakeBus,
    ) -> None:
        """ID-076(d): an open question routes the reply to UserQueryReceived."""
        await interaction_service.create(
            "1757000000.000100",
            interaction_type="user_input_request",
            question="Which ticket should run next?",
            response_required=True,
        )
        updates_before = bus.published_count(UserInteractionUpdated)

        await consumer.consume(
            slack_event(
                event_callback_body(
                    text="run SFP-132 next",
                    ts="1757000000.000900",
                    thread_ts="1757000000.000100",
                )
            )
        )

        queries = published(bus, UserQueryReceived)
        assert len(queries) == 1
        event = queries[0].payload
        assert isinstance(event, UserQueryReceived)
        assert event.session_id == "1757000000.000100"
        assert event.query == "run SFP-132 next"
        assert queries[0].event_type is EventType.USER_QUERY_RECEIVED
        assert queries[0].producer == "communication"
        assert queries[0].idempotency_key == "user-query:1757000000.000100:run SFP-132 next"
        # find-or-create idempotency: no second UserInteractionUpdated.
        assert bus.published_count(UserInteractionUpdated) == updates_before
        assert bus.published_count(UserInputReceived) == 0

    async def test_response_required_false_existing_publishes_user_input(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        bus: FakeBus,
    ) -> None:
        """A notification interaction (no open question) routes to input."""
        await interaction_service.create(
            "1757000000.000100",
            interaction_type="notification",
            question="CI is green on main.",
            response_required=False,
        )

        await consumer.consume(
            slack_event(
                event_callback_body(
                    text="noted, thanks",
                    ts="1757000000.000900",
                    thread_ts="1757000000.000100",
                )
            )
        )

        assert bus.published_count(UserQueryReceived) == 0
        inputs = published(bus, UserInputReceived)
        assert len(inputs) == 1
        assert isinstance(inputs[0].payload, UserInputReceived)
        assert inputs[0].payload.session_id == "1757000000.000100"
        assert inputs[0].payload.text == "noted, thanks"

    async def test_last_message_advances_with_clock_and_expiry_is_untouched(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        clock: FakeClock,
    ) -> None:
        """``last_message_*`` follow the clock; ``expires_at`` does NOT (SFP-130)."""
        await interaction_service.create(
            "1757000000.000100", question="status?", response_required=False
        )

        clock.advance(timedelta(hours=1))
        await consumer.consume(slack_event(event_callback_body()))

        row = load_one(session_factory, "1757000000.000100")
        assert row is not None
        assert as_utc(row.last_message_timestamp) == T0 + timedelta(hours=1)
        assert as_utc(row.expires_at) == T0 + EIGHT_HOURS

    async def test_terminal_interaction_refuses_without_side_effects(
        self,
        consumer: SlackInboundConsumer,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        bus: FakeBus,
    ) -> None:
        """AP-005: a reply to a completed interaction propagates the refusal."""
        await interaction_service.create(
            "1757000000.000100", question="done?", response_required=True
        )
        await interaction_service.complete("1757000000.000100")

        with pytest.raises(InteractionTransitionError):
            await consumer.consume(slack_event(event_callback_body()))

        # Nothing new persisted by the consumer, nothing published.
        assert count_rows(session_factory) == 1
        assert bus.published_count(UserInputReceived) == 0
        assert bus.published_count(UserQueryReceived) == 0
        row = load_one(session_factory, "1757000000.000100")
        assert row is not None
        assert row.completed_at is not None
        assert row.last_message_emissor == "agent"  # untouched


# --------------------------------------------------------------------------- #
# Registry dispatch (SFP-43 / SFP-46) and wiring
# --------------------------------------------------------------------------- #


class TestRegistryDispatch:
    async def test_external_event_dispatches_to_bound_consumer(
        self,
        bound_consumer: SlackInboundConsumer,
        session_factory: SessionFactory,
        bus: FakeBus,
    ) -> None:
        """FakeBus publish resolves the handler by payload type (SFP-43)."""
        await bus.publish(external_envelope(slack_event(event_callback_body())))

        assert load_one(session_factory, "1757000000.000100") is not None
        assert bus.published_count(UserInputReceived) == 1

    async def test_non_slack_source_records_but_does_not_advance(
        self,
        bound_consumer: SlackInboundConsumer,
        session_factory: SessionFactory,
        bus: FakeBus,
    ) -> None:
        await bus.publish(external_envelope(slack_event(event_callback_body(), source="github")))

        assert count_rows(session_factory) == 0
        assert len(bus.published_messages) == 1  # only the consumed event itself

    async def test_unbound_consumer_raises_pinned_error(self, bus: FakeBus) -> None:
        set_slack_inbound_consumer(None)

        with pytest.raises(RuntimeError, match="set_slack_inbound_consumer"):
            await bus.publish(external_envelope(slack_event(event_callback_body())))


# --------------------------------------------------------------------------- #
# Envelope factories — deterministic identity (SFP-124 discipline)
# --------------------------------------------------------------------------- #


class TestEnvelopeFactories:
    def test_user_query_envelope_is_deterministic_per_fact(self) -> None:
        event = UserQueryReceived(session_id="1757000000.000100", query="run SFP-132 next")

        first = make_user_query_received_envelope(event)
        second = make_user_query_received_envelope(event)

        assert first.idempotency_key == second.idempotency_key
        assert first.idempotency_key == "user-query:1757000000.000100:run SFP-132 next"
        assert first.message_id != second.message_id  # a republish is a new message
        assert first.event_type is EventType.USER_QUERY_RECEIVED
        assert first.producer == "communication"
        assert first.payload is event
        assert first.correlation_id == "1757000000.000100"

    def test_user_input_envelope_is_deterministic_per_fact(self) -> None:
        event = UserInputReceived(session_id="1757000000.000100", text="noted")

        first = make_user_input_received_envelope(event)
        second = make_user_input_received_envelope(event)

        assert first.idempotency_key == second.idempotency_key
        assert first.idempotency_key == "user-input:1757000000.000100:noted"
        assert first.message_id != second.message_id
        assert first.event_type is EventType.USER_INPUT_RECEIVED
        assert first.producer == "communication"
        assert first.payload is event


# --------------------------------------------------------------------------- #
# No outbound Slack calls, no SFP-244 interpreter (ID-076 — acceptance)
# --------------------------------------------------------------------------- #


class TestNoOutboundNoInterpreter:
    def test_module_does_not_import_the_interpreter(self) -> None:
        assert not hasattr(slack_inbound, "OperationalCommandInterpreter")

    def test_module_source_references_no_outbound_or_interpreter_surface(self) -> None:
        source = Path(slack_inbound.__file__).read_text(encoding="utf-8")
        for forbidden in ("operational_commands", "slack_outbound", "SlackOutboundClient", "httpx"):
            assert forbidden not in source, f"forbidden inbound reference: {forbidden}"


# --------------------------------------------------------------------------- #
# Drift removal (PR #163 → ID-076: ingress belongs to external-events only)
# --------------------------------------------------------------------------- #


class TestDriftRemoval:
    @pytest.mark.parametrize(
        "module_name",
        [
            "communication.entrypoints.slack_events_endpoint",
            "communication.entrypoints.dev_slack_events",
        ],
    )
    def test_drifted_modules_are_gone(
        self, module_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Purge any stale sys.modules entry so the import re-resolves on disk.
        monkeypatch.delitem(sys.modules, module_name, raising=False)

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)

    def test_entrypoints_exports_no_slack_events_names(self) -> None:
        entrypoints = importlib.import_module("communication.entrypoints")

        for name in ("SlackEventsEndpoint", "SLACK_EVENTS_PATH", "build_dev_app"):
            assert not hasattr(entrypoints, name)

    def test_entrypoints_layer_serves_no_http_app(self) -> None:
        """``/slack/events`` 404s: communication exposes no ASGI app at all.

        With the receiver modules deleted and the entrypoints package
        exporting nothing, no communication code serves HTTP — ingress is
        solely the external-events ``/webhooks/{endpoint_id}`` route
        (MAS §9.2), and Communication's inbound half is the bus consumer in
        ``communication.interfaces.slack_inbound``.
        """
        entrypoints = importlib.import_module("communication.entrypoints")

        public_names = [name for name in vars(entrypoints) if not name.startswith("__")]
        assert public_names == []
