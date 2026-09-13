"""Tests for the ``InteractionService`` application layer (SFP-129, MAS §9.4).

Covers the PRSpec acceptance criteria end-to-end, deterministically (MAS §12.7):

- **Derived status** — all three statuses from timestamps only, the
  ``now == expires_at`` boundary, and naive/aware normalization (the SQLite
  test round-trip returns naive datetimes; the clock is UTC-aware).
- **create()** — persists the SFP-112 row (``expires_at = now + 8h``, AP-009)
  and publishes exactly one ``UserInteractionUpdated`` (state ``ACTIVE``);
  find-or-create idempotency (same id, one row, one event); a TERMINAL
  reference refuses (AP-005 — no reopen in v0, SFP-131).
- **complete()** — the single terminal write: sets ``completed_at``, persists,
  publishes (state ``COMPLETED``); on ``COMPLETED``/``EXPIRED`` raises
  ``InteractionTransitionError`` carrying the derived status, persists and
  publishes nothing.
- **Handlers** — ``RequestUserInput`` / ``NotifyUser`` registered via the
  ``@command_handler`` registry and dispatched through the ``FakeBus``
  (sfp-testing) delegate to the bound service; no Slack I/O is touched
  (posting is SFP-133).
- **update_summary()** (SFP-135) — the concrete
  ``InteractionSummaryWriter`` port (SFP-134, runtime_checkable
  conformance): persists the durable summary, publishes
  ``UserInteractionUpdated`` (``ACTIVE``); unknown reference fails closed
  (``LookupError``); a terminal interaction refuses (AP-005).
- **record_message()** (SFP-135) — the MAS §9.4 message write: updates the
  two ``last_message_*`` fields and re-anchors the 8h window at the
  message instant, so a would-be-EXPIRED interaction derives ACTIVE again;
  unknown reference fails closed.
- **Invariant guard** — the SFP-112 model gains NO status column (derived
  state is mandatory; a schema change is explicitly out of scope).

Determinism: a fixed ``FakeClock`` (no sleeps, no wall clock), a fixed
in-memory SQLite database (StaticPool + ``ATTACH … AS business`` for the
``business`` schema), and the ``FakeBus`` from sfp-testing. The same inputs
always yield the same outcome.

Registry hygiene: the sfp-messaging tests clear the process-global default
registry, which can wipe this module's import-time ``@command_handler``
registrations depending on collection order. The ``_registered_handlers``
fixture therefore RE-APPLIES the exact same bindings (idempotent,
last-write-wins) before every test in this module — deterministic regardless
of run order. This module never *clears* the registry, so it breaks no other
test.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from communication.application.communication_agent import InteractionSummaryWriter
from communication.application.interaction_service import (
    InteractionService,
    InteractionStatus,
    InteractionTransitionError,
    SessionFactory,
    derive_status,
    handle_notify_user,
    handle_request_user_input,
    make_user_interaction_updated_envelope,
    session_scope,
    set_interaction_service,
)
from communication.infrastructure.persistence import Base, UserInteraction
from sfp_contracts.commands import NotifyUser, RequestUserInput
from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.events import UserInteractionUpdated
from sfp_contracts.events.envelope import EventType
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
    """A controllable stand-in for the service's single clock seam.

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
    """An in-memory SQLite engine with the ``business`` schema attached.

    StaticPool pins one connection so the ``ATTACH`` survives every checkout
    (each ``:memory:`` connection would otherwise be a fresh database), and
    ``create_all`` materializes the SFP-112 table on the service Base.
    """
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
    """A committing unit-of-work factory over the engine.

    ``session_scope`` over a bare ``sessionmaker`` satisfies the
    :data:`SessionFactory` contract (commit on success, rollback on error);
    ``expire_on_commit=False`` keeps returned instances readable after the
    unit of work commits.
    """
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return lambda: session_scope(maker)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def service(
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> InteractionService:
    """The service under test — every seam injected, nothing real touched."""
    return InteractionService(
        bus=bus,
        session_factory=session_factory,
        clock=clock,
    )


@pytest.fixture(autouse=True)
def _registered_handlers() -> Iterator[None]:
    """Re-apply the module's ``@command_handler`` bindings before each test.

    The default registry is process-global and other suites clear it; these
    exact-key re-registrations are idempotent (last-write-wins) and make
    dispatch deterministic regardless of collection order. Never cleared here.
    """
    registry = get_default_registry()
    registry.register(RequestUserInput, handle_request_user_input)
    registry.register(NotifyUser, handle_notify_user)
    yield


@pytest.fixture
def bound_service(service: InteractionService) -> Iterator[InteractionService]:
    """Bind the service for the command handlers and unbind afterwards."""
    set_interaction_service(service)
    yield service
    set_interaction_service(None)  # type: ignore[arg-type]


def _load_one(session_factory: SessionFactory, provider_reference: str) -> UserInteraction | None:
    """Load the single interaction for a reference through a FRESH session."""
    with session_factory() as session:
        stmt = select(UserInteraction).where(
            UserInteraction.provider_reference == provider_reference
        )
        return session.scalars(stmt).first()


def _count_rows(session_factory: SessionFactory) -> int:
    with session_factory() as session:
        return len(session.scalars(select(UserInteraction)).all())


def _command_envelope(command_type: CommandType, payload: Any) -> CommandEnvelope:
    """A deterministic command envelope for handler-dispatch tests."""
    return CommandEnvelope(
        message_id=f"cmd-{command_type.value.lower()}",
        idempotency_key=f"idem-{command_type.value.lower()}",
        correlation_id="corr-1",
        causation_id="",
        occurred_at="2026-07-10T12:00:00+00:00",
        command_type=command_type,
        payload=payload,
    )


def _make_interaction(
    *,
    provider_reference: str = "C1/T1",
    completed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> UserInteraction:
    """A fully-populated SFP-112 instance for pure-derivation tests."""
    return UserInteraction(
        origin="outbound",
        type="question",
        response_required=False,
        channel="slack",
        provider_reference=provider_reference,
        question="What is SFP?",
        summary="User asked what SFP is.",
        last_message_emissor="agent",
        last_message_timestamp=T0,
        expires_at=expires_at if expires_at is not None else T0 + EIGHT_HOURS,
        completed_at=completed_at,
    )


# --- Derived status (pure function of the SFP-112 timestamps) -----------------


def test_derive_status_completed_when_completed_at_set() -> None:
    completed = T0 + timedelta(minutes=5)
    assert derive_status(_make_interaction(completed_at=completed), now=T0) is (
        InteractionStatus.COMPLETED
    )


def _load_completed_at(session_factory: SessionFactory, provider_reference: str) -> datetime | None:
    """The persisted completed_at for a reference, through a fresh session."""
    loaded = _load_one(session_factory, provider_reference)
    assert loaded is not None, f"no row for {provider_reference!r}"
    return loaded.completed_at


def test_derive_status_completed_wins_over_expired() -> None:
    """Completion is authoritative: a completed interaction never reads EXPIRED."""
    interaction = _make_interaction(
        completed_at=T0 + timedelta(minutes=5),
        expires_at=T0 - timedelta(seconds=1),
    )
    assert derive_status(interaction, now=T0) is InteractionStatus.COMPLETED


def test_derive_status_expired_when_now_strictly_past_deadline() -> None:
    interaction = _make_interaction(expires_at=T0 + EIGHT_HOURS)
    now = T0 + EIGHT_HOURS + timedelta(seconds=1)
    assert derive_status(interaction, now=now) is InteractionStatus.EXPIRED


def test_derive_status_active_before_deadline() -> None:
    interaction = _make_interaction(expires_at=T0 + EIGHT_HOURS)
    assert derive_status(interaction, now=T0) is InteractionStatus.ACTIVE


def test_derive_status_boundary_now_equals_expires_at_is_active() -> None:
    """The boundary belongs to the open window: only a STRICTLY later now expires."""
    interaction = _make_interaction(expires_at=T0 + EIGHT_HOURS)
    assert derive_status(interaction, now=T0 + EIGHT_HOURS) is InteractionStatus.ACTIVE


def test_derive_status_normalizes_naive_sqlite_timestamps() -> None:
    """SQLite round-trips drop tzinfo; the derivation seam treats naive as UTC."""
    naive_interaction = _make_interaction(expires_at=(T0 + EIGHT_HOURS).replace(tzinfo=None))
    assert derive_status(naive_interaction, now=T0) is InteractionStatus.ACTIVE
    late = T0 + EIGHT_HOURS + timedelta(seconds=1)
    naive_expired = _make_interaction(expires_at=(T0 - timedelta(seconds=1)).replace(tzinfo=None))
    assert derive_status(naive_expired, now=late) is InteractionStatus.EXPIRED


def test_user_interaction_model_has_no_status_column() -> None:
    """Derived state is mandatory (MAS §9.4): the SFP-112 model stays untouched."""
    columns = [column.name for column in UserInteraction.__table__.columns]
    assert "status" not in columns


# --- create(): find-or-create + persistence + event ---------------------------


async def test_create_persists_row_and_publishes_active_event(
    service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    interaction = await service.create(
        "C1/T1",
        interaction_type="user_input_request",
        question="Approve the PR?",
        response_required=True,
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    # The row round-trips through a fresh session with the creation policy.
    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.interaction_id == interaction.interaction_id
    assert loaded.origin == "outbound"
    assert loaded.type == "user_input_request"
    assert loaded.response_required is True
    assert loaded.channel == "slack"
    assert loaded.provider_reference == "C1/T1"
    assert loaded.question == "Approve the PR?"
    assert loaded.summary == "Approve the PR?"  # AP-009: summary initialized, no transcript
    assert loaded.last_message_emissor == "agent"
    assert loaded.last_message_timestamp is not None
    assert loaded.expires_at is not None
    assert loaded.completed_at is None
    assert loaded.user_id == UUID("00000000-0000-0000-0000-000000000001")

    # Exactly one UserInteractionUpdated event, state ACTIVE, enveloped.
    bus.assert_published(UserInteractionUpdated, times=1)
    event = bus.messages_of(UserInteractionUpdated)[0]
    assert event.session_id == "C1/T1"
    assert event.state == "ACTIVE"


async def test_create_sets_eight_hour_expiry_from_creation_clock(
    service: InteractionService,
    session_factory: SessionFactory,
) -> None:
    """AP-009: expires_at is the creation-time now + the 8h inactivity window."""
    await service.create("C1/T1")
    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    expected = (T0 + EIGHT_HOURS).replace(tzinfo=None)
    assert loaded.expires_at == expected


async def test_create_is_find_or_create_idempotent(
    service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    """Duplicate create for an existing non-terminal ref: same interaction, no
    second insert, no second event."""
    first = await service.create("C1/T1", question="Approve the PR?")
    second = await service.create("C1/T1", question="Approve the PR?")

    assert second.interaction_id == first.interaction_id
    assert _count_rows(session_factory) == 1
    bus.assert_published(UserInteractionUpdated, times=1)


async def test_create_on_terminal_reference_refuses_without_reopen(
    service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    """AP-005: a completed reference cannot create a new interaction (SFP-131
    owns closed-handling); nothing extra is persisted or published."""
    await service.create("C1/T1")
    await service.complete("C1/T1")

    with pytest.raises(InteractionTransitionError) as excinfo:
        await service.create("C1/T1")

    assert excinfo.value.derived_status is InteractionStatus.COMPLETED
    assert _count_rows(session_factory) == 1
    bus.assert_published(UserInteractionUpdated, times=2)  # create + complete only


# --- complete(): the single terminal write transition -------------------------


async def test_complete_on_active_sets_completed_at_persists_and_publishes(
    service: InteractionService,
    session_factory: SessionFactory,
    clock: FakeClock,
    bus: FakeBus,
) -> None:
    await service.create("C1/T1")
    clock.advance(timedelta(minutes=10))
    completed = await service.complete("C1/T1")

    assert completed.completed_at == clock.now
    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.completed_at is not None

    events = bus.messages_of(UserInteractionUpdated)
    assert [event.state for event in events] == ["ACTIVE", "COMPLETED"]


async def test_complete_on_completed_raises_terminal_immutability(
    service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    await service.create("C1/T1")
    await service.complete("C1/T1")
    first_completed_at = _load_completed_at(session_factory, "C1/T1")
    assert first_completed_at is not None

    with pytest.raises(InteractionTransitionError) as excinfo:
        await service.complete("C1/T1")

    assert excinfo.value.derived_status is InteractionStatus.COMPLETED
    assert "COMPLETED" in repr(excinfo.value)
    assert excinfo.value.interaction_id is not None
    # Immutability: the original completion instant stands, no third event.
    assert _load_completed_at(session_factory, "C1/T1") == first_completed_at
    bus.assert_published(UserInteractionUpdated, times=2)


async def test_complete_on_expired_raises_and_writes_nothing(
    service: InteractionService,
    session_factory: SessionFactory,
    clock: FakeClock,
    bus: FakeBus,
) -> None:
    await service.create("C1/T1")
    clock.advance(EIGHT_HOURS + timedelta(seconds=1))

    with pytest.raises(InteractionTransitionError) as excinfo:
        await service.complete("C1/T1")

    assert excinfo.value.derived_status is InteractionStatus.EXPIRED
    assert "EXPIRED" in repr(excinfo.value)
    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.completed_at is None  # expiry refused the write — nothing persisted
    bus.assert_published(UserInteractionUpdated, times=1)  # only the creation event


async def test_complete_unknown_reference_raises_lookup_error(
    service: InteractionService,
) -> None:
    with pytest.raises(LookupError):
        await service.complete("C404/T404")


# --- update_summary(): the InteractionSummaryWriter port (SFP-135 / SFP-134) ---


async def test_update_summary_satisfies_landed_protocol(
    service: InteractionService,
) -> None:
    """The runtime_checkable InteractionSummaryWriter port (SFP-134) accepts the service."""
    assert isinstance(service, InteractionSummaryWriter)


async def test_update_summary_persists_and_publishes_active(
    service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    await service.create("C1/T1", question="Deploy staging?")
    bus.assert_published(UserInteractionUpdated, times=1)

    await service.update_summary("C1/T1", "User asked to deploy staging.")

    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.summary == "User asked to deploy staging."  # durable, never a transcript
    assert loaded.completed_at is None
    events = bus.messages_of(UserInteractionUpdated)
    assert [event.state for event in events] == ["ACTIVE", "ACTIVE"]
    assert events[-1].session_id == "C1/T1"


async def test_update_summary_unknown_reference_fails_closed(
    service: InteractionService,
    bus: FakeBus,
) -> None:
    """No interaction for the reference is a typed failure — never a silent no-op."""
    with pytest.raises(LookupError):
        await service.update_summary("C404/T404", "orphan summary")
    bus.assert_published(UserInteractionUpdated, times=0)


async def test_update_summary_on_terminal_interaction_refuses(
    service: InteractionService,
    session_factory: SessionFactory,
    clock: FakeClock,
    bus: FakeBus,
) -> None:
    """AP-005: a summary write never mutates a closed interaction."""
    await service.create("C1/T1", question="Deploy staging?")
    await service.complete("C1/T1")
    before = _load_one(session_factory, "C1/T1")
    assert before is not None
    bus.assert_published(UserInteractionUpdated, times=2)

    with pytest.raises(InteractionTransitionError) as completed_excinfo:
        await service.update_summary("C1/T1", "late correction")
    assert completed_excinfo.value.derived_status is InteractionStatus.COMPLETED

    # …and an EXPIRED interaction is likewise closed to summary writes.
    await service.create("C2/T2", question="Deploy staging?")
    clock.advance(EIGHT_HOURS + timedelta(seconds=1))
    with pytest.raises(InteractionTransitionError) as expired_excinfo:
        await service.update_summary("C2/T2", "late correction")
    assert expired_excinfo.value.derived_status is InteractionStatus.EXPIRED

    after = _load_one(session_factory, "C1/T1")
    assert after is not None
    assert after.summary == before.summary  # nothing persisted
    # create C1 + complete C1 + create C2 only — the refusals publish nothing.
    assert bus.published_count(UserInteractionUpdated) == 3


# --- record_message(): the MAS §9.4 expiry-timer reset (SFP-135) ----------------


async def test_record_message_updates_last_message_fields_and_resets_timer(
    service: InteractionService,
    session_factory: SessionFactory,
    clock: FakeClock,
    bus: FakeBus,
) -> None:
    await service.create("C1/T1", question="Deploy staging?")

    # The clock moves past the creation window: the interaction WOULD derive
    # EXPIRED (expiry is derived — nothing was written).
    message_at = T0 + EIGHT_HOURS + timedelta(minutes=30)
    clock.advance(EIGHT_HOURS + timedelta(minutes=30))
    assert await service.status("C1/T1") is InteractionStatus.EXPIRED

    await service.record_message("C1/T1", "user", message_at)

    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.last_message_emissor == "user"
    # SQLite round-trips drop tzinfo — compare in the service's UTC domain.
    assert loaded.last_message_timestamp == message_at.replace(tzinfo=None)
    # The 8h window re-anchors at the message instant (MAS §9.4) → ACTIVE again.
    assert loaded.expires_at == (message_at + EIGHT_HOURS).replace(tzinfo=None)
    assert await service.status("C1/T1") is InteractionStatus.ACTIVE
    events = bus.messages_of(UserInteractionUpdated)
    assert [event.state for event in events] == ["ACTIVE", "ACTIVE"]


async def test_record_message_unknown_reference_fails_closed(
    service: InteractionService,
    bus: FakeBus,
) -> None:
    with pytest.raises(LookupError):
        await service.record_message("C404/T404", "user", T0)
    bus.assert_published(UserInteractionUpdated, times=0)


async def test_record_message_accepts_completed_interaction_window_reset_only(
    service: InteractionService,
    session_factory: SessionFactory,
) -> None:
    """A message write resets the window but NEVER unsets a persisted completion."""
    await service.create("C1/T1", question="Deploy staging?")
    await service.complete("C1/T1")
    message_at = T0 + timedelta(hours=1)

    await service.record_message("C1/T1", "agent", message_at)

    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.completed_at is not None  # terminal immutability intact
    assert loaded.last_message_emissor == "agent"
    assert loaded.expires_at == (message_at + EIGHT_HOURS).replace(tzinfo=None)


# --- status(): the derived read-only view --------------------------------------


async def test_status_reads_all_three_derived_states(
    service: InteractionService,
    clock: FakeClock,
) -> None:
    # ACTIVE right after creation.
    await service.create("C1/T1")
    assert await service.status("C1/T1") is InteractionStatus.ACTIVE

    # EXPIRED once the injected clock passes expires_at (no write, no column).
    clock.advance(EIGHT_HOURS + timedelta(seconds=1))
    assert await service.status("C1/T1") is InteractionStatus.EXPIRED

    # COMPLETED after the terminal write (a fresh, un-expired interaction).
    await service.create("C2/T2")
    await service.complete("C2/T2")
    assert await service.status("C2/T2") is InteractionStatus.COMPLETED


async def test_status_unknown_reference_raises_lookup_error(
    service: InteractionService,
) -> None:
    with pytest.raises(LookupError):
        await service.status("C404/T404")


async def test_status_boundary_is_active(
    service: InteractionService,
    clock: FakeClock,
) -> None:
    await service.create("C1/T1")
    clock.advance(EIGHT_HOURS)  # exactly at the deadline: still ACTIVE
    assert await service.status("C1/T1") is InteractionStatus.ACTIVE


# --- Command handlers (registry delegation; no Slack I/O) ----------------------


def test_handlers_resolve_in_default_registry() -> None:
    """The exact module callables are the ones the registry dispatches to."""
    registry = get_default_registry()
    assert registry.resolve(RequestUserInput) is handle_request_user_input
    assert registry.resolve(NotifyUser) is handle_notify_user


async def test_request_user_input_handler_creates_interaction(
    bound_service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    envelope = _command_envelope(
        CommandType.REQUEST_USER_INPUT,
        RequestUserInput(session_id="C1/T1", prompt="Approve the PR?"),
    )
    await bus.publish(envelope)  # dispatches through the default registry

    loaded = _load_one(session_factory, "C1/T1")
    assert loaded is not None
    assert loaded.type == "user_input_request"
    assert loaded.question == "Approve the PR?"
    assert loaded.response_required is True
    assert loaded.origin == "outbound"
    bus.assert_published(UserInteractionUpdated, times=1)
    event = bus.messages_of(UserInteractionUpdated)[0]
    assert event.session_id == "C1/T1"
    assert event.state == "ACTIVE"


async def test_notify_user_handler_creates_notification_interaction(
    bound_service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    envelope = _command_envelope(
        CommandType.NOTIFY_USER,
        NotifyUser(session_id="C2/T2", message="PR #42 opened"),
    )
    await bus.publish(envelope)

    loaded = _load_one(session_factory, "C2/T2")
    assert loaded is not None
    assert loaded.type == "notification"
    assert loaded.question == "PR #42 opened"
    assert loaded.response_required is False
    bus.assert_published(UserInteractionUpdated, times=1)


async def test_handlers_delegate_idempotently(
    bound_service: InteractionService,
    session_factory: SessionFactory,
    bus: FakeBus,
) -> None:
    """Two commands for the same thread find-or-create ONE interaction."""
    envelope = _command_envelope(
        CommandType.REQUEST_USER_INPUT,
        RequestUserInput(session_id="C1/T1", prompt="Approve the PR?"),
    )
    await bus.publish(envelope)
    await bus.publish(envelope)

    assert _count_rows(session_factory) == 1
    bus.assert_published(UserInteractionUpdated, times=1)


async def test_unwired_handler_raises_runtime_error(bus: FakeBus) -> None:
    """No bound service (composition root not run) is a pinned hard failure."""
    with pytest.raises(RuntimeError, match="set_interaction_service"):
        await bus.publish(
            _command_envelope(
                CommandType.REQUEST_USER_INPUT,
                RequestUserInput(session_id="C1/T1", prompt="Approve?"),
            )
        )


# --- Event envelope factory (SFP-124 discipline) -------------------------------


def test_envelope_factory_derives_deterministic_idempotency_key() -> None:
    event = UserInteractionUpdated(session_id="C1/T1", state="ACTIVE")
    first = make_user_interaction_updated_envelope(event)
    second = make_user_interaction_updated_envelope(event)

    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key == "user-interaction:C1/T1:ACTIVE"
    assert (
        first.idempotency_key
        != make_user_interaction_updated_envelope(
            UserInteractionUpdated(session_id="C1/T1", state="COMPLETED")
        ).idempotency_key
    )


def test_envelope_factory_stamps_event_type_and_producer() -> None:
    envelope = make_user_interaction_updated_envelope(
        UserInteractionUpdated(session_id="C1/T1", state="COMPLETED")
    )
    assert envelope.event_type is EventType.USER_INTERACTION_UPDATED
    assert envelope.producer == "communication"
    assert envelope.payload.session_id == "C1/T1"
    assert envelope.payload.state == "COMPLETED"


# --- session_scope unit-of-work helper ------------------------------------------


def test_session_scope_rolls_back_on_error(engine: sa.Engine) -> None:
    """A raising body rolls the unit of work back — nothing persists."""
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with pytest.raises(RuntimeError, match="boom"):
        with session_scope(maker) as session:
            session.add(_make_interaction(provider_reference="C9/T9"))
            raise RuntimeError("boom")

    with session_scope(maker) as session:
        assert session.scalars(select(UserInteraction)).first() is None
