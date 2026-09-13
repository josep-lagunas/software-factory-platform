"""The Communication Agent — summarization + context reconstruction (SFP-134).

Deterministic, dependency-injected plumbing over the vendor-neutral
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` seam (SFP-53, AP-010 /
MAS §9.6). The agent owns communication understanding for the
Communication Service (MAS §9.4 "Communication Agent"): interaction
summarization and communication context reconstruction. It EXECUTES
Communication policies (closed interactions stay closed, summaries are
durable) and never defines platform behaviour.

Grounded in:
- MAS §9.4 (Communication Agent) — context is reconstructed from the
  UserInteraction summary, the current incoming message, interaction
  metadata, and provider context. The agent is not session-based; it holds
  no conversation state of its own.
- MAS §9.4 (Summary / AP-009) — every ``UserInteraction`` maintains a
  durable summary; transcripts are never persisted. ``summarize`` produces
  that summary through the runtime; the agent never stores one itself.
- MAS §9.4 (Closed Interactions) — a message arriving on a COMPLETED or
  EXPIRED interaction yields the closed-interaction outcome: the
  interaction stays closed, the user is requested to start a new thread,
  the previous interaction identifier is provided as context reference,
  and the previous summary may serve as contextual history. ZERO mutations.
- MAS §12.7 (determinism) — no ambient wall-clock reads anywhere: output
  timestamps come from carried interaction metadata (the Slack message
  ``ts``) or are injected by the caller (``reconstructed_at``). The same
  inputs always yield the same outputs.
- ID-059 — no prompt text is inlined in source; the default prompt resolves
  via the landed :class:`~sfp_agent_runtime.prompt_builder.PromptBuilder`
  against :data:`_DEFAULT_PROMPT_DIR` (fragments ship with this ticket).
- SFP-51/SFP-53 — all model execution goes through the injected
  ``AgentRuntime`` Protocol; this module imports no concrete runtime.

Persistence seam (FORWARD-LOOKING — surfaced as a known limitation): the
PRSpec requires every lifecycle effect to be delegated to the injected
InteractionService while SFP-129 (landed, immutable here) exposes only
``create`` / ``complete`` / ``status`` — there is NO summary-update method
yet. The agent therefore depends on the narrow
:class:`InteractionSummaryWriter` port (house Protocol idiom) and delegates
the one write it needs — ``update_summary(provider_reference, summary)`` —
to the injected object. It never imports an ORM/Session symbol and never
writes a ``UserInteraction`` row itself. The concrete
:class:`~communication.application.interaction_service.InteractionService`
must grow this method (SFP-135 alignment) before production wiring; tests
pin the expected surface with a fake.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sfp_agent_runtime.interfaces import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntime,
)
from sfp_agent_runtime.prompt_builder import PromptBuilder

from communication.application.interaction_service import InteractionStatus
from communication.interfaces.slack_inbound import SlackProviderMessage

__all__ = [
    "ClosedInteractionOutcome",
    "CommunicationAgent",
    "ConversationContext",
    "InteractionState",
    "InteractionSummary",
    "InteractionSummaryWriter",
    "SummarizationError",
]

#: The agent role and task names resolving the summarize prompt via the
#: :class:`PromptBuilder` fragment layout (shared -> role -> task; ID-059):
#: ``prompts/communication.md`` and ``prompts/communication/summarize_interaction.md``.
_AGENT: Final[str] = "communication"
_TASK: Final[str] = "summarize_interaction"

#: Directory holding the default prompt fragments, colocated with the
#: service's other execution assets (the SFP-68 / planner layout). Exposed as
#: a module attribute so tests may redirect it without seeding real files.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: Who sent the message a summary was produced from — the landed
#: ``last_message_emissor`` vocabulary (MAS §9.4): an inbound message is from
#: the user (mirrors ``slack_inbound``'s ``_INBOUND_EMISSOR``).
_USER_EMISSOR: Final[str] = "user"

#: The terminal statuses that make an interaction CLOSED (MAS §9.4 /
#: AP-005): completed and expired interactions are immutable, never reopened.
_CLOSED_STATUSES: Final[frozenset[InteractionStatus]] = frozenset(
    {InteractionStatus.COMPLETED, InteractionStatus.EXPIRED}
)


class InteractionState(BaseModel):
    """The prior ``UserInteraction`` state ``summarize`` runs against.

    The typed, ORM-free snapshot of the interaction as the caller (the
    inbound wiring) read it — exactly the fields MAS §9.4 names for
    summarization (``interaction_id`` / ``summary`` /
    ``last_message_emissor`` / ``last_message_timestamp`` / status) plus
    ``provider_reference``, the natural key every InteractionService
    operation is keyed on. The agent never touches an ORM instance.

    Frozen: a state snapshot is a fact the agent reads, never rewrites.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    provider_reference: str = Field(min_length=1)
    interaction_id: UUID
    summary: str
    last_message_emissor: str
    last_message_timestamp: datetime
    status: InteractionStatus


class InteractionSummary(BaseModel):
    """The durable summary of one interaction (MAS §9.4 Summary / AP-009).

    The summarize output contract: what the runtime produced plus the
    plumbing identity the agent attaches deterministically. Never a
    transcript — the summary is the durable representation (AP-009).

    Attributes:
        interaction_id: The interaction the summary belongs to.
        summary: The non-empty summary text (the runtime's output).
        emissor: Who sent the message the summary was produced from (the
            landed ``user`` / ``agent`` vocabulary).
        message_timestamp: The timestamp of that message — parsed
            deterministically from the Slack message ``ts``, never a
            wall-clock read (MAS §12.7).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    interaction_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    emissor: str = Field(min_length=1)
    message_timestamp: datetime


class ConversationContext(BaseModel):
    """The reconstructed communication context (MAS §9.4 Communication Agent).

    Pure assembly from exactly four sources — prior summary, current inbound
    message, interaction metadata, provider context — suitable as structured
    prompt input for later agent runs (response generation / the SFP-135
    CONFIRM flow, both NOT built here).

    Attributes:
        interaction_id: The interaction the context belongs to.
        prior_summary: The interaction's durable summary so far.
        current_message: The current inbound message text.
        provider_context: Flattened ``SlackProviderMessage`` fields
            (``channel`` / ``thread_ts`` / ``user_id``) — the provider side
            of the context, already free of Slack HTTP concerns.
        interaction_status: The interaction's (derived) status.
        reconstructed_at: When the caller reconstructed the context —
            injected, never an ambient clock read (MAS §12.7).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    interaction_id: str = Field(min_length=1)
    prior_summary: str
    current_message: str = Field(min_length=1)
    provider_context: dict[str, str]
    interaction_status: str = Field(min_length=1)
    reconstructed_at: datetime


class ClosedInteractionOutcome(BaseModel):
    """The closed-interaction outcome (MAS §9.4 Closed Interactions).

    Returned — never raised, never persisted — when a message arrives on a
    COMPLETED or EXPIRED interaction. It is the typed new-interaction
    request on the agent's return path: the interaction stays closed, the
    user is requested to start a new thread, the previous interaction
    identifier is the context reference, and ``prior_summary`` carries the
    previous summary as contextual history for the new interaction. The
    wiring (SFP-135) decides how the request reaches the user.

    Frozen: an outcome is a fact about a closed interaction, not work state.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    interaction_id: str = Field(min_length=1)
    status: InteractionStatus
    prior_summary: str


@runtime_checkable
class InteractionSummaryWriter(Protocol):
    """Seam: the one lifecycle effect the agent delegates (SFP-129/SFP-135).

    The narrow port the agent needs from the injected InteractionService:
    persisting the updated durable summary. The agent holds no Session/ORM
    dependency and writes no ``UserInteraction`` rows itself — every
    lifecycle effect goes through the injected service object. NOTE: the
    concrete SFP-129 ``InteractionService`` does not implement this method
    yet (it lands with the SFP-135 alignment); the port pins the surface.
    """

    async def update_summary(self, provider_reference: str, summary: str) -> None:
        """Persist the updated durable summary for the interaction."""
        ...  # pragma: no cover


class SummarizationError(Exception):
    """Raised when summarization fails or yields a non-conformant output.

    Fail-closed sentinel: the run raised, returned ``success=False``,
    returned no structured output, produced output that fails
    :class:`InteractionSummary` validation, or carried an unparseable
    message timestamp. No :class:`InteractionSummary` is produced and NO
    persistence is attempted on any of these paths — the caller never sees
    a partial result.
    """


def _parse_slack_ts(ts: str) -> datetime:
    """Parse a Slack message ``ts`` into a timezone-aware UTC datetime.

    Slack's message identity is epoch seconds with microsecond precision
    (``"1757428800.000123"``); the numeric value IS the message's instant.
    Deterministic (MAS §12.7): a pure function of the carried ``ts``, no
    clock read.

    Raises:
        SummarizationError: The ``ts`` is not a parseable epoch value.
    """
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise SummarizationError(f"unparseable Slack message ts: {ts!r}") from exc


class CommunicationAgent:
    """Summarize interactions and reconstruct communication context (SFP-134).

    Constructor-injected seams (all Protocol-typed; no concrete runtime, no
    DB/ORM import anywhere in this module):

    - ``runtime`` — the vendor-neutral :class:`~sfp_agent_runtime.interfaces.\
AgentRuntime` Protocol (AP-010 / MAS §9.6). The ONLY path to model
      execution.
    - ``interactions`` — the InteractionService-side persistence port
      (:class:`InteractionSummaryWriter`). The ONLY path to lifecycle
      effects; the agent never writes rows itself.
    - ``prompt_builder`` — optional :class:`PromptBuilder`; when ``None``
      the default builder against :data:`_DEFAULT_PROMPT_DIR` resolves the
      prompt from the shipped fragments (ID-059).

    Both operations are deterministic (MAS §12.7): every timestamp is
    carried (the Slack ``ts``) or injected (``reconstructed_at``) — no
    ambient wall-clock read exists in this module.
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        interactions: InteractionSummaryWriter,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._runtime = runtime
        self._interactions = interactions
        self._prompt_builder = prompt_builder

    async def summarize(
        self, state: InteractionState, message: SlackProviderMessage
    ) -> InteractionSummary | ClosedInteractionOutcome:
        """Produce the updated durable summary for one interaction.

        Policy execution (MAS §9.4), in order:

        1. **Closed check** — a COMPLETED or EXPIRED interaction returns the
           :class:`ClosedInteractionOutcome` (new-interaction request, prior
           summary as contextual history) BEFORE any run: zero runtime
           calls, zero service calls, zero mutations.
        2. **Prompt + run** — resolve the summarize prompt (ID-059), build
           the run request from the prior state + current message, and
           execute through the injected runtime Protocol.
        3. **Validate (fail-closed)** — parse the run output into a
           contract-valid :class:`InteractionSummary`; any failure mode
           raises :class:`SummarizationError` with nothing persisted.
        4. **Persist via the service** — the single delegated lifecycle
           effect: ``update_summary(provider_reference, summary)`` on the
           injected service. The agent writes no row itself.

        Args:
            state: The prior interaction state (ORM-free snapshot).
            message: The current inbound Slack message.

        Returns:
            The validated :class:`InteractionSummary` on the open path, or
            the :class:`ClosedInteractionOutcome` for a closed interaction.

        Raises:
            SummarizationError: The run failed, returned no output, or the
                output failed contract validation — nothing persisted.
        """
        if state.status in _CLOSED_STATUSES:
            return ClosedInteractionOutcome(
                interaction_id=str(state.interaction_id),
                status=state.status,
                prior_summary=state.summary,
            )

        prompt = self._resolve_prompt()
        context: Mapping[str, Any] = {
            "interaction_id": str(state.interaction_id),
            "prior_summary": state.summary,
            "last_message_emissor": state.last_message_emissor,
            "last_message_timestamp": state.last_message_timestamp.isoformat(),
            "current_message": message.text,
        }
        request = AgentRunRequest(
            agent=_AGENT,
            ticket_id=str(state.interaction_id),
            prompt=prompt,
            context=context,
        )

        summary = self._run_and_validate(state, message, request)
        await self._interactions.update_summary(state.provider_reference, summary.summary)
        return summary

    def reconstruct_context(
        self,
        state: InteractionState,
        message: SlackProviderMessage,
        *,
        reconstructed_at: datetime,
    ) -> ConversationContext:
        """Reconstruct the communication context from exactly four sources.

        Pure assembly (MAS §9.4), no model run and no side effects: prior
        summary + current inbound message + interaction metadata + provider
        context — the ``SlackProviderMessage`` fields ``channel`` /
        ``thread_ts`` / ``user_id`` flattened into ``provider_context``
        (a top-level message contributes its own ``ts`` as the thread root,
        the same rule as the landed ``provider_reference`` property).

        Args:
            state: The prior interaction state (metadata + prior summary).
            message: The current inbound Slack message.
            reconstructed_at: The caller-injected reconstruction timestamp
                (MAS §12.7 — never read from the wall clock here).

        Returns:
            The contract-valid :class:`ConversationContext`, suitable as
            structured prompt input for later runs (SFP-135's to consume).
        """
        provider_context: dict[str, str] = {
            "channel": message.channel,
            "thread_ts": message.thread_ts if message.thread_ts is not None else message.ts,
            "user_id": message.user,
        }
        return ConversationContext(
            interaction_id=str(state.interaction_id),
            prior_summary=state.summary,
            current_message=message.text,
            provider_context=provider_context,
            interaction_status=state.status.value,
            reconstructed_at=reconstructed_at,
        )

    def _resolve_prompt(self) -> str:
        """Resolve the summarize prompt (ID-059: fragments on disk, not inline).

        The injected builder when one was given; otherwise the default
        :class:`PromptBuilder` against :data:`_DEFAULT_PROMPT_DIR`.
        """
        if self._prompt_builder is not None:
            return self._prompt_builder.get_prompt(_AGENT, _TASK)
        return PromptBuilder(_DEFAULT_PROMPT_DIR).get_prompt(_AGENT, _TASK)

    def _run_and_validate(
        self,
        state: InteractionState,
        message: SlackProviderMessage,
        request: AgentRunRequest,
    ) -> InteractionSummary:
        """Run the summarize request and validate its output (fail-closed).

        Returns the validated :class:`InteractionSummary` on success. On any
        failure mode — the run raised, returned ``success=False``, returned
        ``None`` or non-mapping output, produced an invalid summary, or
        carried an unparseable message ``ts`` — raises
        :class:`SummarizationError`. NOTHING is persisted on this path; the
        caller persists only after a validated summary exists.
        """
        try:
            result: AgentRunResult = self._runtime.run(request)
        except Exception as exc:  # noqa: BLE001 - fail-closed: catch broadly
            raise SummarizationError(f"summarize run raised: {type(exc).__name__}: {exc}") from exc

        if not result.success:
            error = result.error if result.error is not None else "unknown"
            raise SummarizationError(f"summarize run failed: {error}")

        output = result.output
        if output is None or not isinstance(output, Mapping):
            raise SummarizationError("summarize run returned no structured output")

        try:
            return InteractionSummary.model_validate(
                {
                    "interaction_id": str(state.interaction_id),
                    "summary": output.get("summary"),
                    "emissor": _USER_EMISSOR,
                    "message_timestamp": _parse_slack_ts(message.ts),
                }
            )
        except ValidationError as exc:
            raise SummarizationError(f"summarize output invalid: {exc}") from exc
