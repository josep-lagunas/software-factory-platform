"""Tests for the deploy-begin policy (MAS §8.14, SFP-144).

Covers: every decision-table row (the 4-row table over state /
``merge_completed`` / ``deploy_target_ref``, in the PRSpec's order); exhaustive
wrong-state coverage; evaluation through the landed SFP-142 engine; the typed
fact model (frozen, ``extra='forbid'``); name-only command carrying (the
landed ``NotifyUser`` payload class name — deciding, never deploying); and
determinism.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    DeployBeginFact,
    DeployBeginPolicy,
)
from orchestrator.domain.workflow.policies.deploy_begin import (
    COMMAND_NAME,
    POLICY_NAME,
    SOURCE_STATE,
    TARGET_STATE,
)
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    PolicyOutcome,
    evaluate,
)
from orchestrator.domain.workflow.states import WorkflowState
from pydantic import ValidationError

POLICY = DeployBeginPolicy()
MERGING = WorkflowState.MERGING

#: A concrete, representative deploy target ref. Its *content* is irrelevant
#: to the policy (it asks only whether one is present); a realistic ref keeps
#: the tests honest about the rendering.
_REF = "refs/heads/main"


def _facts(*, merge_completed: bool = True, deploy_target_ref: str = _REF) -> tuple[str, ...]:
    return DeployBeginFact(
        merge_completed=merge_completed, deploy_target_ref=deploy_target_ref
    ).to_fact_strings()


# --- The 4-row decision table, row by row ----------------------------------------


def test_row_1_any_state_other_than_merging_is_a_recorded_no_move() -> None:
    # Row 1: the state gate dominates — regardless of the facts.
    for state in (s for s in WorkflowState if s is not SOURCE_STATE):
        for facts in (
            _facts(),
            _facts(merge_completed=False),
            _facts(deploy_target_ref=""),
            (),
        ):
            outcome = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert outcome.no_transition is True, (state, facts)
            assert outcome.target_state is None
            assert outcome.target_state_name == NO_TRANSITION
            assert outcome.reason == (
                "not MERGING: the deploy-begin policy applies only while merging, before deploy"
            )


def test_row_2_merge_not_completed_is_a_recorded_no_move() -> None:
    # Row 2: MERGING but the merge has not completed — even with a target ref.
    outcome = evaluate(
        POLICY,
        MERGING,
        _facts(merge_completed=False, deploy_target_ref=_REF),
        policy_name=POLICY_NAME,
    )
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.target_state_name == NO_TRANSITION
    assert outcome.reason == "merge not completed: deployment waits for the merge"
    assert outcome.command_names == ()
    # The ref's absence is not named: the merge row dominates, in table order.
    assert "deploy target ref" not in outcome.reason


def test_row_3_no_target_ref_is_a_recorded_no_move() -> None:
    # Row 3: MERGING, merge completed, but the ref is empty.
    outcome = evaluate(POLICY, MERGING, _facts(deploy_target_ref=""), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.target_state_name == NO_TRANSITION
    assert outcome.reason == (
        "no deploy target ref set: deployment cannot begin without a target ref"
    )
    assert outcome.command_names == ()
    assert "merge not completed" not in outcome.reason


def test_row_4_merge_completed_with_a_target_ref_moves_to_deploying() -> None:
    outcome = evaluate(POLICY, MERGING, _facts(), policy_name=POLICY_NAME)
    assert outcome.no_transition is False
    assert outcome.target_state is TARGET_STATE
    assert outcome.target_state is WorkflowState.DEPLOYING
    assert outcome.target_state_name == "DEPLOYING"
    assert outcome.reason == "merge completed and a deploy target ref is set: begin deployment"
    assert outcome.command_names == (COMMAND_NAME,)


@pytest.mark.parametrize("ref", ["refs/heads/main", "v1.2.3", "a", " "])
def test_row_4_holds_for_any_non_empty_ref(ref: str) -> None:
    # The ref is carried as data: any non-empty string satisfies row 4 — the
    # policy never interprets or resolves it.
    outcome = evaluate(POLICY, MERGING, _facts(deploy_target_ref=ref), policy_name=POLICY_NAME)
    assert outcome.no_transition is False
    assert outcome.target_state is WorkflowState.DEPLOYING


def test_the_table_rows_are_mutually_exclusive_in_order() -> None:
    # The deterministic evaluation order: state → merge → ref → move. A case
    # matching an earlier row never reports a later row's reason.
    cases = (
        (WorkflowState.DEPLOYING, _facts(), "not MERGING"),
        (MERGING, _facts(merge_completed=False), "merge not completed"),
        (MERGING, _facts(deploy_target_ref=""), "no deploy target ref"),
    )
    for state, facts, expected_start in cases:
        outcome = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        assert outcome.reason.startswith(expected_start), (state, facts)


# --- Name-only command carrying ----------------------------------------------------


def test_the_referenced_command_is_the_landed_notify_user_name() -> None:
    from sfp_contracts.commands import NotifyUser

    assert COMMAND_NAME == NotifyUser.__name__ == "NotifyUser"


# --- Absent fact -------------------------------------------------------------------


def test_absent_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, MERGING, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.command_names == ()
    assert outcome.reason == ("no deploy-begin fact observed: the workflow stays at this stage")


def test_no_move_ever_carries_a_command() -> None:
    for facts in ((), _facts(merge_completed=False), _facts(deploy_target_ref="")):
        outcome = evaluate(POLICY, MERGING, facts, policy_name=POLICY_NAME)
        assert outcome.no_transition is True
        assert outcome.command_names == ()


# --- The typed fact model -----------------------------------------------------------


def test_deploy_begin_fact_is_frozen_and_forbids_unknown_fields() -> None:
    fact = DeployBeginFact(merge_completed=True, deploy_target_ref=_REF)
    # Frozen: assignment raises (pydantic's documented frozen behaviour —
    # model_copy(update=…) is an explicit copy path, not mutation).
    with pytest.raises(ValidationError):
        fact.merge_completed = False  # type: ignore[misc]
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        DeployBeginFact(merge_completed=True, deploy_target_ref=_REF, extra="x")  # type: ignore[call-arg]
    assert fact.merge_completed is True and fact.deploy_target_ref == _REF


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    for merge_completed in (True, False):
        for ref in (_REF, ""):
            fact = DeployBeginFact(merge_completed=merge_completed, deploy_target_ref=ref)
            assert POLICY.parse_fact(fact.to_fact_strings()) == fact


def test_an_empty_ref_renders_distinctly_from_an_absent_field() -> None:
    # The empty ref renders as the bare prefix; the policy treats it as "not
    # set" (row 3) while still recognizing the fact as present.
    rendered = _facts(deploy_target_ref="")
    assert "deploy-begin-fact:deploy_target_ref:" in rendered
    lifted = POLICY.parse_fact(rendered)
    assert lifted is not None
    assert lifted.deploy_target_ref == ""


def test_parse_fact_returns_none_when_no_deploy_begin_fact_present() -> None:
    assert POLICY.parse_fact(()) is None
    assert POLICY.parse_fact(("user-approval-fact:validation_profile:LEVEL_1_INTERNAL",)) is None


def test_parse_fact_is_order_independent_and_ignores_unknown_facts() -> None:
    rendered = list(_facts(merge_completed=False, deploy_target_ref=_REF))
    facts = ("unrelated-fact:x",) + tuple(reversed(rendered))
    assert POLICY.parse_fact(facts) == DeployBeginFact(
        merge_completed=False, deploy_target_ref=_REF
    )


# --- Purity -------------------------------------------------------------------------


def test_module_never_touches_the_bus_or_executes_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(call: str) -> object:
        def _raise(*args: object, **kwargs: object) -> object:
            raise AssertionError(f"{call} called")

        return _raise

    import builtins
    import socket
    import time

    monkeypatch.setattr(builtins, "open", _forbidden("open"))
    monkeypatch.setattr(socket.socket, "connect", _forbidden("socket.connect"))
    monkeypatch.setattr(time, "time", _forbidden("time.time"))

    move = evaluate(POLICY, MERGING, _facts(), policy_name=POLICY_NAME)
    hold = evaluate(POLICY, MERGING, _facts(merge_completed=False), policy_name=POLICY_NAME)
    assert move.target_state is WorkflowState.DEPLOYING
    assert hold.no_transition is True


def test_module_source_references_no_bus_or_transport() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.deploy_begin")


# --- Determinism ---------------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = (
        (MERGING, _facts()),
        (MERGING, _facts(merge_completed=False)),
        (MERGING, _facts(deploy_target_ref="")),
        (MERGING, ()),
        (WorkflowState.READY_FOR_MERGE, _facts()),
    )
    for state, facts in cases:
        first = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        for _ in range(5):
            repeat = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert repeat == first, (state, facts)
            assert repeat.to_json() == first.to_json()


def test_outcome_is_serializable_with_plain_string_states() -> None:
    import json

    outcome: PolicyOutcome = evaluate(POLICY, MERGING, _facts(), policy_name=POLICY_NAME)
    payload = json.loads(outcome.to_json())
    assert payload["current_state"] == "MERGING"
    assert payload["target_state"] == "DEPLOYING"
    assert payload["command_names"] == ["NotifyUser"]
    held = evaluate(POLICY, MERGING, _facts(merge_completed=False), policy_name=POLICY_NAME)
    assert json.loads(held.to_json())["target_state"] == NO_TRANSITION
