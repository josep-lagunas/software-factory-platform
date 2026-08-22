"""Tests for the workflow state-machine engine (MAS §8.4–8.8, SFP-137).

Covers: the transition table as explicit data; legality of every required move
(stage progression, ID-068 rework, WAITING_FOR_USER entry/exit, §8.8 FAILED);
illegal-move errors (including terminal states); WorkflowDecision §8.5 field
completeness + immutability + ID-013 plain-string serialization; determinism
(same inputs → identical outputs); purity (no I/O in the core); and the thin
bus-emitting wrapper publishing WorkflowUpdated via the injected bus.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    DecisionSink,
    IllegalTransitionError,
    TransitionDriver,
    TransitionPolicy,
    WorkflowDecision,
    WorkflowTransitionPublisher,
    transition,
)
from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    STATES,
    TERMINAL_STATES,
    WorkflowState,
)
from sfp_contracts.events import EventEnvelope, WorkflowUpdated
from sfp_contracts.events.envelope import EventType

# --- The table is explicit data ----------------------------------------------


def test_transition_table_is_a_plain_mapping_of_states() -> None:
    # Data, not scattered if/else: a Mapping keyed by every state, with
    # frozenset targets.
    assert isinstance(TRANSITIONS, dict)
    assert set(TRANSITIONS) == set(STATES)
    for targets in TRANSITIONS.values():
        assert isinstance(targets, frozenset)
        assert targets <= set(STATES)


def test_every_state_has_a_table_entry() -> None:
    # No implicit default branch: even states with no legal moves (terminal)
    # appear explicitly, with an empty target set.
    assert TRANSITIONS[WorkflowState.COMPLETED] == frozenset()
    assert TRANSITIONS[WorkflowState.FAILED] == frozenset()


# --- Required legal moves (MAS §8.4 order, §8.8, ID-068, ID-069) -------------


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkflowState.READY_FOR_PR_SPECIFICATION, WorkflowState.READY_FOR_CODING),
        (WorkflowState.READY_FOR_CODING, WorkflowState.CODING_IN_PROGRESS),
        (WorkflowState.CODING_IN_PROGRESS, WorkflowState.REVIEW_IN_PROGRESS),
        (WorkflowState.REVIEW_IN_PROGRESS, WorkflowState.READY_FOR_MERGE),
        # ID-068: rework is normal progression, not a failure.
        (WorkflowState.REVIEW_IN_PROGRESS, WorkflowState.CODING_IN_PROGRESS),
        (WorkflowState.READY_FOR_MERGE, WorkflowState.MERGING),
        (WorkflowState.MERGING, WorkflowState.DEPLOYING),
        (WorkflowState.DEPLOYING, WorkflowState.COMPLETED),
    ],
)
def test_stage_progression_moves_are_legal(source: WorkflowState, target: WorkflowState) -> None:
    assert target in TRANSITIONS[source]


@pytest.mark.parametrize("source", sorted(ACTIVE_STATES, key=lambda s: s.name))
def test_waiting_for_user_entry_is_legal_from_active_states(
    source: WorkflowState,
) -> None:
    # §8.9 / ID-069: users influence the workflow only through UserDecision;
    # any active stage may park waiting for one.
    assert WorkflowState.WAITING_FOR_USER in TRANSITIONS[source]


@pytest.mark.parametrize(
    "target",
    sorted(
        set(STATES) - TERMINAL_STATES - {WorkflowState.WAITING_FOR_USER, WorkflowState.FAILED},
        key=lambda s: s.name,
    ),
)
def test_waiting_for_user_exit_moves_are_legal(target: WorkflowState) -> None:
    # Exit = resuming the progression step the workflow was waiting on.
    assert target in TRANSITIONS[WorkflowState.WAITING_FOR_USER]


def test_waiting_for_user_can_exit_to_failed_on_reject() -> None:
    # ID-069 allowed decisions include REJECT: a rejected workflow ends failed.
    assert WorkflowState.FAILED in TRANSITIONS[WorkflowState.WAITING_FOR_USER]


@pytest.mark.parametrize("source", sorted(ACTIVE_STATES, key=lambda s: s.name))
def test_failed_is_reachable_from_active_states(source: WorkflowState) -> None:
    # §8.8: every failure is an observable workflow transition.
    assert WorkflowState.FAILED in TRANSITIONS[source]


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.name))
def test_terminal_states_have_no_outgoing_moves(state: WorkflowState) -> None:
    assert TRANSITIONS[state] == frozenset()


# --- Illegal transitions raise — never an implicit move ----------------------


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (WorkflowState.COMPLETED, WorkflowState.READY_FOR_PR_SPECIFICATION),
        (WorkflowState.FAILED, WorkflowState.READY_FOR_PR_SPECIFICATION),
        (WorkflowState.COMPLETED, WorkflowState.FAILED),
        (WorkflowState.READY_FOR_PR_SPECIFICATION, WorkflowState.COMPLETED),
        (WorkflowState.READY_FOR_PR_SPECIFICATION, WorkflowState.MERGING),
        (WorkflowState.READY_FOR_CODING, WorkflowState.REVIEW_IN_PROGRESS),
        (WorkflowState.READY_FOR_CODING, WorkflowState.READY_FOR_MERGE),
        (WorkflowState.CODING_IN_PROGRESS, WorkflowState.READY_FOR_MERGE),
        (WorkflowState.CODING_IN_PROGRESS, WorkflowState.DEPLOYING),
        (WorkflowState.READY_FOR_MERGE, WorkflowState.CODING_IN_PROGRESS),
        (WorkflowState.READY_FOR_MERGE, WorkflowState.DEPLOYING),
        (WorkflowState.MERGING, WorkflowState.COMPLETED),
        (WorkflowState.MERGING, WorkflowState.REVIEW_IN_PROGRESS),
        (WorkflowState.DEPLOYING, WorkflowState.MERGING),
        (WorkflowState.DEPLOYING, WorkflowState.READY_FOR_MERGE),
        (WorkflowState.WAITING_FOR_USER, WorkflowState.WAITING_FOR_USER),
    ],
)
def test_out_of_table_moves_raise(source: WorkflowState, target: WorkflowState) -> None:
    assert target not in TRANSITIONS[source]
    with pytest.raises(IllegalTransitionError) as excinfo:
        transition(source, target, reason="r", applied_policy="p")
    # The error carries current state and attempted target (observability).
    assert excinfo.value.current_state is source
    assert excinfo.value.attempted_target is target
    assert source.name in str(excinfo.value)
    assert target.name in str(excinfo.value)


def test_illegal_transition_message_is_deterministic() -> None:
    first = IllegalTransitionError(WorkflowState.COMPLETED, WorkflowState.READY_FOR_CODING)
    second = IllegalTransitionError(WorkflowState.COMPLETED, WorkflowState.READY_FOR_CODING)
    assert str(first) == str(second)


def test_transition_rejects_a_current_state_outside_the_table() -> None:
    # Defensive: TRANSITIONS always covers every state, so this branch is
    # unreachable in practice — but a move from an unknown current state must
    # still raise rather than fall through (no implicit moves, ever).
    class _NonState(WorkflowState.__base__):  # type: ignore[misc]
        NOT_A_STATE = 999

    rogue = _NonState.NOT_A_STATE  # type: ignore[attr-defined]
    with pytest.raises(IllegalTransitionError):
        transition(rogue, WorkflowState.READY_FOR_CODING, reason="r", applied_policy="p")


# --- WorkflowDecision: §8.5 fields, immutability, ID-013 serialization -------


def _run_legal_transition() -> tuple[WorkflowState, WorkflowDecision]:
    return transition(
        WorkflowState.REVIEW_IN_PROGRESS,
        WorkflowState.CODING_IN_PROGRESS,
        reason="review requested changes",
        applied_policy="review-outcome",
        business_facts_considered=["REVIEW_UPDATED:1"],
        aggregate_changes=["tickets.workflow_status"],
        command_names=["StartCodingJob"],
    )


def test_transition_returns_new_state_and_decision() -> None:
    new_state, decision = _run_legal_transition()
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS


def test_decision_carries_the_full_8_5_field_set() -> None:
    _, decision = _run_legal_transition()
    assert decision.previous_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.reason == "review requested changes"
    assert decision.applied_policy == "review-outcome"
    assert decision.business_facts_considered == ("REVIEW_UPDATED:1",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)
    assert decision.commands_emitted == ("StartCodingJob",)
    # ID-013: plain-string companions for every enum field.
    assert decision.previous_state_name == "REVIEW_IN_PROGRESS"
    assert decision.resulting_state_name == "CODING_IN_PROGRESS"


def test_decision_defaults_are_empty_tuples() -> None:
    _, decision = transition(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        WorkflowState.READY_FOR_CODING,
        reason="planning complete",
        applied_policy="spec-complete",
    )
    assert decision.business_facts_considered == ()
    assert decision.aggregate_changes == ()
    assert decision.commands_emitted == ()


def test_decision_is_immutable() -> None:
    _, decision = _run_legal_transition()
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"  # type: ignore[misc]


def test_decision_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="extra_forbidden|Extra inputs"):
        WorkflowDecision(  # type: ignore[call-arg]
            previous_state=WorkflowState.MERGING,
            resulting_state=WorkflowState.DEPLOYING,
            reason="merge completed",
            applied_policy="merge-status",
            surprise="no",
        )


def test_decision_serializes_states_as_plain_strings() -> None:
    _, decision = _run_legal_transition()
    payload: dict[str, Any] = json.loads(decision.to_json())
    # ID-013: enums serialize as their plain string value — no enum wrapper,
    # no auto() integer leaking through.
    assert payload["previous_state"] == "REVIEW_IN_PROGRESS"
    assert payload["resulting_state"] == "CODING_IN_PROGRESS"
    assert payload["commands_emitted"] == ["StartCodingJob"]
    assert "WorkflowState" not in decision.to_json()
    assert "WorkflowStatus" not in decision.to_json()


# --- Determinism (AP-011) and purity -----------------------------------------


def test_identical_inputs_produce_identical_decision_and_outputs() -> None:
    first_state, first_decision = _run_legal_transition()
    second_state, second_decision = _run_legal_transition()
    assert first_state is second_state
    assert first_decision == second_decision
    assert first_decision.to_json() == second_decision.to_json()


def test_core_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # Purity: the core never touches the filesystem, sockets, or the clock.
    # Block the usual I/O entry points; a pure function must not notice.
    import builtins
    import socket
    import time

    def _forbidden(call: str) -> Any:
        def _raise(*args: object, **kwargs: object) -> Any:
            raise AssertionError(f"{call} called")

        return _raise

    monkeypatch.setattr(builtins, "open", _forbidden("open"))
    monkeypatch.setattr(socket.socket, "connect", _forbidden("socket.connect"))
    monkeypatch.setattr(time, "time", _forbidden("time.time"))

    new_state, decision = _run_legal_transition()
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state_name == "REVIEW_IN_PROGRESS"


# --- Seams for later tickets (typed, unimplemented here) ----------------------


def test_policy_driver_sink_seams_are_satisfiable_structurally() -> None:
    # Later tickets implement these protocols; a minimal fake satisfies each
    # structurally, proving the seam signatures compile and are callable.
    class FakePolicy:
        def decide(self, current_state: WorkflowState, business_facts: list[str]) -> WorkflowState:
            return next(iter(TRANSITIONS[current_state]))

    class FakeDriver:
        def drive(self, current_state: WorkflowState, business_facts: list[str]) -> WorkflowState:
            return WorkflowState.READY_FOR_MERGE

    recorded: list[WorkflowDecision] = []

    class FakeSink:
        def record(self, decision: WorkflowDecision) -> None:
            recorded.append(decision)

    policy: TransitionPolicy = FakePolicy()  # type: ignore[assignment]
    driver: TransitionDriver = FakeDriver()  # type: ignore[assignment]
    sink: DecisionSink = FakeSink()

    # The seam consumes engine-produced decisions: a later ticket (SFP-131)
    # implements record() exactly this way, against this exact input type.
    # The pure core takes no sink argument at all, so nothing engine-side can
    # record behind the caller's back.
    probe = transition(
        WorkflowState.READY_FOR_CODING,
        WorkflowState.CODING_IN_PROGRESS,
        reason="seam probe",
        applied_policy="probe",
    )
    sink.record(probe[1])
    assert recorded == [probe[1]]

    assert (
        policy.decide(WorkflowState.READY_FOR_CODING, [])
        in TRANSITIONS[WorkflowState.READY_FOR_CODING]
    )
    assert driver.drive(WorkflowState.REVIEW_IN_PROGRESS, []) is WorkflowState.READY_FOR_MERGE


# --- The thin bus-emitting wrapper -------------------------------------------


class _RecordingBus:
    """Minimal MessageBus double: records published envelopes."""

    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("subscribe is not exercised by the wrapper")


async def test_wrapper_publishes_workflow_updated_on_each_transition() -> None:
    bus = _RecordingBus()
    publisher = WorkflowTransitionPublisher(bus)
    new_state, decision = await publisher.transition_and_publish(
        WorkflowState.CODING_IN_PROGRESS,
        WorkflowState.REVIEW_IN_PROGRESS,
        reason="coder finished",
        applied_policy="coding-complete",
        ticket_id="ticket-1",
        message_id="m-1",
        idempotency_key="idem-1",
        correlation_id="corr-1",
        causation_id="cause-1",
        occurred_at="2026-08-22T00:00:00Z",
    )
    assert new_state is WorkflowState.REVIEW_IN_PROGRESS
    assert len(bus.published) == 1
    envelope = bus.published[0]
    assert isinstance(envelope, EventEnvelope)
    assert envelope.event_type is EventType.WORKFLOW_UPDATED
    assert isinstance(envelope.payload, WorkflowUpdated)
    assert envelope.payload.workflow_id == "ticket-1"
    # ID-013: the status is the plain state name.
    assert envelope.payload.status == "REVIEW_IN_PROGRESS"
    assert envelope.message_id == "m-1"
    assert envelope.idempotency_key == "idem-1"
    assert envelope.correlation_id == "corr-1"
    assert envelope.causation_id == "cause-1"
    assert decision.resulting_state is WorkflowState.REVIEW_IN_PROGRESS


async def test_wrapper_does_not_publish_on_illegal_transition() -> None:
    bus = _RecordingBus()
    publisher = WorkflowTransitionPublisher(bus)
    with pytest.raises(IllegalTransitionError):
        await publisher.transition_and_publish(
            WorkflowState.COMPLETED,
            WorkflowState.READY_FOR_CODING,
            reason="cannot restart",
            applied_policy="none",
            ticket_id="ticket-1",
            message_id="m-1",
            idempotency_key="idem-1",
            correlation_id="corr-1",
            causation_id="cause-1",
            occurred_at="2026-08-22T00:00:00Z",
        )
    assert bus.published == []


async def test_wrapper_records_decision_through_injected_sink() -> None:
    bus = _RecordingBus()
    recorded: list[WorkflowDecision] = []

    class FakeSink:
        def record(self, decision: WorkflowDecision) -> None:
            recorded.append(decision)

    publisher = WorkflowTransitionPublisher(bus, decision_sink=FakeSink())
    await publisher.transition_and_publish(
        WorkflowState.MERGING,
        WorkflowState.DEPLOYING,
        reason="merge completed",
        applied_policy="merge-status",
        ticket_id="ticket-2",
        message_id="m-2",
        idempotency_key="idem-2",
        correlation_id="corr-2",
        causation_id="cause-2",
        occurred_at="2026-08-22T00:00:01Z",
    )
    assert len(recorded) == 1
    assert recorded[0].resulting_state is WorkflowState.DEPLOYING
    assert len(bus.published) == 1


async def test_wrapper_uses_injected_envelope_factory() -> None:
    bus = _RecordingBus()

    def factory(decision: WorkflowDecision, ticket_id: str) -> EventEnvelope:
        return EventEnvelope(
            message_id="factory-message",
            idempotency_key="factory-idem",
            correlation_id="factory-corr",
            causation_id="factory-cause",
            occurred_at="2026-08-22T00:00:02Z",
            event_type=EventType.WORKFLOW_UPDATED,
            producer="orchestrator-test",
            payload=WorkflowUpdated(
                workflow_id=ticket_id,
                status=decision.resulting_state.name,
            ),
        )

    publisher = WorkflowTransitionPublisher(bus, envelope_factory=factory)
    await publisher.transition_and_publish(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        WorkflowState.READY_FOR_CODING,
        reason="spec complete",
        applied_policy="spec-complete",
        ticket_id="ticket-3",
        message_id="ignored-by-factory",
        idempotency_key="ignored",
        correlation_id="ignored",
        causation_id="ignored",
        occurred_at="2026-08-22T00:00:03Z",
    )
    assert len(bus.published) == 1
    assert bus.published[0].message_id == "factory-message"
    assert bus.published[0].payload.workflow_id == "ticket-3"


async def test_wrapper_is_deterministic_given_identical_inputs() -> None:
    first_bus, second_bus = _RecordingBus(), _RecordingBus()
    kwargs: dict[str, Any] = {
        "reason": "approved",
        "applied_policy": "review-outcome",
        "ticket_id": "ticket-4",
        "message_id": "m-4",
        "idempotency_key": "idem-4",
        "correlation_id": "corr-4",
        "causation_id": "cause-4",
        "occurred_at": "2026-08-22T00:00:04Z",
    }
    _, first_decision = await WorkflowTransitionPublisher(first_bus).transition_and_publish(
        WorkflowState.REVIEW_IN_PROGRESS, WorkflowState.READY_FOR_MERGE, **kwargs
    )
    _, second_decision = await WorkflowTransitionPublisher(second_bus).transition_and_publish(
        WorkflowState.REVIEW_IN_PROGRESS, WorkflowState.READY_FOR_MERGE, **kwargs
    )
    assert first_decision == second_decision
    assert first_bus.published[0].to_json() == second_bus.published[0].to_json()
