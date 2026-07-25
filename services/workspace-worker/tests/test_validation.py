"""Tests for evaluate_validation_gates (SFP-74; ID-024/ID-067).

Covers the acceptance criteria:
- returns a GateDecision;
- required_gates == list(GATE_MAPPING[profile]) for every profile (delegates,
  not hardcoded);
- requires_human_approval matches REQUIRES_HUMAN_APPROVAL for every profile;
- auto_merge_eligible is True only for LEVEL_1_INTERNAL (the auto-merge tier);
- deterministic: two calls yield equal decisions.

The expected mapping is encoded INDEPENDENTLY here (an oracle table of
per-profile gate sets + the human-approval partition), not imported from the
function's own delegates, so the test is a genuine oracle, not a tautology.
"""

from typing import NamedTuple

import pytest
from sfp_contracts.validation.gate_decision import GateDecision
from sfp_contracts.validation.profiles import (
    GATE_MAPPING,
    REQUIRES_HUMAN_APPROVAL,
    ValidationProfile,
)
from workspace_worker.workflow.validation import evaluate_validation_gates


class Expected(NamedTuple):
    """The expected gates + approval flags for a profile."""

    gates: tuple[str, ...]
    needs_human: bool


_BASE = (
    "blueprint_compliance",
    "acceptance_criteria_satisfied",
    "test_plan_satisfied",
    "no_unrelated_changes",
)

#: Independent oracle: every profile -> its (gates, needs-human-approval) per
#: ID-024. Built WITHOUT consulting the function under test; this is the spec
#: encoded as test data. auto_merge_eligible is derived as not needs_human.
EXPECTED: dict[ValidationProfile, Expected] = {
    ValidationProfile.LEVEL_1_INTERNAL: Expected(_BASE, False),
    ValidationProfile.LEVEL_2_BACKEND_OR_API: Expected(
        _BASE + ("maintainability_acceptable",), True
    ),
    ValidationProfile.LEVEL_3_USER_FACING: Expected(
        _BASE
        + (
            "maintainability_acceptable",
            "security_acceptable",
            "security_review",
        ),
        True,
    ),
    ValidationProfile.LEVEL_4_HIGH_RISK: Expected(
        _BASE
        + (
            "maintainability_acceptable",
            "security_acceptable",
            "security_review",
            "migration_reversibility",
        ),
        True,
    ),
}


def test_expected_table_covers_every_profile() -> None:
    """Guard: the oracle table is total over ValidationProfile."""
    assert set(EXPECTED) == set(ValidationProfile)
    assert len(EXPECTED) == 4


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_returns_gate_decision(profile: ValidationProfile) -> None:
    """evaluate_validation_gates returns a GateDecision instance."""
    assert isinstance(evaluate_validation_gates(profile), GateDecision)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_profile_echoed(profile: ValidationProfile) -> None:
    """The decision carries the exact profile it was resolved from."""
    assert evaluate_validation_gates(profile).profile is profile


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_required_gates_match_oracle(profile: ValidationProfile) -> None:
    """required_gates == list(GATE_MAPPING[profile]) (delegates, not hardcoded)."""
    decision = evaluate_validation_gates(profile)
    assert decision.required_gates == list(EXPECTED[profile].gates)
    assert decision.required_gates == list(GATE_MAPPING[profile])


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_requires_human_approval_matches_oracle(profile: ValidationProfile) -> None:
    """requires_human_approval matches REQUIRES_HUMAN_APPROVAL for every profile."""
    decision = evaluate_validation_gates(profile)
    assert decision.requires_human_approval is EXPECTED[profile].needs_human
    assert decision.requires_human_approval == (profile in REQUIRES_HUMAN_APPROVAL)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_auto_merge_eligible_only_for_level_1(profile: ValidationProfile) -> None:
    """auto_merge_eligible is True only for LEVEL_1_INTERNAL."""
    decision = evaluate_validation_gates(profile)
    assert decision.auto_merge_eligible is (profile is ValidationProfile.LEVEL_1_INTERNAL)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_auto_merge_is_complement_of_human_approval(profile: ValidationProfile) -> None:
    """auto_merge_eligible == not requires_human_approval (consistency invariant)."""
    decision = evaluate_validation_gates(profile)
    assert decision.auto_merge_eligible is (not decision.requires_human_approval)


@pytest.mark.parametrize("profile", list(ValidationProfile))
def test_deterministic(profile: ValidationProfile) -> None:
    """Same input always yields an equal GateDecision."""
    a = evaluate_validation_gates(profile)
    b = evaluate_validation_gates(profile)
    assert a == b


def test_delegates_to_gate_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The function reads GATE_MAPPING from its module, not a local copy.

    Patching the module-level binding the function imported must change the
    output — proving it delegates rather than carrying a hardcoded table.
    """
    import workspace_worker.workflow.validation as mod

    fake_mapping = {profile: ("__sentinel_gate__",) for profile in ValidationProfile}
    monkeypatch.setattr(mod, "GATE_MAPPING", fake_mapping)
    decision = evaluate_validation_gates(ValidationProfile.LEVEL_1_INTERNAL)
    assert decision.required_gates == ["__sentinel_gate__"]


def test_delegates_to_requires_human_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The function calls requires_human_approval, not a local hardcoded rule.

    Patching the module-level binding must flip the flags — proving delegation.
    """
    import workspace_worker.workflow.validation as mod

    monkeypatch.setattr(mod, "requires_human_approval", lambda profile: True)
    decision = evaluate_validation_gates(ValidationProfile.LEVEL_1_INTERNAL)
    assert decision.requires_human_approval is True
    assert decision.auto_merge_eligible is False
