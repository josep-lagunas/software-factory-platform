"""Tests for the merge-readiness policy (MAS §8.14 / ID-072, SFP-143).

Covers: every routing branch (the all-green move to ``MERGING``, wrong state,
absent fact, each single missing fact, both missing); the recorded reason
naming *exactly* which fact is missing; evaluation through the landed SFP-142
engine; the typed fact model (frozen, ``extra='forbid'``); name-only command
carrying (the landed ``RequestMerge`` payload class name — deciding, never
merging); and determinism.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    MergeReadyFact,
    MergeReadyPolicy,
)
from orchestrator.domain.workflow.policies.merge_ready import (
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

POLICY = MergeReadyPolicy()
READY = WorkflowState.READY_FOR_MERGE


def _facts(*, approved: bool = True, green: bool = True) -> tuple[str, ...]:
    return MergeReadyFact(
        pr_review_approved_for_head=approved, ci_gates_green=green
    ).to_fact_strings()


# --- The move ------------------------------------------------------------------


def test_all_green_moves_to_merging_referencing_request_merge() -> None:
    outcome = evaluate(POLICY, READY, _facts(), policy_name=POLICY_NAME)
    assert outcome.no_transition is False
    assert outcome.target_state is WorkflowState.MERGING
    assert outcome.target_state is TARGET_STATE
    assert outcome.command_names == (COMMAND_NAME,)
    assert outcome.reason


def test_the_referenced_command_is_the_landed_request_merge_name() -> None:
    from sfp_contracts.commands import RequestMerge

    assert COMMAND_NAME == RequestMerge.__name__ == "RequestMerge"


# --- Every NO_TRANSITION branch names the missing fact exactly ------------------


def test_wrong_state_is_a_recorded_no_transition() -> None:
    for state in (s for s in WorkflowState if s is not SOURCE_STATE):
        outcome = evaluate(POLICY, state, _facts(), policy_name=POLICY_NAME)
        assert outcome.no_transition is True, state
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            "not READY_FOR_MERGE: the merge-ready policy applies only after approval"
        )


def test_absent_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, READY, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert "no merge-ready fact" in outcome.reason


def test_missing_pr_approval_is_named_exactly() -> None:
    outcome = evaluate(POLICY, READY, _facts(approved=False), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.reason == "merge not ready: missing fact pr_review_approved_for_head"
    assert "ci_gates_green" not in outcome.reason


def test_missing_ci_green_is_named_exactly() -> None:
    outcome = evaluate(POLICY, READY, _facts(green=False), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.reason == "merge not ready: missing fact ci_gates_green"
    assert "pr_review_approved_for_head" not in outcome.reason


def test_both_missing_facts_are_both_named_in_declaration_order() -> None:
    outcome = evaluate(POLICY, READY, _facts(approved=False, green=False), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.reason == (
        "merge not ready: missing facts pr_review_approved_for_head and ci_gates_green"
    )


def test_no_transition_carries_no_commands() -> None:
    for facts in ((), _facts(approved=False), _facts(green=False)):
        outcome = evaluate(POLICY, READY, facts, policy_name=POLICY_NAME)
        assert outcome.no_transition is True
        assert outcome.command_names == ()


# --- The typed fact model -------------------------------------------------------


def test_merge_ready_fact_is_frozen_and_forbids_unknown_fields() -> None:
    fact = MergeReadyFact(pr_review_approved_for_head=True, ci_gates_green=True)
    # Frozen: assignment raises (pydantic's documented frozen behaviour —
    # model_copy(update=…) is an explicit copy path, not mutation).
    with pytest.raises(ValidationError):
        fact.ci_gates_green = False  # type: ignore[misc]
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        MergeReadyFact(  # type: ignore[call-arg]
            pr_review_approved_for_head=True, ci_gates_green=True, extra="x"
        )
    assert fact.pr_review_approved_for_head is True and fact.ci_gates_green is True


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    for approved in (True, False):
        for green in (True, False):
            fact = MergeReadyFact(pr_review_approved_for_head=approved, ci_gates_green=green)
            assert POLICY.parse_fact(fact.to_fact_strings()) == fact


def test_parse_fact_returns_none_without_a_merge_ready_fact() -> None:
    assert POLICY.parse_fact(()) is None
    assert POLICY.parse_fact(("review-outcome-fact:review_status:APPROVED",)) is None


def test_parse_fact_is_order_independent_and_ignores_unknown_facts() -> None:
    facts = tuple(reversed(list(_facts(approved=False)))) + ("unrelated:x",)
    assert POLICY.parse_fact(facts) == MergeReadyFact(
        pr_review_approved_for_head=False, ci_gates_green=True
    )


# --- Purity: decide only — never merge, never touch the provider ----------------


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

    move = evaluate(POLICY, READY, _facts(), policy_name=POLICY_NAME)
    hold = evaluate(POLICY, READY, _facts(green=False), policy_name=POLICY_NAME)
    assert move.target_state is WorkflowState.MERGING
    assert hold.no_transition is True


def test_module_source_references_no_bus_git_or_merge_execution() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.merge_ready")


# --- Determinism ---------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = (
        (READY, _facts()),
        (READY, _facts(approved=False)),
        (READY, _facts(green=False)),
        (READY, _facts(approved=False, green=False)),
        (READY, ()),
        (WorkflowState.REVIEW_IN_PROGRESS, _facts()),
    )
    for state, facts in cases:
        first = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        for _ in range(5):
            repeat = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert repeat == first, (state, facts)
            assert repeat.to_json() == first.to_json()


def test_outcome_is_serializable_with_plain_string_states() -> None:
    import json

    outcome: PolicyOutcome = evaluate(POLICY, READY, _facts(), policy_name=POLICY_NAME)
    payload = json.loads(outcome.to_json())
    assert payload["target_state"] == "MERGING"
    assert payload["command_names"] == ["RequestMerge"]
    held = evaluate(POLICY, READY, _facts(approved=False), policy_name=POLICY_NAME)
    assert json.loads(held.to_json())["target_state"] == NO_TRANSITION
