"""Cross-cutting tests for the SFP-144 context policies (SFP-144).

Covers what is true of the three new policies as a group rather than of any
one of them: the public exports; that every decided target is legal per the
SFP-137 transition table (the engine is the sole legality authority and
raises at decide time otherwise); composition through the engine's policy
set; and the provenance acceptance criterion — every referenced
enum/class/command imports from its landed module, with no new states,
commands, or failure causes invented.
"""

from __future__ import annotations

import pytest
from orchestrator.domain.workflow import policies
from orchestrator.domain.workflow.policies import (
    DeployBeginFact,
    DeployBeginPolicy,
    FailureFact,
    ShouldFailPolicy,
    UserApprovalFact,
    UserApprovalPolicy,
)
from orchestrator.domain.workflow.policy_engine import (
    NO_TRANSITION,
    evaluate,
    evaluate_policy_set,
)
from orchestrator.domain.workflow.state_machine import TRANSITIONS
from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    STATES,
    WorkflowState,
)
from sfp_contracts.validation.profiles import ValidationProfile
from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
    FailureSource,
)

# --- The package exports the three policies and the three fact models ----------


def test_package_exports_the_three_new_policies() -> None:
    assert policies.UserApprovalPolicy is UserApprovalPolicy
    assert policies.DeployBeginPolicy is DeployBeginPolicy
    assert policies.ShouldFailPolicy is ShouldFailPolicy


def test_package_exports_the_three_new_fact_models() -> None:
    assert policies.UserApprovalFact is UserApprovalFact
    assert policies.DeployBeginFact is DeployBeginFact
    assert policies.FailureFact is FailureFact


def test_exported_names_are_exactly_the_public_surface() -> None:
    assert set(policies.__all__) == {
        "CodingStartFact",
        "CodingStartPolicy",
        "DeployBeginFact",
        "DeployBeginPolicy",
        "FailureFact",
        "MergeReadyFact",
        "MergeReadyPolicy",
        "ReviewFact",
        "ReviewStatus",
        "ReviewSuccessPolicy",
        "ShouldFailPolicy",
        "UserApprovalFact",
        "UserApprovalPolicy",
    }


# --- Every decided target is legal per the SFP-137 table -----------------------


def test_every_move_the_policies_request_is_in_the_transition_table() -> None:
    # The engine validates a decided target at decide time and would raise
    # IllegalTransitionError otherwise; this sweeps every (state, fact) pair
    # each policy can see so an out-of-table verdict cannot hide behind an
    # untested state. A no-transition outcome carries no target and is exempt.
    approval = UserApprovalPolicy()
    deploy = DeployBeginPolicy()
    should_fail = ShouldFailPolicy()

    approval_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        UserApprovalFact(validation_profile=p).to_fact_strings() for p in ValidationProfile
    )
    deploy_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        DeployBeginFact(merge_completed=m, deploy_target_ref=r).to_fact_strings()
        for m in (True, False)
        for r in ("refs/heads/main", "")
    )
    failure_facts: tuple[tuple[str, ...], ...] = ((),) + tuple(
        FailureFact(
            classification=FailureClassification(
                category=c.category, cause=c.cause, recoverable=c.recoverable
            )
        ).to_fact_strings()
        for c in _landed_classifications()
    )

    for state in WorkflowState:
        for facts in approval_facts:
            outcome = evaluate(approval, state, facts, policy_name="user-approval")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]
        for facts in deploy_facts:
            outcome = evaluate(deploy, state, facts, policy_name="deploy-begin")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]
        for facts in failure_facts:
            outcome = evaluate(should_fail, state, facts, policy_name="should-fail")
            assert outcome.no_transition or outcome.target_state in TRANSITIONS[state]


def _landed_classifications() -> tuple[FailureClassification, ...]:
    from workspace_worker.workflow.failure import classify_failure

    return tuple(classify_failure(source) for source in FailureSource)


def test_each_policy_owns_its_expected_source_state() -> None:
    assert (
        UserApprovalPolicy()
        .decide(
            WorkflowState.MERGING,
            UserApprovalFact(
                validation_profile=ValidationProfile.LEVEL_3_USER_FACING
            ).to_fact_strings(),
        )
        .target_state
        is WorkflowState.WAITING_FOR_USER
    )
    assert (
        DeployBeginPolicy()
        .decide(
            WorkflowState.MERGING,
            DeployBeginFact(
                merge_completed=True,
                deploy_target_ref="refs/heads/main",
            ).to_fact_strings(),
        )
        .target_state
        is WorkflowState.DEPLOYING
    )
    # No landed classification moves; the should-fail policy's every verdict
    # over the landed taxonomy is a recorded non-move (see test_should_fail).
    assert (
        ShouldFailPolicy()
        .decide(
            WorkflowState.CODING_IN_PROGRESS,
            FailureFact(
                classification=FailureClassification(
                    category=FailureCategory.DEVELOPMENT_FAILURE,
                    cause=None,
                    recoverable=False,
                )
            ).to_fact_strings(),
        )
        .no_transition
        is True
    )


# --- Composition through the engine's policy set -------------------------------


def test_policies_compose_first_match_wins_through_the_engine() -> None:
    # At MERGING with a LEVEL_2 profile, the approval policy parks the
    # workflow before the deploy policy is consulted; at MERGING with a
    # LEVEL_1 profile, the deploy policy's move wins instead.
    approval_facts = UserApprovalFact(
        validation_profile=ValidationProfile.LEVEL_2_BACKEND_OR_API
    ).to_fact_strings()
    at_level_2 = evaluate_policy_set(
        [UserApprovalPolicy(), DeployBeginPolicy()],
        WorkflowState.MERGING,
        approval_facts,
    )
    assert at_level_2.applied_policy == "UserApprovalPolicy"
    assert at_level_2.target_state is WorkflowState.WAITING_FOR_USER

    level_1_facts = (
        UserApprovalFact(validation_profile=ValidationProfile.LEVEL_1_INTERNAL).to_fact_strings()
        + DeployBeginFact(
            merge_completed=True, deploy_target_ref="refs/heads/main"
        ).to_fact_strings()
    )
    at_level_1 = evaluate_policy_set(
        [UserApprovalPolicy(), DeployBeginPolicy()],
        WorkflowState.MERGING,
        level_1_facts,
    )
    assert at_level_1.applied_policy == "DeployBeginPolicy"
    assert at_level_1.target_state is WorkflowState.DEPLOYING


def test_a_set_where_no_policy_moves_records_the_no_transition() -> None:
    outcome = evaluate_policy_set(
        [UserApprovalPolicy(), DeployBeginPolicy(), ShouldFailPolicy()],
        WorkflowState.COMPLETED,
        (),
    )
    assert outcome.no_transition is True
    assert outcome.applied_policy == "policy-set"
    assert outcome.target_state_name == NO_TRANSITION


# --- Provenance: nothing invented, everything imported -------------------------


def test_every_referenced_enum_and_class_imports_from_its_landed_module() -> None:
    # The provenance acceptance criterion: the states, profiles, failure
    # taxonomy, and command names the policies reference are all imported
    # members of landed modules — no new state, command, or failure cause is
    # invented by this ticket.
    from orchestrator.domain.workflow import policies as policies_module
    from orchestrator.domain.workflow.policies import (
        deploy_begin,
        should_fail,
        user_approval,
    )
    from orchestrator.domain.workflow.policies import facts as facts_module
    from sfp_contracts.commands import NotifyUser, RequestUserInput

    # WorkflowState: the landed domain alias of the persistence enum, and the
    # full §8.4 set is exactly these ten — nothing added, nothing renamed.
    assert user_approval.SOURCE_STATE is WorkflowState.MERGING
    assert user_approval.TARGET_STATE is WorkflowState.WAITING_FOR_USER
    assert deploy_begin.SOURCE_STATE is WorkflowState.MERGING
    assert deploy_begin.TARGET_STATE is WorkflowState.DEPLOYING
    assert should_fail.TARGET_STATE is WorkflowState.FAILED
    assert should_fail.SOURCE_STATES is ACTIVE_STATES
    assert len(STATES) == 10

    # ValidationProfile: the landed contract enum, in full.
    assert {p.value for p in ValidationProfile} == {
        "LEVEL_1_INTERNAL",
        "LEVEL_2_BACKEND_OR_API",
        "LEVEL_3_USER_FACING",
        "LEVEL_4_HIGH_RISK",
    }
    assert facts_module.UserApprovalFact.model_fields["validation_profile"].annotation

    # Failure taxonomy: the landed ID-068 members, exactly.
    assert {c.value for c in FailureCategory} == {"DEVELOPMENT_FAILURE", "BLOCKED"}
    assert len(BlockedCause) == 8
    assert len(FailureSource) == 15

    # Commands: the landed payload classes' names — the only strings carried.
    assert user_approval.COMMAND_NAME == RequestUserInput.__name__
    assert deploy_begin.COMMAND_NAME == NotifyUser.__name__
    assert should_fail.ESCALATION_COMMAND_NAME == RequestUserInput.__name__
    assert should_fail.COMMAND_NAME == NotifyUser.__name__

    # The policy modules import their enums/classes (never redefine them).
    assert facts_module.ValidationProfile is ValidationProfile
    assert facts_module.FailureClassification is FailureClassification
    assert policies_module is not None


def test_no_policy_module_defines_an_enum_or_state_of_its_own() -> None:
    # Nothing in the new modules declares a new enum member, state, or
    # failure cause: the only enums referenced are the landed ones.
    import enum
    import inspect

    from orchestrator.domain.workflow.policies import (
        deploy_begin,
        should_fail,
        user_approval,
    )

    for module in (user_approval, deploy_begin, should_fail):
        tree = inspect.getsource(module)
        assert "class " in tree  # each module defines exactly its policy class
        # Enum classes defined *in* the module would appear as class
        # statements inheriting from an enum base; the policy classes are the
        # only class statements allowed. Policy classes carry no bases at all,
        # so the bases clause is parsed defensively (it may be absent).
        for line in tree.splitlines():
            stripped = line.strip()
            if stripped.startswith("class "):
                paren = stripped.find("(")
                bases = stripped[paren + 1 : stripped.rfind(")")] if paren != -1 else ""
                assert "Enum" not in bases and "StrEnum" not in bases, (module.__name__, stripped)
        assert not any(
            isinstance(obj, type)
            and issubclass(obj, enum.Enum)
            and obj.__module__ == module.__name__
            for name, obj in vars(module).items()
        ), module.__name__


@pytest.mark.parametrize(
    ("policy", "state"),
    [
        (UserApprovalPolicy, WorkflowState.MERGING),
        (DeployBeginPolicy, WorkflowState.MERGING),
        (ShouldFailPolicy, WorkflowState.CODING_IN_PROGRESS),
    ],
)
def test_each_policy_type_is_instantiable_without_configuration(
    policy: type, state: WorkflowState
) -> None:
    # The seam requires no constructor arguments or registration.
    instance = policy()
    outcome = evaluate(instance, state, ())
    assert outcome.no_transition is True  # no facts → each policy declines
