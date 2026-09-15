"""Tests for the CONFIRM state machine (SFP-135, ID-069 as amended, MAS §9.4).

Covers the PRSpec acceptance criteria end-to-end, deterministically (MAS
§12.7 — no wall clock, no network, no sleeps):

- **Multilingual, case-insensitive CONFIRM gate** — parametrized: the
  curated confirmation words (EN + ES/CA/FR/DE) confirm in any casing after
  trimming surrounding whitespace/punctuation/emoji (the 2026-09-15 ID-069
  amendment); everything else — sentences, corrections — is an ADJUST input
  that regenerates the summary through the SAME injected agent (one runtime
  run), persists it, and requests re-confirmation — no publish, no
  completion.
- **Classifier hook** — the general-path ``confirmation_intent_classifier``
  callable decides the replies the curated list misses; absent (default),
  they are corrections.
- **LLM intent fallback** — a run whose ``is_confirmation_intent`` output
  field is true confirms the freshly regenerated summary (the LLM is the
  fallback behind the curated list, never the only path).
- **Interaction-level confirmation** — ``confirm_interaction`` (the Block
  Kit button path, SFP-258) publishes the SAME ``UserInputReceived`` shape
  as a typed confirmation and completes the interaction; on a closed
  interaction it returns the closed outcome with zero mutations.
- **Confirm path** — a confirmation publishes ONE ``UserInputReceived``
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


# --- The multilingual, case-insensitive CONFIRM gate (ID-069 as amended,
# --- SFP-258; the curated word list is the deterministic fast path) ------------


@pytest.mark.parametrize(
    ("reply_text", "confirms"),
    [
        ("CONFIRM", True),
        ("confirm", True),  # case-insensitive (the 2026-09-15 amendment)
        ("Confirm", True),  # case-insensitive
        ("  CONFIRM  ", True),  # surrounding whitespace only — still a confirmation
        ("Confirm!", True),  # surrounding punctuation trimmed
        # Curated ES / CA / FR / DE confirmation words (case-insensitive):
        ("confirmo", True),
        ("CONFIRMAR", True),
        ("vale", True),
        ("D'acord", True),
        ("CONFIRMAT", True),
        ("confirmez", True),
        ("OUI", True),
        ("Bestätigen", True),
        ("bestätigt", True),
        ("JA", True),
        # NOT confirmations — adjust input, whatever their positivity:
        ("CONFIRM NOW", False),  # no substring/prefix match: a correction
        ("yes please, but add the rollback plan", False),  # free-text adjustment
        ("Looks good, ship it", False),  # free-text correction
        ("confirmado el despliegue ayer", False),  # a sentence, not a word
    ],
)
async def test_confirm_gate_is_multilingual_and_case_insensitive(
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


# --- SFP-258: the general-path hook, the LLM fallback, the button path -------


@pytest.mark.parametrize("reply_text", ["confirmaré el desplegament", "sounds right to me"])
async def test_classifier_hook_confirms_what_the_curated_list_misses(
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
    runtime: FakeRuntime,
    agent: CommunicationAgent,
    reply_text: str,
) -> None:
    """The injected classifier is the general path — consulted ONLY after
    the curated list misses, and only on the whole message."""
    await _pending(service)
    seen: list[str] = []

    def classifier(text: str) -> bool:
        seen.append(text)
        return text == "sounds right to me"

    flow = ConfirmFlow(
        bus=bus,
        interactions=service,
        agent=agent,
        session_factory=session_factory,
        clock=clock,
        confirmation_intent_classifier=classifier,
    )

    outcome = await flow.handle(_reply(reply_text))

    assert seen == [reply_text]  # the hook only ever sees the exact text
    if reply_text == "sounds right to me":
        assert isinstance(outcome, ConfirmOutcome)
        assert runtime.requests == []  # hook hits: no regeneration
    else:
        assert isinstance(outcome, CorrectionOutcome)
        assert len(runtime.requests) == 1


async def test_llm_intent_fallback_confirms_the_regenerated_summary(
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
    runtime: FakeRuntime,
    agent: CommunicationAgent,
) -> None:
    """A message the curated list misses but the run classifies as a
    confirmation intent confirms the FRESH summary (LLM = fallback)."""
    reference = await _pending(service)
    runtime._result = AgentRunResult(  # noqa: SLF001 - test-only seam
        agent="communication",
        ticket_id=str(INTERACTION_ID),
        success=True,
        output={"summary": REGENERATED_SUMMARY, "is_confirmation_intent": True},
    )
    flow = ConfirmFlow(
        bus=bus,
        interactions=service,
        agent=agent,
        session_factory=session_factory,
        clock=clock,
    )

    outcome = await flow.handle(_reply("tot correcte, endavant"))

    assert isinstance(outcome, ConfirmOutcome)
    assert outcome.confirmed_summary == REGENERATED_SUMMARY
    event = bus.messages_of(UserInputReceived)[-1]
    assert event.session_id == reference
    assert event.text == REGENERATED_SUMMARY
    loaded = _load_one(session_factory, reference)
    assert loaded is not None and loaded.completed_at is not None


async def test_confirm_interaction_is_equivalent_to_typed_confirmation(
    flow: ConfirmFlow,
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
) -> None:
    """The Block Kit button path publishes the SAME payload shape as the
    typed path (SFP-258 acceptance: click ≡ typed confirmation)."""
    await _pending(service)

    typed = await flow.handle(_reply("CONFIRM"))

    # A fresh pending interaction on a SECOND thread for the click path.
    second = "1757428800.000900"
    await service.create(
        second,
        interaction_type="user_input_request",
        question="Deploy staging?",
        response_required=True,
    )
    await service.update_summary(second, PENDING_SUMMARY)

    clicked = await flow.confirm_interaction(second)

    assert isinstance(typed, ConfirmOutcome) and isinstance(clicked, ConfirmOutcome)
    assert typed.confirmed_summary == clicked.confirmed_summary == PENDING_SUMMARY
    # Same summary, same payload shape — different interactions, so
    # different ids (UUIDv4, non-deterministic by design).
    assert typed.interaction_id != clicked.interaction_id
    clicks = bus.messages_of(UserInputReceived)
    assert len(clicks) == 2
    for event in clicks:
        assert event.text == PENDING_SUMMARY
    assert clicks[0].session_id == THREAD_ROOT
    assert clicks[1].session_id == second
    assert await service.status(THREAD_ROOT) is InteractionStatus.COMPLETED
    assert await service.status(second) is InteractionStatus.COMPLETED


@pytest.mark.parametrize("closer", ["complete", "expire"])
async def test_confirm_interaction_on_a_closed_interaction_is_inert(
    closer: str,
    service: InteractionService,
    bus: FakeBus,
    session_factory: SessionFactory,
    clock: FakeClock,
    runtime: FakeRuntime,
    agent: CommunicationAgent,
) -> None:
    """A late button click on a completed/expired interaction: the closed
    outcome, ZERO mutations, ZERO publishes."""
    reference = await _pending(service)
    flow = ConfirmFlow(
        bus=bus,
        interactions=service,
        agent=agent,
        session_factory=session_factory,
        clock=clock,
    )
    if closer == "complete":
        await service.complete(reference)
    else:
        clock.advance(EIGHT_HOURS + timedelta(seconds=1))

    outcome = await flow.confirm_interaction(reference)

    assert isinstance(outcome, ClosedInteractionOutcome)
    assert outcome.prior_summary == PENDING_SUMMARY
    # No UserInputReceived beyond the create/update lifecycle events.
    assert bus.messages_of(UserInputReceived) == []
    assert runtime.requests == []


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
