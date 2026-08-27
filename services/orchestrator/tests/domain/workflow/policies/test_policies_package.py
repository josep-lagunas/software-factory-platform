"""Cross-cutting tests for the core-loop policies package (SFP-143).

Covers what is true of the package as a whole rather than of one policy:
the public exports; that every decided target is legal per the SFP-137
transition table (the engine is the sole legality authority and raises at
decide time otherwise); that the policies compose in a policy set with
first-match-wins semantics; and the acceptance criterion that engine types are
imported from the landed SFP-142 engine, never forked.
"""

from __future__ import annotations

import pytest
from orchestrator.domain.workflow import policies
from orchestrator.domain.workflow.policies import (
    CodingStartFact,
    CodingStartPolicy,
    MergeReadyFact,
    MergeReadyPolicy,
    ReviewFact,
    ReviewStatus,
    ReviewSuccessPolicy,
)
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    evaluate,
    evaluate_policy_set,
)
from orchestrator.domain.workflow.state_machine import TRANSITIONS
from orchestrator.domain.workflow.states import WorkflowState

# --- The package exports the three policies and the three fact models ----------


def test_package_exports_the_three_policies() -> None:
    assert policies.CodingStartPolicy is CodingStartPolicy
    assert policies.ReviewSuccessPolicy is ReviewSuccessPolicy
    assert policies.MergeReadyPolicy is MergeReadyPolicy


def test_package_exports_the_three_fact_models() -> None:
    assert policies.CodingStartFact is CodingStartFact
    assert policies.ReviewFact is ReviewFact
    assert policies.MergeReadyFact is MergeReadyFact


def test_exported_names_are_exactly_the_public_surface() -> None:
    assert set(policies.__all__) == {
        "CodingStartFact",
        "CodingStartPolicy",
        "MergeReadyFact",
        "MergeReadyPolicy",
        "ReviewFact",
        "ReviewStatus",
        "ReviewSuccessPolicy",
    }


# --- Every decided target is legal per the SFP-137 table -----------------------


def test_every_move_the_policies_request_is_in_the_transition_table() -> None:
    # The engine validates a decided target at decide time and would raise
    # IllegalTransitionError otherwise; this sweeps every (state, fact) pair
    # each policy can see so an out-of-table verdict cannot hide behind an
    # untested state. A no-transition outcome carries no target and is exempt.
    coding = CodingStartPolicy()
    review = ReviewSuccessPolicy()
    merge = MergeReadyPolicy()

    coding_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        CodingStartFact(start_request_admitted=a, capacity_available=c).to_fact_strings()
        for a in (True, False)
        for c in (True, False)
    )
    review_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        ReviewFact(review_status=s).to_fact_strings() for s in ReviewStatus
    )
    merge_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        MergeReadyFact(pr_review_approved_for_head=p, ci_gates_green=g).to_fact_strings()
        for p in (True, False)
        for g in (True, False)
    )

    for state in WorkflowState:
        for facts in coding_facts:
            outcome = evaluate(coding, state, facts, policy_name="coding-start")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]
        for facts in review_facts:
            outcome = evaluate(review, state, facts, policy_name="review-success")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]
        for facts in merge_facts:
            outcome = evaluate(merge, state, facts, policy_name="merge-ready")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]


def test_each_policy_owns_its_expected_source_state() -> None:
    assert (
        CodingStartPolicy()
        .decide(
            WorkflowState.READY_FOR_CODING,
            CodingStartFact(start_request_admitted=True, capacity_available=True).to_fact_strings(),
        )
        .target_state
        is WorkflowState.CODING_IN_PROGRESS
    )
    assert (
        ReviewSuccessPolicy()
        .decide(
            WorkflowState.REVIEW_IN_PROGRESS,
            ReviewFact(review_status=ReviewStatus.APPROVED).to_fact_strings(),
        )
        .target_state
        is WorkflowState.READY_FOR_MERGE
    )
    assert (
        MergeReadyPolicy()
        .decide(
            WorkflowState.READY_FOR_MERGE,
            MergeReadyFact(pr_review_approved_for_head=True, ci_gates_green=True).to_fact_strings(),
        )
        .target_state
        is WorkflowState.MERGING
    )


# --- Composition through the engine's policy set -------------------------------


def test_policies_compose_first_match_wins_through_the_engine() -> None:
    # One state where each policy in turn is the first to move: at
    # READY_FOR_CODING the coding policy owns the edge; at REVIEW_IN_PROGRESS
    # the review policy does; the others decline and never stop the search.
    coding_facts = CodingStartFact(
        start_request_admitted=True, capacity_available=True
    ).to_fact_strings()

    at_coding = evaluate_policy_set(
        [ReviewSuccessPolicy(), CodingStartPolicy(), MergeReadyPolicy()],
        WorkflowState.READY_FOR_CODING,
        coding_facts,
    )
    assert at_coding.applied_policy == "CodingStartPolicy"
    assert at_coding.target_state is WorkflowState.CODING_IN_PROGRESS

    review_facts = ReviewFact(review_status=ReviewStatus.CHANGES_REQUESTED).to_fact_strings()
    at_review = evaluate_policy_set(
        [ReviewSuccessPolicy(), CodingStartPolicy(), MergeReadyPolicy()],
        WorkflowState.REVIEW_IN_PROGRESS,
        review_facts,
    )
    assert at_review.applied_policy == "ReviewSuccessPolicy"
    assert at_review.target_state is WorkflowState.CODING_IN_PROGRESS


def test_a_set_where_no_policy_moves_records_the_no_transition() -> None:
    outcome = evaluate_policy_set(
        [CodingStartPolicy(), ReviewSuccessPolicy(), MergeReadyPolicy()],
        WorkflowState.DEPLOYING,
        (),
    )
    assert outcome.no_transition is True
    assert outcome.applied_policy == "policy-set"
    assert outcome.target_state_name == NO_TRANSITION


# --- The engine is imported, never forked --------------------------------------


def test_engine_types_come_from_the_landed_sfp142_engine() -> None:
    # Acceptance criterion: the policies are evaluable via the *existing*
    # policy_engine.evaluate; engine types are imported, not redefined here.
    from orchestrator.domain.workflow import policies as policies_module

    banned_dunder = ("PolicyOutcome", "PolicyDecision", "evaluate", "WorkflowPolicy")
    source_names = {name for name in vars(policies_module) if not name.startswith("__")}
    for name in banned_dunder:
        assert name not in source_names, (
            f"the policies package must re-export nothing engine-owned ({name})"
        )


@pytest.mark.parametrize(
    ("policy", "state"),
    [
        (CodingStartPolicy, WorkflowState.READY_FOR_CODING),
        (ReviewSuccessPolicy, WorkflowState.REVIEW_IN_PROGRESS),
        (MergeReadyPolicy, WorkflowState.READY_FOR_MERGE),
    ],
)
def test_each_policy_type_is_instantiable_without_configuration(
    policy: type, state: WorkflowState
) -> None:
    # The seam requires no constructor arguments or registration.
    instance = policy()
    outcome = evaluate(instance, state, ())
    assert outcome.no_transition is True  # no facts → each policy declines
