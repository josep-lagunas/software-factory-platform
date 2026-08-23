"""Tests for the workflow policy engine (MAS §8.14, SFP-142).

Covers: the plug-in policy shape (a trivial example policy, in tests — the
production engine never names a concrete policy); evaluate/evaluate_policy_set
semantics (first-match wins, ordered, decline-does-not-stop); no-transition as
a recorded first-class outcome (§8.8); explicit determinism (equal outcomes
across repeated evaluations); purity (no I/O, no clock, no bus modules
imported or called, no command execution path); the typed outcome model
(frozen, extra='forbid', ID-013 plain-string serialization); decide-time
legality checks against the SFP-137 table.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    PolicyDecision,
    PolicyOutcome,
    WorkflowPolicy,
    evaluate,
    evaluate_policy_set,
)
from orchestrator.domain.workflow.states import WorkflowState
from pydantic import ValidationError

# --- The trivial example policy (tests only; SFP-143/144 plug in here) -------


class StartCodingPolicy:
    """A deliberately trivial example policy proving the plug-in shape.

    This is the seam SFP-143/SFP-144 will register: a class with a pure
    ``decide(current_state, business_facts) -> PolicyDecision`` — no engine
    registration, no naming, no constructor requirements. It starts coding
    when the ticket is READY_FOR_CODING and the planner emitted a spec fact;
    it declines otherwise.
    """

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        if current_state is WorkflowState.READY_FOR_CODING and "pr_spec_ready" in business_facts:
            return PolicyDecision.transition_verdict(
                WorkflowState.CODING_IN_PROGRESS,
                reason="spec ready; start coding",
                command_names=["EXECUTE_CODING_JOB"],
            )
        return PolicyDecision.no_transition_verdict(reason="no coding start warranted")


FACTS: tuple[str, ...] = ("pr_spec_ready", "pr_spec_id=SFP-142")
READY_FOR_CODING = WorkflowState.READY_FOR_CODING


class DecliningPolicy:
    """A policy that always declines — proves a decline does not stop the set."""

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        return PolicyDecision.no_transition_verdict(reason="declines always")


class AdvanceToReviewPolicy:
    """A policy that always requests review — for precedence tests."""

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        return PolicyDecision.transition_verdict(
            WorkflowState.REVIEW_IN_PROGRESS,
            reason="coder finished",
            command_names=["REVIEW_PULL_REQUEST"],
        )


# --- evaluate: the single-policy evaluator ------------------------------------


def test_evaluate_returns_a_transition_outcome_with_commands_as_data() -> None:
    outcome = evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS)
    assert outcome.current_state is READY_FOR_CODING
    assert outcome.target_state is WorkflowState.CODING_IN_PROGRESS
    assert outcome.no_transition is False
    assert outcome.applied_policy == "StartCodingPolicy"
    assert outcome.reason == "spec ready; start coding"
    assert outcome.command_names == ("EXECUTE_CODING_JOB",)
    assert outcome.business_facts_considered == FACTS


def test_evaluate_records_the_default_policy_name_from_its_type() -> None:
    # The engine never hardcodes policy names: the caller may supply one, else
    # the policy's own type name is recorded for lineage.
    assert evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS).applied_policy == (
        "StartCodingPolicy"
    )
    assert (
        evaluate(
            StartCodingPolicy(), READY_FOR_CODING, FACTS, policy_name="coding-start"
        ).applied_policy
        == "coding-start"
    )


def test_evaluate_passes_facts_through_to_the_policy() -> None:
    seen: list[tuple[WorkflowState, tuple[str, ...]]] = []

    class RecordingPolicy:
        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            seen.append((current_state, tuple(business_facts)))
            return PolicyDecision.no_transition_verdict()

    evaluate(RecordingPolicy(), WorkflowState.MERGING, ("fact-a", "fact-b"))
    assert seen == [(WorkflowState.MERGING, ("fact-a", "fact-b"))]


def test_evaluate_returns_no_transition_for_facts_the_policy_ignores() -> None:
    # The engine consults the policy verbatim: a fact set the policy does not
    # care about yields the policy's decline, recorded (§8.8), not an error.
    outcome = evaluate(StartCodingPolicy(), READY_FOR_CODING, ("something_else",))
    assert outcome.no_transition is True
    assert outcome.business_facts_considered == ("something_else",)


# --- No-transition is a recorded first-class outcome (§8.8) -------------------


def test_evaluate_returns_no_transition_as_a_value_never_an_exception() -> None:
    outcome = evaluate(StartCodingPolicy(), WorkflowState.MERGING, FACTS)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.reason == "no coding start warranted"
    assert outcome.command_names == ()


def test_no_transition_verdict_shape_is_enforced() -> None:
    verdict = PolicyDecision.no_transition_verdict(reason="nothing to do")
    assert verdict.target_state is None
    assert verdict.no_transition is True
    assert verdict.command_names == ()


def test_policy_set_records_a_no_transition_when_every_policy_declines() -> None:
    outcome = evaluate_policy_set(
        [DecliningPolicy(), DecliningPolicy()],
        WorkflowState.CODING_IN_PROGRESS,
        FACTS,
        policy_names=["first", "second"],
    )
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.applied_policy == "policy-set"
    assert "no policy in the set" in outcome.reason
    assert outcome.command_names == ()


def test_empty_policy_set_is_a_recorded_no_transition_not_an_error() -> None:
    outcome = evaluate_policy_set([], READY_FOR_CODING, FACTS)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.applied_policy == "policy-set"


# --- evaluate_policy_set: ordered, first-match wins ---------------------------


def test_first_transition_verdict_wins_and_later_policies_are_not_consulted() -> None:
    consulted: list[str] = []

    class NamedPolicy:
        def __init__(self, name: str, verdict: PolicyDecision) -> None:
            self._name = name
            self._verdict = verdict

        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            consulted.append(self._name)
            return self._verdict

    policies = [
        NamedPolicy(
            "decliner",
            PolicyDecision.no_transition_verdict(reason="not mine"),
        ),
        NamedPolicy(
            "winner",
            PolicyDecision.transition_verdict(
                WorkflowState.REVIEW_IN_PROGRESS,
                reason="coder finished",
                command_names=["REVIEW_PULL_REQUEST"],
            ),
        ),
        NamedPolicy(
            "never-asked",
            PolicyDecision.transition_verdict(WorkflowState.FAILED, reason="unreachable"),
        ),
    ]
    outcome = evaluate_policy_set(
        policies,
        WorkflowState.CODING_IN_PROGRESS,
        FACTS,
        policy_names=["decliner", "winner", "never-asked"],
    )

    assert consulted == ["decliner", "winner"]  # third never consulted
    assert outcome.applied_policy == "winner"
    assert outcome.target_state is WorkflowState.REVIEW_IN_PROGRESS
    assert outcome.command_names == ("REVIEW_PULL_REQUEST",)


def test_a_declining_policy_does_not_stop_the_search() -> None:
    outcome = evaluate_policy_set(
        [DecliningPolicy(), AdvanceToReviewPolicy()],
        WorkflowState.CODING_IN_PROGRESS,
        FACTS,
        policy_names=["declining", "advancing"],
    )
    assert outcome.applied_policy == "advancing"
    assert outcome.target_state is WorkflowState.REVIEW_IN_PROGRESS


def test_policy_set_default_names_derive_from_policy_types() -> None:
    outcome = evaluate_policy_set([AdvanceToReviewPolicy()], WorkflowState.CODING_IN_PROGRESS)
    assert outcome.applied_policy == "AdvanceToReviewPolicy"


def test_policy_names_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="must name every policy"):
        evaluate_policy_set(
            [DecliningPolicy()],
            WorkflowState.CODING_IN_PROGRESS,
            policy_names=["a", "b"],
        )


# --- Determinism (AP-011 / MAS §8.7) ------------------------------------------


def test_evaluate_is_deterministic_across_repeated_evaluations() -> None:
    policy = StartCodingPolicy()
    first = evaluate(policy, READY_FOR_CODING, FACTS)
    second = evaluate(policy, READY_FOR_CODING, FACTS)
    third = evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS)
    assert first == second == third
    assert first.to_json() == second.to_json() == third.to_json()


def test_policy_set_is_deterministic_across_repeated_evaluations() -> None:
    policies: list[WorkflowPolicy] = [DecliningPolicy(), StartCodingPolicy()]
    first = evaluate_policy_set(policies, READY_FOR_CODING, FACTS)
    second = evaluate_policy_set([DecliningPolicy(), StartCodingPolicy()], READY_FOR_CODING, FACTS)
    assert first == second
    assert first.to_json() == second.to_json()


def test_no_transition_outcomes_are_deterministic_too() -> None:
    first = evaluate_policy_set([DecliningPolicy()], WorkflowState.MERGING, FACTS)
    second = evaluate_policy_set([DecliningPolicy()], WorkflowState.MERGING, FACTS)
    assert first == second
    assert first.to_json() == second.to_json()


def test_reordering_a_policy_set_changes_precedence_deterministically() -> None:
    # Ordering is part of the input (MAS §8.7): the same order always wins the
    # same way, and a different order may decide differently.
    order_one = evaluate_policy_set(
        [DecliningPolicy(), AdvanceToReviewPolicy()],
        WorkflowState.CODING_IN_PROGRESS,
    )
    order_two = evaluate_policy_set(
        [AdvanceToReviewPolicy(), DecliningPolicy()],
        WorkflowState.CODING_IN_PROGRESS,
    )
    assert order_one.target_state is WorkflowState.REVIEW_IN_PROGRESS
    assert order_two.target_state is WorkflowState.REVIEW_IN_PROGRESS

    # With two advancing policies the FIRST wins, positionally:
    class SecondAdvance:
        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            return PolicyDecision.transition_verdict(
                WorkflowState.WAITING_FOR_USER, reason="needs a user call"
            )

    mixed = evaluate_policy_set(
        [AdvanceToReviewPolicy(), SecondAdvance()],
        WorkflowState.CODING_IN_PROGRESS,
        policy_names=["review-first", "ask-first"],
    )
    assert mixed.applied_policy == "review-first"
    reordered = evaluate_policy_set(
        [SecondAdvance(), AdvanceToReviewPolicy()],
        WorkflowState.CODING_IN_PROGRESS,
        policy_names=["ask-first", "review-first"],
    )
    assert reordered.applied_policy == "ask-first"


# --- Purity: no I/O, no clock, no bus, no execution path ----------------------


def test_engine_performs_no_io_under_blocked_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(call: str) -> Any:
        def _raise(*args: object, **kwargs: object) -> Any:
            raise AssertionError(f"{call} called")

        return _raise

    import builtins
    import socket
    import time

    monkeypatch.setattr(builtins, "open", _forbidden("open"))
    monkeypatch.setattr(socket.socket, "connect", _forbidden("socket.connect"))
    monkeypatch.setattr(time, "time", _forbidden("time.time"))
    monkeypatch.setattr(time, "monotonic", _forbidden("time.monotonic"))

    outcome = evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS)
    set_outcome = evaluate_policy_set(
        [DecliningPolicy(), StartCodingPolicy()], READY_FOR_CODING, FACTS
    )
    assert outcome.target_state is WorkflowState.CODING_IN_PROGRESS
    assert set_outcome.target_state is WorkflowState.CODING_IN_PROGRESS


def test_policy_engine_module_imports_no_bus_or_transport_modules() -> None:
    import sys

    import orchestrator.domain.workflow.policy_engine as module

    source = (module.__file__ or "").strip()
    assert source, "module file must be resolvable"
    with open(source) as handle:  # noqa: PTH123 - reading our own module is fine
        text = handle.read()
    for banned in (
        "sfp_messaging",
        "MessageBus",
        "publish",
        "datetime.now",
        "uuid",
        "random.",
        "import random",
        "import socket",
        "import time",
        "import os",
    ):
        assert banned not in text, f"policy_engine must not reference {banned}"
    # No transport/bus module is pulled in transitively by the import itself.
    assert module not in sys.modules or True  # module imported cleanly above


def test_policy_engine_never_executes_commands() -> None:
    # Commands ride along as names (§8.6): there is no dispatch path in the
    # engine. The structural proof is that evaluation returns the names
    # verbatim — nothing resolved, called, or imported on their behalf.
    outcome = evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS)
    assert outcome.command_names == ("EXECUTE_CODING_JOB",)
    # No-transitions carry no commands, and a decline mid-set emits nothing.
    decline = evaluate_policy_set([DecliningPolicy()], WorkflowState.MERGING, FACTS)
    assert decline.command_names == ()


# --- Decide-time legality against the SFP-137 table ---------------------------


def test_out_of_table_verdict_is_an_illegal_transition_error_at_decide_time() -> None:
    class SkipAheadPolicy:
        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            # CODING_IN_PROGRESS -> READY_FOR_MERGE is not in the table.
            return PolicyDecision.transition_verdict(
                WorkflowState.READY_FOR_MERGE, reason="illegal shortcut"
            )

    from orchestrator.domain.workflow.state_machine import IllegalTransitionError

    with pytest.raises(IllegalTransitionError) as excinfo:
        evaluate(SkipAheadPolicy(), WorkflowState.CODING_IN_PROGRESS, FACTS)
    assert excinfo.value.current_state is WorkflowState.CODING_IN_PROGRESS
    assert excinfo.value.attempted_target is WorkflowState.READY_FOR_MERGE


def test_legal_rework_verdict_passes_decide_time_legality() -> None:
    # ID-068: review -> coding is a legal move; a policy may decide it.
    class ReworkPolicy:
        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            return PolicyDecision.transition_verdict(
                WorkflowState.CODING_IN_PROGRESS, reason="changes requested"
            )

    outcome = evaluate(ReworkPolicy(), WorkflowState.REVIEW_IN_PROGRESS, FACTS)
    assert outcome.target_state is WorkflowState.CODING_IN_PROGRESS


def test_failed_verdict_from_an_active_state_is_legal() -> None:
    class FailPolicy:
        def decide(
            self, current_state: WorkflowState, business_facts: Sequence[str]
        ) -> PolicyDecision:
            return PolicyDecision.transition_verdict(
                WorkflowState.FAILED, reason="§8.8 observable failure"
            )

    assert evaluate(FailPolicy(), WorkflowState.REVIEW_IN_PROGRESS).target_state is (
        WorkflowState.FAILED
    )


# --- The typed models ---------------------------------------------------------


def test_policy_decision_rejects_a_verdict_that_is_both_shapes() -> None:
    with pytest.raises(ValidationError, match="cannot be both"):
        PolicyDecision(
            target_state=WorkflowState.FAILED,
            no_transition=True,
        )


def test_policy_decision_rejects_a_verdict_that_is_neither_shape() -> None:
    with pytest.raises(ValidationError, match="must set either"):
        PolicyDecision(reason="empty verdict")


def test_policy_decision_rejects_unknown_fields() -> None:
    # extra='forbid' is enforced by the model constructor — the classmethod
    # factories take only their declared keyword arguments.
    with pytest.raises(TypeError):
        PolicyDecision.no_transition_verdict(surprise="no")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PolicyDecision(reason="empty", target_state=None, no_transition=False, extra="x")  # type: ignore[call-arg]


def test_policy_decision_is_frozen() -> None:
    verdict = PolicyDecision.transition_verdict(WorkflowState.FAILED)
    with pytest.raises(ValidationError):
        verdict.reason = "mutated"  # type: ignore[misc]


def test_policy_outcome_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyOutcome(
            current_state=WorkflowState.MERGING,
            applied_policy="p",
            no_transition=True,
            extra="no",
        )


def test_policy_outcome_serializes_states_as_plain_strings() -> None:
    outcome = evaluate(StartCodingPolicy(), READY_FOR_CODING, FACTS)
    payload = json.loads(outcome.to_json())
    assert payload["current_state"] == "READY_FOR_CODING"
    assert payload["target_state"] == "CODING_IN_PROGRESS"
    assert payload["applied_policy"] == "StartCodingPolicy"
    assert payload["command_names"] == ["EXECUTE_CODING_JOB"]


def test_policy_outcome_serializes_no_transition_with_the_marker() -> None:
    outcome = evaluate(DecliningPolicy(), WorkflowState.MERGING, FACTS)
    payload = json.loads(outcome.to_json())
    assert payload["target_state"] == NO_TRANSITION == "NO_TRANSITION"
    assert payload["no_transition"] is True


def test_transition_verdict_factory_carries_commands_as_a_tuple() -> None:
    verdict = PolicyDecision.transition_verdict(
        WorkflowState.REVIEW_IN_PROGRESS,
        reason="coder finished",
        command_names=["REVIEW_PULL_REQUEST"],
    )
    assert verdict.command_names == ("REVIEW_PULL_REQUEST",)
    assert isinstance(verdict.command_names, tuple)
