"""Tests for the CONFIRM state machine (SFP-135, ID-069, MAS §9.4).

Covers the PRSpec acceptance criteria end-to-end, deterministically (MAS
§12.7 — no wall clock, no network, no sleeps):

- **Exact-literal CONFIRM gate** — parametrized: surrounding whitespace is
  stripped, everything else (``confirm`` / ``Confirm`` / ``CONFIRM NOW`` /
  free-text corrections) is a CORRECTION that regenerates the summary
  through the SAME injected agent (one runtime run), persists it, and
  requests re-confirmation — no publish, no completion.
- **Confirm path** — the exact literal publishes ONE ``UserInputReceived``
  carrying the confirmed summary text and completes the interaction
  (``completed_at`` set, ``UserInteractionUpdated`` COMPLETED).
- **Closed interactions** — COMPLETED and EXPIRED both yield the SFP-134
  ``ClosedInteractionOutcome`` with ZERO mutations (no run, no publish, no
  write) — closed stays closed.
- **No UserDecision** — the flow introduces and persists no ``UserDecision``
  symbol anywhere (Orchestrator-owned, MAS §6.3): module-import + source
  scan.
- **Module invariants** — ``InteractionStatus`` gains NO member
  (PENDING_CONFIRM is conversation state, not an enum value) and the new
  module reads no wall clock (source scan; the clock is REQUIRED injected).

Determinism: a fixed ``FakeClock`` (REQUIRED by the flow — no default), a
fixed in-memory SQLite database (StaticPool + ``ATTACH … AS business``), the
``FakeBus`` from sfp-testing, and a deterministic ``FakeRuntime`` for the
agent seam. The same inputs always yield the same outcome.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from communication.application import communication_agent as agent_module
from communication.application import confirm_flow as confirm_flow_module
from communication.application import interaction_service as interaction_service_module
from communication.application.communication_agent import (
    ClosedInteractionOutcome,
    CommunicationAgent,
    InteractionSummaryWriter,
)
from communication.application.confirm_flow import (
    ConfirmFlow,
    ConfirmOutcome,
    CorrectionOutcome,
)
from communication.application.interaction_service import (
    InteractionService,
    InteractionStatus,
    SessionFactory,
    session_scope,
)
from communication.infrastructure.persistence import Base, UserInteraction
from communication.interfaces.slack_inbound import SlackProviderMessage
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_contracts.events import UserInputReceived, UserInteractionUpdated
from sfp_testing import FakeBus
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# --- Deterministic fixtures (MAS §12.7 — literals, never now()) ----------------

#: The pinned start of every test's clock.
T0 = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

#: The AP-009 / MAS §9.4 inactivity window mirrored from the service constant.
EIGHT_HOURS = timedelta(hours=8)

#: The interaction under test — one Slack thread = one interaction (v0).
THREAD_ROOT = "1757428800.000100"
INTERACTION_ID = UUID("7e0c5b2a-9d4f-4a7b-8d1e-2f6a4c9b3e10")

#: The durable summary awaiting confirmation (the PENDING_CONFIRM state).
PENDING_SUMMARY = "User asked to deploy staging and confirmed the checklist."

#: The summary the fake runtime "produces" for a correction round.
REGENERATED_SUMMARY = "Deploy staged; user corrected: only the checklist applied."

#: The reply message's Slack ``ts`` — carried, never derived from a clock.
REPLY_TS = "1757460000.000456"


class FakeClock:
    """A controllable stand-in for the flow's REQUIRED clock seam."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


class FakeRuntime:
    """Deterministic ``AgentRuntime`` stand-in: records requests, replays one result."""

    def __init__(self, result: AgentRunResult) -> None:
        self.requests: list[AgentRunRequest] = []
        self._result = result

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        return self._result


class RecordingPromptBuilder(PromptBuilder):
    """A real ``PromptBuilder`` that records every ``get_prompt`` call."""

    def __init__(self, base_dir: str | Path) -> None:
        super().__init__(base_dir)
        self.calls: list[tuple[str, str]] = []

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return super().get_prompt(agent, task)


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
def service(
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> InteractionService:
    """The real InteractionService — the flow's write path AND the agent's port."""
    return InteractionService(bus=bus, session_factory=session_factory, clock=clock)


@pytest.fixture
def runtime() -> FakeRuntime:
    """The agent's runtime seam: replays one correction-round summary."""
    return FakeRuntime(
        AgentRunResult(
            agent="communication",
            ticket_id=str(INTERACTION_ID),
            success=True,
            output={"summary": REGENERATED_SUMMARY},
            error=None,
        )
    )


@pytest.fixture
def agent(runtime: FakeRuntime, service: InteractionService) -> CommunicationAgent:
    """The SAME agent seam the flow must regenerate through — wired to the real service."""
    return CommunicationAgent(
        runtime=runtime,
        interactions=service,
        prompt_builder=RecordingPromptBuilder(agent_module._DEFAULT_PROMPT_DIR),
    )


@pytest.fixture
def flow(
    bus: FakeBus,
    service: InteractionService,
    agent: CommunicationAgent,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> ConfirmFlow:
    """The flow under test — every seam injected, nothing real touched."""
    return ConfirmFlow(
        bus=bus,
        interactions=service,
        agent=agent,
        session_factory=session_factory,
        clock=clock,
    )


async def _pending(service: InteractionService) -> str:
    """Seed one ACTIVE interaction in the pending-confirmation state.

    ``create`` initializes the summary to the question; ``update_summary``
    (the SFP-135 method) replaces it with the summary awaiting the CONFIRM
    gate. Returns the provider reference every test replies to.
    """
    await service.create(
        THREAD_ROOT,
        interaction_type="user_input_request",
        question="Deploy staging?",
        response_required=True,
    )
    await service.update_summary(THREAD_ROOT, PENDING_SUMMARY)
    return THREAD_ROOT


def _reply(text: str) -> SlackProviderMessage:
    """An inbound thread reply routed to the flow."""
    return SlackProviderMessage(
        type="message",
        text=text,
        channel="C062QABCD",
        ts=REPLY_TS,
        user="U04TMLYYY",
        thread_ts=THREAD_ROOT,
    )


def _load_one(session_factory: SessionFactory, provider_reference: str) -> UserInteraction | None:
    """Load the single interaction for a reference through a FRESH session."""
    with session_factory() as session:
        stmt = select(UserInteraction).where(
            UserInteraction.provider_reference == provider_reference
        )
        return session.scalars(stmt).first()


# --- The exact-literal CONFIRM gate (ID-069) -----------------------------------


@pytest.mark.parametrize(
    ("reply_text", "confirms"),
    [
        ("CONFIRM", True),
        ("  CONFIRM  ", True),  # surrounding whitespace only — still the literal
        ("confirm", False),  # NO case-folding: a correction
        ("Confirm", False),  # NO case-folding: a correction
        ("CONFIRM NOW", False),  # no substring match: a correction
        ("Looks good, ship it", False),  # free-text correction
    ],
)
async def test_confirm_gate_is_exact_literal(
    flow: ConfirmFlow,
    service: InteractionService,
    runtime: FakeRuntime,
    reply_text: str,
    confirms: bool,
) -> None:
    await _pending(service)

    outcome = await flow.handle(_reply(reply_text))

    if confirms:
        assert isinstance(outcome, ConfirmOutcome)
        assert outcome.confirmed_summary == PENDING_SUMMARY
        assert outcome.interaction_id is not None
        # No regeneration on the confirm path: zero agent runs.
        assert runtime.requests == []
    else:
        assert isinstance(outcome, CorrectionOutcome)
        assert outcome.regenerated_summary == REGENERATED_SUMMARY
        # A correction regenerates through the SAME agent — exactly one run.
        assert len(runtime.requests) == 1


async def test_confirm_publishes_user_input_received_and_completes(
    flow: ConfirmFlow,
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
) -> None:
    reference = await _pending(service)

    outcome = await flow.handle(_reply("CONFIRM"))

    # The confirmed summary text rides the published event, enveloped.
    bus.assert_published(UserInputReceived, times=1)
    event = bus.messages_of(UserInputReceived)[0]
    assert event.session_id == reference
    assert event.text == PENDING_SUMMARY

    # The interaction is completed through InteractionService (AP-005).
    loaded = _load_one(session_factory, reference)
    assert loaded is not None
    assert loaded.completed_at is not None
    assert loaded.summary == PENDING_SUMMARY  # the confirmed summary stands
    assert await service.status(reference) is InteractionStatus.COMPLETED
    states = [message.state for message in bus.messages_of(UserInteractionUpdated)]
    assert states == ["ACTIVE", "ACTIVE", "COMPLETED"]  # create, update_summary, complete

    assert isinstance(outcome, ConfirmOutcome)
    assert outcome.confirmed_summary == PENDING_SUMMARY
    assert outcome.interaction_id == str(loaded.interaction_id)


async def test_correction_regenerates_persists_and_re_requests(
    flow: ConfirmFlow,
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
) -> None:
    reference = await _pending(service)

    outcome = await flow.handle(_reply("No — only the checklist part applied."))

    assert isinstance(outcome, CorrectionOutcome)
    assert outcome.regenerated_summary == REGENERATED_SUMMARY

    # The regenerated summary is persisted (AP-009 durable summary, no transcript).
    loaded = _load_one(session_factory, reference)
    assert loaded is not None
    assert loaded.summary == REGENERATED_SUMMARY
    assert loaded.completed_at is None  # NOT completed — confirmation re-requested
    assert await service.status(reference) is InteractionStatus.ACTIVE

    # A correction publishes NO UserInputReceived and completes nothing.
    bus.assert_published(UserInputReceived, times=0)
    assert all(message.state != "COMPLETED" for message in bus.messages_of(UserInteractionUpdated))


async def test_correction_uses_same_agent_not_a_second_llm_path(
    flow: ConfirmFlow,
    agent: CommunicationAgent,
    service: InteractionService,
    runtime: FakeRuntime,
) -> None:
    """The regeneration goes through the ONE injected agent seam."""
    assert flow._agent is agent  # noqa: SLF001 — the SAME injected instance
    await _pending(service)
    await flow.handle(_reply("please fix the summary"))
    assert len(runtime.requests) == 1
    context = runtime.requests[0].context
    assert context["prior_summary"] == PENDING_SUMMARY
    assert context["current_message"] == "please fix the summary"


# --- Closed interactions: zero mutations (MAS §9.4 / AP-005) --------------------


@pytest.mark.parametrize("closer", ["complete", "expire"])
async def test_closed_interaction_yields_closed_outcome_with_zero_mutations(
    flow: ConfirmFlow,
    service: InteractionService,
    runtime: FakeRuntime,
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
    closer: str,
) -> None:
    reference = await _pending(service)
    if closer == "complete":
        await service.complete(reference)
    else:
        clock.advance(EIGHT_HOURS + timedelta(seconds=1))
        assert await service.status(reference) is InteractionStatus.EXPIRED
    expected_summary = PENDING_SUMMARY
    published_before = bus.published_count(UserInteractionUpdated)

    outcome = await flow.handle(_reply("CONFIRM"))  # even the literal confirms nothing

    assert isinstance(outcome, ClosedInteractionOutcome)
    assert outcome.status is (
        InteractionStatus.COMPLETED if closer == "complete" else InteractionStatus.EXPIRED
    )
    assert outcome.prior_summary == expected_summary

    # ZERO mutations: no agent run, no publish, no write.
    assert runtime.requests == []
    bus.assert_published(UserInputReceived, times=0)
    assert bus.published_count(UserInteractionUpdated) == published_before
    loaded = _load_one(session_factory, reference)
    assert loaded is not None
    assert loaded.summary == expected_summary
    if closer == "complete":
        assert loaded.completed_at is not None
    else:
        assert loaded.completed_at is None


async def test_unknown_provider_reference_fails_closed(flow: ConfirmFlow) -> None:
    message = SlackProviderMessage(
        type="message",
        text="CONFIRM",
        channel="C062QABCD",
        ts="1759999999.000001",
        user="U04TMLYYY",
        thread_ts=None,  # a top-level message IS its own thread root
    )
    with pytest.raises(LookupError):
        await flow.handle(message)


# --- Module invariants ----------------------------------------------------------


def test_no_user_decision_symbol_introduced_or_persisted() -> None:
    """Communication persists NO UserDecision anywhere (Orchestrator-owned, MAS §6.3)."""
    assert not hasattr(confirm_flow_module, "UserDecision")
    # AST scan (docstrings excluded — this test's own prose must not trip it):
    # no identifier, import alias, or attribute in the module names the symbol.
    for module in (confirm_flow_module, interaction_service_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        identifiers = (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            | {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
                for alias in node.names
            }
        )
        assert "UserDecision" not in identifiers, f"found in {module.__name__}"


def test_interaction_status_enum_is_unchanged() -> None:
    """PENDING_CONFIRM is conversation state, NOT a new InteractionStatus member."""
    assert {status.name for status in InteractionStatus} == {
        "ACTIVE",
        "COMPLETED",
        "EXPIRED",
    }


def test_confirm_flow_module_has_no_wall_clock_read() -> None:
    """MAS §12.7: the new module carries or injects every timestamp — no clock read.

    AST scan (docstrings excluded): no call to ``datetime.now`` /
    ``datetime.utcnow`` / ``time.time`` exists anywhere in the module. The
    clock seam is a REQUIRED constructor argument instead.
    """
    tree = ast.parse(Path(confirm_flow_module.__file__).read_text(encoding="utf-8"))
    clock_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow", "time"}
    ]
    assert clock_reads == []


def test_interaction_service_satisfies_interaction_summary_writer(
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    """The landed runtime_checkable Protocol (SFP-134) accepts the service."""
    service = InteractionService(bus=bus, session_factory=session_factory, clock=clock)
    assert isinstance(service, InteractionSummaryWriter)
