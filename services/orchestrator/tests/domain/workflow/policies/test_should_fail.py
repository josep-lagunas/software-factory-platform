"""Tests for the should-fail policy (MAS §8.14 / §8.8, ID-068/ID-069, SFP-144).

Covers: every decision-table case (a)–(d); exhaustive coverage over the
landed ID-068 taxonomy — every ``FailureSource`` through the landed
``classify_failure`` oracle, so the sweep is over real classifications, not
hand-picked ones; exhaustive wrong-state coverage over the FAILED-sources
set; evaluation through the landed SFP-142 engine; the typed fact model
(frozen, ``extra='forbid'``); name-only command carrying; the surfaced
case-(d) executability gap; and determinism.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from orchestrator.domain.workflow.policies import (
    FailureFact,
    ShouldFailPolicy,
)
from orchestrator.domain.workflow.policies.should_fail import (
    COMMAND_NAME,
    ESCALATION_COMMAND_NAME,
    POLICY_NAME,
    SOURCE_STATES,
    TARGET_STATE,
)
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    PolicyOutcome,
    evaluate,
)
from orchestrator.domain.workflow.states import ACTIVE_STATES, WorkflowState
from pydantic import ValidationError
from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
    FailureSource,
)

POLICY = ShouldFailPolicy()
CODING = WorkflowState.CODING_IN_PROGRESS


def _facts(classification: FailureClassification) -> tuple[str, ...]:
    """Render a failure fact into the engine's string vocabulary."""
    return FailureFact(classification=classification).to_fact_strings()


#: The landed ID-068/ID-069 oracle: every ``FailureSource`` classified once.
#: Built with the workspace-worker's own ``classify_failure`` so these are
#: the real classifications the factory produces — not restatements of the
#: taxonomy. (Imported lazily: the classifier lives in the other service.)
def _every_landed_classification() -> tuple[FailureClassification, ...]:
    from workspace_worker.workflow.failure import classify_failure

    return tuple(classify_failure(source) for source in FailureSource)


#: The per-cause recoverable flags, read off the oracle's output (never
#: restated from the implementation's private ``_RECOVERABLE`` dict).
def _is_auto_recoverable(classification: FailureClassification) -> bool:
    return classification.recoverable


# --- Case (a): DEVELOPMENT_FAILURE → rework-loop no-move --------------------------


def test_case_a_development_failure_never_fails_the_workflow() -> None:
    for source in (
        FailureSource.LINT,
        FailureSource.TYPECHECK,
        FailureSource.BUILD,
        FailureSource.UNIT_TEST,
        FailureSource.INTEGRATION_TEST,
        FailureSource.CI,
    ):
        classification = _every_landed_classification()[list(FailureSource).index(source)]
        outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
        assert outcome.no_transition is True, source
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            "development failure: the Coder fixes and re-submits (ID-068 rework loop); not FAILED"
        )
        # ID-068: no escalation command on the rework path, ever.
        assert outcome.command_names == ()


@pytest.mark.parametrize(
    "source",
    [
        s
        for s in FailureSource
        if s.name
        in {
            "LINT",
            "TYPECHECK",
            "BUILD",
            "UNIT_TEST",
            "INTEGRATION_TEST",
            "CI",
        }
    ],
)
def test_case_a_each_development_source_row(source: FailureSource) -> None:
    classifications = _every_landed_classification()
    classification = classifications[list(FailureSource).index(source)]
    assert classification.category is FailureCategory.DEVELOPMENT_FAILURE
    outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.command_names == ()
    assert "ID-068" in outcome.reason


# --- Case (b): BLOCKED auto-recoverable → retry no-move ---------------------------


def test_case_b_each_auto_recoverable_cause_is_a_recorded_no_move() -> None:
    for classification in _every_landed_classification():
        if classification.category is not FailureCategory.BLOCKED:
            continue
        if not _is_auto_recoverable(classification):
            continue
        outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
        assert outcome.no_transition is True, classification.cause
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            f"blocked ({classification.cause.value}), auto-recoverable: "
            "retried once the condition clears (ID-069); not FAILED"
        )
        # No human is involved in an auto-recoverable retry.
        assert outcome.command_names == ()


# --- Case (c): BLOCKED human-recoverable → no-move naming the specific cause ------


def test_case_c_each_human_recoverable_cause_names_itself_exactly() -> None:
    for classification in _every_landed_classification():
        if classification.category is not FailureCategory.BLOCKED:
            continue
        if _is_auto_recoverable(classification):
            continue
        outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
        assert outcome.no_transition is True, classification.cause
        assert outcome.target_state is None
        assert outcome.target_state_name == NO_TRANSITION
        assert outcome.reason == (
            f"blocked ({classification.cause.value}), human-recoverable: "
            "the user must decide (RequestUserInput); not FAILED"
        )
        # The ask-the-user command is referenced alongside the non-move.
        assert outcome.command_names == (ESCALATION_COMMAND_NAME,)


def test_case_c_reasons_are_distinct_per_cause() -> None:
    # Every human-recoverable cause must be nameable in the reason — no two
    # causes may render the same recorded non-move.
    human_recoverable = [
        c
        for c in _every_landed_classification()
        if c.category is FailureCategory.BLOCKED and not _is_auto_recoverable(c)
    ]
    reasons = {
        evaluate(POLICY, CODING, _facts(c), policy_name=POLICY_NAME).reason
        for c in human_recoverable
    }
    assert len(reasons) == len(human_recoverable)
    for cause in (c.cause for c in human_recoverable):
        assert any(cause.value in reason for reason in reasons), cause


def test_case_c_does_not_drive_waiting_for_user() -> None:
    # The policy *decides* the human-recoverable case; it never moves to
    # WAITING_FOR_USER — that edge is owned elsewhere (SFP-141).
    classification = _classification_with_cause(BlockedCause.MISSING_CONTEXT)
    outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is not WorkflowState.WAITING_FOR_USER


def _classification_with_cause(cause: BlockedCause) -> FailureClassification:
    for classification in _every_landed_classification():
        if classification.cause is cause:
            return classification
    raise AssertionError(f"no landed classification carries cause {cause}")


# --- Case (d): the terminal genuine failure — surfaced, not implemented -----------


def test_case_d_no_landed_classification_reaches_failed() -> None:
    # The executability gap (MAS §12.9; see the module docstring): the landed
    # taxonomy is total over cases (a)–(c), so no landed classification maps
    # to the terminal row. This test pins that fact so the day a terminal
    # marker lands upstream, this assertion fails and forces the (d) row to
    # be implemented rather than silently ignored.
    for state in ACTIVE_STATES:
        for classification in _every_landed_classification():
            outcome = evaluate(POLICY, state, _facts(classification), policy_name=POLICY_NAME)
            assert outcome.target_state is not WorkflowState.FAILED, (
                f"a landed classification ({classification.category}/"
                f"{classification.cause}) now reaches FAILED from {state}: "
                "implement the case-(d) row and update this guard"
            )


def test_case_d_the_failed_target_remains_declared_and_table_legal() -> None:
    # The edge this policy owns stays owned: the target constant points at
    # the landed FAILED state, and the SFP-137 table makes it legal from
    # every source this policy declares. What is missing is the upstream
    # marker naming which classifications are terminal — not legality.
    from orchestrator.domain.workflow.state_machine import TRANSITIONS

    assert TARGET_STATE is WorkflowState.FAILED
    assert SOURCE_STATES is ACTIVE_STATES
    for state in SOURCE_STATES:
        assert WorkflowState.FAILED in TRANSITIONS[state], state


def test_the_landed_taxonomy_is_total_over_cases_a_through_c() -> None:
    # The premise of the surfaced gap, verified rather than assumed: two
    # categories; every blocked cause partitioned by the recoverable flag;
    # every classification falling into exactly one of (a)/(b)/(c).
    assert [c.name for c in FailureCategory] == ["DEVELOPMENT_FAILURE", "BLOCKED"]
    classifications = _every_landed_classification()
    for classification in classifications:
        if classification.category is FailureCategory.BLOCKED:
            assert classification.cause is not None
    causes_seen = {c.cause for c in classifications if c.category is FailureCategory.BLOCKED}
    assert causes_seen == set(BlockedCause), "classify_failure must cover every cause"


# --- The FAILED-sources set (exhaustive state coverage) ----------------------------


def test_wrong_state_is_a_recorded_no_transition() -> None:
    for state in (s for s in WorkflowState if s not in SOURCE_STATES):
        for classification in (
            _every_landed_classification()[0],
            _classification_with_cause(BlockedCause.MISSING_CONTEXT),
        ):
            outcome = evaluate(POLICY, state, _facts(classification), policy_name=POLICY_NAME)
            assert outcome.no_transition is True, state
            assert outcome.target_state is None
            assert outcome.target_state_name == NO_TRANSITION
            assert outcome.reason == (
                "state not in the FAILED-sources set: "
                "the should-fail policy applies only to active states"
            )


def test_every_active_state_accepts_the_policy() -> None:
    # The policy is evaluable from every state in the FAILED-sources set —
    # no active state is silently excluded.
    classification = _classification_with_cause(BlockedCause.MISSING_SECRET)
    for state in ACTIVE_STATES:
        outcome = evaluate(POLICY, state, _facts(classification), policy_name=POLICY_NAME)
        assert outcome.no_transition is True  # (b): a recorded non-move, not an error


def test_source_states_is_read_from_the_landed_states_module() -> None:
    # The PRSpec's risk note: the FAILED-sources set must be the canonical
    # ACTIVE_STATES expression, never a re-listed copy that could drift.
    assert SOURCE_STATES == ACTIVE_STATES
    assert len(SOURCE_STATES) == 7


# --- Absent and malformed facts ----------------------------------------------------


def test_absent_fact_is_a_recorded_no_transition() -> None:
    outcome = evaluate(POLICY, CODING, (), policy_name=POLICY_NAME)
    assert outcome.no_transition is True
    assert outcome.target_state is None
    assert outcome.command_names == ()
    assert outcome.reason == "no failure fact observed: the workflow stays at this stage"


def test_malformed_fact_fails_closed() -> None:
    # A failure fact is present but does not resolve to a landed
    # classification: recorded no-move, never an exception.
    for facts in (
        ("failure-fact:category:NOT_A_CATEGORY",),
        ("failure-fact:category:BLOCKED",),  # routing fields absent
        ("failure-fact:category:BLOCKED", "failure-fact:cause:MISSING_SECRET"),  # no flag
        # BLOCKED with the flag but NO cause — a pairing no landed
        # classification produces (every BLOCKED carries a BlockedCause); it
        # must fail closed, never route to the auto-recoverable no-move.
        (
            "failure-fact:category:BLOCKED",
            "failure-fact:recoverable:True",
        ),
        # DEVELOPMENT_FAILURE with a cause — the other contract-violating
        # pairing (cause=None is reserved for DEVELOPMENT_FAILURE).
        (
            "failure-fact:category:DEVELOPMENT_FAILURE",
            "failure-fact:cause:MISSING_SECRET",
            "failure-fact:recoverable:False",
        ),
    ):
        outcome = evaluate(POLICY, CODING, facts, policy_name=POLICY_NAME)
        assert outcome.no_transition is True, facts
        assert outcome.target_state is None
        assert outcome.command_names == ()
        assert outcome.reason == (
            "failure fact present but not resolvable to a landed FailureClassification: "
            "the workflow stays (fail-closed)"
        )


def test_parse_classification_returns_none_for_unresolvable_strings() -> None:
    assert POLICY.parse_classification(frozenset(("failure-fact:category:NOPE",))) is None
    assert POLICY.parse_classification(frozenset()) is None
    assert POLICY.parse_classification(frozenset(("failure-fact:category:BLOCKED",))) is None


# --- Name-only command carrying ------------------------------------------------------


def test_the_referenced_commands_are_the_landed_payload_names() -> None:
    from sfp_contracts.commands import NotifyUser, RequestUserInput

    assert COMMAND_NAME == NotifyUser.__name__ == "NotifyUser"
    assert ESCALATION_COMMAND_NAME == RequestUserInput.__name__ == "RequestUserInput"


# --- The typed fact model ------------------------------------------------------------


def test_failure_fact_is_frozen_and_forbids_unknown_fields() -> None:
    classification = _classification_with_cause(BlockedCause.MERGE_QUEUE_FAILURE)
    fact = FailureFact(classification=classification)
    # Frozen: assignment raises (pydantic's documented frozen behaviour).
    with pytest.raises(ValidationError):
        fact.classification = _classification_with_cause(  # type: ignore[misc]
            BlockedCause.MISSING_SECRET
        )
    # extra='forbid': an unknown field is rejected at construction.
    with pytest.raises(ValidationError):
        FailureFact(classification=classification, extra="x")  # type: ignore[call-arg]
    assert fact.classification == classification


def test_fact_renders_the_routing_fields_not_the_detail() -> None:
    # detail is informational and unbounded; it is deliberately not rendered.
    classification = FailureClassification(
        category=FailureCategory.BLOCKED,
        cause=BlockedCause.REPO_INACCESSIBLE,
        recoverable=False,
        detail="REPO exit=128 msg=unreachable",
    )
    rendered = _facts(classification)
    assert rendered == (
        "failure-fact:category:BLOCKED",
        "failure-fact:cause:REPO_INACCESSIBLE",
        "failure-fact:recoverable:False",
    )
    assert all("detail" not in s and "128" not in s for s in rendered)


def test_a_development_failure_renders_its_cause_as_none() -> None:
    classification = _every_landed_classification()[list(FailureSource).index(FailureSource.LINT)]
    assert "failure-fact:cause:NONE" in _facts(classification)


def test_fact_round_trips_through_the_engine_string_vocabulary() -> None:
    # ``detail`` is informational free text and deliberately NOT rendered (it
    # cannot alter routing and would make the fact vocabulary unbounded), so a
    # round trip recovers exactly the routing fields — never the detail.
    for classification in _every_landed_classification():
        fact = FailureFact(classification=classification)
        parsed = POLICY.parse_fact(fact.to_fact_strings())
        assert parsed is not None, classification
        assert parsed.classification.category is classification.category, classification
        assert parsed.classification.cause is classification.cause, classification
        assert parsed.classification.recoverable is classification.recoverable, classification


def test_parse_fact_returns_none_when_no_failure_fact_present() -> None:
    assert POLICY.parse_fact(()) is None
    assert POLICY.parse_fact(("deploy-begin-fact:merge_completed:True",)) is None


def test_parse_fact_is_order_independent_and_ignores_unknown_facts() -> None:
    classification = _classification_with_cause(BlockedCause.DEPLOYMENT_FAILURE)
    facts = ("unrelated-fact:x",) + tuple(reversed(_facts(classification)))
    parsed = POLICY.parse_fact(facts)
    assert parsed is not None
    assert parsed.classification.category is FailureCategory.BLOCKED
    assert parsed.classification.cause is BlockedCause.DEPLOYMENT_FAILURE
    assert parsed.classification.recoverable is classification.recoverable


# --- Purity ---------------------------------------------------------------------------


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

    for classification in _every_landed_classification():
        outcome = evaluate(POLICY, CODING, _facts(classification), policy_name=POLICY_NAME)
        assert outcome.reason


def test_module_source_references_no_bus_or_transport() -> None:
    # Scan *code*, not prose: docstrings legitimately say "no randomness", so a
    # raw substring scan false-positives. See tests/.../policies/_purity.py.
    from _purity import assert_module_references_none_of

    assert_module_references_none_of("orchestrator.domain.workflow.policies.should_fail")


# --- Determinism ------------------------------------------------------------------------


def test_identical_inputs_produce_identical_outcomes_repeatedly() -> None:
    cases: tuple[tuple[WorkflowState, Sequence[str]], ...] = tuple(
        (CODING, _facts(c)) for c in _every_landed_classification()
    ) + ((CODING, ()), (WorkflowState.COMPLETED, _facts(_every_landed_classification()[0])))
    for state, facts in cases:
        first = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
        for _ in range(5):
            repeat = evaluate(POLICY, state, facts, policy_name=POLICY_NAME)
            assert repeat == first, (state, facts)
            assert repeat.to_json() == first.to_json()


def test_outcome_is_serializable_with_plain_string_states() -> None:
    import json

    classification = _classification_with_cause(BlockedCause.MISSING_CONTEXT)
    outcome: PolicyOutcome = evaluate(
        POLICY, CODING, _facts(classification), policy_name=POLICY_NAME
    )
    payload = json.loads(outcome.to_json())
    assert payload["target_state"] == NO_TRANSITION
    assert payload["command_names"] == ["RequestUserInput"]
    assert "MISSING_CONTEXT" in payload["reason"]
