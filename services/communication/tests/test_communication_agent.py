"""Tests for the Communication Agent (SFP-134, MAS §9.4).

Covers the PRSpec acceptance criteria end-to-end, deterministically (MAS
§12.7 — no wall clock, no network, no sleeps):

- **Contracts** — ``InteractionSummary`` and ``ConversationContext`` are
  pydantic models validated in tests (contract conformance; frozen,
  ``extra='forbid'``).
- **summarize()** — the open path produces a contract-valid
  ``InteractionSummary`` (identity/emissor/timestamp attached as
  deterministic plumbing, summary from the runtime) and persists ONLY
  through the injected InteractionService port
  (``update_summary(provider_reference, summary)`` — asserted called);
  the prompt resolves through the ``PromptBuilder`` seam (injected builder
  and the shipped default fragments).
- **Closed interactions** — COMPLETED and EXPIRED states yield the typed
  ``ClosedInteractionOutcome`` (new-interaction request carrying the prior
  summary as contextual history) with ZERO mutations: no runtime run, no
  prompt resolution, no service call.
- **Fail-closed** — run raised, ``success=False``, ``None``/non-mapping
  output, contract-invalid summary, or an unparseable message ``ts`` each
  raise ``SummarizationError`` with NO persistence attempted.
- **reconstruct_context()** — pure assembly from exactly the four defined
  sources, asserted field by field (thread reply AND top-level message,
  whose thread root is its own ``ts``); no runtime run, no service call.
- **Import surface** — ``communication_agent.py`` imports no ORM/Session
  symbol (``sqlalchemy`` / ``Session`` / ``select`` / ``UserInteraction``)
  and no concrete runtime (no ``workspace_worker``/vendor module); the
  runtime seam is the ``sfp_agent_runtime.interfaces`` Protocol.

Persistence seam note (forward-looking, surfaced in the module docstring):
the concrete SFP-129 ``InteractionService`` exposes ``create`` / ``complete``
/ ``status`` only — the summary-update method the PRSpec requires the agent
to delegate to lands with the SFP-135 alignment. The fake below pins the
expected surface; the agent itself holds no ORM/Session dependency.
"""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from communication.application import communication_agent as agent_module
from communication.application.communication_agent import (
    ClosedInteractionOutcome,
    CommunicationAgent,
    ConversationContext,
    InteractionState,
    InteractionSummary,
    InteractionSummaryWriter,
    SummarizationError,
)
from communication.application.interaction_service import InteractionStatus
from communication.interfaces.slack_inbound import SlackProviderMessage
from pydantic import BaseModel, ValidationError
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult, AgentRuntime
from sfp_agent_runtime.prompt_builder import PromptBuilder

# --- Deterministic fixtures (MAS §12.7 — literals, never now()) ----------------

#: The pinned interaction identity and clock anchors for every test.
INTERACTION_ID = UUID("7e0c5b2a-9d4f-4a7b-8d1e-2f6a4c9b3e10")
T0 = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
RECONSTRUCTED_AT = T0 + timedelta(hours=1)

#: The current inbound message's Slack ``ts`` and the instant it denotes.
MESSAGE_TS = "1757428800.000123"
MESSAGE_TIMESTAMP = datetime.fromtimestamp(float(MESSAGE_TS), tz=UTC)

#: The summary the fake runtime "produces".
RUNTIME_SUMMARY = "Deploy finished; user asked to re-run the checks."

#: The thread root this interaction maps to (v0: one thread = one interaction).
THREAD_ROOT = "1757428800.000100"


class FakeRuntime:
    """Deterministic ``AgentRuntime`` stand-in: records requests, replays a result.

    Satisfies the :class:`~sfp_agent_runtime.interfaces.AgentRuntime` Protocol
    structurally (asserted below) — the agent's only path to model execution.
    """

    def __init__(
        self,
        result: AgentRunResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[AgentRunRequest] = []
        self._result = result
        self._error = error

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakeInteractionService:
    """Fake InteractionService-side port: records the delegated summary write.

    Pins the surface the agent is allowed to call (the
    :class:`InteractionSummaryWriter` port). Everything else — ``create`` /
    ``complete`` / ``status`` — is deliberately absent: the agent must not
    need it to summarize.
    """

    def __init__(self) -> None:
        self.update_summary_calls: list[tuple[str, str]] = []

    async def update_summary(self, provider_reference: str, summary: str) -> None:
        self.update_summary_calls.append((provider_reference, summary))


class RecordingPromptBuilder(PromptBuilder):
    """A real ``PromptBuilder`` that records every ``get_prompt`` call."""

    def __init__(self, base_dir: str | Path) -> None:
        super().__init__(base_dir)
        self.calls: list[tuple[str, str]] = []

    def get_prompt(self, agent: str, task: str) -> str:
        self.calls.append((agent, task))
        return super().get_prompt(agent, task)


def make_state(status: InteractionStatus = InteractionStatus.ACTIVE) -> InteractionState:
    """The prior interaction state every test runs against."""
    return InteractionState(
        provider_reference=THREAD_ROOT,
        interaction_id=INTERACTION_ID,
        summary="Prior summary: deploy question open.",
        last_message_emissor="agent",
        last_message_timestamp=T0,
        status=status,
    )


def make_message(**overrides: Any) -> SlackProviderMessage:
    """The current inbound thread-reply message, with per-test overrides."""
    fields: dict[str, Any] = {
        "type": "message",
        "text": "The staging deploy finished; please re-run the checks.",
        "channel": "C062QABCD",
        "ts": MESSAGE_TS,
        "user": "U04TMLYYY",
        "thread_ts": THREAD_ROOT,
    }
    fields.update(overrides)
    return SlackProviderMessage(**fields)


def make_runtime_result(
    output: Any, *, success: bool = True, error: str | None = None
) -> AgentRunResult:
    """A run result with the given output envelope."""
    return AgentRunResult(
        agent="communication",
        ticket_id=str(INTERACTION_ID),
        success=success,
        output=output,
        error=error,
    )


def make_agent(
    runtime: FakeRuntime,
    service: FakeInteractionService,
    prompt_builder: PromptBuilder | None = None,
) -> CommunicationAgent:
    """Wire the agent with the fake seams."""
    return CommunicationAgent(
        runtime=runtime,
        interactions=service,
        prompt_builder=prompt_builder,
    )


@pytest.fixture
def runtime() -> FakeRuntime:
    return FakeRuntime(result=make_runtime_result({"summary": RUNTIME_SUMMARY}))


@pytest.fixture
def service() -> FakeInteractionService:
    return FakeInteractionService()


@pytest.fixture
def builder() -> RecordingPromptBuilder:
    return RecordingPromptBuilder(agent_module._DEFAULT_PROMPT_DIR)


# --- Acceptance 1: summarize() produces a contract-valid InteractionSummary ----


async def test_summarize_open_interaction_produces_valid_summary_and_persists_via_service(
    runtime: FakeRuntime, service: FakeInteractionService, builder: RecordingPromptBuilder
) -> None:
    agent = make_agent(runtime, service, builder)
    result = await agent.summarize(make_state(), make_message())

    # Contract conformance: a pydantic InteractionSummary, round-trip valid.
    assert isinstance(result, InteractionSummary)
    assert isinstance(result, BaseModel)
    assert InteractionSummary.model_validate(result.model_dump()) == result

    # Field-level: identity/emissor/timestamp are deterministic plumbing.
    assert result.interaction_id == str(INTERACTION_ID)
    assert result.summary == RUNTIME_SUMMARY
    assert result.emissor == "user"
    assert result.message_timestamp == MESSAGE_TIMESTAMP
    assert result.message_timestamp.tzinfo is not None

    # Persistence happens ONLY through the injected service, exactly once.
    assert service.update_summary_calls == [(THREAD_ROOT, RUNTIME_SUMMARY)]

    # The run went through the injected runtime with the composed context.
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.agent == "communication"
    assert request.ticket_id == str(INTERACTION_ID)
    assert request.context["prior_summary"] == "Prior summary: deploy question open."
    assert (
        request.context["current_message"]
        == "The staging deploy finished; please re-run the checks."
    )
    assert request.context["last_message_emissor"] == "agent"
    assert request.context["interaction_id"] == str(INTERACTION_ID)
    assert request.context["last_message_timestamp"] == T0.isoformat()

    # The prompt resolved through the PromptBuilder seam (ID-059).
    assert builder.calls == [("communication", "summarize_interaction")]
    assert request.prompt.endswith("\n")

    # The fakes satisfy the Protocols structurally — the seams are duck-typed.
    assert isinstance(runtime, AgentRuntime)
    assert isinstance(service, InteractionSummaryWriter)


async def test_summarize_resolves_default_prompt_from_shipped_fragments(
    runtime: FakeRuntime, service: FakeInteractionService
) -> None:
    """prompt_builder=None falls back to the shipped fragment dir (ID-059)."""
    agent = make_agent(runtime, service, prompt_builder=None)
    result = await agent.summarize(make_state(), make_message())

    assert isinstance(result, InteractionSummary)
    assert len(runtime.requests) == 1
    prompt = runtime.requests[0].prompt
    assert "# Communication Agent" in prompt  # role fragment
    assert "# Summarize interaction" in prompt  # task fragment


# --- Acceptance 4: closed interactions — typed outcome, ZERO mutations --------


@pytest.mark.parametrize("status", [InteractionStatus.COMPLETED, InteractionStatus.EXPIRED])
async def test_closed_interaction_yields_outcome_with_zero_mutations(
    status: InteractionStatus,
    runtime: FakeRuntime,
    service: FakeInteractionService,
    builder: RecordingPromptBuilder,
) -> None:
    agent = make_agent(runtime, service, builder)
    result = await agent.summarize(make_state(status), make_message())

    assert isinstance(result, ClosedInteractionOutcome)
    # The new-interaction request carries the previous identity and summary
    # as contextual history (MAS §9.4 Closed Interactions).
    assert result.interaction_id == str(INTERACTION_ID)
    assert result.status is status
    assert result.prior_summary == "Prior summary: deploy question open."

    # ZERO mutations: no run, no prompt resolution, no service call.
    assert runtime.requests == []
    assert builder.calls == []
    assert service.update_summary_calls == []


# --- Acceptance 6: fail-closed — no summary, no persistence --------------------


async def test_fail_closed_on_runtime_failure(
    service: FakeInteractionService, builder: RecordingPromptBuilder
) -> None:
    runtime = FakeRuntime(result=make_runtime_result(None, success=False, error="rate limited"))
    agent = make_agent(runtime, service, builder)

    with pytest.raises(SummarizationError, match="rate limited"):
        await agent.summarize(make_state(), make_message())

    assert service.update_summary_calls == []
    assert len(runtime.requests) == 1  # the run WAS attempted; persistence was not


async def test_fail_closed_on_runtime_raise(service: FakeInteractionService) -> None:
    runtime = FakeRuntime(error=RuntimeError("boom"))
    agent = make_agent(runtime, service)

    with pytest.raises(SummarizationError, match="boom"):
        await agent.summarize(make_state(), make_message())

    assert service.update_summary_calls == []


async def test_fail_closed_on_missing_output(service: FakeInteractionService) -> None:
    runtime = FakeRuntime(result=make_runtime_result(None))
    agent = make_agent(runtime, service)

    with pytest.raises(SummarizationError, match="no structured output"):
        await agent.summarize(make_state(), make_message())

    assert service.update_summary_calls == []


@pytest.mark.parametrize(
    "output",
    [
        {"summary": ""},  # empty summary fails min_length=1
        {},  # summary key absent
        {"summary": 123},  # not a string
        ["not", "a", "mapping"],  # not a mapping at all
    ],
)
async def test_fail_closed_on_invalid_output(output: Any, service: FakeInteractionService) -> None:
    runtime = FakeRuntime(result=make_runtime_result(output))
    agent = make_agent(runtime, service)

    with pytest.raises(SummarizationError, match="output invalid|no structured output"):
        await agent.summarize(make_state(), make_message())

    assert service.update_summary_calls == []


async def test_fail_closed_on_unparseable_message_ts(service: FakeInteractionService) -> None:
    runtime = FakeRuntime(result=make_runtime_result({"summary": RUNTIME_SUMMARY}))
    agent = make_agent(runtime, service)
    message = make_message(ts="not-a-number")

    with pytest.raises(SummarizationError, match="unparseable Slack message ts"):
        await agent.summarize(make_state(), message)

    assert service.update_summary_calls == []


# --- Acceptance 2: reconstruct_context() — four sources, field by field --------


def test_reconstruct_context_thread_reply_assembles_the_four_sources(
    runtime: FakeRuntime, service: FakeInteractionService
) -> None:
    agent = make_agent(runtime, service)
    context = agent.reconstruct_context(
        make_state(), make_message(), reconstructed_at=RECONSTRUCTED_AT
    )

    assert isinstance(context, ConversationContext)
    assert isinstance(context, BaseModel)
    assert ConversationContext.model_validate(context.model_dump()) == context

    assert context.interaction_id == str(INTERACTION_ID)  # interaction metadata
    assert context.prior_summary == "Prior summary: deploy question open."  # prior summary
    assert context.current_message == (  # current inbound message
        "The staging deploy finished; please re-run the checks."
    )
    assert context.provider_context == {  # provider context (flattened fields)
        "channel": "C062QABCD",
        "thread_ts": THREAD_ROOT,
        "user_id": "U04TMLYYY",
    }
    assert context.interaction_status == "ACTIVE"
    assert context.reconstructed_at == RECONSTRUCTED_AT

    # Pure assembly: no model run, no persistence.
    assert runtime.requests == []
    assert service.update_summary_calls == []


def test_reconstruct_context_top_level_message_uses_own_ts_as_thread_root(
    runtime: FakeRuntime, service: FakeInteractionService
) -> None:
    agent = make_agent(runtime, service)
    message = make_message(thread_ts=None)

    context = agent.reconstruct_context(make_state(), message, reconstructed_at=RECONSTRUCTED_AT)

    assert context.provider_context["thread_ts"] == MESSAGE_TS


# --- Contract conformance details ----------------------------------------------


def test_contracts_are_frozen_and_reject_unknown_fields() -> None:
    summary = InteractionSummary(
        interaction_id=str(INTERACTION_ID),
        summary=RUNTIME_SUMMARY,
        emissor="user",
        message_timestamp=MESSAGE_TIMESTAMP,
    )
    with pytest.raises(ValidationError):
        summary.summary = "rewritten"  # type: ignore[misc]  # frozen
    with pytest.raises(ValidationError):
        InteractionSummary.model_validate(
            {
                "interaction_id": str(INTERACTION_ID),
                "summary": RUNTIME_SUMMARY,
                "emissor": "user",
                "message_timestamp": MESSAGE_TIMESTAMP,
                "unknown": "field",
            }
        )


# --- Acceptance 3 & 5: import surface (static) ---------------------------------


def test_module_imports_no_orm_session_symbol_and_no_concrete_runtime() -> None:
    """Static import check over communication_agent.py's own imports."""
    source = Path(agent_module.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            imported_names.update(alias.name for alias in node.names)

    # No ORM / Session symbol — the agent holds no DB dependency.
    assert "sqlalchemy" not in imported_modules
    assert not (imported_names & {"Session", "select", "UserInteraction", "Base"})

    # No concrete runtime — the seam is the Protocol from sfp-agent-runtime.
    assert all(not module.startswith("workspace_worker") for module in imported_modules)
    assert all(not module.startswith(("anthropic", "openai")) for module in imported_modules)
    assert "sfp_agent_runtime.interfaces" in imported_modules
    assert "AgentRuntime" in imported_names


def test_fakes_are_imported_from_the_landed_surface() -> None:
    """The vocabulary the agent speaks is the landed one (no drift)."""
    assert agent_module._DEFAULT_PROMPT_DIR.is_dir()  # shipped fragments exist
    assert {status.value for status in InteractionStatus} == {"ACTIVE", "COMPLETED", "EXPIRED"}
    assert sys.modules["communication.application.communication_agent"] is agent_module
