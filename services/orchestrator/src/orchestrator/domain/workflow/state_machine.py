"""The deterministic Ticket workflow state-machine engine (MAS §8.4–8.8, SFP-137).

Grounded in:
- MAS §8.4 — the 10 workflow states (re-exported from :mod:`.states`).
- MAS §8.5 — every significant transition produces an immutable
  :class:`WorkflowDecision` recording why, applied policy, facts, aggregate
  changes, commands emitted, previous state, resulting state.
- MAS §8.6 — the workflow advances only on events / user decisions; commands
  never modify workflow state.
- MAS §8.7 — outputs are deterministic; every workflow-affecting output is
  recorded in the decision that caused it.
- MAS §8.8 — failures are business facts producing a transition; the engine
  makes ``-> FAILED`` legal from the active states.
- ID-068 — REVIEW_IN_PROGRESS -> CODING_IN_PROGRESS (rework) is *normal*
  workflow progression and must be a legal move.
- ID-013 — enums serialize as plain strings in any serialized field.
- AP-011 — determinism: the core is a pure function with no I/O, clock, or
  randomness; identical inputs yield identical outputs.
- DOC#42 (landed) — the ``MessageBus`` interface; publishing happens in a thin
  wrapper *outside* the pure core.

Shape (per the PRSpec implementation notes):

- :data:`TRANSITIONS` is the explicit transition table as **data** — a
  module-level ``dict[WorkflowState, frozenset[WorkflowState]]``. Later tickets
  (SFP-121..124) reference/extend the table; they never hard-code moves.
- :func:`transition` is the pure core: it validates the move against the table
  and returns the resulting state, the :class:`WorkflowDecision`, and the
  outputs (commands/events) — without touching the bus, the clock, or any
  random source.
- :class:`WorkflowTransitionPublisher` is the thin bus-emitting wrapper: it
  calls the pure core, then publishes ``WorkflowUpdated`` through the injected
  bus. Publishing failures propagate; the core result is unaffected.

Seams exposed for later tickets (typed, deliberately unimplemented here):
- :class:`TransitionPolicy` (SFP-125..127) — decides which transition applies
  for incoming facts; the engine only *validates and records* transitions.
- :class:`TransitionDriver` (SFP-121..124) — per-stage driver that observes
  facts and requests moves through this engine.
- :class:`DecisionSink` (SFP-131) — persists decisions; the engine produces
  them, the sink stores them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    WorkflowState,
)

if TYPE_CHECKING:
    from sfp_contracts.events.envelope import EventEnvelope
    from sfp_messaging.bus import MessageBus


class IllegalTransitionError(Exception):
    """A requested workflow move is not in the transition table (MAS §8.4).

    The platform never performs implicit state transitions (MAS §8.2): any move
    outside :data:`TRANSITIONS` is an error, never a silent fallback. Carries
    the current state and the attempted target for observability.
    """

    def __init__(
        self,
        current_state: WorkflowState,
        attempted_target: WorkflowState,
    ) -> None:
        self.current_state = current_state
        self.attempted_target = attempted_target
        super().__init__(
            f"Illegal workflow transition: {current_state.name} -> {attempted_target.name}"
        )


def _build_transition_table() -> dict[WorkflowState, frozenset[WorkflowState]]:
    """Assemble the MAS §8.4–8.8 transition table.

    Structure per state, all taken from the pinned §8.4 stage order:

    - READY_FOR_PR_SPECIFICATION -> READY_FOR_CODING:
      planning completed, the ticket is ready for coding (§8.4 order).
    - READY_FOR_CODING -> CODING_IN_PROGRESS:
      coding is started by the Orchestrator (§8.3/§8.4).
    - CODING_IN_PROGRESS -> REVIEW_IN_PROGRESS:
      the Coder finished; review starts (§8.4 order).
    - REVIEW_IN_PROGRESS -> READY_FOR_MERGE (review approved)
      or -> CODING_IN_PROGRESS (ID-068 rework: CHANGES_REQUESTED is the normal
      coder<->reviewer quality loop, not a failure).
    - WAITING_FOR_USER: entered from the active states when a user decision is
      required (ID-069 CONFIRM flow; §8.9 users influence workflow only through
      UserDecision); exited by resuming the progression step the workflow was
      waiting on — i.e. back to any non-terminal, non-waiting stage (the
      resumed answer may approve, request rework, or answer a later-stage
      question). Resuming a review-stage question may itself result in rework
      (WAITING_FOR_USER -> CODING_IN_PROGRESS) or approval
      (WAITING_FOR_USER -> READY_FOR_MERGE), mirroring the two review outcomes.
    - READY_FOR_MERGE -> MERGING -> DEPLOYING -> COMPLETED:
      the delivery tail in §8.4 order (merge executes, deployment executes,
      workflow completes).
    - FAILED (§8.8): reachable from the active states (see
      :data:`~orchestrator.domain.workflow.states.ACTIVE_STATES` — every stage
      where work can observably fail, including an unrecoverable merge-queue
      failure at READY_FOR_MERGE per ID-068) and from WAITING_FOR_USER (a user
      may REJECT — ID-069 allowed decisions include REJECT — ending the
      workflow as failed). Not reachable from terminal states.
    - COMPLETED / FAILED: terminal (§8.4); no moves out.
    """
    table: dict[WorkflowState, set[WorkflowState]] = {
        WorkflowState.READY_FOR_PR_SPECIFICATION: {
            WorkflowState.READY_FOR_CODING,
        },
        WorkflowState.READY_FOR_CODING: {
            WorkflowState.CODING_IN_PROGRESS,
        },
        WorkflowState.CODING_IN_PROGRESS: {
            WorkflowState.REVIEW_IN_PROGRESS,
        },
        WorkflowState.REVIEW_IN_PROGRESS: {
            WorkflowState.READY_FOR_MERGE,
            # ID-068: rework is normal progression, never an error.
            WorkflowState.CODING_IN_PROGRESS,
        },
        WorkflowState.WAITING_FOR_USER: {
            WorkflowState.READY_FOR_PR_SPECIFICATION,
            WorkflowState.READY_FOR_CODING,
            WorkflowState.CODING_IN_PROGRESS,
            WorkflowState.REVIEW_IN_PROGRESS,
            WorkflowState.READY_FOR_MERGE,
            WorkflowState.MERGING,
            WorkflowState.DEPLOYING,
        },
        WorkflowState.READY_FOR_MERGE: {
            WorkflowState.MERGING,
        },
        WorkflowState.MERGING: {
            WorkflowState.DEPLOYING,
        },
        WorkflowState.DEPLOYING: {
            WorkflowState.COMPLETED,
        },
        WorkflowState.COMPLETED: set(),
        WorkflowState.FAILED: set(),
    }

    # MAS §8.8: failures are observable transitions from the active states,
    # and a REJECT user decision ends a WAITING_FOR_USER workflow as failed.
    for source in ACTIVE_STATES | {WorkflowState.WAITING_FOR_USER}:
        table[source].add(WorkflowState.FAILED)

    # MAS §8.9 / ID-069: a user decision may be requested while the workflow is
    # at any active stage; the workflow parks in WAITING_FOR_USER until the
    # decision lands.
    for source in ACTIVE_STATES:
        table[source].add(WorkflowState.WAITING_FOR_USER)

    return {state: frozenset(targets) for state, targets in table.items()}


#: The explicit workflow transition table (MAS §8.4–8.8) as data: every legal
#: ``state -> target`` pair. Frozen at import time; a transition not present
#: here raises :class:`IllegalTransitionError` — there are no implicit moves.
#: Later tickets (SFP-121..124) reference/extend this mapping; they never
#: hard-code moves elsewhere.
TRANSITIONS: Mapping[WorkflowState, frozenset[WorkflowState]] = _build_transition_table()


class WorkflowDecision(BaseModel):
    """The immutable record of one workflow transition (MAS §8.5, SFP-137).

    Carries the §8.5 field set exactly:

    - ``previous_state`` / ``resulting_state`` — the transition's endpoints.
    - ``reason`` — *why* the transition occurred.
    - ``applied_policy`` — *which* policy was applied (SFP-125..127 evaluate;
      here it is recorded as the caller-supplied identifier).
    - ``business_facts_considered`` — *which* business facts were considered
      (identifiers of the events/user decisions that caused the move).
    - ``aggregate_changes`` — *which* aggregate changes were produced (e.g.
      the Ticket's workflow_status change).
    - ``commands_emitted`` — *which* commands were emitted (MAS §8.5: every
      workflow-affecting command is traceable to exactly one decision).

    Notes:
    - Frozen and ``extra="forbid"`` — a decision, once made, is immutable
      history (MAS §8.12: workflow history is immutable).
    - States are stored as the domain enum but serialize as plain strings
      (ID-013): ``WorkflowState`` is a plain ``enum.Enum`` alias, so every
      state field has a ``Field(json_schema_extra=...)``-free ``*_name`` string
      companion, and ``to_json()`` emits those strings.
    - No clock fields: a decision carries no timestamps of its own (determinism,
      AP-011); ``occurred_at`` is the envelope's concern (SFP-219), supplied by
      the publishing wrapper, not by the pure core.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    previous_state: WorkflowState
    resulting_state: WorkflowState
    reason: str
    applied_policy: str
    business_facts_considered: tuple[str, ...] = Field(default=())
    aggregate_changes: tuple[str, ...] = Field(default=())
    commands_emitted: tuple[str, ...] = Field(default=())
    previous_state_name: str = Field(default="")
    resulting_state_name: str = Field(default="")

    def to_json(self) -> str:
        """Serialize to JSON with plain-string states (ID-013)."""
        import json

        return json.dumps(
            {
                "previous_state": self.previous_state_name or self.previous_state.name,
                "resulting_state": self.resulting_state_name or self.resulting_state.name,
                "reason": self.reason,
                "applied_policy": self.applied_policy,
                "business_facts_considered": list(self.business_facts_considered),
                "aggregate_changes": list(self.aggregate_changes),
                "commands_emitted": list(self.commands_emitted),
            },
            sort_keys=True,
        )


def transition(
    current_state: WorkflowState,
    target_state: WorkflowState,
    *,
    reason: str,
    applied_policy: str,
    business_facts_considered: Sequence[str] = (),
    aggregate_changes: Sequence[str] = (),
    command_names: Sequence[str] = (),
) -> tuple[WorkflowState, WorkflowDecision]:
    """Validate and record one workflow transition — the pure core (SFP-137).

    A pure function of its inputs (AP-011): no I/O, no clock, no randomness,
    no bus. Given the same arguments it always returns the same resulting
    state and an equal :class:`WorkflowDecision`.

    Raises:
        IllegalTransitionError: if ``(current_state, target_state)`` is not in
            :data:`TRANSITIONS` — including moves out of terminal states. There
            is never an implicit move.

    Returns:
        The resulting state and the immutable decision. The caller (a driver
        from SFP-121..124 or the publishing wrapper) owns emitting the recorded
        commands and events; commands never mutate workflow state (§8.6).
    """
    if current_state not in TRANSITIONS:
        raise IllegalTransitionError(current_state, target_state)
    if target_state not in TRANSITIONS[current_state]:
        raise IllegalTransitionError(current_state, target_state)

    decision = WorkflowDecision(
        previous_state=current_state,
        resulting_state=target_state,
        reason=reason,
        applied_policy=applied_policy,
        business_facts_considered=tuple(business_facts_considered),
        aggregate_changes=tuple(aggregate_changes),
        commands_emitted=tuple(command_names),
        previous_state_name=current_state.name,
        resulting_state_name=target_state.name,
    )
    return target_state, decision


class TransitionPolicy(Protocol):
    """Seam: policy evaluation (SFP-125..127) — NOT implemented here.

    MAS §8.14: policies are deterministic, side-effect-free functions of
    (current workflow state, observed business facts) deciding whether a
    transition is valid, which transition occurs, and which commands are
    emitted. This engine consumes a policy's *verdict*; it never evaluates
    facts itself. Later tickets implement this protocol and call
    :func:`transition` (or the wrapper) with the decided target.
    """

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> WorkflowState:
        """Return the target state the policy selects for the given facts."""
        ...  # pragma: no cover


class TransitionDriver(Protocol):
    """Seam: per-stage transition drivers (SFP-121..124) — NOT implemented here.

    A driver observes business facts for one stage (specification, coding,
    review, merge/deploy) and requests the decided move through this engine.
    Drivers own *when* a move is requested; only :data:`TRANSITIONS` defines
    *whether* it is legal.
    """

    def drive(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> WorkflowState:
        """Return the target state this driver requests from ``current_state``."""
        ...  # pragma: no cover


class DecisionSink(Protocol):
    """Seam: decision persistence (SFP-131) — NOT implemented here.

    SFP-131 stores every :class:`WorkflowDecision` durably. The engine produces
    decisions; it does not persist them. The publishing wrapper hands each
    decision to an injected sink so persistence lands outside the pure core.
    """

    def record(self, decision: WorkflowDecision) -> None:
        """Persist one decision (implemented by SFP-131)."""
        ...  # pragma: no cover


class WorkflowTransitionPublisher:
    """Thin bus-emitting wrapper around the pure core (DOC#42, SFP-137).

    Constructor-injected seams:

    - ``bus`` — the vendor-neutral :class:`~sfp_messaging.bus.MessageBus`
      (in-memory today per the software-first owner decision; SFP-101
      re-plumbs). On every successful transition it publishes one
      ``WorkflowUpdated`` event (from ``sfp_contracts.events``).
    - ``decision_sink`` — optional :class:`DecisionSink` (SFP-131); when
      provided, each decision is recorded after the core succeeds.
    - ``envelope_factory`` — optional callable producing the
      ``WorkflowUpdated`` :class:`~sfp_contracts.events.envelope.EventEnvelope`
      (``message_id`` / ``idempotency_key`` / ``correlation_id`` /
      ``causation_id`` / ``occurred_at`` are runtime policy — the deterministic
      core never invents them). When omitted, the wrapper builds a minimal
      envelope from the caller-supplied identifiers.

    The wrapper never widens legality: an illegal transition still raises
    :class:`IllegalTransitionError` from the pure core *before* anything is
    published, and no bus call can make an illegal move legal.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        decision_sink: DecisionSink | None = None,
        envelope_factory: EnvelopeFactory | None = None,
    ) -> None:
        self._bus = bus
        self._decision_sink = decision_sink
        self._envelope_factory = envelope_factory

    async def transition_and_publish(
        self,
        current_state: WorkflowState,
        target_state: WorkflowState,
        *,
        reason: str,
        applied_policy: str,
        business_facts_considered: Sequence[str] = (),
        aggregate_changes: Sequence[str] = (),
        command_names: Sequence[str] = (),
        ticket_id: str,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> tuple[WorkflowState, WorkflowDecision]:
        """Run the pure core, then publish ``WorkflowUpdated`` on success.

        Order: (1) the pure core validates + decides (raising on any illegal
        move); (2) the optional decision sink records the decision; (3) the
        ``WorkflowUpdated`` event is published on the injected bus. Publishing
        failures propagate to the caller — they never change the decision.
        """
        new_state, decision = transition(
            current_state,
            target_state,
            reason=reason,
            applied_policy=applied_policy,
            business_facts_considered=business_facts_considered,
            aggregate_changes=aggregate_changes,
            command_names=command_names,
        )
        if self._decision_sink is not None:
            self._decision_sink.record(decision)
        envelope = self._build_envelope(
            decision,
            ticket_id=ticket_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return new_state, decision

    def _build_envelope(
        self,
        decision: WorkflowDecision,
        *,
        ticket_id: str,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> EventEnvelope:
        """Build the ``WorkflowUpdated`` envelope (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(decision, ticket_id)
        from sfp_contracts.events import EventEnvelope, WorkflowUpdated
        from sfp_contracts.events.envelope import EventType

        return EventEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            event_type=EventType.WORKFLOW_UPDATED,
            producer="orchestrator",
            payload=WorkflowUpdated(
                workflow_id=ticket_id,
                status=decision.resulting_state.name,
            ),
        )


class WorkflowUpdatedEnvelopeFactory(
    Protocol,
):
    """Seam: custom envelope construction for ``WorkflowUpdated`` events."""

    def __call__(
        self,
        decision: WorkflowDecision,
        ticket_id: str,
    ) -> EventEnvelope:
        """Return the ``WorkflowUpdated`` envelope for one decision."""
        ...  # pragma: no cover


# Re-exported under a Callable alias for constructor type-checking: a lambda or
# function satisfies a Protocol structurally only when it is not parameterized
# by name, so the wrapper's ``envelope_factory`` parameter is typed with this
# explicit Callable shape instead.
EnvelopeFactory = Callable[[WorkflowDecision, str], "EventEnvelope"]


__all__ = [
    "ACTIVE_STATES",
    "EnvelopeFactory",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "DecisionSink",
    "IllegalTransitionError",
    "TransitionDriver",
    "TransitionPolicy",
    "WorkflowDecision",
    "WorkflowTransitionPublisher",
    "WorkflowUpdatedEnvelopeFactory",
    "transition",
]
