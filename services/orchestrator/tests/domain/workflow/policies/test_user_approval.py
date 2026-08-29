"""Tests for the user-approval policy (MAS §8.14, ID-024/ID-067, SFP-144).

Covers: every decision-table row (the LEVEL_2/3/4 move to ``WAITING_FOR_USER``,
the ``LEVEL_1_INTERNAL`` no-move, the absent/unknown fail-closed no-move);
exhaustive wrong-state coverage; evaluation through the landed SFP-142 engine;
the typed fact model (frozen, ``extra='forbid'``); name-only command carrying
(the landed ``RequestUserInput`` payload class name); profile-derived — never
ad-hoc-boolean — deciding; and determinism.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    UserApprovalFact,
    UserApprovalPolicy,
)
from orchestrator.domain.workflow.policies.user_approval import (
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
from sfp_contracts.validation.profiles import (
    REQUIRES_HUMAN_APPROVAL,
    ValidationProfile,
)

POLICY = UserApprovalPolicy()
MERGING = WorkflowState.MERGING


def _facts(profile: ValidationProfile) -> tuple[str, ...]:
    """Render a user-approval fact into the engine's string vocabulary."""
    return UserApprovalFact(validation_profile=profile).to_fact_strings()


#: The PRSpec's decision table, row by row: (profile, expected no-transition).
DECISION_TABLE: tuple[tuple[ValidationProfile, bool], ...] = tuple(
    (profile, profile not in REQUIRES_HUMAN_APPROVAL) for profile in ValidationProfile
)


# --- The decision table, row by row ---------------------------------------------


@pytest.mark.parametrize(("profile", "expected_no_move"), DECISION_TABLE)
def test_each_profile_row_decides_per_the_table(
    profile: ValidationProfile, expected_no_move: bool
) -> None:
    # One test per row of the PRSpec's table: the decision boolean, the
    # target state, the exact reason, and the command name.
    outcome = evaluate(POLICY, MERGING, _facts(profile), policy_name=POLICY_NAME)
    assert outcome.no_transition is expected_no_move, profile
    if expected_no_move:
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            "LEVEL_1_INTERNAL: no user approval required — the internal auto-merge path proceeds"
        )
        assert outcome.command_names == ()
    else:
        assert outcome.target_state is TARGET_STATE
        assert outcome.target_state is WorkflowState.WAITING_FOR_USER
        assert outcome.target_state_name == "WAITING_FOR_USER"
        assert outcome.reason == (
            "profile above LEVEL_1_INTERNAL: a user approval is required before merge (ID-024)"
        )
        assert outcome.command_names == (COMMAND_NAME,)


def test_the_table_covers_every_landed_profile_exactly_once() -> None:
    # Guard: the table sweep is exhaustive over the landed enum — no profile
    # is silently untested and none is invented.
    assert [p for p, _ in DECISION_TABLE] == list(ValidationProfile)
    assert len(DECISION_TABLE) == 4


def test_every_tier_above_level_1_moves_and_level_1_does_not() -> None:
    # The ID-024 invariant, independent of the landed set's spelling: exactly
    # the tiers in REQUIRES_HUMAN_APPROVAL park the workflow.
    for profile in ValidationProfile:
        outcome = evaluate(POLICY, MERGING, _facts(profile), policy_name=POLICY_NAME)
        assert outcome.no_transition is (profile not in REQUIRES_HUMAN_APPROVAL)


# --- Name-only command carrying ---------------------------------------------------


def test_the_referenced_command_is_the_landed_request_user_input_name() -> None:
    # §8.6 / MAS §12.9: the outcome carries the *name* of the landed payload
    # class from sfp_contracts.commands — never a new or invented name.
    from sfp_contracts.commands import RequestUserInput

    assert COMMAND_NAME == RequestUserInput.__name__ == "RequestUserInput"


# --- Exhaustive no-transition coverage --------------------------------------------


def test_wrong_state_is_a_recorded_no_transition() -> None:
    for state in (s for s in WorkflowState if s is not SOURCE_STATE):
        for profile in ValidationProfile:
            outcome = evaluate(POLICY, state, _facts(profile), policy_name=POLICY_NAME)
            assert outcome.no_transition is True, (state, profile)
            assert outcome.target_state is None
            assert outcome.target_state_name == NO_TRANSITION
            assert outcome.reason == (
                "not MERGING: the user-approval policy applies only while merging, before deploy"
            )


def test_absent_fact_is_a_recorded_no_transition_fail_closed() -> None:
    outcome = evaluate(POLICY, MERGING, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.command_names == ()
    assert outcome.reason == (
        "no user-approval fact observed: the profile is not resolvable (fail-closed)"
    )


def test_unknown_profile_is_a_recorded_no_transition_fail_closed() -> None:
    # A fact is present but its profile string matches no landed member: the
    # policy declines to move rather than guessing (never an exception).
    outcome = evaluate(
        POLICY,
        MERGING,
        ("user-approval-fact:validation_profile:LEVEL_9_UNHEARD_OF",),
        policy_name=POLICY_NAME,
    )
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.command_names == ()
    assert outcome.reason == (
        "validation profile not resolvable to a landed ValidationProfile: "
        "the workflow stays (fail-closed)"
    )


def test_no_move_ever_carries_a_command() -> None:
    for facts in (
        (),
        _facts(ValidationProfile.LEVEL_1_INTERNAL),
        ("user-approval-fact:validation_profile:LEVEL_9_UNHEARD_OF",),
    ):
        outcome = evaluate(POLICY, MERGING, facts, policy_name=POLICY_NAME)
        assert outcome.no_transition is True
        assert outcome.command_names == ()


# --- The requirement is decided from the profile, never an ad-hoc boolean ---------


def test_the_policy_module_references_no_ad_hoc_approval_boolean() -> None:
    # ID-024/ID-067: the requirement is a function of the landed tier set.
    # The policy module must not carry its own approval flag or its own tier
    # literal — it consults the contract's REQUIRES_HUMAN_APPROVAL data.
    import ast
    import inspect

    import orchestrator.domain.workflow.policies.user_approval as module

    tree = ast.parse(inspect.getsource(module))
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    tiers = {profile.value for profile in ValidationProfile}
    referenced = string_literals & tiers
    assert referenced == set(), (
        f"the policy must not spell a tier literal itself (found {referenced}); "
        "REQUIRES_HUMAN_APPROVAL is the only source"
    )
    assert module.REQUIRES_HUMAN_APPROVAL is REQUIRES_HUMAN_APPROVAL


# --- The typed fact model ---------------------------------------------------------


def test_user_approval_fact_is_frozen_and_forbids_unknown_fields() -> None:
    fact = UserApprovalFact(validation_profile=ValidationProfile.LEVEL_3_USER_FACING)
    # Frozen: assignment raises (pydantic's documented frozen behaviour —
    # model_copy(update=…) is an explicit copy path, not mutation).
    with pytest.raises(ValidationError):
        fact.validation_profile = ValidationProfile.LEVEL_1_INTERNAL  # type: ignore[misc]
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        UserApprovalFact(  # type: ignore[call-arg]
            validation_profile=ValidationProfile.LEVEL_1_INTERNAL, extra="x"
        )
    assert fact.validation_profile is ValidationProfile.LEVEL_3_USER_FACING


def test_fact_renders_the_profile_as_its_plain_string_value() -> None:
    # ID-013: the fact string carries the member's plain string value.
    rendered = _facts(ValidationProfile.LEVEL_2_BACKEND_OR_API)
    assert rendered == ("user-approval-fact:validation_profile:LEVEL_2_BACKEND_OR_API",)


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    for profile in ValidationProfile:
        fact = UserApprovalFact(validation_profile=profile)
        assert POLICY.parse_fact(fact.to_fact_strings()) == fact


def test_parse_fact_returns_none_when_unresolvable() -> None:
    assert POLICY.parse_fact(()) is None
    assert POLICY.parse_fact(("deploy-begin-fact:merge_completed:True",)) is None
    assert POLICY.parse_fact(("user-approval-fact:validation_profile:NOT_A_TIER",)) is None


def test_parse_fact_is_order_independent_and_ignores_unknown_facts() -> None:
    facts = ("unrelated-fact:x",) + _facts(ValidationProfile.LEVEL_4_HIGH_RISK)[::-1]
    assert POLICY.parse_fact(facts) == UserApprovalFact(
        validation_profile=ValidationProfile.LEVEL_4_HIGH_RISK
    )


# --- Purity -----------------------------------------------------------------------


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

    move = evaluate(
        POLICY, MERGING, _facts(ValidationProfile.LEVEL_2_BACKEND_OR_API), policy_name=POLICY_NAME
    )
    hold = evaluate(
        POLICY,
        MERGING,
        _facts(ValidationProfile.LEVEL_1_INTERNAL),
        policy_name=POLICY_NAME,
    )
    assert move.target_state is WorkflowState.WAITING_FOR_USER
    assert hold.no_transition is True


def test_module_source_references_no_bus_or_transport() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.user_approval")


# --- Determinism ------------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = tuple(
        (MERGING, _facts(profile)) for profile in ValidationProfile
    ) + (
        (MERGING, ()),
        (MERGING, ("user-approval-fact:validation_profile:NOT_A_TIER",)),
        (WorkflowState.DEPLOYING, _facts(ValidationProfile.LEVEL_3_USER_FACING)),
    )
    for state, facts in cases:
        first = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        for _ in range(5):
            repeat = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert repeat == first, (state, facts)
            assert repeat.to_json() == first.to_json()


def test_outcome_is_serializable_with_plain_string_states() -> None:
    import json

    outcome: PolicyOutcome = evaluate(
        POLICY,
        MERGING,
        _facts(ValidationProfile.LEVEL_4_HIGH_RISK),
        policy_name=POLICY_NAME,
    )
    payload = json.loads(outcome.to_json())
    assert payload["current_state"] == "MERGING"
    assert payload["target_state"] == "WAITING_FOR_USER"
    assert payload["command_names"] == ["RequestUserInput"]
    held = evaluate(
        POLICY,
        MERGING,
        _facts(ValidationProfile.LEVEL_1_INTERNAL),
        policy_name=POLICY_NAME,
    )
    assert json.loads(held.to_json())["target_state"] == NO_TRANSITION
