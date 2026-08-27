"""Tests for the merge/deploy stage drivers + the ID-024 merge wait (SFP-140).

Covers the three new edges driven in ``transitions.py``:

- ``READY_FOR_MERGE → MERGING`` — fired by an *approval* fact
  (``(approved, validation_profile)``; only ``approved is True`` merges).
- ``MERGING → DEPLOYING`` and ``DEPLOYING → COMPLETED`` — decided by the
  exhaustive 6-row decision table: the deploy begins only on a completed merge
  **and** a deploy target, and completes only on a ``"succeeded"`` deployment.
  A ``"failed"`` deployment is a recorded no-move here (failure handling is the
  landed SFP-144 ``ShouldFailPolicy``'s) — this driver never enters ``FAILED``.
- ``MERGING → WAITING_FOR_USER`` (the ID-024 parking edge) — fired by an
  *approval-required* fact (the ID-067 ``REQUIRES_HUMAN_APPROVAL`` profile set
  is the fact's source, not an import here). The wait is parked, never
  resolved: no code path here leaves ``WAITING_FOR_USER``.

Every decision-table row of every driver is asserted row-by-row — endpoints,
reason, ``applied_policy``, ``business_facts_considered``, and the §8.8
same-state non-move records. Illegal moves raise straight from the SFP-137
table guard; the drivers add no states, duplicate no table data, and stay pure
and deterministic (AP-011).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    IllegalTransitionError,
    WorkflowDecision,
    transition,
)
from orchestrator.domain.workflow.states import STATES, WorkflowState
from orchestrator.domain.workflow.transitions import (
    DEPLOY_STAGE_BEGIN_SOURCE,
    DEPLOY_STAGE_BEGIN_TARGET,
    DEPLOY_STAGE_FINISH_SOURCE,
    DEPLOY_STAGE_FINISH_TARGET,
    DEPLOY_STAGE_POLICY,
    DEPLOYMENT_FAILED_STATUS,
    DEPLOYMENT_SUCCEEDED_STATUS,
    MERGE_STAGE_POLICY,
    MERGE_STAGE_SOURCE,
    MERGE_STAGE_TARGET,
    MERGE_WAIT_POLICY,
    MERGE_WAIT_SOURCE,
    MERGE_WAIT_TARGET,
    approval_fact_granted,
    deploy_outcome_recognized,
    drive_deploy_stage,
    drive_merge_stage,
    drive_merge_wait,
)

#: The canonical approval-granted fact: ``(approved, validation_profile)`` —
#: the landed approval observation shape.
APPROVAL_FACT: tuple[bool, str] = (True, "LEVEL_1_INTERNAL")

#: The canonical deploy target, observed present once the merge completes.
DEPLOY_TARGET_REF = "refs/tags/v1"


def _deploy_begin_kwargs(**overrides: Any) -> dict[str, Any]:
    """The canonical row-4 kwargs (completed merge + deploy target)."""
    kwargs: dict[str, Any] = {"merge_completed": True, "deploy_target_ref": DEPLOY_TARGET_REF}
    kwargs.update(overrides)
    return kwargs


def _states_where_the_move_is_table_illegal(
    target: WorkflowState,
) -> list[WorkflowState]:
    """Every §8.4 state from which ``target`` is NOT a legal table move.

    Derived from the landed SFP-137 table as data, so the wrong-state tests
    below encode the actual guarantee — the driver raises iff the table says
    the move is illegal.
    """
    return sorted(
        (state for state in STATES if target not in TRANSITIONS[state]),
        key=lambda s: s.name,
    )


# --- The approval fact predicate ------------------------------------------------


@pytest.mark.parametrize(
    ("approved", "expected"),
    [
        (True, True),
        # An absent or explicitly-not-approved verdict never merges: an absent
        # approval is not an approval, and False covers CHANGES_REQUESTED.
        (False, False),
        (None, False),
    ],
)
def test_approval_fact_granted_predicate(
    approved: bool | None,
    expected: bool,
) -> None:
    assert approval_fact_granted(approved=approved) is expected


# --- Merge stage: READY_FOR_MERGE → MERGING -------------------------------------


def test_approval_moves_to_merging() -> None:
    new_state, decision = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=APPROVAL_FACT,
    )
    assert new_state is WorkflowState.MERGING
    assert decision.previous_state is WorkflowState.READY_FOR_MERGE
    assert decision.resulting_state is WorkflowState.MERGING
    # ID-013: plain-string companions.
    assert decision.previous_state_name == "READY_FOR_MERGE"
    assert decision.resulting_state_name == "MERGING"


def test_approval_decision_is_the_engine_produced_record() -> None:
    # The move is delegated to the SFP-137 engine: the returned decision is
    # exactly what transition() produces for this edge — the driver neither
    # re-implements the guard nor fabricates its own move record.
    _, via_engine = transition(
        MERGE_STAGE_SOURCE,
        MERGE_STAGE_TARGET,
        reason="probe",
        applied_policy=MERGE_STAGE_POLICY,
    )
    _, via_driver = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=APPROVAL_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == MERGE_STAGE_POLICY == via_engine.applied_policy
    assert via_driver.reason == "approval granted: begin merge"
    assert via_driver.business_facts_considered == ("approval-fact:true:LEVEL_1_INTERNAL",)
    assert via_driver.aggregate_changes == ("tickets.workflow_status",)
    assert via_driver.commands_emitted == ()


def test_absent_approval_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_merge_stage(WorkflowState.READY_FOR_MERGE)
    assert new_state is WorkflowState.READY_FOR_MERGE
    assert decision.previous_state is WorkflowState.READY_FOR_MERGE
    assert decision.resulting_state is WorkflowState.READY_FOR_MERGE
    assert decision.business_facts_considered == ("approval-fact:absent",)
    assert decision.reason == "no approval fact observed: the workflow stays at this stage"
    assert decision.applied_policy == MERGE_STAGE_POLICY
    assert decision.aggregate_changes == ()


@pytest.mark.parametrize("approved", [False, None])
def test_not_approved_fact_holds_state_and_records_non_move(
    approved: bool | None,
) -> None:
    # approved=False and approved=None never merge — including the
    # CHANGES_REQUESTED-shaped False verdict.
    fact: tuple[bool | None, str | None] = (approved, "LEVEL_1_INTERNAL")
    new_state, decision = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=fact,
    )
    assert new_state is WorkflowState.READY_FOR_MERGE
    assert decision.previous_state is WorkflowState.READY_FOR_MERGE
    assert decision.resulting_state is WorkflowState.READY_FOR_MERGE
    assert decision.reason == "approval fact not approved: the workflow stays at this stage"
    assert decision.applied_policy == MERGE_STAGE_POLICY
    expected_id = (
        "approval-fact:false:LEVEL_1_INTERNAL"
        if approved is False
        else "approval-fact:unknown-approval:LEVEL_1_INTERNAL"
    )
    assert decision.business_facts_considered == (expected_id,)
    assert decision.aggregate_changes == ()


def test_merge_non_move_identifiers_are_deterministic() -> None:
    fact: tuple[bool | None, str | None] = (False, "LEVEL_1_INTERNAL")
    _, first = drive_merge_stage(WorkflowState.READY_FOR_MERGE, approval_fact=fact)
    _, second = drive_merge_stage(WorkflowState.READY_FOR_MERGE, approval_fact=fact)
    assert first == second
    assert first.to_json() == second.to_json()


def test_merge_stage_decision_is_immutable() -> None:
    _, decision = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=APPROVAL_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_approval_from_wrong_state_raises() -> None:
    # approved=True moves only from READY_FOR_MERGE; from anywhere else the
    # SFP-137 table guard raises — no implicit fallback (MAS §8.2).
    wrong_states = _states_where_the_move_is_table_illegal(MERGE_STAGE_TARGET)
    assert WorkflowState.READY_FOR_CODING in wrong_states
    assert WorkflowState.REVIEW_IN_PROGRESS in wrong_states
    assert WorkflowState.WAITING_FOR_USER not in wrong_states  # resume edge
    assert wrong_states  # sanity: the table still forbids somewhere
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_merge_stage(state, approval_fact=APPROVAL_FACT)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is MERGE_STAGE_TARGET


# --- The deploy-outcome predicate ----------------------------------------------


@pytest.mark.parametrize(
    ("deployment_status", "expected"),
    [
        ("succeeded", True),
        ("failed", True),
        # Unobserved / unrecognized vocabulary values carry no recognized
        # outcome — the driver records a no-move naming them, never raises.
        (None, False),
        ("SUCCEEDED", False),
        ("Succeeded", False),
        ("in_progress", False),
        ("pending", False),
        ("cancelled", False),
        ("", False),
        ("some-arbitrary-unrecognized-string", False),
    ],
)
def test_deploy_outcome_recognized_predicate(
    deployment_status: str | None,
    expected: bool,
) -> None:
    assert deploy_outcome_recognized(deployment_status=deployment_status) is expected


def test_deployment_status_constants_are_the_recognized_vocabulary() -> None:
    assert DEPLOYMENT_SUCCEEDED_STATUS == "succeeded"
    assert DEPLOYMENT_FAILED_STATUS == "failed"


# --- Deploy stage row 1: DEPLOYING + 'succeeded' → COMPLETED --------------------


def test_succeeded_deployment_completes_the_workflow() -> None:
    new_state, decision = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    assert new_state is WorkflowState.COMPLETED
    assert decision.previous_state is WorkflowState.DEPLOYING
    assert decision.resulting_state is WorkflowState.COMPLETED
    assert decision.previous_state_name == "DEPLOYING"
    assert decision.resulting_state_name == "COMPLETED"
    assert decision.reason == "deployment succeeded"
    assert decision.applied_policy == DEPLOY_STAGE_POLICY
    assert decision.business_facts_considered == ("deploy-outcome-fact:succeeded",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)


def test_deploy_finish_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        DEPLOY_STAGE_FINISH_SOURCE,
        DEPLOY_STAGE_FINISH_TARGET,
        reason="probe",
        applied_policy=DEPLOY_STAGE_POLICY,
    )
    _, via_driver = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == DEPLOY_STAGE_POLICY == via_engine.applied_policy


def test_deploy_stage_decision_is_immutable() -> None:
    _, decision = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


# --- Deploy stage row 2: DEPLOYING + 'failed' → recorded no-move, never FAILED --


def test_failed_deployment_never_enters_failed() -> None:
    new_state, decision = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_FAILED_STATUS,
    )
    assert new_state is WorkflowState.DEPLOYING
    assert decision.previous_state is WorkflowState.DEPLOYING
    assert decision.resulting_state is WorkflowState.DEPLOYING
    assert decision.resulting_state is not WorkflowState.FAILED
    assert new_state is not WorkflowState.FAILED
    assert decision.reason == (
        "deployment failed: failure handling belongs to the ShouldFail policy, not this driver"
    )
    assert decision.applied_policy == DEPLOY_STAGE_POLICY
    assert decision.business_facts_considered == ("deploy-outcome-fact:failed",)
    assert decision.aggregate_changes == ()


# --- Deploy stage row 3: DEPLOYING + None/unrecognized → recorded no-move -------


@pytest.mark.parametrize(
    "deployment_status",
    [None, "SUCCEEDED", "in_progress", "pending", "some-arbitrary-unrecognized-string", ""],
)
def test_unrecognized_deployment_status_is_treated_as_absent(
    deployment_status: str | None,
) -> None:
    # An arbitrary unrecognized string is treated as absent — a recorded
    # no-move naming it, never an exception and never a guess.
    new_state, decision = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=deployment_status,
    )
    assert new_state is WorkflowState.DEPLOYING
    assert decision.resulting_state is WorkflowState.DEPLOYING
    assert decision.resulting_state is not WorkflowState.FAILED
    assert decision.reason == "no deploy outcome observed"
    assert decision.applied_policy == DEPLOY_STAGE_POLICY
    expected_id = (
        "deploy-outcome-fact:none"
        if not deployment_status
        else f"deploy-outcome-fact:{deployment_status}"
    )
    assert decision.business_facts_considered == (expected_id,)
    assert decision.aggregate_changes == ()


def test_deploy_finish_non_move_is_deterministic() -> None:
    _, first = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status="in_progress",
    )
    _, second = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status="in_progress",
    )
    assert first == second
    assert first.to_json() == second.to_json()


# --- Deploy stage row 4: MERGING + completed merge AND target → DEPLOYING -------


def test_completed_merge_with_deploy_target_begins_deploying() -> None:
    new_state, decision = drive_deploy_stage(
        WorkflowState.MERGING,
        **_deploy_begin_kwargs(),
    )
    assert new_state is WorkflowState.DEPLOYING
    assert decision.previous_state is WorkflowState.MERGING
    assert decision.resulting_state is WorkflowState.DEPLOYING
    assert decision.previous_state_name == "MERGING"
    assert decision.resulting_state_name == "DEPLOYING"
    assert decision.reason == "merge completed and deploy target present: begin deploy"
    assert decision.applied_policy == DEPLOY_STAGE_POLICY
    assert decision.business_facts_considered == ("deploy-fact:true:refs/tags/v1",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)


def test_deploy_begin_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        DEPLOY_STAGE_BEGIN_SOURCE,
        DEPLOY_STAGE_BEGIN_TARGET,
        reason="probe",
        applied_policy=DEPLOY_STAGE_POLICY,
    )
    _, via_driver = drive_deploy_stage(
        WorkflowState.MERGING,
        **_deploy_begin_kwargs(),
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == DEPLOY_STAGE_POLICY == via_engine.applied_policy
    assert via_driver.commands_emitted == ()


def test_deploy_begin_never_consults_the_deployment_status() -> None:
    # The deployment outcome is deliberately not consulted at MERGING: no
    # deployment exists yet, so even a stray 'succeeded' observation cannot
    # skip the deploy stage, and a 'failed' one cannot stall it either.
    for status in (None, "succeeded", "failed", "in_progress"):
        new_state, decision = drive_deploy_stage(
            WorkflowState.MERGING,
            **_deploy_begin_kwargs(),
            deployment_status=status,
        )
        assert new_state is WorkflowState.DEPLOYING
        assert decision.resulting_state is WorkflowState.DEPLOYING
        assert decision.reason == "merge completed and deploy target present: begin deploy"
        assert decision.business_facts_considered == ("deploy-fact:true:refs/tags/v1",)


# --- Deploy stage row 5: MERGING otherwise → recorded no-move --------------------


@pytest.mark.parametrize(
    ("merge_completed", "deploy_target_ref", "expected_reason", "expected_fact_id"),
    [
        # 'merge not completed' — unobserved or explicitly not complete.
        (
            None,
            DEPLOY_TARGET_REF,
            "merge not completed",
            "deploy-fact:unknown-merge-completed:refs/tags/v1",
        ),
        (
            False,
            DEPLOY_TARGET_REF,
            "merge not completed",
            "deploy-fact:false:refs/tags/v1",
        ),
        # 'no deploy target: nothing to deploy' — merge done, no target.
        (
            True,
            None,
            "no deploy target: nothing to deploy",
            "deploy-fact:true:no-deploy-target",
        ),
        (
            True,
            "",
            "no deploy target: nothing to deploy",
            "deploy-fact:true:no-deploy-target",
        ),
        # Both legs unmet still names the merge first (deterministic order).
        (
            None,
            None,
            "merge not completed",
            "deploy-fact:unknown-merge-completed:no-deploy-target",
        ),
    ],
)
def test_deploy_begin_row5_holds_state_and_records_non_move(
    merge_completed: bool | None,
    deploy_target_ref: str | None,
    expected_reason: str,
    expected_fact_id: str,
) -> None:
    new_state, decision = drive_deploy_stage(
        WorkflowState.MERGING,
        merge_completed=merge_completed,
        deploy_target_ref=deploy_target_ref,
    )
    assert new_state is WorkflowState.MERGING
    assert decision.previous_state is WorkflowState.MERGING
    assert decision.resulting_state is WorkflowState.MERGING
    assert decision.reason == expected_reason
    assert decision.applied_policy == DEPLOY_STAGE_POLICY
    assert decision.business_facts_considered == (expected_fact_id,)
    assert decision.aggregate_changes == ()


def test_deploy_begin_with_no_facts_at_all_records_the_merge_leg() -> None:
    # The fully-absent observation (no kwargs at all) is row 5 with the merge
    # leg unmet — recorded, not swallowed.
    new_state, decision = drive_deploy_stage(WorkflowState.MERGING)
    assert new_state is WorkflowState.MERGING
    assert decision.reason == "merge not completed"
    assert decision.business_facts_considered == (
        "deploy-fact:unknown-merge-completed:no-deploy-target",
    )


# --- Deploy stage row 6: any other state → 'not in a deploy stage' --------------


def test_other_states_are_not_deploy_stages() -> None:
    deploy_stages = {DEPLOY_STAGE_BEGIN_SOURCE, DEPLOY_STAGE_FINISH_SOURCE}
    others = [state for state in STATES if state not in deploy_stages]
    assert others  # sanity: rows 1–5 do not cover the whole table
    for state in others:
        new_state, decision = drive_deploy_stage(
            state,
            **_deploy_begin_kwargs(),
            deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
        )
        assert new_state is state
        assert decision.previous_state is state
        assert decision.resulting_state is state
        assert decision.reason == "not in a deploy stage"
        assert decision.applied_policy == DEPLOY_STAGE_POLICY
        assert decision.aggregate_changes == ()


def test_deploy_driver_never_requests_the_failed_state() -> None:
    # FAILED is never *requested* — exhaustively, over the full state × fact
    # grid the driver can observe. A workflow already at FAILED (or COMPLETED)
    # is merely held there by row 6; the driver never moves anything INTO it.
    for state in STATES:
        for merge_completed in (None, False, True):
            for deploy_target_ref in (None, "", "refs/tags/v1"):
                for deployment_status in (None, "succeeded", "failed", "unknown"):
                    new_state, decision = drive_deploy_stage(
                        state,
                        merge_completed=merge_completed,
                        deploy_target_ref=deploy_target_ref,
                        deployment_status=deployment_status,
                    )
                    assert new_state in (
                        state,
                        WorkflowState.DEPLOYING,
                        WorkflowState.COMPLETED,
                    )
                    if state is not WorkflowState.FAILED:
                        assert new_state is not WorkflowState.FAILED
                        assert decision.resulting_state is not WorkflowState.FAILED


def test_deploy_driver_has_no_failed_code_path() -> None:
    # Structural guarantee: the deploy driver's executable code never names the
    # FAILED *state* — row 2 defers failure handling to the landed
    # ShouldFailPolicy (SFP-144). Docstring stripped first so only real code is
    # inspected; DEPLOYMENT_FAILED_STATUS (an observation vocabulary value) is
    # of course present, which is exactly why the check targets WorkflowState.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    def _body_source(func: object) -> str:
        func_def = ast.parse(inspect.getsource(func)).body[0]
        assert isinstance(func_def, ast.FunctionDef)
        body_nodes = func_def.body
        if (
            body_nodes
            and isinstance(body_nodes[0], ast.Expr)
            and isinstance(body_nodes[0].value, ast.Constant)
        ):
            body_nodes = body_nodes[1:]
        return "\n".join(ast.unparse(node) for node in body_nodes)

    for func in (
        transitions_module.drive_deploy_stage,
        transitions_module._drive_deploy_begin,
        transitions_module._drive_deploy_finish,
    ):
        assert "WorkflowState.FAILED" not in _body_source(func)


# --- Merge wait: MERGING → WAITING_FOR_USER (ID-024) ---------------------------


def test_approval_required_parks_in_waiting_for_user() -> None:
    new_state, decision = drive_merge_wait(
        WorkflowState.MERGING,
        approval_required_fact=True,
    )
    assert new_state is WorkflowState.WAITING_FOR_USER
    assert decision.previous_state is WorkflowState.MERGING
    assert decision.resulting_state is WorkflowState.WAITING_FOR_USER
    assert decision.previous_state_name == "MERGING"
    assert decision.resulting_state_name == "WAITING_FOR_USER"
    assert "ID-024" in decision.reason
    assert decision.applied_policy == MERGE_WAIT_POLICY
    assert decision.business_facts_considered == ("approval-required-fact:true",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)


def test_merge_wait_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        MERGE_WAIT_SOURCE,
        MERGE_WAIT_TARGET,
        reason="probe",
        applied_policy=MERGE_WAIT_POLICY,
    )
    _, via_driver = drive_merge_wait(
        WorkflowState.MERGING,
        approval_required_fact=True,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == MERGE_WAIT_POLICY == via_engine.applied_policy


def test_approval_not_required_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_merge_wait(
        WorkflowState.MERGING,
        approval_required_fact=False,
    )
    assert new_state is WorkflowState.MERGING
    assert decision.previous_state is WorkflowState.MERGING
    assert decision.resulting_state is WorkflowState.MERGING
    assert decision.reason == "approval not required"
    assert decision.applied_policy == MERGE_WAIT_POLICY
    assert decision.business_facts_considered == ("approval-required-fact:false",)
    assert decision.aggregate_changes == ()


def test_absent_approval_required_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_merge_wait(WorkflowState.MERGING)
    assert new_state is WorkflowState.MERGING
    assert decision.resulting_state is WorkflowState.MERGING
    assert decision.reason == "no approval-required fact"
    assert decision.applied_policy == MERGE_WAIT_POLICY
    assert decision.business_facts_considered == ("approval-required-fact:absent",)
    assert decision.aggregate_changes == ()


def test_merge_wait_is_never_resolved() -> None:
    # The driver never resolves the wait. Structurally, its only requested
    # target is WAITING_FOR_USER — it cannot request any exit edge — and
    # behaviorally an approval-required fact observed while already parked is a
    # §8.8 non-move (there is no WAITING_FOR_USER self-edge in the table), so
    # the workflow stays parked until the user decides.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    func_def = ast.parse(
        inspect.getsource(transitions_module.drive_merge_wait),
    ).body[0]
    assert isinstance(func_def, ast.FunctionDef)
    body = "\n".join(ast.unparse(node) for node in func_def.body)
    # The only transition target the code names is the parking target.
    assert "MERGE_WAIT_TARGET" in body
    assert "DEPLOY_STAGE_BEGIN_TARGET" not in body
    assert "MERGE_STAGE_TARGET" not in body

    with pytest.raises(IllegalTransitionError) as excinfo:
        drive_merge_wait(WorkflowState.WAITING_FOR_USER, approval_required_fact=True)
    assert excinfo.value.current_state is WorkflowState.WAITING_FOR_USER
    assert excinfo.value.attempted_target is MERGE_WAIT_TARGET


def test_merge_wait_non_move_is_deterministic() -> None:
    _, first = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=False)
    _, second = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=False)
    assert first == second
    assert first.to_json() == second.to_json()


def test_merge_wait_decision_is_immutable() -> None:
    _, decision = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=True)
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_approval_required_from_wrong_state_raises() -> None:
    wrong_states = _states_where_the_move_is_table_illegal(MERGE_WAIT_TARGET)
    assert wrong_states == [
        WorkflowState.COMPLETED,  # terminal
        WorkflowState.FAILED,  # terminal
        WorkflowState.WAITING_FOR_USER,  # no self-edge — never exits the wait
    ]
    # The ID-024 parking edge is MERGING→WAITING_FOR_USER. Every *other* active
    # state may also park (the SFP-137 table lets any active state wait,
    # ID-069), so the driver does not raise there — the table is the sole
    # authority and this driver requests the same target from them.
    assert WorkflowState.MERGING not in wrong_states  # the ID-024 edge
    assert WorkflowState.READY_FOR_MERGE not in wrong_states  # any active state
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_merge_wait(state, approval_required_fact=True)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is MERGE_WAIT_TARGET


# --- No new states, no table edits ----------------------------------------------


def test_drivers_add_no_workflow_states_and_use_existing_edges() -> None:
    assert len(STATES) == 10
    for edge in (
        (MERGE_STAGE_SOURCE, MERGE_STAGE_TARGET),
        (DEPLOY_STAGE_BEGIN_SOURCE, DEPLOY_STAGE_BEGIN_TARGET),
        (DEPLOY_STAGE_FINISH_SOURCE, DEPLOY_STAGE_FINISH_TARGET),
        (MERGE_WAIT_SOURCE, MERGE_WAIT_TARGET),
    ):
        assert edge[1] in TRANSITIONS[edge[0]]


def test_all_final_stage_edges_exist_in_the_landed_table() -> None:
    assert MERGE_STAGE_SOURCE is WorkflowState.READY_FOR_MERGE
    assert MERGE_STAGE_TARGET is WorkflowState.MERGING
    assert DEPLOY_STAGE_BEGIN_SOURCE is WorkflowState.MERGING
    assert DEPLOY_STAGE_BEGIN_TARGET is WorkflowState.DEPLOYING
    assert DEPLOY_STAGE_FINISH_SOURCE is WorkflowState.DEPLOYING
    assert DEPLOY_STAGE_FINISH_TARGET is WorkflowState.COMPLETED
    assert MERGE_WAIT_SOURCE is WorkflowState.MERGING
    assert MERGE_WAIT_TARGET is WorkflowState.WAITING_FOR_USER


def test_no_driver_constructs_a_move_decision_directly() -> None:
    # All moves route through the SFP-137 engine — no driver may construct a
    # *move* WorkflowDecision itself. Same-state §8.8 records are the
    # deliberate exception (no state has a self-edge in TRANSITIONS).
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    assert ast is not None  # (import hoisted for the helper below)

    def _direct_workflow_decision_calls(func_def: ast.FunctionDef) -> list[ast.Call]:
        calls: list[ast.Call] = []
        for node in ast.walk(func_def):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "WorkflowDecision")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "WorkflowDecision")
            ):
                calls.append(node)
        return calls

    def _keyword_expr(node: ast.Call, arg: str) -> str:
        matches = [kw for kw in node.keywords if kw.arg == arg]
        assert matches, f"WorkflowDecision call missing {arg}"
        return ast.unparse(matches[0].value)

    for name in (
        "drive_merge_stage",
        "drive_deploy_stage",
        "_drive_deploy_begin",
        "_drive_deploy_finish",
        "drive_merge_wait",
    ):
        func = getattr(transitions_module, name)
        parsed = ast.parse(inspect.getsource(func)).body[0]
        assert isinstance(parsed, ast.FunctionDef)
        body = "\n".join(ast.unparse(node) for node in parsed.body)
        # A leaf helper delegates to the engine; the dispatcher delegates to
        # its helpers — either way the module routes the move, not constructs it.
        if "transition(" not in body:
            helpers = []
            for call in ast.walk(parsed):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                    continue
                callee = getattr(transitions_module, call.func.id, None)
                if callee is None:
                    continue
                callee_def = ast.parse(inspect.getsource(callee)).body[0]
                callee_body = "\n".join(ast.unparse(n) for n in callee_def.body)
                if "transition(" in callee_body:
                    helpers.append(call.func.id)
            assert helpers, f"{name} does not route through the engine"
        for call in _direct_workflow_decision_calls(parsed):
            assert _keyword_expr(call, "previous_state") == "current_state", (
                f"{name} constructs a MOVE decision directly"
            )
            assert _keyword_expr(call, "resulting_state") == "current_state", (
                f"{name} constructs a MOVE decision directly"
            )


# --- Purity + determinism (AP-011) ----------------------------------------------


def test_all_three_drivers_are_deterministic() -> None:
    merge_first = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=APPROVAL_FACT,
    )
    merge_second = drive_merge_stage(
        WorkflowState.READY_FOR_MERGE,
        approval_fact=APPROVAL_FACT,
    )
    assert merge_first == merge_second

    deploy_begin_first = drive_deploy_stage(
        WorkflowState.MERGING,
        **_deploy_begin_kwargs(),
    )
    deploy_begin_second = drive_deploy_stage(
        WorkflowState.MERGING,
        **_deploy_begin_kwargs(),
    )
    assert deploy_begin_first == deploy_begin_second

    deploy_finish_first = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    deploy_finish_second = drive_deploy_stage(
        WorkflowState.DEPLOYING,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    assert deploy_finish_first == deploy_finish_second

    wait_first = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=True)
    wait_second = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=True)
    assert wait_first == wait_second

    held_first = drive_merge_wait(WorkflowState.MERGING)
    held_second = drive_merge_wait(WorkflowState.MERGING)
    assert held_first == held_second


def test_all_three_drivers_perform_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert (
        drive_merge_stage(
            WorkflowState.READY_FOR_MERGE,
            approval_fact=APPROVAL_FACT,
        )[0]
        is WorkflowState.MERGING
    )
    assert (
        drive_deploy_stage(
            WorkflowState.MERGING,
            **_deploy_begin_kwargs(),
        )[0]
        is WorkflowState.DEPLOYING
    )
    assert (
        drive_deploy_stage(
            WorkflowState.DEPLOYING,
            deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
        )[0]
        is WorkflowState.COMPLETED
    )
    assert (
        drive_merge_wait(WorkflowState.MERGING, approval_required_fact=True)[0]
        is WorkflowState.WAITING_FOR_USER
    )


def test_bus_is_not_touched_by_the_drivers() -> None:
    # The bus stays an injected seam owned by WorkflowTransitionPublisher; the
    # pure driver module must not reference messaging machinery at all.
    import orchestrator.domain.workflow.transitions as transitions_module

    source = inspect.getsource(transitions_module)
    assert "sfp_messaging" not in source
    assert "MessageBus" not in source
    assert "publish" not in source


def test_merge_wait_does_not_import_the_profile_machinery() -> None:
    # The approval-required fact is consumed as an evaluated bool — the module
    # must not import the ID-067 profile set or any policy module.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    imports = [
        node
        for node in ast.parse(inspect.getsource(transitions_module)).body
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    rendered = "\n".join(ast.unparse(node) for node in imports)
    assert "REQUIRES_HUMAN_APPROVAL" not in rendered
    assert "ValidationProfile" not in rendered
    assert "policies" not in rendered
    assert "sfp_contracts" not in rendered


# --- End-to-end over the landed driver set --------------------------------------


def test_full_stage_chain_merge_to_completed_via_the_drivers() -> None:
    # End-to-end over the final tail: approval → merge → deploy → complete.
    state: WorkflowState = WorkflowState.READY_FOR_MERGE

    state, d1 = drive_merge_stage(state, approval_fact=APPROVAL_FACT)
    assert state is WorkflowState.MERGING
    assert d1.resulting_state is WorkflowState.MERGING

    state, d2 = drive_deploy_stage(state, **_deploy_begin_kwargs())
    assert state is WorkflowState.DEPLOYING
    assert d2.resulting_state is WorkflowState.DEPLOYING

    state, d3 = drive_deploy_stage(
        state,
        deployment_status=DEPLOYMENT_SUCCEEDED_STATUS,
    )
    assert state is WorkflowState.COMPLETED
    assert d3.resulting_state is WorkflowState.COMPLETED

    # Every decision in the chain is a frozen §8.5 record with string states.
    for decision in (d1, d2, d3):
        assert isinstance(decision, WorkflowDecision)
        assert decision.previous_state_name
        assert decision.resulting_state_name


def test_parked_workflow_can_wait_indefinitely_without_degrading() -> None:
    # ID-024: the parked workflow waits for the user's decision — no stage fact
    # here moves it, and repeated observations never degrade it to FAILED.
    # WAITING_FOR_USER has table edges to the active stages (resume), so the
    # *table* permits leaving; what this asserts is that no stage *fact* this
    # module owns does — the deploy driver records its row-6 non-move, and the
    # merge-wait driver raises on the (table-illegal, no self-edge) re-park
    # rather than silently moving.
    state = WorkflowState.MERGING
    state, parked = drive_merge_wait(state, approval_required_fact=True)
    assert state is WorkflowState.WAITING_FOR_USER
    for _ in range(3):
        deploy_held = drive_deploy_stage(state, **_deploy_begin_kwargs())
        assert deploy_held[0] is WorkflowState.WAITING_FOR_USER
        assert deploy_held[1].resulting_state is WorkflowState.WAITING_FOR_USER
        assert deploy_held[1].resulting_state is not WorkflowState.FAILED
        assert deploy_held[1].reason == "not in a deploy stage"

        with pytest.raises(IllegalTransitionError):
            drive_merge_wait(state, approval_required_fact=True)
