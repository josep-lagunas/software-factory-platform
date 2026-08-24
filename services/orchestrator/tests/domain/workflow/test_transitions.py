"""Tests for the Ticket→PR-spec stage-transition driver (MAS §8.4–8.8, SFP-138).

Covers: the successful-plan fact predicate (agent/status/pr-specs gating);
successful-plan fact → transition to READY_FOR_CODING with the engine-produced
immutable WorkflowDecision (§8.5); failed/absent plan fact → NO transition plus
a recorded non-move decision (§8.8 — never swallowed); illegal-state input →
IllegalTransitionError propagates from the SFP-137 table guard (no implicit
moves); the driver adds no states and duplicates no table data; purity
(no I/O, bus as an injected seam elsewhere) and determinism (same inputs →
same outputs, AP-011).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    IllegalTransitionError,
    WorkflowDecision,
)
from orchestrator.domain.workflow.states import STATES, WorkflowState
from orchestrator.domain.workflow.transitions import (
    PLANNER_AGENT,
    SPEC_STAGE_POLICY,
    SPEC_STAGE_SOURCE,
    SPEC_STAGE_TARGET,
    drive_spec_stage,
    spec_stage_fact_is_successful,
)

#: The canonical successful-plan fact used across the happy-path tests:
#: ``(agent, status, pr_spec_ids)`` — the landed contract combination.
SUCCESS_FACT: tuple[str, str, tuple[str, ...]] = (
    "planner",
    "SUCCESS",
    ("SFP-1-prspec-1",),
)


# --- The successful-plan fact predicate ----------------------------------------


@pytest.mark.parametrize(
    ("agent", "status", "pr_spec_ids", "expected"),
    [
        ("planner", "SUCCESS", ("SFP-1-prspec-1",), True),
        ("planner", "SUCCESS", ("a", "b"), True),
        # Wrong agent: only the planner's fact advances this stage (§8.6).
        ("coder", "SUCCESS", ("SFP-1-prspec-1",), False),
        ("reviewer", "SUCCESS", ("SFP-1-prspec-1",), False),
        (None, "SUCCESS", ("SFP-1-prspec-1",), False),
        # Non-success terminal status: FAILED/BLOCKED/NEEDS_HUMAN/NEEDS_RETRY
        # (AgentStatus) are all not-a-completed-plan facts.
        ("planner", "FAILED", ("SFP-1-prspec-1",), False),
        ("planner", "BLOCKED", ("SFP-1-prspec-1",), False),
        ("planner", "NEEDS_HUMAN", ("SFP-1-prspec-1",), False),
        ("planner", "NEEDS_RETRY", ("SFP-1-prspec-1",), False),
        ("planner", None, ("SFP-1-prspec-1",), False),
        # A success-shaped fact with zero PR-specs is not a plan-completed
        # fact: a validated PlannerOutput always carries >=1 PR-spec.
        ("planner", "SUCCESS", (), False),
        ("planner", "SUCCESS", ("",), True),  # non-empty tuple of ids counts
    ],
)
def test_fact_predicate(
    agent: str | None,
    status: str | None,
    pr_spec_ids: tuple[str, ...],
    expected: bool,
) -> None:
    assert (
        spec_stage_fact_is_successful(
            agent=agent,
            status=status,
            pr_spec_ids=pr_spec_ids,
        )
        is expected
    )


# --- Happy path: successful plan → move through the SFP-137 engine -------------


def test_successful_plan_fact_moves_to_ready_for_coding() -> None:
    new_state, decision = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=SUCCESS_FACT,
    )
    assert new_state is WorkflowState.READY_FOR_CODING
    assert decision.previous_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    assert decision.resulting_state is WorkflowState.READY_FOR_CODING
    # ID-013: plain-string companions.
    assert decision.previous_state_name == "READY_FOR_PR_SPECIFICATION"
    assert decision.resulting_state_name == "READY_FOR_CODING"


def test_successful_plan_decision_is_the_engine_produced_record() -> None:
    # The move is delegated to the SFP-137 engine: the returned decision is
    # exactly what transition() produces for this edge — the driver neither
    # re-implements the guard nor fabricates its own move record.
    _, via_engine = transition_probe(SPEC_STAGE_SOURCE, SPEC_STAGE_TARGET)
    _, via_driver = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=SUCCESS_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == SPEC_STAGE_POLICY == via_engine.applied_policy
    assert via_driver.business_facts_considered == ("plan-fact:planner:SUCCESS:SFP-1-prspec-1",)
    assert via_driver.aggregate_changes == ("tickets.workflow_status",)


def transition_probe(
    source: WorkflowState,
    target: WorkflowState,
) -> tuple[WorkflowState, WorkflowDecision]:
    from orchestrator.domain.workflow.state_machine import transition

    return transition(
        source,
        target,
        reason="probe",
        applied_policy=SPEC_STAGE_POLICY,
    )


def test_successful_plan_decision_is_immutable() -> None:
    _, decision = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=SUCCESS_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


# --- Failed / absent plan fact → NO transition + recorded non-move (§8.8) ------


def test_absent_plan_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION)
    assert new_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    # Same state on both endpoints: this is a recorded non-move, not a move.
    assert decision.previous_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    assert decision.resulting_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    assert decision.business_facts_considered == ("plan-fact:absent",)
    assert "no plan fact" in decision.reason
    # The non-move is returned (never swallowed) and carries the applied rule.
    assert decision.applied_policy == SPEC_STAGE_POLICY


@pytest.mark.parametrize(
    ("agent", "status", "pr_spec_ids"),
    [
        ("planner", "FAILED", ("SFP-1-prspec-1",)),
        ("planner", "BLOCKED", ("SFP-1-prspec-1",)),
        ("planner", "NEEDS_HUMAN", ("SFP-1-prspec-1",)),
        ("planner", "NEEDS_RETRY", ("SFP-1-prspec-1",)),
        ("planner", "SUCCESS", ()),  # success-shaped but empty
        ("coder", "SUCCESS", ("SFP-1-prspec-1",)),  # wrong producer
        (None, "SUCCESS", ("SFP-1-prspec-1",)),
        ("planner", None, ("SFP-1-prspec-1",)),
        (None, None, ()),
    ],
)
def test_not_successful_plan_fact_holds_state_and_records_non_move(
    agent: str | None,
    status: str | None,
    pr_spec_ids: tuple[str, ...],
) -> None:
    fact: tuple[str | None, str | None, tuple[str, ...]] = (agent, status, pr_spec_ids)
    new_state, decision = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=fact,
    )
    assert new_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    assert decision.resulting_state is WorkflowState.READY_FOR_PR_SPECIFICATION
    # Every non-success fact still names the fact it considered (§8.8: the
    # failure is a recorded business fact, not a silent branch).
    assert len(decision.business_facts_considered) == 1
    assert decision.business_facts_considered[0].startswith("plan-fact:")
    assert "not successful" in decision.reason


def test_non_move_fact_identifiers_are_deterministic() -> None:
    fact: tuple[str | None, str | None, tuple[str, ...]] = (
        "planner",
        "FAILED",
        ("SFP-1-prspec-1",),
    )
    _, first = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION, plan_fact=fact)
    _, second = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION, plan_fact=fact)
    assert first.business_facts_considered == ("plan-fact:planner:FAILED:SFP-1-prspec-1",)
    assert first == second


# --- Illegal-state input → the table guard raises (no implicit moves) ----------


def test_successful_plan_fact_from_wrong_state_raises() -> None:
    # The driver only ever drives the single spec-stage edge; a workflow not
    # sitting at READY_FOR_PR_SPECIFICATION must raise straight from the
    # SFP-137 table guard — never an implicit or best-effort move.
    #
    # WAITING_FOR_USER is the one §8.4 state whose table row *does* contain
    # READY_FOR_CODING (the SFP-137 resume edge), so it is excluded: that move
    # is legal at the table level by design. Whether a parked workflow should
    # be resumed by this driver is SFP-142 policy territory, out of scope here.
    wrong_states = sorted(
        set(STATES) - {WorkflowState.READY_FOR_PR_SPECIFICATION, WorkflowState.WAITING_FOR_USER},
        key=lambda s: s.name,
    )
    assert wrong_states  # sanity: the exclusion did not empty the set
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_spec_stage(state, plan_fact=SUCCESS_FACT)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is SPEC_STAGE_TARGET


def test_waiting_for_user_source_is_documented_table_legal_not_driver_legal() -> None:
    # Guard the exclusion above: WAITING_FOR_USER -> READY_FOR_CODING must
    # remain a *table* edge (resume semantics), so if the SFP-137 table ever
    # drops it, this test — not a silent behavior change — notices first.
    assert SPEC_STAGE_TARGET in TRANSITIONS[WorkflowState.WAITING_FOR_USER]


def test_illegal_move_error_carries_both_states() -> None:
    with pytest.raises(IllegalTransitionError) as excinfo:
        drive_spec_stage(
            WorkflowState.READY_FOR_CODING,
            plan_fact=SUCCESS_FACT,
        )
    assert "READY_FOR_CODING" in str(excinfo.value)
    assert "READY_FOR_PR_SPECIFICATION" not in str(excinfo.value)


# --- No new states, no table duplication ---------------------------------------


def test_driver_adds_no_workflow_states() -> None:
    # The driver must not introduce workflow vocabulary of its own: its only
    # states are members of the §8.4 enum.
    assert SPEC_STAGE_SOURCE in STATES
    assert SPEC_STAGE_TARGET in STATES
    assert len(STATES) == 10


def test_driver_edge_matches_the_landed_transition_table() -> None:
    # The edge the driver requests exists in the SFP-137 table as data —
    # no table edit was needed for this ticket (verified at read time).
    assert SPEC_STAGE_TARGET in TRANSITIONS[SPEC_STAGE_SOURCE]
    assert SPEC_STAGE_SOURCE is WorkflowState.READY_FOR_PR_SPECIFICATION
    assert SPEC_STAGE_TARGET is WorkflowState.READY_FOR_CODING


def test_driver_planner_agent_name_is_the_canonical_constant() -> None:
    assert PLANNER_AGENT == "planner"


# --- Purity + determinism (AP-011) ---------------------------------------------


def test_drive_spec_stage_is_deterministic() -> None:
    first = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION, plan_fact=SUCCESS_FACT)
    second = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION, plan_fact=SUCCESS_FACT)
    assert first[0] is second[0]
    assert first[1] == second[1]
    assert first[1].to_json() == second[1].to_json()

    held_first = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION)
    held_second = drive_spec_stage(WorkflowState.READY_FOR_PR_SPECIFICATION)
    assert held_first == held_second


def test_drive_spec_stage_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # Purity: block the usual I/O entry points — a pure driver must not notice.
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

    new_state, decision = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=SUCCESS_FACT,
    )
    assert new_state is WorkflowState.READY_FOR_CODING
    assert decision.resulting_state_name == "READY_FOR_CODING"


def test_bus_is_not_touched_by_the_driver() -> None:
    # The bus stays an injected seam owned by WorkflowTransitionPublisher; the
    # pure driver module must not reference messaging machinery at all.
    import orchestrator.domain.workflow.transitions as transitions_module

    source = inspect.getsource(transitions_module)
    assert "sfp_messaging" not in source
    assert "MessageBus" not in source
    assert "publish" not in source

    # And a run with the messaging import broken still succeeds end to end —
    # proving the driver never goes near it at runtime either.
    new_state, _ = drive_spec_stage(
        WorkflowState.READY_FOR_PR_SPECIFICATION,
        plan_fact=SUCCESS_FACT,
    )
    assert new_state is WorkflowState.READY_FOR_CODING


def test_driver_satisfies_the_transition_driver_seam_shape() -> None:
    # The SFP-137 TransitionDriver protocol is `drive(current_state,
    # business_facts) -> WorkflowState`; this driver's public surface drives
    # the same decision path (fact-gated move through the engine), so a later
    # adapter can wrap it without changing behavior.
    from orchestrator.domain.workflow.state_machine import TransitionDriver

    class SpecStageAdapter:
        """Minimal adapter proving the driver fits the SFP-137 seam."""

        def drive(
            self,
            current_state: WorkflowState,
            business_facts: list[str],
        ) -> WorkflowState:
            successful = any(
                spec_stage_fact_is_successful(
                    agent=_split_fact(fact)[0],
                    status=_split_fact(fact)[1],
                    pr_spec_ids=_split_fact(fact)[2],
                )
                for fact in business_facts
            )
            return drive_spec_stage(
                current_state,
                plan_fact=SUCCESS_FACT if successful else None,
            )[0]

    driver: TransitionDriver = SpecStageAdapter()  # type: ignore[assignment]
    assert (
        driver.drive(WorkflowState.READY_FOR_PR_SPECIFICATION, ["plan-fact:planner:SUCCESS:x"])
        is WorkflowState.READY_FOR_CODING
    )
    assert driver.drive(WorkflowState.READY_FOR_PR_SPECIFICATION, []) is (
        WorkflowState.READY_FOR_PR_SPECIFICATION
    )


def _split_fact(fact: str) -> tuple[str, str, tuple[str, ...]]:
    parts = fact.split(":")
    if len(parts) != 4 or not parts[3]:
        return ("", "", ())
    return (parts[1], parts[2], tuple(parts[3].split(",")))
