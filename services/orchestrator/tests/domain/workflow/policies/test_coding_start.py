"""Tests for the coding-start policy (MAS §8.14, SFP-143).

Covers: every routing branch (the admitted+capacity move, wrong state, absent
fact, non-admitted fact, no capacity); evaluation through the landed SFP-142
engine (no forked engine types); the fact model's typed shape (frozen,
``extra='forbid'``); name-only command carrying (the landed ``ExecuteCodingJob``
payload class name, never an emitted command); and determinism (identical
inputs → identical outcomes).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    CodingStartFact,
    CodingStartPolicy,
)
from orchestrator.domain.workflow.policies.coding_start import (
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

POLICY = CodingStartPolicy()
READY = WorkflowState.READY_FOR_CODING


def _facts(*, admitted: bool = True, capacity: bool = True) -> tuple[str, ...]:
    """Render a coding-start fact into the engine's string vocabulary."""
    return CodingStartFact(
        start_request_admitted=admitted, capacity_available=capacity
    ).to_fact_strings()


# --- The move -----------------------------------------------------------------


def test_admitted_fact_with_capacity_starts_coding() -> None:
    outcome = evaluate(POLICY, READY, _facts(), policy_name=POLICY_NAME)
    assert outcome.no_transition is False
    assert outcome.target_state is WorkflowState.CODING_IN_PROGRESS
    assert outcome.target_state_name == WorkflowState.CODING_IN_PROGRESS.name
    assert outcome.command_names == (COMMAND_NAME,)
    assert outcome.reason


def test_the_referenced_command_is_the_landed_execute_coding_job_name() -> None:
    # §8.6 / MAS §12.9: the outcome carries the *name* of the landed payload
    # class from sfp_contracts.commands — never a new or invented name.
    from sfp_contracts.commands import ExecuteCodingJob

    assert COMMAND_NAME == ExecuteCodingJob.__name__ == "ExecuteCodingJob"


def test_policy_implements_the_engine_protocol_shape() -> None:
    # The protocol's exact decide() signature (state, business_facts).
    verdict = POLICY.decide(READY, _facts())
    assert verdict.target_state is TARGET_STATE
    assert POLICY.decide(READY, ()).no_transition is True


# --- Every NO_TRANSITION branch and its reason --------------------------------


def test_wrong_state_is_a_recorded_no_transition() -> None:
    for state in (s for s in WorkflowState if s is not SOURCE_STATE):
        outcome = evaluate(POLICY, state, _facts(), policy_name=POLICY_NAME)
        assert outcome.no_transition is True, state
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            "not READY_FOR_CODING: the coding-start policy applies only to tickets ready for coding"
        )


def test_absent_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, READY, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert "no coding-start fact" in outcome.reason


def test_non_admitted_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, READY, _facts(admitted=False), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert "not admitted" in outcome.reason


def test_no_capacity_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, READY, _facts(capacity=False), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert "capacity" in outcome.reason


def test_non_admitted_and_no_capacity_reports_admission_first() -> None:
    # Deterministic ordering: the earliest failing precondition names itself.
    outcome = evaluate(
        POLICY, READY, _facts(admitted=False, capacity=False), policy_name=POLICY_NAME
    )
    assert outcome.no_transition is True
    assert "not admitted" in outcome.reason


def test_no_transition_carries_no_commands() -> None:
    for facts in ((), _facts(admitted=False), _facts(capacity=False)):
        outcome = evaluate(POLICY, READY, facts, policy_name=POLICY_NAME)
        assert outcome.no_transition is True
        assert outcome.command_names == ()


# --- The typed fact model ------------------------------------------------------


def test_coding_start_fact_is_frozen_and_forbids_unknown_fields() -> None:
    fact = CodingStartFact(start_request_admitted=True, capacity_available=True)
    # Frozen: assignment raises (pydantic's documented frozen behaviour —
    # model_copy(update=…) is an explicit copy path, not mutation).
    with pytest.raises(ValidationError):
        fact.start_request_admitted = False  # type: ignore[misc]
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        CodingStartFact(  # type: ignore[call-arg]
            start_request_admitted=True, capacity_available=True, extra="x"
        )
    # And the original is untouched by any attempted update.
    assert fact.start_request_admitted is True and fact.capacity_available is True


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    for admitted in (True, False):
        for capacity in (True, False):
            fact = CodingStartFact(start_request_admitted=admitted, capacity_available=capacity)
            lifted = POLICY.parse_fact(fact.to_fact_strings())
            assert lifted == fact, (admitted, capacity)


def test_parse_fact_returns_none_when_no_coding_start_fact_present() -> None:
    assert POLICY.parse_fact(()) is None
    assert POLICY.parse_fact(("review-outcome-fact:review_status:APPROVED", "other")) is None


def test_parse_fact_is_order_independent_and_ignores_unknown_facts() -> None:
    rendered = list(_facts(admitted=False, capacity=True))
    reversed_facts = tuple(reversed(rendered)) + ("unrelated-fact:x",)
    assert POLICY.parse_fact(reversed_facts) == CodingStartFact(
        start_request_admitted=False, capacity_available=True
    )


def test_local_fact_vocabulary_matches_the_policy_module_constants() -> None:
    from orchestrator.domain.workflow.policies.facts import CODING_START_FACT_KIND

    rendered = _facts()
    assert all(s.startswith(f"{CODING_START_FACT_KIND}:") for s in rendered)


# --- Purity: no bus, no clock, no I/O, no execution path -----------------------


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
    hold = evaluate(POLICY, READY, _facts(admitted=False), policy_name=POLICY_NAME)
    assert move.target_state is WorkflowState.CODING_IN_PROGRESS
    assert hold.no_transition is True


def test_module_source_references_no_bus_or_transport() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.coding_start")


# --- Determinism ---------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = (
        (READY, _facts()),
        (READY, _facts(admitted=False)),
        (READY, _facts(capacity=False)),
        (READY, ()),
        (WorkflowState.MERGING, _facts()),
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
    assert payload["current_state"] == "READY_FOR_CODING"
    assert payload["target_state"] == "CODING_IN_PROGRESS"
    assert payload["command_names"] == ["ExecuteCodingJob"]
    held = evaluate(POLICY, READY, _facts(admitted=False), policy_name=POLICY_NAME)
    assert json.loads(held.to_json())["target_state"] == NO_TRANSITION
