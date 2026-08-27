"""Tests for the review-outcome policy (MAS §8.14 / ID-068, SFP-143).

Covers: every routing branch (APPROVED → READY_FOR_MERGE,
CHANGES_REQUESTED → the ID-068 rework loop, BLOCKED / NEEDS_HUMAN_DECISION →
recorded no-transition with the escalation command referenced); wrong state;
absent fact; evaluation through the landed SFP-142 engine; the typed fact
model (frozen, ``extra='forbid'``); the local ReviewStatus vocabulary matching
the landed contract enum one-for-one; name-only command carrying; ID-068's
never-FAILED / never-escalated-on-rework guarantee; and determinism.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    ReviewFact,
    ReviewStatus,
    ReviewSuccessPolicy,
)
from orchestrator.domain.workflow.policies.review_success import (
    ESCALATION_COMMAND_NAME,
    MERGE_TARGET,
    POLICY_NAME,
    REWORK_TARGET,
    SOURCE_STATE,
)
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    PolicyOutcome,
    evaluate,
)
from orchestrator.domain.workflow.states import WorkflowState
from pydantic import ValidationError

POLICY = ReviewSuccessPolicy()
IN_REVIEW = WorkflowState.REVIEW_IN_PROGRESS


def _facts(status: ReviewStatus) -> tuple[str, ...]:
    return ReviewFact(review_status=status).to_fact_strings()


# --- The moves -----------------------------------------------------------------


def test_approved_moves_to_ready_for_merge() -> None:
    outcome = evaluate(POLICY, IN_REVIEW, _facts(ReviewStatus.APPROVED), policy_name=POLICY_NAME)
    assert outcome.no_transition is False
    assert outcome.target_state is WorkflowState.READY_FOR_MERGE
    assert outcome.target_state is MERGE_TARGET
    assert outcome.reason == "review approved: ready for merge"


def test_changes_requested_drives_the_id068_rework_loop() -> None:
    outcome = evaluate(
        POLICY,
        IN_REVIEW,
        _facts(ReviewStatus.CHANGES_REQUESTED),
        policy_name=POLICY_NAME,
    )
    assert outcome.no_transition is False
    assert outcome.target_state is WorkflowState.CODING_IN_PROGRESS
    assert outcome.target_state is REWORK_TARGET


def test_rework_is_never_failed_and_never_escalated() -> None:
    # ID-068: rework is normal progression. The policy's only requestable
    # targets are the merge target and the rework target — there is no path to
    # FAILED, and the escalation command is never referenced on this branch.
    outcome = evaluate(
        POLICY,
        IN_REVIEW,
        _facts(ReviewStatus.CHANGES_REQUESTED),
        policy_name=POLICY_NAME,
    )
    assert outcome.target_state is not WorkflowState.FAILED
    assert ESCALATION_COMMAND_NAME not in outcome.command_names
    assert outcome.command_names == ()
    for state in WorkflowState:
        verdict = POLICY.decide(state, _facts(ReviewStatus.CHANGES_REQUESTED))
        assert verdict.target_state in (None, REWORK_TARGET)


def test_approval_carries_no_command_the_merge_policy_owns_that_reference() -> None:
    outcome = evaluate(POLICY, IN_REVIEW, _facts(ReviewStatus.APPROVED), policy_name=POLICY_NAME)
    assert outcome.command_names == ()


# --- The escalations: BLOCKED and NEEDS_HUMAN_DECISION ------------------------


@pytest.mark.parametrize(
    ("status", "expected_word"),
    [
        (ReviewStatus.BLOCKED, "blocked"),
        (ReviewStatus.NEEDS_HUMAN_DECISION, "human decision"),
    ],
)
def test_escalation_verdicts_hold_with_the_escalation_command_referenced(
    status: ReviewStatus, expected_word: str
) -> None:
    outcome = evaluate(POLICY, IN_REVIEW, _facts(status), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.target_state_name == NO_TRANSITION
    assert expected_word in outcome.reason
    # The escalation is a *reference only*: the command name is carried as
    # data, never dispatched. WAITING_FOR_USER is never requested here —
    # driving the user wait is SFP-141's concern.
    assert outcome.command_names == (ESCALATION_COMMAND_NAME,)
    assert outcome.target_state is not WorkflowState.WAITING_FOR_USER


def test_the_escalation_command_is_the_landed_request_user_input_name() -> None:
    # MAS §12.9: taken from what is landed, never invented. RequestUserInput
    # is the catalogue's ask-the-user command (the canonical term is User).
    from sfp_contracts.commands import RequestUserInput

    assert ESCALATION_COMMAND_NAME == RequestUserInput.__name__ == "RequestUserInput"


def test_escalation_reasons_name_the_verdict() -> None:
    blocked = evaluate(POLICY, IN_REVIEW, _facts(ReviewStatus.BLOCKED), policy_name=POLICY_NAME)
    needs = evaluate(
        POLICY,
        IN_REVIEW,
        _facts(ReviewStatus.NEEDS_HUMAN_DECISION),
        policy_name=POLICY_NAME,
    )
    assert blocked.reason != needs.reason
    assert "RequestUserInput" in blocked.reason
    assert "RequestUserInput" in needs.reason


# --- Wrong state and absent fact -----------------------------------------------


def test_wrong_state_is_a_recorded_no_transition_regardless_of_verdict() -> None:
    for state in (s for s in WorkflowState if s is not SOURCE_STATE):
        for status in ReviewStatus:
            outcome = evaluate(POLICY, state, _facts(status), policy_name=POLICY_NAME)
            assert outcome.no_transition is True, (state, status)
            assert outcome.target_state is None
            assert outcome.reason == (
                "not REVIEW_IN_PROGRESS: the review-outcome policy applies only during review"
            )


def test_absent_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, IN_REVIEW, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert "no review fact" in outcome.reason


# --- The typed fact model and vocabulary ---------------------------------------


def test_review_fact_is_frozen_and_forbids_unknown_fields() -> None:
    fact = ReviewFact(review_status=ReviewStatus.APPROVED)
    # Frozen: assignment raises (pydantic's documented frozen behaviour —
    # model_copy(update=…) is an explicit copy path, not mutation).
    with pytest.raises(ValidationError):
        fact.review_status = ReviewStatus.BLOCKED  # type: ignore[misc]
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        ReviewFact(review_status=ReviewStatus.APPROVED, extra="x")  # type: ignore[call-arg]
    assert fact.review_status is ReviewStatus.APPROVED


def test_local_review_status_matches_the_landed_contract_vocabulary() -> None:
    from sfp_contracts.agents.reviewer import ReviewStatus as LandedReviewStatus

    assert [s.value for s in ReviewStatus] == [s.value for s in LandedReviewStatus]
    assert {s.name for s in ReviewStatus} == {
        "APPROVED",
        "CHANGES_REQUESTED",
        "BLOCKED",
        "NEEDS_HUMAN_DECISION",
    }


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    for status in ReviewStatus:
        fact = ReviewFact(review_status=status)
        assert POLICY.parse_fact(fact.to_fact_strings()) == fact


def test_parse_review_status_returns_none_without_a_review_fact() -> None:
    assert POLICY.parse_review_status(()) is None
    assert POLICY.parse_review_status(("coding-start-fact:start_request_admitted:True",)) is None


# --- Purity --------------------------------------------------------------------


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

    for status in ReviewStatus:
        outcome = evaluate(POLICY, IN_REVIEW, _facts(status), policy_name=POLICY_NAME)
        assert outcome.reason


def test_module_source_references_no_bus_or_transport() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.review_success")


# --- Determinism ---------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = tuple(
        (state, _facts(status))
        for state in (IN_REVIEW, WorkflowState.READY_FOR_CODING)
        for status in ReviewStatus
    ) + ((IN_REVIEW, ()),)
    for state, facts in cases:
        first = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        for _ in range(5):
            repeat = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert repeat == first, (state, facts)
            assert repeat.to_json() == first.to_json()


def test_outcome_is_serializable_with_plain_string_states() -> None:
    import json

    outcome: PolicyOutcome = evaluate(
        POLICY, IN_REVIEW, _facts(ReviewStatus.CHANGES_REQUESTED), policy_name=POLICY_NAME
    )
    payload = json.loads(outcome.to_json())
    assert payload["target_state"] == "CODING_IN_PROGRESS"
    blocked = evaluate(POLICY, IN_REVIEW, _facts(ReviewStatus.BLOCKED), policy_name=POLICY_NAME)
    blocked_payload = json.loads(blocked.to_json())
    assert blocked_payload["target_state"] == NO_TRANSITION
    assert blocked_payload["command_names"] == ["RequestUserInput"]
